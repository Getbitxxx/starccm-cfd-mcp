param([Parameter(Mandatory=$true)][string]$JobDir,[Parameter(Mandatory=$true)][string]$ExpectedRequestSha256)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$proc=$null
$locked=$false
$mutex=$null
$appliedEnv=@{}
$previousEnv=@{}
$state=@{engineering_verified=$false;state='STARTING';started_at=[DateTime]::UtcNow.ToString('o');job_dir=$JobDir}
function Publish-FormalStateFile([string]$Temporary,[string]$Destination) {
    # Never delete the previous state before attempting the OS atomic replacement.
    if([IO.File]::Exists($Destination)){
        [IO.File]::Replace($Temporary,$Destination,[System.Management.Automation.Language.NullString]::Value)
    }else{
        [IO.File]::Move($Temporary,$Destination)
    }
}
function Write-State {
    $state.updated_at=[DateTime]::UtcNow.ToString('o')
    $temporary=Join-Path $JobDir 'state.tmp.json'
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Publish-FormalStateFile $temporary (Join-Path $JobDir 'state.json')
}
# Per-stage record. AGENTS.md section 6 requires every runner to write stage-<name>.json on success
# AND on failure, with per-macro ran/status/exit code and not_run for macros a failure prevented from
# running. Independent review round 7 (blocker 2) found this runner had none, so citability of its
# records could only fall back to the run-level state and one failed stage invalidated every other
# stage of the same run. Written into the job directory, beside state.json.
function Save-StageRecord {
    if($null -eq $stageRecord){return}
    # Bind every produced required artifact, including JSON certificates, to this runner record.
    $stageRecord.artifact_hashes=@{}
    foreach($name in @($stageRecord.artifacts)){
        $artifact=Join-Path $JobDir $name
        if(Test-Path -LiteralPath $artifact -PathType Leaf){
            $stageRecord.artifact_hashes[$name]=(Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $stageRecord.updated_at=[DateTime]::UtcNow.ToString('o')
    $temporary=Join-Path $JobDir ('stage-'+$request.stage+'.tmp.json')
    $stageRecord | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Publish-FormalStateFile $temporary (Join-Path $JobDir ('stage-'+$request.stage+'.json'))
}
function Stop-OwnedProcess {
    if($null -ne $proc){Stop-FormalProcessTree $proc $JobDir}
}
try {
    $requestPath=Join-Path $JobDir 'request.json'
    if($ExpectedRequestSha256 -cnotmatch '^[a-f0-9]{64}$' -or (Get-FileHash -LiteralPath $requestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedRequestSha256){throw 'Scheduled request hash mismatch'}
    $request=Get-Content -LiteralPath (Join-Path $JobDir 'request.json') -Raw | ConvertFrom-Json
    if(!$request.runner_sha256){throw 'No reviewed runner hash pinned in this request'}
    if((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $request.runner_sha256){throw 'Runner hash mismatch'}
    if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'runtime_guard.ps1') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $request.runtime_guard_sha256){throw 'Runtime guard hash mismatch'}
    . (Join-Path $PSScriptRoot 'runtime_guard.ps1')
    [void](Assert-FormalRuntime $PSScriptRoot $request)
    Assert-FormalWorkerIdentity $request
    $state.wall_clock_stop_enabled=$false
    $state.timeout_seconds_observed=$request.timeout_seconds
    $state.runtime_manifest_sha256=$request.runtime_manifest_sha256
    $state.job_id=$request.job_id
    $state.workflow_id=$request.workflow_id
    $state.stage=$request.stage
    $state.request_digest=$request.request_digest
    $state.runtime_digest=$request.runtime_digest
    Write-State
    $mutex=New-Object System.Threading.Mutex($false,'Global\CodexCFD2502Worker')
    try{$locked=$mutex.WaitOne(0)}catch [System.Threading.AbandonedMutexException]{$locked=$true}
    if(!$locked){$state.state='REJECTED_BUSY';$state.error='Another Codex CFD job owns the execution slot';Write-State;return}
    Assert-FormalExecutionSlotClear $request.remote_root
    Assert-FormalLauncherPath $request.star_launcher
    if($request.workflow_id -cnotmatch '^[a-z][a-z0-9-]{0,63}$' -or $request.stage -cnotmatch '^[a-z][a-z0-9_-]{0,63}$'){
        throw 'Invalid workflow or stage identifier'
    }
    $manifestPath=Join-Path $PSScriptRoot ('workflows\'+$request.workflow_id+'\manifest.json')
    $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($manifest.schema_version -ne 1 -or $manifest.id -cne $request.workflow_id){throw 'Invalid workflow manifest'}
    $stage=$manifest.stages.PSObject.Properties[$request.stage].Value
    if($null -eq $stage){throw 'Unsupported stage'}
    if($stage.postcheck -notin 'artifacts_v1','preflight_v1','smoke_100_v1','external_domain_v1','cylindrical_domain_v1','rotor360_v1','boundary_values_v1','mrf_interfaces_v1','mrf_mesh_generated_v1','mrf_split_mesh_generated_v1'){throw 'Unsupported postcheck'}
    # The stage record exists from here on: a refused job (hash mismatch, missing macro, wrong input)
    # is also a stage outcome, and §6 says a stage record is written on success AND failure. The
    # independent review found this record was created after every refusal check, so those paths left
    # only state.json.
    $stageRecord=@{schema_version=1;stage=$request.stage;state='RUNNING'
        started_at=[DateTime]::UtcNow.ToString('o');job_dir=$JobDir
        macro_outputs=@();artifacts=@($stage.required);macros=@()}
    $macroRecords=@()
    $currentMacroIndex=$null
    foreach($macro in $stage.macros){
        if($macro -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.java$'){throw 'Invalid macro filename'}
    }
    # STAGE PARAMETERS (R11). A manifest stage may declare the environment its macros read; this block is
    # the only place those values are applied. Two rules keep it fail-closed rather than a way to
    # reconfigure a worker: only names the stage itself declared are accepted (the caller cannot invent
    # one), and process-critical names are refused outright (a parameter can never redirect the
    # interpreter, the loader, the temp directory or the profile this worker runs under). The values are
    # set for this PowerShell process only, so only its own child STAR processes inherit them, and they
    # are removed in the finally block so they cannot leak into the next stage.
    $declaredEnv=@{}
    $declaredProperty=$stage.PSObject.Properties['parameters']
    if($null -ne $declaredProperty -and $null -ne $declaredProperty.Value){
        $declaredEnvProperty=$declaredProperty.Value.PSObject.Properties['env']
        if($null -ne $declaredEnvProperty -and $null -ne $declaredEnvProperty.Value){
            foreach($entry in @($declaredEnvProperty.Value)){if($entry.name){$declaredEnv[[string]$entry.name]=$true}}
        }
    }
    $deniedParams=@('PATH','PATHEXT','SYSTEMROOT','SYSTEMDRIVE','WINDIR','COMSPEC','TEMP','TMP','USERPROFILE',
              'HOMEPATH','HOMEDRIVE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMDATA','PSMODULEPATH',
              'COMPUTERNAME','USERNAME','USERDOMAIN','LOGONSERVER','NUMBER_OF_PROCESSORS',
              'PROCESSOR_ARCHITECTURE','PYTHONPATH','PYTHONHOME','JAVA_HOME')
    $appliedEnv=@{}
    $appliedParams=@{}
    if($null -ne $request.env){
        if(@($request.env.PSObject.Properties).Count -gt 64){throw 'Too many stage parameters'}
        foreach($property in $request.env.PSObject.Properties){
            $name=[string]$property.Name
            if($name -cnotmatch '^[A-Z][A-Z0-9_]{0,63}$'){throw "Invalid stage parameter name: $name"}
            if($deniedParams -contains $name){throw "Refusing a process-critical stage parameter: $name"}
            if($declaredEnv.Count -eq 0){throw "Stage declares no parameters, so $name cannot be applied"}
            if(!$declaredEnv.ContainsKey($name)){throw "Stage does not declare the parameter $name"}
            $value=[string]$property.Value
            if($value -cnotmatch '^[ -~]{0,255}$'){throw "Stage parameter $name is not printable ASCII"}
            $appliedEnv[$name]=$value
            $appliedParams[$name]=$value
        }
    }
# New contracts are pinned by the reviewed pipeline hash; legacy requests keep their original caps.
$reserveGB=16.0
if($null -ne $request.resource_contract){
 $r=$request.resource_contract
 $fields=@('schema_version','logical_cores','memory_gb','memory_reserve_gb','outer_timeout_seconds')
 $actual=@($r.PSObject.Properties.Name)
 foreach($field in $fields){$value=$r.$field;if($value -is [bool] -or $value -is [string] -or $null -eq $value -or $value -isnot [ValueType]){throw 'Resource contract fields must be JSON numbers'}}
 if($actual.Count -ne 5 -or @($actual | Where-Object {$_ -notin $fields}).Count -gt 0 -or $r.schema_version -ne 1){throw 'Invalid resource contract schema'}
 $cores=[double]$r.logical_cores
 $mem=[double]$r.memory_gb
 if([double]::IsNaN($cores) -or [double]::IsInfinity($cores) -or $cores -ne [math]::Floor($cores) -or $cores -lt 1 -or $cores -gt 64){throw 'Invalid contract core budget'}
 if([double]::IsNaN($mem) -or [double]::IsInfinity($mem) -or $mem -lt 1 -or $mem -gt 240){throw 'Invalid contract memory budget'}
 foreach($field in @('schema_version','logical_cores','outer_timeout_seconds')){if($r.$field -isnot [int] -and $r.$field -isnot [long]){throw 'Resource contract integer fields must be JSON integers'}}
 $outer=[double]$r.outer_timeout_seconds
 if([double]::IsNaN($outer) -or [double]::IsInfinity($outer) -or $outer -ne [math]::Floor($outer) -or $outer -lt 60 -or $outer -gt 2678400){Write-Verbose 'Outer time declaration is observational'}
 $reserveGB=[double]$r.memory_reserve_gb
 if([double]::IsNaN($reserveGB) -or [double]::IsInfinity($reserveGB) -or $reserveGB -lt 16 -or $reserveGB -gt 240){throw 'Invalid memory reserve'}
 if([double]$r.logical_cores -ne [double]$request.logical_cores -or [double]$r.memory_gb -ne [double]$request.memory_gb){throw 'Resource contract differs from requested configuration'}
 # Task and outer time declarations are observations, never admission or stop gates.
}

    if(!(Test-Path -LiteralPath $request.star_launcher -PathType Leaf)){throw 'STAR launcher missing'}
    $source=(Resolve-Path -LiteralPath $request.source_path).Path
    $root=[IO.Path]::GetFullPath($request.remote_root).TrimEnd([char]92)+[char]92
    if(!$source.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Source outside configured root'}
    $sourceHash=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    if($sourceHash -ne $request.source_sha256){throw 'Source hash changed after submission'}
    $state.source_sha256=$sourceHash
    Copy-Item -LiteralPath $source -Destination (Join-Path $JobDir 'input.sim')
    if($null -ne $request.case_spec){
        $request.case_spec | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath (Join-Path $JobDir 'case_spec.json') -Encoding UTF8
    }
    foreach($macro in $stage.macros){Copy-Item -LiteralPath (Join-Path "$PSScriptRoot\macros" $macro) -Destination $JobDir}
    # Reviewed-artifact binding. This path used to record runtime_digest and never compare it, so
    # edited macro bytes could be executed while request.json and state.json still named the
    # reviewed digest. These checks make the rule executable: the deployed runtime directory, the
    # runner's own bytes, every macro the stage runs, and the reviewed input hash are all
    # recomputed here, and the job refuses to start on any mismatch. The pinned hashes come from an
    # approved independent review record that scripts/backend.py matched before submission.
    if(!$request.runtime_digest -or $request.runtime_digest.Substring(0,16) -cne (Split-Path -Leaf $PSScriptRoot)){
        throw 'Runtime digest mismatch: deployed runtime is not the reviewed runtime'
    }
    # Hash and compare in ONE statement, so no later line can overwrite an intermediate variable and make
    # the comparison vacuously true (R12 review, blockers W6b/W12). $selfHash below is only for the state
    # record; the guard does not depend on it.
    $selfHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    foreach($macro in $stage.macros){
        $pinned=$null
        if($request.macro_hashes){$prop=$request.macro_hashes.PSObject.Properties[$macro];if($prop){$pinned=[string]$prop.Value}}
        if(!$pinned){throw "No reviewed hash pinned for macro $macro"}
        $macroFile=Join-Path $PSScriptRoot ('macros\'+$macro)
        if(!(Test-Path -LiteralPath $macroFile)){throw "Missing macro $macro"}
        if((Get-FileHash -LiteralPath $macroFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pinned){
            throw "Macro hash mismatch: $macro"
        }
    }
    if((Get-FileHash -LiteralPath (Join-Path $JobDir 'input.sim') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sourceHash){throw 'Staged input copy hash mismatch'}
    if($request.input_sha256 -and $sourceHash -ne $request.input_sha256){
        throw 'Input differs from the reviewed input hash'
    }
    # ②R14: STAGE FILE HANDOVER, verified by THIS layer before STAR starts. run_pipeline.ps1 already refused a
    # missing file / a failed source stage; here every inherited file is re-hashed against the bound value, copied
    # into the job directory the macro will read (the audit reads the session directory), and the copy is re-hashed
    # so the bytes the macro sees are the bytes that were bound. Any mismatch refuses the stage outright - the
    # audit must never be handed a substituted or truncated record.
    $inheritedRecords=@()
    if($null -ne $request.inherited_inputs){
        foreach($entry in @($request.inherited_inputs)){
            $inheritedName=[string]$entry.name
            if($inheritedName -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'){throw "Invalid inherited input name: $inheritedName"}
            # The copy below uses -Force into the job directory, so an inherited name that collides with this
            # stage's OWN staged files would overwrite them AFTER their hashes were verified. Refuse the
            # collision outright rather than relying on the pipeline layer to have caught it.
            $reservedNames=@('input.sim','case_spec.json','state.json','state.tmp.json','request.json','cancel.request')
            foreach($name in @($stage.macros)){$reservedNames+=[string]$name}
            foreach($name in @($stage.required)){if($name){$reservedNames+=[string]$name}}
            if($stage.output_sim){$reservedNames+=[string]$stage.output_sim}
            foreach($name in @($stage.macro_outputs)){if($name){$reservedNames+=[string]$name}}
            if($reservedNames -contains $inheritedName){throw "Inherited input would overwrite this stage's own file: $inheritedName"}
            $inheritedPinned=([string]$entry.source_sha256).ToLowerInvariant()
            if($inheritedPinned -cnotmatch '^[0-9a-f]{64}$'){throw "Inherited input has no usable bound hash: $inheritedName"}
            if(!(Test-Path -LiteralPath ([string]$entry.source_path) -PathType Leaf)){throw "Inherited input source is missing: $inheritedName"}
            $inheritedSource=(Resolve-Path -LiteralPath ([string]$entry.source_path)).Path
            if(!$inheritedSource.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Inherited input outside configured root'}
            $inheritedActual=(Get-FileHash -LiteralPath $inheritedSource -Algorithm SHA256).Hash.ToLowerInvariant()
            if($inheritedActual -ne $inheritedPinned){throw "Inherited input hash mismatch: $inheritedName"}
            $inheritedDest=Join-Path $JobDir $inheritedName
            if($inheritedSource -ne $inheritedDest){Copy-Item -LiteralPath $inheritedSource -Destination $inheritedDest -Force}
            $inheritedCopy=(Get-FileHash -LiteralPath $inheritedDest -Algorithm SHA256).Hash.ToLowerInvariant()
            if($inheritedCopy -ne $inheritedPinned){throw "Inherited input copy hash mismatch: $inheritedName"}
            $inheritedRecords+=[pscustomobject]@{name=$inheritedName;source_stage=[string]$entry.source_stage
                source_path=$inheritedSource;source_sha256=$inheritedActual;staged_sha256=$inheritedCopy;verified=$true}
        }
    }
    if($inheritedRecords.Count -gt 0){$stageRecord.inherited_inputs=$inheritedRecords}
    $state.runtime_digest_verified=$true
    $state.runner_sha256=$selfHash
    $state.review_sha256=$request.review_sha256
    $state.review_ref=$request.review_ref
    $state.macro_hashes=$request.macro_hashes
    Write-State
    # ONE STAR PROCESS PER MACRO. Round 6 measured that `starccm+ -batch A,B,C` keeps executing later
    # macros after an earlier one throws, so a batched stage could leave a self-consistent "ok" record
    # from a macro that ran after the failure. Batching is kept only at the stage/review layer; a
    # multi-macro stage must therefore declare one macro_outputs entry per macro (a .sim file or null),
    # or the only way to run it would be the batched process this refuses. scripts/stage_plan.py holds
    # the same rule for the submitting side.
    $macroList=@($stage.macros)
    $logicalCores=if($request.logical_cores){[int]$request.logical_cores}else{1}
    if($logicalCores -lt 1 -or $logicalCores -gt 64){throw 'Invalid logical core budget'}
    $availableCores=(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
    if($logicalCores -gt $availableCores){throw 'Requested cores unavailable; no fallback'}
    if($null -ne $request.resource_contract){
     $request | Add-Member -NotePropertyName min_free_memory_gb -NotePropertyValue $reserveGB -Force
     if(!$request.min_free_disk_gb){$request | Add-Member -NotePropertyName min_free_disk_gb -NotePropertyValue 20 -Force}
     $freeGb=(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB
     if($freeGb -lt ([double]$request.memory_gb+$reserveGB)){throw 'Declared memory plus reserve unavailable'}
     $state.resource_contract=$request.resource_contract
    }
    $outputList=@()
    if($null -ne $stage.macro_outputs){
        if(@($stage.macro_outputs).Count -ne $macroList.Count){throw 'macro_outputs length mismatch'}
        $outputList=@($stage.macro_outputs)
    } elseif($macroList.Count -eq 1){
        $outputList=@($stage.output_sim)
    } else {
        throw "Stage runs $($macroList.Count) macros and must declare one macro_outputs entry per macro"
    }
    # The chain is known now; record it in the stage record created above.
    $stageRecord.macro_outputs=$outputList
    # The legacy macros have optional time inputs. A formal worker always disables them,
    # including inherited values, and restores the parent environment at the stage boundary.
    $appliedEnv['EXTERNAL_FLOW_BUDGET_SECONDS']='0'
    $appliedEnv['AIR3B_SEGMENT_SECONDS']='0'
    $state.wall_clock_policy_env=@{EXTERNAL_FLOW_BUDGET_SECONDS='0';AIR3B_SEGMENT_SECONDS='0'}
    if($appliedEnv.Count -gt 0){
        # FORMAL_ENV_APPLY: preserve the parent value; every exit restores it in finally.
        foreach($name in @($appliedEnv.Keys)){
            $previousEnv[$name]=[Environment]::GetEnvironmentVariable($name,'Process')
            Set-Item -Path ('Env:'+$name) -Value $appliedEnv[$name]
        }
        # FORMAL_ENV_APPLIED
        $state.stage_parameters=$appliedParams
        $stageRecord.parameters=$appliedParams
        Write-State
    }
    $state.state='RUNNING';Write-State
    $watch=[Diagnostics.Stopwatch]::StartNew()
    $current=Join-Path $JobDir 'input.sim'
    $exitCodes=@()
    $lastStarExitCode=$null
    $combinedLog=''
    for($index=0;$index -lt $macroList.Count;$index++){
        $macroLaunched=$false
        $lastStarExitCode=$null
        $currentMacroIndex=$index
        $macro=$macroList[$index]
        $stem=[IO.Path]::GetFileNameWithoutExtension($macro)
        $macroLog='star-job-'+$index+'-'+$stem+'.log'
        # ②R14: the input identity is captured BEFORE the process starts. It used to be read AFTER the
        # output switch below (`$current=$outPath`), so a macro that produced an output recorded that output
        # as its own input - the recorded input was wrong for every macro that produced a file, including the
        # solve stage whose record the independent audit reads for its read-only proof.
        $macroInputPath=$current
        $macroInputName=Split-Path -Leaf $macroInputPath
        $macroInputSha=(Get-FileHash -LiteralPath $macroInputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $arguments='/d /s /c ""'+$request.star_launcher+'" -np '+$logicalCores+' -noretry -batch '+$macro+' "'+$current+'" > "'+$macroLog+'" 2>&1"'
        $state.launch_arguments=$arguments
        $state.requested_logical_cores=$logicalCores
        $state.actual_logical_cores=$null
        $state.resource_verification='not_observed'
        Write-State
        [void](Assert-FormalRuntime $PSScriptRoot $request)
        if((Get-FileHash -LiteralPath (Join-Path $JobDir $macro) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $request.macro_hashes.$macro){throw 'Staged macro hash mismatch'}
        if($index -eq 0 -and $macroInputSha -cne $request.source_sha256){throw 'Staged input hash mismatch'}
        $healthWindow=900
        if($null -ne $request.health_watchdog_seconds){$healthWindow=[double]$request.health_watchdog_seconds}
        if($healthWindow -lt 60 -or $healthWindow -gt 86400){throw 'Invalid health watchdog window'}
        $progressNames=@($macroLog)+@($stage.required)+@($outputList | Where-Object {$_})
        $progressSignature=Get-FormalProgress $JobDir $progressNames
        $lastProgress=[DateTime]::UtcNow
        if((Test-Path -LiteralPath (Join-Path $JobDir 'cancel.request')) -or ($request.parent_cancel_path -and (Test-Path -LiteralPath $request.parent_cancel_path))){throw [OperationCanceledException]::new('User requested cancellation before launch')}
        Assert-FormalExecutionSlotClear $request.remote_root
        Assert-FormalLauncherPath $request.star_launcher
        if($null -ne $proc){$proc.Dispose()}
        $proc=$null
        New-FormalProcessLease $JobDir
        $proc=New-FormalOwnedProcess $arguments $JobDir
        [void](Update-FormalProcessLease $proc $JobDir)
        $proc.Resume()
        $macroLaunched=$true
        $state.state='RUNNING';$state.process_id=$proc.Id;$state.macro=$macro;Write-State
        while(!$proc.HasExited){
            [void](Update-FormalProcessLease $proc $JobDir)
            if((Test-Path -LiteralPath (Join-Path $JobDir 'cancel.request')) -or
               ($request.parent_cancel_path -and (Test-Path -LiteralPath $request.parent_cancel_path))){
                throw [OperationCanceledException]::new('User requested cancellation')
            }
            if(Test-FormalHealth $proc $JobDir $progressNames $healthWindow ([ref]$progressSignature) ([ref]$lastProgress)){
                throw 'HEALTH_WATCHDOG: no STAR log or declared artifact progress'
            }
            if($request.min_free_memory_gb -and ([int]$watch.Elapsed.TotalSeconds % 10 -lt 2)){
                $freeGb=[double](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB
                if($null -ne $request.resource_contract){
                 $all=@(Get-CimInstance Win32_Process);$owned=@($proc.ProcessIds())
                 $rss=($all | Where-Object {$owned -contains [int]$_.ProcessId} | Measure-Object -Property WorkingSetSize -Sum).Sum
                 if($rss -gt ([double]$request.memory_gb*1GB)){Stop-OwnedProcess;throw 'Owned STAR memory budget exceeded'}
                }
                if($freeGb -lt [double]$request.min_free_memory_gb){
                    throw 'RESOURCE_STOP: Free memory reserve reached'
                }
                $drive=Get-PSDrive -Name ([IO.Path]::GetPathRoot($JobDir).Substring(0,1))
                if($drive.Free/1GB -lt [double]$request.min_free_disk_gb){
                    throw 'RESOURCE_STOP: Free disk reserve reached'
                }
            }
            $state.elapsed_seconds=[math]::Round($watch.Elapsed.TotalSeconds,2)
            $progress=Join-Path $JobDir 'smoke_status.json'
            if(Test-Path -LiteralPath $progress){try{$state.solver=Get-Content $progress -Raw | ConvertFrom-Json}catch{}}
            Write-State;Start-Sleep -Seconds 2;$proc.Refresh()
        }
        $proc.WaitForExit()
        Complete-FormalProcessLease $proc $JobDir
        $logPath=Join-Path $JobDir $macroLog
        if(Test-Path -LiteralPath $logPath){
         $lines=@(Get-Content -LiteralPath $logPath)
         $state.license_checkout_lines=@($lines | Where-Object {$_ -notmatch '(?i)(fail|reject|denied|unable|error|not available)' -and $_ -match '(?i)(checked out).*?(ccmpsuite|hpcdomains)|(ccmpsuite|hpcdomains).*?(checked out)'} | ForEach-Object {[string]$_})
         $state.core_evidence_lines=@($lines | Where-Object {$_ -match '(?i)(running|started|using).*?\d+.*?(processes|processors|cores)|total number of processes\s*:\s*\d+|parallel.*?\d+'} | ForEach-Object {[string]$_})
         foreach($line in $state.core_evidence_lines){if($line -match '(?i)total number of processes\s*:\s*([0-9]+)'){$state.actual_logical_cores=[int]$matches[1];break};if($line -match '(?i)(?:running|started|using).*?([0-9]+).*?(?:processes|processors|cores)'){$state.actual_logical_cores=[int]$matches[1];break}}
         if($null -ne $state.actual_logical_cores -and $state.actual_logical_cores -ne $logicalCores){throw 'STAR core count differs from request; no fallback'}
         if($state.actual_logical_cores -eq $logicalCores -and $state.license_checkout_lines.Count -gt 0){$state.resource_verification='observed'}
        }
        $exitCodes+=@($proc.ExitCode)
        $state.exit_code=$proc.ExitCode
        $state.elapsed_seconds=[math]::Round($watch.Elapsed.TotalSeconds,2)
        Write-State
        if(Test-Path -LiteralPath (Join-Path $JobDir $macroLog)){$combinedLog+=(Get-Content -LiteralPath (Join-Path $JobDir $macroLog) -Raw)}
        $outName=$null
        $outPath=$null
        $outSha=$null
        $lastStarExitCode=$proc.ExitCode
        if($proc.ExitCode -ne 0){throw "STAR process exit code $($proc.ExitCode) in $macroLog"}
        if($null -ne $outputList[$index] -and '' -ne [string]$outputList[$index]){
            $outName=[string]$outputList[$index]
            $outPath=Join-Path $JobDir $outName
            if(!(Test-Path -LiteralPath $outPath)){throw "Macro $macro declared output $outName but produced no such file"}
            $outSha=(Get-FileHash -LiteralPath $outPath -Algorithm SHA256).Hash.ToLowerInvariant()
            $current=$outPath
        }
        $macroRecords+=[pscustomobject]@{index=$index;macro=$macro;ran=$true;status='ok'
            exit_code=$proc.ExitCode;input=$macroInputName;input_sha256=$macroInputSha
            output=$outName;output_sha256=$outSha;log=$macroLog;error=$null}
    }
    $state.exit_codes=$exitCodes
    $state.macro_records=$macroRecords
    $stageRecord.macros=$macroRecords
    # ②R14: the stage's own input/output identity with the hashes, recomputed from the files. The independent
    # audit greps this record for the 64-hex digest of the .sim it opened (`read_only_verified`), and the
    # restricted-trial runner recorded `output_sha256` all along; the formal runner did not. A file that is
    # absent at this point stays null rather than being guessed.
    $stageRecord.input='input.sim'
    $stageRecord.input_sha256=$sourceHash
    if($null -ne $stage.output_sim -and '' -ne [string]$stage.output_sim){
        $stageOutPath=Join-Path $JobDir ([string]$stage.output_sim)
        $stageRecord.output=[string]$stage.output_sim
        if(Test-Path -LiteralPath $stageOutPath -PathType Leaf){
            $stageRecord.output_sha256=(Get-FileHash -LiteralPath $stageOutPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $stageRecord.state='ok'
    $stageRecord.seconds=[math]::Round($watch.Elapsed.TotalSeconds,2)
    Save-StageRecord
    Write-State
    # star.log stays the single log the postchecks scan for markers; it is the concatenation of the
    # per-macro logs, and the per-macro logs are kept so a failure can be attributed to one macro.
    [IO.File]::WriteAllText((Join-Path $JobDir 'star.log'),$combinedLog)
    $log=$combinedLog
    foreach($marker in $stage.markers){if(!$log.Contains($marker)){throw "Missing completion marker: $marker"}}
    foreach($artifact in $stage.required){if(!(Test-Path -LiteralPath (Join-Path $JobDir $artifact))){throw "Missing artifact: $artifact"}}
    if($stage.postcheck -eq 'preflight_v1'){
        $audit=Get-Content (Join-Path $JobDir 'automation_preflight.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($audit.schemaVersion -ne 1 -or $audit.status -notin 'ok','partial'){
            throw "Preflight JSON reports invalid state: $($audit.status)"
        }
        if($null -eq $audit.regions -or $null -eq $audit.physicsContinua -or $null -eq $audit.geometryParts){
            throw 'Preflight JSON missing required sections'
        }
        $state.preflight_status=$audit.status
        $state.preflight_warnings=@($audit.warnings).Count
    }
    if($stage.postcheck -eq 'smoke_100_v1'){
        $solver=Get-Content (Join-Path $JobDir 'smoke_status.json') -Raw | ConvertFrom-Json
        if($solver.state -ne 'SMOKE_COMPLETED' -or ($solver.end_iteration-$solver.start_iteration) -ne 100){throw "Incomplete smoke run: $($solver.state)"}
        $state.solver=$solver
    }
    if($stage.postcheck -eq 'external_domain_v1'){
        if($null -eq $request.case_spec -or $null -eq $request.case_spec.domain.outer_bounds_m){throw 'External-domain stage requires domain.outer_bounds_m in CaseSpec'}
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'external_domain_build.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'EXTERNAL_DOMAIN_BUILD_COMPLETE'){
            throw 'External-domain evidence reports an invalid state'
        }
        if($evidence.model_length_unit -ne $request.case_spec.model_length_unit){throw 'External-domain evidence unit differs from CaseSpec'}
        $expectedBounds=@($request.case_spec.domain.outer_bounds_m)
        $actualBounds=@($evidence.outer_bounds_m)
        if($expectedBounds.Count -ne 6 -or $actualBounds.Count -ne 6){throw 'External-domain bounds evidence is incomplete'}
        for($i=0;$i -lt 6;$i++){
            if([math]::Abs([double]$expectedBounds[$i]-[double]$actualBounds[$i]) -gt 1e-10){throw 'External-domain bounds evidence differs from CaseSpec'}
        }
        $expectedParts=@($request.case_spec.geometry.body_parts)
        if($request.case_spec.geometry.rotor_parts){$expectedParts+=@($request.case_spec.geometry.rotor_parts)}
        $actualParts=@($evidence.subtracted_parts)
        foreach($part in $expectedParts){if($part -notin $actualParts){throw "Subtracted-parts evidence missing $part"}}
        $state.domain_build_verified=$true
    }
    if($stage.postcheck -eq 'cylindrical_domain_v1'){
        if($null -eq $request.case_spec -or $request.case_spec.domain.shape -ne 'cylinder'){
            throw 'Cylindrical-domain stage requires domain.shape=cylinder in CaseSpec'
        }
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'cylindrical_domain_build.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'CYLINDRICAL_DOMAIN_BUILD_COMPLETE'){
            throw 'Cylindrical-domain evidence reports an invalid state'
        }
        if($evidence.model_length_unit -ne $request.case_spec.model_length_unit){throw 'Cylinder unit differs from CaseSpec'}
        foreach($name in 'cylinder_radius_m','cylinder_z_min_m','cylinder_z_max_m'){
            if([math]::Abs([double]$evidence.$name-[double]$request.case_spec.domain.$name) -gt 1e-10){
                throw "Cylinder $name differs from CaseSpec"
            }
        }
        $expectedCenter=@($request.case_spec.domain.cylinder_center_xy_m)
        $actualCenter=@($evidence.cylinder_center_xy_m)
        if($expectedCenter.Count -ne 2 -or $actualCenter.Count -ne 2){throw 'Cylinder center evidence is incomplete'}
        for($i=0;$i -lt 2;$i++){
            if([math]::Abs([double]$actualCenter[$i]-[double]$expectedCenter[$i]) -gt 1e-10){throw 'Cylinder center differs from CaseSpec'}
        }
        $expectedParts=@($request.case_spec.geometry.body_parts)
        if($request.case_spec.geometry.rotor_parts){$expectedParts+=@($request.case_spec.geometry.rotor_parts)}
        foreach($part in $expectedParts){if($part -notin @($evidence.subtracted_parts)){throw "Cylinder evidence missing $part"}}
        $state.domain_build_verified=$true
    }
    if($stage.postcheck -eq 'rotor360_v1'){
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'full_rotor_360_build.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'FULL_ROTOR_360_BUILD_COMPLETE'){
            throw 'Full-360 rotor evidence reports an invalid state'
        }
        $axis=@($evidence.axis_direction);$origin=@($evidence.axis_origin_m);$angles=@($evidence.rotation_angles_deg)
        $geometryParametersMatch=$axis.Count -eq 3 -and $origin.Count -eq 3 -and $angles.Count -eq 2
        if($geometryParametersMatch){
            $expectedAxis=@(0.0,0.0,1.0);$expectedOrigin=@(0.0,0.0,0.0);$expectedAngles=@(120.0,240.0)
            for($i=0;$i -lt 3;$i++){
                if([math]::Abs([double]$axis[$i]-$expectedAxis[$i]) -gt 1e-9 -or
                   [math]::Abs([double]$origin[$i]-$expectedOrigin[$i]) -gt 1e-9){$geometryParametersMatch=$false}
            }
            for($i=0;$i -lt 2;$i++){
                if([math]::Abs([double]$angles[$i]-$expectedAngles[$i]) -gt 1e-9){$geometryParametersMatch=$false}
            }
        }
        if($evidence.source_part -ne 'PROPELLER' -or $evidence.blade_count -ne 3 -or -not $geometryParametersMatch){
            throw 'Full-360 rotor geometry parameters differ from the audited three-blade sector'
        }
        $rotorParts=@($evidence.rotor_parts)
        if($rotorParts.Count -ne 3 -or $rotorParts -notcontains 'PROPELLER' -or
           $rotorParts -notcontains 'AUTO_FULL_ROTOR_BLADE_120' -or
           $rotorParts -notcontains 'AUTO_FULL_ROTOR_BLADE_240'){
            throw 'Full-360 rotor evidence does not contain the source and both rotated blades'
        }
        $state.rotor_geometry_constructed=$true
    }
    if($stage.postcheck -eq 'boundary_values_v1'){
        if($null -eq $request.case_spec -or $null -eq $request.case_spec.boundary_roles){throw 'Boundary-value stage requires CaseSpec roles'}
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'flow_boundaries_configured.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'NORMAL_INLET_OUTLET_CONFIGURED'){
            throw 'Boundary-value evidence reports an invalid state'
        }
        if($evidence.region -ne $request.case_spec.boundary_roles.region -or
           $evidence.inlet -ne $request.case_spec.boundary_roles.inlet -or
           $evidence.outlet -ne $request.case_spec.boundary_roles.outlet){
            throw 'Boundary roles differ from CaseSpec'
        }
        $velocity=@($request.case_spec.flow.velocity_m_s)
        $expectedSpeed=[math]::Sqrt([math]::Pow([double]$velocity[0],2)+[math]::Pow([double]$velocity[1],2)+[math]::Pow([double]$velocity[2],2))
        if([math]::Abs([double]$evidence.speed_m_s-$expectedSpeed) -gt 1e-8 -or
           [math]::Abs([double]$evidence.temperature_k-[double]$request.case_spec.flow.temperature_k) -gt 1e-8 -or
           [math]::Abs(([double]$evidence.reference_pressure_pa+[double]$evidence.outlet_gauge_pressure_pa)-[double]$request.case_spec.flow.pressure_pa) -gt 1e-6 -or
           [math]::Abs([double]$evidence.turbulence_intensity-[double]$request.case_spec.flow.turbulence_intensity) -gt 1e-10 -or
           [math]::Abs([double]$evidence.turbulent_viscosity_ratio-[double]$request.case_spec.flow.turbulent_viscosity_ratio) -gt 1e-8){
            throw 'Boundary values differ from CaseSpec'
        }
        $state.boundary_values_configured=$true
    }
    if($stage.postcheck -eq 'mrf_interfaces_v1'){
        if($null -eq $request.case_spec -or $null -eq $request.case_spec.mrf_regions){throw 'MRF interface stage requires CaseSpec regions'}
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'mrf_interfaces_configured.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'MRF_INTERFACES_CONFIGURED' -or $evidence.interfaces -ne 3){
            throw 'MRF interface evidence reports an invalid state'
        }
        if($evidence.stationary_region -ne $request.case_spec.mrf_regions.stationary -or
           $evidence.rotating_region -ne $request.case_spec.mrf_regions.rotating){
            throw 'MRF interface Region names differ from CaseSpec'
        }
        $velocity=@($request.case_spec.flow.velocity_m_s)
        $expectedSpeed=[math]::Sqrt([math]::Pow([double]$velocity[0],2)+[math]::Pow([double]$velocity[1],2)+[math]::Pow([double]$velocity[2],2))
        $expectedRate=[double]$request.case_spec.rotation.rpm*2.0*[math]::PI/60.0
        if([math]::Abs([double]$evidence.inlet_speed_m_s-$expectedSpeed) -gt 1e-8 -or
           [math]::Abs([double]$evidence.temperature_k-[double]$request.case_spec.flow.temperature_k) -gt 1e-8 -or
           [math]::Abs(([double]$evidence.reference_pressure_pa+[double]$evidence.outlet_gauge_pressure_pa)-[double]$request.case_spec.flow.pressure_pa) -gt 1e-6 -or
           [math]::Abs([double]$evidence.turbulence_intensity-[double]$request.case_spec.flow.turbulence_intensity) -gt 1e-10 -or
           [math]::Abs([double]$evidence.turbulent_viscosity_ratio-[double]$request.case_spec.flow.turbulent_viscosity_ratio) -gt 1e-8 -or
           [math]::Abs([double]$evidence.rotation_rpm-[double]$request.case_spec.rotation.rpm) -gt 1e-8 -or
           [math]::Abs([double]$evidence.rotation_rate_rad_s-$expectedRate) -gt 1e-6*[math]::Abs($expectedRate)){
            throw 'MRF interface values differ from CaseSpec'
        }
        $state.mrf_interfaces_configured=$true
    }
    if($stage.postcheck -eq 'mrf_mesh_generated_v1'){
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'mrf_diagnostic_mesh.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'MRF_DIAGNOSTIC_MESH_GENERATED' -or
           $evidence.operation -ne 'AUTO_MRF_DIAGNOSTIC_MESH' -or
           $evidence.regions -ne 2 -or $evidence.interfaces -ne 3 -or
           [double]$evidence.volume_cells -le 0){
            throw 'Diagnostic MRF mesh evidence is incomplete'
        }
        $state.mesh_cells=[long]$evidence.volume_cells
        $state.mesh_generated=$true
    }
    if($stage.postcheck -eq 'mrf_split_mesh_generated_v1'){
        $evidence=Get-Content -LiteralPath (Join-Path $JobDir 'mrf_split_diagnostic_mesh.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($evidence.schema_version -ne 1 -or $evidence.status -ne 'ok' -or
           $evidence.complete_marker -ne 'MRF_SPLIT_DIAGNOSTIC_MESH_GENERATED' -or
           $evidence.operations -ne 2 -or $evidence.regions -ne 2 -or $evidence.interfaces -ne 3 -or
           [double]$evidence.rotating_checkpoint_cells -le 0 -or
           [double]$evidence.volume_cells -le [double]$evidence.rotating_checkpoint_cells){
            throw 'Split MRF diagnostic mesh evidence is incomplete'
        }
        $state.mesh_cells=[long]$evidence.volume_cells
        $state.mesh_generated=$true
    }
    $state.state='SUCCEEDED'
    $state.convergence_verified=$false
    $state.physical_validation=$false
    Write-State
}catch {
    $originalFailure=$_
    try{Stop-OwnedProcess}catch{$state.termination_error=$_.Exception.Message}
    $_=$originalFailure
    $state.state='FAILED';$failureState='failed';$state.error=$_.Exception.Message
    if($_.Exception -is [OperationCanceledException]){$state.state='CANCELLED';$failureState='cancelled'}
    elseif($_.Exception.Message -like 'RESOURCE_STOP:*'){$state.state='RESOURCE_STOP';$failureState='resource_stop'}
    $state.reason=$_.Exception.Message
    # The macro that threw ran and failed; it must not be recorded as not_run, and its exit code must
    # survive. Independent review round 7, blocker 3, same defect as the trial runner.
    if($null -ne $stageRecord){
        if($null -ne $currentMacroIndex -and $macroRecords.Count -le $currentMacroIndex){
            $macroRecords+=[pscustomobject]@{index=$currentMacroIndex;macro=$macroList[$currentMacroIndex]
                ran=[bool]$macroLaunched;status=$(if($macroLaunched){$failureState}else{'not_run'});exit_code=$lastStarExitCode;input=$macroInputName
                input_sha256=$macroInputSha;output=$null;output_sha256=$null
                log=('star-job-'+$currentMacroIndex+'-'+[IO.Path]::GetFileNameWithoutExtension($macroList[$currentMacroIndex])+'.log')
                error=$_.Exception.Message}
        }
        for($index=$macroRecords.Count;$index -lt $macroList.Count;$index++){
            $macroRecords+=[pscustomobject]@{index=$index;macro=$macroList[$index];ran=$false
                status='not_run';exit_code=$null;input=$null;input_sha256=$null;output=$null
                output_sha256=$null;log=$null;error=$null}
        }
        # A failed stage still knows its own input identity, and must not claim an output it never produced.
        if($null -eq $stageRecord.input){$stageRecord.input='input.sim';$stageRecord.input_sha256=$sourceHash}
        if($null -eq $stageRecord.output_sha256){$stageRecord.output=$null;$stageRecord.output_sha256=$null}
        $stageRecord.state=$failureState
        if($null -ne $watch){$stageRecord.seconds=[math]::Round($watch.Elapsed.TotalSeconds,2)}
        $stageRecord.error=$_.Exception.Message
        $stageRecord.macros=$macroRecords
        Save-StageRecord
    }
    Write-State
}finally {
    # FORMAL_ENV_RESTORE: also runs after successful stages and prelaunch refusals.
    foreach($name in @($previousEnv.Keys)){
        [Environment]::SetEnvironmentVariable($name,$previousEnv[$name],'Process')
    }
    # FORMAL_ENV_RESTORED
    if($null -ne $proc){$proc.Dispose()}
    if($locked){$mutex.ReleaseMutex()}
    if($null -ne $mutex){$mutex.Dispose()}
}
