"""O9 ratchets for the model-form swing (diagnostic A) evidence.

The swing must be computed from the recorded runs (gamma-Re_theta / fully turbulent - 1), must report missing points
instead of silently averaging fewer, and the committed evidence must equal a fresh computation.  These are
diagnostics: the script must never present them as signed points.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import model_form_swing as swing  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/MODEL-FORM-SWING-20261030a.json'


class ModelFormSwingRatchet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = swing.build()

    def test_swing_is_gamma_over_fully_turbulent(self):
        for tag, row in self.data['points'].items():
            ft, gt = row['fully_turbulent'], row['gamma_re_theta']
            self.assertAlmostEqual(gt['ct'] / ft['ct'] - 1.0, row['swing']['ct_rel'], places=15)
            self.assertAlmostEqual(gt['cp_abs'] / ft['cp_abs'] - 1.0, row['swing']['cp_rel'], places=15)
            self.assertEqual('campaigns/uav-2026/evidence/uav-prop-apc10x7e/' + ft['run_id'] + '/mrf_trial_audit.json',
                             ft['audit'])

    def test_non_citable_pairs_are_excluded_with_a_reason(self):
        """The failed gamma-Re_theta low-J runs must never contribute a swing number (measured lesson: they were
        initially picked up and silently changed the summary)."""
        excluded = self.data['excluded_pairs']
        self.assertIn('0.455', excluded)
        self.assertIn('0.504', excluded)
        for tag, entry in excluded.items():
            self.assertIn('may not be cited', entry['reason'], tag)
            self.assertFalse(entry['gamma_re_theta']['citable'], tag)
        # and the surviving points are exactly the citable ones
        self.assertEqual({'0.548', '0.601', '0.647'}, set(self.data['points']))

    def test_citability_comes_from_convergence_evidence(self):
        evidence = swing.convergence_evidence(swing.FT['0.548'])
        self.assertTrue(evidence['converged'])
        self.assertTrue(evidence['gates_passed'])
        # audit-only continuation runs resolve their convergence through the declared input chain
        audit_only = swing.GRT['0.647']
        chain_evidence = swing.convergence_evidence(audit_only)
        self.assertTrue(chain_evidence['converged'], chain_evidence)
        self.assertIn('->', chain_evidence['source'])
        failed_low_j = swing.convergence_evidence(swing.GRT['0.455'])
        self.assertFalse(failed_low_j['converged'])

    def test_missing_points_are_reported_not_dropped(self):
        all_points = {'0.455', '0.504', '0.548', '0.601', '0.647'}
        accounted = set(self.data['points']) | set(self.data['missing_points']) | set(self.data['excluded_pairs'])
        self.assertEqual(all_points, accounted)
        self.assertFalse(set(self.data['points']) & set(self.data['missing_points']))
        self.assertFalse(set(self.data['points']) & set(self.data['excluded_pairs']))
        if self.data['missing_points']:
            self.assertFalse(any(tag in self.data['points'] for tag in self.data['missing_points']))

    def test_tolerance_comes_from_the_signed_reference(self):
        rule = swing.signed_rule()
        self.assertEqual(0.12, rule['ct_rel'])
        self.assertEqual(0.12, rule['cp_rel'])
        for row in self.data['points'].values():
            ref = row['reference']
            self.assertAlmostEqual(max(rule['ct_abs_floor'], rule['ct_rel'] * ref['ct_ref']),
                                   row['tolerance']['ct'], places=15)
            self.assertAlmostEqual(max(rule['cp_abs_floor'], rule['cp_rel'] * ref['cp_ref']),
                                   row['tolerance']['cp'], places=15)

    def test_model_form_does_not_explain_the_ct_j_tilt(self):
        """Both models' CT error grows with J (positive slope), so the model form alone cannot remove the tilt."""
        summary = self.data['summary']
        if len(self.data['points']) < 2:
            self.skipTest('not enough paired points yet')
        self.assertGreater(summary['ct_error_slope_fully_turbulent'], 0.0)
        self.assertGreater(summary['ct_error_slope_gamma_re_theta'], 0.0)

    def test_committed_evidence_matches_a_fresh_run(self):
        if not EVIDENCE.is_file():
            self.skipTest('model-form evidence not written yet')
        committed = json.loads(EVIDENCE.read_text(encoding='utf-8'))
        fresh = json.loads(json.dumps(self.data))
        self.assertEqual(committed['points'].keys(), fresh['points'].keys())
        self.assertEqual(committed['missing_points'], fresh['missing_points'])
        for tag in fresh['points']:
            self.assertEqual(committed['points'][tag]['swing'], fresh['points'][tag]['swing'], tag)

    def test_it_is_not_a_signed_point(self):
        self.assertIn('not signed points', self.data['caveat'])
        self.assertIn('diagnostic A', self.data['scope'])


if __name__ == '__main__':
    unittest.main()
