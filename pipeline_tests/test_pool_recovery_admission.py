"""Real registry/thread regressions; physical Docker/SYSTEM closure is separate."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cochem_pipeline.admission import JointAdmission
from cochem_pipeline.containers import DockerRunner, ContainerError, ContainerCapacityError
from cochem_pipeline.store import JobStore
from pipeline_tests.test_containers import policy
from pipeline_tests.test_execution_integration import test_stage


def observed_limits(runner):
    p=runner.policy
    return {'State':{'Running':True},'HostConfig':{
        'NetworkMode':'none','ReadonlyRootfs':True,'Memory':p.memory_mb*1048576,
        'MemorySwap':p.memory_mb*1048576,'NanoCpus':int(p.cpus*1e9),'PidsLimit':p.pids_limit,
        'IpcMode':'none','Privileged':False,'CapDrop':['ALL'],
        'SecurityOpt':['no-new-privileges:true'],
        'Tmpfs':{'/work':f'size={p.tmpfs_mb}m','/tmp':f'size={p.tmpfs_mb}m'}},
        'Config':{'User':'1000:1000'},'Mounts':[]}


@pytest.mark.parametrize('drift',['none','memory','daemon','policy','image','stopped','expired','legacy'])
def test_warm_recovery_checks_captured_policy_engine_and_readiness(tmp_path,drift):
    runner=DockerRunner(policy(),tmp_path)
    record=runner._reserve('_warm_','captured',preparing=True)
    record.update(prepared_at=time.time(),preparation_seconds=0.1,creation_daemon_id='engine-1')
    value=observed_limits(runner)
    daemon='engine-1'
    if drift=='memory': value['HostConfig']['Memory']=512*1048576
    if drift=='daemon': daemon='engine-2'
    if drift=='policy': record['policy_digest']='0'*64
    if drift=='image': record['image']='sha256:'+'f'*64
    if drift=='stopped': value['State']['Running']=False
    if drift=='expired': record['deadline']=time.time()-1
    if drift=='legacy': record['pool_policy_json']=None
    if drift=='none':
        runner._reattest_warm(record,value,daemon)
    else:
        with pytest.raises((ValueError,ContainerError)):
            runner._reattest_warm(record,value,daemon)


def test_native_claim_during_slow_cold_preparation_uses_spare_seat(tmp_path,monkeypatch):
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    entered,release=threading.Event(),threading.Event()
    # No actual engine here: block at the cold-operation boundary AFTER a real
    # durable reservation. This verifies lock scope, not Docker latency.
    monkeypatch.setattr(runner,'reap_orphans',lambda **kw:None)
    def slow_prepare(target=None,*,admission_lock=None):
        with admission_lock:
            runner._reserve('_warm_','slow',preparing=True,capacity_limit=target)
        entered.set()
        assert release.wait(10)
        return {'census':runner.census()}
    monkeypatch.setattr(runner,'prepare_pool',slow_prepare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(admission.maintenance)
        try:
            assert entered.wait(5)
            store.submit('Native arrives during cold preparation',['REQ-1'],1)
            node,reservation=admission.claim('native',worker_slot='slot1')
            assert node['kind']=='MANIFEST_GENERATOR' and reservation is None
            assert runner.census()['preparing']==1
            assert len(store.active_jobs())+runner.census()['owned']==2
            assert not pending.done()
            admission.update_capacity(0)
            with pytest.raises(ContainerCapacityError):
                runner._reserve('_warm_','pressure-forbidden',preparing=True)
        finally:
            release.set()
        pending.result(timeout=10)
    assert admission.maintenance_requested.is_set()


def test_fifo_head_preserves_one_seat_without_blocking_all_spare_native_capacity(tmp_path):
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    runner.request_preparation(runner.policy.pool_digest,'fifo-head')
    for _ in range(4): store.submit('Native pending',['REQ-1'],1)
    claimed=[admission.claim(f'owner-{n}',worker_slot=f'slot{n}') for n in range(4)]
    assert sum(item is not None for item in claimed)==3
    assert runner.census()['published_capacity']==1
    assert runner.preparation_requests()[0]['job_id']=='fifo-head'
    runner._reserve('_warm_','first-preparation',preparing=True)
    assert len(store.active_jobs())+runner.census()['owned']==4
    assert admission.claim('extra',worker_slot='slot5') is None


def test_slow_engine_census_does_not_hold_native_admission(tmp_path,monkeypatch):
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    entered,release=threading.Event(),threading.Event()
    def slow_census():
        entered.set()
        assert release.wait(10)
        return []
    monkeypatch.setattr(runner,'_discover_owned',slow_census)
    monkeypatch.setattr(runner,'prepare_pool',lambda **kw:{'census':runner.census()})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(admission.maintenance)
        try:
            assert entered.wait(5)
            store.submit('Native during engine census',['REQ-1'],1)
            assert admission.claim('owner',worker_slot='slot1') is not None
            assert not pending.done()
        finally:
            release.set()
        pending.result(timeout=10)


def test_requested_profile_cannot_clear_pressure_signal_or_report_ready(tmp_path,monkeypatch):
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    test_stage(store,runner.policy,'requested')
    runner.request_preparation(runner.policy.pool_digest,'requested-test')
    monkeypatch.setattr(runner,'reap_orphans',lambda **kw:None)
    def pressure_during_preparation(self,capacity,*,admission_lock=None):
        admission.update_capacity(0)
        return {'ready':True,'census':self.census()}
    monkeypatch.setattr(DockerRunner,'prepare_requested_profile',pressure_during_preparation)
    result=admission.maintenance()
    assert result['ready'] is False
    assert result['reason']=='pressure_revoked_prepared_capacity'
    assert admission.maintenance_requested.is_set()


def test_completion_notification_survives_inflight_maintenance(tmp_path,monkeypatch):
    store=JobStore(tmp_path/'jobs.db')
    runner=DockerRunner(policy(),tmp_path/'containers')
    admission=JointAdmission(store,runner,4)
    monkeypatch.setattr(runner,'reap_orphans',lambda **kw:None)
    def completion_during_prepare(**kwargs):
        admission.request_maintenance()
        return {'census':runner.census()}
    monkeypatch.setattr(runner,'prepare_pool',completion_during_prepare)
    admission.maintenance()
    assert admission.maintenance_requested.is_set()
