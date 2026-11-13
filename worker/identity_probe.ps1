param([Parameter(Mandatory=$true)][string]$ConfigPath,[Parameter(Mandatory=$true)][string]$ExpectedConfigSha256)
$ErrorActionPreference='Stop'
function Assert-FormalStateFileAcl($Before,$After,[string]$WorkerSid){
 # Only writable state data may transfer ownership to the fixed worker. This checks
 # DACL authorization, not invariant effective access: ownership implies WRITE_DAC.
 $sidType=[Security.Principal.SecurityIdentifier]
 $beforeOwner=$Before.GetOwner($sidType).Value;$afterOwner=$After.GetOwner($sidType).Value
 if($beforeOwner -cne 'S-1-5-32-544' -or $afterOwner -cnotin @($beforeOwner,$WorkerSid)){throw 'Unexpected state owner'}
 if($Before.GetGroup($sidType).Value -cne $After.GetGroup($sidType).Value -or $Before.AreAccessRulesProtected -ne $After.AreAccessRulesProtected -or !$Before.AreAccessRulesCanonical -or !$After.AreAccessRulesCanonical){throw 'State group/protection/canonical flags changed'}
 $modify=([int64][Security.AccessControl.FileSystemRights]::Modify) -bor ([int64][Security.AccessControl.FileSystemRights]::Synchronize)
 $expected=@{'S-1-5-18'=[int64][Security.AccessControl.FileSystemRights]::FullControl;'S-1-5-32-544'=[int64][Security.AccessControl.FileSystemRights]::FullControl}
 $expected[$WorkerSid]=$modify
 foreach($acl in @($Before,$After)){
  $rights=@{};$counts=@{};$explicitAdmin=0
  foreach($rule in $acl.GetAccessRules($true,$true,$sidType)){
   $sid=$rule.IdentityReference.Value
   if(!$expected.ContainsKey($sid) -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or [int64]$rule.InheritanceFlags -ne 0 -or [int64]$rule.PropagationFlags -ne 0 -or [int64]$rule.FileSystemRights -ne $expected[$sid]){throw 'Unexpected state DACL authorization'}
   if(!$rule.IsInherited){if($sid -cne 'S-1-5-32-544'){throw 'Unexpected explicit state ACE'};$explicitAdmin++}
   if(!$counts.ContainsKey($sid)){$counts[$sid]=0;$rights[$sid]=[int64]0}
   $counts[$sid]++;$rights[$sid]=$rights[$sid] -bor [int64]$rule.FileSystemRights
  }
  if($rights.Count -ne 3 -or $counts[$WorkerSid] -ne 1 -or $counts['S-1-5-18'] -ne 1 -or $explicitAdmin -gt 1 -or $counts['S-1-5-32-544'] -ne (1+$explicitAdmin)){throw 'Unexpected state ACE count'}
  foreach($sid in $expected.Keys){if($rights[$sid] -ne $expected[$sid]){throw 'State DACL rights changed'}}
 }
}
if((Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedConfigSha256){throw 'Probe config hash mismatch'}
$c=Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
try{
 $phase='guard'
 if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'runtime_guard.ps1') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $c.runtime_guard_sha256){throw 'Probe guard hash mismatch'}
 . (Join-Path $PSScriptRoot 'runtime_guard.ps1')
 $phase='full_runtime'
 [void](Assert-FormalRuntime $PSScriptRoot $c)
 $phase='identity'
 Assert-FormalWorkerIdentity $c
 $phase='job_write'
 $protectedAcls=@{}
 foreach($path in @($c.root,$c.probe_dir,$PSScriptRoot,(Join-Path $PSScriptRoot 'runtime_guard.ps1'))){$protectedAcls[$path]=(Get-Acl -LiteralPath $path).Sddl}
 $test=Join-Path $c.probe_dir 'identity-write-probe.txt'
 $testStream=[IO.File]::Open($test,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
 try{$testBytes=[Text.Encoding]::UTF8.GetBytes('restricted worker write probe');$testStream.Write($testBytes,0,$testBytes.Length)}finally{$testStream.Dispose()}
 [IO.File]::Delete($test)
 if([IO.File]::Exists($test)){throw 'Worker could not delete its own probe'}
 $phase='actual_state_publishers'
 $io=@()
 foreach($entry in @(@('run_job.ps1',@('Publish-FormalStateFile','Write-State','Save-StageRecord')),@('run_pipeline.ps1',@('Publish-FormalStateFile','Save-State')))){
  $tokens=$null;$parseErrors=$null
  $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $entry[0]),[ref]$tokens,[ref]$parseErrors)
  if($parseErrors.Count -ne 0){throw 'Reviewed runner cannot parse'}
  foreach($name in $entry[1]){
   $definitions=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
   if($definitions.Count -ne 1){throw 'State publisher definition is not unique'}
   . ([scriptblock]::Create($definitions[0].Extent.Text))
  }
  foreach($writer in @($entry[1] | Where-Object {$_ -cne 'Publish-FormalStateFile'})){
   $JobDir=Join-Path $c.probe_dir $writer;$RunDir=$JobDir
   if(Test-Path -LiteralPath $JobDir){throw 'State publisher fixture already exists'}
   [void][IO.Directory]::CreateDirectory($JobDir)
   $request=@{stage='identity-probe'}
   $destination=Join-Path $JobDir $(if($writer -ceq 'Save-StageRecord'){'stage-identity-probe.json'}else{'state.json'})
   for($index=1;$index -le 20;$index++){
    $state=@{state=('probe-'+$index);engineering_verified=$false};$stageRecord=@{state=$state.state;engineering_verified=$false}
    & $writer
    $read=[IO.File]::ReadAllText($destination)|ConvertFrom-Json
    if($read.state -cne $state.state -or @(Get-ChildItem -LiteralPath $JobDir -Filter '*.tmp.json').Count -ne 0){throw 'State publisher content/source postcheck failed'}
   }
   $io+=@{runner=$entry[0];writer=$writer;successful_publications=20}
  }
 }
 $phase='controller_owned_state_replace'
 $controllerState=Join-Path $c.probe_dir 'controller-state-fixture.json'
 $beforeAcl=Get-Acl -LiteralPath $controllerState
 if(([IO.File]::ReadAllText($controllerState)|ConvertFrom-Json).state -cne 'controller-created-initial'){throw 'Controller fixture initial bytes changed'}
 $temporary=Join-Path $c.probe_dir 'controller-state-fixture.tmp.json'
 for($index=1;$index -le 20;$index++){
  [IO.File]::WriteAllText($temporary,('{"state":"worker-replaced-'+$index+'","engineering_verified":false}'))
  Publish-FormalStateFile $temporary $controllerState
  if(([IO.File]::ReadAllText($controllerState)|ConvertFrom-Json).state -cne ('worker-replaced-'+$index) -or [IO.File]::Exists($temporary)){throw 'Controller state replacement failed'}
  Assert-FormalStateFileAcl $beforeAcl (Get-Acl -LiteralPath $controllerState) $c.expected_worker_sid
 }
 $phase='failed_replace_preserves_previous_state'
 $target=Join-Path $c.probe_dir 'failure-preserved-state.json'
 [IO.File]::WriteAllText($target,'{"state":"previous","engineering_verified":false}')
 $before=[IO.File]::ReadAllText($target);$missing=Join-Path $c.probe_dir 'never-created-source.tmp'
 $refused=$false
 try{Publish-FormalStateFile $missing $target}catch{$refused=$true}
 if(!$refused -or [IO.File]::ReadAllText($target) -cne $before){throw 'Missing source corrupted previous state'}
 $lockedSource=Join-Path $c.probe_dir 'locked-source.tmp.json'
 [IO.File]::WriteAllText($lockedSource,'{"state":"replacement"}')
 $held=[IO.File]::Open($target,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
 $refused=$false
 try{try{Publish-FormalStateFile $lockedSource $target}catch{$refused=$true}}finally{$held.Dispose()}
 if(!$refused -or [IO.File]::ReadAllText($target) -cne $before -or ![IO.File]::Exists($lockedSource)){throw 'Locked target corrupted previous state'}
 $phase='copy_actual_runtime_file'
 $copy=Join-Path $c.probe_dir 'copied-runtime-guard.ps1'
 Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'runtime_guard.ps1') -Destination $copy
 if((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash.ToLowerInvariant() -cne $c.runtime_guard_sha256){throw 'Runtime copy hash mismatch'}
 [IO.File]::Delete($copy)
 [void](Assert-FormalRuntime $PSScriptRoot $c)
 foreach($path in $protectedAcls.Keys){if((Get-Acl -LiteralPath $path).Sddl -cne $protectedAcls[$path]){throw 'Protected parent/runtime security descriptor changed'}}
 $phase='runtime_write_expected_denied'
 $writable=$false
 try{$f=[IO.File]::Open((Join-Path $PSScriptRoot 'runtime_guard.ps1'),[IO.FileMode]::Open,[IO.FileAccess]::Write);$f.Dispose();$writable=$true}catch [UnauthorizedAccessException]{}
 if($writable){throw 'Worker can write runtime'}
 $phase='credential_read_expected_denied'
 $credentialReadable=$false
 try{$discard=[IO.File]::ReadAllText((Join-Path $c.root 'service-identity\worker-credential-v2.json'));$discard=$null;$credentialReadable=$true}catch [UnauthorizedAccessException]{}
 if($credentialReadable){throw 'Worker can read controller credential'}
 @{state='PASS';worker_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;is_admin=$false;runtime_writable=$false;credential_readable=$false;job_writable=$true;runtime_digest=$c.runtime_digest;io_publishers=$io;controller_owned_replacements=20;state_dacl_authorization_unchanged=$true;state_owner_transfer_allowed=$true;protected_parent_runtime_acls_unchanged=$true;missing_source_and_locked_target_preserved=2;runtime_copy_verified=$true;engineering_verified=$false} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $c.probe_dir 'identity-probe.json') -Encoding UTF8
}catch{
 @{state='FAIL';phase=$phase;error=$_.Exception.Message;error_type=$_.Exception.GetType().FullName;stack=$_.ScriptStackTrace;position=$_.InvocationInfo.PositionMessage;engineering_verified=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $c.probe_dir 'identity-probe.json') -Encoding UTF8
 throw
}
