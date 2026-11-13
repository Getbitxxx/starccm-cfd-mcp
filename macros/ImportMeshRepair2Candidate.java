/* New independent extrusion import only, not a native2D repair or equivalence proof. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import star.common.*;
public class ImportMeshRepair2Candidate extends StarMacro {
    private static final int SCIENTIFIC_MESH_REPAIR_INDEX=2,MAX_SCIENTIFIC_MESH_REPAIRS=2;
    private static final String SPEC_SHA256="1e63f1b1b3ced209278c75f8532799f9d2c6cc33d47bf063ce361099e07ad407"; // Exact reviewed last-repair automatic empty-container import Spec.
    private static final String MESH_SHA256="f7ecd4f5460da94e7042d125c6b1b962692c30879f83445ac6146bb46b993b2b";
    private static final String AUDIT_SHA256="12e5e079d15af336dd74a9ca5d2627f0852dd2cda58c5f744aa6b2dc4dbef838";
    private static final String XY_SOURCE_SHA256="c96baba9d129fee7d6bded2853caf713042e16d0f4b687248b78a0a228a668dc";
    private String phase="NOT_STARTED";
    private static String q(String x){if(x==null)return "null";StringBuilder b=new StringBuilder("\"");for(char c:x.toCharArray())switch(c){case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
    private static String hash(File f)throws Exception{byte[] x=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(f.toPath()));StringBuilder b=new StringBuilder();for(byte z:x)b.append(String.format("%02x",z&255));return b.toString();}
    private static void pin(File f,String expected)throws Exception{if(!expected.matches("[a-f0-9]{64}")||!f.isFile()||!hash(f).equals(expected))throw new IllegalStateException("immutable input mismatch "+f.getName());}
    private void at(Simulation s,File d,String x)throws Exception{phase=x;s.println("EXTRUDED3D_PHASE="+x);Files.writeString(new File(d,"extruded3d_current_phase.txt").toPath(),x+"\n",StandardCharsets.UTF_8);}
    private static String stack(Exception e){StringWriter w=new StringWriter();e.printStackTrace(new PrintWriter(w));return w.toString();}
    private static String continuumInventory(Simulation s){StringBuilder b=new StringBuilder("[");for(Continuum c:s.getContinuumManager().getObjects()){if(b.length()>1)b.append(',');b.append("{\"class\":").append(q(c.getClass().getName())).append(",\"name\":").append(q(c.getPresentationName())).append(",\"physics_models\":[");boolean first=true;if(c instanceof PhysicsContinuum)for(Model m:((PhysicsContinuum)c).getModelManager().getObjects()){if(!first)b.append(',');first=false;b.append(q(m.getClass().getName()));}b.append("]}");}return b.append(']').toString();}
    private static String regionPhysicsInventory(Simulation s){StringBuilder b=new StringBuilder("[");for(Region r:s.getRegionManager().getRegions()){if(b.length()>1)b.append(',');PhysicsContinuum c=r.getPhysicsContinuum();b.append("{\"region\":").append(q(r.getPresentationName())).append(",\"assigned_class\":").append(c==null?"null":q(c.getClass().getName())).append(",\"assigned_name\":").append(c==null?"null":q(c.getPresentationName())).append(",\"assigned_model_count\":").append(c==null?"null":Integer.toString(c.getModelManager().getObjects().size())).append('}');}return b.append(']').toString();}
    private static void persistState(Simulation s,File d,FvRepresentation v,String phase,String boundaries)throws Exception{
        String json="{\"status\":\"DIAGNOSTIC_NOT_VERIFIED\",\"phase\":"+q(phase)+",\"continua\":"+continuumInventory(s)+",\"region_physics_assignments\":"+regionPhysicsInventory(s)+",\"initialized\":"+s.getSolution().isInitialized()+",\"iteration\":"+s.getSimulationIterator().getCurrentIteration()+",\"regions\":"+s.getRegionManager().getRegions().size()+",\"cells\":"+(v==null?"null":Long.toString(v.getCellCount()))+",\"vertices\":"+(v==null?"null":Long.toString(v.getVertexCount()))+",\"interior_faces\":"+(v==null?"null":Long.toString(v.getInteriorFaceCount()))+",\"boundaries\":"+boundaries+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}";
        Files.writeString(new File(d,"extruded3d-state-"+phase+".json").toPath(),json,StandardCharsets.UTF_8);
    }
    @Override public void execute(){if(SCIENTIFIC_MESH_REPAIR_INDEX!=2||SCIENTIFIC_MESH_REPAIR_INDEX>MAX_SCIENTIFIC_MESH_REPAIRS)throw new IllegalStateException("scientific mesh repair limit");Simulation s=getActiveSimulation();File d=s.getSessionDirFile(),record=new File(d,"mesh_repair2_import.json");
        try{
            at(s,d,"IMMUTABLE_INPUTS");
            pin(new File(d,"case_spec.json"),SPEC_SHA256);pin(new File(d,"naca0012-extrusion-audit.json"),AUDIT_SHA256);pin(new File(d,"naca0012-xy-source.p2dfmt.gz"),XY_SOURCE_SHA256);
            File mesh=new File(d,"naca0012-extruded3d.msh");pin(mesh,MESH_SHA256);
            // Fixed audit specifies exactly 3 layers, 4 z planes, 0.1 m span, unchanged XY.
            // Fixed bytes, not caller-origin booleans, govern this single candidate.
            if(!s.getRegionManager().getRegions().isEmpty()||!s.getContinuumManager().getObjects().isEmpty()||s.getSolution().isInitialized()||s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("NEW empty no-physics uninitialized session required");
            at(s,d,"IMPORT_MESH_BEFORE");s.getImportManager().importMeshFiles(mesh.getAbsolutePath());at(s,d,"IMPORT_MESH_AFTER");
            FvRepresentation v=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            persistState(s,d,v,"after-import","[]");
            at(s,d,"NATIVE3D_GLOBAL_READBACK");
            if(v==null||!v.is3D()||!v.isAtLeastOne3D()||v.isAtLeastOne2D()||s.getRegionManager().getRegions().size()!=1)throw new IllegalStateException("exclusively native3D one region required");
            if(v.getCellCount()!=10752||v.getVertexCount()!=14816||v.getInteriorFaceCount()!=28312)throw new IllegalStateException("global counts differ from fixed extrusion audit");
            Region r=s.getRegionManager().getRegions().iterator().next();
            if(!v.describesRegion(r)||!v.is3D(r)||!s.getRepresentationManager().is3D(r))throw new IllegalStateException("region is not native3D");
            at(s,d,"FV_REGION_READBACK");FvRegion fv=v.getFvRegionManager().getFvRegion(r);
            if(fv==null||!fv.is3D()||fv.getCellCount()!=10752||fv.getVertexCount()!=14816||fv.getInteriorFaceCount()!=28312)throw new IllegalStateException("local native3D counts mismatch");
            if(r.getBoundaryManager().getBoundaries().size()!=4)throw new IllegalStateException("exact four boundaries required; no wake boundary");
            String[] names={"airfoil","farfield","symmetry_zmin","symmetry_zmax"};long[] counts={192,528,3584,3584};StringBuilder inventory=new StringBuilder("[");long boundaryFaces=0;
            for(int i=0;i<names.length;i++){
                at(s,d,"BOUNDARY_"+names[i]+"_READBACK");Boundary b=r.getBoundaryManager().getBoundary(names[i]);
                if(b==null||!names[i].equals(b.getPresentationName())||!b.getDependentInterfaces().isEmpty())throw new IllegalStateException("boundary identity/interface mismatch "+names[i]);
                FvBoundary fb=fv.getFvBoundaries().getFvBoundary(b);if(fb==null||fb.getFaceCount()!=counts[i])throw new IllegalStateException("boundary faces mismatch "+names[i]);
                String type=b.getBoundaryType().getClass().getName();
                if(i==0&&!type.equals("star.common.WallBoundary"))throw new IllegalStateException("airfoil must import as wall");
                if(i==1&&type.equals("star.common.WallBoundary"))throw new IllegalStateException("farfield cannot import as wall");
                if(i>=2&&!type.equals("star.common.SymmetryBoundary"))throw new IllegalStateException("cap must import as true symmetry, not wall/outlet");
                boundaryFaces+=fb.getFaceCount();if(i>0)inventory.append(',');inventory.append("{\"name\":").append(q(names[i])).append(",\"actual_type\":").append(q(type)).append(",\"faces\":").append(fb.getFaceCount()).append(",\"topology\":").append(q(String.valueOf(b.getTopology()))).append('}');
            }inventory.append(']');
            persistState(s,d,v,"after-boundary-readback",inventory.toString());
            at(s,d,"TOTAL_FACE_GUARD");if(boundaryFaces+v.getInteriorFaceCount()!=36200)throw new IllegalStateException("total faces mismatch");
            at(s,d,"POST_IMPORT_CONTINUUM_GUARD");
            if(s.getContinuumManager().getObjects().size()!=1)throw new IllegalStateException("exactly one observed automatic empty PhysicsContinuum container required");
            Continuum container=s.getContinuumManager().getObjects().iterator().next();
            if(!container.getClass().getName().equals("star.common.PhysicsContinuum")||!(container instanceof PhysicsContinuum))throw new IllegalStateException("unknown continuum type, exact PhysicsContinuum metadata container required");
            PhysicsContinuum emptyContainer=(PhysicsContinuum)container;
            if(!emptyContainer.getModelManager().getObjects().isEmpty())throw new IllegalStateException("active physics models forbidden for import-only");
            at(s,d,"POST_IMPORT_REGION_PHYSICS_GUARD");PhysicsContinuum assigned=r.getPhysicsContinuum();if(assigned!=null&&!assigned.equals(emptyContainer))throw new IllegalStateException("region assigned to unreviewed physics container");
            at(s,d,"POST_IMPORT_INITIALIZED_GUARD");if(s.getSolution().isInitialized())throw new IllegalStateException("import initialized solution");
            at(s,d,"POST_IMPORT_ITERATION_GUARD");if(s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("import advanced iteration");
            at(s,d,"SAVE_IMPORT_ONLY_STATE");s.saveState(new File(d,"naca0012-mesh-repair2-import.sim").getAbsolutePath());
            String json="{\"status\":\"ok\",\"verification_scope\":\"EXTRUDED_NATIVE3D_IMPORT_TOPOLOGY_ONLY\",\"mesh_sha256\":"+q(MESH_SHA256)+",\"spec_sha256\":"+q(SPEC_SHA256)+",\"audit_sha256\":"+q(AUDIT_SHA256)+",\"xy_source_sha256\":"+q(XY_SOURCE_SHA256)+",\"native3D_verified\":true,\"automatic_empty_container_inventory\":"+continuumInventory(s)+",\"region_physics_assignments\":"+regionPhysicsInventory(s)+",\"active_physics_configured\":false,\"imported_empty_continuum_count\":1,\"imported_enabled_models_count\":0,\"auto_empty_physics_container_verified\":true,\"initialized\":false,\"iteration\":0,\"cells\":10752,\"vertices\":14816,\"interior_faces\":28312,\"total_faces\":36200,\"span_m\":0.1,\"layers\":3,\"zplanes\":4,\"layer_design_basis\":\"FIXED_OFFLINE_AUDIT_NOT_NATIVE_LAYER_EQUIVALENCE\",\"boundaries\":"+inventory+",\"quality_accepted\":false,\"span_invariance_measured\":false,\"science_accepted\":false,\"solve_authorized\":false}";
            Files.writeString(record.toPath(),json,StandardCharsets.UTF_8);s.println("MESH_REPAIR2_IMPORT_COMPLETE");
        }catch(Exception e){try{Files.writeString(record.toPath(),"{\"status\":\"gate_failed\",\"phase\":"+q(phase)+",\"error\":"+q(e.toString())+",\"stack\":"+q(stack(e))+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);}catch(Exception write){e.addSuppressed(write);}throw new RuntimeException("extruded native3D import rejected",e);}
    }
}
