#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^slot([1-9]|[1-5][0-9]|6[0-4])$')][string]$Slot,
    [Parameter(Mandatory=$true)][ValidateSet('codex','claude')][string]$Provider,
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$InstallRoot = "$env:ProgramFiles\CoChem\Pipeline4.2.2"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
$python = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$layoutPath = Join-Path $InstallRoot 'windows-layout.json'
$layout = Get-Content -LiteralPath $layoutPath -Raw | ConvertFrom-Json
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$logDirectory = Join-Path (Split-Path -Parent $layout.token_file) 'native-login-logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$id = [Guid]::NewGuid().ToString('N')
$logPath = Join-Path $logDirectory "$Provider-$Slot-$id.log"
$taskName = "CoChem-4.2.2-Login-$Slot-$id"
$values = @('-m','cochem_pipeline.windows','login','--layout',$layoutPath,'--slot',$Slot,
    '--provider',$Provider,'--executable',$Executable,'--log-path',$logPath)
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
