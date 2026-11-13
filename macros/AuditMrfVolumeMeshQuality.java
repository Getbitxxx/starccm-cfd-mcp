/* Read-only volume mesh counts and essential cell-quality metrics for every Region. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import star.base.neo.ClientServerObject;
import star.base.report.ElementCountReport;
import star.base.report.MaxReport;
import star.base.report.MinReport;
import star.base.report.ScalarReport;
import star.base.report.VolumeAverageReport;
import star.common.CellQualityFunction;
import star.common.FaceValidityFunction;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;

public class AuditMrfVolumeMeshQuality extends StarMacro {
    private static final String COMPLETE = "MRF_VOLUME_MESH_QUALITY_AUDIT_COMPLETE";
    // The CaseSpec's own mesh quality thresholds (cases/air-three-blade/mrf-draft.json::quality).
    // Used ONLY to fill quality_gate_passed, which is deliberately a SEPARATE field from `status`:
    // status "ok" means the read-only audit operation completed and the numbers are real
    // measurements; whether they clear the engineering threshold is a different question and is
    // never folded into `status`.
    private static final double MIN_CELL_QUALITY = 1e-5;
    private static final double MIN_FACE_VALIDITY = 1.0;
    private static final double MIN_VOLUME_CHANGE = 0.01;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        List<ClientServerObject> temporary = new ArrayList<>();
        // Measured minima, collected while the CSV is written so the JSON can never disagree with it.
        Map<String, Double> minima = new LinkedHashMap<>();
        try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "mrf_volume_mesh_quality.csv"), StandardCharsets.UTF_8)) {
            FvRepresentation volumeMesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (volumeMesh == null || volumeMesh.getCellCount() <= 0) {
                throw new IllegalStateException("MRF volume mesh missing");
            }
            out.write("region,metric,stat,value\n");
            out.write("ALL,Cells,count," + volumeMesh.getCellCount() + "\n");
            if (sim.getRegionManager().getObjects().isEmpty()) {
                throw new IllegalStateException("Volume mesh audit requires at least one Region");
            }
            for (Region region : sim.getRegionManager().getObjects()) {
                String name = region.getPresentationName();
                if (!volumeMesh.describesRegion(region)) {
                    throw new IllegalStateException("MRF volume mesh omits Region " + name);
                }
                ElementCountReport count = sim.getReportManager().create("star.base.report.ElementCountReport");
                temporary.add(count);
                count.setRepresentation(volumeMesh);
                count.getParts().setObjects(region);
                for (String key : count.getCountKeys(region)) {
                    out.write(name + ",ElementCount:" + csv(key) + ",count," + count.getCount(region, key) + "\n");
                }
                writeMetric(sim, volumeMesh, region, temporary, out, minima, "CellQuality", CellQualityFunction.class);
                writeMetric(sim, volumeMesh, region, temporary, out, minima, "FaceValidity", FaceValidityFunction.class);
                writeMetric(sim, volumeMesh, region, temporary, out, minima, "VolumeChange", VolumeChangeFunction.class);
            }
            out.write("ALL,Audit,complete,1\n");
            writeCertificate(sim, volumeMesh.getCellCount(), minima);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("MRF volume mesh quality audit failed", failure);
        } finally {
            if (!temporary.isEmpty()) sim.deleteObjects(temporary);
        }
    }

    /**
     * The measurement-level record, carrying the values this run actually read back.
     *
     * `status: ok` means "the read-only audit operation completed and these are real measurements";
     * it is written only after every metric has been read and every CSV row has been written, so it
     * cannot claim a measurement that did not happen. `quality_gate_passed` and the threshold fields
     * are separate, so a mesh that is measured but fails the engineering gate still produces a
     * parseable record whose `status` correctly says the operation completed.
     */
    private static void writeCertificate(Simulation sim, long cells, Map<String, Double> minima) throws Exception {
        double cq = minOf(minima, "CellQuality");
        double fv = minOf(minima, "FaceValidity");
        double vc = minOf(minima, "VolumeChange");
        boolean gate = cq >= MIN_CELL_QUALITY && fv >= MIN_FACE_VALIDITY && vc >= MIN_VOLUME_CHANGE;
        StringBuilder j = new StringBuilder();
        j.append("{\"status\":\"ok\",\"audit\":\"mrf_volume_mesh_quality\",\"schema_version\":1,");
        j.append("\"cells\":").append(cells).append(',');
        j.append("\"measured_cell_quality_min\":").append(repr(cq)).append(',');
        j.append("\"measured_face_validity_min\":").append(repr(fv)).append(',');
        j.append("\"measured_volume_change_min\":").append(repr(vc)).append(',');
        j.append("\"per_region_minima\":{");
        boolean first = true;
        for (Map.Entry<String, Double> e : minima.entrySet()) {
            if (!first) j.append(',');
            j.append('"').append(esc(e.getKey())).append("\":").append(repr(e.getValue()));
            first = false;
        }
        j.append("},");
        j.append("\"thresholds\":{\"mesh_min_cell_quality\":").append(repr(MIN_CELL_QUALITY))
         .append(",\"mesh_min_face_validity\":").append(repr(MIN_FACE_VALIDITY))
         .append(",\"mesh_min_volume_change\":").append(repr(MIN_VOLUME_CHANGE)).append("},");
        j.append("\"quality_gate_passed\":").append(gate).append(',');
        j.append("\"quality_gate_note\":\"separate from status: status ok means the audit completed;")
         .append(" this field says whether the CaseSpec quality thresholds were met\",");
        j.append("\"regions\":" ).append(sim.getRegionManager().getObjects().size()).append(',');
        j.append("\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
        try (FileWriter cert = new FileWriter(new File(sim.getSessionDirFile(), "mrf_volume_mesh_quality.json"),
                StandardCharsets.UTF_8)) {
            cert.write(j.toString());
        }
    }

    private static double minOf(Map<String, Double> minima, String metric) {
        Double best = null;
        for (Map.Entry<String, Double> e : minima.entrySet()) {
            if (!e.getKey().endsWith("|" + metric)) continue;
            if (best == null || e.getValue() < best) best = e.getValue();
        }
        if (best == null) throw new IllegalStateException("No minimum recorded for " + metric);
        return best;
    }

    private static String repr(double v) {
        if (!Double.isFinite(v)) throw new IllegalStateException("Nonfinite value in mesh certificate");
        return String.format(java.util.Locale.ROOT, "%.12g", v);
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\t", " ");
    }

    private static void writeMetric(Simulation sim, FvRepresentation representation, Region region,
            List<ClientServerObject> temporary, FileWriter out, Map<String, Double> minima,
            String label, Class<?> fieldClass) throws Exception {
        FieldFunction selected = null;
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(representation)) {
            if (fieldClass.isInstance(candidate)) {
                selected = candidate;
                break;
            }
        }
        if (selected == null) throw new IllegalStateException("Required mesh metric unavailable: " + label);
        for (String stat : new String[] {"min", "max", "volume_average"}) {
            String reportClass = "min".equals(stat) ? "star.base.report.MinReport" :
                    "max".equals(stat) ? "star.base.report.MaxReport" : "star.base.report.VolumeAverageReport";
            ScalarReport report = sim.getReportManager().create(reportClass);
            temporary.add(report);
            report.setRepresentation(representation);
            report.setFieldFunction(selected);
            report.getParts().setObjects(region);
            double value = report.getReportMonitorValue();
            if (!Double.isFinite(value)) throw new IllegalStateException("Nonfinite mesh quality metric " + label + "/" + stat);
            out.write(region.getPresentationName() + "," + label + "," + stat + "," + value + "\n");
            // Only the minimum is certified; it is the statistic the CaseSpec threshold applies to.
            if ("min".equals(stat)) minima.put(region.getPresentationName() + "|" + label, value);
        }
    }

    private static String csv(String value) { return value.replace(',', '_').replace('\n', ' '); }
}
