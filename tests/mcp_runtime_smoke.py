"""Real read-only business smoke via an independent MCP client, never STAR.

This does not certify that the Codex desktop connector has reconnected.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id')
    parser.add_argument('--output', type=Path, default=ROOT / 'test-evidence/mcp-runtime-refresh-20261009/real-business-independent-client.json')
    args = parser.parse_args()
    cfg = tomllib.loads((Path.home() / '.codex/config.toml').read_text())['mcp_servers']['starccm-cfd']
    params = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / 'scripts/server_gateway.py')], cwd=str(ROOT),
        env={**os.environ, **cfg.get('env', {})})
    record = {'scope': 'independent real MCP stdio; NOT desktop acceptance',
              'STAR_started': False, 'records': [], 'passed': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.with_suffix('.stderr.log').open('w') as errors:
            async with stdio_client(params, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    record['tool_names'] = [t.name for t in tools.tools]
                    health = []
                    calls = [('connection_health', {}), ('connection_health', {}), ('list_registered_workflows', {})]
                    if args.run_id:
                        calls.append(('get_registered_workflow_run', {'run_id': args.run_id}))
                    for name, arguments in calls:
                        result = await client.call_tool(name, arguments)
                        record['records'].append({'tool': name, 'arguments': arguments,
                            'result': result.model_dump(mode='json', by_alias=True, exclude_none=True)})
                        assert not result.isError, (name, result)
                        if name == 'connection_health':
                            data = result.structuredContent or json.loads(result.content[0].text)
                            runtime = data['business_runtime']
                            assert runtime['all_files_match_disk'] and runtime['gateway_matches_disk']
                            assert data['desktop_runtime']['loaded_matches_disk']
                            assert runtime['release_id'] == result.meta['starccm_runtime']['release_id']
                            health.append(runtime)
                    assert health[0]['gateway_pid'] == health[1]['gateway_pid']
                    assert health[0]['business_pid'] != health[1]['business_pid']
                    assert health[0]['release_id'] == health[1]['release_id']
                    record['passed'] = True
                    print(json.dumps({'passed': True, 'tools': len(tools.tools),
                        'gateway_pid': health[0]['gateway_pid'],
                        'business_pids': [h['business_pid'] for h in health],
                        'release_id': health[0]['release_id'], 'historical_query': bool(args.run_id),
                        'desktop_acceptance': False, 'STAR_started': False}, indent=2))
    finally:
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    asyncio.run(main())
