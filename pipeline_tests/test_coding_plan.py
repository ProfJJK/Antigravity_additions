"""Physical source files and pure plan/protocol gates; no model execution claims."""
from copy import deepcopy
import hashlib
import json

import pytest

from cochem_pipeline.coding import CodingProject,digest
from cochem_pipeline.coding_plan import (TDD_PHASES,validate_plan,validate_plan_review,
    artifact_protocol,apply_artifact_protocol,initial_phase_ledger,record_phase)


def inputs(tmp_path):
    project=CodingProject('demo',tmp_path,'main',('src',),('tests',))
    path=tmp_path/'src/main.py'; path.parent.mkdir()
    path.write_text(''.join(f'value_{index} = {index}\n' for index in range(200)),encoding='utf-8')
    files={'src/main.py':path.read_bytes()}
    plan={'goal':'Make boundary handling explicit',
          'srs':{'skeleton':'# Boundary handling\nRequirements: R1.\n',
                 'chapters':[{'id':'ch01','title':'Boundary requirements','text':'Reject invalid bounds and preserve valid inputs.',
                              'requirement_ids':['R1']}]},
          'acceptance_criteria':[{'id':'AC1','statement':'Negative bounds are rejected.','requirement_ids':['R1'],'test_ids':['T1']}],
          'test_cases':[{'id':'T1','name':'test_negative_bound','asserts':'A negative bound raises ValueError.','criteria_ids':['AC1']}],
          'leaves':[{'id':'L1','objective':'Validate the single boundary implementation.',
                     'requirement_ids':['R1'],'criteria_ids':['AC1'],'file_targets':['src/main.py'],
                     'estimated_changed_lines':20,'dependencies':[]}]}
    return project,files,plan,['Reject negative bounds.']


def receipt_case(provider='codex',model='gpt-6-sol'):
    """Schema input only; this fixture does not attest a native CLI invocation."""
    return {'provider':provider,'requested_model':model,'reported_model':model,'subscription_verified':True,
            'exit_code':0,'pid':123,'session_id':'schema-case-session','output_sha256':'a'*64}


def test_physical_source_plan_generates_hashed_modular_srs_and_matching_machine_mermaid_dag(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    plan=validate_plan(raw,project,files,requirements)
    assert plan['plan_sha256']==digest({key:value for key,value in plan.items() if key!='plan_sha256'})
    assert plan['fracture_manifest']['N']==1
    for name,text in plan['artifacts'].items():
        assert hashlib.sha256(text.encode()).hexdigest()==plan['artifact_hashes'][name]
        if name.endswith('.md'): assert len(text.splitlines())<=400
    graph=json.loads(plan['artifacts']['graph.json'])
    assert graph['nodes']==['L1'] and graph['edges']==[]
    assert 'L1' in plan['artifacts']['graph.mmd']
    assert plan['artifacts']['wbs/batches/B01.json']==plan['artifacts']['WBS_Micro_Prompts/B01.json']
    assert (tmp_path/'src/main.py').read_bytes()==files['src/main.py']
    assert not (tmp_path/'srs').exists()  # Caller owns durable storage/materialization.


def test_twenty_one_distinct_single_target_leaves_form_two_bounded_dependency_batches(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    template=raw['leaves'][0]
    raw['leaves']=[{**template,'id':f'L{index}','file_targets':[f'src/part{index}.py'],
                   'dependencies':[f'L{index-1}'] if index>1 else []} for index in range(1,22)]
    plan=validate_plan(raw,project,files,requirements)
    fracture=plan['fracture_manifest']
    assert [len(batch['leaf_ids']) for batch in fracture['batches']]==[20,1]
    assert fracture['topological_order']==[f'L{index}' for index in range(1,22)]
    assert ['L20','L21'] in fracture['graph']['edges']
    assert 'L20 --> L21' in fracture['mermaid']


@pytest.mark.parametrize('mutation',[
    lambda p:p['srs'].update(skeleton='line\n'*401),
    lambda p:p['srs']['chapters'][0].update(text='line\n'*399),
    lambda p:p['srs']['chapters'][0].update(requirement_ids=['R2']),
    lambda p:p['leaves'][0].update(file_targets=['src/main.py','src/extra.py']),
    lambda p:p['leaves'][0].update(file_targets=['tests/test_protected.py']),
    lambda p:p['leaves'][0].update(file_targets=['src']),
    lambda p:p['leaves'][0].update(file_targets=['../outside.py']),
    lambda p:p['leaves'][0].update(estimated_changed_lines=19),
    lambda p:p['leaves'][0].update(estimated_changed_lines=101),
    lambda p:p['leaves'][0].update(estimated_changed_lines=True),
    lambda p:p['leaves'][0].update(dependencies=['L1']),
    lambda p:p['leaves'][0].update(criteria_ids=['AC2']),
    lambda p:p['test_cases'][0].update(name='claimed success'),
    lambda p:p['test_cases'][0].update(criteria_ids=[]),
])
def test_invalid_srs_fracture_or_traceability_prevents_dispatch(tmp_path,mutation):
    project,files,raw,requirements=inputs(tmp_path)
    mutation(raw)
    with pytest.raises(ValueError): validate_plan(raw,project,files,requirements)


def test_dependency_cycle_cannot_be_rendered_as_a_successful_plan(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    raw['leaves'].append({**raw['leaves'][0],'id':'L2','dependencies':['L1']})
    raw['leaves'][0]['dependencies']=['L2']
    with pytest.raises(ValueError,match='cycle'): validate_plan(raw,project,files,requirements)


def review_case(plan):
    return {'verdict':'PASS','plan_sha256':plan['plan_sha256'],'requirements_checked':['R1'],
            'findings':[],'artifact_hashes':deepcopy(plan['artifact_hashes'])}


def test_independent_planning_review_is_bound_to_every_artifact_and_distinct_native_identity(tmp_path):
    project,files,raw,requirements=inputs(tmp_path)
    plan=validate_plan(raw,project,files,requirements)
    evidence=validate_plan_review(review_case(plan),plan,planner_receipt=receipt_case(),
        reviewer_receipt=receipt_case('claude','claude-opus-5-5'))
    assert evidence['approved'] is True and evidence['plan_sha256']==plan['plan_sha256']
    assert evidence['planner']!=evidence['reviewer']

@pytest.mark.parametrize('mutation',[
    lambda r:r.update(verdict='INDETERMINATE'),
    lambda r:r.update(plan_sha256='0'*64),
    lambda r:r.update(requirements_checked=[]),
    lambda r:r.update(artifact_hashes={}),
    lambda r:r.update(findings=[{'severity':'HIGH','issue':'A requirement is missing.'}]),
])
def test_missing_or_failed_planning_review_is_not_implementation_authority(tmp_path,mutation):
    project,files,raw,requirements=inputs(tmp_path); plan=validate_plan(raw,project,files,requirements)
    review=review_case(plan); mutation(review)
    with pytest.raises(ValueError):
        validate_plan_review(review,plan,planner_receipt=receipt_case(),reviewer_receipt=receipt_case('claude','claude-opus-5-5'))


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(provider='codex',requested_model='gpt-6-sol',reported_model='gpt-6-sol'),
    lambda r:r.update(subscription_verified=False),lambda r:r.update(pid=True),
    lambda r:r.update(exit_code=False),lambda r:r.update(execution_kind='emulator'),
    lambda r:r.update(reported_model='unrequested-model'),lambda r:r.update(session_id=None),
])
def test_same_model_or_unverified_native_receipt_cannot_supply_independent_plan_audit(tmp_path,mutation):
    project,files,raw,requirements=inputs(tmp_path); plan=validate_plan(raw,project,files,requirements)
    reviewer=receipt_case('claude','claude-opus-5-5'); mutation(reviewer)
    with pytest.raises(ValueError):
        validate_plan_review(review_case(plan),plan,planner_receipt=receipt_case(),reviewer_receipt=reviewer)


def test_complete_file_artifact_protocol_preserves_unchanged_bytes_and_rejects_direct_authority(tmp_path):
    project,files,_,_=inputs(tmp_path)
    changed=files['src/main.py'].decode().replace('value_20 = 20','value_20 = 21')
    text='<<<FILE: src/main.py>>>\n'+changed+'<<<END FILE>>>\nOne boundary changed.'
    after=apply_artifact_protocol(text,files,project,protected=['tests/test_real.py'])
    assert after['src/main.py']==changed.encode() and files['src/main.py']!=(changed.encode())
    assert (tmp_path/'src/main.py').read_bytes()==files['src/main.py']
    prompt=artifact_protocol(tmp_path,['tests/test_real.py'])
    assert '<<<FILE: relative/path/from/workspace.py>>>' in prompt and '<<<END FILE>>>' in prompt
    assert 'do not write files directly' in prompt and 'tests/test_real.py' in prompt


@pytest.mark.parametrize('text',[
    'I changed the file successfully.',
    '<<<FILE: src/main.py>>>\nnever closed',
    '<<<END FILE>>>\n',
    '<<<FILE: ../outside.py>>>\nx=1\n<<<END FILE>>>\n',
    '<<<FILE: src/main.py>>>\n@@ -1 +1 @@\n<<<END FILE>>>\n',
    '<<<FILE: tests/test_real.py>>>\nx=1\n<<<END FILE>>>\n',
    '<<<FILE: src/main.py>>>\nx=1\n<<<FILE: src/other.py>>>\nx=2\n<<<END FILE>>>\n',
])
def test_invalid_or_protected_complete_file_blocks_never_return_an_executable_snapshot(tmp_path,text):
    project,files,_,_=inputs(tmp_path)
    with pytest.raises(ValueError):
        apply_artifact_protocol(text,files,project,protected=['tests/test_real.py'])


def test_transporting_a_complete_file_does_not_allow_an_actual_whole_file_rewrite(tmp_path):
    project,files,_,_=inputs(tmp_path)
    text='<<<FILE: src/main.py>>>\n'+'replacement = 1\n'*200+'<<<END FILE>>>\n'
    with pytest.raises(ValueError,match='rewrite boundary|100 lines'):
        apply_artifact_protocol(text,files,project)


def test_preserved_ten_phase_ledger_requires_actual_gate_hashes_and_semantic_skip_reasons():
    ledger=initial_phase_ledger(); sha='a'*64
    cases=[('P1',{'plan_sha256':sha}),('P2',{'dossier_sha256':sha}),
        ('P3',{'test_receipt_sha256':sha,'red_verified':True}),('P4',{'artifact_sha256':sha}),
        ('P5',{'test_receipt_sha256':sha}),('P6',{'review_sha256':sha})]
    for phase,evidence in cases: ledger=record_phase(ledger,phase,evidence)
    with pytest.raises(ValueError,match='order'):
        record_phase(ledger,'P9',{'artifact_sha256':sha},iteration=1)
    ledger=record_phase(ledger,'P7',{},outcome='SKIPPED',reason='no_qualifying_minor_findings',iteration=1)
    ledger=record_phase(ledger,'P8',{'next_work_sha256':sha,'decision':'CONTINUE'},iteration=1)
    ledger=record_phase(ledger,'P9',{'artifact_sha256':sha},iteration=1)
    ledger=record_phase(ledger,'P10',{'test_receipt_sha256':sha,'review_sha256':sha},iteration=1)
    assert [item['phase'] for item in ledger['records']]==[key for key,_ in TDD_PHASES]
    assert ledger['records'][6]['outcome']=='SKIPPED'
    with pytest.raises(ValueError,match='justified'):
        record_phase(ledger,'P7',{},outcome='SKIPPED',reason='model said unnecessary',iteration=2)


def test_test_first_phase_cannot_be_replaced_by_a_green_model_claim():
    sha='a'*64
    ledger=record_phase(initial_phase_ledger(),'P1',{'plan_sha256':sha})
    ledger=record_phase(ledger,'P2',{'dossier_sha256':sha})
    with pytest.raises(ValueError,match='red test'):
        record_phase(ledger,'P3',{'test_receipt_sha256':sha,'red_verified':False})
