"""Real temporary SQLite and engine decisions; no native actions/production DBs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
from types import SimpleNamespace

import pytest

from cochem_supervisor import budget_authority
from cochem_supervisor.component_recovery import RecoveryLedger
from cochem_supervisor.engine import Supervisor
from cochem_supervisor.state import Ledger
from cochem_supervisor.upgrade import migrate_budget_state, preserve_advanced_budget_state


def evidence():
    return {'schema':'cochem-unresolved-budget-evidence/1',
            'reason':'Legacy spend is unknown; preserve history without a fresh allowance.',
            'sources':[{'kind':'snapshot_manifest','sha256':'1'*64}]}


def seed(path):
    ledger=Ledger(path)
    ledger.observe('old','code',{'synthetic':True},now=100)
    attempt=ledger.reserve('old',now=100)
    return ledger,attempt


def test_hold_preserves_original_charges_and_caps_after_reopen(tmp_path):
    ledger,attempt=seed(tmp_path/'supervisor.db')
    ledger.finish(attempt['attempt_id'],'FAILED',{'preserved':True},now=101)
    original=ledger.get_attempt(attempt['attempt_id']);history=ledger.history()
    held=ledger.hold_legacy_budget_authority(evidence(),now=102)
    assert held['blocked'] and held['remaining_legacy_allowance'] is None
    assert not held['historical_spend_verified']
    assert ledger.hold_legacy_budget_authority(evidence(),now=999)==held
    reopened=Ledger(ledger.path)
    reopened.observe('new','code',{},now=2000)
    assert reopened.reserve('new',now=2000) is None
    assert reopened.reserve('old',now=2000) is None
    assert reopened.get_attempt(attempt['attempt_id'])==original
    assert original['max_per_incident']==2 and original['max_per_day']==4 and original['cooldown_seconds']==1800
    assert reopened.history()[:len(history)]==history
    assert reopened.get_incident('old')['model_calls']==1
    assert reopened.get_incident('new')['model_calls']==0


def test_pending_review_and_extra_spend_stay_held_without_erasing_checkpoint(tmp_path):
    ledger,attempt=seed(tmp_path/'supervisor.db')
    identifier=attempt['attempt_id']
    ledger.save_review_checkpoint(identifier,{'synthetic':'candidate'},now=101)
    checkpoint=ledger.review_checkpoint(identifier)
    ledger.hold_legacy_budget_authority(evidence(),now=102)
    assert ledger.reserve_additional_model_call(identifier,'route',now=103) is None
    with pytest.raises(ValueError,match=budget_authority.STATE):
        ledger.resume_review(identifier,cleanup_verified=True,now=103)
    assert not ledger.heartbeat(identifier,now=103)
    assert ledger.review_checkpoint(identifier)==checkpoint
    assert ledger.get_attempt(identifier)==attempt_without_incident(attempt)
    # A held execution can still record a terminal cleanup result; no refund.
    ledger.finish(identifier,'BLOCKED',{'cleanup_recorded':True},now=104)
    with pytest.raises(ValueError,match=budget_authority.STATE):
        ledger.unblock('old','restored unrelated prerequisite',now=105)
    assert ledger.get_incident('old')['model_calls']==1


def attempt_without_incident(attempt):
    return {key:value for key,value in attempt.items() if key!='incident'}


@pytest.mark.parametrize('prior_attempt', [False,True])
def test_component_health_remains_recordable_but_cannot_recover(tmp_path,prior_attempt):
    ledger=RecoveryLedger(tmp_path/'component.db')
    if prior_attempt:
        for now in (0,10,31): ledger.observe('warden',False,now=now)
    ledger.hold_legacy_budget_authority(evidence(),now=32)
    for now in (40,50,71,120):
        decision=ledger.observe('warden',False,now=now)
        assert decision['state']=='authority_hold' and decision['attempts']==int(prior_attempt)
        assert decision['next_eligible_at'] is None
    health=RecoveryLedger(ledger.path).observe('warden',True,now=121)
    assert health['state']=='healthy' and health['attempts']==int(prior_attempt)
    if prior_attempt:
        ledger.finish('warden',{'prior_action_cleanup_only':True})
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT count(*) FROM recovery_events WHERE event='RECOVERY_RESERVED'").fetchone()[0]==int(prior_attempt)


@pytest.mark.parametrize('sql', [
    "UPDATE unresolved_budget_authority SET recorded_at=0",
    "DELETE FROM unresolved_budget_authority",
    "INSERT OR REPLACE INTO unresolved_budget_authority SELECT * FROM unresolved_budget_authority",
])
def test_hold_immutable_even_for_old_sql_callers(tmp_path,sql):
    ledger=Ledger(tmp_path/'db');held=ledger.hold_legacy_budget_authority(evidence(),now=1)
    with sqlite3.connect(ledger.path) as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute(sql)
    assert ledger.budget_authority_status()==held


@pytest.mark.parametrize('kind', ['attempt','review','lease','component-new','component-increment'])
def test_persisted_sql_triggers_block_old_code_without_python_guards(tmp_path,kind):
    if kind.startswith('component'):
        ledger=RecoveryLedger(tmp_path/'component.db')
        ledger.observe('warden',False,now=1)
        ledger.hold_legacy_budget_authority(evidence(),now=2)
        if kind=='component-new':
            sql="INSERT INTO components VALUES('docker_engine',3,1,31,1,31,NULL)";params=()
        else:
            sql="UPDATE components SET attempts=attempts+1,reserved_at=31 WHERE component='warden'";params=()
    else:
        ledger,attempt=seed(tmp_path/'supervisor.db')
        ledger.hold_legacy_budget_authority(evidence(),now=101)
        if kind=='lease':
            sql='UPDATE supervisor_attempts SET lease_expires_at=9000 WHERE attempt_id=?';params=(attempt['attempt_id'],)
        elif kind=='review':
            sql="INSERT INTO supervisor_model_calls VALUES('newcall',?,'old','1970-01-01',102,'review','newroute',4)";params=(attempt['attempt_id'],)
        else:
            sql="""INSERT INTO supervisor_attempts SELECT 'newid',fingerprint,category,evidence_json,status,
                started_at,finished_at,lease_expires_at,budget_day,max_per_incident,max_per_day,cooldown_seconds,details_json
                FROM supervisor_attempts WHERE attempt_id=?""";params=(attempt['attempt_id'],)
    with sqlite3.connect(ledger.path) as db:
        with pytest.raises(sqlite3.IntegrityError,match=budget_authority.STATE): db.execute(sql,params)


def test_concurrent_callers_cannot_spend_after_hold_commit(tmp_path):
    path=tmp_path/'supervisor.db';ledger=Ledger(path)
    ledger.observe('incident','code',{},now=1)
    ledger.hold_legacy_budget_authority(evidence(),now=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:Ledger(path).reserve('incident',cooldown_seconds=0,now=100),range(24)))
    assert results==[None]*24
    assert ledger.get_incident('incident')['attempts']==0
    component=RecoveryLedger(tmp_path/'component.db');component.hold_legacy_budget_authority(evidence(),now=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:RecoveryLedger(component.path).observe('warden',False,now=100),range(24)))
    assert all(row['state']=='authority_hold' and row['attempts']==0 for row in results)


def test_hold_transaction_serializes_with_a_waiting_reservation(tmp_path):
    ledger=Ledger(tmp_path/'supervisor.db');ledger.observe('incident','code',{},now=1)
    entered=threading.Event()
    def reserve():
        entered.set()
        return ledger.reserve('incident',now=3)
    with sqlite3.connect(ledger.path,isolation_level=None) as db, ThreadPoolExecutor(max_workers=1) as pool:
        db.execute('BEGIN IMMEDIATE')
        budget_authority.record(db,evidence(),now=2)
        pending=pool.submit(reserve)
        assert entered.wait(timeout=2) and not pending.done()
        db.commit()
        assert pending.result(timeout=5) is None
    assert ledger.get_incident('incident')['attempts']==0


def test_sqlite_backup_and_advanced_migration_preserve_both_holds(tmp_path):
    source=tmp_path/'source';target=tmp_path/'target';target.mkdir()
    ledger,attempt=seed(source/'supervisor.db');ledger.finish(attempt['attempt_id'],'FAILED',{},now=101)
    component=RecoveryLedger(source/'component-recovery.db')
    held=ledger.hold_legacy_budget_authority(evidence(),now=102)
    component.hold_legacy_budget_authority(evidence(),now=102)
    report=migrate_budget_state(source,target)
    assert report['status']=='LEDGER_COPIED' and report['component_status']=='LEDGER_COPIED'
    copied=Ledger(target/'supervisor.db');copied_component=RecoveryLedger(target/'component-recovery.db')
    assert copied.budget_authority_status()==held and copied_component.budget_authority_status()==held
    copied.record_event('READ_ONLY_DIAGNOSIS',{'preserved':True},now=103)
    assert copied_component.observe('warden',False,now=103)['state']=='authority_hold'
    assert preserve_advanced_budget_state(source,target)['status']=='ADVANCED_LEDGER_PRESERVED'
    assert copied.reserve('old',now=3000) is None
    assert copied.get_attempt(attempt['attempt_id'])==ledger.get_attempt(attempt['attempt_id'])


@pytest.mark.parametrize('alteration', ['missing-hold','missing-trigger'])
def test_advanced_migration_refuses_lost_hold_or_rollback_guard(tmp_path,alteration):
    source=tmp_path/'source';target=tmp_path/'target';target.mkdir()
    Ledger(source/'supervisor.db').hold_legacy_budget_authority(evidence(),now=1)
    migrate_budget_state(source,target)
    with sqlite3.connect(target/'supervisor.db') as db:
        if alteration=='missing-hold':
            db.execute('DROP TRIGGER unresolved_budget_authority_no_delete')
            db.execute('DELETE FROM unresolved_budget_authority')
        else:
            db.execute('DROP TRIGGER unresolved_authority_attempt_insert')
    with pytest.raises(ValueError,match='unresolved|guard'):
        preserve_advanced_budget_state(source,target)


@pytest.mark.parametrize('held_ledger', ['supervisor','component'])
def test_engine_stops_before_native_board_or_lifecycle_actions(tmp_path,held_ledger):
    # No Windows fixture bypass is involved: construct only the fields needed
    # by real early guards; every downstream side effect is a fatal sentinel.
    supervisor=Supervisor.__new__(Supervisor);supervisor.private=tmp_path
    supervisor.ledger=Ledger(tmp_path/'supervisor.db');supervisor.stage='new'
    if held_ledger=='supervisor': supervisor.ledger.hold_legacy_budget_authority(evidence(),now=1)
    else: RecoveryLedger(tmp_path/'component-recovery.db').hold_legacy_budget_authority(evidence(),now=1)
    publications=[];supervisor._publish=lambda *args,**kwargs:publications.append(kwargs)
    def forbidden(*args,**kwargs): raise AssertionError('No native/board/lifecycle work is permitted')
    supervisor._quiesce_for_repair=forbidden
    supervisor._read_observation=lambda:{'health':{},'incidents':[]}
    supervisor._restart_once=forbidden;supervisor.releases=SimpleNamespace(recover=forbidden)
    assert supervisor._repair({}) is False and supervisor._resume_pending_review({}) is False
    assert supervisor.recover()['recovery_executed'] is False
    observation={'health':{},'incidents':[]}
    assert supervisor._recover_components(observation) is True
    assert observation['health']['budget_authority_hold'] is True
    assert supervisor.tick()['health']['budget_authority_hold'] is True
    assert supervisor._probe('unused') is False
    with pytest.raises(RuntimeError): Supervisor._start(supervisor,'unused')
    assert publications and supervisor.stage=='blocked'
    assert not (tmp_path/'repair-job-board.db').exists()


def test_absent_marker_is_not_reported_as_verified_spend_or_zero_allowance(tmp_path):
    absent=budget_authority.read_status(tmp_path/'not-created.db')
    assert not (tmp_path/'not-created.db').exists()
    assert not absent['blocked'] and not absent['historical_spend_verified']
    assert absent['remaining_legacy_allowance'] is None
    ledger=Ledger(tmp_path/'known-modern.db')
    ledger.observe('new','code',{},now=1)
    assert ledger.reserve('new',now=1) is not None


def test_peer_read_refuses_real_linked_ancestor_even_when_leaf_missing(tmp_path):
    target=tmp_path/'target';target.mkdir()
    Ledger(target/'supervisor.db').hold_legacy_budget_authority(evidence(),now=1)
    linked=tmp_path/'linked'
    if os.name=='nt':
        def quote(path): return "'"+str(path).replace("'","''")+"'"
        child=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',
            "$ErrorActionPreference='Stop';New-Item -ItemType Junction -Path "+quote(linked)+' -Target '+quote(target)+'|Out-Null'],
            text=True,capture_output=True,timeout=10)
        assert child.returncode==0,child.stderr
    else:
        linked.symlink_to(target,target_is_directory=True)
    try:
        for name in ('supervisor.db','missing.db'):
            with pytest.raises(ValueError,match='reparse|symlink'):
                budget_authority.read_status(linked/name)
    finally:
        if os.name=='nt': os.rmdir(linked)
        else: linked.unlink()
    assert budget_authority.read_status(target/'supervisor.db')['blocked']


def test_peer_read_does_not_treat_permission_error_as_missing(tmp_path,monkeypatch):
    path=(tmp_path/'protected.db').absolute();original=Path.lstat
    def denied(self,*args,**kwargs):
        if self==path: raise PermissionError('inert permission fixture')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',denied)
    with pytest.raises(PermissionError): budget_authority.read_status(path)


@pytest.mark.parametrize('change', ['missing-source','duplicate','bad-hash','bad-reason','nan-time','changed-existing'])
def test_malformed_or_conflicting_provenance_cannot_replace_hold(tmp_path,change):
    ledger=Ledger(tmp_path/'db');original=evidence();value=deepcopy(original);now=1
    if change=='missing-source':value['sources']=[]
    elif change=='duplicate':value['sources']*=2
    elif change=='bad-hash':value['sources'][0]['sha256']='not-a-hash'
    elif change=='bad-reason':value['reason']='secret\nmultiline'
    elif change=='nan-time':now=float('nan')
    else:
        ledger.hold_legacy_budget_authority(original,now=1);value['reason']='different evidence'
    with pytest.raises(ValueError):ledger.hold_legacy_budget_authority(value,now=now)
    if change=='changed-existing': assert ledger.budget_authority_status()['blocked']
    else: assert not ledger.budget_authority_status()['blocked']
