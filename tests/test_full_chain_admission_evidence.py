"""Real PAT-18 admission failure counterexamples; these tests never submit a job."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'test-evidence/product-acceptance/deployed-20261018/full-chain'
sys.path.insert(0, str(ROOT / 'scripts'))
from backend import load_review_record


class FullChainAdmissionEvidence(unittest.TestCase):
    def test_actual_gateway_refusal_has_no_remote_job_or_star_evidence(self):
        response = json.loads((RAW / 'SUBMIT.json').read_text())
        self.assertTrue(response['isError'])
        self.assertIn('UNPUBLISHED_CODE', response['content'][0]['text'])
        slot = json.loads((RAW / 'SLOT-AFTER.json').read_text())
        for field in ('run_exists', 'job_exists', 'scheduled_task_exists'):
            self.assertFalse(slot[field])
        self.assertEqual((0, 0), (slot['star_count'], slot['running_managed_tasks']))
        transfer = json.loads((RAW / 'TRANSFER-VERIFY.json').read_text())
        self.assertEqual((1, 0, 0, 0), (transfer['submission_attempts'], transfer['star_sessions'],
                                        transfer['automatic_retries'], transfer['files_transferred']))
        self.assertFalse(transfer['all_transferred_hashes_match'])
        for artifact in transfer['artifacts']:
            self.assertEqual('NOT_PRODUCED', artifact['status'])
            self.assertIsNone(artifact['runner_sha256'])
            self.assertIsNone(artifact['remote_sha256'])
            self.assertIsNone(artifact['desktop_sha256'])

    def test_actual_materialized_trial_review_cannot_authorize_formal_runner(self):
        request = json.loads((RAW / 'REQUEST.json').read_text())
        review = RAW / 'pipeline-review.json'
        before = hashlib.sha256(review.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'pipeline_runner_sha256'):
            load_review_record(str(review), 'static-external-flow-2502', 'audit-body',
                               request['case_ref'], ['run_pipeline.ps1', 'run_job.ps1'],
                               ['AuditStaticBodyGeometry.java'])
        self.assertEqual(before, hashlib.sha256(review.read_bytes()).hexdigest())

    def test_real_pat18_retains_success_and_all_required_artifacts(self):
        definition = json.loads((ROOT / 'test-evidence/product-acceptance/PAT-DEPLOYED-20261018.json').read_text())
        case = next(item for item in definition['cases'] if item['id'] == 'PAT-18-deployed-full-chain')
        self.assertEqual(('SUCCESS', 'full_chain', 'deployed_worker_execution', 'v1', True),
                         (case['expected'], case['test_kind'], case['verification_basis'],
                          case['release_channel'], case['required_for_release']))
        self.assertEqual(['external_flow_validation.json', 'external_flow_mesh_quality.json',
                          'external_flow_solved.sim'], case['required_artifacts'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
