#Requires -Version 5.1
<# Default preview only. One attended command for Codex then Claude across the
   six existing isolated profiles. Existing verified sessions are reused.
   No model, daemon activation, configuration change or automatic repeat. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive,[ValidatePattern('^[a-f0-9]{32}$')][string]$Attempt='00000000000000000000000000000000')
if($Attempt -cnotmatch '^[a-f0-9]{32}$' -or ($Apply -and $Attempt -ceq '00000000000000000000000000000000')){throw 'A lowercase32-hex Attempt is required; Apply forbids the all-zero preview attempt.'}
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -ne 'ConsoleHost')){throw 'Apply requires the owner at an elevated interactive ConsoleHost, with -Interactive and no redirected input/output.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$nativeRoot='C:\Program Files\CoChem\Native4.2.7-windows-20261006'
$powershell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$seriesRoot="C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261008-r3-v3-$Attempt"
$providerSeries=Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v5.ps1'
$providerSeriesHash='6e18cf7d8a816dc36464593fdb9eaed51c7bd45963accb16481befa67df10d58'
$held=[Collections.Generic.List[IO.FileStream]]::new()
function Import-OuterAuthFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Reviewed authentication helper changed.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Authentication source parse failure.'}
  $found=@();foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -cin $Names){$found+=@($f.Name);$f.Extent.Text}}
  if(@($Names|Where-Object{$_ -cnotin $found}).Count -or @($found|Select-Object -Unique).Count -ne $found.Count){throw 'Authentication helper definitions are missing or duplicated.'}
  $held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}
function Get-ProviderAuthPlan {
 param([string]$Provider)
 $savedPreference=$ErrorActionPreference
 try{$ErrorActionPreference='Continue';$output=@(& $powershell -NoLogo -NoProfile -NonInteractive -File $providerSeries -Provider $Provider -Attempt $Attempt 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$savedPreference}
 $text=($output|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($code -ne 0 -or $text.Length -gt 65536){throw 'The provider authentication preview could not establish safe prerequisites. No browser authentication was started.'}
 try{$p=$text|ConvertFrom-Json}catch{throw 'Provider authentication preview was not valid metadata.'}
 if($p.attempt -cne $Attempt -or $p.schema -cne 'cochem-six-worker-status-first-plan/1' -or $p.mode -cne 'READ_ONLY_PLAN' -or $p.provider -cne $Provider -or
    $p.series_root -cne "C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$Provider" -or
    $p.runtime.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $p.runtime.configuration_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $p.runtime.revision.source_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4' -or
    ($p.selected_workers -join ',') -cne 'slot1,slot2,slot3,slot4,slot5,slot6' -or $p.shared_capacity -ne 4 -or $p.maximum_logins -ne 6 -or
    $p.configuration_applied -ne $false -or $p.native_commands_executed -ne 0 -or $p.model_jobs_executed -ne 0 -or $p.activation_ready -ne $false -or
    $p.browser_login_only_for_explicit_logged_out_status -ne $true -or $p.no_automatic_repeat_or_resume -ne $true){throw 'Provider preview differs from the reviewed six-profile status-first flow.'}
 return $p
}
function Read-CompletedProviderAuth {
 param([string]$Provider)
 $root="C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$Provider"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-complete.json';$c=Read-R3Control $path '' 65536;$v=(Read-R3Text $c)|ConvertFrom-Json
 if($v.attempt -cne $Attempt -or $v.schema -cne 'cochem-six-worker-status-first-result/1' -or $v.status -cne 'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or
    $v.provider -cne $Provider -or $v.nonce -cnotmatch '^[a-f0-9]{32}$' -or $v.selected_workers_verified -ne 6 -or
    $v.runtime.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $v.runtime.configuration_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $v.runtime.revision.source_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4' -or
    $v.configuration_applied -ne $false -or $v.model_jobs_executed -ne 0 -or $v.activation_ready -ne $false -or $v.series_preserved -ne $true){throw 'Provider completion is not a bound successful six-profile authentication result.'}
 foreach($key in @('login_commands_executed','existing_sessions_reused')){if(($v.$key -isnot [int] -and $v.$key -isnot [long]) -or $v.$key -lt 0 -or $v.$key -gt 6){throw 'Provider authentication count is invalid.'}}
 if($v.login_commands_executed+$v.existing_sessions_reused -ne 6 -or @($v.status_receipts).Count -ne 6){throw 'Provider status does not account for all six profiles.'}
 $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
 foreach($row in $v.status_receipts){
  if($row.slot -cnotin @('slot1','slot2','slot3','slot4','slot5','slot6') -or -not $seen.Add($row.slot) -or
     $row.status -cnotin @('REUSED_VERIFIED_SESSION','AUTHENTICATED_AFTER_LOGIN') -or $row.proof.decision -cne 'REUSE_VERIFIED_SESSION' -or
     $row.proof.stage -cne $(if($row.status -ceq 'REUSED_VERIFIED_SESSION'){'before'}else{'after'}) -or $row.proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$') {throw 'Provider result has incomplete or duplicated slot proof.'}
  $expected="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$($row.slot)-$Provider-$($row.proof.stage)\worker-native-status.json"
  if($row.proof.receipt_path -cne $expected){throw 'Provider result refers to another status path.'}
  $leaf=(Read-R3Text (Read-R3Control $expected $row.proof.receipt_sha256 32768))|ConvertFrom-Json
  if($leaf.attempt -cne $Attempt){throw 'Status proof belongs to a different explicit attempt.'}
 }
 [pscustomobject]@{attempt=$Attempt;provider=$Provider;status=$v.status;profiles_verified=6;login_commands_executed=$v.login_commands_executed;existing_sessions_reused=$v.existing_sessions_reused;receipt_path=$path;receipt_sha256=$c.Sha256}
}
function Read-PausedProviderAuth {
 param([string]$Provider)
 $root="C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$Provider"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-paused.json';$c=Read-R3Control $path '' 65536;$v=(Read-R3Text $c)|ConvertFrom-Json
 if($v.schema -cne 'cochem-six-worker-status-first-paused/1' -or $v.status -cne 'AUTHENTICATION_PAUSED_BEFORE_LOGIN' -or
    $v.attempt -cne $Attempt -or $v.provider -cne $Provider -or $v.nonce -cnotmatch '^[a-f0-9]{32}$' -or $v.exit_code -ne 20 -or
    $v.paused_slot -cnotin @('slot1','slot2','slot3','slot4','slot5','slot6') -or
    $v.runtime.install_receipt_sha256 -cne '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6' -or
    $v.runtime.configuration_sha256 -cne '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c' -or
    $v.runtime.revision.source_sha256 -cne '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'){throw 'Provider pause metadata is not bound to this attempt.'}
 foreach($key in @('configuration_applied','pipeline_started','activation_ready','automatic_retry_allowed')){if($v.$key -isnot [bool] -or $v.$key){throw 'Provider pause policy differs.'}}
 foreach($key in @('exit_code','model_jobs_executed')){if($v.$key -isnot [int] -and $v.$key -isnot [long]){throw 'Provider pause scalar type differs.'}}
 if($v.series_preserved -isnot [bool] -or -not $v.series_preserved -or $v.model_jobs_executed -ne 0){throw 'Provider pause preservation differs.'}
 foreach($key in @('login_commands_executed','existing_sessions_reused')){if(($v.$key -isnot [int] -and $v.$key -isnot [long]) -or $v.$key -lt 0 -or $v.$key -gt 5){throw 'Provider pause count differs.'}}
 $proof=$v.before_status_proof
 $expected="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$($v.paused_slot)-$Provider-before\worker-native-status.json"
 if($proof.stage -cne 'before' -or $proof.decision -cne 'ATTENDED_LOGIN_REQUIRED' -or $proof.receipt_path -cne $expected -or $proof.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Provider pause is not after a fresh explicit logged-out status.'}
 $leaf=(Read-R3Text (Read-R3Control $expected $proof.receipt_sha256 32768))|ConvertFrom-Json
 if($leaf.attempt -cne $Attempt -or $leaf.slot -cne $v.paused_slot -or $leaf.provider -cne $Provider -or $leaf.stage -cne 'before' -or
    $leaf.decision -cne 'ATTENDED_LOGIN_REQUIRED' -or $leaf.cleanup_verified -isnot [bool] -or -not $leaf.cleanup_verified){throw 'Paused status proof differs.'}
 [pscustomobject]@{attempt=$Attempt;provider=$Provider;status=$v.status;paused_slot=$v.paused_slot;receipt_path=$path;receipt_sha256=$c.Sha256;exit_code=20}
}
function Invoke-BothProviderAuth {
 param([scriptblock]$Invoke,[scriptblock]$Verify,[scriptblock]$Record,[scriptblock]$PauseVerify)
 foreach($provider in @('codex','claude')){
  & $Record $provider 'STARTED' $null
  $exitCode=& $Invoke $provider
  if($exitCode -is [int] -and $exitCode -eq 20){$proof=& $PauseVerify $provider;& $Record $provider 'PAUSED' $proof;return $false}
  if($exitCode -isnot [int] -or $exitCode -ne 0){throw 'Provider authentication series did not complete. Preserve all partial evidence; no automatic repeat or later provider was started.'}
  $proof=& $Verify $provider
  & $Record $provider 'VERIFIED' $proof
 }
 return $true
}
$ownsRoot=$false;$nonce=$null;$phase='preflight'
try{
 foreach($d in @(Import-OuterAuthFunctions $providerSeries $providerSeriesHash @('Initialize-SeriesDirectory','Assert-SeriesPrivateRoot','Write-SeriesRecord','Assert-SeriesDaemonsStopped','Invoke-SeriesConsoleChild'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-OuterAuthFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))};Initialize-FileIdentity
 foreach($d in @(Import-OuterAuthFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-OuterAuthFunctions (Join-Path $PSScriptRoot 'login_pipeline_worker_interactive_r3_v5.ps1') '8e3545109c2bbef9a53763f8af8575ac1ff7376d3bea6c27a5ecd8454af182ec' @('New-PrivateAcl','Get-ExactTaskOrAbsent'))){. ([scriptblock]::Create($d))}
 foreach($row in @(
  [pscustomobject]@{name='check-worker-native-auth-status-six-r3-v4.ps1';pin='4448e76eaeb30f2d2905f5aa6d3e14f0cc29b3d7e703321bb3df55afcba58e81'},
  [pscustomobject]@{name='worker-native-auth-status-six-r3-v3.py';pin='7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'},
  [pscustomobject]@{name='worker_claude_login_bridge_r3.py';pin='b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'},
  [pscustomobject]@{name='worker-native-status-r3.py';pin='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'})){
  $path=Join-Path $PSScriptRoot $row.name;$held.Add((Open-VerifiedFile $path $row.pin (Get-Item -LiteralPath $path).Length))
 }
 $null=Read-R3Control 'C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006\repository\scripts\login_pipeline_worker.ps1' 'e3d4d93f58446e05e4d585ac9215410527f8d1ec2a884153dbf69feb4ee7b32a'
 $plans=@(Get-ProviderAuthPlan codex;Get-ProviderAuthPlan claude)
 $holds=@();foreach($p in $plans){foreach($hold in $p.holds){$holds+="$($p.provider): $hold"}}
 if(Test-Path -LiteralPath $seriesRoot -ErrorAction Stop){$holds+='An earlier combined authentication series exists; preserve it. No automatic repeat or resume.'}
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-SeriesDaemonsStopped $folder
 if(-not $Apply){[ordered]@{schema='cochem-both-providers-status-first-plan/1';attempt=$Attempt;mode='READ_ONLY_PLAN';providers=@('codex','claude');identity_count=6;shared_capacity=4;maximum_browser_authorizations=12;actual_browser_authorizations_required=$null;status_first=$true;configuration_changed=$false;model_jobs_executed=0;activation_ready=$false;series_root=$seriesRoot;holds=$holds}|ConvertTo-Json -Depth 5;return}
 if($holds.Count){throw ($holds -join ' ')}
 Assert-SeriesDaemonsStopped $folder;Assert-ProtectedPath (Split-Path -Parent $seriesRoot)
 Initialize-SeriesDirectory;[CoChemNativeLoginSeriesDirectory]::Create($seriesRoot);$ownsRoot=$true;Assert-SeriesPrivateRoot $seriesRoot
 $nonce=[Guid]::NewGuid().ToString('N');$phase='attended_provider_series';$script:completedProviders=[Collections.Generic.List[object]]::new()
 Write-SeriesRecord (Join-Path $seriesRoot 'series-start.json') ([ordered]@{schema='cochem-both-providers-status-first/1';attempt=$Attempt;status='IN_PROGRESS';nonce=$nonce;started_utc=[DateTime]::UtcNow.ToString('o');configuration_changed=$false;activation_ready=$false})
 $completed=Invoke-BothProviderAuth {
  param($provider)
  Assert-SeriesDaemonsStopped $folder
  Write-Host "Checking $provider in six isolated profiles; browser approval will be requested only for an explicitly logged-out profile."
  Invoke-SeriesConsoleChild @('-NoProfile','-File',$providerSeries,'-Provider',$provider,'-Attempt',$Attempt,'-Apply','-Interactive')
 } {
  param($provider)
  Assert-SeriesDaemonsStopped $folder;Read-CompletedProviderAuth $provider
 } {
  param($provider,$state,$proof)
  Write-SeriesRecord (Join-Path $seriesRoot "$provider-$state.json") ([ordered]@{schema='cochem-both-providers-auth-event/1';attempt=$Attempt;nonce=$nonce;provider=$provider;state=$state;proof=$proof;observed_utc=[DateTime]::UtcNow.ToString('o')})
  if($state -ceq 'VERIFIED'){$script:completedProviders.Add($proof)}
  if($state -ceq 'PAUSED'){$script:pausedProvider=$provider;$script:pausedProviderProof=$proof}
 } {
  param($provider)
  Read-PausedProviderAuth $provider
 }
 Assert-SeriesDaemonsStopped $folder
 if(-not $completed){
  $paused=[ordered]@{schema='cochem-both-providers-status-first-paused/1';attempt=$Attempt;status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';nonce=$nonce;paused_provider=$script:pausedProvider;paused_provider_proof=$script:pausedProviderProof;provider_results=@($script:completedProviders.ToArray());model_jobs_executed=0;configuration_changed=$false;pipeline_started=$false;activation_ready=$false;agy_integration_hold_preserved=$true;series_preserved=$true;automatic_retry_allowed=$false;exit_code=20}
  Write-SeriesRecord (Join-Path $seriesRoot 'series-paused.json') $paused
  $paused|ConvertTo-Json -Depth 8
  exit 20
 }
 if($script:completedProviders.Count -ne 2){throw 'Both configured providers were not authenticated.'}
 $phase='completion';$total=0;$reused=0;foreach($p in $script:completedProviders){$total+=$p.login_commands_executed;$reused+=$p.existing_sessions_reused}
 $result=[ordered]@{schema='cochem-both-providers-status-first-result/1';attempt=$Attempt;status='CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED';nonce=$nonce;provider_results=@($script:completedProviders.ToArray());browser_login_commands_executed=$total;existing_sessions_reused=$reused;model_jobs_executed=0;configuration_changed=$false;provider_account_identity_verified=$false;serving_model_verified=$false;activation_ready=$false;agy_integration_hold_preserved=$true;series_preserved=$true;finished_utc=[DateTime]::UtcNow.ToString('o')}
 Write-SeriesRecord (Join-Path $seriesRoot 'series-complete.json') $result;$result|ConvertTo-Json -Depth 8
}catch{
 if($ownsRoot){try{Write-SeriesRecord (Join-Path $seriesRoot 'series-failed.json') ([ordered]@{schema='cochem-both-providers-status-first-failure/1';attempt=$Attempt;nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;automatic_repeat_allowed=$false;activation_ready=$false})}catch{}}
 throw
}finally{foreach($stream in $held){$stream.Dispose()}}
