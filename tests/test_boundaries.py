import sys
import unittest
import base64
import json
import tempfile
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from backend import valid_id, valid_ref, ps_quote, workflow_catalog, Backend
from tests.formal_worker_test_helpers import (current_runtime_review_pins,
                                              mock_approved_request_pins)

ROOT=Path(__file__).resolve().parents[1]
LIBRARY_SHA='a'*64

def matching_review(workflow_id,stage,case_ref='library/'+LIBRARY_SHA+'.sim',
                    runner='worker/run_job.ps1',approved=True,independent=True,
                    record_stage=None,**overrides):
    """Write a review record that matches the live artifacts, exactly as a real one must."""
    from backend import sha256
    macro_names=sorted(workflow_catalog()[workflow_id]['stages'][stage]['macros'])
    record={'schema_version':1,'approved':approved,'independent':independent,
            'reviewer':'unit-test','workflow_id':workflow_id,
            'stage':record_stage if record_stage is not None else stage,
            'pipeline_sha256':sha256(ROOT/'workflows'/workflow_id/'manifest.json'),
            'controller_sha256':sha256(ROOT/'scripts'/'backend.py'),
            'runner_sha256':sha256(ROOT/runner),
            'macro_hashes':{name:sha256(ROOT/'macros'/name) for name in macro_names},
            'input_hashes':{'input.sim':case_ref.split('/')[-1].replace('.sim','')},
            # 2026-10-14（C3）：两条路径一律要求钉住 resource_contract.py
            'resource_contract_sha256':sha256(ROOT/'scripts'/'resource_contract.py')}
    record.update(current_runtime_review_pins())
    record.update(overrides)
    handle=tempfile.NamedTemporaryFile('w',suffix='.json',delete=False,encoding='utf-8')
    json.dump(record,handle)
    handle.close()
    return handle.name

class InputBoundaries(unittest.TestCase):
    def test_references_cannot_escape_worker_root(self):
        for ref in ['../secret.sim','jobs/a/../../secret.sim','C:/secret.sim','jobs/a;whoami.sim','jobs/a\\secret.sim','/library/a.sim']:
            with self.subTest(ref=ref),self.assertRaises(ValueError):valid_ref(ref,True)
        self.assertEqual(valid_ref('jobs/run-12345/baseline_prepared.sim',True),'jobs/run-12345/baseline_prepared.sim')
    def test_job_ids_cannot_inject_task_or_path_names(self):
        for name in ['../job-123','job;echo-hi','x','UPPERCASE','job\n123456']:
            with self.subTest(name=name),self.assertRaises(ValueError):valid_id(name)
        self.assertEqual(valid_id('job-123456'),'job-123456')
    def test_powershell_quoting_is_literal(self):
        self.assertEqual(ps_quote("a'b$();x"),"'a''b$();x'")
    def test_short_powershell_script_uses_encoded_command_without_stdin(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        completed=unittest.mock.Mock(returncode=0,stdout=b'{}',stderr=b'')
        with patch('backend.subprocess.run',return_value=completed) as run:
            self.assertEqual(backend.ps("[pscustomobject]@{ok=$true} | ConvertTo-Json"),{})
        args,kwargs=run.call_args
        argv=args[0]
        self.assertIn('-EncodedCommand',argv[-1])
        encoded=argv[-1].split()[-1]
        self.assertIn("[pscustomobject]@{ok=$true}",base64.b64decode(encoded).decode('utf-16le'))
        self.assertNotIn('input',kwargs)
    def test_workflow_catalog_and_rejection(self):
        catalog=workflow_catalog()
        # Explicit allowlist snapshot: a packaged workflow that is not listed here fails the
        # test, so admitting a new pack is always a deliberate, reviewable edit.
        self.assertEqual(set(catalog),{'core','propeller-periodic-2502','external-domain-2502',
                                       'static-external-flow-2502'})
        # R11: the scenario-1 pack now registers the whole verified chain, not only build-body. The
        # allowlist is still an explicit snapshot, so adding a stage remains a deliberate edit.
        self.assertEqual(set(catalog['static-external-flow-2502']['stages']),
                         {'build-body','audit-body','region-physics-mesh','mesh-quality-audit','solve','validation'})
        # 2026-10-14（C2 同类：目录名契约）：期望名曾是 audit-volume-mesh-quality，而当前目录
        # 与历史执行快照（r21/r22）里都是 audit-mrf-volume-mesh-quality —— 一次改名未同步测试。
        # 判据改成"存在一个体网格质量审计阶段"，并断言当前实际名称，既同步了改名又不放宽意图。
        self.assertTrue({'preflight','audit-axial-caps','audit-boundary-physics','audit-reference-frames',
                         'audit-mrf-mesh-configuration','audit-mrf-volume-mesh-quality',
                         'diagnose-imported-mrf-face-validity'}.issubset(catalog['core']['stages']))
        self.assertFalse([name for name in catalog['core']['stages']
                          if name.endswith('volume-mesh-quality') and name != 'audit-mrf-volume-mesh-quality'],
                         '体网格质量审计阶段的命名不得再分叉')
        self.assertEqual(set(catalog['propeller-periodic-2502']['stages']),{'inspect','prepare','smoke','qa'})
        self.assertTrue({'build','build-cylinder','create-full-region','map-full-flow-outer-boundaries',
                         'configure-normal-inlet-outlet','build-mrf-fluid-volumes','create-mrf-regions',
                         'configure-mrf-interfaces','execute-mrf-diagnostic-mesh','probe-transform-api'}
                        .issubset(catalog['external-domain-2502']['stages']))
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        with self.assertRaisesRegex(ValueError,'Unsupported workflow/stage'):
            backend.submit_job('preflight','library/abc.sim','reject-12345',workflow_id='../evil')
        with self.assertRaisesRegex(ValueError,'Unsupported workflow/stage'):
            backend.submit_job('smoke','library/abc.sim','reject-12345',workflow_id='core')

    def test_external_domain_requires_case_spec_and_trusted_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        with self.assertRaisesRegex(ValueError,'requires an explicit CaseSpec'):
            backend.submit_job('build','library/example.sim','reject-12345',workflow_id='external-domain-2502')
        spec={
            'schema_version':1,'scenario':'external_static','case_ref':'library/example.sim',
            'objective':'geometry trial','model_length_unit':'m','medium':'air',
            'geometry':{'body_parts':['body']},'domain':{'outer_bounds_m':[-1,-1,-1,1,1,1]},
            'flow':{'velocity_m_s':[1,0,0],'pressure_pa':101325,'temperature_k':293.15},
        }
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('build','library/example.sim','reject-12345',workflow_id='external-domain-2502',case_spec=spec)
    def test_transform_api_probe_does_not_require_engineering_domain_spec(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        review=matching_review('external-domain-2502','probe-transform-api',
                               case_ref='library/'+('b'*64)+'.sim')
        with patch.object(backend,'remote_path',return_value='C:\\service\\library\\example.sim'), \
             patch('formal_worker.request_pins',side_effect=mock_approved_request_pins), \
             patch.object(backend,'ps',return_value={'job_id':'full360-probe-12345','state':'PENDING'}) as remote:
            result=backend.submit_job('probe-transform-api','library/'+('b'*64)+'.sim','full360-probe-12345',
                                      workflow_id='external-domain-2502',review_ref=review)
        self.assertEqual(result['state'],'PENDING')
        remote.assert_called_once()
    def test_full_rotor_expansion_requires_trusted_source_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('expand-rotor-360','library/example.sim','reject-12345',
                               workflow_id='external-domain-2502')

    def test_full_flow_region_requires_trusted_source_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('create-full-region','library/example.sim','reject-12345',
                               workflow_id='external-domain-2502')

    def test_boundary_values_require_explicit_spec_and_source_audit(self):
        # 2026-10-14（C2）：接口的稳定文本是 "a successful core inspect_job_id"；动词随主语变化
        # （configure-normal-inlet-outlet 的主语是复数 Boundary values，故为 require）。原正则写死
        # requires，把一次措辞差异误判成接口变更。
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        spec=json.loads((Path(__file__).resolve().parents[1]/'cases'/'free-air-propeller-360.json').read_text())
        with self.assertRaisesRegex(ValueError,'requires a valid CaseSpec'):
            backend.submit_job('configure-normal-inlet-outlet',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502')
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('configure-normal-inlet-outlet',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502',case_spec=spec)

    def test_mrf_volume_requires_trusted_geometry_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        spec=json.loads((Path(__file__).resolve().parents[1]/'cases'/'free-air-propeller-360-mrf-zone.json').read_text())
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('build-mrf-fluid-volumes',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502',case_spec=spec)

    def test_mrf_regions_require_trusted_geometry_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        with self.assertRaisesRegex(ValueError,'requires a successful core inspect_job_id|requires a valid CaseSpec'):
            backend.submit_job('create-mrf-regions','library/example.sim','reject-12345',
                               workflow_id='external-domain-2502')

    def test_mrf_interfaces_require_trusted_source_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        spec=json.loads((Path(__file__).resolve().parents[1]/'cases'/'free-air-propeller-360-mrf-regions.json').read_text())
        with self.assertRaisesRegex(ValueError,'requires a successful core inspect_job_id|requires a valid explicit mesh CaseSpec'):
            backend.submit_job('configure-mrf-interfaces',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502',case_spec=spec)

    def test_mrf_mesh_requires_trusted_source_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        spec=json.loads((Path(__file__).resolve().parents[1]/'cases'/'free-air-propeller-360-mrf-mesh.json').read_text())
        with self.assertRaisesRegex(ValueError,'a successful core inspect_job_id'):
            backend.submit_job('configure-mrf-mesh',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502',case_spec=spec)

    def test_mrf_mesh_execution_requires_trusted_config_audit(self):
        backend=Backend(str(Path(__file__).resolve().parents[1]/'connection.example.json'))
        spec=json.loads((Path(__file__).resolve().parents[1]/'cases'/'free-air-propeller-360-mrf-mesh-execution.json').read_text())
        with self.assertRaisesRegex(ValueError,'requires a valid explicit mesh CaseSpec'):
            backend.submit_job('execute-mrf-diagnostic-mesh',spec['case_ref'],'reject-12345',
                               workflow_id='external-domain-2502')

if __name__=='__main__':unittest.main()


class ReviewGate(unittest.TestCase):
    """AGENTS.md: a STAR stage may not start without a matching independent review record.

    These tests exist because the gate used to be documentary: the worker recorded
    runtime_digest and never compared it, and submit_job never looked for a review at all.
    """

    def setUp(self):
        self.backend=Backend(str(ROOT/'connection.example.json'))
        self.case_ref='library/'+('a'*64)+'.sim'

    def submit(self,**kwargs):
        values=dict(stage='preflight',case_ref=self.case_ref,request_id='review-gate-1234',
                    workflow_id='core')
        values.update(kwargs)
        return self.backend.submit_job(**values)

    def test_stage_refuses_to_start_without_a_review_record(self):
        with self.assertRaisesRegex(ValueError,'review record is required'):
            self.submit()

    def test_review_record_must_exist(self):
        with self.assertRaisesRegex(ValueError,'Review record not found'):
            self.submit(review_ref='review/does-not-exist.json')

    def test_unapproved_review_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,approved=False)
        with self.assertRaisesRegex(ValueError,'not an approved, independent review'):
            self.submit(review_ref=review)

    def test_non_independent_review_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,independent=False)
        with self.assertRaisesRegex(ValueError,'not an approved, independent review'):
            self.submit(review_ref=review)

    def test_stale_macro_hash_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,
                               macro_hashes={'AuditAutomationPreflight.java':'0'*64})
        with self.assertRaisesRegex(ValueError,'Reviewed macro hash differs'):
            self.submit(review_ref=review)

    def test_stale_controller_hash_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,controller_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'Reviewed controller_sha256 differs'):
            self.submit(review_ref=review)

    def test_stale_runner_hash_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,runner_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'Reviewed runner_sha256 differs'):
            self.submit(review_ref=review)

    def test_stale_pipeline_hash_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,pipeline_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'Reviewed pipeline_sha256 differs'):
            self.submit(review_ref=review)

    def test_review_must_pin_the_input_content_address(self):
        review=matching_review('core','preflight',self.case_ref,
                               input_hashes={'input.sim':'c'*64})
        with self.assertRaisesRegex(ValueError,'Reviewed input hash differs'):
            self.submit(review_ref=review)

    def test_review_for_a_different_stage_is_refused(self):
        review=matching_review('core','preflight',self.case_ref,record_stage='qa')
        with self.assertRaisesRegex(ValueError,'different workflow/stage'):
            self.submit(review_ref=review)

    def test_matching_review_is_forwarded_to_the_worker(self):
        review=matching_review('core','preflight',self.case_ref)
        with patch.object(self.backend,'remote_path',return_value='C:\\service\\library\\x.sim'), \
             patch('formal_worker.request_pins',side_effect=mock_approved_request_pins), \
             patch.object(self.backend,'ps',return_value={'job_id':'review-gate-1234','state':'PENDING'}) as remote:
            result=self.submit(review_ref=review)
        self.assertEqual(result['state'],'PENDING')
        script=remote.call_args[0][0]
        # The request travels base64-encoded inside the submission script, so assert on the
        # decoded payload rather than on the encoded text.
        import re as _re
        payload=_re.search(r"FromBase64String\('([^']+)'\)",script).group(1)
        request=json.loads(base64.b64decode(payload).decode('utf-8'))
        from backend import sha256
        self.assertEqual(request['macro_hashes'],
                         {'AuditAutomationPreflight.java':
                          sha256(ROOT/'macros'/'AuditAutomationPreflight.java')})
        self.assertEqual(request['runner_sha256'],sha256(ROOT/'worker'/'run_job.ps1'))
        self.assertEqual(request['input_sha256'],'a'*64)
        self.assertEqual(request['review_sha256'],sha256(Path(review)))


class WorkerHashBinding(unittest.TestCase):
    """Structural check that the worker actually compares the reviewed hashes.

    There is no PowerShell test harness in this repository, so this asserts the executable
    statements are present. It would fail if the comparisons were removed.
    """

    def test_worker_compares_runtime_digest_runner_macros_and_input(self):
        text=(ROOT/'worker'/'run_job.ps1').read_text(encoding='utf-8')
        self.assertIn("Split-Path -Leaf $PSScriptRoot",text)
        self.assertIn('Runtime digest mismatch',text)
        self.assertIn('$request.runner_sha256',text)
        self.assertIn('Runner hash mismatch',text)
        self.assertIn('$request.macro_hashes',text)
        self.assertIn('Macro hash mismatch',text)
        self.assertIn('Input differs from the reviewed input hash',text)

    def test_pipeline_forwards_reviewed_hashes_to_each_stage(self):
        text=(ROOT/'worker'/'run_pipeline.ps1').read_text(encoding='utf-8')
        self.assertIn('macro_hashes=$item.macro_hashes',text)
        self.assertIn('runner_sha256=$req.runner_sha256',text)
        self.assertIn('input_sha256',text)
