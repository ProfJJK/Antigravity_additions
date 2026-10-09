#Requires -Version 5.1
# Offline evidence projection only: no task, service, driver or ImDisk calls.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$workspace=Split-Path -Parent $PSScriptRoot
$sharedPath=Join-Path $workspace 'administrator-evidence-shared-20261007T043110Z-8b16a518.json'
$shared=Get-Content -LiteralPath $sharedPath -Raw | ConvertFrom-Json
$privatePath=[string]$shared.details_private_path
if ((Get-FileHash -LiteralPath $privatePath -Algorithm SHA256).Hash -ne $shared.details_private_sha256) { throw 'Private capture no longer matches its recorded hash.' }
$original=Get-Content -LiteralPath $privatePath -Raw | ConvertFrom-Json
if ($original.schema -ne 'cochem-windows-admin-details/1' -or $original.identity -ne 'AETHERDESK\ansac' -or -not $original.administrator) { throw 'Expected the recorded administrator capture.' }
$selected=@($original.ramdisk_startup_provenance.task_matches | Where-Object { $_.name -eq 'Mount_CoChem_RAMDisk' -and $_.path -eq '\' })
if ($selected.Count -ne 1 -or $selected[0].actions.Count -ne 1) { throw 'Expected exactly the identified R: task action.' }
if ($selected[0].actions[0].execute -ne 'imdisk.exe' -or $selected[0].actions[0].private_arguments -cne '-a -s 8G -m R: -p "/fs:ntfs /q /y"') { throw 'The recorded R: task action differs from the reviewed command; do not infer its semantics.' }

# Load only the pure projection function, not the administrator discovery runner.
$wrapper=Join-Path $PSScriptRoot 'share-admin-discovery.ps1'
$tokens=$null; $parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($wrapper,[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw 'Sharing script does not parse.' }
$function=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Remove-PrivateDiscoveryFields'},$true))
if ($function.Count -ne 1) { throw 'Expected one reviewed pure projection function.' }
. ([ScriptBlock]::Create($function[0].Extent.Text))
$task=Remove-PrivateDiscoveryFields $selected[0]
$holds=Remove-PrivateDiscoveryFields $original.holds
if ($holds.Count -ne $original.holds.Count) { throw 'Hold count changed during projection.' }
for ($index=0; $index -lt $holds.Count; $index++) {
    if ($holds[$index] -isnot [string] -or $holds[$index] -cne $original.holds[$index]) { throw 'Recorded hold text was not preserved.' }
}
$record=[ordered]@{
    schema='cochem-administrator-r-startup-followup/1'
    reconstructed_at_utc=[DateTime]::UtcNow.ToString('o')
    scope='Curated metadata reconstructed from existing user-run Administrator captures; no new privileged observation or task execution'
    details_observed_at_utc=$original.observed_at_utc
    prerequisite_observed_at_utc=$shared.prerequisites.observed_at_utc
    capture_identity=$original.identity; capture_administrator=$original.administrator; capture_system=$original.system
    provenance=[ordered]@{
        original_shared_file=[IO.Path]::GetFileName($sharedPath)
        original_shared_sha256=(Get-FileHash -LiteralPath $sharedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        private_details_file=[IO.Path]::GetFileName($privatePath)
        private_details_sha256=$shared.details_private_sha256
        private_details_hash_reverified=$true
        original_prerequisite_sha256=$shared.prerequisite_private_sha256
        companion_script_sha256=$original.script_sha256
        corrected_sharing_script_sha256=(Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash.ToLowerInvariant()
        projection_bug='PowerShell-wrapped string array items satisfied the generic object test and became Length objects. Scalars now stay atomic, array positions including null remain intact, and private fields are still removed.'
        original_reports_preserved=$true; administrator_rerun_for_projection=$false
    }
    ramdisk=[ordered]@{
        volume=$shared.prerequisites.ram_volume
        volume_scope='Logical-disk metadata from the original prerequisite observation; ImDisk device evidence has the later details timestamp.'
        imdisk=Remove-PrivateDiscoveryFields $original.imdisk
        owner_startup_task=$task
        configured_action_semantics=[ordered]@{
            create_new_virtual_disk=$true; requested_size_bytes=8589934592; mount='R:'
            filesystem='NTFS'; quick_format_after_creation=$true; noninteractive_format=$true
            explicit_volume_label=$false; configured_executable='imdisk.exe'
            historical_executable_resolution_verified=$false
            interpretation='The existing boot action requests creation and formats the newly created disk. It was not replayed. This is not a claim that invoking it while R: exists would safely reformat or adopt the existing volume.'
        }
        existing_startup_vbs=Remove-PrivateDiscoveryFields $original.startup
        legacy_gate_status='Unchanged VBS expects CoChem_EnsureRamdisk and COCHEM_RAM. The actual task is Mount_CoChem_RAMDisk and the observed R: label is blank; preserve actual task/volume while reviewing this separate legacy gate.'
        preservation=[ordered]@{volume_formatted=$false; volume_resized=$false; volume_relabeled=$false; volume_recreated=$false; task_started=$false; task_modified=$false; task_xml_modified=$false; startup_vbs_modified=$false; reboot_requested=$false}
    }
    sensor_and_restart_metadata=[ordered]@{
        pawnio_registry=Remove-PrivateDiscoveryFields $original.pawnio_registry
        pawnio_service=Remove-PrivateDiscoveryFields $original.pawnio_service
        pawnio_system_drivers=Remove-PrivateDiscoveryFields $original.pawnio_system_drivers
        pending_reboot=Remove-PrivateDiscoveryFields $original.pending_reboot
        monitor_processes=$shared.prerequisites.hardware_monitor
        monitor_scope='Only HWiNFO64.exe, LibreHardwareMonitor.exe and OpenHardwareMonitor.exe at the prerequisite timestamp; no inference about every monitoring program.'
        cpu_sensor_measurement_performed=$false; driver_installed=$false; driver_loaded=$false
    }
    restored_capture_holds=$holds
    remaining_scope='SYSTEM adoption/recovery, protected CPU temperatures and controlled reboot acceptance remain unrun. Four shared slots and Chapter 06 routing remain unchanged. Existing budgets, credentials, databases and task XML were not modified.'
    regression_checks=[ordered]@{pure_projection_checks_passed=15; powershell='5.1'; parse_errors=0; privileged_tests_run=$false}
    primary_sources=@(
        'https://github.com/LTRData/ImDisk/blob/master/cli/imdisk.c',
        'https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/format'
    )
}
$json=$record | ConvertTo-Json -Depth 24
if ($json -match 'private_arguments|"/fs:ntfs /q /y"') { throw 'Raw private task arguments must not be published.' }
$destination='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\administrator-r-startup-followup.json'
$stream=[IO.File]::Open($destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try { $bytes=[Text.UTF8Encoding]::new($false).GetBytes($json); $stream.Write($bytes,0,$bytes.Length) }
finally { $stream.Dispose() }
Write-Output $destination
