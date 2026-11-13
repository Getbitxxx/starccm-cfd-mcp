/* Create two MRF regions from explicit CaseSpec fluid-part roles on a clean copy. */
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
import star.base.neo.NeoObjectVector;
import star.common.*;
import star.meshing.MeshOperationPart;

public class CreateMrfRegions extends StarMacro {
    private static final String COMPLETE = "MRF_REGIONS_CREATED";
    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String geometry = object(spec, "geometry");
            String roles = object(spec, "mrf_regions");
            // Patch identities are measured by the preceding read-only geometry audit.
            String map = read(new File(dir, "mrf_surface_map.json"));
            if (!"ok".equals(string(map, "status"))) throw new IllegalStateException("Surface-map audit did not succeed");
            RegionManager manager = sim.getRegionManager();
            if (!manager.getObjects().isEmpty() || !sim.getInterfaceManager().getObjects().isEmpty())
                throw new IllegalStateException("Isolated geometry entry must have zero regions/interfaces");
            MeshOperationPart stationaryPart = part(sim, string(geometry, "stationary_fluid_part"));
            MeshOperationPart rotatingPart = part(sim, string(geometry, "rotating_fluid_part"));
            if (stationaryPart == rotatingPart) throw new IllegalStateException("Fluid roles select the same part");
            Region stationary = create(manager, stationaryPart, string(roles, "stator_region"));
            Region rotating = create(manager, rotatingPart, string(roles, "rotor_region"));
            Boundary inlet = owned(stationary, stationaryPart, string(map, "outer_inlet"));
            Boundary outlet = owned(stationary, stationaryPart, string(map, "outer_outlet"));
            Boundary side = owned(stationary, stationaryPart, string(map, "outer_side"));
            if (inlet == outlet || inlet == side || outlet == side) throw new IllegalStateException("Outer roles are not distinct");
            inlet.setPresentationName(string(roles, "inlet_boundary"));
            inlet.setBoundaryType(InletBoundary.class);
            outlet.setPresentationName(string(roles, "outlet_boundary"));
            outlet.setBoundaryType(PressureBoundary.class);
            side.setPresentationName(string(roles, "farfield_boundary"));
            side.setBoundaryType(SymmetryBoundary.class);
            Set<Boundary> rotatingInterfaces = new HashSet<Boundary>();
            for (String key : new String[] {"inner_upstream", "inner_downstream", "inner_side"}) {
                owned(stationary, stationaryPart, string(map, key));
                rotatingInterfaces.add(owned(rotating, rotatingPart, string(map, key)));
            }
            if (rotatingInterfaces.size() != 3) throw new IllegalStateException("Interface roles are not distinct");
            int rotorWalls = 0;
            for (Boundary boundary : rotating.getBoundaryManager().getBoundaries()) {
                if (rotatingInterfaces.contains(boundary)) continue;
                if (!(boundary.getBoundaryType() instanceof WallBoundary)) throw new IllegalStateException("Rotor solid boundary is not a wall");
                rotorWalls++;
            }
            if (rotorWalls == 0 || manager.getObjects().size() != 2) throw new IllegalStateException("MRF region construction lacks rotor walls");
            sim.saveState(new File(dir, "mrf_regions_built.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_regions_build.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"ok\",\"stationary_region\":\"" + esc(stationary.getPresentationName()) +
                    "\",\"rotating_region\":\"" + esc(rotating.getPresentationName()) + "\",\"stationary_part\":\"" + esc(stationaryPart.getPresentationName()) +
                    "\",\"rotating_part\":\"" + esc(rotatingPart.getPresentationName()) + "\",\"stationary_boundaries\":" + stationary.getBoundaryManager().getBoundaries().size() +
                    ",\"rotating_boundaries\":" + rotating.getBoundaryManager().getBoundaries().size() + ",\"rotor_wall_boundaries\":" + rotorWalls +
                    ",\"inlet_boundary\":\"" + esc(inlet.getPresentationName()) + "\",\"outlet_boundary\":\"" + esc(outlet.getPresentationName()) +
                    "\",\"farfield_boundary\":\"" + esc(side.getPresentationName()) + "\",\"selection_basis\":\"CaseSpec fluid roles and measured PartSurface ownership\",\"interfaces\":0,\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) { throw new RuntimeException("MRF region creation failed", failure); }
    }
    private static Region create(RegionManager manager, MeshOperationPart part, String outputName) {
        Set<Region> before = new HashSet<Region>(manager.getObjects());
        manager.newRegionsFromParts(new NeoObjectVector(new Object[] {part}), "OneRegionPerPart", null,
            "OneBoundaryPerPartSurface", null, RegionManager.CreateInterfaceMode.BOUNDARY);
        List<Region> created = new ArrayList<Region>();
        for (Region candidate : manager.getObjects()) if (!before.contains(candidate)) created.add(candidate);
        if (created.size() != 1) throw new IllegalStateException("Expected one new region per role part");
        Region region = created.get(0);
        region.setPresentationName(outputName);
        Set<PartSurface> surfaces = new HashSet<PartSurface>();
        for (Boundary b : region.getBoundaryManager().getBoundaries()) {
            if (b.getPartSurfaceGroup().getObjects().size() != 1) throw new IllegalStateException("Expected one boundary per part surface");
            for (PartSurface surface : b.getPartSurfaceGroup().getObjects()) {
                if (!part.getPartSurfaceManager().getObjects().contains(surface) || !surfaces.add(surface))
                    throw new IllegalStateException("Boundary surface has wrong or duplicated ownership");
            }
        }
        if (surfaces.size() != part.getPartSurfaceManager().getObjects().size()) throw new IllegalStateException("Region omits a role part surface");
        return region;
    }
    private static MeshOperationPart part(Simulation sim, String name) {
        Object selected = sim.get(SimulationPartManager.class).getPart(name);
        if (!(selected instanceof MeshOperationPart)) throw new IllegalStateException("CaseSpec fluid role does not select a Boolean fluid part: " + name);
        return (MeshOperationPart) selected;
    }
    private static Boundary owned(Region region, MeshOperationPart part, String surfaceName) {
        PartSurface selected = part.getPartSurfaceManager().getPartSurface(surfaceName);
        if (selected == null) throw new IllegalStateException("Measured part surface missing: " + surfaceName);
        Boundary found = null;
        for (Boundary b : region.getBoundaryManager().getBoundaries()) if (b.getPartSurfaceGroup().getObjects().contains(selected)) {
            if (found != null) throw new IllegalStateException("Part surface belongs to more than one boundary");
            found = b;
        }
        if (found == null) throw new IllegalStateException("Part surface lacks a region boundary");
        return found;
    }
    private static String read(File file) throws Exception { return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8); }
    private static String object(String json, String key) { Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json); if (!m.find()) throw new IllegalArgumentException("Missing object " + key); return m.group(1); }
    private static String string(String json, String key) { Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json); if (!m.find()) throw new IllegalArgumentException("Missing string " + key); return m.group(1); }
    private static String esc(String s) { return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"); }
}

