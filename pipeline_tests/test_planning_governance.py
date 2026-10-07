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
    # Obsolete keys may survive an upgrade but have no agentic authority.
    policy = {'protocol': {'obsolete': True}, 'method_matrix': {'chemical_tiers_only': True},
              'research_sources': [{'id': 'source-a', 'url': 'https://www.python.org/doc/'},
                                   {'id': 'source-b', 'url': 'https://docs.pytest.org/en/stable/'}]}
    return normalize_policy(policy), {}


def test_clean_contract_binds_real_sources_without_phantom_documents(tmp_path):
    from cochem_pipeline.planning_governance import execution_contract
    files = {'src/real.py': b'ANSWER = 42\n'}
    result = validate_registration({}, files)
    assert result['specification_id'] == 'COCHEM-4.2.7'
    assert result['contract'] == execution_contract()
    assert 'stage_execution_verified' not in result
    files['src/real.py'] += b'# A real change\n'
    assert validate_registration({}, files)['source_manifest_sha256'] != result['source_manifest_sha256']


def test_legacy_phantom_policy_is_ignored_without_weakening_real_research():
    policy = normalize_policy({'protocol': {'path': '../legacy'}, 'method_matrix': 'obsolete',
                               'max_revisions': 3})
    assert policy == {'max_revisions': 3}
    assert normalize_policy({'protocol': False, 'method_matrix': None}) == {}
    with pytest.raises(ValueError, match='two to eight'):
        normalize_policy({'research_sources': [{'id': 'one', 'url': 'https://example.com/'}]})


def test_plan_has_audited_canonical_contract_not_agentic_method_matrix(tmp_path):
    from cochem_pipeline.planning_governance import execution_contract
    project, files, raw, requirements = inputs(tmp_path)
    raw['method_matrix'] = 'obsolete content does not add execution semantics'
    plan = validate_plan(raw, project, files, requirements)
    assert json.loads(plan['artifacts']['ExecutionContract.json']) == execution_contract()
    assert 'MethodMatrix.json' not in plan['artifacts']
    assert plan['artifact_hashes']['ExecutionContract.json'] == hashlib.sha256(
        plan['artifacts']['ExecutionContract.json'].encode()).hexdigest()


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


def production_coordinator(fixture):
    coordinator = CodingCoordinator.__new__(CodingCoordinator)
    coordinator.config = SimpleNamespace(coding_projects={'project': fixture.project},
        ramdisk=SimpleNamespace(enabled=True), docker=fixture.docker, git_executable=fixture.git_executable)
    coordinator.store = fixture.store
    return coordinator


def test_production_submission_creates_runnable_plan_without_phantom_registration(tmp_path):
    fixture = CodingFixture(tmp_path)
    coordinator = production_coordinator(fixture)
    workflow = coordinator.submit('project', 'A production request', ['REQ-1'])
    assert workflow['status'] == 'IN_PROGRESS'
    assert workflow['coding']['status'] == 'PLANNING'
    stages = [job for job in workflow['jobs'] if job['kind'] == 'CODE_PLAN']
    assert len(stages) == 1 and stages[0]['status'] == 'PENDING'
    assert stages[0]['payload']['execution_transition']['from_state'] == 'PLANNING'
    assert workflow['coding']['planning_evidence']['specification_id'] == 'COCHEM-4.2.7'
    readiness = planning_readiness(coordinator.config.coding_projects)
    assert readiness['ready'] is True and readiness['specification_blockers'] == []
    assert readiness['projects'][0]['source_bytes_verified'] is False
    assert workflow['evidence'] == []


def test_production_submission_does_not_skip_operator_registered_external_research(tmp_path):
    fixture = CodingFixture(tmp_path)
    policy, _ = registered(tmp_path)
    fixture.project = replace(fixture.project, planning=policy)
    # Direct store admission cannot fabricate collected registered research.
    with pytest.raises(ValueError, match='controller-collected'):
        fixture.store.submit_coding_snapshot(fixture.project, 'Request', ['REQ-1'], fixture.snapshot, fixture.docker)


def legacy_hold(fixture, *, reason=None):
    from cochem_pipeline.planning_governance import LEGACY_PHANTOM_HOLDS
    reason = reason or sorted(LEGACY_PHANTOM_HOLDS)[0]
    return fixture.store.submit_coding_snapshot(fixture.project, 'Legacy request', ['REQ-1'],
        fixture.snapshot, fixture.docker, workflow_id='legacy-held', planning_blocker=reason)


def test_explicit_resume_migrates_only_untouched_phantom_hold_and_retains_evidence(tmp_path):
    fixture = CodingFixture(tmp_path)
    original = legacy_hold(fixture)
    result = production_coordinator(fixture).resume('legacy-held', 'Apply owner-approved Alternative Path')
    assert result['status'] == 'IN_PROGRESS' and result['coding']['status'] == 'PLANNING'
    current = fixture.store.coding_workflow('legacy-held')
    migration = current['coding']['planning_migrations'][0]
    assert migration['withdrawn_reason'] == original['coding']['planning_hold']
    assert migration['previous_planning_evidence'] == original['coding']['planning_evidence']
    assert migration['baseline_commit'] == current['coding']['baseline_commit']
    assert current['coding']['cycle'] == 1 and current['coding']['planning_revision'] == 0
    assert current['coding']['original_snapshot'] == original['coding']['original_snapshot']
    assert len([job for job in current['jobs'] if job['kind'] == 'CODE_PLAN']) == 1
    with pytest.raises(ValueError, match='no operator-held'):
        production_coordinator(fixture).resume('legacy-held', 'Repeated resume is not duplicate dispatch')


@pytest.mark.parametrize('kind', ['revision_hold', 'cancelled', 'executed', 'budget', 'empty_reason'])
def test_legacy_migration_cannot_erase_real_execution_or_holds(tmp_path, kind):
    fixture = CodingFixture(tmp_path)
    legacy_hold(fixture, reason='Planning revision did not change every cited artifact group' if kind == 'revision_hold' else None)
    if kind == 'cancelled':
        fixture.store.cancel_workflow('legacy-held')
    if kind in ('executed', 'budget'):
        with fixture.store._write() as conn:
            state = fixture.store._coding_state(conn, 'legacy-held')
            if kind == 'executed': state['planning_history'] = [{'retained': 'actual prior execution'}]
            else: state['cycle'] = 2
            fixture.store._save_coding(conn, 'legacy-held', state)
    before = fixture.store.coding_workflow('legacy-held')
    with pytest.raises(ValueError):
        production_coordinator(fixture).resume('legacy-held', '' if kind == 'empty_reason' else 'Must not reset')
    assert fixture.store.coding_workflow('legacy-held') == before



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


def completed_contract_case(tmp_path):
    fixture = CodingFixture(tmp_path)
    fixture.integration(fixture.ready())
    return fixture


def test_completed_portable_workflow_passes_canonical_contract_without_native_claim(tmp_path):
    from cochem_pipeline.coding_acceptance import validate_production_planning, validate_coding_workflow
    from cochem_pipeline.service import public_workflow
    fixture = completed_contract_case(tmp_path)
    for workflow in (fixture.store.coding_workflow(fixture.workflow_id),
                     public_workflow(fixture.store.coding_workflow(fixture.workflow_id))):
        state = workflow['coding']
        evidence = {row['job_id']: row for row in workflow['evidence']}
        result = validate_production_planning(state, state['plan'], workflow['jobs'], evidence)
        assert result['specification_id'] == 'COCHEM-4.2.7'
        assert result['stage_count'] == len(workflow['jobs']) - 1
        assert 'accepted' not in result
        with pytest.raises(ValueError, match='fixtures are not live'):
            validate_coding_workflow(workflow)


@pytest.mark.parametrize('mutation', ['wrong_state', 'wrong_contract', 'missing_predecessor',
                                     'wrong_receipt', 'wrong_evidence', 'foreign_predecessor',
                                     'fake_execution_boolean', 'detached_source'])
def test_canonical_acceptance_rejects_forged_transition_and_source_evidence(tmp_path, mutation):
    from cochem_pipeline.coding_acceptance import validate_production_planning
    fixture = completed_contract_case(tmp_path)
    workflow = fixture.store.coding_workflow(fixture.workflow_id)
    state = workflow['coding']; jobs = workflow['jobs']
    node = next(job for job in jobs if job['kind'] == 'CODE_EDIT')
    transition = node['payload']['execution_transition']
    if mutation == 'wrong_state': transition['from_state'] = 'COMPLETED'
    elif mutation == 'wrong_contract': transition['contract_sha256'] = 'a' * 64
    elif mutation == 'missing_predecessor': transition['predecessor'] = None
    elif mutation == 'wrong_receipt': transition['predecessor']['receipt_sha256'] = 'a' * 64
    elif mutation == 'wrong_evidence': transition['predecessor']['evidence_sha256'] = 'a' * 64
    elif mutation == 'foreign_predecessor': transition['predecessor']['job_id'] = 'other-workflow'
    elif mutation == 'fake_execution_boolean':
        state['planning_evidence'] = {'stage_execution_verified': True, 'registration_verified': True}
    else: state['planning_evidence']['source_manifest_sha256'] = 'a' * 64
    with pytest.raises(ValueError):
        validate_production_planning(state, state['plan'], jobs,
                                     {row['job_id']: row for row in workflow['evidence']})


def test_completion_rejects_tampered_workflow_state_before_accepting_output(tmp_path):
    fixture = CodingFixture(tmp_path)
    (tmp_path / 'plan-input').mkdir()
    _, _, raw, _ = inputs(tmp_path / 'plan-input')
    raw['leaves'][0]['file_targets'] = ['src/answer.py']
    node = fixture.claim('CODE_PLAN')
    plan = validate_plan(raw, fixture.project, fixture.snapshot.files, ['REQ-1'])
    with fixture.store._write() as conn:
        state = fixture.store._coding_state(conn, fixture.workflow_id)
        state['status'] = 'EDITING'
        fixture.store._save_coding(conn, fixture.workflow_id, state)
    with pytest.raises(ValueError, match='captured execution state'):
        fixture.complete_native(node, raw, {'plan': plan})
    assert fixture.store.get(node['job_id'])['status'] == 'IN_PROGRESS'
    assert fixture.store.coding_workflow(fixture.workflow_id)['evidence'] == []


@pytest.mark.parametrize('stage', ['CODE_TEST_AUTHOR', 'CODE_EDIT', 'CODE_REVIEW'])
def test_three_native_code_failures_research_from_actual_last_successful_evidence(tmp_path, stage):
    from cochem_pipeline.planning_governance import validate_execution_history
    fixture = CodingFixture(tmp_path)
    fixture.plan(); fixture.initial_research()
    if stage != 'CODE_TEST_AUTHOR':
        fixture.author(); fixture.test(passed=False)
    if stage == 'CODE_REVIEW':
        fixture.edit(); fixture.test(passed=True)
    for failure in range(3):
        node = fixture.claim(stage if failure == 0 else ('CODE_TEST_AUTHOR' if stage == 'CODE_TEST_AUTHOR' else 'CODE_EDIT'))
        assert fixture.store.fail(node['job_id'], node['attempt_id'], node['fencing_token'],
                                  'Recorded native implementation failure', retry=True, category='code')
    assert fixture.state()['status'] == 'RESEARCH_REQUIRED'
    workflow = fixture.store.coding_workflow(fixture.workflow_id)
    validate_execution_history(workflow['jobs'], {row['job_id']: row for row in workflow['evidence']})
    assert fixture.claim('CODE_RESEARCH')['payload']['failure_evidence_sha256']


@pytest.mark.parametrize('reason', [
    'Production planning requires the canonical seven-stage protocol, Method Matrix M-1..M-8, and registered external research sources',
    'Canonical seven-stage source is registered, but its exact execution transitions have no verified controller binding',
    'Registered protocol source is absent or its captured Git bytes changed',
    'Registered method_matrix source is absent or its captured Git bytes changed',
    'Registered protocol clause is absent from its physical source',
    'Registered method_matrix clause is absent from its physical source',
    'Seven-stage registration must preserve the source order',
])
def test_withdrawn_legacy_phantom_source_errors_can_explicitly_migrate(tmp_path, reason):
    fixture = CodingFixture(tmp_path)
    legacy_hold(fixture, reason=reason)
    result = production_coordinator(fixture).resume('legacy-held', 'Withdraw obsolete agentic source registration')
    assert result['coding']['status'] == 'PLANNING'
    assert result['coding']['planning_migrations'][0]['withdrawn_reason'] == reason


def test_completion_rechecks_physical_predecessor_before_publishing_any_output(tmp_path):
    fixture = CodingFixture(tmp_path)
    fixture.plan()
    node = fixture.claim('CODE_RESEARCH')
    with fixture.store._write() as conn:
        task = deepcopy(node['payload'])
        task['execution_transition']['predecessor']['evidence_sha256'] = 'a' * 64
        conn.execute('UPDATE pipeline_jobs SET payload_json=? WHERE job_id=?', (json.dumps(task), node['job_id']))
    with pytest.raises(ValueError, match='physical predecessor evidence'):
        fixture.complete_native(node, {'strategy': 'Must not be accepted'}, {})
    assert fixture.store.get(node['job_id'])['status'] == 'IN_PROGRESS'
    assert all(row['job_id'] != node['job_id'] for row in fixture.store.coding_workflow(fixture.workflow_id)['evidence'])


@pytest.mark.parametrize('reason', ['No external research policy is registered',
    'Research lacks verified HTTPS evidence', 'Planning exhausted its immutable revision budget',
    'Canonical seven-stage source is registered, but its exact execution transitions have no verified controller binding; unresolved source mutation'])
def test_migration_never_broadly_matches_phantom_words_or_real_research_hold(tmp_path, reason):
    fixture = CodingFixture(tmp_path)
    legacy_hold(fixture, reason=reason)
    before = fixture.store.coding_workflow('legacy-held')
    with pytest.raises(ValueError, match='Only a reviewed integration hold'):
        production_coordinator(fixture).resume('legacy-held', 'Do not erase genuine hold')
    assert fixture.store.coding_workflow('legacy-held') == before


@pytest.mark.parametrize('last_success', ['improvement', 'partial_audit'])
def test_native_failure_research_preserves_successful_improvement_or_partial_audit_predecessor(tmp_path, last_success):
    from cochem_pipeline.coding import observed_changes, manifest, digest
    from cochem_pipeline.planning_governance import validate_execution_history
    fixture = CodingFixture(tmp_path)
    fixture.author(); fixture.test(passed=False); fixture.edit(); fixture.test(passed=True)
    if last_success == 'improvement':
        fixture.reviews(minor_findings=['Clarify the first source comment'])
        prior = fixture.claim('CODE_EDIT')
        assert prior['payload']['phase'] == 'P7'
        before = fixture.store.coding_files(fixture.state()['current_snapshot'])
        after = dict(before)
        after['src/answer.py'] = after['src/answer.py'].replace(
            b'# Existing documentation line 0\n', b'# Clarified answer documentation\n')
        baseline = fixture.store.coding_files(fixture.state()['review_base_snapshot'])
        fixture.complete_native(prior, {'done': True, 'requirements_traced': ['REQ-1']},
            {'changes': observed_changes(baseline, after, fixture.snapshot.files, fixture.project),
             'snapshot_sha256': digest(manifest(after))}, after)
        next_kind = 'CODE_EDIT'
    else:
        prior = fixture.claim('CODE_REVIEW')
        fixture.complete_native(prior, fixture.review_output(prior),
                                {'source_snapshot_sha256': fixture.state()['current_snapshot']})
        next_kind = 'CODE_REVIEW'
    for index in range(3):
        node = fixture.claim(next_kind if index == 0 else 'CODE_EDIT')
        if index == 0 and last_success == 'improvement': assert node['payload']['phase'] == 'P9'
        assert fixture.store.fail(node['job_id'], node['attempt_id'], node['fencing_token'],
                                  'Native code failure after real accepted predecessor', retry=True, category='code')
    assert fixture.state()['status'] == 'RESEARCH_REQUIRED'
    assert fixture.state()['consecutive_failures'] == 3
    node = fixture.claim('CODE_RESEARCH')
    assert node['payload']['execution_transition']['predecessor']['job_id'] == prior['job_id']
    workflow = fixture.store.coding_workflow(fixture.workflow_id)
    validate_execution_history(workflow['jobs'], {row['job_id']: row for row in workflow['evidence']})
