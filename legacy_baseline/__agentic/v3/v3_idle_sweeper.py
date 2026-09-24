"""
V3 Idle Sweeper for CoChem Pipeline 3.0.0.

Runs background housekeeping against the v3 kanban database whenever the
pipeline is idle:

* sweeps legacy todo / dropzone folders and registers untracked ``*.md`` files
  as priority-5 ``srs`` tasks,
* resets completed recurring tasks back to ``todo``,
* runs the RECURRING_FABLE_IMPROVEMENT read-only ecosystem audit cycle,
* runs the RECURRING_LESSONS_CURATOR job that purges mitigated / deprecated
  incident records from lessons.md while preserving valid rules.

Connections follow the init_db.py pattern (WAL, busy_timeout=5000,
foreign_keys=ON). Filesystem work always happens before a write transaction is
opened, and no network access is performed anywhere in this module.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence

logger = logging.getLogger("v3_idle_sweeper")

MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = MODULE_DIR / "cochem_kanban_v3.db"

try:
    from paths import (  # type: ignore[import-not-found]
        AGENTIC_ROOT,
        COCHEM_BASE_ROOT,
        COCHEM_ROOT,
        DROPZONES_DIR,
        MCP_DIR,
        SCRIPTS_DIR,
    )
    AGENTIC_ROOT = Path(AGENTIC_ROOT)
    COCHEM_ROOT = Path(COCHEM_ROOT)
    DROPZONES_DIR = Path(DROPZONES_DIR)
    SCRIPTS_DIR = Path(SCRIPTS_DIR)
    MCP_DIR = Path(MCP_DIR)
    COCHEM_BASE_ROOT = Path(COCHEM_BASE_ROOT)
except ImportError as _paths_exc:
    logger.debug("paths module unavailable (%s); using built-in defaults", _paths_exc)
    AGENTIC_ROOT = Path(os.getenv("COCHEM_AGENTIC_ROOT", "D:/__agentic"))
    COCHEM_ROOT = Path(os.getenv("COCHEM_ROOT", "D:/__CoChem"))
    DROPZONES_DIR = Path(os.getenv("COCHEM_DROPZONES_DIR", str(AGENTIC_ROOT / "dropzones")))
    SCRIPTS_DIR = AGENTIC_ROOT / "scripts"
    MCP_DIR = AGENTIC_ROOT / "mcp"
    COCHEM_BASE_ROOT = Path(os.getenv("COCHEM_BASE_ROOT", str(COCHEM_ROOT)))

ECOSYSTEM_CATEGORIES: tuple[str, ...] = ("mcp", "scripts", "skills", "agents", "lessons", "ml_rl")

FABLE_TASK_ID = "RECURRING_FABLE_IMPROVEMENT"
FABLE_EVENT = "FABLE_ECOSYSTEM_CYCLE_COMPLETED"
CURATOR_TASK_ID = "RECURRING_LESSONS_CURATOR"
CURATOR_EVENT = "LESSONS_CURATION_COMPLETED"

# Per-component audit cap so a huge directory cannot stall an idle cycle.
MAX_FILES_PER_COMPONENT = 2000

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_STATUS_TAG_RE = re.compile(
    r"\[\s*(MITIGATED|DEPRECATED|RESOLVED|OBSOLETE|SUPERSEDED)\s*\]", re.IGNORECASE
)
_DEPRECATED_INCIDENT_RE = re.compile(
    r"DEF-SPOOF-01|Silent\s+Research\s+Spoofing|\b8D\s+Resolution\b", re.IGNORECASE
)
_EIGHT_D_RE = re.compile(r"\b8D\b", re.IGNORECASE)
_INCIDENT_KEYWORD_RE = re.compile(
    r"counterfeit|mocking|mocked|spoof\w*|falsified", re.IGNORECASE
)
_RESOLUTION_KEYWORD_RE = re.compile(r"mitigated|resolved|deprecated|fixed", re.IGNORECASE)
_EXCESS_BLANK_RE = re.compile(r"\n(?:[ \t]*\n){3,}")


def _utc_now_sql() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def open_connection(db_path: Path | str, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a v3 kanban connection (same PRAGMAs as init_db.open_connection)."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _as_path_list(value) -> list[Path]:
    """Accept a single path (str / PathLike) or a sequence of paths."""
    if value is None:
        return []
    if isinstance(value, (str, os.PathLike)):
        return [Path(value)]
    return [Path(v) for v in value]


def _norm_key(path_text: str) -> str | None:
    """Normalized comparison key for a payload_uri that denotes a filesystem path."""
    if not path_text or "://" in path_text:
        return None
    try:
        return os.path.normcase(str(Path(path_text).resolve()))
    except (OSError, ValueError, RuntimeError) as exc:
        logger.debug("cannot resolve payload_uri %r: %s", path_text, exc)
        return None


class V3IdleSweeper:
    """Idle-time housekeeping worker for the v3 kanban database."""

    def __init__(
        self,
        db_path: str | os.PathLike | None = None,
        dropzone_dirs: Iterable[str | os.PathLike] | None = None,
        lessons_path: str | os.PathLike | None = None,
        ecosystem_dirs: Mapping[str, object] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        if dropzone_dirs is None:
            self.dropzone_dirs = [DROPZONES_DIR / "inbox_srs", DROPZONES_DIR / "inbox_code"]
        else:
            self.dropzone_dirs = _as_path_list(
                dropzone_dirs if isinstance(dropzone_dirs, (str, os.PathLike)) else list(dropzone_dirs)
            )
        self.lessons_path = (
            Path(lessons_path)
            if lessons_path is not None
            else COCHEM_BASE_ROOT / ".docs" / "lessons.md"
        )
        self.ecosystem_dirs = self._build_ecosystem_dirs(ecosystem_dirs)
        self.conn: sqlite3.Connection | None = open_connection(self.db_path)

    # -- connection management ------------------------------------------------
    def _build_ecosystem_dirs(self, supplied: Mapping[str, object] | None) -> dict[str, list[Path]]:
        if supplied is not None:
            result: dict[str, list[Path]] = {cat: [] for cat in ECOSYSTEM_CATEGORIES}
            for key, value in supplied.items():
                result[str(key)] = _as_path_list(value)
            return result
        home = Path.home()
        return {
            "mcp": [MCP_DIR, home / ".gemini" / "antigravity-cli" / "mcp"],
            "scripts": [SCRIPTS_DIR, COCHEM_ROOT / "scripts"],
            "skills": [home / ".gemini" / "config" / "skills", AGENTIC_ROOT / "skills"],
            "agents": [
                AGENTIC_ROOT / "agents",
                home / ".gemini" / "config" / "agents",
                COCHEM_ROOT / "agents",
            ],
            "lessons": [self.lessons_path.parent],
            "ml_rl": [
                COCHEM_BASE_ROOT / "cochem_ml",
                COCHEM_BASE_ROOT / "antigravity-rl",
                COCHEM_BASE_ROOT / "spycfit",
            ],
        }

    def get_connection(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = open_connection(self.db_path)
            return self.conn
        try:
            self.conn.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            logger.info("reopening closed connection to %s (%s)", self.db_path, exc)
            self.conn = open_connection(self.db_path)
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error as exc:
                logger.warning("error closing connection to %s: %s", self.db_path, exc)
            self.conn = None

    def _ensure_task(self, task_id: str, payload_uri: str) -> None:
        conn = self.get_connection()
        now = _utc_now_sql()
        conn.execute(
            """
            INSERT OR IGNORE INTO kanban_tasks_v3
                (task_id, workflow_type, status, priority, current_state,
                 payload_uri, created_at, updated_at)
            VALUES (?, 'recurring', 'todo', 5, 0, ?, ?, ?)
            """,
            (task_id, payload_uri, now, now),
        )
        conn.commit()

    def _log_telemetry(self, task_id: str, event_type: str, payload: dict) -> None:
        conn = self.get_connection()
        conn.execute(
            "INSERT INTO kanban_telemetry_v3 (task_id, event_type, payload, created_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (task_id, event_type, json.dumps(payload, sort_keys=True)),
        )
        conn.execute(
            "UPDATE kanban_tasks_v3 SET updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
            (task_id,),
        )
        conn.commit()

    # -- idleness ---------------------------------------------------------------
    def is_idle(self) -> bool:
        row = self.get_connection().execute(
            """
            SELECT COUNT(*) FROM kanban_tasks_v3
            WHERE status = 'in_progress'
               OR (status = 'todo' AND priority < 5)
               OR (lease_expires_at IS NOT NULL
                   AND datetime(lease_expires_at) > datetime('now'))
            """
        ).fetchone()
        return int(row[0]) == 0

    # -- legacy dropzone sweep --------------------------------------------------
    def sweep_legacy_todo_folders(self, directories=None) -> list[str]:
        if directories is None:
            dirs = self.dropzone_dirs
        else:
            dirs = _as_path_list(
                directories if isinstance(directories, (str, os.PathLike)) else list(directories)
            )

        # Filesystem enumeration first, then a single short write transaction.
        candidates: list[Path] = []
        for directory in dirs:
            if not directory.is_dir():
                logger.debug("dropzone %s does not exist; skipping", directory)
                continue
            candidates.extend(p.resolve() for p in sorted(directory.rglob("*.md")) if p.is_file())

        conn = self.get_connection()
        tracked_raw = {r[0] for r in conn.execute("SELECT payload_uri FROM kanban_tasks_v3")}
        tracked_keys = {k for k in (_norm_key(u) for u in tracked_raw) if k}

        new_rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for path in candidates:
            path_text = str(path)
            key = os.path.normcase(path_text)
            if path_text in tracked_raw or key in tracked_keys or key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha1(path_text.encode("utf-8")).hexdigest()[:8]
            new_rows.append((f"SRS_SWEPT_{path.stem}_{digest}", path_text))

        inserted: list[str] = []
        now = _utc_now_sql()
        for task_id, path_text in new_rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO kanban_tasks_v3
                    (task_id, workflow_type, status, priority, current_state,
                     payload_uri, created_at, updated_at)
                VALUES (?, 'srs', 'todo', 5, 0, ?, ?, ?)
                """,
                (task_id, path_text, now, now),
            )
            if cur.rowcount == 1:
                inserted.append(task_id)
        conn.commit()
        if inserted:
            logger.info("swept %d legacy todo file(s) into kanban_tasks_v3", len(inserted))
        return inserted

    # -- recurring reset --------------------------------------------------------
    def reset_recurring_tasks(self) -> int:
        if not self.is_idle():
            logger.debug("pipeline busy; recurring reset deferred")
            return 0
        conn = self.get_connection()
        cur = conn.execute(
            r"""
            UPDATE kanban_tasks_v3
               SET status = 'todo', current_state = 0, updated_at = CURRENT_TIMESTAMP
             WHERE (workflow_type = 'recurring' OR task_id LIKE 'RECURRING\_%' ESCAPE '\')
               AND status = 'done'
            """
        )
        conn.commit()
        count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        if count:
            logger.info("reset %d recurring task(s) to todo", count)
        return count

    # -- fable ecosystem improvement cycle --------------------------------------
    @staticmethod
    def _audit_file(path: Path, metrics: dict, findings: list[str]) -> None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            findings.append(f"unreadable {path}: {exc}")
            return
        metrics["file_count"] += 1
        metrics["total_bytes"] += size
        suffix = path.suffix.lower()
        if suffix not in (".py", ".md"):
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(f"unreadable {path}: {exc}")
            return
        if suffix == ".py":
            metrics["py_files"] += 1
            try:
                compile(text, str(path), "exec", dont_inherit=True)
            except (SyntaxError, ValueError) as exc:
                metrics["syntax_errors"] += 1
                findings.append(f"syntax error in {path}: {exc}")
        else:
            metrics["md_files"] += 1
            metrics["md_headings"] += sum(
                1 for line in text.splitlines() if _HEADING_RE.match(line)
            )

    def _audit_component(self, entry: Path, findings: list[str]) -> dict:
        metrics = {
            "file_count": 0, "total_bytes": 0, "py_files": 0,
            "syntax_errors": 0, "md_files": 0, "md_headings": 0, "truncated": False,
        }
        if entry.is_file():
            self._audit_file(entry, metrics, findings)
            return metrics
        for root, subdirs, files in os.walk(entry):
            subdirs[:] = sorted(
                d for d in subdirs if not d.startswith(".") and d != "__pycache__"
            )
            for name in sorted(files):
                if name.startswith("."):
                    continue
                if metrics["file_count"] >= MAX_FILES_PER_COMPONENT:
                    metrics["truncated"] = True
                    return metrics
                self._audit_file(Path(root) / name, metrics, findings)
        return metrics

    def run_fable_improvement_cycle(self, targets: Mapping[str, object] | None = None) -> dict:
        self._ensure_task(FABLE_TASK_ID, "ecosystem://fable")
        if targets is None:
            eco = self.ecosystem_dirs
        else:
            eco = {str(cat): _as_path_list(v) for cat, v in targets.items()}
        categories = list(ECOSYSTEM_CATEGORIES) + [c for c in eco if c not in ECOSYSTEM_CATEGORIES]

        # Read-only filesystem audit happens before any write transaction.
        findings: list[str] = []
        scanned: dict[str, int] = {}
        names: dict[str, list[str]] = {}
        details: dict[str, list[dict]] = {}
        for cat in categories:
            records: list[dict] = []
            for directory in eco.get(cat, []):
                if not directory.is_dir():
                    continue
                try:
                    entries = sorted(directory.iterdir())
                except OSError as exc:
                    findings.append(f"cannot list {directory}: {exc}")
                    continue
                for entry in entries:
                    if entry.name.startswith(".") or entry.name == "__pycache__":
                        continue
                    kind = "dir" if entry.is_dir() else "file"
                    records.append({
                        "name": entry.name if kind == "dir" else entry.stem,
                        "path": str(entry),
                        "kind": kind,
                        "metrics": self._audit_component(entry, findings),
                    })
            scanned[cat] = len(records)
            names[cat] = [r["name"] for r in records]
            details[cat] = records

        conn = self.get_connection()
        cycle_index = int(conn.execute(
            "SELECT COUNT(*) FROM kanban_telemetry_v3 WHERE task_id = ? AND event_type = ?",
            (FABLE_TASK_ID, FABLE_EVENT),
        ).fetchone()[0])
        focus = ECOSYSTEM_CATEGORIES[cycle_index % len(ECOSYSTEM_CATEGORIES)]

        summary = {
            "task_id": FABLE_TASK_ID,
            "cycle_index": cycle_index,
            "focus_category": focus,
            "scanned_components": scanned,
            "components": names,
            "focus_details": details.get(focus, []),
            "findings": findings,
            "timestamp": _utc_now_sql(),
        }
        self._log_telemetry(FABLE_TASK_ID, FABLE_EVENT, summary)
        logger.info(
            "fable cycle %d (focus=%s): %d components, %d findings",
            cycle_index, focus, sum(scanned.values()), len(findings),
        )
        return summary

    # -- lessons curation -------------------------------------------------------
    @staticmethod
    def _heading_is_deprecated(heading: str) -> bool:
        if _STATUS_TAG_RE.search(heading):
            return True
        if _DEPRECATED_INCIDENT_RE.search(heading):
            return True
        has_incident = bool(_INCIDENT_KEYWORD_RE.search(heading))
        if has_incident and _EIGHT_D_RE.search(heading):
            return True
        return has_incident and bool(_RESOLUTION_KEYWORD_RE.search(heading))

    @classmethod
    def _curate_text(cls, text: str) -> tuple[str, list[str], int]:
        """Return (curated_text, purged_headings, preserved_count).

        Headings of level >= 2 start sections; a section owns everything up to
        the next heading of the same or higher level, so nested sub-headings
        are purged together with a purged parent. Headings inside fenced code
        blocks are ignored.
        """
        lines = text.splitlines(keepends=True)
        preamble: list[str] = []
        blocks: list[tuple[int, str, list[str]]] = []
        in_fence = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
            match = None if in_fence else _HEADING_RE.match(line.rstrip("\r\n"))
            if match and (blocks or len(match.group(1)) >= 2):
                blocks.append((len(match.group(1)), match.group(2), [line]))
            elif blocks:
                blocks[-1][2].append(line)
            else:
                preamble.append(line)

        kept: list[str] = list(preamble)
        purged: list[str] = []
        preserved = 0
        stack: list[tuple[int, bool]] = []
        for level, heading, block_lines in blocks:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_purged = any(flag for _, flag in stack)
            is_purged = level >= 2 and (parent_purged or cls._heading_is_deprecated(heading))
            stack.append((level, is_purged))
            if is_purged:
                if not parent_purged:
                    purged.append(heading)
            else:
                kept.extend(block_lines)
                if level >= 2:
                    preserved += 1

        if not purged:
            return text, purged, preserved
        curated = _EXCESS_BLANK_RE.sub("\n\n\n", "".join(kept))
        if text.endswith("\n"):
            curated = curated.rstrip("\n") + "\n"
        return curated, purged, preserved

    def curate_lessons(self, lessons_path: str | os.PathLike | None = None) -> dict:
        self._ensure_task(CURATOR_TASK_ID, "lessons://curator")
        path = Path(lessons_path) if lessons_path is not None else self.lessons_path

        if not path.is_file():
            result = {
                "status": "missing", "path": str(path), "purged_count": 0,
                "preserved_count": 0, "purged_headings": [],
                "bytes_before": 0, "bytes_after": 0,
            }
            logger.warning("lessons file missing: %s", path)
            self._log_telemetry(CURATOR_TASK_ID, CURATOR_EVENT, result)
            return result

        with open(path, "r", encoding="utf-8", newline="") as fh:
            original = fh.read()
        curated, purged, preserved = self._curate_text(original)
        changed = curated != original
        if changed:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(curated)

        result = {
            "status": "curated" if changed else "clean",
            "purged_count": len(purged),
            "preserved_count": preserved,
            "purged_headings": purged,
            "bytes_before": len(original.encode("utf-8")),
            "bytes_after": len(curated.encode("utf-8")),
            "path": str(path),
        }
        self._log_telemetry(CURATOR_TASK_ID, CURATOR_EVENT, result)
        logger.info("lessons curation: purged %d, preserved %d", len(purged), preserved)
        return result

    # -- orchestration ----------------------------------------------------------
    def run_sweep_cycle(self) -> dict:
        summary: dict = {"timestamp": _utc_now_sql()}
        summary["swept_task_ids"] = self.sweep_legacy_todo_folders()
        idle = self.is_idle()
        summary["idle"] = idle
        if idle:
            summary["recurring_reset"] = self.reset_recurring_tasks()
            fable = self.run_fable_improvement_cycle()
            summary["fable"] = {
                "cycle_index": fable["cycle_index"],
                "focus_category": fable["focus_category"],
                "scanned_components": fable["scanned_components"],
                "findings": len(fable["findings"]),
            }
            summary["lessons"] = self.curate_lessons()
        return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CoChem v3 idle sweeper")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="v3 kanban SQLite DB")
    parser.add_argument("--once", action="store_true", help="run a single sweep cycle and exit")
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between cycles")
    parser.add_argument("--dropzone", action="append", default=None,
                        help="legacy todo/dropzone directory (repeatable)")
    parser.add_argument("--lessons-path", default=None, help="path to lessons.md")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db_path = Path(args.db_path)
    if not db_path.is_file():
        logger.error("database not found: %s (run init_db.py first)", db_path)
        return 1

    sweeper = V3IdleSweeper(
        db_path=db_path, dropzone_dirs=args.dropzone, lessons_path=args.lessons_path
    )
    try:
        while True:
            try:
                summary = sweeper.run_sweep_cycle()
                logger.info("sweep cycle: %s", json.dumps(summary, default=str)[:2000])
            except sqlite3.Error as exc:
                logger.error("sweep cycle failed: %s", exc)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        logger.info("sweeper interrupted; shutting down")
        return 0
    finally:
        sweeper.close()


if __name__ == "__main__":
    raise SystemExit(main())
