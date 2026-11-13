"""Exercise terminal evidence extraction/hash validation without starting STAR."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_e387_roles import fetch


class EvidenceFetch(unittest.TestCase):
    def test_compression_assemblies_and_actual_extraction_hash_check(self):
        payload = b'{"status":"error","reason":"surface_count"}\n'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            z.writestr('import/import.json', payload)
            z.writestr('WINDOWS-SHA256SUMS.json', json.dumps([{
                'path': 'import/import.json', 'sha256': hashlib.sha256(payload).hexdigest()}]))
        archive = buffer.getvalue()
        class Backend:
            def __init__(self): self.scripts = []
            def ps(self, script, timeout=40):
                self.scripts.append(script)
                if len(self.scripts) == 1: return {'status': 'FAILED'}
                return {'sha256': hashlib.sha256(archive).hexdigest(), 'bytes': len(archive)}
            def scp(self, local, remote, download=False):
                if not download: raise AssertionError('Read-only fetch required')
                local.write_bytes(archive)
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder)
            (local / 'handle.json').write_text(json.dumps({'remote': r'C:\owned\e387'}))
            b = Backend()
            with contextlib.redirect_stdout(io.StringIO()): fetch(b, local)
            self.assertIn('Add-Type -AssemblyName System.IO.Compression\n', b.scripts[1])
            self.assertIn('Add-Type -AssemblyName System.IO.Compression.FileSystem', b.scripts[1])
            self.assertEqual((local / 'windows/import/import.json').read_bytes(), payload)
            self.assertEqual(json.loads((local / 'transfer.json').read_text())['sha256'], hashlib.sha256(archive).hexdigest())


if __name__ == '__main__': unittest.main()
