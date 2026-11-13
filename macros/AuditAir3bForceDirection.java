/* AIR-3B A3: same-state force-direction readback on a solved MRF state.
 *
 * The thrust report direction was corrected from (0,0,-1) to (0,0,+1) on 2026-10-02, and the
 * restored-mesh pilot then read +93.7265 N where the earlier mesh had read -60.31 N. Two
 * things changed between those runs - the direction and the mesh - so the sign flip was only
 * validated as a pair. The pilot that produced +93.7265 N also saved its solved state, so the
 * direction can now be varied on an otherwise identical state.
 *
 * The macro mutates the DIRECTION of the existing AIR3B_THRUST_Z and AIR3B_TORQUE_Z reports
 * rather than creating new reports, because a new ForceReport would not necessarily reproduce
 * the shipped report's other settings (parts, pressure/shear contributions), and the point of
 * the test is that only the direction varies. If the report is a signed projection of the
 * force onto the direction vector, reversing the direction must reverse the value almost
 * exactly, and a lateral direction must read near zero for this axisymmetric rotor. A report
 * that returned a magnitude could not produce the observed negative reading at all, which is
 * the alternative this test rules out.
 *
 * It never initializes, iterates, meshes or saves. The direction is restored and verified
 * before the macro returns, and the saved simulation on disk is never rewritten. The direction
 * change is in-memory only: this macro never calls saveState, so the .sim on disk keeps the
 * shipped (0,0,+1) direction.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.base.report.*;
import star.common.*;
import star.flow.*;

public class AuditAir3bForceDirection extends StarMacro {

    private static final String COMPLETE = "AIR3B_FORCEDIR_AUDIT_COMPLETE";
    private static final double[][] DIRECTIONS = { { 0, 0, 1 }, { 0, 0, -1 }, { 1, 0, 0 }, { 0, 1, 0 } };
    // Explicit labels: keying results by a formatted vector is fragile, because "%.12g" renders
    // 0.0 as "0" and the lookup then silently misses. The first trial of this stage failed on
    // exactly that mismatch, so results are keyed by these fixed labels instead.
    private static final String[] FORCE_LABELS = { "(0,0,+1)", "(0,0,-1)", "(+1,0,0)", "(0,+1,0)" };
    private static final String[] MOMENT_LABELS = { "(0,0,+1)", "(0,0,-1)" };

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String fmt(double v) {
        return Double.isFinite(v) ? String.format(Locale.ROOT, "%.12g", v) : String.valueOf(v);
    }

    private static String show(double[] v) {
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < v.length; i++) b.append(i == 0 ? "" : ", ").append(fmt(v[i]));
        return b.append("]").toString();
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        String dirPath = System.getenv("AIR3B_JOB_DIR");
        if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
            throw new IllegalStateException("Invalid isolated job");
        File dir = new File(dirPath);
        try {
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            if (iteration <= 0) throw new IllegalStateException("A solved state is required");

            Report thrust = sim.getReportManager().getReport("AIR3B_THRUST_Z");
            Report torque = sim.getReportManager().getReport("AIR3B_TORQUE_Z");
            if (thrust == null || torque == null)
                throw new IllegalStateException("Target-quantity reports are missing");
            if (!(thrust instanceof ForceReport) || !(torque instanceof MomentReport))
                throw new IllegalStateException("Target-quantity reports have unexpected types");

            ForceReport force = (ForceReport) thrust;
            MomentReport moment = (MomentReport) torque;
            Units dimensionless = sim.getUnitsManager().getObject("");

            double[] shippedForce = force.getDirection().getVector().toDoubleArray();
            double[] shippedMoment = moment.getDirection().getVector().toDoubleArray();
            double expected = env("AIR3B_EXPECTED_THRUST_Z", 93.7265470524433);
            double projectionTolerance = env("AIR3B_PROJECTION_RTOL", 1e-9);
            // The lateral reading is recorded as data and gated only against a loose sanity
            // bound. For a nominally axisymmetric rotor whose three blades are meshed one body
            // at a time, a lateral residual of a few percent is physical, so a tight bound would
            // fail a sound audit and say nothing about the direction question. The lateral value
            // is still essential: a report returning the unsigned magnitude would read about
            // |fz| here instead of near zero, which is the only reading that exposes it.
            double lateralTolerance = env("AIR3B_LATERAL_FRACTION", 0.25);
            // Loosened after review: a force is a sum of large opposing pressure and viscous
            // contributions, so re-reading it after a save/load cycle can move the last digits
            // through partitioning and face ordering alone. The residual is recorded as a datum
            // and a miss is a reproducibility finding about save/load, not a falsification of
            // the projection, which rests on the direction reversal itself.
            double sameStateTolerance = env("AIR3B_SAMESTATE_RTOL", 1e-3);

            Map<String, Double> forceValues = new LinkedHashMap<>();
            Map<String, Double> momentValues = new LinkedHashMap<>();
            for (int i = 0; i < DIRECTIONS.length; i++) {
                double[] d = DIRECTIONS[i];
                force.getDirection().setComponentsAndUnits(d[0], d[1], d[2], dimensionless);
                forceValues.put(FORCE_LABELS[i], force.getReportMonitorValue());
            }
            for (int i = 0; i < MOMENT_LABELS.length; i++) {
                double[] d = DIRECTIONS[i];
                moment.getDirection().setComponentsAndUnits(d[0], d[1], d[2], dimensionless);
                momentValues.put(MOMENT_LABELS[i], moment.getReportMonitorValue());
            }
            force.getDirection().setComponentsAndUnits(shippedForce[0], shippedForce[1], shippedForce[2], dimensionless);
            moment.getDirection().setComponentsAndUnits(shippedMoment[0], shippedMoment[1], shippedMoment[2], dimensionless);

            double[] restoredForce = force.getDirection().getVector().toDoubleArray();
            double[] restoredMoment = moment.getDirection().getVector().toDoubleArray();
            boolean directionRestored = Arrays.equals(restoredForce, shippedForce)
                    && Arrays.equals(restoredMoment, shippedMoment);

            Double plus = forceValues.get("(0,0,+1)");
            Double minus = forceValues.get("(0,0,-1)");
            Double lateralX = forceValues.get("(+1,0,0)");
            Double lateralY = forceValues.get("(0,+1,0)");
            Double momentPlus = momentValues.get("(0,0,+1)");
            Double momentMinus = momentValues.get("(0,0,-1)");
            if (plus == null || minus == null || lateralX == null || lateralY == null
                    || momentPlus == null || momentMinus == null)
                throw new IllegalStateException("A direction failed to evaluate");
            if (!Double.isFinite(plus) || !Double.isFinite(minus) || !Double.isFinite(lateralX)
                    || !Double.isFinite(lateralY) || !Double.isFinite(momentPlus) || !Double.isFinite(momentMinus))
                throw new IllegalStateException("A direction returned a non-finite value");

            boolean forceSigned = Math.abs(plus + minus) <= projectionTolerance * Math.max(Math.abs(plus), Math.abs(minus));
            boolean momentSigned = Math.abs(momentPlus + momentMinus)
                    <= projectionTolerance * Math.max(Math.abs(momentPlus), Math.abs(momentMinus));
            boolean lateralNegligible = Math.abs(lateralX) <= lateralTolerance * Math.abs(plus)
                    && Math.abs(lateralY) <= lateralTolerance * Math.abs(plus);
            boolean sameState = plus > 0.0 && minus < 0.0
                    && Math.abs(minus + expected) <= sameStateTolerance * Math.abs(expected);

            try (Writer w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_forcedir.tsv")), StandardCharsets.UTF_8))) {
                w.write("ITERATION\t" + iteration + "\n");
                w.write("MUTATION_SCOPE\tin_memory_only_never_saved; the .sim on disk keeps the shipped direction\n");
                w.write("SHIPPED_DIRECTION\tAIR3B_THRUST_Z\t" + show(shippedForce) + "\n");
                w.write("SHIPPED_DIRECTION\tAIR3B_TORQUE_Z\t" + show(shippedMoment) + "\n");
                for (Map.Entry<String, Double> e : forceValues.entrySet())
                    w.write("FORCE\t" + e.getKey() + "\t" + fmt(e.getValue()) + "\n");
                for (Map.Entry<String, Double> e : momentValues.entrySet())
                    w.write("MOMENT\t" + e.getKey() + "\t" + fmt(e.getValue()) + "\n");
                w.write("PILOT_RECORDED_THRUST_Z\t" + fmt(expected) + "\n");
                w.write("LATERAL_RATIO\tabs_fx_over_fz\t" + fmt(Math.abs(lateralX) / Math.abs(plus)) + "\n");
                w.write("LATERAL_RATIO\tabs_fy_over_fz\t" + fmt(Math.abs(lateralY) / Math.abs(plus)) + "\n");
                w.write("SAME_STATE_RESIDUAL\tminus_plus_expected\t" + fmt(minus + expected) + "\n");
                w.write("SAME_STATE_RESIDUAL\trelative\t"
                        + fmt(Math.abs(minus + expected) / Math.abs(expected)) + "\n");
                w.write("RESTORED_DIRECTION\tAIR3B_THRUST_Z\t" + show(restoredForce) + "\n");
                w.write("RESTORED_DIRECTION\tAIR3B_TORQUE_Z\t" + show(restoredMoment) + "\n");
                w.write("GATE\tdirection_restored\t" + directionRestored + "\n");
                w.write("GATE\tforce_signed_projection\t" + forceSigned + "\n");
                w.write("GATE\tmoment_signed_projection\t" + momentSigned + "\n");
                w.write("GATE\tlateral_force_negligible\t" + lateralNegligible + "\n");
                w.write("GATE\tsame_state_matches_pilot\t" + sameState + "\n");
                w.write("COMPLETE_MARKER\t" + COMPLETE + "\n");
            }
            if (!directionRestored)
                throw new IllegalStateException("The report direction was not restored");
            System.out.println(COMPLETE);
        } catch (Exception e) {
            throw new RuntimeException("AIR-3B force direction audit failed: " + e.getMessage(), e);
        }
    }
}
