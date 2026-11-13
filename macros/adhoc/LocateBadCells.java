/* Read-only diagnostic: turn the "50 bad cells" bracket into tight clusters.
 *
 * The STAR Java API does not expose per-cell arrays through CellSet/FieldFunction (CellSet offers
 * only threshold/range/index; FieldFunction only getValue), so the bad cells are located by recursive
 * bisection on the existing count primitive: split the current box along its longest axis, keep the
 * halves that still contain cells below the cutoff, stop at a minimum box size. The output is a list
 * of tight boxes with their cell counts - enough to attach the failure to a named feature
 * (trailing edge, cove, wing-body junction, tip), which the bounding bracket alone could not.
 *
 * Read-only: no initialisation, no mesh change, no save.
 */
import java.io.File;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import star.base.neo.*;
import star.common.*;
import star.vis.ThresholdMode;

public class LocateBadCells extends StarMacro {

    private static final double MIN_BOX_M = 0.004;   // 4 mm: finer than the body surface size (5 mm)
    private static final int MAX_LEAVES = 4000;

    private CellSet cells;
    private FieldFunction metric;
    private FieldFunction position;
    private double cutoff;
    private final List<String> leaves = new ArrayList<String>();
    private final List<double[]> leafBoxes = new ArrayList<double[]>();
    private FieldFunction prismLayers;
    private int calls = 0;

    private int countBelow(double[] box) {
        calls++;
        cells.replaceThreshold(metric, ThresholdMode.BELOW_TAG.getValue(), cutoff, cutoff);
        for (int a = 0; a < 3; a++) {
            FieldFunction axis = position.getComponentFunction(a);
            cells.subsetThreshold(axis, ThresholdMode.ABOVE_TAG.getValue(), box[a], box[a]);
            cells.subsetThreshold(axis, ThresholdMode.BELOW_TAG.getValue(), box[a + 3], box[a + 3]);
        }
        return cells.getNumCells();
    }

    private void split(double[] box, int[] n, int depth) {
        if (leaves.size() >= MAX_LEAVES) return;
        double dx = box[3] - box[0], dy = box[4] - box[1], dz = box[5] - box[2];
        double longest = Math.max(dx, Math.max(dy, dz));
        if (longest <= MIN_BOX_M || depth >= 24) {
            leaves.add(fmt(box) + "\t" + n[0]);
            leafBoxes.add(box.clone());
            return;
        }
        int axis = (longest == dx) ? 0 : (longest == dy) ? 1 : 2;
        double mid = 0.5 * (box[axis] + box[axis + 3]);
        double[] a = box.clone(), b = box.clone();
        a[axis + 3] = mid;
        b[axis] = mid;
        int na = countBelow(a);
        if (na > 0) split(a, new int[] {na}, depth + 1);
        int nb = countBelow(b);
        if (nb > 0) split(b, new int[] {nb}, depth + 1);
    }

    private static String fmt(double[] box) {
        return String.format(java.util.Locale.ROOT, "%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f",
                box[0], box[1], box[2], box[3], box[4], box[5]);
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try (PrintWriter out = new PrintWriter(new File(sim.getSessionDirFile(), "bad_cell_clusters.tsv"),
                StandardCharsets.UTF_8)) {
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("Volume mesh missing");
            position = sim.getFieldFunctionManager().getFunction("Position");
            if (position == null) throw new IllegalStateException("Position missing");
            for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh))
                if (candidate instanceof VolumeChangeFunction) { metric = candidate; break; }
            if (metric == null) throw new IllegalStateException("VolumeChange missing");
            for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
                String name = candidate.getPresentationName();
                if ("PrismLayerNumber".equals(name) || "Prism Layer Number".equals(name)) { prismLayers = candidate; break; }
            }
            // Cutoff defaults to the diagnostic 0.01 but can be pinned to the actual gate by a cutoff.txt
            // next to the session (0.005 for the VolumeChange gate), so the waiver record can state how many
            // cells are below the REAL threshold rather than below a looser diagnostic one.
            cutoff = Math.nextUp(0.01);
            File cutoffFile = new File(sim.getSessionDirFile(), "cutoff.txt");
            if (cutoffFile.isFile()) {
                String text = new String(Files.readAllBytes(cutoffFile.toPath()), StandardCharsets.UTF_8).trim();
                cutoff = Math.nextUp(Double.parseDouble(text));
            }
            out.println("# cutoff_source=" + (cutoffFile.isFile() ? cutoffFile.getName() : "default_0.01"));

            double[] lo = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY};
            double[] hi = {Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
            for (Region r : sim.getRegionManager().getRegions()) {
                double[] ext = sim.getMeshManager().getRegionExtents(r);
                if (ext.length != 6) throw new IllegalStateException("Missing region extents");
                for (int i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], ext[2 * i]); hi[i] = Math.max(hi[i], ext[2 * i + 1]); }
            }
            double[] root = {lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]};
            cells = mesh.getCellSetManager().createEmptyCellSet();
            int total = countBelow(root);
            out.println("# metric=VolumeChange cutoff=" + cutoff + " total_below=" + total);
            out.println("xmin\tymin\tzmin\txmax\tymax\tzmax\tcount\tno_prism\tlayers_1_2\tlayers_3plus");
            if (total > 0) split(root, new int[] {total}, 0);
            for (int i = 0; i < leaves.size(); i++) {
                double[] box = leafBoxes.get(i);
                int noPrism = 0, few = 0, many = 0;
                if (prismLayers != null) {
                    int n = countBelow(box);
                    cells.subsetThreshold(prismLayers, ThresholdMode.BELOW_TAG.getValue(), 0.5, 0.5);
                    noPrism = cells.getNumCells();
                    int left = n - noPrism;
                    cells.replaceThreshold(metric, ThresholdMode.BELOW_TAG.getValue(), cutoff, cutoff);
                    for (int a = 0; a < 3; a++) {
                        FieldFunction axis = position.getComponentFunction(a);
                        cells.subsetThreshold(axis, ThresholdMode.ABOVE_TAG.getValue(), box[a], box[a]);
                        cells.subsetThreshold(axis, ThresholdMode.BELOW_TAG.getValue(), box[a + 3], box[a + 3]);
                    }
                    cells.subsetThreshold(prismLayers, ThresholdMode.ABOVE_TAG.getValue(), 0.5, 0.5);
                    cells.subsetThreshold(prismLayers, ThresholdMode.BELOW_TAG.getValue(), 2.5, 2.5);
                    few = cells.getNumCells();
                    many = left - few;
                }
                out.println(leaves.get(i) + "\t" + noPrism + "\t" + few + "\t" + many);
            }
            out.println("# calls=" + calls + " leaves=" + leaves.size());
            System.out.println("BAD_CELL_CLUSTERS_COMPLETE\ttotal=" + total + "\tcalls=" + calls
                    + "\tleaves=" + leaves.size());
            sim.deleteObjects(Collections.singletonList(cells));
            cells = null;
        } catch (Exception e) {
            throw new RuntimeException("Bad cell clustering failed: " + e.getMessage(), e);
        } finally {
            if (cells != null) sim.deleteObjects(Collections.singletonList(cells));
        }
    }
}
