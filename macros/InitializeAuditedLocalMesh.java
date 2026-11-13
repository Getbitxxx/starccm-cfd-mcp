/* Fixed-scope initialization diagnostic: fresh readback and prior quality proof.
 * Uses the existing tutorial physics; no solver iteration or physics change.
 * Raw PrismLayerNumber is diagnostic, not a near-wall accuracy certification.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.ClientServerObject;
import star.base.report.*;
import star.common.*;

public class InitializeAuditedLocalMesh extends StarMacro {
    private static double faces(ElementCountReport r,Boundary b){
        r.getParts().setObjects(b);double count=0;
        for(String key:r.getCountKeys(b))if(key.equalsIgnoreCase("faces"))count+=r.getCount(b,key);
        if(!Double.isFinite(count) || count<0)throw new IllegalStateException("Invalid face count");
        return count;
    }
    @Override public void execute(){
        Simulation s=getActiveSimulation();File dir=s.getSessionDirFile();List<ClientServerObject> temp=new ArrayList<>();
        File output=new File(dir,"local_mesh_initialized.sim");
        try{
            if(output.exists() || s.isInitialized() || s.getSimulationIterator().getCurrentIteration()!=0)
                throw new IllegalStateException("Require fresh uninitialized iteration-zero input");
            if(!Files.readString(new File(dir,"local_mesh_readback.tsv").toPath(),StandardCharsets.UTF_8).contains("LOCAL_MESH_READBACK_COMPLETE"))
                throw new IllegalStateException("Fresh configuration readback missing");
            Properties p=new Properties();try(Reader r=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)){p.load(r);}
            FvRepresentation mesh=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            if(mesh==null || mesh.getCellCount()!=Long.parseLong(p.getProperty("diagnostic.preinit_cells")))
                throw new IllegalStateException("Previously assessed cell count differs");
            if(s.getRegionManager().getRegions().size()!=2 || s.getInterfaceManager().getObjects().size()!=1)
                throw new IllegalStateException("Expected fixed trial interface inventory");
            Set<String> walls=new HashSet<>();
            for(int i=0;i<Integer.parseInt(p.getProperty("surface_count"));i++){
                String role=p.getProperty("surface."+i+".role");
                if(Set.of("blade_wall","hub_wall","stationary_wall").contains(role))walls.add(p.getProperty("surface."+i+".selector"));
            }
            s.getSolution().initializeSolution();
            if(!s.isInitialized() || s.getSimulationIterator().getCurrentIteration()!=0)
                throw new IllegalStateException("Initialization failed or advanced iterations");
            ElementCountReport count=s.getReportManager().createReport(ElementCountReport.class);temp.add(count);count.setRepresentation(mesh);
            FieldFunction prism=null;
            try(PrintWriter out=new PrintWriter(new File(dir,"local_mesh_initialization.tsv"),StandardCharsets.UTF_8)){
                for(FieldFunction f:s.getFieldFunctionManager().getScalarFieldFunctions(mesh)){
                    out.println("AVAILABLE_SCALAR\t"+f.getFunctionName());
                    if("PrismLayerNumber".equals(f.getFunctionName()))prism=f;
                }
                for(Interface inter:s.getInterfaceManager().getObjects()){
                    if(!(inter instanceof BoundaryInterface))throw new IllegalStateException("Unexpected interface type");
                    BoundaryInterface bi=(BoundaryInterface)inter;
                    double a=faces(count,bi.getBoundary0()),b=faces(count,bi.getBoundary1());
                    if(a<=0 || b<=0)throw new IllegalStateException("Interface side has no faces");
                    out.println("INTERFACE_FACES\t"+inter.getQualifiedName()+"\t"+a+"\t"+b);
                }
                Set<String> measured=new HashSet<>();
                for(Region region:s.getRegionManager().getRegions())for(Boundary b:region.getBoundaryManager().getBoundaries())
                    for(Object obj:b.getPartSurfaceGroup().getObjects()){
                        String name=((ClientServerObject)obj).getQualifiedName();if(!walls.contains(name))continue;
                        if(!measured.add(name))throw new IllegalStateException("Duplicate wall mapping");
                        double n=faces(count,b);if(n<=0)throw new IllegalStateException("Physical wall lacks faces");
                        out.println("WALL_FACES\t"+name+"\t"+n);
                        if(prism!=null)for(String statistic:new String[]{"min","max"}){
                            ScalarReport r=s.getReportManager().create("min".equals(statistic)?"star.base.report.MinReport":"star.base.report.MaxReport");temp.add(r);
                            r.setRepresentation(mesh);r.setFieldFunction(prism);r.getParts().setObjects(b);
                            double value=r.getReportMonitorValue();if(!Double.isFinite(value))throw new IllegalStateException("Nonfinite prism diagnostic");
                            out.println("WALL_PRISM_LAYER_NUMBER_RAW\t"+name+"\t"+statistic+"\t"+value);
                        }
                    }
                if(!measured.equals(walls))throw new IllegalStateException("Wall inventory incomplete");
                if(prism==null)out.println("PRISM_LAYER_NUMBER_UNAVAILABLE\tnear-wall verification pending");
                out.println("INITIALIZED\ttrue");out.println("ITERATION\t0");
                out.println("LOCAL_MESH_INITIALIZATION_COMPLETE");
            }
        }catch(Exception e){throw new RuntimeException("Mesh initialization diagnostic failed: "+e.getMessage(),e);}
        finally{Collections.reverse(temp);for(ClientServerObject x:temp)s.deleteObjects(Collections.singletonList(x));}
        s.saveState(output.getAbsolutePath());
        System.out.println("LOCAL_MESH_INITIALIZATION_COMPLETE");
    }
}
