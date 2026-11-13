/* Read-only mesh diagnostic: localize the WORST cells (the cells that drive the quality-gate minima).
 *
 * Motivation (2026-10-30, user-authorized TE-mesh route step 1): the mesh-quality gate reports only the MINIMA
 * (volume change 0.010297 on production B13; 0.003581 / 0.004230 on the rejected coarse levels; 0.004731 on B18)
 * and never says WHERE the worst cells are.  A TE-localized mesh fix must be justified by evidence.
 *
 * For CellQuality / FaceValidity / VolumeChange this macro reports:
 *   - the minimum value (MinReport over every Region, with the per-region minima), and
 *   - the WORST-CELL CLUSTER: all cells whose value lies within a relative 1e-6 of the minimum, with the cell count
 *     and the centroid bounding box of that cluster (bisection on the Position components), and
 *   - the cells BELOW the CaseSpec threshold (count, and a bounding box only when that count is non-zero; an empty
 *     set is reported as bbox_defined=false with bbox=[region_min,region_max], never as an inverted pair).
 *
 * Review-15a blocker is addressed: a zero-count set can no longer produce an inverted bounding box or a false
 * "localized" claim; `localized` is true only when every metric's worst-cell cluster was located (count >= 1), and
 * each bounded set carries its own `bbox_defined` flag.
 *
 * Read-only: no initialisation, no solve, no save, no mesh change; temporary CellSets and reports are deleted.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import star.base.neo.ClientServerObject;
import star.base.report.MinReport;
import star.base.report.ScalarReport;
import star.common.CellQualityFunction;
import star.common.CellSet;
import star.common.FaceValidityFunction;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;
import star.vis.ThresholdMode;

public class AuditMrfBadCellLocalization extends StarMacro {
    private static final String COMPLETE = "MRF_BAD_CELL_LOCALIZATION_COMPLETE";
    private static final int BISECTIONS = 20;
    private static final int[] NO = {};

    private final List<ClientServerObject> temporary = new ArrayList<>();

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String spec = new String(Files.readAllBytes(new File(sim.getSessionDirFile(), "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String quality = object(spec, "quality");
            double cellQualityCut = number(quality, "mesh_min_cell_quality");
            double faceValidityCut = number(quality, "mesh_min_face_validity");
            double volumeChangeCut = number(quality, "mesh_min_volume_change");
            if (!(cellQualityCut > 0 && faceValidityCut > 0 && volumeChangeCut > 0)) {
                throw new IllegalStateException("CaseSpec quality thresholds are not physically sensible");
            }
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0) {
                throw new IllegalStateException("Volume mesh missing");
            }
            FieldFunction position = sim.getFieldFunctionManager().getFunction("Position");
            if (position == null) throw new IllegalStateException("Position field missing");
            double[] lo = new double[3];
            double[] hi = new double[3];
            for (int i = 0; i < 3; i++) {
                lo[i] = Double.POSITIVE_INFINITY;
                hi[i] = Double.NEGATIVE_INFINITY;
            }
            for (Region region : sim.getRegionManager().getRegions()) {
                double[] extents = sim.getMeshManager().getRegionExtents(region);
                if (extents.length != 6) throw new IllegalStateException("Missing region extents");
                for (int i = 0; i < 3; i++) {
                    lo[i] = Math.min(lo[i], extents[2 * i]);
                    hi[i] = Math.max(hi[i], extents[2 * i + 1]);
                }
            }
            String[] names = {"CellQuality", "FaceValidity", "VolumeChange"};
            double[] cuts = {cellQualityCut, faceValidityCut, volumeChangeCut};
            Class<?>[] classes = {CellQualityFunction.class, FaceValidityFunction.class, VolumeChangeFunction.class};
            StringBuilder flat = new StringBuilder();
            StringBuilder nested = new StringBuilder();
            boolean allLocated = true;
            try (FileWriter tsv = new FileWriter(new File(sim.getSessionDirFile(), "bad_cell_localization.tsv"),
                    StandardCharsets.UTF_8)) {
                tsv.write("metric\tcutoff\tmin_value\tworst_count\tworst_bbox_defined\t"
                        + "worst_min_x\tworst_max_x\tworst_min_y\tworst_max_y\tworst_min_z\tworst_max_z\t"
                        + "below_cutoff_count\tbelow_bbox_defined\n");
                for (int m = 0; m < names.length; m++) {
                    FieldFunction metric = metricField(sim, mesh, classes[m]);
                    double minValue = minimum(sim, mesh, metric);
                    double tolerance = Math.max(1.0e-12, Math.abs(minValue) * 1.0e-6);
                    double worstCut = minValue + tolerance;
                    double[][] worstBox = new double[3][2];
                    int worstCount = 0;
                    boolean worstDefined = false;
                    CellSet working = mesh.getCellSetManager().createEmptyCellSet();
                    temporary.add(working);
                    FieldFunction[] components = new FieldFunction[3];
                    for (int axis = 0; axis < 3; axis++) components[axis] = position.getComponentFunction(axis);
                    working.replaceThreshold(metric, ThresholdMode.BELOW_TAG.getValue(), worstCut, worstCut);
                    worstCount = working.getNumCells();
                    if (worstCount > 0) {
                        worstDefined = true;
                        for (int axis = 0; axis < 3; axis++) {
                            worstBox[axis][0] = bracket(mesh, working, metric, worstCut, components[axis],
                                    lo[axis], hi[axis], ThresholdMode.BELOW_TAG.getValue());
                            worstBox[axis][1] = bracket(mesh, working, metric, worstCut, components[axis],
                                    lo[axis], hi[axis], ThresholdMode.ABOVE_TAG.getValue());
                        }
                    } else {
                        allLocated = false;
                        for (int axis = 0; axis < 3; axis++) {
                            worstBox[axis][0] = lo[axis];
                            worstBox[axis][1] = hi[axis];
                        }
                    }
                    double[][] cutBox = new double[3][2];
                    CellSet below = mesh.getCellSetManager().createEmptyCellSet();
                    temporary.add(below);
                    below.replaceThreshold(metric, ThresholdMode.BELOW_TAG.getValue(), cuts[m], cuts[m]);
                    int cutCount = below.getNumCells();
                    boolean cutDefined = cutCount > 0;
                    if (cutDefined) {
                        for (int axis = 0; axis < 3; axis++) {
                            cutBox[axis][0] = bracket(mesh, below, metric, cuts[m], components[axis],
                                    lo[axis], hi[axis], ThresholdMode.BELOW_TAG.getValue());
                            cutBox[axis][1] = bracket(mesh, below, metric, cuts[m], components[axis],
                                    lo[axis], hi[axis], ThresholdMode.ABOVE_TAG.getValue());
                        }
                    } else {
                        for (int axis = 0; axis < 3; axis++) {
                            cutBox[axis][0] = lo[axis];
                            cutBox[axis][1] = hi[axis];
                        }
                    }
                    tsv.write(names[m] + "\t" + cuts[m] + "\t" + minValue + "\t" + worstCount + "\t" + worstDefined + "\t"
                            + worstBox[0][0] + "\t" + worstBox[0][1] + "\t" + worstBox[1][0] + "\t" + worstBox[1][1]
                            + "\t" + worstBox[2][0] + "\t" + worstBox[2][1] + "\t" + cutCount + "\t" + cutDefined + "\n");
                    tsv.flush();
                    appendFlat(flat, names[m], minValue, worstCount, worstDefined, worstBox, cutCount, cutDefined);
                    if (nested.length() > 0) nested.append(',');
                    nested.append('"').append(names[m]).append("\":{")
                            .append("\"cutoff\":").append(repr(cuts[m])).append(',')
                            .append("\"min_value\":").append(repr(minValue)).append(',')
                            .append("\"worst_count\":").append(worstCount).append(',')
                            .append("\"worst_bbox_defined\":").append(worstDefined).append(',')
                            .append("\"worst_bbox_min\":[").append(repr(worstBox[0][0])).append(',')
                            .append(repr(worstBox[1][0])).append(',').append(repr(worstBox[2][0])).append("],")
                            .append("\"worst_bbox_max\":[").append(repr(worstBox[0][1])).append(',')
                            .append(repr(worstBox[1][1])).append(',').append(repr(worstBox[2][1])).append("],")
                            .append("\"below_cutoff_count\":").append(cutCount).append(',')
                            .append("\"below_bbox_defined\":").append(cutDefined).append(',')
                            .append("\"below_bbox_min\":[").append(repr(cutBox[0][0])).append(',')
                            .append(repr(cutBox[1][0])).append(',').append(repr(cutBox[2][0])).append("],")
                            .append("\"below_bbox_max\":[").append(repr(cutBox[0][1])).append(',')
                            .append(repr(cutBox[1][1])).append(',').append(repr(cutBox[2][1])).append("]}");
                }
                tsv.write("ALL\taudit\tcomplete\n");
            }
            String json = "{\"status\":\"ok\",\"audit\":\"mrf_bad_cell_localization\",\"schema_version\":1,"
                    + "\"read_only\":true,\"cells\":" + mesh.getCellCount() + ','
                    + "\"thresholds_source\":\"case_spec.quality read at run time\","
                    + "\"thresholds\":{\"mesh_min_cell_quality\":" + repr(cellQualityCut)
                    + ",\"mesh_min_face_validity\":" + repr(faceValidityCut)
                    + ",\"mesh_min_volume_change\":" + repr(volumeChangeCut) + "},"
                    + "\"min_clusters_located\":" + (allLocated ? 3 : 0) + ','
                    + "\"localized\":" + allLocated + ','
                    + flat
                    + "\"metrics\":{" + nested + "},"
                    + "\"complete_marker\":\"" + COMPLETE + "\"}\n";
            writeRecord(sim, json);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("MRF bad-cell localization failed", failure);
        } finally {
            if (!temporary.isEmpty()) sim.deleteObjects(temporary);
        }
    }

    /**
     * Bracket one side (below or above) of a NON-EMPTY cell set's extent along one axis.
     *
     * The working CellSet is re-selected with the metric threshold and then SUBSET by the axis coordinate at each
     * step (the proven pattern from AuditBadMeshCellBounds): replaceThreshold selects the cells of interest and
     * subsetThreshold intersects them with the axis half-space.  An empty set is never bisected (review-15a: the
     * empty case used to produce an inverted bounding box and a false 'localized' claim).
     */
    private double bracket(FvRepresentation mesh, CellSet cells, FieldFunction metric, double cutoff,
                           FieldFunction axis, double lo, double hi, int mode) {
        double a = lo;
        double b = hi;
        for (int step = 0; step < BISECTIONS; step++) {
            double mid = 0.5 * (a + b);
            cells.replaceThreshold(metric, ThresholdMode.BELOW_TAG.getValue(), cutoff, cutoff);
            cells.subsetThreshold(axis, mode, mid, mid);
            int count = cells.getNumCells();
            if (mode == ThresholdMode.BELOW_TAG.getValue()) {
                if (count > 0) b = mid; else a = mid;
            } else {
                if (count > 0) a = mid; else b = mid;
            }
        }
        return 0.5 * (a + b);
    }

    private double minimum(Simulation sim, FvRepresentation mesh, FieldFunction metric) throws Exception {
        double best = Double.POSITIVE_INFINITY;
        for (Region region : sim.getRegionManager().getRegions()) {
            ScalarReport report = sim.getReportManager().create("star.base.report.MinReport");
            temporary.add(report);
            report.setRepresentation(mesh);
            report.setFieldFunction(metric);
            report.getParts().setObjects(region);
            double value = report.getReportMonitorValue();
            if (!Double.isFinite(value)) throw new IllegalStateException("Nonfinite minimum on " + region.getPresentationName());
            best = Math.min(best, value);
        }
        return best;
    }

    private void appendFlat(StringBuilder flat, String name, double minValue, int worstCount, boolean worstDefined,
                            double[][] worstBox, int cutCount, boolean cutDefined) {
        flat.append('"').append(name).append("_min_value\":").append(repr(minValue)).append(',');
        flat.append('"').append(name).append("_worst_count\":").append(worstCount).append(',');
        flat.append('"').append(name).append("_worst_bbox_defined\":").append(worstDefined).append(',');
        flat.append('"').append(name).append("_worst_bbox_min_x\":").append(repr(worstBox[0][0])).append(',');
        flat.append('"').append(name).append("_worst_bbox_max_x\":").append(repr(worstBox[0][1])).append(',');
        flat.append('"').append(name).append("_below_cutoff_count\":").append(cutCount).append(',');
        flat.append('"').append(name).append("_below_bbox_defined\":").append(cutDefined).append(',');
    }

    private FieldFunction metricField(Simulation sim, FvRepresentation mesh, Class<?> wanted) {
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if (wanted.isInstance(candidate)) return candidate;
        }
        throw new IllegalStateException("Metric field missing: " + wanted.getSimpleName());
    }

    private void writeRecord(Simulation sim, String json) throws Exception {
        try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "mrf_bad_cell_localization.json"),
                StandardCharsets.UTF_8)) {
            out.write(json);
        }
    }

    private static String repr(double value) {
        if (!Double.isFinite(value)) throw new IllegalStateException("non-finite value in record");
        return Double.toString(value);
    }

    private static int valueStart(String json, String key) {
        int i = json.indexOf("\"" + key + "\"");
        if (i < 0) throw new IllegalArgumentException("case_spec.json has no key " + key);
        int colon = json.indexOf(':', i + key.length() + 2);
        if (colon < 0) throw new IllegalArgumentException("case_spec.json key has no value " + key);
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) start++;
        return start;
    }

    private static String object(String json, String key) {
        int start = valueStart(json, key);
        if (start >= json.length() || json.charAt(start) != '{') {
            throw new IllegalArgumentException("case_spec.json key is not an object: " + key);
        }
        int depth = 0;
        for (int i = start; i < json.length(); i++) {
            char ch = json.charAt(i);
            if (ch == '{') depth++;
            else if (ch == '}') {
                depth--;
                if (depth == 0) return json.substring(start + 1, i);
            }
        }
        throw new IllegalArgumentException("unterminated object in case_spec.json: " + key);
    }

    private static double number(String json, String key) {
        int start = valueStart(json, key);
        int end = start;
        while (end < json.length() && "-+0123456789.eE".indexOf(json.charAt(end)) >= 0) end++;
        if (end == start) throw new IllegalArgumentException("case_spec.json value is not a number: " + key);
        double value = Double.parseDouble(json.substring(start, end));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("non-finite " + key);
        return value;
    }
}
