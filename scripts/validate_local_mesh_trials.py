"""Finite regression matrix for reviewed local-mesh operations on isolated copies."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from backend import Backend, ps_quote
from local_mesh_plan import prepare_local_mesh_plan, properties_for_plan
from restricted_local_mesh_trial import ROOT, start_trial, poll_trial
from mesh_quality import assess_mesh_quality

OUT = Path('test-evidence/local-mesh-validation-20261001')
BASE_PROPERTIES = Path('test-evidence/premesh-controls-20261001/local_mesh.properties')
ORIGINAL = '96c3169e9666acd04145731d95e3808e8a4b4f62b4182d9558d07fdf612d5584'

def wait(b, name, timeout=220):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        r=poll_trial(b,name,OUT)
        if r['state']!='PENDING_RESULT':
            print(name,r['state'],flush=True)
            time.sleep(2)
            return r
        time.sleep(10)
    raise RuntimeError('Timed out waiting for '+name+'; do not duplicate or terminate other jobs')

def run(b):
    # Existing bounded mesh trial must finish before the single-machine regression matrix.
    r=wait(b,'mesh-generate-perpart-20261001a',1000)
    if r['state']=='GENERATED_PENDING_QUALITY_GATE':
        csv=OUT/'mesh-generate-perpart-20261001a/mrf_volume_mesh_quality.csv'
        quality=assess_mesh_quality(csv,
            {'mesh_min_cell_quality':1e-5,'mesh_min_face_validity':1.0,'mesh_min_volume_change':0.01},
            ['Rotating Region','Static Region'])
        (OUT/'preinit-quality-gate.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
        print('Pre-initialization mesh quality passed:',quality['passed'],flush=True)
    else:
        print('Mesh generation did not pass; preserving diagnosis while validating independent configuration guards',flush=True)
    text=BASE_PROPERTIES.read_text()
    source=b.remote_path('library/'+ORIGINAL+'.sim')
    cases=[('mesh-missing-face-20261001b',text.replace('surface_count=8','surface_count=7'),source,ORIGINAL,'Surface inventory changed'),
           ('mesh-wrong-interface-20261001b',text.replace('surface.6.pair=rotor-stator','surface.6.pair=another-link'),source,ORIGINAL,'Invalid interface pair'),
           ('mesh-existing-op-20261001b',text,ROOT+r'\mesh-config-perpart-20261001a\local_mesh_configured.sim',
            json.loads((OUT/'mesh-config-perpart-20261001a-result.json').read_text())['output_sha256'],'Existing mesh operation')]
    negatives=[]
    for name,props,input_path,digest,reason in cases:
        path=OUT/(name+'.properties');path.write_text(props)
        start_trial(b,name,'configure',input_path,digest,path,OUT)
        r=wait(b,name)
        failure=(OUT/name/'local_mesh_failure.properties').read_text(encoding='utf-8-sig')
        if r['state']!='FAILED' or 'mutation_started=false' not in failure or reason not in failure:
            raise RuntimeError('Incorrect or unproven rejection: '+name)
        integrity=b.ps(f"""[pscustomobject]@{{input_sha256=(Get-FileHash -LiteralPath {ps_quote(ROOT+'\\'+name+'\\input.sim')} -Algorithm SHA256).Hash.ToLowerInvariant();output_present=(Test-Path -LiteralPath {ps_quote(ROOT+'\\'+name+'\\local_mesh_configured.sim')})}} | ConvertTo-Json""")
        if integrity['input_sha256']!=digest or integrity['output_present']:
            raise RuntimeError('Negative case changed source or generated output: '+name)
        negatives.append({'trial_id':name,'expected_reason':reason,'rejected_before_mutation':True,**integrity})
        (OUT/'negative-matrix.json').write_text(json.dumps(negatives,ensure_ascii=False,indent=2))
    name='mesh-rename-fixture-20261001a'
    start_trial(b,name,'rename',source,ORIGINAL,BASE_PROPERTIES,OUT)
    renamed=wait(b,name)
    if renamed['state']!='FIXTURE_RENAMED':raise RuntimeError('Rename fixture failed')
    mapping=dict(line.split('\t',1) for line in (OUT/name/'local_mesh_rename_map.tsv').read_text().splitlines())
    audit=json.loads((OUT/name/'automation_preflight.json').read_text(encoding='utf-8-sig'))
    original=json.loads(Path('test-evidence/restricted-worker-20261001/case-probe-20261001/automation_preflight.json').read_text(encoding='utf-8-sig'))
    after={x['qualifiedName']:x for x in audit['geometryParts']}
    for before in original['geometryParts']:
        part=after[mapping[before['qualifiedName']]]
        for key in ['isClosed','isManifold','triangleCount','boundingBoxRaw','partSurfaceCount']:
            if before[key]!=part[key]:raise RuntimeError('Rename changed measured geometry: '+key)
    controls=json.loads(Path('test-evidence/premesh-controls-20261001/openwater-controls.json').read_text())
    controls['fluid_parts']=[mapping[x] for x in controls['fluid_parts']]
    for section in ['surfaces','edge_controls']:
        for item in controls.get(section,[]):item['selector']=mapping[item['selector']]
    plan=prepare_local_mesh_plan(audit,controls);plan['source_sha256']=renamed['output_sha256']
    (OUT/'renamed-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    properties=OUT/'renamed-local-mesh.properties';properties.write_text(properties_for_plan(plan))
    name='mesh-renamed-config-20261001a'
    start_trial(b,name,'configure',ROOT+r'\mesh-rename-fixture-20261001a\local_mesh_renamed.sim',renamed['output_sha256'],properties,OUT)
    configured=wait(b,name)
    if configured['state']!='CONFIGURED_PENDING_READBACK':raise RuntimeError('Renamed configuration failed')
    name='mesh-renamed-readback-20261001a'
    start_trial(b,name,'readback',ROOT+r'\mesh-renamed-config-20261001a\local_mesh_configured.sim',configured['output_sha256'],properties,OUT)
    r=wait(b,name)
    if r['state']!='LOCAL_MESH_READBACK_SUCCEEDED':raise RuntimeError('Renamed readback failed')
    result={'negative_cases_verified':len(negatives),'renamed_geometry_unchanged':True,
            'renamed_configuration_readback_passed':True,'engineering_verified':False,
            'remaining':['Unit/scale/position variations','Actual prism coverage','Post-initialization interface quality','Solve and sensitivities','Production integration/restart tests']}
    (OUT/'regression-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Pass existing Backend config path; no credentials are printed')
    run(Backend(sys.argv[1]))
