# -*- coding: utf-8 -*-
"""The release gate: a package may ship only when the PAT says so.

Decision of 2026-10-18: E is a quality label, PAT is the release gate. This script is the
executable consequence. It does not look at residuals, Cd or convergence; those are labels in
the report. It looks at whether every v1 product case behaved as promised.

Rules:
  G1  The PAT result must have been produced from the current PAT definition (sha256 match).
  G2  Every v1 case with required_for_release=true must be PASS.
  G3  No case may be a FALSE_PASS (a record claiming engineering_verified=true).
  G4  Beta cases must be present in the result. They may be NOT_RUN by default; a beta false
      pass still fails the gate. --require-beta turns beta PASS into a requirement.
  G5  A NOT_RUN v1 case is a failure, not a warning.
  G6  A deployed release needs four passing required v1 deployed executions, including a
      SUCCESS, a FAIL_CLOSED and PAT-16 checkpoint delivery. Historical readback earns no
      deployed execution credit.
  G7  At least one required v1 deployed execution must PASS with test_kind=full_chain.

Exit 0 only when the release gate passes. Exit 1 otherwise, with the exact blocking cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from run_pat import PatError, basis_counts, validate_pat, verification_basis_errors

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_chain_counts(cases: list[dict]) -> dict[str, int]:
    candidates = [case for case in cases if case.get('release_channel') == 'v1'
                  and case.get('required_for_release') and case.get('test_kind') == 'full_chain']
    return {'total': len(candidates),
            'passed': sum(case.get('result') == 'PASS' and not case.get('false_pass')
                          and case.get('verification_basis') == 'deployed_worker_execution'
                          for case in candidates)}


def resolve_result_path(pat_path: Path, pat: dict, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit if explicit.is_absolute() else ROOT / explicit
    configured = pat.get('result_file')
    # A relative result_file is relative to its PAT definition, making the pair portable.
    preferred = Path(configured) if configured else pat_path.with_name(pat_path.stem + '-RESULT.json')
    if configured and not preferred.is_absolute():
        preferred = pat_path.parent / preferred
    legacy = pat_path.parent / 'PAT-RESULT.json'
    if not preferred.is_file() and legacy.is_file() and preferred != legacy:
        print('WARN: preferred PAT result %s is missing; falling back to legacy %s; '
              'stale-PAT checks still apply.' % (preferred, legacy), file=sys.stderr)
        return legacy
    return preferred


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='PAT release gate.')
    parser.add_argument('--pat', type=Path,
                        default=ROOT / 'test-evidence' / 'product-acceptance' / 'PAT-20261018.json')
    parser.add_argument('--result', type=Path, default=None)
    parser.add_argument('--require-beta', action='store_true',
                        help='beta cases must PASS, not merely be present')
    args = parser.parse_args(argv)

    pat_path = args.pat if args.pat.is_absolute() else (ROOT / args.pat)
    if not pat_path.is_file():
        print('RELEASE BLOCKED: PAT definition missing: %s' % pat_path, file=sys.stderr)
        return 1
    pat = json.loads(pat_path.read_text(encoding='utf-8'))
    result_path = resolve_result_path(pat_path, pat, args.result)
    if not result_path.is_file():
        print('RELEASE BLOCKED: PAT result missing: %s' % result_path, file=sys.stderr)
        print('Run scripts/run_pat.py first.', file=sys.stderr)
        return 1

    result = json.loads(result_path.read_text(encoding='utf-8'))
    blocking, warnings = [], []
    deployed = pat.get('release_context') == 'deployed'
    if deployed:
        try:
            validate_pat(pat)
        except PatError as exc:
            blocking.append('deployed PAT definition: ' + str(exc))

    if result.get('pat_sha256') != sha256(pat_path):
        blocking.append('PAT-RESULT is stale: pat_sha256 does not match the current PAT')
    if result.get('release') != pat.get('release'):
        blocking.append('PAT-RESULT release %r does not match PAT %r'
                        % (result.get('release'), pat.get('release')))

    by_id = {item.get('id'): item for item in result.get('cases') or [] if isinstance(item, dict)}
    classified, fresh_v1 = [], []
    for case in pat.get('cases') or []:
        if deployed:
            for error in verification_basis_errors(case):
                blocking.append('%s: %s' % (case['id'], error))
        item = by_id.get(case['id'])
        if item is None:
            blocking.append('%s: missing from PAT-RESULT' % case['id'])
            continue
        if deployed:
            for error in verification_basis_errors(item):
                blocking.append('%s: PAT-RESULT %s' % (case['id'], error))
        # The definition is authoritative: a result cannot relabel historical evidence.
        if deployed and item.get('verification_basis') != case.get('verification_basis'):
            blocking.append('%s: PAT-RESULT verification_basis does not match PAT' % case['id'])
        if deployed and item.get('evidence') != case.get('evidence'):
            blocking.append('%s: PAT-RESULT evidence does not match PAT' % case['id'])
        if deployed and item.get('test_kind') != case.get('test_kind'):
            blocking.append('%s: PAT-RESULT test_kind does not match PAT' % case['id'])
        classified.append({**case, 'result': item.get('result'),
                           'false_pass': bool(item.get('false_pass'))})
        if item.get('false_pass') or item.get('result') == 'FALSE_PASS':
            blocking.append('%s: FALSE_PASS (%s)' % (case['id'], item.get('detail', '')))
            continue
        if case['release_channel'] == 'v1' and case['required_for_release']:
            if item.get('result') != 'PASS':
                blocking.append('%s: v1 required, result=%s (%s)'
                                % (case['id'], item.get('result'), item.get('detail', '')))
            elif case.get('verification_basis') == 'deployed_worker_execution':
                fresh_v1.append(case)
        elif case['release_channel'] == 'beta':
            if args.require_beta and item.get('result') != 'PASS':
                blocking.append('%s: beta required by --require-beta, result=%s'
                                % (case['id'], item.get('result')))
            elif item.get('result') == 'NOT_RUN':
                warnings.append('%s: beta not run yet' % case['id'])
            elif item.get('result') not in ('PASS', 'NOT_RUN'):
                warnings.append('%s: beta result=%s (%s)'
                                % (case['id'], item.get('result'), item.get('detail', '')))

    if result.get('false_passes'):
        blocking.append('false passes: ' + ', '.join(result['false_passes']))

    if deployed:
        if len(fresh_v1) < 4:
            blocking.append('deployed execution: need at least 4 passing required v1 cases; got %d'
                            % len(fresh_v1))
        for expected, label in [('SUCCESS', 'fresh success'), ('FAIL_CLOSED', 'fresh fail-closed')]:
            if not any(case['expected'] == expected for case in fresh_v1):
                blocking.append('deployed execution: missing ' + label)
        if not any(case['id'] == 'PAT-16-checkpoint-delivery' for case in fresh_v1):
            blocking.append('deployed execution: missing PAT-16-checkpoint-delivery')
        if full_chain_counts(classified)['passed'] < 1:
            blocking.append('deployed execution: missing passing required v1 full_chain '
                            '(verification_basis=deployed_worker_execution)')

    print('Release: %s' % pat.get('release'))
    print('  v1 required: %s/%s passed'
          % (result.get('v1_required_passed'), result.get('v1_required_total')))
    print('  beta:        %s/%s passed' % (result.get('beta_passed'), result.get('beta_total')))
    counts = basis_counts(classified)
    for key, label in [('evidence_identity', '证据身份'), ('deployed_execution', '部署路径')]:
        count = counts[key]
        print('  %s: %d/%d' % (label, count['passed'], count['total']))
    if deployed:
        count = full_chain_counts(classified)
        print('  full_chain: %d/%d' % (count['passed'], count['total']))
    print('  FALSE_PASS=%d' % sum(bool(item.get('false_pass')) or item.get('result') == 'FALSE_PASS'
                                for item in by_id.values()))
    for case in pat.get('cases') or []:
        if case.get('test_kind'):
            print('  %s: test_kind=%s, result=%s' %
                  (case['id'], case['test_kind'], by_id.get(case['id'], {}).get('result', 'MISSING')))
    for warning in warnings:
        print('  WARN  ' + warning)
    if blocking:
        print('  RELEASE BLOCKED:')
        for item in blocking:
            print('    - ' + item)
        return 1
    print('  RELEASE GATE PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
