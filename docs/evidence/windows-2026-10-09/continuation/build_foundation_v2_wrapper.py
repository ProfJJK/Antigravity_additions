"""Create the fresh successor wrapper without editing frozen prior wrappers."""
from pathlib import Path

W=Path(__file__).parent
target=W/'provision-execution-foundation-r3-v2.ps1'
old=(W/'provision-execution-foundation-r3.ps1').read_text()
old=old.replace('<# DRAFT. Default preview only.', '<# Default preview only. Fresh reviewed continuation after the successful diagnostic.')
start=old.index('function Get-FoundationPacket {')
end=old.index('function Invoke-Foundation {',start)
new=r'''function Assert-VerifiedDiagnosticReceipt {
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
        $traces=@();foreach($trace in @($Receipt.docker_traces)){$traces+=@(ConvertTo-SafeDockerTrace $trace)}
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
        foreach($trace in $summary.docker_traces){if($trace.commands_started -ne 4 -or $trace.commands_returned -ne 4 -or $trace.events_recorded -ne 18 -or
            $trace.last_stage -cne 'docker_pipe_denials_after' -or @($trace.failed_events).Count){throw 'Foundation successful Docker trace differs.'}}
        $summary.ram_scoped_roots_verified=6;$summary.empty_registry_capacity=4
    }elseif($Receipt.status -ceq 'EXECUTION_FOUNDATION_HELD'){
        if($Task.LastTaskResult -ne 2 -or $null -eq $Receipt.PSObject.Properties['failure']){throw 'Foundation held/task result differs.'}
    }else{throw 'Unknown foundation outcome.'}
    $summary
}

'''
old=old[:start]+new+old[end:]
old=old.replace('Get-FoundationPacket $Runtime','Get-ContinuationPacket $Runtime').replace('Get-FoundationPacket $runtime','Get-ContinuationPacket $runtime')
start=old.index("    if($receipt.schema -cne 'cochem-execution-foundation/1'")
end=old.index('    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask',start)
old=old[:start]+'''    $summary=Get-FoundationSummary $receipt $task $Runtime $Packet $nonce $packetHash $control.Stream.Name $control.Sha256
    if($receipt.status -cne 'SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'){Write-Host ($summary|ConvertTo-Json -Depth 10);throw 'Fresh foundation held; preserve all outputs. No retry or reset.'}
'''+old[end:]
old=old.replace('provision-execution-foundation-r3.py','provision-execution-foundation-r3-v2.py')
old=old.replace("$root='C:\\Program Files\\CoChem\\ExecutionFoundation4.2.7-windows-20261007-r3';$taskName='CoChem-4.2.7-ExecutionFoundation-20261007-r3'", """$root='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3-v2';$taskName='CoChem-4.2.7-ExecutionFoundation-20261007-r3-v2'
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
$foundationWrapper=Join-Path $PSScriptRoot 'provision-execution-foundation-r3.ps1';$foundationWrapperHash='402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c'""")
needle="$python=Join-Path $installRoot '.venv\\Scripts\\python.exe'"
imports="""foreach($definition in @(Import-PinnedFunctions $foundationWrapper $foundationWrapperHash @('Get-FoundationPacket'))){. ([scriptblock]::Create($definition))}
foreach($definition in @(Import-PinnedFunctions $diagnosticWrapper $diagnosticWrapperHash @('Assert-HeldFoundationTask','Assert-HeldFoundationReceipt','Get-DiagnosticPacket','ConvertTo-SafeDiagnosticChain','ConvertTo-SafeDiagnosticFailure','ConvertTo-SafeDockerTrace'))){. ([scriptblock]::Create($definition))}
"""
old=old.replace(needle,imports+needle)
old=old.replace("$sourceHash='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'","$sourceHash='UNFROZEN'")
old=old.replace('    $pins=@(',"    if($sourceHash -cnotmatch '^[a-f0-9]{64}$'){throw 'Fresh foundation source has not been frozen.'}\n    $pins=@([pscustomobject]@{path=$diagnostic;sha=$diagnosticHash},[pscustomobject]@{path=$diagnosticWrapper;sha=$diagnosticWrapperHash},[pscustomobject]@{path=$foundationWrapper;sha=$foundationWrapperHash},")
old=old.replace('    $records=@(',"    $records=@(\n        [pscustomobject]@{source=$diagnostic;destination=(Join-Path $root 'diagnose-foundation-docker-r3-v1.py');sha256=$diagnosticHash;length=(Get-Item -LiteralPath $diagnostic).Length}")
old=old.replace('$instance=$task.Run($null);Wait-Inspection $instance','$instance=$task.Run($null);Wait-Inspection $instance -Seconds 490')
old=old.replace("schema='cochem-execution-foundation-plan/1'", "schema='cochem-execution-foundation-plan/2'")
old=old.replace('target_root=$root;task_name=$taskName;', 'target_root=$root;task_name=$taskName;diagnostic_receipt_sha256=$diagnosticReceiptHash;failed_foundation_receipt_sha256=$failedReceiptHash;')
old=old.replace('$summary|ConvertTo-Json -Depth 6','$summary|ConvertTo-Json -Depth 10')
with target.open('x',encoding='utf-8',newline='\n') as out:out.write(old)
print(target)
