"""Recover the outer envelope and actual blade cavity from the failed MRF STL.

Only accepts this diagnosed three-shell geometry; refuses ambiguous topology.
The output is a surface-only diagnostic input for Gmsh, not an accepted CFD mesh.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path


def extract(source: Path, output: Path) -> dict:
    data = source.read_bytes()
    if len(data) < 84:
        raise ValueError("Binary STL is too short")
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError("Binary STL triangle count does not match file size")
    vertices, vertex_ids, faces = [], {}, []
    for i in range(count):
        triangle = []
        for j in range(3):
            xyz = struct.unpack_from("<fff", data, 84 + i * 50 + 12 + j * 12)
            if xyz not in vertex_ids:
                vertex_ids[xyz] = len(vertices)
                vertices.append(xyz)
            triangle.append(vertex_ids[xyz])
        faces.append(tuple(triangle))
    edges = defaultdict(list)
    for index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edges[tuple(sorted((a, b)))].append(index)
    if any(len(attached) != 2 for attached in edges.values()):
        raise ValueError("Source STL contains open or non-manifold edges")
    neighbors = [[] for _ in faces]
    for attached in edges.values():
        a, b = attached
        neighbors[a].append(b)
        neighbors[b].append(a)
    unseen = set(range(count))
    shells = []
    while unseen:
        frontier = [unseen.pop()]
        shell = []
        while frontier:
            index = frontier.pop()
            shell.append(index)
            for adjacent in neighbors[index]:
                if adjacent in unseen:
                    unseen.remove(adjacent)
                    frontier.append(adjacent)
        signed = 0.0
        for index in shell:
            a, b, c = (vertices[v] for v in faces[index])
            cross = (b[1]*c[2]-b[2]*c[1], b[2]*c[0]-b[0]*c[2], b[0]*c[1]-b[1]*c[0])
            signed += sum(a[k]*cross[k] for k in range(3))/6
        used = [vertices[v] for index in shell for v in faces[index]]
        bounds = [min(p[k] for p in used) for k in range(3)] + [max(p[k] for p in used) for k in range(3)]
        shells.append({"face_indices": shell, "triangles": len(shell), "signed_volume_m3": signed,
                       "bounds_m": bounds})
    if len(shells) != 3:
        raise ValueError(f"Expected three diagnosed shells; found {len(shells)}")
    ranked = sorted(shells, key=lambda item: abs(item["signed_volume_m3"]), reverse=True)
    outer, erroneous_disk, blade = ranked
    if not (outer["signed_volume_m3"] > 0 and erroneous_disk["signed_volume_m3"] < 0
            and blade["signed_volume_m3"] > 0 and outer["triangles"] < blade["triangles"]):
        raise ValueError("Shell orientation and relative size differ from the audited geometry")
    selected = [(outer, False), (blade, True)]
    output.parent.mkdir(parents=True, exist_ok=True)
    header = b"Diagnosed rotating fluid: outer cylinder minus actual rotor surface".ljust(80, b" ")
    with output.open("wb") as stream:
        stream.write(header)
        stream.write(struct.pack("<I", outer["triangles"] + blade["triangles"]))
        for shell, reverse in selected:
            for index in shell["face_indices"]:
                face = faces[index]
                if reverse:
                    face = (face[0], face[2], face[1])
                a, b, c = (vertices[v] for v in face)
                ab = tuple(b[k]-a[k] for k in range(3))
                ac = tuple(c[k]-a[k] for k in range(3))
                normal = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
                norm = math.sqrt(sum(x*x for x in normal))
                normal = tuple(x/norm for x in normal) if norm else (0.0, 0.0, 0.0)
                stream.write(struct.pack("<12fH", *(normal + a + b + c + (0,))))
    report = {"source_stl": str(source), "source_sha256": hashlib.sha256(data).hexdigest(),
              "output_stl": str(output), "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "outer": {key: value for key, value in outer.items() if key != "face_indices"},
              "removed_disk": {key: value for key, value in erroneous_disk.items() if key != "face_indices"},
              "blade": {key: value for key, value in blade.items() if key != "face_indices"},
              "output_triangles": outer["triangles"] + blade["triangles"],
              "engineering_verified": False}
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: extract_rotating_fluid_shells.py INPUT.stl OUTPUT.stl")
    print(json.dumps(extract(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
