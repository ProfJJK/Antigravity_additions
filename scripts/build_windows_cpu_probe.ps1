#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$Destination,
    [string]$Compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
)
# Builds a staging artifact only. Never installs PawnIO, launches a sensor read,
# registers a task, changes ACLs, or alters any existing hardware-monitor setup.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$expected = '086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001'
$Archive = (Resolve-Path -LiteralPath $Archive).Path
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Official LibreHardwareMonitor 0.9.6 archive digest mismatch.' }
if ($Destination -notmatch '^[A-Za-z]:[\\/]' -or $Destination.Contains("`0") -or $Destination.Substring(2).Contains(':')) { throw 'Destination must be an explicit absolute local drive path without alternate streams.' }
$Destination = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
$privateStage = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CoChem\staging')).TrimEnd('\')
if (-not $Destination.StartsWith($privateStage+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Build only beneath the current user LocalAppData\CoChem\staging; protected installation is a separate phase.' }
if (Test-Path -LiteralPath $Destination) { throw 'Preserve existing artifacts; choose a new staging directory.' }
$parent = Get-Item -LiteralPath (Split-Path -Parent $Destination) -Force
while ($null -ne $parent) {
    if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Staging destination ancestors must not be reparse points.' }
    $parent = $parent.Parent
}
$Compiler = (Resolve-Path -LiteralPath $Compiler).Path
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'windows_cpu_probe.cs')).Path
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    if ($zip.Entries.Count -gt 128) { throw 'Archive file bound exceeded.' }
    foreach ($entry in $zip.Entries) {
        $target = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
        if (-not $target.StartsWith($Destination+'\',[StringComparison]::OrdinalIgnoreCase) -or $entry.Length -gt 33554432 -or (($entry.ExternalAttributes -shr 16) -band 0xf000) -eq 0xa000) { throw 'Archive contains an unsafe path, link, or size.' }
    }
} finally { $zip.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($Archive,$Destination)
$executable = Join-Path $Destination 'cochem-cpu-temperature.exe'
& $Compiler '/nologo' '/target:exe' '/platform:x64' '/optimize+' ('/out:'+$executable) ('/reference:'+(Join-Path $Destination 'LibreHardwareMonitorLib.dll')) '/reference:System.Web.Extensions.dll' $source
if ($LASTEXITCODE -ne 0) { throw 'CPU probe compilation failed; preserve incomplete staging artifact.' }
Copy-Item -LiteralPath (Join-Path $Destination 'LibreHardwareMonitor.exe.config') -Destination ($executable+'.config')
$files = [ordered]@{}
foreach ($file in (Get-ChildItem -LiteralPath $Destination -File -Recurse | Sort-Object FullName)) {
    $relative = $file.FullName.Substring($Destination.Length+1).Replace('\','/')
    $files[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{
    schema='cochem-cpu-probe-bundle/1';entrypoint='cochem-cpu-temperature.exe'
    archive_url='https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip'
    archive_sha256=$expected;source_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    compiler_sha256=(Get-FileHash -LiteralPath $Compiler -Algorithm SHA256).Hash.ToLowerInvariant()
    files=$files;protected_install=$false;hardware_acceptance=$false
}
[IO.File]::WriteAllText((Join-Path $Destination 'cochem-cpu-temperature.manifest.json'),($manifest | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
Write-Output $Destination
