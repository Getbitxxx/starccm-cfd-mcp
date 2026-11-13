import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.PartSurface;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.MeshOperationPart;

/** Read-only patch mapping of cylinder cap centers and a mid-height side point. */
public class AuditCylinderPatchMap extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String spec = new String(Files.readAllBytes(new File(sim.getSessionDirFile(), "case_spec.json").toPath()), StandardCharsets.UTF_8);
            double radius = number(spec, "cylinder_radius_m");
            double zMin = number(spec, "cylinder_z_min_m");
            double zMax = number(spec, "cylinder_z_max_m");
            double[] center = array(spec, "cylinder_center_xy_m", 2);
            MeshOperationPart part = (MeshOperationPart) sim.get(SimulationPartManager.class).getPart("AUTO_FREE_AIR_DOMAIN");
            if (part == null) throw new IllegalStateException("Boolean domain part missing");
            int downstream = part.getPatchIdBasedOnLabXYZ(center[0], center[1], zMin);
            int upstream = part.getPatchIdBasedOnLabXYZ(center[0], center[1], zMax);
            int side = part.getPatchIdBasedOnLabXYZ(center[0] + radius, center[1], 0.5 * (zMin + zMax));
            if (downstream < 0 || upstream < 0 || side < 0 || downstream == upstream ||
                    downstream == side || upstream == side) {
                throw new IllegalStateException("Cylinder sample points did not identify three distinct patches");
            }
            StringBuilder out = new StringBuilder("{\"schema_version\":1,\"status\":\"ok\",\"sample_patch_ids\":{");
            out.append("\"downstream\":").append(downstream).append(",\"upstream\":").append(upstream)
                    .append(",\"side\":").append(side).append("},\"outer_surfaces\":[");
            int count = 0;
            for (PartSurface surface : part.getPartSurfaceManager().getObjects()) {
                if (!surface.getPresentationName().startsWith("AUTO_FREE_AIR_CYLINDER.")) continue;
                List<Integer> patches = part.getPartSurfacePatches(surface);
                if (count++ > 0) out.append(',');
                out.append("{\"name\":\"").append(escape(surface.getPresentationName())).append("\",\"patch_ids\":[");
                for (int i = 0; i < patches.size(); i++) {
                    if (i > 0) out.append(',');
                    out.append(patches.get(i));
                }
                out.append("]}");
            }
            if (count != 3) throw new IllegalStateException("Expected three outer part surfaces");
            out.append("],\"complete_marker\":\"CYLINDER_PATCH_MAP_COMPLETE\"}\n");
            try (FileWriter writer = new FileWriter(new File(sim.getSessionDirFile(), "cylinder_patch_map.json"), StandardCharsets.UTF_8)) {
                writer.write(out.toString());
            }
            System.out.println("CYLINDER_PATCH_MAP_COMPLETE");
        } catch (Throwable failure) {
            throw new RuntimeException("Cylinder patch map failed", failure);
        }
    }

    private static double number(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return Double.parseDouble(m.group(1));
    }

    private static double[] array(String json, String key, int n) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        String[] values = m.group(1).split(",");
        if (values.length != n) throw new IllegalArgumentException("Invalid " + key);
        double[] out = new double[n];
        for (int i = 0; i < n; i++) out[i] = Double.parseDouble(values[i].trim());
        return out;
    }

    private static String escape(String text) { return text.replace("\\", "\\\\").replace("\"", "\\\""); }
}
