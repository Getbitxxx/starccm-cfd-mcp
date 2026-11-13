/* Generic static-body geometry for a free-air external-flow case.
 *
 * Reads an explicit SI body definition from `case_spec.json`, creates exactly one closed body
 * part with the installed 2502 native shape API, verifies it, saves a geometry-only .sim, and
 * writes an evidence record. It never creates regions, boundaries, mesh operations, physics or
 * reports, never initializes and never iterates.
 *
 * Every selector and dimension comes from the CaseSpec; no sample name, unit or size is implied.
 * A shape other than a sphere fails closed.
 *
 * Two kinds of quantity are recorded and are labelled differently, because they are not the same
 * kind of evidence:
 *   - `*_spec_readback_*`  : STAR's read-back of the shape parameter this macro set. It proves the
 *                            API accepted the value; it is NOT a measurement of the geometry.
 *   - `measured_*`         : quantities computed from the tessellation STAR actually produced,
 *                            exported as a binary STL and measured here. These are measurements.
 *
 * A failure must leave its diagnosis behind (project rule: 失败必须留证), so the failure path
 * writes an error record with `usable:false` and rethrows the original cause.
 *
 * Inputs:  case_spec.json in the session directory or the working directory.
 * Outputs: static_body_geometry.sim, static_body_geometry.json, static_body_tessellation.stl.
 */
import java.io.File;
import java.io.FileInputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.DoubleVector;
import star.common.Coordinate;
import star.common.Dimensions;
import star.common.GeometryPart;
import star.common.LabCoordinateSystem;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.MeshPart;
import star.meshing.MeshPartFactory;
import star.meshing.SimpleSpherePart;

public class BuildStaticBodyGeometry extends StarMacro {
    private static final String COMPLETE = "STATIC_BODY_GEOMETRY_BUILT";
    private static final String OUTPUT_SIM = "static_body_geometry.sim";
    private static final String EVIDENCE = "static_body_geometry.json";
    private static final String TESSELLATION = "static_body_tessellation.stl";
    private static final String DEFAULT_BODY_NAME = "BODY";

    /** STL vertices are stored as 32-bit floats, so a coordinate of magnitude |x| carries a storage
     *  error of about ulp(float32 |x|) / 2. A purely relative tolerance therefore becomes unsound as
     *  the body moves away from the origin: the old 1e-6 * R rule is sound only while
     *  sqrt(3) * ulp32(|x|) / 2 <= 1e-6 * R, which for R = 0.05 m means |x| below about 10 * R;
     *  beyond that, whether it accepts a correct body depends on where the tessellation happens to
     *  place vertices. Measured on an emulated float32 sphere of R = 0.05 m (see
     *  tests/test_star_body_tolerances.py, which pins these numbers): the old rule used 4.6 percent of
     *  its budget at the origin, 93.9 percent at [0, 0, 1.5] m - it still PASSED there - and failed at
     *  [-1.234, 0, 2.5] m and [5, 0, 0] m. The new rule measures radii about the measured centroid
     *  with a tolerance derived from the storage precision actually written, so it does not inherit
     *  that dependency. */
    private static final double MIN_VERTEX_RADIUS_RELATIVE_TOLERANCE = 1e-6;
    private static final double STORAGE_ULP_MULTIPLIER = 8.0;
    /** A faceted sphere inscribes the true sphere, so its volume and bounding box are slightly
     *  small. 5 percent is far looser than faceting (well under 1 percent for any normal
     *  tessellation) and still catches a wrong radius or a wrong shape. */
    private static final double MAX_GEOMETRIC_RELATIVE_DEVIATION = 0.05;
    /** The unit STAR writes into an exported STL.
     *
     *  ROUND 12, measured twice: `MeshPart.exportStlPart` does NOT use the simulation's model/display
     *  unit. With model_length_unit = mm the preferred length unit read-back is mm (suffix " (mm)",
     *  getConversion() 0.001, recorded in the evidence) and yet the exported STL measures 0.05 - metres,
     *  i.e. the simulation's INTERNAL length unit. Attempt A
     *  (test-evidence/static-external-flow/round12-mm-geometry/s1-static-sphere-mm-body-20261003a) found
     *  it by failing; attempt B
     *  (test-evidence/static-external-flow/round12-mm-geometry-b/s1-static-sphere-mm-body-20261003b)
     *  confirmed it with the preferred unit read-back passing and the measurement still 1/1000 low.
     *  The installed 20.02.008 API has no Units overload of exportStlPart (javap dump
     *  test-evidence/static-external-flow/star2502-native-part-api.txt), so the export unit cannot be
     *  selected. measure() below therefore converts the STL with THIS constant, while the CaseSpec unit
     *  is still applied to the properties and verified by read-back for the model-unit claim. If a future
     *  build exports in model units instead, the radius gate fails loudly rather than passing silently. */
    private static final double STL_EXPORT_UNIT_TO_M = 1.0;

    private static final class Measurement {
        double[] bbox;
        double[] halfExtent;
        double[] centroid;
        double maxAbsCoordinate;
        double minVertexRadius;
        double maxVertexRadius;
        double volume;
        long triangles;
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        Map<String, String> context = new LinkedHashMap<String, String>();
        File dir = locateDir(sim);
        File evidence = new File(dir, EVIDENCE);
        File output = new File(dir, OUTPUT_SIM);
        try {
            run(sim, dir, output, context);
            writeSuccess(evidence, context);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            String message = describe(failure);
            try {
                writeFailure(evidence, dir, output, context, failure, message);
            } catch (Throwable secondary) {
                message = message + " | failure evidence could not be written: " + describe(secondary);
            }
            throw new RuntimeException("Static-body geometry construction failed: " + message, failure);
        }
    }

    private void run(Simulation sim, File dir, File output, Map<String, String> context) throws Exception {
        File specFile = new File(dir, "case_spec.json");
        if (!specFile.isFile()) {
            throw new IllegalStateException("case_spec.json is required in " + dir.getAbsolutePath());
        }
        String spec = new String(Files.readAllBytes(specFile.toPath()), StandardCharsets.UTF_8);
        context.put("case_spec_sha256", sha(specFile));

        String unitName = stringValue(spec, "model_length_unit");
        context.put("model_length_unit", unitName);
        double scale = unitToMeters(unitName);
        String shape = optionalString(spec, "body_shape", "sphere");
        context.put("body_shape", shape);
        if ("step".equals(optionalString(spec, "geometry_source_format", "native"))) {
            importStep(sim, dir, output, spec, unitName, context);
            return;
        }
        if (!"sphere".equals(shape)) {
            throw new IllegalArgumentException("Unsupported body_shape for this release: " + shape);
        }
        String bodyName = optionalString(spec, "body_part_name", DEFAULT_BODY_NAME);
        context.put("body_part_name", bodyName);
        if (!bodyName.matches("[A-Za-z][A-Za-z0-9_.-]{0,63}")) {
            throw new IllegalArgumentException("Invalid body_part_name");
        }
        double[] originM = arrayValue(spec, "body_origin_m", 3);
        context.put("body_origin_m", "[" + originM[0] + "," + originM[1] + "," + originM[2] + "]");
        double radiusM = numberValue(spec, "body_radius_m");
        context.put("body_radius_m", Double.toString(radiusM));
        if (!(radiusM > 0.0)) throw new IllegalArgumentException("body_radius_m must be positive");
        if (output.exists()) throw new IllegalStateException("Output simulation already exists");
        File stl = new File(dir, TESSELLATION);
        if (stl.exists()) throw new IllegalStateException("Tessellation export target already exists");

        SimulationPartManager parts = sim.get(SimulationPartManager.class);
        if (!parts.getParts().isEmpty()) {
            throw new IllegalStateException("Requires a simulation with no existing parts");
        }
        if (!sim.getRegionManager().getRegions().isEmpty()) {
            throw new IllegalStateException("Requires a simulation with no existing regions");
        }
        if (sim.getSimulationIterator().getCurrentIteration() != 0) {
            throw new IllegalStateException("Requires an un-iterated simulation");
        }

        SimpleSpherePart sphere = sim.get(MeshPartFactory.class).createNewSpherePart(parts);
        sphere.setPresentationName(bodyName);
        sphere.setDoNotRetessellate(true);
        LabCoordinateSystem lab = sim.getCoordinateSystemManager().getLabCoordinateSystem();
        Units units = sim.getUnitsManager().getObject(unitName);
        // ROUND 12: setting each property's units is NOT the same as making the CaseSpec unit the
        // simulation's MODEL unit. Before this block the macro set the radius as 50 mm (read-back 0.05 m,
        // so the read-back check passed) while the simulation's preferred length unit stayed m; STAR then
        // wrote every STL coordinate in m, and measure(stl, scale) - which assumes the STL is in model
        // units - divided by 1000 a second time and reported a radius of 5e-5 m. The unit acceptance run
        // measured exactly that (measured_vertex_radius_abs_error_m 0.04995000000209353 = R*(1-0.001),
        // measured_storage_ulp_m 3.637978807091713E-12 = the float32 ULP of 0.05*0.001), i.e. a real 1000x
        // defect in the instrument. star.common.Units.setPreferred(boolean) exists in the installed
        // 20.02.008 jars (javap dump test-evidence/static-external-flow/star2502-native-part-api.txt), so
        // the model unit is set here and then VERIFIED by read-back against the CaseSpec scale.
        Units previousPreferred = sim.getUnitsManager().getPreferredUnits(
                Dimensions.Builder().length(1).build());
        if (previousPreferred != null && !previousPreferred.equals(units)) {
            previousPreferred.setPreferred(false);
        }
        units.setPreferred(true);
        sphere.setCoordinateSystem(lab);
        Coordinate origin = sphere.getOrigin();
        origin.setCoordinateSystem(lab);
        origin.setUnits(units);
        origin.setValue(new DoubleVector(new double[] {originM[0] / scale, originM[1] / scale, originM[2] / scale}));
        sphere.getRadius().setValueAndUnits(radiusM / scale, units);
        sphere.rebuildSimpleShapePart();
        sphere.setDoNotRetessellate(false);

        GeometryPart created = parts.getPart(bodyName);
        if (created == null) throw new IllegalStateException("Created body part is absent from the part manager");
        if (!(created instanceof MeshPart)) throw new IllegalStateException("Created body part is not a mesh part");
        MeshPart mesh = (MeshPart) created;
        if (!mesh.isClosed()) throw new IllegalStateException("Created body part is not closed");
        if (!mesh.isManifold()) throw new IllegalStateException("Created body part is not manifold");
        int triangles = mesh.getTriangleCount();
        if (triangles <= 0) throw new IllegalStateException("Created body part has no triangles");
        List<String> surfaceNames = new ArrayList<String>();
        for (star.common.PartSurface surface : mesh.getPartSurfaces()) {
            surfaceNames.add(surface.getPresentationName());
        }
        if (surfaceNames.isEmpty()) throw new IllegalStateException("Created body part has no part surfaces");

        DoubleVector readbackSI = origin.getSIValue();
        double[] readbackOrigin = new double[] {readbackSI.get(0), readbackSI.get(1), readbackSI.get(2)};
        double readbackRadius = sphere.getRadius().getValue() * scale;
        double originError = 0.0;
        for (int axis = 0; axis < 3; axis++) {
            finite(readbackOrigin[axis], "read-back origin component");
            originError = Math.max(originError, Math.abs(readbackOrigin[axis] - originM[axis]));
        }
        finite(readbackRadius, "read-back radius");
        double radiusError = Math.abs(readbackRadius - radiusM);
        finite(originError, "origin read-back error");
        finite(radiusError, "radius read-back error");
        if (originError > 1e-12) throw new IllegalStateException("Read-back origin differs from CaseSpec");
        if (radiusError > 1e-12) throw new IllegalStateException("Read-back radius differs from CaseSpec");

        // Read-back of the MODEL unit itself. This is the check that was missing: the property read-back
        // above is satisfied by a value of 50 interpreted as mm even when the simulation's preferred
        // length unit is m, and in that state STAR exports the STL in m. Requiring the preferred length
        // unit's conversion to equal the CaseSpec scale is what makes measure(stl, scale) below sound.
        Units preferredLength = sim.getUnitsManager().getPreferredUnits(
                Dimensions.Builder().length(1).build());
        if (preferredLength == null) {
            throw new IllegalStateException("Simulation reports no preferred length unit");
        }
        double preferredConversion = finite(preferredLength.getConversion(),
                "preferred length unit conversion");
        context.put("model_length_unit_readback", preferredLength.getSuffix());
        context.put("preferred_length_unit_conversion_to_m", Double.toString(preferredConversion));
        if (Math.abs(preferredConversion - scale) > 1e-12) {
            throw new IllegalStateException("Preferred length unit is not the CaseSpec model unit: expected "
                    + unitName + " (conversion " + scale + ") but the simulation reports "
                    + preferredLength.getSuffix() + " (conversion " + preferredConversion + ")");
        }

        // A genuine measurement of the tessellation STAR produced, taken before the state is saved
        // so that a failure here cannot leave a saved output that looks usable.
        mesh.exportStlPart(stl.getAbsolutePath(), true);
        Measurement measured = measure(stl, STL_EXPORT_UNIT_TO_M);
        if (measured.triangles != triangles) {
            throw new IllegalStateException("Exported tessellation has " + measured.triangles
                    + " triangles but the part reports " + triangles);
        }
        // The radial and centre tolerances are set by the storage precision of the coordinates that
        // were actually written, not by a single relative number: a sphere at [0,0,1.5] m stores its
        // vertices as float32 near 1.55 m, so a fixed 1e-6 * R tolerance would reject a correct body.
        double storageUlp = finite(Math.ulp((float) measured.maxAbsCoordinate), "float32 storage ulp");
        double radiusTolerance = finite(Math.max(MIN_VERTEX_RADIUS_RELATIVE_TOLERANCE * radiusM,
                STORAGE_ULP_MULTIPLIER * storageUlp), "vertex radius tolerance");
        double centreOffset = 0.0;
        for (int axis = 0; axis < 3; axis++) {
            centreOffset = Math.max(centreOffset, Math.abs(measured.centroid[axis] - originM[axis]));
        }
        finite(centreOffset, "measured centre offset");
        double centreTolerance = finite(Math.max(1e-12, STORAGE_ULP_MULTIPLIER * storageUlp),
                "centre tolerance");
        double vertexAbsError = Math.max(Math.abs(measured.minVertexRadius - radiusM),
                Math.abs(measured.maxVertexRadius - radiusM));
        finite(vertexAbsError, "vertex radius error");
        double vertexErrorRatio = vertexAbsError / radiusTolerance;
        double centreErrorRatio = centreOffset / centreTolerance;
        finite(vertexErrorRatio, "vertex radius error ratio");
        finite(centreErrorRatio, "centre offset ratio");
        double vertexRelativeError = finite(vertexAbsError / radiusM, "vertex radius relative error");
        double analyticVolume = 4.0 / 3.0 * Math.PI * radiusM * radiusM * radiusM;
        finite(analyticVolume, "analytic sphere volume");
        double volumeDeviation = Math.abs(measured.volume - analyticVolume) / analyticVolume;
        finite(volumeDeviation, "volume deviation");
        double bboxDeviation = Math.abs(measured.halfExtent[0] - radiusM) / radiusM;
        for (int axis = 1; axis < 3; axis++) {
            bboxDeviation = Math.max(bboxDeviation, Math.abs(measured.halfExtent[axis] - radiusM) / radiusM);
        }
        finite(bboxDeviation, "bounding box deviation");
        context.put("measured_vertex_radius_relative_error", Double.toString(vertexRelativeError));
        context.put("measured_vertex_radius_abs_error_m", Double.toString(vertexAbsError));
        context.put("measured_vertex_radius_tolerance_m", Double.toString(radiusTolerance));
        context.put("measured_vertex_radius_error_ratio", Double.toString(vertexErrorRatio));
        context.put("measured_centre_offset_m", Double.toString(centreOffset));
        context.put("measured_centre_tolerance_m", Double.toString(centreTolerance));
        context.put("measured_centre_offset_ratio", Double.toString(centreErrorRatio));
        context.put("measured_storage_ulp_m", Double.toString(storageUlp));
        context.put("measured_bbox_half_extent_relative_deviation", Double.toString(bboxDeviation));
        context.put("measured_volume_relative_deviation", Double.toString(volumeDeviation));
        if (vertexErrorRatio > 1.0) {
            throw new IllegalStateException("Measured vertex radius error " + vertexAbsError
                    + " m exceeds the storage-derived tolerance " + radiusTolerance + " m");
        }
        if (centreErrorRatio > 1.0) {
            throw new IllegalStateException("Measured centre offset " + centreOffset
                    + " m exceeds the storage-derived tolerance " + centreTolerance + " m");
        }
        if (bboxDeviation > MAX_GEOMETRIC_RELATIVE_DEVIATION) {
            throw new IllegalStateException("Measured bounding box deviates from the sphere radius by "
                    + bboxDeviation + " which exceeds " + MAX_GEOMETRIC_RELATIVE_DEVIATION);
        }
        if (volumeDeviation > MAX_GEOMETRIC_RELATIVE_DEVIATION) {
            throw new IllegalStateException("Measured enclosed volume deviates from 4/3*pi*R^3 by "
                    + volumeDeviation + " which exceeds " + MAX_GEOMETRIC_RELATIVE_DEVIATION);
        }

        // Every invariant that does not need the saved file is checked before saving, so an "ok"
        // record can never describe a state that violated one of them.
        if (sim.getRegionManager().getRegions().size() != 0) {
            throw new IllegalStateException("Body construction must not create regions");
        }
        if (sim.getSimulationIterator().getCurrentIteration() != 0) {
            throw new IllegalStateException("Body construction must not iterate");
        }
        if (parts.getParts().size() != 1) {
            throw new IllegalStateException("Body construction must leave exactly one part");
        }
        sim.saveState(output.getAbsolutePath());
        if (!output.isFile() || output.length() == 0L) {
            throw new IllegalStateException("Saved output simulation is missing or empty");
        }

        context.put("is_closed", "true");
        context.put("is_manifold", "true");
        context.put("triangle_count", Integer.toString(triangles));
        context.put("part_surfaces", join(surfaceNames));
        context.put("part_count", Integer.toString(parts.getParts().size()));
        context.put("regions", "0");
        context.put("iteration", "0");
        context.put("tessellation_stl", TESSELLATION);
        context.put("tessellation_stl_sha256", sha(stl));
        context.put("body_origin_spec_readback_m", "[" + readbackOrigin[0] + "," + readbackOrigin[1] + ","
                + readbackOrigin[2] + "]");
        context.put("body_radius_spec_readback_m", Double.toString(readbackRadius));
        context.put("origin_readback_max_abs_error_m", Double.toString(originError));
        context.put("radius_readback_abs_error_m", Double.toString(radiusError));
        context.put("measured_bbox_si_m", "[" + measured.bbox[0] + "," + measured.bbox[1] + ","
                + measured.bbox[2] + "," + measured.bbox[3] + "," + measured.bbox[4] + ","
                + measured.bbox[5] + "]");
        context.put("measured_bbox_half_extent_si_m", "[" + measured.halfExtent[0] + ","
                + measured.halfExtent[1] + "," + measured.halfExtent[2] + "]");
        context.put("measured_centroid_si_m", "[" + measured.centroid[0] + "," + measured.centroid[1]
                + "," + measured.centroid[2] + "]");
        context.put("measured_max_abs_coordinate_m", Double.toString(measured.maxAbsCoordinate));
        context.put("measured_min_vertex_radius_si_m", Double.toString(measured.minVertexRadius));
        context.put("measured_max_vertex_radius_si_m", Double.toString(measured.maxVertexRadius));
        context.put("measured_enclosed_volume_si_m3", Double.toString(measured.volume));
        context.put("analytic_volume_si_m3", Double.toString(analyticVolume));
        context.put("output_sha256", sha(output));
    }

    /** Measure the binary STL STAR wrote, in SI. Nothing here trusts a value this macro set.
     *
     *  A NaN or an infinity anywhere in the input would make every comparison below false and the
     *  run would silently pass, so every quantity is checked with isFinite before it is used or
     *  recorded. That is the whole point of the finite() calls: `if (x > tol) fail` does not fail
     *  for NaN.
     */
    private static Measurement measure(File stl, double scale) throws IOException {
        long length = stl.length();
        FileInputStream stream = new FileInputStream(stl);
        Measurement out = new Measurement();
        try {
            byte[] header = new byte[84];
            readFully(stream, header);
            long count = Integer.toUnsignedLong(ByteBuffer.wrap(header, 80, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).getInt());
            if (count <= 0 || length != 84L + count * 50L) {
                throw new IOException("Invalid or empty binary STL triangle stream (" + length
                        + " bytes, " + count + " declared triangles)");
            }
            double[] bounds = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                    Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY,
                    Double.NEGATIVE_INFINITY};
            double signedVolume = 0.0;
            double sumX = 0.0;
            double sumY = 0.0;
            double sumZ = 0.0;
            double maxAbs = 0.0;
            double[] vertices = new double[(int) count * 9];
            byte[] triangle = new byte[50];
            for (long i = 0; i < count; i++) {
                readFully(stream, triangle);
                ByteBuffer row = ByteBuffer.wrap(triangle).order(ByteOrder.LITTLE_ENDIAN);
                double[][] vertex = new double[3][3];
                for (int v = 0; v < 3; v++) {
                    for (int axis = 0; axis < 3; axis++) {
                        double value = row.getFloat(12 + v * 12 + axis * 4);
                        if (!Double.isFinite(value)) throw new IOException("Non-finite STL vertex");
                        vertex[v][axis] = value * scale;
                        finite(vertex[v][axis], "scaled STL vertex");
                        bounds[axis] = Math.min(bounds[axis], vertex[v][axis]);
                        bounds[axis + 3] = Math.max(bounds[axis + 3], vertex[v][axis]);
                        maxAbs = Math.max(maxAbs, Math.abs(vertex[v][axis]));
                    }
                    int base = (int) i * 9 + v * 3;
                    vertices[base] = vertex[v][0];
                    vertices[base + 1] = vertex[v][1];
                    vertices[base + 2] = vertex[v][2];
                    sumX += vertex[v][0];
                    sumY += vertex[v][1];
                    sumZ += vertex[v][2];
                }
                // Signed volume of the tetrahedron to the origin; the sum over a closed, outward
                // oriented surface is the enclosed volume.
                signedVolume += (vertex[0][0] * (vertex[1][1] * vertex[2][2] - vertex[1][2] * vertex[2][1])
                        - vertex[0][1] * (vertex[1][0] * vertex[2][2] - vertex[1][2] * vertex[2][0])
                        + vertex[0][2] * (vertex[1][0] * vertex[2][1] - vertex[1][1] * vertex[2][0])) / 6.0;
            }
            out.triangles = count;
            out.bbox = bounds;
            out.halfExtent = new double[] {(bounds[3] - bounds[0]) / 2.0, (bounds[4] - bounds[1]) / 2.0,
                    (bounds[5] - bounds[2]) / 2.0};
            out.maxAbsCoordinate = maxAbs;
            // Measure the radius about the tessellation's own centroid rather than about the value
            // this macro asked STAR to use. The centroid is a quantity of the geometry, so the
            // radial check no longer inherits an error from the requested origin.
            double vertexCount = (double) count * 3.0;
            out.centroid = new double[] {sumX / vertexCount, sumY / vertexCount, sumZ / vertexCount};
            for (int i = 0; i < 3; i++) finite(out.centroid[i], "measured centroid");
            double minRadius = Double.POSITIVE_INFINITY;
            double maxRadius = 0.0;
            for (int i = 0; i < count; i++) {
                int base = i * 9;
                for (int v = 0; v < 3; v++) {
                    double dx = vertices[base + v * 3] - out.centroid[0];
                    double dy = vertices[base + v * 3 + 1] - out.centroid[1];
                    double dz = vertices[base + v * 3 + 2] - out.centroid[2];
                    double radius = Math.sqrt(dx * dx + dy * dy + dz * dz);
                    finite(radius, "measured vertex radius");
                    minRadius = Math.min(minRadius, radius);
                    maxRadius = Math.max(maxRadius, radius);
                }
            }
            out.minVertexRadius = minRadius;
            out.maxVertexRadius = maxRadius;
            out.volume = Math.abs(signedVolume);
            finite(out.volume, "measured enclosed volume");
            return out;
        } finally {
            stream.close();
        }
    }

    /** Fail closed on a non-finite value before any comparison can silently be false for NaN. */
    private void importStep(Simulation sim, File dir, File output, String spec, String unitName,
            Map<String, String> context) throws Exception {
        SimulationPartManager parts = sim.get(SimulationPartManager.class);
        if (!parts.getParts().isEmpty() || !sim.getRegionManager().getRegions().isEmpty()
                || sim.getSimulationIterator().getCurrentIteration() != 0 || output.exists())
            throw new IllegalStateException("STEP import requires empty uniterated simulation and new output");
        String name = stringValue(spec, "body_part_name");
        String file = stringValue(spec, "geometry_step_file");
        if (!file.matches("[A-Za-z0-9_.-]+\\.(step|stp)")) throw new IllegalArgumentException("Invalid STEP filename");
        File source = new File(dir, file);
        context.put("geometry_step_sha256", sha(source));
        star.cadmodeler.CadModel model = sim.get(star.cadmodeler.SolidModelManager.class).createSolidModel();
        model.importCadFile(source.getAbsolutePath());
        if (model.getBodyManager().getBodies().size() != 1)
            throw new IllegalStateException("STEP model must contain one native CAD body");
        star.cadmodeler.Body body = model.getBodyManager().getBodies().iterator().next();
        if (!body.isBrepBody() || !body.isSolid() || body.isMeshBody())
            throw new IllegalStateException("Imported body is not a native solid BREP");
        context.put("cad_body_count", "1");
        context.put("cad_body_brep_solid", "true");
        context.put("cad_face_count", Integer.toString(body.getFaceManager().getObjects().size()));
        model.createParts();
        if (parts.getParts().size() != 1) throw new IllegalStateException("Expected one imported CAD part");
        GeometryPart imported = parts.getParts().iterator().next();
        context.put("part_class", imported.getClass().getName());
        // THE NATIVE CAD IMPORT PRODUCES A TREE, and the root is not the solid. On this 2502 install
        // `model.createParts()` yields a star.cadmodeler.SolidModelCompositePart ROOT whose LEAVES are the
        // real BREP solids (star.cadmodeler.SolidModelPart extends star.meshing.CadPart). The previous test
        // was `instanceof star.meshing.CadPart` ON THE ROOT, so it rejected a perfectly good import: round 23
        // measured body_count=1, brep solid, 4 faces, part_class star.cadmodeler.SolidModelCompositePart and
        // the error "STEP did not retain CadPart", with no source saved.
        //
        // Selection below is by TREE POSITION plus MEASURED CAPABILITY, and it never accepts a composite as
        // the body: every selected leaf must be a retained BREP CadPart that is also tessellatable, and the
        // number of leaves must equal the CaseSpec's geometry.body_parts. A composite holding two solids, or
        // one holding a non-BREP mesh body, is refused rather than passed.
        List<GeometryPart> solids = new ArrayList<GeometryPart>();
        if (imported instanceof star.common.CompositePart) {
            star.common.CompositePart composite = (star.common.CompositePart) imported;
            Collection<GeometryPart> leaves = composite.getLeafParts();
            if (leaves != null) solids.addAll(leaves);
            if (solids.isEmpty()) {
                // An unexploded composite can report no leaves while its children are already listed; for
                // an import root those children ARE the solids, so fall back instead of failing a valid
                // import. Still leaves-only: a composite child is refused below.
                Collection<GeometryPart> children = composite.getChildParts().getParts();
                if (children != null) solids.addAll(children);
                context.put("imported_leaves_came_from_children", "true");
            }
            context.put("imported_root_is_composite", "true");
            context.put("imported_composite_child_count",
                    Integer.toString(composite.getChildParts().getParts().size()));
        } else {
            solids.add(imported);
            context.put("imported_root_is_composite", "false");
        }
        context.put("imported_solid_count", Integer.toString(solids.size()));
        int declaredParts = arrayCount(spec, "body_parts");
        if (solids.size() != declaredParts)
            throw new IllegalStateException("Imported " + solids.size() + " solid leaf/leaves but the CaseSpec "
                    + "declares " + declaredParts + " body part(s)");
        List<MeshPart> tessellatable = new ArrayList<MeshPart>();
        for (GeometryPart solid : solids) {
            if (solid instanceof star.common.CompositePart)
                throw new IllegalStateException("A composite part cannot itself be the solid leaf");
            if (!(solid instanceof star.meshing.CadPart))
                throw new IllegalStateException("Imported solid leaf is not a retained BREP CadPart: "
                        + solid.getClass().getName());
            if (!(solid instanceof MeshPart))
                throw new IllegalStateException("Imported solid leaf cannot be tessellated: "
                        + solid.getClass().getName());
            MeshPart candidate = (MeshPart) solid;
            // Force the tessellation before reading it: a lazily tessellated CAD leaf must not be reported
            // as an empty/invalid tessellation (round 23 never reached this line, so it is not measured).
            ((star.meshing.CadPart) solid).tessellate();
            if (!candidate.isClosed() || !candidate.isManifold() || candidate.getTriangleCount() <= 0)
                throw new IllegalStateException("Imported CAD tessellation is not closed manifold nonempty");
            tessellatable.add(candidate);
        }
        GeometryPart part = solids.get(0);
        MeshPart mesh = tessellatable.get(0);
        // The body part carries the CaseSpec name so every downstream part lookup finds the SOLID; a
        // composite root, when STAR created one, keeps an explicit derived name instead of the body's.
        if (part != imported) imported.setPresentationName(name + "_CAD_ROOT");
        part.setPresentationName(name);
        context.put("selected_part_class", part.getClass().getName());
        Units before = sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
        if (before != null) before.setPreferred(false);
        sim.getUnitsManager().getObject(unitName).setPreferred(true);
        context.put("body_part_name", name);
        context.put("part_class", part.getClass().getName());
        context.put("cad_retained", "true");
        context.put("triangle_count", Integer.toString(mesh.getTriangleCount()));
        context.put("part_surfaces", Integer.toString(mesh.getPartSurfaces().size()));
        context.put("regions", "0"); context.put("iteration", "0");
        context.put("is_closed", "true"); context.put("is_manifold", "true");
        sim.saveState(output.getAbsolutePath());
        context.put("output_sim_sha256", sha(output));
        context.put("output_sim_exists", "true");
    }

    private static double finite(double value, String what) {
        if (!Double.isFinite(value)) {
            throw new IllegalStateException("Non-finite " + what + ": " + value);
        }
        return value;
    }

    private static void readFully(InputStream stream, byte[] target) throws IOException {
        int offset = 0;
        while (offset < target.length) {
            int read = stream.read(target, offset, target.length - offset);
            if (read < 0) throw new IOException("Unexpected end of STL stream");
            offset += read;
        }
    }

    /** The directory the worker put case_spec.json in: the session directory, else the CWD. */
    private static File locateDir(Simulation sim) {
        File session = null;
        try {
            session = sim.getSessionDirFile();
        } catch (Throwable ignored) {
            session = null;
        }
        File working = new File(".").getAbsoluteFile();
        for (File candidate : new File[] {session, working}) {
            if (candidate != null && new File(candidate, "case_spec.json").isFile()) return candidate;
        }
        return session != null ? session : working;
    }

    /** Best-effort failure record. It must always be written, even when nothing was parsed. */
    private static void writeFailure(File evidence, File dir, File output, Map<String, String> context,
            Throwable failure, String message) throws Exception {
        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"status\":\"error\"")
                .append(",\"usable\":false")
                .append(",\"working_directory\":\"").append(escape(dir.getAbsolutePath())).append('"')
                .append(",\"error_class\":\"").append(escape(failure.getClass().getName())).append('"')
                .append(",\"error\":\"").append(escape(message)).append('"')
                .append(",\"output_sim\":\"").append(OUTPUT_SIM).append('"')
                .append(",\"output_sim_exists\":").append(output.isFile());
        if (output.isFile()) {
            try {
                json.append(",\"output_sim_sha256\":\"").append(sha(output)).append('"');
            } catch (Throwable ignored) {
                json.append(",\"output_sim_sha256\":null");
            }
        } else {
            json.append(",\"output_sim_sha256\":null");
        }
        json.append(",\"case_spec_fields_read\":").append(map(context))
                .append(",\"complete_marker\":null}\n");
        try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
            writer.write(json.toString());
        }
    }

    private static void writeSuccess(File evidence, Map<String, String> context) throws Exception {
        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"status\":\"ok\"")
                .append(",\"usable\":true,\"complete_marker\":\"").append(COMPLETE).append('"');
        for (Map.Entry<String, String> entry : context.entrySet()) {
            json.append(",\"").append(escape(entry.getKey())).append("\":");
            json.append(render(entry.getKey(), entry.getValue()));
        }
        json.append("}\n");
        try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
            writer.write(json.toString());
        }
    }

    /** Arrays, booleans and numbers keep their JSON type; everything else is a string. */
    private static String render(String key, String value) {
        if ("cad_retained".equals(key) || "cad_body_brep_solid".equals(key)) return value;
        if ("cad_body_count".equals(key) || "cad_face_count".equals(key)) return value;
        if (key.endsWith("_m") || key.endsWith("_m3") || key.endsWith("_3") || key.endsWith("_error")
                || key.endsWith("_deviation") || key.endsWith("_ratio") || key.equals("triangle_count")
                || key.equals("part_count") || key.equals("regions") || key.equals("iteration")) {
            return value.startsWith("[") ? value : jsonNumber(value);
        }
        if (key.equals("is_closed") || key.equals("is_manifold")) return value;
        if (value.startsWith("[")) return value;
        return "\"" + escape(value) + "\"";
    }

    private static String jsonNumber(String value) {
        try {
            double parsed = Double.parseDouble(value);
            if (Double.isFinite(parsed)) return Double.toString(parsed);
        } catch (NumberFormatException ignored) {
            // fall through to a quoted string
        }
        return "\"" + escape(value) + "\"";
    }

    private static String map(Map<String, String> context) {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> entry : context.entrySet()) {
            if (!first) out.append(',');
            first = false;
            out.append('"').append(escape(entry.getKey())).append("\":\"")
                    .append(escape(entry.getValue())).append('"');
        }
        return out.append('}').toString();
    }

    private static String join(List<String> values) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) out.append(',');
            out.append('"').append(escape(values.get(i))).append('"');
        }
        return out.append(']').toString();
    }

    private static String escape(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }

    private static String describe(Throwable failure) {
        StringBuilder out = new StringBuilder();
        Throwable current = failure;
        while (current != null) {
            if (out.length() > 0) out.append(" <- ");
            out.append(current.getClass().getName()).append(": ").append(current.getMessage());
            current = current.getCause();
        }
        return out.toString();
    }

    static String sha(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        InputStream in = new FileInputStream(file);
        try {
            byte[] buffer = new byte[65536];
            int read;
            while ((read = in.read(buffer)) != -1) digest.update(buffer, 0, read);
        } finally {
            in.close();
        }
        StringBuilder hex = new StringBuilder();
        for (byte value : digest.digest()) hex.append(String.format("%02x", value));
        return hex.toString();
    }

    private static String stringValue(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing string " + key);
        return match.group(1);
    }

    private static String optionalString(String json, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        return match.find() ? match.group(1) : fallback;
    }

    private static double numberValue(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double[] arrayValue(String json, String key, int length) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing array " + key);
        String[] fields = match.group(1).split(",");
        if (fields.length != length) throw new IllegalArgumentException("Invalid length for " + key);
        double[] values = new double[length];
        for (int i = 0; i < length; i++) {
            values[i] = Double.parseDouble(fields[i].trim());
            if (!Double.isFinite(values[i])) throw new IllegalArgumentException("Non-finite " + key);
        }
        return values;
    }

    /** How many entries the named JSON string array declares (geometry.body_parts). */
    private static int arrayCount(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing array " + key);
        String body = match.group(1).trim();
        return body.isEmpty() ? 0 : body.split(",").length;
    }

    private static double unitToMeters(String name) {
        if ("m".equals(name)) return 1.0;
        if ("mm".equals(name)) return 0.001;
        if ("cm".equals(name)) return 0.01;
        if ("in".equals(name)) return 0.0254;
        throw new IllegalArgumentException("Unsupported length unit " + name);
    }
}
