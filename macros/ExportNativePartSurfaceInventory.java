import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.meshing.*;
public class ExportNativePartSurfaceInventory extends StarMacro {
    // BEGIN_ACTUAL_GUARD
    static final class Guard {
        static void hash(String actual,String expected){if(expected==null||!expected.matches("[a-f0-9]{64}")||!expected.equals(actual))throw new IllegalStateException("SHA mismatch");}
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
            String expression=java.util.regex.Pattern.quote("\""+key+"\"")+"\\s*:\\s*\"([a-zA-Z0-9_.-]+)\"";
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
        static String quote(String s){StringBuilder b=new StringBuilder("\"");for(int i=0;i<s.length();i++){char c=s.charAt(i);if(c=='"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
        static String prefix(String basename){if(basename==null||!basename.matches("[a-zA-Z0-9_-]+\\.sim"))throw new IllegalStateException("Unsafe selected manifest basename");return basename.substring(0,basename.length()-4);}
        static void json(File destination,String body)throws Exception {
            try(java.io.OutputStream out=Files.newOutputStream(destination.toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){out.write(body.getBytes(StandardCharsets.UTF_8));}
        }
    }
    // END_ACTUAL_GUARD
    static final String JOB="C:\\CodexCFDRestricted\\hm-surface-inventory-20261012b", RUN_ID="hm-surface-inventory-20261012b", SPEC_SHA="67cc211f816570145171ff1037552bd77d2a7e2210dd48b7be8cfe1913130143", MANIFEST_SHA="b5be8329dbf94e64a3fb253b6cc9e4922dff88ef3af7a01ceab1ed9dda1c6fe0";
    static final int MAX_SURFACES=128;
    static void frozen(){if(!SPEC_SHA.matches("[a-f0-9]{64}")||!MANIFEST_SHA.matches("[a-f0-9]{64}")||JOB.equals("PENDING")||RUN_ID.equals("PENDING"))throw new IllegalStateException("PENDING_FIXED_MANIFEST_NO_EXECUTION");}
    static int models(Simulation s){int n=0;for(Continuum c:s.getContinuumManager().getObjects())if(c instanceof PhysicsContinuum)n+=((PhysicsContinuum)c).getModelManager().getObjects().size();return n;}
    static void noFlow(Simulation s){if(s.getRegionManager().getRegions().size()!=0||models(s)!=0||s.isInitialized()||s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("No-flow state violated");}
    static String state(Simulation s){return "{\"parts\":"+s.get(SimulationPartManager.class).getParts().size()+",\"regions\":"+s.getRegionManager().getRegions().size()+",\"models\":"+models(s)+",\"iteration\":"+s.getSimulationIterator().getCurrentIteration()+",\"initialized\":"+s.isInitialized()+"}";}
    static final class Entry {
        final String basename,sha,part;
        Entry(String text){basename=Guard.uniqueString(text,"loaded_basename");sha=Guard.uniqueString(text,"input_sha256");part=Guard.uniqueString(text,"declared_part");if(!basename.matches("[a-zA-Z0-9_-]+\\.sim")||!sha.matches("[a-f0-9]{64}"))throw new IllegalStateException("Malformed manifest entry");String expected="{\"loaded_basename\":"+Guard.quote(basename)+",\"input_sha256\":"+Guard.quote(sha)+",\"declared_part\":"+Guard.quote(part)+"}";if(!text.replaceAll("\\s", "").equals(expected))throw new IllegalStateException("Unexpected manifest schema/order");}
    }
    // Deliberately restricted schema: no nested values/escaped strings; all pins hash-bound.
    static Entry select(String manifest,String basename){
        java.util.regex.Matcher m=java.util.regex.Pattern.compile("\\{[^{}]*\\}").matcher(manifest);Entry selected=null;Set<String> names=new HashSet<String>();int count=0;
        while(m.find()){Entry e=new Entry(m.group());if(!names.add(e.basename))throw new IllegalStateException("Duplicate basename");count++;if(e.basename.equals(basename))selected=e;}
        if(!manifest.replaceAll("\\{[^{}]*\\}", "").matches("\\s*\\[\\s*,\\s*\\]\\s*")||count!=2||selected==null)throw new IllegalStateException("Exactly two manifest controls and unique loaded basename required");return selected;
    }
    static String surfaces(MeshPart part){StringBuilder b=new StringBuilder();for(PartSurface face:part.getPartSurfaceManager().getObjects()){b.append(System.identityHashCode(face)).append(':').append(face.getPresentationName()).append(':').append(part.getPartSurfacePatches(face)).append(';');}return b.toString();}
    static void reserve(File f)throws Exception{Guard.unused(f);Files.createFile(f.toPath());}
    static long triangles(File f)throws Exception{byte[] b=Files.readAllBytes(f.toPath());Guard.bounds(f);return Integer.toUnsignedLong(ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getInt(80));}
    @Override public void execute(){frozen();Simulation s=getActiveSimulation();File failure=null;try{
        if(!JOB.equals(System.getenv("AIR3B_JOB_DIR")))throw new IllegalStateException("Wrong isolated job");
        File dir=new File(JOB),spec=new File(dir,"case_spec.json"),manifest=new File(dir,"surface_inventory_inputs.json");
        String session=s.getSessionPath();if(session==null)throw new IllegalStateException("Saved input required");File input=new File(session);
        if(!Files.isRegularFile(input.toPath(),LinkOption.NOFOLLOW_LINKS)||!input.getCanonicalFile().getParentFile().equals(dir.getCanonicalFile()))throw new IllegalStateException("Input path/symlink mismatch");
        Guard.hash(Guard.sha(spec),SPEC_SHA);Guard.hash(Guard.sha(manifest),MANIFEST_SHA);Entry entry=select(new String(Files.readAllBytes(manifest.toPath()),StandardCharsets.UTF_8),input.getName());String inputShaBefore=Guard.sha(input);Guard.hash(inputShaBefore,entry.sha);noFlow(s);String beforeState=state(s);String prefix=Guard.prefix(entry.basename);
        MeshPart target=null;List<GeometryPart> originals=new ArrayList<GeometryPart>(s.get(SimulationPartManager.class).getObjects());
        for(GeometryPart part:originals)if(part.getPresentationName().equals(entry.part)){if(target!=null||!(part instanceof MeshPart))throw new IllegalStateException("Ambiguous/non-MeshPart target");target=(MeshPart)part;}
        if(target==null)throw new IllegalStateException("Declared target missing");List<PartSurface> faces=new ArrayList<PartSurface>(target.getPartSurfaceManager().getObjects());if(faces.isEmpty()||faces.size()>MAX_SURFACES)throw new IllegalStateException("Surface limit");
        Map<GeometryPart,String> originalPartNames=new HashMap<GeometryPart,String>();for(GeometryPart part:originals)originalPartNames.put(part,part.getPresentationName());Map<MeshPart,String> sourceSurfaces=new HashMap<MeshPart,String>();for(GeometryPart part:originals)if(part instanceof MeshPart)sourceSurfaces.put((MeshPart)part,surfaces((MeshPart)part));String originalSurfaces=surfaces(target);File before=new File(dir,prefix+"-target-before-native-raw.stl"),after=new File(dir,prefix+"-target-after-native-raw.stl"),report=new File(dir,prefix+"-native-surface-inventory.json");failure=new File(dir,prefix+"-native-surface-inventory-failure.json");
        List<File> outputs=new ArrayList<File>(Arrays.asList(before,after,report,failure));for(int i=0;i<faces.size();i++)outputs.add(new File(dir,prefix+String.format("-face-%03d-native-raw.stl",i)));
        for(File out:outputs)if(out.getCanonicalFile().equals(input.getCanonicalFile())||out.getCanonicalFile().equals(spec.getCanonicalFile())||out.getCanonicalFile().equals(manifest.getCanonicalFile()))throw new IllegalStateException("Source/output collision");Guard.unused(outputs.toArray(new File[0]));
        reserve(before);target.exportStlPart(before.getAbsolutePath(),true);String targetSha=Guard.sha(before);Guard.bounds(before);StringBuilder records=new StringBuilder();Set<GeometryPart> shells=new HashSet<GeometryPart>();
        for(int i=0;i<faces.size();i++){
            PartSurface face=faces.get(i);MeshPart shell=s.get(MeshPartFactory.class).newShellFromPartSurfaces(Collections.singletonList(face));if(shell==null||originals.contains(shell)||!shells.add(shell))throw new IllegalStateException("Invalid temporary shell");
            File exported=outputs.get(4+i);reserve(exported);shell.exportStlPart(exported.getAbsolutePath(),true);long n=triangles(exported);if(i>0)records.append(',');
            records.append("{\"ordinal\":").append(i).append(",\"session_identity\":").append(System.identityHashCode(face)).append(",\"name\":").append(Guard.quote(face.getPresentationName())).append(",\"owner_declared_part\":").append(Guard.quote(entry.part)).append(",\"patch_ids\":").append(target.getPartSurfacePatches(face)).append(",\"singleton_member_ordinal\":").append(i).append(",\"export_file\":").append(Guard.quote(exported.getName())).append(",\"stl_sha256\":").append(Guard.quote(Guard.sha(exported))).append(",\"triangles\":").append(n).append(",\"bounds_raw\":").append(Arrays.toString(Guard.bounds(exported))).append('}');noFlow(s);
        }
        List<GeometryPart> actual=new ArrayList<GeometryPart>(s.get(SimulationPartManager.class).getObjects());if(actual.size()!=originals.size()+faces.size()||!actual.containsAll(originals)||!actual.containsAll(shells)||!originalSurfaces.equals(surfaces(target)))throw new IllegalStateException("Unexpected part/surface mutation");
        for(Map.Entry<MeshPart,String> original:sourceSurfaces.entrySet())if(!original.getValue().equals(surfaces(original.getKey())))throw new IllegalStateException("Original source surfaces changed");reserve(after);target.exportStlPart(after.getAbsolutePath(),true);Guard.hash(Guard.sha(after),targetSha);String inputShaAfter=Guard.sha(input);Guard.hash(inputShaAfter,entry.sha);Guard.hash(Guard.sha(spec),SPEC_SHA);Guard.hash(Guard.sha(manifest),MANIFEST_SHA);noFlow(s);
        // Final post-export admission check: native exporter may mutate live geometry.
        actual=new ArrayList<GeometryPart>(s.get(SimulationPartManager.class).getObjects());
        if(actual.size()!=originals.size()+faces.size()||!actual.containsAll(originals)||!actual.containsAll(shells))throw new IllegalStateException("Late part inventory mutation");
        for(Map.Entry<GeometryPart,String> original:originalPartNames.entrySet())if(!original.getValue().equals(original.getKey().getPresentationName()))throw new IllegalStateException("Original part name changed");
        if(!entry.part.equals(target.getPresentationName()))throw new IllegalStateException("Declared target name changed");
        for(Map.Entry<MeshPart,String> original:sourceSurfaces.entrySet())if(!original.getValue().equals(surfaces(original.getKey())))throw new IllegalStateException("Late original source surfaces changed");
        noFlow(s);inputShaAfter=Guard.sha(input);Guard.hash(inputShaAfter,entry.sha);Guard.hash(Guard.sha(spec),SPEC_SHA);Guard.hash(Guard.sha(manifest),MANIFEST_SHA);
        Guard.json(report,"{\"status\":\"ok\",\"verification_scope\":\"NATIVE_PARTSURFACE_EXPORT_INTERFACE_PROBE_ONLY\",\"run_id\":"+Guard.quote(RUN_ID)+",\"input_sha256\":"+Guard.quote(entry.sha)+",\"loaded_basename\":"+Guard.quote(entry.basename)+",\"declared_part\":"+Guard.quote(entry.part)+",\"output_prefix\":"+Guard.quote(prefix)+",\"source_sim_sha256_before\":"+Guard.quote(inputShaBefore)+",\"source_sim_sha256_after\":"+Guard.quote(inputShaAfter)+",\"source_sim_saved\":false,\"original_surfaces_unchanged\":true,\"before\":"+beforeState+",\"after\":"+state(s)+",\"casespec_sha256\":"+Guard.quote(SPEC_SHA)+",\"manifest_sha256\":"+Guard.quote(MANIFEST_SHA)+",\"target_before_export_sha256\":"+Guard.quote(targetSha)+",\"target_after_export_sha256\":"+Guard.quote(Guard.sha(after))+",\"parts_before\":"+originals.size()+",\"parts_after\":"+actual.size()+",\"temporary_shell_count\":"+shells.size()+",\"active_session_modified_by_temporary_shells\":true,\"source_file_unchanged\":true,\"bounds_unit_verified\":false,\"role_accepted\":false,\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false,\"engineering_verified\":false,\"E_verified\":false,\"faces\":["+records+"]}");
        System.out.println("NATIVE_PARTSURFACE_EXPORT_INTERFACE_PROBE_RECORDED_ONLY:"+prefix);
    }catch(Exception cause){cause.printStackTrace();if(failure!=null)try{Guard.json(failure,"{\"status\":\"failed\",\"cause_class\":"+Guard.quote(cause.getClass().getName())+",\"cause\":"+Guard.quote(String.valueOf(cause.getMessage()))+",\"partials_preserved\":true}");}catch(Exception recordFailure){cause.addSuppressed(recordFailure);}throw new RuntimeException("Native surface inventory failed",cause);}}
}
