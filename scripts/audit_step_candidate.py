"""Read-only OCC inspection and orthographic previews of a STEP candidate."""

import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path

import gmsh
from PIL import Image, ImageDraw


def render_projection(nodes, triangles, axes, target):
    width, height, margin = 1000, 1000, 40
    u, v, depth = axes
    low_u = min(p[u] for p in nodes.values())
    high_u = max(p[u] for p in nodes.values())
    low_v = min(p[v] for p in nodes.values())
    high_v = max(p[v] for p in nodes.values())
    low_depth = min(p[depth] for p in nodes.values())
    high_depth = max(p[depth] for p in nodes.values())
    scale = min((width - 2 * margin) / (high_u - low_u),
                (height - 2 * margin) / (high_v - low_v))
    offset_u = (width - scale * (high_u - low_u)) / 2
    offset_v = (height - scale * (high_v - low_v)) / 2
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for tri in sorted(triangles, key=lambda t: sum(nodes[n][depth] for n in t)):
        coords = [(offset_u + scale * (nodes[n][u] - low_u),
                   height - offset_v - scale * (nodes[n][v] - low_v)) for n in tri]
        average_depth = sum(nodes[n][depth] for n in tri) / 3
        shade = int(150 + 80 * (average_depth - low_depth)
                    / max(1e-9, high_depth - low_depth))
        draw.polygon(coords, fill=(60, 110, shade))
    draw.rectangle((margin // 2, margin // 2, width - margin // 2,
                    height - margin // 2), outline=(150, 150, 150))
    image.save(target)


def main(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("candidate_propeller_step")
        volumes = gmsh.model.occ.importShapes(str(source))
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", 1.0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 5.0)
        gmsh.model.mesh.generate(2)
        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        nodes = {int(tag): tuple(node_coords[3 * i:3 * i + 3])
                 for i, tag in enumerate(node_tags)}
        triangles = []
        for surface in gmsh.model.getEntities(2):
            types, _, connectivity = gmsh.model.mesh.getElements(*surface)
            for element_type, tags in zip(types, connectivity):
                if gmsh.model.mesh.getElementProperties(element_type)[1] == 2:
                    stride = gmsh.model.mesh.getElementProperties(element_type)[3]
                    triangles.extend(tuple(int(x) for x in tags[i:i + 3])
                                     for i in range(0, len(tags), stride))
        incidence = Counter(tuple(sorted(edge)) for a, b, c in triangles
                            for edge in ((a, b), (b, c), (c, a)))
        stl = output / "candidate_propeller_m.stl"
        with stl.open("wb") as stream:
            stream.write(b"Gmsh STEP candidate, coordinates scaled from mm to m".ljust(80, b" "))
            stream.write(struct.pack("<I", len(triangles)))
            for tri in triangles:
                a, b, c = [tuple(value * 0.001 for value in nodes[tag]) for tag in tri]
                u = [b[i] - a[i] for i in range(3)]
                v = [c[i] - a[i] for i in range(3)]
                cross = (u[1] * v[2] - u[2] * v[1],
                         u[2] * v[0] - u[0] * v[2],
                         u[0] * v[1] - u[1] * v[0])
                length = math.sqrt(sum(x * x for x in cross))
                normal = tuple(x / length for x in cross) if length else (0.0, 0.0, 0.0)
                stream.write(struct.pack("<12fH", *(normal + a + b + c), 0))
        result = {
            "source": str(source), "volume_count": len(volumes),
            "volume_native_mm3": sum(gmsh.model.occ.getMass(dim, tag)
                                     for dim, tag in volumes),
            "surface_count": len(gmsh.model.getEntities(2)),
            "surface_triangle_count": len(triangles),
            "triangle_edges_with_incidence_not_two": sum(n != 2 for n in incidence.values()),
            "bounding_box_native_mm": list(gmsh.model.getBoundingBox(-1, -1)),
            "stl_m": str(stl),
            "projections": {},
        }
        for label, axes in {"XZ": (0, 2, 1), "XY": (0, 1, 2),
                            "YZ": (1, 2, 0)}.items():
            target = output / f"candidate_propeller_{label}.png"
            render_projection(nodes, triangles, axes, target)
            result["projections"][label] = str(target)
        (output / "candidate_propeller_preview.json").write_text(
            json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
