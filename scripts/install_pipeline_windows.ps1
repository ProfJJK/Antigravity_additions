#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$OperatorName = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [string]$InstallRoot = "$env:ProgramFiles\CoChem\Pipeline4.2.2",
    [string]$DataRoot = "$env:ProgramData\CoChemPipeline422",
    [string]$TokenFile = "$env:USERPROFILE\CoChem422\controller.token",
    [ValidateRange(1,64)][int]$Slots = 6,
    [string]$Config,
    [switch]$RegisterDaemon
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

function Quote-TaskArgument {
    param([string]$Value)
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'Task arguments cannot contain quotes or line breaks.'
    }
    return '"' + $Value.TrimEnd('\') + '"'
}

function Get-TreeWithoutLinks {
    param([string]$Path)
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($Path)
    while ($queue.Count -gt 0) {
        $item = Get-Item -LiteralPath ($queue.Dequeue()) -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse points are forbidden in privileged code: $($item.FullName)" }
        $item
        if ($item.PSIsContainer) {
            foreach ($child in (Get-ChildItem -LiteralPath $item.FullName -Force)) { $queue.Enqueue($child.FullName) }
        }
    }
}

function Assert-ProtectedItem {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Privileged paths cannot use reparse points: $Path" }
    $acl = Get-Acl -LiteralPath $Path
    $trustedOwners = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $trustedOwners) { throw "Privileged code owner is not SYSTEM/Administrators/TrustedInstaller: $Path" }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($rule.AccessControlType -eq 'Allow' -and $sid -notin $trustedOwners -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)) {
            throw "Untrusted writes or replacement are possible beneath a privileged path: $Path"
        }
    }
}

function Assert-ProtectedAncestors {
    param([string]$Path)
    $current = Get-Item -LiteralPath $Path
    while ($null -ne $current -and $current.FullName.TrimEnd('\') -ne $env:ProgramFiles.TrimEnd('\')) {
        Assert-ProtectedItem -Path $current.FullName
        $current = $current.Parent
    }
    Assert-ProtectedItem -Path $env:ProgramFiles
}

function Protect-InstalledTree {
    param([string]$Path)
    foreach ($item in (Get-TreeWithoutLinks -Path $Path)) {
        $acl = if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        $inherit = if ($item.PSIsContainer) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
        foreach ($grant in @(@('S-1-5-18','FullControl'),@('S-1-5-32-544','FullControl'),@('S-1-5-32-545','ReadAndExecute'))) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($grant[0]),[Security.AccessControl.FileSystemRights]$grant[1],$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        Set-Acl -LiteralPath $item.FullName -AclObject $acl
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This installer requires Windows and an elevated administrator PowerShell.'
}
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$DataRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$TokenFile = [IO.Path]::GetFullPath($TokenFile)
$programFilesPrefix = [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\') + '\'
if (-not $Python.StartsWith($programFilesPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use a machine-wide Python 3.12+ installed beneath Program Files. SYSTEM must never import code from an agent-writable Python installation.'
}
if (-not $InstallRoot.StartsWith($programFilesPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'InstallRoot must be a dedicated versioned directory beneath Program Files.'
}
Assert-ProtectedAncestors -Path (Split-Path -Parent $Python)
foreach ($item in (Get-TreeWithoutLinks -Path (Split-Path -Parent $Python))) { Assert-ProtectedItem -Path $item.FullName }
Invoke-Checked -Executable $Python -Arguments @('-c','import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))')

$installedPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$sourceRoot = Join-Path $InstallRoot 'source'
$layoutFile = Join-Path $InstallRoot 'windows-layout.json'
$provisionLog = Join-Path $InstallRoot 'provision.log'
if (Test-Path -LiteralPath $InstallRoot) {
    if (-not (Test-Path -LiteralPath $installedPython -PathType Leaf)) {
        throw 'InstallRoot already exists without a completed installation. Preserve it and choose a fresh versioned InstallRoot.'
    }
    Assert-ProtectedAncestors -Path $InstallRoot
    foreach ($item in (Get-TreeWithoutLinks -Path $InstallRoot)) { Assert-ProtectedItem -Path $item.FullName }
    Write-Host "Preserving existing protected installation: $InstallRoot"
}
else {
    $installParent = Split-Path -Parent $InstallRoot
    if (-not (Test-Path -LiteralPath $installParent)) {
        New-Item -ItemType Directory -Path $installParent -Force | Out-Null
        Protect-InstalledTree -Path $installParent
    }
    Assert-ProtectedAncestors -Path $installParent
    New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'src') -Destination (Join-Path $sourceRoot 'src') -Recurse
    Copy-Item -LiteralPath (Join-Path $repo 'pyproject.toml'),(Join-Path $repo 'README.md') -Destination $sourceRoot
    Invoke-Checked -Executable $Python -Arguments @('-m','venv','--copies',(Join-Path $InstallRoot '.venv'))
    Invoke-Checked -Executable $installedPython -Arguments @('-m','pip','install',($sourceRoot + '[mcp]'))
    # All code, packages and task helpers are machine protected. No editable
    # installation points the SYSTEM interpreter back into the user checkout.
    Protect-InstalledTree -Path $InstallRoot
}

$provisionArgs = @('-m','cochem_pipeline.windows','provision',
    '--private-root',(Join-Path $DataRoot 'private'),'--workers-root',(Join-Path $DataRoot 'workers'),
    '--slots',"$Slots",'--operator-name',$OperatorName,'--controller-token',$TokenFile,'--layout-output',$layoutFile)
$provisionScript = Join-Path $InstallRoot 'provision-task.ps1'
# Serialized argument values are nonsecret paths/account names. The worker
# account passwords are generated and retained inside SYSTEM Credential Manager.
$scriptArgs = ($provisionArgs | ForEach-Object { "'" + $_.Replace("'","''") + "'" }) -join ','
$body = "`$ErrorActionPreference='Stop'`r`n& '" + $installedPython.Replace("'","''") + "' @(" + $scriptArgs + ") *> '" + $provisionLog.Replace("'","''") + "'`r`nexit `$LASTEXITCODE`r`n"
[IO.File]::WriteAllText($provisionScript,$body,[Text.UTF8Encoding]::new($false))
Protect-InstalledTree -Path $InstallRoot
$taskName = 'CoChem-4.2.2-Provision'
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + (Quote-TaskArgument $provisionScript))
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
$requestedStart = Get-Date
Start-ScheduledTask -TaskName $taskName
$deadline = (Get-Date).AddMinutes(10)
do {
    Start-Sleep -Milliseconds 500
    $task = Get-ScheduledTask -TaskName $taskName
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    if ((Get-Date) -gt $deadline) { throw "SYSTEM provisioning timed out. Inspect $provisionLog" }
} while ($task.State -in @('Running','Queued') -or $info.LastRunTime -lt $requestedStart.AddSeconds(-1))
if ($info.LastTaskResult -ne 0) { throw "SYSTEM provisioning failed ($($info.LastTaskResult)). Inspect $provisionLog" }
Write-Host "Provisioned security layout: $layoutFile"
Write-Host 'Worker passwords remain in SYSTEM Credential Manager. Authenticate each CLI separately in each worker profile using login_pipeline_worker.ps1.'

if ($RegisterDaemon) {
    if (-not $Config) { throw '-RegisterDaemon requires a reviewed -Config JSON file with real native CLI paths and model IDs.' }
    $configSource = (Resolve-Path -LiteralPath $Config).Path
    $configTarget = Join-Path $InstallRoot 'pipeline.json'
    if (Test-Path -LiteralPath $configTarget) {
        if ((Get-FileHash -LiteralPath $configSource).Hash -ne (Get-FileHash -LiteralPath $configTarget).Hash) {
            throw 'Existing installed pipeline.json differs. Review it explicitly; this installer does not overwrite configuration.'
        }
    }
    else { Copy-Item -LiteralPath $configSource -Destination $configTarget }
    Protect-InstalledTree -Path $InstallRoot
    $daemonAction = New-ScheduledTaskAction -Execute $installedPython -Argument ('-m cochem_pipeline daemon --config ' + (Quote-TaskArgument $configTarget)) -WorkingDirectory $InstallRoot
    $daemonSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'CoChem-4.2.2-Warden' -Action $daemonAction -Principal $principal -Settings $daemonSettings -Trigger (New-ScheduledTaskTrigger -AtStartup) -Force | Out-Null
    Write-Host 'Registered SYSTEM Warden task. After per-worker logins and config review, start it and run the native Windows acceptance tests: Start-ScheduledTask CoChem-4.2.2-Warden'
}
else {
    Write-Host 'Daemon registration is pending: merge windows-layout.json into the reviewed pipeline config, then rerun with -Config PATH -RegisterDaemon.'
}
