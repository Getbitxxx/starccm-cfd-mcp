"""O9 ratchets for family-level mesh acceptance (user decision 2026-10-30).

"合格" is upgraded from "one mesh cleared the thresholds" to
"族过线 + 余量可见 + 失败可定位 + 求解前必须过族门".

The fixtures are the REAL archived records of the 2026-10-30 campaign (no synthetic numbers): 14 quality records with
their recorded PASS/FAIL outcomes, the four declared families, the archived localization record of a real failure, and
the real solve-plan shape.  A rule that would misclassify any of them reddens these tests.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import casespec  # noqa: E402
import mesh_acceptance as acceptance  # noqa: E402
import run_mrf_chain as chain  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
DECLARATIONS = EVIDENCE / 'MESH-FAMILY-DECLARATIONS-20261030a.json'
FAMILY_GATE = EVIDENCE / 'MESH-FAMILY-GATE-20261030a.json'
FROZEN_SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec.json'

# every archived mesh-quality record, with the outcome the audit recorded at the time
ARCHIVED_MESHES = {
    'mrf-chain3-20261028a': True,
    'mrf-chord1044v2-mesh-20261030a': False,
    'mrf-domain10d-mesh-20261030a': True,
    'mrf-gci-coarse-contrastfix-mesh-20261030a': True,
    'mrf-gci-fine-mesh-20261030a': True,
    'mrf-gci-finer2-mesh-20261030a': True,
    'mrf-gci-finer3-mesh-20261030a': True,
    'mrf-meshb12-20261030a': False,
    'mrf-meshb13-20261030a': True,
    'mrf-meshb2-20261028a': True,
    'mrf-meshb3b-20261028a': False,
    'mrf-meshb4-20261028a': False,
    'mrf-meshv2-20261028a': False,
    'mrf-meshv2b-20261028a': False,
}


def declarations() -> dict:
    return json.loads(DECLARATIONS.read_text(encoding='utf-8'))


def gates() -> dict:
    return declarations()['thresholds']


def margin_rule() -> dict:
    return declarations()['acceptance']['margin']


class CalibrationReplay(unittest.TestCase):
    def test_classifier_reproduces_every_recorded_outcome(self):
        """The classifier must agree with the gate verdict recorded for each of the 14 archived meshes."""
        rule = margin_rule()
        for run, recorded_pass in ARCHIVED_MESHES.items():
            record = json.loads((EVIDENCE / run / 'mrf_volume_mesh_quality.json').read_text(encoding='utf-8'))
            verdict = acceptance.classify_member(record, None, gates(), rule)
            if recorded_pass:
                self.assertIn(verdict['verdict'], (acceptance.VERDICT_PASS, acceptance.VERDICT_FRAGILE), run)
                self.assertEqual([], verdict['hard_failures'], run)
            else:
                self.assertEqual(acceptance.VERDICT_FAIL, verdict['verdict'], run)
                self.assertIn('mesh_min_volume_change', verdict['hard_failures'], run)

    def test_margin_rule_sits_above_the_observed_separation_band(self):
        """Calibration: no passing mesh is below 1.38x and no failing mesh is above 0.92x, so 1.5 is a defensible
        FRAGILE line; a rule below 1.38 would hide the real cliff."""
        passes, fails = [], []
        for run, recorded_pass in ARCHIVED_MESHES.items():
            record = json.loads((EVIDENCE / run / 'mrf_volume_mesh_quality.json').read_text(encoding='utf-8'))
            verdict = acceptance.classify_member(record, None, gates(), margin_rule())
            (passes if recorded_pass else fails).append(verdict['margin'])
        self.assertGreater(min(passes), 1.0, 'a passing mesh must be above the threshold')
        self.assertLess(max(fails), 1.0, 'a failing mesh must be below the threshold')
        self.assertGreater(min(passes), max(fails))
        fragile = margin_rule()['fragile_below']
        self.assertGreater(fragile, max(fails))
        self.assertLessEqual(fragile, min(passes) + 0.12, 'the FRAGILE line must not flag most passing meshes')


class FamilyReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = acceptance.evaluate_declarations(declarations(), EVIDENCE)
        cls.by_id = {family['family_id']: family for family in cls.record['families']}

    def test_the_real_gci_ladder_is_rejected_before_any_solve(self):
        """THE measured lesson: the production mesh passed at 2.06x while the coarse level failed at 0.72x; the family
        verdict must be a refusal, not a pass inherited from the production mesh."""
        gci = next(f for f in self.record['families'] if f['family_id'].startswith('gci-three-level-ladder'))
        self.assertFalse(gci['accepted'])
        self.assertEqual('coarse-v1', gci['worst']['member'])
        refused = {member['id'] for member in gci['members'] if member['verdict'] == acceptance.VERDICT_FAIL}
        self.assertEqual({'coarse-v1', 'coarse-v2'}, refused)
        production = next(m for m in gci['members'] if m['id'] == 'production')
        self.assertEqual(acceptance.VERDICT_PASS, production['verdict'])
        self.assertAlmostEqual(2.059474325283, production['margin'], places=9)

    def test_the_signed_q3_family_is_accepted(self):
        signed = next(f for f in self.record['families'] if f['family_id'].startswith('mesh-scatter signed family'))
        self.assertTrue(signed['accepted'])
        self.assertEqual('finer3', signed['worst']['member'])
        self.assertGreater(signed['worst']['margin'], 1.5)

    def test_the_geometry_arm_is_rejected(self):
        arm = next(f for f in self.record['families'] if f['family_id'].startswith('geometry arm B v2'))
        self.assertFalse(arm['accepted'])
        self.assertEqual(acceptance.VERDICT_FAIL, arm['members'][0]['verdict'])

    def test_every_member_carries_margin_and_distribution(self):
        for family in self.record['families']:
            for member in family['members']:
                self.assertIn('margin', member)
                self.assertIn('distribution', member)
                self.assertIn('record_sha256', member)

    def test_the_committed_record_matches_a_fresh_evaluation(self):
        committed = json.loads(FAMILY_GATE.read_text(encoding='utf-8'))
        fresh = self.record
        key = lambda family: [(m['id'], m['verdict'], round(m['margin'], 9)) for m in family['members']]
        self.assertEqual([(f['family_id'], f['accepted'], key(f)) for f in committed['families']],
                         [(f['family_id'], f['accepted'], key(f)) for f in fresh['families']])


class FailClosed(unittest.TestCase):
    def test_a_missing_member_record_refuses_the_family(self):
        decl = declarations()
        decl['families'] = [{'id': 'broken', 'rule': 'ALL_PASS', 'members': [
            {'id': 'ghost', 'role': 'production', 'quality_record': 'does-not-exist/mrf_volume_mesh_quality.json'}]}]
        record = acceptance.evaluate_declarations(decl, EVIDENCE)
        self.assertFalse(record['families'][0]['accepted'])
        self.assertEqual(acceptance.VERDICT_UNKNOWN, record['families'][0]['members'][0]['verdict'])
        self.assertTrue(record['families'][0]['rejections'])

    def test_a_family_without_a_declared_rule_is_refused(self):
        spec = json.loads(FROZEN_SPEC.read_text(encoding='utf-8'))
        self.assertIsNone(acceptance.acceptance_block(spec), 'the frozen CaseSpec declares no family - it is not judged')
        spec['quality']['mesh_acceptance'] = {'family': {'members': [{'id': 'x', 'role': 'production'}]},
                                              'margin': {'fragile_below': 1.5, 'target': 2.0},
                                              'localization_required_on_failure': True,
                                              'distribution_bands': [1.0, 2.0]}
        self.assertFalse(casespec.validate_case_spec(spec).valid)

    def test_fragile_needs_a_complete_waiver(self):
        decl = declarations()
        decl['families'] = [{'id': 'fragile', 'rule': 'ALL_PASS', 'members': [
            {'id': 'contrast-fix', 'role': 'production',
             'quality_record': 'mrf-gci-coarse-contrastfix-mesh-20261030a/mrf_volume_mesh_quality.json'}]}]
        no_waiver = acceptance.evaluate_declarations(decl, EVIDENCE)
        self.assertFalse(no_waiver['families'][0]['accepted'])
        self.assertEqual(acceptance.VERDICT_FRAGILE, no_waiver['families'][0]['members'][0]['verdict'])
        incomplete = acceptance.evaluate_declarations(decl, EVIDENCE, [{'member': 'contrast-fix', 'declared_by': 'x 2026-10-30'}])
        self.assertFalse(incomplete['families'][0]['accepted'])
        complete = acceptance.evaluate_declarations(decl, EVIDENCE, [{
            'member': 'contrast-fix', 'declared_by': 'user 2026-10-30', 'date': '2026-10-30',
            'reason': 'diagnostic contrast test, not a claim carrier',
            'localization_record': 'mrf-gci-coarse-contrastfix-mesh-20261030a/RESULT.json'}])
        self.assertTrue(complete['families'][0]['accepted'])
        self.assertEqual(1, len(complete['families'][0]['waivers']))


class LocalizationGate(unittest.TestCase):
    def test_a_failure_without_localization_is_not_citable(self):
        failure = json.loads((EVIDENCE / 'mrf-gci-coarse-mesh-20261030a/ATTEMPT-1-FAILED-QUALITY-GATE/'
                              'mrf_volume_mesh_quality.json').read_text(encoding='utf-8'))
        self.assertFalse(acceptance.localization_gate(failure, None)['citable'])
        bogus = {'localized': True, 'read_only': False, 'metrics': {}}
        self.assertFalse(acceptance.localization_gate(failure, bogus)['citable'])

    def test_the_archived_localization_makes_the_failure_citable(self):
        failure = json.loads((EVIDENCE / 'mrf-meshb13-20261030a/mrf_volume_mesh_quality.json').read_text(encoding='utf-8'))
        failure = dict(failure, quality_gate_passed=False)  # force the failure path on a real record
        localization = json.loads((EVIDENCE / 'mrf-badcellloc-b13-20261030a/'
                                   'mrf_bad_cell_localization.json').read_text(encoding='utf-8'))
        outcome = acceptance.localization_gate(failure, localization)
        self.assertTrue(outcome['citable'])
        self.assertEqual(3, len(outcome['worst_bbox']))


class SolveGate(unittest.TestCase):
    def _family(self, accepted=True, mesh_sha='abc'):
        return {'accepted': accepted, 'members': [{'id': 'production', 'mesh_sha256': mesh_sha}],
                'worst': {'verdict': acceptance.VERDICT_PASS}, 'waivers': []}

    def test_a_solve_plan_without_an_accepted_family_is_refused(self):
        plan = {'steps': [{'name': 'run-trial', 'macro': 'RunMrfTrial.java'}]}
        with self.assertRaises(acceptance.MeshAcceptanceError):
            acceptance.require_family_for_solve(plan, self._family(accepted=False), 'abc')

    def test_a_solve_plan_whose_mesh_is_not_in_the_family_is_refused(self):
        plan = {'steps': [{'name': 'run-trial', 'macro': 'RunMrfTrial.java'}]}
        with self.assertRaises(acceptance.MeshAcceptanceError):
            acceptance.require_family_for_solve(plan, self._family(), 'other-sha')

    def test_a_mesh_only_plan_is_not_gated(self):
        plan = {'steps': [{'name': 'execute-mesh', 'macro': 'ExecuteMrfVolumeMesh.java'}]}
        acceptance.require_family_for_solve(plan, self._family(accepted=False), 'abc')  # no exception

    def test_the_chain_wiring_exists(self):
        """pre_submit_check must refuse a solve plan that declares no family gate (measured on a real plan)."""
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/pre_submit_check.py'), 'part57'],
                                capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertTrue(any('mesh_family_gate' in failure for failure in report['failures']), report['failures'])

    def test_the_solve_plan_shape_is_what_the_gate_expects(self):
        plan = chain.PLANS['part57']
        self.assertIn('mrf_diagnostic_meshed.sim', plan['inputs'])
        self.assertTrue(any(step['macro'] in ('RunMrfTrial.java', 'RunMrfSegment.java') for step in plan['steps']))


if __name__ == '__main__':
    unittest.main()
