import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import star.common.*;
import star.meshing.*;

public class AuditCadTessellationParameters extends StarMacro {
    static String env(String key) { String v=System.getenv(key); if(v==null||v.isEmpty()) throw new IllegalStateException("Missing reviewed input "+key); return v; }
    static String safe(String v) { if(!v.matches("[A-Za-z0-9_-]+")) throw new IllegalStateException("Unsafe basename"); return v; }
    static String quote(String v) { StringBuilder b=new StringBuilder("\""); for(char c:v.toCharArray()) { if(c=='\"'||c=='\\')b.append('\\').append(c); else if(c<32)b.append(String.format("\\u%04x",(int)c)); else b.append(c); } return b.append('"').toString(); }
    static String sha(File f)throws Exception { if(!Files.isRegularFile(f.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IOException("Regular input required"); MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=Files.newInputStream(f.toPath())) { byte[] buf=new byte[65536];int n;while((n=in.read(buf))>0)d.update(buf,0,n); } StringBuilder b=new StringBuilder();for(byte x:d.digest())b.append(String.format("%02x",x&255));return b.toString(); }
    static File owned(File root,String name)throws Exception { File f=new File(root,name);if(!f.getCanonicalFile().getParentFile().equals(root.getCanonicalFile())||Files.isSymbolicLink(f.toPath()))throw new IOException("Outside owned directory");return f; }
    static void write(File f,String text)throws Exception { try(OutputStream out=Files.newOutputStream(f.toPath(),StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE)){out.write(text.getBytes(StandardCharsets.UTF_8));} }
    static void finite(double v) { if(!Double.isFinite(v))throw new IllegalStateException("Nonfinite native parameter"); }
    static void noFlow(Simulation s) { if(!s.getRegionManager().getRegions().isEmpty()||!s.getContinuumManager().getObjects().isEmpty()||!s.getInterfaceManager().getObjects().isEmpty()||s.isInitialized()||s.getSimulationIterator().getCurrentIteration()!=0)throw new IllegalStateException("Geometry-only source required"); }
    static void leaves(GeometryPart p,List<CadPart> result,int depth) { if(depth>8||result.size()>32)throw new IllegalStateException("Part traversal limit");if(p instanceof CompositePart) { for(GeometryPart child:((CompositePart)p).getChildParts().getParts())leaves(child,result,depth+1); } else if(p instanceof CadPart) result.add((CadPart)p);else throw new IllegalStateException("Non-CAD source part"); }
    static String parameterFingerprint(CadPart p) {
        TessellationDensityOption o=p.getTessellationDensityOption();TessellationParameters t=o.getDetailedParameters();if(t==null)throw new IllegalStateException("No detailed parameters");
        double[] values={t.getCurveChordTolerance(),t.getCurveChordAngle(),t.getSurfacePlaneTolerance(),t.getSurfacePlaneAngle(),t.getMinFacetWidth(),t.getMaxFacetWidth(),t.getCurveChordMax(),t.getFacetPlaneTolerance(),t.getFacetPlaneAngle()};for(double value:values)finite(value);
        boolean[] flags={t.isCurveChordTolerancePercentage(),t.isSurfacePlaneTolerancePercentage(),t.isMinFacetWidthPercentage(),t.isMaxFacetWidthPercentage(),t.isCurveChordMaxPercentage()};
        return o.getSelectedInput().getSelected().name()+"|"+Arrays.toString(values)+"|"+Arrays.toString(flags);
    }
    static String inventory(List<CadPart> parts) { StringBuilder b=new StringBuilder();for(CadPart p:parts){b.append(p.getPresentationName()).append('|').append(p.getTriangleCount()).append('|').append(p.isDirty()).append('|').append(p.needsUpdate()).append('|').append(parameterFingerprint(p)).append('|');for(PartSurface f:p.getPartSurfaceManager().getObjects())b.append(f.getPresentationName()).append(':').append(p.getPartSurfacePatches(f)).append(';');}return b.toString(); }
    @Override public void execute() {
        File failure=null;
        try {
            Simulation s=getActiveSimulation();noFlow(s);
            String run=safe(env("CAD_RUN_ID")),base=safe(env("CAD_AUDIT_BASE"));
            File root=new File(env("AIR3B_JOB_DIR"));if(!root.getCanonicalPath().equals("C:\\CodexCFDRestricted\\"+run))throw new IllegalStateException("Wrong isolated path");
            File source=owned(root,base+".sim"),spec=owned(root,"case_spec.json"),report=owned(root,base+"-parameters.json");failure=owned(root,base+"-parameters-failure.json");
            if(Files.exists(report.toPath())||Files.exists(failure.toPath()))throw new IOException("Existing report");
            if(!new File(s.getSessionPath()).getCanonicalFile().equals(source.getCanonicalFile()))throw new IllegalStateException("Wrong loaded simulation");
            String sourceHash=sha(source),specHash=sha(spec);
            if(!sourceHash.equals(env("CAD_AUDIT_SHA"))||!specHash.equals(env("CAD_SPEC_SHA")))throw new IllegalStateException("Exact input hash mismatch");
            List<CadPart> parts=new ArrayList<CadPart>();for(GeometryPart p:s.get(SimulationPartManager.class).getParts())leaves(p,parts,0);if(parts.isEmpty()||parts.size()>32)throw new IllegalStateException("Native CAD part count");
            String before=inventory(parts);StringBuilder rows=new StringBuilder();int count=0;
            for(CadPart p:parts) {
                TessellationDensityOption option=p.getTessellationDensityOption();TessellationParameters t=option.getDetailedParameters();if(t==null)throw new IllegalStateException("No detailed parameters");
                double[] values={t.getCurveChordTolerance(),t.getCurveChordAngle(),t.getSurfacePlaneTolerance(),t.getSurfacePlaneAngle(),t.getMinFacetWidth(),t.getMaxFacetWidth(),t.getCurveChordMax(),t.getFacetPlaneTolerance(),t.getFacetPlaneAngle()};for(double value:values)finite(value);
                String[] keys={"curve_chord_tolerance","curve_chord_angle","surface_plane_tolerance","surface_plane_angle","min_facet_width","max_facet_width","curve_chord_max","facet_plane_tolerance","facet_plane_angle"};
                if(count>0)rows.append(',');rows.append("{\"name\":").append(quote(p.getPresentationName())).append(",\"triangles\":").append(p.getTriangleCount()).append(",\"is_dirty\":").append(p.isDirty()).append(",\"needs_update\":").append(p.needsUpdate()).append(",\"selected_option\":").append(quote(option.getSelectedInput().getSelected().name()));
                for(int i=0;i<keys.length;i++)rows.append(',').append(quote(keys[i])).append(':').append(values[i]);
                rows.append(",\"curve_chord_tolerance_percentage\":").append(t.isCurveChordTolerancePercentage()).append(",\"surface_plane_tolerance_percentage\":").append(t.isSurfacePlaneTolerancePercentage()).append(",\"min_facet_width_percentage\":").append(t.isMinFacetWidthPercentage()).append(",\"max_facet_width_percentage\":").append(t.isMaxFacetWidthPercentage()).append(",\"curve_chord_max_percentage\":").append(t.isCurveChordMaxPercentage()).append('}');count++;
            }
            noFlow(s);String after=inventory(parts);if(!before.equals(after)||!sourceHash.equals(sha(source))||!specHash.equals(sha(spec)))throw new IllegalStateException("Readonly audit drift");
            write(report,"{\"status\":\"ok\",\"scope\":\"READONLY_CAD_PARAMETER_GETTERS_ONLY\",\"run_id\":"+quote(run)+",\"source_sha256\":"+quote(sourceHash)+",\"casespec_sha256\":"+quote(specHash)+",\"part_count\":"+count+",\"inventory_before\":"+quote(before)+",\"inventory_after\":"+quote(after)+",\"source_unchanged\":true,\"parameter_unit_semantics_verified\":false,\"geometry_admission_verified\":false,\"tessellation_calls\":0,\"reimport_calls\":0,\"save_calls\":0,\"iteration\":0,\"initialized\":false,\"CFD_quality_verified\":false,\"E_verified\":false,\"parts\":["+rows+"]}");
            s.println("CAD_PARAMETER_READBACK_RECORDED");
        } catch(Exception e) {
            if(failure!=null&&!Files.exists(failure.toPath()))try {write(failure,"{\"status\":\"error\",\"scope\":\"READONLY_CAD_PARAMETER_GETTERS_ONLY\",\"error\":"+quote(e.toString())+",\"geometry_admission_verified\":false,\"E_verified\":false}");}catch(Exception ignored) { }
            throw new RuntimeException("Readonly CAD parameter audit failed",e);
        }
    }
}
