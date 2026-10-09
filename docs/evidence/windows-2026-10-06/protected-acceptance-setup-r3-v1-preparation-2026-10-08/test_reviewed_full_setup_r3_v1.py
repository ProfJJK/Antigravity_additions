"""Actual WinPS5.1 functions; temporary writes and simulated phases only."""
from pathlib import Path
import base64
import json
import subprocess

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / 'run-reviewed-full-setup-r3-v1.ps1'
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
    ('fresh', False, ['execute'] * 5),
    ('fresh', True, ['execute', 'reuse_verified_receipt', 'execute', 'execute', 'execute']),
    ('commissioned', False, ['deferred_maintenance', 'execute', 'reattest_current_instance', 'execute', 'execute']),
    ('commissioned', True, ['deferred_maintenance', 'reuse_verified_receipt', 'reattest_current_instance', 'execute', 'execute']),
])
def test_only_fresh_commissioning_can_launch_old_series(branch, staged, expected):
    value = run_ps(f'@(Get-SetupDisposition {q(branch)} ${str(staged).lower()})|ConvertTo-Json')
    assert [v['mode'] for v in value] == expected


@pytest.mark.parametrize('failure', ['', 'oracle_maintenance', 'independent_staging', 'first_start', 'held_observation', 'resource_observation'])
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
    phases = ['oracle_maintenance', 'independent_staging', 'first_start', 'held_observation', 'resource_observation']
    count = phases.index(failure) + 1 if failure else 5
    assert result['caught'] == bool(failure)
    assert result['invoked'] == phases[:count]
    assert len(result['files']) == count * 2 - bool(failure)
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
    assert len(value['holds']) == (0 if deferred else 1)


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
$w=[CoChemFullSetupWitness]::new([uint32]$p.Id,$created,$image,$sid)
try{$w.Verify()}finally{$w.Dispose()}
try{$w.Verify()}catch{$bad.Add('closed')}
foreach($row in @(@{c=$created+1;i=$image;s=$sid;n='creation'},@{c=$created;i='C:\wrong.exe';s=$sid;n='image'},@{c=$created;i=$image;s='S-1-5-18';n='sid'})){
 try{$x=[CoChemFullSetupWitness]::new([uint32]$p.Id,[long]$row.c,$row.i,$row.s);$x.Dispose()}catch{$bad.Add($row.n)}
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


def test_current_draft_is_explicitly_nonoperational_until_peer_wrapper_frozen():
    result = run_ps('$v=@(Get-SetupPhaseSpecifications);@{missing=@($v|Where-Object{$_.hash -cnotmatch "^[a-f0-9]{64}$"}|ForEach-Object name)}|ConvertTo-Json')
    assert result['missing'] == ['held_observation']
    assert 'operationally_released=$false' in SCRIPT.read_text(encoding='utf-8-sig')


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
