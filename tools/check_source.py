"""Verify this sanitized source distribution without network access."""
from pathlib import Path
import hashlib
import json
import re
import sys
ROOT = Path(__file__).resolve().parents[1]
def check(root=ROOT):
    errors = []
    forbidden = {'.sim', '.step', '.stp', '.stl', '.f3d', '.pem', '.key', '.hm', '.msh', '.cgns', '.npz', '.h5', '.hdf5', '.msg'}
    secret = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----)')
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(x in rel.parts for x in ('.git', '.venv', '__pycache__', '.pytest_cache')) or not path.is_file():
            continue
        if path.suffix in forbidden or path.name in ('connection.json', '.mcp.json', '.env'):
            errors.append('Private/native file: ' + str(rel))
        if path.stat().st_size > 10 * 1024 * 1024:
            errors.append('File exceeds 10 MiB: ' + str(rel))
        if secret.search(path.read_bytes()):
            errors.append('Credential pattern: ' + str(rel))
    snapshot = json.loads((root / 'docs/source-snapshot.json').read_text())
    for item in snapshot['files']:
        path = root / item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            errors.append('Snapshot mismatch: ' + item['path'])
    for line in (root / 'runtime-manifest.txt').read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and not (root / line).is_file():
            errors.append('Missing runtime member: ' + line)
    return errors
if __name__ == '__main__':
    errors = check()
    print('\n'.join(errors) if errors else 'Source distribution checks passed.')
    sys.exit(bool(errors))
