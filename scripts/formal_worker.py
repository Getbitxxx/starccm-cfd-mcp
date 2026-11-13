"""Reviewed formal worker installation and admission. No STAR process is started here."""
import base64
import hashlib
import json
import re
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

DEPLOY_REVIEW = 'review/formal-worker-deployment-active.json'

def deployment_review(backend):
    from backend import PLUGIN, sha256
    pointer_path = PLUGIN / DEPLOY_REVIEW
    pointer = json.loads(pointer_path.read_text(encoding='utf-8-sig'))
    if pointer.get('schema_version') != 1 or not re.fullmatch(r'review/formal-worker-deployment-[A-Za-z0-9_-]+\.json',str(pointer.get('review_ref',''))):
        raise ValueError('Invalid formal deployment approval pointer')
    path = PLUGIN / pointer['review_ref']
    if path.is_symlink() or not path.resolve().is_relative_to((PLUGIN/'review').resolve()) or sha256(path) != pointer.get('review_sha256'):
        raise ValueError('Formal deployment approval pointer hash mismatch')
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    if record.get('approved_for_deployment') is not True or record.get('independent') is not True:
        raise ValueError('Formal worker has no independent deployment approval')
    manifest = backend.runtime_manifest()
    pins = runtime_pins(backend)
    for name, value in pins.items():
        if record.get(name) != value:
            raise ValueError('Deployment approval differs: '+name)
    support = ['scripts/backend.py', 'scripts/formal_worker.py',
               'scripts/security/provision_restricted_worker.ps1', 'scripts/security/grant_batch_logon.ps1']
    if record.get('support_hashes') != {name: sha256(PLUGIN/name) for name in support}:
        raise ValueError('Formal deployment support bytes differ from review')
    if record.get('runtime_files') != manifest['files'] or record.get('remote_root') != str(Path(backend.remote_root)).replace('\\\\', '\\'):
        raise ValueError('Formal deployment file list/root differs from review')
    if not re.fullmatch(r'S-1-5-21-\d+-\d+-\d+-\d+', str(record.get('expected_worker_sid', ''))):
        raise ValueError('Deployment review must pin the actual restricted worker SID')
    return dict(record, deployment_review_sha256=sha256(path))

def runtime_pins(backend):
    from backend import PLUGIN, sha256, runtime_manifest_bytes
    return {'runtime_digest': backend.runtime_digest(),
            'runtime_manifest_sha256': hashlib.sha256(runtime_manifest_bytes(backend.runtime_manifest())).hexdigest(),
            'runtime_guard_sha256': sha256(PLUGIN/'worker/runtime_guard.ps1'),
            'runner_sha256': sha256(PLUGIN/'worker/run_job.ps1'),
            'pipeline_runner_sha256': sha256(PLUGIN/'worker/run_pipeline.ps1')}

def request_pins(backend, require_ready=True):
    approval = deployment_review(backend)
    if require_ready and health(backend).get('worker_deployed') is not True:
        raise ValueError('Formal deployment identity/ACL/runtime receipt is not ready')
    return dict(runtime_pins(backend), expected_worker_sid=approval['expected_worker_sid'],
                deployment_review_sha256=approval['deployment_review_sha256'], health_watchdog_seconds=900)

def credential_registration_ps(backend):
    from backend import ps_quote
    path=backend.remote_root+'\\service-identity\\worker-credential-v2.json'
    return f'''
function Register-FormalTask([string]$TaskName,$Action,$Settings,[string]$ExpectedSid){{
 $u=Get-LocalUser -Name CodexCFDWorker
 $c=Get-Content -LiteralPath {ps_quote(path)} -Raw -Encoding UTF8 | ConvertFrom-Json
 if($u.Description -cne 'Codex CFD scoped restricted worker v1' -or $u.SID.Value -cne $ExpectedSid -or $c.worker_sid -cne $ExpectedSid -or $c.password_last_set -cne $u.PasswordLastSet.ToUniversalTime().ToString('o')){{throw 'Managed task credential identity/stamp mismatch'}}
 if(@(Get-LocalGroupMember -SID 'S-1-5-32-544').SID.Value -contains $ExpectedSid){{throw 'Managed worker must not be administrator'}}
 if($c.format_version -ne 2 -or $c.protection_scope -cne 'LocalMachine'){{throw 'Managed credential format mismatch'}}
 Add-Type -AssemblyName System.Security
 $plainBytes=[Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($c.protected_password),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
 try{{
  $taskSecret=[Text.Encoding]::Unicode.GetString($plainBytes)
  Register-ScheduledTask -TaskName $TaskName -Action $Action -User ($env:COMPUTERNAME+'\\CodexCFDWorker') -Password $taskSecret -RunLevel Limited -Settings $Settings | Out-Null
 }}finally{{[Array]::Clear($plainBytes,0,$plainBytes.Length);$taskSecret=$null}}
}}
'''

def guarded_submission(backend, script):
    """Serialize controllers and refuse occupancy before creating a request directory."""
    from backend import ps_quote
    marker = 'New-Item -ItemType Directory -Path '
    index = script.index(marker)
    check = f'''
$slot=New-Object Threading.Mutex($false,'Global\\CodexCFD2502Worker')
$slotHeld=$false
try{{
 try{{$slotHeld=$slot.WaitOne(0)}}catch [Threading.AbandonedMutexException]{{$slotHeld=$true}}
 if(!$slotHeld){{throw 'REJECTED_BUSY: worker mutex owns the slot'}}
}}finally{{if($slotHeld){{$slot.ReleaseMutex()}};$slot.Dispose()}}
foreach($task in Get-ScheduledTask){{if($task.TaskName -like 'CodexCFD-*' -and $task.State -eq 'Running'){{throw 'REJECTED_BUSY: managed task owns the slot'}}}}
if(@(Get-CimInstance Win32_Process | Where-Object {{$_.Name -match '(?i)^(starccm|star-ccm|ccmserver)'}}).Count -gt 0){{throw 'REJECTED_BUSY: STAR process already active'}}
foreach($container in @('jobs','runs')){{
 $base=Join-Path {ps_quote(backend.remote_root)} $container
 foreach($dir in @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue)){{
  $statePath=Join-Path $dir.FullName 'state.json'
  if(Test-Path -LiteralPath $statePath){{
   $s=Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
   if($s.state -in @('PENDING','STARTING','RUNNING')){{throw 'REJECTED_BUSY: durable active request owns the slot'}}
  }}
 }}
}}
'''
    body = script[:index]+check+script[index:]
    return credential_registration_ps(backend)+"""$admission=New-Object Threading.Mutex($false,'Global\\CodexCFD2502Admission')
$admitted=$false
try{
 try{$admitted=$admission.WaitOne(0)}catch [Threading.AbandonedMutexException]{$admitted=$true}
 if(!$admitted){throw 'REJECTED_BUSY: concurrent admission'}
"""+body+"\n}finally{if($admitted){$admission.ReleaseMutex()};$admission.Dispose()}\n"

def deploy(backend):
    from backend import PLUGIN, ps_quote, runtime_manifest_bytes, sha256
    approval = deployment_review(backend)
    pins = request_pins(backend, require_ready=False)
    digest = pins['runtime_digest']
    installation = digest[:16]+'-'+pins['deployment_review_sha256'][:16]
    runtime = backend.remote_root+'\\runtime\\'+digest[:16]
    staging = backend.remote_root+'\\runtime\\staging-'+uuid.uuid4().hex
    security = backend.remote_root+'\\deployment-security\\'+installation
    probe_dir = backend.remote_root+'\\deployment-probes\\'+installation
    manifest = backend.runtime_manifest()
    remote_dirs = [security, probe_dir, backend.remote_root+'\\runtime', backend.remote_root+'\\jobs', backend.remote_root+'\\runs', backend.remote_root+'\\library']
    backend.ps("if(@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'CodexCFD-*' -and $_.State -eq 'Running'}).Count -gt 0){throw 'Managed worker running; deployment refused'};if(@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '(?i)^(starccm|star-ccm|ccmserver)'}).Count -gt 0){throw 'STAR running; deployment refused'}")
    backend.ps('New-Item -ItemType Directory -Force '+','.join(ps_quote(x) for x in remote_dirs)+' | Out-Null')
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp)/'runtime.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for entry in manifest['files']:
                bundle.write(PLUGIN/entry['source'], entry['path'])
            bundle.writestr('runtime-manifest.json', runtime_manifest_bytes(manifest))
        remote_archive = security+'\\'+uuid.uuid4().hex+'.zip'
        backend.scp(archive, remote_archive)
        payload = base64.b64encode(json.dumps(pins).encode()).decode()
        backend.ps(f'''
if((Get-FileHash -LiteralPath {ps_quote(remote_archive)} -Algorithm SHA256).Hash.ToLowerInvariant() -cne {ps_quote(sha256(archive))}){{throw 'Runtime archive hash mismatch'}}
Expand-Archive -LiteralPath {ps_quote(remote_archive)} -DestinationPath {ps_quote(staging)}
$q=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({ps_quote(payload)})) | ConvertFrom-Json
$guard=Join-Path {ps_quote(staging)} 'runtime_guard.ps1'
if((Get-FileHash -LiteralPath $guard -Algorithm SHA256).Hash.ToLowerInvariant() -cne $q.runtime_guard_sha256){{throw 'Staging guard hash mismatch'}}
. $guard
[void](Assert-FormalRuntime {ps_quote(staging)} $q $false)
if(Test-Path -LiteralPath {ps_quote(runtime)}){{
 [void](Assert-FormalRuntime {ps_quote(runtime)} $q)
 Remove-Item -LiteralPath {ps_quote(staging)} -Recurse -Force
}}else{{Move-Item -LiteralPath {ps_quote(staging)} -Destination {ps_quote(runtime)}}}
Remove-Item -LiteralPath {ps_quote(remote_archive)}
@{{runtime_verified=$true;atomic_publication=$true}} | ConvertTo-Json
''', timeout=60)
        for name in ('provision_restricted_worker.ps1','grant_batch_logon.ps1'):
            backend.scp(PLUGIN/'scripts/security'/name, security+'\\'+name)
        config = dict(pins, mode='formal-worker-deployment', root=backend.remote_root.replace('\\\\','\\'),
                      runtime_path=runtime, probe_dir=probe_dir,
                      bootstrap_hashes={name: sha256(PLUGIN/'scripts/security'/name) for name in ('provision_restricted_worker.ps1','grant_batch_logon.ps1')})
        path = Path(temp)/'deployment-config.json'
        path.write_text(json.dumps(config, sort_keys=True, indent=2)+'\n')
        config_sha = sha256(path)
        remote_config = security+'\\deployment-config.json'
        backend.scp(path, remote_config)
    result = backend.ps(f"& {ps_quote(security+'\\provision_restricted_worker.ps1')} -ConfigPath {ps_quote(remote_config)} -ExpectedConfigSha256 {ps_quote(config_sha)}", timeout=60)
    task = 'CodexCFD-Identity-'+installation
    probe_path = runtime+'\\identity_probe.ps1'
    probe = backend.ps(credential_registration_ps(backend)+f'''
$args='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+{ps_quote(probe_path)}+'" -ConfigPath "'+{ps_quote(remote_config)}+'" -ExpectedConfigSha256 "'+{ps_quote(config_sha)}+'"'
$action=New-ScheduledTaskAction -Execute "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Argument $args
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-FormalTask -TaskName {ps_quote(task)} -Action $action -Settings $settings -ExpectedSid {ps_quote(pins['expected_worker_sid'])}
Start-ScheduledTask -TaskName {ps_quote(task)}
@{{state='IDENTITY_PROBE_STARTED';worker_deployed=$false;task={ps_quote(task)}}} | ConvertTo-Json
''')
    # Polling bounds the transport call only. It never kills or retries a worker.
    receipt_path = security+'\\deployment-receipt.json'
    probe_file = probe_dir+'\\identity-probe.json'
    receipt = None
    for _ in range(15):
        receipt = backend.ps(f'''
$task=Get-ScheduledTask -TaskName {ps_quote(task)}
$info=Get-ScheduledTaskInfo -TaskName {ps_quote(task)}
$path={ps_quote(probe_file)}
if($task.State -eq 'Running' -or !(Test-Path -LiteralPath $path)){{@{{pending=$true}} | ConvertTo-Json;exit 0}}
$p=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
if($info.LastTaskResult -ne 0 -or $task.Settings.ExecutionTimeLimit -ne 'PT0S' -or $p.state -cne 'PASS' -or $p.worker_sid -cne {ps_quote(pins['expected_worker_sid'])} -or $p.runtime_digest -cne {ps_quote(digest)} -or $p.is_admin -ne $false -or $p.runtime_writable -ne $false -or $p.job_writable -ne $true -or $p.credential_readable -ne $false){{throw 'Formal identity/ACL probe failed'}}
$acl=Get-Acl -LiteralPath {ps_quote(probe_dir)}
$sid=New-Object Security.Principal.SecurityIdentifier({ps_quote(pins['expected_worker_sid'])})
$acl.PurgeAccessRules($sid)
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'ReadAndExecute',[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[Security.AccessControl.PropagationFlags]::None,'Allow')))
Set-Acl -LiteralPath {ps_quote(probe_dir)} -AclObject $acl
$taskXml=Export-ScheduledTask -TaskName {ps_quote(task)}
$taskHash=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($taskXml)))).Replace('-','').ToLowerInvariant()
$credentialPath=Join-Path {ps_quote(backend.remote_root)} 'service-identity\\worker-credential-v2.json'
$receipt=@{{schema_version=1;approved_review_sha256={ps_quote(pins['deployment_review_sha256'])};runtime_digest={ps_quote(digest)};runtime_manifest_sha256={ps_quote(pins['runtime_manifest_sha256'])};config_path={ps_quote(remote_config)};config_sha256={ps_quote(config_sha)};probe_path=$path;probe_sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();worker_sid=$p.worker_sid;task={ps_quote(task)};task_definition_sha256=$taskHash;credential_sha256=(Get-FileHash -LiteralPath $credentialPath -Algorithm SHA256).Hash.ToLowerInvariant();execution_time_limit=$task.Settings.ExecutionTimeLimit;engineering_verified=$false}}
$receipt | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath {ps_quote(receipt_path)} -Encoding UTF8
$receipt | ConvertTo-Json -Depth 6
''')
        if not receipt.get('pending'):
            break
        time.sleep(1)
    return dict(probe, provision=result, deployment_receipt=receipt, runtime_path=runtime, **pins)

def health(backend):
    from backend import ps_quote
    pins = runtime_pins(backend)
    runtime = backend.remote_root+'\\runtime\\'+pins['runtime_digest'][:16]
    try:
        approval = deployment_review(backend)
        request = request_pins(backend, require_ready=False)
    except (ValueError, FileNotFoundError) as exc:
        approval = None
        request = pins
        reason = str(exc)
    else:
        reason = None
    payload = base64.b64encode(json.dumps(request).encode()).decode()
    installation=pins['runtime_digest'][:16]+'-'+str(request.get('deployment_review_sha256','unapproved'))[:16]
    probe = backend.remote_root+'\\deployment-probes\\'+installation+'\\identity-probe.json'
    receipt = backend.remote_root+'\\deployment-security\\'+installation+'\\deployment-receipt.json'
    result = backend.ps(f'''
$version=& {ps_quote(backend.config['star_launcher'])} -version 2>&1 | Out-String
$sys=Get-CimInstance Win32_ComputerSystem
$runtimeVerified=$false;$probeResult=$null;$failure={ps_quote(reason or '')}
try{{
 $q=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String({ps_quote(payload)})) | ConvertFrom-Json
 $guard=Join-Path {ps_quote(runtime)} 'runtime_guard.ps1'
 if((Get-FileHash -LiteralPath $guard -Algorithm SHA256).Hash.ToLowerInvariant() -cne $q.runtime_guard_sha256){{throw 'Runtime guard hash mismatch'}}
 . $guard
 [void](Assert-FormalRuntime {ps_quote(runtime)} $q)
 $runtimeVerified=$true
 $receipt=Get-Content -LiteralPath {ps_quote(receipt)} -Raw -Encoding UTF8 | ConvertFrom-Json
 if($receipt.approved_review_sha256 -cne $q.deployment_review_sha256 -or $receipt.runtime_manifest_sha256 -cne $q.runtime_manifest_sha256 -or $receipt.runtime_digest -cne $q.runtime_digest -or $receipt.worker_sid -cne $q.expected_worker_sid){{throw 'Deployment receipt identity mismatch'}}
 if((Get-FileHash -LiteralPath {ps_quote(probe)} -Algorithm SHA256).Hash.ToLowerInvariant() -cne $receipt.probe_sha256 -or (Get-FileHash -LiteralPath $receipt.config_path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $receipt.config_sha256){{throw 'Deployment probe/config hash mismatch'}}
 $credentialPath=Join-Path {ps_quote(backend.remote_root)} 'service-identity\\worker-credential-v2.json'
 if((Get-FileHash -LiteralPath $credentialPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $receipt.credential_sha256){{throw 'Deployment credential hash mismatch'}}
 $credential=Get-Content -LiteralPath $credentialPath -Raw -Encoding UTF8 | ConvertFrom-Json
 if($credential.worker_sid -cne $q.expected_worker_sid -or $credential.password_last_set -cne (Get-LocalUser -Name CodexCFDWorker).PasswordLastSet.ToUniversalTime().ToString('o')){{throw 'Deployment credential identity/stamp mismatch'}}
 $task=Get-ScheduledTask -TaskName $receipt.task
 $taskXml=Export-ScheduledTask -TaskName $receipt.task
 $taskHash=([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($taskXml)))).Replace('-','').ToLowerInvariant()
 if($taskHash -cne $receipt.task_definition_sha256){{throw 'Deployment task definition hash mismatch'}}
 $sid=(New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
 if($task.Settings.ExecutionTimeLimit -ne 'PT0S' -or $sid -cne $q.expected_worker_sid -or (Get-ScheduledTaskInfo -TaskName $receipt.task).LastTaskResult -ne 0){{throw 'Deployment task identity mismatch'}}
 $probeResult=Get-Content -LiteralPath {ps_quote(probe)} -Raw -Encoding UTF8 | ConvertFrom-Json
}}catch{{$failure=$_.Exception.Message}}
$ready=$runtimeVerified -and !$failure -and $probeResult.state -ceq 'PASS' -and $probeResult.worker_sid -ceq $q.expected_worker_sid -and $probeResult.runtime_digest -ceq $q.runtime_digest -and $probeResult.is_admin -eq $false -and $probeResult.runtime_writable -eq $false -and $probeResult.job_writable -eq $true -and $probeResult.credential_readable -eq $false
@{{host=$env:COMPUTERNAME;user=[Environment]::UserName;star_version=$version.Trim();logical_processors=$sys.NumberOfLogicalProcessors;memory_gb=[math]::Round($sys.TotalPhysicalMemory/1GB,1);scheduler=(Get-Service Schedule).Status.ToString();worker_deployed=$ready;runtime_verified=$runtimeVerified;identity_probe=$probeResult;deployment_error=$failure}} | ConvertTo-Json -Depth 8
''', timeout=60)
    if approval is None:
        result['worker_deployed'] = False
        result['deployment_error'] = reason
    result.update(runtime_digest=pins['runtime_digest'], max_parallel_jobs=1, workflows=backend.list_workflows())
    return result
