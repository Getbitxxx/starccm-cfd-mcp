"""Local stdio MCP isolation tests; no STAR/Windows or desktop reconnect proof.

Run with the MCP SDK Python 3.12 environment. All business modules are temporary
fixtures; only the real gateway/release/worker implementation is exercised.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
EVIDENCE = ROOT / 'test-evidence' / 'mcp-runtime-refresh-20261009'
PYTHON = '/Users/example/Documents/Codex/2026-09-20/new-chat/work/mcp-venv/bin/python'

SERVER = '''
from pathlib import Path
import hashlib, json, os, subprocess, sys, time
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
import helper
ROOT = Path(__file__).resolve().parents[1]
mcp = FastMCP('isolated-runtime-fixture')
_HEALTH_FILES = ['scripts/server_v2.py', 'scripts/helper.py', 'scripts/backend.py',
                 'scripts/registered_workflow_mcp_v2.py', 'scripts/run_sensitivity_study.py']
_LOADED_HASHES = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in _HEALTH_FILES}
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def connection_health() -> dict:
    return {'fixture': True, 'version': helper.VERSION, 'desktop_runtime':
            {'version': 'legacy-fixture', 'loaded_sha256': _LOADED_HASHES,
             'disk_sha256': _LOADED_HASHES, 'loaded_matches_disk': True}}
@mcp.tool()
def probe(label: str = 'fixture-default', delay: float = 0, marker_path: str | None = None) -> dict:
    if label == 'raise-busy':
        raise ValueError('REJECTED_BUSY: fixture slot occupied')
    if marker_path:
        Path(marker_path).write_text('entered-' + helper.VERSION)
    time.sleep(delay)
    report = subprocess.run([sys.executable, str(ROOT / 'cases/demo/report.py')],
                            check=True, capture_output=True, text=True)
    data = ROOT / 'work/fixture-data/result.txt'
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(helper.VERSION)
    if marker_path:
        Path(marker_path + '.finished').write_text('finished-' + helper.VERSION)
    return {'fixture': True, 'helper_version': helper.VERSION, 'label': label,
            'child_pid': os.getpid(), 'gateway_pid': os.getppid(),
            'code_root': str(ROOT), 'script': json.loads(report.stdout),
            'helper_bytes_sha256': hashlib.sha256((ROOT / 'scripts/helper.py').read_bytes()).hexdigest(),
            'helper_alias_sha256': hashlib.sha256((ROOT / 'cases/../scripts/helper.py').read_bytes()).hexdigest(),
            'report_bytes_sha256': hashlib.sha256((ROOT / 'cases/demo/report.py').read_bytes()).hexdigest(),
            'data_path': str(data)}
if __name__ == '__main__':
    mcp.run(transport='stdio')
'''
REPORT = '''
import json, os
from pathlib import Path
import sibling
print(json.dumps({'report_version': VERSION, 'sibling_version': sibling.VERSION, 'script_path': str(Path(__file__).resolve()),
                  'script_pid': os.getpid(), 'parent_pid': os.getppid()}))
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RuntimeRefresh(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mcp-runtime-isolation-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'fake-project'
        self.runtime = self.project / 'work/mcp-runtime'
        (self.project / 'scripts').mkdir(parents=True)
        (self.project / 'cases/demo').mkdir(parents=True)
        (self.project / 'scripts/server_v2.py').write_text(textwrap.dedent(SERVER))
        for name in ('backend.py', 'registered_workflow_mcp_v2.py', 'run_sensitivity_study.py'):
            (self.project / 'scripts' / name).write_text('# isolated fixture module\n')
        self.write_version('A')
        for name in ('mcp_business_worker.py', 'mcp_runtime_release.py', 'server_gateway.py'):
            shutil.copy2(SCRIPTS / name, self.project / 'scripts' / name)
        sys.path.insert(0, str(SCRIPTS))
        self.release_api = importlib.import_module('mcp_runtime_release')
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.events = []
        # Real project business modules must remain byte-identical throughout fixture tests.
        self.original_business = {str(p): sha(p) for p in (
            SCRIPTS / 'server_v2.py', SCRIPTS / 'backend.py',
            SCRIPTS / 'registered_workflow_mcp_v2.py', SCRIPTS / 'run_sensitivity_study.py')}

    def tearDown(self):
        for path, digest in self.original_business.items():
            self.assertEqual(sha(path), digest, 'Real business module mutated by isolation test')
        (EVIDENCE / (self._testMethodName + '.json')).write_text(json.dumps({
            'scope': 'isolated local MCP stdio; not real desktop reconnect acceptance',
            'STAR_started': False, 'real_business_unchanged': True,
            'events': self.events,
        }, ensure_ascii=False, indent=2) + '\n')

    def write_version(self, version):
        (self.project / 'scripts/helper.py').write_text(f'VERSION = {version!r}\n')
        (self.project / 'cases/demo/sibling.py').write_text(f'VERSION = {version!r}\n')
        (self.project / 'cases/demo/report.py').write_text(
            f'VERSION = {version!r}\n' + textwrap.dedent(REPORT))

    def publish(self):
        result = self.release_api.publish_release(
            self.project, runtime_root=self.runtime, python_executable=PYTHON)
        active = self.release_api.active_runtime(self.project, runtime_root=self.runtime)
        self.assertEqual(active['release_id'], result['release_id'])
        self.assertEqual(active['files'], result['files'])
        self.assertTrue((self.runtime / 'current.json').is_file())
        self.write_review(result)
        self.events.append({'publish': result})
        return result

    def write_review(self, release):
        path = Path(release['release_dir']) / 'release-review.json'
        path.write_text(json.dumps({'synthetic_test_only': True,
            'approved': True, 'independent': True, 'release_id': release['release_id'],
            'manifest_sha256': release['manifest_sha256'], 'runtime_hashes': release['files']}))
        return path

    @asynccontextmanager
    async def connected(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=PYTHON, args=[
            str(self.project / 'scripts/server_gateway.py'), '--project-root', str(self.project),
            '--runtime-root', str(self.runtime)], cwd=str(self.base),
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        with (EVIDENCE / (self._testMethodName + '-gateway-stderr.log')).open('w') as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as client:
                    await asyncio.wait_for(client.initialize(), 20)
                    tools = await client.list_tools()
                    self.assertIn('probe', [tool.name for tool in tools.tools])
                    yield client

    async def call(self, client, arguments=None):
        result = await asyncio.wait_for(client.call_tool('probe', arguments or {}), 20)
        self.assertFalse(result.isError, str(result))
        data = result.structuredContent
        if data is None:
            blocks = [x.text for x in result.content if getattr(x, 'type', None) == 'text']
            data = json.loads(''.join(blocks))
        # Plain business contract; no reliance on logs for result correctness.
        self.assertIsInstance(data, dict)
        if not data.get('fixture') and isinstance(data.get('result'), dict):
            data = data['result']
        self.assertTrue(data.get('fixture'), data)
        self.assertIsInstance(result.meta, dict)
        runtime = result.meta['starccm_runtime']
        self.assertEqual(data['child_pid'], runtime['business_pid'])
        self.assertEqual(data['gateway_pid'], runtime['gateway_pid'])
        self.events.append({'call': data, 'runtime': runtime})
        data['_test_runtime'] = runtime
        return data

    async def rejected(self, client, name='probe', arguments=None):
        from mcp.shared.exceptions import McpError
        try:
            result = await asyncio.wait_for(client.call_tool(name, arguments or {}), 20)
        except McpError as exc:
            self.events.append({'rejected_protocol': str(exc)})
        else:
            self.assertTrue(result.isError, 'Invalid call unexpectedly accepted: ' + str(result))
            self.events.append({'rejected_tool': str(result)})

    async def test_business_and_gateway_errors_have_user_action(self):
        self.publish()
        async with self.connected() as client:
            result=await client.call_tool('probe',{'label':'raise-busy'})
            self.assertTrue(result.isError)
            data=result.structuredContent
            self.assertEqual('REJECTED_BUSY',data['error_code'])
            self.assertTrue(data['user_action']);self.assertFalse(data['automatic_retry'])
            self.assertIn(data['user_action'],result.content[0].text)
            # An entry byte change is detected before a business process is started.
            path=self.project/'scripts/server_gateway.py'
            path.write_text(path.read_text()+'\n# changed fixture entry\n')
            result=await client.call_tool('probe',{})
            self.assertTrue(result.isError)
            self.assertIn('GATEWAY_RECONNECT_REQUIRED',str(result))
            self.assertIn('处理方法',str(result))

    async def test_same_gateway_refreshes_helper_and_subscript_in_new_child(self):
        first = self.publish()
        async with self.connected() as client:
            a = await self.call(client)
            self.write_version('B')
            second = self.publish()
            b = await self.call(client)
            self.assertNotEqual(first['release_id'], second['release_id'])
            self.assertEqual(a['helper_version'], 'A')
            self.assertEqual(b['helper_version'], 'B')
            self.assertEqual(a['script']['report_version'], 'A')
            self.assertEqual(b['script']['report_version'], 'B')
            self.assertEqual(a['script']['sibling_version'], 'A')
            self.assertEqual(b['script']['sibling_version'], 'B')
            self.assertEqual(a['label'], 'fixture-default')
            self.assertEqual(b['label'], 'fixture-default')
            self.assertNotEqual(a['child_pid'], b['child_pid'])
            self.assertEqual(a['gateway_pid'], b['gateway_pid'])
            self.assertEqual(a['script']['parent_pid'], a['child_pid'])
            self.assertEqual(b['script']['parent_pid'], b['child_pid'])
            self.assertEqual(Path(a['code_root']), self.project)
            self.assertEqual(Path(b['code_root']), self.project)
            self.assertEqual(a['helper_bytes_sha256'], first['files']['scripts/helper.py'])
            self.assertEqual(b['helper_bytes_sha256'], second['files']['scripts/helper.py'])
            self.assertEqual(a['helper_alias_sha256'], first['files']['scripts/helper.py'])
            self.assertEqual(b['helper_alias_sha256'], second['files']['scripts/helper.py'])
            self.assertEqual(a['report_bytes_sha256'], first['files']['cases/demo/report.py'])
            self.assertEqual(b['report_bytes_sha256'], second['files']['cases/demo/report.py'])
            self.assertEqual(a['_test_runtime']['release_id'], first['release_id'])
            self.assertEqual(b['_test_runtime']['release_id'], second['release_id'])
            loaded = b['_test_runtime']['loaded_sha256']
            self.assertIn('scripts/server_v2.py', loaded)
            self.assertIn('scripts/helper.py', loaded)
            self.assertTrue(all(second['files'][key] == value for key, value in loaded.items()))
            self.assertEqual(Path(b['data_path']), self.project / 'work/fixture-data/result.txt')
            self.assertEqual(Path(b['data_path']).read_text(), 'B')
            self.assertEqual(Path(a['script']['script_path']).parents[2], Path(a['code_root']))
            self.assertEqual(Path(b['script']['script_path']).parents[2], Path(b['code_root']))

    async def test_inflight_pins_A_then_next_call_uses_B(self):
        first = self.publish()
        marker = self.base / 'entered.txt'
        async with self.connected() as client:
            pending = asyncio.create_task(self.call(client, {'delay': 8., 'marker_path': str(marker)}))
            for _ in range(300):
                if marker.is_file():
                    break
                await asyncio.sleep(.02)
            self.assertTrue(marker.is_file(), 'A did not enter business child')
            self.assertEqual(marker.read_text(), 'entered-A')
            self.write_version('B')
            self.publish()
            self.assertFalse(Path(str(marker) + '.finished').exists(), 'A finished before B publication; no in-flight proof')
            a = await pending
            b = await self.call(client)
            self.assertEqual(a['helper_version'], 'A')
            self.assertEqual(a['script']['report_version'], 'A')
            self.assertEqual(a['helper_alias_sha256'], first['files']['scripts/helper.py'])
            self.assertEqual(b['helper_version'], 'B')
            self.assertEqual(b['script']['report_version'], 'B')
            self.assertEqual(a['script']['sibling_version'], 'A')
            self.assertEqual(b['script']['sibling_version'], 'B')
            self.assertEqual(a['gateway_pid'], b['gateway_pid'])

    async def test_extra_parameter_and_unknown_tool_rejected_connection_survives(self):
        self.publish()
        async with self.connected() as client:
            before = await self.call(client)
            await self.rejected(client, name='arbitrary_unknown_tool')
            marker = self.base / 'invalid-entered.txt'
            await self.rejected(client, arguments={'unexpected_parameter': 'reject', 'marker_path': str(marker)})
            self.assertFalse(marker.exists(), 'Invalid arguments entered business function')
            after = await self.call(client)
            self.assertEqual(before['gateway_pid'], after['gateway_pid'])

    async def test_snapshot_tamper_rejected(self):
        release = self.publish()
        async with self.connected() as client:
            await self.call(client)
            snapshot = Path(release['release_dir'])
            self.assertNotEqual(snapshot, self.project)
            target = snapshot / 'files/scripts/helper.py'
            target.chmod(target.stat().st_mode | 0o200)
            target.write_text("VERSION = 'tampered'\n")
            await self.rejected(client)

    async def test_generic_dict_health_text_json_runtime_overlay(self):
        release = self.publish()
        async with self.connected() as client:
            result = await client.call_tool('connection_health', {})
            self.assertFalse(result.isError)
            self.assertIsNone(result.structuredContent, 'Generic dict contract unexpectedly wrapped')
            self.assertEqual(len(result.content), 1)
            data = json.loads(result.content[0].text)
            self.assertEqual(data['version'], 'A')
            self.assertEqual(data['desktop_runtime']['version'], 'legacy-fixture')
            expected = {name: release['files'][name] for name in (
                'scripts/server_v2.py', 'scripts/helper.py', 'scripts/backend.py',
                'scripts/registered_workflow_mcp_v2.py', 'scripts/run_sensitivity_study.py')}
            self.assertEqual(data['desktop_runtime']['loaded_sha256'], expected)
            self.assertEqual(data['desktop_runtime']['disk_sha256'], expected)
            self.assertTrue(data['desktop_runtime']['loaded_matches_disk'])
            runtime = data['business_runtime']
            self.assertEqual(runtime['release_id'], release['release_id'])
            self.assertEqual(runtime['published_sha256'], release['files'])
            self.assertTrue(runtime['all_files_match_disk'])
            self.assertEqual(runtime['execution_model'], 'FRESH_PROCESS_PER_CALL')
            self.assertEqual(runtime['business_pid'], result.meta['starccm_runtime']['business_pid'])
            self.assertEqual(runtime['gateway_pid'], result.meta['starccm_runtime']['gateway_pid'])
            self.events.append({'health_text_json': data, 'meta': result.meta})

    async def test_same_connection_serializes_business_entry(self):
        self.publish()
        first = self.base / 'first-entered.txt'
        second = self.base / 'second-entered.txt'
        async with self.connected() as client:
            one = asyncio.create_task(self.call(client, {'delay': .4, 'marker_path': str(first)}))
            for _ in range(300):
                if first.exists():
                    break
                await asyncio.sleep(.01)
            self.assertTrue(first.exists(), 'First business call did not enter')
            two = asyncio.create_task(self.call(client, {'marker_path': str(second)}))
            while not one.done():
                if second.exists():
                    self.assertTrue(Path(str(first) + '.finished').exists(),
                                    'Second call entered before first completed')
                await asyncio.sleep(.01)
            a, b = await asyncio.gather(one, two)
            self.assertTrue(second.exists())
            self.assertTrue(Path(str(first) + '.finished').exists())
            self.assertEqual(a['gateway_pid'], b['gateway_pid'])
            self.assertNotEqual(a['child_pid'], b['child_pid'])
            self.assertGreaterEqual(second.stat().st_mtime_ns,
                                    Path(str(first) + '.finished').stat().st_mtime_ns)

    async def test_missing_and_tampered_release_review_rejected(self):
        release = self.publish()
        async with self.connected() as client:
            await self.call(client)
            review = Path(release['release_dir']) / 'release-review.json'
            review.unlink()
            await self.rejected(client)
            self.write_review(release)
            data = json.loads(review.read_text())
            data['runtime_hashes']['scripts/helper.py'] = '0' * 64
            review.write_text(json.dumps(data))
            await self.rejected(client)
            self.write_review(release)
            await self.call(client)

    async def test_unpublished_workspace_change_rejects_mutating_probe(self):
        self.publish()
        async with self.connected() as client:
            await self.call(client)
            self.write_version('unpublished')
            marker = self.base / 'unpublished-entered.txt'
            await self.rejected(client, arguments={'marker_path': str(marker)})
            self.assertFalse(marker.exists(), 'Unpublished source entered mutating business')

    async def test_new_schema_rejects_old_session_contract(self):
        self.publish()
        async with self.connected() as client:
            a = await self.call(client)
            original = (self.project / 'scripts/server_v2.py').read_text()
            (self.project / 'scripts/server_v2.py').write_text(
                original.replace("label: str = 'fixture-default'", "label: str = 'changed-default'"))
            self.write_version('B')
            self.publish()
            await self.rejected(client)
            # Explicit tools/list acknowledges the new contract without replacing gateway.
            changed = await client.list_tools()
            self.assertIn('probe', [tool.name for tool in changed.tools])
            updated = await self.call(client)
            self.assertEqual(updated['label'], 'changed-default')
            self.assertEqual(updated['gateway_pid'], a['gateway_pid'])
        # A fresh MCP session also advertises the new contract and is usable.
        async with self.connected() as refreshed:
            b = await self.call(refreshed)
            self.assertEqual(b['label'], 'changed-default')
            self.assertEqual(b['helper_version'], 'B')
            self.assertNotEqual(a['gateway_pid'], b['gateway_pid'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
