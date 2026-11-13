/* Isolated import-only candidate: no mesh generation, physics, initialize or solve. */
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;
import java.util.regex.Matcher;
import java.util.function.Supplier;
import java.io.PrintWriter;
import java.io.StringWriter;
import star.common.*;

public class ProbeNaca0012Native2D extends StarMacro {
    private String currentPhase="NOT_STARTED";
    private void phase(Simulation s,File d,String name) {
        currentPhase=name;s.println("NACA_NATIVE_PHASE="+name);
        try {Files.writeString(new File(d,"current_phase.txt").toPath(),name+"\n",StandardCharsets.UTF_8);
            Files.writeString(new File(d,"phase_history.txt").toPath(),name+"\n",StandardCharsets.UTF_8,java.nio.file.StandardOpenOption.CREATE,java.nio.file.StandardOpenOption.APPEND);
        }catch(Exception e){throw new RuntimeException("diagnostic phase persistence failed",e);}
    }
    private <T> T probe(Simulation s,File d,String name,Supplier<T> call) {
        phase(s,d,name+".BEFORE");T value=call.get();phase(s,d,name+".AFTER");return value;
    }
    private void oneKey(String s,String k) {
        Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:").matcher(s);int n=0;while(m.find())n++;
        if(n!=1)throw new IllegalArgumentException("Missing/duplicate key "+k);
    }
    private String field(String s,String k) {
        oneKey(s,k);
        Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(s);
        if(!m.find())throw new IllegalArgumentException("Missing "+k);
        String value=m.group(1);if(m.find())throw new IllegalArgumentException("Duplicate "+k);return value;
    }
    private long count(String s,String k) {
        oneKey(s,k);
        Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*([1-9][0-9]*)(?=\\s*[,}])").matcher(s);
        if(!m.find())throw new IllegalArgumentException("Missing positive integer "+k);
        long value=Long.parseLong(m.group(1));if(m.find())throw new IllegalArgumentException("Duplicate "+k);return value;
    }
    private String hash(File f)throws Exception {
        byte[] b=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(f.toPath()));
        StringBuilder r=new StringBuilder();for(byte v:b)r.append(String.format("%02x",v&255));return r.toString();
    }
    @Override public void execute() {
        Simulation s=getActiveSimulation();File d=s.getSessionDirFile();
        try {
            Files.writeString(new File(d,"naca0012_native_status.json").toPath(),"{\"status\":\"IMPORT_NOT_VERIFIED\",\"native2D_verified\":false,\"topology_counts_verified\":false,\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
            File mesh=new File(d,"naca0012-native.msh");
            String input=Files.readString(new File(d,"naca0012-native-input.json").toPath(),StandardCharsets.UTF_8);
            String expected=field(input,"mesh_sha256");
            long expectedCells=count(input,"expected_cells"),expectedVertices=count(input,"expected_vertices"),expectedInterior=count(input,"expected_interior_faces");
            long expectedAirfoil=count(input,"expected_airfoil_faces"),expectedFarfield=count(input,"expected_farfield_faces");
            if(!expected.matches("[a-f0-9]{64}")||!mesh.isFile()||!hash(mesh).equals(expected))throw new IllegalArgumentException("mesh hash mismatch");
            String text=Files.readString(mesh.toPath(),StandardCharsets.US_ASCII);
            if(!text.contains("(2 2)")||!text.contains("pressure-far-field farfield")||!text.contains("wall airfoil"))throw new IllegalArgumentException("not named native2D Fluent candidate");
            if(!probe(s,d,"REGION_MANAGER_GET_REGIONS",()->s.getRegionManager().getRegions()).isEmpty())throw new IllegalArgumentException("requires empty isolated session");
            phase(s,d,"IMPORT_MESH.BEFORE");
            s.getImportManager().importMeshFiles(mesh.getAbsolutePath());
            phase(s,d,"IMPORT_MESH.AFTER");
            FvRepresentation v=(FvRepresentation)probe(s,d,"REPRESENTATION_GET_VOLUME_MESH",()->s.getRepresentationManager().getObject("Volume Mesh"));
            if(v==null||probe(s,d,"V_GLOBAL_CELL_COUNT",()->v.getCellCount())<=0||probe(s,d,"REGION_MANAGER_GET_REGIONS",()->s.getRegionManager().getRegions()).size()!=1)throw new IllegalStateException("one meshed region required");
            if(probe(s,d,"V_IS3D",()->v.is3D())||probe(s,d,"V_ANY3D",()->v.isAtLeastOne3D())||!probe(s,d,"V_ANY2D",()->v.isAtLeastOne2D()))throw new IllegalStateException("imported mesh is not exclusively native2D");
            if(probe(s,d,"V_GLOBAL_CELL_COUNT",()->v.getCellCount())!=expectedCells||probe(s,d,"V_GLOBAL_VERTEX_COUNT",()->v.getVertexCount())!=expectedVertices||probe(s,d,"V_GLOBAL_INTERIOR_FACE_COUNT",()->v.getInteriorFaceCount())!=expectedInterior)throw new IllegalStateException("imported cell/vertex/interior-face counts differ from pinned converter topology");
            StringBuilder out=new StringBuilder("mesh_sha256="+expected+"\ncells="+probe(s,d,"V_GLOBAL_CELL_COUNT",()->v.getCellCount())+"\nvertices="+probe(s,d,"V_GLOBAL_VERTEX_COUNT",()->v.getVertexCount())+"\ninterior_faces="+probe(s,d,"V_GLOBAL_INTERIOR_FACE_COUNT",()->v.getInteriorFaceCount())+"\n");
            List<Region> regions=new ArrayList<>();
            for(Region r:probe(s,d,"REGION_MANAGER_GET_REGIONS",()->s.getRegionManager().getRegions())) {
                regions.add(r);
                if(!probe(s,d,"V_DESCRIBES_REGION",()->v.describesRegion(r))||probe(s,d,"V_REGION_IS3D",()->v.is3D(r))||probe(s,d,"REPRESENTATION_REGION_IS3D",()->s.getRepresentationManager().is3D(r)))throw new IllegalStateException("region lacks native2D volume representation");
                FvRegion fv=probe(s,d,"FV_MANAGER_GET_REGION",()->probe(s,d,"GET_FV_REGION_MANAGER",()->v.getFvRegionManager()).getFvRegion(r));
                if(fv==null||probe(s,d,"FV_REGION_IS3D",()->fv.is3D())||probe(s,d,"FV_REGION_CELL_COUNT",()->fv.getCellCount())!=expectedCells||probe(s,d,"FV_REGION_VERTEX_COUNT",()->fv.getVertexCount())!=expectedVertices||probe(s,d,"FV_REGION_INTERIOR_FACE_COUNT",()->fv.getInteriorFaceCount())!=expectedInterior)throw new IllegalStateException("native2D region topology counts mismatch");
                if(probe(s,d,"BOUNDARY_MANAGER_GET_BOUNDARIES",()->r.getBoundaryManager().getBoundaries()).size()!=2)throw new IllegalStateException("exactly airfoil and farfield required; extra wake/symmetry rejected");
                Boundary airfoil=probe(s,d,"BOUNDARY_GET_AIRFOIL",()->r.getBoundaryManager().getBoundary("airfoil")),farfield=probe(s,d,"BOUNDARY_GET_FARFIELD",()->r.getBoundaryManager().getBoundary("farfield"));
                if(airfoil==null||farfield==null)throw new IllegalStateException("boundary identities changed");
                if(!probe(s,d,"AIRFOIL_GET_TYPE",()->airfoil.getBoundaryType()).getClass().getName().equals("star.common.WallBoundary"))throw new IllegalStateException("airfoil not imported as wall");
                if(probe(s,d,"FARFIELD_GET_TYPE",()->farfield.getBoundaryType()).getClass().getName().equals("star.common.WallBoundary"))throw new IllegalStateException("farfield imported as solid wall");
                out.append("region="+probe(s,d,"REGION_GET_NAME",()->r.getPresentationName())+"\n");
                out.append("volume_describes_region="+probe(s,d,"V_DESCRIBES_REGION",()->v.describesRegion(r))+"\n");
                out.append("region_topology="+probe(s,d,"REGION_GET_TOPOLOGY",()->r.getTopologyEnum())+"\n");
                for(Boundary b:probe(s,d,"BOUNDARY_MANAGER_GET_BOUNDARIES",()->r.getBoundaryManager().getBoundaries())) {
                    FvBoundary fb=probe(s,d,"FV_BOUNDARY_MANAGER_GET_BOUNDARY",()->probe(s,d,"GET_FV_BOUNDARIES",()->fv.getFvBoundaries()).getFvBoundary(b));
                    long expectedFaces=b==airfoil?expectedAirfoil:expectedFarfield;
                    if(fb==null||probe(s,d,"FV_BOUNDARY_FACE_COUNT",()->fb.getFaceCount())!=expectedFaces||!probe(s,d,"BOUNDARY_DEPENDENT_INTERFACES",()->b.getDependentInterfaces()).isEmpty())throw new IllegalStateException("boundary face count changed or unexpected interface");
                    out.append("boundary="+probe(s,d,"BOUNDARY_GET_NAME",()->b.getPresentationName())+" type="+probe(s,d,"BOUNDARY_GET_TYPE",()->b.getBoundaryType()).getClass().getName()+" topology="+probe(s,d,"BOUNDARY_GET_TOPOLOGY",()->b.getTopology())+"\n");
                    out.append("boundary_faces="+probe(s,d,"FV_BOUNDARY_FACE_COUNT",()->fb.getFaceCount())+" expected="+expectedFaces+" original_faces="+probe(s,d,"FV_BOUNDARY_ORIGINAL_FACE_COUNT",()->fb.getOriginalFaceCount())+"\n");
                }
            }
            out.append("optional_mesh_report_unavailable_for_this_input=true\nOPTIONAL_BOUNDARY_REPORT_FAILED_FOR_THIS_INPUT=true\nOPTIONAL_GLOBAL_REPORT_NOT_EXECUTED=true\n");
            out.append("IMPORT_ONLY_COMPLETE\nNATIVE_2D_VERIFIED=true\nGLOBAL_TOPOLOGY_COUNTS_VERIFIED=true\nBOUNDARY_LOCAL_COUNTS_VERIFIED=true\nQUALITY_ACCEPTED=false\nSCIENCE_ACCEPTED=false\n");
            Files.writeString(new File(d,"naca0012_native_import.txt").toPath(),out.toString(),StandardCharsets.UTF_8);
            phase(s,d,"SAVE_STATE.BEFORE");
            s.saveState(new File(d,"naca0012_native_import_only.sim").getAbsolutePath());
            phase(s,d,"SAVE_STATE.AFTER");
            Files.writeString(new File(d,"naca0012_native_status.json").toPath(),"{\"status\":\"ok\",\"verification_scope\":\"IMPORT_TOPOLOGY_VERIFIED_ONLY\",\"optional_mesh_report_unavailable_for_this_input\":true,\"optional_global_mesh_report_executed\":false,\"mesh_sha256\":\""+expected+"\",\"native2D_verified\":true,\"topology_counts_verified\":true,\"boundary_local_counts_verified\":true,\"cells\":"+probe(s,d,"V_GLOBAL_CELL_COUNT",()->v.getCellCount())+",\"vertices\":"+probe(s,d,"V_GLOBAL_VERTEX_COUNT",()->v.getVertexCount())+",\"interior_faces\":"+probe(s,d,"V_GLOBAL_INTERIOR_FACE_COUNT",()->v.getInteriorFaceCount())+",\"airfoil_faces\":"+expectedAirfoil+",\"farfield_faces\":"+expectedFarfield+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
            s.println("IMPORT_ONLY_COMPLETE; native2D/global/localcounts checked; quality/science still required");
        }catch(Exception e){
            s.println("NACA_NATIVE_FAILED_PHASE="+currentPhase);
            StringWriter trace=new StringWriter();e.printStackTrace(new PrintWriter(trace));s.println(trace.toString());
            try {Files.writeString(new File(d,"failure_stacktrace.txt").toPath(),"phase="+currentPhase+"\n"+trace.toString(),StandardCharsets.UTF_8);}catch(Exception ignored){}
            try {Files.writeString(new File(d,"naca0012_native_status.json").toPath(),"{\"status\":\"IMPORT_REJECTED\",\"native2D_verified\":false,\"topology_counts_verified\":false,\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);}catch(Exception ignored){}
            throw new RuntimeException("NACA0012 native2D import-only probe failed",e);
        }
    }
}
