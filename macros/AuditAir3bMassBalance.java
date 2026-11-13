/* AIR-3B A3 / ② / ③: read-only global mass-conservation AND final-state audit of a saved solution.
 *
 * TWO JOBS, ONE PASS, BECAUSE THE CHECKPOINT MUST BE OPENED ONCE AND NOT DISTURBED
 *   (1) Conservation. The CaseSpec requires a mass imbalance fraction no larger than 0.001 and separates
 *       it from the monitor-stability screen, but the configured simulation carries only thrust and
 *       torque reports, so nothing else measures it. Temporary mass-flow reports are created on the
 *       inlet, the outlet and the symmetry far field, read on the volume-mesh representation, and
 *       deleted again.
 *   (2) Final-state readback (②R10, the "independent read-only verification of the final checkpoint").
 *       The shared AuditPhysicalReadiness candidate cannot supply this: it reads reference frames, not
 *       RotatingMotion objects, it never reads the physical clock, and its before/after state check
 *       compares the iteration and the modified flag but NOT the initialization flag. That macro is a
 *       registered, hash-pinned candidate of another trial line (scripts/restricted_local_mesh_trial.py
 *       SOURCES['physics'] and scripts/security/trial_physical_readiness.ps1), so extending it would
 *       silently invalidate a reviewed gate. The readback therefore lives here, in this branch's own
 *       existing macro, and it is additive: no existing row or JSON key moves.
 *
 * WHAT THE FINAL-STATE READBACK ESTABLISHES, AND WHY EACH PART IS GATED
 *   - the physical clock of the checkpoint (before and after), because "the final checkpoint is a
 *     transient state at the time the run recorded" is a claim about a number this macro can read;
 *   - every rotating object's ACTUAL parameters - RotatingMotion for the transient branch and
 *     UserRotatingReferenceFrame for the steady MRF branch - with axis, origin and rate required to be
 *     finite and the rate required to be non-zero, so "the rotation exists and is a real one" is a
 *     measurement rather than an assumption;
 *   - the mesh (cell count) and the interface inventory (names and topologies) before and after;
 *   - the target loads (thrust and torque) are finite - a non-finite load is a solver failure, not a
 *     physical result, so this stays a hard gate;
 *   - the state is UNCHANGED by this audit: the iteration, the initialization flag, the physical clock,
 *     the cell count, the interface inventory and the rotating parameters must all read identically
 *     before and after. A read-only audit that moved any of them is an audit failure and throws.
 *
 * It never initializes, iterates, meshes or saves, and deletes only the reports it created.
 * `status: ok` means THE READ-ONLY OPERATION COMPLETED AND ITS GATES HELD; conservation pass/fail and
 * every physical verdict remain separate fields.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.motion.*;
import star.flow.*;

public class AuditAir3bMassBalance extends StarMacro {

    private static final String COMPLETE = "AIR3B_MASSBALANCE_AUDIT_COMPLETE";
    private static final String[] BOUNDARIES = { "INLET", "OUTLET", "FARFIELD" };

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    /** Explicit CaseSpec role contract. Legacy jobs keep their previous names. */
    private static String role(File dir, String key, String fallback) throws Exception {
        if (!"true".equals(System.getenv("MRF_ROLE_SPEC"))) return fallback;
        String spec = java.nio.file.Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
        java.util.regex.Matcher match = java.util.regex.Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!match.find()) throw new IllegalStateException("Missing CaseSpec role: " + key);
        String result = match.group(1);
        if (match.find()) throw new IllegalStateException("Ambiguous CaseSpec role: " + key);
        return result;
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation r : sim.getRepresentationManager().getObjects())
            if (r instanceof FvRepresentation) return (FvRepresentation) r;
        return null;
    }

    private static String fmt(double v) {
        return Double.isFinite(v) ? String.format(Locale.ROOT, "%.12g", v) : String.valueOf(v);
    }

    /** JSON-safe number (②R10). Java writes NaN/Infinity as bare tokens, which RFC 8259 forbids, and a
     *  record containing one is unparseable for a strict reader - which would make this certificate
     *  uncitable and would break AGENTS.md section 2-O8 R1's "every record must be parseable" ratchet.
     *  A steady state may legitimately carry no physical clock at all, so the JSON says null and the
     *  TSV keeps the readable NaN: "this state has no clock" has to stay visible. */
    private static String jnum(double v) {
        return Double.isFinite(v) ? fmt(v) : "null";
    }

    /** JSON-safe STRING (②R10, found by the round's own ② run). Every string field must go through this.
     *  The first extended version embedded the rotating-object TSV rows verbatim, and a TSV row contains
     *  TAB characters; a raw TAB inside a JSON string is a control character, so the certificate did not
     *  parse at all. The run showed it exactly as designed - all six stages ok and every checkpoint
     *  delivered, and the CONTROLLER's numeric gate refused the record as "unreadable: Invalid control
     *  character" and downgraded the run to GATE_FAILED. Escaping here rather than at each call site is
     *  the same "one implementation" rule the citation gate follows. */
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

    private static boolean allFinite(double[] v) {
        for (double c : v) if (!Double.isFinite(c)) return false;
        return true;
    }

    private static boolean nonZero(double[] v) {
        double n = 0;
        for (double c : v) n += c * c;
        return n > 0;
    }

    /** The physical clock, or NaN when this state does not carry one. Read through Solution so a steady
     *  state that legitimately has no clock is recorded as absent rather than aborting the audit. */
    private static double physicalTime(Simulation sim) {
        try { return sim.getSolution().getPhysicalTime(); } catch (Throwable t) { return Double.NaN; }
    }

    private static boolean sameTime(double a, double b) {
        if (Double.isNaN(a) || Double.isNaN(b)) return Double.isNaN(a) && Double.isNaN(b);
        return a == b;
    }

    /** Interface names with their topology, so a read-only audit can prove it did not disturb them. */
    private static List<String> interfaceInventory(Simulation sim) {
        List<String> out = new ArrayList<String>();
        for (Interface i : sim.getInterfaceManager().getObjects()) {
            String topo = "not_a_boundary_interface";
            if (i instanceof BoundaryInterface)
                topo = String.valueOf(((BoundaryInterface) i).getTopology().getSelectedElement());
            out.add(i.getPresentationName() + "\t" + topo);
        }
        Collections.sort(out);
        return out;
    }

    /** One line per rotating object, whichever kind this state carries. `params` collects every number
     *  read and `usable` collects the two usability facts (every axis non-zero, every rate non-zero), so
     *  the caller can gate them without re-parsing the rendered text. */
    private static void rotatingInventory(Simulation sim, List<String> rows, List<Double> params,
                                          int[] counts, boolean[] usable) throws IOException {
        usable[0] = true;
        usable[1] = true;
        for (Motion m : sim.get(MotionManager.class).getObjects()) {
            if (!(m instanceof RotatingMotion)) continue;
            counts[0]++;
            RotatingMotion rm = (RotatingMotion) m;
            double[] axis = rm.getAxisVector().getVector().toDoubleArray();
            double[] origin = rm.getOriginVector().getVector().toDoubleArray();
            double rate = Double.NaN;
            if (rm.getRotationSpecification() instanceof RotationRate)
                rate = ((RotationRate) rm.getRotationSpecification()).getRotationRate().getSIValue();
            params.add(rate);
            for (double c : axis) params.add(c);
            for (double c : origin) params.add(c);
            if (!nonZero(axis)) usable[0] = false;
            if (!(Double.isFinite(rate) && rate != 0.0)) usable[1] = false;
            rows.add("MOTION\t" + m.getPresentationName() + "\t" + m.getClass().getName()
                    + "\taxis=" + show(axis) + "\torigin_m=" + show(origin) + "\trate_si=" + fmt(rate)
                    + "\taxis_finite=" + allFinite(axis) + "\taxis_nonzero=" + nonZero(axis)
                    + "\torigin_finite=" + allFinite(origin) + "\trate_finite=" + Double.isFinite(rate));
        }
        // ReferenceFrameManager.getObjects() returns AbstractReferenceFrame, not ReferenceFrame - the
        // same shape AuditPhysicalReadiness.java already works around by iterating Object and testing
        // with instanceof.
        for (Object f : sim.get(ReferenceFrameManager.class).getObjects()) {
            if (!(f instanceof UserRotatingReferenceFrame)) continue;
            counts[1]++;
            UserRotatingReferenceFrame rf = (UserRotatingReferenceFrame) f;
            double[] axis = rf.getAxisVector().getVector().toDoubleArray();
            double[] origin = rf.getOriginVector().getVector().toDoubleArray();
            double rate = rf.getRotationRate().getSIValue();
            params.add(rate);
            for (double c : axis) params.add(c);
            for (double c : origin) params.add(c);
            if (!nonZero(axis)) usable[0] = false;
            if (!(Double.isFinite(rate) && rate != 0.0)) usable[1] = false;
            rows.add("FRAME\t" + rf.getPresentationName() + "\t" + rf.getClass().getName()
                    + "\taxis=" + show(axis) + "\torigin_m=" + show(origin) + "\trate_si=" + fmt(rate)
                    + "\taxis_finite=" + allFinite(axis) + "\taxis_nonzero=" + nonZero(axis)
                    + "\torigin_finite=" + allFinite(origin) + "\trate_finite=" + Double.isFinite(rate));
        }
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        List<ClientServerObject> temporary = new ArrayList<>();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            if (iteration <= 0) throw new IllegalStateException("A solved state is required");
            boolean initializedBefore = sim.isInitialized();
            double timeBefore = physicalTime(sim);

            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No volume mesh");
            long cellsBefore = mesh.getCellCount();
            List<String> interfacesBefore = interfaceInventory(sim);
            List<String> rotatingBefore = new ArrayList<String>();
            List<Double> rotatingParamsBefore = new ArrayList<Double>();
            int[] countsBefore = new int[2];
            boolean[] usableBefore = new boolean[2];
            rotatingInventory(sim, rotatingBefore, rotatingParamsBefore, countsBefore, usableBefore);

            Region stationary = sim.getRegionManager().getRegion(role(dir, "stator_region", "AIR3B_STATIONARY"));
            Region rotating = sim.getRegionManager().getRegion(role(dir, "rotor_region", "AIR3B_ROTATING"));
            if (stationary == null || rotating == null)
                throw new IllegalStateException("Expected both AIR3B regions");

            double measuredInflow = Double.NaN;
            double measuredOmega = Double.NaN;
            double[] measuredAxis = new double[0];
            if ("true".equals(System.getenv("AIR3B_VERIFY_MRF_PARAMETERS"))) {
            if (System.getenv("AIR3B_RPM") == null || System.getenv("AIR3B_INFLOW_MPS") == null)
                throw new IllegalStateException("Missing MRF parameter declaration");
            // R16: boundary role follows its physical type; no additional name selector.
            Boundary measuredInlet = null;
            for (Region r : sim.getRegionManager().getRegions())
                for (Boundary boundary : r.getBoundaryManager().getBoundaries())
                    if (boundary.getBoundaryType() instanceof InletBoundary) {
                        if (measuredInlet != null) throw new IllegalStateException("Ambiguous inlet role");
                        measuredInlet = boundary;
                    }
            if (measuredInlet == null) throw new IllegalStateException("Missing inlet role");
            measuredInflow = measuredInlet.getValues().get(VelocityMagnitudeProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
                double expectedInflow = env("AIR3B_INFLOW_MPS", 30.0);
                double expectedOmega = env("AIR3B_RPM", 10000.0) * 2.0 * Math.PI / 60.0;
                ReferenceFrameBase assignedFrame = rotating.getValues().get(MotionSpecification.class).getReferenceFrame();
                if (!(assignedFrame instanceof UserRotatingReferenceFrame))
                    throw new IllegalStateException("Assigned rotor frame is not rotating");
                UserRotatingReferenceFrame measuredFrame = (UserRotatingReferenceFrame) assignedFrame;
                measuredOmega = measuredFrame.getRotationRate().getSIValue();
                measuredAxis = measuredFrame.getAxisVector().getVector().toDoubleArray();
                if (!Double.isFinite(measuredInflow) || !Double.isFinite(measuredOmega)
                        || Math.abs(measuredInflow - expectedInflow) > 1e-9 * Math.max(1.0, Math.abs(expectedInflow))
                        || Math.abs(measuredOmega - expectedOmega) > 1e-9 * Math.max(1.0, Math.abs(expectedOmega)))
                    throw new IllegalStateException("Parameter declaration mismatch: measured omega=" + measuredOmega
                            + " inlet=" + measuredInflow + "; declared omega=" + expectedOmega + " inlet=" + expectedInflow);
            }

            // Target loads: finite values are an execution-health fact, not a CaseSpec threshold.
            Report thrustReport = sim.getReportManager().getReport(role(dir, "thrust_report", "AIR3B_THRUST_Z"));
            Report torqueReport = sim.getReportManager().getReport(role(dir, "torque_report", "AIR3B_TORQUE_Z"));
            if (thrustReport == null || torqueReport == null)
                throw new IllegalStateException("Target-quantity reports are missing");
            double thrustValue = thrustReport.getReportMonitorValue();
            double torqueValue = torqueReport.getReportMonitorValue();
            boolean loadsFinite = Double.isFinite(thrustValue) && Double.isFinite(torqueValue);

            double limit = env("AIR3B_MASS_IMBALANCE_LIMIT", 0.001);
            Map<String, Double> flows = new LinkedHashMap<>();
            String[] outerRoles = {role(dir, "inlet_boundary", BOUNDARIES[0]), role(dir, "outlet_boundary", BOUNDARIES[1]), role(dir, "farfield_boundary", BOUNDARIES[2])};
            for (int boundaryIndex = 0; boundaryIndex < BOUNDARIES.length; boundaryIndex++) {
                String name = BOUNDARIES[boundaryIndex];
                Boundary b = stationary.getBoundaryManager().getBoundary(outerRoles[boundaryIndex]);
                if (b == null) throw new IllegalStateException("Boundary is missing: " + name);
                // ScalarReport, not the bare Report interface: setRepresentation and getParts
                // live on the scalar report type, and the mass-flow report is a scalar quantity.
                ScalarReport r = (ScalarReport) sim.getReportManager().create("star.flow.MassFlowReport");
                temporary.add(r);
                r.setRepresentation(mesh);
                r.getParts().setObjects(b);
                flows.put(name, r.getReportMonitorValue());
            }
            for (Double v : flows.values())
                if (!Double.isFinite(v)) throw new IllegalStateException("A mass-flow report returned a non-finite value");

            // ---- AFTER the read-only pass: everything above must have left the state exactly as it was.
            int iterationAfter = sim.getSimulationIterator().getCurrentIteration();
            boolean initializedAfter = sim.isInitialized();
            double timeAfter = physicalTime(sim);
            long cellsAfter = mesh.getCellCount();
            List<String> interfacesAfter = interfaceInventory(sim);
            List<String> rotatingAfter = new ArrayList<String>();
            List<Double> rotatingParamsAfter = new ArrayList<Double>();
            int[] countsAfter = new int[2];
            boolean[] usableAfter = new boolean[2];
            rotatingInventory(sim, rotatingAfter, rotatingParamsAfter, countsAfter, usableAfter);

            boolean iterationUnchanged = iterationAfter == iteration;
            boolean initializationUnchanged = initializedAfter == initializedBefore;
            boolean clockUnchanged = sameTime(timeBefore, timeAfter);
            boolean meshUnchanged = cellsAfter == cellsBefore;
            boolean interfacesUnchanged = interfacesAfter.equals(interfacesBefore);
            boolean rotatingUnchanged = rotatingAfter.equals(rotatingBefore)
                    && rotatingParamsAfter.equals(rotatingParamsBefore);
            boolean stateUnchanged = iterationUnchanged && initializationUnchanged && clockUnchanged
                    && meshUnchanged && interfacesUnchanged && rotatingUnchanged;
            boolean rotationPresent = (countsBefore[0] + countsBefore[1]) > 0;
            boolean rotationParametersFinite = true;
            for (Double v : rotatingParamsBefore) if (!Double.isFinite(v)) rotationParametersFinite = false;
            boolean rotationAxesNonZero = usableBefore[0];
            boolean rotationRatesNonZero = usableBefore[1];

            double inlet = flows.get("INLET");
            double outlet = flows.get("OUTLET");
            double farfield = flows.get("FARFIELD");
            double scale = Math.max(Math.max(Math.abs(inlet), Math.abs(outlet)), 1e-12);
            // Primary measure: the SIGNED sum over all three outer openings must vanish for a
            // closed domain, and it uses the signs rather than discarding them. The absolute
            // difference is kept as a reported cross-check because taking absolute values hides a
            // wrong outward-normal convention, which would make the two conventions agree by luck.
            double signedSum = inlet + outlet + farfield;
            double signedFraction = Math.abs(signedSum) / scale;
            double imbalance = Math.abs(Math.abs(inlet) - Math.abs(outlet));
            double fraction = imbalance / scale;
            boolean farfieldNegligible = Math.abs(farfield) <= Math.max(1e-6 * scale, 1e-12);
            // On this configuration INLET lies against its outward normal, so it must read
            // negative and OUTLET positive. Equal signs mean the convention is not what we assume.
            boolean signsAsExpected = inlet < 0.0 && outlet > 0.0;

            if (!stateUnchanged)
                throw new IllegalStateException("This read-only audit changed the state it was measuring:"
                        + " iteration " + iteration + "->" + iterationAfter
                        + ", initialized " + initializedBefore + "->" + initializedAfter
                        + ", clock " + timeBefore + "->" + timeAfter
                        + ", cells " + cellsBefore + "->" + cellsAfter
                        + ", interfaces_unchanged=" + interfacesUnchanged
                        + ", rotating_unchanged=" + rotatingUnchanged);
            if (!rotationPresent)
                throw new IllegalStateException("No rotating object (RotatingMotion or rotating reference"
                        + " frame) is present, so this checkpoint is not a rotating state");
            if (!rotationParametersFinite || !rotationAxesNonZero || !rotationRatesNonZero)
                throw new IllegalStateException("Rotation parameters are not usable: finite="
                        + rotationParametersFinite + " axes_nonzero=" + rotationAxesNonZero
                        + " rates_nonzero=" + rotationRatesNonZero);
            if (!loadsFinite)
                throw new IllegalStateException("A target load is not finite: thrust=" + thrustValue
                        + " torque=" + torqueValue);

            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_massbalance.tsv")), StandardCharsets.UTF_8))) {
                w.write("ITERATION\t" + iteration + "\n");
                w.write("ITERATION_AFTER\t" + iterationAfter + "\n");
                w.write("PHYSICAL_TIME_BEFORE_S\t" + fmt(timeBefore) + "\n");
                w.write("PHYSICAL_TIME_AFTER_S\t" + fmt(timeAfter) + "\n");
                w.write("INITIALIZED_BEFORE\t" + initializedBefore + "\n");
                w.write("INITIALIZED_AFTER\t" + initializedAfter + "\n");
                w.write("CELLS\t" + cellsBefore + "\n");
                w.write("CELLS_AFTER\t" + cellsAfter + "\n");
                w.write("ROTATING_OBJECT\tRotatingMotion\t" + countsBefore[0] + "\n");
                w.write("ROTATING_OBJECT\tUserRotatingReferenceFrame\t" + countsBefore[1] + "\n");
                for (String row : rotatingBefore) w.write(row + "\n");
                for (String row : interfacesBefore) w.write("INTERFACE\t" + row + "\n");
                w.write("LOAD\tthrust_z_n\t" + fmt(thrustValue) + "\n");
                w.write("LOAD\ttorque_z_nm\t" + fmt(torqueValue) + "\n");
                for (Map.Entry<String, Double> e : flows.entrySet())
                    w.write("MASS_FLOW\t" + e.getKey() + "\t" + fmt(e.getValue()) + "\n");
                w.write("SIGNED_SUM\tall_openings_kg_per_s\t" + fmt(signedSum) + "\n");
                w.write("SIGNED_SUM\trelative\t" + fmt(signedFraction) + "\n");
                w.write("SIGNED_SUM\tlimit\t" + fmt(limit) + "\n");
                w.write("CROSSCHECK\tabs_inlet_minus_abs_outlet_kg_per_s\t" + fmt(imbalance) + "\n");
                w.write("CROSSCHECK\tabs_relative\t" + fmt(fraction) + "\n");
                w.write("SYMMETRY_CROSSCHECK\tfarfield_kg_per_s\t" + fmt(farfield) + "\n");
                w.write("SIGN_CONVENTION\tinlet_negative_outlet_positive\t" + signsAsExpected + "\n");
                w.write("GATE\trotation_parameters_finite\t" + rotationParametersFinite + "\n");
                w.write("GATE\trotation_axes_nonzero\t" + rotationAxesNonZero + "\n");
                w.write("GATE\trotation_rates_nonzero\t" + rotationRatesNonZero + "\n");
                w.write("GATE\trotation_present\t" + rotationPresent + "\n");
                w.write("GATE\tloads_finite\t" + loadsFinite + "\n");
                w.write("GATE\tstate_unchanged\t" + stateUnchanged + "\n");
                w.write("GATE\tclock_unchanged\t" + clockUnchanged + "\n");
                w.write("GATE\tinitialization_unchanged\t" + initializationUnchanged + "\n");
                w.write("GATE\titeration_unchanged\t" + iterationUnchanged + "\n");
                w.write("GATE\tmesh_unchanged\t" + meshUnchanged + "\n");
                w.write("GATE\tinterfaces_unchanged\t" + interfacesUnchanged + "\n");
                w.write("GATE\tsign_convention_as_expected\t" + signsAsExpected + "\n");
                w.write("GATE\tfarfield_carries_no_mass\t" + farfieldNegligible + "\n");
                w.write("GATE\tmass_imbalance_within_limit\t" + (signedFraction <= limit) + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            // Measurement-level record. `status: ok` means the read-only audit OPERATION completed and its
            // execution-health gates (finite rotation parameters, finite loads, unchanged state) held;
            // whether the imbalance is inside the CaseSpec limit is the separate field
            // mass_imbalance_within_limit, so a solution that fails conservation still yields a parseable,
            // honestly-labelled record.
            try (Writer j = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_massbalance.json")), StandardCharsets.UTF_8))) {
                StringBuilder b = new StringBuilder();
                b.append("{\"status\":\"ok\"")
                 .append(",\"status_meaning\":\"ok means this read-only conservation and final-state audit")
                 .append(" OPERATION completed and its execution-health gates held (finite rotation")
                 .append(" parameters, finite loads, unchanged state); engineering pass/fail is")
                 .append(" mass_imbalance_within_limit, a separate field\"")
                 .append(",\"source\":\"AuditAir3bMassBalance.java\"")
                 .append(",\"measured_omega_rad_s\":").append(jnum(measuredOmega))
                 .append(",\"measured_inlet_mps\":").append(jnum(measuredInflow))
                 .append(",\"measured_axis\":").append(show(measuredAxis))
                 .append(",\"iteration\":").append(iteration)
                 .append(",\"iteration_after\":").append(iterationAfter)
                 .append(",\"cells\":").append(cellsBefore)
                 .append(",\"cells_after\":").append(cellsAfter)
                 .append(",\"physical_time_before_s\":").append(jnum(timeBefore))
                 .append(",\"physical_time_after_s\":").append(jnum(timeAfter))
                 .append(",\"initialized_before\":").append(initializedBefore)
                 .append(",\"initialized_after\":").append(initializedAfter)
                 .append(",\"rotating_motion_count\":").append(countsBefore[0])
                 .append(",\"rotating_reference_frame_count\":").append(countsBefore[1])
                 .append(",\"rotation_parameters_finite\":").append(rotationParametersFinite)
                 .append(",\"rotation_axes_nonzero\":").append(rotationAxesNonZero)
                 .append(",\"rotation_rates_nonzero\":").append(rotationRatesNonZero)
                 .append(",\"rotation_present\":").append(rotationPresent)
                 .append(",\"loads_finite\":").append(loadsFinite)
                 .append(",\"measured_thrust_z_n\":").append(jnum(thrustValue))
                 .append(",\"measured_torque_z_nm\":").append(jnum(torqueValue))
                 .append(",\"interface_count\":").append(interfacesBefore.size())
                 .append(",\"state_unchanged\":").append(stateUnchanged)
                 .append(",\"iteration_unchanged\":").append(iterationUnchanged)
                 .append(",\"initialization_unchanged\":").append(initializationUnchanged)
                 .append(",\"clock_unchanged\":").append(clockUnchanged)
                 .append(",\"mesh_unchanged\":").append(meshUnchanged)
                 .append(",\"interfaces_unchanged\":").append(interfacesUnchanged)
                 .append(",\"rotating_parameters_unchanged\":").append(rotatingUnchanged)
                 .append(",\"measured_signed_sum_kg_per_s\":").append(jnum(signedSum))
                 .append(",\"measured_signed_relative\":").append(jnum(signedFraction))
                 .append(",\"measured_abs_crosscheck_relative\":").append(jnum(fraction))
                 .append(",\"measured_farfield_kg_per_s\":").append(jnum(farfield))
                 .append(",\"mass_imbalance_limit\":").append(jnum(limit))
                 .append(",\"mass_imbalance_within_limit\":").append(signedFraction <= limit)
                 .append(",\"farfield_carries_no_mass\":").append(farfieldNegligible)
                 .append(",\"sign_convention_as_expected\":").append(signsAsExpected)
                 .append(",\"rotating_objects\":[");
                boolean firstRow = true;
                for (String row : rotatingBefore) {
                    String[] parts = row.split("\t");
                    b.append(firstRow ? "" : ",").append("{\"kind\":\"").append(jstr(parts[0]))
                     .append("\",\"name\":\"").append(jstr(parts[1]))
                     .append("\",\"class\":\"").append(jstr(parts[2]))
                     .append("\",\"detail\":\"").append(jstr(row.substring(
                             parts[0].length() + parts[1].length() + parts[2].length() + 3))).append("\"}");
                    firstRow = false;
                }
                b.append("],\"interfaces\":[");
                firstRow = true;
                for (String row : interfacesBefore) {
                    b.append(firstRow ? "" : ",").append('"').append(jstr(row)).append('"');
                    firstRow = false;
                }
                b.append("],\"mass_flows_kg_per_s\":{");
                boolean first = true;
                for (Map.Entry<String, Double> e : flows.entrySet()) {
                    b.append(first ? "" : ",").append('"').append(jstr(e.getKey())).append("\":")
                     .append(jnum(e.getValue()));
                    first = false;
                }
                b.append("},\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
                j.write(b.toString());
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B mass balance audit failed: " + e.getMessage(), e);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject x : temporary) {
                try { sim.deleteObjects(Collections.singletonList(x)); } catch (Throwable ignored) { }
            }
        }
    }
}
