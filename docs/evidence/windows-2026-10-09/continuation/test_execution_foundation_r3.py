"""Disposable Windows fixtures only; no protected/SYSTEM/provider actions."""
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import pytest

HERE=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0,str(REPO/'src'))
spec=importlib.util.spec_from_file_location('foundation',HERE/'provision-execution-foundation-r3.py')
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)
PS=Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
WRAPPER=HERE/'provision-execution-foundation-r3.ps1'


def policy():
    from cochem_pipeline.config import load_config
    return load_config(str(F.INSTALL/'pipeline.json')).docker


def test_real_installed_constructor_immutable_wal_and_preserved_read(tmp_path,monkeypatch):
    from cochem_pipeline import containers
    expected='736cf378bfa6e298a47a8adbbca5e7b9ac6b3334e8bd1b8f0b91f1592c344f8e'
    assert hashlib.sha256(Path(containers.__file__).read_bytes()).hexdigest()==expected
    monkeypatch.setattr(containers,'_bounded_process',lambda *a,**k:pytest.fail('Constructor must not contact Docker'))
    summary=F.create_fresh_registry(policy(),tmp_path,lambda p:p.mkdir(),lambda p:None,containers.DockerRunner)
    path=tmp_path/'containers/containers.db';before=(path.read_bytes(),path.stat().st_mtime_ns)
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        owner=db.execute("select value from metadata where key='owner'").fetchone()[0]
    assert before[0][18:20]==b'\x02\x02'
    assert F.registry_inspection(path,owner)['journal_mode']=='wal'
    assert before==(path.read_bytes(),path.stat().st_mtime_ns)
    assert summary['capacity']==4 and summary['work_rows']==0
    assert list((tmp_path/'containers').iterdir())==[path]


@pytest.mark.parametrize('change',['owner','capacity','unknown','work','extra_table','header'])
def test_new_registry_refuses_mismatches_without_repair(tmp_path,change):
    from cochem_pipeline.containers import DockerRunner
    root=tmp_path/'containers';runner=DockerRunner(policy(),root)
    path=root/'containers.db'
    with sqlite3.connect(path) as db:
        if change in ('owner','capacity','unknown'):
            key={'unknown':'unknown_owned'}.get(change,change)
            db.execute('UPDATE metadata SET value=? WHERE key=?',('bad',key))
        elif change=='work':db.execute("insert into preparation_requests(profile_digest,job_id,requested_at)values('x','y',1)")
        elif change=='extra_table':db.execute('CREATE TABLE surprise(a)')
    db.close()
    if change=='header':
        raw=bytearray(path.read_bytes());raw[18:20]=b'\x01\x01';path.write_bytes(raw)
    before=path.read_bytes()
    with pytest.raises(ValueError):F.registry_inspection(path,runner.owner)
    assert path.read_bytes()==before


def test_registry_refuses_existing_root_or_concurrent_database(tmp_path):
    from cochem_pipeline.containers import DockerRunner
    root=tmp_path/'containers';root.mkdir();sentinel=root/'preserve';sentinel.write_bytes(b'old')
    with pytest.raises(ValueError):F.create_fresh_registry(policy(),tmp_path,lambda p:p.mkdir(),lambda p:None,DockerRunner)
    assert sentinel.read_bytes()==b'old'
    second=tmp_path/'second';second.mkdir()
    def raced_create(path):
        path.mkdir();(path/'containers.db').write_bytes(b'appeared')
    with pytest.raises(ValueError):F.create_fresh_registry(policy(),second,raced_create,lambda p:None,DockerRunner)
    assert (second/'containers/containers.db').read_bytes()==b'appeared'


def ram_fixture(monkeypatch,tmp_path):
    from cochem_pipeline import windows as win
    monkeypatch.setattr(F,'PRIVATE',tmp_path)
    monkeypatch.setattr(win,'validate_private_directory',lambda p:None)
    monkeypatch.setattr(win,'validate_private_path',lambda p:None)
    cfg={'lifecycle':'adopt_existing'}
    manager=F.fresh_ram_type(object,{'device_number':1})()
    manager.config=SimpleNamespace(as_dict=lambda:cfg);manager.private_root=tmp_path
    value={'state':'READY','adopted_existing_drive':True,'workspace_root':str(F.BOUNDARY),'slots':list(F.SLOTS),
        'config':cfg,'observed':{'device_number':1},'mount_root':str(Path('R:/')),'lifecycle_action':'ADOPT',
        'backup':None,'volume_root_metadata_preserved':True}
    return manager,value


def test_ram_ready_publish_create_new_never_replace(monkeypatch,tmp_path):
    manager,value=ram_fixture(monkeypatch,tmp_path)
    manager._save_state(value);path=tmp_path/'ramdisk-state.json';before=path.read_bytes()
    with pytest.raises(ValueError):manager._save_state(value)
    assert path.read_bytes()==before


@pytest.mark.parametrize('field,value',[('state','PREPARING'),('adopted_existing_drive',False),('workspace_root','R:\\'),('slots',['slot1']),
    ('config',{}),('observed',{}),('mount_root','S:\\'),('lifecycle_action','RECREATE'),('backup','old'),('volume_root_metadata_preserved',False)])
def test_ram_publication_refuses_nonapproved_evidence(monkeypatch,tmp_path,field,value):
    manager,evidence=ram_fixture(monkeypatch,tmp_path);evidence[field]=value
    with pytest.raises(ValueError):manager._save_state(evidence)
    assert not (tmp_path/'ramdisk-state.json').exists()


def test_ram_write_failure_preserves_partial_file_and_refuses_retry(monkeypatch,tmp_path):
    manager,value=ram_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(F.os,'fsync',lambda _:(_ for _ in ()).throw(OSError('fixture')))
    with pytest.raises(OSError):manager._save_state(value)
    path=tmp_path/'ramdisk-state.json';before=path.read_bytes()
    with pytest.raises(ValueError):manager._save_state(value)
    assert before==path.read_bytes()


def preflight():
    packet={'install_receipt_sha256':'1'*64,'workers':{s:'2'*64 for s in F.SLOTS}}
    revision={'verified':True,'source_sha256':'3'*64}
    value=dict(schema='cochem-execution-prerequisites/1',status='READ_ONLY_PREREQUISITES_VERIFIED',system_sid='S-1-5-18',
        helper_sha256=F.PREFLIGHT_SHA,runtime_root=str(F.INSTALL),install_receipt_sha256=packet['install_receipt_sha256'],
        activation_ready=False,native_model_jobs_executed=0,knowledge_service_constructed=False,docker_runner_constructed=False,
        ram_ensure_called=False,existing_databases_modified=False,existing_files_or_acls_modified=False,
        existing_tasks_modified_or_run=False,native_provider_authentication_performed=False,holds=[],
        worker_receipt_sha256=packet['workers'],revision=revision,ram_before={'fixture':1},ram_after={'fixture':1},
        fresh_state_before={'fresh':True},fresh_state_after={'fresh':True},nonce='4'*32,
        docker={'ownership_census':{'empty':True,'owner_label_count':0,'name_prefix_count':0,'union_count':0}},
        knowledge={'current_bytes_unchanged':True,'r2_to_r3_only_resource_limits_delta_verified':True})
    return value,packet,revision


@pytest.mark.parametrize('change',['status','helper','bool','workers','ram','knowledge','census','revision'])
def test_preflight_binding_fails_closed(change):
    value,packet,revision=preflight();value=copy.deepcopy(value)
    if change=='status':value['status']='PREREQUISITES_HELD'
    elif change=='helper':value['helper_sha256']='0'*64
    elif change=='bool':value['activation_ready']=0
    elif change=='workers':value['worker_receipt_sha256']['slot1']='0'*64
    elif change=='ram':value['ram_after']={}
    elif change=='knowledge':value['knowledge']['current_bytes_unchanged']=False
    elif change=='census':value['docker']['ownership_census']['union_count']=1
    else:value['revision']={}
    with pytest.raises(ValueError):F.preflight_binding(value,packet,revision)


def test_preflight_binding_accepts_exact_success():
    F.preflight_binding(*preflight())


def ps_functions():
    return "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"+f"$path='{WRAPPER}';"+r"""
    foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
    $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
    if($e.Count){throw ($e|Out-String)}
    foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
    """+f"""
    foreach($definition in @(Import-PinnedFunctions '{HERE/'inspect-execution-prerequisites-r3.ps1'}' '5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887' @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Get-EvidencePacket','Write-NewPacket','Assert-WindowsKernel32','Assert-DockerNativeCustody','Wait-Inspection'))){{. ([scriptblock]::Create($definition))}}
    """


def ps(tmp_path,body):
    script=tmp_path/'fixture.ps1';script.write_text(ps_functions()+body)
    result=subprocess.run([str(PS),'-NoProfile','-File',str(script)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


def test_real_ps_apply_copies_three_helpers_and_packet_before_fake_task(tmp_path):
    target=tmp_path/'fresh';source=HERE/'provision-execution-foundation-r3.py';pre=HERE/'inspect-execution-prerequisites-r3.py';support=HERE/'worker-denial-acceptance-r3.py'
    body=f"""
    $root='{target}';$source='{source}';$preflight='{pre}';$support='{support}'
    $sourceHash='{hashlib.sha256(source.read_bytes()).hexdigest()}';$preflightHash='{F.PREFLIGHT_SHA}';$supportHash='{F.WORKER_SHA}'
    $installRoot='C:\\fixture-r3';$basePython='C:\\fixture-base\\python.exe';$python='C:\\fixture-r3\\python.exe';$taskName='FIXTURE-NO-REAL-TASK'
    $copy='{REPO/'scripts/stage_aetherdesk_427_payloads.ps1'}';$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($copy,[ref]$t,[ref]$e)
    foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
    Initialize-FileIdentity
    """+r"""
    $held=[Collections.Generic.List[IO.FileStream]]::new()
    function Assert-NoReparseAncestors {param($Path)};function Assert-ProtectedPath {param($Path)}
    function Assert-Stopped {};function Assert-OriginalFailedDenial {param([switch]$RequireTask)}
    function Assert-DockerNativeCustody {}
    function Get-FoundationPacket {param($Runtime)[ordered]@{fixture=$true}}
    function Get-TaskOrAbsent {param($Name)$null}
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
        if($held.Count -ne 4 -or @(Get-ChildItem -LiteralPath (Join-Path $root 'docker-client-config') -Force).Count -ne 0){throw 'Held three copies/packet or empty CLI config differs'}
        throw 'SIMULATED_SYSTEM_TASK_BOUNDARY'
    }
    $reached=$false;try{Invoke-Foundation ([ordered]@{}) ([ordered]@{fixture=$true})}catch{if($_.Exception.Message -notmatch 'SIMULATED_SYSTEM_TASK_BOUNDARY'){throw};$reached=$true}finally{foreach($s in $held){$s.Dispose()}}
    if(-not $reached){throw 'Full control flow did not reach registration'}
    'REAL_COPIES_AND_PACKET_VERIFIED'
    """
    assert 'REAL_COPIES_AND_PACKET_VERIFIED' in ps(tmp_path,body)
    for path in (source,pre,support):assert (target/path.name).read_bytes()==path.read_bytes()


def test_wrapper_nonadmin_apply_stops_before_actions():
    result=subprocess.run([str(PS),'-NoProfile','-File',str(WRAPPER),'-Apply'],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and '-Apply requires the owner' in result.stderr


def test_wrapper_python_pin_matches():
    digest=hashlib.sha256((HERE/'provision-execution-foundation-r3.py').read_bytes()).hexdigest()
    assert digest in WRAPPER.read_text()


def actual_public_inputs():
    """Exact currently public r3 controls; no private corpus/provider material."""
    receipt=json.loads((F.INSTALL/'install-after.json').read_bytes())
    rows={}
    for slot in F.SLOTS:
        path=Path(r'C:\Program Files\CoChem')/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
        rows[slot]=(path,path.read_bytes())
    # The worker receipt carries exactly the installed verifier's full revision.
    revision=json.loads(rows['slot1'][1])['revision']
    return receipt,revision,rows


def knowledge_fixture():
    ns={'__name__':'frozen_inspector_fixture','__file__':str(HERE/'inspect-execution-prerequisites-r3.py')}
    exec(compile((HERE/'inspect-execution-prerequisites-r3.py').read_bytes(),ns['__file__'],'exec'),ns)
    value={'schema':'cochem-published-knowledge-verification/1','status':'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':ns['KNOWLEDGE_HELPER_SHA'],'runtime_root':str(ns['PRIOR']),'corpus_files':138,
        'corpus_bytes_preserved':True,'original_root_identity_and_security_preserved':True,'original_writer_lock_preserved':True,
        'index_size_sla_met':True,'knowledge_service_constructed':False,'index_refreshed_or_repaired':False,
        'existing_files_or_acls_modified':False,'activation_ready':False,'generation':'g-'+'a'*32,'nonce':'f'*32,
        'index_sha256':'b'*64,'current_pointer_sha256':'c'*64,'source_pins_sha256':'d'*64,'index_bytes':5}
    ns['knowledge_receipt'](value)
    return ns,value


@pytest.mark.parametrize('fail',['','before_ram','after_ram','registry'])
def test_full_python_run_real_support_config_and_constructor_with_inert_privilege_boundaries(monkeypatch,tmp_path,fail):
    from cochem_pipeline import windows as win,config as config_module,ramdisk,containers,knowledge as knowledge_module
    actual_config=config_module.load_config(str(F.INSTALL/'pipeline.json'))
    raw_config=json.loads((F.INSTALL/'pipeline.json').read_bytes())
    layout=json.loads((F.INSTALL/'windows-layout.json').read_bytes())
    installed,revision,worker_rows=actual_public_inputs()
    pre,knowledge=knowledge_fixture()
    oldroot=F.ROOT;helper=tmp_path/'helper';helper.mkdir()
    for name in ('provision-execution-foundation-r3.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py'):
        (helper/name).write_bytes((HERE/name).read_bytes())
    (helper/'docker-client-config').mkdir()
    private=tmp_path/'private';private.mkdir();boundary=tmp_path/'ram'
    monkeypatch.setattr(F,'ROOT',helper);monkeypatch.setattr(F,'PRIVATE',private);monkeypatch.setattr(F,'BOUNDARY',boundary)
    monkeypatch.setattr(F,'__file__',str(helper/'provision-execution-foundation-r3.py'))
    monkeypatch.setattr(sys,'executable',str(F.INSTALL/'.venv/Scripts/python.exe'))
    for name in ('require_system','validate_code_path','validate_private_directory','validate_private_path'):
        monkeypatch.setattr(win,name,lambda *a,**k:None)
    monkeypatch.setattr(win,'_account_sid',lambda value:value)
    sid_names={row['name']:layout['slots'][slot]['sid'] for slot,row in raw_config['workers'].items()}
    monkeypatch.setattr(win,'_sid_text',lambda value:sid_names[value])
    ramconfig=SimpleNamespace(workspace_root=boundary,adopted_drive=True,mount_root=Path('R:/'),as_dict=actual_config.ramdisk.as_dict)
    config=SimpleNamespace(private_root=private,ramdisk=ramconfig,docker=actual_config.docker)
    monkeypatch.setattr(config_module,'load_config',lambda *a:config)
    monkeypatch.setattr(knowledge_module,'_private_directory',lambda path,exist_ok=False:path.mkdir(exist_ok=exist_ok))
    monkeypatch.setattr(containers,'_bounded_process',lambda *a,**k:pytest.fail('Constructor cannot use Docker'))
    history=[];exclusions={'old'}
    monkeypatch.setattr(win,'defender_exclusions',lambda:set(exclusions))
    before={'volume':{'device_number':1},'root':{'file_id':1}}
    docker={'ownership_census':{'empty':True,'owner_label_count':0,'name_prefix_count':0,'union_count':0},
        'server':{'pid':123},'engine':{'fixture':True},'api_aliases':['fixture']}
    class FakeNativeManager:
        def __init__(self,cfg,pvt,identities):self.config=cfg;self.private_root=pvt
        def ensure(self):
            history.append('ensure')
            boundary.mkdir()
            for slot in F.SLOTS:(boundary/slot).mkdir()
            exclusions.update(str(boundary/s).casefold() for s in F.SLOTS)
            value={'state':'READY','adopted_existing_drive':True,'workspace_root':str(boundary),'slots':list(F.SLOTS),
                'config':ramconfig.as_dict(),'observed':before['volume'],'mount_root':str(Path('R:/')),'lifecycle_action':'ADOPT',
                'backup':None,'volume_root_metadata_preserved':True}
            self._save_state(value)
            return value
        def inspect(self,require_capacity):
            assert require_capacity
            if fail=='after_ram':raise RuntimeError('private error must not be emitted')
    monkeypatch.setattr(ramdisk,'RamdiskManager',FakeNativeManager)
    @contextmanager
    def mutex(*a):
        history.append('mutex_acquired')
        yield
        history.append('mutex_released')
    monkeypatch.setattr(F,'production_ram_lock',mutex)
    packet={'schema':'cochem-execution-foundation-inputs/1','install_receipt_sha256':hashlib.sha256((F.INSTALL/'install-after.json').read_bytes()).hexdigest(),
        'knowledge_receipt_sha256':pre['KNOWLEDGE_SHA'],'preflight_receipt_sha256':'e'*64,
        'workers':{s:hashlib.sha256(raw).hexdigest() for s,(_,raw) in worker_rows.items()}}
    prior,_,_=preflight();prior.update(install_receipt_sha256=packet['install_receipt_sha256'],worker_receipt_sha256=packet['workers'],
        revision=revision,ram_before=before,ram_after=before,docker=docker)
    fixtures={helper/'inputs.json':json.dumps(packet).encode(),F.PREFLIGHT_ROOT/'execution-prerequisites.json':json.dumps(prior).encode(),
        pre['PRIOR']/'source-manifest.json':(pre['PRIOR']/'source-manifest.json').read_bytes(),
        F.INSTALL/'source-manifest.json':(F.INSTALL/'source-manifest.json').read_bytes(),pre['KNOWLEDGE']:json.dumps(knowledge).encode(),
        pre['STATE']/'writer.lock':b'0',pre['STATE']/'current.json':b'{}',pre['STATE']/'sources.json':b'{}',
        pre['STATE']/knowledge['generation']/'knowledge_index.db':b'index'}
    fixtures.update({path:raw for path,raw in worker_rows.values()})
    def namespace(**kwargs):
        if 'verify_r3_runtime' in kwargs:
            assert kwargs['CONFIG_SHA256']==hashlib.sha256((F.INSTALL/'pipeline.json').read_bytes()).hexdigest()
            kwargs['verify_r3_runtime']=lambda *a:revision
            kwargs['protected_json']=lambda w,path:(layout if path.name=='windows-layout.json' else raw_config,kwargs['CONFIG_SHA256'])
            kwargs['require_stopped']=lambda *a:history.append('stopped')
        else:
            @contextmanager
            def held(path,maximum,w,expected=None,private=False,retain=True):
                if path in fixtures:yield fixtures[path]
                elif path.parent==helper:yield path.read_bytes()
                elif path.name=='python.exe':yield b'bounded base fixture'
                elif not retain:yield None
                else:raise AssertionError(f'Unexpected input {path}')
            kwargs['held_read']=held
            def fresh(*a):
                history.append('fresh')
                if fail=='before_ram':raise ValueError('fixture fresh guard')
                assert not boundary.exists() and not (private/'ramdisk-state.json').exists() and not (private/'containers').exists()
            kwargs['fresh_state']=fresh
            kwargs['ram_observation']=lambda *a:before
            kwargs['docker_observation']=lambda *a:docker
            kwargs['docker_import_custody']=lambda *a:None
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(F,'SimpleNamespace',namespace)
    if fail=='registry':
        original=F.create_fresh_registry
        def fail_registry(*args):
            args[1].joinpath('containers').mkdir()
            raise ValueError('partial fixture directory')
        monkeypatch.setattr(F,'create_fresh_registry',fail_registry)
    exitcode=F.run('0'*32,'1'*64)
    result=json.loads((helper/'execution-foundation.json').read_bytes())
    assert 'private error' not in json.dumps(result)
    assert result['activation_ready'] is False and result['containers_created']==0
    if fail:
        assert exitcode==2 and result['status']=='EXECUTION_FOUNDATION_HELD' and result['partial_outputs_preserved']
        if fail=='before_ram':
            assert not result['ram_provision_started'] and not boundary.exists()
        else:
            assert result['ram_provision_started'] and (private/'ramdisk-state.json').exists()
            assert result['registry_provision_started']==(fail=='registry')
    else:
        assert exitcode==0 and result['status']=='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'
        assert result['registry']['work_rows']==0 and result['registry']['capacity']==4
        assert history.index('fresh')<history.index('ensure')
        assert history[-1]=='mutex_released'


@pytest.mark.parametrize('change',['','held','worker','task_args','task_running','task_result','nonce','knowledge'])
def test_real_ps_complete_packet_receipt_and_task_bindings(tmp_path,change):
    installed,revision,rows=actual_public_inputs()
    first=json.loads(rows['slot1'][1]);pre,k=knowledge_fixture()
    runtime={'install_receipt_sha256':first['install_receipt_sha256'],'source_manifest_sha256':first['source_manifest_sha256'],
        'configuration_sha256':first['config_sha256'],'revision':revision}
    value,packet,_=preflight()
    packet['workers']={s:hashlib.sha256(raw).hexdigest() for s,(_,raw) in rows.items()}
    packet['install_receipt_sha256']=runtime['install_receipt_sha256']
    value.update(install_receipt_sha256=runtime['install_receipt_sha256'],revision=revision,worker_receipt_sha256=packet['workers'],packet_sha256='a'*64)
    if change=='held':value['status']='PREREQUISITES_HELD'
    if change=='worker':value['worker_receipt_sha256']['slot2']='0'*64
    if change=='nonce':value['nonce']='bad'
    if change=='knowledge':value['knowledge']['current_bytes_unchanged']=False
    controls={}
    fixtures=tmp_path/'controls';fixtures.mkdir()
    def add(path,raw):
        target=fixtures/f'{len(controls)}.json';target.write_bytes(raw)
        controls[str(path)]=str(target)
    for path,raw in rows.values():add(path,raw)
    add(F.INSTALL/'windows-layout.json',(F.INSTALL/'windows-layout.json').read_bytes())
    add(pre['KNOWLEDGE'],json.dumps(k).encode())
    add(F.PREFLIGHT_ROOT/'execution-prerequisites.json',json.dumps(value).encode())
    bundle={'runtime':runtime,'controls':controls,'preflight':value,'workers':{s:json.loads(raw) for s,(_,raw) in rows.items()},'knowledge':k}
    bundle_path=tmp_path/'fixture-data.json';bundle_path.write_text(json.dumps(bundle))
    body=f"""
    $data=Get-Content -LiteralPath '{bundle_path}' -Raw|ConvertFrom-Json
    $installRoot='{F.INSTALL}';$python=Join-Path $installRoot '.venv\\Scripts\\python.exe'
    $supportHash='{F.WORKER_SHA}';$preflightHash='{F.PREFLIGHT_SHA}';$preflightRoot='{F.PREFLIGHT_ROOT}'
    $knowledgeHash='{pre['KNOWLEDGE_SHA']}';$change='{change}'
    foreach($definition in @(Import-PinnedFunctions '{HERE/'check-worker-denials-r3.ps1'}' '5f645c51e090eab289b74013391d71c69a53da20e542a1a6ee65823cacd5de2d' @('Read-R3Text'))){{. ([scriptblock]::Create($definition))}}
    """+r'''
    $held=[Collections.Generic.List[IO.FileStream]]::new()
    function Read-R3Control {param($Path,$Expected,[long]$Maximum=1048576)
        $fixture=$data.controls.PSObject.Properties[$Path].Value
        if(-not $fixture){throw 'Unexpected control path'}
        $stream=[IO.File]::Open($fixture,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $held.Add($stream)
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$hash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
        $stream.Position=0
        [pscustomobject]@{Stream=$stream;Sha256=$hash;Length=$stream.Length}
    }
    function Get-TaskOrAbsent {param($Name)
        $exe=$python
        if($Name -match '-WorkerDenial-r3-(slot[1-6])$'){
            $slot=$Matches[1];$v=$data.workers.$slot;$dir="C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261007-r3-$slot"
            $argv='-I -B "'+$dir+'\worker-denial-acceptance-r3.py" --slot '+$slot+' --nonce '+$v.nonce+' --install-receipt-sha256 '+$data.runtime.install_receipt_sha256
        }elseif($Name -ceq 'CoChem-4.2.7-KnowledgePublishedVerification-20261007-r2'){
            $dir='C:\Program Files\CoChem\KnowledgePublishedVerification4.2.7-windows-20261007-r2'
            $exe='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\.venv\Scripts\python.exe'
            $argv='-I -B "'+$dir+'\verify-published-knowledge.py" '+$data.knowledge.nonce
        }elseif($Name -ceq 'CoChem-4.2.7-ExecutionPrerequisites-20261007-r3'){
            $dir=$preflightRoot;$argv='-I -B "'+$dir+'\inspect-execution-prerequisites-r3.py" --nonce '+$data.preflight.nonce+' --packet-sha256 '+$data.preflight.packet_sha256
            if($change -ceq 'task_args'){$argv+=' unexpected'}
        }else{throw 'Unexpected prior task name'}
        $action=[pscustomobject]@{Type=0;Path=$exe;Arguments=$argv;WorkingDirectory=$dir}
        $actions=[pscustomobject]@{Count=1;Action=$action};$actions|Add-Member ScriptMethod Item {param($n)$this.Action}
        $task=[pscustomobject]@{State=3;LastTaskResult=0;Instances=0;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
        if($Name -ceq 'CoChem-4.2.7-ExecutionPrerequisites-20261007-r3'){
            if($change -ceq 'task_running'){$task.Instances=1}
            if($change -ceq 'task_result'){$task.LastTaskResult=2}
        }
        $task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$this.Instances}}
        $task
    }
    $accepted=$false;$packet=$null
    try{$packet=Get-FoundationPacket $data.runtime;$accepted=$true}
    catch{if(-not $change){throw}}
    finally{foreach($stream in $held){$stream.Dispose()}}
    @{accepted=$accepted;held_count=$held.Count;packet=$packet}|ConvertTo-Json -Depth 8 -Compress
    '''
    out=json.loads(ps(tmp_path,body))
    assert out['accepted']==(change=='')
    if not change:
        assert out['held_count']==9
        assert out['packet']['schema']=='cochem-execution-foundation-inputs/1'
        assert out['packet']['preflight_receipt_sha256']==hashlib.sha256(json.dumps(value).encode()).hexdigest()
        assert out['packet']['workers']==packet['workers']
