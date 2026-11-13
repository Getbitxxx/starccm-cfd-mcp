"""Inventory source tetrahedra touching a cylindrical MRF interface.

This narrows STAR post-initialization diagnostics; it does not identify a STAR
cell or reproduce STAR's Face Validity metric after interface construction.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from diagnose_gmsh_neighbor_volume import read_msh, tetra_volume


def inventory(path: Path, radius_m: float, half_length_m: float,
              tolerance_m: float = 1e-6) -> dict:
    nodes, tetra = read_msh(path)
    face_owner = {}
    for tag, corners in tetra:
        for omit in range(4):
            face = tuple(sorted(corners[j] for j in range(4) if j != omit))
            if face in face_owner:
                del face_owner[face]
            else:
                face_owner[face] = (tag, corners)
    groups = {name: [] for name in ("top", "bottom", "side")}
    for face, (tag, corners) in face_owner.items():
        points = [nodes[n] for n in face]
        if all(abs(p[2] - half_length_m) <= tolerance_m for p in points):
            role = "top"
        elif all(abs(p[2] + half_length_m) <= tolerance_m for p in points):
            role = "bottom"
        elif all(abs(math.hypot(p[0], p[1]) - radius_m) <= tolerance_m for p in points):
            role = "side"
        else:
            continue
        cell_points = [nodes[n] for n in corners]
        centroid = [sum(p[k] for p in cell_points) / 4 for k in range(3)]
        groups[role].append({"gmsh_element_tag": tag, "centroid_m": centroid,
                             "volume_m3": tetra_volume(cell_points)})
    summary = {}
    for role, cells in groups.items():
        unique = {cell["gmsh_element_tag"]: cell for cell in cells}
        selected = list(unique.values())
        summary[role] = {
            "boundary_faces": len(cells), "adjacent_source_cells": len(selected),
            "centroid_bounds_m": [[min(c["centroid_m"][k] for c in selected),
                                   max(c["centroid_m"][k] for c in selected)]
                                  for k in range(3)] if selected else None,
            "smallest_source_cells": sorted(selected, key=lambda c: c["volume_m3"])[:5],
        }
    return {"mesh": str(path), "radius_m": radius_m,
            "half_length_m": half_length_m, "tolerance_m": tolerance_m,
            "source_tetra_count": len(tetra), "unmatched_source_faces": len(face_owner),
            "interface_inventory": summary,
            "limitation": "Source interface adjacency is not a STAR post-initialization bad-cell location."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("msh", type=Path)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--half-length-m", type=float, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inventory(args.msh, args.radius_m, args.half_length_m)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
