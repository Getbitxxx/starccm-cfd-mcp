# -*- coding: utf-8 -*-
"""从**既有产物**导出逐迭代历史的两列：密度与 Cd（不跑 STAR，不改宏）。

背景
----
用户 2026-10-14 先选 A（改宏补 Cd 列并跑一次 STAR），后改为**选项 2**：先用既有产物导出。
独立审查（`test-evidence/engineering-validation-start-20261009/solve-history-derived-columns/review/`）
实测：既有 4 列历史的第 3 列 + `external_flow_solve.json` 的实测密度 + CaseSpec 的 U/A
即可**逐值相同**地重建宏会写出的 Cd。

诚实边界（必须随产物一起报告）
------------------------------
* **密度只能用求解末尾的那一个**（既有记录里没有求解前的密度），因此本导出与宏的计划实现
  （用求解前的密度）在基准上不同，与**宏当前实现**（历史列用求解前 rho）也不同；
  与官方 `measured_drag_coefficient_estimate`（用求解末 rho）同基准。
* **Cd 与 Fx 共线**：Cd = Fx × 常数（同一文件内 rho/U/A 恒定）。因此 Cd 的迭代误差估计是
  阻力估计的重标定，**不构成独立证据**。
* 产物文件名带 `.derived`，记录里 `derived: true`，不得当作求解器原生产出的历史。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

HEADER_SUFFIX = ("rho_volume_mean_kg_m3", "drag_coefficient_estimate")


class DerivationRefused(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def derive(trial_dir: Path, *, solve_name: str = "external_flow_solve.json",
           history_name: str = "external_flow_solve_history.csv",
           out_name: str | None = None) -> dict:
    """返回记录字典；同时写出 `<out_name>`（默认在原文件名后加 .derived.csv）。"""
    history = trial_dir / history_name
    solve = trial_dir / solve_name
    if not history.is_file():
        raise DerivationRefused("history missing: %s" % history)
    if not solve.is_file():
        raise DerivationRefused("solve record missing: %s" % solve)
    payload = json.loads(solve.read_text(encoding="utf-8"))

    def need(key: str) -> float:
        value = payload.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise DerivationRefused("solve record lacks a finite %s" % key)
        return float(value)

    rho = need("measured_density_volume_average_kg_m3")
    speed = need("inflow_speed_m_s")
    area = need("reference_area_m2")
    if not (rho > 0 and speed > 0 and area > 0):
        raise DerivationRefused("rho/U/A must be positive")
    official = payload.get("measured_drag_coefficient_estimate")

    with history.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise DerivationRefused("history is empty")
    header = rows[0]
    if tuple(header) == ("iteration", "elapsed_seconds", "drag_x_n", "mass_imbalance"):
        pass
    elif tuple(header) == ("iteration", "elapsed_seconds", "drag_x_n", "mass_imbalance") + HEADER_SUFFIX:
        raise DerivationRefused("history already carries the derived columns; refusing to re-derive")
    else:
        raise DerivationRefused("unexpected history header: %s" % (header,))

    out_rows = [list(header) + list(HEADER_SUFFIX)]
    coefficients: list[float] = []
    for index, row in enumerate(rows[1:], start=1):
        if len(row) != 4:
            raise DerivationRefused("row %d has %d fields, expected 4" % (index, len(row)))
        try:
            force = float(row[2])
        except ValueError as exc:
            raise DerivationRefused("row %d drag_x_n is not a number: %r" % (index, row[2])) from exc
        coefficient = 2.0 * force / (rho * speed * speed * area) if math.isfinite(force) else float("nan")
        coefficients.append(coefficient)
        out_rows.append(row + [repr(rho), repr(coefficient)])

    out_path = trial_dir / (out_name or (history.name + ".derived.csv"))
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(out_rows)

    finite = [value for value in coefficients if math.isfinite(value)]
    # 与官方 Cd 的自洽检查：官方用求解末 rho 与末次 Fx，本导出用同一 rho，故末行应几乎相等
    last_finite = next((value for value in reversed(coefficients) if math.isfinite(value)), None)
    relative_gap = (abs(last_finite - official) / abs(official)
                    if (last_finite is not None and isinstance(official, (int, float))
                        and not isinstance(official, bool) and official) else None)
    ratios = [c / f for c, f in zip(coefficients, [float(r[2]) for r in rows[1:]])
              if math.isfinite(c) and math.isfinite(float(f)) and float(f) != 0.0]
    spread = (max(ratios) - min(ratios)) / abs(sum(ratios) / len(ratios)) if ratios else None
    return {
        "status": "ok",
        "derived": True,
        "trial_dir": str(trial_dir),
        "sources": {"history": str(history), "history_sha256": sha256(history),
                    "solve_record": str(solve), "solve_record_sha256": sha256(solve)},
        "output": {"path": str(out_path), "sha256": sha256(out_path),
                   "columns": list(header) + list(HEADER_SUFFIX), "rows": len(out_rows) - 1},
        "basis": {"formula": "drag_coefficient_estimate = 2*Fx/(rho*U^2*A)",
                  "rho_volume_mean_kg_m3": rho, "inflow_speed_m_s": speed, "reference_area_m2": area,
                  "rho_sampling": "end-of-solve volume-averaged Density from the solve record "
                                  "(the pre-solve rho is not recoverable from existing artefacts)",
                  "constant_within_file": True},
        "verification": {
            "finite_coefficients": len(finite), "rows": len(coefficients),
            "last_coefficient_vs_official_relative_gap": relative_gap,
            "coefficient_over_force_relative_spread": spread,
            "collinearity_note": "Cd = Fx × constant within one file ⇒ the Cd series carries no "
                                 "independent information beyond the drag series; report it as a "
                                 "rescaled drag estimate, never as a second independent measurement",
        },
        "must_not_claim": ["solver-native history", "independent evidence for Cd",
                           "same rho basis as the macro's planned pre-solve rho"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument('trials', nargs='+', type=Path)
    parser.add_argument('--record', type=Path)
    args = parser.parse_args(argv)
    records = []
    for trial in args.trials:
        record = derive(trial)
        records.append(record)
        print("DERIVED|%s|rows=%d|gap=%s|spread=%s"
              % (record["output"]["path"], record["output"]["rows"],
                 record["verification"]["last_coefficient_vs_official_relative_gap"],
                 record["verification"]["coefficient_over_force_relative_spread"]))
    if args.record:
        args.record.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
                               encoding='utf-8')
    return 0


if __name__ == "__main__":
    sys.exit(main())
