/* Configure independently meshed role-selected MRF fluid parts without meshing. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import star.base.neo.DoubleVector;
import star.base.neo.NeoObjectVector;
import star.base.neo.StringVector;
import star.common.*;
import star.meshing.*;
import star.prismmesher.NumPrismLayers;
import star.prismmesher.PrismLayerStretching;
import star.prismmesher.PrismThickness;
import star.prismmesher.PartsCustomizePrismMesh;
import star.prismmesher.PartsCustomPrismsOption;
import star.trimmer.VolumeControlTrimmerSizeOption;

public class ConfigureMrfMeshOperation extends StarMacro {
    private static final String COMPLETE = "MRF_MESH_OPERATION_CONFIGURED";
    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String mesh = object(spec, "mesh");
            String geometry = object(spec, "geometry");
            // ②R20 mesh repair 1 (independent-review finding C8 re-based the mechanism): the round's
            // read-only localization found the single cell below the VolumeChange gate at the thin
            // blade root, 5.9e-6 m inside a 21e-6 m thick fold wedge whose two faces share a 2.616 mm
            // edge at 164.3 deg - a feature the 2.05 mm minimum surface size cannot resolve. The repair
            // therefore refines the VOLUME locally around that measured location (and its two 120-degree
            // rotated copies, because the fold is 3-fold symmetric) and changes nothing else: no prism
            // layer count, no prism thickness, no geometry and no threshold.
            String map = read(new File(dir, "mrf_surface_map.json"));
            if (!"ok".equals(string(map, "status"))) throw new IllegalStateException("Surface-map audit failed");
            double base = number(mesh, "base_size_m");
            double bladeTarget = number(mesh, "blade_target_size_m");
            double bladeMin = number(mesh, "blade_min_size_m");
            double interfaceTarget = number(mesh, "interface_target_size_m");
            double interfaceMin = number(mesh, "interface_min_size_m");
            double layerValue = number(mesh, "prism_layers");
            double rotatingLayerValue = number(mesh, "rotating_prism_layers");
            int layers = (int) layerValue, rotatingLayers = (int) rotatingLayerValue;
            double prismTotal = number(mesh, "prism_total_m");
            double prismStretch = number(mesh, "prism_stretch");
            if (!(base > 0 && bladeMin > 0 && bladeMin < bladeTarget && bladeTarget < base &&
                    interfaceMin > 0 && interfaceMin < interfaceTarget && interfaceTarget < base &&
                    layers == layerValue && rotatingLayers == rotatingLayerValue &&
                    layers >= 3 && layers <= 20 && rotatingLayers >= 3 && rotatingLayers <= 20 &&
                    prismTotal > 0 && prismTotal < bladeTarget && prismStretch > 1.0 && prismStretch <= 2.0) ||
                    sim.getRegionManager().getRegions().size() != 2 || sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("MRF mesh prerequisites or explicit SI dimensions are invalid");
            MeshOperationManager manager = sim.get(MeshOperationManager.class);
            if (manager.getObjects().stream().anyMatch(op -> op instanceof AutoMeshOperation))
                throw new IllegalStateException("An auto mesh operation already exists");
            MeshOperationPart stationary = part(sim, string(geometry, "stationary_fluid_part"));
            MeshOperationPart rotating = part(sim, string(geometry, "rotating_fluid_part"));
            if (stationary == rotating) throw new IllegalStateException("Fluid roles overlap");
            List<PartSurface> stationaryInterfaces = new ArrayList<PartSurface>();
            List<PartSurface> rotatingInterfaces = new ArrayList<PartSurface>();
            for (String key : new String[] {"inner_upstream", "inner_downstream", "inner_side"}) {
                stationaryInterfaces.add(surface(stationary, string(map, key)));
                rotatingInterfaces.add(surface(rotating, string(map, key)));
            }
            if (new HashSet<PartSurface>(stationaryInterfaces).size() != 3 || new HashSet<PartSurface>(rotatingInterfaces).size() != 3)
                throw new IllegalStateException("Interface surface roles overlap");
            // The recovery and Boolean audits certified this part as rotating fluid with solid
            // walls. Its measured interface surfaces are removed by object identity, not prefix.
            List<PartSurface> rotorWalls = new ArrayList<PartSurface>();
            for (PartSurface p : rotating.getPartSurfaceManager().getObjects()) if (!rotatingInterfaces.contains(p)) rotorWalls.add(p);
            if (rotorWalls.isEmpty()) throw new IllegalStateException("Rotating fluid role has no recovered solid wall surface");
            String refinement = objectAllowMissing(spec, "mesh_local_refinement");
            double[] refineCenter = null, refineZ = null;
            double refineRadius = 0, refineSize = 0;
            if (refinement != null) {
                refineCenter = array(refinement, "center_m", 2);
                refineZ = array(refinement, "z_m", 2);
                refineRadius = number(refinement, "radius_m");
                refineSize = number(refinement, "size_m");
                if (!(refineRadius > 0 && refineSize > 0 && refineZ[0] < refineZ[1] &&
                        refineSize < bladeMin && refineRadius <= 0.05))
                    throw new IllegalStateException("Invalid declared local refinement");
            }
            String rotatingOperationName = string(mesh, "rotating_operation");
            String stationaryOperationName = string(mesh, "stationary_operation");
            if (rotatingOperationName.equals(stationaryOperationName)) throw new IllegalStateException("Mesh operation roles overlap");
            AutoMeshOperation rotatingOperation = create(sim, manager, rotating, rotatingOperationName, base, rotatingLayers, prismTotal, prismStretch);
            AutoMeshOperation stationaryOperation = create(sim, manager, stationary, stationaryOperationName, base, layers, prismTotal, prismStretch);
            control(rotatingOperation, "rotor-wall-control", rotorWalls, bladeMin/base, bladeTarget/base, false);
            control(rotatingOperation, "rotor-interface-control", rotatingInterfaces, interfaceMin/base, interfaceTarget/base, true);
            control(stationaryOperation, "stator-interface-control", stationaryInterfaces, interfaceMin/base, interfaceTarget/base, true);
            List<PartSurface> outerSurfaces = new ArrayList<PartSurface>();
            for (String key : new String[] {"outer_inlet", "outer_outlet", "outer_side"}) outerSurfaces.add(surface(stationary, string(map, key)));
            control(stationaryOperation, "outer-flow-control", outerSurfaces, 0.5, 1.0, true);
            int refinementCopies = 0;
            if (refinement != null) {
                Units meters = sim.getUnitsManager().getObject("m");
                LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
                for (int copy = 0; copy < 3; copy++) {
                    double angle = Math.toRadians(120.0 * copy);
                    double cx = refineCenter[0] * Math.cos(angle) - refineCenter[1] * Math.sin(angle);
                    double cy = refineCenter[0] * Math.sin(angle) + refineCenter[1] * Math.cos(angle);
                    SimpleCylinderPart region = sim.get(MeshPartFactory.class)
                            .createNewCylinderPart(sim.get(SimulationPartManager.class));
                    region.setPresentationName("ORIGINAL_LOCAL_REFINE_" + (120 * copy));
                    region.setCoordinateSystem(lab);
                    region.getStartCoordinate().setCoordinateSystem(lab);
                    region.getEndCoordinate().setCoordinateSystem(lab);
                    region.getStartCoordinate().setCoordinate(meters, meters, meters, new DoubleVector(new double[] {cx, cy, refineZ[0]}));
                    region.getEndCoordinate().setCoordinate(meters, meters, meters, new DoubleVector(new double[] {cx, cy, refineZ[1]}));
                    region.getRadius().setValueAndUnits(refineRadius, meters);
                    region.rebuildSimpleShapePart();
                    VolumeCustomMeshControl volume = rotatingOperation.getCustomMeshControls().createVolumeControl();
                    volume.setPresentationName("ORIGINAL_LOCAL_REFINE_" + (120 * copy));
                    volume.getGeometryObjects().setQuery(null);
                    volume.getGeometryObjects().setObjects(region);
                    volume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class).setVolumeControlBaseSizeOption(true);
                    volume.getCustomValues().get(VolumeControlSize.class).setRelativeSize(100.0 * refineSize
                            / rotatingOperation.getDefaultValues().get(BaseSize.class).getSIValue());
                    refinementCopies++;
                }
            }
            sim.saveState(new File(dir, "mrf_mesh_configured.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_mesh_configured.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"ok\",\"operations\":2,\"mesh_part_by_part\":true,\"stationary_operation\":\"" + esc(stationaryOperationName) +
                    "\",\"rotating_operation\":\"" + esc(rotatingOperationName) + "\",\"fluid_parts\":2,\"blade_surfaces\":" + rotorWalls.size() +
                    ",\"interface_surfaces\":6,\"base_size_m\":" + rotatingOperation.getDefaultValues().get(BaseSize.class).getSIValue() +
                    ",\"blade_target_size_m\":" + bladeTarget + ",\"blade_min_size_m\":" + bladeMin +
                    ",\"interface_target_size_m\":" + interfaceTarget + ",\"interface_min_size_m\":" + interfaceMin +
                    ",\"prism_layers\":" + stationaryOperation.getDefaultValues().get(NumPrismLayers.class).getNumLayers() +
                    ",\"rotating_prism_layers\":" + rotatingOperation.getDefaultValues().get(NumPrismLayers.class).getNumLayers() +
                    ",\"prism_total_m\":" + rotatingOperation.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().getSIValue() +
                    ",\"prism_stretch\":" + rotatingOperation.getDefaultValues().get(PrismLayerStretching.class).getStretching() +
                    ",\"local_refinement\":{\"copies\":" + refinementCopies +
                    ",\"radius_m\":" + refineRadius + ",\"size_m\":" + refineSize +
                    ",\"center_m\":[" + (refineCenter == null ? "" : refineCenter[0] + "," + refineCenter[1]) + "]" +
                    ",\"z_m\":[" + (refineZ == null ? "" : refineZ[0] + "," + refineZ[1]) + "]}"
                    + ",\"selection_basis\":\"CaseSpec fluid roles and measured interface surface ownership\",\"interface_and_outer_prisms_disabled\":true,\"actual_prism_coverage\":\"not_measured\",\"mesh_generated\":false,\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) { throw new RuntimeException("MRF mesh operation configuration failed", failure); }
    }
    private static AutoMeshOperation create(Simulation sim, MeshOperationManager manager, MeshOperationPart part,
            String name, double base, int layers, double total, double stretch) {
        AutoMeshOperation op = manager.createAutoMeshOperation(new StringVector(new String[] {
            "star.resurfacer.ResurfacerAutoMesher", "star.resurfacer.AutomaticSurfaceRepairAutoMesher",
            "star.trimmer.TrimmerAutoMesher", "star.prismmesher.PrismAutoMesher"}), new NeoObjectVector(new Object[] {part}));
        op.setPresentationName(name);
        op.setMeshPartByPart(true);
        op.setVerboseOutput(true);
        Units meters = sim.getUnitsManager().getObject("m");
        op.getDefaultValues().get(BaseSize.class).setValueAndUnits(base, meters);
        op.getDefaultValues().get(NumPrismLayers.class).setNumLayers(layers);
        op.getDefaultValues().get(PrismThickness.class).setAbsoluteSize(total, meters);
        op.getDefaultValues().get(PrismLayerStretching.class).setStretching(stretch);
        if (Math.abs(op.getDefaultValues().get(BaseSize.class).getSIValue()-base) > 1e-12 ||
                op.getDefaultValues().get(NumPrismLayers.class).getNumLayers() != layers ||
                Math.abs(op.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().getSIValue()-total) > 1e-12 ||
                Math.abs(op.getDefaultValues().get(PrismLayerStretching.class).getStretching()-stretch) > 1e-12 || !op.getMeshPartByPart())
            throw new IllegalStateException("Mesh settings did not read back from STAR");
        return op;
    }
    private static void control(AutoMeshOperation operation, String name, List<PartSurface> surfaces, double min, double target, boolean disablePrisms) {
        SurfaceCustomMeshControl c = operation.getCustomMeshControls().createSurfaceControl();
        c.setPresentationName(name);
        c.getGeometryObjects().setObjects(surfaces.toArray(new PartSurface[0]));
        c.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class).setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
        c.getCustomConditions().get(PartsTargetSurfaceSizeOption.class).setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
        c.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().setValue(min*100.0);
        c.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().setValue(target*100.0);
        if (disablePrisms) {
            c.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions().setSelected(PartsCustomPrismsOption.Type.DISABLE);
            c.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismControls().setOverrideBoundaryDefault(true);
            if (!"DISABLE".equals(c.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions().getSelectedElement().toString()))
                throw new IllegalStateException("Nonwall prism disable option did not read back");
        }
        if (Math.abs(c.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar().getValue()-min*100.0) > 1e-9 ||
                Math.abs(c.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar().getValue()-target*100.0) > 1e-9)
            throw new IllegalStateException("Surface mesh-control sizes did not read back");
    }
    private static MeshOperationPart part(Simulation sim, String name) { Object p=sim.get(SimulationPartManager.class).getPart(name);if(!(p instanceof MeshOperationPart))throw new IllegalStateException("Role is not a Boolean fluid part");return (MeshOperationPart)p; }
    private static PartSurface surface(MeshOperationPart part, String name) { PartSurface p=part.getPartSurfaceManager().getPartSurface(name);if(p==null)throw new IllegalStateException("Measured surface missing: "+name);return p; }
    private static String read(File f) throws Exception { return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8); }
    private static String objectAllowMissing(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(j);return m.find()?m.group(1):null; }
    private static double[] array(String j,String k,int n) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);String[] w=m.group(1).split(",");if(w.length!=n)throw new IllegalArgumentException("Wrong vector length "+k);double[] v=new double[n];for(int i=0;i<n;i++){v[i]=Double.parseDouble(w[i].trim());if(!Double.isFinite(v[i]))throw new IllegalArgumentException("Nonfinite "+k);}return v; }
    private static String object(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing object "+k);return m.group(1); }
    private static String string(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing string "+k);return m.group(1); }
    private static double number(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing number "+k);double v=Double.parseDouble(m.group(1));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+k);return v; }
    private static String esc(String s) { return s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t"); }
}
