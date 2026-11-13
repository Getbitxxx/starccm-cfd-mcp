"""O9 ratchets for the domain-sensitivity gate (Q3 `q3_requires`: |dCT|/CT and |dCP|/CP at J=0.548 <= 0.5*GCI_fine).

The gate must never report PASS without a GCI value: the delta alone is a measurement, and the pre-registered
rule compares it with half the fine-grid GCI.  These tests pin the baseline numbers, the NOT_EVALUABLE default
and the PASS/FAIL branches on synthetic GCI values, writing to a temporary path (never the evidence file).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import domain_sensitivity_gate as gate  # noqa: E402

EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
LEVEL_10D = EV / 'mrf-domain10d-j0548-20261030a'
SCRIPT = ROOT / 'scripts/domain_sensitivity_gate.py'


def run_script(*args: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'gate.json'
        result = subprocess.run([sys.executable, str(SCRIPT), '--out', str(out), *args],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        return json.loads(out.read_text(encoding='utf-8'))


class DomainSensitivityGateRatchet(unittest.TestCase):
    def test_baseline_is_the_archived_fully_turbulent_j0548_measurement(self):
        base = gate.read_point(gate.BASE_5D)
        self.assertEqual(0.053135339356479315, base['ct'])
        self.assertEqual(0.043598821858364875, base['cp_abs'])
        self.assertTrue(base['converged'])

    def test_without_gci_the_gate_is_not_evaluable_never_pass(self):
        if not (LEVEL_10D / 'mrf_trial_audit.json').is_file():
            self.skipTest('10D J=0.548 run has not completed yet')
        report = run_script()
        self.assertEqual('NOT_EVALUABLE_GCI_MISSING', report['gate']['status'])
        self.assertIn('NOT_COMPLETED', report['gate']['reason'])
        delta = report['delta']
        self.assertAlmostEqual(delta['relative_ct'], abs(delta['ct']) / report['baseline_5d']['ct'], places=12)
        self.assertAlmostEqual(delta['relative_cp'], abs(delta['cp_abs']) / report['baseline_5d']['cp_abs'], places=12)

    def test_gate_branches(self):
        if not (LEVEL_10D / 'mrf_trial_audit.json').is_file():
            self.skipTest('10D J=0.548 run has not completed yet')
        loose = run_script('--gci-ct', '10', '--gci-cp', '10')
        self.assertEqual('PASS', loose['gate']['status'], loose['gate'])
        tight = run_script('--gci-ct', '1e-06', '--gci-cp', '1e-06')
        self.assertEqual('FAIL', tight['gate']['status'], tight['gate'])
        self.assertEqual(0.5 * 1e-06, tight['gate']['limit_ct'])

    def test_committed_evidence_matches_a_fresh_run(self):
        evidence = EV / 'DOMAIN-SENSITIVITY-20261030a.json'
        if not evidence.is_file():
            self.skipTest('evidence not written yet')
        self.assertEqual(json.loads(evidence.read_text(encoding='utf-8')), run_script())


if __name__ == '__main__':
    unittest.main()
