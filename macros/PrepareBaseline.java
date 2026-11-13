package macro;

import star.common.*;
import star.base.report.*;
import star.flow.*;
import star.motion.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Baseline adapter for this exact 2502 periodic propeller case, not arbitrary geometry. */
public class PrepareBaseline extends StarMacro {
  private Simulation sim;
  private PrintWriter audit;
  private final String P="CFD_";
  public void execute() {
    sim=getActiveSimulation();
    try {
      audit=new PrintWriter(new OutputStreamWriter(new FileOutputStream(new File(sim.getSessionDirFile(),"baseline_preflight.txt")),StandardCharsets.UTF_8));
      sim.getSimulationIterator().getAutoSave().setEnabled(false);
      Region fluid=sim.getRegionManager().getRegion("FLUID");
      Region rotor=sim.getRegionManager().getRegion("PROPELLER");
      require(sim.getRegionManager().getRegions().size()==2,"This adapter expects exactly two fluid regions");
      Set<String> models=new HashSet<>();
      for(Object model:rotor.getPhysicsContinuum().getModelManager().getObjects())models.add(model.getClass().getSimpleName());
      require(models.contains("SteadyModel") && models.contains("RkeTwoLayerTurbModel") && models.contains("IdealGasModel"),"This adapter requires the inspected steady realizable k-epsilon ideal-gas baseline");
      Boundary inlet=fluid.getBoundaryManager().getBoundary("INLET");
      Boundary outlet=fluid.getBoundaryManager().getBoundary("OUTLET");
      Boundary blade=rotor.getBoundaryManager().getBoundary("WALL");
      require(inlet.getBoundaryType() instanceof InletBoundary,"INLET must be a velocity inlet");
      require(outlet.getBoundaryType() instanceof PressureBoundary,"OUTLET must be a pressure outlet");
      require(blade.getBoundaryType() instanceof WallBoundary,"Rotor WALL must be a wall");
      audit.println("iteration_before="+sim.getSimulationIterator().getCurrentIteration());
      for(Region r:sim.getRegionManager().getRegions()) audit.println("region="+r.getPresentationName());
      double angle=0;
      for(String name:new String[]{"FLUID/FLUID","PROPELLER/PROPELLER"}) {
        BoundaryInterface i=(BoundaryInterface)sim.getInterfaceManager().getInterface(name);
        require(i.getTopology().getSelectedElement().toString().equals("PERIODIC"),"Expected periodic interface "+name);
        double a=Math.abs(i.getPeriodicTransform().getRotationAngle());
        require(Math.abs(a-120.0)<0.001,"Expected 120 degree sector");
        angle=a; audit.println("periodic_angle_deg_"+name+"="+a);
      }
      double repeats=Math.rint(360.0/angle);
      ScalarGlobalParameter v=param("V");
      ScalarGlobalParameter n=param("n");
      ScalarGlobalParameter diameter=param("D");
      audit.println("V_m_s="+v.getQuantity().getSIValue());
      audit.println("n_per_s="+n.getQuantity().getSIValue());
      audit.println("D_parameter_m="+diameter.getQuantity().getSIValue());
      require(v.getQuantity().getSIValue()>0 && n.getQuantity().getSIValue()>0,"This baseline is positive-speed forward-flight only");
      UserRotatingReferenceFrame frame=(UserRotatingReferenceFrame)sim.get(ReferenceFrameManager.class).getObject("Rotating");
      audit.println("axis="+frame.getAxisVector().getDefinition());
      audit.println("origin="+frame.getOriginVector().getDefinition());
      double[] axis=frame.getAxisVector().getVector().toDoubleArray();
      double[] origin=frame.getOriginVector().getVector().toDoubleArray();
      require(Math.abs(axis[0])+Math.abs(axis[1])+Math.abs(axis[2]-1)<1e-10,"Requires +Z axis");
      require(Math.abs(origin[0])+Math.abs(origin[1])+Math.abs(origin[2])<1e-10,"Requires origin at zero");

      UserFieldFunction radial=null;
      for(FieldFunction f:sim.getFieldFunctionManager().getObjects()) if(f.getPresentationName().equals(P+"Radius")) radial=(UserFieldFunction)f;
      if(radial==null)radial=sim.getFieldFunctionManager().createFieldFunction();
      radial.setPresentationName(P+"Radius"); radial.setFunctionName(P+"Radius");
      radial.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
      radial.setDimensions(Dimensions.Builder().length(1).build());
      radial.setDefinition("sqrt(pow($$Position[0],2)+pow($$Position[1],2))");
      MaxReport radius=report(P+"BladeRadius",MaxReport.class);
      radius.setFieldFunction(radial); radius.getParts().setObjects(blade);
      radius.setUnits((Units)sim.getUnitsManager().getObject("m"));
      double meshDiameter=2*radius.getReportMonitorValue();
      audit.println("D_wall_mesh_m="+meshDiameter);
      double mismatch=Math.abs(meshDiameter-diameter.getQuantity().getSIValue())/diameter.getQuantity().getSIValue();
      audit.println("diameter_relative_difference="+mismatch);
      require(mismatch<0.01,"D differs from rotor wall mesh diameter by >=1%; baseline not saved");

      ForceReport originalForce=(ForceReport)sim.getReportManager().getReport("Force Z");
      MomentReport originalMoment=(MomentReport)sim.getReportManager().getReport("Moment");
      audit.println("legacy_force_use_idealization="+originalForce.getUseIdealization());
      audit.println("legacy_moment_use_idealization="+originalMoment.getUseIdealization());
      ForceReport thrust=report(P+"SectorThrust",ForceReport.class);
      thrust.copyProperties(originalForce); thrust.setPresentationName(P+"SectorThrust");
      thrust.setUseIdealization(false); thrust.getParts().setObjects(blade);
      MomentReport torque=report(P+"SectorTorque",MomentReport.class);
      torque.copyProperties(originalMoment); torque.setPresentationName(P+"SectorTorque");
      torque.setUseIdealization(false); torque.getParts().setObjects(blade);
      Units dimensionless=(Units)sim.getUnitsManager().getObject("");
      Units meters=(Units)sim.getUnitsManager().getObject("m");
      thrust.getDirection().setComponentsAndUnits(0,0,1,dimensionless);
      torque.getDirection().setComponentsAndUnits(0,0,-1,dimensionless);
      torque.getOrigin().setComponentsAndUnits(0,0,0,meters);

      // Keep existing operating point; n (revolutions/s) is the single source of rotation speed.
      frame.getRotationRate().setDefinition("${n}*2*3.141592653589793");
      ((ExpressionReport)sim.getReportManager().getReport("Power")).setDefinition("${MomentReport}*${n}*2*3.141592653589793");
      ((ExpressionReport)sim.getReportManager().getReport("Efficiency no")).setDefinition("${JReport}*${KtReport}/(2*3.141592653589793*${KqReport})");
      inlet.getValues().get(VelocityMagnitudeProfile.class).getMethod(ConstantScalarProfileMethod.class).getQuantity().setDefinition("${V}");

      expression(P+"SectorPower","${CFD_SectorTorqueReport}*${n}*2*3.141592653589793",Dimensions.Builder().power(1).build());
      expression(P+"PeriodicThrust",Double.toString(repeats)+"*${CFD_SectorThrustReport}",Dimensions.Builder().force(1).build());
      expression(P+"PeriodicTorque",Double.toString(repeats)+"*${CFD_SectorTorqueReport}",Dimensions.Builder().moment(1).build());
      expression(P+"PeriodicPower",Double.toString(repeats)+"*${CFD_SectorPowerReport}",Dimensions.Builder().power(1).build());
      expression(P+"Efficiency","${V}*${CFD_SectorThrustReport}/${CFD_SectorPowerReport}",Dimensions.Builder().build());
      AreaAverageReport density=report(P+"InletDensity",AreaAverageReport.class);
      density.setFieldFunction(sim.getFieldFunctionManager().getFunction("Density")); density.getParts().setObjects(inlet);
      expression(P+"CT","${CFD_PeriodicThrustReport}/(${CFD_InletDensityReport}*pow(${n},2)*pow(${D},4))",Dimensions.Builder().build());
      expression(P+"CQ","${CFD_PeriodicTorqueReport}/(${CFD_InletDensityReport}*pow(${n},2)*pow(${D},5))",Dimensions.Builder().build());
      mass(P+"InletMassFlow",inlet); mass(P+"OutletMassFlow",outlet);
      expression(P+"MassImbalance","abs(${CFD_InletMassFlowReport}+${CFD_OutletMassFlowReport})/max(abs(${CFD_InletMassFlowReport}),abs(${CFD_OutletMassFlowReport}))",Dimensions.Builder().build());
      MaxReport yp=report(P+"MaxWallYplus",MaxReport.class);
      yp.setFieldFunction(sim.getFieldFunctionManager().getFunction("WallYplus"));yp.getParts().setObjects(blade);
      for(Report r:sim.getReportManager().getObjects()) {
        if(r.getPresentationName().startsWith(P) && !r.getPresentationName().equals(P+"BladeRadius")) {
          ReportMonitor monitor=null;
          for(Monitor m:sim.getMonitorManager().getObjects()) if(m instanceof ReportMonitor && ((ReportMonitor)m).getReport().equals(r)) monitor=(ReportMonitor)m;
          if(monitor==null)monitor=r.createMonitor();
          monitor.setEnabled(true);monitor.setUpdateFrequency(1);
          audit.println("report="+r.getPresentationName()+",units="+r.getUnits()+",value="+r.getReportMonitorValue());
        }
      }
      for(Monitor m:sim.getMonitorManager().getObjects()) m.setEnabled(true);
      audit.println("periodic_repetitions="+repeats);
      audit.println("report_count="+sim.getReportManager().getObjects().size());
      audit.println("monitor_count="+sim.getMonitorManager().getObjects().size());
      audit.println("qualification=prepared_not_converged");
      sim.saveState(new File(sim.getSessionDirFile(),"baseline_prepared.sim").getAbsolutePath());
      audit.println("PREPARE_COMPLETE");audit.close();sim.println("PREPARE_COMPLETE");
    }catch(Exception e){if(audit!=null){audit.println("PREPARE_FAILED="+e);audit.close();}throw new RuntimeException(e);}
  }
  private void require(boolean condition,String message){if(!condition)throw new IllegalStateException(message);}
  private ScalarGlobalParameter param(String name){return (ScalarGlobalParameter)sim.get(GlobalParameterManager.class).getObject(name);}
  private <T extends Report> T report(String name,Class<T> type){
    for(Report r:sim.getReportManager().getObjects())if(r.getPresentationName().equals(name))return type.cast(r);
    T r=type.cast(sim.getReportManager().create(type.getName()));r.setPresentationName(name);return r;
  }
  private void expression(String name,String definition,Dimensions dimensions){ExpressionReport r=report(name,ExpressionReport.class);r.setDimensions(dimensions);r.setDefinition(definition);}
  private void mass(String name,Boundary b){MassFlowReport r=report(name,MassFlowReport.class);r.setUseIdealization(false);r.getParts().setObjects(b);}
}
