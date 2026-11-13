/* Read-only probe (P2.3 G5 MESH-B6 follow-up): list the default-value classes/values of the ALM rotating
 * mesh operation, so the ALM-specific layer count / thickness / stretching controls can be mapped explicitly.
 * No mesh, no solve, no save; reads the already-loaded saved mesh state and writes one JSON record.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import star.base.neo.ClientServerObject;
import star.common.*;
import star.meshing.*;
import star.prismmesher.NumPrismLayers;
import star.prismmesher.PrismLayerStretching;
import star.prismmesher.PrismThickness;

public class ProbeMrfAlmDefaults extends StarMacro {

    public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder json = new StringBuilder("{\"schema_version\":1,\"status\":\"ok\",\"probe\":\"alm_defaults\"");
        StringBuilder flat = new StringBuilder();
        try {
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            List<String> ops = new ArrayList<String>();
            for (MeshOperation operation : manager.getObjects()) {
                if (!(operation instanceof AutoMeshOperation)) continue;
                AutoMeshOperation op = (AutoMeshOperation) operation;
                ops.add(op.getPresentationName());
                json.append(",\"operation_").append(ops.size()).append("\":{\"name\":\"")
                    .append(op.getPresentationName()).append('"');
                var values = op.getDefaultValues();
                StringBuilder known = new StringBuilder();
                appendKnown(known, op, BaseSize.class, "BaseSize");
                appendKnown(known, op, NumPrismLayers.class, "NumPrismLayers");
                appendKnown(known, op, PrismThickness.class, "PrismThickness");
                appendKnown(known, op, PrismLayerStretching.class, "PrismLayerStretching");
                appendKnown(known, op, star.bodyfittedmesher.BodyfittedPrismLayerThicknessRatio.class,
                        "BodyfittedPrismLayerThicknessRatio");
                appendKnown(known, op, star.bodyfittedmesher.BodyfittedPrismLayerStretching.class,
                        "BodyfittedPrismLayerStretching");
                json.append(",\"known\":[").append(known).append(']');
                StringBuilder numeric = new StringBuilder();
                try {
                    NumPrismLayers layerCount = op.getDefaultValues().get(NumPrismLayers.class);
                    if (layerCount != null) {
                        if (numeric.length() > 0) numeric.append(',');
                        numeric.append("\"num_prism_layers\":").append(layerCount.getNumLayers());
                    }
                    PrismThickness thickness = op.getDefaultValues().get(PrismThickness.class);
                    if (thickness != null) {
                        if (numeric.length() > 0) numeric.append(',');
                        numeric.append("\"prism_total_m\":").append(thickness.getAbsoluteSizeValue().getSIValue());
                    }
                    PrismLayerStretching stretch = op.getDefaultValues().get(PrismLayerStretching.class);
                    if (stretch != null) {
                        if (numeric.length() > 0) numeric.append(',');
                        numeric.append("\"prism_stretch\":").append(stretch.getStretching());
                    }
                } catch (Throwable failure) {
                    json.append(",\"numeric_error\":\"").append(String.valueOf(failure).replace("\"", "\\\"")).append('"');
                }
                if (numeric.length() > 0) json.append(",\"numeric\":{").append(numeric).append('}');
                int index = ops.size();
                try {
                    NumPrismLayers layerCount = op.getDefaultValues().get(NumPrismLayers.class);
                    if (layerCount != null) {
                        if (flat.length() > 0) flat.append(',');
                        flat.append("\"operation_").append(index).append("_num_prism_layers\":").append(layerCount.getNumLayers());
                    }
                    PrismThickness thickness = op.getDefaultValues().get(PrismThickness.class);
                    if (thickness != null) {
                        if (flat.length() > 0) flat.append(',');
                        flat.append("\"operation_").append(index).append("_prism_total_m\":")
                            .append(thickness.getAbsoluteSizeValue().getSIValue());
                    }
                    PrismLayerStretching stretch = op.getDefaultValues().get(PrismLayerStretching.class);
                    if (stretch != null) {
                        if (flat.length() > 0) flat.append(',');
                        flat.append("\"operation_").append(index).append("_prism_stretch\":").append(stretch.getStretching());
                    }
                } catch (Throwable ignored) { }
                try {
                    StringBuilder defaults = new StringBuilder();
                    for (Object value : values.getObjects()) {
                        if (defaults.length() > 0) defaults.append(',');
                        defaults.append("{\"class\":\"").append(value.getClass().getName()).append("\",\"value\":\"")
                            .append(String.valueOf(value).replace("\\", "\\\\").replace("\"", "\\\"")).append("\"}");
                    }
                    json.append(",\"defaults\":[").append(defaults).append(']');
                } catch (Throwable failure) {
                    json.append(",\"defaults_error\":\"").append(String.valueOf(failure).replace("\"", "\\\"")).append('"');
                }
                json.append('}');
            }
            json.append(",\"operations\":").append(ops.size());
        } catch (Throwable failure) {
            json.setLength(0);
            json.append("{\"schema_version\":1,\"status\":\"error\",\"error\":\"")
                .append(String.valueOf(failure).replace("\"", "\\\"")).append('"');
        }
        if (flat.length() > 0) json.append(',').append(flat);
        json.append('}');
        try (FileWriter out = new FileWriter(new File("mrf_alm_defaults_probe.json"), StandardCharsets.UTF_8)) {
            out.write(json.toString());
        } catch (Throwable ignored) { }
        System.out.println("MRF_ALM_DEFAULTS_PROBE_COMPLETE");
    }

    private static void appendKnown(StringBuilder out, AutoMeshOperation op, Class<? extends ClientServerObject> type, String label) {
        try {
            ClientServerObject value = op.getDefaultValues().get(type);
            if (value == null) { return; }
            if (out.length() > 0) out.append(',');
            out.append("{\"label\":\"").append(label).append("\",\"class\":\"").append(value.getClass().getName())
               .append("\",\"value\":\"").append(String.valueOf(value).replace("\\", "\\\\").replace("\"", "\\\"")).append("\"}");
        } catch (Throwable ignored) { }
    }
}
