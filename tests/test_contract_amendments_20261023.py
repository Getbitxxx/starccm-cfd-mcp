"""Counterexamples for the two user-signed contract amendments of 2026-10-23.

C1 replaces the uniform-ratio requirement with per-pair measured ratios inside the
registered admission gate. C2 adds APPROVED_A_LEG_RATIO to the reference vocabulary.

Both amendments must fail closed: the point of a counterexample is that the contract
refuses the wrong shape, not that it accepts a convenient one. Each test below states
which direction is being pinned and what would break if the amendment were sloppy.

Synthetic fixtures only; nothing here is production evidence.
"""
import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

import quality_contract as q  # noqa: E402
from tests.test_quality_contract import Fixture  # noqa: E402


class C1PerPairRatios(unittest.TestCase):
    """C1: measured per-pair ratios inside the registered gate, never averaged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))
        self.p = self.f.packet

    def _family(self, cells):
        """Point the fixture at a synthetic family with these cell counts."""
        values = [.49, .4975, .499375]
        refs = []
        root = Path(self.temp.name) / 'family'
        for i, (param, value) in enumerate(zip(cells, values)):
            lev = Fixture(root / ('lv' + str(i)), is_level=True, cells=int(param),
                          target=value, mesh_size=.1 * (64000 / param) ** (1 / 3))
            path = root / ('bundle%s.json' % i)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'status': 'ok', 'parameter': int(param),
                                        'cells': int(param), 'target_mean': value,
                                        'quality_inputs': lev.packet}), encoding='utf-8')
            refs.append({'path': str(path), 'sha256': q.sha256(path)})
        sens = self.p['numerics']['sensitivity']
        sens['mesh_levels']['records'] = refs
        return sens

    def test_the_registered_gate_is_exactly_what_the_ruling_names(self):
        self.assertEqual({'cell_max': 2.2, 'h_min': 1.26, 'h_max': 1.32}, q.MESH_RATIO_GATES,
                         'the gate must be the s3-a registered values, not a convenient window')

    def test_a_non_uniform_family_inside_the_gate_is_admitted(self):
        """The shape C1 exists for: two different measured ratios, both inside the gate."""
        # 1.30 and 1.28 spacing ratios: unequal, as every real refinement is.
        cells = [16000, 35152, 75762]
        r = [(cells[i + 1] / cells[i]) ** (1 / 3) for i in range(2)]
        self.assertNotAlmostEqual(r[0], r[1], places=6, msg='the fixture must be non-uniform')
        self.assertTrue(all(1.26 <= x <= 1.32 for x in r))
        sens = self._family(cells)
        self.assertTrue(q.sensitivity_proof(self.p),
                        'a non-uniform measured family inside the gate must compute')

    def test_one_pair_outside_the_gate_is_refused(self):
        """Per-pair means per-pair: a single out-of-gate step must sink the family."""
        cells = [16000, 35152, 75762 * 8]      # second step ~2.6 in spacing
        sens = self._family(cells)
        r = [(cells[i + 1] / cells[i]) ** (1 / 3) for i in range(2)]
        self.assertTrue(any(not (1.26 <= x <= 1.32) for x in r))
        self.assertFalse(q.sensitivity_proof(self.p))

    def test_a_uniform_family_outside_the_gate_is_also_refused(self):
        """C1 is not a relaxation: equal steps alone no longer buy admission."""
        cells = [1000, 8000, 64000]            # the old synthetic family, h = 2.0
        sens = self._family(cells)
        r = [(cells[i + 1] / cells[i]) ** (1 / 3) for i in range(2)]
        self.assertAlmostEqual(r[0], r[1], places=9)
        self.assertTrue(all(x > 1.32 for x in r))
        self.assertFalse(q.sensitivity_proof(self.p),
                         'a uniform family outside the gate is still outside the gate')

    def test_the_cell_ratio_gate_binds_separately_from_the_spacing_gate(self):
        """A family can satisfy the spacing window and still fail the cell-ratio gate."""
        cells = [16000, 35152, 80000]
        # spacing ~1.30 and ~1.316 -- the second is inside the spacing window
        sens = self._family(cells)
        r = [(cells[i + 1] / cells[i]) ** (1 / 3) for i in range(2)]
        self.assertTrue(all(1.26 <= x <= 1.32 for x in r))
        self.assertGreater(cells[2] / cells[1], 2.2, 'cell ratio must exceed the gate')
        self.assertFalse(q.sensitivity_proof(self.p))

    def test_both_pairs_are_computed_not_averaged(self):
        """Each measured ratio is computed on its own; no averaging is introduced."""
        src = (ROOT / 'scripts/quality_contract.py').read_text(encoding='utf-8')
        self.assertNotIn('math.isclose(r1,r2', src,
                         'the uniform-ratio assertion C1 removes must stay removed')
        self.assertIn('for r in ratios:', src,
                      'each measured ratio must be computed, not collapsed into one')
        # Averaging would let one in-gate pair mask an out-of-gate one; the per-pair
        # refusal above is what proves it does not.


class C2ReferenceVocabulary(unittest.TestCase):
    """C2: APPROVED_A_LEG_RATIO is a verified status; the vocabulary stays closed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))
        self.f.reference()
        self.p = self.f.packet

    def test_the_amended_status_is_accepted(self):
        self.assertIn('APPROVED_A_LEG_RATIO', q.REFERENCE_VERIFIED_STATUSES)
        self.assertIn('ESTABLISHED', q.REFERENCE_VERIFIED_STATUSES)
        self.assertIn('VERIFIED', q.REFERENCE_VERIFIED_STATUSES)

    def test_a_leg_status_certifies_when_everything_else_binds(self):
        ref = self.p['engineering']['reference']
        ref['status'] = 'APPROVED_A_LEG_RATIO'
        self.f.bind()          # the status is bound into the certificate, not just asserted
        self.assertEqual(q.STATES[3], q.compute_quality_state(self.p),
                         'the A-leg status must be enough for E-phys once the rest binds')

    def test_the_vocabulary_is_not_open_ended(self):
        """Adding one status must not have opened the door to any string."""
        ref = self.p['engineering']['reference']
        ref['status'] = 'PENDING_INDEPENDENT_REVIEW'
        self.f.bind()
        self.assertNotEqual(q.STATES[3], q.compute_quality_state(self.p))

    def test_unverified_still_caps(self):
        ref = self.p['engineering']['reference']
        ref['status'] = 'APPROVED_A_LEG_RATIO'
        ref['verified'] = False
        self.f.bind()
        self.assertNotEqual(q.STATES[3], q.compute_quality_state(self.p),
                            'a status label alone is still not verification')


if __name__ == '__main__':
    unittest.main()
