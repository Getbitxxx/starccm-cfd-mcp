/* Read-only 2502 physical inventory. No save, initialize, model mutation or iteration.
 * Names identify returned objects; no input name selectors or assumed axial direction.
 * Nonconstant profiles remain explicit functions/unsupported methods, never cast to constants.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import star.common.*;
import star.motion.*;

public class AuditPhysicalReadiness extends StarMacro {
    private static String clean(Object value) {
        return String.valueOf(value).replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n");
    }
    private static void row(PrintWriter out, Object... values) {
        for (int i=0;i<values.length;i++) { if(i>0)out.print('\t');out.print(clean(values[i])); }
        out.println();
    }
    @Override public void execute() {
        Simulation s=getActiveSimulation();
        int iteration=s.getSimulationIterator().getCurrentIteration();
        boolean modified=s.isModified();
        try(PrintWriter out=new PrintWriter(new File(s.getSessionDirFile(),"physical_readiness.tsv"),StandardCharsets.UTF_8)) {
            row(out,"VERSION",s.getReleaseNumber());
            row(out,"STATE_BEFORE",iteration,s.isInitialized(),modified);
            for(Object f:s.get(ReferenceFrameManager.class).getObjects()) {
                row(out,"FRAME",f,f.getClass().getName());
                if(f instanceof UserRotatingReferenceFrame) {
                    UserRotatingReferenceFrame r=(UserRotatingReferenceFrame)f;
                    row(out,"ROTATING_FRAME",r.getPresentationName(),
                        java.util.Arrays.toString(r.getAxisVector().getVector().toDoubleArray()),
                        java.util.Arrays.toString(r.getOriginVector().getVector().toDoubleArray()),
                        r.getOriginVector().getUnits(),r.getRotationRate().getSIValue());
                }
            }
            for(Region r:s.getRegionManager().getRegions()) {
                PhysicsContinuum c=r.getPhysicsContinuum();
                row(out,"REGION",r.getPresentationName(),c==null?"NONE":c.getPresentationName());
                if(c!=null)for(Model m:c.getModelManager().getObjects())
                    row(out,"MODEL",r.getPresentationName(),m.getClass().getName());
                MotionSpecification motion=r.getValues().get(MotionSpecification.class);
                row(out,"REGION_MOTION",r.getPresentationName(),motion.getMotion(),
                    motion.getMotion()==null?"NONE":motion.getMotion().getClass().getName(),
                    motion.getReferenceFrame(),motion.getReferenceFrame()==null?"NONE":motion.getReferenceFrame().getClass().getName());
                for(Boundary b:r.getBoundaryManager().getBoundaries()) {
                    row(out,"BOUNDARY",r.getPresentationName(),b.getPresentationName(),b.getBoundaryType().getClass().getName());
                    for(Object cond:b.getConditions().getObjects()) {
                        Object val=cond;
                        if(cond instanceof ReferenceFrameOption)val=((ReferenceFrameOption)cond).getSelectedElement();
                        else if(cond instanceof star.flow.FlowDirectionOption)val=((star.flow.FlowDirectionOption)cond).getSelectedElement();
                        else if(cond instanceof star.flow.InletVelocityOption)val=((star.flow.InletVelocityOption)cond).getSelectedElement();
                        row(out,"CONDITION",r.getPresentationName(),b.getPresentationName(),cond.getClass().getName(),val);
                    }
                    for(Object item:b.getValues().getObjects())if(item instanceof Profile) {
                        Profile p=(Profile)item;ProfileMethod method=p.getMethod();
                        row(out,"PROFILE_METHOD",r.getPresentationName(),b.getPresentationName(),p.getClass().getName(),method==null?"NONE":method.getClass().getName());
                        if(method instanceof ConstantScalarProfileMethod) {
                            ScalarPhysicalQuantity q=((ConstantScalarProfileMethod)method).getQuantity();
                            row(out,"SCALAR",r.getPresentationName(),b.getPresentationName(),p.getClass().getName(),q.getDefinition(),q.getSIValue(),q.getUnits());
                        } else if(method instanceof FunctionScalarProfileMethod) {
                            FieldFunction f=((FunctionScalarProfileMethod)method).getFieldFunction();
                            row(out,"FUNCTION",r.getPresentationName(),b.getPresentationName(),p.getClass().getName(),f.getFunctionName(),f.getClass().getName());
                            if(f instanceof UserFieldFunction)row(out,"FUNCTION_DEFINITION",f.getFunctionName(),((UserFieldFunction)f).getDefinition());
                        }
                    }
                }
            }
            int after=s.getSimulationIterator().getCurrentIteration();
            row(out,"STATE_AFTER",after,s.isInitialized(),s.isModified());
            if(after!=iteration || s.isModified()!=modified)throw new IllegalStateException("Read-only state changed");
            row(out,"PHYSICAL_READINESS_AUDIT_COMPLETE");
            if(out.checkError())throw new IOException("Audit output write failed");
            System.out.println("PHYSICAL_READINESS_AUDIT_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Physical readiness audit failed: "+e.getMessage(),e);}
    }
}
