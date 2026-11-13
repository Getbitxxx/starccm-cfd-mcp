#!/usr/bin/env python3
"""Single writer + self-consistency gate for campaigns/uav-2026/STATUS.json.

WHY THIS EXISTS (measured defect, P2 closeout 2026-10-30). The campaign state file accumulated
mutually contradictory statements: the case block carried ``q3_status: "NOT_ACHIEVED"`` while the
same document recorded ``q: "Q3"`` and ``q3_claim: true``. Nothing refused the contradictory
document, so the drift was caught only by a human at closeout. This module turns those
contradictions into a mechanical refusal at write time, and seals the content with an integrity
hash so tampering/staleness is detectable on read.

Two tiers, deliberately separated:

``validate_status(document)`` -> BLOCKING violations with stable codes. ``write_status()`` REFUSES
    on any of them and leaves the target file byte-identical.
``stale_findings(document)``  -> ADVISORY leftovers (a case-level ``q_state`` that disagrees with
    ``q``, an old ``q3_report.status`` still saying NOT_ACHIEVED). They do NOT block a write,
    because the live document still carries such leftovers today; ``--check-consistency`` reports
    them and exits 2 so they cannot be forgotten. Exit 2 is "needs cleanup", exit 1 is "refused".

The writer always adds an ``integrity`` block and a ``write_log`` entry, writes atomically
(temp file + ``os.replace``), and re-reads the bytes it just wrote to verify them. Documents
without an ``integrity`` block stay readable (legacy), but every write re-seals them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / 'campaigns' / 'uav-2026' / 'STATUS.json'

Q_STATES = ('Q0', 'Q1', 'Q2', 'Q3')
Q_RANK = {q: i for i, q in enumerate(Q_STATES)}
PHASE_PREFIXES = ('P0', 'P1', 'P2', 'P3', 'P4')

INTEGRITY_KEY = 'integrity'
WRITE_LOG_KEY = 'write_log'
CANONICAL = 'json.dumps(document minus the integrity key, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False), UTF-8'
ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')
NOT_ACHIEVED = 'NOT_ACHIEVED'


@dataclass(frozen=True)
class Violation:
    """One machine-checkable finding. ``code`` is stable; ``case_id``/``field`` localise it."""

    code: str
    message: str
    case_id: str | None = None
    field: str | None = None
    severity: str = 'violation'  # 'violation' (blocking) | 'advisory'

    def to_dict(self) -> dict[str, Any]:
        return {'code': self.code, 'severity': self.severity, 'case_id': self.case_id,
                'field': self.field, 'message': self.message}

    def __str__(self) -> str:
        where = '/'.join(part for part in (self.case_id, self.field) if part)
        return '%s%s: %s' % (self.code, ' [%s]' % where if where else '', self.message)


class StatusWriteRefused(Exception):
    """The candidate document failed validation; the target file was not touched."""

    def __init__(self, path: Path, violations: list[Violation]):
        self.path = Path(path)
        self.violations = list(violations)
        super().__init__('%s refused: %d violation(s): %s' % (
            self.path, len(self.violations), '; '.join(str(v) for v in self.violations)))

    def to_dict(self) -> dict[str, Any]:
        return {'refused': True, 'path': str(self.path),
                'violations': [v.to_dict() for v in self.violations]}


class StatusWriteVerificationFailed(Exception):
    """The bytes produced by the writer did not re-validate; the on-disk state is reported."""


# --------------------------------------------------------------------------- hashing


def content_without_integrity(document: dict) -> dict:
    return {key: value for key, value in document.items() if key != INTEGRITY_KEY}


def canonical_bytes(document: dict) -> bytes:
    """Canonical bytes covered by the integrity hash (the integrity block itself is excluded)."""
    return json.dumps(content_without_integrity(document), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def content_sha256(document: dict) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- validation


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _phase_key_for(states: dict, prefix: str) -> str | None:
    if prefix in states:
        return prefix
    for key in states:
        if isinstance(key, str) and (key.startswith(prefix + '_') or key.startswith(prefix + '-')):
            return key
    return None


def _case_name(case: Any, index: int) -> str:
    if isinstance(case, dict) and _nonempty_str(case.get('case_id')):
        return str(case['case_id'])
    return 'cases[%d]' % index


def _validate_case(case: Any, index: int) -> list[Violation]:
    name = _case_name(case, index)
    if not isinstance(case, dict):
        return [Violation('STATUS_CASE_NOT_OBJECT', 'case entry must be an object',
                          case_id=name, field='cases[%d]' % index)]
    out: list[Violation] = []
    if not _nonempty_str(case.get('case_id')):
        out.append(Violation('STATUS_CASE_ID_MISSING', 'case_id must be a non-empty string',
                             case_id=name, field='case_id'))
    q = case.get('q')
    target = case.get('target_q')
    q_ok = q in Q_STATES
    target_ok = target in Q_STATES
    if not q_ok:
        out.append(Violation('STATUS_CASE_Q_INVALID',
                             'q=%r is not one of %s' % (q, list(Q_STATES)), case_id=name, field='q'))
    if not target_ok:
        out.append(Violation('STATUS_CASE_TARGET_Q_INVALID',
                             'target_q=%r is not one of %s' % (target, list(Q_STATES)),
                             case_id=name, field='target_q'))
    if q_ok and target_ok and Q_RANK[q] > Q_RANK[target]:
        out.append(Violation('STATUS_CASE_Q_ABOVE_TARGET',
                             'q=%s ranks above target_q=%s' % (q, target),
                             case_id=name, field='q'))
    claim_present = 'q3_claim' in case
    claim_value = case.get('q3_claim')
    if claim_present and not isinstance(claim_value, bool):
        out.append(Violation('STATUS_Q3_CLAIM_NOT_BOOL',
                             'q3_claim=%r must be a JSON boolean (absent means false)' % (claim_value,),
                             case_id=name, field='q3_claim'))
    claim = claim_value is True
    if claim:
        if q != 'Q3':
            out.append(Violation('STATUS_Q3_CLAIM_NOT_Q3',
                                 'q3_claim=true while q=%r' % (q,), case_id=name, field='q3_claim'))
        if case.get('quality_state') != 'Q3':
            out.append(Violation('STATUS_Q3_CLAIM_QUALITY_STATE',
                                 'q3_claim=true requires quality_state="Q3", found %r'
                                 % (case.get('quality_state'),), case_id=name, field='quality_state'))
        if case.get('engineering_verified') is not True:
            out.append(Violation('STATUS_Q3_CLAIM_NOT_ENGINEERING_VERIFIED',
                                 'q3_claim=true requires engineering_verified=true, found %r'
                                 % (case.get('engineering_verified'),),
                                 case_id=name, field='engineering_verified'))
        if case.get('case_spec_frozen') is not True:
            out.append(Violation('STATUS_Q3_CLAIM_SPEC_NOT_FROZEN',
                                 'q3_claim=true requires case_spec_frozen=true, found %r'
                                 % (case.get('case_spec_frozen'),), case_id=name,
                                 field='case_spec_frozen'))
        if case.get('tolerance_signed') is not True:
            out.append(Violation('STATUS_Q3_CLAIM_TOLERANCE_NOT_SIGNED',
                                 'q3_claim=true requires tolerance_signed=true, found %r'
                                 % (case.get('tolerance_signed'),), case_id=name,
                                 field='tolerance_signed'))
    q3_status = case.get('q3_status')
    not_achieved = isinstance(q3_status, str) and q3_status.strip().upper() == NOT_ACHIEVED
    if not_achieved:
        if q == 'Q3':
            out.append(Violation('STATUS_Q3_STATUS_CONTRADICTS_Q',
                                 'q3_status=%r while q="Q3"' % (q3_status,),
                                 case_id=name, field='q3_status'))
        if claim:
            out.append(Violation('STATUS_Q3_STATUS_CONTRADICTS_CLAIM',
                                 'q3_status=%r while q3_claim=true' % (q3_status,),
                                 case_id=name, field='q3_status'))
    if q == 'Q3':
        if not _nonempty_str(q3_status):
            out.append(Violation('STATUS_Q3_STATUS_MISSING',
                                 'q="Q3" requires a non-empty q3_status', case_id=name,
                                 field='q3_status'))
        if not _nonempty_str(case.get('reference')):
            out.append(Violation('STATUS_Q3_REFERENCE_MISSING',
                                 'q="Q3" requires a reference path', case_id=name, field='reference'))
        if not _nonempty_str(case.get('record')):
            out.append(Violation('STATUS_Q3_RECORD_MISSING',
                                 'q="Q3" requires a citable record path', case_id=name,
                                 field='record'))
    return out


def _validate_q_counts(document: dict, cases: list) -> list[Violation]:
    declared = document.get('q_counts')
    if not isinstance(declared, dict):
        return [Violation('STATUS_Q_COUNTS_NOT_OBJECT', 'q_counts must be an object',
                          field='q_counts')]
    out: list[Violation] = []
    tally = {q: 0 for q in Q_STATES}
    for case in cases:
        if isinstance(case, dict) and case.get('q') in Q_STATES:
            tally[case['q']] += 1
    for q in Q_STATES:
        value = declared.get(q, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            out.append(Violation('STATUS_Q_COUNTS_VALUE_NOT_INT',
                                 'q_counts.%s=%r must be an integer' % (q, value),
                                 field='q_counts.%s' % q))
            continue
        if value != tally[q]:
            out.append(Violation('STATUS_Q_COUNTS_MISMATCH',
                                 'q_counts.%s=%d but the case list tallies %d' % (q, value, tally[q]),
                                 field='q_counts.%s' % q))
    for key in declared:
        if key not in Q_STATES:
            out.append(Violation('STATUS_Q_COUNTS_UNKNOWN_STATE',
                                 'q_counts has unknown state %r (allowed: %s)'
                                 % (key, list(Q_STATES)), field='q_counts.%s' % key))
    return out


def _validate_date(document: dict, key: str, code: str) -> list[Violation]:
    value = document.get(key)
    if not _nonempty_str(value) or not ISO_DATE.match(value):
        return [Violation(code, '%s=%r must be an ISO-8601 date YYYY-MM-DD' % (key, value),
                          field=key)]
    try:
        datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return [Violation(code, '%s=%r is not a real calendar date' % (key, value), field=key)]
    return []


def _validate_integrity(document: dict) -> list[Violation]:
    if INTEGRITY_KEY not in document:
        return []  # legacy document: readable, re-sealed on the next write
    integrity = document.get(INTEGRITY_KEY)
    if not isinstance(integrity, dict):
        return [Violation('STATUS_INTEGRITY_NOT_OBJECT', 'integrity must be an object',
                          field=INTEGRITY_KEY)]
    if 'content_sha256' not in integrity:
        return [Violation('STATUS_INTEGRITY_HASH_MISSING',
                          'integrity exists but carries no content_sha256', field='integrity.content_sha256')]
    declared = integrity.get('content_sha256')
    if not isinstance(declared, str) or not SHA256_HEX.match(declared.lower()):
        return [Violation('STATUS_INTEGRITY_SHA_NOT_HEX',
                          'integrity.content_sha256=%r is not a lowercase sha256 hex digest'
                          % (declared,), field='integrity.content_sha256')]
    actual = content_sha256(document)
    if actual != declared.lower():
        return [Violation('STATUS_INTEGRITY_MISMATCH',
                          'content hash mismatch: declared %s, recomputed %s (content edited '
                          'after sealing or stale)' % (declared, actual),
                          field='integrity.content_sha256')]
    return []


def validate_status(document: Any) -> list[Violation]:
    """Return the BLOCKING violations of one STATUS document (empty list == valid)."""
    if not isinstance(document, dict):
        return [Violation('STATUS_DOCUMENT_NOT_OBJECT',
                          'STATUS document must be a JSON object, got %s' % type(document).__name__)]
    out: list[Violation] = []
    phase = document.get('phase')
    if not _nonempty_str(phase):
        out.append(Violation('STATUS_PHASE_MISSING', 'phase must be a non-empty string',
                             field='phase'))
    states = document.get('phase_states')
    if not isinstance(states, dict) or not states:
        out.append(Violation('STATUS_PHASE_STATES_MISSING',
                             'phase_states must be a non-empty object', field='phase_states'))
    else:
        for prefix in PHASE_PREFIXES:
            if _phase_key_for(states, prefix) is None:
                out.append(Violation('STATUS_PHASE_STATE_KEY_MISSING',
                                     'phase_states has no key for %s' % prefix,
                                     field='phase_states'))
        if _nonempty_str(phase) and phase not in states:
            out.append(Violation('STATUS_PHASE_NOT_A_STATE',
                                 'phase=%r is not a key of phase_states (keys: %s)'
                                 % (phase, sorted(states)), field='phase'))
    cases = document.get('cases')
    if not isinstance(cases, list):
        out.append(Violation('STATUS_CASES_NOT_LIST', 'cases must be a list', field='cases'))
    else:
        for index, case in enumerate(cases):
            out.extend(_validate_case(case, index))
        out.extend(_validate_q_counts(document, cases))
    out.extend(_validate_date(document, 'updated', 'STATUS_UPDATED_NOT_ISO_DATE'))
    out.extend(_validate_date(document, 'last_updated', 'STATUS_LAST_UPDATED_NOT_ISO_DATE'))
    updated = document.get('updated')
    last_updated = document.get('last_updated')
    if _nonempty_str(updated) and _nonempty_str(last_updated) and updated != last_updated:
        out.append(Violation('STATUS_UPDATED_MISMATCH',
                             'updated=%r != last_updated=%r' % (updated, last_updated),
                             field='updated'))
    if not _nonempty_str(document.get('last_round')):
        out.append(Violation('STATUS_LAST_ROUND_MISSING',
                             'last_round must be a non-empty string', field='last_round'))
    out.extend(_validate_integrity(document))
    return out


def stale_findings(document: Any) -> list[Violation]:
    """ADVISORY leftovers: claims that used to be true and were not removed when they stopped being true."""
    if not isinstance(document, dict):
        return []
    out: list[Violation] = []
    cases = document.get('cases') if isinstance(document.get('cases'), list) else []
    q3_cases = [c for c in cases if isinstance(c, dict) and c.get('q') == 'Q3']

    def note_report(report: Any, case_id: str | None, where: str) -> None:
        if not isinstance(report, dict):
            return
        status = report.get('status')
        if isinstance(status, str) and NOT_ACHIEVED in status.upper():
            out.append(Violation('STATUS_STALE_Q3_REPORT_STATUS',
                                 '%s carries status=%r while the case stands at Q3; a reader can '
                                 'quote the old verdict as current' % (where, status),
                                 case_id=case_id, field='q3_report.status',
                                 severity='advisory'))

    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = case.get('case_id') if _nonempty_str(case.get('case_id')) else None
        q_state = case.get('q_state')
        if q_state in Q_STATES and case.get('q') in Q_STATES and q_state != case.get('q'):
            out.append(Violation('STATUS_STALE_Q_STATE',
                                 'q_state=%r disagrees with q=%r' % (q_state, case.get('q')),
                                 case_id=case_id, field='q_state', severity='advisory'))
        if case.get('q') == 'Q3':
            note_report(case.get('q3_report'), case_id, 'case q3_report')
    if q3_cases:
        note_report(document.get('q3_report'), None, 'top-level q3_report')
    return out


def check_document(document: Any) -> dict[str, list[Violation]]:
    return {'violations': validate_status(document), 'advisories': stale_findings(document)}


# --------------------------------------------------------------------------- write


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def dump_document(document: dict) -> bytes:
    """The on-disk rendering: one-space indent (the style of the live STATUS.json) + newline."""
    return json.dumps(document, indent=1, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'


def _verify_written(payload: bytes, document: dict) -> list[Violation]:
    """Re-parse the exact bytes we are about to install and re-check them."""
    try:
        parsed = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover - writer bug
        return [Violation('STATUS_WRITTEN_UNPARSABLE', 'written bytes are not valid JSON: %s' % exc)]
    problems = validate_status(parsed)
    if dump_document(parsed) != payload:
        problems.append(Violation('STATUS_WRITTEN_NONCANONICAL',
                                  'written bytes are not the canonical rendering of their own JSON'))
    if problems:
        return problems
    if content_sha256(parsed) != (parsed.get(INTEGRITY_KEY) or {}).get('content_sha256'):
        problems.append(Violation('STATUS_WRITTEN_INTEGRITY_MISMATCH',
                                  'the integrity hash does not cover the written content'))
    return problems


def write_status(path: str | Path, document: dict, actor: str, reason: str, *,
                 history_limit: int = 50, verify: bool = True) -> dict[str, Any]:
    """Validate, seal (integrity + write log), write atomically, then verify the bytes on disk.

    Refusal leaves the target file byte-identical: validation happens before any temp file exists.
    Returns a summary dict; raises StatusWriteRefused / StatusWriteVerificationFailed otherwise.
    """
    target = Path(path)
    if not _nonempty_str(actor):
        raise ValueError('actor must be a non-empty string')
    if not _nonempty_str(reason):
        raise ValueError('reason must be a non-empty string')
    candidate = copy.deepcopy(document)
    violations = validate_status(candidate)
    if violations:
        raise StatusWriteRefused(target, violations)

    previous_bytes = target.read_bytes() if target.is_file() else None
    previous_content = None
    previous_file = None
    previous_doc = None
    if previous_bytes is not None:
        try:
            previous_doc = json.loads(previous_bytes.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StatusWriteRefused(target, [Violation(
                'STATUS_EXISTING_UNREADABLE',
                'existing file cannot be parsed, so its previous content hash cannot be recorded '
                'honestly: %s' % exc)]) from exc
        previous_content = content_sha256(previous_doc)
        previous_file = file_sha256(previous_bytes)

    candidate_log = candidate.get(WRITE_LOG_KEY)
    if candidate_log is not None and not isinstance(candidate_log, list):
        raise StatusWriteRefused(target, [Violation(
            'STATUS_WRITE_LOG_NOT_LIST', 'write_log must be a list', field=WRITE_LOG_KEY)])
    # The write log belongs to the FILE, not to the candidate: an existing file's history is
    # append-only, so a candidate cannot drop or rewrite earlier entries.
    if previous_doc is not None:
        previous_log = previous_doc.get(WRITE_LOG_KEY)
        if previous_log is None:
            previous_log = []
        elif not isinstance(previous_log, list):
            raise StatusWriteRefused(target, [Violation(
                'STATUS_EXISTING_WRITE_LOG_NOT_LIST',
                'the existing file has a non-list write_log; refusing to overwrite its history',
                field=WRITE_LOG_KEY)])
        log = copy.deepcopy(previous_log)
    else:
        log = copy.deepcopy(candidate_log or [])
    candidate[WRITE_LOG_KEY] = log
    written_at = _utc_now()
    log.append({'actor': actor, 'reason': reason, 'written_at': written_at,
                'previous_content_sha256': previous_content,
                'previous_file_sha256': previous_file})
    if history_limit and len(log) > history_limit:
        del log[:len(log) - history_limit]

    candidate[INTEGRITY_KEY] = {
        'algorithm': 'sha256',
        'content_sha256': content_sha256(candidate),
        'canonicalization': CANONICAL,
        'previous_content_sha256': previous_content,
        'written_by': actor,
        'written_at': written_at,
    }

    payload = dump_document(candidate)
    problems = _verify_written(payload, candidate)
    if problems:
        raise StatusWriteRefused(target, problems)

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp',
                                         dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if temp_path.read_bytes() != payload:
            raise StatusWriteVerificationFailed('temp file bytes differ from the intended payload')
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():  # only on failure paths
            temp_path.unlink()

    if verify:
        on_disk = target.read_bytes()
        problems = _verify_written(on_disk, json.loads(on_disk.decode('utf-8')))
        if problems:
            raise StatusWriteVerificationFailed('%s written but did not verify: %s'
                                                % (target, '; '.join(str(p) for p in problems)))
        if on_disk != payload:  # pragma: no cover - concurrent writer
            raise StatusWriteVerificationFailed('%s changed between replace and verification'
                                               % target)
    return {
        'path': str(target),
        'written_at': written_at,
        'actor': actor,
        'reason': reason,
        'content_sha256': content_sha256(candidate),
        'file_sha256': file_sha256(payload),
        'bytes_written': len(payload),
        'previous_content_sha256': previous_content,
        'previous_file_sha256': previous_file,
        'write_log_entries': len(log),
        'advisories': [v.to_dict() for v in stale_findings(candidate)],
    }


def seal_status(path: str | Path, actor: str, reason: str) -> dict[str, Any]:
    """Add the integrity block + write-log entry to the file at ``path`` without changing content."""
    target = Path(path)
    document = load_status(target)
    return write_status(target, document, actor, reason)


def load_status(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


# --------------------------------------------------------------------------- CLI


def _print_findings(label: str, findings: list[Violation], stream=sys.stdout) -> None:
    if not findings:
        print('%s: none' % label, file=stream)
        return
    print('%s: %d' % (label, len(findings)), file=stream)
    for finding in findings:
        print('  - %s' % finding, file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('path', nargs='?', default=str(DEFAULT_STATUS),
                        help='STATUS.json path (default: %(default)s)')
    parser.add_argument('--validate', action='store_true',
                        help='validate only (exit 1 on a blocking violation); the default action')
    parser.add_argument('--check-consistency', action='store_true',
                        help='validate + advisory audit; exit 2 when only advisories remain')
    parser.add_argument('--seal', action='store_true',
                        help='add the integrity block + write-log entry to PATH (content unchanged)')
    parser.add_argument('--write', metavar='TARGET',
                        help='write the --document candidate to TARGET (requires --actor/--reason)')
    parser.add_argument('--document', help='candidate JSON document for --write')
    parser.add_argument('--actor', help='who is writing (required for --seal/--write)')
    parser.add_argument('--reason', help='why (required for --seal/--write)')
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    args = parser.parse_args(argv)

    try:
        if args.write:
            if not args.document:
                parser.error('--write requires --document')
            if not (args.actor and args.reason):
                parser.error('--write requires --actor and --reason')
            candidate = load_status(args.document)
            result = write_status(args.write, candidate, args.actor, args.reason)
            report = {'written': result, 'violations': [], 'advisories': result['advisories']}
            code = 0
        elif args.seal:
            if not (args.actor and args.reason):
                parser.error('--seal requires --actor and --reason')
            result = seal_status(args.path, args.actor, args.reason)
            report = {'sealed': result, 'violations': [], 'advisories': result['advisories']}
            code = 0
        else:
            document = load_status(args.path)
            checked = check_document(document)
            report = {'path': str(args.path),
                      'violations': [v.to_dict() for v in checked['violations']],
                      'advisories': [v.to_dict() for v in checked['advisories']]}
            if checked['violations']:
                code = 1
            elif args.check_consistency and checked['advisories']:
                code = 2
            else:
                code = 0
    except StatusWriteRefused as exc:
        report = exc.to_dict()
        code = 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {'error': '%s: %s' % (type(exc).__name__, exc)}
        code = 1

    if args.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        if 'error' in report:
            print('ERROR %s' % report['error'], file=sys.stderr)
        _print_findings('violations', [Violation(**v) for v in report.get('violations', [])],
                        sys.stderr if code == 1 else sys.stdout)
        _print_findings('advisories', [Violation(**v) for v in report.get('advisories', [])])
        written = report.get('written') or report.get('sealed')
        if written:
            print('wrote %s content_sha256=%s file_sha256=%s (previous content %s)'
                  % (written['path'], written['content_sha256'], written['file_sha256'],
                     written['previous_content_sha256']))
        print('exit=%d' % code)
    return code


if __name__ == '__main__':
    sys.exit(main())
