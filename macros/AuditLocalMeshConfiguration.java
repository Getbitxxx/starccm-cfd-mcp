/* Read-only saved-state audit. No meshing, initialization, solve or save. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.meshing.*;
import star.prismmesher.*;

public class AuditLocalMeshConfiguration extends StarMacro {
    private void equal(double actual,double expected,String key) {
        if(!Double.isFinite(actual) || Math.abs(actual-expected)>1e-10*Math.max(Math.abs(expected),1e-6))
            throw new IllegalStateException(key+" mismatch: "+actual+" / "+expected);
    }
    @Override public void execute() {
        Simulation s=getActiveSimulation();File dir=s.getSessionDirFile();
        try {
            Properties p=new Properties();
            try(Reader r=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)){p.load(r);}
            AutoMeshOperation op=(AutoMeshOperation)s.get(MeshOperationManager.class).getObject("AUDITED_LOCAL_MESH");
            if(op==null)throw new IllegalStateException("Missing operation");
            if(!op.getMeshPartByPart())throw new IllegalStateException("Multi-part Trimmer requires per-part meshing");
            Set<String> meshers=new HashSet<>();
            for(MesherBase m:op.getMeshersCollection())meshers.add(m.getClass().getName());
            if(!meshers.equals(Set.of("star.resurfacer.ResurfacerAutoMesher","star.trimmer.TrimmerAutoMesher","star.prismmesher.PrismAutoMesher")))
                throw new IllegalStateException("Mesher selection mismatch: "+meshers);
            double base=Double.parseDouble(p.getProperty("base_size_m"));
            equal(op.getDefaultValues().get(BaseSize.class).getSIValue(),base,"base");
            int layers=op.getDefaultValues().get(NumPrismLayers.class).getNumLayers();
            if(layers!=Integer.parseInt(p.getProperty("prism_layers")))throw new IllegalStateException("Layer count mismatch");
            equal(op.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().getSIValue(),Double.parseDouble(p.getProperty("prism_total_m")),"thickness");
            equal(op.getDefaultValues().get(PrismLayerStretching.class).getStretching(),Double.parseDouble(p.getProperty("prism_stretching")),"stretching");
            PrismAutoMesher prismMesher=null;
            for(MesherBase m:op.getMeshersCollection())if(m instanceof PrismAutoMesher)prismMesher=(PrismAutoMesher)m;
            if(prismMesher==null)throw new IllegalStateException("Prism mesher missing");
            String[] prismKeys={"minimum_thickness_pct","layer_chopping_pct","boundary_march_angle_deg"};
            double[] prismActual={prismMesher.getDefaultValuesManager().get(PrismLayerMinimumThickness.class).getValue(),
                prismMesher.getDefaultValuesManager().get(PrismLayerReductionPercentage.class).getValue(),
                prismMesher.getDefaultValuesManager().get(PrismLayerBoundaryMarchAngle.class).getValue()};
            int optionCount=0;for(String key:prismKeys)if(p.containsKey("prism_mesher."+key))optionCount++;
            if(optionCount!=0 && optionCount!=3)throw new IllegalStateException("Incomplete prism_mesher options");
            for(int i=0;i<3;i++){
                if(!Double.isFinite(prismActual[i]))throw new IllegalStateException("Nonfinite prism mesher parameter");
                if(optionCount==3)equal(prismActual[i],Double.parseDouble(p.getProperty("prism_mesher."+prismKeys[i])),prismKeys[i]);
            }
            Set<String> inputs=new HashSet<>();
            for(Object x:op.getInputGeometryObjects().getObjects())inputs.add(((ClientServerObject)x).getQualifiedName());
            Set<String> expectedInputs=new HashSet<>();
            for(int i=0;i<Integer.parseInt(p.getProperty("part_count"));i++)expectedInputs.add(p.getProperty("part."+i));
            if(!inputs.equals(expectedInputs))throw new IllegalStateException("Input parts mismatch");
            try(PrintWriter out=new PrintWriter(new File(dir,"local_mesh_readback.tsv"),StandardCharsets.UTF_8)) {
                out.println("BASE_SIZE_SI\t"+base);out.println("PRISM_LAYERS\t"+layers);
                out.println("MESH_PART_BY_PART\ttrue");
                for(int i=0;i<3;i++)out.println("PRISM_MESHER\t"+prismKeys[i]+"\t"+prismActual[i]);
                out.println("PRISM_TOTAL_SI\t"+op.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().getSIValue());
                out.println("PRISM_STRETCHING\t"+op.getDefaultValues().get(PrismLayerStretching.class).getStretching());
                for(String m:new TreeSet<>(meshers))out.println("MESHER\t"+m);
                int ns=Integer.parseInt(p.getProperty("surface_count")),ne=Integer.parseInt(p.getProperty("edge_count"));
                if(op.getCustomMeshControls().getObjects().size()!=ns+ne+1)throw new IllegalStateException("Control count mismatch");
                for(int i=0;i<ns;i++) {
                    String k="surface."+i+".";SurfaceCustomMeshControl c=(SurfaceCustomMeshControl)op.getCustomMeshControls().getObject("LOCAL_SURFACE_"+i);
                    double min=c.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().getValue();
                    double target=c.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().getValue();
                    if(!c.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class).getSelectedElement().toString().equals("CUSTOM") ||
                       !c.getCustomConditions().get(PartsTargetSurfaceSizeOption.class).getSelectedElement().toString().equals("CUSTOM"))
                        throw new IllegalStateException("Surface custom sizes inactive");
                    equal(min,100*Double.parseDouble(p.getProperty(k+"min_size_m"))/base,k+"min");
                    equal(target,100*Double.parseDouble(p.getProperty(k+"target_size_m"))/base,k+"target");
                    if(c.getGeometryObjects().getObjects().size()!=1)throw new IllegalStateException("Surface selection count");
                    String selected=((PartSurface)c.getGeometryObjects().getObjects().iterator().next()).getQualifiedName();
                    if(!selected.equals(p.getProperty(k+"selector")))throw new IllegalStateException("Surface selection mismatch");
                    String prism=c.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions().getSelectedElement().toString();
                    boolean wall=Set.of("blade_wall","hub_wall","stationary_wall").contains(p.getProperty(k+"role"));
                    if(!prism.equals(wall?"PARENT":"DISABLE"))throw new IllegalStateException("Prism policy mismatch");
                    out.println("SURFACE\t"+selected+"\t"+min+"\t"+target+"\t"+prism);
                }
                for(int i=0;i<ne;i++) {
                    String k="edge."+i+".";CurveCustomMeshControl c=(CurveCustomMeshControl)op.getCustomMeshControls().getObject("LOCAL_EDGE_"+i);
                    if(c.getGeometryObjects().getObjects().size()!=1)throw new IllegalStateException("Curve selection count");
                    String selected=((PartCurve)c.getGeometryObjects().getObjects().iterator().next()).getQualifiedName();
                    if(!c.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class).getSelectedElement().toString().equals("CUSTOM") ||
                       !c.getCustomConditions().get(PartsTargetSurfaceSizeOption.class).getSelectedElement().toString().equals("CUSTOM"))
                        throw new IllegalStateException("Curve custom sizes inactive");
                    if(!selected.equals(p.getProperty(k+"selector")))throw new IllegalStateException("Curve selection mismatch");
                    double min=c.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().getValue(),target=c.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().getValue();
                    equal(min,100*Double.parseDouble(p.getProperty(k+"min_size_m"))/base,k+"min");equal(target,100*Double.parseDouble(p.getProperty(k+"target_size_m"))/base,k+"target");
                    out.println("CURVE\t"+selected+"\t"+min+"\t"+target);
                }
                VolumeCustomMeshControl vc=(VolumeCustomMeshControl)op.getCustomMeshControls().getObject("LOCAL_NEAR_FIELD");
                equal(vc.getCustomValues().get(VolumeControlSize.class).getRelativeSizeScalar().getValue(),100*Double.parseDouble(p.getProperty("near.target_size_m"))/base,"near size");
                if(vc.getGeometryObjects().getObjects().size()!=1)throw new IllegalStateException("Near volume count");
                Object volume=vc.getGeometryObjects().getObjects().iterator().next();
                if(!(volume instanceof SimpleCylinderPart))throw new IllegalStateException("Near control is not a cylinder");
                SimpleCylinderPart cylinder=(SimpleCylinderPart)volume;
                if(cylinder.getCoordinateSystem()!=s.getCoordinateSystemManager().getLabCoordinateSystem())throw new IllegalStateException("Near coordinate system mismatch");
                equal(cylinder.getRadius().getSIValue(),Double.parseDouble(p.getProperty("near.radius_m")),"near radius");
                DoubleVector start=cylinder.getStartCoordinate().getSIValue(),end=cylinder.getEndCoordinate().getSIValue();
                for(int i=0;i<3;i++){
                    equal(start.get(i),Double.parseDouble(p.getProperty("near.start_m."+i)),"near start "+i);
                    equal(end.get(i),Double.parseDouble(p.getProperty("near.end_m."+i)),"near end "+i);
                }
                if(!vc.getCustomConditions().get(star.trimmer.VolumeControlTrimmerSizeOption.class).getVolumeControlBaseSizeOption())throw new IllegalStateException("Near size inactive");
                out.println("NEAR_START_SI\t"+start);out.println("NEAR_END_SI\t"+end);
                out.println("NEAR_RADIUS_SI\t"+cylinder.getRadius().getSIValue());
                out.println("NEAR_SIZE_PERCENT\t"+vc.getCustomValues().get(VolumeControlSize.class).getRelativeSizeScalar().getValue());
                out.println("LOCAL_MESH_READBACK_COMPLETE");
            }
            System.out.println("LOCAL_MESH_READBACK_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Local mesh readback failed",e);}
    }
}
