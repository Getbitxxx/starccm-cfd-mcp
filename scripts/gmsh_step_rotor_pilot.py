"""Isolated Gmsh rotor-fluid mesh pilot for a validated STEP propeller solid.

This checks geometry Boolean, closed fluid volume, tetrahedral generation and
neutral-format export only. It is not a CFD mesh acceptance or a full MRF case.
"""

import json
import sys
from pathlib import Path

import gmsh


def main(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    gmsh.logger.start()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("two_blade_rotor_fluid_pilot")
        solid = gmsh.model.occ.importShapes(str(source))
        if len(solid) != 1 or solid[0][0] != 3:
            raise ValueError(f"Expected one solid body, got {solid}")
        gmsh.model.occ.dilate(solid, 0, 0, 0, 0.001, 0.001, 0.001)
        cylinder = gmsh.model.occ.addCylinder(0, -0.05, 0, 0, 0.10, 0, 0.22)
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
            if max(abs(box[0]), abs(box[3]), abs(box[2]), abs(box[5])) < 0.211:
                blade_surfaces.append(tag)
            else:
                envelope_surfaces.append(tag)
        if not blade_surfaces or len(envelope_surfaces) < 3:
            raise ValueError("Could not distinguish blade and cylinder surfaces")
        gmsh.model.addPhysicalGroup(3, [fluid[0][1]], name="rotating_fluid")
        gmsh.model.addPhysicalGroup(2, blade_surfaces, name="propeller_wall")
        gmsh.model.addPhysicalGroup(2, envelope_surfaces, name="rotor_interface_pilot")
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.0015)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.025)
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
        field.setNumber(threshold, "SizeMin", 0.003)
        field.setNumber(threshold, "SizeMax", 0.025)
        field.setNumber(threshold, "DistMin", 0.005)
        field.setNumber(threshold, "DistMax", 0.05)
        field.setAsBackgroundMesh(threshold)
        gmsh.model.mesh.generate(3)
        types, tags, _ = gmsh.model.mesh.getElements(3, fluid[0][1])
        tetra_tags = [tag for element_type, elements in zip(types, tags)
                      if gmsh.model.mesh.getElementProperties(element_type)[0].startswith("Tetrahedron")
                      for tag in elements]
        if not tetra_tags:
            raise ValueError("No tetrahedra generated")
        qualities = gmsh.model.mesh.getElementQualities(tetra_tags, "minSICN")
        mesh_path = output / "two_blade_rotor_fluid_pilot.msh"
        gmsh.write(str(mesh_path))
        cgns_path = output / "two_blade_rotor_fluid_pilot.cgns"
        cgns_error = None
        try:
            gmsh.write(str(cgns_path))
        except Exception as failure:
            cgns_error = str(failure)
        result = {
            "source": str(source), "fluid_volume_count": len(fluid),
            "fluid_volume_m3": gmsh.model.occ.getMass(*fluid[0]),
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
        (output / "two_blade_rotor_fluid_pilot.json").write_text(
            json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except Exception as failure:
        record = {"source": str(source), "error": str(failure),
                  "log_tail": gmsh.logger.get()[-30:]}
        (output / "two_blade_rotor_fluid_pilot_failure.json").write_text(
            json.dumps(record, indent=2) + "\n")
        raise
    finally:
        gmsh.logger.stop()
        gmsh.finalize()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
