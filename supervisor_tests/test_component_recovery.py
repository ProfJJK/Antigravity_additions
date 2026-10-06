"""Real SQLite recovery reservations; no native service recovery is claimed."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3

import pytest

from cochem_supervisor.component_recovery import RecoveryLedger,start_docker_service


def test_strikes_and_elapsed_backoff_both_precede_a_single_durable_reservation(tmp_path):
    path=tmp_path/'recovery.db'
    ledger=RecoveryLedger(path)
    assert ledger.observe('docker_engine',False,now=0)['state']=='waiting'
    assert ledger.observe('docker_engine',False,now=1)['state']=='waiting'
    assert ledger.observe('docker_engine',False,now=2)['state']=='waiting'
    assert ledger.observe('docker_engine',False,now=30)['state']=='reserved'
    # Simulate a crash after reservation and before action/result recording.
    assert RecoveryLedger(path).observe('docker_engine',False,now=31)['state']=='exhausted'
    ledger.finish('docker_engine',{'service_running':False,'test_scope':'physical ledger only'})
    assert ledger.observe('docker_engine',True,now=32)['state']=='healthy'
    assert ledger.observe('docker_engine',False,now=100)['state']=='exhausted'
    assert ledger.observe('warden',False,now=100)['state']=='waiting'


def test_parallel_observers_cannot_double_reserve_component_recovery(tmp_path):
    path=tmp_path/'recovery.db'; ledger=RecoveryLedger(path)
    ledger.observe('warden',False,now=0)
    ledger.observe('warden',False,now=10)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:RecoveryLedger(path).observe('warden',False,now=40),range(16)))
    assert [result['state'] for result in results].count('reserved')==1
    assert all(result['attempts']==1 for result in results)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM recovery_events WHERE event='RECOVERY_RESERVED'").fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM recovery_events')


def test_recovery_result_requires_exactly_one_unfinished_reservation(tmp_path):
    ledger=RecoveryLedger(tmp_path/'recovery.db')
    with pytest.raises(ValueError,match='reservation'):
        ledger.finish('warden',{})
    for now in (0,10,30): ledger.observe('warden',False,now=now)
    ledger.finish('warden',{'completed':True})
    with pytest.raises(ValueError,match='reservation'):
        ledger.finish('warden',{'completed':False})
    with sqlite3.connect(ledger.path) as db:
        stored=db.execute('SELECT last_result FROM components').fetchone()[0]
        assert json.loads(stored)=={'completed':True}


@pytest.mark.parametrize('component,healthy,now',[
    ('arbitrary-service',False,0),('warden','false',0),('warden',False,-1),('warden',False,float('nan'))])
def test_invalid_recovery_policy_or_clock_cannot_create_reservation(tmp_path,component,healthy,now):
    with pytest.raises(ValueError):
        RecoveryLedger(tmp_path/'recovery.db').observe(component,healthy,now=now)


@pytest.mark.parametrize('timeout',[0,31,True,float('inf')])
def test_native_recovery_timeout_is_strictly_bounded(timeout):
    with pytest.raises(ValueError,match='timeout'):
        start_docker_service(timeout)


@pytest.mark.skipif(os.name=='nt',reason='Non-Windows rejection is a platform-specific contract')
def test_linux_cannot_claim_native_windows_service_recovery():
    with pytest.raises(RuntimeError,match='native Windows'):
        start_docker_service()
