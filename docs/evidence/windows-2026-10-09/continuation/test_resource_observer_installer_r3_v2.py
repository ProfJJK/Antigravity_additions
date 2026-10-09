"""WinPS5.1 functions with real temp copies and simulated scheduler only."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / 'install-resource-observer-r3-v2.ps1'
REGISTRATION = HERE / 'register-stopped-warden-r3.ps1'
PAYLOAD = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(body):
    prefix = r'''$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
function Definitions([string]$Path){$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e);if($e.Count){throw 'Parser failure'};foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$f.Extent.Text}}
'''
    for path in (REGISTRATION, SCRIPT):
        prefix += 'foreach($text in Definitions ' + quote(path) + '){. ([scriptblock]::Create($text))}\n'
    result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        base64.b64encode((prefix + body).encode('utf-16le')).decode()], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    return json.loads(result.stdout.strip().lstrip('\ufeff'))


FAKES = r'''
$taskName='fixture-resource-observer';$python='C:\fixed\python.exe';$packageRoot='C:\fixed\package';$cacheRoot='C:\fixed\private\empty-cache'
$sourceManifestHash='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';$dependencyHash='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
$installRoot='C:\fixed\r3';$installHash='install';$configHash='config';$revisionHash='revision'
$held=[Collections.Generic.List[IO.FileStream]]::new()
function New-TestDefinition {
 $actions=[pscustomobject]@{Count=0;Value=$null}
 $actions|Add-Member ScriptMethod Create {param($type)$this.Value=[pscustomobject]@{Type=$type;Path='';Arguments='';WorkingDirectory=''};$this.Count=1;$this.Value}
 $actions|Add-Member ScriptMethod Item {param($index)if($index -ne 1){throw 'index'};$this.Value}
 [pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};
 Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$true;MultipleInstances=2;RestartCount=0;ExecutionTimeLimit='PT49H'};Triggers=[pscustomobject]@{Count=0};Actions=$actions}
}
function New-TestTask($Definition){
 $task=[pscustomobject]@{Definition=$Definition;State=1;Enabled=$false;LastTaskResult=267011;Xml='<Task fixture="true"/>';Instances=0;Runs=0;Guid='{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}'}
 $task|Add-Member ScriptMethod GetInstances {param($flags)$v=[pscustomobject]@{Count=$this.Instances;Guid=$this.Guid};$v|Add-Member ScriptMethod Item {param($index)[pscustomobject]@{InstanceGuid=$this.Guid}};$v}
 $task|Add-Member ScriptMethod GetSecurityDescriptor {param($flags)'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)'}
 $task|Add-Member ScriptMethod Run {param($ignored)if(-not $this.Enabled){throw 'disabled'};$this.Runs++;$this.State=4;$this.Instances=1;$this.Definition.Settings.Enabled=$true;[pscustomobject]@{InstanceGuid=$this.Guid}}
 $task
}
function New-TestFolder {
 $folder=[pscustomobject]@{Tasks=@{};Created=0}
 $folder|Add-Member ScriptMethod GetTask {param($name)if($this.Tasks.ContainsKey($name)){return $this.Tasks[$name]};throw [Runtime.InteropServices.COMException]::new('missing',-2147024894)}
 $folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$sddl)
  if($flags -ne 2 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or $definition.Settings.Enabled -ne $false){throw 'Unsafe registration'}
  if($this.Tasks.ContainsKey($name)){throw 'collision'};Assert-RegisteredTaskAcl $sddl;$this.Created++;$this.Tasks[$name]=New-TestTask $definition;$this.Tasks[$name]
 };$folder
}
function New-TestScheduler {$x=[pscustomobject]@{};$x|Add-Member ScriptMethod NewTask {param($flags)New-TestDefinition};$x}
function Fixture-Proof {[pscustomobject]@{Sha256='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';Value=[pscustomobject]@{task_instance_guid='{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}';controller=[pscustomobject]@{pid=1234;creation_filetime=[long]133000000000000000;instance_id=('a'*32);final_sequence=2}}}}
function Fixture-Runtime {[pscustomobject]@{install_receipt_sha256='install';configuration_sha256='config';revision=[pscustomobject]@{source_sha256='revision'}}}
function Fixture-Samples {
 $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
 $native=[pscustomobject]@{Sha256='native';Value=[pscustomobject]@{schema='cochem-resource-native-boundary/1';pid=1234;creation_filetime=[long]133000000000000000;instance_id=('a'*32);heartbeat_sequence=3;handles=20;timestamp=$now}}
 $sample=[pscustomobject]@{Sha256='sample';Value=[pscustomobject]@{pid=1234;instance_id=('a'*32);heartbeat_sequence=3;timestamp=$now;handles=20;rss_mb=20.0;cpu_percent=1.0;elapsed_seconds=1.0;desktop_heap=[pscustomobject]@{available=$false;passed=$false}}}
 [pscustomobject]@{Native=$native;Sample=$sample;Now=$now}
}
'''


@pytest.mark.parametrize('mutation', ['', '$task.Enabled=$true', '$task.State=0', '$task.State=3', '$task.Instances=1',
    '$task.LastTaskResult=0', '$d.Settings.ExecutionTimeLimit="PT0S"', '$d.Settings.RestartCount=1', '$d.Triggers.Count=1',
    '$d.Principal.UserId="ansac"', '$d.Actions.Value.Arguments="changed"', '$d.Settings.AllowDemandStart=$false'])
def test_exact_disabled_never_run_task_contract(mutation):
    result = run_ps(FAKES + r'''
$d=New-TestDefinition;$a=$d.Actions.Create(0);$a.Path=$python;$a.Arguments='fixed';$a.WorkingDirectory=$packageRoot;$task=New-TestTask $d
''' + mutation + r'''
$refused=$false;try{Assert-ObserverTask $task 'fixed'}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert result['refused'] == bool(mutation)


@pytest.mark.parametrize('mutation', ['', '$task.Guid="{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}"', '$task.Instances=2', '$task.State=3'])
def test_running_single_instance_contract(mutation):
    result = run_ps(FAKES + r'''
$d=New-TestDefinition;$a=$d.Actions.Create(0);$a.Path=$python;$a.Arguments='fixed';$a.WorkingDirectory=$packageRoot;$task=New-TestTask $d
$task.Enabled=$true;$instance=$task.Run($null)
''' + mutation + r'''
$refused=$false;try{Assert-ObserverTask $task 'fixed' $true '{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}'}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert result['refused'] == bool(mutation)


@pytest.mark.parametrize('mutation', ['', '$v.Native.Value.creation_filetime++', '$v.Sample.Value.instance_id="bad"',
    '$v.Native.Value.timestamp-=61', '$v.Sample.Value.timestamp+=1', '$v.Sample.Value.rss_mb=[double]::NaN',
    '$v.Sample.Value.cpu_percent=[double]::PositiveInfinity', '$v.Sample.Value.handles="20"',
    '$v.Sample.Value.desktop_heap.available=$true', '$v.Native.Value.heartbeat_sequence=1'])
def test_initial_realistic_sample_projection_and_mutations(mutation):
    result = run_ps(FAKES + '$v=Fixture-Samples\n' + mutation + r'''
$refused=$false;try{Assert-ObserverInitialSamples $v.Native $v.Sample (Fixture-Proof) $v.Now}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert result['refused'] == bool(mutation)


@pytest.mark.parametrize('value', ['../escape.py', '/root.py', 'a\\b.py', 'a:b.py', 'a//b.py', ''])
def test_dependency_relative_paths_cannot_escape(value):
    result = run_ps('$refused=$false;try{Assert-RelativeObserverPath ' + quote(value) + '}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json')
    assert result['refused'] is True


def temp_flow(tmp_path):
    return FAKES + '\n$fixture=' + quote(tmp_path) + r'''
$targetRoot=Join-Path $fixture 'code';$packageRoot=Join-Path $targetRoot 'package';$stateRoot=Join-Path $fixture 'private';$cacheRoot=Join-Path $stateRoot 'empty-cache'
$dependencyManifest=Join-Path $fixture 'deps.json';[IO.File]::WriteAllText($dependencyManifest,'dependencies')
$dependencyHash=(Get-FileHash -LiteralPath $dependencyManifest).Hash.ToLowerInvariant()
$script:events=[Collections.Generic.List[string]]::new();$script:copies=0
function Assert-CommissionedTaskRunning {param($f,$c)$script:events.Add('warden-check')}
function New-ProtectedDirectory {param($Path)Assert-ObserverAbsent $Path;$null=[IO.Directory]::CreateDirectory($Path);$script:events.Add('code-directory')}
function New-ObserverPrivateDirectory {param($Path)Assert-ObserverAbsent $Path;$null=[IO.Directory]::CreateDirectory($Path);$script:events.Add('private-directory-before-bytes')}
function Assert-SeriesPrivateRoot {param($Path)if(-not [IO.Directory]::Exists($Path)){throw 'missing fixture private root'}}
function Copy-VerifiedPayload {param($Record)
 if(-not [IO.Directory]::Exists($script:cacheRoot)){throw 'bytes before private root'}
 $raw=[IO.File]::ReadAllBytes($Record.source)
 if($raw.Length -ne $Record.length -or (Get-FileHash -LiteralPath $Record.source).Hash.ToLowerInvariant() -cne $Record.sha256){throw 'source changed'}
 $out=[IO.File]::Open($Record.destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$out.Write($raw,0,$raw.Length)}finally{$out.Dispose()};$script:copies++
}
function Assert-ObserverInstalledSources {param($Records)foreach($r in $Records){if((Get-FileHash -LiteralPath $r.destination).Hash.ToLowerInvariant() -cne $r.sha256){throw 'destination bytes differ'}};$script:events.Add('destination-rehashed');$Records.Count}
function Write-RegistrationControl {param($Path,$Text)
 $raw=[Text.UTF8Encoding]::new($false).GetBytes($Text);$out=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$out.Write($raw,0,$raw.Length)}finally{$out.Dispose()};(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant()
}
$records=[Collections.Generic.List[object]]::new()
foreach($name in @('one.py','two.py','three.json')){$source=Join-Path $fixture $name;[IO.File]::WriteAllText($source,('exact fixture '+$name));$records.Add([pscustomobject]@{source=$source;destination=(Join-Path $packageRoot $name);length=(Get-Item -LiteralPath $source).Length;sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()})}
$folder=New-TestFolder;$scheduler=New-TestScheduler;$proof=Fixture-Proof;$runtime=Fixture-Runtime;$dependencies=[pscustomobject]@{complete_custody_deferred=$false}
'''


def test_real_control_copy_and_full_disabled_registration_flow(tmp_path):
    result = run_ps(temp_flow(tmp_path) + r'''
$r=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies
@{status=$r.Value.status;copies=$copies;created=$folder.Created;runs=$folder.Tasks[$taskName].Runs;intent=[IO.File]::Exists((Join-Path $targetRoot 'registration-intent.json'));events=@($events);preserved=[IO.File]::ReadAllText((Join-Path $packageRoot 'one.py'))}|ConvertTo-Json -Depth 5
''')
    assert result['status'] == 'RESOURCE_OBSERVER_REGISTERED_DISABLED'
    assert result['copies'] == 4 and result['created'] == 1 and result['runs'] == 0
    assert result['intent'] and result['preserved'] == 'exact fixture one.py'
    assert result['events'].index('private-directory-before-bytes') < result['events'].index('destination-rehashed')


@pytest.mark.parametrize('collision', ['code', 'private', 'task'])
def test_existing_namespaces_refuse_before_any_copy_or_registration(tmp_path, collision):
    mutation = {'code': '$null=[IO.Directory]::CreateDirectory($targetRoot)',
                'private': '$null=[IO.Directory]::CreateDirectory($stateRoot)',
                'task': '$folder.Tasks[$taskName]=New-TestTask (New-TestDefinition)'}[collision]
    result = run_ps(temp_flow(tmp_path) + mutation + r'''
$refused=$false;try{$null=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies}catch{$refused=$true};@{refused=$refused;copies=$copies;created=$folder.Created}|ConvertTo-Json
''')
    assert result == {'refused': True, 'copies': 0, 'created': 0}


def test_registration_then_single_start_has_durable_intent_and_refuses_repeat(tmp_path):
    result = run_ps(temp_flow(tmp_path) + r'''
$r=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies
$v=Fixture-Samples
function Read-ObserverFirstLine {param($Path)if($Path.EndsWith('native-boundaries.jsonl')){return $script:v.Native};$script:v.Sample}
$started=Invoke-ObserverStart $folder $proof $r
$refused=$false;try{$null=Invoke-ObserverStart $folder $proof $r}catch{$refused=$true}
@{status=$started.status;runs=$folder.Tasks[$taskName].Runs;repeat_refused=$refused;intent=[IO.File]::Exists((Join-Path $targetRoot 'start-intent.json'));result=[IO.File]::Exists((Join-Path $targetRoot 'observer-started.json'))}|ConvertTo-Json
''')
    assert result['status'] == 'RESOURCE_OBSERVER_RUNNING_INITIAL_MEASUREMENTS_VERIFIED'
    assert result['runs'] == 1 and result['repeat_refused'] and result['intent'] and result['result']


def test_run_failure_preserves_intent_and_produces_sanitized_task_receipt(tmp_path):
    result = run_ps(temp_flow(tmp_path) + r'''
$r=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies
$folder.Tasks[$taskName]|Add-Member -Force ScriptMethod Run {param($x)throw 'raw-sensitive-looking-fixture'}
try{$null=Invoke-ObserverStart $folder $proof $r}catch{}
function Read-R3Control {param($Path,$Hash,$Maximum)throw 'No bootstrap receipt'}
$failure=Write-ObserverStartFailure $folder $proof $r
@{status=$failure.status;intent=[IO.File]::Exists((Join-Path $targetRoot 'start-intent.json'));receipt=[IO.File]::ReadAllText((Join-Path $targetRoot 'observer-start-failure.json'));runs=$folder.Tasks[$taskName].Runs}|ConvertTo-Json -Depth 5
''')
    assert result['status'] == 'RESOURCE_OBSERVER_START_HELD' and result['intent']
    assert 'raw-sensitive' not in result['receipt'] and result['runs'] == 0


@pytest.mark.parametrize('mutation', ['', 'state', 'receipt', 'task', 'intent', 'prior-failure'])
def test_existing_disabled_registration_requires_exact_receipts_and_never_started_state(tmp_path, mutation):
    changes = {
        '': '',
        'state': "[IO.File]::WriteAllText((Join-Path $cacheRoot 'untrusted.pyc'),'preserve')",
        'receipt': "$v=Get-Content -LiteralPath (Join-Path $targetRoot 'observer-registration.json') -Raw|ConvertFrom-Json;$v.commissioning_sha256='changed';[IO.File]::WriteAllText((Join-Path $targetRoot 'observer-registration.json'),($v|ConvertTo-Json -Depth 8))",
        'task': '$folder.Tasks[$taskName].LastTaskResult=0',
        'intent': "[IO.File]::WriteAllText((Join-Path $targetRoot 'start-intent.json'),'preserve')",
        'prior-failure': "[IO.File]::WriteAllText((Join-Path $targetRoot 'observer-start-failure.json'),'preserve')",
    }
    result = run_ps(temp_flow(tmp_path) + r'''
$r=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies
function Read-R3Control {param($Path,$Hash,$Maximum)
 $actual=(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant();if($Hash -and $actual -cne $Hash){throw 'hash mismatch'}
 [pscustomobject]@{Stream=[IO.File]::OpenRead($Path);Sha256=$actual;Length=(Get-Item -LiteralPath $Path).Length}
}
function Read-R3Text {param($Control)$Control.Stream.Position=0;$reader=[IO.StreamReader]::new($Control.Stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$reader.ReadToEnd()}finally{$reader.Dispose()}}
''' + changes[mutation] + r'''
$refused=$false;try{$verified=Read-ObserverRegistration $folder $proof $records}catch{$refused=$true}
@{refused=$refused;runs=$folder.Tasks[$taskName].Runs;created=$folder.Created}|ConvertTo-Json
''')
    assert result == {'refused': bool(mutation), 'runs': 0, 'created': 1}


def test_actual_seven_source_inventory_builds_typed_copy_records_and_holds_files():
    manifest = HERE / 'resource-observer-r3-v2/source-manifest.json'
    pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
    body = FAKES + '\nforeach($text in Definitions ' + quote(PAYLOAD) + ") { if($text -match '^function (Assert-NoReparseAncestors|Initialize-FileIdentity|Open-VerifiedFile)\\b'){. ([scriptblock]::Create($text))}}\n"
    body += r'''
Initialize-FileIdentity
function Read-R3Text {param($Control)$Control.Stream.Position=0;$reader=[IO.StreamReader]::new($Control.Stream,[Text.Encoding]::UTF8,$true,4096,$true);try{$reader.ReadToEnd()}finally{$reader.Dispose()}}
'''
    body += '$sourceRoot=' + quote(manifest.parent) + ';$sourceManifest=' + quote(manifest) + ';$sourceManifestHash=' + quote(pin) + '\n'
    body += r'''
try{$rows=Get-ObserverInventory;@{count=$rows.Count;held=$held.Count;typed=@($rows|Where-Object{$_ -is [pscustomobject] -and $_.source.Length -gt 3 -and $_.sha256.Length -eq 64}).Count}|ConvertTo-Json}finally{foreach($s in $held){$s.Dispose()}}
'''
    result = run_ps(body)
    assert result == {'count': 8, 'held': 8, 'typed': 8}


def test_binding_failure_prevents_registration_and_new_roots(tmp_path):
    result = run_ps(temp_flow(tmp_path) + r'''
function Assert-CommissionedTaskRunning {param($Folder,$Proof)throw 'Fixture wrong Warden instance'}
$refused=$false;try{$null=Invoke-ObserverRegistration $scheduler $folder $runtime $proof $records $dependencies}catch{$refused=$true}
@{refused=$refused;copies=$copies;created=$folder.Created;code_exists=[IO.Directory]::Exists($targetRoot);state_exists=[IO.Directory]::Exists($stateRoot)}|ConvertTo-Json
''')
    assert result == {'refused': True, 'copies': 0, 'created': 0, 'code_exists': False, 'state_exists': False}
