import star.common.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Run a bounded 100-iteration diagnostic on an isolated initialized MRF mesh. */
public class RunImportedMrfSmoke extends StarMacro {
    private static String field(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }

    private static String sha256(File file) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));
        StringBuilder value = new StringBuilder();
        for (byte item : digest) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        int start = sim.getSimulationIterator().getCurrentIteration();
        long began = System.nanoTime();
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            String scenario = field(spec, "scenario");
            if (!"geometry_mesh_probe_only".equals(scenario))
                throw new IllegalStateException("Unexpected scenario: " + scenario);
            String expectedHash = field(spec, "expected_input_sha256");
            String actualHash = sha256(new File(dir, "input.sim"));
            if (!actualHash.equalsIgnoreCase(expectedHash))
                throw new IllegalStateException("Input SHA-256 mismatch: expected=" + expectedHash + " actual=" + actualHash);
            if (sim.getRegionManager().getObjects().size() != 2 ||
                    sim.getInterfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Expected a two-region, three-interface MRF mesh");
            }
            if (sim.getRegionManager().getRegion(field(spec, "rotor_region")) == null ||
                    sim.getRegionManager().getRegion(field(spec, "stator_region")) == null) {
                throw new IllegalStateException("Configured rotor/stator Regions are missing");
            }
            StepStoppingCriterion maximum = (StepStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
            if (maximum == null) throw new IllegalStateException("Maximum Steps criterion is missing");
            if (start == 0) sim.getSolution().initializeSolution();
            maximum.getMaximumNumberStepsObject().getQuantity().setValue(start + 100);
            maximum.setIsUsed(true);
            File csv = new File(dir, "mrf_solver_smoke.csv");
            try (PrintWriter out = new PrintWriter(new OutputStreamWriter(
                    new FileOutputStream(csv), StandardCharsets.UTF_8))) {
                out.println("iteration,elapsed_seconds");
                for (int requested = 0; requested < 100; requested += 10) {
                    int before = sim.getSimulationIterator().getCurrentIteration();
                    sim.getSimulationIterator().run(10);
                    int after = sim.getSimulationIterator().getCurrentIteration();
                    if (after - before != 10) throw new IllegalStateException("Solver stopped before the requested 100-step pilot");
                    out.printf(Locale.ROOT, "%d,%.3f%n", after, (System.nanoTime() - began) / 1e9);
                    out.flush();
                    writeStatus(dir, start, after, began, "RUNNING");
                }
            }
            int end = sim.getSimulationIterator().getCurrentIteration();
            if (end - start != 100) throw new IllegalStateException("Iteration budget was not completed");
            sim.saveState(new File(dir, "mrf_solver_smoke_checkpoint.sim").getAbsolutePath());
            writeStatus(dir, start, end, began, "SMOKE_COMPLETED");
            System.out.println("IMPORTED_MRF_SMOKE_COMPLETE");
        } catch (Throwable failure) {
            try (PrintWriter out = new PrintWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "mrf_solver_smoke_failure.txt")), StandardCharsets.UTF_8))) {
                out.println(failure.toString());
            } catch (Exception ignored) { }
            throw new RuntimeException("Imported MRF smoke run failed", failure);
        }
    }

    private static void writeStatus(File dir, int start, int end, long began, String state) throws Exception {
        try (PrintWriter out = new PrintWriter(new OutputStreamWriter(
                new FileOutputStream(new File(dir, "smoke_status.json")), StandardCharsets.UTF_8))) {
            out.printf(Locale.ROOT, "{\"state\":\"%s\",\"start_iteration\":%d,\"end_iteration\":%d,\"elapsed_seconds\":%.3f,\"convergence_verified\":false}%n",
                    state, start, end, (System.nanoTime() - began) / 1e9);
        }
    }
}
