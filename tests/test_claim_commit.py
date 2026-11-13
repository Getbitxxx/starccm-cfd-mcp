"""O9 for the Round-A re-check finding: the two Round-A commit messages quoted TRUNCATED sha256
values (``116c9475...`` / ``1fa232df...``) even though F4's rule says hashes must be full and a commit
message must be linted before it is committed.

The offending line is kept VERBATIM as a real fixture (AGENTS.md O9 prefers the real failure over a
synthetic one).  ``tests/fixtures/claim-commit-truncated-sha256-20261030.txt`` is that line.

Commit mode was added to scripts/claim_lint.py in the same change: a commit message has no markdown
report block and legitimately quotes 40-hex git object ids (sha1), so report mode cannot lint it.  The
commit block is rendered from the SAME manifest (digest + values read from the records) and verified.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import claim_lint as cl  # noqa: E402

STATUS = ROOT / 'campaigns/uav-2026/STATUS.json'
FIXTURE = ROOT / 'tests/fixtures/claim-commit-truncated-sha256-20261030.txt'
ENGINE = ROOT / 'scripts/run_mrf_chain.py'
# a real 40-hex GIT object id from this repository (commit "P3.0+P3.1 Round A..." before the re-check)
GIT_ID = 'ed6342b6f5f0a1b5adfe5379709c305301294a56'


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest() -> dict:
    status_sha = sha(STATUS)
    return {
        'schema_version': 1,
        'claims': [
            {'id': 'c1', 'text': 'plan engine line count',
             'value': 299, 'record': 'campaigns/uav-2026/STATUS.json',
             'field': 'phase3_round_a_20261030.p30.engine.lines',
             'record_sha256': status_sha},
            {'id': 'c2', 'text': 'plan engine sha256',
             'value': sha(ENGINE), 'record': 'campaigns/uav-2026/STATUS.json',
             'field': 'phase3_round_a_20261030.p30.engine.sha256',
             'record_sha256': status_sha},
        ],
    }


def good_text() -> str:
    """A commit-message shape: prose with the FULL sha256 + the rendered two-line digest."""
    text = ('Change control: controller sha256 %s is the measured engine hash; '
            'PIN-IMPACT lists the pinned stagings.\n\n' % sha(ENGINE))
    return text + cl.render_commit(manifest(), ROOT)


class CommitMode(unittest.TestCase):
    def test_real_round_a_commit_truncation_is_flagged(self):
        fixture = FIXTURE.read_text(encoding='utf-8')
        report = cl.lint(manifest(), ROOT, text=fixture, mode='commit')
        tokens = {e['token'].lower() for e in report['errors'] if e['code'] == 'HASH_TRUNCATED'}
        self.assertIn('116c9475', tokens)
        self.assertIn('1fa232df', tokens)
        self.assertTrue(report['errors'], 'the real truncated-hash commit line must not lint clean')

    def test_corrected_commit_message_lints_clean(self):
        report = cl.lint(manifest(), ROOT, text=good_text(), mode='commit')
        self.assertEqual([], report['errors'])
        self.assertEqual([], report['warnings'])

    def test_git_object_ids_are_not_truncated_sha256_claims(self):
        report = cl.lint(manifest(), ROOT, text=good_text() + '\ncommit %s\n' % GIT_ID, mode='commit')
        self.assertEqual([], report['errors'], report['errors'])

    def test_missing_digest_is_render_stale(self):
        report = cl.lint(manifest(), ROOT, text='no rendered digest here\n', mode='commit')
        self.assertIn('RENDER_STALE', {e['code'] for e in report['errors']})

    def test_tampered_digest_is_render_stale(self):
        text = good_text().replace(cl.manifest_digest(manifest()), '0' * 64)
        report = cl.lint(manifest(), ROOT, text=text, mode='commit')
        self.assertIn('RENDER_STALE', {e['code'] for e in report['errors']})

    def test_tampered_value_in_the_claims_line_is_number_mismatch(self):
        text = good_text().replace('c1=299', 'c1=291')     # the stale line-count value that started this
        report = cl.lint(manifest(), ROOT, text=text, mode='commit')
        self.assertIn('NUMBER_MISMATCH', {e['code'] for e in report['errors']})

    def test_report_mode_still_requires_the_report_block(self):
        report = cl.lint(manifest(), ROOT, text=good_text(), mode='report')
        self.assertIn('RENDER_STALE', {e['code'] for e in report['errors']})

    def test_unknown_mode_is_rejected(self):
        report = cl.lint(manifest(), ROOT, text=good_text(), mode='commit-ish')
        self.assertEqual('SCHEMA_INVALID', report['errors'][0]['code'])


if __name__ == '__main__':
    unittest.main()
