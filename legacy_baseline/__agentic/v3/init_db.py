"""
Database initialization for CoChem Pipeline 3.0.0.
Creates and initializes cochem_kanban_v3.db from schema_v3.sql with PRAGMA journal_mode=WAL,
PRAGMA busy_timeout=5000, and PRAGMA foreign_keys=ON.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


def init_database(db_path: Path | str, schema_path: Path | str) -> Path:
    db_path = Path(db_path).resolve()
    schema_path = Path(schema_path).resolve()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    return db_path


def main() -> int:
    default_dir = Path(__file__).resolve().parent
    default_db = default_dir / "cochem_kanban_v3.db"
    default_schema = default_dir / "schema_v3.sql"

    parser = argparse.ArgumentParser(description="Initialize CoChem Pipeline 3.0.0 database")
    parser.add_argument("--db-path", default=str(default_db), help="Path to target SQLite database file")
    parser.add_argument("--schema-path", default=str(default_schema), help="Path to schema_v3.sql file")

    args = parser.parse_args()

    try:
        init_database(args.db_path, args.schema_path)
        print(f"Database successfully initialized at {args.db_path}")
        return 0
    except Exception as exc:
        print(f"Error initializing database: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
