"""Physical SQLite capture checks; subprocess receipts are not native LLM evidence."""
from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from cochem_pipeline.document_governance import execution_contract
from cochem_pipeline.planning_governance import digest, execution_contract as coding_contract
from cochem_pipeline.store import JobStore, canonical_json
from pipeline_tests.test_store import chapter, finish, manifest


def _submit(store, kind, identifier='captured'):
    if kind == 'provider_preflight':
        return store.submit_preflight(identifier)
    return store.submit('Create one requirement chapter', ['REQ-1'], 1, workflow_id=identifier)


@pytest.mark.parametrize('kind', ['document_plan', 'provider_preflight'])
def test_new_workflow_captures_actual_contract_and_keeps_it_on_idempotent_reopen(tmp_path, kind):
    authority = {'specification_id': 'COCHEM-4.2.7', 'specification_sha256': 'a' * 64,
                 'owner_amendments': [{'source': 'owner-fixture', 'sha256': 'b' * 64}]}
    store = JobStore(tmp_path / 'jobs.db', governing_requirements=authority)
    workflow = _submit(store, kind)
    capture = workflow['root']['payload']['governing_requirements']
    assert capture['contract_type'] == kind
    assert capture['contract_sha256'] == digest(capture['contract'])
    assert capture['contract'] == execution_contract(kind)
    hashes = {digest(execution_contract('document_plan')), digest(execution_contract('provider_preflight')),
              digest(coding_contract())}
    assert len(hashes) == 3
    actual_nodes = sorted((job['kind'], job['status']) for job in workflow['jobs'] if job['parent_job_id'])
    assert actual_nodes == sorted((node['kind'], node['status']) for node in capture['contract']['submission_nodes'])
    assert all(job['payload']['governing_requirements'] == capture for job in workflow['jobs'])
    authority['owner_amendments'].clear()
    reopened = JobStore(store.path, governing_requirements={**authority, 'specification_sha256': 'c' * 64})
    assert _submit(reopened, kind) == workflow
    fresh = _submit(reopened, kind, 'later-capture')
    assert fresh['root']['payload']['governing_requirements']['specification_sha256'] == 'c' * 64


def test_scatter_and_synthesis_release_preserve_original_contract(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    workflow = _submit(store, 'document_plan')
    captured = workflow['root']['payload']['governing_requirements']
    job = store.claim('manifest')
    finish(store, job, manifest(1))
    job = store.claim('chapter')
    assert job['payload']['governing_requirements'] == captured
    finish(store, job, chapter(job))
    job = store.claim('synthesis')
    assert job['kind'] == 'SYNTHESIS'
    assert job['payload']['governing_requirements'] == captured
    finish(store, job, {'artifact_text': '# Verified chapter coverage',
                        'chapter_hashes': job['payload']['chapter_hashes']})
    final = store.workflow(workflow['workflow_id'])
    assert final['root']['status'] == 'COMPLETED'
    assert all(job['payload']['governing_requirements'] == captured for job in final['jobs'])


@pytest.mark.parametrize('kind', ['document_plan', 'provider_preflight'])
def test_database_rejects_rewriting_removing_or_inserting_a_different_capture(tmp_path, kind):
    store = JobStore(tmp_path / 'jobs.db')
    workflow = _submit(store, kind)
    root = workflow['root']
    changed = deepcopy(root['payload'])
    changed['governing_requirements']['contract_sha256'] = 'f' * 64
    removed = {key: value for key, value in root['payload'].items() if key != 'governing_requirements'}
    for job in workflow['jobs']:
        with store._write() as conn:
            for payload in (changed, removed):
                with pytest.raises(sqlite3.IntegrityError, match='captured governing requirements are immutable'):
                    conn.execute('UPDATE pipeline_jobs SET payload_json=? WHERE job_id=?',
                                 (canonical_json(payload), job['job_id']))
    with store._write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='child governing requirements must match'):
            store._insert(conn, 'wrong-child', root['job_id'], root['job_id'],
                          'CHAPTER_DRAFT', 'PENDING', changed, chapter_id='wrong-child')
    assert store.workflow(root['job_id']) == workflow


@pytest.mark.parametrize('kind', ['document_plan', 'provider_preflight'])
def test_legacy_missing_capture_remains_unknown_after_reopening(tmp_path, kind):
    store = JobStore(tmp_path / 'historical.db')
    workflow = _submit(store, kind)
    # Reconstruct a physical pre-capture database, then reopen with current schema.
    with store._write() as conn:
        conn.execute('DROP TRIGGER pipeline_governing_capture_immutable')
        conn.execute("UPDATE pipeline_jobs SET payload_json=json_remove(payload_json,'$.governing_requirements')")
    historical = store.workflow(workflow['workflow_id'])
    reopened = JobStore(store.path, governing_requirements={'specification_sha256': 'a' * 64})
    assert _submit(reopened, kind) == historical
    assert all('governing_requirements' not in job['payload'] for job in historical['jobs'])
    with reopened._write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='captured governing requirements are immutable'):
            conn.execute("UPDATE pipeline_jobs SET payload_json=json_set(payload_json,'$.governing_requirements',json(?))",
                         (canonical_json(workflow['root']['payload']['governing_requirements']),))
