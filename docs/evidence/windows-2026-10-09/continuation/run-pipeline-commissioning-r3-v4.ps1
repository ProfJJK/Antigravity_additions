#Requires -Version 5.1
<# One attended series: reuse/authenticate native profiles, then commission one
   Warden instance. Default is metadata-only. Deliberate authentication continuation uses a fresh attempt; partial controller start remains held. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -ne 'ConsoleHost')){throw 'Apply requires the owner in an elevated interactive ConsoleHost, with -Interactive and no redirected input/output.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$powershell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$Attempt=if($Apply){[Guid]::NewGuid().ToString('N')}else{'00000000000000000000000000000000'}
$seriesRoot="C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v3-$Attempt"
$authSource=Join-Path $PSScriptRoot 'authenticate-native-profiles-status-first-r3-v4.ps1'
$authHash='724c6ac99b4bbfa41a8da2c45892ee0efaa46deed458359194f308d4ee566dca'
$activationSource=Join-Path $PSScriptRoot 'commission-first-warden-r3-v4.ps1'
$activationHash='6e8acd55bdc3a66a78846b8db5601f07baa9b84677197cffb7b1f86721bd720b'
$activationRoot='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1'
$held=[Collections.Generic.List[IO.FileStream]]::new()

function Import-CommissioningFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Reviewed commissioning source changed; no phase was started.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Commissioning support source does not parse.'}
  foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($nodes.Count -ne 1){throw 'Missing or duplicate commissioning function.'};$nodes[0].Extent.Text}
  $held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

# Read-only custody of the reported failed attempt. These receipts authorize no
# login or launch: the new series obtains current status in every profile.
function Assert-RecoveryAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'Recovery prerequisite is inaccessible; absence is not established.'}
 throw 'Unexpected prior completion or later-phase artifact. Preserve it; this continuation is held.'
}

function Assert-RecoveryPriorRecords {
 param($CommissionStart,$CommissionFailure,$AuthStart,$AuthFailure,$WorkerStart,$WorkerFailure,$Before,$Login,[ValidateSet('slot1','slot5')][string]$FailedSlot='slot1',[ValidateSet('v1','v2')][string]$Version='v1')
 foreach($pair in @(
  @($CommissionStart,$CommissionFailure,'cochem-pipeline-commissioning-series/1','cochem-pipeline-commissioning-series-failure/1','reviewed_series'),
  @($AuthStart,$AuthFailure,'cochem-both-providers-status-first/1','cochem-both-providers-status-first-failure/1','attended_provider_series'),
  @($WorkerStart,$WorkerFailure,'cochem-six-worker-status-first/1','cochem-six-worker-status-first-failure/1','status_first_authentication')
 )){
  $start=$pair[0];$failure=$pair[1]
  if($start.schema -cne $pair[2] -or $failure.schema -cne $pair[3] -or $failure.phase -cne $pair[4] -or
     $start.status -cne 'IN_PROGRESS' -or $start.nonce -cnotmatch '^[a-f0-9]{32}$' -or $failure.nonce -cne $start.nonce -or
     $failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$'){throw 'Prior failure receipts do not establish the reported stopped attempt.'}
 }
 foreach($v in @($CommissionFailure,$AuthFailure)){if($v.automatic_repeat_allowed -isnot [bool] -or $v.automatic_repeat_allowed){throw 'Prior repeat policy differs.'}}
 if($CommissionFailure.partial_outputs_preserved -isnot [bool] -or -not $CommissionFailure.partial_outputs_preserved -or
    $CommissionFailure.full_srs_acceptance -isnot [bool] -or $CommissionFailure.full_srs_acceptance -or
    $AuthFailure.activation_ready -isnot [bool] -or $AuthFailure.activation_ready -or
    $WorkerFailure.automatic_resume_allowed -isnot [bool] -or $WorkerFailure.automatic_resume_allowed -or
    $WorkerFailure.configuration_applied -isnot [bool] -or $WorkerFailure.configuration_applied -or
    $WorkerFailure.activation_ready -isnot [bool] -or $WorkerFailure.activation_ready -or
    $WorkerStart.provider -cne 'codex' -or $WorkerFailure.provider -cne 'codex'){throw 'Prior stopped preservation/configuration policy differs.'}
 if($WorkerStart.runtime.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $WorkerStart.runtime.configuration_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $WorkerStart.runtime.revision.source_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'){throw 'Prior runtime differs.'}
 foreach($event in @($Before,$Login)){
  if($event.schema -cne 'cochem-six-worker-auth-event/1' -or $event.provider -cne 'codex' -or $event.slot -cne $FailedSlot -or $event.nonce -cne $WorkerStart.nonce){throw 'Prior login event binding differs.'}
 }
 if($Before.state -cne 'STATUS_BEFORE_COMPLETE' -or $Before.proof.decision -cne 'ATTENDED_LOGIN_REQUIRED' -or $Before.proof.stage -cne 'before' -or
    $Before.proof.receipt_path -cne $(if($Version -ceq 'v1'){"C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-$FailedSlot-codex-before\worker-native-status.json"}else{"C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-$FailedSlot-codex-before\worker-native-status.json"}) -or
    $Before.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or $Login.state -cne 'LOGIN_STARTED'){throw 'Prior attempt differs from the reported slot1 browser-login failure.'}
}

function Read-RecoveryPriorCustody {
 $oldCommission='C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261007-r3-v1'
 $oldAuth='C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261007-r3-v1'
 $oldWorker='C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-codex'
 foreach($root in @($oldCommission,$oldAuth,$oldWorker)){
  Assert-SeriesPrivateRoot $root
  Assert-RecoveryAbsent (Join-Path $root 'series-complete.json')
 }
 foreach($path in @(
  (Join-Path $oldCommission 'first_controller_start-STARTED.json'),
  (Join-Path $oldCommission 'native_authentication-VERIFIED.json'),
  (Join-Path $oldAuth 'codex-VERIFIED.json'),
  (Join-Path $oldAuth 'claude-STARTED.json'),
  (Join-Path $oldWorker 'slot1-LOGIN_EXITED_ZERO.json'),
  (Join-Path $oldWorker 'slot2-STATUS_BEFORE_STARTED.json'),
  'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-claude'
 )){Assert-RecoveryAbsent $path}
 $specs=@(
  [pscustomobject]@{key='CommissionStart';path=(Join-Path $oldCommission 'series-start.json')},
  [pscustomobject]@{key='CommissionFailure';path=(Join-Path $oldCommission 'series-failed.json')},
  [pscustomobject]@{key='AuthStart';path=(Join-Path $oldAuth 'series-start.json')},
  [pscustomobject]@{key='AuthFailure';path=(Join-Path $oldAuth 'series-failed.json')},
  [pscustomobject]@{key='WorkerStart';path=(Join-Path $oldWorker 'series-start.json')},
  [pscustomobject]@{key='WorkerFailure';path=(Join-Path $oldWorker 'series-failed.json')},
  [pscustomobject]@{key='Before';path=(Join-Path $oldWorker 'slot1-STATUS_BEFORE_COMPLETE.json')},
  [pscustomobject]@{key='Login';path=(Join-Path $oldWorker 'slot1-LOGIN_STARTED.json')}
 )
 $values=@{};$proofs=@()
 foreach($spec in $specs){
  $control=Read-R3Control $spec.path '' 65536
  $values[$spec.key]=(Read-R3Text $control)|ConvertFrom-Json
  $held.Add((Open-VerifiedFile $spec.path $control.Sha256 (Get-Item -LiteralPath $spec.path).Length))
  $proofs+=[pscustomobject]@{path=$spec.path;sha256=$control.Sha256}
 }
 Assert-RecoveryPriorRecords @values
 $leaf=$values.Before.proof
 $control=Read-R3Control $leaf.receipt_path $leaf.receipt_sha256 65536
 $held.Add((Open-VerifiedFile $leaf.receipt_path $control.Sha256 (Get-Item -LiteralPath $leaf.receipt_path).Length))
 $proofs+=[pscustomobject]@{path=$leaf.receipt_path;sha256=$control.Sha256}
 return [pscustomobject]@{schema='cochem-failed-login-recovery-custody/1';reported_failure='codex_slot1_device_login';prior_receipts=$proofs;old_roots_modified=$false;prior_status_authorizes_new_login=$false;fresh_status_required=$true;controller_start_previously_requested=$false}
}

# New helpers for attended continuation. No login or deployment on import.
function Enter-AttendedCommissioning {
 $security=[Security.AccessControl.MutexSecurity]::new()
 $security.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)')
 $created=$false
 $mutex=[Threading.Mutex]::new($true,'Global\CoChemPipeline427-AttendedCommissioning',[ref]$created,$security)
 if(-not $created){$mutex.Dispose();throw 'Another attended commissioning command is active. Finish or close that console before starting another; no new attempt was started.'}
 return $mutex
}

function Read-SecondFailedAuthenticationCustody {
 $oldCommission='C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v2'
 $oldAuth='C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v2'
 $oldWorker='C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v2-codex'
 foreach($root in @($oldCommission,$oldAuth,$oldWorker)){Assert-SeriesPrivateRoot $root;Assert-RecoveryAbsent (Join-Path $root 'series-complete.json')}
 foreach($path in @((Join-Path $oldCommission 'first_controller_start-STARTED.json'),(Join-Path $oldCommission 'native_authentication-VERIFIED.json'),(Join-Path $oldAuth 'codex-VERIFIED.json'),(Join-Path $oldAuth 'claude-STARTED.json'),(Join-Path $oldWorker 'slot5-LOGIN_EXITED_ZERO.json'),(Join-Path $oldWorker 'slot6-STATUS_BEFORE_STARTED.json'),'C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v2-claude')){Assert-RecoveryAbsent $path}
 $specs=@(
  [pscustomobject]@{key='CommissionStart';path=(Join-Path $oldCommission 'series-start.json')},
  [pscustomobject]@{key='CommissionFailure';path=(Join-Path $oldCommission 'series-failed.json')},
  [pscustomobject]@{key='AuthStart';path=(Join-Path $oldAuth 'series-start.json')},
  [pscustomobject]@{key='AuthFailure';path=(Join-Path $oldAuth 'series-failed.json')},
  [pscustomobject]@{key='WorkerStart';path=(Join-Path $oldWorker 'series-start.json')},
  [pscustomobject]@{key='WorkerFailure';path=(Join-Path $oldWorker 'series-failed.json')},
  [pscustomobject]@{key='Before';path=(Join-Path $oldWorker 'slot5-STATUS_BEFORE_COMPLETE.json')},
  [pscustomobject]@{key='Login';path=(Join-Path $oldWorker 'slot5-LOGIN_STARTED.json')}
 )
 $values=@{};$proofs=@()
 foreach($spec in $specs){
  $control=Read-R3Control $spec.path '' 65536;$values[$spec.key]=(Read-R3Text $control)|ConvertFrom-Json
  $proofs+=[pscustomobject]@{path=$spec.path;sha256=$control.Sha256}
 }
 Assert-RecoveryPriorRecords @values -FailedSlot slot5 -Version v2
 $leaf=$values.Before.proof;$null=Read-R3Control $leaf.receipt_path $leaf.receipt_sha256 65536
 $proofs+=[pscustomobject]@{path=$leaf.receipt_path;sha256=$leaf.receipt_sha256}
 foreach($n in 1..4){
  $events=@()
  foreach($state in @('REUSED_VERIFIED_SESSION','AUTHENTICATED_AFTER_LOGIN')){
   $path=Join-Path $oldWorker "slot$n-$state.json"
   try{$null=Get-Item -LiteralPath $path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{continue}
   $control=Read-R3Control $path '' 65536;$event=(Read-R3Text $control)|ConvertFrom-Json
   if($event.schema -cne 'cochem-six-worker-auth-event/1' -or $event.provider -cne 'codex' -or $event.slot -cne "slot$n" -or $event.state -cne $state -or $event.nonce -cne $values.WorkerStart.nonce -or $event.proof.decision -cne 'REUSE_VERIFIED_SESSION'){throw 'Earlier profile completion metadata differs.'}
   $expectedStage=if($state -ceq 'REUSED_VERIFIED_SESSION'){'before'}else{'after'}
   $expected="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v2-slot$n-codex-$expectedStage\worker-native-status.json"
   if($event.proof.stage -cne $expectedStage -or $event.proof.receipt_path -cne $expected -or $event.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Earlier profile proof path differs.'}
   $null=Read-R3Control $expected $event.proof.receipt_sha256 65536
   $proofs+=[pscustomobject]@{path=$path;sha256=$control.Sha256};$proofs+=[pscustomobject]@{path=$expected;sha256=$event.proof.receipt_sha256};$events+=$event
  }
  if($events.Count -ne 1){throw 'Each of the first four profiles must have one preserved completion event.'}
 }
 return [pscustomobject]@{schema='cochem-failed-login-recovery-custody/1';reported_failure='codex_slot5_device_login';prior_receipts=$proofs;prior_completed_profiles=4;old_roots_modified=$false;prior_status_authorizes_new_login=$false;fresh_status_required=$true;controller_start_previously_requested=$false}
}

function Read-CommissioningPausedAuthEvidence {
 $root="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-paused.json';$control=Read-R3Control $path '' 65536;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.schema -cne 'cochem-both-providers-status-first-paused/1' -or $v.status -cne 'AUTHENTICATION_PAUSED_BEFORE_LOGIN' -or $v.attempt -cne $Attempt -or
    $v.exit_code -isnot [int] -or $v.exit_code -ne 20 -or $v.model_jobs_executed -ne 0 -or
    $v.activation_ready -isnot [bool] -or $v.activation_ready -or $v.pipeline_started -isnot [bool] -or $v.pipeline_started -or
    $v.series_preserved -isnot [bool] -or -not $v.series_preserved -or $v.automatic_retry_allowed -isnot [bool] -or $v.automatic_retry_allowed){throw 'Authentication pause is not bound to the current attempt.'}
 return [pscustomobject]@{status=$v.status;attempt=$Attempt;receipt_path=$path;receipt_sha256=$control.Sha256;controller_start_requested=$false}
}

function Read-CommissioningAuthEvidence {
 $root="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-complete.json';$control=Read-R3Control $path '' 65536
 $value=(Read-R3Text $control)|ConvertFrom-Json
 foreach($key in @('configuration_changed','provider_account_identity_verified','serving_model_verified','activation_ready')){if($value.$key -isnot [bool] -or $value.$key -ne $false){throw 'Authentication receipt has an invalid boolean flag.'}}
 foreach($key in @('agy_integration_hold_preserved','series_preserved')){if($value.$key -isnot [bool] -or $value.$key -ne $true){throw 'Authentication preservation is not verified.'}}
 foreach($key in @('model_jobs_executed','browser_login_commands_executed','existing_sessions_reused')){if(($value.$key -isnot [int] -and $value.$key -isnot [long]) -or $value.$key -lt 0 -or $value.$key -gt 12){throw 'Authentication receipt has an invalid count.'}}
 if($value.attempt -cne $Attempt -or $value.schema -cne 'cochem-both-providers-status-first-result/1' -or $value.status -cne 'CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED' -or
    $value.nonce -cnotmatch '^[a-f0-9]{32}$' -or $value.model_jobs_executed -ne 0 -or $value.configuration_changed -ne $false -or
    $value.provider_account_identity_verified -ne $false -or $value.serving_model_verified -ne $false -or $value.activation_ready -ne $false -or
    $value.agy_integration_hold_preserved -ne $true -or $value.series_preserved -ne $true -or @($value.provider_results).Count -ne 2){throw 'Combined native authentication completion is not the reviewed result.'}
 $logins=0;$reused=0
 for($index=0;$index -lt 2;$index++){
  $provider=@('codex','claude')[$index];$proof=Read-CompletedProviderAuth $provider;$captured=$value.provider_results[$index]
  if(($captured|ConvertTo-Json -Depth 8 -Compress) -cne ($proof|ConvertTo-Json -Depth 8 -Compress)){throw 'Combined authentication proof differs from retained provider receipts.'}
  $logins+=$proof.login_commands_executed;$reused+=$proof.existing_sessions_reused
 }
 if($value.browser_login_commands_executed -ne $logins -or $value.existing_sessions_reused -ne $reused -or $logins+$reused -ne 12){throw 'Combined authentication counts do not cover twelve provider/profile pairs.'}
 [pscustomobject]@{status=$value.status;profiles_verified=12;browser_login_commands_executed=$logins;existing_sessions_reused=$reused;receipt_path=$path;receipt_sha256=$control.Sha256}
}

function Assert-CommissioningRunningEvidence {
 param($Value)
 foreach($key in @('exactly_one_start_requested','monitoring_started')){if($Value.$key -isnot [bool] -or $Value.$key -ne $true){throw 'Controller commissioning flag is invalid.'}}
 foreach($key in @('full_srs_acceptance','automatic_repair_enabled','automatic_retry_allowed')){if($Value.$key -isnot [bool] -or $Value.$key -ne $false){throw 'First start must not claim full acceptance or enable automatic repair/retry.'}}
 foreach($key in @('authenticated_profiles_verified','model_jobs_submitted')){if(($Value.$key -isnot [int] -and $Value.$key -isnot [long]) -or $Value.$key -lt 0){throw 'Controller commissioning count is invalid.'}}
 if($Value.schema -cne 'cochem-warden-commissioning/1' -or $Value.status -cne 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED' -or
    $Value.runtime_root -cne $installRoot -or
    $Value.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $Value.config_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $Value.source_manifest_sha256 -cne '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1' -or
    $Value.revision_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4' -or
    $Value.authenticated_profiles_verified -ne 12 -or $Value.task_name -cne 'CoChem-4.2.7-Warden' -or
    $Value.exactly_one_start_requested -ne $true -or $Value.model_jobs_submitted -ne 0 -or $Value.full_srs_acceptance -ne $false -or
    $Value.queue_launch_output -cne 'C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1' -or $Value.monitoring_started -ne $true -or
    $Value.monitoring_scope -cne 'heartbeat_and_queue_only' -or $Value.system_sid -cne 'S-1-5-18'){throw 'Controller commissioning receipt does not establish the reviewed first start.'}
 $controller=$Value.controller
 foreach($key in @('pid','creation_filetime','first_sequence','final_sequence')){if(($controller.$key -isnot [int] -and $controller.$key -isnot [long]) -or $controller.$key -le 0){throw 'Controller process/progress identifiers are invalid.'}}
 if($controller.pid -gt 4294967295 -or $controller.instance_id -cnotmatch '^[a-f0-9]{32}$' -or $controller.final_sequence -le $controller.first_sequence){throw 'Controller process identity or heartbeat progress is invalid.'}
 return $controller
}

function Read-CommissioningRunningEvidence {
 $path=Join-Path $activationRoot 'commissioning.json'
 $control=Read-R3Control $path '' 65536;$value=(Read-R3Text $control)|ConvertFrom-Json
 $controller=Assert-CommissioningRunningEvidence $value
 [pscustomobject]@{status=$value.status;receipt_path=$path;receipt_sha256=$control.Sha256;controller=$controller;full_srs_acceptance=$false;model_jobs_submitted=0}
}

function Assert-CommissioningPreview {
 param([string]$Phase,$Value)
 if($Phase -ceq 'native_authentication'){
  if($Value.schema -cne 'cochem-both-providers-status-first-plan/1' -or $Value.mode -cne 'READ_ONLY_PLAN' -or
     $Value.identity_count -ne 6 -or $Value.shared_capacity -ne 4 -or $Value.maximum_browser_authorizations -ne 12 -or
     ($Value.providers -join ',') -cne 'codex,claude' -or $Value.model_jobs_executed -ne 0 -or
     $Value.configuration_changed -ne $false -or $Value.activation_ready -ne $false -or $Value.status_first -ne $true){throw 'Native authentication preview differs from its reviewed contract.'}
 }elseif($Phase -ceq 'first_controller_start'){
  if($Value.schema -cne 'cochem-warden-commissioning-plan/1' -or $Value.mode -cne 'READ_ONLY_PLAN' -or
     $Value.runtime_root -cne $installRoot -or $Value.identities -ne 6 -or $Value.shared_slots -ne 4 -or
     $Value.auth_completion_present -isnot [bool] -or $Value.auth_completion_required -isnot [bool] -or $Value.auth_completion_required -ne $true -or
     $Value.system_preflight_deferred -isnot [bool] -or $Value.system_preflight_deferred -ne $true -or
     $Value.monitoring_scope -cne 'heartbeat_and_queue_only' -or $Value.model_jobs_submitted -ne 0){throw 'Controller first-start preview differs from its reviewed contract.'}
  foreach($key in @('full_srs_acceptance','automatic_repair_enabled','automatic_retry_allowed')){if($Value.$key -isnot [bool] -or $Value.$key -ne $false){throw 'Controller first-start preview has an invalid acceptance/repair/retry flag.'}}
 }else{throw 'Unknown commissioning preview phase.'}
 foreach($hold in @($Value.holds)){if($hold -isnot [string] -or [string]::IsNullOrWhiteSpace($hold) -or $hold.Length -gt 2048){throw 'Preflight hold metadata is invalid.'}}
 return $Value
}

function Get-CommissioningPreview {
 param([string]$Phase)
 $source=if($Phase -ceq 'native_authentication'){$authSource}else{$activationSource}
 $prior=$ErrorActionPreference
 try{$ErrorActionPreference='Continue';$output=@(& $powershell -NoLogo -NoProfile -NonInteractive -File $source -Attempt $Attempt 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$prior}
 $text=($output|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($code -ne 0 -or $text.Length -gt 131072){throw "The read-only $Phase preflight did not complete (exit $code). No browser or controller phase was started."}
 try{$value=$text|ConvertFrom-Json}catch{throw 'Commissioning preview did not return valid bounded metadata.'}
 return Assert-CommissioningPreview $Phase $value
}

function Get-CommissioningPreviewHolds {
 param($AuthPlan,$ActivationPlan)
 foreach($hold in @($AuthPlan.holds)){"native_authentication: $hold"}
 $expected='Completed status-first authentication is required before first start.'
 $expectedCount=0
 foreach($hold in @($ActivationPlan.holds)){
  if($hold -ceq $expected -and $ActivationPlan.auth_completion_present -ceq $false){$expectedCount++;continue}
  "first_controller_start: $hold"
 }
 if($ActivationPlan.auth_completion_present -ceq $false -and $expectedCount -ne 1){'Controller preflight did not explicitly identify its one pending authentication prerequisite.'}
}

function Invoke-CommissioningPhases {
 param([scriptblock]$Invoke,[scriptblock]$Verify,[scriptblock]$Record)
 foreach($phaseName in @('native_authentication','first_controller_start')){
  & $Record $phaseName 'STARTED' $null
  $code=& $Invoke $phaseName
  if($phaseName -ceq 'native_authentication' -and $code -is [int] -and $code -eq 20){& $Record $phaseName 'PAUSED' (Read-CommissioningPausedAuthEvidence);return 20}
  if($code -isnot [int] -or $code -ne 0){throw 'Commissioning phase did not complete. Preserve its printed evidence; no later phase or automatic repeat was started.'}
  $proof=& $Verify $phaseName
  & $Record $phaseName 'VERIFIED' $proof
 }
}

$ownsRoot=$false;$phase='preflight';$nonce=$null;$commissionGate=$null
try{
 if($Apply){$commissionGate=Enter-AttendedCommissioning}
 if($activationHash -cnotmatch '^[a-f0-9]{64}$'){throw 'Combined commissioning is still being prepared; no Apply is available.'}
 foreach($d in @(Import-CommissioningFunctions (Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v4.ps1') 'c908a3b2ece9858e23b023cab0557cab5ccadd03c3efc0460317bd62a1598bdb' @('Initialize-SeriesDirectory','Assert-SeriesPrivateRoot','Write-SeriesRecord','Invoke-SeriesConsoleChild'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-CommissioningFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))};Initialize-FileIdentity
 foreach($d in @(Import-CommissioningFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-CommissioningFunctions (Join-Path $PSScriptRoot 'login_pipeline_worker_interactive_r3_v4.ps1') '6f2181eb8e5ba5dc701ee9d69b496de01ffa5d7d74880bfa96e702dbd2e5c150' @('New-PrivateAcl','Get-ExactTaskOrAbsent'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-CommissioningFunctions $authSource $authHash @('Read-CompletedProviderAuth'))){. ([scriptblock]::Create($d))}
 $held.Add((Open-VerifiedFile $activationSource $activationHash (Get-Item -LiteralPath $activationSource).Length))
 $runtime=Assert-R3InstalledBindings
 $authPlan=Get-CommissioningPreview 'native_authentication'
 $activationPlan=Get-CommissioningPreview 'first_controller_start'
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 $holds=@(Get-CommissioningPreviewHolds $authPlan $activationPlan)
 $priorCustody=$null
 try{$priorCustody=[pscustomobject]@{first_attempt=(Read-RecoveryPriorCustody);second_attempt=(Read-SecondFailedAuthenticationCustody)}}catch{if($Apply){throw};$holds+='Prior failed-login receipts require owner read-only verification.'}
 foreach($path in @($seriesRoot,"C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt",$activationRoot)){if(Test-Path -LiteralPath $path -ErrorAction Stop){$holds+='An immutable attempt or activation artifact already exists. Preserve it; this exact namespace cannot be reused.'}}
 foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){$task=Get-ExactTaskOrAbsent $folder $name;if($name -ceq 'CoChem-4.2.7-Warden' -and $null -ne $task){$holds+='An existing Warden task must be preserved.'}elseif($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){$holds+='A managed daemon is not stopped and disabled.'}}
 if(-not $Apply){[ordered]@{schema='cochem-pipeline-commissioning-series-plan/1';mode='READ_ONLY_PLAN';phases=@('native_authentication','first_controller_start');runtime=$runtime;identity_count=6;shared_capacity=4;maximum_browser_authorizations=12;actual_browser_authorizations_required=$null;automatic_repair_enabled=$false;full_srs_acceptance=$false;model_jobs_submitted=0;tasks_registered=0;series_root=$seriesRoot;phase_previews=@($authPlan,$activationPlan);holds=$holds}|ConvertTo-Json -Depth 9;return}
 if($holds.Count){throw ($holds -join ' ')}
 Assert-ProtectedPath (Split-Path -Parent $seriesRoot);Initialize-SeriesDirectory
 [CoChemNativeLoginSeriesDirectory]::Create($seriesRoot);$ownsRoot=$true;Assert-SeriesPrivateRoot $seriesRoot
 $nonce=[Guid]::NewGuid().ToString('N');$phase='reviewed_series';$script:completedPhases=[Collections.Generic.List[object]]::new()
 Write-SeriesRecord (Join-Path $seriesRoot 'series-start.json') ([ordered]@{schema='cochem-pipeline-commissioning-series/1';status='IN_PROGRESS';attempt=$Attempt;nonce=$nonce;runtime=$runtime;auth_helper_sha256=$authHash;activation_helper_sha256=$activationHash;started_utc=[DateTime]::UtcNow.ToString('o')})
 Write-SeriesRecord (Join-Path $seriesRoot 'preserved-prior-attempt.json') $priorCustody
 Write-SeriesRecord (Join-Path $seriesRoot 'preflight-authentication.json') $authPlan
 Write-SeriesRecord (Join-Path $seriesRoot 'preflight-first-start.json') $activationPlan
 $phaseOutcome=Invoke-CommissioningPhases {
  param($phaseName)
  Write-Host "Running reviewed commissioning phase: $phaseName"
  if($phaseName -ceq 'native_authentication'){Invoke-SeriesConsoleChild @('-NoProfile','-File',$authSource,'-Attempt',$Attempt,'-Apply','-Interactive')}
  else{Invoke-SeriesConsoleChild @('-NoProfile','-File',$activationSource,'-Attempt',$Attempt,'-Apply')}
 } {
  param($phaseName)
  if($phaseName -ceq 'native_authentication'){Read-CommissioningAuthEvidence}else{Read-CommissioningRunningEvidence}
 } {
  param($phaseName,$state,$proof)
  Write-SeriesRecord (Join-Path $seriesRoot "$phaseName-$state.json") ([ordered]@{schema='cochem-pipeline-commissioning-event/1';nonce=$nonce;phase=$phaseName;state=$state;proof=$proof;observed_utc=[DateTime]::UtcNow.ToString('o')})
  if($state -ceq 'VERIFIED'){$script:completedPhases.Add([pscustomobject]@{phase=$phaseName;proof=$proof})}
 }
 if($phaseOutcome -is [int] -and $phaseOutcome -eq 20){
  $paused=[ordered]@{schema='cochem-pipeline-commissioning-series-paused/1';status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';attempt=$Attempt;nonce=$nonce;controller_start_requested=$false;model_jobs_submitted=0;next='Run this same command when ready. It creates a fresh authentication attempt and reuses current valid sign-ins; no automatic retry was started.'}
  Write-SeriesRecord (Join-Path $seriesRoot 'series-paused.json') $paused;$paused|ConvertTo-Json -Depth 5;exit 20
 }
 if($script:completedPhases.Count -ne 2){throw 'Commissioning did not verify both phases.'}
 $result=[ordered]@{schema='cochem-pipeline-commissioning-series-result/1';status='WARDEN_RUNNING_NATIVE_PROFILES_AUTHENTICATED';attempt=$Attempt;nonce=$nonce;phases=@($script:completedPhases.ToArray());identity_count=6;shared_capacity=4;automatic_repair_enabled=$false;full_srs_acceptance=$false;model_jobs_submitted=0;finished_utc=[DateTime]::UtcNow.ToString('o');next='Codex performs live routed workflows and MCP checks; reboot and unattended stability acceptance remain.'}
 Write-SeriesRecord (Join-Path $seriesRoot 'series-complete.json') $result;$result|ConvertTo-Json -Depth 9
}catch{
 if($ownsRoot){try{Write-SeriesRecord (Join-Path $seriesRoot 'series-failed.json') ([ordered]@{schema='cochem-pipeline-commissioning-series-failure/1';attempt=$Attempt;nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;automatic_repeat_allowed=$false;manual_authentication_continuation_requires_fresh_attempt_and_absent_activation=$true;partial_outputs_preserved=$true;full_srs_acceptance=$false})}catch{}}
 throw
}finally{foreach($stream in $held){$stream.Dispose()};if($null -ne $commissionGate){try{$commissionGate.ReleaseMutex()}finally{$commissionGate.Dispose()}}}
