"""Physical-file/SQLite planning contracts, not claims of live model execution."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from cochem_pipeline.coding import CodingCoordinator
from cochem_pipeline.coding_plan import validate_plan
from cochem_pipeline.planning_governance import (normalize_policy,validate_registration,
    validate_url,validate_external_research,planning_readiness)
from pipeline_tests.test_coding_plan import inputs
from pipeline_tests.test_coding_workflow import CodingFixture,git


def registered(tmp_path):
    """Explicit source-registration fixture; these are not missing canonical clauses."""
    documents={}; policy={}
    for name,count in (('protocol',7),('method_matrix',8)):
        clauses=[{'id':f'Fixture-stage-{i}' if name=='protocol' else f'M-{i}',
                  'quote':f'Fixture {name} source clause {i}: verify the declared artifact.'}
                 for i in range(1,count+1)]
        text='\n'.join(clause['quote'] for clause in clauses)+'\n'
        path=tmp_path/'.planning'/f'{name}.md'; path.parent.mkdir(exist_ok=True)
        path.write_text(text,encoding='utf-8')
        relative=path.relative_to(tmp_path).as_posix()
        documents[relative]=path.read_bytes()
        policy[name]={'path':relative,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'clauses':clauses}
    policy['research_sources']=[{'id':'source-a','url':'https://www.python.org/doc/'},
                                {'id':'source-b','url':'https://docs.pytest.org/en/stable/'}]
    return normalize_policy(policy),documents


def test_registered_stage_order_and_method_clauses_bind_hidden_physical_source_bytes(tmp_path):
    policy,files=registered(tmp_path)
    result=validate_registration(policy,files)
    assert result['registration_verified'] is True
    assert result['stage_execution_verified'] is False
    assert len(result['documents']['protocol']['clauses'])==7
    files[policy['method_matrix']['path']]+=b'altered source'
    with pytest.raises(ValueError,match='bytes changed'):
        validate_registration(policy,files)


def test_production_acceptance_cannot_promote_component_or_source_registration_to_execution(tmp_path):
    from cochem_pipeline.coding_acceptance import validate_production_planning
    with pytest.raises(ValueError,match='canonical seven-stage protocol'):
        validate_production_planning({'project':{}},{})
    policy,files=registered(tmp_path)
    state={'project':{'planning':policy},'planning_evidence':validate_registration(policy,files)}
    with pytest.raises(ValueError,match='no verified controller binding'):
        validate_production_planning(state,{})
    assert state['planning_evidence']['stage_execution_verified'] is False


@pytest.mark.parametrize('mutation',[
    lambda p:p['protocol']['clauses'].reverse(),
    lambda p:p['protocol']['clauses'][0].update(quote='This invented clause is not present in the source.'),
    lambda p:p['method_matrix']['clauses'][0].update(id='M-9'),
    lambda p:p['protocol'].update(path='../outside.md'),
])
def test_source_registration_rejects_invented_reordered_or_escaping_clauses(tmp_path,mutation):
    policy,files=registered(tmp_path); mutation(policy)
    with pytest.raises(ValueError):
        validate_registration(policy,files)


def test_method_matrix_links_actual_plan_artifacts_and_is_in_the_audited_hash_set(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    policy,documents=registered(tmp_path); files.update(documents)
    project=replace(project,planning=policy)
    raw['method_matrix']=[{'id':f'M-{i}','artifacts':['srs/ch01.md'],'requirement_ids':['R1']} for i in range(1,9)]
    plan=validate_plan(raw,project,files,requirements)
    matrix=json.loads(plan['artifacts']['MethodMatrix.json'])
    assert matrix['source']['sha256']==policy['method_matrix']['sha256']
    assert matrix['linked_artifact_hashes']['srs/ch01.md']==plan['artifact_hashes']['srs/ch01.md']
    assert 'MethodMatrix.json' in plan['artifact_hashes']
    raw['method_matrix'][0]['artifacts']=['nonexistent.md']
    with pytest.raises(ValueError,match='actual plan artifacts'):
        validate_plan(raw,project,files,requirements)


@pytest.mark.parametrize('url',['http://example.com/','https://user:secret@example.com/',
                              'https://127.0.0.1/','https://[::1]/','https://example.com:8443/',
                              'https://localhost/','https://example.com/#fragment'])
def test_external_research_rejects_unsafe_or_unregistered_transport_shapes(url):
    with pytest.raises(ValueError):
        validate_url(url)


def research_case():
    texts=['A physically verified technical reference explains the first invariant.',
           'A second independently retrieved technical reference documents the boundary.']
    sources=[{'id':f'source-{i}','url':f'https://source{i}.example/spec','text':text,
              'sha256':hashlib.sha256(text.encode()).hexdigest(),'http_status':200,'transport':'https'}
             for i,text in enumerate(texts)]
    output={'confidence':1,'external_sources':[{'source_id':source['id'],'quote':source['text'],
                                               'requirement_ids':['R1']} for source in sources]}
    return sources,output


def test_research_confidence_is_derived_from_bound_quotes_and_coverage():
    sources,output=research_case()
    verified=validate_external_research(output,sources,['R1'])
    assert verified['research_confidence']['passed'] is True
    assert verified['research_confidence']['distinct_sources']==2
    output['confidence']=0
    assert validate_external_research(output,sources,['R1'])==verified
    output['external_sources'][1]['source_id']=sources[0]['id']
    output['external_sources'][1]['quote']=sources[0]['text']
    with pytest.raises(ValueError,match='distinct sources'):
        validate_external_research(output,sources,['R1'])


def test_forged_research_quote_or_changed_bytes_cannot_pass_a_confident_answer():
    sources,output=research_case()
    output['external_sources'][0]['quote']='Invented reference that never existed in the downloaded bytes.'
    with pytest.raises(ValueError,match='HTTPS evidence'):
        validate_external_research(output,sources,['R1'])
    sources,output=research_case(); sources[0]['text']+=' tampered'
    with pytest.raises(ValueError,match='HTTPS evidence'):
        validate_external_research(output,sources,['R1'])


@pytest.mark.parametrize('register',[False,True])
def test_production_submission_requires_sources_and_verified_seven_stage_binding(tmp_path,register):
    fixture=CodingFixture(tmp_path)
    if register:
        git(fixture.repository,'checkout','delivery')
        policy,_=registered(fixture.repository)
        git(fixture.repository,'add','.planning')
        git(fixture.repository,'commit','-m','Explicit source-registration fixture')
        fixture.project=replace(fixture.project,planning=policy)
    coordinator=CodingCoordinator.__new__(CodingCoordinator)
    coordinator.config=SimpleNamespace(coding_projects={'project':fixture.project},
        ramdisk=SimpleNamespace(enabled=True),docker=fixture.docker,git_executable=fixture.git_executable)
    coordinator.store=fixture.store
    held=coordinator.submit('project','A production request',['REQ-1'])
    assert held['status']=='BLOCKED'
    assert held['coding']['status']=='PLANNING_HOLD'
    assert ('verified controller binding' if register else 'canonical seven-stage protocol') in held['coding']['planning_hold']
    assert not any(job['kind']=='CODE_PLAN' for job in held['jobs'])
    assert len(fixture.store.list_workflows())==2
    readiness=planning_readiness(coordinator.config.coding_projects)
    assert readiness['ready'] is False
    assert readiness['projects'][0]['source_registration_configured'] is register
    assert readiness['projects'][0]['stage_execution_verified'] is False
    assert readiness['projects'][0]['reason']==held['coding']['planning_hold']


def first_plan(fixture,tmp_path):
    _,_,raw,_=inputs(tmp_path/'plan-input')
    raw['leaves'][0]['file_targets']=['src/answer.py']
    node=fixture.claim('CODE_PLAN')
    plan=validate_plan(raw,fixture.project,fixture.snapshot.files,['REQ-1'])
    fixture.complete_native(node,raw,{'plan':plan})
    return raw,plan


def reject_plan(fixture,plan):
    node=fixture.claim('CODE_PLAN_REVIEW')
    output={'verdict':'REVISE','plan_sha256':plan['plan_sha256'],'requirements_checked':['R1'],
            'artifact_hashes':plan['artifact_hashes'],'findings':[
                {'severity':'HIGH','issue':'The chapter must explain the measured boundary behavior.',
                 'artifacts':['srs/ch01.md']}]}
    fixture.complete_native(node,output,{'source_snapshot_sha256':fixture.state()['current_snapshot']})


def test_nonpass_planning_audit_creates_budgeted_revision_and_no_tdd_dispatch(tmp_path):
    fixture=CodingFixture(tmp_path); (tmp_path/'plan-input').mkdir()
    raw,plan=first_plan(fixture,tmp_path); reject_plan(fixture,plan)
    assert fixture.state()['status']=='REVISING_PLAN'
    assert fixture.state()['phase_ledger']==[]
    revised=fixture.claim('CODE_PLAN')
    assert revised['payload']['previous_plan']==plan
    raw['srs']['chapters'][0]['text']+=' Verify the measured boundary and record its actual exception.'
    plan=validate_plan(raw,fixture.project,fixture.snapshot.files,['REQ-1'])
    fixture.complete_native(revised,raw,{'plan':plan})
    audit=fixture.claim('CODE_PLAN_REVIEW')
    fixture.complete_native(audit,{'verdict':'PASS','plan_sha256':plan['plan_sha256'],
        'requirements_checked':['R1'],'artifact_hashes':plan['artifact_hashes'],'findings':[]},{})
    assert len(fixture.state()['planning_history'])==4
    assert [row['phase'] for row in fixture.state()['phase_ledger']]==['P1']
    # Verify the acceptance relationship on real SQLite revision history. This
    # helper does not certify the fixture as a native model or Docker execution.
    from cochem_pipeline.coding_acceptance import validate_audit_jobs
    from cochem_pipeline.service import public_workflow
    snapshot=public_workflow(fixture.store.coding_workflow(fixture.workflow_id))
    approved=validate_audit_jobs(snapshot['jobs'],plan,{},
                                 {row['job_id']:row for row in snapshot['evidence']})
    assert approved==fixture.state()['plan_review']
    assert fixture.claim('CODE_RESEARCH')['payload']['plan_sha256']==plan['plan_sha256']


def test_unchanged_plan_revision_is_a_durable_hold_not_an_infinite_review_loop(tmp_path):
    fixture=CodingFixture(tmp_path); (tmp_path/'plan-input').mkdir()
    raw,plan=first_plan(fixture,tmp_path); reject_plan(fixture,plan)
    node=fixture.claim('CODE_PLAN'); fixture.complete_native(node,raw,{'plan':plan})
    assert fixture.state()['status']=='PLANNING_HOLD'
    assert fixture.store.get(fixture.workflow_id)['status']=='BLOCKED'
    assert fixture.state()['planning_history'][-1]['no_progress'] is True
    assert fixture.store.claim('no-implementation',worker_slot='worker-one') is None


def test_revisited_plan_hash_is_bound_to_its_actual_final_producer(tmp_path):
    from cochem_pipeline.coding_acceptance import validate_audit_jobs
    from cochem_pipeline.service import public_workflow
    fixture=CodingFixture(tmp_path); (tmp_path/'plan-input').mkdir()
    raw,plan=first_plan(fixture,tmp_path); original=deepcopy(raw); first_hash=plan['plan_sha256']
    reject_plan(fixture,plan)
    for revised in (False,True):
        node=fixture.claim('CODE_PLAN')
        raw=deepcopy(original)
        if not revised:
            raw['srs']['chapters'][0]['text']+=' Explain the requested alternative measurement boundary.'
        plan=validate_plan(raw,fixture.project,fixture.snapshot.files,['REQ-1'])
        fixture.complete_native(node,raw,{'plan':plan})
        if not revised:
            reject_plan(fixture,plan)
    assert plan['plan_sha256']==first_hash
    audit=fixture.claim('CODE_PLAN_REVIEW')
    assert audit['payload']['planner_job_id']==node['job_id']
    fixture.complete_native(audit,{'verdict':'PASS','plan_sha256':plan['plan_sha256'],
        'requirements_checked':['R1'],'artifact_hashes':plan['artifact_hashes'],'findings':[]},{})
    snapshot=public_workflow(fixture.store.coding_workflow(fixture.workflow_id))
    approved=validate_audit_jobs(snapshot['jobs'],plan,{},
        {row['job_id']:row for row in snapshot['evidence']})
    assert approved==fixture.state()['plan_review']
    assert len(fixture.state()['planning_history'])==6


def test_planning_revision_budget_ends_in_hold_with_all_native_history_retained(tmp_path):
    fixture=CodingFixture(tmp_path); (tmp_path/'plan-input').mkdir()
    raw,plan=first_plan(fixture,tmp_path)
    for revision in range(6):
        reject_plan(fixture,plan)
        if revision==5:
            break
        node=fixture.claim('CODE_PLAN')
        raw['srs']['chapters'][0]['text']+=f' Additional boundary finding {revision} is addressed.'
        plan=validate_plan(raw,fixture.project,fixture.snapshot.files,['REQ-1'])
        fixture.complete_native(node,raw,{'plan':plan})
    assert fixture.state()['planning_revision']==5
    assert fixture.state()['status']=='PLANNING_HOLD'
    assert len(fixture.state()['planning_history'])==12
    assert fixture.state()['phase_ledger']==[]
