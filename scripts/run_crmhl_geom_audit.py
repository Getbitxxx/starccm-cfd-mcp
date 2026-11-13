"""Ad-hoc STAR geometry audit for the signed Phase 3 case uav-crm-hl-hlpw4 (P3.2 W1, first STAR gate).

Two modes:
  prepare  build the local staging directory (CaseSpec + pinned STEP + the two reviewed generic macros
           + config) and print the exact hash list the independent reviewer must pin.
  run      verify an independent review that pins those bytes, upload the staging to the single Windows
           STAR slot, execute the review-gated runner (two separate STAR processes: STEP import ->
           static-body audit), download the records and write the controller record.

The audit is read-only geometry: import the published CRM-HL STEP, require one closed manifold solid,
tessellate, measure SI bounds/volume against the CaseSpec expected values (tolerances DERIVED from the
CaseSpec geometry block: 5 mm bounds / 1% volume, both measured independently with SolidCraft/OCCT at
admission), no mesh, no physics, no solve.

This controller is new for Phase 3 but reuses, byte-for-byte, the two generic macros that were compiled
and executed on STAR 2502 in Phase 1/2 (ImportPropellerStep.java, AuditStaticBodyGeometry.java); the
runner is a minimal copy of the reviewed Phase-2 prop runner with only the record scope changed.
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
STEP = (ROOT / 'campaigns/uav-2026/evidence/phase3-p32-admission-20261031'
        / 'geometry' / 'HLPW-4_CRM-HL_40-37_Nominal_v2.stp')
RUN_ID = 'crmhl-geometry-audit-20261031b'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\crmhl-geom-audit-20261031b'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-crm-hl-hlpw4' / RUN_ID
STAGING = EVIDENCE / 'staging'
MACROS = ['ImportPropellerStep.java', 'AuditStaticBodyGeometry.java']
RUNNER_REL = 'scripts/security/run_crmhl_geom_audit_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_crmhl_geom_audit.py'
BODY = 'CRM_HL_SEMISPAN'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spec_text() -> str:
    """The CaseSpec is used AS IS (it already declares geometry.body_part_name); no derivation."""
    text = SPEC.read_text(encoding='utf-8')
    spec = json.loads(text)
    if spec['geometry'].get('body_part_name') != BODY:
        raise SystemExit('CaseSpec body_part_name is not %s' % BODY)
    if spec['model_length_unit'] != 'm':
        raise SystemExit('CaseSpec model_length_unit is not m')
    if not STEP.is_file():
        raise SystemExit('pinned STEP is missing: %s' % STEP.relative_to(ROOT))
    return text


def config(job_dir: str, staging: Path | None = None) -> dict:
    staging = Path(staging) if staging is not None else STAGING
    text = spec_text()
    spec = json.loads(text)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / 'sources').mkdir(exist_ok=True)
    (staging / 'case_spec.json').write_text(text, encoding='utf-8')
    shutil.copyfile(STEP, staging / (BODY + '.step'))
    for macro in MACROS:
        shutil.copyfile(ROOT / 'macros' / macro, staging / 'sources' / macro)
    inputs = {'case_spec.json': sha256(staging / 'case_spec.json'),
              BODY + '.step': sha256(staging / (BODY + '.step'))}
    macros = {m: sha256(staging / 'sources' / m) for m in MACROS}
    job = job_dir.rstrip('\\')
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_crmhl_geometry_audit_only_not_formal_pipeline_or_PAT',
        'run_id': RUN_ID,
        'job_dir': job,
        'star_launcher': 'C:\\Program Files\\Siemens\\20.02.008-R8\\STAR-CCM+20.02.008-R8\\star\\bin\\starccm+.bat',
        'star_version': '2502.0001',
        'logical_cores': 64,
        'memory_budget_gb': 239,
        'memory_reserve_gb': 16,
        'wall_clock_stop_enabled': False,
        'runner_sha256': sha256(ROOT / RUNNER_REL),
        'inputs': inputs,
        'macros': macros,
        'derivation': {
            'source_case_spec': 'cases/uav-crm-hl-hlpw4/case_spec.json',
            'source_case_spec_sha256': sha256(SPEC),
            'rule': 'none: the CaseSpec already declares geometry.body_part_name and model_length_unit=m',
        },
        # Directory convention (same as the reviewed Phase-2 prop runner): PROP_JOB_DIR is ALWAYS the
        # job root; macros write records and .sim into the root; per-step dirs hold macro + STAR log.
        'steps': [
            {'name': 'import', 'macro': 'ImportPropellerStep.java', 'input': None,
             'output': BODY + '_import.sim',
             'env': {'PROP_JOB_DIR': '{job_dir}', 'PROP_STEP_SHA256': inputs[BODY + '.step'],
                     'PROP_SPEC_SHA256': inputs['case_spec.json']},
             'marker': 'PROP_STEP_IMPORT_COMPLETE', 'records': [BODY + '_import.json']},
            {'name': 'audit', 'macro': 'AuditStaticBodyGeometry.java', 'input': BODY + '_import.sim',
             'env': {'PROP_JOB_DIR': '{job_dir}'},
             'marker': 'STATIC_BODY_GEOMETRY_AUDITED', 'records': ['static_body_audit.json']},
        ],
        # Tolerances are DERIVED FROM THE CASESPEC, never hard-coded here.
        'gates': {'part': BODY, 'model_length_unit': spec['model_length_unit'],
                  'bounds_tolerance_m': float(spec['geometry']['expected_bounds_tolerance_m']),
                  'volume_relative_tolerance': float(spec['geometry']['expected_volume_relative_tolerance'])},
    }


def prepare(args) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cfg = config(args.job_dir)
    config_path = STAGING / 'config.json'
    config_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {
        RUNNER_REL: sha256(ROOT / RUNNER_REL),
        CONTROLLER_REL: sha256(ROOT / CONTROLLER_REL),
        'inputs/case_spec.json': cfg['inputs']['case_spec.json'],
        'inputs/%s.step' % BODY: cfg['inputs'][BODY + '.step'],
        'macros/ImportPropellerStep.java': cfg['macros']['ImportPropellerStep.java'],
        'macros/AuditStaticBodyGeometry.java': cfg['macros']['AuditStaticBodyGeometry.java'],
        'config.json': sha256(config_path),
        'source_case_spec.json': sha256(SPEC),
    }
    (STAGING / 'REVIEW-INPUTS.json').write_text(json.dumps({
        'run_id': RUN_ID,
        'staging_dir': str(STAGING.relative_to(ROOT)),
        'job_dir': args.job_dir,
        'review_json_path': str((STAGING / 'review.json').relative_to(ROOT)),
        'hashes': hashes,
        'audit_contract': {
            'step1': 'ImportPropellerStep.java: one closed manifold solid named %s; preferred unit from '
                     'CaseSpec (m); no region/continuum/boundary/mesh/solution' % BODY,
            'step2': ('AuditStaticBodyGeometry.java: tessellate + SI bounds/volume vs CaseSpec expected_bounds_m '
                      '(+/-%.6g m, measured with SolidCraft/OCCT) and expected_volume_m3 (%.4g relative); '
                      'record static_body_audit.json status=ok'
                      % (cfg['gates']['bounds_tolerance_m'], cfg['gates']['volume_relative_tolerance'])),
            'no_mesh_no_physics_no_solve': True,
            'signed_scope': 'CRM-HL HLPW-4 case 1, no empennage (user D1 2026-10-31)',
            'case_spec_frozen': False,
            'expected_values_source': 'campaigns/uav-2026/evidence/phase3-p32-admission-20261031/ADMISSION.json '
                                      '(independent CAD measurement, SolidCraft/OCCT)',
            'geometry_sha256': cfg['inputs'][BODY + '.step'],
        },
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'staging': str(STAGING), 'hashes': hashes}, indent=1))
    return 0


def verify_review(staging: Path | None = None) -> tuple[dict, Path]:
    """Fail-closed review binding: approved+independent, exact key set, exact hashes, bytes unchanged.

    Extracted from run() so the contract is unit-testable without a STAR run (the 2026-10-31 independent
    review rejected the first staging because the runner's self-hash key was not in this pinned set).
    """
    staging = Path(staging) if staging is not None else STAGING
    cfg = json.loads((staging / 'config.json').read_text(encoding='utf-8'))
    review_path = staging / 'review.json'
    if not review_path.is_file():
        raise SystemExit('missing independent review.json in staging')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('approved') is not True or review.get('independent') is not True:
        raise SystemExit('independent approval required')
    expected = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    required_keys = {RUNNER_REL, CONTROLLER_REL, 'inputs/case_spec.json', 'inputs/%s.step' % BODY,
                     'macros/ImportPropellerStep.java', 'macros/AuditStaticBodyGeometry.java',
                     'config.json', 'source_case_spec.json'}
    if not required_keys <= set(expected):
        raise SystemExit('REVIEW-INPUTS.json is missing required keys: %r' % sorted(required_keys - set(expected)))
    reviewed = review.get('hashes') or {}
    if set(reviewed) != set(expected):
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json: %r vs %r'
                         % (sorted(reviewed), sorted(expected)))
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS: %s != %s' % (rel, reviewed[rel], want))
    paths = {'inputs/case_spec.json': staging / 'case_spec.json',
             'inputs/%s.step' % BODY: staging / (BODY + '.step'),
             'macros/ImportPropellerStep.java': staging / 'sources/ImportPropellerStep.java',
             'macros/AuditStaticBodyGeometry.java': staging / 'sources/AuditStaticBodyGeometry.java',
             'config.json': staging / 'config.json',
             'source_case_spec.json': SPEC,
             RUNNER_REL: ROOT / RUNNER_REL,
             CONTROLLER_REL: ROOT / CONTROLLER_REL}
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
    b.scp(ROOT / RUNNER_REL, job + '\\run_crmhl_geom_audit_adhoc.ps1')
    for name in ('config.json', 'review.json', 'case_spec.json', BODY + '.step'):
        b.scp(STAGING / name, job + '\\' + name)
    for macro in MACROS:
        b.scp(STAGING / 'sources' / macro, job + '\\sources\\' + macro)
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(h), name)
        for name, h in (('config.json', sha256(STAGING / 'config.json')),
                        ('review.json', sha256(review_path)),
                        ('case_spec.json', sha256(STAGING / 'case_spec.json')),
                        (BODY + '.step', sha256(STAGING / (BODY + '.step'))))) +
        "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    controller_record = {'schema_version': 1, 'run_id': RUN_ID,
                         'started_utc': datetime.now(timezone.utc).isoformat(),
                         'remote_verify': verify, 'review_sha256': sha256(review_path),
                         'config_sha256': sha256(STAGING / 'config.json')}
    try:
        controller_record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_crmhl_geom_audit_adhoc.ps1'),
            ps_quote(job + '\\config.json'), ps_quote(sha256(STAGING / 'config.json')),
            ps_quote(sha256(review_path))), timeout=86400)
    except Exception as exception:
        controller_record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        required = ['run.json', BODY + '_import.json', 'static_body_audit.json',
                    'static_body_tessellation.stl']
        optional = ['prop_step_import_failure.txt', BODY + '_import.sim']
        controller_record['downloads'] = {}
        for name in required + optional:
            try:
                b.scp(EVIDENCE / name, job + '\\' + name, download=True)
                controller_record['downloads'][name] = {'bytes': (EVIDENCE / name).stat().st_size,
                                                        'sha256': sha256(EVIDENCE / name)}
            except Exception as exception:
                controller_record['downloads'][name] = {'error': str(exception)[-300:]}
        for sub in ('import', 'audit'):
            try:
                b.scp(EVIDENCE / (sub + '-star.log'), job + '\\' + sub + '\\star.log', download=True)
            except Exception as exception:
                controller_record['downloads'][sub + '-star.log'] = {'error': str(exception)[-300:]}
        missing = [name for name in required
                   if 'error' in controller_record['downloads'].get(name, {'error': 'missing'})]
        if missing:
            controller_record['download_errors'] = missing
        # re-derive the acceptance from the DOWNLOADED record (not from the runner's word)
        try:
            audit = json.loads((EVIDENCE / 'static_body_audit.json').read_text(encoding='utf-8'))
            controller_record['audit_readback'] = {
                'status': audit.get('status'), 'is_closed': audit.get('is_closed'),
                'is_manifold': audit.get('is_manifold'),
                'audited_solid_count': audit.get('audited_solid_count'),
                'bounds_max_abs_error_m': audit.get('bounds_max_abs_error_m'),
                'volume_relative_deviation': audit.get('volume_relative_deviation'),
                'measured_bbox_si_m': audit.get('measured_bbox_si_m'),
                'measured_enclosed_volume_si_m3': audit.get('measured_enclosed_volume_si_m3'),
                'model_length_unit_readback': audit.get('model_length_unit_readback'),
            }
        except Exception as exception:                       # noqa: BLE001 - a missing record is not a pass
            controller_record['audit_readback'] = {'error': str(exception)[-300:]}
        (EVIDENCE / 'CONTROLLER-RUN.json').write_text(
            json.dumps(controller_record, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        if missing and sys.exc_info()[0] is None:
            raise SystemExit('required acceptance artefacts missing from the job root: %r' % missing)
    print(json.dumps({'status': controller_record.get('runner', {}).get('status'),
                      'audit_readback': controller_record.get('audit_readback'),
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
