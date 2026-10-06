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


def receipt(output, provider="codex"):
    # A physical Python process hashes the exact bytes; it is explicitly NOT an LLM.
    proc = subprocess.run(
        [sys.executable, "-c", "import hashlib,json,os,sys; raw=sys.stdin.buffer.read(); print(json.dumps({'pid':os.getpid(),'output_sha256':hashlib.sha256(raw).hexdigest()}))"],
        input=canonical_json(output), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return {**json.loads(proc.stdout), "provider": provider, "exit_code": proc.returncode,
            "session_id": "deterministic-contract-test", "execution_kind": "python-storage-contract-test"}


def finish(store, job, output, provider="codex"):
    return store.complete(job["job_id"], job["attempt_id"], job["fencing_token"], output, receipt(output, provider))


def manifest(count=6):
    return {"chapters": [{"chapter_id": f"ch-{index}", "title": f"Chapter {index}",
                           "requirements": ["REQ-1"]} for index in range(count)]}


def chapter(job):
    return {"chapter_id": job["chapter_id"], "requirements_traced": job["payload"]["requirements"],
            "wbs_tasks_defined": [{"task_id": f"{job['chapter_id']}-01", "description": "Implement and verify requirement"}],
            "artifact_uri": f"db://{job['workflow_id']}/{job['chapter_id']}",
            "artifact_text": f"# {job['payload']['title']}\nRequirement: REQ-1\nVerified structured chapter."}


def seeded(tmp_path, count=6):
    store = JobStore(tmp_path / "job_board.db")
    workflow = store.submit("Create an SRS and WBS", ["REQ-1"], count)
    job = store.claim("manifest-worker")
    finish(store, job, manifest(count))
    return store, workflow["workflow_id"]


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
    synthesis = store.claim("gemini-synthesis")
    assert synthesis["kind"] == "SYNTHESIS"
    assert len(synthesis["payload"]["chapter_hashes"]) == 6
    output = {"artifact_text": "# Complete SRS/WBS", "chapter_hashes": synthesis["payload"]["chapter_hashes"]}
    finish(store, synthesis, output, "gemini")
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
                          second["chapter_id"], canonical_json(wrong), "{}", output_digest(wrong), time.time()))
    assert store.workflow(workflow_id)["artifacts"] == []


def test_artifacts_and_outputs_immutable_and_identical_completion_idempotent(tmp_path):
    store, _ = seeded(tmp_path, 1)
    job = store.claim("chapter-owner")
    output = chapter(job)
    process_receipt = receipt(output)
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


def test_synthesis_rejects_wrong_hashes_or_non_gemini_receipt(tmp_path):
    store, workflow_id = seeded(tmp_path, 1)
    drafting = store.claim("chapter-owner")
    finish(store, drafting, chapter(drafting))
    synthesis = store.claim("synthesis-owner")
    output = {"artifact_text": "Complete document", "chapter_hashes": synthesis["payload"]["chapter_hashes"]}
    with pytest.raises(ValueError, match="Gemini"):
        finish(store, synthesis, output, "claude")
    with pytest.raises(ValueError, match="hashes"):
        finish(store, synthesis, {**output, "chapter_hashes": {drafting["chapter_id"]: "f" * 64}}, "gemini")
    assert store.workflow(workflow_id)["root"]["status"] == "IN_PROGRESS"
    finish(store, synthesis, output, "gemini")
    assert store.workflow(workflow_id)["status"] == "COMPLETED"


def test_crash_expiry_reclaims_lease_and_fences_old_attempt(tmp_path):
    store, _ = seeded(tmp_path, 1)
    old = store.claim("old-process", lease_seconds=0.08)
    output = chapter(old)
    process_receipt = receipt(output)
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
        with pytest.raises(sqlite3.IntegrityError, match="stale"):
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
    valid = receipt(output)
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


def test_three_expired_leases_exhaust_persistent_budget_and_fail_barrier(tmp_path):
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
        assert expired[0]["status"] == ("FAILED" if attempt == 3 else "PENDING_RETRY")
    result = store.workflow(workflow_id)
    assert result["status"] == "FAILED"
    assert all(job["status"] in {"FAILED", "COMPLETED"} for job in result["jobs"])
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
    assert store.get(last["job_id"])["status"] == "FAILED"
    assert store.workflow(workflow_id)["status"] == "FAILED"
    assert store.claim("cannot-bypass") is None


def test_current_attempt_retry_request_becomes_terminal_at_budget(tmp_path):
    store = JobStore(tmp_path / "jobs.db", max_attempts=2)
    workflow_id = store.submit("Objective", ["REQ-1"], 1)["workflow_id"]
    for number in range(1, 3):
        job = store.claim(f"attempt-{number}")
        assert store.fail(job["job_id"], job["attempt_id"], job["fencing_token"], "transient", retry=True)
        assert store.get(job["job_id"])["status"] == ("PENDING_RETRY" if number == 1 else "FAILED")
    assert store.workflow(workflow_id)["status"] == "FAILED"


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
    combined = initial + following
    assert [event["id"] for event in combined] == sorted(event["id"] for event in combined)
    assert {event["workflow_id"] for event in combined} == {first, second}
    assert store.event_batch(following[-1]["id"]) == []
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
