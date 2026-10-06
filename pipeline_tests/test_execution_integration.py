"""Physical SQLite/host-probe integration, not native worker or Docker claims.

Clock-controlled database records exercise supervisor policy without changing
the clock or replacing probe functions. Native process teardown and actual
container execution remain separate Windows/Docker acceptance requirements.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import pytest

from cochem_pipeline.heartbeat import Heartbeat
from cochem_pipeline.resource_telemetry import collect_resources
from cochem_pipeline.store import JobStore
from cochem_supervisor.monitor import read_observation
from cochem_pipeline.admission import JointAdmission
from cochem_pipeline.container_policy import DockerPolicy
from cochem_pipeline.containers import DockerRunner,ContainerCapacityError


def registry_policy():
    image='sha256:'+'a'*64
    return DockerPolicy.from_dict({'enabled':True,'image':image,'allowed_images':[image],
        'commands':[{'name':'unit','argv':['python','-m','pytest','-q']}]})


def test_stage(store,policy,identifier='controller-contract'):
    """Protected schema fixture, not a model-generated or executed coding plan."""
    with store._write() as db:
        store._insert(db,identifier,identifier,None,'CODE_REQUEST','IN_PROGRESS',{})
        db.execute('INSERT INTO coding_workflows VALUES(?,?)',(identifier,json.dumps({'docker':policy.as_dict()})))
        store._insert(db,identifier+'-test',identifier,identifier,'CODE_TEST','PENDING',{'phase':'contract'})


test_stage.__test__=False


def prepared_board(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    workflow=store.submit('Observe physical scheduler state',['REQ-1'],1)
    job=store.claim('integration-owner',lease_seconds=7200,worker_slot='worker1')
    return store,workflow,job


def observe_live_attempt(tmp_path,store,job,*,age,bound=1800,hardware=None):
    with sqlite3.connect(store.path) as db:
        claimed=db.execute("SELECT max(timestamp) FROM pipeline_events WHERE job_id=? AND event='CLAIMED'",
                           (job['job_id'],)).fetchone()[0]
        now=claimed+age
        # A physical persisted renewal at the observation clock. The immutable
        # CLAIMED event remains unchanged; no monkeypatched clock/Store method.
        db.execute('UPDATE pipeline_jobs SET updated_at=?,lease_expires_at=? WHERE job_id=?',
                   (now,now+60,job['job_id']))
        db.commit()
    Heartbeat(tmp_path,'4.2.5').completed_tick({'hardware':hardware or {'capacity':4,'state':'normal'},
        'active_count':1,'execution_bounds':{'native':bound}},now=now)
    return read_observation(tmp_path,now=now,stall_timeout=600)


def test_fresh_lease_renewals_cannot_hide_a_stalled_attempt_past_actual_native_bound(tmp_path):
    store,_,job=prepared_board(tmp_path)
    result=observe_live_attempt(tmp_path,store,job,age=2000,bound=1800)
    stalls=[item for item in result['incidents'] if 'stalled beyond' in item['summary']]
    assert len(stalls)==1 and stalls[0]['repairable'] is True
    assert stalls[0]['evidence']['idle_seconds']==2000
    assert stalls[0]['evidence']['progress_timeout_seconds']==1830


@pytest.mark.parametrize('age,bound',[(700,1800),(2000,3600),(3629,3600)])
def test_legitimate_long_native_budget_is_not_confused_with_six_hundred_second_queue_timeout(tmp_path,age,bound):
    store,_,job=prepared_board(tmp_path)
    result=observe_live_attempt(tmp_path,store,job,age=age,bound=bound)
    assert not any(item['repairable'] for item in result['incidents'])
    assert result['health']['execution_bounds']=={'native':bound}


def test_measured_resource_hold_overrides_stall_without_charging_paid_repair(tmp_path):
    store,_,job=prepared_board(tmp_path)
    measured=collect_resources([tmp_path],sample_seconds=.01,nvidia_smi=str(tmp_path/'missing-gpu-probe'))
    assert measured['gpus']['available'] is False
    result=observe_live_attempt(tmp_path,store,job,age=2000,
        hardware={'capacity':0,'state':'paused','reasons':['Required GPU/VRAM telemetry is unavailable'],
                  'measurements':measured})
    assert result['health']['repair_hold'] is True
    assert not any(item['repairable'] for item in result['incidents'])


def test_actual_host_sample_is_copied_before_the_atomic_claim_event(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    measured=collect_resources([tmp_path],sample_seconds=.01,nvidia_smi=str(tmp_path/'missing-gpu-probe'))
    sample={'measured_at':measured['measured_at'],'measurements':measured}
    store.set_transition_telemetry(sample)
    # Alter the caller's copy; historical evidence must retain actual readings.
    sample['measurements']['cpu']['count']=-1
    workflow=store.submit('Record real host readings',['REQ-1'],1)
    node=store.claim('sample-owner')
    with sqlite3.connect(store.path) as db:
        details=json.loads(db.execute("SELECT details_json FROM pipeline_events WHERE job_id=? AND event='CLAIMED'",
                                      (node['job_id'],)).fetchone()[0])
    telemetry=details['telemetry']
    assert telemetry['cpu_scope']=='host'
    assert telemetry['host_sample']['measurements']['cpu']['count']>0
    assert telemetry['host_sample']['measurements']['gpus']=={'available':False,'devices':[],
        'source':'nvidia-smi','error':measured['gpus']['error']}
    assert telemetry['sampled_at']==measured['measured_at']
    assert telemetry['native_provider'] is None and telemetry['native_reported_model'] is None
    assert workflow['workflow_id']==node['workflow_id']


def test_aborted_state_change_cannot_leave_detached_transition_telemetry(tmp_path):
    store,_,job=prepared_board(tmp_path)
    with sqlite3.connect(store.path) as db:
        before=db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0]
    with pytest.raises(RuntimeError,match='abort physical transaction'):
        with store._write() as db:
            db.execute("UPDATE pipeline_jobs SET status='FAILED' WHERE job_id=?",(job['job_id'],))
            store._event(db,job,'CONTRACT_ABORTED_TRANSITION')
            raise RuntimeError('abort physical transaction')
    assert store.get(job['job_id'])['status']=='IN_PROGRESS'
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM pipeline_events').fetchone()[0]==before
        assert db.execute("SELECT count(*) FROM pipeline_events WHERE event='CONTRACT_ABORTED_TRANSITION'").fetchone()[0]==0


def test_real_shared_job_board_never_allocates_a_fifth_native_or_controller_stage(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    workflows=[store.submit('Bound live controller work',['REQ-1'],1) for _ in range(4)]
    # Actual controller-stage SQL records exercise the same production atomic
    # claim path; they are not a claim that a Docker sandbox was launched.
    with store._write() as db:
        for index,workflow in enumerate(workflows):
            store._insert(db,f'controller-{index}',workflow['workflow_id'],workflow['workflow_id'],
                          'CODE_TEST','PENDING',{'phase':'integration-contract'})
    def acquire(index):
        return JobStore(store.path).claim(f'owner-{index}',max_workers=64,worker_slot=f'slot-{index}')
    with ThreadPoolExecutor(max_workers=12) as pool:
        allocated=[item for item in pool.map(acquire,range(16)) if item is not None]
    assert len(allocated)==4
    assert len({item['attempt_id'] for item in allocated})==4
    assert len(store.active_jobs())==4


def test_resource_failure_without_physical_cleanup_proof_blocks_all_new_slots_and_paid_repairs(tmp_path):
    store=JobStore(tmp_path/'job_board.db',cleanup_boot_id=1)
    store.submit('First physical admission record',['REQ-1'],1)
    store.submit('Second independent queued request',['REQ-2'],1)
    job=store.claim('owner',worker_slot='slot1',requires_cleanup=True,cleanup_boot_id=1)
    store.fail(job['job_id'],job['attempt_id'],job['fencing_token'],'Host thermal pressure',
               retry=True,category='resource')
    restored=JobStore(store.path,cleanup_boot_id=1)
    assert restored.claim('other-owner',worker_slot='slot2') is None
    Heartbeat(tmp_path,'4.2.5').completed_tick({'hardware':{'capacity':0,'state':'critical',
        'reasons':['CPU temperature exceeds critical threshold']},'active_count':0})
    result=read_observation(tmp_path)
    assert result['health']['execution_cleanup_hold']['sampled_holds']==1
    assert result['health']['repair_hold']
    assert not any(item['repairable'] for item in result['incidents'])


def test_joint_native_admission_counts_every_real_preparing_registry_reservation(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    for index in range(6): store.submit('Queued native job',['REQ-1'],1)
    records=[docker._reserve('_warm_',f'pending-preparation-{index}',preparing=True) for index in range(4)]
    assert admission.claim('owner',worker_slot='slot1') is None
    assert admission.maintenance_requested.is_set()
    assert store.active_jobs()==[]
    # This reservation never invoked Docker: release only that known unstarted
    # metadata record. The remaining three PREPARING rows continue occupying seats.
    docker._set(records[0]['lease'],status='REMOVED')
    node,reservation=admission.claim('owner',worker_slot='slot1')
    assert node['kind']=='MANIFEST_GENERATOR' and reservation is None
    assert len(store.active_jobs())+docker.census()['owned']==4
    assert docker.census()['published_capacity']==3
    assert admission.claim('another-owner',worker_slot='slot2') is None
    with pytest.raises(ContainerCapacityError):
        docker._reserve('_warm_','fifth-physical-seat',preparing=True)


def test_joint_test_claim_transfers_compatible_warm_metadata_to_exact_attempt_without_extra_seat(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    record=docker._reserve('_warm_','contract-warm',preparing=True)
    # WARM metadata is an explicit protocol fixture; no container ID/process
    # exists and this test makes no warm-start timing or isolation claim.
    docker._set(record['lease'],status='WARM')
    test_stage(store,docker.policy)
    node,reservation=admission.claim('test-owner',worker_slot='test-slot')
    assert node['kind']=='CODE_TEST'
    assert reservation['lease']==record['lease']
    assert reservation['attempt_id']==node['attempt_id'] and reservation['job_id']==node['job_id']
    assert reservation['status']=='BOUND'
    assert docker.census()['owned']==1 and docker.census()['active']==1
    assert docker.census()['warm']==0
    assert admission._native_active()==0  # The test lease and its container count once.


def test_concurrent_claims_transfer_at_most_four_prepared_seats_without_counting_test_leases_twice(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    for index in range(4):
        record=docker._reserve('_warm_',f'concurrent-warm-{index}',preparing=True)
        docker._set(record['lease'],status='WARM')
    for index in range(12):
        test_stage(store,docker.policy,f'controller-contract-{index}')
    def acquire(index):
        return admission.claim(f'owner-{index}',worker_slot=f'slot-{index}')
    with ThreadPoolExecutor(max_workers=12) as pool:
        allocated=[result for result in pool.map(acquire,range(12)) if result is not None]
    # Contending ticks are intentionally nonblocking. Complete the remaining
    # ticks sequentially, then check the same physical registries at saturation.
    for index in range(12,16):
        result=acquire(index)
        if result is not None:
            allocated.append(result)
    assert len(allocated)==4
    assert len({reservation['lease'] for _,reservation in allocated})==4
    assert len(store.active_jobs())==4
    assert docker.census()['owned']==4 and docker.census()['active']==4
    assert admission.claim('forbidden-fifth',worker_slot='slot-fifth') is None
    assert admission._native_active()+docker.census()['owned']==4


def test_pressure_publishes_zero_while_slow_maintenance_admission_lock_is_held(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    store.submit('Queued work',['REQ-1'],1)
    admission.lock.acquire()
    try:
        assert admission.update_capacity(0)==0
        assert docker.census()['published_capacity']==0
        assert admission.claim('no-launch',worker_slot='slot') is None
        with pytest.raises(ContainerCapacityError):
            docker._reserve('_warm_','forbidden-after-critical',preparing=True)
    finally:
        admission.lock.release()
    assert store.active_jobs()==[]


def test_unverified_idle_container_cleanup_holds_native_and_controller_admission(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    record=docker._reserve('_warm_','unverified-preparation',preparing=True)
    docker._set(record['lease'],status='QUARANTINED')
    store.submit('Native waiting',['REQ-1'],1)
    test_stage(store,docker.policy)
    assert admission.update_capacity(4)==0
    assert admission.claim('no-safe-seat',worker_slot='slot') is None
    assert store.active_jobs()==[] and docker.census()['published_capacity']==0


def test_unclosed_native_guard_also_forbids_background_warm_reservations(tmp_path):
    store=JobStore(tmp_path/'job_board.db',cleanup_boot_id=1)
    docker=DockerRunner(registry_policy(),tmp_path/'containers')
    admission=JointAdmission(store,docker,4)
    store.submit('Native work',['REQ-1'],1)
    node,_=admission.claim('owner',worker_slot='slot',cleanup_boot_id=1)
    store.fail(node['job_id'],node['attempt_id'],node['fencing_token'],'Resource cancellation awaiting cleanup',
               retry=True,category='resource')
    assert admission.update_capacity(4)==0
    with pytest.raises(ContainerCapacityError): docker._reserve('_warm_','must-wait-for-physical-proof',preparing=True)


def test_changed_operator_container_bounds_hold_captured_test_before_any_container_is_allocated(tmp_path):
    store=JobStore(tmp_path/'job_board.db')
    captured=registry_policy()
    current=DockerPolicy.from_dict({**captured.as_dict(),'memory_mb':2048,'tmpfs_mb':1024})
    docker=DockerRunner(current,tmp_path/'containers')
    test_stage(store,captured)
    admission=JointAdmission(store,docker,4)
    assert admission.claim('owner',worker_slot='slot') is None
    assert docker.census()['owned']==0
    assert store.get('controller-contract-test')['status']=='BLOCKED'
    assert store.execution_quarantines()==[]
