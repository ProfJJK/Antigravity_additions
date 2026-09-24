-- CoChem Pipeline 3.0.0 Phase 1a SQLite WAL Schema
-- Tables: kanban_tasks_v3, kanban_telemetry_v3, audit_verdicts, credit_ledger_v3, quota_state, watchdog_events_v3

CREATE TABLE IF NOT EXISTS kanban_tasks_v3 (
    task_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    current_state INTEGER NOT NULL DEFAULT 0,
    payload_uri TEXT NOT NULL,
    lease_expires_at TIMESTAMP DEFAULT NULL,
    heartbeat_at TIMESTAMP DEFAULT NULL,
    owner TEXT DEFAULT NULL,
    worker_pid INTEGER DEFAULT NULL,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- AC2: Composite index on kanban_tasks_v3 with exact sequence: (status, priority, lease_expires_at)
CREATE INDEX IF NOT EXISTS idx_tasks_v3_status_priority_lease
ON kanban_tasks_v3 (status, priority, lease_expires_at);

CREATE TABLE IF NOT EXISTS kanban_telemetry_v3 (
    telemetry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v3(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_verdicts (
    verdict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    auditor TEXT NOT NULL,
    verdict TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v3(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS credit_ledger_v3 (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0.0,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v3(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quota_state (
    provider TEXT PRIMARY KEY,
    current_usage INTEGER NOT NULL DEFAULT 0,
    quota_limit INTEGER NOT NULL DEFAULT 0,
    reset_at TIMESTAMP DEFAULT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchdog_events_v3 (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    event_type TEXT NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v3(task_id) ON DELETE SET NULL
);
