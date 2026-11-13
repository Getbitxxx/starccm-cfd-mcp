"""Cross-check a STAR bad-cell position against source Gmsh tetrahedra and interface faces.

The closest source tetrahedron is a candidate, not a STAR cell-ID equivalence.
"""
import argparse
import json
import math
from itertools import combinations
from pathlib import Path

from diagnose_gmsh_neighbor_volume import read_msh, tetra_volume


def boundary_faces(tetra):
    owners = {}
    for tag, corners in tetra:
        for face in combinations(corners, 3):
            key = tuple(sorted(face))
            if key in owners:
                del owners[key]
            else:
                owners[key] = tag
    return owners


def face_signature(points, tolerance):
    return tuple(sorted(tuple(round(value / tolerance) for value in point)
                        for point in points))


def map_source_cell(rotor_path, stator_path, point, radius, tolerance=1e-9):
    rotor_nodes, rotor_tetra = read_msh(rotor_path)
    stator_nodes, stator_tetra = read_msh(stator_path)
    nearest = min(rotor_tetra, key=lambda item: math.dist(
        tuple(sum(rotor_nodes[node][axis] for node in item[1]) / 4 for axis in range(3)), point))
    tag, corners = nearest
    vertices = [rotor_nodes[node] for node in corners]
    centroid = tuple(sum(vertex[axis] for vertex in vertices) / 4 for axis in range(3))
    rotor_boundary = boundary_faces(rotor_tetra)
    stator_side = set()
    stator_centers = []
    for face in boundary_faces(stator_tetra):
        points = [stator_nodes[node] for node in face]
        if all(abs(math.hypot(x, y) - radius) <= tolerance for x, y, _z in points):
            stator_side.add(face_signature(points, tolerance))
            stator_centers.append(tuple(sum(p[axis] for p in points) / 3 for axis in range(3)))
    side_faces = []
    for face in combinations(corners, 3):
        key = tuple(sorted(face))
        if key not in rotor_boundary:
            continue
        points = [rotor_nodes[node] for node in face]
        if not all(abs(math.hypot(x, y) - radius) <= tolerance for x, y, _z in points):
            continue
        center = tuple(sum(p[axis] for p in points) / 3 for axis in range(3))
        side_faces.append({"node_tags": list(face), "centroid_m": center,
                           "matches_stator_triangle": face_signature(points, tolerance) in stator_side,
                           "nearest_stator_side_triangle_centroid_distance_m": min(
                               math.dist(center, q) for q in stator_centers)})
    return {"star_position_m": point, "source_tetra_candidate_tag": tag,
            "source_tetra_centroid_m": centroid,
            "centroid_distance_m": math.dist(centroid, point),
            "source_tetra_volume_m3": tetra_volume(vertices),
            "source_tetra_node_tags": list(corners),
            "source_tetra_vertices_m": vertices,
            "candidate_boundary_side_faces": side_faces,
            "limitation": "Nearest source tetra is not a verified STAR cell ID; interface initialization can split faces."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("rotor_msh", type=Path)
    parser.add_argument("stator_msh", type=Path)
    parser.add_argument("--position-m", type=float, nargs=3, required=True)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = map_source_cell(args.rotor_msh, args.stator_msh,
                             tuple(args.position_m), args.radius_m)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
