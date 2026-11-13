"""Content-addressed Python releases for the stable MCP gateway.

Business data stays in the project directory. Only code is copied; publication
changes current.json atomically and never rewrites an existing release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CODE_DIRECTORIES = ('scripts', 'cases', 'cad')
# Declared publishing scope. Absent file = publish every code file (the old behaviour); with
# it, files matching `hold` stay in the work tree and OUT of the manifest, so a release no
# longer silently blesses every stray script under scripts/. Holds are fail-closed: a held
# file that published code imports, or a pattern that matches nothing, refuses the build.
SCOPE_FILE = 'release-scope.json'
GATEWAY_FILES = ('scripts/server_gateway.py', 'scripts/mcp_runtime_release.py')
READ_OPEN = open


# One source for MCP error text and the generated user guide. No automatic retry.
ERROR_ACTIONS = {
    'REJECTED_BUSY': '已有任务占用单槽。用原 run_id 查询状态，等待空槽；不要重复提交或结束其他 STAR。',
    'GATEWAY_RECONNECT_REQUIRED': '活动入口字节已变。先确认已审查版本已激活，再在 MCP 设置中重连 starccm-cfd 并刷新工具列表。',
    'TOOL_TIMEOUT': '本地工具调用超时不证明远端任务已停止。用原 run_id 查询状态并取回证据；不要创建新 request_id 重交。',
    'RELEASE_REVIEW_REQUIRED': '业务版本缺少独立审查。由维护者准备版本并取得完整哈希独审后激活；不要绕过审查。',
    'RELEASE_REVIEW_MISMATCH': '业务版本与独审字节不符。由维护者核对完整版本清单并重新独审；不要手改旧批准。',
    'UNPUBLISHED_CODE': '工作树代码尚未发布。由维护者一次完成修改、测试、prepare、独审和 activate，再调用写操作。',
    'DEPLOYMENT_REVIEW_MISMATCH': 'Windows 运行时与部署独审不符。由维护者核对差异；取得新独立部署审查后才可部署和健康核验。不要修改 scope 或旧批准。',
    'TOOL_CONTRACT_CHANGED': '工具契约已变。刷新 tools/list；客户端未刷新时重连 starccm-cfd 后再查询，勿重交已有任务。',
    'RESULTS_QUOTA_EXCEEDED': '本地结果缓存达到配额或磁盘余量不足。先保全被证据引用的文件，人工清理未引用缓存或调整结果目录/配额，再取回结果。',
    'UNCLASSIFIED_ERROR': '保留完整错误与 run_id，交由维护者诊断。不要自动重试 STAR 提交。',
}


_REQUIRED_ERROR_CODES = frozenset(ERROR_ACTIONS)


def validate_error_actions(mapping):
    if set(mapping) != _REQUIRED_ERROR_CODES or any(not isinstance(v,str) or not v.strip() for v in mapping.values()):
        raise ValueError('Error code lacks corresponding user action')
    return True


def error_diagnostic(error):
    validate_error_actions(ERROR_ACTIONS)
    text=str(error)
    code=next((c for c in ERROR_ACTIONS if c != 'UNCLASSIFIED_ERROR' and c in text),None)
    if 'Deployment approval differs' in text or 'Formal deployment support bytes differ from review' in text:
        code='DEPLOYMENT_REVIEW_MISMATCH'
    code=code or ('TOOL_TIMEOUT' if isinstance(error,TimeoutError) else 'UNCLASSIFIED_ERROR')
    return {'error_code':code,'user_action':ERROR_ACTIONS[code],'detail':text,'automatic_retry':False}


def diagnostic_text(error):
    d=error_diagnostic(error)
    return d['detail']+'\n错误码: '+d['error_code']+'\n处理方法: '+d['user_action']


def render_error_actions():
    validate_error_actions(ERROR_ACTIONS)
    return '| 错误码 | 用户操作 |\n|---|---|\n'+'\n'.join('| '+k+' | '+v+' |' for k,v in ERROR_ACTIONS.items())+'\n'


def file_hash(path):
    with READ_OPEN(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def tool_hash(tool):
    return canonical_hash(tool)


def runtime_directory(project_root, runtime_root=None):
    return Path(runtime_root or Path(project_root) / 'work/mcp-runtime').resolve()


def load_scope(project_root):
    """Read the declared scope. Missing file keeps the legacy 'publish everything' mode."""
    path = Path(project_root).resolve() / SCOPE_FILE
    if not path.is_file():
        return {'file': None, 'sha256': None, 'hold': [], 'reason': None}
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('Release scope must be an object with schema_version 1')
    unknown = set(document) - {'schema_version', 'hold', 'reason'}
    if unknown:
        raise ValueError('Unknown release scope keys: ' + ', '.join(sorted(unknown)))
    hold = document.get('hold') or []
    if not isinstance(hold, list) or any(not isinstance(item, str) or not item.strip() for item in hold):
        raise ValueError('Release scope hold must be a list of non-empty globs')
    return {'file': SCOPE_FILE, 'sha256': file_hash(path), 'hold': [item.strip() for item in hold],
            'reason': document.get('reason')}


def _held(relative, patterns):
    return any(Path(relative).match(pattern) for pattern in patterns)


def _imports_of(path):
    import ast
    try:
        tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    except (OSError, SyntaxError) as exc:
        raise ValueError('Cannot parse published code %s: %s' % (path, exc)) from exc
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split('.')[0])
    return modules


def require_published_closure(project_root, published, everything):
    """A held file that published code imports would fail at call time; refuse the build."""
    root = Path(project_root).resolve()
    for relative in sorted(published):
        for module in _imports_of(root / relative):
            candidates = ['%s/%s.py' % (directory, module) for directory in CODE_DIRECTORIES]
            target = next((name for name in candidates if name in everything), None)
            if target and target not in published:
                raise ValueError('Held code is imported by published code: %s (imported by %s)'
                                 % (target, relative))
    for entry in GATEWAY_FILES:
        if entry not in published:
            raise ValueError('Release scope must publish the gateway file ' + entry)
    held = set(everything) - set(published)
    if held:
        for relative in sorted(published):
            text = (root / relative).read_text(encoding='utf-8')
            for name in sorted(held):
                if Path(name).name in text:
                    raise ValueError('Held code is referenced by published code: %s (in %s)'
                                     % (name, relative))


def code_files(project_root, scope=None):
    root = Path(project_root).resolve()
    found = {}
    for directory in CODE_DIRECTORIES:
        for path in sorted((root / directory).rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Code must be a regular project file: ' + str(path))
            found[str(path.relative_to(root))] = file_hash(path)
    if 'scripts/server_v2.py' not in found:
        raise ValueError('Business entry scripts/server_v2.py is missing')
    scope = scope if scope is not None else load_scope(root)
    patterns = scope.get('hold') or []
    if patterns:
        for pattern in patterns:
            if not any(Path(name).match(pattern) for name in found):
                raise ValueError('Release scope pattern matches no code file: ' + pattern)
        everything = dict(found)
        found = {name: value for name, value in found.items() if not _held(name, patterns)}
        require_published_closure(root, found, everything)
        if 'scripts/server_v2.py' not in found:
            raise ValueError('Release scope held the business entry scripts/server_v2.py')
    return found


def scope_summary(project_root, scope, published):
    """What the manifest records about the scope: the declaration, and exactly what it held."""
    patterns = scope.get('hold') or []
    everything = code_files(project_root, scope={'hold': [], 'file': None, 'sha256': None, 'reason': None})
    held = sorted(set(everything) - set(published)) if patterns else []
    return {'file': scope.get('file'), 'sha256': scope.get('sha256'),
            'hold': list(patterns), 'held': held,
            'published': len(published), 'held_count': len(held),
            'reason': scope.get('reason')}


def release_identity(manifest):
    return {key: manifest[key] for key in
            ('schema_version', 'project_root', 'files', 'tools', 'instructions', 'launcher_sha256')}


def validate_manifest(directory, project_root, expected_sha=None, preview=False):
    directory = Path(directory).resolve()
    path = directory / 'manifest.json'
    if expected_sha and file_hash(path) != expected_sha:
        raise ValueError('Release manifest hash mismatch')
    manifest = json.loads(path.read_text())
    if manifest.get('schema_version') != 1 or manifest.get('project_root') != str(Path(project_root).resolve()):
        raise ValueError('Release belongs to a different project or schema')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Empty release file map')
    for name, expected in files.items():
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.suffix != '.py' or
                relative.parts[0] not in CODE_DIRECTORIES or len(expected) != 64):
            raise ValueError('Invalid release file: ' + name)
        source = directory / 'files' / relative
        if (source.is_symlink() or not source.resolve().is_relative_to(directory / 'files') or
                file_hash(source) != expected):
            raise ValueError('Release code hash mismatch: ' + name)
    if not preview:
        if directory.name != manifest.get('release_id'):
            raise ValueError('Release directory identity mismatch')
        if manifest.get('release_id') != canonical_hash(release_identity(manifest)):
            raise ValueError('Release identity mismatch')
        if file_hash(directory / 'python') != manifest['launcher_sha256']:
            raise ValueError('Release launcher hash mismatch')
    return manifest


def active_runtime(project_root, runtime_root=None):
    runtime = runtime_directory(project_root, runtime_root)
    pointer = json.loads((runtime / 'current.json').read_text())
    release_id = pointer.get('release_id', '')
    if (pointer.get('schema_version') != 1 or len(release_id) != 64 or
            any(c not in '0123456789abcdef' for c in release_id)):
        raise ValueError('Invalid current release pointer')
    folder = runtime / 'releases' / release_id
    if folder.is_symlink():
        raise ValueError('Release directory must not be a symlink')
    manifest = validate_manifest(folder, project_root, pointer.get('manifest_sha256'))
    if manifest['release_id'] != release_id:
        raise ValueError('Pointer and release identity differ')
    return dict(manifest, release_dir=str(folder), manifest_sha256=file_hash(folder / 'manifest.json'))


def disk_hashes(project_root, files):
    root = Path(project_root).resolve()
    result = {}
    for relative in files:
        try:
            result[relative] = file_hash(root / relative)
        except (FileNotFoundError, IsADirectoryError):
            result[relative] = None
    return result


def require_release_review(directory, manifest):
    """The transport/loader is execution support, with its own exact-byte review.

    This never replaces the existing per-job pipeline/controller/runner/input
    review. Discovery can inspect a draft; actual calls require both as applicable.
    """
    directory = Path(directory)
    path = directory / 'release-review.json'
    try:
        review = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ValueError('RELEASE_REVIEW_REQUIRED: independent runtime review is missing') from exc
    if (review.get('approved') is not True or review.get('independent') is not True or
            review.get('release_id') != manifest['release_id'] or
            review.get('manifest_sha256') != file_hash(directory / 'manifest.json') or
            review.get('runtime_hashes') != manifest['files']):
        raise ValueError('RELEASE_REVIEW_MISMATCH: actual runtime differs from approved bytes')
    return review


def activate_release(project_root, release_id, runtime_root=None):
    runtime = runtime_directory(project_root, runtime_root)
    folder = runtime / 'releases' / release_id
    manifest = validate_manifest(folder, project_root)
    if manifest['release_id'] != release_id:
        raise ValueError('Activation identity differs')
    pointer = {'schema_version': 1, 'release_id': release_id,
               'manifest_sha256': file_hash(folder / 'manifest.json')}
    fd, name = tempfile.mkstemp(prefix='.current-', dir=runtime)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(pointer, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, runtime / 'current.json')
    finally:
        if Path(name).exists():
            Path(name).unlink()
    return dict(manifest, release_dir=str(folder), manifest_sha256=pointer['manifest_sha256'])


def publish_release(project_root, runtime_root=None, python_executable=sys.executable, activate=True):
    root = Path(project_root).resolve()
    runtime = runtime_directory(root, runtime_root)
    releases = runtime / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    scope = load_scope(root)
    sources = code_files(root, scope=scope)
    staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=releases))
    try:
        for relative, expected in sources.items():
            dest = staging / 'files' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, dest)
            if file_hash(dest) != expected:
                raise ValueError('Source changed during release copy: ' + relative)
        # The launcher pins every nested Python CLI invocation to this release.
        launcher = ('#!' + str(Path(python_executable).absolute()) + '\n'
            'import os, sys\nfrom pathlib import Path\n'
            'r = Path(__file__).resolve().parent\n'
            'os.execv(' + repr(str(Path(python_executable).absolute())) + ', [' +
            repr(str(Path(python_executable).absolute())) +
            ', str(r / "files/scripts/mcp_business_worker.py"), "--cli", "--release-dir", str(r), "--", *sys.argv[1:]])\n')
        (staging / 'python').write_text(launcher)
        (staging / 'python').chmod(0o755)
        manifest = {'schema_version': 1, 'project_root': str(root), 'files': sources,
                    'tools': [], 'instructions': '', 'launcher_sha256': file_hash(staging / 'python')}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        described = subprocess.run([python_executable,
            str(staging / 'files/scripts/mcp_business_worker.py'), '--describe', '--release-dir', str(staging)],
            input='', text=True, capture_output=True, timeout=60, check=False)
        if described.returncode:
            raise ValueError('Business discovery failed: ' + described.stderr[-4000:])
        description = json.loads(described.stdout)
        manifest.update(tools=description['tools'], instructions=description['instructions'])
        names = [t['name'] for t in manifest['tools']]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate tool name')
        manifest['tools'] = sorted(manifest['tools'], key=lambda t: t['name'])
        # Recorded, not enforced at activation: it names what this release deliberately did
        # NOT publish, so a reviewer can see the boundary instead of inferring it.
        manifest['scope'] = scope_summary(root, scope, sources)
        manifest['release_id'] = canonical_hash(release_identity(manifest))
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        if code_files(root, scope=scope) != sources:
            raise ValueError('Project code changed during release discovery')
        destination = releases / manifest['release_id']
        if destination.exists():
            existing = validate_manifest(destination, root)
            if existing != manifest:
                raise ValueError('Existing immutable release differs')
            shutil.rmtree(staging)
        else:
            staging.rename(destination)
            for path in destination.rglob('*'):
                if path.is_file():
                    path.chmod(0o555 if path.name == 'python' else 0o444)
        if activate:
            return activate_release(root, manifest['release_id'], runtime)
        return dict(manifest, release_dir=str(destination), manifest_sha256=file_hash(destination / 'manifest.json'))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--activate', metavar='RELEASE_ID')
    args = parser.parse_args()
    result = (activate_release(args.project_root, args.activate, args.runtime_root) if args.activate else
              publish_release(args.project_root, args.runtime_root, activate=not args.prepare_only))
    print(json.dumps({key: result[key] for key in ('release_id', 'release_dir', 'manifest_sha256')}, indent=2))


if __name__ == '__main__':
    main()
