/* Quality repair1: persist native field inventory before attempting measurements.
 * No initialize/step, synthetic field creation, or scientific/solver authorization.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import star.common.*;
import star.base.neo.ClientServerObject;
import star.base.report.ScalarReport;
public class QualityReadonly extends StarMacro {
    // Populate only after independent review of actual installed field-class inventory.
    private static final Map<String,String> EXPECTED_NATIVE_NEARWALL_CLASSES=Collections.emptyMap();
    private static String q(String x){StringBuilder b=new StringBuilder("\"");for(char c:x.toCharArray()){switch(c){case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}}return b.append('"').toString();}
    private static String hash(File f)throws Exception{MessageDigest m=MessageDigest.getInstance("SHA-256");m.update(Files.readAllBytes(f.toPath()));StringBuilder b=new StringBuilder();for(byte v:m.digest())b.append(String.format("%02x",v&255));return b.toString();}
    private static String stack(Throwable e){StringWriter w=new StringWriter();e.printStackTrace(new PrintWriter(w));return w.toString();}
    private static String fields(Collection<FieldFunction> fs){StringBuilder b=new StringBuilder("[");for(FieldFunction f:fs){if(b.length()>1)b.append(',');b.append("{\"function_name\":").append(q(f.getFunctionName())).append(",\"actual_class\":").append(q(f.getClass().getName())).append(",\"presentation_name\":").append(q(f.getPresentationName())).append('}');}return b.append(']').toString();}
    private static void lookupInventory(Simulation s,FvRepresentation v,File dir,String phase)throws Exception{
        List<FieldFunction> global=new ArrayList<>(s.getFieldFunctionManager().getObjects()),rep=new ArrayList<>();String error="null";
        try{rep.addAll(s.getFieldFunctionManager().getScalarFieldFunctions(v));}catch(Exception e){error=q(stack(e));}
        Files.writeString(new File(dir,"cellquality-lookup-"+phase+"-inventory.json").toPath(),"{\"status\":\"DIAGNOSTIC_ONLY\",\"phase\":"+q(phase)+",\"global_all_existing_types\":"+fields(global)+",\"representation_scalar_fields\":"+fields(rep)+",\"representation_error\":"+error+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
    }
    private static double minimum(Simulation s,FvRepresentation v,Region r,Class<?> type,List<ClientServerObject> tmp)throws Exception{
        String expectedName=type.equals(CellQualityFunction.class)?"CellQuality":type.equals(FaceValidityFunction.class)?"FaceValidity":type.equals(VolumeChangeFunction.class)?"VolumeChange":null;if(expectedName==null)throw new IllegalStateException("unknown native metric identity");
        FieldFunction selected=null;for(FieldFunction f:s.getFieldFunctionManager().getScalarFieldFunctions(v))if(f.getClass().equals(type)){if(selected!=null)throw new IllegalStateException("ambiguous exact native metric class "+type.getName());selected=f;}
        if(selected==null&&type.equals(CellQualityFunction.class)){
            File dir=s.getSessionDirFile();lookupInventory(s,v,dir,"before");
            try{
                // Official getFunction searches children and may synthesize a derived fallback.
                // Diagnostic lookup only; exact native class and representation binding remain required.
                FieldFunction candidate=s.getFieldFunctionManager().getFunction("CellQuality");
                boolean exact=candidate!=null&&candidate.getClass().equals(CellQualityFunction.class);
                boolean bound=candidate!=null&&candidate.isBoundToRepresentation(v);
                boolean nameMatches=candidate!=null&&"CellQuality".equals(candidate.getFunctionName());
                Files.writeString(new File(dir,"cellquality-lookup-result.json").toPath(),"{\"status\":\"DIAGNOSTIC_ONLY\",\"actual_class\":"+(candidate==null?"null":q(candidate.getClass().getName()))+",\"function_name\":"+(candidate==null?"null":q(candidate.getFunctionName()))+",\"exact_CellQualityFunction\":"+exact+",\"bound_to_representation\":"+bound+",\"accepted_for_measurement\":"+(exact&&bound&&nameMatches)+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
                if(!exact||!bound||!nameMatches||candidate instanceof UserFieldFunction)throw new IllegalStateException("CellQuality lookup returned unknown/derived/User/unbound field, not exact native CellQualityFunction");
                selected=candidate;
            }catch(Exception e){Files.writeString(new File(dir,"cellquality-lookup-failure.json").toPath(),"{\"status\":\"gate_failed\",\"phase\":\"diagnostic_CellQuality_lookup\",\"error\":"+q(e.toString())+",\"stack\":"+q(stack(e))+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);throw e;}
            finally{lookupInventory(s,v,dir,"after");}
        }
        if(selected==null||!selected.getClass().equals(type)||selected instanceof UserFieldFunction||!expectedName.equals(selected.getFunctionName())||!selected.isBoundToRepresentation(v))throw new IllegalStateException("exact bound native metric unavailable: "+type.getName());
        ScalarReport report=s.getReportManager().create("star.base.report.MinReport");tmp.add(report);report.setRepresentation(v);report.setFieldFunction(selected);report.getParts().setObjects(r);double value=report.getReportMonitorValue();if(!Double.isFinite(value)||Math.abs(value)>=1e299)throw new IllegalStateException("invalid/empty native min "+type.getName());return value;
    }
    private static String nearwall(Simulation s,FvRepresentation v,Region r,Boundary wall,File dir,String id,List<ClientServerObject> tmp){
        boolean exists=s.getFieldFunctionManager().hasFunction(id);if(!exists)return "{\"id\":"+q(id)+",\"status\":\"NATIVE_EXISTING_FIELD_ABSENT\"}";
        FieldFunction selected=null;for(FieldFunction f:s.getFieldFunctionManager().getObjects())if(id.equals(f.getFunctionName())){if(selected!=null)return "{\"id\":"+q(id)+",\"status\":\"AMBIGUOUS_EXISTING_FIELD\"}";selected=f;}
        if(selected==null)return "{\"id\":"+q(id)+",\"status\":\"HASFUNCTION_WITHOUT_EXISTING_OBJECT_REJECTED\"}";
        String actual=selected.getClass().getName(),expected=EXPECTED_NATIVE_NEARWALL_CLASSES.get(id);
        if(selected instanceof UserFieldFunction||expected==null||!expected.equals(actual))return "{\"id\":"+q(id)+",\"actual_class\":"+q(actual)+",\"status\":\"CLASS_NOT_IN_INDEPENDENTLY_PINNED_NATIVE_REGISTRY\"}";
        if(id.equals("WallYplus")&&!s.getSolution().isInitialized())return "{\"id\":"+q(id)+",\"actual_class\":"+q(actual)+",\"status\":\"NOT_INITIALIZED_NO_MEANINGFUL_YPLUS\"}";
        try{XyzInternalTable t=s.getTableManager().createTable(XyzInternalTable.class);tmp.add(t);t.setRepresentation(v);t.getParts().setObjects(id.equals("WallDistance")?r:wall);t.setExtractVertexData(false);t.setFieldFunctions(Arrays.asList(selected));t.extract();if(t.getRowCount()<=0)throw new IllegalStateException("empty native table");File output=new File(dir,id.equals("WallDistance")?"native-volume-wall-distance.csv":"native-wall-face-yplus.csv");t.export(output.getAbsolutePath());return "{\"id\":"+q(id)+",\"actual_class\":"+q(actual)+",\"status\":\"NATIVE_EXISTING_EXACT_CLASS_EXPORT\",\"location\":"+q(id.equals("WallDistance")?"VOLUME_ALL_CELL_CENTRED_NOT_WALL_ADJACENCY_SUBSET":"WALL_FACE_CENTRED_NOT_VOLUME_CELL_VALUE")+",\"rows\":"+t.getRowCount()+",\"sha256\":"+q(hash(output))+"}";}catch(Exception e){return "{\"id\":"+q(id)+",\"phase\":\"nearwall_table_export\",\"status\":\"EXPORT_UNAVAILABLE\",\"error\":"+q(e.toString())+",\"stack\":"+q(stack(e))+"}";}
    }
    @Override public void execute(){Simulation s=getActiveSimulation();File dir=s.getSessionDirFile(),out=new File(dir,"naca0012_native_quality.json");List<ClientServerObject> tmp=new ArrayList<>();String phase="field_inventory",inventory="null";long started=s.getSimulationIterator().getCurrentIteration();boolean initialized=s.getSolution().isInitialized(),finalRecordWritten=false;
        try{
            Files.writeString(out.toPath(),"{\"status\":\"gate_failed\",\"phase\":\"started_not_completed\",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
            FvRepresentation v=(FvRepresentation)s.getRepresentationManager().getObject("Volume Mesh");
            // Global inventory is an all-types superset: it includes scalars without filtering them away.
            List<FieldFunction> global=new ArrayList<>(s.getFieldFunctionManager().getObjects()),rep=new ArrayList<>();String repError="null";
            try{if(v!=null)rep.addAll(s.getFieldFunctionManager().getScalarFieldFunctions(v));}catch(Exception e){repError=q(stack(e));}
            inventory="{\"status\":\"ok\",\"global_scope\":\"ALL_EXISTING_TYPES_SUPERSET_INCLUDES_SCALARS\",\"global_fields\":"+fields(global)+",\"representation_scalar_fields\":"+fields(rep)+",\"representation_enumeration_error\":"+repError+"}";
            Files.writeString(new File(dir,"native-field-inventory.json").toPath(),inventory,StandardCharsets.UTF_8);
            StringBuilder text=new StringBuilder("scope\tfunction_name\tactual_class\tpresentation_name\n");for(FieldFunction f:global)text.append("global_all_types\t").append(f.getFunctionName()).append('\t').append(f.getClass().getName()).append('\t').append(f.getPresentationName()).append('\n');for(FieldFunction f:rep)text.append("representation_scalar\t").append(f.getFunctionName()).append('\t').append(f.getClass().getName()).append('\t').append(f.getPresentationName()).append('\n');Files.writeString(new File(dir,"native-field-inventory.txt").toPath(),text.toString(),StandardCharsets.UTF_8);
            phase="native2d_preconditions";File spec=new File(dir,"case_spec.json");if(!spec.isFile()||v==null||v.getCellCount()<=0||v.is3D()||v.isAtLeastOne3D()||!v.isAtLeastOne2D()||s.getRegionManager().getRegions().size()!=1)throw new IllegalStateException("Spec and unique exclusively native2D mesh required");Region r=s.getRegionManager().getRegions().iterator().next();Boundary wall=r.getBoundaryManager().getBoundary("airfoil");if(!v.describesRegion(r)||v.is3D(r)||wall==null||r.getBoundaryManager().getBoundary("farfield")==null||r.getBoundaryManager().getBoundaries().size()!=2)throw new IllegalStateException("native2D region/boundary identity mismatch");
            phase="nearwall_existing_field_diagnostic";String near="["+nearwall(s,v,r,wall,dir,"WallDistance",tmp)+","+nearwall(s,v,r,wall,dir,"WallYplus",tmp)+"]";Files.writeString(new File(dir,"native-nearwall-diagnostic.json").toPath(),"{\"status\":\"DIAGNOSTIC_ONLY\",\"fields\":"+near+",\"wall_adjacency_mapping_verified\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
            phase="native_metric_measurements";Class<?>[] types={CellQualityFunction.class,FaceValidityFunction.class,VolumeChangeFunction.class};String[] names={"CellQuality","FaceValidity","VolumeChange"};double[] values=new double[3];boolean complete=true;StringBuilder measurements=new StringBuilder("[");
            for(int i=0;i<3;i++){if(i>0)measurements.append(',');try{values[i]=minimum(s,v,r,types[i],tmp);measurements.append("{\"name\":").append(q(names[i])).append(",\"expected_native_class\":").append(q(types[i].getName())).append(",\"status\":\"MEASURED\",\"min\":").append(values[i]).append('}');}catch(Exception e){complete=false;measurements.append("{\"name\":").append(q(names[i])).append(",\"expected_native_class\":").append(q(types[i].getName())).append(",\"status\":\"UNAVAILABLE_NOT_MEASURED\",\"phase\":\"native_metric_measurements\",\"error\":").append(q(e.toString())).append(",\"stack\":").append(q(stack(e))).append('}');}}measurements.append(']');
            boolean passed=complete&&values[0]>=1e-5&&values[1]>=1&&values[2]>.01;
            if(s.getSimulationIterator().getCurrentIteration()!=started||s.getSolution().isInitialized()!=initialized)throw new IllegalStateException("readonly solution state changed");
            String result="{\"status\":"+q(complete?"ok":"gate_failed")+",\"phase\":\"native_metric_measurements\",\"failure_kind\":"+(complete?"null":"\"NATIVE_METRIC_UNAVAILABLE_NOT_A_QUALITY_FAILURE\"")+",\"spec_sha256\":"+q(hash(spec))+",\"cells\":"+v.getCellCount()+",\"metric_measurements\":"+measurements+",\"metric_measurements_complete\":"+complete+",\"metric_gates_passed\":"+passed+",\"cell_quality_bound\":0.00001,\"face_validity_bound\":1,\"volume_change_strict_bound\":0.01,\"start_iteration\":"+started+",\"end_iteration\":"+s.getSimulationIterator().getCurrentIteration()+",\"initialized_before\":"+initialized+",\"initialized_after\":"+s.getSolution().isInitialized()+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}";Files.writeString(out.toPath(),result,StandardCharsets.UTF_8);finalRecordWritten=true;if(!complete)throw new IllegalStateException("native metric unavailable; diagnostics preserved");s.println("NATIVE_2D_METRIC_DIAGNOSTIC_COMPLETE");
        }catch(Exception e){try{Files.writeString(new File(dir,"native-quality-failure.json").toPath(),"{\"status\":\"gate_failed\",\"phase\":"+q(phase)+",\"error\":"+q(e.toString())+",\"stack\":"+q(stack(e))+",\"inventory_preserved\":"+(!inventory.equals("null"))+",\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);if(!finalRecordWritten)Files.writeString(out.toPath(),"{\"status\":\"gate_failed\",\"phase\":"+q(phase)+",\"error\":"+q(e.toString())+",\"stack\":"+q(stack(e))+",\"quality_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);}catch(Exception writeFailure){e.addSuppressed(writeFailure);}throw new RuntimeException(e);}
        finally{Collections.reverse(tmp);for(ClientServerObject o:tmp)try{s.deleteObjects(Collections.singletonList(o));}catch(Exception cleanup){s.println("readonly diagnostic cleanup failed: "+cleanup);}}
    }
}
