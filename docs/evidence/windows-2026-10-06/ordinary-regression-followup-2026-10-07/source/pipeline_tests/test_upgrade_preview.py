"""Upgrade preview uses actual files/SQLite and leaves captured state untouched."""
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from cochem_pipeline.store import JobStore
from cochem_pipeline.upgrade_preview import upgrade_preview
from pipeline_tests.test_config import config_document


def configs(tmp_path):
    old = config_document(tmp_path)
    current = tmp_path / 'current.json'
    proposed = tmp_path / 'proposed.json'
    current.write_text(json.dumps(old))
    new = json.loads(json.dumps(old))
    new['workers']['slot-0']['name'] = 'NewWorkerIdentity'
    new['providers']['claude']['executable'] = 'new-claude.exe'
    proposed.write_text(json.dumps(new))
    return current, proposed, old


def test_actual_preview_lists_exact_changes_and_keeps_database_immutable(tmp_path):
    current, proposed, raw = configs(tmp_path)
    store = JobStore(tmp_path / 'private' / 'job_board.db')
    workflow = store.submit('Captured original objective', ['REQ-original'], 2)
    with sqlite3.connect(store.path) as conn:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    files = {path: path.read_bytes() for path in (current, proposed)}
    result = upgrade_preview(current, proposed)
    assert result['read_only'] is True and result['native_models_executed'] is False
    identity = next(item for item in result['identities'] if item['key'] == 'slot-0')
    assert identity['changed'] is True
    assert identity['before']['name'] == raw['workers']['slot-0']['name']
    assert identity['after']['name'] == 'NewWorkerIdentity'
    executable = next(item for item in result['protected_paths'] if item['key'] == 'provider_executable.claude')
    assert executable['after'] == 'new-claude.exe'
    assert result['database']['schema_changes'] == []
    captured = result['database']['workflows']
    assert [row['workflow_id'] for row in captured] == [workflow['workflow_id']]
    assert captured[0]['automatic_rewrite'] is False
    assert result['database']['actual_schema_sha256'] == result['database']['proposed_schema_sha256']
    assert result['ready_to_deploy'] is False
    assert all(row['verified'] is False for row in result['rollback_prerequisites'])
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    assert {path: path.read_bytes() for path in files} == files


def test_preview_does_not_create_missing_database_and_has_real_cli(tmp_path):
    current, proposed, _ = configs(tmp_path)
    result = upgrade_preview(current, proposed)
    assert result['database']['exists'] is False
    assert not (tmp_path / 'private').exists()
    command = [sys.executable, '-m', 'cochem_pipeline', 'upgrade-preview',
               '--current-config', str(current), '--proposed-config', str(proposed)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['database']['workflows'] == []
    assert not (tmp_path / 'private').exists()


def test_preview_refuses_linked_config_and_unrelated_database(tmp_path):
    current, proposed, _ = configs(tmp_path)
    linked = tmp_path / 'linked.json'
    from pipeline_tests.windows_test_context import create_optional_symlink
    create_optional_symlink(linked, current)
    with pytest.raises(RuntimeError, match='reparse point or symlink'):
        upgrade_preview(linked, proposed)
    private = tmp_path / 'private'
    private.mkdir()
    with sqlite3.connect(private / 'job_board.db') as conn:
        conn.execute('CREATE TABLE unrelated (value TEXT)')
    with pytest.raises(ValueError, match='not the pipeline job board'):
        upgrade_preview(current, proposed)


def test_schema_difference_is_reported_without_applying_migration(tmp_path):
    current, proposed, _ = configs(tmp_path)
    store = JobStore(tmp_path / 'private' / 'job_board.db')
    with sqlite3.connect(store.path) as conn:
        conn.execute('CREATE TABLE obsolete_extra (value TEXT)')
        conn.execute('PRAGMA user_version=42')
    result = upgrade_preview(current, proposed)
    assert result['database']['user_version'] == 42
    assert result['database']['migration_executed'] is False
    assert any(item['object'] == 'table:obsolete_extra' for item in result['database']['schema_changes'])
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 42
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name='obsolete_extra'").fetchone()
