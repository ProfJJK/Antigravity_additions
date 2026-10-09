"""Inert ordinary-user tests. No SYSTEM, worker token, Docker API or R: action."""
import ast
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('execution_preflight',HERE/'inspect-execution-prerequisites-r3.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
WRAPPER=HERE/'inspect-execution-prerequisites-r3.ps1'
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PS=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'


def test_actual_manifest_proves_exact_resource_only_r2_to_r3_delta():
    old=json.loads((P.PRIOR/'source-manifest.json').read_bytes())
    new=json.loads((HERE/'stopped-runtime-r3-source-manifest.json').read_bytes())
    P.only_resource_delta(old,new)
    drift=copy.deepcopy(new);drift['files'][0]['sha256']='f'*64
    with pytest.raises(ValueError):P.only_resource_delta(old,drift)


def test_actual_six_r3_receipts_match_preflight_contract_without_worker_launch():
    expected=['b9e9fd56c77ac54f97f136b562881a554ed18f6f4f216ccacc0fdf43c51f2c85',
      '7ebac1cedc48cf60c52af7c9b5ff4e883b4325c8d92a6826c935a9e5f7b5322d',
      'c8518fb7c3f126c1c550e36072441eb520a3e76d91f75735d5f3ac832af954ee',
      'f81613cb152f9b5369908f796f55572e3f5779d2253515233b98aafc9e2b1344',
      '84d7043f2044d52e2cfd31bdd373d424821ed3e3a234cef74590b2cfb427283b',
      '5f987ba0f47d27417c0724277d70c52105dd1a96f04dd0a102fe7175d55f2970']
    raw=(P.INSTALL/'install-after.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
    installed=json.loads(raw);layout=json.loads((P.INSTALL/'windows-layout.json').read_bytes())
    for slot,pin in zip(P.SLOTS,expected):
        path=Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
        raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==pin
        P.worker_receipt(json.loads(raw),slot,hashlib.sha256((P.INSTALL/'install-after.json').read_bytes()).hexdigest(),installed['verification']['revision'],layout['slots'][slot]['sid'])


def test_actual_pinned_docker_cli_declares_only_system_known_dll():
    raw=P.DOCKER.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==P.DOCKER_SHA
    assert P.pe_imports(raw)=={'machine':'0x8664','imports':['kernel32.dll'],'delay_imports':[]}


def test_actual_known_dll_registry_and_dependency_path_contract():
    sys.path.insert(0,str(REPO/'src'));checked=[]
    try:result=P.docker_import_custody(SimpleNamespace(validate_code_path=lambda path:checked.append(path)))
    finally:sys.path.pop(0)
    assert result['kernel32_known_dll_registration_verified']
    assert checked==[Path(os.environ['SystemRoot'])/'System32/kernel32.dll']


@pytest.mark.parametrize('raw',[b'',b'MZ'+b'\0'*62,b'MZ'+b'\0'*58+b'\xff'*4],ids=['empty','missing_header','out_of_range'])
def test_pe_parser_rejects_unbounded_or_non_pe_input(raw):
    with pytest.raises((ValueError,struct.error)):P.pe_imports(raw)


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}'])
def test_duplicate_and_nonfinite_controls_rejected(raw):
    with pytest.raises(ValueError):P.strict_json(raw)


def info():
    return dict(OSType='linux',ID='private-host-identifier',MemoryLimit=True,SwapLimit=True,CpuCfsQuota=True,PidsLimit=True,SecurityOptions=['name=seccomp,profile=builtin'])


@pytest.mark.parametrize('field,value',[('OSType','windows'),('ID',''),('MemoryLimit',1),('SwapLimit',False),('CpuCfsQuota',None),('PidsLimit',False),('SecurityOptions',[])])
def test_daemon_requires_real_enforcement_booleans(field,value):
    raw=info();raw[field]=value
    with pytest.raises(ValueError):P.daemon_info(raw)


def test_daemon_output_does_not_publish_raw_identity_or_extra_fields():
    raw=info();raw['RegistryConfig']={'secret':'not-public'}
    out=P.daemon_info(raw)
    assert out['daemon_id_sha256']==hashlib.sha256(b'private-host-identifier').hexdigest()
    assert 'not-public' not in json.dumps(out) and 'private-host-identifier' not in json.dumps(out)


def image():return [dict(Id=P.IMAGE,Os='linux',Config=dict(Env=['PATH=/usr/bin'],Labels={'private':'hidden'}))]


@pytest.mark.parametrize('change',['tag','windows','volumes','secret_name','missing_config','two'])
def test_image_policy_rejects_incomplete_or_credential_bearing_metadata(change):
    value=image()
    if change=='tag':value[0]['Id']='image:latest'
    elif change=='windows':value[0]['Os']='windows'
    elif change=='volumes':value[0]['Config']['Volumes']={'/host':{}}
    elif change=='secret_name':value[0]['Config']['Env']=['API_TOKEN=do-not-publish']
    elif change=='missing_config':value[0].pop('Config')
    else:value+=value
    with pytest.raises(ValueError):P.image_info(value)
    assert 'hidden' not in json.dumps(P.image_info(image()))


@pytest.mark.parametrize('raw',[b'x',b'a'*63,b'a'*64+b'\n'+b'a'*64,b'\xff',b'a'*65537],ids=['bad','short','duplicate','encoding','oversize'])
def test_ownership_census_is_bounded_exact_ids(raw):
    with pytest.raises((ValueError,UnicodeError)):P.census_ids(raw)


def test_empty_and_nonempty_census():
    assert P.census_ids(b'')==set()
    assert P.census_ids(b'a'*64+b'\n')=={'a'*64}


def test_fresh_path_refuses_existing_and_access_denied(monkeypatch,tmp_path):
    sys.path.insert(0,str(REPO/'src'))
    try:
        path=tmp_path/'missing';P.require_absent(path)
        path.write_text('preserved')
        with pytest.raises(ValueError):P.require_absent(path)
        original=Path.lstat
        def denied(self,*a,**k):
            if self==path:raise PermissionError('fixture')
            return original(self,*a,**k)
        monkeypatch.setattr(Path,'lstat',denied)
        with pytest.raises(PermissionError):P.require_absent(path)
    finally:sys.path.pop(0)


def test_actual_windows_held_read_denies_write_and_delete(monkeypatch,tmp_path):
    sys.path.insert(0,str(REPO/'src'))
    from cochem_pipeline import windows as win
    path=tmp_path/'control';path.write_bytes(b'fixture')
    fake=SimpleNamespace(validate_code_path=lambda p:None,validate_private_path=lambda p:None,_api=win._api,_close=win._close)
    try:
        with P.held_read(path,20,fake,hashlib.sha256(b'fixture').hexdigest()) as raw:
            assert raw==b'fixture'
            with pytest.raises(PermissionError):path.write_bytes(b'changed')
            with pytest.raises(PermissionError):path.unlink()
        assert path.read_bytes()==b'fixture'
        other=tmp_path/'alias';os.link(path,other)
        with pytest.raises(ValueError):
            with P.held_read(path,20,fake):pass
    finally:sys.path.pop(0)


def test_docker_transport_exact_readonly_commands_and_sanitized_env(monkeypatch,tmp_path):
    sys.path.insert(0,str(REPO/'src'))
    from cochem_pipeline import deployment,containers
    server=dict(pid=123,process_created_filetime=123456,token_sid='S-1-5-18',executable=str(P.BACKEND),checked_at=1)
    rows=[dict(slot=s,access_denied=True,denied_modes=['write','read','read_write'],server=server) for s in P.SLOTS]
    calls=[];checks=[]
    def boundary(*a,**k):checks.append('boundary');return {P.ENDPOINT:copy.deepcopy(rows)}
    def attest(*a,**k):checks.append('server');return copy.deepcopy(server)
    def bounded(argv,**kwargs):
        calls.append((argv,kwargs))
        if 'info' in argv:raw=json.dumps(info()).encode()
        elif 'image' in argv:raw=json.dumps(image()).encode()
        else:raw=b''
        return SimpleNamespace(returncode=0,timed_out=False,cancelled=False,output_exceeded=False,stderr=b'',stdout=raw)
    monkeypatch.setattr(deployment,'verify_docker_access_boundary',boundary)
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',attest)
    monkeypatch.setattr(containers,'_bounded_process',bounded)
    monkeypatch.setenv('DOCKER_CONTEXT','untrusted');monkeypatch.setenv('API_TOKEN','hidden')
    policy=SimpleNamespace(executable=str(P.DOCKER),endpoint=P.ENDPOINT,image=P.IMAGE,pipe_server_executables=(str(P.BACKEND),),max_containers=4,warm_pool_size=2)
    config=SimpleNamespace(docker=policy,operator_name=r'AETHERDESK\ansac')
    try:out=P.docker_observation(None,{s:object() for s in P.SLOTS},config)
    finally:sys.path.pop(0)
    assert len(calls)==4 and checks.count('boundary')==2 and checks.count('server')==8
    for argv,k in calls:
        assert argv[:5]==[str(P.DOCKER),'--config',str(P.ROOT/'docker-client-config'),'--host',P.ENDPOINT]
        assert not {'DOCKER_CONTEXT','API_TOKEN'}&set(k['env'])
        assert k['timeout']==20 and k['output_limit']==1048576
    assert out['ownership_census']['empty'] and out['commands_executed']==4
    assert 'hidden' not in json.dumps(out)


def test_no_constructor_or_provisioning_entrypoints():
    tree=ast.parse((HERE/'inspect-execution-prerequisites-r3.py').read_text())
    names=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):names.append(node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else '')
    assert not set(names)&{'KnowledgeService','DockerRunner','execution_readiness','ensure','refresh','connect','unlink','rmtree','mkdir','chmod'}


def ps_functions():
    return "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"+f"$path='{WRAPPER}';"+r'''
    foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
    $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
    if($e.Count){throw ($e|Out-String)}
    foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
    '''


def ps(tmp_path,body):
    script=tmp_path/'fixture.ps1';script.write_text(ps_functions()+body)
    result=subprocess.run([str(PS),'-NoProfile','-File',str(script)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


@pytest.mark.parametrize('change,valid',[('',True),('$task.State=0',False),('$task.LastTaskResult=2',False),('$instances=1',False),('$action.Arguments+="x"',False),('$task.Definition.Triggers.Count=1',False)])
def test_actual_ps_prior_success_gate(tmp_path,change,valid):
    body=r'''
    $action=[pscustomobject]@{Type=0;Path='C:\fixed\python.exe';Arguments='-I -B fixed';WorkingDirectory='C:\fixed'}
    $actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($n)$action}
    $task=[pscustomobject]@{State=3;LastTaskResult=0;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
    $instances=0;$task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$instances}}
    '''+change+r'''
    $ok=$true;try{Assert-PassedTask $task 'C:\fixed\python.exe' '-I -B fixed' 'C:\fixed'}catch{$ok=$false}
    @{accepted=$ok}|ConvertTo-Json -Compress
    '''
    assert json.loads(ps(tmp_path,body))['accepted']==valid


def test_wrapper_nonadmin_apply_stops_before_actions():
    result=subprocess.run([str(PS),'-NoProfile','-File',str(WRAPPER),'-Apply'],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and '-Apply requires the owner' in result.stderr


def test_actual_systemroot_known_dll_custody_stops_at_windows_boundary(tmp_path):
    assert 'SYSTEM_DLL_PATH_VERIFIED' in ps(tmp_path,"Assert-WindowsKernel32; 'SYSTEM_DLL_PATH_VERIFIED'")


def test_wrapper_python_pin_matches():
    digest=hashlib.sha256((HERE/'inspect-execution-prerequisites-r3.py').read_bytes()).hexdigest()
    assert digest.upper() in WRAPPER.read_text() or digest in WRAPPER.read_text()


def test_full_run_uses_actual_frozen_support_names_and_actual_config(monkeypatch,tmp_path):
    """Execute run with real support code/config and explicit native boundaries.

    Privileged custody, SYSTEM identities and live R/Docker probes are fixtures;
    support API/constants, six-receipt parsing, manifests and phase flow are real.
    This is not a SYSTEM acceptance claim.
    """
    sys.path.insert(0,str(REPO/'src'))
    from cochem_pipeline import windows as win,config as config_module
    actual_config=config_module.load_config(str(P.PRIOR/'pipeline.json'))
    raw_config=json.loads((P.PRIOR/'pipeline.json').read_bytes())
    layout=json.loads((P.PRIOR/'windows-layout.json').read_bytes())
    assert actual_config.ramdisk.adopted_drive
    assert str(actual_config.ramdisk.workspace_root)==r'R:\CoChem427-windows-20261007'
    support_raw=(HERE/'worker-denial-acceptance-r3.py').read_bytes()
    assert hashlib.sha256(support_raw).hexdigest()==P.SUPPORT_SHA
    helper=tmp_path/'inspect-execution-prerequisites-r3.py';helper.write_bytes((HERE/helper.name).read_bytes())
    (tmp_path/'docker-client-config').mkdir()
    monkeypatch.setattr(P,'ROOT',tmp_path);monkeypatch.setattr(P,'__file__',str(helper))
    monkeypatch.setattr(sys,'executable',str(P.INSTALL/'.venv/Scripts/python.exe'))
    for name in ('require_system','validate_code_path','validate_private_directory'):
        monkeypatch.setattr(win,name,lambda *a,**k:None)
    monkeypatch.setattr(win,'_account_sid',lambda name:name)
    sid_by_name={row['name']:layout['slots'][slot]['sid'] for slot,row in raw_config['workers'].items()}
    monkeypatch.setattr(win,'_sid_text',lambda value:sid_by_name[value])
    monkeypatch.setattr(config_module,'load_config',lambda path:actual_config)
    revision={'verified':True,'source_sha256':'309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4'}
    def support_namespace(**kwargs):
        # Constants/function signatures are taken from actual hash-bound source.
        assert kwargs['CONFIG_SHA256']=='135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
        kwargs['verify_r3_runtime']=lambda *a:revision
        kwargs['require_stopped']=lambda *a:None
        kwargs['protected_json']=lambda w,path:(layout if path.name=='windows-layout.json' else raw_config,kwargs['CONFIG_SHA256'])
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(P,'SimpleNamespace',support_namespace)
    packet={'schema':'cochem-execution-prerequisites-inputs/1','knowledge_receipt_sha256':P.KNOWLEDGE_SHA,
        'install_receipt_sha256':'1'*64,'workers':{s:'2'*64 for s in P.SLOTS}}
    knowledge={'schema':'cochem-published-knowledge-verification/1','status':'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':P.KNOWLEDGE_HELPER_SHA,'runtime_root':str(P.PRIOR),'corpus_files':138,
        'corpus_bytes_preserved':True,'original_root_identity_and_security_preserved':True,'original_writer_lock_preserved':True,
        'index_size_sla_met':True,'knowledge_service_constructed':False,'index_refreshed_or_repaired':False,
        'existing_files_or_acls_modified':False,'activation_ready':False,'generation':'g-'+'a'*32,
        'index_sha256':'b'*64,'current_pointer_sha256':'c'*64,'source_pins_sha256':'d'*64,'index_bytes':5}
    fixtures={tmp_path/'worker-denial-acceptance-r3.py':support_raw,tmp_path/'inputs.json':json.dumps(packet).encode(),
        Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe'):b'fixture-bound-base-executable',
        P.PRIOR/'source-manifest.json':(P.PRIOR/'source-manifest.json').read_bytes(),
        P.INSTALL/'source-manifest.json':(HERE/'stopped-runtime-r3-source-manifest.json').read_bytes(),
        P.KNOWLEDGE:json.dumps(knowledge).encode(),P.STATE/'writer.lock':b'0',
        P.STATE/'current.json':json.dumps({'generation':knowledge['generation']}).encode(),P.STATE/'sources.json':b'{}',
        P.STATE/knowledge['generation']/'knowledge_index.db':b'index'}
    for slot in P.SLOTS:
        sid=layout['slots'][slot]['sid']
        row={'schema':'cochem-worker-denial-acceptance/1','status':'HANDLE_DENIALS_VERIFIED','slot':slot,
            'system_sid':'S-1-5-18','helper_sha256':P.SUPPORT_SHA,'runtime_root':str(P.INSTALL),'install_receipt_sha256':'1'*64,
            'cleanup_verified':True,'worker_released':True,'daemon_states_verified_before_and_after':True,'ioctls_sent':0,
            'tested_target_contents_read':0,'pipeline_started':False,'docker_denial_tested':False,'repair_identity_tested':False,
            'nonce':'e'*32,'revision':revision,'process':{'token_sid':sid,'token_matches_selected_worker':True,
                'image_matches_reviewed_executable':True,'owned_job_membership_verified':True,
                'token_details':{'sid':sid,'elevated':False,'administrators_enabled':False}}}
        fixtures=fixtures|{Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json':json.dumps(row).encode()}
    reads=[];closes=[]
    @contextmanager
    def native_file_boundary(path,maximum,w,expected=None,private=False,retain=True):
        reads.append(path)
        if retain:yield fixtures[path]
        else:yield None
        closes.append(path)
    monkeypatch.setattr(P,'held_read',native_file_boundary)
    monkeypatch.setattr(P,'fresh_state',lambda *a:{'fixture_absent':True})
    monkeypatch.setattr(P,'ram_observation',lambda *a:{'fixture_ram_unchanged':True})
    monkeypatch.setattr(P,'docker_observation',lambda *a:{'ownership_census':{'empty':True}})
    monkeypatch.setattr(P,'docker_import_custody',lambda *a:{'fixture_imports':True})
    try:
        assert P.run('f'*32,'0'*64)==0
        value=json.loads((tmp_path/'execution-prerequisites.json').read_bytes())
        assert value['status']=='READ_ONLY_PREREQUISITES_VERIFIED'
        assert value['knowledge']['r2_to_r3_only_resource_limits_delta_verified']
        assert len(value['worker_receipt_sha256'])==6 and len(reads)==len(closes)
    finally:sys.path.pop(0)


def test_exact_ps_apply_copy_packet_and_task_control_flow(tmp_path):
    """Real two-file copies and CreateNew packet; privileged calls stop at fake task."""
    target=tmp_path/'fresh';source=HERE/'inspect-execution-prerequisites-r3.py';support=HERE/'worker-denial-acceptance-r3.py'
    body=f"""
    $root='{target}';$source='{source}';$support='{support}';$sourceHash='{hashlib.sha256(source.read_bytes()).hexdigest()}';$supportHash='{P.SUPPORT_SHA}'
    $installRoot='C:\\fixture-r3';$basePython='C:\\fixture-base\\python.exe';$python='C:\\fixture-r3\\python.exe';$taskName='FIXTURE-NO-REAL-TASK'
    $copy='{REPO / 'scripts/stage_aetherdesk_427_payloads.ps1'}';$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($copy,[ref]$t,[ref]$e)
    foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
    Initialize-FileIdentity
    """+r'''
    $held=[Collections.Generic.List[IO.FileStream]]::new()
    function Assert-NoReparseAncestors {param($Path)};function Assert-ProtectedPath {param($Path)}
    function Assert-Stopped {};function Assert-OriginalFailedDenial {param([switch]$RequireTask)}
    function Assert-DockerNativeCustody {}
    function Get-EvidencePacket {param($Runtime)};function Get-TaskOrAbsent {param($Name)$null}
    function Assert-CodeTreeOnce {param($Path)1};function New-ProtectedDirectory {param($Path)$null=[IO.Directory]::CreateDirectory($Path)}
    function New-CodeAcl {param([bool]$Directory)
        $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$acl=[Security.AccessControl.FileSecurity]::new()
        $acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));$acl}
    $action=[pscustomobject]@{Path='';Arguments='';WorkingDirectory=''}
    $actions=[pscustomobject]@{};$actions|Add-Member ScriptMethod Create {param($n)$action}
    $definition=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description=''};Principal=[pscustomobject]@{UserId='';LogonType=0;RunLevel=0};Settings=[pscustomobject]@{Enabled=$false;AllowDemandStart=$false;MultipleInstances=0;ExecutionTimeLimit=''};Actions=$actions;Triggers=@()}
    $scheduler=[pscustomobject]@{};$scheduler|Add-Member ScriptMethod NewTask {param($n)$definition}
    $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod RegisterTaskDefinition {
        param($Name,$Definition,$Flags,$User,$Password,$Logon,$Sddl)
        if($Flags -ne 2 -or $User -cne 'SYSTEM' -or $Logon -ne 5 -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Triggers.Count -ne 0 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT8M'){throw 'Task creation contract differs'}
        $packet=Get-Content -LiteralPath (Join-Path $root 'inputs.json') -Raw|ConvertFrom-Json
        if($packet.fixture -ne $true -or $action.Arguments -cnotmatch ' --nonce [a-f0-9]{32} --packet-sha256 [a-f0-9]{64}$'){throw 'Bound packet/task argv differs'}
        if($held.Count -ne 3 -or @(Get-ChildItem -LiteralPath (Join-Path $root 'docker-client-config') -Force).Count -ne 0){throw 'Held two copies/packet or empty CLI config differs'}
        throw 'SIMULATED_SYSTEM_TASK_BOUNDARY'
    }
    $reached=$false;try{Invoke-Inspection ([ordered]@{}) ([ordered]@{fixture=$true})}catch{if($_.Exception.Message -notmatch 'SIMULATED_SYSTEM_TASK_BOUNDARY'){throw};$reached=$true}finally{foreach($s in $held){$s.Dispose()}}
    if(-not $reached){throw 'Full control flow did not reach registration'}
    'REAL_COPIES_AND_PACKET_VERIFIED'
    '''
    assert 'REAL_COPIES_AND_PACKET_VERIFIED' in ps(tmp_path,body)
    assert (target/source.name).read_bytes()==source.read_bytes()
    assert (target/support.name).read_bytes()==support.read_bytes()
