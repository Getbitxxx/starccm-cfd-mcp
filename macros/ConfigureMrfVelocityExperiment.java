/* Generic MRF freestream-velocity experiment (P2.3 G5 part12): apply EXACTLY ONE declared change - the
 * inlet velocity magnitude that sets the advance ratio J = V/(nD) - to a saved checkpoint, with full
 * BEFORE/AFTER readback and fail-closed drift/no-op guards.
 *
 * Contract: a SEPARATE, hash-pinned velocity_experiment.json (the frozen CaseSpec is not edited):
 *   schema_version, experiment_id, single_change {parameter:"inlet_velocity_magnitude", mode:"CONSTANT",
 *   from_mps, to_mps, target_j, reference_j}, basis,
 *   expected_before {inlet_velocity_mps, inlet_velocity_option, inlet_direction_option},
 *   expected_after {inlet_velocity_mps}, unchanged_fields[...].
 *
 * The macro verifies the measured inlet magnitude against from_mps and FAILS CLOSED if it differs (it
 * never re-derives the target), rejects a no-op, applies ONLY the inlet VelocityMagnitudeProfile, reads
 * AFTER, requires the requested equality, verifies the inlet temperature/intensity/viscosity-ratio
 * profiles and the whole boundary-type inventory are unchanged, and saves only the configured sim.
 * It does not initialize, iterate or solve; initial conditions are left untouched (a continuation never
 * reads them).
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
import java.util.Map;

import star.base.neo.*;
import star.common.*;
import star.energy.*;
import star.flow.*;
import star.turbulence.*;

public class ConfigureMrfVelocityExperiment extends StarMacro {

    private static final String COMPLETE = "MRF_VELOCITY_EXPERIMENT_CONFIGURED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "velocity_experiment.json"));
            String caseSpec = read(new File(dir, "case_spec.json"));
            String change = object(spec, "single_change");
            String expectedBefore = object(spec, "expected_before");
            String expectedAfter = object(spec, "expected_after");
            String experimentId = string(spec, "experiment_id");
            String parameter = string(change, "parameter");
            String mode = string(change, "mode");
            double fromMps = number(change, "from_mps");
            double toMps = number(change, "to_mps");
            double targetJ = number(change, "target_j");
            if (!"inlet_velocity_magnitude".equals(parameter)) throw new IllegalStateException("Only the reviewed inlet_velocity_magnitude experiment is allowed");
            if (!"CONSTANT".equals(mode)) throw new IllegalStateException("Only the reviewed CONSTANT inlet mode is allowed");

            String inletName = string(object(caseSpec, "boundary_roles"), "inlet");
            Boundary inlet = null;
            String inletOwner = null;
            List<String> boundaryInventory = new ArrayList<String>();
            for (Region region : sim.getRegionManager().getRegions()) {
                for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                    String label = region.getPresentationName() + "/" + boundary.getPresentationName();
                    boundaryInventory.add(label + ":" + String.valueOf(boundary.getBoundaryType()));
                    if (inletName.equals(boundary.getPresentationName())) {
                        if (inlet != null) throw new IllegalStateException("Boundary name is not unique: " + inletName);
                        inlet = boundary;
                        inletOwner = region.getPresentationName();
                    }
                }
            }
            if (inlet == null) throw new IllegalStateException("Inlet boundary not found: " + inletName);
            if (!(inlet.getBoundaryType() instanceof InletBoundary)) {
                throw new IllegalStateException("Inlet boundary is not an InletBoundary: " + inletName);
            }
            String velocityOption = inlet.getConditions().get(InletVelocityOption.class).getSelectedElement().toString();
            String directionOption = inlet.getConditions().get(FlowDirectionOption.class).getSelectedElement().toString();
            require(velocityOption.toUpperCase().contains("MAGNITUDE_DIRECTION"), "Inlet is not MAGNITUDE_DIRECTION: " + velocityOption);
            require(directionOption.toUpperCase().contains("BOUNDARY_NORMAL"), "Inlet is not BOUNDARY_NORMAL: " + directionOption);

            double current = readConstant(inlet.getValues().get(VelocityMagnitudeProfile.class));
            double temperatureBefore = readConstant(inlet.getValues().get(StaticTemperatureProfile.class));
            double intensityBefore = readConstant(inlet.getValues().get(TurbulenceIntensityProfile.class));
            double viscosityBefore = readConstant(inlet.getValues().get(TurbulentViscosityRatioProfile.class));
            require(close(current, fromMps, 1e-9), "Inlet velocity drifted from the frozen from_mps: " + current
                    + " vs " + fromMps + " - fail closed instead of re-scaling");
            require(close(current, number(expectedBefore, "inlet_velocity_mps"), 1e-9),
                    "Inlet velocity does not match expected_before");
            if (close(current, toMps, 1e-12)) throw new IllegalStateException("Experiment is a no-op: target velocity already set");

            int iteration = sim.getSimulationIterator().getCurrentIteration();
            String ic = icVelocityText(sim);
            setConstant(inlet.getValues().get(VelocityMagnitudeProfile.class), toMps);
            double after = readConstant(inlet.getValues().get(VelocityMagnitudeProfile.class));
            require(close(after, toMps, 1e-9), "Configured inlet velocity does not read back as the requested target");
            require(close(after, number(expectedAfter, "inlet_velocity_mps"), 1e-9), "Readback does not match expected_after");
            require(close(readConstant(inlet.getValues().get(StaticTemperatureProfile.class)), temperatureBefore, 1e-12),
                    "Undeclared inlet temperature profile changed");
            require(close(readConstant(inlet.getValues().get(TurbulenceIntensityProfile.class)), intensityBefore, 1e-12),
                    "Undeclared inlet turbulence-intensity profile changed");
            require(close(readConstant(inlet.getValues().get(TurbulentViscosityRatioProfile.class)), viscosityBefore, 1e-12),
                    "Undeclared inlet viscosity-ratio profile changed");
            List<String> boundaryAfter = new ArrayList<String>();
            for (Region region : sim.getRegionManager().getRegions()) {
                for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                    boundaryAfter.add(region.getPresentationName() + "/" + boundary.getPresentationName()
                            + ":" + String.valueOf(boundary.getBoundaryType()));
                }
            }
            require(boundaryInventory.equals(boundaryAfter), "Boundary inventory/type changed during the velocity experiment");

            File output = new File(dir, "mrf_velocity_experiment_configured.sim");
            sim.saveState(output.getAbsolutePath());
            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"solver\":\"ConfigureMrfVelocityExperiment.java\",")
                .append("\"applied\":true,\"experiment_id\":\"").append(esc(experimentId)).append('"')
                .append(",\"iteration\":").append(iteration)
                .append(",\"target_j\":").append(targetJ)
                .append(",\"inlet_boundary\":\"").append(esc(inletName)).append('"')
                .append(",\"inlet_owner_region\":\"").append(esc(String.valueOf(inletOwner))).append('"')
                .append(",\"changed_fields\":[\"inlet_velocity_magnitude\"]")
                .append(",\"from_mps\":").append(fromMps).append(",\"to_mps\":").append(toMps)
                .append(",\"measured_before_mps\":").append(current).append(",\"measured_after_mps\":").append(after)
                .append(",\"no_op\":false")
                .append(",\"unchanged_profiles_verified\":true,\"boundary_inventory_unchanged\":true")
                .append(",\"before_option\":\"").append(esc(velocityOption)).append('"')
                .append(",\"before_direction\":\"").append(esc(directionOption)).append('"')
                .append(",\"initial_condition_velocity_mps\":\"").append(esc(ic)).append('"')
                .append(",\"initial_condition_modified\":false")
                .append(",\"expected_before_verified\":true,\"expected_after_verified\":true")
                .append(",\"note\":\"ONE documented freestream change applied and read back: inlet velocity magnitude only ")
                .append("(initial conditions are not modified and are unused by a continuation segment, which never initializes). ")
                .append("J = V/(nD) with nD=25.4635 m/s; this is a diagnostic/continuation operating point and the target J is one ")
                .append("of the signed J points. No initialize/iterate/solve.\"}\n");
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_velocity_experiment.json")), StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_velocity_experiment_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF velocity experiment configuration failed", failure);
        }
    }

    private static String icVelocityText(Simulation sim) {
        try {
            for (Continuum continuum : sim.getContinuumManager().getObjects()) {
                if (continuum instanceof PhysicsContinuum) {
                    return String.valueOf(((PhysicsContinuum) continuum).getInitialConditions()
                            .get(VelocityProfile.class).getMethod(ConstantVectorProfileMethod.class)
                            .getQuantity());
                }
            }
        } catch (Throwable unavailable) {
            // the IC value is recorded for traceability only; a continuation never reads it
        }
        return "unavailable";
    }

    private static void setConstant(ScalarProfile profile, double siValue) {
        ConstantScalarProfileMethod method = profile.getMethod(ConstantScalarProfileMethod.class);
        method.getQuantity().setValue(siValue);
        double actual = method.getQuantity().getSIValue();
        if (!Double.isFinite(actual) || Math.abs(actual - siValue) > 1e-8 * Math.max(1.0, Math.abs(siValue))) {
            throw new IllegalStateException("An inlet value did not retain its SI value");
        }
    }

    private static double readConstant(ScalarProfile profile) {
        double actual = profile.getMethod(ConstantScalarProfileMethod.class).getQuantity().getSIValue();
        if (!Double.isFinite(actual)) throw new IllegalStateException("An inlet value reads back non-finite");
        return actual;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private static boolean close(double a, double b, double relTolerance) {
        return Math.abs(a - b) <= relTolerance * Math.max(1.0, Math.abs(b));
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
