/* Generic MRF solver-settings inspector (P2.3 G5 part9): READ-ONLY inventory of the loaded simulation's
 * solver/numerics settings, so the next solver experiment can change exactly ONE documented variable.
 *
 * Why reflection: the installed 2502 API for coupled-solver CFL / AMG cycles / transition settings is not
 * fully documented in the operation catalog, and guessing setter names risks silently changing nothing.
 * This macro therefore reads only (never writes): it walks the physics-continuum models and the solver
 * stopping criteria, and for a set of safe zero-argument getters whose names match CFL/Courant/AMG/Cycle/
 * Tolerance/Relax/Iteration it records the class, the getter, and the returned scalar value. Missing
 * getters are skipped (not invented), and the record states exactly what was and was not readable.
 *
 * No initialization, no iteration, no meshing, no save. Input: a loaded MRF simulation + case_spec.json.
 * Output: mrf_solver_settings.json (+ .tsv) and MRF_SOLVER_SETTINGS_INSPECTED.
 */
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import star.base.neo.*;
import star.common.*;
import star.flow.*;
import star.coupledflow.*;
import star.kwturb.*;

public class InspectMrfSolverSettings extends StarMacro {

    private static final String COMPLETE = "MRF_SOLVER_SETTINGS_INSPECTED";
    private static final String[] NAME_FILTERS = {
        "cfl", "courant", "amg", "cycle", "tolerance", "relax", "iteration", "multigrid", "verbosity", "convection",
        "transition", "gamma", "retheta", "intermittency" };

    private static final class Entry {
        String owner;
        String getter;
        String value;
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String acceptance = object(spec, "acceptance");
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            boolean solutionLoaded = iteration > 0;

            List<Entry> entries = new ArrayList<Entry>();
            List<String> skipped = new ArrayList<String>();
            boolean[] family = {false, false, false}; // cfl/courant, amg/cycle/multigrid, transition/gamma/retheta/intermittency
            List<String> modelClasses = new ArrayList<String>();
            for (Continuum continuum : sim.getContinuumManager().getObjects()) {
                if (!(continuum instanceof PhysicsContinuum)) continue;
                PhysicsContinuum physics = (PhysicsContinuum) continuum;
                for (Model model : physics.getModelManager().getObjects()) {
                    modelClasses.add(model.getClass().getName());
                    inspect(model, "model:" + model.getClass().getSimpleName(), entries, skipped, family);
                }
            }
            List<String> criteria = new ArrayList<String>();
            for (SolverStoppingCriterion criterion : sim.getSolverStoppingCriterionManager().getObjects()) {
                criteria.add(criterion.getClass().getSimpleName() + ":" + criterion.getPresentationName()
                        + ":used=" + criterion.getIsUsed());
                inspect(criterion, "criterion:" + criterion.getPresentationName(), entries, skipped, family);
            }
            boolean inspectionComplete = !entries.isEmpty();
            boolean familyCflAmg = family[0] && family[1];
            boolean familyTransition = family[2];
            boolean baselineAvailable = inspectionComplete && familyCflAmg && familyTransition;
            String familiesMissing;
            if (!familyCflAmg && !familyTransition) familiesMissing = "cfl_amg,transition";
            else if (!familyCflAmg) familiesMissing = "cfl_amg";
            else if (!familyTransition) familiesMissing = "transition";
            else familiesMissing = "";

            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"ok\",\"inspector\":\"InspectMrfSolverSettings.java\",")
                .append("\"read_only\":true,\"iterations\":").append(iteration)
                .append(",\"solution_loaded\":").append(solutionLoaded)
                .append(",\"models\":").append(modelClasses.size())
                .append(",\"readable_settings\":").append(entries.size())
                .append(",\"skipped_getters\":").append(skipped.size())
                .append(",\"family_cfl_amg\":").append(familyCflAmg)
                .append(",\"family_transition\":").append(familyTransition)
                .append(",\"families_missing\":\"").append(familiesMissing).append('"')
                .append(",\"baseline_available\":").append(baselineAvailable)
                .append(",\"inspection_complete\":").append(inspectionComplete)
                .append(",\"model_classes\":[");
            for (int i = 0; i < modelClasses.size(); i++) {
                if (i > 0) json.append(',');
                json.append('"').append(esc(modelClasses.get(i))).append('"');
            }
            json.append("],\"stopping_criteria\":[");
            for (int i = 0; i < criteria.size(); i++) {
                if (i > 0) json.append(',');
                json.append('"').append(esc(criteria.get(i))).append('"');
            }
            json.append("],\"settings\":[");
            boolean first = true;
            for (Entry entry : entries) {
                if (!first) json.append(',');
                first = false;
                json.append("{\"owner\":\"").append(esc(entry.owner)).append('"')
                    .append(",\"getter\":\"").append(esc(entry.getter)).append('"')
                    .append(",\"value\":\"").append(esc(entry.value)).append("\"}");
            }
            json.append("],\"skipped\":[");
            for (int i = 0; i < skipped.size(); i++) {
                if (i > 0) json.append(',');
                json.append('"').append(esc(skipped.get(i))).append('"');
            }
            json.append("],\"name_filters\":[");
            for (int i = 0; i < NAME_FILTERS.length; i++) {
                if (i > 0) json.append(',');
                json.append('"').append(NAME_FILTERS[i]).append('"');
            }
            json.append("],\"note\":\"read-only reflective inventory: only zero-argument getters whose names match the documented ")
                .append("filters are read; every value is a measurement of the loaded state. Missing getters are skipped, not invented. ")
                .append("No initialization, iteration or save. Getter-only rule: names must start with get/is/has/query/retrieve/list and must not start with a mutator prefix; physical quantities are rendered via toString, enums by name, other objects are recorded in skipped[] with a reason. baseline_available requires at least one measured CFL/AMG/cycle/multigrid setting AND at least one measured transition/gamma/retheta/intermittency setting; families_missing names the uncovered family. When false the record is an explicit NO-BASELINE result, not a PASS. This record is the baseline for ONE-change solver experiments.\"}\n");
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_settings.json")), StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            try (Writer tsv = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_settings.tsv")), StandardCharsets.UTF_8)) {
                tsv.write("OWNER\tGETTER\tVALUE\n");
                for (Entry entry : entries) tsv.write(entry.owner + "\t" + entry.getter + "\t" + entry.value + "\n");
                tsv.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (Writer out = new OutputStreamWriter(new FileOutputStream(
                    new File(dir, "mrf_solver_settings_failure.json")), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF solver-settings inspection failed", failure);
        }
    }

    private static final String[] FORBIDDEN_PREFIXES = {"set", "clear", "reset", "initialize", "update",
        "enable", "disable", "run", "step", "apply", "delete", "create", "save", "load", "start", "stop", "remove", "add"};

    /** Getter-only rule: the name must read as a getter AND must not read as a mutator. */
    private static boolean isGetterName(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        boolean getter = lower.startsWith("get") || lower.startsWith("is") || lower.startsWith("has")
                || lower.startsWith("query") || lower.startsWith("retrieve") || lower.startsWith("list");
        if (!getter) return false;
        for (String forbidden : FORBIDDEN_PREFIXES) {
            if (lower.startsWith(forbidden)) return false;
        }
        return true;
    }

    private static void inspect(Object owner, String label, List<Entry> entries, List<String> skipped,
                                boolean[] family) {
        Method[] methods;
        try { methods = owner.getClass().getMethods(); } catch (Throwable none) { return; }
        for (Method method : methods) {
            if (method.getParameterCount() != 0) continue;
            String name = method.getName().toLowerCase(Locale.ROOT);
            boolean matches = false;
            for (String filter : NAME_FILTERS) {
                if (name.contains(filter)) { matches = true; break; }
            }
            if (!matches) continue;
            if (!isGetterName(method.getName())) {
                skipped.add(label + "." + method.getName() + ":not_a_getter");
                continue;
            }
            Class<?> type = method.getReturnType();
            Object value;
            try {
                value = method.invoke(owner);
            } catch (Throwable unreadable) {
                skipped.add(label + "." + method.getName() + ":unreadable:" + unreadable.getClass().getSimpleName());
                continue;
            }
            if (value == null) {
                skipped.add(label + "." + method.getName() + ":null");
                continue;
            }
            String rendered;
            if (type == int.class || type == long.class || type == double.class || type == float.class
                    || type == boolean.class || type == String.class || Number.class.isAssignableFrom(type)) {
                rendered = String.valueOf(value);
            } else if (value instanceof PhysicalQuantity) {
                rendered = String.valueOf(value);
            } else if (value instanceof Enum) {
                rendered = ((Enum<?>) value).name();
            } else if (value instanceof Units) {
                rendered = String.valueOf(value);
            } else {
                skipped.add(label + "." + method.getName() + ":non_scalar:" + value.getClass().getSimpleName());
                continue;
            }
            Entry entry = new Entry();
            entry.owner = label;
            entry.getter = method.getName();
            entry.value = rendered;
            entries.add(entry);
            String lowerName = method.getName().toLowerCase(Locale.ROOT);
            if (lowerName.contains("cfl") || lowerName.contains("courant")) family[0] = true;
            if (lowerName.contains("amg") || lowerName.contains("cycle") || lowerName.contains("multigrid")) family[1] = true;
            if (lowerName.contains("transition") || lowerName.contains("gamma")
                    || lowerName.contains("retheta") || lowerName.contains("intermittency")) family[2] = true;
        }
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
