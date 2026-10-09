#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# User-run read-only discovery. Writes new reports only. No elevation, installation,
# credential copying, ACL changes, database opens, task changes or sensor sampling.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.Name -ne 'AETHERDESK\ansac') { throw 'Run this discovery as Administrator under the existing AETHERDESK\ansac account.' }
$stage = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CoChem\staging\windows-427-20261006'
$originalPath = Join-Path $stage 'prerequisites-elevated.json'
$detailsScript = Join-Path $PSScriptRoot 'inspect-windows-admin-details.ps1'
$workspace = Split-Path -Parent $PSScriptRoot

foreach ($path in @($originalPath, $detailsScript, $workspace)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Discovery paths must not contain reparse points.' }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory } else { $item = $item.Parent }
    }
}
if ((Get-FileHash -LiteralPath $detailsScript -Algorithm SHA256).Hash -ne '201B2FDCB158B4A5D33B62363E7B9C6BFDDF0452F21B8DA1A87F5D812D3F4E35') {
    throw 'Companion discovery changed after review; stop and review its new bytes.'
}
if ((Get-Item -LiteralPath $originalPath).Length -gt 1048576) { throw 'Prerequisite report exceeds expected size.' }
$original = Get-Content -LiteralPath $originalPath -Raw | ConvertFrom-Json
if ($original.schema -ne 'cochem-windows-prerequisites/1' -or
    $original.script_sha256 -ne '3fb4167f9e2d3c12dbc5f79ae33298526943a1ada42e7ca89a6d611c43e1decd' -or
    $original.identity -ne 'AETHERDESK\ansac' -or -not $original.administrator) {
    throw 'Expected the reviewed administrator prerequisite report from AETHERDESK\ansac.'
}

# Arguments are deliberately excluded from the shared task summary.
$taskSummary = @()
foreach ($task in $original.tasks) {
    $entry = [ordered]@{}
    foreach ($name in @('name','path','state','user','run_level','trigger_types','unavailable','error')) {
        $property = $task.PSObject.Properties[$name]
        if ($null -ne $property) { $entry[$name] = $property.Value }
    }
    $entry.action_arguments_omitted = $true
    $taskSummary += $entry
}
$sanitized = [ordered]@{}
foreach ($name in @('schema','observed_at_utc','scope','identity','administrator','system','script_sha256','ram_volume','hardware_monitor','cpu_sensors','holds')) {
    $property = $original.PSObject.Properties[$name]
    if ($null -ne $property) { $sanitized[$name] = $property.Value }
}
$sanitized.tasks = $taskSummary

function Remove-PrivateDiscoveryFields {
    param($Value)
    if ($null -eq $Value) { return $null }
    # Pipeline/JSON values can be PSObject-wrapped scalars that also satisfy
    # -is [pscustomobject]. Preserve atomic values before projecting properties.
    if ($Value -is [string] -or $Value -is [ValueType]) { return $Value }
    if ($Value -is [Array]) {
        $items=[object[]]::new($Value.Length)
        for ($index=0; $index -lt $Value.Length; $index++) { $items[$index]=Remove-PrivateDiscoveryFields $Value[$index] }
        return ,$items
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $result=[ordered]@{}
        foreach ($key in $Value.Keys) {
            if (-not ([string]$key).StartsWith('private_',[StringComparison]::OrdinalIgnoreCase)) { $result[$key]=Remove-PrivateDiscoveryFields $Value[$key] }
        }
        return $result
    }
    if ($Value -is [pscustomobject]) {
        $result=[ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            if (-not $property.Name.StartsWith('private_',[StringComparison]::OrdinalIgnoreCase)) { $result[$property.Name]=Remove-PrivateDiscoveryFields $property.Value }
        }
        return $result
    }
    return $Value
}

$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$detailsPath = Join-Path $stage ('admin-details-' + $stamp + '.json')
& $detailsScript -OutputPath $detailsPath | Out-Null
$details = Get-Content -LiteralPath $detailsPath -Raw | ConvertFrom-Json
$shared = [ordered]@{
    schema = 'cochem-user-shared-admin-discovery/1'
    shared_at_utc = [DateTime]::UtcNow.ToString('o')
    scope = 'User-shared diagnostic metadata only; all original and private task action arguments omitted'
    prerequisite_private_path = $originalPath
    prerequisite_private_sha256 = (Get-FileHash -LiteralPath $originalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    prerequisites = $sanitized
    details_private_path = $detailsPath
    details_private_sha256 = (Get-FileHash -LiteralPath $detailsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    details = Remove-PrivateDiscoveryFields $details
}
$destination = Join-Path $workspace ('administrator-evidence-shared-' + $stamp + '.json')
$json = $shared | ConvertTo-Json -Depth 24
$stream = [IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream.Write($bytes,0,$bytes.Length)
} finally { $stream.Dispose() }
Write-Output $destination
