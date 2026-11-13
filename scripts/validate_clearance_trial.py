"""One final evidence-based mesh repair trial; no initialization or solve.
Does not launch a replacement configuration or repeat generation on timeout.
"""
from __future__ import annotations
import json,sys,time
from pathlib import Path
from backend import Backend
from restricted_local_mesh_trial import ROOT,start_trial,poll_trial
from mesh_quality import assess_mesh_quality

OUT=Path('test-evidence/local-mesh-validation-20261001')

def wait(b,name,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        r=poll_trial(b,name,OUT)
        if r['state']!='PENDING_RESULT':
            print(name,r['state'],flush=True)
            time.sleep(3)
            return r
        time.sleep(10)
    raise RuntimeError('Wait budget exceeded; inspect existing trial, do not resubmit')

def run(b):
    configured=wait(b,'mesh-config-clearance-20261001a',220)
    if configured['state']!='CONFIGURED_PENDING_READBACK':raise RuntimeError('Configuration rejected')
    name='mesh-generate-clearance-20261001a'
    start_trial(b,name,'generate',ROOT+r'\mesh-config-clearance-20261001a\local_mesh_configured.sim',configured['output_sha256'],OUT/'clearance-local-mesh.properties',OUT)
    r=wait(b,name,1000)
    result={'repair_count':2,'automatic_repair_budget_exhausted':True,'engineering_verified':False,'initialized':False,'solved':False,'trial':r}
    if r['state']=='GENERATED_PENDING_QUALITY_GATE':
        result['preinit_quality']=assess_mesh_quality(OUT/name/'mrf_volume_mesh_quality.csv',
            {'mesh_min_cell_quality':1e-5,'mesh_min_face_validity':1.,'mesh_min_volume_change':.01},
            ['Rotating Region','Static Region'])
        (OUT/'clearance-quality-gate.json').write_text(json.dumps(result['preinit_quality'],indent=2))
    (OUT/'clearance-repair-summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':run(Backend(sys.argv[1]))
