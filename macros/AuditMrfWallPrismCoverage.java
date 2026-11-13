/* Generic MRF wall prism-coverage audit (P2.3 G5 mesh stage): measure the REALISED near-wall prism
 * coverage of the generated volume mesh instead of trusting the AutoMesh operation defaults.
 *
 * Measured lesson behind this macro (review-2x): ConfigureMrfMeshOperation records the operation's
 * prism_layers defaults and "actual_prism_coverage: not_measured"; a mesh with zero or too few actual
 * wall prisms can still have >100k cells and pass the generic cell-quality minima. This read-only audit
 * reads the native PrismLayerNumber / FirstPrismLayerHeight fields on the Volume Mesh for every MATERIAL
 * wall of the rotating region (interface parents excluded by native parent-boundary ownership), and gates
 * them against the CaseSpec:
 *   quality.mesh_min_wall_prism_layers (area-averaged wall prism layers >= N)
 *   quality.mesh_wall_prism_exception_area_fraction_max (prism-free wall area fraction <= f)
 * It writes mrf_wall_prism_coverage.json (+ .tsv) with the measured values and gate result, and fails
 * closed if the gate fails or if anything is unmeasured. No initialization, no iterations, no meshing,
 * no save. The y+ gate belongs to the solve stage and is deliberately not attempted here.
 */
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.ClientServerObject;
import star.base.report.AreaAverageReport;
import star.base.report.MinReport;
import star.base.report.ScalarReport;
import star.common.Boundary;
import star.common.BoundaryInterface;
import star.common.FieldFunction;
import star.common.FieldFunctionTypeOption;
import star.common.InternalBoundary;
import star.common.Interface;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.Units;
import star.common.UserFieldFunction;
import star.common.FvRepresentation;

public class AuditMrfWallPrismCoverage extends StarMacro {

    private static final String COMPLETE = "MRF_WALL_PRISM_COVERAGE_COMPLETE";

    private static final class Wall {
        String region;
        String name;
        double prismFreeFraction;
        double belowMinFraction;
        double areaAverageLayers;
        double minLayers;
        double firstHeightMin;
        double firstHeightAverage;
        double firstHeightMax;
        boolean heightMeasured;
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File record = new File(dir, "mrf_wall_prism_coverage.json");
        boolean measured = false;
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            String roles = object(spec, "mrf_regions");
            String quality = object(spec, "quality");
            double minLayers = number(quality, "mesh_min_wall_prism_layers");
            double maxFreeFraction = number(quality, "mesh_wall_prism_exception_area_fraction_max");
            if (!(minLayers >= 1) || !(maxFreeFraction >= 0 && maxFreeFraction <= 1)) {
                throw new IllegalArgumentException("Invalid CaseSpec wall-prism thresholds");
            }
            if (sim.getSimulationIterator().getCurrentIteration() != 0) {
                throw new IllegalStateException("Prism audit requires an unsolved saved mesh");
            }
            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No volume mesh to audit");
            Region rotor = sim.getRegionManager().getRegion(string(roles, "rotor_region"));
            Region stator = sim.getRegionManager().getRegion(string(roles, "stator_region"));
            if (rotor == null || stator == null || rotor == stator) throw new IllegalStateException("MRF regions are missing");
            Set<Boundary> rotorBoundaries = new HashSet<Boundary>(rotor.getBoundaryManager().getBoundaries());
            Set<Boundary> statorBoundaries = new HashSet<Boundary>(stator.getBoundaryManager().getBoundaries());
            Set<Boundary> interfaceParents = new HashSet<Boundary>();
            int interfaces = 0;
            for (Interface inter : sim.getInterfaceManager().getObjects()) {
                if (!(inter instanceof BoundaryInterface)) throw new IllegalStateException("Unsupported interface type");
                BoundaryInterface pair = (BoundaryInterface) inter;
                Boundary a = pair.getParentBoundary0();
                Boundary b = pair.getParentBoundary1();
                if (a == null || b == null || a.equals(b)) throw new IllegalStateException("Interface has bad parents");
                boolean crosses = (rotorBoundaries.contains(a) && statorBoundaries.contains(b))
                        || (rotorBoundaries.contains(b) && statorBoundaries.contains(a));
                if (!crosses) throw new IllegalStateException("Interface does not connect the MRF regions");
                interfaceParents.add(a);
                interfaceParents.add(b);
                interfaces++;
            }
            if (interfaces < 1) throw new IllegalStateException("No in-place interface between the regions");
            FieldFunction numberField = null;
            FieldFunction heightField = null;
            for (FieldFunction f : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
                if ("PrismLayerNumber".equals(f.getFunctionName())) numberField = f;
                if ("FirstPrismLayerHeight".equals(f.getFunctionName())) heightField = f;
            }
            if (numberField == null || heightField == null) {
                throw new IllegalStateException("Native prism fields PrismLayerNumber / FirstPrismLayerHeight unavailable");
            }
            Units metres = sim.getUnitsManager().getObject("m");
            List<Wall> walls = new ArrayList<Wall>();
            for (Boundary boundary : rotor.getBoundaryManager().getBoundaries()) {
                if (interfaceParents.contains(boundary)) continue;
                if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                Wall wall = new Wall();
                wall.region = rotor.getPresentationName();
                wall.name = boundary.getPresentationName();
                wall.prismFreeFraction = areaAverage(sim, temporary, mesh,
                        indicator(sim, temporary, "(${PrismLayerNumber} < 0.5) ? 1 : 0"), boundary, 1.0);
                // CaseSpec semantics: "wall area with fewer than the minimum wall prism layers must stay <= 1%;
                // zero prism-free area on the blade". The AREA-AVERAGE layer count is kept only as a diagnostic.
                wall.belowMinFraction = areaAverage(sim, temporary, mesh,
                        indicator(sim, temporary, String.format("(${PrismLayerNumber} < %.1f) ? 1 : 0", minLayers)),
                        boundary, 1.0);
                wall.areaAverageLayers = areaAverage(sim, temporary, mesh, numberField, boundary, 1.0);
                wall.minLayers = extremum(sim, temporary, mesh, numberField, boundary, true);
                double[] heights = new double[3];
                heights[0] = extremum(sim, temporary, mesh, heightField, boundary, true);
                heights[1] = areaAverage(sim, temporary, mesh, heightField, boundary, 1.0) ;
                heights[2] = extremum(sim, temporary, mesh, heightField, boundary, false);
                wall.heightMeasured = true;
                for (double value : heights) if (!Double.isFinite(value) || value < 0) wall.heightMeasured = false;
                wall.firstHeightMin = heights[0];
                wall.firstHeightAverage = heights[1];
                wall.firstHeightMax = heights[2];
                if (!Double.isFinite(wall.prismFreeFraction) || wall.prismFreeFraction < 0 || wall.prismFreeFraction > 1)
                    throw new IllegalStateException("Invalid prism-free fraction on " + wall.name);
                if (!Double.isFinite(wall.areaAverageLayers) || wall.areaAverageLayers < 0)
                    throw new IllegalStateException("Invalid area-average layer count on " + wall.name);
                walls.add(wall);
            }
            if (walls.isEmpty()) {
                throw new IllegalStateException("No material wall boundary found in the rotating region; the gate would be vacuous");
            }
            double worstFree = 0.0;
            double worstBelowMin = 0.0;
            double worstAverageLayers = Double.MAX_VALUE;
            double worstMinLayers = Double.MAX_VALUE;
            double minHeight = Double.MAX_VALUE;
            int unmeasuredWalls = 0;
            for (Wall wall : walls) {
                worstFree = Math.max(worstFree, wall.prismFreeFraction);
                worstBelowMin = Math.max(worstBelowMin, wall.belowMinFraction);
                worstAverageLayers = Math.min(worstAverageLayers, wall.areaAverageLayers);
                if (Double.isFinite(wall.minLayers)) worstMinLayers = Math.min(worstMinLayers, wall.minLayers);
                if (wall.heightMeasured) {
                    minHeight = Math.min(minHeight, wall.firstHeightMin);
                } else {
                    unmeasuredWalls++;
                }
            }
            // CaseSpec gates: (a) fewer-than-minimum-layer wall area <= exception fraction; (b) zero prism-free
            // area on the blade; (c) at least one native layer everywhere (min layer >= 1); (d) every material wall
            // has a measured positive first-layer height. The area-average layer count stays diagnostic only.
            boolean belowMinPass = worstBelowMin <= maxFreeFraction;
            boolean freePass = worstFree <= 1.0e-9;
            boolean layerMinPass = Double.isFinite(worstMinLayers) && worstMinLayers >= 1.0;
            boolean heightPass = unmeasuredWalls == 0 && Double.isFinite(minHeight) && minHeight > 0.0;
            boolean gatePassed = belowMinPass && freePass && layerMinPass && heightPass;
            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"").append(gatePassed ? "ok" : "error").append('"')
                    .append(",\"audit\":\"mrf_wall_prism_coverage\"")
                    .append(",\"region\":\"").append(esc(rotor.getPresentationName())).append('"')
                    .append(",\"interfaces\":").append(interfaces)
                    .append(",\"wall_boundaries\":").append(walls.size())
                    .append(",\"cells\":").append(mesh.getCellCount())
                    .append(",\"thresholds\":{\"mesh_min_wall_prism_layers\":").append(minLayers)
                    .append(",\"mesh_wall_prism_exception_area_fraction_max\":").append(maxFreeFraction).append('}')
                    .append(",\"max_prism_free_area_fraction\":").append(worstFree)
                    .append(",\"max_below_min_layer_area_fraction\":").append(worstBelowMin)
                    .append(",\"min_area_average_prism_layers\":").append(worstAverageLayers)
                    .append(",\"min_prism_layers\":").append(Double.isFinite(worstMinLayers) ? worstMinLayers : -1.0)
                    .append(",\"min_first_prism_height_m\":").append(Double.isFinite(minHeight) ? minHeight : -1.0)
                    .append(",\"unmeasured_walls\":" ).append(unmeasuredWalls)
                    .append(",\"below_min_layer_pass\":").append(belowMinPass)
                    .append(",\"prism_free_pass\":").append(freePass)
                    .append(",\"layer_min_pass\":").append(layerMinPass)
                    .append(",\"height_pass\":").append(heightPass)
                    .append(",\"gate_passed\":").append(gatePassed)
                    .append(",\"walls\":[");
            for (int i = 0; i < walls.size(); i++) {
                Wall wall = walls.get(i);
                if (i > 0) json.append(',');
                json.append("{\"name\":\"").append(esc(wall.name)).append('"')
                        .append(",\"prism_free_area_fraction\":").append(wall.prismFreeFraction)
                        .append(",\"below_min_layer_area_fraction\":").append(wall.belowMinFraction)
                        .append(",\"area_average_prism_layers\":").append(wall.areaAverageLayers)
                        .append(",\"min_prism_layers\":").append(Double.isFinite(wall.minLayers) ? wall.minLayers : -1.0)
                        .append(",\"first_prism_height_min_m\":").append(wall.heightMeasured ? wall.firstHeightMin : -1.0)
                        .append(",\"first_prism_height_area_average_m\":").append(wall.heightMeasured ? wall.firstHeightAverage : -1.0)
                        .append(",\"first_prism_height_max_m\":").append(wall.heightMeasured ? wall.firstHeightMax : -1.0)
                        .append('}');
            }
            json.append("],\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
            try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) { out.write(json.toString()); }
            measured = true;
            try (Writer tsv = new OutputStreamWriter(new FileOutputStream(new File(dir, "mrf_wall_prism_coverage.tsv")),
                    StandardCharsets.UTF_8)) {
                tsv.write("WALL\tPRISM_FREE_FRACTION\tBELOW_MIN_FRACTION\tAREA_AVERAGE_LAYERS\tMIN_LAYERS\tFIRST_HEIGHT_MIN_M\n");
                for (Wall wall : walls) {
                    tsv.write(wall.name + "\t" + wall.prismFreeFraction + "\t" + wall.belowMinFraction + "\t"
                            + wall.areaAverageLayers + "\t" + wall.minLayers + "\t"
                            + (wall.heightMeasured ? wall.firstHeightMin : "NA") + "\n");
                }
                tsv.write("SUMMARY\tmax_prism_free_fraction\t" + worstFree + "\n");
                tsv.write("SUMMARY\tmin_area_average_layers\t" + worstAverageLayers + "\n");
                tsv.write("GATE\twall_prism_coverage\t" + gatePassed + "\n");
                tsv.write("CELLS\t" + mesh.getCellCount() + "\n");
                tsv.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            if (!gatePassed) {
                throw new IllegalStateException("MRF wall prism coverage gate failed: max_below_min_layer_area_fraction="
                        + worstBelowMin + " (<= " + maxFreeFraction + "), max_prism_free_area_fraction=" + worstFree
                        + " (== 0), min_prism_layers=" + worstMinLayers + " (>= 1), unmeasured_walls=" + unmeasuredWalls
                        + ", min_first_prism_height_m=" + minHeight);
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            if (!measured) {
                try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) {
                    out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
                } catch (Throwable ignored) {
                    // the original failure is rethrown below; the diagnostic must not mask it
                }
            }
            throw new RuntimeException("MRF wall prism-coverage audit failed", failure);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject object : temporary) {
                try { sim.deleteObjects(Collections.singletonList(object)); } catch (Throwable ignored) { }
            }
        }
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (star.common.Representation representation : sim.getRepresentationManager().getObjects()) {
            if (representation instanceof FvRepresentation) return (FvRepresentation) representation;
        }
        return null;
    }

    private static UserFieldFunction indicator(Simulation sim, List<ClientServerObject> temporary, String definition) {
        UserFieldFunction bare = sim.getFieldFunctionManager().createFieldFunction();
        temporary.add(bare);
        bare.setFunctionName("TEMP_PRISM_IND_" + temporary.size());
        bare.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
        bare.setDefinition(definition);
        return bare;
    }

    private static double areaAverage(Simulation sim, List<ClientServerObject> temporary, FvRepresentation mesh,
                                      FieldFunction field, Boundary boundary, double scale) throws Exception {
        AreaAverageReport report = sim.getReportManager().createReport(AreaAverageReport.class);
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(boundary);
        return report.getReportMonitorValue() * scale;
    }

    private static double extremum(Simulation sim, List<ClientServerObject> temporary, FvRepresentation mesh,
                                   FieldFunction field, Boundary boundary, boolean minimum) throws Exception {
        String classId = minimum ? "star.base.report.MinReport" : "star.base.report.MaxReport";
        ScalarReport report = sim.getReportManager().create(classId);
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(boundary);
        return report.getReportMonitorValue();
    }

    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
        return m.group(1);
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }

    private static double number(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(m.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static String esc(String value) {
        StringBuilder out = new StringBuilder();
        for (char c : value.toCharArray()) {
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
