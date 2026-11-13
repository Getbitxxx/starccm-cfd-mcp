"""Negative fixtures for the source distribution gate."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from check_source import check

class DistributionGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'docs').mkdir()
        (self.root / 'docs/source-snapshot.json').write_text('{"files": []}')
        (self.root / 'runtime-manifest.txt').write_text('')

    def test_clean_tree_passes(self):
        self.assertEqual([], check(self.root))

    def test_real_configuration_is_refused(self):
        (self.root / 'connection.json').write_text('{}')
        self.assertTrue(any('Private/native' in e for e in check(self.root)))

    def test_native_simulation_is_refused(self):
        (self.root / 'input.sim').write_bytes(b'native')
        self.assertTrue(any('Private/native' in e for e in check(self.root)))

    def test_credential_pattern_is_refused(self):
        (self.root / 'bad.txt').write_text('gh' + 'p_' + 'a' * 36)
        self.assertTrue(any('Credential' in e for e in check(self.root)))

    def test_source_drift_is_refused(self):
        (self.root / 'a.py').write_text('changed')
        (self.root / 'docs/source-snapshot.json').write_text(json.dumps({'files': [
            {'path': 'a.py', 'sha256': hashlib.sha256(b'original').hexdigest()}]}))
        self.assertTrue(any('Snapshot mismatch' in e for e in check(self.root)))

    def test_missing_runtime_member_is_refused(self):
        (self.root / 'runtime-manifest.txt').write_text('worker/missing.ps1')
        self.assertTrue(any('Missing runtime' in e for e in check(self.root)))

if __name__ == '__main__':
    unittest.main()
