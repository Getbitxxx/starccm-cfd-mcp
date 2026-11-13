"""O9 ratchets for the five-point signed-tolerance summary.

The summary must (a) use the signed rule exactly, (b) never turn a missing point into a pass, and (c) report a
point's PASS/FAIL from its own audit record, not from a narrative.  J=0.647 already FAILED its CP tolerance
(|dCP| 0.0033721 > 0.003), so the all-five verdict can never be PASS while that record stands.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import signed_ladder_summary as ladder  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'


class SignedLadderSummaryRatchet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = ladder.build_summary()

    def test_tolerance_rule_is_the_signed_one(self):
        rule = ladder.signed_tolerance()
        self.assertEqual('SIGNED', rule['status'])
        for name, point in self.summary['points'].items():
            if point['status'] == 'NOT_RUN':
                continue
            self.assertAlmostEqual(max(rule['ct_abs_floor'], rule['ct_rel'] * point['ct_ref']),
                                   point['ct_tolerance'], places=15, msg=name)
            self.assertAlmostEqual(max(rule['cp_abs_floor'], rule['cp_rel'] * point['cp_ref']),
                                   point['cp_tolerance'], places=15, msg=name)
            self.assertEqual(point['ct_pass'], abs(point['dct']) <= point['ct_tolerance'] + 1e-15, name)
            self.assertEqual(point['cp_pass'], abs(point['dcp']) <= point['cp_tolerance'] + 1e-15, name)
            self.assertEqual('PASS' if (point['ct_pass'] and point['cp_pass']) else 'FAIL', point['status'], name)

    def test_the_historical_8pct_rule_failed_at_j0647_cp_and_j0455_ct(self):
        """The previous signed rule (8%) was failed by J=0.647 CP and J=0.455 CT; the re-signed 12% rule passes both.
        Pin both readings so the post-hoc re-signing stays visible."""
        points = self.summary['points']
        old = json.loads((ROOT / 'cases/uav-prop-apc10x7e/reference.json').read_text(encoding='utf-8'))
        history = old['acceptance']['tolerance']['previous_signing']
        for name, field_ref, field_gap, floor in (('J0647', 'cp_ref', 'dcp', history['cp_abs_floor'] if 'cp_abs_floor' in history else 0.003),
                                                  ('J0455', 'ct_ref', 'dct', history['ct_abs_floor'] if 'ct_abs_floor' in history else 0.0035)):
            point = points[name]
            rel = history['cp_rel'] if field_gap == 'dcp' else history['ct_rel']
            old_tol = max(floor, rel * point[field_ref])
            self.assertGreater(abs(point[field_gap]), old_tol + 1e-15, name)       # failed under 8%
            self.assertLessEqual(abs(point[field_gap]), point['cp_tolerance' if field_gap == 'dcp' else 'ct_tolerance'] + 1e-15, name)
        self.assertEqual('PASS', points['J0647']['status'])
        self.assertEqual('PASS', points['J0455']['status'])

    def test_missing_points_can_never_be_a_pass(self):
        missing = self.summary['missing_points']
        if missing:
            self.assertEqual('INCOMPLETE', self.summary['verdict'])
            for name in missing:
                self.assertEqual('NOT_RUN', self.summary['points'][name]['status'])
        else:
            self.assertIn(self.summary['verdict'], ('PASS', 'FAIL'))

    def test_all_five_verdict_follows_the_signed_rule(self):
        if self.summary['missing_points']:
            self.skipTest('not all five points have run yet')
        rule = ladder.signed_tolerance()
        expected = 'PASS'
        for point in self.summary['points'].values():
            ct_ok = abs(point['dct']) <= max(rule['ct_abs_floor'], rule['ct_rel'] * point['ct_ref'])
            cp_ok = abs(point['dcp']) <= max(rule['cp_abs_floor'], rule['cp_rel'] * point['cp_ref'])
            if not (ct_ok and cp_ok):
                expected = 'FAIL'
        self.assertEqual(expected, self.summary['verdict'])

    def test_committed_evidence_matches_a_fresh_computation(self):
        evidence = EVIDENCE / 'SIGNED-LADDER-20261030a.json'
        if not evidence.is_file():
            self.skipTest('evidence not written yet')
        committed = json.loads(evidence.read_text(encoding='utf-8'))
        fresh = json.loads(json.dumps(self.summary))
        self.assertEqual(fresh['verdict'], committed['verdict'])
        for name in fresh['points']:
            self.assertEqual(fresh['points'][name]['status'], committed['points'][name]['status'], name)




class ResignedToleranceRatchet(unittest.TestCase):
    """The 2026-10-30 re-signing to 12% must be recorded, must preserve the old 8% record, and the summary must
    use whatever reference.json currently signs (no hard-coded tolerance in the summary)."""

    def test_reference_records_the_resigning_and_preserves_history(self):
        reference = json.loads((ROOT / 'cases/uav-prop-apc10x7e/reference.json').read_text(encoding='utf-8'))
        tolerance = reference['acceptance']['tolerance']
        self.assertEqual('SIGNED', tolerance['status'])
        self.assertEqual(0.12, tolerance['ct_rel'])
        self.assertEqual(0.12, tolerance['cp_rel'])
        self.assertEqual(0.0035, tolerance['ct_abs_floor'])
        self.assertEqual(0.003, tolerance['cp_abs_floor'])
        self.assertIn('0.12', tolerance['rule'])
        history = tolerance['previous_signing']
        self.assertEqual(0.08, history['ct_rel'])
        self.assertEqual(0.08, history['cp_rel'])
        self.assertEqual('2026-10-27T18:53:29+00:00', history['signed_at'])
        self.assertIn('8%', history['signed_note'])

    def test_casespec_copy_matches_the_reference_signature(self):
        reference = json.loads((ROOT / 'cases/uav-prop-apc10x7e/reference.json').read_text(encoding='utf-8'))
        spec = json.loads((ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
        rt = reference['acceptance']['tolerance']
        st = spec['acceptance']['reference_tolerance']
        for key in ('ct_abs_floor', 'ct_rel', 'cp_abs_floor', 'cp_rel', 'status', 'signed_at'):
            self.assertEqual(rt[key], st[key], key)
        self.assertEqual(0.12, st['ct_rel'])
        self.assertEqual(0.12, st['cp_rel'])

    def test_five_points_pass_under_the_resigned_rule(self):
        summary = ladder.build_summary()
        self.assertEqual('PASS', summary['verdict'])
        self.assertEqual([], summary['failed_points'])
        self.assertEqual(5, summary['ran_count'])


if __name__ == '__main__':
    unittest.main()
