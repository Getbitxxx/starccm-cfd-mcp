/* AIR-3B A5 / ③: one transient SEGMENT of rigid-body rotation, with rotation MEASURED rather than derived.
 *
 * The API pattern (disable SteadyModel, enable ImplicitUnsteadyModel, create a RotatingMotion, set the
 * axis and rate, bind it to the region's MotionSpecification, drive the ImplicitUnsteadySolver's time
 * step and the stopping criteria by name) is taken verbatim from the Siemens tutorial this project holds,
 * test-evidence/tutorial-comparison-20261001/RigidBodyMotionRotatingFan.java, and from the project's own
 * ConfigureAir3bMrfPhysics.java for the Vector setComponentsAndUnits calls.
 *
 * WHAT THIS SEGMENT IS AND IS NOT
 *   It runs exactly AIR3B_TRANSIENT_STEPS time steps of the declared step size and writes ONE checkpoint,
 *   one history, one verdict and one measurement certificate. It does NOT claim convergence, periodicity,
 *   engineering validity or flow unsteadiness: those are separate fields, all false here.
 *
 * SEGMENT IDENTITY, AND WHY THERE IS NO PER-MACRO ENVIRONMENT
 *   The runner sets ONE environment for the whole pipeline, so two macros of the same stage cannot receive
 *   different AIR3B_TRANSIENT_* values. The chain is therefore derived from the ARTEFACTS: the first macro
 *   of the transient stage finds no mrf_transient_s1.sim and writes it; the second is handed that
 *   checkpoint as its STAR input, finds it present, and writes mrf_transient_s2.sim. The earlier
 *   AIR3B_TRANSIENT_OUTPUT / AIR3B_TRANSIENT_INDEX / AIR3B_PHYSICAL_TIME_S overrides have been REMOVED
 *   rather than kept: one shared environment value cannot mean two different things for two segments, and
 *   an override that silently makes both macros target the same file or the same absolute time is a
 *   footgun, not a feature. Every per-segment name is derived from the index.
 *
 * THE HANDOFF IS CHECKED AGAINST THE PREVIOUS SEGMENT'S OWN EVIDENCE
 *   Segment 2 must start at exactly the physical time segment 1 recorded reaching - not merely at a time
 *   "> 0". It reads air3b_transient_s1_measurements.json (written by segment 1 in this same job directory),
 *   requires that record to claim ok and finite loads, and compares the two clocks. A mis-chained stage
 *   can therefore not run "segment 2" on the steady checkpoint or "segment 1" on a transient one. The
 *   initialization FLAG is part of the chain check too: segment 1 must be handed an uninitialised state
 *   and segment 2 an initialised one, so "which segment am I" is not inferred from a filename alone.
 *
 * ROTATION IS MEASURED, AND THE MEASUREMENT IS ITSELF CHECKED
 *   The measured quantity is the area-weighted lab-frame centroid of ONE off-axis blade boundary, read
 *   through the built-in Position field function's component functions - the same call
 *   BuildAir3bMrfRegions.extents() already uses on this install for Position[2]. A boundary NAME proves
 *   nothing: BuildAir3bMrfRegions renames every boundary of the rotating region to its ROLE, and on this
 *   geometry 27 of them are called exactly "BLADE", so the surface is CHOSEN by a measured criterion
 *   (largest measured perpendicular distance to the axis, name as the tie-break) rather than by list
 *   order, and every candidate's measured distance is recorded beside the chosen one. The macro then
 *   gates the properties that make the azimuth meaningful:
 *     - the boundary belongs to AIR3B_ROTATING (it is enumerated from that region, never looked up loose);
 *     - its centroid's perpendicular distance to the MEASURED rotation axis is at least
 *       AIR3B_TRANSIENT_MEASURE_MIN_RADIUS_M, so the azimuth is not a near-axis artefact;
 *     - that distance is preserved across the rotation (true of a rigid rotation about that axis, and
 *       false if the coordinates were reported in a co-rotating frame);
 *     - the centroid really MOVED: the measured chord |c_after - c_before| must match the chord the
 *       expected rotation implies, and a zero displacement fails the segment even though the operation
 *       itself completed;
 *     - the measured azimuth change must agree with the rotation this segment's own clock advance
 *       implies, to within AIR3B_TRANSIENT_ROTATION_TOL_DEG.
 *   physical_time x rpm is still computed and still recorded - as `derived_rotation_angle_deg` and
 *   `degrees_rotated` (with degrees_rotated_is_derived true), and never used as the rotation evidence.
 */
import java.io.*;
import java.util.Arrays;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.motion.*;

public class RunAir3bTransientRevolution extends StarMacro {

    private static final String COMPLETE = "AIR3B_TRANSIENT_REVOLUTION_COMPLETE";

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    /** A comma-separated vector from the environment. Declared requests are read, never assumed. */
    private static double[] envVec(String name, String fallback) {
        String v = envText(name, fallback);
        String[] parts = v.split(",");
        if (parts.length != 3) throw new IllegalArgumentException("Expected three components in " + name);
        double[] out = new double[3];
        for (int i = 0; i < 3; i++) out[i] = Double.parseDouble(parts[i].trim());
        return out;
    }

    private static String fmt(double v) {
        return Double.isFinite(v) ? String.format(Locale.ROOT, "%.12g", v) : String.valueOf(v);
    }

    /** JSON-safe number. Java writes NaN/Infinity as bare tokens, which RFC 8259 forbids; a NaN in a
     *  certificate would make the whole record unparseable for a strict reader and therefore
     *  uncitable (and would break AGENTS.md section 2-O8 R1's "every record must be parseable"
     *  ratchet). Non-finite values are written as null and the TSV keeps the readable NaN, because
     *  "this state has no physical clock" and "the clock is NaN" are facts a reader must see. */
    private static String jnum(double v) {
        return Double.isFinite(v) ? fmt(v) : "null";
    }

    /** JSON-safe STRING. The sibling audit macro shipped a certificate with raw TAB characters inside a
     *  JSON string (they came from a TSV row) and the controller's numeric gate refused the whole run as
     *  unreadable - measured on this round's ② run. Every string that reaches JSON here is an object name
     *  or a status word, and this guard is what keeps that an assumption that cannot bite. */
    private static String jstr(String value) {
        if (value == null) return "";
        StringBuilder b = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            // EVERY C0 control and DEL, not only the four the first version handled: a strict JSON reader
            // rejects any of them inside a string, and the measured defect that cost this round a run was
            // exactly one raw control character. Backslash and double quote are neutralised for the same
            // reason jnum exists: one implementation, so no call site has to remember.
            if (c < 0x20 || c == 0x7f) b.append(' ');
            else if (c == '\\') b.append('/');
            else if (c == '"') b.append('\'');
            else b.append(c);
        }
        return b.toString();
    }

    private static String show(double[] v) {
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) b.append(i == 0 ? "" : ",").append(fmt(v[i]));
        return b.append("]").toString();
    }

    private static double norm(double[] v) {
        return Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    }

    private static double dot(double[] a, double[] b) {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }

    /** The minimal-read JSON readers used on the PREVIOUS segment's record. The file is written by this
     *  same macro family with a fixed shape, and every miss throws rather than defaulting: a silently
     *  defaulted handoff check would be exactly the "recorded but never compared" defect class. */
    private static String rawField(String text, String key) {
        int at = text.indexOf("\"" + key + "\":");
        if (at < 0) throw new IllegalStateException("Previous segment record has no field " + key);
        int start = at + key.length() + 3;
        int end = start;
        while (end < text.length() && text.charAt(end) != ',' && text.charAt(end) != '}') end++;
        String raw = text.substring(start, end).trim();
        if (raw.isEmpty()) throw new IllegalStateException("Previous segment field " + key + " is empty");
        return raw;
    }

    private static double rawNumber(String text, String key) {
        return Double.parseDouble(rawField(text, key));
    }

    private static String rawText(String text, String key) {
        String raw = rawField(text, key);
        if (raw.length() < 2 || raw.charAt(0) != '"' || raw.charAt(raw.length() - 1) != '"')
            throw new IllegalStateException("Previous segment field " + key + " is not a string: " + raw);
        return raw.substring(1, raw.length() - 1);
    }

    /** Read-only interface snapshot: records each interface's name and topology so the state before
     *  and after the arc can be compared. BoundaryInterface.getTopology() is the same call
     *  BuildAir3bMrfRegions.java used to assert INTERNAL or IN_PLACE, so it is proven on this install.
     *  Appends, so one file carries every phase of the run; indexed per segment so the two transient
     *  segments running in one job directory cannot overwrite each other's evidence. */
    private static void interfaceSnapshot(Simulation sim, File dir, String phase, String index) throws IOException {
        File f = new File(dir, "air3b_interface_state_" + index + ".tsv");
        boolean fresh = !f.exists();
        try (Writer w = new FileWriter(f, true)) {
            if (fresh) w.write("phase\tinterface\ttopology\n");
            for (Interface i : sim.getInterfaceManager().getObjects()) {
                String topo = "not_a_boundary_interface";
                if (i instanceof BoundaryInterface)
                    topo = String.valueOf(((BoundaryInterface) i).getTopology().getSelectedElement());
                w.write(phase + "\t" + i.getPresentationName() + "\t" + topo + "\n");
            }
        }
    }

    /** Area-weighted centroid of ONE boundary, measured in the LAB frame through the built-in Position
     *  field function's component functions - all THREE components, so the azimuth below is taken in the
     *  true plane perpendicular to the measured axis instead of in an assumed xy-plane. The three
     *  temporary reports are always deleted, and this method only ever reads, so a failure here cannot
     *  alter the solution. */
    private static double[] labCentroidXYZ(Simulation sim, Boundary boundary) {
        FvRepresentation mesh = null;
        for (Representation r : sim.getRepresentationManager().getObjects())
            if (r instanceof FvRepresentation) mesh = (FvRepresentation) r;
        FieldFunction f = sim.getFieldFunctionManager().getFunction("Position");
        ScalarReport[] reports = new ScalarReport[3];
        try {
            double[] out = new double[3];
            for (int c = 0; c < 3; c++) {
                ScalarReport r = (ScalarReport) sim.getReportManager().create("star.base.report.AreaAverageReport");
                reports[c] = r;
                if (mesh != null) r.setRepresentation(mesh);
                r.setFieldFunction(f.getComponentFunction(c));
                r.getParts().setObjects(boundary);
                out[c] = r.getReportMonitorValue();
                if (!Double.isFinite(out[c]))
                    throw new IllegalStateException("Non-finite rotation-measurement centroid component " + c
                            + " on " + boundary.getPresentationName());
            }
            return out;
        } finally {
            for (ScalarReport r : reports) {
                if (r == null) continue;
                try { sim.deleteObjects(Arrays.asList((ClientServerObject) r)); } catch (Throwable ignored) { }
            }
        }
    }

    /** Plane coordinates of p relative to the axis line {origin + s*axis}: {e1, e2, perpendicular radius}.
     *  The basis is built from the axis read back from the motion, so the measurement is expressed in the
     *  axis the simulation actually rotates about rather than in an assumed +Z. */
    private static double[] planeCoords(double[] p, double[] origin, double[] e1, double[] e2) {
        double[] v = {p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]};
        double x = dot(v, e1), y = dot(v, e2);
        return new double[] {x, y, Math.sqrt(x * x + y * y)};
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            double rpm = env("AIR3B_RPM", 10000.0);
            double dt = env("AIR3B_TRANSIENT_DT_S", (2.0 / 360.0) / (rpm / 60.0));  // default 2 degrees
            int stepsThisSegment = (int) env("AIR3B_TRANSIENT_STEPS", 0.0);
            int innerCap = (int) env("AIR3B_INNER_ITERATIONS", 10.0);
            int chunkSteps = (int) env("AIR3B_TRANSIENT_CHUNK", 1.0);
            double minMeasureRadius = env("AIR3B_TRANSIENT_MEASURE_MIN_RADIUS_M", 0.02);
            double rotationTolDeg = env("AIR3B_TRANSIENT_ROTATION_TOL_DEG", 0.5);
            double originTolM = env("AIR3B_TRANSIENT_ORIGIN_TOL_M", 1e-3);
            double[] requestedAxis = envVec("AIR3B_TRANSIENT_AXIS", "0,0,1");
            double[] requestedOrigin = envVec("AIR3B_TRANSIENT_ORIGIN_M", "0,0,0");
            for (double c : requestedAxis) if (!Double.isFinite(c)) throw new IllegalArgumentException("Non-finite requested axis");
            for (double c : requestedOrigin) if (!Double.isFinite(c)) throw new IllegalArgumentException("Non-finite requested origin");
            double requestedAxisNorm = norm(requestedAxis);
            if (!(requestedAxisNorm > 0)) throw new IllegalArgumentException("Requested rotation axis is zero");
            for (int i = 0; i < 3; i++) requestedAxis[i] /= requestedAxisNorm;

            // Segment 1 is the macro that runs when no segment-1 checkpoint exists yet; segment 2 is the
            // macro the runner hands mrf_transient_s1.sim to. Each segment owns its own file names.
            File s1Checkpoint = new File(dir, "mrf_transient_s1.sim");
            boolean secondSegment = s1Checkpoint.exists();
            String index = secondSegment ? "s2" : "s1";
            File output = new File(dir, "mrf_transient_" + index + ".sim");
            if (output.exists()) throw new IllegalStateException("Transient output already exists: " + output.getName());
            if (!index.matches("[A-Za-z0-9_-]{1,16}")) throw new IllegalArgumentException("Invalid transient index");
            if (!(rpm > 0) || !(dt > 0)) throw new IllegalArgumentException("Invalid transient settings");
            if (stepsThisSegment < 1) throw new IllegalArgumentException("AIR3B_TRANSIENT_STEPS must be at least one");
            if (innerCap < 1 || chunkSteps < 1) throw new IllegalArgumentException("Invalid iteration or chunk budget");
            if (!(minMeasureRadius > 0) || !(rotationTolDeg > 0) || !(originTolM > 0))
                throw new IllegalArgumentException("Invalid rotation-evidence tolerances");
            double physicalTarget = (secondSegment ? 2 : 1) * stepsThisSegment * dt;
            if (!(physicalTarget > 0)) throw new IllegalArgumentException("Invalid transient settings");

            if (sim.getRegionManager().getObjects().size() != 2
                    || sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("Expected two regions and three interfaces");
            Region rotating = sim.getRegionManager().getRegion("AIR3B_ROTATING");
            if (rotating == null) throw new IllegalStateException("AIR3B_ROTATING is missing");
            PhysicsContinuum physics = null;
            for (Continuum c : sim.getContinuumManager().getObjects())
                if (c instanceof PhysicsContinuum) { physics = (PhysicsContinuum) c; break; }
            if (physics == null) throw new IllegalStateException("No physics continuum");

            // Continuation-aware from here on: a transient checkpoint carries a physical clock, and the
            // segment's starting time and initialization state are recorded rather than assumed.
            double physicalTimeBefore = 0.0;
            try { physicalTimeBefore = sim.getSolution().getPhysicalTime(); } catch (Throwable ignored) { }
            boolean initializedBefore = sim.isInitialized();
            int iterationBefore = sim.getSimulationIterator().getCurrentIteration();
            MotionSpecification spec = rotating.getValues().get(MotionSpecification.class);
            String frameBefore = (spec.getReferenceFrame() == null) ? "none"
                    : spec.getReferenceFrame().getPresentationName();

            // THE INITIALIZATION FLAG IS RECORDED, NOT GATED, AND THAT IS A MEASURED DECISION.
            // This project measured that the flag does not survive a save/load on this install
            // (test-evidence/air-three-blade/state.json: "Solution.isInitialized() is not persisted across
            // save/load (measured, not inferred)"; every production segment verdict records
            // initialized_flag_on_load false on states whose loaded iteration is 100..1240). Gating
            // segment 2 on `initializedBefore == true` would therefore fail a CORRECT handoff - the exact
            // way the ① production line's first trial was lost ("Segment input is run but not initialized",
            // repaired by gating on the iteration count instead). Segment identity is pinned instead by
            // quantities that ARE reliable: the physical clock compared against segment 1's OWN record,
            // the recorded handoff, and initialized_here, which is derived from the clock rather than from
            // the flag. What the flag is good for is the BEFORE/AFTER invariance check of a single
            // read-only session, which is where this round uses it (AuditAir3bMassBalance).
            String initializedFlagGate = "recorded_not_gated: the flag is not persisted across save/load on"
                    + " this install (measured; see state.json), so gating a correct handoff on it would fail"
                    + " the run for a property of the loader rather than of the chain";

            interfaceSnapshot(sim, dir, "before", index);

            // ModelManager.getModel THROWS "Model not found in ModelMap" when the model is absent instead of
            // returning null - the seventh API trap of this family in this project, and the one that killed the
            // first continuation run because the checkpoint was already unsteady and SteadyModel was genuinely
            // absent. The lookup must therefore be caught rather than null-checked.
            try {
                SteadyModel steady = physics.getModelManager().getModel(SteadyModel.class);
                if (steady != null) physics.disableModel(steady);
            } catch (Throwable absent) {
                System.out.println("SteadyModel absent, which is the continuation case: " + absent.getMessage());
            }
            physics.enable(ImplicitUnsteadyModel.class);

            // Continuation-aware: a transient checkpoint already carries AIR3B_RBM, and creating it again
            // would either duplicate the name or leave the region bound to a different motion. Reuse it when
            // present and only create it when absent, so an arc can be continued from the saved state.
            RotatingMotion motion = null;
            for (Motion existing : sim.get(MotionManager.class).getObjects())
                if (existing instanceof RotatingMotion && "AIR3B_RBM".equals(existing.getPresentationName()))
                    motion = (RotatingMotion) existing;
            boolean motionReused = (motion != null);
            Units rpmUnits = (Units) sim.getUnitsManager().getObject("rpm");
            Units meterUnits = (Units) sim.getUnitsManager().getObject("m");
            Units dimensionless = (Units) sim.getUnitsManager().getObject("");
            // The axis and the axis POINT are set on the create path so the readback below compares the
            // request against something this macro defined rather than against a build default. Both calls
            // are wrapped: the settable-Vector API is proven for UserRotatingReferenceFrame
            // (ConfigureAir3bMrfPhysics.java:152-153) but not, on this install, for RotatingMotion, and a
            // refusal here must not become a hard failure when the READBACK gate is the real authority.
            // The axis is therefore also set through the tutorial's own path.
            String axisSet = "not_attempted";
            String originSet = "not_attempted";
            if (motion == null) {
                motion = sim.get(MotionManager.class).createMotion(RotatingMotion.class, "AIR3B_RBM");
                try {
                    motion.getRotationAxis().getDirection().setComponents(
                            requestedAxis[0], requestedAxis[1], requestedAxis[2]);
                    axisSet = "tutorial_path";
                } catch (Throwable t) {
                    axisSet = "tutorial_path_failed:" + t.getClass().getSimpleName();
                }
                try {
                    motion.getAxisVector().setComponentsAndUnits(requestedAxis[0], requestedAxis[1],
                            requestedAxis[2], dimensionless);
                    axisSet += "+vector";
                } catch (Throwable t) {
                    axisSet += "+vector_failed:" + t.getClass().getSimpleName();
                }
                try {
                    motion.getOriginVector().setComponentsAndUnits(requestedOrigin[0], requestedOrigin[1],
                            requestedOrigin[2], meterUnits);
                    originSet = "vector";
                } catch (Throwable t) {
                    originSet = "vector_failed:" + t.getClass().getSimpleName();
                }
                ((RotationRate) motion.getRotationSpecification()).getRotationRate().setValueAndUnits(rpm, rpmUnits);
            }
            spec.setMotion(motion);
            // ②R9/②R10: a re-used motion must not be accepted on its NAME alone - the project's own A5
            // evidence shows exactly that (`motion_reused_from_checkpoint: true` with only the name
            // recorded). The ACTUAL axis, origin and rate are read back from the motion object and checked
            // against the REQUEST on BOTH paths, created and reused, so a continuation cannot silently
            // inherit a different axis, a different centre or a different rate.
            double[] motionAxis = motion.getAxisVector().getVector().toDoubleArray();
            double[] motionOrigin = motion.getOriginVector().getVector().toDoubleArray();
            double motionRateSi = ((RotationRate) motion.getRotationSpecification())
                    .getRotationRate().getSIValue();
            double requestedRateSi = rpm * 2.0 * Math.PI / 60.0;
            double motionAxisNorm = norm(motionAxis);
            boolean axisFinite = true;
            for (double c : motionAxis) if (!Double.isFinite(c)) axisFinite = false;
            boolean originFinite = true;
            for (double c : motionOrigin) if (!Double.isFinite(c)) originFinite = false;
            if (!axisFinite || !(motionAxisNorm > 0))
                throw new IllegalStateException("Rotation axis readback is not a finite non-zero vector: "
                        + show(motionAxis));
            for (int i = 0; i < 3; i++) motionAxis[i] /= motionAxisNorm;
            for (int i = 0; i < 3; i++)
                if (Math.abs(motionAxis[i] - requestedAxis[i]) > 1e-9)
                    throw new IllegalStateException("Rotation axis readback " + show(motionAxis)
                            + " is not the requested " + show(requestedAxis) + " (motionReused=" + motionReused + ")");
            if (!originFinite)
                throw new IllegalStateException("Rotation origin readback is not finite: " + show(motionOrigin));
            double originDeflection = Math.sqrt(
                    Math.pow(motionOrigin[0] - requestedOrigin[0], 2)
                    + Math.pow(motionOrigin[1] - requestedOrigin[1], 2)
                    + Math.pow(motionOrigin[2] - requestedOrigin[2], 2));
            if (!(originDeflection <= originTolM))
                throw new IllegalStateException("Rotation origin readback " + show(motionOrigin)
                        + " is " + fmt(originDeflection) + " m from the requested centre " + show(requestedOrigin)
                        + " (tolerance " + fmt(originTolM) + " m, motionReused=" + motionReused
                        + ", originSet=" + originSet + ")");
            if (!Double.isFinite(motionRateSi) || Math.abs(motionRateSi - requestedRateSi) > 1e-6 * requestedRateSi)
                throw new IllegalStateException("Rotation rate readback " + motionRateSi
                        + " rad/s is not the requested " + requestedRateSi + " rad/s (motionReused=" + motionReused + ")");
            String motionClass = motion.getClass().getName();

            String frameAfter = (spec.getReferenceFrame() == null) ? "none"
                    : spec.getReferenceFrame().getPresentationName();
            String motionAfter = (spec.getMotion() == null) ? "none" : spec.getMotion().getPresentationName();

            ImplicitUnsteadySolver unsteady =
                    (ImplicitUnsteadySolver) sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            if (unsteady == null) throw new IllegalStateException("Implicit unsteady solver is missing");
            unsteady.getTimeStep().setValue(dt);
            unsteady.getTimeDiscretizationOption().setSelected(TimeDiscretizationOption.SECOND_ORDER);

            InnerIterationStoppingCriterion inner = (InnerIterationStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Inner Iterations");
            if (inner == null) throw new IllegalStateException("Maximum Inner Iterations criterion is missing");
            inner.setMaximumNumberInnerIterations(innerCap);
            inner.setIsUsed(true);

            PhysicalTimeStoppingCriterion physical = (PhysicalTimeStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Physical Time");
            if (physical == null) throw new IllegalStateException("Maximum Physical Time criterion is missing");
            physical.getMaximumTime().setValue(physicalTarget);
            physical.setIsUsed(true);

            Report thrust = sim.getReportManager().getReport("AIR3B_THRUST_Z");
            Report torque = sim.getReportManager().getReport("AIR3B_TORQUE_Z");
            if (thrust == null || torque == null) throw new IllegalStateException("Target-quantity reports are missing");

            // Assert the intended END STATE rather than a change of frame. The previous version compared before
            // and after and therefore threw on a CONTINUATION, whose checkpoint already carries the Lab frame
            // established by the preceding arc - review round 53 caught that it could never have run.
            boolean mrfGone = (spec.getReferenceFrame() == null)
                    || !frameAfter.toUpperCase(java.util.Locale.ROOT).contains("MRF");
            if (!mrfGone)
                throw new IllegalStateException("An MRF reference frame is still bound after setMotion: " + frameAfter);
            if (!"AIR3B_RBM".equals(motionAfter))
                throw new IllegalStateException("The region is not bound to AIR3B_RBM but to: " + motionAfter);

            // Plane basis of the MEASURED axis, so every azimuth below is expressed about the axis the
            // simulation actually rotates about, and a full 3-D centroid is projected into it.
            double[] e1 = Math.abs(motionAxis[0]) < 0.9 ? new double[] {1, 0, 0} : new double[] {0, 1, 0};
            double[] perp = {motionAxis[1] * e1[2] - motionAxis[2] * e1[1],
                             motionAxis[2] * e1[0] - motionAxis[0] * e1[2],
                             motionAxis[0] * e1[1] - motionAxis[1] * e1[0]};
            double tNorm = norm(perp);
            if (!(tNorm > 0)) throw new IllegalStateException("Could not build a plane perpendicular to the rotation axis");
            for (int i = 0; i < 3; i++) e1[i] = perp[i] / tNorm;
            double[] e2 = {motionAxis[1] * e1[2] - motionAxis[2] * e1[1],
                           motionAxis[2] * e1[0] - motionAxis[0] * e1[2],
                           motionAxis[0] * e1[1] - motionAxis[1] * e1[0]};

            // THE SURFACE IS CHOSEN BY A MEASURED CRITERION, NOT BY ITS NAME OR ITS POSITION IN A LIST.
            // Every boundary of the rotating region whose role was classified BLADE is measured, its
            // perpendicular distance to the axis is recorded, and the one with the LARGEST measured
            // distance is used (name as the tie-break, so a tie is still deterministic). On this geometry
            // BuildAir3bMrfRegions renames 27 boundaries to exactly "BLADE", so list order would be
            // arbitrary; a measured choice cannot be.
            Boundary measureBoundary = null;
            String measureName = null;
            double[] centroidBefore = null;
            double radiusBefore = -1.0;
            double azimuthBefore = Double.NaN;
            List<String> candidateReport = new ArrayList<String>();
            int candidateCount = 0;
            for (Boundary b : rotating.getBoundaryManager().getBoundaries()) {
                String n = b.getPresentationName();
                if (!n.startsWith("BLADE")) continue;
                candidateCount++;
                double[] c = labCentroidXYZ(sim, b);
                double r = planeCoords(c, motionOrigin, e1, e2)[2];
                // The role name is shared by every blade-role boundary, so the index is what makes one
                // candidate row distinguishable from another inside this record; the chosen surface is
                // additionally identified by the measured centroid written into both certificates.
                candidateReport.add("#" + candidateCount + ":" + n + "=" + fmt(r));
                boolean better = (measureBoundary == null) || (r > radiusBefore)
                        || (r == radiusBefore && n.compareTo(measureName) < 0);
                if (better) {
                    measureBoundary = b;
                    measureName = n;
                    centroidBefore = c;
                    radiusBefore = r;
                }
            }
            if (measureBoundary == null)
                throw new IllegalStateException("No BLADE boundary is available to measure rotation on;"
                        + " refusing to substitute an axisymmetric surface, which would show no rotation");
            String measureRegion = rotating.getPresentationName();
            azimuthBefore = Math.atan2(planeCoords(centroidBefore, motionOrigin, e1, e2)[1],
                                       planeCoords(centroidBefore, motionOrigin, e1, e2)[0]);
            if (!(radiusBefore >= minMeasureRadius))
                throw new IllegalStateException("The measured boundary " + measureName + " has a lab-frame"
                        + " centroid only " + fmt(radiusBefore) + " m from the rotation axis, below the declared"
                        + " measurement floor " + fmt(minMeasureRadius) + " m; an azimuth measured that close to"
                        + " the axis is not evidence of rotation. Candidate distances: " + candidateReport);

            // The derived segment identity is ASSERTED against the clock AND against the previous segment's
            // own record, so a mis-chained stage cannot silently run "segment 2" on the steady checkpoint.
            double handoffRecordedTime = Double.NaN;
            boolean handoffRecorded = false;
            if (secondSegment) {
                if (!(physicalTimeBefore > 0.0))
                    throw new IllegalStateException("Segment 2 was given a checkpoint but the physical clock is "
                            + physicalTimeBefore + "; the chain is not the one this stage declares");
                File s1 = new File(dir, "air3b_transient_s1_measurements.json");
                if (!s1.isFile())
                    throw new IllegalStateException("Segment 2 requires segment 1's own record "
                            + s1.getName() + " to prove the checkpoint handoff; it is absent");
                String text = new String(java.nio.file.Files.readAllBytes(s1.toPath()), StandardCharsets.UTF_8);
                String s1Status = rawText(text, "status");
                String s1Index = rawText(text, "segment_index");
                String s1Finite = rawField(text, "finite_loads");
                handoffRecordedTime = rawNumber(text, "physical_time_reached_s");
                handoffRecorded = true;
                if (!"ok".equals(s1Status) || !"s1".equals(s1Index) || !"true".equals(s1Finite))
                    throw new IllegalStateException("Segment 1's record does not certify the handoff: status="
                            + s1Status + " segment_index=" + s1Index + " finite_loads=" + s1Finite);
                double clockTol = Math.max(1e-12, 1e-9 * Math.abs(handoffRecordedTime));
                if (!(Math.abs(physicalTimeBefore - handoffRecordedTime) <= clockTol))
                    throw new IllegalStateException("Segment 2 starts at physical time " + physicalTimeBefore
                            + " s but segment 1 recorded reaching " + handoffRecordedTime
                            + " s; the checkpoint handoff does not match (tolerance " + fmt(clockTol) + " s)");
            } else if (physicalTimeBefore > 0.0) {
                throw new IllegalStateException("Segment 1 was given a checkpoint with a non-zero clock ("
                        + physicalTimeBefore + "); refusing to re-initialise over a continuation");
            }

            // Initialize ONLY from a zero clock. On a continuation the loaded field is the whole point, and this
            // project's steady chain guards the same call for the same reason.
            boolean initializedHere = (physicalTimeBefore <= 0.0);
            if (initializedHere) sim.getSolution().initializeSolution();
            boolean finite = true;
            int chunkCount = 0;
            int stepsExecuted = 0;
            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_transient_" + index + "_history.csv")), StandardCharsets.UTF_8))) {
                w.write("chunk,iteration,physical_time_s,thrust_z_n,torque_z_nm\n");
                while (sim.getSolution().getPhysicalTime() < physicalTarget) {
                    sim.getSimulationIterator().step(chunkSteps);
                    chunkCount++;
                    stepsExecuted += chunkSteps;
                    double t = sim.getSolution().getPhysicalTime();
                    double f = Double.NaN, m = Double.NaN;
                    try { f = thrust.getReportMonitorValue(); } catch (Throwable ignored) { }
                    try { m = torque.getReportMonitorValue(); } catch (Throwable ignored) { }
                    if (!Double.isFinite(f) || !Double.isFinite(m)) finite = false;
                    w.write(chunkCount + "," + sim.getSimulationIterator().getCurrentIteration() + ","
                            + t + "," + f + "," + m + "\n");
                    w.flush();
                    if (!finite) break;
                    if (stepsExecuted > 100000) throw new IllegalStateException("Transient step budget exceeded");
                }
            }
            double reached = sim.getSolution().getPhysicalTime();
            int iterationAfter = sim.getSimulationIterator().getCurrentIteration();
            int innerIterationsDelta = iterationAfter - iterationBefore;

            // READBACK of what actually ran, not of what was requested: the elapsed steps from the clock and
            // from the iteration counter, against the declared count. A declared step count that the clock
            // does not show is exactly the gap this readback exists to close.
            double stepsFromClock = (reached - physicalTimeBefore) / dt;
            boolean stepsDeclaredMatch = stepsExecuted == stepsThisSegment;
            boolean clockMatchesSteps = Math.abs(stepsFromClock - stepsExecuted) <= 1e-6 * Math.max(1.0, stepsExecuted);
            boolean iterationAdvanced = innerIterationsDelta >= stepsExecuted;

            double[] centroidAfter = labCentroidXYZ(sim, measureBoundary);
            double[] planeAfter = planeCoords(centroidAfter, motionOrigin, e1, e2);
            double azimuthAfter = Math.atan2(planeAfter[1], planeAfter[0]);
            double radiusAfter = planeAfter[2];
            double measuredDeg = Math.toDegrees(azimuthAfter - azimuthBefore);
            while (measuredDeg > 180.0) measuredDeg -= 360.0;
            while (measuredDeg < -180.0) measuredDeg += 360.0;

            double[] displacement = {centroidAfter[0] - centroidBefore[0],
                                     centroidAfter[1] - centroidBefore[1],
                                     centroidAfter[2] - centroidBefore[2]};
            double measuredChord = Math.sqrt(displacement[0] * displacement[0] + displacement[1] * displacement[1]
                    + displacement[2] * displacement[2]);
            double advancedTime = reached - physicalTimeBefore;
            double expectedDeg = advancedTime * rpm * 6.0;                   // 6 deg/s per rpm
            double expectedChord = 2.0 * radiusBefore * Math.sin(Math.toRadians(Math.abs(expectedDeg)) / 2.0);
            double chordTol = 2.0 * radiusBefore * Math.sin(Math.toRadians(rotationTolDeg) / 2.0) + 1e-9;
            double radiusTol = Math.max(1e-9, 1e-3 * radiusBefore);

            boolean advanced = advancedTime > 0.0;
            boolean angleMatches = Math.abs(measuredDeg - expectedDeg) <= rotationTolDeg;
            boolean chordMatches = Math.abs(measuredChord - expectedChord) <= chordTol;
            boolean realDisplacement = measuredChord > 0.0
                    && Math.abs(measuredDeg) >= 0.5 * Math.abs(expectedDeg);
            boolean radiusPreserved = Math.abs(radiusAfter - radiusBefore) <= radiusTol;
            boolean finiteMeasurement = Double.isFinite(measuredDeg) && Double.isFinite(expectedDeg)
                    && Double.isFinite(measuredChord) && Double.isFinite(radiusBefore) && Double.isFinite(radiusAfter);
            boolean allGates = finite && advanced && stepsDeclaredMatch && clockMatchesSteps && iterationAdvanced
                    && finiteMeasurement && angleMatches && chordMatches && realDisplacement && radiusPreserved;
            String status = allGates ? "ok" : "gate_failed";

            double derivedDeg = reached * rpm * 360.0 / 60.0;
            // The checkpoint is written FIRST, so an interface-snapshot error can no longer discard a
            // completed arc; the reviewer flagged that ordering after the first extended run.
            sim.saveState(output.getAbsolutePath());
            try {
                interfaceSnapshot(sim, dir, "after_motion", index);
            } catch (Throwable t) {
                System.out.println("Interface snapshot after motion failed (checkpoint already saved): " + t.getMessage());
            }
            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_transient_" + index + "_verdict.json")), StandardCharsets.UTF_8))) {
                w.write("{\"status\":\"" + status + "\""
                        + ",\"status_meaning\":\"ok means this transient SEGMENT OPERATION completed AND its"
                        + " rotation evidence held (real displacement, azimuth matching the segment's own clock"
                        + " advance, off-axis centroid, preserved radius, finite loads); gate_failed means the"
                        + " operation finished but the evidence did not, and is never citable as a success\""
                        + ",\"segment_index\":\"" + index + "\""
                        + ",\"state\":\"" + (finite ? "REVOLUTION_COMPLETED" : "REVOLUTION_DIVERGED") + "\""
                        + ",\"rpm\":" + jnum(rpm) + ",\"time_step_s\":" + jnum(dt)
                        + ",\"inner_iteration_cap\":" + innerCap
                        + ",\"chunk_steps\":" + chunkSteps
                        + ",\"steps_declared\":" + stepsThisSegment + ",\"steps_executed\":" + stepsExecuted
                        + ",\"steps_from_clock\":" + jnum(stepsFromClock)
                        + ",\"inner_iteration_before\":" + iterationBefore
                        + ",\"inner_iteration_after\":" + iterationAfter
                        + ",\"inner_iterations_delta\":" + innerIterationsDelta
                        + ",\"physical_time_target_s\":" + jnum(physicalTarget)
                        + ",\"physical_time_reached_s\":" + jnum(reached)
                        + ",\"physical_time_before_s\":" + jnum(physicalTimeBefore)
                        + ",\"handoff_recorded_time_s\":" + (handoffRecorded ? jnum(handoffRecordedTime) : "null")
                        + ",\"handoff_recorded\":" + handoffRecorded
                        + ",\"initialized_before\":" + initializedBefore
                        + ",\"initialized_before_gate\":\"" + jstr(initializedFlagGate) + "\""
                        + ",\"gate_steps_declared_match\":" + stepsDeclaredMatch
                        + ",\"gate_clock_matches_steps\":" + clockMatchesSteps
                        + ",\"gate_iteration_advanced\":" + iterationAdvanced
                        + ",\"gate_real_displacement\":" + realDisplacement
                        + ",\"gate_angle_matches_expected\":" + angleMatches
                        + ",\"gate_chord_matches_expected\":" + chordMatches
                        + ",\"gate_radius_preserved\":" + radiusPreserved
                        + ",\"gate_finite_measurement\":" + finiteMeasurement
                        + ",\"degrees_rotated\":" + jnum(derivedDeg)
                        + ",\"degrees_rotated_is_derived\":true"
                        + ",\"reference_frame_before\":\"" + jstr(frameBefore) + "\",\"reference_frame_after\":\"" + jstr(frameAfter) + "\""
                        + ",\"motion_after\":\"" + jstr(motionAfter) + "\""
                        + ",\"motion_reused_from_checkpoint\":" + motionReused
                        + ",\"motion_axis_set\":" + (motionReused ? "\"reused_checked_not_set\"" : "\"" + jstr(axisSet) + "\"")
                        + ",\"motion_origin_set\":" + (motionReused ? "\"reused_checked_not_set\"" : "\"" + jstr(originSet) + "\"")
                        + ",\"initialized_here\":" + initializedHere
                        + ",\"finite_loads\":" + finite + ",\"convergence_verified\":false"
                        + ",\"measured_rotation_angle_deg\":" + jnum(measuredDeg)
                        + ",\"expected_rotation_angle_deg\":" + jnum(expectedDeg)
                        + ",\"rotation_tolerance_deg\":" + jnum(rotationTolDeg)
                        + ",\"derived_rotation_angle_deg\":" + jnum(derivedDeg)
                        + ",\"measured_chord_m\":" + jnum(measuredChord)
                        + ",\"expected_chord_m\":" + jnum(expectedChord)
                        + ",\"rotation_measurement_basis\":\"area-weighted lab-frame centroid of one boundary"
                        + " enumerated from region " + jstr(measureRegion) + " (never looked up loose), read through the"
                        + " built-in Position field function component functions; the boundary is CHOSEN by the"
                        + " largest MEASURED off-axis distance, not by its name or list order; the frame relation"
                        + " used for the azimuth is the rotation axis READ BACK from the motion, and the derived"
                        + " value is physical_time x rpm and is NOT a measurement\""
                        + ",\"measured_boundary\":\"" + jstr(measureName) + "\""
                        + ",\"centroid_before_xyz_m\":[" + jnum(centroidBefore[0]) + "," + jnum(centroidBefore[1])
                        + "," + jnum(centroidBefore[2]) + "]"
                        + ",\"centroid_after_xyz_m\":[" + jnum(centroidAfter[0]) + "," + jnum(centroidAfter[1])
                        + "," + jnum(centroidAfter[2]) + "]"
                        + ",\"radius_before_m\":" + jnum(radiusBefore) + ",\"radius_after_m\":" + jnum(radiusAfter)
                        + ",\"measurement_min_radius_m\":" + jnum(minMeasureRadius)
                        + ",\"rotation_axis_readback\":[" + jnum(motionAxis[0]) + "," + jnum(motionAxis[1]) + ","
                        + jnum(motionAxis[2]) + "]"
                        + ",\"rotation_axis_requested\":[" + jnum(requestedAxis[0]) + "," + jnum(requestedAxis[1])
                        + "," + jnum(requestedAxis[2]) + "]"
                        + ",\"rotation_origin_readback_m\":[" + jnum(motionOrigin[0]) + "," + jnum(motionOrigin[1])
                        + "," + jnum(motionOrigin[2]) + "]"
                        + ",\"rotation_origin_requested_m\":[" + jnum(requestedOrigin[0]) + ","
                        + jnum(requestedOrigin[1]) + "," + jnum(requestedOrigin[2]) + "]"
                        + ",\"rotation_origin_deflection_m\":" + jnum(originDeflection)
                        + ",\"rotation_origin_tolerance_m\":" + jnum(originTolM)
                        + ",\"rotation_rate_readback_rad_s\":" + jnum(motionRateSi)
                        + ",\"rotation_rate_requested_rad_s\":" + jnum(requestedRateSi)
                        + ",\"motion_class\":\"" + jstr(motionClass) + "\""
                        + ",\"output\":\"" + jstr(output.getName()) + "\"}\n");
            }
            // Measurement-level certificate. `status: ok` means THE SEGMENT OPERATION COMPLETED AND ITS
            // ROTATION EVIDENCE HELD. Rotation is proved by the MEASURED centroid displacement; the
            // physical-time x rpm value is carried beside it and labelled derived, because this project's own
            // rule is that a quantity computed from the input RPM is not a measurement of the rotor's motion.
            // Convergence / periodicity / engineering are separate fields and are NOT claimed here.
            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_transient_" + index + "_measurements.json")),
                    StandardCharsets.UTF_8))) {
                w.write("{\"status\":\"" + status + "\""
                        + ",\"audit\":\"air3b_transient_segment\""
                        + ",\"segment_index\":\"" + index + "\""
                        + ",\"status_meaning\":\"ok means the segment operation completed and its rotation"
                        + " evidence held; rotation is the measured centroid displacement, while convergence,"
                        + " periodicity and engineering are separate fields and are NOT claimed here\""
                        + ",\"measured_rotation_angle_deg\":" + jnum(measuredDeg)
                        + ",\"expected_rotation_angle_deg\":" + jnum(expectedDeg)
                        + ",\"rotation_tolerance_deg\":" + jnum(rotationTolDeg)
                        + ",\"derived_rotation_angle_deg\":" + jnum(derivedDeg)
                        + ",\"measured_chord_m\":" + jnum(measuredChord)
                        + ",\"expected_chord_m\":" + jnum(expectedChord)
                        + ",\"measured_boundary\":\"" + jstr(measureName) + "\""
                        + ",\"measured_boundary_region\":\"" + jstr(measureRegion) + "\""
                        + ",\"blade_candidate_count\":" + candidateCount
                        + ",\"blade_candidate_offaxis_radius_m\":\"" + jstr(String.join(";", candidateReport)) + "\""
                        + ",\"measured_centroid_before_xyz_m\":[" + jnum(centroidBefore[0]) + ","
                        + jnum(centroidBefore[1]) + "," + jnum(centroidBefore[2]) + "]"
                        + ",\"measured_centroid_after_xyz_m\":[" + jnum(centroidAfter[0]) + ","
                        + jnum(centroidAfter[1]) + "," + jnum(centroidAfter[2]) + "]"
                        + ",\"measured_radius_before_m\":" + jnum(radiusBefore)
                        + ",\"measured_radius_after_m\":" + jnum(radiusAfter)
                        + ",\"measurement_min_radius_m\":" + jnum(minMeasureRadius)
                        + ",\"measured_surface_identity\":\"the role name is NOT unique (every blade-role"
                        + " boundary is renamed BLADE), so the measured surface is identified inside this"
                        + " record by its measured centroid and by its candidate index\""
                        + ",\"measurement_frame\":\"laboratory (global) frame; the built-in Position field"
                        + " function is not a co-rotating frame, and the radius-preservation gate would fail if"
                        + " it were\""
                        + ",\"motion_class\":\"" + jstr(motionClass) + "\""
                        + ",\"motion_reused_from_checkpoint\":" + motionReused
                        + ",\"rotation_axis_readback\":[" + jnum(motionAxis[0]) + "," + jnum(motionAxis[1]) + ","
                        + jnum(motionAxis[2]) + "]"
                        + ",\"rotation_origin_readback_m\":[" + jnum(motionOrigin[0]) + "," + jnum(motionOrigin[1])
                        + "," + jnum(motionOrigin[2]) + "]"
                        + ",\"rotation_origin_deflection_m\":" + jnum(originDeflection)
                        + ",\"rotation_rate_readback_rad_s\":" + jnum(motionRateSi)
                        + ",\"rotation_rate_requested_rad_s\":" + jnum(requestedRateSi)
                        + ",\"time_step_s\":" + jnum(dt)
                        + ",\"chunk_steps\":" + chunkSteps
                        + ",\"steps_declared\":" + stepsThisSegment
                        + ",\"steps_executed\":" + stepsExecuted
                        + ",\"steps_from_clock\":" + jnum(stepsFromClock)
                        + ",\"inner_iteration_cap\":" + innerCap
                        + ",\"inner_iteration_before\":" + iterationBefore
                        + ",\"inner_iteration_after\":" + iterationAfter
                        + ",\"inner_iterations_delta\":" + innerIterationsDelta
                        + ",\"physical_time_before_s\":" + jnum(physicalTimeBefore)
                        + ",\"physical_time_reached_s\":" + jnum(reached)
                        + ",\"handoff_recorded_time_s\":" + (handoffRecorded ? jnum(handoffRecordedTime) : "null")
                        + ",\"handoff_recorded\":" + handoffRecorded
                        + ",\"initialized_before\":" + initializedBefore
                        + ",\"initialized_before_gate\":\"" + jstr(initializedFlagGate) + "\""
                        + ",\"gate_steps_declared_match\":" + stepsDeclaredMatch
                        + ",\"gate_clock_matches_steps\":" + clockMatchesSteps
                        + ",\"gate_iteration_advanced\":" + iterationAdvanced
                        + ",\"gate_real_displacement\":" + realDisplacement
                        + ",\"gate_angle_matches_expected\":" + angleMatches
                        + ",\"gate_chord_matches_expected\":" + chordMatches
                        + ",\"gate_radius_preserved\":" + radiusPreserved
                        + ",\"initialized_here\":" + initializedHere
                        + ",\"finite_loads\":" + finite
                        + ",\"convergence_verified\":false"
                        + ",\"periodicity_verified\":false"
                        + ",\"engineering_verified\":false"
                        + ",\"output\":\"" + jstr(output.getName()) + "\"}\n");
            }
            if (!allGates)
                throw new IllegalStateException("Transient segment " + index + " failed its rotation evidence:"
                        + " finiteLoads=" + finite + " advanced=" + advanced
                        + " stepsDeclaredMatch=" + stepsDeclaredMatch + " clockMatchesSteps=" + clockMatchesSteps
                        + " iterationAdvanced=" + iterationAdvanced + " finiteMeasurement=" + finiteMeasurement
                        + " angleMatches=" + angleMatches + " (measured " + measuredDeg + " deg vs expected "
                        + expectedDeg + " deg, tolerance " + rotationTolDeg + " deg)"
                        + " chordMatches=" + chordMatches + " (measured " + measuredChord + " m vs expected "
                        + expectedChord + " m)" + " realDisplacement=" + realDisplacement
                        + " radiusPreserved=" + radiusPreserved);
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B transient revolution failed: " + e.getMessage(), e);
        }
    }
}
