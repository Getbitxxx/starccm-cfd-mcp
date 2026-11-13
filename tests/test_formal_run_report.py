# -*- coding: utf-8 -*-
"""②R12: the formal run's report step, its hash verification, and its three separate conclusions.

The gap this closes: `report` was wired into the restricted-trial entry only, so a formal `submit_run`
graph could not produce the Chinese report at all, while the plan gate counted the offline tool for the
formal mechanism on the strength of an `applies_to` declaration that no code checked. These tests run the
adapter against a REAL-SHAPED formal run directory (the layout `worker/run_pipeline.ps1` +
`worker/run_job.ps1` write: `state.json`, `stages/<key>/stage-<stage>.json`, `checkpoint.json`) and call
the REAL reporter as a subprocess. No STAR, no SSH, no network.

Covered:
  1. a completed run -> every artifact verified against the runner's own sha256, the completion state
     translated once and re-checkably, and the report verdict REPORT_OK;
  2. a failed run -> an honest report, no translation, the failure named from the records;
  3. a tampered artifact -> HASH_MISMATCH, download_succeeded=false, checkpoint_delivered=false and
     REPORT_ARTIFACT_NOT_DELIVERED (a delivery failure, not a STAR failure);
  4. an artifact the runner never hashed -> NO_RECORDED_HASH, never "verified";
  5. the adapter does not re-implement the reporter or the citation rule: the shared scripts are called,
     and the engineering line is never claimed by execution.
"""
from __future__ import annotations

import hashlib
import shutil
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import formal_run_report as adapter                                        # noqa: E402
from run_record_gate import CITABLE, citation_status, scan_run              # noqa: E402

SIM_BYTES = b'STAR-CCM+ simulation placeholder bytes for the adapter test\n'
RUN_ID = 'formal-run-1'


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_stage(job: Path, name: str, *, state: str = 'ok', artifacts: list[str] | None = None,
                error: str | None = None) -> None:
    """One stage job directory in the shape worker/run_job.ps1 writes (one stage-*.json per directory)."""
    payload = {'schema_version': 1, 'stage': name, 'state': state, 'seconds': 1.5,
               'job_dir': str(job), 'macro_outputs': [], 'artifacts': artifacts or [],
               'macros': [{'index': 0, 'macro': 'SomeMacro.java', 'ran': state == 'ok',
                           'status': 'ok' if state == 'ok' else 'failed',
                           'exit_code': 0 if state == 'ok' else 1,
                           'input': 'input.sim', 'output': None, 'log': 'star.log', 'error': error}]}
    if error:
        payload['error'] = error
    write_json(job / ('stage-%s.json' % name), payload)


def build_run(root: Path, *, run_state: str = 'EXECUTION_COMPLETED', stage_state: str = 'ok',
              tamper: bool = False, extra_unhashed_sim: bool = False) -> tuple[Path, dict]:
    """A three-stage formal run directory plus the hashes its pipeline recorded."""
    run_dir = root / 'runs' / RUN_ID
    audit = run_dir / 'stages' / 'audit_static_body'
    solve = run_dir / 'stages' / 'run_external_flow_solve'
    validation = run_dir / 'stages' / 'audit_external_flow_convergence'
    for job in (audit, solve, validation):
        job.mkdir(parents=True)
    (audit / 'input.sim').write_bytes(b'geometry-only input\n')
    solved = solve / 'external_flow_solved.sim'
    solved.write_bytes(SIM_BYTES + (b'tampered\n' if tamper else b''))

    failed = stage_state != 'ok'
    write_stage(audit, 'audit-body', state=stage_state,
                artifacts=['static_body_audit.json', 'input.sim'],
                error='the audit macro reported an open body' if failed else None)
    # NOTE the deliberate asymmetry in the failed case: the MEASUREMENT record still claims `status: ok`.
    # That is the round-6/R2 defect shape (a success claim inside a failed stage), and it is how this
    # fixture proves the shared gate refuses a success claim at STAGE granularity rather than trusting it.
    write_json(audit / 'static_body_audit.json',
               {'status': 'ok' if not failed else 'ok', 'is_closed': not failed, 'is_manifold': True})
    write_stage(solve, 'solve', artifacts=['external_flow_solve.json', 'external_flow_solved.sim'])
    write_json(solve / 'external_flow_solve.json',
               {'status': 'ok', 'end_iteration': 600, 'measured_drag_x_n_final': 0.4467029158036979})
    write_stage(validation, 'validation', artifacts=['external_flow_validation.json'])
    write_json(validation / 'external_flow_validation.json',
               {'status': 'ok', 'validation_verdict': 'NOT_VALIDATED_NO_REFERENCE', 'residuals_met': True,
                'target_quantities_steady': True, 'read_only_verified': True,
                'reference_status': 'NOT_ESTABLISHED'})
    if extra_unhashed_sim:
        (solve / 'unrecorded_output.sim').write_bytes(b'a .sim the runner never hashed\n')

    recorded = {'output_sha256': digest(SIM_BYTES),
                'input_sha256': digest(b'geometry-only input\n')}
    # The pipeline writes the input hash into the FIRST stage's checkpoint and each stage's state file.
    write_json(audit / 'checkpoint.json',
               {'stage_key': 'audit_static_body', 'input_sha256': recorded['input_sha256']})
    write_json(audit / 'state.json', {'state': 'SUCCEEDED' if not failed else 'FAILED',
                                     'source_sha256': recorded['input_sha256']})
    write_json(solve / 'checkpoint.json',
               {'stage_key': 'run_external_flow_solve', 'output_path': str(solved),
                'output_sha256': recorded['output_sha256']})
    write_json(solve / 'state.json', {'state': 'SUCCEEDED'})
    write_json(validation / 'state.json', {'state': 'SUCCEEDED'})
    write_json(run_dir / 'state.json',
               {'state': run_state, 'run_id': RUN_ID,
                'completed': [] if failed else ['audit_static_body', 'run_external_flow_solve',
                                                'audit_external_flow_convergence'],
                'final_sim': str(solved), 'final_sha256': recorded['output_sha256'],
                'reason': 'All installed stages executed' if not failed else None})
    return run_dir, recorded


class ACompletedFormalRunProducesAVerifiedReport(unittest.TestCase):
    def test_the_evidence_layout_the_shared_reporter_reads_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, recorded = build_run(root)
            evidence = root / 'evidence'
            adapter.build_evidence(RUN_ID, run_dir, evidence)
            for name in (RUN_ID + '-config.json', RUN_ID + '-result.json', RUN_ID + '-checkpoints.json'):
                self.assertTrue((evidence / name).is_file(), name)
            trial = evidence / RUN_ID
            for name in ('stage-audit-body.json', 'stage-solve.json', 'stage-validation.json',
                         'static_body_audit.json', 'external_flow_solve.json',
                         'external_flow_validation.json', 'external_flow_solved.sim', 'state.json'):
                self.assertTrue((trial / name).is_file(), name)
            solve = json.loads((trial / 'stage-solve.json').read_text(encoding='utf-8'))
            self.assertEqual('ok', solve['state'])
            self.assertEqual(recorded['output_sha256'], solve['output_sha256'])
            self.assertIn('adapted_from', solve)
            config = json.loads((evidence / (RUN_ID + '-config.json')).read_text(encoding='utf-8'))
            self.assertEqual([{'name': 'audit-body',
                               'required': ['static_body_audit.json', 'input.sim']},
                              {'name': 'solve',
                               'required': ['external_flow_solve.json', 'external_flow_solved.sim']},
                              {'name': 'validation', 'required': ['external_flow_validation.json']}],
                             config['stages'])

    def test_every_artifact_is_verified_against_the_hash_the_runner_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, recorded = build_run(root)
            delivery = adapter.verify_artifacts(run_dir, adapter.stage_records(run_dir))
            self.assertTrue(delivery['download_succeeded'])
            self.assertTrue(delivery['checkpoint_delivered'])
            statuses = {item['name']: item['status'] for item in delivery['artifacts']}
            self.assertEqual('VERIFIED', statuses['external_flow_solved.sim'])
            self.assertEqual('VERIFIED', statuses['input.sim'])
            self.assertEqual(recorded['output_sha256'], delivery['final_sim']['sha256'])
            self.assertEqual([], delivery['failures'])

    def test_the_completion_state_is_translated_once_and_stays_re_checkable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            result = adapter.build_evidence(RUN_ID, run_dir, root / 'evidence')['result']
            self.assertEqual('AIR3B_PIPELINE_COMPLETE', result['state'])
            self.assertEqual('EXECUTION_COMPLETED', result['formal_state'])
            self.assertEqual('AIR3B_PIPELINE_COMPLETE', result['translated_state'])
            self.assertIn('EXECUTION_COMPLETED -> AIR3B_PIPELINE_COMPLETE', result['translation_rule'])
            self.assertIsNone(result['gate_passed'])
            self.assertEqual('NOT_APPLICABLE_ON_THE_FORMAL_PATH', result['numeric_gate'])

    def test_the_shared_reporter_produces_report_ok_and_three_separate_conclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            outcome = adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir,
                                         case_label='formal adapter test')
            self.assertEqual('REPORT_OK', outcome['report']['verdict'],
                             outcome['report'].get('reasons'))
            process = outcome['conclusions']['process']
            engineering = outcome['conclusions']['engineering']
            delivery = outcome['conclusions']['delivery']
            self.assertTrue(process['all_stage_records_ok'])
            self.assertEqual({'audit-body': 'ok', 'solve': 'ok', 'validation': 'ok'},
                             process['stage_records'])
            self.assertEqual('REPORT_OK', process['report_verdict'])
            self.assertTrue(delivery['download_succeeded'])
            self.assertTrue(delivery['checkpoint_delivered'])
            self.assertIn('external_flow_solved.sim', delivery['verified'])
            # Engineering is never claimed by running: the validation record's own words are surfaced.
            self.assertFalse(engineering['claimed'])
            self.assertEqual('external_flow_validation.json', engineering['record'])
            self.assertEqual('NOT_VALIDATED_NO_REFERENCE',
                             engineering['verdict_fields']['validation_verdict'])
            self.assertEqual('NOT_ESTABLISHED', engineering['verdict_fields']['reference_status'])

    def test_the_report_files_and_the_citation_index_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            outcome = adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir)
            evidence = root / 'evidence'
            for name in (RUN_ID + '-report.md', RUN_ID + '-report.json',
                         RUN_ID + '-evidence-index.json', RUN_ID + '-report-sha256.txt'):
                self.assertTrue((evidence / name).is_file(), name)
            index = json.loads((evidence / (RUN_ID + '-evidence-index.json')).read_text(encoding='utf-8'))
            self.assertEqual(CITABLE, index['static_body_audit.json']['citation_state'])
            self.assertEqual(CITABLE, index['external_flow_validation.json']['citation_state'])
            self.assertEqual('REPORT_OK', outcome['report']['verdict'])


class AFailedFormalRunStillGetsAnHonestReport(unittest.TestCase):
    def test_the_run_state_is_passed_through_and_the_failure_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root, run_state='WAITING_AGENT', stage_state='failed')
            outcome = adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir)
            result = json.loads((root / 'evidence' / (RUN_ID + '-result.json')).read_text(encoding='utf-8'))
            self.assertEqual('WAITING_AGENT', result['state'])
            self.assertIsNone(result['translated_state'],
                              'a failed stage must never be translated into a completion state')
            self.assertEqual('REPORT_RUN_FAILED', outcome['report']['verdict'])
            self.assertFalse(outcome['conclusions']['process']['all_stage_records_ok'])
            self.assertEqual('failed', outcome['conclusions']['process']['stage_records']['audit-body'])
            failed_record = root / 'evidence' / RUN_ID / 'static_body_audit.json'
            # The record itself claims ok, and it is still refused: the STAGE that produced it failed.
            self.assertEqual('ok', json.loads(failed_record.read_text(encoding='utf-8'))['status'])
            verdict = citation_status(failed_record)
            self.assertEqual('NOT_CITABLE_FAILED_RUN', verdict['state'])
            self.assertEqual('stage', verdict.get('decided_by'))

    def test_a_completion_state_with_an_unverified_stage_is_not_translated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root, run_state='EXECUTION_COMPLETED', stage_state='failed')
            built = adapter.build_evidence(RUN_ID, run_dir, root / 'evidence')
            self.assertIsNone(built['result']['translated_state'])
            self.assertIn('does not verify', built['result']['error'])
            self.assertEqual('REPORT_RUN_FAILED',
                             adapter.run_reporter(RUN_ID, root / 'evidence')['verdict'])


class ATamperedArtifactIsNeverReportedAsDelivered(unittest.TestCase):
    def test_a_hash_mismatch_is_a_delivery_failure_not_a_star_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root, tamper=True)
            outcome = adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir)
            delivery = outcome['conclusions']['delivery']
            self.assertFalse(delivery['download_succeeded'])
            self.assertFalse(delivery['checkpoint_delivered'])
            self.assertEqual(['external_flow_solved.sim'],
                             [item['name'] for item in delivery['failures']])
            self.assertEqual('HASH_MISMATCH', delivery['failures'][0]['status'])
            self.assertEqual('REPORT_ARTIFACT_NOT_DELIVERED', outcome['report']['verdict'],
                             outcome['report'].get('reasons'))
            self.assertEqual('AIR3B_PIPELINE_COMPLETE',
                             json.loads((root / 'evidence' / (RUN_ID + '-result.json')
                                         ).read_text(encoding='utf-8'))['state'],
                             'the process really completed; only the transfer failed')

    def test_strict_mode_fails_the_command_on_a_delivery_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root, tamper=True)
            argv = sys.argv
            sys.argv = ['formal_run_report.py', '--run-id', RUN_ID,
                        '--evidence', str(root / 'evidence'), '--run-dir', str(run_dir), '--strict']
            try:
                self.assertEqual(2, adapter.main())
            finally:
                sys.argv = argv


class APartiallyRetrievedRunIsNotACompletedRun(unittest.TestCase):
    """R12's review found `build_evidence` never compared the run's own `completed` list with what was
    retrieved, so three declared stages with one stage directory arrived as AIR3B_PIPELINE_COMPLETE /
    REPORT_OK. Reachable through the CLI and `Backend.report_run(run_dir=...)`."""

    def test_a_declared_stage_with_no_retrieved_directory_blocks_the_completion_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            # The runner declares three completed stages; only the solve directory actually arrived.
            state = json.loads((run_dir / 'state.json').read_text(encoding='utf-8'))
            state['completed'] = ['audit_static_body', 'run_external_flow_solve']
            (run_dir / 'state.json').write_text(json.dumps(state), encoding='utf-8')
            for job in ('audit_static_body', 'audit_external_flow_convergence'):
                shutil.rmtree(run_dir / 'stages' / job)
            built = adapter.build_evidence(RUN_ID, run_dir, root / 'evidence')
            self.assertIsNone(built['result']['translated_state'])
            self.assertIn('no stage directory was retrieved', built['result']['error'])
            self.assertIn('audit_static_body', built['result']['error'])
            self.white_box_not_ok(root)

    def white_box_not_ok(self, root: Path) -> None:
        self.assertEqual('REPORT_RUN_FAILED', adapter.run_reporter(RUN_ID, root / 'evidence')['verdict'])


class AStageRecordMustAgreeWithItsOwnMacros(unittest.TestCase):
    """A record that says `state: ok` while its `macros[]` say failed must not complete a run. Round 6's
    defect shape; `worker/run_job.ps1` only writes ok after every macro succeeded, so this fixture is
    hand-crafted on purpose - the adapter must not depend on the runner being correct."""

    def test_a_contradictory_stage_record_is_not_a_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            path = run_dir / 'stages' / 'run_external_flow_solve' / 'stage-solve.json'
            record = json.loads(path.read_text(encoding='utf-8'))
            record['macros'][0]['status'] = 'failed'
            record['macros'][0]['ran'] = True
            path.write_text(json.dumps(record), encoding='utf-8')
            built = adapter.build_evidence(RUN_ID, run_dir, root / 'evidence')
            self.assertIsNone(built['result']['translated_state'])
            self.assertIn('its own macros do not', built['result']['error'])
            self.assertEqual('REPORT_RUN_FAILED',
                             adapter.run_reporter(RUN_ID, root / 'evidence')['verdict'])

    def test_a_stage_record_with_no_macro_list_is_not_a_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            path = run_dir / 'stages' / 'run_external_flow_solve' / 'stage-solve.json'
            record = json.loads(path.read_text(encoding='utf-8'))
            record.pop('macros')
            path.write_text(json.dumps(record), encoding='utf-8')
            built = adapter.build_evidence(RUN_ID, run_dir, root / 'evidence')
            self.assertIsNone(built['result']['translated_state'])


class AnUnrecordedArtifactIsNotVerified(unittest.TestCase):
    def test_a_sim_with_no_runner_hash_is_reported_as_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root, extra_unhashed_sim=True)
            delivery = adapter.verify_artifacts(run_dir, adapter.stage_records(run_dir))
            unrecorded = {item['name'] for item in delivery['no_recorded_hash']}
            self.assertIn('stages/run_external_flow_solve/unrecorded_output.sim', unrecorded)
            self.assertTrue(delivery['download_succeeded'],
                            'an unhashed extra file must not be silently claimed as verified, nor fail '
                            'an otherwise verified delivery')

    def test_a_fetched_run_without_input_sim_is_still_a_delivered_run(self):
        # The real retrieval path (`Backend.fetch_run_results`) never downloads `input.sim`: the runner
        # copies it locally and `_get_run_status` excludes that name from the artifact list. Counting its
        # absence as a failed delivery would make EVERY real run look undelivered, while a fixture that
        # writes input.sim would hide the defect. The input is therefore reported as NOT_RETRIEVED.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            (run_dir / 'stages' / 'audit_static_body' / 'input.sim').unlink()
            delivery = adapter.verify_artifacts(run_dir, adapter.stage_records(run_dir))
            statuses = {item['name']: item['status'] for item in delivery['artifacts']}
            self.assertEqual('NOT_RETRIEVED', statuses['input.sim'])
            self.assertTrue(delivery['download_succeeded'],
                            'a non-retrieved INPUT is not a missing deliverable')
            self.assertTrue(delivery['checkpoint_delivered'])
            self.assertEqual([], delivery['failures'])
            self.assertNotIn('input.sim', {item['name'] for item in delivery['artifacts']
                                           if item['status'] == 'VERIFIED'})

    def test_a_missing_sim_output_is_still_a_failed_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            (run_dir / 'stages' / 'run_external_flow_solve' / 'external_flow_solved.sim').unlink()
            delivery = adapter.verify_artifacts(run_dir, adapter.stage_records(run_dir))
            self.assertFalse(delivery['download_succeeded'])
            self.assertFalse(delivery['checkpoint_delivered'])
            self.assertEqual(['external_flow_solved.sim'],
                             [item['name'] for item in delivery['failures']])
            self.assertEqual('MISSING', delivery['failures'][0]['status'])


class TheAdapterDoesNotReimplementTheSharedRules(unittest.TestCase):
    def test_the_reporter_and_the_gate_are_called_not_copied(self):
        source = (ROOT / 'scripts' / 'formal_run_report.py').read_text(encoding='utf-8')
        self.assertIn('subprocess.run', source)
        self.assertIn('generate_run_report.py', source)
        self.assertNotIn('from generate_run_report import', source,
                         'the reporter must stay the single implementation of the report')
        for reimplemented in ('def verdict_of', 'def citation_status', 'def claims_ok',
                              'def delivery_reasons_of'):
            self.assertNotIn(reimplemented, source)
        self.assertEqual(ROOT / 'scripts' / 'generate_run_report.py', adapter.REPORT_SCRIPT)

    def test_the_adapted_records_are_judged_by_the_shared_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir)
            report = scan_run(root / 'evidence' / RUN_ID)
            self.assertEqual([], report['violations'], report['violations'])

    def test_engineering_is_never_claimed_by_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, _ = build_run(root)
            outcome = adapter.report_run(RUN_ID, root / 'evidence', run_dir=run_dir)
            self.assertFalse(outcome['conclusions']['engineering']['claimed'])
            self.assertIsNot(True, outcome['report'].get('converged'))


class ThePerMacroOutputHashesAreRead(unittest.TestCase):
    """②R15: the runner records `output`/`output_sha256` per macro; the adapter must read them.

    Measured defect: `stage-region-physics-mesh.json` records the sha256 of `external_flow_region.sim` and
    `external_flow_physics.sim` (the outputs of two of its three macros), but `expected_hashes` only read
    `checkpoint.json` and the run's final state, so both files were reported as having NO recorded hash -
    the adapter failing to READ a record, reported in the shape of the runner failing to WRITE one.
    """

    def run_dir(self, root: Path, macro_records: list[dict], extra_files: dict | None = None) -> Path:
        run = root / 'runs' / 'r15-macro-1'
        job = run / 'stages' / 'run_external_flow_solve'
        job.mkdir(parents=True)
        import hashlib as _h
        files = {'external_flow_solved.sim': b'final bytes\n'}
        files.update(extra_files or {})
        digests = {}
        for name, payload in files.items():
            path = job / name
            path.write_bytes(payload)
            digests[name] = _h.sha256(payload).hexdigest()
        (job / 'stage-solve.json').write_text(json.dumps({
            'schema_version': 1, 'stage': 'solve', 'state': 'ok', 'seconds': 1.0,
            'artifacts': ['external_flow_solved.sim'], 'macros': macro_records,
            'input': 'input.sim', 'input_sha256': 'a' * 64,
            'output': 'external_flow_solved.sim', 'output_sha256': digests['external_flow_solved.sim']}),
            encoding='utf-8')
        (job / 'checkpoint.json').write_text(json.dumps(
            {'output_path': str(job / 'external_flow_solved.sim'),
             'output_sha256': digests['external_flow_solved.sim']}), encoding='utf-8')
        (run / 'state.json').write_text(json.dumps({
            'state': 'EXECUTION_COMPLETED', 'completed': ['run_external_flow_solve'],
            'plan_operations': ['run_external_flow_solve'],
            'final_sim': str(job / 'external_flow_solved.sim'),
            'final_sha256': digests['external_flow_solved.sim']}), encoding='utf-8')
        return run

    def test_a_per_macro_output_is_verified_not_unrecorded(self):
        import hashlib as _h
        intermediate = b'region bytes\n'
        digest = _h.sha256(intermediate).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self.run_dir(root, [
                {'index': 0, 'macro': 'BuildExternalFlowRegion.java', 'ran': True, 'status': 'ok',
                 'exit_code': 0, 'input': 'input.sim', 'output': 'external_flow_region.sim',
                 'output_sha256': digest},
                {'index': 1, 'macro': 'BuildExternalFlowMesh.java', 'ran': True, 'status': 'ok',
                 'exit_code': 0, 'input': 'external_flow_region.sim',
                 'output': 'external_flow_solved.sim',
                 'output_sha256': _h.sha256(b'final bytes\n').hexdigest()}],
                {'external_flow_region.sim': intermediate})
            delivery = adapter.verify_artifacts(run, adapter.stage_records(run))
        by_name = {item['name']: item for item in delivery['artifacts']}
        self.assertEqual('VERIFIED', by_name['external_flow_region.sim']['status'])
        self.assertEqual(digest, by_name['external_flow_region.sim']['recorded_sha256'])
        self.assertIn('macros[0].output_sha256', by_name['external_flow_region.sim']['recorded_by'])
        self.assertEqual([], [item['name'] for item in delivery['no_recorded_hash']])

    def test_a_file_with_no_record_at_all_is_still_reported_as_unrecorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self.run_dir(root, [{'index': 0, 'macro': 'BuildExternalFlowMesh.java', 'ran': True,
                                       'status': 'ok', 'exit_code': 0, 'input': 'input.sim',
                                       'output': 'external_flow_solved.sim',
                                       'output_sha256': None}],
                               {'a_product_nobody_hashed.sim': b'x\n'})
            delivery = adapter.verify_artifacts(run, adapter.stage_records(run))
        self.assertEqual(['stages/run_external_flow_solve/a_product_nobody_hashed.sim'],
                         [item['name'] for item in delivery['no_recorded_hash']],
                         'a file the runner never hashed is still listed, by its path, as unrecorded')

    def test_contradictory_records_are_an_explicit_failure(self):
        import hashlib as _h
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self.run_dir(root, [
                {'index': 0, 'macro': 'BuildExternalFlowRegion.java', 'ran': True, 'status': 'ok',
                 'exit_code': 0, 'input': 'input.sim', 'output': 'external_flow_region.sim',
                 'output_sha256': _h.sha256(b'one\n').hexdigest()},
                {'index': 1, 'macro': 'ConfigureExternalFlowPhysics.java', 'ran': True, 'status': 'ok',
                 'exit_code': 0, 'input': 'external_flow_region.sim',
                 'output': 'external_flow_region.sim', 'output_sha256': '0' * 64}],
                {'external_flow_region.sim': b'one\n'})
            delivery = adapter.verify_artifacts(run, adapter.stage_records(run))
        entry = next(item for item in delivery['artifacts'] if item['name'] == 'external_flow_region.sim')
        self.assertEqual('CONFLICTING_RECORDS', entry['status'])
        self.assertIsNone(entry['recorded_sha256'])
        self.assertEqual(2, len(entry['recorded_sha256_by_source']))
        self.assertFalse(delivery['download_succeeded'])
        self.assertIn('external_flow_region.sim', [item['name'] for item in delivery['failures']])


class TheReportStepRunsEndToEnd(unittest.TestCase):
    """②R14: the round's own main run found that report_run could crash AFTER writing the report.

    The first version of the attribution line referenced an undefined name, so the run's STAR work and the
    report were both fine while the step exited with a NameError and no outcome. A test that only calls
    build_evidence cannot see that; this one drives report_run with a stubbed backend.
    """

    def test_report_run_returns_an_outcome_and_records_the_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / 'r14-e2e-1'
            job = run / 'stages' / 'run_external_flow_solve'
            job.mkdir(parents=True)
            import hashlib as _h
            sim = job / 'external_flow_solved.sim'
            sim.write_bytes(b'x\n')
            digest = _h.sha256(sim.read_bytes()).hexdigest()
            (job / 'stage-solve.json').write_text(json.dumps({
                'schema_version': 1, 'stage': 'solve', 'state': 'ok', 'seconds': 0.5,
                'artifacts': ['external_flow_solve.json', 'external_flow_solved.sim'],
                'input': 'input.sim', 'input_sha256': 'a' * 64, 'output': 'external_flow_solved.sim',
                'output_sha256': digest,
                'macros': [{'index': 0, 'macro': 'RunExternalFlowSolve.java', 'ran': True, 'status': 'ok',
                            'exit_code': 0}]}), encoding='utf-8')
            (job / 'external_flow_solve.json').write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
            (job / 'checkpoint.json').write_text(json.dumps({'output_path': str(sim),
                                                             'output_sha256': digest}), encoding='utf-8')
            (run / 'state.json').write_text(json.dumps({
                'state': 'EXECUTION_COMPLETED', 'completed': ['run_external_flow_solve'],
                'plan_operations': ['run_external_flow_solve'], 'final_sim': str(sim),
                'final_sha256': digest}), encoding='utf-8')
            outcome = adapter.report_run('r14-e2e-1', root / 'evidence', run_dir=run)
        attribution = outcome['conclusions']['process']['stage_record_attribution']
        self.assertEqual('plan_and_catalog', attribution['per_stage']['run_external_flow_solve'])
        self.assertEqual('REPORT_OK', outcome['report']['verdict'])


class AnInheritedStageRecordIsNotThisStagesOutcome(unittest.TestCase):
    """②R14: the validation job directory also holds the SOLVE stage's record (a declared inherited input).

    The audit greps `stage-solve.json` for the read-only proof, so that file must sit in the validation job
    directory - which means the directory holds TWO `stage-*.json` files. The adapter must resolve the stage's
    OWN record from the job's `request.json` rather than from the file count, and must report the other one as
    inherited instead of treating the directory as ambiguous.
    """

    def _job(self, root: Path, key: str, stage: str, extra: dict | None = None) -> Path:
        job = root / 'runs' / 'r14-inherit-1' / 'stages' / key
        job.mkdir(parents=True)
        (job / 'request.json').write_text(json.dumps({'stage': stage, 'key': key}), encoding='utf-8')
        (job / ('stage-%s.json' % stage)).write_text(json.dumps({'stage': stage, 'state': 'ok'}), encoding='utf-8')
        if extra:
            (job / extra['name']).write_text(json.dumps(extra['body']), encoding='utf-8')
        return job

    def test_the_own_record_wins_and_the_inherited_one_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self._job(root, 'audit_external_flow_convergence', 'validation',
                            {'name': 'stage-solve.json', 'body': {'stage': 'solve', 'state': 'ok'}})
            records = adapter.stage_records(root / 'runs' / 'r14-inherit-1')
        self.assertEqual(1, len(records))
        self.assertEqual(job / 'stage-validation.json', records[0]['record_path'])
        self.assertEqual('validation', records[0]['record']['stage'])
        self.assertEqual(['stage-solve.json'], records[0]['inherited_stage_records'])
        self.assertEqual([], records[0]['ambiguous_stage_records'])

    def test_an_unknown_extra_record_still_resolves_the_own_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._job(root, 'audit_external_flow_convergence', 'validation',
                      {'name': 'stage-something-else.json', 'body': {'stage': 'something-else', 'state': 'ok'}})
            records = adapter.stage_records(root / 'runs' / 'r14-inherit-1')
        self.assertEqual('validation', records[0]['record']['stage'])
        self.assertEqual(['stage-something-else.json'], records[0]['inherited_stage_records'])

    def test_without_a_request_record_the_submitted_plan_names_the_stage(self):
        # The downloaded evidence copy of a run does NOT contain request.json (the runner's artifact listing
        # skips it), so the plan + catalog are the available source of truth. Review round 1 of this round
        # found the previous fallback adopted a foreign single payload instead.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / 'r14-inherit-1'
            job = run / 'stages' / 'audit_external_flow_convergence'
            job.mkdir(parents=True)
            (run / 'state.json').write_text(json.dumps({'plan_operations': ['audit_external_flow_convergence']}),
                                            encoding='utf-8')
            (job / 'stage-validation.json').write_text(json.dumps({'stage': 'validation', 'state': 'ok'}),
                                                       encoding='utf-8')
            (job / 'stage-solve.json').write_text(json.dumps({'stage': 'solve', 'state': 'ok'}), encoding='utf-8')
            records = adapter.stage_records(run)
        self.assertEqual('validation', records[0]['expected_stage'])
        self.assertEqual('validation', records[0]['record']['stage'])
        self.assertEqual('plan_and_catalog', records[0]['record_attribution'])
        self.assertEqual(['stage-solve.json'], records[0]['inherited_stage_records'])

    def test_a_foreign_single_payload_is_never_adopted_when_the_plan_names_another_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / 'r14-inherit-1'
            job = run / 'stages' / 'audit_external_flow_convergence'
            job.mkdir(parents=True)
            (run / 'state.json').write_text(json.dumps({'plan_operations': ['audit_external_flow_convergence']}),
                                            encoding='utf-8')
            (job / 'stage-solve.json').write_text(json.dumps({'stage': 'solve', 'state': 'ok'}), encoding='utf-8')
            records = adapter.stage_records(run)
        self.assertIsNone(records[0]['record'],
                          'a record claiming to be a different stage than the plan ran must not be adopted')
        self.assertEqual(['stage-solve.json'], records[0]['ambiguous_stage_records'])

    def test_a_single_payload_is_used_but_tagged_when_the_plan_says_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / 'r14-inherit-1'
            job = run / 'stages' / 'some_legacy_stage'
            job.mkdir(parents=True)
            (job / 'stage-legacy.json').write_text(json.dumps({'stage': 'legacy', 'state': 'ok'}), encoding='utf-8')
            records = adapter.stage_records(run)
            built = adapter.build_evidence('r14-inherit-1', run, root / 'evidence')
        self.assertEqual('legacy', records[0]['record']['stage'])
        self.assertEqual('single_payload_fallback', records[0]['record_attribution'])
        # and the outcome must SURFACE that caveat (review round 1, concern 1), not swallow it
        self.assertEqual(['some_legacy_stage -> legacy'], built['fallback_attributions'])

    def test_without_a_request_record_an_ambiguous_directory_is_still_refused(self):
        # The old fail-closed behaviour is kept for a directory whose own stage cannot be resolved: two
        # records and no way to tell which one is this stage's outcome is not something to guess about.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / 'runs' / 'r14-inherit-1' / 'stages' / 'k'
            job.mkdir(parents=True)
            for name in ('stage-a.json', 'stage-b.json'):
                (job / name).write_text(json.dumps({'stage': name[6:-5], 'state': 'ok'}), encoding='utf-8')
            records = adapter.stage_records(root / 'runs' / 'r14-inherit-1')
        self.assertIsNone(records[0]['record'])
        self.assertEqual(['stage-a.json', 'stage-b.json'], records[0]['ambiguous_stage_records'])



if __name__ == '__main__':
    unittest.main(verbosity=2)
