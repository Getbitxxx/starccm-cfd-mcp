package macro;
import star.common.*;
import star.base.report.*;
import star.base.neo.*;
import java.io.*;
import java.util.*;

public class AuditOuterBoundary extends StarMacro {
  public void execute(){
    Simulation s=getActiveSimulation();
    List<ClientServerObject> temporary=new ArrayList<>();
    try(PrintWriter p=new PrintWriter(new File(s.getSessionDirFile(),"outer_boundary_audit.txt"),"UTF-8")){
      Boundary b=s.getRegionManager().getRegion("FLUID").getBoundaryManager().getBoundary("WALL");
      p.println("boundary=FLUID/WALL,type="+b.getBoundaryType().getClass().getSimpleName());
      for(Object c:b.getConditions().getObjects()){
        p.println("condition="+c.getClass().getSimpleName());
        try{p.println("selected="+c.getClass().getMethod("getSelectedElement").invoke(c));}catch(NoSuchMethodException ignored){}
      }
      FieldFunction radius=s.getFieldFunctionManager().getFunction("CFD_Radius");
      FieldFunction z=s.getFieldFunctionManager().getFunction("Position").getComponentFunction(2);
      for(FieldFunction f:new FieldFunction[]{radius,z}){
        MinReport min=(MinReport)s.getReportManager().create("star.base.report.MinReport");temporary.add(min);
        MaxReport max=(MaxReport)s.getReportManager().create("star.base.report.MaxReport");temporary.add(max);
        min.setFieldFunction(f);max.setFieldFunction(f);min.getParts().setObjects(b);max.getParts().setObjects(b);
        p.println(f.getPresentationName()+" min="+min.getReportMonitorValue()+" max="+max.getReportMonitorValue());
      }
      p.println("OUTER_BOUNDARY_AUDIT_COMPLETE");s.println("OUTER_BOUNDARY_AUDIT_COMPLETE");
    }catch(Exception e){throw new RuntimeException(e);}finally{s.deleteObjects(temporary);}
  }
}
