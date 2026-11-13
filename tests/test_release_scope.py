"""Release scope: publishing is a declared subset, and holds fail closed.

Before this, `code_files()` collected every .py under scripts/, cases/ and cad/, so each
release silently blessed stray spike drivers and a reviewer had to sign files the change
never touched. These tests pin the new behaviour: absent scope keeps the legacy mode, a
hold removes files from the manifest, and every way of holding something the published
code still needs is refused.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import mcp_runtime_release as release  # noqa: E402


class ScopeFixture(unittest.TestCase):
    """A minimal project tree: entry, gateway files, an imported helper, a standalone driver."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='scope-fixture-', dir=ROOT / 'tmp'))
        for directory in ('scripts', 'cases', 'cad'):
            (self.root / directory).mkdir()
        (self.root / 'scripts/server_v2.py').write_text('import helper\n', encoding='utf-8')
        (self.root / 'scripts/helper.py').write_text('VALUE = 1\n', encoding='utf-8')
        (self.root / 'scripts/server_gateway.py').write_text('# gateway\n', encoding='utf-8')
        (self.root / 'scripts/mcp_runtime_release.py').write_text('# release tool\n', encoding='utf-8')
        (self.root / 'scripts/mcp_business_worker.py').write_text('# worker\n', encoding='utf-8')
        (self.root / 'scripts/spike_driver.py').write_text('print("one-off")\n', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_scope(self, document):
        (self.root / 'release-scope.json').write_text(json.dumps(document), encoding='utf-8')


class LegacyModeTests(ScopeFixture):
    def test_absent_scope_publishes_everything(self):
        self.assertIsNone(release.load_scope(self.root)['file'])
        files = release.code_files(self.root)
        self.assertEqual(len(files), 6)
        self.assertIn('scripts/spike_driver.py', files)


class ScopeValidationTests(ScopeFixture):
    def test_unknown_keys_are_refused(self):
        self.write_scope({'schema_version': 1, 'hold': [], 'exclude': ['x']})
        with self.assertRaisesRegex(ValueError, 'Unknown release scope keys'):
            release.load_scope(self.root)

    def test_hold_must_be_a_list_of_non_empty_globs(self):
        for bad in ('scripts/a.py', ['  '], [1]):
            self.write_scope({'schema_version': 1, 'hold': bad})
            with self.subTest(hold=bad):
                with self.assertRaises(ValueError):
                    release.load_scope(self.root)

    def test_pattern_that_matches_nothing_fails_the_build(self):
        self.write_scope({'schema_version': 1, 'hold': ['scripts/typo_*.py']})
        with self.assertRaisesRegex(ValueError, 'matches no code file'):
            release.code_files(self.root)

    def test_holding_the_business_entry_is_refused(self):
        self.write_scope({'schema_version': 1, 'hold': ['scripts/server_v2.py']})
        with self.assertRaisesRegex(ValueError, 'business entry'):
            release.code_files(self.root)

    def test_holding_a_gateway_file_is_refused(self):
        self.write_scope({'schema_version': 1, 'hold': ['scripts/mcp_runtime_release.py']})
        with self.assertRaisesRegex(ValueError, 'gateway file'):
            release.code_files(self.root)


class HoldFailsClosedTests(ScopeFixture):
    def test_holding_an_imported_helper_is_refused(self):
        self.write_scope({'schema_version': 1, 'hold': ['scripts/helper.py']})
        with self.assertRaisesRegex(ValueError, 'Held code is imported by published code'):
            release.code_files(self.root)

    def test_holding_a_file_named_in_published_code_is_refused(self):
        (self.root / 'scripts/server_v2.py').write_text(
            'import helper\nNAME = "spike_driver.py"\n', encoding='utf-8')
        self.write_scope({'schema_version': 1, 'hold': ['scripts/spike_driver.py']})
        with self.assertRaisesRegex(ValueError, 'referenced by published code'):
            release.code_files(self.root)

    def test_a_safe_hold_only_removes_that_file(self):
        self.write_scope({'schema_version': 1, 'hold': ['scripts/spike_driver.py']})
        files = release.code_files(self.root)
        self.assertNotIn('scripts/spike_driver.py', files)
        self.assertIn('scripts/helper.py', files)
        summary = release.scope_summary(self.root, release.load_scope(self.root), files)
        self.assertEqual(summary['held'], ['scripts/spike_driver.py'])
        self.assertEqual(summary['published'], len(files))
        self.assertEqual(len(summary['sha256']), 64)


class RealScopeTests(unittest.TestCase):
    """The repository's own scope file must describe what the release actually holds."""

    def test_repository_scope_publishes_the_mcp_path_and_holds_the_spike_drivers(self):
        scope = release.load_scope(ROOT)
        files = release.code_files(ROOT, scope)
        self.assertEqual(scope['file'], 'release-scope.json')
        self.assertTrue(scope['hold'])
        for entry in release.GATEWAY_FILES:
            self.assertIn(entry, files)
        self.assertIn('scripts/server_v2.py', files)
        self.assertIn('scripts/quality_contract.py', files)
        self.assertNotIn('scripts/run_trapwing_te_prism.py', files)
        self.assertNotIn('scripts/build_te_variant_fc.py', files)
        self.assertLess(len(files), 121, 'scope must actually remove files from the release')

    def test_scope_summary_names_every_held_file(self):
        scope = release.load_scope(ROOT)
        files = release.code_files(ROOT, scope)
        summary = release.scope_summary(ROOT, scope, files)
        self.assertEqual(sorted(summary['held']), sorted(scope['hold']))
        self.assertEqual(summary['held_count'], len(scope['hold']))
        self.assertTrue(summary['reason'])


if __name__ == '__main__':
    unittest.main()
