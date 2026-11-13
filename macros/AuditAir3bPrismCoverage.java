/* AIR-3B A2c: read-only prism-layer coverage audit of the saved MRF mesh.
 *
 * The plan requires that the propeller walls carry no prism-free area and that the
 * realised near-wall resolution is measured rather than asserted. This reads the
 * native PrismLayerNumber and FirstPrismLayerHeight fields on the Volume Mesh
 * representation and reports, per boundary:
 *   - the area fraction with PrismLayerNumber < 0.5 (prism-free area),
 *   - the area-averaged PrismLayerNumber,
 *   - the minimum, area-averaged and maximum FirstPrismLayerHeight in metres.
 *
 * It never initializes, iterates, meshes or saves. The y+ gate belongs to the solve
 * stage and is deliberately not attempted here.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;

public class AuditAir3bPrismCoverage extends StarMacro {

    private static final String COMPLETE = "AIR3B_PRISM_COVERAGE_COMPLETE";

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

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        List<ClientServerObject> temporary = new ArrayList<>();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            if (sim.getSimulationIterator().getCurrentIteration() != 0)
                throw new IllegalStateException("Prism audit requires an unsolved saved mesh");

            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0)
                throw new IllegalStateException("No volume mesh to audit");

            FieldFunction number = null, height = null;
            for (FieldFunction f : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
                if ("PrismLayerNumber".equals(f.getFunctionName())) number = f;
                if ("FirstPrismLayerHeight".equals(f.getFunctionName())) height = f;
            }
            if (number == null || height == null)
                throw new IllegalStateException("Native prism fields PrismLayerNumber / FirstPrismLayerHeight unavailable");

            double expectedLayers = env("AIR3B_PRISM_LAYERS", 5.0);
            Units metres = sim.getUnitsManager().getObject("m");
            double worstBladeFree = 0.0;
            double worstBladeMinLayer = Double.MAX_VALUE;
            int inspected = 0, propellerWalls = 0;
            int expectedWalls = (int) env("AIR3B_EXPECTED_WALL_BOUNDARIES", 44.0);

            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_prism_coverage.tsv")), StandardCharsets.UTF_8)) {
                w.write("CELLS\t" + mesh.getCellCount() + "\n");
                w.write("EXPECTED_PRISM_LAYERS\t" + expectedLayers + "\n");
                for (Region region : sim.getRegionManager().getRegions()) {
                    for (Boundary b : region.getBoundaryManager().getBoundaries()) {
                        if (b.getBoundaryType() instanceof InternalBoundary
                                || b.getPresentationName().contains("[")) continue;
                        String name = b.getPresentationName();
                        String role = name.startsWith("BLADE") ? "PROPELLER_WALL"
                                : name.startsWith("HUB") ? "PROPELLER_WALL"
                                : name.startsWith("INTERFACE") ? "INTERFACE_PATCH"
                                : name;
                        inspected++;

                        UserFieldFunction bare = sim.getFieldFunctionManager().createFieldFunction();
                        temporary.add(bare);
                        bare.setFunctionName("TEMP_PRISM_FREE_" + temporary.size());
                        bare.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
                        bare.setDefinition("(${PrismLayerNumber} < 0.5) ? 1 : 0");
                        AreaAverageReport bareReport = sim.getReportManager().createReport(AreaAverageReport.class);
                        temporary.add(bareReport);
                        bareReport.setRepresentation(mesh);
                        bareReport.setFieldFunction(bare);
                        bareReport.getParts().setObjects(b);
                        double freeFraction = bareReport.getReportMonitorValue();

                        AreaAverageReport avgLayers = sim.getReportManager().createReport(AreaAverageReport.class);
                        temporary.add(avgLayers);
                        avgLayers.setRepresentation(mesh);
                        avgLayers.setFieldFunction(number);
                        avgLayers.getParts().setObjects(b);
                        double meanLayers = avgLayers.getReportMonitorValue();

                        // Second, independent number: on a prism-free face PrismLayerNumber may be
                        // 0, NaN or undefined, and the indicator test would read NaN as "covered".
                        // The minimum layer number exposes that case directly.
                        MinReport minLayers = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
                        temporary.add(minLayers);
                        minLayers.setRepresentation(mesh);
                        minLayers.setFieldFunction(number);
                        minLayers.getParts().setObjects(b);
                        double minLayer = minLayers.getReportMonitorValue();

                        double[] heights = new double[3];
                        String[] stats = { "min", "area_average", "max" };
                        for (int i = 0; i < 3; i++) {
                            String cls = i == 0 ? "star.base.report.MinReport"
                                    : i == 1 ? "star.base.report.AreaAverageReport"
                                    : "star.base.report.MaxReport";
                            ScalarReport r = sim.getReportManager().create(cls);
                            temporary.add(r);
                            r.setRepresentation(mesh);
                            r.setFieldFunction(height);
                            r.getParts().setObjects(b);
                            r.setUnits(metres);
                            heights[i] = r.getReportMonitorValue();
                        }
                        if (!Double.isFinite(freeFraction) || freeFraction < 0 || freeFraction > 1)
                            throw new IllegalStateException("Invalid prism-free fraction on " + name);
                        if (!Double.isFinite(meanLayers) || meanLayers < 0)
                            throw new IllegalStateException("Invalid mean layer number on " + name);
                        // A prism-free boundary legitimately has no first layer, so a nonfinite
                        // height is recorded rather than aborting; the layer-number evidence is
                        // what decides coverage.
                        boolean heightUsable = true;
                        for (double v : heights) if (!Double.isFinite(v) || v < 0) heightUsable = false;

                        w.write("BOUNDARY\t" + region.getPresentationName() + "\t" + name + "\t" + role
                                + "\tprism_free_area_fraction\t" + freeFraction
                                + "\tmean_prism_layers\t" + meanLayers
                                + "\tmin_prism_layers\t" + (Double.isFinite(minLayer) ? minLayer : "NA")
                                + "\tfirst_layer_height_m\t"
                                + (heightUsable ? heights[0] + "\t" + heights[1] + "\t" + heights[2]
                                                : "NA\tNA\tNA")
                                + "\n");
                        if (role.equals("PROPELLER_WALL")) {
                            propellerWalls++;
                            worstBladeFree = Math.max(worstBladeFree, freeFraction);
                            if (Double.isFinite(minLayer)) worstBladeMinLayer = Math.min(worstBladeMinLayer, minLayer);
                        }
                    }
                }
                // The gate must not be satisfiable by having measured nothing.
                if (propellerWalls < 1)
                    throw new IllegalStateException("No propeller wall boundary was classified; the gate would be vacuous");
                if (propellerWalls != expectedWalls)
                    throw new IllegalStateException("Classified " + propellerWalls + " propeller wall boundaries, expected "
                            + expectedWalls + "; the wall predicate and the mesh stage may have diverged");
                if (worstBladeMinLayer == Double.MAX_VALUE)
                    throw new IllegalStateException("No finite minimum prism layer number was measured on the propeller walls");
                boolean covered = worstBladeFree <= 1.0e-9 && worstBladeMinLayer >= 0.5;
                w.write("SUMMARY\tinspected_boundaries\t" + inspected + "\n");
                w.write("SUMMARY\tpropeller_wall_boundaries\t" + propellerWalls + "\n");
                w.write("SUMMARY\texpected_propeller_wall_boundaries\t" + expectedWalls + "\n");
                w.write("SUMMARY\tworst_propeller_wall_prism_free_fraction\t" + worstBladeFree + "\n");
                w.write("SUMMARY\tworst_propeller_wall_min_layer_number\t" + worstBladeMinLayer + "\n");
                w.write("GATE\tpropeller_wall_prism_free_area_zero\t" + covered + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B prism coverage audit failed: " + e.getMessage(), e);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject x : temporary) {
                try { sim.deleteObjects(Collections.singletonList(x)); } catch (Throwable ignored) { }
            }
        }
    }
}
