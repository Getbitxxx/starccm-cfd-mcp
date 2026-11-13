/* Generic MRF fluid-volume adequacy audit (P2.3 G5 part 2): measure the two constructed fluid volumes
 * and gate what the volume-build stage only claimed topologically — volumes, bounds and containment.
 *
 * It re-opens the geometry-only MRF copy (0 regions / 0 interfaces / 0 iterations), requires both
 * CaseSpec fluid parts closed+manifold, exports each tessellation, computes signed volumes / bounds /
 * triangle counts / edge integrity, and compares against the CaseSpec analytic expectations:
 *   stationary = outer cylinder - rotation envelope
 *   rotating   = rotation envelope - rotor solid (volume from recovered_rotor_solids.json)
 * Containment: the rotating volume must lie inside the envelope bounds and the envelope inside the
 * stationary bounds, with a documented tessellation tolerance. It also requires the stationary outer
 * bounds to match the CaseSpec domain cylinder within tolerance.
 *
 * Measurement basis (documented, not a relaxation of physics): both volumes are measured from STAR's
 * COARSE tessellation of the Boolean parts, while the expectations are analytic B-rep volumes; the
 * relative tolerance (2%) covers that discretisation and the bounds tolerance (0.5 mm) covers the
 * cylinder facet sagitta. No mesh/physics/init/solve; writes mrf_fluid_volume_audit.json and saves
 * mrf_fluid_volumes_audited.sim for the surface-map/regions stages.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.common.GeometryPart;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.MeshOperationPart;

public class AuditMrfFluidVolumes extends StarMacro {

    private static final String COMPLETE = "MRF_FLUID_VOLUME_AUDIT_COMPLETE";
    private static final double VOLUME_RELATIVE_TOLERANCE = 0.02;
    private static final double BOUNDS_TOLERANCE_M = 5.0e-4;

    private static final class Measured {
        double volume;              // abs signed volume, m3
        double[] bounds = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
        int triangles;
        int boundaryEdges;
        int nonmanifoldEdges;
        boolean closed;
        boolean manifold;
        double maxRadius;      // max sqrt(x^2+y^2) over tessellation vertices (cylinder radius metric)
        double centerX;
        double centerY;
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File record = new File(dir, "mrf_fluid_volume_audit.json");
        boolean measuredRecordWritten = false;
        try {
            String spec = read(new File(dir, "case_spec.json"));
            String geometry = jsonObject(spec, "geometry");
            String domain = jsonObject(spec, "domain");
            String zone = jsonObject(spec, "rotation_zone");
            String rotorName = jsonString(spec, "recovered_rotor_part");
            double radius = jsonNumber(domain, "cylinder_radius_m");
            double zMin = jsonNumber(domain, "cylinder_z_min_m");
            double zMax = jsonNumber(domain, "cylinder_z_max_m");
            double envelopeRadius = jsonNumber(zone, "radius_m");
            double envelopeZMin = jsonNumber(zone, "z_min_m");
            double envelopeZMax = jsonNumber(zone, "z_max_m");
            double[] center = jsonArray(domain, "cylinder_center_xy_m", 2);
            double[] zoneCenter = jsonArray(zone, "center_xy_m", 2);
            if (Math.abs(center[0] - zoneCenter[0]) > 1e-9 || Math.abs(center[1] - zoneCenter[1]) > 1e-9) {
                throw new IllegalStateException("Outer domain and rotation zone are not coaxial in the CaseSpec");
            }
            if (!(radius > envelopeRadius && envelopeRadius > 0 && zMin < envelopeZMin
                    && envelopeZMin < envelopeZMax && envelopeZMax < zMax)) {
                throw new IllegalStateException("CaseSpec domain/envelope dimensions are not a valid MRF nesting");
            }
            String recovered = read(new File(dir, "recovered_rotor_solids.json"));
            if (!"ok".equals(jsonString(recovered, "status"))) throw new IllegalStateException("Recovered rotor record is not ok");
            if (!rotorName.equals(jsonString(recovered, "output_part"))) throw new IllegalStateException("Recovered rotor part role differs");
            double rotorVolume = jsonNumber(recovered, "readback_surface_volume_m3");
            if (!(rotorVolume > 0.0)) throw new IllegalStateException("Recovered rotor volume is not positive");
            if (!sim.getRegionManager().getObjects().isEmpty() || !sim.getInterfaceManager().getObjects().isEmpty()
                    || sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration() != 0) {
                throw new IllegalStateException("Fluid-volume audit requires the geometry-only copy");
            }
            SimulationPartManager parts = sim.get(SimulationPartManager.class);
            Measured stationary = measure(parts, jsonString(geometry, "stationary_fluid_part"), new File(dir, "mrf_stationary_volume.stl"));
            Measured rotating = measure(parts, jsonString(geometry, "rotating_fluid_part"), new File(dir, "mrf_rotating_volume.stl"));
            double outerVolume = Math.PI * radius * radius * (zMax - zMin);
            double envelopeVolume = Math.PI * envelopeRadius * envelopeRadius * (envelopeZMax - envelopeZMin);
            double expectedStationary = outerVolume - envelopeVolume;
            double expectedRotating = envelopeVolume - rotorVolume;
            if (!(expectedStationary > 0.0) || !(expectedRotating > 0.0)) throw new IllegalStateException("Analytic fluid volumes are not positive");
            double stationaryDeviation = relativeDeviation(stationary.volume, expectedStationary);
            double rotatingDeviation = relativeDeviation(rotating.volume, expectedRotating);
            boolean volumePass = stationaryDeviation <= VOLUME_RELATIVE_TOLERANCE && rotatingDeviation <= VOLUME_RELATIVE_TOLERANCE;
            double[] outerExpected = {center[0] - radius, center[1] - radius, zMin, center[0] + radius, center[1] + radius, zMax};
            // the envelope is placed at the ROTATION ZONE centre (BuildMrfFluidVolumes), not the domain centre
            double[] envelopeExpected = {zoneCenter[0] - envelopeRadius, zoneCenter[1] - envelopeRadius, envelopeZMin,
                    zoneCenter[0] + envelopeRadius, zoneCenter[1] + envelopeRadius, envelopeZMax};
            double outerBoundsError = Math.max(Math.max(Math.abs(stationary.maxRadius - radius),
                            Math.abs(stationary.bounds[2] - zMin)), Math.max(Math.abs(stationary.bounds[5] - zMax),
                            Math.max(Math.abs(stationary.centerX - center[0]), Math.abs(stationary.centerY - center[1]))));
            boolean outerBoundsPass = outerBoundsError <= BOUNDS_TOLERANCE_M;
            boolean envelopeInsideStationary = stationary.maxRadius >= envelopeRadius - BOUNDS_TOLERANCE_M
                    && stationary.bounds[2] <= envelopeZMin + BOUNDS_TOLERANCE_M
                    && stationary.bounds[5] >= envelopeZMax - BOUNDS_TOLERANCE_M
                    && Math.abs(stationary.centerX - zoneCenter[0]) <= BOUNDS_TOLERANCE_M
                    && Math.abs(stationary.centerY - zoneCenter[1]) <= BOUNDS_TOLERANCE_M;
            boolean rotatingInsideEnvelope = rotating.maxRadius <= envelopeRadius + BOUNDS_TOLERANCE_M
                    && rotating.bounds[2] >= envelopeZMin - BOUNDS_TOLERANCE_M
                    && rotating.bounds[5] <= envelopeZMax + BOUNDS_TOLERANCE_M
                    && Math.abs(rotating.centerX - zoneCenter[0]) <= BOUNDS_TOLERANCE_M
                    && Math.abs(rotating.centerY - zoneCenter[1]) <= BOUNDS_TOLERANCE_M;
            boolean rotatingInsideStationary = rotating.maxRadius <= stationary.maxRadius + BOUNDS_TOLERANCE_M
                    && rotating.bounds[2] >= stationary.bounds[2] - BOUNDS_TOLERANCE_M
                    && rotating.bounds[5] <= stationary.bounds[5] + BOUNDS_TOLERANCE_M;
            boolean edgePass = stationary.boundaryEdges == 0 && stationary.nonmanifoldEdges == 0
                    && rotating.boundaryEdges == 0 && rotating.nonmanifoldEdges == 0;
            boolean gatesPass = volumePass && outerBoundsPass && edgePass && envelopeInsideStationary
                    && rotatingInsideEnvelope && rotatingInsideStationary;
            String gateError = gatesPass ? null : ("volumePass=" + volumePass + " outerBoundsPass=" + outerBoundsPass
                    + " edgePass=" + edgePass + " envelopeInsideStationary=" + envelopeInsideStationary
                    + " rotatingInsideEnvelope=" + rotatingInsideEnvelope
                    + " rotatingInsideStationary=" + rotatingInsideStationary);
            StringBuilder json = new StringBuilder();
            json.append("{\"schema_version\":1,\"status\":\"").append(gatesPass ? "ok" : "error")
                    .append("\",\"measurement_basis\":\"STAR coarse closed tessellation signed volume/bounds vs analytic B-rep expectations\"")
                    .append(",\"volume_relative_tolerance\":").append(VOLUME_RELATIVE_TOLERANCE)
                    .append(",\"bounds_tolerance_m\":").append(BOUNDS_TOLERANCE_M)
                    .append(",\"stationary_part\":\"").append(esc(jsonString(geometry, "stationary_fluid_part"))).append('"')
                    .append(",\"rotating_part\":\"").append(esc(jsonString(geometry, "rotating_fluid_part"))).append('"')
                    .append(",\"rotor_part\":\"").append(esc(rotorName)).append('"')
                    .append(",\"rotor_volume_m3\":").append(rotorVolume)
                    .append(",\"outer_volume_expected_m3\":").append(outerVolume)
                    .append(",\"envelope_volume_expected_m3\":").append(envelopeVolume)
                    .append(",\"stationary\":").append(measuredJson(stationary, expectedStationary, stationaryDeviation))
                    .append(",\"rotating\":").append(measuredJson(rotating, expectedRotating, rotatingDeviation))
                    .append(",\"stationary_relative_deviation\":").append(stationaryDeviation)
                    .append(",\"rotating_relative_deviation\":").append(rotatingDeviation)
                    .append(",\"outer_bounds_expected_m\":").append(arrayJson(outerExpected))
                    .append(",\"envelope_bounds_expected_m\":").append(arrayJson(envelopeExpected))
                    .append(",\"outer_bounds_error_m\":").append(outerBoundsError)
                    .append(",\"volume_pass\":").append(volumePass)
                    .append(",\"outer_bounds_pass\":").append(outerBoundsPass)
                    .append(",\"edge_integrity_pass\":").append(edgePass)
                    .append(",\"envelope_inside_stationary\":").append(envelopeInsideStationary)
                    .append(",\"rotating_inside_envelope\":").append(rotatingInsideEnvelope)
                    .append(",\"rotating_inside_stationary\":").append(rotatingInsideStationary)
                    .append(",\"regions\":").append(sim.getRegionManager().getObjects().size())
                    .append(",\"interfaces\":").append(sim.getInterfaceManager().getObjects().size())
                    .append(",\"iteration\":").append(sim.getSimulationIterator().getCurrentIteration())
                    .append(",\"initialized\":").append(sim.isInitialized())
                    .append(gateError == null ? "" : ",\"gate_error\":\"" + esc(gateError) + "\"")
                    .append(",\"complete_marker\":\"").append(COMPLETE).append("\"}\n");
            File saved = new File(dir, "mrf_fluid_volumes_audited.sim");
            if (saved.exists()) throw new IllegalStateException("Refuse to replace an existing fluid-volume audit checkpoint");
            sim.saveState(saved.getAbsolutePath());
            try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) {
                out.write(json.toString());
            }
            measuredRecordWritten = true;
            // A failed gate keeps the measured record (volumes/bounds/deviations/containment plus gate_error) so the
            // failure is diagnosable from recorded numbers; only the gate result is fail-closed.
            if (!gatesPass) throw new IllegalStateException("MRF fluid-volume adequacy gate failed: " + gateError);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            if (!measuredRecordWritten) {
                try (FileWriter out = new FileWriter(record, StandardCharsets.UTF_8)) {
                    out.write("{\"schema_version\":1,\"status\":\"error\",\"error\":\"" + esc(failure.toString()) + "\"}\n");
                } catch (Throwable ignored) {
                    // the original failure is rethrown below; the diagnostic must not mask it
                }
            }
            throw new RuntimeException("MRF fluid-volume audit failed", failure);
        }
    }

    private static Measured measure(SimulationPartManager parts, String name, File stl) throws Exception {
        GeometryPart part = parts.getPart(name);
        if (!(part instanceof MeshOperationPart)) throw new IllegalStateException("Fluid role is not a Boolean part: " + name);
        MeshOperationPart volume = (MeshOperationPart) part;
        if (!volume.isClosed()) throw new IllegalStateException("Fluid volume is not closed: " + name);
        if (!volume.isManifold()) throw new IllegalStateException("Fluid volume is not manifold: " + name);
        if (volume.getTriangleCount() <= 0) throw new IllegalStateException("Fluid volume has no triangles: " + name);
        if (stl.isFile()) throw new IllegalStateException("Refuse to replace an existing tessellation export: " + stl.getName());
        volume.exportStlPart(stl.getAbsolutePath(), true);
        byte[] data = Files.readAllBytes(stl.toPath());
        if (data.length < 84) throw new IllegalStateException("Short STL export: " + stl.getName());
        int count = ByteBuffer.wrap(data, 80, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
        if (count <= 0 || data.length != 84L + 50L * count) throw new IllegalStateException("Invalid binary STL: " + stl.getName());
        if (count != volume.getTriangleCount()) throw new IllegalStateException("STL triangle count differs from the part");
        Measured measured = new Measured();
        measured.triangles = count;
        measured.closed = true;
        measured.manifold = true;
        Map<String, Integer> edgeUses = new HashMap<String, Integer>();
        ByteBuffer buffer = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);
        double signed = 0.0;
        double maxRadius = 0.0;
        for (int i = 0; i < count; i++) {
            int base = 84 + i * 50 + 12;
            double[][] v = new double[3][3];
            String[] keys = new String[3];
            for (int k = 0; k < 3; k++) {
                StringBuilder key = new StringBuilder();
                for (int axis = 0; axis < 3; axis++) {
                    float raw = buffer.getFloat(base + k * 12 + axis * 4);
                    if (!Float.isFinite(raw)) throw new IllegalStateException("Non-finite STL vertex");
                    v[k][axis] = raw;
                    measured.bounds[axis] = Math.min(measured.bounds[axis], raw);
                    measured.bounds[axis + 3] = Math.max(measured.bounds[axis + 3], raw);
                    key.append(Float.floatToIntBits(raw == 0.0f ? 0.0f : raw)).append('/');
                }
                keys[k] = key.toString();
                maxRadius = Math.max(maxRadius, Math.sqrt(v[k][0] * v[k][0] + v[k][1] * v[k][1]));
            }
            signed += (v[0][0] * (v[1][1] * v[2][2] - v[1][2] * v[2][1])
                    - v[0][1] * (v[1][0] * v[2][2] - v[1][2] * v[2][0])
                    + v[0][2] * (v[1][0] * v[2][1] - v[1][1] * v[2][0])) / 6.0;
            for (int k = 0; k < 3; k++) {
                String a = keys[k], b = keys[(k + 1) % 3];
                String edge = a.compareTo(b) < 0 ? a + ":" + b : b + ":" + a;
                Integer prior = edgeUses.get(edge);
                edgeUses.put(edge, prior == null ? 1 : prior + 1);
            }
        }
        measured.volume = Math.abs(signed);
        measured.maxRadius = maxRadius;
        // robust centre: mid-point of the measured bounds (a coarse tessellation of a symmetric cylinder is
        // still symmetric in its extremes; the vertex centroid is biased by non-uniform triangle sizes).
        measured.centerX = 0.5 * (measured.bounds[0] + measured.bounds[3]);
        measured.centerY = 0.5 * (measured.bounds[1] + measured.bounds[4]);
        for (Map.Entry<String, Integer> entry : edgeUses.entrySet()) {
            if (entry.getValue() == 1) measured.boundaryEdges++;
            if (entry.getValue() > 2) measured.nonmanifoldEdges++;
        }
        if (!(measured.volume > 0.0) || !Double.isFinite(measured.volume)) throw new IllegalStateException("Measured volume is not positive");
        for (double value : measured.bounds) if (!Double.isFinite(value)) throw new IllegalStateException("Non-finite measured bounds");
        return measured;
    }

    private static String measuredJson(Measured measured, double expected, double deviation) {
        return "{\"volume_m3\":" + measured.volume + ",\"expected_volume_m3\":" + expected
                + ",\"relative_deviation\":" + deviation + ",\"bounds_m\":" + arrayJson(measured.bounds)
                + ",\"triangles\":" + measured.triangles + ",\"boundary_edges\":" + measured.boundaryEdges
                + ",\"nonmanifold_edges\":" + measured.nonmanifoldEdges
                + ",\"is_closed\":" + measured.closed + ",\"is_manifold\":" + measured.manifold + "}";
    }

    private static double relativeDeviation(double measured, double expected) {
        return Math.abs(measured - expected) / expected;
    }

    private static double maxBoundsError(double[] measured, double[] expected) {
        double error = 0.0;
        for (int i = 0; i < 6; i++) error = Math.max(error, Math.abs(measured[i] - expected[i]));
        return error;
    }

    private static boolean contains(double[] outer, double[] inner, double tolerance) {
        return inner[0] >= outer[0] - tolerance && inner[1] >= outer[1] - tolerance && inner[2] >= outer[2] - tolerance
                && inner[3] <= outer[3] + tolerance && inner[4] <= outer[4] + tolerance && inner[5] <= outer[5] + tolerance;
    }

    private static String arrayJson(double[] values) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < values.length; i++) {
            if (i > 0) out.append(',');
            out.append(values[i]);
        }
        return out.append(']').toString();
    }

    private static String read(File file) throws Exception {
        if (!file.isFile()) throw new IllegalStateException("Missing required file: " + file.getName());
        return Files.readString(file.toPath(), StandardCharsets.UTF_8);
    }

    private static String jsonObject(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing object " + key);
        return m.group(1);
    }

    private static String jsonString(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing string " + key);
        return m.group(1);
    }

    private static double jsonNumber(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(m.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double[] jsonArray(String json, String key, int size) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing array " + key);
        String[] words = m.group(1).split(",");
        if (words.length != size) throw new IllegalArgumentException("Wrong length " + key);
        double[] values = new double[size];
        for (int i = 0; i < size; i++) {
            values[i] = Double.parseDouble(words[i].trim());
            if (!Double.isFinite(values[i])) throw new IllegalArgumentException("Non-finite " + key);
        }
        return values;
    }

    private static String esc(String value) {
        StringBuilder out = new StringBuilder();
        for (char c : value.toCharArray()) {
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
