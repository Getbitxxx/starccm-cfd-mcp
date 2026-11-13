/* Configure SI flow values on explicitly selected boundaries of a Z-cylinder. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.Boundary;
import star.common.ConstantScalarProfileMethod;
import star.common.InletBoundary;
import star.common.PressureBoundary;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.energy.StaticTemperatureProfile;
import star.flow.FlowDirectionOption;
import star.flow.InletVelocityOption;
import star.flow.ReferencePressure;
import star.flow.StaticPressureProfile;
import star.flow.VelocityMagnitudeProfile;
import star.motion.ReferenceFrameOption;
import star.turbulence.TurbulenceIntensityProfile;
import star.turbulence.TurbulentViscosityRatioProfile;

public class ConfigureNormalInletOutlet extends StarMacro {
    private static final String COMPLETE = "NORMAL_INLET_OUTLET_CONFIGURED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String flow = object(spec, "flow");
            String roles = object(spec, "boundary_roles");
            if (!"cylinder".equals(string(object(spec, "domain"), "shape"))) {
                throw new IllegalArgumentException("This stage requires a Z-axis cylindrical domain");
            }
            double[] velocity = array(flow, "velocity_m_s", 3);
            double speed = Math.sqrt(velocity[0] * velocity[0] + velocity[1] * velocity[1] + velocity[2] * velocity[2]);
            if (!Double.isFinite(speed) || speed <= 0 || Math.abs(velocity[0]) > 1e-9 * speed ||
                    Math.abs(velocity[1]) > 1e-9 * speed || velocity[2] >= 0) {
                throw new IllegalArgumentException("Only inward -Z normal flow on the +Z cap is supported");
            }
            double pressure = number(flow, "pressure_pa");
            double temperature = number(flow, "temperature_k");
            double intensity = number(flow, "turbulence_intensity");
            double viscosityRatio = number(flow, "turbulent_viscosity_ratio");
            if (pressure <= 0 || temperature <= 0 || intensity <= 0 || intensity >= 1 || viscosityRatio <= 0) {
                throw new IllegalArgumentException("Flow values are outside supported physical ranges");
            }
            Region region = sim.getRegionManager().getRegion(string(roles, "region"));
            if (region == null || region.getPhysicsContinuum() == null) throw new IllegalStateException("Flow region or continuum missing");
            Boundary inlet = region.getBoundaryManager().getBoundary(string(roles, "inlet"));
            Boundary outlet = region.getBoundaryManager().getBoundary(string(roles, "outlet"));
            if (inlet == null || outlet == null || !(inlet.getBoundaryType() instanceof InletBoundary) ||
                    !(outlet.getBoundaryType() instanceof PressureBoundary)) {
                throw new IllegalStateException("Selected inlet/outlet types do not match the audited model");
            }
            if (!"MAGNITUDE_DIRECTION".equals(inlet.getConditions().get(InletVelocityOption.class).getSelectedElement().toString()) ||
                    !"BOUNDARY_NORMAL".equals(inlet.getConditions().get(FlowDirectionOption.class).getSelectedElement().toString()) ||
                    !"LAB_FRAME".equals(inlet.getConditions().get(ReferenceFrameOption.class).getSelectedElement().toString()) ||
                    !"LAB_FRAME".equals(outlet.getConditions().get(ReferenceFrameOption.class).getSelectedElement().toString())) {
                throw new IllegalStateException("Boundary velocity direction or reference frame differs from the audited setup");
            }
            double referencePressure = region.getPhysicsContinuum().getReferenceValues().get(ReferencePressure.class)
                    .getSIValue();
            double outletGauge = pressure - referencePressure;
            setAndCheck(inlet.getValues().get(VelocityMagnitudeProfile.class).getMethod(ConstantScalarProfileMethod.class), speed);
            setAndCheck(inlet.getValues().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class), temperature);
            setAndCheck(outlet.getValues().get(StaticTemperatureProfile.class).getMethod(ConstantScalarProfileMethod.class), temperature);
            setAndCheck(outlet.getValues().get(StaticPressureProfile.class).getMethod(ConstantScalarProfileMethod.class), outletGauge);
            setAndCheck(inlet.getValues().get(TurbulenceIntensityProfile.class).getMethod(ConstantScalarProfileMethod.class), intensity);
            setAndCheck(outlet.getValues().get(TurbulenceIntensityProfile.class).getMethod(ConstantScalarProfileMethod.class), intensity);
            setAndCheck(inlet.getValues().get(TurbulentViscosityRatioProfile.class).getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            setAndCheck(outlet.getValues().get(TurbulentViscosityRatioProfile.class).getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            sim.saveState(new File(dir, "flow_boundaries_configured.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "flow_boundaries_configured.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"" +
                        escape(region.getPresentationName()) + "\",\"inlet\":\"" + escape(inlet.getPresentationName()) +
                        "\",\"outlet\":\"" + escape(outlet.getPresentationName()) + "\",\"speed_m_s\":" + speed +
                        ",\"temperature_k\":" + temperature + ",\"reference_pressure_pa\":" + referencePressure +
                        ",\"outlet_gauge_pressure_pa\":" + outletGauge + ",\"turbulence_intensity\":" + intensity +
                        ",\"turbulent_viscosity_ratio\":" + viscosityRatio + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("Normal inlet/outlet configuration failed", failure);
        }
    }

    private static void setAndCheck(ConstantScalarProfileMethod method, double value) {
        method.getQuantity().setDefinition(Double.toString(value));
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual) || Math.abs(actual - value) > 1e-8 * Math.max(1.0, Math.abs(value))) {
            throw new IllegalStateException("Profile value or units do not match SI CaseSpec: " + actual + " != " + value);
        }
    }

    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
        return m.group(1);
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }

    private static double number(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(m.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Nonfinite " + key);
        return value;
    }

    private static double[] array(String json, String key, int size) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing vector " + key);
        String[] values = m.group(1).split(",");
        if (values.length != size) throw new IllegalArgumentException("Wrong vector size for " + key);
        double[] result = new double[size];
        for (int i = 0; i < size; i++) result[i] = Double.parseDouble(values[i].trim());
        return result;
    }

    private static String escape(String text) { return text.replace("\\", "\\\\").replace("\"", "\\\""); }
}
