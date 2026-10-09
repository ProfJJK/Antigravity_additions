#Requires -Version 5.1
<#
Default is read-only discovery/plan. Only explicit -Install, run manually in an
elevated ansac session, opens the pinned normal interactive PawnIO installer.
No silent switches, automatic reboot, sensor probe, task start or R: change.
#>
[CmdletBinding()]
param([switch]$Install)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$administrator=[Security.Principal.WindowsPrincipal]::new($identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Install -and (-not $administrator -or $identity.Name -ne 'AETHERDESK\ansac')) { throw '-Install requires the existing AETHERDESK\ansac account in an Administrator Windows PowerShell window; nothing was launched or written.' }
if ($PSVersionTable.PSEdition -ne 'Desktop' -or -not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell 5.1.' }
$stage='C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006'
$source=Join-Path $stage 'pawnio-2.1.0-reviewed\PawnIO_setup.exe'
$digest='a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0'
$signer='F380DCC9F706E2756A5047B832FFE719E1BC35F5'
$operatorSid=([Security.Principal.NTAccount]::new('AETHERDESK','ansac')).Translate([Security.Principal.SecurityIdentifier]).Value

function Assert-PlainPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or $Path.Contains("`0")) { throw 'An absolute local path without alternate streams is required.' }
    $item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse path refused.' }
        $item=if ($item -is [IO.FileInfo]) { $item.Directory } else { $item.Parent }
    }
}
function Hash-Bytes {
    param([byte[]]$Bytes)
    $sha=[Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
}
function Get-PrivateDirectoryEvidence {
    param([string]$Path)
    Assert-PlainPath $Path
    $acl=Get-Acl -LiteralPath $Path
    $trusted=@('S-1-5-18','S-1-5-32-544',$operatorSid)
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $trusted) { throw 'Private report directory has an unrelated owner.' }
    foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and [int64]$rule.FileSystemRights -ne 0) { throw 'Private report directory grants access to an unrelated trustee.' }
    }
    return [ordered]@{path=$Path; owner_sid=$owner; permitted_trustees=$trusted; unrelated_allow_grants=$false; sddl_sha256=(Hash-Bytes ([Text.Encoding]::UTF8.GetBytes($acl.Sddl)))}
}
function Write-NewPrivateFile {
    param([string]$Path,[string]$Text,[switch]$TaskXml)
    if (-not $Path.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Report must stay beneath the existing private stage.' }
    $null=Get-PrivateDirectoryEvidence (Split-Path -Parent $Path)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {
        [byte[]]$bytes=if ($TaskXml) { [Text.Encoding]::Unicode.GetPreamble()+[Text.Encoding]::Unicode.GetBytes($Text) } else { [Text.UTF8Encoding]::new($false).GetBytes($Text) }
        $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true)
    } finally { $stream.Dispose() }
}
function Read-RamState {
    $tasks=@(Get-ScheduledTask -TaskName 'Mount_CoChem_RAMDisk' -TaskPath '\' -ErrorAction Stop)
    if ($tasks.Count -ne 1) { throw 'The reviewed owner R: task is not uniquely readable.' }
    $task=$tasks[0]; $actions=@($task.Actions); $triggers=@($task.Triggers)
    if ($task.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM') -or -not $task.Settings.Enabled -or $actions.Count -ne 1 -or $triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskBootTrigger' -or -not $triggers[0].Enabled) { throw 'The owner R: SYSTEM boot-task contract changed.' }
    if ($actions[0].Execute -cne 'imdisk.exe' -or $actions[0].Arguments -cne '-a -s 8G -m R: -p "/fs:ntfs /q /y"' -or $actions[0].WorkingDirectory) { throw 'The owner R: action changed; preserve it and review before installation.' }
    $volume=@(Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='R:'" -OperationTimeoutSec 10 -ErrorAction Stop)
    if ($volume.Count -ne 1 -or [long]$volume[0].Size -ne 8589930496 -or $volume[0].FileSystem -ne 'NTFS' -or $volume[0].VolumeName) { throw 'The observed existing 8 GiB R: filesystem/blank-label contract changed.' }
    $xml=Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
    if ($xml -notmatch '^\s*<\?xml[^>]*encoding="UTF-16"') { throw 'Unexpected task XML encoding; preserve it for review rather than guessing a backup serialization.' }
    $info=Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
    return [ordered]@{xml=$xml; summary=[ordered]@{task='\Mount_CoChem_RAMDisk'; principal=$task.Principal.UserId; task_xml_sha256=(Hash-Bytes ([Text.Encoding]::Unicode.GetPreamble()+[Text.Encoding]::Unicode.GetBytes($xml))); last_result=$info.LastTaskResult; volume_size_bytes=[long]$volume[0].Size; expected_device_bytes=8589934592; filesystem=$volume[0].FileSystem; label=$volume[0].VolumeName; task_started=$false; volume_changed_by_helper=$false}}
}
function Read-PawnIOState {
    $registrations=@()
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)) {
        $base=$null; $key=$null
        try {
            $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,$view)
            $key=$base.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO',$false)
            $registrations += [ordered]@{view=[string]$view; present=($null -ne $key); version=$(if ($null -ne $key) {[string]$key.GetValue('DisplayVersion') } else {$null})}
        } finally { if ($null -ne $key) {$key.Dispose()}; if ($null -ne $base) {$base.Dispose()} }
    }
    $paths=@('C:\Program Files\PawnIO','C:\Program Files (x86)\PawnIO','C:\Windows\System32\drivers\PawnIO.sys')
    return [ordered]@{registrations=$registrations; service_key_present=(Test-Path -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\PawnIO'); existing_paths=@($paths | Where-Object { Test-Path -LiteralPath $_ }); drivers=@(Get-CimInstance Win32_SystemDriver -Filter "Name='PawnIO'" -OperationTimeoutSec 10 -ErrorAction Stop | Select-Object Name,State,Started,StartMode,PathName)}
}
function Read-RestartFlags {
    $session=Get-ItemProperty -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager'
    $rename=$session.PSObject.Properties['PendingFileRenameOperations']
    return [ordered]@{component_servicing=(Test-Path -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'); windows_update=(Test-Path -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'); pending_file_rename=($null -ne $rename -and $null -ne $rename.Value -and @($rename.Value).Count -gt 0); scope='Flags only; existing flags do not establish a new installer-caused restart requirement.'}
}

$report=[ordered]@{schema='cochem-pawnio-install/1'; observed_at_utc=[DateTime]::UtcNow.ToString('o'); mode='READ_ONLY_PLAN'; identity=$identity.Name; administrator=$administrator; script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant(); source=$source; expected_sha256=$digest; installer_arguments='-install'; interactive=$true; install_invoked=$false; exit_code=$null; ram_before=$null; pawnio_before=$null; restart_before=$null; holds=@(); automatic_reboot=$false; next='Explicit reviewed -Install only; inspect the actual installer dialogs and decline any restart until separately reviewed. No exit code or registry record alone establishes driver/SYSTEM sensor acceptance.'}
if (-not $administrator) { $report.holds += 'Plan observed as ordinary user; -Install requires a manually opened Administrator Windows PowerShell under ansac.' }
$sourceHandle=$null
try {
    $report.stage_acl=Get-PrivateDirectoryEvidence $stage
    Assert-PlainPath $source
    # Keep the verified file open without write/delete sharing through launch.
    $sourceHandle=[IO.File]::Open($source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    if ($sourceHandle.Length -ne 3225016) { throw 'Pinned PawnIO installer length changed.' }
    $sha=[Security.Cryptography.SHA256]::Create()
    try { $actual=[BitConverter]::ToString($sha.ComputeHash($sourceHandle)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
    if ($actual -ne $digest) { throw 'Pinned PawnIO installer digest changed.' }
    $signature=Get-AuthenticodeSignature -LiteralPath $source
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or $signature.SignerCertificate.Thumbprint -ne $signer) { throw 'Pinned PawnIO signer or trust changed.' }
    $report.verified_source=[ordered]@{bytes=$sourceHandle.Length; sha256=$actual; signature=[string]$signature.Status; signer_thumbprint=$signature.SignerCertificate.Thumbprint}
    $report.pawnio_before=Read-PawnIOState
    if (@($report.pawnio_before.registrations | Where-Object {$_.present}).Count -or $report.pawnio_before.service_key_present -or $report.pawnio_before.existing_paths.Count -or $report.pawnio_before.drivers.Count) { throw 'Existing PawnIO registration/service/files detected; preserve and review them. This helper never reinstalls or upgrades.' }
    $report.restart_before=Read-RestartFlags
    $ram=Read-RamState; $report.ram_before=$ram.summary
    if (-not $Install) { $report | ConvertTo-Json -Depth 10; return }
    $stamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
    $prefix=Join-Path $stage ('pawnio-2.1.0-install-'+$stamp)
    $report.task_xml_backup=$prefix+'-ram-task.xml'
    Write-NewPrivateFile $report.task_xml_backup $ram.xml -TaskXml
    $report.mode='INSTALL_READY'; Write-NewPrivateFile ($prefix+'-before.json') ($report | ConvertTo-Json -Depth 10)
    $report.mode='INSTALL_ATTEMPT'; $report.install_attempted=$true; $report.install_invoked=$null
    try {
        # The user needs this normal installer UI. No RunAs, silent or reboot flags.
        $process=Start-Process -FilePath $source -ArgumentList '-install' -WindowStyle Normal -Wait -PassThru
        $report.install_invoked=$true
        try { $report.exit_code=$process.ExitCode } finally { $process.Dispose() }
    } catch { $report.holds += 'Installer launch/wait: '+$_.Exception.Message }
    foreach ($name in @('pawnio_after','restart_after','ram_after')) {
        try {
            switch ($name) {
                'pawnio_after' {$report[$name]=Read-PawnIOState}
                'restart_after' {$report[$name]=Read-RestartFlags}
                'ram_after' {$report[$name]=(Read-RamState).summary}
            }
        } catch { $report[$name]=[ordered]@{unavailable=$true; error=$_.Exception.Message}; $report.holds += 'Post-install observation unavailable: '+$name }
    }
    if ($report.ram_after.Contains('task_xml_sha256')) {
        $report.ram_task_xml_unchanged=($report.ram_after.task_xml_sha256 -eq $report.ram_before.task_xml_sha256)
        if (-not $report.ram_task_xml_unchanged) { $report.holds += 'R: task XML differs after installer; no automatic restoration or task change is attempted.' }
    }
    $report.mode='INSTALL_ATTEMPT_REVIEW_REQUIRED'
    $report.holds += 'Actual dialog outcome and any requested restart require operator review; no return-code success mapping or automatic reboot is assumed.'
    $report.holds += 'Driver/device ACLs and protected SYSTEM CPU temperature acceptance remain separate unrun checks.'
    $final=$prefix+'-after.json'; Write-NewPrivateFile $final ($report | ConvertTo-Json -Depth 12); Write-Output $final
} catch {
    if ($Install) { throw }
    $report.holds += $_.Exception.Message
    $report | ConvertTo-Json -Depth 10
} finally { if ($null -ne $sourceHandle) { $sourceHandle.Dispose() } }
