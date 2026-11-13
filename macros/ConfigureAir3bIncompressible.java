/* AIR-3B A3: switch the PHYSICAL-MODEL axis only, on the existing segment-2 checkpoint.
 *
 * Why this macro exists. Experiment A1 (tighter AMG convergence tolerance) and A2 (constant
 * Courant cap of 500) both came back negative: the residual plateau survived both, so the
 * linear-solver explanation is excluded. The third, never-tested lever is the physical model
 * itself. The recorded 2502 tutorial for a free-air rotating fan (MovingReferenceFramesRotatingFan)
 * runs Steady + Segregated + Constant Density + no energy + realisable k-epsilon; this project's
 * baseline runs Steady + Coupled + Ideal Gas (compressible) + Coupled Energy + SST k-omega.
 *
 * AXIS DISCIPLINE. Exactly one axis changes here - the physical model - and within it exactly the
 * two toggles the experiment defines:
 *   (1) density model: Ideal Gas -> Constant Density (star.flow.ConstantDensityModel)
 *   (2) energy equation: Coupled Energy -> disabled
 * The flow model stays Coupled Flow, the turbulence model stays SST k-omega/all-y+, time stays
 * Steady, the MRF frame, boundaries, reports and the mesh are untouched, and the solver axis
 * (AMG convergence tolerance, Courant option and CFL) is *proved* unchanged by readback so this
 * trial stays comparable with the A1/A2 baseline. A3 changes two toggles on one axis, so a null
 * result cannot attribute the cause to density separately from energy; that limit is stated in the
 * trial's own note and in the report rather than papered over.
 *
 * OFFICIAL BASIS (installed 2502 User Guide, "Equation of State Models Reference",
 * doc/en/online/STARCCMP/GUID-5648FC22-8A3E-4418-BE9F-E50976532040.html): "Constant Density ...
 * Requires Flow: Coupled Flow or Segregated Flow", and "Coupled Energy" is listed as activated by
 * the *Ideal*, IAPWS-w, PolyD, Real and TNEq equation-of-state models - not by Constant Density.
 * So Coupled Flow + Constant Density + no energy is a documented model combination on this install,
 * which is exactly why this axis is worth one trial. The macro still records STAR's own
 * isModelCompatible() verdict and gates on the readback, because a documented combination is not
 * the same as this checkpoint accepting it.
 *
 * Integrity gates before anything is written:
 *   - the loaded iteration must be the expected one (default 1240, the end of segment 2);
 *   - the expected compressible baseline must actually be present (Ideal Gas + Coupled Energy +
 *     Coupled Flow, Constant Density absent). If it is not, the checkpoint is not the state this
 *     experiment is defined against and the macro refuses rather than mutating an unknown state;
 *   - two regions and three interfaces, matching the segment macro's own precondition.
 *
 * It initializes nothing, iterates nothing and meshes nothing. It writes its evidence TSV before
 * saving the checkpoint, and it refuses to save at all if any readback gate fails.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.coupledflow.*;
import star.flow.*;
import star.material.*;

public class ConfigureAir3bIncompressible extends StarMacro {

    private static final String COMPLETE = "AIR3B_PHYSICS_VARIANT_COMPLETE";

    private static final String CD = "star.flow.ConstantDensityModel";
    private static final String IDEAL = "star.flow.IdealGasModel";
    private static final String COUPLED_ENERGY = "star.coupledflow.CoupledEnergyModel";

    private static double env(String name, double fallback) {
        String v = System.getenv(name);
        if (v == null || v.isEmpty()) return fallback;
        return Double.parseDouble(v);
    }

    private static String envText(String name, String fallback) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? fallback : v;
    }

    private static String fmt(double v) {
        return String.format(Locale.ROOT, "%.12g", v);
    }

    /** The simple class names of every enabled model, sorted, for an order-independent model set. */
    private static TreeSet<String> modelNames(PhysicsContinuum physics) {
        TreeSet<String> names = new TreeSet<>();
        for (Model m : physics.getModelManager().getObjects()) names.add(m.getClass().getSimpleName());
        return names;
    }

    /** Presence of one model by simple name, re-read from the live manager every call.
     *
     *  Every mutating step below is guarded by this rather than applied unconditionally. The reason is
     *  a wasted-trial risk, not a wish to loosen anything: enabling one physics model can enable or
     *  disable a dependent one as a side effect, and disabling an already-disabled model (or enabling
     *  an already-enabled one) can throw on this API. A throw here would burn the single scoped STAR
     *  job without producing any physics answer. The guards do NOT weaken the contract - the end state
     *  is still verified by readback from this same live manager, and the BEFORE provenance gate above
     *  already refuses a checkpoint that starts in the target state, so the skipped paths cannot make
     *  a no-op look like a successful change. */
    private static boolean has(PhysicsContinuum physics, String simpleName) {
        for (Model m : physics.getModelManager().getObjects())
            if (m.getClass().getSimpleName().equals(simpleName)) return true;
        return false;
    }

    /** One attempt to read the constant density through one MaterialPropertyManager. */
    private static Double constantFromManager(MaterialPropertyManager mpm, StringBuilder why) {
        try {
            ConstantDensityProperty property = mpm.getMaterialProperty(ConstantDensityProperty.class);
            if (property == null) {
                why.append("no ConstantDensityProperty; ");
                return null;
            }
            ConstantMaterialPropertyMethod method =
                    property.getMaterialPropertyMethod(ConstantMaterialPropertyMethod.class);
            if (method == null) {
                why.append("no ConstantMaterialPropertyMethod; ");
                return null;
            }
            double value = method.getQuantity().getSIValue();
            if (!Double.isFinite(value)) {
                why.append("non-finite value; ");
                return null;
            }
            return Double.valueOf(value);
        } catch (Throwable t) {
            why.append(String.valueOf(t.getMessage()).replace('\n', ' ').replace('\t', ' ')).append("; ");
            return null;
        }
    }

    /** The constant-density material property's SI value, or null when every access path fails.
     *
     *  Deliberately best-effort and deliberately NOT a gate on saving the checkpoint. The value
     *  decides whether this trial also perturbs the Reynolds number, so it must be recorded; but the
     *  material-property API family (MaterialPropertyManager / ConstantDensityProperty /
     *  ConstantMaterialPropertyMethod) has no other occurrence in this repository, so making an
     *  irreversible single-job run depend on the least API-certain call in the macro would convert an
     *  API surprise into a total loss of the physics answer. Instead three access paths are tried -
     *  the manager reached from the continuum, the same from the simulation, and a scan of every
     *  registered property whose class name mentions Density - and the value is compared below
     *  against a derived ideal-gas reference so a confound is quantified rather than hidden. The
     *  independent review of 2026-10-03 recommended refusing to save when the density is outside a
     *  few percent; that recommendation is recorded as deliberately not taken, with this reason. */
    private static Double readDensity(PhysicsContinuum physics, Simulation sim, StringBuilder why) {
        MaterialPropertyManager[] managers = new MaterialPropertyManager[2];
        try { managers[0] = physics.get(MaterialPropertyManager.class); } catch (Throwable t) { }
        try { managers[1] = sim.get(MaterialPropertyManager.class); } catch (Throwable t) { }
        for (int i = 0; i < managers.length; i++) {
            if (managers[i] == null) continue;
            Double value = constantFromManager(managers[i], why);
            if (value != null) {
                why.append(i == 0 ? "via:physics.get(MaterialPropertyManager)"
                                  : "via:sim.get(MaterialPropertyManager)");
                return value;
            }
        }
        for (MaterialPropertyManager mpm : managers) {
            if (mpm == null) continue;
            try {
                for (MaterialProperty property : mpm.getMaterialProperties()) {
                    if (!property.getClass().getSimpleName().contains("Density")) continue;
                    try {
                        ConstantMaterialPropertyMethod method =
                                property.getMaterialPropertyMethod(ConstantMaterialPropertyMethod.class);
                        if (method == null) continue;
                        double value = method.getQuantity().getSIValue();
                        if (Double.isFinite(value)) {
                            why.append("via:property scan ").append(property.getClass().getSimpleName());
                            return Double.valueOf(value);
                        }
                    } catch (Throwable ignored) { }
                }
            } catch (Throwable ignored) { }
        }
        why.append("all access paths failed; ");
        return null;
    }

    /** label, value-or-unavailable, and the provenance or failure reason in a third column. */
    private static void densityRow(StringBuilder r, String label, Double value, String why) {
        r.append(label).append('\t');
        r.append(value == null ? "unavailable" : fmt(value.doubleValue()));
        r.append('\t').append(why).append('\n');
    }

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        StringBuilder report = new StringBuilder();
        try {
            String dirPath = System.getenv("AIR3B_JOB_DIR");
            if (dirPath == null || !dirPath.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))
                throw new IllegalStateException("Invalid isolated job");
            File dir = new File(dirPath);
            File output = new File(dir, envText("AIR3B_VARIANT_OUTPUT", "mrf_variant_cd.sim"));
            if (output.exists()) throw new IllegalStateException("Variant output already exists");

            int expectedIteration = (int) env("AIR3B_EXPECTED_LOADED_ITERATION", 1240.0);
            int iteration = sim.getSimulationIterator().getCurrentIteration();
            report.append("ITERATION\t").append(iteration).append('\n');
            report.append("EXPECTED_ITERATION\t").append(expectedIteration).append('\n');
            if (iteration != expectedIteration)
                throw new IllegalStateException("Loaded iteration is " + iteration + " but this experiment is"
                        + " defined on " + expectedIteration + "; refusing to mutate an unknown state");

            int regions = sim.getRegionManager().getObjects().size();
            int interfaces = sim.getInterfaceManager().getObjects().size();
            report.append("REGIONS\t").append(regions).append('\n');
            report.append("INTERFACES\t").append(interfaces).append('\n');
            if (regions != 2 || interfaces != 3)
                throw new IllegalStateException("Expected two regions and three interfaces, found "
                        + regions + " and " + interfaces);

            List<PhysicsContinuum> continua = new ArrayList<>();
            for (Continuum c : sim.getContinuumManager().getObjects())
                if (c instanceof PhysicsContinuum) continua.add((PhysicsContinuum) c);
            if (continua.size() != 1)
                throw new IllegalStateException("Expected exactly one physics continuum, found " + continua.size());
            PhysicsContinuum physics = continua.get(0);
            report.append("CONTINUUM\t").append(physics.getPresentationName()).append('\n');
            report.append("REFERENCE_PRESSURE_PA\t")
                  .append(physics.getReferenceValues().get(ReferencePressure.class).getSIValue()).append('\n');

            // "Keep Coupled unchanged" is a premise of the experiment, so it is asserted rather than
            // assumed: without the coupled implicit solver this run is not comparable with A1/A2.
            // hasSolver(Class) returns the solver or null (it is not a boolean) - the same shape as
            // ModelManager.hasModel, which the project already recorded as an API trap.
            SolverManager solvers = sim.getSolverManager();
            CoupledImplicitSolver solver = solvers.hasSolver(CoupledImplicitSolver.class);
            if (solver == null)
                throw new IllegalStateException("The coupled implicit solver is absent; the 'Coupled unchanged'"
                        + " premise of this experiment does not hold");
            AMGLinearSolver amg = solver.getAMGLinearSolver();
            if (amg == null) throw new IllegalStateException("The coupled solver has no AMG linear solver");
            CourantNumberController courant = solver.getCourantNumberController();

            TreeSet<String> before = modelNames(physics);
            for (String name : before) report.append("BEFORE_MODEL\t").append(name).append('\n');
            boolean beforeIdeal = before.contains("IdealGasModel");
            boolean beforeEnergy = before.contains("CoupledEnergyModel");
            boolean beforeCd = before.contains("ConstantDensityModel");
            boolean beforeCoupled = before.contains("CoupledFlowModel");
            report.append("BEFORE_DENSITY_MODEL\t").append(beforeIdeal ? "IdealGasModel"
                    : (beforeCd ? "ConstantDensityModel" : "unknown")).append('\n');
            report.append("BEFORE_ENERGY_PRESENT\t").append(beforeEnergy).append('\n');
            report.append("BEFORE_COUPLED_FLOW_PRESENT\t").append(beforeCoupled).append('\n');
            StringBuilder densityWhyBefore = new StringBuilder();
            Double beforeDensity = readDensity(physics, sim, densityWhyBefore);
            densityRow(report, "BEFORE_MATERIAL_DENSITY_KG_M3", beforeDensity, densityWhyBefore.toString());

            double beforeTol = amg.getConvergeTol();
            String beforeCourantOption = String.valueOf(courant.getSelectedCourantOption());
            double beforeCfl = courant.getCFL();
            report.append("BEFORE_AMG_CONVERGE_TOL\t").append(fmt(beforeTol)).append('\n');
            report.append("BEFORE_AMG_MAX_CYCLES\t").append(amg.getMaxCycles()).append('\n');
            report.append("BEFORE_COURANT_OPTION\t").append(beforeCourantOption).append('\n');
            report.append("BEFORE_COURANT_CFL\t").append(fmt(beforeCfl)).append('\n');

            // The checkpoint must be the compressible baseline, otherwise the "one axis changed"
            // comparison this trial exists to make would be against something else entirely.
            if (!beforeIdeal || !beforeEnergy || !beforeCoupled || beforeCd)
                throw new IllegalStateException("The checkpoint is not the expected compressible baseline"
                        + " (IdealGasModel=" + beforeIdeal + ", CoupledEnergyModel=" + beforeEnergy
                        + ", CoupledFlowModel=" + beforeCoupled + ", ConstantDensityModel=" + beforeCd + ")");

            report.append("COMPAT_CONSTANT_DENSITY_BEFORE\t")
                  .append(physics.isModelCompatible(CD)).append('\n');
            report.append("COMPAT_COUPLED_FLOW\t").append(physics.isModelCompatible(
                    "star.coupledflow.CoupledFlowModel")).append('\n');

            // Order: release the compressible density model first, then occupy that slot with Constant
            // Density, then drop the energy equation. IdealGasModel and ConstantDensityModel are
            // MUTUALLY EXCLUSIVE density models (both are star.flow.EquationOfStateModel subclasses;
            // javap-verified, recorded in variant-a3/star2502-constant-density-api-notes.json), so the
            // disable-then-enable order is the one that works; it also has precedent in this repository
            // (test-evidence/star-mrf-prism/StarMrfSolve.java:224-244). This is stated as an observed
            // ordering, not as a general claim about how model slots work. Each step is presence-guarded
            // (see has()) and records whether it acted or was already in the requested state, so the
            // mechanism is auditable rather than inferred from the outcome.
            try {
                if (has(physics, "IdealGasModel")) {
                    physics.disable(IDEAL);
                    report.append("STEP\tdisable_ideal_gas\tok\n");
                } else {
                    report.append("STEP\tdisable_ideal_gas\tskipped:already_absent\n");
                }
            } catch (Throwable t) {
                report.append("STEP\tdisable_ideal_gas\tfailed:")
                      .append(String.valueOf(t.getMessage()).replace('\n', ' ').replace('\t', ' ')).append('\n');
                throw new IllegalStateException("Could not disable Ideal Gas: " + t.getMessage(), t);
            }
            report.append("COMPAT_CONSTANT_DENSITY_AFTER_IDEAL_GAS_OFF\t")
                  .append(physics.isModelCompatible(CD)).append('\n');
            try {
                if (!has(physics, "ConstantDensityModel")) {
                    physics.enable(ConstantDensityModel.class);
                    report.append("STEP\tenable_constant_density\tok\n");
                } else {
                    // Unreachable from a checkpoint that passed the provenance gate above (which
                    // requires ConstantDensityModel to be ABSENT before the change), so this row can
                    // never corroborate anything; it is kept only so a future caller that loosens that
                    // gate cannot get a silent no-op.
                    report.append("STEP\tenable_constant_density\tskipped:already_present\n");
                }
            } catch (Throwable t) {
                report.append("STEP\tenable_constant_density\tfailed:")
                      .append(String.valueOf(t.getMessage()).replace('\n', ' ').replace('\t', ' ')).append('\n');
                throw new IllegalStateException("Could not enable Constant Density: " + t.getMessage(), t);
            }
            try {
                if (has(physics, "CoupledEnergyModel")) {
                    physics.disable(COUPLED_ENERGY);
                    report.append("STEP\tdisable_coupled_energy\tok\n");
                } else {
                    // Reached when enabling Constant Density already dropped the energy equation.
                    // Recorded explicitly so a one-toggle outcome cannot be misread as two.
                    report.append("STEP\tdisable_coupled_energy\tskipped:already_absent\n");
                }
            } catch (Throwable t) {
                report.append("STEP\tdisable_coupled_energy\tfailed:")
                      .append(String.valueOf(t.getMessage()).replace('\n', ' ').replace('\t', ' ')).append('\n');
                throw new IllegalStateException("Could not disable Coupled Energy: " + t.getMessage(), t);
            }

            TreeSet<String> after = modelNames(physics);
            for (String name : after) report.append("AFTER_MODEL\t").append(name).append('\n');
            StringBuilder densityWhyAfter = new StringBuilder();
            Double afterDensity = readDensity(physics, sim, densityWhyAfter);
            densityRow(report, "AFTER_MATERIAL_DENSITY_KG_M3", afterDensity, densityWhyAfter.toString());

            // The density the Constant Density model will actually use decides the Reynolds number of
            // this run, so it is compared here against the density the compressible state implies at
            // the reference conditions. The comparison quantity is DERIVED, not measured: R_air is an
            // assumed textbook constant and T_ref comes from the case spec, so the row says so. It is
            // recorded as an assessment rather than made a gate on saving, for the reason stated on
            // readDensity(): the density read is the least API-certain call in this macro and this
            // job is irreversible, so an unreadable or unexpected density must narrow the conclusion
            // rather than destroy the physics answer. The independent review recommended a bounded
            // gate; that recommendation is recorded as deliberately declined, not silently dropped.
            double referenceTemperature = env("AIR3B_REFERENCE_TEMPERATURE_K", 300.0);
            double airGasConstant = 287.05;
            double referenceDensity = physics.getReferenceValues().get(ReferencePressure.class).getSIValue()
                    / (airGasConstant * referenceTemperature);
            report.append("REFERENCE_TEMPERATURE_K\t").append(fmt(referenceTemperature))
                  .append("\tfrom AIR3B_REFERENCE_TEMPERATURE_K; the case spec's inlet static temperature\n");
            report.append("REFERENCE_IDEAL_GAS_DENSITY_KG_M3\t").append(fmt(referenceDensity))
                  .append("\tDERIVED p_ref/(R_air*T_ref) with R_air=").append(fmt(airGasConstant))
                  .append(" J/kg/K assumed; NOT a measurement\n");
            if (afterDensity == null) {
                report.append("ASSESS\tdensity_comparison\tunavailable: the constant density could not be read,"
                        + " so whether this run also changed the Reynolds number is UNDETERMINED\n");
                report.append("ASSESS\tdensity_offset_pct\tunavailable\n");
            } else {
                double offsetPct = 100.0 * (afterDensity.doubleValue() - referenceDensity) / referenceDensity;
                report.append("ASSESS\tdensity_comparison\tperformed\n");
                report.append("ASSESS\tdensity_offset_pct\t").append(fmt(offsetPct)).append('\n');
                report.append("ASSESS\tdensity_confound\t")
                      .append(Math.abs(offsetPct) <= 2.0 ? "within_2pct_density_axis_clean"
                              : "outside_2pct_reynolds_number_also_changed")
                      .append('\n');
            }
            // Disabling the energy equation also deactivates every temperature-type condition on the
            // boundaries and in the initial conditions. That is inseparable from the energy toggle, so
            // it is inside the axis this experiment declares - but it is recorded rather than left
            // implicit, because the same side effect already caused a defect in this repository's
            // history (a temperature set before energy was enabled silently did not apply).
            report.append("NOTE\tdisabling_coupled_energy_also_deactivates_temperature_conditions\ttrue\n");
            report.append("NOTE\tdensity_recorded_not_hard_gated\treviewer recommended refusing to save outside"
                    + " 2%; declined because the read is the least API-certain call and the single scoped job is"
                    + " irreversible - the confound is quantified in ASSESS rows instead\n");
            report.append("AFTER_DENSITY_MODEL\t").append(after.contains("ConstantDensityModel")
                    ? "ConstantDensityModel" : (after.contains("IdealGasModel") ? "IdealGasModel" : "unknown"))
                  .append('\n');
            report.append("AFTER_ENERGY_PRESENT\t").append(after.contains("CoupledEnergyModel")).append('\n');
            report.append("AFTER_COUPLED_FLOW_PRESENT\t").append(after.contains("CoupledFlowModel")).append('\n');

            double afterTol = amg.getConvergeTol();
            String afterCourantOption = String.valueOf(courant.getSelectedCourantOption());
            double afterCfl = courant.getCFL();
            report.append("AFTER_AMG_CONVERGE_TOL\t").append(fmt(afterTol)).append('\n');
            report.append("AFTER_AMG_MAX_CYCLES\t").append(amg.getMaxCycles()).append('\n');
            report.append("AFTER_COURANT_OPTION\t").append(afterCourantOption).append('\n');
            report.append("AFTER_COURANT_CFL\t").append(fmt(afterCfl)).append('\n');

            boolean cdEnabled = after.contains("ConstantDensityModel");
            boolean idealGone = !after.contains("IdealGasModel");
            boolean energyGone = !after.contains("CoupledEnergyModel");
            boolean coupledKept = after.contains("CoupledFlowModel");
            boolean noSegregated = !after.contains("SegregatedFlowModel") && !after.contains("SegregatedEnergyModel");
            boolean steadyKept = after.contains("SteadyModel") && after.contains("ThreeDimensionalModel");
            boolean turbulenceKept = after.contains("TurbulentModel") && after.contains("RansTurbulenceModel")
                    && after.contains("KOmegaTurbulence") && after.contains("SstKwTurbModel")
                    && after.contains("KwAllYplusWallTreatment");
            // The solver axis is not touched here, and this is the executable proof of that: the
            // AMG tolerance, the Courant option and the CFL must read back bit-identical to BEFORE.
            boolean solverUnchanged = (afterTol == beforeTol)
                    && afterCourantOption.equals(beforeCourantOption)
                    && Math.abs(afterCfl - beforeCfl) <= 1e-12 * Math.max(1.0, Math.abs(beforeCfl));
            boolean applied = cdEnabled && idealGone && energyGone && coupledKept
                    && solverUnchanged && noSegregated && steadyKept && turbulenceKept;

            report.append("REQUESTED\taxis\tphysical_model\n");
            report.append("REQUESTED\tdensity_model\tConstantDensity\n");
            report.append("REQUESTED\tenergy\tdisabled\n");
            report.append("REQUESTED\tflow_model\tCoupledFlow_unchanged\n");
            report.append("GATE\tconstant_density_enabled\t").append(cdEnabled).append('\n');
            report.append("GATE\tideal_gas_disabled\t").append(idealGone).append('\n');
            report.append("GATE\tcoupled_energy_disabled\t").append(energyGone).append('\n');
            report.append("GATE\tcoupled_flow_retained\t").append(coupledKept).append('\n');
            report.append("GATE\tno_segregated_model\t").append(noSegregated).append('\n');
            report.append("GATE\tsteady_and_turbulence_retained\t").append(steadyKept && turbulenceKept).append('\n');
            report.append("GATE\tsolver_axis_unchanged\t").append(solverUnchanged).append('\n');
            report.append("GATE\trequested_change_applied\t").append(applied).append('\n');
            report.append("COMPLETE_MARKER\t").append(COMPLETE).append('\n');

            // Evidence first: a failed save must still leave the physics readback on disk.
            try (Writer w = new OutputStreamWriter(
                    new FileOutputStream(new File(dir, "air3b_physics_variant.tsv")), StandardCharsets.UTF_8)) {
                w.write(report.toString());
            }
            if (!applied)
                throw new IllegalStateException("A requested physics change did not take effect on readback");

            sim.saveState(output.getAbsolutePath());
            System.out.println(COMPLETE);
        } catch (Throwable e) {
            try {
                File dir = new File(System.getenv("AIR3B_JOB_DIR"));
                try (Writer w = new OutputStreamWriter(
                        new FileOutputStream(new File(dir, "air3b_physics_variant.tsv")), StandardCharsets.UTF_8)) {
                    w.write(report.toString());
                    w.write("FAILED\t" + e.getClass().getName() + "\t"
                            + String.valueOf(e.getMessage()).replace('\n', ' ').replace('\t', ' ') + "\n");
                }
            } catch (Throwable ignored) { }
            throw new RuntimeException("AIR-3B physics variant configuration failed: " + e.getMessage(), e);
        }
    }
}
