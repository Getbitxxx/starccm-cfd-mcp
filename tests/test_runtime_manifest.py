# -*- coding: utf-8 -*-
"""`runtime-manifest.txt` must equal what would actually deploy, and must not silently drift.

Why this exists (2026-11-03/04, the round that lost a night to it):

`runtime_files()` builds the Windows runtime with a glob (`macros/*.java`, `worker/*.ps1`,
`workflows/*/manifest.json`, `scripts/resource_contract.py`). So **dropping any `.java` into `macros/`
changes `runtime_digest` and invalidates the deployment approval** — with no decision by anyone. During
the Phase-3 mesh push 24 experiment macros landed there, the runtime went 133 -> 157 files, and the
formal path stopped: `deployment_review()` refuses on the first pin, `worker_deployed` goes false, and
`submit_*` never starts STAR. The only way out that night was a rollback.

The root cause is that "which files are the runtime" has no declarative answer. `runtime-manifest.txt`
is that answer. This test keeps it honest:

  * the manifest and the glob must describe the SAME set — one extra file in `macros/` (usually a
    scratch macro) fails here, in a second, instead of surfacing as a mystery deployment mismatch;
  * `macros/adhoc/` must never be listed (it is outside the non-recursive glob by design);
  * every macro a workflow stage names must be in the manifest, otherwise a promoted stage would ask
    Windows for a macro that was never deployed;
  * the digest computed from the manifest must equal the digest computed from the glob, which is what
    proves the manifest is complete rather than merely plausible.

Note on enforcement: `runtime_files()` now READS this manifest (2026-11-04 new deployment), so the
declaration is the runtime rather than a comment about it. The path checks in `runtime_source_files()`
are part of that: an entry must resolve inside the repository and inside one of the four runtime
categories, otherwise a manifest could smuggle `../outside.java` or `macros/adhoc/exp.java` into the
digest. Those refusals are tested below, because "the test would notice" is not the same as "the code
refuses".

Run: python3 -m unittest tests.test_runtime_manifest -v
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'runtime-manifest.txt'


def listed():
    entries = []
    for line in MANIFEST.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append(line)
    return entries


def globbed():
    """What the OLD glob would have collected, walked from disk and independent of backend.

    This side must never call Backend.runtime_files(): once that reads the manifest, comparing the two
    would be comparing the manifest with itself and an unlisted file would pass silently. Walking the
    directories is the whole point - it is the drift detector.
    """
    files = [*ROOT.glob('macros/*.java'), *ROOT.glob('worker/*.ps1'),
             *ROOT.glob('workflows/*/manifest.json'), ROOT / 'scripts/resource_contract.py']
    return [p.relative_to(ROOT).as_posix() for p in files if p.is_file()]


class RuntimeManifest(unittest.TestCase):
    def setUp(self):
        self.entries = listed()
        self.actual = globbed()

    def test_manifest_has_no_duplicates_and_every_entry_exists(self):
        self.assertEqual(len(self.entries), len(set(self.entries)),
                         'runtime-manifest.txt lists a path twice')
        missing = [e for e in self.entries if not (ROOT / e).is_file()]
        self.assertEqual([], missing, 'runtime-manifest.txt lists files that do not exist')

    def test_manifest_equals_what_would_actually_deploy(self):
        # The whole point: a new file in macros/ (or a deleted one) must fail HERE, immediately.
        extra = sorted(set(self.actual) - set(self.entries))
        gone = sorted(set(self.entries) - set(self.actual))
        self.assertEqual([], extra,
                         'these files would enter the Windows runtime without being declared in '
                         'runtime-manifest.txt: %r. Either move them to macros/adhoc/ or add them to '
                         'the manifest and obtain a new deployment review.' % extra)
        self.assertEqual([], gone,
                         'runtime-manifest.txt declares files that are no longer collected: %r' % gone)
        self.assertEqual(sorted(self.entries), sorted(self.actual))

    def test_adhoc_macros_are_never_part_of_the_runtime(self):
        adhoc = [e for e in self.entries if '/adhoc/' in e]
        self.assertEqual([], adhoc, 'macros/adhoc/ is outside the runtime glob by design')
        present = sorted(p.name for p in (ROOT / 'macros' / 'adhoc').glob('*.java'))
        self.assertTrue(present, 'expected experiment macros to live in macros/adhoc/; found none')

    def test_every_macro_named_by_a_workflow_stage_is_declared(self):
        declared = set(self.entries)
        missing = []
        for path in sorted((ROOT / 'workflows').glob('*/manifest.json')):
            manifest = json.loads(path.read_text(encoding='utf-8-sig'))
            for stage, spec in (manifest.get('stages') or {}).items():
                for macro in (spec.get('macros') or []):
                    rel = 'macros/' + macro
                    if rel not in declared:
                        missing.append('%s (%s/%s)' % (rel, path.parent.name, stage))
        self.assertEqual([], sorted(set(missing)),
                         'a workflow stage names a macro that is not in the runtime manifest')

    def test_digest_from_the_manifest_equals_the_digest_from_the_glob(self):
        # Proves the manifest is COMPLETE, not just plausible: the digest is a function of the exact
        # file set and bytes, so any omission or extra entry changes it.
        sys.path.insert(0, str(ROOT / 'scripts'))
        from backend import build_runtime_manifest
        actual = build_runtime_manifest([ROOT / e for e in sorted(self.actual)])['runtime_digest']
        from_manifest = build_runtime_manifest([ROOT / e for e in sorted(self.entries)])['runtime_digest']
        self.assertEqual(actual, from_manifest,
                         'the manifest does not describe the same byte set as the glob')


class RuntimeSourceRefusals(unittest.TestCase):
    """The declaration must be a closed list: escaping paths and ad-hoc macros are REFUSED, not noticed."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import backend
        self.backend = backend
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'macros' / 'adhoc').mkdir(parents=True)
        (self.root / 'worker').mkdir()
        (self.root / 'macros' / 'Real.java').write_text('class Real {}\n', encoding='utf-8')
        (self.root / 'macros' / 'adhoc' / 'Exp.java').write_text('class Exp {}\n', encoding='utf-8')
        (self.root.parent / 'outside.java').write_text('class Outside {}\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()
        try:
            (self.root.parent / 'outside.java').unlink()
        except FileNotFoundError:
            pass

    def declare(self, *entries):
        (self.root / 'runtime-manifest.txt').write_text('\n'.join(entries) + '\n', encoding='utf-8')

    def test_a_missing_manifest_is_refused_rather_than_falling_back_to_a_glob(self):
        with self.assertRaises(ValueError) as caught:
            self.backend.runtime_source_files(self.root)
        self.assertIn('must be declared', str(caught.exception))

    def test_an_empty_declaration_is_refused(self):
        self.declare('# only comments', '')
        with self.assertRaises(ValueError):
            self.backend.runtime_source_files(self.root)

    def test_a_path_escaping_the_repository_is_refused(self):
        self.declare('../outside.java')
        with self.assertRaises(ValueError) as caught:
            self.backend.runtime_source_files(self.root)
        self.assertIn('../outside.java', str(caught.exception))

    def test_an_adhoc_macro_cannot_be_declared_into_the_runtime(self):
        self.declare('macros/adhoc/Exp.java')
        with self.assertRaises(ValueError) as caught:
            self.backend.runtime_source_files(self.root)
        self.assertIn('macros/adhoc/Exp.java', str(caught.exception))

    def test_a_declared_duplicate_is_refused(self):
        self.declare('macros/Real.java', 'macros/Real.java')
        with self.assertRaises(ValueError) as caught:
            self.backend.runtime_source_files(self.root)
        self.assertIn('twice', str(caught.exception))

    def test_a_normal_declaration_still_works(self):
        self.declare('# comment', '', 'macros/Real.java')
        self.assertEqual([self.root / 'macros' / 'Real.java'],
                         self.backend.runtime_source_files(self.root))

    def test_the_call_site_delegates_to_the_declared_source(self):
        # Pinning runtime_source_files() is not enough: someone could revert Backend.runtime_files() to
        # a glob and every test above would still pass. Making the primitive explode proves the call
        # site really delegates to it - a glob implementation would never touch this function.
        sentinel = RuntimeError('runtime_source_files was called')
        original = self.backend.runtime_source_files

        def explode(root=None):
            raise sentinel
        self.backend.runtime_source_files = explode
        try:
            with self.assertRaises(RuntimeError) as caught:
                self.backend.Backend().runtime_files()
        finally:
            self.backend.runtime_source_files = original
        self.assertIs(caught.exception, sentinel)


if __name__ == '__main__':
    unittest.main()
