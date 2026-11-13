"""Round-4 runner: the four serialised STAR jobs, each behind its own approved review record.

Serialised because the repository guarantees one CFD execution slot (the restricted runner and the
durable worker take the same global mutex). Every step is gated: a trial refuses to start unless its
pipeline-review.json pins the current bytes, and the worker job refuses without a matching record.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from backend import Backend, load_review_record, sha256  # noqa: E402

EV = ROOT / 'test-evidence' / 'static-external-flow'
CASES = ROOT / 'cases' / 'static-sphere-external-flow'
Z15_SIM = EV / 'variant-translate-z1p5' / 's1-static-sphere-translate-z1p5-20261003a' / 'static_body_geometry.sim'
TRIAL = ROOT / 'scripts' / 'air3b_pipeline_trial.py'


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT))


def trial(command: str, trial_id: str, pipeline: Path, steps: list[Path], evidence: Path) -> dict:
    args = [sys.executable, str(TRIAL), command, '--trial-id', trial_id,
            '--evidence', str(evidence.relative_to(ROOT))]
    if command == 'start':
        args += ['--pipeline', str(pipeline.relative_to(ROOT))]
        for step in steps:
            args += ['--step', str(step.relative_to(ROOT))]
    result = run(args)
    print('== %s %s rc=%d' % (command, trial_id, result.returncode), flush=True)
    print(result.stdout[-2500:] or result.stderr[-2500:], flush=True)
    return {'rc': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


def poll_until_terminal(trial_id: str, evidence: Path, limit: int = 12) -> dict:
    for _ in range(limit):
        time.sleep(25)
        out = trial('poll', trial_id, Path(), [], evidence)
        text = out['stdout'] + out['stderr']
        if ('AIR3B_PIPELINE_COMPLETE' in text or 'FAILED' in text or 'GATE_FAILED' in text
                or out['rc'] != 0):
            return out
    return out


def main() -> int:
    summary: dict = {}

    # ---- T1: distinguishing position positive, sphere at [5,0,0] m ---------------------------
    ev1 = EV / 'variant-translate-x5'
    trial('start', 's1-static-sphere-translate-x5-20261003a',
          CASES / 'variants' / 'translate_x5' / 'pipeline.json',
          [CASES / 'variants' / 'translate_x5' / 'case_spec.json'], ev1)
    summary['T1'] = poll_until_terminal('s1-static-sphere-translate-x5-20261003a', ev1)

    # ---- T2: audit control, matching pair (must pass) ---------------------------------------
    ev2 = EV / 'audit-z1p5'
    trial('start', 's1-static-sphere-audit-z1p5-20261003a',
          CASES / 'variants' / 'translate_z1p5' / 'audit-pipeline.json',
          [CASES / 'variants' / 'translate_z1p5' / 'case_spec.json', Z15_SIM], ev2)
    summary['T2'] = poll_until_terminal('s1-static-sphere-audit-z1p5-20261003a', ev2)

    # ---- T3: tolerance-gate negative (must be refused; poll is expected to report GATE_FAILED)
    ev3 = EV / 'audit-mismatch'
    trial('start', 's1-static-sphere-audit-mismatch-20261003a',
          CASES / 'audit-mismatch' / 'pipeline.json',
          [CASES / 'audit-mismatch' / 'case_spec.json', Z15_SIM], ev3)
    summary['T3'] = poll_until_terminal('s1-static-sphere-audit-mismatch-20261003a', ev3)

    # ---- T4: read-only audit of the outer-domain geometry through the durable worker ---------
    b = Backend()
    print('== deploy ==', flush=True)
    print(json.dumps(b.deploy()), flush=True)
    case_ref = 'library/b8cfde0842e5f9082f2d46de16369fea23ae7133bb8c1ed2b0761abbaacb21d7.sim'
    spec = json.loads((CASES / 'case_spec.json').read_text(encoding='utf-8'))
    try:
        r = b.submit_job('preflight', case_ref, 's1domain-audit-20261003a', 300, 'core',
                         review_ref='review/core-preflight-b8cfde0842e5.json')
        print('submitted:', json.dumps(r, ensure_ascii=False), flush=True)
    except Exception as exc:  # the refusal is the evidence
        print('T4 submission refused:', exc, flush=True)
        summary['T4'] = {'rc': 1, 'stdout': str(exc), 'stderr': ''}
        _write_summary(summary)
        return 1
    for _ in range(10):
        time.sleep(20)
        job = b.get_job('s1domain-audit-20261003a')
        state = job['status']['state']
        if state in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'REJECTED_BUSY'}:
            break
    print('T4 state', state, json.dumps(job['status'].get('error'), ensure_ascii=False), flush=True)
    if state == 'SUCCEEDED':
        b.scp(EV / 'domain-audit.json',
              b.remote_path('jobs/s1domain-audit-20261003a/automation_preflight.json'), download=True)
        job['audit'] = json.loads((EV / 'domain-audit.json').read_text(encoding='utf-8-sig'))
    summary['T4'] = {'rc': 0 if state == 'SUCCEEDED' else 1, 'stdout': json.dumps(job['status'], default=str),
                     'stderr': ''}

    # ---- the negative control must be verifiable by a script, not by a report ---------------
    verifier = run([sys.executable, str(ROOT / 'scripts' / 'verify_negative_evidence.py'),
                    '--evidence', str(ev3.relative_to(ROOT)),
                    '--trial-id', 's1-static-sphere-audit-mismatch-20261003a',
                    '--record', 'static_body_audit.json'])
    (ev3 / 'negative-control-verification.json').write_text(verifier.stdout, encoding='utf-8')
    print('== verifier rc=%d ==' % verifier.returncode, flush=True)
    print(verifier.stdout[-1500:], flush=True)
    summary['T3_verifier_rc'] = verifier.returncode
    _write_summary(summary)
    return 0


def _write_summary(summary: dict) -> None:
    (EV / 'round4-run-summary.json').write_text(
        json.dumps({k: (v if isinstance(v, (int, str)) else {'rc': v['rc']})
                    for k, v in summary.items()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
