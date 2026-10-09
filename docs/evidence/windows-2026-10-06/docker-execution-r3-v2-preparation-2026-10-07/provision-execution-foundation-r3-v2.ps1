#Requires -Version 5.1
<# Default preview only. Fresh reviewed continuation after the successful diagnostic. Reviewed -Apply creates exactly one fresh helper
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


function Assert-VerifiedDiagnosticReceipt {
    param($Value,$Runtime,$Packet)
    if($Value.schema -cne 'cochem-foundation-diagnostic/1' -or $Value.status -cne 'FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED' -or
       $Value.system_sid -cne 'S-1-5-18' -or $Value.nonce -cne '3e2e411d9c274167a703a4308404dbe5' -or
       $Value.packet_sha256 -cne $diagnosticPacketHash -or $Value.helper_sha256 -cne $diagnosticHash -or
       $Value.runtime_root -cne $installRoot -or $Value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or
       $Value.preflight_receipt_sha256 -cne $actualPreflightHash -or $Value.failed_foundation_receipt_sha256 -cne $failedReceiptHash -or
       $Value.failed_foundation_packet_sha256 -cne $failedPacketHash -or $null -ne $Value.PSObject.Properties['failure'] -or
       $null -ne $Value.PSObject.Properties['preservation_failure']){throw 'Exact successful diagnostic is required for the fresh continuation.'}
    foreach($flag in @('activation_ready','ram_provision_started','registry_provision_started','existing_files_or_acls_modified','existing_databases_modified','existing_tasks_modified_or_run','knowledge_service_constructed','docker_runner_constructed','ram_ensure_called','native_provider_authentication_performed','automatic_retry_performed')){
        if($Value.$flag -isnot [bool] -or $Value.$flag){throw 'Successful diagnostic preservation/scope differs.'}
    }
    foreach($flag in @('diagnostic_only','preservation_after_observation_verified','own_fresh_docker_config_directory_used','own_fresh_receipt_created','worker_handle_probe_phase_entered','historical_exact_failure_unrecoverable_from_original_receipt')){
        if($Value.$flag -isnot [bool] -or -not $Value.$flag){throw 'Successful diagnostic proof differs.'}
    }
    if(($Value.native_model_jobs_executed -isnot [int] -and $Value.native_model_jobs_executed -isnot [long]) -or $Value.native_model_jobs_executed -ne 0 -or
       $Value.revision.verified -isnot [bool] -or -not $Value.revision.verified -or
       $Value.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or @($Value.worker_receipt_sha256.PSObject.Properties).Count -ne 6){throw 'Diagnostic runtime/workers differ.'}
    foreach($slot in $Packet.workers.Keys){if($Value.worker_receipt_sha256.$slot -cne $Packet.workers[$slot]){throw 'Diagnostic worker binding differs.'}}
    $trace=ConvertTo-SafeDockerTrace $Value.docker_trace
    if($trace.last_stage -cne 'docker_pipe_denials_after' -or $trace.commands_started -ne 4 -or $trace.commands_returned -ne 4 -or
       $trace.events_recorded -ne 18 -or @($trace.failed_events).Count){throw 'Diagnostic complete Docker trace differs.'}
    foreach($flag in @('container_registry_root_absent','ram_ledger_absent','ram_workspace_absent','readiness_receipt_absent')){
        if($Value.fresh_state_before.$flag -isnot [bool] -or -not $Value.fresh_state_before.$flag -or
           $Value.fresh_state_after.$flag -isnot [bool] -or -not $Value.fresh_state_after.$flag){throw 'Diagnostic fresh-state proof differs.'}
    }
}

function Get-ContinuationPacket {
    param($Runtime)
    $packet=Get-DiagnosticPacket $Runtime
    $control=Read-R3Control (Join-Path $diagnosticRoot 'foundation-diagnostic.json') $diagnosticReceiptHash 131072
    $value=(Read-R3Text $control)|ConvertFrom-Json
    Assert-VerifiedDiagnosticReceipt $value $Runtime $packet
    $original=(Read-R3Text (Read-R3Control (Join-Path $diagnosticRoot 'inputs.json') $diagnosticPacketHash 32768))|ConvertFrom-Json
    foreach($binding in @('schema','install_receipt_sha256','knowledge_receipt_sha256','preflight_receipt_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')){
        if($original.$binding -cne $packet[$binding]){throw 'Preserved diagnostic packet differs.'}
    }
    foreach($n in 1..6){$slot="slot$n";if($original.workers.$slot -cne $packet.workers[$slot]){throw 'Preserved diagnostic worker packet differs.'}}
    $null=Read-R3Control (Join-Path $diagnosticRoot 'diagnose-foundation-docker-r3-v1.py') $diagnosticHash 1048576
    $null=Read-R3Control (Join-Path $diagnosticRoot 'inspect-execution-prerequisites-r3.py') $preflightHash 1048576
    $null=Read-R3Control (Join-Path $diagnosticRoot 'worker-denial-acceptance-r3.py') $supportHash 1048576
    $arguments='-I -B "'+$diagnosticRoot+'\diagnose-foundation-docker-r3-v1.py" --nonce 3e2e411d9c274167a703a4308404dbe5 --packet-sha256 '+$diagnosticPacketHash
    Assert-PassedTask (Get-TaskOrAbsent 'CoChem-4.2.7-ExecutionFoundationDiagnostic-20261007-r3-v1') $python $arguments $diagnosticRoot
    $packet.schema='cochem-execution-foundation-inputs/2'
    $packet.diagnostic_receipt_sha256=$diagnosticReceiptHash
    $packet.diagnostic_packet_sha256=$diagnosticPacketHash
    $packet
}

function Get-FoundationSummary {
    param($Receipt,$Task,$Runtime,$Packet,[string]$Nonce,[string]$PacketHash,[string]$ReceiptPath,[string]$ReceiptHash)
    if($Receipt.schema -cne 'cochem-execution-foundation/2' -or $Receipt.nonce -cne $Nonce -or $Receipt.system_sid -cne 'S-1-5-18' -or
       $Receipt.helper_sha256 -cne $sourceHash -or $Receipt.packet_sha256 -cne $PacketHash -or $Receipt.runtime_root -cne $installRoot -or
       $ReceiptPath -cne (Join-Path $root 'execution-foundation.json') -or $ReceiptHash -cnotmatch '^[a-f0-9]{64}$'){
        throw 'Fresh foundation receipt binding differs.'}
    foreach($flag in @('activation_ready','automatic_retry_allowed','ram_volume_or_startup_task_modified','legacy_databases_or_budgets_modified','credentials_or_native_profiles_modified','knowledge_service_constructed_or_refreshed','general_readiness_called','existing_tasks_modified_or_run')){
        if($Receipt.$flag -isnot [bool] -or $Receipt.$flag){throw 'Foundation preservation/scope differs.'}
    }
    foreach($flag in @('partial_outputs_preserved','ram_provision_started','registry_provision_started')){
        if($Receipt.$flag -isnot [bool]){throw 'Foundation phase flags differ.'}
    }
    if(-not $Receipt.partial_outputs_preserved){throw 'Foundation partial outputs not preserved.'}
    foreach($name in @('containers_created','warm_pool_created','native_model_jobs_executed')){
        if(($Receipt.$name -isnot [int] -and $Receipt.$name -isnot [long]) -or $Receipt.$name -ne 0){throw 'Foundation execution scope differs.'}
    }
    $bindings=@('install_receipt_sha256','preflight_receipt_sha256','diagnostic_receipt_sha256','diagnostic_packet_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')
    foreach($binding in $bindings){
        if($null -ne $Receipt.PSObject.Properties[$binding] -and $Receipt.$binding -cne $Packet[$binding]){throw 'Foundation prior evidence binding differs.'}
    }
    $summary=[ordered]@{schema='cochem-execution-foundation-task-result/2';status=$Receipt.status;receipt_path=$ReceiptPath;receipt_sha256=$ReceiptHash;
        last_task_result=$Task.LastTaskResult;runtime_root=$installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;
        diagnostic_receipt_sha256=$diagnosticReceiptHash;failed_foundation_receipt_sha256=$failedReceiptHash;activation_ready=$false;
        automatic_retry_allowed=$false;partial_outputs_preserved=$true;ram_provision_started=$Receipt.ram_provision_started;registry_provision_started=$Receipt.registry_provision_started}
    if($null -ne $Receipt.PSObject.Properties['failure']){$summary.failure=ConvertTo-SafeDiagnosticFailure $Receipt.failure}
    if($null -ne $Receipt.PSObject.Properties['docker_traces']){
        if(@($Receipt.docker_traces).Count -gt 2){throw 'Foundation Docker observations exceed bound.'}
        $traces=@();$expectedObservations=@('before_ram','before_registry');$index=0
        foreach($entry in @($Receipt.docker_traces)){
            if($entry.observation -cne $expectedObservations[$index]){throw 'Foundation observation order differs.'}
            $traces+=@([ordered]@{observation=$entry.observation;trace=(ConvertTo-SafeDockerTrace $entry.trace)});$index++
        }
        $summary.docker_traces=$traces
    }
    if($Receipt.status -ceq 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'){
        if($Task.LastTaskResult -ne 0 -or $null -ne $Receipt.PSObject.Properties['failure']){throw 'Foundation success/task result differs.'}
        foreach($binding in $bindings){if($null -eq $Receipt.PSObject.Properties[$binding]){throw 'Foundation prior evidence is incomplete.'}}
        if($Receipt.ram_provision_started -ne $true -or $Receipt.registry_provision_started -ne $true -or
           $Receipt.ram_scoped_roots_verified -ne 6 -or $Receipt.ram_ledger_created_new -isnot [bool] -or -not $Receipt.ram_ledger_created_new -or
           $Receipt.registry.registry_created_new -isnot [bool] -or -not $Receipt.registry.registry_created_new -or
           $Receipt.registry.capacity -ne 4 -or $Receipt.registry.work_rows -ne 0 -or $Receipt.registry.unknown_owned -ne 0 -or
           $Receipt.registry.integrity_check -cne 'ok' -or $Receipt.revision.verified -isnot [bool] -or -not $Receipt.revision.verified -or
           $Receipt.revision.source_sha256 -cne $Runtime.revision.source_sha256){throw 'Successful foundation claims differ.'}
        if(@($summary.docker_traces).Count -ne 2){throw 'Foundation complete Docker traces required.'}
        foreach($entry in $summary.docker_traces){$trace=$entry.trace;if($trace.commands_started -ne 4 -or $trace.commands_returned -ne 4 -or $trace.events_recorded -ne 18 -or
            $trace.last_stage -cne 'docker_pipe_denials_after' -or @($trace.failed_events).Count){throw 'Foundation successful Docker trace differs.'}}
        $summary.ram_scoped_roots_verified=6;$summary.empty_registry_capacity=4
    }elseif($Receipt.status -ceq 'EXECUTION_FOUNDATION_HELD'){
        if($Task.LastTaskResult -ne 2 -or $null -eq $Receipt.PSObject.Properties['failure']){throw 'Foundation held/task result differs.'}
    }else{throw 'Unknown foundation outcome.'}
    $summary
}

function Invoke-Foundation {
    param($Runtime,$Packet)
    Assert-Stopped
    $null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-ContinuationPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed before creation.'}
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-TaskOrAbsent $taskName)){throw 'Fresh foundation namespace exists; preserve it.'}
    $null=Assert-CodeTreeOnce $installRoot
    $null=Assert-CodeTreeOnce (Split-Path -Parent $basePython)
    Assert-DockerNativeCustody
    New-ProtectedDirectory $root
    New-ProtectedDirectory (Join-Path $root 'docker-client-config')
    $records=@(
        [pscustomobject]@{source=$diagnostic;destination=(Join-Path $root 'diagnose-foundation-docker-r3-v1.py');sha256=$diagnosticHash;length=(Get-Item -LiteralPath $diagnostic).Length}
        [pscustomobject]@{source=$source;destination=(Join-Path $root 'provision-execution-foundation-r3-v2.py');sha256=$sourceHash;length=(Get-Item -LiteralPath $source).Length}
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
    $action.Arguments='-I -B "'+(Join-Path $root 'provision-execution-foundation-r3-v2.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
    $action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-Inspection $instance -Seconds 490
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Foundation task terminal state unknown; preserve evidence.'}
    $control=Read-R3Control (Join-Path $root 'execution-foundation.json') '' 131072
    $receipt=(Read-R3Text $control)|ConvertFrom-Json
    $summary=Get-FoundationSummary $receipt $task $Runtime $Packet $nonce $packetHash $control.Stream.Name $control.Sha256
    if($receipt.status -cne 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'){Write-Host ($summary|ConvertTo-Json -Depth 10);throw 'Fresh foundation held; preserve all outputs. No retry or reset.'}
    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-ContinuationPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Prerequisite packet changed after provisioning; preserve state.'}
    $summary.ram_scoped_roots_verified=6;$summary.empty_registry_capacity=4
    $summary|ConvertTo-Json -Depth 10
}
foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$root='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2';$taskName='CoChem-4.2.7-ExecutionFoundation-20261007-r3-v2'
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
$foundationWrapper=Join-Path $PSScriptRoot 'provision-execution-foundation-r3.ps1';$foundationWrapperHash='402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c'
$preflightRoot='C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3'
$preflight=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.py';$preflightHash='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
$preflightWrapper=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.ps1';$preflightWrapperHash='5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887'
foreach($definition in @(Import-PinnedFunctions $preflightWrapper $preflightWrapperHash @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Get-EvidencePacket','Write-NewPacket','Assert-WindowsKernel32','Assert-DockerNativeCustody','Wait-Inspection'))){. ([scriptblock]::Create($definition))}
foreach($definition in @(Import-PinnedFunctions $foundationWrapper $foundationWrapperHash @('Get-FoundationPacket'))){. ([scriptblock]::Create($definition))}
foreach($definition in @(Import-PinnedFunctions $diagnosticWrapper $diagnosticWrapperHash @('Assert-HeldFoundationTask','Assert-HeldFoundationReceipt','Get-DiagnosticPacket','ConvertTo-SafeDiagnosticChain','ConvertTo-SafeDiagnosticFailure','ConvertTo-SafeDockerTrace'))){. ([scriptblock]::Create($definition))}
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'provision-execution-foundation-r3-v2.py';$sourceHash='620c43db688bac8fca2c7870197c92a7cb4b54f0a1c6b81bc3453ab6b2a3ff0c'
$support=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$knowledgeHash='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$copyHash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'
foreach($definition in @(Import-PinnedFunctions $copyHelper $copyHash @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$leaf=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1';$leafHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    if($sourceHash -cnotmatch '^[a-f0-9]{64}$'){throw 'Fresh foundation source has not been frozen.'}
    $pins=@([pscustomobject]@{path=$diagnostic;sha=$diagnosticHash},[pscustomobject]@{path=$diagnosticWrapper;sha=$diagnosticWrapperHash},[pscustomobject]@{path=$foundationWrapper;sha=$foundationWrapperHash},[pscustomobject]@{path=$source;sha=$sourceHash},[pscustomobject]@{path=$preflight;sha=$preflightHash},[pscustomobject]@{path=$preflightWrapper;sha=$preflightWrapperHash},[pscustomobject]@{path=$support;sha=$supportHash},[pscustomobject]@{path=$leaf;sha=$leafHash})
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
    if(-not $Apply){[ordered]@{schema='cochem-execution-foundation-plan/2';mode='READ_ONLY_PLAN';helper_sha256=$sourceHash;runtime=$runtime;target_root=$root;task_name=$taskName;diagnostic_receipt_sha256=$diagnosticReceiptHash;failed_foundation_receipt_sha256=$failedReceiptHash;
        all_prior_success_receipt_and_task_checks_deferred=$true;system_fresh_state_revalidation_deferred=$true;provisioning_executed=$false;
        proposed_creates=@('fresh helper root, isolated Docker CLI config, packet, SYSTEM task and receipt','R:\CoChem427-windows-20261007 plus exactly six slot roots','fresh private ramdisk-state.json','fresh private containers directory and empty containers.db with transient WAL/SHM');
        scoped_changes=@('ACL and no-content-index flags on seven new RAM directories','only missing six Defender exclusions');
        ram_volume_or_startup_task_changes=0;existing_database_changes=0;containers_created=0;activation_ready=$false;automatic_retry_allowed=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $packet=Get-ContinuationPacket $runtime
    Invoke-Foundation $runtime $packet
}finally{foreach($stream in $held){$stream.Dispose()}}
