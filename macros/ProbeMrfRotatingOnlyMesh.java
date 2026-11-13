/* Isolated diagnostic: mesh the rotating fluid part alone to localize surface-orientation failure. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.PartSurface;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.AutoMeshOperation;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;
import star.meshing.SurfaceCustomMeshControl;

public class ProbeMrfRotatingOnlyMesh extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        String result;
        try {
            MeshOperationPart rotating = (MeshOperationPart) sim.get(SimulationPartManager.class)
                    .getPart("AUTO_MRF_ROTATING_FLUID");
            AutoMeshOperation operation = (AutoMeshOperation) sim.get(MeshOperationManager.class)
                    .getObject("AUTO_MRF_DIAGNOSTIC_MESH");
            if (rotating == null || operation == null) throw new IllegalStateException("MRF diagnostic inputs missing");
            operation.getInputGeometryObjects().setObjects(rotating);
            SurfaceCustomMeshControl control = (SurfaceCustomMeshControl) operation.getCustomMeshControls()
                    .getObject("AUTO_MRF_INTERFACE_SURFACES");
            if (control == null) throw new IllegalStateException("MRF interface mesh control missing");
            PartSurface up = rotating.getPartSurfaceManager().getPartSurface("AUTO_MRF_ENVELOPE.Cylinder Surface 3");
            PartSurface down = rotating.getPartSurfaceManager().getPartSurface("AUTO_MRF_ENVELOPE.Cylinder Surface 2");
            PartSurface side = rotating.getPartSurfaceManager().getPartSurface("AUTO_MRF_ENVELOPE.Cylinder Surface");
            control.getGeometryObjects().setObjects(up, down, side);
            operation.execute();
            result = "ROTATING_ONLY_MESH_SUCCEEDED";
        } catch (Throwable failure) {
            result = "ROTATING_ONLY_MESH_FAILED\n" + failure.toString();
        }
        try (FileWriter out = new FileWriter(new File(dir, "mrf_rotating_only_mesh_probe.txt"), StandardCharsets.UTF_8)) {
            out.write(result + "\nMRF_ROTATING_ONLY_MESH_PROBE_COMPLETE\n");
        } catch (Exception failure) {
            throw new RuntimeException("Could not save rotating-only mesh probe", failure);
        }
        System.out.println("MRF_ROTATING_ONLY_MESH_PROBE_COMPLETE");
    }
}
