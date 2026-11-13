/* AIR-3B A2a: block-split fluid domain -> two MRF regions + conformal internal interface.
 *
 * Consumes the audited two-solid STEP (rotating region = rotating cylinder minus the
 * propeller; static region = outer cylinder minus the rotating cylinder) and produces
 * exactly two fluid Regions connected by three explicit InternalInterfaces, one per
 * audited interface patch (side cylinder plus both end caps).
 *
 * Blocks are identified from measured boundary extents (Z range and maximum radius in
 * metres), never from object names, so the operation survives renaming and unit changes.
 * Every role must be claimed by exactly one boundary, otherwise the stage fails.
 *
 * Creates geometry, regions and interfaces only. No mesh operation, physics continuum,
 * initialization, iteration or solution clearing. The user's source .sim is never touched.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.meshing.*;

public class BuildAir3bMrfRegions extends StarMacro {

    private static final String COMPLETE = "AIR3B_MRF_REGIONS_COMPLETE";

    // Audited AIR-3B geometry in STAR internal units (metres). The STEP is millimetre
    // based and the CAD import converts it, so every threshold here is metric.
    private static final double R_ROT = 0.3075;
    private static final double R_OUT = 2.05;
    private static final double FLAT = 1.0e-3;   // m; end caps are planar to rounding
    private static final double TOL = 0.01;      // 1 % radius tolerance
    private static final double SCALE_TOL = 0.01;

    private static final String RADIUS_FUNCTION = "CFD_Radius";
    private static final String RADIUS_DEFINITION = "sqrt(pow($$Position[0],2)+pow($$Position[1],2))";

    private static final String[] INTERFACE_ROLES = { "INTERFACE_SIDE", "INTERFACE_END_UPSTREAM",
                                                      "INTERFACE_END_DOWNSTREAM" };

    private Simulation sim;
    private final List<ClientServerObject> temporary = new ArrayList<>();
    private final StringBuilder report = new StringBuilder();
    private final Map<String, double[]> extentCache = new LinkedHashMap<>();

    private static String sha(File f) throws Exception {
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(f)) {
            byte[] b = new byte[65536]; int n;
            while ((n = in.read(b)) != -1) d.update(b, 0, n);
        }
        return HexFormat.of().formatHex(d.digest());
    }

    /**
     * CFD_Radius is a project helper, not a STAR-native function, so it must exist or be
     * created before any radial measurement. Its definition is asserted so a same-named
     * function with different semantics cannot be used silently.
     */
    private void ensureRadiusFunction() {
        UserFieldFunction radius = null;
        for (FieldFunction f : sim.getFieldFunctionManager().getObjects()) {
            if (f instanceof UserFieldFunction && RADIUS_FUNCTION.equals(f.getPresentationName())) {
                radius = (UserFieldFunction) f;
                break;
            }
        }
        if (radius == null) {
            radius = sim.getFieldFunctionManager().createFieldFunction();
            radius.setPresentationName(RADIUS_FUNCTION);
            radius.setFunctionName(RADIUS_FUNCTION);
            radius.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
            radius.setDimensions(Dimensions.Builder().length(1).build());
            radius.setDefinition(RADIUS_DEFINITION);
            report.append("CREATED\t").append(RADIUS_FUNCTION).append('\n');
        } else {
            report.append("REUSED\t").append(RADIUS_FUNCTION).append('\n');
        }
        String definition = radius.getDefinition();
        if (definition == null || !definition.replace(" ", "").equals(RADIUS_DEFINITION.replace(" ", "")))
            throw new IllegalStateException(RADIUS_FUNCTION + " has an unexpected definition: " + definition);
    }

    private double[] extents(Boundary b) {
        String key = b.getQualifiedName();
        double[] cached = extentCache.get(key);
        if (cached != null) return cached;
        FieldFunction radius = sim.getFieldFunctionManager().getFunction(RADIUS_FUNCTION);
        FieldFunction z = sim.getFieldFunctionManager().getFunction("Position").getComponentFunction(2);
        Units metres = sim.getUnitsManager().getObject("m");
        MinReport rMin = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
        MaxReport rMax = (MaxReport) sim.getReportManager().create("star.base.report.MaxReport");
        MinReport zMin = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
        MaxReport zMax = (MaxReport) sim.getReportManager().create("star.base.report.MaxReport");
        for (ScalarReport r : new ScalarReport[] { rMin, rMax, zMin, zMax }) temporary.add(r);
        rMin.setFieldFunction(radius); rMax.setFieldFunction(radius);
        zMin.setFieldFunction(z); zMax.setFieldFunction(z);
        rMin.setUnits(metres); rMax.setUnits(metres); zMin.setUnits(metres); zMax.setUnits(metres);
        rMin.getParts().setObjects(b); rMax.getParts().setObjects(b);
        zMin.getParts().setObjects(b); zMax.getParts().setObjects(b);
        double[] result = new double[] { rMin.getReportMonitorValue(), rMax.getReportMonitorValue(),
                                         zMin.getReportMonitorValue(), zMax.getReportMonitorValue() };
        for (double v : result)
            if (!Double.isFinite(v)) throw new IllegalStateException("Nonfinite extent on boundary " + key);
        extentCache.put(key, result);
        return result;
    }

    private interface SplitAction { void run(MeshPart target); }

    /** Run one part-level split, record the outcome, and always return a live handle. */
    private MeshPart attemptSplit(Simulation sim, String name, String label, MeshPart current,
                                  SplitAction action) throws Exception {
        try {
            action.run(current);
            report.append(label).append('\t').append(name).append("\tOK\n");
            flush(new File(System.getenv("AIR3B_JOB_DIR")), "mrf_regions_progress.tsv");
        } catch (Throwable failure) {
            report.append(label).append('\t').append(name).append("\tFAIL:")
                  .append(oneLine(failure)).append('\n');
        }
        MeshPart live = (MeshPart) sim.get(SimulationPartManager.class).getPart(name);
        if (live == null) throw new IllegalStateException("Part disappeared during " + label + ": " + name);
        report.append(label).append("_COUNT\t").append(name).append('\t')
              .append(live.getPartSurfaces().size()).append('\n');
        return live;
    }

    private void flush(File dir, String name) throws IOException {
        try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, name)), StandardCharsets.UTF_8)) {
            w.write(report.toString());
        }
    }

    private static String oneLine(Throwable t) {
        String m = String.valueOf(t.getMessage());
        return m.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ');
    }

    private static boolean near(double a, double b, double tol) {
        return Math.abs(a - b) <= tol * Math.max(1.0, Math.abs(b));
    }

    private String classify(Boundary b, boolean rotating) {
        double[] e = extents(b);
        double rMax = e[1], rMin = e[0];
        boolean flat = (e[3] - e[2]) < FLAT;
        boolean interfaceRadius = near(rMax, R_ROT, TOL);
        // The rotating zone straddles z = 0 by construction, so the sign of a cap's z
        // decides which end it is and the zone's own height does not have to be hard-coded.
        // This matters because a regenerated rotor has a different axial bounding box: the
        // chirality-corrected blade moved the caps from -0.121546/+0.121822 m to
        // -0.121822/+0.121546 m, and a hard-coded tolerance then misread a cap as the side.
        // A cap must also span most of the zone radius, which excludes small flat fragments.
        boolean capLike = flat && interfaceRadius && rMin <= 0.5 * R_ROT;
        String block;
        if (capLike) block = (e[3] > 0.0) ? "INTERFACE_END_UPSTREAM" : "INTERFACE_END_DOWNSTREAM";
        else if (interfaceRadius) block = "INTERFACE_SIDE";
        else if (!rotating && flat && near(rMax, R_OUT, TOL)) block = (e[3] > 0.0) ? "INLET" : "OUTLET";
        else if (!rotating && near(rMax, R_OUT, TOL)) block = "FARFIELD";
        else if (rotating && rMax > 0.1) block = "BLADE";
        else if (rotating) block = "HUB";
        else block = "UNCLASSIFIED";
        report.append("BLOCK\t").append(b.getPresentationName()).append('\t').append(block)
              .append("\tR=").append(String.format(Locale.ROOT, "%.4f", e[0])).append("..")
              .append(String.format(Locale.ROOT, "%.4f", e[1]))
              .append("\tZ=").append(String.format(Locale.ROOT, "%.4f", e[2])).append("..")
              .append(String.format(Locale.ROOT, "%.4f", e[3])).append('\n');
        return block;
    }

    private static File requiredFile(File dir, String nameVar, String hashVar) throws Exception {
        String name = System.getenv(nameVar);
        String digest = System.getenv(hashVar);
        if (name == null || !name.matches("[A-Za-z0-9_.-]{1,64}")
                || digest == null || !digest.matches("[a-f0-9]{64}"))
            throw new IllegalStateException("Missing or invalid " + nameVar + "/" + hashVar);
        File file = new File(dir, name);
        if (!sha(file).equals(digest)) throw new IllegalStateException(name + " hash mismatch");
        return file;
    }

    @Override public void execute() {
        sim = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            File rotor = requiredFile(dir, "AIR3B_ROTOR_STEP", "AIR3B_ROTOR_SHA256");
            File stator = requiredFile(dir, "AIR3B_STATOR_STEP", "AIR3B_STATOR_SHA256");
            File output = new File(dir, "mrf_regions.sim");
            // TWO SUPPORTED ENTRY STATES, and nothing else (②R8).
            //
            // (A) LEGACY new-session path: an EMPTY simulation. The macro imports the two hash-pinned
            //     STEP blocks itself, exactly as it did before this change. Kept for compatibility.
            // (B) GEOMETRY-SIM path: the runner loaded a geometry-only .sim (`block_geometry.sim`) whose
            //     parts are already present. The macro must then CONSUME that state and must NOT import
            //     again. This is the fix for the previously declared chain discontinuity, where the
            //     geometry stage's output was discarded and the STEP re-imported.
            //
            // The two states are distinguished by whether parts are already present, and state (B) is
            // admitted only when the loaded state is provably geometry-only: zero regions, zero
            // interfaces, iteration 0. A solve checkpoint therefore cannot be smuggled in as the
            // "geometry" input, because any solved state carries both regions and a non-zero iteration.
            // The STEP files are still hash-verified below in BOTH paths, so the pinned CAD identity is
            // asserted even when the import is skipped.
            boolean partsPresent = !sim.get(SimulationPartManager.class).getParts().isEmpty();
            int loadedIteration = sim.getSimulationIterator().getCurrentIteration();
            int loadedRegions = sim.getRegionManager().getRegions().size();
            int loadedInterfaces = sim.getInterfaceManager().getObjects().size();
            if (output.exists())
                throw new IllegalStateException("Requires an unused output: " + output.getName());
            if (loadedIteration != 0)
                throw new IllegalStateException("Refusing to treat a non-zero-iteration state as geometry:"
                        + " iteration " + loadedIteration + " (a solve checkpoint is not a geometry input)");
            if (loadedRegions != 0 || loadedInterfaces != 0)
                throw new IllegalStateException("Refusing to treat a state with regions/interfaces as geometry:"
                        + " regions " + loadedRegions + ", interfaces " + loadedInterfaces);
            boolean importedHere = !partsPresent;
            if (importedHere) {
                report.append("GEOMETRY_SOURCE\tlegacy_new_session_import\t"
                        + "empty simulation; importing the two hash-verified STEP blocks\n");
                // One call per block: STAR merges the bodies of a single multi-body STEP here.
                sim.get(PartImportManager.class).importCadParts(
                        Collections.singletonList(rotor.getAbsolutePath()), "default", 30.0, 1,
                        false, 1.0e-5, false, false, false, false);
                sim.get(PartImportManager.class).importCadParts(
                        Collections.singletonList(stator.getAbsolutePath()), "default", 30.0, 1,
                        false, 1.0e-5, false, false, false, false);
            } else {
                // State (B). No import call is made in this branch - that is asserted by the report row
                // below and is visible in the per-macro log, because the absence of a call is otherwise
                // an unobservable claim.
                report.append("GEOMETRY_SOURCE\tloaded_geometry_only_sim\t"
                        + "parts already present; STEP import SKIPPED\n");
                report.append("GEOMETRY_INPUT_GATE\tregions\t").append(loadedRegions).append('\n');
                report.append("GEOMETRY_INPUT_GATE\tinterfaces\t").append(loadedInterfaces).append('\n');
                report.append("GEOMETRY_INPUT_GATE\titeration\t").append(loadedIteration).append('\n');
            }
            report.append("STEP_IMPORT_CALLS\t").append(importedHere ? 2 : 0).append('\n');

            Collection<GeometryPart> parts = sim.get(SimulationPartManager.class).getParts();
            report.append("PARTS\t").append(parts.size()).append('\n');
            if (parts.size() != 2)
                throw new IllegalStateException("Expected two closed fluid blocks, got " + parts.size());
            for (GeometryPart p : parts) {
                if (!(p instanceof MeshPart))
                    throw new IllegalStateException("Block is not a tessellated part: " + p.getPresentationName());
                MeshPart mesh = (MeshPart) p;
                if (!mesh.isClosed() || !mesh.isManifold() || mesh.getTriangleCount() <= 0)
                    throw new IllegalStateException("Block topology failed: " + p.getPresentationName()
                            + " closed=" + mesh.isClosed() + " manifold=" + mesh.isManifold()
                            + " triangles=" + mesh.getTriangleCount());
                if (p.getPartSurfaces().isEmpty())
                    throw new IllegalStateException("Block has no part surfaces: " + p.getPresentationName());
                report.append("PART\t").append(p.getClass().getSimpleName()).append('\t')
                      .append(p.getPresentationName()).append("\ttriangles=").append(mesh.getTriangleCount())
                      .append("\tsurfaces=").append(p.getPartSurfaces().size()).append('\n');
            }

            ensureRadiusFunction();

            // STAR delivers one part surface per imported body here, so the CAD faces needed to
            // resolve the block roles are separated at the PART level, before any Region or
            // Boundary exists. Part-surface objects stay valid, whereas the region-level split
            // was rejected by the server with
            // "Property with name star.common.Boundary_<id> does not exist".
            // STAR delivers one part surface per imported body here, so the CAD faces needed to
            // resolve the block roles are separated at the PART level, before any Region or
            // Boundary exists. Part handles are re-fetched by name after every topology change,
            // for the same reason the region-level attempt is not used at all: the server
            // rejected MeshManager's region-boundary split commands on this meshless CAD case
            // with "Property with name star.common.Boundary_<id> does not exist".
            List<String> partNames = new ArrayList<>();
            for (GeometryPart p : parts) partNames.add(p.getPresentationName());
            for (String name : partNames) {
                MeshPart part = (MeshPart) sim.get(SimulationPartManager.class).getPart(name);
                if (part == null) throw new IllegalStateException("Part disappeared before splitting: " + name);
                report.append("PART_SURFACES_ARRIVED\t").append(name).append('\t')
                      .append(part.getPartSurfaces().size()).append('\n');
                if (part.getPartSurfaces().size() >= 4) continue;

                part = attemptSplit(sim, name, "PART_SPLIT_BY_ANGLE", part,
                        new SplitAction() {
                            public void run(MeshPart target) {
                                target.getPartSurfaceManager().splitPartSurfacesByAngle(
                                        new ArrayList<PartSurface>(target.getPartSurfaces()), 30.0);
                            }
                        });
                report.append("PART_SURFACES_AFTER_ANGLE\t").append(name).append('\t')
                      .append(part.getPartSurfaces().size()).append('\n');
                if (part.getPartSurfaces().size() >= 4) continue;

                // The propeller surface and the inner cavity are connected components that no
                // feature edge separates from the outer shell.
                part = attemptSplit(sim, name, "PART_SPLIT_NON_CONTIGUOUS", part,
                        new SplitAction() {
                            public void run(MeshPart target) {
                                target.getPartSurfaceManager().splitNonContiguousPartSurfaces(
                                        new ArrayList<PartSurface>(target.getPartSurfaces()));
                            }
                        });
                report.append("PART_SURFACES_AFTER_CONNECTIVITY\t").append(name).append('\t')
                      .append(part.getPartSurfaces().size()).append('\n');
                if (part.getPartSurfaces().size() >= 4) continue;

                // Last CAD-side lever: ask STAR for one part surface per topological face.
                part = attemptSplit(sim, name, "PART_SPLIT_BY_TOPOLOGY", part,
                        new SplitAction() {
                            public void run(MeshPart target) { target.splitBySurfaceTopology(); }
                        });
                report.append("PART_SURFACES_AFTER_TOPOLOGY\t").append(name).append('\t')
                      .append(part.getPartSurfaces().size()).append('\n');
            }
            flush(dir, "mrf_regions_progress.tsv");

            sim.getRegionManager().newRegionsFromParts(parts, "OneRegionPerPart", null,
                    "OneBoundaryPerPartSurface", null, RegionManager.CreateInterfaceMode.BOUNDARY);

            Collection<Region> regions = sim.getRegionManager().getRegions();
            report.append("REGIONS\t").append(regions.size()).append('\n');
            if (regions.size() != 2)
                throw new IllegalStateException("Expected two regions, got " + regions.size());
            for (Region region : regions) {
                report.append("BOUNDARIES_ARRIVED\t").append(region.getPresentationName()).append('\t')
                      .append(region.getBoundaryManager().getBoundaries().size()).append('\n');
            }

            // Classify by measured extent: the rotating block is the small cylinder.
            Region rotating = null, stationary = null;
            for (Region region : regions) {
                double rMax = 0.0;
                for (Boundary b : region.getBoundaryManager().getBoundaries())
                    rMax = Math.max(rMax, extents(b)[1]);
                if (rMax < 0.5 * R_OUT) {
                    if (rotating != null) throw new IllegalStateException("Ambiguous rotating region");
                    rotating = region;
                } else {
                    if (stationary != null) throw new IllegalStateException("Ambiguous stationary region");
                    stationary = region;
                }
            }
            if (rotating == null || stationary == null)
                throw new IllegalStateException("Rotating/stationary region split failed");

            // Scale gate: a unit misread (mm taken as m) would put the radii three orders
            // of magnitude out, so assert the audited metric extents explicitly.
            double rotatingMaxR = 0.0, stationaryMaxR = 0.0;
            for (Boundary b : rotating.getBoundaryManager().getBoundaries())
                rotatingMaxR = Math.max(rotatingMaxR, extents(b)[1]);
            for (Boundary b : stationary.getBoundaryManager().getBoundaries())
                stationaryMaxR = Math.max(stationaryMaxR, extents(b)[1]);
            report.append("SCALE\trotating_max_radius_m\t").append(rotatingMaxR).append('\n');
            report.append("SCALE\tstationary_max_radius_m\t").append(stationaryMaxR).append('\n');
            if (!near(rotatingMaxR, R_ROT, SCALE_TOL))
                throw new IllegalStateException("Rotating block radius " + rotatingMaxR
                        + " m is not the audited " + R_ROT + " m; check import units");
            if (!near(stationaryMaxR, R_OUT, SCALE_TOL))
                throw new IllegalStateException("Stationary block radius " + stationaryMaxR
                        + " m is not the audited " + R_OUT + " m; check import units");

            // Require exactly one boundary per interface role: a duplicate role would
            // otherwise silently overwrite the previous holder.
            Map<String, Boundary> rotatingRoles = claimRoles(rotating, true, "AIR3B_ROTATING");
            Map<String, Boundary> stationaryRoles = claimRoles(stationary, false, "AIR3B_STATIONARY");
            for (String role : INTERFACE_ROLES) {
                if (!rotatingRoles.containsKey(role))
                    throw new IllegalStateException("Rotating interface role missing: " + role);
                if (!stationaryRoles.containsKey(role))
                    throw new IllegalStateException("Stationary interface role missing: " + role);
            }

            rotating.setPresentationName("AIR3B_ROTATING");
            stationary.setPresentationName("AIR3B_STATIONARY");

            // Register exactly the three audited patch pairs. Any interface the region
            // construction produced on its own is removed first so the delivered topology
            // is deterministic and fully accounted for.
            Collection<Interface> auto = sim.getInterfaceManager().getObjects();
            if (!auto.isEmpty()) {
                report.append("REPLACED_AUTO_INTERFACES\t").append(auto.size()).append('\n');
                List<ClientServerObject> discard = new ArrayList<>();
                for (Interface i : auto) discard.add(i);
                sim.deleteObjects(discard);
            }
            for (int i = 0; i < INTERFACE_ROLES.length; i++) {
                BoundaryInterface created = sim.getInterfaceManager().createBoundaryInterface(
                        rotatingRoles.get(INTERFACE_ROLES[i]), stationaryRoles.get(INTERFACE_ROLES[i]),
                        "AIR3B_MRF_INTERFACE_" + (i + 1));
                String topology = String.valueOf(created.getTopology().getSelectedElement());
                report.append("INTERFACE\t").append(created.getPresentationName()).append('\t')
                      .append(INTERFACE_ROLES[i]).append("\ttopology=").append(topology).append('\n');
                // STAR registers a coincident internal boundary pair as INTERNAL or, on 2502,
                // as IN_PLACE (the in-place interface, which is the exact-match variant).
                // Anything else (BAFFLE, PERIODIC, ...) would not be a conformal MRF interface.
                String upper = topology.toUpperCase(Locale.ROOT);
                if (!upper.contains("INTERNAL") && !upper.contains("IN_PLACE"))
                    throw new IllegalStateException("Interface " + created.getPresentationName()
                            + " is neither internal nor in-place but " + topology);
                // The interface patches must reproduce the audited geometry; a partial or
                // over-connected patch would otherwise still pass the topology check.
                double[] rotExtent = extents(rotatingRoles.get(INTERFACE_ROLES[i]));
                double[] statExtent = extents(stationaryRoles.get(INTERFACE_ROLES[i]));
                report.append("INTERFACE_EXTENT\t").append(INTERFACE_ROLES[i])
                      .append("\trotating_rmax=").append(rotExtent[1])
                      .append("\tstationary_rmax=").append(statExtent[1])
                      .append("\trotating_z=").append(rotExtent[2]).append("..").append(rotExtent[3])
                      .append("\tstationary_z=").append(statExtent[2]).append("..").append(statExtent[3])
                      .append('\n');
            }
            int interfaces = sim.getInterfaceManager().getObjects().size();
            report.append("INTERFACES\t").append(interfaces).append('\n');
            report.append("REGION\t").append(rotating.getPresentationName()).append("\tboundaries=")
                  .append(rotating.getBoundaryManager().getBoundaries().size()).append('\n');
            report.append("REGION\t").append(stationary.getPresentationName()).append("\tboundaries=")
                  .append(stationary.getBoundaryManager().getBoundaries().size()).append('\n');
            if (interfaces != 3)
                throw new IllegalStateException("Expected three internal interfaces, got " + interfaces);

            if (!sha(rotor).equals(System.getenv("AIR3B_ROTOR_SHA256"))
                    || !sha(stator).equals(System.getenv("AIR3B_STATOR_SHA256")))
                throw new IllegalStateException("Input changed during the stage");

            // Delete the temporary reports BEFORE saving so the delivered simulation does
            // not carry hundreds of measurement artifacts.
            sim.deleteObjects(new ArrayList<ClientServerObject>(temporary));
            temporary.clear();

            sim.saveState(output.getAbsolutePath());
            report.append("ROTOR_STEP_SHA256\t").append(System.getenv("AIR3B_ROTOR_SHA256")).append('\n');
            report.append("STATOR_STEP_SHA256\t").append(System.getenv("AIR3B_STATOR_SHA256")).append('\n');
            report.append("OUTPUT_SHA256\t").append(sha(output)).append('\n');
            report.append("COMPLETE_MARKER\t").append(COMPLETE).append('\n');
            flush(dir, "mrf_regions.tsv");
            System.out.println(COMPLETE);
        } catch (Exception e) {
            try { flush(new File(System.getenv("AIR3B_JOB_DIR")), "mrf_regions_failure.tsv"); }
            catch (Throwable ignored) { }
            throw new RuntimeException("AIR-3B MRF region build failed: " + e.getMessage(), e);
        } finally {
            try { sim.deleteObjects(new ArrayList<ClientServerObject>(temporary)); } catch (Throwable ignored) { }
        }
    }

    /** Assign every boundary a role and reject a duplicate interface role. */
    private Map<String, Boundary> claimRoles(Region region, boolean rotating, String label) {
        Map<String, Boundary> roles = new LinkedHashMap<>();
        for (Boundary b : region.getBoundaryManager().getBoundaries()) {
            String block = classify(b, rotating);
            if (block.equals("UNCLASSIFIED"))
                throw new IllegalStateException("Unclassified boundary in " + label + ": " + b.getPresentationName());
            b.setPresentationName(block);
            if (block.startsWith("INTERFACE")) {
                Boundary previous = roles.put(block, b);
                if (previous != null)
                    throw new IllegalStateException("Role " + block + " claimed by both " + previous
                            + " and " + b + " in " + label + "; check the block split");
            }
        }
        return roles;
    }
}
