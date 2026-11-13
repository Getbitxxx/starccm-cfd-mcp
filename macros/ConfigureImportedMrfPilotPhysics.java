/* Explicit, isolated physics setup for an audited imported two-region MRF pilot. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.report.ElementCountReport;
import star.common.Boundary;
import star.common.ConstantScalarProfileMethod;
import star.common.FvRepresentation;
import star.common.InletBoundary;
import star.common.PressureBoundary;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.SymmetryBoundary;
import star.common.WallBoundary;
import star.energy.StaticTemperatureProfile;
import star.flow.FlowDirectionOption;
import star.flow.InletVelocityOption;
import star.flow.ReferencePressure;
import star.flow.StaticPressureProfile;
import star.flow.VelocityMagnitudeProfile;
import star.motion.MotionSpecification;
import star.motion.ReferenceFrameManager;
import star.motion.ReferenceFrameOption;
import star.motion.UserRotatingReferenceFrame;
import star.turbulence.TurbulenceIntensityProfile;
import star.turbulence.TurbulentViscosityRatioProfile;

public class ConfigureImportedMrfPilotPhysics extends StarMacro {
    private static String field(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }

    private static double number(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        double value = Double.parseDouble(m.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Nonfinite " + key);
        return value;
    }

    private static String sha256(File file) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));
        StringBuilder value = new StringBuilder();
        for (byte item : digest) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    private static Boundary required(Region region, String name) {
        Boundary result = region.getBoundaryManager().getBoundary(name);
        if (result == null) throw new IllegalStateException("Missing boundary " + region.getPresentationName() + "/" + name);
        return result;
    }

    private static void setAndCheck(ConstantScalarProfileMethod method, double value) {
        method.getQuantity().setDefinition(Double.toString(value));
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual) || Math.abs(actual - value) > 1e-8 * Math.max(1.0, Math.abs(value))) {
            throw new IllegalStateException("Profile did not retain its SI value");
        }
    }

    private static double faces(ElementCountReport report, Boundary boundary) {
        report.getParts().setObjects(boundary);
        if (!report.getCountKeys(boundary).contains("faces")) {
            throw new IllegalStateException("Face count unavailable for " + boundary.getPresentationName());
        }
        return report.getCount(boundary, "faces");
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        ElementCountReport countReport = null;
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            if (!"geometry_mesh_probe_only".equals(field(spec, "scenario")) ||
                    !sha256(new File(dir, "input.sim")).equals(field(spec, "expected_input_sha256"))) {
                throw new IllegalStateException("Pilot-only or input hash check failed");
            }
            Region rotor = sim.getRegionManager().getRegion(field(spec, "rotor_region"));
            Region stator = sim.getRegionManager().getRegion(field(spec, "stator_region"));
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (rotor == null || stator == null || mesh == null ||
                    sim.getRegionManager().getObjects().size() != 2 ||
                    sim.getInterfaceManager().getObjects().size() != 3 ||
                    !mesh.describesRegion(rotor) || !mesh.describesRegion(stator) ||
                    rotor.getPhysicsContinuum() == null ||
                    rotor.getPhysicsContinuum() != stator.getPhysicsContinuum()) {
                throw new IllegalStateException("Expected two meshed fluid regions in one continuum");
            }
            Boundary inlet = required(stator, field(spec, "inlet_boundary"));
            Boundary outlet = required(stator, field(spec, "outlet_boundary"));
            Boundary farfield = required(stator, field(spec, "farfield_boundary"));
            if (!(inlet.getBoundaryType() instanceof WallBoundary) ||
                    !(outlet.getBoundaryType() instanceof WallBoundary) ||
                    !(farfield.getBoundaryType() instanceof WallBoundary)) {
                throw new IllegalStateException("Selected outer boundaries are not the audited imported walls");
            }
            double speed = number(spec, "inlet_speed_m_s");
            double temperature = number(spec, "temperature_k");
            double pressure = number(spec, "pressure_pa");
            double intensity = number(spec, "turbulence_intensity");
            double viscosityRatio = number(spec, "turbulent_viscosity_ratio");
            double rpm = number(spec, "rotation_rpm");
            if (speed <= 0 || temperature <= 0 || pressure <= 0 ||
                    intensity <= 0 || intensity >= 1 || viscosityRatio <= 0 || rpm <= 0) {
                throw new IllegalArgumentException("Invalid pilot flow or rotation values");
            }
            double referencePressure = stator.getPhysicsContinuum().getReferenceValues()
                    .get(ReferencePressure.class).getSIValue();
            if (Math.abs(referencePressure - pressure) > 1e-6 * pressure) {
                throw new IllegalStateException("Reference pressure differs from explicit pilot pressure");
            }
            Object selected = sim.get(ReferenceFrameManager.class).getObject(field(spec, "reference_frame_name"));
            if (!(selected instanceof UserRotatingReferenceFrame)) {
                throw new IllegalStateException("Audited rotating frame is absent");
            }
            UserRotatingReferenceFrame frame = (UserRotatingReferenceFrame) selected;
            double[] axis = frame.getAxisVector().getVector().toDoubleArray();
            double[] origin = frame.getOriginVector().getVector().toDoubleArray();
            if (axis.length != 3 || origin.length != 3 || Math.abs(axis[0]) > 1e-9 ||
                    Math.abs(axis[1]) > 1e-9 || Math.abs(axis[2] - 1) > 1e-9 ||
                    Math.abs(origin[0]) > 1e-9 || Math.abs(origin[1]) > 1e-9 ||
                    Math.abs(origin[2]) > 1e-9 ||
                    Math.abs(frame.getRotationRate().getSIValue() - rpm * 2 * Math.PI / 60) >
                            1e-6 * rpm * 2 * Math.PI / 60) {
                throw new IllegalStateException("Rotating frame differs from +Z origin and requested RPM");
            }
            inlet.setBoundaryType(InletBoundary.class);
            outlet.setBoundaryType(PressureBoundary.class);
            farfield.setBoundaryType(SymmetryBoundary.class);
            if (!"MAGNITUDE_DIRECTION".equals(inlet.getConditions().get(InletVelocityOption.class)
                    .getSelectedElement().toString()) ||
                    !"BOUNDARY_NORMAL".equals(inlet.getConditions().get(FlowDirectionOption.class)
                    .getSelectedElement().toString()) ||
                    !"LAB_FRAME".equals(inlet.getConditions().get(ReferenceFrameOption.class)
                    .getSelectedElement().toString()) ||
                    !"LAB_FRAME".equals(outlet.getConditions().get(ReferenceFrameOption.class)
                    .getSelectedElement().toString())) {
                throw new IllegalStateException("Inlet/outlet direction or reference frame differs from audited API defaults");
            }
            setAndCheck(inlet.getValues().get(VelocityMagnitudeProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), speed);
            setAndCheck(inlet.getValues().get(StaticTemperatureProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), temperature);
            setAndCheck(outlet.getValues().get(StaticTemperatureProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), temperature);
            setAndCheck(outlet.getValues().get(StaticPressureProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), pressure - referencePressure);
            setAndCheck(inlet.getValues().get(TurbulenceIntensityProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), intensity);
            setAndCheck(outlet.getValues().get(TurbulenceIntensityProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), intensity);
            setAndCheck(inlet.getValues().get(TurbulentViscosityRatioProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            setAndCheck(outlet.getValues().get(TurbulentViscosityRatioProfile.class)
                    .getMethod(ConstantScalarProfileMethod.class), viscosityRatio);
            rotor.getValues().get(MotionSpecification.class).setReferenceFrame(frame);
            sim.getSolution().initializeSolution();
            countReport = sim.getReportManager().create("star.base.report.ElementCountReport");
            countReport.setRepresentation(mesh);
            String[] statorInterfaces = {field(spec, "stator_top_boundary"),
                    field(spec, "stator_bottom_boundary"), field(spec, "stator_side_boundary")};
            String[] rotorInterfaces = {field(spec, "rotor_top_boundary"),
                    field(spec, "rotor_bottom_boundary"), field(spec, "rotor_side_boundary")};
            for (int i = 0; i < 3; i++) {
                if (faces(countReport, required(stator, statorInterfaces[i])) <= 0 ||
                        faces(countReport, required(rotor, rotorInterfaces[i])) <= 0) {
                    throw new IllegalStateException("MRF interface has no connected faces after initialization");
                }
            }
            if (faces(countReport, inlet) <= 0 || faces(countReport, outlet) <= 0 ||
                    faces(countReport, farfield) <= 0) {
                throw new IllegalStateException("An outer boundary has no faces");
            }
            sim.deleteObjects(Arrays.asList(countReport));
            countReport = null;
            sim.saveState(new File(dir, "imported_mrf_physics_pilot.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "imported_mrf_physics_pilot.txt"), StandardCharsets.UTF_8)) {
                out.write("rotor_region=" + rotor.getPresentationName() + "\n");
                out.write("stator_region=" + stator.getPresentationName() + "\n");
                out.write("inlet_speed_m_s=" + speed + "\n");
                out.write("outlet_gauge_pressure_pa=" + (pressure - referencePressure) + "\n");
                out.write("temperature_k=" + temperature + "\nrotation_rpm=" + rpm + "\n");
                out.write("IMPORTED_MRF_PHYSICS_PILOT_COMPLETE\n");
            }
            System.out.println("IMPORTED_MRF_PHYSICS_PILOT_COMPLETE");
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(dir, "imported_mrf_physics_pilot_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(failure.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Imported MRF physics pilot failed", failure);
        } finally {
            if (countReport != null) sim.deleteObjects(Arrays.asList(countReport));
        }
    }
}
