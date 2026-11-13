"""Ad-hoc positive/negative trial for the read-only convergence audit macro.

Two STAR processes, one input, no solve and no mesh: the same 33 MB solved .sim is audited twice by the
CURRENT bytes of AuditExternalFlowConvergence.java.

  positive  the run's own stage-solve.json is in the session directory and its root output_sha256 equals
            the audited .sim, so the macro may certify read_only=true; expected verdict converged=true
            (the same run produced read_only=true/converged=true on 2026-10-06 with older macro bytes,
            so this reproduces the claim on today's bytes rather than inventing a baseline)
  negative  the same .sim with a stage record whose output_sha256 is a DIFFERENT file's real hash, which
            is the round-15 counterexample shape: a record that merely mentions a 64-hex value must not
            be accepted as proof of read-only binding. Expected verdict read_only=false, converged=false

Why this fixture is legitimate: nothing is fabricated. The two inputs are real files; the negative one is
declared as a constructed counterexample in its own body (counterexample_note) and is not a run record.

Two modes:
  prepare  build the staging directory and print the exact hash list an independent reviewer must pin.
  run      verify that review, upload, execute the review-gated runner, download the records.

Run: python3 scripts/run_convergence_audit.py prepare   |   run
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import Backend, ps_quote

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = 'conv-audit-20261104'
SOURCE_RUN = ROOT / 'test-evidence/static-cylinder-step/r25/run/static-cylinder-r25-20261006a'
SIM = SOURCE_RUN / 'external_flow_solved.sim'
SPEC = SOURCE_RUN / 'case_spec.json'
STAGE = SOURCE_RUN / 'stage-solve.json'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence' / RUN_ID
STAGING = EVIDENCE / 'staging'
MACRO = 'AuditExternalFlowConvergence.java'
RUNNER_REL = 'scripts/security/run_convergence_audit_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_convergence_audit.py'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\' + RUN_ID
MISMATCHED = 'stage-solve-mismatched.json'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def counterexample(stage_text: str, wrong_sha: str) -> str:
    """The round-15 shape: a record whose root output_sha256 is a real hash of a different file."""
    record = json.loads(stage_text)
    original = record.get('output_sha256')
    record['output_sha256'] = wrong_sha
    record['counterexample_note'] = (
        'CONSTRUCTED COUNTEREXAMPLE, NOT A RUN RECORD: output_sha256 was replaced (was %s) so that it '
        'equals the case_spec bytes instead of the audited .sim. It exists to prove the audit refuses to '
        'treat "a record that mentions a 64-hex value" as proof of read-only binding.' % original)
    return json.dumps(record, indent=1, ensure_ascii=False) + '\n'


def build(job_dir: str):
    if not SIM.is_file() or not SPEC.is_file() or not STAGE.is_file():
        raise SystemExit('source run is incomplete: ' + str(SOURCE_RUN.relative_to(ROOT)))
    STAGING.mkdir(parents=True, exist_ok=True)
    if not (STAGING / 'sources').is_dir():
        (STAGING / 'sources').mkdir(parents=True)
    shutil.copyfile(ROOT / 'macros' / MACRO, STAGING / 'sources' / MACRO)
    shutil.copyfile(SIM, STAGING / SIM.name)
    shutil.copyfile(SPEC, STAGING / 'case_spec.json')
    shutil.copyfile(STAGE, STAGING / 'stage-solve.json')
    spec_sha = sha256(STAGING / 'case_spec.json')
    # The source record carries a UTF-8 BOM (the Windows runners write one); the positive step keeps
    # those exact bytes, but re-serialising the counterexample must not fail on it.
    (STAGING / MISMATCHED).write_text(
        counterexample((STAGING / 'stage-solve.json').read_text(encoding='utf-8-sig'), spec_sha),
        encoding='utf-8')
    inputs = {name: sha256(STAGING / name)
              for name in ('case_spec.json', SIM.name, 'stage-solve.json', MISMATCHED)}
    job = job_dir.rstrip('\\')
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_convergence_audit_positive_negative_only_not_formal_pipeline_or_PAT',
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
        'macros': {MACRO: sha256(STAGING / 'sources' / MACRO)},
        'derivation': {
            'source_run': str(SOURCE_RUN.relative_to(ROOT)),
            'source_run_stage_solve_sha256': sha256(STAGE),
            'rule': 'no derivation: the .sim, its own solve stage record and its own case_spec are used as-is',
            'positive_basis': 'the same directory recorded read_only=true, converged=true on 2026-10-06',
        },
        'steps': [
            {'name': 'positive', 'macro': MACRO, 'input': SIM.name, 'env': {}, 'keep': True,
             'marker': 'EXTERNAL_FLOW_VALIDATION_COMPLETE',
             'records': ['external_flow_validation.json'],
             'expect': {'status': 'ok', 'read_only': 'True', 'converged': 'True'}},
            {'name': 'negative', 'macro': MACRO, 'input': SIM.name, 'keep': True,
             'env': {'EXTERNAL_FLOW_SOURCE_SOLVE_STAGE_RECORD': MISMATCHED},
             'marker': 'EXTERNAL_FLOW_VALIDATION_COMPLETE',
             'records': ['external_flow_validation.json'],
             'expect': {'status': 'ok', 'read_only': 'False', 'converged': 'False'}},
        ],
    }


def required_keys() -> set:
    return {RUNNER_REL, CONTROLLER_REL, 'config.json', 'macros/' + MACRO,
            'inputs/case_spec.json', 'inputs/' + SIM.name,
            'inputs/stage-solve.json', 'inputs/' + MISMATCHED,
            'source/stage-solve.json', 'source/case_spec.json'}


def prepare(args) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cfg = build(args.job_dir)
    config_path = STAGING / 'config.json'
    config_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {
        RUNNER_REL: sha256(ROOT / RUNNER_REL),
        CONTROLLER_REL: sha256(ROOT / CONTROLLER_REL),
        'config.json': sha256(config_path),
        'macros/' + MACRO: cfg['macros'][MACRO],
        'inputs/case_spec.json': cfg['inputs']['case_spec.json'],
        'inputs/' + SIM.name: cfg['inputs'][SIM.name],
        'inputs/stage-solve.json': cfg['inputs']['stage-solve.json'],
        'inputs/' + MISMATCHED: cfg['inputs'][MISMATCHED],
        'source/stage-solve.json': sha256(STAGE),
        'source/case_spec.json': sha256(SPEC),
    }
    missing = required_keys() - set(hashes)
    if missing:
        raise SystemExit('prepare did not produce every required key: %r' % sorted(missing))
    (STAGING / 'REVIEW-INPUTS.json').write_text(json.dumps({
        'run_id': RUN_ID,
        'staging_dir': str(STAGING.relative_to(ROOT)),
        'job_dir': args.job_dir,
        'review_json_path': str((STAGING / 'review.json').relative_to(ROOT)),
        'hashes': hashes,
        'audit_contract': {
            'macro': 'AuditExternalFlowConvergence.java (read-only: reloads the saved .sim, never iterates or saves)',
            'positive': 'own stage-solve.json present with output_sha256 == audited .sim -> read_only=true, converged=true',
            'negative': 'stage record %s has output_sha256 = case_spec hash -> read_only=false, converged=false' % MISMATCHED,
            'no_solve_no_mesh': True,
            'case_spec_frozen': False,
            'expected_values_source': 'test-evidence/static-cylinder-step/r25/run/static-cylinder-r25-20261006a',
        },
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'staging': str(STAGING), 'hashes': hashes}, indent=1))
    return 0


def verify_review(staging: Path | None = None):
    """Fail-closed: approved+independent, exact key set, exact hashes, bytes unchanged since review."""
    staging = Path(staging) if staging is not None else STAGING
    cfg = json.loads((staging / 'config.json').read_text(encoding='utf-8'))
    review_path = staging / 'review.json'
    if not review_path.is_file():
        raise SystemExit('missing independent review.json in staging')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('approved') is not True or review.get('independent') is not True:
        raise SystemExit('independent approval required')
    expected = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    if not required_keys() <= set(expected):
        raise SystemExit('REVIEW-INPUTS.json is missing required keys: %r' % sorted(required_keys() - set(expected)))
    reviewed = review.get('hashes') or {}
    if set(reviewed) != set(expected):
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json: %r vs %r'
                         % (sorted(reviewed), sorted(expected)))
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS: %s != %s' % (rel, reviewed[rel], want))
    paths = {RUNNER_REL: ROOT / RUNNER_REL, CONTROLLER_REL: ROOT / CONTROLLER_REL,
             'config.json': staging / 'config.json', 'macros/' + MACRO: staging / 'sources' / MACRO,
             'inputs/case_spec.json': staging / 'case_spec.json',
             'inputs/' + SIM.name: staging / SIM.name,
             'inputs/stage-solve.json': staging / 'stage-solve.json',
             'inputs/' + MISMATCHED: staging / MISMATCHED,
             'source/stage-solve.json': STAGE, 'source/case_spec.json': SPEC}
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
    b.scp(ROOT / RUNNER_REL, job + '\\run_convergence_audit_adhoc.ps1')
    for name in ('config.json', 'review.json', 'case_spec.json', SIM.name, 'stage-solve.json', MISMATCHED):
        b.scp(STAGING / name, job + '\\' + name)
    b.scp(STAGING / 'sources' / MACRO, job + '\\sources\\' + MACRO)
    names = ('config.json', 'review.json', 'case_spec.json', SIM.name, 'stage-solve.json', MISMATCHED)
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(sha256(STAGING / name)), name) for name in names)
        + "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    record = {'schema_version': 1, 'run_id': RUN_ID,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'remote_verify': verify, 'review_sha256': sha256(review_path),
              'config_sha256': sha256(STAGING / 'config.json')}
    try:
        record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_convergence_audit_adhoc.ps1'), ps_quote(job + '\\config.json'),
            ps_quote(sha256(STAGING / 'config.json')), ps_quote(sha256(review_path))), timeout=86400)
    except Exception as exception:
        record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        record['downloads'] = {}
        wanted = ['run.json', 'positive/external_flow_validation.json',
                  'negative/external_flow_validation.json',
                  'positive/star.log', 'negative/star.log',
                  'external_flow_validation_residuals.tsv']
        for name in wanted:
            try:
                b.scp(EVIDENCE / name.replace('/', '__'), job + '\\' + name.replace('/', '\\'), download=True)
                target = EVIDENCE / name.replace('/', '__')
                record['downloads'][name] = {'bytes': target.stat().st_size, 'sha256': sha256(target)}
            except Exception as exception:
                record['downloads'][name] = {'error': str(exception)[-300:]}
        (EVIDENCE / 'CONTROLLER-RUN.json').write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=1, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'run'))
    parser.add_argument('--job-dir', default=DEFAULT_JOB_DIR)
    args = parser.parse_args(argv)
    return prepare(args) if args.mode == 'prepare' else run(args)


if __name__ == '__main__':
    sys.exit(main())
