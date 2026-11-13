"""One explicitly authorized ad hoc role trial; never invokes the formal pipeline/deployer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

from backend import Backend, ps_quote
from compile_e387_roles import NAMES

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'campaigns/uav-2026/evidence/uav-airfoil-e387/p1.3-fix-20261024'
RUNNER = ROOT / 'scripts/security/run_e387_roles_adhoc.ps1'
INPUTS = {'E387_WING.step': ROOT / 'cases/uav-airfoil-e387/geometry/E387_WING.step',
          'case_spec.json': ROOT / 'cases/uav-airfoil-e387/case_spec.json',
          'role_expectations.json': ROOT / 'cases/uav-airfoil-e387/role_expectations.json',
          'deployment-observation.json': FIX / 'connection-health.json'}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_review(review_path):
    review = json.loads(review_path.read_text())
    if review.get('approved') is not True or review.get('independent') is not True:
        raise ValueError('Current-byte independent approval is required')
    required = [f'macros/{n}.java' for n in NAMES] + [
        'scripts/run_e387_roles.py', 'scripts/security/run_e387_roles_adhoc.ps1',
        'scripts/compile_e387_roles.py', 'scripts/backend.py', 'scripts/casespec.py',
        'cases/uav-airfoil-e387/geometry/E387_WING.step',
        'cases/uav-airfoil-e387/case_spec.json', 'cases/uav-airfoil-e387/role_expectations.json',
        str((FIX / 'connection-health.json').relative_to(ROOT)),
        str((FIX / 'COMPILE-2502.json').relative_to(ROOT))]
    for rel in required:
        if review.get('hashes', {}).get(rel) != sha(ROOT / rel):
            raise ValueError('Review does not pin actual bytes: ' + rel)
    return review


def prepare(b, run_id, review_path, local):
    review = verify_review(review_path)
    compile_record = json.loads((FIX / 'COMPILE-2502.json').read_text())
    if compile_record['exit_code'] != 0 or compile_record['jar_count'] != 471:
        raise ValueError('Fresh successful 2502 compile required')
    for rel, digest in compile_record['source_hashes'].items():
        if sha(ROOT / rel) != digest:
            raise ValueError('Compiled source bytes have changed: ' + rel)
    remote = b.remote_root + '\\adhoc\\' + run_id
    b.ps(f"if(Test-Path -LiteralPath {ps_quote(remote)}){{throw 'Ad hoc directory already exists; refuse replacement'}}\nNew-Item -ItemType Directory -Path {ps_quote(remote)} | Out-Null\nNew-Item -ItemType Directory -Path {ps_quote(remote + chr(92) + 'sources')} | Out-Null")
    files = dict(INPUTS)
    files['review.json'] = review_path
    files['run_e387_roles_adhoc.ps1'] = RUNNER
    for name in NAMES:
        files['sources/' + name + '.java'] = ROOT / 'macros' / (name + '.java')
    for name, path in files.items():
        b.scp(path, remote + '\\' + name.replace('/', '\\'))
    # Copy only freshly compiled class bytes from this compile record; Windows re-hashes every file.
    class_dir = compile_record['remote_directory'] + '\\out'
    b.ps(f'Copy-Item -LiteralPath {ps_quote(class_dir)} -Destination {ps_quote(remote + chr(92) + "classes")} -Recurse')
    old_health = json.loads((FIX / 'connection-health.json').read_text())
    config = {
        'schema_version': 1, 'job_dir': remote, 'run_id': run_id,
        'star_launcher': b.config['star_launcher'], 'star_version': old_health['star_version'],
        'runtime_deployment_modified': False,
        'deployment_difference': {k: old_health[k] for k in [
            'runtime_verified', 'worker_deployed', 'deployment_error', 'runtime_digest',
            'business_runtime_all_files_match_disk']},
        'deployment_difference_observation': 'Current fix-round health snapshot (queried_at in deployment-observation.json); live resource and STAR log checks are recorded by runner. No deployment change.',
        'inputs': {name: sha(path) for name, path in INPUTS.items()},
        'macros': {n + '.java': sha(ROOT / 'macros' / (n + '.java')) for n in NAMES},
        'compiled_classes': {p['name']: p['sha256'] for p in compile_record['class_hashes']},
        'review_sha256': sha(review_path), 'approved_sources': review['hashes'],
        'prepared_at': datetime.now(timezone.utc).isoformat(),
    }
    local.mkdir(parents=True, exist_ok=False)
    cfg = local / 'config.json'
    cfg.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    b.scp(cfg, remote + '\\config.json')
    (local / 'handle.json').write_text(json.dumps({
        'remote': remote, 'config_sha256': sha(cfg), 'review_sha256': sha(review_path)}, indent=2) + '\n')
    print(json.dumps({'prepared': True, 'remote': remote, 'local': str(local), 'star_started': False}))


def start(b, local):
    handle = json.loads((local / 'handle.json').read_text())
    remote = handle['remote']
    # CREATE_NEW claim prevents duplicate launch even if an SSH reply is lost.
    (local / 'launch-request.json').write_text(json.dumps({
        'status': 'DIRECT_SSH_RUNNING_UNTIL_RUNNER_RETURNS', 'remote': remote,
        'star_started': 'see durable Windows run.json', 'client_timeout_seconds': None}, indent=2) + '\n')
    r = b.ps(f'''
$claim=Join-Path {ps_quote(remote)} 'launch.claim'
$file=[IO.File]::Open($claim,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);$file.Dispose()
Set-Location -LiteralPath {ps_quote(remote)}
& {ps_quote(remote + chr(92) + 'run_e387_roles_adhoc.ps1')} -ConfigPath {ps_quote(remote + chr(92) + 'config.json')} -ConfigSha256 {ps_quote(handle['config_sha256'])} -ReviewSha256 {ps_quote(handle['review_sha256'])}
[IO.File]::ReadAllText((Join-Path {ps_quote(remote)} 'run.json'))
''', timeout=None)
    (local / 'launch.json').write_text(json.dumps(r, indent=2) + '\n')
    print(json.dumps(r))


def status(b, local):
    h = json.loads((local / 'handle.json').read_text())
    remote = h['remote']
    r = b.ps(f'''
$run=Join-Path {ps_quote(remote)} 'run.json'
if(Test-Path -LiteralPath $run){{[IO.File]::ReadAllText($run)}}else{{$stderr='';$err=Join-Path {ps_quote(remote)} 'runner.stderr.log';if(Test-Path -LiteralPath $err){{$stderr=[IO.File]::ReadAllText($err)}};[pscustomobject]@{{status='NO_RUN_RECORD';stderr=$stderr}} | ConvertTo-Json}}
''')
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return r


def fetch(b, local):
    h = json.loads((local / 'handle.json').read_text())
    remote = h['remote']
    current = status(b, local)
    if current.get('status') not in ['COMPLETED', 'FAILED']:
        raise ValueError('Terminal state required before evidence fetch')
    result = b.ps(f'''
$root={ps_quote(remote)}
$files=@(Get-ChildItem -LiteralPath $root -File -Recurse | Where-Object {{$_.Name -notin @('evidence.zip','WINDOWS-SHA256SUMS.json')}})
$sums=@($files | ForEach-Object {{[pscustomobject]@{{path=$_.FullName.Substring($root.Length+1).Replace([char]92,[char]47);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant();bytes=$_.Length}}}})
[IO.File]::WriteAllText((Join-Path $root 'WINDOWS-SHA256SUMS.json'),($sums | ConvertTo-Json -Depth 4),(New-Object Text.UTF8Encoding($false)))
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip=Join-Path $root 'evidence.zip'
if(Test-Path -LiteralPath $zip){{throw 'Evidence archive already exists; preserve it'}}
$archive=[IO.Compression.ZipFile]::Open($zip,[IO.Compression.ZipArchiveMode]::Create)
try{{foreach($f in @(Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object {{$_.FullName -ne $zip}})){{[IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive,$f.FullName,$f.FullName.Substring($root.Length+1).Replace([char]92,[char]47)) | Out-Null}}}}finally{{$archive.Dispose()}}
[pscustomobject]@{{sha256=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant();bytes=(Get-Item -LiteralPath $zip).Length}} | ConvertTo-Json
''', timeout=60)
    archive = local / 'evidence.zip'
    b.scp(archive, remote + '\\evidence.zip', download=True)
    if sha(archive) != result['sha256']:
        raise ValueError('Downloaded evidence archive hash differs')
    target = local / 'windows'
    target.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (target / name).resolve().is_relative_to(target.resolve()):
                raise ValueError('Unsafe artifact path')
        z.extractall(target)
    for record in json.loads((target / 'WINDOWS-SHA256SUMS.json').read_text(encoding='utf-8-sig')):
        if sha(target / record['path']) != record['sha256']:
            raise ValueError('Artifact hash differs: ' + record['path'])
    (local / 'transfer.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'fetched': True, 'verified': True, 'archive_sha256': result['sha256'], 'local': str(target)}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'start', 'status', 'fetch'])
    p.add_argument('--run-id', required=True)
    p.add_argument('--review', type=Path)
    args = p.parse_args()
    if not re.fullmatch(r'e387-p13-[a-z0-9-]{4,40}', args.run_id):
        raise ValueError('Scoped simple run id required')
    local = ROOT / 'campaigns/uav-2026/evidence/uav-airfoil-e387/role-readback' / args.run_id
    b = Backend()
    if args.command == 'prepare':
        if args.review is None:
            raise ValueError('--review required')
        prepare(b, args.run_id, args.review.resolve(), local)
    elif args.command == 'start': start(b, local)
    elif args.command == 'status': status(b, local)
    else: fetch(b, local)


if __name__ == '__main__':
    main()
