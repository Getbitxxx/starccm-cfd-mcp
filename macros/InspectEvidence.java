package macro;
import star.common.*;
import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class InspectEvidence extends StarMacro {
  private PrintWriter out;
  public void execute() {
    Simulation s=getActiveSimulation();
    try {
      out=new PrintWriter(new OutputStreamWriter(new FileOutputStream(new File(s.getSessionDirFile(),"case_evidence.txt")),StandardCharsets.UTF_8));
      out.println("Inspection only: no meshing, initialization or solver iteration requested.");
      out.println("Current iteration: "+s.getSimulationIterator().getCurrentIteration());
      for(Object r:s.getReportManager().getObjects()) {
        out.println("REPORT "+label(r));
        read(r,"getReportMonitorValue");
        read(r,"getDefinition");
        read(r,"getParts");
        read(r,"getDirection");
        read(r,"getOrigin");
      }
      for(Object m:s.getMonitorManager().getObjects()) {
        out.println("MONITOR "+label(m));
        for(String n:new String[]{"getAllXValues","getAllYValues","getXValues","getYValues","getSampleCount"}) read(m,n);
        for(Method method:m.getClass().getMethods()) if(method.getParameterCount()==0 && method.getName().matches("get.*(Samples|Values|Count|Iteration).*")) out.println("AVAILABLE "+method.getName());
      }
      for(Object r:s.getInterfaceManager().getObjects()) {
        out.println("INTERFACE "+label(r));
        for(String n:new String[]{"getTopology","getPeriodicTransform","getRotationAngle","getInterfaceType"}) read(r,n);
      }
      out.println("EVIDENCE_COMPLETE"); out.close(); s.println("EVIDENCE_COMPLETE");
    }catch(Exception e){if(out!=null)out.close();throw new RuntimeException(e);}
  }
  private String label(Object o) throws Exception {return o.getClass().getSimpleName()+" "+o.getClass().getMethod("getPresentationName").invoke(o);}
  private void read(Object o,String name) {
    try {
      Object v=o.getClass().getMethod(name).invoke(o);
      if(v!=null && v.getClass().isArray()) {
        int n=Array.getLength(v);out.println(name+" count="+n);
        if(n>0){out.println("first="+Array.get(v,0)+" last="+Array.get(v,n-1));
          int from=Math.max(0,n-100); double sum=0,min=Double.POSITIVE_INFINITY,max=Double.NEGATIVE_INFINITY;
          for(int i=from;i<n;i++){double x=((Number)Array.get(v,i)).doubleValue();sum+=x;min=Math.min(min,x);max=Math.max(max,x);}
          out.println("last100_count="+(n-from)+",mean="+(sum/(n-from))+",min="+min+",max="+max);
          out.print("tail=");for(int i=Math.max(0,n-10);i<n;i++)out.print(Array.get(v,i)+",");out.println();}
      }else {out.println(name+"="+v);
        if(v!=null)try{out.println("selected="+v.getClass().getMethod("getSelectedElement").invoke(v));}catch(Exception ignored){}
        if(v!=null)try{
          Object selected=v.getClass().getMethod("getObjects").invoke(v);
          if(selected instanceof Collection<?>) for(Object item:(Collection<?>)selected) out.println("object="+label(item));
        }catch(Exception ignored){}
      }
    }catch(NoSuchMethodException ignored){}catch(Exception e){out.println(name+" ERROR "+e);}
  }
}
