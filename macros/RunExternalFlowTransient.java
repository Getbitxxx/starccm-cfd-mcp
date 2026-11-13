/* Isolated URANS operation. status=ok certifies execution/export only, never track E.
 * Installed 2502 API evidence: test-evidence/e-static-sphere-urans-20261020/API-2502.json.
 * One step(1) advances ONE physical step, with a fixed maximum of inner iterations.
 * Every inner residual is retained; a step is statistically eligible only if EVERY active
 * equation residual falls >= 3 decades from its first to last NEW sample in that step.
 * No residual from the loaded steady history is counted. No wall-clock stop exists here.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.NamedObject;
import star.base.report.*;
import star.common.*;
import star.flow.*;

public class RunExternalFlowTransient extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_TRANSIENT_COMPLETE";
    private static final String[] EQUATIONS = {"continuity", "x_momentum", "y_momentum",
        "z_momentum", "energy", "Tke", "Sdr"};

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File record = new File(dir, "external_flow_transient.json");
        long began = System.nanoTime();
        try {
            Map<String,Object> spec = object(new Json(new String(Files.readAllBytes(
                new File(dir,"case_spec.json").toPath()), StandardCharsets.UTF_8)).parse());
            Config cfg = new Config(spec, System.getenv()); // validate BEFORE touching physics
            Region region = sim.getRegionManager().getRegion(text(spec,"region_name"));
            Boundary wall = region.getBoundaryManager().getBoundary(text(spec,"body_wall_boundary_name"));
            FvRepresentation mesh = null;
            for (Representation r : sim.getRepresentationManager().getObjects())
                if (r instanceof FvRepresentation && ((FvRepresentation)r).describesRegion(region))
                    mesh = (FvRepresentation)r;
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No region volume mesh");
            PhysicsContinuum physics = region.getPhysicsContinuum();
            if (physics == null) throw new IllegalStateException("Region has no physics continuum");
            // This campaign starts from audited steady snapshots, never from a transient continuation.
            physics.disableModel(physics.getModelManager().getModel(SteadyModel.class));
            physics.enable(ImplicitUnsteadyModel.class);
            ImplicitUnsteadySolver solver = sim.getSolverManager().getSolver(ImplicitUnsteadySolver.class);
            solver.getTimeStep().setValue(cfg.dt);
            solver.getTimeDiscretizationOption().setSelected(TimeDiscretizationOption.SECOND_ORDER);
            InnerIterationStoppingCriterion inner = (InnerIterationStoppingCriterion)
                sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Inner Iterations");
            for (SolverStoppingCriterion c:sim.getSolverStoppingCriterionManager().getObjects()) c.setIsUsed(false);
            inner.setMaximumNumberInnerIterations(cfg.inner); inner.setIsUsed(true);
            // Loaded steady stopping criteria must not stop at iteration 600 or use steady residual gates.
            StepStoppingCriterion maximum = (StepStoppingCriterion)
                sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
            maximum.setIsUsed(false);
            double startTime = sim.getSolution().getPhysicalTime();
            if (startTime != 0.0) throw new IllegalStateException("This campaign requires a fresh zero physical clock");
            PhysicalTimeStoppingCriterion physical = (PhysicalTimeStoppingCriterion)
                sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Physical Time");
            physical.getMaximumTime().setValue(startTime + cfg.steps*cfg.dt); physical.setIsUsed(true);
            if (!sim.getSolution().isInitialized()) sim.getSolution().initializeSolution();

            ForceReport drag = force(sim,wall,"URANS_DRAG_X",1,0,0);
            ForceReport liftY = force(sim,wall,"URANS_LIFT_Y",0,1,0);
            ForceReport liftZ = force(sim,wall,"URANS_LIFT_Z",0,0,1);
            Map<String,ScalarReport> flows = new LinkedHashMap<String,ScalarReport>();
            for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                MassFlowReport f = (MassFlowReport)sim.getReportManager().create("star.flow.MassFlowReport");
                f.setPresentationName("URANS_MASS_"+flows.size());f.setUseIdealization(false);
                f.setRepresentation(mesh);f.getParts().setObjects(boundary);
                flows.put(boundary.getPresentationName(), f);
            }
            double density = density(sim,mesh,region);
            double u2=0;for(Object v:list(object(spec.get("flow")).get("velocity_m_s")))u2+=square(numeric(v));
            double area=positive(object(spec.get("geometry")),"reference_area_m2");
            double scale=0.5*density*u2*area;
            if (!(scale>0) || !Double.isFinite(scale))throw new IllegalStateException("Invalid coefficient scale");
            Map<String,ResidualMonitor> residuals = new LinkedHashMap<String,ResidualMonitor>();
            for (Monitor m:sim.getMonitorManager().getObjects()) if(m instanceof ResidualMonitor) {
                ResidualMonitor r=(ResidualMonitor)m;String key=equation(r.getPresentationName());
                if(key!=null && r.getActive()) {
                    if(residuals.put(key,r)!=null)throw new IllegalStateException("Duplicate residual "+key);
                    r.setEnabled(true);r.setUpdateFrequency(1);
                }
            }
            for(String key:EQUATIONS) if(!residuals.containsKey(key))
                throw new IllegalStateException("Missing active residual "+key);
            int startIteration=sim.getSimulationIterator().getCurrentIteration();
            int completed=0, eligible=0, saves=0;
            try(Writer history=writer(new File(dir,"external_flow_transient_history.csv"));
                Writer detail=writer(new File(dir,"external_flow_transient_inner.csv"))) {
                history.write("physical_time_s,time_step,iteration,Cd,Cl_y,Cl_z,continuity,x_momentum,y_momentum,z_momentum,energy,Tke,Sdr,mass_imbalance,min_inner_decay_orders,eligible_statistics\n");
                detail.write("time_step,physical_time_s,equation,sample,iteration,residual\n");
                for(int step=1;step<=cfg.steps;step++) {
                    Map<String,Integer> counts=new LinkedHashMap<String,Integer>();
                    for(String key:EQUATIONS)counts.put(key,residuals.get(key).getAllYValues().length);
                    double before=sim.getSolution().getPhysicalTime();
                    sim.getSimulationIterator().step(1);
                    double t=sim.getSolution().getPhysicalTime();
                    if(!Double.isFinite(t)||Math.abs((t-before)-cfg.dt)>Math.max(1e-12,cfg.dt*1e-6))
                        throw new IllegalStateException("Physical step failed dt readback");
                    double minDecay=Double.POSITIVE_INFINITY;
                    StringBuilder row=new StringBuilder();
                    for(String key:EQUATIONS) {
                        ResidualMonitor m=residuals.get(key);MonitorData samples=m.getAllSamples();
                        double[] ys=samples.getYValues(),xs=samples.getXValues();
                        int first=counts.get(key);int count=ys.length-first;
                        if(count<2 || xs.length!=ys.length)throw new IllegalStateException("Missing inner history "+key);
                        double a=ys[first],z=ys[ys.length-1];
                        for(int k=first;k<ys.length;k++) {
                            if(!Double.isFinite(ys[k])||ys[k]<0)throw new IllegalStateException("Non-finite/negative residual "+key);
                            detail.write(step+","+t+","+key+","+(k-first+1)+","+xs[k]+","+ys[k]+"\n");
                        }
                        double decay=decades(a,z);minDecay=Math.min(minDecay,decay);row.append(',').append(z);
                    }
                    double cd=drag.getReportMonitorValue()/scale,cy=liftY.getReportMonitorValue()/scale,
                        cz=liftZ.getReportMonitorValue()/scale,imbalance=imbalance(flows);
                    if(!Double.isFinite(cd)||!Double.isFinite(cy)||!Double.isFinite(cz)||!Double.isFinite(imbalance))
                        throw new IllegalStateException("Non-finite loads or mass imbalance");
                    boolean usable=minDecay>=3.0;eligible+=usable?1:0;completed=step;
                    history.write(t+","+step+","+sim.getSimulationIterator().getCurrentIteration()+","+cd+","+cy+","+cz
                        +row+","+imbalance+","+minDecay+","+usable+"\n");history.flush();detail.flush();
                    if(step%cfg.checkpoint==0 || step==cfg.steps) {
                        sim.saveState(new File(dir,"external_flow_transient.sim").getAbsolutePath());saves++;
                        write(new File(dir,"external_flow_transient_checkpoint.json"),"{\"schema_version\":1,\"status\":\"checkpoint\",\"time_step\":"+step
                            +",\"physical_time_s\":"+t+",\"iteration\":"+sim.getSimulationIterator().getCurrentIteration()
                            +",\"checkpoint\":\"external_flow_transient.sim\",\"stage_success_certified\":false}\n");
                    }
                }
            }
            write(record,"{\"schema_version\":1,\"status\":\"ok\",\"complete_marker\":\""+COMPLETE
                +"\",\"engineering_verified\":false,\"wall_clock_stop_enabled\":false,\"stop_reason\":\"physical_step_cap\""
                +",\"time_step_s\":"+cfg.dt+",\"physical_time_s\":"+sim.getSolution().getPhysicalTime()
                +",\"steps_completed\":"+completed+",\"eligible_statistics_steps\":"+eligible
                +",\"inner_iteration_cap\":"+cfg.inner+",\"checkpoint_saves\":"+saves+",\"checkpoint_every_steps\":"+cfg.checkpoint
                +",\"start_iteration\":"+startIteration+",\"end_iteration\":"+sim.getSimulationIterator().getCurrentIteration()
                +",\"cell_count\":"+mesh.getCellCount()+",\"density_kg_m3\":"+density
                +",\"macro_elapsed_seconds\":"+((System.nanoTime()-began)/1e9)
                +",\"residual_basis\":\"first and last NEW inner samples of every active equation; all inner samples exported; steady gates not applied\"}\n");
            System.out.println(COMPLETE);
        } catch(Throwable error) {
            try { write(record,"{\"schema_version\":1,\"status\":\"error\",\"engineering_verified\":false,\"error\":"+quote(error.toString())+"}\n"); }
            catch(Throwable ignored) { }
            throw new RuntimeException("External-flow transient failed",error);
        }
    }
    private static ForceReport force(Simulation s,Boundary b,String name,double x,double y,double z) {
        ForceReport f=(ForceReport)s.getReportManager().create("star.flow.ForceReport");
        f.setPresentationName(name);f.setUseIdealization(false);f.getParts().setObjects(new NamedObject[]{b});
        f.getDirection().setComponentsAndUnits(x,y,z,s.getUnitsManager().getObject(""));return f;
    }
    private static double density(Simulation s,FvRepresentation mesh,Region region) {
        FieldFunction field=null;for(FieldFunction f:s.getFieldFunctionManager().getScalarFieldFunctions(mesh))
            if("Density".equals(f.getFunctionName()))field=f;
        if(field==null)throw new IllegalStateException("Density unavailable");
        VolumeAverageReport r=s.getReportManager().createReport(VolumeAverageReport.class);
        r.setRepresentation(mesh);r.setFieldFunction(field);r.getParts().setObjects(region);
        double value=r.getReportMonitorValue();s.deleteObjects(Collections.singletonList(r));
        if(!Double.isFinite(value)||value<=0)throw new IllegalStateException("Invalid density");return value;
    }
    private static double imbalance(Map<String,ScalarReport> flows) {
        double sum=0,scale=0;for(ScalarReport r:flows.values()){double v=r.getReportMonitorValue();
            if(!Double.isFinite(v))return Double.NaN;sum+=v;scale=Math.max(scale,Math.abs(v));}
        return scale>0?Math.abs(sum)/scale:Double.NaN;
    }
    private static String equation(String name) {
        String s=name.toLowerCase(Locale.ROOT).replaceAll("[^a-z]","");
        if(s.equals("continuity"))return "continuity";
        if(s.equals("xmomentum"))return "x_momentum";if(s.equals("ymomentum"))return "y_momentum";
        if(s.equals("zmomentum"))return "z_momentum";if(s.equals("energy"))return "energy";
        if(s.equals("tke"))return "Tke";if(s.equals("sdr"))return "Sdr";return null;
    }
    private static Writer writer(File f)throws Exception{return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(f),StandardCharsets.UTF_8));}
    private static void write(File f,String text)throws Exception{try(Writer w=writer(f)){w.write(text);}}

    // BEGIN PURE CONTRACT -- compiled and executed by the offline negative tests, no STAR stubs.
    static double square(double v){return v*v;}
    static double decades(double a,double b){
        if(!Double.isFinite(a)||!Double.isFinite(b)||a<=0||b<0)return -1e300;
        return b==0?300:Math.log10(a)-Math.log10(b);
    }
    @SuppressWarnings("unchecked") static Map<String,Object> object(Object v){
        if(!(v instanceof Map))throw new IllegalArgumentException("Expected JSON object");return (Map<String,Object>)v;}
    @SuppressWarnings("unchecked") static List<Object> list(Object v){
        if(!(v instanceof List))throw new IllegalArgumentException("Expected JSON list");return (List<Object>)v;}
    static String text(Map<String,Object> m,String key){Object v=m.get(key);if(!(v instanceof String)||((String)v).isEmpty())
        throw new IllegalArgumentException("Missing string "+key);return (String)v;}
    static double numeric(Object v){if(!(v instanceof Number)||!Double.isFinite(((Number)v).doubleValue()))
        throw new IllegalArgumentException("Expected finite number");return ((Number)v).doubleValue();}
    static double positive(Map<String,Object> m,String key){double v=numeric(m.get(key));if(v<=0)throw new IllegalArgumentException("Invalid "+key);return v;}
    static int integer(double v){if(!Double.isFinite(v)||v<1||v>10000000||v!=Math.rint(v))throw new IllegalArgumentException("Invalid positive integer");return (int)v;}
    static double setting(Map<String,Object> t,String key,Map<String,String> env,String name){
        double expected=positive(t,key);String raw=env.get(name);if(raw!=null && Double.parseDouble(raw)!=expected)
            throw new IllegalArgumentException("Environment differs from CaseSpec "+name);return expected;}
    static class Config {
        final double dt,period;final int inner,startup,statistics,window,steps,checkpoint;
        Config(Map<String,Object> s,Map<String,String> env){
            Map<String,Object> t=object(s.get("transient"));
            dt=setting(t,"time_step_s",env,"EXTERNAL_FLOW_DT_S");
            inner=integer(setting(t,"inner_iterations",env,"EXTERNAL_FLOW_INNER_ITER"));
            steps=integer(setting(t,"steps",env,"EXTERNAL_FLOW_STEPS"));
            checkpoint=integer(setting(t,"checkpoint_every_steps",env,"EXTERNAL_FLOW_CHECKPOINT_EVERY"));
            period=positive(t,"period_estimate_s");startup=integer(positive(t,"startup_cycles"));
            statistics=integer(positive(t,"statistics_cycles"));window=integer(positive(t,"window_cycles"));
            if(window>statistics)throw new IllegalArgumentException("Window exceeds statistics cycles");
            if(!Boolean.TRUE.equals(object(s.get("quality")).get("time_step_sensitivity")))
                throw new IllegalArgumentException("Time-step sensitivity required");
            String mode=text(t,"mode");if(!mode.equals("pilot")&&!mode.equals("matrix"))throw new IllegalArgumentException("Invalid mode");
            if(mode.equals("matrix")&&(startup<5||statistics<10||window!=3||steps*dt+1e-12<(startup+statistics)*period))
                throw new IllegalArgumentException("Insufficient matrix cycles");
            if(mode.equals("pilot")&&steps*dt+1e-12<2*period)throw new IllegalArgumentException("Insufficient pilot duration");
            if(positive(t,"inner_decay_decades")!=3.0)throw new IllegalArgumentException("Fixed decay criterion differs");
        }
    }
    static String quote(String s){StringBuilder b=new StringBuilder("\"");for(char c:s.toCharArray()){
        if(c=='"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format(java.util.Locale.ROOT,"\\u%04x",(int)c));else b.append(c);}
        return b.append('"').toString();}
    // Strict small JSON reader: missing/null/bool/string are never coerced into numbers.
    static class Json {
        // 2026-10-23 closure round D. The unchanged Windows runner writes case_spec.json with
        // Set-Content -Encoding UTF8, i.e. a UTF-8 BOM. Decoded as UTF-8 that BOM is U+FEFF at
        // index 0, and Character.isWhitespace does not skip it, so this reader rejected a
        // byte-valid payload with "Invalid JSON at 0" (the 2026-10-20 URANS pilot blocker).
        // Strip at most one leading U+FEFF and nothing else: a BOM in any other position is
        // still invalid input, and no other character is excused.
        final String s;int p=0;Json(String v){s=(v.length()>0&&v.charAt(0)=='\uFEFF')?v.substring(1):v;}
        void ws(){while(p<s.length()&&Character.isWhitespace(s.charAt(p)))p++;}
        RuntimeException bad(){return new IllegalArgumentException("Invalid JSON at "+p);}
        Object parse(){Object v=value();ws();if(p!=s.length())throw bad();return v;}
        Object value(){ws();if(p>=s.length())throw bad();char c=s.charAt(p);
            if(c=='{'){p++;Map<String,Object> m=new LinkedHashMap<String,Object>();ws();if(take('}'))return m;
                do{ws();if(p>=s.length()||s.charAt(p)!='"')throw bad();String k=string();ws();if(!take(':'))throw bad();
                    if(m.containsKey(k))throw bad();m.put(k,value());ws();if(take('}'))return m;}while(take(','));throw bad();}
            if(c=='['){p++;List<Object> a=new ArrayList<Object>();ws();if(take(']'))return a;
                do{a.add(value());ws();if(take(']'))return a;}while(take(','));throw bad();}
            if(c=='"')return string();for(String x:new String[]{"true","false","null"})if(s.startsWith(x,p)){
                p+=x.length();return x.equals("null")?null:Boolean.valueOf(x);}
            java.util.regex.Matcher m=java.util.regex.Pattern.compile("-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?").matcher(s.substring(p));
            if(!m.lookingAt())throw bad();p+=m.end();double n=Double.parseDouble(m.group());if(!Double.isFinite(n))throw bad();return n;}
        boolean take(char c){ws();if(p<s.length()&&s.charAt(p)==c){p++;return true;}return false;}
        String string(){if(s.charAt(p++)!='"')throw bad();StringBuilder b=new StringBuilder();while(p<s.length()){
            char c=s.charAt(p++);if(c=='"')return b.toString();if(c<32)throw bad();if(c=='\\'){
                if(p>=s.length())throw bad();char e=s.charAt(p++);int k="\"\\/bfnrt".indexOf(e);
                if(k>=0)c="\"\\/\b\f\n\r\t".charAt(k);else if(e=='u'&&p+4<=s.length()){
                    try{c=(char)Integer.parseInt(s.substring(p,p+4),16);}catch(Exception ex){throw bad();}p+=4;}else throw bad();}b.append(c);}throw bad();}
    }
    // END PURE CONTRACT
}
