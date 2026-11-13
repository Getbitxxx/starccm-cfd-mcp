"""Keep the ad hoc SSH session alive until its Windows runner exits."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_e387_roles import start


class DirectStart(unittest.TestCase):
    def test_direct_invocation_waits_without_detached_child_or_client_timeout(self):
        class Backend:
            def ps(self, script, timeout=40):
                self.script, self.timeout = script, timeout
                return {'status': 'FAILED', 'star_processes_started': 0}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'handle.json').write_text(json.dumps({'remote': r'C:\owned\e387',
                'config_sha256': 'a'*64, 'review_sha256': 'b'*64}))
            b = Backend()
            start(b, path)
            self.assertIsNone(b.timeout)
            self.assertIn('& ', b.script)
            self.assertIn('run_e387_roles_adhoc.ps1', b.script)
            self.assertIn('[IO.FileMode]::CreateNew', b.script)
            self.assertNotIn('Start-Process', b.script)
            self.assertIn('[IO.File]::ReadAllText', b.script)
            self.assertEqual(json.loads((path / 'launch.json').read_text())['star_processes_started'], 0)


if __name__ == '__main__': unittest.main()
