# -*- coding: utf-8 -*-
"""① 适用性与参考比较合同的可执行验收（§2-O9：本轮发现必须落成测试）。

合同是**事前**冻结物：它规定本机数值证据、条件实验比较、工程 E 三者如何分别判定，
以及参考身份、归一化、误差分列与停止条件。测试保证：
* 合同可解析，且关键判据/禁止项没有在后续编辑中被悄悄删掉；
* 合同要求的 provider 与判据在仓库里**真实存在**（不是文档承诺）；
* "缺项保持 null/UNKNOWN"这条没有被写成已建立（防止把未知写成已证）；
* 参考决策路径 A/B 的**禁止物**清单完整（教育网页值、图中曲线、摘要当全文、公开复用升级、2% 当实验误差）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "test-evidence/engineering-validation-start-20261009/APPLICABILITY-AND-REFERENCE-CONTRACT.json"


class TheContractIsParseableAndComplete(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_required_sections_exist(self):
        for key in ("contract_id", "scenario", "three_separately_reported_tracks", "reference_decision",
                    "targets_and_normalization", "error_decomposition", "acceptance_and_stop",
                    "resources_and_reuse", "open_items_not_closed_by_this_contract"):
            self.assertIn(key, self.contract, key)

    def test_three_tracks_are_kept_separate(self):
        tracks = self.contract["three_separately_reported_tracks"]
        self.assertEqual(set(tracks), {"local_numerical_evidence", "conditional_experimental_comparison", "engineering_E"})
        self.assertIn("不得互相替代", json.dumps(tracks, ensure_ascii=False))

    def test_stop_conditions_include_the_measured_blockers(self):
        joined = json.dumps(self.contract["acceptance_and_stop"], ensure_ascii=False)
        for token in ("r<1.1", "正 p", "振荡", "provider", "参考不匹配", "NOT_ESTABLISHED"):
            self.assertIn(token, joined, token)

    def test_budget_matches_the_resource_contract(self):
        budget = self.contract["acceptance_and_stop"]["budget"]
        self.assertEqual(budget["logical_cores"], 64)
        self.assertGreaterEqual(budget["system_memory_headroom_GB"], 16)
        self.assertTrue(budget["segmented_with_checkpoints"])
        self.assertTrue(budget["single_slot"])


class ReferenceDecisionForbidsSubstitutes(unittest.TestCase):
    def setUp(self):
        self.decision = json.loads(CONTRACT.read_text(encoding="utf-8"))["reference_decision"]

    def test_reference_stays_null_until_real_evidence_exists(self):
        self.assertIsNone(self.decision["current_state"]["reference_value"])
        self.assertIsNone(self.decision["current_state"]["acceptance_tolerance"])

    def test_prohibitions_name_every_known_substitute(self):
        joined = " ".join(self.decision["prohibitions"])
        for token in ("教育网页", "图中标准曲线", "摘要", "全文", "公开资料复用", "2%"):
            self.assertIn(token, joined, token)

    def test_path_b_is_conditional_on_path_a_failing(self):
        self.assertIn("只有在 path A 明确不可得时才启用", self.decision["path_B_independent_matched_benchmark"]["rule"])
        backlog = self.decision["path_B_independent_matched_benchmark"]["backlog_not_registrable_now"]
        self.assertTrue(any("NACA" in item for item in backlog))


class ErrorDecompositionIsHonest(unittest.TestCase):
    def setUp(self):
        self.decomposition = json.loads(CONTRACT.read_text(encoding="utf-8"))["error_decomposition"]

    def test_iterative_provider_points_at_a_real_module(self):
        iterative = next(term for term in self.decomposition["terms"] if term["id"] == "iterative")
        self.assertIn("scripts/iteration_error_bound.py", iterative["how"])
        self.assertTrue((ROOT / "scripts/iteration_error_bound.py").is_file())
        self.assertTrue((ROOT / "tests/test_iteration_error_bound.py").is_file())

    def test_discretization_term_records_the_real_gap(self):
        discretization = next(term for term in self.decomposition["terms"] if term["id"] == "discretization")
        self.assertIn("NOT_ESTABLISHED", discretization["status"])
        self.assertTrue((ROOT / "scripts/grid_convergence.py").is_file())

    def test_experimental_uncertainty_is_unknown_not_zero(self):
        experimental = next(term for term in self.decomposition["terms"] if term["id"] == "experimental")
        self.assertIn("UNKNOWN", experimental["status"])
        self.assertIn("null", experimental["status"])

    def test_reporting_rule_forbids_calling_estimates_bounds(self):
        rule = self.decomposition["reporting_rule"]
        self.assertIn("受证上界", rule)
        self.assertIn("经验估计", rule)
        prohibitions = " ".join(self.decomposition["prohibitions"])
        for token in ("残差稳定", "95%", "abs(p)", "2%/5%"):
            self.assertIn(token, prohibitions, token)


class TheIterationThresholdsAreDeclaredPerCase(unittest.TestCase):
    """用户签署条件之一：四个（六个）方法阈值必须**逐算例事前声明**，不得用模块默认值做接受性判定。"""

    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_the_contract_declares_every_threshold(self):
        block = self.contract["iteration_error_thresholds"]
        for key in ("window", "min_decay_samples", "holdout_fraction",
                    "max_holdout_rel_error", "min_r_squared", "roundoff_rel_floor"):
            self.assertIn(key, block, key)
        self.assertIn("事前", block["declaration_rule"])

    def test_the_provider_reads_the_declared_block(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from iteration_error_bound import iteration_thresholds_from_case_spec  # noqa: PLC0415

        declared = iteration_thresholds_from_case_spec(self.contract)
        self.assertEqual(declared["window"], self.contract["iteration_error_thresholds"]["window"])
        self.assertEqual(declared["roundoff_rel_floor"], 1e-10)

    def test_missing_block_is_refused_not_defaulted(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from iteration_error_bound import Refusal, iteration_thresholds_from_case_spec  # noqa: PLC0415

        with self.assertRaises(Refusal) as caught:
            iteration_thresholds_from_case_spec({"nope": 1})
        self.assertEqual(caught.exception.reason_key, "THRESHOLDS_NOT_DECLARED")


class OpenItemsAreNotSilentlyClosed(unittest.TestCase):
    def test_contract_does_not_claim_the_method_is_accepted(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        open_items = " ".join(contract["open_items_not_closed_by_this_contract"])
        for token in ("实验参考与允许误差仍为 null", "严格相似网格族", "科学", "湍流"):
            self.assertIn(token, open_items, token)

    def test_provider_itself_refuses_to_claim_acceptance(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from iteration_error_bound import derive_iteration_error_bounds  # noqa: PLC0415

        result = derive_iteration_error_bounds(
            {"iteration_error_source": {"source_records": []}}, [], assert_citable=lambda path: {})
        self.assertFalse(result["method_accepted"])
        self.assertEqual(result["method_acceptance"], "PENDING_SCIENTIFIC_REVIEW")


if __name__ == "__main__":
    unittest.main()
