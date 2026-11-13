/* Geometry-only MRF split: stationary outer volume and rotor volume minus solids. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.DoubleVector;
import star.base.neo.NeoObjectVector;
import star.common.GeometryPart;
import star.common.LabCoordinateSystem;
import star.common.PartSurface;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;
import star.meshing.MeshPart;
import star.meshing.MeshOperation;
import star.meshing.AutoMeshOperation;
import star.meshing.MeshPartFactory;
import star.meshing.SimpleCylinderPart;
import star.meshing.SubtractPartsOperation;

public class BuildMrfFluidVolumes extends StarMacro {
    private static final String COMPLETE = "MRF_FLUID_VOLUMES_BUILT";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String unitName = string(spec, "model_length_unit");
            double scale = unitScale(unitName);
            String zone = object(spec, "rotation_zone");
            double radiusM = number(zone, "radius_m");
            double zMinM = number(zone, "z_min_m");
            double zMaxM = number(zone, "z_max_m");
            double[] centerM = array(zone, "center_xy_m", 2);
            if (!(radiusM > 0 && zMinM < zMaxM)) throw new IllegalArgumentException("Invalid MRF envelope dimensions");
            SimulationPartManager parts = sim.get(SimulationPartManager.class);
            String stationaryName=string(spec,"stationary_fluid_part");
            String rotatingName=string(spec,"rotating_fluid_part");
            String outerName=string(spec,"outer_cylinder_part");
            String envelopeName=string(spec,"rotation_envelope_part");
            List<String> actualPartNames=new ArrayList<>();
            for(GeometryPart actual:parts.getObjects())actualPartNames.add(actual.getPresentationName());
            for(String selected:new String[]{stationaryName,rotatingName,outerName,envelopeName})
                if(outputExists(actualPartNames,selected))throw new IllegalStateException("Selected MRF output already exists: "+selected);
            if(!sim.getRegionManager().getRegions().isEmpty() || !sim.getInterfaceManager().getObjects().isEmpty() || sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration()!=0)
                throw new IllegalStateException("MRF construction requires the explicitly cleaned geometry-only copy");
            String recoveryName=string(spec,"recovered_rotor_part");
            List<String> names = strings(object(spec, "geometry"), "rotor_parts");
            if(names.size()!=1 || !names.get(0).equals(recoveryName))throw new IllegalArgumentException("Rotor tools must be the explicitly recovered solid role");
            List<GeometryPart> solids=new ArrayList<>();
            for(String name:names){
                GeometryPart solid=parts.getPart(name);
                if(!(solid instanceof MeshPart) || !((MeshPart)solid).isClosed() || !((MeshPart)solid).isManifold())
                    throw new IllegalStateException("Selected recovered material is not closed and manifold: "+name);
                solids.add(solid);
            }
            String recovery=Files.readString(new File(dir,"recovered_rotor_solids.json").toPath(),StandardCharsets.UTF_8);
            if(!"ok".equals(string(recovery,"status")) || !"rotor_solid".equals(string(recovery,"output_role")) ||
                !recoveryName.equals(string(recovery,"output_part")) || !(number(recovery,"readback_surface_volume_m3")>0))
                throw new IllegalStateException("Missing positive material-volume recovery evidence for the selected solid");
            String domain = object(spec, "domain");
            double outerRadiusM = number(domain, "cylinder_radius_m");
            double outerZMinM = number(domain, "cylinder_z_min_m");
            double outerZMaxM = number(domain, "cylinder_z_max_m");
            double[] outerCenterM = array(domain, "cylinder_center_xy_m", 2);
            SimpleCylinderPart outer = createCylinder(sim, parts, outerName, unitName,
                    scale, outerRadiusM, outerZMinM, outerZMaxM, outerCenterM);
            SimpleCylinderPart envelope = createCylinder(sim, parts, envelopeName, unitName,
                    scale, radiusM, zMinM, zMaxM, centerM);
            MeshOperationPart stationary = subtract(sim, outer, new GeometryPart[] {envelope},
                    stationaryName, "Stationary fluid Boolean");
            MeshOperationPart rotating = subtract(sim, envelope, solids.toArray(new GeometryPart[0]),
                    rotatingName, "Rotating fluid Boolean");
            if(!stationary.isClosed() || !stationary.isManifold() || !rotating.isClosed() || !rotating.isManifold())
                throw new IllegalStateException("Constructed MRF fluid topology is not closed and manifold");
            if (stationary.getPartSurfaceManager().getObjects().size() < 6 ||
                    rotating.getPartSurfaceManager().getObjects().size() < 4) {
                throw new IllegalStateException("MRF envelope and material surfaces were not retained as separate groups");
            }
            sim.saveState(new File(dir, "mrf_fluid_volumes_built.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_fluid_volumes_build.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"rotation_zone_radius_m\":" + radiusM +
                        ",\"rotation_zone_z_min_m\":" + zMinM + ",\"rotation_zone_z_max_m\":" + zMaxM +
                        ",\"rotation_zone_center_xy_m\":[" + centerM[0] + "," + centerM[1] + "]" +
                        ",\"stationary_part\":\"" + escape(stationaryName) + "\",\"rotating_part\":\"" + escape(rotatingName) + "\"" +
                        ",\"solid_tool_part\":\"" + escape(recoveryName) + "\",\"solid_tool_role\":\"rotor_solid\"" +
                        ",\"stationary_closed\":true,\"stationary_manifold\":true,\"rotating_closed\":true,\"rotating_manifold\":true" +
                        ",\"stationary_surfaces\":" + stationary.getPartSurfaceManager().getObjects().size() +
                        ",\"rotating_surfaces\":" + rotating.getPartSurfaceManager().getObjects().size() +
                        ",\"regions\":0,\"iteration\":0,\"initialized\":false" +
                        ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try { Files.writeString(new File(dir,"mrf_fluid_volumes_build.json").toPath(),
                "{\"status\":\"error\",\"error\":\""+escape(failure.toString())+"\"}\n",StandardCharsets.UTF_8);
                sim.saveState(new File(dir,"mrf_fluid_volumes_failed.sim").getAbsolutePath());
            } catch(Throwable ignored) { }
            throw new RuntimeException("MRF fluid-volume construction failed", failure);
        }
    }
    static boolean outputExists(Collection<String> actualPartNames,String selectedName){
        return actualPartNames.contains(selectedName);
    }

    private static SimpleCylinderPart createCylinder(Simulation sim, SimulationPartManager parts, String name,
            String unitName, double scale, double radiusM, double zMinM, double zMaxM, double[] centerM) {
            SimpleCylinderPart envelope = sim.get(MeshPartFactory.class).createNewCylinderPart(parts);
            envelope.setPresentationName(name);
            envelope.setDoNotRetessellate(true);
            LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
            Units units = sim.getUnitsManager().getObject(unitName);
            envelope.setCoordinateSystem(lab);
            envelope.getStartCoordinate().setCoordinateSystem(lab);
            envelope.getStartCoordinate().setCoordinate(units, units, units,
                    new DoubleVector(new double[] {centerM[0]/scale, centerM[1]/scale, zMinM/scale}));
            envelope.getEndCoordinate().setCoordinateSystem(lab);
            envelope.getEndCoordinate().setCoordinate(units, units, units,
                    new DoubleVector(new double[] {centerM[0]/scale, centerM[1]/scale, zMaxM/scale}));
            envelope.getRadius().setValueAndUnits(radiusM/scale, units);
            envelope.rebuildSimpleShapePart();
            envelope.setDoNotRetessellate(false);
            PartSurface unsplit = envelope.getPartSurfaceManager().getPartSurface("Cylinder Surface");
            if (unsplit == null) throw new IllegalStateException("MRF envelope surface missing");
            envelope.getPartSurfaceManager().splitPartSurfacesByAngle(
                    new NeoObjectVector(new Object[] {unsplit}), 89.0);
            if (envelope.getPartSurfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Cylinder caps and side were not separated: " + name);
            }
            return envelope;
    }

    private static MeshOperationPart subtract(Simulation sim, GeometryPart target, GeometryPart[] tools,
            String outputName, String operationName) {
        SimulationPartManager parts = sim.get(SimulationPartManager.class);
        Set<GeometryPart> before = new HashSet<GeometryPart>(parts.getObjects());
        Object[] inputs = new Object[tools.length + 1];
        inputs[0] = target;
        System.arraycopy(tools, 0, inputs, 1, tools.length);
        SubtractPartsOperation operation = (SubtractPartsOperation) sim.get(MeshOperationManager.class)
                .createSubtractPartsOperation(new NeoObjectVector(inputs));
        operation.setTargetPart((MeshPart) target);
        operation.setPerformCADBoolean(true);
        operation.execute();
        MeshOperationPart result = null;
        Collection<GeometryPart> after = parts.getObjects();
        for (GeometryPart part : after) {
            if (part instanceof MeshOperationPart && !before.contains(part)) {
                if (result != null) throw new IllegalStateException("Boolean produced multiple outputs");
                result = (MeshOperationPart) part;
            }
        }
        if (result == null) throw new IllegalStateException("Boolean produced no output");
        result.setPresentationName(outputName);
        operation.setPresentationName(operationName);
        return result;
    }

    private static String escape(String s) {
        StringBuilder out=new StringBuilder();
        for(char c:s.toCharArray()){
            if(c=='\\')out.append("\\\\");
            else if(c=='"')out.append("\\\"");
            else if(c<0x20)out.append(String.format("\\u%04x",(int)c));
            else out.append(c);
        }
        return out.toString();
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
        if (values.length != size) throw new IllegalArgumentException("Wrong vector size " + key);
        double[] result = new double[size];
        for (int i = 0; i < size; i++) result[i] = Double.parseDouble(values[i].trim());
        return result;
    }

    private static List<String> strings(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing strings " + key);
        List<String> result = new ArrayList<String>();
        Matcher item = Pattern.compile("\\\"([^\\\"]+)\\\"").matcher(m.group(1));
        while (item.find()) result.add(item.group(1));
        return result;
    }

    private static double unitScale(String unit) {
        if ("m".equals(unit)) return 1.0;
        if ("mm".equals(unit)) return 0.001;
        if ("cm".equals(unit)) return 0.01;
        if ("in".equals(unit)) return 0.0254;
        throw new IllegalArgumentException("Unsupported length unit " + unit);
    }
}
