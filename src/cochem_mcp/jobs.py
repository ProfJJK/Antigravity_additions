"""Bounded CLI jobs with durable, process-derived receipts."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import re
import subprocess
import threading
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .providers import auth_probe, build_command, executable_prefix, parse_result, subscription_env

TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.state_dir
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._pending: set[str] = set()
        self._state_lock = (self.root / "server.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._state_lock.seek(0)
                if not self._state_lock.read(1):
                    self._state_lock.write(b"0")
                    self._state_lock.flush()
                self._state_lock.seek(0)
                msvcrt.locking(self._state_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._state_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._state_lock.close()
            raise RuntimeError(f"Another {settings.provider} MCP server owns {self.root}") from None
        try:
            for receipt in self.root.glob("*/receipt.json"):
                record = json.loads(receipt.read_text(encoding="utf-8"))
                job_id = record.get("job_id") if isinstance(record, dict) else None
                if (not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id)
                        or receipt.parent.name != job_id or "status" not in record):
                    raise ValueError(f"Invalid job receipt: {receipt}")
                self._records[job_id] = record
                if record["status"] not in TERMINAL:
                    record.update(status="interrupted", finished_at=now(),
                                  error="MCP server restarted; previous completion was not observed. The old CLI may still be running: inspect the recorded PID before retrying. No automatic retry.")
                    self._save(record)
        except (OSError, ValueError, TypeError):
            self._state_lock.close()
            raise
        self._pool = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix=settings.provider)

    def _save(self, record: dict) -> None:
        directory = self.root / record["job_id"]
        directory.mkdir(exist_ok=True, mode=0o700)
        target = directory / "receipt.json"
        temporary = directory / "receipt.tmp"
        temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def health(self) -> dict:
        try:
            prefix = executable_prefix(self.settings.provider, self.settings.executable)
            env = subscription_env(self.settings.provider)
            result = auth_probe(self.settings.provider, prefix, env)
            version = subprocess.run(prefix + ["--version"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", env=env, timeout=15,
                                     **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            result.update(executable=prefix, version=version.stdout.strip() if version.returncode == 0 else None)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {"ready": False, "provider": self.settings.provider, "reason": str(exc)}
        result.update(configured_models=self.settings.models, model_availability_verified=False,
                      workspace_roots=[str(p) for p in self.settings.workspace_roots])
        return result

    def submit(self, prompt: str, workspace: str = "", model: str = "") -> dict:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be nonempty")
        if len(prompt.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("Prompt exceeds 4 MiB")
        directory = self.settings.workspace(workspace)
        selected_model = self.settings.model(model)
        # Resolve before accepting the job. Authentication is checked in the worker,
        # so submission remains fast even when native auth status is slow.
        prefix = executable_prefix(self.settings.provider, self.settings.executable)
        argv = build_command(self.settings.provider, prefix, selected_model, str(directory))
        if self.settings.allowed_tools:
            argv += ["--allowedTools", *self.settings.allowed_tools]
        with self._lock:
            if self._closed:
                raise RuntimeError("MCP server is shutting down")
            if len(self._pending) >= self.settings.max_pending:
                raise RuntimeError("Job queue is full; wait for an existing job")
            job_id = uuid.uuid4().hex
            record = {"job_id": job_id, "provider": self.settings.provider, "status": "queued",
                      "requested_model": selected_model, "reported_model": None,
                      "workspace": str(directory), "command": argv,
                      "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                      "created_at": now(), "started_at": None, "finished_at": None,
                      "pid": None, "exit_code": None, "session_id": None,
                      "error": None, "cancel_requested": False}
            self._records[job_id] = record
            self._save(record)
            self._pending.add(job_id)
            self._pool.submit(self._run, job_id, prompt, prefix)
            return dict(record)

    def _run(self, job_id: str, prompt: str, prefix: list[str]) -> None:
        proc = None
        timed_out = False
        record = self._records[job_id]
        directory = self.root / job_id
        try:
            env = subscription_env(self.settings.provider)
            with self._lock:
                if record["cancel_requested"]:
                    return
                record["status"] = "authenticating"
                self._save(record)
            auth = auth_probe(self.settings.provider, prefix, env)
            if not auth["ready"]:
                raise RuntimeError(auth.get("reason", "Native subscription login is required"))
            with tempfile.TemporaryFile(mode="w+b") as input_file, (directory / "stdout.jsonl").open("w", encoding="utf-8") as output, (directory / "stderr.log").open("w", encoding="utf-8") as errors:
                # A regular stdin file avoids Windows PIPE writes blocking before
                # communicate() starts enforcing its timeout on a large prompt.
                input_file.write(prompt.encode("utf-8"))
                input_file.seek(0)
                with self._lock:
                    if record["cancel_requested"]:
                        return
                    proc = subprocess.Popen(record["command"], cwd=record["workspace"], env=env,
                                            stdin=input_file, stdout=output, stderr=errors,
                                            text=True, encoding="utf-8", errors="replace", shell=False,
                                            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}))
                    self._processes[job_id] = proc
                    record.update(status="running", pid=proc.pid, started_at=now())
                    self._save(record)
                try:
                    proc.wait(timeout=self.settings.timeout_seconds)
                except subprocess.TimeoutExpired:
                    terminate(proc)
                    timed_out = True
            with self._lock:
                record["exit_code"] = proc.returncode
                if record["cancel_requested"]:
                    record.update(status="cancelled", error="Cancelled by MCP caller")
                elif timed_out:
                    record.update(status="timed_out", error="CLI exceeded configured timeout; process tree terminated")
                else:
                    if proc.returncode != 0:
                        raise RuntimeError(f"{self.settings.provider} CLI exited with code {proc.returncode}; inspect stderr.log")
                    raw = (directory / "stdout.jsonl").read_text(encoding="utf-8")
                    parsed = parse_result(self.settings.provider, raw)
                    if not parsed.get("terminal_success"):
                        raise RuntimeError("CLI did not report terminal success")
                    (directory / "result.txt").write_text(parsed["content"], encoding="utf-8")
                    record.update(status="completed", session_id=parsed["session_id"],
                                  reported_model=parsed.get("reported_model"), usage=parsed.get("usage"),
                                  result_sha256=hashlib.sha256(parsed["content"].encode("utf-8")).hexdigest())
                record["finished_at"] = now()
                self._save(record)
        except Exception as exc:
            with self._lock:
                record.update(status="cancelled" if record["cancel_requested"] else "failed", error=str(exc), finished_at=now())
                self._save(record)
        finally:
            if proc is not None and proc.poll() is None:
                terminate(proc)
            with self._lock:
                self._processes.pop(job_id, None)
                self._pending.discard(job_id)
                if record["cancel_requested"]:
                    record["status"] = "cancelled"
                record["finished_at"] = now()
                if proc is not None:
                    record["exit_code"] = proc.returncode
                self._save(record)

    def status(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._records:
                raise ValueError("Unknown job_id")
            return dict(self._records[job_id])

    def result(self, job_id: str, offset: int = 0, limit: int = 16000) -> dict:
        if offset < 0 or not 1 <= limit <= 64000:
            raise ValueError("offset must be nonnegative; limit must be 1..64000")
        record = self.status(job_id)
        directory = self.root / job_id
        record["receipt_path"] = str(directory / "receipt.json")
        if record["status"] == "completed":
            text = (directory / "result.txt").read_text(encoding="utf-8")
            record.update(content=text[offset:offset + limit], total_characters=len(text),
                          next_offset=offset + limit if offset + limit < len(text) else None)
        else:
            record["content"] = None
        return record

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise ValueError("Unknown job_id")
            if record["status"] in TERMINAL:
                return dict(record)
            record["cancel_requested"] = True
            proc = self._processes.get(job_id)
            if proc is None:
                record.update(status="cancelled", finished_at=now())
            self._save(record)
        if proc is not None:
            terminate(proc)
        return self.status(job_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = [key for key, value in self._records.items() if value["status"] not in TERMINAL]
        for job_id in active:
            self.cancel(job_id)
        self._pool.shutdown(wait=True)
        self._state_lock.close()
