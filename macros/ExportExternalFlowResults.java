/* Results-only export on an isolated solved checkpoint. No initialize, step, mesh or save.
 * Coordinates, region/wall roles and reference quantities are required CaseSpec inputs.
 * API basis: installed Siemens 2502.0001 Java documentation, recorded in round18. */
import star.common.*;
import star.vis.*;
import star.flow.*;
import star.base.report.*;
import star.base.neo.*;
import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.*;
import star.motion.*;
import star.material.*;
import star.energy.*;
import star.turbulence.*;
import star.meshing.*;
import star.prismmesher.*;

public class ExportExternalFlowResults extends StarMacro {
    private final List<File> products = new ArrayList<File>();
    public void execute() {
        if("1".equals(System.getenv("PROPERTY_NEARWALL_READBACK_ONLY"))){executePropertyNearwall();return;}
        if("MRF".equals(System.getenv("RESULT_EXPORT_KIND"))){executeMrf();return;}
        Simulation s=getActiveSimulation(); File dir=s.getSessionDirFile();
        File record=new File(dir,"external_flow_export.json");
        try {
            File input=new File(s.getSessionPath()); String before=sha(input);
            String spec=Files.readString(new File(dir,"case_spec.json").toPath(),StandardCharsets.UTF_8);
            if(!"m".equals(text(spec,"model_length_unit"))) throw new IllegalArgumentException("Requires SI metre CaseSpec");
            Region region=s.getRegionManager().getRegion(text(spec,"region_name"));
            Boundary wall=region.getBoundaryManager().getBoundary(text(spec,"body_wall_boundary_name"));
            if(!(wall.getBoundaryType() instanceof WallBoundary)) throw new IllegalArgumentException("Declared body wall is not a wall");
            double[] origin=array(spec,"body_origin_m",3),velocity=array(spec,"velocity_m_s",3);
            double[] bounds=array(spec,"outer_bounds_m",6); double area=number(spec,"reference_area_m2");
            double length=number(spec,"characteristic_length_m"), speed=norm(velocity);
            if(area<=0||length<=0||speed<=0) throw new IllegalArgumentException("Positive reference quantities required");
            double[] flow=scale(velocity,1/speed),aux=new double[3];
            int axis=0;for(int i=1;i<3;i++)if(Math.abs(flow[i])<Math.abs(flow[axis]))axis=i;
            aux[axis]=1;double[] normal=cross(flow,aux);normal=scale(normal,1/norm(normal));
            double[] up=cross(normal,flow);
            int start=s.getSimulationIterator().getCurrentIteration();if(start<=0)throw new IllegalStateException("Solved checkpoint required");
            FvRepresentation mesh=null;
            for(Representation r:s.getRepresentationManager().getObjects())if(r instanceof FvRepresentation && ((FvRepresentation)r).getCellCount()>0){
                if(mesh!=null)throw new IllegalStateException("Ambiguous volume representations");mesh=(FvRepresentation)r;
            }
            if(mesh==null)throw new IllegalStateException("Volume mesh required");
            FieldFunction pressure=s.getFieldFunctionManager().getFunction("Pressure");
            FieldFunction vel=s.getFieldFunctionManager().getFunction("Velocity"),magnitude=vel.getMagnitudeFunction();
            PlaneSection plane=s.getPartManager().createImplicitPart(Arrays.asList(region),Arrays.asList(normal[0],normal[1],normal[2]),Arrays.asList(origin[0],origin[1],origin[2]));
            plane.setPresentationName("RESULT_EXPORT_FLOW_CENTRE_SECTION");
            XyzInternalTable table=s.getTableManager().createTable(XyzInternalTable.class);
            table.setPresentationName("RESULT_EXPORT_SECTION_DATA");table.setRepresentation(mesh);
            table.getParts().setObjects(plane);table.setExtractVertexData(true);
            table.setFieldFunctions(Arrays.asList(pressure,magnitude,vel.getComponentFunction(0),vel.getComponentFunction(1),vel.getComponentFunction(2)));
            table.extract();table.export(product(dir,"flow-centre-section.csv").getAbsolutePath());
            if(table.getRowCount()<=0)throw new IllegalStateException("Empty section table");
            double span=0;double[] focus=new double[3];for(int i=0;i<3;i++){span=Math.max(span,bounds[i+3]-bounds[i]);focus[i]=(bounds[i+3]+bounds[i])/2;}
            scene(s,mesh,plane,pressure,focus,normal,up,span*0.38,product(dir,"pressure-section.png"));
            scene(s,mesh,plane,magnitude,focus,normal,up,span*0.38,product(dir,"velocity-section.png"));
            double[] detail=new double[3];for(int i=0;i<3;i++)detail[i]=origin[i]+2*length*flow[i];
            scene(s,mesh,plane,magnitude,detail,normal,up,3*length,product(dir,"velocity-centre-detail.png"));
            scene(s,mesh,wall,pressure,origin,normal,up,length,product(dir,"pressure-body.png"));
            VolumeAverageReport density=s.getReportManager().createReport(VolumeAverageReport.class);
            density.setRepresentation(mesh);density.setFieldFunction(s.getFieldFunctionManager().getFunction("Density"));density.getParts().setObjects(region);
            double rho=density.getReportMonitorValue();if(!Double.isFinite(rho)||rho<=0)throw new IllegalStateException("Invalid measured density");
            double drag=0;double[] forces=new double[3];
            for(int i=0;i<3;i++){
                ForceReport f=s.getReportManager().createReport(ForceReport.class);f.setRepresentation(mesh);f.setUseIdealization(false);
                f.getParts().setObjects(wall);double[] direction=new double[3];direction[i]=1;
                f.getDirection().setComponentsAndUnits(direction[0],direction[1],direction[2],s.getUnitsManager().getObject(""));
                forces[i]=f.getReportMonitorValue();if(!Double.isFinite(forces[i]))throw new IllegalStateException("Nonfinite force");drag+=forces[i]*flow[i];
            }
            double denominator=0.5*rho*speed*speed*area;
            try(PrintWriter p=new PrintWriter(product(dir,"force-coefficients.csv"),"UTF-8")){
                p.println("iteration,Fx_N,Fy_N,Fz_N,drag_along_flow_N,Cx,Cy,Cz,Cd_estimate,rho_volume_mean_kg_m3,U_ref_m_s,A_ref_m2");
                p.println(start+","+forces[0]+","+forces[1]+","+forces[2]+","+drag+","+forces[0]/denominator+","+forces[1]/denominator+","+forces[2]/denominator+","+drag/denominator+","+rho+","+speed+","+area);
            }
            try(PrintWriter p=new PrintWriter(product(dir,"checkpoint-monitors.csv"),"UTF-8")){
                p.println("kind,name,iteration,value");
                for(Monitor m:s.getMonitorManager().getObjects()){
                    if(!(m instanceof ResidualMonitor)&&!(m instanceof ReportMonitor))continue;
                    File raw=product(dir,"monitor-"+products.size()+".csv");
                    if(m instanceof ResidualMonitor)((ResidualMonitor)m).export(raw.getAbsolutePath());else ((ReportMonitor)m).export(raw.getAbsolutePath());
                    for(String row:Files.readAllLines(raw.toPath(),StandardCharsets.UTF_8)){
                        String[] cells=row.replace("\"", "").split(",");if(cells.length!=2)continue;
                        try{double x=Double.parseDouble(cells[0].trim()),y=Double.parseDouble(cells[1].trim());
                            p.println((m instanceof ResidualMonitor?"residual":"report")+",\""+m.getPresentationName().replace("\"","\"\"")+"\","+x+","+y);
                        }catch(NumberFormatException header){}
                    }
                }
            }
            File history=new File(dir,"source_load_history.csv");
            if(history.isFile())Files.copy(history.toPath(),product(dir,"load-history.csv").toPath());
            int end=s.getSimulationIterator().getCurrentIteration();String after=sha(input);
            if(start!=end||!before.equals(after))throw new IllegalStateException("Source checkpoint changed");
            StringBuilder artifacts=new StringBuilder();
            for(File f:products){if(!f.isFile()||f.length()==0)throw new IllegalStateException("Missing export: "+f.getName());if(artifacts.length()>0)artifacts.append(',');
                artifacts.append("{\"name\":").append(quote(f.getName())).append(",\"sha256\":").append(quote(sha(f))).append(",\"bytes\":").append(f.length()).append('}');}
            Files.writeString(record.toPath(),"{\"schema_version\":1,\"status\":\"ok\",\"results_only\":true,\"input_sim_name\":"+quote(input.getName())+",\"input_sim_sha256\":"+quote(before)+",\"input_sim_sha256_after\":"+quote(after)+",\"case_spec_sha256\":"+quote(sha(new File(dir,"case_spec.json")))+",\"region\":"+quote(region.getPresentationName())+",\"body_wall\":"+quote(wall.getPresentationName())+",\"start_iteration\":"+start+",\"end_iteration\":"+end+",\"cells\":"+mesh.getCellCount()+",\"section_origin_m\":"+Arrays.toString(origin)+",\"section_normal_unit\":"+Arrays.toString(normal)+",\"section_rows\":"+table.getRowCount()+",\"pressure_basis\":\"STAR Pressure field; solver reference pressure must be read with the source physics record\",\"coefficient_basis\":\"Force/(0.5*measured volume-average density*CaseSpec U^2*CaseSpec A); derived numerical estimate\",\"experimental_validation\":false,\"artifacts\":["+artifacts+"],\"complete_marker\":\"EXTERNAL_FLOW_RESULTS_EXPORTED\"}\n",StandardCharsets.UTF_8);
            s.println("EXTERNAL_FLOW_RESULTS_EXPORTED");
        }catch(Exception e){
            try{Files.writeString(record.toPath(),"{\"status\":\"error\",\"error\":"+quote(e.toString())+"}\n",StandardCharsets.UTF_8);}catch(Exception ignored){}
            throw new RuntimeException(e);
        }
    }
    /* Opt-in read-only supplement; legacy export paths and coefficient definitions remain intact. */
    private void executePropertyNearwall(){
        Simulation s=getActiveSimulation();File dir=s.getSessionDirFile(),record=new File(dir,"property_nearwall_readback.json");
        products.clear();
        StringBuilder prerequisites=new StringBuilder();String rejectionStage="session_state";
        try{
            int start=s.getSimulationIterator().getCurrentIteration();
            boolean initializedBefore=s.getSolution().isInitialized();
            double timeBefore=s.getSolution().getPhysicalTime();
            append(prerequisites,"iteration_before",Integer.toString(start));
            append(prerequisites,"initialized_before",Boolean.toString(initializedBefore));
            append(prerequisites,"physical_time_before_s",finite(timeBefore));
            rejectionStage="input_identity";
            File input=new File(s.getSessionPath());String before=sha(input);
            append(prerequisites,"input_sim_sha256",quote(before));
            File specFile=new File(dir,"case_spec.json");String spec=Files.readString(specFile.toPath(),StandardCharsets.UTF_8);
            append(prerequisites,"case_spec_sha256",quote(sha(specFile)));
            rejectionStage="positive_iteration";
            if(start<=0)throw new IllegalStateException("Saved solved data required: active iteration is not positive ("+start+")");
            /* 2502 reopen evidence: initialization flag is session state, not persisted-solution validity.
             * Record both values separately; require unchanged initialization, never initialize to satisfy this audit. */
            rejectionStage="geometry_roles";
            if(!"m".equals(text(spec,"model_length_unit")))throw new IllegalArgumentException("Requires SI metre CaseSpec");
            Region region=s.getRegionManager().getRegion(text(spec,"region_name"));
            Boundary wall=region.getBoundaryManager().getBoundary(text(spec,"body_wall_boundary_name"));
            Boundary inlet=region.getBoundaryManager().getBoundary(text(spec,"inlet"));
            if(!(wall.getBoundaryType() instanceof WallBoundary)||wall.getPartSurfaceGroup().getObjects().isEmpty()||!wall.getDependentInterfaces().isEmpty())throw new IllegalStateException("Unverified material wall ownership");
            FvRepresentation mesh=null;
            for(Representation r:s.getRepresentationManager().getObjects())if(r instanceof FvRepresentation&&((FvRepresentation)r).getCellCount()>0){if(mesh!=null)throw new IllegalStateException("Ambiguous volume mesh");mesh=(FvRepresentation)r;}
            if(mesh==null)throw new IllegalStateException("Volume mesh absent");
            long cellsBefore=mesh.getCellCount();
            append(prerequisites,"mesh_cells",Long.toString(cellsBefore));
            append(prerequisites,"body_wall_type",quote(wall.getBoundaryType().getClass().getName()));
            append(prerequisites,"body_wall_surface_count",Integer.toString(wall.getPartSurfaceGroup().getObjects().size()));
            rejectionStage="actual_solution_fields";
            /* Live volume-mesh reports establish finite saved field data, not just a model or configured initial value. */
            for(String id:new String[]{"Density","Temperature","Velocity"}){
                if(!s.getFieldFunctionManager().hasFunction(id))throw new IllegalStateException("Required saved field unavailable: "+id);
                FieldFunction f=s.getFieldFunctionManager().getFunction(id);if("Velocity".equals(id))f=f.getMagnitudeFunction();
                VolumeAverageReport witness=s.getReportManager().createReport(VolumeAverageReport.class);
                witness.setRepresentation(mesh);witness.setFieldFunction(f);witness.getParts().setObjects(region);
                double value=witness.getReportMonitorValue();String measured=finite(value);
                append(prerequisites,"volume_mean_"+id,measured);
                if(value<=0)throw new IllegalStateException("Required saved field has nonpositive volume mean: "+id+"="+value);
            }
            rejectionStage="property_nearwall_export";
            StringBuilder properties=new StringBuilder(),missing=new StringBuilder(),ownership=new StringBuilder(),conditions=new StringBuilder();
            PhysicsContinuum c=region.getPhysicsContinuum();
            try{
                Material material=c.getModelManager().getModel(SingleComponentGasModel.class).getMaterial();
                MaterialPropertyMethod method=material.getMaterialProperties().getMaterialProperty(DynamicViscosityProperty.class).getMethod();
                properties.append("\"viscosity_active_method\":").append(quote(method.getClass().getName()));
                if(method instanceof SutherlandLaw){SutherlandLaw law=(SutherlandLaw)method;
                    String mu=finite(law.getReferenceValue().getSIValue()),sc=finite(law.getSutherlandConstant().getSIValue()),tref=finite(law.getReferenceTemperature().getSIValue());
                    append(properties,"mu_reference_Pa_s",mu);append(properties,"sutherland_constant_K",sc);append(properties,"reference_temperature_K",tref);
                }else if(method instanceof ConstantMaterialPropertyMethod){String mu=finite(((ConstantMaterialPropertyMethod)method).getQuantity().getSIValue());append(properties,"mu_constant_Pa_s",mu);}
                else missing(missing,"viscosity_parameters","Active method unsupported: "+method.getClass().getName());
            }catch(Exception e){missing(missing,"viscosity_active_method",e.toString());}
            try{
                ScalarProfile p=inlet.getValues().get(StaticTemperatureProfile.class);
                if(p.getMethod() instanceof ConstantScalarProfileMethod){append(properties,"inlet_temperature_configuration_K",finite(p.getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue()));}
                else missing(missing,"inlet_temperature_configuration_K","Nonconstant profile: "+p.getMethod().getClass().getName());
            }catch(Exception e){missing(missing,"inlet_temperature_configuration_K",e.toString());}
            for(Boundary b:region.getBoundaryManager().getBoundaries()){
                if(conditions.length()>0)conditions.append(',');conditions.append("{\"boundary\":").append(quote(b.getQualifiedName())).append(",\"type\":").append(quote(b.getBoundaryType().getClass().getName()));
                if(b.getBoundaryType() instanceof WallBoundary){
                    try{conditions.append(",\"shear_condition\":").append(quote(b.getConditions().get(WallShearStressOption.class).getSelectedElement().name()));}catch(Exception e){missing(missing,b.getQualifiedName()+".shear_condition",e.toString());}
                    try{conditions.append(",\"tangential_motion\":").append(quote(b.getConditions().get(WallSlidingOption.class).getSelectedElement().name()));}catch(Exception e){missing(missing,b.getQualifiedName()+".tangential_motion",e.toString());}
                    try{
                        WallSurfaceOption.Type type=b.getConditions().get(WallSurfaceOption.class).getSelectedElement();
                        conditions.append(",\"roughness_condition\":").append(quote(type.name()));
                        if(type==WallSurfaceOption.Type.SMOOTH)conditions.append(",\"roughness_height_applicability\":\"not applicable to active smooth condition\"");
                        else{ScalarProfile p=b.getValues().get(RoughnessHeightProfile.class);
                            if(p.getMethod() instanceof ConstantScalarProfileMethod){String height=finite(p.getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue());append(conditions,"roughness_height_m",height);}
                            else missing(missing,b.getQualifiedName()+".roughness_height_m","Nonconstant roughness profile");}
                    }catch(Exception e){missing(missing,b.getQualifiedName()+".roughness",e.toString());}
                }conditions.append('}');
            }
            for(PartSurface ps:wall.getPartSurfaceGroup().getObjects()){
                if(ownership.length()>0)ownership.append(',');ownership.append("{\"part\":").append(quote(ps.getPart().getPresentationName())).append(",\"surface\":").append(quote(ps.getPresentationName())).append(",\"boundary\":").append(quote(wall.getQualifiedName())).append('}');
            }
            String[] ids={"WallYplus","WallDistance","WallShearStress","FrictionVelocity","DynamicViscosity","Density","Temperature","Area","PrismLayerNumber","FirstPrismLayerHeight","PrismCellThickness"};
            List<FieldFunction> fields=new ArrayList<FieldFunction>();StringBuilder columns=new StringBuilder();
            for(String id:ids){
                FieldFunction f=readbackNative(s,mesh,id);
                if(f==null){missing(missing,id,"Native function ID absent; no alias or serialization inference");continue;}
                if("WallShearStress".equals(id)||"Area".equals(id))f=f.getMagnitudeFunction();
                fields.add(f);if(columns.length()>0)columns.append(',');columns.append("{\"requested_id\":").append(quote(id)).append(",\"function_name\":").append(quote(f.getFunctionName())).append(",\"presentation_name\":").append(quote(f.getPresentationName())).append('}');
            }
            if(fields.isEmpty())throw new IllegalStateException("No native nearwall fields available");
            XyzInternalTable table=s.getTableManager().createTable(XyzInternalTable.class);
            table.setRepresentation(mesh);table.getParts().setObjects(wall);table.setExtractVertexData(false);table.setFieldFunctions(fields);
            table.extract();table.export(product(dir,"nearwall-face-values.csv").getAbsolutePath());
            int rows=table.getRowCount();if(rows<=0)throw new IllegalStateException("Empty face table");
            /* Native area integral independently checks any face-area column; never infer weights from row count. */
            UserFieldFunction one=s.getFieldFunctionManager().createFieldFunction();one.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);one.setDefinition("1");
            SurfaceIntegralReport total=s.getReportManager().createReport(SurfaceIntegralReport.class);total.setRepresentation(mesh);total.setFieldFunction(one);total.getParts().setObjects(wall);
            double actualArea=total.getReportMonitorValue();if(!Double.isFinite(actualArea)||actualArea<=0)throw new IllegalStateException("Invalid actual wall area");
            for(String id:new String[]{"Density","Temperature","DynamicViscosity"}){
                if(!s.getFieldFunctionManager().hasFunction(id)){missing(missing,"inlet_measured_"+id,"Native function ID absent");continue;}
                try{AreaAverageReport avg=s.getReportManager().createReport(AreaAverageReport.class);avg.setRepresentation(mesh);avg.getParts().setObjects(inlet);avg.setFieldFunction(s.getFieldFunctionManager().getFunction(id));
                    append(properties,"inlet_area_mean_"+id,finite(avg.getReportMonitorValue()));
                }catch(Exception e){missing(missing,"inlet_measured_"+id,e.toString());}
            }
            missing(missing,"undisturbed_point_reference","No verified undisturbed sampling point declared; inlet area mean is separate measured basis");
            try(PrintWriter p=new PrintWriter(product(dir,"available-native-fields.csv"),"UTF-8")){p.println("function_name,presentation_name");for(FieldFunction f:s.getFieldFunctionManager().getObjects())p.println(csvCell(f.getFunctionName())+","+csvCell(f.getPresentationName()));}
            String diagnostics="null";
            String diagnosticsFlag=System.getenv("STATIC_FLOW_DIAGNOSTICS");
            if(diagnosticsFlag!=null&&!"1".equals(diagnosticsFlag))throw new IllegalArgumentException("STATIC_FLOW_DIAGNOSTICS must be 1 when supplied");
            if("1".equals(diagnosticsFlag)){rejectionStage="static_flow_diagnostics";diagnostics=exportStaticDiagnostics(s,mesh,region,wall,spec,dir);}
            int end=s.getSimulationIterator().getCurrentIteration();String after=sha(input);
            boolean initializedAfter=s.getSolution().isInitialized();double timeAfter=s.getSolution().getPhysicalTime();
            append(prerequisites,"iteration_after",Integer.toString(end));append(prerequisites,"initialized_after",Boolean.toString(initializedAfter));
            append(prerequisites,"physical_time_after_s",finite(timeAfter));append(prerequisites,"input_sim_sha256_after",quote(after));
            rejectionStage="state_invariance";
            if(start!=end||!before.equals(after)||initializedBefore!=initializedAfter||cellsBefore!=mesh.getCellCount()||timeBefore!=timeAfter)throw new IllegalStateException("Read-only input, iteration, initialization, mesh or physical time changed");
            StringBuilder artifacts=new StringBuilder();for(File f:products){if(artifacts.length()>0)artifacts.append(',');artifacts.append("{\"name\":").append(quote(f.getName())).append(",\"sha256\":").append(quote(sha(f))).append(",\"bytes\":").append(f.length()).append('}');}
            Files.writeString(record.toPath(),"{\"schema_version\":1,\"status\":\"ok\",\"results_only\":true,\"input_sim_sha256\":"+quote(before)+",\"input_sim_sha256_after\":"+quote(after)+",\"case_spec_sha256\":"+quote(sha(specFile))+",\"start_iteration\":"+start+",\"end_iteration\":"+end+",\"preconditions\":{"+prerequisites+"},\"initialization_unchanged\":true,\"physical_time_unchanged\":true,\"cells\":"+mesh.getCellCount()+",\"wall_area_m2\":"+finite(actualArea)+",\"face_rows\":"+rows+",\"properties\":{"+properties+"},\"boundary_conditions\":["+conditions+"],\"body_wall_surface_ownership\":["+ownership+"],\"face_columns\":["+columns+"],\"missing\":["+missing+"],\"sampling\":\"native face-centred data, not vertex interpolation; area weights require independent sum check\",\"new_yplus_gate\":false,\"experimental_validation\":false,\"static_flow_diagnostics\":"+diagnostics+",\"artifacts\":["+artifacts+"],\"complete_marker\":\"PROPERTY_NEARWALL_READBACK_EXPORTED\"}\n",StandardCharsets.UTF_8);
            s.println("PROPERTY_NEARWALL_READBACK_EXPORTED");
        }catch(Exception e){
            try{append(prerequisites,"iteration_at_failure",Integer.toString(s.getSimulationIterator().getCurrentIteration()));}catch(Exception unavailable){append(prerequisites,"iteration_at_failure_missing",quote(unavailable.toString()));}
            try{append(prerequisites,"initialized_at_failure",Boolean.toString(s.getSolution().isInitialized()));}catch(Exception unavailable){append(prerequisites,"initialized_at_failure_missing",quote(unavailable.toString()));}
            try{append(prerequisites,"input_sim_sha256_at_failure",quote(sha(new File(s.getSessionPath()))));}catch(Exception unavailable){append(prerequisites,"input_hash_at_failure_missing",quote(unavailable.toString()));}
            try{Files.writeString(record.toPath(),"{\"schema_version\":1,\"status\":\"error\",\"results_only\":true,\"rejection_stage\":"+quote(rejectionStage)+",\"error\":"+quote(e.toString())+",\"preconditions\":{"+prerequisites+"}}\n",StandardCharsets.UTF_8);}catch(Exception ignored){}throw new RuntimeException(e);
        }
    }
    private static String finite(double value){if(!Double.isFinite(value))throw new IllegalArgumentException("Nonfinite measurement");return Double.toString(value);}
    private static void append(StringBuilder b,String key,String value){if(b.length()>0)b.append(',');b.append(quote(key)).append(':').append(value);}
    private static void missing(StringBuilder b,String key,String reason){if(b.length()>0)b.append(',');b.append("{\"field\":").append(quote(key)).append(",\"reason\":").append(quote(reason)).append('}');}
    private static String csvCell(String value){return "\""+value.replace("\"","\"\"")+"\"";}
    private static FieldFunction readbackNative(Simulation s,FvRepresentation mesh,String id){
        if(s.getFieldFunctionManager().hasFunction(id))return s.getFieldFunctionManager().getFunction(id);
        // Mesh metrics can be representation-scoped; match the exact native ID used by AuditExternalFlowMesh.
        for(FieldFunction f:s.getFieldFunctionManager().getScalarFieldFunctions(mesh))if(id.equals(f.getFunctionName()))return f;
        return null;
    }
    private String exportStaticDiagnostics(Simulation s,FvRepresentation mesh,Region region,Boundary wall,String spec,File dir)throws Exception{
        // Geometry-relative protocol shared across domain levels; original CaseSpec bytes are untouched.
        double[] origin=array(spec,"body_origin_m",3),flow=array(spec,"velocity_m_s",3),axis=array(spec,"body_axis",3),bounds=array(spec,"outer_bounds_m",6);
        double diameter=number(spec,"characteristic_length_m"),length=number(spec,"body_length_m");
        if(diameter<=0||length<=0||norm(flow)<=0||norm(axis)<=0)throw new IllegalArgumentException("Invalid diagnostic geometry or velocity");
        flow=scale(flow,1/norm(flow));axis=scale(axis,1/norm(axis));
        if(Math.abs(dot(flow,axis))>1e-8)throw new IllegalArgumentException("Diagnostic protocol requires transverse body axis");
        double[] lateral=cross(axis,flow);lateral=scale(lateral,1/norm(lateral));
        List<NamedObject> points=new ArrayList<NamedObject>();StringBuilder pointCoordinates=new StringBuilder();
        for(double[] sample:new double[][]{{1,0,0},{2,0,0},{4,0,0},{8,0,0},{12,0,0},{2,-.75,0},{2,.75,0},{4,-.75,0},{4,.75,0}}){
            double downstream=sample[0],across=sample[1],axial=sample[2];
            double[] p=new double[3];for(int k=0;k<3;k++){p[k]=origin[k]+diameter*(downstream*flow[k]+across*lateral[k])+length*axial*axis[k];if(!(p[k]>bounds[k]&&p[k]<bounds[k+3]))throw new IllegalArgumentException("Diagnostic point outside fluid-domain bounds");}
            PointPart point=s.getPartManager().createPointPart(new Vector<NamedObject>(Arrays.asList(region)),new Vector<Double>(Arrays.asList(p[0],p[1],p[2])));
            points.add(point);if(pointCoordinates.length()>0)pointCoordinates.append(',');pointCoordinates.append(Arrays.toString(p));
        }
        List<FieldFunction> fluidFields=new ArrayList<FieldFunction>();StringBuilder fluidColumns=new StringBuilder();
        for(String id:new String[]{"Pressure","Density","Volume","Velocity"}){
            FieldFunction f=readbackNative(s,mesh,id);if(f==null)throw new IllegalStateException("Required diagnostic native field absent: "+id);
            if("Velocity".equals(id))for(int k=0;k<3;k++){FieldFunction component=f.getComponentFunction(k);fluidFields.add(component);diagnosticColumn(fluidColumns,"Velocity_"+k,component);}
            else{fluidFields.add(f);diagnosticColumn(fluidColumns,id,f);}
        }
        int pointRows=diagnosticTable(s,mesh,points.toArray(new NamedObject[0]),fluidFields,dir,"diagnostic-fixed-points.csv");
        if(pointRows!=points.size())throw new IllegalStateException("Fixed diagnostic point coverage differs: "+pointRows+" / "+points.size());
        int volumeRows=diagnosticTable(s,mesh,new NamedObject[]{region},fluidFields,dir,"diagnostic-volume-cells.csv");
        if(volumeRows!=mesh.getCellCount())throw new IllegalStateException("Volume-cell diagnostic row count differs from native cell count");
        FieldFunction area=readbackNative(s,mesh,"Area");if(area==null)throw new IllegalStateException("Native diagnostic face Area absent");
        List<FieldFunction> boundaryFields=new ArrayList<FieldFunction>(fluidFields);boundaryFields.add(area.getMagnitudeFunction());
        StringBuilder boundaryColumns=new StringBuilder(fluidColumns);diagnosticColumn(boundaryColumns,"Area",area.getMagnitudeFunction());
        StringBuilder boundaries=new StringBuilder();int index=0;
        for(Boundary boundary:region.getBoundaryManager().getBoundaries()){
            if(boundary==wall)continue;
            if(boundary.getBoundaryType() instanceof WallBoundary||!boundary.getDependentInterfaces().isEmpty())throw new IllegalStateException("Unexpected outer diagnostic boundary role");
            String file="diagnostic-boundary-"+(index++)+".csv";
            int rows=diagnosticTable(s,mesh,new NamedObject[]{boundary},boundaryFields,dir,file);
            if(boundaries.length()>0)boundaries.append(',');boundaries.append("{\"boundary\":").append(quote(boundary.getQualifiedName())).append(",\"type\":").append(quote(boundary.getBoundaryType().getClass().getName())).append(",\"file\":").append(quote(file)).append(",\"face_rows\":").append(rows).append('}');
        }
        StringBuilder configurations=new StringBuilder();
        for(MeshOperation operation:s.get(MeshOperationManager.class).getObjects())if(operation instanceof AutoMeshOperation){
            AutoMeshOperation auto=(AutoMeshOperation)operation;
            String operationValue=quote(auto.getQualifiedName());
            if(configurations.length()>0)configurations.append(',');configurations.append("{\"operation\":").append(operationValue);
            try{
                PrismThickness thickness=auto.getDefaultValues().get(PrismThickness.class);
                // Complete each getter/conversion before appending its key; retain prior fields on failure.
                String baseValue=finite(auto.getDefaultValues().get(BaseSize.class).getSIValue());
                configurations.append(",\"status\":\"CONFIGURATION_ONLY\",\"base_size_m\":").append(baseValue);
                int layerValue=auto.getDefaultValues().get(NumPrismLayers.class).getNumLayers();
                configurations.append(",\"prism_layers_configured\":").append(layerValue);
                String thicknessMode=quote(thickness.getRelativeOrAbsoluteOption().getSelectedElement().name());
                configurations.append(",\"prism_thickness_mode\":").append(thicknessMode);
                String absoluteValue=finite(thickness.getAbsoluteSizeValue().getSIValue());
                configurations.append(",\"prism_thickness_absolute_store_m\":").append(absoluteValue);
                String relativeValue=finite(thickness.getRelativeSizeScalar().getValue());
                configurations.append(",\"prism_thickness_relative_percent\":").append(relativeValue);
                String stretchingValue=finite(auto.getDefaultValues().get(PrismLayerStretching.class).getStretching());
                configurations.append(",\"prism_stretching_configured\":").append(stretchingValue);
            }catch(Exception unavailable){String missingValue=quote(unavailable.toString());configurations.append(",\"missing\":").append(missingValue);}
            configurations.append('}');
        }
        return "{\"protocol\":\"geometry_relative_transverse_wake_v1\",\"point_coordinates_m\":["+pointCoordinates+"],\"point_rows\":"+pointRows+",\"volume_rows\":"+volumeRows+",\"fluid_columns\":["+fluidColumns+"],\"boundary_columns\":["+boundaryColumns+"],\"boundaries\":["+boundaries+"],\"mesh_operation_configuration\":["+configurations+"],\"sampling_basis\":\"Fixed point fields are STAR point sampling, not cell-centre measurements. Separate volume table contains native cell-centred Volume for local scale diagnostics; cbrt(Volume) is a derived isotropic proxy, not wall-normal distance. Boundary tables are native face-centred samples for area-weighted perturbation and signed outlet velocity. Mesh operation values are configuration, not realised thickness. No new acceptance gate.\"}";
    }
    private static void diagnosticColumn(StringBuilder columns,String id,FieldFunction field){
        if(columns.length()>0)columns.append(',');columns.append("{\"requested_id\":").append(quote(id)).append(",\"function_name\":").append(quote(field.getFunctionName())).append(",\"presentation_name\":").append(quote(field.getPresentationName())).append('}');
    }
    private int diagnosticTable(Simulation s,FvRepresentation mesh,NamedObject[] parts,List<FieldFunction> fields,File dir,String name)throws Exception{
        XyzInternalTable table=s.getTableManager().createTable(XyzInternalTable.class);table.setRepresentation(mesh);table.getParts().setObjects(parts);table.setExtractVertexData(false);table.setFieldFunctions(fields);
        table.extract();int rows=table.getRowCount();if(rows<=0)throw new IllegalStateException("Empty native diagnostic table: "+name);
        table.export(product(dir,name).getAbsolutePath());return rows;
    }

    /* MRF results branch: roles and coordinates come from the unmodified CaseSpec.
     * Interface parent boundaries can still have WallBoundary type; exclude them explicitly. */
    private void executeMrf(){
        Simulation s=getActiveSimulation();File dir=s.getSessionDirFile(),record=new File(dir,"external_flow_export.json");
        try{
            products.clear();File input=new File(s.getSessionPath());String before=sha(input);
            File specFile=new File(dir,"case_spec.json");String spec=Files.readString(specFile.toPath(),StandardCharsets.UTF_8);
            if(!"m".equals(text(spec,"model_length_unit")))throw new IllegalArgumentException("Requires SI metre CaseSpec");
            int start=s.getSimulationIterator().getCurrentIteration();
            if(start!=(int)number(spec,"max_iterations")||start<=0)throw new IllegalStateException("Iteration differs from declared finite segment");
            Region rotor=s.getRegionManager().getRegion(text(spec,"rotor_region")),stator=s.getRegionManager().getRegion(text(spec,"stator_region"));
            if(rotor==null||stator==null||rotor==stator)throw new IllegalStateException("Missing distinct MRF fluid roles");
            String rotation=raw(spec,"rotation","\\{[^{}]*\\}");
            double[] axis=array(rotation,"axis",3),origin=array(rotation,"center_m",3);axis=scale(axis,1/norm(axis));
            ReferenceFrameBase attached=rotor.getValues().get(MotionSpecification.class).getReferenceFrame();
            if(!(attached instanceof UserRotatingReferenceFrame)||!attached.getPresentationName().equals(text(spec,"reference_frame_name")))throw new IllegalStateException("Unexpected actual rotating frame");
            UserRotatingReferenceFrame frame=(UserRotatingReferenceFrame)attached;
            sameVector(axis,scale(frame.getAxisVector().getSIVector().toDoubleArray(),1/norm(frame.getAxisVector().getSIVector().toDoubleArray())));
            sameVector(origin,frame.getOriginVector().getSIVector().toDoubleArray());
            double omega=frame.getRotationRate().getSIValue();
            if(Math.abs(omega-number(rotation,"rpm")*Math.PI/30)>1e-9)throw new IllegalStateException("Rotation differs from CaseSpec");
            AbstractReferenceFrame lab=null;
            for(AbstractReferenceFrame f:s.get(ReferenceFrameManager.class).getObjects())if(f instanceof LabReferenceFrame){if(lab!=null)throw new IllegalStateException("Ambiguous laboratory frame");lab=f;}
            if(lab==null)throw new IllegalStateException("Laboratory frame absent");
            Set<Boundary> interfaceParents=new HashSet<Boundary>();
            for(Interface i:s.getInterfaceManager().getObjects())if(i instanceof BoundaryInterface){interfaceParents.add(((BoundaryInterface)i).getParentBoundary0());interfaceParents.add(((BoundaryInterface)i).getParentBoundary1());}
            List<NamedObject> walls=new ArrayList<NamedObject>();String fluidPart=text(spec,"rotating_fluid_part");
            StringBuilder boundaryJson=new StringBuilder();
            for(Boundary b:rotor.getBoundaryManager().getBoundaries()){
                if(!(b.getBoundaryType() instanceof WallBoundary)||interfaceParents.contains(b)||!b.getDependentInterfaces().isEmpty())continue;
                if(b.getPartSurfaceGroup().getObjects().isEmpty())throw new IllegalStateException("Wall lacks surface ownership");
                for(PartSurface ps:b.getPartSurfaceGroup().getObjects())if(!fluidPart.equals(ps.getPart().getPresentationName()))throw new IllegalStateException("Wall surface not owned by rotating fluid role");
                walls.add(b);if(boundaryJson.length()>0)boundaryJson.append(',');boundaryJson.append(quote(b.getQualifiedName()));
            }
            if(walls.isEmpty())throw new IllegalStateException("No material wall after interface exclusion");
            FvRepresentation mesh=null;
            for(Representation r:s.getRepresentationManager().getObjects())if(r instanceof FvRepresentation&&((FvRepresentation)r).getCellCount()>0){if(mesh!=null)throw new IllegalStateException("Ambiguous volume representations");mesh=(FvRepresentation)r;}
            if(mesh==null)throw new IllegalStateException("Volume mesh absent");long cells=mesh.getCellCount();
            boolean initialized=s.getSolution().isInitialized();double physicalTime=s.getSolution().getPhysicalTime();
            double referencePressure=rotor.getPhysicsContinuum().getReferenceValues().get(ReferencePressure.class).getSIValue();
            if(!Double.isFinite(referencePressure)||referencePressure!=stator.getPhysicsContinuum().getReferenceValues().get(ReferencePressure.class).getSIValue())throw new IllegalStateException("Inconsistent pressure reference");
            FieldFunction pressure=s.getFieldFunctionManager().getFunction("Pressure");
            FieldFunction velocity=s.getFieldFunctionManager().getFunction("Velocity");
            FieldFunction absolute=velocity.getFunctionInReferenceFrame(lab),relative=velocity.getFunctionInReferenceFrame(frame);
            double[] aux=new double[3];int least=0;for(int i=1;i<3;i++)if(Math.abs(axis[i])<Math.abs(axis[least]))least=i;aux[least]=1;
            double[] normal=cross(axis,aux);normal=scale(normal,1/norm(normal));double[] up=axis;
            PlaneSection plane=s.getPartManager().createImplicitPart(Arrays.asList(stator,rotor),Arrays.asList(normal[0],normal[1],normal[2]),Arrays.asList(origin[0],origin[1],origin[2]));
            PlaneSection rotatingPlane=s.getPartManager().createImplicitPart(Arrays.asList(rotor),Arrays.asList(normal[0],normal[1],normal[2]),Arrays.asList(origin[0],origin[1],origin[2]));
            List<FieldFunction> absFields=Arrays.asList(pressure,absolute.getMagnitudeFunction(),absolute.getComponentFunction(0),absolute.getComponentFunction(1),absolute.getComponentFunction(2));
            List<FieldFunction> relativeFields=Arrays.asList(pressure,absolute.getMagnitudeFunction(),relative.getMagnitudeFunction(),relative.getComponentFunction(0),relative.getComponentFunction(1),relative.getComponentFunction(2));
            int rows=exportTable(s,mesh,Arrays.asList(plane),absFields,product(dir,"flow-centre-section.csv"));
            int relativeRows=exportTable(s,mesh,Arrays.asList(rotatingPlane),relativeFields,product(dir,"rotating-section.csv"));
            int wallRows=exportTable(s,mesh,walls,Arrays.asList(pressure,absolute.getMagnitudeFunction(),relative.getMagnitudeFunction()),product(dir,"blade-wall.csv"));
            double[] solidBounds=array(spec,"recovered_solid_bounds_m",6);double size=0;for(int i=0;i<3;i++)size=Math.max(size,solidBounds[i+3]-solidBounds[i]);if(size<=0)throw new IllegalArgumentException("Invalid material bounds");
            String warning=start+"迭代、未收敛、未经工程验证";
            sceneMrf(s,mesh,Arrays.asList(plane),pressure,origin,normal,up,size*.8,product(dir,"pressure-section.png"),warning,"Pressure [Pa], reference="+referencePressure+" Pa");
            sceneMrf(s,mesh,Arrays.asList(plane),absolute.getMagnitudeFunction(),origin,normal,up,size*1.6,product(dir,"velocity-absolute-section.png"),warning,"Absolute velocity in laboratory frame [m/s]");
            sceneMrf(s,mesh,Arrays.asList(rotatingPlane),relative.getMagnitudeFunction(),origin,normal,up,size*.8,product(dir,"velocity-relative-section.png"),warning,"Relative velocity in "+frame.getPresentationName()+" [m/s]");
            sceneMrf(s,mesh,walls,pressure,origin,axis,aux,size*.65,product(dir,"pressure-blade-front.png"),warning,"Material wall Pressure [Pa], reference="+referencePressure+" Pa");
            sceneMrf(s,mesh,walls,pressure,origin,scale(axis,-1),aux,size*.65,product(dir,"pressure-blade-back.png"),warning,"Opposite material wall Pressure [Pa]");
            sceneMrf(s,mesh,walls,absolute.getMagnitudeFunction(),origin,axis,aux,size*.65,product(dir,"velocity-blade-wall.png"),warning,"Material wall absolute velocity [m/s]");
            double[] forces=new double[3],moments=new double[3];
            for(int k=0;k<3;k++){
                double[] direction=new double[3];direction[k]=1;
                ForceReport f=s.getReportManager().createReport(ForceReport.class);f.setRepresentation(mesh);f.setUseIdealization(false);f.getParts().setObjects(walls.toArray(new NamedObject[0]));f.getDirection().setComponentsAndUnits(direction[0],direction[1],direction[2],s.getUnitsManager().getObject(""));forces[k]=f.getReportMonitorValue();
                MomentReport m=s.getReportManager().createReport(MomentReport.class);m.setRepresentation(mesh);m.setUseIdealization(false);m.getParts().setObjects(walls.toArray(new NamedObject[0]));m.getDirection().setComponentsAndUnits(direction[0],direction[1],direction[2],s.getUnitsManager().getObject(""));m.getOrigin().setComponentsAndUnits(origin[0],origin[1],origin[2],s.getUnitsManager().getObject("m"));moments[k]=m.getReportMonitorValue();
                if(!Double.isFinite(forces[k])||!Double.isFinite(moments[k]))throw new IllegalStateException("Nonfinite wall loads");
            }
            double thrust=dot(forces,axis),torque=dot(moments,axis);
            try(PrintWriter p=new PrintWriter(product(dir,"force-torque.csv"),"UTF-8")){p.println("iteration,Fx_N,Fy_N,Fz_N,Mx_Nm,My_Nm,Mz_Nm,thrust_along_axis_N,torque_about_axis_Nm,convergence_verified,engineering_verified");p.println(start+","+forces[0]+","+forces[1]+","+forces[2]+","+moments[0]+","+moments[1]+","+moments[2]+","+thrust+","+torque+",false,false");}
            try(PrintWriter p=new PrintWriter(product(dir,"checkpoint-monitors.csv"),"UTF-8")){
                p.println("kind,name,iteration,value");
                for(Monitor monitor:s.getMonitorManager().getObjects())if(monitor instanceof ResidualMonitor){File raw=product(dir,"monitor-"+products.size()+".csv");((ResidualMonitor)monitor).export(raw.getAbsolutePath());for(String row:Files.readAllLines(raw.toPath(),StandardCharsets.UTF_8)){String[] c=row.replace("\"", "").split(",");if(c.length!=2)continue;try{double x=Double.parseDouble(c[0].trim()),y=Double.parseDouble(c[1].trim());if(x>=1&&x<=start&&Double.isFinite(y))p.println("residual,"+quote(monitor.getPresentationName())+","+x+","+y);}catch(NumberFormatException header){}}}
            }
            File history=new File(dir,"air3b_segment_e2e_history.csv");if(!history.isFile())history=new File(dir,"source_load_history.csv");
            if(!history.isFile())throw new IllegalStateException("Existing finite-segment load history absent");Files.copy(history.toPath(),product(dir,"load-history.csv").toPath());
            int end=s.getSimulationIterator().getCurrentIteration();String after=sha(input);
            if(start!=end||!before.equals(after)||cells!=mesh.getCellCount()||initialized!=s.getSolution().isInitialized()||physicalTime!=s.getSolution().getPhysicalTime())throw new IllegalStateException("Checkpoint numerical state changed");
            StringBuilder artifacts=new StringBuilder();for(File f:products){if(!f.isFile()||f.length()==0)throw new IllegalStateException("Missing export "+f.getName());if(artifacts.length()>0)artifacts.append(',');artifacts.append("{\"name\":").append(quote(f.getName())).append(",\"sha256\":").append(quote(sha(f))).append(",\"bytes\":").append(f.length()).append('}');}
            Files.writeString(record.toPath(),"{\"schema_version\":2,\"status\":\"ok\",\"results_only\":true,\"export_kind\":\"MRF\",\"input_sim_name\":"+quote(input.getName())+",\"input_sim_sha256\":"+quote(before)+",\"input_sim_sha256_after\":"+quote(after)+",\"case_spec_sha256\":"+quote(sha(specFile))+",\"start_iteration\":"+start+",\"end_iteration\":"+end+",\"cells\":"+cells+",\"initialization_unchanged\":true,\"physical_time_unchanged\":true,\"section_origin_m\":"+Arrays.toString(origin)+",\"section_normal_unit\":"+Arrays.toString(normal)+",\"section_rows\":"+rows+",\"relative_section_rows\":"+relativeRows+",\"wall_rows\":"+wallRows+",\"material_walls\":["+boundaryJson+"],\"wall_count\":"+walls.size()+",\"excluded_interface_parents\":"+interfaceParents.size()+",\"axis\":"+Arrays.toString(axis)+",\"omega_rad_s\":"+omega+",\"reference_pressure_pa\":"+referencePressure+",\"pressure_basis\":\"Native STAR Pressure [Pa], solver reference pressure separately measured; not stagnation pressure\",\"absolute_velocity_frame\":"+quote(lab.getPresentationName())+",\"relative_velocity_frame\":"+quote(frame.getPresentationName())+",\"thrust_N\":"+thrust+",\"torque_Nm\":"+torque+",\"coefficients_exported\":false,\"convergence_verified\":false,\"engineering_verified\":false,\"experimental_validation\":false,\"history_basis\":\"Original finite-segment report history; includes original report boundary set, distinct from newly selected material-wall loads\",\"warning_label\":"+quote(warning)+",\"artifacts\":["+artifacts+"],\"complete_marker\":\"EXTERNAL_FLOW_RESULTS_EXPORTED\"}\n",StandardCharsets.UTF_8);
            s.println("EXTERNAL_FLOW_RESULTS_EXPORTED");
        }catch(Exception e){try{Files.writeString(record.toPath(),"{\"status\":\"error\",\"error\":"+quote(e.toString())+"}\n",StandardCharsets.UTF_8);}catch(Exception ignored){}throw new RuntimeException(e);}
    }
    private static double dot(double[] a,double[] b){double v=0;for(int i=0;i<3;i++)v+=a[i]*b[i];return v;}
    private static void sameVector(double[] a,double[] b){for(int i=0;i<3;i++)if(!Double.isFinite(b[i])||Math.abs(a[i]-b[i])>1e-10)throw new IllegalStateException("Vector differs from declared CaseSpec");}
    private int exportTable(Simulation s,FvRepresentation mesh,List<? extends NamedObject> parts,List<FieldFunction> fields,File out){XyzInternalTable table=s.getTableManager().createTable(XyzInternalTable.class);table.setRepresentation(mesh);table.getParts().setObjects(parts.toArray(new NamedObject[0]));table.setExtractVertexData(true);table.setFieldFunctions(fields);table.extract();if(table.getRowCount()<=0)throw new IllegalStateException("Empty exported table");table.export(out.getAbsolutePath());return table.getRowCount();}
    private void sceneMrf(Simulation s,FvRepresentation mesh,List<? extends NamedObject> parts,FieldFunction field,double[] focus,double[] normal,double[] up,double scale,File file,String warning,String basis)throws Exception{
        Scene scene=s.getSceneManager().createScene("RESULT_EXPORT_"+file.getName());ScalarDisplayer d=scene.getDisplayerManager().createScalarDisplayer("Field");d.setRepresentation(mesh);d.getParts().setObjects(parts.toArray(new NamedObject[0]));d.getScalarDisplayQuantity().setFieldFunction(field);scene.initializeAndWait();double[] camera=new double[3];for(int i=0;i<3;i++)camera[i]=focus[i]+10*scale*normal[i];scene.getCurrentView().setInput(new DoubleVector(focus),new DoubleVector(camera),new DoubleVector(up),scale,1);scene.printAndWait(file.getAbsolutePath(),1,1600,1000,true,false);scene.close();
        // Add a factual caption above the native raster; no field pixel or scalar value is altered.
        java.awt.image.BufferedImage raw=javax.imageio.ImageIO.read(file);if(raw==null)throw new IOException("Invalid native scene PNG");
        java.awt.Font font=null;for(String name:java.awt.GraphicsEnvironment.getLocalGraphicsEnvironment().getAvailableFontFamilyNames()){java.awt.Font candidate=new java.awt.Font(name,java.awt.Font.PLAIN,22);if(candidate.canDisplayUpTo(warning)<0){font=candidate;break;}}if(font==null)throw new IllegalStateException("No font can render required Chinese warning");
        java.awt.image.BufferedImage labelled=new java.awt.image.BufferedImage(raw.getWidth(),raw.getHeight()+78,java.awt.image.BufferedImage.TYPE_INT_RGB);java.awt.Graphics2D g=labelled.createGraphics();g.setColor(java.awt.Color.WHITE);g.fillRect(0,0,labelled.getWidth(),labelled.getHeight());g.drawImage(raw,0,78,null);g.setFont(font);g.setColor(new java.awt.Color(160,25,25));g.drawString(warning,20,30);g.setColor(java.awt.Color.BLACK);g.setFont(new java.awt.Font("SansSerif",java.awt.Font.PLAIN,18));g.drawString(basis,20,60);g.dispose();if(!javax.imageio.ImageIO.write(labelled,"png",file))throw new IOException("Labelled PNG write failed");
    }
    private File product(File dir,String name){File f=new File(dir,name);products.add(f);return f;}
    private void scene(Simulation s,FvRepresentation mesh,NamedObject part,FieldFunction field,double[] focus,double[] normal,double[] up,double scale,File file){
        Scene scene=s.getSceneManager().createScene("RESULT_EXPORT_"+file.getName());
        ScalarDisplayer d=scene.getDisplayerManager().createScalarDisplayer("Field");d.setRepresentation(mesh);d.getParts().setObjects(part);d.getScalarDisplayQuantity().setFieldFunction(field);
        scene.initializeAndWait();double[] camera=new double[3];for(int i=0;i<3;i++)camera[i]=focus[i]+10*scale*normal[i];
        scene.getCurrentView().setInput(new DoubleVector(focus),new DoubleVector(camera),new DoubleVector(up),scale,1);
        scene.printAndWait(file.getAbsolutePath(),1,1400,900,true,false);scene.close();
    }
    private static String raw(String s,String key,String pattern){Matcher m=Pattern.compile("\""+Pattern.quote(key)+"\"\\s*:\\s*("+pattern+")").matcher(s);if(!m.find())throw new IllegalArgumentException("Missing CaseSpec "+key);String v=m.group(1);if(m.find())throw new IllegalArgumentException("Ambiguous CaseSpec "+key);return v;}
    private static String text(String s,String key){String v=raw(s,key,"\"(?:[^\"\\\\]|\\\\.)*\"");if(v.contains("\\"))throw new IllegalArgumentException("Escaped identifiers unsupported");return v.substring(1,v.length()-1);}
    private static double number(String s,String key){double v=Double.parseDouble(raw(s,key,"[-+0-9.eE]+"));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+key);return v;}
    private static double[] array(String s,String key,int n){String v=raw(s,key,"\\[[^\\]]*\\]");String[] a=v.substring(1,v.length()-1).split(",");if(a.length!=n)throw new IllegalArgumentException("Shape "+key);double[] r=new double[n];for(int i=0;i<n;i++){r[i]=Double.parseDouble(a[i].trim());if(!Double.isFinite(r[i]))throw new IllegalArgumentException("Nonfinite "+key);}return r;}
    private static double norm(double[] a){double sum=0;for(double v:a)sum+=v*v;return Math.sqrt(sum);}
    private static double[] scale(double[] a,double k){return new double[]{a[0]*k,a[1]*k,a[2]*k};}
    private static double[] cross(double[] a,double[] b){return new double[]{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]};}
    private static String quote(String s){StringBuilder b=new StringBuilder("\"");for(char c:s.toCharArray()){if(c=='\"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
    private static String sha(File f)throws Exception{MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(f)){byte[] b=new byte[1048576];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}StringBuilder s=new StringBuilder();for(byte b:d.digest())s.append(String.format("%02x",b&255));return s.toString();}
}
