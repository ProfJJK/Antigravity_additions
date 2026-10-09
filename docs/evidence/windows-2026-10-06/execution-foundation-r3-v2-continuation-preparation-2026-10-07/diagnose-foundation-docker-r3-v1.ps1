#Requires -Version 5.1
<# Default is a read-only plan. Reviewed -Apply creates one fresh protected
diagnostic root and no-trigger SYSTEM task. It rechecks the exact preserved
failure and successful preflight, then makes only fixed read-only Docker calls.
No foundation retry, RAM/registry provisioning, container changes or activation. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest

function Import-PinnedFunctions {
    param([string]$Path,[string]$Hash,[string[]]$Names)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try{
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        if($actual -cne $Hash){throw 'Reviewed diagnostic support changed.'}
        $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true,4096,$true)
        $tokens=$null;$errors=$null;try{$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)}finally{$reader.Dispose()}
        if($errors.Count){throw 'Reviewed diagnostic support parse error.'}
        $found=@();foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
            if($f.Name -in $Names){$found+=@($f.Name);$f.Extent.Text}}
        if(@($Names|Where-Object{$_ -notin $found}).Count){throw 'Reviewed diagnostic definition missing.'}
    }finally{$stream.Dispose()}
}

function Assert-HeldFoundationTask {
    param($Task,[string]$Executable)
    if($null -eq $Task){throw 'Preserved failed foundation task is missing.'}
    $d=$Task.Definition
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 2 -or
       $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
       $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Preserved foundation task is not exactly terminal and held.'}
    $action=$d.Actions.Item(1)
    $expected='-I -B "'+$failedRoot+'\provision-execution-foundation-r3.py" --nonce a92fe71e71224573a3738b5131ecfb36 --packet-sha256 '+$failedPacketHash
    if($action.Type -ne 0 -or $action.Path -cne $Executable -or $action.Arguments -cne $expected -or $action.WorkingDirectory -cne $failedRoot){throw 'Preserved failed foundation task action differs.'}
}

function Assert-HeldFoundationReceipt {
    param($Value,$Runtime)
    if($Value.schema -cne 'cochem-execution-foundation/1' -or $Value.status -cne 'EXECUTION_FOUNDATION_HELD' -or
       $Value.system_sid -cne 'S-1-5-18' -or $Value.nonce -cne 'a92fe71e71224573a3738b5131ecfb36' -or
       $Value.helper_sha256 -cne $failedHelperHash -or $Value.packet_sha256 -cne $failedPacketHash -or
       $Value.runtime_root -cne $installRoot -or $Value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or
       $Value.preflight_receipt_sha256 -cne $actualPreflightHash -or $Value.activation_ready -ne $false -or
       $Value.automatic_retry_allowed -ne $false -or $Value.partial_outputs_preserved -ne $true -or
       $Value.ram_provision_started -ne $false -or $Value.registry_provision_started -ne $false -or
       $Value.containers_created -ne 0 -or $Value.warm_pool_created -ne 0 -or $Value.native_model_jobs_executed -ne 0 -or
       $Value.credentials_or_native_profiles_modified -ne $false -or $Value.legacy_databases_or_budgets_modified -ne $false -or
       $Value.ram_volume_or_startup_task_modified -ne $false -or $Value.existing_tasks_modified_or_run -ne $false -or
       $Value.general_readiness_called -ne $false -or $Value.knowledge_service_constructed_or_refreshed -ne $false -or
       $Value.knowledge_index_sha256 -cne 'af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d' -or
       $Value.revision.verified -ne $true -or $Value.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or
       $Value.failure.phase -cne 'docker_owner_census' -or $Value.failure.error_type -cne 'WindowsIsolationError' -or $null -ne $Value.failure.winerror){
        throw 'Preserved foundation failure does not match the reviewed no-provisioning attempt.'}
}

function Get-DiagnosticPacket {
    param($Runtime)
    $packet=Get-FoundationPacket $Runtime
    if($packet.preflight_receipt_sha256 -cne $actualPreflightHash){throw 'Actual successful preflight receipt changed.'}
    $failure=(Read-R3Text (Read-R3Control (Join-Path $failedRoot 'execution-foundation.json') $failedReceiptHash 131072))|ConvertFrom-Json
    Assert-HeldFoundationReceipt $failure $Runtime
    $original=(Read-R3Text (Read-R3Control (Join-Path $failedRoot 'inputs.json') $failedPacketHash 32768))|ConvertFrom-Json
    if($original.schema -cne 'cochem-execution-foundation-inputs/1' -or
       $original.install_receipt_sha256 -cne $packet.install_receipt_sha256 -or
       $original.knowledge_receipt_sha256 -cne $packet.knowledge_receipt_sha256 -or
       $original.preflight_receipt_sha256 -cne $packet.preflight_receipt_sha256){throw 'Preserved original packet differs.'}
    foreach($n in 1..6){$slot="slot$n";if($original.workers.$slot -cne $packet.workers.$slot){throw 'Preserved worker packet differs.'}}
    $null=Read-R3Control (Join-Path $failedRoot 'provision-execution-foundation-r3.py') $failedHelperHash 1048576
    $null=Read-R3Control (Join-Path $failedRoot 'inspect-execution-prerequisites-r3.py') $preflightHash 1048576
    $null=Read-R3Control (Join-Path $failedRoot 'worker-denial-acceptance-r3.py') $supportHash 1048576
    Assert-HeldFoundationTask (Get-TaskOrAbsent 'CoChem-4.2.7-ExecutionFoundation-20261007-r3') $python
    $packet.schema='cochem-foundation-diagnostic-inputs/1'
    $packet.failed_foundation_receipt_sha256=$failedReceiptHash
    $packet.failed_foundation_packet_sha256=$failedPacketHash
    $packet
}

function ConvertTo-SafeDiagnosticChain {
    param($Chain)
    if($Chain.raw_exception_text_published -isnot [bool] -or $Chain.raw_exception_text_published -or
       $Chain.truncated -isnot [bool] -or @($Chain.nodes).Count -gt 8){throw 'Diagnostic exception chain exceeds its schema.'}
    $types=@('WindowsIsolationError','OSError','PermissionError','FileNotFoundError','ValueError','TimeoutError','ContainerError','RuntimeError','KeyError','TypeError','OtherError')
    $operations=@('unclassified','pipe_open','pipe_server_pid','server_process_open','server_token_open','server_image_query','server_creation_time','pipe_server_pid_recheck','current_token_open','token_inspection','sid_conversion','account_sid_lookup','worker_impersonation','worker_credential_read','worker_batch_logon','worker_privilege_removal','ram_mutex_open','ram_mutex_release','server_allowlist_missing','server_pid_invalid','server_token_rejected','server_image_rejected','server_changed_or_exited','token_inspection_size','system_identity_required','protected_code_root_required','worker_credential_identity_rejected','worker_token_identity_rejected','worker_privileged_group_rejected','worker_privilege_retained','code_acl_query','code_acl_control','code_acl_information','code_acl_entry','code_acl_rejected','account_sid_missing','worker_denial_not_established','worker_pipe_accessible')
    $safe=@();$index=0
    foreach($node in @($Chain.nodes)){
        if($index -eq 0){if($null -ne $node.parent -or $node.relation -cne 'root'){throw 'Diagnostic root differs.'}}
        elseif(($node.parent -isnot [int] -and $node.parent -isnot [long]) -or $node.parent -lt 0 -or $node.parent -ge $index -or $node.relation -cnotin @('cause','context')){throw 'Diagnostic parent differs.'}
        $row=[ordered]@{parent=$node.parent;relation=$node.relation}
        if($null -ne $node.PSObject.Properties['reference']){
            if(@($node.PSObject.Properties.Name|Where-Object{$_ -cnotin @('parent','relation','reference')}).Count -or
               ($node.reference -isnot [int] -and $node.reference -isnot [long]) -or $node.reference -lt 0 -or $node.reference -ge $index){throw 'Diagnostic reference differs.'}
            $row.reference=$node.reference
        }else{
            if(@($node.PSObject.Properties.Name|Where-Object{$_ -cnotin @('parent','relation','error_type','operation','winerror','winerror_source')}).Count -or
               $node.error_type -cnotin $types -or $node.operation -cnotin $operations){throw 'Unsafe diagnostic error node.'}
            if($null -eq $node.winerror){if($null -ne $node.winerror_source){throw 'Diagnostic native source differs.'}}
            elseif(($node.winerror -isnot [int] -and $node.winerror -isnot [long]) -or $node.winerror -lt 0 -or $node.winerror -gt 4294967295 -or
                    $node.winerror_source -cnotin @('attribute','allowlisted_message')){throw 'Diagnostic native number differs.'}
            $row.error_type=$node.error_type;$row.operation=$node.operation;$row.winerror=$node.winerror;$row.winerror_source=$node.winerror_source
        }
        $safe+=@($row);$index++
    }
    if($safe.Count -eq 0){throw 'Diagnostic chain is empty.'}
    [ordered]@{nodes=$safe;truncated=$Chain.truncated;raw_exception_text_published=$false}
}

function ConvertTo-SafeDiagnosticFailure {
    param($Failure)
    if($Failure.phase -cnotmatch '^[a-z_]{1,64}$'){throw 'Unsafe diagnostic phase.'}
    [ordered]@{phase=$Failure.phase;chain=(ConvertTo-SafeDiagnosticChain $Failure.chain)}
}

function ConvertTo-SafeDockerTrace {
    param($Trace)
    $commands=@('docker_info','docker_image','docker_owner_census','docker_name_census')
    $boundaries=@('docker_pipe_denials_before','docker_pipe_denials_after')
    if($Trace.last_stage -cnotin ($commands+$boundaries+@('before_observation')) -or
       ($Trace.commands_started -isnot [int] -and $Trace.commands_started -isnot [long]) -or $Trace.commands_started -lt 0 -or $Trace.commands_started -gt 4 -or
       ($Trace.commands_returned -isnot [int] -and $Trace.commands_returned -isnot [long]) -or $Trace.commands_returned -lt 0 -or $Trace.commands_returned -gt $Trace.commands_started -or
       ($Trace.retry_attempts -isnot [int] -and $Trace.retry_attempts -isnot [long]) -or $Trace.retry_attempts -ne 0 -or
       $Trace.original_guard_decisions_unchanged -isnot [bool] -or -not $Trace.original_guard_decisions_unchanged -or @($Trace.events).Count -gt 96){throw 'Docker diagnostic trace differs.'}
    $failed=@()
    foreach($event in @($Trace.events)){
        if($event.stage -cnotin ($commands+$boundaries) -or $event.completed -isnot [bool] -or $event.kind -cnotin @('server_attestation','bounded_cli')){throw 'Unsafe Docker diagnostic event.'}
        $row=[ordered]@{stage=$event.stage;kind=$event.kind;position=$event.position;completed=$event.completed}
        if($event.kind -ceq 'server_attestation'){
            if(($event.stage -cin $commands -and $event.position -cnotin @('before_cli','after_cli')) -or
               ($event.stage -cin $boundaries -and $event.position -cnotmatch '^boundary_attestation_[1-3]$') -or
               $event.alias -cnotin @('selected_linux','windows_alias','default_alias')){throw 'Unsafe attestation event.'}
            $row.alias=$event.alias
        }elseif($event.stage -cnotin $commands -or $event.position -cne 'single_attempt'){throw 'Unsafe transport event.'}
        if($null -ne $event.PSObject.Properties['failure']){
            if($event.completed){throw 'Completed diagnostic event includes failure.'}
            $row.failure=ConvertTo-SafeDiagnosticChain $event.failure
        }elseif(-not $event.completed){throw 'Incomplete diagnostic event lacks failure.'}
        if(-not $event.completed){$failed+=@($row)}
    }
    [ordered]@{last_stage=$Trace.last_stage;events_recorded=@($Trace.events).Count;failed_events=$failed;
        commands_started=$Trace.commands_started;commands_returned=$Trace.commands_returned;
        retry_attempts=0;original_guard_decisions_unchanged=$true}
}

function Get-DiagnosticSummary {
    param($Receipt,$Task,$Runtime,[string]$Nonce,[string]$PacketHash,[string]$ReceiptPath,[string]$ReceiptHash)
    if($Receipt.schema -cne 'cochem-foundation-diagnostic/1' -or $Receipt.nonce -cne $Nonce -or $Receipt.system_sid -cne 'S-1-5-18' -or
       $Receipt.helper_sha256 -cne $sourceHash -or $Receipt.packet_sha256 -cne $PacketHash -or $Receipt.runtime_root -cne $installRoot -or
       $ReceiptPath -cne (Join-Path $root 'foundation-diagnostic.json') -or $ReceiptHash -cnotmatch '^[a-f0-9]{64}$'){
        throw 'Diagnostic receipt binding differs.'}
    foreach($flag in @('activation_ready','ram_provision_started','registry_provision_started','existing_files_or_acls_modified','existing_databases_modified','existing_tasks_modified_or_run','knowledge_service_constructed','docker_runner_constructed','ram_ensure_called','native_provider_authentication_performed','automatic_retry_performed')){
        if($Receipt.$flag -isnot [bool] -or $Receipt.$flag){throw 'Diagnostic preservation/scope differs.'}
    }
    if($Receipt.diagnostic_only -isnot [bool] -or -not $Receipt.diagnostic_only -or
       ($Receipt.native_model_jobs_executed -isnot [int] -and $Receipt.native_model_jobs_executed -isnot [long]) -or $Receipt.native_model_jobs_executed -ne 0){throw 'Diagnostic model/scope differs.'}
    foreach($binding in @(
        [pscustomobject]@{name='install_receipt_sha256';expected=$Runtime.install_receipt_sha256},
        [pscustomobject]@{name='preflight_receipt_sha256';expected=$actualPreflightHash},
        [pscustomobject]@{name='failed_foundation_receipt_sha256';expected=$failedReceiptHash},
        [pscustomobject]@{name='failed_foundation_packet_sha256';expected=$failedPacketHash}
    )){if($null -ne $Receipt.PSObject.Properties[$binding.name] -and $Receipt.($binding.name) -cne $binding.expected){throw 'Diagnostic prior evidence differs.'}}
    if($Receipt.status -ceq 'FOUNDATION_READ_ONLY_DIAGNOSTIC_VERIFIED'){
        if($Task.LastTaskResult -ne 0 -or $null -ne $Receipt.PSObject.Properties['failure'] -or
           $Receipt.preservation_after_observation_verified -isnot [bool] -or -not $Receipt.preservation_after_observation_verified){throw 'Successful diagnostic outcome differs.'}
        foreach($name in @('install_receipt_sha256','preflight_receipt_sha256','failed_foundation_receipt_sha256','failed_foundation_packet_sha256')){
            if($null -eq $Receipt.PSObject.Properties[$name]){throw 'Successful diagnostic evidence binding is incomplete.'}
        }
        if($Receipt.revision.verified -isnot [bool] -or -not $Receipt.revision.verified -or
           $Receipt.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or
           $Receipt.docker_trace.commands_started -ne 4 -or $Receipt.docker_trace.commands_returned -ne 4 -or
           $Receipt.docker_trace.last_stage -cne 'docker_pipe_denials_after' -or
           @($Receipt.docker_trace.events|Where-Object{-not $_.completed}).Count -ne 0){throw 'Successful diagnostic execution evidence is incomplete.'}
    }elseif($Receipt.status -ceq 'FOUNDATION_DIAGNOSTIC_HELD'){
        if($Task.LastTaskResult -ne 2 -or $null -eq $Receipt.PSObject.Properties['failure']){throw 'Held diagnostic outcome differs.'}
    }else{throw 'Unknown diagnostic outcome.'}
    $summary=[ordered]@{schema='cochem-foundation-diagnostic-task-result/1';status=$Receipt.status;
        receipt_path=$ReceiptPath;receipt_sha256=$ReceiptHash;last_task_result=$Task.LastTaskResult;
        failed_foundation_receipt_sha256=$failedReceiptHash;preflight_receipt_sha256=$actualPreflightHash;
        activation_ready=$false;provisioning_performed=$false;existing_state_modified=$false;automatic_retry_allowed=$false;
        docker_trace=(ConvertTo-SafeDockerTrace $Receipt.docker_trace)}
    if($null -ne $Receipt.PSObject.Properties['failure']){$summary.failure=ConvertTo-SafeDiagnosticFailure $Receipt.failure}
    if($null -ne $Receipt.PSObject.Properties['preservation_failure']){$summary.preservation_failure=ConvertTo-SafeDiagnosticFailure $Receipt.preservation_failure}
    if($null -ne $Receipt.PSObject.Properties['preservation_after_observation_verified']){
        if($Receipt.preservation_after_observation_verified -isnot [bool]){throw 'Diagnostic preservation result differs.'}
        $summary.preservation_after_observation_verified=$Receipt.preservation_after_observation_verified
    }
    $summary
}

function Invoke-FoundationDiagnostic {
    param($Runtime,$Packet)
    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask
    $check=Get-DiagnosticPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Diagnostic evidence changed before creation.'}
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-TaskOrAbsent $taskName)){throw 'Fresh diagnostic namespace exists; preserve it.'}
    $null=Assert-CodeTreeOnce $installRoot;$null=Assert-CodeTreeOnce (Split-Path -Parent $basePython)
    Assert-DockerNativeCustody
    New-ProtectedDirectory $root;New-ProtectedDirectory (Join-Path $root 'docker-client-config')
    $records=@(
        [pscustomobject]@{source=$source;destination=(Join-Path $root 'diagnose-foundation-docker-r3-v1.py');sha256=$sourceHash;length=(Get-Item -LiteralPath $source).Length}
        [pscustomobject]@{source=$preflight;destination=(Join-Path $root 'inspect-execution-prerequisites-r3.py');sha256=$preflightHash;length=(Get-Item -LiteralPath $preflight).Length}
        [pscustomobject]@{source=$support;destination=(Join-Path $root 'worker-denial-acceptance-r3.py');sha256=$supportHash;length=(Get-Item -LiteralPath $support).Length}
    )
    foreach($record in $records){Copy-VerifiedPayload $record;$held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))}
    $packetPath=Join-Path $root 'inputs.json';$packetHash=Write-NewPacket $packetPath $Packet
    $held.Add((Open-VerifiedFile $packetPath $packetHash (Get-Item -LiteralPath $packetPath).Length))
    Assert-Stopped
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Read-only diagnosis of preserved held foundation. Fixed Docker observations only; no retry, RAM/registry/container provisioning, provider authentication, state repair or activation.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT8M'
    $action=$definition.Actions.Create(0);$action.Path=$python
    $action.Arguments='-I -B "'+(Join-Path $root 'diagnose-foundation-docker-r3-v1.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
    $action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-Inspection $instance -Seconds 490
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Diagnostic task terminal state unknown; preserve evidence.'}
    $control=Read-R3Control (Join-Path $root 'foundation-diagnostic.json') '' 131072
    $receipt=(Read-R3Text $control)|ConvertFrom-Json
    $summary=Get-DiagnosticSummary $receipt $task $Runtime $nonce $packetHash $control.Stream.Name $control.Sha256
    Assert-Stopped;$check=Get-DiagnosticPacket $Runtime
    if(($check|ConvertTo-Json -Depth 6 -Compress) -cne ($Packet|ConvertTo-Json -Depth 6 -Compress)){throw 'Preserved evidence changed after diagnosis.'}
    $summary|ConvertTo-Json -Depth 12
}

foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -cne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$root='C:\Program Files\CoChem\ExecutionFoundationDiagnostic4.2.7-windows-20261007-r3-v1'
$taskName='CoChem-4.2.7-ExecutionFoundationDiagnostic-20261007-r3-v1'
$failedRoot='C:\Program Files\CoChem\ExecutionFoundation4.2.7-windows-20261007-r3'
$failedReceiptHash='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'
$failedPacketHash='a3cd28986b36c284f77da67bcbf5535ec509810e6dcc3f99be678d56944667f6'
$failedHelperHash='fc5b5946b710da9bce079c687783fbfea91fdb85b9fcb711960ad6657b0b4ab1'
$preflightRoot='C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3'
$actualPreflightHash='3b35d79919916670b12fb1756e543f40b9d837ef96b912a4752386fef504ba2e'
$preflight=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.py';$preflightHash='17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a'
$preflightWrapper=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.ps1';$preflightWrapperHash='5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887'
foreach($definition in @(Import-PinnedFunctions $preflightWrapper $preflightWrapperHash @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Get-EvidencePacket','Write-NewPacket','Assert-WindowsKernel32','Assert-DockerNativeCustody','Wait-Inspection'))){. ([scriptblock]::Create($definition))}
$foundationWrapper=Join-Path $PSScriptRoot 'provision-execution-foundation-r3.ps1'
foreach($definition in @(Import-PinnedFunctions $foundationWrapper '402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c' @('Get-FoundationPacket'))){. ([scriptblock]::Create($definition))}
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'diagnose-foundation-docker-r3-v1.py';$sourceHash='fc17e670e9a6903ba6f99ac2fb38b634283fad910164c010a24f9917c9de5eb5'
$support=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$knowledgeHash='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1';$copyHash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'
foreach($definition in @(Import-PinnedFunctions $copyHelper $copyHash @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$leaf=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1';$leafHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    if($sourceHash -cnotmatch '^[a-f0-9]{64}$'){throw 'Diagnostic source has not been frozen; no phase invoked.'}
    $pins=@([pscustomobject]@{path=$source;sha=$sourceHash},[pscustomobject]@{path=$preflight;sha=$preflightHash},[pscustomobject]@{path=$preflightWrapper;sha=$preflightWrapperHash},[pscustomobject]@{path=$foundationWrapper;sha='402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c'},[pscustomobject]@{path=$support;sha=$supportHash},[pscustomobject]@{path=$leaf;sha=$leafHash})
    foreach($pin in $pins){$held.Add((Open-VerifiedFile $pin.path $pin.sha (Get-Item -LiteralPath $pin.path).Length))}
    foreach($definition in @(Import-PinnedFunctions $leaf $leafHash @('Read-R3Control','Read-R3Text','Assert-CodeTreeOnce','Assert-OriginalFailedDenialTask','Assert-OriginalFailedDenial','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($definition))}
    $null=Read-R3Control $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' 1048576
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-Stopped
    $holds=@();$runtime=Assert-R3InstalledBindings
    if(Test-Path -LiteralPath $root){$holds+='Fresh diagnostic root already exists; preserve it.'}
    if($null -ne (Get-TaskOrAbsent $taskName)){$holds+='Fresh diagnostic task already exists; preserve it.'}
    Assert-DockerNativeCustody
    if(-not $Apply){[ordered]@{schema='cochem-foundation-diagnostic-plan/1';mode='READ_ONLY_PLAN';helper_sha256=$sourceHash;
        runtime=$runtime;target_root=$root;task_name=$taskName;failed_foundation_receipt_sha256=$failedReceiptHash;
        preflight_receipt_sha256=$actualPreflightHash;private_receipt_and_prior_task_checks_deferred=$true;
        proposed_creates=@('fresh protected diagnostic helper/config/packet/receipt root','one no-trigger SYSTEM diagnostic task');
        existing_state_changes=0;provisioning_performed=$false;containers_created=0;native_model_jobs_executed=0;
        activation_ready=$false;automatic_retry_allowed=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $packet=Get-DiagnosticPacket $runtime
    Invoke-FoundationDiagnostic $runtime $packet
}finally{foreach($stream in $held){$stream.Dispose()}}
