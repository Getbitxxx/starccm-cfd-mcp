/* AIR-3B A3c: read-only y+ audit of a solved MRF simulation.
 *
 * The plan requires at least 95 % of the propeller wall area at y+ <= 5, with the
 * remaining area and the maximum recorded. This reads the native wall y+ field on the
 * volume-mesh representation and reports, per propeller wall boundary and in aggregate:
 *   - minimum, area-averaged and maximum y+,
 *   - the area fraction with y+ <= the configured threshold (a single area average of a
 *     0/1 indicator over the union of the propeller walls is already area-weighted),
 *   - the uncovered area fraction above the threshold.
 *
 * It never initializes, iterates, meshes or saves.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;

public class AuditAir3bYplus extends StarMacro {

    private static final String COMPLETE = "AIR3B_YPLUS_AUDIT_COMPLETE";

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation r : sim.getRepresentationManager().getObjects())
            if (r instanceof FvRepresentation) return (FvRepresentation) r;
        return null;
    }

    private static FieldFunction findYPlus(Simulation sim, FvRepresentation mesh) {
        String[] aliases = { "WallYplus", "WallYPlus", "Wall Y+", "Wall y+", "Yplus", "YPlus" };
        for (String alias : aliases) {
            if (!sim.getFieldFunctionManager().hasFunction(alias)) continue;
            FieldFunction f = sim.getFieldFunctionManager().getFunction(alias);
            if (f != null) return f;
        }
        // The fallback must require "wall" in the name: a substring test alone can select
        // "Specified Y+ Heat Transfer Coefficient", which reads Double.MAX_VALUE on purely
        // aerodynamic walls. This project has already made that mistake once.
        for (FieldFunction f : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            String n = (f.getFunctionName() + " " + f.getPresentationName()).toLowerCase(Locale.ROOT);
            if ((n.contains("y+") || n.contains("yplus")) && n.contains("wall")) return f;
        }
        return null;
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        List<ClientServerObject> temporary = new ArrayList<>();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            if (iteration <= 0) throw new IllegalStateException("y+ audit requires a solved simulation");

            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No volume mesh");
            FieldFunction yplus = findYPlus(sim, mesh);
            if (yplus == null) throw new IllegalStateException("No wall y+ field function is available");
            double threshold = env("AIR3B_YPLUS_MAX", 5.0);
            Units metres = sim.getUnitsManager().getObject("m");

            Region rotating = sim.getRegionManager().getRegion("AIR3B_ROTATING");
            if (rotating == null) throw new IllegalStateException("AIR3B_ROTATING is missing");
            List<Boundary> propellerWalls = new ArrayList<>();
            for (Boundary b : rotating.getBoundaryManager().getBoundaries()) {
                String n = b.getPresentationName();
                if ((n.startsWith("BLADE") || n.startsWith("HUB")) && b.getBoundaryType() instanceof WallBoundary)
                    propellerWalls.add(b);
            }
            int expectedWalls = (int) env("AIR3B_EXPECTED_WALL_BOUNDARIES", 44.0);
            if (propellerWalls.size() != expectedWalls)
                throw new IllegalStateException("Found " + propellerWalls.size()
                        + " propeller wall boundaries, expected " + expectedWalls);

            UserFieldFunction covered = sim.getFieldFunctionManager().createFieldFunction();
            temporary.add(covered);
            covered.setFunctionName("TEMP_YPLUS_COVERED");
            covered.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
            covered.setDefinition("(${" + yplus.getFunctionName() + "} <= " + threshold + ") ? 1 : 0");

            AreaAverageReport aggregateCovered = sim.getReportManager().createReport(AreaAverageReport.class);
            temporary.add(aggregateCovered);
            aggregateCovered.setRepresentation(mesh);
            aggregateCovered.setFieldFunction(covered);
            aggregateCovered.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
            double coveredFraction = aggregateCovered.getReportMonitorValue();

            AreaAverageReport aggregateMean = sim.getReportManager().createReport(AreaAverageReport.class);
            temporary.add(aggregateMean);
            aggregateMean.setRepresentation(mesh);
            aggregateMean.setFieldFunction(yplus);
            aggregateMean.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
            double meanYplus = aggregateMean.getReportMonitorValue();

            MaxReport aggregateMax = (MaxReport) sim.getReportManager().create("star.base.report.MaxReport");
            temporary.add(aggregateMax);
            aggregateMax.setRepresentation(mesh);
            aggregateMax.setFieldFunction(yplus);
            aggregateMax.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
            double maxYplus = aggregateMax.getReportMonitorValue();
            double minYplus = Double.NaN;
            try {
                MinReport aggregateMin = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
                temporary.add(aggregateMin);
                aggregateMin.setRepresentation(mesh);
                aggregateMin.setFieldFunction(yplus);
                aggregateMin.getParts().setObjects(propellerWalls.toArray(new NamedObject[0]));
                minYplus = aggregateMin.getReportMonitorValue();
            } catch (Throwable ignored) { }

            if (!Double.isFinite(coveredFraction) || coveredFraction < 0 || coveredFraction > 1)
                throw new IllegalStateException("Invalid covered area fraction: " + coveredFraction);

            double required = env("AIR3B_YPLUS_REQUIRED_FRACTION", 0.95);
            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_yplus.tsv")), StandardCharsets.UTF_8)) {
                w.write("ITERATION\t" + iteration + "\n");
                w.write("YPLUS_FIELD\t" + yplus.getFunctionName() + "\n");
                w.write("THRESHOLD\t" + threshold + "\n");
                w.write("PROPELLER_WALL_BOUNDARIES\t" + propellerWalls.size() + "\n");
                for (Boundary b : propellerWalls) {
                    double[] tri = new double[3];
                    String[] classes = { "star.base.report.MinReport", "star.base.report.AreaAverageReport",
                                         "star.base.report.MaxReport" };
                    for (int i = 0; i < 3; i++) {
                        ScalarReport r = sim.getReportManager().create(classes[i]);
                        temporary.add(r);
                        r.setRepresentation(mesh);
                        r.setFieldFunction(yplus);
                        r.getParts().setObjects(b);
                        tri[i] = r.getReportMonitorValue();
                    }
                    AreaAverageReport one = sim.getReportManager().createReport(AreaAverageReport.class);
                    temporary.add(one);
                    one.setRepresentation(mesh);
                    one.setFieldFunction(covered);
                    one.getParts().setObjects(b);
                    double f = one.getReportMonitorValue();
                    w.write("WALL\t" + b.getPresentationName()
                            + "\typlus_min\t" + tri[0] + "\typlus_area_average\t" + tri[1]
                            + "\typlus_max\t" + tri[2] + "\tcovered_fraction\t" + f + "\n");
                }
                w.write("AGGREGATE\typlus_min\t" + minYplus + "\n");
                w.write("AGGREGATE\typlus_area_average\t" + meanYplus + "\n");
                w.write("AGGREGATE\typlus_max\t" + maxYplus + "\n");
                w.write("AGGREGATE\tcovered_area_fraction\t" + coveredFraction + "\n");
                w.write("AGGREGATE\tuncovered_area_fraction\t" + (1.0 - coveredFraction) + "\n");
                w.write("AGGREGATE\trequired_fraction\t" + required + "\n");
                w.write("GATE\typlus_coverage\t" + (coveredFraction >= required) + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B y+ audit failed: " + e.getMessage(), e);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject x : temporary) {
                try { sim.deleteObjects(Collections.singletonList(x)); } catch (Throwable ignored) { }
            }
        }
    }
}
