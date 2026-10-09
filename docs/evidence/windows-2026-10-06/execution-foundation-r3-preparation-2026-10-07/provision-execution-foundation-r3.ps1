#Requires -Version 5.1
<# DRAFT. Default preview only. Reviewed -Apply creates exactly one fresh helper
root/task/receipt, seven scoped RAM directories, six scoped Defender exclusions
only when missing, one fresh RAM ledger and one empty Docker registry. SQLite
may create transient WAL/SHM files in that new directory. No containers, pools,
jobs, provider auth, existing DB, R volume/root ACL or startup task changes. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-PinnedFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed support changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        $tokens=$null;$errors=$null;try{$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)}finally{$reader.Dispose()}
        if($errors.Count){throw 'Reviewed support parse error.'}
        $found=@();foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
        if(@($Names|Where-Object{$_ -notin $found}).Count){throw 'Reviewed definition missing.'}
    }finally{$stream.Dispose()}
}


function Get-FoundationPacket {
    param($Runtime)
    $packet=Get-EvidencePacket $Runtime
    $control=Read-R3Control (Join-Path $preflightRoot 'execution-prerequisites.json') '' 131072
    $value=(Read-R3Text $control)|ConvertFrom-Json
    if($value.schema -cne 'cochem-execution-prerequisites/1' -or $value.status -cne 'READ_ONLY_PREREQUISITES_VERIFIED' -or
       $value.system_sid -cne 'S-1-5-18' -or $value.helper_sha256 -cne $preflightHash -or
       $value.runtime_root -cne $installRoot -or $value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or
       $value.nonce -cnotmatch '^[a-f0-9]{32}$' -or $value.packet_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
       $value.activation_ready -ne $false -or @($value.holds).Count -ne 0 -or
       $value.existing_databases_modified -ne $false -or $value.existing_files_or_acls_modified -ne $false -or
       $value.existing_tasks_modified_or_run -ne $false -or $value.native_provider_authentication_performed -ne $false -or
       $value.native_model_jobs_executed -ne 0 -or $value.knowledge_service_constructed -ne $false -or
       $value.docker_runner_constructed -ne $false -or $value.ram_ensure_called -ne $false -or
       $value.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or $value.revision.verified -ne $true -or
       $value.docker.ownership_census.empty -ne $true -or $value.docker.ownership_census.union_count -ne 0 -or
       $value.knowledge.current_bytes_unchanged -ne $true -or $value.knowledge.r2_to_r3_only_resource_limits_delta_verified -ne $true){
        throw 'Actual complete read-only prerequisite success is required.'}
    if(@($value.worker_receipt_sha256.PSObject.Properties).Count -ne 6){throw 'Preflight worker count differs.'}
    foreach($slot in $packet.workers.Keys){if($value.worker_receipt_sha256.$slot -cne $packet.workers[$slot]){throw 'Preflight worker receipt changed.'}}
    $arguments='-I -B "'+$preflightRoot+'\inspect-execution-prerequisites-r3.py" --nonce '+$value.nonce+' --packet-sha256 '+$value.packet_sha256
    Assert-PassedTask (Get-TaskOrAbsent 'CoChem-4.2.7-ExecutionPrerequisites-20261007-r3') $python $arguments $preflightRoot
    $packet.schema='cochem-execution-foundation-inputs/1';$packet.preflight_receipt_sha256=$control.Sha256
    $packet
}

function Invoke-Foundation {
    param($Runtime,$Packet)
    Assert-Stopped
    $null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-FoundationPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed before creation.'}
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-TaskOrAbsent $taskName)){throw 'Fresh foundation namespace exists; preserve it.'}
    $null=Assert-CodeTreeOnce $installRoot
    $null=Assert-CodeTreeOnce (Split-Path -Parent $basePython)
    Assert-DockerNativeCustody
    New-ProtectedDirectory $root
    New-ProtectedDirectory (Join-Path $root 'docker-client-config')
    $records=@(
        [pscustomobject]@{source=$source;destination=(Join-Path $root 'provision-execution-foundation-r3.py');sha256=$sourceHash;length=(Get-Item -LiteralPath $source).Length}
        [pscustomobject]@{source=$preflight;destination=(Join-Path $root 'inspect-execution-prerequisites-r3.py');sha256=$preflightHash;length=(Get-Item -LiteralPath $preflight).Length}
        [pscustomobject]@{source=$support;destination=(Join-Path $root 'worker-denial-acceptance-r3.py');sha256=$supportHash;length=(Get-Item -LiteralPath $support).Length}
    )
    foreach($record in $records){Copy-VerifiedPayload $record;$held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))}
    $packetPath=Join-Path $root 'inputs.json';$packetHash=Write-NewPacket $packetPath $Packet
    $held.Add((Open-VerifiedFile $packetPath $packetHash (Get-Item -LiteralPath $packetPath).Length))
    Assert-Stopped
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Fresh scoped adopted-R directories/Defender exclusions/ledger and empty Docker registry only. No R volume, startup task, existing DB, budget, provider auth, jobs, containers, pool or activation. Preserve partial outputs; no retry.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT8M'
    $action=$definition.Actions.Create(0);$action.Path=$python
    $action.Arguments='-I -B "'+(Join-Path $root 'provision-execution-foundation-r3.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
    $action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-Inspection $instance
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Foundation task terminal state unknown; preserve evidence.'}
    $control=Read-R3Control (Join-Path $root 'execution-foundation.json') '' 131072
    $receipt=(Read-R3Text $control)|ConvertFrom-Json
    if($receipt.schema -cne 'cochem-execution-foundation/1' -or $receipt.nonce -cne $nonce -or $receipt.system_sid -cne 'S-1-5-18' -or
       $receipt.helper_sha256 -cne $sourceHash -or $receipt.packet_sha256 -cne $packetHash -or $receipt.runtime_root -cne $installRoot){throw 'Foundation receipt binding differs.'}
    $summary=[ordered]@{schema='cochem-execution-foundation-task-result/1';status=$receipt.status;receipt_path=$control.Stream.Name;receipt_sha256=$control.Sha256;
        last_task_result=$task.LastTaskResult;runtime_root=$installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;activation_ready=$false;
        automatic_retry_allowed=$false;partial_outputs_preserved=$true;ram_provision_started=$receipt.ram_provision_started;registry_provision_started=$receipt.registry_provision_started}
    if($null -ne $receipt.PSObject.Properties['failure']){
        $f=$receipt.failure
        if($f.phase -cnotmatch '^[a-z_]{1,64}$' -or $f.error_type -cnotmatch '^[A-Za-z0-9_]{1,80}$' -or ($null -ne $f.winerror -and $f.winerror -isnot [int] -and $f.winerror -isnot [long])){throw 'Unsafe failure metadata refused.'}
        $summary.failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;winerror=$f.winerror}
    }
    if($receipt.status -cne 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED' -or $task.LastTaskResult -ne 0){Write-Host ($summary|ConvertTo-Json -Depth 6);throw 'Foundation held; preserve partial state and do not retry or reset.'}
    if($receipt.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $receipt.preflight_receipt_sha256 -cne $Packet.preflight_receipt_sha256 -or
       $receipt.activation_ready -ne $false -or $receipt.automatic_retry_allowed -ne $false -or $receipt.ram_scoped_roots_verified -ne 6 -or
       $receipt.ram_ledger_created_new -ne $true -or $receipt.ram_volume_or_startup_task_modified -ne $false -or
       $receipt.legacy_databases_or_budgets_modified -ne $false -or $receipt.credentials_or_native_profiles_modified -ne $false -or
       $receipt.knowledge_service_constructed_or_refreshed -ne $false -or $receipt.general_readiness_called -ne $false -or
       $receipt.containers_created -ne 0 -or $receipt.warm_pool_created -ne 0 -or $receipt.native_model_jobs_executed -ne 0 -or
       $receipt.registry.registry_created_new -ne $true -or $receipt.registry.capacity -ne 4 -or $receipt.registry.work_rows -ne 0 -or
       $receipt.registry.unknown_owned -ne 0 -or $receipt.registry.integrity_check -cne 'ok'){throw 'Successful foundation claims differ.'}
    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-FoundationPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed after provisioning; preserve state.'}
    $summary.ram_scoped_roots_verified=6;$summary.empty_registry_capacity=4
    $summary|ConvertTo-Json -Depth 6
}
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$root='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3';$taskName='CoChem-4.2.7-ExecutionFoundation-20261007-r3'
$preflightRoot='C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3'
$preflight=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.py';$preflightHash='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
$preflightWrapper=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.ps1';$preflightWrapperHash='5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887'
foreach($definition in @(Import-PinnedFunctions $preflightWrapper $preflightWrapperHash @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Get-EvidencePacket','Write-NewPacket','Assert-WindowsKernel32','Assert-DockerNativeCustody','Wait-Inspection'))){. ([scriptblock]::Create($definition))}
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'provision-execution-foundation-r3.py';$sourceHash='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'
$support=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$knowledgeHash='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$copyHash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'
foreach($definition in @(Import-PinnedFunctions $copyHelper $copyHash @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$leaf=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1';$leafHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $pins=@([pscustomobject]@{path=$source;sha=$sourceHash},[pscustomobject]@{path=$preflight;sha=$preflightHash},[pscustomobject]@{path=$preflightWrapper;sha=$preflightWrapperHash},[pscustomobject]@{path=$support;sha=$supportHash},[pscustomobject]@{path=$leaf;sha=$leafHash})
    foreach($pin in $pins){$held.Add((Open-VerifiedFile $pin.path $pin.sha (Get-Item -LiteralPath $pin.path).Length))}
    foreach($definition in @(Import-PinnedFunctions $leaf $leafHash @('Read-R3Control','Read-R3Text','Assert-CodeTreeOnce','Assert-OriginalFailedDenialTask','Assert-OriginalFailedDenial','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($definition))}
    $null=Read-R3Control $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' 1048576
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-Stopped
    $holds=@()
    if(Test-Path -LiteralPath $root){$holds+='Fresh foundation root already exists; preserve it.'}
    if($null -ne (Get-TaskOrAbsent $taskName)){$holds+='Fresh foundation task already exists; preserve it.'}
    $runtime=$null
    if(Test-Path -LiteralPath (Join-Path $installRoot 'install-after.json')){$runtime=Assert-R3InstalledBindings}else{$holds+='Reviewed r3 runtime is not installed.'}
    Assert-DockerNativeCustody
    foreach($n in 1..6){if(-not (Test-Path -LiteralPath "C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-slot$n\worker-denial-acceptance.json")){$holds+="Successful r3 slot$n receipt is not present."}}

    if(-not (Test-Path -LiteralPath (Join-Path $preflightRoot 'execution-prerequisites.json'))){$holds+='Successful read-only prerequisite receipt is not present.'}
    if(-not $Apply){[ordered]@{schema='cochem-execution-foundation-plan/1';mode='READ_ONLY_PLAN';helper_sha256=$sourceHash;runtime=$runtime;target_root=$root;task_name=$taskName;
        all_prior_success_receipt_and_task_checks_deferred=$true;system_fresh_state_revalidation_deferred=$true;provisioning_executed=$false;
        proposed_creates=@('fresh helper root, isolated Docker CLI config, packet, SYSTEM task and receipt','R:\CoChem427-windows-20261007 plus exactly six slot roots','fresh private ramdisk-state.json','fresh private containers directory and empty containers.db with transient WAL/SHM');
        scoped_changes=@('ACL and no-content-index flags on seven new RAM directories','only missing six Defender exclusions');
        ram_volume_or_startup_task_changes=0;existing_database_changes=0;containers_created=0;activation_ready=$false;automatic_retry_allowed=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $packet=Get-FoundationPacket $runtime
    Invoke-Foundation $runtime $packet
}finally{foreach($stream in $held){$stream.Dispose()}}
