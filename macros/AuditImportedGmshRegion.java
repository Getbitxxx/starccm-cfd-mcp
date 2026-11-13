/* Read-only volume and mesh-quality audit of one imported CGNS Region. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.ClientServerObject;
import star.base.report.ElementCountReport;
import star.base.report.ScalarReport;
import star.common.CellQualityFunction;
import star.common.FaceValidityFunction;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;

public class AuditImportedGmshRegion extends StarMacro {
    private static String textField(String spec, String name) {
        Matcher match = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!match.find()) throw new IllegalArgumentException("Missing " + name);
        return match.group(1);
    }

    private static double numberField(String spec, String name) {
        Matcher match = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(spec);
        if (!match.find()) throw new IllegalArgumentException("Missing " + name);
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value) || value <= 0) throw new IllegalArgumentException("Invalid " + name);
        return value;
    }

    private static double scalar(Simulation sim, FvRepresentation mesh, Region region,
            FieldFunction field, String reportClass, List<ClientServerObject> temporary) {
        ScalarReport report = sim.getReportManager().create(reportClass);
        temporary.add(report);
        report.setRepresentation(mesh);
        if (field != null) report.setFieldFunction(field);
        report.getParts().setObjects(region);
        return report.getReportMonitorValue();
    }

    private static FieldFunction function(Simulation sim, FvRepresentation mesh, Class<?> type) {
        for (FieldFunction field : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if (type.isInstance(field)) return field;
        }
        throw new IllegalStateException("Missing mesh field function " + type.getSimpleName());
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        List<ClientServerObject> temporary = new ArrayList<>();
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            String name = textField(spec, "mesh_region_name");
            double expectedVolume = numberField(spec, "expected_fluid_volume_m3");
            long expectedCells = (long) numberField(spec, "expected_tetra_count");
            Region region = sim.getRegionManager().getRegion(name);
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (region == null || mesh == null || !mesh.describesRegion(region)) {
                throw new IllegalStateException("Named imported volume mesh Region is unavailable");
            }
            ElementCountReport elementCount = sim.getReportManager().create("star.base.report.ElementCountReport");
            temporary.add(elementCount);
            elementCount.setRepresentation(mesh);
            elementCount.getParts().setObjects(region);
            long cells = 0;
            for (String key : elementCount.getCountKeys(region)) cells += elementCount.getCount(region, key);
            FieldFunction quality = function(sim, mesh, CellQualityFunction.class);
            double qualityAverage = scalar(sim, mesh, region, quality,
                    "star.base.report.VolumeAverageReport", temporary);
            double qualityIntegral = scalar(sim, mesh, region, quality,
                    "star.base.report.VolumeIntegralReport", temporary);
            if (!Double.isFinite(qualityAverage) || qualityAverage <= 0) {
                throw new IllegalStateException("Nonpositive CellQuality volume average");
            }
            double volume = qualityIntegral / qualityAverage;
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_import_quality.csv"), StandardCharsets.UTF_8)) {
                out.write("metric,value\n");
                out.write("region," + name + "\n");
                out.write("cells," + cells + "\nexpected_cells," + expectedCells + "\n");
                out.write("volume_m3," + volume + "\nexpected_volume_m3," + expectedVolume + "\n");
                for (Class<?> fieldClass : new Class<?>[] {CellQualityFunction.class,
                        FaceValidityFunction.class, VolumeChangeFunction.class}) {
                    FieldFunction field = function(sim, mesh, fieldClass);
                    double minimum = scalar(sim, mesh, region, field, "star.base.report.MinReport", temporary);
                    out.write(fieldClass.getSimpleName() + "_min," + minimum + "\n");
                }
                if (cells != expectedCells || Math.abs(volume / expectedVolume - 1) > 0.01) {
                    throw new IllegalStateException("CGNS cell count or physical volume mismatch");
                }
                out.write("GMSH_IMPORT_QUALITY_AUDIT_COMPLETE,1\n");
            }
            System.out.println("GMSH_IMPORT_QUALITY_AUDIT_COMPLETE");
        } catch (Throwable failure) {
            StringWriter detail = new StringWriter();
            failure.printStackTrace(new PrintWriter(detail));
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_import_quality_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(detail.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Gmsh CGNS quality audit failed", failure);
        } finally {
            if (!temporary.isEmpty()) sim.deleteObjects(temporary);
        }
    }
}
