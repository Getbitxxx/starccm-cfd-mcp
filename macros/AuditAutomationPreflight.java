/*
 * AuditAutomationPreflight.java
 *
 * Read-only preflight audit for an open Simcenter STAR-CCM+ session (2502, Java API 21).
 * Writes <sessionDir>/automation_preflight.json and then prints, exactly once,
 * the marker line AUTOMATION_PREFLIGHT_COMPLETE.
 *
 * Batch:
 *   starccm+.bat <case-copy.sim> -batch <abs path>\AuditAutomationPreflight.java
 * GUI:
 *   open the .sim, then Play Macro and pick this file.
 *
 * Read-only simulation guarantee: the macro reads the object tree, exports temporary binary
 * STL files to measure CAD bounds, deletes them, and writes one JSON report. It never meshes,
 * initializes, iterates, clears the solution, saves the
 * .sim, edits any model object, or touches credentials, MCP state, or service files.
 *
 * API used (checked against the STAR-CCM+ 2502 API jars and the shipped tutorial macros):
 *   star.common.RootObject   getPresentationName / getProcessFileName / getSessionDirFile /
 *                            getReleaseNumber / isModified / isReadOnlyFile / isParallel /
 *                            get(Class) -- the typed child-manager lookup
 *   star.common.Simulation   getSimulationIterator().getCurrentIteration() / getSolution() /
 *                            getRegionManager / getContinuumManager / getInterfaceManager /
 *                            getReportManager / getPartManager
 *   star.common.SimulationPartManager
 *                            Simulation.get(SimulationPartManager.class): the simulation geometry
 *                            parts, i.e. the imported CAD (star.meshing.CadPart). getPartManager()
 *                            does NOT return them -- it returns derived display parts only.
 *   star.common.GeometryPart getPresentationName / getQualifiedName / isLeaf / isShell / getPartSurfaces()
 *   star.common.PartSurface  getPresentationName / getQualifiedName
 *   star.common.Region       getBoundaryManager().getBoundaries(), getRegionType(), getPhysicsContinuum()
 *   star.common.Boundary     getBoundaryType(), getPartSurfaceGroup().getObjects()
 *   star.common.PhysicsContinuum  getIsActive(), getModelManager().getObjects()
 *   star.common.Interface    getInterfaceType(), getRegion0(), getRegion1()
 *   star.base.report.Report  reports live under star.base.report, not star.common
 *
 * Every read goes through a guard: an unreadable field becomes a warning entry instead of an
 * exception, so a partially readable session still yields a valid report.
 */

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

import star.base.neo.ClientServerObject;
import star.base.report.Report;
import star.common.Boundary;
import star.common.BoundaryType;
import star.common.Continuum;
import star.common.GeometryPart;
import star.common.HubInterface;
import star.common.Interface;
import star.common.Model;
import star.common.Part;
import star.common.PartSurface;
import star.common.PhysicsContinuum;
import star.common.Region;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.TwoSidedInterface;
import star.meshing.MeshPart;

public class AuditAutomationPreflight extends StarMacro {

    private static final String OUTPUT_FILE_NAME = "automation_preflight.json";
    private static final String COMPLETE_MARKER = "AUTOMATION_PREFLIGHT_COMPLETE";
    private static final int SCHEMA_VERSION = 1;
    private static final int MAX_WARNINGS = 200;

    /**
     * Set to false to skip the boundary-to-part-surface groups, the only field that resolves a
     * boundary part-surface group query. Geometry-part surfaces are always listed.
     */
    private static final boolean COLLECT_PART_SURFACES = true;

    private static final String OK = "ok";
    private static final String PARTIAL = "partial";
    private static final String ERROR = "error";

    private final List<Object> warnings = new ArrayList<Object>();
    private boolean warningsTruncated = false;
    private String status = OK;
    private String fatalError = null;

    @Override
    public void execute() {
        Map<String, Object> report = new LinkedHashMap<String, Object>();
        report.put("schemaVersion", Integer.valueOf(SCHEMA_VERSION));
        report.put("generatedBy", "AuditAutomationPreflight.java");
        report.put("status", OK);
        report.put("generatedAtUtc", utcNow());
        report.put("outputFile", null);

        File sessionDir = null;
        Simulation simulation = null;
        try {
            simulation = getActiveSimulation();
        } catch (Throwable t) {
            fatal("simulation", "getActiveSimulation() failed: " + describe(t));
        }

        if (simulation == null) {
            fatal("simulation", "no active simulation; open a .sim or pass the .sim as the first "
                    + "argument to starccm+ -batch");
        } else {
            sessionDir = sessionDirOf(simulation);
            collectSimulation(report, simulation);
        }

        File output = (sessionDir == null) ? null : new File(sessionDir, OUTPUT_FILE_NAME);
        if (output != null) {
            report.put("outputFile", output.getAbsolutePath());
        }
        report.put("warnings", warnings);
        report.put("warningsTruncated", Boolean.valueOf(warningsTruncated));
        if (fatalError != null) {
            report.put("fatalError", fatalError);
        }
        report.put("status", status);

        boolean written = false;
        if (output == null) {
            fatal("output", "session directory is unavailable; " + OUTPUT_FILE_NAME + " was not written");
        } else {
            try {
                writeJsonFile(output, report);
                written = true;
            } catch (Throwable t) {
                fatal("output", "writing " + output.getAbsolutePath() + " failed: " + describe(t));
            }
        }

        if (fatalError != null) {
            System.out.println("AUTOMATION_PREFLIGHT_ERROR " + fatalError);
        }
        System.out.println("AUTOMATION_PREFLIGHT_STATUS " + status);
        if (output != null) {
            System.out.println("AUTOMATION_PREFLIGHT_OUTPUT " + output.getAbsolutePath()
                    + (written ? "" : " (write failed)"));
        }
        System.out.println(COMPLETE_MARKER);
    }

    private File sessionDirOf(final Simulation simulation) {
        try {
            File dir = simulation.getSessionDirFile();
            if (dir == null) {
                addWarning("SECTION_UNREADABLE", "sessionDir", "getSessionDirFile() returned null");
            }
            return dir;
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "sessionDir", "getSessionDirFile() failed: " + describe(t));
            return null;
        }
    }

    private void collectSimulation(final Map<String, Object> report, final Simulation sim) {
        report.put("starRelease", read("starRelease", new Value() {
            public Object get() { return sim.getReleaseNumber(); }
        }));
        report.put("javaVersion", System.getProperty("java.version"));
        report.put("simulationName", read("simulationName", new Value() {
            public Object get() { return sim.getPresentationName(); }
        }));
        report.put("processFileName", read("processFileName", new Value() {
            public Object get() { return sim.getProcessFileName(); }
        }));
        report.put("sessionDir", read("sessionDir", new Value() {
            public Object get() {
                File dir = sim.getSessionDirFile();
                return (dir == null) ? null : dir.getAbsolutePath();
            }
        }));
        report.put("iteration", read("iteration", new Value() {
            public Object get() { return Integer.valueOf(sim.getSimulationIterator().getCurrentIteration()); }
        }));
        report.put("physicalTime", read("physicalTime", new Value() {
            public Object get() { return Double.valueOf(sim.getSolution().getPhysicalTime()); }
        }));
        report.put("solutionInitialized", read("solutionInitialized", new Value() {
            public Object get() { return Boolean.valueOf(sim.isInitialized()); }
        }));
        report.put("sessionModified", read("sessionModified", new Value() {
            public Object get() { return Boolean.valueOf(sim.isModified()); }
        }));
        report.put("processFileReadOnly", read("processFileReadOnly", new Value() {
            public Object get() { return Boolean.valueOf(sim.isReadOnlyFile()); }
        }));
        report.put("parallel", read("parallel", new Value() {
            public Object get() { return Boolean.valueOf(sim.isParallel()); }
        }));
        report.put("numberOfWorkers", read("numberOfWorkers", new Value() {
            public Object get() { return Integer.valueOf(sim.getNumberOfWorkers()); }
        }));

        List<Object> regions = collectRegions(sim);
        List<Object> continua = collectPhysicsContinua(sim);
        List<Object> geometryParts = collectGeometryParts(sim);
        List<Object> derivedParts = collectDerivedParts(sim);
        List<Object> interfaces = collectInterfaces(sim);
        List<Object> reports = collectReports(sim);

        report.put("counts", countsOf(regions, continua, geometryParts, derivedParts, interfaces, reports));
        report.put("regions", regions);
        report.put("physicsContinua", continua);
        report.put("geometryParts", geometryParts);
        report.put("derivedParts", derivedParts);
        report.put("interfaces", interfaces);
        report.put("reports", reports);
    }

    private List<Object> collectRegions(final Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        Collection<Region> regions;
        try {
            regions = sim.getRegionManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "regions", "getRegionManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (regions == null) {
            addWarning("SECTION_UNREADABLE", "regions", "the region manager returned no collection");
            return out;
        }

        int index = 0;
        for (Region region : regions) {
            if (region == null) {
                addWarning("SKIPPED", "regions[" + index + "]", "null entry in the region manager");
                index++;
                continue;
            }
            final Region current = region;
            final String area = "regions[" + index + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(current, area + ".name"));
            entry.put("class", className(current));
            entry.put("regionType", read(area + ".regionType", new Value() {
                public Object get() { return current.getRegionType().getPresentationName(); }
            }));
            entry.put("regionTypeClass", read(area + ".regionTypeClass", new Value() {
                public Object get() { return current.getRegionType().getClass().getName(); }
            }));
            entry.put("physicsContinuum", read(area + ".physicsContinuum", new Value() {
                public Object get() {
                    PhysicsContinuum physics = current.getPhysicsContinuum();
                    return (physics == null) ? null : physics.getPresentationName();
                }
            }));
            entry.put("boundaries", collectBoundaries(current, area));
            out.add(entry);
            index++;
        }
        return out;
    }

    private List<Object> collectBoundaries(Region region, String regionArea) {
        List<Object> out = new ArrayList<Object>();
        Collection<Boundary> boundaries;
        try {
            boundaries = region.getBoundaryManager().getBoundaries();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", regionArea + ".boundaries",
                    "getBoundaryManager().getBoundaries() failed: " + describe(t));
            return out;
        }
        if (boundaries == null) {
            addWarning("SECTION_UNREADABLE", regionArea + ".boundaries", "the boundary manager returned no collection");
            return out;
        }

        int index = 0;
        for (Boundary boundary : boundaries) {
            if (boundary == null) {
                addWarning("SKIPPED", regionArea + ".boundaries[" + index + "]", "null entry in the boundary manager");
                index++;
                continue;
            }
            final Boundary current = boundary;
            final String area = regionArea + ".boundaries[" + index + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(current, area + ".name"));
            entry.put("class", className(current));
            final BoundaryType type = (BoundaryType) read(area + ".boundaryType", new Value() {
                public Object get() { return current.getBoundaryType(); }
            });
            entry.put("boundaryType", (type == null) ? null : nameOf(type, area + ".boundaryType"));
            entry.put("boundaryTypeClass", className(type));
            entry.put("boundaryTypeClassSimpleName", simpleClassName(type));
            entry.put("partSurfaces", COLLECT_PART_SURFACES ? collectPartSurfaces(current, area) : null);
            entry.put("partSurfaceOwners", COLLECT_PART_SURFACES ? collectPartSurfaceOwners(current, area) : null);
            out.add(entry);
            index++;
        }
        return out;
    }

    private List<Object> collectPartSurfaces(Boundary boundary, String boundaryArea) {
        List<Object> out = new ArrayList<Object>();
        Collection<PartSurface> surfaces;
        try {
            surfaces = boundary.getPartSurfaceGroup().getObjects();
        } catch (Throwable t) {
            addWarning("FIELD_UNREADABLE", boundaryArea + ".partSurfaces",
                    "getPartSurfaceGroup().getObjects() failed: " + describe(t));
            return null;
        }
        if (surfaces == null) {
            addWarning("FIELD_UNREADABLE", boundaryArea + ".partSurfaces",
                    "the part surface group returned no collection");
            return null;
        }
        for (PartSurface surface : surfaces) {
            if (surface == null) {
                continue;
            }
            out.add(nameOf(surface, boundaryArea + ".partSurfaces"));
        }
        return out;
    }

    private List<Object> collectPartSurfaceOwners(Boundary boundary, String boundaryArea) {
        List<Object> out = new ArrayList<Object>();
        Collection<PartSurface> surfaces;
        try {
            surfaces = boundary.getPartSurfaceGroup().getObjects();
        } catch (Throwable t) {
            addWarning("FIELD_UNREADABLE", boundaryArea + ".partSurfaceOwners",
                    "getPartSurfaceGroup().getObjects() failed: " + describe(t));
            return null;
        }
        if (surfaces == null) {
            return null;
        }
        for (PartSurface surface : surfaces) {
            if (surface == null) {
                continue;
            }
            final PartSurface current = surface;
            out.add(read(boundaryArea + ".partSurfaceOwners", new Value() {
                public Object get() {
                    GeometryPart owner = current.getPart();
                    return owner == null ? null : owner.getQualifiedName();
                }
            }));
        }
        return out;
    }

    private List<Object> collectPhysicsContinua(Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        Collection<Continuum> continua;
        try {
            continua = sim.getContinuumManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "physicsContinua",
                    "getContinuumManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (continua == null) {
            addWarning("SECTION_UNREADABLE", "physicsContinua", "the continuum manager returned no collection");
            return out;
        }

        int index = 0;
        for (Continuum continuum : continua) {
            if (continuum == null) {
                addWarning("SKIPPED", "physicsContinua[" + index + "]", "null entry in the continuum manager");
                index++;
                continue;
            }
            if (!(continuum instanceof PhysicsContinuum)) {
                addWarning("SKIPPED", "physicsContinua[" + index + "]", "continuum '" + safeName(continuum)
                        + "' is a " + className(continuum) + ", not a PhysicsContinuum");
                index++;
                continue;
            }
            final PhysicsContinuum physics = (PhysicsContinuum) continuum;
            final String area = "physicsContinua[" + index + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(physics, area + ".name"));
            entry.put("class", className(physics));
            entry.put("isActive", read(area + ".isActive", new Value() {
                public Object get() { return Boolean.valueOf(physics.getIsActive()); }
            }));
            entry.put("isMotionActive", read(area + ".isMotionActive", new Value() {
                public Object get() { return Boolean.valueOf(physics.getIsMotionActive()); }
            }));
            List<Object> models = collectModels(physics, area);
            List<Object> modelNames = new ArrayList<Object>();
            for (Object model : models) {
                Object name = mapValue(model, "name");
                if (name != null) {
                    modelNames.add(name);
                }
            }
            entry.put("models", models);
            entry.put("enabledModelNames", modelNames);
            out.add(entry);
            index++;
        }
        return out;
    }

    private List<Object> collectModels(PhysicsContinuum physics, String continuumArea) {
        List<Object> out = new ArrayList<Object>();
        Collection<Model> models;
        try {
            models = physics.getModelManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", continuumArea + ".models",
                    "getModelManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (models == null) {
            addWarning("SECTION_UNREADABLE", continuumArea + ".models", "the model manager returned no collection");
            return out;
        }

        int index = 0;
        for (Model model : models) {
            if (model == null) {
                addWarning("SKIPPED", continuumArea + ".models[" + index + "]", "null entry in the model manager");
                index++;
                continue;
            }
            String simple = simpleClassName(model);
            if (simple != null && (simple.endsWith("Group") || simple.endsWith("Manager"))) {
                addWarning("FILTERED", continuumArea + ".models[" + index + "]", "container-like entry '"
                        + safeName(model) + "' (" + className(model) + ") excluded from enabledModelNames");
                index++;
                continue;
            }
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(model, continuumArea + ".models[" + index + "].name"));
            entry.put("class", className(model));
            out.add(entry);
            index++;
        }
        return out;
    }

    /**
     * Collects the simulation geometry parts: the imported CAD under the simulation's
     * SimulationPartManager (for a fluid.x_t + propeller.x_t import, the CadPart objects named
     * FLUID and PROPELLER).
     *
     * <p>These are not reachable through {@code Simulation.getPartManager()}, which holds derived
     * display parts only, so a session with imported CAD can report
     * {@code counts.derivedParts == 0} beside {@code counts.geometryParts == 2}.
     *
     * @param sim the active simulation
     * @return one entry per geometry part, in manager order
     */
    private List<Object> collectGeometryParts(final Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        SimulationPartManager manager;
        try {
            manager = sim.get(SimulationPartManager.class);
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "geometryParts",
                    "get(SimulationPartManager.class) failed: " + describe(t));
            return out;
        }
        if (manager == null) {
            addWarning("SECTION_UNREADABLE", "geometryParts",
                    "get(SimulationPartManager.class) returned null");
            return out;
        }
        Collection<GeometryPart> parts;
        try {
            parts = manager.getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "geometryParts",
                    "getObjects() on the simulation part manager failed: " + describe(t));
            return out;
        }
        if (parts == null) {
            addWarning("SECTION_UNREADABLE", "geometryParts",
                    "the simulation part manager returned no collection");
            return out;
        }

        int index = 0;
        for (GeometryPart part : parts) {
            if (part == null) {
                addWarning("SKIPPED", "geometryParts[" + index + "]",
                        "null entry in the simulation part manager");
                index++;
                continue;
            }
            final GeometryPart current = part;
            final String area = "geometryParts[" + index + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(current, area + ".name"));
            entry.put("class", className(current));
            entry.put("qualifiedName", read(area + ".qualifiedName", new Value() {
                public Object get() { return current.getQualifiedName(); }
            }));
            entry.put("isLeaf", read(area + ".isLeaf", new Value() {
                public Object get() { return Boolean.valueOf(current.isLeaf()); }
            }));
            entry.put("isShell", read(area + ".isShell", new Value() {
                public Object get() { return Boolean.valueOf(current.isShell()); }
            }));
            if (current instanceof MeshPart) {
                final MeshPart meshPart = (MeshPart) current;
                entry.put("isClosed", read(area + ".isClosed", new Value() {
                    public Object get() { return Boolean.valueOf(meshPart.isClosed()); }
                }));
                entry.put("isManifold", read(area + ".isManifold", new Value() {
                    public Object get() { return Boolean.valueOf(meshPart.isManifold()); }
                }));
                entry.put("triangleCount", read(area + ".triangleCount", new Value() {
                    public Object get() { return Integer.valueOf(meshPart.getTriangleCount()); }
                }));
                entry.put("boundingBoxRaw", collectBoundingBox(meshPart, sim, area));
                entry.put("boundingBoxSource", "binary_stl_export");
            } else {
                entry.put("isClosed", null);
                entry.put("isManifold", null);
                entry.put("triangleCount", null);
                entry.put("boundingBoxRaw", null);
                entry.put("boundingBoxSource", null);
            }
            List<Object> surfaces = collectGeometryPartSurfaces(current, area);
            entry.put("partSurfaceCount", (surfaces == null) ? null : Integer.valueOf(surfaces.size()));
            entry.put("partSurfaces", surfaces);
            out.add(entry);
            index++;
        }
        return out;
    }

    /** Export a temporary triangle stream and measure it without altering the simulation. */
    private List<Object> collectBoundingBox(final MeshPart part, Simulation sim, String partArea) {
        File temporary = null;
        try {
            temporary = File.createTempFile("part_bounds_", ".stl", sim.getSessionDirFile());
            part.exportStlPart(temporary.getAbsolutePath(), true);
            try (FileInputStream stream = new FileInputStream(temporary)) {
                byte[] header = new byte[84];
                readFully(stream, header);
                long count = Integer.toUnsignedLong(ByteBuffer.wrap(header, 80, 4)
                        .order(ByteOrder.LITTLE_ENDIAN).getInt());
                if (count == 0 || temporary.length() != 84L + count * 50L) {
                    throw new IOException("Invalid or empty binary STL triangle stream");
                }
                double[] bounds = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                        Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY,
                        Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
                byte[] triangle = new byte[50];
                for (long i = 0; i < count; i++) {
                    readFully(stream, triangle);
                    ByteBuffer row = ByteBuffer.wrap(triangle).order(ByteOrder.LITTLE_ENDIAN);
                    for (int vertex = 0; vertex < 3; vertex++) {
                        for (int axis = 0; axis < 3; axis++) {
                            double value = row.getFloat(12 + vertex * 12 + axis * 4);
                            if (!Double.isFinite(value)) {
                                throw new IOException("Non-finite STL vertex");
                            }
                            bounds[axis] = Math.min(bounds[axis], value);
                            bounds[axis + 3] = Math.max(bounds[axis + 3], value);
                        }
                    }
                }
                List<Object> out = new ArrayList<Object>();
                for (double value : bounds) {
                    out.add(Double.valueOf(value));
                }
                return out;
            }
        } catch (Throwable t) {
            addWarning("FIELD_UNREADABLE", partArea + ".boundingBoxRaw", describe(t));
            return null;
        } finally {
            if (temporary != null && temporary.exists() && !temporary.delete()) {
                addWarning("TEMP_FILE_LEFT", partArea + ".boundingBoxRaw",
                        "Could not remove temporary STL file: " + temporary.getName());
            }
        }
    }

    private static void readFully(FileInputStream stream, byte[] target) throws IOException {
        int offset = 0;
        while (offset < target.length) {
            int read = stream.read(target, offset, target.length - offset);
            if (read < 0) {
                throw new IOException("Truncated binary STL triangle stream");
            }
            offset += read;
        }
    }

    /**
     * Lists the part surfaces of one geometry part. A failed read yields null, so an unreadable
     * surface list stays distinguishable from a geometry part that genuinely has none.
     *
     * @param part the geometry part whose surfaces are listed
     * @param partArea JSON path of the owning geometry part, used in warnings
     * @return one entry per part surface, or null when the list could not be read
     */
    private List<Object> collectGeometryPartSurfaces(GeometryPart part, String partArea) {
        List<Object> out = new ArrayList<Object>();
        Collection<PartSurface> surfaces;
        try {
            surfaces = part.getPartSurfaces();
        } catch (Throwable t) {
            addWarning("FIELD_UNREADABLE", partArea + ".partSurfaces",
                    "getPartSurfaces() failed: " + describe(t));
            return null;
        }
        if (surfaces == null) {
            addWarning("FIELD_UNREADABLE", partArea + ".partSurfaces",
                    "the geometry part returned no surface collection");
            return null;
        }
        for (PartSurface surface : surfaces) {
            if (surface == null) {
                continue;
            }
            final PartSurface current = surface;
            final String area = partArea + ".partSurfaces[" + out.size() + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(current, area + ".name"));
            entry.put("qualifiedName", read(area + ".qualifiedName", new Value() {
                public Object get() { return current.getQualifiedName(); }
            }));
            out.add(entry);
        }
        return out;
    }

    /**
     * Collects derived display parts. These are visualization objects built on top of regions or
     * geometry parts; they are not CAD, and an unmeshed CAD import has none.
     *
     * @param sim the active simulation
     * @return one entry per derived part, in manager order
     */
    private List<Object> collectDerivedParts(Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        Collection<Part> parts;
        try {
            parts = sim.getPartManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "derivedParts", "getPartManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (parts == null) {
            addWarning("SECTION_UNREADABLE", "derivedParts", "the part manager returned no collection");
            return out;
        }

        int index = 0;
        for (Part part : parts) {
            if (part == null) {
                addWarning("SKIPPED", "derivedParts[" + index + "]", "null entry in the part manager");
                index++;
                continue;
            }
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(part, "derivedParts[" + index + "].name"));
            entry.put("class", className(part));
            out.add(entry);
            index++;
        }
        return out;
    }

    private List<Object> collectInterfaces(Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        Collection<Interface> interfaces;
        try {
            interfaces = sim.getInterfaceManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "interfaces", "getInterfaceManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (interfaces == null) {
            addWarning("SECTION_UNREADABLE", "interfaces", "the interface manager returned no collection");
            return out;
        }

        int index = 0;
        for (Interface item : interfaces) {
            if (item == null) {
                addWarning("SKIPPED", "interfaces[" + index + "]", "null entry in the interface manager");
                index++;
                continue;
            }
            final Interface current = item;
            final String area = "interfaces[" + index + "]";
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(current, area + ".name"));
            entry.put("class", className(current));
            entry.put("interfaceType", read(area + ".interfaceType", new Value() {
                public Object get() { return current.getInterfaceType().getPresentationName(); }
            }));
            entry.put("interfaceTypeClass", read(area + ".interfaceTypeClass", new Value() {
                public Object get() { return current.getInterfaceType().getClass().getName(); }
            }));
            // Interface.getRegion0()/getRegion1() are deprecated on the abstract base; the
            // non-deprecated accessors live on TwoSidedInterface, and HubInterface carries a
            // region list instead of two sides.
            if (current instanceof TwoSidedInterface) {
                final TwoSidedInterface twoSided = (TwoSidedInterface) current;
                entry.put("regionModel", "twoSided");
                entry.put("region0", read(area + ".region0", new Value() {
                    public Object get() { return regionName(twoSided.getRegion0()); }
                }));
                entry.put("region1", read(area + ".region1", new Value() {
                    public Object get() { return regionName(twoSided.getRegion1()); }
                }));
                entry.put("connectedRegions", null);
            } else if (current instanceof HubInterface) {
                final HubInterface hub = (HubInterface) current;
                entry.put("regionModel", "hub");
                entry.put("region0", null);
                entry.put("region1", null);
                entry.put("connectedRegions", read(area + ".connectedRegions", new Value() {
                    public Object get() { return regionNames(hub.getConnectedRegions()); }
                }));
            } else {
                entry.put("regionModel", "none");
                entry.put("region0", null);
                entry.put("region1", null);
                entry.put("connectedRegions", null);
            }
            out.add(entry);
            index++;
        }
        return out;
    }

    private List<Object> collectReports(Simulation sim) {
        List<Object> out = new ArrayList<Object>();
        Collection<Report> reports;
        try {
            reports = sim.getReportManager().getObjects();
        } catch (Throwable t) {
            addWarning("SECTION_UNREADABLE", "reports", "getReportManager().getObjects() failed: " + describe(t));
            return out;
        }
        if (reports == null) {
            addWarning("SECTION_UNREADABLE", "reports", "the report manager returned no collection");
            return out;
        }

        int index = 0;
        for (Report item : reports) {
            if (item == null) {
                addWarning("SKIPPED", "reports[" + index + "]", "null entry in the report manager");
                index++;
                continue;
            }
            Map<String, Object> entry = new LinkedHashMap<String, Object>();
            entry.put("name", nameOf(item, "reports[" + index + "].name"));
            entry.put("class", className(item));
            out.add(entry);
            index++;
        }
        return out;
    }

    private Map<String, Object> countsOf(List<Object> regions, List<Object> continua,
            List<Object> geometryParts, List<Object> derivedParts, List<Object> interfaces,
            List<Object> reports) {
        int boundaries = 0;
        for (Object region : regions) {
            List<?> list = listValue(region, "boundaries");
            if (list != null) {
                boundaries += list.size();
            }
        }
        int models = 0;
        for (Object continuum : continua) {
            List<?> list = listValue(continuum, "models");
            if (list != null) {
                models += list.size();
            }
        }
        int geometryPartSurfaces = 0;
        for (Object part : geometryParts) {
            List<?> list = listValue(part, "partSurfaces");
            if (list != null) {
                geometryPartSurfaces += list.size();
            }
        }
        Map<String, Object> counts = new LinkedHashMap<String, Object>();
        counts.put("regions", Integer.valueOf(regions.size()));
        counts.put("boundaries", Integer.valueOf(boundaries));
        counts.put("physicsContinua", Integer.valueOf(continua.size()));
        counts.put("enabledModels", Integer.valueOf(models));
        counts.put("geometryParts", Integer.valueOf(geometryParts.size()));
        counts.put("geometryPartSurfaces", Integer.valueOf(geometryPartSurfaces));
        counts.put("derivedParts", Integer.valueOf(derivedParts.size()));
        counts.put("interfaces", Integer.valueOf(interfaces.size()));
        counts.put("reports", Integer.valueOf(reports.size()));
        counts.put("warnings", Integer.valueOf(warnings.size()));
        return counts;
    }

    private static List<?> listValue(Object entry, String key) {
        Object value = mapValue(entry, key);
        return (value instanceof List) ? (List<?>) value : null;
    }

    private static Object mapValue(Object entry, String key) {
        if (entry instanceof Map) {
            return ((Map<?, ?>) entry).get(key);
        }
        return null;
    }

    private interface Value {
        Object get() throws Exception;
    }

    /**
     * Reads one field; a failing read is recorded as a warning and yields null.
     *
     * @param area JSON path used to locate the failure in the report
     * @param value reader executed once
     * @return the field value, or null when the field could not be read
     */
    private Object read(String area, Value value) {
        try {
            return value.get();
        } catch (Throwable t) {
            addWarning("FIELD_UNREADABLE", area, describe(t));
            return null;
        }
    }

    private String nameOf(final ClientServerObject object, final String area) {
        if (object == null) {
            return null;
        }
        Object name = read(area, new Value() {
            public Object get() { return object.getPresentationName(); }
        });
        return (name == null) ? null : String.valueOf(name);
    }

    private static String regionName(Region region) {
        return (region == null) ? null : region.getPresentationName();
    }

    private static List<Object> regionNames(Collection<Region> regions) {
        List<Object> names = new ArrayList<Object>();
        if (regions == null) {
            return names;
        }
        for (Region region : regions) {
            if (region != null) {
                names.add(region.getPresentationName());
            }
        }
        return names;
    }

    /** Best-effort name for warning text; nameOf() already records the real failure of a field read. */
    private static String safeName(final ClientServerObject object) {
        if (object == null) {
            return "?";
        }
        try {
            return object.getPresentationName();
        } catch (Throwable t) {
            return "?";
        }
    }

    private static String className(Object object) {
        return (object == null) ? null : object.getClass().getName();
    }

    private static String simpleClassName(Object object) {
        if (object == null) {
            return null;
        }
        String name = object.getClass().getName();
        int dot = name.lastIndexOf('.');
        return (dot < 0) ? name : name.substring(dot + 1);
    }

    private void addWarning(String code, String area, String message) {
        if (warnings.size() >= MAX_WARNINGS) {
            warningsTruncated = true;
            return;
        }
        Map<String, Object> warning = new LinkedHashMap<String, Object>();
        warning.put("code", code);
        warning.put("area", area);
        warning.put("message", message);
        warnings.add(warning);
        if (OK.equals(status)) {
            status = PARTIAL;
        }
    }

    private void fatal(String area, String message) {
        status = ERROR;
        if (fatalError == null) {
            fatalError = area + ": " + message;
        }
        addWarning("FATAL", area, message);
    }

    private static String describe(Throwable t) {
        if (t == null) {
            return "unknown error";
        }
        String message = t.getMessage();
        return (message == null) ? t.getClass().getName() : t.getClass().getName() + ": " + message;
    }

    private static String utcNow() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT);
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        return format.format(new Date());
    }

    private static void writeJsonFile(File file, Map<String, Object> report) throws IOException {
        StringBuilder json = new StringBuilder(1 << 16);
        writeValue(json, report, 0);
        json.append('\n');
        Writer writer = new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8);
        try {
            writer.write(json.toString());
        } finally {
            writer.close();
        }
    }

    private static void writeValue(StringBuilder json, Object value, int depth) {
        if (value == null) {
            json.append("null");
            return;
        }
        if (value instanceof String) {
            writeString(json, (String) value);
            return;
        }
        if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
            json.append(value.toString());
            return;
        }
        if (value instanceof Number) {
            double number = ((Number) value).doubleValue();
            if (Double.isNaN(number) || Double.isInfinite(number)) {
                json.append("null");
            } else {
                json.append(value.toString());
            }
            return;
        }
        if (value instanceof Map) {
            writeObject(json, (Map<?, ?>) value, depth);
            return;
        }
        if (value instanceof Collection) {
            writeArray(json, (Collection<?>) value, depth);
            return;
        }
        writeString(json, String.valueOf(value));
    }

    private static void writeObject(StringBuilder json, Map<?, ?> map, int depth) {
        if (map.isEmpty()) {
            json.append("{}");
            return;
        }
        json.append("{\n");
        int remaining = map.size();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            indent(json, depth + 1);
            writeString(json, String.valueOf(entry.getKey()));
            json.append(": ");
            writeValue(json, entry.getValue(), depth + 1);
            remaining--;
            json.append((remaining > 0) ? ",\n" : "\n");
        }
        indent(json, depth);
        json.append('}');
    }

    private static void writeArray(StringBuilder json, Collection<?> values, int depth) {
        if (values.isEmpty()) {
            json.append("[]");
            return;
        }
        json.append("[\n");
        int remaining = values.size();
        for (Object value : values) {
            indent(json, depth + 1);
            writeValue(json, value, depth + 1);
            remaining--;
            json.append((remaining > 0) ? ",\n" : "\n");
        }
        indent(json, depth);
        json.append(']');
    }

    private static void indent(StringBuilder json, int depth) {
        for (int i = 0; i < depth; i++) {
            json.append("  ");
        }
    }

    /**
     * Writes a JSON string with every non-ASCII character escaped as a backslash-u hex escape, so
     * the report stays valid whatever encoding the reader assumes (localized STAR names are
     * frequently non-ASCII).
     */
    private static void writeString(StringBuilder json, String value) {
        json.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"':
                    json.append("\\\"");
                    break;
                case '\\':
                    json.append("\\\\");
                    break;
                case '\b':
                    json.append("\\b");
                    break;
                case '\f':
                    json.append("\\f");
                    break;
                case '\n':
                    json.append("\\n");
                    break;
                case '\r':
                    json.append("\\r");
                    break;
                case '\t':
                    json.append("\\t");
                    break;
                default:
                    if (c < 0x20 || c > 0x7E) {
                        json.append("\\u");
                        json.append(HEX[(c >> 12) & 0xF]);
                        json.append(HEX[(c >> 8) & 0xF]);
                        json.append(HEX[(c >> 4) & 0xF]);
                        json.append(HEX[c & 0xF]);
                    } else {
                        json.append(c);
                    }
                    break;
            }
        }
        json.append('"');
    }

    private static final char[] HEX = "0123456789abcdef".toCharArray();
}
