/* Read-only inspection of flow-region boundary values and reference frame. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.Boundary;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.Profile;
import star.common.ConstantScalarProfileMethod;
import star.energy.StaticTemperatureProfile;
import star.flow.StaticPressureProfile;
import star.flow.VelocityMagnitudeProfile;
import star.flow.FlowDirectionOption;
import star.flow.InletVelocityOption;
import star.motion.ReferenceFrameOption;
import star.turbulence.TurbulenceIntensityProfile;
import star.turbulence.TurbulentViscosityRatioProfile;
import star.meshing.MeshOperation;
import star.meshing.MeshOperationManager;

public class AuditBoundaryPhysics extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File output = new File(sim.getSessionDirFile(), "boundary_physics_audit.txt");
        try (FileWriter out = new FileWriter(output, StandardCharsets.UTF_8)) {
            for (MeshOperation operation : sim.get(MeshOperationManager.class).getObjects()) {
                out.write("MESH_OPERATION\t" + operation.getPresentationName() + "\t" +
                        operation.getClass().getName() + "\n");
            }
            for (Region region : sim.getRegionManager().getObjects()) {
                out.write("REGION\t" + region.getPresentationName() + "\tCONTINUUM\t" +
                        (region.getPhysicsContinuum() == null ? "null" : region.getPhysicsContinuum().getPresentationName()) + "\n");
                if (region.getPhysicsContinuum() != null) {
                    for (Object value : region.getPhysicsContinuum().getReferenceValues().getObjects()) {
                        out.write("REFERENCE_VALUE\t" + value.getClass().getName() + "\t" + value.toString() + "\n");
                    }
                }
                for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                    out.write("BOUNDARY\t" + region.getPresentationName() + "\t" + boundary.getPresentationName() +
                            "\t" + boundary.getBoundaryType().getClass().getName() + "\n");
                    for (Object condition : boundary.getConditions().getObjects()) {
                        out.write("CONDITION\t" + boundary.getPresentationName() + "\t" +
                                condition.getClass().getName() + "\t" + condition.toString() + "\n");
                        if (condition instanceof FlowDirectionOption) {
                            out.write("CONDITION_VALUE\t" + boundary.getPresentationName() + "\tFlowDirectionOption\t" +
                                    ((FlowDirectionOption) condition).getSelectedElement() + "\n");
                        } else if (condition instanceof InletVelocityOption) {
                            out.write("CONDITION_VALUE\t" + boundary.getPresentationName() + "\tInletVelocityOption\t" +
                                    ((InletVelocityOption) condition).getSelectedElement() + "\n");
                        } else if (condition instanceof ReferenceFrameOption) {
                            out.write("CONDITION_VALUE\t" + boundary.getPresentationName() + "\tReferenceFrameOption\t" +
                                    ((ReferenceFrameOption) condition).getSelectedElement() + "\n");
                        }
                    }
                    for (Object item : boundary.getValues().getObjects()) {
                        if (!(item instanceof Profile)) continue;
                        Profile profile = (Profile) item;
                        out.write("PROFILE\t" + boundary.getPresentationName() + "\t" +
                                profile.getClass().getName() + "\t" + profile.getPresentationName() + "\n");
                        try {
                            String definition = null;
                            if (profile instanceof VelocityMagnitudeProfile) {
                                definition = ((VelocityMagnitudeProfile) profile).getMethod(ConstantScalarProfileMethod.class)
                                        .getQuantity().getDefinition();
                            } else if (profile instanceof StaticPressureProfile) {
                                definition = ((StaticPressureProfile) profile).getMethod(ConstantScalarProfileMethod.class)
                                        .getQuantity().getDefinition();
                            } else if (profile instanceof StaticTemperatureProfile) {
                                definition = ((StaticTemperatureProfile) profile).getMethod(ConstantScalarProfileMethod.class)
                                        .getQuantity().getDefinition();
                            } else if (profile instanceof TurbulenceIntensityProfile) {
                                definition = ((TurbulenceIntensityProfile) profile).getMethod(ConstantScalarProfileMethod.class)
                                        .getQuantity().getDefinition();
                            } else if (profile instanceof TurbulentViscosityRatioProfile) {
                                definition = ((TurbulentViscosityRatioProfile) profile).getMethod(ConstantScalarProfileMethod.class)
                                        .getQuantity().getDefinition();
                            }
                            if (definition != null) out.write("PROFILE_VALUE\t" + boundary.getPresentationName() +
                                    "\t" + profile.getClass().getSimpleName() + "\t" + definition + "\n");
                        } catch (Exception ignored) {
                            out.write("PROFILE_VALUE_UNAVAILABLE\t" + boundary.getPresentationName() + "\t" +
                                    profile.getClass().getSimpleName() + "\n");
                        }
                    }
                }
            }
            out.write("BOUNDARY_PHYSICS_AUDIT_COMPLETE\n");
            System.out.println("BOUNDARY_PHYSICS_AUDIT_COMPLETE");
        } catch (Exception e) {
            throw new RuntimeException("Boundary-physics audit failed", e);
        }
    }
}
