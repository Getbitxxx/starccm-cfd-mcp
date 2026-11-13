$ErrorActionPreference='Stop'
function Assert-FormalRuntime([string]$RuntimeRoot,$Request,[bool]$CheckDirectoryName=$true){
    $manifestPath=Join-Path $RuntimeRoot 'runtime-manifest.json'
    if([string]$Request.runtime_manifest_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
       (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Request.runtime_manifest_sha256){throw 'Runtime manifest hash mismatch'}
    $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($manifest.schema_version -ne 1 -or $manifest.runtime_digest -cne $Request.runtime_digest -or
       [string]$Request.runtime_digest -cnotmatch '^[a-f0-9]{64}$'){throw 'Runtime manifest identity mismatch'}
    $root=[IO.Path]::GetFullPath($RuntimeRoot).TrimEnd([char]92)+[char]92
    if((Get-Item -LiteralPath $RuntimeRoot).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Runtime root reparse point refused'}
    $sources=@{};$targets=@{};$previous='';$hasher=[Security.Cryptography.SHA256]::Create()
    try{
        foreach($entry in @($manifest.files)){
            $source=[string]$entry.source;$relative=[string]$entry.path
            if($source -cnotmatch '^(macros/[A-Za-z0-9_.-]+\.java|worker/[A-Za-z0-9_.-]+\.ps1|workflows/[a-z0-9-]+/manifest\.json|scripts/resource_contract\.py)$'){throw 'Invalid runtime source path'}
            $expected=if($source.StartsWith('worker/')){$source.Substring(7)}else{$source}
            if($relative -cne $expected -or $sources.ContainsKey($source) -or $targets.ContainsKey($relative.ToLowerInvariant()) -or
               ($previous -and [string]::CompareOrdinal($previous,$source) -ge 0)){throw 'Runtime path mapping/order mismatch'}
            $sources[$source]=$true;$targets[$relative.ToLowerInvariant()]=$true;$previous=$source
            $file=Join-Path $RuntimeRoot $relative.Replace('/',[char]92)
            if(!(Test-Path -LiteralPath $file -PathType Leaf)){throw ('Missing runtime file: '+$relative)}
            $item=Get-Item -LiteralPath $file
            $parent=$item
            while($null -ne $parent -and $parent.FullName.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){
                if($parent.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Runtime reparse point refused'}
                if($parent -is [IO.FileInfo]){$parent=$parent.Directory}else{$parent=$parent.Parent}
            }
            if([string]$entry.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
               (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256){throw ('Runtime file hash mismatch: '+$relative)}
            $nameBytes=[Text.Encoding]::UTF8.GetBytes($source)
            [void]$hasher.TransformBlock($nameBytes,0,$nameBytes.Length,$nameBytes,0)
            $stream=[IO.File]::OpenRead($file)
            try{$buffer=New-Object byte[] 1048576;while(($count=$stream.Read($buffer,0,$buffer.Length)) -gt 0){[void]$hasher.TransformBlock($buffer,0,$count,$buffer,0)}}finally{$stream.Dispose()}
        }
        foreach($required in @('run_pipeline.ps1','run_job.ps1','runtime_guard.ps1')){if(!$targets.ContainsKey($required)){throw ('Runtime lacks '+$required)}}
        foreach($file in Get-ChildItem -LiteralPath $RuntimeRoot -File -Recurse){
            $relative=$file.FullName.Substring($root.Length).Replace([char]92,'/')
            if($relative -cne 'runtime-manifest.json' -and !$targets.ContainsKey($relative.ToLowerInvariant())){throw ('Unmanifested runtime file: '+$relative)}
        }
        [void]$hasher.TransformFinalBlock((New-Object byte[] 0),0,0)
        $digest=([BitConverter]::ToString($hasher.Hash)).Replace('-','').ToLowerInvariant()
        if($digest -cne $Request.runtime_digest){throw 'Full runtime digest mismatch'}
        if($CheckDirectoryName -and (Split-Path -Leaf $RuntimeRoot) -cne $digest.Substring(0,16)){throw 'Runtime directory identity mismatch'}
    }finally{$hasher.Dispose()}
    foreach($pin in @(@('run_pipeline.ps1','pipeline_runner_sha256'),@('run_job.ps1','runner_sha256'),@('runtime_guard.ps1','runtime_guard_sha256'))){
        if([string]$Request.($pin[1]) -cnotmatch '^[a-f0-9]{64}$' -or
           (Get-FileHash -LiteralPath (Join-Path $RuntimeRoot $pin[0]) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Request.($pin[1])){throw ('Reviewed runtime component mismatch: '+$pin[0])}
    }
    return $digest
}
function Assert-FormalWorkerIdentity($Request){
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Formal worker must not run as administrator'}
    if(!$Request.expected_worker_sid -or $identity.User.Value -cne $Request.expected_worker_sid){throw 'Formal worker identity mismatch'}
}
function Get-FormalProgress([string]$Directory,[string[]]$Names){
    $parts=@()
    foreach($name in @($Names | Sort-Object -Unique)){
        if($name -in @('state.json','state.tmp.json','request.json','health-watchdog.json','termination.log')){continue}
        $path=Join-Path $Directory $name
        if(Test-Path -LiteralPath $path -PathType Leaf){$f=Get-Item -LiteralPath $path;$parts+=($name+':'+$f.Length+':'+$f.LastWriteTimeUtc.Ticks)}
    }
    return ($parts -join '|')
}
function Assert-FormalLauncherPath($Path){
    # cmd.exe is still needed for the vendor .bat. Reject expansion and shell syntax before use.
    if($Path -isnot [string] -or $Path -cnotmatch '\A[A-Za-z]:\\[A-Za-z0-9_ .+\\-]+\z'){
        throw 'Invalid STAR launcher path: cmd metacharacters or non-absolute path'
    }
}
function Get-FormalSubmittedAt($Text){
    if($Text -isnot [string] -or $Text -cnotmatch '\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)\z'){
        throw 'Missing or invalid submitted_at_utc: explicit UTC timestamp required; legacy fallback refused'
    }
    try{return [DateTimeOffset]::Parse($Text,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime}
    catch{throw 'Invalid submitted_at_utc calendar value'}
}
function Save-FormalProcessLease($Lease,[string]$Directory){
    $path=Join-Path $Directory 'process-lease.json'
    $temporary=Join-Path $Directory 'process-lease.tmp.json'
    $Lease | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    if([IO.File]::Exists($path)){
        [IO.File]::Replace($temporary,$path,[System.Management.Automation.Language.NullString]::Value)
    }else{[IO.File]::Move($temporary,$path)}
}
function New-FormalProcessLease([string]$Directory){
    $path=Join-Path $Directory 'process-lease.json'
    if(Test-Path -LiteralPath $path){
        $old=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if($old.state -cne 'RELEASED'){throw 'REJECTED_BUSY: unresolved process lease requires diagnosis'}
    }
    Save-FormalProcessLease @{schema_version=1;state='STARTING';root_id=$null;tracked=@();job_dir=$Directory;engineering_verified=$false} $Directory
}
function Initialize-FormalProcessJobType {
    if('CodexCfdHardeningV1.OwnedProcess' -as [type]){return}
    # Kernel ownership, not PID/ancestry inference. No breakaway flags or wall-clock limits.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
namespace CodexCfdHardeningV1 {
 public sealed class OwnedProcess : IDisposable {
  [StructLayout(LayoutKind.Sequential)] struct SI { public uint cb; public IntPtr reserved,desktop,title; public uint x,y,xSize,ySize,xChars,yChars,fill,flags; public ushort show,reserved2; public IntPtr reservedPtr,input,output,error; }
  [StructLayout(LayoutKind.Sequential)] struct PI { public IntPtr process,thread; public uint pid,tid; }
  [StructLayout(LayoutKind.Sequential)] struct BASIC { public long processTime,jobTime; public uint flags; public UIntPtr minWorking,maxWorking; public uint activeLimit; public UIntPtr affinity; public uint priority,scheduling; }
  [StructLayout(LayoutKind.Sequential)] struct IO { public ulong readOps,writeOps,otherOps,readBytes,writeBytes,otherBytes; }
  [StructLayout(LayoutKind.Sequential)] struct EXT { public BASIC basic; public IO io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob; }
  [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attr,string name);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref EXT info,int length);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,IntPtr info,int length,IntPtr returned);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
  [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string directory,ref SI si,out PI pi);
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  IntPtr job; PI pi; Process root; bool suspended;
  public string OwnershipId { get; private set; }
  public int Id { get { return checked((int)pi.pid); } }
  public bool IsSuspended { get { return suspended; } }
  public bool HasExited { get { return root.HasExited; } }
  public int ExitCode { get { return root.ExitCode; } }
  public void Refresh() { root.Refresh(); }
  public bool WaitForExit(int ms) { return root.WaitForExit(ms); }
  public void WaitForExit() { root.WaitForExit(); }
  public static OwnedProcess CreateSuspended(string app,string arguments,string directory) {
   OwnedProcess p=new OwnedProcess(); p.OwnershipId=Guid.NewGuid().ToString("N");
   try {
    p.job=CreateJobObject(IntPtr.Zero,null); if(p.job==IntPtr.Zero) throw new Win32Exception();
    EXT limits=new EXT(); limits.basic.flags=0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if(!SetInformationJobObject(p.job,9,ref limits,Marshal.SizeOf(typeof(EXT)))) throw new Win32Exception();
    SI si=new SI(); si.cb=(uint)Marshal.SizeOf(typeof(SI));
    if(!CreateProcess(app,new StringBuilder("\""+app+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,directory,ref si,out p.pi)) throw new Win32Exception();
    // The root cannot run user code or create a descendant before assignment succeeds.
    if(!AssignProcessToJobObject(p.job,p.pi.process)) throw new Win32Exception();
    p.root=Process.GetProcessById(p.Id); IntPtr retained=p.root.Handle;
    p.suspended=true; return p;
   } catch {
    if(p.pi.process!=IntPtr.Zero) TerminateProcess(p.pi.process,1); // unexecuted root only
    p.Dispose(); throw;
   }
  }
  public void Resume() {
   if(!suspended || ResumeThread(pi.thread)==0xffffffff) throw new Win32Exception();
   suspended=false; CloseHandle(pi.thread); pi.thread=IntPtr.Zero;
  }
  public int[] ProcessIds() {
   if(job==IntPtr.Zero) throw new InvalidOperationException("Job handle already closed");
   for(int capacity=64;capacity<=1048576;capacity*=2) {
    int size=8+IntPtr.Size*capacity; IntPtr buffer=Marshal.AllocHGlobal(size);
    try {
     if(!QueryInformationJobObject(job,3,buffer,size,IntPtr.Zero)) {
      int error=Marshal.GetLastWin32Error(); if(error==234) continue; throw new Win32Exception(error);
     }
     int count=Marshal.ReadInt32(buffer,4); if(count<0 || count>capacity) throw new InvalidOperationException("Invalid job process count");
     int[] ids=new int[count]; for(int i=0;i<count;i++) ids[i]=checked((int)(IntPtr.Size==8?Marshal.ReadInt64(buffer,8+i*IntPtr.Size):Marshal.ReadInt32(buffer,8+i*IntPtr.Size)));
     return ids;
    } finally { Marshal.FreeHGlobal(buffer); }
   }
   throw new InvalidOperationException("Job process list exceeds safe allocation");
  }
  public void Kill() { if(job==IntPtr.Zero || !TerminateJobObject(job,1)) throw new Win32Exception(); }
  public bool WaitForEmpty(int ms) {
   Stopwatch clock=Stopwatch.StartNew();
   while(ProcessIds().Length!=0) { if(clock.ElapsedMilliseconds>=ms) return false; Thread.Sleep(50); }
   return true;
  }
  public void CloseJob() { if(job!=IntPtr.Zero) { if(!CloseHandle(job)) throw new Win32Exception(); job=IntPtr.Zero; } }
  public void Dispose() {
   CloseJob(); if(pi.thread!=IntPtr.Zero) { CloseHandle(pi.thread); pi.thread=IntPtr.Zero; }
   if(pi.process!=IntPtr.Zero) { CloseHandle(pi.process); pi.process=IntPtr.Zero; }
   if(root!=null) { root.Dispose(); root=null; }
  }
 }
}
'@
}
function New-FormalOwnedProcess([string]$Arguments,[string]$Directory){
    Initialize-FormalProcessJobType
    return [CodexCfdHardeningV1.OwnedProcess]::CreateSuspended((Join-Path $env:SystemRoot 'System32\cmd.exe'),$Arguments,$Directory)
}
function Update-FormalProcessLease($Process,[string]$Directory){
    $path=Join-Path $Directory 'process-lease.json'
    $lease=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if(!$lease.owner_token){
        if(!$Process.IsSuspended){throw 'Root must be assigned to a kernel job before lease registration and resume'}
        $lease | Add-Member -NotePropertyName owner_token -NotePropertyValue $Process.OwnershipId
        $lease.root_id=$Process.Id
    }
    if($lease.owner_token -cne $Process.OwnershipId){throw 'Kernel job ownership token differs from process lease'}
    $owned=@($Process.ProcessIds())
    $lease.tracked=$owned;$lease.state='ACTIVE'
    Save-FormalProcessLease $lease $Directory
    return $owned
}
function Complete-FormalProcessLease($Process,[string]$Directory){
    $live=@(Update-FormalProcessLease $Process $Directory)
    if($live.Count -gt 0){throw 'Owned process tree still alive; execution slot remains quarantined'}
    $Process.CloseJob()
    $lease=Get-Content -LiteralPath (Join-Path $Directory 'process-lease.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $lease.state='RELEASED';Save-FormalProcessLease $lease $Directory
}
function Assert-FormalExecutionSlotClear([string]$Root){
    foreach($name in @('jobs','runs')){
        $base=Join-Path $Root $name
        if(!(Test-Path -LiteralPath $base)){continue}
        foreach($file in @(Get-ChildItem -LiteralPath $base -Filter 'process-lease.json' -File -Recurse -ErrorAction Stop)){
            $lease=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if($lease.state -cne 'RELEASED'){throw 'REJECTED_BUSY: durable process lease requires diagnosis'}
        }
    }
    $star=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '(?i)^(starccm|star-ccm|ccmserver)' -or
        ($_.Name -match '(?i)^javaw?\.exe$' -and $_.CommandLine -match ('(?i)starccm|star-ccm|'+[regex]::Escape($Root)))
    })
    if($star.Count -gt 0){throw 'REJECTED_BUSY: STAR or worker Java process already active'}
}
function Stop-FormalProcessTree($Process,[string]$Directory){
    if($null -eq $Process){return}
    $path=Join-Path $Directory 'process-lease.json'
    $lease=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if($lease.state -ceq 'RELEASED'){return}
    for($attempt=0;$attempt -lt 2;$attempt++){
        $live=@(Update-FormalProcessLease $Process $Directory)
        if($live.Count -eq 0){break}
        $Process.Kill() # TerminateJobObject on the retained handle; no PID-based taskkill.
        [void]$Process.WaitForEmpty(10000)
    }
    Complete-FormalProcessLease $Process $Directory
}
function Test-FormalHealth($Process,[string]$Directory,[string[]]$Names,[double]$Window,[ref]$Signature,[ref]$LastProgress){
    if([double]::IsNaN($Window) -or [double]::IsInfinity($Window) -or $Window -lt 60 -or $Window -gt 86400){throw 'Invalid health watchdog observation window'}
    $current=Get-FormalProgress $Directory $Names
    if($current -cne $Signature.Value){$Signature.Value=$current;$LastProgress.Value=[DateTime]::UtcNow;return $false}
    if(([DateTime]::UtcNow-$LastProgress.Value).TotalSeconds -le $Window){return $false}
    $owned=@($Process.ProcessIds())
    @{state='HEALTH_WATCHDOG';reason='No STAR log or declared artifact progress';window_seconds=$Window;last_progress_at=$LastProgress.Value.ToString('o');observed_at=[DateTime]::UtcNow.ToString('o');owned_process_ids=$owned;progress_signature=$current;wall_clock_stop_enabled=$false;engineering_verified=$false} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Directory 'health-watchdog.json') -Encoding UTF8
    Stop-FormalProcessTree $Process $Directory
    return $true
}
