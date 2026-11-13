/* Read-only STL export of the problematic rotating fluid part for mesh diagnostics. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.common.GeometryPart;
import star.meshing.MeshPart;
import star.meshing.MeshOperationPart;

public class ExportMrfRotatingFluidStl extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            MeshOperationPart part = (MeshOperationPart) sim.get(SimulationPartManager.class)
                    .getPart("AUTO_MRF_ROTATING_FLUID");
            if (part == null) throw new IllegalStateException("Rotating fluid part missing");
            File output = new File(sim.getSessionDirFile(), "mrf_rotating_fluid.stl");
            part.exportStlPart(output.getAbsolutePath(), true);
            if (!output.isFile() || output.length() <= 84) throw new IllegalStateException("Rotating fluid STL export empty");
            String[] sourceNames = {"PROPELLER", "AUTO_FULL_ROTOR_BLADE_120", "AUTO_FULL_ROTOR_BLADE_240"};
            for (int i = 0; i < sourceNames.length; i++) {
                GeometryPart source = sim.get(SimulationPartManager.class).getPart(sourceNames[i]);
                if (!(source instanceof MeshPart)) throw new IllegalStateException("Selected source part is not a mesh part: " + sourceNames[i]);
                File sourceStl = new File(sim.getSessionDirFile(), "rotor_source_part_" + i + ".stl");
                ((MeshPart) source).exportStlPart(sourceStl.getAbsolutePath(), true);
                if (!sourceStl.isFile() || sourceStl.length() <= 84) {
                    throw new IllegalStateException("Selected source STL empty: " + sourceNames[i]);
                }
            }
            try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "mrf_rotating_stl_export.txt"), StandardCharsets.UTF_8)) {
                out.write("part=" + part.getPresentationName() + "\ntriangles=" + part.getTriangleCount() +
                        "\nbytes=" + output.length() + "\nsource_parts=PROPELLER,AUTO_FULL_ROTOR_BLADE_120,AUTO_FULL_ROTOR_BLADE_240" +
                        "\nMRF_ROTATING_STL_EXPORT_COMPLETE\n");
            }
            System.out.println("MRF_ROTATING_STL_EXPORT_COMPLETE");
        } catch (Throwable failure) {
            throw new RuntimeException("Rotating fluid STL export failed", failure);
        }
    }
}
