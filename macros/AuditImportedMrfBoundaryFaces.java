/* Read-only face-count audit of imported MRF outer and interface boundaries. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.report.ElementCountReport;
import star.common.Boundary;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

public class AuditImportedMrfBoundaryFaces extends StarMacro {
    private static String field(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        ElementCountReport report = null;
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            Region rotor = sim.getRegionManager().getRegion(field(spec, "rotor_region"));
            Region stator = sim.getRegionManager().getRegion(field(spec, "stator_region"));
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (rotor == null || stator == null || mesh == null ||
                    sim.getRegionManager().getObjects().size() != 2 ||
                    sim.getInterfaceManager().getObjects().size() != 3) {
                throw new IllegalStateException("Expected the audited two-region, three-interface mesh");
            }
            boolean initialize = Pattern.compile("\\\"initialize_before_audit\\\"\\s*:\\s*true")
                    .matcher(spec).find();
            if (initialize) sim.getSolution().initializeSolution();
            Set<String> statorNames = new HashSet<>(Arrays.asList(
                    field(spec, "inlet_boundary"), field(spec, "outlet_boundary"),
                    field(spec, "farfield_boundary"), field(spec, "stator_top_boundary"),
                    field(spec, "stator_bottom_boundary"), field(spec, "stator_side_boundary"),
                    field(spec, "stator_original_top"), field(spec, "stator_original_bottom"),
                    field(spec, "stator_original_side")));
            Set<String> rotorNames = new HashSet<>(Arrays.asList(
                    field(spec, "rotor_top_boundary"), field(spec, "rotor_bottom_boundary"),
                    field(spec, "rotor_side_boundary"), field(spec, "rotor_original_top"),
                    field(spec, "rotor_original_bottom"), field(spec, "rotor_original_side")));
            report = sim.getReportManager().create("star.base.report.ElementCountReport");
            report.setRepresentation(mesh);
            try (FileWriter out = new FileWriter(new File(dir, "imported_mrf_boundary_faces.csv"), StandardCharsets.UTF_8)) {
                out.write("region,boundary,class,key,count\n");
                out.write("initialization_before_audit," + initialize + ",,,\n");
                for (Region region : Arrays.asList(stator, rotor)) {
                    Set<String> wanted = region == stator ? statorNames : rotorNames;
                    for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                        String name = boundary.getPresentationName();
                        if (!wanted.remove(name)) continue;
                        report.getParts().setObjects(boundary);
                        for (String key : report.getCountKeys(boundary)) {
                            out.write(region.getPresentationName() + "," + name + "," +
                                    boundary.getClass().getSimpleName() + "," + key + "," +
                                    report.getCount(boundary, key) + "\n");
                        }
                    }
                    if (!wanted.isEmpty()) throw new IllegalStateException("Expected boundaries missing: " + wanted);
                }
                out.write("IMPORTED_MRF_BOUNDARY_FACES_AUDIT_COMPLETE,,,,\n");
            }
            System.out.println("IMPORTED_MRF_BOUNDARY_FACES_AUDIT_COMPLETE");
        } catch (Throwable failure) {
            throw new RuntimeException("Imported MRF boundary face audit failed", failure);
        } finally {
            if (report != null) sim.deleteObjects(Arrays.asList(report));
        }
    }
}
