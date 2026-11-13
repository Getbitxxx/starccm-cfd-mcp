import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import star.base.neo.ClientServerObject;
import star.base.report.MaxReport;
import star.base.report.MinReport;
import star.common.Boundary;
import star.common.FieldFunction;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

/** Read-only check of the legacy inlet and outlet axial positions. */
public class AuditAxialCaps extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File output = new File(sim.getSessionDirFile(), "axial_caps_audit.json");
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            Region fluid = sim.getRegionManager().getRegion("FLUID");
            if (fluid == null) throw new IllegalStateException("FLUID region missing");
            FieldFunction z = sim.getFieldFunctionManager().getFunction("Position").getComponentFunction(2);
            double[] inlet = bounds(sim, fluid.getBoundaryManager().getBoundary("INLET"), z, temporary);
            double[] outlet = bounds(sim, fluid.getBoundaryManager().getBoundary("OUTLET"), z, temporary);
            if (Math.abs(inlet[1] - inlet[0]) > 1e-5 || Math.abs(outlet[1] - outlet[0]) > 1e-5) {
                throw new IllegalStateException("An inlet or outlet is not an axial plane");
            }
            if (Math.abs(inlet[0] - outlet[0]) < 1e-5) {
                throw new IllegalStateException("Inlet and outlet axial planes coincide");
            }
            String downstream = inlet[0] < outlet[0] ? "+Z" : "-Z";
            try (FileWriter writer = new FileWriter(output, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"inlet_z_raw\":[" + inlet[0] + "," + inlet[1] +
                        "],\"outlet_z_raw\":[" + outlet[0] + "," + outlet[1] +
                        "],\"legacy_downstream_axis\":\"" + downstream + "\"}\n");
            }
            System.out.println("AXIAL_CAPS_AUDIT_COMPLETE");
        } catch (Exception failure) {
            throw new RuntimeException("Axial-cap audit failed", failure);
        } finally {
            sim.deleteObjects(temporary);
        }
    }

    private static double[] bounds(Simulation sim, Boundary boundary, FieldFunction z,
            List<ClientServerObject> temporary) {
        if (boundary == null) throw new IllegalStateException("Expected axial boundary missing");
        MinReport minimum = (MinReport) sim.getReportManager().create("star.base.report.MinReport");
        MaxReport maximum = (MaxReport) sim.getReportManager().create("star.base.report.MaxReport");
        temporary.add(minimum);
        temporary.add(maximum);
        minimum.setFieldFunction(z);
        maximum.setFieldFunction(z);
        minimum.getParts().setObjects(boundary);
        maximum.getParts().setObjects(boundary);
        double lo = minimum.getReportMonitorValue();
        double hi = maximum.getReportMonitorValue();
        if (!Double.isFinite(lo) || !Double.isFinite(hi)) throw new IllegalStateException("Non-finite axial position");
        return new double[] {lo, hi};
    }
}
