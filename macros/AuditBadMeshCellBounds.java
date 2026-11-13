/* Read-only mesh diagnostic: count bad cells and bracket centroid bounds.
 * No solution initialization, mesh deletion, geometry change or save.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.vis.ThresholdMode;

public class AuditBadMeshCellBounds extends StarMacro {
    private int count(CellSet cells,FieldFunction quality,double cutoff,FieldFunction axis,int mode,double split) {
        cells.replaceThreshold(quality,ThresholdMode.BELOW_TAG.getValue(),cutoff,cutoff);
        cells.subsetThreshold(axis,mode,split,split);
        return cells.getNumCells();
    }
    @Override public void execute() {
        Simulation s=getActiveSimulation();CellSet cells=null;
        try(PrintWriter out=new PrintWriter(new File(s.getSessionDirFile(),"bad_mesh_cell_bounds.tsv"),StandardCharsets.UTF_8)) {
            FvRepresentation mesh=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            if(mesh==null || mesh.getCellCount()<=0)throw new IllegalStateException("Volume mesh missing");
            double[] lo={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY},hi={Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
            for(Region r:s.getRegionManager().getRegions()) {
                double[] ext=s.getMeshManager().getRegionExtents(r);
                if(ext.length!=6)throw new IllegalStateException("Missing region extents");
                for(int i=0;i<3;i++){lo[i]=Math.min(lo[i],ext[2*i]);hi[i]=Math.max(hi[i],ext[2*i+1]);}
            }
            FieldFunction position=s.getFieldFunctionManager().getFunction("Position");
            if(position==null)throw new IllegalStateException("Position missing");
            cells=mesh.getCellSetManager().createEmptyCellSet();
            for(String metric:List.of("FaceValidity","VolumeChange")) {
                FieldFunction f=null;
                for(FieldFunction candidate:s.getFieldFunctionManager().getScalarFieldFunctions(mesh))
                    if((metric.equals("FaceValidity") && candidate instanceof FaceValidityFunction) ||
                       (metric.equals("VolumeChange") && candidate instanceof VolumeChangeFunction)){f=candidate;break;}
                if(f==null)throw new IllegalStateException("Metric missing: "+metric);
                double cutoff=metric.equals("FaceValidity")?1.0:Math.nextUp(0.01);
                cells.replaceThreshold(f,ThresholdMode.BELOW_TAG.getValue(),cutoff,cutoff);
                int total=cells.getNumCells();out.println(metric+"\tcount\t"+total);
                out.println("ZERO_COUNT\t"+metric+"\tno cell below the cutoff on the mesh under audit");
                for(int axis=0;axis<3;axis++) {
                    FieldFunction xyz=position.getComponentFunction(axis);
                    double a=lo[axis],b=hi[axis];
                    for(int step=0;step<22;step++) {
                        double mid=0.5*(a+b);
                        if(count(cells,f,cutoff,xyz,ThresholdMode.BELOW_TAG.getValue(),mid)>0)b=mid;else a=mid;
                    }
                    out.println(metric+"\tmin_"+axis+"_bracket\t"+a+"\t"+b);
                    a=lo[axis];b=hi[axis];
                    for(int step=0;step<22;step++) {
                        double mid=0.5*(a+b);
                        if(count(cells,f,cutoff,xyz,ThresholdMode.ABOVE_TAG.getValue(),mid)>0)a=mid;else b=mid;
                    }
                    out.println(metric+"\tmax_"+axis+"_bracket\t"+a+"\t"+b);
                    out.flush();
                }
            }
            out.println("BAD_MESH_CELL_BOUNDS_COMPLETE");
            System.out.println("BAD_MESH_CELL_BOUNDS_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Bad mesh bounds failed: "+e.getMessage(),e);}
        finally{if(cells!=null)s.deleteObjects(Collections.singletonList(cells));}
    }
}
