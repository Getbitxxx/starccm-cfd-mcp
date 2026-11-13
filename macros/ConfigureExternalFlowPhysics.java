/* Batched step 2 of 3: enable the physics models and set every open-boundary value.
 *
 * Model set and the enable() order are copied from ConfigureAir3bMrfPhysics, which this repository
 * has already executed against the installed 2502 build; the boundary-value calls and their
 * SI read-back check are copied from ConfigureNormalInletOutlet, also already executed. Nothing here
 * is meshed or solved. Values come from the CaseSpec; no sample constant is assumed.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;
import star.energy.*;
import star.flow.*;
import star.motion.*;
import star.kwturb.*;
import star.material.*;
import star.metrics.*;
import star.turbulence.*;
import star.segregatedflow.*;

public class ConfigureExternalFlowPhysics extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_PHYSICS_CONFIGURED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_physics.json");
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            if ("constant_laminar".equals(physicsBranch(spec))) {
                configureConstantLaminar(sim, dir, spec);
                return;
            }
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String bodyName = optional(spec, "body_part_name", "BODY");
            double[] velocity = array(spec, "velocity_m_s", 3);
            double speed = Math.sqrt(velocity[0] * velocity[0] + velocity[1] * velocity[1]
                    + velocity[2] * velocity[2]);
            double pressure = number(spec, "pressure_pa");
            double temperature = number(spec, "temperature_k");
            double intensity = number(spec, "turbulence_intensity");
            double viscosityRatio = number(spec, "turbulent_viscosity_ratio");
            if (!Double.isFinite(speed) || speed <= 0 || Math.abs(velocity[1]) > 1e-6 * speed
                    || Math.abs(velocity[2]) > 1e-6 * speed || velocity[0] <= 0) {
                throw new IllegalArgumentException("Only +X normal inflow is supported by this release");
            }
            if (!(pressure > 0 && temperature > 0 && intensity > 0 && intensity < 1 && viscosityRatio > 0)) {
                throw new IllegalArgumentException("Flow values are outside supported physical ranges");
            }
            Region region = sim.getRegionManager().getRegion(regionName);
            if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
            // STAR may already have attached a default continuum to the region built by
            // newRegionsFromParts, so an existing continuum is ADOPTED rather than assumed absent.
            // An earlier revision threw here, which the executed ConfigureAir3bMrfPhysics shows is
            // deterministic death on this path.
            PhysicsContinuum continuum = region.getPhysicsContinuum();
            String continuumSource;
            StringBuilder priorModels = new StringBuilder();
            if (continuum == null) {
                continuum = sim.getContinuumManager().createContinuum(PhysicsContinuum.class);
                continuumSource = "created";
            } else {
                continuumSource = "adopted";
                for (star.common.Model model : continuum.getModelManager().getObjects()) {
                    if (priorModels.length() > 0) priorModels.append(',');
                    priorModels.append(model.getClass().getName());
                }
            }
            continuum.setPresentationName("AUTO_EXTERNAL_FLOW_PHYSICS");
            continuum.enable(ThreeDimensionalModel.class);
            continuum.enable(SteadyModel.class);
            continuum.enable(SingleComponentGasModel.class);
            continuum.enable(CoupledFlowModel.class);
            continuum.enable(IdealGasModel.class);
            continuum.enable(CoupledEnergyModel.class);
            continuum.enable(TurbulentModel.class);
            continuum.enable(RansTurbulenceModel.class);
            continuum.enable(KOmegaTurbulence.class);
            continuum.enable(SstKwTurbModel.class);
            continuum.enable(KwAllYplusWallTreatment.class);
            region.setPhysicsContinuum(continuum);

            Boundary inlet = required(region, "AUTO_INLET");
            Boundary outlet = required(region, "AUTO_OUTLET");
            Boundary body = required(region, optional(spec, "body_wall_boundary_name",
                    "AUTO_" + bodyName + "_WALL"));
            // The reference pressure is set from the CaseSpec before the gauge is derived from it,
            // so the outlet value does not silently depend on STAR's default.
            StringBuilder enabledModels = new StringBuilder();
            for (star.common.Model model : continuum.getModelManager().getObjects()) {
                if (enabledModels.length() > 0) enabledModels.append(',');
                enabledModels.append(model.getClass().getName());
            }
            continuum.getReferenceValues().get(ReferencePressure.class).setValue(pressure);
            double referencePressure = continuum.getReferenceValues().get(ReferencePressure.class).getSIValue();
            if (!Double.isFinite(referencePressure)
                    || Math.abs(referencePressure - pressure) > 1e-8 * Math.max(1.0, pressure)) {
                throw new IllegalStateException("Reference pressure does not match the CaseSpec");
            }
            // The inlet's velocity and direction options are read back and recorded, so the intended
            // +X inflow is auditable rather than assumed from a sign check.
            String velocityOption = inlet.getConditions().get(InletVelocityOption.class)
                    .getSelectedElement().toString();
            String directionOption = inlet.getConditions().get(FlowDirectionOption.class)
                    .getSelectedElement().toString();
            // The reference-frame condition only exists when a motion model is enabled. Reading it
            // unconditionally threw star.base.neo.NeoException ('Condition not found in
            // ConditionManager') on this static case, which is how the first batch run failed. With
            // no motion model the lab frame is the only possibility, so its absence is recorded
            // rather than asserted.
            String frameOption = "NO_REFERENCE_FRAME_CONDITION";
            try {
                frameOption = inlet.getConditions().get(ReferenceFrameOption.class)
                        .getSelectedElement().toString();
            } catch (Throwable noFrameCondition) {
                frameOption = "NO_REFERENCE_FRAME_CONDITION";
                // Deterministic and API-free: a CaseSpec that declares rotation must not reach a
                // boundary whose reference-frame condition is missing.
                if (spec.contains("\"rotation\"")) {
                    throw new IllegalStateException("The CaseSpec declares rotation but the inlet has no "
                            + "reference-frame condition", noFrameCondition);
                }
            }
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
            set(inlet.getValues().get(TurbulenceIntensityProfile.class).getMethod(ConstantScalarProfileMethod.class), intensity);
            set(outlet.getValues().get(TurbulenceIntensityProfile.class).getMethod(ConstantScalarProfileMethod.class), intensity);
            set(inlet.getValues().get(TurbulentViscosityRatioProfile.class).getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            set(outlet.getValues().get(TurbulentViscosityRatioProfile.class).getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            int farfieldWalls = 0;
            for (Boundary boundary : region.getBoundaryManager().getObjects()) {
                if (boundary.getPresentationName().startsWith("AUTO_FARFIELD_")) farfieldWalls++;
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
            sim.saveState(new File(dir, "external_flow_physics.sim").getAbsolutePath());
            try (FileWriter writer = new FileWriter(new File(dir, "external_flow_physics.json"),
                    StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"" + escape(regionName)
                        + "\",\"continuum\":\"" + escape(continuum.getPresentationName())
                        + "\",\"inlet\":\"" + escape(inlet.getPresentationName())
                        + "\",\"outlet\":\"" + escape(outlet.getPresentationName())
                        + "\",\"body_wall\":\"" + escape(body.getPresentationName())
                        + "\",\"farfield_boundaries\":" + farfieldWalls
                        + ",\"speed_m_s\":" + speed + ",\"temperature_k\":" + temperature
                        + ",\"actual_inlet_speed_m_s\":" + actualInletSpeed
                        + ",\"actual_outlet_gauge_pressure_pa\":" + actualOutletGauge
                        + ",\"reference_pressure_pa\":" + referencePressure
                        + ",\"outlet_gauge_pressure_pa\":" + outletGauge
                        + ",\"turbulence_intensity\":" + intensity
                        + ",\"turbulent_viscosity_ratio\":" + viscosityRatio
                        + ",\"continuum_source\":\"" + continuumSource + "\""
                        + ",\"continuum_prior_models\":\"" + escape(priorModels.toString()) + "\""
                        + ",\"continuum_models_after_enable\":\"" + escape(enabledModels.toString()) + "\""
                        + ",\"inlet_velocity_option\":\"" + velocityOption + "\""
                        + ",\"inlet_direction_option\":\"" + directionOption + "\""
                        + ",\"inlet_reference_frame_option\":\"" + frameOption + "\""
                        + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            String message = String.valueOf(failure);
            // A failure must leave its diagnosis behind, not only a STAR log line.
            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                        + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                        + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                        + ",\"error\":\"" + escape(message) + "\",\"complete_marker\":null}\n");
            } catch (Throwable ignored) {
                // best effort only; the original cause below is what matters
            }
            throw new RuntimeException("External-flow physics configuration failed", failure);
        }
    }

    private static String physicsBranch(String json) {
        Matcher object = Pattern.compile("\"numerics\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!object.find()) throw new IllegalArgumentException("Missing numerics model selection");
        String numerics = object.group(1);
        String density = optional(numerics, "density_model", null);
        String turbulence = optional(numerics, "turbulence_model", null);
        if ("constant".equals(density) && "laminar".equals(turbulence)) {
            if (!Pattern.compile("\"energy_equation\"\\s*:\\s*false(?=\\s*(?:,|$))").matcher(numerics).find())
                throw new IllegalArgumentException("constant/laminar requires energy_equation=false");
            return "constant_laminar";
        }
        if ("ideal_gas".equals(density) && "SST (Menter) k-omega".equals(turbulence))
            return "ideal_gas_sst";
        throw new IllegalArgumentException("Unsupported or missing density_model/turbulence_model");
    }

    private static void configureConstantLaminar(Simulation sim, File dir, String spec) throws Exception {
        if (!"single_component_liquid".equals(optional(spec, "medium", null)))
            throw new IllegalArgumentException("constant/laminar requires single_component_liquid");
        double[] velocity = array(spec, "velocity_m_s", 3);
        double speed = Math.sqrt(velocity[0]*velocity[0] + velocity[1]*velocity[1] + velocity[2]*velocity[2]);
        double pressure = number(spec, "pressure_pa");
        double rho = number(spec, "rho_kg_m3"), mu = number(spec, "mu_pa_s");
        if (!(rho > 0 && mu > 0 && pressure > 0 && Double.isFinite(speed) && speed > 0)
                || velocity[0] <= 0 || Math.abs(velocity[1]) > 1e-6*speed || Math.abs(velocity[2]) > 1e-6*speed)
            throw new IllegalArgumentException("Invalid constant/laminar SI inputs or non-+X inflow");
        String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
        Region region = sim.getRegionManager().getRegion(regionName);
        if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
        // A fresh continuum prevents inherited gas/energy/turbulence models from leaking into this branch.
        PhysicsContinuum continuum = sim.getContinuumManager().createContinuum(PhysicsContinuum.class);
        continuum.setPresentationName("AUTO_EXTERNAL_FLOW_LAMINAR_PHYSICS");
        continuum.enable(ThreeDimensionalModel.class);
        continuum.enable(SingleComponentLiquidModel.class);
        continuum.enable(SegregatedFlowModel.class);
        continuum.enable(ConstantDensityModel.class);
        continuum.enable(SteadyModel.class);
        continuum.enable(LaminarModel.class);
        Liquid material = (Liquid) continuum.getModelManager().getModel(SingleComponentLiquidModel.class).getMaterial();
        ConstantMaterialPropertyMethod densityMethod = (ConstantMaterialPropertyMethod) material
                .getMaterialProperties().getMaterialProperty(ConstantDensityProperty.class).getMethod();
        ConstantMaterialPropertyMethod viscosityMethod = (ConstantMaterialPropertyMethod) material
                .getMaterialProperties().getMaterialProperty(DynamicViscosityProperty.class).getMethod();
        densityMethod.getQuantity().setValue(rho);
        viscosityMethod.getQuantity().setValue(mu);
        double actualRho = densityMethod.getQuantity().getSIValue();
        double actualMu = viscosityMethod.getQuantity().getSIValue();
        if (!Double.isFinite(actualRho) || !Double.isFinite(actualMu)
                || Math.abs(actualRho-rho) > 1e-10*rho || Math.abs(actualMu-mu) > 1e-10*mu)
            throw new IllegalStateException("Material SI readback differs from CaseSpec");
        region.setPhysicsContinuum(continuum);
        StringBuilder models = new StringBuilder();
        for (star.common.Model model : continuum.getModelManager().getObjects()) {
            String name = model.getClass().getName();
            String lower = name.toLowerCase(java.util.Locale.ROOT);
            if (name.contains("IdealGas") || name.contains("CoupledEnergy") || name.contains("SingleComponentGas")
                    || lower.contains("turb") || lower.contains("rans") || lower.contains("yplus"))
                throw new IllegalStateException("Forbidden constant/laminar model: " + name);
            if (models.length() > 0) models.append(',');
            models.append(name);
        }
        String[] requiredModels = {"ThreeDimensionalModel", "SingleComponentLiquidModel", "SegregatedFlowModel",
                "ConstantDensityModel", "SteadyModel", "LaminarModel"};
        for (String requiredModel : requiredModels)
            if (!models.toString().contains("." + requiredModel))
                throw new IllegalStateException("Required model missing: " + requiredModel);
        Boundary inlet = required(region, "AUTO_INLET"), outlet = required(region, "AUTO_OUTLET");
        required(region, stringRequired(spec, "body_wall_boundary_name"));
        continuum.getReferenceValues().get(ReferencePressure.class).setValue(pressure);
        double referencePressure = continuum.getReferenceValues().get(ReferencePressure.class).getSIValue();
        if (!Double.isFinite(referencePressure) || Math.abs(referencePressure-pressure) > 1e-8*Math.max(1.0,pressure))
            throw new IllegalStateException("Reference pressure does not match the CaseSpec");
        String velocityOption = inlet.getConditions().get(InletVelocityOption.class).getSelectedElement().toString();
        String directionOption = inlet.getConditions().get(FlowDirectionOption.class).getSelectedElement().toString();
        if (!"MAGNITUDE_DIRECTION".equals(velocityOption) || !"BOUNDARY_NORMAL".equals(directionOption))
            throw new IllegalStateException("The inlet is not a boundary-normal magnitude inlet");
        double outletGauge = pressure-referencePressure;
        set(inlet.getValues().get(VelocityMagnitudeProfile.class).getMethod(ConstantScalarProfileMethod.class), speed);
        set(outlet.getValues().get(StaticPressureProfile.class).getMethod(ConstantScalarProfileMethod.class), outletGauge);
        int farfields = 0;
        for (Boundary boundary : region.getBoundaryManager().getObjects())
            if (boundary.getPresentationName().startsWith("AUTO_FARFIELD_")) farfields++;
        if (farfields != 4) throw new IllegalStateException("Expected four farfield boundaries, got " + farfields);
        double actualSpeed = inlet.getValues().get(VelocityMagnitudeProfile.class)
                .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        double actualGauge = outlet.getValues().get(StaticPressureProfile.class)
                .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        if (!Double.isFinite(actualSpeed) || !Double.isFinite(actualGauge)
                || Math.abs(actualSpeed-speed) > 1e-9 || Math.abs(actualGauge-outletGauge) > 1e-9)
            throw new IllegalStateException("Actual boundary profile values differ from CaseSpec");
        sim.saveState(new File(dir, "external_flow_physics.sim").getAbsolutePath());
        try (FileWriter writer = new FileWriter(new File(dir, "external_flow_physics.json"), StandardCharsets.UTF_8)) {
            writer.write("{\"schema_version\":1,\"status\":\"ok\",\"physics_branch\":\"constant_laminar\""
                    + ",\"region\":\"" + escape(regionName) + "\",\"continuum_models_after_enable\":\"" + escape(models.toString())
                    + "\",\"requested_density_kg_m3\":" + rho + ",\"actual_density_kg_m3\":" + actualRho
                    + ",\"requested_dynamic_viscosity_pa_s\":" + mu + ",\"actual_dynamic_viscosity_pa_s\":" + actualMu
                    + ",\"energy_equation\":false,\"actual_inlet_speed_m_s\":" + actualSpeed
                    + ",\"reference_pressure_pa\":" + referencePressure + ",\"actual_outlet_gauge_pressure_pa\":" + actualGauge
                    + ",\"farfield_boundaries\":" + farfields + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
        }
        System.out.println(COMPLETE);
    }

    private static String stringRequired(String json, String key) {
        String value = optional(json, key, null);
        if (value == null) throw new IllegalArgumentException("Missing string " + key);
        return value;
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
        // Control characters matter: a STAR exception message contains newlines, and an earlier
        // revision of this batch emitted them raw, which made the failure record unparseable JSON -
        // precisely the evidence the record exists to preserve.
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
