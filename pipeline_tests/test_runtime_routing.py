"""Real SQLite/controller integration, without constructing a fake Windows host.

These tests call the failure-reporting controller method on actual Store claims.
They do not launch providers, attest subscriptions, or emulate Win32 security.
"""
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline.failures import ProviderFailure
from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.runtime import Runtime
from cochem_pipeline.store import JobStore
from cochem_pipeline.worker import ExecutionRevokedError, WorkerCleanupError


def controller(tmp_path,max_attempts=2,policy=None):
    policy = policy or load_routing_policy()
    store = JobStore(tmp_path/'jobs.db',max_attempts=max_attempts,routing_policy=policy)
    workflow = store.submit('Write one documented chapter',['REQ-1'],1)
    runtime = Runtime.__new__(Runtime)
    runtime.config = SimpleNamespace(job_db=store.path,max_attempts=max_attempts,routing=policy)
    runtime.store = store
    runtime.lock = threading.RLock()
    runtime.active = {}
    runtime.quarantined = {}
    runtime.stop_event = threading.Event()
    runtime.boot_id = 101
    runtime.containment_id = 'a'*32
    return runtime,workflow


def claim(runtime,requires_cleanup=False,lease_seconds=60):
    node = runtime.store.claim('runtime-contract',worker_slot='slot1',lease_seconds=lease_seconds,
                               requires_cleanup=requires_cleanup,cleanup_boot_id=runtime.boot_id,
                               containment_id=runtime.containment_id)
    assert node is not None
    runtime.active[node['job_id']] = {'node':node,'slot':'slot1','tripped':False}
    return node


def test_default_lease_and_renewal_each_retain_thirty_minutes(tmp_path,monkeypatch):
    from cochem_pipeline import routing_store, store as store_module
    clock = [1900000000.]
    measured = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(routing_store,'time',measured)
    monkeypatch.setattr(store_module,'time',measured)
    runtime,_ = controller(tmp_path)
    node = runtime.store.claim('lease-contract',worker_slot='slot1')
    assert node['lease_expires_at']==clock[0]+1800
    clock[0] += 5
    assert runtime.store.heartbeat(node['job_id'],node['attempt_id'],node['fencing_token'])
    assert runtime.store.get(node['job_id'])['lease_expires_at']==clock[0]+1800


def test_sqlite_lease_maximum_cannot_be_bypassed_by_direct_claim_or_renewal(tmp_path,monkeypatch):
    from cochem_pipeline import routing_store, store as store_module
    clock=[1900000000.]
    measured=SimpleNamespace(time=lambda:clock[0])
    monkeypatch.setattr(routing_store,'time',measured)
    monkeypatch.setattr(store_module,'time',measured)
    runtime,_=controller(tmp_path)
    with pytest.raises(ValueError,match='at most 3600'):
        runtime.store.claim('too-long',worker_slot='slot1',lease_seconds=3600.001)
    node=runtime.store.claim('maximum',worker_slot='slot1',lease_seconds=3600)
    expires=node['lease_expires_at']
    assert expires==clock[0]+3600
    with pytest.raises(ValueError,match='at most 3600'):
        runtime.store.heartbeat(node['job_id'],node['attempt_id'],node['fencing_token'],3600.001)
    assert runtime.store.get(node['job_id'])['lease_expires_at']==expires
    clock[0]=expires
    assert not runtime.store.heartbeat(node['job_id'],node['attempt_id'],node['fencing_token'],3600)
    replacement=runtime.store.claim('after-expiry',worker_slot='slot1',lease_seconds=3600)
    assert replacement['fencing_token']>node['fencing_token']
    assert replacement['lease_expires_at']==expires+3600


def test_quota_and_auth_advance_routes_without_spending_task_failure_budget(tmp_path):
    runtime,_ = controller(tmp_path,max_attempts=1)
    first = claim(runtime)
    assert runtime._record_failure(first,'slot1',ProviderFailure('quota',12))
    second = claim(runtime)
    assert second['route']['candidate_index'] == first['route']['candidate_index']+1
    assert second['attempt_id'] != first['attempt_id']
    assert second['fencing_token'] > first['fencing_token']
    assert second['worker_slot'] == first['worker_slot'] == 'slot1'
    assert runtime._record_failure(second,'slot1',ProviderFailure('auth'))
    third = claim(runtime)
    assert third['routing']['failure_count'] == 0
    assert third['route']['candidate_index'] == 2
    holds = runtime.store.routing_status()['holds']
    assert {entry['scope'] for entry in holds} == {'pool'}
    assert {entry['reason'] for entry in holds} == {'quota','auth'}
    with sqlite3.connect(runtime.store.path) as connection:
        details = [json.loads(row[0]) for row in connection.execute('SELECT details FROM recovery_telemetry')]
    assert [item['category'] for item in details] == ['quota','auth']
    assert all(item['action']=='provider_unavailable' for item in details)


def test_many_dispatches_do_not_replace_the_ordinary_failure_counter(tmp_path):
    runtime,_ = controller(tmp_path,max_attempts=2)
    for category in ('quota','auth'):
        node = claim(runtime)
        assert runtime._record_failure(node,'slot1',ProviderFailure(category))
    third = claim(runtime)
    assert third['fencing_token'] >= runtime.config.max_attempts
    assert runtime._record_failure(third,'slot1',ProviderFailure('code'))
    fourth = claim(runtime)
    assert fourth['routing']['failure_count'] == 1
    assert fourth['route']['candidate_index'] == third['route']['candidate_index']
    assert runtime._record_failure(fourth,'slot1',ProviderFailure('protocol'))
    assert runtime.store.get(fourth['job_id'])['status'] == 'BLOCKED'
    assert runtime.store.claim('after-task-budget',worker_slot='slot1') is None


@pytest.mark.parametrize('failure', [TimeoutError('Native deadline exceeded'), ProviderFailure('protocol')])
def test_transient_native_failures_wait_durably_until_exact_deadline(tmp_path,monkeypatch,failure):
    """Exercise real SQLite claims across restarts without sleeping for retry delays."""
    from cochem_pipeline import routing_store, store as store_module
    clock = [1900000000.]
    measured = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(routing_store,'time',measured)
    monkeypatch.setattr(store_module,'time',measured)
    policy = load_routing_policy({'backoff_base_seconds':3,'backoff_max_seconds':5,
                                  'backoff_jitter_fraction':0})
    runtime,workflow = controller(tmp_path,max_attempts=4,policy=policy)
    node = claim(runtime,requires_cleanup=True)
    preferred = node['route']['key']
    for failures,delay in enumerate((3,5,5),start=1):
        assert runtime._record_failure(node,'slot1',failure)
        waiting = runtime.store.get(node['job_id'])
        assert waiting['status']=='PENDING_RETRY'
        assert waiting['routing']['state']=='WAITING'
        assert waiting['routing']['next_eligible_at']==clock[0]+delay
        assert waiting['routing']['failure_count']==failures
        assert waiting['routing']['cursor']==0 and waiting['routing']['cycle']==0
        assert waiting['lease_expires_at'] is None and waiting['lease_owner'] is None
        assert runtime.store.routing_status()['active_reservations']==[]
        assert runtime.store.routing_status()['holds']==[]
        assert runtime.store.claim('too-soon',worker_slot='slot1') is None
        runtime.store = JobStore(runtime.store.path,max_attempts=4,routing_policy=policy)
        assert runtime.store.get(node['job_id'])['routing']==waiting['routing']
        clock[0] += delay-.001
        assert runtime.store.claim('before-boundary',worker_slot='slot1') is None
        clock[0] = waiting['routing']['next_eligible_at']
        previous = node
        node = claim(runtime,requires_cleanup=True)
        assert node['route']['key']==preferred
        assert node['attempt_id']!=previous['attempt_id']
        assert node['fencing_token']>previous['fencing_token']
    assert runtime._record_failure(node,'slot1',failure)
    final = runtime.store.get(node['job_id'])
    assert final['status']=='BLOCKED' and final['routing']['failure_count']==4
    assert runtime.store.workflow(workflow['workflow_id'])['root']['status']=='BLOCKED'
    assert runtime.store.claim('budget-exhausted',worker_slot='slot1') is None
    retries = [event for event in runtime.store.events(workflow['workflow_id'])
               if event['event']=='TASK_RETRY_BACKOFF']
    assert [event['details']['delay_seconds'] for event in retries]==[3,5,5]


@pytest.mark.parametrize('rejection',['chapter_schema','synthesis_hash'])
def test_valid_json_with_invalid_artifacts_still_consumes_code_failure_budget(tmp_path,rejection):
    from cochem_pipeline.store import output_digest
    from pipeline_tests.test_routing_store import completion
    from pipeline_tests.test_store import chapter, manifest
    runtime,workflow = controller(tmp_path,max_attempts=2)

    def complete(node,output):
        _,receipt = completion(node)
        receipt['output_sha256'] = output_digest(output)
        return runtime.store.complete(node['job_id'],node['attempt_id'],node['fencing_token'],output,receipt)

    complete(claim(runtime),manifest(1))
    node = claim(runtime)
    output = chapter(node)
    if rejection=='chapter_schema':
        output['wbs_tasks_defined'] = []
    else:
        complete(node,output)
        node = claim(runtime)
        output = {'artifact_text':'Invalid synthesized document',
                  'chapter_hashes':{name:'f'*64 for name in node['payload']['chapter_hashes']}}
    with pytest.raises(ValueError) as rejected:
        complete(node,output)
    assert runtime._record_failure(node,'slot1',rejected.value)
    pending = runtime.store.get(node['job_id'])
    assert pending['output'] is None
    assert pending['routing']['failure_count']==1
    assert pending['routing']['state']=='READY'
    assert pending['routing']['cycle']==0 and pending['routing']['next_eligible_at']==0
    assert runtime.store.routing_status()['holds']==[]
    with sqlite3.connect(runtime.store.path) as connection:
        latest = json.loads(connection.execute('SELECT details FROM recovery_telemetry ORDER BY id DESC LIMIT 1').fetchone()[0])
    assert latest['category']=='code'
    assert runtime.store.workflow(workflow['workflow_id'])['status']=='IN_PROGRESS'


def test_context_failure_rotates_only_this_job_without_global_provider_hold(tmp_path):
    runtime,_ = controller(tmp_path,max_attempts=1)
    node = claim(runtime)
    assert runtime._record_failure(node,'slot1',ProviderFailure('context'))
    assert runtime.store.routing_status()['holds'] == []
    following = claim(runtime)
    assert following['route']['candidate_index'] == 1
    assert following['routing']['failure_count'] == 0


@pytest.mark.parametrize('category',['configuration','compatibility'])
def test_configuration_and_capability_holds_require_operator_resume(tmp_path,category):
    runtime,_ = controller(tmp_path,max_attempts=1)
    node = claim(runtime)
    assert runtime._record_failure(node,'slot1',ProviderFailure(category))
    held = runtime.store.get(node['job_id'])
    assert held['status'] == 'BLOCKED'
    assert held['routing']['state'] == 'BLOCKED'
    assert held['routing']['failure_count'] == 0
    assert runtime.store.claim('cannot-spend-another-dispatch',worker_slot='slot1') is None
    runtime.store.resume_routing(node['job_id'],'Contract test: operator restored the configured native capability')
    resumed = claim(runtime)
    assert resumed['route']['candidate_index'] == node['route']['candidate_index']
    assert resumed['route']['policy_digest'] == node['route']['policy_digest']
    assert resumed['attempt_id'] != node['attempt_id']


def test_cancelled_workflow_rejects_late_failure_without_resubmission(tmp_path):
    runtime,workflow = controller(tmp_path)
    node = claim(runtime)
    runtime.store.cancel_workflow(workflow['workflow_id'])
    runtime.active[node['job_id']]['tripped'] = True
    before = runtime.store.workflow(workflow['workflow_id'])
    assert runtime._record_failure(node,'slot1',ProviderFailure('busy')) is False
    after = runtime.store.workflow(workflow['workflow_id'])
    assert {key:value for key,value in after.items() if key!='events'} == {key:value for key,value in before.items() if key!='events'}
    assert all(event['event']=='EXECUTION_CLEANUP_CONFIRMED' for event in after['events'][len(before['events']):])
    assert runtime.store.claim('cancelled-work',worker_slot='slot1') is None
    assert runtime.store.routing_status()['active_reservations'] == []


@pytest.mark.parametrize('interruption',['lease_revoked','shutdown','breaker','cleanup'])
def test_interruption_and_quarantine_never_request_another_dispatch(tmp_path,interruption):
    runtime,_ = controller(tmp_path)
    node = claim(runtime)
    failure = ProviderFailure('busy')
    if interruption=='lease_revoked':
        failure = ExecutionRevokedError('Controller revoked this execution')
    elif interruption=='shutdown':
        runtime.stop_event.set()
    elif interruption=='breaker':
        runtime.active[node['job_id']]['tripped'] = True
    else:
        failure = WorkerCleanupError('Native process teardown remains unverified')
    assert runtime._record_failure(node,'slot1',failure)
    assert runtime.store.get(node['job_id'])['status'] == 'FAILED'
    assert runtime.store.claim('interrupted-work',worker_slot='slot1') is None
    assert ('slot1' in runtime.quarantined) == (interruption=='cleanup')


def test_unverified_native_cleanup_blocks_all_new_admission_across_restart(tmp_path):
    runtime,_ = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True)
    runtime.store.submit('Another independent workflow',['REQ-2'],1)
    assert runtime._record_failure(node,'slot1',WorkerCleanupError('Native descendant exit is unverified'))
    assert runtime.store.claim('another-slot',worker_slot='slot2') is None
    restarted = JobStore(runtime.store.path,routing_policy=runtime.config.routing,cleanup_boot_id=runtime.boot_id)
    barriers = restarted.execution_quarantines()
    assert len(barriers) == 1
    assert barriers[0]['attempt_id'] == node['attempt_id']
    assert barriers[0]['boot_id'] == runtime.boot_id
    assert barriers[0]['containment_id'] == runtime.containment_id
    assert restarted.claim('new-daemon',worker_slot='slot2') is None
    assert restarted.recover_execution_quarantines_after_boot(runtime.boot_id) == []
    # This is a SQLite policy fixture, not a claim that Linux established a
    # Windows boot change. Production obtains this input from SYSTEM CIM.
    assert len(restarted.recover_execution_quarantines_after_boot(runtime.boot_id+1)) == 1
    assert restarted.claim('after-proven-new-boot',worker_slot='slot2') is not None


def test_cancel_blocks_admission_before_requesting_physical_termination(tmp_path):
    runtime,workflow = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True)
    runtime.store.submit('Separate queued workflow',['REQ-2'],1)
    observations = []
    def observe_termination_order(job_id):
        observations.append(job_id)
        assert runtime.store.claim('while-termination-is-pending',worker_slot='slot2') is None
    # This observes controller ordering only; it does not emulate an OS kill.
    runtime.runner = SimpleNamespace(terminate=observe_termination_order)
    runtime.cancel(workflow['workflow_id'])
    assert observations == [node['job_id']]
    assert runtime.store.execution_quarantines()
    # No native process was launched in this test; the exact owner acknowledgement
    # exercises the same post-teardown controller path used in production.
    assert runtime._record_failure(node,'slot1',ExecutionRevokedError('Cancelled')) is False
    assert runtime.store.execution_quarantines() == []
    assert runtime.store.claim('after-verified-teardown',worker_slot='slot2') is not None


def test_expired_guard_cannot_dispatch_retry_before_exact_owner_cleanup(tmp_path):
    runtime,_ = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True,lease_seconds=.01)
    time.sleep(.02)
    runtime.store.reap_expired()
    assert runtime.store.claim('lease-is-not-process-exit',worker_slot='slot1') is None
    assert runtime._record_failure(node,'slot1',ExecutionRevokedError('Lease expired after native teardown')) is False
    next_attempt = runtime.store.claim('verified-retry',worker_slot='slot1')
    assert next_attempt is not None
    assert next_attempt['attempt_id'] != node['attempt_id']


def test_normal_finished_process_releases_guard_before_failure_reservation(tmp_path):
    runtime,_ = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True)
    assert runtime._record_failure(node,'slot1',ProviderFailure('context'))
    assert runtime.store.execution_quarantines() == []
    next_attempt = runtime.store.claim('ordinary-fallback',worker_slot='slot1')
    assert next_attempt is not None
    assert next_attempt['route']['candidate_index'] == 1


def test_restart_marks_even_unexpired_prior_native_claim_as_uncertain(tmp_path):
    runtime,_ = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True,lease_seconds=600)
    restarted = JobStore(runtime.store.path,routing_policy=runtime.config.routing,cleanup_boot_id=runtime.boot_id)
    assert restarted.recover_execution_quarantines_after_boot(runtime.boot_id) == []
    barriers = restarted.quarantine_unclosed_executions('New daemon cannot attest old process exit')
    assert [row['attempt_id'] for row in barriers] == [node['attempt_id']]
    assert restarted.claim('new-process-memory-is-not-proof',worker_slot='slot2') is None


def test_fenced_local_attempt_is_promptly_stopped_without_releasing_its_guard(tmp_path):
    runtime,workflow = controller(tmp_path)
    node = claim(runtime,requires_cleanup=True)
    observed = []
    runtime.runner = SimpleNamespace(terminate=observed.append)
    runtime._terminate_fenced_attempts()
    assert observed == []
    runtime.store.cancel_workflow(workflow['workflow_id'])
    runtime._terminate_fenced_attempts()
    assert observed == [node['job_id']]
    assert runtime.store.execution_quarantines()
