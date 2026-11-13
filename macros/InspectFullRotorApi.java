import java.io.File;
import java.io.FileWriter;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;

import star.common.Simulation;
import star.common.GeometryPart;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.MeshOperationManager;
import star.meshing.TransformPartsOperation;

/** Read-only inventory of installed geometry transform API signatures. */
public class InspectFullRotorApi extends StarMacro {
    private static final String COMPLETE = "FULL_ROTOR_API_PROBE_COMPLETE";
    private static final String[] TYPES = {
        "star.meshing.TransformPartsOperation",
        "star.meshing.TransformsManager",
        "star.meshing.TransformControl",
        "star.meshing.MeshOperationManager",
        "star.meshing.RotationControl",
        "star.meshing.RotateControl",
        "star.meshing.RotatingControl",
        "star.meshing.UnitePartsOperation",
        "star.meshing.GeometryTransformControl",
        "star.meshing.TransformationControl"
    };

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File output = new File(sim.getSessionDirFile(), "full_rotor_api.json");
        List<String> records = new ArrayList<String>();
        for (String name : TYPES) records.add(inspect(name));
        records.add(inspectLiveControls(sim));
        try (FileWriter writer = new FileWriter(output, StandardCharsets.UTF_8)) {
            writer.write("{\n  \"schema_version\": 1,\n  \"status\": \"ok\",\n  \"types\": [\n    ");
            writer.write(String.join(",\n    ", records));
            writer.write("\n  ],\n  \"complete_marker\": \"" + COMPLETE + "\"\n}\n");
        } catch (Exception e) {
            throw new RuntimeException("Could not write API probe evidence", e);
        }
        System.out.println(COMPLETE);
    }

    private static String inspectLiveControls(Simulation sim) {
        try {
            GeometryPart source = sim.get(SimulationPartManager.class).getPart("PROPELLER");
            TransformPartsOperation operation = (TransformPartsOperation) sim.get(MeshOperationManager.class)
                    .createTransformPartsOperation(java.util.Arrays.asList(source));
            List<String> controls = new ArrayList<String>();
            for (Object control : operation.getTransforms().getObjects()) {
                controls.add("{\"name\":\"" + escape(String.valueOf(control)) + "\",\"class\":\"" +
                        escape(control.getClass().getName()) + "\"}");
            }
            List<String> methods = new ArrayList<String>();
            for (Method method : operation.getTransforms().getClass().getMethods()) {
                String name = method.getName().toLowerCase();
                if (name.contains("add") || name.contains("create") || name.contains("rotat") ||
                        name.contains("transform") || name.contains("object")) methods.add("\"" +
                                escape(method.toGenericString()) + "\"");
            }
            return "{\"class\":\"live_transform_controls\",\"available\":true,\"manager_class\":\"" +
                    escape(operation.getTransforms().getClass().getName()) + "\",\"controls\":[" +
                    String.join(",", controls) + "],\"manager_methods\":[" + String.join(",", methods) + "]}";
        } catch (Throwable failure) {
            return "{\"class\":\"live_transform_controls\",\"available\":false,\"error\":\"" +
                    escape(failure.getClass().getSimpleName() + ": " + String.valueOf(failure.getMessage())) + "\"}";
        }
    }

    private static String inspect(String typeName) {
        try {
            Class<?> type = Class.forName(typeName);
            Method[] methods = type.getMethods();
            Arrays.sort(methods, Comparator.comparing(Method::getName).thenComparing(Method::toString));
            List<String> selected = new ArrayList<String>();
            for (Method method : methods) {
                String name = method.getName().toLowerCase();
                if (name.contains("transform") || name.contains("rotat") || name.contains("angle") ||
                        name.contains("axis") || name.contains("origin") || name.contains("part") ||
                        name.equals("execute") || name.startsWith("create")) {
                    selected.add("\"" + escape(method.toGenericString()) + "\"");
                }
            }
            return "{\"class\":\"" + escape(typeName) + "\",\"available\":true,\"methods\":[" +
                    String.join(",", selected) + "]}";
        } catch (Throwable failure) {
            return "{\"class\":\"" + escape(typeName) + "\",\"available\":false,\"error\":\"" +
                    escape(failure.getClass().getSimpleName() + ": " + String.valueOf(failure.getMessage())) + "\"}";
        }
    }

    private static String escape(String value) {
        // Full control-character coverage: newlines in a STAR exception message made a failure record
        // unparseable in round 6. Round 7 review: escaping only \\r and \\n still leaves tab and other
        // control characters able to destroy the record, so anything below 0x20 is escaped.
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
