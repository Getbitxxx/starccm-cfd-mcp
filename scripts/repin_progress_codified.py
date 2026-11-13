# -*- coding: utf-8 -*-
"""Re-pin every `codified_in` hash in progress.json from the files on disk.

Why this exists (审查 R2-1, 实测缺陷): a round changed `scripts/hypermesh_quality_gate.py`
(after the independent review found D-1/C-2/C-6) and re-pinned six of the seven files in
`generality.mesh_tooling_policy_20261014.codified_in` — but not the judge itself. The
documented hash therefore described the PRE-FIX bytes, which is exactly the shape AGENTS.md
§6 names ("记下了但从不比对"): a pin that no code compares is not a check. The pin is only
worth having if something refuses a mismatch, so:

* this script re-pins every entry of every `codified_in` block it finds (path relative to the
  repository root), reporting each change;
* `tests/test_hypermesh_quality_gate.py::GeneralityAndSingleJudge::test_documented_pins_match_disk`
  fails whenever a pinned file no longer matches, so a stale pin cannot ship silently.

Usage:  python3 scripts/repin_progress_codified.py [--check]
        --check exits 1 and prints the mismatches without writing (what the test does).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = ROOT / 'progress.json'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blocks(node, trail=''):
    """Yield (trail, dict) for every `codified_in` mapping, wherever it sits."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'codified_in' and isinstance(value, dict):
                yield trail or '<root>', value
            else:
                yield from blocks(value, '%s/%s' % (trail, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from blocks(value, '%s[%d]' % (trail, index))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='report mismatches without writing')
    args = parser.parse_args(argv[1:])
    doc = json.loads(PROGRESS.read_text(encoding='utf-8'))
    changes, missing, stale = 0, [], []
    for trail, mapping in blocks(doc):
        for rel, pinned in list(mapping.items()):
            path = ROOT / rel
            if not path.is_file():
                missing.append('%s: %s' % (trail, rel))
                continue
            actual = sha256(path)
            if actual != pinned:
                stale.append('%s: %s\n    pinned %s\n    actual %s' % (trail, rel, pinned, actual))
                if not args.check:
                    mapping[rel] = actual
                    changes += 1
    if missing:
        print('pinned files that do not exist on disk:', file=sys.stderr)
        for item in missing:
            print('  ' + item, file=sys.stderr)
    if args.check:
        for item in stale:
            print('STALE PIN ' + item, file=sys.stderr)
        print('%d stale pin(s), %d missing file(s)' % (len(stale), len(missing)))
        return 1 if (stale or missing) else 0
    if stale:
        PROGRESS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for item in stale:
            print('re-pinned ' + item)
    print('re-pinned %d hash(es); %d missing file(s)' % (changes, len(missing)))
    return 1 if missing else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
