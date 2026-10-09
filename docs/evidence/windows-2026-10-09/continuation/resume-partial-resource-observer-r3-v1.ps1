#Requires -Version 5.1
<# Read-only projection by default. Apply validates the exact preserved, disabled
   resource task and copied payload, publishes only missing registration evidence,
   then invokes the unchanged single-start implementation once. No task registration,
   definition/ACL repair, payload copy, reprovisioning, retry, stop or cleanup. #>
[CmdletBinding()]param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-PartialResourceFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'RESOURCE_RECOVERY_SUPPORT_CHANGED'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'RESOURCE_RECOVERY_SUPPORT_PARSE'}
  foreach($name in $Names){$f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($f.Count -ne 1){throw 'RESOURCE_RECOVERY_SUPPORT_DEFINITION'};$f[0].Extent.Text}
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Get-PartialResourceDefinition {
 param($Task,[string]$Arguments)
 if($null -eq $Task){throw 'RESOURCE_RECOVERY_TASK_MISSING'}
 $d=$Task.Definition;$a=$null;if($d.Actions.Count -eq 1){$a=$d.Actions.Item(1)}
 $raw=[string]$d.Settings.ExecutionTimeLimit
 $safeRaw=if($raw.Length -le 128 -and $raw -cmatch '\A[A-Z0-9.]+\z'){$raw}else{'FORMAT_REFUSED'}
 $checks=[ordered]@{
  task_name=($Task.Name -ceq $script:taskName);system_principal=($d.Principal.UserId -in @('SYSTEM','S-1-5-18'));logon_type=($d.Principal.LogonType -eq 5);run_level=($d.Principal.RunLevel -eq 1)
  allow_demand_start=($d.Settings.AllowDemandStart -eq $true);multiple_instances=($d.Settings.MultipleInstances -eq 2);restart_count=($d.Settings.RestartCount -eq 0);duration_49_hours=(Test-ExactObserverDuration $d.Settings.ExecutionTimeLimit 176400)
  no_triggers=($d.Triggers.Count -eq 0);one_action=($d.Actions.Count -eq 1);action_type=($null -ne $a -and $a.Type -eq 0);action_path=($null -ne $a -and $a.Path -ceq $script:python);action_arguments=($null -ne $a -and $a.Arguments -ceq $Arguments);action_directory=($null -ne $a -and $a.WorkingDirectory -ceq $script:packageRoot)
  disabled=($Task.Enabled -eq $false -and $d.Settings.Enabled -eq $false);state_disabled=($Task.State -eq 1);no_instances=($Task.GetInstances(0).Count -eq 0);never_run=($Task.LastTaskResult -eq 267011)
 }
 $mismatches=@($checks.Keys|Where-Object{-not $checks[$_]})
 $actual=[ordered]@{principal_user=$(if($d.Principal.UserId -in @('SYSTEM','S-1-5-18')){[string]$d.Principal.UserId}else{'VALUE_REFUSED'});logon_type=(Get-PartialResourceScalar $d.Principal.LogonType 'integer');run_level=(Get-PartialResourceScalar $d.Principal.RunLevel 'integer');allow_demand_start=(Get-PartialResourceScalar $d.Settings.AllowDemandStart 'boolean');multiple_instances=(Get-PartialResourceScalar $d.Settings.MultipleInstances 'integer');restart_count=(Get-PartialResourceScalar $d.Settings.RestartCount 'integer');triggers=(Get-PartialResourceScalar $d.Triggers.Count 'integer');actions=(Get-PartialResourceScalar $d.Actions.Count 'integer');task_enabled=(Get-PartialResourceScalar $Task.Enabled 'boolean');settings_enabled=(Get-PartialResourceScalar $d.Settings.Enabled 'boolean');task_state=(Get-PartialResourceScalar $Task.State 'integer');last_task_result=(Get-PartialResourceScalar $Task.LastTaskResult 'integer');running_instances=(Get-PartialResourceScalar $Task.GetInstances(0).Count 'integer')}
 [ordered]@{schema='cochem-resource-observer-definition-projection/1';task_name=$script:taskName;actual=$actual;checks=$checks;mismatches=$mismatches;execution_time_limit_raw=$safeRaw;legacy_literal_duration_match=($raw -ceq 'PT49H');semantic_duration_seconds=176400;task_mutations=0;secrets_published=$false}
}

function Get-PartialResourceScalar {
 param($Value,[string]$Kind)
 if($Kind -ceq 'boolean' -and $Value -is [bool]){return $Value}
 if($Kind -ceq 'integer' -and ($Value -is [int] -or $Value -is [long] -or $Value -is [int16] -or $Value -is [uint32])){return [long]$Value}
 return 'TYPE_REFUSED'
}

function Get-PartialResourceTaskSnapshot {
 param($Folder,[switch]$DaemonsOnly)
 $names=@('CoChem-4.2.7-Warden','CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1');if(-not $DaemonsOnly){$names+=@($script:taskName)}
 $rows=@();foreach($name in $names){
  $t=Get-RegistrationTask $Folder $name;if($null -eq $t){throw 'RESOURCE_RECOVERY_BOUND_TASK_MISSING'}
  $instances=$t.GetInstances(0);if($instances.Count -gt 1){throw 'RESOURCE_RECOVERY_MULTIPLE_TASK_INSTANCES'};$guid=$null;if($instances.Count -eq 1){$guid=[string]$instances.Item(1).InstanceGuid}
  $sha=[Security.Cryptography.SHA256]::Create();try{$xml=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::Unicode.GetBytes([string]$t.Xml))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $sha=[Security.Cryptography.SHA256]::Create();try{$acl=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$t.GetSecurityDescriptor(7)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  $rows+=@([ordered]@{name=$name;enabled=[bool]$t.Enabled;state=[int]$t.State;last_result=[long]$t.LastTaskResult;instances=[int]$instances.Count;instance_guid=$guid;xml_sha256=$xml;acl_sha256=$acl})
 };$rows|ConvertTo-Json -Depth 5 -Compress
}

function Open-PartialResourceDaemonWitness {
 param($Folder,$Activation)
 $controller=Open-SetupControllerWitness $Folder;$script:controllerWitness=$controller
 $name='CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1';$t=Get-RegistrationTask $Folder $name;$n=$Activation.native_process
 if($null -eq $t -or $t.Name -cne $name -or -not $t.Enabled -or $t.State -ne 4 -or $t.GetInstances(0).Count -ne 1 -or $t.GetInstances(0).Item(1).InstanceGuid -cne $n.task_instance_guid){throw 'RESOURCE_RECOVERY_HELD_OBSERVER_CHANGED'}
 $d=$t.Definition;$a=$d.Actions.Item(1);$heldRoot='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1'
 $heldArgs='-I -B "'+(Join-Path $heldRoot 'held-supervisor-observation-r3-v1.py')+'" --observe'
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Actions.Count -ne 1 -or $a.Type -ne 0 -or $a.Path -cne 'C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1\.venv\Scripts\python.exe' -or $a.Arguments -cne $heldArgs -or $a.WorkingDirectory -cne $heldRoot -or $d.Settings.Enabled -ne $true -or $d.Settings.AllowDemandStart -ne $true -or $d.Settings.ExecutionTimeLimit -cne 'PT0S' -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or $d.Triggers.Count -ne 1 -or $d.Triggers.Item(1).Type -ne 8 -or -not $d.Triggers.Item(1).Enabled){throw 'RESOURCE_RECOVERY_HELD_DEFINITION_CHANGED'}
 Assert-RegisteredTaskAcl ($t.GetSecurityDescriptor(7)) $t.Name
 Initialize-HeldProcessWitness;$w=[CoChemHeldObservationProcess]::new([int]$n.pid)
 try{if($n.pid -isnot [int] -or $n.pid -ne 24608 -or $n.creation_filetime -isnot [long] -or $w.CreationFiletime -ne $n.creation_filetime -or $w.Sid -cne 'S-1-5-18' -or -not $w.Image.Equals($script:python,[StringComparison]::OrdinalIgnoreCase)){throw 'RESOURCE_RECOVERY_HELD_NATIVE_CHANGED'};$w.AssertLive();$script:heldObserverWitness=$w;$w=$null}finally{if($null -ne $w){$w.Dispose()}}
 $controller
}

function Read-PartialResourcePinnedJson {
 param([string]$Path,[string]$Hash)
 $c=Read-R3Control $Path $Hash 1048576;[pscustomobject]@{Value=((Read-R3Text $c)|ConvertFrom-Json);Sha256=$c.Sha256}
}

function Get-PartialResourcePreflight {
 param($Folder)
 $script:recoveryPhase='preserved_parent_evidence'
 Assert-ObserverAbsent $script:recoveryRoot
 foreach($name in @('observer-task.xml','observer-registration.json','start-intent.json','observer-started.json','observer-completed.json','observer-start-failure.json','observer-bootstrap-failure.json')){Assert-ObserverAbsent (Join-Path $script:targetRoot $name)}
 $entries=@(Get-ChildItem -LiteralPath $script:targetRoot -Force)
 if($entries.Count -ne 3 -or (@($entries.Name|Sort-Object) -join '|') -cne 'dependencies.json|package|registration-intent.json'){throw 'RESOURCE_RECOVERY_PARTIAL_INVENTORY_DIFFERS'}
 $parent=Read-PartialResourcePinnedJson (Join-Path $script:priorRoot 'series-failed.json') '25bb0e9a58005d9f5529f1dc1d0986c0804da71221ee1503512c0d22df616b5b'
 $p=$parent.Value
 if($p.schema -cne 'cochem-protected-acceptance-setup-failure/1' -or $p.status -cne 'SETUP_HELD_PRESERVE_PARTIAL_AND_RUNNING_STATE' -or $p.nonce -cne '0b8014d6c60c40cfa1a0be9bd51ab3db' -or $p.phase -cne 'resource_observation' -or (@($p.verified_phases) -join '|') -cne 'independent_staging|first_start|held_observation' -or $p.automatic_retry_or_resume -ne $false -or $p.running_state_preserved -ne $true){throw 'RESOURCE_RECOVERY_PRIOR_FAILURE_DIFFERS'}
 $null=Read-PartialResourcePinnedJson (Join-Path $script:priorRoot 'series-intent.json') 'fb80fbe501f5ee4887899a29edd5d59e5480b5f3ab01cd472e2e8802069b8852'
 $activation=Read-PartialResourcePinnedJson 'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1\activation.json' '5599e920e8b457e6389e5d7677a4eca6e5479e8dc72bba2f41ccb5d46e640171'
 $null=Read-PartialResourcePinnedJson 'C:\Program Files\CoChem\HeldObserverRecovery4.2.7-windows-20261008-r3-v1\recovery-complete.json' '20c2e18bf33409bed4813faec3f4917b000917b032dbb39113f00a6c9a216c4a'
 $intent=Read-PartialResourcePinnedJson (Join-Path $script:targetRoot 'registration-intent.json') '3fde035d1aed9f06b950284953a5737ba089cf7e5ab2e12ea5a24a06425537bd'
 $runtime=Assert-R3InstalledBindings;$commission=Read-ObserverCommissioning
 if($commission.Sha256 -cne '4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3'){throw 'RESOURCE_RECOVERY_COMMISSION_CHANGED'}
 $i=$intent.Value;$c=$commission.Value.controller
 if($i.schema -cne 'cochem-resource-observer-registration-intent/1' -or $i.source_manifest_sha256 -cne $script:sourceManifestHash -or $i.dependency_manifest_sha256 -cne $script:dependencyHash -or $i.commissioning_sha256 -cne $commission.Sha256 -or $i.task_name -cne $script:taskName -or $i.controller_pid -ne $c.pid -or $i.controller_creation_filetime -ne $c.creation_filetime -or $i.runtime_root -cne $script:installRoot -or $i.full_srs_acceptance -ne $false -or $i.automatic_restart_or_resume -ne $false){throw 'RESOURCE_RECOVERY_REGISTRATION_INTENT_DIFFERS'}
 $script:recoveryPhase='source_custody';$records=Get-ObserverInventory;$dependencies=Assert-ObserverDependencies -CompleteCustody
 $null=Assert-ObserverInstalledSources $records;Assert-ObserverStateUnstarted
 $script:recoveryPhase='task_validation'
 $args=Get-ObserverArguments $commission.Sha256;$task=Get-RegistrationTask $Folder $script:taskName
 if($null -eq $task -or $task.Name -cne $script:taskName){throw 'RESOURCE_RECOVERY_TASK_MISSING'};Assert-ObserverTask $task $args
 $script:recoveryPhase='native_daemon_proof';$controller=Open-PartialResourceDaemonWitness $Folder $activation.Value
 $snapshot=Get-PartialResourceTaskSnapshot $Folder;$daemons=Get-PartialResourceTaskSnapshot $Folder -DaemonsOnly
 Assert-SetupCurrentWitness $controller $Folder;$script:heldObserverWitness.AssertLive()
 [pscustomobject]@{Runtime=$runtime;Commissioning=$commission;Records=$records;Dependencies=$dependencies;Task=$task;Arguments=$args;RegistrationIntent=$intent;Snapshot=$snapshot;DaemonSnapshot=$daemons;Controller=$controller}
}

function Invoke-PartialResourceRecovery {
 param($Folder,$Preflight)
 $script:recoveryPhase='registration_publication'
 Assert-ObserverAbsent $script:recoveryRoot
 foreach($name in @('observer-task.xml','observer-registration.json','start-intent.json')){Assert-ObserverAbsent (Join-Path $script:targetRoot $name)}
 Assert-ObserverStateUnstarted;Assert-SetupCurrentWitness $Preflight.Controller $Folder;$script:heldObserverWitness.AssertLive()
 $task=Get-RegistrationTask $Folder $script:taskName;Assert-ObserverTask $task $Preflight.Arguments
 if((Get-PartialResourceTaskSnapshot $Folder) -cne $Preflight.Snapshot){throw 'RESOURCE_RECOVERY_TASKS_CHANGED'}
 Assert-ProtectedPath (Split-Path -Parent $script:recoveryRoot);Initialize-SetupNativeWitness
 [CoChemProtectedSetupWitness]::CreateFreshRoot($script:recoveryRoot,(New-CodeAcl $true).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All));Assert-ProtectedPath $script:recoveryRoot
 $rawLimit=[string]$task.Definition.Settings.ExecutionTimeLimit
 $intent=[ordered]@{schema='cochem-partial-resource-observer-recovery-intent/1';status='PRESERVED_DISABLED_TASK_REGISTRATION_COMPLETION';prior_failure_sha256='25bb0e9a58005d9f5529f1dc1d0986c0804da71221ee1503512c0d22df616b5b';original_registration_intent_sha256=$Preflight.RegistrationIntent.Sha256;execution_time_limit_raw=$rawLimit;execution_time_limit_seconds=176400;task_action_limits_triggers_or_acl_modified=$false;task_registration_requested=0;payloads_copied=0;exactly_one_start_requested=$true;automatic_restart_or_resume=$false;full_srs_acceptance=$false}
 $intentHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'recovery-intent.json') ($intent|ConvertTo-Json -Depth 6)
 if((Get-PartialResourceTaskSnapshot $Folder) -cne $Preflight.Snapshot){throw 'RESOURCE_RECOVERY_TASKS_CHANGED'}
 Assert-SetupCurrentWitness $Preflight.Controller $Folder;$script:heldObserverWitness.AssertLive()
 $xmlHash=Write-RegistrationControl (Join-Path $script:targetRoot 'observer-task.xml') ([string]$task.Xml)
 $receipt=[ordered]@{schema='cochem-resource-observer-registration/1';status='RESOURCE_OBSERVER_REGISTERED_DISABLED';task_name=$script:taskName;source_manifest_sha256=$script:sourceManifestHash;dependency_manifest_sha256=$script:dependencyHash;commissioning_sha256=$Preflight.Commissioning.Sha256;runtime_root=$script:installRoot;install_receipt_sha256=$Preflight.Runtime.install_receipt_sha256;revision_sha256=$Preflight.Runtime.revision.source_sha256;configuration_sha256=$Preflight.Runtime.configuration_sha256;task_xml_sha256=$xmlHash;intent_sha256=$Preflight.RegistrationIntent.Sha256;dependencies=$Preflight.Dependencies;task_started=$false;full_srs_acceptance=$false;automatic_restart_or_resume=$false;preserved_registration_recovery_sha256=$intentHash;execution_time_limit_raw=$rawLimit;execution_time_limit_seconds=176400}
 $registrationHash=Write-RegistrationControl (Join-Path $script:targetRoot 'observer-registration.json') ($receipt|ConvertTo-Json -Depth 8)
 $registration=Read-ObserverRegistration $Folder $Preflight.Commissioning $Preflight.Records
 if($registration.Sha256 -cne $registrationHash){throw 'RESOURCE_RECOVERY_REGISTRATION_CHANGED'}
 Assert-SetupCurrentWitness $Preflight.Controller $Folder;$script:heldObserverWitness.AssertLive()
 if((Get-PartialResourceTaskSnapshot $Folder) -cne $Preflight.Snapshot){throw 'RESOURCE_RECOVERY_TASKS_CHANGED'}
 # Original durable start-intent CreateNew fences its sole Enable/Run. This
 # helper never catches and repeats a start or reuses a recovery namespace.
 $script:recoveryPhase='single_start';$result=Invoke-ObserverStartWithReceipt $Folder $Preflight.Commissioning $registration
 Assert-SetupCurrentWitness $Preflight.Controller $Folder;$script:heldObserverWitness.AssertLive()
 if((Get-PartialResourceTaskSnapshot $Folder -DaemonsOnly) -cne $Preflight.DaemonSnapshot){throw 'RESOURCE_RECOVERY_EXISTING_DAEMONS_CHANGED'}
 $complete=[ordered]@{schema='cochem-partial-resource-observer-recovery-result/1';status=$result.status;recovery_intent_sha256=$intentHash;registration_receipt_sha256=$registrationHash;observer_started_receipt_sha256=$result.receipt_sha256;existing_daemons_unchanged=$true;task_action_limits_triggers_or_acl_modified=$false;task_registration_requested=0;payloads_copied=0;exactly_one_start_requested=$true;automatic_restart_or_resume=$false;continuous_48h_complete=$false;full_srs_acceptance=$false}
 $completeHash=Write-RegistrationControl (Join-Path $script:recoveryRoot 'recovery-complete.json') ($complete|ConvertTo-Json -Depth 6)
 $result.recovery_receipt_path=Join-Path $script:recoveryRoot 'recovery-complete.json';$result.recovery_receipt_sha256=$completeHash;$result
}

# Host boundary. Fixtures import function definitions only.
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
if($PSVersionTable.PSEdition -cne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$python=Join-Path $basePythonRoot 'python.exe';$basePython=$python;$psutilRoot=Join-Path $installRoot '.venv\Lib\site-packages\psutil'
$targetRoot='C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v4';$packageRoot=Join-Path $targetRoot 'package';$stateRoot='C:\Program Files\CoChem\ResourceObservationState4.2.7-windows-20261008-r3-v4';$cacheRoot=Join-Path $stateRoot 'empty-cache'
$taskName='CoChem-4.2.7-ResourceObservation-20261008-r3-v4';$commissioning='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json'
$sourceRoot=Join-Path $PSScriptRoot 'resource-observer-r3-v4';$sourceManifest=Join-Path $sourceRoot 'source-manifest.json';$sourceManifestHash='88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49'
$dependencyManifest=Join-Path $PSScriptRoot 'resource-observer-r3-v2-dependencies.json';$dependencyHash='ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c';$runtimeManifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$priorRoot='C:\Program Files\CoChem\RunningMonitoringContinuation4.2.7-windows-20261008-r3-v8';$recoveryRoot='C:\Program Files\CoChem\ResourceObserverRecovery4.2.7-windows-20261009-r3-v1'
$held=[Collections.Generic.List[IO.FileStream]]::new();$controllerWitness=$null;$heldObserverWitness=$null;$mutex=$null;$locked=$false
try{
 foreach($dep in @(
  @{path='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1';hash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b';names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','Initialize-FileIdentity','Open-VerifiedFile')},
  @{path=(Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1');hash='eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198';names=@('Get-RegistrationTask','Write-RegistrationControl')},
  @{path=(Join-Path $PSScriptRoot 'task-private-acl-v8.ps1');hash='f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47';names=@('Assert-RegisteredTaskAcl')},
  @{path=(Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1');hash='18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7';names=@('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings')},
  @{path=(Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1');hash='5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d';names=@('Assert-CodeTreeOnce')},
  @{path=(Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v1.ps1');hash='9e742d4df0b7faa07a6073dc4bc7f15ccf2924df9a14443a5c662bfbc7ff0361';names=@('Assert-SeriesPrivateRoot')},
  @{path=(Join-Path $PSScriptRoot 'install-resource-observer-r3-v5.ps1');hash='ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f';names=@('Assert-ObserverAbsent','Assert-RelativeObserverPath','Read-ObserverSource','Get-ObserverInventory','Assert-ObserverDependencies','Read-ObserverCommissioning','Assert-CommissionedTaskRunning','Get-ObserverArguments','Assert-ObserverStateUnstarted','Assert-ObserverInstalledSources','Read-ObserverRegistration','Read-ObserverFirstLine','Assert-ObserverInitialSamples','Invoke-ObserverStart','Write-ObserverStartFailure','Invoke-ObserverStartWithReceipt')},
  @{path=(Join-Path $PSScriptRoot 'resource-observer-duration-r3-v1.ps1');hash='3dd63e3db9ed65210571dd2336b1d5f67e3464415bb04bc11aa916dc9e17dd9c';names=@('Test-ExactObserverDuration','Assert-ObserverTask')},
  @{path=(Join-Path $PSScriptRoot 'run-post-commissioning-setup-r3-v4.ps1');hash='933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4';names=@('Initialize-SetupNativeWitness','Open-SetupControllerWitness','Assert-SetupCurrentWitness')},
  @{path=(Join-Path $PSScriptRoot 'install-held-supervisor-observation-r3-v3.ps1');hash='f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6';names=@('Initialize-HeldProcessWitness')}
 )){foreach($text in @(Import-PartialResourceFunctions $dep.path $dep.hash $dep.names)){. ([scriptblock]::Create($text))}}
 Initialize-FileIdentity;$holds=@();$holdFailure=$null;$projection=$null;$proof=$null;$folder=$null;$recoveryPhase='definition_projection'
 try{
  $scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
  $task=Get-RegistrationTask $folder $taskName;$projection=Get-PartialResourceDefinition $task (Get-ObserverArguments '4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3')
  if($Apply){Write-Host ($projection|ConvertTo-Json -Depth 6 -Compress)}
  $mutex=[Threading.Mutex]::new($false,'Global\CoChem427-ResourceObserverRecovery-r3-v1');try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true};if(-not $locked){throw 'RESOURCE_RECOVERY_OTHER_OWNER'}
  $proof=Get-PartialResourcePreflight $folder
 }catch{
  $code=if($_.Exception.Message -cmatch '\ARESOURCE_RECOVERY_[A-Z_]+\z'){$_.Exception.Message}elseif($recoveryPhase -ceq 'task_validation'){'RESOURCE_RECOVERY_TASK_GUARD_DIFFERS'}else{'RESOURCE_RECOVERY_GUARD_OR_ACCESS_DENIAL'}
  $type=$_.Exception.GetType().Name;if($type -cnotmatch '\A[A-Za-z][A-Za-z0-9_]{0,79}\z'){$type='Exception'}
  $holdFailure=[ordered]@{phase=$recoveryPhase;code=$code;error_type=$type;hresult=[int]$_.Exception.HResult}
  $holds+='Exact preserved disabled task, partial registration, private empty state and running native daemon proofs require Administrator verification.'
 }
 if(-not $Apply){[ordered]@{schema='cochem-partial-resource-observer-recovery-plan/1';mode='READ_ONLY_PLAN';administrator=$admin;definition=$projection;holds=$holds;failure=$holdFailure;task_registration_requested=0;payloads_copied=0;task_action_limits_triggers_or_acl_modified=$false;optional_single_start=$true;automatic_restart_or_resume=$false;continuous_48h_complete=$false;full_srs_acceptance=$false}|ConvertTo-Json -Depth 8;return}
 if($holds.Count -or $null -eq $proof){Write-Host ([ordered]@{schema='cochem-partial-resource-observer-recovery-held/1';definition=$projection;failure=$holdFailure;outputs_created=$false;task_runs_requested=0;automatic_restart_or_resume=$false}|ConvertTo-Json -Depth 8 -Compress);throw 'Resource observer recovery is held; no registration evidence was published and no task was started.'}
 Invoke-PartialResourceRecovery $folder $proof|ConvertTo-Json -Depth 8
}finally{if($null -ne $controllerWitness){$controllerWitness.Handle.Dispose()};if($null -ne $heldObserverWitness){$heldObserverWitness.Dispose()};if($locked){$mutex.ReleaseMutex()};if($null -ne $mutex){$mutex.Dispose()};foreach($stream in $held){$stream.Dispose()}}
