/* Isolated STAR 2502 probe for a Gmsh-generated CGNS volume mesh. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.Boundary;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;

public class ProbeGmshCgnsImport extends StarMacro {
    private static String field(String spec, String name) {
        Matcher match = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!match.find()) throw new IllegalArgumentException("Missing " + name);
        return match.group(1);
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
            String ref = field(spec, "mesh_ref");
            String expectedHash = field(spec, "mesh_sha256");
            if (!ref.matches("library/cgns_candidates/[A-Za-z0-9_-]+\\.cgns") ||
                    !expectedHash.matches("[0-9a-f]{64}")) {
                throw new IllegalArgumentException("Invalid mesh ref or hash");
            }
            File root = dir.getParentFile().getParentFile().getCanonicalFile();
            File mesh = new File(root, ref.replace('/', File.separatorChar)).getCanonicalFile();
            if (!mesh.isFile() || !mesh.getPath().startsWith(root.getPath() + File.separator) ||
                    !sha256(mesh).equals(expectedHash)) {
                throw new IllegalArgumentException("CGNS missing, outside worker root, or hash mismatch");
            }
            Set<String> before = new HashSet<>();
            for (Region region : sim.getRegionManager().getRegions()) before.add(region.getPresentationName());
            FvRepresentation volumeMesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            long beforeCells = volumeMesh == null ? 0 : volumeMesh.getCellCount();
            sim.getImportManager().importMeshFiles(mesh.getAbsolutePath());
            volumeMesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            long afterCells = volumeMesh == null ? 0 : volumeMesh.getCellCount();
            int newRegions = 0;
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_cgns_import_probe.txt"), StandardCharsets.UTF_8)) {
                out.write("mesh_ref=" + ref + "\nmesh_sha256=" + expectedHash + "\n");
                out.write("cells_before=" + beforeCells + "\ncells_after=" + afterCells + "\n");
                for (Region region : sim.getRegionManager().getRegions()) {
                    if (before.contains(region.getPresentationName())) continue;
                    newRegions++;
                    out.write("new_region=" + region.getPresentationName() + "\n");
                    out.write("volume_mesh_describes=" + (volumeMesh != null && volumeMesh.describesRegion(region)) + "\n");
                    for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                        out.write("boundary=" + boundary.getPresentationName() + "\t" +
                                boundary.getBoundaryType().getPresentationName() + "\n");
                    }
                }
                out.write("new_region_count=" + newRegions + "\n");
                if (newRegions < 1 || afterCells <= beforeCells) {
                    throw new IllegalStateException("CGNS did not add a meshed Region");
                }
                out.write("GMSH_CGNS_IMPORT_PROBE_COMPLETE\n");
            }
            sim.saveState(new File(dir, "gmsh_cgns_imported.sim").getAbsolutePath());
            System.out.println("GMSH_CGNS_IMPORT_PROBE_COMPLETE");
        } catch (Throwable failure) {
            StringWriter detail = new StringWriter();
            failure.printStackTrace(new PrintWriter(detail));
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_cgns_import_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(detail.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Gmsh CGNS import probe failed", failure);
        }
    }
}
