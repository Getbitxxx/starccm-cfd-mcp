#!/usr/bin/env python3
"""Model-form swing: fully turbulent SST vs gamma-Re_theta at the five operating points (zero compute, byte-driven).

Diagnostic A (user-authorized 2026-10-30) adds gamma-Re_theta runs at J=0.455 and J=0.504 on the production MESH-B13;
the already-recorded gamma-Re_theta points at J=0.548/0.601/0.647 come from the earlier model-form comparison.  This
script pairs each gamma-Re_theta measurement with the fully turbulent signed point at the same J and reports:

  * the swing  (gamma-Re_theta / fully_turbulent - 1) for CT and CP;
  * each model's signed-tolerance error (the signed 12% rule from reference.json) and whether it passes;
  * the reference-side gap for each model, so the decomposition can say whether the model form explains it.

It never claims a signed point: the diagnostic runs carry no within-tolerance gates and the case's Q3 claim stays the
one signed under the 2026-10-30 criterion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
OUTPUT = EV / 'MODEL-FORM-SWING-20261030a.json'
REFERENCE = ROOT / 'cases/uav-prop-apc10x7e/reference.json'

# fully turbulent signed points (the five-point signed ladder)
FT = {
    '0.455': EV / 'mrf-resigned-j0455-20261030a',
    '0.504': EV / 'mrf-resigned-j0504-20261030a',
    '0.548': EV / 'mrf-resigned-j0548-20261030a',
    '0.601': EV / 'mrf-resigned-j0601-20261030a',
    '0.647': EV / 'mrf-resigned-j0647-20261030a',
}
# gamma-Re_theta arms: diagnostic A runs for the two low-J points, the earlier comparison runs for the rest
GRT = {
    '0.455': EV / 'mrf-diagA-gamma-theta-j0455-20261030a',
    '0.504': EV / 'mrf-diagA-gamma-theta-j0504-20261030a',
    '0.548': EV / 'mrf-variant-j0548-20261030a',
    '0.601': EV / 'mrf-variant-j0601-20261030a',
    '0.647': EV / 'mrf-variant-audit-20261030a',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def convergence_evidence(run_dir: Path, depth: int = 0) -> dict:
    """Convergence proof for a measurement, following the declared input chain for read-only audit-only runs.

    A continuation/audit-only run has no segment record of its own: its convergence lives in the run that produced the
    .sim it audited.  The chain is resolved through the controller's plan registry (the same declared inputs the
    reviewer pins), so this is not a guess.
    """
    segment_path = run_dir / 'mrf_segment_solve.json'
    if segment_path.is_file():
        segment = loads(segment_path)
        converged = segment.get('stop_reason') == 'CONVERGED' and segment.get('converged') is True
        gates = all(segment.get(k) is True for k in ('residuals_gate_passed', 'mass_gate_passed'))
        return {'converged': converged, 'gates_passed': gates, 'source': run_dir.name,
                'stop_reason': segment.get('stop_reason')}
    if depth >= 3:
        return {'converged': False, 'gates_passed': False, 'source': run_dir.name, 'stop_reason': 'CHAIN_DEPTH'}
    import run_mrf_chain as chain
    plan = next((pl for pl in chain.PLANS.values() if pl['run_id'] == run_dir.name), None)
    if plan is None:
        return {'converged': False, 'gates_passed': False, 'source': run_dir.name, 'stop_reason': 'PLAN_NOT_FOUND'}
    upstream = plan.get('inputs', {}).get('mrf_segment_solved.sim')
    if upstream is None:
        return {'converged': False, 'gates_passed': False, 'source': run_dir.name, 'stop_reason': 'NO_CHAIN_INPUT'}
    found = convergence_evidence(Path(upstream).parent, depth + 1)
    found['source'] = run_dir.name + ' -> ' + found['source']
    return found


def read_point(run_dir: Path, j: float) -> dict:
    audit = loads(run_dir / 'mrf_trial_audit.json')
    segment = loads(run_dir / 'mrf_segment_solve.json') if (run_dir / 'mrf_segment_solve.json').is_file() else {}
    run = loads(run_dir / 'run.json') if (run_dir / 'run.json').is_file() else {}
    # FAIL-CLOSED CITABILITY: a run whose stage FAILED (or that did not converge) must never contribute a number.
    # Measured lesson: the failed gamma-Re_theta low-J runs were initially picked up here and silently changed the
    # swing; the record status plus convergence evidence (own segment, or the declared input chain) are the only
    # citable evidence.  A run-level FAILED caused ONLY by a signed-tolerance gate is allowed: that failure is the
    # phenomenon being measured, not a numerical failure.
    status = run.get('status')
    evidence = convergence_evidence(run_dir)
    numerical_ok = evidence['converged'] and evidence['gates_passed']
    tolerance_only_failure = status == 'FAILED' and numerical_ok
    citable = (status == 'EXECUTION_COMPLETED' or tolerance_only_failure) and numerical_ok
    return {
        'j': j, 'run_id': run_dir.name, 'citable': citable,
        'record_status': status, 'stop_reason': evidence['stop_reason'],
        'converged': evidence['converged'], 'numerical_gates_passed': evidence['gates_passed'],
        'convergence_source': evidence['source'],
        'ct': audit['ct'], 'cp_abs': abs(audit['cp_absorbed']),
        'yplus_max': audit.get('yplus_max'),
        'converged': segment.get('stop_reason') == 'CONVERGED' and segment.get('converged') is True,
        'audit': str((run_dir / 'mrf_trial_audit.json').relative_to(ROOT)),
        'audit_sha256': sha256(run_dir / 'mrf_trial_audit.json'),
        'transition_model_enabled': audit.get('transition_model_enabled'),
    }


def signed_rule() -> dict:
    return loads(REFERENCE)['acceptance']['tolerance']


def build() -> dict:
    points = {round(float(pp['j']), 3): pp for pp in loads(REFERENCE)['acceptance']['operating_points']}
    rule = signed_rule()
    rows = {}
    missing = []
    excluded = {}
    for tag in ('0.455', '0.504', '0.548', '0.601', '0.647'):
        if not ((FT[tag] / 'mrf_trial_audit.json').is_file() and (GRT[tag] / 'mrf_trial_audit.json').is_file()):
            missing.append(tag)
            continue
        ft = read_point(FT[tag], float(tag))
        gt = read_point(GRT[tag], float(tag))
        if not (ft['citable'] and gt['citable']):
            excluded[tag] = {'fully_turbulent': {k: ft[k] for k in ('citable', 'record_status', 'stop_reason', 'converged')},
                             'gamma_re_theta': {k: gt[k] for k in ('citable', 'record_status', 'stop_reason', 'converged')},
                             'reason': 'a run in this pair FAILED or did not converge; a failed stage record may not be cited'}
            continue
        ref = points[round(float(tag), 3)]
        ct_tol = max(rule['ct_abs_floor'], rule['ct_rel'] * ref['ct_ref'])
        cp_tol = max(rule['cp_abs_floor'], rule['cp_rel'] * ref['cp_ref'])
        rows[tag] = {
            'j': float(tag),
            'fully_turbulent': ft, 'gamma_re_theta': gt,
            'reference': {'ct_ref': ref['ct_ref'], 'cp_ref': ref['cp_ref'], 'eta_ref': ref['eta_ref']},
            'swing': {'ct_rel': gt['ct'] / ft['ct'] - 1.0, 'cp_rel': gt['cp_abs'] / ft['cp_abs'] - 1.0},
            'errors_vs_reference': {
                'fully_turbulent': {'dct': ft['ct'] - ref['ct_ref'], 'dcp': ft['cp_abs'] - ref['cp_ref'],
                                    'ct_pass': abs(ft['ct'] - ref['ct_ref']) <= ct_tol,
                                    'cp_pass': abs(ft['cp_abs'] - ref['cp_ref']) <= cp_tol},
                'gamma_re_theta': {'dct': gt['ct'] - ref['ct_ref'], 'dcp': gt['cp_abs'] - ref['cp_ref'],
                                   'ct_pass': abs(gt['ct'] - ref['ct_ref']) <= ct_tol,
                                   'cp_pass': abs(gt['cp_abs'] - ref['cp_ref']) <= cp_tol},
            },
            'tolerance': {'ct': ct_tol, 'cp': cp_tol},
        }
    swings_ct = [r['swing']['ct_rel'] for r in rows.values()]
    swings_cp = [r['swing']['cp_rel'] for r in rows.values()]
    # J-tilt: slope of the CT error vs J for each model (does the model form explain the flat CFD CT-J slope?)
    def slope(key: str) -> float | None:
        items = sorted(rows.values(), key=lambda r: r['j'])
        if len(items) < 2:
            return None
        j0, j1 = items[0]['j'], items[-1]['j']
        e0 = items[0]['errors_vs_reference'][key]['dct']
        e1 = items[-1]['errors_vs_reference'][key]['dct']
        return (e1 - e0) / (j1 - j0)
    return {
        'schema_version': 1,
        'generated_by': 'scripts/model_form_swing.py',
        'scope': ('model-form swing (gamma-Re_theta vs fully turbulent SST) at the five operating points on the '
                  'production MESH-B13; diagnostic A supplies the two low-J points'),
        'signed_tolerance': rule,
        'points': rows,
        'missing_points': missing,
        'excluded_pairs': excluded,
        'summary': {
            'swing_ct_min': min(swings_ct) if swings_ct else None,
            'swing_ct_max': max(swings_ct) if swings_ct else None,
            'swing_cp_min': min(swings_cp) if swings_cp else None,
            'swing_cp_max': max(swings_cp) if swings_cp else None,
            'ct_error_slope_fully_turbulent': slope('fully_turbulent'),
            'ct_error_slope_gamma_re_theta': slope('gamma_re_theta'),
            'note': ('a positive swing means gamma-Re_theta gives a larger value; the CT error slope vs J shows whether '
                     'the model form can explain the flat CFD CT-J slope (both slopes positive => it cannot)'),
        },
        'caveat': ('diagnostics, not signed points; the Q3 claim stays the one signed under the 2026-10-30 criterion'),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    data = build()
    out = Path(args.out) if args.out else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out), 'sha256': sha256(out), 'points': sorted(data['points']),
                      'missing': data['missing_points'], 'summary': data['summary']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
