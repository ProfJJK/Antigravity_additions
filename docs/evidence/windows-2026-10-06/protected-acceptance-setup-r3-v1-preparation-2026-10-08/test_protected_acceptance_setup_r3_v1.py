"""Actual WinPS5.1 functions; temporary writes and simulated phases only."""
from pathlib import Path
import base64
import json
import subprocess

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / 'run-protected-acceptance-setup-r3-v1.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(body):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
function Definitions([string]$p){$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($p,[ref]$t,[ref]$e);if($e.Count){throw 'parse'};foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$f.Extent.Text.Replace('$PSScriptRoot','$script:fixtureScriptRoot')}}
'''
    for path in (HERE / 'register-stopped-warden-r3.ps1', SCRIPT):
        prefix += f'foreach($f in Definitions {q(path)}){{. ([scriptblock]::Create($f))}}\n'
    prefix += '$script:fixtureScriptRoot=' + q(HERE) + '\n'
    encoded = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


@pytest.mark.parametrize('branch,staged,expected', [
    ('fresh', False, ['deferred_maintenance'] + ['execute'] * 4),
    ('fresh', True, ['deferred_maintenance', 'reuse_verified_receipt', 'execute', 'execute', 'execute']),
    ('commissioned', False, ['deferred_maintenance', 'execute', 'reattest_current_instance', 'execute', 'execute']),
    ('commissioned', True, ['deferred_maintenance', 'reuse_verified_receipt', 'reattest_current_instance', 'execute', 'execute']),
])
def test_only_fresh_commissioning_can_launch_old_series(branch, staged, expected):
    value = run_ps(f'@(Get-SetupDisposition {q(branch)} ${str(staged).lower()})|ConvertTo-Json')
    assert [v['mode'] for v in value] == expected


@pytest.mark.parametrize('failure', ['', 'independent_staging', 'first_start', 'held_observation', 'resource_observation'])
def test_real_phase_flow_stops_and_preserves_first_failed_intent(tmp_path, failure):
    result = run_ps(f'$root={q(tmp_path)};$fail={q(failure)}\n' + r'''
$events=[Collections.Generic.List[string]]::new();$invoked=[Collections.Generic.List[string]]::new();$checks=0
$caught=$false
try{Invoke-SetupPhases @(Get-SetupDisposition fresh $false) {
 param($name);$invoked.Add($name);if($name -ceq $fail){return [int]2};return [int]0
} {param($name)[pscustomobject]@{phase=$name;receipt_sha256=('a'*64)}} {
 param($name,$state,$proof);$events.Add($name+':'+$state)
 $p=Join-Path $root ($name+'-'+$state+'.json');$f=[IO.File]::Open($p,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$raw=[Text.Encoding]::UTF8.GetBytes((@{phase=$name;state=$state;proof=$proof}|ConvertTo-Json));$f.Write($raw,0,$raw.Length)}finally{$f.Dispose()}
} {$script:checks++}}catch{$caught=$true}
@{caught=$caught;events=@($events.ToArray());invoked=@($invoked.ToArray());checks=$checks;files=@(Get-ChildItem -LiteralPath $root -File|ForEach-Object Name)}|ConvertTo-Json -Depth 5
''')
    phases = ['independent_staging', 'first_start', 'held_observation', 'resource_observation']
    count = phases.index(failure) + 1 if failure else 4
    assert result['caught'] == bool(failure)
    assert result['invoked'] == phases[:count]
    assert len(result['files']) == 1 + count * 2 - bool(failure)
    assert result['checks'] == count * 2 - bool(failure)
    if failure:
        assert result['events'][-1] == failure + ':STARTED'


def test_running_branch_never_invokes_oracle_or_first_start():
    result = run_ps(r'''
$calls=[Collections.Generic.List[string]]::new();$events=[Collections.Generic.List[string]]::new();$proofs=[Collections.Generic.List[string]]::new()
Invoke-SetupPhases @(Get-SetupDisposition commissioned $true) {param($n)$calls.Add($n);[int]0} {param($n)$proofs.Add($n);@{phase=$n}} {param($n,$s,$p)$events.Add($n+':'+$s)} {}
@{calls=@($calls.ToArray());proofs=@($proofs.ToArray());events=@($events.ToArray())}|ConvertTo-Json
''')
    assert result['calls'] == ['held_observation', 'resource_observation']
    assert result['proofs'] == ['independent_staging', 'first_start', 'held_observation', 'resource_observation']
    assert result['events'][0] == 'oracle_maintenance:DEFERRED_MAINTENANCE'


@pytest.mark.parametrize('failure', ['before', 'after', 'verify'])
def test_controller_identity_or_proof_failure_prevents_later_phase(failure):
    value = run_ps(f'$fail={q(failure)}\n'+r'''
$calls=0;$checks=0;$events=[Collections.Generic.List[string]]::new();$caught=$false
try{Invoke-SetupPhases @(Get-SetupDisposition commissioned $false) {param($n)$script:calls++;[int]0} {param($n)if($fail -ceq 'verify'){throw 'proof'};@{}} {param($n,$s,$p)$events.Add($n+':'+$s)} {
 $script:checks++;if(($fail -ceq 'before' -and $checks -eq 1) -or ($fail -ceq 'after' -and $checks -eq 2)){throw 'identity'}
}}catch{$caught=$true};@{caught=$caught;calls=$calls;events=@($events.ToArray())}|ConvertTo-Json
''')
    assert value['caught']
    assert value['calls'] == (0 if failure == 'before' else 1)
    assert not any(event.endswith(':VERIFIED') for event in value['events'])


@pytest.mark.parametrize('branch,present,hold,deferred', [
    ('fresh', False, 'Successful first-instance commissioning receipt is required.', True),
    ('commissioned', False, 'Successful first-instance commissioning receipt is required.', False),
    ('fresh', True, 'Successful first-instance commissioning receipt is required.', False),
    ('fresh', False, 'Exact observer task absence is not proved.', False),
    ('fresh', 'false', 'Successful first-instance commissioning receipt is required.', False),
])
def test_only_exact_dependency_gap_is_deferred(branch, present, hold, deferred):
    literal = q(present) if isinstance(present, str) else '$' + str(present).lower()
    value = run_ps(f'$v=[pscustomobject]@{{commissioning_present={literal};holds=@({q(hold)})}};@{{holds=@(Get-SetupEffectiveHolds resource_observation $v {q(branch)})}}|ConvertTo-Json -Depth 4')
    assert (len(value['holds']) == 0) == deferred


def test_source_function_hash_is_held_and_mismatch_refused(tmp_path):
    source = tmp_path / 'source.ps1'
    source.write_text('function Fixture { 42 }\n', encoding='utf-8')
    import hashlib
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    value = run_ps(f'$path={q(source)};$pin={q(pin)}\n'+r'''
$held=[Collections.Generic.List[IO.FileStream]]::new();$blocked=$false;$wrong=$false
try{
 foreach($d in Import-SetupFunctions $path $pin @('Fixture')){. ([scriptblock]::Create($d))}
 try{[IO.File]::WriteAllText($path,'changed')}catch{$blocked=$true}
 try{Import-SetupFunctions $path ('a'*64) @('Fixture')|Out-Null}catch{$wrong=$true}
 @{result=(Fixture);write_blocked=$blocked;wrong_pin_refused=$wrong;held=$held.Count}|ConvertTo-Json
}finally{foreach($s in $held){$s.Dispose()}}
''')
    assert value == {'result': 42, 'write_blocked': True, 'wrong_pin_refused': True, 'held': 1}


def test_native_own_process_identity_creation_image_sid_and_closed_handle():
    value = run_ps(r'''
Initialize-SetupNativeWitness
$p=[Diagnostics.Process]::GetCurrentProcess();$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$created=$p.StartTime.ToFileTimeUtc();$image=$p.MainModule.FileName;$bad=[Collections.Generic.List[string]]::new()
$w=[CoChemProtectedSetupWitness]::new([uint32]$p.Id,$created,$image,$sid)
try{$w.Verify()}finally{$w.Dispose()}
try{$w.Verify()}catch{$bad.Add('closed')}
foreach($row in @(@{c=$created+1;i=$image;s=$sid;n='creation'},@{c=$created;i='C:\wrong.exe';s=$sid;n='image'},@{c=$created;i=$image;s='S-1-5-18';n='sid'})){
 try{$x=[CoChemProtectedSetupWitness]::new([uint32]$p.Id,[long]$row.c,$row.i,$row.s);$x.Dispose()}catch{$bad.Add($row.n)}
}
@{passed=$true;refused=@($bad.ToArray());fixture_scope='ordinary_current_process_only'}|ConvertTo-Json
''')
    assert value['passed'] and value['refused'] == ['closed', 'creation', 'image', 'sid']


def test_private_denial_is_not_absence_and_reparse_is_not_regular(tmp_path):
    value = run_ps(f'$p={q(tmp_path / "missing")}\n'+r'''
$missing=Get-SetupPathState $p
function Get-Item {param($LiteralPath,[switch]$Force,$ErrorAction)throw [UnauthorizedAccessException]::new('fixture')}
$denied=$false;try{Get-SetupPathState $p|Out-Null}catch{$denied=$true}
function Get-Item {param($LiteralPath,[switch]$Force,$ErrorAction)[pscustomobject]@{Attributes=[IO.FileAttributes]::ReparsePoint}}
$reparse=$false;try{Get-SetupPathState $p|Out-Null}catch{$reparse=$true}
@{missing=$missing;denied=$denied;reparse=$reparse}|ConvertTo-Json
''')
    assert value == {'missing': 'absent', 'denied': True, 'reparse': True}


def test_public_failure_has_fixed_fields_create_new_and_no_raw_detail(tmp_path):
    value = run_ps(f'$seriesRoot={q(tmp_path)}\n'+r'''
# Actual CreateNew writer semantics; ACL authority is explicitly outside this fixture.
function Write-RegistrationControl {param($Path,$Text)
 $f=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$b=[Text.Encoding]::UTF8.GetBytes($Text);$f.Write($b,0,$b.Length)}finally{$f.Dispose()}
 (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$r=Write-SetupFailure 'untrusted phase' ('a'*32) @('first_start') 'raw secret message';$first=[IO.File]::ReadAllText($r.receipt_path)
$collision=$false;try{Write-SetupFailure preflight ('a'*32) @()|Out-Null}catch{$collision=$true}
@{result=$r;first=($first|ConvertFrom-Json);unchanged=([IO.File]::ReadAllText($r.receipt_path) -ceq $first);collision=$collision}|ConvertTo-Json -Depth 6
''')
    assert value['collision'] and value['unchanged']
    assert value['first']['phase'] == 'preflight'
    assert value['first']['code'] == 'SETUP_PHASE_OR_EVIDENCE_NOT_VERIFIED'
    assert 'raw secret' not in json.dumps(value)


def test_phase_artifacts_match_the_final_frozen_dependency_pins():
    import hashlib
    result = run_ps('@(Get-SetupPhaseSpecifications)|ConvertTo-Json -Depth 5')
    for spec in result:
        assert len(spec['hash']) == 64
        assert hashlib.sha256((HERE / spec['file']).read_bytes()).hexdigest() == spec['hash']
    assert 'UNFROZEN' not in SCRIPT.read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('mutation', ['', '$task.State=4', '$task.State=0', '$task.Instances=1', '$task.LastTaskResult=2',
    '$action.Arguments="different"', '$d.Principal.UserId="ansac"', '$d.Triggers.Count=1', '$d.Settings.RestartCount=1'])
def test_completed_staging_reuse_requires_exact_terminal_task(mutation):
    value = run_ps(r'''
$action=[pscustomobject]@{Type=0;Path='C:\fixed\python.exe';Arguments='fixed';WorkingDirectory='C:\fixed'}
$actions=[pscustomobject]@{Count=1;Value=$action};$actions|Add-Member ScriptMethod Item {param($i)$this.Value}
$d=[pscustomobject]@{Actions=$actions;Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Settings=[pscustomobject]@{MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='PT3M'}}
$task=[pscustomobject]@{Definition=$d;State=3;Instances=0;LastTaskResult=0}
$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$this.Instances}}
$task|Add-Member ScriptMethod GetSecurityDescriptor {param($n)'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'}
$folder=$null
function Get-RegistrationTask {param($f,$n)$script:task}
''' + mutation + r'''
$refused=$false;try{Assert-SetupCompletedTask 'fixture' 'C:\fixed\python.exe' 'fixed' 'C:\fixed' 'PT3M'}catch{$refused=$true}
@{refused=$refused}|ConvertTo-Json
''')
    assert value['refused'] == bool(mutation)


@pytest.mark.parametrize('mutation,branch,staged,valid', [
    ('', 'fresh', False, True),
    ('', 'commissioned', False, False),
    ('', 'fresh', True, False),
    ('$v.holds+=@("SOURCE_NOT_FROZEN")', 'fresh', False, False),
    ('$v.holds+=@("STAGING_NOT_INSTALLED")', 'fresh', False, False),
    ('$v.holds=@("STAGING_NOT_INSTALLED")', 'fresh', False, False),
    ('$v.commissioning_present="false"', 'fresh', False, False),
])
def test_held_dependencies_do_not_mask_unrelated_or_ambiguous_holds(mutation, branch, staged, valid):
    result = run_ps(r'''
$v=[pscustomobject]@{holds=@('STAGING_NOT_INSTALLED','COMMISSIONING_NOT_COMPLETE');staging_present=$false;commissioning_present=$false}
''' + mutation + f'\n@{{holds=@(Get-SetupEffectiveHolds held_observation $v {q(branch)} ${str(staged).lower()})}}|ConvertTo-Json -Depth 5')
    assert (len(result['holds']) == 0) == valid


@pytest.mark.parametrize('mutation', ['', '$v.paid_repair_enabled=$true', '$v.component_recovery_enabled=$true',
    '$v.warden_stop_required=$true', '$v.oracle_acceptance_prerequisite=$true', '$v.commissioning_present="false"'])
def test_held_plan_remains_observation_only(mutation):
    value = run_ps(r'''
$s=@(Get-SetupPhaseSpecifications|Where-Object name -eq held_observation)[0]
$v=[pscustomobject]@{schema=$s.plan_schema;mode='READ_ONLY_PLAN';holds=@('STAGING_NOT_INSTALLED','COMMISSIONING_NOT_COMPLETE');staging_present=$false;commissioning_present=$false;paid_repair_enabled=$false;component_recovery_enabled=$false;warden_stop_required=$false;oracle_acceptance_prerequisite=$false;target_root='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1';data_root='C:\ProgramData\CoChemSupervisor427-observation-20261007-r3-v1'}
''' + mutation + r'''
$refused=$false;try{Assert-SetupPlan $s $v|Out-Null}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert value['refused'] == bool(mutation)


def test_atomic_fresh_root_refuses_existing_without_acl_or_content_changes(tmp_path):
    value = run_ps(f'$path={q(tmp_path / "series")}\n' + r'''
Initialize-SetupNativeWitness;$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$sddl='O:'+ $sid +'D:P(A;OICI;FA;;;'+$sid+')'
[CoChemProtectedSetupWitness]::CreateFreshRoot($path,$sddl)
$acl=(Get-Acl -LiteralPath $path).Sddl;[IO.File]::WriteAllText((Join-Path $path 'marker'),'preserve')
$refused=$false;try{[CoChemProtectedSetupWitness]::CreateFreshRoot($path,$sddl)}catch{$refused=$true}
@{refused=$refused;acl_unchanged=((Get-Acl -LiteralPath $path).Sddl -ceq $acl);marker=[IO.File]::ReadAllText((Join-Path $path 'marker'));scope='ordinary disposable directory'}|ConvertTo-Json
''')
    assert value['refused'] and value['acl_unchanged'] and value['marker'] == 'preserve'


@pytest.mark.parametrize('mutation', ['', '$v.commissioning_receipt_sha256=("d"*64)', '$v.paid_repair_enabled=$true',
    '$v.component_recovery_enabled="false"', '$pv.helper_sha256=("e"*64)', '$pv.existing_warden_changed=$true',
    '$v.native_process.system_sid="S-1-5-21-1"', '$v.native_process.pid="123"', '$pv.observation_verified="true"'])
def test_held_receipt_and_retained_provision_bytes_bind_observation_only(tmp_path, mutation):
    value = run_ps(f'$fixture={q(tmp_path)}\n'+r'''
$stagingRoot='C:\fixed\stage';$heldObservationHelperHash=('a'*64);$commission=[pscustomobject]@{Sha256=('c'*64)}
$s=@(Get-SetupPhaseSpecifications|Where-Object name -eq held_observation)[0]
$map=@{};$map[$s.receipt]=Join-Path $fixture 'activation.json';$map[(Join-Path $stagingRoot 'controls\staging-receipt.json')]=Join-Path $fixture 'stage.json'
[IO.File]::WriteAllText($map[(Join-Path $stagingRoot 'controls\staging-receipt.json')],'{}')
$stagePin=(Get-FileHash -LiteralPath $map[(Join-Path $stagingRoot 'controls\staging-receipt.json')]).Hash.ToLowerInvariant()
$root='C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1';$map[(Join-Path $root 'provisioning.json')]=Join-Path $fixture 'provision.json'
$pv=[pscustomobject]@{schema='cochem-held-supervisor-provision/1';status='HELD_OBSERVATION_PROVISIONED';root=$root;helper_sha256=$heldObservationHelperHash;staging_receipt_sha256=$stagePin;commissioning_receipt_sha256=$commission.Sha256;observation_verified=$true;paid_repair_enabled=$false;component_recovery_enabled=$false;existing_warden_changed=$false;full_srs_acceptance=$false}
$v=[pscustomobject]@{schema=$s.receipt_schema;status=$s.status;root=$root;staging_receipt_sha256=$stagePin;commissioning_receipt_sha256=$commission.Sha256;provisioning_receipt_sha256=('b'*64);runtime_receipt_sha256=('d'*64);paid_repair_enabled=$false;component_recovery_enabled=$false;existing_warden_changed=$false;full_srs_acceptance=$false;partial_outputs_preserved=$true;native_process=[pscustomobject]@{pid=123;system_sid='S-1-5-18';image_sha256='d8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa'}}
''' + mutation + r'''
[IO.File]::WriteAllText($map[(Join-Path $root 'provisioning.json')],($pv|ConvertTo-Json -Depth 6))
$v.provisioning_receipt_sha256=(Get-FileHash -LiteralPath $map[(Join-Path $root 'provisioning.json')]).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($map[$s.receipt],($v|ConvertTo-Json -Depth 6))
# Actual bounded temporary-byte/hash read; substitutes only protected ACL/path fixture.
function Read-R3Control {param($Path,$Hash='',$Maximum=131072)
 $p=$map[$Path];$item=Get-Item -LiteralPath $p;if($item.Length -gt $Maximum){throw 'size'}
 $h=(Get-FileHash -LiteralPath $p).Hash.ToLowerInvariant();if($Hash -and $h -cne $Hash){throw 'hash'}
 [pscustomobject]@{Sha256=$h;Text=[IO.File]::ReadAllText($p)}
}
function Read-R3Text {param($Control)$Control.Text}
function Read-ObserverCommissioning {$script:commission}
$refused=$false;$result=$null;try{$result=Read-SetupReceipt $s}catch{$refused=$true}
@{refused=$refused;result=$result}|ConvertTo-Json -Depth 5
''')
    assert value['refused'] == bool(mutation)
    if not mutation:
        assert value['result']['status'] == 'HELD_SUPERVISOR_OBSERVATION_RUNNING'
        assert len(value['result']['receipt_sha256']) == 64


def test_frozen_oracle_postconditions_conflict_with_frozen_first_start(tmp_path, monkeypatch):
    """Real disposable byte lock; frozen first-start predicates, no production calls."""
    import importlib.util
    import hashlib
    from types import SimpleNamespace

    def module(filename, pin, name):
        path = HERE / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pin
        spec = importlib.util.spec_from_file_location(name, path)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value

    oracle = module('run-oracle-native-acceptance-r3-v1.py',
        '56e78536c2ce85e04d519b13a3347ea620bdf0ab105ed537a0cc9b4afedd9a0d', 'fixture_frozen_oracle')
    first = module('commission-first-warden-r3-v1.py',
        'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c', 'fixture_frozen_first_start')
    monkeypatch.syspath_prepend(str(first.INSTALL / '.venv/Lib/site-packages'))
    from cochem_pipeline import windows, ramdisk
    private = tmp_path / 'private'
    private.mkdir()
    win = SimpleNamespace(validate_private_directory=lambda p: p == private,
        validate_private_path=lambda p: p == private / 'warden.lock', _api=windows._api,
        _close=windows._close, _acl=lambda p: ('ordinary-fixture-owner', True, ()),
        WorkerIdentity=lambda **kwargs: SimpleNamespace(**kwargs),
        validate_layout=lambda *a, **k: {'slots': []}, validate_controller_token=lambda *a: None)
    with oracle.maintained_lock(private / 'warden.lock', win, expected_parent=private) as proof:
        assert proof['created_new']
    before = (private / 'warden.lock').read_bytes()
    config = SimpleNamespace(workers={}, private_root=private, slot_roots={}, token_file=tmp_path / 'unused', operator_name='fixture')
    with pytest.raises(ValueError, match='^EXISTING_FIRST_START_STATE$'):
        first.preflight_state(config, win, {}, {})
    assert (private / 'warden.lock').read_bytes() == before == b'0'

    # Separate fresh fixture: retain both exact namespaces, then execute the
    # frozen scratch names predicate through its actual preflight function.
    other = tmp_path / 'other-private';other.mkdir()
    slot = tmp_path / 'slot1';slot.mkdir()
    (slot / first.FIXTURE_NAME).mkdir()
    oracle_relative = oracle.SCRATCH.parts[-2:]
    (slot / oracle_relative[0] / oracle_relative[1]).mkdir(parents=True)
    class RamFixture:
        def __init__(self, *args): pass
        def inspect(self, **kwargs): pass
        def workspace(self, key): assert key == 'slot1';return SimpleNamespace(root=slot)
    monkeypatch.setattr(ramdisk, 'RamdiskManager', RamFixture)
    monkeypatch.setattr(first, 'QUEUE', tmp_path / 'no-queue')
    config = SimpleNamespace(workers={'slot1': {}}, private_root=other, slot_roots={},
        token_file=tmp_path / 'unused', operator_name='fixture', ramdisk=None)
    with pytest.raises(ValueError, match='^SLOT1_EXPECTED_PHYSICAL_FIXTURE$'):
        first.preflight_state(config, win, {}, {})
    assert (slot / oracle_relative[0] / oracle_relative[1]).is_dir()
