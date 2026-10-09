#Requires -Version 5.1
<# DRAFT. Default read-only plan; Apply needs reviewed frozen source pins.
New observation-only namespace/task; no Warden stop/start/config/pointer changes.
All paid, component and release actuation remains permanently disabled here.
No retry/reset/delete path. Oracle physical acceptance is not a prerequisite. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Read-HeldObservationControl {
 param([string]$Path,[string]$Pin='',[long]$Maximum=65536)
 Assert-ProtectedPath $Path
 $item=Get-Item -LiteralPath $Path -Force
 if($item.PSIsContainer -or $item.Length -le 0 -or $item.Length -gt $Maximum){throw 'Control size or type differs.'}
 if(-not $Pin){$Pin=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
 $stream=Open-VerifiedFile $Path $Pin $item.Length;$script:held.Add($stream)
 [pscustomobject]@{Sha256=$Pin;Length=$item.Length;Stream=$stream;Value=((Read-HeldText $stream)|ConvertFrom-Json)}
}

function Test-HeldObservationPresence {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop;return $true}catch [Management.Automation.ItemNotFoundException]{return $false}
}

function Read-HeldObservationRuntime {
 param([string]$Path)
 # Runtime publication is atomic and ongoing. Permit replacement of this
 # held snapshot, so observing it cannot make the daemon's next write fail.
 Assert-ProtectedPath $Path
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
 try{
  [CoChemStagedFileIdentity]::Check($stream,$Path)
  if($stream.Length -le 0 -or $stream.Length -gt 65536){throw 'Runtime snapshot is oversized.'}
  $raw=[byte[]]::new([int]$stream.Length);$offset=0
  while($offset -lt $raw.Length){$n=$stream.Read($raw,$offset,$raw.Length-$offset);if($n -le 0){throw 'Runtime snapshot shortened.'};$offset+=$n}
  if($stream.ReadByte() -ne -1){throw 'Runtime snapshot grew.'}
  [CoChemStagedFileIdentity]::Check($stream,$Path)
  $sha=[Security.Cryptography.SHA256]::Create()
  try{$hash=[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  [pscustomobject]@{Sha256=$hash;Value=([Text.UTF8Encoding]::new($false,$true).GetString($raw)|ConvertFrom-Json)}
 }finally{$stream.Dispose()}
}

function Initialize-HeldProcessWitness {
 if('CoChemHeldObservationProcess' -as [type]){return}
 Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
public sealed class CoChemHeldObservationProcess : IDisposable {
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr p,out long c,out long e,out long k,out long u);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageNameW(IntPtr p,uint flags,StringBuilder value,ref uint length);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr p,out uint code);
 [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr p,uint access,out IntPtr token);
 IntPtr handle;
 public readonly int Pid;public readonly long CreationFiletime;public readonly string Image;public readonly string Sid;
 public CoChemHeldObservationProcess(int pid) {
  if(pid<=0)throw new ArgumentException("pid");Pid=pid;handle=OpenProcess(0x1000,false,(uint)pid);
  if(handle==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());
  try {
   long end,kernel,user;if(!GetProcessTimes(handle,out CreationFiletime,out end,out kernel,out user))throw new Win32Exception(Marshal.GetLastWin32Error());
   uint size=32768;var image=new StringBuilder((int)size);if(!QueryFullProcessImageNameW(handle,0,image,ref size))throw new Win32Exception(Marshal.GetLastWin32Error());Image=image.ToString();
   IntPtr token;if(!OpenProcessToken(handle,8,out token))throw new Win32Exception(Marshal.GetLastWin32Error());
   try {using(var identity=new WindowsIdentity(token)){Sid=identity.User.Value;}}finally{CloseHandle(token);}
   AssertLive();
  }catch {Dispose();throw;}
 }
 public void AssertLive(){uint code;if(handle==IntPtr.Zero||!GetExitCodeProcess(handle,out code))throw new Win32Exception(Marshal.GetLastWin32Error());if(code!=259)throw new InvalidOperationException("Process ended");}
 public void Dispose(){if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;}}
}
'@
}

function Get-HeldObservationInputs {
 $staging=Read-HeldObservationControl (Join-Path $script:stagingRoot 'controls\staging-receipt.json')
 $s=$staging.Value
 if($s.schema -cne 'cochem-independent-supervisor-staging/1' -or $s.status -cne 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED' -or $s.root -cne $script:stagingRoot -or $s.helper_sha256 -cne $script:stagingHelperHash -or $s.manifest_sha256 -cne $script:manifestHash -or $s.phase -cne 'complete' -or $s.paired_evidence_sha256 -cne $script:evidenceHash -or $s.activation_ready -ne $false -or $s.paid_repair_enabled -ne $false -or $s.component_recovery_enabled -ne $false -or $s.provider_or_model_calls -ne 0 -or $s.nonce -cnotmatch '^[a-f0-9]{32}$'){throw 'Staging receipt is not an accepted unpublished held pair.'}
 $commission=Read-HeldObservationControl (Join-Path $script:commissionRoot 'commissioning.json')
 $c=$commission.Value
 if($c.schema -cne 'cochem-warden-commissioning/1' -or $c.status -cne 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED' -or $c.runtime_root -cne $script:pipelineRoot -or $c.install_receipt_sha256 -cne $script:installHash -or $c.config_sha256 -cne $script:configHash -or $c.source_manifest_sha256 -cne $script:r3ManifestHash -or $c.revision_sha256 -cne $script:revisionHash -or $c.full_srs_acceptance -ne $false -or $c.model_jobs_submitted -ne 0 -or $c.nonce -cnotmatch '^[a-f0-9]{32}$' -or $c.input_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Commissioning receipt does not bind current r3.'}
 [pscustomobject]@{Packet=[ordered]@{schema='cochem-held-supervisor-inputs/1';staging_receipt_sha256=$staging.Sha256;commissioning_receipt_sha256=$commission.Sha256};Staging=$s;Commission=$c}
}

function Assert-HeldObservationRuntime {
 param([switch]$FullCustody)
 $manifest=Read-HeldObservationControl (Join-Path $script:stagingRoot 'controls\staging-manifest.json') $script:manifestHash 262144
 $m=$manifest.Value
 if($m.schema -cne 'cochem-independent-supervisor-staging-freeze/1' -or $m.complete_source_freeze -ne $true -or $m.activation_authorized -ne $false -or $m.target_root -cne $script:stagingRoot -or @($m.source_files).Count -ne 166 -or @($m.acceptance_files).Count -ne 126 -or @($m.control_files).Count -ne 3){throw 'Independent staged inventory differs.'}
 foreach($group in @([pscustomobject]@{root=(Join-Path $script:stagingRoot 'source');rows=$m.source_files},[pscustomobject]@{root=(Join-Path $script:stagingRoot 'acceptance');rows=$m.acceptance_files},[pscustomobject]@{root=(Join-Path $script:stagingRoot 'controls');rows=$m.control_files})){
  foreach($row in @($group.rows)){$path=Join-Path $group.root $row.relative;$script:held.Add((Open-VerifiedFile $path $row.sha256 $row.bytes))}
 }
 $null=Read-HeldObservationControl (Join-Path $script:pipelineRoot 'pipeline.json') $script:configHash 1048576
 $script:held.Add((Open-VerifiedFile $script:python '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e' (Get-Item -LiteralPath $script:python).Length))
 $script:held.Add((Open-VerifiedFile $script:basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' (Get-Item -LiteralPath $script:basePython).Length))
 # Imported exact uv six-key/launcher verifier uses script:python as base.
 $saved=$script:python;try{$script:python=$script:basePython;$null=Assert-NewRuntimeInterpreter $script:stagingRoot}finally{$script:python=$saved}
 if($FullCustody){$null=Assert-CodeTreeOnce $script:stagingRoot;$null=Assert-CodeTreeOnce (Split-Path -Parent $script:basePython)}
}

function Assert-HeldTaskDefinition {
 param($Task,[string]$Arguments,[bool]$Daemon)
 $d=$Task.Definition;$expectedLimit=if($Daemon){'PT0S'}else{'PT6M'}
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Actions.Count -ne 1 -or $d.Actions.Item(1).Type -ne 0 -or $d.Actions.Item(1).Path -cne $script:python -or $d.Actions.Item(1).Arguments -cne $Arguments -or $d.Actions.Item(1).WorkingDirectory -cne $script:targetRoot -or $d.Settings.AllowDemandStart -ne $true -or $d.Settings.ExecutionTimeLimit -cne $expectedLimit -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or $d.Triggers.Count -ne $(if($Daemon){1}else{0})){throw 'Owned observation task definition differs.'}
 Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7)) $Task.Name
 if($Daemon -and ($d.Triggers.Item(1).Type -ne 8 -or -not $d.Triggers.Item(1).Enabled)){throw 'Only the independent observer boot trigger is allowed.'}
}

function Assert-HeldObservationPriorTasks {
 param($Folder,$Inputs)
 $t=Get-StagingTaskOrAbsent $Folder 'CoChem-4.2.7-StageIndependentSupervisor-20261007-v1'
 if($null -eq $t){throw 'Completed staging task is missing.'};Assert-CompletedKnowledgeTask $t
 $expected='-I -B "'+(Join-Path $script:stagingRoot 'controls\stage-independent-supervisor-holds-r3-v1.py')+'" --execute --nonce '+$Inputs.Staging.nonce+' --manifest-sha256 '+$script:manifestHash
 if($t.LastTaskResult -ne 0 -or $t.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $t.Definition.Actions.Count -ne 1 -or $t.Definition.Actions.Item(1).Path -cne $script:python -or $t.Definition.Actions.Item(1).Arguments -cne $expected -or $t.Definition.Triggers.Count -ne 0){throw 'Prior staging task does not bind its receipt.'}
 $t=Get-StagingTaskOrAbsent $Folder 'CoChem-4.2.7-WardenCommissioning-r3-v1'
 if($null -eq $t){throw 'Completed first-start task is missing.'};Assert-CompletedKnowledgeTask $t
 $expected='-I -B "'+(Join-Path $script:commissionRoot 'commission-first-warden-r3-v1.py')+'" --nonce '+$Inputs.Commission.nonce+' --input-sha256 '+$Inputs.Commission.input_sha256
 if($t.LastTaskResult -ne 0 -or $t.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $t.Definition.Actions.Count -ne 1 -or $t.Definition.Actions.Item(1).Path -cne (Join-Path $script:pipelineRoot '.venv\Scripts\python.exe') -or $t.Definition.Actions.Item(1).Arguments -cne $expected -or $t.Definition.Triggers.Count -ne 0){throw 'First-start task does not bind its receipt.'}
}

function Write-HeldObservationControl {
 param([string]$Path,$Value)
 Assert-ProtectedPath (Split-Path -Parent $Path)
 $raw=[Text.UTF8Encoding]::new($false).GetBytes(($Value|ConvertTo-Json -Depth 12))
 if($raw.Length -gt 65536){throw 'Observation control is oversized.'}
 $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-CodeAcl $false))
 try{$stream.Write($raw,0,$raw.Length);$stream.Flush($true)}finally{$stream.Dispose()}
 $hash=(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant();$script:held.Add((Open-VerifiedFile $Path $hash $raw.Length));return $hash
}

function New-HeldObservationTask {
 param($Scheduler,$Folder,[string]$Name,[string]$Arguments,[bool]$Daemon)
 if($null -ne (Get-StagingTaskOrAbsent $Folder $Name)){throw 'Preserve existing observation task; no reuse.'}
 $d=$Scheduler.NewTask(0);$d.RegistrationInfo.Description='Independent held observation only; no Warden or paid/component actuation.'
 $d.Principal.UserId='SYSTEM';$d.Principal.LogonType=5;$d.Principal.RunLevel=1
 $d.Settings.Enabled=$false;$d.Settings.AllowDemandStart=$true;$d.Settings.MultipleInstances=2;$d.Settings.RestartCount=0;$d.Settings.ExecutionTimeLimit=if($Daemon){'PT0S'}else{'PT6M'}
 if($Daemon){$trigger=$d.Triggers.Create(8);$trigger.Enabled=$true}
 $a=$d.Actions.Create(0);$a.Path=$script:python;$a.Arguments=$Arguments;$a.WorkingDirectory=$script:targetRoot
 $task=$Folder.RegisterTaskDefinition($Name,$d,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
 Assert-HeldTaskDefinition $task $Arguments $Daemon
 if($task.Enabled -ne $false -or $task.State -ne 1 -or $task.Definition.Settings.Enabled -ne $false -or $task.GetInstances(0).Count -ne 0){throw 'New observation task must be disabled and never started.'}
 return $task
}

function Assert-HeldProvisionReceipt {
 param($Receipt,$Inputs,[string]$Nonce,[string]$InputsHash,$Task)
 if($Receipt.schema -cne 'cochem-held-supervisor-provision/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.helper_sha256 -cne $script:sourceHash -or $Receipt.root -cne $script:targetRoot -or $Receipt.data_root -cne $script:dataRoot -or $Receipt.inputs_sha256 -cne $InputsHash -or $Receipt.model_jobs_submitted -ne 0){throw 'Observation provisioning receipt binding differs.'}
 foreach($field in @('paid_repair_enabled','component_recovery_enabled','existing_warden_changed','existing_pipeline_pointer_changed','old_ledgers_opened','daemon_running','full_srs_acceptance')){if($Receipt.$field -isnot [bool] -or $Receipt.$field -ne $false){throw 'Observation provisioning exceeded its authority.'}}
 if($Receipt.partial_outputs_preserved -ne $true){throw 'Provisioning preservation claim differs.'}
 if($Receipt.status -ceq 'HELD_OBSERVATION_PROVISIONED'){
  if($Task.LastTaskResult -ne 0 -or $Receipt.phase -cne 'complete' -or $Receipt.observation_verified -ne $true -or $Receipt.staging_receipt_sha256 -cne $Inputs.Packet.staging_receipt_sha256 -or $Receipt.commissioning_receipt_sha256 -cne $Inputs.Packet.commissioning_receipt_sha256 -or $Receipt.configuration_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Provisioning success lacks bound observation.'};return
 }
 if($Receipt.status -cne 'OBSERVATION_PROVISION_HELD' -or $Task.LastTaskResult -ne 2){throw 'Provisioning task has an unrecognized outcome.'}
 $failure=Get-HeldObservationFailure $Receipt.failure
 [ordered]@{schema='cochem-held-supervisor-failure/1';status='OBSERVATION_PROVISION_HELD';failure=$failure;partial_outputs_preserved=$true}|ConvertTo-Json -Compress|Write-Host
 throw 'Bound provisioning failure was reported; preserve all outputs and do not repeat.'
}

function Get-HeldObservationFailure {
 param($Failure)
 $phases=@('bindings','fresh_boundary','repair_identity_precheck','dedicated_identity_layout','closed_held_pair_copy','observation_configuration','held_constructor_and_observation','preservation','observation_cycle')
 $codes=@('REPARSE_CONTROL','NONORDINARY_CONTROL','STAGING_BINDING','COMMISSION_BINDING','OVERSIZED_SUPPORT','STAGING_HELPER_CHANGED','PAIR_SOURCE_RECEIPT','PRESERVE_TARGET_PRIVATE_STATE','COPIED_PAIR_DIFFERS','ORIGINAL_PAIR_CHANGED','OBSERVATION_REQUIRES_UNRESOLVED_AUTHORITY','PAIR_AUTHORITY_DIFFERS','GENERIC_GUARD_DID_NOT_HOLD','ACTUATION_FORBIDDEN','INVALID_INVOCATION','INVALID_INPUTS','SOURCE_AUTHORITY','PRESERVE_EXISTING_DATA_ROOT','PRESERVE_EXISTING_CONTROL','PRESERVE_RELEASE_ROOT','OBSERVATION_NOT_HELD','REPORT_TOO_LARGE','NOT_PROVISIONED','OBSERVATION_CONTRACT','REPAIR_IDENTITY_MIXED_STATE','REPAIR_CREDENTIAL_USERNAME_DIFFERS','REPAIR_ACCOUNT_QUERY_FAILED','OBSERVATION_DURATION_INVALID')
 if($Failure.phase -notin $phases -or $Failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$' -or ($null -ne $Failure.code -and $Failure.code -notin $codes) -or ($null -ne $Failure.winerror -and ($Failure.winerror -isnot [int] -or $Failure.winerror -lt 0 -or $Failure.winerror -gt 65535))){throw 'Failure metadata is not an allowed projection.'}
 return [ordered]@{phase=$Failure.phase;error_type=$Failure.error_type;winerror=$Failure.winerror;code=$Failure.code}
}

function Wait-HeldObservationRunning {
 param($Folder,[string]$Arguments,$Provision,[string]$InstanceGuid)
 $deadline=[DateTime]::UtcNow.AddSeconds(50)
 do{
  $task=$Folder.GetTask($script:observerTask);Assert-HeldTaskDefinition $task $Arguments $true
  if((Test-HeldObservationPresence (Join-Path $script:targetRoot 'observation-runtime.json'))){
   $control=Read-HeldObservationRuntime (Join-Path $script:targetRoot 'observation-runtime.json');$v=$control.Value
   if($v.schema -cne 'cochem-held-supervisor-runtime/1' -or $v.provision_nonce -cne $Provision.nonce -or $v.configuration_sha256 -cne $Provision.configuration_sha256 -or $v.inputs_sha256 -cne $Provision.inputs_sha256 -or $v.helper_sha256 -cne $script:sourceHash -or $v.service_identity -cne 'SYSTEM' -or $v.instance_id -cnotmatch '^[a-f0-9]{32}$'){throw 'Daemon runtime evidence is not bound.'}
   foreach($key in @('paid_repair_enabled','component_recovery_enabled','warden_actuation_enabled','full_srs_acceptance')){if($v.$key -isnot [bool] -or $v.$key){throw 'Daemon runtime exceeded observation scope.'}}
   if($v.status -ceq 'HELD_OBSERVATION_FAILED'){$f=Get-HeldObservationFailure $v.failure;@{schema='cochem-held-supervisor-failure/1';status=$v.status;failure=$f}|ConvertTo-Json -Compress|Write-Host;throw 'Independent observation failed; preserve its evidence.'}
   $instances=$task.GetInstances(0)
   if($v.status -cne 'HELD_SUPERVISOR_OBSERVATION_RUNNING' -or $v.pid -isnot [int] -or $v.pid -le 0 -or $v.sequence -lt 1 -or -not $task.Enabled -or $task.State -ne 4 -or $instances.Count -ne 1 -or $instances.Item(1).InstanceGuid -cne $InstanceGuid){throw 'Observation task is not the exact owned running instance.'}
   $now=([DateTime]::UtcNow-[DateTime]'1970-01-01T00:00:00Z').TotalSeconds
   if([Math]::Abs($now-[double]$v.checked_at) -gt 30){throw 'Observation heartbeat is stale.'}
   Initialize-HeldProcessWitness;$witness=[CoChemHeldObservationProcess]::new([int]$v.pid)
   try{
    if($witness.Sid -cne 'S-1-5-18' -or -not $witness.Image.Equals($script:basePython,[StringComparison]::OrdinalIgnoreCase) -or $v.process_creation_filetime -isnot [long] -or $witness.CreationFiletime -ne $v.process_creation_filetime){throw 'Observer native process identity differs.'}
    $witness.AssertLive();$script:processWitnesses.Add($witness);$witness=$null
   }finally{if($null -ne $witness){$witness.Dispose()}}
   return [pscustomobject]@{Control=$control;Runtime=$v;NativeProcess=[ordered]@{pid=[int]$v.pid;system_sid='S-1-5-18';creation_filetime=$v.process_creation_filetime;held_native_handle=$true;task_instance_guid=$InstanceGuid;image_sha256='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'}}
  }
  if($task.State -notin @(2,4)){throw 'Observer ended before a bound heartbeat; preserve its task.'}
  Start-Sleep -Milliseconds 500
 }while([DateTime]::UtcNow -lt $deadline)
 throw 'Observer startup is ambiguous after bounded wait; preserve task and state, no retry.'
}

function Invoke-HeldObservationInstall {
 param($Scheduler,$Folder,$Inputs)
 $mutex=[Threading.Mutex]::new($false,'Global\CoChem427-HeldSupervisorObservation-v1');$locked=$false
 try{
  try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true};if(-not $locked){throw 'Observation installer already owns this namespace.'}
  Assert-AbsentStagingPath $script:targetRoot;Assert-AbsentStagingPath $script:dataRoot
  foreach($name in @($script:provisionTask,$script:observerTask)){if($null -ne (Get-StagingTaskOrAbsent $Folder $name)){throw 'Preserve existing observation task.'}}
  Assert-HeldObservationPriorTasks $Folder $Inputs;$before=Get-PreservedDaemonDefinitions $Folder
  Assert-HeldObservationRuntime -FullCustody
  $fresh=Get-HeldObservationInputs
  if(($fresh.Packet|ConvertTo-Json -Compress) -cne ($Inputs.Packet|ConvertTo-Json -Compress)){throw 'Prerequisite receipts changed.'}
  $script:phase='copy';New-ProtectedDirectory $script:targetRoot;New-ProtectedDirectory (Join-Path $script:targetRoot 'releases')
  $record=[pscustomobject]@{source=$script:source;destination=(Join-Path $script:targetRoot 'held-supervisor-observation-r3-v1.py');sha256=$script:sourceHash;length=(Get-Item -LiteralPath $script:source).Length}
  Copy-VerifiedPayload $record;$script:held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))
  $inputsHash=Write-HeldObservationControl (Join-Path $script:targetRoot 'inputs.json') $Inputs.Packet;$nonce=[Guid]::NewGuid().ToString('N')
  $args='-I -B "'+$record.destination+'" --prepare --nonce '+$nonce+' --inputs-sha256 '+$inputsHash
  $script:phase='provision_task';$task=New-HeldObservationTask $Scheduler $Folder $script:provisionTask $args $false
  $task.Enabled=$true;$instance=$task.Run($null);Wait-KnowledgeTask $instance -TimeoutSeconds 370
  $task=$Folder.GetTask($script:provisionTask);Assert-CompletedKnowledgeTask $task;Assert-HeldTaskDefinition $task $args $false
  $control=Read-HeldObservationControl (Join-Path $script:targetRoot 'provisioning.json');Assert-HeldProvisionReceipt $control.Value $Inputs $nonce $inputsHash $task
  Assert-DaemonDefinitionsUnchanged $before $Folder
  $script:phase='observer_task';$args='-I -B "'+$record.destination+'" --observe'
  $task=New-HeldObservationTask $Scheduler $Folder $script:observerTask $args $true
  $null=Write-HeldObservationControl (Join-Path $script:targetRoot 'start-intent.json') ([ordered]@{schema='cochem-held-observer-start-intent/1';nonce=$nonce;inputs_sha256=$inputsHash;provisioning_receipt_sha256=$control.Sha256;task=$script:observerTask;automatic_retry_allowed=$false})
  $task.Enabled=$true;$instance=$task.Run($null)
  $observed=Wait-HeldObservationRunning $Folder $args $control.Value ([string]$instance.InstanceGuid)
  Assert-DaemonDefinitionsUnchanged $before $Folder
  $result=[ordered]@{schema='cochem-held-supervisor-activation-result/1';status='HELD_SUPERVISOR_OBSERVATION_RUNNING';root=$script:targetRoot;staging_receipt_sha256=$Inputs.Packet.staging_receipt_sha256;commissioning_receipt_sha256=$Inputs.Packet.commissioning_receipt_sha256;provisioning_receipt_sha256=$control.Sha256;runtime_receipt_sha256=$observed.Control.Sha256;native_process=$observed.NativeProcess;paid_repair_enabled=$false;component_recovery_enabled=$false;existing_warden_changed=$false;full_srs_acceptance=$false;partial_outputs_preserved=$true}
  $receipt=Join-Path $script:targetRoot 'activation.json';$digest=Write-HeldObservationControl $receipt $result
  $result.receipt_path=$receipt;$result.receipt_sha256=$digest;$result|ConvertTo-Json -Depth 8
 }finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
}

# Invocation boundary. Source pin is finalized only after independent review.
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security','CimCmdlets')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use64-bit Windows PowerShell5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner elevated; default preview does not.'}
$stagingRoot='C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1';$targetRoot='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1'
$dataRoot='C:\ProgramData\CoChemSupervisor427-observation-20261007-r3-v1';$pipelineRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$commissionRoot='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1';$python=Join-Path $stagingRoot '.venv\Scripts\python.exe'
$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$observerTask='CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1';$provisionTask='CoChem-4.2.7-ProvisionHeldSupervisor-20261007-r3-v1'
$source=Join-Path $PSScriptRoot 'held-supervisor-observation-r3-v1.py';$sourceHash='d2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640'
$manifestHash='6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4';$stagingHelperHash='215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
$r3ManifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4';$evidenceHash='1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177'
$programFiles='C:\Program Files';$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions';$held=[Collections.Generic.List[IO.Stream]]::new();$processWitnesses=[Collections.Generic.List[IDisposable]]::new();$phase='preconditions'
try{
 $support=Join-Path $PSScriptRoot 'install-independent-supervisor-staging-r3-v2.ps1';$raw=[IO.File]::ReadAllBytes($support)
 $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 if($hash -cne '00412dfa8d7d6668713f9fb850c6fc4a2dd4df4dcc1527c65106ce7497be893d'){throw 'Frozen staging support changed.'}
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::UTF8.GetString($raw),[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'Staging support did not parse.'}
 foreach($name in @('Import-StagingFunctions','Assert-AbsentStagingPath','Get-StagingTaskOrAbsent','Get-PreservedDaemonDefinitions','Assert-DaemonDefinitionsUnchanged')){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true));if($nodes.Count -ne 1){throw 'Missing staging support function.'};. ([scriptblock]::Create($nodes[0].Extent.Text))}
 $dependencies=@(
  [pscustomobject]@{path=(Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1');hash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b';names=@('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'install-stopped-runtime-r3.ps1');hash='372a0fe352124e81a2d097ebe7c075369f6f20275a8a2858f78f41febbc723e4';names=@('Read-HeldText','Assert-UvVenvText','Assert-NewRuntimeInterpreter')},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1');hash='5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a';names=@('Wait-KnowledgeTask','Assert-CompletedKnowledgeTask')},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1');hash='5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d';names=@('Assert-CodeTreeOnce')},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'task-private-acl-v8.ps1');hash='f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47';names=@('Assert-RegisteredTaskAcl')})
 foreach($d in $dependencies){foreach($f in @(Import-StagingFunctions $d.path $d.hash $d.names)){. ([scriptblock]::Create($f))}}
 Initialize-FileIdentity
 $held.Add((Open-VerifiedFile $support $hash $raw.Length));foreach($d in $dependencies){$held.Add((Open-VerifiedFile $d.path $d.hash (Get-Item -LiteralPath $d.path).Length))}
 Assert-AbsentStagingPath $targetRoot;Assert-AbsentStagingPath $dataRoot
 $holds=@();$stagingPresent=Test-HeldObservationPresence (Join-Path $stagingRoot 'controls\staging-receipt.json');$commissionPresent=Test-HeldObservationPresence (Join-Path $commissionRoot 'commissioning.json')
 if(-not $stagingPresent){$holds+='STAGING_NOT_INSTALLED'}else{Assert-HeldObservationRuntime}
 if(-not $commissionPresent){$holds+='COMMISSIONING_NOT_COMPLETE'}
 if($sourceHash -cnotmatch '^[a-f0-9]{64}$'){$holds+='SOURCE_NOT_FROZEN'}else{$held.Add((Open-VerifiedFile $source $sourceHash (Get-Item -LiteralPath $source).Length))}
 $inputs=$null;if($stagingPresent -and $commissionPresent){$inputs=Get-HeldObservationInputs}
 $scheduler=$null;$folder=$null
 if($admin){$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');foreach($name in @($provisionTask,$observerTask)){if($null -ne (Get-StagingTaskOrAbsent $folder $name)){throw 'Preserve existing held observation task.'}};if($null -ne $inputs){Assert-HeldObservationPriorTasks $folder $inputs}}
 $plan=[ordered]@{schema='cochem-held-supervisor-observation-plan/1';mode='READ_ONLY_PLAN';staging_present=$stagingPresent;commissioning_present=$commissionPresent;target_root=$targetRoot;data_root=$dataRoot;task_metadata_and_private_identity_checks_deferred_to_system=(-not $admin);paid_repair_enabled=$false;component_recovery_enabled=$false;warden_stop_required=$false;oracle_acceptance_prerequisite=$false;holds=$holds}
 if(-not $Apply){$plan|ConvertTo-Json -Depth 6;return}
 if($holds.Count){throw 'Held observation prerequisites are incomplete; no installation started.'}
 Invoke-HeldObservationInstall $scheduler $folder $inputs
}catch{
 $failure=[ordered]@{schema='cochem-held-supervisor-installer-failure/1';status='HELD_OBSERVATION_INSTALLER_HELD';phase=$phase;error_type=$_.Exception.GetType().Name;partial_outputs_preserved=$true;automatic_retry_allowed=$false;model_jobs_submitted=0}
 if((Get-Command Write-HeldObservationControl -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $targetRoot)){
  try{$path=Join-Path $targetRoot 'installer-failure.json';$failure.receipt_path=$path;$failure.receipt_sha256=Write-HeldObservationControl $path $failure}catch{}
 }
 $failure|ConvertTo-Json -Compress|Write-Host
 throw 'Held observation installation failed; bounded failure metadata is shown. Preserve all partial outputs; do not repeat.'
}finally{foreach($witness in $processWitnesses){$witness.Dispose()};foreach($stream in $held){$stream.Dispose()}}
