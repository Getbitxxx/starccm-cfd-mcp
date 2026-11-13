#!/usr/bin/env python3
"""Five-point signed-tolerance summary for uav-prop-apc10x7e (zero compute, byte-driven).

The signed acceptance rule (reference.json, user-signed 2026-10-27):
  |dCT| <= max(0.0035, 0.08*CT_ref) and |dCP| <= max(0.003, 0.08*CP_ref) at every one of the five points
  J=0.455/0.504/0.548/0.601/0.647, on the re-signed fully turbulent model form.

This script reads each point's own audit/segment records (never a hand-written summary) and reports PASS/FAIL
per point plus the all-five verdict.  A point with no run yet is NOT_RUN and the all-five verdict is
INCOMPLETE - it is never a pass by omission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
OUTPUT = EV / 'SIGNED-LADDER-20261030a.json'

POINTS = {
    'J0647': {'j': 0.647, 'run': 'mrf-resigned-j0647-20261030a'},
    'J0601': {'j': 0.601, 'run': 'mrf-resigned-j0601-20261030a'},
    'J0548': {'j': 0.548, 'run': 'mrf-resigned-j0548-20261030a'},
    'J0504': {'j': 0.504, 'run': 'mrf-resigned-j0504-20261030a'},
    'J0455': {'j': 0.455, 'run': 'mrf-resigned-j0455-20261030a'},
}
REFERENCE = ROOT / 'cases/uav-prop-apc10x7e/reference.json'


def signed_tolerance() -> dict:
    """Read the CURRENT signed tolerance from reference.json (re-signed 2026-10-30 to 12% relative, floors unchanged)."""
    tol = loads(REFERENCE)['acceptance']['tolerance']
    return {'ct_abs_floor': tol['ct_abs_floor'], 'ct_rel': tol['ct_rel'],
            'cp_abs_floor': tol['cp_abs_floor'], 'cp_rel': tol['cp_rel'],
            'status': tol['status'], 'signed_at': tol['signed_at'],
            'reference': str(REFERENCE.relative_to(ROOT)), 'reference_sha256': sha256(REFERENCE)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_point(j: float, run_dir: Path) -> dict:
    if not run_dir.is_dir():
        return {'status': 'NOT_RUN', 'j': j, 'run': run_dir.name}
    audit_path = run_dir / 'mrf_trial_audit.json'
    segment_path = run_dir / 'mrf_segment_solve.json'
    if not audit_path.is_file() or not segment_path.is_file():
        return {'status': 'NOT_RUN', 'j': j, 'run': run_dir.name,
                'note': 'run directory exists but has no downloaded audit/segment records yet'}
    audit = loads(audit_path)
    segment = loads(segment_path)
    velocity = loads(run_dir / 'mrf_velocity_experiment.json') if (run_dir / 'mrf_velocity_experiment.json').is_file() else None
    ct_ref, cp_ref = audit['ct_ref'], audit['cp_ref']
    rule = signed_tolerance()
    ct_tol = max(rule['ct_abs_floor'], rule['ct_rel'] * ct_ref)
    cp_tol = max(rule['cp_abs_floor'], rule['cp_rel'] * cp_ref)
    dct = audit['ct'] - ct_ref
    dcp = audit['cp_absorbed'] - cp_ref
    ct_pass = abs(dct) <= ct_tol + 1e-15
    cp_pass = abs(dcp) <= cp_tol + 1e-15
    return {
        'status': 'PASS' if (ct_pass and cp_pass) else 'FAIL',
        'j': j,
        'run': run_dir.name,
        'audit': str(audit_path.relative_to(ROOT)), 'audit_sha256': sha256(audit_path),
        'segment': str(segment_path.relative_to(ROOT)), 'segment_sha256': sha256(segment_path),
        'converged': segment.get('stop_reason') == 'CONVERGED' and segment.get('converged') is True,
        'iterations': audit.get('iterations'),
        'ct': audit['ct'], 'ct_ref': ct_ref, 'dct': dct, 'ct_tolerance': ct_tol,
        'ct_pass': ct_pass, 'cp_abs': audit['cp_absorbed'], 'cp_ref': cp_ref, 'dcp': dcp,
        'cp_tolerance': cp_tol, 'cp_pass': cp_pass,
        'eta': j * audit['ct'] / audit['cp_absorbed'] if audit['cp_absorbed'] else None,
        'yplus_max': audit.get('yplus_max'), 'yplus_gate_passed': audit.get('yplus_gate_passed'),
        'velocity': None if velocity is None else {
            'experiment_id': velocity.get('experiment_id'), 'applied': velocity.get('applied'),
            'no_op': velocity.get('no_op'), 'before_mps': velocity.get('measured_before_mps'),
            'after_mps': velocity.get('measured_after_mps')},
        'comparison_class': audit.get('comparison_class'),
    }


def build_summary() -> dict:
    points = {}
    for name, spec in POINTS.items():
        points[name] = read_point(spec['j'], EV / spec['run'])
    ran = [p for p in points.values() if p['status'] in ('PASS', 'FAIL')]
    failed = [name for name, p in points.items() if p['status'] == 'FAIL']
    missing = [name for name, p in points.items() if p['status'] == 'NOT_RUN']
    if missing:
        verdict = 'INCOMPLETE'
    elif failed:
        verdict = 'FAIL'
    else:
        verdict = 'PASS'
    return {
        'schema_version': 1,
        'generated_by': 'scripts/signed_ladder_summary.py',
        'scope': 'five-point signed-tolerance table, re-signed fully turbulent model form, MESH-B13 production mesh',
        'tolerance_source': signed_tolerance(),
        'rule': '|dCT| <= max(ct_abs_floor, ct_rel*CT_ref) and |dCP| <= max(cp_abs_floor, cp_rel*CP_ref) at every point',
        'points': points,
        'ran_count': len(ran),
        'failed_points': failed,
        'missing_points': missing,
        'verdict': verdict,
        'q3_note': ('Q3 additionally requires the three-mesh GCI (FAIL/NOT ESTABLISHED at J=0.548), the domain gate '
                    '(NOT_EVALUABLE without GCI_fine) and a frozen CaseSpec; the signed-tolerance verdict alone is '
                    'not a Q3 claim.'),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    summary = build_summary()
    out = Path(args.out) if args.out else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out), 'sha256': sha256(out), 'verdict': summary['verdict'],
                      'ran_count': summary['ran_count'], 'failed': summary['failed_points'],
                      'missing': summary['missing_points'],
                      'per_point': {k: {'status': v['status'],
                                        'dct': v.get('dct'), 'dcp': v.get('dcp'),
                                        'ct_tol': v.get('ct_tolerance'), 'cp_tol': v.get('cp_tolerance')}
                                    for k, v in summary['points'].items()}}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
