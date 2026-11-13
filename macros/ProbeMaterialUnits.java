/* NEW empty-session material readback only. No mesh, initialize, solver or benchmark. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import star.common.*;
import star.material.*;
import star.coupledflow.*;
import star.energy.*;
import star.flow.*;
import star.metrics.*;
import star.kwturb.*;
import star.turbulence.*;
public class ProbeMaterialUnits extends StarMacro {
    private static String string(String x) {
        if(x==null)return "null";
        StringBuilder b=new StringBuilder("\"");
        for(char c:x.toCharArray())switch(c){case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}
        return b.append('"').toString();
    }
    private static String number(double x){if(!Double.isFinite(x))throw new IllegalStateException("nonfinite native value");return Double.toString(x);}
    private static String dimensions(Dimensions d){
        return "{\"mass\":"+d.getMass()+",\"length\":"+d.getLength()+",\"time\":"+d.getTime()+",\"temperature\":"+d.getTemperature()+",\"current\":"+d.getCurrent()+",\"luminosity\":"+d.getLuminosity()+",\"quantity\":"+d.getQuantity()+",\"angle\":"+d.getAngle()+",\"temperature_difference\":"+d.getTemperatureDifference()+",\"solid_angle\":"+d.getSolidAngle()+",\"digital_information\":"+d.getDigitalInformation()+"}";
    }
    private static String units(Units u){if(u==null)throw new IllegalStateException("native unit absent");return "{\"suffix\":"+string(u.getSuffix())+",\"conversion\":"+number(u.getConversion())+",\"offset\":"+number(u.getOffset())+",\"dimensions\":"+dimensions(u.getDimensions())+"}";}
    private static String quantity(ScalarPhysicalQuantity q){return "{\"raw_value\":"+number(q.getRawValue())+",\"value\":"+number(q.getValue())+",\"SI_value\":"+number(q.getSIValue())+",\"units\":"+units(q.getUnits())+",\"SI_units\":"+units(q.getUnits().getSIUnits())+"}";}
    private static String property(MaterialProperty p){
        MaterialPropertyMethod m=p.getMethod();String body="\"method_class\":"+string(m.getClass().getName());
        if(m instanceof ConstantMaterialPropertyMethod)body+=",\"constant_quantity\":"+quantity(((ConstantMaterialPropertyMethod)m).getQuantity());
        else if(m instanceof SutherlandLaw){SutherlandLaw x=(SutherlandLaw)m;body+=",\"reference_value\":"+quantity(x.getReferenceValue())+",\"reference_temperature\":"+quantity(x.getReferenceTemperature())+",\"sutherland_constant\":"+quantity(x.getSutherlandConstant());}
        else body+=",\"quantity_readback\":\"METHOD_NOT_COVERED_NO_INFERENCE\"";
        return "{"+body+"}";
    }
    private static void phase(Simulation s,File d,String name)throws Exception{s.println("MATERIAL_UNIT_PHASE="+name);Files.writeString(new File(d,"material_current_phase.txt").toPath(),name+"\n",StandardCharsets.UTF_8);}
    private static void partial(File d,String phase,String mw,String cp,String mu)throws Exception{
        Files.writeString(new File(d,"material_unit_partial.json").toPath(),"{\"status\":\"PARTIAL_NOT_COMPLETE\",\"verification_scope\":\"MATERIAL_UNIT_READBACK_ONLY\",\"phase\":"+string(phase)+",\"molecular_weight\":"+mw+",\"specific_heat\":"+cp+",\"dynamic_viscosity\":"+mu+",\"physics_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);
    }
    @Override public void execute(){
        Simulation s=getActiveSimulation();File d=s.getSessionDirFile(),out=new File(d,"material_unit_probe.json");String phase="EMPTY_SESSION_CHECK",mw="null",cp="null",mu="null";
        try{
            // All checks precede the first native mutation; pipeline must also specify NEW.
            if(!s.getRegionManager().getRegions().isEmpty()||!s.getContinuumManager().getObjects().isEmpty()||s.getSolution().isInitialized())throw new IllegalStateException("NEW empty uninitialized session required");
            File[] sources=d.listFiles((dir,name)->name.toLowerCase(java.util.Locale.ROOT).endsWith(".sim"));
            if(sources==null||sources.length!=0)throw new IllegalStateException("source/checkpoint sim files forbidden for NEW probe");
            phase="CREATE_MATERIAL_CONTINUUM";phase(s,d,phase);
            PhysicsContinuum c=s.getContinuumManager().createContinuum(PhysicsContinuum.class);c.setPresentationName("MATERIAL_UNITS_ONLY");
            c.enable(TwoDimensionalModel.class);c.enable(SteadyModel.class);c.enable(SingleComponentGasModel.class);c.enable(CoupledFlowModel.class);c.enable(IdealGasModel.class);c.enable(CoupledEnergyModel.class);
            phase="ENABLE_VISCOUS_SST_PRECEDENT";phase(s,d,phase);
            c.enable(TurbulentModel.class);c.enable(RansTurbulenceModel.class);c.enable(KOmegaTurbulence.class);c.enable(SstKwTurbModel.class);c.enable(KwAllYplusWallTreatment.class);
            if(c.getModelManager().getModel(TurbulentModel.class)==null||c.getModelManager().getModel(RansTurbulenceModel.class)==null||c.getModelManager().getModel(KOmegaTurbulence.class)==null||c.getModelManager().getModel(SstKwTurbModel.class)==null||c.getModelManager().getModel(KwAllYplusWallTreatment.class)==null)throw new IllegalStateException("expected viscous SST precedent model chain absent");
            phase="READ_NATIVE_MATERIAL";phase(s,d,phase);
            Material material=c.getModelManager().getModel(SingleComponentGasModel.class).getMaterial();
            phase="MolecularWeightProperty.BEFORE";phase(s,d,phase);partial(d,phase,mw,cp,mu);
            mw=property(material.getMaterialProperties().getMaterialProperty(MolecularWeightProperty.class));
            phase="MolecularWeightProperty.AFTER";partial(d,phase,mw,cp,mu);phase(s,d,phase);
            phase="SpecificHeatProperty.BEFORE";phase(s,d,phase);partial(d,phase,mw,cp,mu);
            cp=property(material.getMaterialProperties().getMaterialProperty(SpecificHeatProperty.class));
            phase="SpecificHeatProperty.AFTER";partial(d,phase,mw,cp,mu);phase(s,d,phase);
            phase="DynamicViscosityProperty.BEFORE";phase(s,d,phase);partial(d,phase,mw,cp,mu);
            mu=property(material.getMaterialProperties().getMaterialProperty(DynamicViscosityProperty.class));
            phase="DynamicViscosityProperty.AFTER";partial(d,phase,mw,cp,mu);phase(s,d,phase);
            if(!s.getRegionManager().getRegions().isEmpty()||s.getSolution().isInitialized())throw new IllegalStateException("probe changed forbidden state");
            String json="{\"status\":\"ok\",\"verification_scope\":\"MATERIAL_UNIT_READBACK_ONLY\",\"material_class\":"+string(material.getClass().getName())+",\"molecular_weight\":"+mw+",\"specific_heat\":"+cp+",\"dynamic_viscosity\":"+mu+",\"unit_interpretation\":\"RAW_NATIVE_EVIDENCE_NO_MOLAR_BASIS_ASSUMPTION\",\"mesh_created\":false,\"initialized\":false,\"physics_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}";
            Files.writeString(out.toPath(),json,StandardCharsets.UTF_8);
            s.println("MATERIAL_UNIT_READBACK_ONLY; MW="+mw+"; Cp="+cp+"; viscosity="+mu+"; no mol/kmol interpretation or benchmark approval");
        }catch(Exception e){
            try{Files.writeString(out.toPath(),"{\"status\":\"gate_failed\",\"verification_scope\":\"MATERIAL_UNIT_READBACK_ONLY\",\"phase\":"+string(phase)+",\"error_class\":"+string(e.getClass().getName())+",\"error\":"+string(e.getMessage())+",\"physics_accepted\":false,\"science_accepted\":false,\"solve_authorized\":false}",StandardCharsets.UTF_8);}catch(Exception ignored){}
            throw new RuntimeException("material unit readback failed at "+phase,e);
        }
    }
}
