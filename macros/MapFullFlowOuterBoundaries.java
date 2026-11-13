/* Map the three audited cylinder surfaces to open-air boundary roles.
 * This stage does not set inlet values, assign a continuum, mesh, or solve.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.Boundary;
import star.common.InletBoundary;
import star.common.PartSurface;
import star.common.PressureBoundary;
import star.common.Region;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.SymmetryBoundary;
import star.meshing.MeshOperationPart;

public class MapFullFlowOuterBoundaries extends StarMacro {
    private static final String COMPLETE = "FULL_FLOW_OUTER_BOUNDARIES_MAPPED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            double radius = number(spec, "cylinder_radius_m");
            double zMin = number(spec, "cylinder_z_min_m");
            double zMax = number(spec, "cylinder_z_max_m");
            double[] center = array(spec, "cylinder_center_xy_m", 2);
            MeshOperationPart part = (MeshOperationPart) sim.get(SimulationPartManager.class).getPart("AUTO_FREE_AIR_DOMAIN");
            Region region = sim.getRegionManager().getRegion("AUTO_FULL360_FLOW");
            if (part == null || region == null) throw new IllegalStateException("Audited full-flow geometry or region missing");
            int inletPatch = part.getPatchIdBasedOnLabXYZ(center[0], center[1], zMax);
            int outletPatch = part.getPatchIdBasedOnLabXYZ(center[0], center[1], zMin);
            int sidePatch = part.getPatchIdBasedOnLabXYZ(center[0] + radius, center[1], 0.5 * (zMin + zMax));
            if (inletPatch < 0 || outletPatch < 0 || sidePatch < 0 || inletPatch == outletPatch ||
                    inletPatch == sidePatch || outletPatch == sidePatch) {
                throw new IllegalStateException("Cylinder sample points are ambiguous");
            }
            String inletName = nameForPatch(part, inletPatch);
            String outletName = nameForPatch(part, outletPatch);
            String sideName = nameForPatch(part, sidePatch);
            if (inletName.equals(outletName) || inletName.equals(sideName) || outletName.equals(sideName)) {
                throw new IllegalStateException("Cylinder roles do not map to distinct surfaces");
            }
            Boundary inlet = requiredBoundary(region, inletName);
            Boundary outlet = requiredBoundary(region, outletName);
            Boundary side = requiredBoundary(region, sideName);
            inlet.setPresentationName("AUTO_INLET");
            inlet.setBoundaryType(InletBoundary.class);
            outlet.setPresentationName("AUTO_OUTLET");
            outlet.setBoundaryType(PressureBoundary.class);
            side.setPresentationName("AUTO_RADIAL_FARFIELD");
            side.setBoundaryType(SymmetryBoundary.class);
            sim.saveState(new File(dir, "full_flow_boundaries_mapped.sim").getAbsolutePath());
            try (FileWriter writer = new FileWriter(new File(dir, "full_flow_boundaries_map.json"), StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"AUTO_FULL360_FLOW\",\"inlet\":{\"old_name\":\"" +
                        escape(inletName) + "\",\"new_name\":\"AUTO_INLET\",\"patch_id\":" + inletPatch +
                        "},\"outlet\":{\"old_name\":\"" + escape(outletName) + "\",\"new_name\":\"AUTO_OUTLET\",\"patch_id\":" +
                        outletPatch + "},\"side\":{\"old_name\":\"" + escape(sideName) +
                        "\",\"new_name\":\"AUTO_RADIAL_FARFIELD\",\"patch_id\":" + sidePatch +
                        "},\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("Outer-boundary mapping failed", failure);
        }
    }

    private static String nameForPatch(MeshOperationPart part, int patch) {
        String found = null;
        for (PartSurface surface : part.getPartSurfaceManager().getObjects()) {
            if (!surface.getPresentationName().startsWith("AUTO_FREE_AIR_CYLINDER.")) continue;
            List<Integer> patches = part.getPartSurfacePatches(surface);
            if (patches.contains(patch)) {
                if (found != null) throw new IllegalStateException("Patch belongs to multiple outer surfaces");
                found = surface.getPresentationName();
            }
        }
        if (found == null) throw new IllegalStateException("Patch has no outer surface");
        return found;
    }

    private static Boundary requiredBoundary(Region region, String name) {
        Boundary boundary = region.getBoundaryManager().getBoundary(name);
        if (boundary == null) throw new IllegalStateException("Boundary missing for " + name);
        return boundary;
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
