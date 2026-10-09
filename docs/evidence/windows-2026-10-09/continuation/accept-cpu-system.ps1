#Requires -Version 5.1
<# Read-only plan by default. After stopped installation, an explicit owner-run
   -Apply creates one protected script/receipt root and one no-trigger SYSTEM
   task for one bounded CPU sample. It never installs drivers or starts daemons.
   Existing task/root/receipt are preserved and refused, never reset or retried. #>
[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Apply -and (-not $admin -or $identity.Name -ne 'AETHERDESK\ansac')) {throw '-Apply requires the owner in an Administrator Windows PowerShell; nothing was created or executed.'}
if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess -or $env:COMPUTERNAME -ne 'AETHERDESK') {throw 'Use 64-bit Windows PowerShell 5.1 on AETHERDESK.'}
$programFiles='C:\Program Files'
$acceptRoot='C:\Program Files\CoChem\CpuAcceptance4.2.7-windows-20261006'
$python='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe'
$probeManifest='C:\Program Files\CoChem\CpuSensors4.2.7-windows-20261006\cochem-cpu-temperature.manifest.json'
$probeManifestHash='defbb5b713e1cd81316b0d16dc16a49c37e9c170365c7699b18a47be6f24ef65'
$taskName='CoChem-4.2.7-CPU-Acceptance'
$source=Join-Path $PSScriptRoot 'cpu-system-acceptance.py'
$sourceHash='e3b4b1055254baaf2a4b68f7bd39a225b9c9b2c987d348e63ea34f607b0e8b0b'
$helper='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1'
# Import only the exact already-reviewed copy/ACL functions, never its actions.
$stream=[IO.File]::Open($helper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if ($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') {throw 'Reviewed protected-copy helper changed.'}
    $stream.Position=0;$reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) {throw 'Protected-copy helper parse error.'}
    $names=@('Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')
    foreach ($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) {if ($function.Name -in $names) {. ([scriptblock]::Create($function.Extent.Text))}}
}finally{$stream.Dispose()}
Initialize-FileIdentity
$sourceLength=(Get-Item -LiteralPath $source).Length
$stream=Open-VerifiedFile $source $sourceHash $sourceLength;$stream.Dispose()
Assert-ProtectedPath $probeManifest
$stream=Open-VerifiedFile $probeManifest $probeManifestHash (Get-Item -LiteralPath $probeManifest).Length;$stream.Dispose()
$holds=@()
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {$holds+='Stopped pipeline and its protected installed Python are not present.'}else{Assert-ProtectedPath $python}
if (Test-Path -LiteralPath $acceptRoot) {$holds+='Acceptance root already exists; preserve its artifacts and review, never overwrite/retry.'}
$driver=@(Get-CimInstance Win32_SystemDriver -Filter "Name='PawnIO'" -OperationTimeoutSec 10 -ErrorAction Stop)
if ($driver.Count -ne 1 -or $driver[0].State -ne 'Running') {$holds+='PawnIO is not observed running; do not load, reinstall or repair it through this helper.'}
$plan=[ordered]@{schema='cochem-system-cpu-plan/1';mode='READ_ONLY_PLAN';owner=$identity.Name;administrator=$admin;task_name=$taskName;target_root=$acceptRoot;python=$python;source_sha256=$sourceHash;bundle_manifest_sha256=$probeManifestHash;sample_limit_seconds=3;output_limit_bytes=32768;task_limit_seconds=120;holds=$holds;task_created=$false;temperature_sampled=$false;device_containment_tested=$false;pipeline_started=$false}
if (-not $Apply) {$plan|ConvertTo-Json -Depth 5;return}
if ($holds.Count) {throw ($holds -join ' ')}
function Wait-CpuTaskInstance {
    param($Instance,[int]$TimeoutSeconds=75)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try {$Instance.Refresh()}catch{
            $exception=$_.Exception;$completed=$false
            # SCHED_E_TASK_NOT_RUNNING: a short task may finish before Refresh.
            # This only ends polling. The caller still verifies task result and
            # the protected receipt's exact nonce, SYSTEM identity and digest.
            while ($null -ne $exception) {if ($exception.HResult -eq -2147216629) {$completed=$true;break};$exception=$exception.InnerException}
            if ($completed) {return}
            throw
        }
        if ([DateTime]::UtcNow -gt $deadline) {throw 'CPU acceptance wait timed out. Preserve this unique task/root; scheduler limit is 2 minutes. Do not rerun automatically.'}
    }while($Instance.State -in @(2,4))
}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
$absent=$false
try {$null=$folder.GetTask($taskName)}catch{
    $exception=$_.Exception
    # Only ERROR_FILE_NOT_FOUND (0x80070002) proves absence. Task Scheduler
    # account/configuration errors are existing or uncertain state, never absent.
    while ($null -ne $exception) {if ($exception.HResult -eq -2147024894) {$absent=$true;break};$exception=$exception.InnerException}
    if (-not $absent) {throw 'Cannot establish acceptance-task absence; no changes are allowed.'}
}
if (-not $absent) {throw 'Existing CPU acceptance task is preserved; no update, run or deletion is allowed.'}
New-ProtectedDirectory $acceptRoot
$destination=Join-Path $acceptRoot 'cpu-system-acceptance.py'
Copy-VerifiedPayload ([pscustomobject]@{source=$source;destination=$destination;sha256=$sourceHash;length=$sourceLength})
$nonce=[Guid]::NewGuid().ToString('N')
$definition=$scheduler.NewTask(0)
$definition.RegistrationInfo.Description='One bounded CPU-only acceptance sample. No daemon, GPU probe, driver install, credentials or data migration.'
$definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
$definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true
$definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT2M'
$action=$definition.Actions.Create(0);$action.Path=$python
$action.Arguments='-I "'+$destination+'" '+$nonce;$action.WorkingDirectory=$acceptRoot
$task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
$instance=$task.Run($null)
Wait-CpuTaskInstance $instance
$receiptPath=Join-Path $acceptRoot 'cpu-system-acceptance.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {throw ('CPU acceptance exited without a receipt; preserve task. LastTaskResult='+$task.LastTaskResult)}
Assert-ProtectedPath $receiptPath
if ((Get-Item -LiteralPath $receiptPath).Length -gt 32768) {throw 'CPU acceptance receipt exceeds its bound.'}
$receipt=Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8|ConvertFrom-Json
if ($receipt.schema -ne 'cochem-system-cpu-acceptance/1' -or $receipt.nonce -ne $nonce -or $receipt.system_sid -ne 'S-1-5-18' -or $receipt.script_sha256 -ne $sourceHash) {throw 'CPU acceptance receipt identity differs; preserve for review.'}
[ordered]@{schema='cochem-system-cpu-task-result/1';receipt_path=$receiptPath;receipt_sha256=(Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant();status=$receipt.status;last_task_result=$task.LastTaskResult;task_name=$taskName;task_preserved=$true;device_containment_tested=$false;pipeline_started=$false}|ConvertTo-Json -Depth 4
if ($receipt.status -ne 'CPU_SAMPLE_VALID' -or $task.LastTaskResult -ne 0) {throw 'CPU sample did not pass; inspect the preserved receipt, do not retry or weaken the sensor guard.'}
