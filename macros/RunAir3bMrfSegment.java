/* AIR-3B: one finite production segment of the steady MRF solve.
 * New requests use a finite iteration cap and zero wall-clock budget, following the user's
 * 2026-10-14 policy. Positive legacy time values remain explicit opt-in compatibility only.
 * Periodic checkpoints preserve progress independently of the observation client's lifetime.
 *
 * Unlike the pilot, which sampled target quantities once per chunk, this macro creates real
 * per-iteration monitors before solving so that the load history used by the steady-window
 * gate is sampled every iteration, and it exports the normalized residual monitors so the
 * residual gate has evidence. It continues an existing solution when the input is already
 * run, and only initializes when the input is unrun.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;

public class RunAir3bMrfSegment extends StarMacro {

    private static final String COMPLETE = "AIR3B_SEGMENT_COMPLETE";
    private static final String[] REQUIRED_REPORTS = { "AIR3B_THRUST_Z", "AIR3B_TORQUE_Z" };

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private static int finiteInteger(double value, int minimum, String name) {
        if (!Double.isFinite(value) || value != Math.rint(value)
                || value < minimum || value > Integer.MAX_VALUE)
            throw new IllegalArgumentException("Invalid " + name);
        return (int) value;
    }

    private static int nextChunk(int chunk, int planned, int ran) {
        return Math.min(chunk, planned - ran);
    }

    private static boolean wallClockStop(double budgetSeconds, double elapsed) {
        return budgetSeconds > 0 && elapsed >= budgetSeconds;
    }

    private static String probeJson(Map<String, Double> probes) {
        StringBuilder b = new StringBuilder();
        boolean first = true;
        for (Map.Entry<String, Double> e : probes.entrySet()) {
            b.append(first ? "" : ",").append('"').append(e.getKey()).append("\":");
            b.append(Double.isFinite(e.getValue()) ? String.format(Locale.ROOT, "%.12g", e.getValue()) : "null");
            first = false;
        }
        return b.toString();
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation r : sim.getRepresentationManager().getObjects())
            if (r instanceof FvRepresentation) return (FvRepresentation) r;
        return null;
    }

    private static void status(File dir, int start, int current, long began, String state) {
        try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_segment_status.json")),
                StandardCharsets.UTF_8)) {
            w.write("{\"state\":\"" + state + "\",\"start_iteration\":" + start
                    + ",\"current_iteration\":" + current
                    + ",\"elapsed_seconds\":" + String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1e9)
                    + ",\"convergence_verified\":false}\n");
        } catch (Throwable ignored) { }
    }

    /** Exports every residual monitor with its stored normalization, without changing it. */
    private static int exportResiduals(Simulation sim, File dir) {
        int written = 0;
        for (Monitor m : sim.getMonitorManager().getObjects()) {
            if (!(m instanceof ResidualMonitor)) continue;
            ResidualMonitor r = (ResidualMonitor) m;
            String safe = r.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_");
            try {
                r.export(new File(dir, "residual-" + safe + ".csv").getAbsolutePath());
                written++;
            } catch (Throwable t) {
                System.out.println("Residual export failed for " + r.getPresentationName() + ": " + t.getMessage());
            }
        }
        return written;
    }

    private static int exportReportMonitors(Simulation sim, File dir) {
        int written = 0;
        for (Monitor m : sim.getMonitorManager().getObjects()) {
            if (!(m instanceof ReportMonitor)) continue;
            ReportMonitor r = (ReportMonitor) m;
            String safe = r.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_");
            try {
                r.export(new File(dir, "monitor-" + safe + ".csv").getAbsolutePath());
                written++;
            } catch (Throwable t) {
                System.out.println("Monitor export failed for " + r.getPresentationName() + ": " + t.getMessage());
            }
        }
        return written;
    }

    /**
     * Merges the CSV files this macro just exported into one fixed-name long-format table.
     *
     * STAR names an exported monitor file after the monitor, and the residual monitor set is a
     * property of the physics, so those file names are not knowable when a pipeline pins the
     * artifacts it wants downloaded. This reads the exports back and writes a single predictable
     * file per kind, taking the first two numeric fields of every row, which is the (iteration,
     * value) pair for a monitor export. Row counts are reported so an unparsable export shows up
     * as zero rows instead of silently looking like an empty history.
     */
    private static int mergeExports(File dir, final String prefix, String destination, String kind) {
        File[] files = dir.listFiles(new FilenameFilter() {
            @Override public boolean accept(File d, String n) {
                return n.startsWith(prefix) && n.endsWith(".csv");
            }
        });
        Arrays.sort(files == null ? new File[0] : files);
        int rows = 0;
        // The destination is written even when there is nothing to merge: a pipeline pins these
        // artifact names in advance, and a missing file would fail the trial after a good solve.
        try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, destination)),
                StandardCharsets.UTF_8)) {
            w.write("kind\tname\tfirst_number\tsecond_number\n");
            if (files == null || files.length == 0) return 0;
            for (File f : files) {
                String name = f.getName().substring(prefix.length(), f.getName().length() - 4);
                try (BufferedReader r = new BufferedReader(
                        new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        List<String> nums = new ArrayList<>();
                        for (String part : line.split("[,;\\t]+")) {
                            String t = part.trim();
                            if (t.isEmpty()) continue;
                            try { Double.parseDouble(t); nums.add(t); } catch (NumberFormatException ignored) { }
                        }
                        if (nums.size() >= 2) {
                            w.write(kind + "\t" + name + "\t" + nums.get(0) + "\t" + nums.get(1) + "\n");
                            rows++;
                        }
                    }
                } catch (Throwable t) {
                    System.out.println("Merge skipped " + f.getName() + ": " + t.getMessage());
                }
            }
        } catch (Throwable t) {
            System.out.println("Merge failed for " + destination + ": " + t.getMessage());
            return 0;
        }
        return rows;
    }

    /** Writes the monitor long table from the macro-written per-chunk history, when the
     *  per-iteration ReportMonitors could not be created on this install. */
    private static int monitorTableFromHistory(File dir, String historyName) {
        int rows = 0;
        try (Writer w = new OutputStreamWriter(
                new FileOutputStream(new File(dir, "air3b_segment_monitors.tsv")), StandardCharsets.UTF_8)) {
            w.write("kind\tname\tfirst_number\tsecond_number\n");
            try (BufferedReader r = new BufferedReader(new InputStreamReader(
                    new FileInputStream(new File(dir, historyName)), StandardCharsets.UTF_8))) {
                String line = r.readLine();
                while ((line = r.readLine()) != null) {
                    String[] f = line.split(",");
                    if (f.length < 4) continue;
                    w.write("monitor\tMON_THRUST_Z\t" + f[0] + "\t" + f[2] + "\n");
                    w.write("monitor\tMON_TORQUE_Z\t" + f[0] + "\t" + f[3] + "\n");
                    rows += 2;
                }
            }
        } catch (Throwable t) {
            System.out.println("Chunk-history monitor table failed: " + t.getMessage());
            return 0;
        }
        return rows;
    }

    /** Explicit CaseSpec role contract. Legacy jobs keep their previous names. */
    private static String role(File dir, String key, String fallback) throws Exception {
        if (!"true".equals(System.getenv("MRF_ROLE_SPEC"))) return fallback;
        String spec = java.nio.file.Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
        java.util.regex.Matcher match = java.util.regex.Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!match.find()) throw new IllegalStateException("Missing CaseSpec role: " + key);
        String result = match.group(1);
        if (match.find()) throw new IllegalStateException("Ambiguous CaseSpec role: " + key);
        return result;
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        String dirPath = System.getenv("AIR3B_JOB_DIR");
        if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
            throw new IllegalStateException("Invalid isolated job");
        File dir = new File(dirPath);
        File output = new File(dir, envText("AIR3B_SEGMENT_OUTPUT", "mrf_segment.sim"));
        double budgetSeconds = env("AIR3B_SEGMENT_SECONDS", 0.0);
        int chunk = finiteInteger(env("AIR3B_SEGMENT_CHUNK", 10.0), 1, "segment chunk");
        int cap = finiteInteger(env("AIR3B_SEGMENT_MAX_ITERATIONS", 600.0), 0, "segment iteration cap");
        int checkpointEvery = finiteInteger(env("AIR3B_CHECKPOINT_EVERY_CHUNKS", 0.0), 0,
                "checkpoint interval");
        String index = envText("AIR3B_SEGMENT_INDEX", "1");
        if (!Double.isFinite(budgetSeconds) || budgetSeconds < 0)
            throw new IllegalArgumentException("Invalid segment time budget");
        if (chunk > 100) throw new IllegalArgumentException("Invalid segment chunk");
        if (budgetSeconds == 0 && cap == 0)
            throw new IllegalArgumentException("A no-wall-clock segment requires a finite iteration cap");
        if (output.exists()) throw new IllegalStateException("Segment output already exists");

        int start = sim.getSimulationIterator().getCurrentIteration();
        long began = System.nanoTime();
        boolean finite = true;
        try {
            if (sim.getContinuumManager().getObjects().isEmpty())
                throw new IllegalStateException("No physics continuum is configured");
            if (sim.getRegionManager().getObjects().size() != 2
                    || sim.getInterfaceManager().getObjects().size() != 3)
                throw new IllegalStateException("Expected two regions and three interfaces");

            Map<String, Report> reports = new LinkedHashMap<>();
            String thrustName = role(dir, "thrust_report", REQUIRED_REPORTS[0]);
            String torqueName = role(dir, "torque_report", REQUIRED_REPORTS[1]);
            for (String name : new String[] {thrustName, torqueName}) {
                Report r = sim.getReportManager().getReport(name);
                if (r == null) throw new IllegalStateException("Target-quantity report is missing: " + name);
                reports.put(name, r);
            }

            boolean initializedFlag = sim.getSolution().isInitialized();
            // The chain's starting point is recorded as data, not asserted: the loaded iteration,
            // the pre-flight target-quantity values and the object counts all go into the verdict.
            Map<String, Double> loadProbe = new LinkedHashMap<>();
            FvRepresentation loadMesh = findVolumeMesh(sim);
            if (loadMesh == null)
                throw new IllegalStateException("No volume mesh in the loaded state; refusing to solve");
            long loadCells = loadMesh.getCellCount();
            int loadRegions = sim.getRegionManager().getObjects().size();
            int loadInterfaces = sim.getInterfaceManager().getObjects().size();
            int expectedIteration = (int) env("AIR3B_EXPECTED_LOADED_ITERATION", -1.0);
            if (expectedIteration >= 0 && start != expectedIteration)
                throw new IllegalStateException("Loaded iteration is " + start + " but the chain expects "
                        + expectedIteration + "; the checkpoint is not the one this segment continues");
            if (start == 0) {
                sim.getSolution().initializeSolution();
                if (!sim.getSolution().isInitialized())
                    throw new IllegalStateException("Initialization did not take effect");
            } else {
                // A state loaded from a saved .sim reports isInitialized() false even though the
                // iteration counter proves the solver advanced and the fields are present: the
                // flag is not persisted. The first segment of this chain failed on exactly that
                // (air3b-restore-prod-seg01-20261002a, "Segment input is run but not initialized"),
                // so for a continuation the precondition is the iteration count plus field values
                // that actually evaluate, checked here before any solving, rather than the flag.
                for (Map.Entry<String, Report> e : reports.entrySet()) {
                    double probe = e.getValue().getReportMonitorValue();
                    loadProbe.put(e.getKey(), probe);
                    if (!Double.isFinite(probe))
                        throw new IllegalStateException("Continuation input cannot evaluate " + e.getKey()
                                + "; the loaded state does not carry usable fields");
                }
            }

            // Per-iteration monitors must exist before the run: getReportMonitorValue() alone
            // creates no history, and the steady-window gate needs every iteration.
            boolean monitorsCreated = true;
            for (Map.Entry<String, Report> e : reports.entrySet()) {
                String monName = "AIR3B_MON_" + e.getKey().replaceFirst("^AIR3B_", "");
                // getMonitor(name) THROWS "does not exist in manager" rather than returning null;
                // that cost this stage a trial. Existing monitors are found by iterating instead.
                boolean present = false;
                for (Monitor m : sim.getMonitorManager().getObjects())
                    if (monName.equals(m.getPresentationName()) && m instanceof ReportMonitor) present = true;
                if (present) continue;
                try {
                    ReportMonitor mon = sim.getMonitorManager().createMonitor(ReportMonitor.class);
                    mon.setPresentationName(monName);
                    mon.setReport(e.getValue());
                } catch (Throwable t) {
                    // The per-iteration monitor is a refinement of the load evidence, not the
                    // evidence itself: the per-chunk history is written either way, so an API
                    // surprise here is recorded rather than allowed to fail a good solve.
                    monitorsCreated = false;
                    System.out.println("ReportMonitor creation unavailable for " + monName + ": " + t.getMessage());
                }
            }

            int planned = (cap > 0 ? cap : 100000);
            if (start > Integer.MAX_VALUE - planned)
                throw new IllegalArgumentException("Iteration target overflows");
            StepStoppingCriterion maximum = (StepStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
            if (maximum == null) throw new IllegalStateException("Maximum Steps stopping criterion is missing");
            maximum.getMaximumNumberStepsObject().getQuantity().setValue(start + planned);
            maximum.setIsUsed(true);

            int ran = 0;
            int last = start;
            int chunks = 0;
            int checkpointCount = 0;
            File periodicCheckpoint = new File(dir, "mrf_segment_checkpoint.sim");
            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_segment_" + index + "_history.csv")),
                    StandardCharsets.UTF_8)) {
                w.write("iteration,elapsed_seconds,thrust_z_n,torque_z_nm\n");
                status(dir, start, start, began, "RUNNING");
                while (true) {
                    double elapsed = (System.nanoTime() - began) / 1e9;
                    if (wallClockStop(budgetSeconds, elapsed)) break;
                    if (ran >= planned) break;
                    int before = sim.getSimulationIterator().getCurrentIteration();
                    sim.getSimulationIterator().run(nextChunk(chunk, planned, ran));
                    int after = sim.getSimulationIterator().getCurrentIteration();
                    if (after <= before) throw new IllegalStateException("Solver made no progress");
                    ran += after - before;
                    last = after;
                    double f = Double.NaN, m = Double.NaN;
                    try { f = reports.get(thrustName).getReportMonitorValue(); } catch (Throwable ignored) { }
                    try { m = reports.get(torqueName).getReportMonitorValue(); } catch (Throwable ignored) { }
                    if (!Double.isFinite(f) || !Double.isFinite(m)) finite = false;
                    w.write(after + "," + String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1e9)
                            + "," + f + "," + m + "\n");
                    w.flush();
                    status(dir, start, after, began, finite ? "RUNNING" : "DIVERGING");
                    if (!finite) break;
                    chunks++;
                    if (checkpointEvery > 0 && chunks % checkpointEvery == 0) {
                        sim.saveState(periodicCheckpoint.getAbsolutePath());
                        checkpointCount++;
                    }
                }
            }
            if (ran <= 0) throw new IllegalStateException("Segment performed no iterations");

            // Evidence is exported before the checkpoint so a failed save still leaves it.
            int residuals = exportResiduals(sim, dir);
            int monitors = exportReportMonitors(sim, dir);
            int residualRows = mergeExports(dir, "residual-", "air3b_segment_residuals.tsv", "residual");
            // monitor_source is decided AFTER the merge, not before: STAR exporting no monitor
            // files while creation succeeded would otherwise leave a header-only table that
            // satisfies the pipeline's required-content check with zero load samples, a
            // pass-with-no-data hole that would only surface at the convergence screen.
            int monitorRows;
            String monitorSource;
            if (monitorsCreated) {
                monitorRows = mergeExports(dir, "monitor-", "air3b_segment_monitors.tsv", "monitor");
                monitorSource = "report_monitors";
                if (monitorRows == 0) {
                    monitorRows = monitorTableFromHistory(dir, "air3b_segment_" + index + "_history.csv");
                    monitorSource = "chunk_history_incomplete_exports";
                }
            } else {
                monitorRows = monitorTableFromHistory(dir, "air3b_segment_" + index + "_history.csv");
                monitorSource = "chunk_history";
            }
            sim.saveState(output.getAbsolutePath());
            status(dir, start, last, began, finite ? "SEGMENT_COMPLETED" : "SEGMENT_DIVERGED");
            try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_segment_verdict.json")),
                    StandardCharsets.UTF_8)) {
                w.write("{\"status\":\"ok\""
                        + ",\"status_meaning\":\"ok means this bounded solve segment OPERATION completed and"
                        + " every value reported here is this run's own readback. It is NOT a convergence or"
                        + " physics verdict: those are the separate fields state, finite_target_quantities and"
                        + " convergence_verified below, and a segment that diverged still writes status ok"
                        + " because the operation did complete - the stage then fails on state, so this record"
                        + " cannot be cited as a success.\""
                        + ",\"state\":\"" + (finite ? "SEGMENT_COMPLETED" : "SEGMENT_DIVERGED")
                        + "\",\"start_iteration\":" + start + ",\"end_iteration\":" + last
                        + ",\"iterations_run\":" + ran
                        + ",\"budget_seconds\":" + budgetSeconds
                        + ",\"wall_clock_stop_enabled\":" + (budgetSeconds > 0)
                        + ",\"iteration_cap\":" + planned
                        + ",\"checkpoint_every_chunks\":" + checkpointEvery
                        + ",\"periodic_checkpoints_saved\":" + checkpointCount
                        + ",\"periodic_checkpoint\":\"" + periodicCheckpoint.getName() + "\""
                        + ",\"elapsed_seconds\":" + String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1e9)
                        + ",\"residual_files\":" + residuals + ",\"monitor_files\":" + monitors
                        + ",\"residual_rows\":" + residualRows + ",\"monitor_rows\":" + monitorRows
                        + ",\"monitors_created\":" + monitorsCreated
                        + ",\"monitor_source\":\"" + monitorSource + "\""
                        + ",\"initialized_flag_on_load\":" + initializedFlag
                        + ",\"initialized_flag_note\":\"false on a continuation is the expected signature of "
                        + "non-persistence, not an anomaly\""
                        + ",\"loaded_iteration\":" + start
                        + ",\"regions\":" + loadRegions + ",\"interfaces\":" + loadInterfaces
                        + ",\"cells\":" + loadCells
                        + ",\"load_probe\":{" + probeJson(loadProbe) + "}"
                        + ",\"finite_target_quantities\":" + finite
                        + ",\"output\":\"" + output.getName() + "\""
                        + ",\"convergence_verified\":false}\n");
            }
            if (!finite)
                throw new IllegalStateException("Segment produced non-finite target quantities");
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try {
                status(dir, start, sim.getSimulationIterator().getCurrentIteration(), began, "FAILED");
                try (Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "air3b_segment_failure.txt")),
                        StandardCharsets.UTF_8)) {
                    w.write(failure.getClass().getName() + "\t"
                            + String.valueOf(failure.getMessage()).replace('\n', ' ') + "\n");
                }
            } catch (Throwable ignored) { }
            throw new RuntimeException("AIR-3B MRF segment failed: " + failure.getMessage(), failure);
        }
    }
}
