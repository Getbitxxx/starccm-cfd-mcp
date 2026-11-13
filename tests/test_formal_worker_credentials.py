"""DPAPI registration counterexamples; fixture password is never an account credential."""
import json
import os
import sys
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from backend import Backend,ps_quote
from formal_worker import credential_registration_ps
import formal_worker

class RegistrationContract(unittest.TestCase):
    def test_submissions_require_real_installation_readiness(self):
        b=Backend()
        approval={'expected_worker_sid':'S-1-5-21-1-2-3-1008','deployment_review_sha256':'a'*64}
        with patch.object(formal_worker,'deployment_review',return_value=approval),patch.object(formal_worker,'health',return_value={'worker_deployed':False}):
            with self.assertRaisesRegex(ValueError,'identity/ACL/runtime receipt is not ready'):
                formal_worker.request_pins(b)
            # Installation itself may obtain reviewed pins while the identity proof is pending.
            pins=formal_worker.request_pins(b,require_ready=False)
            self.assertEqual(pins['expected_worker_sid'],approval['expected_worker_sid'])
        with patch.object(formal_worker,'deployment_review',return_value=approval),patch.object(formal_worker,'health',return_value={'worker_deployed':True}):
            self.assertEqual(formal_worker.request_pins(b)['expected_worker_sid'],approval['expected_worker_sid'])

    def test_health_requests_reviewed_pins_without_requiring_itself(self):
        b=Backend();approval={'expected_worker_sid':'S-1-5-21-1-2-3-1008','deployment_review_sha256':'a'*64}
        original=formal_worker.request_pins
        with patch.object(formal_worker,'deployment_review',return_value=approval),patch.object(b,'ps',return_value={'worker_deployed':False}),patch.object(b,'list_workflows',return_value=[]),patch.object(formal_worker,'request_pins',wraps=original) as calls:
            self.assertFalse(formal_worker.health(b)['worker_deployed'])
        self.assertEqual(calls.call_count,1)
        self.assertIs(calls.call_args.kwargs['require_ready'],False)

    def test_formal_tasks_use_managed_password_without_s4u_or_elevation(self):
        source=(ROOT/'scripts/backend.py').read_text()+(ROOT/'scripts/formal_worker.py').read_text()
        self.assertNotIn('-LogonType S4U',source)
        self.assertIn('-Password $taskSecret -RunLevel Limited',source)
        self.assertIn('[Array]::Clear($plainBytes,0,$plainBytes.Length)',source)
        self.assertIn('password_last_set -cne',source)
        provisioning=(ROOT/'scripts/security/provision_restricted_worker.ps1').read_text()
        self.assertIn('DataProtectionScope]::LocalMachine',provisioning)
        self.assertIn('DataProtectionScope]::LocalMachine',source)
        self.assertIn("'S-1-5-18','S-1-5-32-544'",provisioning)
        self.assertIn("'Managed worker already running",provisioning)

@unittest.skipUnless(os.environ.get('STARCCM_WINDOWS_GUARDS')=='1','explicit Windows non-STAR credential fixture')
class WindowsCredentialFixtures(unittest.TestCase):
    def test_actual_registration_reader_accepts_dpapi_and_refuses_sid_stamp_drift(self):
        backend=Backend();root=r'C:\CodexCFDRestricted\credential-fixture-'+uuid.uuid4().hex[:16]
        fixture=Backend.__new__(Backend);fixture.remote_root=root
        function=credential_registration_ps(fixture)
        code=rf'''
$root={ps_quote(root)}
New-Item -ItemType Directory -Path (Join-Path $root 'service-identity') | Out-Null
$u=Get-LocalUser -Name CodexCFDWorker
Add-Type -AssemblyName System.Security
$encrypted=[Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::Unicode.GetBytes('fixture-only-not-an-account-password'),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
$credential=@{{format_version=2;protection_scope='LocalMachine';worker_sid=$u.SID.Value;password_last_set=$u.PasswordLastSet.ToUniversalTime().ToString('o');protected_password=[Convert]::ToBase64String($encrypted)}}
$path=Join-Path $root 'service-identity\worker-credential-v2.json'
$credential|ConvertTo-Json|Set-Content -LiteralPath $path -Encoding UTF8
function Register-ScheduledTask {{param($TaskName,$Action,$User,$Password,$RunLevel,$Settings)
 if($Password -cne 'fixture-only-not-an-account-password' -or $RunLevel -cne 'Limited'){{throw 'Incorrect fixture registration'}}
 $script:registered=$true
}}
{function}
$script:registered=$false
Register-FormalTask -TaskName 'NOT-A-REAL-TASK' -Action @{{}} -Settings @{{}} -ExpectedSid $u.SID.Value
$positive=$script:registered
$errors=@()
foreach($kind in @('sid','stamp','format','scope')){{
 $key=switch($kind){{'sid'{{'worker_sid'}};'stamp'{{'password_last_set'}};'format'{{'format_version'}};'scope'{{'protection_scope'}}}}
 $original=$credential[$key]
 $credential[$key]='tampered'
 $credential|ConvertTo-Json|Set-Content -LiteralPath $path -Encoding UTF8
 $script:registered=$false
 try{{Register-FormalTask -TaskName 'NOT-A-REAL-TASK' -Action @{{}} -Settings @{{}} -ExpectedSid $u.SID.Value;throw 'Unexpected accept'}}catch{{$errors+=@{{kind=$kind;reason=$_.Exception.Message;registered=$script:registered}}}}
 $credential[$key]=$original
}}
@{{positive=$positive;negative=$errors;registration_pid=$PID;account_changed=$false;real_task_registered=$false;star_started=$false}}|ConvertTo-Json -Depth 5
'''
        split=code.index('function Register-ScheduledTask')
        first=backend.ps(code[:split]+"@{encryption_pid=$PID}|ConvertTo-Json")
        rehydrate="$u=Get-LocalUser -Name CodexCFDWorker;$root="+ps_quote(root)+";$path=Join-Path $root 'service-identity\\worker-credential-v2.json';$stored=Get-Content -LiteralPath $path -Raw -Encoding UTF8|ConvertFrom-Json;$credential=@{};foreach($p in $stored.PSObject.Properties){$credential[$p.Name]=$p.Value};"
        result=backend.ps(rehydrate+code[split:])
        self.assertNotEqual(first['encryption_pid'],result['registration_pid'])
        result['encryption_pid']=first['encryption_pid']
        self.assertTrue(result['positive'])
        self.assertEqual(len(result['negative']),4)
        self.assertTrue(all(not row['registered'] and row['reason']==('Managed credential format mismatch' if row['kind'] in ('format','scope') else 'Managed task credential identity/stamp mismatch') for row in result['negative']))
        evidence=ROOT/'test-evidence/product-acceptance/deployed-20261018/review'
        (evidence/'credential-reader-fixture.json').write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':unittest.main()
