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

from . import routing_store as routes
from .coding_store import CodingStoreMixin, CODING_KINDS, CONTROLLER_KINDS, SCHEMA as CODING_SCHEMA


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
    kind TEXT NOT NULL CHECK(kind IN ('MACRO_PLANNING_REQUEST','MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS','CODE_REQUEST','CODE_PLAN','CODE_PLAN_REVIEW','CODE_TEST_AUTHOR','CODE_EDIT','CODE_TEST','CODE_REVIEW','CODE_RESEARCH','CODE_INTEGRATE')),
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
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_unique_stage ON pipeline_jobs(workflow_id,kind) WHERE kind IN ('MACRO_PLANNING_REQUEST','MANIFEST_GENERATOR','SYNTHESIS','CODE_REQUEST');
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
        AND j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
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
    SELECT CASE WHEN NEW.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
        AND NOT EXISTS(SELECT 1 FROM pipeline_outputs WHERE job_id=NEW.job_id)
        THEN RAISE(ABORT,'completion requires an accepted output') END;
    SELECT CASE WHEN NEW.kind='MACRO_PLANNING_REQUEST'
        AND NOT EXISTS(SELECT 1 FROM pipeline_jobs WHERE workflow_id=NEW.workflow_id AND kind='SYNTHESIS' AND status='COMPLETED')
        THEN RAISE(ABORT,'root completion requires synthesis') END;
    SELECT CASE WHEN NEW.kind='CODE_REQUEST'
        AND NOT EXISTS(SELECT 1 FROM pipeline_jobs j JOIN pipeline_outputs o USING(job_id)
          WHERE j.workflow_id=NEW.workflow_id AND j.kind='CODE_INTEGRATE' AND j.status='COMPLETED'
          AND json_extract(o.output_json,'$.status')='INTEGRATED')
        THEN RAISE(ABORT,'coding root completion requires verified integration') END;
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


class JobStore(CodingStoreMixin):
    def __init__(self, path: str | Path, max_attempts: int = 3, *, routing_policy=None,
                 cleanup_boot_id: int | None = None):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be an integer from 1 to 100")
        self.max_attempts = max_attempts
        self.transition_telemetry = None
        self.routing_policy = routes.policy_dict(routing_policy) if routing_policy is not None else None
        if cleanup_boot_id is not None and (type(cleanup_boot_id) is not int or cleanup_boot_id<=0):
            raise ValueError('cleanup_boot_id must be a positive trusted Windows boot identity')
        self.cleanup_boot_id = cleanup_boot_id
        if str(path) == ":memory:":
            raise ValueError("A durable on-disk SQLite path is required")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._migrate_coding_schema(conn)
            conn.executescript(SCHEMA)
            conn.executescript(CODING_SCHEMA)
            conn.executescript(routes.SCHEMA)
            # Older 4.2.2 draft databases predate a durable execution budget.
            # Migrate under the write lock so concurrently starting workers
            # cannot both attempt to add the same column.
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(pipeline_jobs)")}
                if "attempts" not in columns:
                    conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0)")
                    conn.execute("UPDATE pipeline_jobs SET attempts=fencing_token WHERE kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')")
                if "max_attempts" not in columns:
                    conn.execute("ALTER TABLE pipeline_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts>=1)")
                    conn.execute("UPDATE pipeline_jobs SET max_attempts=?", (max_attempts,))
                cleanup_columns = {row['name'] for row in conn.execute('PRAGMA table_info(pipeline_execution_cleanup)')}
                if 'boot_id' not in cleanup_columns:
                    conn.execute('ALTER TABLE pipeline_execution_cleanup ADD COLUMN boot_id INTEGER CHECK(boot_id IS NULL OR boot_id>0)')
                if 'observed_boot_id' not in cleanup_columns:
                    conn.execute('ALTER TABLE pipeline_execution_cleanup ADD COLUMN observed_boot_id INTEGER CHECK(observed_boot_id IS NULL OR observed_boot_id>0)')
                if 'containment_id' not in cleanup_columns:
                    conn.execute('ALTER TABLE pipeline_execution_cleanup ADD COLUMN containment_id TEXT')
                if 'owner_containment_id' not in cleanup_columns:
                    conn.execute('ALTER TABLE pipeline_execution_cleanup ADD COLUMN owner_containment_id TEXT')
                # Capture policy for legacy active workflows without changing a
                # live legacy attempt. Pending jobs adopt it only when claimed.
                if self.routing_policy is not None:
                    for root in conn.execute("SELECT job_id FROM pipeline_jobs WHERE kind IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST') AND status='IN_PROGRESS'").fetchall():
                        routes.capture_workflow(conn, root['job_id'], self.routing_policy)
                    for row in conn.execute('''SELECT j.job_id FROM pipeline_jobs j LEFT JOIN pipeline_route_reservations r
                        ON r.job_id=j.job_id AND r.attempt_id=j.attempt_id WHERE j.status='IN_PROGRESS'
                        AND j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST','CODE_TEST','CODE_INTEGRATE') AND r.reservation_id IS NULL''').fetchall():
                        legacy = self._get(conn,row['job_id'])
                        routes.guard_execution(conn,legacy,legacy['worker_slot'],observed_boot_id=self.cleanup_boot_id)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _migrate_coding_schema(conn):
        existing = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='pipeline_jobs'").fetchone()
        if existing is None or "'CODE_PLAN'" in existing[0]:
            return
        # Preserve every child table and FK target name. Renaming the old table
        # would rewrite references in immutable outputs and reservation history.
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('BEGIN IMMEDIATE')
        try:
            columns = [row['name'] for row in conn.execute('PRAGMA table_info(pipeline_jobs)')]
            names = ','.join(columns)
            conn.execute('CREATE TEMP TABLE coding_migration_jobs AS SELECT * FROM pipeline_jobs')
            conn.execute('DROP TABLE pipeline_jobs')
            table_sql = SCHEMA.split(';', 1)[0]
            conn.execute(table_sql)
            conn.execute('INSERT INTO pipeline_jobs('+names+') SELECT '+names+' FROM coding_migration_jobs')
            conn.execute('DROP TABLE coding_migration_jobs')
            # Other tables own these triggers; rebuild their expanded root rule.
            conn.execute('DROP TRIGGER IF EXISTS pipeline_output_owner')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys=ON')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Coding schema migration found broken ownership references')

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.create_function('cochem_transition_telemetry',0,lambda:canonical_json(self.transition_telemetry))
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
        result['routing'] = routes.get_state(conn, job_id)
        result['routing_policy'] = result['routing']['policy'] if result['routing'] is not None else None
        result['route'] = routes.get_route(conn, job_id)
        if result['route'] is not None and result['worker_slot'] is None:
            result['worker_slot'] = result['route'].get('worker_slot')
        if result['worker_slot'] is None and result['attempt_id'] is not None:
            guard=conn.execute('SELECT worker_slot FROM pipeline_execution_cleanup WHERE job_id=? AND attempt_id=?',
                               (job_id,result['attempt_id'])).fetchone()
            if guard is not None:
                result['worker_slot']=guard['worker_slot']
        return result

    def set_transition_telemetry(self, measured: dict | None) -> None:
        # Sensor collection belongs outside SQLite. Publish one immutable JSON
        # copy; the event transaction records its actual observation timestamp.
        self.transition_telemetry = json.loads(canonical_json(measured)) if measured is not None else None

    def _event(self, conn: sqlite3.Connection, job: dict, event: str, **details: Any) -> None:
        details['telemetry'] = routes.transition_evidence(conn,job,event_name=event)
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
        routes.ensure_job(conn, job_id)

    def submit(self, objective: str, requirements: list[str], chapter_count: int = 6,
               workflow_id: str | None = None, *, max_attempts: int | None = None,
               max_dispatches: int | None = None) -> dict:
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("objective must be nonempty")
        _strings(requirements, "requirements")
        if type(chapter_count) is not int or not 1 <= chapter_count <= 64:
            raise ValueError("chapter_count must be an integer from 1 to 64")
        workflow_id = _identifier(workflow_id or uuid.uuid4().hex, "workflow_id")
        budget = self.max_attempts if max_attempts is None else max_attempts
        if type(budget) is not int or not 1 <= budget <= self.max_attempts:
            raise ValueError('Workflow max_attempts may only lower the configured execution budget')
        if max_dispatches is not None and (type(max_dispatches) is not int or not 1 <= max_dispatches <= 1000000):
            raise ValueError('max_dispatches must be an integer from 1 to 1000000')
        # A caller explicitly requesting the new all-dispatch cap opts into
        # routing even when using the backwards-compatible constructor.
        requested_policy = self.routing_policy if self.routing_policy is not None else ({} if max_dispatches is not None else None)
        payload = {"objective": objective, "requirements": requirements, "chapter_count": chapter_count}
        with self._write() as conn:
            existing = conn.execute("SELECT job_id FROM pipeline_jobs WHERE job_id=?", (workflow_id,)).fetchone()
            if existing:
                root = self._get(conn, workflow_id)
                if root["kind"] not in ("MACRO_PLANNING_REQUEST", "CODE_REQUEST") or root["payload"] != payload:
                    raise ValueError("workflow_id already belongs to a different request")
                if max_attempts is not None and root['max_attempts']!=budget:
                    raise ValueError('Existing workflow attempt budget is immutable')
                routes.capture_workflow(conn, workflow_id, requested_policy, max_dispatches)
            else:
                self._insert(conn, workflow_id, workflow_id, None, "MACRO_PLANNING_REQUEST", "IN_PROGRESS", payload, max_attempts=budget)
                routes.capture_workflow(conn, workflow_id, requested_policy, max_dispatches)
                self._insert(conn, uuid.uuid4().hex, workflow_id, workflow_id, "MANIFEST_GENERATOR", "PENDING", payload, max_attempts=budget)
                self._insert(conn, uuid.uuid4().hex, workflow_id, workflow_id, "SYNTHESIS", "BLOCKED", payload, max_attempts=budget)
                self._event(conn, self._get(conn, workflow_id), "WORKFLOW_SUBMITTED", chapter_count=chapter_count)
        return self.workflow(workflow_id)

    @staticmethod
    def _lease_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        return float(value)

    def _reap(self, conn: sqlite3.Connection) -> list[dict]:
        expired = conn.execute("""SELECT j.job_id FROM pipeline_jobs j LEFT JOIN pipeline_routing_jobs r USING(job_id)
            WHERE j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
            AND ((j.status='IN_PROGRESS' AND j.lease_expires_at<=?)
              OR (j.status IN ('PENDING','PENDING_RETRY') AND
                 ((r.job_id IS NULL AND j.kind NOT IN ('CODE_TEST','CODE_INTEGRATE') AND j.attempts>=j.max_attempts) OR
                  (r.job_id IS NOT NULL AND (r.failure_count>=j.max_attempts OR
                   (r.max_dispatches IS NOT NULL AND r.dispatches>=r.max_dispatches))))))""", (time.time(),)).fetchall()
        results = []
        for row in expired:
            previous = self._get(conn, row["job_id"])
            if previous["status"] == "FAILED":
                continue  # Another exhausted sibling in this transaction fenced it.
            root = self._get(conn, previous["workflow_id"])
            if (previous['status']=='IN_PROGRESS' and previous.get('routing') is None and self.routing_policy is not None
                    and previous['kind'] not in CONTROLLER_KINDS):
                routes.guard_execution(conn,previous,previous['worker_slot'],observed_boot_id=self.cleanup_boot_id)
            state = previous.get('routing')
            if state is not None and previous['status']=='IN_PROGRESS':
                state = routes.failed(conn, previous, 'lease_expired')
            controller_failures = None
            if previous['kind'] in CONTROLLER_KINDS:
                conn.execute('INSERT OR IGNORE INTO coding_controller_retries(job_id) VALUES(?)',(previous['job_id'],))
                conn.execute('UPDATE coding_controller_retries SET failures=failures+1 WHERE job_id=?',(previous['job_id'],))
                controller_failures=conn.execute('SELECT failures FROM coding_controller_retries WHERE job_id=?',(previous['job_id'],)).fetchone()[0]
            exhausted = ((state['failure_count']>=previous['max_attempts'] or
                          (state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches']))
                         if state is not None else (controller_failures if controller_failures is not None else previous['attempts']) >= previous['max_attempts'])
            status = "FAILED" if exhausted or root["status"] == "FAILED" else "PENDING_RETRY"
            error = f"Attempt budget exhausted ({previous['max_attempts']} executions)" if exhausted else "Execution lease expired"
            if exhausted and state is not None:
                error = (f"Dispatch budget exhausted ({state['max_dispatches']} reservations)"
                         if state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches'] else
                         f"Task failure budget exhausted ({previous['max_attempts']} failures)")
            conn.execute("""UPDATE pipeline_jobs SET status=?,attempt_id=NULL,lease_owner=NULL,
                lease_expires_at=NULL,error=?,updated_at=? WHERE job_id=?""",
                         (status, error, time.time(), previous["job_id"]))
            routes.release(conn, previous['job_id'], 'lease_expired', 'FAILED' if status=='FAILED' else 'READY')
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
              exclude_job_ids: Iterable[str] = (), worker_slot: str | None = None,
              requires_cleanup: bool = False, cleanup_boot_id: int | None = None,
              containment_id: str | None = None, allowed_kinds: Iterable[str] | None = None) -> dict | None:
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be nonempty")
        duration = self._lease_seconds(lease_seconds)
        if type(max_workers) is not int or not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be an integer from 1 to 64")
        max_workers = min(max_workers, 4)
        if type(requires_cleanup) is not bool:
            raise ValueError('requires_cleanup must be a boolean')
        if cleanup_boot_id is not None and (type(cleanup_boot_id) is not int or cleanup_boot_id<=0):
            raise ValueError('cleanup_boot_id must be a positive trusted Windows boot identity')
        if containment_id is not None and (not isinstance(containment_id,str) or not re.fullmatch(r'[0-9a-f]{32}',containment_id)):
            raise ValueError('containment_id must be the trusted 32-character launch nonce')
        if isinstance(exclude_job_ids, (str, bytes)):
            raise ValueError("exclude_job_ids must be an iterable of job IDs")
        excluded = tuple(sorted({_identifier(value, "excluded job_id") for value in exclude_job_ids}))
        if len(excluded) > 1000:
            raise ValueError("At most 1000 job IDs may be excluded")
        if worker_slot is not None:
            _identifier(worker_slot, "worker_slot")
        exclusion = " AND j.job_id NOT IN (" + ",".join("?" for _ in excluded) + ")" if excluded else ""
        parameters: list[Any] = list(excluded)
        if allowed_kinds is not None:
            if isinstance(allowed_kinds,(str,bytes)):
                raise ValueError('allowed_kinds must be an iterable of executable kinds')
            permitted=tuple(sorted(set(allowed_kinds)))
            valid={'MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS',*CODING_KINDS}-{'CODE_REQUEST'}
            if not permitted or any(kind not in valid for kind in permitted):
                raise ValueError('allowed_kinds contains an unsupported execution kind')
            exclusion+=' AND j.kind IN ('+','.join('?' for _ in permitted)+')'
            parameters.extend(permitted)
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
            if routes.cleanup_barriers(conn):
                return None
            active = conn.execute("SELECT count(*) FROM pipeline_jobs WHERE status='IN_PROGRESS' AND kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')").fetchone()[0]
            if active >= max_workers:
                return None
            if self.routing_policy is not None and conn.execute('''SELECT 1 FROM pipeline_jobs j
                LEFT JOIN pipeline_route_reservations r ON r.job_id=j.job_id AND r.attempt_id=j.attempt_id
                WHERE j.status='IN_PROGRESS' AND j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST','CODE_TEST','CODE_INTEGRATE') AND r.reservation_id IS NULL LIMIT 1''').fetchone():
                # Legacy active executions have no proven routing reservation.
                # Wait for their fenced completion/reaping and OS cleanup rather
                # than inventing a target or ignoring their resource occupancy.
                return None
            rows = conn.execute("""SELECT j.job_id FROM pipeline_jobs j JOIN pipeline_jobs root ON root.job_id=j.workflow_id
                WHERE j.status IN ('PENDING','PENDING_RETRY') AND j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
                AND root.status='IN_PROGRESS'
                AND NOT EXISTS(SELECT 1 FROM coding_controller_retries cr WHERE cr.job_id=j.job_id AND cr.next_eligible_at>(julianday('now')-2440587.5)*86400.0)""" + exclusion + binding_filter
                               + " ORDER BY j.created_at,j.job_id", parameters).fetchall()
            selected, selection = None, None
            for row in rows:
                candidate = self._get(conn, row['job_id'])
                if candidate['status'] not in ('PENDING','PENDING_RETRY') or self._get(conn,candidate['workflow_id'])['status']!='IN_PROGRESS':
                    continue
                choice, routed = ((None, False) if candidate['kind'] in CONTROLLER_KINDS
                                  else routes.select(conn, candidate, self.routing_policy))
                if routed and choice is None:
                    continue
                if routed and choice.get('exhausted'):
                    self._fail_workflow(conn, candidate['workflow_id'], choice['exhausted'])
                    continue
                if not routed and candidate['kind'] not in CONTROLLER_KINDS and candidate['attempts']>=candidate['max_attempts']:
                    continue
                selected, selection = candidate, choice
                break
            if selected is None:
                return None
            if selected["kind"] == "CHAPTER_DRAFT" and worker_slot is not None and selected["worker_slot"] is None:
                conn.execute("""INSERT INTO pipeline_worker_ownership(workflow_id,chapter_id,slot,job_id,created_at)
                    VALUES(?,?,?,?,?)""", (selected["workflow_id"], selected["chapter_id"], worker_slot, selected["job_id"], time.time()))
            timestamp = time.time()
            conn.execute("""UPDATE pipeline_jobs SET status='IN_PROGRESS',attempt_id=?,fencing_token=fencing_token+1,attempts=attempts+1,
                lease_owner=?,lease_expires_at=?,updated_at=?,error=NULL WHERE job_id=?""",
                         (uuid.uuid4().hex, owner, timestamp + duration, timestamp, row["job_id"]))
            job = self._get(conn, row["job_id"])
            if selection is not None:
                routes.reserve(conn, job, selection, worker_slot)
                job = self._get(conn, row['job_id'])
            if requires_cleanup:
                routes.guard_execution(conn,job,worker_slot,cleanup_boot_id if cleanup_boot_id is not None else self.cleanup_boot_id,
                                       containment_id=None if job['kind']=='CODE_TEST' else containment_id,
                                       owner_containment_id=containment_id)
                job['worker_slot']=worker_slot
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
            if job['routing'] is not None:
                route = job['route']
                required = {'provider': route['provider'], 'requested_model': route['model'],
                            'route_reservation_id': route['reservation_id'], 'attempt_id': attempt_id,
                            'fencing_token': fencing_token, 'worker_slot': job['worker_slot'],
                            'job_id': job_id, 'workflow_id': job['workflow_id']}
                if any(receipt.get(key) != value for key,value in required.items()):
                    raise ValueError('Completion receipt does not match the reserved model/attempt/identity')
                if type(receipt.get('fencing_token')) is not int:
                    raise ValueError('Completion receipt requires an integer fencing token')
                if receipt.get('reported_model') not in (None,route['model']):
                    raise ValueError('Completion receipt reports a different native model')
                effort = route.get('reasoning_effort')
                if receipt.get('requested_effort') != effort:
                    raise ValueError('Completion receipt does not match requested reasoning effort')
                if effort is not None and receipt.get('reported_effort') not in (None,effort):
                    raise ValueError('Completion receipt reports a different reasoning effort')
                if effort is not None and receipt.get('selected_route',{}).get('reasoning_effort') != effort:
                    raise ValueError('Completion receipt does not match reserved reasoning effort')
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
            routes.release(conn, job_id, 'completed', 'COMPLETED')
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

    def fail(self, job_id: str, attempt_id: str, fencing_token: int, error: str, retry: bool = False,
             *, category: str = 'code', retry_after_seconds: float | None = None,
             hold_scope: str | None = None) -> bool:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be nonempty")
        with self._write() as conn:
            job = self._get(conn, job_id)
            if not self._owned(job, attempt_id, fencing_token):
                self._reap(conn)
                return False
            if job['kind'] in CONTROLLER_KINDS:
                conn.execute('INSERT OR IGNORE INTO coding_controller_retries(job_id) VALUES(?)',(job_id,))
                if category not in ('resource','configuration','compatibility'):
                    conn.execute('UPDATE coding_controller_retries SET failures=failures+1 WHERE job_id=?',(job_id,))
                failures = conn.execute('SELECT failures FROM coding_controller_retries WHERE job_id=?',(job_id,)).fetchone()[0]
                held = category in ('configuration','compatibility') or (category=='resource' and hold_scope=='job')
                status = ('BLOCKED' if held else 'PENDING_RETRY') if retry and failures<job['max_attempts'] else 'FAILED'
                delay = max(1.,min(86400.,retry_after_seconds or 30.)) if category=='resource' else 0.
                conn.execute('UPDATE coding_controller_retries SET next_eligible_at=? WHERE job_id=?',(time.time()+delay,job_id))
                conn.execute('UPDATE pipeline_jobs SET status=?,lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=? WHERE job_id=?',
                             (status,error,time.time(),job_id))
                self._event(conn,job,status,category=category,attempt_id=attempt_id,fencing_token=fencing_token,error=error)
                if status=='FAILED':
                    self._fail_workflow(conn,job['workflow_id'],error)
                return True
            if (job['kind'] in ('CODE_EDIT','CODE_TEST_AUTHOR','CODE_REVIEW') and retry
                    and category in ('code','protocol')):
                routes.failed(conn,job,category,retry_after_seconds,hold_scope,self.routing_policy)
                conn.execute("UPDATE pipeline_jobs SET status='FAILED',lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=? WHERE job_id=?",
                             (error,time.time(),job_id))
                state = self._coding_state(conn,job['workflow_id'])
                root = self._get(conn,job['workflow_id'])
                # Fence outstanding reviews before starting a replacement chunk.
                for review_id in state.get('pending_reviews',[]):
                    if review_id!=job_id:
                        conn.execute("UPDATE pipeline_jobs SET status='FAILED',attempt_id=NULL,fencing_token=fencing_token+1,lease_expires_at=NULL,error=? WHERE job_id=? AND status<>'COMPLETED'",(error,review_id))
                        routes.release(conn,review_id,error,'FAILED')
                state['pending_reviews'] = []
                state['retry_stage'] = 'CODE_TEST_AUTHOR' if job['kind']=='CODE_TEST_AUTHOR' else 'CODE_EDIT'
                self._event(conn,job,'CODING_STAGE_REJECTED',category=category,error=error)
                self._coding_retry(conn,root,state,error)
                self._save_coding(conn,job['workflow_id'],state)
                return True
            state = routes.failed(conn, job, category, retry_after_seconds, hold_scope,self.routing_policy)
            exhausted = ((state['failure_count']>=job['max_attempts'] or
                          (state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches']))
                         if state is not None else job["attempts"] >= job["max_attempts"])
            status = "PENDING_RETRY" if retry and not exhausted else "FAILED"
            if state is not None and state['state']=='BLOCKED' and retry and not exhausted:
                status = 'BLOCKED'
            if retry and exhausted:
                if state is None:
                    error = f"Attempt budget exhausted ({job['max_attempts']} executions): {error}"
                elif state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches']:
                    error = f"Dispatch budget exhausted ({state['max_dispatches']} reservations): {error}"
                else:
                    error = f"Task failure budget exhausted ({job['max_attempts']} failures): {error}"
            conn.execute("""UPDATE pipeline_jobs SET status=?,lease_owner=NULL,lease_expires_at=NULL,
                error=?,updated_at=? WHERE job_id=?""", (status, error, time.time(), job_id))
            self._event(conn, job, status, attempt_id=attempt_id, fencing_token=fencing_token, error=error)
            if status == "FAILED":
                routes.release(conn, job_id, error, 'FAILED')
                self._fail_workflow(conn, job["workflow_id"], error)
            return True

    @staticmethod
    def _fail_workflow(conn: sqlite3.Connection, workflow_id: str, error: str) -> None:
        """Fence unfinished siblings when any indispensable DAG node fails."""
        conn.execute("""UPDATE pipeline_jobs SET status='FAILED',attempt_id=NULL,fencing_token=fencing_token+1,
            lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=?
            WHERE workflow_id=? AND status NOT IN ('COMPLETED','FAILED')""", (error, time.time(), workflow_id))
        routes.release_workflow(conn, workflow_id, error)

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
            if root["kind"] not in ("MACRO_PLANNING_REQUEST", "CODE_REQUEST"):
                raise ValueError("ID does not identify a workflow root")
            if root["status"] == "COMPLETED":
                return []
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE workflow_id=?
                AND kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST') AND status='IN_PROGRESS'""", (workflow_id,)).fetchall()
            active = [self._get(conn, row["job_id"]) for row in rows]
            conn.execute("""UPDATE pipeline_jobs SET status='FAILED',attempt_id=NULL,fencing_token=fencing_token+1,
                lease_owner=NULL,lease_expires_at=NULL,error=?,updated_at=?
                WHERE workflow_id=? AND status NOT IN ('COMPLETED','FAILED')""", (error, time.time(), workflow_id))
            routes.release_workflow(conn, workflow_id, error)
            if root['kind']=='CODE_REQUEST':
                coding_state = self._coding_state(conn,workflow_id)
                coding_state['status'] = 'CANCELLED'
                coding_state['cancel_reason'] = error
                coding_state['integration_reconciliation_required'] = bool(conn.execute("SELECT 1 FROM coding_integration_intents i JOIN pipeline_jobs j USING(job_id) WHERE j.workflow_id=? AND i.state='PREPARED' LIMIT 1",(workflow_id,)).fetchone())
                self._save_coding(conn,workflow_id,coding_state)
            self._event(conn, root, "WORKFLOW_CANCELLED", error=error, active_job_ids=[job["job_id"] for job in active])
            return active

    def workflow(self, workflow_id: str) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            root = self._get(conn, workflow_id)
            if root["kind"] not in ("MACRO_PLANNING_REQUEST", "CODE_REQUEST"):
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
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE kind IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
                ORDER BY created_at DESC,job_id LIMIT ?""", (limit,)).fetchall()
            return [self._get(conn, row["job_id"]) for row in rows]

    def active_jobs(self) -> list[dict]:
        """Snapshot currently leased execution jobs for runtime supervision."""
        with self._connection() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("""SELECT job_id FROM pipeline_jobs WHERE kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')
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

    def set_route_hold(self, scope: str, key: str, seconds: float, reason: str = 'busy') -> None:
        """Record controller-observed availability without creating an execution."""
        with self._write() as conn:
            routes.set_hold(conn, scope, key, seconds, reason)

    def routing_status(self) -> dict:
        with self._connection() as conn:
            conn.execute('BEGIN')
            return {'holds': [dict(row) for row in conn.execute('SELECT * FROM pipeline_route_holds WHERE until_at>? ORDER BY scope,resource_key',(time.time(),))],
                    'active_reservations': [json.loads(row['route_json']) for row in conn.execute('SELECT route_json FROM pipeline_route_reservations WHERE released_at IS NULL ORDER BY reserved_at')],
                    'execution_quarantines': routes.cleanup_barriers(conn),
                    'counts': {row['state']:row['n'] for row in conn.execute('SELECT state,count(*) AS n FROM pipeline_routing_jobs GROUP BY state')}}

    def quarantine_execution(self, job_id: str, attempt_id: str, fencing_token: int,
                             worker_slot: str | None, reason: str, pid: int | None = None) -> bool:
        """Persist an unresolved native process/profile cleanup; admission fails closed."""
        with self._write() as conn:
            job = self._get(conn,job_id)
            return routes.quarantine_execution(conn,job,attempt_id,fencing_token,worker_slot,reason,pid)

    def clear_execution_quarantine(self, job_id: str, attempt_id: str, fencing_token: int) -> bool:
        """Trusted owner acknowledgement after verified OS process-tree/profile closure.

        This method is deliberately absent from the public controller API. An
        expired lease, elapsed time, restart, or model assertion is not proof.
        """
        with self._write() as conn:
            return routes.clear_execution(conn,self._get(conn,job_id),attempt_id,fencing_token)

    confirm_execution_cleanup = clear_execution_quarantine

    def execution_quarantines(self) -> list[dict]:
        with self._connection() as conn:
            return routes.cleanup_barriers(conn)

    def execution_owner_stopped(self, job_id,attempt_id,fencing_token) -> bool:
        """Read only a frozen supervisor's exact retained-Job owner-stop proof."""
        with self._connection() as conn:
            return conn.execute('''SELECT 1 FROM pipeline_execution_owner_stops p JOIN pipeline_execution_cleanup c
                ON c.job_id=p.job_id AND c.attempt_id=p.attempt_id AND c.fencing_token=p.fencing_token
                WHERE p.job_id=? AND p.attempt_id=? AND p.fencing_token=?
                  AND p.containment_id=c.owner_containment_id AND p.boot_id=c.boot_id''',
                (job_id,attempt_id,fencing_token)).fetchone() is not None

    def quarantine_unclosed_executions(self, reason: str = 'Warden restarted before native cleanup proof') -> list[dict]:
        """Startup-only controller barrier before touching any former workspace."""
        if not isinstance(reason,str) or not reason.strip() or '\x00' in reason:
            raise ValueError('Execution quarantine requires a nonempty reason')
        with self._write() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM pipeline_execution_cleanup WHERE cleared_at IS NULL')]
            for row in rows:
                routes.quarantine_execution(conn,self._get(conn,row['job_id']),row['attempt_id'],
                                            row['fencing_token'],row['worker_slot'],reason,row['pid'])
            return routes.cleanup_barriers(conn)

    def recover_execution_quarantines_after_boot(self, current_boot_id: int) -> list[dict]:
        """Trusted native controller proof: previous boot's processes cannot survive.

        Only the Windows kernel boot identity may be supplied. Daemon start
        times/PIDs and absent in-memory handles do not establish process death.
        For legacy attempts, observed_boot_id records when uncertainty was
        witnessed; it does not claim to know the original execution boot.
        A later different Windows boot also proves those observed processes
        cannot remain. Rows without either witness require verified closure.
        """
        if type(current_boot_id) is not int or current_boot_id<=0:
            raise ValueError('current_boot_id must be a positive trusted Windows boot identity')
        with self._write() as conn:
            rows = [dict(row) for row in conn.execute('''SELECT * FROM pipeline_execution_cleanup
                WHERE cleared_at IS NULL AND ((boot_id IS NOT NULL AND boot_id<>?) OR
                 (boot_id IS NULL AND observed_boot_id IS NOT NULL AND observed_boot_id<>?))''',(current_boot_id,current_boot_id))]
            for row in rows:
                job = self._get(conn,row['job_id'])
                routes.clear_execution(conn,job,row['attempt_id'],row['fencing_token'])
                routes.event(conn,job,'EXECUTION_CLEANUP_VERIFIED_AFTER_REBOOT',attempt_id=row['attempt_id'],
                             previous_boot_id=row['boot_id'],observed_boot_id=row['observed_boot_id'],current_boot_id=current_boot_id)
            return rows

    def resume_routing(self, job_id: str, reason: str) -> dict:
        """Resume an operator-held route without replacing captured policy or budgets."""
        if not isinstance(reason,str) or not reason.strip() or '\x00' in reason:
            raise ValueError('Routing resume requires a nonempty reason without NUL characters')
        with self._write() as conn:
            job = self._get(conn, job_id)
            if job['routing'] is None or job['routing']['state'] != 'BLOCKED' or job['status'] != 'BLOCKED':
                raise ValueError('Only operator-held routing jobs can be resumed')
            if self._get(conn,job['workflow_id'])['status']!='IN_PROGRESS':
                raise ValueError('Cannot resume routing in a finished workflow')
            state = job['routing']
            now = time.time()
            cycle_limit = state['policy']['max_routing_cycles']
            if ((cycle_limit and state['cycle']>=cycle_limit) or
                (state['expires_at'] is not None and now>=state['expires_at']) or
                (state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches']) or
                state['failure_count']>=job['max_attempts']):
                raise ValueError('Captured routing or execution limit is exhausted; resume cannot reset budgets')
            conn.execute("UPDATE pipeline_routing_jobs SET state='READY',next_eligible_at=0,wait_reason='operator_resume' WHERE job_id=?",(job_id,))
            conn.execute("UPDATE pipeline_jobs SET status='PENDING_RETRY',error=NULL,updated_at=? WHERE job_id=?",(time.time(),job_id))
            routes.event(conn,job,'ROUTING_RESUMED',reason=reason)
            return self._get(conn,job_id)
