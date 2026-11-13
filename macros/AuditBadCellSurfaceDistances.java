/* Diagnostic boundary samples, not exact point-to-CAD distances.
 * No initialization, mesh/geometry edits or save. Temporary reports/functions removed.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.ClientServerObject;
import star.base.report.MinReport;
import star.common.*;

public class AuditBadCellSurfaceDistances extends StarMacro {
    @Override public void execute() {
        Simulation s=getActiveSimulation();List<ClientServerObject> temporary=new ArrayList<>();
        try {
            File dir=s.getSessionDirFile();Properties p=new Properties();
            try(Reader r=new InputStreamReader(new FileInputStream(new File(dir,"local_mesh.properties")),StandardCharsets.UTF_8)){p.load(r);}
            double[] point=new double[3];String[] expressions=new String[3];
            for(int i=0;i<3;i++){
                point[i]=Double.parseDouble(p.getProperty("diagnostic.point_m."+i));
                if(!Double.isFinite(point[i]))throw new IllegalArgumentException("Nonfinite point");
                expressions[i]="pow($$Position["+i+"]-("+point[i]+"),2)";
            }
            FvRepresentation mesh=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            if(mesh==null || mesh.getCellCount()<=0)throw new IllegalStateException("Volume mesh missing");
            UserFieldFunction f=s.getFieldFunctionManager().createFieldFunction();temporary.add(f);
            f.setPresentationName("TEMP_DIAGNOSTIC_POINT_DISTANCE");
            f.setFunctionName("TEMP_DIAGNOSTIC_POINT_DISTANCE");
            f.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
            f.setDimensions(Dimensions.Builder().length(1).build());
            f.setDefinition("sqrt("+String.join("+",expressions)+")");
            try(PrintWriter out=new PrintWriter(new File(dir,"bad_cell_surface_distances.tsv"),StandardCharsets.UTF_8)){
                out.println("POINT_SI\t"+Arrays.toString(point));
                out.println("METHOD\tminimum sampled boundary-position distance; not exact CAD distance");
                for(Region region:s.getRegionManager().getRegions())for(Boundary b:region.getBoundaryManager().getBoundaries()){
                    if(b.getPartSurfaceGroup().getObjects().isEmpty())continue;
                    MinReport report=s.getReportManager().createReport(MinReport.class);temporary.add(report);
                    report.setRepresentation(mesh);report.setFieldFunction(f);report.getParts().setObjects(b);
                    double value=report.getReportMonitorValue();
                    if(!Double.isFinite(value) || value<0)throw new IllegalStateException("Invalid distance sample");
                    for(Object surface:b.getPartSurfaceGroup().getObjects())
                        out.println("SURFACE\t"+((ClientServerObject)surface).getQualifiedName()+"\t"+value);
                }
                out.println("BAD_CELL_SURFACE_DISTANCES_COMPLETE");
            }
            System.out.println("BAD_CELL_SURFACE_DISTANCES_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Surface distance audit failed: "+e.getMessage(),e);}
        finally{Collections.reverse(temporary);for(ClientServerObject x:temporary)s.deleteObjects(Collections.singletonList(x));}
    }
}
