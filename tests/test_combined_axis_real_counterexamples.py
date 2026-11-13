"""Real-evidence negatives; unavailable origin certificates are explicitly skipped."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'test-evidence/resource-contract-20261009/generalization-plan'

class CombinedAxisRealCounterexamples(unittest.TestCase):
    def test_real_source_pairing_and_failed_stage_refusals(self):
        spec=FOLDER/'case_spec-final.json'; contract=FOLDER/'source-contract-real.json'
        if not spec.is_file() or not contract.is_file():
            self.skipTest('Real origin final Spec/source contract not yet present')
        module_spec=importlib.util.spec_from_file_location('combined_axis_counterexamples',FOLDER/'run_paired_counterexamples.py')
        helper=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory(prefix='paired-test-',dir=str(FOLDER)) as folder:
            result=helper.run(spec,contract,Path(folder)/'result.json')
        self.assertTrue(result['passed']);self.assertTrue(result['original_evidence_unchanged'])
        self.assertFalse(result['star_started'])
        self.assertEqual({c['name'] for c in result['checks']},{'wrong-origin','wrong-selected-part','wrong-solid-role','old-source-reused','failed-stage-audit','failed-stage-prepare'})
        self.assertTrue(all(c['rejected'] for c in result['checks']))

if __name__=='__main__': unittest.main()
