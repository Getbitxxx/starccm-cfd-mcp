"""Ad-hoc STAR mesh SPIKE: combined recipe from the two measured exponents.

Measured on 2026-11-02 from five arms (all logs in the campaign evidence tree):
    VC ~ BL_total^0.94   (c 4.0 mm -> 2.27e-3 vs m 5.2687 mm -> 2.94e-3)
    VC ~ core^-0.77      (no-local-boxes 50 mm -> 7.99e-4 vs te-prism 10 mm -> 2.766e-3)
First-layer height was measured to have NO effect (2.8222e-5 -> 6.0e-5 gave 2.76644e-3 vs 2.76639e-3)
because the BL total and layer count were held fixed.

This arm therefore changes exactly two knobs:
    mesh_nearfield_cell_size_m   0.01  -> 0.005
    quality.mesh_prism_total_thickness_m  5.2687e-3 -> 8.0e-3
Prediction: VC ~ 7.0e-3 (1.4x margin over the 0.005 gate), ~31M cells.
Original header follows.

Route A-prime (user 2026-11-02): the 0.355-1.335 mm TE caps are NOT addressable PartSurfaces after the CAD
import (measured 2026-11-02, PROBE-2-route-A.md), so the TE prism stack is overridden through VOLUME controls
(VolumeControlPrismsOption=CUSTOMIZE) along the two TE lines instead. Everything else is the previous recipe.

Two modes:
  prepare  build the local staging (CaseSpec + spike mesh keys + the CAD-authored closed fluid-domain STEP +
           four pinned macros + config) and print the hash list the independent reviewer pins.
  run      verify an independent review of those exact bytes, upload, execute the review-gated runner
           (one STAR process per macro: import fluid domain -> named region/boundaries -> volume mesh ->
           read-only mesh audit) and download the records.

This is an ISOLATED FEASIBILITY TRIAL, not formal evidence: it is not a Q3 certificate and its mesh is not
the production mesh.  It measures whether this geometry can be domain-built and volume-meshed, how many
cells and how long a coarse recipe takes, and what the realised prism/quality minima are.  The runner
enforces the F2 rule (<=40 min) as a hard spike budget; exceeding it stops the owned STAR tree and writes
SPIKE_BUDGET_EXCEEDED.

The import and mesh/audit macros are already used by the formal static external-flow chain; BuildNamedFluidRegion
is the new named-region macro (its first execution is this isolated trial) and BuildExternalFlowMesh was adapted
to accept the imported CadPart/MeshPart domain. Their exact bytes are pinned here.
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
CASE = ROOT / 'cases/uav-trapwing-hlpw1'
SPEC = CASE / 'case_spec.json'
FLUID_STEP = (ROOT / 'cases/uav-trapwing-hlpw1/geometry/TRAPWING_FLUID_DOMAIN.step')
RUN_ID = 'trapwing-core005-bl8mm-20261102'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\trapwing-core005-bl8mm-20261102'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1' / RUN_ID
STAGING = EVIDENCE / 'staging'
MACROS = ['ImportPropellerStep.java', 'BuildNamedFluidRegion.java',
          'BuildExternalFlowMesh.java', 'AuditBadMeshCellBounds.java', 'AuditExternalFlowMesh.java']
RUNNER_REL = 'scripts/security/run_trapwing_volume_fix_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_trapwing_core005_bl8mm.py'
BODY = 'AUTO_FREE_AIR_DOMAIN'
SPIKE_BUDGET_SECONDS = 0.0  # user 2026-10-31: 40 min cap removed; run as long as needed, no wall-clock limit

# SPIKE-ONLY coarse surface/volume sizes.  The prism recipe is NOT overridden: first layer, growth and total
# thickness stay the CaseSpec's production values (quality.mesh_prism_*), and the flat layer count added below is
# the CaseSpec target, so the recipe is one consistent 20-layer stack.  Only keys that do not already exist in the
# CaseSpec are added (the macros throw on duplicate fields).
SPIKE_SURFACE = {
    'mesh_base_size_m': 2.0,
    'mesh_body_target_size_m': 0.05,
    'mesh_body_minimum_size_m': 0.005,
    # local refinement around the bad-cell cluster (x 0.159..1.830, y -2.159..-0.221, z -0.0996..0.0237)
    'mesh_nearfield_bounds_m': [0.05, -2.25, -0.20, 1.95, -0.15, 0.10],
    'mesh_nearfield_cell_size_m': 0.005,   # 2026-11-02: 0.01 -> 0.005, combined recipe (VC ~ core^-0.77)
    'mesh_wake_bounds_m': [-0.45, -2.25, -0.30, 4.0, 0.0, 0.30],
    'mesh_wake_cell_size_m': 0.03,
    # Trailing-edge prism override (volume controls along the two TE lines).
    # Axis endpoints are the CAD TE-cap face bounding boxes (TRAPWING_BODY.step, FreeCAD 1.1.3, 2026-11-02),
    # extended 30 mm past both ends; a 4 mm tube covers about 0.7% of the 7.42 m2 wall (strip width 2R over
    # 2.47 m + 2.23 m of TE line), inside the CaseSpec prism-area exception (1%).
    'mesh_te_prism_layers': 3,
    'mesh_te_prism_total_thickness_m': 1.0e-4,
    'mesh_te_prism_radius_m': 0.004,
    'mesh_te_prism_axis_1_m': [0.1084, -2.1870, -0.0062, 1.4994, -0.1505, 0.0062],
    'mesh_te_prism_axis_2_m': [1.1244, -2.2197, 0.0082, 1.8930, -0.1270, 0.0242],
}
EXPECTED_BOUNDARIES = ['AUTO_INLET', 'AUTO_OUTLET', 'AUTO_SYMMETRY', 'AUTO_FARFIELD_Y_MIN',
                       'AUTO_FARFIELD_Z_MIN', 'AUTO_FARFIELD_Z_MAX', 'AUTO_TRAPWING_BODY_WALL']


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def derived_spec() -> tuple[dict, dict]:
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    derived = json.loads(json.dumps(spec))
    # This spike imports the CAD-authored FLUID domain, not the aircraft body: the import macro renames the
    # single closed fluid solid to AUTO_FREE_AIR_DOMAIN, which is the part BuildNamedFluidRegion and
    # BuildExternalFlowMesh expect. These two nested overrides are declared in the derivation rule.
    derived['geometry']['body_part_name'] = BODY
    derived['geometry']['body_parts'] = [BODY]
    added = dict(SPIKE_SURFACE)
    # user 2026-10-31: prism layer reduction is acceptable; spike tests 12 layers / min 10 before any CaseSpec edit
    derived['quality']['mesh_prism_layers_target'] = 12
    derived['quality']['mesh_min_wall_prism_layers'] = 10
    added['mesh_prism_layers'] = 12
    # Combined recipe (2026-11-02): BL total 5.2687e-3 -> 8.0e-3 m (VC ~ BL_total^0.94).
    derived['quality']['mesh_prism_total_thickness_m'] = 8.0e-3
    # Prism total stays at the CaseSpec production value 5.2687e-3 m (V2's 4.0e-3 was measured WORSE: VC 2.27e-3).
    added['body_wall_boundary_name'] = spec['boundary_roles']['body_wall']
    added['spike_only'] = True
    for key, value in added.items():
        if key in derived:
            raise SystemExit('derived spike spec would duplicate CaseSpec key: ' + key)
        derived[key] = value
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
    shutil.copyfile(FLUID_STEP, STAGING / 'AUTO_FREE_AIR_DOMAIN.step')
    for macro in MACROS:
        shutil.copyfile(ROOT / 'macros' / macro, STAGING / 'sources' / macro)
    inputs = {'case_spec.json': sha256(STAGING / 'case_spec.json'),
              'AUTO_FREE_AIR_DOMAIN.step': sha256(STAGING / 'AUTO_FREE_AIR_DOMAIN.step')}
    macros = {m: sha256(STAGING / 'sources' / m) for m in MACROS}
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_trapwing_core005_bl8mm_only_not_formal_evidence_or_Q3',
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
            'source_case_spec': 'cases/uav-trapwing-hlpw1/case_spec.json',
            'source_case_spec_sha256': sha256(SPEC),
            'rule': ('set geometry.body_part_name=geometry.body_parts=[AUTO_FREE_AIR_DOMAIN] for the imported fluid domain, '
                     'declare quality.mesh_prism_layers_target %r->%r, quality.mesh_min_wall_prism_layers %r->%r, '
                     'quality.mesh_prism_total_thickness_m %r->%r and mesh_nearfield_cell_size_m 0.01->0.005 '
                     '(user 2026-10-31 authorised a 12-layer / min-10 stack), and add the spike-only unique keys %r '
                     '+ spike_only=true; every other CaseSpec field, including the domain, the geometry expectations '
                     'and every hard quality threshold, is unchanged'
                     % (spec['quality']['mesh_prism_layers_target'], derived['quality']['mesh_prism_layers_target'],
                        spec['quality']['mesh_min_wall_prism_layers'], derived['quality']['mesh_min_wall_prism_layers'],
                        spec['quality']['mesh_prism_total_thickness_m'], derived['quality']['mesh_prism_total_thickness_m'],
                        sorted(added))),
            'overrides': {'quality.mesh_prism_layers_target': derived['quality']['mesh_prism_layers_target'],
                          'quality.mesh_prism_total_thickness_m': derived['quality']['mesh_prism_total_thickness_m'],
                          'mesh_nearfield_cell_size_m': 0.005,
                          'quality.mesh_min_wall_prism_layers': derived['quality']['mesh_min_wall_prism_layers'],
                          'geometry.body_part_name': BODY,
                          'geometry.body_parts': [BODY]},
        },
        'steps': [
            {'name': 'import', 'macro': 'ImportPropellerStep.java', 'input': None,
             'output': BODY + '_import.sim',
             'env': {'PROP_JOB_DIR': '{job_dir}', 'PROP_STEP_SHA256': inputs['AUTO_FREE_AIR_DOMAIN.step'],
                     'PROP_SPEC_SHA256': inputs['case_spec.json']},
             'marker': 'PROP_STEP_IMPORT_COMPLETE', 'records': [BODY + '_import.json']},
            {'name': 'region', 'macro': 'BuildNamedFluidRegion.java', 'input': BODY + '_import.sim',
             'output': 'named_region.sim',
             'env': {}, 'marker': 'NAMED_FLUID_REGION_BUILD_COMPLETE', 'records': ['named_fluid_region.json']},
            {'name': 'mesh', 'macro': 'BuildExternalFlowMesh.java', 'input': 'named_region.sim',
             'output': 'external_flow_meshed.sim',
             'env': {}, 'marker': 'EXTERNAL_FLOW_MESH_BUILT', 'records': ['external_flow_mesh.json']},
            {'name': 'localize', 'macro': 'AuditBadMeshCellBounds.java', 'input': 'external_flow_meshed.sim',
             'output': None,
             'env': {}, 'marker': 'BAD_MESH_CELL_BOUNDS_COMPLETE', 'records': []},
            {'name': 'audit', 'macro': 'AuditExternalFlowMesh.java', 'input': 'external_flow_meshed.sim',
             'output': None,
             'env': {}, 'marker': 'EXTERNAL_FLOW_MESH_QUALITY_AUDITED', 'records': ['external_flow_mesh_quality.json']},
        ],
        'gates': {
            'spike_budget_seconds': SPIKE_BUDGET_SECONDS,
            'part': BODY,
            'model_length_unit': derived['model_length_unit'],
            'wall_name': derived['body_wall_boundary_name'],
            'boundary_names': EXPECTED_BOUNDARIES,
            'mesh_cells_min': 1,
            'prism_layers': int(derived['mesh_prism_layers']),
            'cell_quality_min': float(spec['quality']['mesh_min_cell_quality']),
            'face_validity_min': float(spec['quality']['mesh_min_face_validity']),
            'volume_change_min': float(spec['quality']['mesh_min_volume_change']),
            'wall_prism_layers_min': int(derived['quality']['mesh_min_wall_prism_layers']),
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
        'inputs/AUTO_FREE_AIR_DOMAIN.step': cfg['inputs']['AUTO_FREE_AIR_DOMAIN.step'],
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
            'budget': '40 min cap removed by user instruction 2026-10-31: no wall-clock limit, run as long as needed; only resource/health gates apply (no time-based stop)',
            'steps': 'import fluid domain -> named region/boundaries -> volume mesh -> read-only mesh audit; one STAR process per macro',
            'spike_mesh': dict(SPIKE_SURFACE,
                                mesh_prism_layers=derived['mesh_prism_layers'],
                                mesh_prism_first_layer_height_m=spec['quality']['mesh_prism_first_layer_height_m'],
                                mesh_prism_growth=spec['quality']['mesh_prism_growth'],
                                mesh_prism_total_thickness_m=derived['quality']['mesh_prism_total_thickness_m'],
                                note=('BL total 8.0e-3 m and nearfield cell size 0.005 m are DECLARED spike overrides (2026-11-02 combined recipe from the measured exponents); layer count 12 is the declared spike override 20->12; first layer 2.8222e-5 is the CaseSpec value')),
            'no_physics_no_initialize_no_solve': True,
            'expected_input': 'the CAD-authored closed fluid-domain STEP (one closed manifold solid, %s after import, unit m)' % BODY,
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
                'inputs/AUTO_FREE_AIR_DOMAIN.step', 'config.json', 'source_case_spec.json',
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
             'inputs/AUTO_FREE_AIR_DOMAIN.step': staging / 'AUTO_FREE_AIR_DOMAIN.step',
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
    b.scp(ROOT / RUNNER_REL, job + '\\run_trapwing_named_region_spike_adhoc.ps1')
    for name in ('config.json', 'review.json', 'case_spec.json', 'AUTO_FREE_AIR_DOMAIN.step'):
        b.scp(STAGING / name, job + '\\' + name)
    for macro in MACROS:
        b.scp(STAGING / 'sources' / macro, job + '\\sources\\' + macro)
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(h), name)
        for name, h in (('config.json', sha256(STAGING / 'config.json')),
                        ('review.json', sha256(review_path)),
                        ('case_spec.json', sha256(STAGING / 'case_spec.json')),
                        ('AUTO_FREE_AIR_DOMAIN.step', sha256(STAGING / 'AUTO_FREE_AIR_DOMAIN.step')))) +
        "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    controller_record = {'schema_version': 1, 'run_id': RUN_ID, 'spike_only': True,
                         'started_utc': datetime.now(timezone.utc).isoformat(),
                         'remote_verify': verify, 'review_sha256': sha256(review_path),
                         'config_sha256': sha256(STAGING / 'config.json')}
    try:
        controller_record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_trapwing_named_region_spike_adhoc.ps1'),
            ps_quote(job + '\\config.json'), ps_quote(sha256(STAGING / 'config.json')),
            ps_quote(sha256(review_path))), timeout=604800)  # observation only (7 d); no wall-clock stop; remote run continues
    except Exception as exception:
        controller_record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        required = ['run.json', BODY + '_import.json', 'named_fluid_region.json',
                    'external_flow_mesh.json', 'bad_mesh_cell_bounds.tsv', 'external_flow_mesh_quality.json']
        optional = [BODY + '_import.sim', 'named_region.sim', 'external_flow_meshed.sim']
        controller_record['downloads'] = {}
        for name in required + optional:
            try:
                b.scp(EVIDENCE / name, job + '\\' + name, download=True)
                controller_record['downloads'][name] = {'bytes': (EVIDENCE / name).stat().st_size,
                                                        'sha256': sha256(EVIDENCE / name)}
            except Exception as exception:
                controller_record['downloads'][name] = {'error': str(exception)[-300:]}
        for sub in ('import', 'region', 'mesh', 'localize', 'audit'):
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
            region = json.loads((EVIDENCE / 'named_fluid_region.json').read_text(encoding='utf-8'))
            controller_record['spike_readback'] = {
                'audit_status': audit.get('status'),
                'cells': mesh.get('cells'), 'mesh_seconds': mesh.get('mesh_seconds'),
                'min_cell_quality': audit.get('measured_cell_quality_min'),
                'min_face_validity': audit.get('measured_face_validity_min'),
                'min_volume_change': audit.get('measured_volume_change_min'),
                'min_wall_prism_layers': audit.get('measured_wall_prism_layers_min'),
                'wall_prism_free_area_fraction_max': audit.get('measured_wall_prism_free_area_fraction_max'),
                'quality_gate_passed': (audit.get('quality_gate') or {}).get('passed'),
                'region_status': region.get('status'),
                'boundary_names': [b.get('name') for b in region.get('boundaries', [])],
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
