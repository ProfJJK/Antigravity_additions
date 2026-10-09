"""Ordinary disposable protocol/owned-process tests; never the real controller."""
import asyncio
import copy
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

HERE=Path(__file__).parent
SOURCE=HERE/'verify-codex-live-bridge-r3.py'
PYTHON=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Scripts\python.exe')
spec=importlib.util.spec_from_file_location('bridge_check',SOURCE)
M=importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
S=M.load_support()


def health():
    return {'version':'4.2.7','service_identity':'SYSTEM','pid':1234,
            'instance_id':'1'*32,'process_started_at':1800000000.25,
            'hardware':{'max_agents':4,'hard_max_agents':4},'secret':'DO_NOT_PUBLISH'}


def knowledge():
    return {'ready':True,'index_ready':True,'authority_matches_capture':True,
            'generation':M.GENERATION,'document_count':137,'section_count':1708,
            'manifest_sha256':'a'*64,'index_size_sla_met':True,'secret':'DO_NOT_PUBLISH'}


def fixture_child(tmp_path, *, drift=False):
    script=tmp_path/'inert_mcp.py'; seen=tmp_path/'seen.json'
    script.write_text('''import json
from pathlib import Path
from cochem_pipeline.server import create_server
HEALTH='''+repr(health())+'''
KNOWLEDGE='''+repr(knowledge())+'''
class InertClient:
    def __init__(self): self.seen=[]
    def call(self,path,payload=None):
        assert payload is None
        self.seen.append(path)
        if path=='/health':
            result=dict(HEALTH)
            if '''+repr(drift)+''' and len(self.seen)>1: result['pid']+=1
            return result
        if path=='/coding/projects': return {'projects':['windows-acceptance'],'secret':'DO_NOT_PUBLISH'}
        if path=='/knowledge/status': return KNOWLEDGE
        raise RuntimeError('No fixture operation authorized')
    def close(self): Path('''+repr(str(seen))+''').write_text(json.dumps(self.seen))
create_server(InertClient()).run(transport='stdio')
''',encoding='utf-8')
    return script,seen


def run_child(script,timeout=8,progress=None):
    return asyncio.run(M.bounded_stdio(str(PYTHON),['-I','-B',str(script)],S.EXPECTED_TOOLS,
        timeout_seconds=timeout,progress=progress))


def test_actual_installed_sdk_transport_only_four_read_only_operations(tmp_path):
    script,seen=fixture_child(tmp_path)
    progress={'mcp_tool_call_requests_attempted':0}
    report=run_child(script,progress=progress)
    assert json.loads(seen.read_text())==['/health','/coding/projects','/knowledge/status','/health']
    assert report['tool_count']==15 and report['same_controller_before_and_after']
    assert report['mcp_sdk_reported_version']=='1.30.0'
    assert report['stderr_bytes_discarded']>0  # Installed SDK emits harmless INFO messages.
    assert progress['owned_stdio_tree_cleanup_verified'] is True
    assert progress['mcp_tool_call_requests_attempted']==4
    assert 'DO_NOT_PUBLISH' not in json.dumps(report)
    assert report['knowledge']['index_sha256_exposed_by_mcp'] is False
    assert report['controller']['independent_os_token_attestation'] is False


def test_actual_server_identity_drift_is_held_and_child_tree_closed(tmp_path):
    script,_=fixture_child(tmp_path,drift=True)
    progress={'mcp_tool_call_requests_attempted':0}
    with pytest.raises(Exception) as caught: run_child(script,progress=progress)
    assert M.safe_failure(caught.value)['code']=='CONTROLLER_INSTANCE_CHANGED'
    assert progress['owned_stdio_tree_cleanup_verified'] is True
    assert progress['mcp_tool_call_requests_attempted']==4


@pytest.mark.parametrize('field,value',[('pid',True),('pid',0),('instance_id','bad'),
    ('version','4.2.6'),('service_identity','ansac'),('process_started_at',float('inf')),
    ('process_started_at',-1),('hardware',{'max_agents':True,'hard_max_agents':4}),
    ('hardware',{'max_agents':6,'hard_max_agents':6})])
def test_health_rejects_unbound_or_invalid_metadata(field,value):
    data=health(); data[field]=value
    with pytest.raises(M.BridgeHeld): M.health_projection(data)


@pytest.mark.parametrize('change',[{'ready':False},{'authority_matches_capture':False},
    {'index_ready':False},{'generation':'another'},{'document_count':True},
    {'section_count':1707},{'index_size_sla_met':False},{'index_sha256':'b'*64}])
def test_knowledge_rejects_mismatch(change):
    data=knowledge(); data.update(change)
    with pytest.raises(M.BridgeHeld): M.knowledge_projection(data)


def test_knowledge_exposed_index_digest_only_claimed_when_actually_present():
    data=knowledge()
    assert M.knowledge_projection(data)['index_sha256'] is None
    data['index_sha256']=M.INDEX_SHA
    assert M.knowledge_projection(data)['index_sha256_verified_via_mcp'] is True


@pytest.mark.parametrize('result',[{}, {'isError':True,'content':[],'structuredContent':{}},
    {'isError':0,'content':[],'structuredContent':{}},
    {'isError':False,'content':[],'structuredContent':[]},
    {'isError':False,'structuredContent':{}}])
def test_tool_error_or_missing_structured_envelope_held(result):
    with pytest.raises(M.BridgeHeld): M.structured_result(result)


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','not-json'])
def test_invalid_json_rejected(raw):
    with pytest.raises(M.BridgeHeld): M.strict_json(raw)


@pytest.mark.parametrize('projects',[[],['windows-acceptance','windows-acceptance'],['elsewhere'],[42]])
def test_invalid_project_inventory_held(projects):
    with pytest.raises(M.BridgeHeld): M.projects_projection({'projects':projects})


@pytest.mark.parametrize('mode,expected',[
    ('stderr','STDERR_LIMIT_EXCEEDED'),('frame','MCP_FRAME_TOO_LARGE'),
    ('envelope','MCP_RESPONSE_ID_OR_ENVELOPE_DIFFERS'),('hang','BRIDGE_DEADLINE_EXPIRED')])
def test_real_disposable_bad_stdio_is_bounded_and_redacted(tmp_path,mode,expected):
    child=tmp_path/'bad_child.py'
    code={'stderr':"sys.stderr.write('DO_NOT_PUBLISH'*8000); sys.stderr.flush()",
          'frame':"sys.stdout.write('DO_NOT_PUBLISH'*90000); sys.stdout.flush()",
          'envelope':"print('{\"jsonrpc\":\"2.0\",\"id\":999,\"result\":{\"secret\":\"DO_NOT_PUBLISH\"}}',flush=True)",
          'hang':'pass'}[mode]
    child.write_text('import sys,time\n'+code+'\ntime.sleep(60)\n')
    progress={'mcp_tool_call_requests_attempted':0}
    started=time.monotonic()
    with pytest.raises(Exception) as caught: run_child(child,timeout=2,progress=progress)
    failure=M.safe_failure(caught.value)
    assert failure['code']==expected
    assert time.monotonic()-started<4
    assert 'DO_NOT_PUBLISH' not in json.dumps(failure)
    assert progress['owned_stdio_tree_cleanup_verified'] is True


def test_timeout_covers_venv_interpreter_and_descendant_using_owned_handle(tmp_path):
    pidfile=tmp_path/'pid.txt'; child=tmp_path/'descendant.py'
    child.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-I','-B','-c',"+
        repr("import os,time; from pathlib import Path; Path("+repr(str(pidfile))+").write_text(str(os.getpid())); time.sleep(60)")+"] )\ntime.sleep(60)\n")
    progress={'mcp_tool_call_requests_attempted':0}
    async def fixture():
        from ctypes import wintypes as W
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.restype=W.HANDLE; k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]
        k.WaitForSingleObject.restype=W.DWORD; k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
        k.CloseHandle.restype=W.BOOL; k.CloseHandle.argtypes=[W.HANDLE]
        task=asyncio.create_task(M.bounded_stdio(str(PYTHON),['-I','-B',str(child)],S.EXPECTED_TOOLS,timeout_seconds=3,progress=progress))
        deadline=time.monotonic()+1.8
        while not pidfile.exists() and time.monotonic()<deadline: await asyncio.sleep(.02)
        assert pidfile.exists()
        handle=k.OpenProcess(0x100000,False,int(pidfile.read_text()))
        assert handle
        try:
            assert k.WaitForSingleObject(handle,0)==258
            with pytest.raises(TimeoutError): await task
            assert k.WaitForSingleObject(handle,0)==0
        finally: k.CloseHandle(handle)
    asyncio.run(fixture())
    assert progress['owned_stdio_tree_cleanup_verified'] is True


def test_output_is_exclusive_and_bad_names_refused_before_use(tmp_path,monkeypatch):
    monkeypatch.setattr(M,'WORK',tmp_path)
    target=tmp_path/'metadata.json'
    M.save_report(S,target,{'safe':True})
    before=target.read_bytes()
    with pytest.raises(M.BridgeHeld): M.save_report(S,target,{'changed':True})
    assert target.read_bytes()==before
    for path in (tmp_path/'bad:name.json',tmp_path/'other.txt',tmp_path.parent/'out.json'):
        with pytest.raises(M.BridgeHeld): M.validate_output(S,path)


def test_configuration_verification_never_opens_token(tmp_path):
    config=tmp_path/'config.toml'; client=tmp_path/'client.json'; token=tmp_path/'CoChem427/controller.token'
    expected=S.entry()
    config.write_text('[mcp_servers."cochem-pipeline"]\n'+
        '\n'.join(k+' = '+json.dumps(v) for k,v in expected.items())+'\n')
    client.write_text(json.dumps({'port':47824,'token_file':str(token)}))
    fake=SimpleNamespace(pin_runtime=lambda:{'source_sha256':'a'*64},ordinary_path=S.ordinary_path,
        CODEX_CONFIG=config,CLIENT_CONFIG=client,SERVER=S.SERVER,entry=S.entry,OPERATOR=tmp_path)
    result=M.inspect_configuration(fake)
    assert result['token_opened_by_verifier'] is False and not token.exists()
    client.write_text(json.dumps({'port':47825,'token_file':str(token)}))
    with pytest.raises(M.BridgeHeld,match='CLIENT_CONFIG_DIFFERS'): M.inspect_configuration(fake)


def test_main_preview_does_not_launch_or_read_token(monkeypatch,capsys):
    monkeypatch.setattr(M,'load_support',lambda:SimpleNamespace(PYTHON=Path(sys.executable)))
    monkeypatch.setattr(M,'sys',SimpleNamespace(executable=sys.executable,flags=SimpleNamespace(isolated=True),dont_write_bytecode=True))
    monkeypatch.setattr(M,'inspect_configuration',lambda _:{'token_opened_by_verifier':False})
    monkeypatch.setattr(M,'OwnedStdio',lambda *_:pytest.fail('Preview launched a child'))
    assert M.main([])==0
    report=json.loads(capsys.readouterr().out)
    assert report['controller_tool_calls']==0 and report['network_calls']==0
    assert report['status']=='READ_ONLY_PREVIEW'


def test_safe_failure_discards_untrusted_exception_messages():
    failure=M.safe_failure(ExceptionGroup('SECRET',[ValueError('DO_NOT_PUBLISH'),M.BridgeHeld('FIXED_HOLD')]))
    assert failure=={'code':'FIXED_HOLD','error_type':'BridgeHeld'}
    assert 'SECRET' not in json.dumps(M.safe_failure(ValueError('SECRET')))
