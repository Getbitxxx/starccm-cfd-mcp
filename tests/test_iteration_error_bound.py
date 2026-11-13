# -*- coding: utf-8 -*-
"""`scripts/iteration_error_bound.py` 的可执行验收（§2-O9）。

夹具分两类：

* **真实夹具**：① 的 R3 工程执行档求解历史（`external_flow_solve_history.csv`）——
  `domain/d1p25` 真实几何衰减、`mesh/coarse|fine` 真实漂移到噪声底、`domain/d1p50` 真实符号翻转；
* **合成夹具**：仅为单独检验某一条判据而构造（形态/留出/R²/噪声底/绑定），
  构造方式在每条测试里写明；行绑定仍指向**真实**可引用记录，避免"合成数据自证合成结论"。

本文件同时是独立审查（`test-evidence/engineering-validation-start-20261009/iteration-error-provider-review/`）
的回归：审查点名的 1 条 blocking 与 13 个变异存活点各有一条测试钉住。
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from iteration_error_bound import (  # noqa: E402
    Refusal,
    derive_iteration_error_bounds,
    iteration_estimate_context,
    iteration_thresholds_from_case_spec,
)

EXEC = ROOT / "test-evidence/static-release-candidate/engineering-execution-20261007"
LEVELS = {
    "coarse": EXEC / "mesh/coarse/static-eng-mesh-coarse-20261008-s1",
    "fine": EXEC / "mesh/fine/static-eng-mesh-fine-20261008-s1",
    "d1p25": EXEC / "domain/d1p25/static-eng-domain-d1p25-20261008-s1",
    "d1p50": EXEC / "domain/d1p50/static-eng-domain-d1p50-20261008-s1",
}
REAL_RECORD = LEVELS["d1p25"] / "external_flow_validation.json"
SIGNATURE_RECORD = (ROOT / "test-evidence/engineering-validation-start-20261009/"
                    "METHOD-ACCEPTANCE-SIGNATURE.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def real_level(name: str) -> dict:
    payload = json.loads((LEVELS[name] / "external_flow_validation.json").read_text(encoding="utf-8"))
    return {"trial_dir": str(LEVELS[name]),
            "row": {"drag_x_n": payload["measured_drag_x_n"],
                    "drag_coefficient_estimate": payload["measured_drag_coefficient_estimate"]}}


def real_record(name: str, *, tamper_record_sha: bool = False) -> dict:
    history = LEVELS[name] / "external_flow_solve_history.csv"
    record = LEVELS[name] / "external_flow_validation.json"
    return {"path": str(history), "sha256": sha256(history),
            "record": str(record),
            "record_sha256": ("0" * 64) if tamper_record_sha else sha256(record)}


def build_real(names, **kwargs):
    records = [real_record(name, **kwargs) for name in names]
    return ({"iteration_error_source": {"source_records": records}},
            [real_level(name) for name in names],
            (lambda path: {"state": "CITABLE", "stub": True}))


REAL_ROW = {"drag_x_n": json.loads(REAL_RECORD.read_text(encoding="utf-8"))["measured_drag_x_n"]}


def write_history(directory: Path, values, column: str = "drag_x_n") -> Path:
    path = directory / ("history-%s.csv" % hashlib.sha256(repr(values).encode()).hexdigest()[:12])
    lines = ["iteration,elapsed_seconds,%s,mass_imbalance" % column]
    for index, value in enumerate(values):
        lines.append("%d,%.3f,%.17g,0.0" % (50 * (index + 1), float(index), value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def synthetic(directory: Path, values, *, column: str = "drag_x_n", row=None,
              tamper_history_sha: bool = False):
    path = write_history(directory, values, column)
    record = {"path": str(path), "sha256": ("0" * 64) if tamper_history_sha else sha256(path),
              "record": str(REAL_RECORD), "record_sha256": sha256(REAL_RECORD)}
    level = {"trial_dir": str(LEVELS["d1p25"]), "row": dict(row or REAL_ROW)}
    return {"iteration_error_source": {"source_records": [record]}}, [level]


def cumulative(increments, start=1.0):
    values = [start]
    for increment in increments:
        values.append(values[-1] + increment)
    return values


class RealFixtureBehaviour(unittest.TestCase):
    """真实夹具：真实几何尾部给界、真实噪声底报漂移、真实振荡拒绝。"""

    def test_geometric_real_level_gives_a_bound(self):
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertTrue(result["supported"], result.get("reason"))
        entry = result["per_level"][0]
        self.assertEqual(entry["bound_kind"], "GEOMETRIC_EXTRAPOLATION")
        self.assertEqual(entry["bound_semantics"], "EMPIRICAL_ITERATION_ESTIMATE")
        self.assertFalse(entry["is_upper_bound"], "科学签署 C1：只能叫经验估计，不能叫界")
        self.assertEqual(entry["a6_status"], "UNVERIFIED_EXTRAPOLATION_BEYOND_WINDOW")
        self.assertTrue(1e-9 < entry["bound"] < 1e-7)

    def test_noise_floor_margin_is_enforced(self):
        """C2b（科学签署）：报"实测漂移"要求底的余量 ≥10×实测相对漂移，否则只能报"不可分辨"。

        coarse/fine 实测相对漂移 3.80107e-11，签署的默认底 1e-10 只有 2.63× ⇒ 按 C2b 必须改判；
        把底提到 1e-9（26×）才允许报 MEASURED_TAIL_DRIFT。
        """
        evidence, levels, gate = build_real(["coarse", "fine"])
        at_signed_default = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(at_signed_default["supported"], "coarse 未达标 ⇒ 整体 fail-closed")
        coarse = at_signed_default["per_level"][0]
        self.assertEqual(coarse["reason_key"], "DRIFT_BELOW_FLOOR_NOT_RESOLVED")
        self.assertNotIn("bound", coarse)
        self.assertGreater(coarse["diagnostics"]["floor_margin"], 1.0)
        self.assertLess(coarse["diagnostics"]["floor_margin"], 10.0)
        # fine 的漂移更小（~3e-15 相对），余量远超 10× ⇒ 仍可报实测漂移
        fine = at_signed_default["per_level"][1]
        self.assertEqual(fine["bound_kind"], "MEASURED_TAIL_DRIFT")
        self.assertGreaterEqual(fine["diagnostics"]["floor_margin"], 10.0)
        resolved = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                                 roundoff_rel_floor=1e-9)
        self.assertTrue(resolved["supported"], resolved.get("reason"))
        for entry in resolved["per_level"]:
            self.assertEqual(entry["bound_kind"], "MEASURED_TAIL_DRIFT")
            self.assertEqual(entry["bound_semantics"], "OBSERVED_DRIFT_AMPLITUDE")
            self.assertGreaterEqual(entry["diagnostics"]["floor_margin"], 10.0)

    def test_oscillatory_real_level_is_refused(self):
        evidence, levels, gate = build_real(["d1p50"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(result["supported"])
        entry = result["per_level"][0]
        self.assertEqual(entry["reason_key"], "OSCILLATORY_OR_PLATEAU")
        self.assertFalse(entry["diagnostics"]["shape_conditions"]["same_sign"])

    def test_real_gate_is_wired_by_default(self):
        evidence, levels, _ = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, levels)  # 真实 run_record_gate
        self.assertTrue(result["supported"], result.get("reason"))
        self.assertEqual(result["per_level"][0]["bound_kind"], "GEOMETRIC_EXTRAPOLATION")


class EmptyEvidenceIsRefused(unittest.TestCase):
    """独立审查的 blocking：空证据曾被报成 supported=True。"""

    def test_no_records_and_no_levels_is_refused(self):
        result = derive_iteration_error_bounds(
            {"iteration_error_source": {"source_records": []}}, [], assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "EMPTY_EVIDENCE")
        self.assertIsNone(result["bounds"]["drag_x_n"])
        self.assertNotIn("[]", json.dumps(result["bounds"]))

    def test_records_without_levels_is_refused(self):
        evidence, _, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, [], assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "EMPTY_EVIDENCE")

    def test_levels_without_records_is_refused(self):
        result = derive_iteration_error_bounds(
            {"iteration_error_source": {"source_records": []}}, [real_level("d1p25")],
            assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "EMPTY_EVIDENCE")


class BoundArithmeticIsPinned(unittest.TestCase):
    """审查 M4/M5/M13：界必须等于独立复算值、拒绝不得携带数值界、0 漂移不得当误差界。"""

    def test_bound_equals_independent_arithmetic(self):
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        increments = [abs(v) for v in entry["diagnostics"]["last_increments"]]
        sample = increments[:entry["diagnostics"]["fit_tail"]]
        xs = list(range(len(sample)))
        ys = [math.log(v) for v in sample]
        n = len(xs)
        mean_x, mean_y = sum(xs) / n, sum(ys) / n
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
        r = math.exp(slope)
        expected = increments[-1] * r / (1.0 - r)
        self.assertLess(abs(entry["bound"] - expected) / expected, 1e-12)
        # 与"错公式"（末增量本身）必须显著不同，测试才不会两式都通过：
        # 本夹具 r≈0.386 ⇒ bound=|Δ|·r/(1-r)≈0.63|Δ|，与 |Δ| 相差约 37%
        self.assertGreater(abs(entry["bound"] - increments[-1]) / increments[-1], 0.3)

    def test_bound_value_after_the_round2_fix_is_pinned(self):
        """D1（独立审查证伪了父代理"界不变"的说法）：修复"拟合吃留出段"后界**变了**。

        第一版（用最后 5 个增量拟合）：1.4889448598379003e-08，r=0.386081573018；
        修复后（只用留出段之前的 5 个增量）：1.4699741160721603e-08，r=0.383046723986（+1.2741%）。
        本测试钉住**修复后**的值：任何下游引用旧值都必须重新取数。
        """
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        self.assertAlmostEqual(entry["bound"], 1.4699741160721603e-08, delta=1e-21)
        self.assertAlmostEqual(entry["diagnostics"]["decay_ratio"], 0.383046723986, delta=1e-11)
        self.assertNotAlmostEqual(entry["bound"], 1.4889448598379003e-08, delta=1e-15)

    def test_refused_entry_has_no_numeric_bound(self):
        evidence, levels, gate = build_real(["d1p50", "d1p25"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        refused = [entry for entry in result["per_level"] if entry["bound_kind"] == "REFUSED"]
        self.assertTrue(refused)
        for entry in refused:
            self.assertNotIn("bound", entry)
            self.assertEqual(entry["bound_semantics"], "NONE")
        self.assertEqual(result["bounds"]["drag_x_n"], [None, None])

    def test_every_refusal_path_carries_no_bound_and_none_semantics(self):
        """D3/M25：原测试只走"形态拒绝"一条路；门失败与行绑定失败也必须无界、语义 NONE。"""
        paths = {}
        # (a) 形态拒绝
        evidence, levels, gate = build_real(["d1p50"])
        paths["shape"] = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        # (b) 门失败
        evidence_b, levels_b, _ = build_real(["d1p25"])
        paths["gate"] = derive_iteration_error_bounds(
            evidence_b, levels_b, assert_citable=lambda path: (_ for _ in ()).throw(RuntimeError("no")))
        # (c) 行绑定失败
        evidence_c, levels_c, gate_c = build_real(["d1p25"])
        levels_c[0]["row"]["drag_x_n"] = 12345.0
        paths["row_binding"] = derive_iteration_error_bounds(evidence_c, levels_c, assert_citable=gate_c)
        # (d) 留出段为空
        evidence_d, levels_d, gate_d = build_real(["d1p25"])
        paths["holdout"] = derive_iteration_error_bounds(evidence_d, levels_d, assert_citable=gate_d,
                                                         window=8, min_decay_samples=7)
        for name, result in paths.items():
            self.assertFalse(result["supported"], name)
            entry = result["per_level"][0]
            self.assertEqual(entry["bound_kind"], "REFUSED", name)
            self.assertEqual(entry["bound_semantics"], "NONE", name)
            self.assertNotIn("bound", entry, name)
            self.assertEqual(result["bounds"]["drag_x_n"], [None], name)  # 逐档 None，避免下游 KeyError

    def test_constant_history_is_not_an_error_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), [0.3] * 9)
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertTrue(result["supported"], result.get("reason"))
        entry = result["per_level"][0]
        self.assertEqual(entry["bound_kind"], "NO_OBSERVED_DRIFT")
        self.assertEqual(entry["bound"], 0.0)
        self.assertEqual(entry["bound_semantics"], "NO_OBSERVED_DRIFT_NOT_AN_ERROR_BOUND")


class HoldoutIsFalsifiable(unittest.TestCase):
    """审查 M1/C2/C7：留出验证必须能被证伪，留出段为空必须拒绝。"""

    def test_non_geometric_held_out_tail_is_refused(self):
        # 前 5 个增量按 ~0.5 衰减，留出段却"抬头"（2.5e-5、2.4e-5），几何预测必然失败
        magnitudes = [1e-3, 5e-4, 2.5e-4, 1.25e-4, 6.25e-5, 3.1e-5, 2.5e-5, 2.4e-5]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(magnitudes))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "HOLDOUT_PREDICTION_FAILED")
        self.assertGreater(max(result["per_level"][0]["diagnostics"]["holdout_relative_errors"]), 0.35)

    def test_empty_holdout_is_refused_instead_of_silently_passing(self):
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               window=8, min_decay_samples=7)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "HOLDOUT_NOT_FORMED")

    def test_holdout_sensitivity_is_live(self):
        """把留出容差收紧到 0，真实 d1p25 也必须被拒绝——证明该判据真的在参与判定。"""
        evidence, levels, gate = build_real(["d1p25"])
        strict = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               max_holdout_rel_error=0.0)
        self.assertFalse(strict["supported"])
        self.assertEqual(strict["per_level"][0]["reason_key"], "HOLDOUT_PREDICTION_FAILED")


class ThresholdsAreLiveNotDecorative(unittest.TestCase):
    """审查 M2/M8/M17/M18：R² 门槛与噪声底必须真的改变判定，默认值必须被钉住。"""

    def test_r_squared_is_reductive_when_the_holdout_already_passes(self):
        """D2（独立审查用反例证伪了父代理原来的论证）：这个真实可复现夹具里**留出已通过**
        （max_rel=0.252212 ≤ 0.35），仅 R²=0.800073 < 0.95 就足以拒绝；把门槛放到 0.5 则通过。

        夹具来自审查者 60k 组随机搜索（`iteration-error-provider-review/_work/r2_counterexample.json`）：
        "R² 中等且留出通过"并不罕见，因此 R² 门槛是**削减性**判据，不是装饰。
        """
        magnitudes = [3.1634742997770635, 0.20380936317928972, 0.17894767847395907, 0.13007564944740943,
                      0.04834462541148913, 0.02678573028188206, 0.008498532578976646]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(magnitudes))
            default = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
            loose = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {},
                                                  min_r_squared=0.5)
        entry = default["per_level"][0]
        self.assertFalse(default["supported"])
        self.assertEqual(entry["reason_key"], "DECAY_FIT_FAILED")
        self.assertAlmostEqual(entry["diagnostics"]["r_squared"], 0.800073, delta=5e-4)
        self.assertTrue(loose["supported"], loose.get("reason"))
        self.assertLessEqual(max(loose["per_level"][0]["diagnostics"]["holdout_relative_errors"]), 0.35)

    def test_r_squared_threshold_participates_in_the_verdict(self):
        """R² 门槛必须真的参与判定：把门槛抬到真实尾部拟合质量之上，正确档也必须被拒。

        （说明：本方法里"同号 + 严格单调 + 留出预测达标"的尾部，其 R² 实测总在 0.99 以上，
        因此不存在"R² 中等但仍通过留出"的夹具；用抬高门槛的方式证明该判据在参与判定，
        而不是造一个不真实的合成尾部来假装它会被触发。）
        """
        evidence, levels, gate = build_real(["d1p25"])
        default = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        strict = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               min_r_squared=0.9999999)
        r_squared = default["per_level"][0]["diagnostics"]["r_squared"]
        self.assertGreater(r_squared, 0.99)
        self.assertTrue(default["supported"], default.get("reason"))
        self.assertFalse(strict["supported"])
        self.assertEqual(strict["per_level"][0]["reason_key"], "DECAY_FIT_FAILED")

    def test_noise_floor_changes_classification(self):
        """签署后的分类：1e-9（26× 余量）→ 实测漂移；1e-10（2.63×）→ 不可分辨；1e-12 → 形态拒绝。"""
        evidence, levels, gate = build_real(["coarse"])
        resolved = derive_iteration_error_bounds(evidence, levels, assert_citable=gate, roundoff_rel_floor=1e-9)
        unresolved = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        refused = derive_iteration_error_bounds(evidence, levels, assert_citable=gate, roundoff_rel_floor=1e-12)
        self.assertEqual(resolved["per_level"][0]["bound_kind"], "MEASURED_TAIL_DRIFT")
        self.assertEqual(unresolved["per_level"][0]["reason_key"], "DRIFT_BELOW_FLOOR_NOT_RESOLVED")
        self.assertEqual(refused["per_level"][0]["bound_kind"], "REFUSED")

    def test_noise_floor_flip_boundary_is_pinned(self):
        """钉住噪声底的实测翻转边界。

        事实（双方实测一致，审查者第三轮已**撤回**其"1e-12 会翻成漂移"的文字结论——那是把
        默认值 1e-10 误写成 1e-12，其扫描表数据本身正确）：coarse 窗口内 7 个增量最大
        1.5816070675356286e-11 N，量值 0.41609490386173953 ⇒ 相对 3.80107e-11。
        故 floor=1e-11（阈值 4.16e-12）→ 形态拒绝；签署 C2b 又要求 MEASURED 的余量 ≥10×，
        于是 floor=1e-10（2.63×）→ 不可分辨、floor≥3.801072911148094e-10（10×）→ MEASURED。
        两种尺度定义（max|v| 与 max(|v_last|, min 非零|v|)）在 coarse/fine 上整条 floor 轴分类一致，
        差异仅落在浮点无法稳定命中的窄窗内——因此新定义的正当理由是"单一、可复算、不用最小值"，
        而不是"改变了真实档位结论"。
        """
        evidence, levels, gate = build_real(["coarse"])
        just_below = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                                   roundoff_rel_floor=3.8e-10)
        just_above = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                                   roundoff_rel_floor=3.9e-10)
        at_1e11 = derive_iteration_error_bounds(evidence, levels, assert_citable=gate, roundoff_rel_floor=1e-11)
        at_1e12 = derive_iteration_error_bounds(evidence, levels, assert_citable=gate, roundoff_rel_floor=1e-12)
        self.assertEqual(just_below["per_level"][0]["reason_key"], "DRIFT_BELOW_FLOOR_NOT_RESOLVED")
        self.assertEqual(just_above["per_level"][0]["bound_kind"], "MEASURED_TAIL_DRIFT")
        self.assertEqual(at_1e11["per_level"][0]["bound_kind"], "REFUSED")
        self.assertEqual(at_1e12["per_level"][0]["bound_kind"], "REFUSED")

    def test_declared_defaults_are_pinned(self):
        evidence, levels, gate = build_real(["d1p25"])
        declared = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["assumptions_declared"]
        self.assertEqual(declared["roundoff_rel_floor"], 1e-10)
        self.assertEqual(declared["min_r_squared"], 0.95)
        self.assertEqual(declared["max_holdout_rel_error"], 0.35)
        self.assertEqual(declared["min_decay_samples"], 5)
        self.assertEqual(declared["window"], 8)

    def test_floor_formula_is_relative_to_the_max_abs_value(self):
        """噪声底是**相对**量：阈值 = roundoff_rel_floor × 窗口内 max|value|（不是绝对量）。"""
        from iteration_error_bound import _relative_floor  # noqa: PLC0415

        self.assertTrue(_relative_floor([2.0, 2.0], [1.9e-10], 1e-10))    # 1.9e-10 ≤ 2.0×1e-10
        self.assertFalse(_relative_floor([2.0, 2.0], [2.1e-10], 1e-10))   # 2.1e-10 > 2.0×1e-10
        self.assertTrue(_relative_floor([2.0, 2.0], [2.1e-10], 1e-9))     # 同样的增量在更松的底下算命中
        self.assertTrue(_relative_floor([0.0, 0.0], [0.0], 1e-10))        # 全零窗口不除以零


class ShapeChecksAreLive(unittest.TestCase):
    """审查 M6/M7：同号与单调两条检查各自都要能被证伪。"""

    def test_same_sign_but_non_monotone_is_refused(self):
        magnitudes = [1e-3, 2e-4, 3e-4, 1e-4, 2e-4, 1e-4]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(magnitudes))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        entry = result["per_level"][0]
        self.assertFalse(result["supported"])
        self.assertEqual(entry["reason_key"], "OSCILLATORY_OR_PLATEAU")
        self.assertTrue(entry["diagnostics"]["shape_conditions"]["same_sign"])
        self.assertFalse(entry["diagnostics"]["shape_conditions"]["monotone_decreasing"])
        self.assertEqual(entry["diagnostics"]["shape_conditions"]["refused_because"], "monotone_decreasing")

    def test_monotone_but_alternating_signs_is_refused(self):
        increments = [1e-3, -5e-4, 2.5e-4, -1.2e-4, 6e-5, -3e-5]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(increments))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        entry = result["per_level"][0]
        self.assertFalse(result["supported"])
        self.assertFalse(entry["diagnostics"]["shape_conditions"]["same_sign"])
        self.assertTrue(entry["diagnostics"]["shape_conditions"]["monotone_decreasing"])
        self.assertEqual(entry["diagnostics"]["shape_conditions"]["refused_because"], "same_sign")


class EvidenceBindingIsEnforced(unittest.TestCase):
    """审查 M11/M14/M15 + 行绑定：sha、数量、NaN、row↔record 不一致都要拒绝。"""

    def test_record_sha_is_compared(self):
        evidence, levels, gate = build_real(["d1p25"], tamper_record_sha=True)
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "SOURCE_SHA_MISMATCH")

    def test_history_sha_is_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative([1e-3, 5e-4, 2.5e-4, 1.25e-4, 6.25e-5, 3.1e-5]),
                                         tamper_history_sha=True)
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "SOURCE_SHA_MISMATCH")

    def test_missing_history_file_is_history_missing(self):
        """D6：缺历史文件必须报 HISTORY_MISSING，而不是被 sha 比较抢答成 SOURCE_SHA_MISMATCH。"""
        evidence, levels, gate = build_real(["d1p25"])
        evidence["iteration_error_source"]["source_records"][0]["path"] = str(
            LEVELS["d1p25"] / "does-not-exist.csv")
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "HISTORY_MISSING")

    def test_more_records_than_levels_is_refused(self):
        evidence, levels, gate = build_real(["d1p25"])
        evidence["iteration_error_source"]["source_records"].append(real_record("coarse"))
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "EVIDENCE_NOT_PINNED")
        self.assertIsNone(result["bounds"]["drag_x_n"])

    def test_row_binding_mismatch_is_refused(self):
        evidence, levels, gate = build_real(["d1p25"])
        levels[0]["row"]["drag_x_n"] = 12345.0
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "ROW_BINDING_MISMATCH")

    def test_row_binding_unverified_is_refused(self):
        evidence, levels, gate = build_real(["d1p25"])
        levels[0]["row"]["mass_imbalance"] = 0.5  # 记录里没有 measured_mass_imbalance
        result = derive_iteration_error_bounds(
            evidence, levels, target_columns={"mass_imbalance": "mass_imbalance"}, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "ROW_BINDING_UNVERIFIED")

    def test_nan_is_refused_with_its_own_reason(self):
        values = [1.0, 0.999, 0.9985, float("nan"), 0.998]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), values)
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "NONFINITE_VALUE")

    def test_asking_for_a_column_the_export_lacks_is_refused(self):
        """派生量（drag_coefficient_estimate）不在求解历史里：必须拒绝，不得拿别的列顶替。"""
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(
            evidence, levels,
            target_columns={"drag_coefficient_estimate": "drag_coefficient_estimate"}, assert_citable=gate)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "COLUMN_MISSING")


class GateFailuresAreDistinguishable(unittest.TestCase):
    """审查 C11：判据"不可用"与"记录不可引用"必须能区分。"""

    def test_gate_unavailable_propagates_its_own_key(self):
        evidence, levels, _ = build_real(["d1p25"])

        def unavailable(path):
            raise Refusal("GATE_UNAVAILABLE", "no gate")

        result = derive_iteration_error_bounds(evidence, levels, assert_citable=unavailable)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "GATE_UNAVAILABLE")

    def test_default_gate_import_failure_is_gate_unavailable(self):
        """D4/M26：默认门的**导入失败**路径此前没被测过。用 import 桩打断 run_record_gate 导入。

        2026-10-15（全量回归归因）：本测试曾只 `pop` 不还原，于是**之后**任何 `from run_record_gate
        import RecordNotCitable` 都会重新执行模块体、造出**第二个** `RecordNotCitable` 类；而早已导入的
        `run_registered_workflow_v2` 仍持有旧模块的 `assert_citable`，抛的是**旧类**。全量回归里
        `test_registered_workflow_v2` 的两条 `assertRaises(RecordNotCitable)` 因此变成 ERROR——单跑该模块
        时通过、全量跑时失败（真实的测试隔离缺陷）。修法：保存并还原同一个模块对象。
        """
        import builtins

        evidence, levels, _ = build_real(["d1p25"])
        real_import = builtins.__import__
        saved_gate = sys.modules.get("run_record_gate")

        def blocking_import(name, *args, **kwargs):
            if name == "run_record_gate":
                raise ImportError("stubbed: gate unavailable")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocking_import
        try:
            sys.modules.pop("run_record_gate", None)
            result = derive_iteration_error_bounds(evidence, levels)  # 不注入桩，走默认门
        finally:
            builtins.__import__ = real_import
            if saved_gate is not None:
                sys.modules["run_record_gate"] = saved_gate
            else:
                sys.modules.pop("run_record_gate", None)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "GATE_UNAVAILABLE")

    def test_the_import_stub_cannot_split_the_gate_module_identity(self):
        """§2-O9：把上面那条测试的真实缺陷钉成判据。

        判据不是"该测试有没有还原 sys.modules"这句写法，而是它的**后果**：消费者从
        `run_record_gate` 拿到的异常类与断言函数必须与 `sys.modules['run_record_gate']` 里那个模块
        是同一个对象。一旦有人再写成只 pop 不还原，后一条导入就会造出第二个类/函数，本测试随即失败。

        独立审查 F4 指出：原第一行 `assertIs(sys.modules['run_record_gate'], gate)` 是同义反复
        （`import X as gate` 返回的就是 `sys.modules['X']`）。已删掉它，改为两条有分辨力的身份断言。
        """
        sys.path.insert(0, str(ROOT / "scripts"))
        import run_record_gate as gate
        import run_registered_workflow_v2 as adapter
        from run_record_gate import assert_citable as fresh_assert_citable

        # 早期导入者（adapter）持有的类/函数必须与**现在**从 sys.modules 拿到的完全同一
        self.assertIs(adapter.assert_citable, fresh_assert_citable,
                      "run_record_gate 被重新导入过：assert 函数身份分裂")
        self.assertIs(adapter.assert_citable.__globals__["RecordNotCitable"], gate.RecordNotCitable,
                      "run_record_gate 被重新导入过：异常类身份分裂，assertRaises(RecordNotCitable) 会漏接")

    def test_other_gate_errors_become_not_citable(self):
        evidence, levels, _ = build_real(["d1p25"])

        def broken(path):
            raise RuntimeError("record not citable")

        result = derive_iteration_error_bounds(evidence, levels, assert_citable=broken)
        self.assertFalse(result["supported"])
        self.assertEqual(result["per_level"][0]["reason_key"], "RECORD_NOT_CITABLE")


class AggregationIsFailClosed(unittest.TestCase):
    """审查 M9：不得"多数档通过就算通过"。"""

    def _geometric_synthetic(self, tmp: Path):
        """构造一个在签署阈值下真的合格（同号+严格衰减+留出通过）的档。"""
        magnitudes = [1e-3, 3.9e-4, 1.5e-4, 6e-5, 2.4e-5, 9.5e-6, 3.8e-6, 1.55e-6]
        return synthetic(tmp, cumulative(magnitudes))

    def test_majority_does_not_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative([1e-3, 3.9e-4, 1.5e-4, 6e-5, 2.4e-5, 9.5e-6, 3.8e-6, 1.55e-6]))
            evidence["iteration_error_source"]["source_records"].append(real_record("d1p50"))
            levels.append(real_level("d1p50"))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertFalse(result["supported"])
        self.assertEqual(result["refusal_count"], 1)
        self.assertIn("[1]=", result["reason"])
        self.assertEqual(result["bounds"]["drag_x_n"], [None, None])

    def test_all_supported_levels_stay_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative([1e-3, 3.9e-4, 1.5e-4, 6e-5, 2.4e-5, 9.5e-6, 3.8e-6, 1.55e-6]))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertTrue(result["supported"], result.get("reason"))
        self.assertEqual([entry["bound_kind"] for entry in result["per_level"]], ["GEOMETRIC_EXTRAPOLATION"])


class DeclaredThresholdsAreRequired(unittest.TestCase):
    """签署条件：阈值必须来自 CaseSpec 事前声明；模块默认值不得用于接受性判定。"""

    def test_requiring_declared_thresholds_without_them_is_refused(self):
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               require_declared_thresholds=True)
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "THRESHOLDS_NOT_DECLARED")
        self.assertIsNone(result["bounds"]["drag_x_n"])
        self.assertEqual(result["threshold_source"], "module_default")

    def test_case_spec_thresholds_are_used_and_recorded(self):
        evidence, levels, gate = build_real(["d1p25"])
        case_spec = {"iteration_error_thresholds": {
            "window": 8, "min_decay_samples": 5, "holdout_fraction": 1 / 3,
            "max_holdout_rel_error": 0.35, "min_r_squared": 0.95, "roundoff_rel_floor": 1e-10,
            "roundoff_rel_floor_basis": "声明约定：双精度 1000 步量级下相对漂移 ≤1e-10 视为不可分辨"}}
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               thresholds=case_spec, require_declared_thresholds=True)
        self.assertTrue(result["supported"], result.get("reason"))
        self.assertEqual(result["threshold_source"], "case_spec_pre_declared")
        self.assertEqual(result["assumptions_declared"]["window"], 8)

    def test_incomplete_case_spec_block_is_refused(self):
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(
            evidence, levels, assert_citable=gate, require_declared_thresholds=True,
            thresholds={"iteration_error_thresholds": {"window": 8}})
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "THRESHOLDS_NOT_DECLARED")

    def test_default_source_is_labelled_when_not_required(self):
        evidence, levels, gate = build_real(["d1p25"])
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
        self.assertEqual(result["threshold_source"], "module_default")


class HonestyBoundaryIsExplicit(unittest.TestCase):
    def test_method_is_never_claimed_accepted(self):
        for names in (["d1p25"], ["d1p50"]):
            evidence, levels, gate = build_real(names)
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)
            self.assertFalse(result["method_accepted"])
            self.assertEqual(result["method_acceptance"], "PENDING_SCIENTIFIC_REVIEW")
            self.assertIn("不得据此宣称工程通过", result["note"])

    def test_consumer_contract_declares_bound_semantics(self):
        evidence, levels, gate = build_real(["d1p25"])
        contract = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["consumer_contract"]
        semantics = contract["bound_semantics"]
        self.assertIn("OBSERVED_DRIFT_AMPLITUDE", semantics)
        self.assertIn("不得", semantics["OBSERVED_DRIFT_AMPLITUDE"])
        self.assertIn("role_pinning", contract)
        self.assertIn("row_binding", contract)
        self.assertIn("extra_targets", contract)
        for key in ("bounds_length", "zero_bound_caveat", "error_propagation_eligibility",
                    "multi_target_ordering", "structural_enforcement"):
            self.assertIn(key, contract, key)
        self.assertIn("不得当误差界", contract["zero_bound_caveat"])
        self.assertIn("EMPIRICAL_ITERATION_ESTIMATE", contract["error_propagation_eligibility"])


if __name__ == "__main__":
    unittest.main()

class ScientificSignedRestrictions(unittest.TestCase):
    """独立科学签署（APPROVED_WITH_RESTRICTIONS）的条件必须可执行：C1~C5。"""

    def test_validator_refuses_thresholds_weaker_than_signed(self):
        """C2：两点拟合 + 1 个留出样本 + R²≥0.5 + 留出 0.9 这种"弱声明"必须被拒。"""
        weak = {"iteration_error_thresholds": {
            "window": 4, "min_decay_samples": 2, "holdout_fraction": 1 / 3,
            "max_holdout_rel_error": 0.9, "min_r_squared": 0.5, "roundoff_rel_floor": 1.0}}
        with self.assertRaises(Refusal) as caught:
            iteration_thresholds_from_case_spec(weak)
        self.assertEqual(caught.exception.reason_key, "THRESHOLDS_WEAKER_THAN_SIGNED")

    def test_signed_declaration_passes_the_validator(self):
        """C2：签署时声明的那六个值必须被校验器接受。"""
        signed = {"iteration_error_thresholds": {
            "window": 8, "min_decay_samples": 5, "holdout_fraction": 1 / 3,
            "max_holdout_rel_error": 0.35, "min_r_squared": 0.95, "roundoff_rel_floor": 1e-10,
            "roundoff_rel_floor_basis": "声明约定：双精度 1000 步量级下相对漂移 ≤1e-10 视为不可分辨"}}
        self.assertEqual(iteration_thresholds_from_case_spec(signed)["window"], 8)

    def test_eligibility_map_carries_only_geometric_estimates(self):
        """C3：MEASURED / NO_OBSERVED_DRIFT 在资格映射里必须是 None，不能是 0 或该档数字。"""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            geometric_mags = [1e-3, 3.9e-4, 1.5e-4, 6e-5, 2.4e-5, 9.5e-6, 3.8e-6, 1.55e-6]
            geometric = write_history(directory, cumulative(geometric_mags))
            constant = write_history(directory, [0.3] * 9)
            record = {"path": str(geometric), "sha256": sha256(geometric),
                      "record": str(REAL_RECORD), "record_sha256": sha256(REAL_RECORD)}
            record_const = {"path": str(constant), "sha256": sha256(constant),
                            "record": str(REAL_RECORD), "record_sha256": sha256(REAL_RECORD)}
            evidence = {"iteration_error_source": {"source_records": [record, record_const]}}
            levels = [{"trial_dir": str(LEVELS["d1p25"]), "row": dict(REAL_ROW)},
                      {"trial_dir": str(LEVELS["d1p25"]), "row": dict(REAL_ROW)}]
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        eligible = result["eligible_iteration_estimates"]["drag_x_n"]
        kinds = [entry["bound_kind"] for entry in result["per_level"]]
        self.assertEqual(kinds, ["GEOMETRIC_EXTRAPOLATION", "NO_OBSERVED_DRIFT"])
        self.assertIsNotNone(eligible[0])
        self.assertIsNone(eligible[1], "逐位不变不得被当成 0 误差项")

    def test_r_sensitivity_band_is_present(self):
        """C4：当"解析标尺"用必须同时给敏感带。"""
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        band = entry["r_sensitivity"]
        self.assertAlmostEqual(band["actual_r"], entry["diagnostics"]["decay_ratio"], delta=1e-12)
        self.assertGreater(band["bound_at_r_0p5"], band["bound_at_actual_r"])
        self.assertGreater(band["bound_at_r_0p7"], band["bound_at_r_0p5"])
        self.assertGreater(band["bound_at_r_0p9"], band["bound_at_r_0p7"])

    def test_context_helper_juxtaposes_the_record_statistics(self):
        """C5：必须并列同档记录的窗口统计，并给出与已发生尾部运动的比值。"""
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        record = json.loads((LEVELS["d1p25"] / "external_flow_validation.json").read_text(encoding="utf-8"))
        context = iteration_estimate_context(entry, record)
        self.assertEqual(context["window_statistics"]["drag_window_samples"],
                         record["drag_window_samples"])
        self.assertGreater(context["ratio_relative_deviation_over_estimate"], 10.0)
        self.assertLess(context["ratio_relative_deviation_over_estimate"], 11.0)
        self.assertGreater(context["ratio_observed_range_over_estimate"], 16.0)
        self.assertLess(context["ratio_observed_range_over_estimate"], 17.0)
        self.assertFalse(context["estimate_is_upper_bound"])

    def test_context_helper_refuses_without_record_statistics(self):
        """C5：记录缺窗口统计时不得单独上报估计值。"""
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        with self.assertRaises(Refusal) as caught:
            iteration_estimate_context(entry, {"measured_drag_x_n": 1.0})
        self.assertEqual(caught.exception.reason_key, "REPORT_CONTEXT_MISSING")


class TheSignatureGateHasNoFixedPointAndNoSelfSigning(unittest.TestCase):
    """C14/A + C16/C17：门控不内嵌签署 sha；pin 必须内容寻址到**可引用运行记录**，且用默认判据校验。"""

    def setUp(self):
        self.evidence, self.levels, _ = build_real(["d1p25"])

    def _fixture(self, directory: Path, *, module_sha=None, thresholds=None, approved=True,
                 pin_sha_override=None, pin_missing_module_sha=False, run_status="ok",
                 run_mentions_pin=True, pin_module_sha="auto"):
        """建一个最小但**真的可引用**的运行目录 + pin 记录（测试夹具，不是真签署）。"""
        run = directory / "run"
        run.mkdir(parents=True, exist_ok=True)
        (run / "case-probe.json").write_text(json.dumps({"state": "EXECUTION_COMPLETED"}), encoding="utf-8")
        signed = {"TEST_FIXTURE_NOT_A_REAL_SIGNATURE": True,
                  "independent_scientific": {
                      "state": "SIGNED", "approved": approved,
                      "signed_module_sha256": module_sha or sha256(ROOT / "scripts/iteration_error_bound.py"),
                      "signed_thresholds": thresholds or {
                          "window": 8, "min_decay_samples": 5, "holdout_fraction": 1 / 3,
                          "max_holdout_rel_error": 0.35, "min_r_squared": 0.95,
                          "roundoff_rel_floor": 1e-10}}}
        sig_path = directory / "fixture-signature.json"
        sig_path.write_text(json.dumps(signed, ensure_ascii=False), encoding="utf-8")
        pin = {"status": "ok", "signature_sha256": pin_sha_override or sha256(sig_path)}
        if not pin_missing_module_sha:
            pin["signature_module_sha256"] = (signed["independent_scientific"]["signed_module_sha256"]
                                              if pin_module_sha == "auto" else pin_module_sha)
        pin_path = run / "signature-pin.json"
        pin_path.write_text(json.dumps(pin, ensure_ascii=False), encoding="utf-8")
        files = [{"name": "signature-pin.json", "sha256": sha256(pin_path)}] if run_mentions_pin else []
        stage = run / "stage-materialize.json"
        stage.write_text(json.dumps({"status": run_status, "files": files}), encoding="utf-8")
        return sig_path, pin_path, stage

    def test_the_module_embeds_no_signature_sha(self):
        import iteration_error_bound as ieb  # noqa: PLC0415

        self.assertFalse(hasattr(ieb, "SIGNED_SIGNATURE_SHAS"))
        source = (ROOT / "scripts/iteration_error_bound.py").read_text(encoding="utf-8")
        self.assertNotIn("SIGNED_SIGNATURE_SHAS", source)

    def test_without_a_signature_record_it_stays_false(self):
        result = derive_iteration_error_bounds(self.evidence, self.levels)
        self.assertFalse(result["method_accepted"])
        self.assertEqual(result["method_acceptance"], "PENDING_SCIENTIFIC_REVIEW")

    def test_in_memory_signature_record_is_refused(self):
        record = json.loads(SIGNATURE_RECORD.read_text(encoding="utf-8"))
        result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=record)
        self.assertFalse(result["method_accepted"])
        self.assertIn("必须是文件路径", " ".join(result["signature_problems"]))

    def test_missing_pin_record_is_refused(self):
        result = derive_iteration_error_bounds(self.evidence, self.levels,
                                               signature_record=SIGNATURE_RECORD)
        self.assertFalse(result["method_accepted"])
        self.assertIn("signature_pin_record", " ".join(result["signature_problems"]))

    def test_bare_expected_sha_is_not_admissible_for_acceptance(self):
        result = derive_iteration_error_bounds(self.evidence, self.levels,
                                               signature_record=SIGNATURE_RECORD,
                                               expected_signature_sha256=sha256(SIGNATURE_RECORD))
        self.assertFalse(result["method_accepted"])
        self.assertIn("裸 expected_signature_sha256", " ".join(result["signature_problems"]))
        self.assertEqual(result["pin_source"], "caller_argument_not_for_production")

    def test_c16_pin_must_be_content_addressed_in_a_run_record(self):
        """C16①：没有运行记录引用该 pin 的 sha ⇒ 拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, _ = self._fixture(Path(tmp))
            result = derive_iteration_error_bounds(self.evidence, self.levels,
                                                   signature_record=sig_path, signature_pin_record=pin_path)
        self.assertFalse(result["method_accepted"])
        self.assertIn("signature_pin_run_record", " ".join(result["signature_problems"]))

    def test_c16_run_record_must_actually_mention_the_pin_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), run_mentions_pin=False)
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("结构化", " ".join(result["signature_problems"]))

    def test_c16_run_record_must_be_citable(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), run_status="gate_failed")
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("默认 §2-O8 判据", " ".join(result["signature_problems"]))

    def test_c16_caller_supplied_gate_may_not_turn_acceptance_on(self):
        """C16③：一个参数就能关掉 pin 校验 ⇒ 覆盖判据时不得声称接受。"""
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp))
            result = derive_iteration_error_bounds(self.evidence, self.levels,
                                                   assert_citable=lambda path: {"state": "CITABLE"},
                                                   signature_record=sig_path, signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("C16③", " ".join(result["signature_problems"]))

    def test_c17_pin_must_declare_the_module_sha(self):
        """C17：pin 缺 signature_module_sha256 时，旧的 `if pin_module and ...` 会跳过交叉校验。"""
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), pin_missing_module_sha=True)
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("C17", " ".join(result["signature_problems"]))

    def test_pin_must_match_the_record_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), pin_sha_override="0" * 64,
                                                      run_mentions_pin=True)
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])

    def test_missing_threshold_keys_in_the_signature_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), thresholds={"window": 8})
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("缺阈值键", " ".join(result["signature_problems"]))

    def test_wrong_module_sha_breaks_the_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp), module_sha="0" * 64)
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("签署 sha256", " ".join(result["signature_problems"]))

    def test_acceptance_is_reachable_through_the_default_gate(self):
        """正向路径：**默认 §2-O8 判据** + 内容寻址 pin ⇒ True，且**模块字节不变**（无不动点）。"""
        before = sha256(ROOT / "scripts/iteration_error_bound.py")
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp))
            pin_sha, run_sha = sha256(pin_path), sha256(stage)
            result = derive_iteration_error_bounds(self.evidence, self.levels, signature_record=sig_path,
                                                   signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertTrue(result["method_accepted"], result.get("signature_problems"))
        self.assertEqual(result["method_acceptance"], "ACCEPTED_WITH_RESTRICTIONS")
        self.assertTrue(result["pin_source"].startswith("structured_content_addressed_run_record"))
        # C20：返回值必须携带两份 sha，供报告门 pin 它实际读到的字节
        self.assertEqual(result["pin_record_sha256"], pin_sha)
        self.assertEqual(result["pin_run_record_sha256"], run_sha)
        self.assertEqual(sha256(ROOT / "scripts/iteration_error_bound.py"), before,
                         "接受路径不得改动模块字节")


class TheSecondScientificReviewHolesAreClosed(unittest.TestCase):
    """第二轮科学复验的 C8 / C11 / C12（C9 见 TheSignatureIsScopedNotAFlippedConstant）。"""

    def test_c8_bare_dictionary_thresholds_are_out_of_scope(self):
        evidence, levels, gate = build_real(["d1p25"])
        bare = {"window": 4, "min_decay_samples": 2, "holdout_fraction": 1 / 3,
                "max_holdout_rel_error": 0.9, "min_r_squared": 0.5, "roundoff_rel_floor": 1.0}
        result = derive_iteration_error_bounds(evidence, levels, assert_citable=gate,
                                               thresholds=bare, require_declared_thresholds=True)
        self.assertFalse(result["supported"])
        self.assertEqual(result["reason_key"], "THRESHOLDS_NOT_DECLARED")
        self.assertIn("纯字典", result["reason"])

    def test_c12_floor_without_basis_or_too_high_is_refused(self):
        base = {"window": 8, "min_decay_samples": 5, "holdout_fraction": 1 / 3,
                "max_holdout_rel_error": 0.35, "min_r_squared": 0.95}
        with self.assertRaises(Refusal) as no_basis:
            iteration_thresholds_from_case_spec(
                {"iteration_error_thresholds": dict(base, roundoff_rel_floor=1e-10)})
        self.assertEqual(no_basis.exception.reason_key, "THRESHOLDS_WEAKER_THAN_SIGNED")
        self.assertIn("basis", str(no_basis.exception))
        with self.assertRaises(Refusal) as too_high:
            iteration_thresholds_from_case_spec(
                {"iteration_error_thresholds": dict(base, roundoff_rel_floor=1e-3,
                                                    roundoff_rel_floor_basis="随便写")})
        self.assertIn("> 1e-8", str(too_high.exception))

    def test_c11_window_max_single_step_ratio_is_the_slowest_decay(self):
        evidence, levels, gate = build_real(["d1p25"])
        entry = derive_iteration_error_bounds(evidence, levels, assert_citable=gate)["per_level"][0]
        band = entry["r_sensitivity"]
        self.assertAlmostEqual(band["window_max_single_step_ratio_over_last"], 0.395795926495215, delta=5e-12)
        self.assertAlmostEqual(band["bound_at_window_max_ratio"], 1.550950e-08, delta=5e-12)
        self.assertLess(band["bound_at_window_max_ratio"], band["bound_at_r_0p5"])

class ContentAddressingMustBeStructured(unittest.TestCase):
    """C19/C20：自由文本命中不算内容寻址；符号链接/异地运行记录一律拒绝。"""

    def setUp(self):
        from tests.test_iteration_error_bound import TheSignatureGateHasNoFixedPointAndNoSelfSigning as Base  # noqa: PLC0415
        self._base = Base

    def _fixture(self, directory):
        helper = TheSignatureGateHasNoFixedPointAndNoSelfSigning("test_the_module_embeds_no_signature_sha")
        return helper._fixture(directory)

    def test_c19_free_text_mention_is_not_content_addressing(self):
        """C19：只在 note 里提到 pin 的 sha（自由文本）必须被拒。"""
        with tempfile.TemporaryDirectory() as tmp:
            sig_path, pin_path, stage = self._fixture(Path(tmp))
            stage.write_text(json.dumps({"status": "ok",
                                         "note": "unrelated log line mentioning pin sha %s" % sha256(pin_path)}),
                             encoding="utf-8")
            result = derive_iteration_error_bounds(build_real(["d1p25"])[0], build_real(["d1p25"])[1],
                                                   signature_record=sig_path, signature_pin_record=pin_path,
                                                   signature_pin_run_record=stage)
        self.assertFalse(result["method_accepted"])
        self.assertIn("结构化", " ".join(result["signature_problems"]))

    def test_c20_symlinked_run_record_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sig_path, pin_path, stage = self._fixture(root)
            link = root / "run" / "stage-link.json"
            link.symlink_to(stage)
            result = derive_iteration_error_bounds(build_real(["d1p25"])[0], build_real(["d1p25"])[1],
                                                   signature_record=sig_path, signature_pin_record=pin_path,
                                                   signature_pin_run_record=link)
        self.assertFalse(result["method_accepted"])
        self.assertIn("符号链接", " ".join(result["signature_problems"]))

    def test_c20_run_record_in_another_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sig_path, pin_path, stage = self._fixture(root)
            elsewhere = root / "other"
            elsewhere.mkdir()
            moved = elsewhere / "stage-copy.json"
            moved.write_bytes(stage.read_bytes())
            (elsewhere / "case-probe.json").write_text(json.dumps({"state": "EXECUTION_COMPLETED"}), encoding="utf-8")
            result = derive_iteration_error_bounds(build_real(["d1p25"])[0], build_real(["d1p25"])[1],
                                                   signature_record=sig_path, signature_pin_record=pin_path,
                                                   signature_pin_run_record=moved)
        self.assertFalse(result["method_accepted"])
        self.assertIn("同一位置", " ".join(result["signature_problems"]))
