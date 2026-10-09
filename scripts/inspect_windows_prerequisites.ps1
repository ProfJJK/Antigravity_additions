#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputPath)

# Read-only system inspection. The sole write is a new report at OutputPath.
# Does not create/stop tasks, provision accounts, load drivers or alter R:.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
if ($OutputPath -notmatch '^[A-Za-z]:[\\/]' -or $OutputPath.Contains("`0") -or $OutputPath.Substring(2).Contains(':')) { throw 'OutputPath must be an absolute local drive path without alternate streams.' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$privateStage = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CoChem\staging')).TrimEnd('\')
if (-not $OutputPath.StartsWith($privateStage+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Write inspection evidence only beneath the current user LocalAppData\CoChem\staging.' }
if (Test-Path -LiteralPath $OutputPath) { throw 'OutputPath already exists; preserve the earlier evidence.' }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $OutputPath) -PathType Container)) { throw 'Output directory must already exist.' }
$parent = Get-Item -LiteralPath (Split-Path -Parent $OutputPath) -Force
while ($null -ne $parent) {
    if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Inspection destination ancestors must not be reparse points.' }
    $parent = $parent.Parent
}

function Write-NewInspectionReport {
    param([string]$Path, [string]$Json)
    $stream = [IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Json)
        $stream.Write($bytes,0,$bytes.Length)
    } finally { $stream.Dispose() }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$report = [ordered]@{
    schema = 'cochem-windows-prerequisites/1'
    observed_at_utc = [DateTime]::UtcNow.ToString('o')
    scope = 'Read-only task, volume and CPU sensor discovery; no deployment mutation'
    identity = $identity.Name
    administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    system = ($identity.User.Value -eq 'S-1-5-18')
    script_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    tasks = @()
    ram_volume = $null
    hardware_monitor = @()
    cpu_sensors = @()
    holds = @()
}
foreach ($name in @('CoChem_EnsureRamdisk','CoChemHostWarden_V412','CoChem V4.1.2 Swarm','CoChem_Ecosystem_Backup')) {
    try {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $report.tasks += [ordered]@{
            name = $task.TaskName; path = $task.TaskPath; state = [string]$task.State
            user = $task.Principal.UserId; run_level = [string]$task.Principal.RunLevel
            actions = @($task.Actions | ForEach-Object {
                [ordered]@{execute=$_.Execute; arguments=$_.Arguments; working_directory=$_.WorkingDirectory}
            })
            trigger_types = @($task.Triggers | ForEach-Object { $_.CimClass.CimClassName })
        }
    } catch {
        $report.tasks += [ordered]@{name=$name; unavailable=$true; error=$_.Exception.Message}
    }
}
try {
    $report.ram_volume = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='R:'" -ErrorAction Stop |
        Select-Object DeviceID,Size,FreeSpace,FileSystem,VolumeName
} catch { $report.holds += 'R volume inspection: ' + $_.Exception.Message }
try {
    $processes = Get-CimInstance Win32_Process -Filter "name='HWiNFO64.exe' OR name='LibreHardwareMonitor.exe' OR name='OpenHardwareMonitor.exe'" -ErrorAction Stop
    foreach ($process in $processes) {
        $entry = [ordered]@{name=$process.Name; pid=$process.ProcessId; executable=$process.ExecutablePath; version=$null; sha256=$null; signature=$null}
        if ($process.ExecutablePath -and (Test-Path -LiteralPath $process.ExecutablePath -PathType Leaf)) {
            $entry.version = (Get-Item -LiteralPath $process.ExecutablePath).VersionInfo.FileVersion
            $entry.sha256 = (Get-FileHash -LiteralPath $process.ExecutablePath -Algorithm SHA256).Hash.ToLowerInvariant()
            $entry.signature = [string](Get-AuthenticodeSignature -LiteralPath $process.ExecutablePath).Status
        }
        $report.hardware_monitor += $entry
    }
} catch { $report.holds += 'Hardware monitor inspection: ' + $_.Exception.Message }
try {
    $report.cpu_sensors = @(Get-CimInstance -Namespace 'root/LibreHardwareMonitor' -ClassName Sensor -ErrorAction Stop |
        Where-Object { $_.SensorType -eq 'Temperature' -and ($_.Identifier -like '/intelcpu/*' -or $_.Identifier -like '/amdcpu/*') } |
        Select-Object Identifier,Name,Value)
    if (-not $report.cpu_sensors.Count) { $report.holds += 'No identifiable LibreHardwareMonitor CPU temperature sensors.' }
} catch { $report.holds += 'LibreHardwareMonitor CPU sensors: ' + $_.Exception.Message }
if (-not $report.administrator) { $report.holds += 'This report is non-elevated; protected discovery remains incomplete.' }
if (-not $report.system) { $report.holds += 'Administrator discovery does not establish SYSTEM deployment acceptance.' }
$json = $report | ConvertTo-Json -Depth 12
Write-NewInspectionReport -Path $OutputPath -Json $json
Write-Output $OutputPath
