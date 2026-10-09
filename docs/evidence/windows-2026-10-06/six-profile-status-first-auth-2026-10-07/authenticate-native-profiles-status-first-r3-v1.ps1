#Requires -Version 5.1
<# Default preview only. One attended command for Codex then Claude across the
   six existing isolated profiles. Existing verified sessions are reused.
   No model, daemon activation, configuration change or automatic repeat. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
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
$seriesRoot='C:\Program Files\CoChem\NativeAuthBoth4.2.7-windows-20261007-r3-v1'
$providerSeries=Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v1.ps1'
$providerSeriesHash='9e742d4df0b7faa07a6073dc4bc7f15ccf2924df9a14443a5c662bfbc7ff0361'
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
 try{$ErrorActionPreference='Continue';$output=@(& $powershell -NoLogo -NoProfile -NonInteractive -File $providerSeries -Provider $Provider 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$savedPreference}
 $text=($output|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($code -ne 0 -or $text.Length -gt 65536){throw 'The provider authentication preview could not establish safe prerequisites. No browser authentication was started.'}
 try{$p=$text|ConvertFrom-Json}catch{throw 'Provider authentication preview was not valid metadata.'}
 if($p.schema -cne 'cochem-six-worker-status-first-plan/1' -or $p.mode -cne 'READ_ONLY_PLAN' -or $p.provider -cne $Provider -or
    $p.series_root -cne "C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-$Provider" -or
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
 $root="C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261007-r3-v1-$Provider"
 Assert-SeriesPrivateRoot $root
 $path=Join-Path $root 'series-complete.json';$c=Read-R3Control $path '' 65536;$v=(Read-R3Text $c)|ConvertFrom-Json
 if($v.schema -cne 'cochem-six-worker-status-first-result/1' -or $v.status -cne 'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or
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
  $expected="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-$($row.slot)-$Provider-$($row.proof.stage)\worker-native-status.json"
  if($row.proof.receipt_path -cne $expected){throw 'Provider result refers to another status path.'}
  $null=Read-R3Control $expected $row.proof.receipt_sha256 32768
 }
 [pscustomobject]@{provider=$Provider;status=$v.status;profiles_verified=6;login_commands_executed=$v.login_commands_executed;existing_sessions_reused=$v.existing_sessions_reused;receipt_path=$path;receipt_sha256=$c.Sha256}
}
function Invoke-BothProviderAuth {
 param([scriptblock]$Invoke,[scriptblock]$Verify,[scriptblock]$Record)
 foreach($provider in @('codex','claude')){
  & $Record $provider 'STARTED' $null
  $exitCode=& $Invoke $provider
  if($exitCode -isnot [int] -or $exitCode -ne 0){throw 'Provider authentication series did not complete. Preserve all partial evidence; no automatic repeat or later provider was started.'}
  $proof=& $Verify $provider
  & $Record $provider 'VERIFIED' $proof
 }
}
$ownsRoot=$false;$nonce=$null;$phase='preflight'
try{
 foreach($d in @(Import-OuterAuthFunctions $providerSeries $providerSeriesHash @('Initialize-SeriesDirectory','Assert-SeriesPrivateRoot','Write-SeriesRecord','Assert-SeriesDaemonsStopped','Invoke-SeriesConsoleChild'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-OuterAuthFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))};Initialize-FileIdentity
 foreach($d in @(Import-OuterAuthFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-OuterAuthFunctions (Join-Path $PSScriptRoot 'login_pipeline_worker_interactive_r3.ps1') '1ec70cd0beeacde2d11df1948ac009be92fbdd5e5cdb8cf78b3f7f6346920109' @('New-PrivateAcl','Get-ExactTaskOrAbsent'))){. ([scriptblock]::Create($d))}
 foreach($row in @(
  [pscustomobject]@{name='check-worker-native-auth-status-six-r3-v1.ps1';pin='60bb748c981f989efbfdee76ece1b1c88986de2debd844ec72d10c2e24b20cd4'},
  [pscustomobject]@{name='worker-native-auth-status-six-r3-v1.py';pin='cd93a330a2d4c578871b4d479067cc5f65f5ddd49bf2a6154c2910f63b5d1ea5'},
  [pscustomobject]@{name='worker_claude_login_bridge_r3.py';pin='b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'},
  [pscustomobject]@{name='worker-native-status-r3.py';pin='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5'})){
  $path=Join-Path $PSScriptRoot $row.name;$held.Add((Open-VerifiedFile $path $row.pin (Get-Item -LiteralPath $path).Length))
 }
 $null=Read-R3Control 'C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006\repository\scripts\login_pipeline_worker.ps1' 'e3d4d93f58446e05e4d585ac9215410527f8d1ec2a884153dbf69feb4ee7b32a'
 $plans=@(Get-ProviderAuthPlan codex;Get-ProviderAuthPlan claude)
 $holds=@();foreach($p in $plans){foreach($hold in $p.holds){$holds+="$($p.provider): $hold"}}
 if(Test-Path -LiteralPath $seriesRoot -ErrorAction Stop){$holds+='An earlier combined authentication series exists; preserve it. No automatic repeat or resume.'}
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-SeriesDaemonsStopped $folder
 if(-not $Apply){[ordered]@{schema='cochem-both-providers-status-first-plan/1';mode='READ_ONLY_PLAN';providers=@('codex','claude');identity_count=6;shared_capacity=4;maximum_browser_authorizations=12;actual_browser_authorizations_required=$null;status_first=$true;configuration_changed=$false;model_jobs_executed=0;activation_ready=$false;series_root=$seriesRoot;holds=$holds}|ConvertTo-Json -Depth 5;return}
 if($holds.Count){throw ($holds -join ' ')}
 Assert-SeriesDaemonsStopped $folder;Assert-ProtectedPath (Split-Path -Parent $seriesRoot)
 Initialize-SeriesDirectory;[CoChemNativeLoginSeriesDirectory]::Create($seriesRoot);$ownsRoot=$true;Assert-SeriesPrivateRoot $seriesRoot
 $nonce=[Guid]::NewGuid().ToString('N');$phase='attended_provider_series';$script:completedProviders=[Collections.Generic.List[object]]::new()
 Write-SeriesRecord (Join-Path $seriesRoot 'series-start.json') ([ordered]@{schema='cochem-both-providers-status-first/1';status='IN_PROGRESS';nonce=$nonce;started_utc=[DateTime]::UtcNow.ToString('o');configuration_changed=$false;activation_ready=$false})
 Invoke-BothProviderAuth {
  param($provider)
  Assert-SeriesDaemonsStopped $folder
  Write-Host "Checking $provider in six isolated profiles; browser approval will be requested only for an explicitly logged-out profile."
  Invoke-SeriesConsoleChild @('-NoProfile','-File',$providerSeries,'-Provider',$provider,'-Apply','-Interactive')
 } {
  param($provider)
  Assert-SeriesDaemonsStopped $folder;Read-CompletedProviderAuth $provider
 } {
  param($provider,$state,$proof)
  Write-SeriesRecord (Join-Path $seriesRoot "$provider-$state.json") ([ordered]@{schema='cochem-both-providers-auth-event/1';nonce=$nonce;provider=$provider;state=$state;proof=$proof;observed_utc=[DateTime]::UtcNow.ToString('o')})
  if($state -ceq 'VERIFIED'){$script:completedProviders.Add($proof)}
 }
 Assert-SeriesDaemonsStopped $folder
 if($script:completedProviders.Count -ne 2){throw 'Both configured providers were not authenticated.'}
 $phase='completion';$total=0;$reused=0;foreach($p in $script:completedProviders){$total+=$p.login_commands_executed;$reused+=$p.existing_sessions_reused}
 $result=[ordered]@{schema='cochem-both-providers-status-first-result/1';status='CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED';nonce=$nonce;provider_results=@($script:completedProviders.ToArray());browser_login_commands_executed=$total;existing_sessions_reused=$reused;model_jobs_executed=0;configuration_changed=$false;provider_account_identity_verified=$false;serving_model_verified=$false;activation_ready=$false;agy_integration_hold_preserved=$true;series_preserved=$true;finished_utc=[DateTime]::UtcNow.ToString('o')}
 Write-SeriesRecord (Join-Path $seriesRoot 'series-complete.json') $result;$result|ConvertTo-Json -Depth 8
}catch{
 if($ownsRoot){try{Write-SeriesRecord (Join-Path $seriesRoot 'series-failed.json') ([ordered]@{schema='cochem-both-providers-status-first-failure/1';nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;automatic_repeat_allowed=$false;activation_ready=$false})}catch{}}
 throw
}finally{foreach($stream in $held){$stream.Dispose()}}
