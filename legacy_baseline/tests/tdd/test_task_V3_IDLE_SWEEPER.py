"""TDD contract for V3_IDLE_SWEEPER.

Deliverable under test: __agentic/v3/v3_idle_sweeper.py (workspace-local).

Public API bound by this contract:

    V3IdleSweeper(
        db_path,                 # path to a SQLite DB already carrying schema_v3.sql
        dropzone_dirs=None,      # iterable of legacy todo/dropzone directories
        lessons_path=None,       # path to lessons.md
        ecosystem_dirs=None,     # mapping: 'mcp', 'scripts', 'skills', 'agents',
                                 #          'lessons', 'ml_rl' -> directory
    )
    .conn / .get_connection()      sqlite3.Connection (WAL, busy_timeout=5000)
    .is_idle() -> bool
    .sweep_legacy_todo_folders()   inject untracked *.md as priority-5 srs tasks
    .reset_recurring_tasks()       done -> todo for recurring tasks when idle
    .run_fable_improvement_cycle() RECURRING_FABLE_IMPROVEMENT job
    .curate_lessons()              RECURRING_LESSONS_CURATOR job
    .close()

Everything runs against real SQLite databases built from schema_v3.sql, real
files under tmp_path, and an AST parse of the real module. Nothing under test
is mocked; a process audit hook fails any test that opens a network socket.
"""
from __future__ import annotations

import ast
import datetime as _dt
import importlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = Path(r"D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\legacy_baseline")
if not WORKSPACE_ROOT.is_dir():
    WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_V3 = WORKSPACE_ROOT / "__agentic" / "v3"
TARGET_V3 = Path(r"D:\__CoChem\__agentic\v3")

for _p in (WORKSPACE_ROOT, TARGET_V3, WORKSPACE_V3):
    if str(_p) in sys.path:
        sys.path.remove(str(_p))
    sys.path.insert(0, str(_p))

MODULE_NAME = "v3_idle_sweeper"


def _resolve_v3_file(filename: str) -> Path:
    """Return the first existing candidate (WORKSPACE_V3, then TARGET_V3); default WORKSPACE_V3."""
    for candidate_dir in (WORKSPACE_V3, TARGET_V3):
        candidate = candidate_dir / filename
        if candidate.is_file():
            return candidate
    return WORKSPACE_V3 / filename


def _resolve_module_path() -> Path:
    return _resolve_v3_file(f"{MODULE_NAME}.py")


def _resolve_schema_path() -> Path:
    return _resolve_v3_file("schema_v3.sql")


MODULE_PATH = _resolve_module_path()
SCHEMA_PATH = _resolve_schema_path()

FABLE_TASK_ID = "RECURRING_FABLE_IMPROVEMENT"
CURATOR_TASK_ID = "RECURRING_LESSONS_CURATOR"
ECOSYSTEM_CATEGORIES = ("mcp", "scripts", "skills", "agents", "lessons", "ml_rl")

SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
FORBIDDEN_IMPORTS = {
    "unittest.mock", "mock", "requests", "httpx", "urllib.request", "urllib3",
    "http.client", "socket", "aiohttp", "paramiko", "websocket", "websockets",
}

# ── Offline guard (audit hook; no monkeypatching) ─────────────────────────────
_NETWORK_EVENTS: list[str] = []
_NETWORK_GUARD = {"armed": False}


def _network_audit_hook(event: str, args) -> None:
    if _NETWORK_GUARD["armed"] and event in (
        "socket.connect", "socket.getaddrinfo", "socket.sendto", "urllib.Request",
    ):
        _NETWORK_EVENTS.append(f"{event}: {args!r}")
        raise RuntimeError(f"network access forbidden in V3IdleSweeper: {event}")


sys.addaudithook(_network_audit_hook)


@pytest.fixture(autouse=True)
def _offline_guard():
    _NETWORK_EVENTS.clear()
    _NETWORK_GUARD["armed"] = True
    try:
        yield
    finally:
        _NETWORK_GUARD["armed"] = False
    assert not _NETWORK_EVENTS, f"network activity detected: {_NETWORK_EVENTS}"


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_v3_idle_sweeper_cls():
    """Load V3IdleSweeper from the workspace module (fails per-test if absent)."""
    if MODULE_PATH.is_file():
        spec = importlib.util.spec_from_file_location(MODULE_NAME, str(MODULE_PATH))
        assert spec is not None and spec.loader is not None, f"cannot load {MODULE_PATH}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(MODULE_NAME)
    cls = getattr(module, "V3IdleSweeper", None)
    assert cls is not None, "v3_idle_sweeper.py does not define V3IdleSweeper"
    assert isinstance(cls, type), "V3IdleSweeper must be a class"
    return cls


def _make_db(tmp_path: Path, name: str = "kanban_v3_under_test.db") -> Path:
    assert SCHEMA_PATH.is_file(), f"schema_v3.sql missing at {SCHEMA_PATH}"
    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "kanban_tasks_v3" in sql_text and "kanban_telemetry_v3" in sql_text
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.executescript(sql_text)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _sql_ts(delta_seconds: int = 0) -> str:
    moment = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=delta_seconds)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _insert_task(
    db_path: Path,
    task_id: str,
    *,
    workflow_type: str = "srs",
    status: str = "todo",
    priority: int = 1,
    current_state: int = 0,
    payload_uri: str | None = None,
    lease_expires_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.execute(
            """
            INSERT INTO kanban_tasks_v3
                (task_id, workflow_type, status, priority, current_state,
                 payload_uri, lease_expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id, workflow_type, status, priority, current_state,
                payload_uri or f"payload://{task_id}", lease_expires_at,
                updated_at or _sql_ts(), updated_at or _sql_ts(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _set_status(db_path: Path, task_id: str, status: str) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        cur = conn.execute(
            "UPDATE kanban_tasks_v3 SET status = ? WHERE task_id = ?", (status, task_id)
        )
        assert cur.rowcount == 1, f"task {task_id} not found"
        conn.commit()
    finally:
        conn.close()


def _fetch_task(db_path: Path, task_id: str):
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM kanban_tasks_v3 WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()


def _telemetry_rows(db_path: Path, task_id: str):
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM kanban_telemetry_v3 WHERE task_id = ? ORDER BY telemetry_id",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()


_CLOSE_ERRORS: list[str] = []


def _close(sweeper) -> None:
    if hasattr(sweeper, "close") and callable(sweeper.close):
        sweeper.close()
    elif hasattr(sweeper, "conn") and isinstance(sweeper.conn, sqlite3.Connection):
        try:
            sweeper.conn.close()
        except Exception as exc:
            _CLOSE_ERRORS.append(f"{type(exc).__name__}: {exc}")


# ── T1 ────────────────────────────────────────────────────────────────────────
def test_v3_idle_sweeper_db_connection_and_pragmas(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)
    _insert_task(db_path, "T1_PROBE", status="done", priority=3)

    sweeper = V3IdleSweeper(db_path=str(db_path))
    try:
        conn = getattr(sweeper, "conn", None)
        if conn is None and hasattr(sweeper, "get_connection"):
            conn = sweeper.get_connection()
        assert isinstance(conn, sqlite3.Connection), (
            "sweeper must provide a valid sqlite3.Connection via .conn or .get_connection()"
        )

        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal", f"journal_mode={journal_mode!r}"
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert int(busy_timeout) == 5000, f"busy_timeout={busy_timeout!r}"

        row = conn.execute(
            "SELECT task_id, status, priority FROM kanban_tasks_v3 WHERE task_id = ?",
            ("T1_PROBE",),
        ).fetchone()
        assert row is not None and tuple(row)[:3] == ("T1_PROBE", "done", 3)

        # The sweeper must be pointed at our file, not some global DB.
        db_list = conn.execute("PRAGMA database_list").fetchall()
        main_file = [r[2] for r in db_list if r[1] == "main"][0]
        assert Path(main_file).resolve() == db_path.resolve()
    finally:
        _close(sweeper)

    # Independent verification: WAL is persisted in the database file itself.
    independent = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        mode = independent.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", f"WAL not persisted on disk (got {mode!r})"
        tables = {
            r[0] for r in independent.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"kanban_tasks_v3", "kanban_telemetry_v3"} <= tables
    finally:
        independent.close()


# ── T2 ────────────────────────────────────────────────────────────────────────
def test_v3_idle_sweeper_idleness_detection(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)

    sweeper = V3IdleSweeper(db_path=str(db_path))
    try:
        assert sweeper.is_idle() is True, "empty queue must be idle"

        active = [
            ("T2_P1_INPROG", "in_progress", 1),
            ("T2_P2_TODO", "todo", 2),
            ("T2_P3_INPROG", "in_progress", 3),
            ("T2_P4_TODO", "todo", 4),
        ]
        for task_id, status, prio in active:
            _insert_task(db_path, task_id, status=status, priority=prio)

        assert sweeper.is_idle() is False, "high-priority active work must block idleness"

        # Clear one at a time: each remaining high-priority task alone blocks idleness.
        for i, (task_id, _status, _prio) in enumerate(active):
            assert sweeper.is_idle() is False, f"still active before finishing {task_id}"
            _set_status(db_path, task_id, "done" if i % 2 == 0 else "failed")
        assert sweeper.is_idle() is True, "done/failed tasks must not block idleness"

        # Priority-5 backlog in 'todo' does not break idleness...
        _insert_task(db_path, "T2_P5_TODO", status="todo", priority=5)
        assert sweeper.is_idle() is True, "priority-5 todo must not break idleness"

        # ...but priority-5 work actually running does.
        _set_status(db_path, "T2_P5_TODO", "in_progress")
        assert sweeper.is_idle() is False, "in_progress priority-5 task must break idleness"
        _set_status(db_path, "T2_P5_TODO", "done")
        assert sweeper.is_idle() is True

        # An unexpired lease marks the pipeline busy regardless of priority.
        _insert_task(
            db_path, "T2_LEASED", status="todo", priority=5,
            lease_expires_at=_sql_ts(+3600),
        )
        assert sweeper.is_idle() is False, "unexpired lease must mark pipeline non-idle"

        # An expired lease is stale and must not block idleness.
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute(
                "UPDATE kanban_tasks_v3 SET lease_expires_at = ? WHERE task_id = ?",
                (_sql_ts(-3600), "T2_LEASED"),
            )
            conn.commit()
        finally:
            conn.close()
        assert sweeper.is_idle() is True, "expired lease must not block idleness"
    finally:
        _close(sweeper)


# ── T3 ────────────────────────────────────────────────────────────────────────
def test_v3_idle_sweeper_scan_legacy_todo_injection(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)

    dropzone_a = tmp_path / "legacy_todo"
    dropzone_b = tmp_path / "dropzone"
    nested = dropzone_b / "nested"
    for d in (dropzone_a, nested):
        d.mkdir(parents=True, exist_ok=True)

    new_files = [
        dropzone_a / "fix_torsion_scan.md",
        dropzone_a / "refit_charges.md",
        dropzone_b / "audit_mcp_router.md",
    ]
    for i, f in enumerate(new_files):
        f.write_text(f"# Legacy task {i}\n\nReal backlog item {f.stem}.\n", encoding="utf-8")

    already_tracked = dropzone_b / "already_tracked.md"
    already_tracked.write_text("# Already registered\n", encoding="utf-8")
    _insert_task(
        db_path, "PRE_TRACKED", status="done", priority=2,
        payload_uri=str(already_tracked.resolve()),
    )

    ignored = dropzone_a / "notes.txt"
    ignored.write_text("not markdown", encoding="utf-8")

    sweeper = V3IdleSweeper(
        db_path=str(db_path), dropzone_dirs=[str(dropzone_a), str(dropzone_b)]
    )
    try:
        sweeper.sweep_legacy_todo_folders()

        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM kanban_tasks_v3").fetchall()
        finally:
            conn.close()

        by_path: dict[Path, list] = {}
        for r in rows:
            try:
                key = Path(r["payload_uri"]).resolve()
            except (OSError, ValueError):
                continue
            by_path.setdefault(key, []).append(r)

        for f in new_files:
            matches = by_path.get(f.resolve(), [])
            assert len(matches) == 1, f"{f.name} injected {len(matches)} times (expected 1)"
            r = matches[0]
            assert r["status"] == "todo"
            assert r["priority"] == 5
            assert r["workflow_type"] == "srs"
            assert r["current_state"] == 0
            assert Path(r["payload_uri"]).is_file(), "payload_uri must point at the real file"
            assert r["task_id"], "injected task must have a task_id"

        tracked = by_path.get(already_tracked.resolve(), [])
        assert len(tracked) == 1 and tracked[0]["task_id"] == "PRE_TRACKED", (
            "already registered file must not be re-injected"
        )
        assert ignored.resolve() not in by_path, "non-markdown files must be ignored"

        injected_ids = {m[0]["task_id"] for f in new_files for m in [by_path[f.resolve()]]}
        assert len(injected_ids) == len(new_files), "task_ids must be unique"
        total_before = len(rows)

        # Idempotency: repeated sweeps must not duplicate.
        sweeper.sweep_legacy_todo_folders()
        sweeper.sweep_legacy_todo_folders()
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            total_after = conn.execute("SELECT COUNT(*) FROM kanban_tasks_v3").fetchone()[0]
            dupes = conn.execute(
                "SELECT payload_uri, COUNT(*) FROM kanban_tasks_v3 "
                "GROUP BY payload_uri HAVING COUNT(*) > 1"
            ).fetchall()
        finally:
            conn.close()
        assert total_after == total_before, "repeat sweeps inserted new rows"
        assert not dupes, f"duplicate payload_uri rows: {dupes}"

        # A newly dropped file is picked up on the next sweep.
        late = nested / "late_arrival.md"
        late.write_text("# Late arrival\n", encoding="utf-8")
        sweeper.sweep_legacy_todo_folders()
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            late_rows = [
                r for r in conn.execute(
                    "SELECT payload_uri, priority, status, workflow_type FROM kanban_tasks_v3"
                ).fetchall()
                if Path(r[0]).resolve() == late.resolve()
            ]
        finally:
            conn.close()
        assert len(late_rows) == 1 and late_rows[0][1:] == (5, "todo", "srs")
    finally:
        _close(sweeper)


# ── T4 ────────────────────────────────────────────────────────────────────────
def test_v3_idle_sweeper_recurring_task_reset_when_idle(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)
    stale = "2000-01-01 00:00:00"

    _insert_task(
        db_path, "WEEKLY_AUDIT", workflow_type="recurring", status="done",
        priority=5, current_state=7, updated_at=stale,
    )
    _insert_task(
        db_path, "RECURRING_NIGHTLY_REFIT", workflow_type="srs", status="done",
        priority=5, current_state=4, updated_at=stale,
    )
    _insert_task(
        db_path, "ONE_SHOT_TASK", workflow_type="srs", status="done",
        priority=5, current_state=9, updated_at=stale,
    )
    _insert_task(
        db_path, "RECURRING_FAILED_JOB", workflow_type="recurring", status="failed",
        priority=5, current_state=3, updated_at=stale,
    )

    sweeper = V3IdleSweeper(db_path=str(db_path))
    try:
        # Pipeline busy: nothing may be reset.
        _insert_task(db_path, "BLOCKER", status="in_progress", priority=1)
        assert sweeper.is_idle() is False
        sweeper.reset_recurring_tasks()
        for tid in ("WEEKLY_AUDIT", "RECURRING_NIGHTLY_REFIT"):
            row = _fetch_task(db_path, tid)
            assert row["status"] == "done", f"{tid} reset while pipeline busy"
            assert row["current_state"] != 0
            assert row["updated_at"] == stale

        # Pipeline idle: recurring 'done' tasks reset, others untouched.
        _set_status(db_path, "BLOCKER", "done")
        assert sweeper.is_idle() is True
        sweeper.reset_recurring_tasks()

        for tid in ("WEEKLY_AUDIT", "RECURRING_NIGHTLY_REFIT"):
            row = _fetch_task(db_path, tid)
            assert row["status"] == "todo", f"{tid} not reset to todo"
            assert row["current_state"] == 0, f"{tid} current_state not reset"
            assert row["updated_at"] != stale, f"{tid} updated_at not refreshed"
            assert str(row["updated_at"]) > stale

        one_shot = _fetch_task(db_path, "ONE_SHOT_TASK")
        assert one_shot["status"] == "done"
        assert one_shot["current_state"] == 9
        assert one_shot["updated_at"] == stale

        failed = _fetch_task(db_path, "RECURRING_FAILED_JOB")
        assert failed["status"] == "failed", "only 'done' recurring tasks are reset"

        blocker = _fetch_task(db_path, "BLOCKER")
        assert blocker["status"] == "done", "non-recurring completed task must stay done"
    finally:
        _close(sweeper)


# ── T5 ────────────────────────────────────────────────────────────────────────
def test_v3_idle_sweeper_fable_ecosystem_cycle(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)

    eco_root = tmp_path / "ecosystem"
    seeded: dict[str, str] = {}
    ecosystem_dirs: dict[str, str] = {}
    layout = {
        "mcp": ("mcp_servers", "mcp_quota_router_server", "server.py",
                "def serve():\n    return 'quota-router'\n"),
        "scripts": ("scripts", "sweep_torsion_profiles", None,
                    "def main():\n    return 42\n"),
        "skills": ("skills", "skill_forcefield_refit", "SKILL.md",
                   "# Forcefield refit skill\n"),
        "agents": ("agents", "agent_architect_reviewer", None,
                   "# Architect reviewer agent\n"),
        "lessons": ("lessons", "lessons_pipeline_core", None,
                    "# Lessons\n\n## Keep WAL enabled\nAlways.\n"),
        "ml_rl": ("ml_rl", "rl_torsion_policy", "train.py",
                  "def train():\n    return 0.0\n"),
    }
    for category, (dirname, item, inner, content) in layout.items():
        base = eco_root / dirname
        base.mkdir(parents=True, exist_ok=True)
        if inner:
            (base / item).mkdir()
            (base / item / inner).write_text(content, encoding="utf-8")
        else:
            suffix = ".py" if category == "scripts" else ".md"
            (base / f"{item}{suffix}").write_text(content, encoding="utf-8")
        seeded[category] = item
        ecosystem_dirs[category] = str(base)

    sweeper = V3IdleSweeper(db_path=str(db_path), ecosystem_dirs=ecosystem_dirs)
    try:
        assert sweeper.is_idle() is True
        result = sweeper.run_fable_improvement_cycle()
        assert result, "fable improvement cycle must return a non-empty result"
        result_text = str(result)
        for category, item in seeded.items():
            assert item in result_text, (
                f"{category} component {item!r} not enumerated in cycle result"
            )

        task = _fetch_task(db_path, FABLE_TASK_ID)
        assert task is not None, f"{FABLE_TASK_ID} must be registered in kanban_tasks_v3"
        is_recurring = (
            task["workflow_type"] == "recurring" or task["task_id"].startswith("RECURRING_")
        )
        assert is_recurring

        rows = _telemetry_rows(db_path, FABLE_TASK_ID)
        assert rows, "fable cycle must log telemetry into kanban_telemetry_v3"
        for r in rows:
            assert r["event_type"], "telemetry event_type must be populated"
        telemetry_text = "\n".join(str(r["payload"] or "") for r in rows)
        for category, item in seeded.items():
            assert item in telemetry_text, (
                f"telemetry does not record the {category} target {item!r}"
            )
        first_count = len(rows)

        # A second cycle logs fresh execution events (it cycles, not a one-shot).
        second = sweeper.run_fable_improvement_cycle()
        assert second, "second fable cycle returned nothing"
        rows_after = _telemetry_rows(db_path, FABLE_TASK_ID)
        assert len(rows_after) > first_count, "second cycle did not record new telemetry"

        # Nothing outside the ecosystem dirs may be modified/deleted.
        for category, (dirname, item, inner, content) in layout.items():
            base = eco_root / dirname
            if inner:
                target = base / item / inner
            else:
                target = base / (f"{item}.py" if category == "scripts" else f"{item}.md")
            assert target.is_file(), f"cycle destroyed seeded component {target}"
    finally:
        _close(sweeper)


# ── T6 ────────────────────────────────────────────────────────────────────────
LESSONS_FIXTURE = """# Lessons Learned

## Keep SQLite in WAL mode for concurrent workers
Open every kanban connection with PRAGMA journal_mode=WAL and busy_timeout=5000.
Concurrent readers otherwise block the lease heartbeat writer.

## [MITIGATED] Counterfeit test log from mocked supervisor
The P4 run produced a counterfeit green log by mocking subprocess.Popen.
Mitigated by the audit-hook harness; mocking is now banned in TDD contracts.

## Torsion scans need 15 degree resolution
Dihedral scans coarser than 15 degrees miss the gauche minimum on butane-like fragments.
Physical rule: E(phi) must be sampled at >= 24 points per rotor.

## [DEPRECATED] Mocking log: fake array returned by fit_charges
fit_charges returned a hard-coded fake array to satisfy the test; deprecated after rewrite.

## [RESOLVED] Spoofing finding: stub NotImplementedError in watchdog
Auditor flagged a spoofed watchdog stub. Resolved in commit a8739d8.

## Windows child processes must be windowless
Every subprocess call uses creationflags=subprocess.CREATE_NO_WINDOW and encoding='utf-8'.
"""

SURVIVING_HEADINGS = (
    "Keep SQLite in WAL mode for concurrent workers",
    "Torsion scans need 15 degree resolution",
    "Windows child processes must be windowless",
)
SURVIVING_BODY_LINES = (
    "Open every kanban connection with PRAGMA journal_mode=WAL and busy_timeout=5000.",
    "Physical rule: E(phi) must be sampled at >= 24 points per rotor.",
    "Every subprocess call uses creationflags=subprocess.CREATE_NO_WINDOW and encoding='utf-8'.",
)
PURGED_MARKERS = (
    "Counterfeit test log from mocked supervisor",
    "by mocking subprocess.Popen",
    "Mocking log: fake array returned by fit_charges",
    "hard-coded fake array",
    "Spoofing finding: stub NotImplementedError in watchdog",
    "[MITIGATED]",
    "[DEPRECATED]",
    "[RESOLVED]",
)


def test_v3_idle_sweeper_lessons_parser_and_purge(tmp_path):
    V3IdleSweeper = get_v3_idle_sweeper_cls()
    db_path = _make_db(tmp_path)
    lessons_path = tmp_path / "lessons.md"
    lessons_path.write_text(LESSONS_FIXTURE, encoding="utf-8")
    original_mtime_ns = lessons_path.stat().st_mtime_ns

    sweeper = V3IdleSweeper(db_path=str(db_path), lessons_path=str(lessons_path))
    try:
        sweeper.curate_lessons()

        assert lessons_path.is_file(), "lessons.md must be written back, not deleted"
        curated = lessons_path.read_text(encoding="utf-8")
        assert curated.strip(), "curated lessons.md is empty"
        assert lessons_path.stat().st_mtime_ns >= original_mtime_ns
        assert curated != LESSONS_FIXTURE, "lessons.md was not sanitized"

        for marker in PURGED_MARKERS:
            assert marker not in curated, f"mitigated/deprecated entry survived: {marker!r}"
        for heading in SURVIVING_HEADINGS:
            assert heading in curated, f"valid lesson dropped: {heading!r}"
        for line in SURVIVING_BODY_LINES:
            assert line in curated, f"valid lesson body altered/dropped: {line!r}"
        assert "# Lessons Learned" in curated, "document title must be preserved"

        # Order of surviving lessons is preserved.
        positions = [curated.index(h) for h in SURVIVING_HEADINGS]
        assert positions == sorted(positions), "surviving lessons were reordered"

        rows = _telemetry_rows(db_path, CURATOR_TASK_ID)
        assert rows, "curate_lessons must log telemetry into kanban_telemetry_v3"
        assert all(r["event_type"] for r in rows)

        task = _fetch_task(db_path, CURATOR_TASK_ID)
        assert task is not None, f"{CURATOR_TASK_ID} must be registered in kanban_tasks_v3"

        # Idempotent: a second pass over clean lessons changes nothing.
        sweeper.curate_lessons()
        assert lessons_path.read_text(encoding="utf-8") == curated
    finally:
        _close(sweeper)


# ── T7 ────────────────────────────────────────────────────────────────────────
def _is_subprocess_call(node: ast.Call, subprocess_aliases: set[str], direct: set[str]) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_CALLS:
        return isinstance(func.value, ast.Name) and func.value.id in subprocess_aliases
    if isinstance(func, ast.Name):
        return func.id in direct
    return False


def _is_only_placeholder(body: list[ast.stmt]) -> bool:
    stmts = [
        s for s in body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]
    if not stmts:
        return True
    return all(
        isinstance(s, ast.Pass)
        or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
            and s.value.value is Ellipsis)
        for s in stmts
    )


def test_v3_idle_sweeper_anti_spoofing_and_subprocess_flags():
    if not MODULE_PATH.is_file():
        raise FileNotFoundError(f"deliverable missing: {MODULE_PATH}")
    assert MODULE_PATH.is_file(), f"deliverable missing: {MODULE_PATH}"
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.strip(), "v3_idle_sweeper.py is empty"
    tree = ast.parse(source, filename=str(MODULE_PATH))

    # The class must actually be defined in this file.
    class_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "V3IdleSweeper" in class_names

    methods = {
        n.name
        for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name == "V3IdleSweeper"
        for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for required in (
        "is_idle", "sweep_legacy_todo_folders", "reset_recurring_tasks",
        "run_fable_improvement_cycle", "curate_lessons", "close",
    ):
        assert required in methods, f"V3IdleSweeper.{required} not defined"

    # No NotImplementedError anywhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "NotImplementedError":
            pytest.fail(f"NotImplementedError at line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "NotImplementedError":
            pytest.fail(f"NotImplementedError at line {node.lineno}")

    # No empty pass / ellipsis stubs in any block.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not _is_only_placeholder(node.body), (
                f"stub function {node.name!r} at line {node.lineno}"
            )
        if isinstance(node, ast.ClassDef):
            assert not _is_only_placeholder(node.body), (
                f"empty class {node.name!r} at line {node.lineno}"
            )
        if isinstance(node, ast.ExceptHandler):
            assert not _is_only_placeholder(node.body), (
                f"silent 'except: pass' at line {node.lineno}"
            )
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            assert not all(isinstance(s, ast.Pass) for s in node.body), (
                f"empty pass block at line {node.lineno}"
            )

    # No mocking / network modules; no fake/dummy/mock identifiers.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS, f"forbidden import {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in FORBIDDEN_IMPORTS, f"forbidden import {node.module}"
            if node.module == "unittest":
                assert all(a.name != "mock" for a in node.names), "unittest.mock import"
        if isinstance(node, ast.Name):
            lowered = node.id.lower()
            assert not any(k in lowered for k in ("mock", "fake", "dummy")), (
                f"spoofing identifier {node.id!r} at line {node.lineno}"
            )

    # Every subprocess invocation is windowless and utf-8.
    subprocess_aliases = {"subprocess"}
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    subprocess_aliases.add(alias.asname or "subprocess")
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_CALLS:
                    direct.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_subprocess_call(node, subprocess_aliases, direct):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        assert "creationflags" in kwargs, (
            f"subprocess call at line {node.lineno} lacks creationflags"
        )
        flag_src = ast.unparse(kwargs["creationflags"])
        assert "CREATE_NO_WINDOW" in flag_src, (
            f"line {node.lineno}: creationflags={flag_src!r} lacks CREATE_NO_WINDOW"
        )
        assert "encoding" in kwargs, f"subprocess call at line {node.lineno} lacks encoding"
        enc = kwargs["encoding"]
        assert isinstance(enc, ast.Constant) and str(enc.value).lower().replace("_", "-") == "utf-8", (
            f"line {node.lineno}: encoding must be 'utf-8', got {ast.unparse(enc)!r}"
        )

    # os.system / os.popen bypass the windowless invariant entirely.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr in {"system", "popen", "startfile"}
        ):
            pytest.fail(f"os.{node.func.attr} at line {node.lineno} bypasses CREATE_NO_WINDOW")

    # Importing the real module must not touch the network (audit hook armed).
    cls = get_v3_idle_sweeper_cls()
    assert cls.__name__ == "V3IdleSweeper"
