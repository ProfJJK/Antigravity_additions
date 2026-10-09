"""WinPS5.1 real disposable control copying and simulated task-registration flow.

No actual task registration, protected-root mutation, Python child, or provider.
"""
import base64
import json
from pathlib import Path
import subprocess

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / 'register-stopped-warden-r3.ps1'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
PAYLOAD = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
LEAF = HERE / 'check-worker-native-status-r3.ps1'
CONFIG = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\pipeline.json')


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(body):
    prefix = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
    prefix += "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop\n"
    prefix += "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop\n"
    prefix += '''
function Import-TestFunctions([string]$Path){
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'Parse error'}
 foreach($node in $ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$node.Extent.Text}
}
'''
    prefix += "foreach($text in Import-TestFunctions " + quote(SCRIPT) + "){. ([scriptblock]::Create($text))}\n"
    command = prefix + body
    result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        base64.b64encode(command.encode('utf-16le')).decode()], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stderr + '\n' + result.stdout
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


FAKES = r'''
$taskSddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'
$taskName='CoChem-4.2.7-Warden'
$installRoot='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3'
$python=Join-Path $installRoot '.venv\Scripts\python.exe'
$basePythonRoot='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312'
function New-TestDefinition {
 $actions=[pscustomobject]@{Count=0;Value=$null}
 $actions|Add-Member ScriptMethod Create {param($type)
  if($type -ne 0){throw 'Only executable action'}
  $this.Value=[pscustomobject]@{Type=0;Path='';Arguments='';WorkingDirectory=''};$this.Count=1;$this.Value
 }
 $actions|Add-Member ScriptMethod Item {param($index)if($index -ne 1){throw 'Bad index'};$this.Value}
 [pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};
 Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='PT0S'};
 Triggers=[pscustomobject]@{Count=0};Actions=$actions}
}
function New-TestTask($Definition){
 $task=[pscustomobject]@{Definition=$Definition;State=1;Enabled=$false;LastTaskResult=267011;Xml='<Task simulated="true"/>';Instances=0}
 $task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=$this.Instances}}
 $task|Add-Member ScriptMethod GetSecurityDescriptor {param($flags)if($flags -ne 7){throw 'Security info'};$script:taskSddl}
 $task
}
function New-TestFolder {
 $folder=[pscustomobject]@{Tasks=@{};Created=0;RegistrationFlags=$null}
 $folder|Add-Member ScriptMethod GetTask {param($name)
  if($this.Tasks.ContainsKey($name)){return $this.Tasks[$name]}
  throw [Runtime.InteropServices.COMException]::new('missing',-2147024894)
 }
 $folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$sddl)
  if($flags -ne 2 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or $definition.Settings.Enabled -ne $false){throw 'Unsafe registration'}
  if($this.Tasks.ContainsKey($name)){throw 'Collision'}
  Assert-RegisteredTaskAcl $sddl
  $this.Created++;$this.RegistrationFlags=$flags;$this.Tasks[$name]=New-TestTask $definition;$this.Tasks[$name]
 }
 $folder
}
function New-TestScheduler {
 $value=[pscustomobject]@{Calls=0}
 $value|Add-Member ScriptMethod NewTask {param($flags)$this.Calls++;New-TestDefinition}
 $value
}
'''


def test_actual_pinned_configuration_and_uv_format_accepted():
    value = run_ps(FAKES + "\nforeach($text in Get-ReviewedRegistrationFunctions " + quote(LEAF) +
        " '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Assert-VenvBinding')){. ([scriptblock]::Create($text))}\n" +
        'Assert-RegistrationConfiguration ([IO.File]::ReadAllText(' + quote(CONFIG) + '))\n' +
        'Assert-VenvBinding ([IO.File]::ReadAllText(' + quote(CONFIG.parent / '.venv/pyvenv.cfg') + "))\n@{passed=$true}|ConvertTo-Json")
    assert value['passed'] is True


@pytest.mark.parametrize('mutation', [
    '$v.max_execution_slots=6', '$v.docker.max_containers=6', '$v.docker.warm_pool_size=4',
    '$v.workers.PSObject.Properties.Remove("slot6")', '$v.ramdisk.size_mb=16384',
    '$v.ramdisk.workspace_subdirectory="other"', '$v.routing.policy_version=1',
    '$v.workers.slot1.credential_target="other"', '$v.slot_roots.slot1="C:\\other"',
])
def test_config_delta_refused(mutation):
    result = run_ps('$v=[IO.File]::ReadAllText(' + quote(CONFIG) + ')|ConvertFrom-Json\n' + mutation +
        '\n$held=$false;try{Assert-RegistrationConfiguration ($v|ConvertTo-Json -Depth 30)}catch{$held=$true};@{held=$held}|ConvertTo-Json')
    assert result['held'] is True


@pytest.mark.parametrize('code,missing', [(-2147024894, True), (-2147024891, False), (-2147216625, False), (-2147216629, False)])
def test_task_lookup_distinguishes_exact_absence_from_denied_and_scheduler_errors(code, missing):
    result = run_ps('$folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)throw [Runtime.InteropServices.COMException]::new("fixed",' + str(code) + ')}\n' +
        '$held=$false;$absent=$false;try{$absent=$null -eq (Get-RegistrationTask $folder "fixed")}catch{$held=$true};@{held=$held;absent=$absent}|ConvertTo-Json')
    assert result == {'held': not missing, 'absent': missing}


@pytest.mark.parametrize('sddl', [
    'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;WD)',
    'O:BAG:BAD:P(A;IO;FA;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;FR;;;SY)(A;;FA;;;BA)',
    'O:BAG:BAD:P(A;;FA;;;SY)(D;;FA;;;BA)',
])
def test_actual_task_security_descriptor_validation(sddl):
    value = run_ps('$held=$false;try{Assert-RegisteredTaskAcl ' + quote(sddl) + '}catch{$held=$true};@{held=$held}|ConvertTo-Json')
    assert value['held'] is (sddl != 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)')


@pytest.mark.parametrize('mutation', [
    '', '$task.Enabled=$true', '$task.State=3', '$task.State=0', '$task.Instances=1',
    '$task.Definition.Principal.UserId="ansac"', '$task.Definition.Principal.LogonType=3',
    '$task.Definition.Settings.AllowDemandStart=$false', '$task.Definition.Settings.RestartCount=1',
    '$task.Definition.Triggers.Count=1', '$task.Definition.Actions.Value.Arguments="unsafe"',
])
def test_exact_registered_definition_refuses_unsafe_postconditions(mutation):
    code = FAKES + r'''
$configTarget='C:\Program Files\CoChem\fixture\pipeline.json'
$d=New-TestDefinition;$a=$d.Actions.Create(0);$a.Path=$python;$a.WorkingDirectory=$installRoot
$a.Arguments='-I -B -m cochem_pipeline daemon --config "'+$configTarget+'"'
$task=New-TestTask $d
''' + mutation + '\n$held=$false;try{Assert-RegisteredStoppedTask $task}catch{$held=$true};@{held=$held}|ConvertTo-Json'
    assert run_ps(code)['held'] is bool(mutation)


@pytest.mark.parametrize('existing', [False, True])
def test_exact_root_collision_is_preserved(tmp_path, existing):
    target = tmp_path / 'fresh'
    if existing:
        target.mkdir(); (target / 'preserve').write_bytes(b'original')
    value = run_ps('$targetRoot=' + quote(target) + ';$held=$false;try{Assert-FreshActivationRoot}catch{$held=$true};@{held=$held}|ConvertTo-Json')
    assert value['held'] is existing
    if existing: assert (target / 'preserve').read_bytes() == b'original'


def fixture_flow(tmp_path):
    config = tmp_path / 'source.json'; config.write_bytes(CONFIG.read_bytes())
    acl_fixture = tmp_path / 'acl.txt'; acl_fixture.write_text('fixture', encoding='utf-8')
    return FAKES + '\n$fixtureRoot=' + quote(tmp_path) + '\n$config=' + quote(config) + '\n$aclFixture=' + quote(acl_fixture) + r'''
$targetRoot=Join-Path $fixtureRoot 'activation';$configTarget=Join-Path $targetRoot 'pipeline.json'
$configHash='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
$held=[Collections.Generic.List[IO.FileStream]]::new()
''' + 'foreach($text in Get-ReviewedRegistrationFunctions ' + quote(PAYLOAD) + r''' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')){. ([scriptblock]::Create($text))}
Initialize-FileIdentity
# Explicit ordinary-user test boundary: never claim production ACL attestation.
function Assert-ProtectedPath([string]$Path){
 if(-not [IO.Path]::GetFullPath($Path).StartsWith($script:fixtureRoot,[StringComparison]::OrdinalIgnoreCase)){throw 'Escaped disposable fixture'}
 $null=Get-Item -LiteralPath $Path -Force
}
function New-CodeAcl([bool]$Directory){Get-Acl -LiteralPath $script:aclFixture}
function New-ProtectedDirectory([string]$Path){$null=New-Item -ItemType Directory -Path $Path -ErrorAction Stop}
function Assert-CodeTreeOnce([string]$Path){$script:treeChecks++;15013}
$treeChecks=0
$testRuntime=[pscustomobject]@{install_receipt_sha256='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6';source_manifest_sha256='6c33a1434df2e5c3c32697bda5f828fe010f366550beeedde65636de982bbcd1';configuration_sha256=$configHash;revision=[pscustomobject]@{source_sha256='309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'}}
function Assert-R3InstalledBindings{$script:testRuntime}
function Read-R3Control([string]$Path,[string]$Hash){
 if($Path -eq $script:config){[pscustomobject]@{Text=[IO.File]::ReadAllText($Path);Length=(Get-Item -LiteralPath $Path).Length}}
 else{[pscustomobject]@{Text='fixture-runtime';Length=0}}
}
function Read-R3Text($Control){$Control.Text}
function Assert-VenvBinding([string]$Text){if($Text -cne 'fixture-runtime'){throw 'Fixture runtime'}}
$folder=New-TestFolder;$scheduler=New-TestScheduler
'''


def test_real_control_copy_and_entire_registration_function_with_simulated_scheduler(tmp_path):
    value = run_ps(fixture_flow(tmp_path) + r'''
try{
 $result=Invoke-StoppedWardenRegistration $scheduler $folder $testRuntime
 $receipt=[IO.File]::ReadAllText((Join-Path $targetRoot 'stopped-registration.json'))|ConvertFrom-Json
 [ordered]@{result=$result;receipt=$receipt;copied_sha256=(Get-FileHash -LiteralPath $configTarget).Hash.ToLowerInvariant();created=$folder.Created;flags=$folder.RegistrationFlags;tree_checks=$treeChecks;files=@(Get-ChildItem -LiteralPath $targetRoot|ForEach-Object Name)}|ConvertTo-Json -Depth 15
}finally{foreach($stream in $held){$stream.Dispose()}}
''')
    assert value['created'] == 1 and value['flags'] == 2 and value['tree_checks'] == 2
    assert value['copied_sha256'] == '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
    assert value['result']['status'] == 'WARDEN_REGISTERED_DISABLED_NOT_STARTED'
    assert value['receipt']['disabled'] and not value['receipt']['task_started'] and not value['receipt']['activation_ready']
    assert value['receipt']['databases_opened'] is False and value['receipt']['credentials_modified'] is False
    assert sorted(value['files']) == ['pipeline.json', 'registration-intent.json', 'stopped-registration.json', 'warden-task.xml']
    assert (tmp_path / 'source.json').read_bytes() == (tmp_path / 'activation/pipeline.json').read_bytes()


@pytest.mark.parametrize('phase', ['old_task', 'custody', 'config_drift', 'registration', 'post_registration'])
def test_registration_refusals_preserve_partial_state_and_never_run(tmp_path, phase):
    mutations = {
        'old_task': '$folder.Tasks[$taskName]=New-TestTask (New-TestDefinition)',
        'custody': "function Assert-CodeTreeOnce([string]$Path){throw 'fixture custody refusal'}",
        'config_drift': "[IO.File]::AppendAllText($config,' ')",
        'registration': "$folder|Add-Member -Force ScriptMethod RegisterTaskDefinition {throw 'fixture registration failure'}",
        'post_registration': "function Assert-RegisteredStoppedTask($Task){throw 'fixture readback failure'}",
    }
    code = fixture_flow(tmp_path) + mutations[phase] + r'''
try{
 $failed=$false;try{Invoke-StoppedWardenRegistration $scheduler $folder $testRuntime|Out-Null}catch{$failed=$true}
 $files=@();if(Test-Path -LiteralPath $targetRoot){$files=@(Get-ChildItem -LiteralPath $targetRoot|ForEach-Object Name)}
 @{failed=$failed;created=$folder.Created;files=$files;source_present=(Test-Path -LiteralPath $config)}|ConvertTo-Json
}finally{foreach($stream in $held){$stream.Dispose()}}
'''
    result = run_ps(code)
    assert result['failed'] and result['source_present']
    assert result['created'] == int(phase == 'post_registration')
    assert 'stopped-registration.json' not in result['files']
    if phase in {'registration', 'post_registration'}:
        assert sorted(result['files']) == ['pipeline.json', 'registration-intent.json']


def test_runtime_support_hash_drift_refused_before_import(tmp_path):
    modified = tmp_path / 'changed.ps1'; modified.write_bytes(LEAF.read_bytes() + b'\n# drift')
    result = run_ps('$held=$false;try{$null=Get-ReviewedRegistrationFunctions ' + quote(modified) +
        " '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Assert-R3InstalledBindings')}catch{$held=$true};@{held=$held}|ConvertTo-Json")
    assert result['held']
