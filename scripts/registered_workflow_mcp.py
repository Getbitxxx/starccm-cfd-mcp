"""Thin MCP calls for reviewed registrations; execution remains in the existing controller.

No background Python scheduler, STAR loop, numeric rule, or transfer implementation lives here.
The durable handle only binds a request to the existing Windows trial and reviewed registration.
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys

from run_registered_workflow import ROOT, digest, load, prepare


def registration(workflow):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', workflow):
        raise ValueError('Invalid registered workflow id')
    path = ROOT / 'registered-workflows' / workflow / 'manifest.json'
    manifest = load(path)
    if manifest.get('id') != workflow or manifest.get('mechanism') != 'restricted_trial':
        raise ValueError('Invalid restricted registration')
    relative = Path(manifest['mcp_evidence'])
    if relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('test-evidence',):
        raise ValueError('Registration evidence must be repository evidence')
    evidence = (ROOT / relative).resolve()
    if not evidence.is_relative_to((ROOT / 'test-evidence').resolve()):
        raise ValueError('Registration evidence escapes repository evidence')
    return manifest, evidence, path


def list_registered_workflows():
    result = []
    for path in sorted((ROOT / 'registered-workflows').glob('*/manifest.json')):
        manifest = load(path)
        if 'mcp_evidence' not in manifest:
            continue
        registration(manifest['id'])
        result.append({key: manifest[key] for key in (
            'id', 'title', 'scope', 'input_identity_sha256', 'case_spec_sha256',
            'star_seconds_cap', 'capability_levels', 'paired_counterexample_scope')})
    return result


def valid_run_id(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{7,63}', value):
        raise ValueError('run_id must be 8-64 lowercase letters, digits or hyphens')
    return value


def checked_registration(workflow):
    manifest, evidence, path = registration(workflow)
    review = load(evidence / 'pipeline-review.json')
    if review.get('approved') is not True or review.get('independent') is not True:
        raise ValueError('Independent review required')
    for relative in manifest['adapter_files'] + [str(path.relative_to(ROOT))]:
        if review.get('registration_hashes', {}).get(relative) != digest(ROOT / relative):
            raise ValueError('Reviewed registration bytes changed: ' + relative)
    return manifest, evidence, path


def invoke(command, log_path):
    # Calls are bounded launch/poll/transfer commands, never the entry's solve-wait loop.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    log_path.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('Existing entry refused/failed; see ' + str(log_path))
    return result.stdout


def submit_registered_workflow(workflow_id, source_sim, case_spec_path, request_id):
    run_id = valid_run_id(request_id)
    manifest, evidence, _ = checked_registration(workflow_id)
    handles = evidence / 'mcp-handles'
    handles.mkdir(parents=True, exist_ok=True)
    with (handles / 'submission.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another registered submission is being prepared; retry same request_id')
        # prepare rechecks exact inputs, budget and adapter pins, stages immutable copies.
        manifest, _ = prepare(workflow_id, source_sim, case_spec_path, evidence, run_id)
        intent = {'workflow_id': workflow_id,
                  'source_sha256': digest(source_sim), 'case_spec_sha256': digest(case_spec_path)}
        handle_path = handles / (run_id + '.json')
        if handle_path.exists():
            handle = load(handle_path)
            if handle['intent'] != intent:
                raise ValueError('request_id already binds a different request')
            return {**handle, 'idempotent_replay': True, 'engineering_verified': False}
        review = load(evidence / 'pipeline-review.json')
        # Registration approval is not unlimited compute authorization. The independently
        # reviewed request allocation fixes the permitted new trial; replays cost no new STAR.
        if run_id not in review.get('registered_request_ids', []):
            raise ValueError('New run_id needs a reviewed compute allocation')
        budget = load(ROOT / 'progress.json')['budget']['per_case']['original-geometry']
        # 2026-10-14 用户裁决 A（AGENTS.md §6）：STAR 秒数台账**只记账、不拦提交**。该值仍然比较，
        # 但结果写成 budget_warning 并照常放行；台账不足不再是 MCP 准入的拒绝理由。
        budget_warning = None
        if budget['remaining_authorized_star_seconds'] < manifest['star_seconds_cap']:
            budget_warning = ('original-geometry STAR ledger below the registered run cap: '
                              'remaining_authorized_star_seconds=%s cap=%s'
                              % (budget['remaining_authorized_star_seconds'],
                                 manifest['star_seconds_cap']))
        handle = {'run_id': run_id, 'intent': intent, 'state': 'SUBMITTING',
                  'evidence': str(evidence), 'engineering_verified': False}
        if budget_warning is not None:
            handle['budget_warning'] = budget_warning
        # Persist BEFORE remote launch: interrupted/uncertain submissions are queried, never re-run.
        handle_path.write_text(json.dumps(handle, indent=2) + '\n')
        command = [sys.executable, str(ROOT / 'scripts/air3b_pipeline_trial.py'), 'start',
                   '--trial-id', run_id, '--pipeline', str(ROOT / manifest['pipeline']),
                   '--evidence', str(evidence)]
        for path in load(evidence / 'submitted-inputs.json')['inputs']:
            command += ['--step', path]
        try:
            text = invoke(command, handles / (run_id + '-start.log'))
            handle.update(state='SUBMITTED', existing_start_result=json.loads(text))
        except ValueError:
            handle['state'] = 'SUBMISSION_UNCERTAIN_OR_REFUSED'
            handle_path.write_text(json.dumps(handle, indent=2) + '\n')
            raise
        handle_path.write_text(json.dumps(handle, indent=2) + '\n')
        return handle


def verify_historical_reader_review(manifest, evidence, handle, review):
    review_path = evidence / 'pipeline-review.json'
    migration_path = evidence / 'historical-read-review.json'
    if not migration_path.is_file():
        raise ValueError('Historical read support bytes changed; current independent read-only review required')
    migration = load(migration_path)
    handle_path = evidence / 'mcp-handles' / (handle['run_id'] + '.json')
    required = set(review['support_hashes']) | {
        'scripts/air3b_pipeline_trial.py', 'scripts/registered_workflow_mcp_v2.py',
        'scripts/registered_workflow_mcp.py', 'scripts/run_registered_workflow_v2.py',
        'scripts/server_v2.py', manifest['presentation_entry']}
    required.update(f for f in manifest['adapter_files'] if Path(f).name == 'output-contract.json')
    if manifest['presentation_entry'] == 'cases/static-cylinder-step/render_report.py':
        required.update(('scripts/run_sensitivity_study.py', 'scripts/evaluate_sensitivity.py',
                         'scripts/grid_convergence.py'))
    if (migration.get('approved') is not True or migration.get('independent') is not True or
            migration.get('execution_authorized') is not False or
            migration.get('registered_request_ids') != [] or
            migration.get('allowed_actions') != ['query', 'fetch'] or
            migration.get('historical_review_sha256') != digest(review_path) or
            migration.get('handle_hashes', {}).get(handle['run_id']) != digest(handle_path) or
            any(migration.get('current_reader_hashes', {}).get(f) != digest(ROOT / f) for f in required)):
        raise ValueError('Independent read-only migration does not bind current reader and historical identity')


def lookup(run_id):
    valid_run_id(run_id)
    found = []
    for item in list_registered_workflows():
        manifest, evidence, manifest_path = registration(item['id'])
        path = evidence / 'mcp-handles' / (run_id + '.json')
        if path.is_file():
            handle = load(path)
            review = load(evidence / 'pipeline-review.json')
            start, intent = handle.get('existing_start_result', {}), handle.get('intent', {})
            pins = ('pipeline_sha256', 'controller_sha256', 'runner_sha256',
                    'macro_hashes', 'input_hashes', 'support_hashes')
            if not all(key in start for key in pins):
                # An older handle without an executed-byte snapshot gets no historical
                # exemption; it still needs the full current-byte approval.
                checked_registration(item['id'])
            else:
                if (review.get('approved') is not True or review.get('independent') is not True or
                        review.get('registration_hashes', {}).get(str(manifest_path.relative_to(ROOT))) != digest(manifest_path) or
                        handle.get('run_id') != run_id or start.get('trial_id') != run_id or
                        run_id not in review.get('registered_request_ids', []) or
                        intent.get('workflow_id') != manifest['id'] or
                        intent.get('source_sha256') != manifest['input_identity_sha256'] or
                        intent.get('case_spec_sha256') != manifest['case_spec_sha256'] or
                        review.get('input_hashes', {}).get(manifest.get('source_input_name', 'source_input.sim')) != manifest['input_identity_sha256'] or
                        review.get('input_hashes', {}).get('case_spec.json') != manifest['case_spec_sha256'] or
                        any(start.get(key) != review.get(key) for key in pins)):
                    raise ValueError('Historical run handle differs from its approved executed bytes')
                readers = ('scripts/stage_plan.py', 'scripts/run_record_gate.py', 'scripts/backend.py',
                           'scripts/generate_run_report.py', 'scripts/run_static_external_flow_e2e.py',
                           'cases/air-three-blade/air3b_summary.py', 'scripts/air3b_convergence_gate.py',
                           'scripts/evaluate_convergence.py')
                if (digest(ROOT / 'scripts/air3b_pipeline_trial.py') != review['controller_sha256'] or
                        digest(ROOT / manifest['presentation_entry']) != review['registration_hashes'].get(manifest['presentation_entry']) or
                        any(digest(ROOT / rel) != review['support_hashes'].get(rel) for rel in readers) or
                        any(digest(ROOT / rel) != review['registration_hashes'].get(rel)
                            for rel in manifest['adapter_files'] if Path(rel).name == 'output-contract.json')):
                    verify_historical_reader_review(manifest, evidence, handle, review)
            found.append((manifest, evidence, handle))
    if len(found) != 1:
        raise ValueError('Unknown or ambiguous registered run_id')
    return found[0]


def get_registered_workflow_run(run_id):
    _, evidence, handle = lookup(run_id)
    command = [sys.executable, str(ROOT / 'scripts/air3b_pipeline_trial.py'), 'poll',
               '--trial-id', run_id, '--evidence', str(evidence)]
    # Existing poll persists failure/numeric-gate verdict before raising; return that honest state.
    result_path = evidence / (run_id + '-result.json')
    previous_stamp = result_path.stat().st_mtime_ns if result_path.exists() else None
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (evidence / 'mcp-handles' / (run_id + '-poll.log')).write_text(result.stdout + '\n' + result.stderr)
    if result.returncode and (not result_path.exists() or
            result_path.stat().st_mtime_ns == previous_stamp):
        raise ValueError('Existing poll failed without a fresh state record; old state is not a new query')
    state = load(result_path)
    if result.returncode and state.get('state') != 'GATE_FAILED':
        raise ValueError('Existing poll failed; persisted state is not a numeric-gate refusal')
    return {'run_id': run_id, 'state': state.get('state'), 'terminal': state.get('state') in
            {'AIR3B_PIPELINE_COMPLETE', 'FAILED', 'GATE_FAILED'},
            'process': state, 'delivery': 'NOT_FETCHED_BY_THIS_CALL', 'engineering_verified': False}


def fetch_registered_workflow_results(run_id):
    manifest, evidence, _ = lookup(run_id)
    result_path = evidence / (run_id + '-result.json')
    if not result_path.exists() or load(result_path).get('state') not in {
            'AIR3B_PIPELINE_COMPLETE', 'FAILED', 'GATE_FAILED'}:
        raise ValueError('Query a terminal state before retrieval; fetch never waits for STAR')
    command = [sys.executable, str(ROOT / 'scripts/run_static_external_flow_e2e.py'),
               '--trial-id', run_id, '--evidence', str(evidence), '--retrieve-only']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (evidence / 'mcp-handles' / (run_id + '-retrieve.log')).write_text(result.stdout + '\n' + result.stderr)
    # Existing reporter exit2 can mean honestly incomplete engineering/records; do not turn it to ok.
    if result.returncode not in (0, 2):
        raise ValueError('Existing result retrieval/report failed; see retrieval log')
    report = load(evidence / (run_id + '-report.json'))
    presentation = None
    if report.get('report_verdict') == 'REPORT_OK':
        invoke([sys.executable, str(ROOT / manifest['presentation_entry']),
                '--run-dir', str(evidence / run_id)],
               evidence / 'mcp-handles' / (run_id + '-presentation.log'))
        presentation = load(evidence / run_id / 'presentation.json')
    return {'run_id': run_id, 'process': load(result_path), 'delivery': {
            'checkpoints': load(evidence / (run_id + '-checkpoints.json')),
            'exports': load(evidence / (run_id + '-exports.json')) if
            (evidence / (run_id + '-exports.json')).is_file() else None,
            'presentation': presentation}, 'report': report, 'engineering_verified': False}
