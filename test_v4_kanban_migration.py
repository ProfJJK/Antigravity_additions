"""Root-level entry point for the Task 196.08 and 196.09 migration & kanban core suites.

The spec names this file (AC-51 and AC-29..34, evidence command
`pytest test_v4_kanban_migration.py -k test_kanban`). The tests live in the
read-only TDD modules tests/tdd/test_task_196_08.py and tests/tdd/test_task_196_09.py.
This file loads those modules and exposes their test functions so there
is a single source of truth.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TDD_DIR = REPO_ROOT / "tests" / "tdd"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(TDD_DIR) not in sys.path:
    sys.path.insert(0, str(TDD_DIR))

# Ensure child spawned processes inherit REPO_ROOT and TDD_DIR on PYTHONPATH
_current_pythonpath = os.environ.get("PYTHONPATH", "")
_needed_paths = [str(TDD_DIR), str(REPO_ROOT)]
_existing_paths = _current_pythonpath.split(os.pathsep) if _current_pythonpath else []
_combined = [p for p in _needed_paths if p not in _existing_paths] + _existing_paths
os.environ["PYTHONPATH"] = os.pathsep.join(_combined)

# --- Task 196.08 migration tests ---
_TDD_08_PATH = TDD_DIR / "test_task_196_08.py"
_spec_08 = importlib.util.spec_from_file_location("_cochem_tdd_task_196_08", _TDD_08_PATH)
_tdd_08 = importlib.util.module_from_spec(_spec_08)
sys.modules[_spec_08.name] = _tdd_08
_spec_08.loader.exec_module(_tdd_08)

test_migration_discovery = _tdd_08.test_migration_discovery
test_sqlite_json1_preflight = _tdd_08.test_sqlite_json1_preflight
test_migration_idempotency = _tdd_08.test_migration_idempotency
test_migration_refuses_write_lock = _tdd_08.test_migration_refuses_write_lock
test_migration_row_parity = _tdd_08.test_migration_row_parity
test_live_tables_pragmas = _tdd_08.test_live_tables_pragmas
test_migration_discovers_all_db_paths = _tdd_08.test_migration_discovers_all_db_paths
test_live_table_classification_leaves_archives_and_views_untouched = (
    _tdd_08.test_live_table_classification_leaves_archives_and_views_untouched
)

# --- Task 196.09 typed queue & atomic transactions tests ---
import test_task_196_09 as _tdd_09  # noqa: E402

test_exception_types_exported = _tdd_09.test_exception_types_exported
test_connect_uses_autocommit_isolation = _tdd_09.test_connect_uses_autocommit_isolation
test_connect_ignores_auxiliary_tables = _tdd_09.test_connect_ignores_auxiliary_tables
test_connect_rejects_unmigrated_queue_table = _tdd_09.test_connect_rejects_unmigrated_queue_table
test_kanban_rejects_invalid_json = _tdd_09.test_kanban_rejects_invalid_json
test_kanban_enqueue_execution_chunk = _tdd_09.test_kanban_enqueue_execution_chunk
test_enqueue_autodetects_queue_table = _tdd_09.test_enqueue_autodetects_queue_table
test_enqueue_respects_provider_status_and_table_name = _tdd_09.test_enqueue_respects_provider_status_and_table_name
test_get_form_legacy_row = _tdd_09.test_get_form_legacy_row
test_get_form_missing_task_raises_keyerror = _tdd_09.test_get_form_missing_task_raises_keyerror
test_get_form_returns_model_when_schema_matches = _tdd_09.test_get_form_returns_model_when_schema_matches
test_get_form_schema_drift = _tdd_09.test_get_form_schema_drift
test_kanban_wal_concurrent_writers = _tdd_09.test_kanban_wal_concurrent_writers
test_kanban_claim_begin_immediate = _tdd_09.test_kanban_claim_begin_immediate
test_claim_task_already_claimed_returns_false = _tdd_09.test_claim_task_already_claimed_returns_false
test_claim_task_is_exclusive_across_processes = _tdd_09.test_claim_task_is_exclusive_across_processes
