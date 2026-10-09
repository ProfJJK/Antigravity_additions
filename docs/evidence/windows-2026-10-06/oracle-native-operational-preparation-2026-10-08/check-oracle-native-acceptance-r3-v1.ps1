#Requires -Version 5.1
<# Default metadata plan. -Apply stages one new protected helper root and one
one-shot SYSTEM task. The child holds the actual Warden byte lock and refuses
live/enabled/uncertain daemon authorities. No stop, restart, retry or task overwrite.
Physical work is confined to a new private database fixture and nested RAM path. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-OracleFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Pinned Oracle support changed.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
  try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
  if($errors.Count){throw 'Pinned support parse error.'}
  foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($nodes.Count -ne 1){throw 'Pinned support definition missing.'};$nodes[0].Extent.Text}
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Get-OracleTask {
 param($Folder,[string]$Name)
 try{return $Folder.GetTask($Name)}catch{$e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147024894){return $null};$e=$e.InnerException};throw 'Task state is inaccessible; absence not assumed.'}
}

function Get-OracleMaintenance {
 param($Folder)
 $rows=@()
 foreach($name in $script:daemonNames){
  $task=Get-OracleTask $Folder $name
  if($null -eq $task){$rows+=@([ordered]@{name=$name;absent=$true;xml_sha256=$null;security_sha256=$null});continue}
  if($name -cnotin @('CoChem-4.2.7-Warden','CoChem-4.2.7-WardenCommissioning-r3-v1')){throw 'Another pipeline launch authority exists; maintenance has not been established.'}
  $commission=$name -ceq 'CoChem-4.2.7-WardenCommissioning-r3-v1'
  $limit=if($commission){'PT8M'}else{'PT0S'};$arguments=if($commission){Get-OracleCommissionArguments}else{$script:daemonArguments}
  if($commission -and $task.LastTaskResult -ne 0){throw 'Preserved commissioning task has no successful terminal result.'}
  $d=$task.Definition
  if($task.Enabled -or $task.State -ne 1 -or $task.GetInstances(0).Count -ne 0 -or $d.Settings.Enabled -or
     -not $d.Settings.AllowDemandStart -or $d.Settings.RestartCount -ne 0 -or $d.Settings.MultipleInstances -ne 2 -or
     $d.Settings.ExecutionTimeLimit -cne $limit -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or
     $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1){throw 'Daemon maintenance is required; no task is stopped or changed.'}
  $a=$d.Actions.Item(1)
  if($a.Type -ne 0 -or $a.Path -cne $script:python -or $a.Arguments -cne $arguments -or $a.WorkingDirectory -cne $script:installRoot){throw 'Disabled launch definition differs.'}
  $sddl=[string]$task.GetSecurityDescriptor(7);Assert-RegisteredTaskAcl $sddl
  $sha=[Security.Cryptography.SHA256]::Create()
  try{$hash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$task.Xml))).Replace('-','').ToLowerInvariant();$security=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sddl))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $rows+=@([ordered]@{name=$name;absent=$false;xml_sha256=$hash;security_sha256=$security})
 }
 return ,$rows
}

function Get-OracleCommissionArguments {
 $base='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1'
 $pin='bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c'
 $inputControl=Read-R3Control (Join-Path $base 'inputs.json') '' 65536;$packet=(Read-R3Text $inputControl)|ConvertFrom-Json
 $receipt=(Read-R3Text (Read-R3Control (Join-Path $base 'commissioning.json') '' 65536))|ConvertFrom-Json
 if($packet.schema -cne 'cochem-warden-commissioning-inputs/1' -or $packet.nonce -cnotmatch '^[a-f0-9]{32}$' -or $packet.config_sha256 -cne $script:configHash -or
    $receipt.schema -cne 'cochem-warden-commissioning/1' -or $receipt.status -cne 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED' -or
    $receipt.nonce -cne $packet.nonce -or $receipt.input_sha256 -cne $inputControl.Sha256 -or $receipt.helper_sha256 -cne $pin -or
    $receipt.system_sid -cne 'S-1-5-18' -or $receipt.runtime_root -cne $script:installRoot -or $receipt.config_sha256 -cne $script:configHash -or
    $receipt.exactly_one_start_requested -isnot [bool] -or -not $receipt.exactly_one_start_requested -or
    $receipt.automatic_retry_allowed -isnot [bool] -or $receipt.automatic_retry_allowed){throw 'Preserved commissioning controls do not match the exact successful helper.'}
 $null=Read-R3Control (Join-Path $base 'commission-first-warden-r3-v1.py') $pin 1048576
 $null=Read-R3Control (Join-Path $base 'pipeline.json') $script:configHash 1048576
 '-I -B "'+(Join-Path $base 'commission-first-warden-r3-v1.py')+'" --nonce '+$packet.nonce+' --input-sha256 '+$inputControl.Sha256
}

function Assert-OracleAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'One-shot namespace is inaccessible; preserve it.'}
 throw 'One-shot namespace exists; preserve it with no automatic retry.'
}

function Assert-OracleOneShot {
 param($Task,[string]$Arguments,[bool]$Enabled)
 $d=$Task.Definition
 if($Task.Enabled -ne $Enabled -or $d.Settings.Enabled -ne $Enabled -or $Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or
    $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
    -not $d.Settings.AllowDemandStart -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or
    $d.Settings.ExecutionTimeLimit -cne 'PT10M' -or $d.Settings.DisallowStartIfOnBatteries -or $d.Settings.StopIfGoingOnBatteries -or
    $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'One-shot task definition/state differs.'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $script:python -or $a.Arguments -cne $Arguments -or $a.WorkingDirectory -cne $script:targetRoot){throw 'One-shot action differs.'}
 Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7))
}

function Grant-OracleFixtureRead {
 param([string]$Path,[string]$Sid,[bool]$Directory)
 # Only two newly-created paths. Never changes an installed/profile/R root ACL.
 if($Path -cnotin @($script:targetRoot,(Join-Path $script:targetRoot 'oracle-rogue-fixture-r3-v1.py')) -or $Sid -cnotmatch '^S-1-5-21-(\d+-){3}\d+$'){throw 'Fixture grant scope differs.'}
 Assert-ProtectedPath $Path
 $acl=Get-Acl -LiteralPath $Path
 $inherit=if($Directory){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
 $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($Sid),[Security.AccessControl.FileSystemRights]::ReadAndExecute,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
 $acl.AddAccessRule($rule);Set-Acl -LiteralPath $Path -AclObject $acl
 Assert-ProtectedPath $Path
}

function Wait-OracleTask {
 param($Instance)
 $deadline=[DateTime]::UtcNow.AddSeconds(610)
 do{
  Start-Sleep -Milliseconds 250
  try{$Instance.Refresh()}catch{$e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147216629){return};$e=$e.InnerException};throw}
  if([DateTime]::UtcNow -gt $deadline){throw 'Oracle one-shot wait expired. Preserve all evidence; native cleanup is not accepted and no repeat is automatic.'}
 }while($Instance.State -in @(2,4))
}

function Get-OracleSummary {
 param($Receipt,$Task,[string]$Nonce,[string]$PacketHash,[string]$ReceiptHash)
 if($Receipt.schema -cne 'cochem-oracle-native-acceptance/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.packet_sha256 -cne $PacketHash -or
    $Receipt.helper_sha256 -cne $script:sourceHash -or $Receipt.system_sid -cne 'S-1-5-18' -or $Receipt.runtime_root -cne $script:installRoot -or
    $ReceiptHash -cnotmatch '^[a-f0-9]{64}$'){throw 'Oracle receipt binding differs.'}
 foreach($name in @('full_service_acceptance','automatic_retry_allowed','daemon_tasks_modified','daemon_stop_requested','production_databases_modified')){
  if($Receipt.$name -isnot [bool] -or $Receipt.$name){throw 'Oracle preservation/scope differs.'}
 }
 if($Receipt.model_jobs_executed -ne 0 -or $Receipt.partial_outputs_preserved -isnot [bool] -or -not $Receipt.partial_outputs_preserved){throw 'Oracle execution scope differs.'}
 $summary=[ordered]@{schema='cochem-oracle-native-task-result/1';status=$Receipt.status;receipt_path=(Join-Path $script:targetRoot 'oracle-native-acceptance.json');receipt_sha256=$ReceiptHash;last_task_result=$Task.LastTaskResult;full_service_acceptance=$false;automatic_retry_allowed=$false;task_preserved=$true;model_jobs_executed=0}
 if($Receipt.status -ceq 'ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED'){
  if($Task.LastTaskResult -ne 0 -or $null -ne $Receipt.PSObject.Properties['failure'] -or
     $Receipt.install_receipt_sha256 -cne $script:installHash -or $Receipt.config_sha256 -cne $script:configHash -or
     $Receipt.source_manifest_sha256 -cne $script:manifestHash -or $Receipt.revision_sha256 -cne $script:revisionHash -or
     $Receipt.ram_baseline_preserved -ne $true -or $Receipt.maintenance_lock.bytes_and_identity_preserved -ne $true -or
     $Receipt.maintenance_lock.exclusive_byte_zero_lock -ne $true -or $Receipt.component.runtime_trip_fencing_verified -ne $true -or
     $Receipt.component.native_cleanup_verified -ne $true -or $Receipt.component.job_active_processes -ne 0 -or
     $Receipt.component.durable_fencing.status -cne 'FAILED' -or $Receipt.component.durable_fencing.stale_completion_rejected_without_mutation -ne $true){throw 'Oracle success evidence is incomplete.'}
  $summary.component_scope=$Receipt.component.scope;$summary.maintenance_lock_created_new=$Receipt.maintenance_lock.created_new
  return $summary
 }
 if($Receipt.status -cne 'ORACLE_NATIVE_ACCEPTANCE_HELD' -or $Task.LastTaskResult -ne 2){throw 'Oracle status/exit mismatch.'}
 $f=$Receipt.failure
 if($f.phase -cnotin @('custody','prior_evidence','maintenance','fresh_fixture','physical_component','preservation') -or $f.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$' -or
    ($null -ne $f.code -and $f.code -cnotmatch '^[A-Z][A-Z0-9_]{0,95}$') -or
    ($null -ne $f.winerror -and (($f.winerror -isnot [int] -and $f.winerror -isnot [long]) -or $f.winerror -lt 0 -or $f.winerror -gt 4294967295))){throw 'Oracle failure metadata differs.'}
 $summary.failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;code=$f.code;winerror=$f.winerror}
 Write-Host ('COCHEM_ORACLE_RESULT '+($summary|ConvertTo-Json -Depth 6 -Compress))
 throw 'Oracle acceptance held. Preserve partial state and do not stop owner work or repeat automatically.'
}

function Write-OracleWrapperFailure {
 param($Folder,[string]$Phase,[string]$Nonce,[string]$PacketHash)
 if($Phase -cnotin @('enable_run','wait_terminal','read_receipt','validate_receipt')){throw 'Wrapper failure phase differs.'}
 $state=$null;$last=$null;$instances=$null
 try{$t=Get-OracleTask $Folder $script:taskName;if($null -ne $t){if([int]$t.State -ge 0 -and [int]$t.State -le 4){$state=[int]$t.State};$last=[long]$t.LastTaskResult;$instances=[int]$t.GetInstances(0).Count}}catch{}
 $value=[ordered]@{schema='cochem-oracle-native-wrapper-failure/1';status='ORACLE_PARENT_WRAPPER_HELD';phase=$Phase;code='TASK_OR_BOUNDED_RECEIPT_NOT_VERIFIED';nonce=$Nonce;packet_sha256=$PacketHash;source_sha256=$script:sourceHash;task_name=$script:taskName;task_state=$state;last_task_result=$last;instances=$instances;automatic_retry_allowed=$false;partial_outputs_preserved=$true;raw_child_output_retained=$false}
 $path=Join-Path $script:targetRoot 'wrapper-failure.json';$saved=$false;$hash=$null
 try{$hash=Write-RegistrationControl $path ($value|ConvertTo-Json -Depth 5);$saved=$true}catch{}
 $value.receipt_path=$path;$value.receipt_sha256=$hash;$value.receipt_saved=$saved
 Write-Host ('COCHEM_ORACLE_WRAPPER_FAILURE '+($value|ConvertTo-Json -Depth 5 -Compress))
}

function Invoke-OracleAcceptance {
 param($Scheduler,$Folder,$Runtime,$Baseline)
 if($null -ne (Get-OracleTask $Folder $script:taskName)){throw 'One-shot task collision.'}
 Assert-OracleAbsent $script:targetRoot
 $null=Assert-CodeTreeOnce $script:installRoot;$null=Assert-CodeTreeOnce $script:basePythonRoot
 $fresh=Assert-R3InstalledBindings
 if($fresh.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or
    ((Get-OracleMaintenance $Folder)|ConvertTo-Json -Compress) -cne ($Baseline|ConvertTo-Json -Compress)){throw 'Runtime/maintenance changed before staging.'}
 $configObject=(Read-R3Text (Read-R3Control (Join-Path $script:installRoot 'pipeline.json') $script:configHash))|ConvertFrom-Json
 $sid=([Security.Principal.NTAccount]::new($configObject.workers.slot1.name)).Translate([Security.Principal.SecurityIdentifier]).Value
 $records=@();foreach($row in $script:sources){$path=Join-Path $script:sourceRoot $row.name;$length=(Get-Item -LiteralPath $path).Length;$script:held.Add((Open-VerifiedFile $path $row.sha256 $length));$records+=@([pscustomobject]@{source=$path;destination=(Join-Path $script:targetRoot $row.name);sha256=$row.sha256;length=$length})}
 New-ProtectedDirectory $script:targetRoot
 foreach($r in $records){Copy-VerifiedPayload $r;$script:held.Add((Open-VerifiedFile $r.destination $r.sha256 $r.length))}
 Grant-OracleFixtureRead $script:targetRoot $sid $true
 Grant-OracleFixtureRead (Join-Path $script:targetRoot 'oracle-rogue-fixture-r3-v1.py') $sid $false
 $nonce=[Guid]::NewGuid().ToString('N')
 $packet=[ordered]@{schema='cochem-oracle-native-inputs/1';nonce=$nonce;config_sha256=$script:configHash;task_baseline=$Baseline}
 $packetHash=Write-RegistrationControl (Join-Path $script:targetRoot 'inputs.json') ($packet|ConvertTo-Json -Depth 6)
 $null=Read-R3Control (Join-Path $script:targetRoot 'inputs.json') $packetHash
 if(((Get-OracleMaintenance $Folder)|ConvertTo-Json -Compress) -cne ($Baseline|ConvertTo-Json -Compress)){throw 'Maintenance changed before task registration.'}
 if($null -ne (Get-OracleTask $Folder $script:taskName)){throw 'One-shot task collision; preserve new root.'}
 $arguments='-I -B "'+(Join-Path $script:targetRoot 'run-oracle-native-acceptance-r3-v1.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
 $d=$Scheduler.NewTask(0);$d.RegistrationInfo.Description='One isolated Oracle native Job/fencing fixture; real Warden lock, no daemon stop/retry or model jobs.'
 $d.Principal.UserId='SYSTEM';$d.Principal.LogonType=5;$d.Principal.RunLevel=1
 $d.Settings.Enabled=$false;$d.Settings.AllowDemandStart=$true;$d.Settings.MultipleInstances=2;$d.Settings.RestartCount=0;$d.Settings.ExecutionTimeLimit='PT10M'
 $d.Settings.DisallowStartIfOnBatteries=$false;$d.Settings.StopIfGoingOnBatteries=$false
 $a=$d.Actions.Create(0);$a.Path=$script:python;$a.Arguments=$arguments;$a.WorkingDirectory=$script:targetRoot
 $null=$Folder.RegisterTaskDefinition($script:taskName,$d,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
 $task=Get-OracleTask $Folder $script:taskName;Assert-OracleOneShot $task $arguments $false
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'task.xml') ([string]$task.Xml)
 $phase='enable_run'
 try{
  $task.Enabled=$true;Assert-OracleOneShot $task $arguments $true
  $instance=$task.Run($null);$phase='wait_terminal';Wait-OracleTask $instance
  $task=Get-OracleTask $Folder $script:taskName;Assert-OracleOneShot $task $arguments $true
  $phase='read_receipt';$control=Read-R3Control (Join-Path $script:targetRoot 'oracle-native-acceptance.json') '' 131072
  $phase='validate_receipt';Get-OracleSummary ((Read-R3Text $control)|ConvertFrom-Json) $task $nonce $packetHash $control.Sha256
 }catch{Write-OracleWrapperFailure $Folder $phase $nonce $packetHash;throw 'Oracle task or evidence held; bounded wrapper metadata was displayed. No retry or stop was performed.'}
}

# Host invocation; tests import the actual function definitions only.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner; no privilege workaround.'}
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3';$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$programFiles='C:\Program Files'
$targetRoot='C:\Program Files\CoChem\OracleNativeAcceptance4.2.7-windows-20261008-r3-v1';$taskName='CoChem-4.2.7-OracleNativeAcceptance-20261008-r3-v1'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';$manifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$sourceHash='56e78536c2ce85e04d519b13a3347ea620bdf0ab105ed537a0cc9b4afedd9a0d'
$sourceRoot=$PSScriptRoot
$sources=@(
 [pscustomobject]@{name='run-oracle-native-acceptance-r3-v1.py';sha256=$sourceHash},
 [pscustomobject]@{name='oracle-native-components-r3-v1.py';sha256='e32634f56bd67199cde3a7b6501030da892f540fa40e184ac0206639b847d1a0'},
 [pscustomobject]@{name='oracle-rogue-fixture-r3-v1.py';sha256='137ffae58caee103a8025b7325f64580026ad6aefb10e95c3feba8f4494f0205'},
 [pscustomobject]@{name='worker-native-status-r3.py';sha256='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'},
 [pscustomobject]@{name='inspect-execution-prerequisites-r3.py';sha256='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'})
$daemonNames=@('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor','CoChem-4.2.7-WardenCommissioning-r3-v1')
$daemonArguments='-I -B -m cochem_pipeline daemon --config "C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\pipeline.json" --queue-launch-output "C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1"'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
 foreach($text in Import-OracleFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')){. ([scriptblock]::Create($text))};Initialize-FileIdentity
 foreach($text in Import-OracleFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-CodeTreeOnce')){. ([scriptblock]::Create($text))}
 foreach($text in Import-OracleFunctions (Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1') 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198' @('Assert-RegisteredTaskAcl','Write-RegistrationControl')){. ([scriptblock]::Create($text))}
 $runtime=Assert-R3InstalledBindings
 $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
 foreach($row in $sources){$path=Join-Path $PSScriptRoot $row.name;$held.Add((Open-VerifiedFile $path $row.sha256 (Get-Item -LiteralPath $path).Length))}
 $holds=@();$baseline=$null
 try{Assert-OracleAbsent $targetRoot}catch{$holds+='Fresh Oracle namespace is absent only after privileged verification or is already occupied.'}
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 if($null -ne (Get-OracleTask $folder $taskName)){$holds+='One-shot task exists and must be preserved.'}
 try{$baseline=Get-OracleMaintenance $folder}catch{$holds+='Daemon maintenance is not established; this helper never stops owner work.'}
 if(-not $Apply){[ordered]@{schema='cochem-oracle-native-plan/1';mode='READ_ONLY_PLAN';source_sha256=$sourceHash;target_root=$targetRoot;task_name=$taskName;runtime=$runtime;holds=$holds;system_private_checks_deferred=$true;authoritative_exclusion='exclusive maintained byte zero on existing or CreateNew warden.lock';new_lock_bytes_if_absent='30';existing_lock_bytes_preserved=$true;worker_processes_executed=0;model_jobs_executed=0;full_service_acceptance=$false;automatic_retry_allowed=$false;activation_prerequisite=$false}|ConvertTo-Json -Depth 6;return}
 if($holds.Count){throw 'Oracle acceptance requires maintenance or a fresh namespace. No tasks have been changed.'}
 Invoke-OracleAcceptance $scheduler $folder $runtime $baseline|ConvertTo-Json -Depth 6
}finally{foreach($stream in $held){$stream.Dispose()}}
