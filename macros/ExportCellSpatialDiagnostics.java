/* Read-only export of a saved solution's cell-centred spatial diagnostics (v2, general-purpose).
 * Derived from macros/ExportMeshSpatialDiagnostics.java (v1, air3b lineage, left untouched).
 * Deltas vs v1, all requested by docs/engineering-preflight-20261021/PLAN.md:
 *   1. No hard-coded worker root: session dir must equal env DIAG_JOB_DIR (isolation is enforced
 *      by the job's own admission, not by a literal path).
 *   2. "Volume" native scalar is REQUIRED (missing or ambiguous -> fail). h = V^(1/3) needs it.
 *   3. Units are verified from the exported CSV header itself, fail-closed: no stated units,
 *      any non-"m" length token, or a Volume token that is not length^3 -> fail. Raw header is
 *      recorded verbatim as evidence.
 *   4. Honest flags stay honest: wall adjacency is NOT topology-proven (grouping is geometric,
 *      done downstream); native cell-id export is NOT claimed.
 * API basis: only idioms already compiled in v1 (installed STAR 20.02.008 / 2502). No mesh
 * generation, initialization, iteration or save; state fingerprint + input re-hash postconditions.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import star.common.*;
import star.base.neo.ClientServerObject;

public class ExportCellSpatialDiagnostics extends StarMacro {
    private static final String COMPLETE="CELL_SPATIAL_DIAGNOSTICS_COMPLETE";
        private static String env(String k){String v=System.getenv(k);if(v==null||v.isEmpty())throw new IllegalArgumentException("Missing "+k);return v;}
    private static String base(String v){if(!v.matches("[a-zA-Z0-9_-]+"))throw new IllegalArgumentException("Unsafe basename");return v;}
    private static String q(String v){StringBuilder b=new StringBuilder("\"");for(char c:v.toCharArray()){if(c=='"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
    private static String sha(File f)throws Exception{if(!Files.isRegularFile(f.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IOException("Regular non-link input required");MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=Files.newInputStream(f.toPath())){byte[] b=new byte[1048576];int n;while((n=in.read(b))>0)d.update(b,0,n);}StringBuilder s=new StringBuilder();for(byte x:d.digest())s.append(String.format("%02x",x&255));return s.toString();}
    private static void pin(File f,String h)throws Exception{if(!h.matches("[a-f0-9]{64}")||!sha(f).equals(h))throw new IllegalStateException("Input SHA mismatch: "+f.getName());}
    private static File owned(File dir,String name)throws Exception{File f=new File(dir,name);if(!f.getCanonicalFile().getParentFile().equals(dir.getCanonicalFile())||Files.exists(f.toPath(),LinkOption.NOFOLLOW_LINKS))throw new IllegalStateException("Unsafe/existing output: "+name);return f;}
    private static void write(File f,String s)throws Exception{Files.writeString(f.toPath(),s,StandardCharsets.UTF_8,StandardOpenOption.CREATE_NEW);}
    private static long count(String k){String x=env(k);if(!x.matches("[0-9]+"))throw new IllegalArgumentException("Invalid "+k);return Long.parseLong(x);}
    private static String fields(Collection<FieldFunction> fs){StringBuilder b=new StringBuilder("[");for(FieldFunction f:fs){if(b.length()>1)b.append(',');b.append("{\"name\":").append(q(f.getFunctionName())).append(",\"native_type\":").append(q(String.valueOf(f.getTypeAsEnum()))).append(",\"class\":").append(q(f.getClass().getName())).append(",\"label\":").append(q(f.getPresentationName())).append(",\"user_field\":").append(f instanceof UserFieldFunction).append('}');}return b.append(']').toString();}
    private static FieldFunction nativeMetric(Simulation s,FvRepresentation v,String name,Class<?> exact){FieldFunction f=s.getFieldFunctionManager().getFunction(name);if(f==null||!f.getClass().equals(exact)||!name.equals(f.getFunctionName())||!f.isBoundToRepresentation(v)||f instanceof UserFieldFunction)throw new IllegalStateException("Exact native metric unavailable: "+name);return f;}
    private static void addExisting(List<FieldFunction> dst,Collection<FieldFunction> existing,String... names){for(String name:names){FieldFunction found=null;for(FieldFunction f:existing)if(name.equals(f.getFunctionName())&&!(f instanceof UserFieldFunction)){if(found!=null&&found!=f)throw new IllegalStateException("Ambiguous native field "+name);found=f;}if(found!=null&&found.getTypeAsEnum()==FieldFunctionTypeOption.Type.SCALAR&&!dst.contains(found))dst.add(found);}}
    private static FieldFunction requiredNativeScalar(Collection<FieldFunction> pool,String name){FieldFunction found=null;for(FieldFunction f:pool)if(name.equals(f.getFunctionName())&&!(f instanceof UserFieldFunction)){if(found!=null&&found!=f)throw new IllegalStateException("Ambiguous native field: "+name);found=f;}if(found==null)throw new IllegalStateException("Required native field missing: "+name);return found;}
    /* Only explicit SI length/volume spellings are admitted; case remains significant. */
    private static String normalizeUnit(String u){return u==null?null:u.trim();}
    private static final class HeaderFailure extends Exception {
        final String raw,file; final boolean truncated;
        HeaderFailure(File csv,String header,Exception cause){super(cause.getMessage(),cause);file=csv.getName();truncated=header.length()>2000;raw=truncated?header.substring(0,2000):header;}
    }
    private static List<String> headerCells(String header){
        List<String> cells=new ArrayList<>();StringBuilder cell=new StringBuilder();boolean quoted=false,closed=false;
        for(int i=0;i<header.length();i++){char c=header.charAt(i);
            if(quoted){if(c=='"'){if(i+1<header.length()&&header.charAt(i+1)=='"'){cell.append('"');i++;}else{quoted=false;closed=true;}}else cell.append(c);}
            else if(c==','){cells.add(cell.toString().trim());cell.setLength(0);closed=false;}
            else if(c=='"'){if(closed||!cell.toString().trim().isEmpty())throw new IllegalStateException("Malformed CSV header");cell.setLength(0);quoted=true;}
            else {if(closed&&!Character.isWhitespace(c))throw new IllegalStateException("Malformed CSV header");cell.append(c);}
        }
        if(quoted)throw new IllegalStateException("Unclosed CSV header quote");cells.add(cell.toString().trim());return cells;
    }
    /* Validate each coordinate column and the exact Volume label, never Volume Change.
     * Other scalar dimensions (e.g. Pressure (Pa)) do not establish coordinate/volume units.
     * Successful headers are bounded in full, so region comparisons never compare prefixes.
     */
    private static String verifyHeader(File csv)throws Exception{return verifyHeader(csv,true);}
    private static String verifyHeader(File csv,boolean needVolume)throws Exception{
        String header="";
        try{
            StringBuilder raw=new StringBuilder();
            try(BufferedReader r=Files.newBufferedReader(csv.toPath(),StandardCharsets.UTF_8)){int c;while((c=r.read())!=-1&&c!='\n'&&c!='\r'){raw.append((char)c);if(raw.length()>2000)break;}}
            header=raw.toString();if(header.isEmpty())throw new IllegalStateException("Empty export header");
            if(header.length()>2000)throw new IllegalStateException("Export header exceeds 2000 characters");
            Map<String,String> columns=new HashMap<>();
            Pattern label=Pattern.compile("^([^()]+?)(?:\\s*\\(([^()]*)\\))?$");
            for(String cell:headerCells(header)){
                Matcher m=label.matcher(cell);if(!m.matches())throw new IllegalStateException("Malformed column label: "+cell);
                String name=m.group(1).trim(),unit=normalizeUnit(m.group(2));
                if(name.isEmpty()||columns.containsKey(name))throw new IllegalStateException("Empty/duplicate column: "+name);
                columns.put(name,unit);
            }
            for(String axis:Arrays.asList("X","Y","Z")){
                if(!columns.containsKey(axis))throw new IllegalStateException("Header lacks coordinate column: "+axis);
                String unit=columns.get(axis);if(unit==null||unit.isEmpty())throw new IllegalStateException("units_not_stated: "+axis);
                if(!unit.equals("m"))throw new IllegalStateException("Unexpected stated unit for "+axis+": "+unit);
            }
            if(needVolume||columns.containsKey("Volume")){
                String unit=columns.get("Volume");if(unit==null||unit.isEmpty())throw new IllegalStateException("Volume unit not stated");
                if(!Arrays.asList("m^3","m3","m³").contains(unit))throw new IllegalStateException("Volume is not length^3: "+unit);
            }
            return header;
        }catch(Exception e){throw new HeaderFailure(csv,header,e);}
    }
    private static String state(Simulation s,FvRepresentation v){StringBuilder b=new StringBuilder();b.append(s.getSimulationIterator().getCurrentIteration()).append('|').append(s.getSolution().getPhysicalTime()).append('|').append(s.getSolution().isInitialized()).append('|').append(v.getCellCount()).append('|').append(v.getVertexCount()).append('|').append(v.getInteriorFaceCount()).append('|').append(s.getInterfaceManager().getObjects().size());for(Region r:s.getRegionManager().getRegions()){b.append('|').append(r.getPresentationName()).append(Arrays.toString(s.getMeshManager().getRegionExtents(r)));for(Boundary x:r.getBoundaryManager().getBoundaries())b.append('|').append(x.getPresentationName()).append(':').append(x.getBoundaryType().getClass().getName()).append(':').append(x.getDependentInterfaces().size());}for(Continuum c:s.getContinuumManager().getObjects()){b.append('|').append(c.getClass().getName()).append(':').append(c.getPresentationName());if(c instanceof PhysicsContinuum)for(Model m:((PhysicsContinuum)c).getModelManager().getObjects())b.append(':').append(m.getClass().getName());}return b.toString();}
    private static String table(Simulation s,FvRepresentation v,star.base.neo.NamedObject part,List<FieldFunction> fs,File dir,String name,long expected,List<ClientServerObject> tmp)throws Exception{for(FieldFunction f:fs)if(f.getTypeAsEnum()!=FieldFunctionTypeOption.Type.SCALAR)throw new IllegalStateException("Only scalar fields admitted to XYZ scalar extraction: "+f.getFunctionName());XyzInternalTable t=s.getTableManager().createTable(XyzInternalTable.class);tmp.add(t);t.setRepresentation(v);t.getParts().setObjects(part);t.setExtractVertexData(false);t.setFieldFunctions(fs);t.extract();long rows=t.getRowCount();if(rows<=0||rows!=expected)throw new IllegalStateException("Native table row count differs: "+name+" "+rows+" vs "+expected);File out=owned(dir,name);t.export(out.getAbsolutePath());if(out.length()==0||out.length()>2147483648L)throw new IllegalStateException("Empty/oversize export");String tableHeader=verifyHeader(out,part instanceof Region);String result="{\"file\":"+q(name)+",\"rows\":"+rows+",\"bytes\":"+out.length()+",\"sha256\":"+q(sha(out))+",\"fields\":"+fields(fs)+",\"extract_vertex_data\":false,\"export_header_raw\":"+q(tableHeader)+"}";s.deleteObjects(Collections.singletonList(t));tmp.remove(t);return result;}
    @Override public void execute(){Simulation s=getActiveSimulation();File dir=s.getSessionDirFile(),source=null;FvRepresentation v=null;String before=null,phase="guards",headerRaw=null;boolean directoryVerified=false;List<ClientServerObject> tmp=new ArrayList<>();try{
        String run=base(env("DIAG_RUN_ID")),sourceBase=base(env("DIAG_SOURCE_BASE"));File jobDir=new File(env("DIAG_JOB_DIR"));if(!dir.getCanonicalFile().equals(jobDir.getCanonicalFile()))throw new IllegalStateException("Session directory is not the admitted job directory");directoryVerified=true;
        source=new File(dir,sourceBase+".sim");if(!new File(s.getSessionPath()).getCanonicalFile().equals(source.getCanonicalFile()))throw new IllegalStateException("Wrong loaded source");pin(source,env("DIAG_SOURCE_SHA"));pin(new File(dir,"case_spec.json"),env("DIAG_SPEC_SHA"));
        v=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");long expected=count("DIAG_EXPECTED_CELLS"),iteration=count("DIAG_EXPECTED_ITERATION");if(v==null||!v.is3D()||v.isAtLeastOne2D()||expected<1||expected>3000000||v.getCellCount()!=expected||s.getSimulationIterator().getCurrentIteration()!=iteration)throw new IllegalStateException("Unexpected saved mesh/iteration");
        before=state(s,v);phase="field_inventory";List<FieldFunction> existing=new ArrayList<>(s.getFieldFunctionManager().getObjects()),scalars=new ArrayList<>(s.getFieldFunctionManager().getScalarFieldFunctions(v));for(FieldFunction f:scalars)if(!existing.contains(f))existing.add(f);
        List<FieldFunction> applicable=new ArrayList<>();for(FieldFunction f:existing)if(!(f instanceof UserFieldFunction)&&f.isBoundToRepresentation(v))applicable.add(f);
        write(owned(dir,"cell-spatial-early-field-inventory.json"),"{\"status\":\"DIAGNOSTIC_ONLY\",\"scope\":\"BEFORE_OPTIONAL_FIELD_SELECTION\",\"fields\":"+fields(existing)+"}");
        phase="volume_field";FieldFunction vol=requiredNativeScalar(applicable,"Volume");String volClass=vol.getClass().getName();
        List<FieldFunction> volume=new ArrayList<>();volume.add(nativeMetric(s,v,"CellQuality",CellQualityFunction.class));volume.add(nativeMetric(s,v,"FaceValidity",FaceValidityFunction.class));volume.add(nativeMetric(s,v,"VolumeChange",VolumeChangeFunction.class));
        addExisting(volume,applicable,"WallDistance","PrismLayerNumber","FirstPrismLayerHeight","PrismCellThickness","Volume","CellIndex","CellId","CellID","Pressure");if(!volume.contains(vol))volume.add(vol);
        List<FieldFunction> residuals=new ArrayList<>();for(FieldFunction f:scalars)if(!(f instanceof UserFieldFunction)&&f.isBoundToRepresentation(v)&&f.getTypeAsEnum()==FieldFunctionTypeOption.Type.SCALAR&&(f.getFunctionName()+" "+f.getPresentationName()).toLowerCase(Locale.ROOT).contains("residual")){residuals.add(f);if(residuals.size()>16)throw new IllegalStateException("Too many residual fields for bounded export");if(!volume.contains(f))volume.add(f);}
        write(owned(dir,"cell-spatial-field-inventory.json"),"{\"status\":\"ok\",\"scope\":\"EXISTING_GLOBAL_AND_REPRESENTATION_FIELDS\",\"volume_required\":true,\"volume_class\":"+q(volClass)+",\"fields\":"+fields(existing)+",\"bound_native_fields\":"+fields(applicable)+",\"residual_fields\":"+fields(residuals)+",\"residual_storage_changed\":false,\"residual_semantics_verified\":false}");
        List<FieldFunction> wall=new ArrayList<>();wall.add(volume.get(0));addExisting(wall,applicable,"WallYplus","PrismLayerNumber","FirstPrismLayerHeight","PrismCellThickness","Pressure");
        phase="cell_centred_tables";StringBuilder exports=new StringBuilder("[");long total=0;int ri=0;
        for(Region r:s.getRegionManager().getRegions()){if(!v.describesRegion(r))throw new IllegalStateException("Region absent from volume mesh");FvRegion fv=v.getFvRegionManager().getFvRegion(r);long n=fv.getCellCount();if(n<=0)throw new IllegalStateException("Empty region");if(exports.length()>1)exports.append(',');String tname="cell-spatial-volume-r"+ri+".csv";exports.append("{\"location\":\"VOLUME_CELL_CENTRES\",\"region\":").append(q(r.getPresentationName())).append(",\"native_region_extents_raw_interleaved\":").append(Arrays.toString(s.getMeshManager().getRegionExtents(r))).append(",\"table\":").append(table(s,v,r,volume,dir,tname,n,tmp)).append('}');String h=verifyHeader(new File(dir,tname));if(headerRaw==null)headerRaw=h;else if(!headerRaw.equals(h))throw new IllegalStateException("Inconsistent export headers across regions");total+=n;
            int bi=0;for(Boundary b:r.getBoundaryManager().getBoundaries()){FvBoundary fb=fv.getFvBoundaries().getFvBoundary(b);if(fb==null)throw new IllegalStateException("Native FV boundary lookup unavailable: "+b.getPresentationName());long faces=fb.getFaceCount();exports.append(",{\"location\":\"BOUNDARY_FACE_CENTRES\",\"region\":").append(q(r.getPresentationName())).append(",\"boundary\":").append(q(b.getPresentationName())).append(",\"boundary_type\":").append(q(b.getBoundaryType().getClass().getName())).append(",\"interface_memberships\":").append(b.getDependentInterfaces().size()).append(",\"native_faces\":").append(faces);if(faces>0){List<FieldFunction> selected=b.getBoundaryType() instanceof WallBoundary?wall:Collections.singletonList(volume.get(0));exports.append(",\"table\":").append(table(s,v,b,selected,dir,"cell-spatial-boundary-r"+ri+"-b"+bi+".csv",faces,tmp));}else exports.append(",\"table_status\":\"EMPTY_NATIVE_BOUNDARY_NO_NUMERIC_SENTINEL_INTERPRETATION\"");exports.append('}');bi++;}ri++;}
        exports.append(']');if(total!=expected)throw new IllegalStateException("Region/global cell counts differ");phase="readonly_postconditions";if(!tmp.isEmpty()||!before.equals(state(s,v)))throw new IllegalStateException("Readonly state changed");pin(source,env("DIAG_SOURCE_SHA"));pin(new File(dir,"case_spec.json"),env("DIAG_SPEC_SHA"));
        write(owned(dir,"cell_spatial_diagnostics.json"),"{\"status\":\"ok\",\"scope\":\"SAVED_NATIVE_MESH_SPATIAL_EXPORT_ONLY_DIAGNOSTIC_NO_CERTIFICATE\",\"run_id\":"+q(run)+",\"source_sha256\":"+q(env("DIAG_SOURCE_SHA"))+",\"spec_sha256\":"+q(env("DIAG_SPEC_SHA"))+",\"iteration\":"+iteration+",\"cells\":"+total+",\"read_only_state_unchanged\":true,\"source_bytes_unchanged\":true,\"new_solver_iterations\":0,\"new_meshes\":0,\"volume_field_class\":"+q(volClass)+",\"coordinate_units_verified\":\"m\",\"volume_units_verified\":\"m3\",\"export_header_raw\":"+q(headerRaw)+",\"wall_adjacency_mapping_verified\":false,\"wall_face_centre_tables_exported\":true,\"grouping_basis\":\"GEOMETRIC_DOWNSTREAM_ANALYSIS_ONLY\",\"native_cell_id_export_verified\":false,\"local_residual_fields_count\":"+residuals.size()+",\"local_residual_values_validity_verified\":false,\"engineering_verified\":false,\"exports\":"+exports+",\"complete_marker\":"+q(COMPLETE)+"}");s.println(COMPLETE);
    }catch(Exception e){try{if(directoryVerified)write(owned(dir,"cell_spatial_failure.json"),"{\"status\":\"error\",\"phase\":"+q(phase)+",\"error\":"+q(e.toString())+",\"export_header_raw\":"+(e instanceof HeaderFailure?q(((HeaderFailure)e).raw):"null")+",\"export_header_file\":"+(e instanceof HeaderFailure?q(((HeaderFailure)e).file):"null")+",\"export_header_truncated\":"+(e instanceof HeaderFailure&&((HeaderFailure)e).truncated)+",\"read_only_state_unchanged\":"+(v!=null&&before!=null&&before.equals(state(s,v)))+",\"engineering_verified\":false}");}catch(Exception ignored){}throw new RuntimeException("Cell spatial diagnostic failed at "+phase,e);
    }finally{if(!tmp.isEmpty())s.deleteObjects(tmp);}}
}
