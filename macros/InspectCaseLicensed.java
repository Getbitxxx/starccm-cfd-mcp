package macro;

import star.common.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.lang.reflect.*;
import java.util.*;

/** Read-only inventory. Does not save, mesh, initialize or iterate. */
public class InspectCaseLicensed extends StarMacro {
  private PrintWriter out;
  private final IdentityHashMap<Object,Boolean> seen = new IdentityHashMap<>();
  private static final String[] GETTERS = {
    "getPresentationName", "getBoundaryType", "getRegionType", 
    "getModelManager", "getModels", "getBoundaryManager", "getBoundaries",
    "getValues", "getConditions", "getInitialConditions", "getReferenceValues",
    "getMotionSpecification", "getReferenceFrame", "getRotationRate", "getAxisVector",
    "getOriginVector", "getOrigin", "getDirection", "getQuantity", "getDefinition",
    "getRawValue", "getSIValue", "getValue", "getUnits", 
    "getParts", "getInputParts", "getPartSurfaceManager", "getPartSurfaces",
     "getSelected", "getMethod", "getMagnitude", "getVector",
    "getDefaultValues", "getCustomMeshControls", "getCustomValues", "getCustomConditions",
    "getMeshers", "getMaximumNumberStepsObject", "getIsUsed", "getLogicalOption",
    "getCriterionOption", "getNumberOfSamples", "getTolerance", "getLimit",
    "getMaximumTime", "getTimeStep", "getProfileMethod", "getFieldFunction", "getCurrentIteration", "getCurrentTimeStep", "getIteration", "getPhysicalTime", "getCellCount", "getNumberOfCells", "getInterfaceType", "getTopology", "getPeriodicTransform", "getRotationAngle", "getReport", "getPlotLimit", "getIterationFrequency", "getMaximumWidth", "getMinimumWidth"
  };
  public void execute() {
    Simulation s = getActiveSimulation();
    File dest = new File(s.getSessionDirFile(), "case_inventory_licensed.txt");
    try {
      out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(dest), StandardCharsets.UTF_8));
      out.println("READ-ONLY CASE INVENTORY / " + new Date());
      out.println("Simulation: " + s.getPresentationName());
      String[] managers = {"getRegionManager", "getContinuumManager", "getInterfaceManager",
        "getReportManager", "getMonitorManager", "getSolverManager", "getSolverStoppingCriterionManager",
        "getSimulationIterator", "getSolution", "getRepresentationManager"};
      for (String name : managers) section(s, name);
      String[] classes = {"star.common.SimulationPartManager", "star.common.GlobalParameterManager",
        "star.motion.ReferenceFrameManager", "star.meshing.MeshOperationManager"};
      for (String name : classes) {
        out.println("\n=== " + name + " ==="); seen.clear();
        try {
          Class<?> cls = Class.forName(name);
          Object manager = s.getClass().getMethod("get", Class.class).invoke(s, cls);
          dump(manager, "", 0);
        } catch (Exception e) { out.println("UNAVAILABLE: " + e.toString()); }
      }
      out.println("\nINVENTORY_COMPLETE");
      out.close();
      s.println("INVENTORY_COMPLETE: " + dest.getAbsolutePath());
    } catch (Exception e) { if(out != null) out.close(); throw new RuntimeException(e); }
  }
  private void section(Object owner, String name) {
    out.println("\n=== " + name + " ==="); seen.clear();
    try { dump(owner.getClass().getMethod(name).invoke(owner), "", 0); }
    catch (Exception e) { out.println("UNAVAILABLE: " + e.toString()); }
  }
  private String label(Object x) {
    if(x == null) return "null";
    try { return x.getClass().getSimpleName() + " [" + x.getClass().getMethod("getPresentationName").invoke(x) + "]"; }
    catch(Exception e) { return x.getClass().getSimpleName(); }
  }
  private boolean scalar(Object x) {
    return x instanceof String || x instanceof Number || x instanceof Boolean || x instanceof Enum;
  }
  private void dump(Object x, String indent, int depth) {
    if(x == null || scalar(x)) { out.println(indent + String.valueOf(x)); return; }
    if(x instanceof Collection) {
      int i=0; for(Object v:(Collection<?>)x) { if(i++>=200) {out.println(indent+"TRUNCATED after 200");break;} dump(v,indent,depth); } return;
    }
    if(x.getClass().isArray()) {
      int n=Array.getLength(x); for(int i=0;i<Math.min(n,200);i++) dump(Array.get(x,i),indent,depth); return;
    }
    out.println(indent + label(x));
    if(depth>=8 || seen.put(x,Boolean.TRUE)!=null) return;
    // Generic managers expose getObjects(); only a no-argument getter is invoked.
    try {
      Method m=x.getClass().getMethod("getObjects");
      Object children=m.invoke(x);
      out.println(indent+"  getObjects:"); dump(children,indent+"    ",depth+1);
    } catch(NoSuchMethodException e) {} catch(Exception e) {out.println(indent+"  getObjects ERROR: "+e);}
    for(String name:GETTERS) {
      try {
        Method m=x.getClass().getMethod(name);
        Object v=m.invoke(x);
        out.println(indent+"  "+name+":"); dump(v,indent+"    ",depth+1);
      } catch(NoSuchMethodException e) {} catch(Exception e) {out.println(indent+"  "+name+" ERROR: "+e);}
    }
  }
}
