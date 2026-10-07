"""Read-only SQLite predictions compared with actual atomic scheduler choices."""
import json

from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.routing_store import preview_next_route
from cochem_pipeline.store import JobStore


def submitted(tmp_path, *, policy=None):
    store=JobStore(tmp_path/'preview.db',routing_policy=policy)
    workflow=store.submit('Document a short note',['REQ-1'],1)
    job=next(row for row in workflow['jobs'] if row['kind']=='MANIFEST_GENERATOR')
    return store,job


def preview(store,job):
    with store._connection() as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        before=list(db.iterdump())
        result=preview_next_route(db,store._get(db,job['job_id']),store.routing_policy)
        assert list(db.iterdump())==before
        return result


def test_held_preferred_predicts_next_candidate_without_mutating_then_claim_agrees(tmp_path):
    store,job=submitted(tmp_path)
    store.set_route_hold('provider','gemini',60,'observed_quota')
    result=preview(store,job)
    assert result['eligible_candidate']['provider']=='claude'
    assert result['candidate_index']==1
    assert result['skipped'][0]['reason']=='observed_quota'
    assert result['unknown_until_atomic_claim'] is True
    actual=store.claim('controller',worker_slot='one')
    assert actual['route']['key']==result['eligible_candidate']['key']
    assert preview(store,job)['state']=='not_queued'


def test_all_held_preview_cannot_invent_or_persist_a_backoff_deadline(tmp_path):
    store,job=submitted(tmp_path)
    for provider in ('gemini','claude','codex'):
        store.set_route_hold('provider',provider,60,'observed_busy')
    result=preview(store,job)
    assert result['state']=='all_candidates_unavailable'
    assert result['eligible_candidate'] is None and result['next_eligible_at'] is None
    assert len(result['skipped'])==3
    assert store.get(job['job_id'])['routing']['cycle']==0
    assert store.claim('controller') is None
    assert store.get(job['job_id'])['routing']['cycle']==1
    assert preview(store,job)['state']=='routing_wait'


def test_preview_uses_live_model_capacity_after_policy_change(tmp_path):
    store,first=submitted(tmp_path)
    second=store.submit('Another short note',['REQ-1'],1)
    second=next(row for row in second['jobs'] if row['kind']=='MANIFEST_GENERATOR')
    store.claim('first',worker_slot='one')
    current=load_routing_policy({'model_limits':{'gemini:gemini-3.8-flash':1}})
    changed=JobStore(store.path,routing_policy=current)
    result=preview(changed,second)
    assert result['skipped'][0]['reason']=='busy'
    assert result['eligible_candidate']['provider']=='claude'
    assert changed.claim('second',worker_slot='two')['route']['key']==result['eligible_candidate']['key']


def test_preview_honors_job_local_context_cursor_without_inventing_token_windows(tmp_path):
    store,job=submitted(tmp_path)
    first=store.claim('first',worker_slot='one')
    store.fail(first['job_id'],first['attempt_id'],first['fencing_token'],
               'Storage fixture: full prompt exceeds transport context bound',retry=True,category='context')
    result=preview(store,job)
    assert result['candidate_index']==1 and result['eligible_candidate']['provider']=='claude'
    assert result['context_check']=='pending_complete_prompt_and_oracle_validation'
    assert store.claim('next',worker_slot='one')['route']['candidate_index']==1


def test_preview_respects_cleanup_barrier_even_after_reservation_release(tmp_path):
    store,job=submitted(tmp_path)
    another=store.submit('Second short note',['REQ-1'],1)
    another=next(row for row in another['jobs'] if row['kind']=='MANIFEST_GENERATOR')
    first=store.claim('first',worker_slot='one',requires_cleanup=True)
    store.fail(first['job_id'],first['attempt_id'],first['fencing_token'],
               'Storage fixture: process cleanup pending',retry=True,category='quota')
    result=preview(store,another)
    assert result['state']=='execution_cleanup_unverified'
    assert result['eligible_candidate'] is None


def test_preview_root_and_budget_exhaustion_never_claim_eligibility(tmp_path):
    store,job=submitted(tmp_path)
    root=store.get(job['workflow_id'])
    assert preview(store,root)['state']=='not_model_job'
    with store._write() as db:
        db.execute('UPDATE pipeline_routing_jobs SET failure_count=? WHERE job_id=?',
                   (job['max_attempts'],job['job_id']))
    assert preview(store,job)['state']=='failure_budget_exhausted'


def test_preview_uses_provider_asymmetry_for_srs_reconciliation(tmp_path):
    from cochem_pipeline import routing_store
    store=JobStore(tmp_path/'review.db')
    with store._write() as db:
        store._insert(db,'workflow','workflow',None,'CODE_REQUEST','IN_PROGRESS',{})
        routing_store.capture_workflow(db,'workflow',store.routing_policy)
        store._insert(db,'review','workflow','workflow','CODE_REVIEW','PENDING',
            {'objective':'Review authentication','requirements':['REQ-1'],
             'review_scope':'srs_wbs_reconciliation',
             'producer':{'provider':'claude','model':'claude-opus-5-5'}})
    result=preview(store,store.get('review'))
    assert result['skipped'][0]['reason']=='asymmetric_review'
    assert result['eligible_candidate']['provider']=='codex'
    with store._write() as db:
        actual,routed=routing_store.select(db,store._get(db,'review'),store.routing_policy)
        assert routed and actual['key']==result['eligible_candidate']['key']
