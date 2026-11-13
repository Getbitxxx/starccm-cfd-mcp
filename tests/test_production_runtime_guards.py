"""Executable refusals on Windows; no STAR/identity/ACL mutation is reachable."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import air3b_pipeline_trial as trial
from backend import Backend,ps_quote
class ReviewPath(unittest.TestCase):
    def test_check_review_uses_repository_contract_from_any_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);contract=trial.hash_file(ROOT/'scripts/resource_contract.py')
            (p/'pipeline-review.json').write_text(json.dumps({'approved':True,'independent':True,
                'macro_hashes':{},'input_hashes':{},'runner_sha256':'runner','controller_sha256':'controller',
                'pipeline_sha256':'pipeline','support_hashes':trial.support_hashes(),
                'resource_contract_sha256':contract}))
            previous=Path.cwd()
            try:
                os.chdir(p)
                trial.check_review(p,{}, {},'runner','controller','pipeline')
            finally:os.chdir(previous)

@unittest.skipUnless(os.environ.get('STARCCM_WINDOWS_GUARDS')=='1','explicit Windows guard execution only')
class WindowsBootstrap(unittest.TestCase):
    def test_large_time_observations_do_not_reject(self):
        contract={'schema_version':1,'logical_cores':64,'memory_gb':64,'memory_reserve_gb':16,'outer_timeout_seconds':2**40}
        config={'resource_contract':contract,'logical_cores':64,'memory_gb':64,'timeout_seconds':2**41}
        for name in ['provision_restricted_worker.ps1','trial_air3b_pipeline.ps1']:
            src=(ROOT/'scripts/security'/name).read_text()
            start=src.index('# New contracts')
            end=src.index('# ExecutionTimeLimit',start) if name.startswith('provision') else src.index('$memoryBytes=$memoryGB*1GB',start)
            code='$c=ConvertFrom-Json '+ps_quote(json.dumps(config))+';\n'+src[start:end]+'\n$true | ConvertTo-Json'
            self.assertIs(Backend().ps(code),True)

    def test_real_config_and_pipeline_tampering_refused_before_side_effects(self):
        b=Backend();guard_id=os.environ.get('STARCCM_GUARD_ID','formal-bootstrap-guards-'+uuid.uuid4().hex[:16]);remote='C:\\CodexCFDRestricted\\'+guard_id
        p=ROOT/'test-evidence/product-acceptance/deployed-20261018/review'/guard_id;p.mkdir(parents=True,exist_ok=True)
        b.ps('if(Test-Path -LiteralPath '+ps_quote(remote)+"){throw 'Guard trial already exists'}; New-Item -ItemType Directory -Path "+ps_quote(remote)+' | Out-Null; $true | ConvertTo-Json')
        names=['provision_restricted_worker.ps1','trial_air3b_pipeline.ps1','grant_batch_logon.ps1']
        for name in names:b.scp(ROOT/'scripts/security'/name,remote+'\\'+name)
        pipeline=p/'pipeline.json';pipeline.write_text('{"test":"original"}\n');b.scp(pipeline,remote+r'\reviewed-pipeline.json')
        config={'root':remote,'job_dir':remote,'reviewed_artifacts':{
           'pipeline_sha256':trial.hash_file(pipeline),
           'runner_sha256':trial.hash_file(ROOT/'scripts/security/trial_air3b_pipeline.ps1'),
           'support_hashes':trial.support_hashes()}}
        cfg=p/'config.json';cfg.write_text(json.dumps(config));b.scp(cfg,remote+r'\config.json')
        def observe():return b.ps("[pscustomobject]@{star=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^star(ccm|[-.])'} | Select-Object Name,ProcessId);tasks=@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'CodexCFD*'} | Select-Object TaskName,@{n='State';e={$_.State.ToString()}});worker=(Get-LocalUser -Name CodexCFDWorker | Select-Object Name,SID,PasswordLastSet)} | ConvertTo-Json -Depth 5")
        before=observe();self.assertFalse(before['star'])
        results=[]
        for kind in ['bad-config','bad-pipeline']:
            if kind=='bad-pipeline':
                pipeline.write_text('{"test":"tampered"}\n');b.scp(pipeline,remote+r'\reviewed-pipeline.json')
            for name in names[:2]:
                actual=b.ps("$o=@{};try{& "+ps_quote(remote+'\\'+name)+' -ConfigPath '+ps_quote(remote+r'\config.json')+' -ExpectedConfigSha256 '+ps_quote('0'*64 if kind=='bad-config' else trial.hash_file(cfg))+"; $o.error='UNEXPECTED_ACCEPT'}catch{$o.error=$_.Exception.Message}; $o | ConvertTo-Json")
                self.assertEqual('Reviewed config hash mismatch' if kind=='bad-config' else 'Reviewed pipeline hash mismatch',actual['error'])
                results.append({'kind':kind,'entry':name,'result':actual})
        parsed=b.ps("$results=@();foreach($p in @("+','.join(ps_quote(remote+'\\'+n) for n in names[:2])+")){$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$tokens,[ref]$errors) | Out-Null;$results+=@{file=$p;errors=@($errors | ForEach-Object {$_.Message})}};$results | ConvertTo-Json -Depth 5")
        self.assertTrue(all(not item['errors'] for item in parsed),parsed)
        after=observe();self.assertEqual(before,after)
        (p/'results.json').write_text(json.dumps({'tests':results,'parse':parsed,'before':before,'after':after,'engineering_verified':False},ensure_ascii=False,indent=2)+'\n')
# The user-facing Windows guard command also executes the formal worker's
# manifest, health, large observation and pre-directory admission regressions.
from tests.test_formal_worker_deployment_guards import WindowsFunctionGuards
from tests.test_formal_worker_io import WindowsAtomicPublication

if __name__=='__main__':unittest.main(verbosity=2)
