#!/usr/bin/env python3
"""V4 multi-database SQLite WAL migration utility (Task 196.08).

Upgrades every live CoChem queue table to carry a validated JSON payload
envelope, additively and idempotently:

* Discovery - finds the SQLite database files referenced by
  ``cochem_kanban.py``, ``cochem_kanban_mcp.py`` (including
  ``DEFAULT_V3_DB_PATH``) and ``fix_queue.py`` by statically evaluating their
  path expressions (no module import, no side effects). Inside each database,
  live queue tables are found through ``sqlite_master`` / ``PRAGMA
  table_info``: real tables (not views, not ``sqlite_%``, not ``%_archive``)
  having a ``status`` column plus ``prompt``, ``description``, ``task_id`` or
  ``workflow_type``.
* Pre-flight - ``SELECT json_valid('{}')`` must return 1, otherwise exit 2
  with the schema untouched.
* Lock detection - ``BEGIN IMMEDIATE`` with a configurable busy timeout
  (default 500 ms). If the write lock cannot be acquired: stderr
  ``WRITE_LOCK_HELD`` and exit 3 with the schema untouched.
* Backup - a single transactionally consistent ``<db>.bak_<UTC>`` copy made
  with the SQLite online backup API, only when schema mutation is needed.
* Schema - 7 additive columns (``payload`` with ``CHECK(json_valid(payload))``),
  an expression index on ``json_extract(payload, '$.task_id')`` and
  ``PRAGMA journal_mode = wal``.
* Verification - post-state check of every live table and 100% row-count
  parity for every table in the backup's ``sqlite_master``.

Exit codes: 0 success / already migrated / discovery done, 1 general error,
2 JSON1 unavailable, 3 WRITE_LOCK_HELD.

CLI::

    python v4_migrate_kanban.py [--db PATH] [--discover] [--busy-timeout MS]
                                [--evidence-dir DIR]
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent
EVIDENCE_DIR = REPO_ROOT / ".evidence"
DDL_EVIDENCE_NAME = "kanban_ddl_before.sql"
LIVE_TABLES_EVIDENCE_NAME = "kanban_live_tables.txt"
SOURCE_FILES = ("cochem_kanban.py", "cochem_kanban_mcp.py", "fix_queue.py")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_JSON1 = 2
EXIT_WRITE_LOCK_HELD = 3

DEFAULT_BUSY_TIMEOUT_MS = 500
BACKUP_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# Column name -> declaration used in ALTER TABLE ... ADD COLUMN.
V4_COLUMNS: tuple[tuple[str, str], ...] = (
    ("payload", "TEXT CHECK(json_valid(payload))"),
    ("schema_name", "TEXT"),
    ("schema_version", "TEXT"),
    ("schema_sha256", "TEXT"),
    ("producer_provider", "TEXT"),
    ("produced_at_utc", "REAL"),
    ("payload_sha256", "TEXT"),
)
V4_COLUMN_NAMES: tuple[str, ...] = tuple(name for name, _ in V4_COLUMNS)
_V4_COLUMN_DECL = dict(V4_COLUMNS)

LIVE_MARKER_COLUMNS = frozenset({"prompt", "description", "task_id", "workflow_type"})
TASK_ID_EXPR = "json_extract(payload, '$.task_id')"
_TASK_ID_EXPR_COMPACT = "json_extract(payload,'$.task_id')"
_JSON_CHECK_COMPACT = "check(json_valid(payload))"

_LOCK_ERRCODES = {5, 6}  # SQLITE_BUSY, SQLITE_LOCKED


class MigrationError(RuntimeError):
    """Raised when the migration cannot be completed safely."""


# --------------------------------------------------------------------------- #
# Small SQLite helpers
# --------------------------------------------------------------------------- #
def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()]


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else ""


def _has_json_check(table_sql: str) -> bool:
    return _JSON_CHECK_COMPACT in _compact(table_sql)


def _find_task_id_index(conn: sqlite3.Connection, table: str) -> Optional[str]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    for name, sql in rows:
        if _TASK_ID_EXPR_COMPACT in _compact(sql):
            return name
    return None


def _journal_mode(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def _is_lock_error(exc: BaseException) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None and (code & 0xFF) in _LOCK_ERRCODES:
        return True
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_json1(conn: sqlite3.Connection) -> bool:
    """Return True when SELECT json_valid('{}') evaluates to 1 on ``conn``."""
    try:
        row = conn.execute("SELECT json_valid('{}')").fetchone()
    except sqlite3.Error:
        return False
    return row is not None and row[0] == 1


_default_check_json1 = check_json1


def discover_live_tables(conn: sqlite3.Connection) -> list[str]:
    """Return the live queue tables of the database behind ``conn`` (sorted)."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_archive' ORDER BY name"
    ).fetchall()
    live: list[str] = []
    for name, sql in rows:
        if sql and sql.lstrip().upper().startswith("CREATE VIRTUAL"):
            continue  # virtual tables cannot be ALTERed
        cols = set(_table_columns(conn, name))
        if "status" in cols and cols & LIVE_MARKER_COLUMNS:
            live.append(name)
    return live


def is_table_migrated(conn: sqlite3.Connection, table_name: str) -> bool:
    """True when all 7 V4 columns exist on ``table_name``."""
    existing = {c.lower() for c in _table_columns(conn, table_name)}
    return all(col in existing for col in V4_COLUMN_NAMES)


def dump_ddl(conn: sqlite3.Connection) -> str:
    """Same output as: '\\n'.join(sql from sqlite_master where sql is not null)."""
    return "\n".join(
        row[0] for row in conn.execute("select sql from sqlite_master where sql is not null")
    )


def create_backup(db_path: Path | str) -> Path:
    """Create ``<db_path>.bak_<UTC>`` using the SQLite online backup API."""
    db_path = Path(db_path)
    while True:
        stamp = datetime.now(timezone.utc).strftime(BACKUP_STAMP_FORMAT)
        target = db_path.with_name(f"{db_path.name}.bak_{stamp}")
        if not target.exists():
            break
        time.sleep(1.05)  # a backup already exists for this second; take the next stamp

    src = sqlite3.connect(str(db_path), timeout=30)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
        # Keep the backup a single self-contained file (no -wal/-shm siblings).
        dst.execute("PRAGMA journal_mode = DELETE").fetchone()
        dst.commit()
        check = dst.execute("PRAGMA quick_check").fetchone()[0]
        if str(check).lower() != "ok":
            raise MigrationError(f"backup {target} failed quick_check: {check}")
    finally:
        dst.close()
        src.close()
    return target


def _count_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]


def verify_row_parity(backup_path: Path | str, migrated_path: Path | str) -> bool:
    """True iff every table in the backup's sqlite_master has the same COUNT(*) in both DBs."""
    bak = sqlite3.connect(str(backup_path), timeout=30)
    mig = sqlite3.connect(str(migrated_path), timeout=30)
    try:
        migrated_tables = set(_count_tables(mig))
        for table in _count_tables(bak):
            if table not in migrated_tables:
                return False
            q = _quote_ident(table)
            n_bak = bak.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
            n_mig = mig.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
            if n_bak != n_mig:
                return False
        return True
    finally:
        mig.close()
        bak.close()


# --------------------------------------------------------------------------- #
# Database-path discovery (static evaluation of the source files)
# --------------------------------------------------------------------------- #
_PATH_CTORS = {
    "Path", "PurePath", "WindowsPath", "PosixPath", "PureWindowsPath", "PurePosixPath",
    "pathlib.Path", "pathlib.PurePath", "pathlib.WindowsPath", "pathlib.PosixPath",
}
_STR_PASSTHROUGH = {"str", "os.fspath", "os.path.normpath", "os.path.abspath", "os.path.realpath"}
_DB_LITERAL_RE = re.compile(r"""[rRbBuU]?["']([^"'\n{}]+?\.db)["']""")
_INVALID_PATH_CHARS = set('*?<>|"\n\r\t{}')


def _dotted(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


class _PathEvaluator:
    """Evaluates the subset of Python used to build filesystem paths, without executing code."""

    def __init__(self, source: Path, tree: ast.AST) -> None:
        self.source = source
        self.assignments: dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.assignments[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                self.assignments[node.target.id] = node.value
        self._resolving: set[str] = set()

    def evaluate(self, node: ast.AST):
        try:
            return self._eval(node, 0)
        except (TypeError, ValueError, OSError, IndexError, RecursionError):
            return None

    def _eval(self, node: ast.AST, depth: int):
        if depth > 50:
            return None
        nxt = depth + 1
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return str(self.source)
            if node.id in self._resolving or node.id not in self.assignments:
                return None
            self._resolving.add(node.id)
            try:
                return self._eval(self.assignments[node.id], nxt)
            finally:
                self._resolving.discard(node.id)
        if isinstance(node, ast.BinOp):
            left, right = self._eval(node.left, nxt), self._eval(node.right, nxt)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Div):
                return Path(left) / right
            if isinstance(node.op, ast.Add) and isinstance(left, str) and isinstance(right, str):
                return left + right
            return None
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for value in node.values:
                result = self._eval(value, nxt)
                if result:
                    return result
            return None
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                base = self._eval(node.value, nxt)
                return Path(base).parent if base is not None else None
            return None
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(node, nxt)
        if isinstance(node, ast.Call):
            return self._eval_call(node, nxt)
        return None

    def _eval_subscript(self, node: ast.Subscript, depth: int):
        index_node = node.slice
        if isinstance(index_node, ast.Index):  # Python < 3.9 AST shape
            index_node = index_node.value  # type: ignore[attr-defined]
        if _dotted(node.value) == "os.environ":
            key = self._eval(index_node, depth)
            return os.environ.get(key) if isinstance(key, str) else None
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            base = self._eval(node.value.value, depth)
            if base is not None and isinstance(index_node, ast.Constant) and isinstance(index_node.value, int):
                return Path(base).parents[index_node.value]
        return None

    def _eval_call(self, node: ast.Call, depth: int):
        if any(isinstance(a, ast.Starred) for a in node.args):
            return None
        name = _dotted(node.func)
        args = node.args
        if name in _PATH_CTORS:
            values = [self._eval(a, depth) for a in args]
            if not values or any(v is None for v in values):
                return None
            return Path(*values)
        if name in _STR_PASSTHROUGH and len(args) == 1:
            value = self._eval(args[0], depth)
            return str(value) if value is not None else None
        if name in ("os.path.expanduser", "os.path.expandvars") and len(args) == 1:
            value = self._eval(args[0], depth)
            if value is None:
                return None
            return os.path.expanduser(str(value)) if name.endswith("expanduser") else os.path.expandvars(str(value))
        if name == "os.path.join":
            values = [self._eval(a, depth) for a in args]
            if not values or any(v is None for v in values):
                return None
            return os.path.join(*[str(v) for v in values])
        if name == "os.path.dirname" and len(args) == 1:
            value = self._eval(args[0], depth)
            return os.path.dirname(str(value)) if value is not None else None
        if name in ("os.environ.get", "os.getenv"):
            if not args:
                return None
            key = self._eval(args[0], depth)
            env_value = os.environ.get(key) if isinstance(key, str) else None
            if env_value:
                return env_value
            default_node = args[1] if len(args) > 1 else None
            for kw in node.keywords:
                if kw.arg == "default":
                    default_node = kw.value
            return self._eval(default_node, depth) if default_node is not None else None
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("resolve", "absolute", "expanduser"):
                base = self._eval(node.func.value, depth)
                if base is None:
                    return None
                return Path(base).expanduser() if attr == "expanduser" else Path(base)
            if attr == "joinpath":
                base = self._eval(node.func.value, depth)
                parts = [self._eval(a, depth) for a in args]
                if base is None or any(p is None for p in parts):
                    return None
                return Path(base).joinpath(*parts)
        return None


def _as_db_path(value, source_dir: Path) -> Optional[Path]:
    if value is None:
        return None
    text = str(value).strip()
    lowered = text.lower()
    if lowered.startswith("sqlite:///"):
        text = text[len("sqlite:///"):]
    elif lowered.startswith("file:"):
        text = text[len("file:"):].split("?", 1)[0]
    if not text.lower().endswith(".db") or text.lower() == ".db":
        return None
    if any(ch in _INVALID_PATH_CHARS for ch in text):
        return None
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        path = source_dir / path
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_existing_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _named_constant_fallback(source: Path, tree: ast.AST, const_name: str) -> Iterator[Path]:
    """If ``const_name`` cannot be evaluated statically, locate its .db basename next to the
    source file or one directory level below it (e.g. v3/cochem_kanban_v3.db)."""
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == const_name for t in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == const_name:
            value = node.value
        if value is None:
            continue
        for sub in ast.walk(value):
            if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
                continue
            basename = sub.value.strip().replace("\\", "/").rsplit("/", 1)[-1]
            if not basename.lower().endswith(".db") or any(c in _INVALID_PATH_CHARS for c in basename):
                continue
            candidates = [source.parent / basename]
            try:
                for child in sorted(source.parent.iterdir()):
                    if child.is_dir() and child.name not in skip_dirs and not child.name.startswith("."):
                        candidates.append(child / basename)
            except OSError:
                pass
            for cand in candidates:
                if _is_existing_file(cand):
                    yield cand.resolve()


def _candidate_db_paths(source: Path) -> Iterator[Path]:
    text = source.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for match in _DB_LITERAL_RE.finditer(text):
            path = _as_db_path(match.group(1), source.parent)
            if path is not None:
                yield path
        return

    evaluator = _PathEvaluator(source, tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        elif isinstance(node, (ast.Call, ast.BinOp, ast.BoolOp, ast.Name, ast.Subscript)):
            value = evaluator.evaluate(node)
        else:
            continue
        path = _as_db_path(value, source.parent)
        if path is not None:
            yield path

    v3_node = evaluator.assignments.get("DEFAULT_V3_DB_PATH")
    if v3_node is not None:
        v3_path = _as_db_path(evaluator.evaluate(v3_node), source.parent)
        if v3_path is None or not _is_existing_file(v3_path):
            yield from _named_constant_fallback(source, tree, "DEFAULT_V3_DB_PATH")


def discover_database_paths(
    source_files: Optional[tuple[str, ...]] = None, root: Optional[Path] = None
) -> list[Path]:
    """Existing DB files referenced by the queue producer modules; absolute, unique, sorted."""
    base = Path(root) if root is not None else REPO_ROOT
    found: dict[str, Path] = {}
    for fname in source_files or SOURCE_FILES:
        source = base / fname
        if not source.is_file():
            continue
        for candidate in _candidate_db_paths(source):
            if _is_existing_file(candidate):
                found.setdefault(os.path.normcase(str(candidate)), candidate)
    return [found[key] for key in sorted(found)]


# --------------------------------------------------------------------------- #
# Migration planning / application
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TablePlan:
    table: str
    missing_columns: tuple[str, ...]
    needs_json_check: bool
    needs_index: bool

    @property
    def needed(self) -> bool:
        return bool(self.missing_columns or self.needs_json_check or self.needs_index)


def plan_table(conn: sqlite3.Connection, table: str) -> TablePlan:
    existing = {c.lower() for c in _table_columns(conn, table)}
    missing = tuple(col for col in V4_COLUMN_NAMES if col not in existing)
    # A newly added payload column carries the CHECK in its own declaration.
    needs_check = "payload" in existing and not _has_json_check(_table_sql(conn, table))
    needs_index = _find_task_id_index(conn, table) is None
    return TablePlan(table, missing, needs_check, needs_index)


def _new_index_name(conn: sqlite3.Connection, table: str) -> str:
    base = "idx_" + re.sub(r"\W", "_", table) + "_payload_task_id"
    taken = {row[0].lower() for row in conn.execute("SELECT name FROM sqlite_master")}
    name, n = base, 2
    while name.lower() in taken:
        name = f"{base}_{n}"
        n += 1
    return name


def _add_json_check_to_existing_payload(conn: sqlite3.Connection, table: str) -> None:
    """Attach CHECK(json_valid(payload)) to a table whose payload column predates V4.

    Uses the procedure documented in the SQLite ALTER TABLE docs for constraint-only
    changes (writable_schema + schema_version bump), after proving no stored row would
    violate the new constraint. Rows are never modified.
    """
    q = _quote_ident(table)
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {q} WHERE payload IS NOT NULL AND json_valid(payload) = 0"
    ).fetchone()[0]
    if bad:
        raise MigrationError(
            f"{table}: {bad} existing row(s) hold non-JSON payload values; "
            "refusing to add CHECK(json_valid(payload)) (data repair is out of scope)"
        )
    sql = _table_sql(conn, table)
    close = sql.rfind(")")
    if close < 0:
        raise MigrationError(f"{table}: cannot parse CREATE TABLE statement: {sql!r}")
    new_sql = sql[:close].rstrip() + ", CHECK(json_valid(payload))" + sql[close:]
    version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    conn.execute("PRAGMA writable_schema = ON")
    try:
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?", (new_sql, table)
        )
        conn.execute(f"PRAGMA schema_version = {version + 1}")
    finally:
        conn.execute("PRAGMA writable_schema = OFF")


def _apply_plans(conn: sqlite3.Connection, plans: list[TablePlan]) -> None:
    legacy_before = int(conn.execute("PRAGMA legacy_alter_table").fetchone()[0])
    # ADD COLUMN re-validates every view/trigger in the schema; legacy mode keeps an
    # unrelated stale view from blocking a purely additive column change.
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        for plan in plans:
            q = _quote_ident(plan.table)
            for col in plan.missing_columns:
                conn.execute(f"ALTER TABLE {q} ADD COLUMN {col} {_V4_COLUMN_DECL[col]}")
            if plan.needs_index:
                index_name = _new_index_name(conn, plan.table)
                conn.execute(f"CREATE INDEX {_quote_ident(index_name)} ON {q} ({TASK_ID_EXPR})")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table = {'ON' if legacy_before else 'OFF'}")
    # Schema-cookie changes last, after all regular DDL in this transaction.
    for plan in plans:
        if plan.needs_json_check:
            _add_json_check_to_existing_payload(conn, plan.table)


def _verify_post_state(db_path: Path, tables: list[str], schema_changed: bool) -> list[str]:
    problems: list[str] = []
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        for table in tables:
            if not is_table_migrated(conn, table):
                problems.append(f"{table}: missing V4 columns")
            if not _has_json_check(_table_sql(conn, table)):
                problems.append(f"{table}: CHECK(json_valid(payload)) absent from DDL")
            if _find_task_id_index(conn, table) is None:
                problems.append(f"{table}: no index on {TASK_ID_EXPR}")
        mode = _journal_mode(conn)
        if mode != "wal":
            problems.append(f"journal_mode is {mode!r}, expected 'wal'")
        if schema_changed:
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
            if str(check).lower() != "ok":
                problems.append(f"quick_check failed: {check}")
    finally:
        conn.close()
    return problems


def _migrate(db: Path, json1_probe, busy_timeout_ms: int, out, err) -> int:
    conn = sqlite3.connect(str(db), timeout=max(busy_timeout_ms, 0) / 1000.0, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        if not json1_probe(conn):
            print(
                f"JSON1_UNAVAILABLE: SELECT json_valid('{{}}') did not return 1 for {db}; "
                "no changes made",
                file=err,
            )
            return EXIT_NO_JSON1

        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _is_lock_error(exc):
                print(
                    f"WRITE_LOCK_HELD: could not acquire the write lock on {db} within "
                    f"{busy_timeout_ms} ms ({exc}); no changes made",
                    file=err,
                )
                return EXIT_WRITE_LOCK_HELD
            raise

        backup_path: Optional[Path] = None
        committed = False
        try:
            live = discover_live_tables(conn)
            plans = [p for p in (plan_table(conn, t) for t in live) if p.needed]
            if plans:
                backup_path = create_backup(db)
                print(f"BACKUP {backup_path}", file=out)
                _apply_plans(conn, plans)
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed and conn.in_transaction:
                conn.execute("ROLLBACK")

        if _journal_mode(conn) != "wal":
            try:
                new_mode = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            except sqlite3.OperationalError as exc:
                if _is_lock_error(exc):
                    print(
                        f"WRITE_LOCK_HELD: schema is migrated but journal_mode=WAL on {db} was "
                        f"blocked by another connection ({exc}); rerun to finish",
                        file=err,
                    )
                    return EXIT_WRITE_LOCK_HELD
                raise
            if new_mode != "wal":
                raise MigrationError(f"journal_mode switch to WAL refused (mode={new_mode!r})")
    finally:
        conn.close()

    problems = _verify_post_state(db, live, schema_changed=bool(plans))
    if problems:
        raise MigrationError("post-migration verification failed: " + "; ".join(problems))
    if backup_path is not None:
        if not verify_row_parity(backup_path, db):
            raise MigrationError(f"ROW_PARITY_MISMATCH between {backup_path} and {db}")
        print("ROW_PARITY OK", file=out)

    if plans:
        for plan in plans:
            print(f"MIGRATED {plan.table}", file=out)
    else:
        print(f"UP_TO_DATE {db} ({len(live)} live table(s))", file=out)
    print("JOURNAL_MODE wal", file=out)
    return EXIT_OK


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_discovery_evidence(db_path: Path, evidence_dir: Path) -> list[str]:
    """Write DDL and live-table evidence for ``db_path``; returns the live tables."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        ddl = dump_ddl(conn)
        live = discover_live_tables(conn)
    finally:
        conn.close()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(evidence_dir / DDL_EVIDENCE_NAME, ddl + "\n")
    _atomic_write(evidence_dir / LIVE_TABLES_EVIDENCE_NAME, "".join(f"{t}\n" for t in live))
    return live


def _run_discover(db: Path, evidence_dir: Path, out, err) -> int:
    for path in discover_database_paths():
        print(f"DATABASE {path}", file=out)
    live = write_discovery_evidence(db, evidence_dir)
    for table in live:
        print(f"LIVE_TABLE {table}", file=out)
    if not live:
        print(f"WARNING: no live queue tables found in {db}", file=err)
    print(f"EVIDENCE {evidence_dir / DDL_EVIDENCE_NAME}", file=out)
    print(f"EVIDENCE {evidence_dir / LIVE_TABLES_EVIDENCE_NAME}", file=out)
    return EXIT_OK


def run_migration(
    db_path: Path | str,
    check_json1: Optional[Callable[[sqlite3.Connection], bool]] = None,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    discover_only: bool = False,
    *,
    check_json1_fn: Optional[Callable[[sqlite3.Connection], bool]] = None,
    evidence_dir: Optional[Path | str] = None,
    out=None,
    err=None,
) -> int:
    """Migrate (or, with discover_only, inspect) one database. Returns 0, 1, 2 or 3."""
    out = out or sys.stdout
    err = err or sys.stderr
    probe = check_json1 or check_json1_fn or _default_check_json1
    db = Path(db_path)
    if not db.is_file():
        print(f"ERROR: database file not found: {db}", file=err)
        return EXIT_ERROR
    db = db.resolve()
    try:
        if discover_only:
            target_dir = Path(evidence_dir) if evidence_dir is not None else EVIDENCE_DIR
            return _run_discover(db, target_dir, out, err)
        return _migrate(db, probe, int(busy_timeout_ms), out, err)
    except MigrationError as exc:
        print(f"MIGRATION_FAILED: {db}: {exc}", file=err)
        return EXIT_ERROR
    except (sqlite3.Error, OSError) as exc:
        print(f"ERROR: {db}: {type(exc).__name__}: {exc}", file=err)
        return EXIT_ERROR


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="V4 SQLite WAL/JSON payload migration for CoChem queues")
    parser.add_argument("--db", help="database to migrate/inspect (default: all discovered databases)")
    parser.add_argument("--discover", action="store_true", help="report databases and live tables; no schema changes")
    parser.add_argument("--busy-timeout", type=int, default=DEFAULT_BUSY_TIMEOUT_MS,
                        help="milliseconds to wait for the write lock (default 500)")
    parser.add_argument("--evidence-dir", default=None,
                        help=f"evidence output directory for --discover (default {EVIDENCE_DIR})")
    args = parser.parse_args(argv)
    if args.busy_timeout < 0:
        parser.error("--busy-timeout must be >= 0")
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else EVIDENCE_DIR

    if args.db:
        return run_migration(args.db, busy_timeout_ms=args.busy_timeout,
                             discover_only=args.discover, evidence_dir=evidence_dir)

    paths = discover_database_paths()
    if args.discover:
        for path in paths:
            print(f"DATABASE {path}")
            conn = sqlite3.connect(str(path), timeout=30)
            try:
                for table in discover_live_tables(conn):
                    print(f"LIVE_TABLE {path} {table}")
            finally:
                conn.close()
        return EXIT_OK
    if not paths:
        print("ERROR: no database files discovered in " + ", ".join(SOURCE_FILES), file=sys.stderr)
        return EXIT_ERROR
    for path in paths:
        rc = run_migration(path, busy_timeout_ms=args.busy_timeout)
        if rc != EXIT_OK:
            return rc
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
