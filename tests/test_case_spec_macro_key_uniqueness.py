# -*- coding: utf-8 -*-
"""宏可见键在 CaseSpec 里必须各只出现一次。

背景（独立审查两轮抓到、我修第一版时又引入的新实例）：宏用**全文首个正则**读 CaseSpec
（`number()/optional()/optionalNumber()/string()/array()`），若同一个键在文件里出现两次，
宏读到的是**先出现**的那个值。所以：

* `quality.mesh_study.baseline_level` 里若复用 `mesh_base_size_m`，宏会读到基准档的尺寸，
  两条层级链会**静默建出基准网格**——照常写 status=ok、通过全部判据，研究却毫无意义；
* `BuildExternalFlowMesh` 的重复检测只覆盖两个棱柱键，这三个键走 number() 直接读，**不抛错**。

本测试从宏源码动态抽取键集合，然后对研究 spec 与由它生成的每个层级 spec 计数，>1 即失败。
"""
from __future__ import annotations

import glob
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MACRO_CALL = re.compile(r'\b(?:string|number|optional|optionalNumber|array)\s*\(\s*\w+\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"')


CASE_PIPELINES = ("cases/sphere-aedc-m0p204-20261014/pipeline-domain-mesh.json",
                  "cases/sphere-aedc-m0p204-20261014/pipeline-solve-validation.json")


def case_macros() -> set[str]:
    """本算例 pipeline 实际用到的宏（不是全仓宏）。"""
    names: set[str] = set()
    for relative in CASE_PIPELINES:
        doc = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        names.update(doc.get("macro_hashes") or {})
    return names


def macro_visible_keys() -> set[str]:
    keys: set[str] = set()
    for name in case_macros():
        path = ROOT / "macros" / name
        if path.is_file():
            keys.update(MACRO_CALL.findall(path.read_text(encoding="utf-8")))
    return keys


def duplicated_keys(document: str, keys: set[str]) -> dict[str, int]:
    counts = {}
    for key in keys:
        n = len(re.findall(r'"%s"\s*:' % re.escape(key), document))
        if n > 1:
            counts[key] = n
    return counts


class MacroVisibleKeysAppearOnce(unittest.TestCase):
    def setUp(self):
        self.keys = macro_visible_keys()
        self.assertTrue(len(self.keys) > 20, "宏键抽取失败：%d" % len(self.keys))

    def test_the_study_spec_has_no_duplicate_macro_visible_key(self):
        path = ROOT / "cases/sphere-aedc-m0p204-20261014/case_spec-study.json"
        duplicates = duplicated_keys(path.read_text(encoding="utf-8"), self.keys)
        self.assertEqual({}, duplicates, "研究 spec 里出现重复的宏可见键：%r" % duplicates)

    def test_every_generated_level_spec_has_no_duplicate_macro_visible_key(self):
        paths = sorted((ROOT / "test-evidence/engineering-validation-start-20261009/"
                               "e-aedc-sphere-m0p204").glob("*-study/*/case_spec.json"))
        self.assertTrue(paths, "找不到层级 spec")
        for path in paths:
            duplicates = duplicated_keys(path.read_text(encoding="utf-8"), self.keys)
            with self.subTest(level=str(path.relative_to(ROOT))):
                self.assertEqual({}, duplicates, "%s：%r" % (path.name, duplicates))

    def test_the_baseline_blocks_use_prefixed_keys(self):
        """baseline 块的键必须加前缀，不能落在宏可见键空间里。"""
        doc = json.loads((ROOT / "cases/sphere-aedc-m0p204-20261014/case_spec-study.json")
                         .read_text(encoding="utf-8"))
        for key in ("mesh_study", "domain_study"):
            baseline = doc["quality"][key]["baseline_level"]
            declared = set(baseline)
            self.assertFalse(declared & self.keys,
                             "%s.baseline_level 里有宏可见键：%r" % (key, sorted(declared & self.keys)))


if __name__ == "__main__":
    unittest.main()


class StageDeadlinesRespectTheRunnerCap(unittest.TestCase):
    """2026-10-14 实测（链 B 全部失败）：runner 在**没有** resource_contract 时对单阶段上限有一条
    硬门 `$deadlineCap=1500`（trial_air3b_pipeline.ps1:49 与 :84），我此前只核了控制器侧的
    min(1800, ...)，于是把 solve 设成 1620 ⇒ runner 抛 'Invalid stage deadline'、退出码 1、
    作业目录空白（任务跑过、什么都不写）。

    不变量：每条 pipeline 的每个阶段上限要么 ≤1500，要么该 pipeline 声明 resource_contract 且
    outer_timeout_seconds ≥ 最大阶段上限（契约路径会把 deadlineCap 抬到 outer）。"""

    RUNNER_CAP = 1500
    CASE_DIR = ROOT / "test-evidence/engineering-validation-start-20261009/e-aedc-sphere-m0p204"

    def test_every_case_pipeline_satisfies_the_runner_stage_cap(self):
        pipelines = sorted(path for path in self.CASE_DIR.glob("*-study/*/pipeline*.json")
                           if "review" not in path.name)
        self.assertTrue(pipelines, "找不到算例 pipeline")
        for path in pipelines:
            doc = json.loads(path.read_text(encoding="utf-8"))
            deadlines = [s["timeout_seconds"] for s in doc["stages"]]
            contract = doc.get("resource_contract")
            with self.subTest(pipeline=str(path.relative_to(ROOT))):
                if contract is None:
                    self.assertLessEqual(max(deadlines), self.RUNNER_CAP)
                else:
                    self.assertGreaterEqual(contract["outer_timeout_seconds"], max(deadlines))
                    # runner 会逐项比对契约与请求的核数/内存
                    self.assertEqual(contract["logical_cores"], doc.get("logical_cores"))
                    self.assertEqual(contract["memory_gb"], doc.get("memory_gb"))
                    self.assertGreaterEqual(doc["task_budget_seconds"], sum(deadlines) + 60)
                    # 第四条门（审查者指出，我此前只核了控制器侧）：
                    # 契约存在时 controller 把 timeout_seconds 设为 task_budget_seconds，而 runner 在
                    # trial_air3b_pipeline.ps1:76 要求 timeout_seconds <= outer_timeout_seconds。
                    self.assertLessEqual(doc["task_budget_seconds"],
                                         contract["outer_timeout_seconds"])
