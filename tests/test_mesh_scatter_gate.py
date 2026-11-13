"""O9 ratchets for the user-signed mesh-scatter numerical criterion (2026-10-30 revision).

The criterion is a SIGNED number in reference.json, not a constant in the script: these tests pin that the script
reads it, that the metric is the production-vs-any-finer deviation (not a GCI), that the domain gate uses half the
measured scatter, and that a value above the signed limit FAILS.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mesh_scatter_gate as gate  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/MESH-SCATTER-GATE-20261030a.json'
REFERENCE = ROOT / 'cases/uav-prop-apc10x7e/reference.json'


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


class MeshScatterCriterionRatchet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = gate.evaluate()

    def test_threshold_and_metric_come_from_the_signed_reference(self):
        criterion = gate.signed_criterion()
        self.assertEqual('mesh_scatter_bound', criterion['type'])
        self.assertEqual(0.05, criterion['value'])
        self.assertEqual('user', criterion['signed_by'])
        self.assertTrue(criterion['signed_at'])
        self.assertIn('max over all admissible refined levels', criterion['metric'])
        self.assertEqual(criterion['value'], self.result['signed_criterion']['value'])
        self.assertFalse(self.result['is_a_gci'])

    def test_deviation_is_measured_from_the_production_level(self):
        levels = self.result['levels']
        production = levels['production']
        for quantity, key in (('ct', 'ct'), ('cp', 'cp_abs')):
            worst = 0.0
            for name, level in levels.items():
                if name == 'production':
                    continue
                expected = abs(level[key] - production[key]) / abs(production[key])
                self.assertAlmostEqual(expected, self.result['deviations_from_production'][quantity][name], places=15)
                worst = max(worst, expected)
            self.assertAlmostEqual(worst, self.result['worst'][quantity], places=15)

    def test_gate_passes_at_the_measured_values_and_would_fail_above_5pct(self):
        self.assertEqual('PASS', self.result['gate'])
        self.assertLess(self.result['worst']['ct'], 0.05)
        self.assertLess(self.result['worst']['cp'], 0.05)
        # a synthetic refinement that moves CT by 6% must FAIL under the same signed limit
        levels = {name: dict(level) for name, level in self.result['levels'].items()}
        levels['B17']['ct'] = levels['production']['ct'] * 1.06
        broken = gate.evaluate(levels)
        self.assertEqual('FAIL', broken['gate'])
        self.assertFalse(broken['checks']['ct']['pass'])

    def test_domain_gate_is_half_the_measured_scatter(self):
        domain = gate.domain_check(self.result)
        self.assertEqual('PASS', domain['gate'])
        for quantity in ('ct', 'cp'):
            self.assertAlmostEqual(0.5 * self.result['worst'][quantity], domain['thresholds'][quantity], places=15)
            self.assertLessEqual(domain['checks'][quantity]['delta'], domain['thresholds'][quantity])
        self.assertEqual(
            hashlib.sha256(Path(ROOT / domain['evidence']).read_bytes()).hexdigest(), domain['evidence_sha256'])

    def test_committed_evidence_matches_a_fresh_run(self):
        if not EVIDENCE.is_file():
            self.skipTest('mesh-scatter evidence not written yet')
        committed = loads(EVIDENCE)
        fresh = json.loads(json.dumps(gate.evaluate()))
        self.assertEqual(committed['mesh']['gate'], fresh['gate'])
        self.assertEqual(committed['mesh']['worst'], fresh['worst'])
        self.assertEqual(committed['domain']['gate'], gate.domain_check(fresh)['gate'])

    def test_reference_keeps_the_superseded_gci_requirement(self):
        reference = loads(REFERENCE)['acceptance']
        self.assertTrue(any('mesh-scatter numerical criterion' in item for item in reference['q3_requires']))
        previous = reference['q3_requires_previous']['list']
        self.assertTrue(any('three-mesh GCI' in item for item in previous))
        self.assertIn('not', reference['q3_requires_previous']['note'].lower())


if __name__ == '__main__':
    unittest.main()
