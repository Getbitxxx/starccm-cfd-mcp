"""Build a free-air stationary annulus around a rotor envelope with Gmsh.

The proportions are explicit multiples of measured propeller diameter. This
generates only a mesh pilot; the coupled MRF and solver remain separate gates.
"""

import argparse
import json
from pathlib import Path

import gmsh


def main(output: Path, diameter_m: float, core_radius_m: float,
         core_half_length_m: float,
         interface_center_m: tuple[float, float, float] | None = None,
         interface_size_m: float = 0.004,
         interface_radius_m: float = 0.015,
         interface_transition_m: float = 0.03):
    if interface_center_m is not None and not (
            interface_size_m > 0 and interface_radius_m > 0 and
            interface_transition_m > 0):
        raise ValueError("Invalid interface refinement dimensions")
    output.mkdir(parents=True, exist_ok=True)
    outer_radius = 5.0 * diameter_m
    inlet_z = 5.0 * diameter_m
    outlet_z = -15.0 * diameter_m
    gmsh.initialize()
    gmsh.logger.start()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("three_blade_stator_pilot")
        outer = gmsh.model.occ.addCylinder(0, 0, outlet_z, 0, 0,
                                          inlet_z - outlet_z, outer_radius)
        core = gmsh.model.occ.addCylinder(0, 0, -core_half_length_m, 0, 0,
                                         2 * core_half_length_m, core_radius_m)
        volumes, _ = gmsh.model.occ.cut([(3, outer)], [(3, core)],
                                        removeObject=True, removeTool=True)
        gmsh.model.occ.synchronize()
        if len(volumes) != 1:
            raise ValueError(f"Expected one stationary fluid region: {volumes}")
        roles = {name: [] for name in ("inlet", "outlet", "radial_farfield",
                                      "rotor_cyl_interface", "rotor_top_interface",
                                      "rotor_bottom_interface")}
        tagged = {}
        tolerance = 1e-5
        for dim, tag in gmsh.model.getBoundary(volumes, oriented=False):
            box = gmsh.model.getBoundingBox(dim, tag)
            radial = max(abs(box[i]) for i in (0, 1, 3, 4))
            zmid = (box[2] + box[5]) / 2
            if radial > outer_radius - tolerance:
                if abs(zmid - inlet_z) < tolerance and abs(box[5] - box[2]) < tolerance:
                    role = "inlet"
                elif abs(zmid - outlet_z) < tolerance and abs(box[5] - box[2]) < tolerance:
                    role = "outlet"
                else:
                    role = "radial_farfield"
            elif abs(zmid - core_half_length_m) < tolerance and abs(box[5] - box[2]) < tolerance:
                role = "rotor_top_interface"
            elif abs(zmid + core_half_length_m) < tolerance and abs(box[5] - box[2]) < tolerance:
                role = "rotor_bottom_interface"
            else:
                role = "rotor_cyl_interface"
            roles[role].append(tag)
            tagged[str(tag)] = {"role": role, "bounds_m": box,
                                "area_m2": gmsh.model.occ.getMass(dim, tag)}
        if any(len(tags) != 1 for tags in roles.values()):
            raise ValueError(f"Expected one face for each stator role: {roles}")
        gmsh.model.addPhysicalGroup(3, [volumes[0][1]], name="stationary_fluid")
        for name, tags in roles.items():
            gmsh.model.addPhysicalGroup(2, tags, name=name)
        mesh_size_min = min(0.02, interface_size_m / 2) if interface_center_m else 0.02
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_min)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.45)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        field = gmsh.model.mesh.field
        distance = field.add("Distance")
        field.setNumbers(distance, "SurfacesList", [tag for name, tags in roles.items()
                                                     if name.startswith("rotor_") for tag in tags])
        field.setNumber(distance, "Sampling", 80)
        threshold = field.add("Threshold")
        field.setNumber(threshold, "InField", distance)
        field.setNumber(threshold, "SizeMin", 0.02)
        field.setNumber(threshold, "SizeMax", 0.45)
        field.setNumber(threshold, "DistMin", 0.05)
        field.setNumber(threshold, "DistMax", 1.3)
        if interface_center_m is not None:
            local = field.add("Ball")
            for key, value in zip(("XCenter", "YCenter", "ZCenter"), interface_center_m):
                field.setNumber(local, key, value)
            field.setNumber(local, "Radius", interface_radius_m)
            field.setNumber(local, "Thickness", interface_transition_m)
            field.setNumber(local, "VIn", interface_size_m)
            field.setNumber(local, "VOut", 0.45)
            combined = field.add("Min")
            field.setNumbers(combined, "FieldsList", [threshold, local])
            field.setAsBackgroundMesh(combined)
        else:
            field.setAsBackgroundMesh(threshold)
        gmsh.model.mesh.generate(3)
        types, tags, _ = gmsh.model.mesh.getElements(3, volumes[0][1])
        tetra_tags = [tag for kind, elements in zip(types, tags)
                      if gmsh.model.mesh.getElementProperties(kind)[0].startswith("Tetrahedron")
                      for tag in elements]
        if not tetra_tags:
            raise ValueError("No stationary-fluid tetrahedra generated")
        qualities = gmsh.model.mesh.getElementQualities(tetra_tags, "minSICN")
        gmsh.write(str(output / "stator_fluid_pilot.msh"))
        gmsh.write(str(output / "stator_fluid_pilot.cgns"))
        record = {"diameter_m": diameter_m, "outer_radius_m": outer_radius,
                  "inlet_z_m": inlet_z, "outlet_z_m": outlet_z,
                  "rotor_radius_m": core_radius_m,
                  "rotor_half_length_m": core_half_length_m,
                  "interface_refinement": None if interface_center_m is None else {
                      "center_m": interface_center_m, "core_size_m": interface_size_m,
                      "radius_m": interface_radius_m,
                      "transition_m": interface_transition_m},
                  "fluid_volume_m3": gmsh.model.occ.getMass(*volumes[0]),
                  "surface_roles": roles, "surfaces": tagged,
                  "tetra_count": len(tetra_tags), "min_sicn": float(min(qualities)),
                  "nonpositive_sicn_count": sum(q <= 0 for q in qualities),
                  "warnings": [line for line in gmsh.logger.get()
                               if "Warning" in line or "Error" in line]}
        (output / "stator_fluid_pilot.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record, indent=2))
    except Exception as failure:
        record = {"error": str(failure), "tail": gmsh.logger.get()[-25:]}
        (output / "stator_fluid_pilot_failure.json").write_text(json.dumps(record, indent=2) + "\n")
        raise
    finally:
        gmsh.logger.stop()
        gmsh.finalize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--diameter-m", type=float, required=True)
    parser.add_argument("--core-radius-m", type=float, required=True)
    parser.add_argument("--core-half-length-m", type=float, required=True)
    parser.add_argument("--interface-center-m", type=float, nargs=3)
    parser.add_argument("--interface-size-m", type=float, default=0.004)
    parser.add_argument("--interface-radius-m", type=float, default=0.015)
    parser.add_argument("--interface-transition-m", type=float, default=0.03)
    args = parser.parse_args()
    main(args.output, args.diameter_m, args.core_radius_m, args.core_half_length_m,
         tuple(args.interface_center_m) if args.interface_center_m else None,
         args.interface_size_m, args.interface_radius_m,
         args.interface_transition_m)
