# -*- coding: utf-8 -*-
"""Tests for the AGENTS.md executable part (scripts/progress_matrix.py).

These tests exist because the doc alone did not bind: the anti-drift rules were prose and were
violated. The checks are now code, so the code needs to be held to the same standard as the rest of
the repo. Each test below corresponds to a clause of AGENTS.md and would fail if that clause were
quietly weakened.
"""
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

spec = importlib.util.spec_from_file_location('progress_matrix', ROOT / 'scripts' / 'progress_matrix.py')
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)


def base_doc():
    """A minimal, all-green document: ① passed, so the §3 ordering gate is not what is under test."""
    stages = {n: {'status': 'PASSED', 'evidence': []} for n in pm.SCENARIO_KEYS and
              ['case_spec', 'geometry', 'mesh', 'solve', 'validation']}
    return {
        'updated': '2026-01-01',
        'stage_names': ['case_spec', 'geometry', 'mesh', 'solve', 'validation'],
        'counters': {'pipeline_verified_P': '3/3', 'engineering_validated_E': '0/3'},
        'scenarios': {
            '1_static_external_flow': {'pipeline': 'PASSED', 'engineering': 'NOT_STARTED', 'stages': stages},
            '2_steady_mrf_propeller': {'pipeline': 'PASSED', 'engineering': 'NOT_STARTED', 'stages': stages},
            '3_transient_rotation': {'pipeline': 'PASSED', 'engineering': 'NOT_STARTED', 'stages': stages},
        },
        'generality': {'matrix_status': 'PASSED', 'operations': []},
        'budget': {'per_case': {'case-a': {
            'rounds_since_stage_advance': 0, 'limit': 3,
            'sample_specific_work_active': False,
            'last_round': {'advanced_stage': True,
                           'previous_rounds_since_stage_advance': 0}}}},
    }


class CountersTest(unittest.TestCase):
    """§1: two counters, derived from the matrix, never merged into one number."""

    def test_counters_are_derived_from_the_matrix(self):
        doc = base_doc()
        doc['scenarios']['3_transient_rotation']['pipeline'] = 'PARTIAL'
        doc['scenarios']['2_steady_mrf_propeller']['engineering'] = 'PASSED'
        self.assertEqual({'pipeline_verified_P': '2/3', 'engineering_validated_E': '1/3'},
                         pm.counters(doc))

    def test_a_stale_declared_counter_blocks(self):
        doc = base_doc()
        doc['counters']['pipeline_verified_P'] = '0/3'   # claims less than the matrix shows
        blocking = pm.check(doc)['blocking']
        self.assertTrue(any('§1' in item for item in blocking), blocking)

    def test_a_missing_engineering_counter_blocks(self):
        doc = base_doc()
        del doc['counters']['engineering_validated_E']
        self.assertTrue(any('§1' in item for item in pm.check(doc)['blocking']))

    def test_a_single_merged_progress_number_is_flagged(self):
        doc = base_doc()
        doc['counters']['engineering_validated_E'] = ''
        self.assertTrue(any('不得只报一个进度数字' in item for item in pm.check(doc)['warnings']))


class BudgetTest(unittest.TestCase):
    """§2-O4: a sample may not absorb effort without a stopping condition."""

    def test_exceeding_the_round_limit_blocks(self):
        doc = base_doc()
        entry = doc['budget']['per_case']['case-a']
        entry['rounds_since_stage_advance'] = 4
        entry['last_round'] = {'advanced_stage': False, 'previous_rounds_since_stage_advance': 3}
        self.assertTrue(any('§2-O4' in item and '超过上限' in item for item in pm.check(doc)['blocking']))

    def test_advancing_a_stage_resets_the_counter(self):
        # Closing a scenario legitimately needs several rounds on the same case; a round that advanced
        # a stage must not count against the sample budget. This must assert the ABSENCE of any O4
        # blocking, otherwise deleting the reset would still leave the suite green.
        doc = base_doc()
        entry = doc['budget']['per_case']['case-a']
        entry['rounds_since_stage_advance'] = 0
        entry['last_round'] = {'advanced_stage': True, 'previous_rounds_since_stage_advance': 5}
        blocking = pm.check(doc)['blocking']
        self.assertEqual([], [item for item in blocking if '§2-O4' in item], blocking)
        # ...and the reset must be the reason: without it the same bookkeeping would be inconsistent.
        entry['last_round']['advanced_stage'] = False
        self.assertTrue(any('§2-O4' in item for item in pm.check(doc)['blocking']))

    def test_every_non_advancing_round_counts_regardless_of_what_it_did(self):
        # This is the rule change: a round spent on generality, validation or auditing with no stage
        # advancement used to be free. It is not any more.
        doc = base_doc()
        entry = doc['budget']['per_case']['case-a']
        entry['rounds_since_stage_advance'] = 2
        entry['last_round'] = {'advanced_stage': False, 'previous_rounds_since_stage_advance': 1}
        warnings = pm.check(doc)['warnings']
        self.assertTrue(any('没有任何阶段推进（报警线）' in item for item in warnings), warnings)

    def test_inconsistent_bookkeeping_blocks(self):
        # The counter must be derivable from the previous round's declaration, otherwise it is just a
        # number someone typed.
        doc = base_doc()
        entry = doc['budget']['per_case']['case-a']
        entry['rounds_since_stage_advance'] = 2
        entry['last_round'] = {'advanced_stage': False, 'previous_rounds_since_stage_advance': 5}
        self.assertTrue(any('记账不自洽' in item for item in pm.check(doc)['blocking']))

    def test_the_active_flag_is_reported_as_a_declaration(self):
        doc = base_doc()
        self.assertTrue(any('声明' in item and '不是校验' in item
                            for item in pm.check(doc)['warnings']))

    def test_hitting_the_limit_warns(self):
        doc = base_doc()
        entry = doc['budget']['per_case']['case-a']
        entry['rounds_since_stage_advance'] = 3
        entry['last_round'] = {'advanced_stage': False, 'previous_rounds_since_stage_advance': 2}
        result = pm.check(doc)
        self.assertFalse(any('§2-O4' in item and '超过上限' in item for item in result['blocking']))
        self.assertTrue(any('§2-O4' in item for item in result['warnings']))

    def test_a_missing_budget_is_not_silently_ok(self):
        doc = base_doc()
        doc['budget'] = {'per_case': {}}
        self.assertTrue(any('§2-O4' in item for item in pm.check(doc)['warnings']))


class LiveDocumentTest(unittest.TestCase):
    """The live progress.json must carry the O4 bookkeeping the rule reads.

    An independent review found the rule was correct but inert: progress.json still used the old
    field names, so the check only emitted warnings and never evaluated anything. This test reads the
    real file, so a regression in the document is caught instead of silently disabling the rule.
    """

    def test_the_live_budget_uses_the_counted_fields(self):
        doc = pm.load()
        per_case = doc['budget']['per_case']
        self.assertTrue(per_case)
        for case, entry in per_case.items():
            with self.subTest(case=case):
                self.assertIn('rounds_since_stage_advance', entry)
                self.assertIn('limit', entry)
                last = entry.get('last_round')
                self.assertIsInstance(last, dict, 'last_round must be present so the count is derivable')
                self.assertIsInstance(last.get('advanced_stage'), bool)
                previous = last.get('previous_rounds_since_stage_advance')
                self.assertIsInstance(previous, int)
                expected = 0 if last['advanced_stage'] else previous + 1
                self.assertEqual(expected, entry['rounds_since_stage_advance'])

    def test_real_engineering_round_limit_alias_does_not_disable_accounting(self):
        fixture = ROOT / 'test-evidence/static-release-candidate/engineering-execution-20261007/round-limit-real-counterexample.json'
        observed = json.loads(fixture.read_text())
        doc = base_doc()
        doc['budget']['per_case'] = {observed['case']: observed['entry']}
        warnings = pm.check(doc)['warnings']
        self.assertTrue(any('缺 rounds_since_stage_advance/limit' in item for item in warnings), warnings)
        repaired = pm.load()['budget']['per_case'][observed['case']]
        self.assertEqual(observed['entry']['round_limit'], repaired['limit'])
        doc['budget']['per_case'] = {observed['case']: repaired}
        warnings = pm.check(doc)['warnings']
        self.assertFalse(any('缺 rounds_since_stage_advance/limit' in item for item in warnings), warnings)

    def test_the_live_budget_labels_the_declaration(self):
        doc = pm.load()
        for case, entry in doc['budget']['per_case'].items():
            with self.subTest(case=case):
                self.assertIn('DECLARATION', entry['sample_specific_work_active_status'])
        self.assertIn('DECLARATION',
                      json.dumps(doc['budget'].get('_fields_are_declarations', '')))

    def test_any_live_o4_block_is_backed_by_a_recorded_exception(self):
        """规则必须**生效**，超额必须**可见**，但每一条 blocking 都要有已记录的例外说明。

        这条测试原先断言"live 文档不得出现 O4 blocking"。2026-10-14 的通用性轮发现该断言与事实冲突：
        AEDC 算例在第 4 轮之后连续 8 轮没有推进任何阶段（TI 对照、敏感性、撤销验证），按 limit=3 就是超额。
        把计数填成合格值会让规则变成装饰；保持缺失字段则规则根本不评估（这正是本轮要修的病）。
        因此改成：允许 blocking，但必须能在对应算例的 `o4_exception` 里找到授权与下一轮承诺。
        """
        doc = pm.load()
        blocking = [item for item in pm.check(doc)['blocking'] if '§2-O4' in item]
        for item in blocking:
            cases = [case for case in doc['budget']['per_case'] if case in item]
            self.assertTrue(cases, '无法从 blocking 文本里定位算例：%s' % item)
            for case in cases:
                entry = doc['budget']['per_case'][case]
                self.assertIsInstance(entry.get('limit'), int)
                self.assertGreater(entry['rounds_since_stage_advance'], entry['limit'],
                                   '%s 被 blocking 但计数并未超额' % case)
                exception = entry.get('o4_exception')
                self.assertIsInstance(exception, dict, '%s 超额却没有任何 o4_exception 记录' % case)
                for key in ('authorized_by', 'commitment', 'not_a_waiver'):
                    self.assertIn(key, exception, '%s 的 o4_exception 缺 %s' % (case, key))
                self.assertEqual(3, entry['limit'], '例外不得以调大 limit 的方式实现')
        # 规则的判定必须与声明的计数一致：把它改成合格值就必须让 blocking 消失（证明规则真的在算）
        for case, entry in doc['budget']['per_case'].items():
            with self.subTest(case=case):
                previous = (entry.get('last_round') or {}).get('previous_rounds_since_stage_advance')
                advanced = (entry.get('last_round') or {}).get('advanced_stage')
                if isinstance(previous, int) and isinstance(advanced, bool):
                    self.assertEqual(0 if advanced else previous + 1,
                                     entry['rounds_since_stage_advance'])


class ScenarioOrderTest(unittest.TestCase):
    """§3: the cheapest scenario closes first; no new sample-specific work before that."""

    def test_sample_specific_work_blocks_while_scenario_one_is_unclosed(self):
        doc = base_doc()
        doc['scenarios']['1_static_external_flow']['pipeline'] = 'NOT_STARTED'
        doc['budget']['per_case']['case-a']['sample_specific_work_active'] = True
        self.assertTrue(any('§3' in item for item in pm.check(doc)['blocking']))

    def test_no_block_when_no_sample_specific_work_is_active(self):
        doc = base_doc()
        doc['scenarios']['1_static_external_flow']['pipeline'] = 'NOT_STARTED'
        self.assertFalse(any('§3' in item for item in pm.check(doc)['blocking']))

    def test_no_block_once_scenario_one_passes(self):
        doc = base_doc()
        doc['scenarios']['1_static_external_flow']['pipeline'] = 'PASSED'
        self.assertFalse(any('§3' in item for item in pm.check(doc)['blocking']))

    def test_the_real_repository_state_honours_the_ordering_invariant(self):
        # This test used to assert that the genuine progress.json RAISES the §3 block ("① has never
        # run while the propeller sample is still active"). It fired on 2026-10-03: the first
        # scenario-1 round ran and no sample-specific work was advanced, so every per-case
        # sample_specific_work_active honestly became false and the block correctly stopped firing.
        # The tripwire therefore pinned a snapshot, not the rule. It now asserts the rule on the real
        # state and additionally proves the gate still fires when the rule is violated, so turning
        # any case's flag back on while ① is open makes this test fail again.
        doc = pm.load()
        first = doc['scenarios']['1_static_external_flow']['pipeline']
        if first == 'PASSED':
            self.skipTest('① has passed track P: the ordering gate no longer applies')
        blocking = [item for item in pm.check(doc)['blocking'] if '§3' in item]
        self.assertEqual([], blocking,
                         '① has not passed but sample-specific work is active: ' + repr(blocking))
        violated = json.loads(json.dumps(doc))
        for case in violated.get('budget', {}).get('per_case', {}):
            violated['budget']['per_case'][case]['sample_specific_work_active'] = True
        self.assertTrue(any('§3' in item for item in pm.check(violated)['blocking']),
                        'the §3 ordering gate no longer fires at all')


class GeneralityTest(unittest.TestCase):
    """§4: an operation that depends on fixed names is not a general operation."""

    def test_fixed_name_dependency_cannot_be_promoted(self):
        doc = base_doc()
        doc['generality']['operations'] = [
            {'name': 'op-x', 'status': 'GENERAL_OPERATION', 'depends_on_fixed_names': True,
             'positive_case': 'a', 'negative_case': 'b'}]
        self.assertTrue(any('§4' in item for item in pm.check(doc)['blocking']))

    def test_promotion_requires_both_a_positive_and_a_negative_case(self):
        doc = base_doc()
        doc['generality']['operations'] = [
            {'name': 'op-y', 'status': 'GENERAL_OPERATION', 'depends_on_fixed_names': False,
             'positive_case': 'a', 'negative_case': ''}]
        self.assertTrue(any('§4' in item for item in pm.check(doc)['blocking']))


class ValidationTest(unittest.TestCase):
    def test_illegal_status_blocks(self):
        doc = base_doc()
        doc['scenarios']['2_steady_mrf_propeller']['pipeline'] = 'PROBABLY_FINE'
        self.assertTrue(any('§2-O1' in item for item in pm.check(doc)['blocking']))

    def test_missing_scenario_blocks(self):
        doc = base_doc()
        del doc['scenarios']['1_static_external_flow']
        self.assertTrue(any('§2-O1' in item for item in pm.check(doc)['blocking']))

    def test_missing_stage_blocks(self):
        doc = base_doc()
        del doc['scenarios']['3_transient_rotation']['stages']['validation']
        self.assertTrue(any('§2-O1' in item for item in pm.check(doc)['blocking']))

    def test_render_shows_both_counters(self):
        text = pm.render(base_doc())
        self.assertIn('流程验收 P = 3/3', text)
        self.assertIn('工程验证 E = 0/3', text)


class MacroLintTest(unittest.TestCase):
    """§4 lint is advisory only; it must never be the thing that blocks or passes a release."""

    def test_lint_reports_the_sample_specific_macros(self):
        findings = pm.lint_macros()
        self.assertTrue(any('RunAir3bTransientRevolution.java' in item for item in findings), findings)

    def test_lint_never_contributes_to_blocking(self):
        doc = base_doc()
        doc['scenarios']['1_static_external_flow']['pipeline'] = 'PASSED'
        self.assertEqual([], pm.check(doc)['blocking'])


if __name__ == '__main__':
    unittest.main()

class TheO4ExceptionCannotBecomeAGeneralWaiver(unittest.TestCase):
    """独立审查裁定：例外必须有署名+日期、到期轮次，且生效期间该算例不活跃并停提交。

    缺任一条，超额的 blocking 必须回来——否则"写一份 exception 就能让任何超额通过"。
    """

    def setUp(self):
        self.doc = pm.load()
        self.case = 'sphere-aedc-m0p204-20261014'
        self.entry = self.doc['budget']['per_case'][self.case]

    def _blocking(self):
        return [i for i in pm.check(self.doc)['blocking'] if '§2-O4' in i and self.case in i]

    def test_a_complete_exception_downgrades_the_block(self):
        self.assertEqual([], self._blocking())

    def test_a_missing_authorizer_signature_restores_the_block(self):
        self.entry['o4_exception'].pop('authorized_by')
        self.assertTrue(self._blocking())

    def test_an_authorizer_without_a_date_restores_the_block(self):
        self.entry['o4_exception']['authorized_by'] = 'someone'
        self.assertTrue(self._blocking())

    def test_a_missing_expiry_restores_the_block(self):
        self.entry['o4_exception'].pop('expires_after_round')
        self.assertTrue(self._blocking())

    def test_an_expired_exception_restores_the_block(self):
        self.entry['o4_exception']['expires_after_round'] = self.entry['rounds_on_case'] - 1
        self.assertTrue(self._blocking())

    def test_an_active_sample_restores_the_block(self):
        self.entry['sample_specific_work_active'] = True
        self.assertTrue(self._blocking())

    def test_missing_no_new_solve_declaration_restores_the_block(self):
        self.entry['o4_exception'].pop('no_new_solve_until_resolved')
        self.assertTrue(self._blocking())
