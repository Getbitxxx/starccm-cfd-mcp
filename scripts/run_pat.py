# -*- coding: utf-8 -*-
"""Evaluate the product acceptance test (PAT) against recorded evidence.

This is the executable half of the 2026-10-18 decision:

    E is a quality label; PAT is the release gate.

The PAT does NOT judge whether the physics is accurate. It judges whether the system
behaved as promised for each product case:

* SUCCESS           the run completed, every stage is ok, the required artifacts exist,
                    and no record claims engineering validation.
* HONEST_NEGATIVE   the run completed and every stage is ok, but the declared label record
                    says the run is NOT converged / NOT periodic / NOT engineering-verified.
                    This is a pass for the product: the system ran and told the truth.
* FAIL_CLOSED       the run stopped with a documented failure and did not produce a success
                    certificate.
* NOT_RUN           no evidence path is recorded yet. This is not a pass.

The evaluator never edits evidence and never treats a failed stage as a success. It uses the
same terminal states as scripts/run_record_gate.py and reads case-probe.json / state.json with
utf-8-sig because the Windows runner writes a BOM.

Usage:
    python scripts/run_pat.py --pat test-evidence/product-acceptance/PAT-20261018.json \
        --root . --out test-evidence/product-acceptance/PAT-RESULT.json
    python scripts/run_pat.py ... --json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUCCESS_STATES = frozenset({'AIR3B_PIPELINE_COMPLETE', 'EXECUTION_COMPLETED', 'SUCCEEDED'})
FAILURE_STATES = frozenset({'FAILED', 'GATE_FAILED', 'WAITING_AGENT', 'TIMED_OUT', 'CANCELLED',
                            'RESOURCE_STOP', 'REJECTED_BUSY'})
STAGE_OK = frozenset({'OK', 'ok'})
EXPECTED = frozenset({'SUCCESS', 'HONEST_NEGATIVE', 'FAIL_CLOSED'})
CHANNELS = frozenset({'v1', 'beta'})
VERIFICATION_BASES = frozenset({'historical_evidence_readback', 'deployed_worker_execution'})
DEPLOYED_EVIDENCE = Path('test-evidence/product-acceptance/deployed-20261018')
SCENARIOS = frozenset({'1_static_external_flow', '2_steady_mrf_propeller',
                       '3_rotating_transient'})
REQUIRED_KEYS = ('id', 'scenario', 'title', 'release_channel', 'required_for_release',
                 'expected', 'evidence', 'required_artifacts')


class PatError(ValueError):
    """The PAT definition is malformed; evaluating it would produce a meaningless gate."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    """Return (payload, error). The Windows runner writes UTF-8 with a BOM."""
    try:
        return json.loads(path.read_text(encoding='utf-8-sig')), None
    except Exception as exc:                                            # noqa: BLE001
        return None, '%s: %s' % (type(exc).__name__, exc)


def verification_basis_errors(case: dict, root: Path = ROOT) -> list[str]:
    """Shared deployed-PAT contract; the release gate also checks the definition itself."""
    basis = case.get('verification_basis')
    if not isinstance(basis, str) or basis not in VERIFICATION_BASES:
        return ['missing or invalid verification_basis: %r' % (basis,)]
    if basis == 'historical_evidence_readback':
        if case.get('simulation_rerun') is not False:
            return ['historical_evidence_readback requires simulation_rerun=false']
        return []

    evidence = case.get('evidence')
    # The unexecuted optional beta restart case has no evidence and earns no execution credit.
    if evidence is None and case.get('release_channel') == 'beta' and not case.get('required_for_release'):
        return []
    if not isinstance(evidence, str) or not evidence or Path(evidence).is_absolute():
        return ['deployed_worker_execution requires a relative deployed evidence path']
    base = (root / DEPLOYED_EVIDENCE).resolve()
    path = (root / evidence).resolve()
    if not path.is_relative_to(base) or path == base or path.is_relative_to(base / 'history'):
        return ['deployed_worker_execution evidence must be under %s/ and outside history/'
                % DEPLOYED_EVIDENCE.as_posix()]
    return []


def basis_counts(cases: list[dict]) -> dict[str, dict[str, int]]:
    """Count registered evidence, including beta readback; null beta evidence earns no credit."""
    counts = {}
    for name, basis in [('evidence_identity', 'historical_evidence_readback'),
                        ('deployed_execution', 'deployed_worker_execution')]:
        items = [case for case in cases if case.get('verification_basis') == basis
                 and case.get('evidence') is not None]
        counts[name] = {'total': len(items),
                        'passed': sum(case.get('result') == 'PASS' for case in items)}
    return counts


def validate_pat(pat: dict, root: Path = ROOT) -> None:
    if not isinstance(pat, dict):
        raise PatError('PAT must be a JSON object')
    if pat.get('schema_version') != 1:
        raise PatError('Unsupported PAT schema_version: %r' % (pat.get('schema_version'),))
    cases = pat.get('cases')
    if not isinstance(cases, list) or not cases:
        raise PatError('PAT has no cases')
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PatError('PAT case is not an object: %r' % (case,))
        missing = [key for key in REQUIRED_KEYS if key not in case]
        if missing:
            raise PatError('PAT case %r is missing %s' % (case.get('id'), missing))
        cid = case['id']
        if not isinstance(cid, str) or not cid:
            raise PatError('PAT case id must be a non-empty string')
        if cid in seen:
            raise PatError('Duplicate PAT case id: ' + cid)
        seen.add(cid)
        if case['scenario'] not in SCENARIOS:
            raise PatError('%s: unknown scenario %r' % (cid, case['scenario']))
        if case['release_channel'] not in CHANNELS:
            raise PatError('%s: unknown release_channel %r' % (cid, case['release_channel']))
        if not isinstance(case['required_for_release'], bool):
            raise PatError('%s: required_for_release must be boolean' % cid)
        if case['expected'] not in EXPECTED:
            raise PatError('%s: unknown expected outcome %r' % (cid, case['expected']))
        if case['evidence'] is not None and not isinstance(case['evidence'], str):
            raise PatError('%s: evidence must be a relative path or null' % cid)
        if not isinstance(case['required_artifacts'], list) or not all(
                isinstance(item, str) and item for item in case['required_artifacts']):
            raise PatError('%s: required_artifacts must be a list of file names' % cid)
        if case['expected'] == 'HONEST_NEGATIVE':
            for key in ('label_record', 'label_key', 'label_expected'):
                if key not in case:
                    raise PatError('%s: HONEST_NEGATIVE requires %s' % (cid, key))
            if not isinstance(case['label_expected'], bool):
                raise PatError('%s: label_expected must be boolean' % cid)
        if pat.get('release_context') == 'deployed':
            errors = verification_basis_errors(case, root)
            if errors:
                raise PatError('%s: %s' % (cid, '; '.join(errors)))


def find_artifact(evidence: Path, name: str) -> Path | None:
    direct = evidence / name
    if direct.is_file():
        return direct
    matches = sorted(evidence.rglob(name))
    return matches[0] if matches else None


def load_records(evidence: Path) -> tuple[list[dict], list[str], list[str]]:
    """Top-level JSON records, parse errors, and false-pass claims."""
    records, parse_errors, false_pass = [], [], []
    for path in sorted(evidence.glob('*.json')):
        payload, error = read_json(path)
        if error:
            parse_errors.append('%s: %s' % (path.name, error))
            continue
        if not isinstance(payload, dict):
            continue
        records.append(payload)
        if payload.get('engineering_verified') is True:
            false_pass.append('%s: engineering_verified=true' % path.name)
    return records, parse_errors, false_pass


def stage_statuses(probe: dict, evidence: Path) -> tuple[list[str], list[str]]:
    statuses, sources = [], []
    stages = probe.get('stages')
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict):
                statuses.append(str(stage.get('status', '')))
                sources.append(str(stage.get('name', 'stage')))
        return statuses, sources
    for path in sorted(evidence.glob('stage-*.json')):
        payload, error = read_json(path)
        if error or not isinstance(payload, dict):
            statuses.append('UNPARSEABLE')
            sources.append(path.name)
            continue
        statuses.append(str(payload.get('state', payload.get('status', ''))))
        sources.append(path.name)
    return statuses, sources


def label_check(evidence: Path, case: dict) -> tuple[bool, str]:
    path = evidence / case['label_record']
    if not path.is_file():
        matches = sorted(evidence.rglob(case['label_record']))
        if not matches:
            return False, 'label record missing: ' + case['label_record']
        path = matches[0]
    payload, error = read_json(path)
    if error or not isinstance(payload, dict):
        return False, 'label record unparseable: ' + case['label_record']
    if case['label_key'] not in payload:
        return False, 'label key missing: ' + case['label_key']
    actual = payload[case['label_key']]
    expected = case['label_expected']
    if actual is not expected:
        return False, 'label %s=%r, expected %r' % (case['label_key'], actual, expected)
    return True, 'label %s=%r as declared' % (case['label_key'], actual)


def classify(root: Path, case: dict) -> dict:
    """Classify one PAT case from its evidence directory. Never edits evidence."""
    result = {
        'id': case['id'],
        'title': case['title'],
        'scenario': case['scenario'],
        'release_channel': case['release_channel'],
        'required_for_release': case['required_for_release'],
        'expected': case['expected'],
        'evidence': case['evidence'],
        'result': 'NOT_RUN',
        'false_pass': False,
        'parse_errors': [],
        'missing_artifacts': [],
        'detail': '',
    }
    for key in ('verification_basis', 'test_kind', 'deployment_status', 'simulation_rerun', 'release_id'):
        if key in case:
            result[key] = case[key]
    if case['evidence'] is None:
        result['detail'] = 'no evidence path recorded yet'
        return result
    evidence = (root / case['evidence']).resolve()
    if not evidence.is_dir():
        result['detail'] = 'evidence directory missing: ' + case['evidence']
        return result

    probe_path = evidence / 'case-probe.json'
    if not probe_path.is_file():
        probe_path = evidence / 'state.json'
    probe, probe_error = read_json(probe_path) if probe_path.is_file() else (None, 'no case-probe.json/state.json')
    if probe_error or not isinstance(probe, dict):
        result['result'] = 'UNPARSEABLE'
        result['detail'] = 'run state: ' + str(probe_error)
        return result

    state = str(probe.get('state', probe.get('final_state', probe.get('status', ''))))
    statuses, sources = stage_statuses(probe, evidence)
    records, parse_errors, false_pass = load_records(evidence)
    result['parse_errors'] = parse_errors
    result['false_pass_claims'] = false_pass
    result['false_pass'] = bool(false_pass)
    if false_pass:
        result['detail'] = 'record claims engineering_verified=true'
        result['result'] = 'FALSE_PASS'
        return result

    missing = []
    for name in case['required_artifacts']:
        artifact = find_artifact(evidence, name)
        if artifact is None:
            missing.append(name)
    result['missing_artifacts'] = missing
    all_stages_ok = bool(statuses) and all(status in STAGE_OK for status in statuses)
    run_ok = state in SUCCESS_STATES
    run_failed = state in FAILURE_STATES

    if case['expected'] == 'FAIL_CLOSED':
        if run_ok or all_stages_ok and not run_failed:
            result['result'] = 'WRONG_OUTCOME'
            result['detail'] = 'expected fail-closed, but the run completed (state=%r)' % state
            return result
        failed_stage = any(status not in STAGE_OK for status in statuses)
        has_error = bool(probe.get('error')) or failed_stage
        if (run_failed or failed_stage) and has_error:
            result['result'] = 'PASS'
            result['detail'] = 'stopped as declared: state=%r, error=%r' % (
                state, probe.get('error'))
        else:
            result['result'] = 'WRONG_OUTCOME'
            result['detail'] = 'failure state without a documented error or failed stage'
        return result

    if not run_ok or not all_stages_ok:
        result['result'] = 'WRONG_OUTCOME'
        result['detail'] = 'expected a completed run; state=%r stages=%r' % (
            state, list(zip(sources, statuses)))
        return result
    if missing:
        result['result'] = 'MISSING_ARTIFACTS'
        result['detail'] = 'required artifacts missing: ' + ', '.join(missing)
        return result
    if case['expected'] == 'HONEST_NEGATIVE':
        ok, detail = label_check(evidence, case)
        result['detail'] = detail
        result['result'] = 'PASS' if ok else 'LABEL_MISMATCH'
        return result
    result['result'] = 'PASS'
    result['detail'] = 'completed, all stages ok, artifacts present, no engineering claim'
    return result


def evaluate(pat_path: Path, root: Path) -> dict:
    pat = json.loads(pat_path.read_text(encoding='utf-8'))
    validate_pat(pat, root)
    results = [classify(root, case) for case in pat['cases']]
    v1 = [item for item in results if item['release_channel'] == 'v1' and item['required_for_release']]
    beta = [item for item in results if item['release_channel'] == 'beta']
    v1_passed = sum(1 for item in v1 if item['result'] == 'PASS')
    beta_passed = sum(1 for item in beta if item['result'] == 'PASS')
    false_passes = [item['id'] for item in results if item['false_pass']]
    overall = 'PASS' if v1 and v1_passed == len(v1) and not false_passes else 'FAIL'
    return {
        'schema_version': 1,
        'pat': str(pat_path),
        'pat_sha256': sha256(pat_path),
        'release': pat.get('release'),
        'release_context': pat.get('release_context'),
        'release_id': pat.get('release_id'),
        'evaluated_at_utc': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        'root': str(root),
        'overall': overall,
        'v1_required_total': len(v1),
        'v1_required_passed': v1_passed,
        'beta_total': len(beta),
        'beta_passed': beta_passed,
        'false_passes': false_passes,
        **basis_counts(results),
        'cases': results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Evaluate the product acceptance test.')
    parser.add_argument('--pat', type=Path,
                        default=ROOT / 'test-evidence' / 'product-acceptance' / 'PAT-20261018.json')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=None)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--strict', action='store_true',
                        help='exit 2 when the v1 gate is not PASS')
    args = parser.parse_args(argv)

    pat_path = args.pat if args.pat.is_absolute() else (ROOT / args.pat)
    root = args.root if args.root.is_absolute() else (ROOT / args.root)
    try:
        result = evaluate(pat_path, root)
    except PatError as exc:
        print('PAT ERROR: %s' % exc, file=sys.stderr)
        return 1
    out = args.out or (pat_path.parent / 'PAT-RESULT.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('PAT %s: %s' % (result['release'], result['overall']))
        print('  v1 required: %d/%d' % (result['v1_required_passed'], result['v1_required_total']))
        print('  beta:        %d/%d' % (result['beta_passed'], result['beta_total']))
        for key, label in [('evidence_identity', '证据身份'), ('deployed_execution', '部署路径')]:
            print('  %s: %d/%d' % (label, result[key]['passed'], result[key]['total']))
        print('  FALSE_PASS=%d' % len(result['false_passes']))
        if result['false_passes']:
            print('  FALSE PASS:  ' + ', '.join(result['false_passes']))
        for item in result['cases']:
            mark = 'PASS' if item['result'] == 'PASS' else item['result']
            if item['result'] != 'PASS' or item.get('test_kind'):
                kind = ' [test_kind=%s]' % item['test_kind'] if item.get('test_kind') else ''
                print('  %-32s %-16s %s%s' % (item['id'], mark, item['detail'], kind))
        print('  wrote', out)
    if args.strict and result['overall'] != 'PASS':
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
