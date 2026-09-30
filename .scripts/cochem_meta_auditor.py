"""
CoChem meta auditor: kills hung processes spawned by the Kanban v3 daemon
==========================================================================
Targets ONLY processes spawned by kanban_v3_daemon.py:
  - live descendants of the daemon PID in the PID file (checked against create_time, so a
    recycled PID is never mistaken for the daemon), plus
  - PID-file "workers" entries whose (pid, create_time) still match a live process
    (orphans whose parent already exited).
Interactive claude.exe sessions and every other process on the machine are never touched.

A process is hung when its whole subtree has used no CPU and done no I/O for
COCHEM_AUDIT_IDLE_MIN minutes (default 20). Wall-clock age does not matter: a coder call that
streams for 50 minutes is fine; one blocked on a dead socket for 20 is not. The topmost idle
process is killed with its children; the daemon sees an ordinary subprocess failure and the
task takes its normal failure path.

Busy loops (pinned CPU) are not idle and are not killed here; pytest runs are bounded by
V2_PYTEST_TIMEOUT_S in task_work_loop.

Usage:  python cochem_meta_auditor.py            # sweep every COCHEM_AUDIT_INTERVAL_S (60)
        python cochem_meta_auditor.py --once     # one sweep, e.g. from Task Scheduler
        python cochem_meta_auditor.py --dry-run  # log what would be killed, kill nothing
Env: COCHEM_KANBAN_PID_FILE, COCHEM_AUDIT_IDLE_MIN (20), COCHEM_AUDIT_INTERVAL_S (60),
     COCHEM_AUDIT_CPU_EPS_S (0.05 CPU-seconds per sweep), COCHEM_AUDIT_IO_EPS_BYTES (4096 per sweep)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Optional

import psutil

_HERE = Path(__file__).resolve().parent


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


PID_FILE = Path(os.environ.get("COCHEM_KANBAN_PID_FILE", str(_HERE / "kanban_v3_pids.json")))
STATE_FILE = _HERE / "cochem_meta_auditor_state.json"
LOG_FILE = _HERE / "cochem_meta_auditor.log"
IDLE_S = _env_float("COCHEM_AUDIT_IDLE_MIN", 20) * 60
INTERVAL_S = max(5.0, _env_float("COCHEM_AUDIT_INTERVAL_S", 60))
CPU_EPS_S = _env_float("COCHEM_AUDIT_CPU_EPS_S", 0.05)
IO_EPS_BYTES = _env_float("COCHEM_AUDIT_IO_EPS_BYTES", 4096)
NEVER_KILL = {"conhost.exe"}  # console hosts are always idle; they exit with their client

logger = logging.getLogger("cochem_meta_auditor")


def _key(p: psutil.Process) -> str:
    return f"{p.pid}:{p.create_time():.3f}"


def _same_process(p: psutil.Process, create_time) -> bool:
    return abs(p.create_time() - float(create_time)) < 0.01


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def find_targets() -> dict[str, psutil.Process]:
    """Processes spawned by the kanban daemon, keyed by pid:create_time. Empty without a PID file."""
    rec = _read_json(PID_FILE)
    if not rec:
        return {}
    targets: dict[str, psutil.Process] = {}
    daemon = rec.get("daemon") or {}
    try:
        d = psutil.Process(int(daemon["pid"]))
        if _same_process(d, daemon["create_time"]):
            for c in d.children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    targets[_key(c)] = c
    except (KeyError, TypeError, ValueError, psutil.Error):
        pass  # daemon gone; the orphans it recorded are still ours
    for w in rec.get("workers") or []:
        try:
            p = psutil.Process(int(w["pid"]))
            if _same_process(p, w["create_time"]):
                targets.setdefault(_key(p), p)
        except (KeyError, TypeError, ValueError, psutil.Error):
            continue
    return {k: p for k, p in targets.items() if p.pid != os.getpid()}


def _activity(p: psutil.Process) -> tuple[float, float]:
    """Cumulative CPU seconds and I/O bytes (read + write + other, which includes device I/O)."""
    with p.oneshot():
        t = p.cpu_times()
        try:
            io = p.io_counters()
            io_bytes = io.read_bytes + io.write_bytes + getattr(io, "other_bytes", 0)
        except (psutil.AccessDenied, AttributeError):
            io_bytes = 0
    return t.user + t.system, float(io_bytes)


def _kill_tree(root: psutil.Process) -> int:
    try:
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        procs = [root]
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=5)
    return len(procs)


def sweep(state: dict, dry_run: bool = False, now: Optional[float] = None) -> dict:
    """One audit pass. state maps pid:create_time -> {pid, ppid, name, cpu, io, last_active};
    returns the new state (live targets only)."""
    now = time.time() if now is None else now
    procs: dict[str, dict] = {}
    handles: dict[str, psutil.Process] = {}
    for key, p in find_targets().items():
        try:
            cpu, io = _activity(p)
            ppid, name = p.ppid(), p.name()
        except psutil.Error:
            continue
        prev = state.get(key)
        try:
            active = cpu - prev["cpu"] > CPU_EPS_S or io - prev["io"] > IO_EPS_BYTES
            last_active = now if active else float(prev["last_active"])
        except (TypeError, KeyError, ValueError):
            last_active = now  # first sighting (or unreadable state): start the idle clock now
        procs[key] = {"pid": p.pid, "ppid": ppid, "name": name, "cpu": cpu, "io": io,
                      "last_active": last_active}
        handles[key] = p

    by_pid = {v["pid"]: k for k, v in procs.items()}
    children: dict[str, list[str]] = {}
    for k, v in procs.items():
        parent = by_pid.get(v["ppid"])
        if parent is not None and parent != k:
            children.setdefault(parent, []).append(k)

    memo: dict[str, float] = {}

    def subtree_last_active(k: str, path: frozenset = frozenset()) -> float:
        if k not in memo:
            memo[k] = max([procs[k]["last_active"]] +
                          [subtree_last_active(c, path | {k}) for c in children.get(k, []) if c not in path])
        return memo[k]

    idle = {k for k in procs
            if now - subtree_last_active(k) >= IDLE_S and procs[k]["name"].lower() not in NEVER_KILL}
    for k in idle:
        if by_pid.get(procs[k]["ppid"]) in idle:
            continue  # killed together with its idle ancestor
        info = procs[k]
        idle_min = (now - subtree_last_active(k)) / 60
        if dry_run:
            logger.warning(f"[AUDIT] DRY RUN: would kill {info['name']} pid={info['pid']} "
                           f"(no CPU/IO in its subtree for {idle_min:.0f} min)")
            continue
        n = _kill_tree(handles[k])
        logger.warning(f"[AUDIT] killed {info['name']} pid={info['pid']} and {n - 1} descendant(s): "
                       f"no CPU/IO for {idle_min:.0f} min")
    return procs


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")]
    if sys.stdout is not None:  # None under pythonw.exe
        handlers.append(logging.StreamHandler(sys.stdout))
    for h in handlers:
        h.setFormatter(fmt)
        logger.addHandler(h)
    logger.setLevel(logging.INFO)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Kill hung processes spawned by the Kanban v3 daemon")
    parser.add_argument("--once", action="store_true", help="Run one sweep and exit (Task Scheduler)")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be killed without killing")
    args = parser.parse_args(argv)
    _setup_logging()
    logger.info(f"[AUDIT] watching {PID_FILE}: kill after {IDLE_S / 60:.0f} min without CPU/IO"
                f"{' (dry run)' if args.dry_run else ''}")

    state = (_read_json(STATE_FILE) or {}).get("procs") or {}
    while True:
        try:
            state = sweep(state, dry_run=args.dry_run)
            _write_json_atomic(STATE_FILE, {"updated_at": time.time(), "procs": state})
        except Exception:
            logger.exception("[AUDIT] sweep failed")
        if args.once:
            return
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
