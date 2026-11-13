/* Read-only verification of separate stationary and rotating MRF mesh operations. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.base.neo.ClientServerObject;
import star.common.Simulation;
import star.common.StarMacro;
import star.meshing.AutoMeshOperation;
import star.meshing.BaseSize;
import star.meshing.MeshOperationManager;
import star.meshing.PartsMinimumSurfaceSize;
import star.meshing.PartsTargetSurfaceSize;
import star.meshing.SurfaceCustomMeshControl;
import star.prismmesher.NumPrismLayers;

public class AuditMrfSplitMeshConfiguration extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "mrf_split_mesh_audit.txt"), StandardCharsets.UTF_8)) {
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            if (manager.getObjects().stream().filter(op -> op instanceof AutoMeshOperation).count() != 2) {
                throw new IllegalStateException("Expected two separate MRF auto mesh operations");
            }
            for (String name : new String[] {"AUTO_MRF_STATIONARY_MESH", "AUTO_MRF_ROTATING_MESH"}) {
                AutoMeshOperation operation = (AutoMeshOperation) manager.getObject(name);
                if (operation == null) throw new IllegalStateException("Missing MRF auto mesh operation " + name);
                out.write("OPERATION\t" + name + "\t" + operation.getDefaultValues().get(BaseSize.class).getSIValue() +
                        "\t" + operation.getDefaultValues().get(NumPrismLayers.class).getNumLayers() + "\n");
                for (Object part : operation.getInputGeometryObjects().getObjects()) {
                    out.write("INPUT\t" + name + "\t" + ((ClientServerObject) part).getPresentationName() + "\n");
                }
                for (Object item : operation.getCustomMeshControls().getObjects()) {
                    if (!(item instanceof SurfaceCustomMeshControl)) continue;
                    SurfaceCustomMeshControl control = (SurfaceCustomMeshControl) item;
                    out.write("CONTROL\t" + name + "\t" + control.getPresentationName() + "\t" +
                            control.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().getValue() + "\t" +
                            control.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().getValue() + "\n");
                    for (Object surface : control.getGeometryObjects().getObjects()) {
                        out.write("SURFACE\t" + name + "\t" + control.getPresentationName() + "\t" +
                                ((ClientServerObject) surface).getPresentationName() + "\n");
                    }
                }
            }
            out.write("MRF_SPLIT_MESH_AUDIT_COMPLETE\n");
            System.out.println("MRF_SPLIT_MESH_AUDIT_COMPLETE");
        } catch (Throwable failure) {
            throw new RuntimeException("MRF split mesh audit failed", failure);
        }
    }
}
