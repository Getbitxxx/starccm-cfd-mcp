/* Generic MRF trial solve (P2.3 G5 part5): initialize and iterate a configured, unsolved MRF propeller
 * simulation, and report honestly what happened. TRACK P SEMANTICS: status "ok" means the trial ran and
 * left evidence; the convergence verdict is a SEPARATE field with the CaseSpec gates it was compared
 * against. An honest NOT_CONVERGED/STAGNATED record is a legitimate result; writing converged:true
 * without the gates being met is not.
 *
 * STOPPING BASIS (no wall-clock stop, per the 2026-10-14 policy): the CaseSpec pilot iteration cap,
 * non-finite divergence, a CaseSpec-registered stagnation window, or a technical failure. The macro
 * writes a complete record on every path (success, NOT_CONVERGED, STAGNATED, DIVERGED) and never
 * throws merely because the physics did not converge.
 *
 * CONVERGENCE GATES read from case_spec.json::acceptance: residuals (continuity/momentum/energy/
 * turbulence max-over-monitors), steady_window_iterations + load_range_fraction for the CT/CP window,
 * and mass_imbalance_fraction for the boundary mass balance. Residual values come from STAR's own
 * ResidualMonitor history (exported per chunk), because ReportMonitor creation fails on this install.
 *
 * Input : a configured but UNINITIALIZED simulation with the two MRF regions, three interfaces and the
 *         CaseSpec force/moment reports; case_spec.json beside the .sim.
 * Output: mrf_trial_solved.sim, mrf_trial_solve.json, mrf_trial_history.tsv and MRF_TRIAL_SOLVED.
 * NO shell/return-code claim of convergence: every number is read back from the loaded STAR state or
 * from the exported monitor history.
 */
import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.FileWriter;
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
import star.base.report.*;
import star.common.*;
import star.flow.*;

public class RunMrfTrial extends StarMacro {

    private static final String COMPLETE = "MRF_TRIAL_SOLVED";
    private static final int CHUNK = 10;
    private static final double R_AIR = 287.058;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File output = new File(dir, "mrf_trial_solved.sim");
        int start = sim.getSimulationIterator().getCurrentIteration();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String solver = object(spec, "solver");
            String acceptance = object(spec, "acceptance");
            String rotation = object(spec, "rotation");
            String geometry = object(spec, "geometry");
            String flow = object(spec, "flow");
            String roles = object(spec, "mrf_regions");
            int iterationCap = (int) number(solver, "trial_iterations");
            int stagnationWindow = (int) number(solver, "stagnation_window_iterations");
            double stagnationResidualRel = number(solver, "stagnation_residual_rel_change");
            double stagnationLoadRel = number(solver, "stagnation_load_rel_change");
            int steadyWindow = (int) number(acceptance, "steady_window_iterations");
            double loadRange = number(acceptance, "load_range_fraction");
            double massImbalanceGate = number(acceptance, "mass_imbalance_fraction");
            // acceptance.residuals is a NESTED object: parse it explicitly instead of inventing flat keys.
            String residualGates = object(acceptance, "residuals");
            double gCont = number(residualGates, "continuity");
            double gMom = number(residualGates, "momentum");
            double gEnergy = number(residualGates, "energy");
            double gTurb = number(residualGates, "turbulence");
            double rpm = number(rotation, "rpm");
            double diameter = number(geometry, "diameter_m");
            double pressure = number(flow, "pressure_pa");
            double temperature = number(flow, "temperature_k");
            String rotorName = string(roles, "rotor_region");
            String statorName = string(roles, "stator_region");
            String thrustName = string(spec, "thrust_report_name");
            String torqueName = string(spec, "torque_report_name");
            double rho = pressure / (R_AIR * temperature);
            double revPerSecond = rpm / 60.0;

            if (iterationCap < 1 || stagnationWindow < 2 || steadyWindow < 2) {
                throw new IllegalArgumentException("Invalid solver/acceptance windows in CaseSpec");
            }
            if (!(gCont > 0 && gMom > 0 && gEnergy > 0 && gTurb > 0 && loadRange > 0 && massImbalanceGate > 0
                    && stagnationResidualRel > 0 && stagnationLoadRel > 0)) {
                throw new IllegalArgumentException("Invalid convergence gates in CaseSpec");
            }
            if (start != 0) throw new IllegalStateException("Trial must start from an unrun simulation");
            if (sim.getSolution().isInitialized()) {
                throw new IllegalStateException("Trial input is already initialized; expected the configured unsolved state");
            }
            if (sim.getRegionManager().getRegions().size() != 2
                    || sim.getInterfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Expected two MRF regions and three interfaces");
            }
            Region rotor = sim.getRegionManager().getRegion(rotorName);
            Region stator = sim.getRegionManager().getRegion(statorName);
            if (rotor == null || stator == null) throw new IllegalStateException("CaseSpec regions are missing");
            Report thrust = sim.getReportManager().getReport(thrustName);
            Report torque = sim.getReportManager().getReport(torqueName);
            if (thrust == null || torque == null) throw new IllegalStateException("CaseSpec force/moment reports are missing");

            sim.getSolution().initializeSolution();
            if (!sim.getSolution().isInitialized()) throw new IllegalStateException("Initialization did not take effect");

            StepStoppingCriterion maximum = (StepStoppingCriterion)
                    sim.getSolverStoppingCriterionManager().getSolverStoppingCriterion("Maximum Steps");
            if (maximum == null) throw new IllegalStateException("Maximum Steps stopping criterion is missing");
            maximum.getMaximumNumberStepsObject().getQuantity().setValue(iterationCap);
            maximum.setIsUsed(true);

            List<Double> ctSamples = new ArrayList<Double>();
            List<Double> cpSamples = new ArrayList<Double>();
            List<Integer> sampleIterations = new ArrayList<Integer>();
            Map<String, Double> residuals = new LinkedHashMap<String, Double>();
            List<Map<String, Double>> residualHistory = new ArrayList<Map<String, Double>>();
            double lastThrust = Double.NaN, lastTorque = Double.NaN;
            boolean finite = true;
            String stopReason = "ITERATION_CAP";
            int end = start;
            try (Writer history = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_trial_history.tsv")), StandardCharsets.UTF_8)) {
                history.write("iteration\tthrust_n\ttorque_nm\tct\tcp\n");
                int chunk = CHUNK;
                while (end - start < iterationCap) {
                    int requested = Math.min(chunk, iterationCap - (end - start));
                    int before = end;
                    sim.getSimulationIterator().run(requested);
                    end = sim.getSimulationIterator().getCurrentIteration();
                    if (end - before != requested) {
                        throw new IllegalStateException("Solver stopped after " + (end - before) + " of " + requested + " steps");
                    }
                    try { lastThrust = thrust.getReportMonitorValue(); } catch (Throwable ignored) { lastThrust = Double.NaN; }
                    try { lastTorque = torque.getReportMonitorValue(); } catch (Throwable ignored) { lastTorque = Double.NaN; }
                    if (!Double.isFinite(lastThrust) || !Double.isFinite(lastTorque)) { finite = false; stopReason = "DIVERGED"; break; }
                    double ct = lastThrust / (rho * revPerSecond * revPerSecond * Math.pow(diameter, 4.0));
                    double cp = 2.0 * Math.PI * lastTorque / (rho * revPerSecond * revPerSecond * Math.pow(diameter, 5.0));
                    ctSamples.add(ct);
                    cpSamples.add(cp);
                    sampleIterations.add(end);
                    history.write(end + "\t" + lastThrust + "\t" + lastTorque + "\t" + ct + "\t" + cp + "\n");
                    history.flush();
                    residuals = exportResiduals(sim, dir);
                    if (residuals.isEmpty()) throw new IllegalStateException("No ResidualMonitor history was exported");
                    residualHistory.add(new LinkedHashMap<String, Double>(residuals));
                    boolean gate = residualsWithinGates(residuals, gCont, gMom, gEnergy, gTurb);
                    boolean window = loadWindowStable(sampleIterations, ctSamples, cpSamples, steadyWindow, loadRange);
                    boolean stagnation = stagnationDetected(residualHistory, ctSamples, cpSamples,
                            sampleIterations, stagnationWindow, stagnationResidualRel, stagnationLoadRel);
                    if (gate && window) { stopReason = "CONVERGED"; break; }
                    if (stagnation) { stopReason = "STAGNATED"; break; }
                }
                history.write("# stop_reason\t" + stopReason + "\n");
                history.write("# iterations\t" + end + "\n");
            }
            sim.saveState(output.getAbsolutePath());
            double massImbalance = measureMassImbalance(sim);
            boolean residualsGate = residualsWithinGates(residuals, gCont, gMom, gEnergy, gTurb);
            boolean loadGate = loadWindowStable(sampleIterations, ctSamples, cpSamples, steadyWindow, loadRange);
            boolean massGate = Double.isFinite(massImbalance) && massImbalance <= massImbalanceGate;
            boolean converged = residualsGate && loadGate && massGate;
            if (!finite) converged = false;
            if (converged) stopReason = "CONVERGED";
            if (!finite) stopReason = "DIVERGED";

            try (Writer json = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_trial_solve.json")), StandardCharsets.UTF_8)) {
                json.write("{\"schema_version\":1,\"status\":\"ok\",\"solver\":\"RunMrfTrial.java\","
                        + "\"initialized\":true," + "\"iterations_start\":" + start + ",\"iterations_end\":" + end
                        + ",\"iteration_cap\":" + iterationCap + ",\"stop_reason\":\"" + esc(stopReason) + "\""
                        + ",\"converged\":" + converged + ",\"finite_target_quantities\":" + finite
                        + ",\"thrust_n\":" + lastThrust + ",\"torque_nm\":" + lastTorque
                        + ",\"ct\":" + (ctSamples.isEmpty() ? "null" : String.valueOf(last(ctSamples)))
                        + ",\"cp\":" + (cpSamples.isEmpty() ? "null" : String.valueOf(last(cpSamples)))
                        + ",\"ct_source\":\"offline from measured report value; rho = case_spec inlet P/(R_AIR*T)\""
                        + ",\"rho_kg_m3\":" + rho + ",\"n_rev_s\":" + revPerSecond + ",\"diameter_m\":" + diameter
                        + ",\"residuals\":{" + residualJson(residuals) + "}"
                        + ",\"residual_gates\":{\"continuity\":" + gCont + ",\"momentum\":" + gMom
                        + ",\"energy\":" + gEnergy + ",\"turbulence\":" + gTurb + "}"
                        + ",\"residuals_gate_passed\":" + residualsGate + ",\"load_window\":" + steadyWindow
                        + ",\"load_range_fraction\":" + loadRange + ",\"load_gate_passed\":" + loadGate
                        + ",\"mass_imbalance_fraction\":" + (Double.isFinite(massImbalance) ? String.valueOf(massImbalance) : "null")
                        + ",\"mass_imbalance_gate\":" + massImbalanceGate + ",\"mass_gate_passed\":" + massGate
                        + ",\"stagnation_window\":" + stagnationWindow
                        + ",\"stagnation_residual_rel_change\":" + stagnationResidualRel
                        + ",\"stagnation_load_rel_change\":" + stagnationLoadRel
                        + ",\"stagnation_samples\":" + Math.max(2, stagnationWindow / CHUNK)
                        + ",\"iteration_cap_source\":\"case_spec.solver.trial_iterations\""
                        + ",\"note\":\"honest solve record: converged is derived from the CaseSpec gates; NOT_CONVERGED/STAGNATED are legitimate results; no wall-clock stop\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer json = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_trial_failure.json")), StandardCharsets.UTF_8)) {
                json.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString())
                        + "\",\"iterations\":" + sim.getSimulationIterator().getCurrentIteration() + "}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF trial solve failed", failure);
        }
    }

    private static boolean residualsWithinGates(Map<String, Double> residuals, double cont, double mom,
                                                double energy, double turb) {
        if (residuals.isEmpty()) return false;
        double c = Double.MAX_VALUE, m = Double.MAX_VALUE, e = Double.MAX_VALUE, t = Double.MAX_VALUE;
        for (Map.Entry<String, Double> entry : residuals.entrySet()) {
            if (!Double.isFinite(entry.getValue())) return false;
            String name = entry.getKey().toLowerCase(Locale.ROOT);
            // Worst (largest) residual per category: a single monitor above the gate means not converged.
            if (name.contains("continuity")) c = Math.max(c, entry.getValue());
            else if (name.contains("momentum")) m = Math.max(m, entry.getValue());
            else if (name.contains("energy")) e = Math.max(e, entry.getValue());
            else if (name.contains("tke") || name.contains("sdr") || name.contains("omega") || name.contains("turb")) {
                t = Math.max(t, entry.getValue());
            }
        }
        if (c == Double.MAX_VALUE || m == Double.MAX_VALUE || e == Double.MAX_VALUE || t == Double.MAX_VALUE) {
            return false;
        }
        return c <= cont && m <= mom && e <= energy && t <= turb;
    }

    private static boolean loadWindowStable(List<Integer> iterations, List<Double> ct, List<Double> cp,
                                            int window, double rangeFraction) {
        if (iterations.isEmpty()) return false;
        int lastIteration = iterations.get(iterations.size() - 1);
        double cMin = Double.MAX_VALUE, cMax = -Double.MAX_VALUE, pMin = Double.MAX_VALUE, pMax = -Double.MAX_VALUE;
        double cSum = 0, pSum = 0;
        int count = 0;
        for (int i = 0; i < iterations.size(); i++) {
            if (iterations.get(i) < lastIteration - window) continue;
            cMin = Math.min(cMin, ct.get(i)); cMax = Math.max(cMax, ct.get(i));
            pMin = Math.min(pMin, cp.get(i)); pMax = Math.max(pMax, cp.get(i));
            cSum += ct.get(i); pSum += cp.get(i); count++;
        }
        if (count < 3) return false;
        double cMean = cSum / count, pMean = pSum / count;
        double cRange = (cMax - cMin) / Math.abs(cMean);
        double pRange = (pMax - pMin) / Math.abs(pMean);
        return cRange <= rangeFraction && pRange <= rangeFraction;
    }

    /**
     * Stagnation over the FULL CaseSpec-registered window, at chunk resolution: the window is
     * stagnation_window_iterations (100) and samples are CHUNK (10) iterations apart, so
     * samplesNeeded = window/CHUNK (10) consecutive intervals must show no residual improvement and
     * the CT/CP change across the whole window must stay below the registered stagnation load band.
     * A single 10-iteration plateau is NOT enough (the earlier version used window/10 samples, which
     * would have stopped ~100 iterations early - review-3m blocking finding 2).
     */
    private static boolean stagnationDetected(List<Map<String, Double>> history, List<Double> ct, List<Double> cp,
                                              List<Integer> iterations, int window, double residualRelChange,
                                              double loadRelChange) {
        int samplesNeeded = Math.max(2, window / CHUNK);
        if (history.size() < samplesNeeded + 1) return false;
        for (int i = history.size() - samplesNeeded; i < history.size(); i++) {
            double worst = worstRelativeChange(history.get(i - 1), history.get(i));
            if (!(worst < residualRelChange)) return false;
        }
        int n = iterations.size();
        if (n < samplesNeeded + 1) return false;
        int first = Math.max(0, n - 1 - samplesNeeded);
        double ctChange = Math.abs(ct.get(n - 1) - ct.get(first)) / Math.max(1e-12, Math.abs(ct.get(n - 1)));
        double cpChange = Math.abs(cp.get(n - 1) - cp.get(first)) / Math.max(1e-12, Math.abs(cp.get(n - 1)));
        return ctChange < loadRelChange && cpChange < loadRelChange;
    }

    private static double worstRelativeChange(Map<String, Double> before, Map<String, Double> after) {
        double worst = 0;
        for (Map.Entry<String, Double> entry : after.entrySet()) {
            Double prior = before.get(entry.getKey());
            if (prior == null || !Double.isFinite(prior) || prior == 0) continue;
            worst = Math.max(worst, Math.abs(entry.getValue() - prior) / Math.abs(prior));
        }
        return worst;
    }

    private static double measureMassImbalance(Simulation sim) {
        double net = 0;
        double inletMagnitude = 0;
        List<Report> temporary = new ArrayList<Report>();
        try {
            for (Region region : sim.getRegionManager().getRegions()) {
                for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                    if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                    MassFlowReport report = (MassFlowReport) sim.getReportManager().create("star.flow.MassFlowReport");
                    temporary.add(report);
                    report.getParts().setObjects(boundary);
                    double flow = report.getReportMonitorValue();
                    if (!Double.isFinite(flow)) return Double.NaN;
                    net += flow;
                    inletMagnitude = Math.max(inletMagnitude, Math.abs(flow));
                }
            }
            if (!(inletMagnitude > 0)) return Double.NaN;
            return Math.abs(net) / inletMagnitude;
        } finally {
            for (Report report : temporary) {
                try { sim.deleteObjects(java.util.Collections.singletonList(report)); }
                catch (Throwable ignored) { }
            }
        }
    }

    private static Map<String, Double> exportResiduals(Simulation sim, File dir) throws Exception {
        Map<String, Double> latest = new LinkedHashMap<String, Double>();
        for (Monitor monitor : sim.getMonitorManager().getObjects()) {
            if (!(monitor instanceof ResidualMonitor)) continue;
            File csv = new File(dir, "residual-latest.csv");
            ((ResidualMonitor) monitor).export(csv.getAbsolutePath());
            double value = lastValue(csv);
            csv.delete();
            if (Double.isFinite(value)) latest.put(monitor.getPresentationName(), value);
        }
        return latest;
    }

    private static double lastValue(File csv) throws Exception {
        double value = Double.NaN;
        BufferedReader reader = new BufferedReader(new FileReader(csv));
        try {
            String line;
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split(",");
                if (parts.length < 2) continue;
                try { value = Double.parseDouble(parts[parts.length - 1].trim().replace("\"", "")); }
                catch (NumberFormatException header) { }
            }
        } finally { reader.close(); }
        return value;
    }

    private static double last(List<Double> values) { return values.get(values.size() - 1); }

    private static String residualJson(Map<String, Double> residuals) {
        StringBuilder out = new StringBuilder();
        boolean first = true;
        for (Map.Entry<String, Double> entry : residuals.entrySet()) {
            if (!first) out.append(',');
            out.append('"').append(esc(entry.getKey())).append("\":").append(entry.getValue());
            first = false;
        }
        return out.toString();
    }

    private static String read(File file) throws Exception {
        return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
    }

    private static int valueStart(String json, String key) {
        int i = json.indexOf("\"" + key + "\"");
        if (i < 0) throw new IllegalArgumentException("case_spec.json has no key " + key);
        int colon = json.indexOf(':', i + key.length() + 2);
        if (colon < 0) throw new IllegalArgumentException("case_spec.json key has no value " + key);
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) start++;
        return start;
    }

    private static String object(String json, String key) {
        int start = valueStart(json, key);
        if (start >= json.length() || json.charAt(start) != '{') {
            throw new IllegalArgumentException("case_spec.json key is not an object: " + key);
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
            throw new IllegalArgumentException("case_spec.json key is not a string: " + key);
        }
        int end = json.indexOf('"', start + 1);
        if (end < 0) throw new IllegalArgumentException("unterminated string: " + key);
        return json.substring(start + 1, end);
    }

    private static double number(String json, String key) {
        int start = valueStart(json, key);
        int end = start;
        while (end < json.length() && "-+0123456789.eE".indexOf(json.charAt(end)) >= 0) end++;
        if (end == start) throw new IllegalArgumentException("case_spec.json value is not a number: " + key);
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
