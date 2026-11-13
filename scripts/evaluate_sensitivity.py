"""Compare CFD target quantities across mesh/domain/time-step study levels."""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path


MIN_LEVELS = {"mesh": 3, "domain": 2, "time_step": 3}


def compare_study(kind: str, rows: list[dict], quantities: list[str],
                  relative_change_limit: float = 0.02,
                  absolute_scale_floors: dict[str, float] | None = None) -> dict:
    if kind not in MIN_LEVELS:
        raise ValueError("kind must be mesh, domain, or time_step")
    if not isinstance(rows, list) or len(rows) < MIN_LEVELS[kind]:
        raise ValueError(f"{kind} sensitivity requires at least {MIN_LEVELS[kind]} levels")
    if not quantities or len(set(quantities)) != len(quantities):
        raise ValueError("quantities must be a nonempty list of distinct names")
    if not math.isfinite(relative_change_limit) or relative_change_limit < 0:
        raise ValueError("relative_change_limit must be finite and nonnegative")
    floors = absolute_scale_floors or {}
    for name, floor in floors.items():
        if name not in quantities or isinstance(floor, bool) or not isinstance(floor, (int, float)) or not math.isfinite(floor) or floor <= 0:
            raise ValueError(f"absolute scale floor for {name} must be finite and positive")
    xkey = {"mesh": "cells", "domain": "domain_scale", "time_step": "time_step_s"}[kind]
    levels = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or xkey not in row:
            raise ValueError(f"level {index} lacks {xkey}")
        parameter = row[xkey]
        if isinstance(parameter, bool) or not isinstance(parameter, (int, float)) or not math.isfinite(parameter) or parameter <= 0:
            raise ValueError(f"level {index} has invalid {xkey}")
        values = {}
        for name in quantities:
            value = row.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"level {index} lacks finite target quantity {name}")
            values[name] = float(value)
        levels.append((float(parameter), values))
    parameters = [item[0] for item in levels]
    if len(set(parameters)) != len(parameters):
        raise ValueError(f"{xkey} values must be distinct")
    # Normalize a study into increasing resolution/extent order.
    levels.sort(key=lambda item: item[0], reverse=(kind == "time_step"))
    comparisons = {}
    for name in quantities:
        values = [item[1][name] for item in levels]
        floor = floors.get(name, 1e-12)
        changes = []
        for index in range(len(values) - 1):
            coarse, fine = values[index], values[index + 1]
            scale = max(abs(coarse), abs(fine), floor)
            changes.append({
                "from_parameter": levels[index][0],
                "to_parameter": levels[index + 1][0],
                "from_value": coarse,
                "to_value": fine,
                "absolute_change": abs(fine - coarse),
                "relative_change": abs(fine - coarse) / scale,
            })
        comparisons[name] = {"levels": values, "adjacent_changes": changes,
                             "maximum_relative_change": max(item["relative_change"] for item in changes),
                             "absolute_scale_floor": floor,
                             "passed": all(item["relative_change"] <= relative_change_limit for item in changes)}
    return {
        "study_kind": kind,
        "parameter": xkey,
        "levels": [{xkey: parameter, **values} for parameter, values in levels],
        "minimum_levels": MIN_LEVELS[kind],
        "relative_change_limit": relative_change_limit,
        "quantity_comparisons": comparisons,
        "passed": all(item["passed"] for item in comparisons.values()),
        "physical_validation": False,
        "interpretation": "Numerical study comparison only; it does not establish model fidelity or experimental validity.",
    }


def evaluate_file(path: str | Path, kind: str) -> dict:
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("Study CSV requires a header")
        xkey = {"mesh": "cells", "domain": "domain_scale", "time_step": "time_step_s"}.get(kind)
        if xkey is None:
            raise ValueError("kind must be mesh, domain, or time_step")
        quantity_names = [name for name in reader.fieldnames if name != xkey]
        rows = []
        for row in reader:
            converted = {xkey: float(row[xkey])}
            converted.update({name: float(row[name]) for name in quantity_names})
            rows.append(converted)
    return compare_study(kind, rows, quantity_names)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: evaluate_sensitivity.py {mesh|domain|time_step} STUDY.csv")
    result = evaluate_file(sys.argv[2], sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(0 if result["passed"] else 2)
