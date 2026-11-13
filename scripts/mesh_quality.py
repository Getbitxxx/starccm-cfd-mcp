"""Fail-closed gate for STAR-CCM+ volume mesh quality audit CSVs."""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "mesh_min_cell_quality": 1e-5,
    "mesh_min_face_validity": 1.0,
    "mesh_min_volume_change": 0.01,
}


def assess_mesh_quality(csv_path: str | Path, quality: dict, regions: list[str]) -> dict:
    thresholds = {key: quality.get(key, default) for key, default in DEFAULT_THRESHOLDS.items()}
    for key, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError(f"{key} must be a finite number in (0, 1]")
    if not regions or len(set(regions)) != len(regions):
        raise ValueError("Expected mesh Regions must be distinct and nonempty")
    rows = {}
    with Path(csv_path).open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            key = (row["region"], row["metric"], row["stat"])
            if key in rows:
                raise ValueError(f"Duplicate mesh audit row: {key}")
            value = float(row["value"])
            if not math.isfinite(value):
                raise ValueError(f"Nonfinite mesh audit value: {key}")
            rows[key] = value
    if rows.get(("ALL", "Audit", "complete")) != 1:
        raise ValueError("Mesh audit is incomplete")
    total = rows.get(("ALL", "Cells", "count"))
    if total is None or total <= 0 or total != int(total):
        raise ValueError("Mesh audit lacks a positive total cell count")
    checks = []
    counted = 0
    for region in regions:
        cells = rows.get((region, "ElementCount:cells", "count"))
        if cells is None or cells <= 0 or cells != int(cells):
            raise ValueError(f"Mesh audit lacks a positive cell count for {region}")
        counted += int(cells)
        for metric, threshold_key in (("CellQuality", "mesh_min_cell_quality"),
                                      ("FaceValidity", "mesh_min_face_validity"),
                                      ("VolumeChange", "mesh_min_volume_change")):
            value = rows.get((region, metric, "min"))
            if value is None:
                raise ValueError(f"Mesh audit lacks {region}/{metric}/min")
            threshold = thresholds[threshold_key]
            passed = value > threshold if metric == "VolumeChange" else value >= threshold
            checks.append({"region": region, "metric": metric, "minimum": value,
                           "threshold": threshold, "passed": passed})
    if counted != total:
        raise ValueError("Region cell counts do not match the total mesh cell count")
    return {"passed": all(item["passed"] for item in checks), "total_cells": int(total),
            "checks": checks, "thresholds": thresholds}


def assess_near_wall_quality(csv_path: str | Path,
                             expected_prism_layers: dict[str, int],
                             wall_y_plus_ranges: dict[str, tuple[float, float]]) -> dict:
    """Fail closed unless selected walls have actual prism-layer and y+ evidence.

    Input is a five-column STAR mesh-quality CSV extended by the candidate
    exporter. ``expected_prism_layers`` maps qualified ``Region/Boundary`` names
    to the minimum observed layers at every wall-face location; rows use section
    ``prism_layer``, metric ``PrismLayerNumber`` and stats ``min``/``max``.
    ``wall_y_plus_ranges`` maps qualified wall names to explicit acceptable y+
    min/max envelopes, so wall-treatment assumptions are not guessed.
    """
    if not expected_prism_layers or not wall_y_plus_ranges:
        raise ValueError("Expected prism-layer and wall y+ boundaries must be explicit")
    if set(expected_prism_layers) != set(wall_y_plus_ranges):
        raise ValueError("Prism-layer and y+ wall selectors must match")
    for boundary, minimum in expected_prism_layers.items():
        if (not isinstance(boundary, str) or not boundary or isinstance(minimum, bool) or
                not isinstance(minimum, int) or minimum <= 0):
            raise ValueError("Each expected wall needs a positive integer minimum prism-layer count")
    for boundary, bounds in wall_y_plus_ranges.items():
        if (not isinstance(boundary, str) or not boundary or not isinstance(bounds, (tuple, list)) or
                len(bounds) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or
                                        not math.isfinite(x) for x in bounds) or
                bounds[0] < 0 or bounds[0] > bounds[1]):
            raise ValueError("Each wall y+ range must be an explicit finite [minimum, maximum]")
    rows = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if header != ["section", "region", "metric", "stat", "value"]:
            raise ValueError("Expected ExportMeshQuality five-column CSV")
        for line, row in enumerate(reader, start=2):
            if len(row) != 5:
                raise ValueError(f"Malformed mesh-quality row {line}")
            section, region, metric, stat, raw = row
            try:
                value = float(raw)
            except ValueError:
                value = math.nan
            rows.append((section, region, metric, stat, value))
    checks = []
    for boundary, minimum_layers in expected_prism_layers.items():
        layer_rows = [row for row in rows if row[0] == "prism_layer" and row[1] == boundary and
                      row[2].lower() in {"prismlayernumber", "prism layer number", "layer_count", "layers"}
                      and row[3] in {"min", "max"}]
        by_stat = {row[3]: row[4] for row in layer_rows}
        observed_min, observed_max = by_stat.get("min"), by_stat.get("max")
        valid_layers = (observed_min is not None and observed_max is not None and
                        math.isfinite(observed_min) and math.isfinite(observed_max) and
                        observed_min == int(observed_min) and observed_max == int(observed_max) and
                        0 <= observed_min <= observed_max)
        checks.append({"kind": "prism_layers", "boundary": boundary,
                       "observed_min": observed_min, "observed_max": observed_max,
                       "minimum_layers": minimum_layers,
                       "passed": bool(valid_layers and observed_min >= minimum_layers)})
    for boundary, (minimum, maximum) in wall_y_plus_ranges.items():
        boundary_rows = [row for row in rows if row[0] == "y_plus" and row[1] == boundary]
        by_stat = {row[3]: row[4] for row in boundary_rows if row[2].lower() in
                   {"wallyplus", "wall y+", "wall yplus", "wall y plus", "yplus", "y+"}}
        observed_min, observed_max = by_stat.get("min"), by_stat.get("max")
        valid = (observed_min is not None and observed_max is not None and
                 math.isfinite(observed_min) and math.isfinite(observed_max) and
                 0 <= observed_min <= observed_max)
        checks.append({"kind": "wall_y_plus", "boundary": boundary,
                       "observed_min": observed_min, "observed_max": observed_max,
                       "acceptable_min": minimum, "acceptable_max": maximum,
                       "passed": bool(valid and observed_min >= minimum and observed_max <= maximum)})
    return {"passed": bool(checks) and all(item["passed"] for item in checks),
            "checks": checks,
            "limitations": ["Prism-layer evidence must be exported after meshing; absent or ambiguous layer data fails the gate.",
                            "Boundary min/max y+ is an envelope check, not a wall-area coverage fraction.",
                            "The user-specified y+ range must match the selected turbulence and wall-treatment model."]}


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit("Usage: mesh_quality.py AUDIT.csv CASESPEC.json REGION [REGION ...]")
    case_spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    result = assess_mesh_quality(sys.argv[1], case_spec.get("quality", {}), sys.argv[3:])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 2)
