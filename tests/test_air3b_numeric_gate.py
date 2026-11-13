# -*- coding: utf-8 -*-
"""Tests for the A5 numeric artefact gate.

Context, so the intent cannot be mistaken for a relaxation later:

The A5 continuation trial `air3b-a5-continuation-180-20261002c` ran correctly and reached
`"degrees_after_total":189.9999999999996`, but the trial runner had been given the substring pin
`air3b_transient_verdict.json::"degrees_after_total":180`. A substring test cannot decide a
floating-point quantity, so the stage was reported as a failure even though every engineering
intent had been met. The independent reviewer had recorded exactly this risk *before* the run.

A substring is simultaneously too strict (it rejects 189.9999999999996) and too weak (it accepts
1800 or 180.5). `numeric_gate` replaces it with a parsed comparison, so it is strictly stronger than
the pin it replaces. These tests pin both directions so the replacement cannot be quietly weakened
back into a substring, and so the gate stays fail-closed.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

spec = importlib.util.spec_from_file_location(
    'air3b_pipeline_trial', ROOT / 'scripts' / 'air3b_pipeline_trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)

# The real, already-downloaded A5 continuation evidence. This is genuine measured output, not a
# fixture invented to make the gate look good.
EVIDENCE = ROOT / 'test-evidence/air-three-blade/a5/continuation-180'
TRIAL_ID = 'air3b-a5-continuation-180-20261002c'
TRIAL_DIR = EVIDENCE / TRIAL_ID
CONFIG = EVIDENCE / (TRIAL_ID + '-config.json')


def synthetic_success_states(evidence, trial_id, trial_dir, stages, stage_state='ok'):
    """Temporary arithmetic fixtures only; never changes or certifies historical evidence."""
    origin = 'SYNTHETIC_STATE_FOR_ARITHMETIC_TEST_ONLY'
    for stage in stages:
        (trial_dir / ('stage-' + stage['name'] + '.json')).write_text(json.dumps({
            'schema_version': 1, 'stage': stage['name'], 'state': stage_state,
            'macros': [], 'evidence_origin': origin}), encoding='utf-8')
    success = json.dumps({'state': 'AIR3B_PIPELINE_COMPLETE', 'evidence_origin': origin})
    (evidence / (trial_id + '-result.json')).write_text(success, encoding='utf-8')
    (trial_dir / 'case-probe.json').write_text(success, encoding='utf-8')


@unittest.skipUnless(CONFIG.exists() and (TRIAL_DIR / 'air3b_transient_verdict.json').exists(),
                     'the A5 continuation evidence is not present in this checkout')
class NumericGateTest(unittest.TestCase):
    """The gate's ARITHMETIC, measured on the real A5 numbers.

    R11 (branch-1 handover item 4) added a state precondition: a numeric gate is a statement about a run
    that succeeded, so `numeric_gate` now refuses when the run or a stage record is not ok. The real A5
    continuation run is itself recorded FAILED - the substring pin is exactly why it was reported that way
    - so these arithmetic tests supply an ok run state (a copy of the real evidence with
    stage-revolution.json state=ok) and the state rule is pinned separately, on the real failed record, by
    StatePreconditionTest below. The numbers compared here are the real measured ones.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = pathlib.Path(self.tmp.name)
        self.trial_id = TRIAL_ID
        self.trial_dir = self.evidence / TRIAL_ID
        self.trial_dir.mkdir()
        for name in ('air3b_transient_verdict.json', 'air3b_transient_history.csv',
                     'air3b_interface_state.tsv'):
            source = TRIAL_DIR / name
            if source.exists():
                (self.trial_dir / name).write_bytes(source.read_bytes())
        self.original = CONFIG.read_text(encoding='utf-8')
        (self.evidence / (TRIAL_ID + '-config.json')).write_text(self.original, encoding='utf-8')
        self.stage_state = 'ok'

    def gate(self, specs):
        cfg = json.loads(self.original)
        cfg['stages'][0]['require_numeric'] = specs
        (self.evidence / (self.trial_id + '-config.json')).write_text(json.dumps(cfg), encoding='utf-8')
        synthetic_success_states(self.evidence, self.trial_id, self.trial_dir,
                                 cfg['stages'], self.stage_state)
        return trial.numeric_gate(self.evidence, self.trial_id, self.trial_dir)

    def test_accepts_the_measured_value(self):
        result = self.gate(['air3b_transient_verdict.json::degrees_after_total>=180.0'])
        self.assertTrue(result['passed'])
        self.assertEqual(189.9999999999996, result['checks'][0]['value'])

    def test_rejects_a_bound_above_the_measured_value(self):
        # Without this, a future arc could "pass" a full-revolution gate without turning.
        for bound in (190.0, 200.0, 360.0):
            with self.subTest(bound=bound):
                self.assertFalse(
                    self.gate([f'air3b_transient_verdict.json::degrees_after_total>={bound}'])['passed'])

    def test_numeric_gate_is_not_a_substring_test(self):
        # A substring pin for "180" also matches the text of "1800", so it would have accepted a
        # wildly wrong angle. The numeric gate must reject it.
        self.assertFalse(self.gate(['air3b_transient_verdict.json::degrees_after_total>=1800'])['passed'])
        # And it must compare the number exactly: the measured value passes, one ulp above fails.
        self.assertTrue(self.gate(['air3b_transient_verdict.json::degrees_after_total>=189.9999999999996'])['passed'])
        self.assertFalse(self.gate(['air3b_transient_verdict.json::degrees_after_total>=189.9999999999997'])['passed'])

    def test_upper_bound_direction(self):
        self.assertTrue(self.gate(['air3b_transient_verdict.json::degrees_after_total<=200'])['passed'])
        self.assertFalse(self.gate(['air3b_transient_verdict.json::degrees_after_total<=100'])['passed'])

    def test_fails_closed(self):
        cases = {
            'missing artefact': ['nope.json::degrees_after_total>=1'],
            'unparseable spec': ['garbage'],
            'non-numeric value': ['air3b_transient_verdict.json::state>=1'],
            'missing key': ['air3b_transient_verdict.json::not_a_field>=1'],
            'malformed bound': ['air3b_transient_verdict.json::degrees_after_total>=abc'],
        }
        for label, specs in cases.items():
            with self.subTest(case=label):
                self.assertFalse(self.gate(specs)['passed'], label)

    def test_no_requirements_means_no_gate(self):
        self.assertEqual({}, self.gate([]))


class StatePreconditionTest(unittest.TestCase):
    """A gate on a run that did not succeed is meaningless - pinned on the REAL failed run."""

    def test_the_historical_failed_run_can_no_longer_write_gate_passed_true(self):
        # test-evidence/air-three-blade/a5/continuation-180/...-result.json is state FAILED (the substring
        # pin reported it that way) and it has no stage record. With a requirement declared, the gate must
        # refuse and say why, instead of persisting gate_passed=true on a failed run.
        cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
        cfg['stages'][0]['require_numeric'] = ['air3b_transient_verdict.json::degrees_after_total>=180.0']
        with tempfile.TemporaryDirectory() as tmp:
            evidence = pathlib.Path(tmp)
            trial_dir = evidence / TRIAL_ID
            trial_dir.mkdir()
            (trial_dir / 'air3b_transient_verdict.json').write_bytes(
                (TRIAL_DIR / 'air3b_transient_verdict.json').read_bytes())
            (evidence / (TRIAL_ID + '-config.json')).write_text(json.dumps(cfg), encoding='utf-8')
            (evidence / (TRIAL_ID + '-result.json')).write_bytes(
                (EVIDENCE / (TRIAL_ID + '-result.json')).read_bytes())
            result = trial.numeric_gate(evidence, TRIAL_ID, trial_dir)
        self.assertFalse(result['passed'])
        self.assertIn('the run did not succeed', result['failed'][0]['reason'])
        self.assertEqual([], result['checks'])

    def test_a_failed_stage_record_refuses_the_gate_even_with_ok_numbers(self):
        cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
        cfg['stages'][0]['require_numeric'] = ['air3b_transient_verdict.json::degrees_after_total>=180.0']
        with tempfile.TemporaryDirectory() as tmp:
            evidence = pathlib.Path(tmp)
            trial_dir = evidence / TRIAL_ID
            trial_dir.mkdir()
            (trial_dir / 'air3b_transient_verdict.json').write_bytes(
                (TRIAL_DIR / 'air3b_transient_verdict.json').read_bytes())
            (trial_dir / 'stage-revolution.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'revolution', 'state': 'failed', 'macros': []}), encoding='utf-8')
            (evidence / (TRIAL_ID + '-config.json')).write_text(json.dumps(cfg), encoding='utf-8')
            result = trial.numeric_gate(evidence, TRIAL_ID, trial_dir)
        self.assertFalse(result['passed'])
        self.assertIn('revolution', result['failed'][0]['reason'])


class RegexShapeTest(unittest.TestCase):
    def test_accepts_the_documented_spec_form(self):
        match = trial.NUMERIC_RE.match('air3b_transient_verdict.json::degrees_after_total>=180.0')
        self.assertIsNotNone(match)
        self.assertEqual(('air3b_transient_verdict.json', 'degrees_after_total', '>=', '180.0'),
                         match.groups())

    def test_rejects_the_old_substring_pin_shape(self):
        # The retired pin must not be silently accepted as a numeric spec.
        self.assertIsNone(trial.NUMERIC_RE.match('air3b_transient_verdict.json::"degrees_after_total":180'))


class FailOpenRegressionTest(unittest.TestCase):
    """Blocker B1/B2 from the independent review of the numeric-gate repair.

    The reviewer proved two fail-open paths by execution: `"degrees_after_total":Infinity` satisfied
    `>=180.0`, and a numeric string or boolean was coerced by float(); and a missing trial config made
    `numeric_gate` return `{}`, which `poll` treats as "no requirement" and therefore never fails.
    Both are now closed, and these tests keep them closed.
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = pathlib.Path(self.tmp.name)
        self.trial_id = 'air3b-test-trial'
        self.trial_dir = self.evidence / self.trial_id
        self.trial_dir.mkdir()

    def write(self, value_json, specs):
        (self.trial_dir / 'verdict.json').write_text('{"degrees_after_total":%s}' % value_json, encoding='utf-8')
        stages = [{'name': 'revolution', 'require_numeric': specs}]
        (self.evidence / (self.trial_id + '-config.json')).write_text(
            json.dumps({'stages': stages}), encoding='utf-8')
        synthetic_success_states(self.evidence, self.trial_id, self.trial_dir, stages)
        return trial.numeric_gate(self.evidence, self.trial_id, self.trial_dir)

    def test_non_finite_values_never_pass(self):
        for literal in ('Infinity', '-Infinity', '1e999', 'NaN'):
            with self.subTest(value=literal):
                self.assertFalse(
                    self.write(literal, ['verdict.json::degrees_after_total>=180.0'])['passed'], literal)

    def test_type_coerced_values_never_pass(self):
        for literal in ('"1000"', 'true', 'false', 'null', '[1000]', '{"v":1000}'):
            with self.subTest(value=literal):
                self.assertFalse(
                    self.write(literal, ['verdict.json::degrees_after_total>=180.0'])['passed'], literal)

    def test_a_real_number_still_passes(self):
        # The guard must reject coercion WITHOUT rejecting the genuine measured value.
        self.assertTrue(self.write('189.9999999999996', ['verdict.json::degrees_after_total>=180.0'])['passed'])
        self.assertTrue(self.write('360', ['verdict.json::degrees_after_total>=360.0'])['passed'])

    def test_missing_config_is_a_failure_not_a_silent_skip(self):
        (self.trial_dir / 'verdict.json').write_text('{"degrees_after_total":1000}', encoding='utf-8')
        result = trial.numeric_gate(self.evidence, self.trial_id, self.trial_dir)
        self.assertNotEqual({}, result)
        self.assertFalse(result['passed'])

    def test_a_lower_bound_alone_still_accepts_overshoot(self):
        # Honest documentation of the gap the reviewer flagged and this repair does NOT close: a
        # lower bound is all the next arc needs to prove a revolution happened, but by itself it also
        # accepts a wild overshoot. An upper sanity bound must be declared alongside it. Pinned here so
        # the limitation is discoverable rather than assumed away.
        self.assertTrue(self.write('1000', ['verdict.json::degrees_after_total>=360.0'])['passed'])
        bracketed = self.write('1000', ['verdict.json::degrees_after_total>=360.0',
                                        'verdict.json::degrees_after_total<=480.0'])
        self.assertFalse(bracketed['passed'])
        bracketed_ok = self.write('360.0', ['verdict.json::degrees_after_total>=360.0',
                                            'verdict.json::degrees_after_total<=480.0'])
        self.assertTrue(bracketed_ok['passed'])


class Round7IntegersAreBracketed(unittest.TestCase):
    """Independent review round 7, blocker 4: an integer substring gate accepts `:70` for `:7`.

    The macros write the number unquoted, so `require_contains '":boundary_count":7'` is satisfied by
    `"boundary_count":70`; a region with 70 boundaries would have passed the stage that certifies the
    boundary classification. The certifying check is now a numeric bracket in the real pipeline, and
    this test drives the real `numeric_gate` over the real stage specs in both directions.
    """

    TRIAL_ID = 'round7-gate-unit'
    RECORDS = {
        'external_flow_region_probe.json': {'status': 'ok', 'part_surfaces_after_split': 7},
        'external_flow_region.json': {'status': 'ok', 'boundary_count': 7},
        'external_flow_mesh.json': {'status': 'ok', 'cells': 58724},
        'external_flow_physics.json': {'status': 'ok', 'speed_m_s': 15.0},
        'external_flow_mesh_quality.json': {'status': 'ok', 'measured_cell_quality_min': 1e-3,
                                            'measured_face_validity_min': 1.0,
                                            'measured_volume_change_min': 0.5,
                                            'measured_wall_prism_layers_min': 6},
    }

    def setUp(self):
        import copy
        import shutil
        # deep copy: mutations in one test must not leak into the next one's fixtures
        self.records = copy.deepcopy(self.RECORDS)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.evidence = self.tmp / 'evidence'
        self.trial_dir = self.evidence / self.TRIAL_ID
        self.trial_dir.mkdir(parents=True)
        pipeline = json.loads((ROOT / 'cases' / 'static-sphere-external-flow'
                               / 'pipeline-region-physics-mesh.json').read_text(encoding='utf-8'))
        (self.evidence / (self.TRIAL_ID + '-config.json')).write_text(
            json.dumps({'stages': pipeline['stages']}), encoding='utf-8')
        synthetic_success_states(self.evidence, self.TRIAL_ID, self.trial_dir, pipeline['stages'])
        for name, payload in self.records.items():
            (self.trial_dir / name).write_text(json.dumps(payload), encoding='utf-8')

    def gate(self):
        return trial.numeric_gate(self.evidence, self.TRIAL_ID, self.trial_dir)

    def test_the_correct_counts_pass(self):
        self.assertTrue(self.gate()['passed'], self.gate()['failed'])

    def test_seventy_boundaries_are_rejected(self):
        self.records['external_flow_region.json']['boundary_count'] = 70
        (self.trial_dir / 'external_flow_region.json').write_text(
            json.dumps(self.records['external_flow_region.json']), encoding='utf-8')
        result = self.gate()
        self.assertFalse(result['passed'])
        self.assertTrue(any('boundary_count' in str(item['spec']) and item.get('op') == '<='
                            for item in result['failed']), result['failed'])

    def test_seventy_split_surfaces_are_rejected(self):
        self.records['external_flow_region_probe.json']['part_surfaces_after_split'] = 70
        (self.trial_dir / 'external_flow_region_probe.json').write_text(
            json.dumps(self.records['external_flow_region_probe.json']), encoding='utf-8')
        self.assertFalse(self.gate()['passed'])

    def test_a_wrong_prism_layer_measurement_is_rejected(self):
        self.records['external_flow_mesh_quality.json']['measured_wall_prism_layers_min'] = 0.0
        (self.trial_dir / 'external_flow_mesh_quality.json').write_text(
            json.dumps(self.records['external_flow_mesh_quality.json']), encoding='utf-8')
        result = self.gate()
        self.assertFalse(result['passed'])
        self.assertTrue(any('measured_wall_prism_layers_min' in str(item['spec'])
                            for item in result['failed']), result['failed'])


if __name__ == '__main__':
    unittest.main()
