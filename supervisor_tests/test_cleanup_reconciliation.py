"""Frozen supervisor reconciliation with real SQLite and explicit proof fixtures.

The native Windows retained-Job proof is tested separately on Windows. These
tests do not claim that a synthetic receipt establishes actual process death.
"""
import sqlite3

import pytest

from cochem_pipeline.routing import RoutingPolicy
from cochem_pipeline.store import JobStore
from cochem_supervisor.cleanup import reconcile_stopped_containment


SCOPE = 'a' * 32


def receipt(**changes):
    return {'stopped_containment_id': SCOPE, 'stopped_boot_id': 100,
            'tree_exit_verified': True, 'scheduling_disabled': True, **changes}


def guarded(store, scope=SCOPE, boot=100, slot='worker'):
    workflow = store.submit('Write a note', ['REQ-1'], 1)
    node = store.claim('controller', worker_slot=slot, requires_cleanup=True,
                       cleanup_boot_id=boot, containment_id=scope)
    assert node is not None
    return workflow['workflow_id'], node


def test_verified_exact_scope_unblocks_admission_without_resetting_attempts(tmp_path):
    store = JobStore(tmp_path / 'board.db', routing_policy=RoutingPolicy())
    workflow_id, node = guarded(store)
    store.cancel_workflow(workflow_id)
    pending = store.submit('Write another note', ['REQ-2'], 1)
    assert store.claim('next', worker_slot='other') is None
    before = store.get(node['job_id'])
    assert reconcile_stopped_containment(store.path, receipt()) == 1
    after = store.get(node['job_id'])
    assert after == before, 'Cleanup proof cannot change job, route, attempt, failure or budget state'
    dispatched = store.claim('next', worker_slot='other')
    assert dispatched['workflow_id'] == pending['workflow_id']
    assert reconcile_stopped_containment(store.path, receipt()) == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM pipeline_events WHERE event='EXECUTION_CLEANUP_VERIFIED_BY_SUPERVISOR'").fetchone()[0] == 1


@pytest.mark.parametrize('other_scope,other_boot', [('b' * 32, 100), (SCOPE, 101), (None, 100)])
def test_foreign_or_unidentified_execution_is_never_cleared(tmp_path, other_scope, other_boot):
    store = JobStore(tmp_path / 'board.db', routing_policy=RoutingPolicy())
    first, _ = guarded(store)
    second, other = guarded(store, scope=other_scope, boot=other_boot, slot='second')
    store.cancel_workflow(first)
    store.cancel_workflow(second)
    assert reconcile_stopped_containment(store.path, receipt()) == 1
    holds = store.execution_quarantines()
    assert len(holds) == 1 and holds[0]['attempt_id'] == other['attempt_id']
    assert store.claim('next') is None


@pytest.mark.parametrize('changes', [
    {'stopped_containment_id': '../outside'}, {'stopped_boot_id': True},
    {'stopped_boot_id': 0}, {'tree_exit_verified': False},
    {'scheduling_disabled': False}, {'scheduling_disabled': 1},
])
def test_incomplete_or_invalid_native_receipt_cannot_clear_guard(tmp_path, changes):
    store = JobStore(tmp_path / 'board.db', routing_policy=RoutingPolicy())
    workflow, _ = guarded(store)
    store.cancel_workflow(workflow)
    with pytest.raises(ValueError, match='verified native'):
        reconcile_stopped_containment(store.path, receipt(**changes))
    assert len(store.execution_quarantines()) == 1


def test_closed_record_only_proof_does_not_access_or_create_database(tmp_path):
    database = tmp_path / 'absent.db'
    assert reconcile_stopped_containment(database, {'tree_exit_verified': True,
                                                   'scheduling_disabled': True}) == 0
    assert not database.exists()


def test_launcher_actual_final_teardown_can_reconcile_before_scheduler_restart(tmp_path):
    store = JobStore(tmp_path / 'board.db', routing_policy=RoutingPolicy())
    workflow, _ = guarded(store)
    store.cancel_workflow(workflow)
    proof = receipt(scheduling_disabled=False, launcher_exit_verified=True)
    assert reconcile_stopped_containment(store.path, proof) == 1
    assert store.execution_quarantines() == []


def test_old_database_without_guard_schema_is_preserved(tmp_path):
    database = tmp_path / 'old.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE legacy(value)')
    assert reconcile_stopped_containment(database, receipt()) == 0
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == [('legacy',)]


def test_verified_startup_failure_before_database_creation_needs_no_reconciliation(tmp_path):
    database = tmp_path / 'never-created.db'
    assert reconcile_stopped_containment(database, receipt(launcher_exit_verified=True)) == 0
    assert not database.exists()


def test_external_docker_guard_records_owner_death_without_claiming_container_cleanup(tmp_path):
    from pipeline_tests.test_coding_workflow import CodingFixture
    case = CodingFixture(tmp_path)
    case.author()
    node = case.store.claim('contained-controller',worker_slot='docker-slot',requires_cleanup=True,
                            cleanup_boot_id=100,containment_id=SCOPE)
    assert node['kind']=='CODE_TEST'
    case.store.cancel_workflow(case.workflow_id)
    assert reconcile_stopped_containment(case.store.path,receipt()) == 0
    holds=case.store.execution_quarantines()
    assert len(holds)==1 and holds[0]['attempt_id']==node['attempt_id']
    assert case.store.claim('next-controller',worker_slot='other-slot') is None
    with sqlite3.connect(case.store.path) as db:
        proof=db.execute('SELECT job_id,attempt_id,fencing_token,containment_id,boot_id FROM pipeline_execution_owner_stops').fetchone()
        assert proof==(node['job_id'],node['attempt_id'],node['fencing_token'],SCOPE,100)
    assert reconcile_stopped_containment(case.store.path,receipt()) == 0
    with sqlite3.connect(case.store.path) as db:
        assert db.execute("SELECT count(*) FROM pipeline_events WHERE event='EXECUTION_OWNER_STOPPED_BY_SUPERVISOR'").fetchone()[0]==1


def test_external_guard_cannot_use_a_different_controller_or_boot_stop_proof(tmp_path):
    from pipeline_tests.test_coding_workflow import CodingFixture
    case=CodingFixture(tmp_path)
    case.author()
    node=case.store.claim('contained-controller',worker_slot='docker-slot',requires_cleanup=True,
                          cleanup_boot_id=100,containment_id=SCOPE)
    case.store.cancel_workflow(case.workflow_id)
    assert reconcile_stopped_containment(case.store.path,receipt(stopped_containment_id='b'*32))==0
    assert reconcile_stopped_containment(case.store.path,receipt(stopped_boot_id=101))==0
    assert len(case.store.execution_quarantines())==1
    with sqlite3.connect(case.store.path) as db:
        exists=db.execute("SELECT 1 FROM sqlite_schema WHERE name='pipeline_execution_owner_stops'").fetchone()
        assert not exists or db.execute('SELECT count(*) FROM pipeline_execution_owner_stops').fetchone()[0]==0


def test_external_quiescence_rejects_assertions_without_stopped_scheduler():
    from cochem_supervisor.cleanup import quiesce_external_container_runner
    from cochem_pipeline.containers import ContainerCleanupError
    for proof in ({}, {'tree_exit_verified': True}, {'tree_exit_verified': 1, 'scheduling_disabled': True}):
        with pytest.raises(ContainerCleanupError, match='verified stopped'):
            quiesce_external_container_runner(None, stop_receipt=proof)


def test_external_quiescence_requires_actual_windows_for_production_entrypoint():
    import os
    if os.name == 'nt':
        pytest.skip('This check exercises the real non-Windows rejection path')
    from cochem_supervisor.cleanup import ensure_external_containers_quiescent
    from cochem_pipeline.windows import WindowsIsolationError
    with pytest.raises(WindowsIsolationError, match='real Windows SYSTEM'):
        ensure_external_containers_quiescent({}, stop_receipt=receipt())


def test_real_docker_quiescence_drains_registered_active_and_prepared_only(tmp_path):
    import os
    from cochem_pipeline.containers import DockerRunner
    from pipeline_tests.test_containers import policy
    from cochem_supervisor.cleanup import quiesce_external_container_runner
    image = os.environ.get('COCHEM_TEST_DOCKER_IMAGE')
    if not image:
        pytest.skip('Set COCHEM_TEST_DOCKER_IMAGE for physical external-container cleanup')
    owned = DockerRunner(policy(image), tmp_path/'owned')
    foreign = DockerRunner(policy(image), tmp_path/'foreign')
    try:
        owned.prepare_pool(target=2)
        foreign_id = foreign.prepare_pool(target=1)['prepared'][0]
        owned.reserve_attempt('interrupted-test', 'interrupted-attempt')
        before = owned.census()
        assert before['active'] == before['warm'] == 1
        result = quiesce_external_container_runner(owned, stop_receipt=receipt())
        assert result['cleanup_verified'] and result['previous_owned'] == 2
        assert result['remaining_owned'] == result['published_capacity'] == 0
        assert result['prepared_pool_drained'] == 1
        assert owned.census()['owned'] == 0
        assert foreign._json(['inspect', foreign_id])[0]['State']['Running'] is True
        with owned._connect() as database:
            assert database.execute("SELECT count(*) FROM containers WHERE status='REMOVED'").fetchone()[0] == 2
        # Native stop metadata above is an explicit storage fixture. This test
        # proves actual Docker cleanup, not native Windows containment shutdown.
    finally:
        owned.reap_orphans(set(), include_warm=True)
        foreign.reap_orphans(set(), include_warm=True)


def test_real_docker_quiescence_holds_unknown_owner_label_without_deleting_it(tmp_path):
    import os
    import uuid
    from cochem_pipeline.containers import DockerRunner, ContainerCleanupError
    from pipeline_tests.test_containers import policy
    from cochem_supervisor.cleanup import quiesce_external_container_runner
    image = os.environ.get('COCHEM_TEST_DOCKER_IMAGE')
    if not image:
        pytest.skip('Set COCHEM_TEST_DOCKER_IMAGE for physical external ownership verification')
    runner = DockerRunner(policy(image), tmp_path/'owned')
    nonce = uuid.uuid4().hex
    record = {'name': 'cochem-unregistered-' + nonce, 'lease': nonce, 'job_id': 'unknown-object'}
    created = runner._call(runner.create_arguments(record))
    assert created.returncode == 0
    identifier = created.stdout.decode().strip()
    try:
        with pytest.raises(ContainerCleanupError, match='Unknown external Docker ownership'):
            quiesce_external_container_runner(runner, stop_receipt=receipt())
        assert runner._json(['inspect', identifier])[0]['Id'] == identifier
        assert runner.census()['unknown_owned'] == 1
    finally:
        # The test, which created this exact isolated object, owns its cleanup.
        assert runner._call(['rm', '--force', identifier]).returncode == 0
