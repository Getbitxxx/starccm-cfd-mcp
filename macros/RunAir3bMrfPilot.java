/* AIR-3B A3b: bounded pilot run on the configured MRF simulation.
 *
 * Initializes the solution once and advances it in small chunks, writing a progress
 * status after every chunk and checkpointing the simulation at the end, so a diverging
 * or over-running pilot still leaves evidence. It is deliberately bounded: the plan
 * calls for a short pilot before any production solve, and this project has already
 * recorded a compressible cold start that diverged within tens of steps.
 *
 * It never meshes, never changes physics, and only saves the pilot checkpoint.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.report.*;
import star.common.*;

public class RunAir3bMrfPilot extends StarMacro {

    private static final String COMPLETE = "AIR3B_PILOT_COMPLETE";

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private static void status(File dir, int start, int end, long began, String state) throws IOException {
        try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_pilot_status.json")),
                StandardCharsets.UTF_8)) {
            w.write("{\"state\":\"" + state + "\",\"start_iteration\":" + start
                    + ",\"end_iteration\":" + end
                    + ",\"elapsed_seconds\":" + String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1e9)
                    + ",\"convergence_verified\":false}\n");
        }
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        String dirPath = System.getenv("AIR3B_JOB_DIR");
        if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
            throw new IllegalStateException("Invalid isolated job");
        File dir = new File(dirPath);
        File output = new File(dir, envText("AIR3B_PILOT_OUTPUT", "mrf_pilot.sim"));
        int iterations = (int) env("AIR3B_PILOT_ITERATIONS", 100.0);
        int chunk = (int) env("AIR3B_PILOT_CHUNK", 10.0);
        if (iterations < 1 || iterations > 2000) throw new IllegalArgumentException("Invalid pilot iteration budget");
        if (chunk < 1 || chunk > iterations || iterations % chunk != 0)
            throw new IllegalArgumentException("Pilot chunk must divide the iteration budget");
        if (output.exists()) throw new IllegalStateException("Pilot output already exists");

        int start = sim.getSimulationIterator().getCurrentIteration();
        long began = System.nanoTime();
        try {
            if (sim.getContinuumManager().getObjects().isEmpty())
                throw new IllegalStateException("No physics continuum is configured");
            if (sim.getRegionManager().getObjects().size() != 2
                    || sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("Expected two regions and three interfaces");
            if (start != 0) throw new IllegalStateException("Pilot must start from an unrun simulation");

            // Keep the generic Report type: the concrete ForceReport/MomentReport classes are
            // not proven to extend ScalarReport, and getReportMonitorValue() is available here.
            Report thrust = sim.getReportManager().getReport("AIR3B_THRUST_Z");
            Report torque = sim.getReportManager().getReport("AIR3B_TORQUE_Z");
            if (thrust == null || torque == null) throw new IllegalStateException("Target-quantity reports are missing");

            sim.getSolution().initializeSolution();
            if (!sim.getSolution().isInitialized()) throw new IllegalStateException("Initialization did not take effect");

            StepStoppingCriterion maximum = (StepStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
            if (maximum == null) throw new IllegalStateException("Maximum Steps stopping criterion is missing");
            maximum.getMaximumNumberStepsObject().getQuantity().setValue(start + iterations);
            maximum.setIsUsed(true);

            boolean finite = true;
            boolean midwayWritten = false;
            int midway = start + iterations / 2;
            try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_pilot_history.csv")),
                    StandardCharsets.UTF_8)) {
                w.write("iteration,elapsed_seconds,thrust_z_n,torque_z_nm\n");
                status(dir, start, start, began, "RUNNING");
                for (int done = 0; done < iterations; done += chunk) {
                    int before = sim.getSimulationIterator().getCurrentIteration();
                    sim.getSimulationIterator().run(chunk);
                    int after = sim.getSimulationIterator().getCurrentIteration();
                    if (after - before != chunk)
                        throw new IllegalStateException("Solver stopped after " + (after - before)
                                + " of the requested " + chunk + " steps");
                    double f = Double.NaN, m = Double.NaN;
                    try { f = thrust.getReportMonitorValue(); } catch (Throwable ignored) { }
                    try { m = torque.getReportMonitorValue(); } catch (Throwable ignored) { }
                    if (!Double.isFinite(f) || !Double.isFinite(m)) finite = false;
                    w.write(after + "," + String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1e9)
                            + "," + f + "," + m + "\n");
                    w.flush();
                    if (!midwayWritten && after >= midway) {
                        // An interim checkpoint means a timeout still leaves something restartable.
                        sim.saveState(new File(dir, "mrf_pilot_partial.sim").getAbsolutePath());
                        midwayWritten = true;
                    }
                    status(dir, start, after, began, finite ? "RUNNING" : "DIVERGING");
                }
            }
            int end = sim.getSimulationIterator().getCurrentIteration();
            if (end - start != iterations)
                throw new IllegalStateException("Pilot iteration budget was not completed");
            sim.saveState(output.getAbsolutePath());
            status(dir, start, end, began, finite ? "PILOT_COMPLETED" : "PILOT_DIVERGED");
            try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_pilot_verdict.json")),
                    StandardCharsets.UTF_8)) {
                w.write("{\"state\":\"" + (finite ? "PILOT_COMPLETED" : "PILOT_DIVERGED")
                        + "\",\"finite_target_quantities\":" + finite + "}\n");
            }
            if (!finite)
                throw new IllegalStateException("Pilot produced non-finite target quantities; not a usable pilot");
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try {
                status(dir, start, sim.getSimulationIterator().getCurrentIteration(), began, "FAILED");
                try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_pilot_failure.txt")),
                        StandardCharsets.UTF_8)) {
                    w.write(failure.getClass().getName() + "\t"
                            + String.valueOf(failure.getMessage()).replace('\n', ' ') + "\n");
                }
            } catch (Throwable ignored) { }
            throw new RuntimeException("AIR-3B MRF pilot failed: " + failure.getMessage(), failure);
        }
    }
}
