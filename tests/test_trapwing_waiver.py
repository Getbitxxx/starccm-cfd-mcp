"""O9 ratchets for the 2026-11-02 Trap Wing mesh-gate waiver.

The waiver is the one place in this campaign where a HARD gate is consciously not met. That makes it the most
dangerous record in the tree: if it silently drifted into "accepted", or if the numbers it cites stopped matching
the bytes it points at, the project would be reading a derogation as a pass. These tests pin exactly that:

  - the gate value in the waiver equals the CaseSpec gate (gates unchanged, no quiet relaxation);
  - the measured value really is BELOW the gate (it is a derogation, never a pass);
  - the failing cell count is small and localized, and the localization record on disk still hashes to what the
    waiver cites;
  - the mesh the waiver is bound to still hashes to the recorded run output (recipe/geometry change voids it);
  - the signature state is explicit and never silently "signed".

Fixtures are the real archived records of 2026-11-02, not synthetic numbers.
"""
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-trapwing-hlpw1'
WAIVER = EVIDENCE / 'MESH-GATE-WAIVER-20261102.json'
SPEC = ROOT / 'cases/uav-trapwing-hlpw1/case_spec.json'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class WaiverContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.waiver = json.loads(WAIVER.read_text(encoding='utf-8'))
        cls.spec = json.loads(SPEC.read_text(encoding='utf-8'))
        cls.run_dir = EVIDENCE / cls.waiver['mesh']['run_id']

    def test_gate_is_the_casespec_gate_not_a_relaxation(self):
        self.assertEqual(self.waiver['gate']['metric'], 'volume_change')
        self.assertEqual(self.waiver['gate']['threshold'], float(self.spec['quality']['mesh_min_volume_change']))
        self.assertIs(self.waiver['gate']['unchanged'], True)

    def test_measured_value_is_below_the_gate(self):
        # A waiver that "passes" would be a silent gate change; a waiver far below the gate would be a false claim.
        measured = self.waiver['measured']['volume_change_min']
        self.assertLess(measured, self.waiver['gate']['threshold'])
        self.assertGreater(measured, 0.0)
        self.assertAlmostEqual(measured / self.waiver['gate']['threshold'],
                               self.waiver['measured']['margin'], places=9)

    def test_failure_is_localised_and_small(self):
        measured = self.waiver['measured']
        self.assertGreater(measured['cells_below_gate'], 0)
        self.assertLess(measured['cells_below_gate'], 1e-4 * self.waiver['mesh']['cells'])
        self.assertEqual(measured['cutoff_used'], self.waiver['gate']['threshold'])
        buckets = measured['localization']['prism_buckets']
        self.assertEqual(sum(buckets.values()), measured['cells_below_gate'])

    def test_localization_record_still_hashes_as_cited(self):
        record = self.run_dir / self.waiver['measured']['localization']['record']
        self.assertTrue(record.is_file())
        self.assertEqual(sha256(record), self.waiver['measured']['localization']['sha256'])

    def test_mesh_identity_still_matches_the_run_output(self):
        run = json.loads((self.run_dir / 'run.json').read_text(encoding='utf-8'))
        mesh_steps = [s for s in run['steps'] if s.get('output_sha256')]
        self.assertIn(self.waiver['mesh']['sha256'], [s['output_sha256'] for s in mesh_steps])
        quality = json.loads((self.run_dir / 'external_flow_mesh_quality.json').read_text(encoding='utf-8'))
        self.assertEqual(quality['cells'], self.waiver['mesh']['cells'])
        self.assertEqual(quality['quality_gate']['volume_change']['value'],
                         self.waiver['measured']['volume_change_min'])

    def test_other_gates_are_recorded_as_passing(self):
        for name in ('cell_quality', 'face_validity', 'wall_prism_layers', 'wall_prism_free_area'):
            self.assertIs(self.waiver['other_gates'][name]['passed'], True, name)

    def test_waiver_is_not_a_q3_certificate(self):
        joined = ' '.join(self.waiver['scope_limits'])
        self.assertIn('NOT a Q3', joined)
        self.assertIn('GCI', joined)

    def test_signature_state_is_explicit(self):
        status = self.waiver['signature_status']
        self.assertIn(status, ('PENDING_USER_SIGNATURE', 'SIGNED'))
        if status == 'PENDING_USER_SIGNATURE':
            self.assertIsNone(self.waiver['signed_by'])
            self.assertIsNone(self.waiver['signed_on'])
        else:
            self.assertTrue(self.waiver['signed_by'])
            self.assertTrue(self.waiver['signed_on'])


if __name__ == '__main__':
    unittest.main()
