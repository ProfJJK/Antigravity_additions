"""Physical queue diagnostics; portable tests never establish Windows acceptance."""
import json
import os
import hashlib
import subprocess
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from cochem_pipeline.queue_launch import QueueLaunchObserver, distribution, _native_receipt_reason
from cochem_pipeline.queue_profile import benchmark_controller_queue, benchmark_configured_queue
from cochem_pipeline.routing import RoutingPolicy
from cochem_pipeline.store import JobStore, output_digest


def runtime(tmp_path):
    config = SimpleNamespace(slot_roots={f"slot{i}": tmp_path / str(i) for i in range(4)},
        lease_seconds=1800, heartbeat_seconds=5, routing=RoutingPolicy())
    return SimpleNamespace(config=config, store=JobStore(tmp_path / 'actual.db'))


def complete_manifest(store, node):
    output = {"chapters": [{"chapter_id": "c1", "title": "One", "requirements": ["R1"]}]}
    receipt = {"provider": "codex", "pid": os.getpid(), "exit_code": 0,
        "session_id": "synthetic-observer-contract", "execution_kind": "python-test",
        "output_sha256": output_digest(output)}
    return store.complete(node['job_id'], node['attempt_id'], node['fencing_token'], output, receipt)


def test_nearest_rank_percentiles_report_all_required_tail_measures():
    assert distribution([])['maximum_ms'] is None
    values = distribution(range(1, 101))
    assert values == {'count': 100, 'p50_ms': 50.5, 'p95_ms': 95, 'p99_ms': 99, 'maximum_ms': 100}


@pytest.mark.skipif(os.name == 'nt', reason='Portable observer checks do not assert native SYSTEM identity')
def test_observer_times_actual_store_and_keeps_committed_jobs_and_closed_store(tmp_path):
    actual = runtime(tmp_path)
    original = actual.store.claim
    workflow = actual.store.submit('Representative fixture', ['R1'], 1)
    with QueueLaunchObserver(actual, tmp_path / 'evidence') as observer:
        node = actual.store.claim('real-controller', worker_slot='slot0')
        assert actual.store.heartbeat(node['job_id'], node['attempt_id'], node['fencing_token'])
        complete_manifest(actual.store, node)
        actual.store.workflow(workflow['workflow_id'])
        report = observer.report()
        assert report['claim_acquisition']['count'] == 1
        assert report['claim_acquisition']['maximum_ms'] > 0
        assert report['operations']['complete']['total_calls'] == 1
        assert report['coverage']['settings_match_production']
        assert report['coverage']['no_duplicate_claim_identity']
        assert report['native_windows_acceptance'] is False
        assert report['workload']['observed_fixture_completions'] == 1
        assert report['workload']['synthetic_outputs'] is True
        assert report['workload']['completed_native_attempts'] == 0
        assert report['acceptance_status'] == 'pending_windows_launch'
        assert report['actual_database'] == str(actual.store.path)
        assert 'Representative fixture' not in json.dumps(report)
        # Runtime closes its store before the observer's context exits.
        actual.store.close()
    assert actual.store.claim == original
    assert actual.store._writer_connection is None
    assert actual.queue_launch_observer is None
    persisted = json.loads((tmp_path / 'evidence' / 'queue-launch.json').read_text())
    assert persisted['coverage']['heartbeat_activity']


@pytest.mark.skipif(os.name == 'nt', reason='Portable observer checks do not assert native SYSTEM identity')
def test_observer_is_bounded_and_does_not_count_same_pid_as_four_workers(tmp_path):
    actual = runtime(tmp_path)
    for index in range(4):
        actual.store.submit('Fixture ' + str(index), ['R1'], 1)
    with QueueLaunchObserver(actual, tmp_path / 'bounded') as observer:
        for index in range(4):
            node = actual.store.claim('controller', worker_slot='slot' + str(index))
            observer.observe_native_launch(node, 'slot' + str(index), os.getpid())
        assert observer.maximum_native == 1
        observer.timings['list_workflows'] = deque(maxlen=2)
        for _ in range(4):
            actual.store.list_workflows()
        assert len(observer.timings['list_workflows']) == 2
        assert observer.report()['windowed_operations'] == ['list_workflows']
        assert observer.report()['coverage']['sample_not_truncated'] is True
    actual.store.close()


@pytest.mark.skipif(os.name == 'nt', reason='Portable observer checks do not assert native SYSTEM identity')
def test_observer_failures_do_not_change_a_successful_queue_claim(tmp_path, monkeypatch):
    actual = runtime(tmp_path)
    actual.store.submit('Survives instrumentation failure', ['R1'], 1)
    with QueueLaunchObserver(actual, tmp_path / 'errors') as observer:
        def broken_record(*_):
            raise ValueError('diagnostic only')
        monkeypatch.setattr(observer, '_claim', broken_record)
        node = actual.store.claim('controller')
        assert node['status'] == 'IN_PROGRESS'
        assert observer.observer_errors == 1
        assert observer.report()['coverage']['no_observer_error'] is False
    actual.store.close()


@pytest.mark.skipif(os.name == 'nt', reason='Portable observer checks do not assert native SYSTEM identity')
def test_failed_initial_publication_restores_real_store_methods(tmp_path, monkeypatch):
    actual = runtime(tmp_path)
    original = actual.store.claim
    observer = QueueLaunchObserver(actual, tmp_path / 'broken-output')
    def fail():
        raise OSError('Disk failed')
    monkeypatch.setattr(observer, 'publish', fail)
    with pytest.raises(OSError, match='Disk failed'):
        with observer:
            pytest.fail('Cannot enter with failed evidence publication')
    assert actual.store.claim == original
    assert actual.queue_launch_observer is None
    assert not observer.thread.is_alive()
    actual.store.close()


@pytest.mark.skipif(os.name == 'nt', reason='Native sibling-directory benchmark requires protected SYSTEM deployment')
def test_four_spawned_job_processes_share_controller_queue_without_touching_live_database(tmp_path):
    production = tmp_path / 'job_board.db'
    production.write_bytes(b'Existing production database is never opened by the profile')
    retained = production.read_bytes()
    report = benchmark_controller_queue(tmp_path / 'profile', database_directory=tmp_path,
        workflows=4, heartbeat_seconds=1, lease_seconds=30,
        worker_delays=(.02, .04, 1.1), artifact_sizes=(1024, 8192, 65536),
        routing={'backoff_base_seconds': .001, 'backoff_max_seconds': .01,
                 'backoff_jitter_fraction': 0}, deadline_seconds=60)
    assert report['passed']
    assert report['worker_processes'] == len(set(report['worker_pids'])) == 4
    assert report['workload']['completed_jobs'] == 20
    assert report['claim_acquisition']['count'] == 20
    assert report['operations']['heartbeat']['count'] > 0
    assert report['operations']['stale_heartbeat']['count'] == 20
    assert report['maximum_active_jobs'] == 4
    assert report['correctness']['stale_heartbeat_and_completion_rejected']
    assert report['native_windows_acceptance'] is False
    assert report['acceptance_status'] == 'pending_actual_windows_launch'
    assert report['disposable_database_removed']
    assert not Path(report['database']).exists()
    assert production.read_bytes() == retained
    assert not list(tmp_path.glob('queue-launch-profile-*'))
    assert json.loads((tmp_path / 'profile' / 'queue-profile.json').read_text()) == report


def test_configured_queue_requires_absolute_existing_production_storage(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'private_root': 'relative', 'slot_roots': {}}))
    with pytest.raises(ValueError, match='absolute private_root'):
        benchmark_configured_queue(config, tmp_path / 'evidence')
    assert not (tmp_path / 'evidence').exists()


def test_profile_rejects_unbounded_fixture_or_invalid_lease(tmp_path):
    with pytest.raises(ValueError, match='bounded synthetic delays'):
        benchmark_controller_queue(tmp_path / 'bad', database_directory=tmp_path,
                                   worker_delays=(float('nan'),))
    with pytest.raises(ValueError, match='at least three heartbeats'):
        benchmark_controller_queue(tmp_path / 'bad', database_directory=tmp_path,
                                   lease_seconds=5, heartbeat_seconds=5)


@pytest.mark.skipif(os.name == 'nt', reason='Portable fixture processes cannot attest Windows CLI inference')
def test_four_physical_fixture_processes_cannot_supply_native_completion_overlap(tmp_path):
    actual = runtime(tmp_path)
    for number in range(4):
        actual.store.submit('Physical fixture ' + str(number), ['R1'], 1)
    children = []
    try:
        with QueueLaunchObserver(actual, tmp_path / 'physical-fixtures') as observer:
            nodes = []
            for number in range(4):
                node = actual.store.claim('controller', worker_slot='slot' + str(number))
                child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
                                         stdin=subprocess.PIPE)
                children.append(child)
                observer.observe_native_launch(node, 'slot' + str(number), child.pid)
                nodes.append(node)
            for node in nodes:
                complete_manifest(actual.store, node)
            report = observer.report()
            assert report['workload']['maximum_live_native_processes'] == 4
            assert report['workload']['observed_fixture_completions'] == 4
            assert report['workload']['synthetic_outputs'] is True
            assert report['workload']['completed_native_attempts'] == 0
            assert report['coverage']['four_overlapping_receipt_attested_completions'] is False
            assert report['coverage']['all_native_completions_attested'] is False
            assert report['native_windows_acceptance'] is False
    finally:
        for child in children:
            child.stdin.close()
            child.wait(timeout=5)
        actual.store.close()


@pytest.mark.skipif(os.name == 'nt', reason='Portable store observer does not simulate native Windows')
def test_false_completion_result_is_never_counted_as_completed(tmp_path):
    actual = runtime(tmp_path)
    actual.store.submit('Noncompletion fixture', ['R1'], 1)
    # A deliberately broken controller adapter returns False without touching
    # SQLite. The observer must not turn its nonexception return into success.
    actual.store.complete = lambda *args, **kwargs: False
    with QueueLaunchObserver(actual, tmp_path / 'not-completed') as observer:
        node = actual.store.claim('controller')
        assert complete_manifest(actual.store, node) is False
        assert actual.store.get(node['job_id'])['status'] == 'IN_PROGRESS'
        report = observer.report()
        assert not observer.completed
        assert report['workload']['synthetic_outputs'] is None
        assert report['workload']['completed_native_attempts'] == 0
        assert report['workload']['completion_rejections']['store_completion_not_accepted'] == 1
    actual.store.close()


def native_receipt_contract_fixture():
    """Literal validator inputs only; never submitted as Windows execution evidence."""
    binding = {'job_id': 'j', 'workflow_id': 'w', 'attempt_id': 'a', 'fencing_token': 1,
        'route': {'provider': 'codex', 'model': 'gpt-6-astra', 'reasoning_effort': 'low',
                  'reservation_id': 'reservation', 'attempt_id': 'a', 'fencing_token': 1, 'worker_slot': 'slot1'}}
    observed = {'pid': 123, 'created_at': 1_700_000_000., 'creation_filetime': 133444736000000000,
        'slot': 'slot1', 'configured_worker': 'Worker1',
        'matches_configured_worker': True, 'matches_configured_executable': True}
    output = {'contract': 'fixture-only metadata'}
    receipt = {'execution_kind': 'native_cli', 'subscription_verified': True,
        'process_identity_source': 'owned_windows_process_handle', 'pid': observed['pid'],
        'process_creation_time': observed['created_at'], 'process_creation_filetime': observed['creation_filetime'],
        'exit_code': 0, 'session_id': 'literal-schema-validator-input',
        **{key: binding[key] for key in ('job_id', 'workflow_id', 'attempt_id', 'fencing_token')},
        'worker_slot': 'slot1', 'worker_account': 'Worker1', 'provider': 'codex',
        'requested_model': 'gpt-6-astra', 'requested_effort': 'low',
        'route_reservation_id': 'reservation', 'selected_route': dict(binding['route']),
        'route_reservation_sha256': hashlib.sha256(b'reservation').hexdigest(),
        'output_sha256': output_digest(output), 'stdout_sha256': hashlib.sha256(b'raw').hexdigest()}
    return receipt, output, binding, observed


@pytest.mark.parametrize('field,value,reason', [
    ('execution_kind', 'physical-native-envelope-fixture', 'fixture_or_simulation'),
    ('simulation', True, 'fixture_or_simulation'),
    ('execution_kind', None, 'native_subscription_receipt_missing'),
    ('subscription_verified', False, 'native_subscription_receipt_missing'),
    ('process_identity_source', 'model_assertion', 'native_subscription_receipt_missing'),
    ('exit_code', False, 'native_subscription_receipt_missing'),
    ('pid', 124, 'native_process_identity_mismatch'),
    ('process_creation_time', 1., 'native_process_identity_mismatch'),
    ('process_creation_filetime', 133444736000000001, 'native_process_identity_mismatch'),
    ('job_id', 'other-job', 'reserved_attempt_or_route_mismatch'),
    ('attempt_id', 'other-attempt', 'reserved_attempt_or_route_mismatch'),
    ('worker_slot', 'slot2', 'reserved_attempt_or_route_mismatch'),
    ('worker_account', 'OtherWorker', 'reserved_attempt_or_route_mismatch'),
    ('provider', 'claude', 'reserved_attempt_or_route_mismatch'),
    ('requested_model', 'gpt-6-sol', 'reserved_attempt_or_route_mismatch'),
    ('requested_effort', 'ultra', 'reserved_attempt_or_route_mismatch'),
    ('fencing_token', True, 'reserved_attempt_or_route_mismatch'),
    ('route_reservation_id', 'other-reservation', 'reserved_attempt_or_route_mismatch'),
    ('output_sha256', '0' * 64, 'native_output_or_reservation_digest_mismatch'),
    ('route_reservation_sha256', '0' * 64, 'native_output_or_reservation_digest_mismatch'),
])
def test_pure_receipt_join_rejects_each_detached_authority(field, value, reason):
    receipt, output, binding, observed = native_receipt_contract_fixture()
    assert _native_receipt_reason(receipt, output, binding, observed) is None
    receipt[field] = value
    assert _native_receipt_reason(receipt, output, binding, observed) == reason


@pytest.mark.parametrize('field', ['matches_configured_worker', 'matches_configured_executable'])
def test_pure_receipt_join_requires_verified_launch_account_and_executable(field):
    receipt, output, binding, observed = native_receipt_contract_fixture()
    observed[field] = False
    assert _native_receipt_reason(receipt, output, binding, observed) == 'native_launch_identity_unverified'


@pytest.mark.skipif(os.name == 'nt' or not Path('/bin/sh').exists(),
                    reason='Physical POSIX executable mismatch; no Windows identity emulation')
def test_mismatched_physical_executable_latches_even_after_a_later_matching_launch(tmp_path):
    actual = runtime(tmp_path)
    actual.config.providers = {'codex': {'executable': '/bin/sh'}}
    actual.store.submit('Physical mismatch fixture', ['R1'], 1)
    with QueueLaunchObserver(actual, tmp_path / 'identity-mismatch') as observer:
        node = actual.store.claim('controller', worker_slot='slot0')
        # Deliberate callback contract fixture: the real process is Python,
        # while the reviewed executable in this test's config is the shell.
        callback_node = {**node, 'route': {'provider': 'codex'}}
        observer.observe_native_launch(callback_node, 'slot0', os.getpid())
        assert observer.launch_identity_mismatch
        actual.config.providers['codex']['executable'] = sys.executable
        observer.observe_native_launch(callback_node, 'slot0', os.getpid())
        key = (node['job_id'], node['attempt_id'], node['fencing_token'])
        assert observer.native[key]['matches_configured_executable'] is True
        assert observer.report()['coverage']['no_native_launch_identity_mismatch'] is False
        assert observer.report()['native_windows_acceptance'] is False
    actual.store.close()
