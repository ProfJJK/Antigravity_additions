#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Python = 'py.exe',
    [string[]]$PythonArgs = @('-3.12'),
    [string]$Uv = 'uv.exe',
    [Parameter(Mandatory=$true)][string]$PipelineClientConfig,
    [string]$ProjectId,
    [string]$Workspace
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

function Install-FrozenEnvironment {
    param([string]$PythonPath)
    $savedEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = $venv
        Invoke-NativeChecked -Executable $Uv -Arguments @('sync','--project',$repo,'--frozen','--no-editable','--link-mode','copy','--extra','mcp','--python',$PythonPath,'--no-python-downloads')
    }
    finally { $env:UV_PROJECT_ENVIRONMENT = $savedEnvironment }
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
    throw 'Run this installer with Windows Python and PowerShell. See docs/EXECUTION_4.2.7.md for the protected pipeline prerequisites.'
}

$PipelineClientConfig = (Resolve-Path -LiteralPath $PipelineClientConfig).Path
$client = Get-Content -LiteralPath $PipelineClientConfig -Raw | ConvertFrom-Json
if ($null -eq $client.PSObject.Properties['port'] -or ($client.port -isnot [int] -and $client.port -isnot [long]) -or $client.port -lt 1024 -or $client.port -gt 65535 -or
    $null -eq $client.PSObject.Properties['token_file'] -or $client.token_file -isnot [string] -or $client.token_file -notmatch '^[A-Za-z]:[\\/]') {
    throw 'PipelineClientConfig requires a reviewed local controller port and an absolute operator token_file path. Token contents are never copied.'
}
if ([bool]$ProjectId -ne [bool]$Workspace) {
    throw 'Compatibility Codex/Claude tool names require both -ProjectId and -Workspace for one registered pipeline project. Omit both to use the primary pipeline MCP.'
}
if ($ProjectId) {
    if ($ProjectId -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') { throw 'ProjectId must identify an existing controller-registered project.' }
    $Workspace = (Resolve-Path -LiteralPath $Workspace).Path
    if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) { throw 'Workspace must be the registered project directory.' }
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$venv = Join-Path $repo '.venv-mcp'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$bridgePath = Join-Path $repo 'config\bridge.4.2.7-r2.local.json'
$antigravityPath = Join-Path $repo 'config\antigravity.4.2.7-r2.local.json'
$examplePath = Join-Path $repo 'config\bridge.windows.example.json'

if (-not (Test-Path -LiteralPath $examplePath -PathType Leaf)) {
    throw "Bridge example is missing: $examplePath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repo 'uv.lock') -PathType Leaf)) {
    throw 'The release uv.lock is required; refusing an unfrozen dependency installation.'
}
$Uv = (Get-Command -Name $Uv -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

Push-Location -LiteralPath $repo
try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $venv) {
            throw "An existing $venv has no Windows Python executable. Preserve it and choose a separate checkout or rename it before retrying."
        }
        $bootstrap = (Get-Command -Name $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        Write-Host 'Checking for Python 3.12 or newer.'
        Invoke-NativeChecked -Executable $bootstrap -Arguments ($PythonArgs + @('-c', 'import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))'))
        $pythonPath = & $bootstrap @PythonArgs -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Could not resolve the selected Windows Python executable.' }
    }
    else { $pythonPath = $venvPython }
    Invoke-NativeChecked -Executable $pythonPath -Arguments @('-c', 'import sys; sys.exit(sys.version_info < (3, 12))')
    Install-FrozenEnvironment -PythonPath $pythonPath
    Write-Host 'Checking the environment uses Python 3.12 or newer.'
    Invoke-NativeChecked -Executable $venvPython -Arguments @('-c', 'import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))')
    Invoke-NativeChecked -Executable $venvPython -Arguments @('-m', 'cochem_mcp', '--version')

    # The primary client carries no native execution authority. Optional old
    # provider tool names are registered only against an authenticated existing
    # controller project, and still forward every job through Chapter 06.
    if ($ProjectId -and -not (Test-Path -LiteralPath $bridgePath)) {
        $verifyProject = 'import json,sys; from cochem_pipeline.service import ControlClient; c=json.load(open(sys.argv[1],encoding="utf-8-sig")); client=ControlClient(c["port"],c["token_file"]); projects=client.call("/coding/projects")["projects"]; client.close(); sys.exit(0 if sys.argv[2] in projects else "ProjectId is not registered by the protected controller")'
        Invoke-NativeChecked -Executable $venvPython -Arguments @('-c',$verifyProject,$PipelineClientConfig,$ProjectId)
        $bridge = Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
        $bridge.workspace_roots = @($Workspace)
        $mapping = @{}
        $mapping[$Workspace] = $ProjectId
        $bridge.controller = [ordered]@{port=$client.port; token_file=$client.token_file; projects=$mapping}
        Write-NewJson -Path $bridgePath -Value $bridge
    }
    elseif ($ProjectId) {
        Write-Host "Preserved existing configuration: $bridgePath"
    }

    $servers = [ordered]@{'cochem-pipeline' = [ordered]@{
        command=$venvPython
        args=@('-m','cochem_pipeline','mcp','--client-config',$PipelineClientConfig)
    }}
    if ($ProjectId) {
        foreach ($provider in @('codex', 'claude')) {
            $checkBridge = 'import sys; from cochem_mcp.config import load_settings; s=load_settings(sys.argv[1],sys.argv[2]); sys.exit(0 if s.controller_port and s.controller_token_file and s.project(sys.argv[3])==sys.argv[4] else "Preserved bridge does not match this registered project/controller; review its configuration")'
            Invoke-NativeChecked -Executable $venvPython -Arguments @('-c',$checkBridge,$bridgePath,$provider,$Workspace,$ProjectId)
            $servers["cochem-$provider"] = [ordered]@{
                command = $venvPython
                args = @('-m', 'cochem_mcp', '--provider', $provider, '--config', $bridgePath)
            }
        }
    }
    Write-NewJson -Path $antigravityPath -Value ([ordered]@{ mcpServers = $servers })
}
finally {
    Pop-Location
}

Write-Host 'Pipeline MCP client installed. Every model task is routed by the protected controller job board.'
Write-Host 'Native subscription logins belong to the isolated pipeline worker accounts; use login_pipeline_worker.ps1 and the reviewed Agy login contract.'
Write-Host 'The primary cochem-pipeline entry uses your existing PipelineClientConfig. Optional provider-named entries do not pin a model.'
Write-Host "Merge the reviewed entries from $antigravityPath into Antigravity using its GUI. Existing local configuration files are preserved."
Write-Host 'Follow docs\EXECUTION_4.2.7.md for actual Windows and native model acceptance; client installation alone does not prove inference.'
