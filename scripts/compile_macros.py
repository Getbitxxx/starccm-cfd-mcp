# -*- coding: utf-8 -*-
"""Compile macros against the installed STAR 2502 API and write a compile-evidence record.

The compile step is part of AGENTS.md section 6 ("检索依据 -> 小型宏 -> 2502 API 编译 -> 独立审查 ->
隔离副本试跑"). It used to be run ad hoc, so the record for a round could not be reproduced. This
driver makes it repeatable: it compiles exactly the macros named on the command line, records the
hash of the bytes it compiled, and refuses to write the record if any macro failed to compile.

Usage:
    python scripts/compile_macros.py --evidence test-evidence/static-external-flow/round7-macro-compile.json \
        macros/AuditExternalFlowMesh.java macros/BuildExternalFlowMesh.java ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import Backend  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('macros', nargs='+', type=Path)
    args = parser.parse_args(argv)

    backend = Backend()
    record, failed = {}, []
    for path in args.macros:
        result = backend.validate_macro(str(path))
        entry = {'compiled': result['compiled'],
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                 'api_jar_count': result.get('api_jar_count'),
                 'static_review_flags': result.get('static_review_flags')}
        if not result['compiled']:
            entry['diagnostics'] = result.get('diagnostics', '')[-4000:]
            failed.append(path.name)
        record[path.name] = entry
        print('%-38s %s' % (path.name, 'COMPILED' if result['compiled'] else 'FAILED'))
        if result.get('static_review_flags'):
            print('    static review flags:', result['static_review_flags'])
    if failed:
        print('REFUSING to write the record: compilation failed for ' + ', '.join(failed))
        return 1
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('wrote', args.evidence)
    return 0


if __name__ == '__main__':
    sys.exit(main())
