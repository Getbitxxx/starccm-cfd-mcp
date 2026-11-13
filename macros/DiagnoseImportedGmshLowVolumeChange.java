/* Read-only diagnostic for low Volume Change cells in an imported mesh. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.Collections;
import java.util.Vector;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.NeoProperty;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;
import star.vis.ThresholdMode;
import star.vis.ThresholdPart;

public class DiagnoseImportedGmshLowVolumeChange extends StarMacro {
    private static String field(String spec, String name) {
        Matcher match = Pattern.compile("\\\"" + name + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(spec);
        if (!match.find()) throw new IllegalArgumentException("Missing " + name);
        return match.group(1);
    }

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        ThresholdPart selected = null;
        try {
            String spec = Files.readString(new File(dir, "case_spec.json").toPath(), StandardCharsets.UTF_8);
            Region region = sim.getRegionManager().getRegion(field(spec, "mesh_region_name"));
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (region == null || mesh == null || !mesh.describesRegion(region)) {
                throw new IllegalStateException("Imported region or volume mesh missing");
            }
            FieldFunction volumeChange = null;
            for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
                if (candidate instanceof VolumeChangeFunction) {
                    volumeChange = candidate;
                    break;
                }
            }
            if (volumeChange == null) throw new IllegalStateException("Volume Change field missing");
            sim.getSolution().initializeSolution();
            selected = sim.getPartManager().createThresholdPart(
                    new Vector<Object>(Collections.singletonList(region)),
                    new Vector<Double>(Arrays.asList(0.0, 0.0100000001)),
                    volumeChange, ThresholdMode.BELOW_TAG.getValue());
            selected.setPresentationName("TEMP_IMPORTED_LOW_VOLUME_CHANGE");
            int estimated = selected.getEstimatedNumberOfCells();
            NeoProperty preview = sim.getMeshManager().previewInvalidCells(
                    Collections.singletonList(region), 1.0, 1.0e-5, 0.0100000001, 0, 0.0, 0.0);
            String export = "skipped";
            if (estimated > 0 && estimated <= 10000) {
                try {
                    File csv = new File(dir, "gmsh_low_volume_change_cells.csv");
                    selected.exportDataSourceCSV(mesh, false, csv.getAbsolutePath(), volumeChange);
                    export = csv.isFile() ? "written" : "missing";
                } catch (Throwable failure) {
                    export = "failed: " + failure.getMessage();
                }
            }
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_low_volume_change.txt"), StandardCharsets.UTF_8)) {
                out.write("region=" + region.getPresentationName() + "\nmetric=VolumeChange\nmode=BELOW\ncutoff=0.0100000001\n");
                out.write("estimated_cells=" + estimated + "\nexport=" + export + "\n");
                out.write("preview_invalid_cells=" + preview.toFormattedString() + "\n");
            }
            System.out.println("GMSH_LOW_VOLUME_CHANGE_DIAGNOSTIC_COMPLETE");
        } catch (Throwable failure) {
            try (FileWriter out = new FileWriter(new File(dir, "gmsh_low_volume_change_failure.txt"), StandardCharsets.UTF_8)) {
                out.write(failure.toString());
            } catch (Throwable ignored) { }
            throw new RuntimeException("Imported low Volume Change diagnostic failed", failure);
        } finally {
            if (selected != null) sim.deleteObjects(Collections.singletonList(selected));
        }
    }
}
