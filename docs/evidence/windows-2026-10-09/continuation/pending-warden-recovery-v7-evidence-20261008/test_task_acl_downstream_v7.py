"""Ordinary WinPS5.1 downstream checks; no real Apply/provider/task/private state.

The sole full-script invocation is the launcher's metadata-only default. Every
Apply-shaped tail and native boundary below uses disposable files and doubles.
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
ACL = W / 'task-private-acl-v8.ps1'
ACL_SHA = 'f48d47e33c6a8885d35199456e24f1e606c771ee215c92037686e4399e1f1c47'
RECOVERY = W / 'resume-pending-warden-r3-v7.ps1'
RECOVERY_SHA = 'a34a1bf07dfa0546752e650eb20e0babdea32bbd5a19c0620112fc878defc873'
HELD = W / 'install-held-supervisor-observation-r3-v3.ps1'
OBSERVER = W / 'install-resource-observer-r3-v5.ps1'
BATCH = W / 'run-post-commissioning-setup-r3-v4.ps1'
LAUNCHER = W / 'run-pending-warden-setup-r3-v7.ps1'
CHILDREN = (
    ('pending_registration_recovery', RECOVERY.name, RECOVERY_SHA),
    ('monitoring_setup', BATCH.name,
     '933aac1f6ca8ab9bf959aa4578a18284fb0aef5d57e93fc2c834da557e1e13c4'),
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, W / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G = load('task_acl_downstream_generator', 'prepare-task-acl-downstream-v7.py')
O = load('task_acl_observer_baseline', 'test_resource_observer_installer_r3_v3.py')
H = load('task_acl_held_baseline', 'test_held_supervisor_observation_wrapper_r3_v1.py')
L = load('task_acl_launcher_baseline', 'test_reboot_setup_launcher_v5.py')
L.SOURCE = LAUNCHER
L.CHILDREN = CHILDREN
H.SCRIPT = HELD


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps(paths, body, timeout=45):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
function Definitions([string]$Path){
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e)
 if($e.Count){throw 'Parser failure'}
 foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$f.Extent.Text.Replace('$PSScriptRoot','$script:fixtureScriptRoot')}
}
'''
    prefix += '$script:fixtureScriptRoot=' + q(W) + '\n'
    for path in paths:
        prefix += 'foreach($f in Definitions ' + q(path) + '){. ([scriptblock]::Create($f))}\n'
    command = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', command],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


def observer_ps(body):
    return ps((W / 'register-stopped-warden-r3.ps1', ACL, OBSERVER), body)


O.run_ps = observer_ps
# The native registered task supplies its Name. Old doubles omitted it because
# the earlier validator did not bind a backing-file witness to the task name.
O.FAKES = O.FAKES.replace(
    'Definition=$Definition;State=1;',
    'Definition=$Definition;Name=$script:taskName;State=1;',
)


def test_frozen_generation_reproduces_all_three_outputs_and_originals():
    expected = {
        HELD.name: 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6',
        OBSERVER.name: 'ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f',
        BATCH.name: CHILDREN[1][2],
    }
    outputs = G.render(acl_sha256=ACL_SHA, recovery_file=RECOVERY.name,
                       recovery_sha256=RECOVERY_SHA)
    assert set(outputs) == set(expected)
    for name, raw in outputs.items():
        assert raw == (W / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected[name]
    for name, pin in G.PINS.items():
        assert hashlib.sha256((W / name).read_bytes()).hexdigest() == pin


@pytest.mark.parametrize('changed', [ACL.name, RECOVERY.name, *G.PINS])
def test_generation_refuses_any_changed_reviewed_source_before_output(tmp_path, monkeypatch, changed):
    for name in (ACL.name, RECOVERY.name, *G.PINS):
        raw = (W / name).read_bytes()
        (tmp_path / name).write_bytes(raw + (b'\n# inert drift' if name == changed else b''))
    monkeypatch.setattr(G, 'W', tmp_path)
    with pytest.raises(ValueError, match='Reviewed source changed'):
        G.render(acl_sha256=ACL_SHA, recovery_file=RECOVERY.name,
                 recovery_sha256=RECOVERY_SHA)
    assert not any((tmp_path / name).exists() for name in (HELD.name, OBSERVER.name, BATCH.name))


@pytest.mark.parametrize('path', [HELD, OBSERVER, BATCH, LAUNCHER, ACL, RECOVERY])
def test_actual_windows_ast_parses_frozen_successors_without_host_invocation(path):
    value = ps((), '$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(' +
               q(path) + ',[ref]$t,[ref]$e);@{errors=$e.Count;functions=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)).Count}|ConvertTo-Json')
    assert value['errors'] == 0 and value['functions'] > 0


def test_only_intended_held_and_observer_functions_change():
    for old, new, expected_changed in (
        (W / 'install-held-supervisor-observation-r3-v2.ps1', HELD, {'Assert-HeldTaskDefinition'}),
        (W / 'install-resource-observer-r3-v4.ps1', OBSERVER,
         {'Assert-CommissionedTaskRunning', 'Assert-ObserverTask'}),
    ):
        value = ps((), r'''
function FunctionMap([string]$Path){
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e);if($e.Count){throw 'parse'}
 $map=@{};foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$map[$f.Name]=$f.Extent.Text};return ,$map
}
''' + '$a=FunctionMap ' + q(old) + ';$b=FunctionMap ' + q(new) + r'''
@{old_names=@($a.Keys|Sort-Object);new_names=@($b.Keys|Sort-Object);changed=@($a.Keys|Where-Object{$a[$_] -cne $b[$_]}|Sort-Object)}|ConvertTo-Json
''')
        assert value['old_names'] == value['new_names']
        assert set(value['changed']) == expected_changed


@pytest.mark.parametrize('path,importer', [(OBSERVER, 'Import-ObserverFunctions'), (BATCH, 'Import-SetupFunctions')])
def test_actual_pinned_acl_import_holds_source_and_refuses_bad_pin(path, importer):
    body = '$held=[Collections.Generic.List[IO.FileStream]]::new();$path=' + q(ACL) + '\n'
    body += 'try{$definitions=@(' + importer + ' $path ' + q(ACL_SHA) + " @('Assert-RegisteredTaskAcl'));"
    body += r'''
foreach($d in $definitions){. ([scriptblock]::Create($d))}
$locked=$false;try{$writer=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$writer.Dispose()}catch [IO.IOException]{$locked=$true}
$refused=$false;try{$null=''' + importer + r''' $path ('0'*64) @('Assert-RegisteredTaskAcl')}catch{$refused=$true}
Assert-RegisteredTaskAcl 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)' 'CoChem-4.2.7-Fixture'
@{definitions=$definitions.Count;held=$held.Count;locked=$locked;drift_refused=$refused}|ConvertTo-Json
}finally{foreach($s in $held){$s.Dispose()}}
'''
    assert ps((path,), body) == {'definitions': 1, 'held': 1, 'locked': True, 'drift_refused': True}


@pytest.mark.parametrize('consumer', ['held', 'observer', 'warden', 'batch'])
@pytest.mark.parametrize('acl_refuses', [False, True])
def test_each_real_acl_call_passes_exact_task_name_and_propagates_refusal(consumer, acl_refuses):
    names = {'held': 'CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1',
             'observer': 'CoChem-4.2.7-ResourceObservation-20261008-r3-v4',
             'warden': 'CoChem-4.2.7-Warden', 'batch': 'CoChem-4.2.7-StageIndependentSupervisor-20261007-v1'}
    source = HELD if consumer == 'held' else BATCH if consumer == 'batch' else OBSERVER
    body = '$expectedName=' + q(names[consumer]) + ';$script:aclRefuses=$' + str(acl_refuses).lower() + '\n'
    body += r'''
$script:seenName=$null;$script:aclCalls=0
function Assert-RegisteredTaskAcl {param([string]$Sddl,[string]$TaskName)
 $script:aclCalls++;$script:seenName=$TaskName
 if($Sddl -cne 'fixture-sddl' -or $TaskName -cne $expectedName){throw 'Unbound task name'}
 if($script:aclRefuses){throw 'Inert ACL rejection'}
}
$python='C:\fixture\python.exe';$targetRoot='C:\fixture\target';$packageRoot=$targetRoot;$installRoot='C:\fixture\r3'
$action=[pscustomobject]@{Type=0;Path=$python;Arguments='fixed';WorkingDirectory=$targetRoot}
$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($n)$script:action}
$triggers=[pscustomobject]@{Count=0}
$d=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='PT49H'};Actions=$actions;Triggers=$triggers}
$task=[pscustomobject]@{Name=$expectedName;Definition=$d;Enabled=$false;State=1;LastTaskResult=267011}
$task|Add-Member ScriptMethod GetSecurityDescriptor {param($n)if($n -ne 7){throw 'Wrong security query'};'fixture-sddl'}
$task|Add-Member ScriptMethod GetInstances {param($n)$instances=[pscustomobject]@{Count=$(if($script:task.State -eq 4){1}else{0})};$instances|Add-Member ScriptMethod Item {param($n)[pscustomobject]@{InstanceGuid='{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}'}};$instances}
function Get-RegistrationTask {param($Folder,$Name)if($Name -cne $expectedName){throw 'Unexpected task lookup'};$script:task}
'''
    if consumer == 'held':
        body += "$d.Settings.ExecutionTimeLimit='PT6M';$call={Assert-HeldTaskDefinition $task 'fixed' $false}\n"
    elif consumer == 'observer':
        body += "$call={Assert-ObserverTask $task 'fixed'}\n"
    elif consumer == 'warden':
        body += r'''
$task.Enabled=$true;$task.State=4;$d.Settings.Enabled=$true;$d.Settings.ExecutionTimeLimit='PT0S'
$action.Path=Join-Path $installRoot '.venv\Scripts\python.exe';$action.WorkingDirectory=$installRoot
$action.Arguments='-I -B -m cochem_pipeline daemon --config "C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\pipeline.json" --queue-launch-output "C:\ProgramData\CoChemPipeline427\private\queue-commissioning-20261007-r3-v1"'
$proof=[pscustomobject]@{Value=[pscustomobject]@{task_instance_guid='{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}'}}
$call={Assert-CommissionedTaskRunning ([pscustomobject]@{}) $proof}
'''
    else:
        body += "$task.State=3;$task.LastTaskResult=0;$folder=[pscustomobject]@{};$call={Assert-SetupCompletedTask $expectedName $python 'fixed' $targetRoot 'PT49H'}\n"
    body += '$refused=$false;try{& $call}catch{$refused=$true};@{refused=$refused;calls=$aclCalls;name=$seenName}|ConvertTo-Json'
    assert ps((source,), body) == {'refused': acl_refuses, 'calls': 1, 'name': names[consumer]}


@pytest.mark.parametrize('staged', [False, True])
def test_batch_phase_graph_reattests_original_first_start_and_never_authenticates(staged):
    body = '$staged=$' + str(staged).lower() + '\n' + r'''
$calls=[Collections.Generic.List[string]]::new();$proofs=[Collections.Generic.List[string]]::new()
Invoke-SetupPhases @(Get-SetupDisposition commissioned $staged) {param($n)$calls.Add($n);[int]0} {param($n)$proofs.Add($n);@{phase=$n}} {} {}
@{calls=@($calls.ToArray());proofs=@($proofs.ToArray());specs=@(Get-SetupPhaseSpecifications|Select-Object name,file,hash,plan_schema)}|ConvertTo-Json -Depth 4
'''
    value = ps((BATCH,), body)
    assert value['calls'] == ([] if staged else ['independent_staging']) + ['held_observation', 'resource_observation']
    assert value['proofs'] == ['independent_staging', 'first_start', 'held_observation', 'resource_observation']
    first = next(row for row in value['specs'] if row['name'] == 'first_start')
    assert first == {'name': 'first_start', 'file': RECOVERY.name, 'hash': RECOVERY_SHA,
                     'plan_schema': 'cochem-pending-warden-recovery-plan/1'}


@pytest.mark.parametrize('failure', ['', 'independent_staging', 'first_start_verify', 'held_observation', 'resource_observation'])
def test_batch_failure_preserves_existing_phase_and_never_calls_later_action(tmp_path, failure):
    body = '$fixture=' + q(tmp_path) + ';$failure=' + q(failure) + '\n' + r'''
$calls=[Collections.Generic.List[string]]::new();$events=[Collections.Generic.List[string]]::new();$refused=$false
try{Invoke-SetupPhases @(Get-SetupDisposition commissioned $false) {param($n)$calls.Add($n);if($n -ceq $failure){[int]2}else{[int]0}} {
 param($n)if($failure -ceq 'first_start_verify' -and $n -ceq 'first_start'){throw 'Inert stale first-start proof'};@{phase=$n}
} {param($n,$s,$p)$events.Add($n+':'+$s);$path=Join-Path $fixture ($n+'-'+$s+'.json');$f=[IO.File]::Open($path,[IO.FileMode]::CreateNew);try{$f.WriteByte(1)}finally{$f.Dispose()}} {}}catch{$refused=$true}
@{refused=$refused;calls=@($calls.ToArray());events=@($events.ToArray())}|ConvertTo-Json
'''
    value = ps((BATCH,), body)
    expected = ['independent_staging', 'held_observation', 'resource_observation']
    if failure == 'first_start_verify':
        expected = expected[:1]
    elif failure:
        expected = expected[:expected.index(failure) + 1]
    assert value['refused'] == bool(failure) and value['calls'] == expected
    assert 'first_start:STARTED' not in value['events']


def test_injected_first_start_execute_is_refused_before_witness_or_any_action():
    value = ps((BATCH,), r'''
$calls=0;$witnesses=0;$refused=$false
try{Invoke-SetupPhases @([pscustomobject]@{phase='first_start';mode='execute'}) {$script:calls++;[int]0} {$script:calls++} {$script:calls++} {$script:witnesses++}}catch{$refused=$_.Exception.Message -ceq 'SETUP_FIRST_START_REPLAY_FORBIDDEN'}
@{refused=$refused;calls=$calls;witnesses=$witnesses}|ConvertTo-Json
''')
    assert value == {'refused': True, 'calls': 0, 'witnesses': 0}


def test_original_python5_observer_payload_manifest_and_host_paths_are_retained():
    old = (W / 'install-resource-observer-r3-v4.ps1').read_text(encoding='utf-8-sig')
    new = OBSERVER.read_text(encoding='utf-8-sig')
    for assignment in ('$sourceRoot=', '$sourceManifestHash=', '$targetRoot=', '$stateRoot=', '$taskName=', '$dependencyHash='):
        assert next(line for line in old.splitlines() if assignment in line) == next(line for line in new.splitlines() if assignment in line)
    manifest = W / 'resource-observer-r3-v4/source-manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == '88d4248e1a2dcdc8c460fd1b325a87cc7925263f4ba7d101e5f73f0043287b49'
    python5 = hashlib.sha256((W / 'commission-first-warden-r3-v5.py').read_bytes()).hexdigest()
    assert python5 == '8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e'
    assert python5 in new and python5 in (W / 'resource-observer-r3-v4/cochem_supervisor/resource_observation.py').read_text()
    assert "(Join-Path $script:commissionRoot 'commission-first-warden-r3-v1.py')" in HELD.read_text()


@pytest.mark.parametrize('accepted', [True, False])
def test_real_observer_commissioning_reader_keeps_python5_binding(tmp_path, accepted):
    receipt = O.commissioning_receipt()
    receipt['helper_sha256'] = '8ae682483caff2c178ab096b481699a5fea12c62519c5002bb75cbedad9e8c9e' if accepted else 'f' * 64
    value = observer_ps(O.commissioning_gate_fixture(tmp_path, receipt) + r'''
$refused=$false;$proof=$null;try{$proof=Read-ObserverCommissioning}catch{$refused=$true}
@{refused=$refused;pid=$(if($null -ne $proof){$proof.Value.controller.pid}else{0})}|ConvertTo-Json
''')
    assert value == {'refused': not accepted, 'pid': 1234 if accepted else 0}


# Reuse only directly relevant substantive baseline fixtures against new code.
test_observer_disabled_contract = O.test_exact_disabled_never_run_task_contract
test_observer_running_contract = O.test_running_single_instance_contract
test_observer_real_registration = O.test_real_control_copy_and_full_disabled_registration_flow
test_observer_collisions = O.test_existing_namespaces_refuse_before_any_copy_or_registration
test_observer_binding_blocks_mutation = O.test_binding_failure_prevents_registration_and_new_roots
test_held_apply_order_and_preservation = H.test_assembled_apply_order_and_partial_preservation
test_launcher_source_reader_custody = L.test_real_source_reader_holds_exact_bytes_denies_writers_and_closes_on_refusal
test_launcher_console_inheritance = L.test_actual_console_invoker_inherits_console_waits_disposes_and_refuses_delimiters
test_launcher_graph_and_pins = L.test_exact_frozen_source_pins_and_no_redirected_or_background_child_path


@pytest.mark.parametrize('first,second,expected', [
    ('0', '0', ['pending_registration_recovery', 'monitoring_setup']),
    ('1', '0', ['pending_registration_recovery']),
    ('20', '0', ['pending_registration_recovery']),
    ('0', '1', ['pending_registration_recovery', 'monitoring_setup']),
    ('0', '20', ['pending_registration_recovery', 'monitoring_setup']),
    ("'0'", '0', ['pending_registration_recovery']),
    ('[double]0', '0', ['pending_registration_recovery']),
])
def test_launcher_actual_tail_two_children_once_no_login_pause_or_retry(tmp_path, first, second, expected):
    # Reuse exact frozen launcher tail/callback and disposable source preflight;
    # replace only its native child boundary with recorded integer outcomes.
    for _, name, _ in CHILDREN:
        (tmp_path / name).write_bytes((W / name).read_bytes())
    source = LAUNCHER.read_text()
    body = L.physical_fixture_header() + '\n'
    body += '$Apply=$true;$firstCode=' + first + ';$secondCode=' + second + ';$callsPath=' + q(tmp_path / 'calls.jsonl') + ';$hostPath=' + q(tmp_path / 'metadata.txt') + '\n'
    body += source[source.index('$specs=@('):source.index('\n$held=')]
    body += r'''
function Write-Host{[CmdletBinding()]param([Parameter(ValueFromPipeline=$true)][string]$Object)process{[IO.File]::AppendAllText($hostPath,$Object+[Environment]::NewLine)}}
function Invoke-RebootConsoleChild {param([string[]]$Arguments)
 $name=[IO.Path]::GetFileName($Arguments[3]);$row=@($specs|Where-Object{$_.file -ceq $name});if($row.Count -ne 1){throw 'Unexpected child'}
 [IO.File]::AppendAllText($callsPath,((@{phase=$row[0].phase;args=@($Arguments)}|ConvertTo-Json -Compress)+[Environment]::NewLine))
 if($row[0].phase -ceq 'pending_registration_recovery'){return $firstCode};return $secondCode
}
'''
    body += source[source.index('\n$held=') + 1:]
    result = L.run_fixture(tmp_path / 'tail.ps1', body)
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert [row['phase'] for row in calls] == expected
    assert len(calls) == len(set(row['phase'] for row in calls))
    assert result.returncode == (0 if first == second == '0' else 1)
    for row in calls:
        name = next(name for phase, name, _ in CHILDREN if phase == row['phase'])
        assert row['args'] == ['-NoLogo', '-NoProfile', '-File', str(tmp_path / name), '-Apply', '-Interactive']
    for _, name, pin in CHILDREN:
        path = tmp_path / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pin
        with path.open('r+b'):
            pass
    if result.returncode == 0:
        value = json.loads(result.stdout)
        assert value['login_commands_executed'] == 0 and value['automatic_retry'] is False
    else:
        value = json.loads(next(line for line in (tmp_path / 'metadata.txt').read_text().splitlines() if line.startswith('{')))
        assert value['phase'] == expected[-1] and value['automatic_retry'] is False


@pytest.mark.parametrize('changed', ['pending_registration_recovery', 'monitoring_setup'])
def test_launcher_changed_child_source_refuses_both_before_any_child(tmp_path, changed):
    result, calls, host = L.actual_tail_fixture(tmp_path, changed=changed)
    assert result.returncode == 1 and calls == []
    value = json.loads(next(line for line in host.splitlines() if line.startswith('{')))
    assert value['phase'] == 'source_preflight'


def test_one_actual_launcher_preview_starts_no_children_and_saves_new_evidence():
    assert hashlib.sha256(LAUNCHER.read_bytes()).hexdigest() == '136cb5ddb505fcde6ac7e7494b42ce299e6258bcc6b7c0ec04e912f09d8c8d9c'
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(LAUNCHER)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value['mode'] == 'READ_ONLY_PLAN' and value['source_custody_verified'] is True
    assert value['child_processes_started'] == value['tasks_changed'] == value['model_jobs_submitted'] == 0
    assert value['full_srs_acceptance'] is False and value['automatic_retry'] is False
    assert [(row['phase'], row['file'], row['sha256']) for row in value['phases']] == list(CHILDREN)
    with (W / 'pending-warden-launcher-v7-preview-final.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
