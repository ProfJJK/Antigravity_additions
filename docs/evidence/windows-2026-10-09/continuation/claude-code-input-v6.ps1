#Requires -Version 5.1
<# Functions only. Masked, nonblocking owner input for the existing private
   Claude channel. No provider, process, task, file, credential or model action.
   Printable P/Q are code characters. Enter requests one submission; Escape
   explicitly cancels. Call Complete only after the existing packet writer
   succeeds, and Close in the caller's finally block. #>

function New-ClaudeCodeInput {
    [pscustomobject]@{
        Secret=[Security.SecureString]::new()
        SubmissionRequested=$false
        CancelRequested=$false
        Submitted=$false
        Closed=$false
    }
}

function Update-ClaudeCodeInput {
    param([Parameter(Mandatory=$true)]$State,[Parameter(Mandatory=$true)][ConsoleKeyInfo]$Key)
    if($State.Closed){throw 'Claude code input is closed.'}
    if($Key.Key -eq [ConsoleKey]::Escape){
        $State.CancelRequested=$true;$State.SubmissionRequested=$false
        if($null -ne $State.Secret){$State.Secret.Clear()}
        return
    }
    if($State.CancelRequested -or $State.Submitted -or $State.SubmissionRequested){return}
    if($Key.Key -eq [ConsoleKey]::Enter){
        if($State.Secret.Length -gt 0){$State.SubmissionRequested=$true}
        return
    }
    if($Key.Key -eq [ConsoleKey]::Backspace){
        if($State.Secret.Length -gt 0){
            $State.Secret.RemoveAt($State.Secret.Length-1)
            [Console]::Write("`b `b")
        }
        return
    }
    # Navigation/modifier and other control keys never become packet bytes.
    # Console paste supplies printable key characters; none is a hotkey.
    if([char]::IsControl($Key.KeyChar)){return}
    if($State.Secret.Length -ge 2048){throw 'Claude code input exceeded its character bound.'}
    $State.Secret.AppendChar($Key.KeyChar)
    [Console]::Write('*')
}

function Complete-ClaudeCodeInput {
    param([Parameter(Mandatory=$true)]$State)
    if($State.Closed -or $State.CancelRequested -or $State.Submitted -or -not $State.SubmissionRequested -or
       $null -eq $State.Secret -or $State.Secret.Length -eq 0){throw 'Claude code input is not ready for completion.'}
    $State.Secret.Dispose();$State.Secret=$null
    $State.Submitted=$true;$State.SubmissionRequested=$false
}

function Close-ClaudeCodeInput {
    param([Parameter(Mandatory=$true)]$State)
    if($null -ne $State.Secret){$State.Secret.Dispose();$State.Secret=$null}
    $State.SubmissionRequested=$false;$State.Closed=$true
}

function Clear-ClaudeConsoleInput {
    param([scriptblock]$KeyAvailable={[Console]::KeyAvailable},[scriptblock]$ReadKey={[Console]::ReadKey($true)})
    # Fixture callbacks are paired. Production borrows only STDIN, verifies it
    # is the current character console, then flushes its native event queue.
    # The bounded managed drain also consumes Console's cached repeated keys.
    # Never echo or return a key, or close the borrowed standard handle.
    $hasAvailable=$PSBoundParameters.ContainsKey('KeyAvailable')
    $hasRead=$PSBoundParameters.ContainsKey('ReadKey')
    if($hasAvailable -ne $hasRead){throw 'Claude console fixture callbacks must be paired.'}
    $native=$null;$inputHandle=[IntPtr]::Zero
    if(-not $hasAvailable){
        $native='CoChemClaudeConsoleInputV6' -as [type]
        if($null -eq $native){
            $null=Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CoChemClaudeConsoleInputV6 {
    [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int kind);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetFileType(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool FlushConsoleInputBuffer(IntPtr handle);
}
'@
            $native='CoChemClaudeConsoleInputV6' -as [type]
        }
        $inputHandle=$native::GetStdHandle(-10);$mode=[uint32]0
        if($inputHandle -eq [IntPtr]::Zero -or $inputHandle -eq [IntPtr](-1) -or
           $native::GetFileType($inputHandle) -ne 2 -or -not $native::GetConsoleMode($inputHandle,[ref]$mode)){
            throw 'Claude console input is not the current character console.'
        }
        if(-not $native::FlushConsoleInputBuffer($inputHandle)){throw 'Claude console input flush failed.'}
    }
    $clock=[Diagnostics.Stopwatch]::StartNew();$count=0
    $bound=$false;$reason='DRAINED'
    try{
        while($true){
            if($count -ge 4096){$bound=$true;$reason='KEY_COUNT';break}
            if($clock.ElapsedMilliseconds -ge 250){$bound=$true;$reason='TIME_LIMIT';break}
            $available=& $KeyAvailable
            if($available -isnot [bool]){throw 'Claude console availability must be a boolean.'}
            if(-not $available){break}
            $null=& $ReadKey;$count++
        }
    }finally{$clock.Stop()}
    $cleared=-not $bound;$flushed=$false
    if($null -ne $native){
        if(-not $native::FlushConsoleInputBuffer($inputHandle)){throw 'Claude console input flush failed.'}
        $flushed=$true;$cleared=-not [Console]::KeyAvailable
    }
    [pscustomobject]@{keys_discarded=$count;bound_reached=$bound;reason=$reason;native_queue_flushed=$flushed;console_queue_clear=$cleared}
}
