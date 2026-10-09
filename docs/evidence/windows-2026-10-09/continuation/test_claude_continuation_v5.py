"""Reviewed Claude-history continuation; ordinary Windows fixtures only.

No provider command, real scheduled task, private login log or Apply invocation
is executed. Callback fixtures use the actual v5 series callbacks with inert
status/history/native boundaries; existing protocol tests retain their bodies.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
LEAF = W / 'login_pipeline_worker_interactive_r3_v5.ps1'
SERIES = W / 'login-six-workers-status-first-r3-v5.ps1'
OUTER = W / 'authenticate-native-profiles-status-first-r3-v5.ps1'
TOP = W / 'run-pipeline-commissioning-r3-v5.ps1'
HISTORY = W / 'claude-session-history-v5.ps1'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, W / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse substantive typed receipt, stage/attempt, status-first, cancellation and
# exclusive-journal fixtures without executing any baseline's old preview test.
auth = load('v5_auth_protocol_baseline', 'test_native_auth_checkpoint_r3_v3.py')
auth.LEAF = W / 'check-worker-native-auth-status-six-r3-v4.ps1'
auth.SERIES = SERIES
auth.OUTER = OUTER
for name in dir(auth):
    if name.startswith('test_'):
        globals()['test_auth_' + name[5:]] = getattr(auth, name)

commission = load('v5_commission_protocol_baseline', 'test_login_continuation_commissioning_r3_v3.py')
commission.first.M = load('v5_first_start_protocol', 'commission-first-warden-r3-v5.py')
commission.validate_auth = commission.first.M.validate_auth
commission.first.M.validate_auth = lambda reader: commission.validate_auth(reader, commission.ATTEMPT)
commission.first.M.AUTH_ROOT = commission.first.M.ROOT.parent / (commission.first.M.AUTH_PREFIX + commission.ATTEMPT)
commission.first.WRAPPER = W / 'commission-first-warden-r3-v5.ps1'
commission.series.SOURCE = TOP
for name in dir(commission):
    if name.startswith('test_') and name != 'test_actual_preview_is_read_only_with_absent_first_start':
        globals()['test_commission_' + name[5:]] = getattr(commission, name)


def digest(filename):
    return hashlib.sha256((W / filename).read_bytes()).hexdigest()


def functions_by_name(path):
    code = f"$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e);if($e.Count){{throw $e[0]}};"
    code += r"$out=[ordered]@{};foreach($n in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($out.Contains($n.Name)){throw 'Duplicate function'};$out[$n.Name]=$n.Extent.Text};$out|ConvertTo-Json -Compress"
    return auth.success(auth.ps(code))


@pytest.mark.parametrize('provider,mode', [
    ('claude', 'reuse'), ('claude', 'history_refused'),
    ('claude', 'ready'), ('claude', 'pause'),
    ('claude', 'history_changed_before_login'), ('codex', 'ready'),
])
def test_actual_series_callbacks_review_history_before_readiness_and_again_before_login(provider, mode):
    """Exercise real v5 callback ASTs, not a rewritten model of their order."""
    code = auth.functions(SERIES)
    code += f"$Provider='{provider}';$mode='{mode}';$nativeRoot='inert-native';$installRoot='inert-install';$codexHelper='inert-codex.ps1';$claudeHelper='inert-claude.ps1';"
    code += f"$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{SERIES}',[ref]$tokens,[ref]$errors);if($errors.Count){{throw $errors[0]}};"
    code += r'''
    $calls=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Invoke-SixStatusFirst'},$true))
    if($calls.Count -ne 1){throw 'Expected one actual series invocation'}
    $callbacks=@($calls[0].CommandElements|Where-Object{$_ -is [Management.Automation.Language.ScriptBlockExpressionAst]})
    if($callbacks.Count -ne 4){throw 'Expected four actual series callbacks'}
    $actualLogin=$callbacks[1].ScriptBlock.GetScriptBlock()
    $actualReady=$callbacks[3].ScriptBlock.GetScriptBlock()
    $script:events=[Collections.Generic.List[string]]::new();$script:historyCount=0;$script:loginCount=0
    $folder=[pscustomobject]@{fixture='exact-folder'};$runtime=[pscustomobject]@{fixture='exact-runtime'}
    function Write-Host{param($Object)}
    function Assert-SeriesDaemonsStopped{param($Folder)}
    function Assert-ReviewedClaudeHistory{
      param([string]$Slot,$Folder,$Runtime)
      if($Slot -cne 'slot5' -or $Folder.fixture -cne 'exact-folder' -or $Runtime.fixture -cne 'exact-runtime'){throw 'History review lost its runtime/slot binding'}
      $script:historyCount++;$script:events.Add("HISTORY:$Slot")
      if($mode -ceq 'history_refused' -or ($mode -ceq 'history_changed_before_login' -and $script:historyCount -eq 2)){throw 'inert incomplete history'}
      [pscustomobject]@{fixture='reviewed-history'}
    }
    function Read-Host{
      param($Prompt);$script:events.Add('READY:slot5')
      if($mode -ceq 'pause'){'PAUSE'}else{'READY'}
    }
    function Invoke-SeriesConsoleChild{
      param([string[]]$Arguments)
      $index=[Array]::IndexOf($Arguments,'-Slot');if($index -lt 0 -or $Arguments[$index+1] -cne 'slot5'){throw 'Inert login received the wrong slot'}
      if($Provider -ceq 'claude' -and ($Arguments[-2] -cne '-Apply' -or $Arguments[-1] -cne '-Interactive')){throw 'Attended Claude arguments changed'}
      $script:events.Add('LOGIN:slot5');return 0
    }
    $failed=$false;$complete=$false
    try{
      $complete=Invoke-SixStatusFirst {
        param($slot,$stage);$script:events.Add("STATUS:$slot/$stage")
        [pscustomobject]@{decision=if($slot -ceq 'slot5' -and $stage -ceq 'before' -and $mode -cne 'reuse'){'ATTENDED_LOGIN_REQUIRED'}else{'REUSE_VERIFIED_SESSION'}}
      } $actualLogin {
        param($slot,$state,$proof);$script:events.Add("RECORD:$slot/$state")
      } $actualReady
    }catch{$failed=$true}
    [ordered]@{failed=$failed;complete=$complete;history_count=$script:historyCount;login_count=$script:loginCount;events=@($script:events.ToArray())}|ConvertTo-Json -Compress
    '''
    result = auth.success(auth.ps(code))
    events = result['events']
    assert result['failed'] == (mode in ('history_refused', 'history_changed_before_login'))
    assert result['complete'] == (mode in ('reuse', 'ready'))
    if mode == 'reuse':
        assert result['history_count'] == result['login_count'] == 0
        assert not any(e.startswith(('HISTORY:', 'READY:', 'LOGIN:')) for e in events)
        assert 'RECORD:slot6/REUSED_VERIFIED_SESSION' in events
    elif mode == 'history_refused':
        assert result['history_count'] == 1 and result['login_count'] == 0
        assert not any(e.startswith(('READY:', 'LOGIN:', 'STATUS:slot6')) for e in events)
        assert 'RECORD:slot5/LOGIN_STARTED' not in events
    elif mode == 'pause':
        assert result['history_count'] == 1 and result['login_count'] == 0
        assert events.index('HISTORY:slot5') < events.index('READY:slot5')
        assert 'RECORD:slot5/PAUSED_BEFORE_LOGIN' in events
        assert not any(e.startswith(('LOGIN:', 'STATUS:slot6')) for e in events)
        assert 'RECORD:slot5/LOGIN_STARTED' not in events
    elif mode == 'history_changed_before_login':
        assert result['history_count'] == 2 and result['login_count'] == 0
        assert events.index('HISTORY:slot5') < events.index('READY:slot5') < len(events)-1
        assert events[-1] == 'HISTORY:slot5'
        assert not any(e.startswith(('LOGIN:', 'STATUS:slot6')) for e in events)
    else:
        assert result['login_count'] == 1
        assert events.index('STATUS:slot5/before') < events.index('READY:slot5') < events.index('LOGIN:slot5') < events.index('STATUS:slot5/after')
        if provider == 'claude':
            assert result['history_count'] == 2
            positions = [i for i, e in enumerate(events) if e == 'HISTORY:slot5']
            assert positions[0] < events.index('READY:slot5') < positions[1] < events.index('LOGIN:slot5')
        else:
            assert result['history_count'] == 0


@pytest.mark.parametrize('old,new,allowed', [
    ('login_pipeline_worker_interactive_r3_v4.ps1', 'login_pipeline_worker_interactive_r3_v5.ps1', set()),
    ('login-six-workers-status-first-r3-v4.ps1', 'login-six-workers-status-first-r3-v5.ps1', {'Assert-FreshClaudeLogin'}),
    ('authenticate-native-profiles-status-first-r3-v4.ps1', 'authenticate-native-profiles-status-first-r3-v5.ps1', set()),
    ('run-pipeline-commissioning-r3-v4.ps1', 'run-pipeline-commissioning-r3-v5.ps1', set()),
    ('commission-first-warden-r3-v4.ps1', 'commission-first-warden-r3-v5.ps1', set()),
])
def test_existing_functions_stay_frozen_except_the_explicit_history_guard(old, new, allowed):
    before, after = functions_by_name(W / old), functions_by_name(W / new)
    assert before.keys() == after.keys()
    for name in before:
        if name not in allowed:
            assert before[name] == after[name], name
    if allowed:
        guard = after['Assert-FreshClaudeLogin']
        assert 'Assert-ReviewedClaudeHistory -Slot $Slot -Folder $folder -Runtime $runtime' in guard
        assert 'Get-ChildItem' not in guard


def test_both_perpetual_directory_guards_are_replaced_before_any_leaf_task_creation():
    leaf = LEAF.read_text()
    series = SERIES.read_text()
    call = 'Assert-ReviewedClaudeHistory -Slot $Slot -Folder $folder -Runtime $runtime'
    assert call in leaf and call in series
    assert leaf.index(call) < leaf.index('$nonce=[Guid]::NewGuid()') < leaf.index('$folder.RegisterTaskDefinition(')
    assert 'if($Apply)' in leaf[max(0, leaf.index(call)-40):leaf.index(call)]
    assert 'An earlier login session exists for this slot; preserve and review it before another login.' not in leaf
    assert 'An earlier Claude session exists for this logged-out worker.' not in series
    assert '$null=@(Assert-ReviewedClaudeHistory' in leaf
    assert '$null=@(Assert-ReviewedClaudeHistory' in series


def test_shared_history_source_is_pinned_and_held_by_both_execution_paths():
    pin = digest(HISTORY.name)
    leaf = LEAF.read_text()
    series = SERIES.read_text()
    for source in (leaf, series):
        assert HISTORY.name in source and pin in source
        assert 'Assert-ReviewedClaudeHistory' in source
    # Leaf's importer closes its own handle, so the source also needs the exact
    # Open-VerifiedFile custody hold; series' importer retains its held stream.
    assert '$held.Add((Open-VerifiedFile $historyPath' in leaf
    assert 'Import-SeriesFunctions' in series
    assert '$held.Add($stream);$stream=$null' in series


def test_all_transitive_wrapper_pins_select_v5_and_preserve_status_and_first_start_protocol():
    claude = digest(LEAF.name)
    provider = digest(SERIES.name)
    outer = digest(OUTER.name)
    for source in (SERIES, OUTER, TOP):
        text = source.read_text()
        assert LEAF.name in text and claude in text
    assert SERIES.name in OUTER.read_text() and provider in OUTER.read_text()
    assert SERIES.name in TOP.read_text() and provider in TOP.read_text()
    assert OUTER.name in TOP.read_text() and outer in TOP.read_text()
    assert 'commission-first-warden-r3-v5.ps1' in TOP.read_text()
    assert digest('commission-first-warden-r3-v5.ps1') in TOP.read_text()
    first_start = (W / 'commission-first-warden-r3-v5.ps1').read_text()
    assert 'commission-first-warden-r3-v5.py' in first_start
    assert digest('commission-first-warden-r3-v5.py') in first_start
    assert digest('check-worker-native-auth-status-six-r3-v4.ps1') in SERIES.read_text()
    assert digest('protected-code-inspection-v4.ps1') in LEAF.read_text()
    assert digest('protected-code-inspection-v4.ps1') in SERIES.read_text()
    for source in (SERIES, OUTER, TOP):
        assert '20261008-r3-v3-' in source.read_text()


@pytest.mark.parametrize('filename,pin', [
    ('login_pipeline_worker_interactive_r3_v4.ps1', '6f2181eb8e5ba5dc701ee9d69b496de01ffa5d7d74880bfa96e702dbd2e5c150'),
    ('login-six-workers-status-first-r3-v4.ps1', 'c908a3b2ece9858e23b023cab0557cab5ccadd03c3efc0460317bd62a1598bdb'),
    ('authenticate-native-profiles-status-first-r3-v4.ps1', '724c6ac99b4bbfa41a8da2c45892ee0efaa46deed458359194f308d4ee566dca'),
    ('run-pipeline-commissioning-r3-v4.ps1', 'fdd8f5fcacae85f2d80cb908d87d6fdad0b06c0783d2ff20fa2d59a458c4939d'),
    ('check-worker-native-auth-status-six-r3-v4.ps1', '4448e76eaeb30f2d2905f5aa6d3e14f0cc29b3d7e703321bb3df55afcba58e81'),
    ('commission-first-warden-r3-v4.ps1', '6e8acd55bdc3a66a78846b8db5601f07baa9b84677197cffb7b1f86721bd720b'),
    ('worker-native-auth-status-six-r3-v3.py', '7efa8d272fcd96701157e381036f7e4ec763551857f4c57091d7bf61977fcfc1'),
    ('commission-first-warden-r3-v3.py', '9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'),
    ('worker_claude_login_bridge_r3.py', 'b5bbede85ceb099460c6d6634f09a361a9b6617740eb082ab8d3bf5931260c73'),
])
def test_frozen_sources_are_preserved_exactly(filename, pin):
    assert digest(filename) == pin


def test_protected_host_configuration_and_capacity_are_unchanged():
    config = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\pipeline.json')
    assert hashlib.sha256(config.read_bytes()).hexdigest() == '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'


def test_one_actual_v5_preview_is_read_only_and_keeps_fresh_attempt_fences():
    run = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', f"& '{TOP}'"], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    value = json.loads(run.stdout)
    assert value['mode'] == 'READ_ONLY_PLAN'
    assert value['tasks_registered'] == value['model_jobs_submitted'] == 0
    assert value['identity_count'] == 6 and value['shared_capacity'] == 4
    assert not Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1').exists()
    assert not Path(r'C:\Program Files\CoChem\CommissioningSeries4.2.7-windows-20261008-r3-v3-' + '0'*32).exists()
    # Never touch a historical v3/v4 preview. The new evidence is exclusive.
    with (W / 'claude-continuation-v5-preview-final.json').open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
