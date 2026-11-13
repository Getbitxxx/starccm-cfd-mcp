/* Batched step 1 of 3: create the fluid Region and classify every boundary role.
 *
 * Derived from the two already-executed operations of this repository: CreateFullFlowRegion (region
 * creation, one boundary per part surface) and MapFullFlowOuterBoundaries (role mapping by probing
 * patch ids at spatial sample points). The difference is that the roles and the sample points come
 * from the CaseSpec box bounds instead of a cylinder, so no propeller or cylinder constant is
 * assumed. Nothing is meshed, no continuum is created, nothing is initialized.
 */
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
import star.common.Boundary;
import star.common.InletBoundary;
import star.common.PartSurface;
import star.common.PressureBoundary;
import star.common.Region;
import star.common.RegionManager;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.SymmetryBoundary;
import star.common.WallBoundary;
import star.meshing.MeshOperationPart;

public class BuildExternalFlowRegion extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_REGION_BUILT";
    private static final String DOMAIN_PART = "AUTO_FREE_AIR_DOMAIN";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            double[] bounds = array(spec, "outer_bounds_m", 6);
            String bodyName = optional(spec, "body_part_name", "BODY");
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            for (int axis = 0; axis < 3; axis++) {
                if (!(bounds[axis] < bounds[axis + 3])) {
                    throw new IllegalArgumentException("outer_bounds_m has no extent on axis " + axis);
                }
            }
            RegionManager regions = sim.getRegionManager();
            for (Region region : regions.getObjects()) {
                if (regionName.equals(region.getPresentationName())) {
                    throw new IllegalStateException("Generated region already exists: " + regionName);
                }
            }
            Object partObject = sim.get(SimulationPartManager.class).getPart(DOMAIN_PART);
            if (!(partObject instanceof MeshOperationPart)) {
                throw new IllegalStateException("Audited Boolean domain part missing: " + DOMAIN_PART);
            }
            MeshOperationPart part = (MeshOperationPart) partObject;

            // The Boolean leaves the box outer boundary as one surface, so split it at its edges
            // before the roles can be assigned to faces.
            List<PartSurface> before = new ArrayList<PartSurface>(part.getPartSurfaceManager().getObjects());
            part.getPartSurfaceManager().splitPartSurfacesByAngle(new NeoObjectVector(before.toArray()), 89.0);
            List<PartSurface> surfaces = new ArrayList<PartSurface>(part.getPartSurfaceManager().getObjects());

            Set<String> existing = new HashSet<String>();
            for (Region region : regions.getObjects()) existing.add(region.getPresentationName());
            regions.newRegionsFromParts(new NeoObjectVector(new Object[] {part}), "OneRegionPerPart", null,
                    "OneBoundaryPerPartSurface", null, RegionManager.CreateInterfaceMode.BOUNDARY);
            Region created = null;
            for (Region region : regions.getObjects()) {
                if (!existing.contains(region.getPresentationName())) {
                    if (created != null) throw new IllegalStateException("More than one region created");
                    created = region;
                }
            }
            if (created == null) throw new IllegalStateException("No region was created");
            created.setPresentationName(regionName);

            // Sample points sit just inside each face of the box, derived from the CaseSpec bounds.
            double[] mid = new double[] {
                (bounds[0] + bounds[3]) / 2.0, (bounds[1] + bounds[4]) / 2.0, (bounds[2] + bounds[5]) / 2.0};
            double inset = 1e-6;
            double[][] samples = new double[][] {
                {bounds[0] + inset, mid[1], mid[2]},
                {bounds[3] - inset, mid[1], mid[2]},
                {mid[0], bounds[1] + inset, mid[2]},
                {mid[0], bounds[4] - inset, mid[2]},
                {mid[0], mid[1], bounds[2] + inset},
                {mid[0], mid[1], bounds[5] - inset}};
            String[] faceRoles = new String[] {"inlet", "outlet", "farfield_y_min", "farfield_y_max",
                    "farfield_z_min", "farfield_z_max"};
            List<String> assigned = new ArrayList<String>();
            List<String> surfaceNames = new ArrayList<String>();
            Set<String> renamed = new HashSet<String>();
            StringBuilder probe = new StringBuilder();
            probe.append("{\"schema_version\":1,\"status\":\"ok\",\"roles\":[");
            try {
            for (int index = 0; index < samples.length; index++) {
                int patch = part.getPatchIdBasedOnLabXYZ(samples[index][0], samples[index][1], samples[index][2]);
                if (patch < 0) throw new IllegalStateException("No patch at sample point for " + faceRoles[index]);
                String surface = nameForPatch(part, patch);
                if (surface == null) throw new IllegalStateException("Patch " + patch + " matches no part surface");
                if (surfaceNames.contains(surface)) {
                    throw new IllegalStateException("Two roles resolve to the same surface: " + surface);
                }
                surfaceNames.add(surface);
                Boundary boundary = requiredBoundary(created, surface);
                if (index > 0) probe.append(',');
                probe.append("{\"role\":\"").append(faceRoles[index]).append("\",\"sample_m\":[")
                        .append(samples[index][0]).append(',').append(samples[index][1]).append(',')
                        .append(samples[index][2]).append("],\"patch_id\":").append(patch)
                        .append(",\"part_surface\":\"").append(escape(surface)).append("\"}");
                boundary.setPresentationName("AUTO_" + faceRoles[index].toUpperCase());
                if ("inlet".equals(faceRoles[index])) boundary.setBoundaryType(InletBoundary.class);
                else if ("outlet".equals(faceRoles[index])) boundary.setBoundaryType(PressureBoundary.class);
                else boundary.setBoundaryType(SymmetryBoundary.class);
                assigned.add(faceRoles[index] + "=" + boundary.getPresentationName());
                // Track the RENAMED boundary identity. Comparing the renamed name against the stale
                // part-surface names is what made an earlier revision declare every boundary
                // unclaimed and throw before it could save anything.
                renamed.add(boundary.getPresentationName());
            }
            } catch (Throwable probeFailure) {
                probe.append("],\"part_surfaces_after_split\":").append(surfaces.size())
                        .append(",\"status\":\"partial\",\"error\":\"")
                        .append(escape(String.valueOf(probeFailure.getMessage())))
                        .append("\",\"complete_marker\":null}\n");
                try (FileWriter writer = new FileWriter(new File(dir, "external_flow_region_probe.json"),
                        StandardCharsets.UTF_8)) {
                    writer.write(probe.toString());
                }
                throw probeFailure;
            }
            // Write the split evidence before anything else can throw: the split and the patch
            // mapping are the genuinely uncertain part of this stage, so a later failure must not
            // take that evidence with it.
            probe.append("],\"part_surfaces_after_split\":").append(surfaces.size())
                    .append(",\"surface_names\":[");
            for (int index = 0; index < surfaces.size(); index++) {
                if (index > 0) probe.append(',');
                probe.append('"').append(escape(surfaces.get(index).getPresentationName())).append('"');
            }
            probe.append("],\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
            try (FileWriter writer = new FileWriter(new File(dir, "external_flow_region_probe.json"),
                    StandardCharsets.UTF_8)) {
                writer.write(probe.toString());
            }
            // The audited one-body subtraction defines the inner faces. Outer faces were
            // measured above; every remaining boundary must own surfaces of this declared
            // body. Combine its cap/side surfaces into one wall without a sample-name branch.
            Boundary body = null;
            List<Boundary> bodyBoundaries = new ArrayList<Boundary>();
            List<PartSurface> bodySurfaces = new ArrayList<PartSurface>();
            String buildEvidence = new String(Files.readAllBytes(new File(dir, "external_domain_build.json").toPath()),
                    StandardCharsets.UTF_8);
            Matcher selected = Pattern.compile("\\\"subtracted_parts\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(buildEvidence);
            if (!selected.find() || !selected.group(1).trim().equals("\"" + bodyName + "\""))
                throw new IllegalStateException("Body ownership requires one audited subtraction target equal to the CaseSpec body");
            for (Boundary boundary : created.getBoundaryManager().getObjects()) {
                if (renamed.contains(boundary.getPresentationName())) continue;
                if (boundary.getPartSurfaceGroup().getObjects().isEmpty())
                    throw new IllegalStateException("Unclaimed body boundary has no surface ownership");
                for (PartSurface surface : boundary.getPartSurfaceGroup().getObjects()) {
                    if (!surfaces.contains(surface) || bodySurfaces.contains(surface))
                        throw new IllegalStateException("Ambiguous body surface ownership");
                    bodySurfaces.add(surface);
                }
                bodyBoundaries.add(boundary);
                if (body == null) body = boundary;
            }
            if (body == null) throw new IllegalStateException("No body boundary was left unclaimed");
            if (bodyBoundaries.size() > 1) {
                for (Boundary boundary : bodyBoundaries)
                    boundary.getPartSurfaceGroup().setObjects(new ArrayList<PartSurface>());
                body.getPartSurfaceGroup().setObjects(bodySurfaces);
                List<Boundary> redundant = new ArrayList<Boundary>(bodyBoundaries);
                redundant.remove(body);
                created.getBoundaryManager().removeObjects(redundant);
            }
            if (body.getPartSurfaceGroup().getObjects().size() != bodySurfaces.size())
                throw new IllegalStateException("Combined body wall lost owned surfaces");
            body.setPresentationName(optional(spec, "body_wall_boundary_name", "AUTO_" + bodyName + "_WALL"));
            body.setBoundaryType(WallBoundary.class);

            int boundaryCount = created.getBoundaryManager().getObjects().size();
            if (boundaryCount != 7) {
                throw new IllegalStateException("Expected seven boundaries (six box faces plus the body), got "
                        + boundaryCount);
            }
            sim.saveState(new File(dir, "external_flow_region.sim").getAbsolutePath());
            try (FileWriter writer = new FileWriter(new File(dir, "external_flow_region.json"),
                    StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"" + escape(regionName)
                        + "\",\"boundary_count\":" + boundaryCount + ",\"part_surfaces\":" + surfaces.size()
                        + ",\"body_surface_count\":" + bodySurfaces.size()
                        + ",\"roles\":[");
                for (int index = 0; index < assigned.size(); index++) {
                    if (index > 0) writer.write(',');
                    writer.write("\"" + escape(assigned.get(index)) + "\"");
                }
                writer.write("],\"body_boundary\":\"" + escape(body.getPresentationName())
                        + "\",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("External-flow region construction failed", failure);
        }
    }

    private static String nameForPatch(MeshOperationPart part, int patch) {
        for (PartSurface surface : part.getPartSurfaceManager().getObjects()) {
            List<Integer> patches = part.getPartSurfacePatches(surface);
            if (patches != null && patches.contains(Integer.valueOf(patch))) return surface.getPresentationName();
        }
        return null;
    }

    private static Boundary requiredBoundary(Region region, String surfaceName) {
        Boundary direct = region.getBoundaryManager().getBoundary(surfaceName);
        if (direct != null) return direct;
        Boundary found = null;
        for (Boundary boundary : region.getBoundaryManager().getObjects()) {
            String name = boundary.getPresentationName();
            if (name.endsWith(surfaceName) || name.endsWith("." + surfaceName) || surfaceName.endsWith(name)) {
                if (found != null) throw new IllegalStateException("Ambiguous boundary for surface " + surfaceName);
                found = boundary;
            }
        }
        if (found == null) {
            StringBuilder names = new StringBuilder();
            for (Boundary boundary : region.getBoundaryManager().getObjects()) {
                names.append(boundary.getPresentationName()).append(' ');
            }
            throw new IllegalStateException("No boundary matches part surface " + surfaceName + "; found: " + names);
        }
        return found;
    }

    private static String optional(String json, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        return match.find() ? match.group(1) : fallback;
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
