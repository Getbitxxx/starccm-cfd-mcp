# -*- coding: utf-8 -*-
"""②R8 regression tests: the scenario-2 P assembly, its new geometry-sim path, and its certificates.

Why these tests exist (AGENTS.md §2-O9: a review finding must become an executable test, not prose):

1. The geometry->regions chain discontinuity that the ②R7 assembly had to declare as a limitation.
   `BuildAir3bMrfRegions` used to refuse any non-empty simulation and always re-import the STEP
   files, so the geometry stage's `block_geometry.sim` was discarded. The ②R8 fix lets it CONSUME a
   hash-bound geometry-only .sim while keeping the legacy new-session import path. The dangerous
   regression is the opposite direction: a state that is secretly a solve checkpoint being accepted
   as "geometry", or the new path silently importing again.

2. The four certificate blockers. `progress_matrix.py` marked ②/③ geometry and mesh PASSED with no
   record, and the ②R8 audit found that `run_record_gate.py` could not certify any existing artefact
   (the measurements lived in TSVs/CSVs with no measurement-level `status: ok`). These tests pin the
   contract for the records added in ②R8: each must be JSON, must carry `status: ok`, must be listed
   in its stage's `required` artifacts, and - the part that actually matters - must be CITABLE by the
   one and only citation implementation when its stage record says ok.

These are structural/source tests because the macros are Java and cannot be executed here; the repo
already uses source-level contract tests for exactly this reason (see tests/test_stage_plan.py).
Nothing here re-implements a gate: citability is asked of scripts/run_record_gate.py itself.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import run_record_gate as gate  # noqa: E402

MACROS = ROOT / 'macros'
PIPELINE = ROOT / 'cases' / 'air-three-blade' / 'pipeline-e2e-geometry-to-validation.json'
INPUTS = ROOT / 'cases' / 'air-three-blade' / 'e2e-inputs.json'
PROGRESS = ROOT / 'progress.json'

# stage name in the pipeline -> (record file, macro that must write it)
# These are the four measurement-level records ②'s progress stages will cite.
CERTIFICATES = {
    'geometry': ('block_geometry_audit.json', 'ImportAir3bBlockGeometry.java',
                 ['measured_parts', 'measured_regions', 'measured_iterations_or_iteration']),
    'mesh': ('mrf_volume_mesh_quality.json', 'AuditMrfVolumeMeshQuality.java',
             ['measured_cell_quality_min', 'measured_face_validity_min', 'measured_volume_change_min']),
    'solve': ('air3b_segment_verdict.json', 'RunAir3bMrfSegment.java',
              ['iterations_run', 'cells']),
    'validation': ('air3b_massbalance.json', 'AuditAir3bMassBalance.java',
                   ['measured_signed_relative']),
}


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


class GeometryOnlyInputPath(unittest.TestCase):
    """The new path must consume a geometry-only .sim and must NOT import again."""

    def setUp(self):
        self.source = read(MACROS / 'BuildAir3bMrfRegions.java')

    def test_legacy_import_is_still_present_exactly_twice(self):
        # One call per block, and only in the legacy branch. If this ever becomes 0 the legacy path
        # is broken; if it becomes 4 the new path is importing too.
        self.assertEqual(len(re.findall(r'importCadParts\s*\(', self.source)), 2,
                         'expected exactly two importCadParts calls (one per STEP block)')

    def test_import_calls_are_guarded_by_the_imported_here_branch(self):
        # The two import calls must sit inside `if (importedHere) { ... }`, so a loaded geometry-only
        # .sim cannot trigger a duplicate import.
        guard = self.source.index('if (importedHere)')
        first_import = self.source.index('importCadParts')
        # the closing of that branch is the `} else {` that follows the guard
        else_at = self.source.index('} else {', guard)
        self.assertTrue(guard < first_import < else_at,
                        'the import calls are not inside the importedHere branch')
        # and the else-branch is the geometry-sim path, which reports that it skipped the import
        self.assertIn('GEOMETRY_SOURCE', self.source)
        self.assertIn('loaded_geometry_only_sim', self.source)
        self.assertIn('STEP import SKIPPED', self.source)

    def test_import_call_count_is_reported_from_the_branch(self):
        # An absent call is otherwise unobservable; the report must say 0 or 2 from the real branch.
        self.assertIn('STEP_IMPORT_CALLS', self.source)
        self.assertIn('importedHere ? 2 : 0', self.source)

    def test_the_checkpoint_guards_are_evaluated_before_the_import_branch(self):
        # Presence alone is not enough: a guard that runs AFTER the branch would let state (A) import
        # and then refuse, or let a solve checkpoint reach the branch. Assert source order.
        iteration_guard = self.source.index('loadedIteration != 0')
        regions_guard = self.source.index('loadedRegions != 0 || loadedInterfaces != 0')
        branch = self.source.index('if (importedHere)')
        self.assertLess(iteration_guard, branch, 'the iteration guard runs after the import branch')
        self.assertLess(regions_guard, branch, 'the regions/interfaces guard runs after the branch')
        self.assertLess(self.source.index('boolean partsPresent'), iteration_guard,
                        'parts presence is read after the guards')

    def test_solve_checkpoint_is_refused_as_geometry(self):
        # Iteration, regions and interfaces are each checked with a hard throw, so a solved state
        # cannot be smuggled in as the "geometry" input.
        self.assertIn('loadedIteration != 0', self.source)
        self.assertIn('loadedRegions != 0 || loadedInterfaces != 0', self.source)
        self.assertIn('a solve checkpoint is not a geometry input', self.source)

    def test_topology_and_scale_gates_survive_on_the_shared_path(self):
        # These checks sit after the branch, so BOTH paths run them.
        for needle in ('isClosed()', 'isManifold()', 'getTriangleCount()',
                       'R_ROT', 'R_OUT', 'SCALE_TOL'):
            self.assertIn(needle, self.source, 'missing gate: %s' % needle)
        after_branch = self.source[self.source.index('STEP_IMPORT_CALLS'):]
        for needle in ('isClosed()', 'near(rotatingMaxR, R_ROT, SCALE_TOL)',
                       'near(stationaryMaxR, R_OUT, SCALE_TOL)'):
            self.assertIn(needle, after_branch, 'gate not shared by both paths: %s' % needle)


class MeasurementRecordsDeclareStatusOk(unittest.TestCase):
    """Each certificate must be a JSON record that claims success at measurement level."""

    def test_every_certificate_macro_writes_status_ok(self):
        for stage, (record, macro, _keys) in CERTIFICATES.items():
            with self.subTest(stage=stage):
                source = read(MACROS / macro)
                self.assertIn(record, source,
                              '%s does not name its certificate %s' % (macro, record))
                # the literal that produces the JSON field
                self.assertRegex(source, r'\\"status\\":\\"ok\\"',
                                 '%s does not write a measurement-level status ok' % macro)

    def test_every_certificate_declares_its_measured_keys(self):
        for stage, (record, macro, keys) in CERTIFICATES.items():
            with self.subTest(stage=stage):
                source = read(MACROS / macro)
                for key in keys:
                    if key == 'measured_iterations_or_iteration':
                        self.assertTrue('measured_iteration' in source or '\\"iteration\\"' in source)
                        continue
                    self.assertIn(key, source, '%s missing measured key %s' % (macro, key))

    def test_measured_values_are_not_hardcoded_literals(self):
        # A certificate must carry THIS run's readback, not a constant copied from a previous run.
        # The build run's values must not appear as literals in any certificate writer.
        tainted = ['2053234', '0.010689664751487906', '2.333833722908902E-5']
        for stage, (record, macro, _keys) in CERTIFICATES.items():
            with self.subTest(stage=stage):
                source = read(MACROS / macro)
                for value in tainted:
                    self.assertNotIn(value, source,
                                     '%s hardcodes a previous run\'s measured value %s' % (macro, value))


class CertificatesAreWiredIntoTheStageArtifacts(unittest.TestCase):
    """A record that is not in its stage's `required` list is not produced or verified."""

    def setUp(self):
        self.pipeline = json.loads(PIPELINE.read_text(encoding='utf-8'))
        self.stages = {s['name']: s for s in self.pipeline['stages']}

    def test_each_certificate_is_declared_in_its_stage_required_list(self):
        for stage, (record, _macro, _keys) in CERTIFICATES.items():
            with self.subTest(stage=stage):
                self.assertIn(stage, self.stages, 'pipeline has no stage %s' % stage)
                required = self.stages[stage].get('required') or []
                self.assertIn(record, required,
                              '%s not in stage %s required list %r' % (record, stage, required))

    def test_inputs_manifest_agrees_with_the_pipeline_pins(self):
        mapping = json.loads(INPUTS.read_text(encoding='utf-8'))
        import hashlib
        pinned = self.pipeline['input_hashes']
        supplied = {}
        for rel in mapping['inputs']:
            path = ROOT / rel
            self.assertTrue(path.is_file(), 'input listed but missing: %s' % rel)
            supplied[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(supplied, pinned,
                         'the inputs manifest and the pipeline input_hashes disagree')

    def test_the_stage_chain_is_actually_continuous(self):
        """The ②R7 limitation, pinned.

        `stage_plan.plan_pipeline` only requires a stage input to be *available*; `null` is always
        allowed, which is how the original discontinuity slipped through: the regions stage declared
        `input: null`, was launched with `-new`, and re-imported the STEP files instead of consuming
        the geometry stage's output. This exact bug was reintroduced once while wiring the ②R8
        certificates and caught only by reading the macro path against the gate - so it is a test now.

        Every stage after the first must consume an earlier stage's declared output, and each stage's
        macros must chain through their `macro_outputs` (a null entry means "reuse the previous
        input", which is legitimate for a read-only audit).
        """
        # Seeded EMPTY on purpose: a stage input must be produced by an earlier STAGE, not merely be
        # one of the registered input files. Seeding this with input_hashes would let a stage read a
        # raw STEP and still look chained.
        produced = set()
        for index, stage in enumerate(self.pipeline['stages']):
            name = stage['name']
            if index > 0:
                self.assertIsNotNone(stage.get('input'),
                                     'stage %s declares no input: the pipeline is not a chain' % name)
                self.assertTrue(stage['input'] in produced or stage['input'] in self.pipeline['input_hashes'],
                                'stage %s reads %s, which no earlier stage produced'
                                % (name, stage['input']))
                if index > 1:
                    self.assertIn(stage['input'], produced,
                                  'stage %s reads %s, which is a raw input rather than a stage output'
                                  % (name, stage['input']))
            current = stage.get('input')
            for position, macro in enumerate(stage['macros']):
                outputs = stage.get('macro_outputs')
                if outputs and position < len(outputs):
                    declared = outputs[position]
                else:
                    declared = stage.get('output') if len(stage['macros']) == 1 else None
                if declared:
                    produced.add(declared)
                    current = declared
                # a null output keeps the previous input, which is the documented chaining rule
            if stage.get('output'):
                self.assertIn(stage['output'], produced)

    def test_regions_stage_consumes_the_geometry_checkpoint(self):
        # The specific fix (②R8, carried into the ②R10 direct-.sim entry): the regions macro must be
        # handed a geometry-only .sim, so it takes the loaded-sim path and the "STEP_IMPORT_CALLS 0"
        # gate in this same pipeline can hold. ②R10 changed WHERE that .sim comes from - it is now the
        # registered entry state (R8's own block_geometry.sim, hash-pinned in input_hashes) instead of
        # the geometry stage's own output - so the geometry stage is a read-only audit that declares no
        # output. The substantive claim is unchanged and is what this test still pins: the regions stage
        # is handed a geometry-only .sim and must not re-import the CAD.
        self.assertEqual(self.stages['regions'].get('input'), 'block_geometry.sim')
        self.assertIsNone(self.stages['geometry'].get('output'))
        self.assertEqual(self.stages['geometry'].get('input'), 'block_geometry.sim')
        self.assertIn('block_geometry.sim', self.pipeline['input_hashes'])
        joined = ' '.join(self.stages['regions']['require_contains'])
        self.assertIn('STEP_IMPORT_CALLS\t0', joined)
        self.assertIn('loaded_geometry_only_sim', joined)

    def test_declared_deadlines_fit_the_launcher_cap(self):
        # The launcher sets timeout_seconds = min(1800, sum(stage timeouts) + 60) and the runner
        # refuses a stage list whose declared deadlines exceed that. 1740 is therefore a hard ceiling
        # on the declared sum; exceeding it makes the trial unsubmittable for a bookkeeping reason.
        total = sum(s['timeout_seconds'] for s in self.pipeline['stages'])
        self.assertLessEqual(total, 1740, 'declared stage deadlines %d s exceed the launcher cap' % total)

    def test_the_solve_stage_has_real_wall_clock_margin_over_its_iteration_cap(self):
        # The solve stage must require exactly its iteration cap, so the cap has to be reachable
        # before the wall-clock budget ends - otherwise a healthy run fails the content gate having
        # done fewer iterations. Measured cost on this mesh is ~2.1 s/iteration.
        import re
        env = self.pipeline['env']
        cap = int(env['AIR3B_SEGMENT_MAX_ITERATIONS'])
        seconds = float(env['AIR3B_SEGMENT_SECONDS'])
        self.assertGreaterEqual(seconds / cap, 3.0,
                                'only %.2f s/iteration of margin before the iteration cap is missed'
                                % (seconds / cap))
        solve = self.stages['solve']
        self.assertLessEqual(seconds + 120, solve['timeout_seconds'],
                             'the segment budget plus load/save could exceed the stage deadline')

    def test_no_content_gate_pins_a_measured_value(self):
        """A gate must not assert a value that only a previous run measured.

        The mesh FaceValidity minima (1.0) were measured on an 8-core build. Pinning `,min,1.0` in
        the pipeline would make a legitimate 24-core re-mesh fail the stage on a *value* rather than
        on the measurement existing - i.e. an engineering outcome would masquerade as an execution
        failure, and the one authorized run would be lost. The gate keeps the region and metric so
        the row must exist, and the measured value lives in the certificate for the report.
        """
        quality = [e for e in self.stages['mesh']['require_contains']
                   if 'FaceValidity' in e or 'VolumeChange' in e or 'CellQuality' in e]
        self.assertTrue(quality, 'no mesh quality row is required at all - the audit could be skipped')
        self.assertTrue(any('FaceValidity,min,' in e for e in quality),
                        'the per-region FaceValidity minimum rows are no longer required')
        for entry in quality:
            self.assertFalse(re.search(r'(true|false|[0-9])$', entry),
                             'mesh quality gate pins a measured value: %r' % entry)

    def test_physical_verdicts_are_reported_not_gated(self):
        """AGENTS.md §1: track P is "runs correctly and reports honestly", not "physics converged".

        A content gate that demands a physical threshold turns an engineering-not-passed into an
        execution failure. A 150-iteration cold start can legitimately still exceed the 1e-3
        conservation limit, so requiring `mass_imbalance_within_limit true` would kill the one
        authorized run for a physics reason. The measurements must still exist - only their VERDICT
        is demoted to a reported field.
        """
        # `normalization_established` is deliberately NOT in this list: the ②R8 independent review
        # (M2) established that whether the residual monitors' normalization could be READ is an
        # execution-health fact about the audit, not a CaseSpec physics threshold. Without it the
        # residual numbers cannot be interpreted at all, so a green stage there would be meaningless.
        # It is asserted as a value gate by test_normalization_readability_stays_an_execution_gate.
        physical = ('mass_imbalance_within_limit', 'sign_convention_as_expected',
                    'quality_gate_passed')
        for stage in self.pipeline['stages']:
            for entry in list(stage.get('require_contains', [])) + list(stage.get('require_numeric', [])):
                for key in physical:
                    if key not in entry:
                        continue
                    # A demoted gate ends at the label: a trailing TSV tab, or a JSON key colon.
                    # A pinned VALUE would end in "true"/"false" or a number.
                    self.assertRegex(entry, r'(\t|:)$',
                                     'physical verdict is gated as a value: %r' % entry)

    def test_execution_health_is_still_gated(self):
        # Non-finite target quantities are a solver failure, not a physics result, so this one stays.
        joined = ' '.join(self.stages['solve']['require_contains'])
        self.assertIn('"finite_target_quantities":true', joined)

    def test_normalization_readability_stays_an_execution_gate(self):
        # ②R8 review M2. If a residual monitor's normalization option could not be read, the residual
        # numbers cannot be interpreted, so a green validation stage would be meaningless. The audit
        # itself reports this through `established`, and the stage must require it.
        joined = ' '.join(self.stages['validation']['require_contains'])
        self.assertIn('normalization_established\ttrue', joined)

    def test_solve_stage_does_not_require_convergence(self):
        # Track P accepts an honest NOT_CONVERGED. The stage must require the operation's own
        # completion fields and the honest false convergence flag - never a converged residual.
        contains = self.stages['solve']['require_contains']
        joined = ' '.join(contains)
        # NB the pipeline has already been json.loads'ed, so these are real quote characters.
        self.assertIn('"finite_target_quantities":true', joined)
        self.assertIn('"convergence_verified":false', joined)
        self.assertNotIn('"convergence_verified":true', joined)


class RecordsAreCitableByTheSingleGateImplementation(unittest.TestCase):
    """The point of the records: run_record_gate must certify them, given an ok stage record.

    This asks the real implementation instead of restating its rules, so it cannot drift from it.
    """

    def _citation(self, stage: str, record_name: str, payload: object) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / ('stage-%s.json' % stage)).write_text(json.dumps({
                'schema_version': 1, 'stage': stage, 'state': 'ok',
                'artifacts': [record_name, 'x.sim'], 'macros': [],
            }), encoding='utf-8')
            (d / record_name).write_text(json.dumps(payload), encoding='utf-8')
            return gate.citation_status(d / record_name)

    def test_a_status_ok_measurement_in_an_ok_stage_is_citable(self):
        result = self._citation('geometry', 'block_geometry_audit.json',
                                {'status': 'ok', 'measured_parts': 2, 'measured_regions': 0})
        self.assertEqual(result['state'], gate.CITABLE, result)

    def test_status_ok_without_the_stage_record_is_not_certified(self):
        # "cannot find the state file" must never read as "passed" (AGENTS.md §2-O8 R2).
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'block_geometry_audit.json').write_text(
                json.dumps({'status': 'ok', 'measured_parts': 2}), encoding='utf-8')
            result = gate.citation_status(d / 'block_geometry_audit.json')
        self.assertEqual(result['state'], gate.UNKNOWN_RUN_STATE, result)

    def test_a_record_without_status_is_not_citable_even_in_an_ok_stage(self):
        # This is exactly the ②R7 defect: a real measurement with no `status` claim.
        result = self._citation('mesh', 'mrf_volume_mesh_quality.json',
                                {'measured_face_validity_min': 1.0})
        self.assertEqual(result['state'], gate.NOT_CITABLE_NO_CLAIM, result)

    def test_a_status_ok_record_in_a_failed_stage_is_not_citable(self):
        # §2-O8 R2: the stage verdict outranks the record's own claim, so a diverged solve segment
        # that still wrote status ok cannot be cited as a success.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'stage-solve.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'solve', 'state': 'failed',
                'artifacts': ['air3b_segment_verdict.json'], 'macros': [],
            }), encoding='utf-8')
            (d / 'air3b_segment_verdict.json').write_text(json.dumps({
                'status': 'ok', 'state': 'SEGMENT_DIVERGED',
                'finite_target_quantities': False}), encoding='utf-8')
            result = gate.citation_status(d / 'air3b_segment_verdict.json')
        self.assertNotEqual(result['state'], gate.CITABLE, result)


class ProgressStagesDeclareTheCertificateKeys(unittest.TestCase):
    """②'s PASSED stages must name a record and the measured keys R3 checks for finite numbers."""

    def setUp(self):
        self.doc = json.loads(PROGRESS.read_text(encoding='utf-8'))

    def test_scenario_two_has_no_passed_stage_without_a_record_or_gap(self):
        stages = self.doc['scenarios']['2_steady_mrf_propeller']['stages']
        for name, stage in stages.items():
            with self.subTest(stage=name):
                if stage.get('status') == 'PASSED':
                    self.assertTrue(stage.get('record'),
                                    'PASSED stage %s names no certificate' % name)
                    self.assertTrue(stage.get('requires_record_keys'),
                                    'PASSED stage %s declares no measured keys' % name)
                else:
                    # A downgraded stage must say WHY, so the gap stays visible.
                    self.assertTrue(stage.get('gap'),
                                    'non-PASSED stage %s carries no gap explanation' % name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
