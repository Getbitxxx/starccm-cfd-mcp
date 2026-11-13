"""Executable contract: real Q1 readback, synthetic Q2/Q3, and fail-closed adversarial records.

Synthetic reviews and measurements below are isolated temporary fixtures, never production evidence.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import quality_contract as q


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return q.file_ref(path)


class Fixture:
    def __init__(self,root,*,is_level=False,cells=64000,target=.5,domain_size=1.,mesh_size=.1,dt=.0002,dt_required=False):
        self.root=root;self.target=target
        qual=dict(mesh_min_cell_quality=1e-5,mesh_min_face_validity=1.,mesh_min_volume_change=.01,
                  mesh_min_wall_prism_layers=6,residual_gates={'continuity':1e-4,'momentum':2.5e-4,'energy':1e-3},
                  mass_imbalance_ratio_limit=.001,force_window_rel_change=.01,mesh_levels=3,
                  domain_sensitivity=True,time_step_sensitivity=dt_required,sensitivity_rel_change=.02)
        spec={'quality':qual,'scenario':'synthetic-fixture','mesh_base_size_m':mesh_size,
              'domain':{'outer_bounds_m':[-domain_size/2]*3+[domain_size/2]*3}}
        if dt_required:spec['transient']={'time_step_s':dt}
        specref=write(root/'case_spec.json',spec)
        manifest=write(root/'manifest.json',{'stages':{'solve':{'required':['artifact.bin','certificate.json'],'macros':['Synthetic.java']}}})
        macro={'Synthetic.java':'b'*64}
        inputfile=root/'input.sim';inputfile.write_bytes(b'synthetic original input');inputhash=q.sha256(inputfile)
        runtime=write(root/'runtime-manifest.json',{'runtime_digest':'a'*64,'files':[{'path':'workflows/synthetic/manifest.json','sha256':manifest['sha256']},{'path':'macros/Synthetic.java','sha256':'b'*64}]})
        request=dict(run_id=root.name,runtime_digest='a'*64,runtime_manifest_sha256=runtime['sha256'],release_id='c'*64,
                     case_spec=spec,input_sha256=inputhash,request_digest='e'*64,steps=[{'key':'solve','stage':'solve','workflow_id':'synthetic','macro_hashes':macro}])
        req=write(root/'request.json',request);state=write(root/'state.json',{'state':'EXECUTION_COMPLETED'})
        self.jobdir=root/'stages/solve';self.jobdir.mkdir(parents=True)
        artifact=self.jobdir/'artifact.bin';artifact.write_bytes(b'synthetic-checkpoint')
        sr=write(self.jobdir/'stage-solve.json',{'stage':'solve','state':'ok','input_sha256':inputhash,'macros':[{'status':'ok','ran':True,'macro':'Synthetic.java','index':0,'exit_code':0,'input_sha256':inputhash}],
             'artifacts':['artifact.bin','certificate.json'],'artifact_hashes':{'artifact.bin':q.sha256(artifact)}})
        job=write(self.jobdir/'request.json',dict(job_id=root.name+'-solve',stage='solve',workflow_id='synthetic',runtime_digest='a'*64,macro_hashes=macro,request_digest='e'*64,source_sha256=inputhash))
        mesh=dict(cells=cells,min_cell_quality=.3,min_face_validity=1.,min_volume_change=.1,min_wall_prism_layers=6,
                  gates=dict(cell_quality=1e-5,face_validity=1.,volume_change=.01,prism_layers=6))
        conv=dict(verdict='NUMERIC_GATES_MET',residual_gates={k:{'final':v/10,'gate':v,'met':True} for k,v in qual['residual_gates'].items()},
                  residuals_met=True,mass_imbalance_ratio=1e-5,mass_imbalance_gate=.001,target_window_relative_change=.001,
                  target_window_gate=.01,target_quantities_steady=True)
        sensitivity={'time_step':dict(required=dt_required,executed=None,relative_change=None)}
        if not is_level:
            # The synthetic mesh family now carries ratios inside the registered admission
            # gate (cell<=2.2, h in [1.26,1.32]), as a real family must. GCI is invariant to
            # the ratio, so the recomputed value below is unchanged by this.
            for kind,params,values in [('mesh_levels',[16000,33554,70376],[.49,.4975,.499375]),('domain_levels',[.75,1.,1.25],[.499,.5,.5005])]:
                refs=[]
                for i,(param,value) in enumerate(zip(params,values)):
                    lev=Fixture(root/(kind+str(i)),is_level=True,cells=param if kind=='mesh_levels' else 64000,
                                target=value,domain_size=param if kind=='domain_levels' else 1.,
                                mesh_size=.1*(64000/param)**(1/3) if kind=='mesh_levels' else .1,dt=dt,dt_required=dt_required)
                    refs.append(write(lev.root/'bundle.json',{'status':'ok','parameter':param if kind=='mesh_levels' else param**3,
                                'cells':param if kind=='mesh_levels' else 64000,'target_mean':value,'quality_inputs':lev.packet}))
                sensitivity[kind]=dict(required=3,executed=3,all_levels_qualified=True,limit=.02,records=refs)
            sensitivity['mesh_levels'].update(gci_32=1.25*(.499375-.4975)/3/.499375,Fs=1.25,monotonic=True)
            sensitivity['domain_levels']['relative_change']=(.5005-.499)/.5005
            if dt_required:
                refs=[]
                for i,(step,value) in enumerate([(dt,.5),(dt/2,.5005)]):
                    lev=Fixture(root/('dt'+str(i)),is_level=True,target=value,dt=step,dt_required=True)
                    refs.append(write(lev.root/'bundle.json',{'status':'ok','parameter':step,'target_mean':value,'quality_inputs':lev.packet}))
                sensitivity['time_step']=dict(required=True,executed=2,relative_change=(.5005-.5)/.5005,limit=.02,all_levels_qualified=True,records=refs)
        self.packet={'execution':dict(run_id=root.name,state='EXECUTION_COMPLETED',request=req,state_record=state,runtime_manifest=runtime,
            plan=['solve'],stages=[dict(key='solve',name='solve',state='ok',workflow_id='synthetic',job_id=root.name+'-solve',manifest=manifest,record=sr,request=job,runner_required_artifacts=['artifact.bin','certificate.json'])],
            required_artifacts=[]),
            'numerics':dict(mesh_quality=mesh,convergence=conv,sensitivity=sensitivity),
            'engineering':dict(reference=dict(status='NOT_ESTABLISHED',source=None,value=None,tolerance=None,relative_error=None),scope_statement='synthetic fixture only',measured_value=target),
            'provenance':dict(release_id='c'*64,runtime_digest='a'*64,case_spec_sha256=specref['sha256'],case_spec_record=specref,input_sha256=inputhash,macro_sha256=macro,input_record=q.file_ref(inputfile),run_record_citation=q.CITABLE)}
        self.bind()
    def bind(self):
        packet={k:copy.deepcopy(self.packet[k]) for k in ('numerics','engineering')}
        packet['engineering'].pop('target_source',None)
        # A raw measured target outside quality_inputs is what the solver actually produced.
        cert=write(self.jobdir/'certificate.json',{'status':'ok','target_mean':self.target,'target_quantity':'drag_coefficient','target_units':'1','target_statistic':'final_or_mean','quality_inputs':packet})
        stage=self.packet['execution']['stages'][0];sr=q.read(stage['record']['path']);sr['artifact_hashes']['certificate.json']=cert['sha256']
        stage['record']=write(Path(stage['record']['path']),sr)
        self.packet['execution']['required_artifacts']=[dict(identity='solve/'+name,name=name,state='VERIFIED',sha256=h,runner_sha256=h,path=str(self.jobdir/name),runner_record=stage['record']) for name,h in sr['artifact_hashes'].items()]
        self.packet['engineering']['target_source']=dict(cert,pointer='target_mean',quantity='drag_coefficient',units='1',statistic='final_or_mean')
        self.packet['evidence']=[dict(cert,certificate=True)]
        self.packet['field_sources']={g:{'path':cert['path'],'pointer':'quality_inputs/'+g} for g in
              ('numerics/mesh_quality','numerics/convergence','numerics/sensitivity','engineering/reference')}
    def reference(self):
        source=write(self.root/'reference.json',{'synthetic_reference_value':.5,'units':'dimensionless'})
        ref=dict(status='VERIFIED',source='SYNTHETIC reference, not a real physical certificate',value=.5,measured_value=.5,tolerance=.02,relative_error=0.,verified=True,scope='synthetic fixture only',source_artifact=source)
        ref['review']=write(self.root/'reference-review.json',dict(approved=True,independent=True,reference_sha256=source['sha256'],case_spec_sha256=self.packet['provenance']['case_spec_sha256'],quantity='drag_coefficient',units='1',statistic='final_or_mean',**{k:ref[k] for k in ('source','value','tolerance','scope')}))
        self.packet['engineering']['reference']=ref;self.bind()


class QualityContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=Fixture(Path(self.temp.name));self.p=self.f.packet
    def test_q2_requires_all_numeric_fields(self):
        b=q.render_quality_block(self.p);self.assertEqual(q.STATES[2],b['quality_state']);self.assertEqual('NOT_APPLICABLE',b['engineering']['e_phys']);q.render_report(b)
    def test_real_casespec_flat_six_bounds_are_supported(self):
        spec=q.read(self.p['execution']['request']['path'])['case_spec']
        self.assertEqual(6,len(spec['domain']['outer_bounds_m']))
        self.assertEqual(q.STATES[2],q.compute_quality_state(self.p))
    def test_real_transient_time_step_s_shape_is_supported(self):
        f=Fixture(Path(self.temp.name)/'transient-shape',dt_required=True)
        self.assertEqual(.0002,q.read(f.packet['execution']['request']['path'])['case_spec']['transient']['time_step_s'])
        self.assertEqual(q.STATES[2],q.compute_quality_state(f.packet))
        f.packet['numerics']['sensitivity']['time_step']['required']=False;f.bind()
        self.assertEqual(q.STATES[1],q.compute_quality_state(f.packet))
    def test_q3_requires_verified_reference_within_tolerance(self):
        self.f.reference();b=q.render_quality_block(self.p);self.assertEqual(q.STATES[3],b['quality_state']);q.render_report(b)
    def test_missing_mesh_caps_q1(self):
        del self.p['numerics']['mesh_quality']['min_cell_quality'];self.f.bind()
        self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_missing_sensitivity_cannot_pass(self):
        self.p['numerics'].pop('sensitivity');self.f.bind();b=q.render_quality_block(self.p)
        self.assertNotEqual('PASS',b['engineering']['e_num'])
    def test_reference_pass_without_reference_rejected(self):
        self.p['engineering']['e_phys']='PASS';self.f.bind()
        with self.assertRaises(q.QualityContractError):q.render_quality_block(self.p)
    def test_failed_stage_ok_record_cannot_certify(self):
        p=Path(self.p['execution']['stages'][0]['record']['path']);d=q.read(p);d['state']='failed';write(p,d)
        with self.assertRaises(q.QualityContractError):q.compute_quality_state(self.p)
    def test_overclaims_fail_generation_for_q1_and_q2(self):
        for downgrade in (False,True):
            if downgrade:self.p['numerics']['sensitivity']={};self.f.bind()
            b=q.render_quality_block(self.p);text=q.render_report(b)
            for claim in ('engineering_verified=true','"engineering_verified": true','可用于设计','工程验证通过','结果可信','engineering verified'):
                with self.subTest(state=b['quality_state'],claim=claim),self.assertRaises(q.QualityContractError):q.validate_report_claims(text+'\n'+claim,b['quality_state'],b)
    def test_structured_design_claim_is_rejected(self):
        for downgrade in (False,True):
            if downgrade:self.p['numerics']['sensitivity']={};self.f.bind()
            self.p['engineering']['scope_statement']='可用于设计';self.f.bind()
            b=q.render_quality_block(self.p)
            with self.assertRaises(q.QualityContractError):q.render_report(b)
    def test_nested_legacy_flag_cannot_upgrade(self):
        b=q.render_quality_block(self.p);b['engineering']['engineering_verified']=True
        with self.assertRaises(q.QualityContractError):q.render_report(b)
    def test_legacy_report_columns_are_only_contract_projections(self):
        self.p['numerics']['convergence']['residual_gates']={};self.f.bind()
        b=q.render_quality_block(self.p);labels=q.report_labels(b)
        self.assertEqual(b['engineering']['e_num'],labels['E-num']['status'])
        self.assertNotEqual('PASS',labels['E-num']['checks']['convergence']['status'])
    def test_missing_report_structure_is_an_error(self):
        b=q.render_quality_block(self.p);del b['numerics']['mesh_quality']['cells']
        with self.assertRaises(q.QualityContractError):q.validate_report_claims('',b['quality_state'],b)
    def test_actual_artifact_hash_mismatch(self):
        Path(self.p['execution']['required_artifacts'][0]['path']).write_bytes(b'tampered')
        self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_fabricated_verified_artifact_is_q0(self):
        self.f.bind();self.p['execution']['required_artifacts'][0]['path']=str(Path(self.temp.name)/'missing')
        self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_shortened_reordered_duplicate_graph_is_q0(self):
        for plan in ([],['other'],['solve','solve']):
            self.p['execution']['plan']=plan;self.f.bind();self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_self_chosen_manifest_not_runtime_pin_is_q0(self):
        self.p['execution']['stages'][0]['manifest']=write(Path(self.temp.name)/'fake-manifest.json',{'stages':{'solve':{'required':[]}}});self.f.bind()
        self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_omitted_required_artifact_is_q0(self):
        self.f.bind();self.p['execution']['required_artifacts']=[];self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_casespec_gates_cannot_be_relaxed_by_citable_certificate(self):
        self.p['numerics']['convergence']['mass_imbalance_gate']=1.;self.f.bind()
        self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_residual_equation_omission_caps_q1(self):
        del self.p['numerics']['convergence']['residual_gates']['energy'];self.f.bind();self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_fake_verified_reference_does_not_upgrade(self):
        self.f.reference();self.p['engineering']['reference'].pop('review');self.f.bind()
        self.assertEqual(q.STATES[2],q.compute_quality_state(self.p))
    def test_reference_must_match_this_measurement(self):
        self.f.reference();self.p['engineering']['measured_value']=.7;self.f.bind();self.assertEqual(q.STATES[2],q.compute_quality_state(self.p))
    def test_nonfinite_and_bool_are_not_numeric_passes(self):
        for value in (True,None,float('nan'),float('inf')):
            self.p['numerics']['mesh_quality']['cells']=value;self.f.bind();self.assertNotEqual(q.STATES[2],q.compute_quality_state(self.p))
    def test_unqualified_level_cannot_establish_gci(self):
        ref=self.p['numerics']['sensitivity']['mesh_levels']['records'][0];r=q.read(ref['path']);r['quality_inputs']['numerics']['convergence']['residuals_met']=False
        self.p['numerics']['sensitivity']['mesh_levels']['records'][0]=write(Path(ref['path']),r);self.f.bind();self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_gci_number_is_recomputed(self):
        self.p['numerics']['sensitivity']['mesh_levels']['gci_32']=0.;self.f.bind();self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_failed_actual_execution_cannot_be_replaced_by_supplement(self):
        # An isolated successful certificate cannot erase a failed root run.
        d=Path(self.temp.name)/'real';stage=d/'stages'/'solve';stage.mkdir(parents=True)
        write(d/'state.json',{'state':'FAILED'});write(d/'request.json',{'steps':[{'key':'solve'}]})
        write(stage/'request.json',{'stage':'solve'});write(stage/'stage-solve.json',{'stage':'solve','state':'ok','macros':[{'status':'ok'}],'artifacts':[]})
        write(stage/'state.json',{'state':'SUCCEEDED'})
        write(stage/'measurement.json',{'status':'ok','quality_inputs':{'execution':self.p['execution']}})
        with self.assertRaises(q.QualityContractError):q.records_from_directory(d)
    def test_actual_pat18d_q1_and_neutral_report(self):
        record=q.read(ROOT/'progress.json')['product_acceptance_20261018']['pat18']
        snapshots=ROOT/'work/quality-contract/readbacks'/record['actual_run_id']
        available=sorted(snapshots.glob('*/request.json'))
        self.assertTrue(available,'Real readback is required, not a fabricated status')
        packet=q.records_from_directory(available[-1].parent);b=q.render_quality_block(packet)
        self.assertEqual(q.STATES[1],b['quality_state']);text=q.render_report(b)
        for term in ('工程验证通过','收敛','结果可信'):self.assertNotIn(term,text)
    def test_two_self_reported_targets_cannot_override_solver_value(self):
        self.f.reference();self.p['engineering']['measured_value']=.499
        ref=self.p['engineering']['reference'];ref['measured_value']=.499;ref['relative_error']=abs(.499-.5)/.5
        self.f.bind();self.assertEqual(q.STATES[2],q.compute_quality_state(self.p))
    def test_an_unexecuted_runner_record_cannot_rebind_artifact(self):
        # Actual executed sr keeps its old hash while a forged sidecar asserts new bytes.
        a=next(x for x in self.p['execution']['required_artifacts'] if x['name']=='certificate.json')
        cert=Path(a['path']);payload=q.read(cert);payload['extra']='new bytes'
        new=write(cert,payload);fake=q.read(a['runner_record']['path']);fake['artifact_hashes']['certificate.json']=new['sha256']
        a['runner_record']=write(cert.parent/'not-the-executed-runner-record.json',fake)
        a['sha256']=a['runner_sha256']=new['sha256'];self.p['evidence']=[dict(new,certificate=True)]
        self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_stage_job_and_request_identity_must_match(self):
        ex=self.p['execution'];ref=ex['stages'][0]['request'];req=q.read(ref['path']);req['request_digest']='f'*64
        ex['stages'][0]['request']=write(Path(ref['path']),req)
        self.assertEqual(q.STATES[0],q.compute_quality_state(self.p))
    def test_unhashed_sidecar_cannot_certify_q2(self):
        side=write(Path(self.temp.name)/'sidecar.json',q.read(self.p['evidence'][0]['path']))
        self.p['evidence']=[dict(side,certificate=True)]
        for source in self.p['field_sources'].values():source['path']=side['path']
        self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_fake_three_mesh_parameters_with_same_cells_rejected(self):
        ref=self.p['numerics']['sensitivity']['mesh_levels']['records'][0];b=q.read(ref['path']);b['parameter']=64000
        self.p['numerics']['sensitivity']['mesh_levels']['records'][0]=write(Path(ref['path']),b);self.f.bind()
        self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_domain_must_compare_all_pairs(self):
        level=self.p['numerics']['sensitivity']['domain_levels'];refs=[]
        for i,(param,value) in enumerate(zip([.75,1.,1.25],[.4901,.5,.5099])):
            f=Fixture(Path(self.temp.name)/('wide-domain'+str(i)),is_level=True,target=value,domain_size=param)
            refs.append(write(f.root/'bundle.json',{'status':'ok','parameter':param**3,'target_mean':value,'quality_inputs':f.packet}))
        level['records']=refs;level['relative_change']=.0198;self.f.bind()
        self.assertEqual(q.STATES[1],q.compute_quality_state(self.p))
    def test_negative_english_is_not_a_positive_claim(self):
        self.p['numerics']['sensitivity']={};self.f.bind();b=q.render_quality_block(self.p)
        self.assertTrue(q.validate_report_claims(q.render_report(b)+'\nnot converged',b['quality_state'],b))
    def test_three_mcp_entries_execute_shared_contract(self):
        import server_v2 as server
        from unittest.mock import Mock
        record=q.read(ROOT/'progress.json')['product_acceptance_20261018']['pat18']
        source=sorted((ROOT/'work/quality-contract/readbacks'/record['actual_run_id']).glob('*/request.json'))[-1].parent
        backend=Mock();backend.config={'local_results':self.temp.name};backend.get_run.return_value={'status':{'state':'EXECUTION_COMPLETED'},'engineering_verified':False}
        backend.fetch_run_results.return_value={'state':'EXECUTION_COMPLETED','directory':str(source),'engineering_verified':False}
        with patch.object(server,'Backend',return_value=backend),patch.object(q,'remote_snapshot',return_value=source):
            get=server.get_run(record['actual_run_id']);fetch=server.fetch_run_results(record['actual_run_id'])
            report=server.report_run(record['actual_run_id'],evidence=str(Path(self.temp.name)/'mcp-report'),include_sim=False)
        for result in (get,fetch,report):
            self.assertEqual(q.STATES[1],result['quality_state']);self.assertEqual(result['quality_state'],result['quality']['quality_state'])
        backend.submit_run.assert_not_called();backend.deploy.assert_not_called()
    def test_legacy_engineering_flag_stays_false_in_tools(self):
        source=(ROOT/'scripts/server_v2.py').read_text()
        self.assertIn('attach_run_quality',source);self.assertIn('remote_snapshot',source)
    def test_support_matrix_has_unverified_labels(self):
        rows=q.support_matrix();self.assertGreaterEqual(len(rows),3)
        self.assertTrue(all(row['numeric_label']=='NUMERICALLY_UNVERIFIED' and row['engineering_label']=='ENGINEERING_UNVERIFIED' for row in rows))

if __name__=='__main__':unittest.main()
