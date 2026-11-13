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

from run_registered_workflow import ROOT, digest, load
from run_registered_workflow_v2 import prepare, valid_readback_environment_artifacts, validate_local_mesh_controls
import registered_workflow_mcp as legacy


def budget_ledger_warning(account, cap):
    """STAR 秒数台账的**只记账**判定（AGENTS.md §6 用户 2026-10-14 裁决 A）。

    两个 key 仍然逐项比较；不足时只返回一条 warning 文本，返回 None 表示足够。
    **调用方不得据此拒绝提交**——MCP 准入不得以台账不足为由拒绝（独立审查 F1：studies 清单的提交门
    曾把 budget_ready 折进 submission_ready，使台账不足以 'aggregate budget' 为由拒绝提交）。
    """
    short = []
    if account.get('remaining_authorized_star_seconds', 0) < cap:
        short.append('remaining_authorized_star_seconds')
    if account.get('window2_remaining_seconds', 0) < cap:
        short.append('window2_remaining_seconds')
    if not short:
        return None
    return ('registered-case STAR ledger below the requested cap for ' + ', '.join(short)
            + ': cap=%s values=%s' % (cap, {key: account.get(key) for key in short}))


def studies_plan_submission_blocker(selected):
    """studies 清单的提交门：只看计划可执行性与已审查的当前字节，**不看台账**（裁决 A）。"""
    if selected.get('call_plan_executable') and selected.get('submission_ready'):
        return None
    return ('Complete selected plan lacks current reviewed registrations or request allocation')


def validate_registered_workflow_plan(workflow_id, case_spec, plan_operations=None):
    """The existing plan gate's candidate branch checks exact calls, not a registry union."""
    validate_local_mesh_controls(case_spec)
    from run_registered_workflow_v2 import PLAN, repo_path
    from casespec import validate_case_spec, resolve_operations
    from run_record_gate import assert_citable
    from stage_plan import plan_pipeline
    import run_sensitivity_study as study
    from air3b_pipeline_trial import check_review

    def reviewed_registration(workflow, request_id):
        try:
            item, folder, path = checked_registration(workflow)
            pipeline = load(ROOT / item['pipeline'])
            check_review(folder, pipeline['macro_hashes'], pipeline['input_hashes'],
                digest(ROOT / 'scripts/security/trial_air3b_pipeline.ps1'),
                digest(ROOT / 'scripts/air3b_pipeline_trial.py'), digest(ROOT / item['pipeline']))
            review = load(folder / 'pipeline-review.json')
            receipt = load(folder / 'registration.json')
            return (request_id in review.get('registered_request_ids', []) and
                receipt.get('state') == 'REGISTERED_CURRENT_BYTES' and
                receipt.get('manifest_sha256') == digest(path) and
                receipt.get('review_sha256') == digest(folder / 'pipeline-review.json') and
                receipt.get('input_hashes') == pipeline['input_hashes'])
        except (ValueError, FileNotFoundError):
            return False
    manifest, evidence, _ = registration(workflow_id)
    candidate = load(evidence / 'candidate.json')
    spec_path = repo_path(candidate['case_spec_path'])
    if load(spec_path) != case_spec:
        raise ValueError('Selected CaseSpec differs from candidate input')
    record = repo_path(candidate['audited_view_source']); assert_citable(record)
    actual = load(record)
    source_sha = digest(repo_path(candidate['source_sim']))
    base_pipeline = load(ROOT / manifest['pipeline'])
    from resource_contract import validate_pipeline_resources
    resource_contract = validate_pipeline_resources(base_pipeline, manifest['star_seconds_cap'])
    plan_pipeline(base_pipeline)
    if any(digest(ROOT / 'macros' / name) != sha for name, sha in base_pipeline['macro_hashes'].items()):
        raise ValueError('Base macro bytes differ from actual executable call')
    if (source_sha != candidate['input_hashes'].get(manifest['source_input_name']) or
            digest(spec_path) != candidate['input_hashes']['case_spec.json'] or
            digest(ROOT / manifest['pipeline']) != candidate['pipeline_sha256']):
        raise ValueError('Candidate source, CaseSpec or pipeline bytes changed')
    if manifest.get('input_kind') == 'successful_solved_readback':
        from run_registered_workflow_v2 import validate_solved_readback_source, validate_readback_deadline_contract
        _, _, _, _, _, _, citation, _, _ = validate_solved_readback_source(
            candidate['source_sim'], spec_path, manifest['checkpoint_contract'])
        deadline = validate_readback_deadline_contract(manifest, base_pipeline)
        selected = ['export_evidence', 'report'] if plan_operations is None else plan_operations
        stages = base_pipeline['stages']
        executable = (selected == ['export_evidence', 'report'] and len(stages) == 1 and
            stages[0]['name'] == 'property-nearwall' and stages[0]['input'] == 'external_flow_solved.sim' and
            stages[0]['output'] is None and stages[0]['macro_outputs'] == [None] and
            stages[0]['macros'] == ['ExportExternalFlowResults.java'] and stages[0]['timeout_seconds'] == deadline and
            valid_readback_environment_artifacts(base_pipeline) and
            (resource_contract is not None or (base_pipeline.get('logical_cores') == 8 and base_pipeline.get('memory_gb') == 64)) and
            base_pipeline['input_hashes'] == candidate['input_hashes'] and manifest['star_seconds_cap'] == deadline)
        account = load(ROOT / 'progress.json')['budget']['per_case'].get(manifest['budget_case'], {})
        budget_warning = budget_ledger_warning(account, deadline)
        budget_ready = budget_warning is None
        return {'workflow_id': workflow_id, 'valid': executable, 'executable': executable,
            'call_plan_executable': executable, 'plan_scope': 'READ_ONLY_SOLVED_PROPERTY_NEARWALL',
            'complete_engineering_plan': False, 'actual_study_calls': {},
            'submission_ready': bool(executable and reviewed_registration(workflow_id, workflow_id)),
            'budget_ready': budget_ready, 'budget_warning': budget_warning,
            'required_STAR_cap_seconds': deadline,
            'checkpoint_citation': citation, 'star_started': False, 'engineering_verified': False,
            'study_call_blockers': ['Read-only property evidence is not a solve or sensitivity study'],
            'plan_blocker': None if executable else 'Read-only property plan/input/stages mismatch'}
    if manifest.get('input_kind') == 'successful_solve_checkpoint':
        from run_registered_workflow_v2 import validate_checkpoint_source
        _, _, _, _, _, _, citation, _, _ = validate_checkpoint_source(
            candidate['source_sim'], spec_path, manifest['checkpoint_contract'])
        finite_operations = ['run_external_flow_solve', 'audit_external_flow_convergence', 'report', 'export_evidence']
        selected = finite_operations if plan_operations is None else plan_operations
        stages = base_pipeline['stages']
        executable = (selected == finite_operations and
            [s['name'] for s in stages] == ['solve', 'validation'] and
            stages[0]['input'] == manifest['source_input_name'] and
            stages[0]['macros'] == ['RunExternalFlowSolve.java'] and
            stages[1]['macros'] == ['AuditExternalFlowConvergence.java', 'ExportExternalFlowResults.java'] and
            base_pipeline['input_hashes'] == candidate['input_hashes'] and
            sum(s['timeout_seconds'] for s in stages) <= manifest['star_seconds_cap'])
        account = load(ROOT / 'progress.json')['budget']['per_case'].get(manifest['budget_case'], {})
        budget_warning = budget_ledger_warning(account, manifest['star_seconds_cap'])
        budget_ready = budget_warning is None
        return {'workflow_id': workflow_id, 'valid': executable, 'executable': executable,
            'call_plan_executable': executable, 'plan_scope': 'FINITE_CHECKPOINT_SOLVE_VALIDATION',
            'complete_engineering_plan': False, 'actual_study_calls': {},
            'submission_ready': bool(executable and reviewed_registration(workflow_id, workflow_id)),
            'budget_ready': budget_ready, 'budget_warning': budget_warning,
            'required_STAR_cap_seconds': manifest['star_seconds_cap'],
            'checkpoint_citation': citation, 'star_started': False, 'engineering_verified': False,
            'study_call_blockers': ['Complete mesh/domain studies are separate required calls'],
            'plan_blocker': None if executable else 'Finite checkpoint plan/input/stages mismatch; cannot request complete studies here'}
    if manifest.get('input_kind') == 'audited_static_geometry_finite' and not manifest.get('studies'):
        selected = PLAN if plan_operations is None else plan_operations
        stages = base_pipeline['stages']
        expected = [('audit-body', ['AuditStaticBodyGeometry.java']),
            ('build-domain', ['BuildExternalDomain.java']),
            ('region-physics-mesh', ['BuildExternalFlowRegion.java', 'ConfigureExternalFlowPhysics.java', 'BuildExternalFlowMesh.java']),
            ('mesh-quality-audit', ['AuditExternalFlowMesh.java']), ('solve', ['RunExternalFlowSolve.java']),
            ('validation', ['AuditExternalFlowConvergence.java', 'ExportExternalFlowResults.java'])]
        executable = (selected == PLAN and [(s['name'], s['macros']) for s in stages] == expected and
            stages[0]['input'] == manifest['source_input_name'] == 'static_body_geometry.sim' and
            base_pipeline['input_hashes'] == candidate['input_hashes'] and
            sum(s['timeout_seconds'] for s in stages) <= manifest['star_seconds_cap'] and
            case_spec.get('finite_execution', {}).get('expected_start_iteration', 0) == 0)
        account = load(ROOT / 'progress.json')['budget']['per_case'].get(manifest['budget_case'], {})
        budget_warning = budget_ledger_warning(account, manifest['star_seconds_cap'])
        budget_ready = budget_warning is None
        return {'workflow_id': workflow_id, 'valid': executable, 'executable': executable,
            'call_plan_executable': executable, 'plan_scope': 'FINITE_AUDITED_GEOMETRY_LEVEL',
            'complete_engineering_plan': False, 'actual_study_calls': {},
            'submission_ready': bool(executable and reviewed_registration(workflow_id, workflow_id)),
            'budget_ready': budget_ready, 'budget_warning': budget_warning,
            'required_STAR_cap_seconds': manifest['star_seconds_cap'],
            'geometry_citation': candidate['geometry_citation'], 'star_started': False, 'engineering_verified': False,
            'study_call_blockers': ['One finite level does not establish complete mesh/domain studies'],
            'plan_blocker': None if executable else 'Finite geometry level plan/input/stages mismatch'}
    scale = {'m': 1, 'mm': .001}[case_spec['model_length_unit']]
    audit = {'status': 'ok', 'geometryParts': [{'name': actual['body_part_name'],
        'isClosed': actual['is_closed'], 'isManifold': actual['is_manifold'],
        'boundingBoxRaw': [v / scale for v in actual['measured_bbox_si_m']]}], 'regions': []}
    selected = PLAN + ['mesh_study', 'domain_study'] if plan_operations is None else plan_operations
    registry = resolve_operations(case=case_spec.get('case'))
    calls, blockers = {}, []
    if case_spec.get('model_length_unit') != 'm' and any(
            'ExportExternalFlowResults.java' in stage.get('macros', [])
            for stage in base_pipeline['stages']):
        blockers.append('Installed ExportExternalFlowResults requires SI metre CaseSpec; audited unit conversion absent')
    base_call = candidate.get('base_call')
    if not base_call:
        blockers.append('base_call missing; prepare a new complete candidate version')
    elif (base_call.get('tool') != 'submit_registered_workflow' or
            base_call.get('workflow_id') != workflow_id or
            repo_path(base_call['source_sim']) != repo_path(candidate['source_sim']) or
            repo_path(base_call['case_spec_path']) != spec_path or
            base_call.get('star_seconds_cap') != manifest['star_seconds_cap']):
        raise ValueError('Base actual call differs from selected candidate')
    else:
        valid_run_id(base_call['request_id'])
    for kind in ('mesh', 'domain'):
        info = manifest.get('studies', {}).get(kind)
        if not info:
            blockers.append(kind + '_study has no actual level calls'); continue
        levels_path = repo_path(info['levels_manifest'])
        levels = load(levels_path)
        if digest(levels_path) != info['sha256'] or levels['spec_sha256'] != digest(spec_path):
            raise ValueError('Study manifest or source CaseSpec changed')
        if levels['geometry_sha256'] != digest(repo_path(candidate['source_sim'])):
            raise ValueError('Study source differs from candidate source')
        declared = study.study_levels(case_spec, kind)
        if [v['level'] for v in levels['levels']] != [v['name'] for v in declared]:
            raise ValueError('Study calls omit or reorder declared levels')
        level_calls = []
        for index, level in enumerate(levels['levels']):
            if level.get('reuse'):
                # Existing equality/citation rules decide if reuse is legitimate.
                equality = study.reused_reference(Path('.'), '', {'reuse': level['reuse']},
                    load(repo_path(level['case_spec'])), repo_path(candidate['source_sim']))
                if not equality.get('ok'):
                    blockers.append(kind + '/' + level['level'] + ': reuse differs')
                level_calls.append({'level': level['level'], 'reuse': equality}); continue
            sub, sub_evidence, _ = registration(level['workflow_id'])
            pipeline = load(ROOT / sub['pipeline']); plan_pipeline(pipeline)
            if any(digest(ROOT / 'macros' / name) != sha for name, sha in pipeline['macro_hashes'].items()):
                raise ValueError('Study macro bytes differ from actual executable call')
            level_spec = repo_path(level['case_spec'])
            if (level.get('submit_tool') != 'submit_registered_workflow' or
                    valid_run_id(level['request_id']) != level['trial_id'] or
                    load(level_spec) != study.level_case_spec(case_spec, kind, declared[index]) or
                    digest(ROOT / sub['pipeline']) != level['pipeline_sha256'] or
                    pipeline['input_hashes'].get('case_spec.json') != digest(level_spec) or
                    pipeline['input_hashes'].get('static_body_geometry.sim') != digest(repo_path(candidate['source_sim']))):
                raise ValueError('Study level actual call differs from selected plan')
            approved = reviewed_registration(level['workflow_id'], level['request_id'])
            level_calls.append({'level': level['level'], 'workflow_id': level['workflow_id'],
                'tool': level['submit_tool'], 'request_id': level['request_id'],
                'approved_current_bytes': approved, 'STAR_state': 'NOT_RUN_BY_PLAN_VALIDATION',
                'star_seconds_cap': sub['star_seconds_cap']})
        calls[kind] = level_calls
        # Only this exact candidate's checked calls bind the offline study to this executor.
        entry = registry['operations'][kind + '_study']
        entry['invoked_by'] = entry.get('invoked_by', []) + [{'mechanism': 'restricted_trial',
            'file': 'scripts/registered_workflow_mcp_v2.py', 'symbol': 'assemble_registered_workflow_studies'}]
    result = validate_case_spec(case_spec, audit=audit, case=case_spec.get('case'),
        plan_operations=selected, operation_registry=registry).as_dict()
    approved_base = reviewed_registration(workflow_id, candidate.get('base_call', {}).get('request_id'))
    required_seconds = manifest['star_seconds_cap'] + sum(c.get('star_seconds_cap', 0)
        for group in calls.values() for c in group)
    account = load(ROOT / 'progress.json')['budget']['per_case'].get(manifest['budget_case'], {})
    budget_warning = budget_ledger_warning(account, required_seconds)
    budget_ready = budget_warning is None
    result.update(workflow_id=workflow_id, actual_study_calls=calls,
        call_plan_executable=bool(result['executable'] and not blockers),
        # 独立审查 F1：submission_ready 不得把 budget_ready 折进来（AGENTS §6 裁决 A）。台账不足只报 warning。
        submission_ready=bool(result['executable'] and not blockers and approved_base and
            all(c.get('approved_current_bytes', bool(c.get('reuse', {}).get('ok'))) for group in calls.values() for c in group)),
        budget_ready=budget_ready, budget_warning=budget_warning, required_STAR_cap_seconds=required_seconds,
        study_call_blockers=blockers, star_started=False, engineering_verified=False)
    if blockers:
        result['executable'] = False; result['plan_blocker'] = '; '.join(blockers)
    return result


def assemble_registered_workflow_studies(workflow_id, run_id=None):
    import run_sensitivity_study as study
    manifest, evidence, _ = registration(workflow_id)
    candidate = load(evidence / 'candidate.json')
    validation = validate_registered_workflow_plan(workflow_id, load(candidate['case_spec_path']))
    if not validation['call_plan_executable']:
        raise ValueError('Complete selected study plan has unresolved actual calls')
    results = {kind: study.assemble(Path(candidate['case_spec_path']), kind,
        Path(info['levels_manifest']).parent, geometry=Path(candidate['source_sim']))
        for kind, info in manifest['studies'].items()}
    result = {'workflow_id': workflow_id, 'studies': results, 'star_started': False,
              'engineering_verified': False}
    if run_id is not None:
        owner, _, _ = lookup(run_id)
        if owner['id'] != workflow_id:
            raise ValueError('Completion report run belongs to another workflow')
        result['completion_report'] = study.completion_report(
            Path(candidate['case_spec_path']), evidence, run_id,
            {kind: Path(info['levels_manifest']).parent / (kind + '-study-report.json')
             for kind, info in manifest['studies'].items()}, evidence / 'completion-report')
    (evidence / 'studies-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def registration(workflow):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', workflow):
        raise ValueError('Invalid registered workflow id')
    path = ROOT / 'registered-workflows-v2' / workflow / 'manifest.json'
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
    result = legacy.list_registered_workflows()
    for path in sorted((ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
        manifest = load(path)
        if 'mcp_evidence' not in manifest:
            continue
        evidence = registration(manifest['id'])[1]
        if not (evidence / 'pipeline-review.json').is_file():
            continue
        if not (evidence / 'registration.json').is_file():
            continue
        historical_registration(manifest['id'])
        review = load(evidence / 'pipeline-review.json')
        item = {key: manifest[key] for key in (
            'id', 'title', 'scope', 'input_identity_sha256', 'case_spec_sha256',
            'star_seconds_cap', 'capability_levels', 'paired_counterexample_scope')}
        item['current_adapter_review_matches'] = all(
            review['registration_hashes'].get(relative) == digest(ROOT / relative)
            for relative in manifest['adapter_files'])
        item['historical_read_only_available'] = True
        result.append(item)
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
    from supersession_ledger import is_recorded_move
    for relative in manifest['adapter_files'] + [str(path.relative_to(ROOT))]:
        pinned = review.get('registration_hashes', {}).get(relative)
        live = digest(ROOT / relative)
        if pinned == live:
            continue
        # 2026-10-14（选项 a）：未被 ledger 记录的一次字节移动仍然失败；已记录的放行。
        if is_recorded_move(relative, live, ROOT / 'test-evidence'):
            continue
        raise ValueError('Reviewed registration bytes changed: ' + relative)
    return manifest, evidence, path


def historical_registration(workflow, handle=None):
    """Read existing runs against their receipt-bound approval, never approve new bytes."""
    manifest, evidence, path = registration(workflow)
    review_path = evidence / 'pipeline-review.json'
    review, receipt = load(review_path), load(evidence / 'registration.json')
    if (review.get('approved') is not True or review.get('independent') is not True or
            receipt.get('state') != 'REGISTERED_CURRENT_BYTES' or
            receipt.get('workflow_id') != workflow or
            receipt.get('manifest_sha256') != digest(path) or
            receipt.get('review_sha256') != digest(review_path) or
            receipt.get('input_hashes') != review.get('input_hashes')):
        raise ValueError('Historical registration approval snapshot differs from its receipt')
    if handle is not None:
        start, intent = handle.get('existing_start_result', {}), handle.get('intent', {})
        if (handle.get('run_id') not in review.get('registered_request_ids', []) or
                start.get('trial_id') != handle.get('run_id') or
                intent.get('workflow_id') != workflow or
                intent.get('source_sha256') != review['input_hashes'].get(manifest['source_input_name']) or
                intent.get('case_spec_sha256') != review['input_hashes'].get('case_spec.json')):
            raise ValueError('Historical run handle differs from its approved allocation or input')
        for key in ('pipeline_sha256', 'controller_sha256', 'runner_sha256',
                    'macro_hashes', 'input_hashes', 'support_hashes'):
            if key not in review or start.get(key) != review[key]:
                raise ValueError('Historical run handle differs from approval snapshot: ' + key)
        # Poll/report/download still use the frozen existing implementation. A changed
        # adapter is allowed to read history; changed reporting/transfer code is not.
        changed = any(digest(ROOT / relative) != expected
                      for relative, expected in review['support_hashes'].items())
        changed = changed or (digest(ROOT / 'scripts/air3b_pipeline_trial.py') != review['controller_sha256'] or
            digest(ROOT / manifest['presentation_entry']) != review['registration_hashes'].get(manifest['presentation_entry']))
        changed = changed or any(digest(ROOT / f) != review['registration_hashes'].get(f)
            for f in manifest['adapter_files'] if Path(f).name == 'output-contract.json')
        if changed:
            # Independent read-only migration never replaces the execution approval.
            legacy.verify_historical_reader_review(manifest, evidence, handle, review)
    return manifest, evidence, path


def invoke(command, log_path):
    # Calls are bounded launch/poll/transfer commands, never the entry's solve-wait loop.
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    log_path.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('Existing entry refused/failed; see ' + str(log_path))
    return result.stdout


def submit_registered_workflow(workflow_id, source_sim, case_spec_path, request_id):
    if not (ROOT / 'registered-workflows-v2' / workflow_id / 'manifest.json').is_file():
        # Reading legacy runs delegates unchanged. New starts through this upgraded
        # entry must also have an explicit current-byte approval of the entry itself.
        _, old_evidence, _ = legacy.checked_registration(workflow_id)
        review = load(old_evidence / 'pipeline-review.json')
        for relative in ('scripts/server_v2.py', 'scripts/registered_workflow_mcp_v2.py',
                         'scripts/run_registered_workflow_v2.py'):
            if review.get('registration_hashes', {}).get(relative) != digest(ROOT / relative):
                raise ValueError('New legacy submission requires current entry approval: ' + relative)
        return legacy.submit_registered_workflow(workflow_id, source_sim, case_spec_path, request_id)
    manifest = registration(workflow_id)[0]
    studies_budget_warning = None
    if manifest.get('studies'):
        selected = validate_registered_workflow_plan(workflow_id, load(case_spec_path))
        blocker = studies_plan_submission_blocker(selected)
        if blocker:
            raise ValueError(blocker)
        studies_budget_warning = selected.get('budget_warning')
    run_id = valid_run_id(request_id)
    manifest, evidence, _ = checked_registration(workflow_id)
    receipt_path = evidence / 'registration.json'
    if not receipt_path.is_file():
        raise ValueError('Reviewed candidate must be registered before submission')
    receipt = load(receipt_path)
    if (receipt.get('state') != 'REGISTERED_CURRENT_BYTES' or
            receipt.get('manifest_sha256') != digest(registration(workflow_id)[2]) or
            receipt.get('review_sha256') != digest(evidence / 'pipeline-review.json')):
        raise ValueError('Candidate registration does not bind current reviewed bytes')
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
        budget = load(ROOT / 'progress.json')['budget']['per_case'][manifest['budget_case']]
        # 2026-10-14 用户裁决 A（AGENTS.md §6）：STAR 秒数台账**只记账、不拦提交**。
        # 两个 key 仍然逐项比较（唯一实现 budget_ledger_warning），不足时只写 budget_warning 并照常放行。
        budget_warning = budget_ledger_warning(budget, manifest['star_seconds_cap']) or studies_budget_warning
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


def lookup(run_id):
    valid_run_id(run_id)
    found = []
    for path in sorted((ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
        item = load(path)
        evidence = registration(item['id'])[1]
        if not (evidence / 'mcp-handles' / (run_id + '.json')).is_file():
            continue
        path = evidence / 'mcp-handles' / (run_id + '.json')
        if path.is_file():
            handle = load(path)
            if handle.get('run_id') != run_id:
                raise ValueError('Historical handle run_id differs from the requested run')
            manifest, evidence, _ = historical_registration(item['id'], handle)
            found.append((manifest, evidence, handle))
    if len(found) != 1:
        raise ValueError('Unknown or ambiguous registered run_id')
    return found[0]


def get_registered_workflow_run(run_id):
    if not any((ROOT / load(p)['mcp_evidence'] / 'mcp-handles' / (valid_run_id(run_id) + '.json')).is_file() for p in (ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
        return legacy.get_registered_workflow_run(run_id)
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
    if not any((ROOT / load(p)['mcp_evidence'] / 'mcp-handles' / (valid_run_id(run_id) + '.json')).is_file() for p in (ROOT / 'registered-workflows-v2').glob('*/manifest.json')):
        return legacy.fetch_registered_workflow_results(run_id)
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
    if report.get('report_verdict') == 'REPORT_OK' and manifest.get('input_kind') != 'successful_solved_readback':
        invoke([sys.executable, str(ROOT / manifest['presentation_entry']),
                '--run-dir', str(evidence / run_id)],
               evidence / 'mcp-handles' / (run_id + '-presentation.log'))
        presentation = load(evidence / run_id / 'presentation.json')
    return {'run_id': run_id, 'process': load(result_path), 'delivery': {
            'checkpoints': load(evidence / (run_id + '-checkpoints.json')),
            'exports': load(evidence / (run_id + '-exports.json')) if
            (evidence / (run_id + '-exports.json')).is_file() else None,
            'presentation': presentation}, 'report': report, 'engineering_verified': False}
