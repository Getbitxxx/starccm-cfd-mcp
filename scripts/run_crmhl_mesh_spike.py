"""Ad-hoc STAR mesh SPIKE for the signed Phase 3 case uav-crm-hl-hlpw4 (P3.2 W2, F2 <=40 min trial).

Two modes:
  prepare  build the local staging (CaseSpec + spike mesh keys + the audited import .sim + four reviewed
           generic external-flow macros + config) and print the hash list the independent reviewer pins.
  run      verify an independent review of those exact bytes, upload, execute the review-gated runner
           (one STAR process per macro: domain -> region/boundaries -> volume mesh -> read-only mesh audit)
           and download the records.

This is an ISOLATED FEASIBILITY TRIAL, not formal evidence: it is not a Q3 certificate and its mesh is not
the production mesh.  It measures whether this geometry can be domain-built and volume-meshed, how many
cells and how long a coarse recipe takes, and what the realised prism/quality minima are.  The runner
enforces the F2 rule (<=40 min) as a hard spike budget; exceeding it stops the owned STAR tree and writes
SPIKE_BUDGET_EXCEEDED.

The four macros are the generic CaseSpec-driven external-flow macros already used by the formal static
external-flow chain; their bytes are pinned here.
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import Backend

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'cases/uav-crm-hl-hlpw4'
SPEC = CASE / 'case_spec.json'
IMPORT_SIM = (ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4/crmhl-geometry-audit-20261031b'
              / 'CRM_HL_SEMISPAN_import.sim')
RUN_ID = 'crmhl-mesh-spike-20261031b'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\crmhl-mesh-spike-20261031b'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4' / RUN_ID
STAGING = EVIDENCE / 'staging'
MACROS = ['BuildExternalDomain.java', 'BuildExternalFlowRegion.java',
          'BuildExternalFlowMesh.java', 'AuditExternalFlowMesh.java']
RUNNER_REL = 'scripts/security/run_crmhl_mesh_spike_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_crmhl_mesh_spike.py'
BODY = 'CRM_HL_SEMISPAN'
SPIKE_BUDGET_SECONDS = 2400.0

# SPIKE-ONLY coarse surface/volume sizes.  The prism recipe is NOT overridden: first layer, growth and total
# thickness stay the CaseSpec's production values (quality.mesh_prism_*), and the flat layer count added below is
# the CaseSpec target, so the recipe is one consistent 20-layer stack.  Only keys that do not already exist in the
# CaseSpec are added (the macros throw on duplicate fields).
SPIKE_SURFACE = {
    'mesh_base_size_m': 6.0,
    'mesh_body_target_size_m': 0.40,
    'mesh_body_minimum_size_m': 0.06,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def derived_spec() -> tuple[dict, dict]:
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    derived = json.loads(json.dumps(spec))                 # deep copy; nested fields are otherwise untouched
    added = dict(SPIKE_SURFACE)
    added['mesh_prism_layers'] = int(spec['quality']['mesh_prism_layers_target'])
    added['body_wall_boundary_name'] = spec['boundary_roles']['body_wall']
    added['spike_only'] = True
    for key, value in added.items():
        if key in derived:
            raise SystemExit('derived spike spec would duplicate CaseSpec key: ' + key)
        derived[key] = value
    # The published half model's measured cut plane is at y = expected_bounds_m[1] (-1.288 mm), NOT exactly 0
    # and NOT the STAR-imported body minimum (-1.28e-6 m); the spike starts the domain AT the measured OCCT cut
    # plane so the symmetry face and the B-rep cut are the same plane, and measures what STAR's import does with
    # it.  Spike-only: the production CaseSpec must resolve cut plane vs y=0.
    derived['domain']['outer_bounds_m'][1] = spec['geometry']['expected_bounds_m'][1]
    text = json.dumps(derived)
    for key in added:
        if text.count('"%s"' % key) != 1:
            raise SystemExit('derived spike spec does not carry exactly one copy of ' + key)
    return derived, spec


def config(job_dir: str) -> dict:
    derived, spec = derived_spec()
    added = {k: v for k, v in derived.items() if k not in spec}
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / 'sources').mkdir(exist_ok=True)
    (STAGING / 'case_spec.json').write_text(json.dumps(derived, indent=1, ensure_ascii=False) + '\n',
                                            encoding='utf-8')
    shutil.copyfile(IMPORT_SIM, STAGING / (BODY + '_import.sim'))
    for macro in MACROS:
        shutil.copyfile(ROOT / 'macros' / macro, STAGING / 'sources' / macro)
    inputs = {'case_spec.json': sha256(STAGING / 'case_spec.json'),
              BODY + '_import.sim': sha256(STAGING / (BODY + '_import.sim'))}
    macros = {m: sha256(STAGING / 'sources' / m) for m in MACROS}
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_crmhl_mesh_spike_only_not_formal_evidence_or_Q3',
        'run_id': RUN_ID,
        'job_dir': job_dir.rstrip('\\'),
        'star_launcher': 'C:\\Program Files\\Siemens\\20.02.008-R8\\STAR-CCM+20.02.008-R8\\star\\bin\\starccm+.bat',
        'star_version': '2502.0001',
        'logical_cores': 64,
        'memory_budget_gb': 239,
        'memory_reserve_gb': 16,
        'wall_clock_stop_enabled': False,
        'spike_budget_seconds': SPIKE_BUDGET_SECONDS,
        'spike_only': True,
        'runner_sha256': sha256(ROOT / RUNNER_REL),
        'inputs': inputs,
        'macros': macros,
        'derivation': {
            'source_case_spec': 'cases/uav-crm-hl-hlpw4/case_spec.json',
            'source_case_spec_sha256': sha256(SPEC),
            'rule': ('add the spike-only unique keys %r + spike_only=true (no key already present is duplicated) and set '
                     'domain.outer_bounds_m[1] to the measured body cut plane geometry.expected_bounds_m[1]; every other '
                     'CaseSpec field is unchanged' % sorted(added)),
        },
        'steps': [
            {'name': 'domain', 'macro': 'BuildExternalDomain.java',
             'input': BODY + '_import.sim', 'output': 'external_domain_built.sim',
             'env': {}, 'marker': 'EXTERNAL_DOMAIN_BUILD_COMPLETE',
             'records': ['external_domain_build.json']},
            {'name': 'region', 'macro': 'BuildExternalFlowRegion.java',
             'input': 'external_domain_built.sim', 'output': 'external_flow_region.sim',
             'env': {}, 'marker': 'EXTERNAL_FLOW_REGION_BUILT',
             'records': ['external_flow_region.json']},
            {'name': 'mesh', 'macro': 'BuildExternalFlowMesh.java',
             'input': 'external_flow_region.sim', 'output': 'external_flow_meshed.sim',
             'env': {}, 'marker': 'EXTERNAL_FLOW_MESH_BUILT',
             'records': ['external_flow_mesh.json']},
            {'name': 'audit', 'macro': 'AuditExternalFlowMesh.java',
             'input': 'external_flow_meshed.sim', 'output': None,
             'env': {}, 'marker': 'EXTERNAL_FLOW_MESH_QUALITY_AUDITED',
             'records': ['external_flow_mesh_quality.json']},
        ],
        'gates': {
            'spike_budget_seconds': SPIKE_BUDGET_SECONDS,
            'part': BODY,
            'wall_name': derived['body_wall_boundary_name'],
            'mesh_cells_min': 1,
            'prism_layers': int(derived['mesh_prism_layers']),
            'cell_quality_min': float(spec['quality']['mesh_min_cell_quality']),
            'face_validity_min': float(spec['quality']['mesh_min_face_validity']),
            'volume_change_min': float(spec['quality']['mesh_min_volume_change']),
            'wall_prism_layers_min': int(spec['quality']['mesh_min_wall_prism_layers']),
        },
    }


def prepare(args) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    derived, spec = derived_spec()
    cfg = config(args.job_dir)
    config_path = STAGING / 'config.json'
    config_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {
        RUNNER_REL: sha256(ROOT / RUNNER_REL),
        CONTROLLER_REL: sha256(ROOT / CONTROLLER_REL),
        'inputs/case_spec.json': cfg['inputs']['case_spec.json'],
        'inputs/%s_import.sim' % BODY: cfg['inputs'][BODY + '_import.sim'],
        'config.json': sha256(config_path),
        'source_case_spec.json': sha256(SPEC),
    }
    for macro in MACROS:
        hashes['macros/' + macro] = cfg['macros'][macro]
    (STAGING / 'REVIEW-INPUTS.json').write_text(json.dumps({
        'run_id': RUN_ID,
        'staging_dir': str(STAGING.relative_to(ROOT)),
        'job_dir': args.job_dir,
        'review_json_path': str((STAGING / 'review.json').relative_to(ROOT)),
        'hashes': hashes,
        'audit_contract': {
            'kind': 'MESH_SPIKE_ONLY_NOT_FORMAL_EVIDENCE_AND_NOT_Q3',
            'budget': '<=%.0f s hard spike budget enforced by the runner (SPIKE_BUDGET_EXCEEDED)' % SPIKE_BUDGET_SECONDS,
            'steps': 'domain -> region/boundaries -> volume mesh -> read-only mesh audit; one STAR process per macro',
            'spike_mesh': dict(SPIKE_SURFACE,
                                mesh_prism_layers=derived['mesh_prism_layers'],
                                mesh_prism_first_layer_height_m=spec['quality']['mesh_prism_first_layer_height_m'],
                                mesh_prism_growth=spec['quality']['mesh_prism_growth'],
                                mesh_prism_total_thickness_m=spec['quality']['mesh_prism_total_thickness_m'],
                                note='prism recipe is the CaseSpec production recipe; only the surface/volume sizes are spike-coarse'),
            'no_physics_no_initialize_no_solve': True,
            'expected_input': 'the W1-audited import .sim (closed+manifold, one solid %s, unit m)' % BODY,
            'derived_spec_rule': cfg['derivation']['rule'],
        },
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'staging': str(STAGING), 'hashes': hashes}, indent=1))
    return 0


def verify_review(staging: Path | None = None) -> tuple[dict, Path]:
    staging = Path(staging) if staging is not None else STAGING
    cfg = json.loads((staging / 'config.json').read_text(encoding='utf-8'))
    review_path = staging / 'review.json'
    if not review_path.is_file():
        raise SystemExit('missing independent review.json in staging')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('approved') is not True or review.get('independent') is not True:
        raise SystemExit('independent approval required')
    expected = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    required = {RUNNER_REL, CONTROLLER_REL, 'inputs/case_spec.json',
                'inputs/%s_import.sim' % BODY, 'config.json', 'source_case_spec.json',
                *('macros/' + m for m in MACROS)}
    if not required <= set(expected):
        raise SystemExit('REVIEW-INPUTS.json is missing required keys: %r' % sorted(required - set(expected)))
    reviewed = review.get('hashes') or {}
    if set(reviewed) != set(expected):
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json: %r vs %r'
                         % (sorted(reviewed), sorted(expected)))
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS: %s != %s' % (rel, reviewed[rel], want))
    paths = {'inputs/case_spec.json': staging / 'case_spec.json',
             'inputs/%s_import.sim' % BODY: staging / (BODY + '_import.sim'),
             'config.json': staging / 'config.json',
             'source_case_spec.json': SPEC,
             RUNNER_REL: ROOT / RUNNER_REL,
             CONTROLLER_REL: ROOT / CONTROLLER_REL,
             **{'macros/' + m: staging / 'sources' / m for m in MACROS}}
    for rel, path in paths.items():
        if sha256(path) != reviewed[rel]:
            raise SystemExit('bytes changed after review: ' + rel)
    return cfg, review_path


def run(args) -> int:
    cfg, review_path = verify_review()
    b = Backend()
    job = cfg['job_dir']
    exists = b.ps('[pscustomobject]@{job_dir_exists=(Test-Path -LiteralPath %s)} | ConvertTo-Json' % ps_quote(job))
    if exists.get('job_dir_exists'):
        raise SystemExit('remote job dir already exists; refusing to reuse a run directory: ' + job)
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job))
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job + '\\sources'))
    b.scp(ROOT / RUNNER_REL, job + '\\run_crmhl_mesh_spike_adhoc.ps1')
    for name in ('config.json', 'review.json', 'case_spec.json', BODY + '_import.sim'):
        b.scp(STAGING / name, job + '\\' + name)
    for macro in MACROS:
        b.scp(STAGING / 'sources' / macro, job + '\\sources\\' + macro)
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(h), name)
        for name, h in (('config.json', sha256(STAGING / 'config.json')),
                        ('review.json', sha256(review_path)),
                        ('case_spec.json', sha256(STAGING / 'case_spec.json')),
                        (BODY + '_import.sim', sha256(STAGING / (BODY + '_import.sim'))))) +
        "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    controller_record = {'schema_version': 1, 'run_id': RUN_ID, 'spike_only': True,
                         'started_utc': datetime.now(timezone.utc).isoformat(),
                         'remote_verify': verify, 'review_sha256': sha256(review_path),
                         'config_sha256': sha256(STAGING / 'config.json')}
    try:
        controller_record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_crmhl_mesh_spike_adhoc.ps1'),
            ps_quote(job + '\\config.json'), ps_quote(sha256(STAGING / 'config.json')),
            ps_quote(sha256(review_path))), timeout=SPIKE_BUDGET_SECONDS + 900)
    except Exception as exception:
        controller_record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        required = ['run.json', 'external_domain_build.json', 'external_flow_region.json',
                    'external_flow_mesh.json', 'external_flow_mesh_quality.json']
        optional = ['external_domain_built.sim', 'external_flow_region.sim', 'external_flow_meshed.sim']
        controller_record['downloads'] = {}
        for name in required + optional:
            try:
                b.scp(EVIDENCE / name, job + '\\' + name, download=True)
                controller_record['downloads'][name] = {'bytes': (EVIDENCE / name).stat().st_size,
                                                        'sha256': sha256(EVIDENCE / name)}
            except Exception as exception:
                controller_record['downloads'][name] = {'error': str(exception)[-300:]}
        for sub in ('domain', 'region', 'mesh', 'audit'):
            try:
                b.scp(EVIDENCE / (sub + '-star.log'), job + '\\' + sub + '\\star.log', download=True)
            except Exception as exception:
                controller_record['downloads'][sub + '-star.log'] = {'error': str(exception)[-300:]}
        missing = [name for name in required
                   if 'error' in controller_record['downloads'].get(name, {'error': 'missing'})]
        if missing:
            controller_record['download_errors'] = missing
        try:
            audit = json.loads((EVIDENCE / 'external_flow_mesh_quality.json').read_text(encoding='utf-8'))
            mesh = json.loads((EVIDENCE / 'external_flow_mesh.json').read_text(encoding='utf-8'))
            controller_record['spike_readback'] = {
                'audit_status': audit.get('status'),
                'cells': mesh.get('cells'), 'mesh_seconds': mesh.get('mesh_seconds'),
                'min_cell_quality': audit.get('measured_cell_quality_min'),
                'min_face_validity': audit.get('measured_face_validity_min'),
                'min_volume_change': audit.get('measured_volume_change_min'),
                'min_wall_prism_layers': audit.get('measured_wall_prism_layers_min'),
                'wall_prism_free_area_fraction_max': audit.get('measured_wall_prism_free_area_fraction_max'),
                'quality_gate_passed': (audit.get('quality_gate') or {}).get('passed'),
            }
        except Exception as exception:                 # noqa: BLE001 - a missing record is not a pass
            controller_record['spike_readback'] = {'error': str(exception)[-300:]}
        (EVIDENCE / 'CONTROLLER-RUN.json').write_text(
            json.dumps(controller_record, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        if missing and sys.exc_info()[0] is None:
            raise SystemExit('required spike artefacts missing from the job root: %r' % missing)
    print(json.dumps({'status': controller_record.get('runner', {}).get('status'),
                      'spike_readback': controller_record.get('spike_readback'),
                      'evidence': str(EVIDENCE)}, indent=1))
    return 0


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='mode', required=True)
    p_prepare = sub.add_parser('prepare')
    p_prepare.add_argument('--job-dir', default=DEFAULT_JOB_DIR)
    sub.add_parser('run')
    args = parser.parse_args()
    return prepare(args) if args.mode == 'prepare' else run(args)


if __name__ == '__main__':
    sys.exit(main())
