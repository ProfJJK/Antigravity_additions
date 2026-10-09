"""Focused WinPS continuation guards with disposable files and inert phase IO."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
spec = importlib.util.spec_from_file_location('running_monitoring_generator', W / 'prepare-running-monitoring-continuation-v8.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def generated(tmp_path, monkeypatch):
    (tmp_path / G.OLD).write_bytes((W / G.OLD).read_bytes())
    child = b'# Inert pinned child used only by generation fixtures\n'
    (tmp_path / G.CHILD).write_bytes(child)
    monkeypatch.setattr(G, 'W', tmp_path)
    pin = hashlib.sha256(child).hexdigest()
    raw = G.render(pin)
    path = tmp_path / G.NEW
    path.write_bytes(raw)
    return path, pin


def run_ps(tmp_path, source, body):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(SOURCE,[ref]$t,[ref]$e)
if($e.Count){throw 'parse'}
foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
'''.replace('SOURCE', q(source))
    path = tmp_path / 'inert-case.ps1'
    path.write_text(prefix + body, encoding='utf-8-sig')
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(path)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


def test_generation_preserves_runtime_resource_payload_and_completed_phase_bindings(generated):
    path, pin = generated
    text = path.read_text(encoding='utf-8-sig')
    assert f"file='{G.CHILD}';hash='{pin}'" in text
    assert "file='install-resource-observer-r3-v5.ps1';hash='ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f'" in text
    assert "source_manifest_sha256 -cne '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49'" in text
    assert 'RunningMonitoringContinuation4.2.7-windows-20261008-r3-v8' in text
    assert 'PostCommissioningSetup4.2.7-windows-20261008-r3-v4' in text
    assert '$priorMonitoringHold=Read-SetupPriorMonitoringHold' in text
    assert 'first_start' in text and 'SETUP_FIRST_START_REPLAY_FORBIDDEN' in text
    assert hashlib.sha256((W / G.OLD).read_bytes()).hexdigest() == G.OLD_SHA


@pytest.mark.parametrize('changed', ['parent', 'child'])
def test_generation_refuses_changed_source_before_any_output(generated, changed):
    path, pin = generated
    target = G.W / (G.OLD if changed == 'parent' else G.CHILD)
    target.write_bytes(target.read_bytes() + b'\n# changed\n')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Reviewed source changed'):
        G.render(pin)
    assert path.read_bytes() == before


@pytest.mark.parametrize('pin', ['', 'A' * 64, '1' * 63])
def test_generation_requires_explicit_final_child_pin(generated, pin):
    with pytest.raises(ValueError, match='Explicit reviewed child'):
        G.render(pin)


def prior_records():
    return {
        'failure': {'schema': 'cochem-protected-acceptance-setup-failure/1',
                    'status': 'SETUP_HELD_PRESERVE_PARTIAL_AND_RUNNING_STATE',
                    'phase': 'held_observation', 'code': 'SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED',
                    'nonce': 'b43e819326c14735a021ff275f47f03a',
                    'verified_phases': ['independent_staging', 'first_start'],
                    'automatic_retry_or_resume': False, 'full_srs_acceptance': False,
                    'running_state_preserved': True, 'no_automatic_stop': True},
        'intent': {'schema': 'cochem-protected-acceptance-setup-intent/1',
                   'nonce': 'b43e819326c14735a021ff275f47f03a', 'branch': 'commissioned',
                   'full_srs_acceptance': False, 'automatic_retry_or_resume': False},
    }


@pytest.mark.parametrize('drift', [None, 'phase', 'nonce', 'verified_phases', 'retry', 'preservation', 'intent', 'pin'])
def test_prior_failure_guard_binds_existing_evidence_without_any_write(tmp_path, generated, drift):
    path, _ = generated
    records = prior_records()
    if drift == 'phase': records['failure']['phase'] = 'resource_observation'
    if drift == 'nonce': records['failure']['nonce'] = '0' * 32
    if drift == 'verified_phases': records['failure']['verified_phases'].reverse()
    if drift == 'retry': records['failure']['automatic_retry_or_resume'] = True
    if drift == 'preservation': records['failure']['running_state_preserved'] = False
    if drift == 'intent': records['intent']['nonce'] = '0' * 32
    body = '$fixture=' + q(json.dumps(records)) + '|ConvertFrom-Json\n'
    body += '$script:badPin=$' + str(drift == 'pin').lower() + '\n'
    body += r'''
$script:reads=[Collections.Generic.List[string]]::new();$script:writes=0
function Read-R3Control {param($Path,$Pin,$Maximum)
 if($Maximum -ne 65536){throw 'bound'}
 $name=Split-Path -Leaf $Path;$script:reads.Add($name)
 if($script:badPin){throw 'pinned source refused'}
 $expected=if($name -ceq 'series-failed.json'){'fde32aea0f0f200b161d7b0fa4ae52fabde716c5ed46a74032e7980245475b12'}else{'4c0638e61527cb4d82fd378e39b270362314d952ff36ae18063e12ebb083b1ac'}
 if($Pin -cne $expected){throw 'unexpected pin'}
 [pscustomobject]@{Sha256=$Pin;Name=$name}
}
function Read-R3Text {param($Control)
 if($Control.Name -ceq 'series-failed.json'){$fixture.failure|ConvertTo-Json -Depth 5}else{$fixture.intent|ConvertTo-Json -Depth 5}
}
function Write-RegistrationControl {$script:writes++;throw 'forbidden write'}
$accepted=$false;$proof=$null;try{$proof=Read-SetupPriorMonitoringHold;$accepted=$true}catch{}
@{accepted=$accepted;writes=$script:writes;reads=@($script:reads.ToArray());proof=$proof}|ConvertTo-Json -Depth 8
'''
    value = run_ps(tmp_path, path, body)
    assert value['accepted'] == (drift is None)
    assert value['writes'] == 0
    if drift is None:
        assert value['reads'] == ['series-failed.json', 'series-intent.json']
        assert value['proof']['running_observer_restarted'] is False
        assert value['proof']['prior_series_replayed'] is False


def test_disposition_requires_completed_staging_and_never_replays_first_start(tmp_path, generated):
    path, _ = generated
    value = run_ps(tmp_path, path, r'''
$refused=$false;try{$null=Get-SetupDisposition commissioned $false}catch{$refused=$_.Exception.Message -ceq 'SETUP_RECORDED_STAGING_REQUIRED_NO_REINSTALL'}
@{refused_missing_stage=$refused;phases=@(Get-SetupDisposition commissioned $true)}|ConvertTo-Json -Depth 5
''')
    assert value['refused_missing_stage'] is True
    assert [(p['phase'], p['mode']) for p in value['phases']] == [
        ('oracle_maintenance', 'deferred_maintenance'), ('independent_staging', 'reuse_verified_receipt'),
        ('first_start', 'reattest_current_instance'), ('held_observation', 'execute'), ('resource_observation', 'execute')]


@pytest.mark.parametrize('failure', [None, 'held_observation', 'resource_observation', 'held_verify'])
def test_real_phase_flow_recovers_only_monitoring_and_stops_at_first_failure(tmp_path, generated, failure):
    path, _ = generated
    body = '$failure=' + q(failure or '') + '\n' + r'''
$script:calls=[Collections.Generic.List[string]]::new();$script:verified=[Collections.Generic.List[string]]::new();$script:records=[Collections.Generic.List[string]]::new();$refused=$false
try{Invoke-SetupPhases @(Get-SetupDisposition commissioned $true) {
 param($n);$script:calls.Add($n);if($n -ceq $failure){[int]2}else{[int]0}
} {
 param($n);if($failure -ceq 'held_verify' -and $n -ceq 'held_observation'){throw 'inert proof failure'};$script:verified.Add($n);@{phase=$n}
} {param($n,$state,$proof)$script:records.Add($n+':'+$state)} {}}catch{$refused=$true}
@{refused=$refused;calls=@($script:calls.ToArray());verified=@($script:verified.ToArray());records=@($script:records.ToArray())}|ConvertTo-Json -Depth 5
'''
    value = run_ps(tmp_path, path, body)
    assert value['refused'] == (failure is not None)
    assert 'first_start' not in value['calls'] and 'independent_staging' not in value['calls']
    assert value['calls'] == (['held_observation'] if failure in ('held_observation', 'held_verify')
                              else ['held_observation', 'resource_observation'])
    assert value['verified'][:2] == ['independent_staging', 'first_start']


def test_injected_first_start_action_is_still_forbidden(tmp_path, generated):
    path, _ = generated
    value = run_ps(tmp_path, path, r'''
$script:actions=0;$refused=$false
try{Invoke-SetupPhases @([pscustomobject]@{phase='first_start';mode='execute'}) {$script:actions++;[int]0} {} {} {}}catch{$refused=$_.Exception.Message -ceq 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}
@{refused=$refused;actions=$script:actions}|ConvertTo-Json
''')
    assert value == {'refused': True, 'actions': 0}


def test_reviewed_generation_matches_final_source_if_present():
    child = W / G.CHILD
    output = W / G.NEW
    assert child.exists() and output.exists(), 'Final reviewed production source must be prepared before this check'
    assert G.render(hashlib.sha256(child.read_bytes()).hexdigest()) == output.read_bytes()
