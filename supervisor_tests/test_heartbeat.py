"""Physical scheduler heartbeat and persistent reduced-attempt acceptance checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline.heartbeat import Heartbeat
from cochem_pipeline.service import ControlClient, ControlServer
from cochem_pipeline.store import JobStore, output_digest
from cochem_supervisor.monitor import read_observation


def test_completed_tick_writes_atomic_real_process_identity_and_throttles(tmp_path):
    heartbeat = Heartbeat(tmp_path, "4.2.3", interval=2)
    assert not heartbeat.path.exists()
    assert heartbeat.completed_tick({"capacity": 4, "active_count": 2}, now=1000)
    first = json.loads(heartbeat.path.read_text())
    assert first["pid"] == os.getpid()
    assert first["instance_id"] == heartbeat.instance_id
    assert first["sequence"] == 1 and first["timestamp"] == 1000
    assert first["source_root"] == str(Path(__file__).resolve().parents[1])
    assert first["status"] == {"capacity": 4, "active_count": 2}
    original_bytes, stamp = heartbeat.path.read_bytes(), heartbeat.path.stat().st_mtime_ns
    assert heartbeat.completed_tick({"capacity": 0}, now=1001) is False
    assert heartbeat.path.read_bytes() == original_bytes
    assert heartbeat.path.stat().st_mtime_ns == stamp
    assert heartbeat.completed_tick({"capacity": 3}, now=1002)
    second = json.loads(heartbeat.path.read_text())
    assert second["sequence"] == 3
    assert second["status"] == {"capacity": 3}
    assert not list(tmp_path.glob(".heartbeat-*.tmp"))


def test_new_heartbeat_instance_is_distinguishable_after_restart(tmp_path):
    old = Heartbeat(tmp_path, "4.2.2")
    old.completed_tick({}, now=1000)
    replacement = Heartbeat(tmp_path, "4.2.3")
    replacement.completed_tick({}, now=1001)
    stored = json.loads(replacement.path.read_text())
    assert replacement.instance_id != old.instance_id
    assert stored["instance_id"] == replacement.instance_id
    assert stored["sequence"] == 1 and stored["version"] == "4.2.3"


def test_reading_actual_monitor_health_cannot_advance_scheduler_heartbeat(tmp_path):
    JobStore(tmp_path / "job_board.db")
    heartbeat = Heartbeat(tmp_path, "4.2.3")
    current = time.time()
    heartbeat.completed_tick({"capacity": 4, "active_count": 0, "quarantined_count": 0}, now=current)
    before, stamp = heartbeat.path.read_bytes(), heartbeat.path.stat().st_mtime_ns
    for observed in (current + 1, current + 2, current + 40):
        health = read_observation(tmp_path, now=observed)
        assert health["health"]["database"] == "readable"
    assert health["health"]["heartbeat"] == "stale"
    assert heartbeat.path.read_bytes() == before
    assert heartbeat.path.stat().st_mtime_ns == stamp
    assert heartbeat.sequence == 1


def test_serialization_failure_keeps_previous_heartbeat_and_cleans_temporary_file(tmp_path):
    heartbeat = Heartbeat(tmp_path, "4.2.3", interval=0)
    heartbeat.completed_tick({"capacity": 4}, now=1000)
    original = heartbeat.path.read_bytes()
    with pytest.raises(ValueError):
        heartbeat.completed_tick({"invalid": float("nan")}, now=1001)
    assert heartbeat.path.read_bytes() == original
    assert not list(tmp_path.glob(".heartbeat-*.tmp"))


def test_concurrent_readers_only_observe_complete_json_documents(tmp_path):
    heartbeat = Heartbeat(tmp_path, "4.2.3", interval=0)
    heartbeat.completed_tick({"padding": "x" * 5000}, now=1000)
    done = threading.Event()
    observations = []
    failures = []

    def read():
        while not done.is_set():
            try:
                data = json.loads(heartbeat.path.read_text())
                observations.append(data["sequence"])
            except BaseException as exc:
                failures.append(exc)
                done.set()

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for index in range(20):
            heartbeat.completed_tick({"padding": "x" * (5000 + index)}, now=1001 + index)
    finally:
        done.set()
        reader.join(timeout=5)
    assert observations and not failures
    assert not reader.is_alive()
    assert json.loads(heartbeat.path.read_text())["sequence"] == 21


def test_workflow_attempt_override_can_only_lower_budget_and_remains_immutable(tmp_path):
    store = JobStore(tmp_path / "job_board.db", max_attempts=3)
    first = store.submit("Acceptance smoke", ["REQ-1"], 1, workflow_id="one-attempt", max_attempts=1)
    assert all(job["max_attempts"] == 1 for job in first["jobs"])
    assert store.submit("Acceptance smoke", ["REQ-1"], 1, workflow_id="one-attempt", max_attempts=1) == first
    assert store.submit("Acceptance smoke", ["REQ-1"], 1, workflow_id="one-attempt") == first
    for override in (0, 4, True, 1.5):
        with pytest.raises(ValueError, match="only lower"):
            store.submit("Bad override", ["REQ-1"], 1, max_attempts=override)
    with pytest.raises(ValueError, match="immutable"):
        store.submit("Acceptance smoke", ["REQ-1"], 1, workflow_id="one-attempt", max_attempts=2)
    job = store.claim("smoke-worker")
    assert store.fail(job["job_id"], job["attempt_id"], job["fencing_token"], "single-attempt failure", retry=True)
    assert store.workflow("one-attempt")["status"] == "BLOCKED"
    assert store.claim("second-not-permitted") is None


def test_lowered_workflow_budget_is_inherited_by_scattered_chapters(tmp_path):
    store = JobStore(tmp_path / "job_board.db", max_attempts=3)
    workflow = store.submit("SRS", ["REQ-1"], 1, max_attempts=1)
    manifest = store.claim("manifest-worker")
    output = {"chapters": [{"chapter_id": "chapter-1", "title": "Requirements", "requirements": ["REQ-1"]}]}
    # Storage-contract input; this test does not invoke or claim a model run.
    route=manifest["route"]
    receipt = {"provider": route["provider"], "requested_model":route["model"],
               "requested_effort":route.get("reasoning_effort"),"route_reservation_id":route["reservation_id"],
               **{key:manifest[key] for key in ("attempt_id","fencing_token","worker_slot","job_id","workflow_id")},
               "pid": os.getpid(), "exit_code": 0,
               "session_id": "storage-contract-test", "output_sha256": output_digest(output)}
    store.complete(manifest["job_id"], manifest["attempt_id"], manifest["fencing_token"], output, receipt)
    chapters = [job for job in store.workflow(workflow["workflow_id"])["jobs"] if job["kind"] == "CHAPTER_DRAFT"]
    assert len(chapters) == 1 and chapters[0]["max_attempts"] == 1


def test_real_authenticated_http_submit_can_lower_but_never_raise_attempt_budget(tmp_path):
    # This composition supplies only the real HTTP/SQLite submission boundary.
    # It does not run or stand in for a native Windows scheduler/provider.
    store = JobStore(tmp_path / "job_board.db", max_attempts=3)
    controller = SimpleNamespace(config=SimpleNamespace(port=0, workers={"slot-0": {}}), store=store)
    token_file = tmp_path / "controller.token"
    token = secrets.token_urlsafe(48)
    token_file.write_text(token, encoding="utf-8")
    server = ControlServer(controller, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ControlClient(server.server_address[1], token_file)
        request = {"objective": "Single-attempt live smoke contract", "requirements": ["REQ-1"],
                   "chapter_count": 1, "workflow_id": "http-smoke", "max_attempts": 1}
        submitted = client.call("/submit", request)
        assert all(job["max_attempts"] == 1 for job in submitted["jobs"])
        with pytest.raises(RuntimeError, match="only lower"):
            client.call("/submit", {**request, "workflow_id": "over-budget", "max_attempts": 4})
        with pytest.raises(RuntimeError, match="immutable"):
            client.call("/submit", {**request, "max_attempts": 2})
        assert client.call("/workflow/http-smoke")["root"]["max_attempts"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
