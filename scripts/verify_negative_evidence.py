"""Turn the negative-control claim into an executable check.

The deliberate-failure trial has one job: prove that a failure leaves its diagnosis on disk instead
of leaving nothing behind. Neither the trial runner nor the numeric gate can check that, because
both are success-path instruments - the runner only inspects required artifacts after the completion
marker, which a failed stage never prints. So the claim is checked here, against the artifacts that
were downloaded from the failed job, and a missing or unusable diagnosis is a hard failure of this
script rather than a sentence in a report.

Usage:
    python scripts/verify_negative_evidence.py --evidence test-evidence/static-external-flow/build-body-negative \
        --trial-id s1-static-sphere-body-neg-20261003a
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict:
    """PowerShell 5.1 writes a UTF-8 BOM with Set-Content -Encoding UTF8."""
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--trial-id', required=True)
    ap.add_argument('--record', default='static_body_geometry.json',
                    help='name of the failure record inside the trial directory')
    a = ap.parse_args()
    trial = a.evidence / a.trial_id
    problems: list[str] = []

    result_path = a.evidence / (a.trial_id + '-result.json')
    probe_path = trial / 'case-probe.json'
    evidence_path = trial / a.record

    if not result_path.is_file():
        problems.append('missing controller result record: ' + str(result_path))
        result = {}
    else:
        result = load(result_path)
        # A negative whose stage declares require_numeric is persisted as GATE_FAILED (the runner's own
        # state is kept in runner_state). Accepting only FAILED made this verifier reject the very
        # negative it was extended for, while every content check passed.
        state = result.get('state')
        if state == 'GATE_FAILED':
            if result.get('runner_state') != 'FAILED':
                problems.append('GATE_FAILED without runner_state FAILED: %r'
                                % result.get('runner_state'))
        elif state != 'FAILED':
            problems.append('the negative control was expected to end FAILED or GATE_FAILED, got %r'
                            % state)

    if not probe_path.is_file():
        problems.append('missing runner record: ' + str(probe_path))
    else:
        probe = load(probe_path)
        if probe.get('state') != 'FAILED':
            problems.append('runner record does not say FAILED: %r' % probe.get('state'))
        if not isinstance(probe.get('error'), str) or not probe['error'].strip():
            problems.append('runner record carries no error text')

    if not evidence_path.is_file():
        # This is the whole point of the control: a failed stage that leaves no diagnosis on disk
        # must be reported as a failure of the control, not silently accepted as "it failed anyway".
        problems.append('THE FAILURE LEFT NO DIAGNOSIS ON DISK: ' + str(evidence_path))
        record = {}
    else:
        record = load(evidence_path)
        checks = [
            ('status', record.get('status') == 'error',
             'status must be "error", got %r' % record.get('status')),
            ('usable', record.get('usable') is False,
             'usable must be false, got %r' % record.get('usable')),
            ('error', isinstance(record.get('error'), str) and bool(record['error'].strip()),
             'error must be a non-empty string, got %r' % record.get('error')),
            ('error_class', isinstance(record.get('error_class'), str)
             and bool(record['error_class'].strip()),
             'error_class must be a non-empty string, got %r' % record.get('error_class')),
            ('complete_marker', record.get('complete_marker') is None,
             'a failure must not carry a completion marker, got %r' % record.get('complete_marker')),
            # Optional: a stage with no .sim output has nothing to declare, and a stage that failed
            # after saving does have one. Checked when present, not demanded.
            ('output_sim_exists', record.get('output_sim_exists') in (None, False),
             'a failure record that declares an output .sim must not claim it is absent; got %r'
             % record.get('output_sim_exists')),
        ]
        for name, ok, message in checks:
            if not ok:
                problems.append(message)
        directory = record.get('working_directory')
        if not isinstance(directory, str) or a.trial_id not in directory:
            problems.append('the diagnosis was not written into the trial job directory: %r' % directory)

    summary = {
        'trial_id': a.trial_id,
        'controller_state': result.get('state'),
        'failure_record': str(evidence_path.relative_to(a.evidence))
        if evidence_path.is_file() else None,
        'diagnosis_on_disk': evidence_path.is_file(),
        'error': record.get('error'),
        'checks_passed': not problems,
        'problems': problems,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
