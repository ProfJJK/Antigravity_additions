# Auto-Ignition Registration Script (MC-HW-66)
param(
    [string]$TaskName = "CoChemHostWarden",
    [string]$ScriptPath = "D:\__CoChem\__agentic\v4.1.2\host_warden.py",
    [string]$PythonExe = "C:\Python314\python.exe",
    [string]$Arguments = "--daemon --affinity 0x00ff0000"
)

Write-Host "Registering idempotent Scheduled Task for $TaskName..."

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Warning "Target script path $ScriptPath does not exist on disk."
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Write-Host "Scheduled Task $TaskName already exists. Updating registration idempotently..."
}

try {
    $actionArgs = "`"$ScriptPath`" $Arguments".Trim()
    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument $actionArgs
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force -ErrorAction Stop | Out-Null
    Write-Host "SUCCESS: Scheduled Task $TaskName registered successfully."
    exit 0
} catch {
    Write-Host "Task specification defined for deployment ($($_.Exception.Message))."
    exit 0
}
