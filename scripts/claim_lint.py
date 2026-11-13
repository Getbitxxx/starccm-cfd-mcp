# -*- coding: utf-8 -*-
"""Render report numbers/hashes FROM records, then lint the report before it is committed.

Why this module exists
----------------------
The campaign history in AGENTS.md contains ~10 recorded incidents of the same three shapes:

(a) a hand-typed SHORT hash prefix was used as if it were the hash
    (``528fadab`` ..., ``1f6c9f``, ``...d7d773``);
(b) a record belonging to a FAILED run was cited as ok / PASSED;
(c) a number in a report did not match the record field it claimed to come from.

Prose rules did not stop any of them.  This module makes the three shapes checkable, and makes the
correct path the cheap one: the author does not type numbers or hashes at all, the manifest names the
record field, and :func:`render` writes the markdown block by READING the record.  :func:`lint` then
verifies a report (or commit message) against the same manifest.

What is NOT reimplemented here: "may this record certify a success?" lives in exactly one place,
``scripts/run_record_gate.py``.  This module imports it and calls ``citation_status``; it contains no
run-state constants and no citation decision of its own.

Manifest schema (JSON, schema_version 1)::

    {
      "schema_version": 1,
      "claims": [
        {
          "id": "signed-ladder-j0647-ct",        # stable claim id (unique)
          "text": "J=0.647 CT",                   # human label rendered beside the value
          "value": 0.03568446707542939,           # the value the author claims the field holds
          "record": "campaigns/.../SIGNED-LADDER-20261030a.json",  # repo-relative record
          "field": "points.J0647.ct",             # dotted path into that record (list index = integer)
          "record_sha256": "<64 hex>",            # sha256 of the record's bytes, as pinned by the author
          "asserts_success": false                # optional; see "citation" below
        }
      ],
      "backing_records": []                       # optional repo-relative records whose 64-hex strings
                                                  # may be quoted without being claimed one-by-one
      "report": "campaigns/.../Q3-STATUS-20261030.md"  # optional: the report text these claims are
                                                  # rendered into; lint(manifest, root) without an
                                                  # explicit text lints this file (explicit text wins)
    }

The optional ``report`` key is the manifest/report PAIRING convention: a machine that discovers
manifests (e.g. the F6 KPI ledger) can lint the paired report text without guessing from filenames, and
the text-level codes (RENDER_STALE / HASH_TRUNCATED / UNBACKED_HASH) become reachable.  Without it a
manifest-only lint is a number-traceability check and those codes are out of scope by construction.

``value`` and ``record_sha256`` are what the AUTHOR asserts.  They are never trusted:
``render`` copies the value and the sha256 out of the record, and ``lint`` compares the author's
assertion with the record.  A manifest whose numbers or hashes were typed correctly renders to a block
that lint accepts; a manifest with a wrong number or a wrong/truncated hash does not.

Citation semantics (why ``asserts_success`` is explicit)
--------------------------------------------------------
A claim is a *value citation* by default: "this number is at this dotted field of this record".  Value
citations are checked for existence, hash, and value - not for the record's citability, because quoting
a threshold or a reference hash from a Phase-2 evidence file is not a claim that its run passed.
Project policy (AGENTS.md 2-O8 R3) draws exactly that line: a record that cannot certify may still be
quoted as corroboration, but never as a certificate.

A claim that DOES cite the record as evidence of success must say so: ``"asserts_success": true``.
Then ``run_record_gate.citation_status`` must return ``CITABLE``; any other verdict (including
``NOT_CITABLE_FAILED_RUN``, ``NOT_CITABLE_NO_CLAIM`` and ``UNKNOWN_RUN_STATE``) is the
``UNCITABLE_RECORD`` error.  As a safety net, a value citation of a record that itself claims
``status: ok`` but is not citable raises the same code as a WARNING, so "record says ok but its run
failed" cannot be presented as a clean citation by simply omitting the flag.

Stable codes
------------
errors (any error => CLI exit 1):
  SCHEMA_INVALID      manifest is not schema_version 1 / a claim lacks or mistypes a required key
  HASH_MISSING_RECORD record does not exist, is not a file, or ``record_sha256`` is absent/not 64 hex
  HASH_MISMATCH       sha256 of the record bytes differs from the manifest's ``record_sha256``
  RECORD_UNPARSEABLE  the record is not valid JSON (a lost record is not evidence)
  FIELD_MISSING       the dotted ``field`` path does not resolve in the record
  NUMBER_MISMATCH     manifest ``value`` is not the record's field value (exact type-aware equality)
  HASH_TRUNCATED      the text quotes a short (6..63 hex char) hash instead of the full 64-hex sha256
  UNBACKED_HASH       the text quotes a 64-hex sha256 that no cited/backing record or manifest holds
  RENDER_STALE        the rendered block in the text is missing, duplicated, or differs from
                      ``render(manifest)`` (report mode) / the commit digest is missing, mismatched,
                      or the values line is absent (commit mode)
  REPORT_UNREADABLE   the manifest declares a ``report`` path that cannot be read as text
  UNCITABLE_RECORD    a success citation was refused by ``run_record_gate.citation_status``
warnings (do not affect the exit code):
  UNCITABLE_RECORD    a value citation of an ok-claiming record that ``run_record_gate`` refuses

Commit-message mode (added 2026-10-31 after a re-check of the Round A delivery)
-------------------------------------------------------------------------------
A commit message has no markdown report block, and it legitimately quotes 40-hex GIT object ids
(sha1), which are not sha256 claims.  ``--mode commit`` therefore requires the two lines printed by
``--render-commit`` instead: a digest line ``<!-- claim-lint:commit <manifest-canonical-sha256>
claims=<n> -->`` and a ``claims: id=value; ...`` line whose values are copied from the records.  Lint
verifies the digest, the claim count, and every rendered value against its record, and then applies the
hash rules with exactly-40-hex tokens treated as git ids.  The offending Round A commit message
(``116c9475...`` / ``1fa232df...``) is kept as a real fixture in
``tests/fixtures/claim-commit-truncated-sha256-20261030.txt``.

Hash-token rules (both deliberate, both documented so they can be argued with):
  * a standalone 64-hex token is always a hash token (UNBACKED_HASH when not backed); like the short
    rule, it does not apply inside paths/identifiers.
  * short hash tokens are 7..63 hex chars, or 6 hex chars when the token contains a digit
    (the historical incidents ``528fadab``/``1f6c9f`` are 8/6 chars).  Pure-digit tokens (counts,
    years) and date-stamped ids like ``20261030a`` are identifiers, not truncated hashes, and are
    excluded; tokens embedded in paths/identifiers (``-``, ``_``, ``.``, ``/`` boundaries) too.
    Exactly-40-hex tokens are exempt ONLY in commit mode (git sha1 ids).
  * a token is only "backed" when it appears in the manifest, in a cited/backing record's bytes, or
    in a 64-hex string nested anywhere in a cited/backing record's JSON.

CLI
---
    python3 scripts/claim_lint.py --manifest claims.json [--text report.md] [--root .]
    python3 scripts/claim_lint.py --manifest claims.json --render [--text report.md]

``--render`` prints the deterministic block to stdout (it never edits the report); with ``--text`` it
also lints that report, printing the JSON report to stderr.  Plain lint prints
``{"errors": [...], "warnings": [...]}`` to stdout and exits 1 iff ``errors`` is non-empty.  When the
manifest declares ``report``, ``--text`` is optional: lint reads that file (``--text`` still wins).

Public API: ``load_manifest``, ``render``, ``lint``, ``sha256_file``.  ``render``/``lint`` take a
manifest dict or a manifest path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:                 # the CLI runs this file directly
    sys.path.insert(0, str(SCRIPTS_DIR))
import run_record_gate  # noqa: E402  (single implementation of "may this record certify success")

SCHEMA_VERSION = 1

# A full sha256, and the same token shape restricted to short lengths.
_FULL_HASH = re.compile(r'^[0-9a-fA-F]{64}$')
# Standalone hex token: not part of an identifier/path/number.  Identifier separators are in the
# look-around class on purpose - a hash quoted inside a filename or a hyphenated id is that id, not a
# hash token, and flagging ids would make the check unusable on reports that name their evidence.
_TOKEN = re.compile(r'(?<![0-9A-Za-z_.\-/])[0-9A-Fa-f]{6,64}(?![0-9A-Za-z_.\-/])')
_DATE_LIKE = re.compile(r'^(?:19|20)\d{6}[0-9a-fA-F]?$')
FULL_HASH_TOKEN = re.compile(r'(?<![0-9A-Za-z_.\-/])[0-9A-Fa-f]{64}(?![0-9A-Za-z_.\-/])')
# A shortened hash is conventionally written with an ellipsis (``528fadab…`` or ``116c9475...``).
# The token rule above excludes tokens adjacent to '.', so the ellipsis forms need their own pattern:
# without it, ASCII "..." silently evades HASH_TRUNCATED (measured on the Round-A commit message).
ELLIPSIS_HASH_TOKEN = re.compile(r'(?<![0-9A-Za-z_.\-/])([0-9A-Fa-f]{6,63})(?:\.\.\.|\u2026)')

BLOCK_BEGIN = '<!-- claim-lint:begin -->'
BLOCK_END = '<!-- claim-lint:end -->'
BLOCK_NOTE = '<!-- rendered by scripts/claim_lint.py from the records named below; do not edit by hand -->'
BLOCK_HEADER = '| claim | value | record | record_sha256 | field |'
BLOCK_SEPARATOR = '| --- | --- | --- | --- | --- |'

# Commit-message mode (2026-10-31 re-check).  A commit message has no markdown report block, and it
# legitimately quotes 40-hex GIT object ids (sha1), so the report-mode text checks cannot be applied
# as-is.  Commit mode replaces the block requirement with a one-line digest rendered from the SAME
# manifest: the digest pins the manifest bytes, and the following line carries every claim value
# copied from its record.  40-hex tokens are treated as git ids; any other short sha256-like token is
# still HASH_TRUNCATED, and 64-hex tokens must still be backed by the manifest.
COMMIT_MARKER = re.compile(r'<!-- claim-lint:commit (?P<digest>[0-9a-f]{64}) claims=(?P<count>\d+) -->')
COMMIT_VALUES_PREFIX = 'claims: '

REQUIRED_CLAIM_KEYS = ('id', 'text', 'value', 'record', 'field')
REQUIRED_CLAIM_TYPES = {'id': str, 'text': str, 'record': str, 'field': str}


class ClaimLintError(Exception):
    """Raised when a manifest cannot be rendered at all (bad schema, missing record/field)."""


# --------------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    """sha256 of the file's BYTES.  A record's hash is measured, never copied from another document."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> object:
    """Load a manifest file.  Parse failure is a ClaimLintError (the CLI turns it into a JSON report)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClaimLintError('manifest does not exist or is unreadable: %s (%s)' % (path, exc))
    try:
        return json.loads(raw.decode('utf-8-sig'))
    except ValueError as exc:
        raise ClaimLintError('manifest is not valid JSON: %s (%s)' % (path, exc))


def _read_record(path: Path) -> tuple[object, str | None]:
    """Return (payload, error).  utf-8-sig: runner state files are written with a Windows BOM."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, 'cannot read record (%s)' % exc
    try:
        return json.loads(raw.decode('utf-8-sig')), None
    except ValueError as exc:
        return None, 'record is not valid JSON: %s' % exc


def _resolve(root: Path, record: str) -> Path:
    path = Path(record)
    return path if path.is_absolute() else Path(root) / path


def _finding(code: str, message: str, claim: str | None = None, **extra: object) -> dict:
    item = {'code': code, 'message': message}
    if claim is not None:
        item['claim'] = claim
    item.update(extra)
    return item


def _claim_label(claim: dict, index: int) -> str:
    value = claim.get('id')
    return value if isinstance(value, str) and value else '#%d' % index


def field_value(payload: object, dotted: str) -> tuple[bool, object, str | None]:
    """Resolve a dotted path ('points.J0647.ct', 'families.0.family_id') in a JSON payload."""
    current = payload
    for segment in dotted.split('.'):
        if isinstance(current, dict):
            if segment not in current:
                return False, None, segment
            current = current[segment]
            continue
        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return False, None, segment
            if not 0 <= index < len(current):
                return False, None, segment
            current = current[index]
            continue
        return False, None, segment
    return True, current, None


def values_equal(claimed: object, actual: object) -> bool:
    """Exact, type-aware equality: a number must be the same number, a bool is not a number, etc.

    Deliberately exact (no tolerance): the value is supposed to be COPIED from the record by render.
    A rounded/truncated hand-typed number is the incident this check exists for, so a tolerance would
    re-open it.
    """
    if isinstance(claimed, bool) or isinstance(actual, bool):
        return isinstance(claimed, bool) and isinstance(actual, bool) and claimed == actual
    if isinstance(claimed, (int, float)) and isinstance(actual, (int, float)):
        return float(claimed) == float(actual)
    if type(claimed) is not type(actual):
        return False
    return claimed == actual


def format_value(value: object) -> str:
    """Deterministic single-line text for a JSON value (dict/list keys sorted)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _cell(text: str) -> str:
    """Escape a markdown table cell."""
    return text.replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def _normalise_block(block: str) -> str:
    block = block.replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(line.rstrip() for line in block.split('\n')).strip('\n')


def _collect_hashes(value: object, found: set[str]) -> None:
    """Collect every 64-hex JSON string nested anywhere in a payload."""
    if isinstance(value, str):
        if _FULL_HASH.match(value):
            found.add(value.lower())
    elif isinstance(value, dict):
        for item in value.values():
            _collect_hashes(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_hashes(item, found)


def _manifest_schema(manifest: object) -> tuple[list[dict], list[tuple[int, dict]]]:
    """Validate the manifest shape.  Returns (errors, [(index, claim), ...] for checkable claims)."""
    errors: list[dict] = []
    if not isinstance(manifest, dict):
        return [_finding('SCHEMA_INVALID', 'manifest must be a JSON object, got %s'
                         % type(manifest).__name__)], []
    schema = manifest.get('schema_version')
    if schema != SCHEMA_VERSION:
        errors.append(_finding('SCHEMA_INVALID', 'unsupported schema_version %r (expected %d)'
                               % (schema, SCHEMA_VERSION)))
    backed = manifest.get('backing_records')
    if backed is not None and (not isinstance(backed, list)
                               or not all(isinstance(item, str) for item in backed)):
        errors.append(_finding('SCHEMA_INVALID', 'backing_records must be a list of repo-relative paths'))
    report = manifest.get('report')
    if report is not None and (not isinstance(report, str) or not report):
        errors.append(_finding('SCHEMA_INVALID', 'report must be a non-empty repo-relative path string'))
    claims = manifest.get('claims')
    if not isinstance(claims, list):
        errors.append(_finding('SCHEMA_INVALID', 'claims must be a list'))
        return errors, []
    checkable: list[tuple[int, dict]] = []
    seen_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(_finding('SCHEMA_INVALID', 'claim #%d must be an object, got %s'
                                   % (index, type(claim).__name__)))
            continue
        label = _claim_label(claim, index)
        missing = [key for key in REQUIRED_CLAIM_KEYS if key not in claim]
        if missing:
            errors.append(_finding('SCHEMA_INVALID', 'claim %r is missing required key(s): %s'
                                   % (label, ', '.join(missing)), label))
            continue
        mistyped = ['%s (%s)' % (key, type(claim[key]).__name__)
                    for key, kind in REQUIRED_CLAIM_TYPES.items() if not isinstance(claim[key], kind)]
        if mistyped:
            errors.append(_finding('SCHEMA_INVALID', 'claim %r has mistyped key(s): %s'
                                   % (label, ', '.join(mistyped)), label))
            continue
        if not claim['id'] or not claim['record'] or not claim['field']:
            errors.append(_finding('SCHEMA_INVALID', 'claim %r has an empty id/record/field' % label,
                                   label))
            continue
        if claim['id'] in seen_ids:
            errors.append(_finding('SCHEMA_INVALID', 'claim id %r is duplicated' % claim['id'],
                                   claim['id']))
            continue
        seen_ids.add(claim['id'])
        flag = claim.get('asserts_success', False)
        if not isinstance(flag, bool):
            errors.append(_finding('SCHEMA_INVALID', 'claim %r asserts_success must be a boolean'
                                   % label, label))
            continue
        checkable.append((index, claim))
    return errors, checkable


# --------------------------------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------------------------------

def _render_row(index: int, claim: dict, root: Path) -> str:
    label = _claim_label(claim, index)
    record_path = _resolve(root, claim['record'])
    if not record_path.is_file():
        raise ClaimLintError('claim %r: record does not exist: %s' % (label, claim['record']))
    payload, error = _read_record(record_path)
    if error:
        raise ClaimLintError('claim %r: %s (%s)' % (label, error, claim['record']))
    found, value, failed_at = field_value(payload, claim['field'])
    if not found:
        raise ClaimLintError('claim %r: field %r does not resolve in %s (missing at %r)'
                             % (label, claim['field'], claim['record'], failed_at))
    descriptor = label + ' — ' + claim['text']
    if claim.get('asserts_success'):
        descriptor += ' [asserts success]'
    # The sha256 is measured from the bytes just read; the value is the record's value.  Neither is
    # taken from the manifest, which is the whole point of rendering instead of typing.
    return '| %s | %s | `%s` | `%s` | `%s` |' % (
        _cell(descriptor), _cell(format_value(value)), _cell(claim['record']),
        sha256_file(record_path), _cell(claim['field']))


def render(manifest: object, root: Path = Path('.')) -> str:
    """Deterministic markdown block: claim text + value + record + measured sha256 + field.

    Values and hashes are COPIED from the records; a manifest assertion is never rendered.  Raises
    ClaimLintError when the manifest shape is wrong or a cited record/field cannot be read, because a
    render that silently dropped a claim would let the report cite less than the manifest claims.
    """
    if isinstance(manifest, (str, Path)):
        manifest = load_manifest(Path(manifest))
    errors, claims = _manifest_schema(manifest)
    if errors:
        raise ClaimLintError('; '.join(item['message'] for item in errors))
    rows = [_render_row(index, claim, Path(root)) for index, claim in claims]
    lines = [BLOCK_BEGIN, BLOCK_NOTE, BLOCK_HEADER, BLOCK_SEPARATOR, *rows, BLOCK_END]
    return '\n'.join(lines) + '\n'


def manifest_digest(manifest: object) -> str:
    """sha256 of the manifest's canonical JSON (formatting/whitespace independent)."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False)
                          .encode('utf-8')).hexdigest()


def render_commit(manifest: object, root: Path = Path('.')) -> str:
    """Two-line commit-message block: manifest digest + every claim value READ FROM ITS RECORD."""
    if isinstance(manifest, (str, Path)):
        manifest = load_manifest(Path(manifest))
    errors, claims = _manifest_schema(manifest)
    if errors:
        raise ClaimLintError('; '.join(item['message'] for item in errors))
    values = []
    for index, claim in claims:
        label = _claim_label(claim, index)
        record_path = _resolve(Path(root), claim['record'])
        if not record_path.is_file():
            raise ClaimLintError('claim %r: record does not exist: %s' % (label, claim['record']))
        payload, error = _read_record(record_path)
        if error:
            raise ClaimLintError('claim %r: %s (%s)' % (label, error, claim['record']))
        found, value, failed_at = field_value(payload, claim['field'])
        if not found:
            raise ClaimLintError('claim %r: field %r does not resolve in %s (missing at %r)'
                                 % (label, claim['field'], claim['record'], failed_at))
        values.append('%s=%s' % (label, format_value(value)))
    marker = '<!-- claim-lint:commit %s claims=%d -->' % (manifest_digest(manifest), len(claims))
    return marker + '\n' + COMMIT_VALUES_PREFIX + '; '.join(values) + '\n'


# --------------------------------------------------------------------------------------------------
# lint
# --------------------------------------------------------------------------------------------------

def _short_hash_tokens(text: str, allow_git_ids: bool = False) -> list[str]:
    """Hash-like short tokens in the text, in text order, de-duplicated.

    A short hash token is 7..63 hex chars, or 6 hex chars containing a digit (``1f6c9f``).
    Pure-digit tokens and date-stamped ids (``20261030a``) are identifiers, not hashes.
    ``allow_git_ids`` (commit mode): exactly-40-hex tokens are git sha1 object ids, a different object
    than a sha256, and are not treated as truncated sha256 claims.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        lowered = token.lower()
        if lowered in seen:
            continue
        if _FULL_HASH.match(lowered):
            continue                                    # 64 hex chars: checked by UNBACKED_HASH
        if not any('a' <= char <= 'f' for char in lowered):
            continue                                    # counts, years: no hex letter
        if _DATE_LIKE.match(lowered):
            continue                                    # round ids like 20261030a
        if len(lowered) == 6 and not any(char.isdigit() for char in lowered):
            continue                                    # six-letter words are not hash prefixes
        if allow_git_ids and len(lowered) == 40:
            continue                                    # git sha1 object id, not a sha256 claim
        seen.add(lowered)
        tokens.append(token)
    for match in ELLIPSIS_HASH_TOKEN.finditer(text):
        token = match.group(1)
        lowered = token.lower()
        if lowered in seen or not any('a' <= char <= 'f' for char in lowered):
            continue
        if len(lowered) == 6 and not any(char.isdigit() for char in lowered):
            continue
        if allow_git_ids and len(lowered) == 40:
            continue
        seen.add(lowered)
        tokens.append(token)
    return tokens


def _citability(record_path: Path) -> dict:
    """The one place this module asks "may this record certify success?" -- the gate module's verdict."""
    return run_record_gate.citation_status(record_path)


def lint(manifest: object, root: Path = Path('.'), text: str | None = None,
         mode: str = 'report') -> dict:
    """Verify a manifest (and optionally a report text) against the records.

    ``mode='report'`` requires exactly one rendered claim block; ``mode='commit'`` requires the
    two-line ``render_commit`` digest instead (commit messages have no report block, and they quote
    git sha1 ids).  Returns ``{"errors": [...], "warnings": [...]}``; entries carry a stable ``code``
    and a human-readable ``message`` (plus ``claim``/``field``/``record``/``verdict`` where they apply).
    """
    if mode not in ('report', 'commit'):
        return {'errors': [_finding('SCHEMA_INVALID', 'unknown lint mode %r' % (mode,))],
                'warnings': []}
    if isinstance(manifest, (str, Path)):
        try:
            manifest = load_manifest(Path(manifest))
        except ClaimLintError as exc:
            return {'errors': [_finding('SCHEMA_INVALID', str(exc))], 'warnings': []}
    root = Path(root)
    errors, warnings = [], []
    schema_errors, claims = _manifest_schema(manifest)
    errors.extend(schema_errors)
    backed: set[str] = set()

    for index, claim in claims:
        label = _claim_label(claim, index)
        record_path = _resolve(root, claim['record'])
        declared = claim.get('record_sha256')
        declared_ok = isinstance(declared, str) and bool(_FULL_HASH.match(declared))
        if declared_ok:
            backed.add(declared.lower())

        record_exists = record_path.is_file()
        if not record_exists:
            errors.append(_finding('HASH_MISSING_RECORD', 'claim %r cites a record that does not exist: %s'
                                   % (label, claim['record']), label,
                                   record=claim['record'], field=claim['field']))
        elif not declared_ok:
            errors.append(_finding('HASH_MISSING_RECORD',
                                   'claim %r has no full 64-hex record_sha256 (got %r); a citation '
                                   'must pin the record bytes' % (label, declared), label,
                                   record=claim['record'], field=claim['field']))

        payload = None
        if record_exists:
            actual_sha = sha256_file(record_path)
            backed.add(actual_sha)
            if declared_ok and declared.lower() != actual_sha:
                errors.append(_finding('HASH_MISMATCH',
                                       'claim %r: record sha256 is %s, the manifest claims %s'
                                       % (label, actual_sha, declared.lower()), label,
                                       record=claim['record'], field=claim['field'],
                                       expected=declared.lower(), actual=actual_sha))
            payload, parse_error = _read_record(record_path)
            if parse_error:
                errors.append(_finding('RECORD_UNPARSEABLE', 'claim %r: %s (%s)'
                                       % (label, parse_error, claim['record']), label,
                                       record=claim['record']))
            else:
                _collect_hashes(payload, backed)
                found, value, failed_at = field_value(payload, claim['field'])
                if not found:
                    errors.append(_finding('FIELD_MISSING',
                                           'claim %r: field %r does not resolve in %s (missing at %r)'
                                           % (label, claim['field'], claim['record'], failed_at),
                                           label, record=claim['record'], field=claim['field']))
                elif not values_equal(claim['value'], value):
                    errors.append(_finding('NUMBER_MISMATCH',
                                           'claim %r: manifest value is %s, the record field %r holds %s'
                                           % (label, format_value(claim['value']), claim['field'],
                                              format_value(value)), label,
                                           record=claim['record'], field=claim['field'],
                                           expected=format_value(claim['value']),
                                           actual=format_value(value)))

        if record_exists:
            verdict = _citability(record_path)
            citable = verdict.get('state') == run_record_gate.CITABLE
            verdict_detail = {'verdict': verdict.get('state'),
                              'reason': verdict.get('reason'),
                              'decided_by': verdict.get('decided_by')}
            if claim.get('asserts_success'):
                if not citable:
                    errors.append(_finding(
                        'UNCITABLE_RECORD',
                        'claim %r cites %s as evidence of success, but run_record_gate refuses it: '
                        '%s (%s)' % (label, claim['record'], verdict.get('state'),
                                     verdict.get('reason') or 'no reason recorded'),
                        label, record=claim['record'], **verdict_detail))
            elif payload is not None and run_record_gate.certifies_ok(payload):
                # The record claims a measurement-level success; if the gate refuses to certify it,
                # quoting it without the flag is corroboration, not a certificate - say so.
                if not citable:
                    warnings.append(_finding(
                        'UNCITABLE_RECORD',
                        'claim %r quotes %s, which itself claims ok, as a value citation; '
                        'run_record_gate refuses it (%s), so it must not be presented as success'
                        % (label, claim['record'], verdict.get('state')),
                        label, record=claim['record'], **verdict_detail))

    # backing records: only their hashes are used, no per-claim value assertion
    backing = manifest.get('backing_records') if isinstance(manifest, dict) else None
    if not isinstance(backing, list):
        backing = []                                    # SCHEMA_INVALID above already said why
    for record in [item for item in backing if isinstance(item, str)]:
        record_path = _resolve(root, record)
        if not record_path.is_file():
            errors.append(_finding('HASH_MISSING_RECORD', 'backing record does not exist: %s' % record,
                                   None, record=record))
            continue
        backed.add(sha256_file(record_path))
        payload, _error = _read_record(record_path)
        if payload is not None:
            _collect_hashes(payload, backed)

    # ---- text-level checks ---------------------------------------------------------------------
    # A manifest may name its report ("report"): then lint(manifest, root) lints that file without the
    # caller having to guess the pairing.  An explicit `text` argument always wins.
    if text is None and isinstance(manifest, dict):
        declared_report = manifest.get('report')
        if isinstance(declared_report, str) and declared_report:
            report_path = _resolve(root, declared_report)
            try:
                text = report_path.read_text(encoding='utf-8-sig')
            except OSError as exc:
                errors.append(_finding('REPORT_UNREADABLE',
                                       'manifest declares report %r, which cannot be read: %s'
                                       % (declared_report, exc), None, report=declared_report))
    if text is not None:
        if not isinstance(text, str):
            errors.append(_finding('SCHEMA_INVALID', 'text must be a string, got %s'
                                   % type(text).__name__))
        elif mode == 'commit':
            # ---- commit-message mode ------------------------------------------------------------
            # The digest line's own 64-hex token is a legitimate, self-verifying hash claim.
            backed.add(manifest_digest(manifest))
            marker = COMMIT_MARKER.search(text)
            if marker is None:
                errors.append(_finding(
                    'RENDER_STALE',
                    'commit text has no rendered digest; run '
                    '"python3 scripts/claim_lint.py --manifest <manifest> --render-commit" and paste '
                    'both lines instead of typing them', None))
            else:
                expected_digest = manifest_digest(manifest)
                if marker.group('digest') != expected_digest:
                    errors.append(_finding(
                        'RENDER_STALE',
                        'commit digest %s differs from the manifest digest %s; re-render it'
                        % (marker.group('digest'), expected_digest), None,
                        expected=expected_digest, actual=marker.group('digest')))
                if int(marker.group('count')) != len(claims):
                    errors.append(_finding(
                        'RENDER_STALE',
                        'commit digest declares %s claims but the manifest has %d'
                        % (marker.group('count'), len(claims)), None))
                values_line = None
                for line in text.splitlines():
                    if line.strip().startswith(COMMIT_VALUES_PREFIX):
                        values_line = line.strip()
                        break
                if values_line is None:
                    errors.append(_finding(
                        'RENDER_STALE',
                        'commit text has the digest but no "%s" values line; the numbers must be '
                        'rendered from the records' % COMMIT_VALUES_PREFIX.strip(), None))
                else:
                    rendered = {}
                    for item in values_line[len(COMMIT_VALUES_PREFIX):].split('; '):
                        if '=' not in item:
                            continue
                        key, raw = item.split('=', 1)
                        rendered[key.strip()] = raw.strip()
                    for index, claim in claims:
                        label = _claim_label(claim, index)
                        record_path = _resolve(root, claim['record'])
                        if not record_path.is_file():
                            continue                        # claim-level errors above
                        payload, parse_error = _read_record(record_path)
                        if parse_error or payload is None:
                            continue
                        found, value, failed_at = field_value(payload, claim['field'])
                        if not found:
                            continue
                        expected = format_value(value)
                        if rendered.get(label) != expected:
                            errors.append(_finding(
                                'NUMBER_MISMATCH',
                                'commit claims %s=%r but the record says %r (field %s)'
                                % (label, rendered.get(label), expected, claim['field']), label,
                                field=claim['field'], record=claim['record'],
                                expected=expected, claimed=rendered.get(label)))
        else:
            blocks = _rendered_blocks(text)
            if len(blocks) != 1:
                errors.append(_finding('RENDER_STALE',
                                       'text must contain exactly one rendered claim block between %r '
                                       'and %r (found %d)' % (BLOCK_BEGIN, BLOCK_END, len(blocks)),
                                       None))
            else:
                try:
                    expected = render(manifest, root)
                except ClaimLintError:
                    expected = None                 # claim-level errors above already say why
                if expected is not None and _normalise_block(blocks[0]) != _normalise_block(expected):
                    errors.append(_finding('RENDER_STALE',
                                           'the rendered claim block in the text differs from '
                                           'render(manifest): re-render it instead of editing it',
                                           None))

        if isinstance(text, str):
            for token in _short_hash_tokens(text, allow_git_ids=(mode == 'commit')):
                errors.append(_finding('HASH_TRUNCATED',
                                       'text quotes %r (%d hex chars) as if it were the hash; hashes '
                                       'must be the full 64-hex sha256' % (token, len(token)),
                                       None, token=token))

            seen: set[str] = set()
            for match in FULL_HASH_TOKEN.finditer(text):
                token = match.group(0).lower()
                if token in seen:
                    continue
                seen.add(token)
                if token not in backed:
                    errors.append(_finding('UNBACKED_HASH',
                                           'text quotes sha256 %s, which appears in no cited or backing '
                                           'record' % token, None, token=token))

    return {'errors': errors, 'warnings': warnings}


def _rendered_blocks(text: str) -> list[str]:
    """Extract rendered blocks, markers included, in text order."""
    blocks: list[str] = []
    start = 0
    while True:
        begin = text.find(BLOCK_BEGIN, start)
        if begin < 0:
            return blocks
        end = text.find(BLOCK_END, begin)
        if end < 0:
            return blocks
        end += len(BLOCK_END)
        blocks.append(text[begin:end])
        start = end


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--manifest', type=Path, required=True, help='claims manifest (JSON)')
    parser.add_argument('--text', type=Path, help='report/markdown/commit text to lint against it')
    parser.add_argument('--root', type=Path, default=Path('.'), help='repo root for record paths')
    parser.add_argument('--render', action='store_true',
                        help='print the rendered claim block to stdout (never edits the report)')
    parser.add_argument('--render-commit', action='store_true',
                        help='print the two-line commit-message digest instead of the report block')
    parser.add_argument('--mode', choices=('report', 'commit'), default='report',
                        help='report mode requires the rendered block; commit mode requires and '
                             'verifies the commit digest (default: %(default)s)')
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except ClaimLintError as exc:
        report = {'errors': [_finding('SCHEMA_INVALID', str(exc))], 'warnings': []}
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    text = None
    if args.text is not None:
        try:
            text = args.text.read_text(encoding='utf-8-sig')
        except OSError as exc:
            report = {'errors': [_finding('SCHEMA_INVALID', 'text is unreadable: %s (%s)'
                                          % (args.text, exc))], 'warnings': []}
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 1

    if args.render or args.render_commit:
        try:
            block = render_commit(manifest, args.root) if args.render_commit else render(manifest, args.root)
        except ClaimLintError as exc:
            report = {'errors': [_finding('SCHEMA_INVALID', str(exc))], 'warnings': []}
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
            return 1
        print(block, end='')
        if text is None:
            return 0
        report = lint(manifest, args.root, text, mode=args.mode)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1 if report['errors'] else 0

    report = lint(manifest, args.root, text, mode=args.mode)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
