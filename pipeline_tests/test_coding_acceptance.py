"""Live acceptance must not relabel SQLite/Git protocol fixtures as inference."""
import pytest
from copy import deepcopy

from cochem_pipeline.coding import digest
from cochem_pipeline.coding_acceptance import (
    validate_coding_workflow, validate_phase_ledger, validate_audit_jobs, validate_final_test, validate_phase_tests,
)
from cochem_pipeline.service import public_workflow
from pipeline_tests.test_coding_workflow import CodingFixture


def test_unfinished_submission_has_no_live_acceptance(tmp_path):
    case=CodingFixture(tmp_path)
    with pytest.raises(ValueError,match='not completed'):
        validate_coding_workflow(case.store.coding_workflow(case.workflow_id))


def test_real_git_and_sqlite_completion_does_not_turn_protocol_fixtures_into_live_models(tmp_path):
    case=CodingFixture(tmp_path)
    node=case.ready()
    case.integration(node)
    snapshot=case.store.coding_workflow(case.workflow_id)
    assert snapshot['status']=='COMPLETED'
    with pytest.raises(ValueError,match='fixtures are not live'):
        validate_coding_workflow(snapshot)


def test_uncertain_git_publication_prevents_acceptance_even_with_terminal_state(tmp_path):
    case=CodingFixture(tmp_path)
    node=case.ready()
    case.integration(node)
    snapshot=case.store.coding_workflow(case.workflow_id)
    snapshot['integration_reconciliation_required']=True
    with pytest.raises(ValueError,match='reconciliation'):
        validate_coding_workflow(snapshot)


@pytest.fixture(scope='module')
def protocol_snapshot(tmp_path_factory):
    """Actual SQLite/Git fixture; never changed into native acceptance evidence."""
    case=CodingFixture(tmp_path_factory.mktemp('coding-acceptance-relationships'))
    case.integration(case.ready())
    return public_workflow(case.store.coding_workflow(case.workflow_id))


def relationship_arguments(snapshot):
    state=snapshot['coding']; plan=state['plan']
    phases=validate_phase_ledger(state['phase_ledger'],plan['fracture_manifest']['topological_order'])
    return snapshot['jobs'],plan,phases,{row['job_id']:row for row in snapshot['evidence']}


def test_phase_and_audit_relationship_helpers_do_not_certify_protocol_fixture_as_native(protocol_snapshot):
    snapshot=deepcopy(protocol_snapshot)
    approved=validate_audit_jobs(*relationship_arguments(snapshot))
    assert approved==snapshot['coding']['plan_review']
    assert 'accepted' not in approved
    with pytest.raises(ValueError,match='fixtures are not live'):
        validate_coding_workflow(snapshot)


@pytest.mark.parametrize('mutation',['failed_final','unknown_outcome','missing_plan','missing_research','skipped_test','wrong_skip'])
def test_phase_names_cannot_substitute_for_executed_successful_final_gates(protocol_snapshot,mutation):
    snapshot=deepcopy(protocol_snapshot); records=snapshot['coding']['phase_ledger']
    if mutation=='failed_final':
        next(row for row in records if row['phase']=='P10')['outcome']='FAILED'
    elif mutation=='unknown_outcome': records[-1]['outcome']='MODEL_SAYS_DONE'
    elif mutation=='missing_plan': records[:]=[row for row in records if row['phase']!='P1']
    elif mutation=='missing_research': records[:]=[row for row in records if row['phase']!='P2']
    elif mutation=='skipped_test':
        next(row for row in records if row['phase']=='P5').update(outcome='SKIPPED',reason=None)
    else: next(row for row in records if row['phase']=='P7')['reason']='unreviewed_shortcut'
    with pytest.raises(ValueError): relationship_arguments(snapshot)


def test_historical_failed_test_and_review_are_allowed_only_before_successful_final_recovery(protocol_snapshot):
    snapshot=deepcopy(protocol_snapshot)
    for row in snapshot['coding']['phase_ledger']:
        if row['phase'] in {'P5','P6'}: row['outcome']='FAILED'
    assert relationship_arguments(snapshot)[2]['L1']['outcome']=='COMPLETED'
    snapshot['coding']['phase_ledger'].append(deepcopy(next(row for row in snapshot['coding']['phase_ledger'] if row['phase']=='P6')))
    with pytest.raises(ValueError,match='successful final'):
        relationship_arguments(snapshot)


@pytest.mark.parametrize('mutation',['no_planner','self_plan_review','self_file_review','different_diff',
                                    'unapproved_final','missing_final_test_file','wrong_plan_evidence'])
def test_audit_relationship_helpers_reject_self_review_detached_hashes_and_missing_files(protocol_snapshot,mutation):
    snapshot=deepcopy(protocol_snapshot)
    jobs,plan,phases,evidence=relationship_arguments(snapshot)
    if mutation=='no_planner': jobs[:]=[job for job in jobs if job['kind']!='CODE_PLAN']
    elif mutation=='wrong_plan_evidence':
        planner=next(job for job in jobs if job['kind']=='CODE_PLAN')
        evidence[planner['job_id']]['evidence']['plan']={}
    else:
        kind='CODE_PLAN_REVIEW' if mutation=='self_plan_review' else 'CODE_REVIEW'
        job=next(job for job in jobs if job['kind']==kind and (kind!='CODE_REVIEW' or job['payload']['phase']=='P10'))
        if mutation in {'self_plan_review','self_file_review'}:
            job['receipt'].update(provider=job['payload']['producer']['provider'],requested_model=job['payload']['producer']['model'])
        elif mutation=='different_diff': job['output']['diff_sha256']='f'*64
        elif mutation=='unapproved_final': job['output']['approved']=False
        else:
            jobs[:]=[row for row in jobs if not (row['kind']=='CODE_REVIEW' and row['payload']['phase']=='P10'
                                                and row['payload']['file']['path'].startswith('tests/'))]
    with pytest.raises(ValueError): validate_audit_jobs(jobs,plan,phases,evidence)


def docker_metadata_case():
    """Pure helper input; no process is launched and no acceptance report issued."""
    return {'kind':'docker-test-execution','container_id':'a'*64,'passed':True,
            'cleanup_verified':True,'source_verified':True,'commands':[{'kind':'pytest','passed':True,
            'exit_code':0,'junit':{'tests':1,'failures':0,'errors':0,'skipped':0,'passed':1},
            'junit_cases':[{'name':'test_contract','status':'passed'}]}]}


def test_docker_metadata_helper_accepts_consistent_counts_without_emitting_certification():
    assert validate_final_test(docker_metadata_case()) is None


@pytest.mark.parametrize('mutation',['fixture','no_container','all_skipped','case_contradiction','timeout','bool_exit'])
def test_docker_metadata_helper_cannot_certify_fixtures_or_skipped_execution(mutation):
    value=docker_metadata_case(); command=value['commands'][0]
    if mutation=='fixture': value['execution_kind']='controller-storage-contract-fixture'
    elif mutation=='no_container': value.pop('container_id')
    elif mutation=='all_skipped':
        command['junit'].update(skipped=1,passed=0)
        command['junit_cases'][0]['status']='skipped'
    elif mutation=='case_contradiction': command['junit_cases'][0]['status']='skipped'
    elif mutation=='timeout': command['timed_out']=True
    else: command['exit_code']=False
    with pytest.raises(ValueError): validate_final_test(value)


def identity_contract_case(case_count=1):
    """Synthetic identity helper input; never a live acceptance report."""
    value=docker_metadata_case()
    value.update(job_id='test-stage',source={'files':{
        'tests/test_contract.py':{'sha256':'c'*64,'size':64,'executable':False}}})
    value['source']['sha256']=digest(value['source']['files'])
    command=value['commands'][0]
    command['junit'].update(tests=case_count,passed=case_count)
    command['junit_cases']=[{'name':f'test_other_{index}','class_name':'tests.test_existing','status':'passed'}
                            for index in range(case_count-1)]
    command['junit_cases'].append({'name':'test_contract[parameter]','class_name':'tests.test_contract.TestScope','status':'passed'})
    job={'job_id':'test-stage','kind':'CODE_TEST','status':'COMPLETED',
         'payload':{'phase':'final','active_leaf':{'id':'L1'},'test_cases':[{'name':'test_contract'}],
                    'test_identities':{'test_contract':{'name':'test_contract','path':'tests/test_contract.py',
                         'class_name':'tests.test_contract.TestScope','file_sha256':'c'*64}}},
         'output':{'test_receipt_sha256':'b'*64}}
    return ({'L1':{'evidence':{'test_receipt_sha256':'b'*64}}},[job],
            {'test-stage':{'sha256':'b'*64,'evidence':value}})


def test_more_than_one_thousand_cases_retains_and_verifies_last_planned_identity():
    arguments=identity_contract_case(1701)
    assert validate_phase_tests(*arguments) is None
    arguments[2]['test-stage']['evidence']['commands'][0]['junit_cases'][:]=arguments[2]['test-stage']['evidence']['commands'][0]['junit_cases'][:1000]
    with pytest.raises(ValueError,match='case details'):
        validate_phase_tests(*arguments)


@pytest.mark.parametrize('mutation',['no_seal','other_module','other_class','different_source_hash',
                                    'different_sealed_hash','noncanonical_module','skipped_parameter'])
def test_name_only_or_different_source_cannot_satisfy_exact_sealed_regression_identity(mutation):
    arguments=identity_contract_case()
    payload=arguments[1][0]['payload']; value=arguments[2]['test-stage']['evidence']
    identity=payload['test_identities']['test_contract']; command=value['commands'][0]
    if mutation=='no_seal': payload.pop('test_identities')
    elif mutation=='other_module': command['junit_cases'][0]['class_name']='tests.unrelated.TestScope'
    elif mutation=='other_class': command['junit_cases'][0]['class_name']='tests.test_contract.OtherClass'
    elif mutation=='different_source_hash':
        value['source']['files']['tests/test_contract.py']['sha256']='d'*64
        value['source']['sha256']=digest(value['source']['files'])
    elif mutation=='different_sealed_hash': identity['file_sha256']='d'*64
    elif mutation=='noncanonical_module':
        identity['class_name']=command['junit_cases'][0]['class_name']='unrelated.TestScope'
    else:
        command['junit'].update(tests=2,passed=1,skipped=1)
        command['junit_cases'].append({'name':'test_contract[skipped]','class_name':identity['class_name'],'status':'skipped'})
    with pytest.raises(ValueError): validate_phase_tests(*arguments)


def test_physical_job_board_persists_each_test_stages_sealed_source_definitions(protocol_snapshot):
    evidence={row['job_id']:row['evidence'] for row in protocol_snapshot['evidence']}
    tests=[job for job in protocol_snapshot['jobs'] if job['kind']=='CODE_TEST']
    assert {job['payload']['phase'] for job in tests}=={'precode','postedit','final'}
    for job in tests:
        identities=job['payload']['test_identities']
        assert set(identities)=={item['name'] for item in job['payload']['test_cases']}
        for identity in identities.values():
            assert evidence[job['job_id']]['source']['files'][identity['path']]['sha256']==identity['file_sha256']
