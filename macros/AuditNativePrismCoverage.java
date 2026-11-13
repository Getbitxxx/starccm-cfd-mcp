/* Read-only saved-mesh diagnostic. No initialization, iterations or save.
 * Raw layer number area fractions and first-prism-height (explicit metres).
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.ClientServerObject;
import star.base.report.*;
import star.common.*;

public class AuditNativePrismCoverage extends StarMacro {
    @Override public void execute(){
        Simulation s=getActiveSimulation();List<ClientServerObject> temp=new ArrayList<>();
        try{
            if(s.getSimulationIterator().getCurrentIteration()!=0)
                throw new IllegalStateException("Fixed diagnostic requires an unsolved saved mesh");
            Properties p=new Properties();File dir=s.getSessionDirFile();
            try(Reader r=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)){p.load(r);}
            int layers=Integer.parseInt(p.getProperty("prism_layers"));if(layers<3 || layers>20)throw new IllegalArgumentException("Invalid layers");
            FvRepresentation mesh=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            FieldFunction number=null,height=null;
            for(FieldFunction f:s.getFieldFunctionManager().getScalarFieldFunctions(mesh)){
                if("PrismLayerNumber".equals(f.getFunctionName()))number=f;
                if("FirstPrismLayerHeight".equals(f.getFunctionName()))height=f;
            }
            if(number==null || height==null)throw new IllegalStateException("Required native prism fields unavailable");
            Set<String> walls=new HashSet<>(),measured=new HashSet<>();
            for(int i=0;i<Integer.parseInt(p.getProperty("surface_count"));i++)
                if(Set.of("blade_wall","hub_wall","stationary_wall").contains(p.getProperty("surface."+i+".role")))walls.add(p.getProperty("surface."+i+".selector"));
            try(PrintWriter out=new PrintWriter(new File(dir,"native_prism_coverage.tsv"),StandardCharsets.UTF_8)){
                out.println("IS_INITIALIZED_ON_LOAD\t"+s.isInitialized());
                for(Region region:s.getRegionManager().getRegions())for(Boundary b:region.getBoundaryManager().getBoundaries())for(Object obj:b.getPartSurfaceGroup().getObjects()){
                    String name=((ClientServerObject)obj).getQualifiedName();if(!walls.contains(name))continue;
                    if(!measured.add(name))throw new IllegalStateException("Ambiguous wall mapping");
                    for(String stat:new String[]{"min","max","area_average"}){
                        String cls=stat.equals("min")?"MinReport":stat.equals("max")?"MaxReport":"AreaAverageReport";
                        ScalarReport r=s.getReportManager().create("star.base.report."+cls);temp.add(r);
                        r.setRepresentation(mesh);r.setFieldFunction(height);r.getParts().setObjects(b);r.setUnits(s.getUnitsManager().getObject("m"));
                        double value=r.getReportMonitorValue();if(!Double.isFinite(value) || value<0)throw new IllegalStateException("Invalid height");
                        out.println("FIRST_PRISM_HEIGHT_M\t"+name+"\t"+stat+"\t"+value);
                    }
                    double sum=0;
                    for(int layer=0;layer<=layers;layer++){
                        UserFieldFunction indicator=s.getFieldFunctionManager().createFieldFunction();temp.add(indicator);
                        indicator.setFunctionName("TEMP_PRISM_BIN_"+temp.size());indicator.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
                        indicator.setDefinition("(${PrismLayerNumber} >= "+(layer-.5)+" && ${PrismLayerNumber} < "+(layer+.5)+") ? 1 : 0");
                        AreaAverageReport r=s.getReportManager().createReport(AreaAverageReport.class);temp.add(r);
                        r.setRepresentation(mesh);r.setFieldFunction(indicator);r.getParts().setObjects(b);
                        double fraction=r.getReportMonitorValue();
                        if(!Double.isFinite(fraction) || fraction<0 || fraction>1)throw new IllegalStateException("Invalid area fraction");
                        sum+=fraction;out.println("PRISM_LAYER_AREA_FRACTION\t"+name+"\t"+layer+"\t"+fraction);
                    }
                    if(Math.abs(sum-1)>1e-6)throw new IllegalStateException("Layer bins do not cover wall area");
                    out.println("AREA_FRACTION_SUM\t"+name+"\t"+sum);
                }
                if(!measured.equals(walls))throw new IllegalStateException("Incomplete wall selection");
                out.println("NATIVE_PRISM_COVERAGE_COMPLETE");
            }
            System.out.println("NATIVE_PRISM_COVERAGE_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Native prism coverage failed: "+e.getMessage(),e);}
        finally{Collections.reverse(temp);for(ClientServerObject x:temp)s.deleteObjects(Collections.singletonList(x));}
    }
}
