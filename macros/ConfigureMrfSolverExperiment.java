/* Generic MRF one-change solver experiment (P2.3 G5 part10): apply EXACTLY ONE declared solver change to a
 * solved-state checkpoint, with full BEFORE/AFTER readback and fail-closed drift/no-op guards.
 *
 * Contract (reviewed design review-4j): the experiment is described by a SEPARATE, hash-pinned
 * solver_experiment.json (the frozen/signed CaseSpec is not edited):
 *   schema_version, experiment_id,
 *   single_change {parameter:"courant_cfl", mode:"CONSTANT_CFL", rule, measured_baseline_CFL, target_CFL},
 *   basis, expected_before {courant_option, courant_cfl, amg_converge_tol, amg_max_cycles},
 *   expected_after {courant_option, courant_cfl}, unchanged_fields[8 AMG fields].
 *
 * The macro reads ALL TEN solver fields BEFORE, verifies expected_before, FAILS CLOSED if the current CFL
 * differs from the frozen measured_baseline_CFL (it never re-derives the 0.5x rule), rejects a no-op or an
 * already-configured target, applies ONLY the frozen change (select CONSTANT_CFL + ConstantCourantNumberControl),
 * reads AFTER, requires the requested equality and that every undeclared AMG field is unchanged, saves only the
 * configured sim and writes mrf_solver_experiment.json. It does NOT initialize, iterate or solve.
 */
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;

public class ConfigureMrfSolverExperiment extends StarMacro {

    private static final String COMPLETE = "MRF_SOLVER_EXPERIMENT_CONFIGURED";
    private static final String[] AMG_FIELDS = {"amg_converge_tol", "amg_max_cycles", "amg_epsilon",
        "amg_cycle_type", "amg_group_size", "amg_smoother", "amg_acceleration", "amg_partition_boundary_sweeps"};

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "solver_experiment.json"));
            String change = object(spec, "single_change");
            String expectedBefore = object(spec, "expected_before");
            String expectedAfter = object(spec, "expected_after");
            String experimentId = string(spec, "experiment_id");
            String parameter = string(change, "parameter");
            String mode = string(change, "mode");
            double frozenBaseline = number(change, "measured_baseline_CFL");
            double targetCfl = number(change, "target_CFL");
            if (!"courant_cfl".equals(parameter)) throw new IllegalStateException("Only the reviewed courant_cfl experiment is allowed");
            if (!"CONSTANT_CFL".equals(mode)) throw new IllegalStateException("Only the reviewed CONSTANT_CFL mode is allowed");
            if (!(Math.abs(targetCfl - 0.5 * frozenBaseline) <= 1e-12 * Math.abs(frozenBaseline))) {
                throw new IllegalStateException("Frozen target is not 0.5 x the frozen measured baseline");
            }

            CoupledImplicitSolver solver = sim.getSolverManager().getSolver(CoupledImplicitSolver.class);
            if (solver == null) throw new IllegalStateException("Coupled implicit solver not present");
            AMGLinearSolver amg = solver.getAMGLinearSolver();
            CourantNumberController courant = solver.getCourantNumberController();
            if (amg == null || courant == null) throw new IllegalStateException("AMG solver or Courant controller missing");

            Map<String, String> before = new LinkedHashMap<String, String>();
            before.put("courant_option", String.valueOf(solver.getSelectedCourantOption()));
            before.put("courant_cfl", String.valueOf(courant.getCFL()));
            before.put("amg_converge_tol", String.valueOf(amg.getConvergeTol()));
            before.put("amg_max_cycles", String.valueOf(amg.getMaxCycles()));
            before.put("amg_epsilon", String.valueOf(amg.getEpsilon()));
            before.put("amg_cycle_type", String.valueOf(amg.getCycleType()));
            before.put("amg_group_size", String.valueOf(amg.getGroupSize()));
            before.put("amg_smoother", String.valueOf(amg.getSelectedSmootherOption()));
            before.put("amg_acceleration", String.valueOf(amg.getSelectedAccelerationOption()));
            before.put("amg_partition_boundary_sweeps", String.valueOf(amg.getPartitionBoundarySweeps()));

            String expectedBeforeOption = string(expectedBefore, "courant_option");
            String measuredBeforeOption = before.get("courant_option");
            boolean optionMatched = measuredBeforeOption != null
                    && measuredBeforeOption.toLowerCase(Locale.ROOT)
                            .contains(expectedBeforeOption.toLowerCase(Locale.ROOT));
            require(optionMatched, "Measured courant_option does not match expected_before: expected="
                    + expectedBeforeOption + " measured=" + measuredBeforeOption);
            double currentCfl = courant.getCFL();
            require(Math.abs(currentCfl - frozenBaseline) <= 1e-12 * Math.max(1.0, Math.abs(frozenBaseline)),
                    "Current CFL drifted from the frozen measured baseline; fail closed instead of re-scaling");
            require(close(amg.getConvergeTol(), number(expectedBefore, "amg_converge_tol"), 1e-12),
                    "AMG converge tolerance differs from expected_before");
            require(amg.getMaxCycles() == (int) number(expectedBefore, "amg_max_cycles"),
                    "AMG max cycles differs from expected_before");
            boolean alreadyConstant = String.valueOf(solver.getSelectedCourantOption())
                    .toLowerCase(Locale.ROOT).contains("constant");
            if (alreadyConstant && Math.abs(currentCfl - targetCfl) <= 1e-12 * Math.max(1.0, targetCfl)) {
                throw new IllegalStateException("Experiment is a no-op: the target CFL is already configured");
            }

            int iteration = sim.getSimulationIterator().getCurrentIteration();
            courant.setSelectedCourantOption(new EnumeratedOptionInput(
                    courant.getCourantNumberControlOption(), CourantNumberControlOption.Type.CONSTANT_CFL));
            ConstantCourantNumberControl constant = (ConstantCourantNumberControl) courant.getCourantNumberControl();
            constant.setCFL(targetCfl);

            Map<String, String> after = new LinkedHashMap<String, String>();
            after.put("courant_option", String.valueOf(solver.getSelectedCourantOption()));
            after.put("courant_cfl", String.valueOf(courant.getCFL()));
            after.put("amg_converge_tol", String.valueOf(amg.getConvergeTol()));
            after.put("amg_max_cycles", String.valueOf(amg.getMaxCycles()));
            after.put("amg_epsilon", String.valueOf(amg.getEpsilon()));
            after.put("amg_cycle_type", String.valueOf(amg.getCycleType()));
            after.put("amg_group_size", String.valueOf(amg.getGroupSize()));
            after.put("amg_smoother", String.valueOf(amg.getSelectedSmootherOption()));
            after.put("amg_acceleration", String.valueOf(amg.getSelectedAccelerationOption()));
            after.put("amg_partition_boundary_sweeps", String.valueOf(amg.getPartitionBoundarySweeps()));

            require(Math.abs(courant.getCFL() - targetCfl) <= 1e-9 * Math.max(1.0, targetCfl),
                    "Configured CFL does not read back as the requested target");
            String expectedAfterOption = string(expectedAfter, "courant_option");
            // The selected-option string is LOCALIZED on this install (measured 2026-10-28: after setting
            // CONSTANT_CFL the localized name did not contain the English token), so CONSTANT_CFL is verified
            // through the CONTROL CLASS; other mode labels keep the localized-string path.
            String afterControlClass = courant.getCourantNumberControl().getClass().getName();
            boolean afterOptionVerified;
            if ("CONSTANT_CFL".equals(expectedAfterOption)) {
                afterOptionVerified = courant.getCourantNumberControl() instanceof ConstantCourantNumberControl;
            } else {
                afterOptionVerified = String.valueOf(solver.getSelectedCourantOption()).toLowerCase(Locale.ROOT)
                        .contains(expectedAfterOption.toLowerCase(Locale.ROOT));
            }
            require(afterOptionVerified, "Configured Courant mode does not match expected_after: " + expectedAfterOption
                    + " (control class " + afterControlClass + ")");
            List<String> changed = new ArrayList<String>();
            for (Map.Entry<String, String> entry : before.entrySet()) {
                String afterValue = after.get(entry.getKey());
                if (afterValue != null && !afterValue.equals(entry.getValue())) {
                    changed.add(entry.getKey());
                }
            }
            for (String field : AMG_FIELDS) {
                require(before.get(field).equals(after.get(field)), "Undeclared AMG field changed: " + field);
            }
            boolean beforeVerified = optionMatched
                    && close(amg.getConvergeTol(), number(expectedBefore, "amg_converge_tol"), 1e-12)
                    && amg.getMaxCycles() == (int) number(expectedBefore, "amg_max_cycles")
                    && Math.abs(currentCfl - frozenBaseline) <= 1e-12 * Math.max(1.0, Math.abs(frozenBaseline));
            boolean afterVerified = afterOptionVerified
                    && Math.abs(courant.getCFL() - targetCfl) <= 1e-9 * Math.max(1.0, targetCfl);

            File output = new File(dir, "mrf_solver_experiment_configured.sim");
            sim.saveState(output.getAbsolutePath());
            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"solver\":\"ConfigureMrfSolverExperiment.java\",")
                .append("\"applied\":true,\"experiment_id\":\"").append(esc(experimentId)).append('"')
                .append(",\"iteration\":").append(iteration)
                .append(",\"changed_fields\":[");
            for (int i = 0; i < changed.size(); i++) {
                if (i > 0) json.append(',');
                json.append('"').append(esc(changed.get(i))).append('"');
            }
            json.append(']')
                .append(",\"target_CFL\":").append(targetCfl)
                .append(",\"frozen_baseline_CFL\":").append(frozenBaseline)
                .append(",\"no_op\":false")
                .append(",\"amg_fields_unchanged\":true")
                .append(",\"before\":{").append(mapJson(before)).append('}')
                .append(",\"after\":{").append(mapJson(after)).append('}')
                .append(",\"expected_before_verified\":").append(beforeVerified)
                .append(",\"expected_after_verified\":").append(afterVerified)
                .append(",\"before_option_verified\":").append(optionMatched)
                .append(",\"after_control_class\":\"").append(esc(afterControlClass)).append('"')
                .append(",\"note\":\"ONE documented solver change applied and read back; all 8 undeclared AMG fields ")
                .append("verified unchanged; no initialize/iterate/solve. Snapshot-derived absolute CFL target - not a ")
                .append("controller-max-relative rule.\"}\n");
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_experiment.json")), StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_experiment_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF solver experiment configuration failed", failure);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private static boolean close(double a, double b, double relTolerance) {
        return Math.abs(a - b) <= relTolerance * Math.max(1.0, Math.abs(b));
    }

    private static String mapJson(Map<String, String> values) {
        StringBuilder out = new StringBuilder();
        boolean first = true;
        for (Map.Entry<String, String> entry : values.entrySet()) {
            if (!first) out.append(',');
            first = false;
            out.append('"').append(esc(entry.getKey())).append("\":\"").append(esc(entry.getValue())).append('"');
        }
        return out.toString();
    }

    private static String read(File file) throws Exception {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    private static int valueStart(String json, String key) {
        int i = json.indexOf("\"" + key + "\"");
        if (i < 0) throw new IllegalArgumentException("json has no key " + key);
        int colon = json.indexOf(':', i + key.length() + 2);
        if (colon < 0) throw new IllegalArgumentException("json key has no value " + key);
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) start++;
        return start;
    }

    private static String object(String json, String key) {
        int start = valueStart(json, key);
        if (start >= json.length() || json.charAt(start) != '{') {
            throw new IllegalArgumentException("json key is not an object: " + key);
        }
        int depth = 0;
        for (int i = start; i < json.length(); i++) {
            char ch = json.charAt(i);
            if (ch == '{') depth++;
            else if (ch == '}') { depth--; if (depth == 0) return json.substring(start + 1, i); }
        }
        throw new IllegalArgumentException("unterminated object: " + key);
    }

    private static String string(String json, String key) {
        int start = valueStart(json, key);
        if (start >= json.length() || json.charAt(start) != '"') {
            throw new IllegalArgumentException("json key is not a string: " + key);
        }
        int end = json.indexOf('"', start + 1);
        if (end < 0) throw new IllegalArgumentException("unterminated string: " + key);
        return json.substring(start + 1, end);
    }

    private static double number(String json, String key) {
        int start = valueStart(json, key);
        int end = start;
        while (end < json.length() && "-+0123456789.eE".indexOf(json.charAt(end)) >= 0) end++;
        if (end == start) throw new IllegalArgumentException("json value is not a number: " + key);
        double value = Double.parseDouble(json.substring(start, end));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("non-finite " + key);
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
