"""Register scenario-1 (static-body external flow) candidate geometry inputs and audit them.

Read-only w.r.t. the Windows source files: register_case copies into the worker library
under a SHA-256 content address; inspect_case runs the installed, read-only `core/preflight`
stage in an isolated job directory. Nothing is written back to the source .sim.

Usage:
    python scripts/static_flow_probe.py --evidence test-evidence/static-external-flow
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, 'scripts')
from backend import Backend  # noqa: E402

SUITE = r'F:\starccm\Siemens.STAR-CCM+2502.0_20.02.007_VerificationSuite_&_Tutorials-SSQ'

CANDIDATES = {
    'oneram6': SUITE + r'\tutorials\compressibleFlow\anisotropic_OneraM6Wing_Start.sim',
    'dualwing': SUITE + r'\tutorials\geometry\dualElementWing_start.sim',
    'sphere': SUITE + r'\tutorials\compressibleFlow\HypersonicSphere_start.sim',
    'eurotruck': SUITE + r'\tutorials\mesh\euroTruck_start.sim',
}


def probe(b: Backend, key: str, windows_path: str, evidence: Path) -> dict:
    record = {'key': key, 'windows_source': windows_path}
    case = b.register_case(windows_path=windows_path)
    record['register_case'] = case
    job_id = 's1probe-' + key
    record['inspect_job_id'] = job_id
    seen = evidence / ('preflight-' + key + '-job.json')
    if seen.exists():
        prior = json.loads(seen.read_text())
        if prior.get('state') == 'SUCCEEDED':
            record['inspect'] = prior
            return record
    result = b.inspect_case(case['case_ref'], job_id, wait_seconds=110)
    record['inspect'] = result
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / ('preflight-' + key + '-job.json')).write_text(
        json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('audit'):
        (evidence / ('preflight-' + key + '-audit.json')).write_text(
            json.dumps(result['audit'], ensure_ascii=False, indent=2))
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--only', action='append')
    a = ap.parse_args()
    a.evidence.mkdir(parents=True, exist_ok=True)
    b = Backend()
    keys = a.only or list(CANDIDATES)
    out = {}
    for key in keys:
        print('== ' + key, flush=True)
        try:
            out[key] = probe(b, key, CANDIDATES[key], a.evidence)
        except Exception as exc:  # keep the probe going; the failure is the evidence
            out[key] = {'key': key, 'error': type(exc).__name__ + ': ' + str(exc)[-1500:]}
        print(json.dumps(out[key], ensure_ascii=False)[:2000], flush=True)
    (a.evidence / 'candidate-probe.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    for key, rec in out.items():
        jobs = rec.get('inspect') or {}
        print(key, '->', jobs.get('state'), 'case_ref', (rec.get('register_case') or {}).get('case_ref'))


if __name__ == '__main__':
    main()
