/* Read-only histogram probe (P2.3 G5 MESH-B6 behaviour gap): measure the realised PrismLayerNumber and
 * FirstPrismLayerHeight distributions on the rotating-region material walls, to distinguish "ALM dropped layers
 * by design" from "the native field means something different on ALM cells". No mesh/solve/save; writes
 * mrf_alm_prism_histogram.json with flat keys (literal runner gate lookup).
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import star.base.neo.ClientServerObject;
import star.base.report.AreaAverageReport;
import star.base.report.MaxReport;
import star.base.report.MinReport;
import star.base.report.ScalarReport;
import star.common.*;

public class ProbeMrfAlmPrismHistogram extends StarMacro {

    private static final String COMPLETE = "MRF_ALM_PRISM_HISTOGRAM_COMPLETE";

    public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder json = new StringBuilder("{\"schema_version\":1,\"status\":\"ok\",\"probe\":\"alm_prism_histogram\"");
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            FvRepresentation mesh = null;
            for (Representation representation : sim.getRepresentationManager().getObjects()) {
                if (representation instanceof FvRepresentation) mesh = (FvRepresentation) representation;
            }
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No volume mesh");
            Region rotor = null;
            for (Region region : sim.getRegionManager().getRegions()) {
                if (region.getPresentationName().contains("ROTATING")) rotor = region;
            }
            if (rotor == null) throw new IllegalStateException("Rotating region missing");
            Region statorRegion = null;
            for (Region region : sim.getRegionManager().getRegions()) {
                if (region.getPresentationName().contains("STATIONARY")) statorRegion = region;
            }
            if (statorRegion == null) throw new IllegalStateException("Stationary region missing");
            Set<Boundary> rotorBoundaries = new HashSet<Boundary>(rotor.getBoundaryManager().getBoundaries());
            Set<Boundary> statorBoundaries = new HashSet<Boundary>(statorRegion.getBoundaryManager().getBoundaries());
            // review-6a B1: getBoundary0/1 return DERIVED InterfaceBoundary objects; the physical region
            // parents are getParentBoundary0/1 (same filter as the accepted prism-coverage audit).
            Set<Boundary> parents = new HashSet<Boundary>();
            int interfaces = 0;
            for (Interface inter : sim.getInterfaceManager().getObjects()) {
                if (!(inter instanceof BoundaryInterface)) throw new IllegalStateException("Unsupported interface type");
                BoundaryInterface pair = (BoundaryInterface) inter;
                Boundary a = pair.getParentBoundary0();
                Boundary b = pair.getParentBoundary1();
                if (a == null || b == null || a.equals(b)) throw new IllegalStateException("Interface has bad parents");
                boolean crosses = (rotorBoundaries.contains(a) && statorBoundaries.contains(b))
                        || (rotorBoundaries.contains(b) && statorBoundaries.contains(a));
                if (!crosses) throw new IllegalStateException("Interface does not connect the MRF regions");
                parents.add(a);
                parents.add(b);
                interfaces++;
            }
            if (interfaces < 1) throw new IllegalStateException("No in-place interface between the regions");
            FieldFunction numberField = null, heightField = null;
            for (FieldFunction f : sim.getFieldFunctionManager().getObjects()) {
                if ("PrismLayerNumber".equals(f.getFunctionName())) numberField = f;
                if ("FirstPrismLayerHeight".equals(f.getFunctionName())) heightField = f;
            }
            if (numberField == null || heightField == null)
                throw new IllegalStateException("Native prism fields unavailable");
            int walls = 0;
            for (Boundary boundary : rotor.getBoundaryManager().getBoundaries()) {
                if (parents.contains(boundary)) continue;
                if (boundary.getBoundaryType() instanceof InternalBoundary) continue;
                walls++;
                double[] bins = new double[8];
                bins[0] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} < 0.5) ? 1 : 0"), boundary);
                bins[1] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 0.5 && ${PrismLayerNumber} < 5) ? 1 : 0"), boundary);
                bins[2] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 5 && ${PrismLayerNumber} < 10) ? 1 : 0"), boundary);
                bins[3] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 10 && ${PrismLayerNumber} < 15) ? 1 : 0"), boundary);
                bins[4] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 15 && ${PrismLayerNumber} < 18) ? 1 : 0"), boundary);
                bins[5] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 18 && ${PrismLayerNumber} < 20) ? 1 : 0"), boundary);
                bins[6] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 20 && ${PrismLayerNumber} < 21) ? 1 : 0"), boundary);
                bins[7] = areaAvg(sim, temporary, mesh, indicator(sim, temporary, "(${PrismLayerNumber} >= 21) ? 1 : 0"), boundary);
                double binsSum = 0.0;
                for (double bin : bins) binsSum += bin;
                String prefix = "wall" + walls + "_";
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("bins_sum\":").append(binsSum);
                for (int i = 0; i < bins.length; i++) {
                    if (json.length() > 0) json.append(',');
                    json.append('"').append(prefix).append("bin_").append(i).append("_area_fraction\":").append(bins[i]);
                }
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("name\":\"").append(boundary.getPresentationName()).append('"');
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("area_average_layers\":")
                    .append(areaAvg(sim, temporary, mesh, numberField, boundary));
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("min_layers\":")
                    .append(extremum(sim, temporary, mesh, numberField, boundary, true));
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("first_height_min_m\":")
                    .append(extremum(sim, temporary, mesh, heightField, boundary, true));
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("first_height_area_average_m\":")
                    .append(areaAvg(sim, temporary, mesh, heightField, boundary));
                if (json.length() > 0) json.append(',');
                json.append('"').append(prefix).append("first_height_max_m\":")
                    .append(extremum(sim, temporary, mesh, heightField, boundary, false));
            }
            json.append(",\"wall_count\":").append(walls);
        } catch (Throwable failure) {
            json.setLength(0);
            json.append("{\"schema_version\":1,\"status\":\"error\",\"error\":\"")
                .append(String.valueOf(failure).replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject object : temporary) {
                try { sim.deleteObjects(Collections.singletonList(object)); } catch (Throwable ignored) { }
            }
        }
        json.append('}');
        try (FileWriter out = new FileWriter(new File("mrf_alm_prism_histogram.json"), StandardCharsets.UTF_8)) {
            out.write(json.toString());
        } catch (Throwable ignored) { }
        System.out.println(COMPLETE);
    }

    private static UserFieldFunction indicator(Simulation sim, List<ClientServerObject> temporary, String definition) {
        UserFieldFunction bare = sim.getFieldFunctionManager().createFieldFunction();
        temporary.add(bare);
        bare.setFunctionName("TEMP_ALM_HIST_" + temporary.size());
        bare.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
        bare.setDefinition(definition);
        return bare;
    }

    private static double areaAvg(Simulation sim, List<ClientServerObject> temporary, FvRepresentation mesh,
                                  FieldFunction field, Boundary boundary) throws Exception {
        AreaAverageReport report = sim.getReportManager().createReport(AreaAverageReport.class);
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(boundary);
        return report.getReportMonitorValue();
    }

    private static double extremum(Simulation sim, List<ClientServerObject> temporary, FvRepresentation mesh,
                                   FieldFunction field, Boundary boundary, boolean minimum) throws Exception {
        ScalarReport report = sim.getReportManager().create(minimum ? "star.base.report.MinReport"
                : "star.base.report.MaxReport");
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(boundary);
        return report.getReportMonitorValue();
    }
}
