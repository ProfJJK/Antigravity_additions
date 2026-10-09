#Requires -Version 5.1
<#
Default: verify a pinned byte inventory and emit a read-only plan. -Apply requires
an elevated administrator and only copies code into two fresh protected roots.
No payload is executed. No account, credential, task, service, driver, database,
configuration, RAM disk or supervisor budget is created or changed.
Partial destinations are preserved on failure, never deleted or reused.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ManifestSha256,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop

function Get-LocalPath {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Contains("`0") -or $Path.Substring(2).Contains(':')) { throw 'An explicit local path without alternate streams is required.' }
    [IO.Path]::GetFullPath($Path).TrimEnd('\')
}
function Assert-NoReparseAncestors {
    param([string]$Path)
    $current = Get-Item -LiteralPath $Path -Force
    while ($null -ne $current) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse path refused: $($current.FullName)" }
        $current = if ($current -is [IO.DirectoryInfo]) { $current.Parent } else { $current.Directory }
    }
}
function Assert-ProtectedPath {
    param([string]$Path)
    $current = Get-Item -LiteralPath $Path -Force
    while ($null -ne $current) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected ancestry contains a reparse point.' }
        $acl = Get-Acl -LiteralPath $current.FullName
        $trusted = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw "Untrusted protected owner: $($current.FullName)" }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            $sid = $rule.IdentityReference.Value
            if ($rule.AccessControlType -eq 'Allow' -and $sid -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)) { throw "Untrusted protected writer: $($current.FullName)" }
        }
        if ($current.FullName.TrimEnd('\') -eq $script:programFiles) { return }
        $current = if ($current -is [IO.DirectoryInfo]) { $current.Parent } else { $current.Directory }
    }
    throw 'Protected ancestry did not reach Program Files.'
}
function New-CodeAcl {
    param([bool]$Directory)
    $acl = if ($Directory) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $inherit = if ($Directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($grant in @(@('S-1-5-18','FullControl'),@('S-1-5-32-544','FullControl'),@('S-1-5-32-545','ReadAndExecute'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($grant[0]),[Security.AccessControl.FileSystemRights]$grant[1],$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    $acl
}
function New-ProtectedDirectory {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) { throw "Destination already exists; preserve and review it: $Path" }
    Assert-ProtectedPath (Split-Path -Parent $Path)
    $directory = [IO.DirectoryInfo]::new($Path)
    $directory.Create((New-CodeAcl $true))
    Assert-ProtectedPath $Path
}
function Initialize-FileIdentity {
    if ('CoChemStagedFileIdentity' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class CoChemStagedFileIdentity {
 [StructLayout(LayoutKind.Sequential)] struct Info {
  public uint attributes; public System.Runtime.InteropServices.ComTypes.FILETIME created,accessed,written;
  public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow;
 }
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle handle,out Info info);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(SafeFileHandle handle,StringBuilder path,uint size,uint flags);
 public static void Check(FileStream stream,string expected) {
  Info info; if(!GetFileInformationByHandle(stream.SafeFileHandle,out info)) throw new Win32Exception();
  if(info.links!=1 || (info.attributes & 0x400)!=0) throw new IOException("Hardlinks and reparse files are forbidden.");
  var path=new StringBuilder(32768); uint count=GetFinalPathNameByHandle(stream.SafeFileHandle,path,(uint)path.Capacity,0);
  if(count==0 || count>=path.Capacity) throw new IOException("Cannot resolve file handle custody.");
  string actual=path.ToString(); if(actual.StartsWith(@"\\?\")) actual=actual.Substring(4);
  if(!String.Equals(actual,Path.GetFullPath(expected),StringComparison.OrdinalIgnoreCase)) throw new IOException("File handle resolved through a different path.");
 }
}
'@
}
function Open-VerifiedFile {
    param([string]$Path,[string]$Sha256,[long]$Length)
    Assert-NoReparseAncestors $Path
    $stream = [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        [CoChemStagedFileIdentity]::Check($stream,$Path)
        if ($stream.Length -ne $Length) { throw "Source length changed: $Path" }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
        if ($digest -ne $Sha256) { throw "Source digest changed: $Path" }
        $stream.Position = 0
        return ,$stream
    } catch { $stream.Dispose(); throw }
}
function Read-Inventory {
    param([string]$Path,[string]$Expected)
    $Path = Get-LocalPath $Path
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -gt 16777216) { throw 'Payload manifest exceeds 16 MiB.' }
    $stream = Open-VerifiedFile $Path $Expected $size
    try { $reader = [IO.StreamReader]::new($stream,[Text.Encoding]::UTF8); try { $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() } } finally { $stream.Dispose() }
}
function Get-PayloadPlan {
    param($Inventory)
    if ($Inventory.schema -ne 'cochem-protected-payload-inventory/1' -or $Inventory.host -ne 'AETHERDESK') { throw 'Unrecognized host payload inventory.' }
    $allowed = @('Toolchain4.2.7-windows-20261006','CpuSensors4.2.7-windows-20261006')
    $roots = @($Inventory.roots)
    if ($roots.Count -ne $allowed.Count -or @($roots | Sort-Object -Unique).Count -ne $allowed.Count) { throw 'Payload roots must be the two dedicated versioned roots.' }
    foreach ($root in $roots) { if ($root -notin $allowed) { throw 'Unexpected protected payload root.' } }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $files = @($Inventory.files)
    if ($files.Count -eq 0 -or $files.Count -gt 50000) { throw 'Payload inventory file count is outside bounds.' }
    $total = [long]0
    foreach ($file in $files) {
        $relative = [string]$file.destination
        if ($relative -notmatch '^[A-Za-z0-9_.-]+[\\/]' -or $relative -match '[\x00-\x1f<>:"|?*]' -or @($relative -split '[\\/]' | Where-Object { $_ -in @('','.', '..') -or $_.EndsWith(' ') -or $_.EndsWith('.') -or $_ -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)' }).Count) { throw 'Unsafe relative payload destination.' }
        $relative = $relative.Replace('/','\')
        if ($relative.Split('\')[0] -notin $allowed -or -not $seen.Add($relative)) { throw 'Unexpected or duplicate payload destination.' }
        if ([string]$file.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or $file.length -lt 0 -or $file.length -gt 1073741824) { throw 'Invalid payload digest or file length.' }
        $total += [long]$file.length
        if ($total -gt 4294967296) { throw 'Payload inventory exceeds 4 GiB.' }
        [pscustomobject]@{source=(Get-LocalPath $file.source);destination=(Join-Path $script:base $relative);relative=$relative;sha256=[string]$file.sha256;length=[long]$file.length}
    }
    foreach ($root in $allowed) { if (-not @($seen | Where-Object { $_.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) }).Count) { throw 'Every payload root must have reviewed files.' } }
}
function Copy-VerifiedPayload {
    param($File)
    $source = Open-VerifiedFile $File.source $File.sha256 $File.length
    try {
        $parent = Split-Path -Parent $File.destination
        Assert-ProtectedPath $parent
        $target = [IO.FileStream]::new($File.destination,[IO.FileMode]::CreateNew,[Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,1048576,[IO.FileOptions]::None,(New-CodeAcl $false))
        try { $source.CopyTo($target); $target.Flush($true) } finally { $target.Dispose() }
    } finally { $source.Dispose() }
    Assert-ProtectedPath $File.destination
    $verify = Open-VerifiedFile $File.destination $File.sha256 $File.length
    $verify.Dispose()
}

# Invocation boundary: tests load the actual functions above without applying ACLs.
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Windows is required.' }
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use Windows PowerShell 5.1 for its native protected-file creation APIs.' }
$programFiles = Get-LocalPath $env:ProgramFiles
$base = Join-Path $programFiles 'CoChem'
Initialize-FileIdentity
$inventory = Read-Inventory $Manifest $ManifestSha256
$files = @(Get-PayloadPlan $inventory)
foreach ($root in $inventory.roots) {
    $destination = Join-Path $base $root
    if (Test-Path -LiteralPath $destination) { throw "Preserve existing payload destination: $destination" }
}
Assert-ProtectedPath $programFiles
if (Test-Path -LiteralPath $base) { Assert-ProtectedPath $base }
# Complete read-only source attestation before creating any destination. Repeat
# it on the held read handle during each copy, so source drift never wins a race.
foreach ($file in $files) { $stream = Open-VerifiedFile $file.source $file.sha256 $file.length; $stream.Dispose() }
$copied = 0
if ($Apply) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '-Apply requires an elevated administrator; no payload destination was created.' }
    if (-not (Test-Path -LiteralPath $base)) { New-ProtectedDirectory $base }
    foreach ($root in $inventory.roots) { New-ProtectedDirectory (Join-Path $base $root) }
    $directories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $files) {
        $parent = Split-Path -Parent $file.destination
        while ($parent -ne $base) { $null = $directories.Add($parent); $parent = Split-Path -Parent $parent }
    }
    foreach ($directory in ($directories | Sort-Object Length)) {
        if (-not (Test-Path -LiteralPath $directory)) { New-ProtectedDirectory $directory } else { Assert-ProtectedPath $directory }
    }
    foreach ($file in $files) { Copy-VerifiedPayload $file; $copied++ }
}
[ordered]@{
    schema='cochem-protected-payload-stage/1';mode=$(if ($Apply) {'PAYLOADS_COPIED'} else {'READ_ONLY_PLAN'})
    manifest_sha256=$ManifestSha256.ToLowerInvariant();files_verified=$files.Count;files_copied=$copied
    protected_roots=@($inventory.roots | ForEach-Object { Join-Path $base $_ })
    payloads_executed=0;tasks_changed=0;accounts_changed=0;driver_installed=$false
    supervisor_history='UNRESOLVED_UNTOUCHED';activation_ready=$false
    next='Separate reviewed driver installation and stopped pipeline provisioning; keep supervisor installation/migration/activation held.'
} | ConvertTo-Json -Depth 5
