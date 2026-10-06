#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Executable,
    [ValidatePattern('^[a-zA-Z][a-zA-Z0-9-]*$')][string]$HeadlessSubcommand
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run this diagnostic on the Windows Antigravity host.' }
if (-not $Executable) {
    foreach ($name in @('agy.exe','agy.cmd','agy')) {
        $found = Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $found) { $Executable = $found.Source; break }
    }
}
if (-not $Executable) { throw 'Agy was not found. Supply -Executable with its installed Windows CLI path.' }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
Write-Host "Installed Agy command: $Executable"
Write-Host 'This diagnostic invokes version/help only. It does not run a model, inspect authentication, or dump environment variables.'

function Show-NativeHelp {
    param([string[]]$Arguments)
    $literalArgs = ($Arguments | ForEach-Object { "'" + $_.Replace("'","''") + "'" }) -join ','
    # A separate, bounded PowerShell process safely supports both native exe and
    # npm cmd shims. All child arguments are fixed version/help selectors.
    $body = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(`$false); & '" + $Executable.Replace("'","''") + "' @(" + $literalArgs + "); exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $start.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(20000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
            throw 'Agy help did not finish within twenty seconds. No headless contract was inferred.'
        }
        Write-Host ($Arguments -join ' ')
        Write-Output $stdout.GetAwaiter().GetResult()
        $errorText = $stderr.GetAwaiter().GetResult()
        if ($errorText) { Write-Output $errorText }
        Write-Host "Exit status: $($process.ExitCode)"
    }
    finally { $process.Dispose() }
}
Show-NativeHelp -Arguments @('--version')
Show-NativeHelp -Arguments @('--help')
if ($HeadlessSubcommand) { Show-NativeHelp -Arguments @($HeadlessSubcommand,'--help') }
Write-Host 'Verify from the installed help: a headless stdin prompt command, explicit model selector, native JSON result/session/model metadata, and subscription login behavior. Missing capabilities remain blockers; no Agy flags are guessed.'
