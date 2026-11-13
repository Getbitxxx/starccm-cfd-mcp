"""One-call business process; frozen code uses the original business data paths."""
from __future__ import annotations

import argparse
import asyncio
import builtins
from contextlib import redirect_stdout
import importlib.abc
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

from mcp_runtime_release import validate_manifest, disk_hashes, file_hash, require_release_review, error_diagnostic, diagnostic_text


class SnapshotCode(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Read only reviewed snapshot code, with original __file__ for data paths.

    Source hashing/copying uses the same frozen bytes as import and nested CLIs.
    Data, approval records and artifacts remain ordinary project filesystem I/O.
    """
    def __init__(self, release_dir, manifest):
        self.release = Path(release_dir).resolve()
        self.root = Path(manifest['project_root'])
        self.files = manifest['files']
        self.real_open = builtins.open
        self.real_io_open = io.open
        self.loaded = {p: self.files[p] for p in
                       ('scripts/mcp_business_worker.py', 'scripts/mcp_runtime_release.py')}

    def relative(self, path):
        if isinstance(path, int):
            return None
        try:
            absolute = Path(os.fsdecode(path)).resolve()
            return str(absolute.relative_to(self.root))
        except (TypeError, ValueError):
            return None

    def open(self, base_open, path, mode='r', *args, **kwargs):
        relative = self.relative(path)
        if relative in self.files:
            if any(flag in mode for flag in 'wax+'):
                raise PermissionError('Published Python code is immutable: ' + relative)
            frozen = self.release / 'files' / relative
            if file_hash(frozen) != self.files[relative]:
                raise ValueError('Frozen code changed: ' + relative)
            path = frozen
        return base_open(path, mode, *args, **kwargs)

    def install(self):
        builtins.open = lambda path, mode='r', *a, **kw: self.open(self.real_open, path, mode, *a, **kw)
        io.open = lambda path, mode='r', *a, **kw: self.open(self.real_io_open, path, mode, *a, **kw)
        sys.meta_path.insert(0, self)
        sys.path.insert(0, str(self.root / 'scripts'))
        sys.executable = str(self.release / 'python')

    def find_spec(self, fullname, path=None, target=None):
        suffix = fullname.rsplit('.', 1)[-1] if path is not None else fullname.replace('.', '/')
        names = []
        # Respect every project import search directory, including a cases/ CLI
        # sibling. Returning None there would let PathFinder execute live code.
        for entry in (path if path is not None else sys.path):
            directory = Path(entry or os.getcwd()).resolve()
            if directory.is_relative_to(self.root):
                for candidate in (directory / (suffix + '.py'), directory / suffix / '__init__.py'):
                    if candidate.resolve().is_relative_to(self.root):
                        names.append(str(candidate.resolve().relative_to(self.root)))
        for relative in names:
            if relative in self.files:
                return importlib.util.spec_from_file_location(fullname, str(self.root / relative),
                    loader=self, submodule_search_locations=[str((self.root / relative).parent)]
                    if relative.endswith('/__init__.py') else None)
            if (self.root / relative).is_file():
                raise ImportError('Project Python module is not published: ' + relative)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = str(Path(module.__file__).relative_to(self.root))
        code = self.read_code(relative)
        exec(compile(code, str(self.root / relative), 'exec'), module.__dict__)

    def read_code(self, relative):
        if relative not in self.files:
            raise ValueError('Python entry is outside published code: ' + relative)
        frozen = self.release / 'files' / relative
        if file_hash(frozen) != self.files[relative]:
            raise ValueError('Frozen code changed: ' + relative)
        with self.real_open(frozen, 'rb') as stream:
            code = stream.read()
        self.loaded[relative] = self.files[relative]
        return code

    def cli(self, arguments):
        if not arguments or arguments[0].startswith('-'):
            raise ValueError('Release Python launcher requires a published script path')
        script = Path(arguments[0]).resolve()
        relative = str(script.relative_to(self.root))
        code = self.read_code(relative)
        sys.argv = [str(script), *arguments[1:]]
        sys.path.insert(0, str(script.parent))
        namespace = {'__name__': '__main__', '__file__': str(script), '__package__': None,
                     '__builtins__': builtins}
        exec(compile(code, str(script), 'exec'), namespace)


async def business_call(manifest, code, request):
    from mcp.types import CallToolResult, TextContent
    import server_v2
    name = request['name']
    tools = {item['name']: item for item in manifest['tools']}
    if name not in tools:
        raise ValueError('Tool is not in published contract: ' + name)
    unknown = set(request.get('arguments', {})) - set(tools[name]['inputSchema'].get('properties', {}))
    if unknown:
        raise ValueError('Unexpected tool arguments: ' + ', '.join(sorted(unknown)))
    # Any write/action uses a currently published source tree; frozen reads keep
    # historical code-byte identity even when newer source is being prepared.
    if not tools[name].get('annotations', {}).get('readOnlyHint', False):
        if disk_hashes(code.root, manifest['files']) != manifest['files']:
            raise ValueError('UNPUBLISHED_CODE: publish the changed business source before actions')
    try:
        value = await server_v2.mcp.call_tool(name, request.get('arguments', {}))
    except Exception as exc:
        return CallToolResult(content=[TextContent(type='text', text=diagnostic_text(exc))],
                              structuredContent=error_diagnostic(exc), isError=True)
    if isinstance(value, tuple) and len(value) == 2:
        result = CallToolResult(content=list(value[0]), structuredContent=value[1], isError=False)
    elif isinstance(value, dict):
        result = CallToolResult(content=[TextContent(type='text', text=json.dumps(value))],
                                structuredContent=value, isError=False)
    else:
        result = CallToolResult(content=list(value), isError=False)
    if name == 'connection_health':
        data = result.structuredContent
        if data is None and len(result.content) == 1 and result.content[0].type == 'text':
            data = json.loads(result.content[0].text)
        if not isinstance(data, dict):
            raise ValueError('Health tool did not return a JSON object')
        legacy = data.get('desktop_runtime', {})
        loaded = {p: manifest['files'][p] for p in legacy.get('loaded_sha256', {}) if p in manifest['files']}
        current = disk_hashes(code.root, loaded)
        legacy.update(loaded_sha256=loaded, disk_sha256=current, loaded_matches_disk=loaded == current)
        data['desktop_runtime'] = legacy
        result.content = [TextContent(type='text', text=json.dumps(data, ensure_ascii=False))]
    return result


async def main_async(args):
    project = json.loads((args.release_dir / 'manifest.json').read_text())['project_root']
    manifest = validate_manifest(args.release_dir, project, preview=args.describe)
    if not args.describe:
        require_release_review(args.release_dir, manifest)
    code = SnapshotCode(args.release_dir, manifest)
    code.install()
    if args.cli:
        remainder = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
        code.cli(remainder)
        return
    with redirect_stdout(sys.stderr):
        if args.describe:
            import server_v2
            tools = [tool.model_dump(mode='json', by_alias=True, exclude_none=True)
                     for tool in await server_v2.mcp.list_tools()]
            payload = {'tools': tools, 'instructions': server_v2.mcp.instructions or ''}
        else:
            request = json.loads(sys.stdin.read())
            try:
                result = await business_call(manifest, code, request)
            except Exception as exc:
                from mcp.types import CallToolResult, TextContent
                result = CallToolResult(content=[TextContent(type='text', text=diagnostic_text(exc))],
                                        structuredContent=error_diagnostic(exc), isError=True)
            payload = {'result': result.model_dump(mode='json', by_alias=True, exclude_none=True),
                       'runtime': {'release_id': manifest['release_id'], 'business_pid': os.getpid(),
                                   'loaded_sha256': code.loaded}}
    print(json.dumps(payload, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--describe', action='store_true')
    parser.add_argument('--cli', action='store_true')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == '__main__':
    main()
