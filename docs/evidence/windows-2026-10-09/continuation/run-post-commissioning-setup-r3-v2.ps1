#Requires -Version 5.1
<# Post-commissioning monitoring setup around immutable reviewed helpers.
   Default is a read-only plan. The original basic first-start series is independent.
   Reviewed code does not establish actual host acceptance or full SRS completion. No stop, retry, cleanup or automatic resume.
   Oracle remains deferred maintenance acceptance in both branches: its preserved lock/scratch artifacts conflict with frozen first-start fresh-state predicates. No Oracle task is invoked here. #>
[CmdletBinding()]
param([switch]$Apply,[switch]$Interactive)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-SetupFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $s=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($s)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'SETUP_SUPPORT_HASH'}
  $s.Position=0;$r=[IO.StreamReader]::new($s,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$r.ReadToEnd()}finally{$r.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
  if($errors.Count){throw 'SETUP_SUPPORT_PARSE'}
  foreach($name in $Names){$f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($f.Count -ne 1){throw 'SETUP_SUPPORT_DEFINITION'};$f[0].Extent.Text}
  $script:held.Add($s);$s=$null
 }finally{if($null -ne $s){$s.Dispose()}}
}

function Get-SetupPathState {
 param([string]$Path)
 try{$item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return 'absent'}catch{throw 'SETUP_PATH_INACCESSIBLE'}
 if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'SETUP_PATH_REPARSE'}
 return 'present'
}

function Get-SetupPhaseSpecifications {
 @(
  [pscustomobject]@{name='oracle_maintenance';file='check-oracle-native-acceptance-r3-v1.ps1';hash='196a696c4f6c5626a34b83d77ae28444edfa88aeac8274b0e69de8096987760c';args=@();plan_schema='cochem-oracle-native-plan/1';receipt='C:\Program Files\CoChem\OracleNativeAcceptance4.2.7-windows-20261008-r3-v1\oracle-native-acceptance.json';receipt_schema='cochem-oracle-native-acceptance/1';status='ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED'}
  [pscustomobject]@{name='independent_staging';file='install-independent-supervisor-staging-r3-v2.ps1';hash='00412dfa8d7d6668713f9fb850c6fc4a2dd4df4dcc1527c65106ce7497be893d';args=@('-Manifest',(Join-Path $PSScriptRoot 'independent-supervisor-staging-r3-v1.manifest.json'),'-ManifestSha256','6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4');plan_schema='cochem-independent-supervisor-staging-plan/2';receipt='C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1\controls\staging-receipt.json';receipt_schema='cochem-independent-supervisor-staging/1';status='INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'}
  [pscustomobject]@{name='first_start';file='run-pipeline-commissioning-r3-v5.ps1';hash='46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0';args=@();plan_schema='cochem-pipeline-commissioning-series-plan/1';receipt='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json';receipt_schema='cochem-warden-commissioning/1';status='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED'}
  [pscustomobject]@{name='held_observation';file='install-held-supervisor-observation-r3-v2.ps1';hash='9fb90f828ff3b0a7776af9bc48acd99eb2a520508a80271a1e87b785a2ce7313';args=@();plan_schema='cochem-held-supervisor-observation-plan/1';receipt='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1\activation.json';receipt_schema='cochem-held-supervisor-activation-result/1';status='HELD_SUPERVISOR_OBSERVATION_RUNNING'}
  [pscustomobject]@{name='resource_observation';file='install-resource-observer-r3-v4.ps1';hash='106d95dd5b22613cb871f3649d4376dc0a75745b6fce38469ddf94d2fcb9ec65';args=@('-Operation','RegisterAndStart');plan_schema='cochem-resource-observer-install-plan/1';receipt='C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v4\observer-started.json';receipt_schema='cochem-resource-observer-started/1';status='RESOURCE_OBSERVER_RUNNING_INITIAL_MEASUREMENTS_VERIFIED'}
 )
}

function Assert-SetupPlan {
 param($Spec,$Value)
 if($Value.schema -cne $Spec.plan_schema){throw 'SETUP_PLAN_SCHEMA'}
 if($Spec.name -cne 'resource_observation' -and $Value.mode -cne 'READ_ONLY_PLAN'){throw 'SETUP_PLAN_MODE'}
 if($null -eq $Value.PSObject.Properties['holds']){throw 'SETUP_PLAN_HOLDS'}
 foreach($h in @($Value.holds)){if($h -isnot [string] -or [string]::IsNullOrWhiteSpace($h) -or $h.Length -gt 2048){throw 'SETUP_PLAN_HOLD_TYPE'}}
 switch -CaseSensitive ($Spec.name){
  'oracle_maintenance' {if($Value.activation_prerequisite -isnot [bool] -or $Value.activation_prerequisite -or $Value.model_jobs_executed -ne 0 -or $Value.automatic_retry_allowed -ne $false){throw 'SETUP_ORACLE_SCOPE'}}
  'independent_staging' {if($Value.manifest_sha256 -cne '6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4' -or $Value.activation_ready -ne $false -or $Value.warden_stop_required -ne $false -or $Value.old_ledgers_opened -ne $false){throw 'SETUP_STAGING_SCOPE'}}
  'first_start' {if($Value.identity_count -ne 6 -or $Value.shared_capacity -ne 4 -or $Value.full_srs_acceptance -ne $false -or $Value.automatic_repair_enabled -ne $false -or $Value.model_jobs_submitted -ne 0){throw 'SETUP_FIRST_START_SCOPE'}}
  'held_observation' {
   foreach($k in @('paid_repair_enabled','component_recovery_enabled','warden_stop_required','oracle_acceptance_prerequisite')){if($Value.$k -isnot [bool] -or $Value.$k){throw 'SETUP_HELD_SCOPE'}}
   foreach($k in @('staging_present','commissioning_present')){if($Value.$k -isnot [bool]){throw 'SETUP_HELD_DEPENDENCY_TYPE'}}
   if($Value.target_root -cne 'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1' -or $Value.data_root -cne 'C:\ProgramData\CoChemSupervisor427-observation-20261007-r3-v1'){throw 'SETUP_HELD_PATHS'}
  }
  'resource_observation' {if($Value.operation -cne 'RegisterAndStart' -or $Value.source_manifest_sha256 -cne '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49' -or $Value.duration_seconds -ne 172800 -or $Value.full_srs_acceptance -ne $false -or $Value.no_restart_or_resume -ne $true){throw 'SETUP_OBSERVER_SCOPE'}}
  default {throw 'SETUP_PHASE_UNKNOWN'}
 }
 return $Value
}

function Get-SetupPreview {
 param($Spec)
 $old=$ErrorActionPreference
 try{$ErrorActionPreference='Continue';$out=@(& $script:powershell -NoLogo -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot $Spec.file) @($Spec.args) 2>&1);$code=$LASTEXITCODE}finally{$ErrorActionPreference=$old}
 $text=($out|ForEach-Object{[string]$_}) -join [Environment]::NewLine
 if($code -ne 0 -or $text.Length -gt 262144){throw 'SETUP_PHASE_PREVIEW_FAILED'}
 try{$v=$text|ConvertFrom-Json}catch{throw 'SETUP_PHASE_PREVIEW_JSON'}
 Assert-SetupPlan $Spec $v
}

function Get-SetupEffectiveHolds {
 param([string]$Phase,$Plan,[string]$Branch,[bool]$StagingCompleted=$false)
 $commissionGaps=0;$stageGaps=0
 foreach($h in @($Plan.holds)){
  if($Phase -ceq 'resource_observation' -and $Branch -ceq 'fresh' -and $Plan.commissioning_present -is [bool] -and -not $Plan.commissioning_present -and $h -ceq 'Successful first-instance commissioning receipt is required.'){$commissionGaps++;continue}
  if($Phase -ceq 'held_observation'){
   if($Branch -ceq 'fresh' -and $Plan.commissioning_present -is [bool] -and -not $Plan.commissioning_present -and $h -ceq 'COMMISSIONING_NOT_COMPLETE'){$commissionGaps++;continue}
   if(-not $StagingCompleted -and $Plan.staging_present -is [bool] -and -not $Plan.staging_present -and $h -ceq 'STAGING_NOT_INSTALLED'){$stageGaps++;continue}
  }
  [pscustomobject]@{phase=$Phase;code='PHASE_PREFLIGHT_HOLD';detail=$h}
 }
 if($Phase -in @('held_observation','resource_observation') -and $Branch -ceq 'fresh' -and $Plan.commissioning_present -is [bool] -and -not $Plan.commissioning_present -and $commissionGaps -ne 1){[pscustomobject]@{phase=$Phase;code='DEPENDENCY_HOLD_MISSING_OR_DUPLICATE'}}
 if($Phase -ceq 'held_observation' -and -not $StagingCompleted -and $Plan.staging_present -is [bool] -and -not $Plan.staging_present -and $stageGaps -ne 1){[pscustomobject]@{phase=$Phase;code='STAGING_HOLD_MISSING_OR_DUPLICATE'}}
}

function Get-SetupDisposition {
 param([string]$Branch,[bool]$StagingCompleted)
 if($Branch -cne 'commissioned'){throw 'SETUP_COMMISSIONING_REQUIRED_NO_FIRST_START'}
 foreach($name in @('oracle_maintenance','independent_staging','first_start','held_observation','resource_observation')){
  $mode='execute'
  if($name -ceq 'oracle_maintenance'){$mode='deferred_maintenance'}
  if($name -ceq 'independent_staging' -and $StagingCompleted){$mode='reuse_verified_receipt'}
  if($name -ceq 'first_start' -and $Branch -ceq 'commissioned'){$mode='reattest_current_instance'}
  [pscustomobject]@{phase=$name;mode=$mode}
 }
}

function Read-SetupReceipt {
 param($Spec)
 $control=Read-R3Control $Spec.receipt '' 131072;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.schema -cne $Spec.receipt_schema -or $v.status -cne $Spec.status){throw 'SETUP_PHASE_RECEIPT_STATUS'}
 switch -CaseSensitive ($Spec.name){
  'oracle_maintenance' {
   if($v.helper_sha256 -cne '56e78536c2ce85e04d519b13a3347ea620bdf0ab105ed537a0cc9b4afedd9a0d' -or $v.runtime_root -cne $script:installRoot -or $v.config_sha256 -cne $script:configHash -or $v.install_receipt_sha256 -cne $script:installHash -or $v.source_manifest_sha256 -cne $script:runtimeManifestHash -or $v.revision_sha256 -cne $script:revisionHash -or $v.system_sid -cne 'S-1-5-18' -or $v.nonce -cnotmatch '^[a-f0-9]{32}$'){throw 'SETUP_ORACLE_BINDING'}
   foreach($k in @('full_service_acceptance','automatic_retry_allowed','daemon_tasks_modified','daemon_stop_requested','production_databases_modified')){if($v.$k -isnot [bool] -or $v.$k){throw 'SETUP_ORACLE_PRESERVATION'}}
   if($v.model_jobs_executed -ne 0 -or $v.component.runtime_trip_fencing_verified -ne $true -or $v.component.native_cleanup_verified -ne $true -or $v.component.job_active_processes -ne 0 -or $v.maintenance_lock.bytes_and_identity_preserved -ne $true){throw 'SETUP_ORACLE_ACCEPTANCE'}
  }
  'independent_staging' {
   if($v.manifest_sha256 -cne $script:stagingManifestHash -or $v.nonce -cnotmatch '^[a-f0-9]{32}$' -or $v.root -cne $script:stagingRoot -or $v.helper_sha256 -cne $script:stagingHelperHash -or $v.phase -cne 'complete' -or $v.revision.verified -ne $true -or $v.revision.files -ne 109 -or $v.paired_receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or $v.paired_evidence_sha256 -cne '1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177'){throw 'SETUP_STAGING_BINDING'}
   foreach($k in @('activation_ready','paid_repair_enabled','component_recovery_enabled','old_ledgers_opened','active_warden_changed','pipeline_configuration_changed')){if($v.$k -isnot [bool] -or $v.$k){throw 'SETUP_STAGING_PRESERVATION'}}
   if($v.provider_or_model_calls -ne 0 -or $v.partial_outputs_preserved -ne $true -or $v.pair_creation_started -ne $true){throw 'SETUP_STAGING_SCOPE'}
   $stageControl=Join-Path $script:stagingRoot 'controls'
   $null=Read-R3Control (Join-Path $stageControl 'staging-manifest.json') $script:stagingManifestHash 131072
   $args='-I -B "'+(Join-Path $stageControl 'stage-independent-supervisor-holds-r3-v1.py')+'" --execute --nonce '+$v.nonce+' --manifest-sha256 '+$script:stagingManifestHash
   Assert-SetupCompletedTask 'CoChem-4.2.7-StageIndependentSupervisor-20261007-v1' (Join-Path $script:stagingRoot '.venv\Scripts\python.exe') $args $stageControl 'PT3M'
  }
  'first_start' {$proof=Read-ObserverCommissioning;if($proof.Sha256 -cne $control.Sha256){throw 'SETUP_COMMISSIONING_CHANGED'}}
  'held_observation' {
   $commission=Read-ObserverCommissioning
   $staging=Read-R3Control (Join-Path $script:stagingRoot 'controls\staging-receipt.json') '' 131072
   if($v.root -cne 'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1' -or $v.staging_receipt_sha256 -cne $staging.Sha256 -or $v.commissioning_receipt_sha256 -cne $commission.Sha256 -or $v.provisioning_receipt_sha256 -cnotmatch '^[a-f0-9]{64}$' -or $v.runtime_receipt_sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'SETUP_HELD_BINDING'}
   foreach($k in @('paid_repair_enabled','component_recovery_enabled','existing_warden_changed','full_srs_acceptance')){if($v.$k -isnot [bool] -or $v.$k){throw 'SETUP_HELD_SCOPE'}}
   if($v.partial_outputs_preserved -isnot [bool] -or -not $v.partial_outputs_preserved -or $v.native_process.pid -isnot [int] -or $v.native_process.pid -le 0 -or $v.native_process.system_sid -cne 'S-1-5-18' -or $v.native_process.image_sha256 -cne 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'){throw 'SETUP_HELD_NATIVE_SCOPE'}
   $provision=Read-R3Control (Join-Path $v.root 'provisioning.json') $v.provisioning_receipt_sha256 131072;$pv=(Read-R3Text $provision)|ConvertFrom-Json
   if($script:heldObservationHelperHash -cnotmatch '^[a-f0-9]{64}$' -or $pv.helper_sha256 -cne $script:heldObservationHelperHash -or $pv.schema -cne 'cochem-held-supervisor-provision/1' -or $pv.status -cne 'HELD_OBSERVATION_PROVISIONED' -or $pv.root -cne $v.root -or $pv.staging_receipt_sha256 -cne $staging.Sha256 -or $pv.commissioning_receipt_sha256 -cne $commission.Sha256 -or $pv.observation_verified -isnot [bool] -or -not $pv.observation_verified){throw 'SETUP_HELD_PROVISION_BINDING'}
   foreach($k in @('paid_repair_enabled','component_recovery_enabled','existing_warden_changed','full_srs_acceptance')){if($pv.$k -isnot [bool] -or $pv.$k){throw 'SETUP_HELD_PROVISION_SCOPE'}}
  }
  'resource_observation' {
   $commission=Read-ObserverCommissioning
   if($v.commissioning_sha256 -cne $commission.Sha256 -or $v.source_manifest_sha256 -cne '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49' -or $v.dependency_manifest_sha256 -cne 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429' -or $v.runtime_root -cne $script:installRoot -or $v.configuration_sha256 -cne $script:configHash -or $v.revision_sha256 -cne $script:revisionHash -or $v.controller_pid -ne $commission.Value.controller.pid -or $v.controller_creation_filetime -ne $commission.Value.controller.creation_filetime -or $v.controller_instance_id -cne $commission.Value.controller.instance_id){throw 'SETUP_OBSERVER_BINDING'}
   foreach($k in @('continuous_48h_complete','desktop_heap_available','recovery_timing_available','full_srs_acceptance','automatic_restart_or_resume','owner_supervision_required')){if($v.$k -isnot [bool] -or $v.$k){throw 'SETUP_OBSERVER_SCOPE'}}
  }
  default {throw 'SETUP_PHASE_UNKNOWN'}
 }
 [pscustomobject]@{phase=$Spec.name;status=$v.status;receipt_path=$Spec.receipt;receipt_sha256=$control.Sha256}
}

function Assert-SetupCompletedTask {
 param([string]$Name,[string]$Exe,[string]$Arguments,[string]$Directory,[string]$Limit)
 $task=Get-RegistrationTask $script:folder $Name
 if($null -eq $task -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0 -or $task.LastTaskResult -ne 0){throw 'SETUP_PRIOR_TASK_NOT_TERMINAL_SUCCESS'}
 $d=$task.Definition
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or $d.Settings.ExecutionTimeLimit -cne $Limit){throw 'SETUP_PRIOR_TASK_DEFINITION'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $Exe -or $a.Arguments -cne $Arguments -or $a.WorkingDirectory -cne $Directory){throw 'SETUP_PRIOR_TASK_ACTION'}
 Assert-RegisteredTaskAcl ($task.GetSecurityDescriptor(7))
}

function Initialize-SetupNativeWitness {
 if('CoChemProtectedSetupWitness' -as [type]){return}
 Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
public sealed class CoChemProtectedSetupWitness : IDisposable {
 [StructLayout(LayoutKind.Sequential)] struct FT {public uint Low,High;}
 [StructLayout(LayoutKind.Sequential)] struct SA {public int Length;public IntPtr Descriptor;[MarshalAs(UnmanagedType.Bool)] public bool Inherit;}
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint a,bool i,uint p);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr h,out FT c,out FT e,out FT k,out FT u);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr h,uint f,StringBuilder s,ref uint n);
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h,uint t);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr h,uint a,out IntPtr t);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string s,uint r,out IntPtr p,out uint n);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateDirectory(string p,ref SA sa);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 public static void CreateFreshRoot(string path,string sddl) {
  IntPtr sd;uint size;if(!ConvertStringSecurityDescriptorToSecurityDescriptor(sddl,1,out sd,out size))throw new Win32Exception(Marshal.GetLastWin32Error());
  try{var sa=new SA{Length=Marshal.SizeOf(typeof(SA)),Descriptor=sd,Inherit=false};if(!CreateDirectory(path,ref sa))throw new Win32Exception(Marshal.GetLastWin32Error());}finally{LocalFree(sd);}
 }
 IntPtr handle; readonly long creation; readonly string image,sid; public readonly uint Pid;
 public CoChemProtectedSetupWitness(uint pid,long expectedCreation,string expectedImage,string expectedSid) {
  handle=OpenProcess(0x101000,false,pid);if(handle==IntPtr.Zero)throw new Win32Exception(Marshal.GetLastWin32Error());
  Pid=pid;creation=expectedCreation;image=expectedImage;sid=expectedSid;
  try{Verify();}catch{Dispose();throw;}
 }
 public void Verify(){
  if(handle==IntPtr.Zero||WaitForSingleObject(handle,0)!=258)throw new InvalidOperationException("SETUP_CONTROLLER_NOT_ALIVE");
  FT c,e,k,u;if(!GetProcessTimes(handle,out c,out e,out k,out u))throw new Win32Exception(Marshal.GetLastWin32Error());
  if((((long)c.High<<32)|c.Low)!=creation)throw new InvalidOperationException("SETUP_CONTROLLER_CREATION");
  uint count=32768;var path=new StringBuilder((int)count);
  if(!QueryFullProcessImageName(handle,0,path,ref count))throw new Win32Exception(Marshal.GetLastWin32Error());
  if(!String.Equals(path.ToString(),image,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("SETUP_CONTROLLER_IMAGE");
  IntPtr token;if(!OpenProcessToken(handle,8,out token))throw new Win32Exception(Marshal.GetLastWin32Error());
  try{using(var identity=new WindowsIdentity(token)){if(identity.User.Value!=sid)throw new InvalidOperationException("SETUP_CONTROLLER_SID");}}finally{CloseHandle(token);}
  if(WaitForSingleObject(handle,0)!=258)throw new InvalidOperationException("SETUP_CONTROLLER_EXITED");
 }
 public void Dispose(){if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;}}
}


'@
}

function New-SetupSeriesRoot {
 Assert-ProtectedPath (Split-Path -Parent $script:seriesRoot)
 Initialize-SetupNativeWitness
 # Atomic CreateDirectoryW refuses a concurrent creator; never reuse/re-ACL a
 # pre-existing directory. Public metadata ACL is established before any byte.
 $sddl=(New-CodeAcl $true).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All)
 [CoChemProtectedSetupWitness]::CreateFreshRoot($script:seriesRoot,$sddl)
 Assert-ProtectedPath $script:seriesRoot
}

function Open-SetupControllerWitness {
 param($Folder)
 $proof=Read-ObserverCommissioning;Assert-CommissionedTaskRunning $Folder $proof
 Initialize-SetupNativeWitness
 $c=$proof.Value.controller
 $w=[CoChemProtectedSetupWitness]::new([uint32]$c.pid,[long]$c.creation_filetime,$script:basePython,'S-1-5-18')
 try{Assert-CommissionedTaskRunning $Folder $proof;$w.Verify()}catch{$w.Dispose();throw}
 [pscustomobject]@{Handle=$w;Proof=$proof}
}

function Assert-SetupCurrentWitness {
 param($Witness,$Folder)
 $Witness.Handle.Verify();Assert-CommissionedTaskRunning $Folder $Witness.Proof;$Witness.Handle.Verify()
}

function Invoke-SetupPhases {
 param([object[]]$Disposition,[scriptblock]$Invoke,[scriptblock]$Verify,[scriptblock]$Record,[scriptblock]$Witness)
 foreach($phase in $Disposition){
  if($phase.phase -ceq 'first_start' -and $phase.mode -cne 'reattest_current_instance'){throw 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}
  if($phase.mode -ceq 'deferred_maintenance'){& $Record $phase.phase 'DEFERRED_MAINTENANCE' $null;continue}
  & $Witness
  if($phase.mode -ceq 'execute'){
   & $Record $phase.phase 'STARTED' $null
   $code=& $Invoke $phase.phase
   if($code -isnot [int] -or $code -ne 0){throw 'SETUP_PHASE_CHILD_FAILED'}
  }elseif($phase.mode -cnotin @('reuse_verified_receipt','reattest_current_instance')){throw 'SETUP_DISPOSITION_UNKNOWN'}
  $proof=& $Verify $phase.phase
  & $Witness
  & $Record $phase.phase 'VERIFIED' $proof
 }
}

function Write-SetupRecord {
 param([string]$Name,$Value)
 if($Name -cnotmatch '^[a-z][a-z0-9-]{0,80}\.json$'){throw 'SETUP_RECORD_NAME'}
 Write-RegistrationControl (Join-Path $script:seriesRoot $Name) ($Value|ConvertTo-Json -Depth 12)
}

function Write-SetupFailure {
 param([string]$Phase,[string]$Nonce,[string[]]$Completed,[string]$Code='SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED')
 if($Phase -cnotin @('preflight','oracle_maintenance','independent_staging','first_start','held_observation','resource_observation','complete')){$Phase='preflight'}
 if($Code -cnotin @('SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED','SETUP_CONTROLLER_CHANGED')){$Code='SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED'}
 $v=[ordered]@{schema='cochem-protected-acceptance-setup-failure/1';status='SETUP_HELD_PRESERVE_PARTIAL_AND_RUNNING_STATE';nonce=$Nonce;phase=$Phase;code=$Code;verified_phases=@($Completed);automatic_retry_or_resume=$false;running_state_preserved=$true;full_srs_acceptance=$false;no_automatic_stop=$true}
 $hash=Write-SetupRecord 'series-failed.json' $v
 [ordered]@{schema='cochem-protected-acceptance-setup-result/1';status=$v.status;phase=$Phase;code=$Code;receipt_path=(Join-Path $script:seriesRoot 'series-failed.json');receipt_sha256=$hash;full_srs_acceptance=$false;automatic_retry_or_resume=$false}
}

# Invocation boundary; tests exercise real functions with disposable IO and fake tasks.
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $Interactive -or -not $admin -or $identity.Name -cne 'AETHERDESK\ansac' -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or $Host.Name -cne 'ConsoleHost')){throw 'Apply requires the elevated owner in an interactive ConsoleHost, without redirected input/output.'}
$programFiles='C:\Program Files';$powershell='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$seriesRoot='C:\Program Files\CoChem\PostCommissioningSetup4.2.7-windows-20261008-r3-v2'
$commissioning='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';$runtimeManifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$stagingRoot='C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1';$stagingManifestHash='6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4'
$stagingHelperHash='UNRESOLVED_FROM_PINNED_MANIFEST'
$heldObservationHelperHash='d2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640'
$held=[Collections.Generic.List[IO.FileStream]]::new();$witness=$null;$ownsRoot=$false;$phase='preflight';$nonce=$null;$completed=[Collections.Generic.List[string]]::new()
try{
 foreach($d in Import-SetupFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile')){. ([scriptblock]::Create($d))}
 foreach($d in Import-SetupFunctions (Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1') 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198' @('Get-RegistrationTask','Assert-RegisteredTaskAcl','Write-RegistrationControl')){. ([scriptblock]::Create($d))}
 foreach($d in Import-SetupFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings')){. ([scriptblock]::Create($d))}
 foreach($d in Import-SetupFunctions (Join-Path $PSScriptRoot 'install-resource-observer-r3-v4.ps1') '106d95dd5b22613cb871f3649d4376dc0a75745b6fce38469ddf94d2fcb9ec65' @('Read-ObserverCommissioning','Assert-CommissionedTaskRunning')){. ([scriptblock]::Create($d))}
 foreach($d in Import-SetupFunctions (Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v1.ps1') '9e742d4df0b7faa07a6073dc4bc7f15ccf2924df9a14443a5c662bfbc7ff0361' @('Invoke-SeriesConsoleChild')){. ([scriptblock]::Create($d))}
 Initialize-FileIdentity;$runtime=Assert-R3InstalledBindings
 $held.Add((Open-VerifiedFile $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' (Get-Item -LiteralPath $basePython).Length))
 $manifestPath=Join-Path $PSScriptRoot 'independent-supervisor-staging-r3-v1.manifest.json';$manifestLength=(Get-Item -LiteralPath $manifestPath -Force).Length
 if($manifestLength -le 0 -or $manifestLength -gt 131072){throw 'SETUP_MANIFEST_SIZE'}
 $manifestStream=Open-VerifiedFile $manifestPath $stagingManifestHash $manifestLength;$held.Add($manifestStream)
 $manifest=[pscustomobject]@{Stream=$manifestStream;Sha256=$stagingManifestHash;Length=$manifestLength}
 $mv=(Read-R3Text $manifest)|ConvertFrom-Json
 $helper=@($mv.control_files|Where-Object{$_.relative -ceq 'stage-independent-supervisor-holds-r3-v1.py'})
 if($helper.Count -ne 1 -or $helper[0].sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'SETUP_STAGING_HELPER_PIN'};$stagingHelperHash=$helper[0].sha256
 $specs=@(Get-SetupPhaseSpecifications);$byName=@{};$holds=[Collections.Generic.List[object]]::new();$previews=[Collections.Generic.List[object]]::new()
 foreach($s in $specs){$byName[$s.name]=$s;if($s.name -ceq 'oracle_maintenance'){continue};if($s.hash -cnotmatch '^[a-f0-9]{64}$'){$holds.Add([pscustomobject]@{phase=$s.name;code='DEPENDENT_ARTIFACT_NOT_FROZEN'});continue};$path=Join-Path $PSScriptRoot $s.file;$held.Add((Open-VerifiedFile $path $s.hash (Get-Item -LiteralPath $path).Length))}
 if((Get-SetupPathState $seriesRoot) -cne 'absent'){$holds.Add([pscustomobject]@{phase='preflight';code='SERIES_ALREADY_EXISTS_NO_RESUME'})}
 $commissioningPresent=((Get-SetupPathState $commissioning) -ceq 'present')
 $branch='commissioned'
 $folder=$null;$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
 $commissionedVerified=$false
 if($commissioningPresent){
  try{$witness=Open-SetupControllerWitness $folder;$commissionedVerified=$true}catch{$holds.Add([pscustomobject]@{phase='preflight';code='CURRENT_COMMISSIONED_INSTANCE_NOT_REATTESTED'})}
 }else{
  $holds.Add([pscustomobject]@{phase='preflight';code='COMMISSIONING_REQUIRED_NO_AUTHENTICATION_OR_STARTUP_IN_THIS_BATCH'})
  if((Get-SetupPathState (Split-Path -Parent $commissioning)) -ceq 'present'){$holds.Add([pscustomobject]@{phase='preflight';code='PARTIAL_FIRST_START_PRESERVE'})}
 }
 $staged=$false
 if((Get-SetupPathState $byName.independent_staging.receipt) -ceq 'present'){$null=Read-SetupReceipt $byName.independent_staging;$staged=$true}
 $disposition=@(Get-SetupDisposition $branch $staged)
 foreach($p in $disposition){
  $s=$byName[$p.phase];if($p.mode -cne 'execute' -or $s.hash -cnotmatch '^[a-f0-9]{64}$'){continue}
  try{$plan=Get-SetupPreview $s;$previews.Add([pscustomobject]@{phase=$p.phase;plan=$plan});foreach($h in Get-SetupEffectiveHolds $p.phase $plan $branch $staged){$holds.Add($h)}}catch{$holds.Add([pscustomobject]@{phase=$p.phase;code='PHASE_PREVIEW_NOT_VERIFIED'})}
 }
 $report=[ordered]@{schema='cochem-protected-acceptance-setup-plan/1';mode='READ_ONLY_PLAN';operationally_released=$true;branch=$branch;administrator=$admin;current_commissioned_instance_reattested=$commissionedVerified;phases=$disposition;phase_previews=@($previews.ToArray());holds=@($holds.ToArray());oracle_global_startup_prerequisite=$false;oracle_deferred_maintenance=$true;authentication_or_first_start_invoked=$false;commissioning_required_before_apply=$true;identities=6;shared_capacity=4;automatic_repair_enabled=$false;full_srs_acceptance=$false;workflows_accepted=$false;owner_supervision_required=$false;automatic_retry_or_resume=$false;system_private_preflight_deferred=$true;series_root=$seriesRoot}
 if(-not $Apply){$report|ConvertTo-Json -Depth 14;return}
 # Only the frozen reviewed phase graph is released. Actual preflight holds
 # still stop before any phase, and no prior partial series can be repeated.
 if(-not $report.operationally_released -or $holds.Count){throw 'Protected acceptance prerequisites remain held; no phase has been executed.'}
 New-SetupSeriesRoot;$ownsRoot=$true;$nonce=[Guid]::NewGuid().ToString('N')
 $null=Write-SetupRecord 'series-intent.json' ([ordered]@{schema='cochem-protected-acceptance-setup-intent/1';nonce=$nonce;branch=$branch;phase_helpers=@($specs|Select-Object name,hash);full_srs_acceptance=$false;automatic_retry_or_resume=$false;started_utc=[DateTime]::UtcNow.ToString('o')})
 $null=Write-SetupRecord 'preflight.json' $report
 Invoke-SetupPhases $disposition {
  param($name);$script:phase=$name;$s=$byName[$name]
  $args=@('-NoLogo','-NoProfile','-File',(Join-Path $PSScriptRoot $s.file))+@($s.args)+@('-Apply')
  if($name -ceq 'first_start'){throw 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}
  Invoke-SeriesConsoleChild $args
 } {
  param($name);$proof=Read-SetupReceipt $byName[$name]
  if($name -ceq 'first_start' -and $null -eq $script:witness){$script:witness=Open-SetupControllerWitness $folder}
  $proof
 } {
  param($name,$state,$proof)
  $null=Write-SetupRecord ($name.Replace('_','-')+'-'+$state.ToLowerInvariant().Replace('_','-')+'.json') ([ordered]@{schema='cochem-protected-acceptance-setup-event/1';nonce=$nonce;phase=$name;state=$state;proof=$proof;observed_utc=[DateTime]::UtcNow.ToString('o')})
  if($state -ceq 'VERIFIED'){$completed.Add($name)}
 } {if($null -ne $script:witness){Assert-SetupCurrentWitness $script:witness $folder}}
 $phase='complete'
 $value=[ordered]@{schema='cochem-protected-acceptance-setup-complete/1';status='COMMISSIONED_CONTROLLER_AND_HELD_OBSERVATION_STARTED_ACCEPTANCE_PENDING';nonce=$nonce;verified_phases=@($completed.ToArray());oracle_deferred_maintenance=$true;workflows_accepted=$false;continuous_48h_complete=$false;desktop_heap_available=$false;automatic_repair_enabled=$false;full_srs_acceptance=$false;automatic_retry_or_resume=$false;owner_supervision_required=$false;no_automatic_stop=$true}
 $pin=Write-SetupRecord 'series-complete.json' $value
 [ordered]@{schema='cochem-protected-acceptance-setup-result/1';status=$value.status;receipt_path=(Join-Path $seriesRoot 'series-complete.json');receipt_sha256=$pin;full_srs_acceptance=$false;workflows_accepted=$false;oracle_deferred_maintenance=$value.oracle_deferred_maintenance}|ConvertTo-Json -Depth 5
}catch{
 if($ownsRoot){try{Write-SetupFailure $phase $nonce @($completed.ToArray())|ConvertTo-Json -Depth 6|Write-Host}catch{Write-Host 'SETUP_FAILURE_RECEIPT_UNAVAILABLE_PRESERVE_SERIES'}}
 throw 'Protected acceptance setup is held. Preserve every partial artifact and any running process; no automatic repeat, stop or cleanup.'
}finally{if($null -ne $witness){$witness.Handle.Dispose()};foreach($s in $held){$s.Dispose()}}
