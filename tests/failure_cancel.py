"""Exercise true failed-macro detection and cancellation through the MCP protocol."""
import asyncio,json,os,sys,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    source, prefix, destination=sys.argv[1:]
    server=Path(__file__).resolve().parents[1]/'scripts/server.py'
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(server)],env=dict(os.environ))) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            async def call(name,args):
                result=await s.call_tool(name,args)
                if result.isError:raise RuntimeError(str(result.content))
                return result.structuredContent or json.loads(result.content[0].text)
            async def wait(job_id,terminal=True):
                started=time.monotonic()
                while time.monotonic()-started<180:
                    job=await call('get_job',{'job_id':job_id})
                    state=job['status']['state']
                    if state in {'SUCCEEDED','FAILED','CANCELLED','TIMED_OUT','REJECTED_BUSY'} or (not terminal and state=='RUNNING'):return job
                    await asyncio.sleep(2)
                raise TimeoutError(job_id)
            case=await call('upload_case',{'local_path':source})
            invalid=prefix+'-missing-preconditions'
            # Original case lacks CFD_Radius: QA macro fails but STAR can still return exit code zero.
            await call('submit_job',{'stage':'qa','case_ref':case['case_ref'],'request_id':invalid,'timeout_seconds':120})
            failure=await wait(invalid)
            assert failure['status']['state']=='FAILED',failure
            assert failure['status']['error'],failure
            print('FAILED_MACRO_DETECTED',flush=True)
            # A conflicting retry must never replace a submitted request.
            conflict=await s.call_tool('submit_job',{'stage':'inspect','case_ref':case['case_ref'],'request_id':invalid,'timeout_seconds':120})
            assert conflict.isError,'Conflicting idempotency key was accepted'
            cancelled=prefix+'-cancel'
            await call('submit_job',{'stage':'inspect','case_ref':case['case_ref'],'request_id':cancelled,'timeout_seconds':120})
            running=await wait(cancelled,False)
            assert running['status']['state']=='RUNNING',running
            await call('cancel_job',{'job_id':cancelled})
            cancellation=await wait(cancelled)
            assert cancellation['status']['state']=='CANCELLED',cancellation
            print('CANCELLATION_PASSED',flush=True)
            result={'failed_macro_detection':failure,'conflicting_retry_rejected':True,'cancellation':cancellation}
            Path(destination).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
if __name__=='__main__':asyncio.run(main())
