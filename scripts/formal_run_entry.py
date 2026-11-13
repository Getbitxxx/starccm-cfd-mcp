# -*- coding: utf-8 -*-
"""The FORMAL entry: submit a full plan through the MCP layer, poll it, then retrieve + verify + report.

Why this file exists (②R12)
---------------------------
The restricted-trial entry (`scripts/run_static_external_flow_e2e.py`) already did submit -> poll ->
fetch -> report for the reviewed trial mechanism. The formal path (`submit_run` -> `worker/run_pipeline.ps1`
-> `worker/run_job.ps1`) had no such entry, so "the formal path produces the report" was only a claim and
`report` was counted for the restricted mechanism alone. This is the formal counterpart, and it is what
makes the report AUTOMATIC rather than a manual afterthought.

It calls the same `Backend` methods the MCP tools expose (`submit_run`, `get_run`, `report_run`), so the
formal MCP entry and this command are one code path. It never omits the CaseSpec, never fabricates a
capability and never skips a study operation to make the plan look executable: `submit_run` applies the
full plan gate to the SELECTED plan and refuses otherwise.

Usage
-----
    python3 scripts/formal_run_entry.py \
        --case-ref library/<sha256>.sim --request-id <id> \
        --case-spec cases/<case>/case_spec.json \
        --operations build_static_body --operations audit_static_body ... \
        --review-ref review/<record>.json \
        [--inspect-job-id <successful core preflight>] [--parameters '<json>'] \
        --evidence test-evidence/<case>/formal-runs [--case-label X] \
        [--timeout 172800] [--poll-seconds 30] [--strict] [--submit-only]

Exit codes: 0 when a report was produced (whatever its verdict), 2 with --strict when the verdict is not
REPORT_OK, 1 when the submission or the adapter failed. A run that FAILED is not a command failure: it
still produces an honest report, which is the point.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

# Terminal run states of worker/run_pipeline.ps1. Everything else is still executing.
TERMINAL_STATES = frozenset({'EXECUTION_COMPLETED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'RESOURCE_STOP',
                             'REJECTED_BUSY', 'WAITING_AGENT', 'INTERRUPTED'})


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def wait_for_terminal(backend, run_id: str, poll_seconds: float = 30.0,
                      deadline_seconds: float | None = None):
    """Poll `get_run` until the run reaches a terminal state. Returns the last status payload."""
    started = time.monotonic()
    while True:
        status = backend.get_run(run_id)
        state = (status.get('status') or {}).get('state')
        if state in TERMINAL_STATES:
            return status
        if deadline_seconds is not None and (time.monotonic() - started) >= deadline_seconds:
            return status
        time.sleep(max(1.0, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-ref', required=True)
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--case-spec', type=Path, required=True,
                        help='the CaseSpec JSON; a full formal run may not omit it')
    parser.add_argument('--operations', action='append', default=[],
                        help='the plan, in order: catalog operation ids (repeatable)')
    parser.add_argument('--steps', type=Path, default=None,
                        help='diagnostic graphs only: a full CaseSpec run must use --operations so the '
                             'pre/post plan steps are members of the plan')
    parser.add_argument('--review-ref', required=True)
    parser.add_argument('--inspect-job-id', default=None)
    parser.add_argument('--parameters', default=None, help='JSON object: operation id -> {NAME: value}')
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--results-dir', type=Path, default=None,
                        help='where the run directory is DOWNLOADED to. Defaults to --evidence, so the transfer '
                             'stays inside this project: the runner connection file may point local_results at a '
                             'directory outside it, which the ②R13 formal run measured as a hard failure of the '
                             'report step (PermissionError) long after the STAR work was already done.')
    parser.add_argument('--case-label', default=None)
    parser.add_argument('--timeout', type=int, default=172800,
                        help='outer window in seconds: DECLARED in the request (observational on the '
                             'worker) and used as the default poll deadline, so the controller cannot '
                             'wait forever past the window it itself declared')
    parser.add_argument('--poll-seconds', type=float, default=30.0)
    parser.add_argument('--wait-seconds', type=float, default=None,
                        help='give up polling after this long (the run keeps going; report is skipped). '
                             'Default: --timeout')
    parser.add_argument('--submit-only', action='store_true')
    parser.add_argument('--strict', action='store_true')
    arguments = parser.parse_args()

    case_spec = load(arguments.case_spec)
    if not isinstance(case_spec, dict):
        raise ValueError('the CaseSpec must be a JSON object')
    parameters = json.loads(arguments.parameters) if arguments.parameters else None
    if arguments.steps:
        steps = load(arguments.steps)
        if arguments.operations:
            raise ValueError('give either --operations or --steps, not both')
        # R12: with a CaseSpec the FULL plan gate judges coverage from the operations that are plan
        # members. Raw steps cannot name the pre/post offline steps (classify_parts / export_evidence /
        # report), so a full run must be expressed as --operations; refusing here is clearer than a plan
        # blocker that looks like a missing capability.
        raise ValueError('a full CaseSpec run must name its plan with --operations (so the pre/post steps '
                         'are part of the plan); --steps is for diagnostic graphs without a CaseSpec')
    else:
        steps = None
        if not arguments.operations:
            raise ValueError('the formal entry needs the plan: pass --operations')

    from backend import Backend
    backend = Backend()
    # A formal submission needs the reviewed runtime DEPLOYED on the worker. The ②R13 acceptance run found this
    # the hard way: submit_run refused with "Deploy this runtime before submission" because the entry never
    # performed the deploy that the MCP exposes separately. It is idempotent (create dirs + copy the pinned
    # bytes) and it is a no-op when the runtime is already there, so the entry now guarantees its own
    # precondition instead of relying on the operator having run deploy by hand.
    from backend import ps_quote
    runtime = backend.remote_root + '\\runtime\\' + backend.runtime_digest()[:16]
    deployed = backend.ps('$p=' + ps_quote(runtime + '\\run_pipeline.ps1') +
                          "\n[pscustomobject]@{deployed=(Test-Path -LiteralPath $p)} | ConvertTo-Json")
    # Presence-only on purpose: the runner itself re-verifies `pipeline_runner_sha256` and `runner_sha256`, so a
    # corrupted-but-present runtime is refused there rather than trusted here. A probe that does not come back as
    # an object is treated as "not deployed" (fail closed) instead of raising AttributeError.
    if not isinstance(deployed, dict) or not deployed.get('deployed'):
        backend.deploy()
    if arguments.results_dir is not None:
        backend.config['local_results'] = str(arguments.results_dir.expanduser().resolve())
    else:
        # keep the download beside the report unless the caller says otherwise
        backend.config['local_results'] = str(arguments.evidence.expanduser().resolve())
    submission = backend.submit_run(arguments.case_ref, arguments.request_id, steps=steps,
                                   timeout_seconds=arguments.timeout, case_spec=case_spec,
                                   inspect_job_id=arguments.inspect_job_id,
                                   review_ref=arguments.review_ref,
                                   operations=arguments.operations or None, parameters=parameters)
    print(json.dumps({'submitted': True, 'submission': submission}, ensure_ascii=False, indent=2))
    if arguments.submit_only:
        return 0

    # 2026-10-21 review fix (A4a): the poll loop used to wait forever by default. --timeout is the outer
    # window DECLARED in the request (observational on the worker), so a controller that keeps waiting
    # while the run sits past its own declared window is exactly the claim/evidence mismatch this project
    # exists to prevent. The poll now defaults to that window; --wait-seconds still overrides explicitly.
    wait_deadline = (arguments.wait_seconds if arguments.wait_seconds is not None
                     else float(arguments.timeout))
    status = wait_for_terminal(backend, arguments.request_id, arguments.poll_seconds,
                               wait_deadline)
    state = (status.get('status') or {}).get('state')
    print(json.dumps({'run_id': arguments.request_id, 'state': state,
                      'reused': submission.get('reused') if isinstance(submission, dict) else None},
                     ensure_ascii=False))
    if state not in TERMINAL_STATES:
        print(json.dumps({'report_skipped': 'the run has not reached a terminal state yet',
                          'state': state}, ensure_ascii=False))
        return 1

    outcome = backend.report_run(arguments.request_id, str(arguments.evidence),
                                 case_label=arguments.case_label, include_sim=True,
                                 strict=arguments.strict)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    if arguments.strict and (outcome['report'].get('verdict') or '') != 'REPORT_OK':
        return 2
    return 0


if __name__ == '__main__':
    if sys.version_info < (3, 12):
        # 2026-10-21 review fix (A2): scripts/formal_worker.py uses PEP 701 f-string syntax and raises a
        # bare SyntaxError on older interpreters. Fail here with the reason instead.
        raise SystemExit('starccm-cfd controller scripts require Python >= 3.12 '
                         '(scripts/formal_worker.py uses PEP 701 f-string syntax); found %d.%d.%d'
                         % sys.version_info[:3])
    sys.exit(main())
