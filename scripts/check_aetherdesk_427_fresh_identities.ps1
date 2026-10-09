#Requires -Version 5.1
<# Read-only SYSTEM precheck. Only the exact six account/credential target names
are inspected. Credential blobs are never dereferenced, logged or serialized. #>
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') { throw 'Fresh identity precheck requires actual SYSTEM.' }
Import-Module (Join-Path $PSHOME 'Modules\CimCmdlets\CimCmdlets.psd1') -ErrorAction Stop
$root='C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006'
if ([IO.Path]::GetFullPath($PSScriptRoot) -ne $root) { throw 'Run only the frozen protected identity precheck.' }
$names=@(1..6 | ForEach-Object {"CoChem422Worker$_"})
$existing=@(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop | Where-Object {$_.Name -in $names})
if ($existing.Count) { throw 'Existing worker identity requires reviewed preservation; fresh-only provisioning refused.' }
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class CoChemCredentialPresence {
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern bool CredReadW(string target,uint type,uint flags,out IntPtr credential);
 [DllImport("advapi32.dll")] static extern void CredFree(IntPtr credential);
 public static bool Exists(string target) {
  IntPtr pointer;
  if(CredReadW(target,1,0,out pointer)) { try { return true; } finally { CredFree(pointer); } }
  int error=Marshal.GetLastWin32Error();
  if(error==1168) return false;
  throw new Win32Exception(error,"Cannot establish exact SYSTEM credential target absence.");
 }
}
'@
foreach ($number in 1..6) {
    if ([CoChemCredentialPresence]::Exists("CoChem422/slot$number")) { throw 'Existing SYSTEM worker credential target requires reviewed preservation; fresh-only provisioning refused.' }
}
foreach ($path in @('C:\ProgramData\CoChemPipeline427','C:\Users\ansac\CoChem427\controller.token')) {
    if (Test-Path -LiteralPath $path -ErrorAction Stop) { throw 'Existing pipeline state or token requires review; fresh-only provisioning refused.' }
}
$receipt=[ordered]@{schema='cochem-fresh-identity-precheck/1';nonce=$Nonce;system_sid='S-1-5-18';status='FRESH_TARGETS_ABSENT';local_accounts_checked=6;credential_targets_checked=6;credential_blobs_dereferenced=$false;checked_at_utc=[DateTime]::UtcNow.ToString('o')}
$stream=[IO.File]::Open((Join-Path $root 'identity-precheck.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try {$writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false));try {$writer.WriteLine(($receipt|ConvertTo-Json -Compress))}finally{$writer.Dispose()}}finally{$stream.Dispose()}
