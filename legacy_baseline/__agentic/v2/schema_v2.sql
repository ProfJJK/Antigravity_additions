PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS kanban_tasks_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'running', 'review', 'done', 'failed', 'abandoned')),
    priority INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TEXT DEFAULT NULL,
    heartbeat_at TEXT DEFAULT NULL,
    owner TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kanban_tasks_v2_claim
ON kanban_tasks_v2 (status, priority, lease_expires_at);

CREATE TABLE IF NOT EXISTS quota_state (
    provider TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('QUOTA_OK', 'QUOTA_EXHAUSTED', 'QUOTA_RECOVERING')),
    backoff_until TEXT DEFAULT NULL,
    last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kanban_telemetry_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    worker_id TEXT NOT NULL,
    execution_time_ms INTEGER NOT NULL DEFAULT 0,
    status_verdict TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v2(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    verdict TEXT NOT NULL DEFAULT 'PASS' CHECK (verdict IN ('PASS', 'FAIL', 'SPOOFING_RISK', 'INDETERMINATE', 'SPOOFING_DETECTED')),
    details TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v2(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS credit_ledger_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER DEFAULT NULL,
    provider TEXT DEFAULT NULL,
    cost REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v2(id) ON DELETE SET NULL,
    FOREIGN KEY (provider) REFERENCES quota_state(provider) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS watchdog_events_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER DEFAULT NULL,
    daemon_name TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'HEARTBEAT_OK' CHECK (event_type IN ('HEARTBEAT_OK', 'AUTH_FAILURE', 'SPAWN_STORM_DETECTED', 'CRASH', 'RESTART', 'DAEMON_CRASHED', 'DAEMON_RESTARTED')),
    pid INTEGER DEFAULT NULL,
    details TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES kanban_tasks_v2(id) ON DELETE SET NULL
);
