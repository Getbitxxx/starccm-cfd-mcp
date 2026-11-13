"""Evidence-based steady/transient monitor screening.

This module evaluates exported monitor histories only. Passing its numerical
screen does not establish physical accuracy, mesh independence, or experimental
validation; residual scaling and mesh quality remain separate gates.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Iterable


def read_history(path: str | Path) -> list[tuple[float, float]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 3:
        raise ValueError("History requires a header and at least two samples")
    result = []
    for number, row in enumerate(rows[1:], start=2):
        if len(row) < 2:
            raise ValueError(f"Malformed monitor row {number}")
        try:
            x, y = float(row[0]), float(row[1])
        except ValueError as exc:
            raise ValueError(f"Non-numeric monitor row {number}") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"Non-finite monitor row {number}")
        if result and x <= result[-1][0]:
            raise ValueError("Monitor coordinates must be strictly increasing")
        result.append((x, y))
    return result


def _relative_spread(values: list[float], absolute_scale_floor: float) -> float:
    mean = sum(values) / len(values)
    scale = max(abs(mean), absolute_scale_floor)
    return (max(values) - min(values)) / scale


def assess_steady_history(samples: Iterable[tuple[float, float]], window_samples: int = 30,
                          relative_range_limit: float = 0.005,
                          relative_drift_limit: float = 0.005,
                          absolute_scale_floor: float = 1e-12) -> dict:
    data = list(samples)
    if window_samples < 2 or len(data) < 2 * window_samples:
        raise ValueError("Steady assessment requires two complete monitor windows")
    if any(not math.isfinite(x) or not math.isfinite(y) for x, y in data):
        raise ValueError("Monitor history contains non-finite data")
    if any(data[i][0] >= data[i + 1][0] for i in range(len(data) - 1)):
        raise ValueError("Monitor coordinates must be strictly increasing")
    if (not math.isfinite(relative_range_limit) or relative_range_limit < 0 or
            not math.isfinite(relative_drift_limit) or relative_drift_limit < 0 or
            not math.isfinite(absolute_scale_floor) or absolute_scale_floor <= 0):
        raise ValueError("Steady limits must be finite and nonnegative; scale floor must be positive")
    values = [value for _, value in data]
    previous, tail = values[-2 * window_samples:-window_samples], values[-window_samples:]
    prev_mean = sum(previous) / len(previous)
    tail_mean = sum(tail) / len(tail)
    scale = max(abs(tail_mean), absolute_scale_floor)
    result = {
        "samples": len(data), "window_samples": window_samples,
        "last_window_mean": tail_mean,
        "last_window_relative_range": _relative_spread(tail, absolute_scale_floor),
        "last_two_window_relative_drift": abs(tail_mean - prev_mean) / scale,
        "relative_range_limit": relative_range_limit,
        "relative_drift_limit": relative_drift_limit,
    }
    result["passed"] = (result["last_window_relative_range"] <= relative_range_limit and
                        result["last_two_window_relative_drift"] <= relative_drift_limit)
    return result


def _integral_mean(samples: list[tuple[float, float]], start: float, end: float) -> float:
    """Trapezoidal mean over [start,end], linearly interpolating endpoints."""
    if start < samples[0][0] or end > samples[-1][0] or end <= start:
        raise ValueError("Requested cycle is outside the monitor time range")
    points = [(start, _interpolate(samples, start))]
    points.extend((t, y) for t, y in samples if start < t < end)
    points.append((end, _interpolate(samples, end)))
    area = sum((points[i + 1][0] - points[i][0]) *
               (points[i + 1][1] + points[i][1]) * 0.5
               for i in range(len(points) - 1))
    return area / (end - start)


def _interpolate(samples: list[tuple[float, float]], t: float) -> float:
    lo, hi = 0, len(samples) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if samples[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    t0, y0 = samples[lo]
    t1, y1 = samples[hi]
    if t == t0:
        return y0
    if t == t1:
        return y1
    return y0 + (y1 - y0) * ((t - t0) / (t1 - t0))


def assess_transient_history(samples: Iterable[tuple[float, float]], rpm: float,
                             stability_window_revolutions: int = 3,
                             relative_cycle_spread_limit: float = 0.01,
                             relative_cycle_drift_limit: float = 0.01,
                             absolute_scale_floor: float = 1e-12) -> dict:
    data = list(samples)
    if not math.isfinite(rpm) or rpm == 0:
        raise ValueError("rpm must be finite and nonzero")
    if (isinstance(stability_window_revolutions, bool) or
            not isinstance(stability_window_revolutions, int) or stability_window_revolutions < 2):
        raise ValueError("At least two revolutions are required for a stability comparison")
    if len(data) < 3 or any(not math.isfinite(x) or not math.isfinite(y) for x, y in data):
        raise ValueError("Transient assessment requires finite monitor history")
    if any(data[i][0] >= data[i + 1][0] for i in range(len(data) - 1)):
        raise ValueError("Monitor times must be strictly increasing")
    period = 60.0 / abs(rpm)
    end = data[-1][0]
    start = end - stability_window_revolutions * period
    if start < data[0][0]:
        raise ValueError("Monitor history does not cover the requested stability window")
    means = [_integral_mean(data, start + i * period, start + (i + 1) * period)
             for i in range(stability_window_revolutions)]
    spread = _relative_spread(means, absolute_scale_floor)
    half = len(means) // 2
    early = sum(means[:half]) / half
    late = sum(means[-half:]) / half
    drift = abs(late - early) / max(abs(late), absolute_scale_floor)
    if (not math.isfinite(relative_cycle_spread_limit) or relative_cycle_spread_limit < 0 or
            not math.isfinite(relative_cycle_drift_limit) or relative_cycle_drift_limit < 0 or
            not math.isfinite(absolute_scale_floor) or absolute_scale_floor <= 0):
        raise ValueError("Transient limits must be finite and nonnegative; scale floor must be positive")
    result = {
        "samples": len(data), "rpm": rpm, "period_s": period,
        "stability_window_revolutions": stability_window_revolutions,
        "cycle_means": means, "relative_cycle_mean_spread": spread,
        "relative_early_to_late_cycle_drift": drift,
        "relative_cycle_spread_limit": relative_cycle_spread_limit,
        "relative_cycle_drift_limit": relative_cycle_drift_limit,
    }
    result["passed"] = (spread <= relative_cycle_spread_limit and
                        drift <= relative_cycle_drift_limit)
    return result


def assess_mass_imbalance(inlet_mass_flow: float, outlet_mass_flow: float,
                          relative_limit: float = 0.001,
                          absolute_scale_floor: float = 1e-12) -> dict:
    values = (inlet_mass_flow, outlet_mass_flow, relative_limit, absolute_scale_floor)
    if any(not math.isfinite(value) for value in values) or relative_limit < 0 or absolute_scale_floor <= 0:
        raise ValueError("Mass-flow values and limits must be finite; limit nonnegative and scale floor positive")
    # STAR outlet mass-flow reports commonly use a negative outward-normal sign.
    imbalance = abs(abs(inlet_mass_flow) - abs(outlet_mass_flow))
    scale = max(abs(inlet_mass_flow), abs(outlet_mass_flow), absolute_scale_floor)
    result = {"inlet_mass_flow": inlet_mass_flow, "outlet_mass_flow": outlet_mass_flow,
              "relative_imbalance": imbalance / scale, "relative_limit": relative_limit}
    result["passed"] = result["relative_imbalance"] <= relative_limit
    return result


def evaluate(case: dict, root: Path) -> dict:
    scenario = case.get("scenario")
    if scenario not in {"external_static", "propeller_mrf", "rotating_transient"}:
        raise ValueError("Unsupported scenario")
    settings = case.get("criteria", {})
    results = {}
    for name, filename in case.get("target_histories", {}).items():
        history = read_history(root / filename)
        if scenario == "rotating_transient":
            result = assess_transient_history(
                history, case["rpm"], case.get("stability_window_revolutions", 3),
                settings.get("relative_cycle_spread_limit", 0.01),
                settings.get("relative_cycle_drift_limit", 0.01),
                settings.get("absolute_scale_floor", 1e-12))
        else:
            result = assess_steady_history(
                history, case.get("window_samples", 30),
                settings.get("relative_range_limit", 0.005),
                settings.get("relative_drift_limit", 0.005),
                settings.get("absolute_scale_floor", 1e-12))
        results[name] = result
    if not results:
        raise ValueError("At least one target history is required")
    mass = None
    if "mass_flow" in case:
        mass = assess_mass_imbalance(
            case["mass_flow"]["inlet"], case["mass_flow"]["outlet"],
            settings.get("mass_imbalance_limit", 0.001),
            settings.get("absolute_scale_floor", 1e-12))
    return {"scenario": scenario, "target_histories": results, "mass_balance": mass,
            "monitor_stability_passed": all(item["passed"] for item in results.values()),
            "mass_balance_passed": None if mass is None else mass["passed"],
            "physical_validation": False,
            "limitations": ["Monitor screening does not replace equation-specific residual assessment.",
                            "Mesh quality, domain/mesh/time-step sensitivity and experimental validation are separate gates."]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: evaluate_convergence.py CASE.json EVIDENCE_DIR")
    case = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    report = evaluate(case, Path(sys.argv[2]))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(0 if report["monitor_stability_passed"] and
                     report["mass_balance_passed"] is not False else 2)
