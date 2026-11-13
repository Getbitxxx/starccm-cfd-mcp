/* Cylindrical open-air geometry on an isolated STAR-CCM+ stage copy. */
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
import star.meshing.MeshPartFactory;
import star.meshing.SimpleCylinderPart;
import star.meshing.SubtractPartsOperation;

public class BuildCylindricalDomain extends StarMacro {
    private static final String COMPLETE = "CYLINDRICAL_DOMAIN_BUILD_COMPLETE";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "cylindrical_domain_build.json");
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String unitName = stringValue(spec, "model_length_unit");
            double scale = unitToMeters(unitName);
            double radiusM = numberValue(spec, "cylinder_radius_m");
            double zMinM = numberValue(spec, "cylinder_z_min_m");
            double zMaxM = numberValue(spec, "cylinder_z_max_m");
            double[] centerM = arrayValue(spec, "cylinder_center_xy_m", 2);
            if (!(radiusM > 0.0 && zMinM < zMaxM)) throw new IllegalArgumentException("Invalid cylinder dimensions");
            List<String> targets = arrayStrings(spec, "body_parts");
            for (String rotor : arrayStrings(spec, "rotor_parts")) if (!targets.contains(rotor)) targets.add(rotor);
            if (targets.isEmpty()) throw new IllegalArgumentException("No selected solid parts");

            SimulationPartManager parts = sim.get(SimulationPartManager.class);
            for (GeometryPart part : parts.getObjects()) {
                if ("AUTO_FREE_AIR_CYLINDER".equals(part.getPresentationName()) ||
                        "AUTO_FREE_AIR_DOMAIN".equals(part.getPresentationName())) {
                    throw new IllegalStateException("Generated domain geometry already exists");
                }
            }
            SimpleCylinderPart cylinder = sim.get(MeshPartFactory.class).createNewCylinderPart(parts);
            cylinder.setPresentationName("AUTO_FREE_AIR_CYLINDER");
            cylinder.setDoNotRetessellate(true);
            LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
            Units units = sim.getUnitsManager().getObject(unitName);
            cylinder.setCoordinateSystem(lab);
            cylinder.getStartCoordinate().setCoordinateSystem(lab);
            cylinder.getStartCoordinate().setCoordinate(units, units, units,
                    new DoubleVector(new double[] {centerM[0] / scale, centerM[1] / scale, zMinM / scale}));
            cylinder.getEndCoordinate().setCoordinateSystem(lab);
            cylinder.getEndCoordinate().setCoordinate(units, units, units,
                    new DoubleVector(new double[] {centerM[0] / scale, centerM[1] / scale, zMaxM / scale}));
            cylinder.getRadius().setValueAndUnits(radiusM / scale, units);
            cylinder.rebuildSimpleShapePart();
            cylinder.setDoNotRetessellate(false);
            PartSurface unsplit = cylinder.getPartSurfaceManager().getPartSurface("Cylinder Surface");
            if (unsplit == null) throw new IllegalStateException("Cylinder surface missing after rebuild");
            cylinder.getPartSurfaceManager().splitPartSurfacesByAngle(
                    new NeoObjectVector(new Object[] {unsplit}), 89.0);
            if (cylinder.getPartSurfaceManager().getObjects().size() < 3) {
                throw new IllegalStateException("Cylinder caps were not separated from the side surface");
            }

            List<Object> inputs = new ArrayList<Object>();
            inputs.add(cylinder);
            for (String name : targets) {
                Object selected = parts.getPart(name);
                if (!(selected instanceof GeometryPart)) throw new IllegalArgumentException("Selected part missing: " + name);
                inputs.add(selected);
            }
            Set<String> before = new HashSet<String>();
            for (GeometryPart part : parts.getObjects()) before.add(part.getPresentationName());
            SubtractPartsOperation operation = (SubtractPartsOperation) sim.get(MeshOperationManager.class)
                    .createSubtractPartsOperation(new NeoObjectVector(inputs.toArray()));
            operation.setTargetPart(cylinder);
            operation.setPerformCADBoolean(true);
            operation.execute();
            MeshOperationPart result = null;
            Collection<GeometryPart> after = parts.getObjects();
            for (GeometryPart part : after) {
                if (part instanceof MeshOperationPart && !before.contains(part.getPresentationName())) {
                    if (result != null) throw new IllegalStateException("Boolean yielded multiple output parts");
                    result = (MeshOperationPart) part;
                }
            }
            if (result == null) throw new IllegalStateException("Boolean yielded no output part");
            result.setPresentationName("AUTO_FREE_AIR_DOMAIN");
            operation.setPresentationName("AUTO_FREE_AIR_CYLINDER_BOOLEAN");
            sim.saveState(new File(dir, "cylindrical_domain_built.sim").getAbsolutePath());
            writeEvidence(evidence, radiusM, zMinM, zMaxM, centerM, unitName, targets, "ok", null);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try { writeEvidence(evidence, 0, 0, 0, null, null, null, "error", failure.toString()); }
            catch (Throwable ignored) { }
            throw new RuntimeException("Cylindrical-domain construction failed", failure);
        }
    }

    private static String stringValue(String json, String key) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing string " + key);
        return match.group(1);
    }

    private static double numberValue(String json, String key) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double[] arrayValue(String json, String key, int length) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
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

    private static List<String> arrayStrings(String json, String key) {
        Matcher match = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        List<String> values = new ArrayList<String>();
        if (!match.find()) return values;
        Matcher item = Pattern.compile("\\\"([^\\\"]+)\\\"").matcher(match.group(1));
        while (item.find()) values.add(item.group(1));
        return values;
    }

    private static double unitToMeters(String name) {
        if ("m".equals(name)) return 1.0;
        if ("mm".equals(name)) return 0.001;
        if ("cm".equals(name)) return 0.01;
        if ("in".equals(name)) return 0.0254;
        throw new IllegalArgumentException("Unsupported length unit " + name);
    }

    private static void writeEvidence(File file, double radius, double zMin, double zMax,
            double[] center, String units, List<String> targets, String status, String error) throws Exception {
        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"status\":\"").append(status).append("\"");
        if (center != null) {
            json.append(",\"cylinder_radius_m\":").append(radius)
                    .append(",\"cylinder_z_min_m\":").append(zMin)
                    .append(",\"cylinder_z_max_m\":").append(zMax)
                    .append(",\"cylinder_center_xy_m\":[").append(center[0]).append(',').append(center[1]).append(']')
                    .append(",\"model_length_unit\":\"").append(units).append("\",\"subtracted_parts\":[");
            for (int i = 0; i < targets.size(); i++) {
                if (i > 0) json.append(',');
                json.append('"').append(targets.get(i).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
            }
            json.append(']');
        }
        if (error != null) json.append(",\"error\":\"").append(escapeControl(error)).append('"');
        json.append(",\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
        try (FileWriter writer = new FileWriter(file, StandardCharsets.UTF_8)) { writer.write(json.toString()); }
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
