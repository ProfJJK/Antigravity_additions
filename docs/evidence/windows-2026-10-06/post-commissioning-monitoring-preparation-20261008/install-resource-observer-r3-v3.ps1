#Requires -Version 5.1
<# Default is read-only. Explicit -Apply registers a disabled independent
observer. -Operation RegisterAndStart optionally starts that same new task once.
StartRegistered only consumes an exact preserved, never-started registration.
No worker/controller changes, recovery action, credentials, databases or models.
Partial roots/tasks and start intent are preserved; no automatic retry/cleanup. #>
[CmdletBinding()]
param([switch]$Apply,[ValidateSet('Register','RegisterAndStart','StartRegistered')][string]$Operation='Register')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-ObserverFunctions {
 param([string]$Path,[string]$Hash,[string[]]$Names)
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  if($actual -cne $Hash){throw 'Reviewed observer support changed.'}
  $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
  try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
  $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
  if($errors.Count){throw 'Reviewed observer support parse failure.'}
  foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name});if($nodes.Count -ne 1){throw 'Missing exact reviewed function.'};$nodes[0].Extent.Text}
  $script:held.Add($stream);$stream=$null
 }finally{if($null -ne $stream){$stream.Dispose()}}
}

function Assert-ObserverAbsent {
 param([string]$Path)
 try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch [Management.Automation.ItemNotFoundException]{return}catch{throw 'Observer namespace is inaccessible; absence is not proved.'}
 throw 'Observer namespace exists. Preserve partial state; registration cannot be repeated.'
}

function Assert-RelativeObserverPath {
 param([string]$Value)
 if(-not $Value -or $Value -cmatch '[\\:\x00-\x1f]' -or $Value.StartsWith('/') -or @($Value.Split('/')|Where-Object{$_ -in @('','.','..')}).Count){throw 'Unsafe observer inventory path.'}
}

function Read-ObserverSource {
 param([string]$Path,[string]$Pin,[long]$Maximum=16777216)
 $item=Get-Item -LiteralPath $Path -Force
 if($item.PSIsContainer -or $item.Length -gt $Maximum -or $item.Length -le 0){throw 'Observer source length differs.'}
 $stream=Open-VerifiedFile $Path $Pin $item.Length;$script:held.Add($stream)
 [pscustomobject]@{Stream=$stream;Sha256=$Pin;Length=$item.Length}
}

function Get-ObserverInventory {
 $control=Read-ObserverSource $script:sourceManifest $script:sourceManifestHash 65536
 $value=(Read-R3Text $control)|ConvertFrom-Json
 $expected=@('observe_resources.py','detector_bootstrap.py','cochem_supervisor/__init__.py','cochem_supervisor/windows.py','cochem_supervisor/resource_observation.py','cochem_supervisor/performance_acceptance.py','cochem_supervisor/shared_io.py')
 if($value.schema -cne 'cochem-external-observer-source-manifest/1' -or @($value.files).Count -ne 7){throw 'Observer source manifest differs.'}
 $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal);$records=[Collections.Generic.List[object]]::new()
 foreach($row in $value.files){
  Assert-RelativeObserverPath $row.path
  if($row.path -cnotin $expected -or -not $seen.Add($row.path) -or $row.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $row.size -le 0 -or $row.size -gt 1048576){throw 'Observer source row differs.'}
  $path=Join-Path $script:sourceRoot $row.path;$file=Read-ObserverSource $path $row.sha256 1048576
  if($file.Length -ne $row.size){throw 'Observer source length changed.'}
  $records.Add([pscustomobject]@{source=$path;destination=(Join-Path $script:packageRoot $row.path);sha256=$row.sha256;length=$row.size})
 }
 $records.Add([pscustomobject]@{source=$script:sourceManifest;destination=(Join-Path $script:packageRoot 'source-manifest.json');sha256=$script:sourceManifestHash;length=$control.Length})
 return ,$records
}

function Assert-ObserverDependencies {
 param([switch]$CompleteCustody)
 $control=Read-ObserverSource $script:dependencyManifest $script:dependencyHash
 $value=(Read-R3Text $control)|ConvertFrom-Json
 if($value.schema -cne 'cochem-sterile-observer-dependencies/1' -or $value.base_root -cne $script:basePythonRoot -or $value.psutil_root -cne $script:psutilRoot -or
    $value.original_payload_manifest_sha256 -cne 'db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0' -or @($value.base_files).Count -ne 2777 -or @($value.psutil_files).Count -ne 11){throw 'Observer dependency manifest differs.'}
 if(-not $CompleteCustody){return [ordered]@{base_files=2777;psutil_files=11;complete_custody_deferred=$true}}
 $baseCount=Assert-CodeTreeOnce $script:basePythonRoot;$psutilCount=Assert-CodeTreeOnce $script:psutilRoot
 foreach($group in @([pscustomobject]@{root=$script:basePythonRoot;rows=@($value.base_files);cacheExcluded=$true},[pscustomobject]@{root=$script:psutilRoot;rows=@($value.psutil_files);cacheExcluded=$false})){
  $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($row in $group.rows){
   Assert-RelativeObserverPath $row.relative
   if(-not $seen.Add($row.relative) -or $row.relative.EndsWith('.pyc',[StringComparison]::OrdinalIgnoreCase) -or $row.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $row.length -lt 0 -or $row.length -gt 67108864){throw 'Invalid dependency source row.'}
   $path=Join-Path $group.root $row.relative
   $stream=Open-VerifiedFile $path $row.sha256 $row.length;$script:held.Add($stream)
  }
  $actual=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($file in Get-ChildItem -LiteralPath $group.root -Recurse -Force -File){
   $key=$file.FullName.Substring($group.root.Length+1).Replace('\','/')
   if($group.cacheExcluded -and $key.EndsWith('.pyc',[StringComparison]::OrdinalIgnoreCase)){continue}
   if(-not $actual.Add($key) -or -not $seen.Contains($key)){throw 'Unpinned interpreter or psutil file found.'}
  }
  if($actual.Count -ne $seen.Count){throw 'Dependency inventory is incomplete.'}
 }
 [ordered]@{base_files=2777;psutil_files=11;base_custody_entries=$baseCount;psutil_custody_entries=$psutilCount;complete_custody_deferred=$false;historical_pyc_not_executed=$true}
}

function Read-ObserverCommissioning {
 $control=Read-R3Control $script:commissioning '' 1048576;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.schema -cne 'cochem-warden-commissioning/1' -or $v.status -cne 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED' -or
    $v.helper_sha256 -cne '9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755' -or $v.runtime_root -cne $script:installRoot -or
    $v.install_receipt_sha256 -cne $script:installHash -or $v.config_sha256 -cne $script:configHash -or $v.source_manifest_sha256 -cne $script:runtimeManifestHash -or $v.revision_sha256 -cne $script:revisionHash -or
    $v.exactly_one_start_requested -ne $true -or $v.monitoring_started -ne $true -or $v.monitoring_scope -cne 'heartbeat_and_queue_only' -or
    $v.automatic_repair_enabled -ne $false -or $v.automatic_retry_allowed -ne $false -or $v.full_srs_acceptance -ne $false -or $v.task_name -cne 'CoChem-4.2.7-Warden' -or
    $v.task_instance_guid -cnotmatch '^\{?[a-fA-F0-9-]{36}\}?$'){throw 'Successful commissioning receipt binding differs.'}
 if($v.authentication_attempt -isnot [string] -or $v.authentication_attempt -cnotmatch '\A[a-f0-9]{32}\z' -or
    $v.authentication_attempt -ceq ('0'*32)){throw 'Commissioning authentication attempt proof differs.'}
 if($v.authentication_receipt_sha256 -isnot [string] -or $v.authentication_receipt_sha256 -cnotmatch '\A[a-f0-9]{64}\z' -or
    ($v.authenticated_profiles_verified -isnot [int] -and $v.authenticated_profiles_verified -isnot [long]) -or
    $v.authenticated_profiles_verified -ne 12){throw 'Commissioning authentication receipt proof differs.'}
 $c=$v.controller
 if($c.pid -isnot [long] -and $c.pid -isnot [int]){throw 'Controller PID must be an integer.'}
 if($c.creation_filetime -isnot [long] -or $c.creation_filetime -le 0 -or $c.pid -le 0 -or $c.pid -gt 4294967295 -or
    $c.instance_id -cnotmatch '^[a-f0-9]{32}$' -or $c.token_sid -cne 'S-1-5-18' -or $c.launcher_arguments_verified -ne $true -or $c.launcher.token_sid -cne 'S-1-5-18' -or
    $c.first_sequence -le 0 -or $c.final_sequence -le $c.first_sequence){throw 'Controller native commissioning proof differs.'}
 [pscustomobject]@{Sha256=$control.Sha256;Value=$v}
}

function Assert-CommissionedTaskRunning {
 param($Folder,$Commissioning)
 $task=Get-RegistrationTask $Folder 'CoChem-4.2.7-Warden'
 if($null -eq $task -or -not $task.Enabled -or $task.State -ne 4){throw 'Commissioned Warden is not currently running.'}
 $instances=$task.GetInstances(0)
 if($instances.Count -ne 1 -or $instances.Item(1).InstanceGuid -cne $Commissioning.Value.task_instance_guid){throw 'Warden instance differs; a reboot/restart needs new attestation and a new observation window.'}
 $d=$task.Definition;$a=$d.Actions.Item(1)
 $args='-I -B -m cochem_pipeline daemon --config "C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\pipeline.json" --queue-launch-output "C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1"'
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or $d.Actions.Count -ne 1 -or
    $a.Type -ne 0 -or $a.Path -cne (Join-Path $script:installRoot '.venv\Scripts\python.exe') -or $a.Arguments -cne $args -or $a.WorkingDirectory -cne $script:installRoot){throw 'Commissioned Warden task action differs.'}
 Assert-RegisteredTaskAcl ($task.GetSecurityDescriptor(7))
}

function Get-ObserverArguments {
 param([string]$CommissioningHash)
 '-I -S -B -X "pycache_prefix='+$script:cacheRoot+'" "'+(Join-Path $script:packageRoot 'observe_resources.py')+'" --run-observer --source-manifest-sha256 '+$script:sourceManifestHash+' --commissioning-sha256 '+$CommissioningHash
}

function Assert-ObserverTask {
 param($Task,[string]$Arguments,[bool]$Running=$false,[string]$InstanceGuid='')
 $d=$Task.Definition
 if($d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
    $d.Settings.AllowDemandStart -ne $true -or $d.Settings.MultipleInstances -ne 2 -or $d.Settings.RestartCount -ne 0 -or $d.Settings.ExecutionTimeLimit -cne 'PT49H' -or
    $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Independent observer task definition differs.'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $script:python -or $a.Arguments -cne $Arguments -or $a.WorkingDirectory -cne $script:packageRoot){throw 'Independent observer task action differs.'}
 Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7))
 if($Running){
  $instances=$Task.GetInstances(0)
  if($Task.Enabled -ne $true -or $d.Settings.Enabled -ne $true -or $Task.State -ne 4 -or $instances.Count -ne 1 -or $instances.Item(1).InstanceGuid -cne $InstanceGuid){throw 'Observer task is not the exact single running instance.'}
 }elseif($Task.Enabled -ne $false -or $d.Settings.Enabled -ne $false -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 267011){throw 'Observer task is not disabled and never run.'}
}

function New-ObserverPrivateDirectory {
 param([string]$Path)
 Assert-ObserverAbsent $Path;Assert-ProtectedPath (Split-Path -Parent $Path)
 Initialize-SeriesDirectory;[CoChemNativeLoginSeriesDirectory]::Create($Path)
 Assert-SeriesPrivateRoot $Path
}

function Assert-ObserverStateUnstarted {
 Assert-SeriesPrivateRoot $script:stateRoot;Assert-SeriesPrivateRoot $script:cacheRoot
 $items=@(Get-ChildItem -LiteralPath $script:stateRoot -Force)
 if($items.Count -ne 1 -or $items[0].FullName -cne $script:cacheRoot -or -not $items[0].PSIsContainer -or @(Get-ChildItem -LiteralPath $script:cacheRoot -Force).Count){throw 'Preserve existing observer state; it is not the exact empty pre-start namespace.'}
 Assert-ObserverAbsent (Join-Path $script:targetRoot 'start-intent.json')
 Assert-ObserverAbsent (Join-Path $script:targetRoot 'observer-started.json')
 Assert-ObserverAbsent (Join-Path $script:targetRoot 'observer-completed.json')
 Assert-ObserverAbsent (Join-Path $script:targetRoot 'observer-start-failure.json')
 Assert-ObserverAbsent (Join-Path $script:targetRoot 'observer-bootstrap-failure.json')
}

function Assert-ObserverInstalledSources {
 param($Records)
 $count=Assert-CodeTreeOnce $script:packageRoot
 foreach($record in $Records){$script:held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))}
 $null=Read-R3Control (Join-Path $script:targetRoot 'dependencies.json') $script:dependencyHash 2097152
 $actual=@(Get-ChildItem -LiteralPath $script:packageRoot -Recurse -Force -File)
 if($actual.Count -ne $Records.Count){throw 'Unexpected observer package file.'}
 return $count
}

function Invoke-ObserverRegistration {
 param($Scheduler,$Folder,$Runtime,$Commissioning,$Records,$Dependencies)
 Assert-ObserverAbsent $script:targetRoot;Assert-ObserverAbsent $script:stateRoot
 if($null -ne (Get-RegistrationTask $Folder $script:taskName)){throw 'Preserve existing observer task.'}
 Assert-CommissionedTaskRunning $Folder $Commissioning
 New-ProtectedDirectory $script:targetRoot;New-ProtectedDirectory $script:packageRoot
 New-ProtectedDirectory (Join-Path $script:packageRoot 'cochem_supervisor')
 New-ObserverPrivateDirectory $script:stateRoot;New-ObserverPrivateDirectory $script:cacheRoot
 foreach($record in $Records){Copy-VerifiedPayload $record}
 $dependencyLength=(Get-Item -LiteralPath $script:dependencyManifest -Force).Length
 Copy-VerifiedPayload ([pscustomobject]@{source=$script:dependencyManifest;destination=(Join-Path $script:targetRoot 'dependencies.json');sha256=$script:dependencyHash;length=$dependencyLength})
 $null=Assert-ObserverInstalledSources $Records
 $intent=[ordered]@{schema='cochem-resource-observer-registration-intent/1';source_manifest_sha256=$script:sourceManifestHash;dependency_manifest_sha256=$script:dependencyHash;
  commissioning_sha256=$Commissioning.Sha256;task_name=$script:taskName;controller_pid=$Commissioning.Value.controller.pid;controller_creation_filetime=$Commissioning.Value.controller.creation_filetime;
  runtime_root=$script:installRoot;full_srs_acceptance=$false;automatic_restart_or_resume=$false}
 $intentHash=Write-RegistrationControl (Join-Path $script:targetRoot 'registration-intent.json') ($intent|ConvertTo-Json -Depth 5)
 if($null -ne (Get-RegistrationTask $Folder $script:taskName)){throw 'Observer task appeared during staging; preserve everything.'}
 Assert-CommissionedTaskRunning $Folder $Commissioning
 $definition=$Scheduler.NewTask(0);$definition.RegistrationInfo.Description='Independent read-only resource observation; disabled until explicit single start. No restart or recovery. Heap unavailable.'
 $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
 $definition.Settings.Enabled=$false;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.RestartCount=0;$definition.Settings.ExecutionTimeLimit='PT49H'
 $args=Get-ObserverArguments $Commissioning.Sha256;$action=$definition.Actions.Create(0);$action.Path=$script:python;$action.Arguments=$args;$action.WorkingDirectory=$script:packageRoot
 $null=$Folder.RegisterTaskDefinition($script:taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
 $task=Get-RegistrationTask $Folder $script:taskName;Assert-ObserverTask $task $args
 $xmlHash=Write-RegistrationControl (Join-Path $script:targetRoot 'observer-task.xml') ([string]$task.Xml)
 $receipt=[ordered]@{schema='cochem-resource-observer-registration/1';status='RESOURCE_OBSERVER_REGISTERED_DISABLED';task_name=$script:taskName;
  source_manifest_sha256=$script:sourceManifestHash;dependency_manifest_sha256=$script:dependencyHash;commissioning_sha256=$Commissioning.Sha256;
  runtime_root=$script:installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;revision_sha256=$Runtime.revision.source_sha256;configuration_sha256=$Runtime.configuration_sha256;
  task_xml_sha256=$xmlHash;intent_sha256=$intentHash;dependencies=$Dependencies;task_started=$false;full_srs_acceptance=$false;automatic_restart_or_resume=$false}
 $pin=Write-RegistrationControl (Join-Path $script:targetRoot 'observer-registration.json') ($receipt|ConvertTo-Json -Depth 7)
 [pscustomobject]@{Value=$receipt;Sha256=$pin}
}

function Read-ObserverRegistration {
 param($Folder,$Commissioning,$Records)
 $control=Read-R3Control (Join-Path $script:targetRoot 'observer-registration.json') '' 65536;$v=(Read-R3Text $control)|ConvertFrom-Json
 if($v.schema -cne 'cochem-resource-observer-registration/1' -or $v.status -cne 'RESOURCE_OBSERVER_REGISTERED_DISABLED' -or $v.task_name -cne $script:taskName -or
    $v.source_manifest_sha256 -cne $script:sourceManifestHash -or $v.dependency_manifest_sha256 -cne $script:dependencyHash -or $v.commissioning_sha256 -cne $Commissioning.Sha256 -or
    $v.runtime_root -cne $script:installRoot -or $v.install_receipt_sha256 -cne $script:installHash -or $v.revision_sha256 -cne $script:revisionHash -or $v.configuration_sha256 -cne $script:configHash -or
    $v.task_started -ne $false -or $v.full_srs_acceptance -ne $false -or $v.automatic_restart_or_resume -ne $false -or $v.dependencies.complete_custody_deferred -ne $false){throw 'Stopped observer registration binding differs.'}
 $null=Read-R3Control (Join-Path $script:targetRoot 'registration-intent.json') $v.intent_sha256 65536
 $xml=Read-R3Control (Join-Path $script:targetRoot 'observer-task.xml') $v.task_xml_sha256 65536
 $task=Get-RegistrationTask $Folder $script:taskName;Assert-ObserverTask $task (Get-ObserverArguments $Commissioning.Sha256)
 if([string]$task.Xml -cne (Read-R3Text $xml)){throw 'Stopped observer task XML changed.'}
 $null=Assert-ObserverInstalledSources $Records;Assert-ObserverStateUnstarted
 [pscustomobject]@{Value=$v;Sha256=$control.Sha256}
}

function Read-ObserverFirstLine {
 param([string]$Path)
 $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
 if($item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Observer evidence is not an ordinary file.'}
 Assert-NoReparseAncestors $Path
 $acl=Get-Acl -LiteralPath $Path;$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544') -or $rules.Count -eq 0){throw 'Observer evidence owner differs.'}
 $system=$false
 foreach($r in $rules){if($r.IdentityReference.Value -cne 'S-1-5-18' -and $r.IdentityReference.Value -cne 'S-1-5-32-544'){throw 'Observer evidence is not private.'};if($r.AccessControlType -ne 'Allow' -or [long]$r.FileSystemRights -ne 2032127 -or ($r.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)){throw 'Observer evidence grant differs.'};if($r.IdentityReference.Value -ceq 'S-1-5-18'){$system=$true}}
 if(-not $system){throw 'Observer evidence lacks SYSTEM access.'}
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
 try{
  [CoChemStagedFileIdentity]::Check($stream,$Path)
  $bytes=[Collections.Generic.List[byte]]::new()
  while($bytes.Count -le 65536){$value=$stream.ReadByte();if($value -eq -1){return $null};$bytes.Add([byte]$value);if($value -eq 10){break}}
  if($bytes.Count -gt 65536){throw 'Observer sample line exceeds bound.'}
  $raw=$bytes.ToArray();$text=[Text.UTF8Encoding]::new($false,$true).GetString($raw);$parsed=$text|ConvertFrom-Json
  $sha=[Security.Cryptography.SHA256]::Create();try{$pin=[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
  [pscustomobject]@{Value=$parsed;Sha256=$pin}
 }finally{$stream.Dispose()}
}

function Assert-ObserverInitialSamples {
 param($Native,$Performance,$Commissioning,[double]$Now)
 $n=$Native.Value;$p=$Performance.Value;$c=$Commissioning.Value.controller
 foreach($number in @($Now,$n.timestamp,$p.timestamp,$p.rss_mb,$p.cpu_percent,$p.elapsed_seconds)){
  if(($number -isnot [double] -and $number -isnot [int] -and $number -isnot [long] -and $number -isnot [decimal]) -or [double]::IsNaN([double]$number) -or [double]::IsInfinity([double]$number)){throw 'Observer measurement must be a finite numeric value.'}
 }
 foreach($integer in @($n.pid,$n.handles,$n.heartbeat_sequence,$p.pid,$p.handles,$p.heartbeat_sequence)){
  if($integer -isnot [int] -and $integer -isnot [long]){throw 'Observer identity/count must be an integer.'}
 }
 if($n.schema -cne 'cochem-resource-native-boundary/1' -or $n.pid -ne $c.pid -or $n.creation_filetime -isnot [long] -or $n.creation_filetime -ne $c.creation_filetime -or
    $n.instance_id -cne $c.instance_id -or $n.heartbeat_sequence -lt $c.final_sequence -or $n.handles -le 0 -or $n.timestamp -gt $Now -or $Now-$n.timestamp -gt 60 -or
    $p.pid -ne $c.pid -or $p.instance_id -cne $c.instance_id -or $p.heartbeat_sequence -lt $c.final_sequence -or $p.timestamp -gt $Now -or $Now-$p.timestamp -gt 60 -or
    $p.handles -le 0 -or $p.rss_mb -le 0 -or $p.cpu_percent -lt 0 -or $p.elapsed_seconds -le 0 -or $p.desktop_heap.available -ne $false -or $p.desktop_heap.passed -ne $false){throw 'Initial observer measurements do not bind the commissioned native instance.'}
}

function Invoke-ObserverStart {
 param($Folder,$Commissioning,$Registration)
 Assert-ObserverStateUnstarted;Assert-CommissionedTaskRunning $Folder $Commissioning
 $args=Get-ObserverArguments $Commissioning.Sha256;$task=Get-RegistrationTask $Folder $script:taskName;Assert-ObserverTask $task $args
 $intent=[ordered]@{schema='cochem-resource-observer-start-intent/1';task_name=$script:taskName;registration_sha256=$Registration.Sha256;commissioning_sha256=$Commissioning.Sha256;
  controller_pid=$Commissioning.Value.controller.pid;controller_creation_filetime=$Commissioning.Value.controller.creation_filetime;automatic_restart_or_resume=$false;started_after_utc=[DateTime]::UtcNow.ToString('o')}
 $intentHash=Write-RegistrationControl (Join-Path $script:targetRoot 'start-intent.json') ($intent|ConvertTo-Json -Depth 5)
 # The durable CreateNew intent precedes the only Enable/Run. No catch retries,
 # no Stop, task deletion or namespace cleanup on timeout or ambiguity.
 $task.Enabled=$true;$instance=$task.Run($null);$guid=$instance.InstanceGuid
 if($guid -cnotmatch '^\{?[a-fA-F0-9-]{36}\}?$'){throw 'Observer start returned no exact task instance; preserve running state.'}
 $deadline=[DateTime]::UtcNow.AddSeconds(360);$last='NO_INITIAL_SAMPLE'
 while([DateTime]::UtcNow -lt $deadline){
  $task=Get-RegistrationTask $Folder $script:taskName;Assert-ObserverTask $task $args $true $guid
  Assert-CommissionedTaskRunning $Folder $Commissioning
  try{
   $native=Read-ObserverFirstLine (Join-Path $script:stateRoot 'samples\native-boundaries.jsonl')
   $sample=Read-ObserverFirstLine (Join-Path $script:stateRoot 'samples\native-samples.jsonl')
   if($null -ne $native -and $null -ne $sample){
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    Assert-ObserverInitialSamples $native $sample $Commissioning $now
    Assert-ObserverTask (Get-RegistrationTask $Folder $script:taskName) $args $true $guid
    $report=[ordered]@{schema='cochem-resource-observer-started/1';status='RESOURCE_OBSERVER_RUNNING_INITIAL_MEASUREMENTS_VERIFIED';task_name=$script:taskName;task_instance_guid=$guid;
     registration_sha256=$Registration.Sha256;start_intent_sha256=$intentHash;commissioning_sha256=$Commissioning.Sha256;source_manifest_sha256=$script:sourceManifestHash;
     dependency_manifest_sha256=$script:dependencyHash;runtime_root=$script:installRoot;configuration_sha256=$script:configHash;revision_sha256=$script:revisionHash;
     controller_pid=$Commissioning.Value.controller.pid;controller_creation_filetime=$Commissioning.Value.controller.creation_filetime;controller_instance_id=$Commissioning.Value.controller.instance_id;
     first_native_line_sha256=$native.Sha256;first_resource_line_sha256=$sample.Sha256;initial_rss_mb=$sample.Value.rss_mb;initial_cpu_percent=$sample.Value.cpu_percent;
     intended_window_seconds=172800;continuous_48h_complete=$false;desktop_heap_available=$false;recovery_timing_available=$false;full_srs_acceptance=$false;automatic_restart_or_resume=$false;owner_supervision_required=$false}
    $pin=Write-RegistrationControl (Join-Path $script:targetRoot 'observer-started.json') ($report|ConvertTo-Json -Depth 7)
    return [ordered]@{schema='cochem-resource-observer-result/1';status=$report.status;receipt_path=(Join-Path $script:targetRoot 'observer-started.json');receipt_sha256=$pin;
      task_started=$true;continuous_48h_complete=$false;full_srs_acceptance=$false;automatic_restart_or_resume=$false}
   }
  }catch [Management.Automation.ItemNotFoundException]{$last='INITIAL_SAMPLE_NOT_YET_PUBLISHED'}
  Start-Sleep -Milliseconds 250
 }
 throw ('Observer initial evidence deadline; preserve task and all output. '+$last)
}

function Write-ObserverStartFailure {
 param($Folder,$Commissioning,$Registration)
 $value=[ordered]@{schema='cochem-resource-observer-start-failure/1';status='RESOURCE_OBSERVER_START_HELD';task_name=$script:taskName;
  commissioning_sha256=$Commissioning.Sha256;registration_sha256=$Registration.Sha256;source_manifest_sha256=$script:sourceManifestHash;
  full_srs_acceptance=$false;automatic_restart_or_resume=$false;potentially_running_state_preserved=$true;task_state_observed=$false}
 try{
  $task=Get-RegistrationTask $Folder $script:taskName
  if($null -ne $task){$value.task_state_observed=$true;$value.task_state=[int]$task.State;$value.last_task_result=[long]$task.LastTaskResult;$value.running_instances=[int]$task.GetInstances(0).Count}
 }catch{}
 try{
  $control=Read-R3Control (Join-Path $script:targetRoot 'observer-bootstrap-failure.json') '' 65536
  $failure=(Read-R3Text $control)|ConvertFrom-Json
  if($failure.schema -cne 'cochem-external-resource-observer-bootstrap-failure/1' -or $failure.full_srs_acceptance -ne $false -or $failure.automatic_restart_or_resume -ne $false -or
     $failure.phase -cnotin @('entry_custody','source_manifest','initial_dependencies','sterile_imports','resource_observation','final_dependencies','completion_projection') -or
     $failure.error_type -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$' -or ($null -ne $failure.code -and $failure.code -cnotmatch '^[A-Z][A-Z0-9_]{0,79}$') -or
     ($null -ne $failure.winerror -and (($failure.winerror -isnot [int] -and $failure.winerror -isnot [long]) -or $failure.winerror -lt 0 -or $failure.winerror -gt 4294967295))){throw 'Invalid bootstrap failure projection.'}
  $value.bootstrap_receipt_sha256=$control.Sha256;$value.failure=[ordered]@{phase=$failure.phase;error_type=$failure.error_type;code=$failure.code;winerror=$failure.winerror}
 }catch{$value.bootstrap_failure_metadata_available=$false}
 $path=Join-Path $script:targetRoot 'observer-start-failure.json';$pin=Write-RegistrationControl $path ($value|ConvertTo-Json -Depth 7)
 [ordered]@{schema='cochem-resource-observer-result/1';status=$value.status;receipt_path=$path;receipt_sha256=$pin;full_srs_acceptance=$false;
  potentially_running_state_preserved=$true;automatic_restart_or_resume=$false;task_state_observed=$value.task_state_observed}
}

function Invoke-ObserverStartWithReceipt {
 param($Folder,$Commissioning,$Registration)
 try{Invoke-ObserverStart $Folder $Commissioning $Registration}
 catch{
  try{$failure=Write-ObserverStartFailure $Folder $Commissioning $Registration;Write-Host ($failure|ConvertTo-Json -Depth 6 -Compress)}catch{}
  throw 'Observer start is held. Preserve the task, original intent and all evidence; no retry or cleanup.'
 }
}

if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the elevated owner; no observer action occurred.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$python=Join-Path $basePythonRoot 'python.exe'
$psutilRoot=Join-Path $installRoot '.venv\Lib\site-packages\psutil';$targetRoot='C:\Program Files\CoChem\ResourceObservation4.2.7-windows-20261008-r3-v3'
$packageRoot=Join-Path $targetRoot 'package';$stateRoot='C:\Program Files\CoChem\ResourceObservationState4.2.7-windows-20261008-r3-v3';$cacheRoot=Join-Path $stateRoot 'empty-cache'
$taskName='CoChem-4.2.7-ResourceObservation-20261008-r3-v3';$commissioning='C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json'
$sourceRoot=Join-Path $PSScriptRoot 'resource-observer-r3-v3';$sourceManifest=Join-Path $sourceRoot 'source-manifest.json';$sourceManifestHash='f44a356bd0bdd4d27aff35e3f3d204509b608f3b72e31a39698003803681ee59'
$dependencyManifest=Join-Path $PSScriptRoot 'resource-observer-r3-v2-dependencies.json';$dependencyHash='ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429'
$installHash='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$runtimeManifestHash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';$revisionHash='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
 foreach($text in Import-ObserverFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')){. ([scriptblock]::Create($text))}
 foreach($text in Import-ObserverFunctions (Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1') 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198' @('Get-RegistrationTask','Assert-RegisteredTaskAcl','Write-RegistrationControl')){. ([scriptblock]::Create($text))}
 foreach($text in Import-ObserverFunctions (Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1') '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings')){. ([scriptblock]::Create($text))}
 foreach($text in Import-ObserverFunctions (Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1') '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d' @('Assert-CodeTreeOnce')){. ([scriptblock]::Create($text))}
 foreach($text in Import-ObserverFunctions (Join-Path $PSScriptRoot 'login-six-workers-status-first-r3-v1.ps1') '9e742d4df0b7faa07a6073dc4bc7f15ccf2924df9a14443a5c662bfbc7ff0361' @('Initialize-SeriesDirectory','Assert-SeriesPrivateRoot')){. ([scriptblock]::Create($text))}
 Initialize-FileIdentity
 $runtime=Assert-R3InstalledBindings;$records=Get-ObserverInventory;$dependencies=Assert-ObserverDependencies
 $holds=@();$proof=$null;$scheduler=$null;$folder=$null
 try{$proof=Read-ObserverCommissioning}catch [Management.Automation.ItemNotFoundException]{$holds+='Successful first-instance commissioning receipt is required.'}
 try{$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\');if($null -ne $proof){Assert-CommissionedTaskRunning $folder $proof}}catch{$holds+='Current exact commissioned task state is inaccessible or mismatched.'}
 if($Operation -eq 'StartRegistered'){
  if($null -ne $proof -and $null -ne $folder){try{$null=Read-ObserverRegistration $folder $proof $records}catch{$holds+='Exact disabled observer registration/private empty state not verified.'}}
 }else{
  foreach($path in @($targetRoot,$stateRoot)){try{Assert-ObserverAbsent $path}catch{$holds+='Observer namespace not proved absent: '+$path}}
  if($null -ne $folder){try{if($null -ne (Get-RegistrationTask $folder $taskName)){$holds+='Observer task already exists; preserve it.'}}catch{$holds+='Exact observer task absence is not proved.'}}
 }
 if(-not $Apply){[ordered]@{schema='cochem-resource-observer-install-plan/1';operation=$Operation;administrator=$admin;holds=$holds;source_manifest_sha256=$sourceManifestHash;dependency_manifest_sha256=$dependencyHash;
  code_root=$targetRoot;private_state_root=$stateRoot;task_name=$taskName;commissioning_present=($null -ne $proof);full_dependency_custody_deferred=$true;
  initial_registration_disabled=$true;optional_single_start=($Operation -ne 'Register');duration_seconds=172800;task_limit='PT49H';no_restart_or_resume=$true;
  source_files=7;base_noncache_files=2777;psutil_files=11;historical_bytecode_untouched_and_redirected=$true;full_srs_acceptance=$false;desktop_heap_available=$false}|ConvertTo-Json -Depth 6;return}
 if($holds.Count){throw 'Observer prerequisites remain held; no operation was performed.'}
 $dependencies=Assert-ObserverDependencies -CompleteCustody
 $fresh=Assert-R3InstalledBindings;if($fresh.install_receipt_sha256 -cne $runtime.install_receipt_sha256){throw 'Runtime changed during dependency custody checks.'}
 $again=Read-ObserverCommissioning;if($again.Sha256 -cne $proof.Sha256){throw 'Commissioning receipt changed during dependency custody checks.'}
 if($Operation -eq 'StartRegistered'){$registered=Read-ObserverRegistration $folder $proof $records}else{$registered=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies}
 if($Operation -ne 'Register'){Invoke-ObserverStartWithReceipt $folder $proof $registered|ConvertTo-Json -Depth 7}else{[ordered]@{schema='cochem-resource-observer-result/1';status=$registered.Value.status;receipt_path=(Join-Path $targetRoot 'observer-registration.json');receipt_sha256=$registered.Sha256;task_started=$false;full_srs_acceptance=$false}|ConvertTo-Json -Depth 6}
}finally{foreach($stream in $held){$stream.Dispose()}}
