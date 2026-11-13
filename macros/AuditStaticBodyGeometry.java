/* Read-only audit of one static-body part against a CaseSpec.
 *
 * The building macro creates the geometry it then measures, so by construction the measured radius
 * always agrees with the requested one: a CaseSpec alone can never trip its tolerance gates. This
 * macro closes that gap. It changes nothing - it re-opens an existing simulation, finds the part the
 * CaseSpec names, exports its tessellation and applies the SAME gates to it. Its natural use is to
 * refuse a CaseSpec that does not describe the geometry it is paired with, which is exactly what
 * protects a pipeline from a mismatched input.
 *
 * It also serves as the independent re-measurement of a produced body: a different macro, a fresh
 * session, and the same reduction re-derived from the file.
 *
 * Fails closed and leaves its diagnosis behind: a failed gate writes static_body_audit.json with
 * status "error" and then throws.
 *
 * Inputs:  case_spec.json in the session directory or the working directory; the simulation itself.
 * Outputs: static_body_audit.json, static_body_tessellation.stl. Nothing is created or saved.
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
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.DoubleVector;
import star.common.Coordinate;
import star.common.Dimensions;
import star.common.GeometryPart;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.Units;
import star.meshing.MeshPart;
import star.meshing.SimpleSpherePart;

public class AuditStaticBodyGeometry extends StarMacro {
    private static final String COMPLETE = "STATIC_BODY_GEOMETRY_AUDITED";
    private static final String EVIDENCE = "static_body_audit.json";
    private static final String TESSELLATION = "static_body_tessellation.stl";
    private static final String DEFAULT_BODY_NAME = "BODY";
    private static final double MIN_VERTEX_RADIUS_RELATIVE_TOLERANCE = 1e-6;
    private static final double STORAGE_ULP_MULTIPLIER = 8.0;
    private static final double MAX_GEOMETRIC_RELATIVE_DEVIATION = 0.05;
    /** The unit STAR writes into an exported STL: the simulation's INTERNAL length unit (metres), NOT
     *  the model/display unit. Round 12 measured this twice on the installed 20.02.008 build - with
     *  model_length_unit = mm the preferred length unit read-back is mm (conversion 0.001) while the
     *  exported STL still measures 0.05 m. See the long note in macros/BuildStaticBodyGeometry.java and
     *  test-evidence/static-external-flow/round12-mm-geometry-b/. */
    private static final double STL_EXPORT_UNIT_TO_M = 1.0;

    @Override
    public void execute() {
        Map<String, String> context = new LinkedHashMap<String, String>();
        // "No active simulation" is itself a failure that must leave a diagnosis, so the try encloses
        // getActiveSimulation() and the evidence path is resolvable without a simulation. An earlier
        // version opened the try after these calls, which meant that failure wrote nothing.
        File dir = new File(".").getAbsoluteFile();
        File evidence = new File(dir, EVIDENCE);
        try {
            Simulation sim = getActiveSimulation();
            if (sim == null) throw new IllegalStateException("No active simulation");
            dir = locateDir(sim);
            evidence = new File(dir, EVIDENCE);
            run(sim, dir, context);
            writeEvidence(evidence, context, null);
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            String message = describe(failure);
            // The failure record must be machine-verifiable by the same checks the other stages use,
            // so it carries the failure class, whether an output was left behind, and where it was
            // written - not just a message.
            context.put("error_class", failure.getClass().getName());
            context.put("output_sim_exists", "false");
            context.put("working_directory", dir.getAbsolutePath());
            try {
                writeEvidence(evidence, context, message);
            } catch (Throwable secondary) {
                message = message + " | audit evidence could not be written: " + describe(secondary);
            }
            throw new RuntimeException("Static-body geometry audit failed: " + message, failure);
        }
    }

    private void run(Simulation sim, File dir, Map<String, String> context) throws Exception {
        File inputSim = new File(sim.getSessionPath());
        context.put("input_sim_sha256", sha(inputSim));
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
        if (!"sphere".equals(shape) && !"cylinder".equals(shape)) {
            throw new IllegalArgumentException("Unsupported body_shape for this release: " + shape);
        }
        String bodyName = optionalString(spec, "body_part_name", DEFAULT_BODY_NAME);
        context.put("body_part_name", bodyName);
        double[] originM = arrayValue(spec, "body_origin_m", 3);
        context.put("body_origin_m", "[" + originM[0] + "," + originM[1] + "," + originM[2] + "]");
        double radiusM = numberValue(spec, "body_radius_m");
        context.put("body_radius_m", Double.toString(radiusM));
        if (!(radiusM > 0.0)) throw new IllegalArgumentException("body_radius_m must be positive");

        boolean cylinder = "cylinder".equals(shape);
        double lengthM = cylinder ? numberValue(spec, "body_length_m") : 0.0;
        if (cylinder) {
            double[] axis = arrayValue(spec, "body_axis", 3);
            if (axis[0] != 0.0 || axis[1] != 0.0 || axis[2] != 1.0 || !(lengthM > 0.0))
                throw new IllegalArgumentException("Cylinder audit currently supports declared +Z axis and positive length only");
            context.put("body_length_m", Double.toString(lengthM));
            context.put("body_axis", "[0,0,1]");
        }
        File stl = new File(dir, TESSELLATION);
        if (stl.exists()) throw new IllegalStateException("Tessellation export target already exists");
        SimulationPartManager parts = sim.get(SimulationPartManager.class);
        GeometryPart part = locatePart(parts, bodyName);
        if (part == null) {
            throw new IllegalStateException("The simulation has no part named " + bodyName
                    + " anywhere in its part tree; the tree contains " + treeNames(parts));
        }
        // THE AUDIT RESOLVES THE SAME TREE THE IMPORT PRODUCED. A native CAD import on this 2502 install
        // yields a star.cadmodeler.SolidModelCompositePart ROOT (a star.common.CompositePart, NOT a
        // MeshPart) whose LEAVES are the real BREP solids (star.cadmodeler.SolidModelPart extends
        // star.meshing.CadPart). Requiring the NAMED part itself to be a MeshPart/CadPart therefore refused
        // valid CAD imports (round 23). The named part may be either the solid or a composite root; the
        // resolution below never accepts a composite as the audited geometry, it requires the leaf count to
        // equal the CaseSpec's geometry.body_parts, and it re-checks each leaf's capability.
        java.util.List<GeometryPart> solids = new java.util.ArrayList<GeometryPart>();
        if (part instanceof star.common.CompositePart) {
            java.util.Collection<GeometryPart> leaves =
                    ((star.common.CompositePart) part).getLeafParts();
            if (leaves != null) solids.addAll(leaves);
            if (solids.isEmpty()) {
                // see BuildStaticBodyGeometry: an unexploded composite may list its children only
                java.util.Collection<GeometryPart> children =
                        ((star.common.CompositePart) part).getChildParts().getParts();
                if (children != null) solids.addAll(children);
                context.put("audited_leaves_came_from_children", "true");
            }
            context.put("audited_root_is_composite", "true");
        } else {
            solids.add(part);
            context.put("audited_root_is_composite", "false");
        }
        context.put("audited_solid_count", Integer.toString(solids.size()));
        int declaredParts = arrayCount(spec, "body_parts");
        if (solids.size() != declaredParts)
            throw new IllegalStateException("Named part resolves to " + solids.size() + " solid leaf/leaves "
                    + "but the CaseSpec declares " + declaredParts + " body part(s)");
        for (GeometryPart solid : solids) {
            if (solid instanceof star.common.CompositePart)
                throw new IllegalStateException("A composite part cannot itself be the audited solid");
            if (!(solid instanceof MeshPart))
                throw new IllegalStateException("Audited solid leaf is not a mesh part: "
                        + solid.getClass().getName());
            MeshPart candidate = (MeshPart) solid;
            if (solid instanceof star.meshing.CadPart) ((star.meshing.CadPart) solid).tessellate();
            if (!candidate.isClosed() || !candidate.isManifold() || candidate.getTriangleCount() <= 0)
                throw new IllegalStateException("Audited solid leaf is not a closed manifold tessellation");
            if (cylinder && !(solid instanceof star.meshing.CadPart))
                throw new IllegalStateException("Cylinder STEP acceptance requires a retained BREP CadPart "
                        + "leaf, not " + solid.getClass().getName());
        }
        GeometryPart selected = solids.get(0);
        MeshPart mesh = (MeshPart) selected;
        context.put("part_class", selected.getClass().getName());
        context.put("cad_retained", Boolean.toString(selected instanceof star.meshing.CadPart));
        if (cylinder) {
            int bodies = 0, faces = 0;
            star.cadmodeler.SolidModelManager cadManager = sim.get(star.cadmodeler.SolidModelManager.class);
            for (star.common.CadModelBase base : cadManager.getObjects()) {
                if (!(base instanceof star.cadmodeler.CadModel)) throw new IllegalStateException("Unexpected CAD model type");
                star.cadmodeler.CadModel model = (star.cadmodeler.CadModel) base;
                cadManager.editCadModel(model);
                try {
                    for (star.cadmodeler.Body body : model.getBodyManager().getBodies()) {
                        if (!body.isBrepBody() || !body.isSolid() || body.isMeshBody())
                            throw new IllegalStateException("Saved CAD body is not solid BREP");
                        bodies++; faces += body.getFaceManager().getObjects().size();
                    }
                } finally {
                    cadManager.endEditCadModel(model);
                }
            }
            context.put("cad_body_count", Integer.toString(bodies));
            context.put("cad_face_count", Integer.toString(faces));
            if (bodies != 1) throw new IllegalStateException("Saved STEP state needs one native CAD body");
        }
        if (!mesh.isClosed()) throw new IllegalStateException("Audited part is not closed");
        if (!mesh.isManifold()) throw new IllegalStateException("Audited part is not manifold");
        int triangles = mesh.getTriangleCount();
        if (triangles <= 0) throw new IllegalStateException("Audited part has no triangles");
        context.put("triangle_count", Integer.toString(triangles));
        context.put("part_surfaces", Integer.toString(mesh.getPartSurfaces().size()));
        context.put("regions", Integer.toString(sim.getRegionManager().getRegions().size()));
        context.put("iteration", Integer.toString(sim.getSimulationIterator().getCurrentIteration()));
        // ROUND 12: the re-open proof of the SAVED simulation's model unit. This macro's whole purpose is
        // to measure the saved .sim in a fresh session instead of trusting the builder, so the model unit
        // is read back HERE too and required to match the CaseSpec. Combined with the STL measurement
        // below (which is in the internal SI unit), the audit record shows both halves: the file really
        // carries the CaseSpec unit, and the geometry really has the CaseSpec SI size.
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
            throw new IllegalStateException("Saved simulation model length unit is not the CaseSpec unit: "
                    + "expected " + unitName + " (conversion " + scale + ") but the simulation reports "
                    + preferredLength.getSuffix() + " (conversion " + preferredConversion + ")");
        }

        mesh.exportStlPart(stl.getAbsolutePath(), true);
        double[] bounds = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
        double[] sum = new double[3];
        double maxAbs = 0.0;
        double signedVolume = 0.0;
        InputStream stream = new FileInputStream(stl);
        try {
            byte[] header = new byte[84];
            readFully(stream, header);
            long count = Integer.toUnsignedLong(ByteBuffer.wrap(header, 80, 4)
                    .order(ByteOrder.LITTLE_ENDIAN).getInt());
            if (count <= 0 || stl.length() != 84L + count * 50L) {
                throw new IOException("Invalid or empty binary STL triangle stream");
            }
            if (count != triangles) throw new IOException("Exported tessellation has " + count
                    + " triangles but the part reports " + triangles);
            double[] vertices = new double[(int) count * 9];
            byte[] triangle = new byte[50];
            for (long i = 0; i < count; i++) {
                readFully(stream, triangle);
                ByteBuffer row = ByteBuffer.wrap(triangle).order(ByteOrder.LITTLE_ENDIAN);
                double[][] vertex = new double[3][3];
                for (int v = 0; v < 3; v++) {
                    for (int axis = 0; axis < 3; axis++) {
                        double raw = row.getFloat(12 + v * 12 + axis * 4);
                        if (!Double.isFinite(raw)) throw new IOException("Non-finite STL vertex");
                        vertex[v][axis] = finite(raw * STL_EXPORT_UNIT_TO_M, "scaled STL vertex");
                        bounds[axis] = Math.min(bounds[axis], vertex[v][axis]);
                        bounds[axis + 3] = Math.max(bounds[axis + 3], vertex[v][axis]);
                        maxAbs = Math.max(maxAbs, Math.abs(vertex[v][axis]));
                    }
                    int base = (int) i * 9 + v * 3;
                    for (int axis = 0; axis < 3; axis++) {
                        vertices[base + axis] = vertex[v][axis];
                        sum[axis] += vertex[v][axis];
                    }
                }
                signedVolume += (vertex[0][0] * (vertex[1][1] * vertex[2][2] - vertex[1][2] * vertex[2][1])
                        - vertex[0][1] * (vertex[1][0] * vertex[2][2] - vertex[1][2] * vertex[2][0])
                        + vertex[0][2] * (vertex[1][0] * vertex[2][1] - vertex[1][1] * vertex[2][0])) / 6.0;
            }
            double vertexCount = (double) count * 3.0;
            double[] centroid = {sum[0] / vertexCount, sum[1] / vertexCount, sum[2] / vertexCount};
            for (int axis = 0; axis < 3; axis++) centroid[axis] = finite(centroid[axis], "measured centroid");
            double minRadius = Double.POSITIVE_INFINITY;
            double maxRadius = 0.0;
            for (int i = 0; i < count; i++) {
                int base = i * 9;
                for (int v = 0; v < 3; v++) {
                    double dx = vertices[base + v * 3] - centroid[0];
                    double dy = vertices[base + v * 3 + 1] - centroid[1];
                    double dz = vertices[base + v * 3 + 2] - centroid[2];
                    double radius = finite(Math.sqrt(dx * dx + dy * dy + dz * dz), "measured vertex radius");
                    minRadius = Math.min(minRadius, radius);
                    maxRadius = Math.max(maxRadius, radius);
                }
            }
            if (cylinder) {
                // A tessellated cap has a different vertex distribution: use measured bbox midpoint,
                // not area-unweighted vertex average, for this +Z cylinder's geometric centre.
                double[] circle = cylinderCircle(vertices, bounds);
                centroid[0] = circle[0]; centroid[1] = circle[1];
                centroid[2] = (bounds[2] + bounds[5]) / 2.0;
                context.put("measured_cylinder_fitted_radius_m", Double.toString(circle[2]));
            }
            double volume = finite(Math.abs(signedVolume), "measured enclosed volume");
            double storageUlp = finite(Math.ulp((float) maxAbs), "float32 storage ulp");
            double radiusTolerance = finite(Math.max(MIN_VERTEX_RADIUS_RELATIVE_TOLERANCE * radiusM,
                    STORAGE_ULP_MULTIPLIER * storageUlp), "radius tolerance");
            double centreTolerance = finite(Math.max(1e-12, STORAGE_ULP_MULTIPLIER * storageUlp),
                    "centre tolerance");
            double centreOffset = 0.0;
            for (int axis = 0; axis < 3; axis++) {
                centreOffset = Math.max(centreOffset, Math.abs(centroid[axis] - originM[axis]));
            }
            centreOffset = finite(centreOffset, "measured centre offset");
            double vertexAbsError = Math.max(Math.abs(minRadius - radiusM), Math.abs(maxRadius - radiusM));
            if (cylinder) {
                vertexAbsError = 0.0;
                for (int k = 0; k < vertices.length; k += 3) {
                    double dx = vertices[k] - originM[0], dy = vertices[k+1] - originM[1];
                    double z = Math.abs(vertices[k+2] - originM[2]);
                    double radial = Math.sqrt(dx*dx + dy*dy);
                    // Closed cylinder surface: side OR cap, with both radial and axial bounds.
                    double error = Math.max(Math.max(0.0, radial-radiusM), Math.max(0.0, z-lengthM/2.0));
                    error = Math.max(error, Math.min(Math.abs(radial-radiusM), Math.abs(z-lengthM/2.0)));
                    vertexAbsError = Math.max(vertexAbsError, error);
                }
                // Explicit dimensional readback, independent of the supplied radius/length.
                double radialX = (bounds[3]-bounds[0])/2.0, radialY = (bounds[4]-bounds[1])/2.0;
                double axialLength = bounds[5]-bounds[2];
                context.put("measured_cylinder_radius_x_m", Double.toString(radialX));
                context.put("measured_cylinder_radius_y_m", Double.toString(radialY));
                context.put("measured_cylinder_length_m", Double.toString(axialLength));
                vertexAbsError = Math.max(vertexAbsError, Math.abs(Double.parseDouble(context.get("measured_cylinder_fitted_radius_m"))-radiusM));
                vertexAbsError = Math.max(vertexAbsError, Math.abs(axialLength-lengthM)/2.0);
            }
            double vertexErrorRatio = finite(finite(vertexAbsError, "vertex radius error") / radiusTolerance,
                    "vertex radius error ratio");
            double centreErrorRatio = finite(centreOffset / centreTolerance, "centre offset ratio");
            double analyticVolume = finite(cylinder ? Math.PI * radiusM * radiusM * lengthM : 4.0 / 3.0 * Math.PI * radiusM * radiusM * radiusM,
                    "analytic sphere volume");
            double volumeDeviation = finite(Math.abs(volume - analyticVolume) / analyticVolume,
                    "volume deviation");
            double bboxDeviation = 0.0;
            for (int axis = 0; axis < 3; axis++) {
                double half = (bounds[axis + 3] - bounds[axis]) / 2.0;
                bboxDeviation = Math.max(bboxDeviation, Math.abs(half - (cylinder && axis == 2 ? lengthM/2.0 : radiusM)) / (cylinder && axis == 2 ? lengthM/2.0 : radiusM));
            }
            bboxDeviation = finite(bboxDeviation, "bounding box deviation");
            context.put("measured_centroid_si_m", "[" + centroid[0] + "," + centroid[1] + "," + centroid[2] + "]");
            context.put("measured_max_abs_coordinate_m", Double.toString(finite(maxAbs, "max coordinate")));
            context.put("measured_storage_ulp_m", Double.toString(storageUlp));
            context.put("measured_vertex_radius_tolerance_m", Double.toString(radiusTolerance));
            context.put("measured_centre_tolerance_m", Double.toString(centreTolerance));
            context.put("measured_vertex_radius_abs_error_m", Double.toString(vertexAbsError));
            context.put("measured_vertex_radius_relative_error", Double.toString(finite(vertexAbsError / radiusM, "relative error")));
            context.put("measured_vertex_radius_error_ratio", Double.toString(vertexErrorRatio));
            context.put("measured_centre_offset_m", Double.toString(centreOffset));
            context.put("measured_centre_offset_ratio", Double.toString(centreErrorRatio));
            context.put("measured_min_vertex_radius_si_m", Double.toString(minRadius));
            context.put("measured_max_vertex_radius_si_m", Double.toString(maxRadius));
            context.put("measured_bbox_si_m", "[" + bounds[0] + "," + bounds[1] + "," + bounds[2] + ","
                    + bounds[3] + "," + bounds[4] + "," + bounds[5] + "]");
            context.put("measured_bbox_half_extent_relative_deviation", Double.toString(bboxDeviation));
            context.put("measured_enclosed_volume_si_m3", Double.toString(volume));
            context.put("analytic_volume_si_m3", Double.toString(analyticVolume));
            context.put("measured_volume_relative_deviation", Double.toString(volumeDeviation));
            context.put("tessellation_stl_sha256", sha(stl));
            if (vertexErrorRatio > 1.0) {
                throw new IllegalStateException("The audited body's surface/dimensions do not match the CaseSpec: "
                        + vertexAbsError + " m error against a storage tolerance of " + radiusTolerance + " m");
            }
            if (centreErrorRatio > 1.0) {
                throw new IllegalStateException("The audited body is not where the CaseSpec says: centre offset "
                        + centreOffset + " m against a storage tolerance of " + centreTolerance + " m");
            }
            if (bboxDeviation > MAX_GEOMETRIC_RELATIVE_DEVIATION) {
                throw new IllegalStateException("Audited bounding box deviates from the CaseSpec radius by "
                        + bboxDeviation);
            }
            if (volumeDeviation > MAX_GEOMETRIC_RELATIVE_DEVIATION) {
                throw new IllegalStateException("Audited enclosed volume deviates from 4/3*pi*R^3 by "
                        + volumeDeviation);
            }
        } finally {
            stream.close();
        }
        context.put("is_closed", "true");
        context.put("is_manifold", "true");
    }

    private static double[] cylinderCircle(double[] vertices, double[] bounds) {
        // Three extremal noncollinear rim vertices, selected from measured geometry, not declared R.
        int a=0, b=0, c=0; double best=-1;
        double mx=(bounds[0]+bounds[3])/2, my=(bounds[1]+bounds[4])/2;
        for(int k=0;k<vertices.length;k+=3){double d=Math.hypot(vertices[k]-mx,vertices[k+1]-my); if(d>best){best=d;a=k;}}
        best=-1;
        for(int k=0;k<vertices.length;k+=3){double d=Math.hypot(vertices[k]-vertices[a],vertices[k+1]-vertices[a+1]);if(d>best){best=d;b=k;}}
        best=-1;
        for(int k=0;k<vertices.length;k+=3){double d=Math.abs((vertices[b]-vertices[a])*(vertices[k+1]-vertices[a+1])-(vertices[b+1]-vertices[a+1])*(vertices[k]-vertices[a]));if(d>best){best=d;c=k;}}
        double ax=vertices[a], ay=vertices[a+1], bx=vertices[b]-ax, by=vertices[b+1]-ay, cx=vertices[c]-ax, cy=vertices[c+1]-ay;
        double den=2*(bx*cy-by*cx);
        if(Math.abs(den)<1e-20) throw new IllegalStateException("Cannot fit noncollinear cylinder rim");
        double u=((bx*bx+by*by)*cy-(cx*cx+cy*cy)*by)/den;
        double v=(bx*(cx*cx+cy*cy)-cx*(bx*bx+by*by))/den;
        return new double[]{finite(ax+u,"fitted circle x"),finite(ay+v,"fitted circle y"),finite(Math.hypot(u,v),"fitted circle radius")};
    }

    private static double finite(double value, String what) {
        if (!Double.isFinite(value)) throw new IllegalStateException("Non-finite " + what + ": " + value);
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

    private static void writeEvidence(File evidence, Map<String, String> context, String error)
            throws Exception {
        StringBuilder json = new StringBuilder();
        json.append("{\"schema_version\":1,\"status\":\"").append(error == null ? "ok" : "error")
                .append("\",\"usable\":").append(error == null).append(",\"complete_marker\":")
                .append(error == null ? "\"" + COMPLETE + "\"" : "null");
        for (Map.Entry<String, String> entry : context.entrySet()) {
            json.append(",\"").append(escape(entry.getKey())).append("\":");
            json.append(render(entry.getKey(), entry.getValue()));
        }
        if (error != null) json.append(",\"error\":\"").append(escape(error)).append('"');
        json.append("}\n");
        try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
            writer.write(json.toString());
        }
    }

    private static String render(String key, String value) {
        if (key.endsWith("_m") || key.endsWith("_m3") || key.endsWith("_error") || key.endsWith("_deviation")
                || key.endsWith("_ratio") || key.equals("triangle_count") || key.equals("part_surfaces")
                || key.equals("regions") || key.equals("iteration") || key.equals("cad_body_count") || key.equals("cad_face_count")) {
            if (!value.startsWith("[")) {
                try {
                    double parsed = Double.parseDouble(value);
                    if (Double.isFinite(parsed)) return Double.toString(parsed);
                } catch (NumberFormatException ignored) {
                    // fall through to a quoted string
                }
            }
        }
        if (key.equals("is_closed") || key.equals("is_manifold") || key.equals("output_sim_exists") || key.equals("cad_retained")) {
            return value;
        }
        if (value.startsWith("[")) return value;
        return "\"" + escape(value) + "\"";
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

    /** Find the DECLARED part by scanning the actual part tree; never by getPart(String).
     *
     * Round 24 measured this on the delivered cylinder source .sim (b8c9a082...): the repaired CAD import
     * keeps the CaseSpec body name on a NESTED solid leaf (`INDEPENDENT_CYLINDER`, class
     * star.cadmodeler.SolidModelPart) under a composite root named `INDEPENDENT_CYLINDER_CAD_ROOT`, while
     * `SimulationPartManager.getPart(String)` searches TOP-LEVEL parts only and THROWS
     * star.base.neo.NeoException for an absent name. That lookup could therefore neither find the body nor be
     * probed for absence. This walks the whole tree - every top-level part, then for each composite its leaves
     * and its children, breadth first - and returns null only after the whole tree has been searched. Nothing
     * is caught or swallowed, so a genuine API error still surfaces.
     */
    private static GeometryPart locatePart(SimulationPartManager parts, String name) {
        java.util.List<GeometryPart> queue = new java.util.ArrayList<GeometryPart>();
        java.util.List<GeometryPart> visited = new java.util.ArrayList<GeometryPart>();
        for (GeometryPart top : parts.getParts()) queue.add(top);
        for (int index = 0; index < queue.size(); index++) {
            GeometryPart candidate = queue.get(index);
            boolean alreadySeen = false;
            for (GeometryPart previous : visited) {
                if (previous == candidate) { alreadySeen = true; break; }
            }
            if (alreadySeen) continue;
            visited.add(candidate);
            if (name.equals(candidate.getPresentationName())) return candidate;
            if (candidate instanceof star.common.CompositePart) {
                star.common.CompositePart composite = (star.common.CompositePart) candidate;
                java.util.Collection<GeometryPart> leaves = composite.getLeafParts();
                if (leaves != null) for (GeometryPart leaf : leaves) queue.add(leaf);
                java.util.Collection<GeometryPart> children = composite.getChildParts().getParts();
                if (children != null) for (GeometryPart child : children) queue.add(child);
            }
        }
        return null;
    }

    /** The names this tree actually exposes, so the failure message names reality instead of guessing. */
    private static String treeNames(SimulationPartManager parts) {
        java.util.List<String> names = new java.util.ArrayList<String>();
        java.util.List<GeometryPart> queue = new java.util.ArrayList<GeometryPart>();
        java.util.List<GeometryPart> visited = new java.util.ArrayList<GeometryPart>();
        for (GeometryPart top : parts.getParts()) queue.add(top);
        for (int index = 0; index < queue.size(); index++) {
            GeometryPart candidate = queue.get(index);
            boolean alreadySeen = false;
            for (GeometryPart previous : visited) {
                if (previous == candidate) { alreadySeen = true; break; }
            }
            if (alreadySeen) continue;
            visited.add(candidate);
            String label = candidate.getPresentationName();
            if (!names.contains(label)) names.add(label);
            if (candidate instanceof star.common.CompositePart) {
                star.common.CompositePart composite = (star.common.CompositePart) candidate;
                java.util.Collection<GeometryPart> leaves = composite.getLeafParts();
                if (leaves != null) for (GeometryPart leaf : leaves) queue.add(leaf);
                java.util.Collection<GeometryPart> children = composite.getChildParts().getParts();
                if (children != null) for (GeometryPart child : children) queue.add(child);
            }
        }
        return names.toString();
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
