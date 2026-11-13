/* AIR-3B block geometry import probe (scoped, non-production).
 *
 * Purpose: establish, on the installed STAR-CCM+ 2502 build, whether
 *   (1) PartImportManager.importCadParts accepts the AIR-3B block STEP,
 *   (2) the two closed fluid blocks arrive as separate GeometryParts,
 *   (3) each block arrives with multiple named PartSurfaces (the "blocks" that
 *       the Siemens tutorial relies on for per-surface mesh control),
 *   (4) which splitting/renaming APIs exist before meshing.
 *
 * It imports geometry only: no region, interface, mesh, physics, initialization
 * or iteration. Output is a TSV inventory plus the saved geometry sim.
 */
import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import star.cadmodeler.*;
import star.common.*;
import star.meshing.*;

public class ImportAir3bBlockGeometry extends StarMacro {

    private static String sha(File f) throws Exception {
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(f)) {
            byte[] b = new byte[65536]; int n;
            while ((n = in.read(b)) != -1) d.update(b, 0, n);
        }
        return HexFormat.of().formatHex(d.digest());
    }

    private static int totalSurfaces(SimulationPartManager parts) {
        int total = 0;
        for (GeometryPart p : parts.getParts()) total += p.getPartSurfaces().size();
        return total;
    }

    private static void dumpMethods(PrintWriter out, String label, Class<?> c) {
        if (c == null) { out.println("API\t" + label + "\t<class-missing>"); return; }
        TreeSet<String> names = new TreeSet<>();
        for (Method m : c.getMethods()) {
            String n = m.getName();
            if (n.contains("plit") || n.contains("urface") || n.contains("ame")
                    || n.contains("eature") || n.contains("olume") || n.contains("Boundar")) {
                StringBuilder sb = new StringBuilder(n).append('(');
                Class<?>[] ps = m.getParameterTypes();
                for (int i = 0; i < ps.length; i++) {
                    if (i > 0) sb.append(',');
                    sb.append(ps[i].getSimpleName());
                }
                names.add(sb.append(')').toString());
            }
        }
        for (String s : names) out.println("API\t" + label + "\t" + s);
    }

    private static File requiredFile(File dir, String nameVar, String hashVar) throws Exception {
        String name = System.getenv(nameVar);
        String digest = System.getenv(hashVar);
        if (name == null || !name.matches("[A-Za-z0-9_.-]{1,64}")
                || digest == null || !digest.matches("[a-f0-9]{64}"))
            throw new IllegalStateException("Missing or invalid " + nameVar + "/" + hashVar);
        File file = new File(dir, name);
        if (!sha(file).equals(digest)) throw new IllegalStateException(name + " hash mismatch");
        return file;
    }

    @Override public void execute() {
        Simulation s = getActiveSimulation();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            // The mode is read FIRST (②R10): it decides which registered inputs this stage requires
            // and how the input is identified in the certificate.
            boolean auditMode = "audit".equalsIgnoreCase(
                    System.getenv("AIR3B_GEOMETRY_MODE") == null ? "" : System.getenv("AIR3B_GEOMETRY_MODE"));
            // The CAD identity is asserted in BOTH modes. The STEP files stay registered, hash-pinned
            // inputs even when the macro is forbidden to import them, so "the geometry audited here is
            // the geometry whose CAD provenance was reviewed" is a machine check, not a story.
            File rotor = requiredFile(dir, "AIR3B_ROTOR_STEP", "AIR3B_ROTOR_SHA256");
            File stator = requiredFile(dir, "AIR3B_STATOR_STEP", "AIR3B_STATOR_SHA256");
            String geometrySimName = System.getenv("AIR3B_GEOMETRY_SIM");
            if (geometrySimName == null || geometrySimName.isEmpty()) geometrySimName = "block_geometry.sim";
            if (!geometrySimName.matches("[A-Za-z0-9_.-]{1,64}"))
                throw new IllegalStateException("Invalid AIR3B_GEOMETRY_SIM name");
            File out = new File(dir, geometrySimName);
            SimulationPartManager parts = s.get(SimulationPartManager.class);

            // TWO ENTRY MODES (②R9), because the formal chain must be able to START from a
            // geometry-only .sim instead of from the CAD files.
            //
            //   AIR3B_GEOMETRY_MODE=audit  the runner loaded a geometry-only .sim that is a REGISTERED,
            //                              hash-pinned pipeline input. The parts are therefore already
            //                              present: this macro must NOT import and must NOT save a new
            //                              checkpoint. It only audits what is loaded.
            //   (default)                  the legacy import path: a new empty simulation plus the two
            //                              hash-verified STEP files, exactly as before.
            //
            // The mode is an explicit environment switch rather than a guess from "does the output file
            // exist", so a stale file cannot silently change which path runs. Both modes end at the same
            // hard gates below (two closed manifold tessellated parts, zero regions, zero interfaces,
            // iteration zero), so the audit path cannot certify a state the import path would reject.
            int importCalls = 0;
            String inputPin = System.getenv("AIR3B_GEOMETRY_SIM_SHA256");
            if (auditMode) {
                // The registered geometry-only .sim must be named by a 64-hex content pin. Without the
                // pin the audit would only say "some geometry was loaded", which is not a lineage.
                if (inputPin == null || !inputPin.matches("[a-f0-9]{64}"))
                    throw new IllegalStateException("audit mode requires AIR3B_GEOMETRY_SIM_SHA256, the sha256"
                            + " of the registered geometry-only .sim; without it the audited lineage is unnamed");
                if (parts.getParts().isEmpty())
                    throw new IllegalStateException("audit mode requires a geometry-only .sim whose parts are"
                            + " already loaded; refusing to import (that would be the discarded-checkpoint path"
                            + " this mode exists to replace)");
                if (!out.exists() || out.length() <= 0)
                    throw new IllegalStateException("audit mode requires the registered input " + out.getName()
                            + " to be present in the job directory");
                if (!s.getRegionManager().getRegions().isEmpty()
                        || !s.getInterfaceManager().getObjects().isEmpty()
                        || s.getSimulationIterator().getCurrentIteration() != 0)
                    throw new IllegalStateException("audit mode requires a geometry-only state: regions="
                            + s.getRegionManager().getRegions().size() + " interfaces="
                            + s.getInterfaceManager().getObjects().size() + " iteration="
                            + s.getSimulationIterator().getCurrentIteration());
            } else {
                if (!parts.getParts().isEmpty() || !s.getRegionManager().getRegions().isEmpty()
                        || s.getSimulationIterator().getCurrentIteration() != 0 || out.exists())
                    throw new IllegalStateException("Requires new empty simulation and unused output");
                // STAR 2502 merges the bodies of one multi-body STEP into a single part, so the
                // two blocks are imported from two files in two calls; that is deterministic and
                // needs no assumption about assembly handling.
                s.get(PartImportManager.class).importCadParts(
                        Collections.singletonList(rotor.getAbsolutePath()), "default", 30.0, 1,
                        false, 1.0e-5, false, false, false, false);
                s.get(PartImportManager.class).importCadParts(
                        Collections.singletonList(stator.getAbsolutePath()), "default", 30.0, 1,
                        false, 1.0e-5, false, false, false, false);
                importCalls = 2;
            }

            // Hard gates: the geometry must arrive as exactly two closed, manifold,
            // non-empty tessellated blocks. Recording a bad state is not enough, because
            // a merged single part would still carry a completion marker.
            if (parts.getParts().size() != 2)
                throw new IllegalStateException("Expected exactly two fluid blocks, got "
                        + parts.getParts().size() + "; a merged or split import is unusable");
            for (GeometryPart p : parts.getParts()) {
                if (!(p instanceof MeshPart))
                    throw new IllegalStateException("Block is not a tessellated part: " + p.getPresentationName());
                MeshPart mesh = (MeshPart) p;
                if (!mesh.isClosed() || !mesh.isManifold() || mesh.getTriangleCount() <= 0)
                    throw new IllegalStateException("Block topology failed: " + p.getPresentationName()
                            + " closed=" + mesh.isClosed() + " manifold=" + mesh.isManifold()
                            + " triangles=" + mesh.getTriangleCount());
                if (p.getPartSurfaces().isEmpty())
                    throw new IllegalStateException("Block has no part surfaces: " + p.getPresentationName());
            }

            // Input IDENTITY gate (②R10), applied to both modes: the loaded/imported blocks must be the
            // reviewed ones by NAME. The geometry-only entry is bound to R8's checkpoint by a content
            // address (AIR3B_GEOMETRY_SIM_SHA256, verified byte for byte by the controller and then again
            // by the Windows runner), and the part names are a second, in-session measurement of the same
            // identity. Names here are read from the loaded state, never assumed.
            String expectedNames = System.getenv("AIR3B_GEOMETRY_EXPECTED_PART_NAMES");
            if (expectedNames != null && !expectedNames.isEmpty()) {
                TreeSet<String> want = new TreeSet<String>(Arrays.asList(expectedNames.split(",")));
                TreeSet<String> have = new TreeSet<String>();
                for (GeometryPart p : parts.getParts()) have.add(p.getPresentationName());
                if (!want.equals(have))
                    throw new IllegalStateException("Loaded parts are not the reviewed ones: expected "
                            + want + " but the session holds " + have);
            }

            if (!sha(rotor).equals(System.getenv("AIR3B_ROTOR_SHA256"))
                    || !sha(stator).equals(System.getenv("AIR3B_STATOR_SHA256")))
                throw new IllegalStateException("Input changed");
            // The audit path must not write a checkpoint: the registered input IS the geometry, and saving
            // over it would destroy the very input the runner hash-verified.
            if (!auditMode) s.saveState(out.getAbsolutePath());

            try (PrintWriter w = new PrintWriter(new File(dir, "block_geometry_import.tsv"), StandardCharsets.UTF_8)) {
                w.println("GEOMETRY_MODE\t" + (auditMode ? "audit_loaded_geometry_sim" : "legacy_step_import"));
                w.println("STEP_IMPORT_CALLS\t" + importCalls);
                w.println("ROTOR_STEP_SHA256\t" + System.getenv("AIR3B_ROTOR_SHA256"));
                w.println("STATOR_STEP_SHA256\t" + System.getenv("AIR3B_STATOR_SHA256"));
                if (auditMode) {
                    // Deliberately NOT the sha256 of the loaded checkpoint. STAR holds that file open, and
                    // the runner has already hashed the exact bytes it handed to STAR and recorded them in
                    // stage-<name>.json; re-hashing here would add a failure mode, not a check. The measured
                    // quantity on this path is the file's byte length, and the identity binding is the pin
                    // that the macOS controller and the Windows runner both compared against real bytes.
                    w.println("INPUT_SIM\t" + out.getName());
                    w.println("INPUT_SIM_BYTES\t" + out.length());
                    w.println("INPUT_SIM_SHA256_PIN\t" + inputPin);
                    w.println("INPUT_SIM_SHA256_VERIFIED_BY\trunner_and_controller");
                } else {
                    w.println("OUTPUT_SHA256\t" + sha(out));
                }
                w.println("PARTS\t" + parts.getParts().size());
                w.println("REGIONS\t" + s.getRegionManager().getRegions().size());
                w.println("ITERATION\t" + s.getSimulationIterator().getCurrentIteration());
                int ip = 0;
                for (GeometryPart p : parts.getParts()) {
                    w.println("PART\t" + (ip++) + "\t" + p.getClass().getName() + "\t" + p.getPresentationName());
                    boolean closed = false, manifold = false; int tris = -1;
                    if (p instanceof MeshPart) {
                        MeshPart mp = (MeshPart) p;
                        closed = mp.isClosed(); manifold = mp.isManifold(); tris = mp.getTriangleCount();
                    }
                    w.println("PART_TOPO\t" + p.getPresentationName() + "\tclosed=" + closed
                            + "\tmanifold=" + manifold + "\ttriangles=" + tris);
                    if (p instanceof SolidModelPart) {
                        SolidModelPart smp = (SolidModelPart) p;
                        PartSurfaceManager psm = smp.getPartSurfaceManager();
                        Collection<PartSurface> surfaces = psm.getPartSurfaces();
                        w.println("PART_SURFACES\t" + p.getPresentationName() + "\t" + surfaces.size());
                        int is = 0;
                        for (PartSurface ps : surfaces)
                            w.println("PART_SURFACE\t" + p.getPresentationName() + "\t" + (is++) + "\t"
                                    + ps.getClass().getSimpleName() + "\t" + ps.getPresentationName());
                        Collection<PartCurve> curves = smp.getPartCurveManager().getPartCurves();
                        w.println("PART_CURVES\t" + p.getPresentationName() + "\t" + curves.size());
                        for (PartCurve pc : curves)
                            w.println("PART_CURVE\t" + p.getPresentationName() + "\t" + pc.getPresentationName());
                    }
                }
                w.println("PART_SURFACE_TOTAL\t" + totalSurfaces(parts));
                dumpMethods(w, "MeshManager", s.get(MeshManager.class).getClass());
                dumpMethods(w, "Simulation", s.getClass());
                dumpMethods(w, "PartSurfaceManager", PartSurfaceManager.class);
                dumpMethods(w, "SolidModelPart", SolidModelPart.class);
                dumpMethods(w, "GeometryPart", GeometryPart.class);
                dumpMethods(w, "MeshPart", MeshPart.class);
                w.println("AIR3B_BLOCK_GEOMETRY_IMPORT_COMPLETE");
                if (w.checkError()) throw new IOException("Output write failed");
            }
            writeCertificate(dir, out, parts, s, auditMode, importCalls);
            System.out.println("AIR3B_BLOCK_GEOMETRY_IMPORT_COMPLETE");
        } catch (Exception e) {
            throw new RuntimeException("Block geometry import failed: " + e.getMessage(), e);
        }
    }

    /**
     * The measurement-level record for the geometry stage, carrying values this run actually read.
     *
     * `status: ok` means "the geometry operation completed and these parts were measured"; it is
     * written only after the two STEP hashes have been re-verified, the mode's own checks have run,
     * and every part has passed the closed/manifold/non-empty gates above. Convergence and physics
     * are not applicable to a geometry stage, so no such field is claimed here.
     *
     * `regions` and `iteration` are part of the certificate on purpose: they are what makes this
     * record unusable as a certificate for a state that was secretly a solve checkpoint, since a
     * solved state carries regions and a non-zero iteration.
     */
    private static void writeCertificate(File dir, File out, SimulationPartManager parts, Simulation sim,
            boolean auditMode, int importCalls) throws Exception {
        StringBuilder j = new StringBuilder();
        j.append("{\"status\":\"ok\",\"audit\":\"air3b_block_geometry_import\",\"schema_version\":1,");
        j.append("\"geometry_mode\":\"").append(auditMode ? "audit_loaded_geometry_sim" : "legacy_step_import").append("\",");
        j.append("\"step_import_calls\":").append(importCalls).append(',');
        j.append("\"measured_parts\":").append(parts.getParts().size()).append(',');
        j.append("\"measured_regions\":").append(sim.getRegionManager().getRegions().size()).append(',');
        j.append("\"measured_interfaces\":").append(sim.getInterfaceManager().getObjects().size()).append(',');
        j.append("\"measured_iteration\":").append(sim.getSimulationIterator().getCurrentIteration()).append(',');
        j.append("\"rotor_step_sha256\":\"").append(System.getenv("AIR3B_ROTOR_SHA256")).append("\",");
        j.append("\"stator_step_sha256\":\"").append(System.getenv("AIR3B_STATOR_SHA256")).append("\",");
        if (auditMode) {
            // There is no NEW output on this path: the audited object IS the registered input. The
            // certificate therefore names the input, its measured byte length, and the content pin that
            // the macOS controller and the Windows runner both compared against the real bytes BEFORE
            // STAR started. The macro does not re-hash the loaded checkpoint: STAR holds that file open,
            // and a second hash here would add a failure mode rather than a check. The pin is labelled as
            // a pin for exactly that reason - it is an identity binding, not this macro's own digest.
            j.append("\"input_sim_name\":\"").append(esc(out.getName())).append("\",");
            j.append("\"input_sim_bytes\":").append(out.length()).append(',');
            j.append("\"input_sim_sha256_pin\":\"").append(System.getenv("AIR3B_GEOMETRY_SIM_SHA256")).append("\",");
            j.append("\"input_sim_sha256_verified_by\":\"macos controller and windows runner, byte for byte"
                    + " against the pipeline input_hashes pin before STAR started\",");
        } else {
            j.append("\"output_sim_sha256\":\"").append(sha(out)).append("\",");
        }
        j.append("\"parts\":[");
        boolean first = true;
        for (GeometryPart p : parts.getParts()) {
            boolean closed = false, manifold = false;
            long triangles = -1;
            if (p instanceof MeshPart) {
                MeshPart mp = (MeshPart) p;
                closed = mp.isClosed();
                manifold = mp.isManifold();
                triangles = mp.getTriangleCount();
            }
            if (!first) j.append(',');
            j.append("{\"name\":\"").append(esc(p.getPresentationName())).append("\",")
             .append("\"class\":\"").append(esc(p.getClass().getSimpleName())).append("\",")
             .append("\"closed\":").append(closed).append(',')
             .append("\"manifold\":").append(manifold).append(',')
             .append("\"triangles\":").append(triangles).append(',')
             .append("\"part_surfaces\":").append(p.getPartSurfaces().size()).append('}');
            first = false;
        }
        j.append("],");
        j.append("\"complete_marker\":\"AIR3B_BLOCK_GEOMETRY_IMPORT_COMPLETE\"}\n");
        try (PrintWriter cert = new PrintWriter(new File(dir, "block_geometry_audit.json"), StandardCharsets.UTF_8)) {
            cert.print(j.toString());
            if (cert.checkError()) throw new IOException("Certificate write failed");
        }
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\t", " ");
    }
}
