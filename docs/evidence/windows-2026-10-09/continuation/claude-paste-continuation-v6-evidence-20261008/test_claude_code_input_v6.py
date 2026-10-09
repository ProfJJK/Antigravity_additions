"""Actual PS5.1 masked input functions with synthetic keys and ordinary IO.

No provider, account, real console interaction, task, private file or Apply.
SecureString values are compared inside the fixture; outputs contain booleans,
counts and masks only. Production native-console flush is inspected and its
redirected-input refusal is exercised, never a user's console input queue.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
HELPER = W / 'claude-code-input-v6.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
EXPORTS = ['New-ClaudeCodeInput', 'Update-ClaudeCodeInput', 'Complete-ClaudeCodeInput',
           'Close-ClaudeCodeInput', 'Clear-ClaudeConsoleInput']


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body, *, redirected=False):
    prefix = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
'''
    prefix += '$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile(' + q(HELPER) + ',[ref]$tokens,[ref]$errors);if($errors.Count){throw $errors[0]};'
    prefix += r'''
foreach($definition in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Send-Text($State,[string]$Value){
    foreach($c in $Value.ToCharArray()){
        Update-ClaudeCodeInput -State $State -Key ([ConsoleKeyInfo]::new($c,[ConsoleKey]::NoName,$false,$false,$false))
    }
}
function Send-Key($State,[ConsoleKey]$Key,[char]$Character=[char]0){
    Update-ClaudeCodeInput -State $State -Key ([ConsoleKeyInfo]::new($Character,$Key,$false,$false,$false))
}
function Test-Secret($Secret,[string]$Expected){
    $ptr=[IntPtr]::Zero
    try{$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret);return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) -ceq $Expected}
    finally{if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}}
}
function Test-Disposed($Secret){
    # .NET Framework Length returns zero after Dispose; a mutating method
    # performs the actual disposed check and must refuse the synthetic value.
    try{$Secret.AppendChar([char]'X');return $false}catch{return $_.Exception.GetBaseException() -is [ObjectDisposedException]}
}
$original=[Console]::Out;$mask=[IO.StringWriter]::new();[Console]::SetOut($mask)
try{
'''
    suffix = r'''
    $result.output=$mask.ToString()
}finally{[Console]::SetOut($original);$mask.Dispose()}
$result|ConvertTo-Json -Compress -Depth 8
'''
    encoded = base64.b64encode((prefix + body + suffix).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            input='' if redirected else None, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout.strip())


def test_exact_five_export_surface_and_functions_only():
    result = run(r'''$names=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|ForEach-Object{$_.Name})
    $top=@($ast.EndBlock.Statements|Where-Object{$_ -isnot [Management.Automation.Language.FunctionDefinitionAst]})
    $result=@{names=$names;outside_functions=$top.Count}''')
    assert result == {'names': EXPORTS, 'outside_functions': 0, 'output': ''}


def test_new_state_is_empty_secure_and_neither_requested_nor_closed():
    result = run(r'''$s=New-ClaudeCodeInput
    $result=@{secure=($s.Secret -is [Security.SecureString]);length=$s.Secret.Length;submission=$s.SubmissionRequested;cancel=$s.CancelRequested;submitted=$s.Submitted;closed=$s.Closed}
    Close-ClaudeCodeInput $s''')
    assert result == dict(secure=True, length=0, submission=False, cancel=False,
                          submitted=False, closed=False, output='')


@pytest.mark.parametrize('code', ['Q-start', 'P-start', 'abcQdefPghi', 'pQqP', 'PQ',
                                'fixture-code_9+/#=:.', ' spaced fixture ', 'synthetic-測試'])
def test_paste_preserves_every_printable_character_including_p_q(code):
    result = run('$s=New-ClaudeCodeInput;Send-Text $s ' + q(code) + ';'
                 'Send-Key $s Enter;'
                 '$result=@{exact=(Test-Secret $s.Secret ' + q(code) + ');length=$s.Secret.Length;submission=$s.SubmissionRequested;cancel=$s.CancelRequested;submitted=$s.Submitted};Close-ClaudeCodeInput $s')
    assert result == dict(exact=True, length=len(code), submission=True,
                          cancel=False, submitted=False, output='*' * len(code))
    assert code not in result['output']


def test_empty_enter_does_not_submit_or_lock_later_input():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Key $s Enter;$empty=$s.SubmissionRequested
    Send-Text $s 'Q-later';Send-Key $s Enter
    $result=@{empty=$empty;later=$s.SubmissionRequested;exact=(Test-Secret $s.Secret 'Q-later')};Close-ClaudeCodeInput $s''')
    assert result == dict(empty=False, later=True, exact=True, output='*******')


def test_backspace_edits_exactly_and_empty_backspace_is_silent():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Key $s Backspace;Send-Text $s 'Qab';Send-Key $s Backspace
    Send-Text $s 'P';Send-Key $s Enter
    $result=@{exact=(Test-Secret $s.Secret 'QaP');length=$s.Secret.Length;submission=$s.SubmissionRequested};Close-ClaudeCodeInput $s''')
    assert result == dict(exact=True, length=3, submission=True, output='***\b \b*')


@pytest.mark.parametrize('before_enter', [False, True])
def test_escape_explicitly_cancels_and_clears_pending_secret(before_enter):
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s 'synthetic-QP';''' +
                 ('Send-Key $s Enter;' if before_enter else '') + r'''
    Send-Key $s Escape;Send-Text $s 'ignored';Send-Key $s Enter
    $result=@{cancel=$s.CancelRequested;submission=$s.SubmissionRequested;length=$s.Secret.Length;submitted=$s.Submitted};Close-ClaudeCodeInput $s''')
    assert result == dict(cancel=True, submission=False, length=0,
                          submitted=False, output='*' * 12)


def test_enter_locks_single_line_against_queued_paste_and_repeat_enter():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s 'QP-first';Send-Key $s Enter
    Send-Text $s 'Q-more';Send-Key $s Backspace;Send-Key $s Enter
    $result=@{exact=(Test-Secret $s.Secret 'QP-first');submission=$s.SubmissionRequested;length=$s.Secret.Length};Close-ClaudeCodeInput $s''')
    assert result == dict(exact=True, submission=True, length=8, output='*' * 8)


def test_complete_disposes_secret_then_queued_keys_are_ignored():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s 'QP-submit';Send-Key $s Enter;$prior=$s.Secret
    $none=@(Complete-ClaudeCodeInput $s);Send-Text $s 'ignoredQP';Send-Key $s Enter;Send-Key $s Backspace
    $result=@{disposed=(Test-Disposed $prior);secret_null=($null -eq $s.Secret);submitted=$s.Submitted;submission=$s.SubmissionRequested;cancel=$s.CancelRequested;outputs=$none.Count};Close-ClaudeCodeInput $s''')
    assert result == dict(disposed=True, secret_null=True, submitted=True,
                          submission=False, cancel=False, outputs=0, output='*' * 9)


def test_escape_can_cancel_native_wait_after_submission_without_secret_revival():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s 'QP';Send-Key $s Enter;Complete-ClaudeCodeInput $s;Send-Key $s Escape
    $result=@{cancel=$s.CancelRequested;submitted=$s.Submitted;secret_null=($null -eq $s.Secret);submission=$s.SubmissionRequested};Close-ClaudeCodeInput $s''')
    assert result == dict(cancel=True, submitted=True, secret_null=True,
                          submission=False, output='**')


@pytest.mark.parametrize('setup', ['', 'Send-Text $s "QP";', 'Send-Text $s "QP";Send-Key $s Escape;',
                                  'Send-Text $s "QP";Send-Key $s Enter;Complete-ClaudeCodeInput $s;',
                                  'Close-ClaudeCodeInput $s;'])
def test_complete_refuses_unrequested_cancelled_repeated_or_closed_state(setup):
    result = run('$s=New-ClaudeCodeInput;' + setup + r'''
    $refused=$false;try{Complete-ClaudeCodeInput $s}catch{$refused=$true;$message=$_.Exception.Message}
    $result=@{refused=$refused;generic=($message -ceq 'Claude code input is not ready for completion.')};Close-ClaudeCodeInput $s''')
    assert result['refused'] and result['generic']
    assert set(result['output']) <= {'*'}


def test_close_disposes_in_finally_is_idempotent_and_update_refuses_closed_state():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s 'Q-secret';$prior=$s.Secret
    try{throw 'fixture failure'}catch{}finally{Close-ClaudeCodeInput $s}
    Close-ClaudeCodeInput $s;$refused=$false;try{Send-Text $s 'P'}catch{$refused=$true}
    $result=@{disposed=(Test-Disposed $prior);secret_null=($null -eq $s.Secret);closed=$s.Closed;submission=$s.SubmissionRequested;refused=$refused}''')
    assert result == dict(disposed=True, secret_null=True, closed=True,
                          submission=False, refused=True, output='*' * 8)


def test_2048_character_limit_preserves_prior_input_and_rejects_without_echo():
    result = run(r'''$s=New-ClaudeCodeInput;Send-Text $s ('Q'*2048);$refused=$false
    try{Send-Text $s 'P'}catch{$refused=$true;$message=$_.Exception.Message}
    Send-Key $s Enter
    $result=@{refused=$refused;generic=($message -ceq 'Claude code input exceeded its character bound.');length=$s.Secret.Length;exact=(Test-Secret $s.Secret ('Q'*2048));submission=$s.SubmissionRequested};Close-ClaudeCodeInput $s''')
    assert result == dict(refused=True, generic=True, length=2048, exact=True,
                          submission=True, output='*' * 2048)


@pytest.mark.parametrize('key,character', [('Tab', 9), ('Delete', 0), ('LeftArrow', 0),
                                        ('RightArrow', 0), ('Home', 0), ('F1', 0),
                                        ('A', 1), ('V', 22), ('J', 10), ('M', 13)])
def test_control_and_navigation_keys_never_enter_secret(key, character):
    result = run('$s=New-ClaudeCodeInput;Send-Text $s "QP";Send-Key $s ' + key + ' ([char]' + str(character) + ');'
                 '$result=@{exact=(Test-Secret $s.Secret "QP");length=$s.Secret.Length;cancel=$s.CancelRequested;submission=$s.SubmissionRequested};Close-ClaudeCodeInput $s')
    assert result == dict(exact=True, length=2, cancel=False, submission=False, output='**')


def test_update_never_returns_key_or_secure_string():
    result = run(r'''$s=New-ClaudeCodeInput
    $items=@(Send-Text $s 'QP';Send-Key $s Backspace;Send-Key $s Enter;Send-Key $s Escape;Close-ClaudeCodeInput $s)
    $result=@{items=$items.Count}''')
    assert result == dict(items=0, output='**\b \b')


def test_console_drain_does_not_echo_or_return_fixture_keys():
    result = run(r'''$script:keys=0
    $r=Clear-ClaudeConsoleInput -KeyAvailable {$script:keys -lt 6} -ReadKey {$script:keys++;[ConsoleKeyInfo]::new('Q',[ConsoleKey]::Q,$false,$false,$false)}
    $result=@{drain=$r;calls=$script:keys}''')
    assert result == dict(drain=dict(keys_discarded=6, bound_reached=False, reason='DRAINED',
                                    native_queue_flushed=False, console_queue_clear=True), calls=6, output='')


def test_console_drain_never_waits_when_no_key_is_available():
    result = run(r'''$script:reads=0;$r=Clear-ClaudeConsoleInput -KeyAvailable {$false} -ReadKey {$script:reads++;throw 'unexpected read'}
    $result=@{drain=$r;reads=$script:reads}''')
    assert result['reads'] == 0 and result['drain']['keys_discarded'] == 0
    assert result['drain']['console_queue_clear'] is True and result['output'] == ''


def test_console_drain_bound_never_claims_fixture_queue_clear():
    result = run(r'''$script:reads=0;$r=Clear-ClaudeConsoleInput -KeyAvailable {$true} -ReadKey {$script:reads++;[char]'Q'}
    $result=@{drain=$r;reads=$script:reads}''')
    assert result['drain']['bound_reached'] is True
    assert result['drain']['console_queue_clear'] is False
    assert result['drain']['native_queue_flushed'] is False
    assert result['drain']['reason'] in {'KEY_COUNT', 'TIME_LIMIT'}
    assert 0 < result['reads'] == result['drain']['keys_discarded'] <= 4096
    assert result['output'] == ''


@pytest.mark.parametrize('value', ['$null', "'true'", '1', '@($true,$true)'])
def test_console_availability_requires_single_boolean(value):
    result = run('$refused=$false;try{$null=Clear-ClaudeConsoleInput -KeyAvailable {' + value + r'''} -ReadKey {throw 'unexpected read'}}catch{$refused=$true;$message=$_.Exception.Message}
    $result=@{refused=$refused;generic=($message -ceq 'Claude console availability must be a boolean.')}''')
    assert result == dict(refused=True, generic=True, output='')


@pytest.mark.parametrize('callback', ['-KeyAvailable {$true}', '-ReadKey {[char]"Q"}'])
def test_console_fixture_callbacks_must_be_paired(callback):
    result = run('$refused=$false;try{$null=Clear-ClaudeConsoleInput ' + callback + r'''}catch{$refused=$true;$message=$_.Exception.Message}
    $result=@{refused=$refused;generic=($message -ceq 'Claude console fixture callbacks must be paired.')}''')
    assert result == dict(refused=True, generic=True, output='')


def test_production_flush_refuses_redirected_stdin_before_console_operations():
    result = run(r'''$refused=$false;try{$null=Clear-ClaudeConsoleInput}catch{$refused=$true;$message=$_.Exception.Message}
    $result=@{refused=$refused;generic=($message -ceq 'Claude console input is not the current character console.')}''', redirected=True)
    assert result == dict(refused=True, generic=True, output='')


def test_production_console_scope_bound_and_no_borrowed_handle_close():
    source = HELPER.read_text()
    assert 'GetStdHandle(-10)' in source and 'GetFileType($inputHandle) -ne 2' in source
    assert 'GetConsoleMode($inputHandle,[ref]$mode)' in source
    assert source.count('$native::FlushConsoleInputBuffer($inputHandle)') == 2
    assert '$count -ge 4096' in source and '$clock.ElapsedMilliseconds -ge 250' in source
    assert '$cleared=-not [Console]::KeyAvailable' in source
    assert '[Console]::ReadKey($true)' in source
    assert 'CloseHandle' not in source and 'Read-Host' not in source
    assert 'Start-Process' not in source and 'Register-ScheduledTask' not in source
    assert 'PtrToString' not in source and 'Write-Output' not in source
    assert hashlib.sha256(HELPER.read_bytes()).hexdigest()
