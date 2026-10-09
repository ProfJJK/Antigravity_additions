#Requires -Version 5.1
<# Draft: default metadata preview only. After independent review, -Apply creates
   only one fresh protected helper/config/receipt root and one no-trigger SYSTEM
   inspection task. No RAM/Docker provisioning, credentials, database changes,
   existing task changes, native provider login, model jobs or activation. #>
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

function Get-TaskOrAbsent {
    param([string]$Name)
    try{$folder.GetTask($Name)}catch{
        $e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147024894){return $null};$e=$e.InnerException}
        throw 'Task state unknown; preserve it.'
    }
}

function Assert-Stopped {
    foreach($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')){
        $task=Get-TaskOrAbsent $name
        if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){throw 'Managed daemons must remain disabled and terminal.'}}
}

function Assert-PassedTask {
    param($Task,[string]$Executable,[string]$Arguments,[string]$Directory)
    if($null -eq $Task){throw 'Required successful task is missing.'}
    $d=$Task.Definition
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0 -or $Task.LastTaskResult -ne 0 -or
       $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1 -or
       $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1){throw 'Required prior SYSTEM task is not exactly terminal and successful.'}
    $action=$d.Actions.Item(1)
    if($action.Type -ne 0 -or $action.Path -cne $Executable -or $action.Arguments -cne $Arguments -or $action.WorkingDirectory -cne $Directory){throw 'Required successful task action differs.'}
}

function Get-EvidencePacket {
    param($Runtime)
    $workers=[ordered]@{}
    $layout=(Read-R3Text (Read-R3Control (Join-Path $installRoot 'windows-layout.json') '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4'))|ConvertFrom-Json
    foreach($n in 1..6){
        $slot="slot$n";$workerRoot="C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-$slot"
        $control=Read-R3Control (Join-Path $workerRoot 'worker-denial-acceptance.json') '' 32768
        $value=(Read-R3Text $control)|ConvertFrom-Json
        if($value.schema -cne 'cochem-worker-denial-acceptance/1' -or $value.status -cne 'HANDLE_DENIALS_VERIFIED' -or
           $value.slot -cne $slot -or $value.system_sid -cne 'S-1-5-18' -or $value.helper_sha256 -cne $supportHash -or
           $value.runtime_root -cne $installRoot -or $value.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or
           $value.source_manifest_sha256 -cne $Runtime.source_manifest_sha256 -or $value.config_sha256 -cne $Runtime.configuration_sha256 -or
           $value.resource_limits_sha256 -cne 'de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f' -or
           $value.cleanup_verified -ne $true -or $value.worker_released -ne $true -or $value.daemon_states_verified_before_and_after -ne $true -or
           $value.nonce -cnotmatch '^[a-f0-9]{32}$' -or $value.revision.verified -ne $true -or $value.revision.source_sha256 -cne $Runtime.revision.source_sha256 -or
           $value.process.token_sid -cne $layout.slots.$slot.sid -or $value.process.owned_job_membership_verified -ne $true -or
           $value.process.token_details.elevated -ne $false -or $value.process.token_details.administrators_enabled -ne $false){throw 'A complete successful restricted-worker receipt is required for every slot.'}
        $arguments='-I -B "'+$workerRoot+'\worker-denial-acceptance-r3.py" --slot '+$slot+' --nonce '+$value.nonce+' --install-receipt-sha256 '+$Runtime.install_receipt_sha256
        Assert-PassedTask (Get-TaskOrAbsent "CoChem-4.2.7-WorkerDenial-r3-$slot") $python $arguments $workerRoot
        $workers[$slot]=$control.Sha256
    }
    $knowledgeRoot='C:\Program Files\CoChem\KnowledgePublishedVerification4.2.7-windows-20261007-r2'
    $knowledge=(Read-R3Text (Read-R3Control (Join-Path $knowledgeRoot 'published-verification.json') $knowledgeHash 131072))|ConvertFrom-Json
    if($knowledge.schema -cne 'cochem-published-knowledge-verification/1' -or $knowledge.status -cne 'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED' -or
       $knowledge.system_sid -cne 'S-1-5-18' -or $knowledge.helper_sha256 -cne '9f3b6c00255d6e5c9bc0a6e6bec546ae5a60a8287c2b24a65a2f675f6d3e0496' -or
       $knowledge.nonce -cnotmatch '^[a-f0-9]{32}$' -or $knowledge.original_writer_lock_preserved -ne $true -or
       $knowledge.corpus_bytes_preserved -ne $true -or $knowledge.index_size_sla_met -ne $true){throw 'Actual accepted knowledge receipt is required.'}
    $knowledgePython='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\.venv\Scripts\python.exe'
    Assert-PassedTask (Get-TaskOrAbsent 'CoChem-4.2.7-KnowledgePublishedVerification-20261007-r2') $knowledgePython ('-I -B "'+$knowledgeRoot+'\verify-published-knowledge.py" '+$knowledge.nonce) $knowledgeRoot
    [ordered]@{schema='cochem-execution-prerequisites-inputs/1';install_receipt_sha256=$Runtime.install_receipt_sha256;knowledge_receipt_sha256=$knowledgeHash;workers=$workers}
}

function Write-NewPacket {
    param([string]$Path,$Value)
    $raw=[Text.UTF8Encoding]::new($false).GetBytes(($Value|ConvertTo-Json -Depth 6))
    if($raw.Length -gt 32768){throw 'Input packet exceeds bound.'}
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::None,(New-CodeAcl $false))
    try{$stream.Write($raw,0,$raw.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    $sha=[Security.Cryptography.SHA256]::Create();try{[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}

function Assert-WindowsKernel32 {
    # Match production validate_code_path's SystemRoot trust boundary. The
    # payload copier's ProgramFiles-only traversal is not an OS DLL validator.
    $boundary=[IO.Path]::GetFullPath($env:SystemRoot).TrimEnd('\')
    $current=Get-Item -LiteralPath (Join-Path $boundary 'System32\kernel32.dll') -Force
    if($current.Attributes -band [IO.FileAttributes]::Directory){throw 'Kernel32 is not an ordinary OS file.'}
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    while($null -ne $current){
        if($current.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Windows dependency reparse path refused.'}
        $acl=Get-Acl -LiteralPath $current.FullName
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Windows dependency has an untrusted owner.'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and
               -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
               ([int64]$rule.FileSystemRights -band 0x500D0116)){throw 'Windows dependency has an untrusted writer.'}}
        if($current.FullName.TrimEnd('\') -ieq $boundary){return}
        $parent=if($current.Attributes -band [IO.FileAttributes]::Directory){$current.Parent}else{$current.Directory}
        if($null -eq $parent -or -not ($parent.FullName.TrimEnd('\') -ieq $boundary -or $parent.FullName.StartsWith($boundary+'\',[StringComparison]::OrdinalIgnoreCase))){throw 'Windows dependency escaped SystemRoot.'}
        $current=$parent
    }
    throw 'Windows dependency trust boundary was not reached.'
}

function Assert-DockerNativeCustody {
    # Fixed built-in info/image/ps commands execute docker.exe only. Packaged
    # plugins/extensions and the already-running backend's dependencies are not
    # launched. Exact CLI PE import inspection is repeated inside SYSTEM before
    # any command: AMD64, kernel32.dll KnownDLL only, no delay imports.
    $null=Read-R3Control 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' '1aaf3dd59c24ff4c83d930bc51ecac8bf6f6f4e7a310573ee69d14ca3a7ef85c' 67108864
    $null=Read-R3Control 'C:\Program Files\Docker\Docker\resources\com.docker.backend.exe' 'c1214651f9de3a00c37b90139e5ac7ed57f0c686a6401e311ecace59854c070a' 268435456
    $key=Get-Item -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs'
    $known=$key.GetValue('*kernel32')
    if($known -cne 'kernel32.dll' -or $key.GetValueKind('*kernel32') -ne [Microsoft.Win32.RegistryValueKind]::String){throw 'Kernel32 KnownDLL registration differs.'}
    Assert-WindowsKernel32
}

function Wait-Inspection {
    param($Instance,[int]$Seconds=470)
    $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
    do{
        Start-Sleep -Milliseconds 250
        try{$Instance.Refresh()}catch{
            $e=$_.Exception;$done=$false;while($null -ne $e){if($e.HResult -eq -2147216629){$done=$true;break};$e=$e.InnerException}
            if($done){return};throw
        }
        if([DateTime]::UtcNow -gt $deadline){throw 'Inspection timed out. Preserve task/root; no retry or provisioning.'}
    }while($Instance.State -in @(2,4))
}

function Invoke-Inspection {
    param($Runtime,$Packet)
    Assert-Stopped
    $null=Assert-OriginalFailedDenial -RequireTask
    $null=Get-EvidencePacket $Runtime
    if((Test-Path -LiteralPath $root) -or $null -ne (Get-TaskOrAbsent $taskName)){throw 'Fresh inspection namespace exists; preserve it.'}
    $null=Assert-CodeTreeOnce $installRoot
    $null=Assert-CodeTreeOnce (Split-Path -Parent $basePython)
    Assert-DockerNativeCustody
    New-ProtectedDirectory $root
    New-ProtectedDirectory (Join-Path $root 'docker-client-config')
    $records=@(
        [pscustomobject]@{source=$source;destination=(Join-Path $root 'inspect-execution-prerequisites-r3.py');sha256=$sourceHash;length=(Get-Item -LiteralPath $source).Length}
        [pscustomobject]@{source=$support;destination=(Join-Path $root 'worker-denial-acceptance-r3.py');sha256=$supportHash;length=(Get-Item -LiteralPath $support).Length}
    )
    foreach($record in $records){Copy-VerifiedPayload $record;$held.Add((Open-VerifiedFile $record.destination $record.sha256 $record.length))}
    $packetPath=Join-Path $root 'inputs.json';$packetHash=Write-NewPacket $packetPath $Packet
    $held.Add((Open-VerifiedFile $packetPath $packetHash (Get-Item -LiteralPath $packetPath).Length))
    Assert-Stopped
    $nonce=[Guid]::NewGuid().ToString('N');$definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Read-only Docker/RAM prerequisite inspection after six worker successes. No existing state, credentials, startup task, container or RAM modification; no model jobs or activation. Only this fresh helper root and result are created.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true;$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT8M'
    $action=$definition.Actions.Create(0);$action.Path=$python
    $action.Arguments='-I -B "'+(Join-Path $root 'inspect-execution-prerequisites-r3.py')+'" --nonce '+$nonce+' --packet-sha256 '+$packetHash
    $action.WorkingDirectory=$root
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null);Wait-Inspection $instance
    if($task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0){throw 'Inspection task terminal state unknown; preserve evidence.'}
    $control=Read-R3Control (Join-Path $root 'execution-prerequisites.json') '' 131072
    $receipt=(Read-R3Text $control)|ConvertFrom-Json
    if($receipt.schema -cne 'cochem-execution-prerequisites/1' -or $receipt.nonce -cne $nonce -or $receipt.system_sid -cne 'S-1-5-18' -or
       $receipt.helper_sha256 -cne $sourceHash -or $receipt.packet_sha256 -cne $packetHash -or $receipt.runtime_root -cne $installRoot){throw 'Inspection receipt binding differs.'}
    $summary=[ordered]@{schema='cochem-execution-prerequisites-task-result/1';status=$receipt.status;receipt_path=$control.Stream.Name;receipt_sha256=$control.Sha256;
        last_task_result=$task.LastTaskResult;runtime_root=$installRoot;install_receipt_sha256=$Runtime.install_receipt_sha256;activation_ready=$false;existing_state_modified=$false}
    if($null -ne $receipt.PSObject.Properties['failure']){
        $f=$receipt.failure
        if($f.phase -cnotmatch '^[a-z_]{1,64}$' -or $f.error_type -cnotmatch '^[A-Za-z0-9_]{1,80}$' -or ($null -ne $f.winerror -and $f.winerror -isnot [int] -and $f.winerror -isnot [long])){throw 'Unsafe failure metadata refused.'}
        $summary.failure=[ordered]@{phase=$f.phase;error_type=$f.error_type;winerror=$f.winerror}
    }
    if($null -ne $receipt.PSObject.Properties['holds']){$summary.holds=$receipt.holds}
    if($receipt.status -cne 'READ_ONLY_PREREQUISITES_VERIFIED' -or $task.LastTaskResult -ne 0){Write-Host ($summary|ConvertTo-Json -Depth 6);throw 'Prerequisites held; preserve this root/task and do not retry or provision.'}
    if($receipt.install_receipt_sha256 -cne $Runtime.install_receipt_sha256 -or $receipt.existing_databases_modified -ne $false -or
       $receipt.existing_files_or_acls_modified -ne $false -or $receipt.activation_ready -ne $false -or @($receipt.holds).Count -ne 0){throw 'Successful receipt claims differ.'}
    Assert-Stopped;$null=Assert-OriginalFailedDenial -RequireTask;$null=Get-EvidencePacket $Runtime
    $summary|ConvertTo-Json -Depth 6
}

foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
if($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK'){throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent();$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if($Apply -and (-not $admin -or $identity.Name -cne 'AETHERDESK\ansac')){throw '-Apply requires the owner in Administrator Windows PowerShell.'}
$programFiles='C:\Program Files';$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$root='C:\Program Files\CoChem\ExecutionPrerequisites4.2.7-windows-20261007-r3';$taskName='CoChem-4.2.7-ExecutionPrerequisites-20261007-r3'
$python=Join-Path $installRoot '.venv\Scripts\python.exe';$basePython='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'
$source=Join-Path $PSScriptRoot 'inspect-execution-prerequisites-r3.py';$sourceHash='17A9A795FD755DC2E4955B1039784B4E19FD854AEE399227E9D9427428EEC41A'.ToLowerInvariant()
$support=Join-Path $PSScriptRoot 'worker-denial-acceptance-r3.py';$supportHash='b48fe231d0b2f51d211ceea7adafd580d29d8c0bba222c0e5c55c686a2f4af77'
$knowledgeHash='f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b'
$copyHelper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
$copyHash='0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b'
foreach($definition in @(Import-PinnedFunctions $copyHelper $copyHash @('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload'))){. ([scriptblock]::Create($definition))}
Initialize-FileIdentity
$leaf=Join-Path $PSScriptRoot 'check-worker-denials-r3.ps1';$leafHash='5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d'
$held=[Collections.Generic.List[IO.FileStream]]::new()
try{
    $pins=@([pscustomobject]@{path=$source;sha=$sourceHash},[pscustomobject]@{path=$support;sha=$supportHash},[pscustomobject]@{path=$leaf;sha=$leafHash})
    foreach($pin in $pins){$held.Add((Open-VerifiedFile $pin.path $pin.sha (Get-Item -LiteralPath $pin.path).Length))}
    foreach($definition in @(Import-PinnedFunctions $leaf $leafHash @('Read-R3Control','Read-R3Text','Assert-CodeTreeOnce','Assert-OriginalFailedDenialTask','Assert-OriginalFailedDenial','Assert-R3InstalledBindings'))){. ([scriptblock]::Create($definition))}
    $null=Read-R3Control $basePython 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa' 1048576
    $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\');Assert-Stopped
    $holds=@()
    if(Test-Path -LiteralPath $root){$holds+='Fresh inspection root already exists; preserve it.'}
    if($null -ne (Get-TaskOrAbsent $taskName)){$holds+='Fresh inspection task already exists; preserve it.'}
    $runtime=$null
    if(Test-Path -LiteralPath (Join-Path $installRoot 'install-after.json')){$runtime=Assert-R3InstalledBindings}else{$holds+='Reviewed r3 runtime is not installed.'}
    Assert-DockerNativeCustody
    foreach($n in 1..6){if(-not (Test-Path -LiteralPath "C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-slot$n\worker-denial-acceptance.json")){$holds+="Successful r3 slot$n receipt is not present."}}
    if(-not $Apply){[ordered]@{schema='cochem-execution-prerequisites-plan/1';mode='READ_ONLY_PLAN';helper_sha256=$sourceHash;runtime=$runtime;target_root=$root;task_name=$taskName;
        all_six_success_and_terminal_task_checks_deferred=$true;private_knowledge_receipt_checks_deferred=$true;preflight_executed=$false;
        proposed_creates=@('fresh helper root','fresh empty Docker CLI config directory','fresh SYSTEM task and receipt');existing_state_changes=0;activation_ready=$false;holds=$holds}|ConvertTo-Json -Depth 7;return}
    if($holds.Count){throw ($holds -join ' ')}
    $packet=Get-EvidencePacket $runtime
    Invoke-Inspection $runtime $packet
}finally{foreach($stream in $held){$stream.Dispose()}}
