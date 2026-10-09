#Requires -Version 5.1
<# Read-only plan by default. -Apply is an explicit one-shot, non-inference
   native authentication status check after stopped provisioning and human login.
   No native command, task, or protected directory is created in plan mode. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('slot1','slot2','slot3','slot4','slot5','slot6')][string]$Slot,
    [Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Slot=$Slot.ToLowerInvariant();$Provider=$Provider.ToLowerInvariant()
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK') {throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
if ($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')) {throw '-Apply requires the owner in Administrator Windows PowerShell; nothing was executed.'}
$programFiles='C:\Program Files'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006'
$targetRoot="C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261006-$Slot-$Provider"
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
$basePython=Join-Path $basePythonRoot 'python.exe'
$venvConfig=Join-Path $installRoot '.venv\pyvenv.cfg'
$layout=Join-Path $installRoot 'windows-layout.json'
$config=Join-Path $installRoot 'pipeline.json'
$native="C:\Program Files\CoChem\Native4.2.7-windows-20261006\$Provider.exe"
$nativeHash=if ($Provider -eq 'codex') {'fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d'} else {'0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469'}
$taskName="CoChem-4.2.7-NativeStatus-$Slot-$Provider"
$source=Join-Path $PSScriptRoot 'worker-native-status.py'
$sourceHash='8b19d224effd975423b6d9fb02f79fa949d92e73a131d710ac95de55c8580a52'
$helper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
# Reuse only exact reviewed copy/ACL function definitions, never helper actions.
$stream=[IO.File]::Open($helper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
    if ($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') {throw 'Reviewed protected-copy helper changed.'}
    $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) {throw 'Protected-copy helper parse error.'}
    $names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')
    foreach ($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {if ($function.Name -in $names) {. ([scriptblock]::Create($function.Extent.Text))}}
} finally {$stream.Dispose()}
Initialize-FileIdentity
$sourceLength=(Get-Item -LiteralPath $source).Length
$stream=Open-VerifiedFile $source $sourceHash $sourceLength;$stream.Dispose()
$holds=@()
foreach ($path in @($python,$layout,$config,$native,$basePython,$venvConfig)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {$holds+='A required stopped installation file is absent: '+$path}
    else {Assert-ProtectedPath $path}
}
if (Test-Path -LiteralPath $native -PathType Leaf) {
    $stream=Open-VerifiedFile $native $nativeHash (Get-Item -LiteralPath $native).Length;$stream.Dispose()
}
if (Test-Path -LiteralPath $targetRoot) {$holds+='This one-shot status root already exists; preserve its evidence, do not overwrite or rerun automatically.'}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
function Get-ExactTaskOrAbsent {
    param([string]$Name)
    try {return $folder.GetTask($Name)} catch {
        $exception=$_.Exception
        while ($null -ne $exception) {if ($exception.HResult -eq -2147024894) {return $null};$exception=$exception.InnerException}
        throw 'Cannot establish exact Task Scheduler state; no native check is authorized by uncertainty.'
    }
}
if ($null -ne (Get-ExactTaskOrAbsent $taskName)) {$holds+='This one-shot status task already exists; preserve it.'}
foreach ($name in @('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor')) {
    $daemon=Get-ExactTaskOrAbsent $name
    if ($null -ne $daemon -and ($daemon.Enabled -or $daemon.State -notin @(1,3) -or $daemon.GetInstances(0).Count -ne 0)) {$holds+='Protected daemons must remain stopped and disabled: '+$name}
}
function Assert-VenvBinding {
    param([string]$Text)
    $settings=@{}
    foreach($line in ($Text -split '\r?\n')) {
        if(-not $line.Trim()){continue}
        if($line -notmatch '^([a-z-]+) = (.+)$' -or $settings.ContainsKey($matches[1])){throw 'Malformed or duplicate virtual environment setting.'}
        $settings[$matches[1]]=$matches[2]
    }
    if($settings['home'] -cne $basePythonRoot -or $settings['executable'] -cne $basePython -or $settings['version'] -cne '3.12.13' -or $settings['include-system-site-packages'] -cne 'false'){throw 'Virtual environment does not bind the exact reviewed protected Python.'}
}
foreach($pin in @(@($python,'560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e'),@($basePython,'d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'),@($venvConfig,'d6ebb0d905488486e5a438a8a30a589f256f3499515baf1dd9d4ab71693b5f95'))){
    if(Test-Path -LiteralPath $pin[0] -PathType Leaf){$stream=Open-VerifiedFile $pin[0] $pin[1] (Get-Item -LiteralPath $pin[0]).Length;try{if($pin[0] -eq $venvConfig){$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);Assert-VenvBinding $reader.ReadToEnd()}}finally{$stream.Dispose()}}
}
$plan=[ordered]@{schema='cochem-worker-native-status-plan/1';mode='READ_ONLY_PLAN';slot=$Slot;provider=$Provider;owner=$identity.Name;administrator=$admin;source_sha256=$sourceHash;native_sha256=$nativeHash;task_name=$taskName;target_root=$targetRoot;native_status_timeout_seconds=30;native_output_limit_bytes=65536;native_status_commands_executed=0;native_model_jobs_executed=0;logins_executed=0;pipeline_started=$false;holds=$holds}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;return}
if ($holds.Count) {throw ($holds -join ' ')}
function Assert-CodeTreeOnce {
    param([string]$Path)
    # Root ancestry is checked once. Protected parents cannot be replaced by an
    # untrusted writer; each child still receives its own ACL and handle check.
    Assert-ProtectedPath $Path
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path)
    $count=0;$deadline=[DateTime]::UtcNow.AddMinutes(4)
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    while($queue.Count){
        if(++$count -gt 50000 -or [DateTime]::UtcNow -gt $deadline){throw 'Protected code inspection exceeded its bound; no Python was executed.'}
        $item=Get-Item -LiteralPath ($queue.Dequeue()) -Force -ErrorAction Stop
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Protected code reparse entry refused.'}
        $acl=Get-Acl -LiteralPath $item.FullName
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Protected code has an untrusted owner.'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            if($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)){throw 'Protected code has an untrusted writer.'}
        }
        if($item.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $item.FullName -Force){$queue.Enqueue($child.FullName)}}else{
            # Handle metadata only, including bundled .db resources; no content read.
            $stream=[IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try{[CoChemStagedFileIdentity]::Check($stream,$item.FullName)}finally{$stream.Dispose()}
        }
    }
    $count
}
$installedEntries=Assert-CodeTreeOnce $installRoot
$baseEntries=Assert-CodeTreeOnce $basePythonRoot
$nativeEntries=Assert-CodeTreeOnce (Split-Path -Parent $native)
New-ProtectedDirectory $targetRoot
$destination=Join-Path $targetRoot 'worker-native-status.py'
Copy-VerifiedPayload ([pscustomobject]@{source=$source;destination=$destination;sha256=$sourceHash;length=$sourceLength})
$nonce=[Guid]::NewGuid().ToString('N')
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='One bounded native authentication-status command in one isolated worker profile. No login, model inference, daemon activation, credential copying or budget changes.'
$definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true
$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT3M'
$action=$definition.Actions.Create(0);$action.Path=$python
$action.Arguments='-I -B "'+$destination+'" --slot '+$Slot+' --provider '+$Provider+' --nonce '+$nonce
$action.WorkingDirectory=$targetRoot
$task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
function Wait-StatusTaskInstance {
    param($Instance,[int]$TimeoutSeconds=175)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try {$Instance.Refresh()} catch {
            $completed=$false;$exception=$_.Exception
            while ($null -ne $exception) {if ($exception.HResult -eq -2147216629) {$completed=$true;break};$exception=$exception.InnerException}
            # SCHED_E_TASK_NOT_RUNNING ends polling only; it never proves success.
            if ($completed) {return}
            throw
        }
        if ([DateTime]::UtcNow -gt $deadline) {throw 'Status task wait timed out. Preserve task/root; its limit is three minutes. Cleanup is unverified: keep all pipeline tasks disabled and do not retry automatically.'}
    }while($Instance.State -in @(2,4))
}
$instance=$task.Run($null)
Wait-StatusTaskInstance $instance
function Assert-CompletedStatusTask {
    param($Task)
    if($Task.State -notin @(1,3) -or $Task.GetInstances(0).Count -ne 0){throw 'Status task is not conclusively terminal; preserve root/task and refuse its receipt.'}
}
Assert-CompletedStatusTask $task
$receiptPath=Join-Path $targetRoot 'worker-native-status.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {throw ('Native status check has no receipt. Preserve task and keep daemons disabled. LastTaskResult='+$task.LastTaskResult)}
Assert-ProtectedPath $receiptPath
if ((Get-Item -LiteralPath $receiptPath).Length -gt 32768) {throw 'Native status receipt exceeds its bound.'}
$receipt=Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8|ConvertFrom-Json
if ($receipt.schema -ne 'cochem-worker-native-status/1' -or $receipt.nonce -ne $nonce -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.helper_sha256 -ne $sourceHash -or $receipt.slot -ne $Slot -or $receipt.provider -ne $Provider) {throw 'Native status receipt binding differs; preserve for review.'}
[ordered]@{schema='cochem-worker-native-status-task-result/1';slot=$Slot;provider=$Provider;receipt_path=$receiptPath;receipt_sha256=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant();status=$receipt.status;cleanup_verified=$receipt.cleanup_verified;last_task_result=$task.LastTaskResult;task_preserved=$true;native_model_jobs_executed=0;serving_model_verified=$false;ready_for_inference=$false;pipeline_started=$false}|ConvertTo-Json -Depth 4
if ($receipt.status -ne 'NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED' -or $receipt.cleanup_verified -ne $true -or $task.LastTaskResult -ne 0) {throw 'Native authentication status did not pass. Keep daemons disabled; review the redacted receipt. Do not retry or copy credentials.'}
