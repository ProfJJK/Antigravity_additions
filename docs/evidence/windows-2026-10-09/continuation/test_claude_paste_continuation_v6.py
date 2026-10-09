"""Active v6 chain: real functions/callbacks/loop, inert external boundaries.

No provider, real task, protected state, private log or child Apply is invoked.
The key loop is copied byte-for-byte from the reviewed leaf except its console
key source; synthetic log files and packet sinks live in ordinary fixtures.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
LEAF = W / 'login_pipeline_worker_interactive_r3_v6.ps1'
SERIES = W / 'login-six-workers-status-first-r3-v6.ps1'
OUTER = W / 'authenticate-native-profiles-status-first-r3-v6.ps1'
TOP = W / 'run-pipeline-commissioning-r3-v6.ps1'
BATCH = W / 'run-post-commissioning-setup-r3-v3.ps1'
LAUNCHER = W / 'run-reboot-setup-r3-v6.ps1'
INPUT = W / 'claude-code-input-v6.ps1'
CODE = 'Q-fixture-PpQq+code'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, W / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


# Keep the substantive status/attempt/pause/receipt and first-start fixtures.
# Rebind globals used by their exact function bodies to the generated v6 chain.
v5 = load('paste_v6_protocol_baseline', 'test_claude_continuation_v5.py')
v5.LEAF, v5.SERIES, v5.OUTER, v5.TOP = LEAF, SERIES, OUTER, TOP
v5.HISTORY = W / 'claude-session-history-v6.ps1'
v5.auth.SERIES, v5.auth.OUTER = SERIES, OUTER
v5.commission.series.SOURCE = TOP
for name in dir(v5):
    if name.startswith(('test_auth_', 'test_commission_')):
        globals()[name] = getattr(v5, name)
for name in ('test_actual_series_callbacks_review_history_before_readiness_and_again_before_login',
             'test_both_perpetual_directory_guards_are_replaced_before_any_leaf_task_creation',
             'test_shared_history_source_is_pinned_and_held_by_both_execution_paths',
             'test_all_transitive_wrapper_pins_select_v5_and_preserve_status_and_first_start_protocol',
             'test_frozen_sources_are_preserved_exactly',
             'test_protected_host_configuration_and_capacity_are_unchanged'):
    globals()[name] = getattr(v5, name)

launcher = load('paste_v6_launcher_protocol_baseline', 'test_reboot_setup_launcher_v5.py')
launcher.SOURCE = LAUNCHER
launcher.CHILDREN = (('commissioning', TOP.name, digest(TOP)),
                     ('monitoring_setup', BATCH.name, digest(BATCH)))
for name in dir(launcher):
    if name.startswith('test_') and 'actual_default' not in name and 'preview' not in name:
        globals()['test_launcher_' + name[5:]] = getattr(launcher, name)

bridge = load('paste_v6_private_bridge_baseline', 'test_claude_login_bridge_r3.py')
bridge.WRAPPER = LEAF
bridge.ps = lambda body: v5.auth.ps(v5.auth.functions(LEAF) + body)
for name in ('test_private_acl_has_only_system_admin_full_control',
             'test_feed_is_one_line_flushed_then_closed',
             'test_native_echo_after_submission_never_reaches_log',
             'test_preinput_browser_instructions_can_be_published',
             'test_cancel_never_feeds_provider'):
    globals()['test_bridge_' + name[5:]] = getattr(bridge, name)


@pytest.mark.parametrize('code', [CODE, 'P-first_Q-middle-qP', 'Q-unicode-測試'])
def test_actual_v6_packet_bytes_preserve_p_q_and_utf8_and_refuse_second_write(tmp_path, code):
    bridge.test_actual_ps51_private_packet_create_new_and_repeat_refusal(tmp_path, code)


def loop_fixture(tmp_path, mode, split=0):
    source = LEAF.read_text()
    begin = source.index('    $instance=$task.Run($null);')
    end = source.index('    Assert-TaskTerminal $task\n', begin)
    actual = source[begin:end]
    assert actual.count('[Console]::KeyAvailable') == 1
    assert actual.count('[Console]::ReadKey($true)') == 1
    # Only the native Console input source is inert. Input mutation, loop
    # ordering, log reads, readiness detection and packet call remain actual.
    actual = actual.replace('[Console]::KeyAvailable', '(Test-FixtureKeyAvailable)')
    actual = actual.replace('[Console]::ReadKey($true)', '(Read-FixtureKey)')
    prefix = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
'''
    prefix += v5.auth.functions(INPUT)
    prefix += '$root=' + q(tmp_path) + ';$Slot="slot5";$nonce="' + 'a'*32 + '";'
    prefix += '$mode=' + q(mode) + ';$split=' + str(split) + ';$expected=' + q(CODE) + ';'
    prefix += r'''
$script:tick=0;$script:done=$false;$script:events=[Collections.Generic.List[string]]::new()
$script:queue=[Collections.Generic.Queue[ConsoleKeyInfo]]::new()
$script:packets=0;$script:packetAttempts=0;$script:exact=$false;$script:packetTick=-1;$script:cancels=0
$script:hosts=[Collections.Generic.List[string]]::new();$script:lastSecret=$null
$instance=[pscustomobject]@{fixture=$true};$task=[pscustomobject]@{fixture=$true}
$task|Add-Member ScriptMethod Run {param($nothing)$script:events.Add('TASK_RUN');return [pscustomobject]@{fixture=$true}}
$fixturePrompt='Paste code here if prompted >';$fixtureIntro="Browser fixture link.`n"
function Add-FixtureText([string]$Value){foreach($c in $Value.ToCharArray()){$script:queue.Enqueue([ConsoleKeyInfo]::new($c,[ConsoleKey]::NoName,$false,$false,$false))}}
function Add-FixtureKey([ConsoleKey]$Key){$script:queue.Enqueue([ConsoleKeyInfo]::new([char]0,$Key,$false,$false,$false))}
function Test-FixtureKeyAvailable{return $script:queue.Count -gt 0}
function Read-FixtureKey{return $script:queue.Dequeue()}
function Write-Host{param($Object)$script:hosts.Add([string]$Object)}
function Test-InstanceComplete{
    param($Instance)
    if(-not $Instance.fixture){throw 'Inert instance binding lost'}
    if($mode -ceq 'terminal_before_write' -and $null -ne (Get-Variable inputState -ErrorAction SilentlyContinue) -and $inputState.SubmissionRequested){return $true}
    return $script:done
}
function Write-CancelRequest{
    param($CancelRoot,$CancelNonce)
    if($CancelRoot -cne $root -or $CancelNonce -cne $nonce){throw 'Inert cancellation binding lost'}
    $script:cancels++;$script:events.Add('CANCEL');$script:done=$true
}
function Write-PrivatePacket{
    param($PacketPath,$PacketNonce,$Secret)
    if($PacketPath -cne (Join-Path $root 'input.once') -or $PacketNonce -cne $nonce){throw 'Inert packet binding lost'}
    if(-not $nativePromptReady -or $script:done){throw 'Input reached sink without a live ready prompt'}
    $script:packetAttempts++;$script:events.Add('PACKET');$script:lastSecret=$Secret
    if($mode -ceq 'packet_failure'){throw 'Inert packet failure'}
    $ptr=[IntPtr]::Zero
    try{$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret);$script:exact=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) -ceq $expected}
    finally{if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}}
    $script:packets++;$script:packetTick=$script:tick
}
$script:actualClear=(Get-Command Clear-ClaudeConsoleInput).ScriptBlock
function Clear-ClaudeConsoleInput{
    $script:events.Add('DRAIN')
    if($mode -ceq 'uncleared_queue'){return [pscustomobject]@{console_queue_clear=$false;bound_reached=$true}}
    if($mode -ceq 'invalid_clear_metadata'){return [pscustomobject]@{console_queue_clear='true';bound_reached=$false}}
    & $script:actualClear -KeyAvailable {Test-FixtureKeyAvailable} -ReadKey {Read-FixtureKey}
}
function Start-Sleep{
    param($Milliseconds)
    if($Milliseconds -ne 100){throw 'Polling cadence changed'}
    $script:tick++
    if($script:tick -gt 4){throw 'Inert loop failed to terminate'}
    if($script:tick -eq 1){
        if($mode -ceq 'preprompt'){
            [IO.File]::WriteAllText((Join-Path $root 'operator.log'),$fixtureIntro+$fixturePrompt,[Text.UTF8Encoding]::new($false))
        }elseif($mode -ceq 'splitprompt'){
            [IO.File]::WriteAllText((Join-Path $root 'operator.log'),$fixtureIntro+$fixturePrompt,[Text.UTF8Encoding]::new($false))
            Add-FixtureText $expected;Add-FixtureKey Enter
        }elseif($mode -ceq 'after_submit_escape'){Add-FixtureKey Escape
        }elseif($mode -ceq 'after_submit_keys'){Add-FixtureText 'Q-extra-P';Add-FixtureKey Enter
        }else{$script:done=$true}
    }else{$script:done=$true}
}
$initial=$fixtureIntro+$fixturePrompt
if($mode -ceq 'preprompt' -or $mode -ceq 'no_prompt_terminal'){$initial=$fixtureIntro}
if($mode -ceq 'splitprompt'){$initial=$fixtureIntro+$fixturePrompt.Substring(0,$split)}
[IO.File]::WriteAllText((Join-Path $root 'operator.log'),$initial,[Text.UTF8Encoding]::new($false))
if($mode -cne 'splitprompt'){
    if($mode -ceq 'cancel'){Add-FixtureText 'QP';Add-FixtureKey Escape;Add-FixtureText 'P-leftover';Add-FixtureKey Enter}
    elseif($mode -ceq 'empty_enter'){Add-FixtureKey Enter;Add-FixtureText $expected;Add-FixtureKey Enter}
    else{Add-FixtureText $expected;Add-FixtureKey Enter;Add-FixtureText 'Q-leftover';Add-FixtureKey Enter}
}
$original=[Console]::Out;$mask=[IO.StringWriter]::new();[Console]::SetOut($mask)
$failed=$false;$message=''
try{
    try{
'''
    suffix = r'''
    }catch{$failed=$true;$message=$_.Exception.Message}
    $disposed=$true
    if($null -ne $script:lastSecret){
        $disposed=$false;try{$script:lastSecret.AppendChar([char]'X')}catch{$disposed=$_.Exception.GetBaseException() -is [ObjectDisposedException]}
    }
    $result=@{failed=$failed;message=$message;packets=$script:packets;packet_attempts=$script:packetAttempts;exact=$script:exact;packet_tick=$script:packetTick;cancels=$script:cancels;events=@($script:events.ToArray());hosts=@($script:hosts.ToArray());output=$mask.ToString();closed=$inputState.Closed;secret_null=($null -eq $inputState.Secret);disposed=$disposed;queued=$script:queue.Count}
}finally{[Console]::SetOut($original);$mask.Dispose()}
$result|ConvertTo-Json -Compress -Depth 8
'''
    encoded = base64.b64encode((prefix + actual + suffix).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode', ['ready', 'preprompt', 'empty_enter', 'after_submit_keys', 'after_submit_escape'])
def test_actual_leaf_loop_accepts_p_q_once_without_echo_and_keeps_polling(tmp_path, mode):
    result = loop_fixture(tmp_path, mode)
    assert not result['failed'], result['message']
    assert result['packets'] == result['packet_attempts'] == 1 and result['exact']
    assert result['packet_tick'] == (1 if mode == 'preprompt' else 0)
    assert result['output'].count('*') == len(CODE)
    assert CODE not in result['output'] and 'Q-leftover' not in result['output'] and 'Q-extra' not in result['output']
    assert result['cancels'] == (1 if mode == 'after_submit_escape' else 0)
    assert result['closed'] and result['secret_null'] and result['disposed'] and result['queued'] == 0
    assert result['events'].count('PACKET') == 1
    assert result['output'].count('Code (masked; Enter submits, Escape cancels): ') == 1
    assert 'Paste code here if prompted' not in result['output']


@pytest.mark.parametrize('split', [1, 5, 15, 27])
def test_actual_leaf_loop_replaces_chunked_native_prompt_without_losing_intro(tmp_path, split):
    result = loop_fixture(tmp_path, 'splitprompt', split)
    assert not result['failed'] and result['packets'] == 1 and result['exact']
    assert result['packet_tick'] == 1 and result['output'].count('Browser fixture link.\n') == 1
    assert 'Paste code' not in result['output'] and CODE not in result['output']
    assert result['output'].count('Code (masked; Enter submits, Escape cancels): ') == 1
    assert result['output'].count('*') == len(CODE) and result['queued'] == 0


@pytest.mark.parametrize('mode,failed,cancels', [('cancel', False, 1), ('terminal_before_write', True, 1),
                                             ('no_prompt_terminal', False, 0), ('packet_failure', True, 1)])
def test_actual_leaf_loop_cancel_terminal_and_missing_prompt_never_submit(tmp_path, mode, failed, cancels):
    result = loop_fixture(tmp_path, mode)
    assert result['failed'] == failed and result['packets'] == 0 and result['cancels'] == cancels
    assert result['packet_attempts'] == (1 if mode == 'packet_failure' else 0)
    assert result['closed'] and result['secret_null'] and result['disposed'] and result['queued'] == 0
    assert CODE not in result['output'] and 'P-leftover' not in result['output']


@pytest.mark.parametrize('mode', ['uncleared_queue', 'invalid_clear_metadata'])
def test_actual_leaf_loop_requires_explicit_boolean_queue_clearing_proof(tmp_path, mode):
    result = loop_fixture(tmp_path, mode)
    assert result['failed'] and result['packets'] == 1 and result['cancels'] == 1
    assert result['message'] == 'Console input exceeded its clearing bound; preserve the session.'
    assert result['closed'] and result['secret_null'] and result['disposed']
    assert CODE not in result['output']


@pytest.mark.parametrize('before,after', [
    ('login_pipeline_worker_interactive_r3_v5.ps1', LEAF.name),
    ('login-six-workers-status-first-r3-v5.ps1', SERIES.name),
    ('authenticate-native-profiles-status-first-r3-v5.ps1', OUTER.name),
    ('run-pipeline-commissioning-r3-v5.ps1', TOP.name),
    ('run-post-commissioning-setup-r3-v2.ps1', BATCH.name),
    ('run-reboot-setup-r3-v5.ps1', LAUNCHER.name),
])
def test_v5_functions_are_exactly_preserved_in_v6_successors(before, after):
    old, new = v5.functions_by_name(W / before), v5.functions_by_name(W / after)
    assert old.keys() == new.keys()
    if before == 'run-post-commissioning-setup-r3-v2.ps1':
        # This one specification selects the additive top successor. All
        # controller witnesses, receipt paths and phase ordering stay exact.
        name = 'Get-SetupPhaseSpecifications'
        old[name] = old[name].replace('run-pipeline-commissioning-r3-v5.ps1', TOP.name)
        old[name] = old[name].replace('46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0', digest(TOP))
    assert old == new


def test_input_helper_is_pinned_and_held_before_any_task_or_native_execution():
    text = LEAF.read_text()
    pin = digest(INPUT)
    assert text.count(pin) == 2
    assert INPUT.name in text
    assert text.index('Import-PinnedFunctions $codeInputPath') < text.index('$folder.RegisterTaskDefinition(')
    assert "$held.Add((Open-VerifiedFile $codeInputPath '" + pin + "'" in text
    assert '[Console]::ReadKey($true).Key' not in text and 'Read-Host' not in text
    assert 'Write-PrivatePacket $inputPath $nonce $inputState.Secret' in text
    assert text.index('if(Test-InstanceComplete $instance)') < text.index('Write-PrivatePacket $inputPath')
    assert text.index('Write-PrivatePacket $inputPath') < text.index('Complete-ClaudeCodeInput -State $inputState')
    assert 'Close-ClaudeCodeInput -State $inputState' in text
    assert text.count('console_queue_clear -isnot [bool]') == 3


def test_updated_top_batch_and_launcher_close_transitive_pins():
    for source, dependencies in ((SERIES, [LEAF]), (OUTER, [LEAF, SERIES]),
                                 (TOP, [LEAF, SERIES, OUTER]), (BATCH, [TOP]),
                                 (LAUNCHER, [TOP, BATCH])):
        text = source.read_text()
        for dependency in dependencies:
            assert dependency.name in text and digest(dependency) in text
    assert 'PostCommissioningSetup4.2.7-windows-20261008-r3-v3' in BATCH.read_text()
    assert digest(W / 'install-resource-observer-r3-v4.ps1') in BATCH.read_text()
    assert digest(W / 'resource-observer-r3-v4/source-manifest.json') in BATCH.read_text()


@pytest.mark.parametrize('name,pin', [
    ('login_pipeline_worker_interactive_r3_v5.ps1', '8e3545109c2bbef9a53763f8af8575ac1ff7376d3bea6c27a5ecd8454af182ec'),
    ('login-six-workers-status-first-r3-v5.ps1', '6e18cf7d8a816dc36464593fdb9eaed51c7bd45963accb16481befa67df10d58'),
    ('authenticate-native-profiles-status-first-r3-v5.ps1', '89e6efcad31ff05f94aea905abb15db73b5c85e69eb9ac3508b2b33d03c91d04'),
    ('run-pipeline-commissioning-r3-v5.ps1', '46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0'),
    ('run-reboot-setup-r3-v5.ps1', '99c3a415bf3f2b1acb1a7103aae5bec440fb9b2c6549f7590a1626e3c5b693cc'),
    ('run-post-commissioning-setup-r3-v2.ps1', 'dd2b8119f8f1c7a206c8ef12e6c2ef45634695fd83fe36bc8b361154044125f2'),
    ('worker_claude_login_bridge_r3.py', 'b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'),
    ('commission-first-warden-r3-v5.py', '8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e'),
])
def test_frozen_execution_and_historical_evidence_sources_remain_exact(name, pin):
    assert digest(W / name) == pin


def test_unique_v6_metadata_only_previews_do_not_create_task_or_attempt_roots():
    previews = []
    for source, filename in ((TOP, 'claude-paste-continuation-v6-preview-final.json'),
                             (LAUNCHER, 'reboot-setup-launcher-v6-preview-final.json')):
        result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-Command', '& ' + q(source)], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        value = json.loads(result.stdout)
        if source == TOP:
            assert value['mode'] == 'READ_ONLY_PLAN'
            assert value['tasks_registered'] == value['model_jobs_submitted'] == 0
            assert value['identity_count'] == 6 and value['shared_capacity'] == 4
        else:
            assert value['mode'] == 'READ_ONLY_PLAN' and value['child_processes_started'] == 0
            assert value['tasks_changed'] == value['model_jobs_submitted'] == 0
        previews.append((filename, value))
    for filename, value in previews:
        with (W / filename).open('x', encoding='utf-8') as output:
            json.dump(value, output, indent=2);output.write('\n')
