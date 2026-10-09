#Requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputPath)

# Manual discovery only. The sole persistent write is a new private JSON report.
# Never starts tasks, installs/loads drivers, samples sensors, or changes R:.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
if ($OutputPath -notmatch '^[A-Za-z]:[\\/]' -or $OutputPath.Contains("`0") -or $OutputPath.Substring(2).Contains(':')) { throw 'OutputPath must be an absolute local drive path without alternate streams.' }
if (@($OutputPath.Substring(3) -split '[\\/]' | Where-Object { $_ -in @('.', '..') }).Count) { throw 'OutputPath must not contain dot path components.' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
$privateStage = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CoChem\staging')).TrimEnd('\')
if (-not $OutputPath.StartsWith($privateStage+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Write inspection evidence only beneath the current user LocalAppData\CoChem\staging.' }
if (Test-Path -LiteralPath $OutputPath) { throw 'OutputPath already exists; preserve the earlier evidence.' }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $OutputPath) -PathType Container)) { throw 'Output directory must already exist.' }

function Assert-NoReparse {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Inspection path ancestors must not be reparse points.' }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory } else { $item = $item.Parent }
    }
}
Assert-NoReparse (Split-Path -Parent $OutputPath)

function Get-FileMetadata {
    param([string]$Path)
    $result = [ordered]@{path=$Path; available=$false}
    try {
        if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':')) { throw 'File metadata requires a local absolute path without alternate streams.' }
        Assert-NoReparse $Path
        $file = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($file -isnot [IO.FileInfo]) { throw 'Expected a file.' }
        $signature = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
        $result.bytes = $file.Length
        $result.modified_utc = $file.LastWriteTimeUtc.ToString('o')
        $result.sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        $result.version = $file.VersionInfo.FileVersion
        $result.signature = [string]$signature.Status
        $result.signer_subject = if ($null -ne $signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
        $result.available = $true
    } catch { $result.error = $_.Exception.Message }
    return $result
}

function Resolve-DriverPath {
    param([string]$Value)
    $path = $Value.Trim().Trim('"')
    if ($path.StartsWith('\??\')) { $path = $path.Substring(4) }
    if ($path.StartsWith('\SystemRoot\',[StringComparison]::OrdinalIgnoreCase)) { $path = Join-Path $env:SystemRoot $path.Substring(12) }
    if ($path.StartsWith('%SystemRoot%\',[StringComparison]::OrdinalIgnoreCase)) { $path = Join-Path $env:SystemRoot $path.Substring(13) }
    if ($path.StartsWith('System32\',[StringComparison]::OrdinalIgnoreCase)) { $path = Join-Path $env:SystemRoot $path }
    if ($path -notmatch '^[A-Za-z]:\\[^"\r\n]+\.sys$' -or $path.Substring(2).Contains(':')) { throw 'Driver path is not a standalone local SYS file; no path is guessed.' }
    return [IO.Path]::GetFullPath($path)
}

function Convert-TaskDetail {
    param($Value, [int]$Depth=0)
    if ($Depth -gt 6) { return '[nested detail omitted]' }
    if ($null -eq $Value) { return $null }
    if ($Value -is [Microsoft.Management.Infrastructure.CimInstance]) {
        $data = [ordered]@{class=$Value.CimClass.CimClassName}
        foreach ($property in $Value.CimInstanceProperties) {
            # Event subscription/value-query strings can contain private values.
            if ($property.Name -in @('Subscription','ValueQueries','Arguments','Actions')) {
                $data[$property.Name+'_omitted'] = ($null -ne $property.Value)
            } else { $data[$property.Name] = Convert-TaskDetail $property.Value ($Depth+1) }
        }
        return $data
    }
    if ($Value -is [Array]) { return ,@($Value | ForEach-Object { Convert-TaskDetail $_ ($Depth+1) }) }
    if ($Value -is [DateTime]) { return $Value.ToString('o') }
    if ($Value -is [string] -and $Value.Length -gt 4096) { return '[overlong detail omitted]' }
    return $Value
}

function Format-OptionalTime {
    param($Value)
    if ($null -eq $Value) { return $null }
    return $Value.ToString('o')
}

function Get-TaskActionMetadata {
    param($Action, [bool]$RamdiskRelated)
    $execute=[string]$Action.Execute
    $arguments=[string]$Action.Arguments
    $entry=[ordered]@{execute=$execute; working_directory=$Action.WorkingDirectory; arguments_present=([bool]$arguments); arguments_omitted=$true; target_files=@()}
    # Only RAM-related arguments are retained privately. The sharing wrapper
    # removes every private_* field. CoChem credential-refresh arguments are not copied.
    if ($RamdiskRelated -and $arguments.Length -le 16384) { $entry.private_arguments=$arguments }
    $paths=@()
    if ($execute -match '^[A-Za-z]:\\' -and $execute -notmatch '"') { $paths += $execute }
    # Extract literal script paths only, never execute/evaluate arguments.
    foreach ($match in [regex]::Matches($arguments,'(?i)"([A-Z]:\\[^"\r\n]+\.(?:ps1|cmd|bat|vbs))"|(?:^|\s)([A-Z]:\\[^\s"\r\n]+\.(?:ps1|cmd|bat|vbs))(?=\s|$)')) {
        $path=if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
        if ($paths.Count -lt 8) { $paths += $path }
    }
    foreach ($path in @($paths | Select-Object -Unique)) { $entry.target_files += Get-FileMetadata $path }
    return $entry
}

function Get-TaskSearchText {
    param($Task)
    $parts=@([string]$Task.TaskPath,[string]$Task.TaskName)
    foreach ($action in $Task.Actions) {
        if ($null -eq $action) { continue }
        foreach ($name in @('Execute','Arguments')) {
            $property=$action.PSObject.Properties[$name]
            if ($null -ne $property) { $parts += [string]$property.Value }
        }
    }
    return ($parts -join ' ')
}

function Invoke-ImDiskReadOnly {
    param([string]$Executable, [ValidateSet('-l','-l -m R:')][string]$Arguments)
    $result = [ordered]@{arguments=$Arguments; available=$false; timeout_seconds=5; max_bytes_per_stream=32768}
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.Arguments = $Arguments
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $started = $false
    try {
        if (-not $process.Start()) { throw 'ImDisk query did not start.' }
        $started = $true
        $streams = @($process.StandardOutput.BaseStream,$process.StandardError.BaseStream)
        $buffers = @([byte[]]::new(4096),[byte[]]::new(4096))
        $data = @([IO.MemoryStream]::new(),[IO.MemoryStream]::new())
        $pending = @($streams[0].ReadAsync($buffers[0],0,4096),$streams[1].ReadAsync($buffers[1],0,4096))
        $ended = @($false,$false)
        $watch = [Diagnostics.Stopwatch]::StartNew()
        try {
            while (-not ($process.HasExited -and $ended[0] -and $ended[1])) {
                if ($watch.Elapsed.TotalSeconds -ge 5) { throw 'ImDisk read-only query exceeded five seconds.' }
                for ($index=0; $index -lt 2; $index++) {
                    if (-not $ended[$index] -and $pending[$index].IsCompleted) {
                        $count = $pending[$index].GetAwaiter().GetResult()
                        if ($count -eq 0) { $ended[$index] = $true; continue }
                        if ($data[$index].Length + $count -gt 32768) { throw 'ImDisk query exceeded its output limit.' }
                        $data[$index].Write($buffers[$index],0,$count)
                        $pending[$index] = $streams[$index].ReadAsync($buffers[$index],0,4096)
                    }
                }
                Start-Sleep -Milliseconds 10
            }
            $result.exit_code = $process.ExitCode
            $result.stdout = [Text.Encoding]::Default.GetString($data[0].ToArray())
            $result.stderr = [Text.Encoding]::Default.GetString($data[1].ToArray())
            $result.completed = $true
            if ($Arguments -eq '-l') {
                # Bare enumeration has a different upstream exit-code convention
                # from device queries. Preserve raw evidence without a global failure claim.
                $result.available = $null
                $result.exit_code_scope = 'Raw bare-list status; do not apply the device-query zero-exit convention.'
            } else { $result.available = ($process.ExitCode -eq 0) }
        } finally { foreach ($stream in $data) { $stream.Dispose() } }
    } catch { $result.error = $_.Exception.Message }
    finally {
        if ($started) {
            if (-not $process.HasExited) {
                # Only this script's own bounded read-only query process.
                try { $process.Kill(); $result.query_cleanup_confirmed = $process.WaitForExit(1000) }
                catch { $result.query_cleanup_confirmed = $false }
            }
            $process.StandardOutput.Close()
            $process.StandardError.Close()
        }
        $process.Dispose()
    }
    return $result
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$report = [ordered]@{
    schema='cochem-windows-admin-details/1'; observed_at_utc=[DateTime]::UtcNow.ToString('o')
    scope='Read-only PawnIO, reboot, task timing, ImDisk and grounded startup discovery; no sensor measurement or deployment mutation'
    identity=$identity.Name; administrator=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    system=($identity.User.Value -eq 'S-1-5-18'); script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    pawnio_registry=@(); pawnio_service=$null; pawnio_system_drivers=@(); pending_reboot=[ordered]@{}
    tasks=@(); imdisk=$null; startup=$null; holds=@()
}

foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)) {
    $base=$null; $key=$null
    $entry=[ordered]@{view=[string]$view; key='HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO'; present=$null; display_version=$null}
    try {
        $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,$view)
        $key=$base.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO',$false)
        $entry.present=($null -ne $key)
        if ($null -ne $key) { $entry.display_version=[string]$key.GetValue('DisplayVersion',$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
    } catch { $entry.error=$_.Exception.Message }
    finally { if ($null -ne $key) {$key.Dispose()}; if ($null -ne $base) {$base.Dispose()} }
    $report.pawnio_registry += $entry
}
$serviceKey='Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\PawnIO'
try {
    $report.pawnio_service=[ordered]@{key=$serviceKey; present=(Test-Path -LiteralPath $serviceKey); file=$null}
    if ($report.pawnio_service.present) {
        $service=Get-ItemProperty -LiteralPath $serviceKey
        foreach ($name in @('Start','Type','ErrorControl')) {
            $property=$service.PSObject.Properties[$name]
            if ($null -ne $property) { $report.pawnio_service[$name]=$property.Value }
        }
        $property=$service.PSObject.Properties['ImagePath']
        if ($null -ne $property) { $report.pawnio_service.file=Get-FileMetadata (Resolve-DriverPath ([string]$property.Value)) }
    }
} catch { $report.holds += 'PawnIO service metadata: '+$_.Exception.Message }
try {
    foreach ($driver in @(Get-CimInstance Win32_SystemDriver -Filter "Name='PawnIO'" -OperationTimeoutSec 10 -ErrorAction Stop)) {
        $entry=[ordered]@{name=$driver.Name; state=$driver.State; started=$driver.Started; start_mode=$driver.StartMode; service_type=$driver.ServiceType; exit_code=$driver.ExitCode; file=$null}
        try { $entry.file=Get-FileMetadata (Resolve-DriverPath $driver.PathName) } catch { $entry.error=$_.Exception.Message }
        $report.pawnio_system_drivers += $entry
    }
} catch { $report.holds += 'PawnIO system-driver metadata: '+$_.Exception.Message }

foreach ($entry in @(
    @{name='component_servicing';path='Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'},
    @{name='windows_update';path='Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'}
)) {
    try { $report.pending_reboot[$entry.name]=[bool](Test-Path -LiteralPath $entry.path -ErrorAction Stop) }
    catch { $report.pending_reboot[$entry.name]=$null; $report.holds += 'Restart flag unavailable: '+$entry.name }
}
try {
    $session=Get-ItemProperty -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager'
    $rename=$session.PSObject.Properties['PendingFileRenameOperations']
    $report.pending_reboot.pending_file_rename=($null -ne $rename -and @($rename.Value).Count -gt 0)
} catch { $report.pending_reboot.pending_file_rename=$null; $report.holds += 'Restart flag unavailable: pending_file_rename' }
$report.pending_reboot.scope='Presence/nonempty flags only; no registry payloads, no guarantee all reboot causes are detected.'

foreach ($name in @('CoChem_EnsureRamdisk','CoChemHostWarden_V412','CoChem V4.1.2 Swarm','CoChem_Ecosystem_Backup')) {
    try {
        $tasks=@(Get-ScheduledTask -TaskName $name -ErrorAction Stop)
        foreach ($task in $tasks) {
            $entry=[ordered]@{name=$task.TaskName; path=$task.TaskPath; state=[string]$task.State; principal=Convert-TaskDetail $task.Principal; triggers=@($task.Triggers | ForEach-Object { Convert-TaskDetail $_ }); settings=Convert-TaskDetail $task.Settings; actions_omitted=$true}
            try {
                $info=Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
                $entry.last_run_time=Format-OptionalTime $info.LastRunTime; $entry.last_result=$info.LastTaskResult
                $entry.next_run_time=Format-OptionalTime $info.NextRunTime; $entry.missed_runs=$info.NumberOfMissedRuns
            } catch { $entry.timing_error=$_.Exception.Message }
            $report.tasks += $entry
        }
    } catch { $report.tasks += [ordered]@{name=$name; unavailable=$true; error=$_.Exception.Message} }
}

# Focused all-folder lookup: the owner's startup task need not have the old
# launcher's expected name. Nothing here changes, exports, or starts a task.
$report.ramdisk_startup_provenance=[ordered]@{scope='Relevant task names/actions across all task folders; task actions and script files are never executed'; task_matches=@(); matched_count=0; task_limit=64; complete=$false}
$ramPattern='(?i)imdisk|ram[ _-]?(?:disk|drive)|(?:^|[\s"''=])R:(?:\\|(?=[\s"'']|$))'
try {
    $matched=@(Get-ScheduledTask -ErrorAction Stop | Where-Object {
        $taskText=Get-TaskSearchText $_
        $taskText -match $ramPattern -or $taskText -match '(?i)cochem'
    })
    $report.ramdisk_startup_provenance.matched_count=$matched.Count
    $report.ramdisk_startup_provenance.complete=($matched.Count -le 64)
    foreach ($task in @($matched | Select-Object -First 64)) {
        $taskText=Get-TaskSearchText $task
        $ramRelated=($taskText -match $ramPattern)
        $entry=[ordered]@{name=$task.TaskName; path=$task.TaskPath; state=[string]$task.State; ramdisk_pattern_match=$ramRelated; principal=Convert-TaskDetail $task.Principal; triggers=@($task.Triggers | ForEach-Object { Convert-TaskDetail $_ }); settings=Convert-TaskDetail $task.Settings; actions=@()}
        foreach ($action in $task.Actions) {
            if ($null -eq $action) { continue }
            if ($null -ne $action.PSObject.Properties['Execute']) { $entry.actions += Get-TaskActionMetadata $action $ramRelated }
            else { $entry.actions += [ordered]@{class=$action.CimClass.CimClassName; non_execute_action_omitted=$true} }
        }
        try {
            $info=Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
            $entry.last_run_time=Format-OptionalTime $info.LastRunTime; $entry.last_result=$info.LastTaskResult
            $entry.next_run_time=Format-OptionalTime $info.NextRunTime; $entry.missed_runs=$info.NumberOfMissedRuns
        } catch { $entry.timing_error=$_.Exception.Message }
        $report.ramdisk_startup_provenance.task_matches += $entry
    }
} catch { $report.ramdisk_startup_provenance.complete=$false; $report.ramdisk_startup_provenance.error=$_.Exception.Message }

$imdiskPath='C:\Windows\System32\imdisk.exe' # Observed installed path, never PATH-resolved.
$imdisk=Get-FileMetadata $imdiskPath
$report.imdisk=[ordered]@{executable=$imdisk; queries=@(); recorded_sha256='3a6a94c98a4b87e59a136052c50da070c99706db3bcd54ef5b148784b21f75dd'}
if ($imdisk.available -and $imdisk.sha256 -eq $report.imdisk.recorded_sha256) {
    foreach ($arguments in @('-l','-l -m R:')) { $report.imdisk.queries += Invoke-ImDiskReadOnly $imdiskPath $arguments }
} else { $report.holds += 'ImDisk query skipped: observed executable is unavailable or differs from the reviewed installed hash.' }

$startupPath='C:\Users\ansac\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\cochem_startup.vbs'
$startup=Get-FileMetadata $startupPath
$report.startup=[ordered]@{launcher=$startup; recorded_sha256='b9e5d2b4347e3c43df5f8a6b4f41d4b91b75e578c4b32541c4d23a22b9688f3a'; matches_reviewed_source=$false; referenced_target=$null}
if ($startup.available -and $startup.sha256 -eq $report.startup.recorded_sha256) {
    $report.startup.matches_reviewed_source=$true
    $report.startup.required_drive='R:'; $report.startup.required_label='COCHEM_RAM'; $report.startup.referenced_task='CoChem_EnsureRamdisk'
    $report.startup.referenced_target=Get-FileMetadata 'D:\__CoChem\__agentic\v4.2.0\start_pipeline.bat'
} else { $report.holds += 'Startup source changed or unavailable; no current launch target/label is inferred.' }
if (-not $report.administrator) { $report.holds += 'Non-elevated discovery; protected details may be unavailable.' }
if (-not $report.system) { $report.holds += 'Administrator discovery does not establish SYSTEM acceptance.' }
$report.holds += 'PawnIO metadata does not prove driver-device ACLs, worker denial or usable CPU temperatures. No PawnIO device was opened or sensor measured; ImDisk inventory queries are read-only.'

Assert-NoReparse (Split-Path -Parent $OutputPath)
$json=$report | ConvertTo-Json -Depth 16
$stream=[IO.File]::Open($OutputPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $bytes=[Text.UTF8Encoding]::new($false).GetBytes($json); $stream.Write($bytes,0,$bytes.Length) }
finally { $stream.Dispose() }
Write-Output $OutputPath
