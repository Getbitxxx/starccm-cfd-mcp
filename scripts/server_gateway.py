"""Stable stdio MCP transport. Business code runs in a fresh pinned process."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys

from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from mcp_runtime_release import (active_runtime, disk_hashes, file_hash, tool_hash,
                                 GATEWAY_FILES, require_release_review, diagnostic_text)


class Gateway:
    def __init__(self, project_root, runtime_root=None):
        self.root = Path(project_root).resolve()
        self.runtime_root = runtime_root
        self.started = active_runtime(self.root, self.runtime_root)
        self.advertised = {t['name']: tool_hash(t) for t in self.started['tools']}
        self.entry_hashes = {p: file_hash(Path(__file__).resolve().parents[1] / p) for p in GATEWAY_FILES}
        self.sessions = set()
        # Preserve the old synchronous gateway's per-connection ordering.
        # Fresh processes must not introduce concurrent submit/cancel actions.
        self.call_lock = asyncio.Lock()
        self.server = Server('starccm-cfd', version='gateway-1', instructions=self.started['instructions'])

        @self.server.list_tools()
        async def list_tools():
            selected = active_runtime(self.root, self.runtime_root)
            self.advertised = {t['name']: tool_hash(t) for t in selected['tools']}
            self.sessions.add(self.server.request_context.session)
            return [Tool.model_validate(t) for t in selected['tools']]

        @self.server.call_tool()
        async def call_tool(name, arguments):
            self.sessions.add(self.server.request_context.session)
            return await self.call(name, arguments)

    async def call(self, name, arguments):
        async with self.call_lock:
            try:
                return await self._call(name, arguments)
            except Exception as exc:
                raise ValueError(diagnostic_text(exc)) from exc

    async def _call(self, name, arguments):
        selected = active_runtime(self.root, self.runtime_root)
        contract = next((t for t in selected['tools'] if t['name'] == name), None)
        if contract is None:
            raise ValueError('Unknown published tool: ' + name)
        if self.advertised.get(name) != tool_hash(contract):
            raise ValueError('TOOL_CONTRACT_CHANGED: refresh tools/list before calling this tool')
        unknown = set(arguments) - set(contract['inputSchema'].get('properties', {}))
        if unknown:
            raise ValueError('Unexpected tool arguments: ' + ', '.join(sorted(unknown)))
        writable = not contract.get('annotations', {}).get('readOnlyHint', False)
        current_entry = {p: file_hash(Path(__file__).resolve().parents[1] / p) for p in GATEWAY_FILES}
        require_release_review(selected['release_dir'], selected)
        if any(selected['files'].get(p) != expected for p, expected in self.entry_hashes.items()):
            raise ValueError('GATEWAY_RECONNECT_REQUIRED: approved stable entry version differs')
        if writable and current_entry != self.entry_hashes:
            raise ValueError('GATEWAY_RECONNECT_REQUIRED: stable entry itself changed')
        folder = Path(selected['release_dir'])
        process = await asyncio.create_subprocess_exec(sys.executable,
            str(folder / 'files/scripts/mcp_business_worker.py'), '--release-dir', str(folder),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(self.root))
        try:
            output, errors = await process.communicate(json.dumps({'name': name, 'arguments': arguments}).encode())
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode:
            raise ValueError('Business process failed: ' + errors.decode(errors='replace')[-4000:])
        payload = json.loads(output)
        result = CallToolResult.model_validate(payload['result'])
        actual = payload['runtime']
        actual.update(gateway_pid=os.getpid(), gateway_version='gateway-1',
                      gateway_loaded_sha256=self.entry_hashes)
        if actual['release_id'] != selected['release_id']:
            raise ValueError('Selected and actually executed release differ')
        result.meta = dict(result.meta or {}, starccm_runtime=actual)
        if name == 'connection_health':
            data = result.structuredContent
            if data is None and len(result.content) == 1 and result.content[0].type == 'text':
                data = json.loads(result.content[0].text)
            if not isinstance(data, dict):
                raise ValueError('Health tool did not return a JSON object')
            real_disk = disk_hashes(self.root, selected['files'])
            data['business_runtime'] = dict(actual,
                release_sha256=selected['manifest_sha256'], project_root=str(self.root),
                published_sha256=selected['files'], disk_sha256=real_disk,
                all_files_match_disk=real_disk == selected['files'],
                gateway_matches_disk=current_entry == self.entry_hashes,
                execution_model='FRESH_PROCESS_PER_CALL')
            result.content = [TextContent(type='text', text=json.dumps(data, ensure_ascii=False))]
        return result

    async def notifications(self):
        old_contract = tool_hash(self.started['tools'])
        while True:
            await asyncio.sleep(2)
            try:
                current = active_runtime(self.root, self.runtime_root)
                new_contract = tool_hash(current['tools'])
                if new_contract != old_contract:
                    for session in tuple(self.sessions):
                        try:
                            await session.send_tool_list_changed()
                        except Exception:
                            self.sessions.discard(session)
                    old_contract = new_contract
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                print('MCP release notification check failed: ' + str(exc), file=sys.stderr)

    async def run(self):
        watcher = asyncio.create_task(self.notifications())
        try:
            async with stdio_server() as (reader, writer):
                await self.server.run(reader, writer,
                    self.server.create_initialization_options(NotificationOptions(tools_changed=True)))
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-root', type=Path)
    args = parser.parse_args()
    asyncio.run(Gateway(args.project_root, args.runtime_root).run())


if __name__ == '__main__':
    main()
