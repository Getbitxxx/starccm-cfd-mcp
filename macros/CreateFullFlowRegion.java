/* Create a new full-360 fluid region from the audited cylindrical Boolean part.
 * Existing sector regions are retained for comparison; no boundary types are assigned.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.Collection;
import java.util.HashSet;
import java.util.Set;

import star.base.neo.NeoObjectVector;
import star.common.Boundary;
import star.common.Region;
import star.common.RegionManager;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.MeshOperationPart;

public class CreateFullFlowRegion extends StarMacro {
    private static final String COMPLETE = "FULL_FLOW_REGION_CREATE_COMPLETE";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            RegionManager regions = sim.getRegionManager();
            Set<String> before = new HashSet<String>();
            for (Region region : regions.getObjects()) before.add(region.getPresentationName());
            for (String name : before) {
                if (name.startsWith("AUTO_FULL360_FLOW")) throw new IllegalStateException("Generated region already exists");
            }
            Object geometry = sim.get(SimulationPartManager.class).getPart("AUTO_FREE_AIR_DOMAIN");
            if (!(geometry instanceof MeshOperationPart)) throw new IllegalStateException("Audited cylindrical Boolean part missing");
            regions.newRegionsFromParts(new NeoObjectVector(new Object[] {geometry}),
                    "OneRegionPerPart", null, "OneBoundaryPerPartSurface", null,
                    RegionManager.CreateInterfaceMode.BOUNDARY);
            Region created = null;
            Collection<Region> after = regions.getObjects();
            for (Region region : after) {
                if (!before.contains(region.getPresentationName())) {
                    if (created != null) throw new IllegalStateException("More than one region created");
                    created = region;
                }
            }
            if (created == null) throw new IllegalStateException("No region created");
            created.setPresentationName("AUTO_FULL360_FLOW");
            int boundaryCount = created.getBoundaryManager().getObjects().size();
            if (boundaryCount < 9) throw new IllegalStateException("Expected nine distinct boundaries, got " + boundaryCount);
            sim.saveState(new File(dir, "full_flow_region_built.sim").getAbsolutePath());
            try (FileWriter writer = new FileWriter(new File(dir, "full_flow_region_build.json"), StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\"AUTO_FULL360_FLOW\",\"boundary_count\":" +
                        boundaryCount + ",\"boundaries\":[");
                int index = 0;
                for (Boundary boundary : created.getBoundaryManager().getObjects()) {
                    if (index++ > 0) writer.write(",");
                    writer.write("\"" + escape(boundary.getPresentationName()) + "\"");
                }
                writer.write("],\"old_region_count\":" + before.size() +
                        ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("Full-flow region construction failed", failure);
        }
    }

    private static String escape(String text) {
        return text.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
