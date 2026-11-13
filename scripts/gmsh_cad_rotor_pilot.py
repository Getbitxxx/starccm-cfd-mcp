"""Isolated Gmsh rotor-fluid mesh pilot for a validated STEP propeller solid.

This checks geometry Boolean, closed fluid volume, tetrahedral generation and
neutral-format export only. It is not a CFD mesh acceptance or a full MRF case.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import gmsh


def main(source: Path, output: Path, axis: str, radius_m: float,
         half_length_m: float, source_length_unit: str,
         blade_size_m: float = 0.003, outer_size_m: float = 0.025,
         tip_center_m: tuple[float, float, float] | None = None,
         tip_core_size_m: float | None = None,
         tip_radius_m: float = 0.003,
         tip_transition_m: float = 0.012,
         interface_center_m: tuple[float, float, float] | None = None,
         interface_size_m: float = 0.004,
         interface_radius_m: float = 0.015,
         interface_transition_m: float = 0.03):
    if not (0 < blade_size_m < outer_size_m < radius_m):
        raise ValueError("Expected 0 < blade_size_m < outer_size_m < radius_m")
    if tip_center_m is not None and not (tip_core_size_m is not None and
            0 < tip_core_size_m < blade_size_m and tip_radius_m > 0 and
            tip_transition_m > 0):
        raise ValueError("Invalid tip refinement dimensions")
    if interface_center_m is not None and not (
            interface_size_m > 0 and interface_radius_m > 0 and
            interface_transition_m > 0):
        raise ValueError("Invalid interface refinement dimensions")
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    gmsh.logger.start()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        # Keep STEP millimetres in OCC. Scaling some trimmed BSpline edges
        # created by CAD fillets raises Geom_TrimmedCurve parameter errors.
        # The volume is meshed at its native CAD scale, then mesh nodes alone
        # are converted to SI metres before MSH/CGNS export.
        mesh_coordinate_scale = 1000.0 if source_length_unit == "mm" else 1.0
        gmsh.model.add("rotor_fluid_pilot")
        solid = gmsh.model.occ.importShapes(str(source))
        if len(solid) != 1 or solid[0][0] != 3:
            raise ValueError(f"Expected one solid body, got {solid}")
        scale = {"m": 1.0, "mm": 0.001}[source_length_unit]
        if axis == "z":
            cylinder = gmsh.model.occ.addCylinder(0, 0, -half_length_m * mesh_coordinate_scale,
                                                   0, 0, 2 * half_length_m * mesh_coordinate_scale,
                                                   radius_m * mesh_coordinate_scale)
            radial_axes = (0, 1)
        elif axis == "y":
            cylinder = gmsh.model.occ.addCylinder(0, -half_length_m * mesh_coordinate_scale, 0,
                                                   0, 2 * half_length_m * mesh_coordinate_scale, 0,
                                                   radius_m * mesh_coordinate_scale)
            radial_axes = (0, 2)
        else:
            raise ValueError("axis must be y or z")
        fluid, _ = gmsh.model.occ.cut([(3, cylinder)], solid,
                                      removeObject=True, removeTool=True)
        gmsh.model.occ.synchronize()
        if len(fluid) != 1 or fluid[0][0] != 3:
            raise ValueError(f"Expected one rotor-fluid volume, got {fluid}")
        surfaces = gmsh.model.getBoundary(fluid, oriented=False)
        blade_surfaces = []
        envelope_surfaces = []
        for dim, tag in surfaces:
            box = gmsh.model.getBoundingBox(dim, tag)
            extent = max(abs(box[i]) for i in radial_axes)
            extent = max(extent, *(abs(box[i + 3]) for i in radial_axes))
            if extent < (radius_m - 0.001) * mesh_coordinate_scale:
                blade_surfaces.append(tag)
            else:
                envelope_surfaces.append(tag)
        if not blade_surfaces or len(envelope_surfaces) < 3:
            raise ValueError("Could not distinguish blade and cylinder surfaces")
        gmsh.model.addPhysicalGroup(3, [fluid[0][1]], name="rotating_fluid")
        gmsh.model.addPhysicalGroup(2, blade_surfaces, name="propeller_wall")
        gmsh.model.addPhysicalGroup(2, envelope_surfaces, name="rotor_interface_pilot")
        min_sizes = [blade_size_m / 2 * mesh_coordinate_scale]
        if tip_center_m:
            min_sizes.append(tip_core_size_m * mesh_coordinate_scale)
        if interface_center_m:
            min_sizes.append(interface_size_m / 2 * mesh_coordinate_scale)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min(min_sizes))
        gmsh.option.setNumber("Mesh.MeshSizeMax", outer_size_m * mesh_coordinate_scale)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.Algorithm3D", 10)
        field = gmsh.model.mesh.field
        distance = field.add("Distance")
        field.setNumbers(distance, "SurfacesList", blade_surfaces)
        field.setNumber(distance, "Sampling", 80)
        threshold = field.add("Threshold")
        field.setNumber(threshold, "InField", distance)
        field.setNumber(threshold, "SizeMin", blade_size_m * mesh_coordinate_scale)
        field.setNumber(threshold, "SizeMax", outer_size_m * mesh_coordinate_scale)
        field.setNumber(threshold, "DistMin", 0.005 * mesh_coordinate_scale)
        field.setNumber(threshold, "DistMax", 0.05 * mesh_coordinate_scale)
        background_fields = [threshold]
        if tip_center_m is not None:
            # Gmsh 4.15.2 reference: Ball + Thickness gives a gradual local
            # transition. The center comes from the measured bad-cell centroids.
            tip = field.add("Ball")
            for key, value in zip(("XCenter", "YCenter", "ZCenter"), tip_center_m):
                field.setNumber(tip, key, value * mesh_coordinate_scale)
            field.setNumber(tip, "Radius", tip_radius_m * mesh_coordinate_scale)
            field.setNumber(tip, "Thickness", tip_transition_m * mesh_coordinate_scale)
            field.setNumber(tip, "VIn", tip_core_size_m * mesh_coordinate_scale)
            field.setNumber(tip, "VOut", outer_size_m * mesh_coordinate_scale)
            background_fields.append(tip)
        if interface_center_m is not None:
            interface = field.add("Ball")
            for key, value in zip(("XCenter", "YCenter", "ZCenter"), interface_center_m):
                field.setNumber(interface, key, value * mesh_coordinate_scale)
            field.setNumber(interface, "Radius", interface_radius_m * mesh_coordinate_scale)
            field.setNumber(interface, "Thickness", interface_transition_m * mesh_coordinate_scale)
            field.setNumber(interface, "VIn", interface_size_m * mesh_coordinate_scale)
            field.setNumber(interface, "VOut", outer_size_m * mesh_coordinate_scale)
            background_fields.append(interface)
        if len(background_fields) == 1:
            field.setAsBackgroundMesh(background_fields[0])
        else:
            combined = field.add("Min")
            field.setNumbers(combined, "FieldsList", background_fields)
            field.setAsBackgroundMesh(combined)
        gmsh.model.mesh.generate(3)
        types, tags, _ = gmsh.model.mesh.getElements(3, fluid[0][1])
        tetra_tags = [tag for element_type, elements in zip(types, tags)
                      if gmsh.model.mesh.getElementProperties(element_type)[0].startswith("Tetrahedron")
                      for tag in elements]
        if not tetra_tags:
            raise ValueError("No tetrahedra generated")
        qualities = gmsh.model.mesh.getElementQualities(tetra_tags, "minSICN")
        if mesh_coordinate_scale != 1.0:
            node_tags, _coords, _params = gmsh.model.mesh.getNodes()
            for node_tag in node_tags:
                coords, parametric, _dim, _entity = gmsh.model.mesh.getNode(node_tag)
                gmsh.model.mesh.setNode(node_tag,
                                        [value / mesh_coordinate_scale for value in coords],
                                        parametric)
        mesh_path = output / "rotor_fluid_pilot.msh"
        gmsh.write(str(mesh_path))
        cgns_path = output / "rotor_fluid_pilot.cgns"
        cgns_error = None
        try:
            gmsh.write(str(cgns_path))
        except Exception as failure:
            cgns_error = str(failure)
        result = {
            "source": str(source), "axis": axis, "radius_m": radius_m,
            "half_length_m": half_length_m,
            "source_length_unit": source_length_unit,
            "blade_size_m": blade_size_m, "outer_size_m": outer_size_m,
            "tip_refinement": None if tip_center_m is None else {
                "center_m": tip_center_m, "core_size_m": tip_core_size_m,
                "radius_m": tip_radius_m, "transition_m": tip_transition_m},
            "interface_refinement": None if interface_center_m is None else {
                "center_m": interface_center_m, "core_size_m": interface_size_m,
                "radius_m": interface_radius_m, "transition_m": interface_transition_m},
            "fluid_volume_count": len(fluid),
            "fluid_volume_m3": gmsh.model.occ.getMass(*fluid[0]) * scale ** 3,
            "blade_surface_count": len(blade_surfaces),
            "envelope_surface_count": len(envelope_surfaces),
            "tetra_count": len(tetra_tags),
            "min_sicn": float(min(qualities)),
            "nonpositive_sicn_count": sum(q <= 0 for q in qualities),
            "msh": str(mesh_path),
            "cgns": str(cgns_path) if cgns_path.exists() else None,
            "cgns_error": cgns_error,
            "warnings": [line for line in gmsh.logger.get()
                         if "Warning" in line or "Error" in line],
        }
        (output / "rotor_fluid_pilot.json").write_text(
            json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except Exception as failure:
        record = {"source": str(source), "error": str(failure),
                  "log_tail": gmsh.logger.get()[-30:]}
        (output / "rotor_fluid_pilot_failure.json").write_text(
            json.dumps(record, indent=2) + "\n")
        raise
    finally:
        gmsh.logger.stop()
        gmsh.finalize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--axis", choices=("y", "z"), required=True)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--half-length-m", type=float, required=True)
    parser.add_argument("--source-length-unit", choices=("mm", "m"), required=True)
    parser.add_argument("--blade-size-m", type=float, default=0.003)
    parser.add_argument("--outer-size-m", type=float, default=0.025)
    parser.add_argument("--tip-center-m", type=float, nargs=3)
    parser.add_argument("--tip-core-size-m", type=float)
    parser.add_argument("--tip-radius-m", type=float, default=0.003)
    parser.add_argument("--tip-transition-m", type=float, default=0.012)
    parser.add_argument("--interface-center-m", type=float, nargs=3)
    parser.add_argument("--interface-size-m", type=float, default=0.004)
    parser.add_argument("--interface-radius-m", type=float, default=0.015)
    parser.add_argument("--interface-transition-m", type=float, default=0.03)
    args = parser.parse_args()
    main(args.source, args.output, args.axis, args.radius_m,
         args.half_length_m, args.source_length_unit,
         args.blade_size_m, args.outer_size_m,
         tuple(args.tip_center_m) if args.tip_center_m else None,
         args.tip_core_size_m, args.tip_radius_m, args.tip_transition_m,
         tuple(args.interface_center_m) if args.interface_center_m else None,
         args.interface_size_m, args.interface_radius_m,
         args.interface_transition_m)
