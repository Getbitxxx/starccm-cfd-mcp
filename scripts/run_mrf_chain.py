"""Review-gated ad-hoc MRF chain controller (P2.3 G5 / phase 3 P30): plans are DATA, not engine bytes.

The 87 plans (geometry audit -> volumes -> regions/interfaces -> mesh -> physics -> trial/segment/audit,
each step one STAR process with one macro) live under campaigns/uav-2026/plans/<campaign>/ as one JSON
file per plan.  This module is the reviewed, pinned execution/hash/gating machinery only:

* PLANS is loaded by the pure loader scripts/plan_registry.py (``load_all('uav-prop-apc10x7e')``); a
  new plan is a new JSON file, so it cannot change this controller's sha256 and cannot invalidate a
  reviewed staging pin ("bytes changed after review");
* the per-plan contract (what the run is allowed to do) is materialised in the plan data and written
  into REVIEW-INPUTS.json by prepare();
* NEW plans must pass the registry's spike-evidence gate (F2) and derive signed CT/CP tolerance bands
  from the staged CaseSpec (F1); both are refused in prepare() before any staging write.

Modes (per plan): prepare builds staging and prints the hashes to pin; run requires an independent
review of those exact bytes, uploads, runs the STAR processes, field-gates the records and downloads
the results (required artefacts fatal when missing, downloads recorded with bytes/sha256).
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import Backend

import plan_mesh_gate
import plan_registry

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'cases/uav-prop-apc10x7e'
SPEC = CASE / 'case_spec.json'
AUD_PASS = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/geometry-audit-20261028a/AUDITED-RUN-2-PASS'
PART1_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain-20261028a'
PART2_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain2-20261028a'
PART3_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain3-20261028a'
PART4_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain4-20261028a'
PART5_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain5-20261028a'
PART6_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain6-20261028a'
PART10_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain10-20261028a'
PART11_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain11-20261028a'
PART13_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-chain13-20261028a'
PART17_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-meshb2-20261028a'
PART18_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-physics2-20261028a'
PART19_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-trial2-20261028a'
PART20_EVID = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/mrf-segment3-20261028a'
RUNNER_REL = 'scripts/security/run_mrf_chain_adhoc.ps1'
CONTROLLER_REL = 'scripts/run_mrf_chain.py'
CAMPAIGN = 'uav-prop-apc10x7e'
EVIDENCE_REL = 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
GCI_EVIDENCE = ROOT / EVIDENCE_REL

# F1 (phase 3 P30): the 87 plans are DATA, not engine bytes.  One JSON file per plan under
# campaigns/uav-2026/plans/<campaign>/; adding a plan does not change this controller's sha256 and
# therefore cannot invalidate a reviewed staging pin.  plan_registry is a pure loader (it writes
# nothing) and carries no plan ids: the campaign manifest lists the legacy plan ids, so "new plan"
# policy (spike evidence, derived tolerance gates) is decided from data.
PLANS = plan_registry.load_all(CAMPAIGN)
# The reviewed contract overrides stay a module-level API (tests and pre-submit checks pin them); the
# final per-plan contract is materialised in each plan JSON and read by prepare().
CONTRACT_OVERRIDES = plan_registry.contract_overrides(CAMPAIGN)

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paths(plan):
    spec = PLANS[plan]
    evidence = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e' / spec['run_id']
    return spec, evidence, evidence / 'staging'




def config_from(spec, plan=None, job_dir=None):
    """PURE plan-config builder for an explicit plan spec (the registry's dict); writes NOTHING.

    Tests and callers use this (via config(plan) or on a throwaway registry); only build_staging()
    writes into an evidence staging directory.
    (Measured defect 2026-10-28: the old config() copied the CURRENT CaseSpec into the plan's staging
    as a side effect; tests calling config() for several plans therefore overwrote ACCEPTED
    evidence staging bytes and broke their SHA256SUMS/REVIEW pins. review-3p blocking finding.)
    """
    plan = plan if plan is not None else spec['plan']
    declared_spec = spec['inputs'].get('case_spec.json')
    case_spec_source = Path(declared_spec) if declared_spec is not None else SPEC
    inputs = {'case_spec.json': sha256(case_spec_source)}
    for name, source in spec['inputs'].items():
        if name == 'case_spec.json':
            continue
        if source is None or not Path(source).is_file():
            raise SystemExit('missing declared input source for ' + name)
        inputs[name] = sha256(source)
    macros = {m: sha256(ROOT / 'macros' / m) for m in spec['macros']}
    target = (job_dir or spec['job_dir']).rstrip('\\')
    return {
        'schema_version': 1,
        'scope': 'ad_hoc_mrf_geometry_chain_only_not_formal_pipeline_or_PAT',
        'plan': plan,
        'run_id': spec['run_id'],
        'job_dir': target,
        'star_launcher': 'C:\\Program Files\\Siemens\\20.02.008-R8\\STAR-CCM+20.02.008-R8\\star\\bin\\starccm+.bat',
        'star_version': '2502.0001',
        'logical_cores': 64,
        'memory_budget_gb': 239,
        'memory_reserve_gb': 16,
        'wall_clock_stop_enabled': False,
        'runner_sha256': sha256(ROOT / RUNNER_REL),
        'inputs': inputs,
        'macros': macros,
        'steps': spec['steps'],
        'record_gates': spec['record_gates'],
    }


def config(plan, job_dir=None):
    """PURE plan-config builder: reads declared sources, writes NOTHING (registry-backed)."""
    spec, _evidence, _staging = paths(plan)
    return config_from(spec, plan, job_dir)


def review_candidates(plan, staging):
    """Read-only map of reviewed relative names -> on-disk paths (used by run() and by the B5 regression test)."""
    spec, _evidence, _staging = paths(plan)
    expected = json.loads((Path(staging) / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    cand = {rel: Path(staging) / rel.split('/', 1)[1] for rel in expected if rel.startswith('inputs/')}
    cand.update({rel: Path(staging) / 'sources' / rel.split('/', 1)[1] for rel in expected if rel.startswith('macros/')})
    cand['config.json'] = Path(staging) / 'config.json'
    declared_spec = spec['inputs'].get('case_spec.json')
    cand['source_case_spec.json'] = Path(declared_spec) if declared_spec is not None else SPEC
    cand['scripts/security/run_mrf_chain_adhoc.ps1'] = ROOT / RUNNER_REL
    cand['scripts/run_mrf_chain.py'] = ROOT / CONTROLLER_REL
    return cand


def build_staging(plan, job_dir=None):
    """Materialises this plan's staging directory (the ONLY writer of evidence staging bytes)."""
    spec, evidence, staging = paths(plan)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / 'sources').mkdir(exist_ok=True)
    declared_spec = spec['inputs'].get('case_spec.json')
    shutil.copyfile(Path(declared_spec) if declared_spec is not None else SPEC,
                    staging / 'case_spec.json')
    for name, source in spec['inputs'].items():
        if name == 'case_spec.json':
            continue
        if source is None or not Path(source).is_file():
            raise SystemExit('missing declared input source for ' + name)
        shutil.copyfile(source, staging / name)
    for macro in spec['macros']:
        shutil.copyfile(ROOT / 'macros' / macro, staging / 'sources' / macro)
    return config(plan, job_dir)


def prepare(args):
    plan = args.plan
    spec, evidence, staging = paths(plan)
    # F2/F1 fail-closed gate: a NEW plan without a PASS spike record (or with a hand-edited, non-derived
    # tolerance band) is refused here, BEFORE evidence.mkdir/staging writes.  Legacy plans pass with their
    # grandfathered marker.
    try:
        plan_registry.assert_preparable(plan, spec, ROOT)
    except plan_registry.PlanRegistryError as exc:
        raise SystemExit(str(exc))
    # F3 (phase 3 P30): a NEW case's solve plan must carry an ACCEPTED mesh-family gate that covers the
    # exact mesh the plan consumes; grandfathered legacy plans without the declared field stay
    # admissible.  Fail-closed BEFORE any evidence.mkdir/staging write.
    try:
        plan_mesh_gate.assert_plan_admissible(spec, ROOT, SPEC)
    except plan_mesh_gate.MeshAcceptanceError as exc:
        raise SystemExit(str(exc))
    evidence.mkdir(parents=True, exist_ok=True)
    cfg = build_staging(plan, args.job_dir)
    config_path = staging / 'config.json'
    config_path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    hashes = {
        'scripts/security/run_mrf_chain_adhoc.ps1': sha256(ROOT / RUNNER_REL),
        'scripts/run_mrf_chain.py': sha256(ROOT / CONTROLLER_REL),
        'config.json': sha256(config_path),
        'source_case_spec.json': sha256(Path(spec['inputs']['case_spec.json']) if spec['inputs'].get('case_spec.json') is not None else SPEC),
    }
    for name, digest in cfg['inputs'].items():
        hashes['inputs/' + name] = digest
    for name, digest in cfg['macros'].items():
        hashes['macros/' + name] = digest
    (staging / 'REVIEW-INPUTS.json').write_text(json.dumps({
        'run_id': spec['run_id'], 'plan': plan, 'staging_dir': str(staging.relative_to(ROOT)),
        'job_dir': cfg['job_dir'], 'review_json_path': str((staging / 'review.json').relative_to(ROOT)),
        'hashes': hashes,
        # F1: the contract is materialised in the plan data (legacy frozen dict = the historical
        # CONTRACT_OVERRIDES entry or the old ternary-chain value).  prepare() no longer has a per-plan
        # ternary chain, so adding a plan cannot change these bytes.
        'contract': dict(spec['contract']),
    }, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    if plan in CONTRACT_OVERRIDES:
        # historical behaviour: an overridden contract staging also pins REVIEW-INPUTS.json itself
        hashes['REVIEW-INPUTS.json'] = sha256(staging / 'REVIEW-INPUTS.json')
    print(json.dumps({'staging': str(staging), 'hashes': hashes}, indent=1))


def run(args):
    plan = args.plan
    spec, evidence, staging = paths(plan)
    cfg = json.loads((staging / 'config.json').read_text(encoding='utf-8'))
    review_path = staging / 'review.json'
    if not review_path.is_file():
        raise SystemExit('missing independent review.json in staging')
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('approved') is not True or review.get('independent') is not True:
        raise SystemExit('independent approval required')
    expected = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))['hashes']
    reviewed = review.get('hashes') or {}
    if set(reviewed) != set(expected):
        raise SystemExit('review.hashes keys differ from REVIEW-INPUTS.json')
    for rel, want in expected.items():
        if reviewed[rel] != want:
            raise SystemExit('review.hashes[%s] differs from REVIEW-INPUTS' % rel)
    candidates = review_candidates(plan, staging)
    for rel, path in candidates.items():
        if sha256(path) != reviewed[rel]:
            raise SystemExit('bytes changed after review: ' + rel)
    b = Backend()
    job = cfg['job_dir']
    existing = b.ps('[pscustomobject]@{exists=(Test-Path -LiteralPath %s)} | ConvertTo-Json' % ps_quote(job))
    if existing.get('exists'):
        raise SystemExit('remote job dir already exists; refusing to reuse: ' + job)
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job))
    b.ps('New-Item -ItemType Directory -Path %s -Force | Out-Null' % ps_quote(job + '\\sources'))
    b.scp(ROOT / RUNNER_REL, job + '\\run_mrf_chain_adhoc.ps1')
    for name in ('config.json', 'review.json'):
        b.scp(staging / name, job + '\\' + name)
    for name in cfg['inputs']:
        b.scp(staging / name, job + '\\' + name)
    for macro in spec['macros']:
        b.scp(staging / 'sources' / macro, job + '\\sources\\' + macro)
    verify = b.ps('\n'.join(
        "if((Get-FileHash -LiteralPath %s -Algorithm SHA256).Hash.ToLowerInvariant() -ne %s){throw 'remote %s differs'}"
        % (ps_quote(job + '\\' + name), ps_quote(cfg['inputs'][name]), name) for name in cfg['inputs']) +
        "\n[pscustomobject]@{status='remote_hashes_ok'} | ConvertTo-Json")
    record = {'schema_version': 1, 'run_id': spec['run_id'], 'plan': plan,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'remote_verify': verify,
              'review_sha256': sha256(review_path), 'config_sha256': sha256(staging / 'config.json')}
    try:
        record['runner'] = b.ps('& %s -ConfigPath %s -ConfigSha256 %s -ReviewSha256 %s' % (
            ps_quote(job + '\\run_mrf_chain_adhoc.ps1'), ps_quote(job + '\\config.json'),
            ps_quote(sha256(staging / 'config.json')), ps_quote(sha256(review_path))), timeout=86400)
    except Exception as exception:
        record['runner_error'] = str(exception)[-2000:]
        raise
    finally:
        evidence.mkdir(parents=True, exist_ok=True)
        record['downloads'] = {}
        for name in spec['required_downloads'] + spec['optional_downloads']:
            try:
                b.scp(evidence / name, job + '\\' + name, download=True)
                record['downloads'][name] = {'bytes': (evidence / name).stat().st_size,
                                             'sha256': sha256(evidence / name)}
            except Exception as exception:
                record['downloads'][name] = {'error': str(exception)[-300:]}
        for step in spec['steps']:
            log = evidence / (step['name'] + '-star.log')
            try:
                b.scp(log, job + '\\' + step['name'] + '\\star.log', download=True)
                record['downloads'][step['name'] + '-star.log'] = {'bytes': log.stat().st_size,
                                                                   'sha256': sha256(log)}
            except Exception as exception:
                record['downloads'][step['name'] + '-star.log'] = {'error': str(exception)[-300:]}
        missing = [name for name in spec['required_downloads']
                   if 'error' in record['downloads'].get(name, {'error': 'missing'})]
        if missing:
            record['download_errors'] = missing
        (evidence / 'CONTROLLER-RUN.json').write_text(json.dumps(record, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        if missing and sys.exc_info()[0] is None:
            raise SystemExit('required chain artefacts missing from the job root: %r' % missing)
    print(json.dumps({'status': record.get('runner', {}).get('status'), 'evidence': str(evidence),
                      'downloads': record['downloads']}, indent=1))
    return 0


def ps_quote(value):
    return "'" + value.replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--plan', choices=sorted(PLANS), required=True)
    p.add_argument('--job-dir', default=None)
    r = sub.add_parser('run')
    r.add_argument('--plan', choices=sorted(PLANS), required=True)
    args = parser.parse_args()
    return prepare(args) if args.mode == 'prepare' else run(args)


if __name__ == '__main__':
    sys.exit(main())
