#!/usr/bin/env python3
"""N-level grid-convergence index (GCI) at J=0.548 for uav-prop-apc10x7e (zero compute, byte-driven).

Protocol pre-registered in Q3-STATUS-20261030.md sections 15, 15.1, 15.2 (upward three-level attempt) and
section 19 (four-level ladder) and section 21 (five-level ladder, user decisions 2026-10-30):
  * levels, all true geometric similarity with the prism stack scaled: production MESH-B13 (coarsest,
    solved by part57) -> MESH-B15 (part66) -> MESH-B16 (part68) -> MESH-B17 (part70) -> MESH-B18 (part72, finest);
  * the observed order p is recovered from the THREE FINEST levels (they are closest to the asymptotic
    range) with the Celik implicit equation; the same p extrapolates the exact value and every level's
    error follows;
  * Roache/Celik: Fs = 1.25, r from the MEASURED cell counts, GCI_i = Fs*|phi_i - phi_extrap|/|phi_i|;
    the gate is applied to the PRODUCTION level: p > 1 and GCI_production <= 5% for CT and CP, all four
    levels converged, every y+ gate pass;
  * the three-coarsest triple (production/B15/B16) is reported as a DIAGNOSTIC: it was the failed
    three-level attempt and is non-asymptotic.

The q3 requirement (reference.json q3_requires) is the judge here: a GCI that cannot be computed
(no observed order, zero differences, p <= 1, non-monotone, or a production GCI above 5%) is reported as
FAIL/NOT_VALID, never as a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
OUTPUT = EV / 'GCI-J0548-NLEVELS-20261030a.json'   # N-level result (five levels after B18)
LEGACY_4LEVEL = EV / 'GCI-J0548-4LEVEL-20261030a.json'  # the failed four-level result, kept as history
LEGACY_3LEVEL = EV / 'GCI-J0548-20261030a.json'          # the failed three-level result, kept as history

# N-level ladder (user decisions 2026-10-30, report sections 19 and 21): production MESH-B13 (coarsest) ->
# MESH-B15 (x1/1.3) -> MESH-B16 (x1/1.69) -> MESH-B17 (x1/2.197) -> MESH-B18 (x1/2.8561, finest).  The observed
# order is recovered from the THREE FINEST levels (closest to the asymptotic range) and the PRODUCTION level's
# error is the extrapolated one (the gate is applied there).  The three-coarsest attempt (production/B15/B16)
# was NON-ASYMPTOTIC and is kept as a diagnostic.
LEVELS = {
    'coarse': EV / 'mrf-resigned-j0548-20261030a',      # production MESH-B13 (part57)
    'medium': EV / 'mrf-gci-fine-j0548-20261030a',      # MESH-B15 (part66)
    'fine': EV / 'mrf-gci-finer2-j0548-20261030a',      # MESH-B16 (part68)
    'finer': EV / 'mrf-gci-finer3-j0548-20261030a',     # MESH-B17 (part70)
    'finer2': EV / 'mrf-gci-finer4-j0548-20261030a',    # MESH-B18 (part72)
}
# the gate is applied to the PRODUCTION mesh's extrapolated error (stricter than the literal GCI_fine wording)
GATE_LEVEL = 'coarse'
FS = 1.25
GCI_LIMIT = 0.05
P_MIN = 1.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loads(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def rel(path: Path) -> str:
    """Repo-relative path when possible, absolute otherwise (tests run on temporary level dirs)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_level(run_dir: Path) -> dict:
    """CT, CP_abs and the measured cell count of one GCI level, from the run's own records."""
    audit_path = run_dir / 'mrf_trial_audit.json'
    physics_path = run_dir / 'mrf_physics_readback.json'
    if not audit_path.is_file() or not physics_path.is_file():
        raise SystemExit('level %s has no audit/physics record yet' % run_dir)
    audit = loads(audit_path)
    physics = loads(physics_path)
    segment_path = run_dir / 'mrf_segment_solve.json'
    segment = loads(segment_path) if segment_path.is_file() else {}
    return {
        'run_id': run_dir.name,
        'audit': rel(audit_path), 'audit_sha256': sha256(audit_path),
        'physics': rel(physics_path), 'physics_sha256': sha256(physics_path),
        'cells': physics['measured_cells'],
        'ct': audit['ct'], 'cp_abs': abs(audit['cp_absorbed']),
        'stop_reason': segment.get('stop_reason'), 'iterations': audit.get('iterations'),
        'yplus_max': audit.get('yplus_max'), 'yplus_gate_passed': audit.get('yplus_gate_passed'),
        'converged': segment.get('stop_reason') == 'CONVERGED' and segment.get('converged') is True,
    }


def _order_of_accuracy(eps21: float, eps32: float, r21: float, r32: float) -> tuple[float | None, str]:
    """Celik's three-level observed order.

    With r21 = h2/h1, r32 = h3/h2 (both > 1) and eps21 = phi2-phi1, eps32 = phi3-phi2, the exact
    Taylor expansion of phi = phi0 + a*h^p gives eps32/eps21 = r21^p (r32^p - 1)/(r21^p - 1), i.e.

        p ln(r21) = ln|eps32/eps21| + ln((r21^p - 1)/(r32^p - 1))

    which is Celik et al. (2008) eq. (3) with the sign carried by the absolute value of the ratio.
    """
    if eps21 == 0.0 or eps32 == 0.0:
        return None, 'ZERO_DIFFERENCE'
    if eps21 * eps32 < 0.0:
        # sign reversal between the two grids = oscillatory convergence; an order cannot be claimed
        return None, 'OSCILLATORY_SIGN_REVERSAL'
    ratio = abs(eps32 / eps21)

    def residual(p: float) -> float:
        return p * math.log(r21) - (math.log(ratio) + math.log((r21 ** p - 1.0) / (r32 ** p - 1.0)))

    lo, hi = 0.2, 8.0
    f_lo, f_hi = residual(lo), residual(hi)
    if f_lo == 0.0:
        return lo, 'OK'
    if f_lo * f_hi > 0.0:
        return None, 'NO_ROOT_IN_0.2_8'
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = residual(mid)
        if f_lo * f_mid <= 0.0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi), 'OK'


def gci_from_levels(coarse: dict, medium: dict, fine: dict, fs: float = FS) -> dict:
    """Three-mesh GCI for one scalar; `cells` and the scalar value must be present on each level.

    Reports the conventional GCI_fine AND the extrapolated error of every level.  The Q3 gate is applied to
    the PRODUCTION level's error (the coarsest here), which is the honest/stricter reading: the reported
    CT/CP live on the production mesh.
    """
    n3, n2, n1 = float(coarse['cells']), float(medium['cells']), float(fine['cells'])
    # h ~ N^(-1/3); Celik's r21 = h_medium/h_fine, r32 = h_coarse/h_medium (both > 1)
    r21 = (n1 / n2) ** (1.0 / 3.0)
    r32 = (n2 / n3) ** (1.0 / 3.0)
    phi1, phi2, phi3 = float(fine['value']), float(medium['value']), float(coarse['value'])
    eps21, eps32 = phi2 - phi1, phi3 - phi2
    p, p_status = _order_of_accuracy(eps21, eps32, r21, r32)
    out = {'cells': {'fine': n1, 'medium': n2, 'coarse': n3},
           'r21': r21, 'r32': r32, 'phi': {'fine': phi1, 'medium': phi2, 'coarse': phi3},
           'eps21': eps21, 'eps32': eps32, 'observed_order': p, 'order_status': p_status,
           'fs': fs}
    if p is None or p <= P_MIN:
        out.update({'valid': False, 'gci_fine': None, 'gci_levels': {},
                    'reason': ('observed order not recoverable: %s' % p_status) if p is None
                    else 'observed order %.4f <= 1 (Q3 requires p > 1)' % p})
        out['gate'] = 'FAIL'
        return out
    phi_extrap = phi1 + (phi1 - phi2) / (r21 ** p - 1.0)
    gci_levels = {}
    for name, phi in (('fine', phi1), ('medium', phi2), ('coarse', phi3)):
        gci_levels[name] = fs * abs(phi - phi_extrap) / abs(phi) if phi else None
    gci_fine = fs * abs(eps21) / (r21 ** p - 1.0)
    production = gci_levels['coarse']
    out.update({'valid': True, 'phi_extrapolated': phi_extrap,
                'gci_fine': gci_fine, 'relative_gci_fine': gci_fine / abs(phi1) if phi1 else None,
                'gci_levels': gci_levels, 'gci_production': production})
    out['gate'] = 'PASS' if production is not None and production <= GCI_LIMIT else 'FAIL'
    out['reason'] = ('production-level GCI %.6f vs limit %.3f (GCI_fine %.6f)'
                     % (production if production is not None else float('nan'), GCI_LIMIT, gci_fine))
    return out


def evaluate(levels: dict, fs: float = FS) -> dict:
    """N-level GCI over an ORDERED dict of levels, coarsest first (production) to finest last.

    p is recovered from the THREE FINEST levels (they are closest to the asymptotic range); the same p
    extrapolates the exact value, every level's error follows, and the Q3 gate is applied to the PRODUCTION
    level (the first entry).  The three-CoARSEST triple is evaluated as a diagnostic: it is the earlier
    three-level attempt that was NON-ASYMPTOTIC.  Works for three or more levels, so adding a fourth or fifth
    level does not need new code.
    """
    order = list(levels)
    if len(order) < 3:
        raise SystemExit('need at least three GCI levels, got %d' % len(order))
    quantities = {}
    for quantity, key in (('ct', 'ct'), ('cp', 'cp_abs')):
        scalar = {level: {'cells': levels[level]['cells'], 'value': levels[level][key]} for level in order}
        finest = gci_from_levels(scalar[order[-3]], scalar[order[-2]], scalar[order[-1]], fs)
        diagnostic = gci_from_levels(scalar[order[0]], scalar[order[1]], scalar[order[2]], fs)
        diag = {'levels': order[:3], 'valid': diagnostic.get('valid'),
                'observed_order': diagnostic.get('observed_order'),
                'order_status': diagnostic.get('order_status'), 'reason': diagnostic.get('reason')}
        if not finest.get('valid'):
            quantities[quantity] = {'valid': False, 'gate': 'FAIL',
                                    'observed_order': finest.get('observed_order'),
                                    'order_status': finest.get('order_status'),
                                    'reason': 'finest-three order not recoverable: %s' % finest.get('order_status'),
                                    'finest_triple': order[-3:], 'diagnostic_three_coarsest': diag}
            continue
        order_p = finest['observed_order']
        phi_extrap = finest['phi_extrapolated']
        per_level = {level: fs * abs(scalar[level]['value'] - phi_extrap) / abs(scalar[level]['value'])
                     for level in order}
        production = per_level[order[0]]
        quantities[quantity] = {
            'valid': True, 'observed_order': order_p, 'extrapolated': phi_extrap,
            'phi': {level: scalar[level]['value'] for level in order},
            'gci_per_level': per_level, 'gci_production': production, 'gci_fine': finest['gci_fine'],
            'cell_ratios': {'r21_finest_pair': finest['r21'], 'r32_finest_triple': finest['r32']},
            'gate': 'PASS' if production <= GCI_LIMIT else 'FAIL',
            'reason': 'production-level GCI %.6f vs limit %.3f (GCI_fine %.6f)'
                      % (production, GCI_LIMIT, finest['gci_fine']),
            'finest_triple': order[-3:], 'diagnostic_three_coarsest': diag,
        }
    both_pass = all(result.get('gate') == 'PASS' for result in quantities.values())
    not_converged = [name for name, level in levels.items() if not level['converged']]
    yplus_ok = all(level.get('yplus_gate_passed') is True for level in levels.values())
    gate = 'PASS' if both_pass and not not_converged and yplus_ok else 'FAIL'
    return {'ladder_schema': len(order), 'levels_used': order, 'quantities': quantities, 'gate': gate,
            'not_converged_levels': not_converged, 'yplus_all_pass': yplus_ok,
            'ladder': {'levels': order, 'direction': 'upward (revisions 15.2, 19 and 21)'},
            'gate_level': order[0],
            'criterion': ('observed order p>1 from the three finest levels AND the PRODUCTION-level extrapolated '
                          'GCI <= 5% for CT and CP; every level converged; y+ gates pass'),
            'note': ('the three-coarsest triple is reported as a diagnostic (it is the failed earlier attempt); '
                     'a finest-three order that is not recoverable (p<=1/zero/sign-reversal) is FAIL'),
            'legacy_4level_evidence': str(LEGACY_4LEVEL.relative_to(ROOT)),
            'legacy_3level_evidence': str(LEGACY_3LEVEL.relative_to(ROOT))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--fs', type=float, default=FS)
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    levels = {name: read_level(path) for name, path in LEVELS.items()}
    result = evaluate(levels, args.fs)
    output = {
        'schema_version': 1,
        'generated_by': 'scripts/gci.py',
        'scope': ('N-level GCI at J=0.548, fully turbulent re-signed form, 5D production domain '
                  '(production MESH-B13 -> B15 -> B16 -> B17 -> B18); the observed order comes from the three '
                  'FINEST levels and the gate is applied to the PRODUCTION level'),
        'levels': levels,
        'gci': result,
        'case_status': 'Q3 NOT ACHIEVED unless every gate passes; this GCI is one gate, not a Q3 claim',
    }
    out = Path(args.out) if args.out else OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out), 'sha256': sha256(out),
                      'gate': result['gate'],
                      'per_quantity': {q: {'gate': r['gate'], 'p': r.get('observed_order'),
                                           'gci_production': r.get('gci_production'),
                                           'gci_fine': r.get('gci_fine'),
                                           'status': r.get('order_status'),
                                           'reason': r.get('reason'),
                                           'diagnostic_three_coarsest': r.get('diagnostic_three_coarsest')}
                                       for q, r in result['quantities'].items()},
                      'not_converged_levels': result['not_converged_levels'],
                      'yplus_all_pass': result['yplus_all_pass']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
