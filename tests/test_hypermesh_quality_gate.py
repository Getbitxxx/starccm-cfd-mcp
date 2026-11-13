# -*- coding: utf-8 -*-
"""AGENTS.md §2-O9：用户 2026-10-14 指出的能力落成可执行测试。

用户的要求是"HyperMesh 里有网格检查和坏网格定位，网格划定、检查、修改都尽量在 HyperMesh 内完成"。
它有两个可检查的后果，本模块各钉一条：

1. 失败的检查必须带失败单元定位（ID + 逐单元数值 + 坐标），否则记录不完整；
2. **定位缺失的记录不得被读成"HyperMesh 路线能力不足"**，因而不得成为切换后备工具的依据。

真实夹具（AGENTS.md §2-O9 要求优先用真实发生过的失败试跑）来自本轮实际失败的修复2候选 c：
`test-evidence/finite-trailing-edge-policy-20261014/hypermesh-native2d-surface-repair2-preparation/quality_gate.json`
——1668 个 2D 单元、Jacobian 门 .5 有 6 个失败（最小 -0.8855737221505027）、skew 门 70 有 5 个失败
（最坏 89.2783714710925），且 `native_full_percell_check_values_saved: false`（逐单元值没有保存）。
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from hypermesh_quality_gate import (ADMITTED, FAILED_CRITERIA_WITH_LOCALIZATION, LOCALIZATION_MISSING,  # noqa: E402
                                    RECORD_INCOMPLETE, AdmissionRefused, assert_admissible, evaluate)

REAL_QUALITY_GATE = (ROOT / 'test-evidence' / 'finite-trailing-edge-policy-20261014'
                     / 'hypermesh-native2d-surface-repair2-preparation' / 'quality_gate.json')


def criterion(name, command, label, direction, threshold, checked, failed, worst, localization=None):
    item = {'name': name, 'native_command': command, 'short_label': label, 'direction': direction,
            'threshold': threshold, 'checked': checked, 'failed': failed, 'worst': worst}
    if localization is not None:
        item['localization'] = localization
    return item


def localization(ids, coordinates=True, output_mark=2):
    block = {'output_mark': output_mark, 'failed_element_ids': list(ids),
             'per_element_values': {str(i): float(i) + 0.25 for i in ids}}
    if coordinates:
        block['coordinates_m'] = {str(i): [0.001 * i, -0.001 * i, -0.05] for i in ids}
    return block


def real_failed_record(**overrides):
    """本轮真实失败的修复2候选 c：数值取自实际记录，定位字段确实不存在。"""
    record = {
        'status': 'gate_failed',
        'scope': 'pre_STAR quality admission only',
        'tool': {'name': 'HyperMesh', 'version': '2023'},
        'source': {'file': 'CAD/finite-te.step', 'sha256': 'a' * 64},
        'mesh': {'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 2,
                 'element_count': 1668, 'units': 'm'},
        'criteria': [
            criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 6,
                      -0.8855737221505027),
            criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 5,
                      89.2783714710925),
        ],
        'localization_complete': False,
        'per_cell_values_saved': False,
        'native_full_percell_check_values_saved': False,
        'star_admitted': False, 'cfd_admitted': False, 'E_verified': False,
    }
    record.update(overrides)
    return record


class RealRepair2Fixture(unittest.TestCase):
    def test_the_cited_real_numbers_are_what_the_actual_record_says(self):
        actual = json.loads(REAL_QUALITY_GATE.read_text(encoding='utf-8'))
        record = real_failed_record()
        self.assertEqual(actual['native_Jacobian_failed'], record['criteria'][0]['failed'])
        self.assertEqual(actual['native_Jacobian_min'], record['criteria'][0]['worst'])
        self.assertEqual(actual['native_skew_failed'], record['criteria'][1]['failed'])
        self.assertEqual(actual['same_mesh_actual2D'], record['mesh']['element_count'])
        self.assertIs(actual['native_full_percell_check_values_saved'], False)

    def test_real_record_is_refused_because_localization_is_missing(self):
        verdict = evaluate(real_failed_record())
        self.assertEqual(verdict['decision'], LOCALIZATION_MISSING)
        self.assertFalse(verdict['quality_admitted'])
        self.assertEqual(sorted(verdict['failed_criteria']), ['jacobian', 'skew'])

    def test_missing_localization_cannot_be_read_as_a_hypermesh_capability_gap(self):
        verdict = evaluate(real_failed_record())
        self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
        self.assertTrue(any('cannot be cited as a HyperMesh capability gap' in reason
                            for reason in verdict['reasons']))

    def test_the_same_mesh_with_localization_does_qualify_as_a_targeted_repair_input(self):
        record = real_failed_record(
            localization_complete=True, per_cell_values_saved=True,
            mesh={'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 2, 'element_count': 1668,
                  'units': 'm', 'element_id_bounds': [1, 3000]},
            criteria=[
                criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 6,
                          -0.8855737221505027, localization([2001, 2002, 2003, 2004, 2005, 2006])),
                criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 5,
                          89.2783714710925, localization([2001, 2002, 2003, 2007, 2008])),
            ])
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], FAILED_CRITERIA_WITH_LOCALIZATION)
        self.assertFalse(verdict['quality_admitted'])
        self.assertTrue(verdict['can_claim_hypermesh_capability_gap'])
        self.assertEqual(verdict['repair_targets']['jacobian']['failed_element_ids'],
                         [2001, 2002, 2003, 2004, 2005, 2006])
        self.assertEqual(verdict['repair_targets']['jacobian']['output_mark'], 2)
        self.assertEqual(len(verdict['repair_targets']['skew']['coordinates_m']), 5)
        with self.assertRaises(AdmissionRefused):
            assert_admissible(record)

    def test_a_failure_may_not_be_reported_as_ok(self):
        verdict = evaluate(real_failed_record(status='ok'))
        self.assertFalse(verdict['quality_admitted'])
        self.assertTrue(any('claims status=ok' in reason for reason in verdict['reasons']))

    def test_a_record_may_not_claim_star_or_cfd_admission(self):
        verdict = evaluate(real_failed_record(cfd_admitted=True, star_admitted=True))
        self.assertFalse(verdict['quality_admitted'])
        self.assertTrue(any('cfd_admitted' in reason for reason in verdict['reasons']))
        self.assertFalse(verdict['star_admitted'])
        self.assertFalse(verdict['cfd_admitted'])


class LocalizationMustBeComplete(unittest.TestCase):
    def base(self, **criterion_overrides):
        block = {'output_mark': 2, 'failed_element_ids': [7, 8],
                 'per_element_values': {'7': 0.1, '8': 0.2},
                 'coordinates_m': {'7': [0.0, 0.0, -0.05], '8': [0.001, 0.0, -0.05]}}
        block.update(criterion_overrides)
        return real_failed_record(
            localization_complete=True, per_cell_values_saved=True,
            criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 2,
                                -0.5, block),
                      criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 0, 12.0)])

    def reject(self, record, needle):
        verdict = evaluate(record)
        self.assertFalse(verdict['quality_admitted'])
        self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
        self.assertTrue(any(needle in reason for reason in verdict['reasons'])
                        or any(needle in item.get('localization_problem', '')
                               for item in verdict['criteria']),
                        'expected %r in %s' % (needle, verdict['reasons'] + [i.get('localization_problem') for i in verdict['criteria']]))

    def test_wrong_number_of_ids_is_refused(self):
        self.reject(self.base(failed_element_ids=[7]), 'entries for 2 failing element(s)')

    def test_duplicate_ids_are_refused(self):
        self.reject(self.base(failed_element_ids=[7, 7]), 'duplicates')

    def test_nonpositive_ids_are_refused(self):
        self.reject(self.base(failed_element_ids=[0, 8]), 'positive integers')

    def test_missing_per_element_value_is_refused(self):
        self.reject(self.base(per_element_values={'7': 0.1}), 'lacks finite values')

    def test_missing_coordinate_is_refused(self):
        self.reject(self.base(coordinates_m={'7': [0.0, 0.0, -0.05]}), 'lacks 3 finite coordinates')

    def test_coordinates_are_optional_only_when_the_caller_says_so(self):
        record = self.base(coordinates_m=None)
        record['criteria'][0]['localization'].pop('coordinates_m')
        self.assertFalse(evaluate(record)['quality_admitted'])
        verdict = evaluate(record, require_coordinates=False)
        self.assertEqual(verdict['decision'], FAILED_CRITERIA_WITH_LOCALIZATION)

    def test_a_criterion_that_did_not_cover_the_whole_mesh_is_refused(self):
        record = self.base()
        record['criteria'][0]['checked'] = 1667
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
        self.assertTrue(any('checked=1667 != mesh.element_count=1668' in reason for reason in verdict['reasons']))

    def test_dimension_counts_let_a_record_cover_2d_and_3d_separately(self):
        # 独立审查 D-1（blocking）的验收面：声明了 dimension_counts 就必须**每个维度**都被覆盖。
        counts_mesh = {'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 3, 'element_count': 3000,
                       'units': 'm', 'dimension_counts': {2: 1668, 3: 1332}, 'element_id_bounds': [1, 3000]}
        three_d = criterion('jacobian_3d', '*elementtestjacobian', '3d_jacobian', 'min', 0.5, 1332, 1, -0.2,
                            localization([91]))
        three_d['dimension'] = 3
        two_d = criterion('jacobian_2d', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 0, 0.9)
        two_d['dimension'] = 2
        record = real_failed_record(
            mesh=counts_mesh, localization_complete=True, per_cell_values_saved=True,
            criteria=[three_d, two_d])
        self.assertEqual(evaluate(record)['decision'], FAILED_CRITERIA_WITH_LOCALIZATION)

        # D-1 最小复现：只查 2D 那 1668 个，3D 的 1332 个（44%）从未被任何准则覆盖 → 拒绝。
        partial = real_failed_record(mesh=counts_mesh, localization_complete=True, per_cell_values_saved=True,
                                     criteria=[dict(two_d)])
        verdict = evaluate(partial)
        self.assertNotEqual(verdict['decision'], ADMITTED)
        self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
        self.assertFalse(verdict['quality_admitted'])
        self.assertTrue(any('declared dimension(s) [3] are covered by no criterion' in reason
                            for reason in verdict['reasons']), verdict['reasons'])

        # 声明了 dimension_counts 却不声明维度：范围未知，拒绝。
        wrong = real_failed_record(
            mesh=counts_mesh,
            criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 0, 0.9)])
        self.assertEqual(evaluate(wrong)['decision'], RECORD_INCOMPLETE)


class ReviewFindingsBecomeTests(unittest.TestCase):
    """§2-O9：2026-10-14 独立对抗审查的 blocking/concer 当轮变成可执行测试。"""

    def bounded(self, **overrides):
        record = real_failed_record(
            localization_complete=True, per_cell_values_saved=True,
            mesh={'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 2, 'element_count': 1668,
                  'units': 'm', 'element_id_bounds': [1, 1668]},
            criteria=[
                criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 2, -0.5,
                          localization([7, 8])),
                criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 0, 12.0)])
        record.update(overrides)
        return record

    def test_c2_a_record_claiming_admission_cannot_be_cited_as_a_capability_gap(self):
        # 审查 C-2：同一记录既自称 STAR/CFD 准入、又被引用为 HM 能力缺口，自相矛盾（后者是 §5/§8
        # 切换 Gmsh 的书面依据）。
        for key in ('star_admitted', 'cfd_admitted', 'engineering_verified', 'CFD_admitted', 'E_verified'):
            with self.subTest(claim=key):
                record = self.bounded()
                record[key] = True
                verdict = evaluate(record)
                self.assertFalse(verdict['quality_admitted'])
                self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
                self.assertEqual(verdict['capability_gap_suppressed_by'], [key])

    def test_c2_a_claim_on_a_clean_record_still_cannot_be_admitted(self):
        record = self.bounded(
            status='ok',
            criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 0, 0.9),
                      criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 0, 12.0)])
        record['cfd_admitted'] = True
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
        self.assertTrue(any('claims admission flags cfd_admitted' in reason for reason in verdict['reasons']))

    def test_c6_failed_ids_without_declared_bounds_are_not_citable_as_a_gap(self):
        record = self.bounded(mesh={'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 2,
                                    'element_count': 1668, 'units': 'm'})
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], FAILED_CRITERIA_WITH_LOCALIZATION)
        self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
        self.assertEqual(verdict['capability_gap_suppressed_by'], ['unbounded_failed_element_ids'])

    def test_c6_an_id_outside_the_mesh_bounds_is_not_localization(self):
        record = self.bounded()
        record['criteria'][0]['localization']['failed_element_ids'] = [7, 999999]
        record['criteria'][0]['localization']['per_element_values'] = {'7': 0.1, '999999': 0.2}
        record['criteria'][0]['localization']['coordinates_m'] = {'7': [0.0, 0.0, -0.05], '999999': [1.0, 0.0, -0.05]}
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], LOCALIZATION_MISSING)
        self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
        problems = [item.get('localization_problem', '') for item in verdict['criteria']]
        self.assertTrue(any('outside mesh.element_id_bounds' in text for text in problems), problems)

    def test_c6_bad_bounds_are_refused_structurally(self):
        for bounds in ([0, 1668], [1668, 1], [1, 100], 'x', [1]):
            with self.subTest(bounds=bounds):
                record = self.bounded(mesh={'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 2,
                                            'element_count': 1668, 'units': 'm',
                                            'element_id_bounds': bounds})
                self.assertEqual(evaluate(record)['decision'], RECORD_INCOMPLETE)

    def test_r2_4_both_suppression_causes_survive_on_the_same_record(self):
        # 审查 R2-4：两个抑制原因同时成立时，记录里两个都要在（原先只留最后写入的一个）。
        record = self.bounded()
        record['cfd_admitted'] = True
        record['mesh'].pop('element_id_bounds')
        verdict = evaluate(record)
        self.assertFalse(verdict['can_claim_hypermesh_capability_gap'])
        self.assertEqual(sorted(verdict['capability_gap_suppressed_by']),
                         ['cfd_admitted', 'unbounded_failed_element_ids'])

    def test_r2_5_a_criterion_dimension_must_be_the_integer_2_or_3(self):
        # 审查 R2-5：2.0 / "2" / True / None 都不是维度声明。
        for dimension in (2.0, '2', True, None, 4):
            with self.subTest(dimension=dimension):
                record = real_failed_record(
                    mesh={'file': 'cap.hm', 'sha256': 'b' * 64, 'dimension': 3, 'element_count': 3000,
                          'units': 'm', 'dimension_counts': {2: 1668, 3: 1332}},
                    criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668,
                                        0, 0.9)])
                record['criteria'][0]['dimension'] = dimension
                verdict = evaluate(record)
                self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
                self.assertTrue(any('dimension must be the integer' in reason
                                    or 'covered by mesh.dimension_counts' in reason
                                    for reason in verdict['reasons']), verdict['reasons'])

    def test_r2_6_the_documented_dimension_rule_matches_the_implementation(self):
        # 审查 R2-6：政策原文说"必须显式写 dimension"，而实现只在该网格声明了 dimension_counts 时检查。
        # 二者必须一致，否则文档比实现严、读者会以为没写 dimension 的记录一定被拒。
        policy = (ROOT / 'docs' / 'mesh-tooling-policy.md').read_text(encoding='utf-8')
        self.assertIn('声明了 `dimension_counts`', policy)
        # 单维度网格不带 dimension 的记录仍可准入（实现口径），并有测试钉住。
        record = real_failed_record(
            status='ok', localization_complete=True, per_cell_values_saved=True,
            criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 0, 0.9)])
        self.assertEqual(evaluate(record)['decision'], ADMITTED)

    def test_c7_the_rules_name_the_commands_that_really_exist_on_this_install(self):
        # 审查 C-7：AGENTS §7 原文只写泛化的 `elementtest`，而该安装实测 `*elementtest` 与
        # `*elementtestvolumeskew` available=false。规则必须点名真正可用的命令，不能靠泛化名当能力。
        agents = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        for command in ('*elementtestjacobian', '*elementtestskew', '*elementtestaspect'):
            self.assertIn(command, agents, command)
        self.assertIn('*elementtestvolumeskew', agents, '不可用的命令必须写明，而不是省略')
        self.assertNotIn('原生 `elementtest` 检查', agents, '泛化命令名不得被当成可用能力')


class PassingAndIncomplete(unittest.TestCase):
    def passing(self, **overrides):
        record = real_failed_record(
            status='ok', localization_complete=True, per_cell_values_saved=True,
            criteria=[criterion('jacobian', '*elementtestjacobian', '2d_jacobian', 'min', 0.5, 1668, 0, 0.9),
                      criterion('skew', '*elementtestskew', '2d_skew', 'max', 70.0, 1668, 0, 21.0)])
        record.update(overrides)
        return record

    def test_a_complete_clean_record_is_admitted_for_the_next_independent_stage_only(self):
        verdict = evaluate(self.passing())
        self.assertEqual(verdict['decision'], ADMITTED)
        self.assertTrue(verdict['quality_admitted'])
        # 两个工具的质量指标互不替代（AGENTS.md §8）：本门永不给 STAR/CFD 背书。
        self.assertFalse(verdict['star_admitted'])
        self.assertFalse(verdict['cfd_admitted'])
        self.assertFalse(verdict['engineering_verified'])
        self.assertEqual(assert_admissible(self.passing())['decision'], ADMITTED)

    def test_all_passed_without_per_cell_values_is_not_an_admission(self):
        verdict = evaluate(self.passing(per_cell_values_saved=False))
        self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
        self.assertFalse(verdict['quality_admitted'])

    def test_a_nonfinite_worst_value_is_refused(self):
        record = self.passing()
        record['criteria'][0]['worst'] = float('nan')
        verdict = evaluate(record)
        self.assertEqual(verdict['decision'], RECORD_INCOMPLETE)
        self.assertTrue(any('must be finite' in reason for reason in verdict['reasons']))

    def test_missing_blocks_are_refused_not_crashed(self):
        for key in ('tool', 'source', 'mesh', 'criteria'):
            record = self.passing()
            record.pop(key)
            self.assertEqual(evaluate(record)['decision'], RECORD_INCOMPLETE, key)
        self.assertEqual(evaluate({'mesh': {}})['decision'], RECORD_INCOMPLETE)

    def test_duplicate_criterion_names_are_refused(self):
        record = self.passing()
        record['criteria'].append(dict(record['criteria'][0]))
        self.assertEqual(evaluate(record)['decision'], RECORD_INCOMPLETE)


class GeneralityAndSingleJudge(unittest.TestCase):
    """§4 通用性：判据本身不得依赖样本专属名；§2-O8：不得有第二份门槛实现。"""

    MODULE = ROOT / 'scripts' / 'hypermesh_quality_gate.py'
    AGENTS = ROOT / 'AGENTS.md'
    POLICY = ROOT / 'docs' / 'mesh-tooling-policy.md'

    def test_the_gate_hardcodes_no_sample_specific_name(self):
        text = self.MODULE.read_text(encoding='utf-8')
        for token in ('AIR3B_', 'NACA', 'naca', 'finite_te', 'R500c', 'PROPELLER', 'hmwork'):
            self.assertNotIn(token, text, token)

    def test_the_policy_is_discoverable_from_agents_and_names_the_single_judge(self):
        agents = self.AGENTS.read_text(encoding='utf-8')
        self.assertIn('docs/mesh-tooling-policy.md', agents)
        self.assertIn('scripts/hypermesh_quality_gate.py', agents)

    def test_documented_pins_match_disk(self):
        # 审查 R2-1（blocking）：本轮改了判据却漏钉它，pin 描述的是修复前的字节——"记下了但从不比对"。
        # 这条测试让陈旧 pin 无法静默发出；修法是把 pin 重算：python3 scripts/repin_progress_codified.py
        doc = json.loads((ROOT / 'progress.json').read_text(encoding='utf-8'))
        pins = doc['generality']['mesh_tooling_policy_20261014']['codified_in']
        self.assertTrue(pins)
        stale = {rel: (pinned, hashlib.sha256((ROOT / rel).read_bytes()).hexdigest())
                 for rel, pinned in pins.items()
                 if not (ROOT / rel).is_file()
                 or hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != pinned}
        self.assertEqual({}, stale, 'stale pins against disk; run scripts/repin_progress_codified.py')

    def test_the_policy_document_states_both_consequences(self):
        policy = self.POLICY.read_text(encoding='utf-8')
        self.assertIn('hypermesh_quality_gate.py', policy)
        self.assertIn('坏单元定位', policy)
        self.assertIn('Gmsh', policy)


if __name__ == '__main__':
    unittest.main()
