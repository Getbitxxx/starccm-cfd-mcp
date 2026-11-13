# -*- coding: utf-8 -*-
"""迭代误差界的**可机械验证**推导（工程验证轨道 E 的共用前置能力）。

背景（为什么需要它）
--------------------
`test-evidence/static-release-candidate/remediation-20261008/research/prospective/gci/gci_applicability.py`
里的 `derive_iteration_error_bounds` 一直是**拒绝函数**：它机械读取窗口统计后返回
`supported=False / UNSUPPORTED`，因为"窗口均值 + 相对偏差 + 样本数"确实推不出受证上界。
数值准备度报告（`engineering-validation-start-20261009/numerical-readiness/`）要求：先由科学独审
确定一个**可机械验证**的方法、适用假设与真实证据，再接线到原 driver/consumer；方法不成立就保持
UNSUPPORTED，不拿网格预算赌通过。

本模块提供的就是那个"可机械验证的方法实现"。方法已由独立科学方**附条件签署**（C1~C15）；
本模块只负责把那些条件编译成可执行判据。

方法与判据（全部可由数据机械判定）
----------------------------------
对每个目标量的收敛尾部：

1. **形态条件**（逐条记录，便于审计）：尾部增量是否**同号**、|Δ| 是否**严格单调衰减**、
   是否落在**声明的噪声底**内、**留出段是否真的形成**。
2. `NO_OBSERVED_DRIFT`：窗口内增量**恰为 0**（目标量逐位不变）⇒ 只报"未观察到漂移"，
   `bound=0.0`，并明确标注这**不是**误差界。
3. `MEASURED_TAIL_DRIFT`：漂移在噪声底内 ⇒ 报**实测漂移幅值** `max|Δ|`，**不外推**，
   `bound_semantics="OBSERVED_DRIFT_AMPLITUDE"`。
4. `GEOMETRIC_EXTRAPOLATION`：同号 + 严格衰减 + 对数线性拟合 R² 达标 + **留出段预测达标**
   ⇒ 报 `|Δ_last|·r/(1-r)`，`bound_semantics="EMPIRICAL_ITERATION_ESTIMATE"`（经验估计，非界）。
5. 其余一律 `REFUSED`，带机器可读 `reason_key`，**不给数值界**。

诚实边界
--------
* `method_accepted` 只在"签署记录 sha == **可引用运行记录**里 pin 的 sha + 六个阈值逐项一致"时为 True：
  本模块实现方法，**不代表**方法已被工程或科学签收。
* 噪声底 `roundoff_rel_floor` 是**声明式假设**（不是从数据推导出来的），由调用方事前给出并原样
  回显；它只影响"是否报实测漂移"，不影响 `GEOMETRIC_EXTRAPOLATION` 的界。尺度取窗口内
  `max|value|`，因此它相对第一版（`max(|v_last|, min 非零|v|)`）**只会更宽松**（前者恒 ≤ 后者）。
  实测余量（真实夹具，声明的默认底 1e-10）：`mesh/coarse` 尾部漂移 1.5816070675356286e-11 N、
  量值 0.41609490386173953 ⇒ 相对 3.80107e-11，翻转点即该值；把底收到 1e-11 时该档由"报实测漂移"
  变为拒绝，`mesh/fine` 更小。两种尺度定义（`max|v|` 与 `max(|v_last|, min 非零|v|)`）在 coarse/fine 上
  **整条 floor 轴分类一致**（差异窄到浮点无法稳定命中），因此新定义的理由是"单一、可复算、不用最小值"，
  而非"改变了真实档位结论"（独立审查第三轮已撤回相反主张）。即：默认底**不**决定该档能否通过形态判定，
  只决定它报"漂移"还是"拒绝"。
* `row_binding`：目标量必须与同档**可引用记录**里的实测值**精确一致**，否则拒绝——
  防止"界绑到了另一个档的目标量"。
* 任一档拒绝 ⇒ 整体 `supported=False`（fail-closed）；空证据同样拒绝（不允许 0 档"全过"）。

consumer 接线语义（尚未接线，见返回值里的 `consumer_contract`）
--------------------------------------------------------------
现有 consumer（`gci_applicability.py`）pin 的是 `external_flow_validation.json`、要求
`drag_x_n` 与 `drag_coefficient_estimate` 两个目标、并自行校验 row↔record 绑定与档位角色。
本模块把这类校验内置，但**返回结构不等于可以直接替换**：接线前必须按 `consumer_contract`
逐项对齐（目标集合、档位角色、第 4 档 holdout 语义、`MEASURED_TAIL_DRIFT` 不得当误差传播项）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Mapping, Sequence

__all__ = ["derive_iteration_error_bounds", "Refusal", "REFUSAL_REASONS", "DEFAULT_TARGETS"]

# 默认只覆盖**求解历史导出里真实存在的列**：`external_flow_solve_history.csv` 的列是
# iteration / elapsed_seconds / drag_x_n / mass_imbalance，因此 drag_coefficient_estimate 等派生量
# 必须由调用方显式给 `target_columns`（否则按 COLUMN_MISSING 拒绝——不静默拿别的列顶替）。
DEFAULT_TARGETS = {"drag_x_n": "drag_x_n"}

#: C14（第三轮科学复验）：**模块内不得内嵌任何"签署记录 sha 白名单"**——那会造成互指而没有不动点：
#: 白名单含 sha(记录) ⇒ 模块字节变 ⇒ 记录里的 signed_module_sha256 失配 ⇒ 又要改记录 ⇒ 记录 sha 变 ⇒ …
#: 因此改由调用方传入 `signature_pin_record`（§2-O8 **可引用**的运行记录，内含 signature_sha256 与
#: signature_module_sha256），pin 的可信来源是内容寻址的运行记录，而不是调用方参数或模块常量。

#: 方法阈值集合。签署方（用户 2026-10-14）要求：**逐算例在 CaseSpec 中事前声明**，
#: 模块默认值只作建议，不得用于任何接受性判定（`require_declared_thresholds=True` 时强制）。
THRESHOLD_KEYS = ("window", "min_decay_samples", "holdout_fraction",
                  "max_holdout_rel_error", "min_r_squared", "roundoff_rel_floor")


def iteration_thresholds_from_case_spec(case_spec: Mapping) -> dict:
    """从 CaseSpec 里取四个（六个）方法阈值；缺项/非有限一律拒绝，不回落到默认值。"""
    block = case_spec.get("iteration_error_thresholds")
    if not isinstance(block, Mapping):
        raise Refusal("THRESHOLDS_NOT_DECLARED", "case_spec 缺 iteration_error_thresholds 块")
    missing = [key for key in THRESHOLD_KEYS if key not in block]
    if missing:
        raise Refusal("THRESHOLDS_NOT_DECLARED", "缺键: %s" % ",".join(missing))
    out = {}
    for key in THRESHOLD_KEYS:
        value = block[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise Refusal("THRESHOLDS_NOT_DECLARED", "%s=%r 非有限数值" % (key, value))
        out[key] = float(value)
    if not 0.0 < out["holdout_fraction"] < 1.0:
        raise Refusal("THRESHOLDS_NOT_DECLARED", "holdout_fraction 必须落在 (0,1)")
    if not 0.0 < out["min_r_squared"] <= 1.0:
        raise Refusal("THRESHOLDS_NOT_DECLARED", "min_r_squared 必须落在 (0,1]")
    # C2：签署方要求的下限必须编译进校验器，否则"逐算例声明"可以把方法削弱到低于签署强度。
    increments = int(out["window"]) - 1
    fit_tail = max(int(out["min_decay_samples"]),
                   int(math.ceil(increments * (1.0 - out["holdout_fraction"]))))
    if int(out["min_decay_samples"]) < 4 or fit_tail < 4:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED", "拟合点 %d < 4" % fit_tail)
    if increments - fit_tail < 2:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED",
                      "留出增量 %d < 2（window=%d, min_decay_samples=%d）"
                      % (increments - fit_tail, out["window"], out["min_decay_samples"]))
    if out["max_holdout_rel_error"] > 0.5:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED", "max_holdout_rel_error>0.5")
    if out["min_r_squared"] < 0.95:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED", "min_r_squared<0.95")
    if out["roundoff_rel_floor"] <= 0.0:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED", "roundoff_rel_floor<=0")
    # C12：底必须给依据且有上限——否则把底抬高就能把真实漂移吞成"不可分辨"（制造通过通道）。
    basis = block.get("roundoff_rel_floor_basis")
    if not isinstance(basis, str) or not basis.strip():
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED",
                      "缺 roundoff_rel_floor_basis：噪声底必须给出依据（C12）")
    if out["roundoff_rel_floor"] > 1e-8:
        raise Refusal("THRESHOLDS_WEAKER_THAN_SIGNED",
                      "roundoff_rel_floor=%.6g > 1e-8：过高的底会把真实漂移吞掉（C12）" % out["roundoff_rel_floor"])
    return out

REFUSAL_REASONS = {
    "EMPTY_EVIDENCE": "档数为 0 或历史证据为 0：空证据不得被报成通过",
    "THRESHOLDS_NOT_DECLARED": "六个方法阈值未在 CaseSpec 中事前声明（签署条件之一）：不得用模块默认值做接受性判定",
    "THRESHOLDS_WEAKER_THAN_SIGNED": "声明的阈值弱于独立科学签署的范围（C2）：拟合点<4、留出增量<2、max_holdout_rel_error>0.5 或 min_r_squared<0.95",
    "HOLDOUT_TOO_SMALL": "留出增量少于 2 个（C2）：留出验证不足以证伪",
    "DRIFT_BELOW_FLOOR_NOT_RESOLVED": "漂移在声明的噪声底内但余量不足 10 倍（C2）：只能报『不可分辨』，不得报成实测漂移",
    "SIGNATURE_SCOPE_MISMATCH": "传入的签署范围与本模块字节或声明阈值不一致：不得据此声称已接受",
    "REPORT_CONTEXT_MISSING": "缺少同档记录的窗口统计字段（C5）：不得单独上报迭代估计值",
    "EVIDENCE_NOT_PINNED": "证据缺 path/sha256/record，未绑定实际字节",
    "SOURCE_SHA_MISMATCH": "证据字节与声明的 sha256 不一致",
    "GATE_UNAVAILABLE": "§2-O8 判据不可用（未接上/导入失败）：未判定的记录不得放行",
    "RECORD_NOT_CITABLE": "同档记录未通过 §2-O8 的唯一判据（不可引用），拒绝为其给界",
    "HISTORY_MISSING": "缺少逐迭代历史文件",
    "HISTORY_UNPARSEABLE": "逐迭代历史不可解析",
    "COLUMN_MISSING": "历史里没有声明的目标量列",
    "NOT_ENOUGH_SAMPLES": "收敛尾部样本不足，无法判定形态",
    "HOLDOUT_NOT_FORMED": "留出段为空：留出验证会退化为空操作，拒绝",
    "NONFINITE_VALUE": "历史里出现非有限值",
    "ROW_BINDING_UNVERIFIED": "同档记录里没有该目标量的实测字段，无法绑定",
    "ROW_BINDING_MISMATCH": "level.row 的目标量与同档记录实测值不一致",
    "OSCILLATORY_OR_PLATEAU": "尾部增量同号性/单调性不成立（振荡或平台），拒绝外推",
    "DECAY_FIT_FAILED": "对数线性衰减拟合不达标，拒绝外推",
    "HOLDOUT_PREDICTION_FAILED": "留出段预测误差超限，拒绝外推",
}


class Refusal(Exception):
    """携带机器可读原因的拒绝。"""

    def __init__(self, reason_key: str, detail: str = "") -> None:
        if reason_key not in REFUSAL_REASONS:
            raise KeyError("unknown refusal reason: %s" % reason_key)
        self.reason_key = reason_key
        self.detail = detail
        super().__init__("%s%s" % (reason_key, (": " + detail) if detail else ""))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _default_citable(path: Path) -> Mapping:
    """按 §2-O8 走唯一判据；判据不可用与记录不可引用必须能区分。"""
    import sys

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import run_record_gate  # noqa: PLC0415 - 延迟导入，避免离线测试强依赖
    except Exception as exc:  # noqa: BLE001
        raise Refusal("GATE_UNAVAILABLE", "%s: %s" % (type(exc).__name__, exc)) from exc
    return run_record_gate.assert_citable(Path(path))


def _read_history(evidence_path: Path, column: str) -> list[float]:
    if not evidence_path.is_file():
        raise Refusal("HISTORY_MISSING", str(evidence_path))
    try:
        with evidence_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:  # noqa: BLE001 - 记录原因，不吞掉
        raise Refusal("HISTORY_UNPARSEABLE", "%s: %s" % (type(exc).__name__, exc)) from exc
    if not rows or column not in rows[0]:
        raise Refusal("COLUMN_MISSING", "column=%s columns=%s" % (column, list(rows[0]) if rows else []))
    values: list[float] = []
    for index, row in enumerate(rows):
        raw = row.get(column)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise Refusal("NONFINITE_VALUE", "row %d column %s = %r" % (index, column, raw)) from exc
        if not math.isfinite(value):
            raise Refusal("NONFINITE_VALUE", "row %d column %s = %r" % (index, column, raw))
        values.append(value)
    return values


def _increments(values: Sequence[float]) -> list[float]:
    return [values[i + 1] - values[i] for i in range(len(values) - 1)]


def _relative_floor(values: Sequence[float], increments: Sequence[float], roundoff_rel_floor: float) -> bool:
    """尾部漂移是否落在**声明的**噪声底内：`max|Δ| ≤ roundoff_rel_floor × max|value|`。

    尺度取窗口内目标量的**最大绝对值**（单一、可复算的定义；不使用分位数或最小值，
    避免"取最小值反而放大阈值"这种反直觉行为）。该阈值是声明式假设，由调用方事前给定并被
    原样回显；它只决定"是否只报实测漂移"，不影响几何外推的界。落在底内**不代表**离散误差、
    GCI 或工程精度已达标。
    """
    scale = max((abs(v) for v in values), default=0.0)
    if scale == 0.0:
        return all(inc == 0.0 for inc in increments)
    return all(abs(inc) <= roundoff_rel_floor * scale for inc in increments)


def _geometric_fit(sample_increments: Sequence[float]) -> tuple[float, float]:
    """对**给定样本**（调用方已切好的"留出段之前"那部分）的 |Δ| 做 log 线性最小二乘。

    这里**不得**自己再取末尾 N 个——否则拟合会吃掉留出段，留出验证就退化成同义反复
    （独立审查 M3 指出的缺陷）。样本切分由 `_bound_from_tail` 负责。
    """
    sample = [abs(v) for v in sample_increments]
    if any(v <= 0.0 for v in sample):
        raise Refusal("DECAY_FIT_FAILED", "尾部增量含 0，无法拟合衰减比")
    xs = list(range(len(sample)))
    ys = [math.log(v) for v in sample]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0.0:
        raise Refusal("DECAY_FIT_FAILED", "样本长度不足")
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0.0 else 0.0
    return math.exp(slope), r_squared


def _bound_from_tail(
    history: Sequence[float],
    *,
    window: int,
    min_decay_samples: int,
    holdout_fraction: float,
    max_holdout_rel_error: float,
    min_r_squared: float,
    roundoff_rel_floor: float,
) -> dict:
    """对单一目标的收敛尾部给出界或拒绝。所有阈值由调用方事前给定并回显。"""
    tail = list(history[-window:])
    if len(tail) < min_decay_samples + 1:
        raise Refusal("NOT_ENOUGH_SAMPLES", "tail=%d need>=%d" % (len(tail), min_decay_samples + 1))
    increments = _increments(tail)
    magnitude = [abs(v) for v in increments]
    diagnostics = {
        "window_samples": len(tail),
        "last_value": tail[-1],
        "last_increments": increments,
        "max_abs_increment": max(magnitude),
        "shape_conditions": {},
    }
    if all(inc == 0.0 for inc in increments):
        diagnostics["shape_conditions"] = {"all_increments_zero": True}
        return {
            "bound_kind": "NO_OBSERVED_DRIFT",
            "bound_semantics": "NO_OBSERVED_DRIFT_NOT_AN_ERROR_BOUND",
            "supported": True,
            "bound": 0.0,
            "extrapolated": False,
            "diagnostics": diagnostics,
            "note": "窗口内目标量逐位不变：只报『未观察到漂移』，bound=0.0 **不是**误差界",
        }
    floor_hit = _relative_floor(tail, increments, roundoff_rel_floor)
    signs = {value > 0.0 for value in increments if value != 0.0}
    same_sign = len(signs) == 1
    monotone_decreasing = all(magnitude[i + 1] < magnitude[i] for i in range(len(magnitude) - 1))
    diagnostics["shape_conditions"] = {
        "relative_floor_hit": floor_hit,
        "same_sign": same_sign,
        "monotone_decreasing": monotone_decreasing,
    }
    if floor_hit:
        scale = max((abs(v) for v in tail), default=0.0)
        relative_drift = (max(magnitude) / scale) if scale else 0.0
        if roundoff_rel_floor < 10.0 * relative_drift:
            # C2b：底与实测漂移余量不足 10 倍 ⇒ 只能说"不可分辨"，不能说"实测漂移"。
            return {
                "bound_kind": "REFUSED",
                "bound_semantics": "NONE",
                "supported": False,
                "reason_key": "DRIFT_BELOW_FLOOR_NOT_RESOLVED",
                "reason": ("%s (floor=%.6g, relative_drift=%.6g, margin=%.3gx)"
                           % (REFUSAL_REASONS["DRIFT_BELOW_FLOOR_NOT_RESOLVED"], roundoff_rel_floor,
                              relative_drift, roundoff_rel_floor / relative_drift if relative_drift else float("inf"))),
                "diagnostics": dict(diagnostics, relative_drift=relative_drift,
                                    floor_margin=(roundoff_rel_floor / relative_drift) if relative_drift else None),
            }
        return {
            "bound_kind": "MEASURED_TAIL_DRIFT",
            "bound_semantics": "OBSERVED_DRIFT_AMPLITUDE",
            "supported": True,
            "bound": max(magnitude),
            "extrapolated": False,
            "diagnostics": dict(diagnostics, relative_drift=relative_drift,
                                floor_margin=(roundoff_rel_floor / relative_drift) if relative_drift else None),
            "note": ("尾部漂移在声明的噪声底内：只报实测漂移幅值，**不外推**；"
                     "这不是迭代误差上界，也不代表离散误差或工程精度达标"),
        }
    if not same_sign or not monotone_decreasing:
        diagnostics["shape_conditions"]["refused_because"] = (
            "same_sign" if not same_sign else "monotone_decreasing")
        return {
            "bound_kind": "REFUSED",
            "bound_semantics": "NONE",
            "supported": False,
            "reason_key": "OSCILLATORY_OR_PLATEAU",
            "reason": REFUSAL_REASONS["OSCILLATORY_OR_PLATEAU"],
            "observed_amplitude": max(magnitude),
            "diagnostics": diagnostics,
        }
    fit_tail = max(min_decay_samples, int(math.ceil(len(increments) * (1.0 - holdout_fraction))))
    held_out = increments[fit_tail:]
    if not held_out:
        # 留出段为空会让"留出验证"变成空操作；这种配置一律拒绝，不静默放行。
        raise Refusal("HOLDOUT_NOT_FORMED",
                      "increments=%d fit_tail=%d window=%d" % (len(increments), fit_tail, window))
    if len(held_out) < 2:
        # C2：单个留出点不足以证伪几何尾部假设。
        raise Refusal("HOLDOUT_TOO_SMALL", "held_out=%d" % len(held_out))
    r, r_squared = _geometric_fit(increments[:fit_tail])
    if not (0.0 < r < 1.0) or r_squared < min_r_squared:
        return {
            "bound_kind": "REFUSED",
            "bound_semantics": "NONE",
            "supported": False,
            "reason_key": "DECAY_FIT_FAILED",
            "reason": "%s (r=%.6g R2=%.6g)" % (REFUSAL_REASONS["DECAY_FIT_FAILED"], r, r_squared),
            "diagnostics": dict(diagnostics, decay_ratio=r, r_squared=r_squared),
        }
    predicted = [magnitude[fit_tail - 1] * (r ** (k + 1)) for k in range(len(held_out))]
    rel_errors = [
        abs(magnitude[fit_tail + k] - predicted[k]) / max(magnitude[fit_tail + k], 1e-300)
        for k in range(len(held_out))
    ]
    if max(rel_errors) > max_holdout_rel_error:
        return {
            "bound_kind": "REFUSED",
            "bound_semantics": "NONE",
            "supported": False,
            "reason_key": "HOLDOUT_PREDICTION_FAILED",
            "reason": "%s (max_rel_error=%.6g)" % (
                REFUSAL_REASONS["HOLDOUT_PREDICTION_FAILED"], max(rel_errors)),
            "diagnostics": dict(diagnostics, decay_ratio=r, r_squared=r_squared,
                                holdout_relative_errors=rel_errors),
        }
    diagnostics = dict(diagnostics, decay_ratio=r, r_squared=r_squared,
                       holdout_relative_errors=rel_errors,
                       fit_tail=fit_tail, holdout_samples=len(held_out))
    # C11：窗内"最慢一步"的衰减比 = max(|Δ_{i+1}|/|Δ_i|)。旧实现写成 max|Δ|/|Δ_last|（动态范围，
    # 实测 309），会把 bound_at_window_max_ratio 变成 null。
    adjacent = [magnitude[i + 1] / magnitude[i] for i in range(len(magnitude) - 1) if magnitude[i] > 0.0]
    window_max_ratio = max(adjacent) if adjacent else None
    r_sensitivity = {
        "actual_r": r,
        "bound_at_actual_r": magnitude[-1] * r / (1.0 - r),
        "bound_at_r_0p5": magnitude[-1] * 0.5 / 0.5,
        "bound_at_r_0p7": magnitude[-1] * 0.7 / 0.3,
        "bound_at_r_0p9": magnitude[-1] * 0.9 / 0.1,
        "window_max_single_step_ratio_over_last": window_max_ratio,
        "bound_at_window_max_ratio": (magnitude[-1] * window_max_ratio / (1.0 - window_max_ratio)
                                      if window_max_ratio and window_max_ratio < 1.0 else None),
        "note": "r/(1-r) 敏感带：任何把该估计当'解析标尺'的用法都必须同时给出这条带（C4）",
    }
    return {
        "bound_kind": "GEOMETRIC_EXTRAPOLATION",
        "bound_semantics": "EMPIRICAL_ITERATION_ESTIMATE",
        "is_upper_bound": False,
        "a6_status": "UNVERIFIED_EXTRAPOLATION_BEYOND_WINDOW",
        "estimate_not_bound_note": "这是**经验估计**，不是界：A6（窗口之后的外推）无现有证据可证（独立科学签署 C1）",
        "supported": True,
        "bound": magnitude[-1] * r / (1.0 - r),
        "extrapolated": True,
        "r_sensitivity": r_sensitivity,
        "assumptions": [
            "尾部增量同号且严格单调衰减",
            "|Δ| 的对数对迭代步近似线性（R² ≥ %.3g）" % min_r_squared,
            "留出段预测相对误差 ≤ %.3g（留出段非空）" % max_holdout_rel_error,
        ],
        "diagnostics": diagnostics,
    }


def iteration_estimate_context(entry: Mapping, validation_record: Mapping, *,
                               target: str = "drag_x_n") -> dict:
    """C5：把经验估计与**同档记录**里已发生的尾部运动并列，缺字段即拒绝。

    签署方要求：任何使用处都必须同时给出 window 统计（mean / relative_deviation / samples / size）
    与 Δ_last、r、R²、留出误差，并写明与"已发生尾部运动"的比值——否则 1.47e-08 会被误读成
    对可观测运动的界。
    """
    required = ("drag_window_mean_n", "drag_window_relative_deviation",
                "drag_window_samples", "drag_window_size")
    missing = [key for key in required if key not in validation_record]
    if missing:
        raise Refusal("REPORT_CONTEXT_MISSING", "记录缺字段: %s" % ",".join(missing))
    estimate = entry.get("bound")
    diagnostics = dict(entry.get("diagnostics") or {})
    increments = [abs(v) for v in (diagnostics.get("last_increments") or [])]
    window_size = int(validation_record["drag_window_size"])
    span_increments = increments[-(window_size - 1):] if window_size > 1 else []
    observed_range = sum(span_increments) if span_increments else None
    value = validation_record.get("measured_%s" % target)
    relative_estimate = (abs(estimate) / abs(value)) if (estimate and value) else None
    relative_deviation = validation_record["drag_window_relative_deviation"]
    return {
        "target": target,
        "estimate_kind": entry.get("bound_kind"),
        "estimate": estimate,
        "estimate_is_upper_bound": False,
        "a6_status": entry.get("a6_status", "UNVERIFIED_EXTRAPOLATION_BEYOND_WINDOW"),
        "decay_ratio": diagnostics.get("decay_ratio"),
        "r_squared": diagnostics.get("r_squared"),
        "holdout_relative_errors": diagnostics.get("holdout_relative_errors"),
        "last_increment": increments[-1] if increments else None,
        "window_statistics": {key: validation_record[key] for key in required},
        "observed_tail_range_last_window": observed_range,
        "relative_estimate_to_target": relative_estimate,
        "observed_relative_deviation_in_record": relative_deviation,
        "ratio_relative_deviation_over_estimate": (
            relative_deviation / relative_estimate if (relative_deviation and relative_estimate) else None),
        "ratio_observed_range_over_estimate": (
            observed_range / abs(estimate) if (observed_range and estimate) else None),
        "r_sensitivity": entry.get("r_sensitivity"),
        "note": ("估计是模型给出的**剩余**量，记录里的偏差是**已发生**的尾部运动，两者并列而非互相替代 (C5)"),
    }


def _structured_binding(payload: object, pin_path: Path, pin_sha: str) -> tuple:
    """C19：内容寻址必须是**结构化条目**（含 name 与 sha256），不是自由文本子串命中。

    返回 (是否绑定, 命中的条目)。只看形如 {"name"/"file"/"path": …, "sha256": …} 的对象，
    并要求 name 的文件名与 pin 文件名一致。
    """
    hits = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "sha256" in node:
                name = node.get("name") or node.get("file") or node.get("path")
                hits.append({"name": name, "sha256": node.get("sha256")})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    for hit in hits:
        name = hit.get("name")
        if not name:
            continue
        if str(hit.get("sha256")) != pin_sha:
            continue
        if Path(str(name)).name == pin_path.name:
            return True, hit
    return False, None


def _bind_row(level: Mapping, target: str, record_payload: Mapping | None, value) -> None:
    """把界绑定到该档**记录里的实测值**：缺字段或不一致一律拒绝。"""
    key = (level.get("row_keys") or {}).get(target, "measured_%s" % target)
    if record_payload is None:
        raise Refusal("ROW_BINDING_UNVERIFIED", "record payload unavailable for %s" % target)
    if key not in record_payload:
        raise Refusal("ROW_BINDING_UNVERIFIED", "record lacks %s" % key)
    if value is None or record_payload[key] != value:
        raise Refusal("ROW_BINDING_MISMATCH",
                      "%s: row=%r record=%r" % (target, value, record_payload[key]))


def _load_record_payload(record_path: Path) -> Mapping | None:
    try:
        return json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 读不出即无法绑定，由 _bind_row 拒绝
        return None


def derive_iteration_error_bounds(
    evidence: Mapping,
    levels: Sequence[Mapping],
    *,
    target_columns: Mapping[str, str] | None = None,
    history_key: str = "solve_history_csv",
    window: int = 8,
    min_decay_samples: int = 5,
    holdout_fraction: float = 1.0 / 3.0,
    max_holdout_rel_error: float = 0.35,
    min_r_squared: float = 0.95,
    roundoff_rel_floor: float = 1e-10,
    thresholds: Mapping | None = None,
    require_declared_thresholds: bool = False,
    signature_record: Mapping | str | Path | None = None,
    signature_pin_record: str | Path | None = None,
    signature_pin_run_record: str | Path | None = None,
    expected_signature_sha256: str | None = None,
    assert_citable: Callable[[Path], Mapping] | None = None,
) -> dict:
    """从实际逐迭代历史推导目标量的迭代误差界（或按原因拒绝）。

    `levels` 每项须给 `trial_dir` 与 `row`（该档目标量实测值，用于把界绑定到该档实际值）。
    `evidence['iteration_error_source']['source_records']` 每项给历史文件 `{path, sha256}` 与
    同档可引用记录 `{record, record_sha256}`。
    """
    targets = dict(target_columns or DEFAULT_TARGETS)
    threshold_source = "module_default"
    if thresholds is not None:
        try:
            # C8：只接受 **CaseSpec 形状**（含 iteration_error_thresholds 块）。纯字典路径属签署范围外：
            # 它绕过全部下限，并会把 threshold_source 谎报成 case_spec_pre_declared。
            if "iteration_error_thresholds" not in thresholds:
                raise Refusal("THRESHOLDS_NOT_DECLARED",
                              "纯字典阈值路径属签署范围外（C8）：必须给 CaseSpec 的 iteration_error_thresholds 块")
            declared = iteration_thresholds_from_case_spec(thresholds)
            window = int(declared["window"])
            min_decay_samples = int(declared["min_decay_samples"])
            holdout_fraction = float(declared["holdout_fraction"])
            max_holdout_rel_error = float(declared["max_holdout_rel_error"])
            min_r_squared = float(declared["min_r_squared"])
            roundoff_rel_floor = float(declared["roundoff_rel_floor"])
            threshold_source = "case_spec_pre_declared"
        except Refusal as refusal:
            if require_declared_thresholds:
                return {"supported": False, "bounds": {target: None for target in targets},
                        "reason_key": refusal.reason_key, "reason": refusal.detail or str(refusal),
                        "per_level": [], "refusal_count": 0, "method_accepted": False,
                        "method_acceptance": "PENDING_SCIENTIFIC_REVIEW",
                        "threshold_source": "undeclared"}
            threshold_source = "module_default"
    elif require_declared_thresholds:
        return {"supported": False, "bounds": {target: None for target in targets},
                "reason_key": "THRESHOLDS_NOT_DECLARED",
                "reason": "require_declared_thresholds=True 但未提供 CaseSpec 阈值",
                "per_level": [], "refusal_count": 0, "method_accepted": False,
                "method_acceptance": "PENDING_SCIENTIFIC_REVIEW",
                "threshold_source": threshold_source}
    declared_values = {key: locals().get(key) for key in THRESHOLD_KEYS}
    source = dict(evidence.get("iteration_error_source") or {})
    records = list(source.get("source_records") or [])
    declared = {
        "window": window, "min_decay_samples": min_decay_samples,
        "holdout_fraction": holdout_fraction, "max_holdout_rel_error": max_holdout_rel_error,
        "min_r_squared": min_r_squared, "roundoff_rel_floor": roundoff_rel_floor,
    }
    base = {
        "method": "iteration_error_bound.geometric_tail_with_holdout",
        "method_accepted": False,
        "method_acceptance": "PENDING_SCIENTIFIC_REVIEW",
        "assumptions_declared": declared,
        "threshold_source": threshold_source,
        "consumer_contract": {
            "targets_required": sorted(DEFAULT_TARGETS),
            "extra_targets": "派生量（如 drag_coefficient_estimate）必须显式给 target_columns；求解历史里没有的列会被 COLUMN_MISSING 拒绝",
            "role_pinning": "每档必须给同档可引用记录（§2-O8 唯一判据）；顺序即档位顺序，不做重排",
            "row_binding": "level.row[target] 必须与该档记录里的 measured_<target> 精确一致",
            "fourth_level_holdout": "消费者若传入 holdout 档，本模块按普通档处理——holdout 语义仍需消费者显式声明",
            "bound_semantics": {
                "EMPIRICAL_ITERATION_ESTIMATE": "经验估计（非界）：可作为迭代误差项的候选值；方法已附条件签署",
                "OBSERVED_DRIFT_AMPLITUDE": "只是实测漂移幅值，**不得**当误差传播项",
                "NO_OBSERVED_DRIFT_NOT_AN_ERROR_BOUND": "逐位不变，bound=0.0 不是误差界",
                "NONE": "该档被拒绝，无界可用",
            },
            "bounds_length": "bounds[target] 的长度恒等于档数；若消费者硬要求 len==4，必须由调用方自己传满 4 档，本模块不补档",
            "zero_bound_caveat": "NO_OBSERVED_DRIFT 的 bound=0.0；只接受正数的消费者会拒绝它——这是预期行为，0.0 不得当误差界",
            "error_propagation_eligibility": ("只有 bound_semantics=EMPIRICAL_ITERATION_ESTIMATE 的条目可作迭代误差传播项；"
                                              "OBSERVED_DRIFT_AMPLITUDE / NO_OBSERVED_DRIFT_NOT_AN_ERROR_BOUND / NONE 一律不可"),
            "multi_target_ordering": "per_level 按 (档序, 目标序) 排列；bounds[target] 按档序。多目标时不得按 per_level 的线性顺序猜目标",
            "structural_enforcement": "以上均为结构约定，本模块不强制；接线方必须自行断言（当前无可执行门阻止误用）",
        },
        "reporting_requirements": {
            "eligibility_source": "只能逐档读 per_level 里 bound_kind=GEOMETRIC_EXTRAPOLATION 的条目，或 eligible_iteration_estimates[target][i]；禁止直接用 bounds[] 作资格判断 (C3)",
            "measured_and_zero_drift": "MEASURED_TAIL_DRIFT / NO_OBSERVED_DRIFT 在 eligible 里一律为 None：必须上报 NULL / NOT_ESTABLISHED，不得写成 0 (C3)",
            "juxtaposition": "任何上报必须调用 iteration_estimate_context()，并列给出 window 统计与 Δ_last/r/R²/留出误差及与已发生尾部运动的比值 (C5)",
            "yardstick_ban": "不得把该估计当作『网格差量是否被解析』的解析标尺，除非同时给出 r_sensitivity 敏感带 (C4)",
        },
        "note": ("本返回值只给出**经验估计**（非界）；A6 外推无现有证据可证，"
                 "不得据此宣称工程通过、受证上界或实验验证。"),
    }
    # 签署门控需要 §2-O8 判据来校验 pin 记录，故先把 gate 定好（此前该赋值在门控之后，会 UnboundLocal）。
    gate = assert_citable or _default_citable
    # 签署门控：只有在**签署记录的范围**与"本模块字节 + 本次使用的阈值"逐项一致、
    # 且 pin 来自可引用记录时，method_accepted 才为 True；否则保持 False。
    module_sha = _sha256(Path(__file__).resolve())
    base["method_accepted"] = False
    base["method_acceptance"] = "PENDING_SCIENTIFIC_REVIEW"
    base["signed_scope"] = {"module_sha256": module_sha,
                            "thresholds_used": {key: declared_values.get(key) for key in THRESHOLD_KEYS}
                            if 'declared_values' in dir() else None}
    if signature_record is not None:
        try:
            problems = []
            scope, signed_thresholds = {}, {}
            base["pin_source"] = None
            # C9/C14：只接受**文件路径**（拒绝内存 dict）；pin 必须来自**可引用运行记录**，
            # 不接受调用方直接给的裸 sha（那等于自签）。
            if not isinstance(signature_record, (str, Path)):
                problems.append("signature_record 必须是文件路径（拒绝内存 dict，C9）")
            else:
                sig_path = Path(signature_record)
                sig_sha = _sha256(sig_path) if sig_path.is_file() else None
                if sig_sha is None:
                    problems.append("签署记录文件不存在：%s" % sig_path)
                pinned = None
                if signature_pin_record is not None:
                    pin_path = Path(signature_pin_record)
                    base["pin_source"] = "citable_record:%s" % pin_path
                    try:
                        gate(Path(pin_path))  # §2-O8 唯一判据
                    except Refusal:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        problems.append("pin 记录未通过 §2-O8 判据（%s: %s）" % (type(exc).__name__, exc))
                    try:
                        pin_payload = json.loads(pin_path.read_text(encoding="utf-8"))
                    except Exception as exc:  # noqa: BLE001
                        pin_payload = {}
                        problems.append("pin 记录不可解析：%s" % exc)
                    pinned = pin_payload.get("signature_sha256")
                    pin_module = pin_payload.get("signature_module_sha256")
                    if pinned != sig_sha:
                        problems.append("pin（%s…）≠ 签署记录实际 sha（%s…）"
                                        % (str(pinned)[:12] or "?", (sig_sha or "?")[:12]))
                    # C17：缺 signature_module_sha256 时旧的 `if pin_module and ...` 会跳过交叉校验。
                    if not isinstance(pin_module, str) or len(pin_module) != 64:
                        problems.append("pin 记录缺 signature_module_sha256（C17）：必须逐字比对模块字节")
                    elif pin_module != module_sha:
                        problems.append("pin 记录里的模块 sha（%s…）≠ 本模块 sha（%s…）"
                                        % (pin_module[:12], module_sha[:12]))
                    # C16①：pin 必须**内容寻址**到运行记录（stage-*.json / case-probe.json 等）并被引用，
                    # 且该校验只能用**默认** §2-O8 判据完成（C16③：调用方覆盖判据即可关掉校验）。
                    if signature_pin_run_record is None:
                        problems.append("缺 signature_pin_run_record：pin 必须内容寻址到运行记录（C16①）")
                    else:
                        run_path = Path(signature_pin_run_record)
                        # C20：运行记录必须是**常规文件**（拒符号链接）且与 pin 同位置（同一目录或其祖先）。
                        if run_path.is_symlink():
                            problems.append("运行记录是符号链接：拒绝（C20）")
                        if not run_path.is_file():
                            problems.append("运行记录不是常规文件：拒绝（C20）")
                        try:
                            pin_parent = pin_path.resolve().parent
                            run_resolved = run_path.resolve()
                            if pin_parent != run_resolved.parent and pin_parent not in run_resolved.parents:
                                problems.append("运行记录与 pin 不在同一位置（同目录或其祖先）：拒绝（C20）")
                        except Exception:  # noqa: BLE001
                            problems.append("无法解析 pin/运行记录的位置关系（C20）")
                        try:
                            _default_citable(run_path)  # 强制默认判据，不用调用方注入的 gate
                        except Refusal:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            problems.append("pin 的运行记录未通过默认 §2-O8 判据（%s: %s）"
                                            % (type(exc).__name__, exc))
                        # 运行记录必须**结构化**内容寻址 pin 记录本身的 sha256（pin 里再声明签署记录的 sha）。
                        pin_sha = _sha256(pin_path) if pin_path.is_file() else None
                        base["pin_record_sha256"] = pin_sha
                        base["pin_run_record_sha256"] = (_sha256(run_path) if run_path.is_file() else None)
                        try:
                            run_payload = json.loads(run_path.read_text(encoding="utf-8"))
                        except Exception:  # noqa: BLE001
                            run_payload = None
                        bound, hit = (_structured_binding(run_payload, pin_path, pin_sha)
                                      if (run_payload is not None and pin_sha) else (False, None))
                        if not bound:
                            problems.append("运行记录里没有**结构化**绑定该 pin（需 name==pin 文件名 + sha256==pin sha）（C19）")
                        else:
                            base["pin_binding_entry"] = hit
                        base["pin_source"] = "structured_content_addressed_run_record:%s" % run_path
                elif expected_signature_sha256 is not None:
                    base["pin_source"] = "caller_argument_not_for_production"
                    problems.append("裸 expected_signature_sha256 属离线/测试用途，不得据以声称已接受（C14/A）")
                else:
                    problems.append("缺 signature_pin_record：pin 必须来自可引用运行记录（C14/A）")
                record = (json.loads(sig_path.read_text(encoding="utf-8")) if sig_sha else {})
                scope = record.get("independent_scientific") or {}
                signed_thresholds = scope.get("signed_thresholds") or {}
                signed_sha = str(scope.get("signed_module_sha256") or "")
                if signed_sha != module_sha:
                    problems.append("签署 sha256(%s) ≠ 本模块 sha256(%s)"
                                    % (signed_sha[:12] or "?", module_sha[:12]))
            if signature_pin_record is not None and assert_citable is not None:
                problems.append("调用方覆盖了 §2-O8 判据：pin 校验可被一个参数关掉（C16③），"
                                "该结果只能作离线诊断，不得声称已接受")
            if scope.get("approved") is not True:
                problems.append("independent_scientific.approved 非 true")
            missing = [key for key in THRESHOLD_KEYS if key not in signed_thresholds]
            if missing:
                problems.append("签署记录缺阈值键（C9）：%s" % ",".join(missing))
            else:
                for key, value in base["signed_scope"]["thresholds_used"].items():
                    if float(signed_thresholds[key]) != float(value):
                        problems.append("阈值 %s 与签署不一致：%r ≠ %r" % (key, value, signed_thresholds[key]))
            if problems:
                base["method_acceptance"] = "SIGNATURE_SCOPE_MISMATCH"
                base["signature_problems"] = problems
            else:
                base["method_accepted"] = True
                base["method_acceptance"] = "ACCEPTED_WITH_RESTRICTIONS"
                base["signature_conditions"] = list(scope.get("conditions") or [])
        except Exception as exc:  # noqa: BLE001 - 读不出签署记录即视为未签署
            base["method_acceptance"] = "SIGNATURE_SCOPE_MISMATCH"
            base["signature_problems"] = ["%s: %s" % (type(exc).__name__, exc)]
    if not levels or not records:
        return dict(base, supported=False, bounds={target: None for target in targets},
                    reason_key="EMPTY_EVIDENCE", reason=REFUSAL_REASONS["EMPTY_EVIDENCE"],
                    per_level=[], refusal_count=0)
    if len(records) != len(levels):
        return dict(base, supported=False, bounds={target: None for target in targets},
                    reason_key="EVIDENCE_NOT_PINNED",
                    reason="历史证据数量(%d)与档数(%d)不一致" % (len(records), len(levels)),
                    per_level=[], refusal_count=len(levels))
    gate = assert_citable or _default_citable
    per_level: list[dict] = []
    bounds: dict[str, list] = {target: [] for target in targets}
    for index, (record, level) in enumerate(zip(records, levels)):
        path = Path(str(record.get("path") or ""))
        declared_history = str(record.get("sha256") or "")
        record_path = Path(str(record.get("record") or ""))
        declared_record = str(record.get("record_sha256") or "")
        payload = None
        try:
            if len(declared_history) != 64:
                raise Refusal("EVIDENCE_NOT_PINNED", "history sha256=%r" % declared_history)
            # 先判存在性再比 sha：否则"文件不存在"会被报成 SOURCE_SHA_MISMATCH，
            # HISTORY_MISSING 成为不可达的死词条（独立审查 D6）。
            if not path.is_file():
                raise Refusal("HISTORY_MISSING", str(path))
            if _sha256(path) != declared_history:
                raise Refusal("SOURCE_SHA_MISMATCH", "history %s" % path)
            if len(declared_record) != 64 or not record_path.is_file():
                raise Refusal("EVIDENCE_NOT_PINNED", "record=%r" % declared_record)
            if _sha256(record_path) != declared_record:
                raise Refusal("SOURCE_SHA_MISMATCH", "record %s" % record_path)
            try:
                gate(record_path)
            except Refusal:
                raise
            except Exception as exc:  # noqa: BLE001 - 门抛异常即"不可引用"，必须失败关闭
                raise Refusal("RECORD_NOT_CITABLE",
                              "%s: %s" % (type(exc).__name__, exc)) from exc
            payload = _load_record_payload(record_path)
        except Refusal as refusal:
            for target in targets:
                per_level.append({"target": target, "level_index": index,
                                  "trial_dir": str(level.get("trial_dir") or ""),
                                  "path": str(path), "record": str(record_path), "bound_kind": "REFUSED",
                                  "bound_semantics": "NONE", "supported": False,
                                  "reason_key": refusal.reason_key, "reason": refusal.detail or str(refusal)})
                bounds[target].append(None)
            continue
        for target, column in targets.items():
            entry: dict = {"target": target, "level_index": index,
                           "trial_dir": str(level.get("trial_dir") or ""), "path": str(path),
                           "record": str(record_path)}
            try:
                value = (level.get("row") or {}).get(target)
                _bind_row(level, target, payload, value)
                history = _read_history(path, column)
                outcome = _bound_from_tail(
                    history, window=window, min_decay_samples=min_decay_samples,
                    holdout_fraction=holdout_fraction, max_holdout_rel_error=max_holdout_rel_error,
                    min_r_squared=min_r_squared, roundoff_rel_floor=roundoff_rel_floor)
            except Refusal as refusal:
                outcome = {"bound_kind": "REFUSED", "bound_semantics": "NONE", "supported": False,
                           "reason_key": refusal.reason_key, "reason": refusal.detail or str(refusal)}
            entry.update(outcome)
            entry["measured_target_value"] = (level.get("row") or {}).get(target)
            per_level.append(entry)
            bounds[target].append(outcome.get("bound"))
    refusals = [entry for entry in per_level if entry.get("bound_kind") == "REFUSED"]
    supported = not refusals
    # C3：资格口径必须**逐档从 per_level 取**，不得直接用 bounds[]；只有 EXTRAPOLATED 条目有值，
    # MEASURED_TAIL_DRIFT / NO_OBSERVED_DRIFT / REFUSED 一律为 None（不是 0，也不是该档数字）。
    eligible = {target: [None] * len(levels) for target in targets}
    for entry in per_level:
        if entry.get("bound_kind") == "GEOMETRIC_EXTRAPOLATION" and entry.get("supported"):
            eligible[entry["target"]][entry["level_index"]] = entry.get("bound")
    return dict(base, supported=supported, eligible_iteration_estimates=eligible,
                bounds=bounds if supported else {target: [None] * len(levels) for target in targets},
                per_level=per_level, refusal_count=len(refusals),
                reason=None if supported else "; ".join(
                    "%s[%d]=%s" % (entry["target"], entry["level_index"], entry.get("reason_key"))
                    for entry in refusals))
