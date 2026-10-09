"""Ordinary disposable SQLite; no SYSTEM, provider, task or real service calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from cochem_supervisor.component_recovery import RecoveryLedger
from cochem_supervisor.engine import Supervisor
from cochem_supervisor.monitor import read_observation
from cochem_supervisor.state import Ledger


def evidence():
    return {'schema': 'cochem-unresolved-budget-evidence/1',
            'reason': 'Synthetic history is unresolved; do not grant any allowance.',
            'sources': [{'kind': 'synthetic_fixture', 'sha256': '1' * 64}]}


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def assert_existing_bytes_preserved(root, before):
    after = snapshot(root)
    assert {name: after[name] for name in before} == before
    added = set(after) - set(before)
    # The unchanged existing authority reader opens the peer in SQLite mode=ro.
    # SQLite may create coordination sidecars even for a query-only reader;
    # this is not a new charged/history row or a change to the existing DB.
    allowed = {str(Path('supervisor') / ('component-recovery.db' + suffix))
               for suffix in ('-wal', '-shm')}
    assert added <= allowed
    if added:
        assert (root / 'supervisor' / 'component-recovery.db-wal').read_bytes() == b''


def held_supervisor(root, held, *, controller_failure=False):
    private = root / 'supervisor'; private.mkdir()
    primary = root / 'pipeline'; primary.mkdir()
    ledger = Ledger(private / 'supervisor.db')
    if held == 'supervisor':
        ledger.hold_legacy_budget_authority(evidence(), now=1)
    elif held == 'component':
        RecoveryLedger(private / 'component-recovery.db').hold_legacy_budget_authority(evidence(), now=1)
    elif held == 'corrupt-peer':
        (private / 'component-recovery.db').write_bytes(b'not a SQLite ledger')
    else:
        raise AssertionError('Unexpected fixture hold')
    # Real readable SQLite and a real stale heartbeat, without application data.
    with sqlite3.connect(primary / 'job_board.db') as db:
        db.executescript('''
            CREATE TABLE pipeline_jobs(job_id TEXT PRIMARY KEY,kind TEXT,status TEXT,
                attempts INTEGER,updated_at REAL,lease_expires_at REAL,error TEXT,payload_json TEXT);
            CREATE TABLE pipeline_events(id INTEGER PRIMARY KEY,event TEXT,timestamp REAL,details_json TEXT);
        ''')
    (primary / 'supervisor_status.json').write_text(json.dumps({
        'timestamp': 1, 'pid': 42, 'instance_id': 'synthetic-instance',
        'process_started_at': 1, 'sequence': 1, 'version': '4.2.7',
        'status': {'hardware': {'capacity': 4}, 'active_count': 0}}), encoding='utf-8')
    observed = []; controller = []; publications = []; actuators = []
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.private = private; supervisor.ledger = ledger; supervisor.stage = 'fixture'
    def read():
        observed.append(True)
        return read_observation(primary, now=10000)
    def call(route):
        controller.append(route)
        if controller_failure:
            raise RuntimeError('Authentication failed api_key=sk-synthetic-secret-value')
        return {'service_identity': 'SYSTEM', 'pid': 42, 'instance_id': 'synthetic-instance'}
    def forbidden(*args, **kwargs):
        actuators.append(True)
        raise AssertionError('Held observation must not actuate or mutate budget state')
    supervisor._read_observation = read
    supervisor.client = SimpleNamespace(call=call)
    supervisor._publish = lambda *args, **kwargs: publications.append((deepcopy(args), deepcopy(kwargs)))
    for name in ('_quarantine_hold', 'recover', '_recover_components', '_prepare_structural_repair',
                 '_repair_boundary_hold', '_version_checks', '_resume_pending_review', '_repair',
                 '_start', '_stop', '_probe', '_restart_once'):
        setattr(supervisor, name, forbidden)
    for name in ('recover_expired', 'record_event', 'observe', 'reserve', 'pending_reviews'):
        setattr(ledger, name, forbidden)
    supervisor.releases = SimpleNamespace(recovery_required=forbidden)
    return supervisor, observed, controller, publications, actuators


@pytest.mark.parametrize('held', ['supervisor', 'component', 'corrupt-peer'])
@pytest.mark.parametrize('controller_failure', [False, True])
def test_held_tick_retains_real_observation_without_actuation_or_budget_writes(tmp_path, held, controller_failure):
    supervisor, observed, controller, publications, actuators = held_supervisor(
        tmp_path, held, controller_failure=controller_failure)
    before = snapshot(tmp_path)
    result = supervisor.tick(ignore_startup_grace=True)
    assert observed == [True] and controller == ['/health'] and actuators == []
    assert result['health']['heartbeat'] == 'stale'
    assert result['health']['database'] == 'readable'
    assert result['incidents']  # A hold no longer hides the actual stale-heartbeat diagnosis.
    assert result['health']['state'] == 'blocked' and result['health']['budget_authority_hold'] is True
    assert result['health']['repair_hold'] is True and result['health']['observation_seconds'] >= 0
    assert result['health']['components']['controller']['state'] == ('unavailable' if controller_failure else 'healthy')
    assert publications[-1][0] == (result,)
    authority = publications[-1][1]['budget_authority']
    if held == 'corrupt-peer':
        assert authority == {'state': 'AUTHORITY_READ_FAILED', 'error_type': 'DatabaseError'}
    else:
        assert authority['supervisor' if held == 'supervisor' else 'component_recovery']['blocked'] is True
    assert supervisor.stage == 'blocked'
    assert 'sk-synthetic-secret-value' not in json.dumps(publications)
    assert_existing_bytes_preserved(tmp_path, before)


def test_detector_failure_does_not_fall_through_to_any_actuator(tmp_path):
    supervisor, _, controller, publications, actuators = held_supervisor(tmp_path, 'supervisor')
    def fail():
        raise ValueError('inert detector failure')
    supervisor._read_observation = fail
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='inert detector failure'):
        supervisor.tick()
    assert controller == [] and actuators == []
    assert publications[-1][1]['budget_authority']['supervisor']['blocked'] is True
    assert supervisor.stage == 'blocked' and snapshot(tmp_path) == before
