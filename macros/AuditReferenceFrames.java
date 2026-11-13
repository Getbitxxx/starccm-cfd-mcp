/* Read-only frame inventory before assigning an MRF region. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;

import star.common.Simulation;
import star.common.StarMacro;
import star.common.Region;
import star.motion.MotionSpecification;
import star.motion.ReferenceFrameManager;
import star.motion.UserRotatingReferenceFrame;

public class AuditReferenceFrames extends StarMacro {
    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        try (FileWriter out = new FileWriter(new File(sim.getSessionDirFile(), "reference_frames_audit.txt"),
                StandardCharsets.UTF_8)) {
            for (Object frame : sim.get(ReferenceFrameManager.class).getObjects()) {
                out.write("FRAME\t" + frame.toString() + "\t" + frame.getClass().getName() + "\n");
                if (frame instanceof UserRotatingReferenceFrame) {
                    UserRotatingReferenceFrame rotating = (UserRotatingReferenceFrame) frame;
                    double[] axis = rotating.getAxisVector().getVector().toDoubleArray();
                    double[] origin = rotating.getOriginVector().getVector().toDoubleArray();
                    out.write("ROTATING\t" + rotating.getPresentationName() + "\t" + axis[0] + "," + axis[1] + "," + axis[2] +
                            "\t" + origin[0] + "," + origin[1] + "," + origin[2] +
                            "\t" + rotating.getRotationRate().getSIValue() + "\n");
                }
            }
            for (Region region : sim.getRegionManager().getRegions()) {
                MotionSpecification motion = region.getValues().get(MotionSpecification.class);
                out.write("REGION_FRAME\t" + region.getPresentationName() + "\t" +
                        (motion.getReferenceFrame() == null ? "null" : motion.getReferenceFrame().getPresentationName()) + "\n");
            }
            out.write("REFERENCE_FRAMES_AUDIT_COMPLETE\n");
            System.out.println("REFERENCE_FRAMES_AUDIT_COMPLETE");
        } catch (Exception e) {
            throw new RuntimeException("Reference-frame audit failed", e);
        }
    }
}
