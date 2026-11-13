"""Real MCP stdio client for one R22 trial; no direct Backend/controller calls."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'test-evidence/original-geometry/r22'
RUN_ID = 'original-r22-20261005a'


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['discover', 'submit', 'poll', 'fetch'])
    args = parser.parse_args()
    params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'scripts/server.py')], env=dict(os.environ))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            if args.action == 'discover':
                names = [item.name for item in (await session.list_tools()).tools]
                required = ['list_registered_workflows', 'submit_registered_workflow',
                            'get_registered_workflow_run', 'fetch_registered_workflow_results']
                assert all(name in names for name in required)
                name, arguments = 'list_registered_workflows', {}
            elif args.action == 'submit':
                name, arguments = 'submit_registered_workflow', {
                    'workflow_id': 'original-geometry-mrf-mcp-2502',
                    'source_sim': str(ROOT / 'cases/original-geometry/r17/source_input.sim'),
                    'case_spec_path': str(ROOT / 'cases/original-geometry/r20/case_spec.json'),
                    'request_id': RUN_ID}
            elif args.action == 'poll':
                name, arguments = 'get_registered_workflow_run', {'run_id': RUN_ID}
            else:
                name, arguments = 'fetch_registered_workflow_results', {'run_id': RUN_ID}
            started = time.monotonic()
            result = await session.call_tool(name, arguments)
            record = {'transport': 'MCP stdio JSON-RPC ClientSession.call_tool',
                'server': str(ROOT / 'scripts/server.py'),
                'protocol_version': initialized.protocolVersion, 'tool': name,
                'arguments': arguments, 'utc': datetime.now(timezone.utc).isoformat(),
                'call_wall_seconds': round(time.monotonic() - started, 3),
                'is_error': bool(result.isError), 'response': result.model_dump(mode='json')}
            if args.action == 'discover':
                record['tool_names'] = names
            directory = EVIDENCE / 'mcp-calls'
            directory.mkdir(exist_ok=True)
            sequence = len(list(directory.glob('*.json'))) + 1
            target = directory / ('%03d-%s.json' % (sequence, args.action))
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'record': str(target), 'tool': name,
                'wall_seconds': record['call_wall_seconds'], 'is_error': record['is_error'],
                'result': result.structuredContent or result.content[0].text}, ensure_ascii=False))
            if result.isError:
                raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
