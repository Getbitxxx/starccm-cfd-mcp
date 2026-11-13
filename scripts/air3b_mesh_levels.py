"""Derive an AIR-3B mesh-refinement level from the reviewed build pipeline.

A4 needs coarse/medium/fine MRF meshes for a discretisation study. The reviewed build
pipeline is already fully parameterised by environment variables, so a level is a pure env
transformation of that template and no reviewed macro changes. This tool writes the derived
pipeline and reports exactly which env values moved, refusing to proceed if anything outside
the declared size set differs.

Refinement convention (documented because it is a choice, not a fact): the finest level is the
mesh that already passed the freeze, quality, prism and y+ gates, and the coarser levels are
obtained by multiplying the linear mesh sizes by r. Cell count then falls by roughly r^3, so
r = 1.5 gives about 3.4x fewer cells and r = 2.25 about 11x fewer. Making the validated mesh
the FINEST level rather than the coarsest is what keeps the study affordable: the two new
levels are the cheap ones.

`--prism-mode systematic` (default) also scales the prism stack thickness with r, which is a
fully systematic refinement but coarsens the near-wall resolution and can push a coarse level
out of the wall-function range; `--prism-mode fixed` keeps the validated near-wall stack and
refines only the surface and volume mesh, which isolates the outer discretisation but is not a
systematic refinement. y+ must be reported for every level either way, and the GCI is only
meaningful if every level stays inside the wall-treatment range.

Usage:
    python scripts/air3b_mesh_levels.py --level medium --ratio 1.5 [--prism-mode systematic]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "cases/air-three-blade/pipeline-mrf-build.json"
# Review round A4-1: the tool previously wrote to a path built from free-text --level with no
# existence guard, so a re-run could silently destroy a derived pipeline whose hash was already
# pinned in an approved review record, and "--level ../x" could escape the case directory. The
# template was also re-read live with nothing tying it to the artefact under review.
TEMPLATE_SHA256 = "c3361557b67bae1f2cb8a361638685adf63cae05abadebcb618c94195e16f0e1"
LEVEL_WHITELIST = ("coarse", "medium", "fine", "nearwall-thin", "nearwall-base", "nearwall-thick")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

# Only linear mesh sizes scale. Domain and refinement-zone dimensions, layer count, stretch
# ratio, curvature points and the prism-transition ratio are deliberately held fixed: the
# first three define the geometry and the domain, and the rest are quality controls.
SCALED_SIZES = ("AIR3B_BASE_SIZE_M", "AIR3B_BLADE_SIZE_M", "AIR3B_BLADE_MIN_M",
                "AIR3B_EDGE_SIZE_M", "AIR3B_EDGE_MIN_M", "AIR3B_NEAR_SIZE_M",
                "AIR3B_MID_SIZE_M", "AIR3B_FAR_SIZE_M", "AIR3B_INTERFACE_SIZE_M")
PRISM_SIZE = "AIR3B_PRISM_TOTAL_M"
REFERENCE_CELLS = 2053234  # the validated fine mesh, measured


def build(level: str, ratio: float, prism_mode: str, force: bool = False) -> dict:
    if level not in LEVEL_WHITELIST:
        raise SystemExit("Level must be one of %s, not %r" % (list(LEVEL_WHITELIST), level))
    actual_template_sha = sha256(TEMPLATE)
    if TEMPLATE_SHA256 and actual_template_sha != TEMPLATE_SHA256:
        raise SystemExit("Template sha256 is %s but this tool was reviewed against %s; review the "
                         "template change before deriving from it"
                         % (actual_template_sha, TEMPLATE_SHA256))
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if ratio <= 1.0:
        raise SystemExit("Ratio must be greater than one; the template is the finest level")
    derived = json.loads(json.dumps(template))
    scaled = list(SCALED_SIZES)
    if prism_mode == "systematic":
        scaled = [*SCALED_SIZES, PRISM_SIZE]
    moved = {}
    for name in scaled:
        if name not in template["env"]:
            raise SystemExit("Template env is missing %s; refusing to derive" % name)
        original = float(template["env"][name])
        new = original * ratio
        derived["env"][name] = repr(round(new, 12))
        moved[name] = {"from": original, "to": derived["env"][name]}
    expected_cells = int(round(REFERENCE_CELLS / ratio ** 3))
    derived["pipeline_id"] = "air3b-mrf-build-%s" % level
    derived["note"] = (
        "%s Mesh level %r derived from the reviewed build pipeline by scaling the linear mesh "
        "sizes by %g with prism mode %r. Expected cell count is roughly %d against the validated "
        "fine mesh's %d. The finest level is the validated mesh, so the new levels are the cheap "
        "ones. y+ must be re-measured on every level: the wall-treatment range, not only the "
        "CaseSpec coverage fraction, decides whether a level may enter the GCI."
        % (template["note"], level, ratio, prism_mode, expected_cells, REFERENCE_CELLS))
    derived["expected_cells"] = expected_cells
    derived["expected_cells_basis"] = ("cubic extrapolation from the validated fine mesh's %d cells at ratio %g. This is "
        "a HEURISTIC, not a bound: the domain and refinement-zone dimensions, the layer count, the stretch and the "
        "curvature-point control are held fixed while the model geometry is not scaled, so where the curvature rule "
        "binds, the surface size does not scale with r and the realised count is expected to run above this estimate."
        % (REFERENCE_CELLS, ratio))
    out = REPO / f"cases/air-three-blade/pipeline-mrf-build-{level}.json"
    if out.exists() and not force:
        raise SystemExit("Refusing to overwrite %s (already exists). Its hash may be pinned in an approved review "
                         "record; pass --force after reviewing that consequence." % out)
    if out.exists():
        backup = out.with_suffix(".json.bak")
        backup.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print("backed up the previous %s to %s" % (out.name, backup.name))
    out.write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-load and re-diff what was actually written, so a serialisation surprise cannot go unnoticed.
    written = json.loads(out.read_text(encoding="utf-8"))
    if diff_pipelines(template, {k: v for k, v in written.items()
                                 if k not in ("expected_cells", "expected_cells_basis")}) != changes:
        raise SystemExit("Post-write verification failed: the written file does not diff as intended")
    return {
        "level": level, "ratio": ratio, "prism_mode": prism_mode,
        "pipeline": str(out.relative_to(REPO)),
        "scaled_env": moved,
        "held_fixed": [name for name in template["env"] if name not in scaled],
        "expected_cells": expected_cells,
        "reference_cells": REFERENCE_CELLS,
        "derivation_tool_sha256": sha256(Path(__file__).resolve()),
        "template_sha256": actual_template_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", required=True)
    parser.add_argument("--ratio", required=True, type=float)
    parser.add_argument("--prism-mode", choices=("systematic", "fixed"), default="systematic")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing derived pipeline, after backing it up")
    args = parser.parse_args(argv)
    print(json.dumps(build(args.level, args.ratio, args.prism_mode, args.force),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
