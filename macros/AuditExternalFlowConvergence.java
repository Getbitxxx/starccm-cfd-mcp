/* Stage 2 of the external-flow solve: re-measure convergence, mass balance and target-quantity
 * stability on the SAVED solution, in a fresh session, and state honestly what is and is not validated.
 *
 * WHY A SEPARATE READ-ONLY MACRO: the macro that ran the solver also decided whether it converged.
 * That is the same "builder grades its own homework" shape that the geometry and mesh stages were
 * split to avoid. This macro never initializes, iterates or saves; it re-exports STAR's residual
 * monitor history from the reloaded .sim, recomputes the mass flow on every boundary and the force on
 * the body wall, and re-derives the convergence verdict from the CaseSpec gates.
 *
 * REFERENCE-VALUE POLICY: the reference lives in the CaseSpec
 * (engineering_target.drag_coefficient) and this macro reads it rather than assuming it. A reference
 * counts as usable when it carries a finite value AND either status=ESTABLISHED or an explicit
 * reference_established=true; then a relative error is reported and the policy sentence names the
 * source and the uncertainty basis the CaseSpec declares. Without a usable reference no relative
 * error is claimed at all: the estimate is recorded, the reference is recorded as NOT_ESTABLISHED and
 * the verdict says NOT_VALIDATED_NO_REFERENCE. Inventing a baseline to produce a percentage is exactly
 * the failure this repository's evidence rules exist to prevent. The sentence is DERIVED, never a
 * fixed string, because a fixed "NOT_ESTABLISHED" wording becomes false the first time a CaseSpec
 * supplies a usable reference (independent review, blocker B1).
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
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.ClientServerObject;
import star.base.neo.NamedObject;
import star.base.report.*;
import star.common.*;
import star.flow.*;

public class AuditExternalFlowConvergence extends StarMacro {
    private static final String COMPLETE = "EXTERNAL_FLOW_VALIDATION_COMPLETE";
    private static final String DRAG = "EXTERNAL_FLOW_DRAG_X";
    // The drag report is a +X force report; its direction is asserted in the record so a reused report
    // whose direction was changed cannot pass as the same measurement.
    private static final String DRAG_DIRECTION_UNIT = "1,0,0";
    private static final double DRAG_DIRECTION_TOLERANCE = 1.0E-9;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_validation.json");
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String stage1ForceOption = requirePressureAndShear(new String(Files.readAllBytes(
                    new File(dir, "external_flow_solve.json").toPath()), StandardCharsets.UTF_8));
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String wallName = string(spec, "body_wall_boundary_name");
            double speed = Math.sqrt(sumSquares(array(spec, "velocity_m_s")));
            double referenceArea = number(spec, "reference_area_m2");
            double forceWindowRelChange = optionalNumber(spec, "force_window_rel_change", 0.01);
            double imbalanceLimit = optionalNumber(spec, "mass_imbalance_ratio_limit", 1.0E-3);
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            if (iteration <= 0)
                throw new IllegalStateException("Validation requires a solved state; iteration is " + iteration);
            Region region = sim.getRegionManager().getRegion(regionName);
            if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
            Boundary wall = region.getBoundaryManager().getBoundary(wallName);
            if (wall == null) throw new IllegalStateException("Body wall boundary missing: " + wallName);
            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("No volume mesh in the loaded state");

            // Mass balance is recomputed here rather than read from stage 1: for a closed domain the
            // SIGNED sum over every outer boundary must vanish, and taking absolute values would hide
            // a boundary that flows the wrong way.
            double flowSum = 0.0;
            double flowScale = 0.0;
            int flowCount = 0;
            StringBuilder flowDetail = new StringBuilder();
            for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                // Audit-specific name: stage 1 created EXTERNAL_FLOW_MASS_<boundary> reports and saved them
                // into the checkpoint this stage reads, so reusing that name would collide with an existing
                // object. These are temporary and deleted in the finally block.
                MassFlowReport report = (MassFlowReport) sim.getReportManager().create("star.flow.MassFlowReport");
                report.setPresentationName("EXTERNAL_FLOW_AUDIT_MASS_" + safe(boundary.getPresentationName()));
                temporary.add(report);
                report.setUseIdealization(false);
                report.setRepresentation(mesh);
                report.getParts().setObjects(boundary);
                double value = report.getReportMonitorValue();
                if (!Double.isFinite(value))
                    throw new IllegalStateException("Non-finite mass flow on " + boundary.getPresentationName());
                flowSum += value;
                flowScale = Math.max(flowScale, Math.abs(value));
                flowCount++;
                flowDetail.append(flowDetail.length() == 0 ? "" : ",")
                        .append("{\"boundary\":\"").append(escape(boundary.getPresentationName()))
                        .append("\",\"measured_mass_flow_kg_s\":").append(json(value)).append('}');
            }
            if (flowCount < 3) throw new IllegalStateException("Mass balance needs at least three outer boundaries");
            // A zero-flow state would otherwise divide into "balanced" (review round 8, C8): the ratio
            // is only meaningful when at least one boundary carries flow.
            boolean flowMeasurable = flowScale > 0;
            double imbalance = flowMeasurable ? Math.abs(flowSum) / flowScale : Double.NaN;

            // Same measured lesson as stage 1: getReport(name) throws when the object is absent. Reuse
            // stage 1's saved drag report when it is there (and do NOT add it to `temporary`, so a
            // read-only audit does not delete the solver stage's report), otherwise create one.
            //
            // The REUSED report is verified, not trusted (round 9 review, carried concern (a)): the old
            // code kept whatever object merely carried the name, so a report bound to the wrong parts or
            // a stale direction would silently redefine the force that this stage re-measures and the
            // whole validation verdict would be computed from it. A report whose parts are not exactly
            // {body wall} or whose direction is not +X is NOT adopted; a fresh temporary report is
            // created instead and the substitution is recorded. `temporary` deletion is what makes the
            // substitution read-only, so on a frozen .sim the reuse path and the fallback measure the
            // same quantity.
            ForceReport drag = null;
            boolean dragPartsVerified = false;
            for (Report candidate : sim.getReportManager().getObjects()) {
                if (DRAG.equals(candidate.getPresentationName()) && candidate instanceof ForceReport
                        && dragReportVerified((ForceReport) candidate, wall)) {
                    drag = (ForceReport) candidate;
                    dragPartsVerified = true;
                    break;
                }
            }
            boolean dragWasInherited = drag != null;
            if (drag == null) {
                dragPartsVerified = false;
                drag = (ForceReport) sim.getReportManager().create("star.flow.ForceReport");
                temporary.add(drag);
                drag.setPresentationName(DRAG);
                drag.setUseIdealization(false);
                drag.getParts().setObjects(new NamedObject[] {wall});
                drag.getDirection().setComponentsAndUnits(1.0, 0.0, 0.0, sim.getUnitsManager().getObject(""));
            }
            double force = drag.getReportMonitorValue();
            if (!Double.isFinite(force)) throw new IllegalStateException("Non-finite body-wall force");
            double density = volumeAverageDensity(sim, mesh, region, temporary);
            double coefficient = Double.NaN;
            if (Double.isFinite(density) && density > 0 && referenceArea > 0 && speed > 0)
                coefficient = 2.0 * force / (density * speed * speed * referenceArea);

            // Residual history: STAR keeps the monitor history in the saved .sim, so re-exporting it
            // here is an independent read of the same solver record, not a copy of stage 1's file.
            int exported = exportResiduals(sim, dir);
            File table = new File(dir, "external_flow_validation_residuals.tsv");
            int rows = mergeMonitors(dir, table);
            if (rows <= 0) throw new IllegalStateException("No residual history in the saved state to validate");
            Map<String, double[]> stats = residualStats(table);

            Map<String, Double> gates = new LinkedHashMap<String, Double>();
            for (String key : new String[] {"continuity", "momentum", "energy"}) {
                double value = optionalNumber(spec, key, Double.NaN);
                if (Double.isFinite(value)) gates.put(key, value);
            }
            if (gates.isEmpty()) throw new IllegalStateException("CaseSpec declares no residual gates");
            boolean residualsMet = true;
            StringBuilder residualDetail = new StringBuilder();
            for (Map.Entry<String, Double> gate : gates.entrySet()) {
                // The worst matching component, not the first match: STAR splits momentum into
                // X/Y/Z-momentum, and a first-match gate let one component exceed it unnoticed.
                double[] series = gateSeries(stats, gate.getKey());
                double value = series == null ? Double.NaN : series[0];
                double lastMean = series == null ? Double.NaN : series[1];
                double previousMean = series == null ? Double.NaN : series[2];
                boolean met = Double.isFinite(value) && value <= gate.getValue();
                residualsMet = residualsMet && met;
                residualDetail.append(residualDetail.length() == 0 ? "" : ",")
                        .append('"').append(gate.getKey()).append("\":{\"final\":").append(json(value))
                        .append(",\"last_window_mean\":").append(json(lastMean))
                        .append(",\"previous_window_mean\":").append(json(previousMean))
                        .append(",\"decay_ratio\":").append(json(ratio(previousMean, lastMean)))
                        .append(",\"gate\":").append(json(gate.getValue()))
                        .append(",\"met\":").append(met).append('}');
            }

            // The drag stability window. ReportMonitor creation FAILS on this install
            // ("Unknown type to create: star.base.report.ReportMonitor", measured by the other branch in
            // test-evidence/air-three-blade/.../seg01-result.json and in five star-segment.log files), so
            // the per-iteration monitor series usually does not exist. The fallback is stage 1's chunk
            // history, which is a declared `required` artefact of this same job directory. Without this
            // fallback the verdict was a foregone NOT_CONVERGED that asserted a quantity was not steady
            // while holding ZERO samples (independent review round 8, blocker 1).
            double[] dragHistory = series(table, "monitor", "EXTERNAL_FLOW_MON_DRAG_X");
            String dragWindowSource = dragHistory != null && dragHistory.length > 0
                    ? "report_monitors" : "none";
            if (dragHistory == null || dragHistory.length == 0) {
                double[] fromHistory = chunkHistoryDrag(new File(dir, "external_flow_solve_history.csv"));
                if (fromHistory != null && fromHistory.length > 0) {
                    dragHistory = fromHistory;
                    dragWindowSource = "chunk_history";
                }
            }
            double[] dragStats = windowStats(dragHistory);
            Boolean loadsSteady = null;
            if (dragStats != null && dragStats[4] >= 3.0 && Double.isFinite(dragStats[0]) && Math.abs(dragStats[0]) > 0
                    && Double.isFinite(dragStats[3])) {
                loadsSteady = dragStats[3] <= forceWindowRelChange;
            }
            boolean massBalanced = flowMeasurable && imbalance <= imbalanceLimit;

            // The reference policy, read from the CaseSpec rather than assumed.
            String[] reference = referenceBlock(spec);
            String referenceStatus = reference[0];
            double referenceValue = reference[1] == null ? Double.NaN : Double.parseDouble(reference[1]);
            double referenceTolerance = reference[2] == null ? Double.NaN : Double.parseDouble(reference[2]);
            // A CaseSpec may express this either as status=ESTABLISHED or with the explicit
            // reference_established flag; one field should not have to carry both meanings
            // (independent review, blocker B1).
            boolean referenceEstablishedFlag = "true".equalsIgnoreCase(optional(spec, "reference_established", "false"));
            boolean referenceUsable = Double.isFinite(referenceValue)
                    && (referenceStatus.equalsIgnoreCase("ESTABLISHED") || referenceEstablishedFlag);
            double relativeError = referenceUsable && Math.abs(referenceValue) > 0
                    ? Math.abs(coefficient - referenceValue) / Math.abs(referenceValue) : Double.NaN;
            // Cross-check against stage 1 instead of ignoring it: the two stages must agree about the
            // same solution, and a disagreement is recorded rather than averaged away.
            double stage1Force = Double.NaN;
            double stage1Imbalance = Double.NaN;
            // Default false: a missing stage-1 record must not assume the good value.
            boolean stage1FiniteState = false;
            String stage1StopReason = "unknown";
            File solveRecord = new File(dir, "external_flow_solve.json");
            if (solveRecord.isFile()) {
                String solveText = new String(Files.readAllBytes(solveRecord.toPath()), StandardCharsets.UTF_8);
                stage1Force = optionalNumber(solveText, "measured_drag_x_n_final", Double.NaN);
                stage1Imbalance = optionalNumber(solveText, "measured_mass_imbalance_final", Double.NaN);
                stage1FiniteState = solveText.contains("\"finite_state\":true");
                stage1StopReason = optional(solveText, "stop_reason", "unknown");
            }
            // The policy sentence is DERIVED, not hardcoded: a fixed "NOT_ESTABLISHED and no relative
            // error is claimed" string became false the first time a CaseSpec supplied a usable
            // reference (review blocker B1). The wording names no case: the source and the uncertainty
            // basis are read from the CaseSpec.
            String referencePolicy = referenceUsable
                    ? "reference provided by the CaseSpec: " + optional(spec, "reference_source", "source not named")
                      + "; " + optional(spec, "reference_uncertainty_basis", "uncertainty basis not named")
                      + "; relative_error_against_reference is a comparison against that value, not a measurement of it"
                    : "no verified drag-coefficient source exists in this repository, so the reference is "
                      + "recorded as NOT_ESTABLISHED and no relative error is claimed";
            double forceDelta = Double.isFinite(stage1Force)
                    ? Math.abs(force - stage1Force) / Math.max(Math.abs(stage1Force), 1.0E-12) : Double.NaN;
            double imbalanceDelta = Double.isFinite(stage1Imbalance)
                    ? Math.abs(imbalance - stage1Imbalance) : Double.NaN;
            boolean stage1Agrees = Double.isFinite(forceDelta) && forceDelta <= 1.0E-6
                    && Double.isFinite(imbalanceDelta) && imbalanceDelta <= 1.0E-9;

            // read_only is not self-declared any more: the same bytes must appear in the stage record
            // the runner wrote before this stage started. Independent review round 8, blocker 4.
            //
            // WHICH file to hash is read from the session, not assumed from a name. This macro used to
            // hard-code `external_flow_solved.sim`, the single-directory end-to-end name. The formal
            // `submit_run` path runs every stage in its own directory and stages the declared input as
            // `input.sim` (measured in round 13: the run request named `external_flow_solved.sim` and the
            // job directory contained `input.sim`), so the hard-coded name threw FileNotFoundException
            // BEFORE any criterion was judged - a second crash of the same class as the windowStats one.
            // `star.common.RootObject.getSessionPath()` is the installed-2502 API for "the file this
            // session was opened from", so the audit now hashes exactly the bytes it read, whatever the
            // runner called them. The historical name remains as a fallback so a session whose path is
            // unavailable keeps its previous behaviour rather than skipping the check.
            File openedSim = null;
            String sessionPath = sim.getSessionPath();
            if (sessionPath != null && !sessionPath.isEmpty()) {
                File candidate = new File(sessionPath);
                if (candidate.isFile()) openedSim = candidate;
            }
            if (openedSim == null) openedSim = new File(dir, "external_flow_solved.sim");
            String inputSha = sha256(openedSim);
            String inputSimName = openedSim.getName();
            // read_only is bound to the DECLARED SOLVE OUTPUT, not to "any 64-hex string in the record".
            //
            // The first version scanned the whole record for any 64-hex value and accepted the run if one
            // of them equalled the input .sim's hash. A record that merely MENTIONS that hash somewhere
            // else - an input hash, a macro hash, a runner digest - would therefore have been accepted as
            // proof that the audit read the solve stage's output. Round 15 binds the check to the explicit
            // `output_sha256` field AND to a ROOT-level read of it (rootString): `optional()` is a
            // first-match regex, and the runner emits `macros:[{... "output_sha256" ...}]` before the
            // top-level field, so a first-match read bound the proof to the first PER-MACRO output hash.
            // The round-15 review measured that the counter-example record therefore verified as read-only.
            // It also requires the record to name a .sim output identity, so "only input_sha256 matches,
            // output_sha256 does not" is a refusal instead of a pass.
            String stageOutputSha = null;
            String stageOutputName = null;
            int stageRecordHashes = 0;
            String sourceStageRecord = System.getenv("EXTERNAL_FLOW_SOURCE_SOLVE_STAGE_RECORD");
            if (sourceStageRecord == null || sourceStageRecord.isEmpty()) sourceStageRecord = "stage-solve.json";
            if (!sourceStageRecord.matches("[A-Za-z0-9_.-]+\\.json"))
                throw new IllegalArgumentException("Source solve stage record must be a JSON basename");
            File stageRecord = new File(dir, sourceStageRecord);
            if (stageRecord.isFile()) {
                String record = new String(Files.readAllBytes(stageRecord.toPath()), StandardCharsets.UTF_8);
                Matcher hashes = Pattern.compile("\"([0-9a-f]{64})\"").matcher(record);
                while (hashes.find()) stageRecordHashes++;
                stageOutputSha = rootString(record, "output_sha256", null);
                stageOutputName = rootString(record, "output", null);
            }
            boolean outputIdentityIsSim = stageOutputName != null
                    && stageOutputName.toLowerCase(Locale.ROOT).endsWith(".sim");
            boolean readOnlyVerified = outputIdentityIsSim && inputSha.equals(stageOutputSha);
            // A stage 1 that recorded a non-finite target quantity cannot be certified here: the audit
            // must not overrule its honest flag (review round 8, blocker 5).
            if (!stage1FiniteState) loadsSteady = null;
            boolean dragSignConsistent = force > 0;                 // +X flow => +X force on the body
            boolean converged = residualsMet && Boolean.TRUE.equals(loadsSteady) && massBalanced
                    && stage1Agrees && readOnlyVerified && stage1FiniteState && dragSignConsistent;

            String verdict;
            if (!converged) verdict = loadsSteady == null ? "NOT_CONVERGED_UNMEASURED" : "NOT_CONVERGED";
            else verdict = referenceComparisonVerdict(referenceUsable, relativeError, referenceTolerance);

            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"read_only\":" + readOnlyVerified
                        + ",\"region\":\"" + escape(regionName) + "\""
                        + ",\"body_wall\":\"" + escape(wallName) + "\""
                        + ",\"iteration\":" + iteration
                        + ",\"cells\":" + mesh.getCellCount()
                        + ",\"input_sim_sha256\":\"" + inputSha + "\""
                        + ",\"input_sim_name\":\"" + escape(inputSimName) + "\""
                        + ",\"read_only_verified\":" + readOnlyVerified
                        + ",\"stage_record\":\"" + escape(sourceStageRecord) + "\""
                        + ",\"stage_record_hashes_seen\":" + stageRecordHashes
                        + ",\"stage_record_output\":\"" + escape(stageOutputName == null ? "" : stageOutputName) + "\""
                        + ",\"stage_record_output_sha256\":\""
                        + escape(stageOutputSha == null ? "" : stageOutputSha) + "\""
                        + ",\"read_only_binding\":\"input .sim sha256 == source solve stage record output_sha256 and "
                        + "source solve stage record output names a .sim\""
                        + ",\"stage1_finite_state\":" + stage1FiniteState
                        + ",\"stage1_stop_reason\":\"" + escape(stage1StopReason) + "\""
                        + ",\"force_option_checked\":\"" + escape(stage1ForceOption) + "\""
                        + ",\"force_option_check_passed\":true"
                        + ",\"drag_sign_consistent\":" + dragSignConsistent
                        + ",\"drag_report_inherited_from_solve\":" + dragWasInherited
                        + ",\"drag_report_parts_verified\":" + dragPartsVerified
                        + ",\"drag_report_direction_unit\":\"" + escape(DRAG_DIRECTION_UNIT) + "\""
                        + ",\"residual_export_files\":" + exported
                        + ",\"residual_rows\":" + rows
                        + ",\"outer_boundaries_measured\":" + flowCount
                        + ",\"flow_measurable\":" + flowMeasurable
                        + ",\"measured_mass_flow_signed_sum_kg_s\":" + json(flowSum)
                        + ",\"measured_mass_imbalance_ratio\":" + json(imbalance)
                        + ",\"measured_mass_imbalance_gate\":" + json(imbalanceLimit)
                        + ",\"mass_balanced\":" + massBalanced
                        + ",\"measured_drag_x_n\":" + json(force)
                        + ",\"measured_density_volume_average_kg_m3\":" + json(density)
                        + ",\"measured_drag_coefficient_estimate\":" + json(coefficient)
                        + ",\"drag_coefficient_basis\":\"2*Fx/(rho*U^2*A) recomputed from the saved state; a "
                        + "derived estimate\""
                        + ",\"drag_window_mean_n\":" + json(dragStats == null ? Double.NaN : dragStats[0])
                        + ",\"drag_window_relative_deviation\":" + json(dragStats == null ? Double.NaN : dragStats[3])
                        + ",\"drag_window_samples\":" + (dragStats == null ? 0 : (long) dragStats[4])
                        + ",\"drag_window_size\":" + (dragStats == null ? 0 : (long) dragStats[5])
                        + ",\"drag_window_source\":\"" + dragWindowSource + "\""
                        + ",\"target_quantities_steady\":" + (loadsSteady == null ? "null" : loadsSteady.toString())
                        + ",\"target_stability_gate\":" + json(forceWindowRelChange)
                        + ",\"stage1_force_relative_delta\":" + json(forceDelta)
                        + ",\"stage1_imbalance_absolute_delta\":" + json(imbalanceDelta)
                        + ",\"stage1_agrees\":" + stage1Agrees
                        + ",\"residual_gates\":{" + residualDetail + "}"
                        + ",\"residuals_met\":" + residualsMet
                        + ",\"converged\":" + converged
                        + ",\"reference_status\":\"" + escape(referenceStatus) + "\""
                        + ",\"reference_value\":" + (referenceUsable ? json(referenceValue) : "null")
                        + ",\"relative_error_against_reference\":" + (referenceUsable ? json(relativeError) : "null")
                        + ",\"reference_acceptance_tolerance\":" + json(referenceTolerance)
                        + ",\"engineering_verified\":false"
                        + ",\"reference_policy\":\"" + escape(referencePolicy) + "\""
                        + ",\"validation_verdict\":\"" + verdict + "\""
                        + ",\"mass_flow_by_boundary\":[" + flowDetail + "]"
                        + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            boolean partial = true;
            try {
                partial = !evidence.isFile() || !new String(Files.readAllBytes(evidence.toPath()),
                        StandardCharsets.UTF_8).contains(COMPLETE);
            } catch (Throwable ignored) {
                partial = true;
            }
            if (partial) {
                try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                    writer.write("{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                            + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                            + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                            + ",\"error\":\"" + escape(String.valueOf(failure)) + "\",\"complete_marker\":null}\n");
                } catch (Throwable ignored) {
                    // best effort only
                }
            }
            throw new RuntimeException("External-flow convergence audit failed", failure);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject object : temporary) {
                try {
                    sim.deleteObjects(Collections.singletonList(object));
                } catch (Throwable ignored) {
                    // best effort only
                }
            }
        }
    }

    private static String requirePressureAndShear(String solveRecord) {
        String option = rootString(solveRecord, "force_option", null);
        String readback = rootString(solveRecord, "force_option_readback", null);
        if (!"PRESSURE_AND_SHEAR".equals(option) || !option.equals(readback))
            throw new IllegalStateException("Solve force_option/readback must both be PRESSURE_AND_SHEAR");
        return option;
    }

    /**
     * True only if this stored report still measures the declared body wall along +X.
     *
     * Round 9 review, carried concern (a): the audit reused stage 1's saved drag report by NAME, so a
     * report bound to another boundary (or one whose direction had been edited) would have redefined
     * the force this stage reports as an independent re-measurement. An unverifiable report is not
     * adopted; the caller then builds a temporary report over the audited body wall.
     */
    private static boolean dragReportVerified(ForceReport report, Boundary wall) {
        // getParts().getObjects() returns Collection<NamedObject> on this install (measured: an Object[]
        // assignment is a compile error, "Collection<NamedObject>无法转换为Object[]"), so the membership
        // test is written against the collection the API actually hands back.
        Object single = null;
        int count = 0;
        for (Object part : report.getParts().getObjects()) {
            single = part;
            count++;
        }
        if (count != 1 || single != wall) return false;
        double[] direction = report.getDirection().getVector().toDoubleArray();
        if (direction == null || direction.length < 3) return false;
        return Math.abs(direction[0] - 1.0) <= DRAG_DIRECTION_TOLERANCE
                && Math.abs(direction[1]) <= DRAG_DIRECTION_TOLERANCE
                && Math.abs(direction[2]) <= DRAG_DIRECTION_TOLERANCE;
    }

    private static int exportResiduals(Simulation sim, File dir) {
        int written = 0;
        for (Monitor monitor : sim.getMonitorManager().getObjects()) {
            try {
                if (monitor instanceof ResidualMonitor) {
                    ((ResidualMonitor) monitor).export(new File(dir, "residual-"
                            + monitor.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_") + ".csv")
                            .getAbsolutePath());
                    written++;
                } else if (monitor instanceof ReportMonitor) {
                    ((ReportMonitor) monitor).export(new File(dir, "monitor-"
                            + monitor.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_") + ".csv")
                            .getAbsolutePath());
                    written++;
                }
            } catch (Throwable failure) {
                System.out.println("Monitor export failed for " + monitor.getPresentationName() + ": "
                        + failure.getMessage());
            }
        }
        return written;
    }

    /**
     * Merge the residual AND report-monitor exports into one table: kind, name, iteration, value.
     *
     * The first version merged only residual-*.csv, so the drag monitor series was absent from the
     * table, `series(table, "monitor-DRAG")` always returned null, target_quantities_steady was
     * therefore always false and the audit would have reported NOT_CONVERGED for every run - including
     * a converged one. Found by walking the data path rather than by reading the code.
     */
    private static int mergeMonitors(File dir, File destination) throws Exception {
        StringBuilder out = new StringBuilder("kind,name,iteration,value\n");
        int rows = 0;
        for (final String prefix : new String[] {"residual-", "monitor-"}) {
            String kind = prefix.substring(0, prefix.length() - 1);
            File[] files = dir.listFiles(new FilenameFilter() {
                @Override public boolean accept(File folder, String name) {
                    return name.startsWith(prefix) && name.endsWith(".csv");
                }
            });
            Arrays.sort(files == null ? new File[0] : files);
            for (File file : files) {
                String label = file.getName().substring(prefix.length(),
                        file.getName().length() - ".csv".length());
                BufferedReader reader = new BufferedReader(new FileReader(file));
                try {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        String[] parts = line.split(",");
                        if (parts.length < 2) continue;
                        try {
                            double iteration = Double.parseDouble(parts[0].trim().replace("\"", ""));
                            double value = Double.parseDouble(parts[1].trim().replace("\"", ""));
                            out.append(kind).append(',').append(label).append(',')
                                    .append((long) iteration).append(',').append(value).append('\n');
                            rows++;
                        } catch (NumberFormatException headerRow) {
                            // skip the header
                        }
                    }
                } finally {
                    reader.close();
                }
            }
        }
        try (FileWriter writer = new FileWriter(destination, StandardCharsets.UTF_8)) {
            writer.write(out.toString());
        }
        return rows;
    }

    /** kind,name,iteration,value -> per-name windows, keyed "<kind>:<name>". */
    private static Map<String, double[]> residualStats(File table) throws Exception {
        Map<String, List<Double>> series = new LinkedHashMap<String, List<Double>>();
        BufferedReader reader = new BufferedReader(new FileReader(table));
        try {
            String line = reader.readLine();
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length != 4) continue;
                String key = parts[0] + ":" + parts[1];
                List<Double> values = series.get(key);
                if (values == null) {
                    values = new ArrayList<Double>();
                    series.put(key, values);
                }
                values.add(Double.parseDouble(parts[3]));
            }
        } finally {
            reader.close();
        }
        Map<String, double[]> stats = new LinkedHashMap<String, double[]>();
        for (Map.Entry<String, List<Double>> entry : series.entrySet())
            stats.put(entry.getKey(), windowStats(entry.getValue()));
        return stats;
    }

    /**
     * {lastWindowMean, previousWindowMean, finalValue, relativeDeviation, sampleCount, windowSize}.
     *
     * relativeDeviation is max|value - mean| / |mean| over the window - the SAME metric stage 1 uses,
     * so the two records cannot disagree about the same solution (review round 8, concern C2). The
     * window is the last fifth, forced to at least three samples when that many exist, and the sample
     * and window counts are returned so a reader can size up the claim.
     */
    private static double[] windowStats(List<Double> values) {
        int n = values.size();
        if (n == 0) return new double[] {Double.NaN, Double.NaN, Double.NaN, Double.NaN, 0, 0};
        int window = Math.max(1, n / 5);
        if (window < 3) window = Math.min(3, n);
        double lastMean = mean(values, n - window, n);
        double previousMean = n >= 2 * window ? mean(values, n - 2 * window, n - window) : Double.NaN;
        double deviation = Double.NaN;
        if (Double.isFinite(lastMean) && Math.abs(lastMean) > 0) {
            deviation = 0.0;
            for (int i = n - window; i < n; i++) {
                double value = values.get(i);
                if (!Double.isFinite(value)) {
                    deviation = Double.NaN;
                    break;
                }
                deviation = Math.max(deviation, Math.abs(value - lastMean) / Math.abs(lastMean));
            }
        }
        return new double[] {lastMean, previousMean, values.get(n - 1), deviation, n, window};
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

    /** Worst matching residual component: max final value, and the means of that same component. */
    private static double[] gateSeries(Map<String, double[]> stats, String gate) {
        double[] worst = null;
        double worstFinal = Double.NaN;
        for (Map.Entry<String, double[]> entry : stats.entrySet()) {
            if (!entry.getKey().toLowerCase(Locale.ROOT).contains(gate)) continue;
            double[] window = entry.getValue();
            double value = window[2];
            if (!Double.isFinite(value)) return new double[] {Double.NaN, Double.NaN, Double.NaN};
            if (worst == null || value > worstFinal) {
                worst = window;
                worstFinal = value;
            }
        }
        return worst == null ? null : new double[] {worst[2], worst[0], worst[1]};
    }

    /** Drag samples from stage 1's chunk history (column 3), or null when the file is absent. */
    private static double[] chunkHistoryDrag(File history) throws Exception {
        if (!history.isFile()) return null;
        List<Double> values = new ArrayList<Double>();
        BufferedReader reader = new BufferedReader(new FileReader(history));
        try {
            String line = reader.readLine();
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length < 3) continue;
                try {
                    // Every parsed sample is kept, including NaN/Infinity (Double.parseDouble accepts
                    // both). Filtering non-finite samples out was a fail-open: the window then looked
                    // steady while stage 1 had recorded a divergence (review round 8, blocker 5).
                    values.add(Double.parseDouble(parts[2]));
                } catch (NumberFormatException notANumber) {
                    // unparsable text is skipped; a non-finite NUMBER is not
                }
            }
        } finally {
            reader.close();
        }
        if (values.isEmpty()) return null;
        double[] array = new double[values.size()];
        for (int i = 0; i < array.length; i++) array[i] = values.get(i);
        return array;
    }

    /** sha256 of the file this audit opened, so `read_only` can be checked against stage 1's output. */
    private static String sha256(File file) throws Exception {
        java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[1 << 20];
        java.io.FileInputStream stream = new java.io.FileInputStream(file);
        try {
            int read;
            while ((read = stream.read(buffer)) > 0) digest.update(buffer, 0, read);
        } finally {
            stream.close();
        }
        StringBuilder out = new StringBuilder();
        for (byte b : digest.digest()) out.append(String.format("%02x", b));
        return out.toString();
    }

    /** Block-scoped read of the reference from engineering_target.drag_coefficient (concern C1). */
    private static String[] referenceBlock(String spec) {
        String[] result = new String[] {"NOT_ESTABLISHED", null, null};
        int key = spec.indexOf("\"drag_coefficient\"");
        if (key < 0) return result;
        int open = spec.indexOf('{', key);
        if (open < 0) return result;
        int depth = 0;
        int end = spec.length();
        for (int i = open; i < spec.length(); i++) {
            char c = spec.charAt(i);
            if (c == '{') depth++;
            else if (c == '}') {
                depth--;
                if (depth == 0) {
                    end = i;
                    break;
                }
            }
        }
        String block = spec.substring(open, end);
        Matcher status = Pattern.compile("\"status\"\\s*:\\s*\"([^\"]+)\"").matcher(block);
        if (status.find()) result[0] = status.group(1);
        Matcher value = Pattern.compile("\"value\"\\s*:\\s*([-+0-9.eE]+)").matcher(block);
        if (value.find()) result[1] = value.group(1);
        Matcher tolerance = Pattern.compile("\"acceptance_tolerance\"\\s*:\\s*([-+0-9.eE]+)").matcher(block);
        if (tolerance.find()) result[2] = tolerance.group(1);
        return result;
    }

    /** Reference agreement alone does not certify mesh/domain uncertainty or engineering E. */
    private static String referenceComparisonVerdict(boolean usable, double error, double tolerance) {
        if (!usable) return "NOT_VALIDATED_NO_REFERENCE";
        if (!Double.isFinite(tolerance) || tolerance <= 0) return "NOT_VALIDATED_NO_TOLERANCE";
        if (!Double.isFinite(error) || error < 0) return "NOT_VALIDATED_UNMEASURED_REFERENCE_ERROR";
        return error <= tolerance ? "REFERENCE_WITHIN_TOLERANCE" : "NOT_WITHIN_REFERENCE_TOLERANCE";
    }

    private static String safe(String value) {
        return value.replaceAll("[^A-Za-z0-9_-]", "_");
    }

    private static double[] series(File table, String kind, String namePrefix) throws Exception {
        List<Double> values = new ArrayList<Double>();
        BufferedReader reader = new BufferedReader(new FileReader(table));
        try {
            String line = reader.readLine();
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length != 4) continue;
                if (parts[0].equals(kind) && parts[1].startsWith(namePrefix))
                    values.add(Double.parseDouble(parts[3]));
            }
        } finally {
            reader.close();
        }
        if (values.isEmpty()) return null;
        double[] array = new double[values.size()];
        for (int i = 0; i < array.length; i++) array[i] = values.get(i);
        return array;
    }

    /**
     * Window statistics of a sample series, or null when there is no series to describe.
     *
     * The null case is not hypothetical and it is NOT "the criterion failed": round 13's formal runs
     * (s1-formal-r13-20261004a and -b) died here. `execute` passes the drag series, which can be null
     * because BOTH sources can be absent - the report-monitor series does not exist on this install
     * (`ReportMonitor` creation fails, measured), and the chunk-history fallback
     * `external_flow_solve_history.csv` is a stage-1 artefact that the formal per-stage runner does NOT
     * stage into the validation stage's own directory (measured: the audit job directory holds
     * `input.sim`, `case_spec.json` and the macro, and no solve-stage file at all).
     *
     * The previous body dereferenced the parameter immediately (`for (double value : values)`), which
     * javac compiles to `aload_0; astore_2; aload_2; arraylength`. A null argument therefore threw
     * `NullPointerException: Cannot read the array length because "<local2>" is null` - slot 2 is the
     * compiler's array copy, which is why the message named no variable. It was NOT
     * `report.getDirection()`: that call's array-length read is at slot 4 and is guarded.
     *
     * The wrapper then turned the crash into `RuntimeException("External-flow convergence audit failed")`,
     * i.e. a crash was presented in the shape of a criteria rejection. Returning null instead lets the
     * caller do what it already does for an absent series: record zero samples, NaN window metrics,
     * `target_quantities_steady: null` and the verdict `NOT_CONVERGED_UNMEASURED`. That is "I could not
     * measure this", which is what an audit that cannot measure must say.
     */
    private static double[] windowStats(double[] values) {
        if (values == null || values.length == 0) return null;
        List<Double> list = new ArrayList<Double>();
        for (double value : values) list.add(value);
        return windowStats(list);
    }

    private static double ratio(double previous, double current) {
        if (!Double.isFinite(previous) || !Double.isFinite(current) || current == 0) return Double.NaN;
        return previous / current;
    }

    private static double volumeAverageDensity(Simulation sim, FvRepresentation mesh, Region region,
            List<ClientServerObject> temporary) throws Exception {
        FieldFunction density = null;
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if ("Density".equals(candidate.getFunctionName())) {
                density = candidate;
                break;
            }
        }
        if (density == null) return Double.NaN;
        VolumeAverageReport report = sim.getReportManager().createReport(VolumeAverageReport.class);
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(density);
        report.getParts().setObjects(region);
        return report.getReportMonitorValue();
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation representation : sim.getRepresentationManager().getObjects()) {
            if (representation instanceof FvRepresentation) return (FvRepresentation) representation;
        }
        return null;
    }

    private static double[] array(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(text);
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

    /**
     * The string value of a key that sits DIRECTLY in the record's ROOT object, or the fallback.
     *
     * `optional()` is a first-match regex over the whole raw text, and a stage record contains nested
     * objects: the runner writes `macros:[{..., "output_sha256": ...}, ...]` BEFORE the top-level
     * `"output"`/`"output_sha256"`. The round-15 independent review measured the consequence on the
     * read-only counter-example fixture: the first `output_sha256` found was `macros[0].output_sha256`,
     * which happened to equal the input .sim's hash, so `readOnlyVerified` came out TRUE and the pipeline's
     * own `read_only_verified:true` gate would have been satisfied - a false pass. The same ordering is
     * present in the real round-10 record, so this was not a fixture artefact.
     *
     * This reader walks the text once, tracking string literals and brace/bracket depth, and only accepts
     * a key found at depth 1 (directly inside the root object). A nested value can therefore never satisfy
     * the read-only binding, whichever order the writer emits the fields in.
     */
    private static String rootString(String text, String key, String fallback) {
        String needle = "\"" + key + "\"";
        int depth = 0;
        boolean inString = false;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (inString) {
                if (c == '\\') {
                    i++;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }
            if (c == '"') {
                if (depth == 1 && text.startsWith(needle, i)) {
                    int j = i + needle.length();
                    while (j < text.length() && Character.isWhitespace(text.charAt(j))) j++;
                    if (j >= text.length() || text.charAt(j) != ':') continue;
                    j++;
                    while (j < text.length() && Character.isWhitespace(text.charAt(j))) j++;
                    if (j < text.length() && text.charAt(j) == '"') {
                        int end = text.indexOf('"', j + 1);
                        if (end > j) return text.substring(j + 1, end);
                    }
                    return fallback;
                }
                inString = true;
                continue;
            }
            if (c == '{' || c == '[') depth++;
            else if (c == '}' || c == ']') depth--;
        }
        return fallback;
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
