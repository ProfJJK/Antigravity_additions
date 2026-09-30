CREATE TABLE kanban_tasks (
        task_id TEXT PRIMARY KEY,
        workflow_type TEXT NOT NULL DEFAULT 'testbank',
        status TEXT NOT NULL,
        file_path TEXT NOT NULL DEFAULT '',
        payload_uri TEXT NOT NULL DEFAULT '',
        sha256_hash TEXT NOT NULL DEFAULT ''
    , payload TEXT CHECK(json_valid(payload)), schema_name TEXT, schema_version TEXT, schema_sha256 TEXT, producer_provider TEXT, produced_at_utc REAL, payload_sha256 TEXT)
CREATE TABLE kanban_tasks_archive (
        task_id TEXT,
        status TEXT
    )
CREATE VIEW tasks AS SELECT task_id AS id, status, '2026-09-25T16:00:00Z' AS updated_at FROM kanban_tasks
CREATE TRIGGER tasks_update INSTEAD OF UPDATE ON tasks BEGIN UPDATE kanban_tasks SET status = new.status WHERE task_id = new.id; END
CREATE TRIGGER tasks_insert INSTEAD OF INSERT ON tasks BEGIN INSERT OR REPLACE INTO kanban_tasks (task_id, status) VALUES (new.id, new.status); END
CREATE INDEX "idx_kanban_tasks_payload_task_id" ON "kanban_tasks" (json_extract(payload, '$.task_id'))
