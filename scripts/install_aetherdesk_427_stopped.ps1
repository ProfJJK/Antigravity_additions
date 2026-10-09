#Requires -Version 5.1
<# Default is a read-only guard. Explicit -Apply stages frozen source, runs a
bounded read-only SYSTEM credential-presence precheck, then calls ONLY the
pipeline installer without registration. Existing state is refused, not repaired.
This fresh-only wrapper never migrates budgets or activates a daemon. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourceManifest,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$SourceManifestSha256,
    [switch]$Apply
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use Windows PowerShell 5.1.' }
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\CimCmdlets\CimCmdlets.psd1') -ErrorAction Stop

function Assert-TaskAbsent {
    param($Folder,[string]$Name)
    try { $null=$Folder.GetTask($Name) }
    catch {
        $exception=$_.Exception
        while ($null -ne $exception) {
            if ($exception.HResult -eq -2147024894) { return } # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND) only
            $exception=$exception.InnerException
        }
        throw "Task inspection failed for $Name; access denied or uncertain state is not absence. $($_.Exception.Message)"
    }
    throw "Preserve existing scheduled task: $Name"
}
function Assert-FreshDeployment {
    param($Folder,[switch]$GuardCreated)
    foreach ($name in @('CoChem-4.2.2-Provision','CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor')) { Assert-TaskAbsent $Folder $name }
    if (-not $GuardCreated) { Assert-TaskAbsent $Folder 'CoChem-4.2.7-Install-Preflight' }
    $paths=@($script:installRoot,$script:dataRoot,$script:tokenFile)
    if (-not $GuardCreated) { $paths+=@($script:guardRoot) }
    foreach ($path in $paths) { if (Test-Path -LiteralPath $path -ErrorAction Stop) { throw "Fresh-only deployment refuses existing state; preserve and review: $path" } }
    $names=@(1..6 | ForEach-Object {"CoChem422Worker$_"})
    if (@(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop | Where-Object {$_.Name -in $names}).Count) { throw 'Existing worker accounts require reviewed reuse; this fresh-only guard never changes them.' }
}
function Get-StoppedInstallerParameters {
    [ordered]@{Python='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe';
      Uv='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe';
      OperatorName='AETHERDESK\ansac';InstallRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006';
      DataRoot='C:\ProgramData\CoChemPipeline427';TokenFile='C:\Users\ansac\CoChem427\controller.token';
      WardenTaskName='CoChem-4.2.7-Warden';SupervisorTaskName='CoChem-4.2.7-Supervisor';Slots=6;RefuseProvisionTaskOverwrite=$true}
}
function Get-StoppedInstallerArguments {
    foreach ($entry in (Get-StoppedInstallerParameters).GetEnumerator()) {
        '-'+$entry.Key
        if ($entry.Value -isnot [bool]) {[string]$entry.Value}
    }
}
function Invoke-StoppedInstaller {
    param([string]$Path)
    # Hashtable splatting preserves PowerShell parameter binding. The argv
    # strings emitted for review are not executable PowerShell parameter syntax.
    $parameters=Get-StoppedInstallerParameters
    & $Path @parameters
    if (-not $?) {throw 'Stopped pipeline installer failed; preserve its partial state and logs.'}
}
function Get-GuardFiles {
    param($Inventory)
    if ($Inventory.schema -ne 'cochem-stopped-install-inventory/1' -or $Inventory.host -ne 'AETHERDESK' -or $Inventory.max_execution_slots -ne 4 -or $Inventory.worker_identities -ne 6) { throw 'Unexpected stopped installation inventory or capacity.' }
    $required=@('identity-precheck.ps1','repository/scripts/install_pipeline_windows.ps1','repository/scripts/login_pipeline_worker.ps1','repository/pyproject.toml','repository/uv.lock','repository/README.md','repository/4.2.7_SRS.md','repository/docs/SRS_ADDENDUM_4.2.7.md','repository/docs/WINDOWS_CODEX_HANDOFF_4.2.7.md','repository/config/windows/aetherdesk-427.proposed.json')
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $files=@($Inventory.files)
    if ($files.Count -lt $required.Count -or $files.Count -gt 10000) { throw 'Unexpected source file count.' }
    $bytes=[long]0
    foreach ($file in $files) {
        $relative=[string]$file.destination
        if ($relative.Contains('\') -or $relative -match '[\x00-\x1f<>:"|?*]' -or @($relative.Split('/') | Where-Object {$_ -in @('','.', '..') -or $_.EndsWith(' ') -or $_.EndsWith('.') -or $_ -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)'}).Count) { throw 'Unsafe frozen source destination.' }
        if (-not $seen.Add($relative) -or ($relative -notin $required -and -not $relative.StartsWith('repository/src/'))) { throw 'Unexpected or duplicate frozen source destination.' }
        if ($file.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $file.length -lt 0 -or $file.length -gt 67108864) { throw 'Invalid frozen source digest or size.' }
        $bytes+=[long]$file.length
        if ($bytes -gt 268435456) { throw 'Frozen source size exceeds 256 MiB.' }
        [pscustomobject]@{source=(Get-LocalPath $file.source);destination=(Join-Path $script:guardRoot $relative);sha256=$file.sha256;length=[long]$file.length}
    }
    foreach ($name in $required) { if (-not $seen.Contains($name)) { throw "Missing frozen source asset: $name" } }
    if (-not @($seen | Where-Object {$_.StartsWith('repository/src/cochem_pipeline/')}).Count) { throw 'Pipeline source is absent.' }
}
function Assert-IdentityReceipt {
    param($Receipt,[string]$Nonce)
    if ($Receipt.schema -ne 'cochem-fresh-identity-precheck/1' -or $Receipt.nonce -ne $Nonce -or $Receipt.system_sid -ne 'S-1-5-18' -or $Receipt.status -ne 'FRESH_TARGETS_ABSENT' -or $Receipt.local_accounts_checked -ne 6 -or $Receipt.credential_targets_checked -ne 6 -or $Receipt.credential_blobs_dereferenced -ne $false) { throw 'Fresh SYSTEM identity receipt is missing, stale or inconsistent.' }
}
function Invoke-FreshIdentityPrecheck {
    param($Scheduler,$Folder)
    Assert-TaskAbsent $Folder 'CoChem-4.2.7-Install-Preflight'
    $nonce=[Guid]::NewGuid().ToString('N')
    $definition=$Scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='CoChem 4.2.7 fresh identity presence check only; no credential values or account mutations.'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.AllowDemandStart=$true
    $definition.Settings.MultipleInstances=2;$definition.Settings.ExecutionTimeLimit='PT2M'
    $action=$definition.Actions.Create(0)
    $action.Path="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $action.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $script:guardRoot 'identity-precheck.ps1')+'" -Nonce '+$nonce
    $action.WorkingDirectory=$script:guardRoot
    # TASK_CREATE (2), never CREATE_OR_UPDATE or -Force. Denied/duplicate fails.
    $task=$Folder.RegisterTaskDefinition('CoChem-4.2.7-Install-Preflight',$definition,2,'SYSTEM',$null,5,'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')
    $instance=$task.Run($null)
    $deadline=[DateTime]::UtcNow.AddSeconds(75)
    do {
        Start-Sleep -Milliseconds 250
        $instance.Refresh()
        if ([DateTime]::UtcNow -gt $deadline) { throw 'SYSTEM identity precheck timed out. Preserve its task and protected receipt directory; do not retry/reset automatically.' }
    } while ($instance.State -in @(2,4))
    if ($task.LastTaskResult -ne 0) { throw "SYSTEM identity precheck failed ($($task.LastTaskResult)); no pipeline installation was invoked." }
    $path=Join-Path $script:guardRoot 'identity-precheck.json'
    Assert-ProtectedPath $path
    if ((Get-Item -LiteralPath $path).Length -gt 4096) { throw 'Unexpected identity receipt size.' }
    $receipt=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-IdentityReceipt $receipt $nonce
}

# Import only hash-bound function definitions from the frozen copy helper. Do
# not dot-source its invocation boundary or alter the helper already issued.
$copyHelper=Join-Path $PSScriptRoot 'stage_aetherdesk_427_payloads.ps1'
$copyStream=[IO.File]::Open($copyHelper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
    $sha=[Security.Cryptography.SHA256]::Create()
    try {$hash=[BitConverter]::ToString($sha.ComputeHash($copyStream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if ($hash -ne '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b') { throw 'Frozen copy helper changed.' }
    $copyStream.Position=0;$reader=[IO.StreamReader]::new($copyStream,[Text.Encoding]::UTF8)
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($reader.ReadToEnd(),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw 'Frozen copy helper parser errors.' }
    $needed=@('Get-LocalPath','Assert-NoReparseAncestors','Assert-ProtectedPath','New-CodeAcl','New-ProtectedDirectory','Initialize-FileIdentity','Open-VerifiedFile','Read-Inventory','Copy-VerifiedPayload')
    foreach ($function in $ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst]},$true)) { if ($function.Name -in $needed) {. ([scriptblock]::Create($function.Extent.Text))} }
} finally {$copyStream.Dispose()}
if ($env:COMPUTERNAME -ne 'AETHERDESK' -or $env:ProgramFiles -ne 'C:\Program Files') { throw 'This reviewed guard targets AETHERDESK and its explicit C: paths.' }
$programFiles='C:\Program Files';$base='C:\Program Files\CoChem'
$guardRoot=Join-Path $base 'InstallGuard4.2.7-windows-20261006'
$installRoot=Join-Path $base 'Pipeline4.2.7-windows-20261006'
$dataRoot='C:\ProgramData\CoChemPipeline427';$tokenFile='C:\Users\ansac\CoChem427\controller.token'
Initialize-FileIdentity
$inventory=Read-Inventory $SourceManifest $SourceManifestSha256
$files=@(Get-GuardFiles $inventory)
foreach ($file in $files) {$stream=Open-VerifiedFile $file.source $file.sha256 $file.length;$stream.Dispose()}
$scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect();$folder=$scheduler.GetFolder('\')
Assert-FreshDeployment $folder
$payloadInventory=Read-Inventory (Join-Path $PSScriptRoot '..\config\windows\aetherdesk-427.payloads.json') 'db9e6567095326bbeaf528a400525ee047a7887b5a5479bf8ac0ba6f8b4625d0'
$missing=0
foreach ($file in $payloadInventory.files) {
    if (-not $file.destination.StartsWith('Toolchain4.2.7-windows-20261006/')) {continue}
    $path=Join-Path $base $file.destination
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {$missing++;continue}
    Assert-ProtectedPath $path
    $stream=Open-VerifiedFile $path $file.sha256 $file.length;$stream.Dispose()
}
$invoked=$false
if ($Apply) {
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw '-Apply requires an elevated administrator; no installation was invoked.'}
    if ($missing) {throw 'Reviewed protected toolchain is incomplete; no installation was invoked.'}
    Assert-ProtectedPath $base
    New-ProtectedDirectory $guardRoot
    $directories=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $files) {$parent=Split-Path -Parent $file.destination;while($parent -ne $guardRoot){$null=$directories.Add($parent);$parent=Split-Path -Parent $parent}}
    foreach ($directory in ($directories|Sort-Object Length)) {New-ProtectedDirectory $directory}
    foreach ($file in $files) {Copy-VerifiedPayload $file}
    Invoke-FreshIdentityPrecheck $scheduler $folder
    Assert-FreshDeployment $folder -GuardCreated
    $installer=Join-Path $guardRoot 'repository\scripts\install_pipeline_windows.ps1'
    $invoked=$true
    Invoke-StoppedInstaller $installer
}
[ordered]@{schema='cochem-stopped-install-guard/1';mode=$(if($invoked){'STOPPED_PIPELINE_INSTALL_INVOKED'}else{'READ_ONLY_PLAN'});source_manifest_sha256=$SourceManifestSha256.ToLowerInvariant();source_files_verified=$files.Count;missing_toolchain_files=$missing;max_execution_slots=4;worker_identities=6;system_credential_precheck=$(if($invoked){'PASSED_FRESH_TARGETS_ABSENT'}else{'REQUIRES_ACTUAL_SYSTEM'});installer_invoked=$invoked;daemon_registered=$false;supervisor_migration_invoked=$false;activation_ready=$false;installer_arguments=@(Get-StoppedInstallerArguments)}|ConvertTo-Json -Depth 5
