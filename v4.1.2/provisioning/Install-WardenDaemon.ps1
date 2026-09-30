# Host Warden daemon installer: pre-flight checks, task registration, start, liveness probe.
#
# Usage (elevated):   .\Install-WardenDaemon.ps1
#                     .\Install-WardenDaemon.ps1 -RunAsSystem -Port 47821
[CmdletBinding()]
param(
    [string]$TaskName = "CoChemHostWarden_V412",
    [string]$InstallDir = "D:\__CoChem\__agentic\v4.1.2",
    [string]$PythonExe = "C:\Python314\python.exe",
    [ValidateSet("http", "sse")]
    [string]$Transport = "http",
    [ValidateRange(1024, 65535)]
    [int]$Port = 47821,
    [switch]$RunAsSystem,
    [int]$StartupTimeoutSec = 30
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Install-WardenDaemon.ps1 must run from an elevated PowerShell session."
    exit 1
}

function Test-LoopbackPort([int]$P) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect("127.0.0.1", $P, $null, $null)
        return ($connect.AsyncWaitHandle.WaitOne(500) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# 1. Pre-flight: interpreter, fastmcp, and the module importing exactly as the task will import it.
Write-Host "Pre-flight checks..."
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Error "Python interpreter not found: $PythonExe"
    exit 1
}
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $InstallDir "src"
try {
    & $PythonExe -c "import fastmcp, cochem.warden.mcp_server as m; print('fastmcp', fastmcp.__version__, '| tools module OK')"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Module import failed; fix the Python environment before installing."
        exit 1
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and $existing.State -eq "Running") {
    Write-Host "Stopping running instance of '$TaskName' before re-registration..."
    Stop-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
}
if (Test-LoopbackPort $Port) {
    Write-Error "127.0.0.1:$Port is already in use by another process; choose a different -Port."
    exit 1
}

# 2. Register the boot task (RunLevel HighestAvailable, python -m, PYTHONPATH=src).
$createArgs = @{
    TaskName   = $TaskName
    InstallDir = $InstallDir
    PythonExe  = $PythonExe
    Transport  = $Transport
    Port       = $Port
}
if ($RunAsSystem) { $createArgs.RunAsSystem = $true }
& (Join-Path $PSScriptRoot "Create-WardenTask.ps1") @createArgs

# 3. Start it now rather than waiting for the next boot, then wait for the listener.
Start-ScheduledTask -TaskName $TaskName
$deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Test-LoopbackPort $Port) {
        $endpoint = if ($Transport -eq "sse") { "sse" } else { "mcp" }
        Write-Host "Host Warden MCP is listening at http://127.0.0.1:$Port/$endpoint"
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

$logFile = Join-Path $InstallDir ".evidence\warden\mcp_server.log"
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Error ("Task '$TaskName' started but nothing listened on 127.0.0.1:$Port within $StartupTimeoutSec s " +
             "(LastTaskResult=$($info.LastTaskResult)). Check $logFile.")
exit 1
