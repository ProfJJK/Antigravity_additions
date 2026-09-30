"""Resuscitation 2026-09-30 bundle 20260930T085228Z (session v4-1-2-d3): watchdog_sre.py defects.

1. No-consumer starvation (ready PENDING rows, zero live DSP workers) paged Fable as COLLAPSED although the
   board holds no illegal state and a resuscitation session cannot register worker tasks; it re-pages every
   cooldown. It is now a DEGRADED verdict (heartbeat + error log), never a freeze/bundle/claude launch.
2. No single-instance guard: two watchdogs (PIDs 31124/37536) each paged Fable for the same event, twice today.
3. assemble_evidence_bundle raced exists()->mkdir between the two instances (FileExistsError in PID 37536).
Every replacement must match exactly once.
"""
from pathlib import Path

P = Path(r"D:\__CoChem\__agentic\v4.1.2\src\cochem\watchdog\watchdog_sre.py")
src = P.read_text(encoding="utf-8")
edits = [
    # --- 3. bundle dir race ---------------------------------------------------------------------------
    ('''    bundle_dir = next(p for n in range(10**6) if not (p := Path(f"{stamp}_{n}") if n else stamp).exists())
    bundle_dir.mkdir(parents=True)
''',
     '''    for n in range(10**6):  # mkdir is the existence check: two processes racing on one stamp cannot collide
        bundle_dir = Path(f"{stamp}_{n}") if n else stamp
        try:
            bundle_dir.mkdir(parents=True)
            break
        except FileExistsError:
            if bundle_dir.is_dir():  # a real collision on this stamp: try the next suffix
                continue
            raise  # e.g. bundles_dir is a regular file: fail at once, never spin through the suffixes
    else:
        raise FileExistsError(f"no free bundle directory name under {bundles_dir} for {stamp.name}")
'''),
    # --- 1. no-consumer -> DEGRADED ------------------------------------------------------------------
    ('''        if ready_pending and live == 0:
            self.no_consumer_since = self.no_consumer_since or now
            if now - self.no_consumer_since >= NO_CONSUMER_GRACE_SEC:
                failures.append(f"Matrix 1: {ready_pending} ready PENDING job(s) and no live DSP worker for "
                                f"{now - self.no_consumer_since:.0f}s")
        else:
            self.no_consumer_since = None
        return failures
''',
     '''        if ready_pending and live == 0:
            self.no_consumer_since = self.no_consumer_since or now
            if now - self.no_consumer_since >= NO_CONSUMER_GRACE_SEC:
                failures.append(f"Matrix 1: {ready_pending} ready PENDING job(s) and {NO_CONSUMER_MARKER} for "
                                f"{now - self.no_consumer_since:.0f}s")
        else:
            self.no_consumer_since = None
        return failures

    @staticmethod
    def is_no_consumer_failure(failure: str) -> bool:
        """The starvation finding (ready work, zero live workers): DEGRADED, not COLLAPSED (see diagnose)."""
        return NO_CONSUMER_MARKER in failure
'''),
    ('''        extra = self.worker_record_failures(workers, diagnosis["ready_pending"], now)
        if extra:
            diagnosis["matrix_failures"] = diagnosis["matrix_failures"] + extra
            diagnosis["verdict"] = "COLLAPSED"
        diagnosis["workers"] = [rec for _, rec in workers]
        return diagnosis
''',
     '''        extra = self.worker_record_failures(workers, diagnosis["ready_pending"], now)
        collapse = [f for f in extra if not self.is_no_consumer_failure(f)]
        starved = [f for f in extra if self.is_no_consumer_failure(f)]
        if collapse:
            diagnosis["matrix_failures"] = diagnosis["matrix_failures"] + collapse
            diagnosis["verdict"] = "COLLAPSED"
        # Starvation with a legal board is an operational gap (no worker deployed / all workers exited), not a
        # queue collapse: nothing on the board can be repaired and a resuscitation session cannot register the
        # worker tasks (Install-PipelineDaemons.ps1 needs elevation). Paging Fable for it re-pages every cooldown
        # (2026-09-30 bundles 20260930T085228Z / 20260930T085258Z). It is reported as DEGRADED in the heartbeat
        # and log, and listed in the bundle when another matrix collapses at the same time.
        diagnosis["degraded_failures"] = starved
        if starved:
            if diagnosis["verdict"] == "COLLAPSED":
                diagnosis["matrix_failures"] = diagnosis["matrix_failures"] + starved
            else:
                diagnosis["verdict"] = "DEGRADED"
                diagnosis["matrix_failures"] = list(starved)
        diagnosis["workers"] = [rec for _, rec in workers]
        return diagnosis
'''),
    ('''            else:
                diagnosis["bundle"] = self.handle_collapse(diagnosis, confirmed_at)
        elif self.cycles % 20 == 0:
''',
     '''            else:
                diagnosis["bundle"] = self.handle_collapse(diagnosis, confirmed_at)
        elif diagnosis["verdict"] == "DEGRADED":
            logger.error("DEGRADED (no resuscitation: board legal, no consumer to restart from here): %s",
                         "; ".join(diagnosis["matrix_failures"]))
        elif self.cycles % 20 == 0:
'''),
    ('''        self.write_heartbeat(diagnosis["verdict"], diagnosis["cycle_duration_sec"])
        self.save_state()
        return diagnosis
''',
     '''        self.write_heartbeat(diagnosis["verdict"], diagnosis["cycle_duration_sec"], diagnosis.get("matrix_failures"))
        self.save_state()
        return diagnosis
'''),
    ('''    def write_heartbeat(self, verdict: str, duration: float) -> None:
        """Heartbeat read by the Host Warden, which restarts the watchdog when it ceases."""
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.heartbeat_file, {"pid": os.getpid(), "ts": time.time(), "cycle": self.cycles,
                                                 "verdict": verdict, "cycle_duration_sec": round(duration, 4),
''',
     '''    def write_heartbeat(self, verdict: str, duration: float, failures: list[str] | None = None) -> None:
        """Heartbeat read by the Host Warden, which restarts the watchdog when it ceases."""
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.heartbeat_file, {"pid": os.getpid(), "ts": time.time(), "cycle": self.cycles,
                                                 "verdict": verdict, "cycle_duration_sec": round(duration, 4),
                                                 "matrix_failures": list(failures or []),
'''),
    ('''NO_CONSUMER_GRACE_SEC: float = 300.0; WORKER_RSS_CAP_MB: float = 512.0
''',
     '''NO_CONSUMER_GRACE_SEC: float = 300.0; WORKER_RSS_CAP_MB: float = 512.0
NO_CONSUMER_MARKER: str = "no live DSP worker"; INSTANCE_LOCK_NAME: str = "watchdog.pid"; EXIT_ALREADY_RUNNING: int = 3
'''),
    # --- 2. single-instance guard -------------------------------------------------------------------
    ('''        self.state_file = self.evidence_dir / "watchdog_state.json"
        self.heartbeat_file = self.evidence_dir / "watchdog_heartbeat.json"
''',
     '''        self.state_file = self.evidence_dir / "watchdog_state.json"
        self.heartbeat_file = self.evidence_dir / "watchdog_heartbeat.json"
        self.lock_file = self.evidence_dir / INSTANCE_LOCK_NAME
        self._lock_held = False
'''),
    ('''    def run(self, once: bool = False, interval: float = CYCLE_INTERVAL_SEC) -> int:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.load_state()
        if self.state.get("frozen_pids") and self._recovery_process() is None:
            self.thaw()  # a previous watchdog died between freeze and thaw
        logger.info("SRE Watchdog pid %s monitoring %s (read-only) every %.0fs", os.getpid(), self.db_path, interval)
        run_watchdog_loop(self.run_cycle, self.stop_event, interval, max_cycles=1 if once else None)
        self.save_state()
        return 0
''',
     '''    # -- single-instance guard ----------------------------------------------------------
    # Two watchdogs on one evidence dir share the state/heartbeat files and each pages its own Fable session
    # for the same event (2026-09-30: PIDs 31124 and 37536, four recovery sessions). One pid file, O_EXCL.
    def acquire_instance_lock(self) -> int | None:
        """Take the evidence-dir pid lock; returns the pid of a live holder when another watchdog owns it."""
        for _ in range(3):
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_lock_pid(self.lock_file)
                if holder is not None and holder != os.getpid() and _is_live_watchdog(holder):
                    return holder
                try:  # stale lock: holder dead, pid reused by something else, or unreadable content
                    self.lock_file.unlink()
                except FileNotFoundError:
                    logger.debug("stale lock %s vanished before removal", self.lock_file)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\\n")
            self._lock_held = True
            return None
        return _read_lock_pid(self.lock_file) or -1  # lost the race repeatedly: report whoever holds it

    def release_instance_lock(self) -> None:
        if self._lock_held and _read_lock_pid(self.lock_file) == os.getpid():
            self.lock_file.unlink(missing_ok=True)
        self._lock_held = False

    def run(self, once: bool = False, interval: float = CYCLE_INTERVAL_SEC) -> int:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        holder = self.acquire_instance_lock()
        if holder is not None:
            logger.error("SRE Watchdog pid %s already monitors %s (lock %s); this instance (pid %s) exits",
                         holder, self.db_path, self.lock_file, os.getpid())
            return EXIT_ALREADY_RUNNING
        try:
            self.load_state()
            if self.state.get("frozen_pids") and self._recovery_process() is None:
                self.thaw()  # a previous watchdog died between freeze and thaw
            logger.info("SRE Watchdog pid %s monitoring %s (read-only) every %.0fs", os.getpid(), self.db_path, interval)
            run_watchdog_loop(self.run_cycle, self.stop_event, interval, max_cycles=1 if once else None)
            self.save_state()
        finally:
            self.release_instance_lock()
        return 0


def _read_lock_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _is_live_watchdog(pid: int) -> bool:
    """True when pid is a running process whose command line names this module (AccessDenied counts as live)."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return "watchdog_sre" in " ".join(proc.cmdline())
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
'''),
]
for old, new in edits:
    n = src.count(old)
    assert n == 1, (n, old[:80])
    src = src.replace(old, new)
P.write_text(src, encoding="utf-8")
print("patched", P, len(edits), "edits")
