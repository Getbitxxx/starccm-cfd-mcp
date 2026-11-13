"""Real archived U12 counterexamples: stale extent prose cannot certify a ratio."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_record_gate import assert_citable
AUDIT = ROOT / 'test-evidence/resource-contract-20261009/engineering-reference-audit'

class EngineeringReferenceAudit(unittest.TestCase):
    def setUp(self):
        self.binding = json.loads((AUDIT / 'case-match-contract.json').read_text())
        self.paths = {Path(x['path']).name: ROOT / x['path'] for x in self.binding['inputs']}

    def test_real_bounds_reject_stale_extent_ratio_without_rewriting_spec(self):
        entry = next(x for x in self.binding['inputs'] if Path(x['path']).name == 'case_spec.json')
        path = ROOT / entry['path']
        original = path.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), entry['sha256'])
        spec = json.loads(original)
        self.assertIn('1.43%', spec['domain']['extent_basis'])
        bounds = spec['domain']['outer_bounds_m']
        area = spec['geometry']['reference_area_m2']
        actual_percent = 100 * area / ((bounds[4]-bounds[1]) * (bounds[5]-bounds[2]))
        self.assertAlmostEqual(actual_percent, 0.634920634920635, places=12)
        self.assertGreater(abs(actual_percent - 1.43), 0.79)
        self.assertEqual(path.read_bytes(), original)

    def test_re_is_derived_from_citable_native_properties_not_ti_measurement(self):
        native_path = self.paths['property_nearwall_readback.json']
        self.assertEqual(assert_citable(native_path)['state'], 'CITABLE')
        native = json.loads(native_path.read_text())
        spec = json.loads(self.paths['case_spec.json'].read_text())
        props = native['properties']
        reynolds = props['inlet_area_mean_Density'] * spec['flow']['velocity_m_s'][0] * spec['geometry']['characteristic_length_m'] / props['inlet_area_mean_DynamicViscosity']
        self.assertAlmostEqual(reynolds, self.binding['case']['Re_D'], places=8)
        self.assertIn('derived', self.binding['case']['Re_basis'])
        self.assertEqual(self.binding['case']['inlet_turbulence_intensity_config'], 0.05)
        self.assertIn('not exported', self.binding['case']['actual_turbulence_measurement'])
        self.assertEqual(native['start_iteration'], native['end_iteration'])
        self.assertEqual(native['input_sim_sha256'], native['input_sim_sha256_after'])

    def test_real_candidates_cannot_certify_unmatched_reference(self):
        document = json.loads((AUDIT / 'reference-candidates.json').read_text())
        candidates = {c['id']: c for c in document['candidates']}
        case = self.binding['case']
        self.assertLess(case['Re_D'], candidates['Zdravkovich1989']['Re_range'][0])
        self.assertLess(case['L_over_D'], candidates['Shepard2022']['AR_range'][0])
        self.assertLess(case['L_over_D'], candidates['Tips2024']['AR_range'][0])
        self.assertGreater(case['inlet_turbulence_intensity_config'], candidates['Tips2024']['inlet_TI_upper'])
        self.assertFalse(document['matching_reference_pass'])
        self.assertFalse(case['matched_experimental_reference'])
        for candidate in candidates.values():
            self.assertIsNone(candidate['reference_value'])
            self.assertNotEqual(candidate['disposition'], 'matched')

if __name__ == '__main__':
    unittest.main()
