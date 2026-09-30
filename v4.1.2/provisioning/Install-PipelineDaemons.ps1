# Pipeline daemon installer: SRE Watchdog (SRS-412-08) + DSP Worker(s) (SRS-412-03/04/07).
#
# Registers boot-triggered scheduled tasks that start alongside CoChemHostWarden_V412:
#   CoChemSREWatchdog_V412     python -m cochem.watchdog.watchdog_sre
#   CoChemDspWorker_V412_W<n>  python -m cochem.dsp.worker_daemon
# Each task repeats every minute with MultipleInstancesPolicy=IgnoreNew, so a daemon
# that crashes or recycles itself (RSS cap exit) is relaunched within ~60 s. Task
# Scheduler's RestartOnFailure alone does not fire on a non-zero exit code.
# Both daemons run with LeastPrivilege: neither drives Hyper-V.
#
# Usage (elevated):   .\Install-PipelineDaemons.ps1 [-WorkerCount 2] [-NoRecovery]
# Preview only:       .\Install-PipelineDaemons.ps1 -DryRun
# Remove:             .\Install-PipelineDaemons.ps1 -Uninstall
[CmdletBinding()]
param(
    [string]$InstallDir = "D:\__CoChem\__agentic\v4.1.2",
    [string]$PythonExe = "C:\Python314\python.exe",
    [ValidateRange(1, 6)]
    [int]$WorkerCount = 1,
    [string]$WatchdogTaskName = "CoChemSREWatchdog_V412",
    [string]$WorkerTaskPrefix = "CoChemDspWorker_V412",
    # Watchdog diagnoses and writes Evidence Bundles but never launches claude.exe.
    [switch]$NoRecovery,
    [switch]$RunAsSystem,
    [switch]$DryRun,
    [switch]$Uninstall,
    [int]$StartupTimeoutSec = 30
)

$ErrorActionPreference = "Stop"

$srcDir = Join-Path $InstallDir "src"
$dbPath = Join-Path $InstallDir "job_board.db"
$workerStateDir = Join-Path $InstallDir ".evidence\dsp_worker"
$watchdogDir = Join-Path $InstallDir ".evidence\watchdog"
$workerTaskNames = 1..$WorkerCount | ForEach-Object { "${WorkerTaskPrefix}_W$_" }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Uninstall) {
    if (-not $isAdmin) { throw "Install-PipelineDaemons.ps1 -Uninstall must run from an elevated PowerShell session." }
    $names = @($WatchdogTaskName) + (Get-ScheduledTask -TaskName "${WorkerTaskPrefix}_W*" -ErrorAction SilentlyContinue).TaskName
    foreach ($name in $names | Where-Object { $_ }) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed scheduled task '$name'."
        }
    }
    exit 0
}

# 1. Pre-flight: interpreter, job board, and both modules importing exactly as the tasks will.
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python interpreter not found: $PythonExe" }
if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) { throw "Job board not found: $dbPath" }

# PYTHONPATH: repo src first, then the interpreter's user site-packages (psutil lives there).
$pythonPath = @($srcDir)
$userSite = (& $PythonExe -m site --user-site 2>$null | Select-Object -First 1)
if ($userSite -and (Test-Path -LiteralPath $userSite)) { $pythonPath += $userSite }
$pythonPathValue = $pythonPath -join ";"

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $pythonPathValue
try {
    & $PythonExe -c "import psutil, cochem.watchdog.watchdog_sre, cochem.dsp.worker_daemon; print('daemon modules OK')"
    if ($LASTEXITCODE -ne 0) { throw "Daemon module import failed; fix the Python environment before installing." }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

if ($RunAsSystem) {
    $principalXml = "<UserId>S-1-5-18</UserId><RunLevel>LeastPrivilege</RunLevel>"
    $runAs = "SYSTEM"
} else {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principalXml = "<UserId>$([Security.SecurityElement]::Escape($currentUser))</UserId>" +
                    "<LogonType>S4U</LogonType><RunLevel>LeastPrivilege</RunLevel>"
    $runAs = "$currentUser (S4U)"
}

function New-DaemonTaskXml([string]$Description, [string]$ModuleArgs, [string]$StderrLog, [string]$BootDelay) {
    # cmd.exe /s /c "<line>": /s strips only the outermost quote pair, leaving the inner quoting intact.
    $innerLine = "set `"PYTHONPATH=$pythonPathValue`" && `"$PythonExe`" -m $ModuleArgs 2>> `"$StderrLog`""
    $arguments = "/d /s /c `"$innerLine`""
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>CoChem Pipeline V4.1.2</Author>
    <Description>$([Security.SecurityElement]::Escape($Description))</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>$BootDelay</Delay>
      <Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">$principalXml</Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$([Security.SecurityElement]::Escape("$env:SystemRoot\System32\cmd.exe"))</Command>
      <Arguments>$([Security.SecurityElement]::Escape($arguments))</Arguments>
      <WorkingDirectory>$([Security.SecurityElement]::Escape($InstallDir))</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

$watchdogArgs = "cochem.watchdog.watchdog_sre --db `"$dbPath`" --log-file `"$watchdogDir\watchdog_sre.log`""
if ($NoRecovery) { $watchdogArgs += " --no-recovery" }
$tasks = [ordered]@{}
# Watchdog boots after the Host Warden (PT30S) so its first cycle sees a settled host.
$tasks[$WatchdogTaskName] = New-DaemonTaskXml `
    "CoChem V4.1.2 out-of-band SRE Watchdog (4-Matrix engine, read-only job_board.db)." `
    $watchdogArgs "$watchdogDir\watchdog_sre.stderr.log" "PT45S"
foreach ($name in $workerTaskNames) {
    $tasks[$name] = New-DaemonTaskXml `
        "CoChem V4.1.2 DSP worker daemon ($name): leases PENDING jobs from job_board.db." `
        "cochem.dsp.worker_daemon --db `"$dbPath`" --log-file `"$workerStateDir\$name.log`"" `
        "$workerStateDir\$name.stderr.log" "PT60S"
}

if ($DryRun) {
    Write-Host "[DryRun] Tasks would run as $runAs with PYTHONPATH = $pythonPathValue"
    foreach ($name in $tasks.Keys) { Write-Host "`n=== $name ==="; $tasks[$name] }
    exit 0
}

if (-not $isAdmin) { throw "Install-PipelineDaemons.ps1 must run from an elevated PowerShell session." }

$warden = Get-ScheduledTask -TaskName "CoChemHostWarden_V412" -ErrorAction SilentlyContinue
if ($null -eq $warden) { Write-Warning "CoChemHostWarden_V412 is not registered; run Install-WardenDaemon.ps1 as well." }

# 2. Register, replacing stale worker tasks beyond the requested count.
New-Item -ItemType Directory -Path $workerStateDir, $watchdogDir -Force | Out-Null
foreach ($stale in (Get-ScheduledTask -TaskName "${WorkerTaskPrefix}_W*" -ErrorAction SilentlyContinue)) {
    if ($workerTaskNames -notcontains $stale.TaskName) {
        Stop-ScheduledTask -TaskName $stale.TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $stale.TaskName -Confirm:$false
        Write-Host "Removed surplus worker task '$($stale.TaskName)'."
    }
}
foreach ($name in $tasks.Keys) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -ne $existing -and $existing.State -eq "Running") { Stop-ScheduledTask -TaskName $name }
    Register-ScheduledTask -TaskName $name -Xml $tasks[$name] -Force | Out-Null
    Write-Host "Registered scheduled task '$name' (RunAs=$runAs)."
}

# 3. Start now and wait for each daemon's heartbeat file.
$since = Get-Date
foreach ($name in $tasks.Keys) { Start-ScheduledTask -TaskName $name }
$deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
$watchdogBeat = Join-Path $watchdogDir "watchdog_heartbeat.json"
do {
    Start-Sleep -Milliseconds 500
    $watchdogUp = (Test-Path $watchdogBeat) -and ((Get-Item $watchdogBeat).LastWriteTime -gt $since)
    $workersUp = @(Get-ChildItem -Path $workerStateDir -Filter "worker_*.json" -ErrorAction SilentlyContinue |
                   Where-Object { $_.LastWriteTime -gt $since }).Count
} while ((-not $watchdogUp -or $workersUp -lt $WorkerCount) -and (Get-Date) -lt $deadline)

Write-Host ("SRE Watchdog heartbeat: " + $(if ($watchdogUp) { "OK ($watchdogBeat)" } else { "MISSING" }))
Write-Host "DSP worker heartbeats: $workersUp / $WorkerCount"
if (-not $watchdogUp -or $workersUp -lt $WorkerCount) {
    Write-Error "Daemons did not report within $StartupTimeoutSec s; check the *.stderr.log files under $InstallDir\.evidence."
    exit 1
}
exit 0
