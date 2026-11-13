/* Generic MRF rotor-solid audit (P2.3 G5): proves the imported propeller solid is a single closed
 * manifold solid and writes the `recovered_rotor_solids.json` contract record that
 * BuildMrfFluidVolumes.java requires (status ok, output_role rotor_solid, output_part ==
 * CaseSpec.recovered_rotor_part, readback_surface_volume_m3 > 0).
 *
 * This macro is the generic replacement for the AIR-3B sector-recovery route: when the actual rotor
 * SOLID is supplied as CAD (our Fusion-built propeller), no Boolean recovery from fluid sectors is
 * needed; the solid is measured directly. It changes nothing else: it re-opens a geometry-only copy,
 * audits the named part, exports its tessellation, measures the SI signed volume/bounds, saves
 * `recovered_rotor_solids.sim` for the next stage, and writes the record. No region, interface,
 * continuum, boundary, mesh or solution is created.
 *
 * Inputs: case_spec.json in the session directory (recovered_rotor_part, geometry.rotor_parts,
 * model_length_unit); the imported-rotor .sim as the active simulation.
 * Outputs: recovered_rotor_solids.json, recovered_rotor_solids.txt, recovered_rotor_solids.stl,
 * recovered_rotor_solids.sim (saved copy for the domain-build stage).
 */
import java.io.File;
import java.io.FileInputStream;
import java.io.FileWriter;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.Dimensions;
import star.common.GeometryPart;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.CadPart;
import star.meshing.MeshPart;

public class AuditMrfRotorSolid extends StarMacro {

    private static final String COMPLETE = "MRF_ROTOR_SOLID_AUDITED";
    private static final String RECORD = "recovered_rotor_solids.json";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File record = new File(dir, RECORD);
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            String unitName = string(spec, "model_length_unit");
            String rotorName = string(spec, "recovered_rotor_part");
            String geometry = object(spec, "geometry");
            List<String> declared = strings(geometry, "rotor_parts");
            if (declared.size() != 1 || !declared.get(0).equals(rotorName)) {
                throw new IllegalStateException("CaseSpec geometry.rotor_parts must be exactly the recovered rotor part");
            }
            if (!sim.getRegionManager().getRegions().isEmpty() || !sim.getInterfaceManager().getObjects().isEmpty()
                    || sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration() != 0) {
                throw new IllegalStateException("Rotor-solid audit requires the explicitly cleaned geometry-only copy");
            }
            SimulationPartManager parts = sim.get(SimulationPartManager.class);
            GeometryPart part = parts.getPart(rotorName);
            if (part == null) throw new IllegalStateException("No part named " + rotorName + " in the simulation");
            if (!(part instanceof MeshPart)) throw new IllegalStateException("Rotor part is not a mesh part: " + part.getClass().getName());
            MeshPart mesh = (MeshPart) part;
            if (part instanceof CadPart) ((CadPart) part).tessellate();
            if (!mesh.isClosed()) throw new IllegalStateException("Rotor part is not closed");
            if (!mesh.isManifold()) throw new IllegalStateException("Rotor part is not manifold");
            int triangles = mesh.getTriangleCount();
            if (triangles <= 0) throw new IllegalStateException("Rotor part has no triangles");
            Units preferred = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
            if (preferred == null) throw new IllegalStateException("Simulation reports no preferred length unit");
            Units expected = sim.getUnitsManager().getObject(unitName);
            if (expected == null || !expected.equals(preferred)) {
                throw new IllegalStateException("Saved simulation length unit is not the CaseSpec unit " + unitName);
            }

            File stl = new File(dir, "recovered_rotor_solids.stl");
            if (stl.exists()) throw new IllegalStateException("Refuse to replace an existing tessellation export");
            mesh.exportStlPart(stl.getAbsolutePath(), true);
            double[] bounds = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                    Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
            double signedVolume = 0.0;
            long count;
            byte[] data = Files.readAllBytes(stl.toPath());
            if (data.length < 84) throw new IllegalStateException("Short STL export");
            ByteBuffer buffer = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);
            count = Integer.toUnsignedLong(buffer.getInt(80));
            if (count <= 0 || data.length != 84L + count * 50L) throw new IllegalStateException("Invalid binary STL layout");
            if (count != triangles) throw new IllegalStateException("STL triangle count differs from the part: " + count + " vs " + triangles);
            byte[] triangle = new byte[50];
            ByteBuffer row = ByteBuffer.wrap(triangle).order(ByteOrder.LITTLE_ENDIAN);
            for (long i = 0; i < count; i++) {
                System.arraycopy(data, 84 + (int) i * 50, triangle, 0, 50);
                double[][] vertex = new double[3][3];
                for (int v = 0; v < 3; v++) {
                    for (int axis = 0; axis < 3; axis++) {
                        double raw = row.getFloat(12 + v * 12 + axis * 4);
                        if (!Double.isFinite(raw)) throw new IllegalStateException("Non-finite STL vertex");
                        vertex[v][axis] = raw;
                        bounds[axis] = Math.min(bounds[axis], raw);
                        bounds[axis + 3] = Math.max(bounds[axis + 3], raw);
                    }
                }
                signedVolume += (vertex[0][0] * (vertex[1][1] * vertex[2][2] - vertex[1][2] * vertex[2][1])
                        - vertex[0][1] * (vertex[1][0] * vertex[2][2] - vertex[1][2] * vertex[2][0])
                        + vertex[0][2] * (vertex[1][0] * vertex[2][1] - vertex[1][1] * vertex[2][0])) / 6.0;
            }
            double volume = Math.abs(signedVolume);
            if (!(volume > 0.0) || !Double.isFinite(volume)) throw new IllegalStateException("Measured rotor volume is not positive");
            for (int axis = 0; axis < 6; axis++) if (!Double.isFinite(bounds[axis])) throw new IllegalStateException("Non-finite measured bounds");
            String stlSha = sha(stl);
            File saved = new File(dir, "recovered_rotor_solids.sim");
            if (saved.exists()) throw new IllegalStateException("Refuse to replace an existing recovered-rotor checkpoint");
            sim.saveState(saved.getAbsolutePath());
            String simSha = sha(saved);
            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":2,\"status\":\"ok\",\"input_role\":\"imported_rotor_cad\"")
                    .append(",\"source_part\":\"").append(escape(rotorName)).append('"')
                    .append(",\"output_part\":\"").append(escape(rotorName)).append('"')
                    .append(",\"output_role\":\"rotor_solid\"")
                    .append(",\"is_closed\":true,\"is_manifold\":true,\"solid_count\":1")
                    .append(",\"selected_closed\":true,\"selected_manifold\":true")
                    .append(",\"part_class\":\"").append(escape(part.getClass().getName())).append('"')
                    .append(",\"part_surfaces\":").append(mesh.getPartSurfaces().size())
                    .append(",\"triangle_count\":").append(triangles)
                    .append(",\"readback_surface_volume_m3\":").append(volume)
                    .append(",\"selected_surface_volume_m3\":").append(volume)
                    .append(",\"bounds_m\":[").append(bounds[0]).append(',').append(bounds[1]).append(',')
                    .append(bounds[2]).append(',').append(bounds[3]).append(',').append(bounds[4]).append(',')
                    .append(bounds[5]).append(']')
                    .append(",\"model_length_unit\":\"").append(escape(unitName)).append('"')
                    .append(",\"model_length_unit_readback\":\"").append(escape(preferred.getSuffix())).append('"')
                    .append(",\"regions\":").append(sim.getRegionManager().getRegions().size())
                    .append(",\"interfaces\":").append(sim.getInterfaceManager().getObjects().size())
                    .append(",\"iteration\":").append(sim.getSimulationIterator().getCurrentIteration())
                    .append(",\"initialized\":").append(sim.isInitialized())
                    .append(",\"stl_file\":\"recovered_rotor_solids.stl\",\"stl_sha256\":\"").append(stlSha).append('"')
                    .append(",\"sim_file\":\"recovered_rotor_solids.sim\",\"sim_sha256\":\"").append(simSha).append('"')
                    .append(",\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
            try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            try (FileWriter out = new FileWriter(new File(dir, "recovered_rotor_solids.txt"), StandardCharsets.UTF_8)) {
                out.write("source_part=" + rotorName + "\noutput_part=" + rotorName + "\nvolume_m3=" + volume
                        + "\ntriangles=" + triangles + "\nclosed=" + mesh.isClosed() + "\nmanifold=" + mesh.isManifold()
                        + "\n" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"error\",\"error\":\""
                        + escape(failure.toString()) + "\"}\n");
            } catch (Throwable ignored) {
                // the original failure is rethrown below; the diagnostic must not mask it
            }
            throw new RuntimeException("MRF rotor-solid audit failed", failure);
        }
    }

    private static String sha(File file) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file.toPath()));
        StringBuilder value = new StringBuilder();
        for (byte item : digest) value.append(String.format("%02x", item & 0xff));
        return value.toString();
    }

    private static String escape(String value) {
        StringBuilder out = new StringBuilder();
        for (char c : value.toCharArray()) {
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }

    private static String object(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
        return m.group(1);
    }

    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }

    private static List<String> strings(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing array " + key);
        List<String> out = new ArrayList<>();
        Matcher item = Pattern.compile("\\\"([^\\\"]+)\\\"").matcher(m.group(1));
        while (item.find()) out.add(item.group(1));
        return out;
    }
}
