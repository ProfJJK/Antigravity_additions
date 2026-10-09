"""Ordinary Windows fixtures only. No task/provider/Docker/controller execution."""
import base64
import ast
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import types
import uuid

import pytest

HERE=Path(__file__).parent
SCRIPT=HERE/'commission-first-warden-r3-v1.py'
WRAPPER=HERE/'commission-first-warden-r3-v1.ps1'
PS=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
spec=importlib.util.spec_from_file_location('first_start',SCRIPT);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
spec=importlib.util.spec_from_file_location('registration_tests',HERE/'test_stopped_warden_registration.py');R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)

def auth_fixture():
    controls={};providers=[]
    for provider in ('codex','claude'):
        receipts=[]
        for i in range(1,7):
            slot=f'slot{i}';stage='before' if i%2 else 'after'
            path=M.ROOT.parent/f'NativeAuthStatusSix4.2.7-windows-20261007-r3-v1-{slot}-{provider}-{stage}'/'worker-native-status.json'
            leaf=dict(schema='cochem-worker-native-auth-status/1',status='NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED',decision='REUSE_VERIFIED_SESSION',provider=provider,slot=slot,stage=stage,
             system_sid='S-1-5-18',helper_sha256=M.AUTH_HELPER_HASH,runtime_root=str(M.INSTALL),install_receipt_sha256=M.INSTALL_HASH,source_manifest_sha256=M.MANIFEST_HASH,
             config_sha256=M.CONFIG_HASH,layout_sha256=M.LAYOUT_HASH,cleanup_verified=True,runtime_custody_verified=True,daemon_states_verified_before_and_after=True,native_status_commands_executed=1,native_model_jobs_executed=0,login_commands_executed=0,native_exit_code=0,
             revision={'source_sha256':M.REVISION_HASH},authentication={'native_subscription_authentication_verified':True},process={k:True for k in ('token_matches_selected_worker','image_matches_reviewed_executable','owned_job_membership_verified')})
            controls[path]=(leaf,'a'*64)
            receipts.append({'slot':slot,'status':'REUSED_VERIFIED_SESSION' if stage=='before' else 'AUTHENTICATED_AFTER_LOGIN','proof':{'stage':stage,'decision':'REUSE_VERIFIED_SESSION','receipt_path':str(path),'receipt_sha256':'a'*64}})
        path=M.ROOT.parent/f'NativeAuthSix4.2.7-windows-20261007-r3-v1-{provider}'/'series-complete.json'
        value={'schema':'cochem-six-worker-status-first-result/1','status':'SIX_CONFIGURED_WORKERS_SUBSCRIPTION_AUTHENTICATION_VERIFIED','provider':provider,'selected_workers_verified':6,
           'configuration_applied':False,'model_jobs_executed':0,'activation_ready':False,'series_preserved':True,'runtime':{'install_receipt_sha256':M.INSTALL_HASH,'configuration_sha256':M.CONFIG_HASH,'revision':{'source_sha256':M.REVISION_HASH}},
           'login_commands_executed':3,'existing_sessions_reused':3,'status_receipts':receipts}
        controls[path]=(value,'b'*64);providers.append({'provider':provider,'receipt_path':str(path),'receipt_sha256':'b'*64})
    outer={'schema':'cochem-both-providers-status-first-result/1','status':'CODEX_AND_CLAUDE_SIX_PROFILE_AUTHENTICATION_VERIFIED','model_jobs_executed':0,'configuration_changed':False,'activation_ready':False,
           'agy_integration_hold_preserved':True,'series_preserved':True,'nonce':'c'*32,'provider_results':providers}
    controls[M.AUTH_ROOT/'series-complete.json']=(outer,'d'*64)
    return controls

def reader(controls):
    def read(path,pin):
        value,actual=controls[path]
        assert pin is None or pin==actual
        return value,actual
    return read

def test_auth_chain_covers_twelve_actual_shaped_slot_proofs():
    result=M.validate_auth(reader(auth_fixture()))
    assert len(result['profiles'])==12 and result['receipt_sha256']=='d'*64

@pytest.mark.parametrize('field,value',[('cleanup_verified',False),('native_exit_code',1),('helper_sha256','f'*64),('config_sha256','f'*64),('native_model_jobs_executed',1),('daemon_states_verified_before_and_after',False),('native_status_commands_executed',True)])
def test_bad_auth_leaf_prevents_first_start(field,value):
    controls=auth_fixture();next(iter(controls.values()))[0][field]=value
    with pytest.raises(ValueError):M.validate_auth(reader(controls))

def test_duplicate_profile_and_alternate_receipt_path_refused():
    controls=auth_fixture();path=M.ROOT.parent/'NativeAuthSix4.2.7-windows-20261007-r3-v1-codex/series-complete.json'
    controls[path][0]['status_receipts'][5]=copy.deepcopy(controls[path][0]['status_receipts'][0])
    with pytest.raises(ValueError):M.validate_auth(reader(controls))

def test_start_intent_is_durable_before_one_callback_and_blocks_retry(tmp_path):
    path=tmp_path/'start-intent.json';calls=[]
    def ambiguous():
        assert json.loads(path.read_bytes())=={'intent':True};calls.append('start');raise TimeoutError('simulated ambiguous task response')
    with pytest.raises(TimeoutError):M.start_once(path,{'intent':True},ambiguous)
    with pytest.raises(FileExistsError):M.start_once(path,{'intent':True},ambiguous)
    assert calls==['start'] and path.is_file()

@pytest.mark.parametrize('conflict',[None,0,3,5])
def test_six_production_exclusion_names_refuse_existing_and_release_all(conflict):
    created=[];closed=[];entered=[]
    def create(_security,initial,name):
        assert initial is False
        created.append(name)
        if len(created)-1==conflict:ctypes.set_last_error(183)
        return len(created)
    win=types.SimpleNamespace(_account_sid=lambda name:name,_sid_text=lambda name:'fixture-'+name,
        _api=lambda:{'kernel32':types.SimpleNamespace(CreateMutexW=create)},_check=lambda value,_:value,_close=closed.append)
    workers={f'slot{i}':{'name':f'worker{i}'} for i in range(1,7)}
    if conflict is None:
        with pytest.raises(RuntimeError):
            with M.reserve_workers(win,workers):entered.append(True);raise RuntimeError('inert downstream failure')
        assert entered==[True] and len(created)==6
    else:
        with pytest.raises(ValueError):
            with M.reserve_workers(win,workers):entered.append(True)
        assert entered==[] and len(created)==conflict+1
    assert created==['Global\\CoChemPipeline422-fixture-worker'+str(i) for i in range(1,len(created)+1)]
    assert closed==list(range(len(created),0,-1))

def test_actual_windows_named_object_collision_and_cleanup_only_fixture_names():
    sys.path.insert(0,str(M.INSTALL/'.venv/Lib/site-packages'))
    try:from cochem_pipeline import windows as win
    finally:sys.path.pop(0)
    # Native CreateMutex/CloseHandle under this ordinary account; unique fake
    # SID suffixes can never collide with the six real provisioned worker SIDs.
    prefix='fixture-'+uuid.uuid4().hex+'-'
    fixture=types.SimpleNamespace(_api=win._api,_check=win._check,_close=win._close,
        _account_sid=lambda name:name,_sid_text=lambda name:prefix+name)
    workers={f'slot{i}':{'name':f'worker{i}'} for i in range(1,7)}
    with pytest.raises(RuntimeError):
        with M.reserve_workers(fixture,workers):
            with pytest.raises(ValueError,match='WORKER_ALREADY_ACTIVE'):
                with M.reserve_workers(fixture,workers):pytest.fail('native collision allowed')
            raise RuntimeError('inert downstream failure releases every held object')
    with M.reserve_workers(fixture,workers):pass  # All six names disappeared.

def test_empty_directory_includes_hidden_entry_and_never_deletes(tmp_path):
    result=M.empty_directory(tmp_path,lambda _:None);assert result['file_id']==tmp_path.stat().st_ino
    (tmp_path/'.keep').write_text('preserve')
    with pytest.raises(ValueError):M.empty_directory(tmp_path,lambda _:None)
    assert (tmp_path/'.keep').read_text()=='preserve'

def physical_fixture(tmp_path):
    raw=(HERE/'accept-docker-execution-r3-v2.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='157549c5806e5486085d0d941ca619c5218e668653309ce6bd55dc75eb4a944f'
    tree=ast.parse(raw);test_raw=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TEST_SOURCE' for t in n.targets))
    root=tmp_path/'source';root.mkdir();receipt={'executions':[]}
    for phase,number in [('red',41),('green',42)]:
        folder=root/phase/'tests';folder.mkdir(parents=True)
        raw_files={'tests/business.py':f'def answer(): return {number}\n'.encode(),'tests/test_contract.py':test_raw}
        entries={}
        for name,data in raw_files.items():
            (root/phase/name).write_bytes(data);entries[name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'executable':False}
        receipt['executions'].append({'phase':phase,'source_sha256':hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),'input_and_output_source_verified':True})
    sys.path.insert(0,str(M.INSTALL/'.venv/Lib/site-packages'))
    try:from cochem_pipeline import windows as win
    finally:sys.path.pop(0)
    def boundary(path,*args,**kwargs):assert Path(path).is_relative_to(tmp_path)
    fixture_win=types.SimpleNamespace(_api=win._api,_close=win._close,_account_sid=lambda _:None,_sid_text=lambda _:'fixture',
       _validate_worker_directory=boundary,validate_private_directory=boundary,validate_private_path=boundary)
    return root,receipt,fixture_win

def test_exact_docker_sources_preserved_create_new_before_normal_runtime_cleanup(tmp_path):
    root,receipt,win=physical_fixture(tmp_path);destination=tmp_path/'retention'
    evidence=M.preserve_fixture(root,destination,receipt,win,lambda p:None)
    assert evidence['source_deleted_by_helper'] is False and evidence['files']==4
    for name,(pin,length) in M.FIXTURE_FILES.items():
        assert (root/name).read_bytes()==(destination/name).read_bytes()
        assert hashlib.sha256((destination/name).read_bytes()).hexdigest()==pin
    assert hashlib.sha256((destination/'preservation.json').read_bytes()).hexdigest()==evidence['manifest_sha256']
    with pytest.raises(ValueError):M.preserve_fixture(root,destination,receipt,win,lambda _:None)

@pytest.mark.parametrize('mutation',['extra','changed','receipt','partial'])
def test_preservation_never_starts_from_changed_or_partial_fixture(tmp_path,mutation):
    root,receipt,win=physical_fixture(tmp_path);destination=tmp_path/'retention'
    if mutation=='extra':(root/'.keep').write_text('never erase')
    elif mutation=='changed':(root/'red/tests/business.py').write_bytes(b'def answer(): return 99\n')
    elif mutation=='receipt':receipt['executions'][0]['source_sha256']='0'*64
    else:destination.mkdir();(destination/'partial').write_text('preserve')
    with pytest.raises(ValueError):M.preserve_fixture(root,destination,receipt,win,lambda _:None)
    assert root.is_dir()
    assert destination.exists()==(mutation=='partial')

def registry_bytes(tmp_path,status='REMOVED',uncertain=0,capacity='4',pending=False):
    path=tmp_path/'fixture.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE containers(status TEXT,creation_uncertain INTEGER);CREATE TABLE preparation_requests(sequence INTEGER);')
        db.executemany('INSERT INTO metadata VALUES(?,?)',[('owner','a'*32),('capacity',capacity),('unknown_owned','0')]);db.executemany('INSERT INTO containers VALUES(?,?)',[(status,uncertain),('REMOVED',0)])
        if pending:db.execute('INSERT INTO preparation_requests VALUES(1)')
    return path.read_bytes()

@pytest.mark.parametrize('kwargs',[{}, {'status':'QUARANTINED'},{'uncertain':1},{'capacity':'6'},{'pending':True}])
def test_closed_registry_buffer_validation_without_source_sqlite_reopen(tmp_path,kwargs):
    raw=registry_bytes(tmp_path,**kwargs);original=hashlib.sha256(raw).hexdigest();pin=hashlib.sha256(('a'*32).encode()).hexdigest()
    if kwargs:
        with pytest.raises(ValueError):M.inspect_registry_buffer(raw,pin)
    else:assert M.inspect_registry_buffer(raw,pin)['retained_removed_rows']==2
    assert hashlib.sha256((tmp_path/'fixture.db').read_bytes()).hexdigest()==original
    assert sorted(p.name for p in tmp_path.iterdir())==['fixture.db']

def health_fixture():
    beat={'schema':1,'timestamp':101,'process_started_at':100,'pid':42,'sequence':1,'instance_id':'c'*32,'version':'4.2.7','source_root':str(M.INSTALL/'.venv/Lib')}
    health={**beat,'service_identity':'SYSTEM','quarantined_slots':{},'active':[],'trip_errors':[],'knowledge':{'ready':True},'admission_capacity':4}
    return beat,health

@pytest.mark.parametrize('field,value',[('pid',44),('source_root',r'C:\other'),('knowledge',{'ready':False}),('active',[{'job_id':'unexpected'}]),('quarantined_slots',{'slot1':'held'}),('admission_capacity',5)])
def test_health_identity_and_workload_gates(field,value):
    beat,health=health_fixture();health[field]=value
    with pytest.raises(ValueError):M.health_sample(beat,health,100,101)

def test_healthy_progress_sample_has_only_safe_identity_fields():
    beat,health=health_fixture();result=M.health_sample(beat,health,100,101)
    assert result=={'pid':42,'instance_id':'c'*32,'sequence':1,'process_started_at':100}

def test_actual_windows_base_child_and_venv_parent_identity_only():
    # Actual unprivileged probe of the same Windows venv launcher mechanism;
    # expected SID is this ordinary test user, never claimed to be SYSTEM.
    sys.path.insert(0,str(M.INSTALL/'.venv/Lib/site-packages'))
    try:from cochem_pipeline import windows as win
    finally:sys.path.pop(0)
    started=time.time()
    body='import os,time;print(os.getpid(),flush=True);time.sleep(20)'
    with subprocess.Popen([str(M.PYTHON),'-I','-B','-c',body],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as proc:
        try:
            pid=int(proc.stdout.readline().strip())
            sid=win._sid_text(win._account_sid(os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']))
            child=M.one_process_proof(win,pid,started,M.BASE_PYTHON,sid)
            parent=M.one_process_proof(win,proc.pid,started,M.PYTHON,sid)
            assert child['creation_filetime']>=parent['creation_filetime'] and child['pid']!=parent['pid']
        finally:
            if 'pid' in locals():
                import psutil
                child=psutil.Process(pid);child.kill();child.wait(timeout=10)
            proc.kill();proc.wait(timeout=10)

def run_ps(body):
    original=R.SCRIPT;R.SCRIPT=WRAPPER
    try:return R.run_ps("foreach($text in Import-TestFunctions "+R.quote(HERE/'register-stopped-warden-r3.ps1')+"){. ([scriptblock]::Create($text))}\n"+body)
    finally:R.SCRIPT=original

def test_actual_config_and_uv_contract_unchanged():
    result=run_ps(R.FAKES+"\nforeach($text in Get-ReviewedRegistrationFunctions "+R.quote(R.LEAF)+" '18f58ebb448d8a0c6329dd187d4a9a27fa9cbe942cd05e906bb7aabc67e787a7' @('Assert-VenvBinding')){. ([scriptblock]::Create($text))}\n"+
       'Assert-RegistrationConfiguration ([IO.File]::ReadAllText('+R.quote(R.CONFIG)+'))\nAssert-VenvBinding ([IO.File]::ReadAllText('+R.quote(R.CONFIG.parent/'.venv/pyvenv.cfg')+'))\n@{accepted=$true}|ConvertTo-Json')
    assert result['accepted']

@pytest.mark.parametrize('mutation',['','$task.Enabled=$true','$task.State=3','$task.Instances=1','$task.Definition.Settings.RestartCount=1','$task.Definition.Actions.Value.Arguments="other"'])
def test_new_disabled_task_readback_real_acl_parser(mutation):
    result=run_ps(R.FAKES+r'''
$d=New-TestDefinition;$a=$d.Actions.Create(0);$a.Path=$python;$a.Arguments='expected';$a.WorkingDirectory=$installRoot;$task=New-TestTask $d
'''+mutation+r'''
$refused=$false;try{Assert-FirstStartTask $task $true 'expected'}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json
''')
    assert result['refused']==bool(mutation)

def flow(tmp_path):
    source=tmp_path/'source.py';source.write_bytes(b'# inert commissioning fixture\r\n')
    support=tmp_path/'support.py';support.write_bytes(b'# inert pinned support fixture\r\n')
    config=tmp_path/'source.json';config.write_bytes(R.CONFIG.read_bytes())
    return R.FAKES+f'''
$held=[Collections.Generic.List[IO.FileStream]]::new();$targetRoot={R.quote(tmp_path/'fresh')};$config={R.quote(config)};$configTarget=Join-Path $targetRoot 'pipeline.json'
$source={R.quote(source)};$sourceHash='{hashlib.sha256(source.read_bytes()).hexdigest()}';$pythonSupport={R.quote(support)};$pythonSupportHash='{hashlib.sha256(support.read_bytes()).hexdigest()}'
$configHash='{M.CONFIG_HASH}';$installHash='{M.INSTALL_HASH}';$manifestHash='{M.MANIFEST_HASH}';$revisionHash='{M.REVISION_HASH}'
$queueRoot='R:\\fixture';$commissionTask='Fixture-Commission';$testRuntime=[pscustomobject]@{{install_receipt_sha256=$installHash}}
foreach($text in Get-ReviewedRegistrationFunctions {R.quote(R.PAYLOAD)} '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Copy-VerifiedPayload','Open-VerifiedFile','Initialize-FileIdentity','Assert-NoReparseAncestors')){{. ([scriptblock]::Create($text))}}
Initialize-FileIdentity
function Assert-ProtectedPath([string]$Path){{}}
function New-CodeAcl([bool]$Directory){{Get-Acl -LiteralPath {R.quote(source)}}}
function New-ProtectedDirectory([string]$Path){{$null=[IO.Directory]::CreateDirectory($Path)}}
function Assert-CodeTreeOnce([string]$Path){{1}}
function Assert-R3InstalledBindings{{$testRuntime}}
function Read-FirstStartAuth{{'a'*64}}
function Read-R3Control([string]$Path,[string]$Pin,[int]$Maximum){{[pscustomobject]@{{Text=[IO.File]::ReadAllText($Path);Sha256=(Get-FileHash -LiteralPath $Path).Hash.ToLowerInvariant()}}}}
function Read-R3Text($Control){{$Control.Text}}
function Assert-CompletedStatusTask($Task){{if($Task.State -ne 3 -or $Task.GetInstances(0).Count -ne 0){{throw 'not terminal'}}}}
$script:runCount=0;$script:waited=0;$folder=New-TestFolder;$scheduler=New-TestScheduler
$folder|Add-Member -Force ScriptMethod RegisterTaskDefinition {{param($name,$d,$flags,$user,$password,$logon,$sddl)
 if($flags -ne 2 -or $d.Settings.Enabled -ne $false -or $this.Tasks.ContainsKey($name)){{throw 'unsafe registration'}}
 $this.Created++;$t=New-TestTask $d
 $t|Add-Member ScriptMethod Run {{param($nothing)
  $script:runCount++;if($this.Definition.Actions.Value.Arguments -notlike '*commission-first-warden-r3-v1.py*'){{throw 'Daemon run by wrapper'}}
  [pscustomobject]@{{State=4}}
 }}
 $this.Tasks[$name]=$t;$t
}}
function Wait-FirstStartTask($Instance){{
 $script:waited++;$task=$folder.Tasks[$commissionTask];$task.State=3;$task.LastTaskResult=0
 $packet=[IO.File]::ReadAllText((Join-Path $targetRoot 'inputs.json'))|ConvertFrom-Json
 $receipt=[ordered]@{{schema='cochem-warden-commissioning/1';nonce=$packet.nonce;system_sid='S-1-5-18';helper_sha256=$sourceHash;input_sha256=(Get-FileHash -LiteralPath (Join-Path $targetRoot 'inputs.json')).Hash.ToLowerInvariant();
 runtime_root=$installRoot;install_receipt_sha256=$installHash;config_sha256=$configHash;source_manifest_sha256=$manifestHash;revision_sha256=$revisionHash;task_name=$taskName;queue_launch_output=$queueRoot;
 model_jobs_submitted=0;full_srs_acceptance=$false;automatic_repair_enabled=$false;automatic_retry_allowed=$false;status='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED';exactly_one_start_requested=$true;authenticated_profiles_verified=12;monitoring_started=$true;monitoring_scope='heartbeat_and_queue_only';controller=@{{pid=42;creation_filetime=134000000000000000L;first_sequence=1;final_sequence=2;instance_id=('c'*32)}}}}
 [IO.File]::WriteAllText((Join-Path $targetRoot 'commissioning.json'),($receipt|ConvertTo-Json -Depth 8))
}}
'''

def test_entire_apply_function_real_three_payload_copies_simulated_scheduler(tmp_path):
    result=run_ps(flow(tmp_path)+r'''
try{$result=Invoke-FirstStart $scheduler $folder $testRuntime ('a'*64);@{result=$result;created=$folder.Created;runs=$runCount;waits=$waited;warden_disabled=(-not $folder.Tasks[$taskName].Enabled);files=@(Get-ChildItem -LiteralPath $targetRoot|ForEach-Object Name)}|ConvertTo-Json -Depth 12}finally{foreach($stream in $held){$stream.Dispose()}}
''')
    assert result['created']==2 and result['runs']==1 and result['waits']==1 and result['warden_disabled']
    assert result['result']['status']=='WARDEN_RUNNING_CONTROL_PLANE_VERIFIED'
    for source,dest in [('source.py','commission-first-warden-r3-v1.py'),('support.py','worker-native-status-r3.py'),('source.json','pipeline.json')]:
        assert (tmp_path/source).read_bytes()==(tmp_path/'fresh'/dest).read_bytes()

@pytest.mark.parametrize('phase',['prior_task','runtime','source_drift','registration','wait'])
def test_entire_apply_failure_never_retries_and_preserves_partial_state(tmp_path,phase):
    mutations={'prior_task':'$folder.Tasks[$taskName]=New-TestTask (New-TestDefinition)',
       'runtime':"function Assert-CodeTreeOnce([string]$Path){throw 'fixture custody refusal'}",
       'source_drift':"[IO.File]::AppendAllText($source,'changed')",'registration':"$folder|Add-Member -Force ScriptMethod RegisterTaskDefinition {throw 'fixture registration refusal'}",
       'wait':"function Wait-FirstStartTask($Instance){throw 'fixture ambiguous timeout'}"}
    result=run_ps(flow(tmp_path)+mutations[phase]+r'''
try{$refused=$false;try{Invoke-FirstStart $scheduler $folder $testRuntime ('a'*64)|Out-Null}catch{$refused=$true};@{refused=$refused;runs=$runCount;created=$folder.Created;root_exists=(Test-Path -LiteralPath $targetRoot)}|ConvertTo-Json}finally{foreach($stream in $held){$stream.Dispose()}}
''')
    assert result['refused'] and result['runs']==int(phase=='wait')
    assert result['root_exists']==(phase in ('registration','wait'))
