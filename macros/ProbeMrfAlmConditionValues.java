/* Read-only probe (P2.3 G5 MESH-B7 follow-up): read the NUMERIC values of the PrismDefaultValuesManager conditions
 * of the ALM rotating operation (NumPrismLayers / PrismThickness / PrismLayerStretching), to decide whether the
 * effective ALM controls differ from the top-level default values the macro sets. No save/solve/mesh; flat keys.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.*;
import star.meshing.*;
import star.prismmesher.NumPrismLayers;
import star.prismmesher.PrismLayerStretching;
import star.prismmesher.PrismThickness;
import star.bodyfittedmesher.AdvancingLayerAutoMesher;

public class ProbeMrfAlmConditionValues extends StarMacro {

    private static final String COMPLETE = "MRF_ALM_CONDITION_VALUES_COMPLETE";

    public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder body = new StringBuilder();
        boolean failed = false;
        String errorText = null;
        try {
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            int ops = 0;
            for (MeshOperation operation : manager.getObjects()) {
                if (!(operation instanceof AutoMeshOperation)) continue;
                AutoMeshOperation op = (AutoMeshOperation) operation;
                boolean alm = false;
                for (MesherBase mesher : op.getMeshersCollection()) {
                    if (mesher instanceof AdvancingLayerAutoMesher) alm = true;
                }
                if (!alm) continue;
                ops++;
                String p = "operation_" + ops + "_";
                PrismDefaultValuesManager pdvm = null;
                try { pdvm = op.getDefaultValues().get(PrismDefaultValuesManager.class); } catch (Throwable ignored) { }
                body.append(",\"").append(p).append("name\":\"").append(op.getPresentationName()).append('"');
                body.append(",\"").append(p).append("manager_present\":").append(pdvm != null);
                int conditions = 0, parsedCount = 0;
                boolean hasLayers = false, hasThickness = false, hasStretch = false;
                StringBuilder unexpected = new StringBuilder();
                int layersValue = -1;
                double thicknessValue = Double.NaN, stretchValue = Double.NaN;
                if (pdvm != null) {
                    for (Object condition : pdvm.getConditions()) {
                        conditions++;
                        String c = p + "condition_" + conditions + "_";
                        body.append(",\"").append(c).append("class\":\"").append(condition.getClass().getName()).append('"');
                        try {
                            if (condition instanceof NumPrismLayers) {
                                layersValue = ((NumPrismLayers) condition).getNumLayers();
                                hasLayers = true; parsedCount++;
                                body.append(",\"").append(c).append("num_prism_layers\":").append(layersValue);
                            } else if (condition instanceof PrismThickness) {
                                thicknessValue = ((PrismThickness) condition).getAbsoluteSizeValue().getSIValue();
                                if (!Double.isFinite(thicknessValue) || thicknessValue <= 0)
                                    throw new IllegalStateException("non-positive PrismThickness");
                                hasThickness = true; parsedCount++;
                                body.append(",\"").append(c).append("prism_total_m\":").append(thicknessValue);
                            } else if (condition instanceof PrismLayerStretching) {
                                stretchValue = ((PrismLayerStretching) condition).getStretching();
                                if (!Double.isFinite(stretchValue) || stretchValue <= 1.0)
                                    throw new IllegalStateException("non-positive PrismLayerStretching");
                                hasStretch = true; parsedCount++;
                                body.append(",\"").append(c).append("prism_stretch\":").append(stretchValue);
                            } else {
                                if (unexpected.length() > 0) unexpected.append(';');
                                unexpected.append(condition.getClass().getName());
                            }
                        } catch (Throwable failure) {
                            if (unexpected.length() > 0) unexpected.append(';');
                            unexpected.append(condition.getClass().getName()).append(": ").append(failure);
                        }
                    }
                }
                body.append(",\"").append(p).append("conditions_count\":").append(conditions);
                body.append(",\"").append(p).append("num_conditions_parsed\":").append(parsedCount);
                if (unexpected.length() > 0 || parsedCount != 3 || !hasLayers || !hasThickness || !hasStretch) {
                    failed = true;
                    errorText = "conditions=" + conditions + " parsed=" + parsedCount + " unexpected=" + unexpected;
                } else {
                    body.append(",\"").append(p).append("num_prism_layers\":").append(layersValue);
                    body.append(",\"").append(p).append("prism_total_m\":").append(thicknessValue);
                    body.append(",\"").append(p).append("prism_stretch\":").append(stretchValue);
                }
            }
            body.append(",\"alm_operations\":").append(ops);
        } catch (Throwable failure) {
            failed = true;
            errorText = "probe failure: " + failure;
        }
        StringBuilder json = new StringBuilder("{\"schema_version\":1,\"probe\":\"alm_condition_values\",\"status\":\"")
                .append(failed ? "error" : "ok").append('"').append(body);
        if (failed) {
            json.append(",\"conditions_error\":\"").append(String.valueOf(errorText)
                .replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")).append('"');
        }
        json.append('}');
        try (FileWriter out = new FileWriter(new File("mrf_alm_condition_values.json"), StandardCharsets.UTF_8)) {
            out.write(json.toString());
        } catch (Throwable ignored) { }
        System.out.println(COMPLETE);
    }
}
