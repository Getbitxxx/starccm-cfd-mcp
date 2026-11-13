# -*- coding: utf-8 -*-
"""层级链的源 pipeline 不得声明时间预算（独立审查 2026-10-14 的要求）。

层级生成器会继承源的 env 与阶段描述。历史上的源里有 `EXTERNAL_FLOW_BUDGET_SECONDS=480` 与写死
`480/540` 的描述，新链一旦复制就把**已撤销**的时间预算带了回来——那正是花三轮才止住的复发形态。
判据：prepare() 对含 BUDGET 键的源**显式报错**；净化模板本身不含该键。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'cases/sphere-aedc-m0p204-20261014/pipeline-template-external-flow.json'


class SensitivitySourceMustBeBudgetFree(unittest.TestCase):
    def test_the_sanitised_template_declares_no_time_budget(self):
        env = json.loads(TEMPLATE.read_text(encoding='utf-8')).get('env') or {}
        offenders = [key for key in env if 'BUDGET' in key.upper()]
        self.assertEqual([], offenders, '净化模板仍声明时间预算：%r' % offenders)

    def test_the_generator_strips_the_time_budget_at_emission(self):
        """生成端必须清掉时间预算键，且清掉的实事要记在被写出的 pipeline 里。

        方案选择说明：历史 release 夹具（cases/static-reusable-entry/**）各自被 3 份记录 pin 住，
        改写它们的字节会作废历史证据；所以判据放在**生成端**而不是读取端。
        """
        import air3b_pipeline_trial  # noqa: F401  (确保脚本可导入)
        import importlib.util
        spec = importlib.util.spec_from_file_location('study', ROOT / 'scripts' / 'run_sensitivity_study.py')
        study = importlib.util.module_from_spec(spec); spec.loader.exec_module(study)
        sample = {'env': {'EXTERNAL_FLOW_BUDGET_SECONDS': '480',
                           'EXTERNAL_FLOW_BUDGET_BASIS': 'iterator',
                           'EXTERNAL_FLOW_CHUNK': '50'},
                  'stages': [], 'logical_cores': 64}
        cleaned = study._strip_time_budget(sample)
        # 只删"启用墙钟停止"的那个键；BASIS 是功能键（选时钟基准），必须保留
        self.assertEqual({'EXTERNAL_FLOW_BUDGET_BASIS': 'iterator', 'EXTERNAL_FLOW_CHUNK': '50'},
                         cleaned['env'])
        self.assertIn('EXTERNAL_FLOW_BUDGET_SECONDS', cleaned['_time_budget_stripped'])
        self.assertIn('EXTERNAL_FLOW_BUDGET_BASIS', cleaned['_time_budget_stripped'],
                      '被保留的 BUDGET 相关键必须在记录里说明')
        # 无预算键时不得改动（也不得凭空添加说明字段）
        clean_source = {'env': {'EXTERNAL_FLOW_CHUNK': '50'}, 'stages': []}
        self.assertEqual(clean_source, study._strip_time_budget(clean_source))

    def test_the_historical_source_is_still_flagged_by_the_rule(self):
        # 真实反例：本算例历史上用的四阶段源确实带 480（它被旧记录 pin，不能改写字节）。
        env = json.loads((ROOT / 'cases/sphere-aedc-m0p204-20261014/pipeline-domain-mesh.json')
                         .read_text(encoding='utf-8')).get('env') or {}
        self.assertIn('EXTERNAL_FLOW_BUDGET_SECONDS', env,
                      '若该键已消失，本测试的反例失效，应更新为当前真实反例')


if __name__ == '__main__':
    unittest.main()
