#Requires -Version 5.1
<# Fresh registration only. Default is a read-only plan. -Apply copies the
exact installed r3 configuration into a new protected root and creates one
DISABLED SYSTEM task. It never starts Python, tasks, providers, provisioning,
knowledge, databases, RAM, migrations or independent repair. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Get-ReviewedRegistrationFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed registration support changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed registration support has parse errors.'}
        foreach($name in $Names){
            $nodes=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object{$_.Name -ceq $name})
            if($nodes.Count -ne 1){throw 'Expected one exact reviewed function.'}
            $nodes[0].Extent.Text
        }
    }finally{$stream.Dispose()}
}

function Get-RegistrationTask {
    param($Folder,[string]$Name)
    try{return $Folder.GetTask($Name)}catch{
        $errorItem=$_.Exception
        while($null -ne $errorItem){
            if($errorItem.HResult -eq -2147024894){return $null}
            $errorItem=$errorItem.InnerException
        }
        throw 'Exact scheduled-task state is inaccessible or unknown; registration is held.'
    }
}

function Assert-RegistrationTasks {
    param($Folder)
    if($null -ne (Get-RegistrationTask $Folder $script:taskName)){throw 'Preserve existing Warden task; this registration has no overwrite or resume mode.'}
    foreach($name in @('CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        $task=Get-RegistrationTask $Folder $name
        if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){
            throw 'Managed modern daemon task is not stopped and disabled.'
        }
    }
    # CoChemHostWarden_V412 is a separate legacy Hyper-V service and is neither
    # stopped, reconfigured nor claimed as a replacement for this new task.
}

function Assert-FreshActivationRoot {
    try{$null=Get-Item -LiteralPath $script:targetRoot -Force -ErrorAction Stop}
    catch [System.Management.Automation.ItemNotFoundException]{return}
    catch {throw 'Activation-root state is inaccessible or unknown.'}
    throw 'Preserve existing activation root; do not rerun or clean a partial registration.'
}

function Assert-RegistrationConfiguration {
    param([string]$Text)
    $value=$Text|ConvertFrom-Json
    if($value.max_execution_slots -ne 4 -or $value.docker.max_containers -ne 4 -or $value.docker.warm_pool_size -ne 2 -or
       @($value.workers.PSObject.Properties.Name).Count -ne 6 -or @($value.slot_roots.PSObject.Properties.Name).Count -ne 6 -or
       $value.routing.policy_version -ne 2 -or $value.ramdisk.mount_root -cne 'R:\' -or
       $value.ramdisk.size_mb -ne 8192 -or $value.ramdisk.workspace_subdirectory -cne 'CoChem427-windows-20261007' -or
       $value.private_root -cne 'C:\ProgramData\CoChemPipeline427\private'){
        throw 'Pinned configuration capacity, routes, identities or preserved RAM/state paths differ.'
    }
    foreach($number in 1..6){
        $slot='slot'+$number
        if($value.workers.$slot.name -cne ('CoChem422Worker'+$number) -or
           $value.workers.$slot.credential_target -cne ('CoChem422/'+$slot) -or
           $value.slot_roots.$slot -cne ('C:\ProgramData\CoChemPipeline427\workers\'+$slot)){
            throw 'Pinned isolated identity mapping differs.'
        }
    }
}

function Assert-RegisteredTaskAcl {
    param([string]$Sddl)
    $sd=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    if($sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or
       -not ($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or
       $null -eq $sd.DiscretionaryAcl -or $sd.DiscretionaryAcl.Count -ne 2){throw 'Registered task ownership/private DACL differs.'}
    $seen=[Collections.Generic.HashSet[string]]::new()
    foreach($ace in $sd.DiscretionaryAcl){
        if($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed -or $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None -or
           $ace.AccessMask -ne 2032127 -or $ace.SecurityIdentifier.Value -notin @('S-1-5-18','S-1-5-32-544') -or
           -not $seen.Add($ace.SecurityIdentifier.Value)){throw 'Registered task ACL grants differ.'}
    }
}

function Assert-RegisteredStoppedTask {
    param($Task)
    $definition=$Task.Definition
    if($Task.Enabled -ne $false -or $Task.State -ne 1 -or $Task.GetInstances(0).Count -ne 0 -or
       $definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $definition.Principal.LogonType -ne 5 -or $definition.Principal.RunLevel -ne 1 -or
       $definition.Settings.Enabled -ne $false -or $definition.Settings.AllowDemandStart -ne $true -or
       $definition.Settings.MultipleInstances -ne 2 -or $definition.Settings.RestartCount -ne 0 -or
       $definition.Settings.ExecutionTimeLimit -cne 'PT0S' -or $definition.Triggers.Count -ne 0 -or $definition.Actions.Count -ne 1){
        throw 'New Warden task is not the exact disabled, idle, trigger-free SYSTEM definition.'
    }
    $action=$definition.Actions.Item(1)
    if($action.Type -ne 0 -or $action.Path -cne $script:python -or
       $action.Arguments -cne ('-I -B -m cochem_pipeline daemon --config "'+$script:configTarget+'"') -or
       $action.WorkingDirectory -cne $script:installRoot){throw 'Registered daemon action differs from pinned r3/configuration.'}
    Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7))
}

function Write-RegistrationControl {
    param([string]$Path,[string]$Text)
    Assert-ProtectedPath (Split-Path -Parent $Path)
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-CodeAcl $false))
    try{$raw=[Text.UTF8Encoding]::new($false).GetBytes($Text);$stream.Write($raw,0,$raw.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    Assert-ProtectedPath $Path
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-StoppedWardenRegistration {
    param($Scheduler,$Folder,$Runtime)
    Assert-RegistrationTasks $Folder
    Assert-FreshActivationRoot
    $runtimeEntries=Assert-CodeTreeOnce $script:installRoot
    $baseEntries=Assert-CodeTreeOnce $script:basePythonRoot
    # The full tree scan checks structural custody; the reviewed installed
    # binding function rechecks every exact source/package asset and controls.
    $fresh=Assert-R3InstalledBindings
    if($fresh.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $fresh.configuration_sha256 -cne $script:configHash){throw 'Runtime binding changed during custody inspection.'}
    Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $script:installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
    $null=Read-R3Control (Join-Path $script:basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
    $configControl=Read-R3Control $script:config $script:configHash
    Assert-RegistrationConfiguration (Read-R3Text $configControl)
    Assert-RegistrationTasks $Folder
    Assert-FreshActivationRoot
    New-ProtectedDirectory $script:targetRoot
    if(@(Get-ChildItem -LiteralPath $script:targetRoot -Force).Count){throw 'New activation root unexpectedly contains entries; preserve it.'}
    $record=[pscustomobject]@{source=$script:config;destination=$script:configTarget;sha256=$script:configHash;length=$configControl.Length}
    Copy-VerifiedPayload $record
    $script:held.Add((Open-VerifiedFile $script:configTarget $script:configHash $configControl.Length))
    $intent=[ordered]@{schema='cochem-stopped-warden-registration-intent/1';task_name=$script:taskName;runtime_root=$script:installRoot;config_sha256=$script:configHash;mode='REGISTER_DISABLED_ONLY';activation_ready=$false}
    $intentHash=Write-RegistrationControl (Join-Path $script:targetRoot 'registration-intent.json') ($intent|ConvertTo-Json -Depth 6)
    Assert-RegistrationTasks $Folder
    $definition=$Scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Protected r3 Warden registration only. Disabled; no boot trigger, restart or initial run. Separate reviewed commissioning required.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$false;$definition.Settings.AllowDemandStart=$true
    $definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT0S';$definition.Settings.RestartCount=0
    $action=$definition.Actions.Create(0);$action.Path=$script:python
    $action.Arguments='-I -B -m cochem_pipeline daemon --config "'+$script:configTarget+'"'
    $action.WorkingDirectory=$script:installRoot
    # TASK_CREATE=2 (never CREATE_OR_UPDATE/Force). Disabled at registration,
    # so there is no transient enabled task before a later Disable call.
    $null=$Folder.RegisterTaskDefinition($script:taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $task=Get-RegistrationTask $Folder $script:taskName
    if($null -eq $task){throw 'Registered task could not be read back; preserve the root and investigate.'}
    Assert-RegisteredStoppedTask $task
    $xmlHash=Write-RegistrationControl (Join-Path $script:targetRoot 'warden-task.xml') ([string]$task.Xml)
    $report=[ordered]@{schema='cochem-stopped-warden-registration/1';status='WARDEN_REGISTERED_DISABLED_NOT_STARTED';task_name=$script:taskName;target_root=$script:targetRoot;
        runtime_root=$script:installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;source_manifest_sha256=$Runtime.source_manifest_sha256;revision_sha256=$Runtime.revision.source_sha256;
        configuration_path=$script:configTarget;configuration_sha256=$script:configHash;task_xml_sha256=$xmlHash;intent_sha256=$intentHash;runtime_entries=$runtimeEntries;base_python_entries=$baseEntries;
        identities=6;shared_slots=4;disabled=$true;task_state=$task.State;running_instances=0;last_task_result=$task.LastTaskResult;task_started=$false;triggers=0;restart_count=0;
        accounts_provisioned=0;credentials_modified=$false;databases_opened=$false;knowledge_rebuilt=$false;ram_modified=$false;legacy_tasks_changed=$false;supervisor_installed=$false;
        native_authentication_verified=$false;activation_ready=$false;automatic_repair_enabled=$false}
    $reportHash=Write-RegistrationControl (Join-Path $script:targetRoot 'stopped-registration.json') ($report|ConvertTo-Json -Depth 8)
    Assert-RegisteredStoppedTask (Get-RegistrationTask $Folder $script:taskName)
    [ordered]@{schema='cochem-stopped-warden-registration-result/1';status=$report.status;receipt_path=(Join-Path $script:targetRoot 'stopped-registration.json');receipt_sha256=$reportHash;
        task_name=$script:taskName;disabled=$true;task_started=$false;activation_ready=$false;runtime_root=$script:installRoot;configuration_sha256=$script:configHash}
}

# Invocation boundary: test fixtures load these exact functions without running
# this host/Apply block; all scheduler mutations are simulated in those fixtures.
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner elevated; no registration occurred.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$config=Join-Path $installRoot 'pipeline.json'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$targetRoot='C:\Program Files\CoChem\WardenActivation4.2.7-windows-20261007-r3'
$configTarget=Join-Path $targetRoot 'pipeline.json'
$taskName='CoChem-4.2.7-Warden'
$support='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
foreach($text in Get-ReviewedRegistrationFunctions $support '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')){. ([scriptblock]::Create($text))}
$runtimeSupport=Join-Path $PSScriptRoot 'check-worker-native-status-r3.ps1'
foreach($text in Get-ReviewedRegistrationFunctions $runtimeSupport '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Read-R3Control','Read-R3Text','Assert-R3InstalledBindings','Assert-VenvBinding','Assert-CodeTreeOnce')){. ([scriptblock]::Create($text))}
Initialize-FileIdentity
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $runtime=Assert-R3InstalledBindings
    Assert-RegistrationConfiguration (Read-R3Text (Read-R3Control $config $configHash))
    Assert-VenvBinding (Read-R3Text (Read-R3Control (Join-Path $installRoot '.venv\pyvenv.cfg') '0c2b1a15dcdfe67436882fcf0f8d567d79442bcac41f3b744153c17c21df727d'))
    $null=Read-R3Control (Join-Path $basePythonRoot 'python.exe') 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'
    $holds=@();$folder=$null;$scheduler=$null
    try{Assert-FreshActivationRoot}catch{$holds+='activation_root_not_proved_fresh'}
    try{$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-RegistrationTasks $folder}catch{$holds+='exact_task_state_not_proved_missing_or_stopped'}
    if(-not $Apply){
        [ordered]@{schema='cochem-stopped-warden-registration-plan/1';mode='READ_ONLY_PLAN';runtime_root=$installRoot;install_receipt_sha256=$runtime.install_receipt_sha256;
            source_manifest_sha256=$runtime.source_manifest_sha256;configuration_sha256=$configHash;target_root=$targetRoot;task_name=$taskName;identities=6;shared_slots=4;
            administrator=$admin;holds=$holds;full_tree_custody_deferred_until_apply=$true;register_disabled=$true;demand_start_allowed_but_task_disabled=$true;
            triggers=0;restart_count=0;python_executed=$false;tasks_registered=0;activation_ready=$false}|ConvertTo-Json -Depth 6
        return
    }
    if($holds.Count){throw 'Registration prerequisites remain held. Preserve all current roots/tasks; no automatic retry.'}
    Invoke-StoppedWardenRegistration $scheduler $folder $runtime|ConvertTo-Json -Depth 8
}finally{foreach($stream in $held){$stream.Dispose()}}
