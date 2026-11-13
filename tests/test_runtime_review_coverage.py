# -*- coding: utf-8 -*-
"""Every file in the Windows runtime must be covered by a review that pins its CURRENT bytes.

Why this exists (2026-11-03, the same round that found it by hand):

`deployment_review()` already fails closed when the runtime digest moves, so a stale approval can
never start a job. What it cannot tell anyone is *which* files moved and *whether those particular
bytes were ever looked at by a human or an independent reviewer*. On 2026-11-03 answering that took
a hand-written scan of 21009 files, three of which instruments (ripgrep on this Mac) silently
returned "0 matches" for hashes that were demonstrably present. A gate whose answer depends on a
tool that can silently return empty is not a gate.

The invariant, and where coverage may come from:

    A runtime file is covered when its current sha256 appears in
      (a) the ACTIVE deployment approval's ``runtime_files``   (the umbrella review of one runtime), or
      (b) an independent review record's ``hashes``/``macro_hashes`` (a review of these very bytes).

Only (a)-for-the-ACTIVE-pointer counts as deployment coverage: an approval that has been written but
not activated describes a runtime that is not the one on disk, and saying otherwise is exactly the
"recorded but never compared" failure this repo keeps re-finding.

Pinned gaps (2026-11-04, re-measured after ``macros/LocateBadCells.java`` moved to
``macros/adhoc/`` — that subdirectory is outside the non-recursive ``macros/*.java`` glob, so the
runtime went 157 -> 156 files and this one stopped needing a review):

    macros/AuditExternalFlowConvergence.java   compile evidence only, exit 0, reviewed=false
    macros/ConfigureExternalFlowPhysics.java   compile evidence only, exit 0, reviewed=false
    macros/RunExternalFlowSolve.java           compile evidence only, exit 0, reviewed=false
    macros/ExportExternalFlowResults.java      changed 2026-10-27, no review, in no workflow but
                                               still named by registered-workflow code paths

Coverage is expected to become complete when the next deployment approval pins the whole 156-file
runtime: at that moment KNOWN_GAPS must be edited to ``frozenset()`` in the same commit. Leaving a
stale pin is a failure, not a courtesy.

Run: python3 -m unittest tests.test_runtime_review_coverage -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POINTER = ROOT / 'review' / 'formal-worker-deployment-active.json'
DEPLOY_PREFIX = 'review/formal-worker-deployment-'

# Measured 2026-11-04 (deployment 20261104c). The runtime is the 2026-10-23 approved 133 files plus the
# 23 experiment macros that had been moved to macros/adhoc/ during the rollback and back out again: each
# of the 23 carries an independent review pinning its exact bytes, so coverage stays complete.
# ``macros/LocateBadCells.java`` stays in macros/adhoc/ precisely because it has none.
KNOWN_GAPS = frozenset()

RUNTIME_FILES = 156
MIN_REVIEWS = 60


def runtime_files():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from backend import Backend
    return {entry['path']: entry['sha256'] for entry in Backend().runtime_manifest()['files']}


def active_approval():
    pointer = json.loads(POINTER.read_text(encoding='utf-8-sig'))
    ref = pointer['review_ref']
    assert ref.startswith(DEPLOY_PREFIX), ref
    return json.loads((ROOT / ref).read_text(encoding='utf-8-sig'))


def review_paths():
    paths = sorted((ROOT / 'review').glob('*.json'))
    for base in ('campaigns', 'test-evidence'):
        paths.extend(sorted((ROOT / base).rglob('review.json')))
    return paths


def independent_reviews():
    """(path, {runtime path: sha256}) for every approved+independent record that names bytes."""
    found = []
    for path in review_paths():
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get('approved') is not True or record.get('independent') is not True:
            continue
        hashes = {}
        for key in ('hashes', 'macro_hashes'):
            value = record.get(key)
            if isinstance(value, dict):
                hashes.update({k: v for k, v in value.items() if isinstance(v, str)})
        if hashes:
            found.append((path, hashes))
    return found


class RuntimeReviewCoverage(unittest.TestCase):
    def setUp(self):
        self.files = runtime_files()
        self.approval = active_approval()
        self.reviews = independent_reviews()

    def coverage(self):
        covered = {}
        for entry in self.approval.get('runtime_files') or []:
            if isinstance(entry, dict) and entry.get('sha256') == self.files.get(entry.get('path')):
                covered[entry['path']] = str(POINTER)
        for path, hashes in self.reviews:
            for name, sha in hashes.items():
                if self.files.get(name) == sha and name not in covered:
                    covered[name] = str(path.relative_to(ROOT))
        return covered

    def test_the_scan_actually_looked_at_something(self):
        # A scan that finds nothing must fail loudly: three silent "0 matches" happened on
        # 2026-11-03 while the hashes were in the files.
        self.assertEqual(len(self.files), RUNTIME_FILES,
                         'runtime file count changed; re-measure KNOWN_GAPS before touching this pin')
        self.assertGreaterEqual(len(self.reviews), MIN_REVIEWS,
                                'too few independent reviews found; the scan is probably broken')
        self.assertEqual(len(self.approval.get('runtime_files') or []), 156,
                         'active deployment approval changed size; coverage arithmetic is stale')

    def test_every_runtime_file_has_a_review_of_its_exact_bytes(self):
        covered = self.coverage()
        gaps = frozenset(set(self.files) - set(covered))
        self.assertEqual(sorted(gaps), sorted(KNOWN_GAPS),
                         'runtime review coverage changed: ' + repr(sorted(set(gaps) ^ KNOWN_GAPS)))

    def test_no_review_is_credited_to_the_wrong_bytes(self):
        # A review that pins a different sha256 for the same path must not silently cover it.
        for path, hashes in self.reviews:
            for name, sha in hashes.items():
                if name in self.files and sha != self.files[name]:
                    self.assertNotEqual(sha, self.files[name])


if __name__ == '__main__':
    unittest.main()
