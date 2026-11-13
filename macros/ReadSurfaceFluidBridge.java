import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.meshing.*;
public class ReadSurfaceFluidBridge extends StarMacro {
    // BEGIN_ACTUAL_GUARD
    static final class Guard {
        static final String JOB="C:\\CodexCFDRestricted\\hm-naca-fluid-bridge-20261012a"; // ROOT_FIXED_INPUT_PIN
        static final String STL_SHA="96f159b9c083afe7c072496570695ba5492a49d0f8a0b696791d9a4cac906b15"; // ROOT_FIXED_INPUT_PIN
        static final String SPEC_SHA="e40aeee01b9cea27893e0ce0397bcf1dd1bef92740095df72760149291fd3568"; // ROOT_FIXED_INPUT_PIN
        static final String RUN_ID="hm-naca-fluid-bridge-20261012a"; // ROOT_FIXED_INPUT_PIN
        static final long INPUT_TRIANGLES=28908; // ROOT_FIXED_INPUT_PIN
        static final String PART="HM_NACA_SURFACE_BRIDGE_BODY";
        static final String FLUID="HM_SURFACE_MODE_FLUID_CONTROL";
        static void frozen(){if(!JOB.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}")||RUN_ID.isEmpty()||!JOB.endsWith("\\"+RUN_ID)||!STL_SHA.matches("[a-f0-9]{64}")||!SPEC_SHA.matches("[a-f0-9]{64}")||INPUT_TRIANGLES<=0)throw new IllegalStateException("PENDING_FIXED_INPUT_PINS_NO_EXECUTION");}
        static void job(String value){if(!JOB.equals(value))throw new IllegalStateException("Wrong isolated job");}
        static void hash(String actual,String expected){if(expected==null||!expected.matches("[a-f0-9]{64}")||!expected.equals(actual))throw new IllegalStateException("SHA mismatch");}
        static void state(int parts,int regions,int models,int iteration,boolean initialized,boolean importing){
            if(parts!=(importing?0:3)||regions!=0||models!=0||iteration!=0||initialized)throw new IllegalStateException("Unexpected simulation state");
        }
        static void topology(boolean closed,boolean manifold,long triangles){if(!closed||!manifold||triangles!=INPUT_TRIANGLES)throw new IllegalStateException("Topology/count mismatch");}
        static void unused(File...paths)throws Exception {for(File f:paths)if(Files.exists(f.toPath(),java.nio.file.LinkOption.NOFOLLOW_LINKS))throw new IOException("Output collision: "+f);}
        static String sha(File f)throws Exception {
            MessageDigest d=MessageDigest.getInstance("SHA-256");
            try(InputStream in=new FileInputStream(f)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}
            StringBuilder result=new StringBuilder();for(byte b:d.digest())result.append(String.format("%02x",b&255));return result.toString();
        }
        static double[] bounds(File f)throws Exception {
            byte[] data=Files.readAllBytes(f.toPath());if(data.length<84)throw new IOException("Truncated STL");
            ByteBuffer bb=ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);long count=Integer.toUnsignedLong(bb.getInt(80));
            if(count==0||data.length!=84+50*count)throw new IOException("Native export count/layout mismatch");
            double[] out={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
            for(int i=0;i<count;i++)for(int vertex=0;vertex<3;vertex++)for(int axis=0;axis<3;axis++){
                double x=bb.getFloat(84+i*50+12+vertex*12+axis*4);if(!Double.isFinite(x))throw new IOException("Nonfinite native vertex");
                out[axis]=Math.min(out[axis],x);out[axis+3]=Math.max(out[axis+3],x);
            }return out;
        }
        static String uniqueString(String body,String key){
            java.util.regex.Matcher keys=java.util.regex.Pattern.compile(java.util.regex.Pattern.quote("\""+key+"\"")).matcher(body);int count=0;while(keys.find())count++;
            if(count!=1)throw new IllegalStateException("Missing/duplicate certificate key "+key);
            String expression=java.util.regex.Pattern.quote("\""+key+"\"")+"\\s*:\\s*\"([a-zA-Z0-9_-]+)\"";
            java.util.regex.Matcher match=java.util.regex.Pattern.compile(expression).matcher(body);
            if(!match.find())throw new IllegalStateException("Malformed certificate value "+key);return match.group(1);
        }
        static final class SourceSession {
            final File file;final boolean existed;final String shaBefore;
            SourceSession(String path,File...outputs)throws Exception{
                file=(path==null||path.isEmpty())?null:new File(path);
                if(file!=null)for(File output:outputs)if(file.getCanonicalFile().equals(output.getCanonicalFile()))throw new IllegalStateException("Source/output collision");
                existed=file!=null&&Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS);
                if(existed&&!Files.isRegularFile(file.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IOException("Source session is not a regular file");
                shaBefore=existed?sha(file):null;
            }
            void unchanged()throws Exception{
                if(existed)hash(sha(file),shaBefore);
                else if(file!=null&&Files.exists(file.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IllegalStateException("Absent source session path appeared");
            }
            String json(){return "\"source_sim_sha256\":"+(shaBefore==null?"null":quote(shaBefore))+",\"source_session_file_exists_before\":"+existed;}
        }
        static String quote(String s){return "\""+s.replace("\\","\\\\").replace("\"","\\\"").replace("\r","\\r").replace("\n","\\n").replace("\t","\\t")+"\"";}
        static void json(File destination,String body)throws Exception {
            try(java.io.OutputStream out=Files.newOutputStream(destination.toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){out.write(body.getBytes(StandardCharsets.UTF_8));}
        }
    }
    // END_ACTUAL_GUARD
    static int models(Simulation s){int n=0;for(Continuum c:s.getContinuumManager().getObjects())if(c instanceof PhysicsContinuum)n+=((PhysicsContinuum)c).getModelManager().getObjects().size();return n;}
    static void unchangedPhysics(Simulation s){if(s.getRegionManager().getRegions().size()!=0||models(s)!=0||s.isInitialized()||s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("No-flow state violated");}
    static String state(Simulation s){return "{\"parts\":"+s.get(SimulationPartManager.class).getParts().size()+",\"regions\":"+s.getRegionManager().getRegions().size()+",\"models\":"+models(s)+",\"iteration\":"+s.getSimulationIterator().getCurrentIteration()+",\"initialized\":"+s.isInitialized()+",\"modified\":"+s.isModified()+"}";}
    static MeshPart fluid(Simulation s){Guard.state(s.get(SimulationPartManager.class).getParts().size(),s.getRegionManager().getRegions().size(),models(s),s.getSimulationIterator().getCurrentIteration(),s.isInitialized(),false);MeshPart found=null;for(GeometryPart p:s.get(SimulationPartManager.class).getObjects())if(Guard.FLUID.equals(p.getPresentationName())){if(found!=null||!(p instanceof MeshPart))throw new IllegalStateException("Ambiguous/non-MeshPart fluid");found=(MeshPart)p;}if(found==null||!found.isClosed()||!found.isManifold()||found.getTriangleCount()<=0)throw new IllegalStateException("Fluid topology not established");return found;}
    static MeshPart originalBody(Simulation s){MeshPart found=null;for(GeometryPart p:s.get(SimulationPartManager.class).getObjects())if(Guard.PART.equals(p.getPresentationName())){if(found!=null||!(p instanceof MeshPart))throw new IllegalStateException("Ambiguous/non-MeshPart original body");found=(MeshPart)p;}if(found==null)throw new IllegalStateException("Original body missing");Guard.topology(found.isClosed(),found.isManifold(),found.getTriangleCount());return found;}
    static String bodyRecord(String report,MeshPart body,File nativeBody)throws Exception{return report.substring(0,report.length()-1)+",\"body_native_export_sha256\":"+Guard.quote(Guard.sha(nativeBody))+",\"body_bounds_raw\":"+Arrays.toString(Guard.bounds(nativeBody))+",\"body_triangles\":"+body.getTriangleCount()+",\"body_export_unit_verified\":false}";}
    static String record(Simulation s,String before,String stl,String spec,String output,String producer,MeshPart fluid,File exported)throws Exception{return "{\"status\":\"ok\",\"run_id\":"+Guard.quote(Guard.RUN_ID)+",\"verification_scope\":\"SURFACE_BOOLEAN_GEOMETRY_CONTROL_ONLY\",\"requested_perform_cad_boolean\":false,\"input_stl_sha256\":"+Guard.quote(stl)+",\"casespec_sha256\":"+Guard.quote(spec)+",\"output_sim_sha256\":"+Guard.quote(output)+",\"producer_record_sha256\":"+(producer==null?"null":Guard.quote(producer))+",\"closed\":"+fluid.isClosed()+",\"manifold\":"+fluid.isManifold()+",\"triangles\":"+fluid.getTriangleCount()+",\"native_export_sha256\":"+Guard.quote(Guard.sha(exported))+",\"bounds_raw\":"+Arrays.toString(Guard.bounds(exported))+",\"bounds_unit_verified\":false,\"mm_conversion\":0.001,\"before\":"+before+",\"after\":"+state(s)+",\"surface_fluid_bridge_capability\":\"NOT_ESTABLISHED_PENDING_NATIVE_OFFLINE_REVIEW\",\"formal_NACA_R500c_domain\":false,\"CFD_quality_verified\":false,\"nearwall_verified\":false,\"science_accepted\":false,\"solve_authorized\":false,\"engineering_verified\":false}";}
    @Override public void execute(){Guard.frozen();Simulation s=getActiveSimulation();try{
        Guard.job(System.getenv("AIR3B_JOB_DIR"));File dir=new File(Guard.JOB),stl=new File(dir,"naca_hm_surface_mm.stl"),spec=new File(dir,"case_spec.json"),input=new File(dir,"surface-fluid-control.sim"),producer=new File(dir,"surface-fluid-build.json"),exported=new File(dir,"fluid-reopened-native-raw.stl"),certificate=new File(dir,"surface-fluid-read.json"),bodyExport=new File(dir,"body-reopened-native-raw.stl");
        Guard.unused(exported,certificate,bodyExport);Guard.hash(Guard.sha(stl),Guard.STL_SHA);Guard.hash(Guard.sha(spec),Guard.SPEC_SHA);String producerSha=Guard.sha(producer),text=new String(Files.readAllBytes(producer.toPath()),StandardCharsets.UTF_8);
        if(!"ok".equals(Guard.uniqueString(text,"status"))||!"SURFACE_BOOLEAN_GEOMETRY_CONTROL_ONLY".equals(Guard.uniqueString(text,"verification_scope"))||!Guard.RUN_ID.equals(Guard.uniqueString(text,"run_id")))throw new IllegalStateException("Producer identity/scope/status mismatch");Guard.hash(Guard.uniqueString(text,"input_stl_sha256"),Guard.STL_SHA);Guard.hash(Guard.uniqueString(text,"casespec_sha256"),Guard.SPEC_SHA);String inputSha=Guard.uniqueString(text,"output_sim_sha256");Guard.hash(Guard.sha(input),inputSha);
        String session=s.getSessionPath();if(session==null||!new File(session).getCanonicalFile().equals(input.getCanonicalFile()))throw new IllegalStateException("Wrong reopened sim");unchangedPhysics(s);String before=state(s);if(s.getUnitsManager().getObject("mm").getConversion()!=.001)throw new IllegalStateException("mm conversion mismatch");MeshPart body=originalBody(s);body.exportStlPart(bodyExport.getAbsolutePath(),true);Guard.bounds(bodyExport);String bodyExportSha=Guard.sha(bodyExport);unchangedPhysics(s);MeshPart actual=fluid(s);actual.exportStlPart(exported.getAbsolutePath(),true);Guard.bounds(exported);unchangedPhysics(s);if(!before.equals(state(s)))throw new IllegalStateException("Readonly state changed");Guard.hash(Guard.sha(input),inputSha);Guard.hash(Guard.sha(producer),producerSha);Guard.hash(Guard.sha(stl),Guard.STL_SHA);Guard.hash(Guard.sha(spec),Guard.SPEC_SHA);Guard.hash(Guard.sha(bodyExport),bodyExportSha);Guard.json(certificate,bodyRecord(record(s,before,Guard.STL_SHA,Guard.SPEC_SHA,inputSha,producerSha,actual,exported),body,bodyExport));System.out.println("SURFACE_BOOLEAN_REOPEN_RECORDED_ONLY");
    }catch(Exception original){original.printStackTrace();throw new RuntimeException("Surface bridge readonly reopen failed",original);}}
}
