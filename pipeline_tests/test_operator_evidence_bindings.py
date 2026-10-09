"""Physical manifest mutation and synthetic topology projection counterexamples.

Topology records here are fixtures, not native Windows deployment acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline import deployment
from cochem_pipeline.operator_views import acceptance_dashboard, deployment_view
from pipeline_tests.test_operator_views import _catalog


@pytest.mark.parametrize('filename,operation', [
    ('src/controller.py', 'change'), ('src/controller.py', 'remove'),
    ('uv.lock', 'change'), ('uv.lock', 'remove'), ('src/added.py', 'change'),
])
def test_installed_source_and_dependency_changes_reopen_historical_closures(tmp_path, filename, operation):
    _catalog(tmp_path, platforms=('POSIX',))
    before = (tmp_path / 'docs/acceptance_4.2.7.json').read_bytes()
    assert acceptance_dashboard(tmp_path)['all_verified']
    target = tmp_path / filename
    if operation == 'remove':
        target.unlink()
    else:
        target.write_text('raise RuntimeError("not the tested bytes")\n')
    result = acceptance_dashboard(tmp_path)
    assert result['counts']['verified'] == 0
    assert result['source_freshness'][0]['status'] == 'stale'
    assert all(row['evidence'][0]['historical_artifact_verified'] for row in result['requirements'])
    assert (tmp_path / 'docs/acceptance_4.2.7.json').read_bytes() == before


def test_missing_unbound_and_external_manifests_never_verify(tmp_path):
    _, evidence = _catalog(tmp_path, platforms=('POSIX',))
    for relative in (None, '../external.json', 'docs/absent.json'):
        for row in evidence['evidence']:
            row['tested_source_manifest'] = relative
        (tmp_path / 'docs/acceptance_4.2.7.json').write_text(json.dumps(evidence))
        assert acceptance_dashboard(tmp_path)['counts']['verified'] == 0


def test_validation_artifact_can_bind_manifest_without_rewriting_history(tmp_path):
    _, evidence = _catalog(tmp_path, platforms=('POSIX',))
    binding = {key: evidence['evidence'][0][key] for key in
               ('tested_source_manifest', 'tested_source_manifest_sha256')}
    artifact = tmp_path / 'docs/validation.json'
    artifact.write_text(json.dumps(binding))
    for row in evidence['evidence']:
        for key in binding:
            row.pop(key)
        row.update(artifact='docs/validation.json', sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    (tmp_path / 'docs/acceptance_4.2.7.json').write_text(json.dumps(evidence))
    assert acceptance_dashboard(tmp_path)['all_verified']
    (tmp_path / 'uv.lock').write_text('changed lock')
    assert not acceptance_dashboard(tmp_path)['all_verified']


def _topology(tmp_path, monkeypatch):
    now = time.time()
    endpoint = 'npipe:////./pipe/docker_engine'
    ram = SimpleNamespace(mount_root='R:\\', size_mb=8192, backing='vm')
    policy = SimpleNamespace(endpoint=endpoint, pipe_server_executables=['C:\\Docker\\com.docker.backend.exe'])
    config = SimpleNamespace(private_root=tmp_path, ramdisk=ram, docker=policy,
        workers={'slot1': {'name': 'Worker1', 'credential_target': 'DO NOT PROJECT SECRET'}},
        slot_roots={'slot1': 'R:\\slot1'}, job_db=tmp_path / 'jobs.db', operator_name=None)
    runtime = SimpleNamespace(config=config, boot_id=99, store=SimpleNamespace(path=config.job_db),
        docker=SimpleNamespace(policy=policy, _daemon_identity='engine-1'))
    sources = deployment.attestation_source_hashes()
    report = {'checked_at': now, 'boot_id': 99, 'attestation_source_sha256': sources,
        'topology_configuration': deployment.topology_configuration(config), 'checks': {
        'docker': {'ready': True, 'evidence': {'daemon_id': 'engine-1', 'worker_access': {endpoint: [{
            'slot': 'slot1', 'access_denied': True, 'denied_modes': ['read', 'write', 'read_write'],
            'credential': 'DO NOT PROJECT SECRET', 'server': {'pid': 51, 'token_sid': 'S-1-5-18',
                'process_created_filetime': 12, 'checked_at': now,
                'executable': policy.pipe_server_executables[0], 'password': 'DO NOT PROJECT SECRET'}}]}}},
        'ramdisk': {'ready': True, 'evidence': {'mount_root': 'R:\\', 'checked_at': now,
            'observed': {'device_number': 0, 'target': '\\Device\\ImDisk0', 'size_bytes': 8192 * 1024**2,
                'backing': 'vm', 'nonpageable': False, 'filesystem': 'NTFS', 'volume_serial': 7}}}}}
    monkeypatch.setattr(deployment, 'attest_controller_identity', lambda: {
        'pid': os.getpid(), 'token_sid': 'S-1-5-18', 'is_system': True,
        'checked_at': now, 'source': 'explicit projection fixture', 'source_sha256': 'a' * 64})
    return runtime, report


def _view(runtime, report):
    (runtime.config.private_root / 'execution-readiness.json').write_text(json.dumps(report))
    return deployment_view(runtime, [], {'pid': os.getpid()})


def test_current_projections_show_physical_fields_and_strip_secrets(tmp_path, monkeypatch):
    runtime, report = _topology(tmp_path, monkeypatch)
    result = _view(runtime, report)
    nodes = {node['id']: node for node in result['nodes']}
    assert nodes['controller']['observed_identity']['token_sid'] == 'S-1-5-18'
    assert nodes['docker']['attestation_state'] == nodes['ram']['attestation_state'] == 'current'
    assert nodes['ram']['observed_device']['nonpageable'] is False
    assert nodes['docker']['pipe_access_evidence'][0]['workers'][0]['server']['pid'] == 51
    assert nodes['docker']['repair_access_state'] == 'unknown'
    assert nodes['ram']['capture']['capture_sha256']
    assert not result['observed_drift']
    assert 'DO NOT PROJECT SECRET' not in json.dumps(result)
    assert '(current)' in result['mermaid']


@pytest.mark.parametrize('field,value', [('checked_at', 1), ('boot_id', 100),
    ('attestation_source_sha256', {}), ('topology_configuration', {})])
def test_old_boot_time_source_or_config_capture_cannot_attest_current_topology(tmp_path, monkeypatch, field, value):
    runtime, report = _topology(tmp_path, monkeypatch)
    report[field] = value
    nodes = {node['id']: node for node in _view(runtime, report)['nodes']}
    assert nodes['ram']['attestation_state'] in {'stale', 'drift'}
    assert nodes['docker']['attestation_state'] in {'stale', 'drift'}
    assert nodes['ram']['runtime_verified'] is False


@pytest.mark.parametrize('fact', ['controller', 'engine', 'pipe', 'denial', 'ram_path', 'ram_backing', 'ram_size'])
def test_each_observed_boundary_drift_is_visible(tmp_path, monkeypatch, fact):
    runtime, report = _topology(tmp_path, monkeypatch)
    docker = report['checks']['docker']['evidence']
    ram = report['checks']['ramdisk']['evidence']
    if fact == 'controller':
        monkeypatch.setattr(deployment, 'attest_controller_identity', lambda: {
            'pid': os.getpid(), 'token_sid': 'S-1-5-21-123', 'is_system': False})
    elif fact == 'engine':
        docker['daemon_id'] = 'engine-2'
    elif fact == 'pipe':
        docker['worker_access'][runtime.docker.policy.endpoint][0]['server']['token_sid'] = 'S-1-5-21-123'
    elif fact == 'denial':
        docker['worker_access'][runtime.docker.policy.endpoint][0]['access_denied'] = False
    elif fact == 'ram_path':
        ram['mount_root'] = 'D:\\'
    elif fact == 'ram_backing':
        ram['observed']['backing'] = 'awe'
    else:
        ram['observed']['size_bytes'] = 1024
    result = _view(runtime, report)
    assert result['observed_drift']
    assert '(drift)' in result['mermaid']


def test_missing_readiness_is_unknown_even_when_runtime_objects_exist(tmp_path, monkeypatch):
    runtime, _ = _topology(tmp_path, monkeypatch)
    nodes = {node['id']: node for node in deployment_view(runtime, [], {})['nodes']}
    assert nodes['controller']['attestation_state'] == 'unknown'
    assert nodes['docker']['attestation_state'] == nodes['ram']['attestation_state'] == 'unknown'
    assert not nodes['ram']['runtime_verified']


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows process-token observation; no token emulation')
def test_current_process_token_observation_is_physical_and_does_not_require_system():
    before = time.time()
    observed = deployment.attest_controller_identity()
    assert observed['pid'] == os.getpid()
    assert observed['token_sid'].startswith('S-1-5-')
    assert observed['is_system'] == (observed['token_sid'] == 'S-1-5-18')
    assert observed['checked_at'] >= before
    assert observed['boot_id'] if observed['is_system'] else observed['boot_id'] is None
    assert observed['source'] == 'current_windows_process_token'
    assert observed['source_sha256'] == hashlib.sha256(Path(deployment.__file__).read_bytes()).hexdigest()
