/* Split the audited joint MRF mesh operation into independent stationary and rotating operations. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.NeoObjectVector;
import star.base.neo.StringVector;
import star.common.PartSurface;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.AutoMeshOperation;
import star.meshing.BaseSize;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;
import star.meshing.PartsMinimumSurfaceSize;
import star.meshing.PartsMinimumSurfaceSizeOption;
import star.meshing.PartsTargetSurfaceSize;
import star.meshing.PartsTargetSurfaceSizeOption;
import star.meshing.SurfaceCustomMeshControl;
import star.prismmesher.NumPrismLayers;

public class SplitMrfMeshOperations extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String mesh = object(spec, "mesh");
            String map = object(spec, "surface_map");
            double base = number(mesh, "base_size_m");
            double interfaceMin = number(mesh, "interface_min_size_m");
            double interfaceTarget = number(mesh, "interface_target_size_m");
            int prismLayers = (int) number(mesh, "prism_layers");
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            AutoMeshOperation rotatingOperation = (AutoMeshOperation) manager.getObject("AUTO_MRF_DIAGNOSTIC_MESH");
            MeshOperationPart stationary = (MeshOperationPart) sim.get(SimulationPartManager.class).getPart("AUTO_MRF_STATIONARY_FLUID");
            MeshOperationPart rotating = (MeshOperationPart) sim.get(SimulationPartManager.class).getPart("AUTO_MRF_ROTATING_FLUID");
            if (rotatingOperation == null || stationary == null || rotating == null ||
                    manager.getObjects().stream().filter(op -> op instanceof AutoMeshOperation).count() != 1 ||
                    sim.getInterfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Audited joint MRF mesh prerequisites missing");
            }
            rotatingOperation.setPresentationName("AUTO_MRF_ROTATING_MESH");
            rotatingOperation.getInputGeometryObjects().setObjects(rotating);
            SurfaceCustomMeshControl rotatingInterface = (SurfaceCustomMeshControl) rotatingOperation
                    .getCustomMeshControls().getObject("AUTO_MRF_INTERFACE_SURFACES");
            if (rotatingInterface == null || rotatingOperation.getCustomMeshControls().getObject("AUTO_MRF_BLADE_SURFACES") == null) {
                throw new IllegalStateException("Audited MRF mesh controls missing");
            }
            rotatingInterface.getGeometryObjects().setObjects(
                    surface(rotating, string(map, "inner_upstream")),
                    surface(rotating, string(map, "inner_downstream")),
                    surface(rotating, string(map, "inner_side")));
            AutoMeshOperation stationaryOperation = manager.createAutoMeshOperation(
                    new StringVector(new String[] {"star.resurfacer.ResurfacerAutoMesher",
                            "star.resurfacer.AutomaticSurfaceRepairAutoMesher",
                            "star.trimmer.TrimmerAutoMesher", "star.prismmesher.PrismAutoMesher"}),
                    new NeoObjectVector(new Object[] {stationary}));
            stationaryOperation.setPresentationName("AUTO_MRF_STATIONARY_MESH");
            Units meters = sim.getUnitsManager().getObject("m");
            stationaryOperation.getDefaultValues().get(BaseSize.class).setValueAndUnits(base, meters);
            stationaryOperation.getDefaultValues().get(NumPrismLayers.class).setNumLayers(prismLayers);
            SurfaceCustomMeshControl stationaryInterface = stationaryOperation.getCustomMeshControls().createSurfaceControl();
            stationaryInterface.setPresentationName("AUTO_MRF_STATIONARY_INTERFACE_SURFACES");
            stationaryInterface.getGeometryObjects().setObjects(
                    surface(stationary, string(map, "inner_upstream")),
                    surface(stationary, string(map, "inner_downstream")),
                    surface(stationary, string(map, "inner_side")));
            stationaryInterface.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class)
                    .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
            stationaryInterface.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            stationaryInterface.getCustomValues().get(PartsMinimumSurfaceSize.class)
                    .getRelativeSizeScalar().setValue(100.0*interfaceMin/base);
            stationaryInterface.getCustomValues().get(PartsTargetSurfaceSize.class)
                    .getRelativeSizeScalar().setValue(100.0*interfaceTarget/base);
            sim.saveState(new File(dir, "mrf_mesh_split.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_mesh_split.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"operations\":2," +
                        "\"stationary_part\":\"AUTO_MRF_STATIONARY_FLUID\"," +
                        "\"rotating_part\":\"AUTO_MRF_ROTATING_FLUID\"," +
                        "\"base_size_m\":" + base + ",\"prism_layers\":" + prismLayers +
                        ",\"mesh_generated\":false,\"complete_marker\":\"MRF_MESH_OPERATIONS_SPLIT\"}\n");
            }
            System.out.println("MRF_MESH_OPERATIONS_SPLIT");
        } catch (Throwable failure) {
            throw new RuntimeException("Splitting MRF mesh operations failed", failure);
        }
    }

    private static PartSurface surface(MeshOperationPart part, String name) {
        PartSurface found = part.getPartSurfaceManager().getPartSurface(name);
        if (found == null) throw new IllegalStateException("MRF part surface missing: " + name);
        return found;
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
}
