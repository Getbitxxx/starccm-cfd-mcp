"""Real MCP/SSH integration check. Runs only isolated copies and bounded stages."""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',required=True)
    parser.add_argument('--prefix',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--stages',default='inspect,prepare,smoke,qa')
    args=parser.parse_args()
    server=Path(__file__).resolve().parents[1]/'scripts/server.py'
    params=StdioServerParameters(command=sys.executable,args=[str(server)],env=dict(os.environ))
    evidence={}
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            async def call(name,arguments):
                result=await session.call_tool(name,arguments)
                if result.isError:
                    raise RuntimeError(str(result.content))
                if result.structuredContent is not None:
                    return result.structuredContent
                return json.loads(result.content[0].text)
            evidence['tools']=[t.name for t in (await session.list_tools()).tools]
            evidence['health']=await call('connection_health',{})
            print('HEALTH',json.dumps(evidence['health']),flush=True)
            case=await call('upload_case',{'local_path':args.case})
            evidence['upload']=case
            source=case['case_ref']
            evidence['jobs']={}
            for stage in args.stages.split(','):
                job_id=args.prefix+'-'+stage
                submission={'stage':stage,'case_ref':source,'request_id':job_id,'timeout_seconds':300}
                submitted=await call('submit_job',submission)
                retry=await call('submit_job',submission)
                assert retry['reused'], 'Retry did not reuse original job'
                print('SUBMITTED',stage,json.dumps(submitted),flush=True)
                started=time.monotonic()
                last=None
                while True:
                    job=await call('get_job',{'job_id':job_id})
                    state=job['status']['state']
                    if state!=last:
                        print('STATE',stage,state,flush=True);last=state
                    if state in {'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','REJECTED_BUSY'}:
                        break
                    if time.monotonic()-started>380:
                        await call('cancel_job',{'job_id':job_id})
                        raise TimeoutError('Job did not terminate in integration budget')
                    await asyncio.sleep(5)
                evidence['jobs'][stage]=job
                if state!='SUCCEEDED':
                    Path(args.output).write_text(json.dumps(evidence,indent=2,ensure_ascii=False))
                    raise RuntimeError(json.dumps(job,ensure_ascii=False))
                fetched=await call('fetch_results',{'job_id':job_id})
                evidence['jobs'][stage]['download']=fetched
                if stage=='prepare':source='jobs/'+job_id+'/baseline_prepared.sim'
                if stage=='smoke':source='jobs/'+job_id+'/baseline_after_smoke.sim'
                print('DONE',stage,flush=True)
    Path(args.output).write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
    print('MCP_INTEGRATION_PASSED',flush=True)

if __name__=='__main__':asyncio.run(main())
