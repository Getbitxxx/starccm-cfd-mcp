"""Local, read-only probe of how much CAD a `.sim` actually carries.

A `.sim` is a record tree; the STAR CAD kernel keeps its model in a `ModelString` whose length the
`star.meshing.CadManager` record reports, and each imported body is referenced through a
`SerializableVectorCadBodyHandles` record. Both are greppable without a STAR session, which is what
makes this a useful second opinion when an audit reports zero geometry parts: an empty model string
with no body handles means the file really carries no CAD, as opposed to a reader that failed.

It never writes to the file it reads and never opens STAR.

Usage:
    python scripts/probe_sim_cad_bodies.py <file.sim> [<file.sim> ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MODEL = re.compile(rb"'ModelStringLength':\s*(\d+)")


def probe(path: Path) -> dict:
    data = path.read_bytes()
    match = MODEL.search(data)
    return {
        'file': str(path),
        'bytes': len(data),
        'cad_model_string_length': int(match.group(1)) if match else None,
        'cad_body_handle_records': data.count(b'SerializableVectorCadBodyHandles'),
        'cad_part_records': data.count(b'star.meshing.CadPart'),
        'simple_shape_part_records': data.count(b'SimpleShapePart'),
        'mesh_part_factory_records': data.count(b'star.meshing.MeshPartFactory'),
    }


if __name__ == '__main__':
    print(json.dumps([probe(Path(p)) for p in sys.argv[1:]], ensure_ascii=False, indent=2))
