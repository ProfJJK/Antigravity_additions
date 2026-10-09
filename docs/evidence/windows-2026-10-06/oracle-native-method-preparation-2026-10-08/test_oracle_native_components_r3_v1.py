"""Ordinary disposable SQLite/composition tests; no worker, SYSTEM or watcher execution."""
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).with_name('oracle-native-components-r3-v1.py')
spec = importlib.util.spec_from_file_location('oracle_component_draft', SOURCE)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def proof():
    def child(pid):
        return {'pid': pid, 'creation_time_filetime': 133000000000000000 + pid,
                'token_matches_worker': True, 'image_matches_base': True, 'owned_job_member': True}
    return {'schema': 'cochem-oracle-native-components/1',
        'scope': 'isolated_oracle_runtime_trip_method_integration', 'runtime_trip_fencing_verified': True,
        'runtime_constructed': False, 'full_service_acceptance': False,
        'durable_fencing': {'status': 'FAILED', 'fencing_token_advanced': True,
            'stale_completion_rejected_without_mutation': True, 'unrelated_workflow_preserved': True,
            'exact_cleanup_telemetry': True,
            'scope': 'actual_Runtime_trip_and_cancel_methods_on_fresh_real_JobStore'},
        'storm_window': {'event_count': 501, 'first_call_started': 10., 'last_call_completed': 10.5,
                         'elapsed_seconds': .5},
        'callback_count': 1, 'callback_async': True, 'tripped_task': True,
        'no_tripped_context': True, 'unrelated_context_preserved': True, 'watcher_stopped': True,
        'native_cleanup_verified': True, 'parent_exit_verified': True, 'grandchild_exit_verified': True,
        'selected_identity_reacquired': True, 'job_active_processes': 0,
        'parent': child(100), 'grandchild': child(101), 'trip_started_at': 10.4,
        'cleanup_completed_at': 10.8, 'durable_trip_completed_at': 10.85, 'identity_reacquired_at': 10.9}


def test_complete_proof_is_component_only():
    report = proof()
    assert M.validate_result(report)
    report['full_service_acceptance'] = True
    with pytest.raises(ValueError, match='OVERCLAIM'):
        M.validate_result(report)


@pytest.mark.parametrize('field', ['callback_async', 'tripped_task', 'no_tripped_context',
    'unrelated_context_preserved', 'watcher_stopped', 'native_cleanup_verified',
    'parent_exit_verified', 'grandchild_exit_verified', 'selected_identity_reacquired'])
def test_missing_physical_proof_is_not_pass(field):
    report = proof()
    report[field] = False
    with pytest.raises(ValueError):
        M.validate_result(report)


@pytest.mark.parametrize('change', ['under_threshold', 'at_one_second', 'nan', 'duplicate_pid',
    'wrong_creation', 'missing_job_membership', 'extra_callback', 'active_descendant', 'release_before_cleanup',
    'missing_durable_fencing', 'wrong_disposition', 'stale_completion_not_rejected'])
def test_unsafe_or_inconsistent_native_receipts_are_rejected(change):
    report = proof()
    if change == 'under_threshold': report['storm_window']['event_count'] = 500
    if change == 'at_one_second': report['storm_window'].update(last_call_completed=11., elapsed_seconds=1.)
    if change == 'nan': report['storm_window']['elapsed_seconds'] = float('nan')
    if change == 'duplicate_pid': report['grandchild']['pid'] = report['parent']['pid']
    if change == 'wrong_creation': report['parent']['creation_time_filetime'] = 0
    if change == 'missing_job_membership': report['grandchild']['owned_job_member'] = False
    if change == 'extra_callback': report['callback_count'] = 2
    if change == 'active_descendant': report['job_active_processes'] = 1
    if change == 'release_before_cleanup': report['identity_reacquired_at'] = 10.7
    if change == 'missing_durable_fencing': report.pop('durable_fencing')
    if change == 'wrong_disposition': report['durable_fencing']['status'] = 'PENDING_RETRY'
    if change == 'stale_completion_not_rejected': report['durable_fencing']['stale_completion_rejected_without_mutation'] = False
    with pytest.raises(ValueError):
        M.validate_result(report)


def test_501_complete_call_intervals_must_fit_real_rolling_second():
    witness = M.EventWitness()
    for index in range(500): witness.append(index / 1000, index / 1000 + .0001)
    with pytest.raises(ValueError, match='THRESHOLD'):
        witness.storm_window()
    witness.append(.5, .5001)
    assert witness.storm_window()['event_count'] == 501
    assert witness.storm_window()['elapsed_seconds'] == .5001


def test_slow_events_cannot_be_counted_as_storm_and_old_prefix_expires():
    witness = M.EventWitness()
    for index in range(501): witness.append(index / 400, index / 400 + .0001)
    with pytest.raises(ValueError, match='THRESHOLD'):
        witness.storm_window()
    for index in range(501): witness.append(5 + index / 2000, 5 + index / 2000 + .0001)
    assert witness.storm_window()['first_call_started'] >= 5


@pytest.mark.parametrize('values', [(1., .9), (float('nan'), 2.), (False, 1.), (-1., 0.)])
def test_event_clock_invalid_is_not_accepted(values):
    with pytest.raises(ValueError):
        M.EventWitness().append(*values)


def test_event_bound_and_clock_reversal_fail_closed(monkeypatch):
    monkeypatch.setattr(M, 'MAX_EVENTS', 2)
    witness = M.EventWitness()
    witness.append(1., 1.1)
    with pytest.raises(ValueError, match='ORDER'): witness.append(1.05, 1.2)
    witness.append(1.2, 1.3)
    with pytest.raises(ValueError, match='COUNT_BOUND'): witness.append(1.4, 1.5)


def test_candidate_binds_actual_installed_component_sources_without_importing_them():
    for module, digest in M.MODULE_PINS.items():
        path = M.INSTALL / '.venv/Lib/site-packages/cochem_pipeline' / (module + '.py')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_no_operational_cli_or_fabricated_oracle_clock_in_candidate():
    tree = ast.parse(SOURCE.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(isinstance(call.func, ast.Name) and call.func.id == '_component_core' for call in calls)
    assert not any(isinstance(call.func, ast.Attribute) and call.func.attr == 'record'
        and any(keyword.arg == 'now' for keyword in call.keywords) for call in calls)
    assert not any(isinstance(call.func, ast.Name) and call.func.id == 'Runtime' for call in calls)


def test_fixture_cannot_dispatch_from_unprotected_test_location():
    # Importing then calling only the refusal path does not spawn a process or
    # read a credential. This is not a physical worker acceptance test.
    path = SOURCE.with_name('oracle-rogue-fixture-r3-v1.py')
    spec = importlib.util.spec_from_file_location('inert_rogue_refusal', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 2


@pytest.fixture
def isolated_trip(tmp_path, monkeypatch):
    # Only installed r3 methods are imported. Native boundaries below are inert;
    # all actual SQLite writes stay in pytest's new disposable directory.
    monkeypatch.syspath_prepend(str(M.INSTALL / '.venv/Lib/site-packages'))
    from cochem_pipeline.runtime import Runtime
    from cochem_pipeline.store import JobStore
    import cochem_pipeline.runtime as runtime_module
    assert hashlib.sha256(Path(runtime_module.__file__).read_bytes()).hexdigest() == M.MODULE_PINS['runtime']
    monkeypatch.setattr(M, 'PRIVATE', tmp_path)
    store = JobStore(tmp_path / 'job_board.db', cleanup_boot_id=1234)
    store.submit('Disposable reaper method fixture', ['REQ-1'], 1, workflow_id='rogue-workflow')
    node = store.claim('test-controller', worker_slot='slot1', requires_cleanup=True,
                       cleanup_boot_id=1234, containment_id='1' * 32)
    assert node is not None
    store.submit('Unaffected fixture workflow', ['REQ-1'], 1, workflow_id='unrelated-workflow')
    invoked = threading.Event()
    calls = []
    def terminate(job_id):
        calls.append(job_id)
        invoked.set()
    runner = SimpleNamespace(terminate=terminate)
    cleaned = threading.Event()
    fixture = M.IsolatedRuntimeTrip(store, runner, node, 'unrelated-workflow', cleaned)
    assert fixture.trip.__func__ is Runtime.trip and fixture.cancel.__func__ is Runtime.cancel
    try:
        yield fixture, store, node, invoked, calls, cleaned
    finally:
        # Test threads must already have joined; this is not process cleanup.
        cleaned.set()
        store.close()


def test_actual_trip_method_waits_for_physical_proof_then_durably_fences_and_rejects_stale(isolated_trip):
    fixture, store, node, invoked, calls, cleaned = isolated_trip
    errors = []
    def invoke():
        try:
            fixture.trip(node['job_id'])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        assert invoked.wait(3)
        assert store.get(node['job_id'])['status'] == 'IN_PROGRESS'
        # A live, correctly leased execution guard is intentionally omitted by
        # execution_quarantines(); inspect only this disposable DB's exact row.
        with store._connection() as db:
            guard = db.execute('SELECT worker_slot, cleared_at FROM pipeline_execution_cleanup '
                               'WHERE job_id=? AND attempt_id=? AND fencing_token=?',
                               (node['job_id'], node['attempt_id'], node['fencing_token'])).fetchone()
        assert guard is not None and guard['worker_slot'] == 'slot1' and guard['cleared_at'] is None
        with pytest.raises(ValueError, match='PHYSICAL_CLEANUP_REQUIRED'):
            fixture.confirm_physical_cleanup({'native_cleanup_verified': True})
        assert not cleaned.is_set()
        fixture.confirm_physical_cleanup(proof())
        thread.join(5)
        assert not thread.is_alive() and not errors
        result = fixture.verify_durable_result()
        assert result['status'] == 'FAILED'
        assert result['stale_completion_rejected_without_mutation'] is True
        assert result['unrelated_workflow_preserved'] is True
        assert calls and set(calls) == {node['job_id']}
        assert store.get(node['job_id'])['attempt_id'] is None
    finally:
        cleaned.set()
        thread.join(5)


def test_actual_trip_cleanup_uncertainty_quarantines_and_does_not_pass(isolated_trip):
    fixture, store, node, _, _, _ = isolated_trip
    # Avoid an artificial 30-second test sleep. This fake wait is an explicit
    # native-cleanup failure boundary, not physical evidence.
    fixture.active[node['job_id']]['cleaned'] = SimpleNamespace(wait=lambda timeout: False)
    fixture.trip(node['job_id'])
    assert store.get(node['job_id'])['status'] == 'FAILED'
    assert fixture.quarantined == {'slot1': 'Circuit-breaker cleanup could not be verified'}
    assert len(store.execution_quarantines()) == 1
    with pytest.raises(ValueError, match='CLEANUP_GUARD_REMAINS'):
        fixture.verify_durable_result()


def test_actual_oracle_slot_monitor_asynchronously_reaches_actual_trip_on_disposable_store(isolated_trip, tmp_path, monkeypatch):
    # Deliberately synthetic event objects and physical-proof boundary: this
    # ordinary test verifies the composition, never claims native watcher/Job
    # acceptance. Oracle timestamps and its asynchronous callback are real.
    from cochem_pipeline.oracle import ContextEngine, Oracle, Rule
    import cochem_pipeline.oracle as oracle_module
    from cochem_pipeline.runtime import SlotMonitor
    fixture, store, node, invoked, _, cleaned = isolated_trip
    finished = threading.Event()
    callback_threads, failures = [], []
    def callback(job_id):
        try:
            callback_threads.append(threading.get_ident())
            fixture.trip(job_id)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()
    engine = ContextEngine([Rule('fixture-core', 'Fixed fixture rule.', (), core=True)], 4096)
    # Only tracking-file custody is substituted for an ordinary disposable test.
    # The installed SYSTEM guard remains unchanged and is never invoked through
    # an operational entry point. All Oracle logic/SQLite/callbacks below are real.
    tracking = tmp_path / 'tracking.db'
    def disposable_tracking_path(path):
        assert path == tracking and path.parent == tmp_path and not path.exists()
        return path
    monkeypatch.setattr(oracle_module, '_protect_tracking_path', disposable_tracking_path)
    try:
        with Oracle(engine, tracking, callback) as oracle:
            monitor = SlotMonitor(oracle, node['job_id'])
            for index in range(501):
                monitor.on_any_event(SimpleNamespace(event_type='created', src_path='inert-event-' + str(index)))
            assert invoked.wait(5)
            assert not finished.is_set()
            assert oracle.tripped_tasks == frozenset((node['job_id'],))
            assert not oracle.has_pending(node['job_id'])
            assert store.get(node['job_id'])['status'] == 'IN_PROGRESS'
            fixture.confirm_physical_cleanup(proof())
            assert finished.wait(5) and not failures
            assert callback_threads and callback_threads == [callback_threads[0]]
            assert callback_threads[0] != threading.get_ident()
            assert fixture.verify_durable_result()['stale_completion_rejected_without_mutation'] is True
    finally:
        cleaned.set()


def test_fixture_store_cannot_select_production_database(isolated_trip):
    fixture, _, node, _, _, cleaned = isolated_trip
    false_store = SimpleNamespace(path=r'C:\ProgramData\CoChemPipeline427\private\job_board.db')
    with pytest.raises(ValueError, match='ISOLATED_JOBSTORE_PATH'):
        M.IsolatedRuntimeTrip(false_store, fixture.runner, node, 'unrelated', cleaned)


def test_limits_bind_to_actual_r3_default_contract_and_reject_unverified_observation(monkeypatch):
    monkeypatch.syspath_prepend(str(M.INSTALL / '.venv/Lib/site-packages'))
    from cochem_pipeline.resource_limits import ResourceLimits
    limits = ResourceLimits()
    observed = {**limits.as_dict(), 'native_limits_verified': True, 'kill_on_job_close': True,
                'topology_verified': True, 'affinity_mask': 65536}
    M.validate_launch_limits(limits, observed)
    for key, value in [('native_limits_verified', False), ('memory_limit_mb', 4096), ('topology_verified', False)]:
        bad = {**observed, key: value}
        with pytest.raises(ValueError):
            M.validate_launch_limits(limits, bad)


def test_fixture_acl_must_establish_actual_worker_or_universal_read_grant():
    sid = 'S-1-5-21-100-100-100-1000'
    win = SimpleNamespace(SYSTEM_SID='S-1-5-18', ADMIN_SID='S-1-5-32-544', TRUSTED_INSTALLER_SID='S-1-5-80-1',
        validate_code_path=lambda path: None,
        _acl=lambda path: ('S-1-5-18', True, [(sid, 0x1200a9, 0)]))
    M.require_fixture_read_access(win, M.ROOT / 'fixture.py', sid)
    win._acl = lambda path: ('S-1-5-18', True, [('S-1-5-32-545', 0x1200a9, 0)])
    with pytest.raises(ValueError, match='FIXTURE_WORKER_READ_NOT_PROVEN'):
        M.require_fixture_read_access(win, M.ROOT / 'fixture.py', sid)
