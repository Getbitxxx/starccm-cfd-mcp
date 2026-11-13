"""O9 ratchets for the zero-compute reference-applicability audit (2026-10-30).

The audit answers "can the signed UIUC reference explain the J=0.647 CP failure?" from stored bytes only.
These tests pin (a) the audit's inputs are the pinned bytes, (b) its conclusions still hold, and (c) the
committed evidence JSON is exactly what the script regenerates today — so a silent reference/measurement
change cannot leave a stale conclusion in the evidence tree.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ref_applicability_audit as audit  # noqa: E402

CASE = ROOT / 'cases/uav-prop-apc10x7e'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
OUTPUT = EVIDENCE / 'REFERENCE-APPLICABILITY-CHECK-20261030.json'
SIGNED_TOLERANCE = {'ct_abs_floor': 0.0035, 'ct_rel': 0.12, 'cp_abs_floor': 0.003, 'cp_rel': 0.12}
PREVIOUS_TOLERANCE = {'ct_abs_floor': 0.0035, 'ct_rel': 0.08, 'cp_abs_floor': 0.003, 'cp_rel': 0.08}


class ReferenceApplicabilityAuditRatchet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = audit.build_audit()
        cls.points = {p['point']: p for p in cls.audit['measured_points']}

    def test_audit_inputs_are_the_pinned_bytes(self):
        self.assertTrue(self.audit['reference']['selected_run']['file_pin_matches'])
        self.assertTrue(self.audit['reference']['rows_agree'])
        for point in self.audit['measured_points']:
            path = ROOT / point['result_path']
            self.assertTrue(path.is_file(), point['point'])
            self.assertEqual(point['result_sha256'], hashlib.sha256(path.read_bytes()).hexdigest(), point['point'])
        repeat_file = ROOT / self.audit['reference']['repeatability_csv']['path']
        self.assertEqual(self.audit['reference']['repeatability_csv']['sha256'],
                         hashlib.sha256(repeat_file.read_bytes()).hexdigest())

    def test_signed_tolerance_and_its_history_are_recorded(self):
        """The 2026-10-30 re-signing (12%, floors unchanged) must be recorded and the previous 8% kept."""
        reference = json.loads((CASE / 'reference.json').read_text(encoding='utf-8'))
        tolerance = reference['acceptance']['tolerance']
        for key, value in SIGNED_TOLERANCE.items():
            self.assertEqual(value, tolerance[key], key)
        for key, value in PREVIOUS_TOLERANCE.items():
            self.assertEqual(value, tolerance['previous_signing'][key], key)
        live = json.loads((CASE / 'case_spec.json').read_text(encoding='utf-8'))['acceptance']['reference_tolerance']
        for key in ('ct_abs_floor', 'ct_rel', 'cp_abs_floor', 'cp_rel', 'status', 'signed_by', 'signed_at'):
            self.assertEqual(tolerance[key], live[key], key)
        self.assertEqual('SIGNED', tolerance['status'])

    def test_historical_failing_point_and_the_reference_cannot_explain_it(self):
        """J=0.647 CP (and J=0.455 CT) failed the PREVIOUS 8% rule; under the re-signed 12% rule both pass.
        The reference-side budget must stay small relative to the observed deviations under either reading."""
        point = self.points['fully_turbulent_J0647']
        self.assertFalse(point['previous_rule']['cp_pass'])
        previous_ratio = abs(point['dcp']) / point['previous_rule']['cp_tolerance']
        self.assertTrue(1.0 < previous_ratio < 1.3, previous_ratio)
        self.assertTrue(point['cp_pass'])  # current signed 12% rule
        self.assertTrue(self.points['fully_turbulent_J0548']['cp_pass'])
        share = self.audit['reference_side_budget']['per_point']['fully_turbulent_J0647']
        self.assertLess(share['reference_share_of_observed_gap_cp'], 0.2)
        self.assertLess(share['reference_share_of_observed_gap_ct'], 0.5)

    def test_condition_match_is_declared_not_corrected(self):
        match = self.audit['condition_match']
        self.assertTrue(64000 < match['re75'] < 67000)
        self.assertTrue(0.22 < match['tip_mach'] < 0.24)
        self.assertIn('no Re/Mach mismatch', match['note'])
        for entry in self.audit['measured_points']:
            self.assertIn(entry['model_form'], ('FULLY_TURBULENT_SST', 'GAMMA_RE_THETA'))

    def test_committed_evidence_equals_a_fresh_audit(self):
        """Byte ratchet: the stored JSON must be what the script produces from today's bytes."""
        self.assertTrue(OUTPUT.is_file(), 'run scripts/ref_applicability_audit.py')
        committed = OUTPUT.read_text(encoding='utf-8')
        regenerated = json.dumps(self.audit, indent=1, ensure_ascii=False) + '\n'
        self.assertEqual(committed, regenerated)


if __name__ == '__main__':
    unittest.main()
