"""Ordinary Windows PS5.1 receipt-only recovery tests.

Only AST-imported functions run; task/native/filesystem boundaries use inert
doubles and disposable files. No real Apply, task, provider, or private state.
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

W = Path(__file__).resolve().parent
SOURCE = W / 'resume-running-held-observer-r3-v1.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps(body):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(SOURCE,[ref]$t,[ref]$e)
if($e.Count){throw 'Parse failure'}
foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
'''.replace('SOURCE', q(SOURCE))
    raw = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    p = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', raw],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().lstrip('\ufeff'))


COMMON = r'''
$script:targetRoot='C:\fixture\observer';$script:recoveryRoot='C:\fixture\recovery'
$script:stagingRoot='C:\fixture\staging';$script:commissionRoot='C:\fixture\commission'
$script:dataRoot='C:\fixture\data';$script:observerTask='observer';$script:provisionTask='provision'
$script:sourceHash='d2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640'
$script:held=[Collections.Generic.List[IO.Stream]]::new()
$script:events=[Collections.Generic.List[string]]::new();$script:reads=[Collections.Generic.List[string]]::new()
$script:writeCount=0;$script:waitCount=0;$script:snapshotCount=0;$script:mode='success'
$script:processWitnesses=@([pscustomobject]@{})
$script:processWitnesses[0]|Add-Member ScriptMethod AssertLive {$script:events.Add('native-live');if($script:mode -ceq 'native-ended'){throw 'native ended'}}
function Runtime([int]$Sequence){[pscustomobject]@{pid=[int]24608;instance_id='a0136f033e6b4045a484cbd3c9828cad';process_creation_filetime=[long]134359860348047610;sequence=$Sequence;checked_at=([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0)}}
function Observed([int]$Sequence){[pscustomobject]@{Runtime=(Runtime $Sequence);Control=[pscustomobject]@{Sha256=('a'*64)};NativeProcess=[ordered]@{pid=24608;system_sid='S-1-5-18';creation_filetime=[long]134359860348047610;held_native_handle=$true;task_instance_guid='{12345678-1234-1234-1234-123456789012}';image_sha256=('b'*64)}}}
function Assert-RunningHeldAbsent([string]$Path){$script:events.Add('absent:'+([IO.Path]::GetFileName($Path)));if($script:mode -ceq 'activation-exists' -and $Path.EndsWith('activation.json')){throw 'exists'};if($script:mode -ceq 'recovery-exists' -and $Path -ceq $script:recoveryRoot){throw 'exists'}}
function Read-HeldObservationControl([string]$Path,[string]$Pin,[long]$Maximum){$script:reads.Add($Path);if($script:mode -ceq 'pin-drift' -and $Path.EndsWith('installer-failure.json')){throw 'pin drift'};[pscustomobject]@{Sha256=$Pin;Value=[pscustomobject]@{nonce='10c0d0613b1740108e38138630b6b4ad'}}}
function Assert-ProtectedPath([string]$Path){$script:events.Add('protected:'+([IO.Path]::GetFileName($Path)))}
function Get-Item {param([string]$LiteralPath,[switch]$Force);[pscustomobject]@{Length=100;PSIsContainer=$false}}
function Open-VerifiedFile([string]$Path,[string]$Pin,[long]$Length){$script:events.Add('source-held');if($Pin -cne $script:sourceHash){throw 'source pin'};[IO.MemoryStream]::new([byte[]]@(1,2,3))}
function Get-HeldObservationInputs {[pscustomobject]@{Packet=[ordered]@{staging_receipt_sha256=('c'*64);commissioning_receipt_sha256=('d'*64)}}}
function Assert-HeldObservationPriorTasks($Folder,$Inputs){$script:events.Add('prior-tasks')}
function Get-RegistrationTask($Folder,[string]$Name){[pscustomobject]@{Name=$Name}}
function Assert-CompletedKnowledgeTask($Task){$script:events.Add('provision-terminal')}
function Assert-HeldTaskDefinition($Task,[string]$Arguments,[bool]$Daemon){$script:events.Add('provision-definition');if($Daemon -or -not $Arguments.Contains('--prepare --nonce 10c0d0613b1740108e38138630b6b4ad --inputs-sha256 e65f44e1396a1f02802372a69579008e118ef93d1987d8edf7301a45b1218c61')){throw 'provision binding'}}
function Assert-HeldProvisionReceipt($Receipt,$Inputs,[string]$Nonce,[string]$InputsHash,$Task){$script:events.Add('provision-receipt');if($script:mode -ceq 'provision-held'){throw 'provision held'}}
function Assert-RunningHeldTask($Folder,[string]$ExpectedGuid=''){$script:events.Add('observer-owned');if($script:mode -ceq 'task-drift' -and $ExpectedGuid){throw 'task changed'};[pscustomobject]@{Guid='{12345678-1234-1234-1234-123456789012}';Arguments='observe'}}
function Open-SetupControllerWitness($Folder){$script:events.Add('controller-open');[pscustomobject]@{Proof='controller'}}
function Assert-SetupCurrentWitness($Witness,$Folder){$script:events.Add('controller-current');if($script:mode -ceq 'controller-ended'){throw 'controller ended'}}
function Assert-HeldObservationRuntime {param([switch]$FullCustody);if(-not $FullCustody){throw 'custody missing'};$script:events.Add('full-custody')}
function Get-RunningHeldTasksSnapshot($Folder){$script:snapshotCount++;if($script:mode -ceq 'snapshot-drift' -and $script:snapshotCount -gt 1){return 'changed'};'same-tasks'}
function Wait-HeldObservationRunning($Folder,[string]$Arguments,$Provision,[string]$InstanceGuid){$script:waitCount++;$script:events.Add('heartbeat');Observed (100+$script:waitCount)}
function Start-Sleep {param([int]$Milliseconds)}
function New-ProtectedDirectory([string]$Path){$script:events.Add('new-recovery-root')}
function Write-HeldObservationControl([string]$Path,$Value){$script:writeCount++;$script:events.Add('write:'+([IO.Path]::GetFileName($Path)));if($script:mode -ceq 'publication-fails' -and $script:writeCount -eq 2){throw 'publish failed'};$script:written.Add([pscustomobject]@{Path=$Path;Value=$Value});('e'*64)}
$script:written=[Collections.Generic.List[object]]::new()
'''


def test_all_held_support_pins_match_sources_and_named_functions_exist():
    text = SOURCE.read_text(encoding='utf-8-sig')
    deps = re.findall(r"@\{path=\(Join-Path \$PSScriptRoot '([^']+)'\);hash='([a-f0-9]{64})';names=@\(([^}]+)\)\}", text)
    assert len(deps) == 9
    for name, pin, exports in deps:
        raw = (W / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin, name
        source = raw.decode('utf-8-sig')
        for fn in re.findall(r"'([^']+)'", exports):
            assert len(re.findall(r'^function ' + re.escape(fn) + r'\s*\{', source, re.M)) == 1
    original = W / 'install-held-supervisor-observation-r3-v3.ps1'
    assert hashlib.sha256(original.read_bytes()).hexdigest() == 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6'


@pytest.mark.parametrize('field,value', [
    ('pid', '$true'), ('pid', '24609'), ('instance_id', "'0'*32"),
    ('process_creation_filetime', '[long]134359860348047611'),
    ('process_creation_filetime', "'134359860348047610'"),
    ('sequence', '$true'), ('sequence', '0'), ('checked_at', '$true'),
    ('checked_at', '[double]::NaN'), ('checked_at', '[double]::PositiveInfinity'),
    ('checked_at', '([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0-31)'),
    ('checked_at', '([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0+31)'),
])
def test_runtime_refuses_mismatched_identity_invalid_types_and_stale_clock(field, value):
    result = ps(COMMON + f"$v=Runtime 5;$v.{field}={value};try{{Assert-RunningHeldRuntimeIdentity $v;throw 'accepted'}}catch{{@{{error=$_.Exception.Message}}|ConvertTo-Json}}")
    assert result['error'].startswith('HELD_RECOVERY_')


def test_runtime_requires_strict_progress_and_accepts_current_original_identity():
    result = ps(COMMON + r'''$v=Runtime 5;Assert-RunningHeldRuntimeIdentity $v 4
try{Assert-RunningHeldRuntimeIdentity $v 5;throw 'accepted'}catch{@{error=$_.Exception.Message}|ConvertTo-Json}''')
    assert result['error'] == 'HELD_RECOVERY_RUNTIME_IDENTITY_OR_PROGRESS'


def test_preflight_reads_only_pinned_json_holds_source_and_two_progressing_heartbeats():
    result = ps(COMMON + r'''$p=Get-RunningHeldPreflight 'inert'
@{first=$p.First.Runtime.sequence;second=$p.Second.Runtime.sequence;reads=@($script:reads);events=@($script:events);writes=$script:writeCount;held=$script:held.Count}|ConvertTo-Json -Depth 5''')
    assert result['first'] == 101 and result['second'] == 102
    assert len(result['reads']) == 8
    assert not any(p.endswith('.py') for p in result['reads'])
    assert 'source-held' in result['events'] and 'provision-definition' in result['events']
    assert result['events'].count('heartbeat') == 2
    assert result['held'] == 1 and result['writes'] == 0


@pytest.mark.parametrize('mode', ['activation-exists', 'recovery-exists', 'pin-drift', 'provision-held', 'task-drift', 'controller-ended', 'snapshot-drift'])
def test_preflight_failures_create_no_outputs_and_request_no_task_action(mode):
    result = ps(COMMON + f"$script:mode={q(mode)};try{{Get-RunningHeldPreflight 'inert'|Out-Null;throw 'accepted'}}catch{{@{{error=$_.Exception.Message;writes=$script:writeCount;events=@($script:events)}}|ConvertTo-Json -Depth 4}}")
    assert result['error'] != 'accepted'
    assert result['writes'] == 0 and 'new-recovery-root' not in result['events']


def test_publication_matches_original_activation_schema_and_only_creates_three_receipts():
    result = ps(COMMON + r'''$p=Get-RunningHeldPreflight 'inert';$r=Invoke-RunningHeldRecovery 'inert' $p
@{result=$r;written=@($script:written);events=@($script:events)}|ConvertTo-Json -Depth 15''')
    written = result['written']
    assert [Path(v['Path']).name for v in written] == ['recovery-intent.json', 'activation.json', 'recovery-complete.json']
    activation = written[1]['Value']
    assert activation['schema'] == 'cochem-held-supervisor-activation-result/1'
    assert activation['status'] == 'HELD_SUPERVISOR_OBSERVATION_RUNNING'
    # OrderedDictionary gets the returned receipt paths after CreateNew; the
    # durable publication at call time is checked separately by the flow order.
    for key in ('paid_repair_enabled', 'component_recovery_enabled', 'existing_warden_changed', 'full_srs_acceptance'):
        assert activation[key] is False
    assert written[0]['Value']['task_runs_requested'] == 0
    assert written[2]['Value']['task_runs_requested'] == 0
    assert written[2]['Value']['model_jobs_submitted'] == 0
    assert written[2]['Value']['databases_opened'] is False
    assert result['events'].index('new-recovery-root') > result['events'].index('full-custody')


@pytest.mark.parametrize('mode', ['activation-exists', 'recovery-exists', 'controller-ended', 'task-drift', 'snapshot-drift'])
def test_prepublication_recheck_failure_does_not_create_recovery_root_or_activation(mode):
    result = ps(COMMON + f"$p=Get-RunningHeldPreflight 'inert';$script:mode={q(mode)};try{{Invoke-RunningHeldRecovery 'inert' $p|Out-Null;throw 'accepted'}}catch{{@{{error=$_.Exception.Message;writes=$script:writeCount;events=@($script:events)}}|ConvertTo-Json -Depth 4}}")
    assert result['error'] != 'accepted'
    assert result['writes'] == 0 and 'new-recovery-root' not in result['events']


def test_publication_failure_preserves_intent_without_completion_or_automatic_repeat():
    result = ps(COMMON + r'''$p=Get-RunningHeldPreflight 'inert';$script:mode='publication-fails'
try{Invoke-RunningHeldRecovery 'inert' $p|Out-Null;throw 'accepted'}catch{@{error=$_.Exception.Message;writes=$script:writeCount;written=@($script:written);events=@($script:events)}|ConvertTo-Json -Depth 12}''')
    assert result['error'] == 'publish failed'
    assert result['writes'] == 2
    assert len(result['written']) == 1
    assert result['written'][0]['Value']['automatic_retry_allowed'] is False
    assert 'write:recovery-complete.json' not in result['events']


def test_native_process_exit_after_intent_refuses_activation_and_preserves_intent():
    result = ps(COMMON + r'''$p=Get-RunningHeldPreflight 'inert';$script:mode='native-ended'
try{Invoke-RunningHeldRecovery 'inert' $p|Out-Null;throw 'accepted'}catch{@{error=$_.Exception.Message;writes=$script:writeCount;written=@($script:written)}|ConvertTo-Json -Depth 12}''')
    assert 'native ended' in result['error'] and result['writes'] == 1
    assert len(result['written']) == 1


def test_ast_contains_no_task_mutator_provider_or_database_invocation():
    result = ps(r'''$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(''' + q(SOURCE) + r''',[ref]$t,[ref]$e)
$methods=@($a.FindAll({param($n)$n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)|ForEach-Object{$_.Member.Value})
$assigned=@($a.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst]},$true)|ForEach-Object{$_.Left.Extent.Text})
@{methods=$methods;assigned=$assigned}|ConvertTo-Json -Depth 5''')
    forbidden = {'Run', 'RunEx', 'RegisterTaskDefinition', 'RegisterTask', 'SetSecurityDescriptor', 'DeleteTask', 'Stop', 'CreateProcessWithLogonW'}
    assert not forbidden.intersection(result['methods'])
    assert not any(a.endswith('.Enabled') for a in result['assigned'])
    text = SOURCE.read_text(encoding='utf-8-sig')
    assert "schema='cochem-held-supervisor-observation-plan/1'" in text
    assert "warden_stop_required=$false" in text and "activation_receipt_only=$true" in text
    assert "New-HeldObservationTask'" not in text and "Invoke-HeldObservationInstall'" not in text
