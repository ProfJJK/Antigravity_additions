"""Durable routing contracts using physical SQLite and real concurrent callers.

Metadata inputs to completion are explicitly storage-contract fixtures; these
tests do not assert live provider availability or invoke any model CLI.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.store import JobStore, canonical_json, output_digest


def policy(**changes):
    return load_routing_policy({"backoff_base_seconds": .025, "backoff_max_seconds": .05,
                                "backoff_jitter_fraction": 0, **changes})


def submitted(tmp_path, selected_policy=None, **submit_options):
    store = JobStore(tmp_path / "routes.db", routing_policy=selected_policy or policy())
    workflow = store.submit("Document a note", ["REQ-1"], 1, **submit_options)
    manifest = next(job for job in workflow["jobs"] if job["kind"] == "MANIFEST_GENERATOR")
    return store, workflow["workflow_id"], manifest["job_id"]


def fail(store, node, category="quota", **options):
    return store.fail(node["job_id"], node["attempt_id"], node["fencing_token"],
                      "Storage-contract fixture: " + category, retry=True, category=category, **options)


def completion(node):
    output = {"chapters": [{"chapter_id": "chapter-one", "title": "Note", "requirements": ["REQ-1"]}]}
    route = node["route"]
    receipt = {"execution_kind": "controller-storage-contract-fixture", "provider": route["provider"],
               "requested_model": route["model"], "requested_effort": route.get("reasoning_effort"),
               "route_reservation_id": route["reservation_id"], "pid": os.getpid(), "exit_code": 0,
               "session_id": "storage-contract-only", "output_sha256": output_digest(output),
               "attempt_id": node["attempt_id"], "fencing_token": node["fencing_token"],
               "worker_slot": node["worker_slot"], "job_id": node["job_id"], "workflow_id": node["workflow_id"],
               "selected_route": route}
    return output, receipt


def test_quota_and_auth_advance_without_consuming_task_failure_budget(tmp_path):
    selected = policy(failure_cooldowns={"quota": 0, "auth": 0})
    store, workflow_id, job_id = submitted(tmp_path, selected)
    first_cycle = []
    for _ in range(3):
        node = store.claim("owner", worker_slot="isolated")
        first_cycle.append(node["route"]["provider"])
        assert fail(store, node)
    assert first_cycle == ["gemini", "claude", "codex"]
    waiting = store.get(job_id)
    assert waiting["attempts"] == waiting["routing"]["dispatches"] == 3
    assert waiting["routing"]["failure_count"] == 0
    assert waiting["routing"]["state"] == "WAITING"
    assert store.claim("too-early") is None
    time.sleep(.04)
    restarted = JobStore(store.path, routing_policy=selected)
    fourth = restarted.claim("after-restart", worker_slot="isolated")
    assert fourth["route"]["provider"] == "gemini" and fourth["route"]["cycle"] == 1
    assert fourth["attempts"] == 4
    assert fail(restarted, fourth, "auth")
    assert restarted.workflow(workflow_id)["status"] == "IN_PROGRESS"
    assert restarted.get(job_id)["routing"]["failure_count"] == 0


def test_model_pool_reservations_are_atomic_under_real_threads(tmp_path):
    selected = policy(model_limits={target['key']: 1 for score in (1, 4, 7, 10)
                                   for target in [item.as_dict() for item in policy().candidates(score)]})
    store = JobStore(tmp_path / "routes.db", routing_policy=selected)
    workflows = [store.submit("Document a note", ["REQ-1"], 1) for _ in range(4)]
    gate = threading.Barrier(4)

    def claim(index):
        local = JobStore(store.path, routing_policy=selected)
        gate.wait(timeout=5)
        return local.claim(f"owner-{index}", worker_slot=f"slot-{index}", max_workers=4)

    with ThreadPoolExecutor(max_workers=4) as pool:
        nodes = list(pool.map(claim, range(4)))
    active = [node for node in nodes if node]
    assert len(active) == 3
    assert {node["route"]["provider"] for node in active} == {"gemini", "claude", "codex"}
    assert len(store.routing_status()["active_reservations"]) == 3
    remaining = [job for workflow in workflows for job in store.workflow(workflow["workflow_id"])["jobs"]
                 if job["kind"] == "MANIFEST_GENERATOR" and job["status"] == "PENDING"]
    assert len(remaining) == 1
    assert remaining[0]["attempts"] == remaining[0]["routing"]["dispatches"] == 0
    assert remaining[0]["routing"]["state"] == "WAITING"


def test_receipt_binding_rejects_wrong_target_and_stale_attempt_and_releases_on_success(tmp_path):
    store, workflow_id, job_id = submitted(tmp_path, policy(failure_cooldowns={"busy": 0}))
    old = store.claim("first", worker_slot="identity")
    output, old_receipt = completion(old)
    assert fail(store, old, "busy")
    current = store.claim("second", worker_slot="identity")
    with pytest.raises(ValueError, match="Stale"):
        store.complete(job_id, old["attempt_id"], old["fencing_token"], output, old_receipt)
    output, receipt = completion(current)
    for field, value in (("provider", "codex"), ("requested_model", "wrong-model"),
                         ("route_reservation_id", "unrelated"), ("worker_slot", "sibling-identity"),
                         ("job_id", "other-job"), ("requested_effort", "ultra")):
        with pytest.raises(ValueError, match="receipt"):
            store.complete(job_id, current["attempt_id"], current["fencing_token"], output, {**receipt, field: value})
    store.complete(job_id, current["attempt_id"], current["fencing_token"], output, receipt)
    assert store.routing_status()["active_reservations"] == []
    assert store.get(job_id)["routing"]["state"] == "COMPLETED"
    assert len([job for job in store.workflow(workflow_id)["jobs"] if job["kind"] == "CHAPTER_DRAFT"]) == 1


def test_policy_snapshot_and_sql_route_identity_cannot_be_reinterpreted(tmp_path):
    old_policy = policy()
    store, workflow_id, job_id = submitted(tmp_path, old_policy)
    revised = old_policy.as_dict()
    revised["model_limits"]["gemini:gemini-3.8-flash"] = 2
    reopened = JobStore(store.path, routing_policy=load_routing_policy(revised))
    node = reopened.claim("changed-policy", worker_slot="identity")
    assert node["route"]["provider"] == "gemini"
    assert node["route"]["policy_digest"] == old_policy.digest
    assert node["routing_policy"] == old_policy.as_dict()
    for sql in ("UPDATE pipeline_routing_jobs SET score_json='{}'", "UPDATE pipeline_route_reservations SET model='invented'",
                "UPDATE pipeline_routing_workflows SET policy_json='{}'"):
        with sqlite3.connect(store.path) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(sql)
    reopened.cancel_workflow(workflow_id)
    assert reopened.routing_status()["active_reservations"] == []
    assert reopened.get(job_id)["routing"]["state"] == "FAILED"


def test_explicit_dispatch_cap_counts_availability_failure_without_allowing_second_call(tmp_path):
    store, workflow_id, job_id = submitted(tmp_path, max_dispatches=1)
    node = store.claim("single-call", worker_slot="identity")
    assert fail(store, node, "quota")
    assert store.claim("forbidden-second") is None
    final = store.get(job_id)
    assert final["attempts"] == final["routing"]["dispatches"] == 1
    assert final["routing"]["failure_count"] == 0
    assert store.workflow(workflow_id)["status"] == "FAILED"
    assert store.routing_status()["active_reservations"] == []
    with pytest.raises(ValueError, match="immutable"):
        store.submit("Document a note", ["REQ-1"], 1, workflow_id=workflow_id, max_dispatches=2)


def test_context_skip_is_job_local_without_shared_model_or_pool_hold(tmp_path):
    store, _, job_id = submitted(tmp_path)
    first = store.claim("first", worker_slot="identity")
    assert fail(store, first, "context")
    assert store.routing_status()["holds"] == []
    second = store.claim("second", worker_slot="identity")
    assert second["job_id"] == job_id and second["route"]["candidate_index"] == 1
    other = store.submit("Document another note", ["REQ-1"], 1)
    next_job = store.claim("other", worker_slot="other-identity")
    assert next_job["workflow_id"] == other["workflow_id"]
    assert next_job["route"]["candidate_index"] == 0


@pytest.mark.parametrize("category", ["configuration", "compatibility"])
def test_operator_hold_can_resume_without_resetting_policy_or_budgets(tmp_path, category):
    store, _, job_id = submitted(tmp_path)
    first = store.claim("first", worker_slot="identity")
    assert fail(store, first, category, hold_scope="job")
    held = store.get(job_id)
    assert held["status"] == held["routing"]["state"] == "BLOCKED"
    assert held["routing"]["failure_count"] == 0
    assert store.claim("cannot-bypass") is None
    resumed = JobStore(store.path).resume_routing(job_id, 'Operator restored the prerequisite')
    assert resumed["routing"]["dispatches"] == 1
    second = store.claim("operator-restored", worker_slot="identity")
    assert second["route"]["key"] == first["route"]["key"]
    assert second["route"]["policy_digest"] == first["route"]["policy_digest"]
    assert second["attempt_id"] != first["attempt_id"]


def test_cycle_limit_holds_instead_of_losing_job_and_resume_cannot_reset_horizon(tmp_path):
    store, workflow_id, job_id = submitted(tmp_path, policy(max_routing_cycles=1))
    for target in store.get(job_id)["routing"]["candidates"]:
        store.set_route_hold("model", target["key"], .025)
    assert store.claim("held") is None
    time.sleep(.04)
    assert store.claim("limit") is None
    assert store.get(job_id)["status"] == "BLOCKED"
    assert store.workflow(workflow_id)["status"] == "IN_PROGRESS"
    assert store.get(job_id)["attempts"] == 0
    with pytest.raises(ValueError, match='cannot reset budgets'):
        store.resume_routing(job_id, 'Operator attempted to resume')
    assert store.get(job_id)['routing']['cycle'] == 1
    assert store.get(job_id)['attempts'] == 0


def test_real_lease_expiry_consumes_failure_budget_and_releases_occupancy(tmp_path):
    store, workflow_id, job_id = submitted(tmp_path, max_attempts=1)
    node = store.claim("crashed-owner", lease_seconds=.03, worker_slot="identity")
    time.sleep(.05)
    assert store.reap_expired()[0]["job_id"] == job_id
    assert store.get(job_id)["routing"]["failure_count"] == 1
    assert store.routing_status()["active_reservations"] == []
    assert store.workflow(workflow_id)["status"] == "BLOCKED"
    assert store.heartbeat(job_id, node["attempt_id"], node["fencing_token"]) is False


def test_legacy_live_attempt_is_not_given_a_fabricated_route_and_new_claims_wait(tmp_path):
    legacy = JobStore(tmp_path / 'routes.db')
    legacy.routing_policy = None  # Explicit old-version storage fixture, never a native dispatch.
    first = legacy.submit('Legacy task', ['REQ-1'], 1)
    legacy.submit('Queued legacy task', ['REQ-1'], 1)
    running = legacy.claim('legacy-process', worker_slot='legacy-slot')
    current = JobStore(legacy.path, routing_policy=policy())
    assert current.get(running['job_id'])['route'] is None
    assert current.get(running['job_id'])['routing'] is None
    assert current.claim('cannot-ignore-live-legacy', worker_slot='new-slot') is None
    output = {'chapters': [{'chapter_id':'chapter-one','title':'Note','requirements':['REQ-1']}]}
    receipt = {'provider':'codex','pid':os.getpid(),'exit_code':0,'session_id':'legacy-storage-contract',
               'output_sha256':output_digest(output)}
    # Explicit controller storage-contract closure acknowledgement; no native
    # Windows process is involved in this legacy metadata fixture.
    current.clear_execution_quarantine(running['job_id'],running['attempt_id'],running['fencing_token'])
    current.complete(running['job_id'],running['attempt_id'],running['fencing_token'],output,receipt)
    assert current.get(running['job_id'])['route'] is None
    node = current.claim('new-controller',worker_slot='new-slot')
    assert node is not None and node['route'] is not None
    assert len(current.routing_status()['active_reservations']) == 1
    assert current.workflow(first['workflow_id'])['status'] == 'IN_PROGRESS'


def test_new_admission_limits_apply_without_reordering_captured_policy(tmp_path):
    generous = policy().as_dict()
    generous['model_limits'] = {key:4 for key in generous['model_limits']}
    generous['provider_limits'] = {key:{**value,'max_concurrency':4} for key,value in generous['provider_limits'].items()}
    generous['quota_pool_limits'] = {key:4 for key in generous['quota_pool_limits']}
    store = JobStore(tmp_path/'routes.db',routing_policy=load_routing_policy(generous))
    for _ in range(3):
        store.submit('Document a note',['REQ-1'],1)
    first = store.claim('first',worker_slot='s1')
    assert first['route']['provider']=='gemini'
    limited = dict(generous)
    limited['provider_limits'] = {key:{**value,'max_concurrency':1,'quota_pool':'shared'}
                                  for key,value in generous['provider_limits'].items()}
    limited['quota_pool_limits'] = {'shared':1}
    reopened = JobStore(store.path,routing_policy=load_routing_policy(limited))
    assert reopened.claim('shared-current-cap',worker_slot='s2') is None
    assert len(reopened.routing_status()['active_reservations'])==1
    assert reopened.get(first['job_id'])['routing_policy']==load_routing_policy(generous).as_dict()


def test_direct_sql_cannot_swap_astra_effort_in_reserved_completion(tmp_path):
    store = JobStore(tmp_path/'routes.db',routing_policy=policy())
    objective = 'security schema concurrency production proof '*100
    workflow = store.submit(objective,[f'REQ-{index}' for index in range(12)],8)
    first = store.claim('first',worker_slot='identity')
    assert first['route']['score']==10 and first['route']['provider']=='claude'
    fail(store,first,'context')
    astra = store.claim('second',worker_slot='identity')
    assert astra['route']['reasoning_effort']=='ultra'
    output,receipt = completion(astra)
    receipt['requested_effort']='low'
    with sqlite3.connect(store.path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with pytest.raises(sqlite3.IntegrityError,match='receipt does not match'):
            conn.execute('''INSERT INTO pipeline_outputs(job_id,attempt_id,fencing_token,chapter_id,
                output_json,receipt_json,output_sha256,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                (astra['job_id'],astra['attempt_id'],astra['fencing_token'],None,canonical_json(output),
                 canonical_json(receipt),output_digest(output),time.time()))
    assert store.workflow(workflow['workflow_id'])['status']=='IN_PROGRESS'


def test_configured_hardware_capacity_is_separate_from_provider_caps(tmp_path):
    store = JobStore(tmp_path/'routes.db')
    for _ in range(65):
        store.submit('Document a note', ['REQ-1'], 1)
    nodes = [store.claim(f'owner-{index}', worker_slot=f's-{index}', max_workers=128) for index in range(65)]
    assert all(node and node['route']['provider'] == 'gemini' for node in nodes)
    assert len(store.active_jobs()) == len(store.routing_status()['active_reservations']) == 65
    # An independently configured hardware bound still refuses excess work.
    store.submit('One more note', ['REQ-1'], 1)
    assert store.claim('hardware-bound', worker_slot='s-65', max_workers=65) is None


def test_claude_twenty_concurrent_reservations_spill_over_atomically(tmp_path):
    store = JobStore(tmp_path/'routes.db')
    for _ in range(22):
        store.submit('Document authentication', ['REQ-1'], 1)
    nodes = [store.claim(f'owner-{index}', worker_slot=f's-{index}', max_workers=64) for index in range(22)]
    assert [node['route']['provider'] for node in nodes] == ['claude'] * 20 + ['codex'] * 2
    assert all(node['route']['score'] == 4 for node in nodes)


@pytest.mark.parametrize('ending',['cancel','fatal_sibling'])
def test_real_process_cleanup_barrier_survives_cancel_or_fatal_sibling(tmp_path,ending):
    store = JobStore(tmp_path/'routes.db',routing_policy=policy(),cleanup_boot_id=100)
    workflow = store.submit('Document notes',['REQ-1'],2 if ending=='fatal_sibling' else 1)
    if ending=='fatal_sibling':
        manifest = store.claim('manifest',worker_slot='manifest-identity')
        output,receipt = completion(manifest)
        output['chapters'].append({'chapter_id':'chapter-two','title':'Second note','requirements':['REQ-1']})
        receipt['output_sha256']=output_digest(output)
        store.complete(manifest['job_id'],manifest['attempt_id'],manifest['fencing_token'],output,receipt)
    owned = store.claim('physical-fixture',worker_slot='physical-identity',requires_cleanup=True,
                        cleanup_boot_id=100,containment_id='a'*32)
    child = subprocess.Popen([sys.executable,'-c',"import sys; print('fixture-ready',flush=True); sys.stdin.buffer.read(1)"],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert child.stdout.readline()==b'fixture-ready\n'
        if ending=='cancel':
            store.cancel_workflow(workflow['workflow_id'])
        else:
            sibling = store.claim('failed-sibling',worker_slot='sibling-identity')
            assert sibling['workflow_id']==workflow['workflow_id'] and sibling['job_id']!=owned['job_id']
            assert store.fail(sibling['job_id'],sibling['attempt_id'],sibling['fencing_token'],'fatal protocol fixture',retry=False)
        store.submit('Independent waiting task',['REQ-1'],1)
        assert child.poll() is None
        restarted = JobStore(store.path,routing_policy=policy(),cleanup_boot_id=100)
        assert restarted.claim('cannot-spend-while-old-child-lives',worker_slot='next-identity') is None
        assert restarted.recover_execution_quarantines_after_boot(100)==[]
        barriers = restarted.execution_quarantines()
        assert len(barriers)==1 and barriers[0]['containment_id']=='a'*32
        with pytest.raises(ValueError,match='mismatched'):
            restarted.clear_execution_quarantine(owned['job_id'],owned['attempt_id'],owned['fencing_token']+1)
        child.communicate(b'x',timeout=5)
        assert child.returncode==0
        assert restarted.clear_execution_quarantine(owned['job_id'],owned['attempt_id'],owned['fencing_token'])
        assert restarted.execution_quarantines()==[]
        assert restarted.claim('after-physical-close',worker_slot='next-identity') is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_cleanup_proof_precedes_cancel_without_reopening_and_identity_cannot_be_rewritten(tmp_path):
    store, workflow_id, _ = submitted(tmp_path)
    node = store.claim('owner',worker_slot='identity',requires_cleanup=True,cleanup_boot_id=100,containment_id='b'*32)
    assert store.confirm_execution_cleanup(node['job_id'],node['attempt_id'],node['fencing_token'])
    store.cancel_workflow(workflow_id)
    assert store.execution_quarantines()==[]
    assert store.quarantine_execution(node['job_id'],node['attempt_id'],node['fencing_token'],
                                      'identity','late duplicate cancellation') is False
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute("UPDATE pipeline_execution_cleanup SET containment_id=?",('c'*32,))
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('UPDATE pipeline_execution_cleanup SET boot_id=999')
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('UPDATE pipeline_execution_cleanup SET observed_boot_id=999')
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('UPDATE pipeline_execution_cleanup SET cleared_at=NULL')


def test_reboot_proof_distinguishes_known_unknown_and_legacy_observed_boot(tmp_path):
    # Boot numbers here are explicit storage-contract inputs. The native
    # controller, independently tested on Windows, supplies the trusted value.
    store, _, _ = submitted(tmp_path)
    known = store.claim('known-boot',worker_slot='known',requires_cleanup=True,cleanup_boot_id=100)
    store.quarantine_execution(known['job_id'],known['attempt_id'],known['fencing_token'],'known','uncertain close')
    assert store.recover_execution_quarantines_after_boot(100)==[]
    assert len(store.recover_execution_quarantines_after_boot(200))==1
    assert store.execution_quarantines()==[]
    store.fail(known['job_id'],known['attempt_id'],known['fencing_token'],'fixture failure',retry=True)
    unknown = store.claim('unknown-boot',worker_slot='unknown',requires_cleanup=True)
    store.quarantine_execution(unknown['job_id'],unknown['attempt_id'],unknown['fencing_token'],'unknown','uncertain close')
    assert store.recover_execution_quarantines_after_boot(300)==[]
    assert len(store.execution_quarantines())==1

    legacy = JobStore(tmp_path/'legacy.db')
    legacy.routing_policy = None  # Explicit old-version storage fixture, never a native dispatch.
    legacy.submit('Legacy task',['REQ-1'],1)
    old = legacy.claim('unknown-original-boot',worker_slot='legacy',lease_seconds=.02)
    migrated = JobStore(legacy.path,routing_policy=policy(),cleanup_boot_id=400)
    with migrated._connection() as conn:
        record=dict(conn.execute('SELECT * FROM pipeline_execution_cleanup').fetchone())
    assert record['boot_id'] is None and record['observed_boot_id']==400
    time.sleep(.035)
    assert migrated.claim('no-proof',worker_slot='new') is None
    assert migrated.recover_execution_quarantines_after_boot(400)==[]
    later_boot = JobStore(legacy.path,routing_policy=policy(),cleanup_boot_id=500)
    assert len(later_boot.recover_execution_quarantines_after_boot(500))==1
    assert later_boot.execution_quarantines()==[]
    assert later_boot.claim('post-reboot',worker_slot='new') is not None


def test_startup_quarantines_unexpired_guard_before_any_workspace_reuse(tmp_path):
    store, _, _ = submitted(tmp_path)
    node = store.claim('former-daemon',worker_slot='identity',lease_seconds=60,requires_cleanup=True,cleanup_boot_id=100)
    assert store.execution_quarantines()==[]
    restarted = JobStore(store.path,routing_policy=policy(),cleanup_boot_id=100)
    held = restarted.quarantine_unclosed_executions()
    assert len(held)==1 and held[0]['attempt_id']==node['attempt_id']
    assert held[0]['quarantined']==1
    assert restarted.claim('cannot-reuse-live-workspace',worker_slot='another') is None


def test_current_admission_can_release_legacy_caps_without_rewriting_captured_routes(tmp_path):
    constrained = policy(model_limits={'gemini:gemini-3.8-flash': 1},
                         provider_limits={'gemini': {'max_concurrency': 1}},
                         quota_pool_limits={'gemini': 1, 'codex': None, 'claude': None})
    store = JobStore(tmp_path/'relax.db', routing_policy=constrained)
    store.submit('First note', ['REQ-1'], 1)
    store.submit('Second note', ['REQ-1'], 1)
    first = store.claim('first', worker_slot='one')
    current = policy()
    reopened = JobStore(store.path, routing_policy=current)
    second = reopened.claim('second', worker_slot='two')
    assert first['route']['provider'] == second['route']['provider'] == 'gemini'
    assert first['route']['policy_digest'] == second['route']['policy_digest'] == constrained.digest
    assert first['route']['admission_policy_digest'] == constrained.digest
    assert second['route']['admission_policy_digest'] == current.digest
    assert second['route']['max_concurrency'] == 1  # Original task authority remains unchanged.
    assert reopened.get(first['job_id'])['route'] == first['route']
