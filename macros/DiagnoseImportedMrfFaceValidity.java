/* Isolated, read-only localization of initialized interface cells below Face Validity 1. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.Collections;
import java.util.ArrayList;
import java.util.List;
import java.util.Vector;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.NeoProperty;
import star.base.neo.NeoObjectVector;
import star.base.neo.DoubleVector;
import star.common.FaceValidityFunction;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Dimensions;
import star.common.CellSet;
import star.common.CoordinateSystem;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.Units;
import star.vis.ThresholdMode;
import star.vis.ThresholdPart;

public class DiagnoseImportedMrfFaceValidity extends StarMacro {
    private static final double CUTOFF = 1.0;
    private static String field(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }

    private static int countInBox(CellSet cells, FieldFunction validity, double[] low,
            double[] high, FieldFunction[] xyz, double[] domainLow, double[] domainHigh) {
        cells.replaceThreshold(validity, ThresholdMode.BELOW_TAG.getValue(), CUTOFF, CUTOFF);
        for (int axis = 0; axis < 3; axis++) {
            if (low[axis] > domainLow[axis])
                cells.subsetThreshold(xyz[axis], ThresholdMode.ABOVE_TAG.getValue(), low[axis], low[axis]);
            if (high[axis] < domainHigh[axis])
                cells.subsetThreshold(xyz[axis], ThresholdMode.BELOW_TAG.getValue(), high[axis], high[axis]);
        }
        return cells.getNumCells();
    }

    private static int countLowHalf(CellSet cells, FieldFunction validity, FieldFunction coordinate,
            double[] low, double[] high, int axis, double split, FieldFunction[] xyz,
            double[] domainLow, double[] domainHigh) {
        double[] clippedHigh = high.clone(); clippedHigh[axis] = split;
        int inBox = countInBox(cells, validity, low, clippedHigh, xyz, domainLow, domainHigh);
        if (inBox == 0) return 0;
        return cells.getNumCells();
    }

    private static void locateCells(CellSet cells, FieldFunction validity, FieldFunction[] xyz,
            double[] low, double[] high, double[] domainLow, double[] domainHigh,
            List<double[]> centers, int depth) {
        int count = countInBox(cells, validity, low, high, xyz, domainLow, domainHigh);
        if (count == 0) return;
        if (depth > 80) throw new IllegalStateException("Cell localization exceeded spatial split limit");
        if (count == 1) {
            double[] a = low.clone(), b = high.clone();
            for (int axis = 0; axis < 3; axis++) {
                for (int step = 0; step < 22; step++) {
                    double mid = 0.5 * (a[axis] + b[axis]);
                    if (countLowHalf(cells, validity, xyz[axis], a, b, axis, mid,
                            xyz, domainLow, domainHigh) == 1)
                        b[axis] = mid;
                    else a[axis] = mid;
                }
            }
            centers.add(new double[] {0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]), 0.5 * (a[2] + b[2])});
            return;
        }
        int axis = 0;
        for (int i = 1; i < 3; i++) if (high[i] - low[i] > high[axis] - low[axis]) axis = i;
        double mid = 0.5 * (low[axis] + high[axis]);
        double[] leftHigh = high.clone(); leftHigh[axis] = mid;
        double[] rightLow = low.clone(); rightLow[axis] = mid;
        int leftCount = countInBox(cells, validity, low, leftHigh, xyz, domainLow, domainHigh);
        int rightCount = countInBox(cells, validity, rightLow, high, xyz, domainLow, domainHigh);
        if (leftCount + rightCount != count) {
            double epsilon = Math.max(1.0e-12, (high[axis] - low[axis]) * 1.0e-10);
            leftHigh[axis] = mid - epsilon;
            rightLow[axis] = mid + epsilon;
            leftCount = countInBox(cells, validity, low, leftHigh, xyz, domainLow, domainHigh);
            rightCount = countInBox(cells, validity, rightLow, high, xyz, domainLow, domainHigh);
        }
        if (leftCount + rightCount != count)
            throw new IllegalStateException("Spatial partition did not account for all bad cells");
        locateCells(cells, validity, xyz, low, leftHigh, domainLow, domainHigh, centers, depth + 1);
        locateCells(cells, validity, xyz, rightLow, high, domainLow, domainHigh, centers, depth + 1);
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        ThresholdPart selected = null;
        ThresholdPart control = null;
        CellSet badCells = null;
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            Region region = sim.getRegionManager().getRegion(field(spec, "mesh_region_name"));
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (region == null || mesh == null || !mesh.describesRegion(region)) {
                throw new IllegalStateException("Selected Region or Volume Mesh missing");
            }
            FieldFunction validity = null;
            for (FieldFunction function : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
                if (function instanceof FaceValidityFunction) { validity = function; break; }
            }
            if (validity == null) throw new IllegalStateException("Face Validity field missing");
            sim.getSolution().initializeSolution();
            Units dimensionless = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().build());
            selected = sim.getPartManager().createThresholdPart(
                    new NeoObjectVector(new Object[] {region}),
                    new DoubleVector(new double[] {CUTOFF, CUTOFF}),
                    dimensionless, validity, ThresholdMode.BELOW_TAG.getValue());
            selected.setPresentationName("TEMP_MRF_LOW_FACE_VALIDITY");
            int estimated = selected.getEstimatedNumberOfCells();
            control = sim.getPartManager().createThresholdPart(
                    new NeoObjectVector(new Object[] {region}),
                    new DoubleVector(new double[] {0.0, 1.01}),
                    dimensionless, validity, ThresholdMode.BETWEEN_TAG.getValue());
            int controlEstimated = control.getEstimatedNumberOfCells();
            badCells = mesh.getCellSetManager().createEmptyCellSet();
            badCells.replaceThreshold(validity, ThresholdMode.BELOW_TAG.getValue(),
                    CUTOFF, CUTOFF);
            int cellSetCount = badCells.getNumCells();
            double[] regionExtents = sim.getMeshManager().getRegionExtents(region);
            Units lengthUnits = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
            CoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
            Vector<Double> broadMin = new Vector<>(Arrays.asList(-1000.0, -1000.0, -1000.0));
            Vector<Double> broadMax = new Vector<>(Arrays.asList(1000.0, 1000.0, 1000.0));
            badCells.subsetRange(broadMin, broadMax,
                    Arrays.asList(lengthUnits, lengthUnits, lengthUnits),
                    Arrays.asList(lengthUnits, lengthUnits, lengthUnits), lab);
            int broadRangeCount = badCells.getNumCells();
            double[] lower = new double[3];
            double[] upper = new double[3];
            int localizedCount = -1;
            List<double[]> localizedCenters = new ArrayList<>();
            if (cellSetCount > 0 && regionExtents.length == 6) {
                FieldFunction position = sim.getFieldFunctionManager().getFunction("Position");
                FieldFunction[] xyz = new FieldFunction[] {position.getComponentFunction(0),
                        position.getComponentFunction(1), position.getComponentFunction(2)};
                double[] low = new double[3], high = new double[3];
                for (int axis = 0; axis < 3; axis++) {
                    low[axis] = regionExtents[2 * axis] - 1.0e-6;
                    high[axis] = regionExtents[2 * axis + 1] + 1.0e-6;
                }
                locateCells(badCells, validity, xyz, low, high,
                        new double[] {regionExtents[0], regionExtents[2], regionExtents[4]},
                        new double[] {regionExtents[1], regionExtents[3], regionExtents[5]},
                        localizedCenters, 0);
                localizedCount = localizedCenters.size();
                if (localizedCount != cellSetCount)
                    throw new IllegalStateException("Localized cell count differs from CellSet total");
                if (!localizedCenters.isEmpty()) {
                    lower = localizedCenters.get(0).clone();
                    upper = localizedCenters.get(0).clone();
                }
            }
            NeoProperty preview = sim.getMeshManager().previewInvalidCells(
                    Collections.singletonList(region), 1.0, 1.0e-5, 0.01, 0, 0.0, 0.0);
            String export = "skipped";
            try {
                File csv = new File(dir, "mrf_low_face_validity_cells.csv");
                selected.exportDataSourceCSV(mesh, false, csv.getAbsolutePath(), validity);
                export = csv.isFile() ? "written:bytes=" + csv.length() : "missing";
            } catch (Throwable failure) {
                export = "failed: " + failure.getMessage();
            }
            try (FileWriter out = new FileWriter(new File(dir, "mrf_low_face_validity.txt"), StandardCharsets.UTF_8)) {
                out.write("region=" + region.getPresentationName() + "\nmetric=FaceValidity\nmode=BELOW\ncutoff=" + CUTOFF + "\n");
                out.write("field=" + validity.getPresentationName() + "\n");
                out.write("selected_is_volume=" + selected.isVolume() + "\n");
                out.write("selected_input_parts=" + selected.getInputPartsCollection() + "\n");
                out.write("selected_mode=" + selected.getModeEnum() + "\n");
                out.write("selected_range=" + Arrays.toString(selected.getRange()) + "\n");
                out.write("selected_range_query=" + selected.getRangeQuery() + "\n");
                out.write("control_between_0_and_1_01_cells=" + controlEstimated + "\n");
                out.write("cell_set_below_cutoff_cells=" + cellSetCount + "\n");
                out.write("region_extents=" + Arrays.toString(regionExtents) + "\n");
                out.write("cell_set_broad_range_cells=" + broadRangeCount + "\n");
                out.write("cell_set_localization_lower_m=" + Arrays.toString(lower) + "\n");
                out.write("cell_set_localization_upper_m=" + Arrays.toString(upper) + "\n");
                out.write("cell_set_localization_final_count=" + localizedCount + "\n");
                out.write("localized_cell_centers_m=" + localizedCenters.stream()
                        .map(Arrays::toString).reduce((a, b) -> a + ";" + b).orElse("") + "\n");
                out.write("estimated_cells=" + estimated + "\nexport=" + export + "\n");
                out.write("preview_invalid_cells=" + preview.toFormattedString() + "\n");
            }
            System.out.println("MRF_LOW_FACE_VALIDITY_DIAGNOSTIC_COMPLETE");
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(dir, "mrf_low_face_validity_failure.txt"), StandardCharsets.UTF_8)) {
                StringWriter trace = new StringWriter();
                failure.printStackTrace(new PrintWriter(trace));
                out.write(trace.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Imported MRF Face Validity diagnostic failed", failure);
        } finally {
            if (selected != null) sim.deleteObjects(Collections.singletonList(selected));
            if (control != null) sim.deleteObjects(Collections.singletonList(control));
            if (badCells != null) sim.deleteObjects(Collections.singletonList(badCells));
        }
    }
}
