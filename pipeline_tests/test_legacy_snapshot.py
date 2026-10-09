"""Actual SQLite online backup checks, including committed WAL and open writers."""
from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from scripts.backup_legacy_windows_state import snapshot_databases


def test_live_wal_snapshot_preserves_committed_state_without_switch_or_budget_reset(tmp_path):
    source = tmp_path/'old.db'
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE budget(id INTEGER PRIMARY KEY, charged INTEGER)')
        writer.execute('INSERT INTO budget VALUES(1,7)')
        writer.commit()
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE budget SET charged=8')
        report = snapshot_databases([source],tmp_path/'snapshots')
        snapshot = tmp_path/'snapshots'/report['snapshots'][0]['snapshot']
        with closing(sqlite3.connect(snapshot)) as reader:
            assert reader.execute('SELECT charged FROM budget').fetchone() == (7,)
        writer.rollback()
        assert writer.execute('SELECT charged FROM budget').fetchone() == (7,)
    assert report['production_state_switched'] is False
    assert report['repair_budget_reconstructed'] is False
    assert report['snapshots'][0]['sha256'] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    # Real Windows rename proves all connections and digest streams were closed.
    snapshot.rename(snapshot.with_suffix('.closed.db'))
    source.rename(source.with_suffix('.closed.db'))


def test_existing_destination_and_duplicate_inputs_are_preserved(tmp_path):
    source=tmp_path/'source.db'
    with closing(sqlite3.connect(source)) as writer:
        writer.execute('CREATE TABLE jobs(id INTEGER)')
    destination=tmp_path/'existing'
    destination.mkdir()
    sentinel=destination/'keep.txt'
    sentinel.write_text('preserve')
    with pytest.raises(ValueError,match='new absolute'):
        snapshot_databases([source],destination)
    with pytest.raises(ValueError,match='Duplicate'):
        snapshot_databases([source,source],tmp_path/'new')
    assert sentinel.read_text() == 'preserve'
    assert not (tmp_path/'new').exists()


def test_invalid_database_retains_incomplete_manifest_and_original(tmp_path):
    source=tmp_path/'bad.db'
    source.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_databases([source],tmp_path/'failed')
    report=json.loads((tmp_path/'failed/snapshot-manifest.json').read_text())
    assert report['status'] == 'INCOMPLETE_PRESERVED'
    assert report['snapshots'] == []
    assert source.read_bytes() == b'not a SQLite database'
