"""Durable supervisor state checks with real SQLite, threads and process death.

No repair/model execution is simulated. These tests exercise reservations,
budgets, evidence and lease ownership using the ledger's public clock argument.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

from cochem_supervisor.state import Ledger


def observed(tmp_path, fingerprint="failure-1", timestamp=1000):
    ledger = Ledger(tmp_path / "supervisor.db")
    ledger.observe(fingerprint, "pipeline_failure", {"reason": "detected fault"}, now=timestamp)
    return ledger


def test_observations_preserve_times_counts_status_and_complete_evidence(tmp_path):
    ledger = Ledger(tmp_path / "supervisor.db")
    evidence = {"trace": "Δ failure\nsecond line", "nested": {"values": [1, False, None]}, "logs": "x" * 9000}
    original = json.loads(json.dumps(evidence))
    first = ledger.observe("fp", "crash", evidence, now=1000)
    evidence["trace"] = "caller changed its local object"
    second = Ledger(ledger.path).observe("fp", "crash", {"trace": "new occurrence"}, now=1030)
    older = ledger.observe("fp", "crash", {"trace": "late observation"}, now=1020)
    assert first["first_seen"] == second["first_seen"] == older["first_seen"] == 1000
    assert second["last_seen"] == older["last_seen"] == 1030
    assert older["occurrences"] == 3
    assert older["status"] == "OPEN"
    assert ledger.get_incident("fp") == older
    with pytest.raises(ValueError, match="Unknown"):
        ledger.get_incident("missing")
    history = ledger.history("fp")
    assert history[0]["details"]["evidence"] == original
    assert history[-1]["details"]["evidence"] == {"trace": "late observation"}
    with pytest.raises(ValueError, match="change category"):
        ledger.observe("fp", "unrelated_update", {}, now=1040)
    assert ledger.list_incidents()[0]["occurrences"] == 3
    with ledger._connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_concurrent_reservations_accept_exactly_one_global_attempt(tmp_path):
    ledger = observed(tmp_path)
    for index in range(12):
        ledger.observe(f"failure-{index}", "pipeline_failure", {"reason": f"fault {index}"}, now=1000)
    barrier = threading.Barrier(12)

    def reserve(index):
        local = Ledger(ledger.path)
        barrier.wait(timeout=10)
        return local.reserve(f"failure-{index}", cooldown_seconds=0, now=1001)

    with ThreadPoolExecutor(max_workers=12) as pool:
        attempts = [result for result in pool.map(reserve, range(12)) if result is not None]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert uuid.UUID(attempt["attempt_id"]).version == 4
    assert attempt["status"] == "RUNNING"
    assert attempt["incident"]["status"] == "REPAIRING"
    assert sum(incident["attempts"] for incident in ledger.list_incidents()) == 1
    with sqlite3.connect(ledger.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute("""INSERT INTO supervisor_attempts SELECT ?,fingerprint,category,evidence_json,status,
                started_at,finished_at,lease_expires_at,budget_day,max_per_incident,max_per_day,cooldown_seconds,details_json
                FROM supervisor_attempts WHERE attempt_id=?""", (uuid.uuid4().hex, attempt["attempt_id"]))


def test_distinct_manual_fingerprints_share_daily_budget_and_cannot_raise_it_midday(tmp_path):
    ledger = Ledger(tmp_path / "supervisor.db")
    for index in range(4):
        fingerprint = f"manual-upgrade-{index}"
        ledger.observe(fingerprint, "manual_update", {"request": index}, now=1000 + index * 10)
        attempt = ledger.reserve(fingerprint, cooldown_seconds=0, now=1001 + index * 10)
        assert attempt is not None
        ledger.finish(attempt["attempt_id"], "FAILED", {"test": index}, now=1002 + index * 10)
    ledger = Ledger(ledger.path)
    ledger.observe("different-fingerprint", "manual_update", {"request": "next"}, now=1100)
    assert ledger.reserve("different-fingerprint", max_per_day=999, cooldown_seconds=0, now=1101) is None
    assert len([event for event in ledger.history() if event["event"] == "ATTEMPT_RESERVED"]) == 4
    next_day = ledger.reserve("different-fingerprint", cooldown_seconds=0, now=86401)
    assert next_day is not None
    assert next_day["budget_day"] == "1970-01-02"


def test_global_cooldown_survives_restart_changed_fingerprint_and_smaller_argument(tmp_path):
    ledger = observed(tmp_path)
    first = ledger.reserve("failure-1", cooldown_seconds=1800, now=1000)
    ledger.finish(first["attempt_id"], "FAILED", {"reason": "repair failed"}, now=1001)
    restarted = Ledger(ledger.path)
    restarted.observe("manual-new", "manual_update", {}, now=1002)
    assert restarted.reserve("manual-new", cooldown_seconds=0, now=2799) is None
    assert restarted.reserve("manual-new", cooldown_seconds=0, now=2800) is not None


def test_per_incident_budget_cannot_reset_after_success_or_changed_limit(tmp_path):
    ledger = observed(tmp_path)
    first = ledger.reserve("failure-1", cooldown_seconds=0, now=1001)
    ledger.finish(first["attempt_id"], "SUCCEEDED", {"validated": True}, now=1002)
    assert ledger.reserve("failure-1", cooldown_seconds=0, now=1003) is None
    recurrent = ledger.observe("failure-1", "pipeline_failure", {"reason": "recurrence"}, now=1004)
    assert recurrent["status"] == "OPEN" and recurrent["attempts"] == 1
    second = Ledger(ledger.path).reserve("failure-1", max_per_incident=999, cooldown_seconds=0, now=1005)
    assert second["max_per_incident"] == 2
    ledger.finish(second["attempt_id"], "ROLLED_BACK", {"reason": "validation regression"}, now=1006)
    assert ledger.list_incidents()[0]["status"] == "EXHAUSTED"
    ledger.observe("failure-1", "pipeline_failure", {"reason": "again"}, now=86401)
    assert ledger.reserve("failure-1", max_per_incident=999, cooldown_seconds=0, now=86402) is None


@pytest.mark.parametrize("status,incident_status", [
    ("SUCCEEDED", "RESOLVED"), ("FAILED", "OPEN"), ("BLOCKED", "BLOCKED"), ("ROLLED_BACK", "OPEN"),
])
def test_finish_preserves_details_and_expected_incident_status(tmp_path, status, incident_status):
    ledger = observed(tmp_path)
    attempt = ledger.reserve("failure-1", cooldown_seconds=0, now=1001)
    details = {"validation": {"commands": ["actual test command"], "returncode": 0}, "report": "complete\nreport"}
    result = ledger.finish(attempt["attempt_id"], status, details, now=1002)
    assert result["status"] == status
    assert result["details"] == details
    assert result["finished_at"] == 1002 and result["lease_expires_at"] is None
    assert ledger.list_incidents()[0]["status"] == incident_status
    assert Ledger(ledger.path).get_attempt(attempt["attempt_id"]) == result
    with pytest.raises(ValueError, match="Stale"):
        ledger.finish(attempt["attempt_id"], "SUCCEEDED", {"replacement": True}, now=1003)
    assert ledger.get_attempt(attempt["attempt_id"])["details"] == details


def test_expired_finish_is_fenced_and_expiry_is_committed_before_rejection(tmp_path):
    ledger = observed(tmp_path)
    old = ledger.reserve("failure-1", cooldown_seconds=0, lease_seconds=1, now=1001)
    with pytest.raises(ValueError, match="Stale"):
        ledger.finish(old["attempt_id"], "SUCCEEDED", {"stale": True}, now=1003)
    expired = Ledger(ledger.path).get_attempt(old["attempt_id"])
    assert expired["status"] == "FAILED" and expired["details"]["lease_expired"] is True
    replacement = ledger.reserve("failure-1", cooldown_seconds=0, now=1004)
    assert replacement["attempt_id"] != old["attempt_id"]
    with pytest.raises(ValueError, match="Stale"):
        ledger.finish(old["attempt_id"], "SUCCEEDED", {}, now=1005)
    assert ledger.get_attempt(replacement["attempt_id"])["status"] == "RUNNING"
    assert ledger.heartbeat(old["attempt_id"], now=1005) is False
    assert ledger.heartbeat(replacement["attempt_id"], lease_seconds=5, now=1005) is True


def test_real_process_crash_reservation_stays_counted_and_two_expiries_exhaust(tmp_path):
    path = tmp_path / "supervisor.db"
    program = """import json,os,sys
from cochem_supervisor.state import Ledger
ledger=Ledger(sys.argv[1]); ledger.observe('crashing','runtime_failure',{'phase':'launch'})
attempt=ledger.reserve('crashing',cooldown_seconds=0,lease_seconds=.1)
print(json.dumps(attempt),flush=True)
os._exit(23)
"""
    child = subprocess.run([sys.executable, "-c", program, str(path)], capture_output=True, text=True, timeout=10)
    assert child.returncode == 23
    first = json.loads(child.stdout)
    time.sleep(0.15)
    ledger = Ledger(path)
    expired = ledger.recover_expired()
    assert [attempt["attempt_id"] for attempt in expired] == [first["attempt_id"]]
    assert ledger.list_incidents()[0]["attempts"] == 1
    second = ledger.reserve("crashing", cooldown_seconds=0, lease_seconds=0.06)
    assert second is not None
    time.sleep(0.09)
    assert ledger.recover_expired()[0]["status"] == "FAILED"
    assert ledger.list_incidents()[0]["attempts"] == 2
    assert ledger.list_incidents()[0]["status"] == "EXHAUSTED"
    assert ledger.reserve("crashing", cooldown_seconds=0) is None
    assert ledger.recover_expired() == []


def test_heartbeat_renews_live_lease_but_does_not_resurrect_expired_attempt(tmp_path):
    ledger = observed(tmp_path)
    attempt = ledger.reserve("failure-1", lease_seconds=5, cooldown_seconds=0, now=1000)
    assert ledger.heartbeat(attempt["attempt_id"], lease_seconds=20, now=1004)
    assert ledger.get_attempt(attempt["attempt_id"])["lease_expires_at"] == 1024
    assert ledger.heartbeat(attempt["attempt_id"], lease_seconds=1, now=1005)
    assert ledger.get_attempt(attempt["attempt_id"])["lease_expires_at"] == 1024
    assert ledger.recover_expired(now=1023) == []
    assert ledger.heartbeat(attempt["attempt_id"], lease_seconds=20, now=1024) is False
    assert ledger.get_attempt(attempt["attempt_id"])["status"] == "FAILED"


def test_evidence_snapshot_history_and_budget_rows_are_immutable(tmp_path):
    ledger = observed(tmp_path)
    attempt = ledger.reserve("failure-1", cooldown_seconds=0, now=1001)
    ledger.observe("failure-1", "pipeline_failure", {"latest": "new evidence"}, now=1002)
    assert ledger.get_attempt(attempt["attempt_id"])["evidence"] == {"reason": "detected fault"}
    assert ledger.list_incidents()[0]["evidence"] == {"latest": "new evidence"}
    ledger.record_event("VALIDATION_STARTED", {"commands": ["check"]}, attempt_id=attempt["attempt_id"], now=1003)
    assert ledger.history("failure-1")[-1]["event"] == "VALIDATION_STARTED"
    for sql in ("DELETE FROM supervisor_attempts", "UPDATE supervisor_attempts SET max_per_day=999",
                "DELETE FROM supervisor_events", "UPDATE supervisor_events SET details_json='{}'"):
        with sqlite3.connect(ledger.path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
    before = ledger.list_incidents(), ledger.history(), ledger.get_attempt(attempt["attempt_id"])
    assert (ledger.list_incidents(), ledger.history(), ledger.get_attempt(attempt["attempt_id"])) == before


def test_invalid_configuration_and_unknown_incidents_fail_without_reservations(tmp_path):
    ledger = observed(tmp_path)
    for options in ({"max_per_day": True}, {"max_per_incident": 0}, {"cooldown_seconds": -1},
                    {"lease_seconds": float("inf")}, {"now": float("nan")}):
        with pytest.raises(ValueError):
            ledger.reserve("failure-1", **options)
    with pytest.raises(ValueError, match="Unknown"):
        ledger.reserve("not-observed")
    assert ledger.list_incidents()[0]["attempts"] == 0


def test_unblock_requires_operator_transition_and_preserves_cooldown_and_budget(tmp_path):
    ledger = observed(tmp_path)
    first = ledger.reserve("failure-1", cooldown_seconds=1800, now=1000)
    ledger.finish(first["attempt_id"], "BLOCKED", {"reason": "subscription login needed"}, now=1001)
    blocked = ledger.get_incident("failure-1")
    resumed = Ledger(ledger.path).unblock("failure-1", "Operator renewed subscription login", now=1002)
    assert resumed == {**blocked, "status": "OPEN"}
    assert ledger.history("failure-1")[-1]["event"] == "INCIDENT_UNBLOCKED"
    assert ledger.history("failure-1")[-1]["details"] == {"reason": "Operator renewed subscription login"}
    # The historical cooldown still applies after a process restart/resume.
    assert ledger.reserve("failure-1", cooldown_seconds=0, now=2799) is None
    second = ledger.reserve("failure-1", max_per_incident=999, cooldown_seconds=0, now=2802)
    assert second["max_per_incident"] == 2
    ledger.finish(second["attempt_id"], "BLOCKED", {"reason": "another prerequisite missing"}, now=2803)
    ledger.unblock("failure-1", now=5000)
    assert ledger.reserve("failure-1", max_per_incident=999, cooldown_seconds=0, now=5001) is None
    assert ledger.get_incident("failure-1")["status"] == "EXHAUSTED"
    assert ledger.get_incident("failure-1")["attempts"] == 2
    assert ledger.get_attempt(first["attempt_id"])["status"] == "BLOCKED"


def test_unblock_is_atomic_and_cannot_reopen_other_statuses(tmp_path):
    ledger = observed(tmp_path)
    with pytest.raises(ValueError, match="Only BLOCKED"):
        ledger.unblock("failure-1")
    with pytest.raises(ValueError, match="Unknown"):
        ledger.unblock("unknown")
    first = ledger.reserve("failure-1", cooldown_seconds=0, now=1000)
    with pytest.raises(ValueError, match="Only BLOCKED"):
        ledger.unblock("failure-1")
    ledger.finish(first["attempt_id"], "BLOCKED", {}, now=1001)
    # No cooldown applies here: BLOCKED itself must prevent direct reservations.
    assert ledger.reserve("failure-1", cooldown_seconds=0, now=1002) is None
    barrier = threading.Barrier(4)

    def unblock(_index):
        local = Ledger(ledger.path)
        barrier.wait(timeout=5)
        try:
            return local.unblock("failure-1", now=1002)
        except ValueError as exc:
            assert "Only BLOCKED" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = [result for result in pool.map(unblock, range(4)) if result is not None]
    assert len(accepted) == 1
    assert len([event for event in ledger.history("failure-1") if event["event"] == "INCIDENT_UNBLOCKED"]) == 1
    second = ledger.reserve("failure-1", cooldown_seconds=0, now=1003)
    ledger.finish(second["attempt_id"], "SUCCEEDED", {}, now=1004)
    with pytest.raises(ValueError, match="Only BLOCKED"):
        ledger.unblock("failure-1")
    assert ledger.get_incident("failure-1")["status"] == "RESOLVED"


def test_durable_event_lookup_outlives_bounded_history_and_filters_exact_scope(tmp_path):
    ledger = observed(tmp_path)
    other = ledger.observe("another-failure", "pipeline_failure", {}, now=1000)
    first = ledger.reserve("failure-1", cooldown_seconds=0, now=1001)
    ledger.record_event("WARDEN_RESTART_REQUESTED", {"reason": "stale heartbeat"},
                        attempt_id=first["attempt_id"], now=1002)
    # Populate via actual public transactions, never mock the history query.
    for index in range(1005):
        ledger.record_event("LATER_OBSERVATION", {"index": index}, fingerprint="failure-1", now=1003 + index)
    assert not any(event["event"] == "WARDEN_RESTART_REQUESTED" for event in ledger.history("failure-1"))
    restarted = Ledger(ledger.path)
    assert restarted.has_event("WARDEN_RESTART_REQUESTED")
    assert restarted.has_event("WARDEN_RESTART_REQUESTED", fingerprint="failure-1")
    assert restarted.has_event("WARDEN_RESTART_REQUESTED", attempt_id=first["attempt_id"])
    assert restarted.has_event("WARDEN_RESTART_REQUESTED", fingerprint="failure-1", attempt_id=first["attempt_id"])
    assert not restarted.has_event("WARDEN_RESTART_REQUESTED", fingerprint=other["fingerprint"])
    assert not restarted.has_event("WARDEN_RESTART_REQUESTED", attempt_id="unknown-attempt")
    assert not restarted.has_event("WARDEN_RESTART_REQUESTED", fingerprint="failure-1' OR 1=1 --")
    assert not restarted.has_event("NEVER_RECORDED")
    with pytest.raises(ValueError, match="nonempty"):
        restarted.has_event("")


def test_asymmetric_review_charges_original_incident_and_daily_model_budget(tmp_path):
    ledger=Ledger(tmp_path/'ledger.db')
    ledger.observe('repair','code',{},now=1000)
    attempt=ledger.reserve('repair',max_per_incident=2,max_per_day=2,cooldown_seconds=0,now=1000)
    review=ledger.reserve_additional_model_call(attempt['attempt_id'],'a'*64,now=1001)
    assert review is not None and ledger.get_incident('repair')['model_calls']==2
    assert ledger.reserve_additional_model_call(attempt['attempt_id'],'b'*64,now=1002) is None
    ledger.finish(attempt['attempt_id'],'FAILED',{'review':'rejected'},now=1003)
    assert ledger.get_incident('repair')['status']=='EXHAUSTED'
    assert ledger.reserve('repair',max_per_incident=50,max_per_day=50,cooldown_seconds=0,now=1004) is None
    ledger.observe('different','code',{},now=1004)
    assert ledger.reserve('different',max_per_incident=2,max_per_day=50,cooldown_seconds=0,now=1004) is None
    restored=Ledger(ledger.path)
    assert restored.get_incident('repair')['model_calls']==2
    with restored._connection() as db:
        with pytest.raises(Exception,match='refunded'):
            db.execute('DELETE FROM supervisor_model_calls')
