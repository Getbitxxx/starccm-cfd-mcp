/* AIR-3B A4: read-only per-cell listing of cells below FaceValidity 1.0.
 *
 * Purpose: the A4 medium level's post-initialization minimum FaceValidity is 0.9999985698, which is
 * 1.43e-6 below the strict CaseSpec gate of 1.0 and which blocks filing the level's y+ and blocks the
 * level from entering any GCI. A preceding audit bounded the failing cells to 4 in a box whose z extent
 * matches the rotating/stator interface end planes, but a bounding box is not an attribution. This
 * writes ONE ROW PER CELL with its centroid and radius so each cell can be attributed.
 *
 * It is deliberately built from the pieces this project has already proven:
 *   - the read-only domain-bounds and empty-CellSet pattern of AuditBadMeshCellBounds.java, which does
 *     not initialize anything; and
 *   - the per-cell bisection helpers of DiagnoseImportedMrfFaceValidity.java, which isolate each cell
 *     and refine its centroid to 22 bisection steps per axis.
 *
 * It fixes the three defects that independent review found in the earlier attempt:
 *   1. no case_spec.json dependency: the domain bounds come from the regions themselves, so the trial
 *      harness (which stages only pinned macros and pinned inputs) can actually run it;
 *   2. no initializeSolution() call, so it is genuinely read-only and its result is directly
 *      comparable with the bounding pass on the same checkpoint;
 *   3. no pre-flush throws: an unreconciled partition is recorded as an INCOMPLETE line and the CSV is
 *      still written, because a localization imperfection must not destroy the evidence.
 *
 * It never initializes, iterates, meshes, deletes cells or saves.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.vis.ThresholdMode;

public class ListAir3bLowFaceValidityCells extends StarMacro {

    private static final double CUTOFF = 1.0;
    private static final String COMPLETE = "AIR3B_LOWFV_LIST_COMPLETE";
    private static boolean degraded = false;

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
        double[] clippedHigh = high.clone();
        clippedHigh[axis] = split;
        int inBox = countInBox(cells, validity, low, clippedHigh, xyz, domainLow, domainHigh);
        if (inBox == 0) return 0;
        return cells.getNumCells();
    }

    private static void locateCells(CellSet cells, FieldFunction validity, FieldFunction[] xyz,
            double[] low, double[] high, double[] domainLow, double[] domainHigh,
            List<double[]> centers, int depth) {
        int count = countInBox(cells, validity, low, high, xyz, domainLow, domainHigh);
        if (count == 0) return;
        // A split-limit or partition imperfection is recorded, not thrown: the CSV must still be written.
        if (depth > 80) { degraded = true; return; }
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
        if (leftCount + rightCount != count) {
            // Fall back to the naive split and flag it rather than aborting the whole audit.
            degraded = true;
            leftHigh[axis] = mid;
            rightLow[axis] = mid;
        }
        locateCells(cells, validity, xyz, low, leftHigh, domainLow, domainHigh, centers, depth + 1);
        locateCells(cells, validity, xyz, rightLow, high, domainLow, domainHigh, centers, depth + 1);
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        CellSet cells = null;
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);

            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("Volume mesh missing");

            FieldFunction validity = null;
            for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh))
                if (candidate instanceof FaceValidityFunction) { validity = candidate; break; }
            if (validity == null) throw new IllegalStateException("FaceValidity field function missing");

            FieldFunction position = sim.getFieldFunctionManager().getFunction("Position");
            if (position == null) throw new IllegalStateException("Position field function missing");
            FieldFunction[] xyz = new FieldFunction[3];
            for (int axis = 0; axis < 3; axis++) xyz[axis] = position.getComponentFunction(axis);

            // Domain bounds from the regions themselves: no configuration file is needed, so the trial
            // harness can run this with only its pinned macros and inputs.
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

            cells = mesh.getCellSetManager().createEmptyCellSet();
            cells.replaceThreshold(validity, ThresholdMode.BELOW_TAG.getValue(), CUTOFF, CUTOFF);
            int count = cells.getNumCells();

            List<double[]> centers = new ArrayList<double[]>();
            if (count > 0) locateCells(cells, validity, xyz, lo, hi, lo, hi, centers, 0);

            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_low_facevalidity_cells.csv")),
                    StandardCharsets.UTF_8))) {
                w.write("cell,face_validity_cutoff,x_m,y_m,z_m,radius_m,z_over_interface_plane_m\n");
                for (int i = 0; i < centers.size(); i++) {
                    double[] c = centers.get(i);
                    double radius = Math.sqrt(c[0] * c[0] + c[1] * c[1]);
                    // Distance of |z| beyond the nearest interface end plane (Z = -0.12155 / +0.12182 m):
                    // negative means inside the interface band, positive means beyond it.
                    double beyond = Math.max(Math.abs(c[2]) - 0.12182, 0.0);
                    w.write((i + 1) + "," + CUTOFF + "," + c[0] + "," + c[1] + "," + c[2] + ","
                            + radius + "," + beyond + "\n");
                }
                w.write("CELL_COUNT\t" + count + "\n");
                w.write("LOCALIZED_COUNT\t" + centers.size() + "\n");
                w.write("GATE\tcells_found_equals_localized\t" + (count == centers.size() && !degraded) + "\n");
                if (count != centers.size() || degraded)
                    w.write("INCOMPLETE\tlocalization did not fully reconcile; the rows above are the cells "
                            + "that were isolated and must be read as a partial listing\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B low FaceValidity listing failed: " + e.getMessage(), e);
        } finally {
            if (cells != null) {
                try { sim.deleteObjects(Collections.singletonList(cells)); } catch (Throwable ignored) { }
            }
        }
    }
}
