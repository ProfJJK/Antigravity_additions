"""Physical benchmark checks never turn an unmet timing threshold into a pass."""
import json
import os
import gc
import multiprocessing
import sqlite3
import weakref

import pytest

from cochem_pipeline.performance_acceptance import benchmark_queue,desktop_heap_evidence,observe_native
from cochem_pipeline.runtime import clear_ram_workspace
from cochem_pipeline.store import JobStore


def _claim_inherited_writer(store, result):
    try:
        store.claim('unsafe-inherited-owner')
    except RuntimeError as exc:
        result.send(str(exc))
    else:
        result.send('Inherited SQLite handle was incorrectly reused')
    finally:
        result.close()


def test_ten_actual_sqlite_claimers_report_measured_latency_and_four_seats(tmp_path):
    report=benchmark_queue(tmp_path/'queue',rounds=2)
    assert len(report['samples'])==20
    assert report['acquired_count']==8
    assert report['total_claims']==20
    assert report['concurrency']=='threads'
    assert report['four_seat_invariant_verified'] is True
    assert report['maximum_acquisition_ms']>0
    assert report['passed']==(report['maximum_acquisition_ms']<5)
    assert json.loads((tmp_path/'queue'/'queue-performance.json').read_text())==report


def test_ten_separate_processes_preserve_actual_four_seat_admission(tmp_path):
    report=benchmark_queue(tmp_path/'process-queue',rounds=1,concurrency='processes')
    assert report['total_claims']==10 and report['acquired_count']==4
    assert len({row['job_id'] for row in report['samples'] if row['acquired']})==4
    assert report['maximum_all_claims_ms']>=report['maximum_acquisition_ms']>0
    assert report['passed']==(report['maximum_acquisition_ms']<5)
    with sqlite3.connect(tmp_path/'process-queue'/'queue-0.db') as connection:
        rows=connection.execute("SELECT fencing_token,lease_expires_at-updated_at FROM pipeline_jobs "
            "WHERE status='IN_PROGRESS' AND kind='MANIFEST_GENERATOR'").fetchall()
    assert len(rows)==4 and all(token==1 and lease==1800 for token,lease in rows)


def test_one_idle_writer_connection_retains_durability_and_closes_cleanly(tmp_path):
    store=JobStore(tmp_path/'writer.db')
    store.submit('Actual pooled writer',['R1'],1)
    connection=store._writer_connection
    assert not connection.in_transaction
    assert connection.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    assert connection.execute('PRAGMA synchronous').fetchone()[0]==1
    assert connection.execute('PRAGMA foreign_keys').fetchone()[0]==1
    claimed=store.claim('lease-owner')
    assert store._writer_connection is connection
    assert claimed['fencing_token']==1
    assert claimed['lease_expires_at']-claimed['updated_at']==1800
    store.close()
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connection.execute('SELECT 1')
    assert store.heartbeat(claimed['job_id'],claimed['attempt_id'],claimed['fencing_token'])
    assert store._writer_connection is not connection
    store.close()


def test_idle_writer_has_no_store_reference_cycle_and_is_finalized(tmp_path):
    store=JobStore(tmp_path/'finalizer.db')
    store.submit('Finalizer proof',['R1'],1)
    connection=store._writer_connection
    reference=weakref.ref(store)
    del store
    gc.collect()
    assert reference() is None
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connection.execute('SELECT 1')


@pytest.mark.skipif(os.name=='nt',reason='Windows uses fresh spawned processes, not fork')
def test_forked_child_rejects_parent_writer_before_acquiring_inherited_lock(tmp_path):
    store=JobStore(tmp_path/'fork.db')
    store.submit('Fork ownership',['R1'],1)
    context=multiprocessing.get_context('fork')
    receiver,sender=context.Pipe(duplex=False)
    child=context.Process(target=_claim_inherited_writer,args=(store,sender))
    try:
        with store._writer_lock:
            child.start()
            assert receiver.poll(5),'Inherited writer lock deadlocked the child'
            assert 'Construct a new JobStore after forking' in receiver.recv()
        child.join(timeout=5)
        assert child.exitcode==0
        assert store.active_jobs()==[]
        assert store.claim('original-owner') is not None
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        receiver.close()
        sender.close()
        store.close()


def test_pooled_writer_rollback_preserves_independent_wal_readers(tmp_path):
    store=JobStore(tmp_path/'rollback.db')
    root=store.submit('WAL reader proof',['R1'],1)['workflow_id']
    with pytest.raises(RuntimeError,match='transaction aborted'):
        with store._write() as connection:
            connection.execute('UPDATE pipeline_jobs SET error=? WHERE job_id=?',('uncommitted',root))
            with store._connection() as reader:
                assert reader.execute('SELECT error FROM pipeline_jobs WHERE job_id=?',(root,)).fetchone()[0] is None
            raise RuntimeError('transaction aborted')
    assert not store._writer_connection.in_transaction
    with store._connection() as reader:
        assert reader.execute('SELECT error FROM pipeline_jobs WHERE job_id=?',(root,)).fetchone()[0] is None
    assert store.claim('after-rollback') is not None
    store.close()


def test_absent_desktop_heap_sensor_is_unavailable_not_fabricated_zero():
    report=desktop_heap_evidence(None,pid=os.getpid(),created_at=1,now=1)
    assert report['available'] is False and report['passed'] is False
    assert 'percent' not in report


@pytest.mark.skipif(os.name=='nt',reason='Linux cannot attest native performance')
def test_short_linux_probe_cannot_claim_windows_acceptance(tmp_path):
    with pytest.raises(RuntimeError,match='real Windows SYSTEM'):
        observe_native(tmp_path/'heartbeat',tmp_path/'evidence',duration_seconds=1)


def test_ram_startup_cleanup_rejects_plain_directory_without_touching_bytes(tmp_path):
    (tmp_path/'project').mkdir()
    (tmp_path/'project'/'retained').write_bytes(b'must not delete ordinary disk')
    with pytest.raises(TypeError,match='genuine controller workspace'):
        clear_ram_workspace(tmp_path)
    assert (tmp_path/'project'/'retained').read_bytes()==b'must not delete ordinary disk'
