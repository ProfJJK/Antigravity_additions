#Requires -Version 5.1
<# Default read-only. Apply is an explicit attended series for six configured existing
   workers and one provider. No output/code capture, model jobs, credential
   copying, provisioning or daemon activation. A fresh fixed private marker
   blocks every repeat, including partial or successful series; never resume. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,[switch]$Apply,[switch]$Interactive,[ValidatePattern('^[a-f0-9]{32}$')][string]$Attempt='00000000000000000000000000000000')
if($Attempt -cnotmatch '^[a-f0-9]{32}$' -or ($Apply -and $Attempt -ceq '00000000000000000000000000000000')){throw 'A lowercase32-hex Attempt is required; Apply forbids the all-zero preview attempt.'}
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
$Provider=$Provider.ToLowerInvariant()
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell5.1 on AETHERDESK.'}
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -ne 'ConsoleHost')){throw 'Apply requires the owner at an elevated interactive ConsoleHost, with -Interactive and no redirected input/output.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$nativeRoot='C:\Program Files\CoChem\Native4.2.7-windows-20261006'
$powershell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$seriesRoot="C:\Program Files\CoChem\NativeAuthSix4.2.7-windows-20261008-r3-v3-$Attempt-$Provider"
$codexHelper='C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006\repository\scripts\login_pipeline_worker.ps1'
$claudeHelper=Join-Path $PSScriptRoot 'login_pipeline_worker_interactive_r3_v5.ps1'
$baseStatusLeaf=Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1'
$statusPython=Join-Path $PSScriptRoot 'worker-native-auth-status-six-r3-v3.py'
$statusPythonHash='7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'
$statusLeafHash='4448e76eaeb30f2d2905f5aa6d3e14f0cc29b3d7e703321bb3df55afcba58e81'
$statusLeaf=Join-Path $PSScriptRoot 'check-worker-native-auth-status-six-r3-v4.ps1'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$held=[Collections.Generic.List[IO.FileStream]]::new()
function Import-SeriesFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Reviewed series function source changed.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Reviewed function source did not parse.'}
  $found=@();foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
  if(@($Names|Where-Object{$_ -notin $found}).Count -or @($found|Select-Object -Unique).Count -ne $found.Count){throw 'Missing or duplicate reviewed function definition.'}
  $held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}
function Initialize-SeriesDirectory {
 if('CoChemNativeLoginSeriesDirectory' -as [type]){return}
 Add-Type -TypeDefinition @"
using System;using System.Runtime.InteropServices;
public static class CoChemNativeLoginSeriesDirectory {
 [StructLayout(LayoutKind.Sequential)] struct SA {public int length;public IntPtr descriptor;[MarshalAs(UnmanagedType.Bool)]public bool inherit;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool CreateDirectoryW(string p,ref SA sa);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string s,uint rev,out IntPtr p,out uint n);
 [DllImport("kernel32.dll")]static extern IntPtr LocalFree(IntPtr p);
 public static void Create(string path){IntPtr sd;uint n;if(!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",1,out sd,out n))throw new System.ComponentModel.Win32Exception();try{var sa=new SA{length=Marshal.SizeOf(typeof(SA)),descriptor=sd,inherit=false};if(!CreateDirectoryW(path,ref sa))throw new System.ComponentModel.Win32Exception();}finally{LocalFree(sd);}}
}
"@
}
function Assert-SeriesPrivateRoot {
 param([string]$Path)
 Assert-NoReparseAncestors $Path;$item=Get-Item -LiteralPath $Path -Force
 if(-not $item.PSIsContainer){throw 'Series root must be an ordinary directory.'}
 $acl=Get-Acl -LiteralPath $Path;$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if(-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544') -or $rules.Count -ne 2){throw 'Series root ownership or private ACL differs.'}
 $sids=@();foreach($r in $rules){$sids+=$r.IdentityReference.Value;if($r.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544') -or $r.AccessControlType -ne 'Allow' -or [long]$r.FileSystemRights -ne 2032127 -or $r.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' -or $r.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None){throw 'Series root permission differs.'}}
 if(@($sids|Select-Object -Unique).Count -ne 2){throw 'Series root private grants incomplete.'}
}
function Write-SeriesRecord {
 param([string]$Path,$Value)
 Assert-SeriesPrivateRoot (Split-Path -Parent $Path)
 $bytes=[Text.UTF8Encoding]::new($false,$true).GetBytes(($Value|ConvertTo-Json -Depth 8));if($bytes.Length -gt 65536){throw 'Series metadata exceeded its bound.'}
 $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-PrivateAcl))
 try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
function Get-SeriesFreshHolds {
 param($Folder)
 if(Test-Path -LiteralPath $seriesRoot -ErrorAction Stop){'An earlier six-worker provider series root exists. Preserve it; no automatic repeat or resume.'}
 foreach($n in 1..6){foreach($stage in @('before','after')){
  $slot="slot$n";$path="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$slot-$Provider-$stage"
  if(Test-Path -LiteralPath $path -ErrorAction Stop){"Existing status evidence for $slot/$stage must be preserved."}
  if($null -ne (Get-ExactTaskOrAbsent $Folder "CoChem-4.2.7-NativeAuthStatusSix-20261008-r3-v3-$Attempt-$slot-$Provider-$stage")){"Existing status task for $slot/$stage must be preserved."}
 }}
}
function Assert-FreshClaudeLogin {
 param([string]$Slot)
 $null=@(Assert-ReviewedClaudeHistory -Slot $Slot -Folder $folder -Runtime $runtime)
}
function Get-SeriesWorkerRootHolds {
 param($Layout)
 foreach($n in 1..6){
  $root=[string]$Layout.slots.PSObject.Properties["slot$n"].Value.root
  try{Assert-NoReparseAncestors $root;$item=Get-Item -LiteralPath $root -Force -ErrorAction Stop;if(-not $item.PSIsContainer){throw 'A provisioned slot root is not a directory.'}}
  catch{
   $errorValue=$_.Exception;$denied=$false
   while($null -ne $errorValue){if($errorValue -is [UnauthorizedAccessException]){$denied=$true};$errorValue=$errorValue.InnerException}
   if($denied){"Worker slot$n root metadata is inaccessible in this token; Administrator preflight must establish its ordinary directory identity."}else{throw}
  }
 }
}
function Assert-SeriesDaemonsStopped {
 param($Folder)
 foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
  $task=Get-ExactTaskOrAbsent $Folder $name
  if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){throw 'Protected daemons must remain stopped and disabled.'}
 }
}
function Invoke-SeriesConsoleChild {
 param([string[]]$Arguments)
 # No redirection or output capture: browser/code interaction stays in console.
 $quoted=foreach($a in $Arguments){if($a.Contains('"') -or $a.Contains("`r") -or $a.Contains("`n")){throw 'Invalid fixed child argument.'};'"'+$a+'"'}
 $process=Start-Process -FilePath $powershell -ArgumentList ($quoted -join ' ') -WorkingDirectory $seriesRoot -NoNewWindow -Wait -PassThru
 try{$process.WaitForExit();$process.Refresh();[int]$process.ExitCode}finally{$process.Dispose()}
}

function Invoke-AuthStatusChild {
 param([string]$Slot,[string]$Stage)
 $savedPreference=$ErrorActionPreference
 try{
  $ErrorActionPreference='Continue'
  $output=@(& $powershell -NoLogo -NoProfile -NonInteractive -File $statusLeaf -Slot $Slot -Provider $Provider -Stage $Stage -Attempt $Attempt -Apply 2>&1)
  $code=$LASTEXITCODE
 }finally{$ErrorActionPreference=$savedPreference}
 $text=($output|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($text.Length -gt 65536){throw 'Native status metadata exceeded its bound. Preserve its receipt/task; no login was started.'}
 if($code -ne 0){
  # Accept only the reviewed leaf's tagged summary, then verify the protected
  # receipt/task again before projecting it. Never echo stderr/provider output.
  $tag='COCHEM_NATIVE_AUTH_STATUS_RESULT '
  $lines=@($text -split '\r?\n'|Where-Object{$_.StartsWith($tag,[StringComparison]::Ordinal)})
  if($lines.Count -ne 1){throw 'Native status could not establish a bound failure receipt. Preserve its task/root; no login or automatic retry was started.'}
  $text=$lines[0].Substring($tag.Length)
 }
 try{$result=$text|ConvertFrom-Json}catch{throw 'Native status returned malformed metadata; no login is authorized.'}
 if($result.attempt -cne $Attempt -or $result.schema -cne 'cochem-worker-native-auth-status-task-result/1' -or $result.slot -cne $Slot -or $result.provider -cne $Provider -or $result.stage -cne $Stage -or $result.nonce -cnotmatch '^[a-f0-9]{32}$' -or $result.receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'Native status summary does not bind the requested stage.'}
 $root="C:\Program Files\CoChem\NativeAuthStatusSix4.2.7-windows-20261008-r3-v3-$Attempt-$Slot-$Provider-$Stage"
 if($result.receipt_path -cne (Join-Path $root 'worker-native-status.json')){throw 'Native status path differs.'}
 $task=Get-ExactTaskOrAbsent $folder "CoChem-4.2.7-NativeAuthStatusSix-20261008-r3-v3-$Attempt-$Slot-$Provider-$Stage"
 if($null -eq $task -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Native status task is not terminal.'}
 $d=$task.Definition
 $argv='-I -B "'+(Join-Path $root 'worker-native-auth-status-six-r3-v3.py')+'" --slot '+$Slot+' --provider '+$Provider+' --nonce '+$result.nonce+' --stage '+$Stage+' --attempt '+$Attempt
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or $d.Actions.Item(1).Path -cne (Join-Path $installRoot '.venv\Scripts\python.exe') -or $d.Actions.Item(1).Arguments -cne $argv -or $d.Actions.Item(1).WorkingDirectory -cne $root -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.ExecutionTimeLimit -cne 'PT3M'){throw 'Native status task action/identity differs.'}
 $null=Read-R3Control (Join-Path $root 'worker-native-auth-status-six-r3-v3.py') $statusPythonHash
 $control=Read-R3Control $result.receipt_path $result.receipt_sha256 32768
 $value=(Read-R3Text $control)|ConvertFrom-Json
 $decision=Assert-AuthStatusReceipt $value $task $runtime $Slot $Provider $Stage $result.nonce $statusPythonHash $Attempt
 if($code -ne 0 -or $decision -ceq 'HOLD'){
  if($value.status -cnotin @('STATUS_CHECK_FAILED','AUTHENTICATION_NOT_VERIFIED','NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED') -or $value.cleanup_verified -isnot [bool]){throw 'Failure receipt has invalid status metadata.'}
  $safe=[ordered]@{schema='cochem-worker-native-auth-status-task-result/1';attempt=$Attempt;slot=$Slot;provider=$Provider;stage=$Stage;nonce=$result.nonce;decision='HOLD';receipt_path=$result.receipt_path;receipt_sha256=$control.Sha256;status=$value.status;cleanup_verified=$value.cleanup_verified;last_task_result=$task.LastTaskResult;task_preserved=$true;pipeline_started=$false}
  Add-AuthStatusFailureMetadata $safe $value
  Write-Host ('COCHEM_NATIVE_AUTH_STATUS_RESULT '+($safe|ConvertTo-Json -Depth 5 -Compress))
  throw 'Native status did not establish reuse or explicit logout. Preserve the reported receipt; no login or automatic retry was started.'
 }
 if($decision -cnotin @('REUSE_VERIFIED_SESSION','ATTENDED_LOGIN_REQUIRED') -or $decision -cne $result.decision){throw 'Native status is not an established reuse or login decision.'}
 [pscustomobject]@{decision=$decision;stage=$Stage;receipt_path=$result.receipt_path;receipt_sha256=$control.Sha256}
}
function Show-CodexDeviceLoginInstructions {
 Write-Host 'Codex device authentication does not open a browser window automatically.'
 Write-Host 'When the URL and code appear below, open that URL manually in your browser and enter the fresh code on that page.'
 Write-Host 'Finish browser approval, then leave this console open and wait until it continues automatically. Do not enter the device code in PowerShell.'
}

function Confirm-AttendedLoginReady {
 param([string]$Slot,[string]$Provider)
 if($Provider -ceq 'codex'){Show-CodexDeviceLoginInstructions}
 $answer=Read-Host "For $Provider $Slot, type READY when you can complete browser approval now, or PAUSE to stop safely"
 if($answer -ceq 'READY'){return $true}
 if($answer -cne 'PAUSE'){Write-Host 'Readiness was not confirmed. Pausing without starting a login code.'}
 return $false
}

function Invoke-SixStatusFirst {
 param([scriptblock]$Status,[scriptblock]$Login,[scriptblock]$Record,[scriptblock]$Ready)
 foreach($n in 1..6){
  $slot="slot$n";& $Record $slot 'STATUS_BEFORE_STARTED' $null
  $before=& $Status $slot 'before'
  if($before.decision -cnotin @('REUSE_VERIFIED_SESSION','ATTENDED_LOGIN_REQUIRED')){throw 'Status does not authorize reuse or an attended login.'}
  & $Record $slot 'STATUS_BEFORE_COMPLETE' $before
  if($before.decision -ceq 'REUSE_VERIFIED_SESSION'){
   & $Record $slot 'REUSED_VERIFIED_SESSION' $before
   continue
  }
  if(-not (& $Ready $slot)){& $Record $slot 'PAUSED_BEFORE_LOGIN' $before;return $false}
  & $Record $slot 'OPERATOR_READY' $null
  & $Record $slot 'LOGIN_STARTED' $null
  $code=& $Login $slot
  if($code -isnot [int] -or $code -ne 0){throw 'Attended login did not exit successfully. Preserve evidence; no automatic repeat or later slot was started.'}
  & $Record $slot 'LOGIN_EXITED_ZERO' $null
  $after=& $Status $slot 'after'
  if($after.decision -cne 'REUSE_VERIFIED_SESSION'){throw 'Post-login authentication is not verified. Preserve evidence; no automatic repeat.'}
  & $Record $slot 'AUTHENTICATED_AFTER_LOGIN' $after
 }
 return $true
}
$ownsRoot=$false;$phase='preflight';$nonce=$null
try{
 foreach($d in @(Import-SeriesFunctions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))};Initialize-FileIdentity
 foreach($d in @(Import-SeriesFunctions $baseStatusLeaf '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-VenvBinding'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions $statusLeaf $statusLeafHash @('Assert-AuthStatusReceipt','Add-AuthStatusFailureMetadata'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions $claudeHelper '8e3545109c2bbef9a53763f8af8575ac1ff7376d3bea6c27a5ecd8454af182ec' @('New-PrivateAcl','Get-ExactTaskOrAbsent'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions (Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1') '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d' @('Assert-CodeTreeOnce'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions (Join-Path $PSScriptRoot 'claude-session-history-v5.ps1') '82f66a4c135454ad406ad47cc8b6abce25fdad55951dbd6dc70f30a0c68634d4' @('Test-ClaudeHistoryInteger','Get-ClaudeHistoryValue','Get-ClaudeHistorySafeReceiptMetadata','Assert-ClaudeHistoryPrivateAcl','Assert-ClaudeHistoryPrivateRoot','Assert-ClaudeHistoryTask','Assert-ClaudeHistoryReceipt','Assert-ReviewedClaudeHistory'))){. ([scriptblock]::Create($d))}
 $runtime=Assert-R3InstalledBindings
 Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
 $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
 foreach($row in @(
  [pscustomobject]@{path=$statusPython;sha256=$statusPythonHash;protected=$false},
  [pscustomobject]@{path=$codexHelper;sha256='e3d4d93f58446e05e4d585ac9215410527f8d1ec2a884153dbf69feb4ee7b32a';protected=$true},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'worker_claude_login_bridge_r3.py');sha256='b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73';protected=$false},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'worker-native-status-r3.py');sha256='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5';protected=$false},
  [pscustomobject]@{path=(Join-Path $nativeRoot "$Provider.exe");sha256=if($Provider -eq 'codex'){'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d'}else{'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'};protected=$true})){if($row.protected){Assert-ProtectedPath $row.path};$held.Add((Open-VerifiedFile $row.path $row.sha256 (Get-Item -LiteralPath $row.path).Length))}
 $current=(Read-R3Text (Read-R3Control (Join-Path $installRoot 'pipeline.json') '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'))|ConvertFrom-Json
 $layout=(Read-R3Text (Read-R3Control (Join-Path $installRoot 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'))|ConvertFrom-Json
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 Assert-SeriesDaemonsStopped $folder;$holds=@(Get-SeriesWorkerRootHolds $layout)+@(Get-SeriesFreshHolds $folder)
 if(-not $Apply){[ordered]@{schema='cochem-six-worker-status-first-plan/1';attempt=$Attempt;mode='READ_ONLY_PLAN';provider=$Provider;selected_workers=@('slot1','slot2','slot3','slot4','slot5','slot6');shared_capacity=4;runtime=$runtime;configuration_applied=$false;series_root=$seriesRoot;human_console_required=$true;browser_login_only_for_explicit_logged_out_status=$true;maximum_logins=6;actual_logins_required=$null;no_automatic_repeat_or_resume=$true;native_commands_executed=0;model_jobs_executed=0;activation_ready=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
 if($holds.Count){throw ($holds -join ' ')}
 $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot;$null=Assert-CodeTreeOnce $nativeRoot
 Assert-SeriesDaemonsStopped $folder;$fresh=@(Get-SeriesWorkerRootHolds $layout)+@(Get-SeriesFreshHolds $folder);if($fresh.Count){throw ($fresh -join ' ')}
 Assert-ProtectedPath (Split-Path -Parent $seriesRoot);Initialize-SeriesDirectory
 [CoChemNativeLoginSeriesDirectory]::Create($seriesRoot);$ownsRoot=$true;Assert-SeriesPrivateRoot $seriesRoot
 $nonce=[Guid]::NewGuid().ToString('N')
 Write-SeriesRecord (Join-Path $seriesRoot 'series-start.json') ([ordered]@{schema='cochem-six-worker-status-first/1';attempt=$Attempt;status='IN_PROGRESS';provider=$Provider;nonce=$nonce;runtime=$runtime;configuration_applied=$false;started_utc=[DateTime]::UtcNow.ToString('o');activation_ready=$false})
 $script:authResults=[Collections.Generic.List[object]]::new();$script:loginCount=0;$script:reusedCount=0;$phase='status_first_authentication'
 $completed=Invoke-SixStatusFirst {
  param($slot,$stage)
  Assert-SeriesDaemonsStopped $folder;Invoke-AuthStatusChild $slot $stage
 } {
  param($slot)
  Assert-SeriesDaemonsStopped $folder
  if($Provider -ceq 'claude'){Assert-FreshClaudeLogin $slot}
  Write-Host "The selected $Provider worker $slot is explicitly logged out. Follow the provider browser instructions for this profile."
  $script:loginCount++
  $arguments=@('-NoProfile','-File')
  if($Provider -ceq 'codex'){$arguments+=@($codexHelper,'-Slot',$slot,'-Provider','codex','-Executable',(Join-Path $nativeRoot 'codex.exe'),'-InstallRoot',$installRoot)}
  else{$arguments+=@($claudeHelper,'-Slot',$slot,'-Apply','-Interactive')}
  Invoke-SeriesConsoleChild $arguments
 } {
  param($slot,$state,$proof)
  Write-SeriesRecord (Join-Path $seriesRoot "$slot-$state.json") ([ordered]@{schema='cochem-six-worker-auth-event/1';attempt=$Attempt;provider=$Provider;slot=$slot;nonce=$nonce;state=$state;proof=$proof;observed_utc=[DateTime]::UtcNow.ToString('o');raw_provider_output_recorded_by_series=$false})
  if($state -cin @('REUSED_VERIFIED_SESSION','AUTHENTICATED_AFTER_LOGIN')){$script:authResults.Add([pscustomobject]@{slot=$slot;status=$state;proof=$proof})}
  if($state -ceq 'REUSED_VERIFIED_SESSION'){$script:reusedCount++}
  if($state -ceq 'PAUSED_BEFORE_LOGIN'){$script:pausedSlot=$slot;$script:pausedProof=$proof}
 } {
  param($slot)
  # Review history before consuming owner readiness or starting a login.
  if($Provider -ceq 'claude'){Assert-FreshClaudeLogin $slot}
  Confirm-AttendedLoginReady $slot $Provider
 }
 Assert-SeriesDaemonsStopped $folder
 if(-not $completed){
  $paused=[ordered]@{schema='cochem-six-worker-status-first-paused/1';attempt=$Attempt;status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';provider=$Provider;nonce=$nonce;runtime=$runtime;paused_slot=$script:pausedSlot;before_status_proof=$script:pausedProof;configuration_applied=$false;login_commands_executed=$script:loginCount;existing_sessions_reused=$script:reusedCount;status_receipts=@($script:authResults.ToArray());model_jobs_executed=0;pipeline_started=$false;activation_ready=$false;series_preserved=$true;automatic_retry_allowed=$false;exit_code=20}
  Write-SeriesRecord (Join-Path $seriesRoot 'series-paused.json') $paused
  $paused|ConvertTo-Json -Depth 10
  exit 20
 }
 if($script:authResults.Count -ne 6){throw 'Six configured workers were not authenticated.'}
 $phase='completion'
 $result=[ordered]@{schema='cochem-six-worker-status-first-result/1';attempt=$Attempt;status='SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED';provider=$Provider;nonce=$nonce;runtime=$runtime;configuration_applied=$false;selected_workers_verified=6;other_accounts_preserved=$true;login_commands_executed=$script:loginCount;existing_sessions_reused=$script:reusedCount;status_receipts=@($script:authResults.ToArray());model_jobs_executed=0;serving_model_verified=$false;provider_account_identity_verified=$false;activation_ready=$false;completed_utc=[DateTime]::UtcNow.ToString('o');series_preserved=$true}
 Write-SeriesRecord (Join-Path $seriesRoot 'series-complete.json') $result
 $result|ConvertTo-Json -Depth 10
}catch{
 if($ownsRoot){try{Write-SeriesRecord (Join-Path $seriesRoot 'series-failed.json') ([ordered]@{schema='cochem-six-worker-status-first-failure/1';attempt=$Attempt;provider=$Provider;nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;failed_utc=[DateTime]::UtcNow.ToString('o');automatic_resume_allowed=$false;configuration_applied=$false;activation_ready=$false})}catch{}}
 throw
}finally{foreach($stream in $held){$stream.Dispose()}}
