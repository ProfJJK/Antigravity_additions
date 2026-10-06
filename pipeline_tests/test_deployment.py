"""Deployment boundaries using real configuration/filesystem/native operations.

Native cases require COCHEM_WINDOWS_DEPLOYMENT_CONFIG pointing to a reviewed,
already-provisioned pipeline.json, and the protected Python running as SYSTEM.
They never provision accounts, mount RAM, execute models, or send Docker API
commands. Named-pipe security cases create and close their own local test pipe.
Linux skips are not Windows acceptance evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from cochem_pipeline.config import load_config
from cochem_pipeline.deployment import execution_readiness, verify_worker_docker_denial, verify_docker_access_boundary
from cochem_pipeline import windows as win
from pipeline_tests.test_config import config_document, write_config


def tree_bytes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


@pytest.mark.skipif(os.name == 'nt', reason='Actual non-Windows rejection contract')
@pytest.mark.parametrize('provision', [False, True])
def test_nonwindows_readiness_cannot_create_or_overwrite_deployment_state(tmp_path, provision):
    raw = config_document(tmp_path)
    config = load_config(write_config(tmp_path, raw))
    config.private_root.mkdir()
    prior = config.private_root / 'execution-readiness.json'
    prior.write_text('{"historical_only":true}', encoding='utf-8')
    before = tree_bytes(tmp_path)
    with pytest.raises(win.WindowsIsolationError, match='real Windows SYSTEM'):
        execution_readiness(config, provision=provision)
    assert tree_bytes(tmp_path) == before
    assert not any(root.exists() for root in config.slot_roots.values())


@pytest.mark.skipif(os.name == 'nt', reason='Actual non-Windows CLI rejection contract')
@pytest.mark.parametrize('command', ['doctor', 'provision-execution'])
def test_actual_deployment_cli_fails_without_provisioning_on_linux(tmp_path, command):
    filename = write_config(tmp_path, config_document(tmp_path))
    before = tree_bytes(tmp_path)
    completed = subprocess.run([sys.executable, '-m', 'cochem_pipeline', command,
                                '--config', filename], capture_output=True, text=True,
                               encoding='utf-8', timeout=30, check=False)
    assert completed.returncode != 0
    assert 'real Windows SYSTEM' in completed.stderr
    assert not completed.stdout.strip(), 'A failed native gate must not emit a ready JSON result'
    assert tree_bytes(tmp_path) == before


@pytest.mark.skipif(os.name == 'nt', reason='Actual non-Windows token rejection contract')
def test_linux_cannot_attest_native_worker_docker_pipe_denial():
    identities = {'slot1': win.WorkerIdentity('CoChemTestWorker', 'test-only-target')}
    with pytest.raises(win.WindowsIsolationError, match='real Windows SYSTEM'):
        verify_worker_docker_denial('npipe:////./pipe/docker_engine', identities)
    with pytest.raises(win.WindowsIsolationError, match='real Windows SYSTEM'):
        verify_docker_access_boundary(None, identities, required=False)


def test_failed_identity_restoration_terminates_the_whole_real_process(tmp_path):
    sentinel = tmp_path / 'unsafe-continuation.txt'
    code = '''import sys,threading
from pathlib import Path
from cochem_pipeline.deployment import _terminate_unknown_identity
def worker():
    try:
        _terminate_unknown_identity()
    except BaseException:
        Path(sys.argv[1]).write_text('exception caught')
thread=threading.Thread(target=worker)
thread.start()
thread.join()
Path(sys.argv[1]).write_text('main thread continued')
'''
    child = subprocess.run([sys.executable,'-c',code,str(sentinel)],capture_output=True,timeout=10)
    assert child.returncode == 70
    assert not sentinel.exists()


@pytest.fixture
def native_deployment():
    filename = os.environ.get('COCHEM_WINDOWS_DEPLOYMENT_CONFIG')
    if os.name != 'nt' or not filename:
        pytest.skip('Requires real Windows SYSTEM and COCHEM_WINDOWS_DEPLOYMENT_CONFIG')
    win.require_system()
    config = load_config(filename)
    identities = {slot: win.WorkerIdentity(**value) for slot, value in config.workers.items()}
    win.validate_layout(config.private_root, config.slot_roots, identities, require_defender=True)
    return config, identities


@contextmanager
def native_test_pipe(worker_sid: str, worker_access: str | None):
    """A physical named pipe with a precise worker ACL, not an emulated API."""
    api = win._api()
    descriptor = win.HANDLE()
    pipe = None
    endpoint = 'npipe:////./pipe/CoChemDeploymentTest-' + uuid.uuid4().hex
    path = '\\\\.\\pipe\\' + endpoint.rsplit('/', 1)[1]
    sddl = 'O:SYG:SYD:P(A;;GA;;;SY)'
    if worker_access is not None:
        sddl += f'(A;;{worker_access};;;{worker_sid})'
    try:
        win._check(api['advapi32'].ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, C.byref(descriptor), None), 'Create test named-pipe security descriptor')
        attributes = win._SECURITY_ATTRIBUTES(C.sizeof(win._SECURITY_ATTRIBUTES), descriptor, False)
        create = api['kernel32'].CreateNamedPipeW
        create.restype = win.HANDLE
        create.argtypes = [win.LPWSTR, win.DWORD, win.DWORD, win.DWORD, win.DWORD,
                           win.DWORD, win.DWORD, C.POINTER(win._SECURITY_ATTRIBUTES)]
        # Duplex server, first instance only, byte protocol. The test sends no
        # payload and never creates a Docker endpoint or privileged container.
        pipe = create(path, 3 | 0x00080000, 0, 1, 4096, 4096, 0, C.byref(attributes))
        if pipe in (None, C.c_void_p(-1).value):
            raise win.WindowsIsolationError(f'Cannot create real test pipe (Win32 {C.get_last_error()})')
        yield endpoint
    finally:
        if pipe not in (None, C.c_void_p(-1).value):
            win._close(pipe)
        if descriptor:
            api['kernel32'].LocalFree(descriptor)


def test_native_private_pipe_proves_all_three_access_modes_denied(native_deployment):
    _, identities = native_deployment
    slot, identity = next(iter(identities.items()))
    sid = win._sid_text(win._account_sid(identity.name))
    with native_test_pipe(sid, None) as endpoint:
        result = verify_worker_docker_denial(endpoint, {slot: identity})
    assert result == [{'slot': slot, 'access_denied': True,
                       'denied_modes': ['write', 'read', 'read_write']}]
    # Impersonation must have reverted after the probe.
    win.require_system()


@pytest.mark.parametrize('worker_access,mode', [('GW', 'write'), ('GR', 'read'), ('GA', 'write')])
def test_native_pipe_with_any_worker_access_is_not_security_denial(native_deployment, worker_access, mode):
    _, identities = native_deployment
    slot, identity = next(iter(identities.items()))
    sid = win._sid_text(win._account_sid(identity.name))
    with native_test_pipe(sid, worker_access) as endpoint:
        with pytest.raises(win.WindowsIsolationError, match=f'can access the Docker daemon \\({mode}\\)'):
            verify_worker_docker_denial(endpoint, {slot: identity})
    # This catches a real thread-token leak after the failure path.
    win.require_system()


def test_native_missing_pipe_does_not_count_as_denied_access(native_deployment):
    _, identities = native_deployment
    endpoint = 'npipe:////./pipe/CoChemMissingTest-' + uuid.uuid4().hex
    with pytest.raises(win.WindowsIsolationError, match='denial.*not established'):
        verify_worker_docker_denial(endpoint, identities)
    win.require_system()


def test_native_optional_mode_still_rejects_existing_writable_pipe(native_deployment):
    config, identities = native_deployment
    slot, identity = next(iter(identities.items()))
    sid = win._sid_text(win._account_sid(identity.name))
    with native_test_pipe(sid, 'GW') as endpoint:
        # The actual server is this Python process, not a trusted Docker image.
        with pytest.raises(win.WindowsIsolationError, match='Docker pipe server executable'):
            verify_docker_access_boundary(endpoint, {slot: identity}, required=False,
                trusted_operator=config.operator_name,trusted_server_executables=config.docker.pipe_server_executables)
    win.require_system()


def test_native_optional_mode_does_not_require_absent_configured_pipe(native_deployment):
    config, identities = native_deployment
    endpoint = 'npipe:////./pipe/CoChemAbsentOptional-' + uuid.uuid4().hex
    result = verify_docker_access_boundary(endpoint, identities, required=False,
        trusted_operator=config.operator_name,trusted_server_executables=config.docker.pipe_server_executables)
    assert endpoint not in result
    # Any discovered real Desktop aliases still need actual all-mode denial.
    for records in result.values():
        assert all(record['denied_modes'] == ['write', 'read', 'read_write'] for record in records)
    win.require_system()


def test_native_empty_or_duplicate_worker_verification_is_rejected(native_deployment):
    _, identities = native_deployment
    first = next(iter(identities.values()))
    for invalid in ({}, {'one': first, 'two': first}):
        with pytest.raises(ValueError, match='distinct, nonempty'):
            verify_worker_docker_denial('npipe:////./pipe/docker_engine', invalid)


def test_native_provisioned_execution_readiness_has_fresh_private_evidence(native_deployment):
    config, identities = native_deployment
    assert config.ramdisk.enabled and config.docker.enabled and config.coding_projects, (
        'Native execution acceptance requires a fully configured coding environment')
    before = time.time()
    report = execution_readiness(config, provision=False)
    assert report['ready'] is True, json.dumps(report['checks'], indent=2)
    assert report['checked_at'] >= before
    assert report['provisioned'] is False
    assert report['native_models_executed'] is False
    assert report['projects'] == sorted(config.coding_projects)
    assert set(report['checks']) >= {'ramdisk', 'docker', 'hardware'}
    access_by_endpoint = report['checks']['docker']['evidence']['worker_access']
    assert config.docker.endpoint in access_by_endpoint
    for access in access_by_endpoint.values():
        assert {entry['slot'] for entry in access} == set(identities)
        assert all(entry['access_denied'] and entry['denied_modes'] == ['write', 'read', 'read_write']
                   for entry in access)
        for entry in access:
            server = entry['server']
            assert server['pid'] > 0 and server['process_created_filetime'] > 0
            assert os.path.normcase(server['executable']) in {
                os.path.normcase(path) for path in config.docker.pipe_server_executables}
    target = config.private_root / 'execution-readiness.json'
    win.validate_private_path(target)
    assert json.loads(target.read_text(encoding='utf-8')) == report
    assert not list(config.private_root.glob('.execution-readiness-*'))
