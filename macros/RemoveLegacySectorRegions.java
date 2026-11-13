/* Retire the original 120-degree sector fluid regions after full-domain audit. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

import star.common.Interface;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

public class RemoveLegacySectorRegions extends StarMacro {
    private static final String COMPLETE = "LEGACY_SECTOR_REGIONS_REMOVED";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            Region full = sim.getRegionManager().getRegion("AUTO_FULL360_FLOW");
            Region fluid = sim.getRegionManager().getRegion("FLUID");
            Region propeller = sim.getRegionManager().getRegion("PROPELLER");
            if (full == null || fluid == null || propeller == null ||
                    sim.getRegionManager().getObjects().size() != 3 ||
                    full.getBoundaryManager().getBoundaries().size() != 9) {
                throw new IllegalStateException("Unexpected region layout; refusing sector cleanup");
            }
            Set<String> expected = new HashSet<>(Arrays.asList(
                    "FLUID/FLUID", "FLUID/PROPELLER", "PROPELLER/PROPELLER"));
            if (sim.getInterfaceManager().getObjects().size() != expected.size()) {
                throw new IllegalStateException("Unexpected interface count");
            }
            for (Interface item : sim.getInterfaceManager().getObjects()) {
                if (!expected.remove(item.getPresentationName())) {
                    throw new IllegalStateException("Unexpected interface " + item.getPresentationName());
                }
            }
            if (!expected.isEmpty()) throw new IllegalStateException("Expected sector interfaces missing");
            sim.getInterfaceManager().removeObjects(sim.getInterfaceManager().getObjects());
            sim.getRegionManager().removeObjects(Arrays.asList(fluid, propeller));
            if (sim.getRegionManager().getObjects().size() != 1 ||
                    !sim.getInterfaceManager().getObjects().isEmpty()) {
                throw new IllegalStateException("Sector cleanup did not leave one region and zero interfaces");
            }
            sim.saveState(new File(dir, "full_flow_sector_cleaned.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "full_flow_sector_cleanup.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"ok\",\"remaining_region\":\"AUTO_FULL360_FLOW\","
                        + "\"removed_regions\":[\"FLUID\",\"PROPELLER\"],\"removed_interfaces\":3,"
                        + "\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            throw new RuntimeException("Legacy-sector cleanup failed", failure);
        }
    }
}
