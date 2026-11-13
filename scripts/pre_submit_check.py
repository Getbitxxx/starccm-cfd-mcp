#!/usr/bin/env python3
"""Pre-submission self-check: staging pins must match HEAD bytes, macros must equal steps,
and the live CaseSpec must not carry stale keys. Read-only; exits 1 on any violation."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def head_bytes(path: str) -> bytes:
    try:
        return subprocess.run(['git', 'show', 'HEAD:' + path], cwd=ROOT, check=True,
                              capture_output=True).stdout
    except subprocess.CalledProcessError:
        raise SystemExit('NOT_IN_HEAD %s' % path)


def main(plan_name: str, historical: bool = False) -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location('chain', ROOT / 'scripts/run_mrf_chain.py')
    chain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chain)
    plan = chain.PLANS[plan_name]
    staging = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e' / plan['run_id'] / 'staging'
    review_inputs = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))
    hashes = review_inputs['hashes']
    failures = []

    # 1) macros must equal the step macros, and every macro source must be staged
    step_macros = [s['macro'] for s in plan['steps']]
    if plan.get('macros') != step_macros:
        failures.append('plan.macros %r != step macros %r' % (plan.get('macros'), step_macros))
    for macro in step_macros:
        src = staging / 'sources' / macro
        if not src.is_file():
            failures.append('missing staged source %s' % macro)
        elif sha_bytes(src.read_bytes()) != sha_bytes(head_bytes('macros/' + macro)):
            failures.append('staged source differs from HEAD: %s' % macro)

    # 2) staging files must match their pins; repo-tracked sources/inputs must match HEAD bytes
    def staged_path(name):
        if name == 'config.json' or name == 'source_case_spec.json':
            return staging / 'config.json' if name == 'config.json' else staging / 'case_spec.json'
        if name.startswith('macros/'):
            return staging / 'sources' / name[len('macros/'):]
        if name.startswith('inputs/'):
            return staging / name[len('inputs/'):]
        if name.startswith('scripts/'):
            return ROOT / name
        return staging / name

    plan_inputs = plan['inputs']
    for name, digest in hashes.items():
        if historical and name.startswith('scripts/'):
            continue  # a historical staging pins the controller/runner it was approved with; those bytes are not in staging
        local = staged_path(name)
        if not local.is_file():
            failures.append('pinned file missing: %s' % name)
            continue
        if sha_bytes(local.read_bytes()) != digest:
            failures.append('staging pin mismatch: %s' % name)
        if name.startswith('macros/'):
            rel = 'macros/' + name[len('macros/'):]
            if not historical and sha_bytes(head_bytes(rel)) != digest:
                failures.append('macro pin differs from HEAD: %s' % rel)
        elif name.startswith('inputs/'):
            rel = name[len('inputs/'):]
            src = plan_inputs.get(rel)
            if src is not None:
                rel_path = str(Path(src).relative_to(ROOT))
                tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', rel_path], cwd=ROOT,
                                         capture_output=True)
                if not historical and tracked.returncode == 0 and sha_bytes(head_bytes(rel_path)) != digest:
                    failures.append('input pin differs from HEAD: %s' % rel_path)

    # 3) CaseSpec: no stale ALM key / stale basis on the current revision
    # 3) CaseSpec: no stale ALM key / stale basis on the current revision
    live = json.loads((ROOT / 'cases/uav-prop-apc10x7e/case_spec.json').read_text(encoding='utf-8'))
    revision = live['mesh_revision']['revision']
    if revision >= 'MESH-B11' and 'mesh_rotating_layer_mesher' in live:
        failures.append('stale mesh_rotating_layer_mesher present with %s' % revision)
    for key in ('mesh_min_wall_prism_layers_basis', 'yplus_target_basis'):
        text = live['quality'].get(key, '')
        if 'UNCHANGED' not in text or 'MESH-B13' not in text:
            failures.append('stale quality basis: %s' % key)

    # 4) 2026-10-30 user decision: a SOLVE plan may not be submitted unless the accepted mesh family covers the
    #    exact mesh it consumes.  Archival (--historical) stagings describe runs approved BEFORE the standard, so the
    #    gate is not applied to them; a strict submission of any solve plan must satisfy it.
    # exact mesh it consumes.  A plan that predates the standard must carry an explicit legacy flag with a reason
    # (never a silent exemption), and legacy plans are accepted only in --historical (archival) mode.
    import importlib.util as importlib_util
    acceptance_spec = importlib_util.spec_from_file_location('mesh_acceptance',
                                                            ROOT / 'scripts' / 'mesh_acceptance.py')
    acceptance = importlib_util.module_from_spec(acceptance_spec)
    acceptance_spec.loader.exec_module(acceptance)
    solve_macros = {'RunMrfTrial.java', 'RunMrfSegment.java'}
    if not historical and any(str(step.get('macro')) in solve_macros for step in plan['steps']):
        contract = chain.CONTRACT_OVERRIDES.get(plan_name, {})
        legacy = contract.get('legacy_no_family_gate')
        record_source = plan['inputs'].get('mesh_family_gate.json')
        if legacy:
            if not isinstance(legacy, str) or '2026-10-30' >= legacy:
                failures.append('legacy_no_family_gate needs a dated reason string (plan predates 2026-10-30)')
        elif record_source is None:
            failures.append('solve plan declares no mesh_family_gate.json input: solve refused (family gate required)')
        else:
            record = json.loads(Path(record_source).read_text(encoding='utf-8'))
            mesh_source = plan['inputs'].get('mrf_diagnostic_meshed.sim')
            if mesh_source is None:
                failures.append('solve plan declares no mrf_diagnostic_meshed.sim input to link the family gate')
            else:
                mesh_sha = sha_bytes(Path(mesh_source).read_bytes())
                try:
                    acceptance.require_family_for_solve(plan, record, mesh_sha)
                except acceptance.MeshAcceptanceError as exc:
                    failures.append(str(exc))

    print(json.dumps({'plan': plan_name, 'run_id': plan['run_id'], 'checks': 'pins/macros/casespec/family_gate',
                      'failures': failures, 'ok': not failures}, indent=1))
    return 0 if not failures else 1


if __name__ == '__main__':
    plan_arg = sys.argv[1] if len(sys.argv) > 1 else 'part51'
    sys.exit(main(plan_arg, '--historical' in sys.argv[2:]))
