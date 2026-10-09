"""Real SQLite, thread, and child-process acceptance tests for the job board.

Structured document fixtures exercise the trusted scheduler/storage contract.
Receipt metadata is explicitly labelled as deterministic contract-test data;
these tests neither call a model nor establish live provider availability.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from cochem_pipeline.store import JobStore, artifact_digest, canonical_json, output_digest


def receipt(output, provider=None, job=None):
    # A physical Python process hashes the exact bytes; it is explicitly NOT an LLM.
    proc = subprocess.run(
        [sys.executable, "-c", "import hashlib,json,os,sys; raw=sys.stdin.buffer.read(); print(json.dumps({'pid':os.getpid(),'output_sha256':hashlib.sha256(raw).hexdigest()}))"],
        input=canonical_json(output), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    result = {**json.loads(proc.stdout), "provider": provider or "codex", "exit_code": proc.returncode,
              "session_id": "deterministic-contract-test", "execution_kind": "python-storage-contract-test"}
    if job is not None and job.get('route'):
        route = job['route']
        result.update(provider=provider or route['provider'], requested_model=route['model'],
                      requested_effort=route.get('reasoning_effort'), selected_route=route,
                      route_reservation_id=route['reservation_id'], attempt_id=job['attempt_id'],
                      fencing_token=job['fencing_token'], worker_slot=job['worker_slot'],
                      job_id=job['job_id'], workflow_id=job['workflow_id'])
    return result


def finish(store, job, output, provider=None):
    return store.complete(job["job_id"], job["attempt_id"], job["fencing_token"], output, receipt(output, provider, job))


def manifest(count=6):
    return {"chapters": [{"chapter_id": f"ch-{index}", "title": f"Chapter {index}",
                           "requirements": ["REQ-1"], 'wbs_tasks_defined': [
                               {'id': f'ch-{index}-declared', 'description': 'Implement assigned requirement',
                                'requirements': ['REQ-1']}]} for index in range(count)]}


def chapter(job):
    return {"chapter_id": job["chapter_id"], "requirements_traced": job["payload"]["requirements"],
            "wbs_tasks_defined": job['payload']['wbs_tasks_defined'] + [
                {'id': f"{job['chapter_id']}-01", 'description': 'Verify requirement',
                 'requirements': job['payload']['requirements']}],
            "artifact_uri": f"db://{job['workflow_id']}/{job['chapter_id']}",
            "artifact_text": f"# {job['payload']['title']}\nRequirement: REQ-1\nVerified structured chapter."}


def seeded(tmp_path, count=6):
    store = JobStore(tmp_path / "job_board.db")
    workflow = store.submit("Create an SRS and WBS", ["REQ-1"], count)
    job = store.claim("manifest-worker")
    finish(store, job, manifest(count))
    return store, workflow["workflow_id"]


def synthesis_output(job, text='Complete document'):
    return {'artifact_text': text, **{key: job['payload'][key] for key in (
        'chapter_hashes', 'chapter_output_hashes', 'coverage_report_sha256', 'wbs_tasks_by_chapter')}}


def test_submit_is_durable_idempotent_and_preserves_legacy_tables(tmp_path):
    db = tmp_path / "job_board.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs(legacy_value TEXT)")
        conn.execute("INSERT INTO jobs VALUES('preserve-me')")
    store = JobStore(db)
    workflow = store.submit("SRS objective", ["REQ-1"], workflow_id="explicit-workflow")
    again = JobStore(db).submit("SRS objective", ["REQ-1"], workflow_id="explicit-workflow")
    assert workflow == again
    assert len(workflow["jobs"]) == 3
    assert [root["job_id"] for root in store.list_workflows()] == [workflow["workflow_id"]]
    assert store.active_jobs() == []
    assert workflow["root"]["status"] == "IN_PROGRESS"
    assert {job["kind"]: job["status"] for job in workflow["jobs"]}["SYNTHESIS"] == "BLOCKED"
    with pytest.raises(ValueError, match="different request"):
        store.submit("Different objective", ["REQ-1"], workflow_id="explicit-workflow")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("SELECT legacy_value FROM jobs").fetchone()[0] == "preserve-me"
    with store._connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


@pytest.mark.parametrize("bad", [
    {"chapters": []},
    {"chapters": [{"chapter_id": "same", "title": "A", "requirements": ["REQ-1"]}] * 2},
    {"chapters": [{"chapter_id": "a", "title": "A", "requirements": ["REQ-1"]},
                  {"chapter_id": "b", "title": "B", "requirements": ["UNKNOWN"]}]},
])
def test_invalid_manifest_rolls_back_scatter_and_completion(tmp_path, bad):
    store = JobStore(tmp_path / "jobs.db")
    workflow = store.submit("Objective", ["REQ-1"], 2)
    job = store.claim("owner")
    with pytest.raises(ValueError):
        finish(store, job, bad)
    current = store.workflow(workflow["workflow_id"])
    assert len(current["jobs"]) == 3
    assert store.get(job["job_id"])["status"] == "IN_PROGRESS"
    assert current["artifacts"] == []


def test_six_chapter_scatter_with_four_worker_limit_and_real_overlap(tmp_path):
    store, workflow_id = seeded(tmp_path)
    ready = queue.Queue()
    gate = threading.Event()

    def execute(owner):
        local = JobStore(store.path)
        job = local.claim(owner, max_workers=4)
        assert job is not None and job["kind"] == "CHAPTER_DRAFT"
        start = time.monotonic()
        ready.put((job, start))
        assert gate.wait(5)
        finish(local, job, chapter(job))
        return job["job_id"], start, time.monotonic()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(execute, f"thread-worker-{i}") for i in range(4)]
        accepted = [ready.get(timeout=5) for _ in range(4)]
        assert len({job["job_id"] for job, _ in accepted}) == 4
        assert store.claim("fifth-worker", max_workers=4) is None
        assert sum(j["kind"] == "CHAPTER_DRAFT" and j["status"] == "IN_PROGRESS"
                   for j in store.workflow(workflow_id)["jobs"]) == 4
        gate.set()
        intervals = [future.result(timeout=10) for future in futures]
    assert max(start for _, start, _ in intervals) < min(end for _, _, end in intervals)
    with ThreadPoolExecutor(max_workers=2) as pool:
        remaining = list(pool.map(execute, ["final-a", "final-b"]))
    assert len({entry[0] for entry in intervals + remaining}) == 6
    workflow = store.workflow(workflow_id)
    assert len([j for j in workflow["jobs"] if j["kind"] == "SYNTHESIS"]) == 1
    assert len([e for e in store.events(workflow_id) if e["event"] == "SYNTHESIS_RELEASED"]) == 1
    assert workflow["root"]["status"] == "IN_PROGRESS"
    synthesis = store.claim("synthesis-controller")
    assert synthesis["kind"] == "SYNTHESIS"
    assert len(synthesis["payload"]["chapter_hashes"]) == 6
    output = synthesis_output(synthesis, '# Complete SRS/WBS')
    finish(store, synthesis, output)
    final = store.workflow(workflow_id)
    assert final["root"]["status"] == "COMPLETED"
    assert len(final["artifacts"]) == 7
    assert store.cancel_workflow(workflow_id) == []
    assert store.workflow(workflow_id) == final


def test_chapter_ownership_enforced_in_python_and_database_trigger(tmp_path):
    store, workflow_id = seeded(tmp_path, 2)
    first, second = store.claim("first"), store.claim("second")
    wrong = chapter(first)
    wrong["chapter_id"] = second["chapter_id"]
    with pytest.raises(ValueError, match="ownership"):
        finish(store, first, wrong)
    # Bypass the Python validator: the database must still reject sibling output.
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="ownership"):
            conn.execute("""INSERT INTO pipeline_outputs VALUES(?,?,?,?,?,?,?,?)""",
                         (first["job_id"], first["attempt_id"], first["fencing_token"],
                          second["chapter_id"], canonical_json(wrong), canonical_json(receipt(wrong, job=first)), output_digest(wrong), time.time()))
    assert store.workflow(workflow_id)["artifacts"] == []


def test_artifacts_and_outputs_immutable_and_identical_completion_idempotent(tmp_path):
    store, _ = seeded(tmp_path, 1)
    job = store.claim("chapter-owner")
    output = chapter(job)
    process_receipt = receipt(output, job=job)
    before = store.complete(job["job_id"], job["attempt_id"], job["fencing_token"], output, process_receipt)
    again = store.complete(job["job_id"], job["attempt_id"], job["fencing_token"], output, process_receipt)
    assert again == before
    assert before["artifact_sha256"] == artifact_digest(output["artifact_text"])
    for query in ("UPDATE pipeline_artifacts SET artifact_text='mutated'", "DELETE FROM pipeline_artifacts",
                  "UPDATE pipeline_outputs SET output_json='{}'", "DELETE FROM pipeline_outputs"):
        with sqlite3.connect(store.path) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(query)
    changed = {**output, "artifact_text": "Different artifact"}
    with pytest.raises(ValueError, match="Stale"):
        finish(store, job, changed)


def test_synthesis_rejects_wrong_hashes_or_unreserved_provider_receipt(tmp_path):
    store, workflow_id = seeded(tmp_path, 1)
    drafting = store.claim("chapter-owner")
    finish(store, drafting, chapter(drafting))
    synthesis = store.claim("synthesis-owner")
    output = synthesis_output(synthesis)
    with pytest.raises(ValueError, match="receipt"):
        finish(store, synthesis, output, "codex" if synthesis["route"]["provider"] != "codex" else "claude")
    with pytest.raises(ValueError, match="hashes"):
        finish(store, synthesis, {**output, "chapter_hashes": {drafting["chapter_id"]: "f" * 64}})
    assert store.workflow(workflow_id)["root"]["status"] == "IN_PROGRESS"
    finish(store, synthesis, output)
    assert store.workflow(workflow_id)["status"] == "COMPLETED"


def test_crash_expiry_reclaims_lease_and_fences_old_attempt(tmp_path):
    store, _ = seeded(tmp_path, 1)
    old = store.claim("old-process", lease_seconds=0.08)
    output = chapter(old)
    process_receipt = receipt(output, job=old)
    time.sleep(0.12)
    reaped = store.reap_expired()
    assert [job["job_id"] for job in reaped] == [old["job_id"]]
    assert reaped[0]["status"] == "PENDING_RETRY"
    new = JobStore(store.path).claim("new-process")
    assert new["attempt_id"] != old["attempt_id"]
    assert new["fencing_token"] > old["fencing_token"]
    assert store.heartbeat(old["job_id"], old["attempt_id"], old["fencing_token"]) is False
    assert store.fail(old["job_id"], old["attempt_id"], old["fencing_token"], "stale failure") is False
    with pytest.raises(ValueError, match="Stale"):
        store.complete(old["job_id"], old["attempt_id"], old["fencing_token"], output, process_receipt)
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="stale|reserved route"):
            conn.execute("INSERT INTO pipeline_outputs VALUES(?,?,?,?,?,?,?,?)",
                         (old["job_id"], old["attempt_id"], old["fencing_token"], old["chapter_id"],
                          canonical_json(output), canonical_json(process_receipt), output_digest(output), time.time()))
    finish(store, new, chapter(new))


def test_real_process_claim_survives_death_then_recovers(tmp_path):
    store, _ = seeded(tmp_path, 1)
    script = "from cochem_pipeline.store import JobStore; import json,os,sys; print(json.dumps(JobStore(sys.argv[1]).claim('child-process',lease_seconds=0.08)),flush=True); os._exit(17)"
    process = subprocess.run([sys.executable, "-c", script, str(store.path)], capture_output=True, text=True, check=False)
    assert process.returncode == 17  # Abrupt process exit: no shutdown/cleanup callbacks.
    prior = json.loads(process.stdout)
    assert prior["kind"] == "CHAPTER_DRAFT"
    time.sleep(0.12)
    recovered = store.claim("replacement")
    assert recovered["job_id"] == prior["job_id"]
    assert recovered["fencing_token"] == prior["fencing_token"] + 1
    finish(store, recovered, chapter(recovered))


def test_heartbeat_extension_and_failure_propagation(tmp_path):
    store, workflow_id = seeded(tmp_path, 2)
    job = store.claim("owner", lease_seconds=0.15)
    assert store.heartbeat(job["job_id"], job["attempt_id"], job["fencing_token"], lease_seconds=2)
    time.sleep(0.18)
    assert store.reap_expired() == []
    assert store.fail(job["job_id"], job["attempt_id"], job["fencing_token"], "transient", retry=True)
    retry = store.claim("retry")
    # Claim ordering may choose the original sibling first; find the retry separately.
    if retry["job_id"] != job["job_id"]:
        retry = store.claim("retry-next")
    assert retry["job_id"] == job["job_id"]
    assert store.fail(retry["job_id"], retry["attempt_id"], retry["fencing_token"], "permanent")
    assert store.workflow(workflow_id)["root"]["status"] == "FAILED"
    assert store.claim("not-allowed") is None


def test_receipt_must_bind_successful_real_process_metadata_and_output(tmp_path):
    store, _ = seeded(tmp_path, 1)
    job = store.claim("owner")
    output = chapter(job)
    valid = receipt(output, job=job)
    for patch in ({"pid": 0}, {"pid": True}, {"exit_code": 1}, {"session_id": ""},
                  {"provider": "pretend-codex"}, {"output_sha256": "0" * 64}):
        with pytest.raises(ValueError, match="receipt"):
            store.complete(job["job_id"], job["attempt_id"], job["fencing_token"], output, {**valid, **patch})
    assert store.get(job["job_id"])["status"] == "IN_PROGRESS"


def test_context_is_persisted_in_sqlite_and_inherited_without_hint_files(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    workflow = store.submit("Objective", ["REQ-1"], 1)
    xml = "<oracle_directive>Preserve evidence</oracle_directive>"
    watermark = hashlib.sha256(xml.encode()).hexdigest()
    store.set_context(workflow["workflow_id"], xml, watermark)
    claimed = store.claim("manifest-worker")
    assert [job["job_id"] for job in store.active_jobs()] == [claimed["job_id"]]
    assert claimed["context"]["xml"] == xml
    assert claimed["context"]["watermark"] == watermark
    assert JobStore(store.path).get_context(claimed["job_id"])["xml"] == xml
    assert not list(tmp_path.glob("*.md"))


def test_manifest_positions_are_immutable_and_preserve_original_order(tmp_path):
    store, workflow_id = seeded(tmp_path, 6)
    chapters = [j for j in store.workflow(workflow_id)["jobs"] if j["kind"] == "CHAPTER_DRAFT"]
    assert {j["chapter_id"]: j["payload"]["chapter_index"] for j in chapters} == {
        f"ch-{index}": index for index in range(6)
    }
    modified = {**chapters[0]["payload"], "chapter_index": 99}
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="manifest chapter position is immutable"):
            conn.execute("UPDATE pipeline_jobs SET payload_json=? WHERE job_id=?",
                         (canonical_json(modified), chapters[0]["job_id"]))


def test_cancel_fences_active_pending_and_barrier_without_losing_completed_output(tmp_path):
    store, workflow_id = seeded(tmp_path, 3)
    completed = store.claim("completed-worker")
    finish(store, completed, chapter(completed))
    active = store.claim("still-running")
    artifact_before = store.workflow(workflow_id)["artifacts"]
    cancelled = store.cancel_workflow(workflow_id)
    assert [job["job_id"] for job in cancelled] == [active["job_id"]]
    current = store.workflow(workflow_id)
    assert current["root"]["status"] == "FAILED"
    assert all(job["status"] in {"COMPLETED", "FAILED"} for job in current["jobs"])
    assert current["artifacts"] == artifact_before
    assert store.get(active["job_id"])["fencing_token"] > active["fencing_token"]
    assert store.heartbeat(active["job_id"], active["attempt_id"], active["fencing_token"]) is False
    with pytest.raises(ValueError, match="Stale"):
        finish(store, active, chapter(active))
    assert store.claim("after-stop") is None
    assert store.cancel_workflow(workflow_id) == []


def test_excluded_expired_attempt_is_not_reclaimed_while_other_work_can_start(tmp_path):
    store, _ = seeded(tmp_path, 2)
    cleaning = store.claim("old-worker", lease_seconds=0.06)
    time.sleep(0.09)
    unrelated = store.claim("new-worker", exclude_job_ids=[cleaning["job_id"]])
    assert unrelated is not None and unrelated["job_id"] != cleaning["job_id"]
    assert store.get(cleaning["job_id"])["status"] == "PENDING_RETRY"
    assert store.claim("still-cleaning", exclude_job_ids=[cleaning["job_id"]]) is None
    retry = store.claim("cleanup-finished")
    assert retry["job_id"] == cleaning["job_id"]
    assert retry["fencing_token"] > cleaning["fencing_token"]


def test_concurrent_identity_binding_allows_only_one_chapter_per_workflow(tmp_path):
    store, workflow_id = seeded(tmp_path, 6)
    barrier = threading.Barrier(4)

    def competing_claim(index):
        local = JobStore(store.path)
        barrier.wait(timeout=5)
        return local.claim(f"competing-{index}", worker_slot="same-isolated-account")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = [job for job in pool.map(competing_claim, range(4)) if job is not None]
    assert len(claimed) == 1
    assert claimed[0]["worker_slot"] == "same-isolated-account"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM pipeline_worker_ownership WHERE workflow_id=?", (workflow_id,)).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE pipeline_worker_ownership SET slot='another-account'")
    finish(store, claimed[0], chapter(claimed[0]))
    assert store.claim("cannot-read-sibling", worker_slot="same-isolated-account") is None
    assert JobStore(store.path).get(claimed[0]["job_id"])["worker_slot"] == "same-isolated-account"


def test_retry_keeps_original_identity_and_unslotted_call_cannot_steal_it(tmp_path):
    store, _ = seeded(tmp_path, 2)
    first = store.claim("worker-a", worker_slot="isolated-a")
    assert store.fail(first["job_id"], first["attempt_id"], first["fencing_token"], "transient failure", retry=True)
    sibling = store.claim("worker-b", worker_slot="isolated-b")
    assert sibling["job_id"] != first["job_id"]
    assert store.claim("b-cannot-steal", worker_slot="isolated-b") is None
    assert store.claim("unslotted-cannot-steal") is None
    retry = JobStore(store.path).claim("worker-a-restarted", worker_slot="isolated-a")
    assert retry["job_id"] == first["job_id"]
    assert retry["worker_slot"] == "isolated-a"
    assert retry["attempt_id"] != first["attempt_id"]
    assert retry["fencing_token"] > first["fencing_token"]


def test_worker_slot_and_exclusion_parameters_are_validated(tmp_path):
    store, _ = seeded(tmp_path, 1)
    with pytest.raises(ValueError, match="worker_slot"):
        store.claim("owner", worker_slot="../unsafe slot")
    with pytest.raises(ValueError, match="iterable"):
        store.claim("owner", exclude_job_ids="not-a-list")


def test_three_expired_leases_exhaust_persistent_budget_and_block_barrier(tmp_path):
    store, workflow_id = seeded(tmp_path, 2)
    original_id = None
    for attempt in range(1, 4):
        # A caller cannot reset the stored workflow budget merely by reopening.
        store = JobStore(store.path, max_attempts=99)
        job = store.claim("crashing-worker", worker_slot="same-worker", lease_seconds=0.04)
        assert job is not None
        original_id = original_id or job["job_id"]
        assert job["job_id"] == original_id
        assert job["attempts"] == attempt
        assert job["max_attempts"] == 3
        time.sleep(0.07)
        expired = store.reap_expired()
        assert expired[0]["status"] == ("BLOCKED" if attempt == 3 else "PENDING_RETRY")
    result = store.workflow(workflow_id)
    assert result["status"] == "BLOCKED"
    assert all(job["status"] in {"BLOCKED", "COMPLETED"} for job in result["jobs"])
    assert all(job['lease_owner'] is None and job['lease_expires_at'] is None for job in result['jobs'])
    assert store.claim("fourth-attempt", worker_slot="same-worker") is None
    assert "budget exhausted" in store.get(original_id)["error"]
    assert any(event["event"] == "ATTEMPT_BUDGET_EXHAUSTED" for event in store.events(workflow_id))


def test_retry_true_cannot_exceed_attempt_budget_and_expired_fail_reaps(tmp_path):
    store = JobStore(tmp_path / "jobs.db", max_attempts=2)
    workflow_id = store.submit("Objective", ["REQ-1"], 1)["workflow_id"]
    first = store.claim("first-attempt")
    assert store.fail(first["job_id"], first["attempt_id"], first["fencing_token"], "retry request", retry=True)
    last = store.claim("last-attempt", lease_seconds=0.04)
    time.sleep(0.07)
    assert store.fail(last["job_id"], last["attempt_id"], last["fencing_token"], "expired retry", retry=True) is False
    assert store.get(last["job_id"])["status"] == "BLOCKED"
    assert store.workflow(workflow_id)["status"] == "BLOCKED"
    assert store.claim("cannot-bypass") is None


def test_current_attempt_retry_request_becomes_terminal_at_budget(tmp_path):
    store = JobStore(tmp_path / "jobs.db", max_attempts=2)
    workflow_id = store.submit("Objective", ["REQ-1"], 1)["workflow_id"]
    for number in range(1, 3):
        job = store.claim(f"attempt-{number}")
        assert store.fail(job["job_id"], job["attempt_id"], job["fencing_token"], "transient", retry=True)
        assert store.get(job["job_id"])["status"] == ("PENDING_RETRY" if number == 1 else "BLOCKED")
    assert store.workflow(workflow_id)["status"] == "BLOCKED"


def test_budget_is_inherited_at_scatter_and_old_database_migrates_attempt_count(tmp_path):
    store = JobStore(tmp_path / "jobs.db", max_attempts=2)
    workflow_id = store.submit("Objective", ["REQ-1"], 1)["workflow_id"]
    manifest_job = store.claim("manifest")
    # Recreate the two missing columns in a pre-budget 4.2.2 draft database.
    with sqlite3.connect(store.path) as conn:
        conn.execute("ALTER TABLE pipeline_jobs DROP COLUMN attempts")
        conn.execute("ALTER TABLE pipeline_jobs DROP COLUMN max_attempts")
    migrated = JobStore(store.path, max_attempts=2)
    assert migrated.get(manifest_job["job_id"])["attempts"] == 1
    finish(JobStore(store.path, max_attempts=99), manifest_job, manifest(1))
    assert all(job["max_attempts"] == 2 for job in migrated.workflow(workflow_id)["jobs"])


def test_event_batch_is_bounded_ordered_cross_workflow_and_read_only(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    first = store.submit("First", ["REQ-A"], 1)["workflow_id"]
    second = store.submit("Second", ["REQ-B"], 1)["workflow_id"]
    store.claim("first-owner")
    before = {key: store.workflow(key) for key in (first, second)}
    initial = store.event_batch(0, limit=1)
    assert len(initial) == 1
    following = store.event_batch(initial[0]["id"], limit=2)
    assert len(following) == 2
    remaining = store.event_batch(following[-1]["id"])
    combined = initial + following + remaining
    assert [event["id"] for event in combined] == sorted(event["id"] for event in combined)
    assert {event["workflow_id"] for event in combined} == {first, second}
    assert store.event_batch(combined[-1]["id"]) == []
    assert {key: store.workflow(key) for key in (first, second)} == before
    assert before[first]["events"] == store.events(first)
    for after, limit in ((-1, 1), (True, 1), (0, 0), (0, 1001), (0, True)):
        with pytest.raises(ValueError):
            store.event_batch(after, limit)


def test_context_updates_do_not_feed_oracle_event_stream(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    workflow_id = store.submit("Objective", ["REQ-1"], 1)["workflow_id"]
    before = store.event_batch(0)
    for xml in ("<oracle_directive>First</oracle_directive>", "<oracle_directive>Second</oracle_directive>"):
        store.set_context(workflow_id, xml, artifact_digest(xml))
    assert store.event_batch(0) == before
    assert "Second" in store.get_context(workflow_id)["xml"]


def test_synthesis_coverage_is_controller_computed_hashed_and_records_overlaps(tmp_path):
    store, workflow_id = seeded(tmp_path, 2)
    for owner in ('first', 'second'):
        job = store.claim(owner)
        finish(store, job, chapter(job))
    synthesis = store.claim('synthesis')
    coverage = synthesis['payload']['coverage_report']
    assert coverage['complete'] is True and coverage['gaps'] == []
    assert coverage['overlaps'] == ['REQ-1']
    assert coverage['requirements'] == [{'requirement': 'REQ-1',
        'owning_chapters': ['ch-0', 'ch-1'], 'traced_by_chapters': ['ch-0', 'ch-1'],
        'overlap': True, 'gap': False}]
    assert coverage['chapter_hashes'] == synthesis['payload']['chapter_hashes']
    assert output_digest(coverage) == synthesis['payload']['coverage_report_sha256']
    event = next(item for item in store.events(workflow_id) if item['event'] == 'SYNTHESIS_RELEASED')
    assert event['details']['coverage_report_sha256'] == output_digest(coverage)


@pytest.mark.parametrize('mutation', ['missing', 'empty', 'duplicate', 'unowned', 'oversized'])
def test_manifest_requires_bounded_unique_owned_wbs_before_atomic_scatter(tmp_path, mutation):
    store = JobStore(tmp_path / 'jobs.db')
    workflow = store.submit('Preserve the whole WBS', ['REQ-1'], 2)
    node = store.claim('manifest')
    output = manifest(2)
    first, second = output['chapters']
    if mutation == 'missing':
        first.pop('wbs_tasks_defined')
    elif mutation == 'empty':
        first['wbs_tasks_defined'] = []
    elif mutation == 'duplicate':
        second['wbs_tasks_defined'] = first['wbs_tasks_defined']
    elif mutation == 'unowned':
        first['wbs_tasks_defined'][0]['requirements'] = ['REQ-other']
    else:
        first['wbs_tasks_defined'][0]['description'] = 'x' * 16385
    with pytest.raises(ValueError, match='[Ww][Bb][Ss]'):
        finish(store, node, output)
    assert store.get(node['job_id'])['output'] is None
    assert not any(job['kind'] == 'CHAPTER_DRAFT' for job in store.workflow(workflow['workflow_id'])['jobs'])


def test_structured_wbs_and_full_accepted_output_survive_scatter_gather_and_reopen(tmp_path):
    store, workflow = seeded(tmp_path, 1)
    draft = store.claim('chapter')
    assert draft['payload']['wbs_tasks_defined'] == manifest(1)['chapters'][0]['wbs_tasks_defined']
    output = chapter(draft)
    output['additional_commitment'] = {'retention': 'Keep this unique accepted structured commitment'}
    accepted = finish(store, draft, output)
    reopened = JobStore(store.path)
    synthesis = reopened.claim('synthesis')
    payload = synthesis['payload']
    assert payload['chapters'][0]['accepted_output'] == output
    assert payload['chapters'][0]['output_sha256'] == output_digest(output) == accepted['output_sha256']
    assert payload['chapter_output_hashes'] == {draft['chapter_id']: output_digest(output)}
    assert payload['chapter_hashes'] == {draft['chapter_id']: artifact_digest(output['artifact_text'])}
    assert payload['coverage_report']['chapter_output_hashes'] == payload['chapter_output_hashes']
    assert payload['wbs_tasks_by_chapter'] == {draft['chapter_id']: output['wbs_tasks_defined']}
    finish(reopened, synthesis, synthesis_output(synthesis))
    assert reopened.workflow(workflow)['status'] == 'COMPLETED'
    assert reopened.get(draft['job_id'])['output'] == output


@pytest.mark.parametrize('mutation', ['drop', 'change'])
def test_chapter_cannot_drop_or_change_manifest_wbs_declaration(tmp_path, mutation):
    store, _ = seeded(tmp_path, 1)
    job = store.claim('chapter')
    output = chapter(job)
    if mutation == 'drop':
        output['wbs_tasks_defined'] = output['wbs_tasks_defined'][1:]
    else:
        output['wbs_tasks_defined'][0] = {**output['wbs_tasks_defined'][0], 'description': 'Replaced commitment'}
    with pytest.raises(ValueError, match='preserve every owned manifest'):
        finish(store, job, output)
    assert store.get(job['job_id'])['output'] is None


@pytest.mark.parametrize('key', ['chapter_output_hashes', 'coverage_report_sha256', 'wbs_tasks_by_chapter'])
@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_synthesis_cannot_omit_or_change_accepted_output_commitments(tmp_path, key, mutation):
    store, _ = seeded(tmp_path, 1)
    draft = store.claim('chapter')
    finish(store, draft, chapter(draft))
    node = store.claim('synthesis')
    output = synthesis_output(node)
    if mutation == 'missing':
        output.pop(key)
    else:
        output[key] = 'wrong' if key == 'coverage_report_sha256' else {}
    with pytest.raises(ValueError, match='commitments'):
        finish(store, node, output)
    assert store.get(node['job_id'])['output'] is None


def test_synthesis_rechecks_gather_payload_against_immutable_accepted_outputs(tmp_path):
    store, _ = seeded(tmp_path, 1)
    draft = store.claim('chapter')
    finish(store, draft, chapter(draft))
    node = store.claim('synthesis')
    changed = json.loads(canonical_json(node['payload']))
    changed['chapters'][0]['accepted_output']['wbs_tasks_defined'][0]['description'] = 'Lost original task'
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE pipeline_jobs SET payload_json=? WHERE job_id=?', (canonical_json(changed), node['job_id']))
    with pytest.raises(ValueError, match='input commitments'):
        finish(store, node, synthesis_output(node))
    assert store.get(draft['job_id'])['output']['wbs_tasks_defined'][0]['description'] == 'Implement assigned requirement'


@pytest.mark.parametrize('kind', ['CHAPTER_DRAFT', 'SYNTHESIS'])
def test_active_legacy_document_attempt_gets_explicit_compatibility_hold_without_rewriting_outputs(tmp_path, kind):
    from cochem_pipeline.document_governance import DocumentCommitmentHold
    store, workflow = seeded(tmp_path, 1)
    draft = store.claim('chapter')
    if kind == 'SYNTHESIS':
        finish(store, draft, chapter(draft))
        node = store.claim('synthesis')
        output = synthesis_output(node)
        removed = 'chapter_output_hashes'
    else:
        node, output, removed = draft, chapter(draft), 'wbs_tasks_defined'
    legacy_payload = {key: value for key, value in node['payload'].items() if key != removed}
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE pipeline_jobs SET payload_json=? WHERE job_id=?', (canonical_json(legacy_payload), node['job_id']))
        original_outputs = conn.execute('SELECT * FROM pipeline_outputs ORDER BY job_id').fetchall()
    before = store.get(node['job_id'])
    with pytest.raises(DocumentCommitmentHold, match='preserving accepted outputs and budgets') as error:
        finish(store, node, output)
    assert store.fail(node['job_id'], node['attempt_id'], node['fencing_token'], str(error.value),
                      retry=True, category=error.value.category, hold_scope=error.value.hold_scope)
    after = store.get(node['job_id'])
    assert after['routing']['failure_count'] == before['routing']['failure_count']
    assert after['routing']['dispatches'] == before['routing']['dispatches']
    assert after['attempts'] == before['attempts']
    assert store.claim('must-not-launch-incomplete-plan') is None
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT * FROM pipeline_outputs ORDER BY job_id').fetchall() == original_outputs


def test_document_governing_requirements_are_captured_once_and_inherited(tmp_path):
    governance = {'specification_id': 'COCHEM-4.2.7', 'specification_sha256': 'a' * 64,
                  'owner_amendments': ['all-model-jobs-use-Chapter-06']}
    store = JobStore(tmp_path/'governed.db', governing_requirements=governance)
    workflow = store.submit('One bounded chapter', ['REQ-1'], 1, workflow_id='governed')
    governance['owner_amendments'].clear()
    current = JobStore(store.path, governing_requirements={**governance, 'specification_sha256': 'b' * 64})
    again = current.submit('One bounded chapter', ['REQ-1'], 1, workflow_id='governed')
    assert again == workflow
    assert all(job['payload']['governing_requirements']['specification_sha256'] == 'a' * 64 for job in again['jobs'])
    job = current.claim('manifest')
    finish(current, job, manifest(1))
    child = current.claim('chapter')
    assert child['payload']['governing_requirements']['owner_amendments'] == ['all-model-jobs-use-Chapter-06']
    assert child['route'] is not None


def test_legacy_single_model_synthesis_is_amended_without_mutating_captured_history(tmp_path):
    from cochem_pipeline.routing import load_routing_policy, score_task
    # Historical fixed-model synthesis used catalogue v1. Do not derive its
    # identity from today's catalogue, where the medium Gemini route is Flash.
    captured = load_routing_policy({'policy_version': 1})
    store = JobStore(tmp_path / 'job_board.db', routing_policy=captured)
    workflow_id = store.submit('Create an SRS and WBS', ['REQ-1'], 1)['workflow_id']
    finish(store, store.claim('manifest-worker'), manifest(1))
    job = store.claim('chapter')
    finish(store, job, chapter(job))
    synthesis = next(item for item in store.workflow(workflow_id)['jobs'] if item['kind'] == 'SYNTHESIS')
    old_candidate = next(value.as_dict() for value in captured.candidates(4) if value.provider == 'gemini')
    old = [old_candidate]
    score = score_task('SYNTHESIS', synthesis['payload'])
    # A physical historical database record, not a native-model execution.
    with store._write() as conn:
        conn.execute('''INSERT INTO pipeline_routing_jobs(job_id,policy_json,score_json,candidates_json,
            cursor,cycle,failure_count,dispatches,state,created_at) VALUES(?,?,?,?,1,2,1,3,'READY',?)''',
            (synthesis['job_id'], canonical_json(captured.as_dict()), canonical_json(score), canonical_json(old), time.time()))
    # Reopen the real database under the current deployment catalogue. The
    # amendment must retain the old authority, ordering and spent budgets.
    store = JobStore(store.path)
    dispatched = store.claim('new-controller')
    assert dispatched['route']['candidate_index'] == 0
    assert dispatched['route']['model'] == captured.candidates(score['score'], 'SYNTHESIS')[0].model
    assert dispatched['routing']['captured_candidates'] == old
    assert dispatched['routing']['dispatches'] == 4
    assert dispatched['routing']['failure_count'] == 1 and dispatched['routing']['cycle'] == 2
    with sqlite3.connect(store.path) as conn:
        assert json.loads(conn.execute('SELECT policy_json FROM pipeline_routing_workflows WHERE workflow_id=?',
                                       (workflow_id,)).fetchone()[0]) == captured.as_dict()
        assert json.loads(conn.execute('SELECT candidates_json FROM pipeline_routing_jobs WHERE job_id=?',
                                       (synthesis['job_id'],)).fetchone()[0]) == old
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE pipeline_routing_amendments SET reason='erase-history'")
    amendments = [event for event in store.events(workflow_id) if event['event'] == 'UNIVERSAL_ROUTING_AMENDED']
    assert len(amendments) == 1 and amendments[0]['details']['preserved_dispatches'] == 3


def test_manual_preflight_is_one_routed_readonly_model_job_with_finite_fallback(tmp_path):
    store = JobStore(tmp_path/'preflight.db')
    workflow = store.submit_preflight('probe-one')
    assert store.submit_preflight('probe-one') == workflow
    native = [job for job in workflow['jobs'] if job['kind'] != 'MACRO_PLANNING_REQUEST']
    assert len(native) == 1 and native[0]['kind'] == 'PREFLIGHT_REQUEST'
    assert native[0]['routing']['max_dispatches'] == 3
    assert 0 < native[0]['routing']['expires_at'] - time.time() <= 300
    first = store.claim('probe', worker_slot='identity')
    assert first['route']['provider'] == 'gemini'
    assert store.fail(first['job_id'], first['attempt_id'], first['fencing_token'],
                      'Native quota unavailable', retry=True, category='quota')
    second = store.claim('probe-fallback', worker_slot='identity')
    assert second['route']['provider'] == 'claude'
    with pytest.raises(ValueError, match='exact bounded'):
        finish(store, second, {'ready': True, 'fabricated_all_providers_verified': True})
    finish(store, second, {'ready': True})
    final = store.workflow('probe-one')
    assert final['status'] == 'COMPLETED'
    assert len(final['jobs']) == 2 and final['artifacts'] == []
    assert store.claim('extra-paid-job') is None
