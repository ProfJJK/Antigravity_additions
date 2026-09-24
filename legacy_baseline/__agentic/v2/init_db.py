"""Database initializer and connection factory for CoChem Pipeline v2."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

DEFAULT_DB_NAME = "cochem_kanban_v2.db"
DEFAULT_SCHEMA_NAME = "schema_v2.sql"


def open_connection(db_path: str | Path, timeout: float = 5.0) -> sqlite3.Connection:
    """Open SQLite connection with WAL, foreign keys enabled, and busy_timeout=5000."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path, schema_path: str | Path | None = None) -> Path:
    """Apply schema_v2.sql to db_path in WAL mode and return the resolved db path."""
    target_db = Path(db_path).resolve()
    target_db.parent.mkdir(parents=True, exist_ok=True)

    if schema_path is None:
        schema_path = Path(__file__).resolve().parent / DEFAULT_SCHEMA_NAME
    else:
        schema_path = Path(schema_path).resolve()

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as fh:
        schema_sql = fh.read()

    conn = open_connection(target_db, timeout=5.0)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    return target_db


get_connection = open_connection
init_database = init_db


def main(argv: list[str] | None = None) -> int:
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
        help="Target database path or directory (optional).",
    )

    args = parser.parse_args(argv)

    db_path_arg = args.db_path or args.positional_path
    if not db_path_arg:
        db_path = Path(__file__).resolve().parent / DEFAULT_DB_NAME
    else:
        candidate = Path(db_path_arg)
        if candidate.is_dir() or db_path_arg.endswith(("\\", "/")):
            db_path = candidate / DEFAULT_DB_NAME
        else:
            db_path = candidate

    init_db(db_path, schema_path=args.schema_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
