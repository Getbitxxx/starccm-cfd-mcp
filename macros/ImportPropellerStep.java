/* Import a propeller STEP solid with an explicit target part name (Phase 2 geometry entry).
 *
 * Why this macro exists: the Phase-1 ImportStepToSim.java requires the imported part's presentation
 * name to equal body_part_name and additionally gates on the E387 single-B-spline patch map
 * ("one_group_expected_faces"), which is not the right gate for a 90-face propeller B-rep. This macro
 * does only the import contract the next stage needs:
 *   - new empty session, STEP named <body_part_name>.step, input bytes hash-pinned by the caller,
 *   - import via the proven PartImportManager.importCadParts call,
 *   - require exactly one imported solid leaf, rename it to body_part_name (recording the original
 *     name), require closed + manifold + triangles > 0,
 *   - set the CaseSpec preferred length unit so the saved .sim is measured in SI by the audit stage,
 *   - save the imported .sim and a measured import record; fail closed with a diagnostic file.
 *
 * It creates no region, continuum, boundary, mesh or solution and does not initialize.
 *
 * Environment (set by the reviewed runner):
 *   PROP_JOB_DIR      absolute job directory containing <body>.step and case_spec.json
 *   PROP_STEP_SHA256  expected sha256 of <body>.step
 *   PROP_SPEC_SHA256  expected sha256 of case_spec.json
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Collection;
import java.util.Collections;

import star.common.*;
import star.meshing.*;

public class ImportPropellerStep extends StarMacro {

    private static final String COMPLETE = "PROP_STEP_IMPORT_COMPLETE";

    private static String sha(File file) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));
        StringBuilder value = new StringBuilder();
        for (byte item : digest) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    private static String string(String json, String key) {
        java.util.regex.Matcher match = java.util.regex.Pattern
                .compile("\\\"" + java.util.regex.Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("CaseSpec is missing string: " + key);
        return match.group(1);
    }

    private static File dir() {
        String path = System.getenv("PROP_JOB_DIR");
        if (path == null || path.trim().isEmpty()) throw new IllegalStateException("PROP_JOB_DIR is required");
        File dir = new File(path);
        if (!dir.isDirectory()) throw new IllegalStateException("PROP_JOB_DIR is not a directory");
        return dir;
    }

    @Override public void execute() {
        File job = dir();
        StringBuilder report = new StringBuilder();
        try {
            File specFile = new File(job, "case_spec.json");
            if (!specFile.isFile()) throw new IllegalStateException("case_spec.json missing in job dir");
            String spec = new String(Files.readAllBytes(specFile.toPath()), StandardCharsets.UTF_8);
            String bodyName = string(spec, "body_part_name");
            if (!bodyName.matches("[A-Za-z0-9_-]{1,64}")) throw new IllegalArgumentException("Unsafe body_part_name");
            String unitName = string(spec, "model_length_unit");

            Simulation sim = getActiveSimulation();
            if (sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration() != 0
                    || !sim.getRegionManager().getObjects().isEmpty()
                    || !sim.getContinuumManager().getObjects().isEmpty()
                    || !sim.get(SimulationPartManager.class).getParts().isEmpty())
                throw new IllegalStateException("A new empty session is required");

            File step = new File(job, bodyName + ".step");
            if (!step.isFile()) throw new IllegalStateException("STEP missing: " + step.getAbsolutePath());
            String stepSha = sha(step), specSha = sha(specFile);
            String expectedStep = System.getenv("PROP_STEP_SHA256"), expectedSpec = System.getenv("PROP_SPEC_SHA256");
            if (expectedStep != null && !expectedStep.isEmpty() && !stepSha.equals(expectedStep))
                throw new IllegalStateException("STEP sha256 differs from the reviewed input");
            if (expectedSpec != null && !expectedSpec.isEmpty() && !specSha.equals(expectedSpec))
                throw new IllegalStateException("CaseSpec sha256 differs from the reviewed input");

            int expectedFaces = 0;
            java.util.regex.Matcher faces = java.util.regex.Pattern.compile("=\\s*ADVANCED_FACE\\(")
                    .matcher(new String(Files.readAllBytes(step.toPath()), StandardCharsets.UTF_8));
            while (faces.find()) expectedFaces++;
            if (expectedFaces <= 0) throw new IllegalStateException("STEP has no ADVANCED_FACE");
            report.append("STEP_FACES\t").append(expectedFaces).append('\n');

            sim.get(PartImportManager.class).importCadParts(Collections.singletonList(step.getAbsolutePath()),
                    "default", 30.0, 1, false, 1.0e-5, false, false, false, false);
            Collection<GeometryPart> leaves = sim.get(SimulationPartManager.class).getLeafParts();
            if (leaves.size() != 1) throw new IllegalStateException("Expected one imported solid leaf, got " + leaves.size());
            GeometryPart leaf = leaves.iterator().next();
            if (!(leaf instanceof MeshPart)) throw new IllegalStateException("Imported leaf is not a mesh part: "
                    + leaf.getClass().getName());
            MeshPart part = (MeshPart) leaf;
            String importedName = part.getPresentationName();
            report.append("IMPORTED_NAME\t").append(importedName).append('\n');
            if (!bodyName.equals(importedName)) {
                part.setPresentationName(bodyName);
                report.append("RENAMED_TO\t").append(bodyName).append('\n');
            }
            if (!bodyName.equals(part.getPresentationName()))
                throw new IllegalStateException("Rename did not stick: " + part.getPresentationName());
            if (!part.isClosed()) throw new IllegalStateException("Imported part is not closed");
            if (!part.isManifold()) throw new IllegalStateException("Imported part is not manifold");
            int triangles = part.getTriangleCount();
            if (triangles <= 0) throw new IllegalStateException("Imported part has no triangles");
            int surfaces = part.getPartSurfaces().size();
            String partClass = part.getClass().getName();

            // The audit stage measures the SAVED .sim in SI, so the preferred length unit is written
            // here from the CaseSpec and read back before saving.
            Units preferred = sim.getUnitsManager().getObject(unitName);
            if (preferred == null) throw new IllegalArgumentException("Unknown CaseSpec model_length_unit: " + unitName);
            // Proven 2502 form (ImportStepToSim): clear the old preferred length unit, then set the new one.
            // Readback is compared as a Units OBJECT: Units.getSuffix() returns a decorated string such as
            // " (m)" on this install (measured 2026-10-28 by the closed-TE diagnostic import), so a suffix
            // string comparison would refuse a correct unit.
            Units old = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
            if (old != null && !old.equals(preferred)) old.setPreferred(false);
            preferred.setPreferred(true);
            Units preferredNow = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
            if (preferredNow == null || !preferredNow.equals(preferred))
                throw new IllegalStateException("Preferred length unit did not stick: " + preferredNow);
            String readback = preferredNow.getPresentationName();

            File output = new File(job, bodyName + "_import.sim");
            if (output.exists()) throw new IllegalStateException("Refuse checkpoint replacement: " + output.getName());
            sim.saveState(output.getAbsolutePath());

            try (FileWriter out = new FileWriter(new File(job, bodyName + "_import.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"source\":\"ImportPropellerStep.java\""
                        + ",\"step_sha256\":\"" + stepSha + "\",\"casespec_sha256\":\"" + specSha + "\""
                        + ",\"part\":\"" + bodyName + "\",\"imported_name\":\"" + importedName + "\""
                        + ",\"renamed\":" + (!bodyName.equals(importedName)) + ",\"part_class\":\"" + partClass + "\""
                        + ",\"is_closed\":true,\"is_manifold\":true,\"solid_count\":1"
                        + ",\"part_surfaces\":" + surfaces + ",\"triangles\":" + triangles
                        + ",\"step_advanced_faces\":" + expectedFaces
                        + ",\"model_length_unit\":\"" + unitName + "\",\"preferred_unit_readback\":\"" + readback + "\""
                        + ",\"import_sim_sha256\":\"" + sha(output) + "\""
                        + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(job, "prop_step_import_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(report.toString());
                out.write("FAILED\t" + failure.getClass().getName() + "\t"
                        + String.valueOf(failure.getMessage()).replace('\n', ' ') + "\n");
            } catch (Throwable ignored) {
                // the original failure is rethrown below; the diagnostic must not mask it
            }
            throw new RuntimeException("Propeller STEP import failed: " + failure.getMessage(), failure);
        }
    }
}
