"""SYSTEM deployment checks for the actual execution host, without model calls."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import re
import time
import uuid


def _terminate_unknown_identity():
    # A ThreadPool worker raising SystemExit would only abandon that future;
    # the process could keep running with an impersonated thread in its pool.
    # No exception handler or cleanup callback may resume privileged work.
    os._exit(70)


def attest_docker_pipe_server(endpoint, identities, *, trusted_operator=None,
                              trusted_server_executables=()):
    """Inspect the actual local server token/PID/image before trusting its API.

    The identification-only client handle cannot give an untrusted pipe server
    a SYSTEM impersonation token. No Docker command or request is transmitted.
    """
    from . import windows as win
    win.require_system()
    match = re.fullmatch(r'npipe:////\./pipe/([A-Za-z0-9_.-]+)',endpoint) if isinstance(endpoint,str) else None
    if match is None:
        raise ValueError('Docker server attestation requires a local named pipe')
    if not isinstance(trusted_server_executables,(list,tuple)) or not trusted_server_executables:
        raise win.WindowsIsolationError('Docker pipe server executable paths require an explicit reviewed allowlist')
    allowed_names = {'com.docker.backend.exe','com.docker.service.exe','dockerd.exe'}
    allowed = set()
    for filename in trusted_server_executables:
        if not isinstance(filename,str) or not Path(filename).is_absolute() or Path(filename).name.casefold() not in allowed_names:
            raise ValueError('Docker pipe server executable must be an exact reviewed native Docker backend path')
        allowed.add(os.path.normcase(os.path.abspath(filename)))
    trusted_sids = {win.SYSTEM_SID}
    if trusted_operator is not None:
        trusted_sids.add(win._sid_text(win._account_sid(trusted_operator)))
    worker_sids = {win._sid_text(win._account_sid(identity.name)) for identity in identities.values()}
    api = win._api()
    kernel = api['kernel32']
    server_pid = kernel.GetNamedPipeServerProcessId
    server_pid.restype, server_pid.argtypes = win.BOOL, [win.HANDLE,C.POINTER(win.DWORD)]
    open_process = kernel.OpenProcess
    open_process.restype, open_process.argtypes = win.HANDLE,[win.DWORD,win.BOOL,win.DWORD]
    query_image = kernel.QueryFullProcessImageNameW
    query_image.restype, query_image.argtypes = win.BOOL,[win.HANDLE,win.DWORD,win.LPWSTR,C.POINTER(win.DWORD)]
    process_times = kernel.GetProcessTimes
    process_times.restype, process_times.argtypes = win.BOOL,[win.HANDLE]+[C.POINTER(win._FILETIME)]*4
    pipe = process = token = None
    try:
        pipe = kernel.CreateFileW('\\\\.\\pipe\\'+match.group(1),0,0,None,3,0x00110000,None)
        if pipe in (None,C.c_void_p(-1).value):
            pipe = None
            raise win.WindowsIsolationError(f'Cannot inspect Docker pipe server (Windows error {C.get_last_error()})')
        pid = win.DWORD()
        win._check(server_pid(pipe,C.byref(pid)),'Inspect actual Docker pipe server PID')
        if not pid.value:
            raise win.WindowsIsolationError('Docker pipe server returned an invalid process ID')
        process = win._check(open_process(0x00101000,False,pid.value),'Open actual Docker pipe server process')
        token = win.HANDLE()
        win._check(api['advapi32'].OpenProcessToken(process,0x8,C.byref(token)),'Open Docker pipe server token')
        user = win._SID_AND_ATTRIBUTES.from_buffer(win._token_info(token,1))
        sid = win._sid_text(user.Sid)
        if sid in worker_sids or sid not in trusted_sids:
            raise win.WindowsIsolationError('Docker pipe server token is not SYSTEM or the reviewed operator')
        size = win.DWORD(32768)
        buffer = C.create_unicode_buffer(size.value)
        win._check(query_image(process,0,buffer,C.byref(size)),'Read actual Docker pipe server executable')
        executable = Path(buffer.value)
        if (executable.name.casefold() not in allowed_names or
                os.path.normcase(os.path.abspath(executable)) not in allowed):
            raise win.WindowsIsolationError('Docker pipe server executable is outside the reviewed Docker backend allowlist')
        win.validate_code_path(executable)
        created,exited,kernel_time,user_time = (win._FILETIME() for _ in range(4))
        win._check(process_times(process,C.byref(created),C.byref(exited),C.byref(kernel_time),C.byref(user_time)),
                   'Read Docker server process creation time')
        current = win.DWORD()
        win._check(server_pid(pipe,C.byref(current)),'Recheck Docker pipe server PID')
        if current.value!=pid.value or kernel.WaitForSingleObject(process,0)!=0x102:
            raise win.WindowsIsolationError('Docker pipe server exited or changed during attestation')
        return {'pid':pid.value,'process_created_filetime':(created.dwHighDateTime<<32)|created.dwLowDateTime,
                'token_sid':sid,'executable':str(executable),'checked_at':time.time()}
    finally:
        win._close(token)
        win._close(process)
        win._close(pipe)


def verify_worker_docker_denial(endpoint, identities):
    """Physically try the configured daemon pipe using each native worker token.

    The Docker controller can create containers; generated code must not obtain
    that authority. Group membership or a nominal ACL is not an access test.
    Only actual ACCESS_DENIED proves this prerequisite; missing/busy pipes do not.
    """
    from . import windows as win
    win.require_system()
    if (not identities or len({identity.name.casefold() for identity in identities.values()}) != len(identities)):
        raise ValueError('Docker access verification requires distinct, nonempty worker identities')
    match = re.fullmatch(r'npipe:////\./pipe/([A-Za-z0-9_.-]+)', endpoint)
    if match is None:
        raise ValueError('Windows execution requires a local Docker named-pipe endpoint')
    path = '\\\\.\\pipe\\' + match.group(1)
    api = win._api()
    impersonate = api['advapi32'].ImpersonateLoggedOnUser
    impersonate.restype, impersonate.argtypes = win.BOOL, [win.HANDLE]
    revert = api['advapi32'].RevertToSelf
    revert.restype, revert.argtypes = win.BOOL, []
    results = []
    for slot, identity in identities.items():
        token = win._worker_token(identity)
        try:
            win._check(impersonate(token), 'Impersonate isolated worker for Docker access test')
            try:
                denied = []
                # A write-only connection can submit mutating HTTP requests
                # without reading the reply. Duplex denial alone is no proof.
                for access, mode in ((0x40000000,'write'),(0x80000000,'read'),
                                     (0xC0000000,'read_write')):
                    # Probing an accidentally accessible pipe must not let its
                    # server impersonate this isolated worker either.
                    handle = api['kernel32'].CreateFileW(path, access, 0, None, 3, 0x00110000, None)
                    error = C.get_last_error()
                    if handle not in (None, C.c_void_p(-1).value):
                        win._close(handle)
                        raise win.WindowsIsolationError(f'Worker {slot} can access the Docker daemon ({mode})')
                    if error != 5:
                        raise win.WindowsIsolationError(f'Docker {mode} access denial for {slot} was not established (Win32 {error})')
                    denied.append(mode)
                results.append({'slot':slot,'access_denied':True,'denied_modes':denied})
            finally:
                if not revert():
                    # Continuing privileged host setup with an unknown thread
                    # identity is unsafe. This must bypass readiness error
                    # aggregation and terminate the calling controller.
                    _terminate_unknown_identity()
        finally:
            win._close(token)
    return results


def execution_readiness(config, *, provision=False):
    """Check real prerequisites; provision only the reviewed RAM workspace.

    No credential is read into the report, no model is invoked, and a missing
    sensor/driver/image never silently enables ordinary-disk or host execution.
    """
    from . import windows as win
    from .hardware_guard import HardwareGuard, effective_execution_policy
    from .ramdisk import RamdiskManager
    from .containers import DockerRunner
    win.require_system()
    win.validate_code_path(Path(__file__))
    win.validate_private_directory(config.private_root)
    identities = {slot:win.WorkerIdentity(**value) for slot,value in config.workers.items()}
    win.validate_layout(config.private_root,config.slot_roots,identities,require_defender=True)
    win.validate_controller_token(config.token_file,config.operator_name,identities)
    from .planning_governance import planning_readiness
    checks = {'planning':planning_readiness(config.coding_projects)}
    try:
        from .knowledge import KnowledgeService,provision_knowledge
        if provision:
            provision_knowledge(config.knowledge)
        knowledge=KnowledgeService(config.knowledge)
        try:
            evidence=knowledge.refresh()
            checks['knowledge']={'ready':evidence['index_size_sla_met'],'evidence':evidence}
        finally:
            knowledge.close()
    except Exception as exc:
        checks['knowledge']={'ready':False,'error':str(exc)[:2048]}
    try:
        from .resource_limits import controller_limits
        from .controller_guard import ControllerMonitor
        scheduling = controller_limits(config.execution_limits,apply=True)
        controller = ControllerMonitor().sample(force=True)
        checks['warden_controller'] = {'ready':controller['ready'],
                                       'evidence':{**controller,'scheduling':scheduling}}
    except Exception as exc:
        checks['warden_controller'] = {'ready':False,'error':str(exc)[:2048]}
    try:
        from .resource_limits import validate_host_limits
        checks['execution_limits'] = {'ready':True,'evidence':validate_host_limits(config.execution_limits)}
    except Exception as exc:
        checks['execution_limits'] = {'ready':False,'error':str(exc)[:2048]}
    if config.coding_projects:
        try:
            import subprocess
            win.validate_code_path(config.git_executable)
            result = subprocess.run([config.git_executable,'--version'],capture_output=True,text=True,
                                    timeout=10,creationflags=0x08000000,check=True)
            if not result.stdout.startswith('git version '):
                raise ValueError('The configured executable did not report a Git version')
            checks['git'] = {'ready':True,'version':result.stdout.strip()[:256]}
        except Exception as exc:
            checks['git'] = {'ready':False,'error':str(exc)[:2048]}
    workspaces = list(config.slot_roots.values())
    if config.ramdisk.enabled:
        try:
            manager = RamdiskManager(config.ramdisk,config.private_root,identities)
            evidence = manager.ensure() if provision else manager.inspect()
            workspaces.extend(manager.workspace(slot).root for slot in identities)
            checks['ramdisk'] = {'ready':True,'evidence':evidence}
        except Exception as exc:
            checks['ramdisk'] = {'ready':False,'error':str(exc)[:2048]}
    else:
        checks['ramdisk'] = {'ready':not bool(config.coding_projects),'enabled':False}
    if config.docker.enabled:
        try:
            if config.coding_projects and config.docker.warm_pool_size<=0:
                raise ValueError('Production coding requires preallocated warm Docker capacity')
            win.validate_code_path(config.docker.executable)
            runner = DockerRunner(config.docker,config.private_root/'containers',trusted_operator=config.operator_name,
                                  host_boot_id=win.current_boot_identity())
            boundary = verify_docker_access_boundary(config.docker.endpoint,identities,
                trusted_operator=config.operator_name,trusted_server_executables=config.docker.pipe_server_executables)
            evidence = runner.preflight()
            evidence['worker_access'] = boundary
            checks['docker'] = {'ready':True,'evidence':evidence}
        except Exception as exc:
            checks['docker'] = {'ready':False,'error':str(exc)[:2048]}
    else:
        checks['docker'] = {'ready':not bool(config.coding_projects),'enabled':False}
    try:
        hardware = HardwareGuard(max_agents=min(getattr(config,'max_execution_slots',4),len(identities)),workspaces=(config.private_root,*workspaces),
            policy=effective_execution_policy(config.hardware,config.execution_limits.memory_limit_mb,
                config.docker.memory_mb if config.docker.enabled else 0)).evaluate(force=True)
        # Startup ramp-up is expected. Readiness requires valid measurements and
        # positive raw capacity, not bypassing the runtime's hysteresis schedule.
        checks['hardware'] = {'ready':hardware['raw_capacity']>0 and hardware['state']!='critical',
                              'evidence':hardware}
    except Exception as exc:
        checks['hardware'] = {'ready':False,'error':str(exc)[:2048]}
    report = {'schema':1,'checked_at':time.time(),'provisioned':bool(provision),
              'ready':all(value['ready'] for value in checks.values()),'checks':checks,
              'native_models_executed':False,'projects':sorted(config.coding_projects)}
    target = config.private_root/'execution-readiness.json'
    if target.exists():
        win.validate_private_path(target)
    temporary = target.with_name('.execution-readiness-'+uuid.uuid4().hex)
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def verify_docker_access_boundary(endpoint, identities, *, required=True,
                                 trusted_operator=None, trusted_server_executables=()):
    """Check the selected API and Docker Desktop's existing local API aliases.

    An alternate Desktop API pipe must not bypass the selected pipe's ACL.
    When required is false, an absent configured pipe is not a prerequisite;
    existing configured/known local API pipes must still deny every access mode.
    This does not purport to audit unrelated remote Docker installations.
    """
    from . import windows as win
    win.require_system()
    if type(required) is not bool:
        raise ValueError('Docker boundary required flag must be boolean')
    if (not identities or len({identity.name.casefold() for identity in identities.values()}) != len(identities)):
        raise ValueError('Docker access verification requires distinct, nonempty worker identities')
    match = re.fullmatch(r'npipe:////\./pipe/([A-Za-z0-9_.-]+)',endpoint) if isinstance(endpoint,str) else None
    if endpoint is not None and match is None:
        raise ValueError('Windows execution requires a local Docker named-pipe endpoint')
    if required and match is None:
        raise ValueError('Required Docker isolation needs a configured local pipe')
    results = {}
    def inspect(selected):
        server = attest_docker_pipe_server(selected,identities,trusted_operator=trusted_operator,
                                           trusted_server_executables=trusted_server_executables)
        return [{**record,'server':server} for record in verify_worker_docker_denial(selected,identities)]
    if required:
        results[endpoint] = inspect(endpoint)
    names = {name.casefold():name for name in os.listdir('\\\\.\\pipe\\')}
    aliases = {'docker_engine','dockerdesktoplinuxengine','dockerdesktopwindowsengine'}
    if match is not None:
        aliases.add(match.group(1).casefold())
    for alias in sorted(aliases):
        if alias in names:
            alternate = 'npipe:////./pipe/' + names[alias]
            if not any(alternate.casefold() == checked.casefold() for checked in results):
                results[alternate] = inspect(alternate)
    return results
