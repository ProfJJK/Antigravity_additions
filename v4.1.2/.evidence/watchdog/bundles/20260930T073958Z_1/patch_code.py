"""Code fixes applied by resuscitation session v4-1-2-e6 (bundle 20260930T073958Z_1)."""
from pathlib import Path
import os
os.chdir(r"D:\__CoChem\__agentic\v4.1.2")

def patch(path, pairs):
    p = Path(path); s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:60])
        s = s.replace(old, new)
    p.write_text(s, encoding="utf-8"); print("patched", path)

# 1. worker_daemon.fail_job honours max_attempts (SRS-412-04-FR-005) and clears a stale not_before
patch("src/cochem/dsp/worker_daemon.py", [
('''def fail_job(db_path: Path, job_id: int, daemon_id: str, error_log: str) -> bool:
    """Non-retryable pipeline verdict -> FAILED."""
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = 'FAILED', lease_owner = NULL, lease_expires_at = NULL, error_log = ?, updated_at = ?",
        (error_log, int(time.time())),
    )''',
'''def fail_job(db_path: Path, job_id: int, daemon_id: str, error_log: str) -> bool:
    """Non-retryable pipeline verdict -> FAILED, or BLOCKED once attempts reach max_attempts.

    SRS-412-04-FR-005 quarantines a task whose attempts are exhausted regardless of why the last
    attempt failed; a FAILED row at attempts >= max_attempts can never be claimed again and would
    only page the SRE Watchdog (Signal ZD-8) forever. not_before is cleared so no retry backoff
    from an earlier transient fault lingers on a terminal row. (Resuscitation 2026-09-30.)
    """
    return _fenced_update(
        db_path, job_id, daemon_id,
        "status = CASE WHEN attempts >= max_attempts THEN 'BLOCKED' ELSE 'FAILED' END, "
        "lease_owner = NULL, lease_expires_at = NULL, not_before = NULL, error_log = ?, updated_at = ?",
        (error_log, int(time.time())),
    )'''),
('''                return self._finish(fail_job(self.db_path, job_id, self.daemon_id, f"dispatch rejected: {exc}"),
                                    task_id, "FAILED")''',
'''                return self._finish(fail_job(self.db_path, job_id, self.daemon_id, f"dispatch rejected: {exc}"),
                                    task_id, "BLOCKED" if attempts >= job["max_attempts"] else "FAILED")'''),
('''                return self._finish(fail_job(self.db_path, job_id, self.daemon_id,
                                             f"{domain}.validate() rejected payload"), task_id, "FAILED")''',
'''                return self._finish(fail_job(self.db_path, job_id, self.daemon_id,
                                             f"{domain}.validate() rejected payload"),
                                    task_id, "BLOCKED" if attempts >= job["max_attempts"] else "FAILED")'''),
('''                                             f"{domain}.audit() rejected result: {json.dumps(result, default=str)[:2000]}"),
                                    task_id, "FAILED")''',
'''                                             f"{domain}.audit() rejected result: {json.dumps(result, default=str)[:2000]}"),
                                    task_id, "BLOCKED" if attempts >= job["max_attempts"] else "FAILED")'''),
])

# 2. forge orchestrator: a crashed bridge subprocess is a pipeline fault, not a TDD verdict
patch("src/cochem/dsp/forge/orchestrator.py", [
('''class ForgeFSMState(str, Enum):''',
'''class CodeForgeBridgeError(RuntimeError):
    """The task_work_loop.py bridge subprocess exited non-zero (crash/traceback), not a TDD verdict.

    Raised from execute() so the DSP worker treats it as a pipeline fault (retry with backoff,
    BLOCKED at max_attempts) instead of an audit rejection that lands the row in FAILED and
    trips Signal ZD-8 for an infrastructure failure. (Resuscitation 2026-09-30.)
    """

    def __init__(self, task_id: str, returncode: int, stderr: str) -> None:
        self.task_id, self.returncode, self.stderr = task_id, returncode, stderr
        tail = (stderr or "").strip().splitlines()
        last = tail[-1] if tail else "(no stderr)"
        super().__init__(f"task_work_loop.py exited {returncode} for {task_id}: {last}")


class ForgeFSMState(str, Enum):'''),
('''            if res.returncode != 0:
                self.transition(ForgeFSMState.FAILED, {"error": res.stderr})
                tel = telemetry.finish(status="FAILED")
                return {"status": "FAILED", "task_id": task_id, "target_file": target_file, "state": self.state.value, "error": res.stderr, "telemetry": tel}
''',
'''            if res.returncode != 0:
                # A non-zero exit means the bridge process itself died (uncaught traceback); a TDD
                # verdict (PIVOT/QUARANTINE) still exits 0. Surface it as a fault, not a verdict.
                self.transition(ForgeFSMState.FAILED, {"error": res.stderr, "returncode": res.returncode})
                telemetry.finish(status="FAILED")
                raise CodeForgeBridgeError(task_id, res.returncode, res.stderr)
'''),
])

# 3. migrate_v3_to_v412.py: never store non-JSON payloads; never delete existing rows
patch("migrate_v3_to_v412.py", [
('''conn_v4.execute("DELETE FROM jobs WHERE id > 291")
''',
'''# Resuscitation 2026-09-30: the former `DELETE FROM jobs WHERE id > 291` wiped ids 292-365
# (batch 07-10 rows). Migration must be additive (SRS-412-04-FR-008: INSERT OR IGNORE).
'''),
('''    try:
        p_data = json.loads(payload_str)
        p_data["task_id"] = task_id
        if "target_file" not in p_data:
            p_data["target_file"] = p_data.get("target", "D:/__CoChem/__agentic/v3")
        payload_str = json.dumps(p_data)
    except Exception as e:
        pass
''',
'''    try:
        p_data = json.loads(payload_str)
        if not isinstance(p_data, dict):
            raise ValueError("payload is not a JSON object")
    except ValueError:
        # v3 payload_uri files are often raw Markdown prompts: wrap them instead of storing
        # non-JSON text that the DSP worker can only reject ("dispatch rejected: Expecting value").
        p_data = {"prompt": payload_str, "target": "D:/__CoChem/__agentic/v3",
                  "payload_provenance": f"v3 kanban payload_uri {payload_uri}"}
    p_data["task_id"] = task_id
    if "target_file" not in p_data:
        p_data["target_file"] = p_data.get("target", "D:/__CoChem/__agentic/v3")
    payload_str = json.dumps(p_data)
'''),
('''            INSERT INTO jobs (task_id, job_type, status, payload_json, priority)''',
'''            INSERT OR IGNORE INTO jobs (task_id, job_type, status, payload_json, priority)'''),
])
print("ALL PATCHED")
