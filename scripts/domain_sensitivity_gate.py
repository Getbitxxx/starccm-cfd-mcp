#!/usr/bin/env python3
"""Domain-sensitivity gate for uav-prop-apc10x7e (zero compute, byte-driven).

Q3 requirement (reference.json `q3_requires`):
    |dCT|/CT and |dCP|/CP between the 5D production domain and the larger domain level at J=0.548
    must be <= 0.5 * GCI_fine.

This script reads the two archived run RESULTS, computes the relative CT/CP deltas, and evaluates the
gate against a supplied GCI_fine.  If GCI_fine is not supplied the gate is NOT_EVALUABLE (never PASS):
the delta is still a measurement and is reported as such.

Usage:
  python3 scripts/domain_sensitivity_gate.py                     # delta only, gate NOT_EVALUABLE
  python3 scripts/domain_sensitivity_gate.py --gci-ct 0.03 --gci-cp 0.03
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
BASE_5D = EV / 'mrf-variant-transition-off-20261030a'   # fully turbulent J=0.548 on the 5D MESH-B13 mesh
LEVEL_10D = EV / 'mrf-domain10d-j0548-20261030a'        # fully turbulent J=0.548 on the 10D mesh
OUTPUT = EV / 'DOMAIN-SENSITIVITY-20261030a.json'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_point(run_dir: Path) -> dict:
    """CT / CP_abs of the J=0.548 point from a run directory, with the record actually used.

    Preferred source is the archived RESULT.json; before the Lead writes that summary (immediately after a
    run) the raw audit record is used instead, so the gate never depends on a hand-written summary.
    """
    # The RAW audit record is preferred for both levels: it is written by the same macro at run time, it
    # exists even before the Lead writes the RESULT summary, and it keeps this gate independent of those
    # summaries (a RESULT can then pin this gate's JSON without a circular hash dependency).
    audit_path = run_dir / 'mrf_trial_audit.json'
    result_path = run_dir / 'RESULT.json'
    if audit_path.is_file():
        audit = loads(audit_path)
        segment_path = run_dir / 'mrf_segment_solve.json'
        segment = loads(segment_path) if segment_path.is_file() else {}
        return {'run_id': run_dir.name, 'result': str(audit_path.relative_to(ROOT)),
                'result_sha256': sha256(audit_path), 'record_section': 'mrf_trial_audit.json',
                'ct': audit['ct'], 'cp_abs': abs(audit['cp_absorbed']), 'j': 0.548,
                'converged': segment.get('stop_reason') == 'CONVERGED',
                'iterations': audit.get('iterations'),
                'segment_converged': segment.get('stop_reason'), 'segment_iterations': segment.get('iterations_end'),
                'yplus_max': audit.get('yplus_max'), 'yplus_gate_passed': audit.get('yplus_gate_passed')}
    result = loads(result_path)
    candidates = [('measured.segment', result.get('measured', {}).get('segment')),
                  ('measured.audit', result.get('measured', {}).get('audit')),
                  ('segment', result.get('segment')),
                  ('audit', result.get('audit'))]
    for name, block in candidates:
        if isinstance(block, dict) and 'ct' in block and ('cp_absorbed' in block or 'cp' in block):
            cp = abs(block['cp_absorbed'] if 'cp_absorbed' in block else block['cp'])
            return {'run_id': result.get('run_id'), 'result': str(result_path.relative_to(ROOT)),
                    'result_sha256': sha256(result_path), 'record_section': name,
                    'ct': block['ct'], 'cp_abs': cp, 'j': 0.548,
                    'converged': block.get('converged', block.get('stop_reason') == 'CONVERGED'),
                    'iterations': block.get('iterations_end', block.get('iterations'))}
    raise SystemExit('no CT/CP block in %s' % result_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gci-ct', type=float, default=None, help='GCI_fine fraction for CT (e.g. 0.03)')
    parser.add_argument('--gci-cp', type=float, default=None, help='GCI_fine fraction for CP (e.g. 0.03)')
    parser.add_argument('--out', default=None, help='output JSON path (default: the evidence file)')
    args = parser.parse_args()

    base = read_point(BASE_5D)
    level = read_point(LEVEL_10D)
    dct = level['ct'] - base['ct']
    dcp = level['cp_abs'] - base['cp_abs']
    rel_ct = abs(dct) / base['ct']
    rel_cp = abs(dcp) / base['cp_abs']

    if args.gci_ct is not None and args.gci_cp is not None:
        limit_ct = 0.5 * args.gci_ct
        limit_cp = 0.5 * args.gci_cp
        gate = {'status': 'PASS' if rel_ct <= limit_ct and rel_cp <= limit_cp else 'FAIL',
                'gci_fine_ct': args.gci_ct, 'gci_fine_cp': args.gci_cp,
                'limit_ct': limit_ct, 'limit_cp': limit_cp}
    else:
        gate = {'status': 'NOT_EVALUABLE_GCI_MISSING',
                'reason': 'the Q3 gate compares the domain delta with 0.5*GCI_fine; the three-mesh GCI at J=0.548 '
                          'is NOT_COMPLETED, so the delta above is a measurement, not a pass/fail'}

    output = {
        'schema_version': 1,
        'generated_by': 'scripts/domain_sensitivity_gate.py',
        'scope': 'domain-sensitivity level 2 (5D -> 10D lateral radius) at J=0.548, fully turbulent re-signed form',
        'not_for_q3_configuration': True,
        'baseline_5d': base,
        'level_10d': level,
        'delta': {'ct': dct, 'cp_abs': dcp,
                  'relative_ct': rel_ct, 'relative_cp': rel_cp,
                  'relative_eta': abs((0.548 * level['ct'] / level['cp_abs'])
                                      - (0.548 * base['ct'] / base['cp_abs']))
                                  / (0.548 * base['ct'] / base['cp_abs'])},
        'gate': gate,
        'consequence': '',
    }
    output['consequence'] = (
        'domain delta measured; Q3 domain gate %s. ' % gate['status'] +
        ('The 10D level is adequate at J=0.548 for the measured quantities.' if gate['status'] == 'PASS'
         else 'The domain is not shown adequate: re-run the signed points on the larger domain and recompute GCI.'
         if gate['status'] == 'FAIL'
         else 'Do not claim the domain gate: compute the three-mesh GCI at J=0.548 first.'))
    out = Path(args.out) if args.out else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out), 'sha256': sha256(out),
                      'baseline_ct': base['ct'], 'level_ct': level['ct'],
                      'relative_ct': rel_ct, 'relative_cp': rel_cp,
                      'relative_eta': output['delta']['relative_eta'], 'gate': gate['status']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
