# -*- coding: utf-8 -*-
"""②R10 regression tests: the direct geometry-only .sim entry, the ③ six-stage transient chain, the
measured-rotation evidence, the final-checkpoint readback and the three-conclusion summary adapter.

Why these tests exist (AGENTS.md section 2-O9: a review finding must become an executable test, not
prose). Every gate added in ②R10 was added because a previous round's evidence showed the weaker check
passing something it should not have:

1. ②'s chain started by IMPORTING the CAD files, so "starts from a geometry .sim" was a claim about the
   `.sim` the geometry stage happened to save, not about the entry state. The entry is now a registered,
   hash-pinned geometry-only `.sim` and the import path is forbidden on it; the tests pin both the
   pipeline declaration and the macro's refusal to hash the file STAR has open.
2. The ③ chain's checkpoint handoff used to be checked only as "the clock is > 0". A mis-chained stage
   could hand over any transient state. It is now checked against the PREVIOUS SEGMENT'S OWN RECORD;
   the tests pin that, and pin that the assertion runs before the solution is touched.
3. Rotation used to be reported as `physical_time x rpm` with the measurement absent, and a boundary
   NAME was treated as proof that the chosen surface had a meaningful azimuth. The tests pin the radius
   floor, the real-displacement gate, the radius-preservation check and the derived-vs-measured split.
4. The final checkpoint had no independent readback of the physical clock or of the RotatingMotion's
   actual parameters, and the shared candidate's state check did not compare the initialization flag.
   The readback now lives in this branch's own macro; one test proves the SHARED candidate was left
   untouched (its bytes still equal the hashes two other gates pin), so extending it by accident - and
   silently invalidating those gates - cannot pass unnoticed.

These are structural/source tests because the macros are Java and cannot be executed here; the repo
already uses source-level contract tests for exactly this reason (tests/test_stage_plan.py,
tests/test_air3b_p_e2e.py). Nothing here re-implements a gate: citability is asked of
scripts/run_record_gate.py itself, and the summary adapter is executed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import run_record_gate as gate                     # noqa: E402
from stage_plan import StagePlanError, plan_pipeline  # noqa: E402

MACROS = ROOT / 'macros'
CASE = ROOT / 'cases' / 'air-three-blade'
PIPELINE_2 = CASE / 'pipeline-e2e-geometry-to-validation.json'
PIPELINE_3 = CASE / 'pipeline-transient-revolution.json'
INPUTS = CASE / 'e2e-inputs.json'
SUMMARY = CASE / 'air3b_summary.py'
R8_EVIDENCE = ROOT / 'test-evidence' / 'air-three-blade' / 'p-e2e' / 'run-a'
R8_TRIAL = 'air3b-mrf-e2e-20261004a'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GeometryOnlySimEntry(unittest.TestCase):
    """②'s entry state must be R8's geometry-only .sim, bound by its real hash, and nothing may import."""

    def setUp(self):
        self.pipeline = json.loads(PIPELINE_2.read_text(encoding='utf-8'))
        self.stages = {s['name']: s for s in self.pipeline['stages']}
        self.mapping = json.loads(INPUTS.read_text(encoding='utf-8'))
        self.source = read(MACROS / 'ImportAir3bBlockGeometry.java')

    def test_the_entry_is_a_registered_geometry_only_sim(self):
        geometry = self.stages['geometry']
        self.assertEqual(geometry.get('input'), 'block_geometry.sim')
        self.assertIsNone(geometry.get('output'),
                          'the audit stage writes no new checkpoint; declaring one would be a lie')
        self.assertEqual(geometry.get('macro_outputs'), [None])

    def test_the_registered_input_exists_with_the_pinned_hash(self):
        pinned = self.pipeline['input_hashes']['block_geometry.sim']
        listed = [rel for rel in self.mapping['inputs'] if rel.endswith('block_geometry.sim')]
        self.assertEqual(len(listed), 1, 'the geometry-only .sim must be listed exactly once')
        path = ROOT / listed[0]
        self.assertTrue(path.is_file(), 'the registered geometry-only .sim is missing')
        self.assertEqual(sha256(path), pinned, 'the registered .sim does not match its own pin')
        self.assertEqual(self.mapping['expected_basenames']['block_geometry.sim'], pinned)

    def test_the_entry_binds_r8s_actual_artefact(self):
        # The whole point of "bind R8's actual input hash": the pin must be the hash of the file R8's own
        # run produced, not a re-export. That file is still in the R8 evidence directory.
        r8 = R8_EVIDENCE / R8_TRIAL / 'block_geometry.sim'
        if not r8.is_file():
            self.skipTest('R8 evidence for the geometry-only checkpoint is not checked in')
        self.assertEqual(sha256(r8), self.pipeline['input_hashes']['block_geometry.sim'],
                         'the registered entry is not R8s own geometry-only checkpoint')
        audit = json.loads((R8_EVIDENCE / R8_TRIAL / 'block_geometry_audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['output_sim_sha256'], self.pipeline['input_hashes']['block_geometry.sim'])

    def test_the_cad_provenance_is_asserted_but_never_imported(self):
        joined = ' '.join(self.stages['geometry']['require_contains'])
        self.assertIn('STEP_IMPORT_CALLS\t0', joined)
        self.assertIn('GEOMETRY_MODE\taudit_loaded_geometry_sim', joined)
        # The CAD identity is still pinned even though nothing imports it: the STEP files stay
        # registered inputs and both entry macros assert their hashes.
        self.assertIn('ROTOR_STEP_SHA256\t' + self.pipeline['input_hashes']['air3b_rotating_region.step'], joined)
        self.assertIn('STATOR_STEP_SHA256\t' + self.pipeline['input_hashes']['air3b_static_region.step'], joined)
        # The regions stage consumes the same .sim and must independently report zero imports.
        regions = ' '.join(self.stages['regions']['require_contains'])
        self.assertIn('STEP_IMPORT_CALLS\t0', regions)
        self.assertIn('GEOMETRY_SOURCE\tloaded_geometry_only_sim', regions)

    def test_the_certificate_must_carry_the_same_pin_as_the_pipeline(self):
        pinned = self.pipeline['input_hashes']['block_geometry.sim']
        joined = ' '.join(self.stages['geometry']['require_contains'])
        self.assertIn('INPUT_SIM_SHA256_PIN\t' + pinned, joined)
        self.assertIn('"input_sim_sha256_pin":"' + pinned + '"', joined)
        self.assertIn('"step_import_calls":0', joined)
        self.assertIn('"measured_regions":0', joined)
        self.assertIn('"measured_interfaces":0', joined)
        self.assertIn('"measured_iteration":0', joined)

    def test_the_audit_path_does_not_hash_the_loaded_checkpoint(self):
        """A second hash of the file STAR holds open would add a failure mode, not a check.

        The runner has already hashed the exact bytes it handed to STAR and recorded them in
        stage-<name>.json, so the macro records the pin and the file's measured LENGTH on that path.
        """
        self.assertIn('w.println("INPUT_SIM_BYTES\\t" + out.length());', self.source)
        self.assertIn('w.println("INPUT_SIM_SHA256_PIN\\t" + inputPin);', self.source)
        self.assertEqual(self.source.count('sha(out)'), 2,
                         'sha(out) must appear exactly once in the legacy TSV and once in the legacy'
                         ' certificate writer, and nowhere on the audit path')
        self.assertEqual(self.source.count('"OUTPUT_SHA256\\t" + sha(out)'), 1)
        self.assertIn('AIR3B_GEOMETRY_SIM_SHA256', self.source)
        self.assertIn('matches("[a-f0-9]{64}")', self.source)

    def test_the_input_identity_gate_is_a_name_check_not_a_physical_threshold(self):
        self.assertIn('AIR3B_GEOMETRY_EXPECTED_PART_NAMES', self.source)
        self.assertIn('Loaded parts are not the reviewed ones', self.source)
        # No measured tessellation VALUE may be pinned by a content gate (a CadPart's tessellation is not
        # guaranteed byte-stable across sessions, so pinning it would turn bookkeeping into a failure).
        for entry in self.stages['geometry']['require_contains']:
            self.assertNotIn('triangles=', entry, 'a triangle-count value is pinned: %r' % entry)


class TransientChainDeclaration(unittest.TestCase):
    """③ must be exactly six stages with the two-segment checkpoint handoff declared per macro."""

    def setUp(self):
        self.pipeline = json.loads(PIPELINE_3.read_text(encoding='utf-8'))
        self.stages = {s['name']: s for s in self.pipeline['stages']}

    def test_six_stages_in_order(self):
        self.assertEqual([s['name'] for s in self.pipeline['stages']],
                         ['geometry', 'regions', 'mesh', 'physics', 'transient', 'validation'])

    def test_the_transient_stage_declares_the_two_segment_handoff(self):
        stage = self.stages['transient']
        self.assertEqual(stage['macros'],
                         ['RunAir3bTransientRevolution.java', 'RunAir3bTransientRevolution.java'])
        self.assertEqual(stage['macro_outputs'], ['mrf_transient_s1.sim', 'mrf_transient_s2.sim'])
        self.assertEqual(stage['output'], 'mrf_transient_s2.sim')
        self.assertEqual(stage['input'], 'mrf_physics.sim')
        for name in ('mrf_transient_s1.sim', 'mrf_transient_s2.sim'):
            self.assertIn(name, stage['required'], 'a declared output must be a verified artefact')

    def test_a_two_macro_stage_without_macro_outputs_is_refused(self):
        broken = json.loads(json.dumps(self.pipeline))
        for stage in broken['stages']:
            if stage['name'] == 'transient':
                stage.pop('macro_outputs')
        with self.assertRaises(StagePlanError):
            plan_pipeline(broken)

    def test_the_declared_time_steps_are_two_degrees_at_ten_thousand_rpm(self):
        env = self.pipeline['env']
        self.assertAlmostEqual(float(env['AIR3B_TRANSIENT_DT_S']),
                               (2.0 / 360.0) / (float(env['AIR3B_RPM']) / 60.0), places=18)
        self.assertEqual(env['AIR3B_TRANSIENT_STEPS'], '2')
        self.assertEqual(env['AIR3B_INNER_ITERATIONS'], '10')
        self.assertEqual(env['AIR3B_TRANSIENT_CHUNK'], '1',
                         'the chunk must be explicitly one step so the readback is exact')

    def test_the_plan_is_valid_and_within_the_launcher_cap(self):
        plan_pipeline(self.pipeline)
        total = sum(s['timeout_seconds'] for s in self.pipeline['stages'])
        self.assertLessEqual(total, 1740, 'declared stage deadlines %d s exceed the launcher cap' % total)

    def test_the_chain_is_continuous_and_the_validation_stage_is_read_only(self):
        produced = set()
        for index, stage in enumerate(self.pipeline['stages']):
            if index:
                self.assertIsNotNone(stage.get('input'), 'stage %s declares no input' % stage['name'])
                self.assertTrue(stage['input'] in produced
                                or stage['input'] in self.pipeline['input_hashes'],
                                'stage %s reads %s, which nothing produced' % (stage['name'], stage['input']))
            current = stage.get('input')
            for position, _macro in enumerate(stage['macros']):
                outputs = stage.get('macro_outputs')
                declared = outputs[position] if outputs else stage.get('output')
                if declared:
                    produced.add(declared)
                    current = declared
        self.assertEqual(self.stages['validation']['input'], 'mrf_transient_s2.sim')
        self.assertEqual(self.stages['validation']['macro_outputs'], [None, None])
        self.assertIsNone(self.stages['validation']['output'])
        self.assertEqual(self.stages['validation']['macros'],
                         ['AuditAir3bMassBalance.java', 'AuditAir3bResidualProvenance.java'])

    def test_the_stage_gates_name_the_handoff_and_the_rotation_evidence(self):
        joined = ' '.join(self.stages['transient']['require_contains'])
        for needle in ('"segment_index":"s1"', '"segment_index":"s2"', '"initialized_here":true',
                       '"initialized_here":false', '"motion_reused_from_checkpoint":true',
                       '"steps_declared":2', '"steps_executed":2', '"chunk_steps":1',
                       '"gate_real_displacement":true', '"gate_angle_matches_expected":true',
                       '"gate_chord_matches_expected":true', '"gate_radius_preserved":true',
                       '"gate_steps_declared_match":true', '"gate_clock_matches_steps":true',
                       '"handoff_recorded_time_s":', '"measurement_frame":"laboratory (global) frame',
                       '"blade_candidate_offaxis_radius_m":', '"convergence_verified":false'):
            self.assertIn(needle, joined, 'the transient stage does not gate %s' % needle)
        numeric = ' '.join(self.stages['transient']['require_numeric'])
        self.assertIn('measured_rotation_angle_deg>=0.5', numeric)
        self.assertIn('measured_chord_m>=0.0005', numeric)


class RotationEvidenceIsMeasuredAndChecked(unittest.TestCase):
    """Source-level contract for the rotation measurement: what it reads, and what it refuses."""

    def setUp(self):
        self.source = read(MACROS / 'RunAir3bTransientRevolution.java')

    def test_the_handoff_is_compared_against_the_previous_segments_record(self):
        self.assertIn('air3b_transient_s1_measurements.json', self.source)
        self.assertIn('physical_time_reached_s', self.source)
        self.assertIn('handoffRecordedTime', self.source)
        self.assertIn('the checkpoint handoff does not match', self.source)
        # A record that does not certify itself cannot be used as the handoff proof.
        self.assertIn('does not certify the handoff', self.source)

    def test_the_handoff_is_checked_before_the_solution_is_touched(self):
        handoff = self.source.index('the checkpoint handoff does not match')
        initialize = self.source.index('initializeSolution()')
        stepping = self.source.index('.step(chunkSteps)')
        self.assertLess(handoff, initialize, 'the handoff is checked after the solution is initialised')
        self.assertLess(handoff, stepping, 'the handoff is checked after stepping has begun')

    def test_a_zero_displacement_cannot_pass(self):
        self.assertIn('realDisplacement', self.source)
        self.assertIn('measuredChord > 0.0', self.source)
        self.assertIn('gate_real_displacement', self.source)
        self.assertIn('measuredChord', self.source)
        # ②R10 independent review C11: a gate that is COMPUTED and then dropped from the conjunction
        # would still satisfy every assertion above, and "computed but never used" is a defect this
        # project has already shipped twice. So the conjunction itself is checked: every rotation gate
        # must be a member of `allGates`, which is what sets `status` and throws.
        conjunction = self.source[self.source.index('boolean allGates ='):]
        conjunction = conjunction[:conjunction.index(';')]
        for gate in ('finite', 'advanced', 'stepsDeclaredMatch', 'clockMatchesSteps', 'iterationAdvanced',
                     'finiteMeasurement', 'angleMatches', 'chordMatches', 'realDisplacement',
                     'radiusPreserved'):
            self.assertIn(gate, conjunction, 'gate %s is computed but not part of allGates' % gate)
        self.assertIn('String status = allGates ? "ok" : "gate_failed"', self.source)
        self.assertIn('if (!allGates)', self.source)

    def test_the_centroid_must_be_off_axis(self):
        self.assertIn('minMeasureRadius', self.source)
        self.assertIn('radiusBefore >= minMeasureRadius', self.source)
        self.assertIn('MEASURE_MIN_RADIUS_M', self.source)

    def test_the_radius_is_required_to_be_preserved_by_the_rotation(self):
        # This is what pins the measurement to the lab frame: in a co-rotating frame the centroid would
        # not move at all, and the chord/angle gates would already fail, but the radius check states the
        # frame assumption explicitly.
        self.assertIn('radiusPreserved', self.source)
        self.assertIn('radiusAfter', self.source)

    def test_the_axis_origin_and_rate_are_readback_and_checked_on_both_paths(self):
        self.assertIn('motionReused', self.source)
        self.assertIn('originDeflection', self.source)
        self.assertIn('originTolM', self.source)
        self.assertIn('is not the requested', self.source)
        self.assertIn('Rotation origin readback is not finite', self.source)
        self.assertIn('axisFinite', self.source)
        # The create path also sets them, so the check compares the request against something this macro
        # defined rather than against a build default.
        self.assertIn('setComponentsAndUnits', self.source)
        self.assertIn('AIR3B_TRANSIENT_ORIGIN_M', self.source)
        self.assertIn('AIR3B_TRANSIENT_AXIS', self.source)

    def test_the_steps_and_the_clock_are_read_back_not_assumed(self):
        self.assertIn('stepsExecuted', self.source)
        self.assertIn('stepsFromClock', self.source)
        self.assertIn('innerIterationsDelta', self.source)
        self.assertIn('gate_steps_declared_match', self.source)
        self.assertIn('gate_clock_matches_steps', self.source)

    def test_the_derived_angle_is_labelled_and_is_not_the_evidence(self):
        self.assertIn('derived_rotation_angle_deg', self.source)
        self.assertIn('measured_rotation_angle_deg', self.source)
        self.assertIn('NOT a measurement', self.source)
        self.assertIn('physical_time x rpm', self.source)

    def test_a_failed_gate_is_written_as_gate_failed_and_throws(self):
        self.assertIn('gate_failed', self.source)
        self.assertIn('String status = allGates ? "ok" : "gate_failed"', self.source)
        self.assertIn('if (!allGates)', self.source)
        # The checkpoint and the records are written BEFORE the throw, so a failing segment still leaves
        # its evidence on disk instead of vanishing with the exception.
        self.assertLess(self.source.index('sim.saveState(output.getAbsolutePath())'),
                        self.source.index('if (!allGates)'),
                        'the checkpoint is saved after the gate throw, so a failure would lose it')

    def test_a_near_axis_boundary_is_refused_rather_than_substituted(self):
        self.assertIn('refusing to substitute an axisymmetric surface', self.source)
        self.assertIn('blade_candidate_offaxis_radius_m', self.source)

    def test_no_per_macro_environment_override_survives(self):
        # One environment for the whole pipeline cannot mean two different things for two segments, so
        # these overrides are gone rather than merely documented: no lookup may remain. (The names may
        # still appear in the comment that says they were removed.)
        for removed in ('AIR3B_TRANSIENT_OUTPUT', 'AIR3B_TRANSIENT_INDEX', 'AIR3B_PHYSICAL_TIME_S'):
            self.assertNotIn('getenv("%s")' % removed, self.source,
                             '%s can silently make both segments collide' % removed)
        self.assertIn('AIR3B_TRANSIENT_STEPS', self.source)
        self.assertIn('mrf_transient_s1.sim', self.source)


class FinalCheckpointReadback(unittest.TestCase):
    """The ②-owned audit must read back the final state and refuse to certify a disturbed one."""

    def setUp(self):
        self.source = read(MACROS / 'AuditAir3bMassBalance.java')
        self.pipeline2 = json.loads(PIPELINE_2.read_text(encoding='utf-8'))
        self.pipeline3 = json.loads(PIPELINE_3.read_text(encoding='utf-8'))

    def test_it_reads_the_physical_clock_and_the_rotating_parameters(self):
        for needle in ('PHYSICAL_TIME_BEFORE_S', 'PHYSICAL_TIME_AFTER_S', 'getPhysicalTime()',
                       'RotatingMotion', 'UserRotatingReferenceFrame', 'getAxisVector',
                       'getOriginVector', 'getRotationRate', 'ROTATING_OBJECT'):
            self.assertIn(needle, self.source, 'the final-state readback is missing %s' % needle)

    def test_it_requires_the_state_to_be_unchanged(self):
        for needle in ('stateUnchanged', 'iterationUnchanged', 'initializationUnchanged',
                       'clockUnchanged', 'meshUnchanged', 'interfacesUnchanged', 'rotatingUnchanged',
                       'changed the state it was measuring'):
            self.assertIn(needle, self.source, 'the invariance check is missing %s' % needle)

    def test_the_initialization_flag_is_compared(self):
        # The shared candidate compared the iteration and the modified flag but NOT the initialization
        # flag; that gap is the specific thing this readback closes.
        self.assertIn('initializedBefore', self.source)
        self.assertIn('initializedAfter', self.source)
        self.assertIn('initialization_unchanged', self.source)

    def test_the_existing_certificate_contract_is_preserved(self):
        # ② already cites this record, so its status and its declared measured key must survive.
        self.assertIn('\\"status\\":\\"ok\\"', self.source)
        self.assertIn('measured_signed_relative', self.source)
        self.assertIn('air3b_massbalance.json', self.source)
        for needle in ('mass_imbalance_within_limit', 'sign_convention_as_expected',
                       'abs_inlet_minus_abs_outlet_kg_per_s', 'AIR3B_MASSBALANCE_AUDIT_COMPLETE'):
            self.assertIn(needle, self.source)

    def test_the_readback_is_gated_in_both_pipelines(self):
        for pipeline, label in ((self.pipeline2, 'scenario 2'), (self.pipeline3, 'scenario 3')):
            validation = [s for s in pipeline['stages'] if s['name'] == 'validation'][0]
            joined = ' '.join(validation['require_contains'])
            for needle in ('GATE\trotation_parameters_finite\ttrue', 'GATE\tloads_finite\ttrue',
                           'GATE\tstate_unchanged\ttrue', 'GATE\tclock_unchanged\ttrue',
                           'GATE\tinitialization_unchanged\ttrue', 'GATE\titeration_unchanged\ttrue',
                           '"state_unchanged":true', '"loads_finite":true'):
                self.assertIn(needle, joined, '%s does not gate %s' % (label, needle))

    def test_the_transient_pipeline_demands_the_actual_rotating_motion(self):
        validation = [s for s in self.pipeline3['stages'] if s['name'] == 'validation'][0]
        joined = ' '.join(validation['require_contains'])
        self.assertIn('"rotating_motion_count":1', joined)
        self.assertIn('MOTION\tAIR3B_RBM\t', joined)

    def test_the_shared_physical_readiness_candidate_was_not_touched(self):
        """Extending it would silently invalidate two other gates that pin its bytes.

        AGENTS.md section 6 requires the review gate's five hashes to be compared by code; these two
        registries pin this macro by hash, and a ②R10 edit to it would make both refuse. This test makes
        that consequence explicit instead of leaving it to a reviewer to notice.
        """
        source = read(ROOT / 'scripts' / 'restricted_local_mesh_trial.py')
        registry = re.search(r"'physics': \('AuditPhysicalReadiness\.java', 'trial_physical_readiness\.ps1',\s*'([a-f0-9]{64})'\)", source)
        self.assertIsNotNone(registry, 'the physics candidate registry no longer pins a hash')
        runner = read(ROOT / 'scripts' / 'security' / 'trial_physical_readiness.ps1')
        runner_hash = re.search(r"\$c\.macro_sha256 -ne '([a-f0-9]{64})'", runner)
        self.assertIsNotNone(runner_hash, 'the physical-readiness runner no longer pins a hash')
        actual = sha256(MACROS / 'AuditPhysicalReadiness.java')
        self.assertEqual(registry.group(1), actual,
                         'AuditPhysicalReadiness changed; scripts/restricted_local_mesh_trial.py would refuse')
        self.assertEqual(runner_hash.group(1), actual,
                         'AuditPhysicalReadiness changed; trial_physical_readiness.ps1 would refuse')


class ReviewFixRegressions(unittest.TestCase):
    """②R10 independent review, concerns C1/C2/C3/C10: each finding is pinned by a test."""

    def setUp(self):
        self.pipeline3 = json.loads(PIPELINE_3.read_text(encoding='utf-8'))
        self.transient = [s for s in self.pipeline3['stages'] if s['name'] == 'transient'][0]

    def test_c1_the_rules_and_the_executed_security_scripts_are_hash_bound(self):
        # The criterion modules the summary DELEGATES to, and the two security scripts start() uploads
        # and executes, must be compared by code - otherwise their bytes can change what the round
        # reports or runs while every other pin still matches.
        import air3b_pipeline_trial as trial
        support = trial.support_hashes()
        for key in ('scripts/air3b_convergence_gate.py', 'scripts/evaluate_convergence.py',
                    'scripts/security/provision_restricted_worker.ps1',
                    'scripts/security/grant_batch_logon.ps1'):
            self.assertIn(key, support, '%s decides behaviour but is not pinned' % key)
            self.assertEqual(support[key], sha256(ROOT / key))

    def test_c2_every_declared_numerical_setting_is_also_a_gate(self):
        env = self.pipeline3['env']
        joined = ' '.join(self.transient['require_contains'])
        # B2 + NC1 (②R10 re-reviews): the time_step_s gate must not be built from ANY formatter model.
        # jnum -> fmt -> Java String.format(Locale.ROOT, "%.12g") strips nothing but Java KEEPS trailing
        # zeros while Python's %g strips them, so an equality test against either rendering is a latent
        # unsatisfiable gate. The gate therefore carries the DECLARED VALUE'S OWN LEADING DIGITS, taken
        # from the env string this pipeline sets, which every formatter emits first.
        prefix = re.match(r'[0-9.]+', env['AIR3B_TRANSIENT_DT_S']).group(0)[:7]
        self.assertEqual(prefix, '3.33333')
        self.assertLessEqual(len(prefix), 8, 'the prefix is long enough to depend on a formatter again')
        for seg in ('s1', 's2'):
            for filename in ('verdict', 'measurements'):
                self.assertIn('air3b_transient_%s_%s.json::"time_step_s":%s' % (seg, filename, prefix),
                              joined, '%s/%s does not gate the declared step' % (seg, filename))
        self.assertNotIn('"time_step_s":3.3333333333333335', joined,
                         'the gate carries the Double.toString spelling the macro never writes')
        for seg in ('s1', 's2'):
            for needle in ('"inner_iteration_cap":10', '"chunk_steps":1'):
                self.assertIn('air3b_transient_%s_verdict.json::%s' % (seg, needle), joined,
                              '%s/%s is recorded but not gated' % (seg, needle))
        self.assertIn('"chunk_steps":1', joined)
        self.assertIn('air3b_transient_s2_verdict.json::"chunk_steps":1', joined)

    def test_c2b_the_segment_identity_is_gated_on_the_clock_not_the_unpersisted_flag(self):
        # B3 (②R10 re-review): the initialization flag does NOT survive a save/load on this install (a
        # measured project fact), so gating segment 2 on it would fail a correct handoff. What IS gated is
        # the clock-derived identity and the previous segment's own record.
        joined = ' '.join(self.transient['require_contains'])
        self.assertIn('air3b_transient_s1_verdict.json::"initialized_here":true', joined)
        self.assertIn('air3b_transient_s2_verdict.json::"initialized_here":false', joined)
        self.assertIn('air3b_transient_s1_verdict.json::"handoff_recorded":false', joined)
        self.assertIn('air3b_transient_s2_verdict.json::"handoff_recorded":true', joined)
        self.assertIn('air3b_transient_s2_measurements.json::"handoff_recorded_time_s":', joined)
        # The flag is not gated anywhere in this pipeline; the DECISION not to gate it is.
        for entry in self.transient['require_contains']:
            self.assertNotIn('"initialized_before":', entry,
                             'chain identity is gated on a flag this install does not persist: %r' % entry)
        self.assertIn('"initialized_before_gate":"recorded_not_gated', joined)
        # And the macro must not throw on it either.
        source = read(MACROS / 'RunAir3bTransientRevolution.java')
        self.assertNotIn('was handed an uninitialised state', source)
        self.assertNotIn('was handed an already initialised state', source)
        self.assertIn('initializedFlagGate', source)

    def test_c10_the_history_tables_must_carry_their_header(self):
        joined = ' '.join(self.transient['require_contains'])
        for seg in ('s1', 's2'):
            self.assertIn('air3b_transient_%s_history.csv::chunk,iteration,physical_time_s,'
                          'thrust_z_n,torque_z_nm' % seg, joined,
                          'the %s history is required by existence only' % seg)

    def test_c3_the_json_writers_cannot_emit_a_bare_non_finite_number(self):
        # Java writes NaN/Infinity as bare tokens, which RFC 8259 forbids; a record containing one is
        # unparseable for a strict reader and would therefore be uncitable. Both ②R10 certificates that
        # can carry a non-finite value (a steady state with no physical clock; segment 1 with no handoff
        # time) must funnel every number through a helper that falls back to null.
        for name in ('AuditAir3bMassBalance.java', 'RunAir3bTransientRevolution.java'):
            source = read(MACROS / name)
            self.assertIn('private static String jnum(double v)', source, '%s has no JSON-safe number' % name)
            self.assertIn('Double.isFinite(v) ? fmt(v) : "null"', source)
            writer = source[source.index('StringBuilder b = new StringBuilder();'):] \
                if 'StringBuilder b = new StringBuilder();' in source \
                else source[source.index('_measurements.json")'):]
            self.assertNotIn('.append(fmt(', writer, '%s still appends a raw double to JSON' % name)
            self.assertIn('jnum(', writer)
        # Segment 1 has no handoff time, and a null (not a bare NaN) is what the writer must emit.
        source = read(MACROS / 'RunAir3bTransientRevolution.java')
        self.assertIn('(handoffRecorded ? jnum(handoffRecordedTime) : "null")', source)

    def test_c3b_a_nan_bearing_record_is_parseable_only_permissively(self):
        # The reason the guard above exists, demonstrated rather than asserted: a strict RFC 8259 reader
        # refuses a bare NaN, while Python's json accepts it - so whether a certificate is parseable must
        # not depend on which reader looks at it.
        text = '{"status":"ok","handoff_recorded_time_s":NaN}'
        with self.assertRaises(ValueError):
            json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'stage-transient.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'transient', 'state': 'ok',
                'artifacts': ['air3b_transient_s1_measurements.json'], 'macros': []}), encoding='utf-8')
            (d / 'air3b_transient_s1_measurements.json').write_text(text, encoding='utf-8')
            verdict = gate.citation_status(d / 'air3b_transient_s1_measurements.json')
        self.assertEqual(verdict['state'], gate.CITABLE,
                         'the permissive parse is exactly the trap: the shared gate certifies it today')

    def test_the_content_gates_name_keys_the_macros_actually_write(self):
        # Cross-check every JSON KEY a stage gate names against the keys its macro writes, so a typo or a
        # renamed field fails here instead of silently making a gate unsatisfiable.
        writers = {
            'air3b_transient_s1_verdict.json': 'RunAir3bTransientRevolution.java',
            'air3b_transient_s2_verdict.json': 'RunAir3bTransientRevolution.java',
            'air3b_transient_s1_measurements.json': 'RunAir3bTransientRevolution.java',
            'air3b_transient_s2_measurements.json': 'RunAir3bTransientRevolution.java',
            'air3b_massbalance.json': 'AuditAir3bMassBalance.java',
            'block_geometry_audit.json': 'ImportAir3bBlockGeometry.java',
        }
        checked = 0
        for pipeline in (self.pipeline3, json.loads(PIPELINE_2.read_text(encoding='utf-8'))):
            for stage in pipeline['stages']:
                for entry in stage.get('require_contains') or []:
                    head, _, needle = entry.partition('::')
                    if head not in writers or not needle.startswith('"'):
                        continue
                    key = needle[1:needle.index('"', 1)]
                    source = read(MACROS / writers[head])
                    self.assertIn('\\"%s\\":' % key, source,
                                  '%s gates %s but %s never writes that key'
                                  % (stage['name'], entry, writers[head]))
                    checked += 1
        self.assertGreater(checked, 20, 'the cross-check found almost nothing to check')

    def test_json_strings_cannot_carry_control_characters(self):
        # MEASURED ON THIS ROUND'S OWN ② RUN (②R10, O9: a real failure becomes the fixture). The first
        # extended certificate embedded the rotating-object TSV rows verbatim, so raw TAB characters sat
        # inside a JSON string. All six stages were ok and every checkpoint was delivered, and the
        # controller's numeric gate still refused the record - "air3b_massbalance.json::measured_signed_
        # relative>=0 unreadable: Invalid control character at: line 1 column 1505" - and downgraded the
        # run to GATE_FAILED. The evidence is kept at
        # test-evidence/air-three-blade/p-e2e/r10-sim-entry-b (stages ok, report verdict
        # REPORT_GATE_FAILED).
        for name in ('AuditAir3bMassBalance.java', 'RunAir3bTransientRevolution.java'):
            source = read(MACROS / name)
            with self.subTest(macro=name):
                self.assertIn('private static String jstr(String value)', source)
                # Every C0 control and DEL, not only the three the first version neutralised: the
                # independent review pointed out that \b, \f and U+007F would still destroy a record.
                self.assertIn('c < 0x20 || c == 0x7f', source,
                              '%s neutralises only some control characters' % name)
                self.assertIn("b.append('/')", source)
                self.assertIn("b.append('\\'')", source)
        mass = read(MACROS / 'AuditAir3bMassBalance.java')
        self.assertIn('jstr(parts[1])', mass)
        self.assertIn('jstr(row.substring(', mass)
        self.assertIn("append(jstr(row)).append", mass)
        self.assertNotIn('.append(row.replace("\\t", " ")', mass,
                         'the interface rows are embedded without the control-character guard again')
        # The parser-level consequence, on the exact shape that failed: a raw tab inside a JSON string is
        # invalid for a strict reader (Python rejects it by default, Java writes it by default).
        with self.assertRaises(ValueError):
            json.loads('{"detail":"axis=[0,0,1]\torigin_m=[0,0,0]"}')

    def test_the_marked_raw_artifact_is_kept_and_is_still_unparseable(self):
        # ②R10: this round's own ② run produced one unparseable certificate, and the R1 parse ratchet
        # (scripts/run_record_gate.py::known_unparseable) failed the suite for it exactly as designed - its
        # contract is "exactly the round-6 loss, and never a second one". The producer was fixed and the
        # defective bytes were preserved under a name that is not `.json`, so the ratchet still pins exactly
        # one loss and the loss this round caused is pinned HERE instead. Neither may rot silently: this
        # test fails if the raw file is deleted (a hidden loss) or if it starts parsing (a laundered one).
        raw = (ROOT / 'test-evidence' / 'air-three-blade' / 'p-e2e' / 'r10-sim-entry-b' /
               'air3b-mrf-sim-entry-20261004b' / 'air3b_massbalance.json.raw-control-chars')
        self.assertTrue(raw.is_file(), 'the marked raw artifact was removed - that hides the loss')
        text = raw.read_text(encoding='utf-8')
        # The digest is pinned too: a swapped unparseable file would otherwise satisfy every assertion here.
        self.assertEqual(sha256(raw), 'f938202c7a5b562a8102545d8d6874a1d844032d792c436ce764d4553c30a929',
                         'the preserved bytes changed')
        self.assertIn('\t', text, 'the artifact no longer shows the defect it is kept for')
        with self.assertRaises(ValueError):
            json.loads(text)
        # And it is outside the ratchet's glob, which is what keeps that constant at one entry.
        self.assertFalse(raw.name.endswith('.json'))
        note = raw.parent / 'README-unparseable-artifact.md'
        self.assertTrue(note.is_file(), 'the artifact is kept without the note that explains it')

    def test_n1_every_pipeline_pin_is_the_disk_byte(self):
        # ②R10 re-review N1: nothing compared a pipeline's own macro_hashes with the disk, which is how a
        # freeze assembled from the disk could leave BOTH pipelines unsubmittable (start() refuses on the
        # first stale pin). This test is the missing comparison, and it also checks the freeze against the
        # pipelines it claims to freeze.
        freeze = json.loads((ROOT / 'test-evidence' / 'air-three-blade' / 'p-e2e' /
                             'r10-freeze.json').read_text(encoding='utf-8'))
        frozen = {entry['path']: entry for entry in freeze['pipelines'].values()}
        for path in (PIPELINE_2, PIPELINE_3):
            pipeline = json.loads(path.read_text(encoding='utf-8'))
            rel = str(path.relative_to(ROOT))
            # R16 updates the scenario-2 audit only. Historical R10 signatures must remain
            # unchanged, rather than be re-signed against current bytes. The CURRENT
            # execution gate still compares every disk byte (tested in test_air3b_r16).
            evidence_name = 'r10-sim-entry-c' if path == PIPELINE_2 else 'r10-transient-main'
            old_dir = ROOT / 'test-evidence/air-three-blade/p-e2e' / evidence_name
            old_review = json.loads((old_dir / 'pipeline-review.json').read_text())
            self.assertEqual(old_review['pipeline_sha256'], sha256(path))
            self.assertEqual(old_review['macro_hashes'], pipeline['macro_hashes'])
            self.assertEqual(old_review['input_hashes'], pipeline['input_hashes'])
            configs = list(old_dir.glob('*-config.json'))
            self.assertEqual(len(configs), 1)
            old_config = json.loads(configs[0].read_text())
            self.assertEqual(old_config['macro_hashes'], old_review['macro_hashes'])
            self.assertEqual(old_config['input_hashes'], old_review['input_hashes'])
            indexes = list(old_dir.glob('*-evidence-index.json'))
            self.assertEqual(len(indexes), 1)
            index = json.loads(indexes[0].read_text())
            run = old_dir / indexes[0].name.removesuffix('-evidence-index.json')
            for record_name in ('air3b_massbalance.json', 'air3b_residual_provenance.json'):
                record = run / record_name
                self.assertEqual(sha256(record), index[record_name]['sha256'])
                self.assertEqual(gate.citation_status(record)['state'], gate.CITABLE)
            # R17 legitimately updates physics/solve/audit candidate bytes. Historical
            # runs are bound above to their signed review/config/evidence, not current disk.
            self.assertIn(rel, frozen, 'the freeze does not name %s' % rel)
            self.assertEqual(frozen[rel]['pipeline_sha256'], sha256(path))
            self.assertEqual(frozen[rel]['macro_hashes'], pipeline['macro_hashes'],
                             'the freeze disagrees with %s about its macro pins' % rel)
            self.assertEqual(frozen[rel]['input_hashes'], pipeline['input_hashes'])
        # NC3: the freeze must not carry a pipeline this test never checked.
        self.assertEqual(set(frozen), {str(PIPELINE_2.relative_to(ROOT)), str(PIPELINE_3.relative_to(ROOT))},
                         'the freeze names a pipeline that no pin check covers')

    def test_n3_numeric_gates_are_checked_for_satisfiability_not_only_for_their_key(self):
        # ②R10 re-review N3/NC1: the key cross-check cannot catch a value the macro never emits, and a
        # value built from a formatter model can be wrong for a different step. The time-step gate is
        # therefore checked as a formatter-free digit prefix of the value the pipeline itself declares.
        env = self.pipeline3['env']
        prefix = re.match(r'[0-9.]+', env['AIR3B_TRANSIENT_DT_S']).group(0)[:7]
        joined = ' '.join(self.transient['require_contains'])
        self.assertIn('"time_step_s":' + prefix, joined)
        # Every formatting model agrees on the leading digits and disagrees later, which is the whole
        # reason the gate stops there.
        self.assertTrue(('%.12g' % float(env['AIR3B_TRANSIENT_DT_S'])).startswith(prefix))
        self.assertTrue(repr(float(env['AIR3B_TRANSIENT_DT_S'])).startswith(prefix))
        # Integer gates are emitted by int concatenation, so their literal text is what the run must show.
        for literal in ('"chunk_steps":1', '"inner_iteration_cap":10', '"steps_declared":2',
                        '"steps_executed":2'):
            self.assertIn(literal, joined)

    def test_n3b_header_and_tsv_gates_are_cross_checked_too(self):
        # The key cross-check skips every gate whose needle is not a JSON key, which is all TSV gates and
        # the CSV headers. Those are checked here against the writer literally.
        source = read(MACROS / 'RunAir3bTransientRevolution.java')
        for seg in ('s1', 's2'):
            self.assertIn('air3b_transient_%s_history.csv::chunk,iteration,physical_time_s,'
                          'thrust_z_n,torque_z_nm' % seg, self.transient['require_contains'])
        self.assertIn('w.write("chunk,iteration,physical_time_s,thrust_z_n,torque_z_nm\\n");', source)
        self.assertIn('air3b_interface_state_s2.tsv::after_motion\t', self.transient['require_contains'])
        self.assertIn('w.write(phase + "\\t" + i.getPresentationName()', source)

    def test_n5_the_runner_refuses_a_directory_that_already_holds_an_UNREGISTERED_checkpoint(self):
        # ②R10: the first version of this check refused EVERY run, because the registered pipeline inputs
        # are themselves .sim files that start() has just uploaded and hash-verified - measured, not
        # hypothesised: the supplementary trial was refused by this very line (LastTaskResult 1, no STAR
        # process ever started). A stale checkpoint is one the pipeline did NOT register.
        runner = read(ROOT / 'scripts' / 'security' / 'trial_air3b_pipeline.ps1')
        self.assertIn("Get-ChildItem -LiteralPath $c.job_dir -Filter '*.sim'", runner)
        self.assertIn('already holds an unregistered .sim', runner)
        self.assertIn('foreach($p in $c.input_hashes.PSObject.Properties){$declaredInputs+=$p.Name}', runner)
        self.assertIn('$declaredInputs -notcontains $_.Name', runner,
                      'the check does not exclude the registered inputs, so it refuses every run')
        # And the exclusion cannot be vacuous for THIS pipeline: the registered inputs really do contain a
        # .sim, which is why the exclusion is required rather than decorative.
        manifest = json.loads(INPUTS.read_text(encoding='utf-8'))
        self.assertTrue(any(rel.endswith('.sim') for rel in manifest['inputs']),
                        'no registered input is a .sim, so this test no longer guards the measured defect')
        self.assertIn('block_geometry.sim', json.loads(PIPELINE_2.read_text(encoding='utf-8'))['input_hashes'])

    def test_n4_the_measured_surface_is_identifiable(self):
        # The role name is shared by every blade-role boundary, so the record must carry something that
        # distinguishes them: a candidate index and the measured centroid.
        source = read(MACROS / 'RunAir3bTransientRevolution.java')
        self.assertIn('candidateReport.add("#" + candidateCount', source)
        self.assertIn('measured_surface_identity', source)
        self.assertIn('measured_centroid_before_xyz_m', source)


class SummaryAdapterReportsThreeConclusions(unittest.TestCase):
    """The adapter must reuse the two existing rules and keep the three verdicts apart."""

    def test_the_adapter_is_itself_hash_bound_by_the_review_gate(self):
        # ②R10: the reader that produces the round's three conclusions is part of the record. An
        # unpinned reader is the easiest place for a reported conclusion to drift, so its bytes are
        # compared by code at submission time like the report generator's and the entry command's.
        import air3b_pipeline_trial as trial
        key = 'cases/air-three-blade/air3b_summary.py'
        support = trial.support_hashes()
        self.assertIn(key, support)
        self.assertEqual(support[key], sha256(SUMMARY))

    def test_it_reuses_both_rules_instead_of_restating_them(self):
        source = read(SUMMARY)
        self.assertIn('import run_record_gate', source)
        self.assertIn('import air3b_convergence_gate', source)
        self.assertNotIn('def citation_status', source)
        self.assertNotIn('def claims_ok', source)
        self.assertNotIn('def screen_residuals', source)

    def test_it_reproduces_the_r8_conclusions_offline(self):
        if not (R8_EVIDENCE / (R8_TRIAL + '-config.json')).is_file():
            self.skipTest('R8 evidence is not checked in')
        completed = subprocess.run([sys.executable, str(SUMMARY), '--trial-id', R8_TRIAL,
                                    '--evidence', str(R8_EVIDENCE)],
                                   capture_output=True, text=True)
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        payload = json.loads(completed.stdout[:completed.stdout.index('\n\n###')])
        self.assertEqual(payload['process']['verdict'], 'PASS', payload['process'])
        self.assertEqual(payload['delivery']['verdict'], 'DELIVERED')
        self.assertEqual(payload['delivery']['checkpoint_delivered'], True)
        # Track E must be reported as NOT passed for this run, and must not be merged into track P.
        self.assertEqual(payload['engineering']['verdict'], 'NOT_PASSED')
        self.assertFalse(payload['engineering']['steady_window_passed'])

    def test_a_run_without_a_convergence_table_is_not_screened_rather_than_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            trial = evidence / 'trial-12345678'
            trial.mkdir()
            (evidence / 'trial-12345678-config.json').write_text(json.dumps({
                'pipeline_id': 'x', 'stages': [{'name': 'transient', 'required': ['a.json']}]}),
                encoding='utf-8')
            (trial / 'a.json').write_text(json.dumps({'status': 'ok', 'measured_rotation_angle_deg': 4.0}),
                                          encoding='utf-8')
            (trial / 'stage-transient.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'transient', 'state': 'ok', 'artifacts': ['a.json'],
                'macros': []}), encoding='utf-8')
            completed = subprocess.run([sys.executable, str(SUMMARY), '--trial-id', 'trial-12345678',
                                        '--evidence', str(evidence)], capture_output=True, text=True)
        payload = json.loads(completed.stdout[:completed.stdout.index('\n\n###')])
        self.assertEqual(payload['engineering']['verdict'], 'NOT_SCREENED')
        self.assertEqual(payload['process']['verdict'], 'PASS')
        self.assertEqual(payload['delivery']['verdict'], 'NOT_RECORDED')

    def test_a_failed_stage_makes_the_process_conclusion_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            trial = evidence / 'trial-12345678'
            trial.mkdir()
            (evidence / 'trial-12345678-config.json').write_text(json.dumps({
                'pipeline_id': 'x', 'stages': [{'name': 'solve', 'required': ['a.json']}]}), encoding='utf-8')
            (trial / 'a.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
            (trial / 'stage-solve.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'solve', 'state': 'failed', 'artifacts': ['a.json'],
                'macros': []}), encoding='utf-8')
            completed = subprocess.run([sys.executable, str(SUMMARY), '--trial-id', 'trial-12345678',
                                        '--evidence', str(evidence)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout[:completed.stdout.index('\n\n###')])
        self.assertEqual(payload['process']['verdict'], 'FAIL')

    def test_a_gate_failed_measurement_is_not_citable(self):
        # The macro writes status "gate_failed" when its own evidence does not hold; the single citation
        # rule must refuse it even though the surrounding stage would say ok.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'stage-transient.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'transient', 'state': 'ok',
                'artifacts': ['air3b_transient_s1_measurements.json'], 'macros': []}), encoding='utf-8')
            (d / 'air3b_transient_s1_measurements.json').write_text(json.dumps({
                'status': 'gate_failed', 'measured_rotation_angle_deg': 0.0,
                'gate_real_displacement': False}), encoding='utf-8')
            verdict = gate.citation_status(d / 'air3b_transient_s1_measurements.json')
        self.assertNotEqual(verdict['state'], gate.CITABLE, verdict)


if __name__ == '__main__':
    unittest.main(verbosity=2)
