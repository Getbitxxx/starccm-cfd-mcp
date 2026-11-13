/* Generic MRF solver-object inventory (P2.3 G5 part9b): READ-ONLY baseline of the coupled solver's
 * Courant-number controller and AMG linear solver, read from the SOLVER OBJECTS (not the continuum
 * models, which was part9's fail-closed NO-BASELINE finding).
 *
 * Proven API (executed/reviewed AIR-3B macro ConfigureAir3bAmgSolver on this install):
 *   CoupledImplicitSolver solver = sim.getSolverManager().getSolver(CoupledImplicitSolver.class);
 *   AMGLinearSolver amg = solver.getAMGLinearSolver();
 *   CourantNumberController courant = solver.getCourantNumberController();
 * with getSelectedCourantOption()/getCFL() and AMG getConvergeTol/getMaxCycles/getEpsilon/getCycleType/
 * getGroupSize/getSelectedSmootherOption/getSelectedAccelerationOption/getPartitionBoundarySweeps.
 *
 * No initialization, no iteration, no meshing, no save; no setters. This is the baseline that the
 * part10 single-change solver experiment will be registered against (separate reviewed
 * solver_experiment.json; the frozen CaseSpec is not edited).
 */
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;

public class InspectMrfSolverObjects extends StarMacro {

    private static final String COMPLETE = "MRF_SOLVER_OBJECTS_INSPECTED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            boolean solutionLoaded = iteration > 0;

            CoupledImplicitSolver solver = sim.getSolverManager().getSolver(CoupledImplicitSolver.class);
            if (solver == null) throw new IllegalStateException("Coupled implicit solver not present");
            AMGLinearSolver amg = solver.getAMGLinearSolver();
            if (amg == null) throw new IllegalStateException("Coupled solver has no AMG linear solver");
            CourantNumberController courant = solver.getCourantNumberController();
            if (courant == null) throw new IllegalStateException("Coupled solver has no Courant-number controller");

            Map<String, String> values = new LinkedHashMap<String, String>();
            values.put("solver_class", solver.getClass().getName());
            values.put("courant_option", String.valueOf(solver.getSelectedCourantOption()));
            values.put("courant_cfl", String.valueOf(courant.getCFL()));
            values.put("courant_control_class", courant.getClass().getName());
            values.put("amg_converge_tol", String.valueOf(amg.getConvergeTol()));
            values.put("amg_max_cycles", String.valueOf(amg.getMaxCycles()));
            values.put("amg_epsilon", String.valueOf(amg.getEpsilon()));
            values.put("amg_cycle_type", String.valueOf(amg.getCycleType()));
            values.put("amg_group_size", String.valueOf(amg.getGroupSize()));
            values.put("amg_smoother", String.valueOf(amg.getSelectedSmootherOption()));
            values.put("amg_acceleration", String.valueOf(amg.getSelectedAccelerationOption()));
            values.put("amg_partition_boundary_sweeps", String.valueOf(amg.getPartitionBoundarySweeps()));
            values.put("amg_class", amg.getClass().getName());

            // Field-level validation: every baseline field must be usable before part10 registers the 0.5x rule.
            boolean validCfl = positive(values.get("courant_cfl"));
            boolean validOption = nonEmpty(values.get("courant_option"));
            boolean validMaxCycles = positive(values.get("amg_max_cycles"));
            boolean validEpsilon = finiteAtLeastZero(values.get("amg_epsilon"));
            boolean validCycleType = nonEmpty(values.get("amg_cycle_type"));
            boolean validGroupSize = positive(values.get("amg_group_size"));
            boolean validSmoother = nonEmpty(values.get("amg_smoother"));
            boolean validAcceleration = nonEmpty(values.get("amg_acceleration"));
            boolean validPartitionSweeps = finiteAtLeastZero(values.get("amg_partition_boundary_sweeps"));
            boolean validTolerance = positive(values.get("amg_converge_tol"));
            boolean finiteCfl = validCfl && validOption;
            boolean finiteTol = validTolerance;
            boolean amgMeasured = validTolerance && validMaxCycles && validEpsilon && validCycleType
                    && validGroupSize && validSmoother && validAcceleration && validPartitionSweeps;
            boolean measured = finiteCfl && amgMeasured;
            StringBuilder validation = new StringBuilder();
            validation.append("courant_cfl=").append(validCfl)
                    .append(",courant_option=").append(validOption)
                    .append(",amg_converge_tol=").append(validTolerance)
                    .append(",amg_max_cycles=").append(validMaxCycles)
                    .append(",amg_epsilon=").append(validEpsilon)
                    .append(",amg_cycle_type=").append(validCycleType)
                    .append(",amg_group_size=").append(validGroupSize)
                    .append(",amg_smoother=").append(validSmoother)
                    .append(",amg_acceleration=").append(validAcceleration)
                    .append(",amg_partition_boundary_sweeps=").append(validPartitionSweeps);

            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"inspector\":\"InspectMrfSolverObjects.java\",")
                .append("\"read_only\":true,\"iterations\":").append(iteration)
                .append(",\"solution_loaded\":").append(solutionLoaded)
                .append(",\"cfl_measured\":").append(finiteCfl)
                .append(",\"amg_measured\":").append(amgMeasured)
                .append(",\"field_validation\":\"").append(esc(validation.toString())).append('"')
                .append(",\"baseline_available\":").append(measured)
                .append(",\"values\":{");
            boolean first = true;
            for (Map.Entry<String, String> entry : values.entrySet()) {
                if (!first) json.append(',');
                first = false;
                json.append('"').append(esc(entry.getKey())).append("\":\"")
                    .append(esc(entry.getValue())).append('"');
            }
            json.append("},\"note\":\"read-only solver-object baseline: CoupledImplicitSolver/AMGLinearSolver/CourantNumberController ")
                .append("read via the proven SolverManager API; no setter is called, nothing is initialized, iterated or saved. ")
                .append("This is the measured baseline for the separately reviewed solver_experiment.json (do not edit the frozen CaseSpec).\"}\n");
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_objects.json")), StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            try (Writer tsv = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_objects.tsv")), StandardCharsets.UTF_8)) {
                tsv.write("SETTING\tVALUE\n");
                for (Map.Entry<String, String> entry : values.entrySet()) {
                    tsv.write(entry.getKey() + "\t" + entry.getValue() + "\n");
                }
                tsv.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_objects_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF solver-object inspection failed", failure);
        }
    }

    private static boolean nonEmpty(String value) {
        return value != null && !value.trim().isEmpty() && !"null".equalsIgnoreCase(value.trim());
    }

    private static boolean positive(String value) {
        try {
            double v = Double.parseDouble(value);
            return Double.isFinite(v) && v > 0;
        } catch (RuntimeException notANumber) { return false; }
    }

    private static boolean finiteAtLeastZero(String value) {
        try { double v = Double.parseDouble(value); return Double.isFinite(v) && v >= 0; }
        catch (RuntimeException notANumber) { return false; }
    }

    private static boolean isFinite(String value) {
        try {
            return Double.isFinite(Double.parseDouble(value));
        } catch (RuntimeException notANumber) {
            return false;
        }
    }

    private static String read(File file) throws Exception {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
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
