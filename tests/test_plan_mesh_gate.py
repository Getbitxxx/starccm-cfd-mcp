"""O9 ratchet for F3's solve-plan rule: no solve before an accepted mesh family covers the mesh.

Positive/negative fixtures are the REAL 2026-10-30 records, never invented: the accepted family
"mesh-scatter signed family (the Q3 carrier)" from
campaigns/uav-2026/evidence/uav-prop-apc10x7e/MESH-FAMILY-GATE-20261030a.json, the real 310 MB
mesh state it lists (mrf-meshb13-20261030a/mrf_diagnostic_meshed.sim), and a real CaseSpec file
copied to tmp with one byte mutated (the "new case" of the regime).  Hashes are computed at test
time; nothing here is a hand-typed hash.
"""
import argparse
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mesh_acceptance as acceptance  # noqa: E402
import plan_mesh_gate as gate  # noqa: E402
import run_mrf_chain as chain  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
FAMILY_RECORD = EVIDENCE / 'MESH-FAMILY-GATE-20261030a.json'
MESH_SIM = EVIDENCE / 'mrf-meshb13-20261030a/mrf_diagnostic_meshed.sim'
OTHER_SIM = ROOT / 'cases/air-three-blade/e2e-inputs/block_geometry.sim'
FROZEN_SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec.json'

ACCEPTED_FAMILY = 'mesh-scatter signed family (the Q3 carrier)'
UNACCEPTED_FAMILY = 'gci-three-level-ladder (declared for the GCI claim)'


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class SolvePlanGateFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.tmp = Path(cls._tmp.name)
        data = FROZEN_SPEC.read_bytes()
        needle = b'APC 10x7E'
        index = data.find(needle)
        assert index >= 0, 'positive fixture changed'
        cls.new_spec_bytes = data[:index] + b'APC 10x7F' + data[index + len(needle):]
        cls.new_spec_path = cls.tmp / 'case_spec.json'
        cls.new_spec_path.write_bytes(cls.new_spec_bytes)
        cls.new_spec = json.loads(cls.new_spec_bytes)
        cls.legacy_spec = json.loads(data)
        cls.record_sha = acceptance.sha256_file(FAMILY_RECORD)
        cls.mesh_sha = acceptance.sha256_file(MESH_SIM)
        cls.other_mesh_sha = acceptance.sha256_file(OTHER_SIM)
        cls.record = json.loads(FAMILY_RECORD.read_text(encoding='utf-8'))
        cls.families = {family['family_id']: family for family in cls.record['families']}

    def make_plan(self, *, family=ACCEPTED_FAMILY, record=FAMILY_RECORD, sha=None,
                  contract=None, steps=None, inputs=None):
        plan = {
            'plan': 'new-f3-plan',
            'run_id': 'new-f3-run',
            'contract': {'solve_run': True} if contract is None else contract,
            'steps': [{'name': 'run-trial', 'macro': 'RunMrfTrial.java'}] if steps is None else steps,
            'inputs': ({'mrf_diagnostic_meshed.sim': rel(MESH_SIM)} if inputs is None else inputs),
        }
        if family is not None:
            plan['mesh_family_gate'] = {
                'record': rel(record) if record is not None else 'campaigns/uav-2026/evidence/'
                                                                 'uav-prop-apc10x7e/missing-record.json',
                'sha256': self.record_sha if sha is None else sha,
                'family_id': family,
            }
        return plan

    def refusal(self, plan, spec=None, **kwargs):
        with self.assertRaises(gate.MeshRegimeError) as raised:
            gate.assert_solve_plan_family_accepted(plan, self.new_spec if spec is None else spec, ROOT, **kwargs)
        return raised.exception


class RealFixturePreconditions(SolvePlanGateFixtures):
    def test_the_positive_fixture_really_is_an_accepted_family_listing_the_real_mesh(self):
        self.assertEqual('mesh_family_gate_replay', self.record['kind'])
        accepted = self.families[ACCEPTED_FAMILY]
        self.assertIs(True, accepted['accepted'])
        self.assertIn(ACCEPTED_FAMILY, json.dumps(accepted))
        mesh_shas = {member['mesh_sha256'] for member in accepted['members']}
        self.assertIn(self.mesh_sha, mesh_shas)
        self.assertNotIn(self.other_mesh_sha, mesh_shas)
        self.assertEqual(310240893, MESH_SIM.stat().st_size)

    def test_the_unaccepted_family_is_a_real_rejection(self):
        family = self.families[UNACCEPTED_FAMILY]
        self.assertIs(False, family['accepted'])
        self.assertEqual('FAIL', family['worst']['verdict'])

    def test_the_new_spec_is_the_real_file_with_one_byte_changed(self):
        self.assertTrue(gate.is_grandfathered(self.legacy_spec, ROOT, case_spec_path=FROZEN_SPEC))
        self.assertFalse(gate.is_grandfathered(self.new_spec, ROOT, case_spec_path=self.new_spec_path))


class SolveClassification(SolvePlanGateFixtures):
    def test_the_declared_contract_can_only_add_the_solve_label(self):
        self.assertTrue(gate.plan_runs_solve(
            {'contract': {'solve_run': True}, 'steps': [{'macro': 'AuditMrfTrial.java'}]})[0])
        # fail closed: a solver step overrules solve_run=false
        self.assertTrue(gate.plan_runs_solve(
            {'contract': {'solve_run': False}, 'steps': [{'macro': 'RunMrfTrial.java'}]})[0])
        self.assertTrue(gate.plan_runs_solve({'steps': [{'macro': 'RunMrfSegment.java'}]})[0])
        self.assertTrue(gate.plan_runs_solve({'steps': [{'macro': 'SolveSomething.java'}]})[0])
        self.assertFalse(gate.plan_runs_solve(
            {'contract': {'solve_run': False}, 'steps': [{'macro': 'ExecuteMrfVolumeMesh.java'}]})[0])

    def test_a_mesh_only_plan_is_not_gated_even_on_a_new_case(self):
        plan = self.make_plan(family=None, contract={'plan': 'mesh-only'},
                              steps=[{'name': 'execute-mesh', 'macro': 'ExecuteMrfVolumeMesh.java'}])
        self.assertIsNone(gate.assert_solve_plan_family_accepted(plan, self.new_spec, ROOT))


class SolvePlanRefusals(SolvePlanGateFixtures):
    def test_a_solve_plan_on_a_new_case_without_the_field_is_refused(self):
        plan = self.make_plan(family=None)
        self.assertEqual('FAMILY_GATE_MISSING', self.refusal(plan).code)

    def test_a_legacy_grandfathered_plan_without_the_field_stays_admissible(self):
        plan = self.make_plan(family=None)
        self.assertIsNone(gate.assert_solve_plan_family_accepted(
            plan, self.legacy_spec, ROOT, case_spec_path=FROZEN_SPEC))

    def test_a_malformed_field_is_refused(self):
        plan = self.make_plan()
        del plan['mesh_family_gate']['family_id']
        self.assertEqual('FAMILY_GATE_INVALID', self.refusal(plan).code)
        plan = self.make_plan()
        plan['mesh_family_gate']['sha256'] = 'abc'
        self.assertEqual('FAMILY_GATE_INVALID', self.refusal(plan).code)

    def test_a_missing_record_is_refused(self):
        self.assertEqual('FAMILY_GATE_RECORD_MISSING', self.refusal(self.make_plan(record=None)).code)

    def test_a_record_hash_mismatch_is_refused(self):
        plan = self.make_plan(sha='0' * 64)
        self.assertEqual('FAMILY_GATE_HASH_MISMATCH', self.refusal(plan).code)

    def test_an_unaccepted_family_is_refused(self):
        plan = self.make_plan(family=UNACCEPTED_FAMILY)
        self.assertEqual('FAMILY_GATE_NOT_ACCEPTED', self.refusal(plan).code)

    def test_an_unknown_family_id_is_refused(self):
        plan = self.make_plan(family='family-that-does-not-exist')
        self.assertEqual('FAMILY_GATE_FAMILY_UNKNOWN', self.refusal(plan).code)

    def test_a_family_that_does_not_list_the_consumed_mesh_is_refused(self):
        plan = self.make_plan(inputs={'mrf_diagnostic_meshed.sim': rel(OTHER_SIM)})
        self.assertEqual('FAMILY_GATE_MESH_NOT_LISTED', self.refusal(plan).code)

    def test_a_plan_with_no_existing_sim_input_cannot_be_verified(self):
        plan = self.make_plan(inputs={'mrf_diagnostic_meshed.sim': None})
        self.assertEqual('FAMILY_GATE_NO_MESH_INPUT', self.refusal(plan).code)


class SolvePlanAdmission(SolvePlanGateFixtures):
    def test_the_fully_matching_accepted_family_admits_the_plan(self):
        admission = gate.assert_solve_plan_family_accepted(self.make_plan(), self.new_spec, ROOT)
        self.assertEqual('mesh_family_solve_admission', admission['kind'])
        self.assertEqual(rel(FAMILY_RECORD), admission['record'])
        self.assertEqual(self.record_sha, admission['record_sha256'])
        self.assertEqual(ACCEPTED_FAMILY, admission['family_id'])
        self.assertEqual('mrf_diagnostic_meshed.sim', admission['matched_input'])
        self.assertEqual(self.mesh_sha, admission['mesh_sha256'])
        self.assertEqual('new-f3-plan', admission['plan'])

    def test_a_plan_may_declare_the_gate_inside_its_contract(self):
        """The data-driven registry (2026-10-30) pins `contract` as free-form data and refuses unknown
        top-level plan keys, so a NEW plan can carry the gate there without touching the loader."""
        plan = self.make_plan(family=None)
        plan['contract'] = {'solve_run': True, 'mesh_family_gate': {
            'record': rel(FAMILY_RECORD), 'sha256': self.record_sha, 'family_id': ACCEPTED_FAMILY}}
        admission = gate.assert_solve_plan_family_accepted(plan, self.new_spec, ROOT)
        self.assertEqual(self.mesh_sha, admission['mesh_sha256'])

    def test_engine_style_absolute_path_inputs_are_supported(self):
        plan = self.make_plan(inputs={'mrf_diagnostic_meshed.sim': str(MESH_SIM)})
        admission = gate.assert_solve_plan_family_accepted(plan, self.new_spec, ROOT)
        self.assertEqual(self.mesh_sha, admission['mesh_sha256'])

    def test_assert_plan_admissible_loads_the_staged_case_spec(self):
        """Legacy case + no field -> admissible; the same plan on the mutated (new) case -> refused."""
        legacy_plan = self.make_plan(family=None, inputs={'mrf_diagnostic_meshed.sim': rel(MESH_SIM),
                                                          'case_spec.json': None})
        self.assertIsNone(gate.assert_plan_admissible(legacy_plan, ROOT, FROZEN_SPEC))
        with self.assertRaises(gate.MeshRegimeError) as raised:
            gate.assert_plan_admissible(legacy_plan, ROOT, self.new_spec_path)
        self.assertEqual('FAMILY_GATE_MISSING', raised.exception.code)

    def test_assert_plan_admissible_admits_a_new_case_with_the_gate(self):
        plan = self.make_plan(inputs={'mrf_diagnostic_meshed.sim': rel(MESH_SIM),
                                      'case_spec.json': None})
        admission = gate.assert_plan_admissible(plan, ROOT, self.new_spec_path)
        self.assertEqual(self.mesh_sha, admission['mesh_sha256'])

    def test_assert_plan_admissible_refuses_a_new_case_without_the_gate(self):
        plan = self.make_plan(family=None, inputs={'mrf_diagnostic_meshed.sim': rel(MESH_SIM),
                                                   'case_spec.json': None})
        with self.assertRaises(gate.MeshRegimeError) as raised:
            gate.assert_plan_admissible(plan, ROOT, self.new_spec_path)
        self.assertEqual('FAMILY_GATE_MISSING', raised.exception.code)


class PrepareIntegration(SolvePlanGateFixtures):
    """O9: the APPLIED prepare() hook refuses a NEW non-grandfathered solve plan before any write.

    A synthetic plan is injected into chain.PLANS as a standalone spec dict (the shape
    plan_registry.load_all produces; the loader is not re-run on it here).  Its CaseSpec is a REAL
    frozen CaseSpec copied to tmp with one byte mutated.  The registry forbids plan inputs outside the
    repo, so only plan_mesh_gate.load_case_spec is patched to hand over that temp copy; the regime
    rule itself (grandfathering snapshot, hashes, codes) runs for real.  The run id is unique and
    build_staging is watched, so "refused BEFORE evidence.mkdir/build_staging" and "nothing written"
    are both asserted, not assumed.
    """

    SPIKE_RECORD = EVIDENCE / 'MESH-FAMILY-GATE-20261030a.json'  # a real record: the F2 gate only pins bytes

    def _synthetic_plan(self, family=None):
        plan = {
            'plan': 'f3-integration-probe',
            'run_id': 'f3-integration-probe-%s' % uuid.uuid4().hex[:12],
            'job_dir': 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\f3-integration-probe',
            'macros': ['RunMrfTrial.java'],
            'inputs': {
                'case_spec.json': None,
                'mrf_velocity_experiment_configured.sim': rel(OTHER_SIM),
            },
            'steps': [{'name': 'run-trial', 'macro': 'RunMrfTrial.java',
                       'input': 'mrf_velocity_experiment_configured.sim',
                       'output': 'mrf_trial_solved.sim', 'records': ['mrf_trial.json']}],
            'record_gates': [],
            'required_downloads': [],
            'optional_downloads': [],
            'contract': {'plan': 'f3-integration-probe',
                         'steps': [['run-trial', 'RunMrfTrial.java']], 'solve_run': True},
            'spike_evidence': {'kind': 'spike_record', 'path': rel(self.SPIKE_RECORD),
                               'sha256': acceptance.sha256_file(self.SPIKE_RECORD),
                               'duration_min': 5, 'verdict': 'PASS'},
            'spike_gate': {'max_duration_min': 40, 'required_verdict': 'PASS'},
            'legacy': False,
        }
        if family is not None:
            plan['mesh_family_gate'] = {'record': rel(FAMILY_RECORD),
                                        'sha256': self.record_sha, 'family_id': family}
        return plan

    def _prepare_and_expect_refusal(self, family=None):
        plan_id = 'f3-integration-probe'
        plan = self._synthetic_plan(family)
        self.assertNotIn(plan_id, chain.PLANS, 'the probe plan id must be free')
        chain.PLANS[plan_id] = plan
        evidence = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e' / plan['run_id']
        self.assertFalse(evidence.exists(), 'a unique run id must not exist before the probe')
        writer = mock.Mock(name='build_staging')
        try:
            with mock.patch.object(chain, 'build_staging', writer), \
                    mock.patch.object(gate, 'load_case_spec', return_value=self.new_spec):
                with self.assertRaises(SystemExit) as raised:
                    chain.prepare(argparse.Namespace(plan=plan_id, job_dir=None))
        finally:
            chain.PLANS.pop(plan_id, None)
            shutil.rmtree(evidence, ignore_errors=True)  # a bug must not leave a probe dir behind
        self.assertEqual(0, writer.call_count, 'prepare must refuse BEFORE build_staging')
        self.assertFalse(evidence.exists(), 'the evidence dir must not be created')
        self.assertFalse((evidence / 'staging').exists(), 'no staging dir may be created')
        return str(raised.exception)

    def test_prepare_refuses_a_new_solve_plan_without_the_gate_and_writes_nothing(self):
        message = self._prepare_and_expect_refusal()
        self.assertIn('[FAMILY_GATE_MISSING]', message)

    def test_prepare_refuses_a_new_solve_plan_whose_family_is_not_accepted(self):
        message = self._prepare_and_expect_refusal(family=UNACCEPTED_FAMILY)
        self.assertIn('[FAMILY_GATE_NOT_ACCEPTED]', message)


if __name__ == '__main__':
    unittest.main()
