"""Ordinary Windows inert fixtures; no Docker, worker or SYSTEM invocation."""
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
import uuid
import pytest

HERE=Path(__file__).parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0,str(REPO/'src'))


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


D=load('diagnostic',HERE/'diagnose-foundation-docker-r3-v1.py')
P=load('frozen_inspector',HERE/'inspect-execution-prerequisites-r3.py')
from cochem_pipeline import windows as win,deployment,containers


def public_inputs():
    paths=[D.PREFLIGHT_ROOT/'execution-prerequisites.json',D.FOUNDATION_ROOT/'execution-foundation.json',
           D.FOUNDATION_ROOT/'inputs.json',D.PREFLIGHT_ROOT/'inputs.json']
    pins=[D.PREFLIGHT_RECEIPT_SHA,D.FOUNDATION_RECEIPT_SHA,D.FOUNDATION_PACKET_SHA,D.PREFLIGHT_PACKET_SHA]
    raws=[p.read_bytes() for p in paths]
    assert [hashlib.sha256(r).hexdigest() for r in raws]==pins
    prior,failed,old,old_preflight=map(json.loads,raws)
    packet={**old,'schema':'cochem-foundation-diagnostic-inputs/1',
            'failed_foundation_receipt_sha256':D.FOUNDATION_RECEIPT_SHA,'failed_foundation_packet_sha256':D.FOUNDATION_PACKET_SHA}
    return packet,prior,failed,old,old_preflight


def test_actual_public_prior_receipts_and_packets_bind():
    packet,prior,failed,old,old_preflight=public_inputs()
    D.prior_bindings(P,packet,prior,failed,old,old_preflight,prior['revision'])


@pytest.mark.parametrize('field,value',[('ram_provision_started',True),('registry_provision_started',True),
    ('nonce','b'*32),('packet_sha256','a'*64),('status','SCOPED_RAM_AND_EMPTY_REGISTRY_VERIFIED'),
    ('failure',{'phase':'another_phase','error_type':'WindowsIsolationError','winerror':None})])
def test_changed_failure_is_never_reused(field,value):
    packet,prior,failed,old,old_preflight=public_inputs();failed[field]=value
    with pytest.raises(ValueError):D.prior_bindings(P,packet,prior,failed,old,old_preflight,prior['revision'])


@pytest.mark.parametrize('message,operation,code',[
    ('Cannot inspect Docker pipe server (Windows error 231)','pipe_open',231),
    ('Inspect actual Docker pipe server PID failed (Windows error 232)','pipe_server_pid',232),
    ('Open actual Docker pipe server process failed (Windows error 5)','server_process_open',5),
    ('Open Docker pipe server token failed (Windows error 5)','server_token_open',5),
    ('Read actual Docker pipe server executable failed (Windows error 87)','server_image_query',87),
    ('Recheck Docker pipe server PID failed (Windows error 109)','pipe_server_pid_recheck',109),
    ('Read security control failed (Windows error 5)','code_acl_control',5),
    ('Read directory ACL failed (Windows error 87)','code_acl_information',87),
    ('Read ACL entry failed (Windows error 1336)','code_acl_entry',1336),
    ('Docker pipe server exited or changed during attestation','server_changed_or_exited',None),
    ('Cannot inspect directory ACL (Windows error 5): C:\\do-not-publish','code_acl_query',5),
    ('Untrusted code writes or path replacement are possible: C:\\do-not-publish','code_acl_rejected',None),
    ('Docker read_write access denial for slot6 was not established (Win32 231)','worker_denial_not_established',231),
    ('do-not-publish failed (Windows error 5)','unclassified',None),
    ('Cannot inspect Docker pipe server (Windows error 9999999999)','pipe_open',None),
])
def test_allowlisted_operation_and_numeric_metadata_only(message,operation,code):
    result=D.error_chain(win.WindowsIsolationError(message))
    assert result['nodes'][0]['operation']==operation and result['nodes'][0]['winerror']==code
    assert message not in json.dumps(result) and 'do-not-publish' not in json.dumps(result)


def test_real_check_loses_code_but_new_classifier_recovers_exact_allowlisted_message():
    for code in (5,231,232):
        ctypes.set_last_error(code)
        with pytest.raises(win.WindowsIsolationError) as caught:win._check(False,'Inspect actual Docker pipe server PID')
        assert getattr(caught.value,'winerror',None) is None
        assert P.safe_failure(caught.value,'docker_owner_census')['winerror'] is None
        assert D.error_chain(caught.value)['nodes'][0]['winerror']==code


def test_both_cause_and_context_retained_without_secret_text_and_cycles_bounded():
    native=PermissionError('secret');native.winerror=5
    context=win.WindowsIsolationError('Cannot inspect Docker pipe server (Windows error 231)')
    outer=ValueError('private outer');outer.__cause__=native;outer.__context__=context;context.__cause__=outer
    result=D.error_chain(outer)
    assert [node.get('winerror') for node in result['nodes'][:3]]==[None,5,231]
    assert result['nodes'][3]['reference']==0
    assert all(value not in json.dumps(result) for value in ('secret','private outer'))
    node=outer
    for _ in range(20):node.__cause__=ValueError('hidden');node=node.__cause__
    result=D.error_chain(outer);assert len(result['nodes'])==8 and result['truncated']


def transport_fixture(monkeypatch,failure=None):
    """Exact frozen collector; native/CLI boundaries explicitly inert."""
    assert hashlib.sha256((HERE/'inspect-execution-prerequisites-r3.py').read_bytes()).hexdigest()==D.PREFLIGHT_SHA
    assert hashlib.sha256(Path(deployment.__file__).read_bytes()).hexdigest()=='3e3eca79e1f8e552c1c837cdd6c81fed2daa193abba3a03445368e6a20939a63'
    trace=D.DiagnosticTrace();calls=[];attests=[]
    server={'pid':7,'process_created_filetime':42,'token_sid':'S-1-5-18','executable':str(P.BACKEND)}
    def attest(endpoint,*args,**kwargs):
        attests.append((trace.stage,trace.events[-1]['position']))
        if failure==attests[-1]:raise win.WindowsIsolationError('Inspect actual Docker pipe server PID failed (Windows error 232)')
        return {**server,'checked_at':len(attests)}
    def boundary(endpoint,identities,**kwargs):
        selected=deployment.attest_docker_pipe_server(endpoint,identities,**kwargs)
        return {endpoint:[dict(slot=s,access_denied=True,denied_modes=['write','read','read_write'],server=selected) for s in P.SLOTS]}
    def bounded(argv,**kwargs):
        calls.append((argv,kwargs))
        if argv[5]=='info':raw=json.dumps(dict(OSType='linux',ID='private-id',MemoryLimit=True,SwapLimit=True,CpuCfsQuota=True,PidsLimit=True,SecurityOptions=['seccomp'])).encode()
        elif argv[5]=='image':raw=json.dumps([dict(Id=P.IMAGE,Os='linux',Config={'Env':['PATH=/usr/bin']})]).encode()
        else:raw=b''
        return SimpleNamespace(returncode=0,timed_out=False,cancelled=False,output_exceeded=False,stderr=b'',stdout=raw)
    monkeypatch.setattr(deployment,'attest_docker_pipe_server',attest)
    monkeypatch.setattr(deployment,'verify_docker_access_boundary',boundary)
    monkeypatch.setattr(containers,'_bounded_process',bounded)
    config=SimpleNamespace(operator_name='fixture-operator',docker=SimpleNamespace(executable=str(P.DOCKER),endpoint=P.ENDPOINT,
        image=P.IMAGE,pipe_server_executables=(str(P.BACKEND),),max_containers=4,warm_pool_size=2))
    return trace,calls,attests,config,attest,bounded


@pytest.mark.parametrize('stage,position',[(s,p) for s in D.COMMAND_STAGES for p in ('before_cli','after_cli')]+
                         [(s,'boundary_attestation_1') for s in D.BOUNDARY_STAGES])
def test_exact_collector_injected_attestation_position_is_preserved_no_retry(monkeypatch,stage,position):
    trace,calls,attests,config,original_attest,original_transport=transport_fixture(monkeypatch,(stage,position))
    with pytest.raises(win.WindowsIsolationError):
        with trace.instrument():P.docker_observation(None,{s:object() for s in P.SLOTS},config,trace.progress)
    event=trace.events[-1]
    assert (event['stage'],event['position'],event['completed'])==(stage,position,False)
    assert event['failure']['nodes'][0]['operation']=='pipe_server_pid'
    assert event['failure']['nodes'][0]['winerror']==232
    expected=(D.COMMAND_STAGES.index(stage)+(position=='after_cli')) if stage in D.COMMAND_STAGES else (0 if stage.endswith('before') else 4)
    assert len(calls)==expected and trace.result()['retry_attempts']==0
    assert deployment.attest_docker_pipe_server is original_attest and containers._bounded_process is original_transport


def test_exact_frozen_collector_single_four_command_success(monkeypatch):
    trace,calls,attests,config,original_attest,original_transport=transport_fixture(monkeypatch)
    with trace.instrument():result=P.docker_observation(None,{s:object() for s in P.SLOTS},config,trace.progress)
    assert result['commands_executed']==4 and result['ownership_census']['empty']
    assert trace.commands_started==trace.commands_completed==4 and len(attests)==10
    assert all(event['completed'] for event in trace.events)
    for argv,kwargs in calls:
        assert argv[:5]==[str(P.DOCKER),'--config',str(P.ROOT/'docker-client-config'),'--host',P.ENDPOINT]
        assert kwargs['timeout']==20 and kwargs['output_limit']==1048576
    assert deployment.attest_docker_pipe_server is original_attest and containers._bounded_process is original_transport


def test_actual_ordinary_private_pipe_busy_code_is_identified_without_docker():
    """Real own-token disposable pipe; zero bytes read/written, no Docker path."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateNamedPipeW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_uint32,
                                    ctypes.c_uint32,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateNamedPipeW.restype=ctypes.c_void_p
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle.restype=ctypes.c_int
    name=r'\\.\pipe\CoChemDisposableDiagnosticFixture-'+uuid.uuid4().hex
    server=kernel.CreateNamedPipeW(name,3|0x80000,0,1,512,512,0,None)
    invalid=ctypes.c_void_p(-1).value
    assert server not in (None,invalid)
    client=None;second=None
    try:
        client=kernel.CreateFileW(name,0,0,None,3,0x00110000,None)
        assert client not in (None,invalid),ctypes.get_last_error()
        second=kernel.CreateFileW(name,0,0,None,3,0x00110000,None);code=ctypes.get_last_error()
        assert second==invalid and code==231
        error=win.WindowsIsolationError(f'Cannot inspect Docker pipe server (Windows error {code})')
        assert P.safe_failure(error,'docker_owner_census')['winerror'] is None
        assert D.error_node(error)=={'error_type':'WindowsIsolationError','operation':'pipe_open','winerror':231,'winerror_source':'allowlisted_message'}
    finally:
        for handle in (second,client,server):
            if handle not in (None,invalid):assert kernel.CloseHandle(handle)


@pytest.mark.parametrize('fail_observation,fail_postcheck',[(False,False),(True,False),(False,True),(True,True)])
def test_full_run_real_support_actual_public_controls_and_safe_private_boundaries(monkeypatch,tmp_path,fail_observation,fail_postcheck):
    """Real support/config/public receipts; no private production reads or APIs."""
    from cochem_pipeline import config as config_module
    packet,prior,failed,old,old_preflight=public_inputs()
    # Derived Docker baseline is explicit fixture data; all real public receipt
    # schema/nonce/revision/worker commitments remain in use. The original exact
    # bytes are independently hash/semantics tested above.
    trace,cli_calls,attests,_,_,_=transport_fixture(monkeypatch,
        ('docker_owner_census','before_cli') if fail_observation else None)
    prior['docker']['server']={'pid':7,'process_created_filetime':42,'token_sid':'S-1-5-18','executable':str(P.BACKEND)}
    prior['docker']['engine']=P.daemon_info(dict(OSType='linux',ID='private-id',MemoryLimit=True,SwapLimit=True,CpuCfsQuota=True,PidsLimit=True,SecurityOptions=['seccomp']))
    prior['docker']['api_aliases']=[P.ENDPOINT]
    monkeypatch.setattr(D,'DiagnosticTrace',lambda:trace)
    actual_config=config_module.load_config(str(D.INSTALL/'pipeline.json'))
    config_bytes=(D.INSTALL/'pipeline.json').read_bytes();raw_config=json.loads(config_bytes)
    layout=json.loads((D.INSTALL/'windows-layout.json').read_bytes())
    helper=tmp_path/'diagnose-foundation-docker-r3-v1.py';helper.write_bytes((HERE/helper.name).read_bytes())
    for name in ('inspect-execution-prerequisites-r3.py','worker-denial-acceptance-r3.py'):(tmp_path/name).write_bytes((HERE/name).read_bytes())
    (tmp_path/'docker-client-config').mkdir()
    monkeypatch.setattr(D,'ROOT',tmp_path);monkeypatch.setattr(D,'__file__',str(helper));monkeypatch.setattr(sys,'executable',str(D.INSTALL/'.venv/Scripts/python.exe'))
    for name in ('require_system','validate_code_path','validate_private_directory'):monkeypatch.setattr(win,name,lambda *a,**k:None)
    sid_by_name={row['name']:layout['slots'][s]['sid'] for s,row in raw_config['workers'].items()}
    monkeypatch.setattr(win,'_account_sid',lambda name:name);monkeypatch.setattr(win,'_sid_text',lambda name:sid_by_name[name])
    monkeypatch.setattr(config_module,'load_config',lambda path:actual_config)
    synthetic_knowledge={'schema':'cochem-published-knowledge-verification/1','status':'PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED',
        'system_sid':'S-1-5-18','helper_sha256':P.KNOWLEDGE_HELPER_SHA,'runtime_root':str(P.PRIOR),'corpus_files':138,
        'corpus_bytes_preserved':True,'original_root_identity_and_security_preserved':True,'original_writer_lock_preserved':True,
        'index_size_sla_met':True,'knowledge_service_constructed':False,'index_refreshed_or_repaired':False,
        'existing_files_or_acls_modified':False,'activation_ready':False,'generation':prior['knowledge']['generation'],
        'index_sha256':prior['knowledge']['index_sha256'],'current_pointer_sha256':'b'*64,'source_pins_sha256':'c'*64}
    private_fixtures={P.KNOWLEDGE:json.dumps(synthetic_knowledge).encode(),P.STATE/'writer.lock':b'0',
        P.STATE/'current.json':json.dumps({'generation':synthetic_knowledge['generation']}).encode(),P.STATE/'sources.json':b'{}',
        P.STATE/synthetic_knowledge['generation']/'knowledge_index.db':b'synthetic-private-index'}
    reads=[];closes=[];stopped=[]
    @contextmanager
    def held_boundary(path,maximum,w,expected=None,private=False,retain=True):
        reads.append(path)
        if path==tmp_path/'inputs.json':raw=json.dumps(packet).encode()
        elif path==D.PREFLIGHT_ROOT/'execution-prerequisites.json':raw=json.dumps(prior).encode()
        elif path in private_fixtures:raw=private_fixtures[path]
        elif not retain:raw=None
        elif path.name=='python.exe':raw=b'synthetic-base-executable'
        else:
            raw=path.read_bytes()
            if expected:assert hashlib.sha256(raw).hexdigest()==expected
        try:yield raw
        finally:closes.append(path)
    ram_calls=[]
    def ram_boundary(*args):
        ram_calls.append(True)
        if fail_postcheck and len(ram_calls)==2:raise ValueError('private postcheck details not published')
        return prior['ram_after']
    def support_namespace(**namespace):
        if 'SUPPORT_SHA' in namespace:
            # Keep real strict parsing, receipt checks and captured constants.
            namespace['held_read']=held_boundary;namespace['docker_import_custody']=lambda *a:{'fixture':True}
            namespace['fresh_state']=lambda *a:prior['fresh_state_after'];namespace['ram_observation']=ram_boundary
        else:
            assert namespace['CONFIG_SHA256']==hashlib.sha256(config_bytes).hexdigest()
            namespace['verify_r3_runtime']=lambda *a:prior['revision']
            namespace['protected_json']=lambda w,path:(layout if path.name=='windows-layout.json' else raw_config,namespace['CONFIG_SHA256'])
            namespace['require_stopped']=lambda *a:stopped.append(True)
        return SimpleNamespace(**namespace)
    monkeypatch.setattr(D,'SimpleNamespace',support_namespace)
    @contextmanager
    def lock_boundary(*a):yield
    monkeypatch.setattr(D,'production_ram_lock',lock_boundary)
    code=D.run('a'*32,'d'*64)
    result=json.loads((tmp_path/'foundation-diagnostic.json').read_bytes())
    assert code==(2 if fail_observation or fail_postcheck else 0)
    assert len(ram_calls)==2 and len(reads)==len(closes)
    if fail_observation:
        assert result['failure']['phase']=='docker_owner_census'
        assert result['failure']['chain']['nodes'][0]['operation']=='pipe_server_pid'
        assert result['failure']['chain']['nodes'][0]['winerror']==232
        assert result['docker_trace']['events'][-1]['position']=='before_cli' and len(cli_calls)==2
    else:assert result['docker_trace']['commands_started']==4 and len(cli_calls)==4
    if fail_postcheck:assert result['preservation_after_observation_verified'] is False and 'preservation_failure' in result
    else:assert result['preservation_after_observation_verified'] is True and len(stopped)==2
    assert all(result[k] is False for k in ('ram_provision_started','registry_provision_started','existing_files_or_acls_modified','existing_databases_modified','activation_ready'))
    assert result['own_fresh_docker_config_directory_used'] and result['worker_handle_probe_phase_entered']
    assert 'private postcheck details' not in json.dumps(result)


def test_no_provisioning_or_docker_constructor_calls():
    source=(HERE/'diagnose-foundation-docker-r3-v1.py').read_text();tree=ast.parse(source)
    calls={node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else '' for node in ast.walk(tree) if isinstance(node,ast.Call)}
    assert not calls&{'DockerRunner','KnowledgeService','execution_readiness','ensure','refresh','mkdir','unlink','rmtree','chmod','connect'}
    assert source.count(".open('x',")==1
