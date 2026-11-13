"""Keep the UAV campaign pointer and status consistent (anti-drift guard)."""
from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / 'campaigns' / 'uav-2026' / 'STATUS.json'
CURRENT = ROOT / 'CURRENT.md'
PLAN = ROOT / 'docs' / 'plan-uav-external-flow-20261024.md'
Q_STATES = ('Q0', 'Q1', 'Q2', 'Q3')


class CampaignStatus(unittest.TestCase):
    def setUp(self):
        self.status = json.loads(STATUS.read_text(encoding='utf-8'))
        self.cases = self.status.get('cases') or []

    def test_goal_and_phase_are_explicit(self):
        self.assertIn('UAV', self.status['goal'])
        self.assertIn('Q3', self.status['goal'])
        self.assertIn(self.status['phase'], self.status['phase_states'])
        self.assertTrue(self.status.get('next_gate'))

    def test_forbidden_work_is_listed(self):
        forbidden = self.status.get('forbidden') or []
        self.assertTrue(forbidden)
        self.assertTrue(all(isinstance(item, str) and item.strip() for item in forbidden))

    def test_every_case_has_identity_and_a_valid_q_label(self):
        for case in self.cases:
            self.assertTrue(case.get('case_id'), case)
            self.assertTrue(case.get('source'), case)
            self.assertIn(case.get('q'), Q_STATES, case)
            if case.get('q') == 'Q3':
                self.assertTrue(case.get('reference'), 'Q3 needs a reference')
                self.assertTrue(case.get('record'), 'Q3 needs a citable record')
            if case.get('q') == 'Q2':
                self.assertTrue(case.get('record'), 'Q2 needs a citable record')

    def test_q_counts_match_the_case_list(self):
        counts = Counter(case.get('q') for case in self.cases)
        declared = self.status.get('q_counts') or {}
        for q in Q_STATES:
            self.assertEqual(int(declared.get(q, 0)), counts.get(q, 0), q)

    def test_current_md_points_at_the_plan_and_status(self):
        text = CURRENT.read_text(encoding='utf-8')
        self.assertIn('docs/plan-uav-external-flow-20261024.md', text)
        self.assertIn('campaigns/uav-2026/STATUS.json', text)
        self.assertIn(self.status['phase'], text)

    def test_plan_exists_and_names_the_q_rules(self):
        text = PLAN.read_text(encoding='utf-8')
        self.assertIn('Q3', text)
        self.assertIn('Q2', text)
        self.assertIn('每轮结束的 5 问', text)


if __name__ == '__main__':
    unittest.main()
