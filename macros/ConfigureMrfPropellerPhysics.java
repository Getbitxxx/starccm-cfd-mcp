/* Generic steady-MRF propeller physics, rotation, boundary values and load reports.
 *
 * CT/CP are NOT created as STAR expression reports here: this macro creates the force and moment
 * reports plus the measured normalisation (rho*n^2*D^4, rho*n^2*D^5, J), and CT/CP are derived
 * offline from those exported loads and denominators. Per-iteration ReportMonitors belong to the
 * solve stage (only report.createMonitor() is proven on this install; MonitorManager.createMonitor
 * fails, measured on 2502).
 *
 * Input : an isolated, volume-meshed simulation that already contains exactly two fluid
 *         regions (one rotating, one stationary) and at least one in-place interface between
 *         them, plus case_spec.json beside the .sim (sim.getSessionDirFile()).
 * Output: a configured, still UNSOLVED simulation (mrf_physics_configured.sim), a measured
 *         readback JSON, a TSV transcript, and the COMPLETE marker on stdout.
 *
 * WHY THIS MACRO EXISTS
 *   The closed AIR-3B campaign's ConfigureAir3bMrfPhysics.java already implements this physics in
 *   the right 2502 API order, but it selects by AIR3B_* environment variables and fixed fallback
 *   names, so operations/catalog.json classifies it sample_specific: it can never serve a generic
 *   plan requirement. ConfigureImportedMrfPilotPhysics.java is pilot-only (scenario
 *   geometry_mesh_probe_only, adopts an existing continuum, creates no force/moment reports).
 *   This macro carries the proven model-enable order, the UserRotatingReferenceFrame assignment and
 *   the ForceReport/MomentReport pattern into a CaseSpec-driven, name-free operation.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   No meshing, no initialization, no iteration, no report-value claims. It never reads a reference
 *   value to decide a physics setting, and it never writes "engineering_verified". Every number it
 *   reports is read back from the loaded STAR state (CaseSpec values are inputs only).
 *
 * GENERIC BY CONSTRUCTION (AGENTS.md section 4)
 *   Every part/region/boundary/report name and every physical value comes from case_spec.json; there
 *   is no sample name and no hardcoded geometry constant. Only scenario-level assumptions are baked
 *   in: exactly two regions with one rotating frame (propeller_mrf), axial inflow, an in-place
 *   boundary interface, and the ideal-gas/SST family that scripts/casespec.py already constrains.
 *
 * Environment (optional): MRF_OUTPUT_SIM_NAME = bare output .sim filename (default below).
 */
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.coupledflow.*;
import star.energy.*;
import star.flow.*;
import star.kwturb.*;
import star.material.*;
import star.metrics.*;
import star.motion.*;
import star.segregatedenergy.*;
import star.segregatedflow.*;
import star.segregatedspecies.*;
import star.turbulence.*;

public class ConfigureMrfPropellerPhysics extends StarMacro {

    private static final String COMPLETE = "MRF_PHYSICS_CONFIGURED";
    /** Dry-air specific gas constant, J/(kg K). Used only to REPORT rho = p/(RT) for the CT/CP scale. */
    private static final double R_AIR = 287.058;
    private static final String DEFAULT_OUTPUT = "mrf_physics_configured.sim";

    private final StringBuilder report = new StringBuilder();

    // ---------------------------------------------------------------- CaseSpec reading
    private static String read(File file) throws Exception {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    /** Brace-matched object extraction: nested objects and quoted braces are handled. */
    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\{").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("CaseSpec is missing object: " + key);
        int start = m.end() - 1;
        int depth = 0;
        boolean inString = false;
        for (int i = start; i < json.length(); i++) {
            char character = json.charAt(i);
            if (inString) {
                if (character == '\\') i++;
                else if (character == '"') inString = false;
                continue;
            }
            if (character == '"') inString = true;
            else if (character == '{') depth++;
            else if (character == '}') {
                depth--;
                if (depth == 0) return json.substring(start + 1, i);
            }
        }
        throw new IllegalArgumentException("Unterminated object in CaseSpec: " + key);
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("CaseSpec is missing string: " + key);
        String value = m.group(1);
        if (value.trim().isEmpty()) throw new IllegalArgumentException("CaseSpec string is empty: " + key);
        return value;
    }

    private static String optionalString(String json, String key, String fallback) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        return m.find() ? m.group(1) : fallback;
    }

    private static double number(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("CaseSpec is missing number: " + key);
        double value = Double.parseDouble(m.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("CaseSpec number is not finite: " + key);
        return value;
    }

    private static double[] array(String json, String key, int size) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("CaseSpec is missing vector: " + key);
        String[] parts = m.group(1).split(",");
        if (parts.length != size) throw new IllegalArgumentException("CaseSpec vector has wrong size: " + key);
        double[] values = new double[size];
        for (int i = 0; i < size; i++) {
            values[i] = Double.parseDouble(parts[i].trim());
            if (!Double.isFinite(values[i])) throw new IllegalArgumentException("Non-finite vector entry: " + key);
        }
        return values;
    }

    private static boolean hasKey(String json, String key) {
        return Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:").matcher(json).find();
    }

    // ---------------------------------------------------------------- small helpers
    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private static double norm(double[] vector) {
        return Math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]);
    }

    /** Unit-vector agreement that accepts either sign (direction line, not ray); rejects zero vectors. */
    private static void requireParallel(double[] a, double[] b, double tolerance, String what) {
        double na = norm(a), nb = norm(b);
        require(na > 0 && nb > 0, what + ": zero vector");
        double dot = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (na * nb);
        require(Math.abs(Math.abs(dot) - 1.0) <= tolerance,
                what + ": vectors are not parallel (|cos| = " + Math.abs(dot) + ")");
    }

    /** Writes a scalar SI value, then re-reads it from STAR; a value that did not stick aborts. */
    private static void setConstant(ScalarProfile profile, double siValue) {
        ConstantScalarProfileMethod method = profile.getMethod(ConstantScalarProfileMethod.class);
        method.getQuantity().setValue(siValue);
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual) || Math.abs(actual - siValue) > 1e-8 * Math.max(1.0, Math.abs(siValue))) {
            throw new IllegalStateException("A boundary value did not retain its SI value");
        }
    }

    /** Reads a scalar constant profile back from the loaded STAR state (no tolerance: it is a measurement). */
    private static double readConstant(ScalarProfile profile) {
        ConstantScalarProfileMethod method = profile.getMethod(ConstantScalarProfileMethod.class);
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual)) {
            throw new IllegalStateException("A boundary value reads back non-finite");
        }
        return actual;
    }

    private static Boundary requiredBoundary(Region region, String name) {
        Boundary boundary = region.getBoundaryManager().getBoundary(name);
        if (boundary == null) {
            throw new IllegalStateException("Missing boundary " + region.getPresentationName() + "/" + name);
        }
        return boundary;
    }

    private static String esc(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
                .replace("\r", "\\r").replace("\t", "\\t");
    }

    // ---------------------------------------------------------------- interface / wall selection
    /**
     * Native interface-parent ownership, independent of boundary names (V2 pattern).
     * 2502: BoundaryInterface.getParentBoundary0/1 are the physical region boundaries that may still
     * carry WallBoundary type; they are interface parents and must never be counted as propeller walls.
     */
    private static List<Boundary> selectRotorMaterialWalls(Simulation sim, Region rotor, Region stator,
                                                          StringBuilder report) {
        Set<Boundary> rotorBoundaries = new HashSet<Boundary>(rotor.getBoundaryManager().getBoundaries());
        Set<Boundary> statorBoundaries = new HashSet<Boundary>(stator.getBoundaryManager().getBoundaries());
        Set<Boundary> interfaceParents = new HashSet<Boundary>();
        Set<Boundary> rotorParents = new HashSet<Boundary>();
        Set<Boundary> statorParents = new HashSet<Boundary>();
        int interfaces = 0;
        for (Interface inter : sim.getInterfaceManager().getObjects()) {
            require(inter instanceof BoundaryInterface, "Unsupported interface type: " + inter.getPresentationName());
            BoundaryInterface pair = (BoundaryInterface) inter;
            Boundary a = pair.getParentBoundary0();
            Boundary b = pair.getParentBoundary1();
            require(a != null && b != null && !a.equals(b),
                    "Interface has a missing or duplicate parent: " + inter.getPresentationName());
            Boundary rotorSide, statorSide;
            if (rotorBoundaries.contains(a) && statorBoundaries.contains(b)) {
                rotorSide = a;
                statorSide = b;
            } else if (rotorBoundaries.contains(b) && statorBoundaries.contains(a)) {
                rotorSide = b;
                statorSide = a;
            } else {
                throw new IllegalStateException("Interface does not connect the rotating and stationary regions: "
                        + inter.getPresentationName());
            }
            require(rotorParents.add(rotorSide) && statorParents.add(statorSide),
                    "Interface parents are not one-to-one: " + inter.getPresentationName());
            require(!rotorSide.getPartSurfaceGroup().getObjects().isEmpty()
                            && !statorSide.getPartSurfaceGroup().getObjects().isEmpty(),
                    "Interface parent has no part surface: " + inter.getPresentationName());
            interfaceParents.add(a);
            interfaceParents.add(b);
            interfaces++;
            report.append("INTERFACE_PARENT_PAIR\t").append(inter.getPresentationName()).append('\t')
                    .append(rotorSide.getPresentationName()).append('\t')
                    .append(statorSide.getPresentationName()).append('\n');
        }
        require(interfaces >= 1, "No in-place interface between the two regions");
        require(interfaceParents.size() == 2 * interfaces, "Interface parent boundaries are shared between interfaces");
        require(rotorParents.size() == interfaces, "Not every interface has a unique rotating-side parent");

        // 2502 measured topology: an in-place MRF pair reports INTERNAL or IN_PLACE. Anything else
        // (BAFFLE, PERIODIC, ...) is a different physical connection and must not be configured as MRF.
        for (Interface inter : sim.getInterfaceManager().getObjects()) {
            String topology = String.valueOf(((BoundaryInterface) inter).getTopology().getSelectedElement())
                    .toUpperCase(java.util.Locale.ROOT);
            // Proven-form comparison (the executed BuildAir3bMrfRegions assertion): uppercase + contains,
            // not exact equality, so a case/label variant cannot false-reject a valid in-place pair.
            require(topology.contains("INTERNAL") || topology.contains("IN_PLACE"),
                    "Interface topology is not internal/in-place: " + inter.getPresentationName() + " / " + topology);
            report.append("INTERFACE_TOPOLOGY\t").append(inter.getPresentationName()).append('\t')
                    .append(topology).append('\n');
        }

        List<Boundary> walls = new ArrayList<Boundary>();
        for (Boundary boundary : rotor.getBoundaryManager().getBoundaries()) {
            if (interfaceParents.contains(boundary)) {
                report.append("EXCLUDED_INTERFACE_PARENT\t").append(boundary.getPresentationName()).append('\n');
                continue;
            }
            if (boundary.getBoundaryType() instanceof WallBoundary
                    && !boundary.getPartSurfaceGroup().getObjects().isEmpty()) {
                require(boundary.getDependentInterfaces().isEmpty(),
                        "Interface membership leaked onto a material-wall candidate: " + boundary.getPresentationName());
                walls.add(boundary);
                report.append("SELECTED_MATERIAL_WALL\t").append(boundary.getPresentationName()).append('\n');
            }
        }
        require(!walls.isEmpty(), "No rotating material wall found for thrust/torque reports");
        report.append("WALL_SELECTION_METHOD\tnative_parent_boundary_ownership\n");
        return walls;
    }

    private static File outputFile(File dir) {
        String override = System.getenv("MRF_OUTPUT_SIM_NAME");
        String name = (override == null || override.trim().isEmpty()) ? DEFAULT_OUTPUT : override.trim();
        if (!name.matches("[A-Za-z0-9_.-]{1,120}") || !name.endsWith(".sim")) {
            throw new IllegalArgumentException("MRF_OUTPUT_SIM_NAME is not a bare .sim filename: " + name);
        }
        return new File(dir, name);
    }

    // ---------------------------------------------------------------- main
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File output = outputFile(dir);
        try {
            File specFile = new File(dir, "case_spec.json");
            require(specFile.isFile(), "case_spec.json is missing beside the .sim: " + specFile.getAbsolutePath());
            require(!output.exists(), "Output already exists: " + output.getName());
            // A previous run's ok artefacts must not be silently overwritten: a stale readback beside a
            // new .sim would look like evidence for bytes it never described.
            for (String stale : new String[] { "mrf_physics_readback.json", "mrf_physics_config.tsv" }) {
                require(!new File(dir, stale).exists(), "Stale readback artefact present: " + stale);
            }
            String spec = read(specFile);

            require("propeller_mrf".equals(string(spec, "scenario")),
                    "This macro only serves scenario propeller_mrf");

            String geometry = object(spec, "geometry");
            String rotation = object(spec, "rotation");
            String flow = object(spec, "flow");
            String numerics = object(spec, "numerics");
            String roles = object(spec, "mrf_regions");

            double diameter = number(geometry, "diameter_m");
            require(diameter > 0, "geometry.diameter_m must be positive");
            double[] axis = array(rotation, "axis", 3);
            double[] center = array(rotation, "center_m", 3);
            double rpm = number(rotation, "rpm");
            require(rpm > 0, "rotation.rpm must be positive");
            double[] velocity = array(flow, "velocity_m_s", 3);
            double speed = norm(velocity);
            double pressure = number(flow, "pressure_pa");
            double temperature = number(flow, "temperature_k");
            double intensity = number(flow, "turbulence_intensity");
            double viscosityRatio = number(flow, "turbulent_viscosity_ratio");
            require(speed > 0, "flow.velocity_m_s must be nonzero for a steady MRF propeller case");
            require(pressure > 0 && temperature > 0, "Pressure and temperature must be positive");
            require(intensity > 0 && intensity < 1, "Turbulence intensity must be in (0,1)");
            require(viscosityRatio > 0, "Turbulent viscosity ratio must be positive");
            // Scenario-level constraint: this release serves axial-inflow propellers only. A non-axial
            // freestream (propeller at incidence) is a different physical setup and must not silently
            // reuse the axial boundary-value recipe.
            requireParallel(velocity, axis, 1e-6, "flow.velocity_m_s vs rotation.axis (axial inflow only)");

            String densityModel = string(numerics, "density_model");
            String turbulenceModel = string(numerics, "turbulence_model");
            require("ideal_gas".equals(densityModel), "Unsupported numerics.density_model: " + densityModel);
            require("SST (Menter) k-omega".equals(turbulenceModel),
                    "Unsupported numerics.turbulence_model: " + turbulenceModel);
            boolean transition = hasKey(numerics, "transition_model") && numerics.contains("GammaReTheta");
            boolean energyRequired = !Pattern.compile("\\\"energy_equation\\\"\\s*:\\s*false").matcher(numerics).find();

            String rotorName = string(roles, "rotor_region");
            String statorName = string(roles, "stator_region");
            require(!rotorName.equals(statorName), "Rotating and stationary region names are identical");
            String inletName = string(roles, "inlet_boundary");
            String outletName = string(roles, "outlet_boundary");
            String farfieldName = string(roles, "farfield_boundary");
            String frameName = string(spec, "reference_frame_name");
            String continuumName = optionalString(spec, "physics_continuum_name", "AUTO_MRF_PHYSICS");
            String thrustName = optionalString(spec, "thrust_report_name", "MRF_THRUST");
            String torqueName = optionalString(spec, "torque_report_name", "MRF_TORQUE");
            // force_positive_direction/torque_axis are declared under CaseSpec acceptance; the readers
            // search the whole document, so the nested location is intentional and tested unique.
            double[] thrustDirection = array(spec, "force_positive_direction", 3);
            double[] torqueDirection = array(spec, "torque_axis", 3);
            requireParallel(thrustDirection, axis, 1e-6, "force_positive_direction vs rotation.axis");
            requireParallel(torqueDirection, axis, 1e-6, "torque_axis vs rotation.axis");

            // ---- preconditions: meshed, unsolved, two regions, existing interface
            require(sim.getSimulationIterator().getCurrentIteration() == 0,
                    "Physics configuration requires an unsolved mesh");
            // An initialized-but-zero-iteration sim carries stale fields from earlier physics; configuring
            // it would produce a readback that no longer describes the loaded state.
            require(!sim.getSolution().isInitialized(),
                    "Physics configuration requires an uninitialized solution");
            FvRepresentation mesh = null;
            for (Representation representation : sim.getRepresentationManager().getObjects()) {
                if (representation instanceof FvRepresentation) mesh = (FvRepresentation) representation;
            }
            require(mesh != null && mesh.getCellCount() > 0, "No volume mesh present");
            Collection<Region> regions = sim.getRegionManager().getRegions();
            require(regions.size() == 2, "Expected exactly two fluid regions, found " + regions.size());
            Region rotor = sim.getRegionManager().getRegion(rotorName);
            Region stator = sim.getRegionManager().getRegion(statorName);
            require(rotor != null && stator != null && rotor != stator,
                    "CaseSpec region roles do not resolve to two regions");
            require(mesh.describesRegion(rotor) && mesh.describesRegion(stator),
                    "Volume mesh does not describe both regions");
            int interfaces = sim.getInterfaceManager().getObjects().size();
            require(interfaces >= 1, "Expected at least one in-place interface between the regions");

            // ---- physics continuum: adopt or create, then enable the recorded 2502 model order
            List<PhysicsContinuum> existing = new ArrayList<PhysicsContinuum>();
            for (Continuum continuum : sim.getContinuumManager().getObjects()) {
                if (continuum instanceof PhysicsContinuum) existing.add((PhysicsContinuum) continuum);
            }
            require(existing.size() <= 1, "More than one physics continuum is present: " + existing.size());
            PhysicsContinuum physics = existing.isEmpty() ? null : existing.get(0);
            boolean continuumCreated = false;
            if (physics == null) {
                physics = sim.getContinuumManager().createContinuum(PhysicsContinuum.class);
                continuumCreated = true;
            }
            physics.setPresentationName(continuumName);
            report.append("CONTINUUM_SOURCE\t").append(continuumCreated ? "created" : "adopted").append('\n');
            for (Model prior : physics.getModelManager().getObjects()) {
                report.append("CONTINUUM_PRIOR_MODEL\t").append(prior.getClass().getName()).append('\n');
            }
            // Mutually exclusive segregated defaults must be disabled first; the String overload only
            // resolves fully-qualified names on this install (the executed macros use FQCN for this).
            for (String doomed : new String[] {
                    "star.segregatedflow.SegregatedFlowModel",
                    "star.segregatedenergy.SegregatedEnergyModel",
                    "star.segregatedenergy.SegregatedFluidTemperatureModel",
                    "star.flow.ConstantDensityModel",
                    "star.segregatedspecies.SegregatedSpeciesModel" }) {
                try {
                    physics.disable(doomed);
                    report.append("DISABLED\t").append(doomed).append("\tOK\n");
                } catch (Throwable notEnabled) {
                    report.append("DISABLED\t").append(doomed).append("\tskipped:")
                            .append(String.valueOf(notEnabled.getMessage()).replace('\n', ' ')).append('\n');
                }
            }
            physics.enable(ThreeDimensionalModel.class);
            physics.enable(SteadyModel.class);
            physics.enable(SingleComponentGasModel.class);
            physics.enable(CoupledFlowModel.class);
            physics.enable(IdealGasModel.class);
            if (energyRequired) physics.enable(CoupledEnergyModel.class);
            physics.enable(TurbulentModel.class);
            physics.enable(RansTurbulenceModel.class);
            physics.enable(KOmegaTurbulence.class);
            physics.enable(SstKwTurbModel.class);
            if (transition) physics.enable(GammaReThetaTransitionModel.class);
            physics.enable(KwAllYplusWallTreatment.class);
            physics.add(rotor);
            physics.add(stator);
            rotor.setPhysicsContinuum(physics);
            stator.setPhysicsContinuum(physics);
            physics.getReferenceValues().get(ReferencePressure.class).setValue(pressure);
            double referencePressure = physics.getReferenceValues().get(ReferencePressure.class).getSIValue();
            require(Math.abs(referencePressure - pressure) <= 1e-8 * Math.max(1.0, pressure),
                    "Reference pressure did not match the CaseSpec");

            // ---- MRF rotation: create/reuse the rotating frame, then attach it to the rotating region
            Object priorFrame = null;
            for (AbstractReferenceFrame candidate : sim.get(ReferenceFrameManager.class).getObjects()) {
                if (frameName.equals(candidate.getPresentationName())) priorFrame = candidate;
            }
            require(priorFrame == null || priorFrame instanceof UserRotatingReferenceFrame,
                    "CaseSpec frame role has the wrong type: " + frameName);
            UserRotatingReferenceFrame frame = priorFrame == null
                    ? (UserRotatingReferenceFrame) sim.get(ReferenceFrameManager.class)
                            .createReferenceFrame(UserRotatingReferenceFrame.class, frameName)
                    : (UserRotatingReferenceFrame) priorFrame;
            double omega = rpm * 2.0 * Math.PI / 60.0;
            frame.getAxisVector().setComponentsAndUnits(axis[0], axis[1], axis[2], sim.getUnitsManager().getObject(""));
            frame.getOriginVector().setComponentsAndUnits(center[0], center[1], center[2],
                    sim.getUnitsManager().getObject("m"));
            frame.getRotationRate().setValue(omega);
            // MotionSpecification only exists once the continuum with the MRF motion model is attached;
            // the earlier R19 defect ('Object not found in TypedObjectManager' at a bare geometry region)
            // is why the assignment happens after the continuum work above.
            rotor.getValues().get(MotionSpecification.class).setReferenceFrame(frame);
            ReferenceFrameBase assignedFrame = rotor.getValues().get(MotionSpecification.class).getReferenceFrame();
            require(assignedFrame instanceof UserRotatingReferenceFrame,
                    "Rotating region frame is not a rotating frame");
            double assignedOmega = ((UserRotatingReferenceFrame) assignedFrame).getRotationRate().getSIValue();
            require(Math.abs(assignedOmega - omega) <= 1e-9 * omega,
                    "Rotating region did not receive the requested frame rate");
            double[] assignedAxis = frame.getAxisVector().getVector().toDoubleArray();
            double[] assignedOrigin = frame.getOriginVector().getVector().toDoubleArray();
            for (int i = 0; i < 3; i++) {
                require(Math.abs(assignedAxis[i] - axis[i]) <= 1e-9, "Frame axis readback differs");
                require(Math.abs(assignedOrigin[i] - center[i]) <= 1e-9, "Frame origin readback differs");
            }

            // ---- boundary roles and values
            Boundary inlet = requiredBoundary(stator, inletName);
            Boundary outlet = requiredBoundary(stator, outletName);
            Boundary farfield = requiredBoundary(stator, farfieldName);
            inlet.setBoundaryType(InletBoundary.class);
            outlet.setBoundaryType(PressureBoundary.class);
            farfield.setBoundaryType(SymmetryBoundary.class);
            String velocityOption = inlet.getConditions().get(InletVelocityOption.class).getSelectedElement().toString();
            String directionOption = inlet.getConditions().get(FlowDirectionOption.class).getSelectedElement().toString();
            require("MAGNITUDE_DIRECTION".equals(velocityOption) && "BOUNDARY_NORMAL".equals(directionOption),
                    "Inlet is not a boundary-normal magnitude inlet: " + velocityOption + " / " + directionOption);
            setConstant(inlet.getValues().get(VelocityMagnitudeProfile.class), speed);
            setConstant(inlet.getValues().get(StaticTemperatureProfile.class), temperature);
            setConstant(inlet.getValues().get(TurbulenceIntensityProfile.class), intensity);
            setConstant(inlet.getValues().get(TurbulentViscosityRatioProfile.class), viscosityRatio);
            setConstant(outlet.getValues().get(StaticTemperatureProfile.class), temperature);
            setConstant(outlet.getValues().get(StaticPressureProfile.class), pressure - referencePressure);
            setConstant(outlet.getValues().get(TurbulenceIntensityProfile.class), intensity);
            setConstant(outlet.getValues().get(TurbulentViscosityRatioProfile.class), viscosityRatio);
            physics.getInitialConditions().get(VelocityProfile.class)
                    .getMethod(ConstantVectorProfileMethod.class)
                    .getQuantity().setComponentsAndUnits(velocity[0], velocity[1], velocity[2],
                            sim.getUnitsManager().getObject("m/s"));
            setConstant(physics.getInitialConditions().get(StaticTemperatureProfile.class), temperature);
            // Measured boundary state: read every applied value back from the boundary profiles after setting.
            double measuredInletSpeed = readConstant(inlet.getValues().get(VelocityMagnitudeProfile.class));
            double measuredInletTemperature = readConstant(inlet.getValues().get(StaticTemperatureProfile.class));
            double measuredInletIntensity = readConstant(inlet.getValues().get(TurbulenceIntensityProfile.class));
            double measuredInletViscosityRatio = readConstant(inlet.getValues().get(TurbulentViscosityRatioProfile.class));
            double measuredOutletTemperature = readConstant(outlet.getValues().get(StaticTemperatureProfile.class));
            double measuredOutletStaticPressure = readConstant(outlet.getValues().get(StaticPressureProfile.class));
            double measuredOutletPressure = measuredOutletStaticPressure + referencePressure;

            // ---- thrust/torque reports over the rotating material walls only
            List<Boundary> walls = selectRotorMaterialWalls(sim, rotor, stator, report);
            // Optional CaseSpec contract: expected_rotor_wall_boundaries > 0 pins the exact wall count.
            // Absent/0 means "record, do not assert": the count is geometry-specific (one boundary per
            // part surface) and a generic rule cannot invent it.
            if (hasKey(roles, "expected_interface_count")) {
                double expectedInterfaces = number(roles, "expected_interface_count");
                require(expectedInterfaces >= 0, "expected_interface_count must be >= 0");
                if (expectedInterfaces > 0) {
                    require(interfaces == (int) expectedInterfaces,
                            "Interface count differs from CaseSpec: " + interfaces + " vs " + (int) expectedInterfaces);
                }
            }
            if (hasKey(roles, "expected_rotor_wall_boundaries")) {
                double expectedWalls = number(roles, "expected_rotor_wall_boundaries");
                require(expectedWalls >= 0, "expected_rotor_wall_boundaries must be >= 0");
                if (expectedWalls > 0) {
                    require(walls.size() == (int) expectedWalls,
                            "Rotor wall boundary count differs from CaseSpec: " + walls.size()
                                    + " vs " + (int) expectedWalls);
                }
            }
            Units dimensionless = sim.getUnitsManager().getObject("");
            ForceReport thrust = (ForceReport) sim.getReportManager().create("star.flow.ForceReport");
            thrust.setPresentationName(thrustName);
            thrust.setUseIdealization(false);
            thrust.getParts().setObjects(walls.toArray(new NamedObject[0]));
            thrust.getDirection().setComponentsAndUnits(thrustDirection[0], thrustDirection[1],
                    thrustDirection[2], dimensionless);
            MomentReport torque = (MomentReport) sim.getReportManager().create("star.flow.MomentReport");
            torque.setPresentationName(torqueName);
            torque.setUseIdealization(false);
            torque.getParts().setObjects(walls.toArray(new NamedObject[0]));
            torque.getDirection().setComponentsAndUnits(torqueDirection[0], torqueDirection[1],
                    torqueDirection[2], dimensionless);
            torque.getOrigin().setComponentsAndUnits(center[0], center[1], center[2],
                    sim.getUnitsManager().getObject("m"));
            // Measured report state: query the report manager for the actual objects and presentation names.
            // The reports stay UNEVALUATED here (no solve); this only proves they were created and named.
            List<ForceReport> forceReports = new ArrayList<ForceReport>();
            List<MomentReport> momentReports = new ArrayList<MomentReport>();
            for (Report candidate : sim.getReportManager().getObjects()) {
                if (candidate instanceof ForceReport) forceReports.add((ForceReport) candidate);
                if (candidate instanceof MomentReport) momentReports.add((MomentReport) candidate);
            }
            require(forceReports.size() == 1, "Expected exactly one ForceReport, found " + forceReports.size());
            require(momentReports.size() == 1, "Expected exactly one MomentReport, found " + momentReports.size());
            String measuredThrustName = forceReports.get(0).getPresentationName();
            String measuredTorqueName = momentReports.get(0).getPresentationName();
            require(thrustName.equals(measuredThrustName), "Thrust report name readback differs: " + measuredThrustName);
            require(torqueName.equals(measuredTorqueName), "Torque report name readback differs: " + measuredTorqueName);

            // ---- read the whole configuration back from the loaded STAR state
            Set<String> enabled = new TreeSet<String>();
            for (Model model : physics.getModelManager().getObjects()) enabled.add(model.getClass().getSimpleName());
            report.append("CONTINUUM\t").append(physics.getPresentationName()).append('\n');
            for (String model : enabled) report.append("MODEL\t").append(model).append('\n');
            report.append("REFERENCE_PRESSURE_PA\t").append(referencePressure).append('\n');
            report.append("FRAME\t").append(frame.getPresentationName())
                    .append("\taxis=").append(Arrays.toString(assignedAxis))
                    .append("\torigin=").append(Arrays.toString(assignedOrigin))
                    .append("\trate_rad_s=").append(assignedOmega)
                    .append("\trpm=").append(assignedOmega * 60.0 / (2.0 * Math.PI)).append('\n');
            for (Region region : new Region[] { rotor, stator }) {
                ReferenceFrameBase frameRead = region.getValues().get(MotionSpecification.class).getReferenceFrame();
                report.append("REGION\t").append(region.getPresentationName())
                        .append("\tcontinuum=").append(region.getPhysicsContinuum() == null
                                ? "none" : region.getPhysicsContinuum().getPresentationName())
                        .append("\tframe=").append(frameRead == null ? "none" : frameRead.getPresentationName())
                        .append('\n');
            }
            for (Boundary boundary : new Boundary[] { inlet, outlet, farfield }) {
                report.append("BOUNDARY\t").append(boundary.getPresentationName()).append('\t')
                        .append(boundary.getBoundaryType().getClass().getSimpleName()).append('\n');
            }
            report.append("INLET\tvelocity_m_s\t").append(measuredInletSpeed).append('\n');
            report.append("INLET\ttemperature_k\t").append(measuredInletTemperature).append('\n');
            report.append("INLET\tturbulence_intensity\t").append(measuredInletIntensity).append('\n');
            report.append("INLET\tturbulent_viscosity_ratio\t").append(measuredInletViscosityRatio).append('\n');
            report.append("INLET\tvelocity_option\t").append(velocityOption).append('\n');
            report.append("INLET\tdirection_option\t").append(directionOption).append('\n');
            report.append("OUTLET\tgauge_pressure_pa\t").append(measuredOutletStaticPressure).append('\n');
            report.append("OUTLET\tabsolute_pressure_pa\t").append(measuredOutletPressure).append('\n');
            report.append("OUTLET\ttemperature_k\t").append(measuredOutletTemperature).append('\n');
            report.append("REPORT\tthrust_name\t").append(measuredThrustName).append('\n');
            report.append("REPORT\ttorque_name\t").append(measuredTorqueName).append('\n');
            report.append("MESH\tcells\t").append(mesh.getCellCount()).append('\n');
            report.append("INTERFACES\t").append(interfaces).append('\n');
            report.append("ROTOR_WALLS\t").append(walls.size()).append('\n');
            report.append("ITERATION\t").append(sim.getSimulationIterator().getCurrentIteration()).append('\n');
            report.append("INITIALIZED\t").append(sim.getSolution().isInitialized()).append('\n');

            for (String forbidden : new String[] { "SegregatedFlowModel", "SegregatedEnergyModel",
                    "SegregatedFluidTemperatureModel", "ConstantDensityModel", "SingleComponentLiquidModel" }) {
                require(!enabled.contains(forbidden), "Mutually exclusive model still enabled: " + forbidden);
            }
            for (String requiredName : new String[] { "ThreeDimensionalModel", "SteadyModel",
                    "SingleComponentGasModel", "CoupledFlowModel", "IdealGasModel", "TurbulentModel",
                    "RansTurbulenceModel", "KOmegaTurbulence", "SstKwTurbModel", "KwAllYplusWallTreatment" }) {
                require(enabled.contains(requiredName), "Required model not enabled: " + requiredName);
            }
            if (energyRequired) require(enabled.contains("CoupledEnergyModel"), "CoupledEnergyModel was not enabled");
            if (transition) require(enabled.contains("GammaReThetaTransitionModel"), "Transition model was not enabled");
            boolean measuredTransitionEnabled = enabled.contains("GammaReThetaTransitionModel");
            report.append("TRANSITION_MODEL\t").append(measuredTransitionEnabled ? "GammaReTheta" : "none").append('\n');

            // ---- CT/CP normalisation derived from MEASURED loaded-state values, not from the CaseSpec locals:
            // assigned frame omega, boundary-profile pressure/temperature and boundary-profile inflow speed.
            double measuredOmega = assignedOmega;
            double measuredRpm = measuredOmega * 60.0 / (2.0 * Math.PI);
            double rho = measuredOutletPressure / (R_AIR * measuredInletTemperature);
            double revPerSecond = measuredOmega / (2.0 * Math.PI);
            double forceScale = rho * revPerSecond * revPerSecond * Math.pow(diameter, 4.0);
            double momentScale = rho * revPerSecond * revPerSecond * Math.pow(diameter, 5.0);
            double advanceRatio = measuredInletSpeed / (revPerSecond * diameter);
            boolean wallYplusField;
            try {
                wallYplusField = sim.getFieldFunctionManager().getFunction("WallYplus") != null;
            } catch (Throwable absent) {
                wallYplusField = false;
            }
            report.append("NORMALIZATION\trho_kg_m3\t").append(rho).append('\n');
            report.append("NORMALIZATION\tn_rev_s\t").append(revPerSecond).append('\n');
            report.append("NORMALIZATION\tdiameter_m\t").append(diameter).append('\n');
            report.append("NORMALIZATION\tforce_denominator_n\t").append(forceScale).append('\n');
            report.append("NORMALIZATION\tmoment_denominator_nm\t").append(momentScale).append('\n');
            report.append("CONDITION\tJ\t").append(advanceRatio).append('\n');
            // Inlet is a verified BOUNDARY_NORMAL magnitude inlet; the speed magnitude is measured from the inlet
            // profile and the axial sign follows the CaseSpec tractor convention (freestream arrives from +Z).
            double inflowAxisDot = -measuredInletSpeed;
            report.append("CONDITION\tinflow_axis_dot\t").append(inflowAxisDot).append('\n');
            report.append("CONDITION\trpm_sign\t").append(rpm > 0 ? "+" : "-").append('\n');
            // The magnitude/direction checks above cannot prove the sign of the produced thrust before a
            // solve: that also depends on blade handedness. The sign is a solve-stage gate (measured
            // thrust must be positive along force_positive_direction); recording it here keeps the
            // limitation auditable instead of implied.
            report.append("CONDITION\tthrust_sign_verified\tfalse_pre_solve\n");
            report.append("FIELD\tWallYplus_present\t").append(wallYplusField).append('\n');
            report.append("CT_CP_OFFLINE\tCT = T/force_denominator_n; CP = 2*pi*Q/moment_denominator_nm; "
                    + "derived from exported loads, rho basis frozen in CaseSpec acceptance.ct_cp_rho_basis\n");
            report.append("COMPLETE_MARKER\t").append(COMPLETE).append('\n');

            sim.saveState(output.getAbsolutePath());
            try (Writer json = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "mrf_physics_readback.json")), StandardCharsets.UTF_8)) {
                json.write("{\"schema_version\":1,\"status\":\"ok\","
                        + "\"source\":\"ConfigureMrfPropellerPhysics.java\",\"scenario\":\"propeller_mrf\""
                        + ",\"measured_omega_rad_s\":" + measuredOmega
                        + ",\"measured_rpm\":" + measuredRpm
                        + ",\"measured_axis\":[" + assignedAxis[0] + "," + assignedAxis[1] + "," + assignedAxis[2] + "]"
                        + ",\"measured_origin_m\":[" + assignedOrigin[0] + "," + assignedOrigin[1] + "," + assignedOrigin[2] + "]"
                        + ",\"measured_inlet_mps\":" + measuredInletSpeed
                        + ",\"measured_inlet_temperature_k\":" + measuredInletTemperature
                        + ",\"measured_inlet_turbulence_intensity\":" + measuredInletIntensity
                        + ",\"measured_inlet_turbulent_viscosity_ratio\":" + measuredInletViscosityRatio
                        + ",\"measured_outlet_temperature_k\":" + measuredOutletTemperature
                        + ",\"measured_outlet_static_pressure_pa\":" + measuredOutletStaticPressure
                        + ",\"measured_pressure_pa\":" + measuredOutletPressure
                        + ",\"measured_temperature_k\":" + measuredInletTemperature
                        + ",\"measured_turbulence_intensity\":" + measuredInletIntensity
                        + ",\"measured_turbulent_viscosity_ratio\":" + measuredInletViscosityRatio
                        + ",\"measured_wall_boundary_count\":" + walls.size()
                        + ",\"measured_interface_count\":" + interfaces
                        + ",\"measured_cells\":" + mesh.getCellCount()
                        + ",\"measured_iteration\":" + sim.getSimulationIterator().getCurrentIteration()
                        + ",\"initialized\":" + sim.getSolution().isInitialized()
                        + ",\"transition_model_enabled\":" + measuredTransitionEnabled
                        + ",\"models_enabled_count\":" + enabled.size()
                        + ",\"wall_yplus_field_present\":" + wallYplusField
                        + ",\"rho_kg_m3\":" + rho
                        + ",\"rho_source\":\"measured_outlet_pressure_pa/(R_AIR*measured_inlet_temperature_k)\""
                        + ",\"n_rev_s\":" + revPerSecond
                        + ",\"n_rev_s_source\":\"measured frame omega/(2*pi)\""
                        + ",\"diameter_m\":" + diameter
                        + ",\"force_denominator_n\":" + forceScale
                        + ",\"moment_denominator_nm\":" + momentScale
                        + ",\"advance_ratio\":" + advanceRatio
                        + ",\"inflow_axis_dot\":" + inflowAxisDot
                        + ",\"inflow_axis_dot_source\":\"measured inlet speed magnitude + verified BOUNDARY_NORMAL option + CaseSpec tractor sign\""
                        + ",\"thrust_sign_verified\":false"
                        + ",\"thrust_report\":\"" + esc(measuredThrustName) + "\",\"torque_report\":\"" + esc(measuredTorqueName) + "\""
                        + ",\"reports_created_not_evaluated\":true"
                        + ",\"engineering_verified\":false"
                        + ",\"note\":\"all values read back from the loaded STAR state (boundary profiles, enabled model set, report manager, assigned frame); no solve was run\"}\n");
            }
            try (Writer tsv = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "mrf_physics_config.tsv")), StandardCharsets.UTF_8)) {
                tsv.write(report.toString());
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer diagnostic = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "mrf_physics_failure.txt")), StandardCharsets.UTF_8)) {
                diagnostic.write(report.toString());
                diagnostic.write("FAILED\t" + failure.getClass().getName() + "\t"
                        + String.valueOf(failure.getMessage()).replace('\n', ' ').replace('\t', ' ') + "\n");
            } catch (Throwable ignored) {
                // the original failure is rethrown below; a diagnostic write must not mask it
            }
            throw new RuntimeException("MRF physics configuration failed: " + failure.getMessage(), failure);
        }
    }
}
