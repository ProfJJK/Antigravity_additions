"""Live acceptance must not relabel SQLite/Git protocol fixtures as inference."""
import pytest
from copy import deepcopy

from cochem_pipeline.coding import digest
from cochem_pipeline.coding_acceptance import (
    validate_coding_workflow, validate_phase_ledger, validate_audit_jobs, validate_final_test, validate_phase_tests,
    startup_performance_failures,CodingPerformanceError,
    validate_precode_tests, validate_red_test,
)


@pytest.mark.parametrize('seconds,claimed,expected',[(1.5,True,False),(1.5001,True,True),(None,True,True),
                                                   (1.0,False,True),(float('nan'),True,True)])
def test_startup_sla_verifier_recomputes_deadline_and_reports_missing_measurements(seconds,claimed,expected):
    # Metadata helper input only: this does not certify a native/container run.
    jobs=[{'job_id':'test-stage','kind':'CODE_TEST','status':'COMPLETED','payload':{'phase':'final'}}]
    receipt={'startup_seconds':seconds,'startup_sla_seconds':1.5,'startup_sla_met':claimed,
        'request_started_at':10.,'execution_started_at':10.5,
        'execution_startup_seconds':seconds-.5 if type(seconds) in (int,float) else None,
        'queue_wait_seconds':.3,'handoff_wait_seconds':.2}
    findings=startup_performance_failures(jobs,{'test-stage':{'sha256':'a'*64,'evidence':receipt}})
    assert bool(findings) is expected
    if findings:
        error=CodingPerformanceError(findings)
        assert error.performance_failures==findings and 'Functional coding completed' in str(error)
        assert findings[0]['evidence_sha256']=='a'*64


def test_local_container_speed_cannot_hide_queue_and_handoff_delay():
    jobs=[{'job_id':'test-stage','kind':'CODE_TEST','status':'COMPLETED','payload':{'phase':'precode'}}]
    receipt={'startup_seconds':.2,'startup_sla_seconds':1.5,'startup_sla_met':True,
        'request_started_at':10.,'execution_started_at':13.,'execution_startup_seconds':.2,
        'queue_wait_seconds':2.,'handoff_wait_seconds':1.}
    findings=startup_performance_failures(jobs,{'test-stage':{'evidence':receipt}})
    assert findings[0]['measured_request_to_ready_seconds']==3.2
    assert findings[0]['reason']=='missing_or_inconsistent_startup_measurement'
from cochem_pipeline.service import public_workflow
from pipeline_tests.test_coding_workflow import CodingFixture, pending_noop_refinement


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


@pytest.mark.parametrize('kind',['CODE_PLAN','CODE_PLAN_REVIEW'])
def test_final_plan_requires_one_unambiguous_producer_and_successful_audit(protocol_snapshot,kind):
    snapshot=deepcopy(protocol_snapshot)
    jobs,plan,phases,evidence=relationship_arguments(snapshot)
    duplicate=deepcopy(next(job for job in jobs if job['kind']==kind))
    jobs.append(duplicate)
    with pytest.raises(ValueError,match='planning execution and independent audit'):
        validate_audit_jobs(jobs,plan,phases,evidence)


@pytest.mark.parametrize('mutation',['limit','count','patch','context','window'])
def test_physical_patch_and_context_bounds_are_required_before_native_certification(protocol_snapshot,mutation):
    snapshot=deepcopy(protocol_snapshot);chunk=snapshot['coding']['chunks'][0]
    if mutation=='limit': chunk['staged']['max_changed_lines']=None
    elif mutation=='count': chunk['staged']['changed_lines']=101
    elif mutation=='patch': chunk['staged']['patch_sha256']=''
    elif mutation=='context': chunk['bounds']['context_lines']=19
    else: chunk['bounds']['context_windows'][0]['content_sha256']='invalid'
    with pytest.raises(ValueError,match='diff ceiling|patch evidence|physical context'):
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


def test_historical_failed_test_and_review_are_allowed_only_before_successful_final_recovery(tmp_path):
    case=CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    case.edit()
    case.test(passed=False)
    case.reviews(approved=False,objective_satisfied=False)
    case.edit()
    case.test(passed=True)
    case.reviews()
    snapshot=public_workflow(case.store.coding_workflow(case.workflow_id))
    assert {row['phase'] for row in snapshot['coding']['phase_ledger'] if row['outcome']=='FAILED'}=={'P5','P6'}
    assert relationship_arguments(snapshot)[2]['L1']['outcome']=='COMPLETED'
    snapshot['coding']['phase_ledger'].append(deepcopy(next(row for row in snapshot['coding']['phase_ledger'] if row['phase']=='P6')))
    with pytest.raises(ValueError,match='successful final'):
        relationship_arguments(snapshot)


@pytest.mark.parametrize('mutation',['reversed_phases','planning_last','research_after_red','empty_self_hashed',
                                    'false_red','preservation','duplicate_planning'])
def test_phase_ledger_rejects_unordered_or_unsubstantiated_phases(protocol_snapshot,mutation):
    snapshot=deepcopy(protocol_snapshot); records=snapshot['coding']['phase_ledger']
    if mutation=='reversed_phases': records[1:-1]=reversed(records[1:-1])
    elif mutation=='planning_last': records.append(records.pop(0))
    elif mutation=='research_after_red': records[1],records[2]=records[2],records[1]
    elif mutation=='duplicate_planning': records.insert(1,deepcopy(records[0]))
    else:
        row=next(row for row in records if row['phase']=='P3')
        if mutation=='empty_self_hashed': row['evidence']={}
        elif mutation=='false_red': row['evidence']['red_verified']=False
        else: row['evidence']['strategy']='preserve_behavior'
        row['evidence_sha256']=digest(row['evidence'])
    with pytest.raises(ValueError): relationship_arguments(snapshot)


def test_leaf_phase_order_cannot_be_reconstructed_by_filtering_interleaved_records(protocol_snapshot):
    records=deepcopy(protocol_snapshot['coding']['phase_ledger'])
    second=deepcopy(records[1:])
    for row in second: row['leaf_index']=1
    records[3:3]=second
    with pytest.raises(ValueError,match='leaf order'):
        validate_phase_ledger(records,['L1','L2'])


@pytest.mark.parametrize('rejection',['tests_failed','minor_findings'])
def test_real_store_recovery_allows_unrecorded_noop_refinement_only_in_historical_rounds(tmp_path,rejection):
    case=CodingFixture(tmp_path)
    pending_noop_refinement(case)
    case.test(passed=rejection!='tests_failed')
    if rejection=='minor_findings':
        case.reviews(minor_findings=['The documentation needs another clarification'])
    else:
        case.reviews(approved=False,objective_satisfied=False)
    case.edit()
    case.test(passed=True)
    case.reviews()
    state=case.state(); records=state['phase_ledger']
    assert any(previous['phase']=='P8' and current['phase']=='P10'
               for previous,current in zip(records,records[1:]))
    assert validate_phase_ledger(records,['L1'])['L1']['outcome']=='COMPLETED'
    records.pop(-2)
    with pytest.raises(ValueError):
        validate_phase_ledger(records,['L1'])


@pytest.mark.parametrize('mutation',['no_planner','self_plan_review','self_file_review','different_diff',
                                    'unapproved_final','missing_final_test_file','wrong_plan_evidence',
                                    'wrong_planner_id','wrong_planner_receipt'])
def test_audit_relationship_helpers_reject_self_review_detached_hashes_and_missing_files(protocol_snapshot,mutation):
    snapshot=deepcopy(protocol_snapshot)
    jobs,plan,phases,evidence=relationship_arguments(snapshot)
    if mutation=='no_planner': jobs[:]=[job for job in jobs if job['kind']!='CODE_PLAN']
    elif mutation=='wrong_plan_evidence':
        planner=next(job for job in jobs if job['kind']=='CODE_PLAN')
        evidence[planner['job_id']]['evidence']['plan']={}
    elif mutation in {'wrong_planner_id','wrong_planner_receipt'}:
        review=next(job for job in jobs if job['kind']=='CODE_PLAN_REVIEW')
        review['payload']['planner_job_id' if mutation=='wrong_planner_id' else 'planner_receipt_sha256']='unbound'
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
            'test_cycle_seconds':1.5,'test_cycle_deadline_met':True,
            'cleanup_verified':True,'source_verified':True,'commands':[{'kind':'pytest','passed':True,
            'exit_code':0,'junit':{'tests':1,'failures':0,'errors':0,'skipped':0,'passed':1},
            'junit_cases':[{'name':'test_contract','status':'passed'}]}]}


def test_docker_metadata_helper_accepts_consistent_counts_without_emitting_certification():
    assert validate_final_test(docker_metadata_case()) is None


@pytest.mark.parametrize('mutation',['fixture','no_container','all_skipped','case_contradiction','timeout','bool_exit',
                                    'missing_deadline','slow','nonfinite_time','negative_time','deadline_exceeded'])
def test_docker_metadata_helper_cannot_certify_fixtures_or_skipped_execution(mutation):
    value=docker_metadata_case(); command=value['commands'][0]
    if mutation=='fixture': value['execution_kind']='controller-storage-contract-fixture'
    elif mutation=='no_container': value.pop('container_id')
    elif mutation=='all_skipped':
        command['junit'].update(skipped=1,passed=0)
        command['junit_cases'][0]['status']='skipped'
    elif mutation=='case_contradiction': command['junit_cases'][0]['status']='skipped'
    elif mutation=='timeout': command['timed_out']=True
    elif mutation=='bool_exit': command['exit_code']=False
    elif mutation=='missing_deadline': value.pop('test_cycle_seconds')
    elif mutation=='slow': value['test_cycle_seconds']=30.01
    elif mutation=='nonfinite_time': value['test_cycle_seconds']=float('nan')
    elif mutation=='negative_time': value['test_cycle_seconds']=-1
    else: value['test_cycle_deadline_met']=False
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
    snapshot=digest({path:item['sha256'] for path,item in value['source']['files'].items()})
    job['payload']['snapshot_sha256']=job['output']['source_snapshot_sha256']=snapshot
    arguments=({'L1':{'evidence':{}}},[job],{'test-stage':{'evidence':value}})
    rehash_test_metadata(arguments)
    return arguments


def rehash_test_metadata(arguments):
    """Bind synthetic helper inputs; this never produces native certification."""
    phases,jobs,evidence=arguments
    sha=digest(evidence['test-stage']['evidence'])
    evidence['test-stage']['sha256']=sha
    jobs[0]['output']['test_receipt_sha256']=sha
    phases['L1']['evidence']['test_receipt_sha256']=sha


def test_more_than_one_thousand_cases_retains_and_verifies_last_planned_identity():
    arguments=identity_contract_case(1701)
    assert validate_phase_tests(*arguments) is None
    arguments[2]['test-stage']['evidence']['commands'][0]['junit_cases'][:]=arguments[2]['test-stage']['evidence']['commands'][0]['junit_cases'][:1000]
    rehash_test_metadata(arguments)
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
    rehash_test_metadata(arguments)
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


def red_contract_case():
    """Synthetic RED relationship metadata, never live Docker acceptance."""
    phases,jobs,evidence=identity_contract_case()
    value=evidence['test-stage']['evidence']; command=value['commands'][0]
    value.update(passed=False,failure_category='tests_failed')
    command.update(passed=False,exit_code=1)
    command['junit'].update(passed=0,failures=1)
    command['junit_cases'][0]['status']='failed'
    leaf={'id':'L1','criteria_ids':['AC1'],'file_targets':['src/contract.py']}
    cases=[{'name':'test_contract','criteria_ids':['AC1']}]
    plan={'plan_sha256':'d'*64,'fracture_manifest':{'topological_order':['L1'],'leaves':[leaf]},
          'test_cases':cases}
    jobs[0]['payload'].update(phase='precode',cycle=1,plan_sha256=plan['plan_sha256'],
                             active_leaf=deepcopy(leaf),test_cases=deepcopy(cases))
    rehash_test_metadata((phases,jobs,evidence))
    phase={'phase':'P3','outcome':'COMPLETED','leaf_index':0,'cycle':1,'evidence':phases['L1']['evidence']}
    phase['evidence'].update(red_verified=True,strategy='red_green')
    return [phase],plan,jobs,evidence


def rehash_red_metadata(arguments):
    records,plan,jobs,evidence=arguments
    rehash_test_metadata(({'L1':records[0]},jobs,evidence))


def test_red_relationship_metadata_requires_the_actual_planned_case_to_fail():
    arguments=red_contract_case()
    assert validate_precode_tests(*arguments) is None
    command=arguments[3]['test-stage']['evidence']['commands'][0]
    command['junit'].update(tests=2,passed=1)
    command['junit_cases'][0]['status']='passed'
    command['junit_cases'].append({'name':'test_unrelated','class_name':'tests.test_existing','status':'failed'})
    rehash_red_metadata(arguments)
    with pytest.raises(ValueError,match='exact sealed planned test'):
        validate_precode_tests(*arguments)


@pytest.mark.parametrize('mutation',['postedit','unfinished','detached_receipt','different_evidence_hash',
                                    'different_job','different_plan','different_leaf','different_planned_test','different_cycle',
                                    'different_snapshot','different_output_snapshot','different_manifest',
                                    'different_file_hash','no_seal','other_class','other_module','false_red',
                                    'passing_tests','unrelated_failure','skipped_planned','collection_error',
                                    'wrong_exit','bool_exit','case_count','timeout','fixture','slow'])
def test_precode_acceptance_rejects_detached_or_non_red_execution(mutation):
    arguments=red_contract_case(); records,plan,jobs,evidence=arguments
    job=jobs[0]; payload=job['payload']; value=evidence['test-stage']['evidence']
    command=value['commands'][0]
    if mutation=='postedit': payload['phase']='postedit'
    elif mutation=='unfinished': job['status']='PENDING'
    elif mutation=='detached_receipt': job['output']['test_receipt_sha256']='e'*64
    elif mutation=='different_evidence_hash': evidence['test-stage']['sha256']='e'*64
    elif mutation=='different_job': value['job_id']='unrelated-job'
    elif mutation=='different_plan': payload['plan_sha256']='e'*64
    elif mutation=='different_leaf': payload['active_leaf']['file_targets']=['src/other.py']
    elif mutation=='different_planned_test': payload['test_cases'][0]['criteria_ids']=['AC2']
    elif mutation=='different_cycle': payload['cycle']=2
    elif mutation=='different_snapshot': payload['snapshot_sha256']='e'*64
    elif mutation=='different_output_snapshot': job['output']['source_snapshot_sha256']='e'*64
    elif mutation=='different_manifest': value['source']['sha256']='e'*64
    elif mutation=='different_file_hash': payload['test_identities']['test_contract']['file_sha256']='e'*64
    elif mutation=='no_seal': payload.pop('test_identities')
    elif mutation=='other_class': command['junit_cases'][0]['class_name']='tests.test_contract.OtherClass'
    elif mutation=='other_module': command['junit_cases'][0]['class_name']='tests.unrelated.TestScope'
    elif mutation=='false_red': records[0]['evidence']['red_verified']=False
    elif mutation=='passing_tests':
        value['passed']=command['passed']=True
        command['exit_code']=0
        command['junit'].update(failures=0,passed=1)
        command['junit_cases'][0]['status']='passed'
    elif mutation=='unrelated_failure': command['junit_cases'][0]['name']='test_unrelated'
    elif mutation=='skipped_planned':
        command['junit'].update(tests=2,skipped=1)
        command['junit_cases'].append({'name':'test_contract[skipped]',
            'class_name':'tests.test_contract.TestScope','status':'skipped'})
    elif mutation=='collection_error':
        command['junit'].update(failures=0,errors=1)
        command['junit_cases'][0]['status']='error'
    elif mutation=='wrong_exit': command['exit_code']=2
    elif mutation=='bool_exit': command['exit_code']=True
    elif mutation=='case_count': command['junit']['failures']=2
    elif mutation=='timeout': command['timed_out']=True
    elif mutation=='fixture': value['execution_kind']='controller-storage-contract-fixture'
    else: value['test_cycle_seconds']=31
    if mutation not in {'detached_receipt','different_evidence_hash'}: rehash_red_metadata(arguments)
    with pytest.raises(ValueError): validate_precode_tests(*arguments)


def test_red_evidence_rejects_runtime_failure_even_with_assertion_metadata():
    arguments=red_contract_case(); value=arguments[3]['test-stage']['evidence']
    value['failure_category']='timeout'
    with pytest.raises(ValueError,match='failing-first'): validate_red_test(value)


def test_red_evidence_cannot_count_failed_lint_as_a_planned_assertion_failure():
    arguments=red_contract_case(); value=arguments[3]['test-stage']['evidence']
    value['commands'][0]['kind']='lint'
    with pytest.raises(ValueError,match='non-test command'): validate_red_test(value)
