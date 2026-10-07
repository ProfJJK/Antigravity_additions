"""Durable incident budgets and fenced leases for an independent supervisor.

Reservations count against budgets even when their process crashes. A single
SQLite ledger coordinates all callers and update fingerprints. SQLite work is
short-lived; no process, network, or other external I/O runs in transactions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
import uuid


_TERMINAL = {"SUCCEEDED", "FAILED", "BLOCKED", "ROLLED_BACK"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty string without NUL characters")
    return value


def _timestamp(now: float | None) -> float:
    value = time.time() if now is None else now
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("now must be a finite, nonnegative epoch timestamp")
    return float(value)


def _duration(value: float, name: str, zero: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")
    return float(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS supervisor_incidents (
    fingerprint TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    occurrences INTEGER NOT NULL CHECK(occurrences>0),
    status TEXT NOT NULL CHECK(status IN ('OPEN','BLOCKED','REPAIRING','RESOLVED','EXHAUSTED')),
    last_attempt_at REAL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS supervisor_attempts (
    attempt_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL REFERENCES supervisor_incidents(fingerprint),
    category TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','BLOCKED','ROLLED_BACK')),
    started_at REAL NOT NULL,
    finished_at REAL,
    lease_expires_at REAL,
    budget_day TEXT NOT NULL,
    max_per_incident INTEGER NOT NULL CHECK(max_per_incident>0),
    max_per_day INTEGER NOT NULL CHECK(max_per_day>0),
    cooldown_seconds REAL NOT NULL CHECK(cooldown_seconds>=0),
    details_json TEXT NOT NULL CHECK(json_valid(details_json)),
    CHECK((status='RUNNING' AND finished_at IS NULL AND lease_expires_at IS NOT NULL)
       OR (status<>'RUNNING' AND finished_at IS NOT NULL AND lease_expires_at IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS supervisor_one_active ON supervisor_attempts(status) WHERE status='RUNNING';
CREATE INDEX IF NOT EXISTS supervisor_attempt_budget ON supervisor_attempts(budget_day);
CREATE INDEX IF NOT EXISTS supervisor_attempt_incident ON supervisor_attempts(fingerprint,started_at);
CREATE TABLE IF NOT EXISTS supervisor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event TEXT NOT NULL,
    fingerprint TEXT REFERENCES supervisor_incidents(fingerprint),
    attempt_id TEXT REFERENCES supervisor_attempts(attempt_id),
    details_json TEXT NOT NULL CHECK(json_valid(details_json))
);
CREATE TABLE IF NOT EXISTS supervisor_model_calls (
    call_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES supervisor_attempts(attempt_id),
    fingerprint TEXT NOT NULL REFERENCES supervisor_incidents(fingerprint),
    budget_day TEXT NOT NULL,
    reserved_at REAL NOT NULL,
    role TEXT NOT NULL,
    route_digest TEXT NOT NULL UNIQUE,
    max_per_day INTEGER NOT NULL CHECK(max_per_day>0)
);
CREATE TRIGGER IF NOT EXISTS supervisor_model_call_no_update BEFORE UPDATE ON supervisor_model_calls
BEGIN SELECT RAISE(ABORT,'Additional model-call budget reservations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_model_call_no_delete BEFORE DELETE ON supervisor_model_calls
BEGIN SELECT RAISE(ABORT,'Additional model-call spend cannot be refunded'); END;
CREATE INDEX IF NOT EXISTS supervisor_event_lookup ON supervisor_events(event,fingerprint,attempt_id);
CREATE TABLE IF NOT EXISTS supervisor_review_checkpoints (
    attempt_id TEXT PRIMARY KEY REFERENCES supervisor_attempts(attempt_id),
    fingerprint TEXT NOT NULL REFERENCES supervisor_incidents(fingerprint),
    created_at REAL NOT NULL,
    checkpoint_json TEXT NOT NULL CHECK(json_valid(checkpoint_json)),
    status TEXT NOT NULL CHECK(status IN ('PENDING','COMPLETE'))
);
CREATE TRIGGER IF NOT EXISTS supervisor_review_checkpoint_identity BEFORE UPDATE OF
    attempt_id,fingerprint,created_at,checkpoint_json ON supervisor_review_checkpoints
BEGIN SELECT RAISE(ABORT,'Repair review checkpoint authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_review_checkpoint_no_delete BEFORE DELETE ON supervisor_review_checkpoints
BEGIN SELECT RAISE(ABORT,'Repair review checkpoints cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_event_no_update BEFORE UPDATE ON supervisor_events
BEGIN SELECT RAISE(ABORT,'supervisor history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_event_no_delete BEFORE DELETE ON supervisor_events
BEGIN SELECT RAISE(ABORT,'supervisor history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_attempt_identity BEFORE UPDATE OF attempt_id,fingerprint,category,evidence_json,started_at,budget_day,max_per_incident,max_per_day,cooldown_seconds ON supervisor_attempts
BEGIN SELECT RAISE(ABORT,'reservation identity and budget are immutable'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_attempt_no_delete BEFORE DELETE ON supervisor_attempts
BEGIN SELECT RAISE(ABORT,'reservation budget history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS supervisor_terminal_immutable BEFORE UPDATE ON supervisor_attempts
WHEN OLD.status<>'RUNNING'
BEGIN SELECT RAISE(ABORT,'finished repair attempts are immutable'); END;
"""


class Ledger:
    def __init__(self, path: str | Path):
        if str(path) == ":memory:":
            raise ValueError("Supervisor budgets require an on-disk ledger")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _incident(connection: sqlite3.Connection, fingerprint: str) -> dict:
        row = connection.execute("""SELECT i.*,
            (SELECT count(*) FROM supervisor_attempts a WHERE a.fingerprint=i.fingerprint) AS attempts,
            ((SELECT count(*) FROM supervisor_attempts a WHERE a.fingerprint=i.fingerprint)+
             (SELECT count(*) FROM supervisor_model_calls m WHERE m.fingerprint=i.fingerprint)) AS model_calls
            FROM supervisor_incidents i WHERE i.fingerprint=?""", (fingerprint,)).fetchone()
        if row is None:
            raise ValueError("Unknown incident fingerprint")
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    @staticmethod
    def _attempt(connection: sqlite3.Connection, attempt_id: str) -> dict:
        row = connection.execute("SELECT * FROM supervisor_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown repair attempt")
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["details"] = json.loads(result.pop("details_json"))
        return result

    @staticmethod
    def _event(connection: sqlite3.Connection, event: str, details_json: str, timestamp: float,
               fingerprint: str | None = None, attempt_id: str | None = None) -> dict:
        cursor = connection.execute("""INSERT INTO supervisor_events(timestamp,event,fingerprint,attempt_id,details_json)
            VALUES(?,?,?,?,?)""", (timestamp, event, fingerprint, attempt_id, details_json))
        return {"id": cursor.lastrowid, "timestamp": timestamp, "event": event,
                "fingerprint": fingerprint, "attempt_id": attempt_id, "details": json.loads(details_json)}

    def observe(self, fingerprint: str, category: str, evidence: dict, now: float | None = None) -> dict:
        fingerprint, category = _text(fingerprint, "fingerprint"), _text(category, "category")
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be a JSON object")
        evidence_json, timestamp = _json(evidence), _timestamp(now)
        with self._write() as connection:
            existing = connection.execute("SELECT category,status FROM supervisor_incidents WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing is None:
                connection.execute("""INSERT INTO supervisor_incidents(fingerprint,category,evidence_json,
                    first_seen,last_seen,occurrences,status) VALUES(?,?,?,?,?,1,'OPEN')""",
                                   (fingerprint, category, evidence_json, timestamp, timestamp))
            else:
                if existing["category"] != category:
                    raise ValueError("An incident fingerprint cannot change category")
                # A newly observed recurrence reopens the same logical incident;
                # its previous reservations and cooldown remain authoritative.
                status = "OPEN" if existing["status"] == "RESOLVED" else existing["status"]
                connection.execute("""UPDATE supervisor_incidents SET evidence_json=?,last_seen=max(last_seen,?),
                    occurrences=occurrences+1,status=?,resolved_at=CASE WHEN status='RESOLVED' THEN NULL ELSE resolved_at END
                    WHERE fingerprint=?""", (evidence_json, timestamp, status, fingerprint))
            self._event(connection, "INCIDENT_OBSERVED", _json({"category": category, "evidence": evidence}), timestamp, fingerprint)
            return self._incident(connection, fingerprint)

    def _finish(self, connection: sqlite3.Connection, attempt: dict, status: str, details_json: str,
                timestamp: float) -> dict:
        connection.execute("""UPDATE supervisor_attempts SET status=?,finished_at=?,lease_expires_at=NULL,
            details_json=? WHERE attempt_id=? AND status='RUNNING'""",
                           (status, timestamp, details_json, attempt["attempt_id"]))
        connection.execute("UPDATE supervisor_review_checkpoints SET status='COMPLETE' WHERE attempt_id=?",(attempt['attempt_id'],))
        incident = self._incident(connection, attempt["fingerprint"])
        exhausted = incident["model_calls"] >= attempt["max_per_incident"]
        incident_status = ("RESOLVED" if status == "SUCCEEDED" else "BLOCKED" if status == "BLOCKED"
                           else "EXHAUSTED" if exhausted else "OPEN")
        connection.execute("UPDATE supervisor_incidents SET status=?,resolved_at=? WHERE fingerprint=?",
                           (incident_status, timestamp if status == "SUCCEEDED" else None, attempt["fingerprint"]))
        self._event(connection, "ATTEMPT_" + status, details_json, timestamp, attempt["fingerprint"], attempt["attempt_id"])
        return self._attempt(connection, attempt["attempt_id"])

    def _recover(self, connection: sqlite3.Connection, timestamp: float) -> list[dict]:
        rows = connection.execute("""SELECT attempt_id FROM supervisor_attempts a WHERE status='RUNNING' AND lease_expires_at<=?
            AND NOT EXISTS (SELECT 1 FROM supervisor_review_checkpoints r WHERE r.attempt_id=a.attempt_id AND r.status='PENDING')""", (timestamp,)).fetchall()
        recovered = []
        for row in rows:
            attempt = self._attempt(connection, row["attempt_id"])
            recovered.append(self._finish(connection, attempt, "FAILED", _json({
                "reason": "Supervisor execution lease expired; reservation remains charged to its budgets",
                "lease_expired": True,
            }), timestamp))
        return recovered

    def recover_expired(self, now: float | None = None) -> list[dict]:
        timestamp = _timestamp(now)
        with self._write() as connection:
            return self._recover(connection, timestamp)

    def reserve(self, fingerprint: str, max_per_incident: int = 2, max_per_day: int = 4,
                cooldown_seconds: float = 1800, lease_seconds: float = 1800,
                now: float | None = None) -> dict | None:
        fingerprint = _text(fingerprint, "fingerprint")
        for name, value in (("max_per_incident", max_per_incident), ("max_per_day", max_per_day)):
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError(f"{name} must be an integer from 1 to 1000")
        cooldown = _duration(cooldown_seconds, "cooldown_seconds", zero=True)
        lease, timestamp = _duration(lease_seconds, "lease_seconds"), _timestamp(now)
        day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        with self._write() as connection:
            self._recover(connection, timestamp)
            incident = self._incident(connection, fingerprint)
            if incident["status"] in {"RESOLVED", "EXHAUSTED", "REPAIRING", "BLOCKED"}:
                return None
            prior_limit = connection.execute("SELECT min(max_per_incident) FROM supervisor_attempts WHERE fingerprint=?", (fingerprint,)).fetchone()[0]
            incident_budget = min(max_per_incident, prior_limit) if prior_limit is not None else max_per_incident
            if incident["model_calls"] >= incident_budget:
                connection.execute("UPDATE supervisor_incidents SET status='EXHAUSTED' WHERE fingerprint=?", (fingerprint,))
                return None
            if connection.execute("SELECT 1 FROM supervisor_attempts WHERE status='RUNNING'").fetchone():
                return None
            spent, prior_daily_limit = connection.execute("SELECT count(*),min(max_per_day) FROM supervisor_attempts WHERE budget_day=?", (day,)).fetchone()
            extra_spent, extra_limit = connection.execute("SELECT count(*),min(max_per_day) FROM supervisor_model_calls WHERE budget_day=?", (day,)).fetchone()
            spent += extra_spent
            daily_budget = min(value for value in (max_per_day,prior_daily_limit,extra_limit) if value is not None)
            if spent >= daily_budget:
                return None
            latest = connection.execute("SELECT started_at,cooldown_seconds FROM supervisor_attempts ORDER BY started_at DESC,attempt_id DESC LIMIT 1").fetchone()
            if latest is not None and timestamp < latest["started_at"] + max(cooldown, latest["cooldown_seconds"]):
                return None
            attempt_id = uuid.uuid4().hex
            connection.execute("""INSERT INTO supervisor_attempts(attempt_id,fingerprint,category,evidence_json,status,
                started_at,lease_expires_at,budget_day,max_per_incident,max_per_day,cooldown_seconds,details_json)
                VALUES(?,?,?,?,'RUNNING',?,?,?,?,?,?,'{}')""",
                               (attempt_id, fingerprint, incident["category"], _json(incident["evidence"]), timestamp,
                                timestamp + lease, day, incident_budget, daily_budget, cooldown))
            connection.execute("UPDATE supervisor_incidents SET status='REPAIRING',last_attempt_at=? WHERE fingerprint=?",
                               (timestamp, fingerprint))
            self._event(connection, "ATTEMPT_RESERVED", _json({
                "max_per_incident": incident_budget, "max_per_day": daily_budget, "cooldown_seconds": cooldown,
                "lease_expires_at": timestamp + lease, "budget_day": day,
            }), timestamp, fingerprint, attempt_id)
            attempt = self._attempt(connection, attempt_id)
            attempt["incident"] = self._incident(connection, fingerprint)
            return attempt

    def reserve_additional_model_call(self, attempt_id: str, route_digest: str, *, role='asymmetric_review', now=None):
        """Charge an additional inference under an active repair, without refund.

        The original attempt reservation already pays for generation. Every
        reviewer dispatch (including failed/quota/crashed native invocations)
        consumes one additional unit from those same captured incident/day caps.
        """
        _text(attempt_id,'attempt_id');_text(route_digest,'route_digest');_text(role,'role')
        timestamp=_timestamp(now)
        day=datetime.fromtimestamp(timestamp,timezone.utc).date().isoformat()
        with self._write() as connection:
            self._recover(connection,timestamp)
            attempt=self._attempt(connection,attempt_id)
            if attempt['status']!='RUNNING' or attempt['lease_expires_at']<=timestamp:
                return None
            existing=connection.execute('SELECT * FROM supervisor_model_calls WHERE route_digest=?',(route_digest,)).fetchone()
            if existing is not None:
                raise ValueError('One routing reservation cannot authorize a repeated paid model call')
            incident=self._incident(connection,attempt['fingerprint'])
            if incident['model_calls']>=attempt['max_per_incident']:
                return None
            spent,old_limit=connection.execute('SELECT count(*),min(max_per_day) FROM supervisor_attempts WHERE budget_day=?',(day,)).fetchone()
            extra,extra_limit=connection.execute('SELECT count(*),min(max_per_day) FROM supervisor_model_calls WHERE budget_day=?',(day,)).fetchone()
            limit=min(value for value in (attempt['max_per_day'],old_limit,extra_limit) if value is not None)
            if spent+extra>=limit:
                return None
            identifier=uuid.uuid4().hex
            connection.execute('INSERT INTO supervisor_model_calls VALUES(?,?,?,?,?,?,?,?)',
                (identifier,attempt_id,attempt['fingerprint'],day,timestamp,role,route_digest,limit))
            result={'call_id':identifier,'attempt_id':attempt_id,'fingerprint':attempt['fingerprint'],
                'budget_day':day,'reserved_at':timestamp,'role':role,'route_digest':route_digest,
                'max_per_day':limit,'max_per_incident':attempt['max_per_incident']}
            self._event(connection,'MODEL_CALL_RESERVED',_json(result),timestamp,attempt['fingerprint'],attempt_id)
            return result

    def save_review_checkpoint(self,attempt_id,checkpoint,now=None):
        """Preserve the validated candidate before an independently routed review.

        This does not reserve or refund inference. The original charged attempt
        remains fenced; only verified process cleanup permits lease resumption.
        """
        if not isinstance(checkpoint,dict):
            raise ValueError('Review checkpoint must be a controller-authored object')
        data=_json(checkpoint);timestamp=_timestamp(now)
        with self._write() as connection:
            attempt=self._attempt(connection,attempt_id)
            if attempt['status']!='RUNNING' or attempt['lease_expires_at']<=timestamp:
                raise ValueError('Only an active charged repair can checkpoint its review')
            old=connection.execute('SELECT checkpoint_json,status FROM supervisor_review_checkpoints WHERE attempt_id=?',(attempt_id,)).fetchone()
            if old is not None:
                if old['checkpoint_json']!=data or old['status']!='PENDING':
                    raise ValueError('Captured repair review checkpoint cannot change')
                return
            connection.execute('INSERT INTO supervisor_review_checkpoints VALUES(?,?,?,?,?)',
                (attempt_id,attempt['fingerprint'],timestamp,data,'PENDING'))
            self._event(connection,'REVIEW_CHECKPOINT_SAVED',_json({'candidate_preserved':True}),timestamp,attempt['fingerprint'],attempt_id)

    def review_checkpoint(self,attempt_id):
        with self._connection() as connection:
            row=connection.execute("SELECT * FROM supervisor_review_checkpoints WHERE attempt_id=? AND status='PENDING'",(attempt_id,)).fetchone()
            if row is None:return None
            return {**dict(row),'checkpoint':json.loads(row['checkpoint_json'])}

    def pending_reviews(self):
        with self._connection() as connection:
            rows=connection.execute("SELECT * FROM supervisor_review_checkpoints WHERE status='PENDING' ORDER BY created_at,attempt_id").fetchall()
            return [{**dict(row),'checkpoint':json.loads(row['checkpoint_json'])} for row in rows]

    def resume_review(self,attempt_id,*,cleanup_verified,lease_seconds=180,now=None):
        if cleanup_verified is not True:
            raise ValueError('Review lease continuation requires verified physical cleanup')
        timestamp=_timestamp(now);duration=_duration(lease_seconds,'lease_seconds')
        with self._write() as connection:
            attempt=self._attempt(connection,attempt_id)
            checkpoint=connection.execute("SELECT 1 FROM supervisor_review_checkpoints WHERE attempt_id=? AND status='PENDING'",(attempt_id,)).fetchone()
            if checkpoint is None or attempt['status']!='RUNNING':
                raise ValueError('Only a pending original repair may resume its review lease')
            connection.execute('UPDATE supervisor_attempts SET lease_expires_at=? WHERE attempt_id=?',(timestamp+duration,attempt_id))
            self._event(connection,'REVIEW_LEASE_RESUMED',_json({'cleanup_verified':True,'charged_generation_unchanged':True}),timestamp,attempt['fingerprint'],attempt_id)
            return self._attempt(connection,attempt_id)

    def finish(self, attempt_id: str, status: str, details: dict, now: float | None = None) -> dict:
        _text(attempt_id, "attempt_id")
        if status not in _TERMINAL:
            raise ValueError("status must be SUCCEEDED, FAILED, BLOCKED, or ROLLED_BACK")
        if not isinstance(details, dict):
            raise ValueError("details must be a JSON object")
        details_json, timestamp = _json(details), _timestamp(now)
        result = None
        with self._write() as connection:
            self._recover(connection, timestamp)
            attempt = self._attempt(connection, attempt_id)
            if attempt["status"] == "RUNNING" and attempt["lease_expires_at"] > timestamp:
                result = self._finish(connection, attempt, status, details_json, timestamp)
        # Commit an expired attempt's recovery before rejecting its stale writer.
        if result is None:
            raise ValueError("Stale or completed repair attempt cannot finish")
        return result

    def heartbeat(self, attempt_id: str, lease_seconds: float = 1800, now: float | None = None) -> bool:
        _text(attempt_id, "attempt_id")
        duration, timestamp = _duration(lease_seconds, "lease_seconds"), _timestamp(now)
        with self._write() as connection:
            self._recover(connection, timestamp)
            row = connection.execute("SELECT status,lease_expires_at FROM supervisor_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None or row["status"] != "RUNNING" or row["lease_expires_at"] <= timestamp:
                return False
            connection.execute("UPDATE supervisor_attempts SET lease_expires_at=? WHERE attempt_id=?",
                               (max(row["lease_expires_at"], timestamp + duration), attempt_id))
            return True

    def record_event(self, event: str, details: dict | None = None, *, fingerprint: str | None = None,
                     attempt_id: str | None = None, now: float | None = None) -> dict:
        event = _text(event, "event")
        details = {} if details is None else details
        if not isinstance(details, dict):
            raise ValueError("details must be a JSON object")
        details_json, timestamp = _json(details), _timestamp(now)
        with self._write() as connection:
            if fingerprint is not None:
                self._incident(connection, fingerprint)
            if attempt_id is not None:
                attempt = self._attempt(connection, attempt_id)
                if fingerprint is None:
                    fingerprint = attempt["fingerprint"]
                elif fingerprint != attempt["fingerprint"]:
                    raise ValueError("Event attempt and incident do not match")
            return self._event(connection, event, details_json, timestamp, fingerprint, attempt_id)

    def list_incidents(self, limit: int = 1000) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer from 1 to 10000")
        with self._connection() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT fingerprint FROM supervisor_incidents ORDER BY last_seen DESC,fingerprint LIMIT ?", (limit,)).fetchall()
            return [self._incident(connection, row["fingerprint"]) for row in rows]

    def get_attempt(self, attempt_id: str) -> dict:
        with self._connection() as connection:
            return self._attempt(connection, attempt_id)

    def get_incident(self, fingerprint: str) -> dict:
        fingerprint = _text(fingerprint, "fingerprint")
        with self._connection() as connection:
            return self._incident(connection, fingerprint)

    def unblock(self, fingerprint: str, reason: str = "Operator confirmed prerequisite restored",
                *, now: float | None = None) -> dict:
        """Resume a blocked incident without resetting its reservations or cooldown."""
        fingerprint, reason = _text(fingerprint, "fingerprint"), _text(reason, "reason")
        timestamp = _timestamp(now)
        with self._write() as connection:
            incident = self._incident(connection, fingerprint)
            if incident["status"] != "BLOCKED":
                raise ValueError("Only BLOCKED incidents can be unblocked")
            connection.execute("UPDATE supervisor_incidents SET status='OPEN' WHERE fingerprint=?", (fingerprint,))
            self._event(connection, "INCIDENT_UNBLOCKED", _json({"reason": reason}), timestamp, fingerprint)
            return self._incident(connection, fingerprint)

    def has_event(self, event: str, *, fingerprint: str | None = None,
                  attempt_id: str | None = None) -> bool:
        """Check durable audit markers without the bounded display-history window."""
        conditions, parameters = ["event=?"], [_text(event, "event")]
        for name, value in (("fingerprint", fingerprint), ("attempt_id", attempt_id)):
            if value is not None:
                conditions.append(name + "=?")
                parameters.append(_text(value, name))
        with self._connection() as connection:
            return connection.execute("SELECT 1 FROM supervisor_events WHERE " + " AND ".join(conditions)
                                      + " LIMIT 1", parameters).fetchone() is not None

    def history(self, fingerprint: str | None = None, limit: int = 1000) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer from 1 to 10000")
        with self._connection() as connection:
            if fingerprint is None:
                rows = connection.execute("SELECT * FROM supervisor_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM supervisor_events WHERE fingerprint=? ORDER BY id DESC LIMIT ?", (fingerprint, limit)).fetchall()
            result = []
            for row in reversed(rows):
                event = dict(row)
                event["details"] = json.loads(event.pop("details_json"))
                result.append(event)
            return result
