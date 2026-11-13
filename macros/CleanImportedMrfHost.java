/* Isolated cleanup of two audited legacy regions from a paired imported mesh. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.FvRepresentation;
import star.common.Interface;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

public class CleanImportedMrfHost extends StarMacro {
    private static String field(String spec, String name) {
        Matcher m = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + name);
        return m.group(1);
    }

    private static long count(String spec, String name) {
        Matcher m = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*([0-9]+)").matcher(spec);
        if (!m.find()) throw new IllegalArgumentException("Missing " + name);
        return Long.parseLong(m.group(1));
    }

    private static String sha256(File file) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));
        StringBuilder value = new StringBuilder();
        for (byte item : digest) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            if (!"geometry_mesh_probe_only".equals(field(spec, "scenario")) ||
                    !sha256(new File(dir, "input.sim")).equals(field(spec, "expected_input_sha256"))) {
                throw new IllegalStateException("Probe-only or input hash check failed");
            }
            Region rotor = sim.getRegionManager().getRegion(field(spec, "rotor_region"));
            Region stator = sim.getRegionManager().getRegion(field(spec, "stator_region"));
            Region oldA = sim.getRegionManager().getRegion(field(spec, "legacy_region_a"));
            Region oldB = sim.getRegionManager().getRegion(field(spec, "legacy_region_b"));
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (rotor == null || stator == null || oldA == null || oldB == null ||
                    sim.getRegionManager().getObjects().size() != 4 || mesh == null ||
                    !mesh.describesRegion(rotor) || !mesh.describesRegion(stator) ||
                    mesh.getCellCount() != count(spec, "expected_cells_before")) {
                throw new IllegalStateException("Audited four-region input layout changed");
            }
            Set<String> legacyNames = new HashSet<>(Arrays.asList(
                    field(spec, "legacy_interface_a"), field(spec, "legacy_interface_b"),
                    field(spec, "legacy_interface_c")));
            Set<String> retainedNames = new HashSet<>(Arrays.asList(
                    field(spec, "retained_interface_a"), field(spec, "retained_interface_b"),
                    field(spec, "retained_interface_c")));
            if (legacyNames.size() != 3 || retainedNames.size() != 3 ||
                    sim.getInterfaceManager().getObjects().size() != 6) {
                throw new IllegalStateException("Audited six-interface layout changed");
            }
            List<Interface> toRemove = new ArrayList<>();
            for (Interface item : sim.getInterfaceManager().getObjects()) {
                String name = item.getPresentationName();
                if (legacyNames.remove(name)) toRemove.add(item);
                else if (!retainedNames.remove(name)) {
                    throw new IllegalStateException("Unexpected interface " + name);
                }
            }
            if (!legacyNames.isEmpty() || !retainedNames.isEmpty() || toRemove.size() != 3) {
                throw new IllegalStateException("Interface names differ from trusted audit");
            }
            sim.getInterfaceManager().removeObjects(toRemove);
            sim.getRegionManager().removeObjects(Arrays.asList(oldA, oldB));
            if (sim.getRegionManager().getObjects().size() != 2 ||
                    sim.getInterfaceManager().getObjects().size() != 3 ||
                    mesh.getCellCount() != count(spec, "expected_cells_after")) {
                throw new IllegalStateException("Legacy removal did not leave expected two-region mesh");
            }
            File output = new File(dir, "clean_imported_mrf_probe.sim");
            sim.saveState(output.getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "clean_imported_mrf_host.txt"), StandardCharsets.UTF_8)) {
                out.write("rotor_region=" + rotor.getPresentationName() + "\n");
                out.write("stator_region=" + stator.getPresentationName() + "\n");
                out.write("remaining_regions=2\nremaining_interfaces=3\n");
                out.write("remaining_cells=" + mesh.getCellCount() + "\n");
                out.write("CLEAN_IMPORTED_MRF_HOST_COMPLETE\n");
            }
            System.out.println("CLEAN_IMPORTED_MRF_HOST_COMPLETE");
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(dir, "clean_imported_mrf_host_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(failure.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Imported MRF host cleanup failed", failure);
        }
    }
}
