/* AIR-3B A2b: STAR-native mesh for the two-block MRF domain.
 *
 * Follows the mesh-control structure of the installed Siemens 2502 tutorial
 * MovingReferenceFramesMarinePropellerInOpenWater (Resurfacer + Trimmer + Prism,
 * per-part meshing, an explicit surface control on the rotating walls, a far-field
 * surface control, a volumetric refinement cylinder around the propeller) and the
 * project's own executed ConfigureAuditedLocalMesh patterns:
 *   - per-part meshing, which STAR 2502 requires for a multi-part Trimmer;
 *   - prism layers explicitly disabled on the open boundaries instead of merely
 *     changing their boundary type;
 *   - the audited setter/readback pairs for base size, layer count, total thickness
 *     and stretching, plus the typed prism options.
 *
 * Runs on an already-built two-region MRF simulation. Every size is supplied by
 * environment so the same operation serves the coarse/medium/fine levels and the
 * sensitivity matrix without code changes.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.cadmodeler.*;
import star.common.*;
import star.meshing.*;
import star.prismmesher.*;
import star.trimmer.*;

public class BuildAir3bMrfMesh extends StarMacro {

    private static final String COMPLETE = "AIR3B_MRF_MESH_COMPLETE";

    private final StringBuilder report = new StringBuilder();

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            File output = new File(dir, envText("AIR3B_MESH_OUTPUT", "mrf_mesh.sim"));
            if (output.exists()) throw new IllegalStateException("Output already exists: " + output.getName());

            Collection<Region> regions = sim.getRegionManager().getRegions();
            if (regions.size() != 2) throw new IllegalStateException("Expected two MRF regions, got " + regions.size());
            if (sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("Expected three MRF internal interfaces");
            // On a simulation that has never been meshed the "Volume Mesh" representation does
            // not exist at all, so the lookup is expected to fail before the first mesh.
            FvRepresentation existing = findVolumeMesh(sim);
            report.append("EXISTING_VOLUME_MESH\t")
                  .append(existing == null ? "none" : existing.getPresentationName() + ":" + existing.getCellCount())
                  .append('\n');
            if (existing != null && existing.getCellCount() > 0)
                throw new IllegalStateException("Simulation already carries a volume mesh");

            Units metres = sim.getUnitsManager().getObject("m");
            Units dimless = sim.getUnitsManager().getObject("");

            // Boundary roles must be set before meshing, and prism layers must then be
            // disabled explicitly on the open boundaries: changing the boundary type alone
            // does not stop the prism mesher from growing layers there.
            List<GeometryObject> outerSurfaces = new ArrayList<>();
            List<GeometryObject> wallSurfaces = new ArrayList<>();
            List<GeometryObject> interfaceSurfaces = new ArrayList<>();
            int outerBoundaries = 0, wallBoundaries = 0, derivedBoundaries = 0, interfaceBoundaries = 0;
            for (Region region : regions) {
                for (Boundary b : region.getBoundaryManager().getBoundaries()) {
                    String n = b.getPresentationName();
                    // Creating a boundary interface adds a derived in-place boundary per
                    // region (STAR names it "<parent> [<interface>]" and types it
                    // InternalBoundary). Those carry no part surfaces of their own -- the
                    // parent boundary keeps them -- so they are excluded from the surface
                    // selection and from the non-empty-group assertion.
                    boolean derived = b.getBoundaryType() instanceof InternalBoundary || n.contains("[");
                    if (derived) {
                        derivedBoundaries++;
                        report.append("DERIVED_BOUNDARY\t").append(region.getPresentationName())
                              .append('\t').append(n).append('\t').append(b.getBoundaryType().getClass().getSimpleName())
                              .append('\n');
                        continue;
                    }
                    boolean outer = n.equals("INLET") || n.equals("OUTLET") || n.equals("FARFIELD");
                    boolean interfacePatch = n.startsWith("INTERFACE");
                    Collection<PartSurface> surfaces = b.getPartSurfaceGroup().getObjects();
                    if (surfaces.isEmpty())
                        throw new IllegalStateException("Selected boundary has no part surfaces: " + n);
                    report.append("BOUNDARY\t").append(region.getPresentationName()).append('\t').append(n)
                          .append("\tsurfaces=").append(surfaces.size()).append('\n');
                    if (outer) {
                        if (n.equals("INLET")) b.setBoundaryType(InletBoundary.class);
                        else if (n.equals("OUTLET")) b.setBoundaryType(PressureBoundary.class);
                        else b.setBoundaryType(SymmetryBoundary.class);
                        outerSurfaces.addAll(surfaces);
                        outerBoundaries++;
                    } else if (interfacePatch) {
                        interfaceSurfaces.addAll(surfaces);
                        interfaceBoundaries++;
                    } else {
                        wallSurfaces.addAll(surfaces);
                        wallBoundaries++;
                    }
                }
            }
            report.append("SETTING\tderived_boundary_count\t").append(derivedBoundaries).append('\n');
            if (outerBoundaries != 3)
                throw new IllegalStateException("Expected INLET, OUTLET and FARFIELD; found " + outerBoundaries);
            if (wallBoundaries < 3)
                throw new IllegalStateException("Expected rotating wall and interface boundaries; found " + wallBoundaries);
            for (Region region : regions) {
                for (Boundary b : region.getBoundaryManager().getBoundaries()) {
                    String n = b.getPresentationName();
                    if (n.equals("INLET") || n.equals("OUTLET") || n.equals("FARFIELD"))
                        report.append("BOUNDARY_TYPE\t").append(n).append('\t')
                              .append(b.getBoundaryType().getClass().getSimpleName()).append('\n');
                }
            }

            List<GeometryPart> parts = new ArrayList<>(sim.get(SimulationPartManager.class).getParts());
            if (parts.size() != 2) throw new IllegalStateException("Expected two geometry parts, got " + parts.size());

            AutoMeshOperation op = sim.get(MeshOperationManager.class).createAutoMeshOperation(
                    new StringVector(new String[] {
                            "star.resurfacer.ResurfacerAutoMesher",
                            "star.trimmer.TrimmerAutoMesher",
                            "star.prismmesher.PrismAutoMesher" }),
                    new NeoObjectVector(parts.toArray()));
            // STAR 2502's Trimmer rejects multiple parts in one Auto Mesh Operation, so
            // per-part meshing is mandatory here, not an optimisation. The switch is read
            // from the environment and a request to disable it is rejected outright rather
            // than silently ignored.
            boolean perPart = !"false".equalsIgnoreCase(envText("AIR3B_MESH_PART_BY_PART", "true"));
            if (!perPart)
                throw new IllegalStateException("AIR3B_MESH_PART_BY_PART=false is not usable: STAR 2502's "
                        + "Trimmer cannot accommodate multiple parts in one Auto Mesh Operation");
            op.setMeshPartByPart(true);
            op.setVerboseOutput(true);

            double base = env("AIR3B_BASE_SIZE_M", 0.150);
            double bladeSize = env("AIR3B_BLADE_SIZE_M", 0.0020);
            double bladeMin = env("AIR3B_BLADE_MIN_M", 0.00050);
            double edgeSize = env("AIR3B_EDGE_SIZE_M", 0.00080);
            double edgeMin = env("AIR3B_EDGE_MIN_M", 0.00030);
            double nearSize = env("AIR3B_NEAR_SIZE_M", 0.0050);
            double nearR = env("AIR3B_NEAR_R_M", 0.240);
            double nearH = env("AIR3B_NEAR_HALF_H_M", 0.140);
            double midSize = env("AIR3B_MID_SIZE_M", 0.0120);
            double midR = env("AIR3B_MID_R_M", 0.350);
            double midH = env("AIR3B_MID_HALF_H_M", 0.250);
            double farSize = env("AIR3B_FAR_SIZE_M", 0.150);
            double interfaceSize = env("AIR3B_INTERFACE_SIZE_M", 0.0060);
            int layers = (int) env("AIR3B_PRISM_LAYERS", 5);
            double prismTotal = env("AIR3B_PRISM_TOTAL_M", 0.0005);
            double stretch = env("AIR3B_PRISM_STRETCH", 1.2);
            double coreRatio = env("AIR3B_CORE_PRISM_RATIO", 8.0);
            int curvaturePts = (int) env("AIR3B_CURVATURE_POINTS", 36);
            double minThicknessPct = env("AIR3B_PRISM_MIN_THICKNESS_PCT", 1.0);
            double layerChoppingPct = env("AIR3B_PRISM_CHOPPING_PCT", 5.0);
            double marchAngleDeg = env("AIR3B_PRISM_MARCH_ANGLE_DEG", 25.0);

            AutoMeshDefaultValuesManager defaults = op.getDefaultValues();
            defaults.get(BaseSize.class).setValueAndUnits(base, metres);
            defaults.get(NumPrismLayers.class).setNumLayers(layers);
            defaults.get(PrismThickness.class).setAbsoluteSize(prismTotal, metres);
            defaults.get(PrismLayerStretching.class).setStretching(stretch);

            PrismAutoMesher prism = null;
            TrimmerAutoMesher trimmer = null;
            Set<String> meshers = new TreeSet<>();
            for (MesherBase m : op.getMeshersCollection()) {
                meshers.add(m.getClass().getName());
                if (m instanceof PrismAutoMesher) prism = (PrismAutoMesher) m;
                if (m instanceof TrimmerAutoMesher) trimmer = (TrimmerAutoMesher) m;
            }
            if (prism == null || trimmer == null)
                throw new IllegalStateException("Prism or Trimmer mesher missing: " + meshers);
            if (!meshers.equals(new TreeSet<>(Arrays.asList("star.meshing.AutoMesherBase", "star.prismmesher.PrismAutoMesher",
                    "star.resurfacer.ResurfacerAutoMesher", "star.trimmer.TrimmerAutoMesher"))))
                report.append("MESHERS\t").append(meshers).append('\n');

            trimmer.setDoMeshAlignment(true);
            prism.getDefaultValuesManager().get(PrismLayerMinimumThickness.class).setValue(minThicknessPct);
            prism.getDefaultValuesManager().get(PrismLayerReductionPercentage.class).setValue(layerChoppingPct);
            prism.getDefaultValuesManager().get(PrismLayerBoundaryMarchAngle.class).setValue(marchAngleDeg);

            // Read back every value that the mesh depends on, so a silently ineffective
            // setter cannot pass as configured.
            report.append("READBACK\tbase_size_si\t").append(defaults.get(BaseSize.class).getSIValue()).append('\n');
            report.append("READBACK\tprism_layers\t").append(defaults.get(NumPrismLayers.class).getNumLayers()).append('\n');
            report.append("READBACK\tprism_total_si\t")
                  .append(defaults.get(PrismThickness.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("READBACK\tprism_stretching\t")
                  .append(defaults.get(PrismLayerStretching.class).getStretching()).append('\n');
            report.append("READBACK\tmesh_part_by_part\t").append(op.getMeshPartByPart()).append('\n');
            report.append("READBACK\tprism_min_thickness_pct\t")
                  .append(prism.getDefaultValuesManager().get(PrismLayerMinimumThickness.class).getValue()).append('\n');
            report.append("READBACK\tprism_layer_chopping_pct\t")
                  .append(prism.getDefaultValuesManager().get(PrismLayerReductionPercentage.class).getValue()).append('\n');
            report.append("READBACK\tprism_march_angle_deg\t")
                  .append(prism.getDefaultValuesManager().get(PrismLayerBoundaryMarchAngle.class).getValue()).append('\n');
            report.append("MESHER\t").append(meshers).append('\n');
            for (Object[] kv : new Object[][] {
                    { "base_size_m", base }, { "blade_size_m", bladeSize }, { "blade_min_m", bladeMin },
                    { "edge_size_m", edgeSize }, { "edge_min_m", edgeMin }, { "near_size_m", nearSize },
                    { "near_radius_m", nearR }, { "near_half_height_m", nearH }, { "mid_size_m", midSize },
                    { "mid_radius_m", midR }, { "mid_half_height_m", midH }, { "far_size_m", farSize },
                    { "prism_layers", (double) layers }, { "prism_total_m", prismTotal },
                    { "prism_stretch", stretch }, { "core_prism_ratio", coreRatio },
                    { "curvature_points", (double) curvaturePts } })
                report.append("SETTING\t").append(kv[0]).append('\t').append(kv[1]).append('\n');

            try {
                MaxTrimmerSizeToPrismThicknessRatio ratio =
                        defaults.get(MaxTrimmerSizeToPrismThicknessRatio.class);
                ratio.setLimitCellSizeByPrismThickness(true);
                ratio.setSizeThicknessRatio(coreRatio);
                report.append("SETTING\tcore_prism_ratio_applied\ttrue\n");
            } catch (Throwable notRegistered) {
                report.append("SETTING\tcore_prism_ratio_applied\tfalse\n");
                report.append("SETTING\tcore_prism_ratio_error\t")
                      .append(String.valueOf(notRegistered.getMessage()).replace('\n', ' ')).append('\n');
            }

            // Surface control on the rotating walls and the interface rings.
            SurfaceCustomMeshControl walls = op.getCustomMeshControls().createSurfaceControl();
            walls.setPresentationName("AIR3B_ROTOR_WALLS");
            walls.getGeometryObjects().setQuery(null);
            walls.getGeometryObjects().setObjects(wallSurfaces.toArray(new GeometryObject[0]));
            walls.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            walls.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class)
                    .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
            walls.getCustomConditions().get(PartsSurfaceCurvatureOption.class)
                    .setSelected(PartsSurfaceCurvatureOption.Type.CUSTOM_VALUES);
            walls.getCustomValues().get(PartsTargetSurfaceSize.class).setAbsoluteSizeValue(bladeSize, metres);
            walls.getCustomValues().get(PartsMinimumSurfaceSize.class).setAbsoluteSizeValue(bladeMin, metres);
            walls.getCustomValues().get(SurfaceCurvature.class).setNumPointsAroundCircle((double) curvaturePts);
            report.append("SETTING\twall_surface_count\t").append(wallSurfaces.size()).append('\n');

            // Interface patches: the audited interface is 1.064 m2, so a blade-sized target there
            // alone would demand a quarter of a million surface faces. They get their own coarser
            // target and no prism layers, matching the project's executed local-mesh configuration.
            SurfaceCustomMeshControl iface = op.getCustomMeshControls().createSurfaceControl();
            iface.setPresentationName("AIR3B_INTERFACE_PATCHES");
            iface.getGeometryObjects().setQuery(null);
            iface.getGeometryObjects().setObjects(interfaceSurfaces.toArray(new GeometryObject[0]));
            iface.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            iface.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions()
                    .setSelected(PartsCustomPrismsOption.Type.DISABLE);
            iface.getCustomConditions().get(PartsCustomizePrismMesh.class)
                    .getCustomPrismControls().setOverrideBoundaryDefault(true);
            iface.getCustomValues().get(PartsTargetSurfaceSize.class).setAbsoluteSizeValue(interfaceSize, metres);
            report.append("SETTING\tinterface_surface_count\t").append(interfaceSurfaces.size()).append('\n');

            // Outer boundaries: coarse size and no prism layers at all.
            SurfaceCustomMeshControl far = op.getCustomMeshControls().createSurfaceControl();
            far.setPresentationName("AIR3B_FARFIELD");
            far.getGeometryObjects().setQuery(null);
            far.getGeometryObjects().setObjects(outerSurfaces.toArray(new GeometryObject[0]));
            far.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            far.getCustomConditions().get(PartsCustomizePrismMesh.class).getCustomPrismOptions()
                    .setSelected(PartsCustomPrismsOption.Type.DISABLE);
            far.getCustomConditions().get(PartsCustomizePrismMesh.class)
                    .getCustomPrismControls().setOverrideBoundaryDefault(true);
            far.getCustomValues().get(PartsTargetSurfaceSize.class).setAbsoluteSizeValue(farSize, metres);
            report.append("SETTING\touter_surface_count\t").append(outerSurfaces.size()).append('\n');

            // Feature-curve control on every imported part curve. Recorded explicitly so an
            // import that produces no curves cannot be mistaken for applied edge refinement.
            List<GeometryObject> curves = new ArrayList<>();
            for (GeometryPart part : parts) {
                if (part instanceof SolidModelPart) {
                    for (PartCurve curve : ((SolidModelPart) part).getPartCurveManager().getPartCurves())
                        curves.add(curve);
                }
            }
            boolean edgeControlBound = !curves.isEmpty();
            if (edgeControlBound) {
                CurveCustomMeshControl edges = op.getCustomMeshControls().createCurveControl();
                edges.setPresentationName("AIR3B_FEATURE_CURVES");
                edges.getGeometryObjects().setQuery(null);
                edges.getGeometryObjects().setObjects(curves.toArray(new GeometryObject[0]));
                edges.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                        .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
                edges.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class)
                        .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
                edges.getCustomValues().get(PartsTargetSurfaceSize.class).setAbsoluteSizeValue(edgeSize, metres);
                edges.getCustomValues().get(PartsMinimumSurfaceSize.class).setAbsoluteSizeValue(edgeMin, metres);
            }
            report.append("READBACK\twall_surface_size_si\t")
                  .append(walls.getCustomValues().get(PartsTargetSurfaceSize.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("READBACK\tinterface_surface_size_si\t")
                  .append(iface.getCustomValues().get(PartsTargetSurfaceSize.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("READBACK\tfar_surface_size_si\t")
                  .append(far.getCustomValues().get(PartsTargetSurfaceSize.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("SETTING\tedge_control_bound\t").append(edgeControlBound).append('\n');
            report.append("SETTING\tedge_curve_count\t").append(curves.size()).append('\n');

            // Volumetric refinement around the propeller, in two nested cylinders.
            VolumeCustomMeshControl nearVolume = op.getCustomMeshControls().createVolumeControl();
            nearVolume.setPresentationName("AIR3B_NEAR_VOLUME_CONTROL");
            nearVolume.getGeometryObjects().setQuery(null);
            nearVolume.getGeometryObjects().setObjects(
                    cylinder(sim, 0.0, -nearH, nearH, nearR, "AIR3B_NEAR_VOLUME"));
            // The size profile only takes effect when the trimmer volume-control condition is
            // switched on; the project's executed configuration sets exactly this flag, and its
            // readback throws if the near control is inactive.
            nearVolume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class)
                    .setVolumeControlBaseSizeOption(true);
            nearVolume.getCustomValues().get(VolumeControlSize.class).setAbsoluteSizeValue(nearSize, metres);

            VolumeCustomMeshControl midVolume = op.getCustomMeshControls().createVolumeControl();
            midVolume.setPresentationName("AIR3B_MID_VOLUME_CONTROL");
            midVolume.getGeometryObjects().setQuery(null);
            midVolume.getGeometryObjects().setObjects(
                    cylinder(sim, 0.0, -midH, midH, midR, "AIR3B_MID_VOLUME"));
            midVolume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class)
                    .setVolumeControlBaseSizeOption(true);
            midVolume.getCustomValues().get(VolumeControlSize.class).setAbsoluteSizeValue(midSize, metres);
            report.append("READBACK\tnear_volume_size_si\t")
                  .append(nearVolume.getCustomValues().get(VolumeControlSize.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("READBACK\tmid_volume_size_si\t")
                  .append(midVolume.getCustomValues().get(VolumeControlSize.class).getAbsoluteSizeValue().getSIValue()).append('\n');
            report.append("READBACK\tnear_volume_active\t")
                  .append(nearVolume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class)
                          .getVolumeControlBaseSizeOption()).append('\n');
            report.append("READBACK\tmid_volume_active\t")
                  .append(midVolume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class)
                          .getVolumeControlBaseSizeOption()).append('\n');

            MeshPipelineController pipeline = sim.get(MeshPipelineController.class);
            pipeline.generateSurfaceMesh();
            pipeline.generateVolumeMesh();

            FvRepresentation volumeMesh = findVolumeMesh(sim);
            if (volumeMesh == null || volumeMesh.getCellCount() <= 0)
                throw new IllegalStateException("Volume mesh generation produced no cells");
            report.append("CELLS\t").append(volumeMesh.getCellCount()).append('\n');
            for (Region region : regions)
                report.append("REGION\t").append(region.getPresentationName())
                      .append("\tboundaries=").append(region.getBoundaryManager().getBoundaries().size())
                      .append('\n');

            sim.saveState(output.getAbsolutePath());
            report.append("COMPLETE_MARKER\t").append(COMPLETE).append('\n');
            try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "mrf_mesh_build.tsv")), StandardCharsets.UTF_8)) {
                w.write(report.toString());
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B MRF mesh build failed: " + e.getMessage(), e);
        }
    }

    /** The Volume Mesh representation, or null when the simulation has never been meshed. */
    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation representation : sim.getRepresentationManager().getObjects()) {
            // Any finite-volume representation is a volume mesh; the display name is only a
            // secondary check, and a geometry-only simulation carries none at all.
            if (representation instanceof FvRepresentation) return (FvRepresentation) representation;
        }
        return null;
    }

    private static SimpleCylinderPart cylinder(Simulation sim, double cx, double z0, double z1,
                                               double radius, String name) {
        MeshPartFactory factory = sim.get(MeshPartFactory.class);
        SimpleCylinderPart cyl = factory.createNewCylinderPart(sim.get(SimulationPartManager.class));
        cyl.setPresentationName(name);
        cyl.setDoNotRetessellate(true);
        LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
        Units metres = sim.getUnitsManager().getObject("m");
        cyl.setCoordinateSystem(lab);
        Coordinate start = cyl.getStartCoordinate();
        start.setCoordinateSystem(lab);
        start.setCoordinate(metres, metres, metres, new DoubleVector(new double[] { cx, 0.0, z0 }));
        Coordinate end = cyl.getEndCoordinate();
        end.setCoordinateSystem(lab);
        end.setCoordinate(metres, metres, metres, new DoubleVector(new double[] { cx, 0.0, z1 }));
        cyl.getRadius().setUnits(metres);
        cyl.getRadius().setValue(radius);
        cyl.getTessellationDensityOption().setSelected(TessellationDensityOption.Type.MEDIUM);
        cyl.rebuildSimpleShapePart();
        cyl.setDoNotRetessellate(false);
        return cyl;
    }
}
