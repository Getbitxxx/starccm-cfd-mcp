#!/usr/bin/env python3
"""Mesh-scatter numerical criterion for uav-prop-apc10x7e (zero compute, byte-driven).

USER-SIGNED REVISION 2026-10-30 (path 2 of the Q3 options list): the three-mesh GCI requirement is replaced by a
MEASURED mesh-scatter bound, because the GCI could not be established for this mesh family (three-level and
four-level ladders FAILED with a CT sign reversal / unrecoverable CP order, and a fifth level was rejected by the
unchanged mesh-quality gate).

Signed criterion (reference.json acceptance.numerical_criterion):
    max over all ADMISSIBLE refined levels of |phi_level - phi_production| / |phi_production| <= 5%
    for CT and CP separately, at J=0.548 on the production 5D fully turbulent case.
Signed domain gate:
    |dCT|/CT and |dCP|/CP between the 5D and the larger domain <= 0.5 * the MEASURED mesh scatter per coefficient.

This script reads the per-level records (never a hand-written summary), applies the criterion as signed, and writes
one evidence JSON.  It is NOT a GCI: it reports a measurement-based bound and says so in the output.  A FAIL is
reported as FAIL; the thresholds come from the signed reference file, not from constants in this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
OUTPUT = EV / 'MESH-SCATTER-GATE-20261030a.json'
REFERENCE = ROOT / 'cases/uav-prop-apc10x7e/reference.json'
DOMAIN_EVIDENCE = EV / 'DOMAIN-SENSITIVITY-20261030a.json'

LEVELS = {
    'production': EV / 'mrf-resigned-j0548-20261030a',      # MESH-B13, the answer under test
    'B15': EV / 'mrf-gci-fine-j0548-20261030a',
    'B16': EV / 'mrf-gci-finer2-j0548-20261030a',
    'B17': EV / 'mrf-gci-finer3-j0548-20261030a',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_level(run_dir: Path) -> dict:
    audit_path = run_dir / 'mrf_trial_audit.json'
    physics_path = run_dir / 'mrf_physics_readback.json'
    if not audit_path.is_file() or not physics_path.is_file():
        raise SystemExit('level %s has no audit/physics record' % run_dir)
    audit = loads(audit_path)
    physics = loads(physics_path)
    segment_path = run_dir / 'mrf_segment_solve.json'
    segment = loads(segment_path) if segment_path.is_file() else {}
    return {
        'run_id': run_dir.name, 'audit': str(audit_path.relative_to(ROOT)), 'audit_sha256': sha256(audit_path),
        'cells': physics['measured_cells'], 'ct': audit['ct'], 'cp_abs': abs(audit['cp_absorbed']),
        'yplus_max': audit.get('yplus_max'), 'yplus_gate_passed': audit.get('yplus_gate_passed'),
        'converged': segment.get('stop_reason') == 'CONVERGED' and segment.get('converged') is True,
        'thresholds': {'volume_change_min': None},
    }


def signed_criterion() -> dict:
    tolerance = loads(REFERENCE)['acceptance']
    criterion = tolerance.get('numerical_criterion')
    if not criterion:
        raise SystemExit('reference.json has no signed numerical_criterion')
    return criterion


def evaluate(levels: dict | None = None, criterion: dict | None = None) -> dict:
    levels = levels or {name: read_level(path) for name, path in LEVELS.items()}
    criterion = criterion or signed_criterion()
    production = levels['production']
    refined = [name for name in levels if name != 'production']
    deviations = {}
    for quantity, key in (('ct', 'ct'), ('cp', 'cp_abs')):
        deviations[quantity] = {
            name: abs(levels[name][key] - production[key]) / abs(production[key]) for name in refined
        }
    worst = {quantity: max(per.values()) for quantity, per in deviations.items()}
    limit = criterion['value']
    checks = {quantity: {'worst': worst[quantity], 'worst_level': max(deviations[quantity], key=deviations[quantity].get),
                         'limit': limit, 'pass': worst[quantity] <= limit}
              for quantity in ('ct', 'cp')}
    not_converged = [name for name, level in levels.items() if not level['converged']]
    yplus_ok = all(level.get('yplus_gate_passed') is True for level in levels.values())
    gate = 'PASS' if all(checks[q]['pass'] for q in checks) and not not_converged and yplus_ok else 'FAIL'
    return {
        'schema_version': 1,
        'signed_criterion': {
            'type': criterion['type'], 'value': criterion['value'], 'signed_by': criterion['signed_by'],
            'signed_at': criterion['signed_at'],
            'metric': criterion['metric'], 'replaces': criterion.get('replaces'),
        },
        'levels': levels,
        'deviations_from_production': deviations,
        'worst': worst,
        'checks': checks,
        'gate': gate,
        'not_converged_levels': not_converged,
        'yplus_all_pass': yplus_ok,
        'is_a_gci': False,
        'interpretation': ('measurement-based bound on how far an admissible refinement moves the production answer; '
                           'NOT a grid-convergence index (no observed order exists for this family)'),
    }


def domain_check(mesh: dict) -> dict:
    domain = loads(DOMAIN_EVIDENCE)
    delta = domain['delta']
    thresholds = {'ct': 0.5 * mesh['worst']['ct'], 'cp': 0.5 * mesh['worst']['cp']}
    checks = {q: {'delta': abs(delta[key]), 'limit': thresholds[q], 'pass': abs(delta[key]) <= thresholds[q]}
              for q, key in (('ct', 'relative_ct'), ('cp', 'relative_cp')) if key in delta}
    return {'evidence': str(DOMAIN_EVIDENCE.relative_to(ROOT)), 'evidence_sha256': sha256(DOMAIN_EVIDENCE),
            'rule': '<= 0.5 * measured mesh scatter per coefficient', 'thresholds': thresholds,
            'checks': checks, 'gate': 'PASS' if all(c['pass'] for c in checks.values()) else 'FAIL'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    mesh = evaluate()
    result = {
        'schema_version': 1,
        'generated_by': 'scripts/mesh_scatter_gate.py',
        'scope': 'mesh-scatter numerical criterion at J=0.548 (user-signed revision 2026-10-30, replaces three-mesh GCI)',
        'mesh': mesh,
        'domain': domain_check(mesh),
        'case_status': 'Q3 only if every q3_requires gate passes; this evidence covers the numerical and domain gates',
    }
    out = Path(args.out) if args.out else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out), 'sha256': sha256(out), 'mesh_gate': mesh['gate'],
                      'worst': mesh['worst'], 'domain_gate': result['domain']['gate'],
                      'domain_thresholds': result['domain']['thresholds']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
