/* Connect measured MRF surface pairs and assign CaseSpec rotation before meshing. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import star.common.*;
import star.meshing.MeshOperationPart;
import star.motion.MotionSpecification;
import star.motion.ReferenceFrameManager;
import star.motion.UserRotatingReferenceFrame;

public class ConfigureMrfInterfaces extends StarMacro {
    private static final String COMPLETE = "MRF_INTERFACES_CONFIGURED";
    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String geometry = object(spec, "geometry");
            String roles = object(spec, "mrf_regions");
            String rotation = object(spec, "rotation");
            String map = read(new File(dir, "mrf_surface_map.json"));
            if (!"ok".equals(string(map, "status"))) throw new IllegalStateException("Surface map is not an audited success");
            Region stationary = sim.getRegionManager().getRegion(string(roles, "stator_region"));
            Region rotating = sim.getRegionManager().getRegion(string(roles, "rotor_region"));
            MeshOperationPart stationaryPart = part(sim, string(geometry, "stationary_fluid_part"));
            MeshOperationPart rotatingPart = part(sim, string(geometry, "rotating_fluid_part"));
            if (stationary == null || rotating == null || stationary == rotating ||
                    sim.getRegionManager().getObjects().size() != 2 || !sim.getInterfaceManager().getObjects().isEmpty())
                throw new IllegalStateException("Expected two fresh role-selected regions and no interfaces");
            Boundary inlet = owned(stationary, stationaryPart, string(map, "outer_inlet"));
            Boundary outlet = owned(stationary, stationaryPart, string(map, "outer_outlet"));
            Boundary side = owned(stationary, stationaryPart, string(map, "outer_side"));
            if (!(inlet.getBoundaryType() instanceof InletBoundary) ||
                    !(outlet.getBoundaryType() instanceof PressureBoundary) ||
                    !(side.getBoundaryType() instanceof SymmetryBoundary))
                throw new IllegalStateException("Measured outer surfaces have wrong physical boundary roles");
            Set<Boundary> staticPairs = new HashSet<Boundary>();
            Set<Boundary> rotatingPairs = new HashSet<Boundary>();
            String[] keys = {"inner_upstream", "inner_downstream", "inner_side"};
            for (String key : keys) {
                staticPairs.add(owned(stationary, stationaryPart, string(map, key)));
                rotatingPairs.add(owned(rotating, rotatingPart, string(map, key)));
            }
            if (staticPairs.size() != 3 || rotatingPairs.size() != 3) throw new IllegalStateException("MRF pair roles overlap");
            for (String key : keys)
                sim.getInterfaceManager().createBoundaryInterface(
                    owned(stationary, stationaryPart, string(map, key)),
                    owned(rotating, rotatingPart, string(map, key)), "MRF_" + key);
            if (sim.getInterfaceManager().getObjects().size() != 3) throw new IllegalStateException("Expected three interfaces");
            double[] expectedAxis = array(rotation, "axis", 3);
            double[] expectedOrigin = array(rotation, "center_m", 3);
            if (Math.abs(expectedAxis[0]) > 1e-12 || Math.abs(expectedAxis[1]) > 1e-12 || Math.abs(expectedAxis[2]-1.0) > 1e-12)
                throw new IllegalArgumentException("Audited cylindrical operation supports +Z rotation only");
            double rpm = number(rotation, "rpm");
            if (rpm <= 0) throw new IllegalArgumentException("RPM must be positive");
            double expectedRate = rpm * 2.0 * Math.PI / 60.0;
            ReferenceFrameManager frames = sim.get(ReferenceFrameManager.class);
            String frameName = string(spec, "reference_frame_name");
            Object prior = null;
            for (AbstractReferenceFrame candidate : frames.getObjects())
                if (frameName.equals(candidate.getPresentationName())) prior = candidate;
            if (prior != null && !(prior instanceof UserRotatingReferenceFrame)) throw new IllegalStateException("Selected frame role has wrong type");
            UserRotatingReferenceFrame frame = prior == null
                ? (UserRotatingReferenceFrame) frames.createReferenceFrame(UserRotatingReferenceFrame.class, frameName)
                : (UserRotatingReferenceFrame) prior;
            frame.getAxisVector().setComponentsAndUnits(expectedAxis[0], expectedAxis[1], expectedAxis[2], sim.getUnitsManager().getObject(""));
            frame.getOriginVector().setComponentsAndUnits(expectedOrigin[0], expectedOrigin[1], expectedOrigin[2], sim.getUnitsManager().getObject("m"));
            frame.getRotationRate().setValue(expectedRate);
            // ②R19 measured on the first run that ever reached this stage (trial original-r19-20261005a,
            // regions macro 2): a bare geometry region has no MotionSpecification, so assigning the frame
            // here threw "Object not found in TypedObjectManager" at ModelPart.getValues. The motion
            // specification only exists once the physics continuum with the MRF motion model is attached,
            // which is the NEXT stage; ConfigureAir3bMrfPhysics already assigns this same frame and reads
            // the assignment back. This macro therefore keeps creating, configuring and MEASURING the
            // frame (so the CaseSpec rotation vector is still cross-checked) and defers only the
            // assignment to the stage that owns the continuum.
            double[] axis = frame.getAxisVector().getVector().toDoubleArray();
            double[] origin = frame.getOriginVector().getVector().toDoubleArray();
            for (int i = 0; i < 3; i++)
                if (!Double.isFinite(axis[i]) || !Double.isFinite(origin[i]) ||
                        Math.abs(axis[i]-expectedAxis[i]) > 1e-9 || Math.abs(origin[i]-expectedOrigin[i]) > 1e-9)
                    throw new IllegalStateException("Actual frame axis or origin differs from SI CaseSpec");
            double omega = frame.getRotationRate().getSIValue();
            if (!Double.isFinite(omega) || Math.abs(omega-expectedRate) > 1e-9*Math.max(1.0, expectedRate))
                throw new IllegalStateException("Actual angular rate differs from CaseSpec");
            sim.saveState(new File(dir, "mrf_interfaces_configured.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_interfaces_configured.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"ok\",\"stationary_region\":\"" + esc(stationary.getPresentationName()) +
                    "\",\"rotating_region\":\"" + esc(rotating.getPresentationName()) + "\",\"interfaces\":3,\"rotation_rpm\":" + rpm +
                    ",\"rotation_rate_rad_s\":" + omega + ",\"measured_axis\":[" + axis[0] + "," + axis[1] + "," + axis[2] +
                    "],\"measured_origin_m\":[" + origin[0] + "," + origin[1] + "," + origin[2] +
                    "],\"inlet_boundary\":\"" + esc(inlet.getPresentationName()) + "\",\"outlet_boundary\":\"" + esc(outlet.getPresentationName()) +
                    "\",\"farfield_boundary\":\"" + esc(side.getPresentationName()) +
                    "\",\"boundary_values_deferred_to_physics\":true,\"rotation_frame_role\":\"" + esc(frameName) +
                    "\",\"rotation_frame_created_and_measured\":true,\"rotation_specification_assignment_deferred_to\":\"ConfigureAir3bMrfPhysics\"" +
                    ",\"selection_basis\":\"CaseSpec regions and measured PartSurface ownership\",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (PrintWriter diagnostic = new PrintWriter(new FileWriter(new File(dir, "mrf_interface_failure.txt"), StandardCharsets.UTF_8))) { failure.printStackTrace(diagnostic); }
            catch (Throwable ignored) { }
            throw new RuntimeException("MRF interface configuration failed", failure);
        }
    }
    private static MeshOperationPart part(Simulation sim, String name) {
        Object p = sim.get(SimulationPartManager.class).getPart(name);
        if (!(p instanceof MeshOperationPart)) throw new IllegalStateException("CaseSpec fluid role is not a Boolean part");
        return (MeshOperationPart) p;
    }
    private static Boundary owned(Region region, MeshOperationPart part, String surfaceName) {
        PartSurface surface = part.getPartSurfaceManager().getPartSurface(surfaceName);
        if (surface == null) throw new IllegalStateException("Measured part surface missing: " + surfaceName);
        Boundary found = null;
        for (Boundary b : region.getBoundaryManager().getBoundaries()) if (b.getPartSurfaceGroup().getObjects().contains(surface)) {
            if (found != null) throw new IllegalStateException("Surface role has multiple boundary owners");
            found = b;
        }
        if (found == null) throw new IllegalStateException("Measured surface has no region boundary");
        return found;
    }
    private static String read(File f) throws Exception { return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8); }
    private static String object(String json, String key) { Matcher m=Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json); if(!m.find())throw new IllegalArgumentException("Missing object "+key);return m.group(1); }
    private static String string(String json, String key) { Matcher m=Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json); if(!m.find())throw new IllegalArgumentException("Missing string "+key);return m.group(1); }
    private static double number(String json,String key) { Matcher m=Pattern.compile("\\\""+key+"\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);if(!m.find())throw new IllegalArgumentException("Missing number "+key);double v=Double.parseDouble(m.group(1));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+key);return v; }
    private static double[] array(String json,String key,int size) { Matcher m=Pattern.compile("\\\""+key+"\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(json);if(!m.find())throw new IllegalArgumentException("Missing vector "+key);String[] values=m.group(1).split(",");if(values.length!=size)throw new IllegalArgumentException("Wrong vector size "+key);double[] a=new double[size];for(int i=0;i<size;i++){a[i]=Double.parseDouble(values[i].trim());if(!Double.isFinite(a[i]))throw new IllegalArgumentException("Nonfinite vector "+key);}return a; }
    private static String esc(String s) { return s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t"); }
}

