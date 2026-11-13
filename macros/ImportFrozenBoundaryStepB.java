/* Geometry-only STEP import/reopen. All CAD is authored in Fusion.
 * Native STL exports are diagnostics, never solver geometry or main CAD delivery.
 * The clean checkpoint is saved before temporary face-export shells are created.
 */
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import star.common.*;
import star.meshing.*;

public class ImportFrozenBoundaryStepB extends StarMacro {
    static final String KEY="CARRIER";
    static final boolean REOPEN=false;
    static final boolean RECOVER=false;
    // BEGIN_ACTUAL_GUARD
    static final class Guard {
        static String safe(String x){if(x==null||!x.matches("[A-Za-z0-9_-]+"))throw new IllegalArgumentException("Unsafe basename");return x;}
        static String quote(String x){StringBuilder b=new StringBuilder("\"");for(char c:x.toCharArray()){if(c=='\"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('\"').toString();}
        static String sha(File f)throws Exception{if(!Files.isRegularFile(f.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IOException("Regular non-symlink input required");MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=Files.newInputStream(f.toPath())){byte[] b=new byte[65536];int n;while((n=in.read(b))>0)d.update(b,0,n);}StringBuilder r=new StringBuilder();for(byte b:d.digest())r.append(String.format("%02x",b&255));return r.toString();}
        static void same(String a,String b){if(b==null||!b.matches("[a-f0-9]{64}")||!b.equals(a))throw new IllegalStateException("Exact byte hash mismatch");}
        static void unused(File f){if(Files.exists(f.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IllegalStateException("Refuse output replacement: "+f.getName());}
        static File owned(File root,String name)throws Exception{File f=new File(root,name);if(!f.getCanonicalFile().getParentFile().equals(root.getCanonicalFile())||Files.isSymbolicLink(f.toPath()))throw new IllegalStateException("File outside isolated job");return f;}
        static void write(File f,String text)throws Exception{try(OutputStream o=Files.newOutputStream(f.toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){o.write(text.getBytes(StandardCharsets.UTF_8));}}
        static double[] bounds(File f)throws Exception{byte[] b=Files.readAllBytes(f.toPath());if(b.length<84)throw new IOException("Short native STL");ByteBuffer v=ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);long n=Integer.toUnsignedLong(v.getInt(80));if(n==0||84+50*n!=b.length)throw new IOException("Invalid native STL size/count");double[] r={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};for(int i=0;i<n;i++){int p=84+50*i;for(int k=0;k<12;k++){float x=v.getFloat(p+4*k);if(!Float.isFinite(x))throw new IOException("Nonfinite native triangle");if(k>=3){int a=(k-3)%3;r[a]=Math.min(r[a],x);r[a+3]=Math.max(r[a+3],x);}}}return r;}
    }
    // END_ACTUAL_GUARD
    static String env(String k){String x=System.getenv(k);if(x==null||x.isEmpty())throw new IllegalStateException("Missing reviewed input: "+k);return x;}
    static void noFlow(Simulation s){if(!s.getRegionManager().getRegions().isEmpty()||!s.getContinuumManager().getObjects().isEmpty()||!s.getInterfaceManager().getObjects().isEmpty()||s.isInitialized()||s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("Geometry-only state required");}
    static void leaves(GeometryPart p,List<MeshPart> out){if(out.size()>32)throw new IllegalStateException("Part limit");if(p instanceof CompositePart){for(GeometryPart q:((CompositePart)p).getChildParts().getParts())leaves(q,out);}else if(p instanceof MeshPart)out.add((MeshPart)p);else throw new IllegalStateException("Unexpected imported part class: "+p.getClass().getName());}
    static String inventory(List<MeshPart> ps){StringBuilder b=new StringBuilder();for(MeshPart p:ps){b.append(p.getPresentationName()).append('|');for(PartSurface f:p.getPartSurfaceManager().getObjects())b.append(f.getPresentationName()).append(':').append(p.getPartSurfacePatches(f)).append(';');}return b.toString();}
    @Override public void execute(){File failure=null;try{
        Simulation s=getActiveSimulation();noFlow(s);
        String rootText=env("AIR3B_JOB_DIR"),run=env("CAD_RUN_ID");Guard.safe(run);
        File root=new File(rootText);if(!root.getCanonicalPath().equals("C:\\CodexCFDRestricted\\"+run))throw new IllegalStateException("Wrong isolated job path");
        String base=Guard.safe(env("CAD_"+KEY+"_BASE"));String prefix=base+(REOPEN?"-reopen":"-import");
        File step=Guard.owned(root,base+".step"),spec=Guard.owned(root,"case_spec.json"),checkpoint=Guard.owned(root,base+".sim");
        File report=Guard.owned(root,prefix+".json");failure=Guard.owned(root,prefix+"-failure.json");Guard.unused(report);Guard.unused(failure);
        Guard.same(Guard.sha(step),env("CAD_"+KEY+"_SHA"));Guard.same(Guard.sha(spec),env("CAD_SPEC_SHA"));
        String checkpointBefore=null;File loaded=null;int calls=0,tessellationCalls=0;
        if(REOPEN||RECOVER){loaded=RECOVER?Guard.owned(root,base+"-untessellated.sim"):checkpoint;if(!new File(s.getSessionPath()).getCanonicalFile().equals(loaded.getCanonicalFile()))throw new IllegalStateException("Wrong saved checkpoint");checkpointBefore=Guard.sha(loaded);if(RECOVER){Guard.same(checkpointBefore,env("CAD_RECOVERY_SHA"));Guard.unused(checkpoint);}}
        else {if(!s.get(SimulationPartManager.class).getParts().isEmpty())throw new IllegalStateException("New empty simulation required");Guard.unused(checkpoint);s.get(PartImportManager.class).importCadParts(Collections.singletonList(step.getAbsolutePath()),"default",30.0,1,false,1.0e-5,false,false,false,false);calls=1;}
        noFlow(s);List<MeshPart> ps=new ArrayList<MeshPart>();for(GeometryPart p:s.get(SimulationPartManager.class).getParts())leaves(p,ps);
        if(ps.isEmpty()||ps.size()>32)throw new IllegalStateException("Native part count outside limit");
        for(MeshPart p:ps){if(!(p instanceof CadPart))throw new IllegalStateException("STEP BRep CadPart required");if(!REOPEN){((CadPart)p).tessellate();tessellationCalls++;}if(p.getTriangleCount()<=0)throw new IllegalStateException("Explicit tessellation must be nonempty");}
        if(KEY.equals("FLUID")&&(ps.size()!=1||!ps.get(0).isClosed()||!ps.get(0).isManifold()))throw new IllegalStateException("One closed manifold fluid part required");
        String original=inventory(ps);int surfaceCount=0;for(MeshPart p:ps)surfaceCount+=p.getPartSurfaceManager().getObjects().size();if(surfaceCount<1||surfaceCount>64)throw new IllegalStateException("Native surface count outside limit");
        // Save only the imported CAD geometry. Diagnostic shells are never in this checkpoint.
        if(!REOPEN)s.saveState(checkpoint.getAbsolutePath());String checkpointSha=Guard.sha(checkpoint);
        StringBuilder rows=new StringBuilder();int ordinal=0,temporary=0;
        for(int j=0;j<ps.size();j++){
            MeshPart p=ps.get(j);File whole=Guard.owned(root,prefix+String.format("-part-%03d.stl",j));Guard.unused(whole);Files.createFile(whole.toPath());p.exportStlPart(whole.getAbsolutePath(),true);double[] bb=Guard.bounds(whole);
            if(j>0)rows.append(',');rows.append("{\"ordinal\":").append(j).append(",\"name\":").append(Guard.quote(p.getPresentationName())).append(",\"class\":").append(Guard.quote(p.getClass().getName())).append(",\"closed\":").append(p.isClosed()).append(",\"manifold\":").append(p.isManifold()).append(",\"triangles\":").append(p.getTriangleCount()).append(",\"whole_file\":").append(Guard.quote(whole.getName())).append(",\"whole_sha256\":").append(Guard.quote(Guard.sha(whole))).append(",\"diagnostic_bounds_raw\":").append(Arrays.toString(bb)).append(",\"surfaces\":[");
            int k=0;for(PartSurface f:new ArrayList<PartSurface>(p.getPartSurfaceManager().getObjects())){
                MeshPart shell=s.get(MeshPartFactory.class).newShellFromPartSurfaces(Collections.singletonList(f));if(shell==null||ps.contains(shell))throw new IllegalStateException("Invalid diagnostic shell");temporary++;
                File stl=Guard.owned(root,prefix+String.format("-surface-%03d.stl",ordinal++));Guard.unused(stl);Files.createFile(stl.toPath());shell.exportStlPart(stl.getAbsolutePath(),true);double[] fb=Guard.bounds(stl);
                if(k++>0)rows.append(',');rows.append("{\"name\":").append(Guard.quote(f.getPresentationName())).append(",\"patch_ids\":").append(p.getPartSurfacePatches(f)).append(",\"file\":").append(Guard.quote(stl.getName())).append(",\"sha256\":").append(Guard.quote(Guard.sha(stl))).append(",\"diagnostic_bounds_raw\":").append(Arrays.toString(fb)).append('}');
            }rows.append("]}");
        }
        noFlow(s);if(!original.equals(inventory(ps)))throw new IllegalStateException("Imported surface names/patches changed during export");Guard.same(Guard.sha(checkpoint),checkpointSha);if(REOPEN)Guard.same(checkpointSha,checkpointBefore);if(loaded!=null)Guard.same(Guard.sha(loaded),checkpointBefore);Guard.same(Guard.sha(step),env("CAD_"+KEY+"_SHA"));Guard.same(Guard.sha(spec),env("CAD_SPEC_SHA"));
        Guard.write(report,"{\"status\":\"ok\",\"verification_scope\":\"FUSION_STEP_NATIVE_IMPORT_INVENTORY_ONLY\",\"run_id\":"+Guard.quote(run)+",\"control\":"+Guard.quote(KEY)+",\"reopen\":"+REOPEN+",\"step_sha256\":"+Guard.quote(Guard.sha(step))+",\"casespec_sha256\":"+Guard.quote(Guard.sha(spec))+",\"checkpoint_sha256\":"+Guard.quote(checkpointSha)+",\"import_calls\":"+calls+",\"recovery\":"+RECOVER+",\"tessellation_calls\":"+tessellationCalls+",\"loaded_source_sha256\":"+(loaded==null?"null":Guard.quote(checkpointBefore))+",\"clean_checkpoint_imported_part_count\":"+ps.size()+",\"surface_count\":"+surfaceCount+",\"temporary_diagnostic_shells\":"+temporary+",\"source_step_unchanged\":true,\"saved_checkpoint_unchanged_during_export\":true,\"saved_checkpoint_has_diagnostic_shells\":false,\"regions\":0,\"models\":0,\"iteration\":0,\"initialized\":false,\"boundary_mapping_verified\":false,\"CFD_quality_verified\":false,\"nearwall_verified\":false,\"E_verified\":false,\"parts\":["+rows+"]}");
        System.out.println("FROZEN_STEP_IMPORT_RECORDED:"+prefix);
    }catch(Exception e){if(failure!=null)try{Guard.write(failure,"{\"status\":\"failed\",\"class\":"+Guard.quote(e.getClass().getName())+",\"cause\":"+Guard.quote(String.valueOf(e.getMessage()))+",\"partials_preserved\":true}");}catch(Exception x){e.addSuppressed(x);}throw new RuntimeException("Frozen STEP import/reopen failed",e);}}
}
