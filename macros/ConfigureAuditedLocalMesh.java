/* Candidate only: configure a mesh on already mapped fluid geometry; do not generate it.
 * Inputs: sessionDir/local_mesh.properties, from the audit-bound Python planner.
 * No name/axis/diameter assumptions. All lookup and role checks precede mutation.
 * Requires verified isolated worker; compile success never grants execution.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.meshing.*;
import star.prismmesher.*;
import star.trimmer.VolumeControlTrimmerSizeOption;

public class ConfigureAuditedLocalMesh extends StarMacro {
    private Properties p;
    private String required(String key) {
        String v=p.getProperty(key);
        if(v==null || v.isBlank()) throw new IllegalArgumentException("Missing "+key);
        return v;
    }
    private double num(String key) {
        double v=Double.parseDouble(required(key));
        if(!Double.isFinite(v)) throw new IllegalArgumentException("Nonfinite "+key);
        return v;
    }
    private int count(String key, int lo, int hi) {
        int v=Integer.parseInt(required(key));
        if(v<lo || v>hi) throw new IllegalArgumentException("Invalid "+key);
        return v;
    }
    private boolean wall(String role) {
        return Set.of("blade_wall","hub_wall","stationary_wall").contains(role);
    }
    private Boundary boundaryRole(Simulation sim, PartSurface s, String role) {
        Boundary match=null;
        for(Region r:sim.getRegionManager().getRegions())
            for(Boundary b:r.getBoundaryManager().getBoundaries())
                if(b.getPartSurfaceGroup().getObjects().contains(s)) {
                    BoundaryType t=b.getBoundaryType();
                    boolean ok=wall(role) ? t instanceof WallBoundary :
                        role.equals("inlet") ? t instanceof InletBoundary :
                        role.equals("outlet") ? t instanceof PressureBoundary :
                        role.equals("farfield") ? t instanceof SymmetryBoundary :
                        role.equals("interface") && t instanceof InternalBoundary;
                    // Uninitialized internal boundaries can have no part surfaces.
                    // Resolve the interface through its actual parent boundaries instead.
                    if(role.equals("interface") && t instanceof WallBoundary) {
                        for(Interface inter:sim.getInterfaceManager().getObjects())
                            if(inter instanceof BoundaryInterface) {
                                BoundaryInterface bi=(BoundaryInterface)inter;
                                if(bi.getParentBoundary0()==b || bi.getParentBoundary1()==b) ok=true;
                            }
                    }
                    if(!ok) throw new IllegalStateException("Boundary role mismatch: "+s.getQualifiedName());
                    if(match!=null && match!=b) throw new IllegalStateException("Ambiguous boundary mapping");
                    match=b;
                }
        if(match==null) throw new IllegalStateException("No mapped boundary for "+s.getQualifiedName()+" / "+role);
        return match;
    }
    @Override public void execute() {
        Simulation sim=getActiveSimulation();
        File dir=sim.getSessionDirFile();
        boolean mutationStarted=false;
        try {
            p=new Properties();
            try(Reader in=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)) {p.load(in);}
            if(!required("schema_version").equals("1")) throw new IllegalArgumentException("Wrong schema");
            for(String key:List.of("source_sha256","audit_sha256","controls_sha256"))
                if(!required(key).matches("[a-f0-9]{64}")) throw new IllegalArgumentException("Missing provenance");
            int n=count("part_count",2,2), ns=count("surface_count",5,10000), ne=count("edge_count",0,10000);
            double base=num("base_size_m"), thick=num("prism_total_m"), stretch=num("prism_stretching");
            int layers=count("prism_layers",3,20);
            if(base<=0 || thick<=0 || stretch<1 || stretch>2) throw new IllegalArgumentException("Bad mesh sizes");
            String[] prismKeys={"minimum_thickness_pct","layer_chopping_pct","boundary_march_angle_deg"};
            int optionCount=0;for(String key:prismKeys)if(p.containsKey("prism_mesher."+key))optionCount++;
            if(optionCount!=0 && optionCount!=3)throw new IllegalArgumentException("Incomplete prism_mesher options");
            double[] prismOptions=new double[3];
            if(optionCount==3){
                for(int i=0;i<3;i++)prismOptions[i]=num("prism_mesher."+prismKeys[i]);
                if(prismOptions[0]<=0 || prismOptions[0]>100 || prismOptions[1]<0 || prismOptions[1]>=100 ||
                   prismOptions[2]<=0 || prismOptions[2]>90)throw new IllegalArgumentException("Invalid prism_mesher options");
            }
            for(Object key:p.keySet())if(key.toString().startsWith("prism_mesher.") && !Set.of(prismKeys).contains(key.toString().substring(13)))
                throw new IllegalArgumentException("Unknown prism_mesher option");
            MeshOperationManager mm=sim.get(MeshOperationManager.class);
            for(Object o:mm.getObjects()) if(o instanceof AutoMeshOperation)
                throw new IllegalStateException("Existing mesh operation; use geometry-only isolated input");
            List<GeometryPart> parts=new ArrayList<>();
            Map<String,PartSurface> surfaces=new HashMap<>();
            Map<String,PartCurve> curves=new HashMap<>();
            Set<String> selectedParts=new HashSet<>();
            for(int i=0;i<n;i++) {
                String name=required("part."+i);
                if(!selectedParts.add(name)) throw new IllegalArgumentException("Duplicate part");
                GeometryPart part=sim.get(SimulationPartManager.class).getPart(name);
                if(part==null || !part.getQualifiedName().equals(name)) throw new IllegalStateException("Ambiguous/missing part "+name);
                parts.add(part);
                for(PartSurface s:part.getPartSurfaces())
                    if(surfaces.put(s.getQualifiedName(),s)!=null) throw new IllegalStateException("Duplicate surface");
                for(PartCurve c:part.getPartCurves())
                    if(curves.put(c.getQualifiedName(),c)!=null) throw new IllegalStateException("Duplicate curve");
            }
            if(surfaces.size()!=ns) throw new IllegalStateException("Surface inventory changed");
            List<PartSurface> targets=new ArrayList<>();
            List<PartCurve> edges=new ArrayList<>();
            Set<String> seen=new HashSet<>(), roles=new HashSet<>();
            Map<String,List<Boundary>> pairs=new HashMap<>();
            for(int i=0;i<ns;i++) {
                String key="surface."+i+".", name=required(key+"selector"), role=required(key+"role");
                PartSurface s=surfaces.get(name);
                if(s==null || !seen.add(name)) throw new IllegalStateException("Missing/duplicate surface "+name);
                if(!Set.of("blade_wall","hub_wall","stationary_wall","inlet","outlet","farfield","interface").contains(role))
                    throw new IllegalArgumentException("Unknown role "+role);
                roles.add(role);
                if(num(key+"min_size_m")<=0 || num(key+"target_size_m")<num(key+"min_size_m")) throw new IllegalArgumentException("Bad surface size");
                Boundary mapped=boundaryRole(sim,s,role);
                if(role.equals("interface")) pairs.computeIfAbsent(required(key+"pair"),x->new ArrayList<>()).add(mapped);
                targets.add(s);
            }
            if(!roles.containsAll(Set.of("blade_wall","inlet","outlet","farfield","interface"))) throw new IllegalArgumentException("Missing roles");
            for(List<Boundary> pair:pairs.values()) {
                if(pair.size()!=2 || pair.get(0)==pair.get(1)) throw new IllegalArgumentException("Invalid interface pair");
                boolean paired=false;
                for(Interface inter:sim.getInterfaceManager().getObjects())
                    if(inter instanceof BoundaryInterface) {
                        BoundaryInterface bi=(BoundaryInterface)inter;
                        if(pair.contains(bi.getParentBoundary0()) && pair.contains(bi.getParentBoundary1())) paired=true;
                    }
                if(!paired) throw new IllegalStateException("Declared pair is not an existing boundary interface");
            }
            Set<String> seenCurves=new HashSet<>();
            for(int i=0;i<ne;i++) {
                String k="edge."+i+".", name=required(k+"selector");
                PartCurve c=curves.get(name);
                if(c==null || !seenCurves.add(name)) throw new IllegalStateException("Missing/duplicate curve "+name);
                if(num(k+"min_size_m")<=0 || num(k+"target_size_m")<num(k+"min_size_m")) throw new IllegalArgumentException("Bad edge size");
                edges.add(c);
            }
            double[] start=new double[3], end=new double[3];
            for(int i=0;i<3;i++){start[i]=num("near.start_m."+i);end[i]=num("near.end_m."+i);}
            double radius=num("near.radius_m"), near=num("near.target_size_m");
            if(Arrays.equals(start,end) || radius<=0 || near<=0 || near>=base) throw new IllegalArgumentException("Bad near field");
            if(sim.get(SimulationPartManager.class).getParts().stream().anyMatch(x->x.getPresentationName().equals("LOCAL_MESH_NEAR_FIELD")))
                throw new IllegalStateException("Near-field output name already exists");
            // Mutation begins here. The worker must discard the isolated copy on any failure.
            mutationStarted=true;
            Units meters=sim.getUnitsManager().getObject("m");
            AutoMeshOperation op=mm.createAutoMeshOperation(new StringVector(new String[]{
                "star.resurfacer.ResurfacerAutoMesher","star.trimmer.TrimmerAutoMesher","star.prismmesher.PrismAutoMesher"}),
                new NeoObjectVector(parts.toArray()));
            op.setPresentationName("AUDITED_LOCAL_MESH");
            // STAR 2502 Trimmer requires per-part execution for multiple fluid parts.
            // Same setting as the installed open-water tutorial.
            op.setMeshPartByPart(true);
            op.getDefaultValues().get(BaseSize.class).setValueAndUnits(base,meters);
            op.getDefaultValues().get(NumPrismLayers.class).setNumLayers(layers);
            op.getDefaultValues().get(PrismThickness.class).setAbsoluteSize(thick,meters);
            op.getDefaultValues().get(PrismLayerStretching.class).setStretching(stretch);
            if(optionCount==3){
                PrismAutoMesher prism=null;
                for(MesherBase m:op.getMeshersCollection())if(m instanceof PrismAutoMesher)prism=(PrismAutoMesher)m;
                if(prism==null)throw new IllegalStateException("Prism mesher missing");
                prism.getDefaultValuesManager().get(PrismLayerMinimumThickness.class).setValue(prismOptions[0]);
                prism.getDefaultValuesManager().get(PrismLayerReductionPercentage.class).setValue(prismOptions[1]);
                prism.getDefaultValuesManager().get(PrismLayerBoundaryMarchAngle.class).setValue(prismOptions[2]);
            }

            for(int i=0;i<ns;i++) {
                String k="surface."+i+".";
                SurfaceCustomMeshControl c=op.getCustomMeshControls().createSurfaceControl();
                c.setPresentationName("LOCAL_SURFACE_"+i);
                c.getGeometryObjects().setQuery(null); c.getGeometryObjects().setObjects(targets.get(i));
                c.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class).setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
                c.getCustomConditions().get(PartsTargetSurfaceSizeOption.class).setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
                c.getCustomValues().get(PartsMinimumSurfaceSize.class).setRelativeSize(100*num(k+"min_size_m")/base);
                c.getCustomValues().get(PartsTargetSurfaceSize.class).setRelativeSize(100*num(k+"target_size_m")/base);
                // Walls inherit explicit global prism values; open boundaries/interfaces disable them.
                c.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions()
                    .setSelected(wall(required(k+"role")) ? PartsCustomPrismsOption.Type.PARENT : PartsCustomPrismsOption.Type.DISABLE);
            }
            for(int i=0;i<ne;i++) {
                String k="edge."+i+".";
                CurveCustomMeshControl c=op.getCustomMeshControls().createCurveControl();
                c.setPresentationName("LOCAL_EDGE_"+i); c.getGeometryObjects().setQuery(null); c.getGeometryObjects().setObjects(edges.get(i));
                c.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class).setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
                c.getCustomConditions().get(PartsTargetSurfaceSizeOption.class).setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
                c.getCustomValues().get(PartsMinimumSurfaceSize.class).setRelativeSize(100*num(k+"min_size_m")/base);
                c.getCustomValues().get(PartsTargetSurfaceSize.class).setRelativeSize(100*num(k+"target_size_m")/base);
            }
            SimpleCylinderPart cylinder=sim.get(MeshPartFactory.class).createNewCylinderPart(sim.get(SimulationPartManager.class));
            cylinder.setPresentationName("LOCAL_MESH_NEAR_FIELD");
            cylinder.setCoordinateSystem(sim.getCoordinateSystemManager().getLabCoordinateSystem());
            cylinder.getStartCoordinate().setCoordinate(meters,meters,meters,new DoubleVector(start));
            cylinder.getEndCoordinate().setCoordinate(meters,meters,meters,new DoubleVector(end));
            cylinder.getRadius().setValueAndUnits(radius,meters); cylinder.rebuildSimpleShapePart();
            VolumeCustomMeshControl vc=op.getCustomMeshControls().createVolumeControl();
            vc.setPresentationName("LOCAL_NEAR_FIELD"); vc.getGeometryObjects().setQuery(null); vc.getGeometryObjects().setObjects(cylinder);
            vc.getCustomConditions().get(VolumeControlTrimmerSizeOption.class).setVolumeControlBaseSizeOption(true);
            vc.getCustomValues().get(VolumeControlSize.class).setRelativeSize(100*near/base);
            sim.saveState(new File(dir,"local_mesh_configured.sim").getAbsolutePath());
            Properties evidence=new Properties();
            evidence.setProperty("status","CONFIGURED_NOT_MESHED");
            evidence.setProperty("controls_sha256",required("controls_sha256"));
            evidence.setProperty("audit_sha256",required("audit_sha256"));
            evidence.setProperty("source_sha256",required("source_sha256"));
            evidence.setProperty("surface_count",Integer.toString(ns)); evidence.setProperty("edge_count",Integer.toString(ne));
            evidence.setProperty("engineering_verified","false");
            try(Writer w=Files.newBufferedWriter(new File(dir,"local_mesh_configured.properties").toPath(),StandardCharsets.UTF_8)){evidence.store(w,"Configuration only; reopen and audit before meshing");}
            System.out.println("AUDITED_LOCAL_MESH_CONFIGURED");
        } catch(Exception e) {
            try {
                Properties failure=new Properties();
                failure.setProperty("status","REJECTED");
                failure.setProperty("mutation_started",Boolean.toString(mutationStarted));
                failure.setProperty("exception_class",e.getClass().getName());
                failure.setProperty("message",String.valueOf(e.getMessage()));
                try(Writer w=Files.newBufferedWriter(new File(dir,"local_mesh_failure.properties").toPath(),StandardCharsets.UTF_8)){failure.store(w,"Failed trial; isolated input unchanged when mutation_started=false");}
            }catch(Exception diagnosticFailure){e.addSuppressed(diagnosticFailure);}
            throw new RuntimeException("Local mesh configuration rejected: "+e.getMessage(),e);
        }
    }
}
