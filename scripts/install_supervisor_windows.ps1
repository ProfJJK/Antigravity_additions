#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Uv = "$env:ProgramFiles\uv\uv.exe",
    [string]$OperatorName = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [string]$InstallRoot = "$env:ProgramFiles\CoChem\Supervisor4.2.5",
    [string]$PipelineRoot = "$env:ProgramFiles\CoChem\Pipeline4.2.5",
    [string]$PipelineConfig,
    [string]$DataRoot = "$env:ProgramData\CoChemSupervisor425",
    [string]$PreviousDataRoot = "$env:ProgramData\CoChemSupervisor424",
    [string]$ReleaseRoot = "$env:ProgramFiles\CoChem\PipelineReleases425",
    [string]$LoginLogRoot = "$env:USERPROFILE\CoChem423\native-login-logs",
    [string]$WardenTaskName = 'CoChem-4.2.2-Warden',
    [string]$SupervisorTaskName = 'CoChem-4.2.3-Supervisor',
    [string]$Config,
    [switch]$RegisterSupervisor
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

function Install-FrozenEnvironment {
    $savedEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $InstallRoot '.venv'
        Invoke-Checked -Executable $Uv -Arguments @('sync','--project',$sourceRoot,'--frozen','--no-editable','--extra','mcp','--extra','dev','--python',$Python,'--no-python-downloads')
    }
    finally { $env:UV_PROJECT_ENVIRONMENT = $savedEnvironment }
}

function Assert-ReviewedExecutionPolicy {
    param([string]$Path)
    $reviewed = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($null -eq $reviewed.PSObject.Properties['routing']) { throw 'The reviewed configuration must include the authoritative routing policy.' }
    $projects = $reviewed.PSObject.Properties['coding_projects']
    if ($null -ne $projects -and $null -ne $projects.Value -and @($projects.Value.PSObject.Properties).Count -gt 0) {
        foreach ($key in @('hardware','execution_limits','ramdisk','docker')) {
            if ($null -eq $reviewed.PSObject.Properties[$key]) {
                throw "Coding requires an explicit reviewed $key configuration. Copy and review the current example."
            }
        }
        if ($reviewed.ramdisk.enabled -ne $true -or $reviewed.docker.enabled -ne $true) {
            throw 'Coding requires enabled RAM-disk and Docker policies; ordinary disk paths are not RAM provisioning.'
        }
    }
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

function Invoke-SystemSetup {
    param([string]$Mode, [string[]]$Arguments)
    $taskName = "CoChem-4.2.5-Supervisor-$Mode"
    $script = Join-Path $InstallRoot "$Mode-task.ps1"
    $log = Join-Path $InstallRoot "$Mode.log"
    $quoted = ($Arguments | ForEach-Object { "'" + $_.Replace("'","''") + "'" }) -join ','
    $body = "`$ErrorActionPreference='Stop'`r`n& '" + $installedPython.Replace("'","''") + "' @(" + $quoted + ") *> '" + $log.Replace("'","''") + "'`r`nexit `$LASTEXITCODE`r`n"
    [IO.File]::WriteAllText($script,$body,[Text.UTF8Encoding]::new($false))
    Protect-InstalledTree -Path $InstallRoot
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + (Quote-TaskArgument $script))
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
        if ((Get-Date) -gt $deadline) { throw "SYSTEM $Mode timed out. Inspect $log" }
    } while ($task.State -in @('Running','Queued') -or $info.LastRunTime -lt $requestedStart.AddSeconds(-1))
    if ($info.LastTaskResult -ne 0) { throw "SYSTEM $Mode failed ($($info.LastTaskResult)). Inspect $log" }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run the supervisor installer on Windows as an administrator.' }
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$Uv = (Resolve-Path -LiteralPath $Uv).Path
$PipelineRoot = (Resolve-Path -LiteralPath $PipelineRoot).Path
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$ReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\')
$DataRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$PreviousDataRoot = [IO.Path]::GetFullPath($PreviousDataRoot).TrimEnd('\')
$LoginLogRoot = [IO.Path]::GetFullPath($LoginLogRoot)
$programFilesPrefix = [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\') + '\'
foreach ($path in @($Python,$Uv,$PipelineRoot,$InstallRoot,$ReleaseRoot)) {
    if (-not $path.StartsWith($programFilesPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Machine Python, standalone uv.exe, pipeline installation, supervisor installation and release store must use protected Program Files paths.'
    }
}
if ($Uv.StartsWith($InstallRoot+'\.venv\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'uv.exe must be separate from the environment it synchronizes.'
}
if ($InstallRoot -eq $PipelineRoot -or $InstallRoot.StartsWith($PipelineRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or $PipelineRoot.StartsWith($InstallRoot+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'The supervisor must have an independent installation directory and interpreter.'
}
foreach ($name in @($WardenTaskName,$SupervisorTaskName)) {
    if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$') { throw 'Scheduled task names must be literal names in the root task folder.' }
}
if ($WardenTaskName -eq $SupervisorTaskName) { throw 'Supervisor and Warden must be separate Scheduled Tasks.' }
if ($DataRoot -eq $PreviousDataRoot) { throw '4.2.5 requires a fresh supervisor state root; preserve the previous ledger and pointer for migration.' }
foreach ($name in @($WardenTaskName,$SupervisorTaskName)) {
    $existingTask = Get-ScheduledTask -TaskName $name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $existingTask) {
        $scheduler = New-Object -ComObject 'Schedule.Service'
        $scheduler.Connect()
        $registeredTask = $scheduler.GetFolder('\').GetTask($name)
        $runningInstances = $registeredTask.GetInstances(0).Count
        if ($runningInstances -gt 0 -or $existingTask.State -in @('Running','Queued') -or $existingTask.Settings.Enabled) {
            throw "Stop and disable managed task $name before upgrading. Activation is performed after a full Windows Restart; this installer does not terminate an uncertain process tree."
        }
    }
}
Assert-ProtectedAncestors -Path (Split-Path -Parent $Python)
foreach ($item in (Get-TreeWithoutLinks -Path (Split-Path -Parent $Python))) { Assert-ProtectedItem -Path $item.FullName }
Assert-ProtectedAncestors -Path (Split-Path -Parent $Uv)
Assert-ProtectedItem -Path $Uv
Assert-ProtectedAncestors -Path $PipelineRoot
foreach ($item in (Get-TreeWithoutLinks -Path $PipelineRoot)) { Assert-ProtectedItem -Path $item.FullName }
Invoke-Checked -Executable $Python -Arguments @('-I','-c','import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))')
Invoke-Checked -Executable $Uv -Arguments @('--version')
$pipelinePython = Join-Path $PipelineRoot '.venv\Scripts\python.exe'
$pipelineConfigSource = if ($PipelineConfig) { (Resolve-Path -LiteralPath $PipelineConfig).Path } else { Join-Path $PipelineRoot 'pipeline.json' }
$previousSource = Join-Path $PipelineRoot 'source'
foreach ($required in @($pipelinePython,$pipelineConfigSource,$previousSource)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Existing managed pipeline deployment is incomplete: $required" }
}
$sourceRoot = Join-Path $InstallRoot 'source'
$acceptanceRoot = Join-Path $InstallRoot 'acceptance'
$installedPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$layoutFile = Join-Path $InstallRoot 'windows-layout.json'
$generatedConfig = Join-Path $InstallRoot 'supervisor.generated.json'
$pipelineConfig = Join-Path $InstallRoot 'pipeline.reviewed.json'

if (Test-Path -LiteralPath $InstallRoot) {
    foreach ($required in @($installedPython,(Join-Path $acceptanceRoot 'pipeline_tests'),(Join-Path $acceptanceRoot 'mcp_tests'),(Join-Path $acceptanceRoot 'pytest.ini'))) {
        if (-not (Test-Path -LiteralPath $required)) { throw 'Preserve the incomplete supervisor installation and choose a fresh versioned InstallRoot.' }
    }
    Assert-ProtectedAncestors -Path $InstallRoot
    foreach ($item in (Get-TreeWithoutLinks -Path $InstallRoot)) { Assert-ProtectedItem -Path $item.FullName }
    Invoke-Checked -Executable $installedPython -Arguments @('-I','-c','import cochem_supervisor; raise SystemExit(tuple(map(int,cochem_supervisor.__version__.split(chr(46)))) != (4,2,5))')
    Write-Host "Preserving independent supervisor code and acceptance snapshot: $InstallRoot"
}
else {
    $parent = Split-Path -Parent $InstallRoot
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Protect-InstalledTree -Path $parent
    }
    Assert-ProtectedAncestors -Path $parent
    New-Item -ItemType Directory -Path $sourceRoot,$acceptanceRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'src') -Destination (Join-Path $sourceRoot 'src') -Recurse
    Copy-Item -LiteralPath (Join-Path $repo 'pyproject.toml'),(Join-Path $repo 'uv.lock'),(Join-Path $repo 'README.md') -Destination $sourceRoot
    # These tests are administrator-installed policy. Candidate repairs cannot
    # replace their own acceptance tests, pytest settings or bootstrap code.
    foreach ($suite in @('pipeline_tests','mcp_tests')) {
        Copy-Item -LiteralPath (Join-Path $repo $suite) -Destination (Join-Path $acceptanceRoot $suite) -Recurse
    }
    # A developer checkout may contain local bytecode. Install source and policy
    # snapshots without inheriting those generated artifacts.
    foreach ($tree in @($sourceRoot,$acceptanceRoot)) {
        foreach ($item in (Get-TreeWithoutLinks -Path $tree | Sort-Object { $_.FullName.Length } -Descending)) {
            if ($item.Name -eq '__pycache__') { Remove-Item -LiteralPath $item.FullName -Recurse -Force }
            elseif (-not $item.PSIsContainer -and $item.Extension -eq '.pyc') { Remove-Item -LiteralPath $item.FullName -Force }
        }
    }
    [IO.File]::WriteAllText((Join-Path $acceptanceRoot 'pytest.ini'),"[pytest]`r`naddopts = -ra`r`n",[Text.UTF8Encoding]::new($false))
    Invoke-Checked -Executable $Python -Arguments @('-I','-m','venv','--copies',(Join-Path $InstallRoot '.venv'))
    Install-FrozenEnvironment
    Protect-InstalledTree -Path $InstallRoot
}

$reviewedPipeline = Get-Content -LiteralPath $pipelineConfigSource -Raw | ConvertFrom-Json
Assert-ReviewedExecutionPolicy -Path $pipelineConfigSource
if ($null -eq $reviewedPipeline.PSObject.Properties['routing']) {
    throw 'The reviewed 4.2.5 pipeline configuration must include routing. Copy and review the current example; the installer does not invent or overwrite routing policy.'
}
Invoke-Checked -Executable $installedPython -Arguments @('-I','-c','import sys; from cochem_pipeline.config import load_config; load_config(sys.argv[1])',$pipelineConfigSource)
if (Test-Path -LiteralPath $pipelineConfig) {
    if ((Get-FileHash -LiteralPath $pipelineConfigSource).Hash -ne (Get-FileHash -LiteralPath $pipelineConfig).Hash) {
        throw 'Existing protected pipeline.reviewed.json differs; preserve it and choose a fresh reviewed installation.'
    }
}
else { Copy-Item -LiteralPath $pipelineConfigSource -Destination $pipelineConfig }
Protect-InstalledTree -Path $InstallRoot
Invoke-Checked -Executable $installedPython -Arguments @('-I','-c','import sys; from cochem_pipeline.config import load_config; load_config(sys.argv[1])',$pipelineConfig)

if (Test-Path -LiteralPath $ReleaseRoot) {
    Assert-ProtectedAncestors -Path $ReleaseRoot
    foreach ($item in (Get-TreeWithoutLinks -Path $ReleaseRoot)) { Assert-ProtectedItem -Path $item.FullName }
}
else {
    Assert-ProtectedAncestors -Path (Split-Path -Parent $ReleaseRoot)
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
    Protect-InstalledTree -Path $ReleaseRoot
}

Invoke-SystemSetup -Mode 'Provision' -Arguments @('-I','-m','cochem_supervisor.windows','provision',
    '--private-root',(Join-Path $DataRoot 'private'),'--repair-workspace',(Join-Path $DataRoot 'workers\repair'),
    '--operator-name',$OperatorName,'--layout-output',$layoutFile,'--login-log-root',$LoginLogRoot)
Invoke-SystemSetup -Mode 'MigrateLedger' -Arguments @('-I','-m','cochem_supervisor.windows','migrate-ledger',
    '--source-private',(Join-Path $PreviousDataRoot 'private'),'--target-private',(Join-Path $DataRoot 'private'),
    '--supervisor-task',$SupervisorTaskName)
Invoke-SystemSetup -Mode 'Execution' -Arguments @('-I','-m','cochem_pipeline','provision-execution','--config',$pipelineConfig)
if (-not (Test-Path -LiteralPath $generatedConfig)) {
    $pipeline = Get-Content -LiteralPath $pipelineConfig -Raw | ConvertFrom-Json
    $repairLimits = Invoke-Checked -Executable $installedPython -Arguments @('-I','-c','import json; from cochem_pipeline.resource_limits import ResourceLimits; print(json.dumps(ResourceLimits().as_dict()))') | ConvertFrom-Json
    $generated = [ordered]@{
        private_root = (Join-Path $DataRoot 'private')
        repair_workspace = (Join-Path $DataRoot 'workers\repair')
        repair_worker = @{name='CoChem423Repair';credential_target='CoChem423/repair'}
        repair_execution_limits = $repairLimits
        release_root = $ReleaseRoot
        baseline_source = $sourceRoot
        acceptance_root = $acceptanceRoot
        pipeline_python = $pipelinePython
        pipeline_config = $pipelineConfig
        pointer_file = (Join-Path $DataRoot 'private\active-release.json')
        test_python = $installedPython
        operator_name = $OperatorName
        warden_task = $WardenTaskName
        supervisor_task = $SupervisorTaskName
        providers = @(
            @{provider='codex';executable=$pipeline.providers.codex.executable;model='gpt-6-astra'},
            @{provider='claude';executable=$pipeline.providers.claude.executable;model='claude-fable-5-1'}
        )
        auto_deploy = $true
        max_per_incident = 2
        max_per_day = 4
        cooldown_seconds = 1800
        allowed_paths = @('src/cochem_pipeline/','src/cochem_mcp/','src/cochem/warden/ladder.py')
    }
    [IO.File]::WriteAllText($generatedConfig,($generated | ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
    Protect-InstalledTree -Path $InstallRoot
}
else { Write-Host "Preserved generated review configuration: $generatedConfig" }
Write-Host "Independent repair identity layout: $layoutFile"
Write-Host "Review configuration and native repair-provider access: $generatedConfig"
Write-Host "The 4.2.5 supervisor uses a fresh protected source/test snapshot and state root. Prior ledger history is migrated through SQLite backup; previous state remains at $PreviousDataRoot."
Write-Host 'CoChem423Repair account password remains in SYSTEM Credential Manager; no pipeline worker identity or subscription credential was copied.'
Write-Host "Use login_supervisor_worker.ps1 for each configured repair provider. Native login logs may be placed in $LoginLogRoot"

if ($RegisterSupervisor) {
    if (-not $Config) { throw '-RegisterSupervisor requires an explicit reviewed -Config JSON file.' }
    $candidateConfig = (Resolve-Path -LiteralPath $Config).Path
    Invoke-Checked -Executable $installedPython -Arguments @('-I','-c','import sys; from cochem_supervisor.config import load_config; load_config(sys.argv[1])',$candidateConfig)
    $reviewedSupervisor = Get-Content -LiteralPath $candidateConfig -Raw | ConvertFrom-Json
    $expectedPaths = [ordered]@{
        private_root = (Join-Path $DataRoot 'private')
        repair_workspace = (Join-Path $DataRoot 'workers\repair')
        release_root = $ReleaseRoot
        baseline_source = $sourceRoot
        acceptance_root = $acceptanceRoot
        pipeline_python = $pipelinePython
        pipeline_config = $pipelineConfig
        pointer_file = (Join-Path $DataRoot 'private\active-release.json')
        test_python = $installedPython
    }
    foreach ($entry in $expectedPaths.GetEnumerator()) {
        $property = $reviewedSupervisor.PSObject.Properties[$entry.Key]
        if ($null -eq $property -or $property.Value -isnot [string] -or
            [IO.Path]::GetFullPath($property.Value).TrimEnd('\') -ne [IO.Path]::GetFullPath($entry.Value).TrimEnd('\')) {
            throw "Reviewed $($entry.Key) must match this fresh protected deployment. Preserve policy paths from supervisor.generated.json."
        }
    }
    if ($reviewedSupervisor.warden_task -ne $WardenTaskName -or $reviewedSupervisor.supervisor_task -ne $SupervisorTaskName -or
        $reviewedSupervisor.repair_worker.name -ne 'CoChem423Repair' -or $reviewedSupervisor.repair_worker.credential_target -ne 'CoChem423/repair') {
        throw 'Reviewed configuration must preserve the provisioned repair identity and selected existing task names.'
    }
    $installedConfig = Join-Path $InstallRoot 'supervisor.json'
    if (Test-Path -LiteralPath $installedConfig) {
        if ((Get-FileHash -LiteralPath $candidateConfig).Hash -ne (Get-FileHash -LiteralPath $installedConfig).Hash) {
            throw 'Existing installed supervisor.json differs. Preserve and review it explicitly; the installer does not overwrite configuration.'
        }
    }
    else { Copy-Item -LiteralPath $candidateConfig -Destination $installedConfig }
    Protect-InstalledTree -Path $InstallRoot
    Invoke-SystemSetup -Mode 'Configure' -Arguments @('-I','-m','cochem_supervisor.windows','configure','--config',$installedConfig,'--previous-source',$previousSource)
    Write-Host 'Registered the 4.2.5 SYSTEM supervisor under the preserved task identity and migrated the Warden to the fresh protected release pointer.'
    Write-Host "Original Warden task XML and source hash are retained under $DataRoot\private\installation-rollback. The old pipeline installation remains intact."
    Write-Host 'Reviewed Warden and supervisor actions are registered and enabled; no model or service was started. Perform a full Windows Restart after configuration and login review. Keep old code, pointers and a compatible database recovery plan for administrator rollback; do not edit frozen acceptance tests in place.'
}
else { Write-Host 'Next: authenticate the dedicated repair account, review generated config, then rerun with -Config PATH -RegisterSupervisor.' }
