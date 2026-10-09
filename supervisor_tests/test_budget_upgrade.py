"""Real SQLite budget migration, with no simulated Windows privilege claims."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing

import pytest

from cochem_supervisor.state import Ledger
from cochem_supervisor.upgrade import migrate_budget_state


def roots(tmp_path):
    source, target = tmp_path / '423', tmp_path / '424'
    source.mkdir()
    target.mkdir()
    return source, target


def reserve_once(ledger, fingerprint='incident', now=1000):
    ledger.observe(fingerprint, 'code', {'source': 'fixture'}, now=now)
    return ledger.reserve(fingerprint, max_per_incident=2, max_per_day=2,
                          cooldown_seconds=0, lease_seconds=30, now=now)


def test_copy_wal_history_preserves_reservations_and_cannot_reset_limits(tmp_path):
    source, target = roots(tmp_path)
    ledger = Ledger(source / 'supervisor.db')
    # Keep a real read connection open so committed changes stay in the WAL.
    connection = sqlite3.connect(source / 'supervisor.db')
    connection.execute('PRAGMA journal_mode=WAL')
    try:
        attempt = reserve_once(ledger)
        ledger.finish(attempt['attempt_id'], 'FAILED', {'fixture': True}, now=1001)
        assert (source / 'supervisor.db-wal').exists()
        before = ledger.history()
        result = migrate_budget_state(source, target)
        assert result['status'] == 'LEDGER_COPIED'
        migrated = Ledger(target / 'supervisor.db')
        assert migrated.history() == before == ledger.history()
        assert migrated.get_attempt(attempt['attempt_id']) == ledger.get_attempt(attempt['attempt_id'])
        second = migrated.reserve('incident', max_per_incident=50, max_per_day=50,
                                  cooldown_seconds=0, now=1002)
        assert second['max_per_incident'] == second['max_per_day'] == 2
        migrated.finish(second['attempt_id'], 'FAILED', {}, now=1003)
        assert migrated.reserve('incident', max_per_incident=50, max_per_day=50,
                                cooldown_seconds=0, now=1004) is None
        assert ledger.get_incident('incident')['attempts'] == 1
    finally:
        connection.close()


def test_repeat_is_idempotent_but_different_destination_is_never_overwritten(tmp_path):
    source, target = roots(tmp_path)
    ledger = Ledger(source / 'supervisor.db')
    attempt = reserve_once(ledger)
    ledger.finish(attempt['attempt_id'], 'FAILED', {}, now=1001)
    migrate_budget_state(source, target)
    assert migrate_budget_state(source, target)['status'] == 'IDENTICAL_LEDGER_PRESERVED'
    destination = Ledger(target / 'supervisor.db')
    destination.record_event('OPERATOR_OBSERVED')
    before = destination.history()
    with pytest.raises(ValueError, match='differs'):
        migrate_budget_state(source, target)
    assert destination.history() == before


def test_pending_reservation_remains_charged_after_copy_and_expiry(tmp_path):
    source, target = roots(tmp_path)
    ledger = Ledger(source / 'supervisor.db')
    attempt = reserve_once(ledger)
    migrate_budget_state(source, target)
    destination = Ledger(target / 'supervisor.db')
    assert destination.get_attempt(attempt['attempt_id'])['status'] == 'RUNNING'
    destination.recover_expired(now=1031)
    assert destination.get_incident('incident')['attempts'] == 1
    assert destination.get_attempt(attempt['attempt_id'])['status'] == 'FAILED'


def test_quarantine_survives_version_upgrade(tmp_path):
    source, target = roots(tmp_path)
    Ledger(source / 'supervisor.db')
    evidence = {'identities': ['CoChem423Repair'], 'reason': 'Fixture unverified cleanup'}
    (source / 'repair-quarantine.json').write_text(json.dumps(evidence))
    assert migrate_budget_state(source, target)['quarantine_preserved'] is True
    assert json.loads((target / 'repair-quarantine.json').read_text()) == evidence


@pytest.mark.parametrize('state', ['PREPARED', 'ACTIVATING', 'VERIFYING', 'ROLLING_BACK', 'ROLLBACK_FAILED', 'UNKNOWN'])
def test_unresolved_deployment_blocks_upgrade_before_copy(tmp_path, state):
    source, target = roots(tmp_path)
    Ledger(source / 'supervisor.db')
    (source / 'release-journal.json').write_text(json.dumps({'state': state}))
    with pytest.raises(ValueError, match='Recover'):
        migrate_budget_state(source, target)
    assert not (target / 'supervisor.db').exists()


def test_fresh_install_needs_no_previous_directory(tmp_path):
    target = tmp_path / '424'
    target.mkdir()
    result = migrate_budget_state(tmp_path / 'absent' / 'private', target)
    assert result['status'] == 'NO_PRIOR_LEDGER'
    assert not (target / 'supervisor.db').exists()


def test_wrong_sqlite_database_and_link_are_rejected(tmp_path):
    source, target = roots(tmp_path)
    with closing(sqlite3.connect(source / 'supervisor.db')) as db:
        db.execute('CREATE TABLE unrelated(value)')
        db.commit()
    with pytest.raises(ValueError, match='not a supervisor'):
        migrate_budget_state(source, target)
    (source / 'supervisor.db').unlink()
    try:
        (source / 'supervisor.db').symlink_to(target / 'outside.db')
    except OSError:
        pytest.skip('Host policy disallows real symlink creation')
    with pytest.raises((ValueError, RuntimeError)):
        migrate_budget_state(source, target)


def test_component_recovery_reservations_and_wal_history_survive_upgrade(tmp_path):
    from cochem_supervisor.component_recovery import RecoveryLedger
    source,target=roots(tmp_path)
    Ledger(source/'supervisor.db')
    recovery=RecoveryLedger(source/'component-recovery.db')
    connection=sqlite3.connect(recovery.path)
    connection.execute('PRAGMA journal_mode=WAL')
    try:
        for at in (1000,1010,1030):
            recovery.observe('docker_engine',False,now=at)
        # A crash between reservation and finish must not refund this action.
        result=migrate_budget_state(source,target)
        assert result['component_status']=='LEDGER_COPIED'
        assert migrate_budget_state(source,target)['component_status']=='IDENTICAL_LEDGER_PRESERVED'
        migrated=RecoveryLedger(target/'component-recovery.db')
        assert migrated.observe('docker_engine',False,now=1040)['state']=='exhausted'
        migrated.finish('docker_engine',{'interrupted':True})
        with pytest.raises(ValueError,match='differs'):
            migrate_budget_state(source,target)
        assert migrated.observe('docker_engine',True,now=1050)['attempts']==1
    finally:
        connection.close()


def test_component_ledger_mismatch_is_checked_before_copying_model_ledger(tmp_path):
    from cochem_supervisor.component_recovery import RecoveryLedger
    source,target=roots(tmp_path)
    Ledger(source/'supervisor.db')
    RecoveryLedger(source/'component-recovery.db').observe('warden',False,now=1)
    RecoveryLedger(target/'component-recovery.db').observe('warden',False,now=2)
    with pytest.raises(ValueError,match='differs'):
        migrate_budget_state(source,target)
    assert not (target/'supervisor.db').exists()


def test_reviewed_same_state_upgrade_preserves_advanced_charged_budget(tmp_path):
    from cochem_supervisor.upgrade import preserve_advanced_budget_state
    source,target=roots(tmp_path)
    old=Ledger(source/'supervisor.db')
    attempt=reserve_once(old)
    old.finish(attempt['attempt_id'],'FAILED',{},now=1001)
    migrate_budget_state(source,target)
    current=Ledger(target/'supervisor.db')
    second=current.reserve('incident',max_per_incident=20,max_per_day=20,cooldown_seconds=0,now=1002)
    current.finish(second['attempt_id'],'FAILED',{},now=1003)
    history=current.history()
    result=preserve_advanced_budget_state(source,target)
    assert result['status']=='ADVANCED_LEDGER_PRESERVED' and result['budgets_reset'] is False
    assert current.history()==history
    assert current.reserve('incident',max_per_incident=20,max_per_day=20,cooldown_seconds=0,now=1004) is None


def test_existing_database_without_prior_lineage_does_not_authorize_preserve(tmp_path):
    from cochem_supervisor.upgrade import preserve_advanced_budget_state
    source,target=roots(tmp_path)
    Ledger(source/'supervisor.db');Ledger(target/'supervisor.db')
    with pytest.raises((OSError,ValueError,RuntimeError)):
        preserve_advanced_budget_state(source,target)
