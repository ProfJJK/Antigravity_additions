"""Ordinary Windows fixtures only; no Docker, SYSTEM, worker or state action."""
from contextlib import contextmanager
import ast
import copy
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

HERE=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0,str(REPO/'src'))


def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


F=load('foundation_v2','provision-execution-foundation-r3-v2.py')
D=load('diagnostic_support_fixture','diagnose-foundation-docker-r3-v1.py')
P=load('preflight_support_fixture','inspect-execution-prerequisites-r3.py')
OLD=load('old_foundation_fixtures','test_execution_foundation_r3.py')


def actual_controls():
    paths=[F.PREFLIGHT_ROOT/'execution-prerequisites.json',D.FOUNDATION_ROOT/'execution-foundation.json',
        D.FOUNDATION_ROOT/'inputs.json',F.PREFLIGHT_ROOT/'inputs.json',F.DIAGNOSTIC_ROOT/'foundation-diagnostic.json',F.DIAGNOSTIC_ROOT/'inputs.json']
    pins=[D.PREFLIGHT_RECEIPT_SHA,D.FOUNDATION_RECEIPT_SHA,D.FOUNDATION_PACKET_SHA,D.PREFLIGHT_PACKET_SHA,F.DIAGNOSTIC_RECEIPT_SHA,F.DIAGNOSTIC_PACKET_SHA]
    raw=[p.read_bytes() for p in paths]
    assert [hashlib.sha256(v).hexdigest() for v in raw]==pins
    prior,failed,old,old_pre,diagnostic,diagnostic_packet=map(json.loads,raw)
    packet={**diagnostic_packet,'schema':'cochem-execution-foundation-inputs/2','diagnostic_receipt_sha256':F.DIAGNOSTIC_RECEIPT_SHA,'diagnostic_packet_sha256':F.DIAGNOSTIC_PACKET_SHA}
    return packet,diagnostic,diagnostic_packet,prior,failed,old,old_pre,prior['revision']


def test_actual_successful_diagnostic_and_original_receipt_hashes_bind():
    F.diagnostic_binding(P,D,*actual_controls())


@pytest.mark.parametrize('change',['status','helper','nonce','packet','provisioned','boolean_type','failure','postfailure','events','unfinished','retried','revision','workers','ram','fresh','docker','old_failure','old_packet','input_pin'])
def test_continuation_requires_exact_complete_preserved_authority(change):
    args=list(actual_controls());packet,diag,diag_packet,prior,failed,old,old_pre,revision=args
    if change=='status':diag['status']='FOUNDATION_DIAGNOSTIC_HELD'
    elif change=='helper':diag['helper_sha256']='a'*64
    elif change=='nonce':diag['nonce']='0'*32
    elif change=='packet':diag['packet_sha256']='a'*64
    elif change=='provisioned':diag['ram_provision_started']=True
    elif change=='boolean_type':diag['activation_ready']=0
    elif change=='failure':diag['failure']={}
    elif change=='postfailure':diag['preservation_failure']={}
    elif change=='events':diag['docker_trace']['events'].pop()
    elif change=='unfinished':diag['docker_trace']['events'][0]['completed']=False
    elif change=='retried':diag['docker_trace']['retry_attempts']=1
    elif change=='revision':diag['revision']={}
    elif change=='workers':diag['worker_receipt_sha256']['slot4']='a'*64
    elif change=='ram':diag['ram_after']['root_file_id']+=1
    elif change=='fresh':diag['fresh_state_after']['ram_ledger_absent']=False
    elif change=='docker':diag['docker']['ownership_census']['empty']=False
    elif change=='old_failure':failed['registry_provision_started']=True
    elif change=='old_packet':old['workers']['slot5']='a'*64
    elif change=='input_pin':packet['diagnostic_receipt_sha256']='b'*64
    with pytest.raises(ValueError):F.diagnostic_binding(P,D,*args)


@pytest.mark.parametrize('name',['require_absent','fresh_ram_type','production_ram_lock','registry_inspection','create_fresh_registry'])
def test_frozen_provisioning_and_no_replacement_guards_are_unchanged(name):
    def body(path):
        source=path.read_text();tree=ast.parse(source)
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
        return ast.get_source_segment(source,function)
    assert body(HERE/'provision-execution-foundation-r3.py')==body(HERE/'provision-execution-foundation-r3-v2.py')


def test_new_namespace_keeps_actual_registry_wal_header_and_schema_contract(monkeypatch,tmp_path):
    monkeypatch.setattr(OLD,'F',F)
    OLD.test_real_installed_constructor_immutable_wal_and_preserved_read(tmp_path,monkeypatch)


def test_existing_registry_and_concurrent_file_are_preserved(monkeypatch,tmp_path):
    monkeypatch.setattr(OLD,'F',F)
    OLD.test_registry_refuses_existing_root_or_concurrent_database(tmp_path)


def test_ram_ledger_exclusive_create_and_partial_failure_preservation(monkeypatch,tmp_path):
    monkeypatch.setattr(OLD,'F',F)
    OLD.test_ram_write_failure_preserves_partial_file_and_refuses_retry(monkeypatch,tmp_path)


@pytest.mark.parametrize('failure',['','fresh','ram_drift','docker_drift','docker_before_ram','after_ram','docker_before_registry','second_docker_drift','registry'])
def test_full_continuation_real_public_bindings_collector_and_sqlite_with_inert_native_boundaries(monkeypatch,tmp_path,failure):
    """Exact collector/constructor with disposable paths and inert native I/O.

    Public controls are real; the Docker daemon ID baseline and private index
    byte boundary are explicit fixtures. No protected state is opened for write.
    """
    from cochem_pipeline import windows as win,config as config_module,ramdisk,containers,deployment,knowledge as knowledge_module
    packet,diag,diag_packet,prior,failed,old,old_pre,revision=actual_controls()
    actual_config=config_module.load_config(str(F.INSTALL/'pipeline.json'))
    config_raw=(F.INSTALL/'pipeline.json').read_bytes();raw_config=json.loads(config_raw)
    layout=json.loads((F.INSTALL/'windows-layout.json').read_bytes())
    source=tmp_path/'helper';source.mkdir()
    for name in ('provision-execution-foundation-r3-v2.py','inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py','diagnose-foundation-docker-r3-v1.py'):(source/name).write_bytes((HERE/name).read_bytes())
    (source/'docker-client-config').mkdir()
    private=tmp_path/'private';private.mkdir();boundary=tmp_path/'ram'
    monkeypatch.setattr(F,'ROOT',source);monkeypatch.setattr(F,'PRIVATE',private);monkeypatch.setattr(F,'BOUNDARY',boundary)
    monkeypatch.setattr(F,'__file__',str(source/'provision-execution-foundation-r3-v2.py'))
    monkeypatch.setattr(sys,'executable',str(F.INSTALL/'.venv/Scripts/python.exe'))
    for name in ('require_system','validate_code_path','validate_private_directory','validate_private_path'):monkeypatch.setattr(win,name,lambda *a,**k:None)
    sid_names={row['name']:layout['slots'][slot]['sid'] for slot,row in raw_config['workers'].items()}
    monkeypatch.setattr(win,'_account_sid',lambda name:name);monkeypatch.setattr(win,'_sid_text',lambda name:sid_names[name])
    ramconfig=SimpleNamespace(workspace_root=boundary,adopted_drive=True,mount_root=Path('R:/'),as_dict=actual_config.ramdisk.as_dict)
    config=SimpleNamespace(private_root=private,ramdisk=ramconfig,docker=actual_config.docker,operator_name=actual_config.operator_name)
    monkeypatch.setattr(config_module,'load_config',lambda *a:config)
    monkeypatch.setattr(knowledge_module,'_private_directory',lambda path,exist_ok=False:path.mkdir(exist_ok=exist_ok))
    history=[];exclusions={'preserved-old-exclusion'};cli_calls=[];attest_calls=[]
    monkeypatch.setattr(win,'defender_exclusions',lambda:set(exclusions))
    info=dict(OSType='linux',ID='fixture-daemon-id',MemoryLimit=True,SwapLimit=True,CpuCfsQuota=True,PidsLimit=True,SecurityOptions=['seccomp'])
    # The real collector hashes rather than publishes the daemon ID. This
    # deterministic fixture ID is mirrored only in derived receipt fixtures.
    prior['docker']['engine']=P.daemon_info(info);diag['docker']['engine']=P.daemon_info(info)
    server=prior['docker']['server']
    def attest(endpoint,*args,**kwargs):
        attest_calls.append(endpoint)
        if (failure=='docker_before_ram' and len(attest_calls)==8) or (failure=='docker_before_registry' and len(attest_calls)==22):
            ctypes.set_last_error(232);win._check(False,'Inspect actual Docker pipe server PID')
        return {**server,'checked_at':len(attest_calls)}
    def boundary_check(endpoint,identities,**kwargs):
        result={}
        for alias in (P.ENDPOINT,'npipe:////./pipe/docker_engine','npipe:////./pipe/dockerDesktopWindowsEngine'):
            observed=deployment.attest_docker_pipe_server(alias,identities,**kwargs)
            result[alias]=[dict(slot=s,access_denied=True,denied_modes=['write','read','read_write'],server=observed) for s in F.SLOTS]
        return result
    def transport(argv,**kwargs):
        cli_calls.append(argv)
        assert argv[:5]==[str(P.DOCKER),'--config',str(source/'docker-client-config'),'--host',P.ENDPOINT]
        assert kwargs['timeout']==20 and kwargs['output_limit']==1048576
        if argv[5]=='info':
            current={**info}
            if failure=='docker_drift' or (failure=='second_docker_drift' and len(cli_calls)>4):current['ID']='changed-but-valid-daemon'
            out=json.dumps(current).encode()
        elif argv[5]=='image':out=json.dumps([dict(Id=P.IMAGE,Os='linux',Config={'Env':['PATH=/usr/bin']})]).encode()
        else:out=b''
        return SimpleNamespace(returncode=0,timed_out=False,cancelled=False,output_exceeded=False,stderr=b'',stdout=out)
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',attest);monkeypatch.setattr(deployment,'verify_docker_access_boundary',boundary_check)
    monkeypatch.setattr(containers,'_bounded_process',transport)
    class NativeBoundaryFixture:
        def __init__(self,cfg,pvt,ids):self.config=cfg;self.private_root=pvt
        def ensure(self):
            history.append('ensure');boundary.mkdir()
            for slot in F.SLOTS:(boundary/slot).mkdir()
            exclusions.update(str(boundary/s).casefold() for s in F.SLOTS)
            evidence={'state':'READY','adopted_existing_drive':True,'workspace_root':str(boundary),'slots':list(F.SLOTS),
                'config':ramconfig.as_dict(),'observed':diag['ram_after']['volume'],'mount_root':str(Path('R:/')),
                'lifecycle_action':'ADOPT','backup':None,'volume_root_metadata_preserved':True}
            self._save_state(evidence);return evidence
        def inspect(self,require_capacity):
            assert require_capacity
            if failure=='after_ram':raise ValueError('private preservation test detail')
    monkeypatch.setattr(ramdisk,'RamdiskManager',NativeBoundaryFixture)
    @contextmanager
    def mutex(*a):
        history.append('lock')
        try:yield
        finally:history.append('unlock')
    monkeypatch.setattr(F,'production_ram_lock',mutex)
    knowledge={'schema':'cochem-published-knowledge-verification/1','status':'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':P.KNOWLEDGE_HELPER_SHA,'runtime_root':str(P.PRIOR),'corpus_files':138,
        'corpus_bytes_preserved':True,'original_root_identity_and_security_preserved':True,'original_writer_lock_preserved':True,
        'index_size_sla_met':True,'knowledge_service_constructed':False,'index_refreshed_or_repaired':False,
        'existing_files_or_acls_modified':False,'activation_ready':False,'generation':prior['knowledge']['generation'],
        'index_sha256':prior['knowledge']['index_sha256'],'current_pointer_sha256':'b'*64,'source_pins_sha256':'c'*64}
    fixtures={source/'inputs.json':json.dumps(packet).encode(),F.PREFLIGHT_ROOT/'execution-prerequisites.json':json.dumps(prior).encode()}
    fixtures.update({F.DIAGNOSTIC_ROOT/'foundation-diagnostic.json':json.dumps(diag).encode(),P.KNOWLEDGE:json.dumps(knowledge).encode(),
        P.STATE/'writer.lock':b'0',P.STATE/'current.json':json.dumps({'generation':knowledge['generation']}).encode(),
        P.STATE/'sources.json':b'{}',P.STATE/knowledge['generation']/'knowledge_index.db':b'synthetic-index'})
    reads=[];closes=[]
    def namespace(**kwargs):
        if 'verify_r3_runtime' in kwargs:
            assert kwargs['CONFIG_SHA256']==hashlib.sha256(config_raw).hexdigest()
            kwargs['verify_r3_runtime']=lambda *a:revision
            kwargs['protected_json']=lambda w,path:(layout if path.name=='windows-layout.json' else raw_config,kwargs['CONFIG_SHA256'])
            kwargs['require_stopped']=lambda *a:history.append('stopped')
        elif 'SUPPORT_SHA' in kwargs:
            @contextmanager
            def held(path,maximum,w,expected=None,private=False,retain=True):
                reads.append(path)
                if path in fixtures:raw=fixtures[path]
                elif not retain:raw=None
                else:
                    raw=path.read_bytes()
                    if expected:assert hashlib.sha256(raw).hexdigest()==expected
                try:yield raw
                finally:closes.append(path)
            kwargs['held_read']=held
            def fresh(*a):
                history.append('fresh')
                if failure=='fresh':raise ValueError('Existing target fixture')
                assert not boundary.exists() and not (private/'ramdisk-state.json').exists() and not (private/'containers').exists()
            def ram_observation(*a):
                observed=copy.deepcopy(diag['ram_after'])
                if failure=='ram_drift':observed['root_file_id']+=1
                return observed
            kwargs['fresh_state']=fresh;kwargs['ram_observation']=ram_observation;kwargs['docker_import_custody']=lambda *a:None
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(F,'SimpleNamespace',namespace)
    if failure=='registry':
        def partial_registry(*a):
            (private/'containers').mkdir();(private/'containers/preserved-partial').write_bytes(b'partial');raise ValueError('Do not expose private detail')
        monkeypatch.setattr(F,'create_fresh_registry',partial_registry)
    code=F.run('0'*32,'1'*64);report=json.loads((source/'execution-foundation.json').read_bytes())
    assert report['schema']=='cochem-execution-foundation/2' and report['diagnostic_receipt_sha256']==F.DIAGNOSTIC_RECEIPT_SHA
    assert report['failed_foundation_receipt_sha256']==D.FOUNDATION_RECEIPT_SHA
    assert report['activation_ready'] is False and report['original_failure_cause_established'] is False
    assert len(reads)==len(closes) and history[-1]=='unlock'
    assert 'private detail' not in json.dumps(report) and 'private preservation test detail' not in json.dumps(report)
    if not failure:
        assert code==0 and report['status']=='SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'
        assert report['registry']['capacity']==4 and report['registry']['work_rows']==0
        assert [row['observation'] for row in report['docker_traces']]==['before_ram','before_registry']
        assert [row['trace']['commands_started'] for row in report['docker_traces']]==[4,4] and len(cli_calls)==8
    else:
        assert code==2 and report['status']=='EXECUTION_FOUNDATION_HELD' and report['partial_outputs_preserved']
        if failure in ('fresh','ram_drift','docker_drift','docker_before_ram'):
            assert report['ram_provision_started'] is False and not boundary.exists() and not (private/'ramdisk-state.json').exists()
        else:assert report['ram_provision_started'] and (private/'ramdisk-state.json').exists()
        assert report['registry_provision_started']==(failure=='registry')
        if failure in ('docker_before_ram','docker_before_registry'):
            assert report['failure']['phase']=='docker_owner_census'
            assert report['failure']['chain']['nodes'][0]['operation']=='pipe_server_pid'
            assert report['failure']['chain']['nodes'][0]['winerror']==232
            assert report['docker_traces'][-1]['trace']['events'][-1]['position']=='before_cli'
            assert len(cli_calls)==(2 if failure=='docker_before_ram' else 6)
        if failure=='registry':assert (private/'containers/preserved-partial').read_bytes()==b'partial'
        if failure=='ram_drift':assert report['failure']['phase']=='ram_baseline' and report['ram_before']!=diag['ram_after']
        if failure=='docker_drift':assert report['failure']['phase']=='docker_baseline' and report['docker_before']!=diag['docker']
        if failure=='second_docker_drift':assert report['failure']['phase']=='docker_before_registry_baseline' and report['docker_before_registry']!=report['docker_before']
    if failure not in ('fresh','ram_drift','docker_drift','docker_before_ram'):assert set(p.name for p in boundary.iterdir())==set(F.SLOTS)


def test_early_failure_is_sanitized_before_support_can_be_loaded():
    error=PermissionError('private path');error.winerror=5;error.__cause__=ValueError('hidden')
    result=F.early_error_chain(error)
    assert result['nodes'][0]['winerror']==5 and result['truncated'] and 'private path' not in json.dumps(result)


def test_new_continuation_has_no_unscoped_readiness_or_native_provider_calls():
    tree=ast.parse((HERE/'provision-execution-foundation-r3-v2.py').read_text())
    calls={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else '' for n in ast.walk(tree) if isinstance(n,ast.Call)}
    assert not calls&{'KnowledgeService','execution_readiness','refresh','prepare_pool','run_test','reap','unlink','rmtree','replace','bind_native_caches','environment'}
