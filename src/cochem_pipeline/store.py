"""Transactional scatter/gather job board and immutable document artifacts.

Connections are short-lived and never span CLI execution. The trusted scheduler
supplies process receipts; generated prose is never treated as provider identity.
The namespaced tables coexist with older CoChem job-board schemas.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable, Iterator
import uuid


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def output_digest(output: dict) -> str:
    """Digest the exact structured result accepted from a CLI by the scheduler."""
    return hashlib.sha256(canonical_json(output).encode("utf-8")).hexdigest()


def artifact_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    job_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
    parent_job_id TEXT REFERENCES pipeline_jobs(job_id),
    kind TEXT NOT NULL CHECK(kind IN ('MACRO_PLANNING_REQUEST','MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS')),
    status TEXT NOT NULL CHECK(status IN ('BLOCKED','PENDING','IN_PROGRESS','COMPLETED','FAILED','PENDING_RETRY')),
    chapter_id TEXT,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    attempt_id TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token >= 0),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts >= 1),
    lease_owner TEXT,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    error TEXT,
    CHECK((kind = 'CHAPTER_DRAFT' AND chapter_id IS NOT NULL) OR (kind <> 'CHAPTER_DRAFT' AND chapter_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_unique_chapter ON pipeline_jobs(workflow_id,chapter_id) WHERE kind='CHAPTER_DRAFT';
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_unique_stage ON pipeline_jobs(workflow_id,kind) WHERE kind<>'CHAPTER_DRAFT';
CREATE INDEX IF NOT EXISTS pipeline_claimable ON pipeline_jobs(status,created_at);
CREATE TABLE IF NOT EXISTS pipeline_outputs (
    job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id),
    attempt_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    chapter_id TEXT,
    output_json TEXT NOT NULL CHECK(json_valid(output_json)),
    receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)),
    output_sha256 TEXT NOT NULL CHECK(length(output_sha256)=64),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_artifacts (
    job_id TEXT PRIMARY KEY REFERENCES pipeline_outputs(job_id),
    workflow_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
    chapter_id TEXT NOT NULL,
    artifact_uri TEXT NOT NULL UNIQUE,
    artifact_text TEXT NOT NULL CHECK(length(artifact_text)>0),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    created_at REAL NOT NULL,
    UNIQUE(workflow_id,chapter_id)
);
CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
    job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
    timestamp REAL NOT NULL,
    event TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK(json_valid(details_json))
);
CREATE TABLE IF NOT EXISTS pipeline_context (
    job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id),
    xml TEXT NOT NULL,
    watermark TEXT NOT NULL CHECK(length(watermark)=64),
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_worker_ownership (
    workflow_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
    chapter_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES pipeline_jobs(job_id),
    created_at REAL NOT NULL,
    PRIMARY KEY(workflow_id,chapter_id),
    UNIQUE(workflow_id,slot)
);
CREATE TRIGGER IF NOT EXISTS pipeline_worker_binding_owner BEFORE INSERT ON pipeline_worker_ownership
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM pipeline_jobs j WHERE j.job_id=NEW.job_id AND j.workflow_id=NEW.workflow_id
        AND j.chapter_id=NEW.chapter_id AND j.kind='CHAPTER_DRAFT'
    ) THEN RAISE(ABORT,'worker binding ownership mismatch') END;
END;
CREATE TRIGGER IF NOT EXISTS pipeline_worker_binding_no_update BEFORE UPDATE ON pipeline_worker_ownership
BEGIN SELECT RAISE(ABORT,'worker identity bindings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_worker_binding_no_delete BEFORE DELETE ON pipeline_worker_ownership
BEGIN SELECT RAISE(ABORT,'worker identity bindings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_chapter_index_immutable BEFORE UPDATE OF payload_json ON pipeline_jobs
WHEN OLD.kind='CHAPTER_DRAFT' AND json_extract(NEW.payload_json,'$.chapter_index') IS NOT json_extract(OLD.payload_json,'$.chapter_index')
BEGIN SELECT RAISE(ABORT,'manifest chapter position is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_output_owner BEFORE INSERT ON pipeline_outputs
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM pipeline_jobs j WHERE j.job_id=NEW.job_id AND j.status='IN_PROGRESS'
        AND j.attempt_id=NEW.attempt_id AND j.fencing_token=NEW.fencing_token
        AND j.lease_expires_at > (julianday('now')-2440587.5)*86400.0
        AND j.kind<>'MACRO_PLANNING_REQUEST'
    ) THEN RAISE(ABORT,'stale or unowned attempt') END;
    SELECT CASE WHEN NEW.chapter_id IS NOT (SELECT chapter_id FROM pipeline_jobs WHERE job_id=NEW.job_id)
        THEN RAISE(ABORT,'chapter ownership mismatch') END;
    SELECT CASE WHEN (SELECT kind FROM pipeline_jobs WHERE job_id=NEW.job_id)='CHAPTER_DRAFT'
        AND json_extract(NEW.output_json,'$.chapter_id') IS NOT NEW.chapter_id
        THEN RAISE(ABORT,'chapter output ownership mismatch') END;
END;
CREATE TRIGGER IF NOT EXISTS pipeline_output_no_update BEFORE UPDATE ON pipeline_outputs
BEGIN SELECT RAISE(ABORT,'outputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_output_no_delete BEFORE DELETE ON pipeline_outputs
BEGIN SELECT RAISE(ABORT,'outputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_artifact_owner BEFORE INSERT ON pipeline_artifacts
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM pipeline_jobs j WHERE j.job_id=NEW.job_id AND j.workflow_id=NEW.workflow_id
        AND ((j.kind='CHAPTER_DRAFT' AND j.chapter_id=NEW.chapter_id)
          OR (j.kind='SYNTHESIS' AND NEW.chapter_id='synthesis'))
    ) THEN RAISE(ABORT,'artifact ownership mismatch') END;
END;
CREATE TRIGGER IF NOT EXISTS pipeline_artifact_no_update BEFORE UPDATE ON pipeline_artifacts
BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_artifact_no_delete BEFORE DELETE ON pipeline_artifacts
BEGIN SELECT RAISE(ABORT,'artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_job_identity BEFORE UPDATE OF job_id,workflow_id,parent_job_id,kind,chapter_id ON pipeline_jobs
BEGIN SELECT RAISE(ABORT,'job ownership is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_completed_output BEFORE UPDATE OF status ON pipeline_jobs
WHEN NEW.status='COMPLETED' AND OLD.status<>'COMPLETED'
BEGIN
    SELECT CASE WHEN NEW.kind<>'MACRO_PLANNING_REQUEST'
        AND NOT EXISTS(SELECT 1 FROM pipeline_outputs WHERE job_id=NEW.job_id)
        THEN RAISE(ABORT,'completion requires an accepted output') END;
    SELECT CASE WHEN NEW.kind='MACRO_PLANNING_REQUEST'
        AND NOT EXISTS(SELECT 1 FROM pipeline_jobs WHERE workflow_id=NEW.workflow_id AND kind='SYNTHESIS' AND status='COMPLETED')
        THEN RAISE(ABORT,'root completion requires synthesis') END;
END;
"""


def _strings(value: Any, label: str) -> list[str]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be a nonempty list of unique nonblank strings")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(f"{label} must be an alphanumeric identifier (up to 128 characters)")
    return value


class JobStore:
    def __init__(self, path: str | Path, max_attempts: int = 3):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be an integer from 1 to 100")
        self.max_attempts = max_attempts
        if str(path) == ":memory:":
            raise ValueError("A durable on-disk SQLite path is required")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # Older 4.2.2 draft databases predate a durable execution budget.
            # Migrate under the write lock so concurrently starting workers
            # cannot both attempt to add the same column.
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(pipeline_jobs)")}
                if "attempts" not in columns:
                    conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0)")
                    conn.execute("UPDATE pipeline_jobs SET attempts=fencing_token WHERE kind<>'MACRO_PLANNING_REQUEST'")
                if "max_attempts" not in columns:
                    conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts>=1)")
                    conn.execute("UPDATE pipeline_jobs SET max_attempts=?", (max_attempts,))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _get(conn: sqlite3.Connection, job_id: str) -> dict:
        row = conn.execute("""SELECT j.*,o.output_json,o.receipt_json,o.output_sha256,a.sha256 AS artifact_sha256,w.slot AS worker_slot
            FROM pipeline_jobs j LEFT JOIN pipeline_outputs o USING(job_id)
            LEFT JOIN pipeline_artifacts a USING(job_id)
            LEFT JOIN pipeline_worker_ownership w USING(job_id) WHERE j.job_id=?""", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown job_id")
        result = dict(row)
        for source, dest in (("payload_json", "payload"), ("output_json", "output"), ("receipt_json", "receipt")):
            value = result.pop(source)
            result[dest] = json.loads(value) if value is not None else None
        context = conn.execute("""SELECT job_id,xml,watermark,updated_at FROM pipeline_context
            WHERE job_id IN (?,?) ORDER BY CASE WHEN job_id=? THEN 0 ELSE 1 END LIMIT 1""",
                               (job_id, result["workflow_id"], job_id)).fetchone()
        result["context"] = dict(context) if context is not None else None
        return result

    @staticmethod
    def _event(conn: sqlite3.Connection, job: dict, event: str, **details: Any) -> None:
        conn.execute("INSERT INTO pipeline_events(workflow_id,job_id,timestamp,event,details_json) VALUES(?,?,?,?,?)",
                     (job["workflow_id"], job["job_id"], time.time(), event, canonical_json(details)))

    @staticmethod
    def _insert(conn: sqlite3.Connection, job_id: str, workflow_id: str, parent: str | None,
                kind: str, status: str, payload: dict, chapter_id: str | None = None,
                max_attempts: int = 3) -> None:
        timestamp = time.time()
        conn.execute("""INSERT INTO pipeline_jobs(job_id,workflow_id,parent_job_id,kind,status,chapter_id,
            payload_json,created_at,updated_at,max_attempts) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (job_id, workflow_id, parent, kind, status, chapter_id, canonical_json(payload), timestamp, timestamp, max_attempts))

    def submit(self, objective: str, requirements: list[str], chapter_count: int = 6,
               workflow_id: str | None = None) -> dict:
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be nonempty")
        _strings(requirements, "requirements")
        if type(chapter_count) is not int or not 1 <= chapter_count <= 64:
            raise ValueError("chapter_count must be an integer from 1 to 64")
        workflow_id = _identifier(workflow_id or uuid.uuid4().hex, "workflow_id")
        payload = {"objective": objective, "requirements": requirements, "chapter_count": chapter_count}
        with self._write() as conn:
            existing = conn.execute("SELECT job_id FROM pipeline_jobs WHERE job_id=?", (workflow_id,)).fetchone()
            if existing:
                root = self._get(conn, workflow_id)
                if root["kind"] != "MACRO_PLANNING_REQUEST" or root["payload"] != payload:
                    raise ValueError("workflow_id already belongs to a different request")
            else:
                self._insert(conn, workflow_id, workflow_id, None, "MACRO_PLANNING_REQUEST", "IN_PROGRESS", payload, max_attempts=self.max_attempts)
                self._insert(conn, uuid.uuid4().hex, workflow_id, workflow_id, "MANIFEST_GENERATOR", "PENDING", payload, max_attempts=self.max_attempts)
                self._insert(conn, uuid.uuid4().hex, workflow_id, workflow_id, "SYNTHESIS", "BLOCKED", payload, max_attempts=self.max_attempts)
                self._event(conn, self._get(conn, workflow_id), "WORKFLOW_SUBMITTED", chapter_count=chapter_count)
        return self.workflow(workflow_id)

    @staticmethod
    def _lease_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        return float(value)

    def _reap(self, conn: sqlite3.Connection) -> list[dict]:
        expired = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE kind<>'MACRO_PLANNING_REQUEST'
            AND ((status='IN_PROGRESS' AND lease_expires_at<=?)
              OR (status IN ('PENDING','PENDING_RETRY') AND attempts>=max_attempts))""", (time.time(),)).fetchall()
        results = []
        for row in expired:
            previous = self._get(conn, row["job_id"])
            if previous["status"] == "FAILED":
                continue  # Another exhausted sibling in this transaction fenced it.
            root = self._get(conn, previous["workflow_id"])
            exhausted = previous["attempts"] >= previous["max_attempts"]
            status = "FAILED" if exhausted or root["status"] == "FAILED" else "PENDING_RETRY"
            error = f"Attempt budget exhausted ({previous['max_attempts']} executions)" if exhausted else "Execution lease expired"
            conn.execute("""UPDATE pipeline_jobs SET status=?,attempt_id=NULL,lease_owner=NULL,
                lease_expires_at=NULL,error=?,updated_at=? WHERE job_id=?""",
                         (status, error, time.time(), previous["job_id"]))
            self._event(conn, previous, "ATTEMPT_BUDGET_EXHAUSTED" if exhausted else "LEASE_EXPIRED",
                        attempt_id=previous["attempt_id"], fencing_token=previous["fencing_token"],
                        attempts=previous["attempts"], max_attempts=previous["max_attempts"])
            if status == "FAILED":
                self._fail_workflow(conn, previous["workflow_id"], error)
            results.append(self._get(conn, previous["job_id"]))
        return results

    def reap_expired(self) -> list[dict]:
        with self._write() as conn:
            return self._reap(conn)

    def claim(self, owner: str, lease_seconds: float = 60, max_workers: int = 4, *,
              exclude_job_ids: Iterable[str] = (), worker_slot: str | None = None) -> dict | None:
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be nonempty")
        duration = self._lease_seconds(lease_seconds)
        if type(max_workers) is not int or not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be an integer from 1 to 64")
        if isinstance(exclude_job_ids, (str, bytes)):
            raise ValueError("exclude_job_ids must be an iterable of job IDs")
        excluded = tuple(sorted({_identifier(value, "excluded job_id") for value in exclude_job_ids}))
        if len(excluded) > 1000:
            raise ValueError("At most 1000 job IDs may be excluded")
        if worker_slot is not None:
            _identifier(worker_slot, "worker_slot")
        exclusion = " AND j.job_id NOT IN (" + ",".join("?" for _ in excluded) + ")" if excluded else ""
        parameters: list[Any] = list(excluded)
        if worker_slot is None:
            # Compatibility callers may operate unbound jobs, but cannot steal
            # a chapter already assigned to a persistent production identity.
            binding_filter = " AND NOT EXISTS(SELECT 1 FROM pipeline_worker_ownership w WHERE w.job_id=j.job_id)"
        else:
            binding_filter = """ AND (j.kind<>'CHAPTER_DRAFT'
                OR EXISTS(SELECT 1 FROM pipeline_worker_ownership w WHERE w.job_id=j.job_id AND w.slot=?)
                OR (NOT EXISTS(SELECT 1 FROM pipeline_worker_ownership w WHERE w.job_id=j.job_id)
                    AND NOT EXISTS(SELECT 1 FROM pipeline_worker_ownership w WHERE w.workflow_id=j.workflow_id AND w.slot=?)))"""
            parameters.extend((worker_slot, worker_slot))
        with self._write() as conn:
            self._reap(conn)
            active = conn.execute("SELECT count(*) FROM pipeline_jobs WHERE status='IN_PROGRESS' AND kind<>'MACRO_PLANNING_REQUEST'").fetchone()[0]
            if active >= max_workers:
                return None
            row = conn.execute("""SELECT j.job_id FROM pipeline_jobs j JOIN pipeline_jobs root ON root.job_id=j.workflow_id
                WHERE j.status IN ('PENDING','PENDING_RETRY') AND j.kind<>'MACRO_PLANNING_REQUEST'
                AND j.attempts<j.max_attempts
                AND root.status='IN_PROGRESS'""" + exclusion + binding_filter
                               + " ORDER BY j.created_at,j.job_id LIMIT 1", parameters).fetchone()
            if row is None:
                return None
            selected = self._get(conn, row["job_id"])
            if selected["kind"] == "CHAPTER_DRAFT" and worker_slot is not None and selected["worker_slot"] is None:
                conn.execute("""INSERT INTO pipeline_worker_ownership(workflow_id,chapter_id,slot,job_id,created_at)
                    VALUES(?,?,?,?,?)""", (selected["workflow_id"], selected["chapter_id"], worker_slot, selected["job_id"], time.time()))
            timestamp = time.time()
            conn.execute("""UPDATE pipeline_jobs SET status='IN_PROGRESS',attempt_id=?,fencing_token=fencing_token+1,attempts=attempts+1,
                lease_owner=?,lease_expires_at=?,updated_at=?,error=NULL WHERE job_id=?""",
                         (uuid.uuid4().hex, owner, timestamp + duration, timestamp, row["job_id"]))
            job = self._get(conn, row["job_id"])
            self._event(conn, job, "CLAIMED", attempt_id=job["attempt_id"], fencing_token=job["fencing_token"],
                        owner=owner, worker_slot=worker_slot)
            return job

    @staticmethod
    def _owned(job: dict, attempt_id: str, fencing_token: int) -> bool:
        return (job["status"] == "IN_PROGRESS" and job["attempt_id"] == attempt_id
                and type(fencing_token) is int and job["fencing_token"] == fencing_token
                and job["lease_expires_at"] is not None and job["lease_expires_at"] > time.time())

    def heartbeat(self, job_id: str, attempt_id: str, fencing_token: int, lease_seconds: float = 60) -> bool:
        duration = self._lease_seconds(lease_seconds)
        with self._write() as conn:
            job = self._get(conn, job_id)
            if not self._owned(job, attempt_id, fencing_token):
                return False
            conn.execute("UPDATE pipeline_jobs SET lease_expires_at=?,updated_at=? WHERE job_id=?",
                         (time.time() + duration, time.time(), job_id))
            return True

    @staticmethod
    def _receipt(output: dict, receipt: dict) -> None:
        if not isinstance(output, dict) or not isinstance(receipt, dict):
            raise ValueError("output and receipt must be objects")
        if (receipt.get("provider") not in {"codex", "claude", "gemini"}
                or type(receipt.get("pid")) is not int or receipt["pid"] <= 0
                or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0
                or not isinstance(receipt.get("session_id"), str) or not receipt["session_id"].strip()
                or receipt.get("output_sha256") != output_digest(output)):
            raise ValueError("A successful process receipt bound to the exact structured output is required")

    def _manifest(self, job: dict, output: dict) -> list[dict]:
        chapters = output.get("chapters")
        if not isinstance(chapters, list) or len(chapters) != job["payload"]["chapter_count"]:
            raise ValueError("Manifest must contain the configured chapter count")
        assigned: set[str] = set()
        identifiers: set[str] = set()
        required = set(job["payload"]["requirements"])
        for chapter in chapters:
            if not isinstance(chapter, dict):
                raise ValueError("Each chapter must be an object")
            chapter_id = _identifier(chapter.get("chapter_id"), "chapter_id")
            if chapter_id in identifiers or chapter_id == "synthesis":
                raise ValueError("Chapter IDs must be unique and may not be synthesis")
            identifiers.add(chapter_id)
            if not isinstance(chapter.get("title"), str) or not chapter["title"].strip():
                raise ValueError("Each chapter requires a title")
            traced = set(_strings(chapter.get("requirements"), "chapter requirements"))
            if not traced <= required:
                raise ValueError("Manifest includes an unknown requirement")
            assigned.update(traced)
        if assigned != required:
            raise ValueError("Manifest must cover every required requirement")
        return chapters

    @staticmethod
    def _chapter(job: dict, output: dict) -> None:
        if output.get("chapter_id") != job["chapter_id"]:
            raise ValueError("Chapter ownership mismatch")
        traced = set(_strings(output.get("requirements_traced"), "requirements_traced"))
        if not set(job["payload"]["requirements"]) <= traced:
            raise ValueError("Chapter must trace every assigned requirement")
        tasks = output.get("wbs_tasks_defined")
        if not isinstance(tasks, list) or not tasks or not all(isinstance(x, dict) and x for x in tasks):
            raise ValueError("wbs_tasks_defined must be a nonempty structured list")
        expected = f"db://{job['workflow_id']}/{job['chapter_id']}"
        if output.get("artifact_uri") != expected:
            raise ValueError(f"Chapter artifact_uri must be {expected}")

    @staticmethod
    def _hashes(output: dict) -> dict:
        hashes = output.get("chapter_hashes")
        if isinstance(hashes, list):
            if not all(isinstance(x, dict) and isinstance(x.get("chapter_id"), str) and isinstance(x.get("sha256"), str) for x in hashes):
                raise ValueError("chapter_hashes list requires chapter_id and sha256")
            result = {x["chapter_id"]: x["sha256"] for x in hashes}
            if len(result) != len(hashes):
                raise ValueError("chapter_hashes contains duplicate chapters")
            return result
        if not isinstance(hashes, dict):
            raise ValueError("Synthesis requires exact chapter_hashes")
        return hashes

    def complete(self, job_id: str, attempt_id: str, fencing_token: int, output: dict, receipt: dict) -> dict:
        self._receipt(output, receipt)
        output_json, receipt_json = canonical_json(output), canonical_json(receipt)
        with self._write() as conn:
            job = self._get(conn, job_id)
            if (job["status"] == "COMPLETED" and job["attempt_id"] == attempt_id
                    and job["fencing_token"] == fencing_token and job["output"] == output and job["receipt"] == receipt):
                return job
            if not self._owned(job, attempt_id, fencing_token):
                raise ValueError("Stale or unowned attempt cannot complete a job")
            chapters = None
            if job["kind"] == "MANIFEST_GENERATOR":
                chapters = self._manifest(job, output)
            elif job["kind"] == "CHAPTER_DRAFT":
                self._chapter(job, output)
            elif job["kind"] == "SYNTHESIS":
                expected = {row["chapter_id"]: row["sha256"] for row in conn.execute(
                    "SELECT chapter_id,sha256 FROM pipeline_artifacts WHERE workflow_id=? AND chapter_id<>'synthesis'", (job["workflow_id"],))}
                count = self._get(conn, job["workflow_id"])["payload"]["chapter_count"]
                if len(expected) != count or self._hashes(output) != expected:
                    raise ValueError("Synthesis chapter hashes must exactly match every immutable chapter")
                if receipt["provider"] != "gemini":
                    raise ValueError("Synthesis requires a Gemini process receipt")
            else:
                raise ValueError("Root jobs cannot be completed by workers")
            if job["kind"] in {"CHAPTER_DRAFT", "SYNTHESIS"}:
                text = output.get("artifact_text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Artifact text must be nonempty")
            conn.execute("""INSERT INTO pipeline_outputs(job_id,attempt_id,fencing_token,chapter_id,
                output_json,receipt_json,output_sha256,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                         (job_id, attempt_id, fencing_token, job["chapter_id"], output_json, receipt_json, output_digest(output), time.time()))
            if job["kind"] in {"CHAPTER_DRAFT", "SYNTHESIS"}:
                chapter_id = job["chapter_id"] or "synthesis"
                conn.execute("""INSERT INTO pipeline_artifacts(job_id,workflow_id,chapter_id,artifact_uri,
                    artifact_text,sha256,created_at) VALUES(?,?,?,?,?,?,?)""",
                             (job_id, job["workflow_id"], chapter_id, f"db://{job['workflow_id']}/{chapter_id}",
                              output["artifact_text"], artifact_digest(output["artifact_text"]), time.time()))
            conn.execute("UPDATE pipeline_jobs SET status='COMPLETED',lease_expires_at=NULL,updated_at=? WHERE job_id=?", (time.time(), job_id))
            self._event(conn, job, "COMPLETED", attempt_id=attempt_id, fencing_token=fencing_token, output_sha256=output_digest(output))
            if chapters is not None:
                for chapter_index, chapter in enumerate(chapters):
                    payload = {"objective": job["payload"]["objective"], "chapter_id": chapter["chapter_id"],
                               "chapter_index": chapter_index, "title": chapter["title"], "requirements": chapter["requirements"]}
                    self._insert(conn, uuid.uuid4().hex, job["workflow_id"], job["workflow_id"],
                                 "CHAPTER_DRAFT", "PENDING", payload, chapter["chapter_id"], job["max_attempts"])
                self._event(conn, job, "CHAPTERS_SCATTERED", count=len(chapters))
            elif job["kind"] == "CHAPTER_DRAFT":
                self._release_synthesis(conn, job["workflow_id"])
            elif job["kind"] == "SYNTHESIS":
                conn.execute("UPDATE pipeline_jobs SET status='COMPLETED',updated_at=? WHERE job_id=?", (time.time(), job["workflow_id"]))
                self._event(conn, self._get(conn, job["workflow_id"]), "WORKFLOW_COMPLETED", synthesis_job_id=job_id)
            return self._get(conn, job_id)

    def _release_synthesis(self, conn: sqlite3.Connection, workflow_id: str) -> None:
        root = self._get(conn, workflow_id)
        rows = conn.execute("""SELECT job_id,status FROM pipeline_jobs WHERE workflow_id=? AND kind='CHAPTER_DRAFT'
            ORDER BY json_extract(payload_json,'$.chapter_index'),chapter_id""", (workflow_id,)).fetchall()
        if root["status"] != "IN_PROGRESS" or len(rows) != root["payload"]["chapter_count"] or any(row["status"] != "COMPLETED" for row in rows):
            return
        chapters = []
        for row in rows:
            job = self._get(conn, row["job_id"])
            chapters.append({**job["payload"], "artifact_uri": job["output"]["artifact_uri"],
                             "artifact_text": job["output"]["artifact_text"], "sha256": job["artifact_sha256"]})
        payload = {**root["payload"], "chapters": chapters,
                   "chapter_hashes": {chapter["chapter_id"]: chapter["sha256"] for chapter in chapters}}
        changed = conn.execute("""UPDATE pipeline_jobs SET status='PENDING',payload_json=?,updated_at=?
            WHERE workflow_id=? AND kind='SYNTHESIS' AND status='BLOCKED'""", (canonical_json(payload), time.time(), workflow_id)).rowcount
        if changed:
            self._event(conn, root, "SYNTHESIS_RELEASED", chapter_hashes=payload["chapter_hashes"])

    def fail(self, job_id: str, attempt_id: str, fencing_token: int, error: str, retry: bool = False) -> bool:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be nonempty")
        with self._write() as conn:
            job = self._get(conn, job_id)
            if not self._owned(job, attempt_id, fencing_token):
                self._reap(conn)
                return False
            exhausted = job["attempts"] >= job["max_attempts"]
            status = "PENDING_RETRY" if retry and not exhausted else "FAILED"
            if retry and exhausted:
                error = f"Attempt budget exhausted ({job['max_attempts']} executions): {error}"
            conn.execute("""UPDATE pipeline_jobs SET status=?,lease_owner=NULL,lease_expires_at=NULL,
                error=?,updated_at=? WHERE job_id=?""", (status, error, time.time(), job_id))
            self._event(conn, job, status, attempt_id=attempt_id, fencing_token=fencing_token, error=error)
            if status == "FAILED":
                self._fail_workflow(conn, job["workflow_id"], error)
            return True

    @staticmethod
    def _fail_workflow(conn: sqlite3.Connection, workflow_id: str, error: str) -> None:
        """Fence unfinished siblings when any indispensable DAG node fails."""
        conn.execute("""UPDATE pipeline_jobs SET status='FAILED',attempt_id=NULL,fencing_token=fencing_token+1,
            lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=?
            WHERE workflow_id=? AND status NOT IN ('COMPLETED','FAILED')""", (error, time.time(), workflow_id))

    def get(self, job_id: str) -> dict:
        with self._connection() as conn:
            return self._get(conn, job_id)

    def cancel_workflow(self, workflow_id: str, error: str = "Cancelled by operator") -> list[dict]:
        """Fence unfinished work and return prior active rows for OS cleanup.

        Process termination belongs to the runtime and never runs inside this
        database transaction. Completed immutable artifacts remain available.
        """
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be nonempty")
        with self._write() as conn:
            root = self._get(conn, workflow_id)
            if root["kind"] != "MACRO_PLANNING_REQUEST":
                raise ValueError("ID does not identify a workflow root")
            if root["status"] == "COMPLETED":
                return []
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE workflow_id=?
                AND kind<>'MACRO_PLANNING_REQUEST' AND status='IN_PROGRESS'""", (workflow_id,)).fetchall()
            active = [self._get(conn, row["job_id"]) for row in rows]
            conn.execute("""UPDATE pipeline_jobs SET status='FAILED',attempt_id=NULL,fencing_token=fencing_token+1,
                lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=?
                WHERE workflow_id=? AND status NOT IN ('COMPLETED','FAILED')""", (error, time.time(), workflow_id))
            self._event(conn, root, "WORKFLOW_CANCELLED", error=error, active_job_ids=[job["job_id"] for job in active])
            return active

    def workflow(self, workflow_id: str) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            root = self._get(conn, workflow_id)
            if root["kind"] != "MACRO_PLANNING_REQUEST":
                raise ValueError("ID does not identify a workflow root")
            identifiers = conn.execute("SELECT job_id FROM pipeline_jobs WHERE workflow_id=? ORDER BY created_at,job_id", (workflow_id,)).fetchall()
            artifacts = [dict(row) for row in conn.execute("SELECT * FROM pipeline_artifacts WHERE workflow_id=? ORDER BY chapter_id", (workflow_id,))]
            events = [self._decode_event(row) for row in conn.execute(
                "SELECT * FROM pipeline_events WHERE workflow_id=? ORDER BY id", (workflow_id,))]
            return {"workflow_id": workflow_id, "status": root["status"], "root": root,
                    "jobs": [self._get(conn, row["job_id"]) for row in identifiers],
                    "artifacts": artifacts, "events": events}

    @staticmethod
    def _decode_event(row: sqlite3.Row) -> dict:
        event = dict(row)
        event["details"] = json.loads(event.pop("details_json"))
        return event

    def events(self, workflow_id: str) -> list[dict]:
        with self._connection() as conn:
            return [self._decode_event(row) for row in conn.execute(
                "SELECT * FROM pipeline_events WHERE workflow_id=? ORDER BY id", (workflow_id,))]

    def event_batch(self, after_id: int, limit: int = 1000) -> list[dict]:
        """Read a bounded global event cursor without mutating workflow state."""
        if type(after_id) is not int or after_id < 0:
            raise ValueError("after_id must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        with self._connection() as conn:
            return [self._decode_event(row) for row in conn.execute(
                "SELECT * FROM pipeline_events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))]

    def list_workflows(self, limit: int = 100) -> list[dict]:
        """Return recent root records without loading every document artifact."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        with self._connection() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE kind='MACRO_PLANNING_REQUEST'
                ORDER BY created_at DESC,job_id LIMIT ?""", (limit,)).fetchall()
            return [self._get(conn, row["job_id"]) for row in rows]

    def active_jobs(self) -> list[dict]:
        """Snapshot currently leased execution jobs for runtime supervision."""
        with self._connection() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE kind<>'MACRO_PLANNING_REQUEST'
                AND status='IN_PROGRESS' ORDER BY created_at,job_id""").fetchall()
            return [self._get(conn, row["job_id"]) for row in rows]

    def set_context(self, job_id: str, xml: str, watermark: str) -> None:
        """Persist scheduler-produced context; CLI workers receive only its XML."""
        if not isinstance(xml, str) or not xml.strip():
            raise ValueError("Context XML must be nonempty")
        if not isinstance(watermark, str) or not re.fullmatch(r"[0-9a-f]{64}", watermark):
            raise ValueError("Context watermark must be a SHA256 hex string")
        with self._write() as conn:
            self._get(conn, job_id)
            conn.execute("""INSERT INTO pipeline_context(job_id,xml,watermark,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET xml=excluded.xml,watermark=excluded.watermark,updated_at=excluded.updated_at""",
                         (job_id, xml, watermark, time.time()))
            # Context delivery is an Oracle output, not a new input event.
            # Emitting it into the polled stream would create a feedback loop.

    def get_context(self, job_id: str) -> dict | None:
        return self.get(job_id)["context"]
