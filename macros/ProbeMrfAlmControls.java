/* Read-only probe (P2.3 G5 MESH-B6 follow-up): print the ALM model controls (aspect-ratio limit, sharp-edge
 * refinement, surface element option, boundary march angle) of the rotating mesh operation, to identify the
 * control that caps the realised layer count (~10) below the requested 20. No mesh/solve/save; flat JSON keys.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import star.common.*;
import star.meshing.*;
import star.bodyfittedmesher.AdvancingLayerAutoMesher;

public class ProbeMrfAlmControls extends StarMacro {

    private static final String COMPLETE = "MRF_ALM_CONTROLS_PROBE_COMPLETE";

    public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder json = new StringBuilder("{\"schema_version\":1,\"status\":\"ok\",\"probe\":\"alm_controls\"");
        try {
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            int ops = 0;
            for (MeshOperation operation : manager.getObjects()) {
                if (!(operation instanceof AutoMeshOperation)) continue;
                AutoMeshOperation op = (AutoMeshOperation) operation;
                for (MesherBase model : op.getMeshersCollection()) {
                    if (!(model instanceof AdvancingLayerAutoMesher)) continue;
                    AdvancingLayerAutoMesher alm = (AdvancingLayerAutoMesher) model;
                    ops++;
                    String p = "operation_" + ops + "_";
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(p).append("name\":\"").append(op.getPresentationName()).append('"');
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(p).append("is_advancing_layer_mesher\":").append(alm.isAdvancingLayerMesher());
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(p).append("limit_aspect_ratio\":").append(alm.getLimitAspectRatio());
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(p).append("sharp_edge_refinement\":").append(alm.getSharpEdgeRefinement());
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(p).append("boundary_march_angle_deg\":").append(alm.getBoundaryMarchAngle());
                    try {
                        if (json.length() > 0) json.append(',');
                        json.append('"').append(p).append("surface_element_option\":\"")
                            .append(String.valueOf(alm.getSurfaceElementOption())).append('"');
                    } catch (Throwable ignored) { }
                }
            }
            if (json.length() > 0) json.append(',');
            json.append("\"alm_operations\":").append(ops);
        } catch (Throwable failure) {
            json.setLength(0);
            json.append("{\"schema_version\":1,\"status\":\"error\",\"error\":\"")
                .append(String.valueOf(failure).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
        }
        json.append('}');
        try (FileWriter out = new FileWriter(new File("mrf_alm_controls_probe.json"), StandardCharsets.UTF_8)) {
            out.write(json.toString());
        } catch (Throwable ignored) { }
        System.out.println(COMPLETE);
    }
}
