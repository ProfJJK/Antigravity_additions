"""Fixed read-only MCP bridge check; preview never launches a child or reads a token.

Run with the installed r3 Python -I -B. --check-live is an explicit operational
choice after controller commissioning. Only the unprivileged stdio child reads
the existing token. Controller GET telemetry/cache updates remain possible.
This does not reload the current chat's tools or certify native performance.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import tomllib
import subprocess

WORK = Path(r'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next')
SUPPORT = WORK / 'connect-codex-pipeline-r3.py'
SUPPORT_SHA = 'de8741c843df2cc12ce81fbdf3ee78f0c7c6bc8449b3efd1aff3c6c768a4d1d4'
GENERATION = 'g-abed1cd030c746559aed6bb30a47093a'
INDEX_SHA = 'af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d'
CALLS = ('pipeline_health', 'pipeline_projects', 'knowledge_status', 'pipeline_health')
MAX_FRAME = 1048576
MAX_STDERR = 65536
TIMEOUT = 60.0


class OwnedStdio:
    """Suspended ordinary child, restricted pipe inheritance, owned kill-on-close Job.

    This covers the Windows venv launcher and its interpreter. Never opens an
    existing PID. Installed ctypes structure definitions are reused, not worker
    logon/profile/privilege APIs. Polling only reads bytes reported available.
    """
    def __init__(self, command, arguments):
        from cochem_pipeline import windows as w
        C = ctypes
        self.w = w
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.handles = []
        self.job = self.process = self.thread = None
        self.returncode = None
        self.sent = 0
        self.attributes = None
        declarations = {
            'CreatePipe':(w.BOOL,[C.POINTER(w.HANDLE),C.POINTER(w.HANDLE),w.HANDLE,w.DWORD]),
            'SetHandleInformation':(w.BOOL,[w.HANDLE,w.DWORD,w.DWORD]),
            'PeekNamedPipe':(w.BOOL,[w.HANDLE,w.HANDLE,w.DWORD,w.HANDLE,C.POINTER(w.DWORD),w.HANDLE]),
            'ReadFile':(w.BOOL,[w.HANDLE,w.HANDLE,w.DWORD,C.POINTER(w.DWORD),w.HANDLE]),
            'WriteFile':(w.BOOL,[w.HANDLE,w.HANDLE,w.DWORD,C.POINTER(w.DWORD),w.HANDLE]),
            'CreateProcessW':(w.BOOL,[w.LPWSTR,w.LPWSTR,w.HANDLE,w.HANDLE,w.BOOL,w.DWORD,w.HANDLE,w.LPWSTR,w.HANDLE,C.POINTER(w._PROCESS_INFORMATION)]),
        }
        # Existing kernel declarations include pointer-width Job/handle APIs.
        self.api = w._api()['kernel32']
        for name,(restype,argtypes) in declarations.items():
            function = getattr(self.k,name); function.restype=restype; function.argtypes=argtypes
        attributes_initialized = False
        try:
            security = w._SECURITY_ATTRIBUTES(C.sizeof(w._SECURITY_ATTRIBUTES),None,True)
            pairs = []
            for _ in range(3):
                read,write = w.HANDLE(),w.HANDLE()
                self.check(self.k.CreatePipe(C.byref(read),C.byref(write),C.byref(security),16384))
                self.handles.extend([read.value,write.value]); pairs.append((read.value,write.value))
            child_in,self.input_handle = pairs[0]
            self.output_handle,child_out = pairs[1]
            self.error_handle,child_err = pairs[2]
            for parent in (self.input_handle,self.output_handle,self.error_handle):
                self.check(self.k.SetHandleInformation(parent,1,0))
            size=w.SIZE_T()
            self.api.InitializeProcThreadAttributeList(None,1,0,C.byref(size))
            self.attributes=C.create_string_buffer(size.value)
            self.check(self.api.InitializeProcThreadAttributeList(self.attributes,1,0,C.byref(size)))
            attributes_initialized=True
            inherited=(w.HANDLE*3)(child_in,child_out,child_err)
            self.check(self.api.UpdateProcThreadAttribute(self.attributes,0,0x20002,inherited,C.sizeof(inherited),None,None))
            startup=w._STARTUPINFOEXW(); startup.StartupInfo.cb=C.sizeof(startup)
            startup.StartupInfo.dwFlags=0x100
            startup.StartupInfo.hStdInput=child_in; startup.StartupInfo.hStdOutput=child_out; startup.StartupInfo.hStdError=child_err
            startup.lpAttributeList=C.cast(self.attributes,w.HANDLE)
            self.job=self.api.CreateJobObjectW(None,None); self.check(self.job)
            limits=w._EXTENDED_LIMIT(); limits.BasicLimitInformation.LimitFlags=0x2000
            self.check(self.api.SetInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits)))
            from mcp.client.stdio import get_default_environment
            env=C.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(get_default_environment().items()))+'\0\0')
            info=w._PROCESS_INFORMATION()
            # CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
            self.check(self.k.CreateProcessW(command,C.create_unicode_buffer(subprocess.list2cmdline([command,*arguments])),
                None,None,True,0x08080404,env,str(Path(command).parent),C.byref(startup),C.byref(info)))
            self.process,self.thread=info.hProcess,info.hThread
            self.check(self.api.AssignProcessToJobObject(self.job,self.process))
            if self.api.ResumeThread(self.thread)==0xFFFFFFFF:
                raise BridgeHeld('STDIO_RESUME_FAILED')
            self.api.CloseHandle(self.thread); self.thread=None
            for child in (child_in,child_out,child_err): self.close_handle(child)
            self.stdin=self
            self.stdout=_PipeReader(self,self.output_handle)
            self.stderr=_PipeReader(self,self.error_handle)
        except BaseException:
            if self.process:
                self.api.TerminateProcess(self.process,2)
                self.api.WaitForSingleObject(self.process,1000)
            self.dispose()
            raise
        finally:
            if attributes_initialized: self.api.DeleteProcThreadAttributeList(self.attributes)

    @staticmethod
    def check(ok):
        if not ok: raise BridgeHeld('OWNED_STDIO_WIN32_OPERATION_FAILED')

    def close_handle(self,handle):
        if handle in self.handles:
            self.api.CloseHandle(handle); self.handles.remove(handle)

    def write(self,raw):
        # All fixed requests together stay well below the 16 KiB pipe buffer;
        # no synchronous write can wait for a non-reading server to drain it.
        self.sent+=len(raw)
        if self.sent>4096: raise BridgeHeld('REQUEST_BYTES_EXCEEDED')
        written=self.w.DWORD()
        self.check(self.k.WriteFile(self.input_handle,raw,len(raw),ctypes.byref(written),None))
        if written.value!=len(raw): raise BridgeHeld('STDIO_SHORT_WRITE')

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.close_handle(self.input_handle)

    async def wait(self):
        while self.api.WaitForSingleObject(self.process,0)==258:
            await asyncio.sleep(.01)
        code=self.w.DWORD(); self.check(self.api.GetExitCodeProcess(self.process,ctypes.byref(code)))
        self.returncode=code.value
        return self.returncode

    def active(self):
        accounting=self.w._BASIC_ACCOUNTING()
        self.check(self.api.QueryInformationJobObject(self.job,1,ctypes.byref(accounting),ctypes.sizeof(accounting),None))
        return accounting.ActiveProcesses

    async def cleanup(self,seconds):
        self.close()
        deadline=time.monotonic()+seconds
        graceful=min(deadline,time.monotonic()+seconds/3)
        try:
            while self.active() and time.monotonic()<graceful: await asyncio.sleep(.01)
            if self.active(): self.check(self.api.TerminateJobObject(self.job,2))
            while self.active() and time.monotonic()<deadline: await asyncio.sleep(.01)
            if self.active(): raise BridgeHeld('OWNED_STDIO_CLEANUP_UNVERIFIED')
            await asyncio.wait_for(self.wait(),max(.01,deadline-time.monotonic()))
        finally:
            self.dispose()

    def dispose(self):
        for handle in list(self.handles): self.close_handle(handle)
        for name in ('job','thread','process'):
            if getattr(self,name,None): self.api.CloseHandle(getattr(self,name)); setattr(self,name,None)


class _PipeReader:
    def __init__(self,owner,handle):
        self.owner=owner; self.handle=handle; self.buffer=b''

    async def read(self,count):
        while True:
            available=self.owner.w.DWORD()
            if not self.owner.k.PeekNamedPipe(self.handle,None,0,None,ctypes.byref(available),None):
                if ctypes.get_last_error() in (109,232): return b''
                raise BridgeHeld('STDIO_PIPE_PEEK_FAILED')
            if available.value:
                size=min(count,available.value); raw=ctypes.create_string_buffer(size); got=self.owner.w.DWORD()
                self.owner.check(self.owner.k.ReadFile(self.handle,raw,size,ctypes.byref(got),None))
                return raw.raw[:got.value]
            await asyncio.sleep(.01)

    async def readline(self):
        while b'\n' not in self.buffer:
            raw=await self.read(4096)
            if not raw: result,self.buffer=self.buffer,b''; return result
            self.buffer+=raw
            if len(self.buffer)>MAX_FRAME: raise BridgeHeld('MCP_FRAME_TOO_LARGE')
        result,self.buffer=self.buffer.split(b'\n',1)
        return result+b'\n'


class BridgeHeld(ValueError):
    """Fixed public code only; no native/controller exception text."""


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BridgeHeld('DUPLICATE_JSON_FIELD')
            result[key] = value
        return result
    def constant(_):
        raise BridgeHeld('NONFINITE_JSON')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except BridgeHeld:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise BridgeHeld('INVALID_JSON') from None


def bounded_read(path, maximum):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise BridgeHeld('INPUT_TOO_LARGE')
    return raw


def load_support():
    raw = bounded_read(SUPPORT, 65536)
    if hashlib.sha256(raw).hexdigest() != SUPPORT_SHA:
        raise BridgeHeld('CONNECTOR_SOURCE_DRIFT')
    spec = importlib.util.spec_from_file_location('cochem_frozen_connector_r3', SUPPORT)
    module = importlib.util.module_from_spec(spec)
    # Compile the exact verified bytes; do not reopen mutable source via loader.
    exec(compile(raw, str(SUPPORT), 'exec'), module.__dict__)
    module.ordinary_path(SUPPORT)
    return module


def inspect_configuration(support):
    revision = support.pin_runtime()
    support.ordinary_path(support.CODEX_CONFIG)
    support.ordinary_path(support.CLIENT_CONFIG)
    codex = bounded_read(support.CODEX_CONFIG, MAX_FRAME)
    client = bounded_read(support.CLIENT_CONFIG, 16384)
    try:
        registered = tomllib.loads(codex.decode('utf-8-sig')).get('mcp_servers', {}).get(support.SERVER)
    except (ValueError, UnicodeError):
        raise BridgeHeld('CODEX_CONFIG_INVALID') from None
    if registered != support.entry():
        raise BridgeHeld('CODEX_ENTRY_DIFFERS')
    expected = {'port':47824, 'token_file':str(support.OPERATOR/'CoChem427/controller.token')}
    if strict_json(client) != expected:
        raise BridgeHeld('CLIENT_CONFIG_DIFFERS')
    return {'codex_config_sha256':hashlib.sha256(codex).hexdigest(),
            'client_config_sha256':hashlib.sha256(client).hexdigest(),
            'connector_sha256':SUPPORT_SHA, 'installed_revision_sha256':revision['source_sha256'],
            'server_entry_matches':True, 'loopback_port':47824,
            'token_path_matches':True, 'token_opened_by_verifier':False}


def structured_result(result):
    if (not isinstance(result, dict) or type(result.get('isError')) is not bool
            or result['isError'] or not isinstance(result.get('structuredContent'), dict)
            or not isinstance(result.get('content'), list)):
        raise BridgeHeld('TOOL_RESULT_NOT_SUCCESSFUL_STRUCTURED_OBJECT')
    return result['structuredContent']


def health_projection(value):
    hardware = value.get('hardware')
    started = value.get('process_started_at')
    if (value.get('version') != '4.2.7' or value.get('service_identity') != 'SYSTEM'
            or type(value.get('pid')) is not int or not 1 <= value['pid'] <= 0xFFFFFFFF
            or not isinstance(value.get('instance_id'), str)
            or not re.fullmatch('[a-f0-9]{32}', value['instance_id'])
            or type(started) not in (int, float) or not math.isfinite(started) or not 0 < started < 1e12
            or not isinstance(hardware, dict)
            or type(hardware.get('max_agents')) is not int or hardware['max_agents'] != 4
            or type(hardware.get('hard_max_agents')) is not int or hardware['hard_max_agents'] != 4):
        raise BridgeHeld('HEALTH_IDENTITY_OR_CAPACITY_DIFFERS')
    return {'reported_version':'4.2.7', 'reported_service_identity':'SYSTEM',
            'pid':value['pid'], 'instance_id':value['instance_id'],
            'service_reported_process_started_at':started,
            'configured_max_agents':4, 'hard_max_agents':4,
            'independent_os_token_attestation':False, 'performance_acceptance':False}


def knowledge_projection(value):
    if (value.get('ready') is not True or value.get('index_ready') is not True
            or value.get('authority_matches_capture') is not True
            or value.get('generation') != GENERATION
            or type(value.get('document_count')) is not int or value['document_count'] != 137
            or type(value.get('section_count')) is not int or value['section_count'] != 1708
            or value.get('index_size_sla_met') is not True
            or not isinstance(value.get('manifest_sha256'), str)
            or not re.fullmatch('[a-f0-9]{64}', value['manifest_sha256'])):
        raise BridgeHeld('KNOWLEDGE_READINESS_OR_GENERATION_DIFFERS')
    exposed = 'index_sha256' in value
    if exposed and value['index_sha256'] != INDEX_SHA:
        raise BridgeHeld('EXPOSED_KNOWLEDGE_INDEX_DIGEST_DIFFERS')
    return {'ready':True, 'authority_matches_capture':True, 'generation':GENERATION,
            'manifest_sha256':value['manifest_sha256'], 'document_count':137, 'section_count':1708,
            'index_sha256_exposed_by_mcp':exposed, 'index_sha256_verified_via_mcp':exposed,
            'index_sha256':INDEX_SHA if exposed else None,
            'index_digest_scope':'verified only if reported; installed r3 does not expose this field'}


def projects_projection(value):
    projects = value.get('projects')
    if (not isinstance(projects, list) or not 1 <= len(projects) <= 256
            or any(not isinstance(p, str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', p) for p in projects)
            or len(set(projects)) != len(projects) or 'windows-acceptance' not in projects):
        raise BridgeHeld('EXPECTED_PROJECT_UNAVAILABLE')
    return {'windows_acceptance_registered':True, 'registered_project_count':len(projects)}


async def exchange(process, expected_tools, progress):
    async def send(value):
        process.stdin.write(json.dumps(value, separators=(',', ':'), allow_nan=False).encode()+b'\n')
        await process.stdin.drain()

    async def request(identifier, method, params):
        await send({'jsonrpc':'2.0', 'id':identifier, 'method':method, 'params':params})
        try:
            raw = await process.stdout.readline()
        except ValueError:
            raise BridgeHeld('MCP_FRAME_TOO_LARGE') from None
        if not raw or len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
            raise BridgeHeld('MCP_FRAME_INVALID_OR_CLOSED')
        response = strict_json(raw)
        if (not isinstance(response, dict) or response.get('jsonrpc') != '2.0'
                or type(response.get('id')) is not int or response['id'] != identifier
                or set(response) != {'jsonrpc','id','result'} or not isinstance(response['result'], dict)):
            raise BridgeHeld('MCP_RESPONSE_ID_OR_ENVELOPE_DIFFERS')
        return response['result']

    initialized = await request(1, 'initialize', {'protocolVersion':'2025-11-25', 'capabilities':{},
        'clientInfo':{'name':'cochem-read-only-bridge-verifier','version':'1'}})
    server = initialized.get('serverInfo', {})
    if (initialized.get('protocolVersion') != '2025-11-25'
            or server.get('name') != 'CoChem Pipeline 4.2.7' or server.get('version') != '1.30.0'):
        raise BridgeHeld('MCP_INITIALIZATION_DIFFERS')
    await send({'jsonrpc':'2.0','method':'notifications/initialized'})
    inventory = await request(2, 'tools/list', {})
    tools = inventory.get('tools')
    if (not isinstance(tools, list) or len(tools) != 15 or inventory.get('nextCursor') is not None
            or any(not isinstance(tool, dict) or not isinstance(tool.get('name'), str) for tool in tools)
            or {tool['name'] for tool in tools} != expected_tools):
        raise BridgeHeld('MCP_TOOL_INVENTORY_DIFFERS')
    values = []
    for number, name in enumerate(CALLS, 3):
        progress['mcp_tool_call_requests_attempted'] += 1
        value = structured_result(await request(number, 'tools/call', {'name':name,'arguments':{}}))
        values.append(health_projection(value) if name == 'pipeline_health' else
                      knowledge_projection(value) if name == 'knowledge_status' else projects_projection(value))
    if values[0] != values[3]:
        raise BridgeHeld('CONTROLLER_INSTANCE_CHANGED')
    return {'protocol_version':'2025-11-25','server_name':'CoChem Pipeline 4.2.7',
            'mcp_sdk_reported_version':'1.30.0','tool_count':15,
            'read_only_calls':list(CALLS), 'controller':values[0], 'projects':values[1],
            'knowledge':values[2], 'same_controller_before_and_after':True}


async def bounded_stdio(command, arguments, expected_tools, *, timeout_seconds=TIMEOUT, progress=None):
    """Fixed caller supplies the reviewed command; fixture tests supply an inert child."""
    if not .3 <= timeout_seconds <= TIMEOUT:
        raise BridgeHeld('INVALID_TIMEOUT')
    cleanup_budget = min(5., timeout_seconds/4)
    process = None
    stderr_bytes = 0
    if progress is None:
        progress = {'mcp_tool_call_requests_attempted':0}
    try:
        async with asyncio.timeout(timeout_seconds-cleanup_budget):
            progress['owned_stdio_launch_attempted'] = True
            progress['owned_stdio_child_started'] = None
            process = OwnedStdio(command, arguments)
            progress['owned_stdio_child_started'] = True

            async def discard_stderr():
                nonlocal stderr_bytes
                while chunk := await process.stderr.read(4096):
                    stderr_bytes += len(chunk)
                    if stderr_bytes > MAX_STDERR:
                        raise BridgeHeld('STDERR_LIMIT_EXCEEDED')
                    # Intentionally retain no bytes/hash/text from stderr.

            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(discard_stderr())
                result = await exchange(process, expected_tools, progress)
                process.stdin.close()
                await process.wait()
            if process.returncode != 0:
                raise BridgeHeld('STDIO_CHILD_EXIT_NONZERO')
            # The direct process handle may signal before Job accounting
            # publishes its exit; retain the same overall deadline until zero.
            while process.active():
                await asyncio.sleep(.01)
            return {**result, 'stdio_child_exit_code':0, 'stderr_bytes_discarded':stderr_bytes,
                    'stderr_content_retained':False, 'owned_stdio_child_exited':True}
    finally:
        progress['stderr_bytes_discarded'] = stderr_bytes
        if process is not None:
            await process.cleanup(cleanup_budget)
            progress['owned_stdio_tree_cleanup_verified'] = True


def safe_failure(error):
    pending = [error]
    for _ in range(16):
        if not pending:
            break
        item = pending.pop(0)
        if isinstance(item, BridgeHeld) and re.fullmatch('[A-Z_]{1,80}', str(item)):
            return {'code':str(item), 'error_type':'BridgeHeld'}
        if isinstance(item, TimeoutError):
            return {'code':'BRIDGE_DEADLINE_EXPIRED','error_type':'TimeoutError'}
        pending.extend(list(getattr(item, 'exceptions', ()))[:8])
    return {'code':'BRIDGE_CHECK_FAILED','error_type':'Exception'}


def validate_output(support, path):
    path = Path(path)
    if (not path.is_absolute() or path.parent != WORK
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.json', path.name)):
        raise BridgeHeld('OUTPUT_PATH_NOT_FIXED_WORKSPACE_CHILD')
    support.ordinary_path(path, may_be_absent=True)
    if path.exists():
        raise BridgeHeld('FRESH_WORKSPACE_OUTPUT_REQUIRED')
    return path


def save_report(support, path, report):
    path = validate_output(support,path)
    raw = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    if len(raw) > 65536:
        raise BridgeHeld('REPORT_TOO_LARGE')
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.check_live and args.output is None:
        parser.error('--check-live requires a fresh --output metadata file in the fixed workspace')
    report = {'schema':'cochem-codex-live-bridge/1','status':'PREVIEW_HELD',
              'check_live_requested':args.check_live, 'controller_tool_calls':0,
              'mcp_tool_call_requests_attempted':0,'owned_stdio_child_started':False,
              'owned_stdio_tree_cleanup_verified':False,
              'model_jobs_submitted':0, 'configuration_changed':False,
              'token_opened_by_verifier':False, 'raw_tool_responses_retained':False,
              'running_chat_tool_catalog_reloaded':False, 'native_performance_verified':False,
              'activation_ready':False}
    support = None
    try:
        support = load_support()
        if (os.name != 'nt' or Path(sys.executable) != support.PYTHON
                or not sys.flags.isolated or not sys.dont_write_bytecode
                or ctypes.windll.shell32.IsUserAnAdmin()):
            raise BridgeHeld('USE_INSTALLED_ISOLATED_PYTHON_AS_ORDINARY_OPERATOR')
        report['configuration'] = inspect_configuration(support)
        if args.output is not None:
            validate_output(support,args.output)
        if args.check_live:
            report['status'] = 'LIVE_CHECK_HELD'
            report.update(controller_tool_calls=None,controller_get_telemetry_writes_possible=True,
                          existing_token_read_by_stdio_bridge_possible=True)
            result = asyncio.run(bounded_stdio(str(support.PYTHON), support.entry()['args'], support.EXPECTED_TOOLS,progress=report))
            if inspect_configuration(support) != report['configuration']:
                raise BridgeHeld('CONFIGURATION_CHANGED_DURING_CHECK')
            report.update(status='LIVE_MCP_READ_ONLY_BRIDGE_VERIFIED', bridge=result,
                          controller_tool_calls=4, controller_get_telemetry_writes_possible=True,
                          existing_token_read_by_stdio_bridge=True)
        else:
            report.update(status='READ_ONLY_PREVIEW', child_started=False,
                          network_calls=0, existing_token_read_by_stdio_bridge=False)
    except Exception as error:
        report['failure'] = safe_failure(error)
    if args.output is not None and support is not None:
        save_report(support, args.output, report)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report['status'] in ('READ_ONLY_PREVIEW','LIVE_MCP_READ_ONLY_BRIDGE_VERIFIED') else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'schema':'cochem-codex-live-bridge/1','status':'OUTPUT_OR_ENTRY_HELD',
                          'failure':safe_failure(error),'activation_ready':False}), file=sys.stderr)
        raise SystemExit(2) from None
