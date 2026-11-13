"""O9 ratchet for the pre-existing contract-chain defect found while externalising the 87 plans (F1).

Finding (measured, not inherited from a report): the PRE-refactor ``prepare()`` ternary chain is shifted
after part34.  The plan data had to materialise those exact semantics to keep the 87/87 config identity,
so three plans now carry contract flags that are known to be wrong for them:

  * part35 - the part35 branch dict is gone; only the surviving ``no_mesh_no_physics_no_solve`` flag remains,
             while the executed staging (mrf-almcond) pinned ``read_only_diagnostic``/``no_solve``/
             ``alm_condition_values_probe``;
  * part37 - got the part36 te-variant flags, while its never-run staging pinned
             ``j_ladder_velocity``/``target_j=0.601``;
  * part75 - its staging predates the later CONTRACT_OVERRIDES entry that marks ``diagnostic_C_BLOCKED``.

This test does NOT bless the defect: it pins the exact contract bytes so that a silent change fails and a
deliberate data-only fix has to update the committed fixture.  It also records the verified boundary: the
earlier statement that part38/part39 were shifted is NOT reproduced by measurement - they carry their own
correct flags, and this test would fail if they silently changed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_mrf_chain as chain  # noqa: E402

FIXTURE = ROOT / 'tests/fixtures/mrf-contract-known-divergences-20261030.json'


def canonical_contract(plan: str) -> str:
    return hashlib.sha256(
        json.dumps(chain.PLANS[plan]['contract'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class KnownContractDivergences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_known_divergent_plans_still_carry_the_pinned_contract_bytes(self):
        for plan, entry in self.fx['known_divergent'].items():
            self.assertIn(plan, chain.PLANS, plan)
            self.assertEqual(entry['contract_sha256'], canonical_contract(plan),
                             '%s contract changed: update the fixture only with a deliberate fix' % plan)
            self.assertEqual(entry['flags'], sorted(chain.PLANS[plan]['contract']), plan)

    def test_plans_verified_not_affected_by_the_shift_stay_correct(self):
        for plan, entry in self.fx['verified_not_affected'].items():
            self.assertIn(plan, chain.PLANS, plan)
            self.assertEqual(entry['contract_sha256'], canonical_contract(plan), plan)
            self.assertEqual(entry['flags'], sorted(chain.PLANS[plan]['contract']), plan)

    def test_the_fixture_names_the_record_that_documents_the_cause(self):
        self.assertIn('PIN-IMPACT.json', self.fx['source_record'])
        for plan, entry in self.fx['known_divergent'].items():
            self.assertTrue(entry['reason'].strip(), plan)
        self.assertIn('data-only fix', self.fx['disposition'])

    def test_no_extra_plan_has_silently_lost_its_required_contract_flags(self):
        """A guard against the defect spreading: every plan that declares a step must still declare the
        contract key(s) its own step names imply, except the three known-divergent plans."""
        known = set(self.fx['known_divergent'])
        for plan, spec in chain.PLANS.items():
            if plan in known:
                continue
            self.assertIn('contract', spec, plan)
            contract = spec['contract']
            self.assertIsInstance(contract, dict, plan)
            # every non-known plan must carry at least one policy flag besides plan/steps
            policy = set(contract) - {'plan', 'steps'}
            self.assertTrue(policy, '%s carries no policy flags at all (chain shift?)' % plan)


if __name__ == '__main__':
    unittest.main()
