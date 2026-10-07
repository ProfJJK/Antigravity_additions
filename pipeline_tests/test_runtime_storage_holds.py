"""Physical SQLite readiness holds; these do not emulate native RAM/ACL proof."""
import json
import sqlite3
import time

import pytest

from cochem_pipeline.ramdisk import RamdiskCapacityError, RamdiskError
from cochem_pipeline.resource_limits import ResourcePolicyError
from cochem_pipeline.runtime import startup_quarantines
from cochem_pipeline.store import JobStore
from cochem_pipeline.windows import WindowsIsolationError
from pipeline_tests.test_runtime_routing import controller, claim


@pytest.mark.parametrize('failure,category',[
    (RamdiskError('RAM device identity changed'),'configuration'),
    (ResourcePolicyError('Native E-core topology is unsupported'),'configuration'),
    (WindowsIsolationError('Native isolation access boundary changed'),'configuration'),
])
def test_native_readiness_failures_preserve_actual_task_budget_and_do_not_request_paid_repair(tmp_path,failure,category):
    runtime,_=controller(tmp_path,max_attempts=1)
    node=claim(runtime,requires_cleanup=True)
    assert runtime._record_failure(node,'slot1',failure)
    restored=JobStore(runtime.store.path,routing_policy=runtime.config.routing)
    job=restored.get(node['job_id'])
    assert job['status']=='BLOCKED'
    assert job['routing']['failure_count']==0
    assert job['routing']['wait_reason']==category
    assert restored.claim('another-controller',worker_slot='slot2') is None
    assert not restored.execution_quarantines()
    with sqlite3.connect(restored.path) as db:
        events=[json.loads(row[0]) for row in db.execute('SELECT details FROM recovery_telemetry')]
    assert events[-1]['action']=='routing_hold'
    assert events[-1]['category']==category
    assert events[-1]['hold_scope']=='job'


def test_ram_capacity_recovers_by_durable_delayed_retry_without_operator_resume(tmp_path):
    runtime,_=controller(tmp_path,max_attempts=1)
    node=claim(runtime,requires_cleanup=True)
    assert runtime._record_failure(node,'slot1',RamdiskCapacityError('Verified RAM reserve is temporarily exhausted'))
    restored=JobStore(runtime.store.path,routing_policy=runtime.config.routing)
    job=restored.get(node['job_id'])
    assert job['status']=='PENDING_RETRY'
    assert job['routing']['failure_count']==0
    assert job['routing']['wait_reason']=='host_resource_pressure'
    assert job['routing']['next_eligible_at']>time.time()
    assert restored.claim('before-recovery-delay',worker_slot='slot1') is None
    assert not restored.execution_quarantines()
    with sqlite3.connect(restored.path) as db:
        event=json.loads(db.execute('SELECT details FROM recovery_telemetry ORDER BY id DESC LIMIT 1').fetchone()[0])
    assert event['action']=='provider_unavailable'
    assert event['hold_scope'] is None
    # Exercise an expired persisted deadline without replacing any clock or
    # native capability. This is a SQLite retry contract, not RAM attestation.
    with sqlite3.connect(restored.path) as db:
        db.execute('UPDATE pipeline_routing_jobs SET next_eligible_at=? WHERE job_id=?',
                   (time.time()-1,node['job_id']))
    retried=restored.claim('healthy-capacity-admission',worker_slot='slot1')
    assert retried['attempt_id']!=node['attempt_id']
    assert retried['route']['candidate_index']==node['route']['candidate_index']
    assert retried['routing']['failure_count']==0


def test_storage_admission_requires_both_resolved_cleanup_and_fresh_ram_verification(tmp_path):
    runtime,_=controller(tmp_path)
    node=claim(runtime,requires_cleanup=True)
    slots={'slot1':tmp_path/'first','slot2':tmp_path/'second'}
    restarted=JobStore(runtime.store.path,routing_policy=runtime.config.routing,cleanup_boot_id=runtime.boot_id)
    unresolved=restarted.quarantine_unclosed_executions()
    assert set(startup_quarantines(slots,unresolved,ramdisk_required=True,ramdisk_ready=False))==set(slots)
    # Physical SQLite cleanup acknowledges the exact guarded attempt. The test
    # supplies a controller proof fixture; it does not claim Linux killed a
    # Windows process or established native volume identity.
    restarted.clear_execution_quarantine(node['job_id'],node['attempt_id'],node['fencing_token'])
    resolved=restarted.quarantine_unclosed_executions()
    assert resolved==[]
    assert set(startup_quarantines(slots,resolved,ramdisk_required=True,ramdisk_ready=False))==set(slots)
    assert startup_quarantines(slots,resolved,ramdisk_required=True,ramdisk_ready=True)=={}


def test_unknown_worker_cleanup_ownership_closes_all_slots_even_with_ram_ready(tmp_path):
    slots={'slot1':tmp_path/'first','slot2':tmp_path/'second'}
    assert set(startup_quarantines(slots,[{'worker_slot':'removed-worker'}],
        ramdisk_required=True,ramdisk_ready=True))==set(slots)
