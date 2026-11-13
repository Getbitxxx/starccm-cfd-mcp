/* Read-only probe (P2.3 G5 MESH-B7 follow-up): inspect the PrismDefaultValuesManager conditions of the ALM
 * rotating operation - the last API place an ALM-specific prism-layer override could live. No save/solve/mesh;
 * flat JSON keys for the literal runner gate lookup.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.*;
import star.meshing.*;
import star.bodyfittedmesher.AdvancingLayerAutoMesher;

public class ProbeMrfAlmPrismDefaultsManager extends StarMacro {

    private static final String COMPLETE = "MRF_ALM_PRISM_MANAGER_PROBE_COMPLETE";

    public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder json = new StringBuilder("{\"schema_version\":1,\"status\":\"ok\",\"probe\":\"alm_prism_manager\"");
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
                json.append(",\"").append(p).append("name\":\"").append(op.getPresentationName()).append('"');
                json.append(",\"").append(p).append("manager_present\":").append(pdvm != null);
                int conditions = 0;
                if (pdvm != null) {
                    try {
                        for (Object condition : pdvm.getConditions()) {
                            conditions++;
                            String c = p + "condition_" + conditions + "_";
                            json.append(",\"").append(c).append("class\":\"").append(condition.getClass().getName()).append('"');
                            json.append(",\"").append(c).append("value\":\"")
                                .append(String.valueOf(condition).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
                        }
                    } catch (Throwable failure) {
                        json.append(",\"").append(p).append("conditions_error\":\"")
                            .append(String.valueOf(failure).replace("\"", "\\\"")).append('"');
                    }
                }
                json.append(",\"").append(p).append("conditions_count\":").append(conditions);
            }
            json.append(",\"alm_operations\":").append(ops);
        } catch (Throwable failure) {
            json.setLength(0);
            json.append("{\"schema_version\":1,\"status\":\"error\",\"error\":\"")
                .append(String.valueOf(failure).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
        }
        json.append('}');
        try (FileWriter out = new FileWriter(new File("mrf_alm_prism_manager_probe.json"), StandardCharsets.UTF_8)) {
            out.write(json.toString());
        } catch (Throwable ignored) { }
        System.out.println(COMPLETE);
    }
}
