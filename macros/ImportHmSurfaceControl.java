import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import star.common.*;
import star.meshing.*;
public class ImportHmSurfaceControl extends StarMacro {
    // BEGIN_ACTUAL_GUARD
    static final class Guard {
        static final String JOB="C:\\CodexCFDRestricted\\hm-star-surface-control-20261011b";
        static final String STL_SHA="3c20fcef4ce30c85f4859081bac66e1eb3c364619705b499fc2d24fa31ec9683";
        static final String SPEC_SHA="9ce4dc7b29fbc1b19c2e82cf7262409812bb75942bed4b63a99796b0ddf74e97";
        static final String PART="HM_CYLINDER_SURFACE_CONTROL";
        static void job(String value){if(!JOB.equals(value))throw new IllegalStateException("Wrong isolated job");}
        static void hash(String actual,String expected){if(expected==null||!expected.matches("[a-f0-9]{64}")||!expected.equals(actual))throw new IllegalStateException("SHA mismatch");}
        static void state(int parts,int regions,int models,int iteration,boolean initialized,boolean importing){
            if(parts!=(importing?0:1)||regions!=0||models!=0||iteration!=0||initialized)throw new IllegalStateException("Unexpected simulation state");
        }
        static void topology(boolean closed,boolean manifold,long triangles){if(!closed||!manifold||triangles!=2336)throw new IllegalStateException("Topology/count mismatch");}
        static void unused(File...paths)throws Exception {for(File f:paths)if(Files.exists(f.toPath(),java.nio.file.LinkOption.NOFOLLOW_LINKS))throw new IOException("Output collision: "+f);}
        static String sha(File f)throws Exception {
            MessageDigest d=MessageDigest.getInstance("SHA-256");
            try(InputStream in=new FileInputStream(f)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}
            StringBuilder result=new StringBuilder();for(byte b:d.digest())result.append(String.format("%02x",b&255));return result.toString();
        }
        static double[] bounds(File f)throws Exception {
            byte[] data=Files.readAllBytes(f.toPath());if(data.length<84)throw new IOException("Truncated STL");
            ByteBuffer bb=ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);long count=Integer.toUnsignedLong(bb.getInt(80));
            if(count!=2336||data.length!=84+50*count)throw new IOException("Native export count/layout mismatch");
            double[] out={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
            for(int i=0;i<count;i++)for(int vertex=0;vertex<3;vertex++)for(int axis=0;axis<3;axis++){
                double x=bb.getFloat(84+i*50+12+vertex*12+axis*4);if(!Double.isFinite(x))throw new IOException("Nonfinite native vertex");
                out[axis]=Math.min(out[axis],x);out[axis+3]=Math.max(out[axis+3],x);
            }return out;
        }
        static String uniqueString(String body,String key){
            java.util.regex.Matcher keys=java.util.regex.Pattern.compile(java.util.regex.Pattern.quote("\""+key+"\"")).matcher(body);int count=0;while(keys.find())count++;
            if(count!=1)throw new IllegalStateException("Missing/duplicate certificate key "+key);
            String expression=java.util.regex.Pattern.quote("\""+key+"\"")+"\\s*:\\s*\"([a-zA-Z0-9_]+)\"";
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
    static int enabledModels(Simulation s){int n=0;for(Continuum c:s.getContinuumManager().getObjects())if(c instanceof PhysicsContinuum)n+=((PhysicsContinuum)c).getModelManager().getObjects().size();return n;}
    static String snapshot(Simulation s){return "{\"parts\":"+s.get(SimulationPartManager.class).getParts().size()+",\"regions\":"+s.getRegionManager().getRegions().size()+",\"enabled_physics_models\":"+enabledModels(s)+",\"iteration\":"+s.getSimulationIterator().getCurrentIteration()+",\"initialized\":"+s.isInitialized()+",\"modified\":"+s.isModified()+"}";}
    static void check(Simulation s,boolean importing){Guard.state(s.get(SimulationPartManager.class).getParts().size(),s.getRegionManager().getRegions().size(),enabledModels(s),s.getSimulationIterator().getCurrentIteration(),s.isInitialized(),importing);}
    static MeshPart part(Simulation s){GeometryPart p=s.get(SimulationPartManager.class).getParts().iterator().next();if(!(p instanceof MeshPart))throw new IllegalStateException("Not MeshPart");MeshPart mesh=(MeshPart)p;Guard.topology(mesh.isClosed(),mesh.isManifold(),mesh.getTriangleCount());return mesh;}
    @Override public void execute(){Simulation s=getActiveSimulation();try{
        Guard.job(System.getenv("AIR3B_JOB_DIR"));File dir=new File(Guard.JOB);
        File stl=new File(dir,"hm_surface_exchange_20261011a_mm.stl"),spec=new File(dir,"case_spec.json"),geometry=new File(dir,"geometry.sim");
        Guard.hash(Guard.sha(stl),Guard.STL_SHA);String specSha=Guard.SPEC_SHA;Guard.hash(System.getenv("HM_CASESPEC_SHA256"),specSha);Guard.hash(Guard.sha(spec),specSha);
        File exported=new File(dir,"import-native-si.stl"),record=new File(dir,"import-control.json");
        Guard.unused(exported,record,geometry);
        Guard.SourceSession sourceSession=new Guard.SourceSession(s.getSessionPath(),geometry,exported,record);
        check(s,true);String before=snapshot(s);
        double mmConversion=s.getUnitsManager().getObject("mm").getConversion();if(mmConversion!=0.001)throw new IllegalStateException("mm conversion mismatch");
        s.get(PartImportManager.class).importStlParts(Collections.singletonList(stl.getAbsolutePath()),"OneSurfacePerPatch","OnePartPerFile",s.getUnitsManager().getObject("mm"),true,1e-6,false,false);
        check(s,false);MeshPart mesh=part(s);mesh.setPresentationName(Guard.PART);
        String afterImport=snapshot(s);
        s.saveState(geometry.getAbsolutePath());check(s,false);Guard.hash(Guard.sha(stl),Guard.STL_SHA);Guard.hash(Guard.sha(spec),specSha);
        sourceSession.unchanged();
        String geometrySha=Guard.sha(geometry);
        mesh.exportStlPart(exported.getAbsolutePath(),true);
        double[] bounds=Guard.bounds(exported);check(s,false);String after=snapshot(s);
        Guard.hash(Guard.sha(geometry),geometrySha);Guard.hash(Guard.sha(stl),Guard.STL_SHA);Guard.hash(Guard.sha(spec),specSha);
        
        String body="{\"status\":\"ok\",\"verification_scope\":\"GEOMETRY_EXCHANGE_CONTROL_ONLY\",\"input_stl_sha256\":"+Guard.quote(Guard.STL_SHA)+",\"output_sim_sha256\":"+Guard.quote(geometrySha)+",\"casespec_sha256\":"+Guard.quote(specSha)+",\"native_export_sha256\":"+Guard.quote(Guard.sha(exported))+",\"part_name\":"+Guard.quote(mesh.getPresentationName())+",\"closed\":"+mesh.isClosed()+",\"manifold\":"+mesh.isManifold()+",\"triangles\":"+mesh.getTriangleCount()+",\"mm_conversion\":"+mmConversion+",\"bounds_raw\":"+Arrays.toString(bounds)+",\"native_export_unit_verified\":false,\"bounds_units\":\"UNVERIFIED_NATIVE_EXPORT_COORDINATES\",\"before\":"+before+",\"after_import\":"+afterImport+",\"after\":"+after+",\"CFD_quality_verified\":false,\"nearwall_verified\":false,\"science_accepted\":false,\"solve_authorized\":false,\"engineering_verified\":false}";
        sourceSession.unchanged();
        body=body.substring(0,body.length()-1)+","+sourceSession.json()+"}";
        Guard.json(record,body);System.out.println("HM_SURFACE_CONTROL_RECORD_WRITTEN");
    }catch(Exception e){e.printStackTrace();throw new RuntimeException("HM surface control failed: "+e.getMessage(),e);}}
}
