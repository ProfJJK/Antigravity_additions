# Windows Scheduled Task Registration for the Host Warden MCP daemon (MC-HW-65)
#
# Registers a boot-triggered task that runs `python -m cochem.warden.mcp_server`
# with PYTHONPATH pinned to <InstallDir>\src and RunLevel HighestAvailable, so the
# daemon can drive the Hyper-V cmdlets (Restart-VM, Restore-VMSnapshot) as admin.
# The server binds 127.0.0.1 only (streamable HTTP by default).
#
# Usage (elevated):   .\Create-WardenTask.ps1
#                     .\Create-WardenTask.ps1 -RunAsSystem
# Preview only:       .\Create-WardenTask.ps1 -DryRun
[CmdletBinding()]
param(
    [string]$TaskName = "CoChemHostWarden_V412",
    [string]$InstallDir = "D:\__CoChem\__agentic\v4.1.2",
    [string]$PythonExe = "C:\Python314\python.exe",
    [ValidateSet("http", "sse")]
    [string]$Transport = "http",
    [ValidateRange(1024, 65535)]
    [int]$Port = 47821,
    # Default principal is the installing user (S4U: runs at boot without a stored
    # password). SYSTEM cannot see per-user site-packages unless they are added to
    # PYTHONPATH, which this script does automatically.
    [switch]$RunAsSystem,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$srcDir = Join-Path $InstallDir "src"
$logDir = Join-Path $InstallDir ".evidence\warden"
$logFile = Join-Path $logDir "mcp_server.log"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python interpreter not found: $PythonExe" }
if (-not (Test-Path -LiteralPath (Join-Path $srcDir "cochem\warden\mcp_server.py") -PathType Leaf)) {
    throw "Host Warden module not found under $srcDir"
}

# PYTHONPATH: repo src first, then the interpreter's user site-packages (fastmcp lives there).
$pythonPath = @($srcDir)
$userSite = (& $PythonExe -m site --user-site 2>$null | Select-Object -First 1)
if ($userSite -and (Test-Path -LiteralPath $userSite)) { $pythonPath += $userSite }
$pythonPathValue = $pythonPath -join ";"

# cmd.exe /s /c "<line>": /s strips only the outermost quote pair, leaving the inner quoting intact.
$innerLine = "set `"PYTHONPATH=$pythonPathValue`" && `"$PythonExe`" -m cochem.warden.mcp_server " +
             "--transport $Transport --port $Port >> `"$logFile`" 2>&1"
$arguments = "/d /s /c `"$innerLine`""

if ($RunAsSystem) {
    $principalXml = "<UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel>"
    $runAs = "SYSTEM"
} else {
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principalXml = "<UserId>$([Security.SecurityElement]::Escape($currentUser))</UserId>" +
                    "<LogonType>S4U</LogonType><RunLevel>HighestAvailable</RunLevel>"
    $runAs = "$currentUser (S4U)"
}

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>CoChem Host Warden</Author>
    <Description>CoChem Host Warden FastMCP daemon ($Transport on 127.0.0.1:$Port) for the ephemeral Hyper-V quarantine VM.</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
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
      <WorkingDirectory>$([Security.SecurityElement]::Escape($srcDir))</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

if ($DryRun) {
    Write-Host "[DryRun] Task '$TaskName' would run as $runAs with:"
    Write-Host "  PYTHONPATH = $pythonPathValue"
    Write-Host "  cmd.exe $arguments"
    $xml
    return
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Create-WardenTask.ps1 must run from an elevated PowerShell session." }

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null

$registered = Get-ScheduledTask -TaskName $TaskName
Write-Host "Registered scheduled task '$TaskName' (RunLevel=$($registered.Principal.RunLevel), RunAs=$runAs)."
Write-Host "  Launch: python -m cochem.warden.mcp_server --transport $Transport --port $Port"
Write-Host "  PYTHONPATH: $pythonPathValue"
Write-Host "  Log: $logFile"
