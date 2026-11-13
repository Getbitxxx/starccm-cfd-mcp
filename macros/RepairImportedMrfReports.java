import star.common.*;
import star.base.report.*;
import star.flow.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.*;

/** Repair empty inherited force, moment and density report selections on an isolated MRF checkpoint. */
public class RepairImportedMrfReports extends StarMacro {
  private String field(String spec, String key) {
    Matcher m=Pattern.compile("\\\""+key+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
    if(!m.find()) throw new IllegalArgumentException("Missing "+key);
    return m.group(1);
  }
  private String sha256(File f) throws Exception {
    byte[] d=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(f.toPath()));
    StringBuilder b=new StringBuilder(); for(byte x:d)b.append(String.format("%02x",x&255)); return b.toString();
  }
  private void require(boolean ok,String message){if(!ok)throw new IllegalStateException(message);}
  @Override public void execute(){
    Simulation sim=getActiveSimulation(); File dir=sim.getSessionDirFile();
    try {
      String spec=Files.readString(new File(dir,"case_spec.json").toPath(),StandardCharsets.UTF_8);
      require(Pattern.compile("\\\"scenario\\\"\\s*:\\s*\\\"geometry_mesh_probe_only\\\"").matcher(spec).find(),"Probe-only CaseSpec required");
      require(sha256(new File(dir,"input.sim")).equals(field(spec,"expected_input_sha256")),"Input hash mismatch");
      require(sim.getRegionManager().getObjects().size()==2 && sim.getInterfaceManager().getObjects().size()==3,"Expected audited two-region, three-interface mesh");
      Region rotor=sim.getRegionManager().getRegion(field(spec,"rotor_region"));
      Region stator=sim.getRegionManager().getRegion(field(spec,"stator_region"));
      require(rotor!=null && stator!=null && rotor!=stator,"Rotor/stator selector mismatch");
      List<Boundary> walls=new ArrayList<>();
      for(Boundary b:rotor.getBoundaryManager().getBoundaries()) if(b.getBoundaryType() instanceof WallBoundary) walls.add(b);
      require(walls.size()>=3,"Expected rotor wall boundaries; found "+walls.size());
      ForceReport force=(ForceReport)sim.getReportManager().getReport("Force Z");
      MomentReport moment=(MomentReport)sim.getReportManager().getReport("Moment");
      VolumeAverageReport density=(VolumeAverageReport)sim.getReportManager().getReport("Density");
      require(force!=null && moment!=null && density!=null,"Expected inherited Force Z, Moment and Density reports");
      force.setUseIdealization(false); force.getParts().setObjects(walls.toArray(new Boundary[0]));
      force.getDirection().setComponentsAndUnits(0,0,1,sim.getUnitsManager().getObject(""));
      moment.setUseIdealization(false); moment.getParts().setObjects(walls.toArray(new Boundary[0]));
      moment.getDirection().setComponentsAndUnits(0,0,-1,sim.getUnitsManager().getObject(""));
      moment.getOrigin().setComponentsAndUnits(0,0,0,sim.getUnitsManager().getObject("m"));
      density.setFieldFunction(sim.getFieldFunctionManager().getFunction("Density"));
      density.getParts().setObjects(stator);
      Boundary inlet=null,outlet=null; int inletCount=0,outletCount=0;
      for(Boundary b:stator.getBoundaryManager().getBoundaries()) {
        if(b.getBoundaryType() instanceof InletBoundary){inlet=b;inletCount++;}
        if(b.getBoundaryType() instanceof PressureBoundary){outlet=b;outletCount++;}
      }
      require(inletCount==1 && outletCount==1,"Expected exactly one stator inlet and one pressure outlet; got "+inletCount+"/"+outletCount);
      MassFlowReport inletFlow=report(sim,"MRF_InletMassFlow",MassFlowReport.class);
      MassFlowReport outletFlow=report(sim,"MRF_OutletMassFlow",MassFlowReport.class);
      inletFlow.setUseIdealization(false); inletFlow.getParts().setObjects(inlet);
      outletFlow.setUseIdealization(false); outletFlow.getParts().setObjects(outlet);
      ExpressionReport imbalance=report(sim,"MRF_MassImbalance",ExpressionReport.class);
      imbalance.setDimensions(Dimensions.Builder().build());
      imbalance.setDefinition("abs(${MRF_InletMassFlowReport}+${MRF_OutletMassFlowReport})/max(abs(${MRF_InletMassFlowReport}),abs(${MRF_OutletMassFlowReport}))");
      int disabledInvalidMonitors=0;
      for(Monitor monitor:sim.getMonitorManager().getObjects()) if(monitor instanceof ReportMonitor) {
        Report report=((ReportMonitor)monitor).getReport();
        if(report!=null && report.getPresentationName().equals("Lift Drag Ratio")) {
          monitor.setEnabled(false); disabledInvalidMonitors++;
        }
      }
      double f=force.getReportMonitorValue(),m=moment.getReportMonitorValue(),rho=density.getReportMonitorValue();
      require(Double.isFinite(f)&&Double.isFinite(m)&&Double.isFinite(rho)&&rho>0,"Repaired report values are not finite and physical");
      double mIn=inletFlow.getReportMonitorValue(),mOut=outletFlow.getReportMonitorValue(),mErr=imbalance.getReportMonitorValue();
      require(Double.isFinite(mIn)&&Double.isFinite(mOut)&&Double.isFinite(mErr)&&Math.abs(mIn)>0&&Math.abs(mOut)>0,
              "Mass-flow reports are not finite and nonzero");
      File out=new File(dir,"mrf_report_repair.txt");
      try(PrintWriter p=new PrintWriter(new OutputStreamWriter(new FileOutputStream(out),StandardCharsets.UTF_8))){
        p.println("iteration="+sim.getSimulationIterator().getCurrentIteration()); p.println("rotor_wall_boundary_count="+walls.size());
        for(Boundary b:walls)p.println("rotor_wall="+b.getPresentationName());
        p.println("force_z_N="+f);p.println("moment_z_Nm="+m);p.println("stator_volume_average_density_kg_m3="+rho);
        p.println("inlet_mass_flow_kg_s="+mIn);p.println("outlet_mass_flow_kg_s="+mOut);p.println("mass_imbalance="+mErr);
        p.println("disabled_zero_sideforce_ratio_monitors="+disabledInvalidMonitors);
        p.println("Kt="+sim.getReportManager().getReport("Kt").getReportMonitorValue());
        p.println("Kq="+sim.getReportManager().getReport("Kq").getReportMonitorValue());
        p.println("REPORT_REPAIR_COMPLETE");
      }
      sim.saveState(new File(dir,"mrf_reports_repaired.sim").getAbsolutePath());
      sim.println("REPORT_REPAIR_COMPLETE");
    }catch(Exception e){try{Files.writeString(new File(dir,"mrf_report_repair_failure.txt").toPath(),e.toString(),StandardCharsets.UTF_8);}catch(Exception ignored){}throw new RuntimeException(e);}
  }
  private <T extends Report> T report(Simulation sim,String name,Class<T> type){
    for(Report existing:sim.getReportManager().getObjects())
      if(existing.getPresentationName().equals(name))return type.cast(existing);
    T created=type.cast(sim.getReportManager().create(type.getName()));created.setPresentationName(name);return created;
  }
}
