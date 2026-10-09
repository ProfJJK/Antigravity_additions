"""Discovery must neither change legacy state nor project private payloads."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from scripts import discover_legacy_windows_state as discovery


def legacy_db(path):
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE jobs (status TEXT, attempts INTEGER, payload_json TEXT)')
    connection.executemany('INSERT INTO jobs VALUES (?, ?, ?)', [
        ('RUNNING', 3, 'PRIVATE-PAYLOAD-DO-NOT-EXPOSE'),
        ('PENDING', 2, 'PRIVATE-PAYLOAD-DO-NOT-EXPOSE')])
    connection.commit()
    return connection


def test_read_only_preserves_database_and_never_projects_payload(tmp_path):
    path = tmp_path / 'job_board.db'
    legacy_db(path).close()
    before = path.read_bytes()
    report = discovery.inspect_database(path)
    assert path.read_bytes() == before
    assert report['main_file_metadata_unchanged_during_inspection']
    assert report['tables']['jobs']['rows'] == 2
    assert sum(row['recorded_attempts_sum'] for row in report['jobs_by_status']) == 5
    assert 'PRIVATE-PAYLOAD' not in json.dumps(report)
    assert report['modern_supervisor_compatible'] is False
    assert report['modern_component_compatible'] is False
    assert report['repair_budget_remaining'] is None


def test_missing_database_cannot_be_created(tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(FileNotFoundError):
        discovery.inspect_database(path)
    assert not path.exists()


def test_copied_knowledge_index_exposes_old_owner_without_document_text(tmp_path):
    root = tmp_path / 'v4.2.0'
    root.mkdir()
    path = root / 'knowledge_index.db'
    old_prefix = tmp_path / 'v4.1.2/wiki'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE documents (doc_path TEXT, title TEXT)')
        connection.executemany('INSERT INTO documents VALUES (?, ?)', [
            (str(old_prefix / 'one.md'), 'PRIVATE-TITLE'),
            ('Z:/unmapped/PRIVATE-NAME.md', 'PRIVATE-TITLE')])
    report = discovery.inspect_database(path)
    assert report['knowledge_path_prefix_counts'][0]['documents'] == 1
    assert report['knowledge_other_path_count'] == 1
    assert 'PRIVATE-' not in json.dumps(report)


def test_reads_committed_wal_without_checkpoint_or_discard(tmp_path):
    path = tmp_path / 'job_board.db'
    connection = legacy_db(path)
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('INSERT INTO jobs VALUES (?, ?, ?)', ('COMPLETED', 7, 'PRIVATE-WAL-PAYLOAD'))
        connection.commit()
        wal = Path(str(path) + '-wal')
        before = wal.read_bytes()
        report = discovery.inspect_database(path)
        assert report['tables']['jobs']['rows'] == 3
        assert wal.read_bytes() == before
        assert 'PRIVATE-WAL' not in json.dumps(report)
    finally:
        connection.close()


def test_duplicates_and_incomplete_receipts_do_not_reset_budgets(tmp_path):
    roots = [tmp_path / 'old', tmp_path / 'new']
    receipt = {'recovery_launch': {'outcome': 'LAUNCHED', 'timestamp': '2026-10-03T00:00:00Z',
                                  'command': 'PRIVATE-COMMAND'}, 'payload': 'PRIVATE-PAYLOAD'}
    for root in roots:
        bundle = root / '.evidence/watchdog/bundles/20261003T000000Z'
        bundle.mkdir(parents=True)
        (bundle / 'recovery_launch.json').write_text(json.dumps(receipt), encoding='utf-8')
    broken = roots[1] / '.evidence/watchdog/bundles/20261004T000000Z'
    broken.mkdir()
    (broken / 'recovery_launch.json').write_text('{malformed PRIVATE-PAYLOAD', encoding='utf-8')
    report = discovery.discover(roots)
    assert report['launch_deduplication']['distinct_launched_receipts'] == 1
    assert report['launch_deduplication']['identical_copies_not_additional_attempts'] == 1
    assert report['roots'][1]['launch_receipt_errors'][0]['error_type'] == 'JSONDecodeError'
    assert report['absence_is_zero_spend'] is False
    assert report['repair_budget_remaining'] is None
    assert report['migration_executed'] is False
    assert 'PRIVATE-' not in json.dumps(report)


@pytest.mark.parametrize('relative', ['report.json', 'subdir/../report.json'])
def test_report_cannot_be_written_inside_legacy_root(tmp_path, monkeypatch, relative):
    monkeypatch.setattr(sys, 'argv', ['discover', '--root', str(tmp_path), '--output', str(tmp_path / relative)])
    with pytest.raises(ValueError, match='legacy source tree'):
        discovery.main()
    assert not (tmp_path / 'report.json').exists()


def test_existing_report_is_preserved(tmp_path, monkeypatch):
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    report = tmp_path / 'report.json'
    report.write_text('previous evidence', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['discover', '--root', str(legacy), '--output', str(report)])
    with pytest.raises(ValueError, match='Preserve the existing'):
        discovery.main()
    assert report.read_text(encoding='utf-8') == 'previous evidence'
