"""Isolated Gmsh volume-mesh trial from an audited closed rotating-fluid STL.

This is a diagnostic artifact. Import into STAR-CCM+ and remap all boundary
roles before treating it as a CFD mesh.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import gmsh


def run(stl: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.logger.start()
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.002)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 0.03)
    gmsh.option.setNumber("Mesh.MeshOnlyEmpty", 1)
    gmsh.option.setNumber("Mesh.Optimize", 1)
    gmsh.model.add("mrf_rotating_fluid_trial")
    try:
        gmsh.merge(str(stl))
        gmsh.model.mesh.classifySurfaces(math.radians(40), True, True)
        gmsh.model.mesh.createGeometry()
        surfaces = [tag for dim, tag in gmsh.model.getEntities(2)]
        if not surfaces:
            raise RuntimeError("Gmsh did not recover any surface entities")
        bounds = {tag: gmsh.model.getBoundingBox(2, tag) for tag in surfaces}
        groups = {"upstream_cap": [], "downstream_cap": [], "rotor_interface_side": [], "blade_walls": []}
        for tag, box in bounds.items():
            xmin, ymin, zmin, xmax, ymax, zmax = box
            radial_max = max(math.hypot(x, y) for x in (xmin, xmax) for y in (ymin, ymax))
            if abs(zmin - 0.1) < 1e-4 and abs(zmax - 0.1) < 1e-4:
                groups["upstream_cap"].append(tag)
            elif abs(zmin + 0.2) < 1e-4 and abs(zmax + 0.2) < 1e-4:
                groups["downstream_cap"].append(tag)
            elif radial_max > 0.29:
                groups["rotor_interface_side"].append(tag)
            else:
                groups["blade_walls"].append(tag)
        if any(not values for values in groups.values()):
            raise RuntimeError(f"Cannot map all rotating fluid boundary roles: {groups}")
        # The STL contains an outer envelope and three separate blade void shells.
        # A Gmsh volume needs the outer shell and every hole as separate loops.
        curves_to_surfaces = {}
        for tag in surfaces:
            for _, curve in gmsh.model.getBoundary([(2, tag)], oriented=False):
                curves_to_surfaces.setdefault(curve, set()).add(tag)
        neighbors = {tag: set() for tag in surfaces}
        for linked in curves_to_surfaces.values():
            for tag in linked:
                neighbors[tag].update(linked - {tag})
        remaining = set(surfaces)
        shells = []
        while remaining:
            shell = set()
            frontier = [remaining.pop()]
            while frontier:
                current = frontier.pop()
                if current in shell:
                    continue
                shell.add(current)
                next_tags = neighbors[current] & remaining
                frontier.extend(next_tags)
                remaining -= next_tags
            shells.append(sorted(shell))
        if len(shells) < 2:
            raise RuntimeError(f"Expected an outer envelope and blade void shells, found {len(shells)}")
        outer = max(shells, key=lambda shell: max(bounds[tag][5] for tag in shell) - min(bounds[tag][2] for tag in shell))
        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        node_xyz = {int(tag): tuple(node_coords[3*i:3*i+3]) for i, tag in enumerate(node_tags)}
        def signed_shell_volume(shell):
            total = 0.0
            for tag in shell:
                types, _, connectivity = gmsh.model.mesh.getElements(2, tag)
                for kind, nodes in zip(types, connectivity):
                    if kind != 2:
                        raise RuntimeError(f"Unexpected surface element type {kind}")
                    for i in range(0, len(nodes), 3):
                        a, b, c = (node_xyz[int(nodes[i+j])] for j in range(3))
                        cross = (b[1]*c[2]-b[2]*c[1], b[2]*c[0]-b[0]*c[2], b[0]*c[1]-b[1]*c[0])
                        total += sum(a[k]*cross[k] for k in range(3))/6
            return total
        shell_volumes = [signed_shell_volume(shell) for shell in shells]
        loops = []
        for shell, signed_volume in [(outer, signed_shell_volume(outer))] + [(s, signed_shell_volume(s)) for s in shells if s is not outer]:
            expected_sign = 1 if shell is outer else -1
            oriented_tags = shell if signed_volume * expected_sign > 0 else [-tag for tag in shell]
            loops.append(gmsh.model.geo.addSurfaceLoop(oriented_tags))
        volume = gmsh.model.geo.addVolume(loops)
        gmsh.model.geo.synchronize()
        for name, tags in groups.items():
            physical = gmsh.model.addPhysicalGroup(2, tags)
            gmsh.model.setPhysicalName(2, physical, name)
        physical = gmsh.model.addPhysicalGroup(3, [volume])
        gmsh.model.setPhysicalName(3, physical, "rotating_fluid")
        gmsh.model.mesh.generate(3)
        element_types, element_tags, _ = gmsh.model.mesh.getElements(3)
        counts = {str(kind): len(tags) for kind, tags in zip(element_types, element_tags)}
        all_tags = [int(tag) for tags in element_tags for tag in tags]
        if not all_tags:
            details = [line for line in gmsh.logger.get() if any(s in line for s in ("Warning", "Error", "void region", "3D refinement"))]
            raise RuntimeError(f"Gmsh produced no volume elements; shells={[len(s) for s in shells]}; shell_signed_volumes={shell_volumes}; details={details[-15:]}")
        quality = gmsh.model.mesh.getElementQualities(all_tags, "minSICN")
        gmsh.write(str(output / "mrf_rotating_gmsh_trial.msh"))
        cgns_error = None
        try:
            gmsh.write(str(output / "mrf_rotating_gmsh_trial.cgns"))
        except Exception as exc:
            cgns_error = str(exc)
        result = {
            "source_stl": str(stl), "source_sha256": hashlib.sha256(stl.read_bytes()).hexdigest(),
            "gmsh_version": gmsh.option.getString("General.Version"),
            "surface_entities": len(surfaces), "boundary_groups": groups,
            "shell_surface_counts": [len(shell) for shell in shells],
            "shell_signed_volumes": shell_volumes,
            "volume_element_counts_by_type": counts, "volume_elements": len(all_tags),
            "min_sicn": float(min(quality)), "nonpositive_sicn": int(sum(q <= 0 for q in quality)),
            "cgns_export_error": cgns_error,
            "gmsh_warnings": [line for line in gmsh.logger.get() if "Warning" in line or "Error" in line],
            "star_import_verified": False,
        }
        (output / "gmsh_trial.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    except Exception as failure:
        record = {"source_stl": str(stl), "source_sha256": hashlib.sha256(stl.read_bytes()).hexdigest(),
                  "gmsh_version": gmsh.option.getString("General.Version"),
                  "error": str(failure), "log_tail": gmsh.logger.get()[-30:],
                  "volume_mesh_generated": False, "star_import_verified": False}
        (output / "gmsh_trial_failure.json").write_text(json.dumps(record, indent=2) + "\n")
        raise
    finally:
        gmsh.logger.stop()
        gmsh.finalize()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: gmsh_rotating_trial.py ROTATING_FLUID.stl OUTPUT_DIR")
    print(json.dumps(run(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
