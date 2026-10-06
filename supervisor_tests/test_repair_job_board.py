"""Actual SQLite repair routing, independent of candidate modules or CLI fixtures."""
from copy import deepcopy
import json
import sqlite3
import subprocess
import sys

import pytest

from cochem_pipeline.routing import RoutingPolicy, score_task
from cochem_supervisor.job_board import RepairJobBoard
from cochem_supervisor.probes import configured_routing_policy, score_routing_task
from cochem_supervisor.replay import replay_incident, repair_scoring_payload


def payload():
    return repair_scoring_payload({'summary':'TypeError: transaction routing failed'})


def native_contract(route):
    return {'provider':route['provider'],'requested_model':route['model'],
        'requested_effort':route['reasoning_effort'], 'route_reservation_sha256':route['reservation_sha256'],
        'subscription_verified':True,'terminal_success':True,'exit_code':0,
        'pid':42,'session_id':'fixture-contract-not-live','output_sha256':'a'*64}


def test_independent_policy_and_score_match_primary_contract():
    assert configured_routing_policy()==RoutingPolicy().as_dict()
    for kind in ('REPAIR_REQUEST','PREFLIGHT_REQUEST','SYNTHESIS'):
        assert score_routing_task(kind,payload())==score_task(kind,payload())
    altered=deepcopy(configured_routing_policy())
    altered['tiers']['10'].reverse()
    with pytest.raises(ValueError,match='Chapter 06'):
        configured_routing_policy(altered)


def test_quota_spillover_and_exhaustion_backoff_survive_reopening(tmp_path):
    path=tmp_path/'board.db'
    board=RepairJobBoard(path)
    job=board.submit('incident',payload(),{'backoff_jitter_fraction':0},now=100)
    first=board.claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=101)
    assert (first['provider'],first['model'])==('claude','claude-sonnet-5-5')
    board.finish(first,failure='quota',now=102)
    board=RepairJobBoard(path)
    second=board.claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=103)
    assert second['provider']=='codex'
    board.finish(second,failure='busy',now=104)
    third=board.claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=105)
    assert third['provider']=='gemini'
    board.finish(third,failure='provider',now=106)
    assert board.claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=107) is None
    waiting=board.get(job['job_id'])
    assert waiting['next_eligible']==137 and waiting['candidate_index']==0
    assert board.claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=136) is None
    # All provider holds elapsed; the next cycle returns to the preferred model.
    resumed=RepairJobBoard(path).claim(job['job_id'],['claude','codex','gemini'],primary_cleanup_verified=True,now=403)
    assert resumed['provider']=='claude' and resumed['cycle']==1


def test_cleanup_fence_and_native_receipt_are_required_before_completion(tmp_path):
    board=RepairJobBoard(tmp_path/'board.db')
    job=board.submit('incident',payload())
    with pytest.raises(ValueError,match='cleanup'):
        board.claim(job['job_id'],['claude'],primary_cleanup_verified=False)
    route=board.claim(job['job_id'],['claude'],primary_cleanup_verified=True)
    for changed in ({'requested_model':'gpt-6-sol'},{'subscription_verified':False},
                    {'pid':True},{'route_reservation_sha256':'0'*64}):
        with pytest.raises(ValueError,match='receipt evidence'):
            board.finish(route,receipt={**native_contract(route),**changed})
    board.finish(route,receipt=native_contract(route))
    with pytest.raises(ValueError,match='Stale'):
        board.finish(route,failure='quota')
    with sqlite3.connect(board.path) as db:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute("UPDATE repair_jobs SET scoring='{}'")
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM repair_route_events')


def test_unspent_reservation_preserves_budget_and_first_candidate(tmp_path):
    board=RepairJobBoard(tmp_path/'board.db')
    job=board.submit('incident',payload())
    first=board.claim(job['job_id'],['claude'],primary_cleanup_verified=True)
    board.release_unspent(first)
    second=board.claim(job['job_id'],['claude'],primary_cleanup_verified=True)
    assert second['candidate_index']==0 and second['reservation_id']!=first['reservation_id']
    assert board.get(job['job_id'])['dispatches']==1


def test_dry_replay_redacts_secrets_and_executes_no_provider(tmp_path):
    incident={'summary':'TypeError: route failed','category':'code','evidence':
        {'password':'private-secret','message':'Authorization: Bearer abcdef-secret','payload':{'text':'private source'}}}
    report=replay_incident(incident)
    assert report['model_calls']==report['process_calls']==report['state_mutations']==0
    assert report['classification']['category']=='code' and report['execution_authorized'] is False
    assert 'private-secret' not in json.dumps(report) and 'private source' not in json.dumps(report)
    path=tmp_path/'incident.json';path.write_text(json.dumps(incident))
    child=subprocess.run([sys.executable,'-m','cochem_supervisor','replay-incident','--input',str(path)],
                         capture_output=True,text=True,timeout=15,check=True)
    actual=json.loads(child.stdout)
    assert actual['ordered_candidates']==report['ordered_candidates']
    assert sorted(p.name for p in tmp_path.iterdir())==['incident.json']


def test_interrupted_native_reservation_cannot_be_refunded_or_refenced_without_cleanup(tmp_path):
    board=RepairJobBoard(tmp_path/'board.db')
    job=board.submit('incident',payload())
    route=board.claim(job['job_id'],['claude'],primary_cleanup_verified=True)
    board.bind_budget(route,'charged-attempt')
    persisted=RepairJobBoard(board.path).interrupted_attempt(job['job_id'])
    assert persisted['attempt_id']=='charged-attempt'
    with pytest.raises(ValueError,match='cleanup'):
        board.recover_interrupted(route,cleanup_verified=False,budget_attempt_terminal=True)
    with pytest.raises(ValueError,match='terminal'):
        board.recover_interrupted(route,cleanup_verified=True,budget_attempt_terminal=False)
    board.recover_interrupted(route,cleanup_verified=True,budget_attempt_terminal=True)
    assert board.get(job['job_id'])['dispatches']==1
    assert board.claim(job['job_id'],['claude','codex'],primary_cleanup_verified=True)['provider']=='claude'


def test_review_reservation_crash_is_visible_to_boardwide_recovery(tmp_path):
    board=RepairJobBoard(tmp_path/'board.db')
    review=board.submit('review',{'objective':'Review code','excluded_providers':['gemini']},kind='REPAIR_REVIEW')
    route=board.claim(review['job_id'],['claude'],primary_cleanup_verified=True)
    board.bind_budget(route,'charged-review-parent')
    reopened=RepairJobBoard(board.path)
    interrupted=reopened.interrupted_reservations()
    assert len(interrupted)==1 and interrupted[0]['reservation']['kind']=='REPAIR_REVIEW'
    reopened.recover_interrupted(route,cleanup_verified=True,budget_attempt_terminal=True)
    assert reopened.interrupted_reservations()==[]
