"""O9 formal-worker regressions. Default tests never use SSH or start STAR.

Explicit Windows function probes use only isolated fixture files/processes; they
do not deploy the worker, change accounts/ACLs/tasks, or submit a STAR request.
"""
import base64
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import backend as module
import formal_worker


class RuntimeManifest(unittest.TestCase):
    def test_sort_mapping_digest_and_canonical_bytes_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {'worker/run_job.ps1': b'job\r\n',
                        'macros/Probe.java': b'macro\n',
                        'scripts/resource_contract.py': b'contract\n'}
            files = []
            for name, data in contents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                files.append(path)
            manifest = module.build_runtime_manifest(reversed(files), root)
            digest = hashlib.sha256()
            for name in sorted(contents):
                digest.update(name.encode('utf-8'))
                digest.update(contents[name])
            self.assertEqual(manifest['runtime_digest'], digest.hexdigest())
            self.assertEqual([x['source'] for x in manifest['files']], sorted(contents))
            self.assertEqual(manifest['files'][-1]['path'], 'run_job.ps1')
            self.assertEqual(manifest['files'][0]['path'], 'macros/Probe.java')
            encoded = module.runtime_manifest_bytes(manifest)
            self.assertEqual(encoded, module.runtime_manifest_bytes(dict(reversed(list(manifest.items())))))
            self.assertTrue(encoded.endswith(b'\n'))
            self.assertFalse(encoded.startswith(b'\xef\xbb\xbf'))
            before = manifest['runtime_digest']
            files[1].write_bytes(b'changed macro\n')
            self.assertNotEqual(module.build_runtime_manifest(files, root)['runtime_digest'], before)

    def test_windows_case_collisions_duplicate_files_and_outside_root_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root/'macros/Probe.java'
            first.parent.mkdir()
            first.write_text('first')
            second = root/'macros/probe.java'
            second.write_text('second')
            for files in ([first, first], [first, second], [Path(__file__)]):
                with self.subTest(files=files), self.assertRaises(ValueError):
                    module.build_runtime_manifest(files, root)


class IndependentReviewPins(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)/'review.json'
        self.case_ref = 'library/'+'a'*64+'.sim'
        self.macro = 'AuditAutomationPreflight.java'
        manifest = module.build_runtime_manifest(module.Backend.runtime_files(None))
        self.record = {'approved': True, 'independent': True,
                       'controller_sha256': module.sha256(ROOT/'scripts/backend.py'),
                       'pipeline_sha256': module.sha256(ROOT/'workflows/core/manifest.json'),
                       'macro_hashes': {self.macro: module.sha256(ROOT/'macros'/self.macro)},
                       'input_hashes': {'input.sim': 'a'*64},
                       'resource_contract_sha256': module.sha256(ROOT/'scripts/resource_contract.py'),
                       'runtime_digest': manifest['runtime_digest'],
                       'runtime_manifest_sha256': hashlib.sha256(module.runtime_manifest_bytes(manifest)).hexdigest(),
                       'runtime_guard_sha256': module.sha256(ROOT/'worker/runtime_guard.ps1'),
                       'runner_sha256': module.sha256(ROOT/'worker/run_job.ps1'),
                       'pipeline_runner_sha256': module.sha256(ROOT/'worker/run_pipeline.ps1')}

    def load(self, record):
        self.path.write_text(json.dumps(record))
        return module.load_review_record(str(self.path), 'core', 'preflight', self.case_ref,
                                        ['run_pipeline.ps1', 'run_job.ps1'], [self.macro])

    def test_each_complete_runtime_pin_required_and_forwarded(self):
        fields = ('runtime_digest', 'runtime_manifest_sha256', 'runtime_guard_sha256',
                  'pipeline_runner_sha256', 'runner_sha256')
        accepted = self.load(self.record)
        for field in fields:
            self.assertEqual(accepted[field], self.record[field])
            for missing in (True, False):
                changed = copy.deepcopy(self.record)
                if missing:
                    changed.pop(field)
                else:
                    changed[field] = '0'*64
                with self.subTest(field=field, missing=missing), self.assertRaises(ValueError):
                    self.load(changed)

    def test_unrelated_runtime_drift_refuses_old_stage_review(self):
        real = module.build_runtime_manifest
        def drift(*args, **kwargs):
            value = real(*args, **kwargs)
            value['runtime_digest'] = '0'*64
            return value
        with patch.object(module, 'build_runtime_manifest', side_effect=drift), self.assertRaises(ValueError):
            self.load(self.record)

    def test_input_or_macro_drift_refuses_even_matching_runtime(self):
        for field in ('input_hashes', 'macro_hashes'):
            changed = copy.deepcopy(self.record)
            changed[field] = {next(iter(changed[field])): '0'*64}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(changed)


class SubmissionProtocol(unittest.TestCase):
    def setUp(self):
        self.worker = module.Backend.__new__(module.Backend)
        self.worker.remote_root = r'C:\FixtureWorker'
        self.worker.config = {'star_launcher': r'C:\Fixture\never-executed.exe'}
        self.worker.ps = Mock(return_value={'fixture_only': True})
        self.worker.runtime_digest = lambda: 'd'*64
        self.pins = {'runtime_digest': 'd'*64, 'runtime_manifest_sha256': 'e'*64,
                     'runtime_guard_sha256': 'f'*64, 'runner_sha256': '1'*64,
                     'pipeline_runner_sha256': '2'*64,
                     'expected_worker_sid': 'S-1-5-21-125541176-3054636984-2811364288-1008',
                     'deployment_review_sha256': '3'*64, 'health_watchdog_seconds': 900}
        self.review = {'macro_hashes': {'AuditAutomationPreflight.java': '4'*64},
                       'runner_sha256': '1'*64, 'pipeline_runner_sha256': '2'*64,
                       'input_sha256': 'a'*64, 'sha256': '5'*64, 'path': 'fixture-review.json',
                       'resource_contract_sha256': module.sha256(ROOT/'scripts/resource_contract.py')}

    def submit(self, kind, outer, stage):
        with patch.object(module, 'load_review_record', return_value=self.review), \
             patch.object(formal_worker, 'request_pins', return_value=self.pins), \
             patch('casespec.resolve_operations', return_value={'operations': {}}):
            if kind == 'run':
                self.worker.submit_run('library/'+'a'*64+'.sim', 'fixture-run-1',
                    steps=[{'key': 'first', 'workflow_id': 'core', 'stage': 'preflight',
                            'timeout_seconds': stage},
                           {'key': 'second', 'workflow_id': 'core', 'stage': 'preflight',
                            'timeout_seconds': stage}], timeout_seconds=outer, review_ref='fixture-review.json')
            else:
                self.worker.submit_job('preflight', 'library/'+'a'*64+'.sim', 'fixture-job-1',
                                       timeout_seconds=outer, workflow_id='core', review_ref='fixture-review.json')
        script = self.worker.ps.call_args.args[0]
        values = re.findall(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", script)
        self.assertEqual(len(values), 1)
        return json.loads(base64.b64decode(values[0])), script

    def test_all_pins_reach_both_requests_and_scheduled_action_binds_raw_request(self):
        for kind in ('job', 'run'):
            with self.subTest(kind=kind):
                request, script = self.submit(kind, 1, 2**40)
                for field, value in self.pins.items():
                    self.assertEqual(request[field], value, field)
                self.assertEqual(request['macro_hashes'] if kind == 'job' else request['steps'][0]['macro_hashes'],
                                 self.review['macro_hashes'])
                self.assertIn('-ExpectedRequestSha256', script)
                self.assertIn('(Get-FileHash -LiteralPath $requestFile -Algorithm SHA256)', script)
                self.assertIn('-ExecutionTimeLimit ([TimeSpan]::Zero)', script)
                self.assertIn('-RunLevel Limited', script)
                self.assertEqual(request['timeout_seconds'], 1)
                if kind == 'run':
                    self.assertEqual([x['timeout_seconds'] for x in request['steps']], [2**40, 2**40])

    def test_zero_and_large_observations_do_not_reject_either_entry(self):
        for kind in ('job', 'run'):
            for observed in (0, 1, 2**40):
                with self.subTest(kind=kind, observation=observed):
                    request, _ = self.submit(kind, observed, 2**40)
                    self.assertEqual(request['timeout_seconds'], observed)

    def test_invalid_observation_types_are_rejected_before_transport(self):
        for kind in ('job', 'run'):
            for value in (-1, True, 1.5, '1'):
                self.worker.ps.reset_mock()
                with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                    self.submit(kind, value, 1)
                self.worker.ps.assert_not_called()

    def test_undeclared_stage_parameter_refused_locally(self):
        with self.assertRaisesRegex(ValueError, 'does not declare'):
            self.worker.submit_job('preflight', 'library/'+'a'*64+'.sim', 'fixture-job-1',
                                   workflow_id='core', parameters={'UNDECLARED_VALUE': '1'})
        self.worker.ps.assert_not_called()

    def test_unapproved_deployment_refuses_before_transport(self):
        with patch.object(module, 'load_review_record', return_value=self.review), \
             patch.object(formal_worker, 'deployment_review', side_effect=ValueError('no independent deployment approval')):
            with self.assertRaisesRegex(ValueError, 'deployment approval'):
                self.worker.submit_job('preflight', 'library/'+'a'*64+'.sim', 'fixture-job-1',
                                       workflow_id='core', review_ref='fixture-review.json')
        self.worker.ps.assert_not_called()


class DeploymentApproval(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'review').mkdir()
        self.path = self.root/'review/formal-worker-deployment-fixture.json'
        self.pointer = self.root/'review/active.json'
        self.worker = module.Backend.__new__(module.Backend)
        self.worker.remote_root = r'C:\FixtureWorker'
        self.worker.ps = Mock(side_effect=AssertionError('Offline approval must not query observed identity'))
        self.record = dict(formal_worker.runtime_pins(self.worker),
            approved_for_deployment=True, independent=True, remote_root=self.worker.remote_root,
            runtime_files=self.worker.runtime_manifest()['files'],
            expected_worker_sid='S-1-5-21-125541176-3054636984-2811364288-1008',
            support_hashes={name: module.sha256(ROOT/name) for name in
                ('scripts/backend.py', 'scripts/formal_worker.py',
                 'scripts/security/provision_restricted_worker.ps1', 'scripts/security/grant_batch_logon.ps1')})
        actual_pins=formal_worker.runtime_pins(self.worker)
        manifest=self.worker.runtime_manifest()
        for name in self.record['support_hashes']:
            dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
        for patcher in (patch.object(module,'PLUGIN',self.root),
                        patch.object(formal_worker,'runtime_pins',return_value=actual_pins),
                        patch.object(self.worker,'runtime_manifest',return_value=manifest)):
            patcher.start();self.addCleanup(patcher.stop)

    def pins(self, record):
        self.path.write_text(json.dumps(record))
        self.pointer.write_text(json.dumps({'schema_version':1,'review_ref':'review/'+self.path.name,'review_sha256':module.sha256(self.path)}))
        with patch.object(formal_worker, 'DEPLOY_REVIEW', str(self.pointer)):
            return formal_worker.request_pins(self.worker,require_ready=False)

    def test_pointer_sha_ref_and_symlink_drift_are_refused(self):
        for kind in ('sha','outside','invalid','symlink'):
            with self.subTest(kind=kind):
                self.pins(self.record)
                pointer=json.loads(self.pointer.read_text())
                if kind=='sha':pointer['review_sha256']='0'*64
                elif kind=='outside':pointer['review_ref']='review/../outside.json'
                elif kind=='invalid':pointer['review_ref']='review/not-approved.json'
                else:
                    target=self.root/'outside.json';target.write_bytes(self.path.read_bytes());self.path.unlink();self.path.symlink_to(target)
                self.pointer.write_text(json.dumps(pointer))
                with patch.object(formal_worker,'DEPLOY_REVIEW',str(self.pointer)),self.assertRaises(ValueError):
                    formal_worker.deployment_review(self.worker)
                if kind=='symlink':self.path.unlink()

    def test_deployment_pins_fixed_review_sid_and_exact_review_bytes(self):
        accepted = self.pins(self.record)
        self.assertEqual(accepted['expected_worker_sid'], self.record['expected_worker_sid'])
        self.assertEqual(accepted['deployment_review_sha256'], module.sha256(self.path))
        self.worker.ps.assert_not_called()

    def test_each_deployment_identity_and_support_drift_refused(self):
        mutations = {'approved_for_deployment': False, 'independent': False,
                     'runtime_digest': '0'*64, 'runtime_manifest_sha256': '0'*64,
                     'runner_sha256': '0'*64, 'pipeline_runner_sha256': '0'*64,
                     'runtime_guard_sha256': '0'*64, 'remote_root': r'C:\OtherRoot',
                     'runtime_files': self.record['runtime_files'][:-1],
                     'support_hashes': {}, 'expected_worker_sid': 'observed-current-user'}
        for field, value in mutations.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.pins(dict(self.record, **{field: value}))
        for name in self.record['support_hashes']:
            changed = copy.deepcopy(self.record)
            changed['support_hashes'][name] = '0'*64
            with self.subTest(support=name), self.assertRaises(ValueError):
                self.pins(changed)


@unittest.skipUnless(os.environ.get('STARCCM_FORMAL_WINDOWS_GUARDS') == '1' or os.environ.get('STARCCM_WINDOWS_GUARDS') == '1',
                     'explicit Windows non-STAR fixture only')
class WindowsFunctionGuards(unittest.TestCase):
    def test_actual_staged_macro_hash_guard_refuses_copy_tamper(self):
        source=(ROOT/'worker/run_job.ps1').read_text()
        line=next(line for line in source.splitlines() if "throw 'Staged macro hash mismatch'" in line)
        self.assertLess(source.index(line),source.index('$proc=New-FormalOwnedProcess'))
        b=module.Backend();directory='C:\\CodexCFDRestricted\\formal-staged-macro-'+uuid.uuid4().hex[:16]
        text='reviewed macro fixture';digest=hashlib.sha256(text.encode()).hexdigest()
        config={'macro_hashes':{'Fixture.java':digest}}
        setup='$JobDir='+module.ps_quote(directory)+";$macro='Fixture.java';$request=ConvertFrom-Json "+module.ps_quote(json.dumps(config))+";New-Item -ItemType Directory -Path $JobDir|Out-Null;[IO.File]::WriteAllText((Join-Path $JobDir $macro),'reviewed macro fixture',(New-Object Text.UTF8Encoding($false)));"
        code=setup+line+";[IO.File]::WriteAllText((Join-Path $JobDir $macro),'tampered');$reason=$null;try{"+line+"}catch{$reason=$_.Exception.Message};@{positive_pass=$true;tamper_reason=$reason;star_started=$false}|ConvertTo-Json"
        result=b.ps(code)
        self.assertTrue(result['positive_pass'])
        self.assertEqual(result['tamper_reason'],'Staged macro hash mismatch')

    def test_stage_environment_restores_parent_after_success_and_failure(self):
        source=(ROOT/'worker/run_job.ps1').read_text()
        apply=source.split('# FORMAL_ENV_APPLY:',1)[1].split('\n',1)[1].split('# FORMAL_ENV_APPLIED',1)[0]
        restore=source.split('# FORMAL_ENV_RESTORE:',1)[1].split('\n',1)[1].split('# FORMAL_ENV_RESTORED',1)[0]
        self.assertGreater(source.index('# FORMAL_ENV_RESTORE:'),source.rindex('}finally {'))
        code="""$results=@();foreach($fail in @($false,$true)){
            [Environment]::SetEnvironmentVariable('FORMAL_ENV_ORIGINAL','parent','Process')
            [Environment]::SetEnvironmentVariable('FORMAL_ENV_NEW',$null,'Process')
            $previousEnv=@{};$appliedEnv=@{FORMAL_ENV_ORIGINAL='stage';FORMAL_ENV_NEW='new'}
            try{
        """+apply+"""
            if($env:FORMAL_ENV_ORIGINAL -cne 'stage' -or $env:FORMAL_ENV_NEW -cne 'new'){throw 'Apply failed'}
            if($fail){throw 'fixture failure'}
            }catch{if($_.Exception.Message -ne 'fixture failure'){throw}}finally{
        """+restore+"""
            }
            $results+=@{failure=$fail;original=$env:FORMAL_ENV_ORIGINAL;new_missing=($null -eq [Environment]::GetEnvironmentVariable('FORMAL_ENV_NEW','Process'))}
        };$results|ConvertTo-Json
        """
        result=module.Backend().ps(code)
        self.assertEqual(len(result),2)
        self.assertTrue(all(row['original']=='parent' and row['new_missing'] for row in result),result)

    def test_request_resource_defaults_can_be_added_to_powershell_object(self):
        source=(ROOT/'worker/run_job.ps1').read_text()
        first=source.index('     $request | Add-Member -NotePropertyName min_free_memory_gb')
        stop=source.index('     $freeGb=',first)
        request={'resource_contract':{'memory_reserve_gb':16},'logical_cores':64,'memory_gb':64}
        result=module.Backend().ps('$request=ConvertFrom-Json '+module.ps_quote(json.dumps(request))+';$reserveGB=16;'+source[first:stop]+';$request|ConvertTo-Json -Depth 4')
        self.assertEqual(result['min_free_memory_gb'],16)
        self.assertEqual(result['min_free_disk_gb'],20)

    def test_two_real_runner_bootstraps_refuse_before_unreviewed_helper(self):
        worker = module.Backend()
        fixture_id = os.environ.get('STARCCM_FORMAL_BOOTSTRAP_ID', 'formal-runner-bootstrap-'+uuid.uuid4().hex[:16])
        root = 'C:\\CodexCFDRestricted\\'+fixture_id
        evidence = ROOT/'test-evidence/product-acceptance/deployed-20261018/review'/fixture_id
        evidence.mkdir(parents=True, exist_ok=False)
        marker = root+r'\UNREVIEWED_HELPER_EXECUTED.txt'
        worker.ps('if(Test-Path -LiteralPath '+module.ps_quote(root)+
                  "){throw 'Fixture already exists'};New-Item -ItemType Directory -Path "+module.ps_quote(root)+'|Out-Null;$true|ConvertTo-Json')
        guard = evidence/'runtime_guard.ps1'
        guard.write_text('Set-Content -LiteralPath '+module.ps_quote(marker)+
                         " -Value 'must not execute';throw 'UNREVIEWED_HELPER_EXECUTED'\n")
        worker.scp(guard, root+r'\runtime_guard.ps1')
        hashes = {}
        for name in ('run_pipeline.ps1', 'run_job.ps1'):
            snapshot = evidence/name
            snapshot.write_bytes((ROOT/'worker'/name).read_bytes())
            hashes[name] = module.sha256(snapshot)
            worker.scp(snapshot, root+'\\'+name)
        rows = []
        for name, argument in (('run_pipeline.ps1', 'RunDir'), ('run_job.ps1', 'JobDir')):
            for failure in ('raw-request', 'runner-self', 'guard-helper'):
                directory = root+'\\'+name.replace('.ps1', '')+'-'+failure
                worker.ps('New-Item -ItemType Directory -Path '+module.ps_quote(directory)+'|Out-Null;$true|ConvertTo-Json')
                request = {'runner_sha256': hashes['run_job.ps1'], 'pipeline_runner_sha256': hashes['run_pipeline.ps1'],
                           'runtime_guard_sha256': '0'*64, 'fixture_only': True}
                if failure == 'runner-self':
                    request['runner_sha256'] = request['pipeline_runner_sha256'] = '0'*64
                path = evidence/(name+'-'+failure+'-request.json')
                path.write_text(json.dumps(request))
                worker.scp(path, directory+r'\request.json')
                expected = '0'*64 if failure == 'raw-request' else module.sha256(path)
                result = worker.ps('& '+module.ps_quote(root+'\\'+name)+' -'+argument+' '+module.ps_quote(directory)+
                                   ' -ExpectedRequestSha256 '+module.ps_quote(expected)+
                                   ';Get-Content -LiteralPath '+module.ps_quote(directory+r'\state.json')+' -Raw -Encoding UTF8|ConvertFrom-Json|ConvertTo-Json -Depth 8')
                reason = {'raw-request': 'Scheduled request hash mismatch',
                          'runner-self': 'Pipeline runner hash mismatch' if name == 'run_pipeline.ps1' else 'Runner hash mismatch',
                          'guard-helper': 'Runtime guard hash mismatch'}[failure]
                self.assertEqual(result['error'], reason, result)
                self.assertFalse(worker.ps('Test-Path -LiteralPath '+module.ps_quote(marker)+'|ConvertTo-Json'))
                rows.append({'runner': name, 'case': failure, 'state': result, 'unreviewed_helper_executed': False})
        (evidence/'results.json').write_text(json.dumps({'star_started': False, 'source_hashes': hashes, 'results': rows}, indent=2)+'\n')

    def test_real_manifest_and_owned_process_health_fixtures(self):
        evidence = ROOT/'test-evidence/product-acceptance/deployed-20261018/review'
        spec = importlib.util.spec_from_file_location('independent_formal_guard_probe', evidence/'run_windows_formal_guard_probe.py')
        probe_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe_module)
        probe_id = os.environ.get('STARCCM_FORMAL_HEALTH_ID', 'formal-function-guards-'+uuid.uuid4().hex[:16])
        probe = probe_module.Probe(probe_id)
        probe.run(health=True)
        self.assertTrue(probe.data['all_expected_results_matched'])
        self.assertFalse(probe.data['star_started'])
        self.assertFalse(probe.data['deployment_performed'])

    def test_real_admission_refuses_each_occupancy_before_request_directory(self):
        worker = module.Backend()
        fixture_id = os.environ.get('STARCCM_FORMAL_GUARD_ID', 'formal-admission-'+uuid.uuid4().hex[:16])
        root = 'C:\\CodexCFDRestricted\\' + fixture_id
        remote = module.Backend.__new__(module.Backend)
        remote.remote_root = root
        results = []
        for kind in ('clear', 'formal-task', 'restricted-task', 'star-process', 'durable-request'):
            directory = root+'\\'+kind
            target = directory+'\\new-request'
            task = "@{TaskName='CodexCFD-Run-Fixture';State='Running'}" if kind == 'formal-task' else (
                   "@{TaskName='CodexCFD-Restricted-Fixture';State='Running'}" if kind == 'restricted-task' else '')
            process = "@{Name='starccm+.exe'}" if kind == 'star-process' else ''
            setup = ('function Get-ScheduledTask {'+task+'};function Get-CimInstance {'+process+'};'
                     '$ErrorActionPreference="Stop";if(Test-Path -LiteralPath '+module.ps_quote(directory)+
                     "){throw 'Fixture already exists'};New-Item -ItemType Directory -Path "+module.ps_quote(directory)+' | Out-Null;')
            if kind == 'durable-request':
                setup += 'New-Item -ItemType Directory -Force -Path '+module.ps_quote(root+r'\jobs\active-fixture')+" | Out-Null;Set-Content -LiteralPath "+module.ps_quote(root+r'\jobs\active-fixture\state.json')+" -Value '{\"state\":\"RUNNING\"}';"
            body = 'New-Item -ItemType Directory -Path '+module.ps_quote(target)+" | Out-Null;@{accepted=$true}|ConvertTo-Json"
            wrapped = formal_worker.guarded_submission(remote, body)
            code = setup+'try{'+wrapped+'}catch{@{accepted=$false;reason=$_.Exception.Message}|ConvertTo-Json}'
            value = worker.ps(code)
            exists = worker.ps('Test-Path -LiteralPath '+module.ps_quote(target)+' | ConvertTo-Json')
            self.assertEqual(value['accepted'], kind == 'clear', value)
            self.assertEqual(exists, kind == 'clear', value)
            results.append({'kind': kind, 'result': value, 'request_directory_exists': exists})
        path = ROOT/'test-evidence/product-acceptance/deployed-20261018/review'/fixture_id
        path.mkdir(parents=True, exist_ok=True)
        (path/'admission.json').write_text(json.dumps({'star_started': False, 'results': results}, indent=2)+'\n')

    def test_actual_powershell_resource_fragments_accept_large_observations(self):
        worker = module.Backend()
        contract = {'schema_version': 1, 'logical_cores': 64, 'memory_gb': 64,
                    'memory_reserve_gb': 16, 'outer_timeout_seconds': 2**40}
        for name, variable, end in [('run_pipeline.ps1', 'req', '$system=Get-CimInstance'),
                                    ('run_job.ps1', 'request', '    if(!(Test-Path -LiteralPath $request.star_launcher')]:
            source = (ROOT/'worker'/name).read_text()
            start = source.index('# New contracts')
            stop = source.index(end, start)
            request = dict(resource_contract=contract, logical_cores=64, memory_gb=64, timeout_seconds=1)
            result = worker.ps('$'+variable+'=ConvertFrom-Json '+module.ps_quote(json.dumps(request))+';'+source[start:stop]+';$true|ConvertTo-Json')
            self.assertIs(result, True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
