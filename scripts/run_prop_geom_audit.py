"""Ad-hoc STAR geometry audit for the Phase 2 propeller (P2.3, first STAR gate).

Two modes:
  prepare  build the local staging directory (derived audit CaseSpec + STEP + macros + config) and
           print the exact hash list the independent reviewer must pin.
  run      verify an independent review that pins those bytes, upload the staging directory to the
           single Windows STAR slot, execute the review-gated runner (two separate STAR processes:
           STEP import -> static-body audit), download the records and write the controller record.

The audit itself is read-only geometry: import the rev2 STEP, require one closed manifold solid,
tessellate, measure SI bounds/volume against the CaseSpec expected values (tolerances DERIVED from the
CaseSpec geometry block, currently 0.2 mm / 1% measurement basis), no mesh,
no physics, no solve.
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
CASE = ROOT / 'cases/uav-prop-apc10x7e'
SPEC = CASE / 'case_spec.json'
STEP = CASE / 'geometry' / 'APC_10x7E.step'
RUN_ID = 'geometry-audit-20261028a'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\prop-geom-audit-20261028a'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e' / RUN_ID
STAGING = EVIDENCE / 'staging'
MACROS = ['ImportPropellerStep.java', 'AuditStaticBodyGeometry.java']
RUNNER_REL = 'scripts/security/run_prop_geom_audit_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_prop_geom_audit.py'
BODY = 'APC10X7E_ROTOR'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def derived_spec_text() -> tuple[str, str]:
    """Signed CaseSpec + the one key the Phase-1 import/audit contract needs (body_part_name).

    The signed CaseSpec must not be edited for a run-time contract key (that would invalidate its
    review pin), so the audit uses this deterministic derivation: insert exactly one top-level
    "body_part_name" right after the opening brace and record both hashes.
    """
    text = SPEC.read_text(encoding='utf-8')
    if '"body_part_name"' in text:
        raise SystemExit('CaseSpec already declares body_part_name; derivation rule no longer applies')
    marker = '"schema_version"'
    index = text.index(marker)
    insert = '"body_part_name": "%s",\n  ' % BODY
    derived = text[:index] + insert + text[index:]
    # parse-check
    json.loads(derived)
    return derived, text


def config(job_dir: str) -> dict:
    spec_text, original = derived_spec_text()
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / 'sources').mkdir(exist_ok=True)
    (STAGING / 'case_spec.json').write_text(spec_text, encoding='utf-8')
    shutil.copyfile(STEP, STAGING / (BODY + '.step'))
    for macro in MACROS:
        shutil.copyfile(ROOT / 'macros' / macro, STAGING / 'sources' / macro)
    inputs = {'case_spec.json': sha256(STAGING / 'case_spec.json'),
              BODY + '.step': sha256(STAGING / (BODY + '.step'))}
    macros = {m: sha256(STAGING / 'sources' / m) for m in MACROS}
    job = job_dir.rstrip('\\')
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_prop_geometry_audit_only_not_formal_pipeline_or_PAT',
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
            'source_case_spec': 'cases/uav-prop-apc10x7e/case_spec.json',
            'source_case_spec_sha256': sha256(SPEC),
            'rule': 'insert one top-level "body_part_name": "%s" after the opening brace; no other change' % BODY,
            'original_case_spec_sha256': hashlib.sha256(original.encode()).hexdigest(),
        },
        # Directory convention (review-2g B1): PROP_JOB_DIR is ALWAYS the job root; macros write their
        # records and .sim into the root; per-step dirs hold only the copied macro and the STAR log.
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
        # The runner gates these fields itself; the tolerances are DERIVED FROM THE CASESPEC (review-2l):
        # hard-coding them let a re-based measurement tolerance (volume 0.2% -> 1%) pass the audit macro and
        # then fail the runner gate after both STAR processes.
        'gates': {'part': BODY, 'model_length_unit': json.loads(spec_text)['model_length_unit'],
                  'bounds_tolerance_m': float(json.loads(spec_text)['geometry']['expected_bounds_tolerance_m']),
                  'volume_relative_tolerance': float(json.loads(spec_text)['geometry']['expected_volume_relative_tolerance']) },
    }


def prepare(args) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cfg = config(args.job_dir)
    config_path = STAGING / 'config.json'
    config_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {
        'scripts/security/run_prop_geom_audit_adhoc.ps1': sha256(ROOT / RUNNER_REL),
        'scripts/run_prop_geom_audit.py': sha256(ROOT / CONTROLLER_REL),
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
            'step1': 'ImportPropellerStep.java: one closed manifold solid named %s; preferred unit from CaseSpec' % BODY,
            'step2': ('AuditStaticBodyGeometry.java: tessellate + SI bounds/volume vs CaseSpec expected_bounds_m '
                      '(+/-%.6g m) and expected_volume_m3 (%.4g relative); record static_body_audit.json status=ok'
                      % (cfg['gates']['bounds_tolerance_m'], cfg['gates']['volume_relative_tolerance'])),
            'no_mesh_no_physics_no_solve': True,
        },
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'staging': str(STAGING), 'hashes': hashes}, indent=1))
    return 0


def run(args) -> int:
    cfg = json.loads((STAGING / 'config.json').read_text(encoding='utf-8'))
    review_path = STAGING / 'review.json'
    if not review_path.is_file():
        raise SystemExit('missing independent review.json in staging')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('approved') is not True or review.get('independent') is not True:
        raise SystemExit('independent approval required')
    expected = json.loads((STAGING / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    reviewed = review.get('hashes') or {}
    # strict equality (review-2g): every REVIEW-INPUTS key must be present with the same hash; extras rejected
    if set(reviewed) != set(expected):
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json: %r vs %r'
                         % (sorted(reviewed), sorted(expected)))
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS: %s != %s' % (rel, reviewed[rel], want))
    paths = {'inputs/case_spec.json': STAGING / 'case_spec.json',
             'inputs/%s.step' % BODY: STAGING / (BODY + '.step'),
             'macros/ImportPropellerStep.java': STAGING / 'sources/ImportPropellerStep.java',
             'macros/AuditStaticBodyGeometry.java': STAGING / 'sources/AuditStaticBodyGeometry.java',
             'config.json': STAGING / 'config.json',
             'source_case_spec.json': SPEC,
             'scripts/security/run_prop_geom_audit_adhoc.ps1': ROOT / RUNNER_REL}
    for rel, path in paths.items():
        if sha256(path) != reviewed[rel]:
            raise SystemExit('bytes changed after review: ' + rel)

    b = Backend()
    job = cfg['job_dir']
    existing = b.ps('[pscustomobject]@{job_dir_exists=(Test-Path -LiteralPath %s)} | ConvertTo-Json' % ps_quote(job))
    if existing.get('job_dir_exists'):
        raise SystemExit('remote job dir already exists; refusing to reuse a run directory: ' + job)
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job))
    # Create the sources upload directory BEFORE scp: a missing parent directory made the first
    # execution attempt fail after the job dir existed (measured 2026-10-28, no STAR started).
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job + '\\sources'))
    uploads = [STAGING / 'config.json', STAGING / 'review.json', STAGING / 'case_spec.json',
               STAGING / (BODY + '.step'), STAGING / 'sources/ImportPropellerStep.java',
               STAGING / 'sources/AuditStaticBodyGeometry.java', ROOT / RUNNER_REL]
    b.scp(ROOT / RUNNER_REL, job + '\\run_prop_geom_audit_adhoc.ps1')
    for path in uploads:
        if path == ROOT / RUNNER_REL:
            continue
        rel = path.relative_to(STAGING).as_posix() if STAGING in path.parents else path.name
        b.scp(path, job + '\\' + rel.replace('/', '\\'))
    check = {name: sha256(STAGING / name) if (STAGING / name).is_file() else None
             for name in ('config.json', 'review.json', 'case_spec.json', BODY + '.step')}
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(h), name) for name, h in check.items() if h) +
        "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    controller_record = {'schema_version': 1, 'run_id': RUN_ID, 'started_utc': datetime.now(timezone.utc).isoformat(),
                         'remote_verify': verify, 'review_sha256': sha256(review_path),
                         'config_sha256': sha256(STAGING / 'config.json')}
    try:
        result = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_prop_geom_audit_adhoc.ps1'),
            ps_quote(job + '\\config.json'), ps_quote(sha256(STAGING / 'config.json')),
            ps_quote(sha256(review_path))), timeout=86400)
        controller_record['runner'] = result
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
        (EVIDENCE / 'CONTROLLER-RUN.json').write_text(
            json.dumps(controller_record, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        if missing and sys.exc_info()[0] is None:
            raise SystemExit('required acceptance artefacts missing from the job root: %r' % missing)

    print(json.dumps({'status': controller_record.get('runner', {}).get('status'),
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
