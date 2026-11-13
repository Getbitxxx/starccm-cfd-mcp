"""Measure the residual decay law of the AIR-3B production chain and test for a plateau.

Segment 1 showed that the local decay rate is the thing that decides whether more segments can
reach the CaseSpec residual limits, and that a rate measured over one short window is easy to
over-read: the pilot's early 0.898 per-iteration slope projected 60 to 80 iterations when the
measured slope over iterations 300 to 680 was 0.9982 and the true requirement was thousands.

This reads the merged residual long table that RunAir3bMrfSegment.java writes, fits the decay over
several successive windows, and reports:
  - the per-iteration factor in each window, so degradation is visible rather than averaged away,
  - a plateau test: whether the late-window factor has risen close enough to 1.0 that the remaining
    iterations to a limit exceed a stated budget,
  - projected iterations to each CaseSpec limit from the latest window, and the wall-clock cost at a
    supplied seconds-per-iteration.

A plateau verdict means more segments cannot close the gap on the current numerics, which is the
signal to change solver settings or to re-declare the criterion on the measured scale - not to keep
solving.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_convergence import read_history  # noqa: E402  (same two-column contract)

DEFAULT_LIMITS = {"Continuity": 1e-4, "X-momentum": 2.5e-4, "Y-momentum": 2.5e-4,
                  "Z-momentum": 2.5e-4, "Energy": 1e-3, "Tke": 1e-3, "Sdr": 1e-3}


def read_residual_table(path: str | Path) -> dict[str, list[tuple[float, float]]]:
    """Reads the merged residual long table into name -> [(iteration, value)]."""
    series: dict[str, dict[float, float]] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\n").split("\t")
        if header[:4] != ["kind", "name", "first_number", "second_number"]:
            raise ValueError("Unexpected residual table header: %r" % (header[:4],))
        for number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4:
                raise ValueError("Malformed row %d" % number)
            try:
                x, y = float(fields[2]), float(fields[3])
            except ValueError as exc:
                raise ValueError("Non-numeric row %d" % number) from exc
            if not math.isfinite(x) or not math.isfinite(y) or y <= 0:
                # A zero or negative residual carries no decay information and is not silently
                # treated as a fast decay to the limit.
                continue
            series.setdefault(fields[1], {})[x] = y
    if not series:
        raise ValueError("No usable residual series in %s" % path)
    return {name: sorted(points.items()) for name, points in series.items()}


def window_rate(points: list[tuple[float, float]], start: float, end: float) -> float | None:
    """Per-iteration decay factor between two iterations, by interpolation on log residual."""
    xs = [x for x, _ in points]
    if start < xs[0] or end > xs[-1] or end <= start:
        return None
    lo = _value_at(points, start)
    hi = _value_at(points, end)
    if lo <= 0 or hi <= 0:
        return None
    return (hi / lo) ** (1.0 / (end - start))


def _value_at(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for i in range(1, len(points)):
        if points[i][0] >= x:
            x0, y0 = points[i - 1]
            x1, y1 = points[i]
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0))
    return points[-1][1]


def clip(points: list[tuple[float, float]], start: float | None, end: float | None
         ) -> list[tuple[float, float]]:
    """Restricts a series to [start, end].

    Added after review round 17: a residual export carries the whole stored history (iterations
    1..N) because every checkpoint re-exports it, so the default windows span 1..last and cannot
    isolate the iterations a new experiment actually produced. Without a clip, an experiment that
    changed iterations 1240..1640 would be judged largely on data it did not generate.
    """
    if start is None and end is None:
        return points
    lo = points[0][0] if start is None else start
    hi = points[-1][0] if end is None else end
    if hi <= lo:
        raise ValueError("Empty window: start %r end %r" % (start, end))
    return [(x, y) for x, y in points if lo <= x <= hi]


def analyse(series: dict[str, list[tuple[float, float]]], limits: dict[str, float] | None = None,
            windows: int = 4, seconds_per_iteration: float = 2.1,
            plateau_iteration_budget: int = 12000,
            start: float | None = None, end: float | None = None) -> dict:
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    report: dict[str, dict] = {}
    for name, raw in sorted(series.items()):
        points = clip(raw, start, end)
        if len(points) < 2:
            report[name] = {"samples": len(points), "note": "window contains fewer than two samples"}
            continue
        first_written, last_written = raw[0][0], raw[-1][0]
        first, last = points[0][0], points[-1][0]
        span = last - first
        if span < windows * 20:
            report[name] = {"samples": len(points), "first": first, "last": last,
                            "note": "iteration span too short for the requested windows"}
            continue
        step = span / windows
        rates = []
        for i in range(windows):
            a = first + i * step
            b = first + (i + 1) * step
            r = window_rate(points, a, b)
            rates.append({"from": round(a, 1), "to": round(b, 1),
                          "per_iteration": None if r is None else round(r, 6)})
        usable = [r["per_iteration"] for r in rates if r["per_iteration"] is not None]
        latest = usable[-1] if usable else None
        limit = limits.get(name)
        entry = {"samples": len(points), "first": first, "last": last, "last_value": points[-1][1],
                 "rates": rates, "series_first": first_written, "series_last": last_written,
                 "window": [start, end]}
        if limit is not None and latest is not None and points[-1][1] > limit:
            if latest >= 1.0:
                entry["projected_iterations_to_limit"] = None
                entry["plateau"] = True
            else:
                needed = math.log(limit / points[-1][1]) / math.log(latest)
                entry["projected_iterations_to_limit"] = int(math.ceil(needed))
                entry["projected_wall_hours"] = round(needed * seconds_per_iteration / 3600.0, 2)
                # Degradation test: if the rate is rising towards 1.0, or the projection exceeds the
                # budget, more segments are not the answer.
                entry["plateau"] = (latest > 0.999) or (needed > plateau_iteration_budget)
                entry["rate_degradation"] = (None if len(usable) < 2 else round(usable[-1] - usable[0], 6))
        elif limit is not None:
            entry["meets_limit"] = True
        report[name] = entry
    gated = {k: v for k, v in report.items() if "meets_limit" not in v
             and "projected_iterations_to_limit" in v}
    return {
        "windows": windows, "seconds_per_iteration": seconds_per_iteration,
        "window": [start, end],
        "plateau_iteration_budget": plateau_iteration_budget,
        "series": report,
        "any_plateau": any(v.get("plateau") for v in gated.values()),
        "max_projected_iterations": max((v["projected_iterations_to_limit"] or 0) for v in gated.values())
        if gated else 0,
        "recommendation": ("plateau: more segments cannot close the gap on the current numerics, so "
                           "change solver settings or re-declare the criterion on the measured scale"
                           if any(v.get("plateau") for v in gated.values()) else
                           "continue solving: the measured slopes still project to the limits inside "
                           "the stated iteration budget"),
        "limitations": ["A projected rate is an extrapolation of a decaying transient, not a guarantee.",
                        "The plateau test is a heuristic on the latest window and the budget."],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residuals", required=True)
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--seconds-per-iteration", type=float, default=2.1)
    parser.add_argument("--plateau-iteration-budget", type=int, default=12000)
    parser.add_argument("--start", type=float, default=None,
                        help="first iteration to include; omit for the whole stored history")
    parser.add_argument("--end", type=float, default=None,
                        help="last iteration to include")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    result = analyse(read_residual_table(args.residuals), windows=args.windows,
                     seconds_per_iteration=args.seconds_per_iteration,
                     plateau_iteration_budget=args.plateau_iteration_budget,
                     start=args.start, end=args.end)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
