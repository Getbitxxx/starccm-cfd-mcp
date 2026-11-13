/*
 * BuildExternalDomain.java
 *
 * Build a rectangular external fluid volume on the isolated stage copy. The six
 * outer bounds and the target CAD part names come from case_spec.json, supplied
 * by the durable runner. This stage does not mesh, assign boundary conditions,
 * initialize, iterate, or modify the source .sim.
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
import star.base.neo.IntVector;
import star.base.neo.NeoObjectVector;
import star.common.GeometryPart;
import star.common.LabCoordinateSystem;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;
import star.meshing.MeshPartFactory;
import star.meshing.SimpleBlockPart;
import star.meshing.SubtractPartsOperation;

public class BuildExternalDomain extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_DOMAIN_BUILD_COMPLETE";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_domain_build.json");
        try {
            File specFile = new File(dir, "case_spec.json");
            if (!specFile.isFile()) throw new IllegalStateException("case_spec.json missing beside stage input");
            String spec = new String(Files.readAllBytes(specFile.toPath()), StandardCharsets.UTF_8);
            String unitName = stringValue(spec, "model_length_unit");
            double unitToM = unitToMeters(unitName);
            double[] boundsM = arrayValue(spec, "outer_bounds_m", 6);
            List<String> targets = arrayStrings(spec, "body_parts");
            for (String rotor : arrayStrings(spec, "rotor_parts")) if (!targets.contains(rotor)) targets.add(rotor);
            if (targets.isEmpty()) throw new IllegalArgumentException("No geometry.body_parts or rotor_parts selected");
            for (int axis = 0; axis < 3; axis++) {
                if (!(boundsM[axis] < boundsM[axis + 3])) throw new IllegalArgumentException("Outer domain bounds are degenerate");
            }

            SimulationPartManager partManager = sim.get(SimulationPartManager.class);
            for (GeometryPart existing : partManager.getObjects()) {
                if ("AUTO_FREE_AIR_BLOCK".equals(existing.getPresentationName()) ||
                        "AUTO_FREE_AIR_DOMAIN".equals(existing.getPresentationName())) {
                    throw new IllegalStateException("Generated part name already exists; inspect and rename it before rebuilding");
                }
            }
            List<Object> subtract = new ArrayList<Object>();
            MeshPartFactory factory = sim.get(MeshPartFactory.class);
            SimpleBlockPart block = factory.createNewBlockPart(partManager);
            block.setPresentationName("AUTO_FREE_AIR_BLOCK");
            block.setDoNotRetessellate(true);
            LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
            Units modelUnits = sim.getUnitsManager().getObject(unitName);
            block.setCoordinateSystem(lab);
            double[] lo = new double[3];
            double[] hi = new double[3];
            for (int axis = 0; axis < 3; axis++) {
                lo[axis] = boundsM[axis] / unitToM;
                hi[axis] = boundsM[axis + 3] / unitToM;
            }
            block.getCorner1().setCoordinateSystem(lab);
            block.getCorner1().setCoordinate(modelUnits, modelUnits, modelUnits,
                    new DoubleVector(new double[] {lo[0], lo[1], lo[2]}));
            block.getCorner2().setCoordinateSystem(lab);
            block.getCorner2().setCoordinate(modelUnits, modelUnits, modelUnits,
                    new DoubleVector(new double[] {hi[0], hi[1], hi[2]}));
            block.rebuildSimpleShapePart();
            block.setDoNotRetessellate(false);
            subtract.add(block);
            for (String name : targets) {
                // Same tree scan as the audit (①R24 measured that getPart(String) searches TOP-LEVEL parts
                // only and THROWS for an absent name, while the repaired CAD import keeps the CaseSpec body
                // name on a NESTED solid leaf under a *CompositePart root). The ①R25 authorized revision is
                // this lookup only: a subtract target must still be the declared GeometryPart, and a missing
                // or unowned name is refused with the names the tree really exposes.
                GeometryPart found = locatePart(partManager, name);
                if (found == null) {
                    throw new IllegalArgumentException("Selected CAD geometry part not found: " + name
                            + "; the part tree contains " + treeNames(partManager));
                }
                subtract.add(found);
            }

            Set<String> existingNames = new HashSet<String>();
            for (GeometryPart part : partManager.getObjects()) existingNames.add(part.getPresentationName());
            SubtractPartsOperation operation = (SubtractPartsOperation) sim.get(MeshOperationManager.class)
                    .createSubtractPartsOperation(new NeoObjectVector(subtract.toArray()));
            operation.setTargetPart(block);
            operation.setPerformCADBoolean(true);
            operation.execute();
            MeshOperationPart result = null;
            List<String> newParts = new ArrayList<String>();
            Collection<GeometryPart> after = partManager.getObjects();
            for (GeometryPart part : after) {
                if (part instanceof MeshOperationPart && !existingNames.contains(part.getPresentationName())) {
                    if (result != null) throw new IllegalStateException("Boolean produced multiple new mesh-operation parts: " + newParts);
                    result = (MeshOperationPart) part;
                }
                if (!existingNames.contains(part.getPresentationName())) newParts.add(part.getPresentationName());
            }
            if (result == null) throw new IllegalStateException("Boolean subtraction output part was not found; new parts: " + newParts);
            result.setPresentationName("AUTO_FREE_AIR_DOMAIN");
            operation.setPresentationName("AUTO_FREE_AIR_DOMAIN_BOOLEAN");
            sim.saveState(new File(dir, "external_domain_built.sim").getAbsolutePath());
            writeEvidence(evidence, boundsM, unitName, targets, "ok", null);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try { writeEvidence(evidence, null, null, null, "error", failure.toString()); }
            catch (Throwable ignored) { }
            throw new RuntimeException("External-domain construction failed: " + failure, failure);
        }
    }

    /** Find the DECLARED part by scanning the actual part tree; never by getPart(String).
     *
     * Identical method to AuditStaticBodyGeometry.locatePart (①R25): round 24 measured on the delivered
     * cylinder source .sim (b8c9a082...) that `SimulationPartManager.getPart(String)` searches TOP-LEVEL
     * parts only and THROWS star.base.neo.NeoException for an absent name, while the repaired CAD import
     * keeps the CaseSpec body name on a NESTED solid leaf (`INDEPENDENT_CYLINDER`, a
     * star.cadmodeler.SolidModelPart) under a composite root. Breadth-first over top-level parts and, for
     * every composite, its leaves and children; null only after the whole tree was searched. Nothing is
     * caught, so a genuine API error still surfaces.
     */
    private static GeometryPart locatePart(SimulationPartManager parts, String name) {
        List<GeometryPart> queue = new ArrayList<GeometryPart>();
        List<GeometryPart> visited = new ArrayList<GeometryPart>();
        for (GeometryPart top : parts.getParts()) queue.add(top);
        for (int index = 0; index < queue.size(); index++) {
            GeometryPart candidate = queue.get(index);
            boolean alreadySeen = false;
            for (GeometryPart previous : visited) {
                if (previous == candidate) { alreadySeen = true; break; }
            }
            if (alreadySeen) continue;
            visited.add(candidate);
            if (name.equals(candidate.getPresentationName())) return candidate;
            if (candidate instanceof star.common.CompositePart) {
                star.common.CompositePart composite = (star.common.CompositePart) candidate;
                Collection<GeometryPart> leaves = composite.getLeafParts();
                if (leaves != null) for (GeometryPart leaf : leaves) queue.add(leaf);
                Collection<GeometryPart> children = composite.getChildParts().getParts();
                if (children != null) for (GeometryPart child : children) queue.add(child);
            }
        }
        return null;
    }

    /** The names this tree actually exposes, so the failure message names reality instead of guessing. */
    private static String treeNames(SimulationPartManager parts) {
        List<String> names = new ArrayList<String>();
        List<GeometryPart> queue = new ArrayList<GeometryPart>();
        List<GeometryPart> visited = new ArrayList<GeometryPart>();
        for (GeometryPart top : parts.getParts()) queue.add(top);
        for (int index = 0; index < queue.size(); index++) {
            GeometryPart candidate = queue.get(index);
            boolean alreadySeen = false;
            for (GeometryPart previous : visited) {
                if (previous == candidate) { alreadySeen = true; break; }
            }
            if (alreadySeen) continue;
            visited.add(candidate);
            String label = candidate.getPresentationName();
            if (!names.contains(label)) names.add(label);
            if (candidate instanceof star.common.CompositePart) {
                star.common.CompositePart composite = (star.common.CompositePart) candidate;
                Collection<GeometryPart> leaves = composite.getLeafParts();
                if (leaves != null) for (GeometryPart leaf : leaves) queue.add(leaf);
                Collection<GeometryPart> children = composite.getChildParts().getParts();
                if (children != null) for (GeometryPart child : children) queue.add(child);
            }
        }
        return names.toString();
    }

    private static String stringValue(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string: " + key);
        return m.group(1);
    }

    private static double[] arrayValue(String json, String key, int count) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing array: " + key);
        String[] fields = m.group(1).split(",");
        if (fields.length != count) throw new IllegalArgumentException(key + " must contain " + count + " numbers");
        double[] values = new double[count];
        for (int i = 0; i < count; i++) {
            values[i] = Double.parseDouble(fields[i].trim());
            if (Double.isNaN(values[i]) || Double.isInfinite(values[i])) throw new IllegalArgumentException(key + " contains a non-finite number");
        }
        return values;
    }

    private static List<String> arrayStrings(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        List<String> values = new ArrayList<String>();
        if (!m.find()) return values;
        Matcher item = Pattern.compile("\\\"([^\\\"]+)\\\"").matcher(m.group(1));
        while (item.find()) values.add(item.group(1));
        return values;
    }

    private static double unitToMeters(String name) {
        if ("m".equals(name)) return 1.0;
        if ("mm".equals(name)) return 0.001;
        if ("cm".equals(name)) return 0.01;
        if ("in".equals(name)) return 0.0254;
        throw new IllegalArgumentException("Unsupported model length unit: " + name);
    }

    private static void writeEvidence(File file, double[] bounds, String unit, List<String> targets,
            String status, String error) throws Exception {
        StringBuilder json = new StringBuilder();
        json.append("{\n  \"schema_version\": 1,\n  \"status\": \"").append(status).append("\",\n");
        if (bounds != null) {
            json.append("  \"outer_bounds_m\": [");
            for (int i = 0; i < bounds.length; i++) { if (i > 0) json.append(", "); json.append(bounds[i]); }
            json.append("],\n  \"model_length_unit\": \"").append(unit).append("\",\n  \"subtracted_parts\": [");
            for (int i = 0; i < targets.size(); i++) { if (i > 0) json.append(", "); json.append("\"").append(targets.get(i).replace("\"", "\\\"")).append("\""); }
            json.append("],\n");
        }
        if (error != null) json.append("  \"error\": \"").append(escapeControl(error)).append("\",\n");
        json.append("  \"complete_marker\": \"").append(COMPLETE).append("\"\n}\n");
        FileWriter out = new FileWriter(file, StandardCharsets.UTF_8);
        try { out.write(json.toString()); } finally { out.close(); }
    }

    /** JSON string escaper for evidence records: control characters must never be written raw.
     *  A STAR exception message contains newlines; emitting them unescaped produced an unparseable
     *  failure record in round 6, i.e. the evidence that existed to preserve the diagnosis was lost. */
    private static String escapeControl(String value) {
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
