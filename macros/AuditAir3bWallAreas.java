/* AIR-3B A3c follow-up: read-only face-count audit of the propeller wall boundaries.
 *
 * The y+ audit reported three of the 44 propeller walls (BLADE 6, BLADE 12, BLADE 18)
 * with the sentinel triple min=Double.MAX_VALUE, area average=0, max=-Double.MAX_VALUE
 * and a covered fraction of 0. That is what STAR reports for a part with no faces, but
 * "no faces" must be measured, not assumed: a wall that carries real area and produces
 * no y+ sample would be an unreported coverage hole and would also deflate the
 * area-weighted aggregate.
 *
 * This macro duplicates the exact boundary selection of AuditAir3bYplus and reports, per
 * wall and for two unions (all 44 walls, and only the walls with a non-zero face count):
 *   - the summed indicator field, which counts the entities the report operates on,
 *   - the summed native area field when the installation exposes one.
 * If the 44-wall union and the non-empty union agree, the empty walls carry no area and
 * the y+ aggregate is not missing covered area.
 *
 * It never initializes, iterates, meshes or saves.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;

public class AuditAir3bWallAreas extends StarMacro {

    private static final String COMPLETE = "AIR3B_WALLAREA_AUDIT_COMPLETE";
    private static final String[] AREA_ALIASES =
            { "Area", "FaceArea", "Face Area", "Surface Area", "SurfaceArea" };

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private static FvRepresentation findVolumeMesh(Simulation sim) {
        for (Representation r : sim.getRepresentationManager().getObjects())
            if (r instanceof FvRepresentation) return (FvRepresentation) r;
        return null;
    }

    /** Sums a field function over the given parts; returns null when no report is possible. */
    private static Double sumOver(Simulation sim, FvRepresentation mesh, FieldFunction f,
                                  List<Boundary> parts, List<ClientServerObject> temporary) {
        if (f == null || parts.isEmpty()) return null;
        SumReport r = sim.getReportManager().createReport(SumReport.class);
        temporary.add(r);
        r.setRepresentation(mesh);
        r.setFieldFunction(f);
        r.getParts().setObjects(parts.toArray(new NamedObject[0]));
        double v = r.getReportMonitorValue();
        return v;
    }

    /** As sumOver, but a rejected field degrades to null instead of aborting the audit. */
    private static Double safeSumOver(Simulation sim, FvRepresentation mesh, FieldFunction f,
                                      List<Boundary> parts, List<ClientServerObject> temporary) {
        try {
            return sumOver(sim, mesh, f, parts, temporary);
        } catch (Throwable t) {
            System.out.println("WALLAREA area sum unavailable: " + t.getMessage());
            return null;
        }
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        List<ClientServerObject> temporary = new ArrayList<>();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);

            FvRepresentation mesh = findVolumeMesh(sim);
            if (mesh == null || mesh.getCellCount() <= 0) throw new IllegalStateException("No volume mesh");

            Region rotating = sim.getRegionManager().getRegion("AIR3B_ROTATING");
            if (rotating == null) throw new IllegalStateException("AIR3B_ROTATING is missing");
            List<Boundary> propellerWalls = new ArrayList<>();
            for (Boundary b : rotating.getBoundaryManager().getBoundaries()) {
                String n = b.getPresentationName();
                if ((n.startsWith("BLADE") || n.startsWith("HUB")) && b.getBoundaryType() instanceof WallBoundary)
                    propellerWalls.add(b);
            }
            int expectedWalls = (int) env("AIR3B_EXPECTED_WALL_BOUNDARIES", 44.0);
            if (propellerWalls.size() != expectedWalls)
                throw new IllegalStateException("Found " + propellerWalls.size()
                        + " propeller wall boundaries, expected " + expectedWalls);

            // A constant field makes a sum report count whatever entity the report integrates over.
            UserFieldFunction one = sim.getFieldFunctionManager().createFieldFunction();
            temporary.add(one);
            one.setFunctionName("TEMP_WALLAREA_ONE");
            one.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
            one.setDefinition("1");

            // The installed 2502 exposes a native function named "Area", but it is a vector
            // (the face area vector) and SumReport rejects it with "Area is not a scalar field
            // function". A scalar magnitude of it is the documented way to obtain an integrable
            // area, but accepting the first alias that merely EXISTS once cost this stage a
            // failed trial, so each candidate is now probed on the full union and accepted only
            // when it yields a positive finite sum. Nothing is trusted on name alone, and the
            // probe values are recorded.
            List<Boundary> nonEmpty = new ArrayList<>();
            List<String> emptyNames = new ArrayList<>();
            List<String> areaProbes = new ArrayList<>();
            FieldFunction areaField = null;
            String areaName = null;
            Double probedAllArea = null;
            for (String alias : AREA_ALIASES) {
                if (!sim.getFieldFunctionManager().hasFunction(alias)) continue;
                FieldFunction f = sim.getFieldFunctionManager().getFunction(alias);
                if (f == null) continue;
                UserFieldFunction mag = sim.getFieldFunctionManager().createFieldFunction();
                temporary.add(mag);
                mag.setFunctionName("TEMP_WALLAREA_AREA_MAG");
                mag.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
                mag.setDefinition("mag(${" + f.getFunctionName() + "})");
                Double probe = safeSumOver(sim, mesh, mag, propellerWalls, temporary);
                boolean usable = probe != null && probe > 0.0 && Double.isFinite(probe);
                areaProbes.add("AREA_PROBE\t" + alias + "\tmag(${" + f.getFunctionName() + "})"
                        + "\tunion_sum\t" + (probe == null ? "NA" : probe.toString())
                        + "\taccepted\t" + usable);
                if (usable) {
                    areaField = mag;
                    areaName = "mag(${" + f.getFunctionName() + "})";
                    probedAllArea = probe;
                    break;
                }
            }

            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_wallarea.tsv")), StandardCharsets.UTF_8))) {
                w.write("CELLS\t" + mesh.getCellCount() + "\n");
                w.write("AREA_FIELD\t" + (areaName == null ? "UNAVAILABLE" : areaName) + "\n");
                w.write("WALLS_TOTAL\t" + propellerWalls.size() + "\n");
                for (String line : areaProbes) w.write(line + "\n");
                for (Boundary b : propellerWalls) {
                    // The constant indicator is summed defensively too: a rejected field must
                    // degrade to no measurement rather than abort and destroy the evidence.
                    Double count = safeSumOver(sim, mesh, one, Collections.singletonList(b), temporary);
                    if (count != null && (!Double.isFinite(count))) count = null;
                    Double area = safeSumOver(sim, mesh, areaField, Collections.singletonList(b), temporary);
                    if (area != null && (!(area > 0.0) || !Double.isFinite(area))) area = null;
                    boolean empty = count != null && count == 0.0;
                    if (empty) emptyNames.add(b.getPresentationName()); else nonEmpty.add(b);
                    w.write("WALL\t" + b.getPresentationName()
                            + "\tindicator_sum\t" + count
                            + "\tarea_sum_m2\t" + (area == null ? "NA" : area.toString())
                            + "\tempty\t" + (count == null ? "UNKNOWN" : Boolean.toString(empty)) + "\n");
                }

                Double allCount = safeSumOver(sim, mesh, one, propellerWalls, temporary);
                if (allCount != null && !Double.isFinite(allCount)) allCount = null;
                Double allArea = (probedAllArea != null)
                        ? probedAllArea : safeSumOver(sim, mesh, areaField, propellerWalls, temporary);
                Double nonEmptyCount = safeSumOver(sim, mesh, one, nonEmpty, temporary);
                if (nonEmptyCount != null && !Double.isFinite(nonEmptyCount)) nonEmptyCount = null;
                Double nonEmptyArea = safeSumOver(sim, mesh, areaField, nonEmpty, temporary);
                // A measured area must be a positive finite number; anything else is no measurement.
                if (allArea != null && (!(allArea > 0.0) || !Double.isFinite(allArea))) allArea = null;
                if (nonEmptyArea != null && (!(nonEmptyArea > 0.0) || !Double.isFinite(nonEmptyArea)))
                    nonEmptyArea = null;

                w.write("UNION\tALL\tindicator_sum\t" + allCount
                        + "\tarea_sum_m2\t" + (allArea == null ? "NA" : allArea.toString()) + "\n");
                w.write("UNION\tNON_EMPTY\tindicator_sum\t" + nonEmptyCount
                        + "\tarea_sum_m2\t" + (nonEmptyArea == null ? "NA" : nonEmptyArea.toString()) + "\n");
                w.write("EMPTY_COUNT\t" + emptyNames.size() + "\n");
                w.write("EMPTY_WALLS\t" + emptyNames + "\n");

                boolean countsAgree = allCount != null && nonEmptyCount != null
                        && Math.abs(allCount - nonEmptyCount) < 1e-9;
                // The conclusion is only filed when the area was actually measured: with no
                // scalar area available the audit is inconclusive, not passing.
                boolean areasAvailable = allArea != null && nonEmptyArea != null;
                boolean areasAgree = areasAvailable
                        && Math.abs(allArea - nonEmptyArea) <= 1e-12 * Math.max(1.0, Math.abs(allArea));
                // A run in which nothing is empty would satisfy the comparison vacuously and
                // would audit nothing, so the premise is gated separately from the conclusion,
                // and the identity of the empty walls is pinned as well as their count: a
                // different three walls would be audited by the same logic without complaint.
                boolean premiseHolds = emptyNames.size() == (int) env("AIR3B_EXPECTED_EMPTY_WALLS", 3.0);
                List<String> expectedEmpty = new ArrayList<>();
                for (String n : envText("AIR3B_EXPECTED_EMPTY_WALL_NAMES", "BLADE 6,BLADE 12,BLADE 18").split(","))
                    if (!n.trim().isEmpty()) expectedEmpty.add(n.trim());
                boolean identityHolds = emptyNames.size() == expectedEmpty.size()
                        && emptyNames.containsAll(expectedEmpty);
                w.write("EXPECTED_EMPTY_WALLS\t" + expectedEmpty + "\n");
                w.write("GATE\tempty_wall_premise\t" + premiseHolds + "\n");
                w.write("GATE\tempty_wall_identity\t" + identityHolds + "\n");
                w.write("GATE\tarea_field_available\t" + areasAvailable + "\n");
                w.write("GATE\tempty_walls_carry_no_area\t"
                        + (premiseHolds && identityHolds && areasAvailable && countsAgree && areasAgree) + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B wall area audit failed: " + e.getMessage(), e);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject x : temporary) {
                try { sim.deleteObjects(Collections.singletonList(x)); } catch (Throwable ignored) { }
            }
        }
    }
}
