#Requires -Version 5.1
<# Recover only the observed, never-run pending registration. Saved provider
authentication and all four original commissioning files are consumed intact.
One journaled task-descriptor repair, one missing commissioning registration,
one commissioning Run. No login, task replacement, stop, deletion or retry. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-RecoveryFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'RECOVERY_SUPPORT_CHANGED'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'RECOVERY_SUPPORT_PARSE'}
  foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($nodes.Count -ne 1){throw 'RECOVERY_SUPPORT_FUNCTION'};$nodes[0].Extent.Text}
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Assert-RecoveryObservedAcl {
 param([string]$Sddl)
 $sd=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
 if($null -eq $sd.Owner -or $null -eq $sd.Group -or $sd.Owner.Value -cne 'S-1-5-32-544' -or $sd.Group.Value -cne 'S-1-5-32-544' -or
    ($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $null -eq $sd.DiscretionaryAcl -or $sd.DiscretionaryAcl.Count -ne 3){throw 'RECOVERY_OBSERVED_ACL_CHANGED'}
 $full=[Collections.Generic.HashSet[string]]::new();$read=0
 foreach($ace in $sd.DiscretionaryAcl){
  if($ace.AceType -ne 0 -or $ace.AceFlags -ne 0){throw 'RECOVERY_OBSERVED_ACL_CHANGED'}
  if($ace.AccessMask -eq 2032127 -and $ace.SecurityIdentifier.Value -in @('S-1-5-18','S-1-5-32-544')){if(-not $full.Add($ace.SecurityIdentifier.Value)){throw 'RECOVERY_OBSERVED_ACL_CHANGED'}}
  elseif($ace.AccessMask -eq 1179785 -and $ace.SecurityIdentifier.Value -ceq 'S-1-5-18'){$read++}
  else{throw 'RECOVERY_OBSERVED_ACL_CHANGED'}
 }
 if($full.Count -ne 2 -or $read -ne 1){throw 'RECOVERY_OBSERVED_ACL_CHANGED'}
}

function Assert-RecoveryNeverRun {
 param($Task)
 if($Task.LastTaskResult -ne 267011 -or $Task.LastRunTime.Year -ge 2000){throw 'RECOVERY_TASK_PREVIOUSLY_RUN_OR_UNKNOWN'}
}

function Assert-RecoveryOtherDaemons {
 param($Folder)
 foreach($name in @('CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
  $task=Get-RegistrationTask $Folder $name
  if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){throw 'RECOVERY_OTHER_DAEMON_ACTIVE'}
 }
}

function Get-RecoveryPendingState {
 param($Folder,[string]$AuthHash,[bool]$Canonical=$false)
 Assert-RecoveryOtherDaemons $Folder
 if($null -ne (Get-RegistrationTask $Folder $script:commissionTask)){throw 'RECOVERY_COMMISSION_TASK_ALREADY_EXISTS'}
 $warden=Get-RegistrationTask $Folder $script:taskName;if($null -eq $warden){throw 'RECOVERY_PENDING_WARDEN_MISSING'}
 Assert-RecoveryTaskBackingAcl ($warden.GetSecurityDescriptor(7)) $warden.Name
 # Original shape check is unchanged. Only its ACL callback is temporarily
 # rebound to the exact owner-observed projection while inspecting pre-repair.
 $saved=${function:Assert-RegisteredTaskAcl}
 try{
  if(-not $Canonical){Set-Item -Path Function:Assert-RegisteredTaskAcl -Value ${function:Assert-RecoveryObservedAcl}}
  Assert-FirstStartTask $warden $true $script:daemonArguments
 }finally{Set-Item -Path Function:Assert-RegisteredTaskAcl -Value $saved}
 Assert-RecoveryNeverRun $warden
 Assert-ProtectedPath $script:targetRoot
 $expected=@('commission-first-warden-r3-v1.py','inputs.json','pipeline.json','worker-native-status-r3.py')
 $actual=@(Get-ChildItem -LiteralPath $script:targetRoot -Force|ForEach-Object Name)
 if($actual.Count -ne $expected.Count -or @($actual|Where-Object{$_ -cnotin $expected}).Count){throw 'RECOVERY_ORIGINAL_ROOT_ENTRIES_CHANGED'}
 $rows=@()
 foreach($spec in @(
  [pscustomobject]@{name='commission-first-warden-r3-v1.py';hash=$script:sourceHash},
  [pscustomobject]@{name='worker-native-status-r3.py';hash=$script:pythonSupportHash},
  [pscustomobject]@{name='pipeline.json';hash=$script:configHash})){
  $control=Read-R3Control (Join-Path $script:targetRoot $spec.name) $spec.hash 131072
  $rows+=[pscustomobject]@{name=$spec.name;sha256=$control.Sha256;length=$control.Length}
 }
 $input=Read-R3Control (Join-Path $script:targetRoot 'inputs.json') '' 65536;$packet=(Read-R3Text $input)|ConvertFrom-Json
 $fields=@('schema','nonce','config_sha256','source_manifest_sha256','auth_receipt_sha256','auth_attempt','model_jobs_submitted','automatic_retry_allowed')
 if(@($packet.PSObject.Properties).Count -ne $fields.Count -or @($packet.PSObject.Properties.Name|Where-Object{$_ -cnotin $fields}).Count){throw 'RECOVERY_PENDING_PACKET_SHAPE'}
 foreach($name in @('schema','nonce','config_sha256','source_manifest_sha256','auth_receipt_sha256','auth_attempt')){if($packet.PSObject.Properties[$name].Value -isnot [string]){throw 'RECOVERY_PENDING_PACKET_TYPE'}}
 if($packet.schema -cne 'cochem-warden-commissioning-inputs/1' -or $packet.nonce -cnotmatch '^[a-f0-9]{32}$' -or
    $packet.config_sha256 -cne $script:configHash -or $packet.source_manifest_sha256 -cne $script:manifestHash -or
    $packet.auth_receipt_sha256 -cne $AuthHash -or $packet.auth_attempt -cne $script:Attempt -or
    $packet.PSObject.Properties['model_jobs_submitted'].Value -isnot [int] -or $packet.model_jobs_submitted -ne 0 -or
    $packet.PSObject.Properties['automatic_retry_allowed'].Value -isnot [bool] -or $packet.automatic_retry_allowed){throw 'RECOVERY_PENDING_PACKET_BINDING'}
 $rows+=[pscustomobject]@{name='inputs.json';sha256=$input.Sha256;length=$input.Length}
 [pscustomobject]@{Warden=$warden;Xml=[string]$warden.Xml;Sddl=[string]$warden.GetSecurityDescriptor(7);Packet=$packet;InputHash=$input.Sha256;Files=$rows}
}

function Invoke-PendingWardenRecovery {
 param($Scheduler,$Folder,$Runtime,[string]$AuthHash)
 $script:phase='pending_preflight'
 Assert-FirstStartAbsent $script:recoveryRoot
 $null=Assert-CodeTreeOnce $script:installRoot;$null=Assert-CodeTreeOnce $script:basePythonRoot
 $fresh=Assert-R3InstalledBindings
 if($fresh.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or (Read-FirstStartAuth) -cne $AuthHash){throw 'RECOVERY_RUNTIME_OR_AUTH_DRIFT'}
 $pending=Get-RecoveryPendingState $Folder $AuthHash
 foreach($row in $pending.Files){$script:held.Add((Open-VerifiedFile (Join-Path $script:targetRoot $row.name) $row.sha256 $row.length))}
 $again=Get-RecoveryPendingState $Folder $AuthHash
 if($again.Xml -cne $pending.Xml -or $again.Sddl -cne $pending.Sddl -or $again.InputHash -cne $pending.InputHash){throw 'RECOVERY_PENDING_DRIFT'}
 $script:phase='preservation'
 New-ProtectedDirectory $script:recoveryRoot
 $sddlHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'original-warden.sddl') $pending.Sddl
 $xmlHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'original-warden.xml') $pending.Xml
 $capture=[ordered]@{schema='cochem-pending-warden-preservation/1';authentication_attempt=$script:Attempt;authentication_receipt_sha256=$AuthHash;original_root=$script:targetRoot;original_files=$pending.Files;original_task_sddl_sha256=$sddlHash;original_task_xml_sha256=$xmlHash;never_run_result=267011;originals_deleted=$false}
 $captureHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'preservation.json') ($capture|ConvertTo-Json -Depth 7)
 $script:phase='descriptor_intent'
 $intent=[ordered]@{schema='cochem-pending-warden-recovery-intent/1';task_name=$script:taskName;authentication_attempt=$script:Attempt;input_sha256=$pending.InputHash;preservation_sha256=$captureHash;setter_flags=16;registration_flags=18;descriptor_set_calls_allowed=1;commissioning_run_calls_allowed=1;automatic_retry_allowed=$false}
 $intentHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'recovery-intent.json') ($intent|ConvertTo-Json -Depth 6)
 # This service API repairs only the newly created, bound disabled Warden.
 # DONT_ADD_PRINCIPAL_ACE retains the original requested protected two-ACE ACL.
 $script:phase='descriptor_repair'
 $pending.Warden.SetSecurityDescriptor('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)',16)
 $canonical=Get-RecoveryPendingState $Folder $AuthHash $true
 if($canonical.Xml -cne $pending.Xml -or $canonical.InputHash -cne $pending.InputHash){throw 'RECOVERY_TASK_ORIGINAL_BYTES_CHANGED'}
 $sd=[Security.AccessControl.RawSecurityDescriptor]::new($canonical.Sddl)
 if($sd.Owner.Value -cne 'S-1-5-32-544' -or $null -eq $sd.Group -or $sd.Group.Value -cne 'S-1-5-32-544' -or $sd.DiscretionaryAcl.Count -ne 2){throw 'RECOVERY_CANONICAL_READBACK_DIFFERED'}
 $script:phase='missing_commission_registration'
 $commissionArguments='-I -B "'+(Join-Path $script:targetRoot 'commission-first-warden-r3-v1.py')+'" --nonce '+$pending.Packet.nonce+' --input-sha256 '+$pending.InputHash
 if($null -ne (Get-RegistrationTask $Folder $script:commissionTask)){throw 'RECOVERY_TASK_COLLISION'}
 $d=$Scheduler.NewTask(0);$d.RegistrationInfo.Description='Reviewed r3 pending commissioning continuation. No triggers/retries; preserve on any failure.'
 $d.Principal.UserId='SYSTEM';$d.Principal.LogonType=5;$d.Principal.RunLevel=1
 $d.Settings.Enabled=$false;$d.Settings.AllowDemandStart=$true;$d.Settings.MultipleInstances=2;$d.Settings.RestartCount=0;$d.Settings.ExecutionTimeLimit='PT8M'
 $a=$d.Actions.Create(0);$a.Path=$script:python;$a.Arguments=$commissionArguments;$a.WorkingDirectory=$script:installRoot
 $null=$Folder.RegisterTaskDefinition($script:commissionTask,$d,18,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
 $task=Get-RegistrationTask $Folder $script:commissionTask;Assert-FirstStartTask $task $false $commissionArguments;Assert-RecoveryNeverRun $task
 $warden=Get-RegistrationTask $Folder $script:taskName;Assert-FirstStartTask $warden $true $script:daemonArguments;Assert-RecoveryNeverRun $warden
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'disabled-warden-task.xml') ([string]$warden.Xml)
 $null=Write-RegistrationControl (Join-Path $script:targetRoot 'commissioning-task.xml') ([string]$task.Xml)
 if((Read-FirstStartAuth) -cne $AuthHash){throw 'RECOVERY_AUTH_CHANGED_BEFORE_RUN'}
 $script:phase='commissioning_run'
 $task.Enabled=$true;$instance=$task.Run($null);Wait-FirstStartTask $instance
 $script:phase='commissioning_receipt'
 $task=Get-RegistrationTask $Folder $script:commissionTask;Assert-CompletedStatusTask $task
 $control=Read-R3Control (Join-Path $script:targetRoot 'commissioning.json') '' 65536
 $result=Get-FirstStartResult ((Read-R3Text $control)|ConvertFrom-Json) $task $pending.Packet.nonce $pending.InputHash $control.Sha256
 $done=[ordered]@{schema='cochem-pending-warden-recovery/1';status='PENDING_REGISTRATION_RECOVERED_AND_COMMISSIONED';authentication_attempt=$script:Attempt;authentication_receipt_sha256=$AuthHash;preservation_sha256=$captureHash;intent_sha256=$intentHash;commissioning_receipt_sha256=$control.Sha256;original_files_preserved=$true;old_warden_reregistered=$false;descriptor_set_calls=1;missing_commissioning_tasks_registered=1;commissioning_run_calls=1;login_commands_executed=0;model_jobs_submitted=0;automatic_retry_allowed=$false;full_srs_acceptance=$false}
 $doneHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'recovery-complete.json') ($done|ConvertTo-Json -Depth 7)
 [ordered]@{schema='cochem-pending-warden-recovery-result/1';status=$done.status;receipt_path=(Join-Path $script:recoveryRoot 'recovery-complete.json');receipt_sha256=$doneHash;commissioning=$result;login_commands_executed=0;automatic_retry_allowed=$false;full_srs_acceptance=$false}
}

# Host boundary: default preview never calls Python, registers tasks or repairs ACLs.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner.'}
$Attempt='8ba7ce50d32c4a96a3c3fd5947a6cd4b'
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$manifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$config=Join-Path $installRoot 'pipeline.json';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$targetRoot='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1';$configTarget=Join-Path $targetRoot 'pipeline.json'
$recoveryRoot='C:\Program Files\CoChem\TaskAclRecovery4.2.7-windows-20261008-r3-v1'
$queueRoot='C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1'
$taskName='CoChem-4.2.7-Warden';$commissionTask='CoChem-4.2.7-WardenCommissioning-r3-v1'
$sourceHash='8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e';$pythonSupportHash='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'
$daemonArguments='-I -B -m cochem_pipeline daemon --config "'+$configTarget+'" --queue-launch-output "'+$queueRoot+'"'
$held=[Collections.Generic.List[IO.FileStream]]::new();$phase='source_preflight'
try{
 foreach($text in Import-RecoveryFunctions (Join-Path $PSScriptRoot 'commission-first-warden-r3-v5.ps1') 'a40e093839f8482937a814a236b4a50775e741f30f2514c3ae9715fab371363e' @('Assert-FirstStartAbsent','Assert-FirstStartTask','Read-FirstStartAuth','Get-FirstStartResult','Wait-FirstStartTask')){. ([scriptblock]::Create($text))}
 foreach($text in Import-RecoveryFunctions (Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1') 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198' @('Get-RegistrationTask','Assert-RegistrationConfiguration','Assert-RegisteredTaskAcl','Write-RegistrationControl')){. ([scriptblock]::Create($text))}
 foreach($text in Import-RecoveryFunctions (Join-Path $PSScriptRoot 'task-private-acl-v8.ps1') 'f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47' @('Assert-RegisteredTaskAcl')){. ([scriptblock]::Create($text.Replace('function Assert-RegisteredTaskAcl {','function Assert-RecoveryTaskBackingAcl {')))}
 foreach($text in Import-RecoveryFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile')){. ([scriptblock]::Create($text))};Initialize-FileIdentity
 foreach($text in Import-RecoveryFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-VenvBinding','Assert-CompletedStatusTask')){. ([scriptblock]::Create($text))}
 foreach($text in Import-RecoveryFunctions (Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1') '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d' @('Assert-CodeTreeOnce')){. ([scriptblock]::Create($text))}
 $runtime=Assert-R3InstalledBindings;Assert-RegistrationConfiguration (Read-R3Text (Read-R3Control $config $configHash))
 Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
 $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
 $holds=@();$authHash=$null;$folder=$null;$scheduler=$null
 try{Assert-FirstStartAbsent $recoveryRoot}catch{$holds+='Recovery namespace exists or absence is unproved; preserve it.'}
 if($admin){
  $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
  $authHash=Read-FirstStartAuth;$null=Get-RecoveryPendingState $folder $authHash
 }else{$holds+='Elevated owner must verify saved private authentication, exact original files and never-run task before recovery.'}
 if(-not $Apply){[ordered]@{schema='cochem-pending-warden-recovery-plan/1';mode='READ_ONLY_PLAN';authentication_attempt=$Attempt;runtime_verified=$true;runtime_root=$installRoot;original_commissioning_root=$targetRoot;recovery_root=$recoveryRoot;identity_count=6;shared_capacity=4;login_commands_executed=0;model_jobs_submitted=0;tasks_changed=0;python_executed=$false;automatic_repair_enabled=$false;automatic_retry_allowed=$false;full_srs_acceptance=$false;holds=$holds}|ConvertTo-Json -Depth 6;return}
 if($holds.Count){throw 'RECOVERY_PREFLIGHT_HELD'}
 Invoke-PendingWardenRecovery $scheduler $folder $runtime $authHash|ConvertTo-Json -Depth 10
}catch{
 $failure=[ordered]@{schema='cochem-pending-warden-recovery-failure/1';status='HELD_PRESERVE_EVERY_ARTIFACT_AND_RUNNING_STATE';phase=$phase;error_type=$_.Exception.GetType().Name;automatic_retry_allowed=$false;login_commands_executed=0;full_srs_acceptance=$false}
 if($Apply -and (Test-Path -LiteralPath $recoveryRoot) -and (Get-Command Write-RegistrationControl -ErrorAction SilentlyContinue)){try{$failure.receipt_path=Join-Path $recoveryRoot 'recovery-failure.json';$failure.receipt_sha256=Write-RegistrationControl $failure.receipt_path ($failure|ConvertTo-Json -Depth 6)}catch{}}
 $failure|ConvertTo-Json -Depth 6|Write-Host
 throw 'Pending recovery held. Preserve all tasks, original files and any running controller; do not repeat, delete or stop automatically.'
}finally{foreach($stream in $held){$stream.Dispose()}}
