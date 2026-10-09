"""Physical capability files and SQLite routing; no CLI/login/model emulation."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cochem_pipeline.config import load_config
from cochem_pipeline.failures import ProviderFailure
from cochem_pipeline.provider_integration import integration_hold, IntegrationVerificationHold
from cochem_pipeline.worker import NativeRunner, check_native_integration, provider_command
from cochem_supervisor.runner import RepairRunner, NativeRepairProtocolError, validate_provider_spec


def held_spec(tmp_path):
    digest = hashlib.sha256(b'non-executable test bytes').hexdigest()
    evidence = tmp_path / 'capability.json'
    evidence.write_text(json.dumps({'schema': 'windows-native-contract-followup/1',
        'installed_executables': {'gemini': {'version': 'fixture-1', 'sha256': digest}},
        'activation_blockers': [{'id': 'AGY-ISOLATION', 'status': 'unverified'}]}))
    return {'provider': 'gemini', 'model': 'gemini-3.8-flash',
        'executable': str(tmp_path / 'staged' / 'agy.exe'),
        'arguments': None, 'subscription_probe': None, 'inference_only': None,
        'integration_hold': {'schema': 'native-integration-hold/1', 'state': 'verification_pending',
            'scope': 'isolated_pipeline_integration',
            'reason': 'Owner confirms Agy works; isolated integration has not been verified. Fixture only.',
            'evidence_path': str(evidence), 'evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest(),
            'executable_sha256': digest, 'version': 'fixture-1', 'finding_ids': ['AGY-ISOLATION']}}


def test_held_staged_binary_can_be_absent_but_existing_bytes_must_match(tmp_path):
    spec = held_spec(tmp_path)
    observed = integration_hold('gemini', spec)
    assert observed['state'] == 'verification_pending'
    assert observed['category'] == 'compatibility'
    assert observed['scope'] == 'isolated_pipeline_integration'
    assert observed['staged_executable_present'] is False
    path = Path(spec['executable'])
    path.parent.mkdir()
    path.write_bytes(b'non-executable test bytes')
    assert integration_hold('gemini', spec)['staged_executable_present'] is True
    path.write_bytes(b'replaced bytes')
    with pytest.raises(ValueError, match='differs'):
        integration_hold('gemini', spec)


@pytest.mark.parametrize('change', [
    lambda s: s.update(integration_hold=None),
    lambda s: s['integration_hold'].update(state='ready'),
    lambda s: s['integration_hold'].update(scope='provider_outage'),
    lambda s: s['integration_hold'].update(category='auth'),
    lambda s: s['integration_hold'].update(reason=''),
    lambda s: s['integration_hold'].update(evidence_sha256='a'*64),
    lambda s: s['integration_hold'].update(executable_sha256='a'*64),
    lambda s: s['integration_hold'].update(version='not-observed'),
    lambda s: s['integration_hold'].update(finding_ids=['CODEX-ISOLATION']),
    lambda s: s['integration_hold'].update(finding_ids=['AGY-MISSING']),
    lambda s: s['integration_hold'].update(finding_ids=['AGY-ISOLATION']*2),
    lambda s: s.update(executable='relative.exe'),
])
def test_invalid_or_contradictory_hold_never_enables_execution(tmp_path, change):
    spec = held_spec(tmp_path)
    change(spec)
    with pytest.raises((ValueError, OSError)):
        integration_hold('gemini', spec)
    with pytest.raises(ProviderFailure) as caught:
        check_native_integration('gemini', spec)
    assert caught.value.category == 'compatibility'
    assert caught.value.integration_evidence['state'] == 'evidence_invalid'


@pytest.mark.parametrize('mutation', ['changed', 'missing', 'resolved', 'duplicate'])
def test_missing_changed_or_nonpending_evidence_fails_closed(tmp_path, mutation):
    spec = held_spec(tmp_path)
    path = Path(spec['integration_hold']['evidence_path'])
    if mutation == 'missing':
        path.unlink()
    elif mutation == 'changed':
        path.write_text(path.read_text() + '\n')
    else:
        data = json.loads(path.read_text())
        if mutation == 'resolved':
            data['activation_blockers'][0]['status'] = 'verified'
        else:
            data['activation_blockers'].append(copy.deepcopy(data['activation_blockers'][0]))
        path.write_text(json.dumps(data))
        spec['integration_hold']['evidence_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises((ValueError, OSError)):
        integration_hold('gemini', spec)


def test_evidence_relocation_requires_identical_bytes_and_retains_hold(tmp_path):
    spec = held_spec(tmp_path)
    original = Path(spec['integration_hold']['evidence_path'])
    copied = tmp_path / 'copied-capability.json'
    copied.write_bytes(original.read_bytes())
    spec['integration_hold']['evidence_path'] = str(copied)
    assert integration_hold('gemini', spec)['evidence_sha256'] == spec['integration_hold']['evidence_sha256']
    copied.write_bytes(b'{}')
    with pytest.raises(ValueError, match='digest changed'):
        integration_hold('gemini', spec)


def test_pipeline_config_permits_only_evidence_bound_deferral_of_native_contract(tmp_path):
    from pipeline_tests.test_config import config_document, write_config
    raw = config_document(tmp_path, slots=4)
    spec = held_spec(tmp_path)
    raw['providers']['gemini'] = spec
    config = load_config(write_config(tmp_path, raw))
    assert config.max_execution_slots == 4
    assert [r.provider for r in config.routing.candidates(1)] == ['gemini', 'claude', 'codex']
    assert config.providers['gemini']['subscription_probe'] is None
    del spec['integration_hold']
    with pytest.raises(ValueError, match='verified native headless'):
        load_config(write_config(tmp_path, raw))


def test_hold_precedes_every_native_policy_probe_auth_and_command_path(tmp_path):
    spec = held_spec(tmp_path)
    with pytest.raises(ProviderFailure) as caught:
        NativeRunner(None)._inference_preflight({'route': {'model': spec['model']}}, 'gemini',
            ['must-not-launch'], spec, None, None, lambda: True, None, {}, None)
    assert caught.value.category == 'compatibility'
    assert caught.value.integration_evidence['evidence_sha256'] == spec['integration_hold']['evidence_sha256']
    with pytest.raises(IntegrationVerificationHold):
        provider_command('gemini', ['must-not-launch'], spec['model'], 'missing-workspace', spec)
    with pytest.raises(IntegrationVerificationHold):
        validate_provider_spec(spec)
    logs = tmp_path / 'must-not-create'
    with pytest.raises(NativeRepairProtocolError) as repair:
        RepairRunner().preflight_selected_spec(spec, {}, tmp_path / 'missing', logs, lambda: True)
    assert repair.value.category == 'compatibility'
    assert repair.value.integration_evidence['state'] == 'verification_pending'
    assert not logs.exists()
    with pytest.raises(IntegrationVerificationHold):
        RepairRunner().run(spec, {}, tmp_path / 'missing', {}, logs, 60, lambda: True)


def test_runtime_records_scoped_hold_and_real_ordered_spillover_without_failure_spend(tmp_path):
    from pipeline_tests.test_runtime_routing import controller, claim
    runtime, _ = controller(tmp_path)
    first = claim(runtime)
    assert first['route']['provider'] == 'gemini'
    spec = held_spec(tmp_path)
    runtime.config.providers = {'gemini': spec}
    with pytest.raises(ProviderFailure) as caught:
        # The real runner validates the actual stored reservation, then holds
        # before it can require a RAM workspace or construct a worker identity.
        NativeRunner(runtime.config).run(first, 'slot1', '', lambda: True)
    assert runtime._record_failure(first, 'slot1', caught.value)
    second = claim(runtime)
    assert second['route']['provider'] == 'claude' and second['route']['candidate_index'] == 1
    assert second['routing']['failure_count'] == 0
    assert len(runtime.store.routing_status()['active_reservations']) == 1
    with sqlite3.connect(runtime.store.path) as db:
        events = [json.loads(row[0]) for row in db.execute('SELECT details FROM recovery_telemetry')]
    assert [event['action'] for event in events] == ['integration_verification_hold']
    assert events[0]['category'] == 'compatibility'
    assert events[0]['integration_evidence']['evidence_sha256'] == spec['integration_hold']['evidence_sha256']
    assert 'auth_failed' not in json.dumps(events) and 'provider_unavailable' not in json.dumps(events)


def test_supervisor_keeps_pending_integration_as_a_canonical_unspent_candidate(tmp_path):
    from cochem_supervisor.engine import Supervisor
    from cochem_supervisor.job_board import RepairJobBoard
    spec = held_spec(tmp_path)
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.private = tmp_path
    supervisor.config = {'providers': [spec]}
    assert supervisor._eligible_providers() == [spec]
    board = RepairJobBoard(tmp_path / 'repair-board.db')
    job = board.submit('integration-fixture', {}, kind='PREFLIGHT_REQUEST', now=100)
    first = board.claim(job['job_id'], ['gemini', 'claude', 'codex'], primary_cleanup_verified=True, now=101)
    assert first['provider'] == 'gemini'
    with pytest.raises(NativeRepairProtocolError):
        RepairRunner().preflight_selected_spec(spec, {}, tmp_path, tmp_path / 'logs', lambda: True)
    board.reject_unspent(first, failure='compatibility', now=102)
    retained = RepairJobBoard(board.path).get(job['job_id'])
    assert retained['dispatches'] == 0 and retained['receipt'] is None
    second = board.claim(job['job_id'], ['gemini', 'claude', 'codex'], primary_cleanup_verified=True, now=103)
    assert second['provider'] == 'claude' and second['candidate_index'] == 1
    assert retained['policy']['tiers']['1-3'][0]['provider'] == 'gemini'


def test_engine_preflight_persists_scoped_evidence_before_ordered_next_candidate(tmp_path):
    import threading
    from cochem_supervisor.engine import Supervisor
    from cochem_supervisor.job_board import RepairJobBoard
    from cochem_supervisor.state import Ledger
    spec = held_spec(tmp_path)
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.private = tmp_path
    supervisor.config = {'repair_worker': {}}
    supervisor.current_attempt = None
    supervisor.stop_event = threading.Event()
    supervisor.runner = RepairRunner()
    supervisor.ledger = Ledger(tmp_path / 'supervisor.db')
    board = RepairJobBoard(tmp_path / 'repair-board.db')
    job = board.submit('engine-integration-fixture', {}, kind='PREFLIGHT_REQUEST')
    # A deliberately incomplete next-provider fixture stops at its own native
    # capability validation. Neither route reaches a process or paid gate.
    assert supervisor._claim_preflighted_route(board, job['job_id'],
        {'gemini': spec, 'claude': {'provider': 'claude'}}, tmp_path) is None
    with sqlite3.connect(supervisor.ledger.path) as db:
        events = [json.loads(row[0]) for row in db.execute(
            "SELECT details_json FROM supervisor_events WHERE event='REPAIR_ROUTE_PREFLIGHT_REJECTED' ORDER BY id")]
    assert len(events) == 2
    assert events[0]['key'].startswith('gemini:') and events[0]['category'] == 'compatibility'
    assert events[0]['integration_evidence']['evidence_sha256'] == spec['integration_hold']['evidence_sha256']
    assert events[1]['key'].startswith('claude:') and events[1]['category'] == 'compatibility'
    assert 'integration_evidence' not in events[1]
    assert all(event['paid_inference'] is False for event in events)
    assert board.get(job['job_id'])['dispatches'] == 0
    assert 'provider_unavailable' not in json.dumps(events) and 'auth_failed' not in json.dumps(events)


def test_supervisor_cannot_drop_or_corrupt_pipeline_integration_binding(tmp_path):
    from supervisor_tests.test_config import document, write_config
    from cochem_supervisor.config import load_config as load_supervisor
    raw = document(tmp_path)
    spec = held_spec(tmp_path)
    pipeline = Path(raw['pipeline_config'])
    data = json.loads(pipeline.read_text())
    data['providers']['gemini'] = spec
    pipeline.write_text(json.dumps(data))
    raw['providers'].append(copy.deepcopy(spec))
    config = load_supervisor(write_config(tmp_path, raw))
    assert config['provider_integration_holds']['gemini']['state'] == 'verification_pending'
    raw['providers'][-1]['integration_hold']['evidence_sha256'] = 'a'*64
    with pytest.raises(ValueError, match='evidence is invalid'):
        load_supervisor(write_config(tmp_path, raw))
    raw['providers'][-1] = {k:v for k,v in spec.items() if k != 'integration_hold'}
    with pytest.raises(ValueError, match='preserve the pipeline integration hold'):
        load_supervisor(write_config(tmp_path, raw))


def test_pending_integration_cannot_be_certified_from_a_native_receipt_fixture(tmp_path):
    from cochem_supervisor.probes import verify_effort_profile
    spec = held_spec(tmp_path)
    with pytest.raises(ValueError, match='integration verification remains pending'):
        verify_effort_profile({}, {'provider': 'gemini', 'model': spec['model'], 'reasoning_effort': None},
                              {'gemini': spec})
