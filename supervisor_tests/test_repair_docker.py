"""Repair-only Docker admission: real policy/ledger checks, explicit host tests.

The local engine driver is a labelled protocol boundary, not simulated Win32
security. Native denial requires the actual installed SYSTEM configuration.
"""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from cochem_supervisor.windows import repair_docker_endpoint, verify_repair_docker_boundary
from cochem_pipeline.windows import WindowsIsolationError
from supervisor_tests.test_engine import LocalSupervisor, incident


def pipeline_policy():
    image = 'sha256:' + 'a' * 64
    return {'docker': {'enabled': True, 'image': image, 'allowed_images': [image],
                       'endpoint': 'npipe:////./pipe/docker_engine',
                       'commands': [{'name': 'regression', 'argv': ['python', '-m', 'pytest', 'tests']}]}}


def test_repair_uses_exact_reviewed_local_endpoint_without_mutating_policy():
    policy = pipeline_policy()
    original = deepcopy(policy)
    assert repair_docker_endpoint(policy) == policy['docker']['endpoint']
    assert policy == original
    assert repair_docker_endpoint({}) is None
    assert repair_docker_endpoint({'docker': {'enabled': False}}) is None


@pytest.mark.parametrize('changes', [
    {'enabled': 'false'}, {'enabled': 0}, {'endpoint': 'unix:///var/run/docker.sock'},
    {'endpoint': 'tcp://127.0.0.1:2375'}, {'endpoint': 'npipe:////./pipe/docker_engine\n'},
    {'endpoint': 'npipe:////./pipe/../other'}, {'image': 'latest'}, {'extra': True},
])
def test_invalid_repair_scope_is_never_silently_docker_disabled(changes):
    policy = pipeline_policy()
    policy['docker'].update(changes)
    with pytest.raises(ValueError):
        repair_docker_endpoint(policy)


@pytest.mark.parametrize('policy', [[], None, {'docker': None}, {'docker': []}, {'docker': False}])
def test_malformed_pipeline_policy_is_not_a_disabled_execution_plane(policy):
    with pytest.raises(ValueError):
        repair_docker_endpoint(policy)


class BoundaryUnavailable(LocalSupervisor):
    """Only the explicit native boundary is absent; ledger/engine are real."""
    def _repair_boundary_check(self):
        raise WindowsIsolationError('Protocol fixture: repair pipe denial not established')


def test_repair_hold_precedes_charge_workspace_changes_and_cli_probes(tmp_path):
    supervisor = BoundaryUnavailable(tmp_path)
    observed = incident(supervisor)
    sentinel = Path(supervisor.config['repair_workspace']) / 'existing.txt'
    sentinel.write_text('must survive infrastructure hold', encoding='utf-8')
    assert supervisor._repair(observed) is False
    assert supervisor._version_checks() == []
    assert supervisor.ledger.get_incident(observed['fingerprint'])['attempts'] == 0
    assert supervisor.clear_calls == 0
    assert supervisor.runner.repair_calls == []
    assert supervisor.runner.process_calls == []
    assert sentinel.read_text() == 'must survive infrastructure hold'
    assert supervisor.ledger.has_event('REPAIR_ISOLATION_BLOCKED')


def test_repair_boundary_hold_does_not_prevent_supervisor_journal_recovery(tmp_path):
    supervisor = BoundaryUnavailable(tmp_path)
    assert supervisor.recover() is None
    assert supervisor.runner.repair_calls == []
    assert supervisor.stage == 'recovering'


@pytest.mark.skipif(os.name == 'nt', reason='Actual non-Windows capability rejection')
def test_repair_boundary_cannot_attest_windows_from_linux():
    with pytest.raises(WindowsIsolationError, match='real Windows SYSTEM'):
        verify_repair_docker_boundary({})


def test_actual_repair_identity_denied_every_existing_docker_api_pipe():
    if os.name != 'nt':
        pytest.skip('Requires real Windows SYSTEM, repair identity and Docker API ACLs')
    filename = os.environ.get('COCHEM_SUPERVISOR_WINDOWS_CONFIG')
    if not filename:
        pytest.skip('Set COCHEM_SUPERVISOR_WINDOWS_CONFIG and run installed Python as SYSTEM')
    from cochem_supervisor.config import load_config
    from cochem_supervisor.windows import require_supervisor
    config = load_config(filename)
    require_supervisor(config)
    receipt = verify_repair_docker_boundary(config, config['repair_worker'])
    if not receipt['worker_access']:
        pytest.skip('Docker is disabled and no known local API pipe exists; no access-denial claim is made')
    for records in receipt['worker_access'].values():
        assert len(records) == 1
        assert records[0]['slot'] == 'repair' and records[0]['access_denied'] is True
        assert records[0]['denied_modes'] == ['write', 'read', 'read_write']
        assert records[0]['server']['pid'] > 0
        assert records[0]['server']['process_created_filetime'] > 0
