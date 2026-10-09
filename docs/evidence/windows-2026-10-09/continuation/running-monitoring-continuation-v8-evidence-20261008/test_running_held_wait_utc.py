"""Actual PS5 imported observer Wait with inert task/runtime/process seams.

Only ordinary source files are read. No Scheduler/native process handle,
provider, protected state, Apply, or production clock/timezone mutation.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
READER = W / 'resume-running-held-observer-r3-v1.ps1'
HELD = W / 'install-held-supervisor-observation-r3-v3.ps1'
PIN = 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6'
OLD = "([DateTime]::UtcNow-[DateTime]'1970-01-01T00:00:00Z').TotalSeconds"
NEW = '([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    prefix = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
'''
    encoded = base64.b64encode((prefix+body).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


def load_reader():
    return ('$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile('
            + q(READER) + r''',[ref]$t,[ref]$e);if($e.Count){throw $e[0]}
$reader=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Import-RunningHeldFunctions'},$true))
if($reader.Count -ne 1){throw 'Fixture reader definition missing'}
. ([scriptblock]::Create($reader[0].Extent.Text))
$script:held=[Collections.Generic.List[IDisposable]]::new()
''')


def test_actual_reader_changes_only_the_epoch_expression_and_holds_source():
    code = load_reader() + '$path=' + q(HELD) + ';$pin=' + q(PIN) + ';'
    code += r'''
try{
 $defs=@(Import-RunningHeldFunctions $path $pin @('Wait-HeldObservationRunning') -CorrectUtcWait)
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
 $original=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Wait-HeldObservationRunning'},$true))[0].Extent.Text
 $value=@{definition=$defs[0];original=$original;source_held=$script:held.Count;source_read_only=($script:held[0].CanRead -and -not $script:held[0].CanWrite)}
}finally{foreach($s in $script:held){$s.Dispose()}}
$value|ConvertTo-Json -Compress -Depth 4
'''
    value = run(code)
    assert value['definition'] == value['original'].replace(OLD, NEW)
    assert value['original'].count(OLD) == 1
    assert value['definition'].count(NEW) == 1
    assert OLD not in value['definition']
    assert value['source_held'] == 1 and value['source_read_only']


def fixture(*, corrected=True, age=0.0, fault=''):
    code = load_reader() + '$path=' + q(HELD) + ';$pin=' + q(PIN) + ';$fault=' + q(fault) + ';$age=' + str(age) + ';'
    if corrected:
        code += r'''$defs=@(Import-RunningHeldFunctions $path $pin @('Wait-HeldObservationRunning') -CorrectUtcWait)
. ([scriptblock]::Create($defs[0]))
'''
    else:
        code += r'''$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
$def=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Wait-HeldObservationRunning'},$true))[0].Extent.Text
. ([scriptblock]::Create($def))
'''
    code += r'''
Add-Type -TypeDefinition @'
using System;
public sealed class CoChemHeldObservationProcess : IDisposable {
 public static int Created, Live, Disposed;
 public static string ExpectedSid="S-1-5-18";
 public static bool FailLive=false;
 public string Sid {get{return ExpectedSid;}}
 public string Image {get{return @"C:\inert-fixture\python.exe";}}
 public long CreationFiletime {get{return 134359860348047610L;}}
 public CoChemHeldObservationProcess(int pid){if(pid!=1234)throw new Exception("Unexpected fixture pid");Created++;}
 public void AssertLive(){Live++;if(FailLive)throw new Exception("Inert witness not live");}
 public void Dispose(){Disposed++;}
}
'@
$script:events=[Collections.Generic.List[string]]::new()
$script:observerTask='inert-fixture-task';$script:targetRoot='C:\inert-fixture'
$script:sourceHash='a'*64;$script:basePython='C:\inert-fixture\python.exe'
$script:processWitnesses=[Collections.Generic.List[IDisposable]]::new()
$instanceGuid='{11111111-1111-1111-1111-111111111111}'
$provision=[pscustomobject]@{nonce='fixture-nonce';configuration_sha256='b'*64;inputs_sha256='c'*64}
$runtime=[pscustomobject]@{
 schema='cochem-held-supervisor-runtime/1';provision_nonce=$provision.nonce;configuration_sha256=$provision.configuration_sha256;inputs_sha256=$provision.inputs_sha256;
 helper_sha256=$script:sourceHash;service_identity='SYSTEM';instance_id='d'*32;paid_repair_enabled=$false;component_recovery_enabled=$false;warden_actuation_enabled=$false;full_srs_acceptance=$false;
 status='HELD_SUPERVISOR_OBSERVATION_RUNNING';pid=[int]1234;sequence=[long]2;process_creation_filetime=[long]134359860348047610;checked_at=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0-$age)
}
$instances=[pscustomobject]@{Count=1;FixtureGuid=$instanceGuid}
$instances|Add-Member ScriptMethod Item {param($n)if($n -ne 1){throw 'Wrong fixture instance index'};[pscustomobject]@{InstanceGuid=$this.FixtureGuid}}
$task=[pscustomobject]@{Enabled=$true;State=4}
$task|Add-Member ScriptMethod GetInstances {param($flags)if($flags -ne 0){throw 'Wrong fixture instance flags'};$script:events.Add('INSTANCES');$instances}
$folder=[pscustomobject]@{}
$folder|Add-Member ScriptMethod GetTask {param($name)if($name -cne $script:observerTask){throw 'Wrong fixture task'};$script:events.Add('TASK');$task}
function Assert-HeldTaskDefinition {param($given,$givenArguments,[bool]$enabled)if($given -ne $task -or $givenArguments -cne 'fixture-args' -or -not $enabled){throw 'Fixture task definition binding lost'};$script:events.Add('DEFINITION')}
function Test-HeldObservationPresence {param($path)if($path -cne 'C:\inert-fixture\observation-runtime.json'){throw 'Wrong fixture runtime path'};$script:events.Add('PRESENCE');return $true}
function Read-HeldObservationRuntime {param($path)$script:events.Add('RUNTIME');return [pscustomobject]@{Value=$runtime;Sha256='e'*64}}
function Initialize-HeldProcessWitness {$script:events.Add('NATIVE')}
if($fault -ceq 'binding'){$runtime.configuration_sha256='f'*64}
if($fault -ceq 'scope'){$runtime.paid_repair_enabled=$true}
if($fault -ceq 'task_state'){$task.State=2}
if($fault -ceq 'instance'){$instances.FixtureGuid='{22222222-2222-2222-2222-222222222222}'}
if($fault -ceq 'native_sid'){[CoChemHeldObservationProcess]::ExpectedSid='S-1-5-19'}
if($fault -ceq 'native_dead'){[CoChemHeldObservationProcess]::FailLive=$true}
$ok=$false;$errorText='';$result=$null
try{
 try{$result=Wait-HeldObservationRunning $folder 'fixture-args' $provision '{11111111-1111-1111-1111-111111111111}';$ok=$true}catch{$errorText=$_.Exception.Message}
 $retained=$script:processWitnesses.Count
 $value=@{ok=$ok;error=$errorText;created=[CoChemHeldObservationProcess]::Created;live=[CoChemHeldObservationProcess]::Live;disposed_before_cleanup=[CoChemHeldObservationProcess]::Disposed;retained=$retained;events=@($script:events)}
 if($ok){$value.native=$result.NativeProcess;$value.runtime_same=[object]::ReferenceEquals($result.Runtime,$runtime)}
}finally{foreach($w in $script:processWitnesses){$w.Dispose()};foreach($s in $script:held){$s.Dispose()}}
$value.disposed_after_cleanup=[CoChemHeldObservationProcess]::Disposed
$value|ConvertTo-Json -Compress -Depth 5
'''
    return run(code)


@pytest.mark.parametrize('age', [0.0, -29.0, 29.0])
def test_corrected_actual_wait_accepts_fresh_runtime_and_retains_native_witness(age):
    value = fixture(age=age)
    assert value['ok'], value
    assert value['runtime_same'] and value['retained'] == 1
    assert (value['created'], value['live'], value['disposed_before_cleanup'], value['disposed_after_cleanup']) == (1, 1, 0, 1)
    assert value['native']['pid'] == 1234 and value['native']['system_sid'] == 'S-1-5-18'
    assert value['events'] == ['TASK', 'DEFINITION', 'PRESENCE', 'RUNTIME', 'INSTANCES', 'NATIVE']


@pytest.mark.parametrize('age', [-31.0, 31.0, 21600.0])
def test_corrected_actual_wait_rejects_stale_or_future_runtime_before_native_witness(age):
    value = fixture(age=age)
    assert not value['ok'] and value['error'] == 'Observation heartbeat is stale.'
    assert value['created'] == value['retained'] == value['disposed_after_cleanup'] == 0
    assert 'NATIVE' not in value['events']


def test_original_actual_wait_reproduces_false_stale_on_this_windows_host():
    value = fixture(corrected=False)
    assert not value['ok'] and value['error'] == 'Observation heartbeat is stale.'
    assert value['created'] == 0 and 'NATIVE' not in value['events']


@pytest.mark.parametrize('fault,error', [
    ('binding', 'Daemon runtime evidence is not bound.'),
    ('scope', 'Daemon runtime exceeded observation scope.'),
    ('task_state', 'Observation task is not the exact owned running instance.'),
    ('instance', 'Observation task is not the exact owned running instance.'),
])
def test_corrected_wait_preserves_binding_scope_and_exact_task_guards(fault, error):
    value = fixture(fault=fault)
    assert not value['ok'] and value['error'] == error
    assert value['created'] == value['retained'] == 0


@pytest.mark.parametrize('fault,error', [
    ('native_sid', 'Observer native process identity differs.'),
    ('native_dead', 'Inert witness not live'),
])
def test_corrected_wait_keeps_native_refusal_and_disposes_rejected_witness(fault, error):
    value = fixture(fault=fault)
    assert not value['ok'] and error in value['error']
    assert value['created'] == value['disposed_before_cleanup'] == value['disposed_after_cleanup'] == 1
    assert value['retained'] == 0


def test_original_installation_source_is_still_frozen():
    assert hashlib.sha256(HELD.read_bytes()).hexdigest() == PIN
