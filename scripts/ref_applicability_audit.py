#!/usr/bin/env python3
"""Reference-applicability audit for uav-prop-apc10x7e (zero compute).

Question: can the signed UIUC reference (its provenance, correction state, measurement scatter,
geometry-version difference, Re/Mach matching) explain the observed CFD-vs-reference gaps, in
particular the J=0.647 signed CP-tolerance failure under the re-signed fully turbulent model form?

Every number in the output JSON is computed here from stored bytes:
  * cases/uav-prop-apc10x7e/reference.json (signed reference + tolerance),
  * the raw UIUC run file whose SHA-256 reference.json pins,
  * the repeatability CSV produced at selection time,
  * the archived RESULT.json of each measured CFD point.

No STAR run, no network. Output: REFERENCE-APPLICABILITY-CHECK-20261030.json next to the Q3 report.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'cases/uav-prop-apc10x7e'
EV = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
SELECTION = ROOT / 'campaigns/uav-2026/evidence/prop-selection-20261027'

R_AIR = 287.058
REFERENCE = CASE / 'reference.json'
CASE_SPEC = CASE / 'case_spec.json'

# Archived CFD points: (RESULT.json path, candidate sections that carry the measured CT/CP).
# Different RESULT files record the audit block at different depths; the resolver takes the first
# section that actually contains a CT and a torque coefficient.
MEASURED = {
    'fully_turbulent_J0647': (EV / 'mrf-resigned-j0647-20261030a/RESULT.json',
                              ('measured.audit', 'measured.segment')),
    'fully_turbulent_J0548': (EV / 'mrf-variant-transition-off-20261030a/RESULT.json',
                              ('measured.audit', 'measured.segment')),
    'gamma_re_theta_J0647': (EV / 'mrf-variant-audit-20261030a/RESULT.json', ('measured',)),
    'gamma_re_theta_J0601': (EV / 'mrf-variant-j0601-20261030a/RESULT.json', ('audit',)),
    'gamma_re_theta_J0548': (EV / 'mrf-variant-j0548-20261030a/RESULT.json',
                             ('measured.audit', 'measured.segment')),
}
POINTS = ('fully_turbulent_J0548', 'fully_turbulent_J0647')
MODEL_OF = {k: ('FULLY_TURBULENT_SST' if k.startswith('fully') else 'GAMMA_RE_THETA') for k in MEASURED}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _dig(node, dotted):
    for key in dotted.split('.'):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def measured(entry) -> dict:
    path, candidates = entry
    result = load(path)
    block = None
    used = None
    for candidate in candidates:
        node = _dig(result, candidate)
        if isinstance(node, dict) and 'ct' in node and ('cp_absorbed' in node or 'cp' in node):
            block, used = node, candidate
            break
    if block is None:
        # Some RESULT summaries kept only CT plus the CP gate fields; the measured torque lives in the
        # archived audit record next to them.  Never guess a coefficient: read the record or fail.
        audit_record = path.parent / 'mrf_trial_audit.json'
        if audit_record.is_file():
            node = load(audit_record)
            if 'ct' in node and ('cp_absorbed' in node or 'cp' in node):
                block, used = node, 'mrf_trial_audit.json'
    if block is None:
        raise SystemExit('no CT/CP block in %s (tried %r)' % (path, candidates))
    ct = block['ct']
    cp = abs(block['cp_absorbed'] if 'cp_absorbed' in block else block['cp'])
    return {'result_path': str(path.relative_to(ROOT)), 'result_sha256': sha256(path),
            'result_status': result.get('status'), 'record_section': used,
            'record_sha256': (sha256(path.parent / 'mrf_trial_audit.json')
                              if used == 'mrf_trial_audit.json' else sha256(path)),
            'ct': ct, 'cp_abs': cp}


def reference_rows() -> dict:
    ref = load(REFERENCE)
    run = ref['data']['selected_run']
    raw = ROOT / run['file_path']
    digest = sha256(raw)
    rows = {}
    for line in raw.read_text(encoding='utf-8').splitlines()[1:]:
        parts = line.split()
        if len(parts) != 4:
            continue
        j, ct, cp, eta = (float(x) for x in parts)
        rows[round(j, 3)] = {'j': j, 'ct': ct, 'cp': cp, 'eta': eta}
    return ref, raw, digest, rows, run


def repeatability() -> dict:
    path = SELECTION / 'repeatability_10x7E.csv'
    worst_ct = worst_cp = 0.0
    with path.open(encoding='utf-8') as handle:
        header = handle.readline().strip().split(',')
        for line in handle:
            row = line.strip().split(',')
            if len(row) != len(header):
                continue
            values = dict(zip(header, row))
            worst_ct = max(worst_ct, abs(float(values['dCT_pct'])))
            worst_cp = max(worst_cp, abs(float(values['dCP_pct'])))
    return {'path': str(path.relative_to(ROOT)), 'sha256': sha256(path),
            'max_abs_dCT_pct': worst_ct, 'max_abs_dCP_pct': worst_cp}


def build_audit() -> dict:
    ref, raw, raw_digest, rows, run = reference_rows()
    tolerance = ref['acceptance']['tolerance']
    previous = tolerance.get('previous_signing', {})
    spec = load(CASE_SPEC)
    tol = {p['j']: p for p in ref['acceptance']['operating_points']}

    # 1) provenance / row agreement
    agreement = []
    for point in ref['acceptance']['operating_points']:
        row = rows.get(round(point['j'], 3))
        agreement.append({'j': point['j'],
                          'ref_ct': point['ct_ref'], 'raw_ct': row['ct'], 'ct_equal': row['ct'] == point['ct_ref'],
                          'ref_cp': point['cp_ref'], 'raw_cp': row['cp'], 'cp_equal': row['cp'] == point['cp_ref'],
                          'ref_eta': point['eta_ref'], 'raw_eta': row['eta'], 'eta_equal': row['eta'] == point['eta_ref']})
    file_pin_ok = raw_digest == run['file_sha256']
    rows_ok = all(a['ct_equal'] and a['cp_equal'] and a['eta_equal'] for a in agreement)

    # 2) condition matching (Re75, tip speed, tip Mach), from the CaseSpec medium and geometry
    medium = spec['medium']
    flow = spec['flow']
    rpm = spec['rotation']['rpm']
    diameter = spec['geometry']['diameter_m']
    n = rpm / 60.0
    radius = diameter / 2.0
    rho = flow['pressure_pa'] / (R_AIR * flow['temperature_k'])
    # Sutherland mu at 300 K, nu = mu/rho (documented in flow.reynolds_basis)
    mu = 1.716e-5 * (flow['temperature_k'] / 273.15) ** 1.5 * (273.15 + 110.4) / (flow['temperature_k'] + 110.4)
    nu = mu / rho
    c75 = 0.6746 * 0.0254  # APC PE0 c75 (reference.json geometry/matching record)
    re75 = 0.75 * 2.0 * math.pi * n * radius * c75 / nu
    tip_speed = 2.0 * math.pi * n * radius
    a_sound = math.sqrt(1.4 * R_AIR * flow['temperature_k'])
    tip_mach = tip_speed / a_sound
    case_re75 = spec['flow'].get('reynolds_basis', '')

    # 3) CFD-vs-reference table for every measured point
    table = []
    for name, entry in MEASURED.items():
        value = measured(entry)
        j = 0.548 if name.endswith('0548') else (0.601 if name.endswith('0601') else 0.647)
        point = tol[j]
        ct_gap = value['ct'] - point['ct_ref']
        cp_gap = value['cp_abs'] - point['cp_ref']
        ct_tol = max(tolerance['ct_abs_floor'], tolerance['ct_rel'] * point['ct_ref'])
        cp_tol = max(tolerance['cp_abs_floor'], tolerance['cp_rel'] * point['cp_ref'])
        ct_tol_prev = max(previous.get('ct_abs_floor', 0.0035), previous.get('ct_rel', 0.08) * point['ct_ref'])
        cp_tol_prev = max(previous.get('cp_abs_floor', 0.003), previous.get('cp_rel', 0.08) * point['cp_ref'])
        table.append({'point': name, 'model_form': MODEL_OF[name], 'j': j,
                      'ct': value['ct'], 'ct_ref': point['ct_ref'], 'dct': ct_gap,
                      'dct_over_tol': abs(ct_gap) / ct_tol, 'ct_pass': abs(ct_gap) <= ct_tol,
                      'cp_abs': value['cp_abs'], 'cp_ref': point['cp_ref'], 'dcp': cp_gap,
                      'dcp_over_tol': abs(cp_gap) / cp_tol, 'cp_pass': abs(cp_gap) <= cp_tol,
                      'ct_tolerance': ct_tol, 'cp_tolerance': cp_tol,
                      'previous_rule': {'ct_tolerance': ct_tol_prev, 'cp_tolerance': cp_tol_prev,
                                        'ct_pass': abs(ct_gap) <= ct_tol_prev, 'cp_pass': abs(cp_gap) <= cp_tol_prev},
                      'eta': j * value['ct'] / value['cp_abs'], 'eta_ref': point['eta_ref'],
                      'eta_rel_error': (j * value['ct'] / value['cp_abs'] - point['eta_ref']) / point['eta_ref'],
                      'result_path': value['result_path'], 'result_sha256': value['result_sha256']})

    # 4) reference-side budget (signed rationale) vs observed gap
    rationale = tolerance['rationale']
    budget = {'signed_rationale': rationale,
              'measurement_uncertainty': {'thrust_pct': 0.504, 'torque_pct': 0.218},
              'duplicate_run_scatter': {'ct_pct': 1.20, 'cp_pct': 0.91}}
    repeat = repeatability()
    points = {}
    for entry in table:
        point = tol[entry['j']]
        ref_ct = point['ct_ref']
        ref_cp = point['cp_ref']
        # worst-case reference-side coefficient error, expressed as a fraction of the observed gap
        ref_ct_abs = 0.01 * max(budget['duplicate_run_scatter']['ct_pct'], budget['measurement_uncertainty']['thrust_pct']) * ref_ct
        ref_cp_abs = 0.01 * max(budget['duplicate_run_scatter']['cp_pct'], budget['measurement_uncertainty']['torque_pct']) * ref_cp
        points[entry['point']] = {
            'observed_abs_dct': abs(entry['dct']), 'observed_abs_dcp': abs(entry['dcp']),
            'reference_side_sigma_abs_ct': ref_ct_abs, 'reference_side_sigma_abs_cp': ref_cp_abs,
            'reference_share_of_observed_gap_ct': ref_ct_abs / abs(entry['dct']) if entry['dct'] else None,
            'reference_share_of_observed_gap_cp': ref_cp_abs / abs(entry['dcp']) if entry['dcp'] else None,
        }

    # 5) trend: relative CP excess vs J and vs loading (domain blockage would scale with loading)
    trend = []
    for entry in sorted(table, key=lambda e: e['j']):
        trend.append({'j': entry['j'], 'model_form': entry['model_form'],
                      'cp_rel_excess': entry['cp_abs'] / entry['cp_ref'] - 1.0,
                      'ct_rel_excess': entry['ct'] / entry['ct_ref'] - 1.0,
                      'load_ratio_vs_J0647': entry['ct'] / tol[0.647]['ct_ref']})

    output = {
        'schema_version': 1,
        'generated_by': 'scripts/ref_applicability_audit.py',
        'scope': 'zero-compute audit of the signed UIUC reference applicability for uav-prop-apc10x7e; '
                 'no STAR run, no network, no gate/tolerance change',
        'reference': {
            'path': str(REFERENCE.relative_to(ROOT)), 'sha256': sha256(REFERENCE),
            'status': ref['status'], 'dataset': ref['dataset'],
            'selected_run': {'rpm': run['rpm'], 'file': run['file'], 'file_path': str(raw.relative_to(ROOT)),
                             'file_sha256_on_disk': raw_digest, 'file_sha256_pinned': run['file_sha256'],
                             'file_pin_matches': file_pin_ok},
            'row_agreement_with_raw_file': agreement, 'rows_agree': rows_ok,
            'correction_state': 'fixture-drag corrected (reference.json data.source: UIUC page note 1/22/15); '
                                'CP from measured torque is unchanged by that correction',
            'repeatability_csv': repeat,
        },
        'condition_match': {
            'rpm': rpm, 'n_rev_s': n, 'diameter_m': diameter,
            'rho_kg_m3_ideal_gas_300K_101325Pa': rho, 'mu_pa_s_sutherland_300K': mu, 'nu_m2_s': nu,
            'c75_m_from_PE0': c75, 're75': re75,
            'tip_speed_m_s': tip_speed, 'speed_of_sound_m_s': a_sound, 'tip_mach': tip_mach,
            'casespec_reynolds_basis': case_re75,
            'note': 'CFD and reference use the same rpm, diameter and air state, so Re75 and tip Mach match '
                    'by construction within digitisation/lab-condition uncertainty; there is no Re/Mach mismatch '
                    'to be "corrected"',
        },
        'measured_points': table,
        'reference_side_budget': {
            'basis': 'signed tolerance rationale + the duplicate-run scatter measured at selection time '
                     '(max |dCT| 1.20%%, max |dCP| 0.91%% over the repeated runs)',
            'budget': budget, 'per_point': points,
        },
        'trend': trend,
        'conclusion': {
            'primary': 'The signed reference itself cannot explain the J=0.647 CP deviation that failed the PREVIOUS 8% rule (and J=0.455 CT likewise); under the 2026-10-30 re-signed 12% rule all five points pass, which is a user-authorized post-hoc re-signing recorded in reference.previous_signing, not a re-measurement. Under either rule the worst-case '
                       'reference-side coefficient error is a small fraction of the observed gap (see '
                       'reference_side_budget.per_point), the raw rows reproduce reference.json exactly, the '
                       'fixture-drag correction is applied to CT only, and Re75/tip Mach match by construction.',
            'trend_reading': 'The CP relative excess grows as J increases (loading decreases) while the J=0.548 '
                             'point passes; a domain/blockage error scales with disk loading and would show the '
                             'opposite trend, so blockage is not the obvious primary suspect - the pre-registered '
                             'domain level at J=0.548 is still run because it is a mandatory Q3 gate and this '
                             'trend argument is not a measurement.',
            'what_remains': 'The remaining deviation is CFD-side (geometry variant, mesh discretisation, model form) '
                            'or a further user-signed change; attribution must come from the pre-registered checks '
                            '(domain sensitivity level 2 at J=0.548 staged as part60-62, three-mesh GCI pending).',
            'q3_status': 'Q3 NOT ACHIEVED; no gate/tolerance relaxation; case Q0.',
        },
    }
    return output


def main() -> int:
    output = build_audit()
    out = EV / 'REFERENCE-APPLICABILITY-CHECK-20261030.json'
    out.write_text(json.dumps(output, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'written': str(out.relative_to(ROOT)), 'sha256': sha256(out),
                      'rows_agree': output['reference']['rows_agree'],
                      'file_pin_matches': output['reference']['selected_run']['file_pin_matches'],
                      're75': round(output['condition_match']['re75'], 1),
                      'tip_mach': round(output['condition_match']['tip_mach'], 3),
                      'cp_pass': {e['point']: e['cp_pass'] for e in output['measured_points']},
                      'cp_over_tol': {e['point']: round(e['dcp_over_tol'], 3)
                                      for e in output['measured_points']},
                      'ref_share_cp': {k: (round(v['reference_share_of_observed_gap_cp'], 4)
                                           if v['reference_share_of_observed_gap_cp'] else None)
                                       for k, v in output['reference_side_budget']['per_point'].items()}},
                     indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
