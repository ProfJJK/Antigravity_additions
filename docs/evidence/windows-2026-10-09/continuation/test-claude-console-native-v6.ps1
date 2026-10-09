#Requires -Version 5.1
<# Inert ordinary Windows fixture. Launch only in a NEW hidden owned console.
   Native synthetic key events are written only after proving this process is
   the console's sole member and the launch nonce matches its inherited value.
   No providers, tasks, worker accounts, credentials or user console input. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Helper,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedHelperSha256,
    [Parameter(Mandatory=$true)][string]$Report,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][int]$LauncherPid
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$receipt=[ordered]@{
    schema='cochem-claude-console-native-fixture/1'
    status='HELD'
    fixture_pid=$PID
    launcher_pid=$LauncherPid
    nonce=$Nonce
    powershell_version=$PSVersionTable.PSVersion.ToString()
    is_64_bit_process=[Environment]::Is64BitProcess
    helper_sha256=$ExpectedHelperSha256
    user_console_accessed=$false
    native_provider_jobs_executed=0
    system_tasks_created=0
    synthetic_events_written=0
    borrowed_stdin_closed=$false
    started_utc=[DateTime]::UtcNow.ToString('o')
}
$phase='launch_binding';$exitCode=2
try {
    if($env:COCHEM_CONSOLE_FIXTURE_NONCE -cne $Nonce -or $LauncherPid -le 0 -or $LauncherPid -eq $PID){throw 'FIXTURE_LAUNCH_BINDING'}
    Import-Module -Name ([IO.Path]::Combine($PSHOME,'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
    if((Get-FileHash -LiteralPath $Helper -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedHelperSha256){throw 'FIXTURE_HELPER_PIN'}
    $phase='native_declarations'
    $null=Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CoChemOwnedConsoleFixtureV6 {
    [StructLayout(LayoutKind.Explicit, Size=20)]
    public struct InputRecord {
        [FieldOffset(0)] public ushort EventType;
        [FieldOffset(4)] public int KeyDown;
        [FieldOffset(8)] public ushort RepeatCount;
        [FieldOffset(10)] public ushort VirtualKeyCode;
        [FieldOffset(12)] public ushort VirtualScanCode;
        [FieldOffset(14)] public ushort UnicodeChar;
        [FieldOffset(16)] public uint ControlKeyState;
    }
    [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetFileType(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetConsoleProcessList([Out] uint[] processes, uint length);
    [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetNumberOfConsoleInputEvents(IntPtr handle, out uint count);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool WriteConsoleInputW(IntPtr handle, InputRecord[] records, uint length, out uint written);
}
'@
    $phase='console_ownership'
    $members=[uint32[]]::new(16)
    $memberCount=[CoChemOwnedConsoleFixtureV6]::GetConsoleProcessList($members,$members.Length)
    if($memberCount -ne 1 -or $members[0] -ne $PID -or $members[0] -eq $LauncherPid){throw 'FIXTURE_OWNED_CONSOLE_REQUIRED'}
    $receipt.console_process_ids=@([int]$members[0])
    $receipt.separate_owned_console_verified=$true
    $handle=[CoChemOwnedConsoleFixtureV6]::GetStdHandle(-10)
    $mode=[uint32]0
    $fileType=[CoChemOwnedConsoleFixtureV6]::GetFileType($handle)
    if($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1) -or $fileType -ne 2 -or
       -not [CoChemOwnedConsoleFixtureV6]::GetConsoleMode($handle,[ref]$mode)){throw 'FIXTURE_CHARACTER_STDIN_REQUIRED'}
    $receipt.stdin_file_type=$fileType
    $receipt.stdin_console_mode=$mode
    . $Helper

    $phase='clear_empty_console'
    $empty=Clear-ClaudeConsoleInput
    if(-not $empty.native_queue_flushed -or -not $empty.console_queue_clear -or $empty.bound_reached){throw 'FIXTURE_EMPTY_CLEAR_FAILED'}
    $receipt.empty_queue_result=$empty

    $phase='write_native_synthetic_events'
    $records=[CoChemOwnedConsoleFixtureV6+InputRecord[]]::new(3)
    for($index=0;$index -lt $records.Length;$index++){
        $record=[CoChemOwnedConsoleFixtureV6+InputRecord]::new()
        $record.EventType=1;$record.KeyDown=1;$record.RepeatCount=1
        $record.UnicodeChar=[uint16](@(81,80,13)[$index])
        $record.VirtualKeyCode=$record.UnicodeChar
        $records[$index]=$record
    }
    $written=[uint32]0
    if(-not [CoChemOwnedConsoleFixtureV6]::WriteConsoleInputW($handle,$records,$records.Length,[ref]$written) -or $written -ne 3){throw 'FIXTURE_SYNTHETIC_WRITE_FAILED'}
    $receipt.synthetic_events_written+=$written
    $queued=[uint32]0
    if(-not [CoChemOwnedConsoleFixtureV6]::GetNumberOfConsoleInputEvents($handle,[ref]$queued) -or $queued -lt 3){throw 'FIXTURE_NATIVE_EVENTS_NOT_QUEUED'}
    $receipt.native_events_before_clear=$queued
    $phase='clear_native_queue'
    $native=Clear-ClaudeConsoleInput
    if(-not $native.native_queue_flushed -or -not $native.console_queue_clear -or $native.bound_reached){throw 'FIXTURE_NATIVE_CLEAR_FAILED'}
    $queued=[uint32]0
    if(-not [CoChemOwnedConsoleFixtureV6]::GetNumberOfConsoleInputEvents($handle,[ref]$queued) -or $queued -ne 0){throw 'FIXTURE_NATIVE_QUEUE_NOT_EMPTY'}
    $receipt.native_queue_result=$native
    $receipt.native_events_after_clear=$queued

    $phase='write_cached_repeat_fixture'
    $record=[CoChemOwnedConsoleFixtureV6+InputRecord]::new()
    $record.EventType=1;$record.KeyDown=1;$record.RepeatCount=31
    $record.UnicodeChar=81;$record.VirtualKeyCode=81
    $written=[uint32]0
    if(-not [CoChemOwnedConsoleFixtureV6]::WriteConsoleInputW($handle,@($record),1,[ref]$written) -or $written -ne 1){throw 'FIXTURE_SYNTHETIC_WRITE_FAILED'}
    $receipt.synthetic_events_written+=$written
    $key=[Console]::ReadKey($true)
    if($key.KeyChar -ne [char]81 -or -not [Console]::KeyAvailable){throw 'FIXTURE_MANAGED_CACHE_NOT_PRIMED'}
    $key=$null
    $phase='clear_managed_cached_repeat'
    $cached=Clear-ClaudeConsoleInput
    $receipt.managed_cached_repeat_result=$cached
    # .NET Framework may cache the original full repeat record. Require a
    # nonempty bounded managed drain and verified empty queues, not an assumed
    # decrement convention for the priming ReadKey call.
    if(-not $cached.native_queue_flushed -or -not $cached.console_queue_clear -or $cached.bound_reached -or
       $cached.keys_discarded -lt 1 -or $cached.keys_discarded -gt 31){throw 'FIXTURE_MANAGED_CACHE_CLEAR_FAILED'}
    $phase='borrowed_handle_preserved'
    $mode=[uint32]0
    if(-not [CoChemOwnedConsoleFixtureV6]::GetConsoleMode($handle,[ref]$mode) -or [CoChemOwnedConsoleFixtureV6]::GetFileType($handle) -ne 2){throw 'FIXTURE_BORROWED_HANDLE_CHANGED'}
    if([Console]::KeyAvailable){throw 'FIXTURE_MANAGED_QUEUE_NOT_EMPTY'}
    if((Get-FileHash -LiteralPath $Helper -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedHelperSha256){throw 'FIXTURE_HELPER_PIN'}
    $receipt.borrowed_stdin_still_valid=$true
    $receipt.status='OWNED_WINDOWS_PS5_NATIVE_AND_MANAGED_CLEAR_VERIFIED'
    $exitCode=0
} catch {
    $receipt.failure=[ordered]@{phase=$phase;error_type=$_.Exception.GetType().Name}
    if($_.Exception.Message -cmatch '^FIXTURE_[A-Z_]+$'){$receipt.failure.code=$_.Exception.Message}
    if($_.Exception -is [Management.Automation.CommandNotFoundException] -and $_.Exception.CommandName -cmatch '^[A-Za-z][A-Za-z0-9-]{0,79}$'){$receipt.failure.command_name=$_.Exception.CommandName}
} finally {
    $receipt.finished_utc=[DateTime]::UtcNow.ToString('o')
    $stream=[IO.File]::Open($Report,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{
        $writer=[IO.StreamWriter]::new($stream,[Text.UTF8Encoding]::new($false))
        try{$writer.Write(($receipt|ConvertTo-Json -Depth 8));$writer.Flush();$stream.Flush($true)}finally{$writer.Dispose()}
    }finally{$stream.Dispose()}
}
exit $exitCode
