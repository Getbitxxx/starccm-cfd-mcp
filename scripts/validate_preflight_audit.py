"""Distinguish "the audit found nothing" from "the audit could not read it".

`AuditAutomationPreflight.java` downgrades `status` to "partial" and appends a warning on every
failure path of `collectGeometryParts` (manager lookup threw, manager null, getObjects() threw,
collection null). So `status == "ok"` with `warnings == []` and an empty section is a positive
statement that the section was enumerated and is empty - which is why the two Siemens `_start.sim`
sessions really do have no parts (locally confirmed: an empty CAD model string and zero
SerializableVectorCadBodyHandles, against 11517/4 for a session that does have a body).

Nothing checked that. A consumer reading only `geometryParts` would treat an unreadable section
exactly like an empty one, and the worker's `preflight_v1` postcheck accepts "ok" OR "partial". This
validator is the executable criterion: for every section it either proves the emptiness is real, or
refuses the audit as ambiguous.

Usage:
    python scripts/validate_preflight_audit.py --audit <automation_preflight.json> [--expect-parts N]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SECTIONS = ('regions', 'physicsContinua', 'geometryParts', 'derivedParts', 'interfaces', 'reports')


def load(path: Path) -> dict:
    # PowerShell writes a UTF-8 BOM with Set-Content -Encoding UTF8.
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--audit', type=Path, required=True)
    ap.add_argument('--expect-parts', type=int, default=None)
    a = ap.parse_args()

    audit = load(a.audit)
    problems: list[str] = []
    sections: dict[str, dict] = {}

    status = audit.get('status')
    if status not in {'ok', 'partial'}:
        problems.append('status is %r, not ok/partial' % status)
    warnings = audit.get('warnings')
    if not isinstance(warnings, list):
        problems.append('warnings is missing or not a list (the whole audit is untrustworthy)')
        warnings = []
    if audit.get('warningsTruncated') is True:
        problems.append('the warning list was truncated, so emptiness cannot be attributed')

    counts = audit.get('counts')
    if not isinstance(counts, dict):
        problems.append('counts is missing or not an object')
        counts = {}

    warning_areas = [str(entry.get('area', '')) for entry in warnings if isinstance(entry, dict)]

    for name in SECTIONS:
        if name not in audit:
            problems.append('%s: section is ABSENT, which is not the same as empty' % name)
            sections[name] = {'verdict': 'ABSENT'}
            continue
        value = audit[name]
        if value is None:
            # An explicit null is how the writer reports "the manager returned nothing readable".
            problems.append('%s: section is null, i.e. it could not be read' % name)
            sections[name] = {'verdict': 'UNREADABLE'}
            continue
        if not isinstance(value, list):
            problems.append('%s: section is not a list' % name)
            sections[name] = {'verdict': 'MALFORMED'}
            continue
        declared = counts.get(name)
        if declared != len(value):
            problems.append('%s: counts.%s = %r but the section holds %d entries'
                            % (name, name, declared, len(value)))
        implicated = sorted({area for area in warning_areas
                             if area == name or area.startswith(name + '[') or area.startswith(name + '.')})
        if value:
            verdict = 'PRESENT'
        elif implicated:
            # Empty AND a warning names it: the audit cannot tell "none" from "could not read".
            problems.append('%s: EMPTY but %d warning(s) name it (%s), so emptiness is ambiguous'
                            % (name, len(implicated), ', '.join(implicated[:3])))
            verdict = 'AMBIGUOUS_EMPTY'
        elif status != 'ok':
            problems.append('%s: EMPTY while status is %r, so emptiness is not attributable' % (name, status))
            verdict = 'AMBIGUOUS_EMPTY'
        else:
            verdict = 'GENUINELY_EMPTY'
        sections[name] = {'verdict': verdict, 'count': len(value), 'declared_count': declared,
                          'warnings_naming_it': implicated}

    if a.expect_parts is not None and counts.get('geometryParts') != a.expect_parts:
        problems.append('expected %d geometry parts, the audit reports %r'
                        % (a.expect_parts, counts.get('geometryParts')))

    summary = {
        'audit': str(a.audit),
        'status': status,
        'warnings': len(warnings),
        'sections': sections,
        'usable': not problems,
        'problems': problems,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
