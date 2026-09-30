"""
Kanban v3 daemon
================
Runs task_work_loop.daemon_loop: dropzone poller plus a worker pool gated by one resizable
AdaptiveLimiter (hardware + memory_worker_cap() admission), with pytest_gate() capping
concurrent pytest subprocesses. The TDD phases stay in task_work_loop.py so there is exactly
one copy of the pipeline.

This file adds the PID file cochem_meta_auditor.py uses to find the daemon's processes.
PID file (COCHEM_KANBAN_PID_FILE, default .scripts/kanban_v3_pids.json), rewritten atomically
every COCHEM_PID_FILE_INTERVAL seconds (default 10):
    {"daemon": {"pid", "create_time", "cmdline"}, "updated_at", "limit", "in_flight",
     "workers": [{"pid", "ppid", "create_time", "name", "cmdline"}, ...]}
"workers" holds live descendants plus earlier-seen processes that outlived their parent
(orphans are no longer reachable through the process tree but are still ours).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import psutil

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import task_work_loop as twl  # noqa: E402  (also configures logging -> task_work_loop.log)

logger = logging.getLogger("kanban_v3_daemon")

PID_FILE = Path(os.environ.get("COCHEM_KANBAN_PID_FILE", str(_HERE / "kanban_v3_pids.json")))
PID_FILE_INTERVAL = max(1.0, float(os.environ.get("COCHEM_PID_FILE_INTERVAL", "10")))


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _describe(p: psutil.Process) -> dict:
    with p.oneshot():
        try:
            cmdline = " ".join(p.cmdline())[:300]
        except (psutil.AccessDenied, psutil.ZombieProcess):
            cmdline = ""
        return {"pid": p.pid, "ppid": p.ppid(), "create_time": round(p.create_time(), 3),
                "name": p.name(), "cmdline": cmdline}


class PidRegistry:
    """Processes spawned by this daemon, keyed by (pid, create_time) so a recycled PID
    never inherits an entry."""

    def __init__(self, me: psutil.Process):
        self._me = me
        self._seen: dict[tuple[int, float], dict] = {}

    def refresh(self) -> list[dict]:
        for child in self._me.children(recursive=True):
            try:
                info = _describe(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            self._seen[(info["pid"], info["create_time"])] = info
        for pid, ctime in list(self._seen):
            try:
                alive = abs(psutil.Process(pid).create_time() - ctime) < 0.01
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                alive = False
            if not alive:
                del self._seen[(pid, ctime)]
        return list(self._seen.values())


def _other_daemon_alive() -> Optional[dict]:
    """The daemon recorded in PID_FILE if it is still running and is not this process."""
    try:
        rec = json.loads(PID_FILE.read_text(encoding="utf-8"))["daemon"]
        p = psutil.Process(int(rec["pid"]))
        if p.pid != os.getpid() and abs(p.create_time() - float(rec["create_time"])) < 0.01:
            return rec
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    return None


async def _pid_file_writer(daemon: dict, registry: PidRegistry) -> None:
    while True:
        try:
            workers = await asyncio.to_thread(registry.refresh)
            limiter = twl._worker_semaphore
            _write_json_atomic(PID_FILE, {
                "daemon": daemon,
                "updated_at": time.time(),
                "limit": getattr(limiter, "limit", None),
                "in_flight": getattr(limiter, "in_flight", None),
                "workers": workers,
            })
        except asyncio.CancelledError:
            raise
        except Exception as e:  # e.g. the auditor holding the file open during os.replace
            logger.warning(f"[PIDS] could not update {PID_FILE}: {e}")
        await asyncio.sleep(PID_FILE_INTERVAL)


async def run(dropzone: Path, poll_interval: int, max_workers: Optional[int]) -> None:
    me = psutil.Process()
    daemon = {"pid": me.pid, "create_time": round(me.create_time(), 3),
              "cmdline": " ".join(me.cmdline())[:300]}
    writer = asyncio.create_task(_pid_file_writer(daemon, PidRegistry(me)))
    logger.info(f"[DAEMON] kanban v3 pid={me.pid}, PID file {PID_FILE}")
    try:
        await twl.daemon_loop(dropzone, poll_interval=poll_interval, max_workers=max_workers)
    finally:
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer
        with contextlib.suppress(OSError):
            PID_FILE.unlink()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="CoChem Kanban v3 daemon (TDD pipeline + PID file for the meta auditor)")
    parser.add_argument("--dropzone", default=twl.PROMPTS_DIR, help="Dropzone directory to watch")
    parser.add_argument("--poll", type=int, default=10, help="Seconds between dropzone polls")
    parser.add_argument("--max-workers", type=int, default=None,
                        help=f"Worker coroutines (default: ceiling, {twl._HW_CEIL}); the limiter still gates them")
    args = parser.parse_args(argv)

    other = _other_daemon_alive()
    if other is not None:
        logger.error(f"[DAEMON] another kanban daemon is running (pid {other['pid']}); not starting a second one")
        sys.exit(1)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(Path(args.dropzone), args.poll, args.max_workers))


if __name__ == "__main__":
    main()
