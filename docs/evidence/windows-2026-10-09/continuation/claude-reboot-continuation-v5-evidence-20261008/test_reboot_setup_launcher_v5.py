"""Attended launcher gating; real source/tail, inert children and ordinary IO.

Never invokes a real child Apply, provider, scheduled task, protected write or
private report. The only full-script invocation is its metadata-only default.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
SOURCE = W / 'run-reboot-setup-r3-v5.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
CHILDREN = (
    ('commissioning', 'run-pipeline-commissioning-r3-v5.ps1', '46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0'),
    ('monitoring_setup', 'run-post-commissioning-setup-r3-v2.ps1', 'dd2b8119f8f1c7a206c8ef12e6c2ef45634695fd83fe36bc8b361154044125f2'),
)


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps(body, timeout=30):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
'''
    prefix += '$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile(' + q(SOURCE) + ',[ref]$tokens,[ref]$errors);if($errors.Count){throw $errors[0]};'
    prefix += r'''foreach($definition in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($definition.Extent.Text))}
'''
    encoded = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    return subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=timeout)


def success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip())


def physical_fixture_header():
    # Physical script metadata supplies the same automatic PSScriptRoot that
    # production receives. AST-created anonymous scriptblocks leave it empty.
    source = SOURCE.read_text()
    return source[:source.index('\nif($PSVersionTable')]


def run_fixture(path, text):
    path.write_text(text)
    return subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(path)], capture_output=True, text=True, timeout=30)


def actual_tail_fixture(tmp_path, first='0', second='0', changed=None):
    """Keep the real source preflight, callback, catch/finally and exit20 tail."""
    for phase, name, pin in CHILDREN:
        raw = (W / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin
        (tmp_path / name).write_bytes(raw + (b'\n# changed inert fixture' if changed == phase else b''))
    source = SOURCE.read_text()
    tail = source[source.index('\n$held=')+1:]
    tail_path = tmp_path / 'actual-tail.ps1'
    body = physical_fixture_header() + "\nImport-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1')\n"
    body += '$Apply=$true;$Interactive=$true;'
    body += '$firstCode=' + first + ';$secondCode=' + second + ';$callsPath=' + q(tmp_path / 'calls.jsonl') + ';'
    body += '$hostPath=' + q(tmp_path / 'host-metadata.txt') + ';'
    # These are exact production specs and callback/tail bytes. Only the native
    # child boundary and host-output sink are substituted below.
    body += source[source.index('$specs=@('):source.index('\n$held=')]
    body += r'''
    function Write-Host{
      [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)][string]$Object)
      process{[IO.File]::AppendAllText($hostPath,$Object+[Environment]::NewLine)}
    }
    function Invoke-RebootConsoleChild{
      param([string[]]$Arguments)
      $leaf=[IO.Path]::GetFileName($Arguments[3])
      $row=@($specs|Where-Object{$_.file -ceq $leaf})
      if($row.Count -ne 1){throw 'Unexpected inert child'}
      $record=@{phase=$row[0].phase;arguments=@($Arguments)}|ConvertTo-Json -Compress
      [IO.File]::AppendAllText($callsPath,$record+[Environment]::NewLine)
      if($row[0].phase -ceq 'commissioning'){return $firstCode};return $secondCode
    }
    '''
    body += tail
    result = run_fixture(tail_path, body)
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()] if (tmp_path / 'calls.jsonl').exists() else []
    host = (tmp_path / 'host-metadata.txt').read_text() if (tmp_path / 'host-metadata.txt').exists() else ''
    return result, calls, host


@pytest.mark.parametrize('first,second,expected_exit,expected_phases', [
    ('0', '0', 0, ['commissioning', 'monitoring_setup']),
    ('20', '0', 20, ['commissioning']),
    ('1', '0', 1, ['commissioning']),
    ('0', '1', 1, ['commissioning', 'monitoring_setup']),
    ('0', '20', 1, ['commissioning', 'monitoring_setup']),
    ("'0'", '0', 1, ['commissioning']),
    ('[double]0', '0', 1, ['commissioning']),
    ('[long]0', '0', 1, ['commissioning']),
    ('$null', '0', 1, ['commissioning']),
    ('@([int]0,[int]0)', '0', 1, ['commissioning']),
    ('0', "'0'", 1, ['commissioning', 'monitoring_setup']),
])
def test_real_tail_and_actual_phase_callback_stop_at_pause_failure_or_invalid_exit(tmp_path, first, second, expected_exit, expected_phases):
    result, calls, host = actual_tail_fixture(tmp_path, first, second)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert [call['phase'] for call in calls] == expected_phases
    assert len(calls) == len(set(call['phase'] for call in calls))
    for call in calls:
        name = next(name for phase, name, _ in CHILDREN if phase == call['phase'])
        assert call['arguments'] == ['-NoLogo', '-NoProfile', '-File', str(tmp_path / name), '-Apply', '-Interactive']
    if expected_exit == 20:
        value = json.loads(result.stdout)
        assert value['status'] == 'AUTHENTICATION_PAUSED_BEFORE_LOGIN'
        assert value['monitoring_setup_started'] is False and value['completed_signins_preserved'] is True
    elif expected_exit == 0:
        value = json.loads(result.stdout)
        assert value['status'] == 'COMMISSIONING_AND_MONITORING_SETUP_COMPLETED_ACCEPTANCE_PENDING'
        for field in ('paid_repair_enabled', 'component_recovery_enabled', 'continuous_48h_complete', 'full_srs_acceptance', 'automatic_retry'):
            assert value[field] is False
    else:
        metadata = json.loads(next(line for line in host.splitlines() if line.startswith('{')))
        assert metadata['status'] == 'HELD_PRESERVE_CHILD_EVIDENCE_AND_RUNNING_STATE'
        assert metadata['phase'] == expected_phases[-1]
        assert metadata['automatic_retry'] is False
    # Every actual tail exit, including exit20 and throws, disposes held source
    # streams. Existing fixture bytes remain; nothing retries or deletes them.
    for _, name, pin in CHILDREN:
        path = tmp_path / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pin
        with path.open('r+b'):
            pass


@pytest.mark.parametrize('changed', ['commissioning', 'monitoring_setup'])
def test_either_changed_source_blocks_both_children_before_any_phase(tmp_path, changed):
    result, calls, host = actual_tail_fixture(tmp_path, changed=changed)
    assert result.returncode == 1 and calls == []
    metadata = json.loads(next(line for line in host.splitlines() if line.startswith('{')))
    assert metadata['phase'] == 'source_preflight'
    assert metadata['status'] == 'HELD_PRESERVE_CHILD_EVIDENCE_AND_RUNNING_STATE'
    changed_name = next(name for phase, name, _ in CHILDREN if phase == changed)
    assert (tmp_path / changed_name).read_bytes().endswith(b'# changed inert fixture')
    for _, name, _ in CHILDREN:
        with (tmp_path / name).open('r+b'):
            pass


@pytest.mark.parametrize('kind', ['valid', 'changed_pin', 'empty', 'oversize'])
def test_real_source_reader_holds_exact_bytes_denies_writers_and_closes_on_refusal(tmp_path, kind):
    path = tmp_path / 'inert.ps1'
    raw = b'# ordinary source fixture\n'
    if kind == 'empty':
        raw = b''
    elif kind == 'oversize':
        raw = b'x'*131073
    path.write_bytes(raw)
    pin = 'f'*64 if kind == 'changed_pin' else hashlib.sha256(raw).hexdigest()
    body = '$path=' + q(path) + ';$pin=' + q(pin) + ';'
    body += r'''
    $held=$null;$accepted=$false;$writerDenied=$false;$deleteDenied=$false
    try{
      $held=Open-RebootSetupSource $path $pin;$accepted=$true
      try{$writer=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$writer.Dispose()}catch{$writerDenied=$true}
      try{[IO.File]::Delete($path)}catch{$deleteDenied=$true}
    }catch{}finally{if($null -ne $held){$held.Dispose()}}
    $writer=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite);$writer.Dispose()
    @{accepted=$accepted;writer_denied=$writerDenied;delete_denied=$deleteDenied;exists=[IO.File]::Exists($path);released=$true}|ConvertTo-Json
    '''
    value = success(ps(body))
    assert value['accepted'] == (kind == 'valid')
    assert value['exists'] and value['released']
    if kind == 'valid':
        assert value['writer_denied'] and value['delete_denied']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('argument,accepted', [('fixed', True), ('has"quote', False), ('has\rreturn', False), ('has\nnewline', False)])
def test_actual_console_invoker_inherits_console_waits_disposes_and_refuses_delimiters(tmp_path, argument, accepted):
    body = physical_fixture_header() + "\nImport-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1')\n"
    body += '$argument=' + q(argument) + ';$powershell=' + q(PS) + ';'
    body += r'''
    $script:calls=0;$script:waits=0;$script:refreshes=0;$script:disposals=0;$script:flags=$null
    function Start-Process{
      param($FilePath,$ArgumentList,$WorkingDirectory,[switch]$NoNewWindow,[switch]$Wait,[switch]$PassThru)
      $script:calls++;$script:flags=@{path=$FilePath;arguments=$ArgumentList;working=$WorkingDirectory;inherit=[bool]$NoNewWindow;wait=[bool]$Wait;process=[bool]$PassThru}
      $p=[pscustomobject]@{ExitCode=0}
      $p|Add-Member ScriptMethod WaitForExit{$script:waits++}
      $p|Add-Member ScriptMethod Refresh{$script:refreshes++}
      $p|Add-Member ScriptMethod Dispose{$script:disposals++}
      return $p
    }
    $accepted=$false;$code=$null
    try{$code=Invoke-RebootConsoleChild @($argument);$accepted=$true}catch{}
    @{accepted=$accepted;code=$code;calls=$calls;waits=$waits;refreshes=$refreshes;disposals=$disposals;flags=$flags}|ConvertTo-Json -Depth 4
    '''
    value = success(run_fixture(tmp_path / 'actual-invoker.ps1', body))
    assert value['accepted'] == accepted
    assert value['calls'] == int(accepted)
    if accepted:
        assert value['code'] == 0 and value['waits'] == value['refreshes'] == value['disposals'] == 1
        assert value['flags'] == {'path': PS, 'arguments': '"fixed"', 'working': str(tmp_path), 'inherit': True, 'wait': True, 'process': True}


def test_exact_frozen_source_pins_and_no_redirected_or_background_child_path():
    text = SOURCE.read_text()
    for _, name, pin in CHILDREN:
        assert hashlib.sha256((W / name).read_bytes()).hexdigest() == pin
        assert name in text and pin in text
    value = success(ps(r'''
    $definitions=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Invoke-RebootConsoleChild'},$true))
    $commands=@($definitions[0].FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Start-Process'},$true))
    @{count=$commands.Count;parameters=@($commands[0].CommandElements|Where-Object{$_ -is [Management.Automation.Language.CommandParameterAst]}|ForEach-Object ParameterName)}|ConvertTo-Json
    '''))
    assert value['count'] == 1
    assert set(value['parameters']) == {'FilePath', 'ArgumentList', 'WorkingDirectory', 'NoNewWindow', 'Wait', 'PassThru'}
    assert '$held.Add((Open-RebootSetupSource' in text
    assert text.index('foreach($s in $specs){$held.Add(') < text.index('$outcome=Invoke-RebootSetupPhases')
    assert 'Schedule.Service' not in text and 'RegisterTaskDefinition' not in text
    assert 'RedirectStandard' not in text and 'Stop-Process' not in text and 'Remove-Item' not in text


def test_one_actual_default_plan_is_metadata_only_and_keeps_acceptance_explicit():
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', '& ' + q(SOURCE)], capture_output=True, text=True, timeout=30)
    value = success(result)
    assert value['mode'] == 'READ_ONLY_PLAN' and value['source_custody_verified'] is True
    assert value['child_processes_started'] == value['tasks_changed'] == value['model_jobs_submitted'] == 0
    assert value['full_srs_acceptance'] is False and value['automatic_retry'] is False
    assert [(row['phase'], row['file'], row['sha256']) for row in value['phases']] == list(CHILDREN)
    with (W / 'reboot-setup-launcher-v5-preview-final.json').open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
