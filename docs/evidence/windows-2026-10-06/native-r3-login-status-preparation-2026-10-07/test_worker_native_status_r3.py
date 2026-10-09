"""Portable parser/bounds tests only; no native provider or privileged launch."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('draft_worker_native_status', ROOT / 'worker-native-status-r3.py')
status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


def claude(**changes):
    return json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                      'subscriptionType': 'max', 'email': 'private@example.invalid',
                      'orgId': 'private-org', 'futureSecret': 'private-secret', **changes}).encode()


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_exact_codex_status_is_authentication_evidence_only(stream):
    output = b'Logged in using ChatGPT\r\n'
    result = status.parse_status('codex', output if stream == 'stdout' else b'',
                                 output if stream == 'stderr' else b'', 0)
    assert result['protocol_valid'] and result['logged_in']
    assert result['native_subscription_authentication_verified']
    assert result['auth_kind'] == 'chatgpt'
    assert not result['plan_entitlement_verified']
    assert not result['provider_account_identity_verified']


@pytest.mark.parametrize('out,err,code', [
    (b'Logged in using ChatGPT', b'', 1),
    (b'Logged in using ChatGPT', b'', True),
    (b'Logged in using ChatGPT', b'Logged in using an API key - private-secret', 0),
    (b'Logged in using ChatGPT\nWarning: private@example.invalid', b'', 0),
    (b'not logged in\nLogged in using ChatGPT', b'', 0),
    (b'Logged in using an API key - private-secret', b'', 0),
    (b'\xff', b'', 0),
    (b'x' * (status.MAX_OUTPUT + 1), b'', 0),
    (b'Logged in using ChatGPT', b'\0', 0),
], ids=lambda value: 'bytes-' + str(len(value)) if isinstance(value, bytes) else str(value))
def test_codex_errors_extra_or_ambiguous_output_never_attest(out, err, code):
    result = status.parse_status('codex', out, err, code)
    assert not result['protocol_valid'] and not result['native_subscription_authentication_verified']
    assert 'private' not in json.dumps(result)


def test_codex_logged_out_exact_negative_result():
    result = status.parse_status('codex', b'', b'Not logged in\n', 1)
    assert result['protocol_valid'] and not result['logged_in']
    assert result['auth_kind'] == 'none'


@pytest.mark.parametrize('plan', ['pro', 'max', 'team', 'enterprise'])
def test_claude_native_subscription_fields_are_allowlisted_and_redacted(plan):
    result = status.parse_status('claude', claude(subscriptionType=plan), b'', 0)
    assert result['protocol_valid'] and result['native_subscription_authentication_verified']
    assert result['subscription_type'] == plan
    assert not result['plan_entitlement_verified'] and not result['provider_account_identity_verified']
    encoded = json.dumps(result)
    assert all(value not in encoded for value in ('private@example.invalid', 'private-org', 'private-secret', 'email', 'orgId'))


@pytest.mark.parametrize('changes', [
    {'authMethod': 'api_key'}, {'authMethod': 'oauth_token'}, {'authMethod': 'api_key_helper'},
    {'authMethod': 'third_party'}, {'apiProvider': 'bedrock'}, {'apiProvider': None},
    {'subscriptionType': 'free'}, {'subscriptionType': None},
])
def test_claude_non_subscription_or_incomplete_status_remains_unverified(changes):
    result = status.parse_status('claude', claude(**changes), b'', 0)
    assert result['protocol_valid'] and result['logged_in']
    assert not result['native_subscription_authentication_verified']


@pytest.mark.parametrize('out,err,code', [
    (b'{"loggedIn":true,"loggedIn":false,"authMethod":"claude.ai"}', b'', 0),
    (b'{"loggedIn":true,"authMethod":"claude.ai","extra":NaN}', b'', 0),
    (b'[]', b'', 0), (b'not JSON', b'', 0), (b'\xff', b'', 0),
    (claude(loggedIn=1), b'', 0), (claude(loggedIn='true'), b'', 0),
    (claude(authMethod='none'), b'', 0), (claude(authMethod='private-secret'), b'', 0),
    (claude(), b'private error', 0), (claude(), b'', 1), (claude(), b'', True),
    (claude(loggedIn=False), b'', 0), (b'x' * (status.MAX_OUTPUT + 1), b'', 0),
], ids=lambda value: 'bytes-' + str(len(value)) if isinstance(value, bytes) else str(value))
def test_claude_malformed_inconsistent_or_error_output_never_attests(out, err, code):
    result = status.parse_status('claude', out, err, code)
    assert not result['protocol_valid'] and not result['native_subscription_authentication_verified']
    assert 'private' not in json.dumps(result)


def test_claude_logged_out_status_is_an_explicit_negative():
    result = status.parse_status('claude', claude(loggedIn=False, authMethod='none', subscriptionType=None), b'', 1)
    assert result['protocol_valid'] and not result['logged_in']
    assert not result['native_subscription_authentication_verified']


def test_agy_has_no_status_contract_in_this_helper():
    with pytest.raises(ValueError):
        status.parse_status('gemini', b'quota 99%', b'', 0)


def test_combined_output_bound_is_enforced():
    result = status.parse_status('codex', b'Logged in using ChatGPT', b' ' * status.MAX_OUTPUT, 0)
    assert not result['native_subscription_authentication_verified']


def test_wait_observes_actual_temporary_file_size_before_accepting_exit():
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        output.write(b'x' * status.MAX_OUTPUT); output.flush()
        errors.write(b'x'); errors.flush()
        process = SimpleNamespace(poll=lambda: 0)
        with pytest.raises(ValueError, match='output exceeded'):
            status.bounded_wait(process, output, errors)


def test_wait_deadline_does_not_wait_for_status_child_indefinitely(monkeypatch):
    readings = iter((0, status.STATUS_SECONDS))
    monkeypatch.setattr(status, 'time', SimpleNamespace(monotonic=lambda: next(readings), sleep=lambda _: None))
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = SimpleNamespace(poll=lambda: None)
        with pytest.raises(TimeoutError):
            status.bounded_wait(process, output, errors)


def test_digest_reads_real_bytes_and_refuses_size_limit(tmp_path):
    import hashlib
    path = tmp_path / 'capture'; path.write_bytes(b'bounded source bytes')
    assert status.digest_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='size bound'):
        status.digest_file(path, maximum=3)


@pytest.mark.skipif(os.name != 'nt', reason='Actual PowerShell parser, no script actions')
def test_powershell_wrapper_syntax_defaults_and_source_pin():
    import hashlib
    wrapper = ROOT / 'check-worker-native-status-r3.ps1'
    text = wrapper.read_text()
    assert hashlib.sha256((ROOT / 'worker-native-status-r3.py').read_bytes()).hexdigest() in text
    escaped = str(wrapper).replace("'", "''")
    command = f"$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{escaped}',[ref]$tokens,[ref]$errors); if ($errors.Count) {{throw $errors[0]}}; "
    command += "$body=$ast.ParamBlock.Extent.Text+'; [ordered]@{apply=[bool]$Apply;slot=$Slot;provider=$Provider}';$bind=[scriptblock]::Create($body); & $bind -Slot slot6 -Provider claude | ConvertTo-Json -Compress"
    exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'apply': False, 'slot': 'slot6', 'provider': 'claude'}


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell task-wait fixture, no scheduler/native actions')
def test_task_wait_accepts_only_exact_completion_and_keeps_other_failures_closed():
    wrapper = str(ROOT / 'check-worker-native-status-r3.ps1').replace("'", "''")
    command = f"$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{wrapper}',[ref]$tokens,[ref]$errors); "
    command += r"""
        $definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Wait-StatusTaskInstance'},$true);
        . ([scriptblock]::Create($definition.Extent.Text));
        function Start-Sleep {param($Milliseconds)}
        $done=[pscustomobject]@{State=3};$done|Add-Member ScriptMethod Refresh {return};Wait-StatusTaskInstance $done;
        $finished=[pscustomobject]@{State=4};$finished|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('fixture',-2147216629)};
        Wait-StatusTaskInstance $finished;
        $denied=[pscustomobject]@{State=4};$denied|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('fixture',-2147024891)};
        $unknownRefused=$false;try {Wait-StatusTaskInstance $denied} catch {$unknownRefused=$true};
        $running=[pscustomobject]@{State=4};$running|Add-Member ScriptMethod Refresh {return};
        $timeoutRefused=$false;try {Wait-StatusTaskInstance $running -TimeoutSeconds -1} catch {$timeoutRefused=$true};
        [ordered]@{exact_completion=$true;unknown_refused=$unknownRefused;timeout_refused=$timeoutRefused}|ConvertTo-Json -Compress
    """
    exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'exact_completion': True, 'unknown_refused': True, 'timeout_refused': True}


@pytest.mark.skipif(os.name != 'nt', reason='Production PowerShell guard with inert scheduler objects; no task/native execution')
@pytest.mark.parametrize('task_name', ['CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor'])
@pytest.mark.parametrize('enabled,state', [(True, 3), (False, 4), (False, 2), (False, 0)])
def test_actual_deployment_daemon_names_prevent_native_launch(task_name, enabled, state):
    def run_guard(script, names):
        assert {'CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor'} <= set(names)
        quoted_names = json.dumps(names).replace("'", "''")
        prefix = f"$ErrorActionPreference='Stop';$data=ConvertFrom-Json -InputObject '{quoted_names}';$script:blockedTask='{task_name}';$script:enabled=${str(enabled).lower()};$script:state={state};"
        prefix += r"""
            $script:fakeFolder=[pscustomobject]@{};
            $script:fakeFolder|Add-Member ScriptMethod GetTask {param($name)
                if ($name -eq $script:blockedTask) {$task=[pscustomobject]@{Enabled=$script:enabled;State=$script:state}}else{$task=[pscustomobject]@{Enabled=$false;State=1}}
                $task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=0}};return $task
            };
            $script:fakeScheduler=[pscustomobject]@{};
            $script:fakeScheduler|Add-Member ScriptMethod Connect {return};
            $script:fakeScheduler|Add-Member ScriptMethod GetFolder {param($path) return $script:fakeFolder};
            function New-Object {param($ComObject) if ($ComObject -ne 'Schedule.Service') {throw 'Unexpected object'};return $script:fakeScheduler};
        """
        exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', prefix + script],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0
        assert 'Protected daemons must remain stopped and disabled' in result.stderr
        raise RuntimeError('Actual selected task blocks native launch')
    win = SimpleNamespace(_powershell=run_guard, launch_worker=lambda *a, **k: pytest.fail('Native launch must not be reached'))
    report = {'native_status_dispatch_attempted': False, 'native_status_commands_executed': 0}
    with pytest.raises(RuntimeError, match='blocks native launch'):
        status.launch_status_process(win, None, None, None, None, None, None, None, report)
    assert report == {'native_status_dispatch_attempted': False, 'native_status_commands_executed': 0}


@pytest.mark.skipif(os.name != 'nt', reason='PowerShell wrapper guard AST with inert task objects')
@pytest.mark.parametrize('task_name', ['CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor'])
def test_wrapper_plan_holds_enabled_actual_deployment_daemon(task_name):
    wrapper = str(ROOT / 'check-worker-native-status-r3.ps1').replace("'", "''")
    command = f"$ErrorActionPreference='Stop';$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{wrapper}',[ref]$tokens,[ref]$errors);$script:blockedTask='{task_name}'; "
    command += r"""
        $loop=$ast.Find({param($n) $n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Extent.Text.Contains('CoChem-4.2.7-Warden')},$true);
        if ($null -eq $loop) {throw 'Actual deployment guard loop missing'};
        function Get-ExactTaskOrAbsent {param($Name) $task=[pscustomobject]@{Enabled=($Name -eq $script:blockedTask);State=1};$task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=0}};return $task};
        $holds=@();. ([scriptblock]::Create($loop.Extent.Text));
        [ordered]@{hold_count=$holds.Count;actual_name_held=($holds[0].Contains($script:blockedTask))}|ConvertTo-Json -Compress
    """
    exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'hold_count': 1, 'actual_name_held': True}


def test_sanitized_failure_retains_phase_and_error_number_without_sensitive_text():
    error = OSError('secret-code private@example.invalid')
    error.winerror = 5
    result = status.safe_failure(error, 'layout')
    assert result == {'phase':'layout','error_type':'OSError','winerror':5}
    assert 'secret' not in json.dumps(result)
    assert status.safe_failure(error, 'untrusted code')['phase'] == 'trusted_preflight'


@pytest.mark.parametrize('change,valid', [('none',True),('base',False),('isolation',False),('bytecode',False),('version',False),('cfg',False)])
def test_interpreter_binding_requires_protected_base_and_isolation(monkeypatch, change, valid):
    fake = SimpleNamespace(executable=str(status.PYTHON),base_prefix=str(status.BASE_PYTHON.parent),
        _base_executable=str(status.BASE_PYTHON),version_info=(3,12,13),flags=SimpleNamespace(isolated=1),dont_write_bytecode=True)
    if change == 'base': fake.base_prefix = r'C:\untrusted'
    if change == 'isolation': fake.flags.isolated = 0
    if change == 'bytecode': fake.dont_write_bytecode = False
    if change == 'version': fake.version_info = (3,12,12)
    monkeypatch.setattr(status, 'sys', fake)
    monkeypatch.setattr(status, 'digest_file', lambda path: '0'*64 if change=='cfg' else status.VENV_CONFIG_SHA256)
    if valid: status.validate_interpreter()
    else:
        with pytest.raises(ValueError,match='interpreter binding'):
            status.validate_interpreter()


def run_wrapper_functions(body):
    wrapper = str(ROOT/'check-worker-native-status-r3.ps1').replace("'", "''")
    code = f"$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{wrapper}',[ref]$tokens,[ref]$errors);if($errors.Count){{throw $errors[0]}};"
    code += "foreach($f in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))};"
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code+body],capture_output=True,text=True,timeout=15)


@pytest.mark.parametrize('state,instances,valid', [(1,0,True),(3,0,True),(0,0,False),(2,0,False),(4,0,False),(3,1,False)])
def test_terminal_status_requires_known_state_and_no_instances(state, instances, valid):
    result = run_wrapper_functions(f"$task=[pscustomobject]@{{State={state}}};$task|Add-Member ScriptMethod GetInstances {{param($flags)[pscustomobject]@{{Count={instances}}}}};Assert-CompletedStatusTask $task")
    assert (result.returncode == 0) is valid, result.stderr


@pytest.mark.parametrize('change,valid', [('',True),("$text=$text.Replace('include-system-site-packages = false','include-system-site-packages = true')",False),
    ("$text=$text.Replace('Python312','untrusted')",False),("$text+=\"`nhome = C:\\untrusted\"",False)])
def test_venv_file_requires_exact_safe_binding(change, valid):
    result = run_wrapper_functions(r"$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312';$basePython=Join-Path $basePythonRoot 'python.exe';$text=[IO.File]::ReadAllText('C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\pyvenv.cfg');"+change+';Assert-VenvBinding $text')
    assert (result.returncode == 0) is valid, result.stderr
