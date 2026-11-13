"""R22 MCP boundary counterexamples; no STAR or SSH in unit tests."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import registered_workflow_mcp as api
import run_registered_workflow as adapter


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'test-evidence/run'
        self.evidence.mkdir(parents=True)
        self.workflow = 'registered-fixture'
        self.run_id = 'request-test-001'
        self.source = self.root / 'user-name.sim'
        self.source.write_bytes(b'original registered geometry')
        self.spec = self.root / 'spec.json'
        self.spec.write_text('{}')
        (self.root / 'present.py').write_text('# presentation')
        (self.root / 'adapter.py').write_text('# adapter')
        pipeline = {'stages': [{'timeout_seconds': 600}], 'input_hashes': {
            'source_input.sim': api.digest(self.source), 'case_spec.json': api.digest(self.spec)}}
        self.write('pipeline.json', pipeline)
        self.manifest = {'id': self.workflow, 'mechanism': 'restricted_trial',
            'title': 'Fixture', 'scope': 'exact bytes only',
            'input_identity_sha256': api.digest(self.source), 'case_spec_sha256': api.digest(self.spec),
            'capability_levels': {'general_operation_acceptance': False, 'engineering_validated': False},
            'paired_counterexample_scope': 'fixture only',
            'pipeline': 'pipeline.json', 'star_seconds_cap': 600, 'poll_timeout_seconds': 900,
            'adapter_files': ['adapter.py', 'present.py', 'pipeline.json'],
            'presentation_entry': 'present.py', 'mcp_evidence': 'test-evidence/run'}
        self.manifest_path = self.root / 'registered-workflows' / self.workflow / 'manifest.json'
        self.write(str(self.manifest_path.relative_to(self.root)), self.manifest)
        self.write('progress.json', {'budget': {'per_case': {'original-geometry': {
            'remaining_authorized_star_seconds': 1923.08}}}})
        self.sign()
        self.patches = [patch.object(api, 'ROOT', self.root), patch.object(adapter, 'ROOT', self.root)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def sign(self):
        self.review = {'approved': True, 'independent': True,
            'registered_request_ids': [self.run_id],
            'registration_hashes': {p: api.digest(self.root / p) for p in
                self.manifest['adapter_files'] + [str(self.manifest_path.relative_to(self.root))]}}
        self.write('test-evidence/run/pipeline-review.json', self.review)

    def submit(self, request=None):
        return api.submit_registered_workflow(self.workflow, str(self.source), str(self.spec),
                                               request or self.run_id)

    def test_submit_returns_without_solve_wait_and_replays_once(self):
        with patch.object(api, 'invoke', return_value='{"trial_id":"request-test-001"}') as call:
            first = self.submit()
            second = self.submit()
        self.assertEqual(first['state'], 'SUBMITTED')
        self.assertTrue(second['idempotent_replay'])
        self.assertEqual(call.call_count, 1)
        self.assertIn('start', call.call_args.args[0])
        self.assertNotIn('--timeout', call.call_args.args[0])
        self.assertEqual(self.source.read_bytes(), b'original registered geometry')

    def test_interrupted_submission_cannot_repeat_star(self):
        with patch.object(api, 'invoke', side_effect=ValueError('network uncertain')) as call:
            with self.assertRaises(ValueError):
                self.submit()
            replay = self.submit()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(replay['state'], 'SUBMISSION_UNCERTAIN_OR_REFUSED')

    def test_different_input_refused_before_start(self):
        self.source.write_bytes(b'different geometry')
        with patch.object(api, 'invoke') as call, self.assertRaises(ValueError):
            self.submit()
        call.assert_not_called()

    def test_unallocated_run_refused_and_insufficient_budget_only_warns(self):
        """2026-10-15：本条原先要求台账不足时**拒绝提交**。AGENTS.md §6 的用户裁决 A（2026-10-14）
        已把 STAR 秒数台账改为"只记账、不拦提交：不足时写 budget_warning 并照常放行，MCP 准入不得据此拒绝"。
        拒绝的那半边已作废；**审查分配**那一半（未在 registered_request_ids 里）仍然必须拒绝。
        判据没有被删除，只是改成裁决后的形状：分配仍拒绝；台账不足必须提交并留下 budget_warning。
        """
        with patch.object(api, 'invoke', return_value='{"trial_id":"request-test-001"}') as call:
            with self.assertRaisesRegex(ValueError, 'compute allocation'):
                self.submit('unapproved-new-id')
            call.assert_not_called()
            self.write('progress.json', {'budget': {'per_case': {'original-geometry': {
                'remaining_authorized_star_seconds': 599.99}}}})
            handle = self.submit()
        self.assertEqual(1, call.call_count)
        self.assertEqual('SUBMITTED', handle['state'])
        self.assertIn('original-geometry STAR ledger below the registered run cap',
                      handle['budget_warning'])
        recorded = json.loads((self.evidence / 'mcp-handles' / (self.run_id + '.json')).read_text())
        self.assertEqual(handle['budget_warning'], recorded['budget_warning'])

    def test_pipeline_and_adapter_mutations_refused(self):
        for relative in ('adapter.py', 'pipeline.json'):
            original = (self.root / relative).read_bytes()
            (self.root / relative).write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'bytes changed'):
                api.checked_registration(self.workflow)
            (self.root / relative).write_bytes(original)

    def test_invalid_paths_and_unknown_handles_refused(self):
        for value in ('../escape', '/tmp/x', '..'):
            with self.assertRaises(ValueError):
                api.registration(value)
            with self.assertRaises(ValueError):
                api.lookup(value)
        with self.assertRaises(ValueError):
            api.lookup('unknown-run-001')

    def test_fetch_never_waits_for_running_star(self):
        with patch.object(api, 'invoke', return_value='{}'):
            self.submit()
        self.write('test-evidence/run/' + self.run_id + '-result.json', {'state': 'RUNNING'})
        with patch.object(api.subprocess, 'run') as call, self.assertRaisesRegex(ValueError, 'terminal'):
            api.fetch_registered_workflow_results(self.run_id)
        call.assert_not_called()

    def test_numeric_failure_remains_failure(self):
        with patch.object(api, 'invoke', return_value='{}'):
            self.submit()
        self.write('test-evidence/run/' + self.run_id + '-result.json',
                   {'state': 'GATE_FAILED', 'gate_passed': False})
        with patch.object(api.subprocess, 'run') as call:
            def refreshed(*args, **kwargs):
                self.write('test-evidence/run/' + self.run_id + '-result.json',
                           {'state': 'GATE_FAILED', 'gate_passed': False})
                return call.return_value
            call.side_effect = refreshed
            call.return_value.returncode = 1
            call.return_value.stdout = ''
            call.return_value.stderr = 'numeric gate failed'
            result = api.get_registered_workflow_run(self.run_id)
        self.assertEqual(result['state'], 'GATE_FAILED')
        self.assertFalse(result['engineering_verified'])
        self.assertTrue(result['terminal'])

    def test_failed_poll_cannot_reuse_old_success(self):
        with patch.object(api, 'invoke', return_value='{}'):
            self.submit()
        self.write('test-evidence/run/' + self.run_id + '-result.json',
                   {'state': 'AIR3B_PIPELINE_COMPLETE', 'gate_passed': True})
        with patch.object(api.subprocess, 'run') as call:
            call.return_value.returncode = 1
            call.return_value.stdout = ''
            call.return_value.stderr = 'connection lost'
            with self.assertRaisesRegex(ValueError, 'fresh state'):
                api.get_registered_workflow_run(self.run_id)

    def test_legacy_server_signatures_unchanged(self):
        old = ast.parse((ROOT / 'test-evidence/original-geometry/r21/run/execution-source/scripts/backend.py').read_text())
        # Public server functions from the prior revision stay additive and preserve argument lists.
        current = ast.parse((ROOT / 'scripts/server.py').read_text())
        funcs = {n.name: n for n in current.body if isinstance(n, ast.FunctionDef)}
        for name, args in {'submit_run': ['case_ref','request_id','steps','timeout_seconds','case_spec',
            'inspect_job_id','review_ref','operations','parameters'], 'get_run': ['run_id','case'],
            'fetch_run_results': ['run_id','include_sim'], 'list_workflows': []}.items():
            self.assertEqual([a.arg for a in funcs[name].args.args], args)
        self.assertTrue(old.body)


if __name__ == '__main__':
    unittest.main()
