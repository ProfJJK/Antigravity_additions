"""Real SQLite checks for the historical Signal ZD-8 lease invariant.

Completion payloads reuse the explicitly labelled storage-contract fixtures;
these tests establish database transitions, not native model execution.
"""
import sqlite3

import pytest

from cochem_pipeline.store import JobStore
from pipeline_tests.test_store import finish, manifest
from pipeline_tests.test_coding_workflow import CodingFixture
from pipeline_tests.test_routing_store import submitted, fail


def test_completed_job_clears_lease_and_upgrade_preserves_active_ownership(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    store.submit('Lease cleanup', ['REQ-1'], 1)
    node = store.claim('manifest-owner')
    assert node['lease_owner'] == 'manifest-owner'
    completed = finish(store, node, manifest(1))
    assert completed['lease_owner'] is None
    assert completed['lease_expires_at'] is None
    active = store.claim('chapter-owner')
    output = completed['output']
    # Recreate an actual older completion row, then reopen through migration.
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE pipeline_jobs SET lease_owner=? WHERE job_id=?',
                           ('old-manifest-owner', node['job_id']))
    connection.close()
    reopened = JobStore(store.path)
    migrated = reopened.get(node['job_id'])
    assert migrated['lease_owner'] is None
    assert migrated['lease_expires_at'] is None
    assert migrated['output'] == output
    assert migrated['attempt_id'] == node['attempt_id']
    assert migrated['fencing_token'] == node['fencing_token']
    current = reopened.get(active['job_id'])
    assert current['lease_owner'] == active['lease_owner']
    assert current['lease_expires_at'] == active['lease_expires_at']


def test_coding_completion_and_failed_sibling_reviews_clear_lease_owners(tmp_path):
    case = CodingFixture(tmp_path)
    authored = case.author()
    completed = case.store.get(authored['job_id'])
    assert completed['status'] == 'COMPLETED'
    assert completed['lease_owner'] is None
    assert completed['lease_expires_at'] is None
    case.test(passed=False)
    case.edit()
    case.test(passed=True)
    first = case.claim('CODE_REVIEW')
    second = case.store.claim('second-review-owner', worker_slot='worker-two')
    assert second is not None and second['kind'] == 'CODE_REVIEW'
    assert second['lease_owner'] == 'second-review-owner'
    assert case.store.fail(first['job_id'], first['attempt_id'], first['fencing_token'],
                           'Reviewer process failed', retry=True, category='code')
    for node in (first, second):
        failed = case.store.get(node['job_id'])
        assert failed['status'] == 'FAILED'
        assert failed['lease_owner'] is None
        assert failed['lease_expires_at'] is None
    with case.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM pipeline_jobs WHERE status<>'IN_PROGRESS' "
                                  'AND (lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL)').fetchone()[0] == 0


def test_poison_pill_is_terminal_blocked_and_cannot_refund_failure_budget(tmp_path):
    store, workflow_id, job_id = submitted(tmp_path)
    for number in range(1, 4):
        node = store.claim('ordinary-worker', worker_slot='isolated')
        assert node is not None
        assert fail(store,node,'code')
        current = store.get(job_id)
        assert current['routing']['failure_count'] == number
        assert current['status'] == ('BLOCKED' if number == 3 else 'PENDING_RETRY')
    assert current['routing']['state'] == 'BLOCKED'
    assert current['routing']['wait_reason'] == 'poison_pill'
    assert current['lease_owner'] is None and current['lease_expires_at'] is None
    assert store.routing_status()['active_reservations'] == []
    reopened = JobStore(store.path,max_attempts=10,routing_policy=store.routing_policy)
    assert reopened.workflow(workflow_id)['status'] == 'BLOCKED'
    assert reopened.get(job_id)['max_attempts'] == 3
    assert reopened.claim('fourth-attempt',worker_slot='isolated') is None
    with pytest.raises(ValueError):
        reopened.resume_routing(job_id,'Do not reset a poison-pill budget')
    assert reopened.get(job_id)['routing']['failure_count'] == 3
    assert reopened.heartbeat(job_id,node['attempt_id'],node['fencing_token']) is False
    # An explicit cancellation remains a distinct terminal FAILED operation.
    reopened.cancel_workflow(workflow_id)
    assert reopened.workflow(workflow_id)['status'] == 'FAILED'


def test_coding_controller_poison_pill_cannot_use_operator_resume(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    for failure in range(3):
        node = case.claim('CODE_TEST')
        assert case.store.fail(node['job_id'],node['attempt_id'],node['fencing_token'],
                               'Controller execution failed',retry=True,category='code')
        if failure < 2:
            # No-delay retries must not compare Python's sub-millisecond clock
            # with SQLite's rounded clock and intermittently hold the claim.
            with case.store._write() as connection:
                retry = connection.execute('SELECT failures,next_eligible_at FROM coding_controller_retries WHERE job_id=?',
                                           (node['job_id'],)).fetchone()
                assert tuple(retry) == (failure+1,0)
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'BLOCKED'
    assert case.state()['status'] == 'POISON_PILL'
    with pytest.raises(ValueError,match='budgets are final'):
        case.store.resume_coding(case.workflow_id,'Must not refund execution failures')
    assert case.store.claim('fourth-controller-attempt',worker_slot='worker-one') is None
