#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Config
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'RAM workspace provisioning requires native Windows, ImDisk and AWEAlloc.'
}
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') {
    throw 'Run this protected helper as SYSTEM after provisioning the dedicated worker identities.'
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$Config = (Resolve-Path -LiteralPath $Config).Path
if (-not $Python.StartsWith($env:ProgramFiles.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use the protected pipeline Python under Program Files.'
}
# Python verifies executable/ancestor ACLs, exact mount reparse data, the ImDisk
# kernel backing flags, actual NTFS and worker isolation. Existing files are
# moved into a same-volume before-ramdisk backup before any mount is created.
# No credential/home directories are read, copied or redirected.
& $Python -I -m cochem_pipeline.ramdisk --config $Config
if ($LASTEXITCODE -ne 0) { throw 'RAM disk provisioning or native verification failed; dispatch remains disabled.' }
