/* On a copy, reduce only rotating-region prism layers after STAR reports low-quality cells. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.Simulation;
import star.common.StarMacro;
import star.meshing.AutoMeshOperation;
import star.meshing.MeshOperationManager;
import star.prismmesher.NumPrismLayers;

public class AdjustMrfRotatingPrismLayers extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String mesh = object(spec, "mesh");
            int expectedOriginal = (int) number(mesh, "prism_layers");
            int requested = (int) number(mesh, "rotating_prism_layers");
            AutoMeshOperation rotating = (AutoMeshOperation) sim.get(MeshOperationManager.class)
                    .getObject("AUTO_MRF_ROTATING_MESH");
            AutoMeshOperation stationary = (AutoMeshOperation) sim.get(MeshOperationManager.class)
                    .getObject("AUTO_MRF_STATIONARY_MESH");
            if (rotating == null || stationary == null || requested < 3 || requested >= expectedOriginal ||
                    rotating.getDefaultValues().get(NumPrismLayers.class).getNumLayers() != expectedOriginal ||
                    stationary.getDefaultValues().get(NumPrismLayers.class).getNumLayers() != expectedOriginal) {
                throw new IllegalStateException("Audited prism-layer state differs from expected source");
            }
            rotating.getDefaultValues().get(NumPrismLayers.class).setNumLayers(requested);
            if (rotating.getDefaultValues().get(NumPrismLayers.class).getNumLayers() != requested) {
                throw new IllegalStateException("Rotating prism-layer setting did not persist");
            }
            sim.saveState(new File(dir, "mrf_mesh_prism_adjusted.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_mesh_prism_adjusted.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"stationary_prism_layers\":" +
                        expectedOriginal + ",\"rotating_prism_layers\":" + requested +
                        ",\"mesh_generated\":false,\"complete_marker\":\"MRF_ROTATING_PRISM_LAYERS_ADJUSTED\"}\n");
            }
            System.out.println("MRF_ROTATING_PRISM_LAYERS_ADJUSTED");
        } catch (Throwable failure) {
            throw new RuntimeException("MRF rotating prism-layer adjustment failed", failure);
        }
    }

    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
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
