"""MC-HW-55 acceptance checks against real SQLite files and connections."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from cochem.warden.ladder import emit_recovery_event


def test_emit_recovery_event_execution_and_row_id(tmp_path: Path) -> None:
    database = tmp_path / "default.db"
    environment = dict(os.environ, COCHEM_JOB_DB=str(database))
    program = (
        "from cochem.warden.ladder import emit_recovery_event\n"
        "print(emit_recovery_event({'tier': 1, 'action': 'test'}))\n"
        "print(emit_recovery_event(event_data={'tier': 2, 'action': 'keyword'}))\n"
        "print(emit_recovery_event())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], env=environment, check=True,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    row_ids = [int(line) for line in result.stdout.splitlines()]
    assert row_ids == [1, 2, 3]
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT id, tier, action, details FROM recovery_telemetry ORDER BY id").fetchall()
        assert rows == [
            (1, 1, "test", '{"tier": 1, "action": "test"}'),
            (2, 2, "keyword", '{"tier": 2, "action": "keyword"}'),
            (3, 1, "unspecified_recovery", "{}"),
        ]
    connection.close()


def test_recovery_telemetry_schema_definition(tmp_path: Path) -> None:
    database = tmp_path / "schema.db"
    earliest = datetime.now(timezone.utc).replace(microsecond=0)
    emit_recovery_event(database, {})
    latest = datetime.now(timezone.utc)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA table_info(recovery_telemetry)").fetchall() == [
            (0, "id", "INTEGER", 0, None, 1),
            (1, "timestamp", "TEXT", 1, "datetime('now')", 0),
            (2, "tier", "INTEGER", 1, None, 0),
            (3, "action", "TEXT", 1, None, 0),
            (4, "details", "TEXT", 1, None, 0),
        ]
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='recovery_telemetry'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in definition.upper()
        timestamp = connection.execute("SELECT timestamp FROM recovery_telemetry").fetchone()[0]
        assert earliest <= datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc) <= latest
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    connection.close()


@pytest.mark.parametrize("use_string", [False, True])
def test_emit_recovery_event_connection_injection_and_lifecycle(tmp_path: Path, use_string: bool) -> None:
    database = tmp_path / "lifecycle.db"
    path = str(database) if use_string else database
    assert emit_recovery_event(path, {"tier": 2}) == 1
    connection = sqlite3.connect(database)
    try:
        assert emit_recovery_event(conn=connection, event_data={"tier": 3}) == 2
        assert not connection.in_transaction
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT COUNT(*) FROM recovery_telemetry").fetchone()[0] == 2
        with sqlite3.connect(database) as independent_reader:
            assert independent_reader.execute("SELECT COUNT(*) FROM recovery_telemetry").fetchone()[0] == 2
        independent_reader.close()
    finally:
        connection.close()
    database.unlink()
    assert not database.exists()


@pytest.mark.parametrize("event", [
    {},
    {"tier": None, "action": None, "custom_uuid": "xyz"},
    {"tier": "2", "action": 42, "message": "恢复: 温度 Δ", "nested": {"valid": True, "items": [1, None]}},
])
def test_emit_recovery_event_payload_serialization_and_defaults(event: dict, tmp_path: Path) -> None:
    before = deepcopy(event)
    database = tmp_path / "metadata.db"
    emit_recovery_event(database, event)
    assert event == before
    with sqlite3.connect(database) as connection:
        tier, action, details = connection.execute("SELECT tier, action, details FROM recovery_telemetry").fetchone()
    connection.close()
    assert tier == (int(event["tier"]) if event.get("tier") is not None else 1)
    assert action == (str(event["action"]) if event.get("action") is not None else "unspecified_recovery")
    assert json.loads(details) == before
    if "message" in event:
        assert event["message"] in details


def test_emit_recovery_event_sequential_multi_tier_logging(tmp_path: Path) -> None:
    database = tmp_path / "tiers.db"
    row_ids = [emit_recovery_event(database, {"tier": tier}) for tier in (1, 2, 3)]
    assert 0 < row_ids[0] < row_ids[1] < row_ids[2]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT tier FROM recovery_telemetry ORDER BY id").fetchall() == [(1,), (2,), (3,)]
        connection.execute("DELETE FROM recovery_telemetry WHERE id=?", (row_ids[-1],))
    connection.close()
    assert emit_recovery_event(database, {"tier": 1}) > row_ids[-1]


def test_in_memory_connection_is_supported_and_remains_open() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        row_id = emit_recovery_event(connection, {"tier": None})
        assert connection.execute("SELECT tier FROM recovery_telemetry WHERE id=?", (row_id,)).fetchone() == (1,)
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_sql_failure_closes_owned_connection_and_preserves_injected_connection(tmp_path: Path) -> None:
    database = tmp_path / "invalid_schema.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE recovery_telemetry (id INTEGER)")
        connection.commit()
        with pytest.raises(sqlite3.OperationalError):
            emit_recovery_event(connection, {"tier": 2})
        assert connection.execute("SELECT COUNT(*) FROM recovery_telemetry").fetchone()[0] == 0
        assert not connection.in_transaction
    finally:
        connection.close()
    with pytest.raises(sqlite3.OperationalError):
        emit_recovery_event(database, {})
    database.unlink()
    assert not database.exists()


def test_invalid_metadata_does_not_create_or_change_database(tmp_path: Path) -> None:
    database = tmp_path / "invalid_payload.db"
    with pytest.raises(TypeError):
        emit_recovery_event(database, {"unsupported": object()})
    assert not database.exists()
    with pytest.raises(ValueError):
        emit_recovery_event("", {})
    with pytest.raises(TypeError):
        emit_recovery_event(False, {})


def test_ladder_zero_mock_anti_spoof_compliance() -> None:
    import cochem.warden.ladder as ladder

    source = Path(ladder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_names = {"NotImplementedError", "mock", "MagicMock", "monkeypatch"}
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Pass)
        if isinstance(node, ast.Name):
            assert node.id not in forbidden_names
        elif isinstance(node, ast.Attribute):
            assert node.attr not in forbidden_names
        elif isinstance(node, ast.Import):
            assert all(not forbidden_names.intersection(alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not forbidden_names.intersection((node.module or "").split("."))
            assert all(alias.name not in forbidden_names for alias in node.names)
