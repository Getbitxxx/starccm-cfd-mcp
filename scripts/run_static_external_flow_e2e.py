# -*- coding: utf-8 -*-
"""One entry command for a reviewed STAR pipeline: submit it, poll it, fetch its checkpoints, report.

    python scripts/run_static_external_flow_e2e.py \
        --trial-id s1-static-sphere-e2e-20261003a \
        --evidence test-evidence/static-external-flow/round10-e2e

    # any other reviewed pipeline + input list, same command (round 11 generalisation):
    python scripts/run_static_external_flow_e2e.py \
        --pipeline cases/static-sphere-rename-variant/pipeline-e2e.json \
        --inputs   cases/static-sphere-rename-variant/e2e-inputs.json \
        --trial-id s1-static-sphere-rename-20261003a \
        --evidence test-evidence/static-external-flow/round11-rename

    # resume ONLY the checkpoint transfer of a run that already finished, without touching STAR:
    python scripts/run_static_external_flow_e2e.py \
        --retrieve-only --trial-id <id> --evidence <dir>

The defaults still point at the round-10 baseline, so the original invocation is unchanged. Nothing in
this script -- or in the report generator it calls -- may assume WHICH case is running: the pipeline
path, the input list path, the trial id and the evidence directory are all parameters, and the case
label shown in the report comes from the pipeline JSON recorded into the trial config.

Why a driver exists at all: the round-9 state was "five stages, each of which ran", wired together by
hand between two submissions. Track P is about ONE automatic run, so the hand-off has to be a command,
not a habit. This script does exactly these things and invents no policy:

  1. resolve the reviewed pipeline + its pinned inputs from `cases/.../`;
  2. call `scripts/air3b_pipeline_trial.py::start` (which plans the stage chain, checks the review record
     against every pinned hash and refuses to start if the Windows execution slot is occupied);
  3. poll until the runner reaches a terminal state (`poll` downloads the small records itself) and
     surface the controller's numeric-gate verdict, which is written by the poller;
  4. **fetch the .sim checkpoints the run actually produced** and verify each one against the sha256 the
     Windows runner recorded for it (round 12). This is the step round 11 left to a human: `poll` only
     transfers .json/.log/.tsv/.csv, so every .sim had to be copied by hand. Which files are wanted is
     read from the runner's OWN records (`case-probe.json` and `stage-<name>.json`), never from a
     hard-coded name: a one-stage build-body run retrieves its own `static_body_geometry.sim` and the
     six-stage pipeline retrieves its final output whatever the pipeline decided to call it. A transfer
     failure or a hash mismatch is reported as a FAILED DELIVERY and is never papered over by "the run
     finished"; whether the SOLVE completed and whether the DOWNLOAD succeeded are two separate fields.
     Re-running with `--retrieve-only` retries only what is missing or wrong, so a dropped transfer
     never costs another STAR run.
  5. call `scripts/generate_run_report.py` so the Chinese report, the evidence index and the report
     hashes are produced by the same command. The report reads the delivery record and cannot claim a
     `.sim` was delivered when it was not.

Exit status:
    0  the report was written, the run reached a terminal state and every expected checkpoint was
       retrieved and hash-verified (REPORT_OK, or a report that honestly says the run failed or is
       incomplete -- an honest negative verdict is a pass for track P per AGENTS.md section 1, and
       turning it into a non-zero exit here would be the false failure the rule exists to prevent).
       With --strict, 2 instead when the report's verdict is not REPORT_OK.
    1  the trial could not be started, or report generation itself failed.
    3  polling gave up before the runner reached a terminal state: the run is still going on the worker,
       no verdict exists yet, and pretending otherwise would be the failure this script exists to avoid.
       The round-10 independent review found that the first version returned 0 here, which contradicted
       its own documentation.
    4  the run is over but a checkpoint could not be delivered: a transfer failed, a hash did not match
       the runner's record, or two records disagree about a file's hash. The report is still written and
       still says exactly that. This code exists so a shell pipeline cannot mistake "STAR finished" for
       "the deliverable was retrieved".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import re
import csv
import math
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The round-10 baseline. Kept as the default so the original command still works; every one of these is
# overridable on the command line.
DEFAULT_PIPELINE = ROOT / 'cases' / 'static-sphere-external-flow' / 'pipeline-e2e-geometry-to-validation.json'
DEFAULT_INPUTS = ROOT / 'cases' / 'static-sphere-external-flow' / 'e2e-inputs.json'
REPORT_SCRIPT = ROOT / 'scripts' / 'generate_run_report.py'

# Delivery states. "already_present" is not a weaker "verified": a local file counts as present only when
# its sha256 equals the runner's record, which is the same test a fresh transfer has to pass. That is what
# makes `--retrieve-only` a resume instead of a second, differently-judged path.
VERIFIED_STATES = ('verified', 'already_present')
FAILED_STATES = ('download_failed', 'hash_mismatch', 'recording_conflict', 'unverifiable')


def resolve_inputs(inputs_path: Path) -> list[Path]:
    """The reviewed inputs, by path. Their bytes are checked against the pipeline pins by the controller.

    The list is a file of repository-relative paths, so a new case adds a list instead of editing this
    script. The controller still compares every path's sha256 against the pipeline's own input_hashes,
    so a list that disagrees with the pipeline is refused before anything is uploaded.
    """
    mapping = json.loads(inputs_path.read_text(encoding='utf-8'))
    return [ROOT / rel for rel in mapping['inputs']]


def pipeline_stages(pipeline_path: Path) -> list[dict]:
    return json.loads(pipeline_path.read_text(encoding='utf-8'))['stages']


def declared_seconds(pipeline_path: Path) -> int:
    return sum(stage['timeout_seconds'] for stage in pipeline_stages(pipeline_path))


def sha256(path: Path) -> str:
    # Streamed, deliberately: a .sim checkpoint is tens of megabytes and Python 3.9 on the development
    # machine has no hashlib.file_digest. This is the same digest the Windows runner records, computed
    # from the transferred bytes rather than from the file's size or its name.
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    """utf-8-sig: the Windows runner writes stage records with a BOM (measured on stage-solve.json)."""
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:                                               # noqa: BLE001
        return None


def run_stage_order(cfg, case_probe, trial_dir: Path) -> list[str]:
    """The order stages actually ran in, from the records themselves (never a hard-coded list).

    The submitted config is the run's own declaration of its stage order; the runner's `case-probe.json`
    is what it actually completed; the stage records are the fallback for a run that failed before the
    probe was written. All three are read; none is assumed to exist.
    """
    order = [stage.get('name') for stage in ((cfg or {}).get('stages') or [])
             if isinstance(stage, dict) and stage.get('name')]
    if order:
        return order
    order = [stage.get('name') for stage in ((case_probe or {}).get('stages') or [])
             if isinstance(stage, dict) and stage.get('name')]
    if order:
        return order
    return [path.name[len('stage-'):-len('.json')] for path in sorted(trial_dir.glob('stage-*.json'))]


def declared_checkpoints(trial_dir: Path, cfg, case_probe) -> dict:
    """Every .sim the runner's own records declare, with the sha256 the runner computed for it.

    Sources, all read-only and all inside this trial's evidence directory:
      * `case-probe.json`      the runner's final record: one entry per stage, output + output_sha256;
      * `stage-<name>.json`    the per-stage record: stage output plus one entry per macro, so a stage
                               that runs several macros still declares each macro's own file.

    Two records disagreeing about one file's hash is recorded as a conflict rather than resolved by
    picking a favourite: the point of the check is to compare the transferred bytes against the byte
    identity the runner recorded, and a genuine disagreement means the identity is not established.
    """
    found: dict = {}

    def add(name, digest, source: str, stage, stage_state, kind: str) -> None:
        if not isinstance(name, str) or not name or not name.lower().endswith('.sim'):
            return
        entry = found.setdefault(name, {
            'name': name, 'recorded_sha256': None, 'sources': [], 'stages': [],
            'stage_output_declared': False, 'ok_declared': False,
            'unhashed_sources': [], 'conflicts': []})
        entry['sources'].append(source)
        entry['stages'].append({'stage': stage, 'state': stage_state, 'kind': kind})
        if kind == 'stage_output':
            entry['stage_output_declared'] = True
        if digest in (None, ''):
            entry['unhashed_sources'].append(source)
        elif entry['recorded_sha256'] is None:
            entry['recorded_sha256'] = digest
        elif entry['recorded_sha256'] != digest:
            entry['conflicts'].append({'source': source, 'sha256': digest})
        if str(stage_state) == 'ok':
            entry['ok_declared'] = True

    for stage in (case_probe or {}).get('stages') or []:
        if isinstance(stage, dict):
            state = 'ok' if str(stage.get('status', '')).upper() == 'OK' else str(stage.get('status'))
            add(stage.get('output'), stage.get('output_sha256'), 'case-probe.json',
                stage.get('name'), state, 'stage_output')
    for path in sorted(trial_dir.glob('stage-*.json')):
        record = read_json(path) or {}
        stage = record.get('stage') or path.name[len('stage-'):-len('.json')]
        state = record.get('state')
        add(record.get('output'), record.get('output_sha256'), path.name, stage, state, 'stage_output')
        for macro in record.get('macros') or []:
            if isinstance(macro, dict):
                add(macro.get('output'), macro.get('output_sha256'),
                    '%s[%s]' % (path.name, macro.get('macro')), stage, state, 'macro_output')
    return found


def final_checkpoint(order: list[str], found: dict):
    """Which .sim the run was supposed to hand over: the LAST stage output that actually ran ok.

    This is the round-12 answer to "do not assume the output is called external_flow_solved.sim". The
    name comes from the record of the stage that produced it, in the run's own stage order; a run whose
    final stage is read-only (the validation stage declares no output) still resolves to the solve
    stage's checkpoint, and a one-stage build-body run resolves to its own geometry file.
    """
    last = None
    for stage in order:
        for name, entry in found.items():
            if not entry['stage_output_declared']:
                continue
            if any(s['stage'] == stage and s['state'] == 'ok' and s['kind'] == 'stage_output'
                   for s in entry['stages']):
                last = name
    return last


def retrieve_checkpoints(backend, trial, trial_id: str, evidence: Path, result: dict) -> dict:
    """Transfer the run's .sim products through the existing Backend and verify every byte.

    Solve state and download state are recorded separately on purpose:
      * `solve_completed`  the Windows runner reached AIR3B_PIPELINE_COMPLETE;
      * `numeric_gate_passed`  the controller's magnitude gate agreed (None when no gate was declared);
      * `download_succeeded`  every checkpoint the runner recorded a hash for is now local AND its
        sha256 matches that record;
      * `checkpoint_delivered`  the final stage output itself is one of those verified files.
    A run that failed in stage 3 has no final checkpoint and `checkpoint_delivered` is false, but
    `download_succeeded` can still be true for the products the earlier stages really made -- that is
    the honest distinction, and it is why this function does not collapse the two into one boolean.
    """
    trial_dir = evidence / trial_id
    evidence.mkdir(parents=True, exist_ok=True)
    cfg = read_json(evidence / (trial_id + '-config.json')) or {}
    case_probe = read_json(trial_dir / 'case-probe.json')
    found = declared_checkpoints(trial_dir, cfg, case_probe)
    order = run_stage_order(cfg, case_probe, trial_dir)
    # With no runner record at all there is nothing to identify a product FROM. Reporting success here
    # would be the "retrieved nothing, claimed everything" shape, so it is an explicit failure: the
    # transfer step could not even establish what was supposed to arrive.
    runner_records_present = bool(cfg) or bool(case_probe) or bool(list(trial_dir.glob('stage-*.json')))
    remote_dir = cfg.get('job_dir') or (trial.ROOT + '\\' + trial_id)
    runner_state = (result or {}).get('runner_state') or (result or {}).get('state')
    numeric_gate = result.get('numeric_gate') if isinstance(result, dict) else None

    artifacts = []
    for name in sorted(found):
        entry = found[name]
        local = trial_dir / name
        record = {
            'name': name,
            'recorded_sha256': entry['recorded_sha256'],
            'declared_by': entry['sources'],
            'declared_stages': entry['stages'],
            'stage_output_declared': entry['stage_output_declared'],
            'declared_by_ok_stage': entry['ok_declared'],
            'local_path': str(local),
            'resumed_from_local_copy': False,
            'downloaded': False,
            'local_sha256': None,
            'bytes': None,
            'status': None,
            'error': None,
        }
        if entry['conflicts']:
            record['status'] = 'recording_conflict'
            record['error'] = ('runner records disagree about the sha256 of %s: %s'
                               % (name, json.dumps(entry['conflicts'], ensure_ascii=False)))
            artifacts.append(record)
            continue
        if entry['unhashed_sources']:
            # A file the runner never hashed cannot be verified after transfer, so claiming delivery
            # would be exactly the unsupported claim this check exists to prevent.
            record['status'] = 'unverifiable'
            record['error'] = ('the runner recorded no sha256 for %s (declared by %s)'
                               % (name, ', '.join(entry['unhashed_sources'])))
            artifacts.append(record)
            continue
        recorded = entry['recorded_sha256']
        if local.is_file() and sha256(local) == recorded:
            record['resumed_from_local_copy'] = True
            record['local_sha256'] = recorded
            record['bytes'] = local.stat().st_size
            record['status'] = 'already_present'
            artifacts.append(record)
            continue
        try:
            backend.scp(local, remote_dir + '\\' + name, download=True)
            record['downloaded'] = True
        except Exception as exc:                                    # noqa: BLE001 - recorded, not hidden
            record['status'] = 'download_failed'
            record['error'] = '%s: %s' % (type(exc).__name__, exc)
            artifacts.append(record)
            continue
        if not local.is_file():
            record['status'] = 'download_failed'
            record['error'] = 'transfer reported success but no local file exists'
            artifacts.append(record)
            continue
        actual = sha256(local)
        record['local_sha256'] = actual
        record['bytes'] = local.stat().st_size
        if actual == recorded:
            record['status'] = 'verified'
        else:
            record['status'] = 'hash_mismatch'
            record['error'] = 'sha256 %s does not match the runner record %s' % (actual, recorded)
        artifacts.append(record)

    # What counts as an expected deliverable: anything the runner actually hashed (so it existed), plus
    # anything a successful stage declared even if the runner failed to hash it, plus a hash conflict.
    # A file merely named by a stage that never ran is NOT an expected deliverable, which is what keeps a
    # legitimately failed run from also being reported as a delivery failure.
    def is_expected(record) -> bool:
        return (record['recorded_sha256'] is not None or record['declared_by_ok_stage']
                or record['status'] == 'recording_conflict')

    expected = [record for record in artifacts if is_expected(record)]
    failures = [record for record in expected if record['status'] in FAILED_STATES]
    failed_names = {record['name'] for record in failures}
    final_name = final_checkpoint(order, found)
    final_record = next((record for record in artifacts if record['name'] == final_name), None)
    download_succeeded = not failures and runner_records_present
    checkpoint_delivered = bool(final_record and final_record['status'] in VERIFIED_STATES
                                and final_record['name'] not in failed_names)

    summary = {
        'schema_version': 1,
        'trial_id': trial_id,
        'pipeline_id': cfg.get('pipeline_id'),
        'job_dir': remote_dir,
        'stage_order': order,
        'solve_state': runner_state,
        'solve_completed': runner_state == 'AIR3B_PIPELINE_COMPLETE',
        'numeric_gate_passed': (numeric_gate or {}).get('passed') if numeric_gate else None,
        'runner_records_present': runner_records_present,
        'download_succeeded': download_succeeded,
        'checkpoint_delivered': checkpoint_delivered,
        'final_sim': ({'name': final_record['name'], 'sha256': final_record['local_sha256'],
                       'path': final_record['local_path'], 'status': final_record['status']}
                      if final_record else None),
        'expected_artifacts': [record['name'] for record in expected],
        'artifacts': artifacts,
        'failures': failures,
        '_note': ('`solve_completed` is the Windows runner reaching AIR3B_PIPELINE_COMPLETE; '
                  '`download_succeeded` is every runner-hashed .sim being present locally with a matching '
                  'sha256. They are deliberately different fields: a finished solve whose transfer '
                  'failed must not read as a delivered checkpoint, and a failed solve whose earlier '
                  'stages really did produce files must not read as a transfer failure. '
                  '`--retrieve-only` retries only the transfer.'),
    }
    (evidence / (trial_id + '-checkpoints.json')).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def poll_until_terminal(trial, backend, trial_id: str, evidence: Path,
                        timeout_s: int) -> tuple[dict, bool]:
    """Return (last poll result, reached_terminal). The flag is what the exit status keys on."""
    started = time.time()
    while True:
        try:
            result = trial.poll(backend, trial_id, evidence)
        except ValueError as exc:
            # The poller raises only for a failed numeric gate, and it has ALREADY downgraded the
            # persisted record to GATE_FAILED before raising. Surface it and keep the evidence. That is
            # a terminal state: the run finished and the gate said no.
            print('numeric gate: %s' % exc, file=sys.stderr)
            return json.loads((evidence / (trial_id + '-result.json')).read_text(encoding='utf-8-sig')), True
        if result.get('state') in trial.TERMINAL or result.get('state') in ('GATE_FAILED', 'FAILED'):
            return result, True
        if time.time() - started > timeout_s:
            print('poll timeout after %d s; the run continues on the worker' % timeout_s,
                  file=sys.stderr)
            return result, False
        time.sleep(15)


def load_result(evidence: Path, trial_id: str) -> dict:
    """The persisted result record, for the resume path that skips submission and polling."""
    path = evidence / (trial_id + '-result.json')
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8-sig'))


def retrieve_result_exports(backend, trial_id: str, evidence: Path) -> dict | None:
    """Deliver a results-only export's actual files; success certification stays in run_record_gate.

    PNGs are outside the frozen trial poller's download extensions. The macro's hashed list is
    authoritative; use the existing transport, keep each mismatch visible, and never resubmit STAR.
    """
    import run_record_gate as gate
    trial_dir = evidence / trial_id
    record_path = trial_dir / 'external_flow_export.json'
    if not record_path.is_file():
        record_path = trial_dir / 'property_nearwall_readback.json'
    if not record_path.is_file():
        return None
    citation = gate.citation_status(record_path)
    summary = {'trial_id': trial_id, 'citation': citation, 'artifacts': [], 'failures': [],
               'download_succeeded': False, 'derived_curves': []}
    if citation['state'] != gate.CITABLE:
        summary['failures'].append({'name': record_path.name, 'status': citation['state']})
    else:
        record = read_json(record_path)
        cfg = read_json(evidence / (trial_id + '-config.json')) or {}
        remote = cfg.get('job_dir')
        for item in record.get('artifacts', []):
            name, digest = item.get('name'), item.get('sha256')
            result = dict(item, status='unverifiable', local_sha256=None)
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(png|csv)', name) or not re.fullmatch(r'[a-f0-9]{64}', str(digest)) or not remote:
                result['error'] = 'Unsafe filename, missing digest or job directory'
            else:
                local = trial_dir / name
                try:
                    if not local.is_file() or sha256(local) != digest:
                        backend.scp(local, remote + '\\' + name, download=True)
                    result['local_sha256'] = sha256(local)
                    result['local_path'] = str(local.resolve())
                    result['status'] = 'verified' if result['local_sha256'] == digest and local.stat().st_size == item.get('bytes') else 'hash_mismatch'
                except Exception as exc:
                    result.update(status='download_failed', error=str(exc))
            summary['artifacts'].append(result)
            if result['status'] != 'verified':
                summary['failures'].append(result)
        summary['download_succeeded'] = bool(summary['artifacts']) and not summary['failures']
        if summary['download_succeeded']:
            summary['derived_curves'] = export_monitor_curves(trial_dir)
    (evidence / (trial_id + '-exports.json')).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def export_monitor_curves(trial_dir: Path) -> list[dict]:
    """SVG plots of exported raw samples. No smoothing, invented samples or convergence decision."""
    outputs = []
    sources = [('checkpoint-monitors.csv', 'residual-curves.svg', True),
               ('load-history.csv', 'load-curves.svg', False)]
    colors = ['#1565c0', '#d84315', '#388e3c', '#6a1b9a', '#00838f', '#ad1457', '#795548']
    for name, target, logarithmic in sources:
        source = trial_dir / name
        if not source.is_file():
            continue
        groups = {}
        for row in csv.DictReader(source.open(encoding='utf-8-sig')):
            if logarithmic and row.get('kind') != 'residual':
                continue
            xkey = 'iteration'
            ykey = 'value' if logarithmic else 'drag_x_n'
            # The solve history's schema is inherited from the source run, not reconstructed.
            if ykey not in row and not logarithmic:
                ykey = 'measured_drag_x_n'
            try:
                x, y = float(row[xkey]), float(row[ykey])
            except (KeyError, ValueError):
                continue
            if not math.isfinite(x) or not math.isfinite(y) or (logarithmic and y <= 0):
                continue
            groups.setdefault(row.get('name', 'Drag X [N]'), []).append((x, math.log10(y) if logarithmic else y))
        points = [v for series in groups.values() for v in series]
        if not points:
            continue
        xmin, xmax = min(x for x,y in points), max(x for x,y in points)
        ymin, ymax = min(y for x,y in points), max(y for x,y in points)
        dx, dy = xmax-xmin or 1, ymax-ymin or 1
        ymax += dy*0.05; ymin -= dy*0.05; dy=ymax-ymin
        elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="650" viewBox="0 0 1100 650">',
                    '<rect width="1100" height="650" fill="white"/>',
                    '<g font-family="sans-serif" font-size="16" fill="#222">',
                    '<text x="90" y="30">Numerical checkpoint monitor samples — no experimental validation</text>',
                    '<path d="M90 65 V560 H870" fill="none" stroke="#555"/>',
                    '<text x="380" y="615">Iteration</text>',
                    '<text x="15" y="50">%s</text>' % ('log10(residual)' if logarithmic else 'Force [N]')]
        for tick in range(6):
            fraction=tick/5; yy=560-495*fraction; xx=90+780*fraction
            elements += ['<text x="10" y="%g">%.3g</text>' % (yy,ymin+dy*fraction),
                         '<text x="%g" y="588">%.0f</text>' % (xx,xmin+dx*fraction)]
        for i,(label,series) in enumerate(groups.items()):
            coords=' '.join('%.2f,%.2f' % (90+780*(x-xmin)/dx,560-495*(y-ymin)/dy) for x,y in series)
            color=colors[i%len(colors)]
            elements += ['<polyline points="%s" fill="none" stroke="%s" stroke-width="1.5"/>' % (coords,color),
                         '<text x="885" y="%d" fill="%s">%s</text>' % (95+i*28,color,html.escape(label))]
        elements += ['</g></svg>']
        path=trial_dir/target;path.write_text('\n'.join(elements),encoding='utf-8')
        outputs.append({'name':target,'path':str(path.resolve()),'sha256':sha256(path),
                        'source':name,'source_sha256':sha256(source),'origin':'offline_plot_of_raw_exported_samples'})
    return outputs


def write_report(trial_id: str, evidence: Path, strict: bool,
                 case_label: str | None = None) -> int:
    command = [sys.executable, str(REPORT_SCRIPT), '--trial-id', trial_id,
               '--evidence', str(evidence)]
    if case_label:
        command += ['--case-label', case_label]
    report = subprocess.run(command + (['--strict'] if strict else []),
                            capture_output=True, text=True)
    print(report.stdout)
    if report.returncode not in (0, 2):
        print(report.stderr, file=sys.stderr)
        return 1
    return report.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--trial-id', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--pipeline', type=Path, default=DEFAULT_PIPELINE,
                    help='reviewed pipeline JSON (default: the round-10 scenario-1 baseline)')
    ap.add_argument('--inputs', type=Path, default=DEFAULT_INPUTS,
                    help='JSON file listing the reviewed inputs as repository-relative paths')
    ap.add_argument('--case-label', default=None,
                    help='display name for the report; default comes from the pipeline JSON')
    # OBSERVER TIMEOUT ONLY (user revoked every time budget; execution layer landed in this round).
    # It bounds how long THIS client polls for a result; it never stops the job on the worker, which
    # now runs to the macro's numerical stopping criteria.
    ap.add_argument('--timeout', type=int, default=1800, help='observer-only poll timeout in seconds')
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--report-only', action='store_true',
                    help='skip submission and poll; regenerate the report for an existing trial')
    ap.add_argument('--retrieve-only', action='store_true',
                    help='skip submission and poll; resume ONLY the checkpoint transfer of a finished '
                         'trial (files already verified locally are not transferred again)')
    a = ap.parse_args()
    pipeline = a.pipeline if a.pipeline.is_absolute() else (ROOT / a.pipeline)
    inputs_path = a.inputs if a.inputs.is_absolute() else (ROOT / a.inputs)
    evidence = a.evidence if a.evidence.is_absolute() else (ROOT / a.evidence)
    if a.report_only and not a.retrieve_only:
        # Regenerate the report from evidence already on disk. No backend, no transfer.
        return write_report(a.trial_id, evidence, a.strict, a.case_label)
    sys.path.insert(0, str(ROOT / 'scripts'))
    import air3b_pipeline_trial as trial
    from backend import Backend
    backend = Backend()
    if a.retrieve_only:
        print('retrieve-only: skipping submission and polling; resuming only the checkpoint transfer',
              file=sys.stderr)
    else:
        inputs = resolve_inputs(inputs_path)
        if a.case_label:
            # NOT written into the pipeline: those bytes are exactly what the independent review pinned,
            # and rewriting them would (correctly) make the submission be refused. The label is passed to
            # the report generator instead, which prefers the trial config's own case_label and falls
            # back to the pipeline id.
            print('case label override: %s (display only; the pipeline bytes are unchanged)'
                  % a.case_label, file=sys.stderr)
        summary = trial.start(backend, a.trial_id, pipeline, inputs, evidence)
        print(json.dumps({'started': True, 'trial_id': a.trial_id,
                          'pipeline': str(pipeline), 'inputs': str(inputs_path),
                          'stages': [stage['name'] for stage in summary['macro_plan']],
                          'declared_seconds': declared_seconds(pipeline),
                          'job_dir': summary['job_dir']}, ensure_ascii=False, indent=2))
        result, terminal = poll_until_terminal(trial, backend, a.trial_id, evidence, a.timeout)
        print(json.dumps({'state': result.get('state'), 'gate_passed': result.get('gate_passed'),
                          'runner_state': result.get('runner_state'), 'terminal': terminal},
                         ensure_ascii=False, indent=2))
        if not terminal:
            # A report is still written (it will say the stages it can see are not all there), because a
            # reader needs to know what the run has produced so far. The exit status is the honest part.
            write_report(a.trial_id, evidence, False, a.case_label)
            return 3
    result = load_result(evidence, a.trial_id)
    checkpoints = retrieve_checkpoints(backend, trial, a.trial_id, evidence, result)
    exports = retrieve_result_exports(backend, a.trial_id, evidence)
    print(json.dumps({'trial_id': a.trial_id,
                      'solve_state': checkpoints['solve_state'],
                      'solve_completed': checkpoints['solve_completed'],
                      'download_succeeded': checkpoints['download_succeeded'],
                      'checkpoint_delivered': checkpoints['checkpoint_delivered'],
                      'final_sim': checkpoints['final_sim'],
                      'failures': [record['name'] for record in checkpoints['failures']]},
                     ensure_ascii=False, indent=2))
    report_status = write_report(a.trial_id, evidence, a.strict, a.case_label)
    if not checkpoints['download_succeeded']:
        print('checkpoint delivery failed for: %s'
              % ', '.join(record['name'] for record in checkpoints['failures']), file=sys.stderr)
        return 4
    if exports is not None and not exports['download_succeeded']:
        print('result exports delivery failed; see %s-exports.json' % a.trial_id, file=sys.stderr)
        return 4
    return report_status


if __name__ == '__main__':
    sys.exit(main())
