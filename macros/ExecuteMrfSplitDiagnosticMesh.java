/* Mesh rotating and stationary MRF parts separately, retaining an intermediate checkpoint. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.meshing.AutoMeshOperation;
import star.meshing.MeshOperationManager;

public class ExecuteMrfSplitDiagnosticMesh extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            AutoMeshOperation rotatingMesh = (AutoMeshOperation) sim.get(MeshOperationManager.class)
                    .getObject("AUTO_MRF_ROTATING_MESH");
            AutoMeshOperation stationaryMesh = (AutoMeshOperation) sim.get(MeshOperationManager.class)
                    .getObject("AUTO_MRF_STATIONARY_MESH");
            Region rotating = sim.getRegionManager().getRegion("AUTO_MRF_ROTATING");
            Region stationary = sim.getRegionManager().getRegion("AUTO_MRF_STATIONARY");
            if (rotatingMesh == null || stationaryMesh == null || rotating == null || stationary == null ||
                    sim.getInterfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Audited split MRF meshing prerequisites missing");
            }
            rotatingMesh.execute();
            FvRepresentation volumeMesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (volumeMesh == null || !volumeMesh.describesRegion(rotating) || volumeMesh.getCellCount() <= 0) {
                throw new IllegalStateException("Rotating MRF volume mesh missing after first operation");
            }
            long rotatingCheckpointCells = volumeMesh.getCellCount();
            sim.saveState(new File(dir, "mrf_rotating_mesh_checkpoint.sim").getAbsolutePath());
            stationaryMesh.execute();
            volumeMesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (volumeMesh == null || !volumeMesh.describesRegion(rotating) ||
                    !volumeMesh.describesRegion(stationary) || volumeMesh.getCellCount() <= rotatingCheckpointCells) {
                throw new IllegalStateException("Split mesh does not cover both MRF Regions");
            }
            sim.saveState(new File(dir, "mrf_split_diagnostic_meshed.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_split_diagnostic_mesh.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"operations\":2," +
                        "\"regions\":2,\"interfaces\":3,\"rotating_checkpoint_cells\":" + rotatingCheckpointCells +
                        ",\"volume_cells\":" + volumeMesh.getCellCount() +
                        ",\"complete_marker\":\"MRF_SPLIT_DIAGNOSTIC_MESH_GENERATED\"}\n");
            }
            System.out.println("MRF_SPLIT_DIAGNOSTIC_MESH_GENERATED");
        } catch (Throwable failure) {
            try (PrintWriter out = new PrintWriter(new FileWriter(new File(dir, "mrf_split_diagnostic_mesh_failure.txt"), StandardCharsets.UTF_8))) {
                failure.printStackTrace(out);
            } catch (Throwable ignored) { }
            throw new RuntimeException("Split MRF diagnostic mesh failed", failure);
        }
    }
}
