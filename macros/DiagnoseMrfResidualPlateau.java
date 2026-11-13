/* Generic MRF residual-plateau diagnosis (P2.3 G5 part8): READ-ONLY analysis of STAR's residual monitor
 * histories in a saved solve state. It never initializes, iterates, meshes or saves.
 *
 * Purpose: the part5/part6 solve honestly stopped at the iteration cap with residuals NOT below the
 * CaseSpec gates (continuity/X-momentum/energy) while the load window and mass balance passed. Adding
 * iterations is not a diagnosis, so this stage measures the residual history:
 *   - per category (continuity/momentum/energy/turbulence, plus transition diagnostics),
 *   - window statistics (first/last/min/max, oscillation amplitude),
 *   - the log10-slope per 100 iterations over the tail window,
 *   - a classification (INSUFFICIENT / PLATEAU_OR_WORSENING / OSCILLATING / SLOW_DECAY / CONVERGING)
 *     and, when the slope is negative, the iterations needed to reach the CaseSpec gate at that slope.
 * The target-quantity stability is read from the solve-stage history TSV when present (declared source).
 * It writes mrf_residual_plateau_diagnosis.json (+ .tsv) and prints MRF_RESIDUAL_PLATEAU_DIAGNOSED.
 * No numbers are claimed from a solve: every value is read back from the saved monitor history.
 */
import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
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

public class DiagnoseMrfResidualPlateau extends StarMacro {

    private static final String COMPLETE = "MRF_RESIDUAL_PLATEAU_DIAGNOSED";
    private static final int TAIL_WINDOW = 300;

    private static final class Series {
        String name;
        List<double[]> rows = new ArrayList<double[]>();
        double first, last, minTail, maxTail;
        double slopePer100;
        double oscillationFraction;
        String classification = "INSUFFICIENT";
        double iterationsToGate = Double.POSITIVE_INFINITY;
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String acceptance = object(spec, "acceptance");
            String residualGates = object(acceptance, "residuals");
            double gCont = number(residualGates, "continuity");
            double gMom = number(residualGates, "momentum");
            double gEnergy = number(residualGates, "energy");
            double gTurb = number(residualGates, "turbulence");
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            boolean solutionLoaded = iteration > 0;

            List<Series> all = new ArrayList<Series>();
            for (Monitor monitor : sim.getMonitorManager().getObjects()) {
                if (!(monitor instanceof ResidualMonitor)) continue;
                Series series = new Series();
                series.name = monitor.getPresentationName();
                File csv = new File(dir, "plateau-" + safe(series.name) + ".csv");
                ((ResidualMonitor) monitor).export(csv.getAbsolutePath());
                BufferedReader reader = new BufferedReader(new FileReader(csv));
                try {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        String[] parts = line.split(",");
                        if (parts.length < 2) continue;
                        try {
                            double x = Double.parseDouble(parts[0].trim().replace("\"", ""));
                            double y = Double.parseDouble(parts[1].trim().replace("\"", ""));
                            if (Double.isFinite(x) && Double.isFinite(y) && y != 0.0) {
                                series.rows.add(new double[] {x, Math.abs(y)});
                            }
                        } catch (NumberFormatException header) { }
                    }
                } finally { reader.close(); }
                csv.delete();
                if (!series.rows.isEmpty()) all.add(series);
            }
            if (all.isEmpty()) throw new IllegalStateException("No ResidualMonitor history in the saved state");

            for (Series series : all) {
                double gate = gateFor(series.name, gCont, gMom, gEnergy, gTurb);
                analyse(series, gate);
            }
            String overall = overallClassification(all);
            boolean diagnosisComplete = solutionLoaded && all.size() >= 5;

            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"diagnostic\":\"residual_plateau\",")
                .append("\"read_only\":true,\"iterations\":").append(iteration)
                .append(",\"solution_loaded\":").append(solutionLoaded)
                .append(",\"monitors\":").append(all.size())
                .append(",\"tail_window\":").append(TAIL_WINDOW)
                .append(",\"overall_classification\":\"").append(esc(overall)).append('"')
                .append(",\"diagnosis_complete\":").append(diagnosisComplete)
                .append(",\"series\":[");
            boolean first = true;
            for (Series series : all) {
                if (!first) json.append(',');
                first = false;
                json.append("{\"name\":\"").append(esc(series.name)).append('"')
                    .append(",\"samples\":").append(series.rows.size())
                    .append(",\"first\":").append(series.first)
                    .append(",\"last\":").append(series.last)
                    .append(",\"min_tail\":").append(series.minTail)
                    .append(",\"max_tail\":").append(series.maxTail)
                    .append(",\"slope_per_100_iterations\":").append(series.slopePer100)
                    .append(",\"oscillation_fraction\":").append(series.oscillationFraction)
                    .append(",\"classification\":\"").append(esc(series.classification)).append('"')
                    .append(",\"iterations_to_gate_at_slope\":")
                    .append(Double.isInfinite(series.iterationsToGate) ? "null" : String.valueOf(series.iterationsToGate))
                    .append('}');
            }
            json.append("],\"residual_gates\":{\"continuity\":").append(gCont)
                .append(",\"momentum\":").append(gMom).append(",\"energy\":").append(gEnergy)
                .append(",\"turbulence\":").append(gTurb).append('}')
                .append(",\"target_history\":{\"").append("present\":").append(historyPresent(dir)).append('}')
                .append(",\"note\":\"read-only residual-history diagnosis of the saved solve state: no initialize, no iteration, no save. ")
                .append("Classification thresholds: slope over the tail window in log10 per 100 iterations; PLATEAU_OR_WORSENING when slope >= -0.02/100, ")
                .append("OSCILLATING when detrended variation dominates, SLOW_DECAY when -0.1 < slope < -0.02, CONVERGING when slope <= -0.1/100. ")
                .append("min_tail/max_tail are TAIL-WINDOW statistics (first/last are full-history). iterations_to_gate extrapolates the current tail log-slope to the CaseSpec gate for the four gated categories (transition diagnostics have no CaseSpec gate -> null) and is informational only.\"}")
                .append('\n');
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_residual_plateau_diagnosis.json")), StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            try (Writer tsv = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_residual_plateau_diagnosis.tsv")), StandardCharsets.UTF_8)) {
                tsv.write("MONITOR\tFIRST\tLAST\tMIN_TAIL\tMAX_TAIL\tSLOPE_PER_100\tOSCILLATION\tCLASSIFICATION\tITERATIONS_TO_GATE\n");
                for (Series series : all) {
                    tsv.write(series.name + "\t" + series.first + "\t" + series.last + "\t" + series.minTail + "\t"
                            + series.maxTail + "\t" + series.slopePer100 + "\t" + series.oscillationFraction + "\t"
                            + series.classification + "\t"
                            + (Double.isInfinite(series.iterationsToGate) ? "inf" : series.iterationsToGate) + "\n");
                }
                tsv.write("OVERALL\t\t\t\t\t\t\t" + overall + "\t\n");
                tsv.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_residual_plateau_diagnosis_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF residual-plateau diagnosis failed", failure);
        }
    }

    private static double gateFor(String name, double cont, double mom, double energy, double turb) {
        String lower = name.toLowerCase(Locale.ROOT);
        if (lower.contains("continuity")) return cont;
        if (lower.contains("momentum")) return mom;
        if (lower.contains("energy")) return energy;
        if (lower.contains("tke") || lower.contains("sdr") || lower.contains("omega") || lower.contains("turb")) return turb;
        return Double.NaN; // transition diagnostics have no CaseSpec gate
    }

    private static void analyse(Series series, double gate) {
        int n = series.rows.size();
        series.first = series.rows.get(0)[1];
        series.last = series.rows.get(n - 1)[1];
        series.minTail = series.last;
        series.maxTail = series.last;
        int start = 0;
        double lastIteration = series.rows.get(n - 1)[0];
        for (int i = 0; i < n; i++) {
            if (series.rows.get(i)[0] >= lastIteration - TAIL_WINDOW) { start = i; break; }
        }
        double sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
        int count = 0;
        List<Double> y = new ArrayList<Double>();
        for (int i = start; i < n; i++) {
            double x = series.rows.get(i)[0], value = series.rows.get(i)[1];
            if (!Double.isFinite(value) || value <= 0) continue;
            double yv = Math.log10(value);
            y.add(yv);
            sumX += x; sumY += yv; sumXY += x * yv; sumXX += x * x; count++;
            series.minTail = Math.min(series.minTail, value);
            series.maxTail = Math.max(series.maxTail, value);
        }
        if (count < 2) { series.classification = "INSUFFICIENT"; return; }
        double denominator = count * sumXX - sumX * sumX;
        double slope = denominator == 0 ? 0.0 : (count * sumXY - sumX * sumY) / denominator;
        series.slopePer100 = slope * 100.0;
        // detrended variation vs total variation over the window: high ratio = oscillation
        double meanY = sumY / count;
        double inter = meanY - slope * (sumX / count);
        double totalVar = 0, detrendedVar = 0;
        for (int i = 0; i < count; i++) {
            double x = series.rows.get(start + i)[0];
            double residual = y.get(i) - (inter + slope * x);
            detrendedVar += residual * residual;
            totalVar += (y.get(i) - meanY) * (y.get(i) - meanY);
        }
        series.oscillationFraction = totalVar == 0 ? 0.0 : Math.sqrt(detrendedVar / count) / Math.sqrt(totalVar / count);
        if (series.slopePer100 >= -0.02) series.classification = "PLATEAU_OR_WORSENING";
        else if (series.oscillationFraction > 0.5) series.classification = "OSCILLATING";
        else if (series.slopePer100 <= -0.1) series.classification = "CONVERGING";
        else series.classification = "SLOW_DECAY";
        series.minTail = Math.min(series.minTail, series.last);
        series.maxTail = Math.max(series.maxTail, series.last);
        // informational extrapolation: iterations for log10(residual) to reach log10(gate) at the tail slope
        if (Double.isFinite(gate) && gate > 0 && series.last > gate && slope < 0) {
            double needed = (Math.log10(series.last) - Math.log10(gate)) / (-slope);
            series.iterationsToGate = Double.isFinite(needed) && needed > 0 ? needed : Double.POSITIVE_INFINITY;
        } else {
            series.iterationsToGate = Double.POSITIVE_INFINITY;
        }
    }

    private static String overallClassification(List<Series> all) {
        int plateau = 0, oscillating = 0, converging = 0, slow = 0, insufficient = 0;
        for (Series series : all) {
            String name = series.name.toLowerCase(Locale.ROOT);
            if (!(name.contains("continuity") || name.contains("momentum") || name.contains("energy")
                    || name.contains("tke") || name.contains("sdr") || name.contains("turb"))) continue;
            if ("PLATEAU_OR_WORSENING".equals(series.classification)) plateau++;
            else if ("OSCILLATING".equals(series.classification)) oscillating++;
            else if ("CONVERGING".equals(series.classification)) converging++;
            else if ("SLOW_DECAY".equals(series.classification)) slow++;
            else insufficient++;
        }
        if (plateau > 0) return "PLATEAU_OR_WORSENING";
        if (oscillating > 0) return "OSCILLATING";
        if (slow > 0) return "SLOW_DECAY";
        if (converging > 0) return "CONVERGING";
        return "INSUFFICIENT";
    }

    private static boolean historyPresent(File dir) {
        return new File(dir, "mrf_segment_history.tsv").isFile() || new File(dir, "mrf_trial_history.tsv").isFile();
    }

    private static String safe(String value) {
        return value.replaceAll("[^A-Za-z0-9_-]", "_");
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
