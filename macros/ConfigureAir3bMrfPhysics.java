/* AIR-3B A3a: physics, MRF rotation, boundary values and target-quantity reports.
 *
 * Runs on the meshed two-region MRF simulation and produces a configured, still
 * unsolved simulation. The model set follows the recorded 2502 tutorial sequence for
 * a compressible turbulent gas (SingleComponentGasModel, coupled flow and energy,
 * ideal gas, RANS SST k-omega with the all-y+ wall treatment), and the boundary
 * values follow this project's executed ConfigureImportedMrfPilotPhysics patterns.
 *
 * Reads every physical value from the environment, asserts the state it depends on,
 * reads the whole configuration back into a TSV, and does NOT initialize or iterate.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
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
import star.turbulence.*;

public class ConfigureAir3bMrfPhysics extends StarMacro {

    private static final String COMPLETE = "AIR3B_PHYSICS_CONFIGURED";

    private final StringBuilder report = new StringBuilder();

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private void setConstant(ScalarProfile profile, double value) {
        profile.getMethod(ConstantScalarProfileMethod.class).getQuantity().setValue(value);
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

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            File output = new File(dir, envText("AIR3B_PHYSICS_OUTPUT", "mrf_physics.sim"));
            if (output.exists()) throw new IllegalStateException("Output already exists");

            double rpm = env("AIR3B_RPM", 10000.0);
            double inflow = env("AIR3B_INFLOW_MPS", 30.0);
            double temperature = env("AIR3B_TEMPERATURE_K", 300.0);
            double referencePressure = env("AIR3B_REFERENCE_PRESSURE_PA", 101325.0);
            double intensity = env("AIR3B_TURBULENCE_INTENSITY", 0.05);
            double viscosityRatio = env("AIR3B_TURBULENCE_VISCOSITY_RATIO", 10.0);
            double outletGauge = env("AIR3B_OUTLET_GAUGE_PA", 0.0);
            double omega = rpm * 2.0 * Math.PI / 60.0;
            if (!(rpm > 0)) throw new IllegalArgumentException("AIR3B_RPM must be positive");
            if (!(inflow > 0)) throw new IllegalArgumentException("AIR3B_INFLOW_MPS must be positive");
            if (!(temperature > 0)) throw new IllegalArgumentException("AIR3B_TEMPERATURE_K must be positive");
            if (!(referencePressure > 0)) throw new IllegalArgumentException("AIR3B_REFERENCE_PRESSURE_PA must be positive");
            if (!(intensity > 0 && intensity < 1)) throw new IllegalArgumentException("AIR3B_TURBULENCE_INTENSITY must be in (0,1)");
            if (!(viscosityRatio > 0)) throw new IllegalArgumentException("AIR3B_TURBULENCE_VISCOSITY_RATIO must be positive");

            if (sim.getSimulationIterator().getCurrentIteration() != 0)
                throw new IllegalStateException("Physics configuration requires an unsolved mesh");
            FvRepresentation mesh = null;
            for (Representation r : sim.getRepresentationManager().getObjects())
                if (r instanceof FvRepresentation) mesh = (FvRepresentation) r;
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("No volume mesh present");

            Collection<Region> regions = sim.getRegionManager().getRegions();
            if (regions.size() != 2) throw new IllegalStateException("Expected two regions");
            Region rotating = null, stationary = null;
            String rotorRole = role(dir, "rotor_region", "AIR3B_ROTATING");
            String statorRole = role(dir, "stator_region", "AIR3B_STATIONARY");
            if (rotorRole.equals(statorRole)) throw new IllegalStateException("Rotor and stator roles must differ");
            for (Region region : regions) {
                if (region.getPresentationName().equals(rotorRole)) rotating = region;
                if (region.getPresentationName().equals(statorRole)) stationary = region;
            }
            if (rotating == null || stationary == null)
                throw new IllegalStateException("CaseSpec rotor/stator regions are missing");

            // STAR may already have attached a default continuum to the regions built by
            // newRegionsFromParts, so an existing physics continuum is adopted rather than
            // assumed absent; the outcome is recorded either way.
            List<PhysicsContinuum> existing = new ArrayList<>();
            for (Continuum c : sim.getContinuumManager().getObjects())
                if (c instanceof PhysicsContinuum) existing.add((PhysicsContinuum) c);
            if (existing.size() > 1)
                throw new IllegalStateException("More than one physics continuum is present: " + existing.size());
            PhysicsContinuum physics = existing.isEmpty() ? null : existing.get(0);
            boolean continuumCreated = false;
            if (physics == null) {
                physics = sim.getContinuumManager().createContinuum(PhysicsContinuum.class);
                continuumCreated = true;
            }
            physics.setPresentationName(role(dir, "physics_continuum", "AIR3B_PHYSICS"));
            report.append("CONTINUUM_SOURCE\t").append(continuumCreated ? "created" : "adopted").append('\n');
            for (Model prior : physics.getModelManager().getObjects())
                report.append("CONTINUUM_PRIOR_MODEL\t").append(prior.getClass().getName()).append('\n');

            // Coupled and segregated models are mutually exclusive, so the segregated defaults
            // must be disabled before the coupled ones are enabled.
            // The String overload only resolves fully-qualified class names on this install;
            // the project's own executed macros disable these by FQCN for exactly that reason.
            for (String doomed : new String[] { "star.segregatedflow.SegregatedFlowModel",
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

            // Recorded 2502 order for a compressible turbulent gas.
            physics.enable(ThreeDimensionalModel.class);
            physics.enable(SteadyModel.class);
            physics.enable(SingleComponentGasModel.class);
            physics.enable(CoupledFlowModel.class);
            physics.enable(IdealGasModel.class);
            physics.enable(CoupledEnergyModel.class);
            physics.enable(TurbulentModel.class);
            physics.enable(RansTurbulenceModel.class);
            physics.enable(KOmegaTurbulence.class);
            physics.enable(SstKwTurbModel.class);
            physics.enable(KwAllYplusWallTreatment.class);
            physics.add(rotating);
            physics.add(stationary);
            rotating.setPhysicsContinuum(physics);
            stationary.setPhysicsContinuum(physics);
            if (sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("Expected three internal interfaces");
            physics.getReferenceValues().get(ReferencePressure.class).setValue(referencePressure);

            String frameRole = role(dir, "reference_frame_name", "AIR3B_MRF");
            Object priorFrame = null;
            for (AbstractReferenceFrame candidate : sim.get(ReferenceFrameManager.class).getObjects())
                if (frameRole.equals(candidate.getPresentationName())) priorFrame = candidate;
            if (priorFrame != null && !(priorFrame instanceof UserRotatingReferenceFrame))
                throw new IllegalStateException("CaseSpec frame role has the wrong type");
            UserRotatingReferenceFrame frame = priorFrame == null ? (UserRotatingReferenceFrame)
                    sim.get(ReferenceFrameManager.class).createReferenceFrame(UserRotatingReferenceFrame.class, frameRole)
                    : (UserRotatingReferenceFrame) priorFrame;
            frame.getAxisVector().setComponentsAndUnits(0.0, 0.0, 1.0, sim.getUnitsManager().getObject(""));
            frame.getOriginVector().setComponentsAndUnits(0.0, 0.0, 0.0, sim.getUnitsManager().getObject("m"));
            frame.getRotationRate().setValue(omega);
            rotating.getValues().get(MotionSpecification.class).setReferenceFrame(frame);

            Boundary inlet = stationary.getBoundaryManager().getBoundary(role(dir, "inlet_boundary", "INLET"));
            Boundary outlet = stationary.getBoundaryManager().getBoundary(role(dir, "outlet_boundary", "OUTLET"));
            Boundary farfield = stationary.getBoundaryManager().getBoundary(role(dir, "farfield_boundary", "FARFIELD"));
            if (inlet == null || outlet == null || farfield == null)
                throw new IllegalStateException("Outer boundary names are missing");
            inlet.setBoundaryType(InletBoundary.class);
            outlet.setBoundaryType(PressureBoundary.class);
            farfield.setBoundaryType(SymmetryBoundary.class);

            setConstant(inlet.getValues().get(VelocityMagnitudeProfile.class), inflow);
            // The inlet is a MAGNITUDE_DIRECTION velocity inlet whose direction option is
            // BOUNDARY_NORMAL, so the inflow enters normal to the upstream cap, i.e. along -Z. No explicit direction vector is written: the
            // boundary does not expose VelocityProfile in that mode, and the option pair is
            // recorded instead so the direction is auditable rather than assumed.
            report.append("INLET_OPTION\t").append(String.valueOf(inlet.getConditions()
                    .get(InletVelocityOption.class).getSelectedElement())).append('\n');
            report.append("INLET_DIRECTION\t").append(String.valueOf(inlet.getConditions()
                    .get(FlowDirectionOption.class).getSelectedElement())).append('\n');
            setConstant(inlet.getValues().get(StaticTemperatureProfile.class), temperature);
            setConstant(inlet.getValues().get(TurbulenceIntensityProfile.class), intensity);
            setConstant(inlet.getValues().get(TurbulentViscosityRatioProfile.class), viscosityRatio);
            setConstant(outlet.getValues().get(StaticTemperatureProfile.class), temperature);
            setConstant(outlet.getValues().get(StaticPressureProfile.class), outletGauge);
            setConstant(outlet.getValues().get(TurbulenceIntensityProfile.class), intensity);
            setConstant(outlet.getValues().get(TurbulentViscosityRatioProfile.class), viscosityRatio);

            physics.getInitialConditions().get(VelocityProfile.class)
                    .getMethod(ConstantVectorProfileMethod.class)
                    .getQuantity().setComponentsAndUnits(0.0, 0.0, -inflow, sim.getUnitsManager().getObject("m/s"));
            setConstant(physics.getInitialConditions().get(StaticTemperatureProfile.class), temperature);

            List<NamedObject> propellerWalls = new ArrayList<>();
            for (Boundary b : rotating.getBoundaryManager().getBoundaries()) {
                if (b.getBoundaryType() instanceof WallBoundary && !b.getPartSurfaceGroup().getObjects().isEmpty())
                    propellerWalls.add(b);
            }
            int expectedWalls = (int) env("AIR3B_EXPECTED_WALL_BOUNDARIES", 44.0);
            if (propellerWalls.isEmpty() || (expectedWalls > 0 && propellerWalls.size() != expectedWalls))
                throw new IllegalStateException("Found " + propellerWalls.size()
                        + " propeller wall boundaries, expected " + expectedWalls);
            Units dimensionless = sim.getUnitsManager().getObject("");
            ForceReport thrust = (ForceReport) sim.getReportManager().create("star.flow.ForceReport");
            thrust.setPresentationName(role(dir, "thrust_report", "AIR3B_THRUST_Z"));
            thrust.setUseIdealization(false);
            thrust.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
            // Thrust is reported along +Z, not -Z. The freestream is [0,0,-30] m/s with the domain's
            // upstream at +Z, so a vehicle flying +Z has its freestream arriving from +Z and
            // travelling -Z, and momentum theory puts the useful thrust opposite to the flow, i.e.
            // along +Z. A -Z report direction reported minus the physical thrust and made a working
            // propeller look like a turbine; trial air3b-mrf-pilot-20261002a measured -60.31 N that
            // way while the imposed +Z rotation and the negative +Z fluid torque prove the rotor is
            // adding energy to the flow.
            thrust.getDirection().setComponentsAndUnits(0.0, 0.0, 1.0, dimensionless);
            MomentReport torque = (MomentReport) sim.getReportManager().create("star.flow.MomentReport");
            torque.setPresentationName(role(dir, "torque_report", "AIR3B_TORQUE_Z"));
            torque.setUseIdealization(false);
            torque.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
            torque.getDirection().setComponentsAndUnits(0.0, 0.0, 1.0, dimensionless);
            torque.getOrigin().setComponentsAndUnits(0.0, 0.0, 0.0, sim.getUnitsManager().getObject("m"));

            // Read the whole configuration back before saving.
            Set<String> enabled = new TreeSet<>();
            for (Model m : physics.getModelManager().getObjects()) enabled.add(m.getClass().getSimpleName());
            report.append("CONTINUUM\t").append(physics.getPresentationName()).append('\n');
            for (String m : enabled) report.append("MODEL\t").append(m).append('\n');
            report.append("REFERENCE_PRESSURE_PA\t")
                  .append(physics.getReferenceValues().get(ReferencePressure.class).getSIValue()).append('\n');
            report.append("FRAME\t").append(frame.getPresentationName())
                  .append("\taxis=").append(Arrays.toString(frame.getAxisVector().getVector().toDoubleArray()))
                  .append("\torigin=").append(Arrays.toString(frame.getOriginVector().getVector().toDoubleArray()))
                  .append("\trate_rad_s=").append(frame.getRotationRate().getSIValue())
                  .append("\trpm=").append(frame.getRotationRate().getSIValue() * 60.0 / (2.0 * Math.PI)).append('\n');
            double assigned = Double.NaN;
            for (Region region : regions) {
                report.append("REGION\t").append(region.getPresentationName()).append("\tcontinuum=")
                      .append(region.getPhysicsContinuum() == null ? "none" : region.getPhysicsContinuum().getPresentationName())
                      .append("\tframe=")
                      .append(region.getValues().get(MotionSpecification.class).getReferenceFrame() == null ? "none"
                              : region.getValues().get(MotionSpecification.class).getReferenceFrame().getPresentationName())
                      .append('\n');
                if (region == rotating) {
                    ReferenceFrameBase rf = region.getValues().get(MotionSpecification.class).getReferenceFrame();
                    if (!(rf instanceof UserRotatingReferenceFrame))
                        throw new IllegalStateException("Rotating region frame is not a rotating frame");
                    assigned = ((UserRotatingReferenceFrame) rf).getRotationRate().getSIValue();
                }
            }
            if (!Double.isFinite(assigned) || Math.abs(assigned - omega) > 1e-9 * omega)
                throw new IllegalStateException("Rotating region did not receive the requested frame");
            for (Boundary b : new Boundary[] { inlet, outlet, farfield }) {
                report.append("BOUNDARY\t").append(b.getPresentationName()).append('\t')
                      .append(b.getBoundaryType().getClass().getSimpleName()).append('\n');
            }
            report.append("INLET\tvelocity_m_s\t")
                  .append(inlet.getValues().get(VelocityMagnitudeProfile.class)
                          .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue()).append('\n');
            report.append("INLET\ttemperature_k\t")
                  .append(inlet.getValues().get(StaticTemperatureProfile.class)
                          .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue()).append('\n');
            report.append("INLET\tturbulence_intensity\t")
                  .append(inlet.getValues().get(TurbulenceIntensityProfile.class)
                          .getMethod(ConstantScalarProfileMethod.class).getQuantity().getValue()).append('\n');
            report.append("INLET\tturbulent_viscosity_ratio\t")
                  .append(inlet.getValues().get(TurbulentViscosityRatioProfile.class)
                          .getMethod(ConstantScalarProfileMethod.class).getQuantity().getValue()).append('\n');
            report.append("OUTLET\tgauge_pressure_pa\t")
                  .append(outlet.getValues().get(StaticPressureProfile.class)
                          .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue()).append('\n');
            // Force and moment monitors are not expected to be meaningful before the solution
            // exists, so their pre-solve evaluation is attempted but never allowed to abort the
            // stage, and the outcome is recorded.
            try {
                double[] thrustDirection = thrust.getDirection().getVector().toDoubleArray();
            report.append("REPORT_DIRECTION\tAIR3B_THRUST_Z\t")
                  .append(Arrays.toString(thrustDirection)).append('\n');
            report.append("REPORT_DIRECTION\tAIR3B_TORQUE_Z\t")
                  .append(Arrays.toString(torque.getDirection().getVector().toDoubleArray())).append('\n');
            report.append("REPORT\tAIR3B_THRUST_Z\t").append(thrust.getReportMonitorValue())
                      .append("\tstate=evaluated_pre_solve\n");
            } catch (Throwable notEvaluable) {
                report.append("REPORT\tAIR3B_THRUST_Z\tNA\tstate=not_evaluable_pre_solve:")
                      .append(String.valueOf(notEvaluable.getMessage()).replace('\n', ' ')).append('\n');
            }
            try {
                report.append("REPORT\tAIR3B_TORQUE_Z\t").append(torque.getReportMonitorValue())
                      .append("\tstate=evaluated_pre_solve\n");
            } catch (Throwable notEvaluable) {
                report.append("REPORT\tAIR3B_TORQUE_Z\tNA\tstate=not_evaluable_pre_solve:")
                      .append(String.valueOf(notEvaluable.getMessage()).replace('\n', ' ')).append('\n');
            }
            report.append("PROPELLER_WALL_BOUNDARIES\t").append(propellerWalls.size()).append('\n');
            for (String forbidden : new String[] { "SegregatedFlowModel", "SegregatedEnergyModel",
                                                   "SegregatedFluidTemperatureModel", "ConstantDensityModel",
                                                   "SingleComponentLiquidModel" })
                if (enabled.contains(forbidden))
                    throw new IllegalStateException("Mutually exclusive model still enabled: " + forbidden);
            for (String required : new String[] { "ThreeDimensionalModel", "SteadyModel", "SingleComponentGasModel",
                                                  "CoupledFlowModel", "IdealGasModel", "CoupledEnergyModel",
                                                  "TurbulentModel", "RansTurbulenceModel", "KOmegaTurbulence",
                                                  "SstKwTurbModel", "KwAllYplusWallTreatment" })
                if (!enabled.contains(required))
                    throw new IllegalStateException("Required model not enabled: " + required);
            report.append("ITERATION\t").append(sim.getSimulationIterator().getCurrentIteration()).append('\n');
            report.append("INITIALIZED\t").append(sim.getSolution().isInitialized()).append('\n');
            report.append("COMPLETE_MARKER\t").append(COMPLETE).append('\n');

            sim.saveState(output.getAbsolutePath());
            // R17 measured certificate: it describes loaded STAR values, not environment declarations.
            try (Writer j = new OutputStreamWriter(new FileOutputStream(new File(dir,
                    "mrf_physics_readback.json")), StandardCharsets.UTF_8)) {
                double measuredInlet = inlet.getValues().get(VelocityMagnitudeProfile.class)
                        .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
                j.write("{\"status\":\"ok\",\"source\":\"ConfigureAir3bMrfPhysics.java\","
                        + "\"measured_omega_rad_s\":" + assigned
                        + ",\"measured_inlet_mps\":" + measuredInlet
                        + ",\"measured_axis\":" + Arrays.toString(frame.getAxisVector().getVector().toDoubleArray())
                        + ",\"measured_origin_m\":" + Arrays.toString(frame.getOriginVector().getVector().toDoubleArray())
                        + ",\"measured_wall_boundary_count\":" + propellerWalls.size()
                        + ",\"measured_cells\":" + mesh.getCellCount()
                        + ",\"measured_iteration\":" + sim.getSimulationIterator().getCurrentIteration()
                        + ",\"engineering_verified\":false}\n");
            }
            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_physics_config.tsv")), StandardCharsets.UTF_8)) {
                w.write(report.toString());
            }
            System.out.println(COMPLETE);
        } catch (Throwable e) {
            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(new File(System.getenv("AIR3B_JOB_DIR")),
                            "air3b_physics_failure.tsv")), StandardCharsets.UTF_8)) {
                w.write(report.toString());
                w.write("FAILED\t" + e.getClass().getName() + "\t"
                        + String.valueOf(e.getMessage()).replace('\n', ' ').replace('\t', ' ') + "\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("AIR-3B physics configuration failed: " + e.getMessage(), e);
        }
    }
}
