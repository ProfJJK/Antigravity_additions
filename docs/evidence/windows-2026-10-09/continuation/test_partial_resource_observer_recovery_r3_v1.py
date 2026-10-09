"""Ordinary PS5.1 recovery-flow fixtures; no real Apply/tasks/private state."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

W = Path(__file__).resolve().parent
SOURCE = W / 'resume-partial-resource-observer-r3-v1.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def ps(body, helper=False):
    prefix = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
    for path in ([SOURCE, W / 'resource-observer-duration-r3-v1.ps1'] if helper else [SOURCE]):
        prefix += r'''$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(PATH,[ref]$t,[ref]$e)
if($e.Count){throw 'Parse failure'}
foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
'''.replace('PATH', q(path))
    encoded = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    p = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=35)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().lstrip('\ufeff'))


COMMON = r'''
$script:targetRoot='C:\fixture\resource';$script:packageRoot='C:\fixture\resource\package';$script:recoveryRoot='C:\fixture\recovery';$script:priorRoot='C:\fixture\prior'
$script:python='C:\fixture\python.exe';$script:installRoot='C:\fixture\install';$script:taskName='resource';$script:sourceManifestHash='source';$script:dependencyHash='dependencies'
$script:events=[Collections.Generic.List[string]]::new();$script:written=[Collections.Generic.List[object]]::new();$script:starts=0;$script:mode='success'
$script:heldObserverWitness=[pscustomobject]@{};$script:heldObserverWitness|Add-Member ScriptMethod AssertLive {$script:events.Add('native-live');if($script:mode -ceq 'native-ended'){throw 'native ended'}}
$controller=[pscustomobject]@{pid=5788;creation_filetime=[long]134359858192107395;instance_id='c867588c063c4362beca10358ebf79ef'}
$commission=[pscustomobject]@{Sha256='4bf82adb21852c52df8dd20c112b8df912b4813c225aa66cdfdcb272fe148bd3';Value=[pscustomobject]@{controller=$controller}}
$runtime=[pscustomobject]@{install_receipt_sha256='install';configuration_sha256='config';revision=[pscustomobject]@{source_sha256='revision'}}
$task=[pscustomobject]@{Name='resource';Xml='<inert-task/>';Definition=[pscustomobject]@{Settings=[pscustomobject]@{ExecutionTimeLimit='P2DT1H'}}}
function Assert-ObserverAbsent([string]$Path){$script:events.Add('absent:'+([IO.Path]::GetFileName($Path)));if($script:mode -ceq 'existing-activation' -and $Path.EndsWith('observer-registration.json')){throw 'preserved registration'};if($script:mode -ceq 'existing-recovery' -and $Path -ceq $script:recoveryRoot){throw 'preserved recovery'}}
function Get-ChildItem {param([string]$LiteralPath,[switch]$Force);if($script:mode -ceq 'extra-file'){return @([pscustomobject]@{Name='unexpected'})};@([pscustomobject]@{Name='dependencies.json'},[pscustomobject]@{Name='package'},[pscustomobject]@{Name='registration-intent.json'})}
function Read-PartialResourcePinnedJson([string]$Path,[string]$Hash){$script:events.Add('pin:'+([IO.Path]::GetFileName($Path)));if($script:mode -ceq 'pin-drift'){throw 'pin drift'};$v=[pscustomobject]@{};switch([IO.Path]::GetFileName($Path)){
 'series-failed.json' {$v=[pscustomobject]@{schema='cochem-protected-acceptance-setup-failure/1';status='SETUP_HELD_PRESERVE_PARTIAL_AND_RUNNING_STATE';nonce='0b8014d6c60c40cfa1a0be9bd51ab3db';phase='resource_observation';verified_phases=@('independent_staging','first_start','held_observation');automatic_retry_or_resume=$false;running_state_preserved=$true}}
 'registration-intent.json' {$v=[pscustomobject]@{schema='cochem-resource-observer-registration-intent/1';source_manifest_sha256='source';dependency_manifest_sha256='dependencies';commissioning_sha256=$commission.Sha256;task_name='resource';controller_pid=5788;controller_creation_filetime=$controller.creation_filetime;runtime_root='C:\fixture\install';full_srs_acceptance=$false;automatic_restart_or_resume=$false}}
 };[pscustomobject]@{Value=$v;Sha256=$Hash}}
function Assert-R3InstalledBindings {$script:events.Add('runtime');$runtime}
function Read-ObserverCommissioning {$script:events.Add('commission');$commission}
function Get-ObserverInventory {$script:events.Add('source');@('frozen-source')}
function Assert-ObserverDependencies {param([switch]$CompleteCustody);if(-not $CompleteCustody){throw 'custody missing'};$script:events.Add('dependencies-custody');if($script:mode -ceq 'dependency-drift'){throw 'dependency drift'};[pscustomobject]@{complete_custody_deferred=$false}}
function Assert-ObserverInstalledSources($Records){$script:events.Add('installed-custody');if($script:mode -ceq 'source-drift'){throw 'source drift'}}
function Assert-ObserverStateUnstarted {$script:events.Add('empty-private-state');if($script:mode -ceq 'nonempty-state'){throw 'private state not empty'}}
function Get-ObserverArguments([string]$Hash){'exact-observer-arguments'}
function Get-RegistrationTask($Folder,[string]$Name){$task}
function Assert-ObserverTask($Task,[string]$Arguments){$script:events.Add('exact-disabled-task');if($script:mode -ceq 'task-drift'){throw 'task drift'}}
function Open-PartialResourceDaemonWitness($Folder,$Activation){$script:events.Add('native-daemons');[pscustomobject]@{proof='controller'}}
function Get-PartialResourceTaskSnapshot($Folder,[switch]$DaemonsOnly){if($script:mode -ceq 'snapshot-drift'){return 'changed'};if($DaemonsOnly){return 'same-daemons'};'same-all'}
function Assert-SetupCurrentWitness($Witness,$Folder){$script:events.Add('controller-live');if($script:mode -ceq 'controller-ended'){throw 'controller ended'}}
function Assert-ProtectedPath([string]$Path){$script:events.Add('protected-root')}
function Initialize-SetupNativeWitness {}
Add-Type -TypeDefinition 'public static class CoChemProtectedSetupWitness { public static int Roots=0; public static void CreateFreshRoot(string p,string s){Roots++;} }'
function New-CodeAcl([bool]$Directory){$v=[pscustomobject]@{};$v|Add-Member ScriptMethod GetSecurityDescriptorSddlForm {param($x)'inert-sddl'};$v}
function Write-RegistrationControl([string]$Path,[string]$Text){$script:events.Add('write:'+([IO.Path]::GetFileName($Path)));if($script:mode -ceq 'publication-fails' -and $Path.EndsWith('observer-registration.json')){throw 'publication failed'};$script:written.Add([pscustomobject]@{Path=$Path;Text=$Text});('e'*64)}
function Read-ObserverRegistration($Folder,$Commissioning,$Records){$script:events.Add('registration-readback');[pscustomobject]@{Sha256=('e'*64)}}
function Invoke-ObserverStartWithReceipt($Folder,$Commissioning,$Registration){$script:starts++;$script:events.Add('original-single-start');if($script:mode -ceq 'start-held'){throw 'original start held'};[ordered]@{status='RESOURCE_OBSERVER_RUNNING_INITIAL_MEASUREMENTS_VERIFIED';receipt_sha256=('f'*64)}}
'''


def test_preflight_full_custody_and_private_state_before_any_publication():
    r = ps(COMMON + r'''$p=Get-PartialResourcePreflight 'inert';@{snapshot=$p.Snapshot;events=@($script:events);writes=$script:written.Count;starts=$script:starts}|ConvertTo-Json -Depth 5''')
    assert r['snapshot'] == 'same-all' and r['writes'] == r['starts'] == 0
    for key in ('dependencies-custody', 'installed-custody', 'empty-private-state', 'exact-disabled-task', 'native-daemons'):
        assert key in r['events']
    assert r['events'].count('pin:registration-intent.json') == 1


@pytest.mark.parametrize('mode', ['existing-activation', 'existing-recovery', 'extra-file', 'pin-drift', 'dependency-drift', 'source-drift', 'nonempty-state', 'task-drift', 'controller-ended', 'native-ended'])
def test_preflight_refusal_preserves_partial_state_without_writes_or_start(mode):
    r = ps(COMMON + f"$script:mode={q(mode)};try{{Get-PartialResourcePreflight 'inert'|Out-Null;throw 'accepted'}}catch{{@{{error=$_.Exception.Message;writes=$script:written.Count;starts=$script:starts;roots=[CoChemProtectedSetupWitness]::Roots}}|ConvertTo-Json}}")
    assert r['error'] != 'accepted' and r['writes'] == r['starts'] == r['roots'] == 0


def test_recovery_completes_existing_registration_then_starts_original_task_once():
    r = ps(COMMON + r'''$p=Get-PartialResourcePreflight 'inert';$result=Invoke-PartialResourceRecovery 'inert' $p
@{result=$result;written=@($script:written);events=@($script:events);starts=$script:starts;roots=[CoChemProtectedSetupWitness]::Roots}|ConvertTo-Json -Depth 10''')
    assert r['starts'] == r['roots'] == 1
    assert [Path(v['Path']).name for v in r['written']] == ['recovery-intent.json', 'observer-task.xml', 'observer-registration.json', 'recovery-complete.json']
    registration = json.loads(r['written'][2]['Text'])
    assert registration['schema'] == 'cochem-resource-observer-registration/1'
    assert registration['status'] == 'RESOURCE_OBSERVER_REGISTERED_DISABLED'
    assert registration['execution_time_limit_raw'] == 'P2DT1H'
    assert registration['execution_time_limit_seconds'] == 176400
    assert registration['task_started'] is registration['full_srs_acceptance'] is False
    assert r['events'].index('registration-readback') < r['events'].index('original-single-start')
    complete = json.loads(r['written'][3]['Text'])
    assert complete['task_registration_requested'] == complete['payloads_copied'] == 0
    assert complete['existing_daemons_unchanged'] is True
    assert complete['task_action_limits_triggers_or_acl_modified'] is False
    assert complete['exactly_one_start_requested'] is True
    assert 'task_definition_or_acl_modified' not in complete


@pytest.mark.parametrize('mode', ['existing-activation', 'existing-recovery', 'nonempty-state', 'task-drift', 'controller-ended', 'native-ended', 'snapshot-drift'])
def test_recovery_rechecks_all_guards_before_root_and_registration_publication(mode):
    r = ps(COMMON + f"$p=Get-PartialResourcePreflight 'inert';$script:mode={q(mode)};try{{Invoke-PartialResourceRecovery 'inert' $p|Out-Null;throw 'accepted'}}catch{{@{{error=$_.Exception.Message;writes=$script:written.Count;starts=$script:starts;roots=[CoChemProtectedSetupWitness]::Roots}}|ConvertTo-Json}}")
    assert r['error'] != 'accepted' and r['writes'] == r['starts'] == r['roots'] == 0


@pytest.mark.parametrize('mode,writes,starts', [('publication-fails', 2, 0), ('start-held', 3, 1)])
def test_failure_after_intent_preserves_outputs_and_never_retries_start(mode, writes, starts):
    r = ps(COMMON + f"$p=Get-PartialResourcePreflight 'inert';$script:mode={q(mode)};try{{Invoke-PartialResourceRecovery 'inert' $p|Out-Null;throw 'accepted'}}catch{{@{{error=$_.Exception.Message;written=@($script:written);starts=$script:starts}}|ConvertTo-Json -Depth 10}}")
    assert r['error'] != 'accepted' and len(r['written']) == writes and r['starts'] == starts
    intent = json.loads(r['written'][0]['Text'])
    assert intent['automatic_restart_or_resume'] is False
    assert not any(Path(v['Path']).name == 'recovery-complete.json' for v in r['written'])


def test_recovery_ast_has_no_registration_recopy_or_task_permission_mutation():
    r = ps(r'''$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile(''' + q(SOURCE) + r''',[ref]$t,[ref]$e)
@{methods=@($a.FindAll({param($n)$n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)|ForEach-Object{$_.Member.Value});assigned=@($a.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst]},$true)|ForEach-Object{$_.Left.Extent.Text})}|ConvertTo-Json -Depth 6''')
    assert not {'RegisterTask', 'RegisterTaskDefinition', 'SetSecurityDescriptor', 'DeleteTask', 'Run', 'Stop'}.intersection(r['methods'])
    assert not any(v.endswith('.Enabled') for v in r['assigned'])
    text = SOURCE.read_text(encoding='utf-8-sig')
    assert "'Copy-VerifiedPayload'" not in text and "'Invoke-ObserverRegistration'" not in text


def test_all_source_pins_and_exports_match_exact_frozen_support():
    text = SOURCE.read_text(encoding='utf-8-sig')
    rows = re.findall(r"@\{path=\(Join-Path \$PSScriptRoot '([^']+)'\);hash='([a-f0-9]{64})';names=@\(([^}]+)\)\}", text)
    assert len(rows) == 9
    for name, pin, exports in rows:
        raw = (W / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin, name
        for export in re.findall(r"'([^']+)'", exports):
            assert len(re.findall(r'^function ' + re.escape(export) + r'\s*\{', raw.decode('utf-8-sig'), re.M)) == 1


@pytest.mark.parametrize('value,kind,expected', [
    ('$true', 'boolean', True), ('$false', 'boolean', False), ('0', 'integer', 0),
    ('[long]267011', 'integer', 267011), ('[int16]2', 'integer', 2),
    ('$true', 'integer', 'TYPE_REFUSED'), ("'2'", 'integer', 'TYPE_REFUSED'),
    ('1.0', 'integer', 'TYPE_REFUSED'), ("'false'", 'boolean', 'TYPE_REFUSED'),
    ('0', 'boolean', 'TYPE_REFUSED'),
])
def test_definition_projection_scalar_fields_do_not_coerce_unknown_types(value, kind, expected):
    r = ps(f"@{{value=(Get-PartialResourceScalar ({value}) {q(kind)})}}|ConvertTo-Json")
    assert r['value'] == expected


def test_definition_projection_reports_normalized_duration_and_safe_actual_fields_only():
    r = ps(r'''
$script:taskName='resource';$script:python='python';$script:packageRoot='package'
$actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($i)[pscustomobject]@{Type=0;Path='python';Arguments='DO_NOT_PUBLISH';WorkingDirectory='package'}}
$t=[pscustomobject]@{Name='resource';Enabled=$false;State=1;LastTaskResult=267011;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SECRET_USER';LogonType=5;RunLevel=1};Settings=[pscustomobject]@{AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='P2DT1H';Enabled=$false};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
$t|Add-Member ScriptMethod GetInstances {param($f)[pscustomobject]@{Count=0}}
Get-PartialResourceDefinition $t 'expected-arguments'|ConvertTo-Json -Depth 8
''', helper=True)
    assert r['execution_time_limit_raw'] == 'P2DT1H'
    assert r['legacy_literal_duration_match'] is False
    assert r['checks']['duration_49_hours'] is True
    assert set(r['mismatches']) == {'system_principal', 'action_arguments'}
    assert r['actual']['principal_user'] == 'VALUE_REFUSED'
    assert r['actual']['running_instances'] == 0
    assert r['actual']['last_task_result'] == 267011
    assert 'DO_NOT_PUBLISH' not in json.dumps(r) and 'SECRET_USER' not in json.dumps(r)
