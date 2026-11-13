package macro;

import star.common.*;
import star.base.report.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bounded continuation of saved fields. Successful execution is not CFD validation. */
public class RunBaselineSmoke extends StarMacro {
  private Simulation sim;
  private File dir;
  public void execute() {
    sim=getActiveSimulation();dir=sim.getSessionDirFile();
    int initial=sim.getSimulationIterator().getCurrentIteration();
    int budget=100, chunk=10;
    long started=System.nanoTime();
    String status="RUNNING", reason="";
    try(PrintWriter csv=new PrintWriter(new OutputStreamWriter(new FileOutputStream(new File(dir,"smoke_checkpoints.csv")),StandardCharsets.UTF_8))) {
      csv.println("iteration,elapsed_s,sector_thrust_N,sector_torque_Nm,periodic_thrust_N,periodic_power_W,efficiency,mass_imbalance,max_wall_yplus");
      StepStoppingCriterion maximum=(StepStoppingCriterion)sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
      maximum.getMaximumNumberStepsObject().getQuantity().setValue(initial+budget);
      maximum.setIsUsed(true);
      sim.getSimulationIterator().getAutoSave().setEnabled(false);
      for(Monitor m:sim.getMonitorManager().getObjects())m.setEnabled(true);
      for(int requested=0;requested<budget;requested+=chunk) {
        double elapsed=(System.nanoTime()-started)/1e9;
        if(elapsed>300){status="TIME_BUDGET_REACHED";reason="300 second budget checked between chunks";break;}
        int before=sim.getSimulationIterator().getCurrentIteration();
        sim.getSimulationIterator().run(Math.min(chunk,budget-requested));
        int after=sim.getSimulationIterator().getCurrentIteration();
        if(after<=before){status="STOPPED_EARLY";reason="Solver did not advance; inspect stopping criteria and stop file";break;}
        double t=value("CFD_SectorThrust"),q=value("CFD_SectorTorque"),tt=value("CFD_PeriodicThrust"),p=value("CFD_PeriodicPower"),e=value("CFD_Efficiency"),m=value("CFD_MassImbalance"),y=value("CFD_MaxWallYplus");
        elapsed=(System.nanoTime()-started)/1e9;
        csv.printf(Locale.ROOT,"%d,%.6f,%.12g,%.12g,%.12g,%.12g,%.12g,%.12g,%.12g%n",after,elapsed,t,q,tt,p,e,m,y);csv.flush();
        for(double x:new double[]{t,q,tt,p,e,m,y})if(!Double.isFinite(x))throw new IllegalStateException("Non-finite monitored value");
        if(after-initial>=20 && m>0.1){status="DIAGNOSTIC_STOP";reason="Mass imbalance exceeds 10 percent; exploratory diagnostic threshold";break;}
        writeStatus("RUNNING",initial,after,elapsed,"execution_check_only");
      }
      int end=sim.getSimulationIterator().getCurrentIteration();
      if(status.equals("RUNNING")) {status=end-initial==budget?"SMOKE_COMPLETED":"STOPPED_EARLY";reason="not_a_convergence_or_validation_certificate";}
      export();
      sim.saveState(new File(dir,"baseline_after_smoke.sim").getAbsolutePath());
      writeStatus(status,initial,end,(System.nanoTime()-started)/1e9,reason);
      sim.println(status+": iteration="+end);
    }catch(Exception e){
      writeStatus("FAILED",initial,sim.getSimulationIterator().getCurrentIteration(),(System.nanoTime()-started)/1e9,e.toString());
      throw new RuntimeException(e);
    }
  }
  private double value(String name){return sim.getReportManager().getReport(name).getReportMonitorValue();}
  private void export() throws Exception {
    File history=new File(dir,"monitor_csv");history.mkdirs();
    try(PrintWriter index=new PrintWriter(new File(dir,"monitor_index.csv"),"UTF-8")){
      index.println("name,file");
      for(Monitor m:sim.getMonitorManager().getObjects()){
        String filename=m.getPresentationName().replaceAll("[^A-Za-z0-9_-]","_")+".csv";
        m.export(new File(history,filename).getAbsolutePath());
        index.println("\""+m.getPresentationName().replace("\"","\"\"")+"\","+filename);
      }
    }
    try(PrintWriter summary=new PrintWriter(new File(dir,"smoke_reports.csv"),"UTF-8")){
      summary.println("name,unit,value");
      for(Report r:sim.getReportManager().getObjects()) if(r.getPresentationName().startsWith("CFD_")) summary.printf(Locale.ROOT,"%s,%s,%.15g%n",r.getPresentationName(),r.getUnits().getPresentationName(),r.getReportMonitorValue());
    }
  }
  private void writeStatus(String state,int start,int end,double elapsed,String reason){
    try(PrintWriter p=new PrintWriter(new File(dir,"smoke_status.json"),"UTF-8")){
      String message=reason.replace("\\","\\\\").replace("\"","\\\"").replace("\n"," ").replace("\r"," ");
      p.printf(Locale.ROOT,"{\"state\":\"%s\",\"start_iteration\":%d,\"end_iteration\":%d,\"elapsed_seconds\":%.3f,\"convergence_verified\":false,\"reason\":\"%s\"}%n",state,start,end,elapsed,message);
    }catch(IOException e){throw new RuntimeException(e);}
  }
}
