"""
SQLite Kanban Schema Migration 001
====================================
Adds LLM provider columns to the Kanban tasks table.
Idempotent — safe to run multiple times. Uses WAL mode and busy timeout.

Run: python migrations/001_add_llm_provider_columns.py
"""

import sqlite3
import sys
import argparse
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "cochem_kanban.db"


NEW_COLUMNS = [
    ("assigned_provider", "TEXT DEFAULT 'gemini'"),
    ("input_tokens",      "INTEGER"),
    ("output_tokens",     "INTEGER"),
    ("llm_model",         "TEXT"),
]


def get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cur.fetchall()}


def get_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return [row[0] for row in cur.fetchall()]


def migrate(db_path: Path, dry_run: bool = False) -> None:
    print(f"[migration-001] Target DB: {db_path}")
    if not db_path.exists():
        print(f"[migration-001] ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")

        tables = get_tables(conn)
        print(f"[migration-001] Tables found: {tables}")

        # Target the tasks table (or the only table present)
        target_tables = [t for t in tables if "task" in t.lower() or "kanban" in t.lower()]
        if not target_tables:
            target_tables = tables  # fall back to all tables

        for table in target_tables:
            existing = get_existing_columns(conn, table)
            print(f"[migration-001] Table '{table}' — existing columns: {existing}")
            for col_name, col_def in NEW_COLUMNS:
                if col_name in existing:
                    print(f"  [SKIP] Column '{col_name}' already exists in '{table}'.")
                    continue
                sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def};"
                if dry_run:
                    print(f"  [DRY-RUN] Would execute: {sql}")
                else:
                    conn.execute(sql)
                    print(f"  [ADDED] {sql}")

        if not dry_run:
            conn.commit()
            print("[migration-001] Migration committed successfully.")
        else:
            print("[migration-001] Dry-run complete. No changes written.")

        # Verify
        for table in target_tables:
            final_cols = get_existing_columns(conn, table)
            added = [c for c, _ in NEW_COLUMNS if c in final_cols]
            print(f"[migration-001] Final columns present in '{table}': {added}")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kanban schema migration 001 — add LLM provider columns")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to cochem_kanban.db")
    parser.add_argument("--dry-run", action="store_true", help="Show SQL without executing")
    args = parser.parse_args()

    migrate(Path(args.db), dry_run=args.dry_run)
