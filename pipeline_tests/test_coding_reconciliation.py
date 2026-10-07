"""Real SQLite/Git gates; native receipt content is explicitly a protocol fixture."""
from copy import deepcopy
import json

import pytest

from cochem_pipeline.coding import digest, observed_changes
from cochem_pipeline.coding_plan import validate_plan
from cochem_pipeline.coding_reconciliation import validate_reconciliation
from cochem_pipeline.coding_acceptance import validate_srs_wbs_reconciliations
from pipeline_tests.test_coding_plan import inputs
from pipeline_tests.test_coding_workflow import CodingFixture


@pytest.mark.parametrize('field,value,reason',[
    ('estimated_context_lines',101,'planned_context_outside_20_100'),
    ('estimated_context_lines',19,'planned_context_outside_20_100'),
    ('estimated_added_deleted_lines',101,'planned_source_and_test_diff_exceeds_100'),
])
def test_oversize_plan_produces_hashed_further_fracture_proposal(tmp_path,field,value,reason):
    project,files,raw,requirements=inputs(tmp_path)
    raw['leaves'][0].pop('estimated_changed_lines')
    raw['leaves'][0]['estimated_context_lines']=20
    raw['leaves'][0][field]=value
    plan=validate_plan(raw,project,files,requirements)
    estimates=plan['leaf_size_estimates']
    assert estimates['all_dispatch_ready'] is False
    row=estimates['leaves'][0]
    assert reason in row['blocking_reasons']
    assert row['physical_source_lines']==200
    assert row['physical_diff_verified'] is False
    assert row['remediation']['preserve_requirement_ids']==['R1']
    assert json.loads(plan['artifacts']['LeafSizeEstimates.json'])==estimates


def test_fifteen_small_test_files_are_not_rejected_by_claude_concurrency_limit(tmp_path):
    project,files,_,_=inputs(tmp_path)
    after={**files,**{f'tests/test_part{index}.py':b'# physical test fragment\n' for index in range(15)}}
    assert len(observed_changes(files,after,files,project,tests_only=True))==15


def pending_reconciliation(tmp_path):
    case=CodingFixture(tmp_path)
    case.author();case.test(passed=False);case.edit();case.test(passed=True)
    while case.state()['status']!='RECONCILING_SRS':
        if case.state()['status']=='FINAL_TESTING':
            case.test(passed=True)
        else:
            node=case.claim('CODE_REVIEW')
            case.complete_native(node,case.review_output(node),{'source_snapshot_sha256':case.state()['current_snapshot']})
    return case,case.claim('CODE_REVIEW')


@pytest.mark.parametrize('mutation',['missing_requirement','wrong_chapter','wrong_wbs','wrong_test',
                                   'stale_manifest','self_provider','divergence'])
def test_no_final_approval_from_unsupported_or_divergent_review(tmp_path,mutation):
    case,node=pending_reconciliation(tmp_path)
    output=case.review_output(node)
    receipt=case.native_receipt(node,output)
    if mutation=='missing_requirement': output['requirements_checked']=[]
    elif mutation=='wrong_chapter': output['requirements_checked'][0]['chapter_ids']=['ch99']
    elif mutation=='wrong_wbs': output['requirements_checked'][0]['wbs_leaf_id']='L99'
    elif mutation=='wrong_test': output['requirements_checked'][0]['test_ids']=['T99']
    elif mutation=='stale_manifest': output['reconciliation_manifest_sha256']='a'*64
    elif mutation=='self_provider': receipt['provider']=node['payload']['producer']['provider']
    else: output['divergences']=['The implementation silently drops its required boundary behavior.']
    receipt['output_sha256']=digest(output)
    with pytest.raises(ValueError):
        validate_reconciliation(output,node['payload']['reconciliation_manifest'],receipt,node['payload']['producer'])
    assert case.state()['status']=='RECONCILING_SRS'
    assert not any(job['kind']=='CODE_INTEGRATE' for job in case.store.coding_workflow(case.workflow_id)['jobs'])


def test_rejected_reconciliation_consumes_cycle_and_routes_back_for_remediation(tmp_path):
    case,node=pending_reconciliation(tmp_path)
    output=case.review_output(node,approved=False)
    case.complete_native(node,output,{'source_snapshot_sha256':case.state()['current_snapshot']})
    assert case.state()['cycle']==2
    assert case.state()['status']=='EDITING'
    correction=case.claim('CODE_EDIT')
    assert 'SRS/WBS reconciliation rejected divergence' in correction['payload']['previous_failure']
    assert not any(job['kind']=='CODE_INTEGRATE' for job in case.store.coding_workflow(case.workflow_id)['jobs'])


def test_git_intent_requires_unchanged_completed_reconciliation(tmp_path):
    case=CodingFixture(tmp_path)
    node=case.ready()
    with case.store._write() as conn:
        state=case.store._coding_state(conn,case.workflow_id)
        state['reconciliation']['manifest_sha256']='a'*64
        case.store._save_coding(conn,case.workflow_id,state)
    with pytest.raises(ValueError,match='SRS/WBS reconciliation'):
        case.stage(node)
    assert case.store.coding_workflow(case.workflow_id)['integration_intents']==[]


def test_acceptance_joins_reconciliation_to_each_actual_git_result(tmp_path):
    case=CodingFixture(tmp_path,two_leaves=True)
    case.integration(case.ready())
    case.integration(case.ready(value=1))
    workflow=case.store.coding_workflow(case.workflow_id)
    state=workflow['coding'];evidence={row['job_id']:row for row in workflow['evidence']}
    validate_srs_wbs_reconciliations(workflow['jobs'],state['plan'],state['chunks'],evidence)
    altered=deepcopy(state['chunks']);altered[1]['reconciliation']=altered[0]['reconciliation']
    with pytest.raises(ValueError,match='exact SRS/WBS reconciliation'):
        validate_srs_wbs_reconciliations(workflow['jobs'],state['plan'],altered,evidence)


def test_oversize_plan_audit_cannot_dispatch_coding_before_controller_refracture(tmp_path):
    case=CodingFixture(tmp_path)
    node=case.claim('CODE_PLAN')
    raw={'goal':'Correct the answer preserving all original module content',
         'srs':{'skeleton':'The answer must equal forty-two.','chapters':[{'id':'ch01','title':'Answer',
              'text':'The answer must equal forty-two.','requirement_ids':['R1']}]},
         'acceptance_criteria':[{'id':'AC1','statement':'Answer equals forty-two.','requirement_ids':['R1'],'test_ids':['T1']}],
         'test_cases':[{'id':'T1','name':'test_regression','asserts':'ANSWER equals 42.','criteria_ids':['AC1']}],
         'leaves':[{'id':'L1','objective':'Correct the answer','file_targets':['src/answer.py'],
             'estimated_context_lines':150,'estimated_added_deleted_lines':7,'requirement_ids':['R1'],'criteria_ids':['AC1'],'dependencies':[]}]}
    plan=validate_plan(raw,case.project,case.snapshot.files,['REQ-1'])
    case.complete_native(node,raw,{'plan':plan})
    review=case.claim('CODE_PLAN_REVIEW')
    case.complete_native(review,{'verdict':'PASS','plan_sha256':plan['plan_sha256'],
        'requirements_checked':['R1'],'artifact_hashes':plan['artifact_hashes'],'findings':[]},
        {'source_snapshot_sha256':case.state()['current_snapshot']})
    assert case.state()['status']=='REVISING_PLAN'
    assert case.state()['planning_revision']==1
    revision=case.claim('CODE_PLAN')
    assert revision['payload']['planning_feedback']['verdict']=='REVISE'
    assert 'LeafSizeEstimates.json' in revision['payload']['planning_feedback']['findings'][0]['artifacts']
    assert not any(job['kind']=='CODE_TEST_AUTHOR' for job in case.store.coding_workflow(case.workflow_id)['jobs'])


def test_captured_specification_hash_is_real_and_cannot_drift_during_planning(tmp_path,monkeypatch):
    import importlib.resources
    import hashlib
    installed=importlib.resources.files('cochem_pipeline').joinpath('specification')
    package=tmp_path/'installed-package'
    assets=package/'specification';assets.mkdir(parents=True)
    for name in ('4.2.7_SRS.md','SRS_ADDENDUM_4.2.7.md'):
        (assets/name).write_bytes(installed.joinpath(name).read_bytes())
    monkeypatch.setattr(importlib.resources,'files',lambda package_name:package)
    case=CodingFixture(tmp_path)
    captured=case.state()['planning_evidence']
    actual=(assets/'4.2.7_SRS.md').read_bytes()
    assert captured['specification_sha256']==hashlib.sha256(actual).hexdigest()
    assert captured['owner_amendments'][0]['revision']=='model-routing-2026-10-07'
    (assets/'4.2.7_SRS.md').write_bytes(actual+b'\nUnratified physical asset change.\n')
    with pytest.raises(ValueError,match='authority drifted'):
        case.plan()
    assert case.state()['status']=='PLANNING'


def test_final_review_context_cannot_omit_sealed_tests_behind_large_documentation(tmp_path):
    from cochem_pipeline.coding import source_context
    case,node=pending_reconciliation(tmp_path)
    files=case.store.coding_files(case.state()['current_snapshot'])
    files['docs/large.md']=b'Documentation.\n'*5000
    context=source_context(files,node['payload'],case.project,max_bytes=4096)
    tests={item['path'] for item in node['payload']['reconciliation_manifest']['sealed_test_identities'].values()}
    assert tests.issubset(context['files'])
    assert 'docs/large.md' not in context['files']
    assert node['payload']['reconciliation_manifest']['file_reviews']
    assert all(item['passed'] for item in node['payload']['reconciliation_manifest']['test_results'])


def test_final_code_approval_reconciles_all_chapters_and_wbs_leaves(tmp_path):
    case=CodingFixture(tmp_path,two_leaves=True)
    case.integration(case.ready())
    integration=case.ready(value=1)
    workflow=case.store.coding_workflow(case.workflow_id)
    review=next(job for job in workflow['jobs'] if job['job_id']==case.state()['reconciliation']['job_id'])
    manifest=review['payload']['reconciliation_manifest']
    assert manifest['reconciliation_scope']=='workflow'
    assert [leaf['id'] for leaf in manifest['wbs_leaves']]==['L1','L2']
    assert {test['id'] for test in manifest['test_cases']}=={'T1','T2'}
    assert set(manifest['sealed_test_identities'])=={'test_regression','test_helper_regression'}
    assert len(manifest['prior_leaf_reconciliations'])==1
    assert review['output']['requirements_checked'][0]['wbs_leaf_ids']==['L1','L2']
    assert integration['payload']['reconciliation_sha256']==digest(case.state()['reconciliation'])


def test_last_physical_green_gate_cannot_skip_an_earlier_leafs_sealed_regression(tmp_path):
    case=CodingFixture(tmp_path,two_leaves=True)
    case.integration(case.ready())
    case.author();case.test(passed=False);case.edit(value=1);case.test(passed=True)
    while case.state()['status']!='FINAL_TESTING':
        review=case.claim('CODE_REVIEW')
        case.complete_native(review,case.review_output(review),{'source_snapshot_sha256':case.state()['current_snapshot']})
    node=case.claim('CODE_TEST')
    assert {item['name'] for item in node['payload']['test_cases']}=={'test_regression','test_helper_regression'}
    evidence=case.test_evidence(node,passed=True)
    evidence['commands'][0]['junit_cases']=[case for case in evidence['commands'][0]['junit_cases'] if case['name']=='test_helper_regression']
    with pytest.raises(ValueError,match='every planned leaf test'):
        case.complete_controller(node,{'passed':True,'source_snapshot_sha256':node['payload']['snapshot_sha256'],
            'test_receipt_sha256':digest(evidence),'phase':'final'},evidence)
    assert case.state()['status']=='FINAL_TESTING'


def test_missing_change_estimate_is_a_revision_proposal_not_dispatch_permission(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    del raw['leaves'][0]['estimated_added_deleted_lines']
    plan=validate_plan(raw,project,files,requirements)
    estimate=plan['leaf_size_estimates']['leaves'][0]
    assert estimate['dispatch_ready'] is False
    assert estimate['blocking_reasons']==['missing_source_and_test_change_estimate']
    assert estimate['remediation']['action']=='further_fracture_before_dispatch'
