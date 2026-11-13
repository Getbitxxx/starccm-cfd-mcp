"""O9 ratchets for the pre-submission self-check.

Measured defect (2026-10-30 review of part57/part60, non-blocking N1): after a controller change (e65a354) the
part55 staging's historical controller pin legitimately differs from HEAD, so the old test that ran part55 in
strict mode went red although the staging bytes were untouched.  The fix is a rule, not a list: every staging
that pins the CURRENT controller bytes must pass the strict check (it is about to be submitted), while approved
historical stagings are checked in --historical mode (their script pins describe the bytes they were approved
with).
"""
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_mrf_chain as chain  # noqa: E402

EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e'
CONTROLLER = 'scripts/run_mrf_chain.py'


def controller_digest() -> str:
    return hashlib.sha256((ROOT / CONTROLLER).read_bytes()).hexdigest()


def staging_of(plan: str) -> Path:
    return EVIDENCE / chain.PLANS[plan]['run_id'] / 'staging'


def check(plan: str, historical: bool = False) -> dict:
    args = [sys.executable, str(ROOT / 'scripts/pre_submit_check.py'), plan]
    if historical:
        args.append('--historical')
    result = subprocess.run(args, capture_output=True, text=True)
    return {'report': json.loads(result.stdout), 'returncode': result.returncode}


class PreSubmitCheck(unittest.TestCase):
    def test_historical_approved_stagings_are_self_consistent(self):
        for plan in ('part50', 'part51', 'part54', 'part55'):
            staging = staging_of(plan)
            if not (staging / 'REVIEW-INPUTS.json').is_file():
                continue
            outcome = check(plan, historical=True)
            self.assertEqual([], outcome['report']['failures'], outcome['report'])
            self.assertTrue(outcome['report']['ok'])
            self.assertEqual(0, outcome['returncode'])

    def test_submission_candidates_pass_strictly(self):
        """A staging is a SUBMISSION CANDIDATE only if it still equals what prepare() would produce today.

        Executed stagings are archival: after a plan or an input changes (e.g. the 2026-10-30 tolerance
        re-signing changed reference.json), their pins legitimately differ from HEAD and the strict check
        would redden a green history.  The candidate test is therefore content-based: the staged config
        (inputs + record gates) must equal the pure config(plan) computed from today's declared sources.
        """
        candidates = []
        for plan in sorted(chain.PLANS):
            staging = staging_of(plan)
            pins = staging / 'REVIEW-INPUTS.json'
            staged_config = staging / 'config.json'
            if not pins.is_file() or not staged_config.is_file():
                continue
            hashes = json.loads(pins.read_text(encoding='utf-8'))['hashes']
            if hashes.get(CONTROLLER) != controller_digest():
                continue  # archival staging: reviewed against older controller bytes
            try:
                expected = chain.config(plan)
            except SystemExit:
                continue  # a declared input source no longer exists -> archival staging
            staged = json.loads(staged_config.read_text(encoding='utf-8'))
            if staged.get('inputs') != expected.get('inputs') or staged.get('record_gates') != expected.get('record_gates'):
                continue
            candidates.append(plan)
            outcome = check(plan)
            self.assertEqual([], outcome['report']['failures'], '%s: %s' % (plan, outcome['report']))
            self.assertEqual(0, outcome['returncode'], plan)
        if not candidates:
            self.skipTest('no staging currently matches prepare(); stage the next plan to exercise the strict check')


if __name__ == '__main__':
    unittest.main()
