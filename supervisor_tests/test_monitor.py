"""Real SQLite/heartbeat-file observations without pipeline imports or mocks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cochem_supervisor.monitor import classify_error, read_observation, redact_diagnostic

NOW = 10000


def board(root: Path) -> Path:
    path = root / "job_board.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE pipeline_jobs(job_id TEXT PRIMARY KEY,kind TEXT,status TEXT,attempts INTEGER,
            updated_at REAL,lease_expires_at REAL,error TEXT,payload_json TEXT);
        CREATE TABLE pipeline_events(id INTEGER PRIMARY KEY,event TEXT,timestamp REAL,details_json TEXT);
        INSERT INTO pipeline_events VALUES(1,'CREATED',9990,'{"secret":"EVENT-PAYLOAD-MUST-STAY-PRIVATE"}');
        """)
    return path


def heartbeat(root: Path, *, timestamp=NOW, capacity=4, pid=42, sequence=2, active=0):
    (root / "supervisor_status.json").write_text(json.dumps({
        "instance_id": "physical-test-heartbeat", "pid": pid, "process_started_at": 1,
        "sequence": sequence, "timestamp": timestamp, "version": "4.2.3",
        "status": {"hardware": {"capacity": capacity}, "active_count": active,
                   "token": "HEARTBEAT-SECRET-MUST-NOT-LEAK"},
    }), encoding="utf-8")


def insert(path, *, job_id="job-1", status="FAILED", error="TypeError: invalid scheduler state", attempts=3,
           updated_at=NOW - 5, lease_expires_at=None, kind="CHAPTER_DRAFT"):
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO pipeline_jobs VALUES(?,?,?,?,?,?,?,?)", (
            job_id, kind, status, attempts, updated_at, lease_expires_at, error,
            '{"prompt":"RAW-PROMPT-MUST-STAY-PRIVATE","artifact_text":"PRIVATE-CHAPTER"}',
        ))


def test_healthy_idle_board_is_observed_without_creating_or_modifying_files(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["state"] == "healthy"
    assert result["health"]["database"] == "readable"
    assert result["incidents"] == []
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}


def test_missing_files_are_reported_without_creating_database_or_directories(tmp_path):
    root = tmp_path / "not-yet-installed"
    result = read_observation(root, now=NOW)
    assert result["health"]["heartbeat"] == "missing"
    assert result["health"]["database"] == "missing"
    assert any(item["category"] == "code" and item["repairable"] for item in result["incidents"])
    assert not root.exists()


def test_stale_heartbeat_fingerprint_ignores_pid_sequence_and_timestamps(tmp_path):
    board(tmp_path)
    heartbeat(tmp_path, timestamp=NOW-100, pid=91)
    first = read_observation(tmp_path, now=NOW)["incidents"][0]
    heartbeat(tmp_path, timestamp=NOW-200, pid=142, sequence=9)
    second = read_observation(tmp_path, now=NOW+10)["incidents"][0]
    assert first["fingerprint"] == second["fingerprint"]
    assert first["repairable"] is True
    assert first["evidence"] != second["evidence"]


@pytest.mark.parametrize("message,category,repairable", [
    ("Authentication failed; please log in", "auth", False),
    ("TypeError: subscription login unverified", "auth", False),
    ("HTTP 429 quota exceeded", "quota", False),
    ("Provider overloaded: service unavailable", "provider", False),
    ("ConnectionError: connection refused", "provider", False),
    ("MemoryError: cannot allocate memory", "resource", False),
    ("sqlite3.OperationalError: database is locked", "resource", False),
    ("PermissionError: Access denied", "configuration", False),
    ("unknown option --headless", "compatibility", True),
    ("unrecognized arguments: --json", "compatibility", True),
    ("no such column: job.attempts", "compatibility", True),
    ("AttributeError: Scheduler has no attribute next_job", "code", True),
    ("ModuleNotFoundError: No module named scheduler", "code", True),
    ("Unknown provider response with unspecified cause", "configuration", False),
])
def test_error_classes_never_authorize_model_spend_for_external_blockers(message, category, repairable):
    result = classify_error(message)
    assert result["category"] == category
    assert result["repairable"] is repairable


def test_actual_exception_message_without_class_prefix_still_identifies_code_error():
    try:
        object().missing_scheduler_method()
    except AttributeError as error:
        classification = classify_error(str(error))
    assert classification["category"] == "code"
    assert classification["repairable"] is True


def test_repeated_code_failure_exposes_bounded_safe_metadata_not_prompts(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, error="TypeError: scheduler state invalid password='sensitive password' api_key=sk-01234567890123456789")
    result = read_observation(tmp_path, now=NOW)
    assert len(result["incidents"]) == 1
    incident = result["incidents"][0]
    assert incident["category"] == "code" and incident["repairable"] is True
    assert incident["evidence"]["latest_failure_at"] == NOW-5
    serialized = json.dumps(result)
    for secret in ("sensitive password", "sk-01234567890123456789", "RAW-PROMPT", "PRIVATE-CHAPTER", "EVENT-PAYLOAD", "HEARTBEAT-SECRET"):
        assert secret not in serialized


def test_one_code_failure_does_not_authorize_repair_until_repeated(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, attempts=1)
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["state"] == "blocked"
    assert result["incidents"][0]["repairable"] is False


def test_three_matching_jobs_are_grouped_without_job_identity_in_fingerprint(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    for index in range(3):
        insert(path, job_id=f"chapter-{index}", attempts=1, error=f"TypeError: broken scheduler job_id=chapter-{index} pid={index+91}")
    result = read_observation(tmp_path, now=NOW)
    assert len(result["incidents"]) == 1
    incident = result["incidents"][0]
    assert incident["repairable"] is True
    assert incident["evidence"]["observed_jobs"] == 3


def test_new_failure_changes_evidence_time_without_resetting_fingerprint(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, error="TypeError: invalid state attempt_id=ca8b8c1d6c2748e2bba201c83f4e8b55 pid=100 timestamp=9990")
    original = read_observation(tmp_path, now=NOW)["incidents"][0]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE pipeline_jobs SET updated_at=?,error=?", (NOW, "TypeError: invalid state attempt_id=939f70afc17b4ba9b865fed32f4fba42 pid=200 timestamp=10000"))
    current = read_observation(tmp_path, now=NOW)["incidents"][0]
    assert original["fingerprint"] == current["fingerprint"]
    assert current["evidence"]["latest_failure_at"] > original["evidence"]["latest_failure_at"]


@pytest.mark.parametrize("error,category", [
    ("subscription login unverified", "auth"),
    ("usage limit quota exceeded", "quota"),
    ("provider service unavailable", "provider"),
    ("no space left on device", "resource"),
    ("Unrecognized opaque problem", "configuration"),
])
def test_old_retry_waiting_on_external_blocker_is_not_misclassified_as_code_stall(tmp_path, error, category):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="PENDING_RETRY", error=error, updated_at=1)
    result = read_observation(tmp_path, now=NOW)
    assert {item["category"] for item in result["incidents"]} == {category}
    assert not any(item["repairable"] for item in result["incidents"])


@pytest.mark.parametrize("capacity,active", [(0, 0), (4, 4)])
def test_stalled_pending_work_waiting_for_host_capacity_does_not_spend_on_repair(tmp_path, capacity, active):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=capacity, active=active)
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_legacy_active_list_retains_capacity_hold(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=4, active=4)
    heartbeat_path = tmp_path / "supervisor_status.json"
    data = json.loads(heartbeat_path.read_text())
    data["status"].pop("active_count")
    data["status"]["active"] = [{"pid": value} for value in range(4)]
    heartbeat_path.write_text(json.dumps(data), encoding="utf-8")
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_current_active_count_takes_precedence_over_legacy_list(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=4, active=4)
    heartbeat_path = tmp_path / "supervisor_status.json"
    data = json.loads(heartbeat_path.read_text())
    data["status"]["active"] = []
    heartbeat_path.write_text(json.dumps(data), encoding="utf-8")
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"


def test_expired_execution_is_detected_from_real_database_lease_metadata(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="IN_PROGRESS", error=None, lease_expires_at=NOW-40)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "code"
    assert "lease expired" in result["incidents"][0]["summary"]
    assert result["incidents"][0]["evidence"]["expired_seconds"] == 40


def test_missing_schema_is_a_compatibility_incident(tmp_path):
    with sqlite3.connect(tmp_path / "job_board.db") as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    heartbeat(tmp_path)
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["database"] == "unreadable"
    assert result["incidents"][0]["category"] == "compatibility"


def test_real_sqlite_write_lock_is_a_resource_hold(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_wal_committed_failure_is_seen_without_immutable_stale_reads(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO pipeline_jobs VALUES('wal-job','CHAPTER_DRAFT','FAILED',3,9999,NULL,'TypeError: bad scheduler','{}')")
        conn.commit()
        assert Path(str(path)+"-wal").is_file()
        result = read_observation(tmp_path, now=NOW)
        assert result["incidents"][0]["repairable"] is True
        assert result["health"]["job_counts"] == {"FAILED": 1}
    finally:
        conn.close()


@pytest.mark.parametrize("content", ["{", "[]", '{"timestamp":10000}', "x"*65537])
def test_malformed_heartbeat_never_claims_healthy(tmp_path, content):
    board(tmp_path)
    (tmp_path / "supervisor_status.json").write_text(content, encoding="utf-8")
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["heartbeat"] == "invalid"
    assert result["health"]["state"] != "healthy"


def test_diagnostic_redaction_covers_authorization_urls_and_payload_echoes():
    diagnostic = redact_diagnostic('TypeError: Authorization: Bearer bearer-secret token="token secret" '
                                   'https://name:password@example.com/?api_key=url-secret '
                                   'prompt="private prompt" payload={"x":3}')
    for secret in ("bearer-secret", "token secret", "name:password", "url-secret", "private prompt"):
        assert secret not in diagnostic
    assert "TypeError" in diagnostic


def test_payload_echoes_cannot_authorize_code_repair_or_expose_nested_request_data():
    error = 'Unexpected failure payload={"details":"ordinary", "next":"TypeError: secret request text"}'
    classification = classify_error(error)
    assert classification["repairable"] is False
    assert "secret request text" not in classification["diagnostic"]


@pytest.mark.parametrize("settings", [{"heartbeat_timeout": 0}, {"stall_timeout": -1}, {"now": float("nan")}, {"repeated_failures": True}])
def test_invalid_monitor_limits_are_rejected(tmp_path, settings):
    with pytest.raises(ValueError):
        read_observation(tmp_path, **settings)
