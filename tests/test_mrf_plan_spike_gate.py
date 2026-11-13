"""O9 ratchets for the F2 spike-evidence gate (phase-3 P30): no spike, no staging bytes.

Measured motivation: the project's expensive failures came from plans that were registered and staged
before anyone had spent a cheap spike on the unproven part (e.g. the first GCI ladder ran five J points
before the tolerance identity/normalisation was spiked).  The gate is therefore fail-closed: a NEW plan
(the campaign manifest does not list it as legacy) must point at an existing spike record whose sha256
matches, whose duration is within the campaign budget (40 min) and whose verdict is PASS.  prepare()
checks this BEFORE it creates the evidence directory or writes staging bytes, so a refused plan leaves
nothing behind.

The 87 legacy plans are grandfathered with an honest ``legacy_pre_split`` marker: their validation was
the plan's own executed run/staging record, which did not exist when they were registered.
"""
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plan_registry  # noqa: E402
import run_mrf_chain as chain  # noqa: E402

ENGINE = ROOT / 'scripts/run_mrf_chain.py'
LIVE_CASE_SPEC = ROOT / 'cases/uav-prop-apc10x7e/case_spec.json'
SPIKE_RECORD = 'cases/uav-prop-apc10x7e/case_spec.json'  # any existing repo file with a stable sha
PLAN_ID = 'part91'


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spike(**overrides):
    record = {'kind': 'spike_record', 'path': SPIKE_RECORD, 'sha256': sha_file(LIVE_CASE_SPEC),
              'duration_min': 12.5, 'verdict': 'PASS'}
    record.update(overrides)
    return record


def new_plan(**overrides):
    """A NEW (non-legacy) plan spec good enough for prepare() to stage into a throwaway directory."""
    data = {
        'plan': PLAN_ID, 'run_id': 'mrf-gate-test-20261030a',
        'job_dir': 'C:\\Users\\chen\\Documents\\CodexCFD\\service_v1\\adhoc\\prop-gate-test',
        'macros': ['AuditMrfRotorSolid.java'],
        'inputs': {'case_spec.json': None},
        'steps': [{'name': 'audit-rotor', 'macro': 'AuditMrfRotorSolid.java', 'input': None,
                   'output': 'recovered_rotor_solids.sim', 'env': {},
                   'marker': 'MRF_ROTOR_SOLID_AUDITED', 'records': ['recovered_rotor_solids.json']}],
        'record_gates': [{'record': 'recovered_rotor_solids.json',
                          'fields': [{'field': 'status', 'op': 'eq', 'value': 'ok'}]}],
        'required_downloads': ['run.json'], 'optional_downloads': [],
        'contract': {'plan': PLAN_ID, 'steps': [['audit-rotor', 'AuditMrfRotorSolid.java']]},
        'spike_evidence': spike(),
        'spike_gate': {'max_duration_min': 40, 'required_verdict': 'PASS'},
        'legacy': False,
    }
    if overrides.pop('drop_spike', False):
        data.pop('spike_evidence')
    data.update(overrides)
    return data


class SpikeGateRefusal(unittest.TestCase):
    """prepare() must refuse BEFORE any staging write and leave no bytes behind."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='.p30-spike-gate-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.evidence = self.work / 'evidence'
        self._original_paths = chain.paths
        self._original_plan = chain.PLANS.get(PLAN_ID)
        chain.paths = lambda plan: (chain.PLANS[plan], self.evidence, self.evidence / 'staging')
        self.addCleanup(self._restore)

    def _restore(self):
        chain.paths = self._original_paths
        if self._original_plan is None:
            chain.PLANS.pop(PLAN_ID, None)
        else:
            chain.PLANS[PLAN_ID] = self._original_plan

    def _prepare(self, spec):
        chain.PLANS[PLAN_ID] = spec
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            chain.prepare(argparse.Namespace(plan=PLAN_ID, job_dir=None))
        self.assertFalse(self.evidence.exists(), 'a refused plan must not write evidence bytes')
        self.assertEqual('', stdout.getvalue())
        return str(caught.exception)

    def test_no_spike_evidence_is_refused(self):
        message = self._prepare(new_plan(drop_spike=True))
        self.assertIn('spike_evidence', message)

    def test_spike_longer_than_the_budget_is_refused(self):
        message = self._prepare(new_plan(spike_evidence=spike(duration_min=41)))
        self.assertIn('41', message)
        self.assertIn('40', message)

    def test_failed_spike_verdict_is_refused(self):
        message = self._prepare(new_plan(spike_evidence=spike(verdict='FAIL')))
        self.assertIn('verdict', message)

    def test_spike_hash_mismatch_is_refused(self):
        message = self._prepare(new_plan(spike_evidence=spike(sha256='0' * 64)))
        self.assertIn('sha256', message)

    def test_missing_spike_record_is_refused(self):
        message = self._prepare(new_plan(spike_evidence=spike(path='cases/does-not-exist-spike.json')))
        self.assertIn('spike record missing', message)

    def test_new_plan_may_not_claim_the_legacy_marker(self):
        message = self._prepare(new_plan(spike_evidence={'kind': 'legacy_pre_split',
                                                         'reason': 'pretend this is old'}))
        self.assertIn('spike_record', message)

    def test_spec_without_a_legacy_flag_is_treated_as_new_and_refused(self):
        spec = new_plan(drop_spike=True)
        spec.pop('legacy')
        message = self._prepare(spec)
        self.assertIn('spike_evidence', message)

    def test_hand_edited_tolerance_band_is_refused_before_staging(self):
        spec = new_plan()
        spec['record_gates'].append({'record': 'mrf_trial_audit.json', 'fields': [
            {'field': 'ct_ref', 'op': 'eq', 'value': 0.0656},
            {'field': 'cp_ref', 'op': 'eq', 'value': 0.0476},
            {'field': 'ct_tolerance', 'op': 'ge', 'value': 0.004},
            {'field': 'ct_tolerance', 'op': 'le', 'value': 0.006}]})
        spec['gates_derivation'] = {'rule': 'signed_tolerance_bands',
                                    'case_spec': 'cases/uav-prop-apc10x7e/case_spec.json',
                                    'records': ['mrf_trial_audit.json'], 'eps': 1e-12}
        message = self._prepare(spec)
        self.assertIn('not the CaseSpec-derived band', message)


class SpikeGateAcceptance(unittest.TestCase):
    """A valid spike record is accepted and prepare() stages exactly as before."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='.p30-spike-gate-ok-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.evidence = self.work / 'evidence'
        self._original_paths = chain.paths
        self._original_plan = chain.PLANS.get(PLAN_ID)
        chain.paths = lambda plan: (chain.PLANS[plan], self.evidence, self.evidence / 'staging')
        self.addCleanup(self._restore)

    def _restore(self):
        chain.paths = self._original_paths
        if self._original_plan is None:
            chain.PLANS.pop(PLAN_ID, None)
        else:
            chain.PLANS[PLAN_ID] = self._original_plan

    def test_valid_spike_record_is_accepted_and_the_staging_is_review_pinned(self):
        spec = new_plan()
        chain.PLANS[PLAN_ID] = spec
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            chain.prepare(argparse.Namespace(plan=PLAN_ID, job_dir=None))
        staging = self.evidence / 'staging'
        self.assertTrue((staging / 'config.json').is_file())
        self.assertTrue((staging / 'case_spec.json').is_file())
        self.assertTrue((staging / 'sources/AuditMrfRotorSolid.java').is_file())
        review_inputs = json.loads((staging / 'REVIEW-INPUTS.json').read_text(encoding='utf-8'))
        self.assertEqual(spec['contract'], review_inputs['contract'])
        hashes = review_inputs['hashes']
        self.assertEqual(sha_file(ENGINE), hashes['scripts/run_mrf_chain.py'])
        self.assertEqual(sha_file(LIVE_CASE_SPEC), hashes['source_case_spec.json'])
        self.assertIn('inputs/case_spec.json', hashes)
        printed = json.loads(stdout.getvalue())
        self.assertEqual(str(staging), printed['staging'])

    def test_legacy_marker_passes_the_gate_without_a_spike_record(self):
        record = plan_registry.assert_spike_evidence('part1', chain.PLANS['part1'], ROOT)
        self.assertEqual('legacy_pre_split', record['kind'])
        self.assertTrue(record['reason'].strip())
        plan_registry.assert_preparable('part1', chain.PLANS['part1'], ROOT)  # no SystemExit

    def test_valid_new_plan_record_returns_the_resolved_path_and_hash(self):
        record = plan_registry.assert_spike_evidence(PLAN_ID, new_plan(), ROOT)
        self.assertEqual(str(ROOT / SPIKE_RECORD), record['resolved_path'])
        self.assertEqual(sha_file(LIVE_CASE_SPEC), record['actual_sha256'])
        self.assertAlmostEqual(12.5, record['duration_min'])


if __name__ == '__main__':
    unittest.main()
