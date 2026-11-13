import copy
import unittest
from run_registered_workflow_v2 import valid_readback_environment_artifacts, READBACK_DIAGNOSTIC_ARTIFACTS

class DiagnosticAdmission(unittest.TestCase):
    def test_exact_diagnostics_and_fail_closed(self):
        good = {'env': {'PROPERTY_NEARWALL_READBACK_ONLY': '1', 'STATIC_FLOW_DIAGNOSTICS': '1'},
                'stages': [{'required': sorted(READBACK_DIAGNOSTIC_ARTIFACTS)}]}
        self.assertTrue(valid_readback_environment_artifacts(good))
        for mutate in ('value', 'extra_env', 'missing_csv', 'extra_csv'):
            bad = copy.deepcopy(good)
            if mutate == 'value': bad['env']['STATIC_FLOW_DIAGNOSTICS'] = '0'
            elif mutate == 'extra_env': bad['env']['EXTERNAL_FLOW_MAX_ITERATIONS'] = '1000'
            elif mutate == 'missing_csv': bad['stages'][0]['required'].remove('diagnostic-volume-cells.csv')
            else: bad['stages'][0]['required'].append('external_flow_solved.sim')
            with self.subTest(mutate=mutate): self.assertFalse(valid_readback_environment_artifacts(bad))

    def test_legacy_property_contract(self):
        self.assertTrue(valid_readback_environment_artifacts({'env': {'PROPERTY_NEARWALL_READBACK_ONLY': '1'},
            'stages': [{'required': ['property_nearwall_readback.json', 'nearwall-face-values.csv']}]}))
