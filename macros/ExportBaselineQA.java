package macro;
import star.common.*;
import star.base.report.*;
import java.io.*;
import java.util.*;

/** Exports residuals with normalization disabled, then restores each original setting. */
public class ExportBaselineQA extends StarMacro {
  public void execute(){
    Simulation s=getActiveSimulation();
    File dir=s.getSessionDirFile();File raw=new File(dir,"residual_raw_csv");raw.mkdirs();
    try(PrintWriter p=new PrintWriter(new File(dir,"residual_normalization.txt"),"UTF-8")){
      for(Monitor m:s.getMonitorManager().getObjects())if(m instanceof ResidualMonitor){
        ResidualMonitor r=(ResidualMonitor)m;
        MonitorNormalizeOption.Type original=(MonitorNormalizeOption.Type)r.getNormalizeOption().getSelectedElement();
        p.println(r.getPresentationName()+": original="+original);
        try{r.getNormalizeOption().setSelected(MonitorNormalizeOption.Type.OFF);
          r.export(new File(raw,r.getPresentationName().replaceAll("[^A-Za-z0-9_-]","_")+".csv").getAbsolutePath());
          double[] ys=r.getAllYValues();
          p.println("samples="+ys.length+",last="+(ys.length>0?ys[ys.length-1]:Double.NaN));
        }finally{r.getNormalizeOption().setSelected(original);}
      }
      p.println("RESIDUAL_QA_COMPLETE");
    }catch(Exception e){throw new RuntimeException(e);}
    s.println("RESIDUAL_QA_COMPLETE");
  }
}
