"""O9 for the F6 gap found in the Round-A re-check: no per-wave cost/benefit + stop-loss record existed.

F6's wording is "each wave's plan writes cost/benefit (estimated STAR hours, rounds) and the stop-loss
condition (2 consecutive fail-closed => stop and write a diagnosis)".  The Round-A delivery shipped the
stop-loss helper and the KPI ledger, but not a machine-checked wave plan; this test pins the validator
(``scripts/kpi_ledger.py::validate_wave_plan``) and the committed plan shape.

The committed fixture ``tests/fixtures/wave-plan-20261030.json`` is byte-identical to the campaign
evidence record when that local evidence file exists (the evidence store is gitignored by convention);
the test skips only the identity check when it is absent.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import kpi_ledger as kl  # noqa: E402

FIXTURE = ROOT / 'tests/fixtures/wave-plan-20261030.json'
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/phase3-p30-20261030/WAVE-PLAN.json'


def plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def codes(document: dict) -> set[str]:
    return {item['code'] for item in kl.validate_wave_plan(document)}


class WavePlanContract(unittest.TestCase):
    def test_committed_wave_plan_is_valid(self):
        self.assertEqual([], kl.validate_wave_plan(plan()))

    def test_evidence_record_is_byte_identical_to_the_committed_fixture(self):
        if not EVIDENCE.is_file():
            self.skipTest('local evidence store absent; the committed fixture is the pinned shape')
        self.assertEqual(FIXTURE.read_bytes(), EVIDENCE.read_bytes())

    def test_missing_stop_condition_is_refused(self):
        document = plan()
        del document['waves'][0]['stop_condition']
        self.assertIn('WAVE_PLAN_STOP_CONDITION', codes(document))

    def test_stop_rule_other_than_two_is_refused(self):
        document = plan()
        document['policy']['consecutive_fail_closed'] = 3
        self.assertIn('WAVE_PLAN_STOP_RULE', codes(document))
        document = plan()
        document['waves'][1]['stop_condition']['consecutive_fail_closed'] = 1
        self.assertIn('WAVE_PLAN_STOP_CONDITION', codes(document))

    def test_wave_without_cost_benefit_is_refused(self):
        document = plan()
        del document['waves'][1]['cost_benefit']
        self.assertIn('WAVE_PLAN_COST_BENEFIT_MISSING', codes(document))

    def test_closed_wave_must_state_benefit_and_evidence(self):
        document = plan()
        document['waves'][0]['cost_benefit'].pop('benefit')
        document['waves'][0].pop('evidence')
        self.assertIn('WAVE_PLAN_BENEFIT_MISSING', codes(document))
        self.assertIn('WAVE_PLAN_EVIDENCE_MISSING', codes(document))

    def test_not_admitted_wave_must_list_blockers_and_zero_spend(self):
        document = plan()
        del document['waves'][1]['blocked_by']
        document['waves'][1]['cost_benefit']['actual_star_hours'] = 3.5
        self.assertIn('WAVE_PLAN_NOT_ADMITTED_NEEDS_BLOCKERS', codes(document))
        self.assertIn('WAVE_PLAN_NOT_ADMITTED_HAS_SPEND', codes(document))

    def test_not_admitted_wave_must_say_what_the_estimate_is_based_on(self):
        document = plan()
        document['waves'][1]['cost_benefit'].pop('basis')
        self.assertIn('WAVE_PLAN_ESTIMATE_BASIS', codes(document))

    def test_cli_exit_codes(self):
        valid = subprocess.run([sys.executable, 'scripts/kpi_ledger.py', '--wave-plan', str(FIXTURE)],
                               cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, valid.returncode, valid.stdout + valid.stderr)
        document = plan()
        del document['waves'][0]['stop_condition']
        with tempfile.TemporaryDirectory() as name:
            broken = Path(name) / 'WAVE-PLAN.json'
            broken.write_text(json.dumps(document), encoding='utf-8')
            bad = subprocess.run([sys.executable, 'scripts/kpi_ledger.py', '--wave-plan', str(broken)],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertIn('WAVE_PLAN_STOP_CONDITION', bad.stdout)


if __name__ == '__main__':
    unittest.main()
