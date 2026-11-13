/* AIR-3B A3: read back the coupled solver's linear-solver settings and optionally tighten them.
 *
 * Segment 1 decayed at 0.9982 per iteration between iterations 300 and 680, with 18 "AMG solve
 * rejected" events, 162 "AMG coarsening halted" events and 160 "insufficient precision on
 * multigrid level 11" warnings. The installed 2502 Help does not document those messages, but its
 * coupled-solver settings page documents the AMG defaults and states that if the AMG
 * preconditioner residual is not decreasing by 1 to 2 orders of magnitude the Courant number
 * should be reduced. The AMG linear solver's convergence tolerance is the setting that directly
 * encodes "how many orders the linear residual must fall", and unlike the Courant option it needs
 * no enumerated-option construction: its setters are plain double and int.
 *
 * It supports TWO levers and sets neither unless asked:
 *   - A2 (primary, and the first experiment): AIR3B_COURANT_CFL pins the coupled solver's Courant
 *     number to a constant instead of letting the controller ramp it until a solve is rejected and
 *     halve it. Review round 17b argued this should go first, and the argument is mechanistic: a
 *     logged AMG rejection means the linear solve failed the criterion it already had, so
 *     tightening that criterion cannot help, whereas the controller's own halving response is
 *     literally the documented remedy.
 *   - A1 (follow-up probe): AIR3B_AMG_TOL_DECADES or AIR3B_AMG_CONVERGE_TOL tightens the linear
 *     tolerance relative to the measured baseline.
 *
 * This macro therefore also does two things and never guesses:
 *   - it ALWAYS records, before touching anything, the coupled solver's Courant option and the
 *     AMG converge tolerance, max cycles, epsilon, cycle type, group size, smoother, acceleration
 *     and partition-boundary sweeps, so the baseline is evidence rather than assumption;
 *   - it applies ONLY the changes named by environment variables, verifies each by readback, and
 *     refuses to run at all when no change is requested, because saving a 700 MB checkpoint to
 *     record a no-op is pure cost. A relative request (AIR3B_AMG_TOL_DECADES) is resolved against
 *     the MEASURED baseline so a no-op is impossible, and a baseline already at or below 1e-3 makes
 *     the macro refuse rather than spend a trial proving the tolerance is not the lever.
 *
 * It initializes nothing, iterates nothing and meshes nothing. It saves only the new checkpoint.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;

public class ConfigureAir3bAmgSolver extends StarMacro {

    private static final String COMPLETE = "AIR3B_AMG_CONFIGURE_COMPLETE";

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private static Double envOpt(String name) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return null;
        return Double.valueOf(v);
    }

    private static String fmt(double v) {
        return String.format(Locale.ROOT, "%.12g", v);
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            File output = new File(dir, envText("AIR3B_SOLVER_OUTPUT", "mrf_solver_tuned.sim"));
            if (output.exists()) throw new IllegalStateException("Solver output already exists");

            Double wantTol = envOpt("AIR3B_AMG_CONVERGE_TOL");
            Double wantDecades = envOpt("AIR3B_AMG_TOL_DECADES");
            Double wantCycles = envOpt("AIR3B_AMG_MAX_CYCLES");
            Double wantEpsilon = envOpt("AIR3B_AMG_EPSILON");
            Double wantCfl = envOpt("AIR3B_COURANT_CFL");
            if (wantTol != null && wantDecades != null)
                throw new IllegalArgumentException("Specify either an absolute tolerance or decades, not both");
            if (wantTol == null && wantDecades == null && wantCycles == null && wantEpsilon == null && wantCfl == null)
                throw new IllegalStateException("No solver change requested; refusing to rewrite a checkpoint");
            if (wantCfl != null && !(wantCfl > 0.0))
                throw new IllegalArgumentException("AIR3B_COURANT_CFL must be positive");
            if (wantTol != null && !(wantTol > 0.0 && wantTol < 1.0))
                throw new IllegalArgumentException("AIR3B_AMG_CONVERGE_TOL must be between 0 and 1");
            if (wantDecades != null && !(wantDecades > 0.0 && wantDecades <= 4.0))
                throw new IllegalArgumentException("AIR3B_AMG_TOL_DECADES must be in (0, 4]");
            if (wantCycles != null && wantCycles < 1)
                throw new IllegalArgumentException("AIR3B_AMG_MAX_CYCLES must be positive");
            if (wantEpsilon != null && !(wantEpsilon >= 0.0))
                throw new IllegalArgumentException("AIR3B_AMG_EPSILON must not be negative");

            int iteration = sim.getSimulationIterator().getCurrentIteration();
            CoupledImplicitSolver solver = sim.getSolverManager().getSolver(CoupledImplicitSolver.class);
            if (solver == null) throw new IllegalStateException("The coupled implicit solver is not present");
            AMGLinearSolver amg = solver.getAMGLinearSolver();
            if (amg == null) throw new IllegalStateException("The coupled solver has no AMG linear solver");

            // A relative target is computed from the MEASURED baseline, because an absolute target
            // cannot be known in advance and a request that happened to equal the baseline would be a
            // silent no-op that still satisfied an equality readback. Review round 17 rejected the
            // absolute-only version for exactly that reason.
            double baselineTol = amg.getConvergeTol();
            double floorTol = 1e-3;
            if (wantDecades != null) {
                if (baselineTol <= floorTol)
                    throw new IllegalStateException("AMG converge tolerance is already " + fmt(baselineTol)
                            + ", at or below the " + fmt(floorTol) + " floor, so the tolerance is not the lever;"
                            + " use the Courant-number experiment instead");
                wantTol = Double.valueOf(baselineTol / Math.pow(10.0, wantDecades.doubleValue()));
            }

            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_amg_settings.tsv")), StandardCharsets.UTF_8))) {
                w.write("ITERATION\t" + iteration + "\n");
                w.write("BEFORE\tcourant_option\t" + solver.getSelectedCourantOption() + "\n");
                w.write("BEFORE\tamg_converge_tol\t" + fmt(amg.getConvergeTol()) + "\n");
                w.write("BEFORE\tamg_max_cycles\t" + amg.getMaxCycles() + "\n");
                w.write("BEFORE\tamg_epsilon\t" + fmt(amg.getEpsilon()) + "\n");
                w.write("BEFORE\tamg_cycle_type\t" + amg.getCycleType() + "\n");
                w.write("BEFORE\tamg_group_size\t" + amg.getGroupSize() + "\n");
                w.write("BEFORE\tamg_smoother\t" + amg.getSelectedSmootherOption() + "\n");
                w.write("BEFORE\tamg_acceleration\t" + amg.getSelectedAccelerationOption() + "\n");
                w.write("BEFORE\tamg_partition_boundary_sweeps\t" + amg.getPartitionBoundarySweeps() + "\n");
                CourantNumberController courant = solver.getCourantNumberController();
                double baselineCfl = courant.getCFL();
                w.write("BEFORE\tcourant_cfl\t" + fmt(baselineCfl) + "\n");

                if (wantTol != null) amg.setConvergeTol(wantTol.doubleValue());
                if (wantCycles != null) amg.setMaxCycles(wantCycles.intValue());
                if (wantEpsilon != null) amg.setEpsilon(wantEpsilon.doubleValue());

                w.write("AFTER\tamg_converge_tol\t" + fmt(amg.getConvergeTol()) + "\n");
                w.write("AFTER\tamg_max_cycles\t" + amg.getMaxCycles() + "\n");
                w.write("AFTER\tamg_epsilon\t" + fmt(amg.getEpsilon()) + "\n");

                // Experiment A2: cap the Courant number at a constant instead of letting the coupled
                // controller ramp it until an AMG solve is rejected and halve it. Review round 17b
                // argued this should be the FIRST lever, because a rejection means the linear solve
                // failed the criterion it already had, so tightening that criterion cannot help,
                // whereas the controller's own halving response is the documented remedy.
                boolean courantApplied = true;
                if (wantCfl != null) {
                    courant.setSelectedCourantOption(new EnumeratedOptionInput(
                            courant.getCourantNumberControlOption(),
                            CourantNumberControlOption.Type.CONSTANT_CFL));
                    ConstantCourantNumberControl constant =
                            (ConstantCourantNumberControl) courant.getCourantNumberControl();
                    constant.setCFL(wantCfl.doubleValue());
                    double readback = constant.getCFL();
                    courantApplied = Math.abs(readback - wantCfl.doubleValue())
                            <= 1e-9 * Math.max(1.0, wantCfl.doubleValue());
                    w.write("AFTER\tcourant_option\t" + courant.getSelectedCourantOption() + "\n");
                    w.write("AFTER\tcourant_cfl\t" + fmt(readback) + "\n");
                    w.write("BASELINE\tcourant_cfl\t" + fmt(baselineCfl) + "\n");
                    w.write("REQUESTED\tcourant_cfl\t" + fmt(wantCfl) + "\n");
                }

                boolean applied = true;
                if (wantTol != null && amg.getConvergeTol() != wantTol.doubleValue()) applied = false;
                if (wantTol != null && !(amg.getConvergeTol() < baselineTol)) applied = false;
                if (!courantApplied) applied = false;
                if (wantCycles != null && amg.getMaxCycles() != wantCycles.intValue()) applied = false;
                if (wantEpsilon != null && amg.getEpsilon() != wantEpsilon.doubleValue()) applied = false;
                w.write("GATE\tcourant_change_applied\t" + courantApplied + "\n");
                w.write("GATE\tstrictly_tighter_than_baseline\t"
                        + (wantTol == null || amg.getConvergeTol() < baselineTol) + "\n");
                w.write("GATE\trequested_change_applied\t" + applied + "\n");
                if (!applied)
                    throw new IllegalStateException("A requested AMG change did not take effect on readback");
                w.write("REQUESTED\tamg_converge_tol\t" + (wantTol == null ? "unchanged" : fmt(wantTol)) + "\n");
                w.write("REQUESTED\tamg_tol_decades\t"
                        + (wantDecades == null ? "none" : fmt(wantDecades)) + "\n");
                w.write("BASELINE\tamg_converge_tol\t" + fmt(baselineTol) + "\n");
                w.write("REQUESTED\tamg_max_cycles\t" + (wantCycles == null ? "unchanged" : wantCycles.toString()) + "\n");
                w.write("REQUESTED\tamg_epsilon\t" + (wantEpsilon == null ? "unchanged" : fmt(wantEpsilon)) + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            sim.saveState(output.getAbsolutePath());
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B AMG configure failed: " + e.getMessage(), e);
        }
    }
}
