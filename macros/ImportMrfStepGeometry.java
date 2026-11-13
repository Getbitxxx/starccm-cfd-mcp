/* Import a STEP geometry into an empty 2502 simulation (registered finite-TE variant pipeline).
 * Geometry only: imports parts, renames the imported part to CaseSpec.recovered_rotor_part (the roles used by
 * the accepted AuditMrfRotorSolid/BuildMrfFluidVolumes macros), records the source variant name, and saves.
 * No regions/mesh/physics, no initialization, no iteration.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Collections;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.*;
import star.meshing.*;

public class ImportMrfStepGeometry extends StarMacro {

    private static final String COMPLETE = "MRF_STEP_GEOMETRY_IMPORTED";

    public void execute() {
        try {
            run();
        } catch (Exception failure) {
            throw new RuntimeException("STEP import failed", failure);
        }
    }

    private void run() throws Exception {
        Simulation sim = getActiveSimulation();
        if (!sim.get(SimulationPartManager.class).getParts().isEmpty()) {
            throw new IllegalStateException("New empty simulation required");
        }
        File dir = new File(sim.getSessionDir());
        File step = new File(dir, "geometry_variant.step");
        if (!step.isFile() || step.length() <= 0) {
            throw new IllegalStateException("geometry_variant.step missing from the job directory");
        }
        String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
        String target = string(spec, "recovered_rotor_part");
        sim.get(PartImportManager.class).importCadParts(
                Collections.singletonList(step.getAbsolutePath()), "default", 30.0, 1, false, 1.0e-5,
                false, false, false, false);
        SimulationPartManager parts = sim.get(SimulationPartManager.class);
        List<GeometryPart> imported = new ArrayList<GeometryPart>(parts.getParts());
        if (imported.isEmpty()) throw new IllegalStateException("no part imported");
        StringBuilder source = new StringBuilder();
        for (GeometryPart part : imported) {
            if (source.length() > 0) source.append(';');
            source.append(part.getPresentationName());
        }
        for (GeometryPart part : imported) {
            part.setPresentationName(target);
        }
        String renamed = new ArrayList<GeometryPart>(parts.getParts()).get(0).getPresentationName();
        if (!target.equals(renamed)) throw new IllegalStateException("part rename did not take effect: " + renamed);
        try (FileWriter out = new FileWriter(new File(dir, "geometry_import.json"), StandardCharsets.UTF_8)) {
            out.write("{\"schema_version\":1,\"status\":\"ok\",\"step_file\":\"geometry_variant.step\""
                    + ",\"imported_parts\":" + imported.size()
                    + ",\"source_part_names\":\"" + esc(source.toString()) + "\""
                    + ",\"renamed_to\":\"" + esc(target) + "\""
                    + ",\"renamed_verified\":" + target.equals(renamed) + "}\n");
        }
        sim.saveState(new File(dir, "geometry_imported.sim").getAbsolutePath());
        System.out.println(COMPLETE);
    }

    private static String esc(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }
}
