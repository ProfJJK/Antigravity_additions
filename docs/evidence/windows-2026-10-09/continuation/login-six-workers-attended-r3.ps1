#Requires -Version 5.1
<# Default read-only. Apply is an explicit attended series for six existing
   workers and one provider. No output/code capture, model jobs, credential
   copying, provisioning or daemon activation. A fresh fixed private marker
   blocks every repeat, including partial or successful series; never resume. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,[switch]$Apply,[switch]$Interactive)
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
$seriesRoot="C:\Program Files\CoChem\NativeLoginSeries4.2.7-windows-20261007-r3-$Provider"
$codexHelper='C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006\repository\scripts\login_pipeline_worker.ps1'
$claudeHelper=Join-Path $PSScriptRoot 'login_pipeline_worker_interactive_r3.ps1'
$statusBatch=Join-Path $PSScriptRoot 'check-all-worker-native-status-r3.ps1'
$statusLeaf=Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1'
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
 if(Test-Path -LiteralPath $seriesRoot -ErrorAction Stop){'An earlier provider series root exists. Preserve it; no automatic repeat or resume.'}
 Get-AllFreshTargetHolds $Folder
 if($Provider -eq 'claude'){
  foreach($n in 1..6){foreach($pattern in @("InteractiveClaude427-slot$n-*","InteractiveClaude427-r3-slot$n-*")){
   $previous=@(Get-ChildItem -LiteralPath 'C:\Program Files\CoChem' -Directory -Filter $pattern -ErrorAction Stop)
   if($previous.Count){"A prior Claude session for slot$n exists; preserve and review it."}
  }}
 }
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
function Invoke-SixAttendedLogins {
 param([scriptblock]$Login,[scriptblock]$Record)
 foreach($n in 1..6){
  $slot="slot$n";& $Record $slot 'STARTING' $null;$code=& $Login $slot
  if($code -isnot [int]){throw 'Login helper did not yield a process exit code.'}
  & $Record $slot 'EXITED' $code
  if($code -ne 0){throw "Attended login for $slot did not exit successfully. Preserve this series and its leaf evidence; no retry or later login was started."}
 }
}
function Read-SeriesStatusReceipts {
 $results=@()
 foreach($n in 1..6){
  $slot="slot$n";$path="C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261007-r3-$slot-$Provider\worker-native-status.json"
  $control=Read-R3Control $path '' 32768;$value=(Read-R3Text $control)|ConvertFrom-Json
  if($value.schema -cne 'cochem-worker-native-status/1' -or $value.slot -cne $slot -or $value.provider -cne $Provider -or
   $value.status -cne 'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or $value.cleanup_verified -ne $true -or $value.system_sid -cne 'S-1-5-18' -or
   $value.helper_sha256 -cne 'c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5' -or $value.runtime_root -cne $installRoot -or
   $value.install_receipt_sha256 -cne $runtime.install_receipt_sha256 -or $value.source_manifest_sha256 -cne $runtime.source_manifest_sha256 -or
   $value.resource_limits_sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f' -or
   $value.config_sha256 -cne $runtime.configuration_sha256 -or $value.revision.verified -ne $true -or $value.revision.source_sha256 -cne $runtime.revision.source_sha256){throw 'A final status receipt does not attest the selected r3 worker/provider.'}
  $results+=@([ordered]@{slot=$slot;path=$path;sha256=$control.Sha256;status=$value.status;cleanup_verified=$true})
 }
 $results
}
$ownsRoot=$false;$phase='preflight';$nonce=$null
try{
 foreach($d in @(Import-SeriesFunctions $copyHelper '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($d))};Initialize-FileIdentity
 foreach($d in @(Import-SeriesFunctions $statusLeaf '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-VenvBinding','Assert-CodeTreeOnce'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions $claudeHelper '1ec70cd0beeacde2d11df1948ac009be92fbdd5e5cdb8cf78b3f7f6346920109' @('New-PrivateAcl','Get-ExactTaskOrAbsent'))){. ([scriptblock]::Create($d))}
 foreach($d in @(Import-SeriesFunctions $statusBatch 'd96ab0928d7b1f61035eb6423a8877878d76899f52604f5448d860c02da2a713' @('Get-AllFreshTargetHolds'))){. ([scriptblock]::Create($d))}
 $runtime=Assert-R3InstalledBindings
 Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
 $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
 $pins=@(
  [pscustomobject]@{path=$codexHelper;sha256='e3d4d93f58446e05e4d585ac9215410527f8d1ec2a884153dbf69feb4ee7b32a';protected=$true},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'worker-native-status-r3.py');sha256='c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5';protected=$false},
  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'worker_claude_login_bridge_r3.py');sha256='b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73';protected=$false},
  [pscustomobject]@{path=(Join-Path $nativeRoot "$Provider.exe");sha256=if($Provider -eq 'codex'){'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d'}else{'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'};protected=$true})
 foreach($p in $pins){if($p.protected){Assert-ProtectedPath $p.path};$held.Add((Open-VerifiedFile $p.path $p.sha256 (Get-Item -LiteralPath $p.path).Length))}
 $layout=(Read-R3Text (Read-R3Control (Join-Path $installRoot 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'))|ConvertFrom-Json
 $workerHolds=@(Get-SeriesWorkerRootHolds $layout)
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 Assert-SeriesDaemonsStopped $folder;$holds=@($workerHolds)+@(Get-SeriesFreshHolds $folder)
 if(-not $Apply){[ordered]@{schema='cochem-attended-native-series-plan/1';mode='READ_ONLY_PLAN';provider=$Provider;slots=@('slot1','slot2','slot3','slot4','slot5','slot6');runtime=$runtime;series_root=$seriesRoot;human_console_required=$true;all_six_targets_checked=$true;no_automatic_repeat_or_resume=$true;native_commands_executed=0;model_jobs_executed=0;activation_ready=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
 if($holds.Count){throw ($holds -join ' ')}
 $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce $basePythonRoot;$null=Assert-CodeTreeOnce $nativeRoot
 Assert-SeriesDaemonsStopped $folder;$fresh=@(Get-SeriesWorkerRootHolds $layout)+@(Get-SeriesFreshHolds $folder);if($fresh.Count){throw ($fresh -join ' ')}
 Assert-ProtectedPath (Split-Path -Parent $seriesRoot);Initialize-SeriesDirectory
 [CoChemNativeLoginSeriesDirectory]::Create($seriesRoot);$ownsRoot=$true;Assert-SeriesPrivateRoot $seriesRoot
 $nonce=[Guid]::NewGuid().ToString('N')
 Write-SeriesRecord (Join-Path $seriesRoot 'series-start.json') ([ordered]@{schema='cochem-attended-native-series/1';status='IN_PROGRESS';provider=$Provider;nonce=$nonce;runtime=$runtime;started_utc=[DateTime]::UtcNow.ToString('o');activation_ready=$false;authentication_verified=$false})
 $phase='attended_logins'
 Invoke-SixAttendedLogins {
  param($slot)
  Assert-SeriesDaemonsStopped $folder
  Write-Host "Attended $Provider login for $slot of six. Follow the existing helper's browser instructions."
  $arguments=@('-NoProfile','-File')
  if($Provider -eq 'codex'){$arguments+=@($codexHelper,'-Slot',$slot,'-Provider','codex','-Executable',(Join-Path $nativeRoot 'codex.exe'),'-InstallRoot',$installRoot)}
  else{$arguments+=@($claudeHelper,'-Slot',$slot,'-Apply','-Interactive')}
  Invoke-SeriesConsoleChild $arguments
 } {
  param($slot,$state,$exitCode)
  Write-SeriesRecord (Join-Path $seriesRoot "$slot-$state.json") ([ordered]@{schema='cochem-attended-native-slot/1';provider=$Provider;slot=$slot;nonce=$nonce;state=$state;exit_code=$exitCode;observed_utc=[DateTime]::UtcNow.ToString('o');authentication_verified=$false;raw_provider_output_recorded_by_series=$false})
 }
 $phase='authentication_status';Assert-SeriesDaemonsStopped $folder
 $code=Invoke-SeriesConsoleChild @('-NoProfile','-NonInteractive','-File',$statusBatch,'-Provider',$Provider,'-Apply')
 if($code -ne 0){throw 'Reviewed authentication status batch did not pass. Preserve series and per-slot status receipts; no automatic repeat.'}
 $receipts=@(Read-SeriesStatusReceipts);Assert-SeriesDaemonsStopped $folder;$phase='completion'
 $result=[ordered]@{schema='cochem-attended-native-series-result/1';status='ALL_SIX_NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED';provider=$Provider;nonce=$nonce;runtime=$runtime;slots_verified=$receipts.Count;status_receipts=$receipts;model_jobs_executed=0;serving_model_verified=$false;provider_account_identity_verified=$false;activation_ready=$false;completed_utc=[DateTime]::UtcNow.ToString('o');series_preserved=$true}
 Write-SeriesRecord (Join-Path $seriesRoot 'series-complete.json') $result
 $result|ConvertTo-Json -Depth 8
}catch{
 if($ownsRoot){try{Write-SeriesRecord (Join-Path $seriesRoot 'series-failed.json') ([ordered]@{schema='cochem-attended-native-series-failure/1';provider=$Provider;nonce=$nonce;phase=$phase;error_type=$_.Exception.GetType().Name;failed_utc=[DateTime]::UtcNow.ToString('o');automatic_resume_allowed=$false;activation_ready=$false})}catch{}}
 throw
}finally{foreach($stream in $held){$stream.Dispose()}}
