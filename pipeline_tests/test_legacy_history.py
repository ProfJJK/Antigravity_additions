import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cochem_pipeline.legacy_history import preview_legacy_archive


def capture(tmp_path, sources=2, modern=False, limited=False):
    rows = []
    for number in range(sources):
        database = tmp_path / f'{number+1:02d}-job_board.db'
        with sqlite3.connect(database) as connection:
            connection.executescript('CREATE TABLE jobs(id INTEGER PRIMARY KEY,status TEXT,attempts INTEGER,payload_json TEXT' + ('' if limited else ',max_attempts INTEGER') + ');')
            connection.execute('INSERT INTO jobs VALUES (' + ','.join('?' * (4 if limited else 5)) + ')',
                               (1, 'RUNNING' if number == 0 else 'COMPLETED', 3, 'PRIVATE_CONTENT') + (() if limited else (4,)))
            if modern:
                connection.execute('CREATE TABLE pipeline_jobs(job_id TEXT)')
        rows.append({'source': 'C:/legacy-' + str(number) + '/job_board.db', 'snapshot': database.name,
                     'bytes': database.stat().st_size, 'sha256': hashlib.sha256(database.read_bytes()).hexdigest(), 'integrity_check': 'ok'})
    manifest = tmp_path / 'snapshot-manifest.json'
    manifest.write_text(json.dumps({'schema': 'cochem-legacy-online-snapshots/1', 'status': 'COMPLETE_PER_DATABASE',
        'created_at_utc': '2026-10-07T02:44:05Z', 'snapshots': rows}), encoding='utf8')
    return manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_overlapping_primary_keys_preserve_attempts_without_queue_or_payload(tmp_path):
    manifest, pin = capture(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = preview_legacy_archive(manifest, pin)
    a, b = [item['rows'][0] for item in result['snapshots']]
    assert a['reference'] != b['reference']
    assert a['original']['attempts'] == b['original']['attempts'] == 3
    assert a['original']['max_attempts'] == 4
    assert a['disposition'] == 'LEGACY_IMPORT_HOLD'
    assert b['disposition'] == 'HISTORICAL_COMPLETION_ONLY'
    assert not a['executable'] and not b['counts_as_current_acceptance']
    assert result['modern_jobs_created'] == 0
    assert 'PRIVATE_CONTENT' not in json.dumps(result)
    assert result['repair_budget_authority'] == 'UNRESOLVED_LEGACY_AUTHORITY'
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_missing_attempt_limit_remains_unknown(tmp_path):
    result = preview_legacy_archive(*capture(tmp_path, limited=True))
    assert result['snapshots'][0]['rows'][0]['original']['max_attempts'] is None


def test_modern_database_is_not_legacy_import(tmp_path):
    with pytest.raises(ValueError, match='modern'):
        preview_legacy_archive(*capture(tmp_path, modern=True))


@pytest.mark.parametrize('sidecar', ['-wal', '-shm', '-journal'])
def test_live_sidecar_rejected_before_queries(tmp_path, sidecar):
    manifest, pin = capture(tmp_path)
    Path(str(tmp_path / '01-job_board.db') + sidecar).write_bytes(b'preserve')
    with pytest.raises(ValueError, match='sidecars'):
        preview_legacy_archive(manifest, pin)


def test_snapshot_drift_rejected(tmp_path):
    manifest, pin = capture(tmp_path)
    with (tmp_path / '01-job_board.db').open('ab') as output:
        output.write(b'changed')
    with pytest.raises(ValueError, match='changed'):
        preview_legacy_archive(manifest, pin)


def test_manifest_drift_rejected(tmp_path):
    manifest, pin = capture(tmp_path)
    manifest.write_bytes(manifest.read_bytes() + b' ')
    with pytest.raises(ValueError, match='changed'):
        preview_legacy_archive(manifest, pin)


def test_row_bound_refuses_truncated_continuity(tmp_path):
    manifest, _ = capture(tmp_path, sources=1)
    with sqlite3.connect(tmp_path / '01-job_board.db') as connection:
        connection.execute("INSERT INTO jobs VALUES(2,'PENDING',0,'secret',2)")
    value = json.loads(manifest.read_text())
    database = tmp_path / '01-job_board.db'
    value['snapshots'][0]['sha256'] = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='row bound'):
        preview_legacy_archive(manifest, hashlib.sha256(manifest.read_bytes()).hexdigest(), maximum_jobs=1)


def test_duplicate_snapshot_name_rejected(tmp_path):
    manifest, _ = capture(tmp_path)
    value = json.loads(manifest.read_text()); value['snapshots'][1]['snapshot'] = value['snapshots'][0]['snapshot']
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='path/identity'):
        preview_legacy_archive(manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())


def test_unknown_status_is_held(tmp_path):
    manifest, _ = capture(tmp_path, sources=1)
    with sqlite3.connect(tmp_path / '01-job_board.db') as connection:
        connection.execute("UPDATE jobs SET status='DONE_MAYBE'")
    value = json.loads(manifest.read_text())
    value['snapshots'][0]['sha256'] = hashlib.sha256((tmp_path/'01-job_board.db').read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    result = preview_legacy_archive(manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())
    assert result['snapshots'][0]['rows'][0]['disposition'] == 'LEGACY_IMPORT_HOLD'


def test_fts_engine_integrity_does_not_select_private_document_text(tmp_path):
    manifest, _ = capture(tmp_path, sources=1)
    database = tmp_path/'01-job_board.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE VIRTUAL TABLE fts_index USING fts5(text)')
        connection.execute("INSERT INTO fts_index VALUES('PRIVATE_DOCUMENT')")
    value = json.loads(manifest.read_text())
    value['snapshots'][0].update(sha256=hashlib.sha256(database.read_bytes()).hexdigest(), bytes=database.stat().st_size)
    manifest.write_text(json.dumps(value))
    before = database.read_bytes()
    result = preview_legacy_archive(manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())
    assert result['snapshots'][0]['integrity_check'] == 'ok'
    assert 'PRIVATE_DOCUMENT' not in json.dumps(result)
    assert database.read_bytes() == before


def test_swap_then_restore_cannot_substitute_rows_under_original_hash(tmp_path, monkeypatch):
    from cochem_pipeline import legacy_history
    manifest, pin = capture(tmp_path, sources=1)
    original_inspect = legacy_history._inspect
    def swapping(path, raw, *args, **kwargs):
        before = path.read_bytes()
        try:
            with sqlite3.connect(path) as writer:
                writer.execute("UPDATE jobs SET status='COMPLETED',attempts=0")
            return original_inspect(path, raw, *args, **kwargs)
        finally:
            path.write_bytes(before)
    monkeypatch.setattr(legacy_history, '_inspect', swapping)
    result = preview_legacy_archive(manifest, pin)
    row = result['snapshots'][0]['rows'][0]
    assert row['original']['status'] == 'RUNNING' and row['original']['attempts'] == 3
    assert row['disposition'] == 'LEGACY_IMPORT_HOLD'


def test_closed_wal_backup_header_normalization_only_changes_memory(tmp_path):
    manifest, _ = capture(tmp_path, sources=1)
    database = tmp_path/'01-job_board.db'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    assert database.read_bytes()[18:20] == b'\2\2'
    value = json.loads(manifest.read_text())
    value['snapshots'][0]['sha256'] = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    before = database.read_bytes()
    result = preview_legacy_archive(manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())
    assert result['snapshots'][0]['in_memory_wal_header_normalized']
    assert result['snapshots'][0]['rows'][0]['original']['attempts'] == 3
    assert database.read_bytes() == before
