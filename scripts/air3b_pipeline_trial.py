"""Scoped launcher for the AIR-3B restricted STAR pipeline trials.

Fixed, hash-pinned candidate stages run under the restricted worker identity in an
isolated job directory. This is the project's first-execution path for new macros:
compile -> independent review -> scoped trial -> reopen/readback -> promote.

Usage:
    python air3b_pipeline_trial.py start --trial-id <id> --pipeline <json> \
        --step <path> --evidence <dir>
    python air3b_pipeline_trial.py poll  --trial-id <id> --evidence <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from backend import Backend, ps_quote
from stage_plan import plan_pipeline

ROOT = r'C:\CodexCFDRestricted'
TEMPLATE = Path('test-evidence/restricted-worker-20261001/local-mesh-trial-config.json')
PIPELINE_RUNNER = 'trial_air3b_pipeline.ps1'
REVIEW_FILE = 'pipeline-review.json'
# Files whose bytes change what a trial RUNS but which are neither the runner nor the pipeline JSON.
# stage_plan.py decides the per-macro process chain, so a review record that does not pin it would
# leave the round-6 defect fixable-by-edit without any hash noticing - the "recorded but never
# compared" defect class this project keeps meeting. Each entry is compared by code in check_review.
# backend.py is included for the same reason and after a measured gap: this module does
# `from backend import Backend, ps_quote` (line 21) and `from backend import ... load_review_record`,
# so backend.py's bytes decide the SSH transport, the upload paths, the hash verification and the
# macro compilation that a trial actually performs - yet check_review pinned only the IMPORTING
# controller (this file). Editing backend.py therefore changed what ran while every pinned hash still
# matched: silent, not loud. It is also exactly how the sibling gate treats the file - backend.py
# pins `controller_sha256 = sha256(PLUGIN/'scripts'/'backend.py')` for the worker path - so leaving
# it out here was an inconsistency between the two gates, not a design choice. Adding it can only
# ADD a refusal; it can never let a previously-refused run through.
SUPPORT_FILES = ('scripts/production_catalog.py', 'scripts/resource_contract.py', 'scripts/stage_plan.py', 'scripts/run_record_gate.py', 'scripts/progress_matrix.py',
                 'scripts/backend.py', 'scripts/generate_run_report.py',
                 'scripts/run_static_external_flow_e2e.py',
                 'cases/air-three-blade/air3b_summary.py',
                 'scripts/air3b_convergence_gate.py', 'scripts/evaluate_convergence.py',
                 'scripts/security/provision_restricted_worker.ps1',
                 'scripts/security/grant_batch_logon.ps1',
                 'test-evidence/restricted-worker-20261001/local-mesh-trial-config.json')


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def support_hashes() -> dict:
    """Hashes of the supporting files a trial's behaviour depends on, compared by code.

    Paths resolve against the repository root, not the process working directory, so the comparison
    gives the same answer from anywhere (the reviewer found it only worked from the repo root).
    run_record_gate.py and progress_matrix.py are included because they decide what may be *certified*
    after a run: leaving them unpinned was a fresh instance of the "recorded but never compared" class.
    backend.py is included because it is this module's own import dependency, so its bytes decide the
    transport and the macro compilation a trial performs; pinning only the importing controller left
    the executed backend outside the record. See the SUPPORT_FILES comment above.
    generate_run_report.py is included from round 10 for the same reason as run_record_gate.py: it turns
    a run's evidence into the report a human reads, so if it were outside the record its bytes could
    change what the round's conclusion says while every pinned hash still matched.
    run_static_external_flow_e2e.py is included for the same reason again, and after the round-10
    independent review pointed out the gap: it is the entry command for the whole run, so it decides
    which pipeline and which inputs are submitted - leaving it unpinned would let an edit change what
    ran while this gate stayed green.
    cases/air-three-blade/air3b_summary.py is included from ②R10 for the third time in the same class:
    it is the reader that turns a run's evidence into this branch's three reported conclusions
    (process / engineering / delivery), so if it were outside the record its bytes could change what
    the round's summary SAYS while every pinned hash still matched. It is not on the run path - nothing
    in the entry command calls it - which is exactly why it needs the pin: an unpinned reader is the
    easiest place for a conclusion to drift. Adding it can only ADD a refusal.
    scripts/air3b_convergence_gate.py and scripts/evaluate_convergence.py are included after the ②R10
    independent review pointed them out: they ARE the existing engineering criterion, so if they were
    outside the record their bytes could change the engineering verdict the summary reports while every
    pinned hash still matched. scripts/security/provision_restricted_worker.ps1 and
    scripts/security/grant_batch_logon.ps1 are included for the same reason again: start() uploads and
    EXECUTES them on the Windows side, so they decide what a trial actually does and were pinned
    nowhere.
    test-evidence/restricted-worker-20261001/local-mesh-trial-config.json is the TEMPLATE start() reads to
    build every trial config (worker SID, ACLs, stage timeouts, the STAR launcher); the branch-1 handover
    for R11 listed it as the last unpinned file that decides what a trial runs.
    """
    root = Path(__file__).resolve().parents[1]
    return {name: hash_file(root / name) for name in SUPPORT_FILES}


def check_review(evidence: Path, macro_shas: dict, input_shas: dict, runner_sha: str,
                 controller_sha: str, pipeline_sha: str) -> None:
    """Bind the reviewed hashes to every artifact that changes the run's behaviour."""
    review = json.loads((evidence / REVIEW_FILE).read_text())
    if review.get('approved') is not True or review.get('independent') is not True:
        raise ValueError('Independent scoped review of the candidate stages is required')
    for name, digest in macro_shas.items():
        if review.get('macro_hashes', {}).get(name) != digest:
            raise ValueError('Reviewed hash differs from the current candidate: ' + name)
    if review.get('input_hashes') != input_shas:
        raise ValueError('Reviewed input hashes differ from the current inputs')
    for label, reviewed, actual in (('runner', review.get('runner_sha256'), runner_sha),
                                    ('controller', review.get('controller_sha256'), controller_sha),
                                    ('pipeline', review.get('pipeline_sha256'), pipeline_sha)):
        if reviewed != actual:
            raise ValueError('Reviewed %s hash differs from the current artifact' % label)
    reviewed_support = review.get('support_hashes')
    actual_support = support_hashes()
    if reviewed_support != actual_support:
        raise ValueError('Reviewed support hashes differ from the current artifacts: %r vs %r'
                         % (reviewed_support, actual_support))
    # 2026-10-14（C3）：MCP/backend 路径要求 review 钉住 resource_contract.py，而试跑路径此前不要求，
    # 于是同一份记录在两条路径上的结论相反（我此前按试跑路径签的多份记录都没有该字段却全部通过）。
    # 裁定：两条路径一律要求。该字段缺失或与当前实现不符即拒绝启动。
    reviewed_contract = review.get('resource_contract_sha256')
    actual_contract = hashlib.sha256((Path(__file__).resolve().parent / 'resource_contract.py').read_bytes()).hexdigest()
    if reviewed_contract != actual_contract:
        raise ValueError('Reviewed resource_contract_sha256 differs from the current implementation: '
                         '%r vs %r' % (reviewed_contract, actual_contract))


def start(b: Backend, trial_id: str, pipeline_path: Path, steps: list, evidence: Path) -> dict:
    if not re.fullmatch(r'[a-z0-9-]{8,64}', trial_id):
        raise ValueError('Invalid trial id')
    pipeline = json.loads(pipeline_path.read_text())
    # Centralised per-macro plan (scripts/stage_plan.py). Round 6 showed that a stage run as one
    # `-batch A,B,C` process keeps executing later macros after an earlier one throws, so a stage with
    # several macros must declare one macro_outputs entry per macro and is executed as one STAR process
    # per macro. This raises before anything is uploaded, so a stage that cannot be isolated is never
    # submitted by accident.
    plan = plan_pipeline(pipeline)
    macro_shas = {name: hash_file(Path('macros') / name) for name in pipeline['macro_hashes']}
    for name, digest in pipeline['macro_hashes'].items():
        if macro_shas[name] != digest:
            raise ValueError('Macro differs from the pipeline pin: ' + name)
    input_shas = {}
    for step in steps:
        digest = hash_file(step)
        if pipeline['input_hashes'].get(step.name) != digest:
            raise ValueError('Input differs from the pipeline pin: ' + step.name)
        input_shas[step.name] = digest
    if set(input_shas) != set(pipeline['input_hashes']):
        raise ValueError('Not every pinned pipeline input was supplied')
    runner_sha = hash_file(Path('scripts/security') / PIPELINE_RUNNER)
    controller_sha = hash_file(Path(__file__))
    pipeline_sha = hash_file(pipeline_path)
    check_review(evidence, macro_shas, input_shas, runner_sha, controller_sha, pipeline_sha)
    # Blocker B2: validate the numeric requirements up front. A spec such as 'x::deg>=-180' parses and
    # would pass trivially, and a mistyped spec used to fail only after the whole STAR stage had run.
    for stage in pipeline['stages']:
        for spec in stage.get('require_numeric', []) or []:
            match = NUMERIC_RE.match(str(spec))
            if not match:
                raise ValueError('Unparseable require_numeric spec: ' + str(spec))
            if float(match.group(4)) < 0:
                raise ValueError('require_numeric bound must not be negative: ' + str(spec))

    check = b.ps("""[pscustomobject]@{
      tasks=@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'CodexCFD*' -and $_.State -eq 'Running'} | Select-Object -ExpandProperty TaskName);
      star=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^star(ccm|[-.])'} | Select-Object -ExpandProperty ProcessId)
    } | ConvertTo-Json""")
    if check['tasks'] or check['star']:
        raise ValueError('Windows CFD execution slot is occupied')

    from resource_contract import validate_pipeline_resources
    resource_contract = validate_pipeline_resources(pipeline)
    memory = pipeline.get('memory_gb', 32)
    cores = pipeline.get('logical_cores', 8)
    if (isinstance(memory, bool) or not isinstance(memory, (int, float)) or not 1 <= memory <= 240 or
            isinstance(cores, bool) or not isinstance(cores, int) or not 1 <= cores <= 64):
        raise ValueError('Invalid reviewed per-request resource cap')
    job = ROOT + '\\' + trial_id
    b.ps(f"""if(Test-Path -LiteralPath {ps_quote(job)}){{throw 'Trial id already exists'}}
    New-Item -ItemType Directory -Path {ps_quote(job)} | Out-Null""")
    # The runner lives in the restricted root and is invoked from there; the stage
    # macros and the input geometry must sit inside the job directory, which is where
    # the runner hashes them and where STAR is launched with them.
    root_uploads = [Path('scripts/security') / PIPELINE_RUNNER,
                    Path('scripts/security/provision_restricted_worker.ps1'),
                    Path('scripts/security/grant_batch_logon.ps1')]
    for local in root_uploads:
        b.scp(local, ROOT + '\\' + local.name)
    for name in macro_shas:
        b.scp(Path('macros') / name, job + '\\' + name)
    for step in steps:
        b.scp(step, job + '\\' + step.name)
    verify = {name: digest for name, digest in macro_shas.items()}
    verify.update(input_shas)
    remote = b.ps('$o=@{}; foreach($n in @(' +
                  ','.join(ps_quote(n) for n in verify) + ')){' +
                  '$o[$n]=(Get-FileHash -LiteralPath (Join-Path ' + ps_quote(job) +
                  ' $n) -Algorithm SHA256).Hash.ToLowerInvariant()}; $o | ConvertTo-Json')
    if not isinstance(remote, dict):
        raise ValueError('Uploaded artefact verification returned no result')
    for name, digest in verify.items():
        if remote.get(name) != digest:
            raise ValueError('Uploaded artefact hash mismatch: ' + name)
    runner_remote = b.ps('(Get-FileHash -LiteralPath ' + ps_quote(ROOT + '\\' + PIPELINE_RUNNER) +
                         ' -Algorithm SHA256).Hash.ToLowerInvariant() | ConvertTo-Json')
    if runner_remote != runner_sha:
        raise ValueError('Uploaded runner hash mismatch')

    c = json.loads(TEMPLATE.read_text())
    c.update(job_dir=job, probe_script=ROOT + '\\' + PIPELINE_RUNNER,
             stages=pipeline['stages'], env=pipeline['env'],
             macro_hashes=macro_shas, input_hashes=input_shas,
             logical_cores=pipeline.get('logical_cores', 8), memory_gb=pipeline.get('memory_gb', 32),
             stage_timeout_seconds=pipeline.get('stage_timeout_seconds', 300),
             # NO WALL-CLOCK CAP (user revoked every time budget; execution layer landed in this
             # round). The declared sum is recorded as an observation; the runner no longer refuses or
             # kills on it, so clamping it to 1800 would only hide what the pipeline declared.
             timeout_seconds=sum(stage.get('timeout_seconds',
                                 pipeline.get('stage_timeout_seconds', 300))
                                 for stage in pipeline['stages']) + 60)
    if resource_contract is not None:
        c['resource_contract'] = resource_contract
        c['timeout_seconds'] = pipeline['task_budget_seconds']
    # The runner ignores these two keys; they exist so that the OFFLINE report generator can name the
    # run without inferring the case from the evidence path (round 11 generalisation). Additive only:
    # no gate, no threshold and no run behaviour depends on them.
    c['reviewed_artifacts'] = {'runner_sha256': runner_sha, 'pipeline_sha256': pipeline_sha,
                               'support_hashes': support_hashes()}
    c['pipeline_id'] = pipeline.get('pipeline_id')
    c['case_label'] = pipeline.get('case_label') or c['pipeline_id']
    for key in ('input_sim', 'source_sha256', 'audit_macro', 'macro_sha256',
                'preflight_macro', 'preflight_sha256', 'environment_proof', 'case_proof'):
        c.pop(key, None)
    evidence.mkdir(parents=True, exist_ok=True)
    config = evidence / (trial_id + '-config.json')
    config.write_text(json.dumps(c, ensure_ascii=False, indent=2))
    remote_config = ROOT + '\\' + trial_id + '-config.json'
    b.scp(config, remote_config)
    b.scp(pipeline_path, job + r'\reviewed-pipeline.json')
    # Verify every bootstrap executable and config before executing privileged provisioning.
    bootstrap = {ROOT + '\\' + local.name: hash_file(local) for local in root_uploads}
    bootstrap[remote_config] = hash_file(config)
    bootstrap[job + r'\reviewed-pipeline.json'] = pipeline_sha
    for remote_path, expected_sha in bootstrap.items():
        b.ps('if((Get-FileHash -LiteralPath ' + ps_quote(remote_path) +
             " -Algorithm SHA256).Hash.ToLowerInvariant() -ne " + ps_quote(expected_sha) +
             "){throw 'Bootstrap upload hash mismatch'}; $true | ConvertTo-Json")
    result = b.ps('& ' + ps_quote(ROOT + r'\provision_restricted_worker.ps1') +
                  ' -ConfigPath ' + ps_quote(remote_config) +
                  ' -ExpectedConfigSha256 ' + ps_quote(hash_file(config)))
    return {'trial_id': trial_id, 'pipeline': pipeline['pipeline_id'],
            'macro_hashes': macro_shas, 'input_hashes': input_shas, 'job_dir': job,
            'macro_plan': plan['stages'],
            'runner_sha256': runner_sha, 'controller_sha256': controller_sha,
            'pipeline_sha256': pipeline_sha, 'support_hashes': support_hashes(),
            'provision': result, 'production_executable': False,
            'engineering_verified': False}


TERMINAL = {'AIR3B_PIPELINE_COMPLETE', 'FAILED'}
DOWNLOADS = ('.json', '.log', '.tsv', '.csv')


NUMERIC_RE = re.compile(r'^([^:]+)::([A-Za-z0-9_.]+)(>=|<=)(-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)$')


def numeric_gate(evidence: Path, trial_id: str, trial_dir: Path) -> dict:
    """Evaluate the pipeline's numeric requirements against the DOWNLOADED artefacts.

    The safety runner can only do substring tests, which are the wrong instrument for a floating-point
    quantity: the previous A5 continuation reached 189.9999999999996 degrees against a
    '"degrees_after_total":180' substring pin and was reported as a stage failure even though the run
    itself succeeded. A substring is simultaneously too strict (it fails at 189.999...) and too weak
    (it also accepts 1800 or 180.5). This gate is the precise replacement: it parses the artefact and
    compares the number, so it is strictly stronger than the pin it replaces and never loosens anything.

    Spec form, one per entry in a stage's `require_numeric`: 'file.json::key>=bound'.
    """
    cfg_path = evidence / (trial_id + '-config.json')
    if not cfg_path.exists():
        # Blocker B2: a missing config used to mean "no gate", which silently skipped the magnitude
        # check. It is now an explicit failure: the config is written by start() before the trial, so
        # its absence means the check cannot be performed, which is not the same as passing.
        return {'checks': [], 'passed': False,
                'failed': [{'spec': None, 'reason': 'missing trial config ' + cfg_path.name}]}
    def refused(reasons):
        return {'checks': [], 'passed': False,
                'failed': [{'spec': None, 'reason': 'the run did not succeed, so no numeric gate applies: '
                            + '; '.join(reasons[:6])}]}
    try:
        config = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
        stages = config.get('stages') if isinstance(config, dict) else None
        if not isinstance(stages, list) or not stages:
            return refused(['missing or invalid declared stages'])
        declared = []
        for stage in stages:
            name = stage.get('name') if isinstance(stage, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                return refused(['invalid declared stage name'])
            if name in declared:
                return refused(['duplicate declared stage ' + name])
            declared.append(name)
    except Exception as exc:
        return refused(['unreadable trial config: ' + str(exc)])
    # Exact stage inventory is mandatory; an empty glob cannot certify a successful run.
    state_reasons = []
    if trial_dir.resolve() != (evidence / trial_id).resolve():
        state_reasons.append('artefact directory differs from declared trial directory')
    expected = {'stage-' + name + '.json' for name in declared}
    actual = {path.name for path in trial_dir.glob('stage-*.json')}
    for filename in sorted(expected - actual):
        state_reasons.append('missing declared stage record ' + filename)
    for filename in sorted(actual - expected):
        state_reasons.append('undeclared stage record ' + filename)
    for name in declared:
        path = trial_dir / ('stage-' + name + '.json')
        if path.name not in actual:
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(record, dict) or record.get('stage') != name or record.get('state') != 'ok':
                state_reasons.append('invalid identity/state for declared stage ' + name)
        except Exception as exc:
            state_reasons.append('unreadable stage ' + name + ': ' + str(exc))
    result_path = evidence / (trial_id + '-result.json')
    try:
        result = json.loads(result_path.read_text(encoding='utf-8-sig'))
        if not isinstance(result, dict) or result.get('state') != 'AIR3B_PIPELINE_COMPLETE':
            state_reasons.append('runner result is not AIR3B_PIPELINE_COMPLETE')
    except Exception as exc:
        state_reasons.append('missing/unreadable runner result: ' + str(exc))
    # poll downloads the original runner record. If present it must agree with the result;
    # a previous controller GATE_FAILED record is never accepted as a successful runner state.
    probe_path = trial_dir / 'case-probe.json'
    if probe_path.exists():
        try:
            probe = json.loads(probe_path.read_text(encoding='utf-8-sig'))
            if not isinstance(probe, dict) or probe.get('state') != 'AIR3B_PIPELINE_COMPLETE':
                state_reasons.append('original case-probe state is not AIR3B_PIPELINE_COMPLETE')
        except Exception as exc:
            state_reasons.append('unreadable original case-probe: ' + str(exc))
    if state_reasons:
        return refused(state_reasons)
    if not any(stage.get('require_numeric') for stage in stages):
        return {}   # Valid successful run, but no numeric requirement declared.
    results = []
    for stage in stages:
        for spec in stage.get('require_numeric', []) or []:
            match = NUMERIC_RE.match(str(spec))
            if not match:
                results.append({'spec': spec, 'ok': False, 'reason': 'unparseable numeric spec'})
                continue
            name, key, op, raw = match.groups()
            target = trial_dir / name
            if not target.exists():
                results.append({'spec': spec, 'ok': False, 'reason': 'missing artefact ' + name})
                continue
            try:
                value = json.loads(target.read_text(encoding='utf-8'))[key]
            except Exception as exc:
                results.append({'spec': spec, 'ok': False, 'reason': 'unreadable: ' + str(exc)})
                continue
            bound = float(raw)
            # Blockers B1/B2 from the independent review of 2026-10-02: a non-finite or type-coerced
            # value must never certify a result. float('Infinity') >= 180 is True, float('1000') is
            # 1000.0, and float(True) is 1.0, so without this guard a non-physical artefact could
            # satisfy a revolution gate. Only a real int/float (never bool) that is finite counts.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                results.append({'spec': spec, 'ok': False,
                                'reason': 'value is not a JSON number: ' + repr(value)})
                continue
            if not math.isfinite(value):
                results.append({'spec': spec, 'ok': False,
                                'reason': 'value is not finite: ' + repr(value)})
                continue
            ok = value >= bound if op == '>=' else value <= bound
            results.append({'stage': stage.get('name'), 'spec': spec, 'key': key, 'value': value,
                            'op': op, 'bound': bound, 'ok': bool(ok)})
    failed = [entry for entry in results if not entry['ok']]
    return {'checks': results, 'passed': not failed, 'failed': failed}


def poll(b: Backend, trial_id: str, evidence: Path) -> dict:
    if not re.fullmatch(r'[a-z0-9-]{8,64}', trial_id):
        raise ValueError('Invalid trial id')
    job = ROOT + '\\' + trial_id
    r = b.ps(f"""$p=Join-Path {ps_quote(job)} 'case-probe.json'
    if(Test-Path -LiteralPath $p){{Get-Content -LiteralPath $p -Raw -Encoding UTF8}}else{{
    $task=Get-ScheduledTask -TaskName CodexCFD-RestrictedProbe -ErrorAction SilentlyContinue
    $state=if($task){{$task.State.ToString()}}else{{'NOT_REGISTERED'}}
    $last=if($task){{(Get-ScheduledTaskInfo -TaskName CodexCFD-RestrictedProbe).LastTaskResult}}else{{$null}}
    $info=if($task){{Get-ScheduledTaskInfo -TaskName CodexCFD-RestrictedProbe}}else{{$null}}
    $started=if($info -and $info.LastRunTime -and $info.LastRunTime.Year -gt 1900){{$info.LastRunTime}}else{{$null}}
    $age=if($started){{([DateTime]::Now-$started).TotalSeconds}}else{{0}}
    $over=if($started){{$age -gt 1860}}else{{$false}}
    if($state -ne 'Running' -and $over){{
      [pscustomobject]@{{state='FAILED';error='Scheduled task ended without a result record';task_state=$state;last_result=$last}} | ConvertTo-Json
    }}else{{
      [pscustomobject]@{{state='PENDING_RESULT';task_state=$state;last_result=$last;age_seconds=[math]::Round($age,1)}} | ConvertTo-Json
    }}
    }}
    """)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / (trial_id + '-result.json')).write_text(
        json.dumps(r, ensure_ascii=False, indent=2))
    if r.get('state') in TERMINAL:
        names = b.ps('@(Get-ChildItem -LiteralPath ' + ps_quote(job) +
                     ' -File | Where-Object {$_.Extension -in ' +
                     '".json",".log",".tsv",".csv"} | Select-Object -ExpandProperty Name) | ConvertTo-Json')
        if isinstance(names, str):
            names = [names]
        for name in names or []:
            d = evidence / trial_id
            d.mkdir(exist_ok=True)
            b.scp(d / name, job + '\\' + name, download=True)
        gate = numeric_gate(evidence, trial_id, evidence / trial_id)
        if gate:
            r['numeric_gate'] = gate
            r['gate_passed'] = gate['passed']
            if not gate['passed']:
                # Blocker B3: the runner's own state is 'AIR3B_PIPELINE_COMPLETE', which a consumer
                # reading only `state` would file as a success. Downgrade the persisted record itself,
                # not just the raised error, so a gate failure cannot be mistaken for a passing run.
                r['runner_state'] = r.get('state')
                r['state'] = 'GATE_FAILED'
                r['engineering_verified'] = False
            (evidence / (trial_id + '-result.json')).write_text(
                json.dumps(r, ensure_ascii=False, indent=2))
            if not gate['passed']:
                raise ValueError('Numeric gate failed: ' + json.dumps(gate['failed'], ensure_ascii=False))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=('start', 'poll'))
    ap.add_argument('--trial-id', required=True)
    ap.add_argument('--pipeline', type=Path)
    ap.add_argument('--step', type=Path, action='append')
    ap.add_argument('--evidence', type=Path, required=True)
    a = ap.parse_args()
    b = Backend()
    if a.command == 'start':
        if not a.pipeline or not a.step:
            raise SystemExit('--pipeline and --step are required for start')
        print(json.dumps(start(b, a.trial_id, a.pipeline, a.step, a.evidence),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(poll(b, a.trial_id, a.evidence), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
