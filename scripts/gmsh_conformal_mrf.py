"""Build conformal rotor/stator tetra meshes in one fragmented Gmsh model.

Both MRF Regions are meshed together so the three shared interface surfaces use
one triangulation. This is a mesh candidate only; STAR import and quality
audits remain required.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import gmsh


def _tetra_data(volume_tag: int) -> tuple[int, float, int]:
    element_types, element_tags, _ = gmsh.model.mesh.getElements(3, volume_tag)
    tetra_tags = []
    for element_type, tags in zip(element_types, element_tags):
        name = gmsh.model.mesh.getElementProperties(element_type)[0]
        if name.startswith("Tetrahedron"):
            tetra_tags.extend(tags)
    if not tetra_tags:
        raise RuntimeError(f"No tetrahedra in volume {volume_tag}")
    qualities = gmsh.model.mesh.getElementQualities(tetra_tags, "minSICN")
    return len(tetra_tags), float(min(qualities)), sum(q <= 0 for q in qualities)


def build(source: Path, output: Path, radius_m: float = 0.3,
          half_length_m: float = 0.15, outer_radius_m: float = 2.05,
          inlet_z_m: float = 2.05, outlet_z_m: float = -6.15,
          farfield_size_m: float = 0.15,
          interface_size_m: float = 0.025,
          local_center_m: tuple[float, float, float] =
          (0.0510037120, 0.2955359741, -0.1075113827),
          local_radius_m: float = 0.015, local_size_m: float = 0.004):
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    gmsh.logger.start()
    try:
        # Keep imported millimetre STEP entities native during OCC operations.
        # Scaling trimmed BSpline fillet curves in OCC can fail with
        # Geom_TrimmedCurve::parameters out of range. Mesh in mm, then scale
        # mesh nodes to SI metres before writing MSH/CGNS.
        unit_scale = 1000.0
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min(0.001, local_size_m / 2) * unit_scale)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.45 * unit_scale)
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.model.add("three_blade_conformal_mrf")

        blade = gmsh.model.occ.importShapes(str(source))
        if len(blade) != 1 or blade[0][0] != 3:
            raise ValueError(f"Expected one closed STEP solid, got {blade}")
        rotor_radius = radius_m * unit_scale
        half_length = half_length_m * unit_scale
        outer_radius = outer_radius_m * unit_scale
        inlet_z = inlet_z_m * unit_scale
        outlet_z = outlet_z_m * unit_scale
        rotor_cylinder = gmsh.model.occ.addCylinder(
            0, 0, -half_length, 0, 0, 2 * half_length, rotor_radius)
        rotor, _ = gmsh.model.occ.cut([(3, rotor_cylinder)], blade,
                                      removeObject=True, removeTool=True)
        outer = gmsh.model.occ.addCylinder(
            0, 0, outlet_z, 0, 0, inlet_z - outlet_z, outer_radius)
        core = gmsh.model.occ.addCylinder(
            0, 0, -half_length, 0, 0, 2 * half_length, rotor_radius)
        stator, _ = gmsh.model.occ.cut([(3, outer)], [(3, core)],
                                       removeObject=True, removeTool=True)
        gmsh.model.occ.synchronize()
        if len(rotor) != 1 or len(stator) != 1:
            raise ValueError("Expected one rotor and one stator fluid volume")

        fragmented, _ = gmsh.model.occ.fragment(rotor, stator,
                                                removeObject=True, removeTool=True)
        gmsh.model.occ.synchronize()
        volumes = [tag for dim, tag in gmsh.model.getEntities(3)]
        if len(volumes) != 2:
            raise ValueError(f"Fragmentation produced {len(volumes)} fluid volumes")
        volume_info = [(tag, gmsh.model.occ.getMass(3, tag)) for tag in volumes]
        rotor_tag = min(volume_info, key=lambda item: abs(item[1] / unit_scale**3 - 0.0847))[0]
        stator_tag = max(volume_info, key=lambda item: item[1])[0]

        rotor_surfaces = {tag for dim, tag in gmsh.model.getBoundary(
            [(3, rotor_tag)], oriented=False) if dim == 2}
        stator_surfaces = {tag for dim, tag in gmsh.model.getBoundary(
            [(3, stator_tag)], oriented=False) if dim == 2}
        shared = rotor_surfaces & stator_surfaces
        if len(shared) != 3:
            raise ValueError(f"Expected three shared MRF interfaces, got {sorted(shared)}")

        interfaces: dict[str, int] = {}
        for tag in shared:
            box = gmsh.model.getBoundingBox(2, tag)
            if abs(box[5] - box[2]) < 1e-4 and abs(box[2] - half_length) < 1e-4:
                role = "top"
            elif abs(box[5] - box[2]) < 1e-4 and abs(box[2] + half_length) < 1e-4:
                role = "bottom"
            else:
                role = "side"
            if role in interfaces:
                raise ValueError(f"Duplicate shared interface surface role {role}")
            interfaces[role] = tag
        if set(interfaces) != {"top", "bottom", "side"}:
            raise ValueError(f"Unexpected shared interface roles: {interfaces}")

        stator_outer = stator_surfaces - shared
        inlet, outlet, farfield = [], [], []
        for tag in stator_outer:
            box = gmsh.model.getBoundingBox(2, tag)
            if abs(box[5] - box[2]) < 1e-4 and abs(box[2] - inlet_z) < 1e-4:
                inlet.append(tag)
            elif abs(box[5] - box[2]) < 1e-4 and abs(box[2] - outlet_z) < 1e-4:
                outlet.append(tag)
            else:
                farfield.append(tag)
        if not inlet or not outlet or not farfield:
            raise ValueError("Missing inlet, outlet or radial farfield surfaces")
        rotor_wall = rotor_surfaces - shared

        def physical(dim: int, entities: list[int] | set[int], name: str):
            group = gmsh.model.addPhysicalGroup(dim, sorted(entities))
            gmsh.model.setPhysicalName(dim, group, name)
            return group

        physical(3, [rotor_tag], "rotor_fluid")
        physical(3, [stator_tag], "stator_fluid")
        physical(2, rotor_wall, "propeller_wall")
        physical(2, inlet, "inlet")
        physical(2, outlet, "outlet")
        physical(2, farfield, "radial_farfield")
        for role, tag in interfaces.items():
            physical(2, [tag], f"mrf_{role}")

        field = gmsh.model.mesh.field
        blade_distance = field.add("Distance")
        field.setNumbers(blade_distance, "SurfacesList", sorted(rotor_wall))
        field.setNumber(blade_distance, "Sampling", 80)
        blade_size = field.add("Threshold")
        field.setNumber(blade_size, "InField", blade_distance)
        field.setNumber(blade_size, "SizeMin", 0.002 * unit_scale)
        field.setNumber(blade_size, "SizeMax", farfield_size_m * unit_scale)
        field.setNumber(blade_size, "DistMin", 0.005 * unit_scale)
        field.setNumber(blade_size, "DistMax", 0.05 * unit_scale)
        blade_restrict = field.add("Restrict")
        field.setNumber(blade_restrict, "InField", blade_size)
        field.setNumbers(blade_restrict, "VolumesList", [rotor_tag])
        local = field.add("Ball")
        for key, value in zip(("XCenter", "YCenter", "ZCenter"), local_center_m):
            field.setNumber(local, key, value * unit_scale)
        field.setNumber(local, "Radius", local_radius_m * unit_scale)
        field.setNumber(local, "Thickness", 0.03 * unit_scale)
        field.setNumber(local, "VIn", local_size_m * unit_scale)
        field.setNumber(local, "VOut", farfield_size_m * unit_scale)
        interface_distance = field.add("Distance")
        field.setNumbers(interface_distance, "SurfacesList", sorted(interfaces.values()))
        field.setNumber(interface_distance, "Sampling", 80)
        interface_threshold = field.add("Threshold")
        field.setNumber(interface_threshold, "InField", interface_distance)
        field.setNumber(interface_threshold, "SizeMin", interface_size_m * unit_scale)
        field.setNumber(interface_threshold, "SizeMax", farfield_size_m * unit_scale)
        field.setNumber(interface_threshold, "DistMin", 0.005 * unit_scale)
        field.setNumber(interface_threshold, "DistMax", 0.15 * unit_scale)
        rotor_zone = field.add("Ball")
        field.setNumber(rotor_zone, "XCenter", 0.0)
        field.setNumber(rotor_zone, "YCenter", 0.0)
        field.setNumber(rotor_zone, "ZCenter", 0.0)
        field.setNumber(rotor_zone, "Radius", 0.34 * unit_scale)
        field.setNumber(rotor_zone, "Thickness", 0.10 * unit_scale)
        field.setNumber(rotor_zone, "VIn", 0.025 * unit_scale)
        field.setNumber(rotor_zone, "VOut", farfield_size_m * unit_scale)
        combined = field.add("Min")
        field.setNumbers(combined, "FieldsList",
                         [blade_restrict, interface_threshold, rotor_zone, local])
        field.setAsBackgroundMesh(combined)

        print("MESHING_SHARED_SURFACES_AND_VOLUMES", flush=True)
        # Gmsh 4.15.2's Python binding uses the default dimension when called
        # without arguments; explicit positional dimension raises at runtime.
        gmsh.model.mesh.generate()
        print("MESH_GENERATION_COMPLETE", flush=True)
        rotor_cells, rotor_sicn, rotor_bad = _tetra_data(rotor_tag)
        stator_cells, stator_sicn, stator_bad = _tetra_data(stator_tag)
        interface_triangles = {}
        for role, tag in interfaces.items():
            element_types, element_tags, _ = gmsh.model.mesh.getElements(2, tag)
            count = sum(len(tags) for kind, tags in zip(element_types, element_tags)
                        if gmsh.model.mesh.getElementProperties(kind)[0].startswith("Triangle"))
            interface_triangles[role] = count
            if count == 0:
                raise ValueError(f"Shared {role} interface has no triangles")

        node_tags, _coords, _params = gmsh.model.mesh.getNodes()
        for node_tag in node_tags:
            coords, parametric, _dim, _entity = gmsh.model.mesh.getNode(node_tag)
            gmsh.model.mesh.setNode(node_tag,
                                    [value / unit_scale for value in coords],
                                    parametric)

        msh_path = output / "three_blade_conformal_mrf.msh"
        cgns_path = output / "three_blade_conformal_mrf.cgns"
        gmsh.write(str(msh_path))
        cgns_error = None
        try:
            gmsh.write(str(cgns_path))
        except Exception as exc:
            cgns_error = str(exc)
        result = {
            "source_step": str(source), "step_length_unit": "mm",
            "rotor_cells": rotor_cells, "stator_cells": stator_cells,
            "rotor_min_sicn": rotor_sicn, "stator_min_sicn": stator_sicn,
            "rotor_nonpositive_sicn": rotor_bad,
            "stator_nonpositive_sicn": stator_bad,
            "rotor_volume_m3": gmsh.model.occ.getMass(3, rotor_tag) / unit_scale**3,
            "stator_volume_m3": gmsh.model.occ.getMass(3, stator_tag) / unit_scale**3,
            "shared_interface_surface_tags": interfaces,
            "shared_interface_triangle_counts": interface_triangles,
            "shared_interface_method": "one OCC-fragmented CAD surface meshed once for both adjacent fluid volumes",
            "local_refinement_center_m": local_center_m,
            "local_refinement_radius_m": local_radius_m,
            "local_size_m": local_size_m,
            "farfield_size_m": farfield_size_m,
            "interface_size_m": interface_size_m,
            "msh": str(msh_path), "cgns": str(cgns_path) if cgns_path.exists() else None,
            "cgns_error": cgns_error,
            "warnings": [line for line in gmsh.logger.get()
                         if "Warning" in line or "Error" in line],
            "star_quality_accepted": False,
        }
        (output / "three_blade_conformal_mrf.json").write_text(
            json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except Exception as exc:
        (output / "three_blade_conformal_mrf_failure.json").write_text(
            json.dumps({"error": str(exc), "log_tail": gmsh.logger.get()[-40:]},
                       indent=2) + "\n")
        raise
    finally:
        gmsh.logger.stop()
        gmsh.finalize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--local-center-m", type=float, nargs=3,
                        default=(0.0510037120, 0.2955359741, -0.1075113827))
    parser.add_argument("--local-radius-m", type=float, default=0.015)
    parser.add_argument("--local-size-m", type=float, default=0.004)
    args = parser.parse_args()
    build(args.source, args.output, local_center_m=tuple(args.local_center_m),
          local_radius_m=args.local_radius_m, local_size_m=args.local_size_m)
