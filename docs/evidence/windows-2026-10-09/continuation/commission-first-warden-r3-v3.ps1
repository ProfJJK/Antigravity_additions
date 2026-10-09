#Requires -Version 5.1
<# Default is read-only. -Apply creates one disabled r3 Warden task, then a
bounded SYSTEM commissioning task verifies private first-start prerequisites
and requests exactly one Enable/Run. No automatic retry, stop or cleanup.
Native authentication is consumed, never initiated by this helper. #>
[CmdletBinding()]
param([switch]$Apply,[ValidatePattern('^[a-f0-9]{32}$')][string]$Attempt='00000000000000000000000000000000')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
if($Apply -and $Attempt -ceq '00000000000000000000000000000000'){throw 'Apply requires a nonzero bound authentication attempt.'}

function Import-FirstStartFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Reviewed first-start support changed.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'First-start support parse failure.'}
  foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($nodes.Count -ne 1){throw 'Missing exact support definition.'};$nodes[0].Extent.Text}
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Assert-FirstStartAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'First-start namespace is inaccessible; absence is not proved.'}
 throw 'First-start namespace exists. Preserve it; no automatic repeat or resume.'
}

function Assert-FirstStartTasks {
 param($Folder)
 Assert-RegistrationTasks $Folder
 if($null -ne (Get-RegistrationTask $Folder $script:commissionTask)){throw 'Existing commissioning task must be preserved.'}
}

function Assert-FirstStartTask {
 param($Task,[bool]$Daemon,[string]$Arguments)
 $d=$Task.Definition
 if($Task.Enabled -ne $false -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or
    $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
    $d.Settings.Enabled -ne $false -or $d.Settings.AllowDemandStart -ne $true -or $d.Settings.MultipleInstances -ne 2 -or
    $d.Settings.RestartCount -ne 0 -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or
    $d.Settings.ExecutionTimeLimit -cne $(if($Daemon){'PT0S'}else{'PT8M'})){throw 'New task is not the exact disabled SYSTEM definition.'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $script:python -or $a.Arguments -cne $Arguments -or $a.WorkingDirectory -cne $script:installRoot){throw 'New task action differs.'}
 Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7))
}

function Read-FirstStartAuth {
 $path="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\series-complete.json"
 $control=Read-R3Control $path '' 65536;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.attempt -cne $Attempt -or $v.schema -cne 'cochem-both-providers-status-first-result/1' -or $v.status -cne 'CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED' -or
    $v.nonce -cnotmatch '^[a-f0-9]{32}$' -or $v.model_jobs_executed -ne 0 -or $v.configuration_changed -ne $false -or
    $v.activation_ready -ne $false -or $v.agy_integration_hold_preserved -ne $true -or $v.series_preserved -ne $true -or @($v.provider_results).Count -ne 2){throw 'Completed authentication has invalid bindings.'}
 $seen=@{}
 foreach($row in $v.provider_results){
  $provider=$row.provider
  if($provider -cnotin @('codex','claude') -or $seen.ContainsKey($provider)){throw 'Invalid authentication provider set.'};$seen[$provider]=$true
  $expected="C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$provider\series-complete.json"
  if($row.receipt_path -cne $expected -or $row.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Authentication source path differs.'}
  $p=(Read-R3Text (Read-R3Control $expected $row.receipt_sha256 65536))|ConvertFrom-Json
  if($p.attempt -cne $Attempt -or $p.status -cne 'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or $p.selected_workers_verified -ne 6 -or @($p.status_receipts).Count -ne 6 -or
     $p.runtime.install_receipt_sha256 -cne $script:installHash -or $p.runtime.configuration_sha256 -cne $script:configHash -or $p.runtime.revision.source_sha256 -cne $script:revisionHash){throw 'Provider authentication runtime differs.'}
  $slots=@{}
  foreach($r in $p.status_receipts){
   if($r.slot -cnotin @('slot1','slot2','slot3','slot4','slot5','slot6') -or $slots.ContainsKey($r.slot) -or $r.proof.stage -cnotin @('before','after') -or $r.proof.decision -cne 'REUSE_VERIFIED_SESSION'){throw 'Invalid authentication slot proof.'};$slots[$r.slot]=$true
   $leaf="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$($r.slot)-$provider-$($r.proof.stage)\worker-native-status.json"
   if($r.proof.receipt_path -cne $leaf -or $r.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Invalid authentication leaf path.'}
   $null=Read-R3Control $leaf $r.proof.receipt_sha256 65536
  }
 }
 return $control.Sha256
}

function Get-FirstStartResult {
 param($Receipt,$Task,[string]$Nonce,[string]$PacketHash,[string]$ReceiptHash)
 if($Receipt.schema -cne 'cochem-warden-commissioning/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.system_sid -cne 'S-1-5-18' -or
    $Receipt.helper_sha256 -cne $script:sourceHash -or $Receipt.input_sha256 -cne $PacketHash -or
    $Receipt.runtime_root -cne $script:installRoot -or $Receipt.install_receipt_sha256 -cne $script:installHash -or
    $Receipt.config_sha256 -cne $script:configHash -or $Receipt.source_manifest_sha256 -cne $script:manifestHash -or $Receipt.revision_sha256 -cne $script:revisionHash -or
    $Receipt.task_name -cne $script:taskName -or $Receipt.queue_launch_output -cne $script:queueRoot -or
    $Receipt.model_jobs_submitted -ne 0 -or $Receipt.full_srs_acceptance -ne $false -or $Receipt.automatic_repair_enabled -ne $false -or $Receipt.automatic_retry_allowed -ne $false){throw 'Commissioning receipt binding differs.'}
 Assert-CompletedStatusTask $Task
 $result=[ordered]@{schema='cochem-warden-commissioning-result/1';status=$Receipt.status;receipt_path=(Join-Path $script:targetRoot 'commissioning.json');receipt_sha256=$ReceiptHash;last_task_result=$Task.LastTaskResult;task_preserved=$true;automatic_retry_allowed=$false;full_srs_acceptance=$false;model_jobs_submitted=0}
 if($Receipt.status -ceq 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED'){
  if($Task.LastTaskResult -ne 0 -or $Receipt.exactly_one_start_requested -ne $true -or $Receipt.authenticated_profiles_verified -ne 12 -or
     $Receipt.monitoring_started -ne $true -or $Receipt.monitoring_scope -cne 'heartbeat_and_queue_only'){throw 'First-start success claim is incomplete.'}
  foreach($key in @('pid','creation_filetime','first_sequence','final_sequence')){if(($Receipt.controller.$key -isnot [int] -and $Receipt.controller.$key -isnot [long]) -or $Receipt.controller.$key -le 0){throw 'Invalid controller identity/progress.'}}
  if($Receipt.controller.instance_id -cnotmatch '^[a-f0-9]{32}$' -or $Receipt.controller.final_sequence -le $Receipt.controller.first_sequence){throw 'Controller heartbeat did not progress.'}
  $result.controller=$Receipt.controller;return $result
 }
 if($Receipt.status -cne 'WARDEN_COMMISSIONING_HELD' -or $Task.LastTaskResult -ne 2){throw 'Commissioning status/task result disagreement.'}
 $failure=$Receipt.failure
 if($failure.phase -cnotin @('runtime_custody','prior_evidence','authentication','state_preflight','start_intent','enable_run','control_plane') -or
    $failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$' -or ($null -ne $failure.winerror -and (($failure.winerror -isnot [int] -and $failure.winerror -isnot [long]) -or $failure.winerror -lt 0 -or $failure.winerror -gt 4294967295))){throw 'Invalid sanitized failure metadata.'}
 $result.failure=[ordered]@{phase=$failure.phase;error_type=$failure.error_type;winerror=$failure.winerror}
 if($null -ne $failure.PSObject.Properties['last_observation_code']){
  if($failure.last_observation_code -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$'){throw 'Invalid observation code.'}
  $result.failure.last_observation_code=$failure.last_observation_code
 }
 Write-Host ('COCHEM_COMMISSIONING_RESULT '+($result|ConvertTo-Json -Depth 6 -Compress))
 throw 'First start is held. Preserve every task and receipt, including any running controller. No automatic retry or stop.'
}

function Invoke-FirstStart {
 param($Scheduler,$Folder,$Runtime,[string]$AuthHash)
 Assert-FirstStartTasks $Folder;Assert-FirstStartAbsent $script:targetRoot
 $null=Assert-CodeTreeOnce $script:installRoot;$null=Assert-CodeTreeOnce $script:basePythonRoot
 $fresh=Assert-R3InstalledBindings
 if($fresh.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or (Read-FirstStartAuth) -cne $AuthHash){throw 'First-start runtime/authentication drift.'}
 $records=@(
  [pscustomobject]@{source=$script:source;destination=(Join-Path $script:targetRoot 'commission-first-warden-r3-v1.py');sha256=$script:sourceHash;length=(Get-Item -LiteralPath $script:source).Length},
  [pscustomobject]@{source=$script:pythonSupport;destination=(Join-Path $script:targetRoot 'worker-native-status-r3.py');sha256=$script:pythonSupportHash;length=(Get-Item -LiteralPath $script:pythonSupport).Length},
  [pscustomobject]@{source=$script:config;destination=$script:configTarget;sha256=$script:configHash;length=(Get-Item -LiteralPath $script:config).Length})
 foreach($r in $records){$script:held.Add((Open-VerifiedFile $r.source $r.sha256 $r.length))}
 Assert-FirstStartTasks $Folder;Assert-FirstStartAbsent $script:targetRoot
 New-ProtectedDirectory $script:targetRoot
 foreach($r in $records){Copy-VerifiedPayload $r;$script:held.Add((Open-VerifiedFile $r.destination $r.sha256 $r.length))}
 $nonce=[Guid]::NewGuid().ToString('N')
 $packet=[ordered]@{schema='cochem-warden-commissioning-inputs/1';nonce=$nonce;config_sha256=$script:configHash;source_manifest_sha256=$script:manifestHash;auth_receipt_sha256=$AuthHash;auth_attempt=$script:Attempt;model_jobs_submitted=0;automatic_retry_allowed=$false}
 $packetHash=Write-RegistrationControl (Join-Path $script:targetRoot 'inputs.json') ($packet|ConvertTo-Json -Depth 6)
 $null=Read-R3Control (Join-Path $script:targetRoot 'inputs.json') $packetHash
 $daemonArguments='-I -B -m cochem_pipeline daemon --config "'+$script:configTarget+'" --queue-launch-output "'+$script:queueRoot+'"'
 $commissionArguments='-I -B "'+(Join-Path $script:targetRoot 'commission-first-warden-r3-v1.py')+'" --nonce '+$nonce+' --input-sha256 '+$packetHash
 foreach($spec in @([pscustomobject]@{name=$script:taskName;daemon=$true;arguments=$daemonArguments},[pscustomobject]@{name=$script:commissionTask;daemon=$false;arguments=$commissionArguments})){
  if($null -ne (Get-RegistrationTask $Folder $spec.name)){throw 'Task collision; preserve partial commissioning.'}
  $d=$Scheduler.NewTask(0);$d.RegistrationInfo.Description='Reviewed r3 first commissioning. No triggers/retries; preserve on any failure.'
  $d.Principal.UserId='SYSTEM';$d.Principal.LogonType=5;$d.Principal.RunLevel=1
  $d.Settings.Enabled=$false;$d.Settings.AllowDemandStart=$true;$d.Settings.MultipleInstances=2;$d.Settings.RestartCount=0;$d.Settings.ExecutionTimeLimit=if($spec.daemon){'PT0S'}else{'PT8M'}
  $a=$d.Actions.Create(0);$a.Path=$script:python;$a.Arguments=$spec.arguments;$a.WorkingDirectory=$script:installRoot
  $null=$Folder.RegisterTaskDefinition($spec.name,$d,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
  Assert-FirstStartTask (Get-RegistrationTask $Folder $spec.name) $spec.daemon $spec.arguments
 }
 $warden=Get-RegistrationTask $Folder $script:taskName;Assert-FirstStartTask $warden $true $daemonArguments
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'disabled-warden-task.xml') ([string]$warden.Xml)
 $task=Get-RegistrationTask $Folder $script:commissionTask;Assert-FirstStartTask $task $false $commissionArguments
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'commissioning-task.xml') ([string]$task.Xml)
 # Only the commissioning task is started here. Its SYSTEM preflight writes a
 # durable intent before a single separate Enable/Run of the daemon task.
 $task.Enabled=$true;$instance=$task.Run($null);Wait-FirstStartTask $instance
 $task=Get-RegistrationTask $Folder $script:commissionTask;Assert-CompletedStatusTask $task
 $control=Read-R3Control (Join-Path $script:targetRoot 'commissioning.json') '' 65536
 Get-FirstStartResult ((Read-R3Text $control)|ConvertFrom-Json) $task $nonce $packetHash $control.Sha256
}

function Wait-FirstStartTask {
 param($Instance)
 $deadline=[DateTime]::UtcNow.AddSeconds(490)
 do{
  Start-Sleep -Milliseconds 500
  try{$Instance.Refresh()}catch{$e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147216629){return};$e=$e.InnerException};throw}
  if([DateTime]::UtcNow -gt $deadline){throw 'Commissioning wait expired; preserve potentially running controller and all evidence. Do not repeat.'}
 }while($Instance.State -in @(2,4))
}

# Host invocation boundary. Fixtures import actual functions without this block.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$manifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$config=Join-Path $installRoot 'pipeline.json';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$targetRoot='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1';$configTarget=Join-Path $targetRoot 'pipeline.json'
$queueRoot='C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1'
$taskName='CoChem-4.2.7-Warden';$commissionTask='CoChem-4.2.7-WardenCommissioning-r3-v1'
$source=Join-Path $PSScriptRoot 'commission-first-warden-r3-v3.py';$sourceHash='9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'
$pythonSupport=Join-Path $PSScriptRoot 'worker-native-status-r3.py';$pythonSupportHash='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
 foreach($text in Import-FirstStartFunctions (Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1') 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198' @('Get-RegistrationTask','Assert-RegistrationTasks','Assert-RegistrationConfiguration','Assert-RegisteredTaskAcl','Write-RegistrationControl')){. ([scriptblock]::Create($text))}
 foreach($text in Import-FirstStartFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')){. ([scriptblock]::Create($text))};Initialize-FileIdentity
 foreach($text in Import-FirstStartFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-VenvBinding','Assert-CodeTreeOnce','Assert-CompletedStatusTask')){. ([scriptblock]::Create($text))}
 $runtime=Assert-R3InstalledBindings;Assert-RegistrationConfiguration (Read-R3Text (Read-R3Control $config $configHash))
 Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
 $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
 $held.Add((Open-VerifiedFile $source $sourceHash (Get-Item -LiteralPath $source).Length));$held.Add((Open-VerifiedFile $pythonSupport $pythonSupportHash (Get-Item -LiteralPath $pythonSupport).Length))
 $holds=@();$authPresent=$false;$authHash=$null
 try{Assert-FirstStartAbsent $targetRoot}catch{$holds+='Fresh commissioning namespace was not proved absent.'}
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 try{Assert-FirstStartTasks $folder}catch{$holds+='Exact commissioning/daemon task state was not proved fresh and stopped.'}
 $authPath="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt\series-complete.json"
 try{$null=Get-Item -LiteralPath $authPath -ErrorAction Stop;$authPresent=$true;$authHash=Read-FirstStartAuth}
 catch [Management.Automation.ItemNotFoundException]{$holds+='Completed status-first authentication is required before first start.'}
 catch{$holds+='Authentication completion is inaccessible or its pinned chain is invalid.'}
 if(-not $Apply){[ordered]@{schema='cochem-warden-commissioning-plan/1';mode='READ_ONLY_PLAN';runtime_root=$installRoot;config_sha256=$configHash;source_manifest_sha256=$manifestHash;
    target_root=$targetRoot;task_name=$taskName;identities=6;shared_slots=4;auth_completion_present=$authPresent;auth_completion_required=$true;holds=$holds;
    system_preflight_deferred=$true;system_preflight_scope='Exact private receipt pins, fresh primary database namespace, token ACL, empty six SSD/five RAM roots, exact four-file slot1 Docker fixture privately preserved before normal daemon cleanup, closed registry, Defender/RAM attestation, free loopback port';
    sole_nonempty_scratch_exception='R:\CoChem427-windows-20261007\slot1\execution-acceptance-r3-v2';fixture_preservation_required=$true;
    full_tree_custody_deferred_until_apply=$true;monitoring_scope='heartbeat_and_queue_only';full_srs_acceptance=$false;model_jobs_submitted=0;automatic_repair_enabled=$false;automatic_retry_allowed=$false}|ConvertTo-Json -Depth 6;return}
 if($holds.Count){throw 'First commissioning is held. No tasks were registered or started.'}
 Invoke-FirstStart $scheduler $folder $runtime $authHash|ConvertTo-Json -Depth 8
}finally{foreach($stream in $held){$stream.Dispose()}}
