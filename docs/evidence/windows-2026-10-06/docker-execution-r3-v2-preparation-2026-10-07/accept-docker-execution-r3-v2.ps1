#Requires -Version 5.1
<# DRAFT, no Apply issued. Fresh physical Docker boundary trial after accepted
foundation. Two owned single-use warm containers, fixed RED/GREEN sources only.
No provider/model/Chapter06 workflow, generic readiness, legacy DB/budget work.
Finish zero live containers; preserve all records and any unverified quarantine. #>
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



function Get-AcceptancePacket {
    param($Runtime)
    $packet=Get-ContinuationPacket $Runtime
    $control=Read-R3Control (Join-Path $foundationRoot 'execution-foundation.json') '' 131072
    $value=(Read-R3Text $control)|ConvertFrom-Json
    if($value.schema -cne 'cochem-execution-foundation/2' -or $value.status -cne 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED' -or
       $value.system_sid -cne 'S-1-5-18' -or $value.helper_sha256 -cne $foundationHash -or $value.runtime_root -cne $installRoot -or
       $value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $value.preflight_receipt_sha256 -cne $packet.preflight_receipt_sha256 -or
       $value.revision.verified -ne $true -or $value.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or
       $value.nonce -cnotmatch '^[a-f0-9]{32}$' -or $value.packet_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
       $value.activation_ready -ne $false -or $value.automatic_retry_allowed -ne $false -or $value.ram_scoped_roots_verified -ne 6 -or
       $value.ram_ledger_created_new -ne $true -or $value.ram_volume_or_startup_task_modified -ne $false -or
       $value.legacy_databases_or_budgets_modified -ne $false -or $value.credentials_or_native_profiles_modified -ne $false -or
       $value.knowledge_service_constructed_or_refreshed -ne $false -or $value.general_readiness_called -ne $false -or
       $value.containers_created -ne 0 -or $value.warm_pool_created -ne 0 -or $value.native_model_jobs_executed -ne 0 -or
       $value.registry.registry_created_new -ne $true -or $value.registry.capacity -ne 4 -or $value.registry.work_rows -ne 0 -or
       $value.registry.unknown_owned -ne 0 -or $value.registry.integrity_check -cne 'ok' -or
       $value.registry.database_sha256 -cnotmatch '^[a-f0-9]{64}$' -or $value.ram_ledger_sha256 -cnotmatch '^[a-f0-9]{64}$'){
        throw 'Actual successful scoped foundation is required.'}
    foreach($binding in @('diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')){
        if($value.$binding -cne $packet[$binding]){throw 'Foundation continuation authority differs.'}
    }
    if($value.original_failure_cause_established -isnot [bool] -or $value.original_failure_cause_established -or
       $null -ne $value.PSObject.Properties['failure'] -or @($value.worker_receipt_sha256.PSObject.Properties).Count -ne 6){throw 'Foundation preservation proof differs.'}
    foreach($slot in $packet.workers.Keys){if($value.worker_receipt_sha256.$slot -cne $packet.workers[$slot]){throw 'Foundation worker binding differs.'}}
    $arguments='-I -B "'+$foundationRoot+'\provision-execution-foundation-r3-v2.py" --nonce '+$value.nonce+' --packet-sha256 '+$value.packet_sha256
    Assert-PassedTask (Get-TaskOrAbsent 'CoChem-4.2.7-ExecutionFoundation-20261007-r3-v2') $python $arguments $foundationRoot
    $original=(Read-R3Text (Read-R3Control (Join-Path $foundationRoot 'inputs.json') $value.packet_sha256 32768))|ConvertFrom-Json
    foreach($binding in @('schema','install_receipt_sha256','knowledge_receipt_sha256','preflight_receipt_sha256','diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')){
        if($original.$binding -cne $packet[$binding]){throw 'Foundation input packet changed.'}
    }
    foreach($slot in $packet.workers.Keys){if($original.workers.$slot -cne $packet.workers[$slot]){throw 'Foundation input worker binding differs.'}}
    $packet.schema='cochem-docker-physical-inputs/2';$packet.foundation_receipt_sha256=$control.Sha256
    $packet
}
function Invoke-Acceptance {
    param($Runtime,$Packet)
    Assert-Stopped
    $null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-AcceptancePacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed before creation.'}
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-TaskOrAbsent $taskName)){throw 'Fresh acceptance namespace exists; preserve it.'}
    $null=Assert-CodeTreeOnce $installRoot
    $null=Assert-CodeTreeOnce (Split-Path -Parent $basePython)
    Assert-DockerNativeCustody
    New-ProtectedDirectory $root
    New-ProtectedDirectory (Join-Path $root 'docker-client-config')
    $records=@(
        [pscustomobject]@{source=$source;destination=(Join-Path $root 'accept-docker-execution-r3-v2.py');sha256=$sourceHash;length=(Get-Item -LiteralPath $source).Length}
        [pscustomobject]@{source=$foundation;destination=(Join-Path $root 'provision-execution-foundation-r3-v2.py');sha256=$foundationHash;length=(Get-Item -LiteralPath $foundation).Length}
        [pscustomobject]@{source=$preflight;destination=(Join-Path $root 'inspect-execution-prerequisites-r3.py');sha256=$preflightHash;length=(Get-Item -LiteralPath $preflight).Length}
        [pscustomobject]@{source=$support;destination=(Join-Path $root 'worker-denial-acceptance-r3.py');sha256=$supportHash;length=(Get-Item -LiteralPath $support).Length}
        [pscustomobject]@{source=$diagnostic;destination=(Join-Path $root 'diagnose-foundation-docker-r3-v1.py');sha256=$diagnosticHash;length=(Get-Item -LiteralPath $diagnostic).Length}
    )
    foreach($record in $records){Copy-VerifiedPayload $record;$held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))}
    $packetPath=Join-Path $root 'inputs.json';$packetHash=Write-NewPacket $packetPath $Packet
    $held.Add((Open-VerifiedFile $packetPath $packetHash (Get-Item -LiteralPath $packetPath).Length))
    Assert-Stopped
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Two single-use owned Docker containers and fixed RED/GREEN boundary fixtures only, under unchanged shared cap4. Existing empty registry gains retained test history. No model/provider auth/budget/knowledge/bootstrap. Finish zero live containers or retain quarantine. Not a Chapter06 coding workflow; no retry.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT10M'
    $action=$definition.Actions.Create(0);$action.Path=$python
    $action.Arguments='-I -B "'+(Join-Path $root 'accept-docker-execution-r3-v2.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
    $action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-Inspection $instance -Seconds 610
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Acceptance task terminal state unknown; preserve evidence.'}

    $control=Read-R3Control (Join-Path $root 'docker-physical-acceptance.json') '' 131072
    $receipt=(Read-R3Text $control)|ConvertFrom-Json
    if($receipt.schema -cne 'cochem-docker-physical-acceptance/2' -or $receipt.nonce -cne $nonce -or $receipt.system_sid -cne 'S-1-5-18' -or
       $receipt.helper_sha256 -cne $sourceHash -or $receipt.packet_sha256 -cne $packetHash -or $receipt.runtime_root -cne $installRoot){throw 'Acceptance receipt binding differs.'}
    $summary=[ordered]@{schema='cochem-docker-physical-task-result/2';status=$receipt.status;receipt_path=$control.Stream.Name;receipt_sha256=$control.Sha256;
        last_task_result=$task.LastTaskResult;runtime_root=$installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;activation_ready=$false;
        automatic_retry_allowed=$false;chapter06_coding_workflow_tested=$false;physical_boundary_verified=$receipt.physical_boundary_verified;
        cleanup_verified=$receipt.cleanup_verified;prepared_count=$receipt.prepared_count;container_creation_started=$receipt.container_creation_started;
        ram_fixture_creation_started=$receipt.ram_fixture_creation_started;foundation_registry_mutation_started=$receipt.foundation_registry_mutation_started}
    if($null -ne $receipt.PSObject.Properties['failure']){
        $f=$receipt.failure
        if($f.phase -cnotmatch '^[a-z_]{1,64}$' -or $f.error_type -cnotmatch '^[A-Za-z0-9_]{1,80}$' -or ($null -ne $f.winerror -and $f.winerror -isnot [int] -and $f.winerror -isnot [long])){throw 'Unsafe failure metadata refused.'}
        $summary.failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;winerror=$f.winerror}
        $summary.failure.chain=ConvertTo-SafeDiagnosticChain $f.chain
    }
    if($null -ne $receipt.PSObject.Properties['startup_sla_met']){$summary.startup_sla_met=$receipt.startup_sla_met}
    if($receipt.status -cne 'DOCKER_PHYSICAL_ACCEPTANCE_VERIFIED' -or $task.LastTaskResult -ne 0){Write-Host ($summary|ConvertTo-Json -Depth 6);throw 'Physical acceptance held; preserve state and do not repeat or clear quarantine.'}
    if($receipt.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $receipt.foundation_receipt_sha256 -cne $Packet.foundation_receipt_sha256 -or
       $receipt.activation_ready -ne $false -or $receipt.automatic_retry_allowed -ne $false -or $receipt.chapter06_coding_workflow_tested -ne $false -or
       $receipt.native_model_jobs_executed -ne 0 -or $receipt.ram_volume_or_startup_task_modified -ne $false -or
       $receipt.legacy_databases_or_budgets_modified -ne $false -or $receipt.credentials_or_native_profiles_modified -ne $false -or
       $receipt.knowledge_service_constructed_or_refreshed -ne $false -or $receipt.ram_ensure_called -ne $false -or
       $receipt.prepared_count -ne 2 -or $receipt.cleanup_verified -ne $true -or $receipt.physical_boundary_verified -ne $true -or
       $receipt.startup_sla_met -ne $true -or $receipt.final_physical_census_empty -ne $true -or $receipt.shared_capacity -ne 4 -or
       $receipt.native_occupancy -ne 0 -or @($receipt.executions).Count -ne 2 -or $receipt.registry_owner_preserved -ne $true -or
       $receipt.ram_ledger_preserved -ne $true -or $receipt.all_six_worker_identities_excluded -ne $true){throw 'Successful physical acceptance claims differ.'}
    foreach($binding in @('diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')){
        if($receipt.$binding -cne $Packet[$binding]){throw 'Physical acceptance continuity evidence differs.'}
    }
    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-AcceptancePacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed; preserve state.'}
    $summary|ConvertTo-Json -Depth 6
}
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$root='C:\Program Files\CoChem\DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2';$taskName='CoChem-4.2.7-DockerPhysical-20261007-r3-v2'
$foundationRoot='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2'
$foundation=Join-Path $PSScriptRoot 'provision-execution-foundation-r3-v2.py';$foundationHash='620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c'
$foundationWrapper=Join-Path $PSScriptRoot 'provision-execution-foundation-r3-v2.ps1';$foundationWrapperHash='0df813342fa8070d1f2f6041ccbc24a7d8ec0d5c94baa269eec02c243e043ca0'
foreach($definition in @(Import-PinnedFunctions $foundationWrapper $foundationWrapperHash @('Assert-VerifiedDiagnosticReceipt','Get-ContinuationPacket'))){. ([scriptblock]::Create($definition))}
$originalFoundationWrapper=Join-Path $PSScriptRoot 'provision-execution-foundation-r3.ps1';$originalFoundationWrapperHash='402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c'
foreach($definition in @(Import-PinnedFunctions $originalFoundationWrapper $originalFoundationWrapperHash @('Get-FoundationPacket'))){. ([scriptblock]::Create($definition))}
$failedRoot='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3'
$failedReceiptHash='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
$failedPacketHash='a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'
$failedHelperHash='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'
$actualPreflightHash='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
$diagnosticRoot='C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1'
$diagnosticReceiptHash='cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9'
$diagnosticPacketHash='8744b6a0b569b820404734a491dd7b1671ab2a439ccc27f7251dccbf75a7550d'
$diagnostic=Join-Path $PSScriptRoot 'diagnose-foundation-docker-r3-v1.py';$diagnosticHash='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'
$diagnosticWrapper=Join-Path $PSScriptRoot 'diagnose-foundation-docker-r3-v1.ps1';$diagnosticWrapperHash='06522b04378d8d0b6cb915102e7fce1125700d96f68159c13e9d26a5c1c52e0b'
foreach($definition in @(Import-PinnedFunctions $diagnosticWrapper $diagnosticWrapperHash @('Assert-HeldFoundationTask','Assert-HeldFoundationReceipt','Get-DiagnosticPacket','ConvertTo-SafeDiagnosticChain','ConvertTo-SafeDockerTrace'))){. ([scriptblock]::Create($definition))}
$preflightRoot='C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3'
$preflight=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.py';$preflightHash='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
$preflightWrapper=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.ps1';$preflightWrapperHash='5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887'
foreach($definition in @(Import-PinnedFunctions $preflightWrapper $preflightWrapperHash @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Get-EvidencePacket','Write-NewPacket','Assert-WindowsKernel32','Assert-DockerNativeCustody','Wait-Inspection'))){. ([scriptblock]::Create($definition))}
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'accept-docker-execution-r3-v2.py';$sourceHash='157549c5806e5486085d0d941ca619c5218e668653309ce6bd55dc75eb4a944f'
$support=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$knowledgeHash='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$copyHash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'
foreach($definition in @(Import-PinnedFunctions $copyHelper $copyHash @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$leaf=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1';$leafHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $pins=@([pscustomobject]@{path=$source;sha=$sourceHash},[pscustomobject]@{path=$foundation;sha=$foundationHash},[pscustomobject]@{path=$foundationWrapper;sha=$foundationWrapperHash},[pscustomobject]@{path=$preflight;sha=$preflightHash},[pscustomobject]@{path=$preflightWrapper;sha=$preflightWrapperHash},[pscustomobject]@{path=$support;sha=$supportHash},[pscustomobject]@{path=$leaf;sha=$leafHash})
    $pins+=@([pscustomobject]@{path=$originalFoundationWrapper;sha=$originalFoundationWrapperHash},[pscustomobject]@{path=$diagnosticWrapper;sha=$diagnosticWrapperHash},[pscustomobject]@{path=$diagnostic;sha=$diagnosticHash})
    foreach($pin in $pins){$held.Add((Open-VerifiedFile $pin.path $pin.sha (Get-Item -LiteralPath $pin.path).Length))}
    foreach($definition in @(Import-PinnedFunctions $leaf $leafHash @('Read-R3Control','Read-R3Text','Assert-CodeTreeOnce','Assert-OriginalFailedDenialTask','Assert-OriginalFailedDenial','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($definition))}
    $null=Read-R3Control $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' 1048576
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-Stopped
    $holds=@()
    if(Test-Path -LiteralPath $root){$holds+='Fresh acceptance root already exists; preserve it.'}
    if($null -ne (Get-TaskOrAbsent $taskName)){$holds+='Fresh acceptance task already exists; preserve it.'}
    $runtime=$null
    if(Test-Path -LiteralPath (Join-Path $installRoot 'install-after.json')){$runtime=Assert-R3InstalledBindings}else{$holds+='Reviewed r3 runtime is not installed.'}
    Assert-DockerNativeCustody
    foreach($n in 1..6){if(-not (Test-Path -LiteralPath "C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-slot$n\worker-denial-acceptance.json")){$holds+="Successful r3 slot$n receipt is not present."}}

    if(-not (Test-Path -LiteralPath (Join-Path $preflightRoot 'execution-prerequisites.json'))){$holds+='Successful read-only prerequisite receipt is not present.'}

    if(-not (Test-Path -LiteralPath (Join-Path $foundationRoot 'execution-foundation.json'))){$holds+='Successful scoped foundation receipt is not present.'}
    if(-not $Apply){[ordered]@{schema='cochem-docker-physical-plan/2';mode='READ_ONLY_PLAN';helper_sha256=$sourceHash;runtime=$runtime;target_root=$root;task_name=$taskName;
        all_prior_success_receipt_and_task_checks_deferred=$true;system_empty_registry_and_ram_revalidation_deferred=$true;containers_executed=$false;
        proposed_creates=@('fresh protected helper/config/packet/task/receipt namespace','fresh fixed RED/GREEN subtree inside slot1','exactly two single-use owned Docker containers');
        declared_mutations=@('existing foundation empty registry: two lease/demand/tombstone rows and two production receipts; transient WAL/SHM','scoped new RAM fixture subtree ACL/index flags','production ownership-verified removal of both new containers; retain quarantine if unverifiable');
        final_live_container_target=0;shared_capacity=4;maximum_prepared=2;chapter06_coding_workflow_tested=$false;native_model_jobs=0;
        legacy_database_or_budget_changes=0;ram_ledger_or_volume_changes=0;activation_ready=$false;automatic_retry_allowed=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $packet=Get-AcceptancePacket $runtime
    Invoke-Acceptance $runtime $packet
}finally{foreach($stream in $held){$stream.Dispose()}}
