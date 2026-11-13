/* Read-only independent audit of the saved MRF mesh operation. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.base.neo.ClientServerObject;
import star.common.PartSurface;
import star.common.Simulation;
import star.common.StarMacro;
import star.meshing.AutoMeshOperation;
import star.meshing.BaseSize;
import star.meshing.MeshOperationManager;
import star.meshing.PartsMinimumSurfaceSize;
import star.meshing.PartsTargetSurfaceSize;
import star.meshing.SurfaceCustomMeshControl;
import star.prismmesher.NumPrismLayers;

public class AuditMrfMeshConfiguration extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "mrf_mesh_audit.txt"), StandardCharsets.UTF_8)) {
            AutoMeshOperation operation = (AutoMeshOperation) sim.get(MeshOperationManager.class).getObject("AUTO_MRF_DIAGNOSTIC_MESH");
            if (operation == null) throw new IllegalStateException("MRF auto mesh operation missing");
            out.write("OPERATION\t" + operation.getPresentationName() + "\n");
            for (Object item : operation.getInputGeometryObjects().getObjects()) {
                out.write("INPUT\t" + ((ClientServerObject) item).getPresentationName() + "\n");
            }
            out.write("BASE_SIZE_SI\t" + operation.getDefaultValues().get(BaseSize.class).getSIValue() + "\n");
            out.write("PRISM_LAYERS\t" + operation.getDefaultValues().get(NumPrismLayers.class).getNumLayers() + "\n");
            for (Object item : operation.getCustomMeshControls().getObjects()) {
                if (!(item instanceof SurfaceCustomMeshControl)) continue;
                SurfaceCustomMeshControl control = (SurfaceCustomMeshControl) item;
                out.write("CONTROL\t" + control.getPresentationName() + "\t" +
                        control.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().getValue() + "\t" +
                        control.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().getValue() + "\n");
                for (Object selected : control.getGeometryObjects().getObjects()) {
                    out.write("SURFACE\t" + control.getPresentationName() + "\t" +
                            ((PartSurface) selected).getPresentationName() + "\n");
                }
            }
            out.write("MRF_MESH_AUDIT_COMPLETE\n");
            System.out.println("MRF_MESH_AUDIT_COMPLETE");
        } catch (Throwable failure) {
            throw new RuntimeException("MRF mesh audit failed", failure);
        }
    }
}
