"""Real child-process tests using a labelled CLI protocol emulator, never an LLM.

Executable discovery and native authentication are replaced at the test boundary.
All job processes, stdin, files, exit codes, cancellations and timeouts are real.
These tests do not establish live subscription access or model availability.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import pytest

from cochem_mcp.config import Settings
from cochem_mcp import jobs


EMULATOR = r'''
import json
from pathlib import Path
import sys
import time

if "--version" in sys.argv:
    print("explicit-test-protocol-emulator 1.0")
    raise SystemExit(0)

request = json.loads(sys.stdin.read())
Path(request["started_file"]).write_text("test emulator executed", encoding="utf-8")
time.sleep(request.get("delay", 0))
if request.get("mode") == "malformed":
    print("EMULATOR: this is not the CLI JSON protocol")
    raise SystemExit(0)
if "--print" in sys.argv:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "session_id": "emulator-claude-session", "result": "EMULATED Claude output"}))
    raise SystemExit(0)
print(json.dumps({"type": "thread.started", "thread_id": "emulator-session"}))
if request.get("mode") == "terminal_failure":
    print(json.dumps({"type": "turn.failed", "error": {"message": "emulated terminal failure"}}))
    raise SystemExit(0)
print(json.dumps({"type": "item.completed", "item": {
    "id": "emulator-message", "type": "agent_message", "text": request.get("content", "EMULATED output")
}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}}))
if request.get("mode") == "nonzero":
    print("EMULATED process failure after success-shaped output", file=sys.stderr)
    raise SystemExit(7)
'''


@pytest.fixture
def job_environment(tmp_path, monkeypatch):
    emulator = tmp_path / "explicit_cli_protocol_emulator.py"
    emulator.write_text(EMULATOR, encoding="utf-8")
    workspace = tmp_path / "workspace with spaces ü"
    workspace.mkdir()
    monkeypatch.setattr(jobs, "executable_prefix", lambda *_: [sys.executable, str(emulator)])
    monkeypatch.setattr(jobs, "auth_probe", lambda *_: {
        "ready": True, "provider": "codex", "auth_method": "test-emulator-only",
        "reason": "Authentication bypassed exclusively for protocol-emulator tests",
    })
    managers = []

    def create(**overrides):
        values = {
            "provider": "codex", "executable": str(emulator),
            "workspace_roots": (workspace,),
            "state_dir": tmp_path / f"state-{len(managers)}",
            "models": {"test-model": "emulator-model"}, "default_model": "test-model",
            "timeout_seconds": 5, "max_workers": 1, "max_pending": 2,
        }
        values.update(overrides)
        manager = jobs.JobManager(Settings(**values))
        managers.append(manager)
        return manager

    yield create, workspace
    for manager in managers:
        manager.close()


def request(workspace: Path, **values) -> tuple[str, Path]:
    marker = workspace / f"started-{uuid.uuid4().hex}"
    return json.dumps({"started_file": str(marker), **values}), marker


def wait_status(manager, job_id, accepted=jobs.TERMINAL):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = manager.status(job_id)
        if status["status"] in accepted:
            return status
        time.sleep(0.01)
    pytest.fail(f"Emulator job did not reach {accepted}: {manager.status(job_id)}")


def test_accepted_job_observes_real_pid_and_persists_protocol_success(job_environment):
    create, workspace = job_environment
    manager = create()
    content = "EMULATED output: π\nsecond line"
    prompt, marker = request(workspace, delay=0.25, content=content, extra="x" * 70000)
    accepted = manager.submit(prompt)
    assert accepted["status"] == "queued"
    assert accepted["pid"] is None
    running = wait_status(manager, accepted["job_id"], {"running"})
    assert running["pid"] > 0
    assert running["started_at"]
    if os.name != "nt":
        os.kill(running["pid"], 0)  # Operating system confirms this is an actual child.
    result = wait_status(manager, accepted["job_id"])
    assert marker.read_text() == "test emulator executed"
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["session_id"] == "emulator-session"
    assert result["requested_model"] == "emulator-model"
    assert result["reported_model"] is None
    assert result["result_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert result["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert prompt not in result["command"]  # The long prompt travelled through stdin.
    assert manager.result(accepted["job_id"])["content"] == content
    page = manager.result(accepted["job_id"], offset=4, limit=7)
    assert page["content"] == content[4:11]
    assert page["next_offset"] == 11
    receipt = manager.root / accepted["job_id"] / "receipt.json"
    assert json.loads(receipt.read_text())["status"] == "completed"


@pytest.mark.parametrize("mode", ["nonzero", "terminal_failure", "malformed"])
def test_process_or_protocol_failure_cannot_be_reported_as_completed(job_environment, mode):
    create, workspace = job_environment
    manager = create()
    prompt, marker = request(workspace, mode=mode)
    job = manager.submit(prompt)
    result = wait_status(manager, job["job_id"])
    assert marker.exists()
    assert result["status"] == "failed"
    assert result["error"]
    assert result["exit_code"] == (7 if mode == "nonzero" else 0)
    assert manager.result(job["job_id"])["content"] is None
    assert not (manager.root / job["job_id"] / "result.txt").exists()


def test_auth_failure_never_starts_a_job_process(job_environment, monkeypatch):
    create, workspace = job_environment
    manager = create()
    monkeypatch.setattr(jobs, "auth_probe", lambda *_: {
        "ready": False, "reason": "EMULATED missing subscription login",
    })
    prompt, marker = request(workspace)
    job = manager.submit(prompt)
    status = wait_status(manager, job["job_id"])
    assert status["status"] == "failed"
    assert "missing subscription login" in status["error"]
    assert status["pid"] is None
    assert not marker.exists()


def test_timeout_terminates_real_child_and_does_not_publish_result(job_environment):
    create, workspace = job_environment
    manager = create(timeout_seconds=1)
    prompt, marker = request(workspace, delay=30)
    job = manager.submit(prompt)
    running = wait_status(manager, job["job_id"], {"running"})
    status = wait_status(manager, job["job_id"])
    assert marker.exists()
    assert status["status"] == "timed_out"
    assert status["exit_code"] != 0
    assert manager.result(job["job_id"])["content"] is None
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(running["pid"], 0)


def test_queued_cancellation_and_queue_cap_do_not_launch_cancelled_work(job_environment):
    create, workspace = job_environment
    manager = create(max_pending=2)
    first_prompt, _ = request(workspace, delay=0.4)
    first = manager.submit(first_prompt)
    wait_status(manager, first["job_id"], {"running"})
    second_prompt, second_marker = request(workspace)
    second = manager.submit(second_prompt)
    with pytest.raises(RuntimeError, match="queue is full"):
        manager.submit(second_prompt)
    cancelled = manager.cancel(second["job_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["pid"] is None
    # A cancelled callback still holds its prompt until the executor consumes it.
    # Repeated submit/cancel must not evade the bounded pending-work limit.
    with pytest.raises(RuntimeError, match="queue is full"):
        manager.submit(second_prompt)
    wait_status(manager, first["job_id"])
    manager.close()  # Waits for all queued callbacks, including cancellation cleanup.
    assert not second_marker.exists()
    assert manager.status(second["job_id"])["status"] == "cancelled"


def test_running_cancellation_terminates_child_and_is_idempotent(job_environment):
    create, workspace = job_environment
    manager = create()
    prompt, _ = request(workspace, delay=30)
    job = manager.submit(prompt)
    running = wait_status(manager, job["job_id"], {"running"})
    manager.cancel(job["job_id"])
    status = wait_status(manager, job["job_id"])
    manager.close()
    assert status["status"] == "cancelled"
    assert status["exit_code"] != 0
    assert manager.result(job["job_id"])["content"] is None
    before = manager.status(job["job_id"])
    assert manager.cancel(job["job_id"]) == before
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(running["pid"], 0)


def test_status_and_result_reads_do_not_change_receipts_or_job_state(job_environment):
    create, workspace = job_environment
    manager = create()
    prompt, _ = request(workspace)
    job = manager.submit(prompt)
    wait_status(manager, job["job_id"])
    manager.close()  # Avoid races with final receipt persistence in the worker.
    before = manager.status(job["job_id"])
    receipt = manager.root / job["job_id"] / "receipt.json"
    payload, stamp = receipt.read_bytes(), receipt.stat().st_mtime_ns
    for _ in range(5):
        assert manager.status(job["job_id"]) == before
        assert manager.result(job["job_id"])["status"] == before["status"]
    assert receipt.read_bytes() == payload
    assert receipt.stat().st_mtime_ns == stamp


def test_restart_preserves_completed_result_and_marks_unfinished_job_interrupted(job_environment):
    create, workspace = job_environment
    manager = create()
    prompt, _ = request(workspace, content="EMULATED durable result")
    completed = manager.submit(prompt)
    wait_status(manager, completed["job_id"])
    manager.close()
    orphan_id = uuid.uuid4().hex
    orphan_dir = manager.root / orphan_id
    orphan_dir.mkdir()
    orphan = dict(manager.status(completed["job_id"]))
    orphan.update(job_id=orphan_id, status="authenticating", pid=None, exit_code=None,
                  started_at=None, finished_at=None, session_id=None)
    (orphan_dir / "receipt.json").write_text(json.dumps(orphan))
    restarted = create(state_dir=manager.root)
    interrupted = restarted.status(orphan_id)
    assert interrupted["status"] == "interrupted"
    assert interrupted["finished_at"]
    assert "No automatic retry" in interrupted["error"]
    assert restarted.result(orphan_id)["content"] is None
    assert restarted.result(completed["job_id"])["content"] == "EMULATED durable result"


def test_state_directory_rejects_concurrent_server_owner(job_environment):
    create, _ = job_environment
    manager = create()
    with pytest.raises(RuntimeError, match="Another codex MCP server owns"):
        create(state_dir=manager.root)


def test_explicit_claude_tool_permissions_reach_real_child_argv(job_environment):
    create, workspace = job_environment
    manager = create(provider='claude', allowed_tools=('Bash(python -m pytest:*)',))
    prompt, marker = request(workspace)
    accepted = manager.submit(prompt)
    result = wait_status(manager, accepted['job_id'])
    assert result['status'] == 'completed'
    assert marker.is_file()
    assert result['command'][-2:] == ['--allowedTools', 'Bash(python -m pytest:*)']
