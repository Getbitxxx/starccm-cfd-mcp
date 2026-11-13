param([Parameter(Mandatory=$true)][string]$RunDir,[Parameter(Mandatory=$true)][string]$ExpectedRequestSha256)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$state=@{state='STARTING';started_at=[DateTime]::UtcNow.ToString('o');run_dir=$RunDir;completed=@()}
$mutex=$null
$locked=$false
function Publish-FormalStateFile([string]$Temporary,[string]$Destination) {
    # Never delete the previous state before attempting the OS atomic replacement.
    if([IO.File]::Exists($Destination)){
        [IO.File]::Replace($Temporary,$Destination,[System.Management.Automation.Language.NullString]::Value)
    }else{
        [IO.File]::Move($Temporary,$Destination)
    }
}
function Save-State {
    $state.updated_at=[DateTime]::UtcNow.ToString('o')
    $temp=Join-Path $RunDir 'state.tmp.json'
    $state | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temp -Encoding UTF8
    Publish-FormalStateFile $temp (Join-Path $RunDir 'state.json')
}
function Stop-Run([string]$status,[string]$message){
    $state.state=$status
    $state.reason=$message
    Save-State
}
try {
    $requestPath=Join-Path $RunDir 'request.json'
    if($ExpectedRequestSha256 -cnotmatch '^[a-f0-9]{64}$' -or (Get-FileHash -LiteralPath $requestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedRequestSha256){throw 'Scheduled request hash mismatch'}
    $req=Get-Content -LiteralPath (Join-Path $RunDir 'request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(!$req.pipeline_runner_sha256){throw 'No reviewed pipeline-runner hash pinned in this request'}
    if((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $req.pipeline_runner_sha256){throw 'Pipeline runner hash mismatch'}
    if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'runtime_guard.ps1') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $req.runtime_guard_sha256){throw 'Runtime guard hash mismatch'}
    . (Join-Path $PSScriptRoot 'runtime_guard.ps1')
    [void](Assert-FormalRuntime $PSScriptRoot $req)
    Assert-FormalWorkerIdentity $req
    $state.wall_clock_stop_enabled=$false
    $state.timeout_seconds_observed=$req.timeout_seconds
    $state.runtime_manifest_sha256=$req.runtime_manifest_sha256
    $state.run_id=$req.run_id
    $state.request_digest=$req.request_digest
    $state.runtime_digest=$req.runtime_digest
    $state.case_ref=$req.case_ref
    $state.engineering_verified=$false
    # R12: the submitted PLAN travels with the run. `pre_operations` are the steps backend.submit_run
    # already ran for real (with their result digest); `post_operations` are the steps the reviewed
    # report entry performs after the graph (export_evidence / report). Recording them here is what lets
    # get_run say whether the post steps have really happened for THIS run, instead of assuming it.
    $state.plan_operations=$req.plan_operations
    $state.pre_operations=$req.pre_operations
    $state.post_operations=$req.post_operations
    $observedStart=Get-FormalSubmittedAt $req.submitted_at_utc
    Assert-FormalLauncherPath $req.star_launcher
    Save-State
    $mutex=New-Object System.Threading.Mutex($false,'Global\CodexCFD2502Worker')
    try{$locked=$mutex.WaitOne(0)}catch [System.Threading.AbandonedMutexException]{$locked=$true}
    if(!$locked){Stop-Run 'REJECTED_BUSY' 'Another pipeline owns the execution slot';exit 0}
    Assert-FormalExecutionSlotClear $req.remote_root
    $root=[IO.Path]::GetFullPath($req.remote_root).TrimEnd([char]92)+[char]92
    $source=(Resolve-Path -LiteralPath $req.source_path).Path
    if(!$source.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Input outside worker root'}
    if((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $req.source_sha256){
        throw 'Input hash changed after submission'
    }
# New contracts are pinned by the reviewed pipeline hash; legacy requests keep their original caps.
$reserveGB=16.0
if($null -ne $req.resource_contract){
 $r=$req.resource_contract
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
 if([double]$r.logical_cores -ne [double]$req.logical_cores -or [double]$r.memory_gb -ne [double]$req.memory_gb){throw 'Resource contract differs from requested configuration'}
 # Task and outer time declarations are observations, never admission or stop gates.
}

    $system=Get-CimInstance Win32_ComputerSystem
    $os=Get-CimInstance Win32_OperatingSystem
    if([int]$req.logical_cores -lt 1 -or [int]$req.logical_cores -gt 64 -or
       [int]$req.logical_cores -gt $system.NumberOfLogicalProcessors){throw 'Requested logical cores exceed available or configured limit'}
    $freeMemoryGb=[double]$os.FreePhysicalMemory/1MB
    if($null -ne $req.resource_contract -and $freeMemoryGb -lt ([double]$req.memory_gb+$reserveGB)){throw 'Declared memory plus reserve unavailable; no silent reduction'}
    $effectiveMemoryGb=[math]::Min([double]$req.memory_gb,[math]::Max(0,$freeMemoryGb-$reserveGB))
    if($effectiveMemoryGb -lt 1){throw 'Less than 16 GB free-memory reserve remains'}
    $state.resource_contract=$req.resource_contract
    $state.logical_cores=[int]$req.logical_cores
    $state.effective_memory_gb=[math]::Round($effectiveMemoryGb,2)
    Save-State
    if($req.runtime_digest.Substring(0,16) -ne (Split-Path -Leaf $PSScriptRoot)){throw 'Runtime digest mismatch'}
    # R12: THIS layer verifies its OWN reviewed bytes. It used to send the single reviewed runner hash
    # into each stage request, and run_job.ps1 compared that value with the hash of its own - different -
    # file, so every formal stage was refused before STAR started. Each layer now has its own pinned hash:
    # `pipeline_runner_sha256` here against $PSCommandPath, and `runner_sha256` (forwarded unchanged) in
    # run_job.ps1 against its own $PSCommandPath. No comparison is skipped and no live digest is used.
    # The hash is computed AND compared inside the condition. An intermediate variable would be a hole:
    # a later line could overwrite it and make the check vacuously true while keeping this literal and this
    # position (R12 review, blockers W5b/W11). With the comparison inline there is nothing to overwrite.
    $pipelineSelfHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $state.pipeline_runner_sha256=$pipelineSelfHash
    Save-State
    $state.state='RUNNING';Save-State
    $stepIndex=-1
    foreach($item in $req.steps){
        $stepIndex++
        if(Test-Path -LiteralPath (Join-Path $RunDir 'cancel.request')){Stop-Run 'CANCELLED' 'User requested cancellation';exit 0}
        if($item.key -cnotmatch '^[a-z][a-z0-9_-]{0,63}$' -or
           $item.workflow_id -cnotmatch '^[a-z][a-z0-9-]{0,63}$' -or
           $item.stage -cnotmatch '^[a-z][a-z0-9_-]{0,63}$'){
            throw 'Invalid stage identifier'
        }
        $manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot ('workflows\'+$item.workflow_id+'\manifest.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
        if($manifest.id -cne $item.workflow_id -or $manifest.schema_version -ne 1){throw 'Manifest mismatch'}
        $stageProperty=$manifest.stages.PSObject.Properties[$item.stage]
        if($null -eq $stageProperty){throw 'Stage not in installed workflow'}
        $stage=$stageProperty.Value
        $job=Join-Path $RunDir ('stages\'+$item.key)
        $checkpoint=Join-Path $job 'checkpoint.json'
        if(Test-Path -LiteralPath $checkpoint){
            $previous=Get-Content -LiteralPath $checkpoint -Raw -Encoding UTF8 | ConvertFrom-Json
            if($previous.request_digest -cne $req.request_digest -or $previous.runtime_digest -cne $req.runtime_digest -or $previous.stage_key -cne $item.key){throw 'Checkpoint execution identity mismatch'}
            $priorState=Get-Content -LiteralPath (Join-Path $job 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $priorRecordPath=Join-Path $job ('stage-'+$item.stage+'.json')
            if($priorState.state -cne 'SUCCEEDED' -or (Get-FileHash -LiteralPath $priorRecordPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $previous.stage_record_sha256){throw 'Checkpoint successful stage identity mismatch'}
            if($previous.output_path -and $previous.output_path -cne (Join-Path $job $stage.output_sim)){throw 'Checkpoint output path mismatch'}
            if($previous.input_sha256 -ne (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()){
                throw 'Checkpoint input hash mismatch'
            }
            if($previous.output_path){
                if(!(Test-Path -LiteralPath $previous.output_path) -or
                   (Get-FileHash -LiteralPath $previous.output_path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $previous.output_sha256){
                    throw 'Checkpoint output hash mismatch'
                }
                $source=$previous.output_path
            }
            $state.completed=@($state.completed)+@($item.key)
            $state.current_stage=$item.key;Save-State
            continue
        }
        if(Test-Path -LiteralPath (Join-Path $job 'request.json')){throw 'Existing incomplete stage requires diagnosis; automatic retry refused'}
        New-Item -ItemType Directory -Path $job -Force | Out-Null
        $stageBudget=[double]$item.timeout_seconds
        # ②R14: STAGE FILE HANDOVER. A stage may DECLARE that it needs records produced by an earlier stage of
        # the same run (the validation stage needs the solve stage's three files: without them the independent
        # audit can only report NOT_CONVERGED_UNMEASURED - measured, see round14-formal-layout-finding.json).
        # The runner resolves them HERE, from this run's own earlier stage directory, and binds each one to the
        # path and sha256 it actually has. Nothing is carried by hand and no historical record may substitute:
        # the source step must be earlier in THIS plan and its job must have SUCCEEDED, otherwise the stage is
        # refused before anything is started. run_job.ps1 re-verifies every hash again before STAR.
        $inheritedInputs=@()
        $inheritedProperty=$stage.PSObject.Properties['inherited_inputs']
        if($null -ne $inheritedProperty -and $null -ne $inheritedProperty.Value){
            $declared=$inheritedProperty.Value
            $fromStageName=[string]$declared.stage
            $declaredFiles=@($declared.files)
            if(!$fromStageName){throw 'inherited_inputs declares no source stage'}
            if($declaredFiles.Count -eq 0){throw 'inherited_inputs declares no files'}
            $fromStep=$null
            for($i=0;$i -lt $stepIndex;$i++){
                if([string]$req.steps[$i].stage -ceq $fromStageName){$fromStep=$req.steps[$i]}
            }
            if($null -eq $fromStep){throw ('inherited_inputs names the stage '+$fromStageName+', which is not an EARLIER step of this run')}
            # What may be inherited is what the SOURCE STAGE really produces: the files it declares in
            # `required` (which run_job.ps1 verifies exist before the stage may succeed), its declared .sim
            # output, its macro outputs, and its own runner-written stage record. Binding an arbitrary name
            # that merely happens to exist in the source directory would let a declaration name `input.sim`
            # and have it copied over this stage's reviewed input after verification (found by ②R14 review).
            $fromManifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot ('workflows\'+$fromStep.workflow_id+'\manifest.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
            $fromStageProperty=$fromManifest.stages.PSObject.Properties[$fromStep.stage]
            if($null -eq $fromStageProperty){throw ('inherited_inputs source stage is not in its manifest: '+[string]$fromStep.stage)}
            $fromStage=$fromStageProperty.Value
            $producible=@{('stage-'+[string]$fromStep.stage+'.json')=$true}
            foreach($name in @($fromStage.required)){if($name){$producible[[string]$name]=$true}}
            if($fromStage.output_sim){$producible[[string]$fromStage.output_sim]=$true}
            foreach($name in @($fromStage.macro_outputs)){if($name){$producible[[string]$name]=$true}}
            $fromJob=Join-Path $RunDir ('stages\'+$fromStep.key)
            $fromStatePath=Join-Path $fromJob 'state.json'
            if(!(Test-Path -LiteralPath $fromStatePath -PathType Leaf)){throw ('inherited_inputs source stage '+$fromStep.key+' has no state record')}
            $fromState=Get-Content -LiteralPath $fromStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            if($fromState.state -ne 'SUCCEEDED'){throw ('inherited_inputs source stage '+$fromStep.key+' did not succeed: '+[string]$fromState.state)}
            foreach($name in $declaredFiles){
                if($name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'){throw ('Invalid inherited input name: '+[string]$name)}
                if(!$producible.ContainsKey([string]$name)){throw ('Inherited input '+[string]$name+' is not produced by the source stage '+$fromStep.key)}
                $fromPath=Join-Path $fromJob $name
                if(!(Test-Path -LiteralPath $fromPath -PathType Leaf)){throw ('Inherited input missing from the source stage '+$fromStep.key+' : '+$name)}
                if($name -like 'stage-*.json'){
                    $sourceRecord=Get-Content -LiteralPath $fromPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    if($sourceRecord.state -ne 'ok'){throw ('Inherited stage record '+$name+' does not report ok: '+[string]$sourceRecord.state)}
                }
                $inheritedInputs+=[pscustomobject]@{name=$name;source_stage=$fromStep.key;source_path=$fromPath
                    source_sha256=(Get-FileHash -LiteralPath $fromPath -Algorithm SHA256).Hash.ToLowerInvariant()}
            }
        }
        $stageReq=[pscustomobject]@{
            job_id=$req.run_id+'-'+$item.key
            workflow_id=$item.workflow_id
            stage=$item.stage
            request_digest=$req.request_digest
            runtime_digest=$req.runtime_digest
            source_path=$source
            source_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
            remote_root=$req.remote_root
            star_launcher=$req.star_launcher
            timeout_seconds=$stageBudget
            logical_cores=[int]$req.logical_cores
            min_free_memory_gb=$reserveGB
            memory_gb=[double]$req.memory_gb
            resource_contract=$req.resource_contract
            min_free_disk_gb=20
            case_spec=$req.case_spec
            macro_hashes=$item.macro_hashes
            # R11: stage parameters (the environment a manifest stage DECLARES its macros read) travel with
            # the step. run_job.ps1 re-validates the names and applies them only to its own child processes.
            env=$item.env
            runner_sha256=$req.runner_sha256
            pipeline_runner_sha256=$req.pipeline_runner_sha256
            runtime_manifest_sha256=$req.runtime_manifest_sha256
            runtime_guard_sha256=$req.runtime_guard_sha256
            expected_worker_sid=$req.expected_worker_sid
            health_watchdog_seconds=$req.health_watchdog_seconds
            review_sha256=$req.review_sha256
            review_ref=$req.review_ref
            parent_cancel_path=(Join-Path $RunDir 'cancel.request')
            inherited_inputs=$inheritedInputs
        }
        # Stage 1's input is the reviewed library input; later stages consume the previous stage's
        # checkpoint, whose hash this pipeline already compared above.
        if($item.key -eq $req.steps[0].key){
            $stageReq | Add-Member -NotePropertyName input_sha256 -NotePropertyValue $req.input_sha256
        }
        $stageReq | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $job 'request.json') -Encoding UTF8
        $state.current_stage=$item.key;Save-State
        $stageRequestHash=(Get-FileHash -LiteralPath (Join-Path $job 'request.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        & (Join-Path $PSScriptRoot 'run_job.ps1') -JobDir $job -ExpectedRequestSha256 $stageRequestHash
        $result=Get-Content -LiteralPath (Join-Path $job 'state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($result.state -ne 'SUCCEEDED'){
            if($result.state -eq 'CANCELLED'){Stop-Run 'CANCELLED' 'Stage cancelled';exit 0}
            if($result.state -eq 'RESOURCE_STOP'){Stop-Run 'RESOURCE_STOP' $result.reason;exit 0}
            $state.stage_error=$result.error
            Stop-Run 'WAITING_AGENT' ('Stage '+$item.key+' ended '+$result.state)
            exit 0
        }
        $checkpointData=@{request_digest=$req.request_digest;runtime_digest=$req.runtime_digest;stage_record_sha256=(Get-FileHash -LiteralPath (Join-Path $job ('stage-'+$item.stage+'.json')) -Algorithm SHA256).Hash.ToLowerInvariant();stage_key=$item.key;input_sha256=$stageReq.source_sha256;completed_at=[DateTime]::UtcNow.ToString('o')}
        if($stage.output_sim){
            $out=Join-Path $job $stage.output_sim
            if(!(Test-Path -LiteralPath $out)){throw 'Declared .sim output missing'}
            $checkpointData.output_path=$out
            $checkpointData.output_sha256=(Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash.ToLowerInvariant()
            $source=$out
        }
        $checkpointData | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $checkpoint -Encoding UTF8
        $state.completed=@($state.completed)+@($item.key)
        Save-State
    }
    $state.final_sim=$source
    $state.final_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $state.elapsed_seconds=[math]::Round(([DateTime]::UtcNow-$observedStart).TotalSeconds,2)
    $state.state='EXECUTION_COMPLETED'
    $state.reason='All installed stages executed; engineering verification is evaluated separately'
    Save-State
}catch {
    $state.error=$_.Exception.Message
    Stop-Run 'WAITING_AGENT' 'Pipeline requires diagnosis'
}finally {
    if($locked){$mutex.ReleaseMutex()}
    if($null -ne $mutex){$mutex.Dispose()}
}
