"""O9 for the 2026-10-31 finding: a test was rewriting independently reviewed staging bytes.

`tests/test_prop_geom_audit_contract.py::setUpClass` used to call `audit.config()`, which copies the CURRENT
CaseSpec into the module STAGING directory.  Every test run therefore rewrote the committed prop
geometry-audit staging beyond its REVIEW-INPUTS pin (reviewed 033714ff..., current cbc354e2...) and the
drifted bytes were committed.  The executed run itself used the reviewed bytes, so the evidence is
consistent; what is broken is the staging's identity.

This test pins the known drift and the read-only fix:
  * the committed fixture records reviewed/config/current hashes; the current staging must equal the
    recorded drifted bytes (so a NEW silent rewrite fails here);
  * the reviewed pin and the executed config pin must still agree (the evidence claim);
  * the contract test must be read-only - it generates into a temporary directory, so running this test
    BEFORE and AFTER it leaves the staging bytes unchanged.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/geometry-audit-20261028a'
FIXTURE = ROOT / 'tests/fixtures/prop-geometry-audit-staging-drift-20261031.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PropStagingDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = json.loads(FIXTURE.read_text(encoding='utf-8'))
        cls.review_inputs = json.loads((BASE / 'staging/REVIEW-INPUTS.json').read_text(encoding='utf-8'))
        cls.config = json.loads((BASE / 'staging/config.json').read_text(encoding='utf-8'))
        cls.run_record = json.loads((BASE / 'run.json').read_text(encoding='utf-8'))

    def test_known_drift_is_exactly_as_recorded(self):
        self.assertEqual(self.fx['reviewed_case_spec_sha256'],
                         self.review_inputs['hashes']['inputs/case_spec.json'],
                         'the reviewed pin moved: update the drift record deliberately')
        self.assertEqual(self.fx['config_case_spec_pin'], self.config['inputs']['case_spec.json'],
                         'the executed config pin moved')
        self.assertNotEqual(self.fx['reviewed_case_spec_sha256'],
                            self.fx['current_committed_case_spec_sha256'],
                            'the record says the staging drifted')
        self.assertEqual(self.fx['current_committed_case_spec_sha256'], sha(BASE / 'staging/case_spec.json'),
                         'the staging bytes changed again; a silent rewrite is not allowed')

    def test_the_executed_run_used_the_reviewed_bytes(self):
        self.assertEqual('EXECUTION_COMPLETED', self.run_record['status'])
        self.assertEqual(self.fx['reviewed_case_spec_sha256'], self.config['inputs']['case_spec.json'])
        # review.json is the independent approval of exactly the reviewed hash
        review = json.loads((BASE / 'staging/review.json').read_text(encoding='utf-8'))
        self.assertTrue(review['approved'] and review['independent'])
        self.assertEqual(self.fx['reviewed_case_spec_sha256'], review['hashes']['inputs/case_spec.json'])

    def test_the_contract_test_is_read_only(self):
        before = sha(BASE / 'staging/case_spec.json')
        result = subprocess.run([sys.executable, '-m', 'unittest',
                                 'tests.test_prop_geom_audit_contract'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertEqual(before, sha(BASE / 'staging/case_spec.json'),
                         'running the prop contract test rewrote reviewed staging bytes again')


if __name__ == '__main__':
    unittest.main()
