"""Compatibility bridge through real authenticated HTTP and durable SQLite.

The test controller has no inference runner; it cannot claim native completion.
Native CLI process/receipt tests live in pipeline_tests and provider tests.
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from cochem_mcp.config import Settings
from cochem_mcp.jobs import JobManager
from pipeline_tests.test_service import endpoint


@pytest.fixture
def job_environment(tmp_path, endpoint):
    workspace = tmp_path / 'workspace with spaces ü'; workspace.mkdir()
    token = tmp_path / 'bridge-token'; token.write_text(endpoint.token)
    endpoint.controller.config.coding_projects = {'example': {}}
    requests = []
    # A bounded HTTP protocol fixture for coding submission, never a model result.
    def submit_coding(project_id, objective, requirements, workflow_id=None):
        requests.append({'project_id': project_id, 'objective': objective, 'requirements': requirements})
        return {'workflow_id': 'recorded-code-request', 'state': 'PLANNING', 'status': 'IN_PROGRESS'}
    endpoint.controller.submit_coding = submit_coding
    managers=[]
    def create(**overrides):
        values = dict(provider='codex', executable=None, workspace_roots=(workspace,),
            state_dir=tmp_path/'bridge-state', models={'sol':'gpt-6-sol'}, default_model='sol',
            controller_port=endpoint.port, controller_token_file=str(token),
            projects=((str(workspace), 'example'),))
        values.update(overrides)
        manager=JobManager(Settings(**values)); manager.endpoint=endpoint; manager.requests=requests
        managers.append(manager); return manager
    yield create, workspace
    for manager in managers: manager.close()


def test_coding_request_goes_to_board_without_provider_or_model_pin(job_environment):
    create, workspace=job_environment
    manager=create(provider='claude')
    record=manager.submit('Correct the regression', str(workspace))
    assert record['status']=='queued' and record['job_id']=='code:recorded-code-request'
    assert record['provider'] is None and record['requested_model'] is None
    assert manager.requests==[{'project_id':'example','objective':'Correct the regression','requirements':['REQ-001']}]
    assert not manager.root.exists()  # No local worker, process receipt or inference queue.


def test_planning_is_durable_controller_dag_with_routing_and_no_completion_claim(job_environment):
    create,_=job_environment; manager=create()
    record=manager.submit_node('MANIFEST_GENERATOR', {'objective':'Design recovery','requirements':['R1'],
        'chapter_count':1}, 'bridge-plan')
    saved=manager.endpoint.controller.store.workflow('bridge-plan')
    assert saved['root']['payload']['objective']=='Design recovery'
    model_jobs=[job for job in saved['jobs'] if job['kind']!='MACRO_PLANNING_REQUEST' and job['status']=='PENDING']
    assert all(job['routing'] for job in model_jobs)
    assert manager.result(record['job_id'])['content'] is None
    before=manager.endpoint.controller.store.workflow('bridge-plan')
    for _ in range(3): manager.status(record['job_id'])
    assert manager.endpoint.controller.store.workflow('bridge-plan')==before
    cancelled = manager.cancel(record['job_id'])
    assert cancelled['status'] == 'cancelled' and cancelled['controller_aggregate_status'] == 'FAILED'
    assert manager.endpoint.controller.store.workflow('bridge-plan')['status']=='FAILED'
    assert manager.result(record['job_id'])['content'] is None


def test_chapter_request_preserves_scope_in_new_controller_owned_dag(job_environment):
    create, _ = job_environment
    manager = create()
    record = manager.submit_node('CHAPTER_DRAFT', {'objective': 'Design RAM recovery',
        'requirements': ['RAM-1'], 'chapter_id': 'ram-09', 'title': 'Preserve the existing R: volume'},
        'chapter-scope-request')
    saved = manager.endpoint.controller.store.workflow('chapter-scope-request')
    objective = saved['root']['payload']['objective']
    assert 'Design RAM recovery' in objective and 'ram-09' in objective
    assert 'Preserve the existing R: volume' in objective
    assert saved['root']['payload']['requirements'] == ['RAM-1']
    assert record['status'] != 'completed'
    assert {(job['kind'], job['status']) for job in saved['jobs'] if job['parent_job_id']} == {
        ('MANIFEST_GENERATOR', 'PENDING'), ('SYNTHESIS', 'BLOCKED')}
    contract = saved['root']['payload']['governing_requirements']['contract']
    assert 'bounded-owned-wbs-declarations' in contract['manifest_completion']['requirements']
    assert all('wbs_tasks_defined' not in job['payload'] for job in saved['jobs'])


@pytest.mark.parametrize('root_state,coding_state,expected', [
    ('BLOCKED', 'PLANNING_HOLD', 'blocked'),
    ('BLOCKED', 'READY_TO_INTEGRATE', 'blocked'),
    ('FAILED', 'CANCELLED', 'cancelled'),
    ('IN_PROGRESS', 'PLANNING', 'queued'),
    ('IN_PROGRESS', 'COMPLETED', 'running'),
    ('FAILED', 'TESTING', 'failed'),
])
def test_actual_coding_response_shape_projects_holds_and_cancellation_honestly(job_environment, root_state, coding_state, expected):
    create, _ = job_environment
    manager = create()
    # A physical HTTP response in the actual controller coding_workflow shape;
    # this verifies state projection, not execution or native model evidence.
    manager.endpoint.controller.coding_workflow = lambda _: {
        'workflow_id': 'shape', 'status': root_state, 'coding': {'status': coding_state},
        'events': [], 'jobs': [], 'artifacts': []}
    record = manager.status('code:shape')
    assert record['status'] == expected and record['controller_state'] == coding_state
    result = manager.result('code:shape')
    assert result['content'] is None and 'workflow' not in result


@pytest.mark.parametrize('model',['sol','gpt-6-astra','claude-fable-5-1'])
def test_every_explicit_model_pin_rejected_before_submission(job_environment,model):
    create,_=job_environment; manager=create()
    with pytest.raises(ValueError,match='pinning is forbidden'): manager.submit('Task',model=model)
    assert not manager.requests


def test_missing_controller_fails_closed_without_native_execution(job_environment):
    create,_=job_environment; manager=create(controller_port=0,controller_token_file='')
    with pytest.raises(RuntimeError,match='job-board routing is required'): manager.submit('Task')
    assert manager.health()['ready'] is False


def test_no_implicit_workspace_registration_or_override(job_environment,tmp_path):
    create,workspace=job_environment; manager=create(projects=())
    with pytest.raises(ValueError,match='registered pipeline project'): manager.submit('Task')
    manager=create()
    with pytest.raises(ValueError,match='workspace_roots'): manager.submit('Task',str(tmp_path))
    assert not manager.requests


def test_bad_controller_auth_cannot_submit(job_environment,tmp_path):
    create,_=job_environment; token=tmp_path/'bad-token';token.write_text('wrong')
    manager=create(controller_token_file=str(token))
    with pytest.raises(RuntimeError,match='Unauthorized'): manager.submit('Task')
    assert not manager.requests
    assert manager.endpoint.controller.store.list_workflows() == []


@pytest.mark.parametrize('identifier',['../escape','code:../escape','code:x/../../health','x','plan:'])
def test_job_id_cannot_change_controller_operation(job_environment,identifier):
    create,_=job_environment;manager=create()
    with pytest.raises(ValueError,match='workflow ID'):manager.status(identifier)


def test_bridge_restart_does_not_interrupt_controller_work(job_environment):
    create,_=job_environment;manager=create()
    record=manager.submit_node('MANIFEST_GENERATOR', {'objective':'Keep durable work','requirements':['R1'],
        'chapter_count':1}, 'persistent-plan')
    before=manager.status(record['job_id']); manager.close()
    restarted=create()
    assert restarted.status(record['job_id'])==before
    assert restarted.result(record['job_id'])['content'] is None


def test_result_paging_requires_real_controller_completion(job_environment):
    create,_=job_environment;manager=create()
    # An explicit returned-data fixture checks transport, never native acceptance.
    workflow={'workflow_id':'fixture','state':'COMPLETED','evidence_scope':'controller-response-fixture'}
    manager.endpoint.controller.coding_workflow=lambda _:workflow
    record=manager.result('code:fixture',offset=2,limit=12)
    content=json.dumps(workflow,ensure_ascii=False,sort_keys=True)
    assert record['content']==content[2:14] and record['next_offset']==14
    assert 'workflow' not in record and record['total_characters'] == len(content)
    with pytest.raises(ValueError,match='paging'):manager.result('code:fixture',limit=0)
