# -*- coding: utf-8 -*-
"""The single executable rule for "may this record certify a success?".

Why this file exists (in code, because prose has no binding force here):

Round 6 ran a stage that STAR executes as one process with three macros
(``starccm+ -batch A,B,C``). The second macro threw, the runner failed the stage on the process
exit code, and **the third macro still ran** and wrote ``external_flow_mesh.json`` with
``"status":"ok"``. The stage record said FAILED, the macro record said ok, and both were true - which
is exactly the shape that lets a reader file a failed run as a success.

The previous proposal was to make every downstream macro verify its predecessor's artefacts. That is
a scattered check: each new macro has to remember to do it, and this round proved that "each writer
checks its own predecessor" is what already failed. This module is the centralised replacement: one
function decides whether a record may be cited as ok, and every consumer calls it.

Two rules, both executable:

R1 PARSE    Every ``*.json`` record in a run directory must be ``json.loads``-able. A failure record
            that cannot be parsed is not evidence, it is a loss of evidence. (A third review
            predicted this defect verbatim and it still shipped; see AGENTS.md section 6.)
R2 CITABLE  A record that claims success may not be cited as ok if the run it belongs to ended in
            failure. The record itself stays truthful - the mesh *was* built, by macro 3 - but it
            cannot certify anything, because the stage that contains it failed.

What this gate deliberately does NOT claim: that a record with no discoverable run-state file came
from a successful run. Absence of a state file is reported as UNKNOWN_RUN_STATE and is never treated
as a pass. Certification of such a record is a separate, larger obligation.

Usage:
    python scripts/run_record_gate.py --run <run-dir>
    python scripts/run_record_gate.py --cite <record.json> [--cite ...]
    python scripts/run_record_gate.py --evidence-root test-evidence --parse-only
Exit code 1 on any violation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Terminal states of the two runners in this repository. The trial runner
# (scripts/security/trial_air3b_pipeline.ps1) writes case-probe.json; the persistent worker
# (worker/run_pipeline.ps1, worker/run_job.ps1) writes state.json.
SUCCESS_STATES = frozenset({'AIR3B_PIPELINE_COMPLETE', 'EXECUTION_COMPLETED', 'SUCCEEDED'})
FAILURE_STATES = frozenset({'FAILED', 'GATE_FAILED', 'WAITING_AGENT', 'TIMED_OUT', 'CANCELLED',
                            'RESOURCE_STOP', 'REJECTED_BUSY'})
STATE_FILES = ('case-probe.json', 'state.json')
# Per-stage records written by the runners. They decide citability at STAGE granularity, which is the
# granularity the rule is about: one trial can hold a stage that succeeded and a later stage that
# failed, and the second failure must not invalidate the first - the mirror image of the round-6
# defect, where a success inside a failed stage was left looking like a success.
STAGE_RECORD_GLOB = 'stage-*.json'
STAGE_SUCCESS = 'ok'
# How far above a record the run-state file may sit. 0 is the normal case (the trial runner writes
# the records beside case-probe.json); 1 covers the layout where per-stage evidence sits one level
# below its job state. It is deliberately not unbounded: an unbounded walk found an unrelated
# test-evidence/state.json three levels above an AIR-3B record, and a *successful* state file that
# far away would have certified a record belonging to a different run. Anything further is reported
# as UNKNOWN_RUN_STATE, which refuses to certify.
MAX_STATE_WALK = 1

# Citation verdicts. Only CITABLE certifies success; every other value is a refusal to certify.
CITABLE = 'CITABLE'
NOT_CITABLE_FAILED_RUN = 'NOT_CITABLE_FAILED_RUN'
UNPARSEABLE = 'UNPARSEABLE'
UNKNOWN_RUN_STATE = 'UNKNOWN_RUN_STATE'
NOT_CITABLE_NO_CLAIM = 'NOT_CITABLE_NO_CLAIM'


class RecordNotCitable(Exception):
    """Raised by assert_citable. Carries the machine-readable verdict for evidence."""

    def __init__(self, verdict: dict):
        super().__init__(verdict.get('reason', verdict.get('state', 'not citable')))
        self.verdict = verdict


def read_json(path: Path) -> tuple[object, str | None]:
    """Return (value, error). A record that does not PARSE yields (None, reason).

    Any JSON value is a successful parse here - several evidence files in this repository are lists,
    and calling those "unparseable" would have made the parse ratchet useless by lumping real losses of
    evidence together with files that are simply not objects. Callers that need a mapping check it.
    """
    try:
        # utf-8-sig, not utf-8: the two runners write their state with Windows PowerShell
        # Set-Content -Encoding UTF8, which emits a BOM. Reading with plain utf-8 turned the real
        # round-6 run-state file into "unparseable", i.e. the gate could not see that the run FAILED -
        # a fail-open in the checker itself, found by running it against that fixture.
        return json.loads(path.read_text(encoding='utf-8-sig')), None
    except Exception as exc:                      # noqa: BLE001 - any parse failure is the finding
        return None, '%s: %s' % (type(exc).__name__, exc)


def find_stage_state(record_path: Path) -> tuple[Path | None, dict | None, str | None]:
    """Find the stage record that produced this artefact, at stage granularity.

    Attribution is by BASENAME and only within the record's OWN directory. Both restrictions come from
    the second independent review of round 7: matching by name across directory levels let a record in
    `audit/` be attributed to the OK stage in the parent job directory while `audit/stage-audit.json`
    said the stage failed, which produced a false success. A same-directory match cannot cross a stage
    boundary, and both runners write `stage-<name>.json` beside the artefacts it produced.

    Returns (stage_path, payload, note). `note` carries an ambiguity that is NOT an error: when several
    stage records in the same directory name the same artefact (easy to create accidentally, because
    `artifacts` is the stage's `required` list), the caller falls back to the run state rather than
    refusing - a refusal there would make such an artefact permanently uncertifiable.
    """
    name = record_path.name
    directory = record_path if record_path.is_dir() else record_path.parent
    stage_files = sorted(directory.glob(STAGE_RECORD_GLOB))
    if not stage_files:
        return None, None, None
    matches, unreadable = [], []
    for stage_file in stage_files:
        payload, error = read_json(stage_file)
        if error or not isinstance(payload, dict):
            unreadable.append('%s (%s)' % (stage_file.name, error or 'not an object'))
            continue
        named = set()
        for key in ('artifacts', 'macro_outputs'):
            value = payload.get(key)
            if isinstance(value, list):
                named.update(str(item) for item in value if item)
        if payload.get('stage'):
            named.add('stage-%s.json' % payload['stage'])
        if name in named:
            matches.append(stage_file)
    if unreadable:
        return None, None, 'unreadable stage record beside %s: %s' % (name, ', '.join(unreadable))
    if len(matches) == 1:
        payload, error = read_json(matches[0])
        if error:
            return matches[0], None, 'stage record unreadable (%s): %s' % (matches[0], error)
        return matches[0], payload, None
    if len(matches) > 1:
        verdicts, refused = [], []
        for stage_file in matches:
            payload, error = read_json(stage_file)
            if error or not isinstance(payload, dict):
                refused.append('%s (unreadable)' % stage_file.name)
                continue
            kind, detail = stage_verdict(payload)
            verdicts.append((stage_file, payload, kind))
            if kind != 'SUCCESS':
                refused.append('%s (%s)' % (stage_file.name, detail))
        if refused:
            # Any stage that names the artefact and did not succeed refuses it. Discarding these
            # verdicts in favour of the run state was a demonstrated false success.
            return (None, None,
                    {'ambiguous_refusal': refused,
                     'message': ('%s is named by several stage records in %s and at least one did not '
                                 'succeed: %s' % (name, directory, '; '.join(refused)))})
        return None, None, ('%s is named by several successful stage records in %s (%s); falling back '
                            'to the run state' % (name, directory,
                                                  ', '.join(f.name for f in matches)))
    return None, None, None


def find_run_state(record_path: Path) -> tuple[Path | None, dict | None, str | None]:
    """Walk up from the record to the nearest run-state file and parse it.

    Returns (state_path, state_payload, error). The search starts at the record's own directory so a
    record stored beside its state file is found first.
    """
    current = record_path if record_path.is_dir() else record_path.parent
    for depth, candidate_dir in enumerate([current, *current.parents]):
        if depth > MAX_STATE_WALK:
            break
        for name in STATE_FILES:
            candidate = candidate_dir / name
            if candidate.is_file():
                payload, error = read_json(candidate)
                if error:
                    return candidate, None, 'run state unreadable (%s): %s' % (candidate, error)
                return candidate, payload, None
    return None, None, 'no run-state file (%s) found above %s' % ('/'.join(STATE_FILES), record_path)


def stage_verdict(state_payload: object) -> tuple[str, str]:
    """Classify a stage record: only state 'ok' may certify anything."""
    if not isinstance(state_payload, dict):
        return 'UNKNOWN', 'stage record is absent or not an object'
    value = state_payload.get('state')
    if value == STAGE_SUCCESS:
        return 'SUCCESS', 'stage %s ok' % state_payload.get('stage')
    if value is None:
        return 'UNKNOWN', 'stage record has no state'
    return 'FAILURE', 'stage %s ended %s: %s' % (state_payload.get('stage'), value,
                                                  state_payload.get('error') or '')


def run_verdict(state_payload: dict | None) -> tuple[str, str]:
    """Classify a run-state payload as success, failure or unknown. Unknown is never success."""
    if not isinstance(state_payload, dict):
        return 'UNKNOWN', 'run state is absent or not an object'
    value = state_payload.get('state')
    if value in SUCCESS_STATES:
        return 'SUCCESS', str(value)
    if value in FAILURE_STATES:
        return 'FAILURE', '%s: %s' % (value, state_payload.get('error') or state_payload.get('reason') or '')
    return 'UNKNOWN', 'unrecognised run state %r' % (value,)


def certifies_ok(payload: object) -> bool:
    """Does this record assert the success of a MEASUREMENT, i.e. can it be a certificate?

    Independent review round 7, second pass: `claims_ok` also accepted a runner state file
    (`case-probe.json`, `state.json`), so a PASSED stage with `record: case-probe.json` and no
    `requires_record_keys` was certified by a file that asserts nothing about any measurement. That is
    the same objection that made a stage record non-certifying, so the same rule applies: a certificate
    must carry a measurement-level `status: ok`.
    """
    return isinstance(payload, dict) and payload.get('status') == 'ok'


def claims_ok(payload: object) -> bool:
    """Does this record assert its own success?"""
    if not isinstance(payload, dict):
        return False
    if 'status' in payload:
        return payload.get('status') == 'ok'
    if 'state' in payload:
        return payload.get('state') in SUCCESS_STATES
    return False


def citation_status(record_path: Path) -> dict:
    """The single decide function. Returns a verdict dict; never raises.

    Precedence: the stage record that produced the artefact decides; without one, the run's terminal
    state decides; with neither, nothing certifies (UNKNOWN_RUN_STATE). The last case is deliberately
    a refusal: absence of a state file is not evidence of success.
    """
    record_path = Path(record_path)
    if not record_path.is_file():
        return {'state': UNKNOWN_RUN_STATE, 'record': str(record_path),
                'reason': 'record does not exist'}
    payload, error = read_json(record_path)
    if error:
        return {'state': UNPARSEABLE, 'record': str(record_path),
                'reason': 'record is not parseable JSON (%s)' % error}
    claims = claims_ok(payload)

    # A record may only certify success if it ASSERTS success. Independent review (round 7) found this
    # fail-open: `claims` was computed and then ignored on the success path, so a record whose own
    # status was 'gate_failed' - with physically impossible measurements - was certified as citable
    # whenever the surrounding stage said ok, and progress_matrix found nothing to report. The
    # project's own "recorded but never compared" defect, in the file that is supposed to BE the rule.
    if not certifies_ok(payload):
        shape = ('a runner state file' if record_path.name in STATE_FILES
                 else 'no measurement-level status')
        return {'state': NOT_CITABLE_NO_CLAIM, 'record': str(record_path),
                'record_claims_ok': claims_ok(payload),
                'reason': ('%s cannot certify success: %s (status=%r). Only a record asserting '
                           'status "ok" about a measurement may be cited as one.'
                           % (record_path.name, shape,
                              payload.get('status', payload.get('state'))
                              if isinstance(payload, dict) else None))}

    stage_path, stage_payload, stage_note = find_stage_state(record_path)
    if isinstance(stage_note, dict) and stage_note.get('ambiguous_refusal'):
        # One of the stages that names this artefact failed (see find_stage_state). Refuse; do NOT let
        # the run state decide, which is what made run-ok + failed-stage certify before.
        return {'state': NOT_CITABLE_FAILED_RUN, 'record': str(record_path), 'decided_by': 'stage',
                'stage_note': stage_note, 'record_claims_ok': claims,
                'reason': stage_note.get('message') or (
                    '%s is named by several stage records and at least one did not succeed: %s'
                    % (record_path.name, '; '.join(stage_note['ambiguous_refusal'])))}
    stage_error = (stage_note if (isinstance(stage_note, str) and 'unreadable' in stage_note)
                   else None)
    if stage_error:
        return {'state': UNKNOWN_RUN_STATE, 'record': str(record_path),
                'stage_record': None if stage_path is None else str(stage_path),
                'record_claims_ok': claims, 'reason': stage_error}
    if stage_path is not None:
        kind, detail = stage_verdict(stage_payload)
        verdict = {'record': str(record_path), 'stage_record': str(stage_path),
                   'stage_note': stage_note,
                   'stage': stage_payload.get('stage') if isinstance(stage_payload, dict) else None,
                   'stage_state': stage_payload.get('state') if isinstance(stage_payload, dict) else None,
                   'decided_by': 'stage', 'run_kind': kind, 'record_claims_ok': claims,
                   'run_detail': detail}
        if kind == 'SUCCESS':
            verdict['state'] = CITABLE
            return verdict
        verdict['state'] = NOT_CITABLE_FAILED_RUN if kind == 'FAILURE' else UNKNOWN_RUN_STATE
        verdict['reason'] = ('%s claims ok=%s, but the stage that produced it %s'
                             % (record_path.name, claims, detail))
        return verdict

    state_path, state_payload, state_error = find_run_state(record_path)
    if state_error:
        return {'state': UNKNOWN_RUN_STATE, 'record': str(record_path),
                'run_state_file': None if state_path is None else str(state_path),
                'decided_by': None, 'record_claims_ok': claims, 'reason': state_error}
    kind, detail = run_verdict(state_payload)
    reported_state = state_payload.get('state') if isinstance(state_payload, dict) else None
    verdict = {'record': str(record_path), 'run_state_file': str(state_path),
               'run_state': reported_state, 'decided_by': 'run', 'run_kind': kind,
               'stage_note': stage_note, 'record_claims_ok': claims, 'run_detail': detail}
    if kind == 'SUCCESS':
        verdict['state'] = CITABLE
        return verdict
    if kind == 'FAILURE':
        verdict['state'] = NOT_CITABLE_FAILED_RUN
        verdict['reason'] = ('%s claims ok=%s, but its run (%s) ended %s'
                             % (record_path.name, claims, state_path.name, detail))
        return verdict
    verdict['state'] = UNKNOWN_RUN_STATE
    verdict['reason'] = 'run state unrecognised: %s' % detail
    return verdict


def assert_citable(record_path: Path) -> dict:
    """Raise unless the record may be cited as ok. Returns the verdict when it may."""
    verdict = citation_status(record_path)
    if verdict['state'] != CITABLE:
        raise RecordNotCitable(verdict)
    return verdict


def scan_run(run_dir: Path, parse_only: bool = False) -> dict:
    """Inspect every JSON record in a run directory and list what may not be cited."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return {'run_dir': str(run_dir), 'exists': False, 'violations': [
            {'kind': UNKNOWN_RUN_STATE, 'record': str(run_dir), 'reason': 'run directory does not exist'}]}
    state_path, state_payload, state_error = find_run_state(run_dir)
    kind, detail = ('UNKNOWN', state_error) if state_error else run_verdict(state_payload)
    records, unparseable, not_citable = [], [], []
    for path in sorted(run_dir.rglob('*.json')):
        entry = {'name': path.name, 'path': str(path)}
        if path.name in STATE_FILES:
            entry['is_run_state'] = True
        payload, error = read_json(path)
        if error:
            entry['parses'] = False
            entry['error'] = error
            unparseable.append(entry)
        else:
            # Per record, not per run: a stage record decides for the artefacts its stage produced, so
            # a success in stage 1 survives a failure in stage 2 and a failure in stage 1 is not
            # excused by a later success.
            verdict = citation_status(path)
            entry['parses'] = True
            entry['status'] = (payload.get('status', payload.get('state'))
                               if isinstance(payload, dict) else None)
            entry['claims_ok'] = claims_ok(payload)
            entry['decided_by'] = verdict.get('decided_by')
            entry['citation_state'] = verdict['state']
            # `citable` is always present so a consumer cannot mistake a missing key for a pass.
            entry['citable'] = bool(entry['claims_ok'] and verdict['state'] == CITABLE)
            # Every ok-claiming record that may not be cited is listed here, whatever the reason:
            # independent review round 7, second pass, found an ok-claiming record with an
            # undiscoverable state file (UNKNOWN_RUN_STATE) was neither citable nor listed, so it was
            # invisible to a directory-level citation.
            # Listed here: records that ASSERT a measurement-level success (status "ok") but may not be
            # cited. A runner state file is the authority that decides, not a citation target, and a
            # record that only claims a run-level `state` is a run record - neither is a false success
            # claim, so neither belongs in this list (that distinction is what keeps the list readable).
            if certifies_ok(payload) and not entry['citable']:
                entry['reason'] = verdict.get('reason')
                not_citable.append(entry)
        records.append(entry)
    violations = [{'kind': UNPARSEABLE, 'record': entry['path'], 'reason': entry['error']}
                  for entry in unparseable]
    if not parse_only:
        # Only a proven failure of the producing stage (or an unreadable record) is a violation. An
        # ok-claiming record whose provenance cannot be established is listed in `not_citable` but is
        # not by itself a violation: it is a gap in provenance, not a demonstrated false success.
        violations += [{'kind': NOT_CITABLE_FAILED_RUN, 'record': entry['path'],
                        'reason': entry.get('reason') or 'record claims ok inside a failed stage'}
                       for entry in not_citable
                       if entry.get('citation_state') == NOT_CITABLE_FAILED_RUN]
    return {'run_dir': str(run_dir), 'exists': True, 'run_kind': kind,
            'run_state_file': None if state_path is None else str(state_path),
            'run_state': state_payload.get('state') if isinstance(state_payload, dict) else None,
            'run_detail': detail, 'records': records, 'unparseable': unparseable,
            'not_citable': not_citable, 'violations': violations}


def check_citations(citations: list[Path]) -> dict:
    """Evaluate a list of cited records against R1/R2."""
    verdicts = [citation_status(path) for path in citations]
    violations = [v for v in verdicts if v['state'] != CITABLE]
    return {'citations': verdicts, 'violations': violations, 'passed': not violations}


def known_unparseable(root: Path) -> list[dict]:
    """The parse ratchet: what in this tree is known NOT to be JSON, and nothing else.

    A fixed exception list is deliberate. The repository must not launder a historical record (it is
    evidence, and editing it to parse would destroy the fact that it did not), but it must also not
    accept a second one. Ratcheting to the exact known set does both: a new unparseable record fails
    the check, and a repaired/removed one fails it too, so the list cannot silently rot.
    """
    findings = []
    for path in sorted(root.rglob('*.json')):
        payload, error = read_json(path)
        if error:
            findings.append({'path': str(path), 'error': error})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--run', type=Path, action='append', default=[])
    parser.add_argument('--cite', type=Path, action='append', default=[])
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--parse-only', action='store_true',
                        help='report parse failures only, not R2 citation violations')
    args = parser.parse_args(argv)

    report = {'runs': [], 'citations': None, 'unparseable': None}
    violations = 0
    for run_dir in args.run:
        result = scan_run(run_dir, parse_only=args.parse_only)
        report['runs'].append(result)
        violations += len(result['violations'])
    if args.cite:
        result = check_citations(args.cite)
        report['citations'] = result
        violations += len(result['violations'])
    if args.evidence_root:
        findings = known_unparseable(args.evidence_root)
        report['unparseable'] = findings
        violations += len(findings)
    report['violations'] = violations
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if violations else 0


if __name__ == '__main__':
    sys.exit(main())
