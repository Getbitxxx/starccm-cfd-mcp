# -*- coding: utf-8 -*-
"""②R12: the formal entry chains submit -> poll -> retrieve+verify+report, and refuses to skip the plan.

The gap this closes: the FORMAL path had no entry command, so "a formal run produces the report" was a
claim rather than something a command did. These tests drive `scripts/formal_run_entry.py` with a stubbed
Backend (no SSH, no STAR) and pin the properties that matter:

  1. the CaseSpec is mandatory and is passed to submit_run unchanged;
  2. the plan is passed through and submit_run's refusal propagates (a non-executable plan stops the run);
  3. a failed run still reaches the report step - the report is not a reward for success;
  4. --strict turns a non-REPORT_OK verdict into exit code 2, and without it a failed run is exit 0;
  5. the report step is `report_run`, i.e. the same code path the MCP tool exposes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import formal_run_entry as entry                                          # noqa: E402


def spec(case_ref: str) -> dict:
    return {'schema_version': 1, 'scenario': 'external_static', 'case_ref': case_ref,
            'objective': 'formal entry test', 'model_length_unit': 'm', 'medium': 'air',
            'geometry': {'body_parts': ['body']},
            'domain': {'outer_bounds_m': [-10, -10, -10, 10, 10, 10]},
            'flow': {'velocity_m_s': [10, 0, 0], 'pressure_pa': 101325, 'temperature_k': 293.15}}


class StubBackend:
    """Records the calls instead of talking to Windows.

    `calls` holds the three MCP-layer methods the entry chains. The runtime precondition (deployed? deploy) and
    the download destination are recorded separately in `precondition`, because ②R13 added them to the entry
    after the acceptance run measured `submit_run` refusing an undeployed runtime and the report step failing
    to write outside the project.
    """

    def __init__(self, run_state: str = 'EXECUTION_COMPLETED', verdict: str = 'REPORT_OK',
                 submit_error: Exception | None = None, deployed: bool = True):
        self.calls = []
        self.precondition = []
        self.run_state = run_state
        self.verdict = verdict
        self.submit_error = submit_error
        self.deployed = deployed
        self.remote_root = 'C:\\nowhere'
        self.config = {}

    def runtime_digest(self):
        return 'a' * 64

    def ps(self, script, *a, **k):
        self.precondition.append(('ps', script[:60]))
        return {'deployed': self.deployed}

    def deploy(self):
        self.precondition.append(('deploy', None))
        self.deployed = True
        return {'deployed': True, 'runtime_digest': self.runtime_digest()}

    def submit_run(self, case_ref, request_id, steps=None, timeout_seconds=172800, case_spec=None,
                   inspect_job_id=None, review_ref=None, operations=None, parameters=None):
        self.calls.append(('submit_run', {'case_ref': case_ref, 'request_id': request_id,
                                          'steps': steps, 'case_spec': case_spec,
                                          'inspect_job_id': inspect_job_id,
                                          'review_ref': review_ref, 'operations': operations,
                                          'parameters': parameters}))
        if self.submit_error is not None:
            raise self.submit_error
        return {'run_id': request_id, 'state': 'PENDING', 'reused': False}

    def get_run(self, run_id):
        self.calls.append(('get_run', {'run_id': run_id}))
        return {'status': {'state': self.run_state}, 'artifacts': [], 'plan': {}}

    def report_run(self, run_id, evidence=None, case_label=None, include_sim=True, strict=False):
        self.calls.append(('report_run', {'run_id': run_id, 'evidence': evidence,
                                          'case_label': case_label, 'strict': strict}))
        return {'run_id': run_id, 'report': {'verdict': self.verdict, 'reasons': []},
                'conclusions': {'process': {}, 'engineering': {'claimed': False}, 'delivery': {}}}


class TheEntryGuaranteesItsOwnRuntimePrecondition(unittest.TestCase):
    """②R13 acceptance-run findings, pinned behaviourally rather than by reading the source."""

    def run_entry(self, backend, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / 'case_spec.json'
            spec_path.write_text(json.dumps(spec('library/' + 'a' * 64 + '.sim')), encoding='utf-8')
            argv = ['formal_run_entry.py', '--case-ref', 'library/' + 'a' * 64 + '.sim',
                    '--request-id', 'formal-entry-1', '--case-spec', str(spec_path),
                    '--operations', 'build_static_body', '--operations', 'audit_static_body',
                    '--review-ref', 'review/x.json', '--evidence', tmp]
            argv.extend(extra)
            old_argv = sys.argv
            sys.argv = argv
            try:
                with patch('backend.Backend', return_value=backend):
                    try:
                        code = entry.main()
                    except SystemExit as exc:
                        code = exc.code
            finally:
                sys.argv = old_argv
            return code, backend, tmp

    def test_an_undeployed_runtime_is_deployed_before_the_submission(self):
        stub = StubBackend(deployed=False)
        self.run_entry(stub)
        names = [name for name, _ in stub.precondition]
        self.assertIn('deploy', names, 'an undeployed runtime must be deployed, not assumed')
        self.assertEqual('ps', names[0])
        # and the submission happens only afterwards: the plan is built after the deploy block
        self.assertIn('submit_run', [name for name, _ in stub.calls])

    def test_an_already_deployed_runtime_is_not_re_deployed(self):
        stub = StubBackend(deployed=True)
        self.run_entry(stub)
        self.assertNotIn('deploy', [name for name, _ in stub.precondition],
                         'a deployed runtime must be left alone (deploy copies bytes and is not free)')

    def test_the_download_destination_defaults_into_the_evidence_directory(self):
        stub = StubBackend()
        _code, stub, tmp = self.run_entry(stub)
        self.assertEqual(str(Path(tmp).resolve()), stub.config.get('local_results'),
                         'the run directory must land beside the report, not wherever the connection file points')

    def test_an_explicit_results_dir_wins(self):
        stub = StubBackend()
        with tempfile.TemporaryDirectory() as other:
            self.run_entry(stub, extra=['--results-dir', other])
        self.assertNotEqual('C:\\nowhere', stub.config.get('local_results'))



class TheFormalEntryChainsTheFullPlan(unittest.TestCase):
    def run_entry(self, backend, extra=(), run_state='EXECUTION_COMPLETED', verdict='REPORT_OK'):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / 'case_spec.json'
            spec_path.write_text(json.dumps(spec('library/' + 'a' * 64 + '.sim')), encoding='utf-8')
            argv = ['formal_run_entry.py', '--case-ref', 'library/' + 'a' * 64 + '.sim',
                    '--request-id', 'formal-entry-1', '--case-spec', str(spec_path),
                    '--operations', 'build_static_body', '--operations', 'audit_static_body',
                    '--review-ref', 'review/x.json', '--evidence', str(Path(tmp) / 'evidence')]
            argv += list(extra)
            old_argv = sys.argv
            sys.argv = argv
            try:
                with patch('backend.Backend', return_value=backend):
                    return entry.main(), backend
            finally:
                sys.argv = old_argv

    def test_the_casespec_and_the_plan_reach_submit_run_unchanged(self):
        code, backend = self.run_entry(StubBackend())
        self.assertEqual(0, code)
        kind, payload = backend.calls[0]
        self.assertEqual('submit_run', kind)
        self.assertEqual(['build_static_body', 'audit_static_body'], payload['operations'])
        self.assertEqual('library/' + 'a' * 64 + '.sim', payload['case_ref'])
        self.assertEqual('external_static', payload['case_spec']['scenario'],
                         'a full formal run may not omit the CaseSpec')
        self.assertEqual('review/x.json', payload['review_ref'])
        self.assertEqual(['submit_run', 'get_run', 'report_run'],
                         [name for name, _ in backend.calls],
                         'the report step must follow the run, through the same Backend the MCP tools use')

    def test_a_refused_plan_propagates_instead_of_being_worked_around(self):
        backend = StubBackend(submit_error=ValueError('CaseSpec is incomplete or the SELECTED plan is not '
                                                      'executable (plan_blocker: the selected plan does not '
                                                      'provide: mesh_study)'))
        with self.assertRaisesRegex(ValueError, 'SELECTED plan is not executable'):
            self.run_entry(backend)
        self.assertEqual(['submit_run'], [name for name, _ in backend.calls],
                         'nothing may be retrieved or reported when the plan was refused')

    def test_a_failed_run_still_reaches_the_report_step(self):
        code, backend = self.run_entry(StubBackend(run_state='WAITING_AGENT',
                                                   verdict='REPORT_RUN_FAILED'))
        self.assertEqual(0, code, 'a failed run is reported, not treated as a command error')
        self.assertEqual(['submit_run', 'get_run', 'report_run'], [name for name, _ in backend.calls])

    def test_strict_turns_a_failed_verdict_into_exit_code_two(self):
        code, _ = self.run_entry(StubBackend(run_state='WAITING_AGENT', verdict='REPORT_RUN_FAILED'),
                                 extra=['--strict'])
        self.assertEqual(2, code)

    def test_a_submit_only_run_does_not_poll_or_report(self):
        code, backend = self.run_entry(StubBackend(), extra=['--submit-only'])
        self.assertEqual(0, code)
        self.assertEqual(['submit_run'], [name for name, _ in backend.calls])

    def test_a_missing_plan_is_refused_before_any_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / 'case_spec.json'
            spec_path.write_text(json.dumps(spec('library/' + 'a' * 64 + '.sim')), encoding='utf-8')
            old_argv = sys.argv
            sys.argv = ['formal_run_entry.py', '--case-ref', 'library/' + 'a' * 64 + '.sim',
                        '--request-id', 'x', '--case-spec', str(spec_path),
                        '--review-ref', 'review/x.json', '--evidence', str(Path(tmp) / 'e')]
            try:
                with self.assertRaisesRegex(ValueError, 'needs the plan'):
                    entry.main()
            finally:
                sys.argv = old_argv

    def test_raw_steps_cannot_be_used_for_a_full_run(self):
        # A full CaseSpec run must name its plan as operations, otherwise the pre/post steps could never be
        # plan members and the gate would refuse anyway. Refuse early, with the reason.
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / 'case_spec.json'
            spec_path.write_text(json.dumps(spec('library/' + 'a' * 64 + '.sim')), encoding='utf-8')
            steps_path = Path(tmp) / 'steps.json'
            steps_path.write_text(json.dumps([{'key': 'audit_static_body',
                                               'workflow_id': 'static-external-flow-2502',
                                               'stage': 'audit-body'}]), encoding='utf-8')
            old_argv = sys.argv
            sys.argv = ['formal_run_entry.py', '--case-ref', 'library/' + 'a' * 64 + '.sim',
                        '--request-id', 'x', '--case-spec', str(spec_path), '--steps', str(steps_path),
                        '--review-ref', 'review/x.json', '--evidence', str(Path(tmp) / 'e')]
            try:
                with self.assertRaisesRegex(ValueError, 'must name its plan with --operations'):
                    entry.main()
            finally:
                sys.argv = old_argv

    def test_an_unfinished_run_does_not_produce_a_report(self):
        code, backend = self.run_entry(StubBackend(run_state='RUNNING'), extra=['--wait-seconds', '0'])
        self.assertEqual(1, code)
        self.assertEqual(['submit_run', 'get_run'], [name for name, _ in backend.calls],
                         'no report may be produced from a run that has not finished')


if __name__ == '__main__':
    unittest.main(verbosity=2)
