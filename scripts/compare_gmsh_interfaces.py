"""Compare source triangle geometry on two cylindrical MRF interface meshes.

Matching is geometric at a stated Euclidean coordinate tolerance, independent
of node and element IDs. Vertices are matched one-to-one by nearest coordinate;
quantized-bin equality is deliberately avoided. A mismatch is diagnostic and
does not itself fail CFD quality.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from collections import defaultdict
from collections import Counter
from itertools import product

from diagnose_gmsh_neighbor_volume import read_msh


def interface_faces(path: Path, radius_m: float, half_length_m: float,
                    tolerance_m: float) -> dict[str, list[tuple[tuple[float, float, float], ...]]]:
    nodes, tetra = read_msh(path)
    owners = {}
    for _tag, corners in tetra:
        for omit in range(4):
            face = tuple(sorted(corners[j] for j in range(4) if j != omit))
            if face in owners:
                del owners[face]
            else:
                owners[face] = True
    groups = {role: [] for role in ("top", "bottom", "side")}
    for face in owners:
        points = [nodes[n] for n in face]
        if all(abs(p[2] - half_length_m) <= tolerance_m for p in points):
            role = "top"
        elif all(abs(p[2] + half_length_m) <= tolerance_m for p in points):
            role = "bottom"
        elif all(abs(math.hypot(p[0], p[1]) - radius_m) <= tolerance_m for p in points):
            role = "side"
        else:
            continue
        groups[role].append(tuple(sorted(tuple(float(x) for x in p) for p in points)))
    return groups


def _match_faces(a: list[tuple], b: list[tuple], tolerance_m: float) -> tuple[int, int, int]:
    """Match triangles by nearby vertices, verifying true Euclidean tolerance.

    Neighboring quantization bins are searched so matches near a bin edge are
    not lost. A triangle is counted once, even if duplicate faces occur.
    """
    bins: dict[tuple[int, int, int], list[tuple[float, float, float]]] = defaultdict(list)
    b_points = {point for face in b for point in face}
    for point in b_points:
        bins[tuple(math.floor(value / tolerance_m) for value in point)].append(point)

    def map_point(point):
        key = tuple(math.floor(value / tolerance_m) for value in point)
        candidates = []
        for offset in product((-1, 0, 1), repeat=3):
            neighbor = tuple(key[k] + offset[k] for k in range(3))
            candidates.extend(bins.get(neighbor, ()))
        matches = [(math.dist(point, candidate), candidate) for candidate in candidates
                   if math.dist(point, candidate) <= tolerance_m]
        return min(matches, default=(None, None), key=lambda item: item[0])[1]

    signatures_b = Counter(tuple(sorted(face)) for face in b)
    mapped_a = []
    for face in a:
        mapped = tuple(map_point(point) for point in face)
        mapped_a.append(None if any(point is None for point in mapped) else tuple(sorted(mapped)))
    common = Counter(sig for sig in mapped_a if sig is not None) & signatures_b
    matched = sum(common.values())
    return matched, len(a) - matched, len(b) - matched


def compare(rotor: Path, stator: Path, radius_m: float, half_length_m: float,
            tolerance_m: float = 1e-9) -> dict:
    if radius_m <= 0 or half_length_m <= 0 or tolerance_m <= 0:
        raise ValueError("Interface dimensions and tolerance must be positive")
    a = interface_faces(rotor, radius_m, half_length_m, tolerance_m)
    b = interface_faces(stator, radius_m, half_length_m, tolerance_m)
    groups = {}
    for role in a:
        matched, unmatched_a, unmatched_b = _match_faces(a[role], b[role], tolerance_m)
        groups[role] = {"rotor_faces": len(a[role]), "stator_faces": len(b[role]),
                        "geometrically_matching_faces": matched,
                        "rotor_unmatched_faces": unmatched_a,
                        "stator_unmatched_faces": unmatched_b,
                        "conformal_at_tolerance": unmatched_a == 0 and unmatched_b == 0 and bool(a[role])}
    return {"rotor_msh": str(rotor), "stator_msh": str(stator),
            "radius_m": radius_m, "half_length_m": half_length_m,
            "coordinate_tolerance_m": tolerance_m, "interfaces": groups,
            "limitation": "This compares source triangle geometry, not STAR post-initialization cell IDs or Face Validity."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("rotor_msh", type=Path)
    parser.add_argument("stator_msh", type=Path)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--half-length-m", type=float, required=True)
    parser.add_argument("--tolerance-m", type=float, default=1e-9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(args.rotor_msh, args.stator_msh, args.radius_m,
                     args.half_length_m, args.tolerance_m)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
