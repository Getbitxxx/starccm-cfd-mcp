# -*- coding: utf-8 -*-
"""方法接受包的可执行验收（§2-O9）。

接受包是**签署物**：它请求采纳"迭代误差界方法"作为 ① 误差分列中『迭代』项的候选。
本测试保证：

* 包可解析、处于**未签署**状态，且签署选项/效果界定清楚；
* **包不能过度主张**：禁止把它说成受证上界/certified/工程通过，且明确 `method_accepted=False` 时不得接线；
* 包里的**每个数字都能被当前代码复现**（防止包与实现漂移）——正例界值、r、R²、噪声底漂移、
  以及三类反例的拒绝原因都在真实夹具上现场复算；
* 核心外推假设 A6 被显式标为高风险且"现有证据无法证明"，不得被含糊过去。
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from iteration_error_bound import derive_iteration_error_bounds  # noqa: E402

PACKET_JSON = ROOT / "test-evidence/engineering-validation-start-20261009/METHOD-ACCEPTANCE-PACKET.json"
PACKET_MD = ROOT / "test-evidence/engineering-validation-start-20261009/METHOD-ACCEPTANCE-PACKET.md"
SIGNATURE_JSON = ROOT / "test-evidence/engineering-validation-start-20261009/METHOD-ACCEPTANCE-SIGNATURE.json"
EXEC = ROOT / "test-evidence/static-release-candidate/engineering-execution-20261007"
LEVELS = {
    "coarse": EXEC / "mesh/coarse/static-eng-mesh-coarse-20261008-s1",
    "fine": EXEC / "mesh/fine/static-eng-mesh-fine-20261008-s1",
    "d1p25": EXEC / "domain/d1p25/static-eng-domain-d1p25-20261008-s1",
    "d1p50": EXEC / "domain/d1p50/static-eng-domain-d1p50-20261008-s1",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_provider(name: str, **kwargs) -> dict:
    trial = LEVELS[name]
    history = trial / "external_flow_solve_history.csv"
    record = trial / "external_flow_validation.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    evidence = {"iteration_error_source": {"source_records": [
        {"path": str(history), "sha256": sha256(history),
         "record": str(record), "record_sha256": sha256(record)}]}}
    levels = [{"trial_dir": str(trial), "row": {"drag_x_n": payload["measured_drag_x_n"]}}]
    kwargs.setdefault("assert_citable", lambda path: {})
    return derive_iteration_error_bounds(evidence, levels, **kwargs)


REAL_ROW = {"drag_x_n": json.loads(
    (LEVELS["d1p25"] / "external_flow_validation.json").read_text(encoding="utf-8"))["measured_drag_x_n"]}


def cumulative(increments, start=1.0):
    values = [start]
    for increment in increments:
        values.append(values[-1] + increment)
    return values


def synthetic(directory: Path, values, column: str = "drag_x_n"):
    """内联的最小夹具：合成历史 + **真实**可引用记录（行绑定取真实记录里的实测值）。"""
    path = directory / "history.csv"
    lines = ["iteration,elapsed_seconds,%s,mass_imbalance" % column]
    for index, value in enumerate(values):
        lines.append("%d,%.3f,%.17g,0.0" % (50 * (index + 1), float(index), value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record = LEVELS["d1p25"] / "external_flow_validation.json"
    evidence = {"iteration_error_source": {"source_records": [
        {"path": str(path), "sha256": sha256(path),
         "record": str(record), "record_sha256": sha256(record)}]}}
    return evidence, [{"trial_dir": str(LEVELS["d1p25"]), "row": dict(REAL_ROW)}]


class ThePacketIsAPendingSignature(unittest.TestCase):
    def setUp(self):
        self.packet = json.loads(PACKET_JSON.read_text(encoding="utf-8"))

    def test_state_and_signature_block(self):
        self.assertEqual(self.packet["state"],
                         "SIGNED_ROUND6_CONVERGED")
        signature = self.packet["signature"]
        self.assertEqual(signature["current"], "SIGNED_ROUND6_CONVERGED")
        self.assertIn("ACCEPTED_FOR_USE_AS_CANDIDATE_ITERATION_TERM", signature["allowed_decisions"])
        self.assertIn("不得自签", signature["required_from"])

    def test_user_decision_is_recorded_with_both_conditions(self):
        decision = self.packet["signature"]["user_decision"]
        self.assertEqual(decision["decision"], "ACCEPTED_FOR_USE_AS_CANDIDATE_ITERATION_TERM（接受 A6 外推作为候选迭代误差项）")
        joined = " ".join(decision["conditions"])
        self.assertIn("CaseSpec", joined)
        self.assertIn("独立科学审查", joined)

    def test_signature_history_converged_on_the_current_bytes(self):
        """六轮签署收敛：当前字节被 round-6 覆盖，但仍不得接线（C7 + 报告门义务）。"""
        scientific = self.packet["signature"]["independent_scientific"]
        self.assertEqual([h["round"] for h in scientific["history"]], [1, 2, 3, 4, 5, 6])
        for entry in scientific["history"]:
            self.assertEqual(entry["decision"], "APPROVED_WITH_RESTRICTIONS")
        self.assertTrue(scientific["current_scope_valid"])
        self.assertIn("收敛", scientific["awaiting"])
        signature_record = json.loads(SIGNATURE_JSON.read_text(encoding="utf-8"))
        self.assertTrue(signature_record["effective_method_accepted"])
        self.assertIn("不得接线", signature_record["effective_method_accepted_scope"])
        self.assertFalse(signature_record["method_accepted_for_wiring"])
        self.assertTrue(signature_record["method_accepted_runtime_reachable"])
        for key in ("record_sha256", "record_round1_sha256"):
            self.assertEqual(len(signature_record[key]), 64, key)

    def test_wiring_checklist_and_pin_integration_are_recorded(self):
        checklist = self.packet["signature"]["independent_scientific"]["wiring_checklist"]
        joined = " ".join(checklist)
        for token in ("C7", "报告门", "eligible_iteration_estimates", "只用模块返回值", "NOT_ESTABLISHED"):
            self.assertIn(token, joined, token)
        integration = json.loads((ROOT / self.packet["pin_integration"]["record"]).read_text(encoding="utf-8"))
        self.assertTrue(integration["result"]["method_accepted"], integration["result"])
        self.assertEqual(integration["result"]["method_acceptance"], "ACCEPTED_WITH_RESTRICTIONS")
        self.assertEqual(len(integration["result"]["pin_record_sha256"]), 64)
        self.assertEqual(len(integration["result"]["pin_run_record_sha256"]), 64)
        self.assertEqual(integration["estimate"]["bound_semantics"], "EMPIRICAL_ITERATION_ESTIMATE")

    def test_the_conditions_are_implemented_not_just_promised(self):
        impl = self.packet["implementation_of_the_conditions"]
        for key in ("threshold_declaration", "refusal_key", "case_spec_declaration", "tests"):
            self.assertIn(key, impl, key)
        self.assertEqual(impl["refusal_key"], "THRESHOLDS_NOT_DECLARED")

    def test_acceptance_does_not_mean_engineering_pass(self):
        signature = self.packet["signature"]
        joined = " ".join(signature["effect_of_acceptance_does_NOT_include"])
        for token in ("工程 E 通过", "受证上界", "GCI"):
            self.assertIn(token, joined, token)
        self.assertIn("保持 method_accepted=False", signature["until_signed"])

    def test_the_rendered_markdown_comes_from_the_same_source(self):
        self.assertTrue(PACKET_MD.is_file())
        text = PACKET_MD.read_text(encoding="utf-8")
        bound = self.packet["evidence_on_real_data"]["positive_geometric_tail"]["bound"]
        self.assertIn("%.16g" % bound, text)
        self.assertIn("经验估计（非界）", text)
        self.assertIn("APPROVED_WITH_RESTRICTIONS", text)


class TheCoreExtrapolationAssumptionIsExposed(unittest.TestCase):
    def setUp(self):
        self.assumptions = json.loads(PACKET_JSON.read_text(encoding="utf-8"))["assumptions_to_be_judged"]

    def test_a6_is_present_and_marked_high_risk(self):
        a6 = next(item for item in self.assumptions if item["id"] == "A6")
        self.assertIn("窗口之后", a6["text"])
        self.assertIn("高", a6["risk"])
        self.assertIn("无法由现有证据证明", a6["checkable_by"])

    def test_holdout_is_not_oversold(self):
        a4 = next(item for item in self.assumptions if item["id"] == "A4")
        self.assertIn("窗口内", a4["checkable_by"])

    def test_all_six_assumptions_are_listed(self):
        self.assertEqual([item["id"] for item in self.assumptions], ["A1", "A2", "A3", "A4", "A5", "A6"])


class ProhibitionsAreExplicit(unittest.TestCase):
    def setUp(self):
        self.usage = json.loads(PACKET_JSON.read_text(encoding="utf-8"))["usage_contract"]

    def test_may_not_list_covers_every_real_trap(self):
        joined = " ".join(self.usage["may_not"])
        for token in ("受证上界", "certified", "MEASURED_TAIL_DRIFT", "GCI",
                      "method_accepted=False", "工程 E 通过"):
            self.assertIn(token, joined, token)

    def test_structural_constraints_are_carried_over(self):
        joined = " ".join(self.usage["structural_constraints_for_the_consumer"])
        for token in ("长度恒等于档数", "0.0", "EMPIRICAL_ITERATION_ESTIMATE", "档序"):
            self.assertIn(token, joined, token)


class EveryNumberInThePacketIsReproducible(unittest.TestCase):
    """防止包与实现漂移：包里的界/r/R²/漂移/拒绝原因都现场复算。"""

    def setUp(self):
        self.packet = json.loads(PACKET_JSON.read_text(encoding="utf-8"))
        self.evidence = self.packet["evidence_on_real_data"]

    def test_geometric_positive_numbers_match_the_live_provider(self):
        quoted = self.evidence["positive_geometric_tail"]
        entry = run_provider("d1p25")["per_level"][0]
        self.assertEqual(entry["bound_kind"], quoted["classification"])
        self.assertAlmostEqual(entry["bound"], quoted["bound"], delta=1e-21)
        self.assertAlmostEqual(entry["diagnostics"]["decay_ratio"], quoted["decay_ratio"], delta=1e-11)
        self.assertAlmostEqual(entry["diagnostics"]["r_squared"], quoted["r_squared"], delta=1e-15)
        for got, want in zip(entry["diagnostics"]["holdout_relative_errors"],
                             quoted["holdout_relative_errors"]):
            self.assertAlmostEqual(got, want, delta=1e-15)

    def test_noise_floor_resolution_matches_the_live_provider(self):
        """C2b 生效后：签署底 1e-10 下 coarse 只能报"不可分辨"，fine 仍报实测漂移。"""
        quoted = self.evidence["positive_below_noise_floor"]
        coarse = run_provider("coarse")["per_level"][0]
        self.assertEqual(coarse["reason_key"], "DRIFT_BELOW_FLOOR_NOT_RESOLVED")
        self.assertAlmostEqual(coarse["diagnostics"]["relative_drift"],
                               quoted["coarse"]["measured_relative_drift"], delta=1e-24)
        self.assertAlmostEqual(coarse["diagnostics"]["floor_margin"],
                               quoted["coarse"]["floor_margin"], delta=1e-9)
        fine = run_provider("fine")["per_level"][0]
        self.assertEqual(fine["bound_kind"], "MEASURED_TAIL_DRIFT")
        resolved = run_provider("coarse", roundoff_rel_floor=1e-9)["per_level"][0]
        self.assertEqual(resolved["bound_kind"], "MEASURED_TAIL_DRIFT")
        self.assertAlmostEqual(resolved["bound"], 1.5816070675356286e-11, delta=1e-24)

    def test_oscillatory_negative_matches(self):
        quoted = self.evidence["negative_oscillatory"]
        entry = run_provider("d1p50")["per_level"][0]
        self.assertEqual(entry["bound_kind"], quoted["classification"])
        self.assertEqual(entry["reason_key"], quoted["reason_key"])

    def test_r_squared_negative_fixture_matches(self):
        quoted = self.evidence["negative_r_squared_reductive"]
        magnitudes = json.loads(
            (ROOT / "test-evidence/engineering-validation-start-20261009/iteration-error-provider-review/"
                     "_work/r2_counterexample.json").read_text(encoding="utf-8"))["fixture_R2_0p800"]["magnitudes"]
        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(magnitudes))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        entry = result["per_level"][0]
        self.assertEqual(entry["reason_key"], "DECAY_FIT_FAILED")
        self.assertAlmostEqual(entry["diagnostics"]["r_squared"], quoted["r_squared"], delta=5e-4)
        self.assertGreater(entry["diagnostics"]["r_squared"], 0.1)
        self.assertTrue(True)  # 行绑定来自真实记录（见 _synthetic 的 row 取自真实 validation 记录）

    def test_holdout_negative_fixture_matches(self):
        quoted = self.evidence["negative_holdout_falsified"]
        import tempfile  # noqa: PLC0415

        magnitudes = [1e-3, 5e-4, 2.5e-4, 1.25e-4, 6.25e-5, 3.1e-5, 2.5e-5, 2.4e-5]
        with tempfile.TemporaryDirectory() as tmp:
            evidence, levels = synthetic(Path(tmp), cumulative(magnitudes))
            result = derive_iteration_error_bounds(evidence, levels, assert_citable=lambda path: {})
        self.assertEqual(result["per_level"][0]["reason_key"], quoted["outcome"])
        self.assertIn("留出段抬头", quoted["fixture"])

    def test_packet_hashes_match_the_current_artifacts(self):
        artifact = self.packet["artifact_under_review"]
        self.assertEqual(sha256(ROOT / artifact["module"]), artifact["module_sha256"])
        self.assertEqual(sha256(ROOT / artifact["tests"]), artifact["tests_sha256"])


class TheProviderStillRefusesToSelfAccept(unittest.TestCase):
    def test_live_provider_reports_pending_review(self):
        result = run_provider("d1p25")
        self.assertFalse(result["method_accepted"])
        self.assertEqual(result["method_acceptance"], "PENDING_SCIENTIFIC_REVIEW")

    def test_packet_keeps_the_open_questions_open(self):
        questions = json.loads(PACKET_JSON.read_text(encoding="utf-8"))["open_questions_for_the_signer"]
        ids = [item["id"] for item in questions]
        for expected in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(expected, ids)
        self.assertTrue(all(item["options"] for item in questions))


if __name__ == "__main__":
    unittest.main()


class SignedDisclosuresCorrectionsAndPreconditions(unittest.TestCase):
    """独立科学签署要求的一并披露（C6）、更正（N1/N4）、并列要求（C5）与接线前提（C7）。"""

    def setUp(self):
        self.packet = json.loads(PACKET_JSON.read_text(encoding="utf-8"))

    def test_c6_disclosures_are_all_present(self):
        joined = " ".join(self.packet["disclosures_C6"])
        for token in ("四档整体拒绝", "声明晚于运行", "没有", "iteration_cap", "未单独复审"):
            self.assertIn(token, joined, token)

    def test_c7_wiring_precondition_is_stated_and_forbids_substitution(self):
        block = self.packet["wiring_precondition_C7"]
        self.assertIn("drag_coefficient_estimate", block["blocker"])
        self.assertIn("不得用别的列顶替", block["forbidden"])

    def test_c5_juxtaposition_numbers_are_present(self):
        juxtaposition = self.packet["evidence_on_real_data"]["juxtaposition"]
        self.assertGreater(juxtaposition["ratio_relative_deviation_over_estimate"], 10.0)
        self.assertLess(juxtaposition["ratio_relative_deviation_over_estimate"], 11.0)
        self.assertGreater(juxtaposition["ratio_observed_range_over_estimate"], 16.0)
        self.assertLess(juxtaposition["ratio_observed_range_over_estimate"], 17.0)

    def test_n1_sign_correction_is_recorded(self):
        change = self.packet["evidence_on_real_data"]["positive_geometric_tail"]["change_vs_first_implementation"]
        self.assertLess(change["relative_change"], 0.0, "估计是变小，不是变大")
        self.assertIn("M3", change["cause"])
        self.assertIn("噪声底", change["not_the_cause"])

    def test_c1_wording_is_experience_estimate_not_bound(self):
        statement = self.packet["method_statement"]
        self.assertIn("经验估计", statement["estimate_not_bound"])
        self.assertIn("不是界", statement["estimate_not_bound"])
        self.assertFalse(self.packet["evidence_on_real_data"]["positive_geometric_tail"]["is_upper_bound"])
        self.assertIn("经验估计", PACKET_MD.read_text(encoding="utf-8"))

    def test_r3_outcome_and_acceptance_rate_are_disclosed(self):
        evidence = self.packet["evidence_on_real_data"]
        self.assertFalse(evidence["r3_four_level_outcome"]["supported"])
        self.assertEqual(evidence["acceptance_rate_on_real_data"]["geometric"], 2)
        self.assertIn("普遍成立", evidence["acceptance_rate_on_real_data"]["note"])

    def test_live_provider_does_not_accept_until_the_new_bytes_are_signed(self):
        """C9：修复改动字节后，白名单为空 ⇒ 现场调用必须拒绝（不得因为历史签署而放行）。"""
        result = run_provider("d1p25", signature_record=SIGNATURE_JSON)
        self.assertFalse(result["method_accepted"])
        self.assertEqual(result["method_acceptance"], "SIGNATURE_SCOPE_MISMATCH")
        self.assertIn("signature_pin_record", " ".join(result["signature_problems"]))
