"""Locate abrupt tetrahedron volume transitions in a Gmsh 4.1 ASCII mesh.

This is a source-mesh cross-check, not STAR-CCM+ Volume Change or a repair.
"""
import argparse
import json
import math
from pathlib import Path


def tetra_volume(points):
    a, b, c, d = points
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    w = [d[i] - a[i] for i in range(3)]
    cross = (v[1] * w[2] - v[2] * w[1],
             v[2] * w[0] - v[0] * w[2],
             v[0] * w[1] - v[1] * w[0])
    return abs(sum(u[i] * cross[i] for i in range(3))) / 6.0


def read_msh(path):
    nodes = {}
    tetra = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip() == "$Nodes":
                block_count = int(next(source).split()[0])
                for _ in range(block_count):
                    dim, _entity, parametric, count = map(int, next(source).split())
                    tags = [int(next(source)) for _ in range(count)]
                    for tag in tags:
                        values = [float(x) for x in next(source).split()]
                        nodes[tag] = tuple(values[:3])
                        if parametric and len(values) < 3 + dim:
                            raise ValueError("Malformed parametric node")
            elif line.strip() == "$Elements":
                block_count = int(next(source).split()[0])
                for _ in range(block_count):
                    dim, _entity, element_type, count = map(int, next(source).split())
                    for _ in range(count):
                        values = [int(x) for x in next(source).split()]
                        if dim == 3 and element_type == 4:
                            tetra.append((values[0], tuple(values[1:5])))
                        elif dim == 3:
                            raise ValueError(f"Unsupported 3D element type {element_type}")
    if not nodes or not tetra:
        raise ValueError("No nodes or tetrahedra found")
    return nodes, tetra


def diagnose(path, cutoff):
    nodes, tetra = read_msh(path)
    volumes = []
    centroids = []
    for _tag, corners in tetra:
        points = [nodes[n] for n in corners]
        volumes.append(tetra_volume(points))
        centroids.append(tuple(sum(p[k] for p in points) / 4 for k in range(3)))
    if min(volumes) <= 0:
        raise ValueError("Nonpositive tetrahedron volume")
    largest_neighbor = [0.0] * len(tetra)
    face_owner = {}
    unmatched = 0
    for index, (_tag, corners) in enumerate(tetra):
        for omit in range(4):
            face = tuple(sorted(corners[j] for j in range(4) if j != omit))
            prior = face_owner.pop(face, None)
            if prior is None:
                face_owner[face] = index
            else:
                largest_neighbor[index] = max(largest_neighbor[index], volumes[prior])
                largest_neighbor[prior] = max(largest_neighbor[prior], volumes[index])
    unmatched = len(face_owner)
    low = []
    minimum = math.inf
    for i, (tag, _corners) in enumerate(tetra):
        if largest_neighbor[i] == 0:
            continue
        ratio = volumes[i] / largest_neighbor[i]
        minimum = min(minimum, ratio)
        if ratio <= cutoff:
            x, y, z = centroids[i]
            low.append({"gmsh_element_tag": tag, "centroid_m": [x, y, z],
                        "radius_m": math.hypot(x, y), "volume_m3": volumes[i],
                        "largest_face_neighbor_volume_m3": largest_neighbor[i],
                        "face_neighbor_volume_ratio": ratio})
    return {"mesh": str(path), "tetra_count": len(tetra), "node_count": len(nodes),
            "boundary_face_count": unmatched, "method": "tetra volume / largest face-neighbor tetra volume",
            "star_metric_equivalence": "not established", "cutoff": cutoff,
            "minimum_face_neighbor_volume_ratio": minimum,
            "count_at_or_below_cutoff": len(low), "lowest": sorted(low, key=lambda x: x["face_neighbor_volume_ratio"])[:20]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("msh", type=Path)
    parser.add_argument("--cutoff", type=float, default=0.01)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = diagnose(args.msh, args.cutoff)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
