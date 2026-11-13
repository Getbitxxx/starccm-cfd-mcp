/* Locate cells below the installed STAR-CCM+ 2502 volume-change guidance.
 *
 * ②R20 adaptation of the existing diagnostic (no new audit macro was added): the region role and the
 * threshold now come from the CaseSpec instead of a sample-specific literal, and the primary evidence
 * files are downloadable ones (.json/.csv), because the runner only transfers .json/.log/.tsv/.csv. The
 * legacy `mrf_low_volume_change.txt` is still written because `workflows/core/manifest.json` declares it
 * as the required artefact of the packaged stage `diagnose-mrf-low-volume-change`; dropping that name
 * would silently retire an installed stage (independent-review finding B2).
 *
 * It is READ-ONLY with respect to the mesh: it does not initialize the solution, iterate, mesh, delete
 * cells or save. It measures the same VolumeChange field function that AuditMrfVolumeMeshQuality already
 * reads uninitialized through MinReport, so both numbers describe the same checkpoint.
 *
 * Ordering matters and is deliberate (independent-review concern C5): the JSON with the exact count,
 * the cut distribution and the per-region minima is flushed BEFORE the optional per-cell listing, so a
 * stage deadline can never spend the round's only authorized localization with no evidence file. The
 * JSON is rewritten once the listing finishes, and the listing is bounded by cells AND never throws.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Vector;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.ClientServerObject;
import star.base.report.ScalarReport;
import star.common.CellSet;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;
import star.vis.ThresholdMode;
import star.vis.ThresholdPart;

public class DiagnoseMrfLowVolumeChange extends StarMacro {
    private static final String COMPLETE = "MRF_LOW_VOLUME_CHANGE_DIAGNOSTIC_COMPLETE";
    private static final int MAX_PER_CELL_LISTING = 32;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        ThresholdPart selected = null;
        CellSet cells = null;
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            double threshold = number(object(spec, "quality"), "mesh_min_volume_change");
            String rotatingName = string(object(spec, "mrf_regions"), "rotor_region");
            String stationaryName = string(object(spec, "mrf_regions"), "stator_region");
            String zone = object(spec, "rotation_zone");
            double zoneRadius = number(zone, "radius_m"), zoneZMin = number(zone, "z_min_m"),
                    zoneZMax = number(zone, "z_max_m");
            if (!(threshold > 0 && zoneRadius > 0 && zoneZMin < zoneZMax))
                throw new IllegalArgumentException("Invalid CaseSpec localization inputs");

            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("Volume mesh representation missing on the diagnostic input");
            Region rotating = sim.getRegionManager().getRegion(rotatingName);
            if (rotating == null) throw new IllegalStateException("CaseSpec rotating region missing: " + rotatingName);

            FieldFunction volumeChange = null;
            for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh))
                if (candidate instanceof VolumeChangeFunction) { volumeChange = candidate; break; }
            if (volumeChange == null) throw new IllegalStateException("Volume Change field function missing");

            // Per-region minimum, measured exactly the way the existing audit measures it (MinReport on
            // the mesh representation, no initialization), so the two numbers are comparable.
            String minRotating = report(sim, mesh, rotating, volumeChange, temporary);
            Region stationary = sim.getRegionManager().getRegion(stationaryName);
            String minStationary = stationary == null ? "null"
                    : report(sim, mesh, stationary, volumeChange, temporary);

            // Exact counts at the gate and at progressively tighter cuts, from one whole-mesh CellSet.
            // Scope is recorded explicitly: the counts are mesh-wide, the minima are per region.
            cells = mesh.getCellSetManager().createEmptyCellSet();
            double[] cuts = {threshold, threshold * 0.95, threshold * 0.90, threshold * 0.80, threshold * 0.50};
            long[] counts = new long[cuts.length];
            for (int i = 0; i < cuts.length; i++) {
                cells.replaceThreshold(volumeChange, ThresholdMode.BELOW_TAG.getValue(), cuts[i], cuts[i]);
                counts[i] = cells.getNumCells();
            }
            long exact = counts[0];

            FieldFunction position = sim.getFieldFunctionManager().getFunction("Position");
            double[] lo = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY};
            double[] hi = {Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
            for (Region region : sim.getRegionManager().getRegions()) {
                double[] ext = sim.getMeshManager().getRegionExtents(region);
                if (ext.length != 6) throw new IllegalStateException("Missing region extents");
                for (int i = 0; i < 3; i++) {
                    lo[i] = Math.min(lo[i], ext[2 * i]);
                    hi[i] = Math.max(hi[i], ext[2 * i + 1]);
                }
            }

            // Phase 1: the count/min evidence, flushed before anything that can be slow or fail.
            writeJson(dir, record(rotatingName, stationaryName, threshold, mesh.getCellCount(), exact, cuts,
                    counts, minRotating, minStationary, zoneRadius, zoneZMin, zoneZMax, lo, hi,
                    "pending", -1, "pending", sim.isInitialized()));

            // Whole-set listing in ONE call: every offending cell's centroid plus its VolumeChange value.
            // A file that exists but holds no data row is NOT a success (concern C2).
            String export = "skipped";
            File csv = new File(dir, "mrf_low_volume_change_cells.csv");
            long estimated = -1;
            try {
                Vector<Object> regions = new Vector<Object>(sim.getRegionManager().getObjects());
                selected = sim.getPartManager().createThresholdPart(regions,
                        new Vector<Double>(Arrays.asList(0.0, threshold * 1.0000001)),
                        volumeChange, ThresholdMode.BETWEEN_TAG.getValue());
                selected.setPresentationName("TEMP_LOW_VOLUME_CHANGE_DIAGNOSTIC");
                estimated = selected.getEstimatedNumberOfCells();
                selected.exportDataSourceCSV(mesh, false, csv.getAbsolutePath(), volumeChange);
                long rows = csv.isFile() ? Math.max(0, Files.readAllLines(csv.toPath()).size() - 1) : -1;
                export = rows > 0 ? "written:" + rows + "_data_rows" : "failed:no_data_rows(" + rows + ")";
            } catch (Throwable exportFailure) {
                export = "failed: " + exportFailure;
            }

            // Phase 2: bounded, non-throwing independent fallback so a header-only export cannot drop
            // the coordinates, and a partial listing is still evidence (concern C3).
            String fallback = "not_needed";
            List<double[]> listed = new ArrayList<double[]>();
            boolean reconciled = false;
            if (!export.startsWith("written")) {
                if (exact > 0 && exact <= MAX_PER_CELL_LISTING && position != null) {
                    try {
                        locate(cells, volumeChange, position, threshold, lo, hi, listed);
                        reconciled = listed.size() == exact;
                        fallback = reconciled ? "listed_" + listed.size() : "partial_" + listed.size() + "_of_" + exact;
                    } catch (Throwable locateFailure) {
                        fallback = "failed_after_" + listed.size() + ": " + locateFailure;
                    }
                } else fallback = "bounded_out(" + exact + ")";
            }
            if (!listed.isEmpty()) {
                try (FileWriter out = new FileWriter(new File(dir, "mrf_low_volume_change_fallback.csv"),
                        StandardCharsets.UTF_8)) {
                    out.write("cell,x_m,y_m,z_m,radius_m\n");
                    for (int i = 0; i < listed.size(); i++) {
                        double[] c = listed.get(i);
                        out.write((i + 1) + "," + c[0] + "," + c[1] + "," + c[2] + ","
                                + Math.sqrt(c[0] * c[0] + c[1] * c[1]) + "\n");
                    }
                    out.write("CELL_COUNT\t" + exact + "\n");
                    out.write("LOCALIZED_COUNT\t" + listed.size() + "\n");
                    out.write("GATE\tcells_found_equals_localized\t" + reconciled + "\n");
                }
            }

            // Phase 3: rewrite the JSON with the final listing status, and write the legacy .txt the
            // packaged workflow stage still requires (finding B2).
            writeJson(dir, record(rotatingName, stationaryName, threshold, mesh.getCellCount(), exact, cuts,
                    counts, minRotating, minStationary, zoneRadius, zoneZMin, zoneZMax, lo, hi,
                    export, estimated, fallback, sim.isInitialized()));
            try (FileWriter out = new FileWriter(new File(dir, "mrf_low_volume_change.txt"),
                    StandardCharsets.UTF_8)) {
                out.write("region=" + rotatingName + "\nmetric=VolumeChange\nrange=[0," + threshold + "]\n");
                out.write("exact_cells_below_threshold=" + exact + "\n");
                out.write("estimated_cells_in_threshold_part=" + estimated + "\nexport=" + export + "\n");
                out.write("min_volume_change_rotating=" + minRotating + "\n");
                out.write("fallback_listing=" + fallback + "\n");
                out.write("read_only=true\n" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(dir, "mrf_low_volume_change.json"),
                    StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + escape(failure.toString())
                        + "\",\"read_only\":true,\"complete_marker\":null}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF low-volume-change diagnostic failed", failure);
        } finally {
            if (selected != null) { try { sim.deleteObjects(Collections.singletonList(selected)); } catch (Throwable ignored) { } }
            if (cells != null) { try { sim.deleteObjects(Collections.singletonList(cells)); } catch (Throwable ignored) { } }
            if (!temporary.isEmpty()) { try { sim.deleteObjects(new ArrayList<ClientServerObject>(temporary)); } catch (Throwable ignored) { } }
        }
    }

    private static String record(String rotatingName, String stationaryName, double threshold, long meshCells,
            long exact, double[] cuts, long[] counts, String minRotating, String minStationary,
            double zoneRadius, double zoneZMin, double zoneZMax, double[] lo, double[] hi, String export,
            long estimated, String fallback, boolean initialized) {
        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"status\":\"ok\",\"diagnostic\":\"mrf_low_volume_change\",")
            .append("\"region_role\":\"").append(escape(rotatingName)).append("\",")
            .append("\"stationary_region\":\"").append(escape(stationaryName)).append("\",")
            .append("\"metric\":\"VolumeChange\",\"threshold\":").append(threshold).append(',')
            .append("\"count_scope\":\"whole_volume_mesh\",\"minimum_scope\":\"per_region\",")
            .append("\"cells\":").append(meshCells).append(',')
            .append("\"exact_cells_below_threshold\":").append(exact).append(',')
            .append("\"cut_counts\":[");
        for (int i = 0; i < cuts.length; i++) {
            if (i > 0) json.append(',');
            json.append("{\"cut\":").append(cuts[i]).append(",\"cells\":").append(counts[i]).append('}');
        }
        json.append("],")
            .append("\"min_volume_change_rotating\":").append(minRotating).append(',')
            .append("\"min_volume_change_stationary\":").append(minStationary).append(',')
            .append("\"rotation_zone\":{\"radius_m\":").append(zoneRadius)
            .append(",\"z_min_m\":").append(zoneZMin).append(",\"z_max_m\":").append(zoneZMax).append("},")
            .append("\"domain_extents_m\":[").append(lo[0]).append(',').append(lo[1]).append(',').append(lo[2])
            .append(',').append(hi[0]).append(',').append(hi[1]).append(',').append(hi[2]).append("],")
            .append("\"csv_export\":\"").append(escape(export)).append("\",")
            .append("\"csv\":\"mrf_low_volume_change_cells.csv\",")
            .append("\"estimated_cells_in_threshold_part\":").append(estimated).append(',')
            .append("\"fallback_listing\":\"").append(escape(fallback)).append("\",")
            .append("\"solution_initialized\":").append(initialized).append(',')
            .append("\"read_only\":true,\"mesh_modified\":false,\"cells_deleted\":0,")
            .append("\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
        return json.toString();
    }

    private static void writeJson(File dir, String json) throws Exception {
        try (FileWriter out = new FileWriter(new File(dir, "mrf_low_volume_change.json"),
                StandardCharsets.UTF_8)) {
            out.write(json);
        }
    }

    private static String report(Simulation sim, FvRepresentation mesh, Region region, FieldFunction field,
            List<ClientServerObject> temporary) {
        ScalarReport report = sim.getReportManager().create("star.base.report.MinReport");
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(region);
        double value = report.getReportMonitorValue();
        if (!Double.isFinite(value))
            throw new IllegalStateException("Nonfinite VolumeChange minimum in " + region.getPresentationName());
        return Double.toString(value);
    }

    // Exact per-cell centroid by bisection of the offending CellSet, reusing the pattern this project
    // already proved in ListAir3bLowFaceValidityCells; the caller bounds the offending cell count.
    private static void locate(CellSet cells, FieldFunction field, FieldFunction position, double cutoff,
            double[] domainLow, double[] domainHigh, List<double[]> centers) {
        FieldFunction[] xyz = new FieldFunction[3];
        for (int axis = 0; axis < 3; axis++) xyz[axis] = position.getComponentFunction(axis);
        locateRecursive(cells, field, xyz, cutoff, domainLow, domainHigh, domainLow, domainHigh, centers, 0);
    }

    private static int countInBox(CellSet cells, FieldFunction field, FieldFunction[] xyz, double cutoff,
            double[] low, double[] high, double[] domainLow, double[] domainHigh) {
        cells.replaceThreshold(field, ThresholdMode.BELOW_TAG.getValue(), cutoff, cutoff);
        for (int axis = 0; axis < 3; axis++) {
            if (low[axis] > domainLow[axis])
                cells.subsetThreshold(xyz[axis], ThresholdMode.ABOVE_TAG.getValue(), low[axis], low[axis]);
            if (high[axis] < domainHigh[axis])
                cells.subsetThreshold(xyz[axis], ThresholdMode.BELOW_TAG.getValue(), high[axis], high[axis]);
        }
        return cells.getNumCells();
    }

    private static void locateRecursive(CellSet cells, FieldFunction field, FieldFunction[] xyz, double cutoff,
            double[] low, double[] high, double[] domainLow, double[] domainHigh, List<double[]> centers,
            int depth) {
        int count = countInBox(cells, field, xyz, cutoff, low, high, domainLow, domainHigh);
        if (count == 0) return;
        if (depth > 60) throw new IllegalStateException("Localization bisection did not converge");
        if (count == 1) {
            double[] a = low.clone(), b = high.clone();
            for (int axis = 0; axis < 3; axis++) {
                for (int step = 0; step < 22; step++) {
                    double mid = 0.5 * (a[axis] + b[axis]);
                    double[] leftHigh = b.clone();
                    leftHigh[axis] = mid;
                    if (countInBox(cells, field, xyz, cutoff, a, leftHigh, domainLow, domainHigh) == 1) b[axis] = mid;
                    else a[axis] = mid;
                }
            }
            centers.add(new double[] {0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]), 0.5 * (a[2] + b[2])});
            return;
        }
        int axis = 0;
        for (int i = 1; i < 3; i++) if (high[i] - low[i] > high[axis] - low[axis]) axis = i;
        double mid = 0.5 * (low[axis] + high[axis]);
        double[] leftHigh = high.clone();
        leftHigh[axis] = mid;
        double[] rightLow = low.clone();
        rightLow[axis] = mid;
        int left = countInBox(cells, field, xyz, cutoff, low, leftHigh, domainLow, domainHigh);
        int right = countInBox(cells, field, xyz, cutoff, rightLow, high, domainLow, domainHigh);
        if (left + right != count) {
            double eps = Math.max(1.0e-12, (high[axis] - low[axis]) * 1.0e-10);
            leftHigh[axis] = mid - eps;
            rightLow[axis] = mid + eps;
            left = countInBox(cells, field, xyz, cutoff, low, leftHigh, domainLow, domainHigh);
            right = countInBox(cells, field, xyz, cutoff, rightLow, high, domainLow, domainHigh);
            if (left + right != count) throw new IllegalStateException("Cell-set partition did not reconcile");
        }
        locateRecursive(cells, field, xyz, cutoff, low, leftHigh, domainLow, domainHigh, centers, depth + 1);
        locateRecursive(cells, field, xyz, cutoff, rightLow, high, domainLow, domainHigh, centers, depth + 1);
    }

    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
        return m.group(1);
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }

    private static double number(String json, String key) {
        Matcher m = Pattern.compile("\"" + key + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing number " + key);
        double v = Double.parseDouble(m.group(1));
        if (!Double.isFinite(v)) throw new IllegalArgumentException("Nonfinite " + key);
        return v;
    }

    private static String escape(String s) {
        StringBuilder out = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
