"""Database initializer and connection factory for CoChem Pipeline v2.

CLI exit-code contract:
    0   success (idempotent; re-running on an initialised DB is safe)
    10  invalid CLI arguments
    11  schema file not found
    12  sqlite3.Error during execution
    13  any other unhandled exception
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

DEFAULT_DB_NAME = "cochem_kanban_v2.db"
DEFAULT_SCHEMA_NAME = "schema_v2.sql"

EXIT_OK = 0
EXIT_BAD_ARGS = 10
EXIT_SCHEMA_NOT_FOUND = 11
EXIT_SQLITE_ERROR = 12
EXIT_UNHANDLED = 13


def open_connection(db_path: str | Path, timeout: float = 0.0) -> sqlite3.Connection:
    """Open SQLite connection with WAL, foreign keys enabled, and busy_timeout=5000.

    The sqlite3 ``timeout`` defaults to 0.0 so the effective busy handler is the
    one installed by ``PRAGMA busy_timeout=5000``. busy_timeout is installed
    first, immediately after connecting, so every subsequent statement --
    including the ``PRAGMA journal_mode=WAL`` switch -- honours the 5 s busy
    handler instead of failing instantly with "database is locked". Raises
    sqlite3.OperationalError (after closing the connection) if WAL journal mode
    could not be enabled; the connection is closed on any failure.
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(row[0]).lower() if row else ""
        if mode != "wal":
            raise sqlite3.OperationalError(
                f"failed to enable WAL journal mode on {db_path} (got {mode!r})"
            )
        conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        conn.close()
        raise
    return conn


def init_db(db_path: str | Path, schema_path: str | Path | None = None) -> Path:
    """Apply schema_v2.sql to db_path in WAL mode and return the resolved db path."""
    target_db = Path(db_path).resolve()

    if schema_path is None:
        resolved_schema = Path(__file__).resolve().parent / DEFAULT_SCHEMA_NAME
    else:
        resolved_schema = Path(schema_path).resolve()

    if not resolved_schema.is_file():
        raise FileNotFoundError(f"Schema file not found at {resolved_schema}")

    with open(resolved_schema, "r", encoding="utf-8") as fh:
        schema_sql = fh.read()

    target_db.parent.mkdir(parents=True, exist_ok=True)

    conn = open_connection(target_db)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    return target_db


get_connection = open_connection
init_database = init_db


class _CliArgumentError(Exception):
    """Raised for CLI argument combinations argparse cannot express."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize CoChem Pipeline v2 SQLite database.")
    parser.add_argument(
        "--db-path",
        dest="db_path",
        type=str,
        default=None,
        help="Path to target SQLite database file.",
    )
    parser.add_argument(
        "--schema-path",
        dest="schema_path",
        type=str,
        default=None,
        help="Path to schema SQL file.",
    )
    parser.add_argument(
        "positional_path",
        nargs="?",
        type=str,
        default=None,
        help="Target database path or directory (optional; directory -> cochem_kanban_v2.db).",
    )
    return parser


def _resolve_target(args: argparse.Namespace) -> Path:
    if args.db_path and args.positional_path:
        raise _CliArgumentError("use either --db-path or a positional path, not both")
    db_path_arg = args.db_path or args.positional_path
    if not db_path_arg:
        return Path(__file__).resolve().parent / DEFAULT_DB_NAME
    candidate = Path(db_path_arg)
    if candidate.is_dir() or db_path_arg.endswith(("\\", "/")):
        return candidate / DEFAULT_DB_NAME
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            return EXIT_OK
        return EXIT_BAD_ARGS

    try:
        db_path = _resolve_target(args)
        resolved = init_db(db_path, schema_path=args.schema_path)
    except _CliArgumentError as exc:
        print(f"init_db: invalid arguments: {exc}", file=sys.stderr)
        return EXIT_BAD_ARGS
    except FileNotFoundError as exc:
        print(f"init_db: schema not found: {exc}", file=sys.stderr)
        return EXIT_SCHEMA_NOT_FOUND
    except sqlite3.Error as exc:
        print(f"init_db: sqlite error: {exc}", file=sys.stderr)
        return EXIT_SQLITE_ERROR
    except Exception as exc:  # noqa: BLE001 - exit-code contract requires a catch-all
        print(f"init_db: unhandled error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UNHANDLED

    print(f"init_db: initialised {resolved}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
