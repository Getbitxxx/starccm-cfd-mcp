import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resource_contract import make_contract, validate_contract, validate_pipeline_resources
from stage_plan import plan_stage, plan_pipeline, StagePlanError

class ResourceContract(unittest.TestCase):
    def pipeline(self):
        c = make_contract({})
        return {'resource_contract': c, 'logical_cores': 64, 'memory_gb': 64,
                'task_budget_seconds': 172800, 'input_hashes': {'input.sim': 'a'},
                'stages': [{'name': 'solve', 'macros': ['Solve.java'], 'input': 'input.sim',
                            'output': None, 'timeout_seconds': 172740,
                            'timing_contract': dict(load_seconds=300, macro_seconds=160000,
                              chunk_overrun_seconds=300, save_seconds=300, exit_seconds=300)}]}
    def test_formal_review_pins_helper_bytes_and_rejects_mutation(self):
        import tempfile
        import hashlib
        from unittest.mock import patch
        import backend
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'scripts').mkdir()
            helper = root / 'scripts/resource_contract.py'; helper.write_text('first')
            with patch.object(backend, 'PLUGIN', root), patch.object(backend, 'sha256', lambda p: hashlib.sha256(p.read_bytes()).hexdigest()):
                review = {'resource_contract_sha256': backend.sha256(helper)}
                backend.check_resource_review(review)
                helper.write_text('changed')
                with self.assertRaises(ValueError): backend.check_resource_review(review)
                with self.assertRaises(ValueError): backend.check_resource_review({})

    def test_old_spec_core_limit_is_not_a_new_resource_allocation(self):
        self.assertEqual(make_contract({'logical_cores': 8})['logical_cores'], 64)
        self.assertEqual(make_contract({'logical_cores': 8}, {'logical_cores': 8})['logical_cores'], 8)

    def test_new_defaults_and_long_timing(self):
        p = self.pipeline()
        self.assertEqual(validate_pipeline_resources(p, 172740)['outer_timeout_seconds'], 172800)
        plan_pipeline(p)
    def test_readback_new_source_contract_keeps_legacy_boundaries(self):
        from run_registered_workflow_v2 import readback_deadline
        self.assertEqual(readback_deadline({'resource_contract': make_contract({}), 'readback_deadline_seconds': 1800}), 1800)
        self.assertEqual(readback_deadline({}), 60)
        with self.assertRaises(ValueError): readback_deadline({'readback_deadline_seconds': 1800})
        with self.assertRaises(ValueError): readback_deadline({'resource_contract': make_contract({}), 'readback_deadline_seconds': 172800})

    def test_contract_changes_cannot_diverge_from_pipeline(self):
        p = self.pipeline(); p['logical_cores'] = 8
        with self.assertRaises(ValueError): validate_pipeline_resources(p)
    def test_reserve_and_nonfinite_rejected(self):
        for k,v in [('memory_reserve_gb', 15), ('memory_gb', float('nan')), ('logical_cores', True), ('outer_timeout_seconds', True)]:
            c = make_contract({}); c[k] = v
            with self.assertRaises(ValueError): validate_contract(c)
    def test_time_relations_are_observations_not_admission_gates(self):
        for key, value in [('task_budget_seconds', 172799), ('task_budget_seconds', 172801)]:
            p=self.pipeline(); p[key]=value
            validate_pipeline_resources(p)
        p=self.pipeline()
        validate_pipeline_resources(p, 172700)
    def test_historical_without_contract_has_no_timing_cap(self):
        p=self.pipeline(); p.pop('resource_contract')
        self.assertIsNone(validate_pipeline_resources(p))
        plan_pipeline(p)
    def test_timing_sum_does_not_reject(self):
        p=self.pipeline(); p['stages'][0]['timing_contract']['save_seconds']=172800
        plan_pipeline(p)

if __name__ == '__main__': unittest.main()
