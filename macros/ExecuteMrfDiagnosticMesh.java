/* Mesh the two explicit MRF fluid roles separately and retain a first-region checkpoint. */
import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import star.common.*;
import star.meshing.AutoMeshOperation;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;

public class ExecuteMrfDiagnosticMesh extends StarMacro {
    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        String phase = "prerequisites";
        long rotatingCells = 0;
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String mesh = object(spec, "mesh");
            String roles = object(spec, "mrf_regions");
            String geometry = object(spec, "geometry");
            AutoMeshOperation rotatingOperation = operation(sim, string(mesh, "rotating_operation"), string(geometry, "rotating_fluid_part"));
            AutoMeshOperation stationaryOperation = operation(sim, string(mesh, "stationary_operation"), string(geometry, "stationary_fluid_part"));
            Region rotating = sim.getRegionManager().getRegion(string(roles, "rotor_region"));
            Region stationary = sim.getRegionManager().getRegion(string(roles, "stator_region"));
            if (rotating == null || stationary == null || rotating == stationary || rotatingOperation == stationaryOperation ||
                    sim.getRegionManager().getRegions().size() != 2 || sim.getInterfaceManager().getObjects().size() != 3 ||
                    sim.get(MeshOperationManager.class).getObjects().stream().filter(op -> op instanceof AutoMeshOperation).count() != 2)
                throw new IllegalStateException("Audited split MRF topology or operation roles are invalid");
            phase = "rotating_mesh";
            rotatingOperation.execute();
            FvRepresentation meshRepresentation = representation(sim);
            if (!meshRepresentation.describesRegion(rotating)) throw new IllegalStateException("Volume mesh omits rotating role");
            rotatingCells = meshRepresentation.getCellCount();
            sim.saveState(new File(dir, "mrf_rotating_mesh_checkpoint.sim").getAbsolutePath());
            phase = "stationary_mesh";
            stationaryOperation.execute();
            meshRepresentation = representation(sim);
            if (!meshRepresentation.describesRegion(rotating) || !meshRepresentation.describesRegion(stationary) ||
                    meshRepresentation.getCellCount() <= rotatingCells)
                throw new IllegalStateException("Split volume mesh does not cover both fluid roles");
            phase = "save";
            sim.saveState(new File(dir, "mrf_diagnostic_meshed.sim").getAbsolutePath());
            try (FileWriter out = new FileWriter(new File(dir, "mrf_diagnostic_mesh.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"ok\",\"operations\":2,\"regions\":2,\"interfaces\":3," +
                    "\"rotating_checkpoint_cells\":" + rotatingCells + ",\"volume_cells\":" + meshRepresentation.getCellCount() +
                    ",\"geometry_entry\":\"isolated original input with explicitly removed legacy regions/solution\"," +
                    "\"quality_gate\":\"requires subsequent fresh AuditMrfVolumeMeshQuality\",\"complete_marker\":\"MRF_DIAGNOSTIC_MESH_GENERATED\"}\n");
            }
            System.out.println("MRF_DIAGNOSTIC_MESH_GENERATED");
        } catch (Throwable failure) {
            try (PrintWriter out = new PrintWriter(new FileWriter(new File(dir, "mrf_diagnostic_mesh_failure.txt"), StandardCharsets.UTF_8))) { failure.printStackTrace(out); }
            catch (Throwable ignored) { }
            boolean checkpoint = false;
            try { sim.saveState(new File(dir, "mrf_mesh_failure_checkpoint.sim").getAbsolutePath()); checkpoint = true; }
            catch (Throwable ignored) { }
            try (FileWriter out = new FileWriter(new File(dir, "mrf_diagnostic_mesh_failure.json"), StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"error\",\"phase\":\"" + escape(phase) + "\",\"rotating_checkpoint_cells\":" + rotatingCells +
                    ",\"failure_checkpoint_saved\":" + checkpoint + ",\"message\":\"" + escape(String.valueOf(failure)) + "\"}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("MRF diagnostic mesh failed during " + phase, failure);
        }
    }
    private static AutoMeshOperation operation(Simulation sim, String name, String partName) {
        Object selected = sim.get(MeshOperationManager.class).getObject(name);
        Object part = sim.get(SimulationPartManager.class).getPart(partName);
        if (!(selected instanceof AutoMeshOperation) || !(part instanceof MeshOperationPart)) throw new IllegalStateException("CaseSpec operation/fluid role type mismatch");
        AutoMeshOperation op = (AutoMeshOperation) selected;
        if (op.getInputGeometryObjects().getObjects().size() != 1 || !op.getInputGeometryObjects().getObjects().contains(part) || !op.getMeshPartByPart())
            throw new IllegalStateException("Mesh operation does not belong to the selected fluid role");
        return op;
    }
    private static FvRepresentation representation(Simulation sim) {
        FvRepresentation found = null;
        for (Representation r : sim.getRepresentationManager().getObjects()) if (r instanceof FvRepresentation) {
            FvRepresentation candidate = (FvRepresentation) r;
            if (candidate.getCellCount() > 0) {
                if (found != null) throw new IllegalStateException("More than one nonempty volume representation");
                found = candidate;
            }
        }
        if (found == null) throw new IllegalStateException("MRF mesh has no volume cells");
        return found;
    }
    private static String object(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing object "+k);return m.group(1); }
    private static String string(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing string "+k);return m.group(1); }
    private static String escape(String s) {
        StringBuilder out = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int)c));
            else out.append(c);
        }
        return out.toString();
    }

}

