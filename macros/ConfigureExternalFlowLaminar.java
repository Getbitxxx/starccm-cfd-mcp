/* Diagnostic control arm for the AEDC sphere: keep the mesh, geometry, flow and energy
 * equations, remove every turbulence model, and save a laminar checkpoint for the same
 * solve/audit macros.
 *
 * WHY THIS EXISTS: the SST baseline converged but gave Cd=0.5842 against the AEDC table
 * value 0.396 and the Clift-Gauvin standard-curve value 0.4178. The open question is how
 * much of that +39.8% is the fully-turbulent model (no transition model) rather than the
 * mesh. This macro changes exactly one thing: the turbulence model set. It does not touch
 * geometry, mesh, boundary values, reference values, residual gates or the CaseSpec's
 * acceptance tolerance.
 *
 * SCOPE: this is a diagnostic arm, not an E candidate. It reads case_spec.json, adopts the
 * existing physics continuum from the meshed .sim (so boundary types and values are not
 * rebuilt), disables the turbulence models in reverse-enable order, re-applies the flow
 * boundary values, verifies that no turbulence class remains, and saves
 * external_flow_laminar_meshed.sim. A failure leaves a machine-readable error record.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;
import star.energy.*;
import star.flow.*;
import star.material.*;
import star.metrics.*;

public class ConfigureExternalFlowLaminar extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_LAMINAR_PHYSICS_CONFIGURED";
    private static final String OUT_SIM = "external_flow_laminar_meshed.sim";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_laminar_physics.json");
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String bodyName = optional(spec, "body_part_name", "BODY");
            double[] velocity = array(spec, "velocity_m_s", 3);
            double speed = Math.sqrt(velocity[0] * velocity[0] + velocity[1] * velocity[1]
                    + velocity[2] * velocity[2]);
            double pressure = number(spec, "pressure_pa");
            double temperature = number(spec, "temperature_k");
            if (!Double.isFinite(speed) || speed <= 0 || Math.abs(velocity[1]) > 1e-6 * speed
                    || Math.abs(velocity[2]) > 1e-6 * speed || velocity[0] <= 0) {
                throw new IllegalArgumentException("Only +X normal inflow is supported by this release");
            }
            if (!(pressure > 0 && temperature > 0)) {
                throw new IllegalArgumentException("Flow values are outside supported physical ranges");
            }
            Region region = sim.getRegionManager().getRegion(regionName);
            if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("Volume mesh missing: this macro must run on the meshed .sim");
            if (!mesh.describesRegion(region))
                throw new IllegalStateException("The Volume Mesh does not describe the flow region");
            if (sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration() != 0)
                throw new IllegalStateException("Expected an uninitialized mesh checkpoint at iteration 0");

            List<Continuum> existing = new ArrayList<>(sim.getContinuumManager().getObjects());
            List<PhysicsContinuum> physicsOnly = new ArrayList<>();
            for (Continuum c : existing) {
                if (c instanceof PhysicsContinuum) physicsOnly.add((PhysicsContinuum) c);
            }
            if (physicsOnly.size() > 1)
                throw new IllegalStateException("More than one physics continuum is present: " + physicsOnly.size());
            PhysicsContinuum continuum;
            String continuumSource;
            if (physicsOnly.isEmpty()) {
                continuum = sim.getContinuumManager().createContinuum(PhysicsContinuum.class);
                continuumSource = "created";
            } else {
                continuum = physicsOnly.get(0);
                continuumSource = "adopted";
            }
            continuum.setPresentationName("AUTO_EXTERNAL_FLOW_LAMINAR");

            StringBuilder priorModels = new StringBuilder();
            for (Model m : continuum.getModelManager().getObjects()) {
                if (priorModels.length() > 0) priorModels.append(',');
                priorModels.append(m.getClass().getName());
            }

            // Reverse-enable order. The String overload resolves fully-qualified class names on
            // this install; the project already disables models this way in executed macros.
            // Each step is presence-guarded and recorded, so a no-op cannot be mistaken for a
            // successful removal.
            StringBuilder disabled = new StringBuilder();
            String[][] doomed = {
                {"star.kwturb.KwAllYplusWallTreatment", "KwAllYplusWallTreatment"},
                {"star.kwturb.SstKwTurbModel", "SstKwTurbModel"},
                {"star.kwturb.KOmegaTurbulence", "KOmegaTurbulence"},
                {"star.turbulence.RansTurbulenceModel", "RansTurbulenceModel"},
                {"star.turbulence.TurbulentModel", "TurbulentModel"}
            };
            for (String[] item : doomed) {
                String fqcn = item[0];
                String simple = item[1];
                if (!has(continuum, simple)) {
                    appendDisabled(disabled, fqcn, "skipped:absent");
                    continue;
                }
                try {
                    continuum.disable(fqcn);
                    appendDisabled(disabled, fqcn, "disabled");
                } catch (Throwable failed) {
                    appendDisabled(disabled, fqcn, "failed:" + oneLine(failed));
                    throw new IllegalStateException("Could not disable turbulence model " + fqcn, failed);
                }
            }

            // Space / flow / energy models must remain. Use enable() only for classes that are
            // missing, so an adopted continuum is not reconfigured more than necessary.
            ensure(continuum, ThreeDimensionalModel.class);
            ensure(continuum, SteadyModel.class);
            ensure(continuum, SingleComponentGasModel.class);
            ensure(continuum, CoupledFlowModel.class);
            ensure(continuum, IdealGasModel.class);
            ensure(continuum, CoupledEnergyModel.class);

            // No turbulence class may survive. This is the gate for the diagnostic arm: if it
            // fires, the run is not a laminar arm and must not be reported as one. The check is
            // both a positive FQCN check for the five classes we disabled and a broader name
            // check that also catches a wall-treatment or transition class we did not name.
            for (String[] item : doomed) {
                if (hasFqcn(continuum, item[0]))
                    throw new IllegalStateException("Turbulence model survived the laminar switch: " + item[0]);
            }
            StringBuilder afterModels = new StringBuilder();
            for (Model m : continuum.getModelManager().getObjects()) {
                String cn = m.getClass().getName();
                if (afterModels.length() > 0) afterModels.append(',');
                afterModels.append(cn);
                if (cn.contains("Turbul") || cn.contains("Sst") || cn.contains("KOmega")
                        || cn.contains("Kw") || cn.contains("Kepsilon") || cn.contains("Spalart")
                        || cn.contains("ReynoldsStress") || cn.contains("DetachedEddy")
                        || cn.contains("LargeEddy") || cn.contains("WallTreatment")
                        || cn.contains("Transition") || cn.contains("Gamma") || cn.contains("ReTheta")
                        || cn.contains("Intermittency") || cn.contains("ScaleAdaptive")) {
                    throw new IllegalStateException("A turbulence model survived the laminar switch: " + cn);
                }
            }
            region.setPhysicsContinuum(continuum);
            continuum.getReferenceValues().get(ReferencePressure.class).setValue(pressure);
            double referencePressure = continuum.getReferenceValues().get(ReferencePressure.class).getSIValue();
            if (!Double.isFinite(referencePressure)
                    || Math.abs(referencePressure - pressure) > 1e-8 * Math.max(1.0, pressure)) {
                throw new IllegalStateException("Reference pressure does not match the CaseSpec");
            }

            Boundary inlet = required(region, "AUTO_INLET");
            Boundary outlet = required(region, "AUTO_OUTLET");
            Boundary body = required(region, optional(spec, "body_wall_boundary_name",
                    "AUTO_" + bodyName + "_WALL"));
            String velocityOption = inlet.getConditions().get(InletVelocityOption.class)
                    .getSelectedElement().toString();
            String directionOption = inlet.getConditions().get(FlowDirectionOption.class)
                    .getSelectedElement().toString();
            if (!"MAGNITUDE_DIRECTION".equals(velocityOption) || !"BOUNDARY_NORMAL".equals(directionOption)) {
                throw new IllegalStateException("The inlet is not a boundary-normal magnitude inlet: "
                        + velocityOption + " / " + directionOption);
            }
            double outletGauge = pressure - referencePressure;
            if (!Double.isFinite(outletGauge)) throw new IllegalStateException("Non-finite outlet gauge pressure");
            set(inlet.getValues().get(VelocityMagnitudeProfile.class).getMethod(ConstantScalarProfileMethod.class), speed);
            set(inlet.getValues().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class), temperature);
            set(outlet.getValues().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class), temperature);
            set(outlet.getValues().get(StaticPressureProfile.class).getMethod(ConstantScalarProfileMethod.class), outletGauge);

            // The inlet/outlet may still carry turbulence profile conditions from the adopted
            // continuum. They are recorded, not set: a laminar arm has no turbulence equations.
            String inletTi = presence(inlet, "TurbulenceIntensityProfile");
            String inletTvr = presence(inlet, "TurbulentViscosityRatioProfile");
            String outletTi = presence(outlet, "TurbulenceIntensityProfile");
            String outletTvr = presence(outlet, "TurbulentViscosityRatioProfile");

            int farfieldWalls = 0;
            for (Boundary b : region.getBoundaryManager().getObjects()) {
                if (b.getPresentationName().startsWith("AUTO_FARFIELD_")) farfieldWalls++;
            }
            if (farfieldWalls != 4) throw new IllegalStateException("Expected four farfield boundaries, got " + farfieldWalls);

            double actualInletSpeed = inlet.getValues().get(VelocityMagnitudeProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
            double actualOutletGauge = outlet.getValues().get(StaticPressureProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
            if (!Double.isFinite(actualInletSpeed) || !Double.isFinite(actualOutletGauge)
                    || Math.abs(actualInletSpeed - speed) > 1e-9
                    || Math.abs(actualOutletGauge - outletGauge) > 1e-9)
                throw new IllegalStateException("Actual boundary profile values differ from CaseSpec");

            sim.saveState(new File(dir, OUT_SIM).getAbsolutePath());
            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"diagnostic_arm\":\"laminar\""
                        + ",\"region\":\"" + escape(regionName)
                        + "\",\"cells\":" + mesh.getCellCount()
                        + ",\"iteration\":" + sim.getSimulationIterator().getCurrentIteration()
                        + ",\"initialized\":" + sim.isInitialized()
                        + ",\"continuum\":\"" + escape(continuum.getPresentationName())
                        + "\",\"continuum_source\":\"" + continuumSource + "\""
                        + ",\"prior_models\":\"" + escape(priorModels.toString()) + "\""
                        + ",\"disabled\":\"" + escape(disabled.toString()) + "\""
                        + ",\"models_after\":\"" + escape(afterModels.toString()) + "\""
                        + ",\"inlet\":\"" + escape(inlet.getPresentationName())
                        + "\",\"outlet\":\"" + escape(outlet.getPresentationName())
                        + "\",\"body_wall\":\"" + escape(body.getPresentationName())
                        + "\",\"farfield_boundaries\":" + farfieldWalls
                        + ",\"speed_m_s\":" + speed + ",\"temperature_k\":" + temperature
                        + ",\"actual_inlet_speed_m_s\":" + actualInletSpeed
                        + ",\"actual_outlet_gauge_pressure_pa\":" + actualOutletGauge
                        + ",\"reference_pressure_pa\":" + referencePressure
                        + ",\"outlet_gauge_pressure_pa\":" + outletGauge
                        + ",\"inlet_velocity_option\":\"" + escape(velocityOption) + "\""
                        + ",\"inlet_direction_option\":\"" + escape(directionOption) + "\""
                        + ",\"inlet_turbulence_intensity_profile\":\"" + inletTi + "\""
                        + ",\"inlet_turbulent_viscosity_ratio_profile\":\"" + inletTvr + "\""
                        + ",\"outlet_turbulence_intensity_profile\":\"" + outletTi + "\""
                        + ",\"outlet_turbulent_viscosity_ratio_profile\":\"" + outletTvr + "\""
                        + ",\"output\":\"" + OUT_SIM + "\""
                        + ",\"engineering_verified\":false"
                        + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                        + ",\"diagnostic_arm\":\"laminar\""
                        + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                        + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                        + ",\"error\":\"" + escape(String.valueOf(failure)) + "\",\"complete_marker\":null}\n");
            } catch (Throwable ignored) {
                // best effort only; the original cause is what matters
            }
            throw new RuntimeException("Laminar diagnostic physics configuration failed", failure);
        }
    }

    private static boolean has(PhysicsContinuum physics, String simpleName) {
        for (Model m : physics.getModelManager().getObjects())
            if (m.getClass().getSimpleName().equals(simpleName)) return true;
        return false;
    }

    private static boolean hasFqcn(PhysicsContinuum physics, String fqcn) {
        for (Model m : physics.getModelManager().getObjects())
            if (m.getClass().getName().equals(fqcn)) return true;
        return false;
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation representation : sim.getRepresentationManager().getObjects()) {
            if (representation instanceof FvRepresentation) return (FvRepresentation) representation;
        }
        return null;
    }

    private static void ensure(PhysicsContinuum physics, Class<? extends Model> type) {
        if (!has(physics, type.getSimpleName())) physics.enable(type);
    }

    private static void appendDisabled(StringBuilder out, String fqcn, String state) {
        if (out.length() > 0) out.append(';');
        out.append(fqcn).append('=').append(state);
    }

    private static String presence(Boundary boundary, String simpleName) {
        try {
            if ("TurbulenceIntensityProfile".equals(simpleName)) {
                boundary.getValues().get(TurbulenceIntensityProfile.class);
                return "present_inactive";
            }
            if ("TurbulentViscosityRatioProfile".equals(simpleName)) {
                boundary.getValues().get(TurbulentViscosityRatioProfile.class);
                return "present_inactive";
            }
            return "unknown_profile_name";
        } catch (Throwable absent) {
            return "absent";
        }
    }

    private static String oneLine(Throwable t) {
        return String.valueOf(t.getMessage() == null ? t.toString() : t.getMessage())
                .replace('\n', ' ').replace('\r', ' ').replace('\t', ' ');
    }

    private static void set(ConstantScalarProfileMethod method, double value) {
        method.getQuantity().setDefinition(Double.toString(value));
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual) || Math.abs(actual - value) > 1e-8 * Math.max(1.0, Math.abs(value))) {
            throw new IllegalStateException("Profile value or units do not match the SI CaseSpec: " + actual
                    + " != " + value);
        }
    }

    private static Boundary required(Region region, String name) {
        Boundary boundary = region.getBoundaryManager().getBoundary(name);
        if (boundary == null) throw new IllegalStateException("Boundary missing: " + name);
        return boundary;
    }

    private static String optional(String json, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        return match.find() ? match.group(1) : fallback;
    }

    private static double number(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double[] array(String json, String key, int length) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing array " + key);
        String[] fields = match.group(1).split(",");
        if (fields.length != length) throw new IllegalArgumentException("Invalid length for " + key);
        double[] values = new double[length];
        for (int i = 0; i < length; i++) {
            values[i] = Double.parseDouble(fields[i].trim());
            if (!Double.isFinite(values[i])) throw new IllegalArgumentException("Non-finite " + key);
        }
        return values;
    }

    private static String escape(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
