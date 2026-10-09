"""Actual SQLite metadata projection and physical PEP 657 crash envelopes."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from cochem_pipeline.crash import record_crash
from cochem_supervisor.diagnostics import capture_bundle,read_crash
from cochem_supervisor.monitor import read_observation
from supervisor_tests.test_monitor import board,heartbeat,insert


def test_bundle_contains_consistent_sqlite_metadata_and_real_exception_coordinates(tmp_path):
    source=tmp_path/'pipeline';source.mkdir()
    db=board(source)
    insert(db)
    try:
        1/0
    except ZeroDivisionError as exc:
        record_crash(source,exc)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    bundle=capture_bundle(source,tmp_path/'bundle',{'health':{'process_resources':{'rss_bytes':123}}})
    assert bundle['database']['state']=='captured' and bundle['sqlite_header_valid']
    with sqlite3.connect(tmp_path/'bundle/database-metadata.db') as snapshot:
        assert snapshot.execute('SELECT kind,status,attempts FROM jobs').fetchall()==[('CHAPTER_DRAFT','FAILED',3.0)]
        assert snapshot.execute('SELECT event FROM events').fetchall()==[('CREATED',)]
    crash=json.loads((tmp_path/'bundle/crash.json').read_text())
    frame=crash['frames'][-1]
    assert frame['filename']==Path(__file__).name and frame['lineno']>0
    assert frame['end_lineno']==frame['lineno'] and frame['end_colno']>frame['colno']
    assert before==hashlib.sha256(db.read_bytes()).hexdigest()
    for path in (tmp_path/'bundle').iterdir():
        if path.name=='database-private.db':continue
        for secret in (b'RAW-PROMPT',b'PRIVATE-CHAPTER',b'EVENT-PAYLOAD'):
            assert secret not in path.read_bytes()
    assert bundle['private_database_snapshot']['consistent_sqlite_backup']
    with sqlite3.connect(tmp_path/'bundle/database-private.db') as backup:
        assert 'RAW-PROMPT' in backup.execute('SELECT payload_json FROM pipeline_jobs').fetchone()[0]
    for name,entry in bundle['files'].items():
        assert entry['sha256']==hashlib.sha256((tmp_path/'bundle'/name).read_bytes()).hexdigest()


def test_database_projection_is_bounded_and_excludes_error_payloads(tmp_path):
    root=tmp_path/'pipeline';root.mkdir();db=board(root)
    for index in range(300): insert(db,job_id=str(index),error='secret payload '+str(index))
    result=capture_bundle(root,tmp_path/'bundle',{'health':{}})
    assert result['database']['truncated'] and result['database']['sampled_jobs']==256
    assert (tmp_path/'bundle/database-metadata.db').stat().st_size<1024*1024


def test_corrupt_source_still_produces_metrics_and_crash_evidence(tmp_path):
    root=tmp_path/'pipeline';root.mkdir();(root/'job_board.db').write_bytes(b'not a SQLite database')
    bundle=capture_bundle(root,tmp_path/'bundle',{'health':{'state':'blocked'}})
    assert bundle['sqlite_header_valid'] is False and bundle['database']['state']=='unavailable'
    assert set(bundle['files'])=={'metrics.json','crash.json','database-header.bin'}
    assert bundle['private_database_snapshot']['state']=='unavailable'
    assert (tmp_path/'bundle/database-header.bin').read_bytes()==b'not a SQLite database'


def test_actual_backup_size_limit_does_not_skip_safe_metadata_projection(tmp_path):
    root=tmp_path/'pipeline';root.mkdir();db=board(root);insert(db)
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE large_private_payload(value BLOB)')
        connection.execute('INSERT INTO large_private_payload VALUES(zeroblob(34000000))')
    result=capture_bundle(root,tmp_path/'bundle',{'health':{}})
    assert result['private_database_snapshot']['reason']=='size_limit'
    assert result['database']['state']=='captured'
    assert not (tmp_path/'bundle/database-private.db').exists()


def test_private_backup_includes_committed_live_wal_pages(tmp_path):
    root=tmp_path/'pipeline';root.mkdir();db=board(root)
    with sqlite3.connect(db) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute("INSERT INTO pipeline_jobs(job_id,kind,status,attempts,updated_at,payload_json) VALUES('wal-only','CHAPTER_DRAFT','PENDING',0,1,'private-WAL-value')")
        live.commit()
        assert (root/'job_board.db-wal').stat().st_size>0
        bundle=capture_bundle(root,tmp_path/'bundle',{'health':{}})
        assert bundle['private_database_snapshot']['state']=='captured'
        with sqlite3.connect(tmp_path/'bundle/database-private.db') as snapshot:
            assert snapshot.execute('SELECT payload_json FROM pipeline_jobs').fetchone()==('private-WAL-value',)
            assert snapshot.execute('PRAGMA integrity_check').fetchone()==('ok',)


def test_crash_ingestion_keeps_environmental_categories_and_omits_injected_fields(tmp_path):
    board(tmp_path)
    for category in ('resource','auth','quota','provider'):
        try: raise TypeError('private secret=DO-NOT-COPY')
        except TypeError as exc: record_crash(tmp_path,exc,category)
        raw=json.loads((tmp_path/'crash-envelope.json').read_text())
        raw['locals']={'secret':'DO-NOT-COPY'};raw['diagnostic']='TypeError secret=DO-NOT-COPY'
        (tmp_path/'crash-envelope.json').write_text(json.dumps(raw))
        observed=read_observation(tmp_path)
        crash=next(item for item in observed['incidents'] if item['evidence'].get('source')=='crash-envelope.json')
        assert crash['category']==category and not crash['repairable']
        assert observed['health']['repair_hold']
        assert 'DO-NOT-COPY' not in json.dumps(observed)


def test_physical_code_crash_is_actionable_but_new_healthy_process_supersedes_it(tmp_path):
    board(tmp_path)
    try: object().missing_method()
    except AttributeError as exc: record_crash(tmp_path,exc)
    assert any(item['repairable'] and item['evidence'].get('source')=='crash-envelope.json'
               for item in read_observation(tmp_path)['incidents'])
    heartbeat(tmp_path,timestamp=time.time())
    path=tmp_path/'supervisor_status.json';hb=json.loads(path.read_text())
    hb['process_started_at']=time.time();hb['timestamp']=hb['process_started_at'];path.write_text(json.dumps(hb))
    assert not any(item['evidence'].get('source')=='crash-envelope.json' for item in read_observation(tmp_path)['incidents'])


def test_oversized_and_linked_crash_envelopes_are_never_ingested(tmp_path):
    path=tmp_path/'crash-envelope.json';path.write_bytes(b'x'*65537)
    assert read_crash(tmp_path)['state']=='invalid'
    path.unlink()
    from pipeline_tests.windows_test_context import create_optional_symlink
    create_optional_symlink(path, tmp_path/'missing')
    assert read_crash(tmp_path)['state']=='invalid'
