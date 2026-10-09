"""Disposable lock/SQLite and simulated scheduler tests; no worker or task runs."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
SOURCE = HERE/'run-oracle-native-acceptance-r3-v1.py'
WRAPPER = HERE/'check-oracle-native-acceptance-r3-v1.ps1'
spec = importlib.util.spec_from_file_location('oracle_operational_candidate', SOURCE)
M = importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
spec = importlib.util.spec_from_file_location('oracle_registration_fixtures', HERE/'test_stopped_warden_registration.py')
R = importlib.util.module_from_spec(spec);spec.loader.exec_module(R)


@pytest.fixture
def native_lock_boundary(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(M.INSTALL/'.venv/Lib/site-packages'))
    from cochem_pipeline import windows
    # Only custody is replaced for this exact new ordinary test directory. The
    # native CreateFile/CRT nonblocking byte-lock and close behavior are real.
    def exact_directory(path): assert path == tmp_path
    def exact_file(path): assert path == tmp_path/'warden.lock'
    return SimpleNamespace(validate_private_directory=exact_directory, validate_private_path=exact_file,
        _api=windows._api, _close=windows._close, _acl=lambda path: ('fixture-owner', True, ())), tmp_path


@pytest.mark.parametrize('existing', [None, b'0', b'unchanged-lock-bytes'])
def test_native_service_lock_preserves_bytes_and_excludes_second_opener(native_lock_boundary, existing):
    win, root = native_lock_boundary
    path = root/'warden.lock'
    if existing is not None: path.write_bytes(existing)
    with M.maintained_lock(path, win, expected_parent=root) as value:
        identity = (path.stat().st_dev, path.stat().st_ino)
        assert value['created_new'] == (existing is None)
        with pytest.raises(OSError):
            with M.maintained_lock(path, win, expected_parent=root):
                raise AssertionError('exclusive lock must prevent entry')
    assert value['bytes_and_identity_preserved'] is True
    assert path.read_bytes() == (existing if existing is not None else b'0')
    assert (path.stat().st_dev, path.stat().st_ino) == identity
    # The one-shot context releases its handle without deleting the file.
    with M.maintained_lock(path, win, expected_parent=root) as again:
        assert not again['created_new']


def test_empty_existing_lock_is_not_initialized_or_truncated(native_lock_boundary):
    win, root = native_lock_boundary
    path = root/'warden.lock';path.write_bytes(b'')
    with pytest.raises(ValueError, match='SERVICE_LOCK_SHAPE'):
        with M.maintained_lock(path, win, expected_parent=root): pass
    assert path.read_bytes() == b''


def test_actual_installed_daemon_service_lock_is_excluded(native_lock_boundary):
    from cochem_pipeline.__main__ import service_lock
    win, root = native_lock_boundary
    path=root/'warden.lock';path.write_bytes(b'original')
    with M.maintained_lock(path,win,expected_parent=root):
        with pytest.raises(OSError):
            with service_lock(path): raise AssertionError('Production daemon entered maintenance')
    assert path.read_bytes()==b'original'
    with service_lock(path):
        with pytest.raises(OSError):
            with M.maintained_lock(path,win,expected_parent=root): raise AssertionError('Fixture entered active daemon')
    assert path.read_bytes()==b'original'


def test_existing_hardlink_refused_without_changing_either_path(native_lock_boundary):
    win, root = native_lock_boundary
    source = root/'original';source.write_bytes(b'keep')
    os.link(source, root/'warden.lock')
    with pytest.raises(Exception):
        with M.maintained_lock(root/'warden.lock', win, expected_parent=root): pass
    assert source.read_bytes() == b'keep'


def test_direct_daemon_census_refuses_or_bounds_without_exposing_argv(monkeypatch):
    import psutil
    safe = SimpleNamespace(info={'pid': 12, 'name': 'python.exe'}, cmdline=lambda: ['python.exe', '-I', 'safe.py'])
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([safe]))
    assert M.daemon_census()['direct_daemons'] == 0
    safe.cmdline = lambda: ['python.exe', '-m', 'cochem_pipeline', 'daemon', '--config', 'secret-not-returned']
    with pytest.raises(ValueError, match='^DIRECT_DAEMON_PRESENT$'): M.daemon_census()
    def denied(): raise psutil.AccessDenied(12)
    safe.cmdline = denied
    with pytest.raises(ValueError, match='^PROCESS_CENSUS_DENIED$'): M.daemon_census()
    safe.cmdline = lambda: ['python.exe','-m','cochem_pipeline.__main__','daemon']
    with pytest.raises(ValueError,match='^DIRECT_DAEMON_PRESENT$'): M.daemon_census()
    safe.cmdline = lambda: ['python.exe','-m','cochem_supervisor','run']
    with pytest.raises(ValueError,match='^DIRECT_SUPERVISOR_PRESENT$'): M.daemon_census()


def test_support_pins_and_core_entry_remain_exact():
    for name, pin in M.PINS.items(): assert hashlib.sha256((HERE/name).read_bytes()).hexdigest() == pin
    path = M.INSTALL/'.venv/Lib/site-packages/cochem_pipeline/__main__.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == M.DAEMON_SOURCE_SHA


def test_create_new_receipt_never_replaces_previous(tmp_path):
    path = tmp_path/'receipt.json';M.write_new(path, {'status': 'held'})
    before = path.read_bytes()
    with pytest.raises(FileExistsError): M.write_new(path, {'status': 'pass'})
    assert path.read_bytes() == before


def test_preserved_commissioning_derives_action_only_from_bound_success():
    packet = {'schema': 'cochem-warden-commissioning-inputs/1', 'nonce': 'a'*32, 'config_sha256': M.CONFIG_SHA}
    raw = json.dumps(packet).encode();digest = hashlib.sha256(raw).hexdigest()
    receipt = dict(schema='cochem-warden-commissioning/1', status='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',
        nonce=packet['nonce'], input_sha256=digest, helper_sha256=M.COMMISSION_SHA, system_sid='S-1-5-18',
        runtime_root=str(M.INSTALL), config_sha256=M.CONFIG_SHA, exactly_one_start_requested=True, automatic_retry_allowed=False)
    requested=[]
    def read(path,pin):
        requested.append((path,pin))
        if path.name=='inputs.json': return raw
        if path.name=='commissioning.json': return json.dumps(receipt).encode()
        assert pin in (M.COMMISSION_SHA,M.CONFIG_SHA)
        return b'already verified by held reader'
    assert M.commission_arguments(read).endswith('--nonce '+'a'*32+' --input-sha256 '+digest)
    assert len(requested)==4
    receipt['automatic_retry_allowed']=True
    with pytest.raises(ValueError,match='COMMISSION_RECEIPT'): M.commission_arguments(read)


def run_ps(body):
    old = R.SCRIPT;R.SCRIPT = WRAPPER
    try:
        return R.run_ps('foreach($text in Import-TestFunctions '+R.quote(HERE/'register-stopped-warden-r3.ps1')+') {. ([scriptblock]::Create($text))}\n'+body)
    finally: R.SCRIPT = old


SETUP = R.FAKES + r'''
$daemonNames=@('CoChem-4.2.7-Warden','CoChem-4.2.7-Supervisor','CoChem-4.2.2-Warden','CoChem-4.2.3-Supervisor','CoChem-4.2.7-WardenCommissioning-r3-v1')
$daemonArguments='exact daemon arguments';$folder=New-TestFolder
$d=New-TestDefinition;$a=$d.Actions.Create(0);$a.Path=$python;$a.Arguments=$daemonArguments;$a.WorkingDirectory=$installRoot
$warden=New-TestTask $d;$folder.Tasks['CoChem-4.2.7-Warden']=$warden
'''


@pytest.mark.parametrize('change', ['', '$warden.Enabled=$true', '$warden.State=3', '$warden.Instances=1',
    '$warden.Definition.Settings.RestartCount=1', '$warden.Definition.Actions.Value.Arguments="wrong"',
    '$folder.Tasks["CoChem-4.2.7-WardenCommissioning-r3-v1"]=New-TestTask (New-TestDefinition)'])
def test_real_maintenance_function_requires_exact_disabled_definition(change):
    result = run_ps(SETUP+change+r'''
$refused=$false;$rows=$null;try{$rows=Get-OracleMaintenance $folder}catch{$refused=$true}
@{refused=$refused;rows=$rows}|ConvertTo-Json -Depth 6
''')
    assert result['refused'] == bool(change)
    if not change: assert len(result['rows']) == 5 and not result['rows'][0]['absent']


@pytest.mark.parametrize('hresult,missing', [(-2147024894, True), (-2147216625, False), (-2147024891, False)])
def test_task_absence_is_only_exact_not_found(hresult, missing):
    result = run_ps(f'''
$f=[pscustomobject]@{{}};$f|Add-Member ScriptMethod GetTask {{param($name)throw [Runtime.InteropServices.COMException]::new('fixture',{hresult})}}
$refused=$false;try{{$value=Get-OracleTask $f 'name';$absent=$null -eq $value}}catch{{$refused=$true;$absent=$false}}
@{{refused=$refused;absent=$absent}}|ConvertTo-Json
''')
    assert result['absent'] == missing and result['refused'] != missing


def test_python_task_guard_matches_ps_definition_contract():
    # Exercise the embedded script with inert scheduler via a local New-Object
    # replacement, not an operational PowerShell boundary or real task lookup.
    source = SETUP + r'''
function New-Object {param([string]$ComObject)$script:scheduler}
$scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod Connect {};$scheduler|Add-Member ScriptMethod GetFolder {param($path)$script:folder}
$data=@{names=$daemonNames;python=$python;install=$installRoot;arguments=$daemonArguments;commission_arguments=$null}
'''
    result = run_ps(source+M.TASK_SCRIPT)
    assert len(result) == 5 and result[0]['name'] == 'CoChem-4.2.7-Warden'


@pytest.mark.parametrize('change', ['', '$commission.Enabled=$true', '$commission.LastTaskResult=2', '$commission.Instances=1'])
def test_preserved_commission_task_supported_only_after_verified_disabled_maintenance(change):
    result = run_ps(SETUP+r'''
function Get-OracleCommissionArguments {'bound commission arguments'}
$d=New-TestDefinition;$d.Settings.ExecutionTimeLimit='PT8M';$a=$d.Actions.Create(0);$a.Path=$python;$a.Arguments='bound commission arguments';$a.WorkingDirectory=$installRoot
$commission=New-TestTask $d;$commission.LastTaskResult=0;$folder.Tasks['CoChem-4.2.7-WardenCommissioning-r3-v1']=$commission
'''+change+r'''
$refused=$false;try{$rows=Get-OracleMaintenance $folder}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert result['refused']==bool(change)


def flow(tmp_path):
    # Actual exclusive source copies and packet writer; only native ownership,
    # privileged destination and scheduler effects are explicitly substituted.
    names = ['run-oracle-native-acceptance-r3-v1.py', *M.PINS]
    for name in names: (tmp_path/name).write_bytes(('# inert '+name+'\n').encode())
    rows = [{'name': name, 'sha256': hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()} for name in names]
    return SETUP+f'''
$sourceRoot={R.quote(tmp_path)};$targetRoot={R.quote(tmp_path/'fresh')};$taskName='Fixture-Oracle';$sourceHash='{rows[0]['sha256']}'
$sources=({R.quote(json.dumps(rows))}|ConvertFrom-Json);$held=[Collections.Generic.List[IO.FileStream]]::new()
$installHash='a'*64;$configHash='b'*64;$manifestHash='c'*64;$revisionHash='d'*64;$testRuntime=[pscustomobject]@{{install_receipt_sha256=$installHash}}
foreach($text in Get-ReviewedRegistrationFunctions {R.quote(R.PAYLOAD)} '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Copy-VerifiedPayload','Open-VerifiedFile','Initialize-FileIdentity','Assert-NoReparseAncestors')){{. ([scriptblock]::Create($text))}}
Initialize-FileIdentity
function Assert-ProtectedPath([string]$Path){{}}
function New-CodeAcl([bool]$Directory){{Get-Acl -LiteralPath {R.quote(tmp_path/names[0])}}}
function New-ProtectedDirectory([string]$Path){{$null=[IO.Directory]::CreateDirectory($Path)}}
function Assert-CodeTreeOnce([string]$Path){{1}}
function Assert-R3InstalledBindings{{$testRuntime}}
function Grant-OracleFixtureRead([string]$Path,[string]$Sid,[bool]$Directory){{if($Sid -notlike 'S-1-5-21-*'){{throw 'SID'}}}}
function Read-R3Control([string]$Path,[string]$Pin,[int]$Maximum){{
 if($Path -like '*pipeline.json'){{
  $configuration=@{{workers=@{{slot1=@{{name=[Security.Principal.WindowsIdentity]::GetCurrent().Name}}}}}}
  return [pscustomobject]@{{Text=($configuration|ConvertTo-Json -Depth 5)}}
 }}
 [pscustomobject]@{{Text=[IO.File]::ReadAllText($Path);Sha256=(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant()}}
}}
function Read-R3Text($Control){{$Control.Text}}
$scheduler=New-TestScheduler;$script:runs=0;$script:waits=0
$script:messages=[Collections.Generic.List[string]]::new()
function Write-Host([string]$Object){{$script:messages.Add($Object)}}
$scheduler|Add-Member -Force ScriptMethod NewTask {{param($flags)
 $d=New-TestDefinition;$d.Settings|Add-Member NoteProperty DisallowStartIfOnBatteries $false;$d.Settings|Add-Member NoteProperty StopIfGoingOnBatteries $false;$d
}}
$folder|Add-Member -Force ScriptMethod RegisterTaskDefinition {{param($name,$d,$flags,$user,$password,$logon,$sddl)
 if($flags -ne 2 -or $d.Settings.Enabled -ne $false -or $this.Tasks.ContainsKey($name)){{throw 'unsafe registration'}}
 $this.Created++;$t=New-TestTask $d
 $t.PSObject.Properties.Remove('Enabled');$t|Add-Member ScriptProperty Enabled {{$this.Definition.Settings.Enabled}} {{$this.Definition.Settings.Enabled=$args[0]}}
 $t|Add-Member ScriptMethod Run {{param($nothing)$script:runs++;[pscustomobject]@{{State=4}}}}
 $this.Tasks[$name]=$t;$t
}}
function Wait-OracleTask($Instance){{
 $script:waits++;$t=$folder.Tasks[$taskName];$t.State=3;$t.LastTaskResult=0
 $packet=[IO.File]::ReadAllText((Join-Path $targetRoot 'inputs.json'))|ConvertFrom-Json
 $r=[ordered]@{{schema='cochem-oracle-native-acceptance/1';nonce=$packet.nonce;packet_sha256=(Get-FileHash -LiteralPath (Join-Path $targetRoot 'inputs.json')).Hash.ToLowerInvariant();helper_sha256=$sourceHash;system_sid='S-1-5-18';runtime_root=$installRoot;
 status='ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED';full_service_acceptance=$false;automatic_retry_allowed=$false;daemon_tasks_modified=$false;daemon_stop_requested=$false;production_databases_modified=$false;model_jobs_executed=0;partial_outputs_preserved=$true;
 install_receipt_sha256=$installHash;config_sha256=$configHash;source_manifest_sha256=$manifestHash;revision_sha256=$revisionHash;ram_baseline_preserved=$true;maintenance_lock=@{{bytes_and_identity_preserved=$true;exclusive_byte_zero_lock=$true;created_new=$false}};
 component=@{{scope='isolated_oracle_runtime_trip_method_integration';runtime_trip_fencing_verified=$true;native_cleanup_verified=$true;job_active_processes=0;durable_fencing=@{{status='FAILED';stale_completion_rejected_without_mutation=$true}}}}}}
 $null=Write-RegistrationControl (Join-Path $targetRoot 'oracle-native-acceptance.json') ($r|ConvertTo-Json -Depth 8)
}}
$baseline=Get-OracleMaintenance $folder
'''


def test_entire_apply_real_five_exclusive_copies_and_one_simulated_task(tmp_path):
    result = run_ps(flow(tmp_path)+r'''
try{$result=Invoke-OracleAcceptance $scheduler $folder $testRuntime $baseline;@{result=$result;runs=$runs;waits=$waits;created=$folder.Created;files=@(Get-ChildItem -LiteralPath $targetRoot|ForEach-Object Name)}|ConvertTo-Json -Depth 8}finally{foreach($s in $held){$s.Dispose()}}
''')
    assert result['runs'] == result['waits'] == result['created'] == 1
    assert result['result']['status'] == 'ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED'
    for name in ['run-oracle-native-acceptance-r3-v1.py', *M.PINS]:
        assert (tmp_path/name).read_bytes() == (tmp_path/'fresh'/name).read_bytes()


@pytest.mark.parametrize('phase', ['maintenance', 'source_drift', 'registration', 'wait', 'missing_receipt'])
def test_apply_failure_preserves_partial_and_never_retries(tmp_path, phase):
    changes = {'maintenance': '$warden.Enabled=$true',
       'source_drift': "[IO.File]::AppendAllText((Join-Path $sourceRoot $sources[0].name),'drift')",
       'registration': "$folder|Add-Member -Force ScriptMethod RegisterTaskDefinition {throw 'fixture refusal'}",
       'wait': "function Wait-OracleTask($Instance){throw 'fixture timeout secret'}",
       'missing_receipt': "function Wait-OracleTask($Instance){$t=$folder.Tasks[$taskName];$t.State=3;$t.LastTaskResult=2}"}
    result = run_ps(flow(tmp_path)+changes[phase]+r'''
try{$refused=$false;try{Invoke-OracleAcceptance $scheduler $folder $testRuntime $baseline|Out-Null}catch{$refused=$true};@{refused=$refused;runs=$runs;exists=(Test-Path -LiteralPath $targetRoot);messages=@($messages)}|ConvertTo-Json}finally{foreach($s in $held){$s.Dispose()}}
''')
    assert result['refused'] and result['runs'] == int(phase in {'wait','missing_receipt'})
    assert result['exists'] == (phase in {'registration', 'wait','missing_receipt'})
    if phase in {'wait','missing_receipt'}:
        report=json.loads((tmp_path/'fresh'/'wrapper-failure.json').read_text(encoding='utf-8-sig'))
        assert report['phase']==('wait_terminal' if phase=='wait' else 'read_receipt')
        assert report['automatic_retry_allowed'] is False
        assert 'secret' not in json.dumps(result) and result['messages']
