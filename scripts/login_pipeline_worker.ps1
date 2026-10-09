#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^slot([1-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-6])$')][string]$Slot,
    [Parameter(Mandatory=$true)][ValidateSet('codex','claude','gemini')][string]$Provider,
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$LoginContract = '',
    [string]$InstallRoot = "$env:ProgramFiles\CoChem\Pipeline4.2.7-r2"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Use this PowerShell installation's built-in ACL cmdlet module even when the
# caller inherited a different PowerShell edition's PSModulePath.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force -ErrorAction Stop

function Assert-ProtectedItem {
    param([IO.FileSystemInfo]$Item)
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Privileged paths cannot use reparse points: $($Item.FullName)" }
    $acl = Get-Acl -LiteralPath $Item.FullName
    $trusted = @('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw "Untrusted owner of privileged code: $($Item.FullName)" }
    foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        $sid = $rule.IdentityReference.Value
        if ($rule.AccessControlType -eq 'Allow' -and $sid -notin $trusted -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ([int64]$rule.FileSystemRights -band 0x500D0116)) {
            throw "Untrusted writes to privileged code: $($Item.FullName)"
        }
    }
}

function Assert-ProtectedLoginInstallation {
    param([Parameter(Mandatory=$true)][string]$Path)
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $programFiles = [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\')
    if (-not $resolved.StartsWith($programFiles+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Pipeline login requires its protected Program Files installation.' }
    $current = Get-Item -LiteralPath $resolved -Force
    if (-not $current.PSIsContainer) { throw 'Pipeline login requires an installed directory.' }
    while ($null -ne $current) {
        Assert-ProtectedItem -Item $current
        if ($current.FullName.TrimEnd('\') -eq $programFiles) { break }
        $current = $current.Parent
    }
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($resolved)
    while ($queue.Count -gt 0) {
        $item = Get-Item -LiteralPath ($queue.Dequeue()) -Force
        Assert-ProtectedItem -Item $item
        if ($item.PSIsContainer) {
            foreach ($child in (Get-ChildItem -LiteralPath $item.FullName -Force)) { $queue.Enqueue($child.FullName) }
        }
    }
    return $resolved
}

# Validate the complete machine-protected interpreter/package tree before
# scheduling it as SYSTEM, matching the independent supervisor login helper.
$InstallRoot = Assert-ProtectedLoginInstallation -Path $InstallRoot
$python = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$layoutPath = Join-Path $InstallRoot 'windows-layout.json'
$layout = Get-Content -LiteralPath $layoutPath -Raw | ConvertFrom-Json
if ($null -eq $layout.slots.PSObject.Properties[$Slot]) { throw 'Choose a slot present in the installed windows-layout.json; login never provisions accounts.' }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
if ($Provider -eq 'gemini') {
    if (-not $LoginContract) { throw 'Agy login requires a protected reviewed JSON LoginContract with actual native subscription-login arguments; no Agy flags are assumed.' }
    $LoginContract = (Resolve-Path -LiteralPath $LoginContract).Path
}
elseif ($LoginContract) { throw 'LoginContract is only supported for the gemini (Agy) provider.' }
$logDirectory = Join-Path (Split-Path -Parent $layout.token_file) 'native-login-logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$id = [Guid]::NewGuid().ToString('N')
$logPath = Join-Path $logDirectory "$Provider-$Slot-$id.log"
$taskName = "CoChem-4.2.2-Login-$Slot-$id"
$values = @('-I','-m','cochem_pipeline.windows','login','--layout',$layoutPath,'--slot',$Slot,
    '--provider',$Provider,'--executable',$Executable,'--log-path',$logPath)
if ($LoginContract) { $values += @('--login-contract',$LoginContract) }
$quoted = foreach ($value in $values) {
    if ($value.Contains('"') -or $value.Contains("`n") -or $value.Contains("`r")) { throw 'Invalid task argument.' }
    '"' + $value.TrimEnd('\') + '"'
}
$action = New-ScheduledTaskAction -Execute $python -Argument ($quoted -join ' ') -WorkingDirectory $InstallRoot
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 11) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null
try {
    $requestedStart = Get-Date
    Start-ScheduledTask -TaskName $taskName
    Write-Host "Native $Provider subscription login for $Slot. Follow the provider's URL/code below in your browser."
    Write-Host 'This authenticates this worker account only. No credential is copied from your regular account.'
    $lastText = ''
    $deadline = (Get-Date).AddMinutes(11)
    do {
        Start-Sleep -Milliseconds 500
        if (Test-Path -LiteralPath $logPath) {
            $stream = [IO.File]::Open($logPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
            try {
                $reader = [IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
                try { $text = $reader.ReadToEnd() }
                finally { $reader.Dispose() }
            }
            finally { $stream.Dispose() }
            if ($text.Length -gt $lastText.Length) { Write-Host -NoNewline $text.Substring($lastText.Length) }
            $lastText = $text
        }
        $task = Get-ScheduledTask -TaskName $taskName
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        if ((Get-Date) -gt $deadline) { Stop-ScheduledTask -TaskName $taskName; throw 'Native login timed out.' }
    } while ($task.State -in @('Running','Queued') -or $info.LastRunTime -lt $requestedStart.AddSeconds(-1))
    if ($info.LastTaskResult -ne 0) { throw "Native login exited with status $($info.LastTaskResult). Private diagnostic log: $logPath" }
    Write-Host "Native login command exited successfully. Verify this account's subscription status before dispatch. Log: $logPath"
}
finally {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
