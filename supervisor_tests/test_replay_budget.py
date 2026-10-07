"""Read-only replay against actual durable budget reservations, without inference."""
import hashlib
import sqlite3

from cochem_supervisor.replay import replay_incident
from cochem_supervisor.state import Ledger


def incident():
    return {'fingerprint':'saved-incident','summary':'TypeError: queue acquisition failed',
        'category':'code','evidence':{}}


def snapshot(directory):
    return {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


def test_replay_counts_generation_and_review_without_mutating_current_ledger(tmp_path):
    ledger=Ledger(tmp_path/'supervisor.db')
    ledger.observe('saved-incident','code',incident(),now=10000)
    attempt=ledger.reserve('saved-incident',now=10000)
    assert ledger.reserve_additional_model_call(attempt['attempt_id'],'review-reservation',now=10001)
    ledger.finish(attempt['attempt_id'],'FAILED',{},now=10002)
    before=snapshot(tmp_path)
    report=replay_incident(incident(),ledger_path=ledger.path,now=12000)
    after=snapshot(tmp_path)
    budget=report['budget_projection']
    assert before==after
    assert budget['known'] is True and budget['read_only'] is True
    assert budget['charged_model_calls']=={'incident':2,'utc_day':2,'incident_generations':1,'incident_reviews':1}
    assert budget['remaining_model_calls']=={'incident':0,'utc_day':2}
    assert budget['eligible_to_reserve'] is False
    assert report['action']=='hold_budget_or_incident'
    assert report['model_calls']==report['process_calls']==report['state_mutations']==0


def test_replay_preserves_historical_lower_limits_and_cooldown(tmp_path):
    ledger=Ledger(tmp_path/'supervisor.db')
    ledger.observe('other','code',{},now=10000)
    attempt=ledger.reserve('other',max_per_incident=1,max_per_day=1,cooldown_seconds=3600,now=10000)
    ledger.finish(attempt['attempt_id'],'FAILED',{},now=10001)
    report=replay_incident(incident(),ledger_path=ledger.path,now=10010)
    budget=report['budget_projection']
    assert budget['effective_limits']=={'max_per_incident':2,'max_per_day':1,'cooldown_seconds':3600}
    assert budget['cooldown_until']==13600 and budget['remaining_model_calls']['utc_day']==0
    assert 'repair cooldown has not elapsed' in budget['blockers']


def test_replay_never_recovers_or_refunds_an_expired_reservation(tmp_path):
    ledger=Ledger(tmp_path/'supervisor.db')
    ledger.observe('saved-incident','code',incident(),now=10000)
    attempt=ledger.reserve('saved-incident',lease_seconds=5,now=10000)
    before=snapshot(tmp_path)
    budget=replay_incident(incident(),ledger_path=ledger.path,now=13000)['budget_projection']
    assert budget['expired_reservations']==1 and budget['active_reservations']==1
    assert budget['eligible_to_reserve'] is False and budget['charged_model_calls']['incident']==1
    assert snapshot(tmp_path)==before
    assert ledger.get_attempt(attempt['attempt_id'])['status']=='RUNNING'


def test_missing_or_corrupt_ledger_stays_unknown_and_is_not_created(tmp_path):
    missing=tmp_path/'missing.db'
    report=replay_incident(incident(),ledger_path=missing,now=10000)
    assert report['action']=='hold_budget_evidence_unknown' and not missing.exists()
    corrupt=tmp_path/'corrupt.db';corrupt.write_bytes(b'not a SQLite ledger')
    before=snapshot(tmp_path)
    assert replay_incident(incident(),ledger_path=corrupt)['budget_projection']['known'] is False
    assert snapshot(tmp_path)==before


def test_fresh_existing_ledger_projects_available_calls_but_never_authorizes_execution(tmp_path):
    ledger=Ledger(tmp_path/'supervisor.db')
    before=snapshot(tmp_path)
    report=replay_incident(incident(),ledger_path=ledger.path,now=10000)
    assert report['action']=='would_queue_bounded_repair' and report['execution_authorized'] is False
    assert report['budget_projection']['remaining_model_calls']=={'incident':2,'utc_day':4}
    assert snapshot(tmp_path)==before
