# -*- coding: utf-8 -*-
"""执行层时间预算撤销：时间只记录，不作停止或拒绝依据；资源门保留。

政策（AGENTS §6，用户 2026-10-14 明确撤销全部时间预算）此前只落在文档里，执行层未改，于是墙钟
真的停过一次求解（基准 run a 在 539.94 s 被杀）并污染过一次敏感性对照（两跑停在 600 vs 500 迭代）。
本测试把撤销钉住：任何一处重新引入"按时间拒绝/杀进程"都会让它变红。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/security/trial_air3b_pipeline.ps1"
PROVISIONER = ROOT / "scripts/security/provision_restricted_worker.ps1"
CONTROLLER = ROOT / "scripts/air3b_pipeline_trial.py"
MACRO = ROOT / "macros/RunExternalFlowSolve.java"
DRIVER = ROOT / "scripts/run_static_external_flow_e2e.py"


class NoWallClockStopOrRefusal(unittest.TestCase):
    def setUp(self):
        self.runner = RUNNER.read_text(encoding="utf-8")
        self.controller = CONTROLLER.read_text(encoding="utf-8")
        self.macro = MACRO.read_text(encoding="utf-8")
        self.provisioner = PROVISIONER.read_text(encoding="utf-8")

    def test_the_runner_no_longer_kills_on_elapsed_time(self):
        self.assertNotIn("Owned STAR timeout", self.runner)
        # 资源异常仍然要杀——这是资源门，不是时间预算
        self.assertIn("Owned STAR resource limit exceeded", self.runner)

    def test_the_runner_no_longer_refuses_a_request_over_time(self):
        for banned in ("Declared stage deadlines", "budget exhausted", "Invalid stage deadline",
                       "Invalid default stage deadline", "Task budget exceeds resource outer deadline"):
            self.assertNotIn(banned, self.runner, "runner 仍会按时间拒绝：%s" % banned)
        self.assertIn("timeBudgetObservations", self.runner)

    def test_the_wait_loop_cannot_kill_on_time(self):
        """M8（审查者实测漏网）：把到点杀进程加回等待循环、但换一条不在禁用词表里的消息，原测试全绿。

        结构改钉**意图**而不是词表：等待循环里允许并且只允许两条 throw —— ①健康看门狗（触发条件必须
        是"可观察进展的时间戳没有更新"），②资源门（RSS / 空闲内存）。任何一条 throw 的触发条件里
        出现"经过时间"（$sw.Elapsed…）或阶段上限（$limitSeconds）即为按时间杀进程的回潮。
        """
        start = self.runner.index("while(!$proc.WaitForExit(250)){")
        end = self.runner.index("$proc.WaitForExit()", start)
        loop = self.runner[start:end]
        # 只看代码：注释里可能出现 "throw" 这个词（本项目已经因此误判过一次）
        loop = "\n".join(line.split("#")[0] for line in loop.splitlines())
        throws = [m.start() for m in re.finditer(r"\bthrow\b", loop)]
        self.assertEqual(2, len(throws), "等待循环里的 throw 数应为 2（看门狗 + 资源门），实际 %d" % len(throws))
        for position in throws:
            context = loop[max(0, position - 400):position]
            self.assertNotIn("$sw.Elapsed", context, "循环里的 throw 由经过时间触发 —— 按时间杀进程回潮")
            self.assertNotIn("$limitSeconds", context, "循环里的 throw 由阶段上限触发 —— 按时间杀进程回潮")
        self.assertIn("no observable progress", loop, "看门狗 throw 不见了")
        self.assertIn("Owned STAR resource limit exceeded", loop, "资源门 throw 不见了")
        self.assertIn("$lastEvidenceChange", loop, "看门狗不再依据进展时间戳判定")

    def test_the_health_watchdog_exists_and_is_not_a_runtime_cap(self):
        self.assertIn("HEALTH WATCHDOG", self.runner)
        self.assertIn("no observable progress", self.runner)
        self.assertIn("health_watchdog_seconds", self.runner)
        # 它必须只看"有无进展"，不得退化成总时长上限：窗口来自 env 且有上下界校验
        self.assertIn("EXTERNAL_FLOW_HEALTH_WINDOW_SECONDS", self.runner)

    def test_the_resource_gates_survive(self):
        for kept in ("Invalid contract core budget", "Invalid contract memory budget",
                     "Declared memory budget plus 16GB reserve unavailable",
                     "Restricted worker identity mismatch", "Invalid trial directory"):
            self.assertIn(kept, self.runner, "资源/身份门被误删：%s" % kept)

    def test_the_controller_no_longer_clamps_the_declared_sum(self):
        self.assertNotIn("min(1800", self.controller)

    def test_the_scheduled_task_has_no_execution_time_limit(self):
        self.assertIn("[TimeSpan]::Zero", self.provisioner)

    def test_the_macro_defaults_to_no_wall_clock_stop(self):
        self.assertIn('env("EXTERNAL_FLOW_BUDGET_SECONDS", 0.0)', self.macro)
        self.assertIn("if (budgetSeconds > 0 && budgetClock >= budgetSeconds)", self.macro)
        self.assertNotIn('env("EXTERNAL_FLOW_BUDGET_SECONDS", 540.0)', self.macro)

    def test_the_driver_timeout_is_documented_as_observer_only(self):
        self.assertIn("observer-only poll timeout", DRIVER.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class TheWatchdogEvidenceIsDerivedNotHardcoded(unittest.TestCase):
    """独立审查 2026-10-14：看门狗的进展证据文件曾是写死的三个名字（宏日志 +
    external_flow_solve_history.csv + external_flow_solved.sim）。那对求解阶段正确，但 ②MRF/③旋转机械的
    产物名不同，看门狗会因"看不到这三个名字"在观察窗内误杀。判据：证据列表必须由 stage 的
    required / macro_outputs 派生，且派生结果写进记录。"""

    def setUp(self):
        self.runner = (ROOT / 'scripts/security/trial_air3b_pipeline.ps1').read_text(encoding='utf-8')

    def test_the_watchdog_takes_a_derived_evidence_list(self):
        self.assertIn('[string[]]$progressEvidence=@()', self.runner)
        self.assertIn('$stageEvidence', self.runner)
        self.assertIn('progress_evidence=@($progressEvidence)', self.runner)

    def test_no_hardcoded_solve_artifact_names_remain_in_the_watchdog(self):
        start = self.runner.index('HEALTH WATCHDOG')
        end = self.runner.index('Owned STAR health watchdog')
        watchdog = self.runner[start:end]
        # 只看代码：注释里会出现被替换掉的旧名字（本项目已因"注释里引用旧串"误报过两次）
        watchdog = "\n".join(line.split('#')[0] for line in watchdog.splitlines())
        for name in ('external_flow_solve_history.csv', 'external_flow_solved.sim'):
            self.assertNotIn(name, watchdog,
                             '看门狗仍写死求解阶段的产物名 %s；②/③ 会误杀' % name)

    def test_the_caller_derives_it_from_required_and_macro_outputs(self):
        self.assertIn('foreach($required in @($stage.required))', self.runner)
        self.assertIn('foreach($declared in @($outputList))', self.runner)
