/* Generic MRF trial audit (P2.3 G5 part7): READ-ONLY re-measurement of a saved solve state.
 *
 * This is the independent audit stage: it never initializes, iterates, meshes or saves. It re-derives the
 * convergence verdict from STAR's own ResidualMonitor history, re-measures the boundary mass balance,
 * reads the solved WallYplus field on the rotating walls, checks the thrust sign, and forms CT/CP from the
 * CaseSpec force/moment report values. It also reports the transition-model residuals (Intermittency,
 * ReTheta_t) explicitly with the justification that the CaseSpec acceptance.residuals gates name only the
 * four categories continuity/momentum/energy/turbulence - so the transition residuals are diagnostics here,
 * not silent omissions.
 *
 * The signed CT/CP tolerance comparison is recorded as PRELIMINARY only: the case is not converged, uses one
 * mesh and one operating point, so no Q3/engineering_verified claim is made.
 *
 * Input : a saved MRF solve state + case_spec.json + reference.json beside it (optional solve-stage history
 *         TSVs: mrf_segment_history.tsv / mrf_trial_history.tsv).
 * Output: mrf_trial_audit.json, mrf_trial_audit.tsv and MF_RE_TRIAL_AUDITED marker.
 */
import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.FilenameFilter;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.HashSet;
import java.util.Set;

import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.flow.*;
import star.energy.*;
import star.material.*;

public class AuditMrfTrial extends StarMacro {

    private static final String COMPLETE = "MRF_TRIAL_AUDITED";
    private static final double R_AIR = 287.058;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String reference = read(new File(dir, "reference.json"));
            String acceptance = object(spec, "acceptance");
            String solver = object(spec, "solver");
            String rotation = object(spec, "rotation");
            String geometry = object(spec, "geometry");
            String flow = object(spec, "flow");
            String roles = object(spec, "mrf_regions");
            String residualGates = object(acceptance, "residuals");
            String tolerance = object(acceptance, "reference_tolerance");
            double ctAbsFloor = number(tolerance, "ct_abs_floor");
            double ctRel = number(tolerance, "ct_rel");
            double cpAbsFloor = number(tolerance, "cp_abs_floor");
            double cpRel = number(tolerance, "cp_rel");
            double gCont = number(residualGates, "continuity");
            double gMom = number(residualGates, "momentum");
            double gEnergy = number(residualGates, "energy");
            double gTurb = number(residualGates, "turbulence");
            int steadyWindow = (int) number(acceptance, "steady_window_iterations");
            double loadRange = number(acceptance, "load_range_fraction");
            double massGate = number(acceptance, "mass_imbalance_fraction");
            double yMax = number(acceptance, "yplus_max");
            double yAvg = number(acceptance, "yplus_area_average_max");
            double yCovFrac = number(acceptance, "yplus_coverage_fraction");
            double yCovLimit = number(acceptance, "yplus_coverage_limit");
            double rpm = number(rotation, "rpm");
            double diameter = number(geometry, "diameter_m");
            String rotorName = string(roles, "rotor_region");
            String statorName = string(roles, "stator_region");
            String thrustName = string(spec, "thrust_report_name");
            String torqueName = string(spec, "torque_report_name");
            double[] thrustDirection = array(spec, "force_positive_direction");
            String inletName = string(roles, "inlet_boundary");
            String outletName = string(roles, "outlet_boundary");
            double rho = readBoundaryRho(sim, rotorName, statorName, inletName, outletName, flow);
            double n = rpm / 60.0;
            if (!(rho > 0) || !(n > 0) || !(diameter > 0)) throw new IllegalStateException("invalid rho/n/D");

            Report thrust = sim.getReportManager().getReport(thrustName);
            Report torque = sim.getReportManager().getReport(torqueName);
            if (thrust == null || torque == null) throw new IllegalStateException("CaseSpec reports missing");
            double thrustValue = thrust.getReportMonitorValue();
            double torqueValue = torque.getReportMonitorValue();
            if (!Double.isFinite(thrustValue) || !Double.isFinite(torqueValue)) {
                throw new IllegalStateException("non-finite force/moment report values");
            }
            double forceScale = rho * n * n * Math.pow(diameter, 4.0);
            double momentScale = 2.0 * Math.PI / (rho * n * n * Math.pow(diameter, 5.0));
            double ct = thrustValue / forceScale;
            double cpRaw = torqueValue * 2.0 * Math.PI / (rho * n * n * Math.pow(diameter, 5.0));
            // CaseSpec shaft_power_definition = "-Q*Omega": the absorbed-power coefficient is the positive one.
            double cpAbsorbed = -cpRaw;
            double advance = measuredAdvanceRatio(sim, statorName, inletName, n, diameter);

            // ---- residuals from STAR's own monitor history (read-only export)
            Map<String, List<double[]>> series = exportResidualSeries(sim, dir);
            Map<String, Double> lastResiduals = lastValues(series);
            if (lastResiduals.isEmpty()) throw new IllegalStateException("No ResidualMonitor history in the saved state");
            boolean residualsGate = residualsWithinGates(lastResiduals, gCont, gMom, gEnergy, gTurb);
            boolean transitionSettled = true;
            StringBuilder transitionDiagnostics = new StringBuilder();
            for (Map.Entry<String, Double> entry : lastResiduals.entrySet()) {
                String name = entry.getKey().toLowerCase(Locale.ROOT);
                if (name.contains("intermittency") || name.contains("retheta") || name.contains("theta")) {
                    transitionDiagnostics.append(entry.getKey()).append('=').append(entry.getValue()).append(' ');
                    if (!(entry.getValue() <= gTurb)) transitionSettled = false;
                }
            }

            // ---- load stability re-derived from the solve-stage history TSV (declared source)
            double[] load = loadWindowFromHistory(dir, steadyWindow, thrustValue, torqueValue, rho, n, diameter);
            boolean loadGate = load[0] <= loadRange && load[1] <= loadRange && load[6] > 0.5;
            String loadSource = load[4] > 0 ? "merged solve-stage history TSVs sampled every 10 iterations" : "single final sample only";

            // ---- boundary mass balance re-measured now
            double massImbalance = measureMassImbalance(sim);

            // ---- y+ statistics on the rotating material walls from the SOLVED field
            String[] yplusDetail = new String[2];
            double[] yplus = yplusStats(sim, rotorName, statorName, yMax, yAvg, yCovFrac, yCovLimit, yplusDetail);
            boolean yplusGate = Double.isFinite(yplus[3]) && yplus[3] <= yMax && yplus[4] <= yAvg
                    && yplus[5] >= yCovFrac;

            // ---- thrust sign: report direction is CaseSpec force_positive_direction (+Z), so thrust > 0 is positive
            boolean thrustSignPositive = thrustValue > 0 && thrustDirection[2] > 0;

            // ---- signed tolerance comparison against reference.json operating points (preliminary)
            double[] ref = referencePoint(reference, advance);
            double toleranceCt = Math.max(ctAbsFloor, ctRel * ref[0]);
            double toleranceCp = Math.max(cpAbsFloor, cpRel * ref[1]);
            boolean ctWithin = Double.isFinite(ref[0]) && Math.abs(ct - ref[0]) <= toleranceCt;
            boolean cpWithin = Double.isFinite(ref[1]) && Math.abs(cpAbsorbed - ref[1]) <= toleranceCp;

            boolean converged = residualsGate && loadGate && (Double.isFinite(massImbalance) && massImbalance <= massGate);
            boolean yplusMeasured = Double.isFinite(yplus[3]);
            boolean solutionLoaded = sim.getSimulationIterator().getCurrentIteration() > 0
                    && !lastResiduals.isEmpty() && !Double.isNaN(advance);

            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"audit\":\"AuditMrfTrial.java\",\"read_only\":true,")
                .append("\"residuals\":{").append(residualJson(lastResiduals)).append('}')
                .append(",\"residual_gates\":{\"continuity\":").append(gCont).append(",\"momentum\":").append(gMom)
                .append(",\"energy\":").append(gEnergy).append(",\"turbulence\":").append(gTurb).append('}')
                .append(",\"residuals_gate_passed\":").append(residualsGate)
                .append(",\"transition_residuals_diagnostic\":\"").append(esc(transitionDiagnostics.toString().trim())).append('"')
                .append(",\"transition_settled_against_turbulence_gate\":").append(transitionSettled)
                .append(",\"transition_gate_policy\":\"CaseSpec acceptance.residuals gates only continuity/momentum/energy/turbulence; ")
                .append("Intermittency/ReTheta_t are reported as diagnostics (not silently omitted)\"")
                .append(",\"load_ct_range_fraction\":").append(load[0])
                .append(",\"load_range_fraction\":").append(load[0])
                .append(",\"load_cp_range_fraction\":").append(load[1])
                .append(",\"load_gate_passed\":").append(loadGate)
                .append(",\"load_source\":\"").append(esc(loadSource)).append('"')
                .append(",\"steady_window\":").append(steadyWindow).append(",\"load_window_samples\":").append((int) load[4])
                .append(",\"load_window_span_iterations\":").append((int) load[5])
                .append(",\"load_window_covered\":").append(load[6] > 0.5)
                .append(",\"mass_imbalance_fraction\":").append(Double.isFinite(massImbalance) ? String.valueOf(massImbalance) : "null")
                .append(",\"mass_imbalance_gate\":").append(massGate)
                .append(",\"mass_gate_passed\":").append(Double.isFinite(massImbalance) && massImbalance <= massGate)
                .append(",\"converged_rederived\":").append(converged)
                .append(",\"iterations\":").append(sim.getSimulationIterator().getCurrentIteration())
                .append(",\"initialized_flag\":").append(sim.getSolution().isInitialized())
                .append(",\"solution_loaded\":").append(solutionLoaded)
                .append(",\"thrust_n\":").append(thrustValue).append(",\"torque_nm\":").append(torqueValue)
                .append(",\"thrust_sign_positive\":").append(thrustSignPositive)
                .append(",\"rho_kg_m3\":").append(rho).append(",\"n_rev_s\":").append(n).append(",\"diameter_m\":").append(diameter)
                .append(",\"advance_ratio\":").append(advance)
                .append(",\"ct\":").append(ct).append(",\"cp_raw\":").append(cpRaw).append(",\"cp_absorbed\":").append(cpAbsorbed)
                .append(",\"ct_ref\":").append(ref[0]).append(",\"cp_ref\":").append(ref[1])
                .append(",\"ct_tolerance\":").append(toleranceCt).append(",\"cp_tolerance\":").append(toleranceCp)
                .append(",\"ct_within_signed_tolerance\":").append(ctWithin)
                .append(",\"cp_within_signed_tolerance\":").append(cpWithin)
                .append(",\"comparison_class\":\"PRELIMINARY_SINGLE_POINT_NOT_Q3\"")
                .append(",\"yplus_min\":").append(yplus[0]).append(",\"yplus_area_average\":").append(yplus[1])
                .append(",\"yplus_max\":").append(yplus[2])
                .append(",\"yplus_measured_max\":").append(yplus[3]).append(",\"yplus_measured_area_average\":").append(yplus[4])
                .append(",\"yplus_coverage_fraction\":").append(yplus[5])
                .append(",\"yplus_limits\":{\"max\":").append(yMax).append(",\"area_average\":").append(yAvg)
                .append(",\"coverage_fraction\":").append(yCovFrac).append(",\"coverage_limit\":").append(yCovLimit).append('}')
                .append(",\"yplus_gate_passed\":").append(yplusGate)
                .append(",\"yplus_measured\":").append(yplusMeasured)
                .append(",\"yplus_wall_boundaries\":").append(yplusDetail[1] == null ? 0 : Integer.parseInt(yplusDetail[1]))
                .append(",\"yplus_wall_names\":\"").append(esc(yplusDetail[0] == null ? "" : yplusDetail[0])).append('"')
                .append(",\"engineering_verified\":false")
                .append(",\"q3_claim\":false")
                .append(",\"note\":\"independent read-only audit of the saved solve state; convergence re-derived from STAR monitor history; ")
                .append("CT/CP comparison is preliminary (single mesh, single J, NOT_CONVERGED); no Q3 claim\"")
                .append('}');
            File record = new File(dir, "mrf_trial_audit.json");
            try (Writer out = new OutputStreamWriter(new FileOutputStream(record), StandardCharsets.UTF_8)) {
                out.write(json.append('\n').toString());
            }
            try (Writer tsv = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_trial_audit.tsv")), StandardCharsets.UTF_8)) {
                tsv.write("METRIC\tVALUE\n");
                tsv.write("iterations\t" + sim.getSimulationIterator().getCurrentIteration() + "\n");
                tsv.write("residuals_gate_passed\t" + residualsGate + "\n");
                tsv.write("load_gate_passed\t" + loadGate + "\n");
                tsv.write("mass_gate_passed\t" + (Double.isFinite(massImbalance) && massImbalance <= massGate) + "\n");
                tsv.write("converged_rederived\t" + converged + "\n");
                tsv.write("thrust_n\t" + thrustValue + "\n");
                tsv.write("torque_nm\t" + torqueValue + "\n");
                tsv.write("ct\t" + ct + "\n");
                tsv.write("cp_absorbed\t" + cpAbsorbed + "\n");
                tsv.write("yplus_area_average\t" + yplus[1] + "\n");
                tsv.write("yplus_max\t" + yplus[2] + "\n");
                tsv.write("yplus_gate_passed\t" + yplusGate + "\n");
                tsv.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_trial_audit_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF trial audit failed", failure);
        }
    }

    /** rho from CaseSpec role boundaries + loaded profiles; FAIL CLOSED (no 300 K fallback, no name guesses). */
    private static double readBoundaryRho(Simulation sim, String rotorName, String statorName,
                                          String inletName, String outletName, String flowText) throws Exception {
        Boundary inlet = requiredBoundary(sim, statorName, inletName);
        Boundary outlet = requiredBoundary(sim, statorName, outletName);
        double inletTemperature = inlet.getValues().get(StaticTemperatureProfile.class)
                .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        double outletStaticPressure = outlet.getValues().get(StaticPressureProfile.class)
                .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        double referencePressure = Double.NaN;
        for (Continuum continuum : sim.getContinuumManager().getObjects()) {
            if (continuum instanceof PhysicsContinuum) {
                referencePressure = ((PhysicsContinuum) continuum).getReferenceValues()
                        .get(ReferencePressure.class).getSIValue();
            }
        }
        if (!Double.isFinite(referencePressure)) throw new IllegalStateException("reference pressure unreadable");
        double pressure = outletStaticPressure + referencePressure;
        if (!Double.isFinite(inletTemperature) || inletTemperature <= 0 || !(pressure > 0)) {
            throw new IllegalStateException("invalid measured boundary pressure/temperature");
        }
        return pressure / (R_AIR * inletTemperature);
    }

    private static Boundary requiredBoundary(Simulation sim, String regionName, String boundaryName) {
        Region region = sim.getRegionManager().getRegion(regionName);
        if (region == null) throw new IllegalStateException("region missing: " + regionName);
        Boundary boundary = region.getBoundaryManager().getBoundary(boundaryName);
        if (boundary == null) throw new IllegalStateException("boundary missing: " + regionName + "/" + boundaryName);
        return boundary;
    }

    /** advance ratio from the loaded inlet velocity magnitude and the CaseSpec n/D; fail closed. */
    private static double measuredAdvanceRatio(Simulation sim, String statorName, String inletName,
                                               double n, double diameter) {
        Boundary inlet = requiredBoundary(sim, statorName, inletName);
        double speed = inlet.getValues().get(VelocityMagnitudeProfile.class)
                .getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        if (!Double.isFinite(speed) || speed <= 0) throw new IllegalStateException("inlet speed is not measured");
        return speed / (n * diameter);
    }

    private static Map<String, List<double[]>> exportResidualSeries(Simulation sim, File dir) throws Exception {
        Map<String, List<double[]>> series = new LinkedHashMap<String, List<double[]>>();
        for (Monitor monitor : sim.getMonitorManager().getObjects()) {
            if (!(monitor instanceof ResidualMonitor)) continue;
            File csv = new File(dir, "audit-residual-" + monitor.getPresentationName().replaceAll("[^A-Za-z0-9_-]", "_") + ".csv");
            ((ResidualMonitor) monitor).export(csv.getAbsolutePath());
            List<double[]> rows = new ArrayList<double[]>();
            BufferedReader reader = new BufferedReader(new FileReader(csv));
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] parts = line.split(",");
                    if (parts.length < 2) continue;
                    try {
                        double x = Double.parseDouble(parts[0].trim().replace("\"", ""));
                        double y = Double.parseDouble(parts[1].trim().replace("\"", ""));
                        if (Double.isFinite(x) && Double.isFinite(y)) rows.add(new double[] {x, y});
                    } catch (NumberFormatException header) { }
                }
            } finally { reader.close(); }
            csv.delete();
            if (!rows.isEmpty()) series.put(monitor.getPresentationName(), rows);
        }
        return series;
    }

    private static Map<String, Double> lastValues(Map<String, List<double[]>> series) {
        Map<String, Double> values = new LinkedHashMap<String, Double>();
        for (Map.Entry<String, List<double[]>> entry : series.entrySet()) {
            List<double[]> rows = entry.getValue();
            values.put(entry.getKey(), rows.get(rows.size() - 1)[1]);
        }
        return values;
    }

    private static boolean residualsWithinGates(Map<String, Double> residuals, double cont, double mom,
                                                double energy, double turb) {
        double c = 0, m = 0, e = 0, t = 0;
        boolean sc = false, sm = false, se = false, st = false;
        for (Map.Entry<String, Double> entry : residuals.entrySet()) {
            if (!Double.isFinite(entry.getValue())) return false;
            String name = entry.getKey().toLowerCase(Locale.ROOT);
            if (name.contains("continuity")) { c = Math.max(c, entry.getValue()); sc = true; }
            else if (name.contains("momentum")) { m = Math.max(m, entry.getValue()); sm = true; }
            else if (name.contains("energy")) { e = Math.max(e, entry.getValue()); se = true; }
            else if (name.contains("tke") || name.contains("sdr") || name.contains("omega") || name.contains("turb")) {
                t = Math.max(t, entry.getValue()); st = true;
            }
        }
        return sc && sm && se && st && c <= cont && m <= mom && e <= energy && t <= turb;
    }

    /** {ctRangeFraction, cpRangeFraction, ctFallback, cpFallback, samples, spanIterations, covered}.
     *  Merges EVERY solve-stage history in the session dir (mrf_*history*.tsv) so a short segment history can
     *  never masquerade as a full steady window (review-5l). Fails closed (covered=0) when the window is not
     *  covered by at least MIN_WINDOW_SAMPLES samples over the full steady window. */
    private static final int MIN_WINDOW_SAMPLES = 21;

    private static double[] loadWindowFromHistory(File dir, int steadyWindow, double thrust, double torque,
                                                 double rho, double n, double diameter) throws Exception {
        java.util.TreeMap<Integer, double[]> merged = new java.util.TreeMap<Integer, double[]>();
        File[] files = dir.listFiles();
        if (files != null) {
            for (File file : files) {
                String name = file.getName();
                if (!file.isFile() || !name.startsWith("mrf_") || !name.contains("history") || !name.endsWith(".tsv")) continue;
                BufferedReader reader = new BufferedReader(new FileReader(file));
                try {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        if (line.startsWith("#")) continue;
                        String[] parts = line.split("\t");
                        if (parts.length < 5 || "iteration".equals(parts[0].trim())) continue;
                        try {
                            int iteration = Integer.parseInt(parts[0].trim());
                            double ctValue = Double.parseDouble(parts[3].trim());
                            double cpValue = Double.parseDouble(parts[4].trim());
                            if (!Double.isFinite(ctValue) || !Double.isFinite(cpValue)) continue;
                            merged.put(iteration, new double[] {ctValue, cpValue});
                        } catch (NumberFormatException skip) { }
                    }
                } finally { reader.close(); }
            }
        }
        if (merged.isEmpty()) {
            double ct = thrust / (rho * n * n * Math.pow(diameter, 4.0));
            double cp = 2.0 * Math.PI * torque / (rho * n * n * Math.pow(diameter, 5.0));
            return new double[] {Double.NaN, Double.NaN, ct, cp, 0, 0, 0};
        }
        int last = merged.lastKey();
        double cMin = Double.MAX_VALUE, cMax = -Double.MAX_VALUE, pMin = Double.MAX_VALUE, pMax = -Double.MAX_VALUE;
        double cSum = 0, pSum = 0;
        int count = 0, first = Integer.MIN_VALUE;
        for (Map.Entry<Integer, double[]> entry : merged.entrySet()) {
            if (entry.getKey() < last - steadyWindow) continue;
            if (first == Integer.MIN_VALUE) first = entry.getKey();
            cMin = Math.min(cMin, entry.getValue()[0]); cMax = Math.max(cMax, entry.getValue()[0]);
            pMin = Math.min(pMin, entry.getValue()[1]); pMax = Math.max(pMax, entry.getValue()[1]);
            cSum += entry.getValue()[0]; pSum += entry.getValue()[1]; count++;
        }
        if (count == 0) {
            double ct = thrust / (rho * n * n * Math.pow(diameter, 4.0));
            double cp = 2.0 * Math.PI * torque / (rho * n * n * Math.pow(diameter, 5.0));
            return new double[] {Double.NaN, Double.NaN, ct, cp, 0, 0, 0};
        }
        int span = last - first;
        boolean covered = count >= MIN_WINDOW_SAMPLES && span >= steadyWindow;
        return new double[] {(cMax - cMin) / Math.abs(cSum / count), (pMax - pMin) / Math.abs(pSum / count),
                0, 0, count, span, covered ? 1 : 0};
    }

    private static double measureMassImbalance(Simulation sim) {
        double net = 0, inletMagnitude = 0;
        List<Report> temporary = new ArrayList<Report>();
        try {
            for (Region region : sim.getRegionManager().getRegions()) {
                for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                    if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                    MassFlowReport report = (MassFlowReport) sim.getReportManager().create("star.flow.MassFlowReport");
                    temporary.add(report);
                    report.getParts().setObjects(boundary);
                    double value = report.getReportMonitorValue();
                    if (!Double.isFinite(value)) return Double.NaN;
                    net += value;
                    inletMagnitude = Math.max(inletMagnitude, Math.abs(value));
                }
            }
            if (!(inletMagnitude > 0)) return Double.NaN;
            return Math.abs(net) / inletMagnitude;
        } finally {
            for (Report report : temporary) {
                try { sim.deleteObjects(Collections.singletonList(report)); } catch (Throwable ignored) { }
            }
        }
    }

    private static double[] yplusStats(Simulation sim, String rotorName, String statorName, double yMax,
                                       double yAvg, double yCovFrac, double yCovLimit, String[] detail) throws Exception {
        FvRepresentation mesh = null;
        for (Representation representation : sim.getRepresentationManager().getObjects()) {
            if (representation instanceof FvRepresentation) mesh = (FvRepresentation) representation;
        }
        if (mesh == null) throw new IllegalStateException("no volume mesh");
        Region rotor = sim.getRegionManager().getRegion(rotorName);
        Region stator = sim.getRegionManager().getRegion(statorName);
        if (rotor == null || stator == null) throw new IllegalStateException("regions missing");
        List<Boundary> walls = new ArrayList<Boundary>();
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            FieldFunction yplus = null;
            for (FieldFunction candidate : sim.getFieldFunctionManager().getObjects()) {
                String name = candidate.getFunctionName();
                if ("WallYplus".equals(name) || "WallYPlus".equals(name)) { yplus = candidate; break; }
            }
            if (yplus == null) throw new IllegalStateException("WallYplus field unavailable");
            // Native interface-parent ownership (same rule as part4 selectRotorMaterialWalls): the interface
            // parent boundaries may still carry WallBoundary type but are NOT physical propeller walls.
            Set<Boundary> interfaceParents = new HashSet<Boundary>();
            for (Interface inter : sim.getInterfaceManager().getObjects()) {
                if (!(inter instanceof BoundaryInterface)) throw new IllegalStateException("unsupported interface type");
                BoundaryInterface pair = (BoundaryInterface) inter;
                Boundary a = pair.getParentBoundary0();
                Boundary b = pair.getParentBoundary1();
                if (a == null || b == null || a.equals(b)) throw new IllegalStateException("interface has bad parents");
                interfaceParents.add(a);
                interfaceParents.add(b);
            }
            for (Boundary boundary : rotor.getBoundaryManager().getBoundaries()) {
                if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                if (interfaceParents.contains(boundary)) continue;
                walls.add(boundary);
            }
            if (walls.isEmpty()) throw new IllegalStateException("no native rotor material-wall boundaries");
            StringBuilder wallNames = new StringBuilder();
            for (Boundary boundary : walls) {
                if (wallNames.length() > 0) wallNames.append(';');
                wallNames.append(boundary.getPresentationName());
            }
            Units dimensionless = sim.getUnitsManager().getObject("");
            MinReport min = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
            temporary.add(min); min.setRepresentation(mesh); min.setFieldFunction(yplus);
            min.getParts().setObjects(walls.toArray(new NamedObject[0]));
            AreaAverageReport average = sim.getReportManager().createReport(AreaAverageReport.class);
            temporary.add(average); average.setRepresentation(mesh); average.setFieldFunction(yplus);
            average.getParts().setObjects(walls.toArray(new NamedObject[0]));
            MaxReport max = (MaxReport) sim.getReportManager().create("star.base.report.MaxReport");
            temporary.add(max); max.setRepresentation(mesh); max.setFieldFunction(yplus);
            max.getParts().setObjects(walls.toArray(new NamedObject[0]));
            double minValue = min.getReportMonitorValue();
            double averageValue = average.getReportMonitorValue();
            double maxValue = max.getReportMonitorValue();
            UserFieldFunction indicator = sim.getFieldFunctionManager().createFieldFunction();
            temporary.add(indicator);
            indicator.setFunctionName("TEMP_YS_IND_" + temporary.size());
            indicator.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
            indicator.setDefinition("(${WallYplus} <= " + yCovLimit + ") ? 1 : 0");
            AreaAverageReport coverage = sim.getReportManager().createReport(AreaAverageReport.class);
            temporary.add(coverage); coverage.setRepresentation(mesh); coverage.setFieldFunction(indicator);
            coverage.getParts().setObjects(walls.toArray(new NamedObject[0]));
            double coverageFraction = coverage.getReportMonitorValue();
            detail[0] = wallNames.toString();
            detail[1] = String.valueOf(walls.size());
            return new double[] {minValue, averageValue, maxValue, maxValue, averageValue, coverageFraction};
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject object : temporary) {
                try { sim.deleteObjects(Collections.singletonList(object)); } catch (Throwable ignored) { }
            }
        }
    }

    /**
     * Signed reference points live at data.selected_run.points[] with keys j/ct/cp (reference.json
     * 44d0ce79...). Select by closest J and FAIL CLOSED on a missing/non-finite selection: the old
     * parser looked for a non-existent operating_points/ct_ref/cp_ref schema (review-3s blocking 1).
     */
    private static double[] referencePoint(String reference, double advance) {
        if (!Double.isFinite(advance)) throw new IllegalStateException("advance ratio is not measured");
        String data = object(reference, "data");
        String selectedRun = object(data, "selected_run");
        int pointsKey = selectedRun.indexOf("\"points\"");
        if (pointsKey < 0) throw new IllegalStateException("reference.json has no selected_run.points");
        int arrayStart = selectedRun.indexOf('[', pointsKey);
        if (arrayStart < 0) throw new IllegalStateException("selected_run.points is not an array");
        int arrayEnd = selectedRun.indexOf(']', arrayStart);
        if (arrayEnd < 0) throw new IllegalStateException("unterminated selected_run.points array");
        String arrayBody = selectedRun.substring(arrayStart + 1, arrayEnd);
        double best = Double.MAX_VALUE, ct = Double.NaN, cp = Double.NaN;
        int cursor = 0;
        while (true) {
            int braceStart = arrayBody.indexOf('{', cursor);
            if (braceStart < 0) break;
            int braceEnd = arrayBody.indexOf('}', braceStart);
            if (braceEnd < 0) throw new IllegalStateException("unterminated reference point object");
            String block = arrayBody.substring(braceStart, braceEnd + 1);
            try {
                double j = number(block, "j");
                double ctv = number(block, "ct");
                double cpv = number(block, "cp");
                if (Math.abs(j - advance) < best) { best = Math.abs(j - advance); ct = ctv; cp = cpv; }
            } catch (RuntimeException missingField) {
                throw new IllegalStateException("reference point lacks j/ct/cp: " + block, missingField);
            }
            cursor = braceEnd + 1;
        }
        if (!Double.isFinite(ct) || !Double.isFinite(cp)) {
            throw new IllegalStateException("no finite signed reference point for J=" + advance);
        }
        return new double[] {ct, cp};
    }

    private static double[] array(String json, String key) {
        int start = valueStart(json, key);
        if (json.charAt(start) != '[') throw new IllegalArgumentException("not an array: " + key);
        int end = json.indexOf(']', start);
        String body = json.substring(start + 1, end);
        String[] parts = body.split(",");
        double[] values = new double[parts.length];
        for (int i = 0; i < parts.length; i++) values[i] = Double.parseDouble(parts[i].trim());
        return values;
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
