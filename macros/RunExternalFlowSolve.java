/* Stage 1 of the external-flow solve: run the steady solver for a bounded budget and report what
 * actually happened, converged or not.
 *
 * TRACK P SEMANTICS: this macro's success criterion is "the solve ran and left evidence", not "the
 * residuals are low". `status:"ok"` means the stage did its job; the convergence verdict lives in a
 * separate field (`converged`) with the CaseSpec gates it was compared against. An honest
 * NOT_CONVERGED is a pass for track P; writing converged:true without the gates being met is not.
 *
 * STOPPING BASIS: the iteration cap (EXTERNAL_FLOW_MAX_ITERATIONS, default 1200; a value below 1 makes
 * the macro fail fast rather than run unbounded), the non-finite divergence stop below, a resource
 * anomaly detected by the runner, or a user stop. A wall-clock budget is honoured ONLY if
 * EXTERNAL_FLOW_BUDGET_SECONDS is explicitly set to a positive value; by default (0/unset) elapsed time
 * never stops the solve. The user revoked every time budget on 2026-10-14 and this round landed the
 * execution-layer half of that decision.
 *
 * NOT IMPLEMENTED - STAGNATION DIAGNOSTIC: residuals that plateau above the gates are NOT detected
 * here. Such a run is still bounded by the iteration cap, so it wastes iterations rather than running
 * unbounded, but it is not stopped early and no stagnation record is written. An earlier revision of
 * this comment claimed stagnation diagnostics; that claim was wrong and is recorded as an open item.
 *
 * HISTORY: the macro used to default to a 540 s budget, which is why earlier records read
 * stop_reason=wall_clock_budget. Those records keep their original bytes; the behaviour changed here.
 * WHY IT USED TO BE BUDGETED RATHER THAN FIXED-COUNT: a controller trial is bounded by the scheduled task's
 * ExecutionTimeLimit, and the loop is chunked so that a checkpoint and the evidence are written at a
 * chunk boundary well before any wall-clock limit can kill the process. The chunked loop, the
 * `Maximum Steps` stopping criterion, the residual/monitor export and the "maximum steps" pattern are
 * copied from RunAir3bMrfSegment, the one segmented solver sequence this repository has executed.
 *
 * It creates the target-quantity reports its own verdict needs (a +X force on the body wall, a mass
 * flow on every boundary of the region, a per-iteration drag monitor), because a verdict may not
 * depend on reports that a previous stage happened to leave behind.
 */
import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.FilenameFilter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.NamedObject;
import star.base.report.*;
import star.common.*;
import star.flow.*;

public class RunExternalFlowSolve extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_SOLVE_COMPLETE";
    private static final String DRAG = "EXTERNAL_FLOW_DRAG_X";
    private static final String DRAG_MONITOR = "EXTERNAL_FLOW_MON_DRAG_X";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_solve.json");
        long began = System.nanoTime();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String wallName = string(spec, "body_wall_boundary_name");
            double speed = Math.sqrt(sumSquares(array(spec, "velocity_m_s")));
            if (!(speed > 0)) throw new IllegalArgumentException("CaseSpec inflow speed is not positive");
            double referenceArea = number(spec, "reference_area_m2");
            int chunk = (int) env("EXTERNAL_FLOW_CHUNK", 50.0);
            int maxIterations = (int) env("EXTERNAL_FLOW_MAX_ITERATIONS", 1200.0);
            // Thresholds come from the CaseSpec, not from literals in this file (review round 8, C3/C4).
            double forceWindowRelChange = optionalNumber(spec, "force_window_rel_change", 0.01);
            double imbalanceLimit = optionalNumber(spec, "mass_imbalance_ratio_limit", 1.0E-3);
            // NO WALL-CLOCK STOP (user 2026-10-14 revoked every time budget; execution layer landed
            // in this round). 0 or unset means the macro never stops on elapsed time; it stops on the
            // the iteration cap, the non-finite divergence stop, a resource anomaly or a user stop.
            double budgetSeconds = env("EXTERNAL_FLOW_BUDGET_SECONDS", 0.0);
            String budgetBasis = System.getenv("EXTERNAL_FLOW_BUDGET_BASIS");
            if (budgetBasis == null || budgetBasis.isEmpty()) budgetBasis = "macro_elapsed";
            if (!budgetBasis.equals("macro_elapsed") && !budgetBasis.equals("iterator"))
                throw new IllegalArgumentException("Unknown solver budget basis");
            double checkpointInterval = env("EXTERNAL_FLOW_CHECKPOINT_EVERY_CHUNKS", 0.0);
            if (!Double.isFinite(checkpointInterval) || checkpointInterval < 0
                    || checkpointInterval != Math.rint(checkpointInterval) || checkpointInterval > Integer.MAX_VALUE)
                throw new IllegalArgumentException("Checkpoint interval must be a nonnegative integer");
            int checkpointEvery = (int) checkpointInterval;
            double expectedStart = env("EXTERNAL_FLOW_EXPECTED_START_ITERATION", -1.0);
            if (!Double.isFinite(expectedStart) || expectedStart < -1 || expectedStart != Math.rint(expectedStart))
                throw new IllegalArgumentException("Expected start must be -1 or a nonnegative integer");
            if (chunk < 1 || chunk > 500) throw new IllegalArgumentException("Chunk size is out of range");
            if (maxIterations < chunk) throw new IllegalArgumentException("Iteration cap is below one chunk");
            if (budgetSeconds > 0 && budgetSeconds < 20)
                throw new IllegalArgumentException("A positive wall-clock budget must be at least 20 s");
            if (checkpointEvery < 0) throw new IllegalArgumentException("Checkpoint interval is negative");

            Region region = sim.getRegionManager().getRegion(regionName);
            if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
            Boundary wall = region.getBoundaryManager().getBoundary(wallName);
            if (wall == null) throw new IllegalStateException("Body wall boundary missing: " + wallName);
            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("No volume mesh in the loaded state; refusing to solve");
            if (!mesh.describesRegion(region))
                throw new IllegalStateException("The Volume Mesh does not describe the flow region");

            // Target-quantity reports, CREATED HERE and deleted first if a same-named one exists:
            // the previous version reused an existing report by name without checking its parts or
            // direction, which is exactly the "inherit someone else's report" shape the comment
            // claimed to avoid (independent review round 8, concern C7).
            deleteReport(sim, DRAG);
            ForceReport drag = (ForceReport) sim.getReportManager().create("star.flow.ForceReport");
            drag.setPresentationName(DRAG);
            drag.setUseIdealization(false);
            drag.getParts().setObjects(new NamedObject[] {wall});
            drag.getDirection().setComponentsAndUnits(1.0, 0.0, 0.0, sim.getUnitsManager().getObject(""));
            String forceOption = setForceOption(drag, ForceReportForceOption.Type.PRESSURE_AND_SHEAR);
            String forceReportParts = forceReportParts(drag);
            Map<String, ScalarReport> flows = new LinkedHashMap<String, ScalarReport>();
            for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                String name = "EXTERNAL_FLOW_MASS_" + safe(boundary.getPresentationName());
                deleteReport(sim, name);
                // MassFlowReport, not the bare ScalarReport type: setUseIdealization lives on it.
                // Precedent with a recorded run: RepairImportedMrfReports.java:55 (declares
                // MassFlowReport and calls setUseIdealization); AuditAir3bMassBalance.java:70-74
                // casts to ScalarReport and never calls it, so it is not the precedent.
                MassFlowReport created = (MassFlowReport) sim.getReportManager()
                        .create("star.flow.MassFlowReport");
                created.setPresentationName(name);
                created.setUseIdealization(false);
                created.setRepresentation(mesh);
                created.getParts().setObjects(boundary);
                flows.put(boundary.getPresentationName(), created);
            }
            // A per-iteration drag monitor: getReportMonitorValue() alone creates no history, and the
            // stability window needs every iteration. Found by iterating because getMonitor(name)
            // throws rather than returning null when the monitor is absent.
            boolean monitorCreated = false;
            for (Monitor monitor : sim.getMonitorManager().getObjects()) {
                if (DRAG_MONITOR.equals(monitor.getPresentationName()) && monitor instanceof ReportMonitor)
                    monitorCreated = true;
            }
            if (!monitorCreated) {
                try {
                    ReportMonitor monitor = sim.getMonitorManager().createMonitor(ReportMonitor.class);
                    monitor.setPresentationName(DRAG_MONITOR);
                    monitor.setReport(drag);
                    monitorCreated = true;
                } catch (Throwable failure) {
                    System.out.println("Drag monitor creation unavailable: " + failure.getMessage());
                }
            }

            int start = sim.getSimulationIterator().getCurrentIteration();
            if (expectedStart >= 0 && (expectedStart != Math.rint(expectedStart) || start != expectedStart))
                throw new IllegalStateException("Checkpoint start iteration differs from expected start");
            if (start == 0) {
                sim.getSolution().initializeSolution();
                if (!sim.getSolution().isInitialized())
                    throw new IllegalStateException("Initialization did not take effect");
            }
            double loadDrag = finite(drag.getReportMonitorValue());
            double loadImbalance = imbalance(flows);

            int planned = maxIterations;
            StepStoppingCriterion maximum = (StepStoppingCriterion) sim.getSolverStoppingCriterionManager()
                    .getSolverStoppingCriterion("Maximum Steps");
            if (maximum == null) throw new IllegalStateException("Maximum Steps stopping criterion is missing");
            maximum.getMaximumNumberStepsObject().getQuantity().setValue(start + planned);
            maximum.setIsUsed(true);

            StringBuilder history = new StringBuilder("iteration,elapsed_seconds,drag_x_n,mass_imbalance\n");
            int ran = 0;
            int last = start;
            boolean finiteState = true;
            String stopReason = "iteration_cap";
            double setupSeconds = (System.nanoTime() - began) / 1.0E9;
            double iteratorSeconds = 0.0;
            double saveSeconds = 0.0;
            int checkpointSaves = 0;
            int chunksRan = 0;
            while (true) {
                double elapsed = (System.nanoTime() - began) / 1.0E9;
                double budgetClock = budgetBasis.equals("iterator") ? iteratorSeconds : elapsed;
                if (budgetSeconds > 0 && budgetClock >= budgetSeconds) {
                    stopReason = "wall_clock_budget";   // only reachable when a budget was explicitly requested
                    break;
                }
                if (ran >= maxIterations) {
                    stopReason = "iteration_cap";
                    break;
                }
                int before = sim.getSimulationIterator().getCurrentIteration();
                long iteratorBegan = System.nanoTime();
                sim.getSimulationIterator().run(Math.min(chunk, maxIterations - ran));
                iteratorSeconds += (System.nanoTime() - iteratorBegan) / 1.0E9;
                int after = sim.getSimulationIterator().getCurrentIteration();
                if (after <= before) throw new IllegalStateException("Solver made no progress");
                ran += after - before;
                last = after;
                double force = finite(drag.getReportMonitorValue());
                double imbalanceNow = imbalance(flows);
                if (!Double.isFinite(force) || !Double.isFinite(imbalanceNow)) finiteState = false;
                history.append(after).append(',')
                        .append(String.format(Locale.ROOT, "%.3f", (System.nanoTime() - began) / 1.0E9))
                        .append(',').append(finite(force)).append(',').append(finite(imbalanceNow)).append('\n');
                writeText(new File(dir, "external_flow_solve_history.csv"), history.toString());
                chunksRan++;
                if (finiteState && checkpointEvery > 0 && chunksRan % checkpointEvery == 0) {
                    long saveBegan = System.nanoTime();
                    sim.saveState(new File(dir, "external_flow_solved.sim").getAbsolutePath());
                    saveSeconds += (System.nanoTime() - saveBegan) / 1.0E9;
                    checkpointSaves++;
                    // A partial checkpoint is diagnostic until the runner certifies its stage.
                    writeText(new File(dir, "external_flow_segment_checkpoint.json"),
                            "{\"schema_version\":1,\"status\":\"checkpoint\",\"start_iteration\":" + start
                            + ",\"end_iteration\":" + last + ",\"iterations_run\":" + ran
                            + ",\"checkpoint\":\"external_flow_solved.sim\",\"stage_success_certified\":false}\n");
                }
                if (!finiteState) {
                    stopReason = "non_finite_target_quantity";
                    break;
                }
            }
            if (ran <= 0) throw new IllegalStateException("Solve performed no iterations");
            double seconds = (System.nanoTime() - began) / 1.0E9;

            // Evidence before the checkpoint: a failed save must not lose the solve.
            long exportBegan = System.nanoTime();
            int residualFiles = exportMonitors(sim, dir, true);
            int reportFiles = exportMonitors(sim, dir, false);
            File residuals = new File(dir, "external_flow_residuals.tsv");
            int residualRows = mergeResiduals(dir, residuals);
            Map<String, double[]> residualStats = residualStats(residuals);
            double density = volumeAverageDensity(sim, mesh, region);
            double finalDrag = finite(drag.getReportMonitorValue());
            double finalImbalance = imbalance(flows);
            double coefficient = Double.NaN;
            if (Double.isFinite(density) && density > 0 && Double.isFinite(finalDrag)
                    && referenceArea > 0 && speed > 0) {
                coefficient = 2.0 * finalDrag / (density * speed * speed * referenceArea);
            }

            Map<String, Double> gates = new LinkedHashMap<String, Double>();
            for (String key : new String[] {"continuity", "momentum", "energy"}) {
                double value = optionalNumber(spec, key, Double.NaN);
                if (Double.isFinite(value)) gates.put(key, value);
            }
            if (gates.isEmpty()) throw new IllegalStateException("CaseSpec declares no residual gates");
            boolean residualsMet = true;
            StringBuilder residualDetail = new StringBuilder();
            for (Map.Entry<String, Double> gate : gates.entrySet()) {
                // Worst matching monitor, not the first one: STAR names the momentum residuals
                // X-momentum / Y-momentum / Z-momentum, so "the first match" let one component exceed
                // the gate while the gate was reported as met. Index 0 is the FINAL value and it is both
                // the value compared against the gate and the value written under `final`.
                double[] window = worstWindow(residualStats, gate.getKey());
                double value = window == null ? Double.NaN : window[0];
                double lastMean = window == null ? Double.NaN : window[1];
                double previousMean = window == null ? Double.NaN : window[2];
                boolean ok = Double.isFinite(value) && value <= gate.getValue();
                residualsMet = residualsMet && ok;
                residualDetail.append(residualDetail.length() == 0 ? "" : ",")
                        .append('"').append(gate.getKey()).append("\":{\"final\":").append(json(value))
                        .append(",\"last_window_mean\":").append(json(lastMean))
                        .append(",\"previous_window_mean\":").append(json(previousMean))
                        .append(",\"decay_ratio\":").append(json(ratio(previousMean, lastMean)))
                        .append(",\"gate\":").append(json(gate.getValue())).append(",\"met\":").append(ok).append('}');
            }
            // Stability is only claimed when it was measurable: with fewer than three samples in the
            // window the test degenerates (one sample has zero spread), so the verdict becomes
            // "unmeasured" rather than a claim either way. Independent review round 8, blocker 2.
            Boolean loadsSteady = stability(history.toString(), forceWindowRelChange, imbalanceLimit);
            // The sign check answers "is the measured force in the drag direction?": the flow is +X, so
            // the force on the body must be +X. It is recorded AND it enters the verdict, because a
            // record saying converged:true beside drag_sign_consistent:false would be incoherent.
            boolean dragSignConsistent = Double.isFinite(finalDrag) && finalDrag > 0;
            boolean converged = residualsMet && Boolean.TRUE.equals(loadsSteady) && finiteState
                    && dragSignConsistent;

            double exportSeconds = (System.nanoTime() - exportBegan) / 1.0E9;
            long finalSaveBegan = System.nanoTime();
            sim.saveState(new File(dir, "external_flow_solved.sim").getAbsolutePath());
            saveSeconds += (System.nanoTime() - finalSaveBegan) / 1.0E9;
            checkpointSaves++;
            StringBuilder body = new StringBuilder();
            body.append("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"").append(escape(regionName))
                    .append("\",\"body_wall\":\"").append(escape(wallName)).append("\"")
                    .append(",\"force_option\":\"").append(escape(forceOption)).append("\"")
                    .append(",\"force_option_readback\":\"").append(escape(forceOption)).append("\"")
                    .append(",\"force_report_name\":\"").append(escape(drag.getPresentationName())).append("\"")
                    .append(",\"force_report_parts\":").append(forceReportParts)
                    .append(",\"start_iteration\":").append(start).append(",\"end_iteration\":").append(last)
                    .append(",\"iterations_run\":").append(ran)
                    .append(",\"chunk\":").append(chunk)
                    .append(",\"iteration_cap\":").append(maxIterations)
                    .append(",\"wall_clock_budget_s\":").append(budgetSeconds > 0 ? json(budgetSeconds) : "null")
                    .append(",\"wall_clock_stop_enabled\":").append(budgetSeconds > 0)
                    .append(",\"solve_seconds\":").append(json(seconds))
                    .append(",\"setup_seconds\":").append(json(setupSeconds))
                    .append(",\"iterator_seconds\":").append(json(iteratorSeconds))
                    .append(",\"monitor_export_seconds\":").append(json(exportSeconds))
                    .append(",\"checkpoint_save_seconds\":").append(json(saveSeconds))
                    .append(",\"checkpoint_saves\":").append(checkpointSaves)
                    .append(",\"checkpoint_every_chunks\":").append(checkpointEvery)
                    .append(",\"macro_elapsed_before_record_seconds\":").append(json((System.nanoTime() - began) / 1.0E9))
                    .append(",\"solver_budget_basis\":\"").append(escape(budgetBasis)).append("\"")
                    .append(",\"budget_check_scope\":\"checked at chunk boundaries; a chunk/save can overshoot; runner process and exit limits are separate\"")
                    .append(",\"stop_reason\":\"").append(escape(stopReason)).append("\"")
                    .append(",\"finite_state\":").append(finiteState)
                    .append(",\"residual_monitor_files\":").append(residualFiles)
                    .append(",\"report_monitor_files\":").append(reportFiles)
                    .append(",\"residual_rows\":").append(residualRows)
                    .append(",\"drag_monitor_created\":").append(monitorCreated)
                    .append(",\"measured_drag_x_n_at_load\":").append(json(loadDrag))
                    .append(",\"measured_drag_x_n_final\":").append(json(finalDrag))
                    .append(",\"measured_mass_imbalance_at_load\":").append(json(loadImbalance))
                    .append(",\"measured_mass_imbalance_final\":").append(json(finalImbalance))
                    .append(",\"measured_density_volume_average_kg_m3\":").append(json(density))
                    .append(",\"inflow_speed_m_s\":").append(json(speed))
                    .append(",\"reference_area_m2\":").append(json(referenceArea))
                    .append(",\"measured_drag_x_n_abs\":").append(json(Math.abs(finalDrag)))
                    .append(",\"drag_sign_consistent\":").append(dragSignConsistent)
                    .append(",\"measured_drag_coefficient_estimate\":").append(json(coefficient))
                    .append(",\"drag_coefficient_basis\":\"2*Fx/(rho*U^2*A) with Fx measured on the body wall, "
                            + "rho the volume-averaged Density of the region and U, A from the CaseSpec; this is a "
                            + "derived estimate, not a measurement of a reference value\"")
                    .append(",\"drag_history_samples\":").append(historyRows(history.toString()))
                    .append(",\"drag_window_size\":").append(historyWindow(history.toString()))
                    .append(",\"force_window_rel_change_gate\":").append(json(forceWindowRelChange))
                    .append(",\"mass_imbalance_gate\":").append(json(imbalanceLimit))
                    .append(",\"drag_window_source\":\"chunk_history\"")
                    .append(",\"residual_gates\":{").append(residualDetail).append("}")
                    .append(",\"residuals_met\":").append(residualsMet)
                    .append(",\"target_quantities_steady\":").append(loadsSteady == null ? "null" : loadsSteady.toString())
                    .append(",\"converged\":").append(converged)
                    .append(",\"convergence_basis\":\"converged = every CaseSpec residual gate met by the "
                            + "WORST matching monitor final value AND target_quantities_steady true AND all "
                            + "target quantities finite AND the measured body force is in the drag direction "
                            + "(drag_sign_consistent). target_quantities_steady = the last ")
                    .append(historyWindow(history.toString()))
                    .append(" chunk-history samples within ")
                    .append(json(forceWindowRelChange))
                    .append(" relative of their mean AND the worst mass imbalance in that window below ")
                    .append(json(imbalanceLimit))
                    .append("; it is null (unmeasured) when the window holds fewer than three samples. "
                            + "These are process gates, not track-P requirements\"")
                    .append(",\"checkpoint\":\"external_flow_solved.sim\"")
                    .append(",\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
            writeText(evidence, body.toString());
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            // A partial write leaves a marker-less file: repair it rather than leaving a truncated
            // record that a reader could mistake for evidence (review round 8, concern C11).
            boolean partial = true;
            try {
                partial = !evidence.isFile() || !readText(evidence).contains(COMPLETE);
            } catch (Throwable ignored) {
                partial = true;
            }
            if (partial) {
                try {
                    writeText(evidence, "{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                            + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                            + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                            + ",\"error\":\"" + escape(String.valueOf(failure)) + "\",\"complete_marker\":null}\n");
                } catch (Throwable ignored) {
                    // best effort only: the failure record is a courtesy, the exception is the result
                }
            }
            throw new RuntimeException("External-flow solve failed", failure);
        }
    }

    private static double imbalance(Map<String, ScalarReport> flows) throws Exception {
        double sum = 0.0;
        double scale = 0.0;
        for (ScalarReport report : flows.values()) {
            double value = report.getReportMonitorValue();
            if (!Double.isFinite(value)) return Double.NaN;
            sum += value;
            scale = Math.max(scale, Math.abs(value));
        }
        return scale > 0 ? Math.abs(sum) / scale : 0.0;
    }

    private static double volumeAverageDensity(Simulation sim, FvRepresentation mesh, Region region)
            throws Exception {
        FieldFunction density = null;
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if ("Density".equals(candidate.getFunctionName())) {
                density = candidate;
                break;
            }
        }
        if (density == null) return Double.NaN;
        VolumeAverageReport report = sim.getReportManager().createReport(VolumeAverageReport.class);
        try {
            report.setRepresentation(mesh);
            report.setFieldFunction(density);
            report.getParts().setObjects(region);
            return report.getReportMonitorValue();
        } finally {
            try {
                sim.deleteObjects(java.util.Collections.singletonList(report));
            } catch (Throwable ignored) {
                // best effort
            }
        }
    }

    private static int exportMonitors(Simulation sim, File dir, boolean residuals) {
        int written = 0;
        for (Monitor monitor : sim.getMonitorManager().getObjects()) {
            if (residuals != (monitor instanceof ResidualMonitor)) continue;
            String prefix = residuals ? "residual-" : "monitor-";
            String name = prefix + monitor.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_");
            try {
                if (monitor instanceof ResidualMonitor)
                    ((ResidualMonitor) monitor).export(new File(dir, name + ".csv").getAbsolutePath());
                else if (monitor instanceof ReportMonitor)
                    ((ReportMonitor) monitor).export(new File(dir, name + ".csv").getAbsolutePath());
                written++;
            } catch (Throwable failure) {
                System.out.println("Monitor export failed for " + monitor.getPresentationName() + ": "
                        + failure.getMessage());
            }
        }
        return written;
    }

    // Installed 2502 API exposes selection on the option object, not ForceReport.setForceOption.
    private static String setForceOption(ForceReport report, ForceReportForceOption.Type requested) {
        report.getForceOption().setSelected(requested);
        ForceReportForceOption.Type readback = report.getForceOption().getSelectedElement();
        if (readback != requested || readback != ForceReportForceOption.Type.PRESSURE_AND_SHEAR)
            throw new IllegalStateException("Force option readback differs from PRESSURE_AND_SHEAR: " + readback);
        return readback.name();
    }

    private static String forceReportParts(ForceReport report) {
        StringBuilder parts = new StringBuilder("[");
        for (NamedObject part : report.getParts().getObjects()) {
            if (parts.length() > 1) parts.append(',');
            parts.append('"').append(escape(part.getPresentationName())).append('"');
        }
        return parts.append(']').toString();
    }

    /** Merge the residual exports into one predictable table: kind, name, iteration, value. */
    private static int mergeResiduals(File dir, File destination) throws Exception {
        File[] files = dir.listFiles(new FilenameFilter() {
            @Override public boolean accept(File folder, String name) {
                return name.startsWith("residual-") && name.endsWith(".csv");
            }
        });
        Arrays.sort(files == null ? new File[0] : files);
        StringBuilder out = new StringBuilder("kind,name,iteration,value\n");
        int rows = 0;
        for (File file : files) {
            String label = file.getName().substring("residual-".length(),
                    file.getName().length() - ".csv".length());
            BufferedReader reader = new BufferedReader(new FileReader(file));
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] parts = line.split(",");
                    if (parts.length < 2) continue;
                    String a = parts[0].trim().replace("\"", "");
                    String b = parts[1].trim().replace("\"", "");
                    try {
                        double iteration = Double.parseDouble(a);
                        double value = Double.parseDouble(b);
                        out.append("residual,").append(label).append(',')
                                .append((long) iteration).append(',').append(value).append('\n');
                        rows++;
                    } catch (NumberFormatException notANumber) {
                        // header row
                    }
                }
            } finally {
                reader.close();
            }
        }
        writeText(destination, out.toString());
        return rows;
    }

    /** final value and the mean of the last and previous fifth, per residual name. */
    private static Map<String, double[]> residualStats(File table) throws Exception {
        Map<String, List<Double>> series = new LinkedHashMap<String, List<Double>>();
        BufferedReader reader = new BufferedReader(new FileReader(table));
        try {
            String line = reader.readLine();
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length != 4) continue;
                List<Double> values = series.get(parts[1]);
                if (values == null) {
                    values = new ArrayList<Double>();
                    series.put(parts[1], values);
                }
                values.add(Double.parseDouble(parts[3]));
            }
        } finally {
            reader.close();
        }
        Map<String, double[]> stats = new LinkedHashMap<String, double[]>();
        for (Map.Entry<String, List<Double>> entry : series.entrySet()) {
            List<Double> values = entry.getValue();
            int n = values.size();
            if (n == 0) continue;
            int window = Math.max(1, n / 5);
            double lastMean = mean(values, n - window, n);
            double previousMean = n >= 2 * window ? mean(values, n - 2 * window, n - window) : Double.NaN;
            stats.put(entry.getKey(), new double[] {values.get(n - 1), lastMean, previousMean, n});
        }
        return stats;
    }

    private static double mean(List<Double> values, int from, int to) {
        double sum = 0.0;
        int count = 0;
        for (int i = Math.max(0, from); i < Math.min(values.size(), to); i++) {
            sum += values.get(i);
            count++;
        }
        return count > 0 ? sum / count : Double.NaN;
    }

    /**
     * Worst matching component as {final, lastWindowMean, previousWindowMean}, or null when nothing
     * matches. The order is the SAME as residualStats returns ({final,lastMean,previousMean,n}), and it
     * is spelled out here because an earlier revision mixed the two up and gated on the previous window
     * mean while writing it under the key `final` (independent review round 8, blocker 3).
     */
    private static double[] worstWindow(Map<String, double[]> stats, String gate) {
        double[] worst = null;
        for (Map.Entry<String, double[]> entry : stats.entrySet()) {
            if (!entry.getKey().toLowerCase(Locale.ROOT).contains(gate)) continue;
            double[] window = entry.getValue();
            if (!Double.isFinite(window[0])) return null;
            if (worst == null || window[0] > worst[0]) worst = window;
        }
        return worst == null ? null : new double[] {worst[0], worst[1], worst[2]};
    }


    /**
     * Target-quantity stability from the chunk history: the last fifth of the drag samples must lie
     * within `tolerance` (relative) of their mean, and the worst imbalance in that window must be at
     * or below `imbalanceLimit`. Returns null - "not measurable" - when the window holds fewer than
     * three samples, because with one or two samples the spread test is vacuous.
     */
    private static Boolean stability(String history, double tolerance, double imbalanceLimit) {
        String[] lines = history.split("\n");
        List<Double> drag = new ArrayList<Double>();
        List<Double> imbalance = new ArrayList<Double>();
        for (int i = 1; i < lines.length; i++) {
            String[] parts = lines[i].split(",");
            if (parts.length != 4) continue;
            try {
                drag.add(Double.parseDouble(parts[2]));
                imbalance.add(Double.parseDouble(parts[3]));
            } catch (NumberFormatException ignored) {
                // non-finite rows are recorded as NaN by finite()
            }
        }
        if (drag.size() < 3) return null;
        int window = Math.max(1, drag.size() / 5);
        if (window < 3) window = Math.min(3, drag.size());
        if (window < 3) return null;                 // still not measurable: report null, never a claim
        double mean = mean(drag, drag.size() - window, drag.size());
        if (!(Math.abs(mean) > 0)) return null;
        boolean dragSteady = true;
        for (int i = drag.size() - window; i < drag.size(); i++) {
            double value = drag.get(i);
            if (!Double.isFinite(value) || Math.abs(value - mean) > tolerance * Math.abs(mean))
                dragSteady = false;
        }
        double worst = 0.0;
        for (int i = imbalance.size() - window; i < imbalance.size(); i++) {
            if (i < 0) continue;
            if (!Double.isFinite(imbalance.get(i))) return null;
            worst = Math.max(worst, imbalance.get(i));
        }
        return dragSteady && worst <= imbalanceLimit;
    }

    /**
     * Find a report by presentation name, or null when it is absent.
     *
     * MEASURED IN ROUND 8, not assumed: `ReportManager.getReport(name)` THROWS
     * `NeoException: Named object "<name>" does not exist in manager` when the report is absent - it does
     * not return null. The first solve trial therefore failed 18.54 s in, before a single iteration
     * (`s1-static-sphere-solve-validation-20261003c`, external_flow_solve.json). This iterates the
     * manager's objects and matches on the name, which is the pattern the already-executed
     * RunAir3bMrfSegment uses for monitors for the same reason. No broad catch: an absent report is a
     * normal state here, and every other failure must still propagate.
     */
    private static Report findReport(Simulation sim, String name) {
        for (Report candidate : sim.getReportManager().getObjects()) {
            if (name.equals(candidate.getPresentationName())) return candidate;
        }
        return null;
    }

    private static void deleteReport(Simulation sim, String name) {
        Report existing = findReport(sim, name);
        if (existing == null) return;
        try {
            sim.deleteObjects(java.util.Collections.singletonList(existing));
        } catch (Throwable failure) {
            System.out.println("Could not delete pre-existing report " + name + ": " + failure.getMessage());
        }
    }

    /** Drag history sample count and window size, so a stability claim can be sized up. */
    private static int historyRows(String history) {
        String[] lines = history.split("\n");
        return Math.max(0, lines.length - 1);
    }

    private static int historyWindow(String history) {
        int rows = historyRows(history);
        if (rows < 3) return 0;
        return Math.max(3, Math.min(rows, Math.max(1, rows / 5)));
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation representation : sim.getRepresentationManager().getObjects()) {
            if (representation instanceof FvRepresentation) return (FvRepresentation) representation;
        }
        return null;
    }

    private static String safe(String value) {
        return value.replaceAll("[^A-Za-z0-9_-]", "_");
    }

    private static double[] array(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^\\]]*)\\]")
                .matcher(text);
        if (!match.find()) throw new IllegalArgumentException("Missing array " + key + " in CaseSpec");
        String[] parts = match.group(1).split(",");
        double[] values = new double[parts.length];
        for (int i = 0; i < parts.length; i++) values[i] = Double.parseDouble(parts[i].trim());
        return values;
    }

    private static double sumSquares(double[] values) {
        double sum = 0.0;
        for (double value : values) sum += value * value;
        return sum;
    }

    private static double finite(double value) {
        return Double.isFinite(value) ? value : Double.NaN;
    }

    private static String json(double value) {
        return Double.isFinite(value) ? Double.toString(value) : "null";
    }

    private static String string(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(text);
        if (!match.find()) throw new IllegalArgumentException("Missing " + key + " in CaseSpec");
        return match.group(1);
    }

    private static String optional(String text, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(text);
        return match.find() ? match.group(1) : fallback;
    }

    private static double number(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(text);
        if (!match.find()) throw new IllegalArgumentException("Missing " + key + " in CaseSpec");
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double optionalNumber(String text, String key, double fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(text);
        if (!match.find()) return fallback;
        try {
            return Double.parseDouble(match.group(1));
        } catch (NumberFormatException notANumber) {
            return fallback;
        }
    }

    private static double env(String name, double fallback) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) return fallback;
        return Double.parseDouble(value);
    }

    private static String readText(File file) throws Exception {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    private static double ratio(double previous, double current) {
        if (!Double.isFinite(previous) || !Double.isFinite(current) || current == 0) return Double.NaN;
        return previous / current;
    }

    private static void writeText(File file, String text) throws Exception {
        FileWriter writer = new FileWriter(file, StandardCharsets.UTF_8);
        try {
            writer.write(text);
        } finally {
            writer.close();
        }
    }

    private static String escape(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
