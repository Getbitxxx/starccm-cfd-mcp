/* Isolated connectivity probe for two imported CGNS regions; no CFD solve. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Collections;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.report.ElementCountReport;
import star.common.Boundary;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

public class ProbeImportedMrfInterfaces extends StarMacro {
    private static String field(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }

    private static int number(String spec, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*(\\d+)").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return Integer.parseInt(m.group(1));
    }

    private static double faces(ElementCountReport report, Boundary boundary) {
        report.getParts().setObjects(boundary);
        if (!report.getCountKeys(boundary).contains("faces"))
            throw new IllegalStateException("Face count unavailable: " + boundary.getPresentationName());
        return report.getCount(boundary, "faces");
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            if (!Pattern.compile("\\\"scenario\\\"\\s*:\\s*\\\"geometry_mesh_probe_only\\\"").matcher(spec).find()) {
                throw new IllegalArgumentException("Only an isolated mesh probe is supported");
            }
            Region stationary = sim.getRegionManager().getRegion(field(spec, "stationary_region"));
            Region rotating = sim.getRegionManager().getRegion(field(spec, "rotating_region"));
            if (stationary == null || rotating == null || stationary == rotating ||
                    stationary.getBoundaryManager().getBoundaries().size() != number(spec, "expected_stationary_boundaries") ||
                    rotating.getBoundaryManager().getBoundaries().size() != number(spec, "expected_rotating_boundaries")) {
                throw new IllegalStateException("Imported MRF region and boundary counts differ from audited CGNS");
            }
            String[][] pairs = {
                {field(spec, "stationary_top_boundary"), field(spec, "rotating_top_boundary"), "PILOT_MRF_TOP"},
                {field(spec, "stationary_bottom_boundary"), field(spec, "rotating_bottom_boundary"), "PILOT_MRF_BOTTOM"},
                {field(spec, "stationary_side_boundary"), field(spec, "rotating_side_boundary"), "PILOT_MRF_SIDE"}
            };
            ElementCountReport report = sim.getReportManager().create("star.base.report.ElementCountReport");
            report.setRepresentation((FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh"));
            double[][] faceCounts = new double[3][2];
            for (int i = 0; i < pairs.length; i++) {
                Boundary a = stationary.getBoundaryManager().getBoundary(pairs[i][0]);
                Boundary b = rotating.getBoundaryManager().getBoundary(pairs[i][1]);
                if (a == null || b == null) throw new IllegalStateException("Missing interface face " + pairs[i][2]);
                faceCounts[i][0] = faces(report, a);
                faceCounts[i][1] = faces(report, b);
                if (faceCounts[i][0] <= 0 || faceCounts[i][1] <= 0)
                    throw new IllegalStateException("Selected interface has no mesh faces: " + pairs[i][2]);
            }
            sim.deleteObjects(Collections.singletonList(report));
            if (faceCounts[2][0] <= faceCounts[0][0] || faceCounts[2][0] <= faceCounts[1][0] ||
                    faceCounts[2][1] <= faceCounts[0][1] || faceCounts[2][1] <= faceCounts[1][1]) {
                throw new IllegalStateException("Side interface face count must exceed both cap interfaces");
            }
            int before = sim.getInterfaceManager().getObjects().size();
            for (String[] pair : pairs) {
                Boundary a = stationary.getBoundaryManager().getBoundary(pair[0]);
                Boundary b = rotating.getBoundaryManager().getBoundary(pair[1]);
                sim.getInterfaceManager().createBoundaryInterface(a, b, pair[2]);
            }
            int after = sim.getInterfaceManager().getObjects().size();
            if (after != before + 3) throw new IllegalStateException("Three interfaces were not created");
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_mrf_interface_probe.csv"), StandardCharsets.UTF_8)) {
                out.write("stator_boundary,rotor_boundary,interface,stator_faces,rotor_faces\n");
                for (int i = 0; i < pairs.length; i++)
                    out.write(pairs[i][0] + "," + pairs[i][1] + "," + pairs[i][2] + "," +
                            faceCounts[i][0] + "," + faceCounts[i][1] + "\n");
                out.write("interface_count_before," + before + ",\ninterface_count_after," + after + ",\n");
                out.write("GMSH_MRF_INTERFACE_PROBE_COMPLETE,1,\n");
            }
            sim.saveState(new File(dir, "gmsh_mrf_interfaces_probe.sim").getAbsolutePath());
            System.out.println("GMSH_MRF_INTERFACE_PROBE_COMPLETE");
        } catch (Exception failure) {
            StringWriter detail = new StringWriter();
            failure.printStackTrace(new PrintWriter(detail));
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_mrf_interface_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(detail.toString());
            } catch (Exception ignored) { }
            throw new RuntimeException("Imported MRF interface probe failed", failure);
        }
    }
}
