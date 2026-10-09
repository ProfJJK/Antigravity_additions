#Requires -Version 5.1
<# One invocation of pending task recovery and monitoring; saved logins are reused.
   Each child owns its existing journals/fresh-state guards. No retry or cleanup.
   Children share the owner's console so browser consent is never redirected. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Open-RebootSetupSource {
 param([string]$Path,[string]$Hash)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  if($stream.Length -le 0 -or $stream.Length -gt 131072){throw 'SETUP_SOURCE_SIZE'}
  $sha=[Security.Cryptography.SHA256]::Create()
  try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'SETUP_SOURCE_CHANGED'}
  $stream.Position=0;return $stream
 }catch{$stream.Dispose();throw}
}

function Invoke-RebootSetupPhases {
 param([scriptblock]$Invoke)
 foreach($phase in @('pending_registration_recovery','monitoring_setup')){
  $script:currentPhase=$phase
  $code=& $Invoke $phase
  if($code -isnot [int] -or $code -ne 0){throw 'SETUP_PHASE_FAILED_PRESERVE_STATE'}
 }
 return 0
}

function Invoke-RebootConsoleChild {
 param([string[]]$Arguments)
 # Synchronous attended child: inherit the existing console, never redirect.
 $quoted=foreach($a in $Arguments){if($a.Contains('"') -or $a.Contains("`r") -or $a.Contains("`n")){throw 'Invalid fixed child argument.'};'"'+$a+'"'}
 $process=Start-Process -FilePath $powershell -ArgumentList ($quoted -join ' ') -WorkingDirectory $PSScriptRoot -NoNewWindow -Wait -PassThru
 try{$process.WaitForExit();$process.Refresh();[int]$process.ExitCode}finally{$process.Dispose()}
}

if($PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -cne 'ConsoleHost')){throw 'Apply requires the elevated owner in an interactive ConsoleHost, with -Interactive and no redirected input/output.'}
$powershell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$specs=@(
 [pscustomobject]@{phase='pending_registration_recovery';file='resume-pending-warden-r3-v7.ps1';sha256='a34a1bf07dfa0546752e650eb20e0babdea32bbd5a19c0620112fc878defc873'},
 [pscustomobject]@{phase='monitoring_setup';file='run-post-commissioning-setup-r3-v4.ps1';sha256='933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4'}
)
$held=[Collections.Generic.List[IO.FileStream]]::new();$script:currentPhase='source_preflight'
try{
 foreach($s in $specs){$held.Add((Open-RebootSetupSource (Join-Path $PSScriptRoot $s.file) $s.sha256))}
 if(-not $Apply){
  [ordered]@{schema='cochem-reboot-setup-launcher-plan/1';mode='READ_ONLY_PLAN';phases=$specs;source_custody_verified=$true;child_processes_started=0;tasks_changed=0;model_jobs_submitted=0;full_srs_acceptance=$false;automatic_retry=$false}|ConvertTo-Json -Depth 5
  return
 }
 $outcome=Invoke-RebootSetupPhases {
  param($phase)
  $s=@($specs|Where-Object{$_.phase -ceq $phase})
  if($s.Count -ne 1){throw 'SETUP_PHASE_SPEC'}
  Write-Host "Running reviewed continuation: $phase"
  Invoke-RebootConsoleChild @('-NoLogo','-NoProfile','-File',(Join-Path $PSScriptRoot $s[0].file),'-Apply','-Interactive')
 }
 [ordered]@{schema='cochem-reboot-setup-launcher-result/1';status='PENDING_REGISTRATION_AND_MONITORING_SETUP_COMPLETED_ACCEPTANCE_PENDING';login_commands_executed=0;model_jobs_submitted=0;paid_repair_enabled=$false;component_recovery_enabled=$false;continuous_48h_complete=$false;full_srs_acceptance=$false;automatic_retry=$false}|ConvertTo-Json -Compress
}catch{
 [ordered]@{schema='cochem-reboot-setup-launcher-result/1';status='HELD_PRESERVE_CHILD_EVIDENCE_AND_RUNNING_STATE';phase=$script:currentPhase;automatic_retry=$false;full_srs_acceptance=$false}|ConvertTo-Json -Compress|Write-Host
 throw 'Setup stopped. Preserve the child reports and all partial/running state; do not rerun after controller start or monitoring setup failure.'
}finally{foreach($stream in $held){$stream.Dispose()}}
