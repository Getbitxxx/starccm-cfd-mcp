"""Ad-hoc proof that RunExternalFlowSolve's DEFAULT iteration cap is 1200, on current bytes.

The 2026-11-03 deployment review rejected this change twice with the same objection: `cfd-1200-20261101`
shows 1200 iterations running to cap, but that record was written by the macro itself and cannot show
which BYTES ran, nor whether the cap came from the default or from an explicit
`EXTERNAL_FLOW_MAX_ITERATIONS`. Both questions are answerable only with a run this controller stages:

  * the macro under test is `fcccfa4:macros/RunExternalFlowSolve.java` (sha256 ac64b70e…), staged from
    git into `sources/` so the bytes are pinned by the review and re-verified on Windows before launch;
  * the step declares NO `EXTERNAL_FLOW_MAX_ITERATIONS`, so the cap can only come from the macro default;
  * the record must report `iteration_cap: 1200` and `iterations_run: 1200` with `status: ok`, and the
    runner refuses the trial if it does not.

It runs on `static-cylinder-step/r25` (15 MB meshed, its own case_spec): the macro resolves CaseSpec
fields with a whole-text regex, so `flow.velocity_m_s` under a nested key is found exactly as a
top-level one would be.

Modes:
  prepare  stage + print the hash list an independent reviewer must pin
  run      verify that review, upload, execute, download

Run: python3 scripts/run_solve_default_1200.py prepare | run
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import Backend, ps_quote

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = 'solve-default-1200-20261104'
SOURCE_RUN = ROOT / 'test-evidence/static-cylinder-step/r25/run/static-cylinder-r25-20261006a'
SIM = SOURCE_RUN / 'external_flow_meshed.sim'
SPEC = SOURCE_RUN / 'case_spec.json'
MACRO = 'RunExternalFlowSolve.java'
MACRO_COMMIT = 'fcccfa4'
MACRO_SHA = 'ac64b70e74b80031cd9fa59579fc23945c3c3ae64d6e1b35caa4ad005115add7'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence' / RUN_ID
STAGING = EVIDENCE / 'staging'
RUNNER_REL = 'scripts/security/run_convergence_audit_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_solve_default_1200.py'
DEFAULT_JOB_DIR = 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\' + RUN_ID


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def macro_bytes() -> bytes:
    blob = subprocess.run(['git', 'show', '%s:macros/%s' % (MACRO_COMMIT, MACRO)],
                          cwd=ROOT, capture_output=True, check=True).stdout
    got = hashlib.sha256(blob).hexdigest()
    if got != MACRO_SHA:
        raise SystemExit('candidate macro bytes changed: %s != %s' % (got, MACRO_SHA))
    return blob


def build(job_dir: str) -> dict:
    if not SIM.is_file() or not SPEC.is_file():
        raise SystemExit('source fixture incomplete: ' + str(SOURCE_RUN.relative_to(ROOT)))
    STAGING.mkdir(parents=True, exist_ok=True)
    if not (STAGING / 'sources').is_dir():
        (STAGING / 'sources').mkdir(parents=True)
    (STAGING / 'sources' / MACRO).write_bytes(macro_bytes())
    shutil.copyfile(SIM, STAGING / 'external_flow_meshed.sim')
    shutil.copyfile(SPEC, STAGING / 'case_spec.json')
    inputs = {name: sha256(STAGING / name) for name in ('external_flow_meshed.sim', 'case_spec.json')}
    job = job_dir.rstrip('\\')
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_solve_default_cap_1200_only_not_formal_pipeline_or_PAT',
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
            'macro_source': '%s:macros/%s' % (MACRO_COMMIT, MACRO),
            'macro_sha256': MACRO_SHA,
            'fixture': str(SOURCE_RUN.relative_to(ROOT)),
            'rule': 'no derivation: the meshed .sim and its own case_spec are used as-is',
            'cap_source': 'the macro default: no EXTERNAL_FLOW_MAX_ITERATIONS is declared for this step',
        },
        'steps': [
            {'name': 'solve', 'macro': MACRO, 'input': 'external_flow_meshed.sim',
             # Deliberately empty: nothing here may set the iteration cap.
             'env': {}, 'keep': True, 'output': 'external_flow_solved.sim',
             'marker': 'EXTERNAL_FLOW_SOLVE_COMPLETE',
             'records': ['external_flow_solve.json'],
             # A CSV is bytes, not a record: the runner must not try to parse it as JSON.
             'artifacts': ['external_flow_solve_history.csv'],
             'expect': {'iteration_cap': '1200', 'iterations_run': '1200'}},
        ],
        # Every switch the macro reads through System.getenv. The runner refuses the trial if any of
        # them is set in Process, User or Machine scope, and records the readings either way: an empty
        # `env` block alone would not distinguish "the default" from "the host happens to define 1200".
        'env_must_be_unset': [
            'EXTERNAL_FLOW_MAX_ITERATIONS', 'EXTERNAL_FLOW_CHUNK', 'EXTERNAL_FLOW_BUDGET_SECONDS',
            'EXTERNAL_FLOW_CHECKPOINT_EVERY_CHUNKS', 'EXTERNAL_FLOW_EXPECTED_START_ITERATION',
            'EXTERNAL_FLOW_BUDGET_BASIS',
        ],
    }


def required_keys() -> set:
    return {RUNNER_REL, CONTROLLER_REL, 'config.json', 'macros/' + MACRO,
            'inputs/external_flow_meshed.sim', 'inputs/case_spec.json',
            'source/external_flow_meshed.sim', 'source/case_spec.json'}


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
        'inputs/external_flow_meshed.sim': cfg['inputs']['external_flow_meshed.sim'],
        'inputs/case_spec.json': cfg['inputs']['case_spec.json'],
        'source/external_flow_meshed.sim': sha256(SIM),
        'source/case_spec.json': sha256(SPEC),
    }
    missing = required_keys() - set(hashes)
    if missing:
        raise SystemExit('prepare is missing required keys: %r' % sorted(missing))
    (STAGING / 'REVIEW-INPUTS.json').write_text(json.dumps({
        'run_id': RUN_ID,
        'staging_dir': str(STAGING.relative_to(ROOT)),
        'job_dir': args.job_dir,
        'review_json_path': str((STAGING / 'review.json').relative_to(ROOT)),
        'hashes': hashes,
        'contract': {
            'question_1': 'which bytes ran -> the runner verifies sources/%s against %s on Windows' % (MACRO, MACRO_SHA),
            'question_2': 'is the cap the default -> the step declares no EXTERNAL_FLOW_MAX_ITERATIONS',
            'expect': 'external_flow_solve.json reports iteration_cap=1200, iterations_run=1200, status=ok',
            'scope_limit': 'one steady solve on a 15 MB cylinder; not convergence, not E, not a stage acceptance',
        },
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'staging': str(STAGING), 'hashes': hashes}, indent=1))
    return 0


def verify_review(staging: Path | None = None):
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
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json')
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS' % rel)
    paths = {RUNNER_REL: ROOT / RUNNER_REL, CONTROLLER_REL: ROOT / CONTROLLER_REL,
             'config.json': staging / 'config.json', 'macros/' + MACRO: staging / 'sources' / MACRO,
             'inputs/external_flow_meshed.sim': staging / 'external_flow_meshed.sim',
             'inputs/case_spec.json': staging / 'case_spec.json',
             'source/external_flow_meshed.sim': SIM, 'source/case_spec.json': SPEC}
    for rel, path in paths.items():
        if sha256(path) != reviewed[rel]:
            raise SystemExit('bytes changed after review: ' + rel)
    return cfg, review_path


def run(args) -> int:
    cfg, review_path = verify_review()
    b = Backend()
    job = cfg['job_dir']
    exists = b.ps('[pscustomobject]@{e=(Test-Path -LiteralPath %s)} | ConvertTo-Json' % ps_quote(job))
    if exists.get('e'):
        raise SystemExit('remote job dir already exists; refusing to reuse: ' + job)
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job))
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job + '\\sources'))
    b.scp(ROOT / RUNNER_REL, job + '\\run_solve_adhoc.ps1')
    for name in ('config.json', 'review.json', 'case_spec.json', 'external_flow_meshed.sim'):
        b.scp(STAGING / name, job + '\\' + name)
    b.scp(STAGING / 'sources' / MACRO, job + '\\sources\\' + MACRO)
    names = ('config.json', 'review.json', 'case_spec.json', 'external_flow_meshed.sim')
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + n), ps_quote(sha256(STAGING / n)), n) for n in names)
        + "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    record = {'schema_version': 1, 'run_id': RUN_ID,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'remote_verify': verify, 'review_sha256': sha256(review_path),
              'config_sha256': sha256(STAGING / 'config.json')}
    try:
        record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_solve_adhoc.ps1'), ps_quote(job + '\\config.json'),
            ps_quote(sha256(STAGING / 'config.json')), ps_quote(sha256(review_path))), timeout=86400)
    except Exception as exception:
        record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        record['downloads'] = {}
        for name in ('run.json', 'external_flow_solve.json', 'external_flow_solve_history.csv',
                     'solve/star.log', 'external_flow_solved.sim'):
            target = EVIDENCE / name.replace('/', '__')
            try:
                b.scp(target, job + '\\' + name.replace('/', '\\'), download=True)
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
