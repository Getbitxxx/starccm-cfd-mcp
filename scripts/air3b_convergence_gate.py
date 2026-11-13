"""AIR-3B steady MRF convergence screen over merged segment evidence.

This reads the long-format tables that RunAir3bMrfSegment.java writes after every
production segment and screens them against the CaseSpec acceptance criteria for the
propeller_mrf scenario:

  - equation residuals (continuity, momentum, energy, turbulence) against the CaseSpec
    limits, which are defined for NORMALIZED residuals, and
  - a steady window on thrust and torque, using the shared steady-history screen from
    evaluate_convergence.py so the window semantics are not duplicated.

The tables are one row per sample: kind, name, iteration, value. The iteration column is
the first numeric field of STAR's own monitor export, taken by the macro.

Passing this screen is a numerical screen only. Residual normalization provenance has to
be established separately, and mesh, domain, time-step and experimental validation are all
outside this module; the returned record says so explicitly rather than implying otherwise.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_convergence import assess_steady_history  # noqa: E402

CATEGORY_ALIASES = {
    "continuity": ("continuity",),
    "momentum": ("momentum",),
    "energy": ("energy",),
    "turbulence": ("tke", "sdr", "turbulen", "omega", "dissipation"),
}
DEFAULT_RESIDUAL_LIMITS = {
    "continuity": 1e-4,
    "momentum": 2.5e-4,
    "energy": 1e-3,
    "turbulence": 1e-3,
}


def read_long_table(path: str | Path) -> dict[str, list[tuple[float, float]]]:
    """Reads a merged long-format table into name -> [(iteration, value)] with unique x."""
    series: dict[str, dict[float, float]] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\n").split("\t")
        if header[:4] != ["kind", "name", "first_number", "second_number"]:
            raise ValueError("Unexpected long-table header: %r" % (header[:4],))
        for number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4:
                raise ValueError("Malformed long-table row %d" % number)
            name = fields[1]
            try:
                x, y = float(fields[2]), float(fields[3])
            except ValueError as exc:
                raise ValueError("Non-numeric long-table row %d" % number) from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("Non-finite long-table row %d" % number)
            series.setdefault(name, {})[x] = y
    result = {name: sorted(points.items()) for name, points in series.items()}
    if not result:
        raise ValueError("No samples found in %s" % path)
    return result


def read_segment_history(path: str | Path) -> dict[str, list[tuple[float, float]]]:
    """Reads the macro-written per-chunk segment history into per-quantity series.

    This is the trusted load source: RunAir3bMrfSegment.java writes it directly from report values,
    so it does not depend on parsing STAR's own export layout, which the merged monitor table does.
    """
    series = {"MON_THRUST_Z": [], "MON_TORQUE_Z": []}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\n").split(",")
        if header[:4] != ["iteration", "elapsed_seconds", "thrust_z_n", "torque_z_nm"]:
            raise ValueError("Unexpected segment history header: %r" % (header[:4],))
        for number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split(",")
            if len(fields) < 4:
                raise ValueError("Malformed segment history row %d" % number)
            try:
                iteration = float(fields[0])
                thrust, torque = float(fields[2]), float(fields[3])
            except ValueError as exc:
                raise ValueError("Non-numeric segment history row %d" % number) from exc
            if not all(math.isfinite(v) for v in (iteration, thrust, torque)):
                raise ValueError("Non-finite segment history row %d" % number)
            series["MON_THRUST_Z"].append((iteration, thrust))
            series["MON_TORQUE_Z"].append((iteration, torque))
    if not series["MON_THRUST_Z"]:
        raise ValueError("No samples found in %s" % path)
    if len(series["MON_THRUST_Z"]) != len(series["MON_TORQUE_Z"]):
        raise ValueError("Segment history columns have different lengths")
    return series


def classify_residual_scale(provenance: dict | None) -> str:
    """Classifies the recorded residual scale as normalized, absolute, or unknown.

    The CaseSpec limits were written for normalized residuals, so the scale a monitor actually
    uses decides whether those limits may gate the verdict at all. An absolute or OFF reading is
    a legitimate recorded outcome - the precondition is that the scale is KNOWN, not that it is
    normalized - but it means the limits are being applied to a different quantity.
    """
    if not provenance:
        return "unknown"
    monitors = provenance.get("monitors")
    if not isinstance(monitors, dict) or not monitors:
        return "unknown"
    options = [str(value).upper() for value in monitors.values()]
    if any("OFF" in option or "ABSOLUTE" in option for option in options):
        return "absolute"
    # AUTO is STAR's automatic normalization and is what the CaseSpec invokes by name
    # ("STAR residual monitor normalization; export settings and denominators"). Measured
    # corroboration on this case: the monitors read AUTO and several residuals are exactly 1.0
    # at iteration 1, which is the signature of normalization by the initial residual. The
    # official 2502 Help does not document the residual normalization semantics, so this mapping
    # rests on the CaseSpec wording plus that measured signature, and DEFAULT is accepted as the
    # same thing rather than left unrecognised.
    if any(("ON" in option) or ("NORMAL" in option) or ("INITIAL" in option)
           or option in ("AUTO", "DEFAULT", "AUTOMATIC") for option in options):
        return "normalized"
    return "unknown"


def categorise(name: str) -> str | None:
    lowered = name.lower()
    for category, aliases in CATEGORY_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return category
    return None


def tail_mean(values: list[float], window: int) -> float:
    if window < 1:
        raise ValueError("Window must be positive")
    tail = values[-window:]
    return sum(tail) / len(tail)


def screen_residuals(histories: dict[str, list[tuple[float, float]]], limits: dict[str, float] | None,
                     window: int = 50) -> dict:
    """Takes the worst tail-mean residual per CaseSpec category.

    A series shorter than the requested window is not averaged over whatever it has: averaging
    three samples and calling the result a tail mean would let a handful of rows pass a gate that
    two windows of evidence are supposed to decide. Short series are reported and excluded, and a
    category whose only series are short fails closed.
    """
    limits = dict(DEFAULT_RESIDUAL_LIMITS if limits is None else limits)
    per_series = {}
    worst: dict[str, dict] = {}
    for name, samples in histories.items():
        category = categorise(name)
        usable = len(samples) >= window
        value = tail_mean([y for _, y in samples], window) if usable else None
        per_series[name] = {"category": category, "samples": len(samples),
                            "last_iteration": samples[-1][0], "tail_mean": value,
                            "tail_window": window if usable else 0,
                            "sufficient_samples": usable}
        if category is None or not usable:
            continue
        current = worst.get(category)
        if current is None or value > current["tail_mean"]:
            worst[category] = {"series": name, "tail_mean": value,
                               "last_iteration": samples[-1][0]}
    categories = {}
    for category, limit in limits.items():
        found = worst.get(category)
        categories[category] = {
            "limit": limit,
            "worst_series": None if found is None else found["series"],
            "worst_tail_mean": None if found is None else found["tail_mean"],
            "last_iteration": None if found is None else found["last_iteration"],
            "present": found is not None,
            "passed": found is not None and found["tail_mean"] <= limit,
        }
    return {"per_series": per_series, "categories": categories,
            "passed": all(item["passed"] for item in categories.values())}


def screen_window(histories: dict[str, list[tuple[float, float]]], targets: dict[str, str],
                  window_samples: int, relative_range_limit: float,
                  relative_drift_limit: float, absolute_scale_floor: float) -> dict:
    """Screens each target quantity with the shared steady-history assessment.

    Targets are matched by exact series name. Substring matching with an arbitrary tie-break would
    silently pick the wrong series the moment a second thrust-like monitor exists, and a steady
    window computed on the wrong quantity is worse than no window at all.
    """
    results = {}
    for label, match in targets.items():
        if match not in histories:
            results[label] = {"present": False, "passed": False,
                              "reason": "no series named %r" % match}
            continue
        name = match
        samples = histories[name]
        if len(samples) < 2 * window_samples:
            results[label] = {"present": True, "series": name, "samples": len(samples),
                              "required_samples": 2 * window_samples, "passed": False,
                              "reason": "not enough samples for two complete windows"}
            continue
        assessment = assess_steady_history(samples, window_samples, relative_range_limit,
                                          relative_drift_limit, absolute_scale_floor)
        assessment.update({"present": True, "series": name})
        results[label] = assessment
    return {"targets": results, "window_samples": window_samples,
            "passed": all(item.get("passed", False) for item in results.values())}


def evaluate(residuals: dict[str, list[tuple[float, float]]],
             monitors: dict[str, list[tuple[float, float]]],
             limits: dict[str, float] | None = None, residual_window: int = 50,
             window_iterations: int = 200, sample_interval: int = 1,
             relative_range_limit: float = 0.005,
             relative_drift_limit: float = 0.005,
             absolute_scale_floor: float = 1e-12,
             normalization_provenance: dict | None = None) -> dict:
    """Screens residuals and the steady window.

    Residual normalization provenance is a hard precondition, not a footnote: the CaseSpec limits
    are only meaningful against STAR's normalized residuals, and this configuration sets no
    reference velocity, density or area, so an unqualified residual pass would be unfounded.
    Without provenance the residual half cannot pass, whatever the numbers say.
    """
    limits = dict(DEFAULT_RESIDUAL_LIMITS if limits is None else limits)
    if sample_interval < 1:
        raise ValueError("Sample interval must be at least one iteration")
    window_samples = max(2, int(round(window_iterations / sample_interval)))
    residual_screen = screen_residuals(residuals, limits, residual_window)
    window_screen = screen_window(
        monitors, {"thrust_z_n": "MON_THRUST_Z", "torque_z_nm": "MON_TORQUE_Z"},
        window_samples, relative_range_limit, relative_drift_limit, absolute_scale_floor)
    provenance_ok = bool(normalization_provenance) and all(
        normalization_provenance.get(key) not in (None, "", "unknown")
        for key in ("monitors", "source"))
    scale = classify_residual_scale(normalization_provenance) if provenance_ok else "unknown"
    residual_screen["normalization_provenance"] = normalization_provenance
    residual_screen["normalization_provenance_present"] = provenance_ok
    residual_screen["residual_scale"] = scale
    # The raw option strings are preserved whenever provenance exists, in all three cases: the
    # datum case is exactly where a later reader asks what scale the numbers were measured on.
    if provenance_ok:
        residual_screen["raw_options"] = sorted(
            {str(v) for v in (normalization_provenance.get("monitors") or {}).values()})
    # The residual half may gate only when the scale it was measured on is the scale the limits
    # were written for. Without provenance nothing is known, so the conservative outcome is a
    # failure; with a known absolute/OFF scale the numbers are reported as a datum and are kept
    # out of the verdict, because testing absolute residuals against normalized limits would be
    # a category error in either direction.
    residual_gating = True
    deferred = None
    if not provenance_ok:
        # Nothing is known about the scale, so the conservative outcome is that the residual half
        # fails and keeps gating: an unestablished precondition must not quietly become a pass.
        residual_screen["passed"] = False
        residual_screen["blocked_by"] = "residual normalization provenance is not established"
    elif scale == "absolute":
        # A RECOGNISED absolute/OFF scale means the numbers are a legitimate measurement on a scale
        # the CaseSpec limits were not written for, so they are reported as a datum and kept out of
        # the verdict: neither passing them against normalized limits nor failing them would be a
        # statement about convergence.
        residual_gating = False
        deferred = ("residuals are reported as a measured datum, not a gate: the recorded residual "
                    "scale is 'absolute' while the CaseSpec limits are defined for normalized "
                    "residuals, so either the CaseSpec must declare absolute-residual limits or "
                    "this half is filed without gating the verdict")
        residual_screen["datum_only"] = True
    elif scale != "normalized":
        # An UNRECOGNISED spelling is not the same thing as a recognised absolute scale: nothing
        # establishes what the numbers are measured against, so the residual half fails and keeps
        # gating. Running this screen on real segment evidence showed that deferring here let the
        # overall verdict read True while the residuals were 320x above their limits, which is the
        # opposite of the rule that unestablished provenance cannot become a pass.
        residual_screen["passed"] = False
        residual_screen["residual_scale_unrecognized"] = True
        residual_screen["blocked_by"] = ("residual normalization scale was recorded but not "
                                         "recognised, so the limits cannot be applied")
    return {
        "residuals": residual_screen,
        "steady_window": window_screen,
        "window_iterations": window_iterations,
        "sample_interval": sample_interval,
        "window_samples": window_samples,
        "residuals_passed": residual_screen["passed"],
        "residuals_gating": residual_gating,
        "residual_gate_deferred": deferred,
        "steady_window_passed": window_screen["passed"],
        "passed": window_screen["passed"] and (residual_screen["passed"] or not residual_gating),
        "physical_validation": False,
        "limitations": [
            "This is a numerical screen over exported monitor histories; it does not establish "
            "physical accuracy, mesh or domain independence, or agreement with experiment.",
            "Residual normalization provenance must be established from the simulation's monitor "
            "settings. The CaseSpec limits are defined for normalized residuals; a recorded "
            "absolute or OFF scale is reported as a datum and is kept out of the verdict rather "
            "than compared against limits written for a different scale.",
            "Mass conservation is not assessed here and remains a separate gate.",
        ],
    }


def load_monitors(path: str | Path) -> dict[str, list[tuple[float, float]]]:
    """Loads either a macro-written segment history or a merged monitor long table.

    The macro-written per-chunk history is the trusted source because it does not depend on
    parsing STAR's own export layout; the merged long table is accepted as a cross-check.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline().rstrip("\n").replace("\t", ",").split(",")
    if header[:4] == ["iteration", "elapsed_seconds", "thrust_z_n", "torque_z_nm"]:
        return read_segment_history(path)
    return read_long_table(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--residuals", required=True, help="merged residual long table")
    parser.add_argument("--monitors", required=True,
                        help="macro-written segment history, or a merged monitor long table")
    parser.add_argument("--provenance", required=True,
                        help="JSON recording residual normalization provenance; without it the "
                             "residual half cannot pass")
    parser.add_argument("--window-iterations", type=int, default=200)
    parser.add_argument("--sample-interval", type=int, default=1)
    parser.add_argument("--residual-window", type=int, default=50)
    parser.add_argument("--relative-range-limit", type=float, default=0.005)
    parser.add_argument("--relative-drift-limit", type=float, default=0.005)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    provenance = json.loads(Path(args.provenance).read_text(encoding="utf-8"))
    result = evaluate(read_long_table(args.residuals), load_monitors(args.monitors),
                      residual_window=args.residual_window,
                      window_iterations=args.window_iterations,
                      sample_interval=args.sample_interval,
                      relative_range_limit=args.relative_range_limit,
                      relative_drift_limit=args.relative_drift_limit,
                      normalization_provenance=provenance)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
