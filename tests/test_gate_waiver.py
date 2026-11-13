"""Gate waivers: they record an unmet gate and must never become a pass.

The state machine is index-ranked (support_matrix picks a scenario's certificate with
max()), so a waiver is deliberately an orthogonal record, not a fifth state. These tests
pin both halves: the waiver cannot raise a state, and the validator refuses every way of
dressing a failure up as an acceptable one.
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import quality_contract as q  # noqa: E402

WAIVER = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1/MESH-GATE-WAIVER-20261102.json'
CASESPEC = ROOT / 'cases/uav-trapwing-hlpw1/case_spec.json'


class WaiverValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(WAIVER.read_text(encoding='utf-8'))
        cls.spec = json.loads(CASESPEC.read_text(encoding='utf-8'))

    def check(self, document, **kwargs):
        return q.validate_waiver(document, case_spec=self.spec,
                                 base_dir=WAIVER.parent, **kwargs)

    def test_the_real_waiver_is_structurally_valid_and_still_not_a_pass(self):
        summary, errors, checks = self.check(self.document)
        self.assertEqual(errors, [])
        self.assertTrue(summary['valid'])
        self.assertEqual(summary['status'], 'PENDING_USER_SIGNATURE')
        self.assertFalse(summary['usable'], 'an unsigned waiver must not be citable')
        self.assertAlmostEqual(summary['measured'], 0.0034776225434174635, places=12)
        self.assertAlmostEqual(summary['margin'], 0.6955, places=4)
        self.assertTrue(all(item['ok'] for item in checks))
        self.assertIn('waiver:gate_not_failed', [item['check'] for item in checks])

    def test_a_waiver_cannot_raise_the_state(self):
        self.assertNotIn('WAIVED', q.STATES)
        self.assertEqual(len(q.STATES), 4)

    def test_threshold_that_disagrees_with_the_case_spec_is_refused(self):
        document = copy.deepcopy(self.document)
        document['gate']['threshold'] = 0.004
        _, errors, _ = self.check(document)
        self.assertIn('waiver:threshold_differs_from_case_spec', errors)

    def test_a_gate_that_actually_passes_is_not_waivable(self):
        document = copy.deepcopy(self.document)
        document['measured']['volume_change_min'] = 0.006
        _, errors, _ = self.check(document)
        self.assertIn('waiver:gate_not_failed', errors)

    def test_tampered_localisation_digest_is_refused(self):
        document = copy.deepcopy(self.document)
        document['measured']['localization']['sha256'] = '0' * 64
        _, errors, _ = self.check(document)
        self.assertIn('waiver:localization_sha_differs', errors)

    def test_missing_localisation_is_refused(self):
        document = copy.deepcopy(self.document)
        document['measured'].pop('localization')
        _, errors, _ = self.check(document)
        self.assertIn('waiver:localization_missing', errors)

    def test_signed_status_requires_a_name_and_a_date(self):
        document = copy.deepcopy(self.document)
        document['signature_status'] = 'SIGNED'
        summary, errors, _ = self.check(document)
        self.assertIn('waiver:signed_by_missing', errors)
        self.assertIn('waiver:signed_on_missing', errors)
        self.assertFalse(summary['usable'], 'an invalid record must never be citable')

        document['signed_by'] = '   '
        document['signed_on'] = '2026-11-02'
        summary, errors, _ = self.check(document)
        self.assertIn('waiver:signed_by_missing', errors)
        self.assertFalse(summary['usable'], 'whitespace is not a signature')

    def test_a_signed_waiver_is_citable_only_against_a_frozen_case_spec(self):
        document = copy.deepcopy(self.document)
        document['signature_status'] = 'SIGNED'
        document['signed_by'] = 'user'
        document['signed_on'] = '2026-11-02'
        summary, errors, _ = self.check(document)
        self.assertEqual(errors, [])
        self.assertFalse(summary['usable'], 'the real CaseSpec is not frozen')
        self.assertIn('waiver:case_spec_not_frozen', summary['conditions'])

        frozen = dict(self.spec, case_spec_frozen=True)
        summary, errors, _ = q.validate_waiver(document, case_spec=frozen, base_dir=WAIVER.parent)
        self.assertEqual(errors, [])
        self.assertEqual(summary['conditions'], [])
        self.assertTrue(summary['usable'])

    def test_measured_must_be_strictly_below_the_gate(self):
        document = copy.deepcopy(self.document)
        document['measured']['volume_change_min'] = self.document['gate']['threshold']
        _, errors, _ = self.check(document)
        self.assertIn('waiver:gate_not_failed', errors)

    def test_non_numeric_and_non_finite_measurements_are_refused(self):
        for value in (True, '0.0005', None, float('nan'), float('inf'), [0.0005]):
            document = copy.deepcopy(self.document)
            document['measured']['volume_change_min'] = value
            with self.subTest(value=repr(value)):
                summary, errors, _ = self.check(document)
                self.assertIn('waiver:measured_missing', errors)
                self.assertFalse(summary['usable'])

    def test_non_finite_threshold_is_refused(self):
        document = copy.deepcopy(self.document)
        document['gate']['threshold'] = float('inf')
        _, errors, _ = self.check(document)
        self.assertIn('waiver:gate_threshold', errors)

    def test_case_id_must_match_the_case_spec(self):
        document = copy.deepcopy(self.document)
        document['case_id'] = 'some-other-case'
        _, errors, _ = self.check(document)
        self.assertIn('waiver:case_id_differs_from_case_spec', errors)

    def test_blank_scope_limits_are_refused(self):
        document = copy.deepcopy(self.document)
        document['scope_limits'] = ['   ']
        _, errors, _ = self.check(document)
        self.assertIn('waiver:scope_limits_missing', errors)

    def test_a_signed_waiver_without_a_cross_checked_threshold_is_not_usable(self):
        # Without the CaseSpec the threshold would be self-declared, so signing alone is
        # not enough: the cross-check is part of what makes a waiver citable.
        document = copy.deepcopy(self.document)
        document['signature_status'] = 'SIGNED'
        document['signed_by'] = 'user'
        document['signed_on'] = '2026-11-02'
        summary, errors, _ = q.validate_waiver(document, case_spec=None, base_dir=WAIVER.parent)
        self.assertEqual(errors, [])
        self.assertFalse(summary['usable'])
        self.assertFalse(summary['checked_against_case_spec'])

    def test_revoked_waiver_is_invalid_authority_but_structurally_parseable(self):
        document = copy.deepcopy(self.document)
        document['signature_status'] = 'REVOKED'
        summary, errors, _ = self.check(document)
        self.assertEqual(errors, [])
        self.assertFalse(summary['usable'])

    def test_unknown_status_is_refused(self):
        document = copy.deepcopy(self.document)
        document['signature_status'] = 'APPROVED'
        _, errors, _ = self.check(document)
        self.assertIn('waiver:signature_status', errors)

    def test_scope_limits_are_mandatory(self):
        document = copy.deepcopy(self.document)
        document['scope_limits'] = []
        _, errors, _ = self.check(document)
        self.assertIn('waiver:scope_limits_missing', errors)

    def test_mesh_sha_must_match_when_the_caller_knows_it(self):
        _, errors, _ = self.check(self.document, mesh_sha256='f' * 64)
        self.assertIn('waiver:mesh_sha_differs', errors)
        _, errors, _ = self.check(self.document, mesh_sha256=self.document['mesh']['sha256'])
        self.assertEqual(errors, [])

    def test_garbage_does_not_raise(self):
        summary, errors, _ = q.validate_waiver({}, case_spec=None, base_dir=None)
        self.assertFalse(summary['valid'])
        self.assertIn('waiver:schema_version', errors)
        self.assertIn('waiver:gate_metric', errors)
        self.assertIn('waiver:measured_missing', errors)


class WaiverDiscoveryTests(unittest.TestCase):
    def test_discovery_finds_the_campaign_waiver(self):
        items = q.load_waivers(case_spec_path='cases/uav-trapwing-hlpw1/case_spec.json')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['summary']['run_id'], 'trapwing-core005-bl8mm-20261102')
        self.assertEqual(len(items[0]['sha256']), 64)

    def test_case_spec_is_resolved_from_the_declared_source(self):
        # gate.source names the CaseSpec; it must be resolvable for the threshold check to
        # run at all, otherwise the waiver stays non-usable.
        without = q.load_waivers()[0]
        self.assertTrue(without['summary']['checked_against_case_spec'])
        self.assertEqual(without['errors'], [])
        forged = copy.deepcopy(json.loads(WAIVER.read_text(encoding='utf-8')))
        self.assertIsNone(q.declared_case_spec(dict(forged, gate=dict(forged['gate'], source='cases/nope/case_spec.json:quality.mesh_min_volume_change'))))
        self.assertIsNone(q.declared_case_spec(dict(forged, gate=dict(forged['gate'], source='not-a-path'))))

    def test_recorded_case_spec_sha_is_verified_not_trusted(self):
        items = q.load_waivers()
        self.assertNotIn('waiver:case_spec_sha_differs', items[0]['errors'])
        document = json.loads(WAIVER.read_text(encoding='utf-8'))
        document['gate']['case_spec_sha256'] = '0' * 64
        work = Path(tempfile.mkdtemp(prefix='waiver-', dir=ROOT / 'tmp'))
        try:
            path = work / 'MESH-GATE-WAIVER-sha.json'
            path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
            item = q.load_waivers([path])[0]
            self.assertIn('waiver:case_spec_sha_differs', item['errors'])
            self.assertFalse(item['summary']['usable'])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_lookup_is_by_run_id_and_absence_is_empty(self):
        self.assertEqual(len(q.waivers_for_run('trapwing-core005-bl8mm-20261102')), 1)
        self.assertEqual(q.waivers_for_run('trapwing-te-prism-20261102'), [],
                         'the 8M arm is not covered by this waiver')
        self.assertEqual(q.waivers_for_run('nope'), [])

    def test_report_block_says_it_is_not_a_pass(self):
        block = q.render_waiver_block(
            q.load_waivers(case_spec_path='cases/uav-trapwing-hlpw1/case_spec.json'))
        self.assertIn('不是通过', block)
        self.assertIn('PENDING_USER_SIGNATURE', block)
        self.assertIn('不可引用', block)
        self.assertIn('case_spec_not_frozen', block)
        self.assertIn('0.696', block)
        self.assertEqual(q.render_waiver_block([]), '')


if __name__ == '__main__':
    unittest.main()
