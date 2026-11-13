# -*- coding: utf-8 -*-
"""把独立审查 B1 钉住：validation 记录的 reference_policy 必须是**派生**的，不能是写死字符串。

B1 实测：把 CaseSpec 的参考写成可用后，写死的那句 "the reference is recorded as NOT_ESTABLISHED
and no relative error is claimed" 会与同一记录里的 reference_status=ESTABLISHED / relative_error
自相矛盾。而那份记录正是将来回答"Cd 与参考差多少"的证据。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACRO = ROOT / "macros/AuditExternalFlowConvergence.java"
CASE_SPEC = ROOT / "cases/sphere-aedc-m0p204-20261014/case_spec.json"


class ThePolicySentenceIsDerived(unittest.TestCase):
    def setUp(self):
        self.source = MACRO.read_text(encoding="utf-8")

    def test_the_record_writes_the_derived_variable(self):
        self.assertIn('+ ",\\"reference_policy\\":\\"" + escape(referencePolicy) + "\\""', self.source)

    def test_no_hardcoded_policy_literal_is_written_into_the_record(self):
        # 那句说明只能出现在三元表达式的"不可用"分支里，不能直接拼进 JSON
        self.assertEqual(1, len(re.findall(r"recorded as NOT_ESTABLISHED and no relative error is claimed", self.source)))
        record_line = [line for line in self.source.splitlines() if "reference_policy" in line and "escape(referencePolicy)" in line]
        self.assertEqual(1, len(record_line))

    def test_usability_accepts_an_explicit_flag_as_well_as_status(self):
        self.assertIn('optional(spec, "reference_established", "false")', self.source)
        self.assertIn('referenceStatus.equalsIgnoreCase("ESTABLISHED") || referenceEstablishedFlag', self.source)

    def test_the_wording_names_no_case(self):
        """通用宏里不得写死 AEDC/球体等样本名（AGENTS §4）。"""
        for token in ("AEDC", "TR-67-218", "sphere"):
            self.assertNotIn(token, self.source)


class TheCaseSpecCarriesWhatTheSentenceNeeds(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(CASE_SPEC.read_text(encoding="utf-8"))

    def test_reference_source_and_uncertainty_are_named(self):
        block = self.spec["engineering_target"]["drag_coefficient"]
        self.assertIn("reference_source", block)
        self.assertIn("reference_uncertainty_basis", block)

    def test_the_reference_is_usable_and_the_tolerance_is_declared(self):
        block = self.spec["engineering_target"]["drag_coefficient"]
        self.assertTrue(block["status"] == "ESTABLISHED" or block.get("reference_established") is True)
        self.assertIsInstance(block["value"], (int, float))
        self.assertEqual(0.0224, block["acceptance_tolerance"])

    def test_the_shared_imbalance_limit_is_declared_at_the_top_level(self):
        """两个宏各自默认值偶然相等不算声明（审查 concern）。"""
        self.assertEqual(1e-3, self.spec["mass_imbalance_ratio_limit"])

    def test_the_residual_gates_are_declared(self):
        gates = self.spec["residual_gates"]
        for key in ("continuity", "momentum", "energy"):
            self.assertIsInstance(gates[key], (int, float))

class TheTwoArmsAreWiredTheRightWayRound(unittest.TestCase):
    """审查者变异电池的三个存活项：M6 分支接反、M5 可用臂不读 CaseSpec、M8 删掉关键子句。"""

    def setUp(self):
        source = MACRO.read_text(encoding="utf-8")
        start = source.index("String referencePolicy = referenceUsable")
        self.statement = source[start:source.index(";", source.index("no relative error is claimed")) + 1]
        body = self.statement.split("?", 1)[1]          # 三元表达式的两个臂
        usable, unusable = re.split(r"\n\s*:\s", body, maxsplit=1)
        self.usable_arm = usable
        self.unusable_arm = unusable

    def test_the_usable_arm_does_not_claim_the_reference_is_unestablished(self):
        """M6：分支接反时，可用臂会输出 NOT_ESTABLISHED 措辞（原样重现 B1）。"""
        self.assertNotIn("NOT_ESTABLISHED", self.usable_arm)

    def test_the_unusable_arm_still_says_not_established(self):
        self.assertIn("NOT_ESTABLISHED", self.unusable_arm)

    def test_the_usable_arm_reads_the_source_and_uncertainty_from_the_case_spec(self):
        """M5：可用臂必须是读 CaseSpec 的，不能是固定占位串。"""
        self.assertIn('optional(spec, "reference_source"', self.usable_arm)
        self.assertIn('optional(spec, "reference_uncertainty_basis"', self.usable_arm)

    def test_the_comparison_disclaimer_is_present(self):
        """M8：删掉这个子句会让"比较"被读成"对参考的测量"。"""
        self.assertIn("not a measurement of it", self.usable_arm)


if __name__ == "__main__":
    unittest.main()


class TheStageDeadlineMustOutlastTheMacroBudget(unittest.TestCase):
    """2026-10-14 实测：宏只在 chunk 边界查预算，阶段上限若只比预算大一点，runner 会先杀掉宏、
    记录根本写不出来（第一次求解在 539.9 s 被杀，而宏的预算是 480 s）。"""

    def test_every_aedc_pipeline_leaves_room_for_one_chunk_overrun(self):
        """对整个 AEDC 家族的所有 pipeline 都成立，而不只是基准那一份。"""
        pipelines = sorted((ROOT / "cases").glob("sphere-aedc-*/pipeline-*.json"))
        self.assertTrue(pipelines, "找不到 AEDC 家族的 pipeline")
        for path in pipelines:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if "EXTERNAL_FLOW_BUDGET_SECONDS" not in (doc.get("env") or {}):
                continue
            budget = int(doc["env"]["EXTERNAL_FLOW_BUDGET_SECONDS"])
            solve = next((stage for stage in doc["stages"] if stage["name"] == "solve"), None)
            if solve is None:                     # 不含求解阶段的 pipeline 不适用这条不变量
                continue
            with self.subTest(pipeline=path.name):
                self.assertGreater(solve["timeout_seconds"], budget)
        # 余量必须覆盖"最坏一次 chunk 越界 + 收尾落盘"。2026-10-14 实测：单 chunk 最大 93.066 s
        # （iteration 200→250），失败运行的总时长 543.36 s 与最后一行 history 434.02 s 之差 ≈109 s
        # 为收尾上界 ⇒ 约 155 s；取 ≥200 s 作为下界，保证"测试绿"不会被 runner 先杀的组合绕过。
                self.assertGreaterEqual(solve["timeout_seconds"] - budget, 200)
