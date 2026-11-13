"""Split a Gmsh multi-region MSH into two STAR-importable CGNS regions.

The MRF rotor/stator surfaces are shared in the source mesh. Each CGNS output
retains the identical interface triangles while carrying only one 3D region.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import gmsh


def split(source: Path, output: Path, physical_volume_names=("rotor_fluid", "stator_fluid")):
    output.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.open(str(source))
        physicals = {(dim, tag): gmsh.model.getPhysicalName(dim, tag)
                     for dim, tag in gmsh.model.getPhysicalGroups()}
        by_name = {name: (dim, tag) for (dim, tag), name in physicals.items()}
        surface_groups = [(name, set(map(int, gmsh.model.getEntitiesForPhysicalGroup(2, tag))))
                          for (dim, tag), name in physicals.items() if dim == 2]
        result = {}
        for volume_name in physical_volume_names:
            dim, ptag = by_name[volume_name]
            if dim != 3:
                raise ValueError(f"{volume_name} is not a volume physical group")
            keep_volumes = set(map(int, gmsh.model.getEntitiesForPhysicalGroup(3, ptag)))
            if len(keep_volumes) != 1:
                raise ValueError(f"Expected one volume in {volume_name}, got {keep_volumes}")
            volume = next(iter(keep_volumes))
            keep_surfaces = {tag for d, tag in gmsh.model.getBoundary(
                [(3, volume)], combined=False, oriented=False) if d == 2}

            # Remove cells and face elements outside this Region. Shared faces
            # are one model entity and therefore retained unchanged on both sides.
            for d, tag in gmsh.model.getEntities(3):
                if tag != volume:
                    gmsh.model.mesh.removeElements(3, tag)
            for d, tag in gmsh.model.getEntities(2):
                if tag not in keep_surfaces:
                    gmsh.model.mesh.removeElements(2, tag)

            gmsh.model.removePhysicalGroups()
            gmsh.model.addPhysicalGroup(3, [volume], name=volume_name)
            kept_names = {}
            for name, entities in surface_groups:
                selected = sorted(entities & keep_surfaces)
                if selected:
                    new_tag = gmsh.model.addPhysicalGroup(2, selected, name=name)
                    kept_names[name] = selected
            target = output / f"{volume_name}.cgns"
            gmsh.write(str(target))
            result[volume_name] = {
                "path": str(target), "volume_entity": volume,
                "retained_surface_count": len(keep_surfaces),
                "physical_surfaces": kept_names,
            }
            # Reload the full source mesh before extracting the other Region.
            if volume_name != physical_volume_names[-1]:
                gmsh.clear()
                gmsh.option.setNumber("General.Terminal", 0)
                gmsh.option.setNumber("Mesh.SaveAll", 0)
                gmsh.open(str(source))
                physicals = {(d, t): gmsh.model.getPhysicalName(d, t)
                             for d, t in gmsh.model.getPhysicalGroups()}
                by_name = {name: (d, t) for (d, t), name in physicals.items()}
                surface_groups = [(name, set(map(int, gmsh.model.getEntitiesForPhysicalGroup(2, t))))
                                  for (d, t), name in physicals.items() if d == 2]
        return result
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(split(args.source, args.output))
