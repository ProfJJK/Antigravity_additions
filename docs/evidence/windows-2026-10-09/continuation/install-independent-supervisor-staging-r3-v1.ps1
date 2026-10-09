#Requires -Version 5.1
<# DRAFT: fresh unpublished supervisor source/venv/tests and paired uncertainty holds.
   No live Warden/task/config/pointer/account changes; no pipeline provisioning.
   Default preview. Apply requires a separately frozen, reviewed manifest. #>
[CmdletBinding()]
param([string]$Manifest,[ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestSha256,[switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-StagingFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create();try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed staging dependency changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
        $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
        if($errors.Count){throw 'Reviewed staging dependency did not parse.'}
        foreach($name in $Names){$nodes=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true));if($nodes.Count -ne 1){throw 'Reviewed function is missing or ambiguous.'};$nodes[0].Extent.Text}
    }finally{$stream.Dispose()}
}

function Assert-AbsentStagingPath {
    param([string]$Path)
    try{$null=Get-Item -LiteralPath $Path -Force -ErrorAction Stop}catch{
        if($_.Exception -is [Management.Automation.ItemNotFoundException]){return}
        throw 'Fresh staging path cannot be inspected; absence is unverified.'}
    throw 'Fresh staging destination already exists; preserve it and do not retry.'
}

function Get-StagingTaskOrAbsent {
    param($Folder,[string]$Name)
    try{return $Folder.GetTask($Name)}catch{
        $errorItem=$_.Exception
        while($null -ne $errorItem){if($errorItem.HResult -eq -2147024894){return $null};$errorItem=$errorItem.InnerException}
        throw 'Scheduled task metadata is unavailable.'}
}

function Get-PreservedDaemonDefinitions {
    param($Folder)
    $result=@{}
    foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        $task=Get-StagingTaskOrAbsent $Folder $name
        if($null -eq $task){$result[$name]='ABSENT';continue}
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$result[$name]=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::Unicode.GetBytes([string]$task.Xml))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    }
    return $result
}

function Assert-DaemonDefinitionsUnchanged {
    param($Before,$Folder)
    $after=Get-PreservedDaemonDefinitions $Folder
    foreach($name in $Before.Keys){if($after[$name] -cne $Before[$name]){throw 'Existing daemon definition changed during unpublished staging; preserve all outputs.'}}
}

function Get-StagingFiles {
    param($Inventory,$Prior)
    if($Inventory.schema -cne 'cochem-independent-supervisor-staging-freeze/1' -or $Inventory.complete_source_freeze -isnot [bool] -or $Inventory.complete_source_freeze -ne $true -or
       $Inventory.target_root -cne $script:targetRoot -or $Inventory.unpublished_pair_root -cne $script:pairRoot -or
       $Inventory.pipeline_config_sha256 -cne $script:configHash -or $Inventory.activation_authorized -isnot [bool] -or $Inventory.activation_authorized -ne $false){throw 'A complete reviewed unpublished staging freeze is required.'}
    $priorRows=@{};foreach($row in @($Prior.files)){$priorRows[[string]$row.relative]=$row}
    if($priorRows.Count -ne 166 -or @($Inventory.source_files).Count -ne 166){throw 'Independent source must retain the full reviewed r3 inventory.'}
    $changes=@{'src/cochem_supervisor/engine.py'='9ec21d808bfbb1c5dfe01a6dd41b0dc969f9fc92df4ff5de010c4890c1dddeea';'src/cochem_supervisor/replay.py'='d0a10b30e3f16d44a2906d855d46f44dc0fbad7249b8b43d6b67b98e4d94d60d';'src/cochem_pipeline/performance_acceptance.py'='8164ca69e6f31ad1e1912d1396df086b2590c103fd3c9a64d7a6031872c56e4c'}
    $total=[long]0
    foreach($kind in @('source_files','acceptance_files')){
        $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $rows=@($Inventory.$kind);if($rows.Count -lt 1 -or $rows.Count -gt 5000){throw 'Invalid staging inventory count.'}
        foreach($row in $rows){
            $relative=[string]$row.relative
            if((@($row.PSObject.Properties.Name|Sort-Object) -join ',') -cne 'bytes,relative,sha256' -or
               $row.relative -isnot [string] -or $row.sha256 -isnot [string] -or ($row.bytes -isnot [int] -and $row.bytes -isnot [long]) -or
               $relative.Contains('\') -or $relative -match '[\x00-\x1f<>:"|?*]' -or
               @($relative.Split('/')|Where-Object {$_ -in @('','.','..') -or $_.EndsWith('.') -or $_.EndsWith(' ') -or $_ -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)'}).Count -or -not $seen.Add($relative) -or
               [string]$row.sha256 -notmatch '^[a-f0-9]{64}$' -or [long]$row.bytes -lt 0 -or [long]$row.bytes -gt 16777216){throw 'Invalid or unsafe staging inventory row.'}
            $total += [long]$row.bytes;if($total -gt 268435456){throw 'Staging inventory exceeds 256 MiB.'}
            if($kind -eq 'source_files'){
                if(-not $priorRows.ContainsKey($relative)){throw 'Unreviewed source addition.'}
                $expected=if($changes.ContainsKey($relative)){$changes[$relative]}else{[string]$priorRows[$relative].sha256}
                if($row.sha256 -cne $expected){throw 'Source differs outside the three reviewed changes.'}
                $destination=Join-Path $script:sourceRoot $relative;$source=Join-Path $script:repo $relative
            }else{
                if($relative -notmatch '^(pipeline_tests|mcp_tests|supervisor_tests)/[^:]+\.(py|json|md|xml)$|^knowledge/[^:]+\.(md|json)$|^scripts/(verify_pipeline_acceptance\.py|parse_agy_status_capture\.py|install_aetherdesk_pawnio\.ps1)$|^pytest\.ini$'){throw 'Unreviewed protected acceptance path.'}
                $destination=Join-Path $script:acceptanceRoot $relative
                $source=if($relative -ceq 'pytest.ini'){Join-Path $script:workspaceRoot 'acceptance-pytest-staging-r3-v1.ini'}else{Join-Path $script:repo $relative}
            }
            [pscustomobject]@{source=$source;destination=$destination;relative=$relative;sha256=[string]$row.sha256;length=[long]$row.bytes}
        }
        if($kind -eq 'acceptance_files'){
            foreach($required in @('pytest.ini','pipeline_tests/windows_test_context.py','pipeline_tests/test_runtime_knowledge_authority.py','mcp_tests/test_server.py','supervisor_tests/test_held_observation.py','knowledge/v4.1.2_manifest.json')){if(-not $seen.Contains($required)){throw 'Protected test closure is incomplete.'}}
        }
    }
}

function Get-StagingControlFiles {
    param($Inventory,[string]$ManifestPath,[string]$ManifestHash)
    $controls=@($Inventory.control_files)
    $required=@('stage-independent-supervisor-holds-r3-v1.py','bootstrap-unresolved-budget-pair.py','unresolved-budget-evidence.json')
    if($controls.Count -ne 3 -or (@($controls.relative|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')){throw 'Staging controls are missing, duplicated or unexpected.'}
    foreach($row in $controls){
        if((@($row.PSObject.Properties.Name|Sort-Object) -join ',') -cne 'bytes,relative,sha256' -or $row.relative -isnot [string] -or $row.sha256 -isnot [string] -or ($row.bytes -isnot [int] -and $row.bytes -isnot [long]) -or [string]$row.sha256 -notmatch '^[a-f0-9]{64}$' -or [long]$row.bytes -lt 1 -or [long]$row.bytes -gt 1048576){throw 'Invalid staging control pin.'}
        if($row.relative -ceq 'bootstrap-unresolved-budget-pair.py' -and $row.sha256 -cne '1c431c12f5b502664494fb09bf6c0ef75bea9e6fad7739f8478b3591a24dcc11'){throw 'Immutable bootstrap changed.'}
        if($row.relative -ceq 'unresolved-budget-evidence.json' -and $row.sha256 -cne 'b7b822ff263720dfb443607f0e3cbf515d175b9b7784ea4fd15f8968b7ff3709'){throw 'Unresolved provenance changed.'}
        $name=if($row.relative -ceq 'unresolved-budget-evidence.json'){'unresolved-budget-evidence.proposed.json'}else{[string]$row.relative}
        [pscustomobject]@{source=(Join-Path $script:workspaceRoot $name);destination=(Join-Path $script:controlRoot $row.relative);relative=[string]$row.relative;sha256=[string]$row.sha256;length=[long]$row.bytes}
    }
    [pscustomobject]@{source=$ManifestPath;destination=(Join-Path $script:controlRoot 'staging-manifest.json');relative='staging-manifest.json';sha256=$ManifestHash;length=(Get-Item -LiteralPath $ManifestPath).Length}
}

function Get-FrozenSyncArguments {
    @('--no-config','--no-cache','sync','--project',$script:sourceRoot,'--frozen','--no-editable','--link-mode','copy','--extra','mcp','--extra','dev','--python',$script:python,'--no-python-downloads')
}

function Invoke-StagingTask {
    param($Folder,[string]$Nonce,[string]$ManifestHash)
    if($null -ne (Get-StagingTaskOrAbsent $Folder $script:taskName)){throw 'Preserve the existing staging task; no reuse.'}
    $definition=$script:scheduler.NewTask(0);$definition.RegistrationInfo.Description='One-shot unpublished supervisor staging; no daemon or provider activation.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.ExecutionTimeLimit='PT3M';$definition.Settings.MultipleInstances=2
    $action=$definition.Actions.Create(0);$action.Path=Join-Path $script:targetRoot '.venv\Scripts\python.exe'
    $action.Arguments='-I -B "'+(Join-Path $script:controlRoot 'stage-independent-supervisor-holds-r3-v1.py')+'" --execute --nonce '+$Nonce+' --manifest-sha256 '+$ManifestHash
    $action.WorkingDirectory=$script:controlRoot
    $task=$Folder.RegisterTaskDefinition($script:taskName,$definition,2,'SYSTEM',$null,5,'D:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-KnowledgeTask $instance -TimeoutSeconds 190
    $task=$Folder.GetTask($script:taskName);Assert-CompletedKnowledgeTask $task
    if($task.Definition.Actions.Count -ne 1 -or $task.Definition.Actions.Item(1).Path -cne $action.Path -or $task.Definition.Actions.Item(1).Arguments -cne $action.Arguments -or $task.Definition.Actions.Item(1).WorkingDirectory -cne $script:controlRoot -or $task.Definition.Triggers.Count -ne 0 -or $task.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $task.Definition.Principal.LogonType -ne 5 -or $task.Definition.Principal.RunLevel -ne 1 -or $task.Definition.Settings.ExecutionTimeLimit -cne 'PT3M' -or $task.Definition.Settings.MultipleInstances -ne 2){throw 'Staging task binding changed.'}
    return $task
}

function Read-StagingResult {
    param([string]$Nonce,[string]$ManifestHash,[string]$HelperHash,$Task)
    $path=Join-Path $script:controlRoot 'staging-receipt.json';Assert-ProtectedPath $path
    $info=Get-Item -LiteralPath $path;if($info.Length -lt 1 -or $info.Length -gt 65536){throw 'Staging receipt is missing or oversized.'}
    $hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $stream=Open-VerifiedFile $path $hash $info.Length
    try{$value=(Read-HeldText $stream)|ConvertFrom-Json}finally{$stream.Dispose()}
    foreach($key in @('activation_ready','paid_repair_enabled','component_recovery_enabled','old_ledgers_opened','active_warden_changed','pipeline_configuration_changed','partial_outputs_preserved','pair_creation_started')){if($value.$key -isnot [bool]){throw 'Staging preservation flags must be JSON booleans.'}}
    if($value.provider_or_model_calls -isnot [int]){throw 'Staging call count must be a JSON integer.'}
    if($value.schema -cne 'cochem-independent-supervisor-staging/1' -or $value.nonce -cne $Nonce -or $value.root -cne $script:targetRoot -or $value.helper_sha256 -cne $HelperHash -or $value.manifest_sha256 -cne $ManifestHash -or $value.activation_ready -ne $false -or $value.paid_repair_enabled -ne $false -or $value.component_recovery_enabled -ne $false -or $value.old_ledgers_opened -ne $false -or $value.provider_or_model_calls -ne 0 -or $value.active_warden_changed -ne $false -or $value.pipeline_configuration_changed -ne $false -or $value.partial_outputs_preserved -ne $true){throw 'Staging receipt binding or preservation claim differs.'}
    $result=[ordered]@{schema='cochem-independent-supervisor-staging-task-result/1';status=[string]$value.status;receipt_path=$path;receipt_sha256=$hash;task_result=$Task.LastTaskResult;activation_ready=$false;partial_outputs_preserved=$true}
    if($value.status -ceq 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'){
        if($Task.LastTaskResult -ne 0 -or $value.phase -cne 'complete' -or $value.pair_creation_started -ne $true -or $value.revision.verified -isnot [bool] -or $value.revision.verified -ne $true -or $value.revision.files -isnot [int] -or $value.revision.files -ne 109 -or [string]$value.paired_receipt_sha256 -notmatch '^[a-f0-9]{64}$' -or $value.paired_evidence_sha256 -cne '1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177' -or (@($value.ledger_sha256.PSObject.Properties.Name|Sort-Object) -join ',') -cne 'component-recovery.db,supervisor.db' -or @($value.ledger_sha256.PSObject.Properties|Where-Object {$_.Value -isnot [string] -or $_.Value -notmatch '^[a-f0-9]{64}$'}).Count){throw 'Successful staging is not bound to a verified new held pair.'}
        $result.paired_receipt_sha256=$value.paired_receipt_sha256
    }elseif($value.status -ceq 'STAGING_HELD'){
        if($Task.LastTaskResult -ne 2 -or $value.failure.phase -notin @('runtime','fresh_private_boundary','paired_hold_creation','post_verification') -or [string]$value.failure.error_type -notmatch '^[A-Za-z][A-Za-z0-9_]{0,79}$' -or ($null -ne $value.failure.winerror -and ($value.failure.winerror -isnot [int] -or $value.failure.winerror -lt 0 -or $value.failure.winerror -gt 65535))){throw 'Failed staging metadata is not recognized.'}
        $result.failure=[ordered]@{phase=$value.failure.phase;error_type=$value.failure.error_type;winerror=$value.failure.winerror}
    }else{throw 'Unrecognized staging result.'}
    return $result
}

function Invoke-IndependentStaging {
    param([object[]]$Files,[object[]]$Controls,$Folder,$Before,[string]$ManifestHash)
    $mutex=[Threading.Mutex]::new($false,'Global\CoChem427-IndependentSupervisorStaging-v1');$locked=$false
    try{
        try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
        if(-not $locked){throw 'Another staging invocation owns this namespace.'}
        Assert-AbsentStagingPath $script:targetRoot
        if($null -ne (Get-StagingTaskOrAbsent $Folder $script:taskName)){throw 'Preserve existing staging task.'}
        Assert-DaemonDefinitionsUnchanged $Before $Folder
        $null=Assert-CodeTreeOnce (Split-Path -Parent $script:python);Assert-ProtectedPath $script:uv
        New-ProtectedDirectory $script:targetRoot
        $directories=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach($file in @($Files)+@($Controls)){$parent=Split-Path -Parent $file.destination;while($parent -cne $script:targetRoot){$null=$directories.Add($parent);$parent=Split-Path -Parent $parent}}
        foreach($directory in @($directories|Sort-Object Length)){New-ProtectedDirectory $directory}
        foreach($file in @($Files)+@($Controls)){Copy-VerifiedPayload $file}
        New-ProtectedDirectory (Join-Path $script:targetRoot 'build-temp')
        Invoke-FrozenRuntimeBuild;Protect-NewRuntimeTree $script:targetRoot
        $null=Assert-CodeTreeOnce $script:targetRoot
        Assert-PostBuildFrozenFiles $Files;Assert-PostBuildFrozenFiles $Controls
        $null=Assert-NewRuntimeInterpreter $script:targetRoot
        # Private state is created only after code protection, so it never
        # receives the public-read code ACL during the completed build walk.
        New-PrivateDirectory $script:privateRoot -Root
        Assert-DaemonDefinitionsUnchanged $Before $Folder
        $nonce=[Guid]::NewGuid().ToString('N')
        $task=Invoke-StagingTask $Folder $nonce $ManifestHash
        Assert-DaemonDefinitionsUnchanged $Before $Folder
        $helper=@($Controls|Where-Object {$_.relative -ceq 'stage-independent-supervisor-holds-r3-v1.py'})[0]
        $result=Read-StagingResult $nonce $ManifestHash $helper.sha256 $task
        $result|ConvertTo-Json -Depth 6
        if($result.status -cne 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'){throw 'Staging held; bound failure metadata was printed. Preserve all new outputs/task; do not retry.'}
    }finally{if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
}

# Invocation boundary; inert tests extract these functions without running it.
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw 'Apply requires the owner elevated; default preview does not.'}
$repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions';$programFiles='C:\Program Files';$workspaceRoot=$PSScriptRoot
$targetRoot='C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1'
$sourceRoot=Join-Path $targetRoot 'source';$acceptanceRoot=Join-Path $targetRoot 'acceptance';$controlRoot=Join-Path $targetRoot 'controls'
$privateRoot=Join-Path $targetRoot 'unpublished-private';$pairRoot=Join-Path $privateRoot 'unresolved-pair'
$taskName='CoChem-4.2.7-StageIndependentSupervisor-20261007-v1'
$pipelineRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$python='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$uv='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe'
$dependencies=@(
    [pscustomobject]@{path=(Join-Path $repo 'scripts\stage_aetherdesk_427_payloads.ps1');hash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b';names=@('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Copy-VerifiedPayload')}
    [pscustomobject]@{path=(Join-Path $PSScriptRoot 'install-stopped-runtime-r3.ps1');hash='372a0fe352124e81a2d097ebe7c075369f6f20275a8a2858f78f41febbc723e4';names=@('Read-HeldText','Assert-UvVenvText','Assert-NewRuntimeInterpreter','Assert-PostBuildFrozenFiles','Protect-NewRuntimeTree','Invoke-FrozenRuntimeBuild')}
    [pscustomobject]@{path=(Join-Path $PSScriptRoot 'install-private-knowledge-candidate.ps1');hash='5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a';names=@('New-PrivateAcl','Assert-PrivateItem','New-PrivateDirectory','Assert-CodeTreeOnce','Wait-KnowledgeTask','Assert-CompletedKnowledgeTask')}
)
$held=[Collections.Generic.List[IO.Stream]]::new()
try{
    foreach($dependency in $dependencies){foreach($definition in @(Import-StagingFunctions $dependency.path $dependency.hash $dependency.names)){. ([scriptblock]::Create($definition))}}
    Initialize-FileIdentity
    foreach($dependency in $dependencies){$held.Add((Open-VerifiedFile $dependency.path $dependency.hash (Get-Item -LiteralPath $dependency.path).Length))}
    foreach($pin in @(
        [pscustomobject]@{path=$python;hash='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'},
        [pscustomobject]@{path=$uv;hash='2019cdf564cb8f749262f5f021cedc75a99abb1c6081227ca340bbcda972611d'},
        [pscustomobject]@{path=(Join-Path $pipelineRoot 'pipeline.json');hash=$configHash},
        [pscustomobject]@{path=(Join-Path $pipelineRoot 'source-manifest.json');hash='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'})){
        $held.Add((Open-VerifiedFile $pin.path $pin.hash (Get-Item -LiteralPath $pin.path).Length))}
    Assert-AbsentStagingPath $targetRoot
    $scheduler=$null;$folder=$null;$before=$null
    if($admin){
        $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
        if($null -ne (Get-StagingTaskOrAbsent $folder $taskName)){throw 'Staging task already exists; no reuse.'}
        $before=Get-PreservedDaemonDefinitions $folder
    }
    $holds=@();$files=@();$controls=@()
    if(-not $Manifest -or -not $ManifestSha256){$holds+='Complete reviewed independent source/test/control freeze is required.'}else{
        $inventory=Read-Inventory $Manifest $ManifestSha256
        $prior=Read-Inventory (Join-Path $pipelineRoot 'source-manifest.json') '6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1'
        $files=@(Get-StagingFiles $inventory $prior);$controls=@(Get-StagingControlFiles $inventory $Manifest $ManifestSha256)
        foreach($file in @($files)+@($controls)){$held.Add((Open-VerifiedFile $file.source $file.sha256 $file.length))}
    }
    $plan=[ordered]@{schema='cochem-independent-supervisor-staging-plan/2';mode='READ_ONLY_PLAN';target_root=$targetRoot;unpublished_pair_root=$pairRoot;manifest_sha256=$ManifestSha256;source_and_test_files=$files.Count;controls=$controls.Count;activation_ready=$false;daemon_and_task_metadata_checks_deferred_to_admin=(-not $admin);daemon_definitions_observed=$admin;warden_stop_required=$false;new_one_shot_task_on_apply=$taskName;accounts_changed=0;credentials_read=$false;old_ledgers_opened=$false;holds=$holds}
    if(-not $Apply){$plan|ConvertTo-Json -Depth 5;return}
    if($holds.Count){throw ($holds -join ' ')}
    Invoke-IndependentStaging $files $controls $folder $before $ManifestSha256
}finally{foreach($stream in $held){$stream.Dispose()}}
