"""F3 (2026-10-30 user decision): the mesh family-gate REGIME for NEW cases and solve plans.

`mesh_acceptance.py` implements the 2026-10-30 standard for a single declared family (family pass +
visible margin + localized failure + no solve before the family gate).  This module is the REGIME
around it: it decides WHEN the standard applies, and it never changes a rule or a threshold.

Two fail-closed rules:
  R1 CaseSpec regime
     A CaseSpec must DECLARE quality.mesh_acceptance
     (family / margin / localization_required_on_failure / distribution_bands) unless it is
     grandfathered: byte-identical to the split snapshot
     campaigns/uav-2026/case-regime-legacy.json (written by scripts/mesh_regime.py).
     The snapshot pins BOTH the file sha256 and the canonical payload sha256, so a changed legacy
     CaseSpec is no longer grandfathered: editing an old case forces the block (or a recorded
     exception in the snapshot).  Missing snapshot is an error (fail closed), never "all legacy".
     Enforcement: casespec.validate_case_spec(..., require_mesh_acceptance=True) appends the
     regime error to its normal error list (opt-in so the 100+ existing callers keep behaviour).

  R2 solve-plan rule
     A plan that runs a solve must carry
         "mesh_family_gate": {"record": <repo-relative path>, "sha256": <64 hex>, "family_id": ...}
     as a top-level plan field (preferred) or inside the plan's `contract` (both are read; the
     data-driven plan registry pins the contract as free-form data and refuses unknown top-level keys,
     so a NEW plan can use either form without touching the loader).
     `assert_solve_plan_family_accepted` refuses when the field is missing, the field is malformed,
     the record file is missing/unreadable, the sha256 differs, the family is unknown, the family
     is not accepted, or the accepted family does not list the exact mesh sha the plan consumes.
     A plan is a solve plan when its declared contract says solve_run is true, OR a step macro
     names a solver operation (Solve/Segment token or one of the known solver macros).  The
     explicit contract field can only ADD a solve classification, never remove one (fail closed).
     A solve plan without the field is admitted ONLY when its CaseSpec is grandfathered: legacy
     stagings keep replaying, and a NEW case (whose CaseSpec is not in the snapshot) must carry
     the gate.  The consumed mesh shas are the sha256 of every `*.sim` input the plan declares and
     that exists on disk; no such input means the plan cannot be verified -> refused.

Error contract: `MeshRegimeError` subclasses `mesh_acceptance.MeshAcceptanceError` and carries a
stable `.code` (the bracketed token that also prefixes the message) plus `.details`.  Codes used:
    LEGACY_SNAPSHOT_MISSING / LEGACY_SNAPSHOT_UNREADABLE / LEGACY_SNAPSHOT_INVALID
    CASE_SPEC_UNREADABLE
    MESH_ACCEPTANCE_QUALITY_MISSING / MESH_ACCEPTANCE_MISSING_BLOCK / LEGACY_SPEC_CHANGED
    MESH_ACCEPTANCE_INCOMPLETE / MESH_ACCEPTANCE_INVALID
    PLAN_SPEC_INVALID / PLAN_INPUT_OUTSIDE_ROOT
    FAMILY_GATE_MISSING / FAMILY_GATE_INVALID / FAMILY_GATE_RECORD_MISSING
    FAMILY_GATE_HASH_MISMATCH / FAMILY_GATE_RECORD_UNREADABLE / FAMILY_GATE_RECORD_INVALID
    FAMILY_GATE_FAMILY_UNKNOWN / FAMILY_GATE_NOT_ACCEPTED
    FAMILY_GATE_NO_MESH_INPUT / FAMILY_GATE_MESH_NOT_LISTED

This module is PURE: it reads plan specs, CaseSpecs and records; it never writes.  The only writer
of the snapshot is scripts/mesh_regime.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from mesh_acceptance import (
    REQUIRED_ACCEPTANCE_KEYS,
    MeshAcceptanceError,
    acceptance_block,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SNAPSHOT_REL = 'campaigns/uav-2026/case-regime-legacy.json'
SNAPSHOT_SCHEMA_VERSION = 1

# A plan is a solve plan if the explicit contract says so, if a step macro uses one of the two known
# solver macros, or if a step macro name contains one of these tokens.  The name token alone would
# miss RunMrfTrial, and the explicit field alone would let a mis-declared plan hide a solve, so the
# classification is the OR of all three (the explicit field only ever adds).
SOLVE_MACRO_TOKENS = ('solve', 'segment')
SOLVER_MACROS = frozenset({'RunMrfTrial.java', 'RunMrfSegment.java'})

_HEX64 = re.compile(r'^[0-9a-f]{64}$')


class MeshRegimeError(MeshAcceptanceError):
    """A regime refusal with a stable machine-readable code."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})


def repo_root(repo_root: str | Path | None = None) -> Path:
    return Path(repo_root).resolve() if repo_root is not None else ROOT


def canonical_spec_sha256(spec: dict) -> str:
    """Sha256 of the canonical JSON payload, the same encoding validate_case_spec digests with."""
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def snapshot_file(root: str | Path | None = None) -> Path:
    return repo_root(root) / LEGACY_SNAPSHOT_REL


def _resolve_under_root(value: str | Path, root: Path, code: str) -> Path:
    """Resolve a repo-relative (or absolute-under-root) path and refuse anything outside the root."""
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise MeshRegimeError(code, f"path is outside the repo root: {value}") from None
    return path


# --------------------------------------------------------------------------------------------------
# R1: the grandfathered-CaseSpec snapshot
# --------------------------------------------------------------------------------------------------
def load_legacy_snapshot(root: str | Path | None = None) -> dict:
    """Read and schema-check the snapshot.  A missing/broken snapshot is an error, never an empty set."""
    root = repo_root(root)
    path = snapshot_file(root)
    if not path.is_file():
        raise MeshRegimeError('LEGACY_SNAPSHOT_MISSING',
                              f'grandfathering snapshot missing: {LEGACY_SNAPSHOT_REL} (run '
                              f'`python3 scripts/mesh_regime.py --snapshot`)')
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeshRegimeError('LEGACY_SNAPSHOT_UNREADABLE', f'{LEGACY_SNAPSHOT_REL}: {exc}') from exc
    errors = validate_snapshot(document)
    if errors:
        raise MeshRegimeError('LEGACY_SNAPSHOT_INVALID',
                              f'{LEGACY_SNAPSHOT_REL}: ' + '; '.join(errors), {'errors': errors})
    return document


def validate_snapshot(document) -> list[str]:
    """Schema errors of a snapshot document ([] = valid).  Kept public so the writer checks the same rules."""
    if not isinstance(document, dict):
        return ['snapshot must be a JSON object']
    if document.get('schema_version') != SNAPSHOT_SCHEMA_VERSION:
        return [f'schema_version must be {SNAPSHOT_SCHEMA_VERSION}']
    entries = document.get('entries')
    if not isinstance(entries, list) or not entries:
        return ['entries must be a non-empty list']
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f'entries[{index}]'
        if not isinstance(entry, dict):
            errors.append(f'{where} must be an object')
            continue
        for key in ('case_id', 'case_spec_path', 'sha256', 'canonical_sha256'):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f'{where}.{key} must be a non-empty string')
        relative = entry.get('case_spec_path')
        if isinstance(relative, str):
            if relative.startswith('/') or '..' in Path(relative).parts:
                errors.append(f'{where}.case_spec_path must be repo-relative: {relative}')
            elif relative in seen:
                errors.append(f'{where}.case_spec_path duplicated: {relative}')
            else:
                seen.add(relative)
        for key in ('sha256', 'canonical_sha256'):
            value = entry.get(key)
            if isinstance(value, str) and value and not _HEX64.match(value):
                errors.append(f'{where}.{key} must be 64 lower-case hex characters')
    return errors


def legacy_entry(spec: dict, root: str | Path | None = None, *, case_id: str | None = None,
                 case_spec_path: str | Path | None = None) -> tuple[dict | None, str]:
    """Return (snapshot entry, status) with status in {'grandfathered','changed','absent'}.

    Lookup order: the declared case_spec_path, then the case_id, then a canonical-payload scan (so a
    parsed dict that was never tied to a path can still be recognised when it is byte-identical).
    A path/case_id that matches an entry but whose file sha or canonical payload differs is
    'changed', not 'absent': the two need different remedies (restore the bytes vs declare the block).
    """
    root = repo_root(root)
    document = load_legacy_snapshot(root)
    entries = document['entries']
    canonical = canonical_spec_sha256(spec)

    def verdict(entry: dict) -> tuple[dict | None, str]:
        return entry, ('grandfathered' if canonical == entry['canonical_sha256'] else 'changed')

    if case_spec_path is not None:
        path = Path(case_spec_path)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        relative = None
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = None  # a copy outside the repo: identify it by content, never by location
        matches = [entry for entry in entries if relative is not None and entry['case_spec_path'] == relative]
        if matches:
            entry = matches[0]
            # Require the on-disk bytes to match too; a mutated file is 'changed' even if a stale
            # handwritten payload were passed as `spec`.
            if path.is_file() and sha256_file(path) != entry['sha256']:
                return entry, 'changed'
            return verdict(entry)
        if path.is_file():
            # The same bytes copied elsewhere are still the same CaseSpec payload.
            by_content = [entry for entry in entries if entry['sha256'] == sha256_file(path)]
            if by_content:
                return by_content[0], 'grandfathered'
    if case_id is not None:
        matches = [entry for entry in entries if entry['case_id'] == case_id]
        if matches:
            return verdict(matches[0])
    for entry in entries:
        if canonical == entry['canonical_sha256']:
            return entry, 'grandfathered'
    return None, 'absent'


def is_grandfathered(spec: dict, root: str | Path | None = None, *, case_id: str | None = None,
                     case_spec_path: str | Path | None = None) -> bool:
    return legacy_entry(spec, root, case_id=case_id, case_spec_path=case_spec_path)[1] == 'grandfathered'


def require_declared_acceptance(spec: dict, case_id: str | None = None,
                                repo_root_path: str | Path | None = None, *,
                                case_spec_path: str | Path | None = None) -> dict | None:
    """R1: return the declared acceptance block, or None for a grandfathered CaseSpec.

    Raises MeshRegimeError when the block is missing on a non-grandfathered spec, incomplete, or
    malformed.  `repo_root_path` keeps the optional positional shape while letting tests point the
    rule at a temporary campaign tree.
    """
    if not isinstance(spec, dict):
        raise MeshRegimeError('MESH_ACCEPTANCE_INVALID', 'CaseSpec must be a JSON object')
    quality = spec.get('quality')
    if not isinstance(quality, dict):
        raise MeshRegimeError('MESH_ACCEPTANCE_QUALITY_MISSING',
                              'CaseSpec has no quality object; quality.mesh_acceptance is required for '
                              'a non-grandfathered CaseSpec')
    block = quality.get('mesh_acceptance')
    if block is not None:
        if not isinstance(block, dict):
            raise MeshRegimeError('MESH_ACCEPTANCE_INVALID', 'quality.mesh_acceptance must be an object')
        missing = [key for key in REQUIRED_ACCEPTANCE_KEYS if key not in block]
        if missing:
            raise MeshRegimeError('MESH_ACCEPTANCE_INCOMPLETE',
                                  'quality.mesh_acceptance is incomplete: ' + ', '.join(missing),
                                  {'missing': missing})
        try:
            return acceptance_block(spec)
        except MeshAcceptanceError as exc:
            raise MeshRegimeError('MESH_ACCEPTANCE_INVALID', str(exc)) from exc
    entry, status = legacy_entry(spec, repo_root_path, case_id=case_id, case_spec_path=case_spec_path)
    if status == 'grandfathered':
        return None
    if status == 'changed':
        raise MeshRegimeError(
            'LEGACY_SPEC_CHANGED',
            'quality.mesh_acceptance is required: this CaseSpec matches the legacy entry '
            f"{entry['case_spec_path']} but its bytes/payload changed since the "
            f"{load_legacy_snapshot(repo_root_path).get('snapshot_date')} split "
            f"(pinned sha256 {entry['sha256']}); declare the block or record the exception",
            {'legacy_sha256': entry['sha256'], 'case_spec_path': entry['case_spec_path']})
    raise MeshRegimeError(
        'MESH_ACCEPTANCE_MISSING_BLOCK',
        'quality.mesh_acceptance is required for a non-grandfathered CaseSpec'
        + (f' (case_id {case_id})' if case_id else '')
        + ': declare family/margin/localization_required_on_failure/distribution_bands',
        {'case_id': case_id})


# --------------------------------------------------------------------------------------------------
# R2: solve-plan admission
# --------------------------------------------------------------------------------------------------
def load_case_spec(path: str | Path) -> dict:
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeshRegimeError('CASE_SPEC_UNREADABLE', f'CaseSpec unreadable: {path}: {exc}') from exc


def plan_case_spec_path(plan_spec: dict, root: str | Path | None = None,
                        default_case_spec_path: str | Path | None = None) -> Path:
    """The CaseSpec a plan stages against: its declared `case_spec.json` input, else the default."""
    root = repo_root(root)
    declared = (plan_spec.get('inputs') or {}).get('case_spec.json') if isinstance(plan_spec, dict) else None
    if declared is not None:
        return _resolve_under_root(declared, root, 'PLAN_INPUT_OUTSIDE_ROOT')
    if default_case_spec_path is None:
        raise MeshRegimeError('PLAN_SPEC_INVALID', 'plan declares no case_spec.json input and no default was given')
    # The default is the CALLER's constant (not plan data), so it is trusted as given.
    return Path(default_case_spec_path).resolve()


def plan_runs_solve(plan_spec: dict) -> tuple[bool, str]:
    """Classify a plan as a solve plan.  The explicit contract field only ever ADDS the label."""
    if not isinstance(plan_spec, dict):
        raise MeshRegimeError('PLAN_SPEC_INVALID', 'plan spec must be a JSON object')
    contract = plan_spec.get('contract')
    explicit = contract.get('solve_run') if isinstance(contract, dict) else None
    if explicit is None and isinstance(plan_spec.get('solve_run'), bool):
        explicit = plan_spec['solve_run']
    if explicit is True:
        return True, 'declared contract.solve_run is true'
    steps = plan_spec.get('steps') or []
    if not isinstance(steps, list):
        raise MeshRegimeError('PLAN_SPEC_INVALID', 'plan.steps must be a list')
    for step in steps:
        if not isinstance(step, dict):
            continue
        macro = str(step.get('macro') or '')
        lowered = macro.lower()
        if macro in SOLVER_MACROS or any(token in lowered for token in SOLVE_MACRO_TOKENS):
            return True, f'step macro {macro}'
    return False, 'no solve step and contract.solve_run is not true'


def _plan_input_paths(plan_spec: dict, root: Path) -> dict[str, Path]:
    inputs = plan_spec.get('inputs')
    if not isinstance(inputs, dict):
        raise MeshRegimeError('PLAN_SPEC_INVALID', 'plan.inputs must be an object')
    resolved: dict[str, Path] = {}
    for name, source in inputs.items():
        if source is None:
            continue
        resolved[str(name)] = _resolve_under_root(source, root, 'PLAN_INPUT_OUTSIDE_ROOT')
    return resolved


def consumed_mesh_sha256(plan_spec: dict, root: str | Path | None = None) -> dict[str, str]:
    """sha256 of every declared `*.sim` plan input that exists on disk (name -> sha256)."""
    root = repo_root(root)
    consumed: dict[str, str] = {}
    for name, path in _plan_input_paths(plan_spec, root).items():
        if not name.lower().endswith('.sim') or not path.is_file():
            continue
        consumed[name] = sha256_file(path)
    return consumed


def _family_from_record(record: dict, family_id: str) -> dict:
    if not isinstance(record, dict):
        raise MeshRegimeError('FAMILY_GATE_RECORD_INVALID', 'family gate record must be a JSON object')
    kind = record.get('kind')
    if kind == 'mesh_family_gate_replay':
        families = record.get('families')
        if not isinstance(families, list) or not families:
            raise MeshRegimeError('FAMILY_GATE_RECORD_INVALID', 'replay record has no families list')
        matches = [family for family in families
                   if isinstance(family, dict) and str(family.get('family_id')) == family_id]
        if not matches:
            raise MeshRegimeError(
                'FAMILY_GATE_FAMILY_UNKNOWN',
                f'record declares no family with family_id {family_id!r} '
                f'(known: {[family.get("family_id") for family in families if isinstance(family, dict)]})')
        return matches[0]
    if kind == 'mesh_family_gate':
        if str(record.get('family_id')) != family_id:
            raise MeshRegimeError(
                'FAMILY_GATE_FAMILY_UNKNOWN',
                f'record family_id {record.get("family_id")!r} differs from declared {family_id!r}')
        return record
    raise MeshRegimeError('FAMILY_GATE_RECORD_INVALID',
                          f'unsupported family gate record kind: {kind!r}')


def _member_mesh_sha256(family: dict) -> set[str]:
    shas: set[str] = set()
    members = family.get('members')
    if not isinstance(members, list):
        return shas
    for member in members:
        if not isinstance(member, dict):
            continue
        shas.add(str(member.get('mesh_sha256')))
        declared = member.get('declared')
        if isinstance(declared, dict):
            shas.add(str(declared.get('mesh_sha256')))
    shas.discard('None')
    shas.discard('')
    return shas


def assert_solve_plan_family_accepted(plan_spec: dict, spec: dict, repo_root_path: str | Path | None = None,
                                      *, case_id: str | None = None,
                                      case_spec_path: str | Path | None = None) -> dict | None:
    """R2: refuse a solve plan that does not carry an accepted family covering the consumed mesh.

    Returns None when the plan is not a solve plan or is a grandfathered legacy plan without the
    declared field; otherwise returns the admission evidence (record path/sha, family id, matched
    input and its mesh sha256).  `spec` is the CaseSpec the plan stages against.
    """
    root = repo_root(repo_root_path)
    is_solve, reason = plan_runs_solve(plan_spec)
    if not is_solve:
        return None
    declared = plan_spec.get('mesh_family_gate')
    if declared is None:
        # The 2026-10-30 data-driven plan registry pins the per-plan contract as free-form data, so a
        # plan may also declare the gate inside `contract` (e.g. a template-inherited field); the
        # top-level field is preferred because it is what the F3 brief names.
        contract = plan_spec.get('contract')
        if isinstance(contract, dict):
            declared = contract.get('mesh_family_gate')
    if declared is None:
        entry, status = legacy_entry(spec, root, case_id=case_id, case_spec_path=case_spec_path)
        if status == 'grandfathered':
            return None
        raise MeshRegimeError(
            'FAMILY_GATE_MISSING',
            'solve refused: this plan runs a solve (' + reason + ') and its CaseSpec is not '
            'grandfathered, so it must declare mesh_family_gate {record, sha256, family_id}',
            {'reason': reason, 'legacy_status': status})
    if not isinstance(declared, dict):
        raise MeshRegimeError('FAMILY_GATE_INVALID', 'mesh_family_gate must be an object')
    record_ref = declared.get('record')
    record_sha = declared.get('sha256')
    family_id = declared.get('family_id')
    if not isinstance(record_ref, str) or not record_ref.strip():
        raise MeshRegimeError('FAMILY_GATE_INVALID', 'mesh_family_gate.record must be a repo-relative path')
    if not isinstance(record_sha, str) or not _HEX64.match(record_sha.lower()):
        raise MeshRegimeError('FAMILY_GATE_INVALID', 'mesh_family_gate.sha256 must be 64 hex characters')
    if not isinstance(family_id, str) or not family_id.strip():
        raise MeshRegimeError('FAMILY_GATE_INVALID', 'mesh_family_gate.family_id must be a non-empty string')
    record_sha = record_sha.lower()

    record_path = _resolve_under_root(record_ref, root, 'FAMILY_GATE_INVALID')
    relative = record_path.relative_to(root).as_posix()
    if not record_path.is_file():
        raise MeshRegimeError('FAMILY_GATE_RECORD_MISSING', f'family gate record missing: {relative}')
    actual = sha256_file(record_path)
    if actual != record_sha:
        raise MeshRegimeError(
            'FAMILY_GATE_HASH_MISMATCH',
            f'family gate record sha256 mismatch for {relative}: declared {record_sha}, actual {actual}',
            {'declared': record_sha, 'actual': actual})
    try:
        record = json.loads(record_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise MeshRegimeError('FAMILY_GATE_RECORD_UNREADABLE', f'{relative}: {exc}') from exc
    family = _family_from_record(record, family_id)
    if family.get('accepted') is not True:
        raise MeshRegimeError(
            'FAMILY_GATE_NOT_ACCEPTED',
            f'family {family_id!r} is not accepted (accepted={family.get("accepted")!r}, '
            f'worst={family.get("worst")!r})',
            {'family_id': family_id, 'worst': family.get('worst')})
    consumed = consumed_mesh_sha256(plan_spec, root)
    if not consumed:
        raise MeshRegimeError(
            'FAMILY_GATE_NO_MESH_INPUT',
            'solve refused: the plan declares no existing *.sim input, so the consumed mesh sha cannot be verified')
    listed = _member_mesh_sha256(family)
    matches = sorted(name for name, digest in consumed.items() if digest in listed)
    if not matches:
        raise MeshRegimeError(
            'FAMILY_GATE_MESH_NOT_LISTED',
            f"solve refused: accepted family {family_id!r} lists none of the plan's consumed mesh shas "
            f'(consumed: { {name: digest for name, digest in consumed.items()} }, listed: {sorted(listed)})',
            {'family_id': family_id, 'consumed': consumed, 'listed': sorted(listed)})
    matched = matches[0]
    return {
        'schema_version': 1,
        'kind': 'mesh_family_solve_admission',
        'plan': plan_spec.get('plan'),
        'run_id': plan_spec.get('run_id'),
        'record': relative,
        'record_sha256': actual,
        'family_id': family_id,
        'matched_input': matched,
        'mesh_sha256': consumed[matched],
        'reason': reason,
    }


def assert_plan_admissible(plan_spec: dict, root: str | Path | None = None,
                           default_case_spec_path: str | Path | None = None) -> dict | None:
    """Engine call site: load the staged CaseSpec and apply R2 BEFORE any staging write."""
    root = repo_root(root)
    case_spec_path = plan_case_spec_path(plan_spec, root, default_case_spec_path)
    spec = load_case_spec(case_spec_path)
    return assert_solve_plan_family_accepted(plan_spec, spec, root, case_spec_path=case_spec_path)


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description='Check a CaseSpec against the 2026-10-30 mesh family-gate regime (read-only)')
    parser.add_argument('case_spec', help='CaseSpec JSON to check (repo path, or a proposed spec elsewhere)')
    parser.add_argument('--root', default=None, help='repo root (default: this checkout)')
    parser.add_argument('--json', action='store_true', help='print the machine-readable verdict')
    args = parser.parse_args(argv)
    root = repo_root(args.root)
    given = Path(args.case_spec)
    path = given.resolve() if given.is_absolute() else (root / given).resolve()
    try:
        block = require_declared_acceptance(load_case_spec(path), repo_root_path=root, case_spec_path=path)
    except MeshRegimeError as exc:
        verdict = {'case_spec': _display(path, root), 'ok': False, 'code': exc.code,
                   'error': exc.message, 'details': exc.details}
        print(json.dumps(verdict, indent=1, ensure_ascii=False) if args.json else f'REFUSED {exc.code}: {exc.message}')
        return 1
    verdict = {'case_spec': _display(path, root), 'ok': True,
               'grandfathered': block is None, 'declared': block is not None}
    print(json.dumps(verdict, indent=1, ensure_ascii=False) if args.json
          else f"OK {'grandfathered' if block is None else 'declares mesh_acceptance'}")
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main(sys.argv[1:]))
