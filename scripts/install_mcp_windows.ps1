#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Python = 'py.exe',
    [string[]]$PythonArgs = @('-3.12')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-NativeChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable exited with code $LASTEXITCODE. Installation stopped."
    }
}

function Find-NativeCli {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $found = Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $found) {
            return $found.Source
        }
    }
    return $null
}

function Write-NewJson {
    param([string]$Path, [object]$Value)
    if (Test-Path -LiteralPath $Path) {
        Write-Host "Preserved existing configuration: $Path"
        return
    }
    $json = $Value | ConvertTo-Json -Depth 12
    # CreateNew also refuses to overwrite a file created after the existence check.
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
        try {
            $writer.WriteLine($json)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
    Write-Host "Created local configuration: $Path"
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'Run this installer with Windows PowerShell or PowerShell on Windows. See docs/MCP_4.2.1.md for WSL.'
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $repo '.venv-mcp'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$bridgePath = Join-Path $repo 'config\bridge.local.json'
$antigravityPath = Join-Path $repo 'config\antigravity.local.json'
$examplePath = Join-Path $repo 'config\bridge.windows.example.json'

if (-not (Test-Path -LiteralPath $examplePath -PathType Leaf)) {
    throw "Bridge example is missing: $examplePath"
}

Push-Location -LiteralPath $repo
try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $venv) {
            throw "An existing $venv has no Windows Python executable. Preserve it and choose a separate checkout or rename it before retrying."
        }
        $bootstrap = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        Write-Host 'Checking for Python 3.12 or newer.'
        Invoke-NativeChecked -Executable $bootstrap -Arguments ($PythonArgs + @('-c', 'import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))'))
        Invoke-NativeChecked -Executable $bootstrap -Arguments ($PythonArgs + @('-m', 'venv', $venv))
    }
    Write-Host 'Checking the environment uses Python 3.12 or newer.'
    Invoke-NativeChecked -Executable $venvPython -Arguments @('-c', 'import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))')
    Invoke-NativeChecked -Executable $venvPython -Arguments @('-m', 'pip', 'install', '-e', '.[mcp]')
    Invoke-NativeChecked -Executable $venvPython -Arguments @('-m', 'cochem_mcp', '--version')

    if (-not (Test-Path -LiteralPath $bridgePath)) {
        $bridge = Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
        $bridge.workspace_roots = @($repo)
        foreach ($provider in @('codex', 'claude')) {
            $names = if ($provider -eq 'codex') { @('codex.exe', 'codex.cmd') } else { @('claude.exe') }
            $executable = Find-NativeCli -Names $names
            if ($null -ne $executable) {
                $bridge.providers.$provider | Add-Member -MemberType NoteProperty -Name 'executable' -Value $executable -Force
            }
            else {
                Write-Warning "$provider was not found in Windows PATH. Install its native CLI and log in before running health checks."
            }
        }
        Write-NewJson -Path $bridgePath -Value $bridge
    }
    else {
        Write-Host "Preserved existing configuration: $bridgePath"
    }

    $servers = [ordered]@{}
    foreach ($provider in @('codex', 'claude')) {
        $servers["cochem-$provider"] = [ordered]@{
            command = $venvPython
            args = @('-m', 'cochem_mcp', '--provider', $provider, '--config', $bridgePath)
        }
    }
    Write-NewJson -Path $antigravityPath -Value ([ordered]@{ mcpServers = $servers })
}
finally {
    Pop-Location
}

Write-Host 'Bridge installed. Review model IDs and workspace roots in config\bridge.local.json.'
Write-Host 'Use codex login and claude auth login as the Windows user who runs Antigravity.'
Write-Host 'Run both --health checks from docs\MCP_4.2.1.md before starting the MCP servers.'
Write-Host 'Merge the two entries from config\antigravity.local.json into Antigravity using its GUI.'
Write-Host 'Existing Antigravity settings have not been modified. Follow the real handoff acceptance tests in the guide.'
