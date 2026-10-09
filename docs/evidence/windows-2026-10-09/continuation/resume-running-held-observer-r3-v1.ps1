#Requires -Version 5.1
<# Reattest the existing running observer after the verified local-epoch bug.
   Apply publishes only its missing activation receipt and a fresh recovery
   journal. No task creation, modification, start, stop, retry or provisioning. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-RunningHeldFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names,[switch]$CorrectUtcWait)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'HELD_RECOVERY_SUPPORT_CHANGED'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'HELD_RECOVERY_SUPPORT_PARSE'}
  foreach($name in $Names){
   $nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name})
   if($nodes.Count -ne 1){throw 'HELD_RECOVERY_SUPPORT_DEFINITION'};$definition=$nodes[0].Extent.Text
   if($name -ceq 'Wait-HeldObservationRunning'){
    if(-not $CorrectUtcWait){throw 'HELD_RECOVERY_UTC_CORRECTION_REQUIRED'}
    $old="([DateTime]::UtcNow-[DateTime]'1970-01-01T00:00:00Z').TotalSeconds"
    if(($definition.Split(@($old),[StringSplitOptions]::None)).Count -ne 2){throw 'HELD_RECOVERY_UTC_SOURCE_DIFFERS'}
    $definition=$definition.Replace($old,'([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)')
   }
   $definition
  }
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Assert-RunningHeldAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'HELD_RECOVERY_ABSENCE_UNKNOWN'}
 throw 'HELD_RECOVERY_EXISTING_OUTPUT_PRESERVED'
}

function Get-RunningHeldTasksSnapshot {
 param($Folder)
 $rows=@()
 foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor','CoChem-4.2.7-StageIndependentSupervisor-20261007-v1','CoChem-4.2.7-WardenCommissioning-r3-v1',$script:provisionTask,$script:observerTask)){
  $task=Get-RegistrationTask $Folder $name
  if($null -eq $task){$rows+=@([ordered]@{name=$name;present=$false});continue}
  $instances=$task.GetInstances(0);$guid=$null
  if($instances.Count -gt 1){throw 'HELD_RECOVERY_MULTIPLE_TASK_INSTANCES'}
  if($instances.Count -eq 1){$guid=[string]$instances.Item(1).InstanceGuid}
  $sha=[Security.Cryptography.SHA256]::Create();try{$xml=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::Unicode.GetBytes([string]$task.Xml))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $sha=[Security.Cryptography.SHA256]::Create();try{$acl=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$task.GetSecurityDescriptor(7)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $rows+=@([ordered]@{name=$name;present=$true;enabled=[bool]$task.Enabled;state=[int]$task.State;last_task_result=[long]$task.LastTaskResult;instances=[int]$instances.Count;instance_guid=$guid;xml_sha256=$xml;acl_sha256=$acl})
 }
 return ($rows|ConvertTo-Json -Depth 5 -Compress)
}

function Assert-RunningHeldTask {
 param($Folder,[string]$ExpectedGuid='')
 $task=Get-RegistrationTask $Folder $script:observerTask
 if($null -eq $task -or $task.Name -cne $script:observerTask){throw 'HELD_RECOVERY_OBSERVER_TASK_MISSING'}
 $args='-I -B "'+(Join-Path $script:targetRoot 'held-supervisor-observation-r3-v1.py')+'" --observe'
 Assert-HeldTaskDefinition $task $args $true
 $instances=$task.GetInstances(0)
 if($task.Enabled -isnot [bool] -or -not $task.Enabled -or $task.State -ne 4 -or $task.Definition.Settings.Enabled -ne $true -or $instances.Count -ne 1){throw 'HELD_RECOVERY_OBSERVER_NOT_ONE_RUNNING_INSTANCE'}
 $guid=[string]$instances.Item(1).InstanceGuid
 if($guid -cnotmatch '\A\{[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\}\z' -or ($ExpectedGuid -and $guid -cne $ExpectedGuid)){throw 'HELD_RECOVERY_OBSERVER_INSTANCE_CHANGED'}
 [pscustomobject]@{Task=$task;Arguments=$args;Guid=$guid}
}

function Assert-RunningHeldRuntimeIdentity {
 param($Value,[long]$AfterSequence=0)
 if($Value.pid -isnot [int] -or $Value.pid -ne 24608 -or $Value.instance_id -cne 'a0136f033e6b4045a484cbd3c9828cad' -or
    $Value.process_creation_filetime -isnot [long] -or $Value.process_creation_filetime -ne 134359860348047610 -or
    ($Value.sequence -isnot [int] -and $Value.sequence -isnot [long]) -or $Value.sequence -le $AfterSequence){throw 'HELD_RECOVERY_RUNTIME_IDENTITY_OR_PROGRESS'}
 foreach($n in @($Value.checked_at)){
  if(($n -isnot [double] -and $n -isnot [int] -and $n -isnot [long] -and $n -isnot [decimal]) -or [double]::IsNaN([double]$n) -or [double]::IsInfinity([double]$n)){throw 'HELD_RECOVERY_RUNTIME_CLOCK_TYPE'}
 }
 if([Math]::Abs([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0-[double]$Value.checked_at) -gt 30){throw 'HELD_RECOVERY_RUNTIME_STALE'}
}

function Get-RunningHeldPreflight {
 param($Folder)
 Assert-RunningHeldAbsent (Join-Path $script:targetRoot 'activation.json');Assert-RunningHeldAbsent $script:recoveryRoot
 foreach($pin in @(
  @((Join-Path $script:targetRoot 'installer-failure.json'),'b2012c0ec8bbfaaa2982020915ec693321bc858ff71f4839c19bb5ba4e5b5d0b'),
  @((Join-Path $script:targetRoot 'inputs.json'),'e65f44e1396a1f02802372a69579008e118ef93d1987d8edf7301a45b1218c61'),
  @((Join-Path $script:targetRoot 'supervisor.json'),'e3f2ce4d4ace15f6428c181c158507f286ce974a0e2fefd6757c350c4744d49f'),
  @((Join-Path $script:targetRoot 'provisioning.json'),'beff7c79325f1c123ba7ae01aba4e810c033769063ba4aae5f9a9c5a67656e50'),
  @((Join-Path $script:targetRoot 'start-intent.json'),'3800a4a09d7513d074bb3d0928f15d216d03b48bb4d86a349d3b32e952e65c1b'),
  @((Join-Path $script:stagingRoot 'controls\staging-receipt.json'),'32697215f4d9d62573371a9185fff6920fa84d34a8b066f5a183673ec9500f1d'),
  @((Join-Path $script:commissionRoot 'commissioning.json'),'4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3'))){$null=Read-HeldObservationControl $pin[0] $pin[1] 1048576}
 # Preserve the original Python source under a verified held handle. It is
 # byte-pinned, never parsed as JSON or executed by this continuation.
 $source=Join-Path $script:targetRoot 'held-supervisor-observation-r3-v1.py';Assert-ProtectedPath $source
 $item=Get-Item -LiteralPath $source -Force
 if($item.PSIsContainer -or $item.Length -le 0 -or $item.Length -gt 1048576){throw 'HELD_RECOVERY_SOURCE_SIZE_OR_TYPE'}
 $script:held.Add((Open-VerifiedFile $source $script:sourceHash $item.Length))
 $inputs=Get-HeldObservationInputs;Assert-HeldObservationPriorTasks $Folder $inputs
 $provision=Read-HeldObservationControl (Join-Path $script:targetRoot 'provisioning.json') 'beff7c79325f1c123ba7ae01aba4e810c033769063ba4aae5f9a9c5a67656e50'
 $task=Get-RegistrationTask $Folder $script:provisionTask;Assert-CompletedKnowledgeTask $task
 if($null -eq $task -or $task.Name -cne $script:provisionTask){throw 'HELD_RECOVERY_PROVISION_TASK_MISSING'}
 $prepareArgs='-I -B "'+$source+'" --prepare --nonce 10c0d0613b1740108e38138630b6b4ad --inputs-sha256 e65f44e1396a1f02802372a69579008e118ef93d1987d8edf7301a45b1218c61'
 Assert-HeldTaskDefinition $task $prepareArgs $false
 Assert-HeldProvisionReceipt $provision.Value $inputs '10c0d0613b1740108e38138630b6b4ad' 'e65f44e1396a1f02802372a69579008e118ef93d1987d8edf7301a45b1218c61' $task
 $owned=Assert-RunningHeldTask $Folder
 $controller=Open-SetupControllerWitness $Folder;$script:controllerWitness=$controller
 Assert-HeldObservationRuntime -FullCustody
 $before=Get-RunningHeldTasksSnapshot $Folder
 $first=Wait-HeldObservationRunning $Folder $owned.Arguments $provision.Value $owned.Guid
 Assert-RunningHeldRuntimeIdentity $first.Runtime
 $deadline=[DateTime]::UtcNow.AddSeconds(50);$second=$null
 do{
  Assert-SetupCurrentWitness $controller $Folder;$null=Assert-RunningHeldTask $Folder $owned.Guid
  Start-Sleep -Milliseconds 500
  $candidate=Wait-HeldObservationRunning $Folder $owned.Arguments $provision.Value $owned.Guid
  Assert-RunningHeldRuntimeIdentity $candidate.Runtime
  if($candidate.Runtime.sequence -gt $first.Runtime.sequence){$second=$candidate;break}
 }while([DateTime]::UtcNow -lt $deadline)
 if($null -eq $second){throw 'HELD_RECOVERY_NO_HEARTBEAT_PROGRESS'}
 Assert-RunningHeldRuntimeIdentity $second.Runtime ([long]$first.Runtime.sequence)
 Assert-SetupCurrentWitness $controller $Folder
 if((Get-RunningHeldTasksSnapshot $Folder) -cne $before){throw 'HELD_RECOVERY_TASKS_CHANGED'}
 [pscustomobject]@{Inputs=$inputs;Provision=$provision;Owned=$owned;First=$first;Second=$second;Tasks=$before;Controller=$controller}
}

function Invoke-RunningHeldRecovery {
 param($Folder,$Preflight)
 Assert-RunningHeldAbsent (Join-Path $script:targetRoot 'activation.json');Assert-RunningHeldAbsent $script:recoveryRoot
 Assert-SetupCurrentWitness $Preflight.Controller $Folder;$null=Assert-RunningHeldTask $Folder $Preflight.Owned.Guid
 Assert-RunningHeldRuntimeIdentity $Preflight.Second.Runtime ([long]$Preflight.First.Runtime.sequence)
 if((Get-RunningHeldTasksSnapshot $Folder) -cne $Preflight.Tasks){throw 'HELD_RECOVERY_TASKS_CHANGED'}
 New-ProtectedDirectory $script:recoveryRoot
 $intent=[ordered]@{schema='cochem-running-held-observer-recovery-intent/1';status='RECEIPT_PUBLICATION_ONLY';failure_receipt_sha256='b2012c0ec8bbfaaa2982020915ec693321bc858ff71f4839c19bb5ba4e5b5d0b';provisioning_receipt_sha256=$Preflight.Provision.Sha256;task_instance_guid=$Preflight.Owned.Guid;first_sequence=$Preflight.First.Runtime.sequence;final_sequence=$Preflight.Second.Runtime.sequence;pid=$Preflight.Second.Runtime.pid;instance_id=$Preflight.Second.Runtime.instance_id;tasks_before=$Preflight.Tasks;tasks_changed=$false;task_runs_requested=0;automatic_retry_allowed=$false}
 $intentHash=Write-HeldObservationControl (Join-Path $script:recoveryRoot 'recovery-intent.json') $intent
 Assert-SetupCurrentWitness $Preflight.Controller $Folder;$null=Assert-RunningHeldTask $Folder $Preflight.Owned.Guid
 if((Get-RunningHeldTasksSnapshot $Folder) -cne $Preflight.Tasks){throw 'HELD_RECOVERY_TASKS_CHANGED'}
 foreach($w in $script:processWitnesses){$w.AssertLive()}
 $result=[ordered]@{schema='cochem-held-supervisor-activation-result/1';status='HELD_SUPERVISOR_OBSERVATION_RUNNING';root=$script:targetRoot;staging_receipt_sha256=$Preflight.Inputs.Packet.staging_receipt_sha256;commissioning_receipt_sha256=$Preflight.Inputs.Packet.commissioning_receipt_sha256;provisioning_receipt_sha256=$Preflight.Provision.Sha256;runtime_receipt_sha256=$Preflight.Second.Control.Sha256;native_process=$Preflight.Second.NativeProcess;paid_repair_enabled=$false;component_recovery_enabled=$false;existing_warden_changed=$false;full_srs_acceptance=$false;partial_outputs_preserved=$true}
 $receipt=Join-Path $script:targetRoot 'activation.json';$activationHash=Write-HeldObservationControl $receipt $result
 Assert-SetupCurrentWitness $Preflight.Controller $Folder
 if((Get-RunningHeldTasksSnapshot $Folder) -cne $Preflight.Tasks){throw 'HELD_RECOVERY_TASKS_CHANGED'}
 foreach($w in $script:processWitnesses){$w.AssertLive()}
 $complete=[ordered]@{schema='cochem-running-held-observer-recovery-result/1';status='RUNNING_HELD_OBSERVER_REATTESTED_AND_ACTIVATION_PUBLISHED';activation_path=$receipt;activation_sha256=$activationHash;intent_sha256=$intentHash;tasks_changed=$false;task_runs_requested=0;model_jobs_submitted=0;databases_opened=$false;credentials_modified=$false;automatic_retry_allowed=$false;full_srs_acceptance=$false}
 $completeHash=Write-HeldObservationControl (Join-Path $script:recoveryRoot 'recovery-complete.json') $complete
 $result.receipt_path=$receipt;$result.receipt_sha256=$activationHash;$result.recovery_receipt_path=Join-Path $script:recoveryRoot 'recovery-complete.json';$result.recovery_receipt_sha256=$completeHash
 return $result
}

# Host boundary. Tests import only function definitions and use inert doubles.
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
if($PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner.'}
$programFiles='C:\Program Files';$pipelineRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3';$installRoot=$pipelineRoot
$stagingRoot='C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1';$targetRoot='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1'
$dataRoot='C:\ProgramData\CoChemSupervisor427-observation-20261007-r3-v1';$commissionRoot='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1';$commissioning=Join-Path $commissionRoot 'commissioning.json'
$recoveryRoot='C:\Program Files\CoChem\HeldObserverRecovery4.2.7-windows-20261008-r3-v1'
$python=Join-Path $stagingRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$observerTask='CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1';$provisionTask='CoChem-4.2.7-ProvisionHeldSupervisor-20261007-r3-v1'
$sourceHash='d2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640';$manifestHash='6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4';$stagingHelperHash='215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$r3ManifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$runtimeManifestHash=$r3ManifestHash;$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4';$evidenceHash='1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177'
$held=[Collections.Generic.List[IO.Stream]]::new();$processWitnesses=[Collections.Generic.List[IDisposable]]::new();$controllerWitness=$null;$locked=$false;$mutex=$null
try{
 foreach($dep in @(
  @{path='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1';hash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b';names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile')},
  @{path=(Join-Path $PSScriptRoot 'install-stopped-runtime-r3.ps1');hash='372a0fe352124e81a2d097ebe7c075369f6f20275a8a2858f78f41febbc723e4';names=@('Read-HeldText','Assert-UvVenvText','Assert-NewRuntimeInterpreter')},
  @{path=(Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1');hash='5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a';names=@('Assert-CompletedKnowledgeTask')},
  @{path=(Join-Path $PSScriptRoot 'install-independent-supervisor-staging-r3-v2.ps1');hash='00412dfa8d7d6668713f9fb850c6fc4a2dd4df4dcc1527c65106ce7497be893d';names=@('Get-StagingTaskOrAbsent')},
  @{path=(Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1');hash='eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198';names=@('Get-RegistrationTask')},
  @{path=(Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1');hash='18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7';names=@('Read-R3Control','Read-R3Text')},
  @{path=(Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1');hash='5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d';names=@('Assert-CodeTreeOnce')},
  @{path=(Join-Path $PSScriptRoot 'task-private-acl-v8.ps1');hash='f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47';names=@('Assert-RegisteredTaskAcl')},
  @{path=(Join-Path $PSScriptRoot 'install-resource-observer-r3-v5.ps1');hash='ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f';names=@('Read-ObserverCommissioning','Assert-CommissionedTaskRunning')},
  @{path=(Join-Path $PSScriptRoot 'run-post-commissioning-setup-r3-v4.ps1');hash='933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4';names=@('Initialize-SetupNativeWitness','Open-SetupControllerWitness','Assert-SetupCurrentWitness')}
 )){foreach($d in @(Import-RunningHeldFunctions $dep.path $dep.hash $dep.names)){. ([scriptblock]::Create($d))}}
 foreach($d in @(Import-RunningHeldFunctions (Join-Path $PSScriptRoot 'install-held-supervisor-observation-r3-v3.ps1') 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6' @('Read-HeldObservationControl','Test-HeldObservationPresence','Read-HeldObservationRuntime','Initialize-HeldProcessWitness','Get-HeldObservationInputs','Assert-HeldObservationRuntime','Assert-HeldTaskDefinition','Assert-HeldObservationPriorTasks','Write-HeldObservationControl','Assert-HeldProvisionReceipt','Get-HeldObservationFailure','Wait-HeldObservationRunning') -CorrectUtcWait)){. ([scriptblock]::Create($d))}
 Initialize-FileIdentity
 $holds=@();$proof=$null;$folder=$null
 try{
  $scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
  $mutex=[Threading.Mutex]::new($false,'Global\CoChem427-HeldSupervisorObservation-v1');try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
  if(-not $locked){throw 'HELD_RECOVERY_OTHER_OWNER'}
  $proof=Get-RunningHeldPreflight $folder
 }catch{$holds+='Existing running observer/controller and immutable recovery evidence require exact Administrator verification.'}
 if(-not $Apply){[ordered]@{schema='cochem-held-supervisor-observation-plan/1';mode='READ_ONLY_PLAN';staging_present=(Test-HeldObservationPresence (Join-Path $stagingRoot 'controls\staging-receipt.json'));commissioning_present=(Test-HeldObservationPresence $commissioning);target_root=$targetRoot;data_root=$dataRoot;task_metadata_and_private_identity_checks_deferred_to_system=(-not $admin);paid_repair_enabled=$false;component_recovery_enabled=$false;warden_stop_required=$false;oracle_acceptance_prerequisite=$false;holds=$holds;existing_observer_reattested=($null -ne $proof);task_runs_requested=0;activation_receipt_only=$true}|ConvertTo-Json -Depth 5;return}
 if($holds.Count -or $null -eq $proof){throw 'Running observer recovery is held; no recovery outputs were created.'}
 Invoke-RunningHeldRecovery $folder $proof|ConvertTo-Json -Depth 8
}finally{if($null -ne $controllerWitness){$controllerWitness.Handle.Dispose()};foreach($w in $processWitnesses){$w.Dispose()};if($locked){$mutex.ReleaseMutex()};if($null -ne $mutex){$mutex.Dispose()};foreach($s in $held){$s.Dispose()}}
