"""Ordinary Windows fixtures only. No Docker/SYSTEM/provider execution."""
import ast
from contextlib import closing
import copy
import ctypes
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import pytest

HERE=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0,str(REPO/'src'))
spec=importlib.util.spec_from_file_location('physical_acceptance',HERE/'accept-docker-execution-r3-v2.py')
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
PS=Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
PYTHON=Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\venv-copy\Scripts\python.exe')
WRAPPER=HERE/'accept-docker-execution-r3-v2.ps1'


def policy():
    from cochem_pipeline.config import load_config
    return load_config(str(A.INSTALL/'pipeline.json')).docker


def write_sources(tmp_path):
    for phase,files in A.FIXTURES.items():
        for name,raw in files.items():
            path=tmp_path/phase/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)


def test_real_fixture_answer_uses_installed_isolated_observer_and_source_hash(tmp_path):
    from cochem_pipeline.pytest_assertions import ASSERTION_OBSERVER,bind_assertion_evidence
    from cochem_pipeline.containers import source_snapshot,junit_cases,parse_junit
    write_sources(tmp_path)
    for phase in ('red','green'):
        root=tmp_path/phase;junit=tmp_path/f'{phase}.xml';sidecar=tmp_path/f'{phase}.json'
        run=subprocess.run([str(PYTHON),'-I','-c',ASSERTION_OBSERVER,str(sidecar),str(root),'tests','-q','-k','test_answer',
            '--rootdir='+str(root),'--junitxml='+str(junit),'-p','no:cacheprovider'],cwd=root,capture_output=True,timeout=30)
        assert run.returncode==(1 if phase=='red' else 0),run.stdout+run.stderr
        _,source=source_snapshot(root,policy(),source_modes={name:'100644' for name in A.FIXTURES[phase]})
        assert source==A.source_manifest(A.FIXTURES[phase])
        assert parse_junit(junit.read_bytes())['tests']==1
        cases=bind_assertion_evidence(junit_cases(junit.read_bytes()),sidecar.read_bytes(),source['files'])
        if phase=='red':
            assert cases[0]['assertion_failure']['exception_type']=='AssertionError'
            assert cases[0]['assertion_failure']['source_sha256']==hashlib.sha256(A.TEST_SOURCE).hexdigest()
        else:assert cases[0]['status']=='passed'


@pytest.mark.parametrize('bad',['','uid','pids','memory','cpu','seccomp','cap','network','root'])
def test_fixed_physical_assertions_in_inert_os_fixture(bad):
    data={'/proc/self/status':'NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t00000000\n',
        '/proc/mounts':'tmpfs /work tmpfs rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n',
        '/sys/fs/cgroup/memory.max':'4294967296','/sys/fs/cgroup/pids.max':'512','/sys/fs/cgroup/cpu.max':'200000 100000'}
    if bad=='pids':data['/sys/fs/cgroup/pids.max']='max'
    if bad=='memory':data['/sys/fs/cgroup/memory.max']='999'
    if bad=='cpu':data['/sys/fs/cgroup/cpu.max']='300000 100000'
    if bad=='seccomp':data['/proc/self/status']=data['/proc/self/status'].replace('Seccomp:\t2','Seccomp:\t0')
    if bad=='cap':data['/proc/self/status']=data['/proc/self/status'].replace('00000000','00000001')
    class FakePath:
        def __init__(self,path):self.path=str(path)
        def __truediv__(self,child):return FakePath(self.path+'/'+child)
        def read_text(self):return data[self.path]
        def exists(self):return self.path.endswith('cgroup.controllers')
        def write_bytes(self,raw):
            if self.path=='/cochem-readonly-root-probe' and bad!='root':raise OSError(errno.EROFS,'fixture')
            data[self.path]=raw
        def read_bytes(self):return data[self.path]
        def unlink(self):del data[self.path]
    modules={'errno':errno,'os':SimpleNamespace(getuid=lambda:0 if bad=='uid' else 1000,getgid=lambda:1000),
        'pathlib':SimpleNamespace(Path=FakePath),'socket':SimpleNamespace(if_nameindex=lambda:[(1,'lo'),(2,'eth0')] if bad=='network' else [(1,'lo')]),
        'business':SimpleNamespace(answer=lambda:42)}
    builtins=__import__('builtins');env={'__builtins__':dict(vars(builtins),__import__=lambda name,*a:modules[name])}
    exec(A.TEST_SOURCE,env)
    if bad:
        with pytest.raises((AssertionError,ValueError)):env['test_physical_sandbox']()
    else:env['test_physical_sandbox']()


def test_exact_production_worker_object_exclusion_and_collision_cleanup(monkeypatch):
    calls=[];closed=[]
    class Kernel:
        def CreateMutexW(self,security,owned,name):
            assert security is None and owned is False
            calls.append(name);ctypes.set_last_error(183 if len(calls)==3 else 0);return len(calls)
    fake=SimpleNamespace(_sid_text=lambda v:v,_account_sid=lambda v:v,_check=lambda h,*a:h,_api=lambda:{'kernel32':Kernel()},_close=closed.append)
    identities={slot:SimpleNamespace(name='S-1-5-21-'+slot) for slot in A.SLOTS}
    with pytest.raises(ValueError):
        with A.reserve_worker_identities(fake,identities):pytest.fail('Collision must stop before body')
    assert calls==['Global\\CoChemPipeline422-S-1-5-21-slot'+str(i) for i in (1,2,3)]
    assert closed==[3,2,1]


def test_all_six_worker_exclusion_handles_remain_until_context_exit():
    handles=[];closed=[]
    class Kernel:
        def CreateMutexW(self,s,o,n):ctypes.set_last_error(0);handles.append(n);return len(handles)
    fake=SimpleNamespace(_sid_text=lambda v:v,_account_sid=lambda v:v,_check=lambda h,*a:h,_api=lambda:{'kernel32':Kernel()},_close=closed.append)
    identities={slot:SimpleNamespace(name=slot) for slot in A.SLOTS}
    with A.reserve_worker_identities(fake,identities):assert len(handles)==6 and not closed
    assert closed==[6,5,4,3,2,1]


class InertDocker:
    """Actual installed registry/pool/run logic, explicit Docker transport fixture."""
    def __init__(self,policy,root,monkeypatch):
        from cochem_pipeline import containers,windows
        outer=self;self.objects={};self.calls=[];self.sequence=0;self.snapshot={}
        monkeypatch.setattr(windows,'current_boot_identity',lambda:'fixture-boot')
        class Runner(containers.DockerRunner):
            def _call(self,args,**kw):return outer.call(self,args,kw)
        self.runner=Runner(policy,root)
    def result(self,raw=b'',code=0,err=b''):
        return SimpleNamespace(returncode=code,stdout=raw,stderr=err,timed_out=False,output_exceeded=False,cancelled=False)
    def call(self,runner,args,kw):
        from cochem_pipeline import containers
        self.calls.append(args)
        if args[0]=='info':return self.result(json.dumps(dict(ID='fixture-daemon',OSType='linux',MemoryLimit=True,SwapLimit=True,CpuCfsQuota=True,PidsLimit=True,SecurityOptions=['seccomp'])).encode())
        if args[:2]==['image','inspect']:return self.result(json.dumps([dict(Id=runner.policy.image,Os='linux',Config={'Env':[]})]).encode())
        if args[0]=='run':
            assert args[1]=='--detach' and args[args.index('--pull')+1]=='never'
            self.sequence+=1;identifier=f'{self.sequence:064x}'
            labels=dict(args[i+1].split('=',1) for i,item in enumerate(args) if item=='--label')
            self.objects[identifier]={'Id':identifier,'Name':'/'+args[args.index('--name')+1],'Image':runner.policy.image,'Config':{'Labels':labels,'User':'1000:1000'},
                'State':{'Running':True},'Mounts':[{'Type':'tmpfs'}],
                'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Memory':4294967296,'MemorySwap':4294967296,'NanoCpus':2000000000,'PidsLimit':512,
                'IpcMode':'none','Privileged':False,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Tmpfs':{'/work':'size=2048m','/tmp':'size=2048m'}}}
            return self.result(identifier.encode())
        if args[0]=='inspect':
            value=self.objects.get(args[1])
            return self.result(json.dumps([value]).encode()) if value else self.result(code=1,err=b'No such object')
        if args[0]=='rm':
            assert args[1:3]==['--force','--volumes'];self.objects.pop(args[3]);return self.result()
        if args[0]=='top':return self.result(b'PID COMMAND\n1 python\n')
        assert args[0]=='exec'
        if containers._EXTRACT in args:
            cid=args[2];files={}
            with tarfile.open(fileobj=io.BytesIO(kw['data'])) as tar:
                for member in tar:files[member.name]=tar.extractfile(member).read()
            self.snapshot[cid]=files
            return self.result(A.source_manifest(files)['sha256'].encode())
        cid=args[3] if args[1]=='--workdir' else args[1]
        files=self.snapshot[cid];red=b'return 41' in files['tests/business.py']
        if args[1]=='--workdir':return self.result(b'fixture pytest output',1 if red else 0)
        if containers._OUTPUT_TREE in args:return self.result(json.dumps(A.source_manifest(files)['files']).encode())
        assert containers._READ_RESULT in args
        if args[-2].endswith('.xml'):
            failure='<failure message="fixture">assert 41 == 42</failure>' if red else ''
            raw=f'<testsuite tests="2" failures="{int(red)}" errors="0" skipped="0"><testcase classname="tests.test_contract" name="test_physical_sandbox"/><testcase classname="tests.test_contract" name="test_answer">{failure}</testcase></testsuite>'
            return self.result(raw.encode())
        records=[{'nodeid':'tests/test_contract.py::test_physical_sandbox','outcome':'passed','assertion':None},
            {'nodeid':'tests/test_contract.py::test_answer','outcome':'failed' if red else 'passed','assertion':
            {'phase':'call','exception_type':'AssertionError','path':'tests/test_contract.py','line':len(A.TEST_SOURCE.splitlines()),
            'source_sha256':hashlib.sha256(A.TEST_SOURCE).hexdigest()} if red else None}]
        return self.result(json.dumps(records).encode())


def test_actual_installed_pool_reservation_run_and_cleanup_methods_inert_transport(monkeypatch,tmp_path):
    from cochem_pipeline import containers
    assert hashlib.sha256(Path(containers.__file__).read_bytes()).hexdigest()=='736cf378bfa6e298a47a8adbbca5e7b9ac6b3334e8bd1b8f0b91f1592c344f8e'
    source=tmp_path/'source';write_sources(source);monkeypatch.setattr(A,'WORKSPACE',source)
    docker=InertDocker(policy(),tmp_path/'registry',monkeypatch)
    phases=[];report={'nonce':'a'*32};A.execute_cycle(docker.runner,None,phases.append,report)
    assert report['prepared_count']==2 and report['physical_boundary_verified'] and report['cleanup_verified']
    assert report['final_registry_counts']['owned']==0 and report['final_registry_counts']['published_capacity']==4
    assert [row['failures'] for row in report['executions']]==[1,0]
    assert len([c for c in docker.calls if c[0]=='run'])==2 and len([c for c in docker.calls if c[0]=='rm'])==2
    assert not docker.objects
    with closing(docker.runner._connect()) as db:
        assert db.execute("SELECT count(*) FROM containers WHERE status='REMOVED'").fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM pool_demand").fetchone()[0]==2
    assert len(list((tmp_path/'registry').glob('*.receipt.json')))==2


def test_transport_sanitizes_env_and_guards_server_and_creation_budget(monkeypatch,tmp_path):
    from cochem_pipeline import containers,deployment
    calls=[]
    monkeypatch.setenv('API_TOKEN','private-do-not-forward');monkeypatch.setenv('DOCKER_CONTEXT','wrong')
    server=dict(pid=1,process_created_filetime=2,token_sid='SYSTEM',executable='backend')
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',lambda *a,**k:server)
    monkeypatch.setattr(containers,'_bounded_process',lambda argv,**kw:(calls.append((argv,kw)) or SimpleNamespace(returncode=0)))
    P=SimpleNamespace(server_identity=lambda v:v,ENDPOINT=policy().endpoint,DOCKER=Path(policy().executable))
    stopped=[];S=SimpleNamespace(require_stopped=lambda _:stopped.append(True))
    config=SimpleNamespace(operator_name='fixture',docker=policy())
    class Base:
        def __init__(self):self.policy=policy()
        def prepare_pool(self,target,admission_lock=None):return target
    runner=A.bounded_runner_type(Base,P,S,None,config,{},server)()
    assert runner.prepare_pool(target=2)==2
    with pytest.raises(ValueError):runner.prepare_pool(target=2)
    with pytest.raises(ValueError):runner.prepare_pool(target=3)
    runner._call(['run','--detach'],timeout=300,output_limit=99999999)
    runner._call(['run','--detach'])
    with pytest.raises(ValueError):runner._call(['run','--detach'])
    with pytest.raises(ValueError):runner._call(['pull','image'])
    assert len(calls)==2 and len(stopped)==4
    assert calls[0][1]['timeout']==60 and calls[0][1]['output_limit']==4194304
    assert 'API_TOKEN' not in calls[0][1]['env'] and 'DOCKER_CONTEXT' not in calls[0][1]['env']
    assert calls[0][0][1:3]==['--config',str(A.ROOT/'docker-client-config')]


@pytest.mark.parametrize('failure',['red_assertion','cleanup','slow_startup'])
def test_actual_runner_preserves_failure_history_and_no_creation_retry(monkeypatch,tmp_path,failure):
    source=tmp_path/'source';write_sources(source);monkeypatch.setattr(A,'WORKSPACE',source)
    docker=InertDocker(policy(),tmp_path/'registry',monkeypatch);original=docker.call
    def fault(runner,args,kw):
        result=original(runner,args,kw)
        if failure=='red_assertion' and args[0]=='exec' and args[-2].endswith('-assertions.json'):
            rows=json.loads(result.stdout)
            for row in rows:row['assertion']=None
            result.stdout=json.dumps(rows).encode()
        return result
    if failure=='cleanup':
        def no_remove(runner,args,kw):
            if args[0]=='rm':return docker.result(code=1,err=b'fixture denied')
            return original(runner,args,kw)
        docker.call=no_remove
    else:docker.call=fault
    if failure=='slow_startup':
        run=docker.runner.run
        def slow(*a,**kw):
            receipt=run(*a,**kw);receipt['startup_sla_met']=False;receipt['startup_seconds']=2.;return receipt
        monkeypatch.setattr(docker.runner,'run',slow)
    report={'nonce':'a'*32};phases=[]
    if failure=='slow_startup':
        A.execute_cycle(docker.runner,None,phases.append,report)
        assert report['physical_boundary_verified'] and not report['startup_sla_met'] and report['cleanup_verified']
    else:
        from cochem_pipeline.containers import ContainerCleanupError
        with pytest.raises(ContainerCleanupError if failure=='cleanup' else ValueError):A.execute_cycle(docker.runner,None,phases.append,report)
        assert not report.get('physical_boundary_verified')
        if failure=='red_assertion':assert report['cleanup_verified'] and phases[-1]=='red_execution'
        else:assert not report['cleanup_verified'] and report['final_registry_counts']['quarantined']>=1
    assert len([c for c in docker.calls if c[0]=='run'])==2
    assert list((tmp_path/'registry').glob('*.receipt.json'))


def foundation():
    packet={'schema':'cochem-execution-foundation-inputs/2','install_receipt_sha256':'1'*64,'preflight_receipt_sha256':'2'*64,
        'knowledge_receipt_sha256':'a'*64,'workers':{s:str(i)*64 for i,s in enumerate(A.SLOTS,1)},**A.CONTINUITY_PINS}
    revision={'verified':True,'source_sha256':'3'*64}
    value=dict(schema='cochem-execution-foundation/2',status='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED',helper_sha256=A.FOUNDATION_SHA,
        system_sid='S-1-5-18',runtime_root=str(A.INSTALL),install_receipt_sha256=packet['install_receipt_sha256'],
        preflight_receipt_sha256=packet['preflight_receipt_sha256'],activation_ready=False,automatic_retry_allowed=False,
        ram_ledger_created_new=True,ram_scoped_roots_verified=6,containers_created=0,warm_pool_created=0,native_model_jobs_executed=0,
        legacy_databases_or_budgets_modified=False,credentials_or_native_profiles_modified=False,ram_volume_or_startup_task_modified=False,
        existing_tasks_modified_or_run=False,knowledge_service_constructed_or_refreshed=False,general_readiness_called=False,
        revision=revision,ram_before={'fixture':1},ram_after={'fixture':1},nonce='4'*32,packet_sha256='5'*64,ram_ledger_sha256='6'*64,
        registry={'registry_created_new':True,'capacity':4,'unknown_owned':0,'work_rows':0,'integrity_check':'ok','journal_mode':'wal',
            'database_sha256':'7'*64,'owner_sha256':'8'*64})
    value.update(A.CONTINUITY_PINS,original_failure_cause_established=False,worker_receipt_sha256=packet['workers'])
    return value,packet,revision


@pytest.mark.parametrize('field,change',[('status','HELD'),('schema','cochem-execution-foundation/1'),('activation_ready',0),('ram_scoped_roots_verified',4),('helper_sha256','9'*64),
    *[(k,'0'*64) for k in A.CONTINUITY_PINS],('worker_receipt_sha256',{}),('original_failure_cause_established',True),('failure',{}),
    ('ram_after',{}),('revision',{}),('registry',{}),('ram_ledger_sha256','wrong')])
def test_foundation_binding_rejects_incomplete_or_drifted_success(field,change):
    value,packet,revision=foundation();value[field]=change
    with pytest.raises((ValueError,KeyError)):A.foundation_binding(value,packet,revision)


def ps_functions():
    return "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;"+f"$path='{WRAPPER}';"+r'''
    foreach($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$module\$module.psd1") -ErrorAction Stop}
    $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
    if($e.Count){throw ($e|Out-String)}
    foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){. ([scriptblock]::Create($f.Extent.Text))}
    '''+f'''
    foreach($definition in @(Import-PinnedFunctions '{HERE/'inspect-execution-prerequisites-r3.ps1'}' '5c3c043b9f10d70096d077fd0fd00f10edfb27739c4996d9477239b0692b4887' @('Get-TaskOrAbsent','Assert-Stopped','Assert-PassedTask','Write-NewPacket','Wait-Inspection'))){{. ([scriptblock]::Create($definition))}}
    '''


def ps(tmp_path,body):
    script=tmp_path/'fixture.ps1';script.write_text(ps_functions()+body)
    result=subprocess.run([str(PS),'-NoProfile','-File',str(script)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return result.stdout


@pytest.mark.parametrize('change',['','held','capacity','prior','task_args','running','task_result','old_schema','diagnostic','packet_drift','workers','failure'])
def test_real_ps_foundation_packet_and_task_gate(tmp_path,change):
    value,packet,revision=foundation()
    if change=='held':value['status']='EXECUTION_FOUNDATION_HELD'
    if change=='capacity':value['registry']['capacity']=8
    if change=='prior':value['preflight_receipt_sha256']='9'*64
    if change=='old_schema':value['schema']='cochem-execution-foundation/1'
    if change=='diagnostic':value['diagnostic_receipt_sha256']='0'*64
    if change=='workers':value['worker_receipt_sha256']={}
    if change=='failure':value['failure']={}
    runtime={'install_receipt_sha256':packet['install_receipt_sha256'],'revision':revision}
    fixture=tmp_path/'data.json';fixture.write_text(json.dumps({'value':value,'packet':packet,'runtime':runtime}))
    body=fr'''
    $data=Get-Content -LiteralPath '{fixture}' -Raw|ConvertFrom-Json
    $foundationRoot='{A.FOUNDATION_ROOT}';$foundationHash='{A.FOUNDATION_SHA}';$installRoot='{A.INSTALL}';$python=Join-Path $installRoot '.venv\Scripts\python.exe';$change='{change}'
    function Get-ContinuationPacket {{param($Runtime)
        $copy=[ordered]@{{}};foreach($p in $data.packet.PSObject.Properties){{$copy[$p.Name]=$p.Value}}
        $copy.workers=[ordered]@{{}};foreach($p in $data.packet.workers.PSObject.Properties){{$copy.workers[$p.Name]=$p.Value}};$copy
    }}
    function Read-R3Control {{param($Path,$Hash,$Maximum)
        $v=$data.value
        if((Split-Path -Leaf $Path) -ceq 'inputs.json'){{$v=$data.packet;if($change -ceq 'packet_drift'){{$v.knowledge_receipt_sha256='changed'}}}}
        [pscustomobject]@{{Sha256=('a'*64);Fixture=$v}}
    }}
    function Read-R3Text {{param($Control)$Control.Fixture|ConvertTo-Json -Depth 8}}
    '''+r'''
    function Get-TaskOrAbsent {param($Name)
        if($Name -cne 'CoChem-4.2.7-ExecutionFoundation-20261007-r3-v2'){throw 'Wrong prior task'}
        $args='-I -B "'+$foundationRoot+'\provision-execution-foundation-r3-v2.py" --nonce '+$data.value.nonce+' --packet-sha256 '+$data.value.packet_sha256
        if($change -ceq 'task_args'){$args+=' changed'}
        $action=[pscustomobject]@{Type=0;Path=$python;Arguments=$args;WorkingDirectory=$foundationRoot}
        $actions=[pscustomobject]@{Count=1;Action=$action};$actions|Add-Member ScriptMethod Item {param($n)$this.Action}
        $task=[pscustomobject]@{State=3;Instances=0;LastTaskResult=0;Definition=[pscustomobject]@{Principal=[pscustomobject]@{UserId='SYSTEM';LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=0};Actions=$actions}}
        if($change -ceq 'running'){$task.Instances=1};if($change -ceq 'task_result'){$task.LastTaskResult=2}
        $task|Add-Member ScriptMethod GetInstances {param($n)[pscustomobject]@{Count=$this.Instances}};$task
    }
    $accepted=$false;$packet=$null;try{$packet=Get-AcceptancePacket $data.runtime;$accepted=$true}catch{if(-not $change){throw}}
    @{accepted=$accepted;packet=$packet}|ConvertTo-Json -Depth 8 -Compress
    '''
    out=json.loads(ps(tmp_path,body));assert out['accepted']==(not change)
    if not change:assert out['packet']['schema']=='cochem-docker-physical-inputs/2' and out['packet']['foundation_receipt_sha256']=='a'*64


def test_real_five_copy_packet_apply_path_stops_at_simulated_task(tmp_path):
    paths=[HERE/name for name in ('accept-docker-execution-r3-v2.py','provision-execution-foundation-r3-v2.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py','diagnose-foundation-docker-r3-v1.py')]
    target=tmp_path/'fresh'
    body=f"$root='{target}';"+''.join(f"${name}='{path}';${name}Hash='{hashlib.sha256(path.read_bytes()).hexdigest()}';" for name,path in zip(('source','foundation','preflight','support','diagnostic'),paths))+fr'''
    $installRoot='C:\fixture-r3';$basePython='C:\fixture-base\python.exe';$python='C:\fixture-r3\python.exe';$taskName='FIXTURE-NO-REAL-TASK'
    $copy='{REPO/'scripts/stage_aetherdesk_427_payloads.ps1'}';$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($copy,[ref]$t,[ref]$e)
    foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Initialize-FileIdentity','Open-VerifiedFile','Copy-VerifiedPayload')}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
    Initialize-FileIdentity
    '''+r'''
    $held=[Collections.Generic.List[IO.FileStream]]::new()
    function Assert-NoReparseAncestors {param($Path)};function Assert-ProtectedPath {param($Path)};function Assert-Stopped {}
    function Assert-OriginalFailedDenial {param([switch]$RequireTask)};function Assert-DockerNativeCustody {}
    function Get-AcceptancePacket {param($Runtime)[ordered]@{fixture=$true}};function Get-TaskOrAbsent {param($Name)$null}
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
        if($Flags -ne 2 -or $User -cne 'SYSTEM' -or $Logon -ne 5 -or $Definition.Principal.RunLevel -ne 1 -or $Definition.Triggers.Count -ne 0 -or $Definition.Settings.ExecutionTimeLimit -cne 'PT10M'){throw 'Task creation contract differs'}
        $packet=Get-Content -LiteralPath (Join-Path $root 'inputs.json') -Raw|ConvertFrom-Json
        if($packet.fixture -ne $true -or $action.Arguments -cnotmatch 'accept-docker-execution-r3-v2.py" --nonce [a-f0-9]{32} --packet-sha256 [a-f0-9]{64}$'){throw 'Bound packet/task argv differs'}
        if($held.Count -ne 6 -or @(Get-ChildItem -LiteralPath (Join-Path $root 'docker-client-config') -Force).Count -ne 0){throw 'Held copies/packet or empty config differs'}
        throw 'SIMULATED_SYSTEM_TASK_BOUNDARY'
    }
    $reached=$false;try{Invoke-Acceptance ([ordered]@{}) ([ordered]@{fixture=$true})}catch{if($_.Exception.Message -notmatch 'SIMULATED_SYSTEM_TASK_BOUNDARY'){throw};$reached=$true}finally{foreach($s in $held){$s.Dispose()}}
    if(-not $reached){throw 'Control flow did not reach registration'};'REAL_FIVE_COPIES_VERIFIED'
    '''
    assert 'REAL_FIVE_COPIES_VERIFIED' in ps(tmp_path,body)
    for path in paths:assert (target/path.name).read_bytes()==path.read_bytes()


def test_wrapper_nonadmin_apply_stops_before_actions():
    result=subprocess.run([str(PS),'-NoProfile','-File',str(WRAPPER),'-Apply'],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0 and '-Apply requires the owner' in result.stderr


@pytest.mark.parametrize('tamper',[False,True])
def test_assembled_run_exact_support_and_current_control_shapes_at_inert_native_boundaries(monkeypatch,tmp_path,tamper):
    """No privileged action; verify the assembled names/paths and pre-write gate."""
    from contextlib import contextmanager
    from cochem_pipeline import windows as win,config as config_module,ramdisk
    from cochem_pipeline.containers import DockerRunner
    ts=importlib.util.spec_from_file_location('foundation_fixture_support',HERE/'test_execution_foundation_r3.py')
    T=importlib.util.module_from_spec(ts);ts.loader.exec_module(T)
    _,revision,workers=T.actual_public_inputs();pre,kb=T.knowledge_fixture()
    raw_config=json.loads((A.INSTALL/'pipeline.json').read_bytes());layout=json.loads((A.INSTALL/'windows-layout.json').read_bytes())
    actual_config=config_module.load_config(str(A.INSTALL/'pipeline.json'))
    root=tmp_path/'helper';root.mkdir();private=tmp_path/'private';private.mkdir();workspace=tmp_path/'ram-fixture'
    for name in ('accept-docker-execution-r3-v2.py','provision-execution-foundation-r3-v2.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py','diagnose-foundation-docker-r3-v1.py'):
        (root/name).write_bytes((HERE/name).read_bytes())
    (root/'docker-client-config').mkdir();(private/'ramdisk-state.json').write_bytes(b'fixture-ledger')
    baseline_runner=DockerRunner(actual_config.docker,private/'containers');database=private/'containers/containers.db'
    baseline=database.read_bytes()
    monkeypatch.setattr(A,'ROOT',root);monkeypatch.setattr(A,'PRIVATE',private);monkeypatch.setattr(A,'WORKSPACE',workspace)
    monkeypatch.setattr(A,'__file__',str(root/'accept-docker-execution-r3-v2.py'))
    monkeypatch.setattr(sys,'executable',str(A.INSTALL/'.venv/Scripts/python.exe'))
    for name in ('require_system','validate_code_path','validate_private_directory','validate_private_path'):monkeypatch.setattr(win,name,lambda *a,**k:None)
    monkeypatch.setattr(win,'_account_sid',lambda v:v)
    sids={row['name']:layout['slots'][slot]['sid'] for slot,row in raw_config['workers'].items()}
    monkeypatch.setattr(win,'_sid_text',lambda value:sids[value])
    config=SimpleNamespace(private_root=private,ramdisk=actual_config.ramdisk,docker=actual_config.docker,operator_name=actual_config.operator_name)
    monkeypatch.setattr(config_module,'load_config',lambda *a:config)
    descriptor=object()
    class Manager:
        def __init__(self,*a):pass
        def inspect(self,require_capacity):assert require_capacity
        def workspace(self,slot):assert slot=='slot1';return descriptor
    monkeypatch.setattr(ramdisk,'RamdiskManager',Manager)
    history=[]
    @contextmanager
    def exclusion(*a):
        history.append('exclusion');yield;history.append('released')
    monkeypatch.setattr(A,'reserve_worker_identities',exclusion)
    packet={'schema':'cochem-docker-physical-inputs/2','install_receipt_sha256':hashlib.sha256((A.INSTALL/'install-after.json').read_bytes()).hexdigest(),
        'preflight_receipt_sha256':'a'*64,'foundation_receipt_sha256':'b'*64,'knowledge_receipt_sha256':pre['KNOWLEDGE_SHA'],
        'workers':{s:hashlib.sha256(raw).hexdigest() for s,(_,raw) in workers.items()},**A.CONTINUITY_PINS}
    value,_,_=foundation();value.update(install_receipt_sha256=packet['install_receipt_sha256'],preflight_receipt_sha256=packet['preflight_receipt_sha256'],revision=revision,
        ram_ledger_sha256=hashlib.sha256(b'fixture-ledger').hexdigest(),worker_receipt_sha256=packet['workers'])
    value['registry'].update(database_sha256=hashlib.sha256(baseline).hexdigest(),owner_sha256=hashlib.sha256(baseline_runner.owner.encode()).hexdigest())
    docker={'server':{'pid':1},'engine':{'fixture':True},'api_aliases':['fixture'],'ownership_census':{'empty':True}}
    value['docker_before']=docker
    if tamper:value['status']='EXECUTION_FOUNDATION_HELD'
    prior=T.preflight()[0];prior.update(install_receipt_sha256=packet['install_receipt_sha256'],worker_receipt_sha256=packet['workers'],revision=revision)
    fixture={root/'inputs.json':json.dumps(packet).encode(),pre['STATE']/'writer.lock':b'0',pre['STATE']/'current.json':b'{}',pre['STATE']/'sources.json':b'{}',
        pre['STATE']/kb['generation']/'knowledge_index.db':b'index',pre['KNOWLEDGE']:json.dumps(kb).encode(),
        pre['PRIOR']/'source-manifest.json':(pre['PRIOR']/'source-manifest.json').read_bytes(),A.INSTALL/'source-manifest.json':(A.INSTALL/'source-manifest.json').read_bytes(),
        A.FOUNDATION_ROOT/'execution-foundation.json':json.dumps(value).encode(),T.F.PREFLIGHT_ROOT/'execution-prerequisites.json':json.dumps(prior).encode()}
    fixture.update({path:raw for path,raw in workers.values()})
    historical={A.DIAGNOSTIC_ROOT/'foundation-diagnostic.json':'diagnostic_receipt_sha256',A.DIAGNOSTIC_ROOT/'inputs.json':'diagnostic_packet_sha256',
        A.FAILED_FOUNDATION_ROOT/'execution-foundation.json':'failed_foundation_receipt_sha256',A.FAILED_FOUNDATION_ROOT/'inputs.json':'failed_foundation_packet_sha256'}
    def namespace(**kw):
        if 'verify_r3_runtime' in kw:
            assert kw['CONFIG_SHA256']==hashlib.sha256((A.INSTALL/'pipeline.json').read_bytes()).hexdigest()
            kw['verify_r3_runtime']=lambda *a:revision
            kw['protected_json']=lambda w,p:(layout if p.name=='windows-layout.json' else raw_config,kw['CONFIG_SHA256'])
            kw['require_stopped']=lambda *a:history.append('stopped')
        elif 'preflight_binding' in kw:kw['production_ram_lock']=exclusion
        elif 'held_read' in kw:
            @contextmanager
            def held(path,maximum,w,expected=None,private=False,retain=True):
                if path in historical:
                    assert expected==A.CONTINUITY_PINS[historical[path]]
                    history.append(historical[path]);yield b'bound historical evidence fixture'
                elif path in fixture:yield fixture[path]
                elif path.parent==root or path==database or path.name=='ramdisk-state.json':yield path.read_bytes()
                elif path.name=='python.exe':yield b'bound base fixture'
                elif not retain:yield None
                else:raise AssertionError('Unexpected custody path')
            kw['held_read']=held;kw['docker_import_custody']=lambda *a:None
            kw['ram_observation']=lambda *a:value['ram_after'];kw['docker_observation']=lambda *a:docker
        return SimpleNamespace(**kw)
    monkeypatch.setattr(A,'SimpleNamespace',namespace)
    def sources(*a):history.append('source_creation');workspace.mkdir()
    monkeypatch.setattr(A,'create_sources',sources)
    def cycle(runner,desc,progress,report):
        assert desc is descriptor and runner.owner==baseline_runner.owner
        history.append('execution_boundary')
        report.update(physical_boundary_verified=True,startup_sla_met=True,cleanup_verified=True,prepared_count=2)
    monkeypatch.setattr(A,'execute_cycle',cycle)
    result=A.run('c'*32,'d'*64)
    output=json.loads((root/'docker-physical-acceptance.json').read_bytes())
    if tamper:
        assert result==2 and output['failure']['phase']=='prior_receipts'
        assert not workspace.exists() and database.read_bytes()==baseline
        assert not output['ram_fixture_creation_started'] and not output['foundation_registry_mutation_started']
    else:
        assert result==0 and output['status']=='DOCKER_PHYSICAL_ACCEPTANCE_VERIFIED'
        assert 'execution_boundary' in history and output['ram_ledger_preserved'] and output['registry_owner_preserved']
        assert output['ram_fixture_creation_started'] and output['foundation_registry_mutation_started']
        assert set(A.CONTINUITY_PINS)<=set(history)


def test_wrapper_binds_exact_helper_bytes():
    assert hashlib.sha256((HERE/'accept-docker-execution-r3-v2.py').read_bytes()).hexdigest() in WRAPPER.read_text()


def test_frozen_v1_preserved_and_physical_methods_unchanged():
    for name,pin in [('accept-docker-execution-r3-v1.py','3ac01c18be77c18b6c80601a681f7b3c082424e88d2f79a29fe15dc9a63aa0c5'),
        ('accept-docker-execution-r3-v1.ps1','380386520ace322d7967382d8778b9751bb510f13ce391cd7e92ee3b000bf035')]:
        assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==pin
    def functions(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    before=functions(HERE/'accept-docker-execution-r3-v1.py');after=functions(HERE/'accept-docker-execution-r3-v2.py')
    assert set(after)==set(before)|{'failure_chain'}
    for name in before.keys()-{'foundation_binding','run','main'}:assert before[name]==after[name],name


@pytest.mark.parametrize('key',list(A.CONTINUITY_PINS))
def test_success_does_not_accept_changed_historical_packet(key):
    value,packet,revision=foundation();A.foundation_binding(value,packet,revision)
    packet[key]='0'*64
    with pytest.raises(ValueError,match='diagnostic authority'):A.foundation_binding(value,packet,revision)


def diagnostic_support():
    path=HERE/'diagnose-foundation-docker-r3-v1.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==A.DIAGNOSTIC_SHA
    spec=importlib.util.spec_from_file_location('physical_failure_diagnostic_support',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_numeric_and_causal_failure_preserved_without_raw_secret_text():
    from cochem_pipeline.windows import WindowsIsolationError
    error=WindowsIsolationError('Cannot inspect Docker pipe server (Windows error 231)')
    error.__cause__=PermissionError('secret-token-and-private-path-must-not-escape')
    error.__cause__.winerror=5
    chain=A.failure_chain(error,diagnostic_support())
    assert chain['nodes'][0]['operation']=='pipe_open' and chain['nodes'][0]['winerror']==231
    assert chain['nodes'][1]['parent']==0 and chain['nodes'][1]['relation']=='cause' and chain['nodes'][1]['winerror']==5
    assert 'secret-token' not in json.dumps(chain) and chain['raw_exception_text_published'] is False


def test_failure_before_diagnostic_load_retains_only_bounded_root_metadata(monkeypatch,capsys):
    from cochem_pipeline.windows import WindowsIsolationError
    error=WindowsIsolationError('secret-unverified-support-message')
    error.winerror=2**32;error.__cause__=ValueError('another-secret')
    chain=A.failure_chain(error)
    assert len(chain['nodes'])==1 and chain['nodes'][0]['winerror'] is None and chain['truncated'] is True
    assert 'secret' not in json.dumps(chain)
    def reject(*a):raise error
    monkeypatch.setattr(A,'run',reject);monkeypatch.setattr(sys,'argv',['helper','--nonce','a'*32,'--packet-sha256','b'*64])
    assert A.main()==3
    output=json.loads(capsys.readouterr().err)
    assert output['status']=='PRE_RECEIPT_FAILURE' and output['chain']==chain and 'secret' not in json.dumps(output)


def test_ps_failure_chain_projection_rejects_unapproved_metadata(tmp_path):
    from cochem_pipeline.windows import WindowsIsolationError
    chain=A.failure_chain(WindowsIsolationError('Cannot inspect Docker pipe server (Windows error 231)'),diagnostic_support())
    fixture=tmp_path/'chain.json';fixture.write_text(json.dumps(chain))
    body=fr'''
    foreach($definition in @(Import-PinnedFunctions '{HERE/'diagnose-foundation-docker-r3-v1.ps1'}' '06522b04378d8d0b6cb915102e7fce1125700d96f68159c13e9d26a5c1c52e0b' @('ConvertTo-SafeDiagnosticChain'))){{. ([scriptblock]::Create($definition))}}
    $chain=Get-Content -LiteralPath '{fixture}' -Raw|ConvertFrom-Json
    $safe=ConvertTo-SafeDiagnosticChain $chain
    $chain.nodes[0]|Add-Member NoteProperty secret 'do-not-print'
    $rejected=$false;try{{$null=ConvertTo-SafeDiagnosticChain $chain}}catch{{$rejected=$true}}
    if(-not $rejected){{throw 'Unapproved metadata accepted'}}
    $safe|ConvertTo-Json -Depth 6 -Compress
    '''
    safe=json.loads(ps(tmp_path,body))
    assert safe['nodes'][0]['winerror']==231 and 'secret' not in json.dumps(safe)
