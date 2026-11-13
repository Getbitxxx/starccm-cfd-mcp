"""B1-B3 regressions. Windows probes are isolated, never submit a CFD request."""
import base64
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from backend import Backend, ps_quote


class Integration(unittest.TestCase):
    def test_both_runners_gate_slot_after_mutex_and_launcher_before_use(self):
        for name, variable in [('run_job.ps1', '$request'), ('run_pipeline.ps1', '$req')]:
            src = (ROOT / 'worker' / name).read_text()
            self.assertLess(src.index('WaitOne(0)'), src.index('Assert-FormalExecutionSlotClear'))
            self.assertIn('Assert-FormalLauncherPath ' + variable + '.star_launcher', src)
        job = (ROOT / 'worker/run_job.ps1').read_text()
        self.assertLess(job.index('New-FormalProcessLease $JobDir'), job.index('$proc=New-FormalOwnedProcess'))
        self.assertIn('Complete-FormalProcessLease $proc $JobDir', job)

    def test_pipeline_has_no_state_timestamp_fallback(self):
        src = (ROOT / 'worker/run_pipeline.ps1').read_text()
        self.assertIn('Get-FormalSubmittedAt $req.submitted_at_utc', src)
        self.assertNotIn('$previousState.started_at', src)


@unittest.skipUnless(os.environ.get('STARCCM_HARDENING_WINDOWS') == '1', 'explicit Windows no-STAR probes')
class WindowsGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend()
        cls.guard = (ROOT / 'worker/runtime_guard.ps1').read_text()

    def run_ps(self, body):
        encoded = base64.b64encode(self.guard.encode()).decode()
        return self.backend.ps(". ([scriptblock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))))\n" + body, timeout=60)

    def test_all_three_files_parse_on_windows_powershell(self):
        data = {name: (ROOT / 'worker' / name).read_text() for name in ['run_job.ps1', 'run_pipeline.ps1', 'runtime_guard.ps1']}
        payload = base64.b64encode(json.dumps(data).encode()).decode()
        result = self.backend.ps("$files=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + payload + "'))|ConvertFrom-Json;$out=@();foreach($p in $files.PSObject.Properties){$t=$null;$e=$null;[void][Management.Automation.Language.Parser]::ParseInput($p.Value,[ref]$t,[ref]$e);$out+=@{name=$p.Name;errors=@($e|ForEach-Object {$_.Message})}};$out|ConvertTo-Json -Depth 5", timeout=60)
        self.assertEqual(3, len(result))
        self.assertTrue(all(not p['errors'] for p in result), result)

    def test_launcher_spaces_plus_and_shell_injection_refusals(self):
        good = [r'C:\Program Files\Siemens\20.02.008\STAR-CCM+20.02.008\star\bin\starccm+.bat']
        bad = [good[0] + c for c in ['&whoami', '|whoami', '"', '%TEMP%', '!x!', '\n', '^', '<', '>']]
        bad += ['relative.bat', '\\\\server\\launcher.bat', None, 1]
        payload = ps_quote(json.dumps({'good': good, 'bad': bad}))
        result = self.run_ps("$c=ConvertFrom-Json " + payload + ";foreach($p in $c.good){Assert-FormalLauncherPath $p};$rejected=0;foreach($p in $c.bad){try{Assert-FormalLauncherPath $p}catch{$rejected++}};@{good=$true;rejected=$rejected}|ConvertTo-Json")
        self.assertTrue(result['good'])
        self.assertEqual(len(bad), result['rejected'])

    def test_explicit_utc_required_and_legacy_values_refused(self):
        result = self.run_ps("$valid=@('2026-10-21T16:00:00Z','2026-10-21T16:00:00.123456+00:00');foreach($v in $valid){[void](Get-FormalSubmittedAt $v)};$bad=@($null,'','2026-10-21T16:00:00','2026-10-21T16:00:00+03:00','2026-99-99T16:00:00Z');$n=0;foreach($v in $bad){try{[void](Get-FormalSubmittedAt $v)}catch{$n++}};@{rejected=$n;valid=2}|ConvertTo-Json")
        self.assertEqual({'rejected': 5, 'valid': 2}, result)

    def lease_fixture(self, body):
        return self.run_ps(r"""
+$base=Join-Path $env:TEMP ('codex-hardening-'+[guid]::NewGuid().ToString('N'))
+$dir=Join-Path $base 'jobs\fixture';New-Item -ItemType Directory -Path $dir -Force|Out-Null
+$script:Ids=@(501);$script:Kills=0
+$proc=[pscustomobject]@{Id=501;OwnershipId='retained-job-handle-A';IsSuspended=$true}
+$proc|Add-Member ScriptMethod ProcessIds {return $script:Ids}
+$proc|Add-Member ScriptMethod Kill {$script:Kills++}
+$proc|Add-Member ScriptMethod WaitForEmpty {param($ms);return $false}
+$proc|Add-Member ScriptMethod CloseJob {}
+try{
+New-FormalProcessLease $dir
+[void](Update-FormalProcessLease $proc $dir)
+""".replace('\n+', '\n') + body + r"""
+}finally{Remove-Item -LiteralPath $base -Recurse -Force}
+""".replace('\n+', '\n'))

    def test_failed_kill_keeps_durable_quarantine_after_mutex_release(self):
        result = self.lease_fixture(r"""
$reason=$null;try{Stop-FormalProcessTree $proc $dir}catch{$reason=$_.Exception.Message}
$blocked=$false;try{Assert-FormalExecutionSlotClear $base}catch{$blocked=$true}
@{blocked=$blocked;reason=$reason;attempts=$script:Kills;state=(Get-Content (Join-Path $dir 'process-lease.json') -Raw|ConvertFrom-Json).state}|ConvertTo-Json
""")
        self.assertTrue(result['blocked'])
        self.assertIn('still alive', result['reason'])
        self.assertEqual(2, result['attempts'])
        self.assertEqual('ACTIVE', result['state'])

    def test_exited_parent_does_not_hide_live_java_child(self):
        result = self.lease_fixture(r"""
$script:Ids=@(502)
$reason=$null;try{Complete-FormalProcessLease $proc $dir}catch{$reason=$_.Exception.Message}
@{reason=$reason;tracked=@((Get-Content (Join-Path $dir 'process-lease.json') -Raw|ConvertFrom-Json).tracked)}|ConvertTo-Json
""")
        self.assertIn('still alive', result['reason'])
        self.assertEqual([502], result['tracked'])

    def test_pid_or_foreign_owner_cannot_supply_kill_authority(self):
        result = self.lease_fixture(r"""
$proc.OwnershipId='unrelated-owner-same-pid'
$reason=$null;try{Stop-FormalProcessTree $proc $dir}catch{$reason=$_.Exception.Message}
@{kills=$script:Kills;reason=$reason}|ConvertTo-Json
""")
        self.assertEqual(0, result['kills'])
        self.assertIn('ownership token differs', result['reason'])

    def test_uncaptured_running_root_cannot_be_registered(self):
        result = self.lease_fixture(r"""
$script:Ids=@();Complete-FormalProcessLease $proc $dir;New-FormalProcessLease $dir
$proc.IsSuspended=$false;$script:Ids=@(501)
$reason=$null;try{[void](Update-FormalProcessLease $proc $dir)}catch{$reason=$_.Exception.Message}
@{reason=$reason;kills=$script:Kills}|ConvertTo-Json
""")
        self.assertIn('before lease registration', result['reason'])
        self.assertEqual(0, result['kills'])

    def test_clean_exit_releases_lease_and_allows_next_stage(self):
        result = self.lease_fixture(r"""
$script:Ids=@();Complete-FormalProcessLease $proc $dir
function Get-CimInstance {param($ClassName,$ErrorAction);return @()}
Assert-FormalExecutionSlotClear $base
@{state=(Get-Content (Join-Path $dir 'process-lease.json') -Raw|ConvertFrom-Json).state}|ConvertTo-Json
""")
        self.assertEqual('RELEASED', result['state'])

    def test_java_with_worker_identity_is_refused_without_a_lease(self):
        result = self.lease_fixture(r"""
$script:Ids=@();Complete-FormalProcessLease $proc $dir
function Get-CimInstance {param($ClassName,$ErrorAction);[pscustomobject]@{ProcessId=900;Name='java.exe';CommandLine=($base+'\fixture')}}
$blocked=$false;try{Assert-FormalExecutionSlotClear $base}catch{$blocked=$true}
@{blocked=$blocked;kills=$script:Kills}|ConvertTo-Json
""")
        self.assertTrue(result['blocked'])
        self.assertEqual(0, result['kills'])

    def native_fixture(self, body):
        return self.run_ps(r"""
$base=Join-Path $env:TEMP ('codex-job-object-'+[guid]::NewGuid().ToString('N'))
$dir=Join-Path $base 'jobs\fixture';New-Item -ItemType Directory -Path $dir -Force|Out-Null
$proc=$null;$other=$null
try{
$exe=Join-Path $PSHOME 'powershell.exe'
""" + body + r"""
}finally{
 if($null -ne $proc){$proc.Dispose()}
 if($null -ne $other){$other.Refresh();if(!$other.HasExited){$other.Kill();[void]$other.WaitForExit(10000)};$other.Dispose()}
 Remove-Item -LiteralPath $base -Recurse -Force
}
""")

    def test_real_short_lived_parent_grandchild_and_unrelated_process(self):
        result = self.native_fixture(r"""
$other=Start-Process -FilePath $exe -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 90' -PassThru
$grandFile=Join-Path $dir 'grandchild.txt'
$childCode='$g=Start-Process -FilePath "'+$exe+'" -ArgumentList "-NoProfile -NonInteractive -Command Start-Sleep -Seconds 90" -PassThru;[IO.File]::WriteAllText("'+$grandFile+'",[string]$g.Id)'
$childEncoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
$rootCode='$c=Start-Process -FilePath "'+$exe+'" -ArgumentList "-NoProfile -NonInteractive -EncodedCommand '+$childEncoded+'" -PassThru;$c.WaitForExit();Start-Sleep -Seconds 90'
$rootEncoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($rootCode))
$args='/d /s /c ""'+$exe+'" -NoProfile -NonInteractive -EncodedCommand '+$rootEncoded+'"'
New-FormalProcessLease $dir;$proc=New-FormalOwnedProcess $args $dir
[void](Update-FormalProcessLease $proc $dir);$suspended=$proc.IsSuspended;$proc.Resume()
for($i=0;$i -lt 100 -and !(Test-Path -LiteralPath $grandFile);$i++){Start-Sleep -Milliseconds 100}
if(!(Test-Path -LiteralPath $grandFile)){throw 'Grandchild fixture did not start'}
$grandId=[int](Get-Content -LiteralPath $grandFile -Raw)
$inJob=(@($proc.ProcessIds()) -contains $grandId)
Stop-FormalProcessTree $proc $dir;$other.Refresh()
@{suspended_before_registration=$suspended;grandchild_in_job=$inJob;unrelated_survives=(!$other.HasExited);grandchild_gone=(@(Get-CimInstance Win32_Process -Filter ('ProcessId='+$grandId)).Count -eq 0);state=(Get-Content (Join-Path $dir 'process-lease.json') -Raw|ConvertFrom-Json).state}|ConvertTo-Json
""")
        self.assertEqual({'suspended_before_registration': True, 'grandchild_in_job': True,
                          'unrelated_survives': True, 'grandchild_gone': True, 'state': 'RELEASED'}, result)

    def test_real_root_exit_keeps_descendant_in_job(self):
        result = self.native_fixture(r"""
$args='/d /s /c start "" /b "'+$exe+'" -NoProfile -NonInteractive -Command Start-Sleep -Seconds 90'
New-FormalProcessLease $dir;$proc=New-FormalOwnedProcess $args $dir
[void](Update-FormalProcessLease $proc $dir);$proc.Resume();[void]$proc.WaitForExit(10000)
$reason=$null;try{Complete-FormalProcessLease $proc $dir}catch{$reason=$_.Exception.Message}
Stop-FormalProcessTree $proc $dir
@{reason=$reason;root_exited=$proc.HasExited;state=(Get-Content (Join-Path $dir 'process-lease.json') -Raw|ConvertFrom-Json).state}|ConvertTo-Json
""")
        self.assertTrue(result['root_exited'])
        self.assertIn('still alive', result['reason'])
        self.assertEqual('RELEASED', result['state'])

    def test_real_job_close_kills_tree_but_retains_unresolved_lease(self):
        result = self.native_fixture(r"""
$args='/d /s /c ""'+$exe+'" -NoProfile -NonInteractive -Command Start-Sleep -Seconds 90"'
New-FormalProcessLease $dir;$proc=New-FormalOwnedProcess $args $dir
[void](Update-FormalProcessLease $proc $dir);$proc.Resume();Start-Sleep -Milliseconds 500
$ids=@($proc.ProcessIds());$proc.CloseJob();[void]$proc.WaitForExit(10000)
Start-Sleep -Milliseconds 200
$alive=@(Get-CimInstance Win32_Process | Where-Object {$ids -contains [int]$_.ProcessId})
$blocked=$false;try{Assert-FormalExecutionSlotClear $base}catch{$blocked=$true}
@{alive=$alive.Count;blocked=$blocked}|ConvertTo-Json
""")
        self.assertEqual({'alive': 0, 'blocked': True}, result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
