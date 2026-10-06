"""Verify one completed coding workflow using authenticated controller evidence.

This command submits no work and invokes no model. It does not certify Windows
hardware, a 48-hour soak, or other workflows. Native identity/exit metadata is
trusted controller evidence; private CLI stdout cannot be independently read
by this unprivileged client.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import time

from cochem_pipeline.coding import digest, safe_path
from cochem_pipeline.coding_plan import validate_plan_review
from cochem_pipeline.service import ControlClient


def require(value, message):
    if not value:
        raise ValueError(message)


class CodingPerformanceError(ValueError):
    """Functional work completed, but measured SRS performance did not pass."""
    def __init__(self, failures):
        self.performance_failures=failures
        super().__init__('Functional coding completed; container startup SRS acceptance failed: '+
                         json.dumps(failures,sort_keys=True,allow_nan=False))


def startup_performance_failures(jobs,evidence):
    failures=[]
    for job in jobs:
        if job.get('kind')!='CODE_TEST' or job.get('status')!='COMPLETED':
            continue
        receipt=evidence.get(job['job_id'],{}).get('evidence',{})
        seconds=receipt.get('startup_seconds')
        measured=type(seconds) in (int,float) and math.isfinite(seconds) and seconds>=0
        clocks=[receipt.get(key) for key in ('request_started_at','execution_started_at','execution_startup_seconds')]
        clocks_valid=all(type(value) in (int,float) and math.isfinite(value) for value in clocks)
        measured_total=(clocks[1]-clocks[0]+clocks[2]) if clocks_valid else None
        if measured_total is not None and not math.isfinite(measured_total):
            measured_total=None
        consistent=(measured and measured_total is not None and clocks[1]>=clocks[0] and clocks[2]>=0
                    and abs(measured_total-seconds)<=1e-6)
        reason=('missing_or_inconsistent_startup_measurement' if not consistent or
                receipt.get('startup_sla_met') is not (seconds<=1.5) or receipt.get('startup_sla_seconds')!=1.5 else
                'request_to_test_ready_exceeded_1_5_seconds' if seconds>1.5 else None)
        if reason:
            keys=('startup_seconds','startup_sla_seconds','queue_wait_seconds','handoff_wait_seconds','execution_startup_seconds')
            timings={key:value if type(value:=receipt.get(key)) in (int,float) and math.isfinite(value) else None for key in keys}
            failures.append({'job_id':job['job_id'],'phase':job.get('payload',{}).get('phase'),
                'reason':reason,'measured_request_to_ready_seconds':measured_total,
                'evidence_sha256':evidence.get(job['job_id'],{}).get('sha256'),**timings})
    return failures


def validate_startup_acceptance(jobs, evidence):
    """Require actual prepared-pool provenance; measured latency is an observation."""
    for job in jobs:
        if job.get('kind') != 'CODE_TEST' or job.get('status') != 'COMPLETED':
            continue
        receipt = evidence.get(job['job_id'], {}).get('evidence', {})
        values = [receipt.get(key) for key in ('prepared_at', 'preparation_seconds',
                   'request_started_at', 'reservation_started_at', 'execution_started_at')]
        finite = all(type(value) in (int, float) and math.isfinite(value) for value in values)
        require(receipt.get('warm_pool_used') is True
                and receipt.get('container_creation_path') == 'background_pool_replenishment'
                and isinstance(receipt.get('container_id'), str)
                and re.fullmatch('[0-9a-f]{64}', receipt['container_id'])
                and finite and values[0] > 0 and values[1] >= 0
                and values[0] <= values[3] <= values[4] and values[2] <= values[3],
                'Coding execution has no verified prepared-container pool provenance')
        require(all(type(receipt.get(key)) in (int, float) and math.isfinite(receipt[key])
                    and abs(receipt[key] - expected) <= 1e-6 for key, expected in (
                        ('queue_wait_seconds', values[3] - values[2]),
                        ('handoff_wait_seconds', values[4] - values[3]))),
                'Coding prepared-pool wait timings are missing or inconsistent')
    observations = startup_performance_failures(jobs, evidence)
    invalid = [item for item in observations if item['reason'] == 'missing_or_inconsistent_startup_measurement']
    if invalid:
        raise CodingPerformanceError(invalid)
    return observations


def reject_fixture_evidence(value):
    kind=str(value.get('execution_kind','')).lower()
    require(not any(marker in kind for marker in ('fixture','mock','emulator','simulation')),
            'Storage-protocol fixtures are not live native acceptance evidence')


def validate_phase_ledger(records, leaves):
    """Check phase semantics, allowing failed historical gates before recovery."""
    require(isinstance(records,list) and records, 'Planning phase is missing')
    for item in records:
        require(isinstance(item,dict) and item.get('phase') in {'P'+str(n) for n in range(1,11)}
                and item.get('outcome') in {'COMPLETED','FAILED','SKIPPED'}, 'Invalid TDD phase outcome')
        require(type(item.get('leaf_index')) is int and 0<=item['leaf_index']<len(leaves),
                'Phase refers to an unknown planned leaf')
        require(type(item.get('cycle')) is int and 1<=item['cycle']<=10,
                'Phase has no valid immutable cycle budget')
        require(item.get('evidence_sha256')==digest(item.get('evidence')), 'Phase evidence digest mismatch')
        proof=item.get('evidence')
        require(isinstance(proof,dict), 'TDD phase evidence is missing')
        fields={'P1':('plan_sha256','review_sha256'),'P2':('dossier_sha256',),
                'P3':('test_receipt_sha256',),'P4':('artifact_sha256','receipt_sha256'),
                'P5':('test_receipt_sha256',),'P6':('review_sha256','test_receipt_sha256'),
                'P7':('artifact_sha256','receipt_sha256'),'P8':('next_work_sha256',),
                'P9':('artifact_sha256','receipt_sha256'),'P10':('review_sha256','test_receipt_sha256')}
        required=fields[item['phase']]
        if item['outcome']=='SKIPPED':
            reasons={'P7':'no_qualifying_minor_findings','P9':'all_tests_pass_no_open_findings'}
            require(item['phase'] in reasons and item.get('reason')==reasons[item['phase']],
                    'An unjustified TDD phase was skipped')
            if item['phase']=='P7': required=('reviews_sha256',)
            elif 'source_sha256' in proof: required=('source_sha256',)
        require(all(isinstance(proof.get(key),str) and re.fullmatch('[0-9a-f]{64}',proof[key])
                    for key in required), 'TDD phase evidence is missing required digests')
        if item['phase'] in {'P1','P2','P3','P4','P8'}:
            require(item['outcome']=='COMPLETED', 'Required TDD phase did not complete')
        if item['phase']=='P3':
            require(proof.get('red_verified') is True and proof.get('strategy')=='red_green',
                    'Failing-first P3 RED evidence is required')
        if item['phase']=='P8':
            require(proof.get('decision') in {'ACCEPT','CONTINUE'}, 'TDD next-work decision is missing')
    require(records[0]['phase']=='P1' and records[0]['leaf_index']==0 and
            sum(item['phase']=='P1' for item in records)==1,
            'Successful planning phase is missing')
    require([item['leaf_index'] for item in records]==sorted(item['leaf_index'] for item in records),
            'TDD phases do not follow the planned leaf order')
    final={}
    for index,leaf in enumerate(leaves):
        local=[item for item in records if item['leaf_index']==index and item['phase']!='P1']
        for phase in ('P2','P3','P4','P8'):
            require(any(item['phase']==phase and item['outcome']=='COMPLETED' for item in local),
                    'Leaf is missing a successful required TDD phase: '+phase)
        for phase in ('P7','P9'):
            require(any(item['phase']==phase and item['outcome'] in {'COMPLETED','SKIPPED'} for item in local),
                    'Leaf is missing executed or explicitly justified TDD phases')
        for phase in ('P5','P6'):
            require(any(item['phase']==phase and item['outcome'] in {'COMPLETED','FAILED'} for item in local),
                    'Leaf is missing an executed test or audit gate: '+phase)
        # P5/P6 failures can legitimately recover through improvement/refinement.
        # That recovery must finish with a successful final test AND audit gate.
        require(local and local[-1]['phase']=='P10' and local[-1]['outcome']=='COMPLETED',
                'Leaf has no successful final test and audit phase')
        require(local[0]['phase']=='P2', 'Leaf phases must begin with research')
        transitions={'P2':{'P3'},'P3':{'P4'},'P4':{'P5'},'P5':{'P6'},
                     'P6':{'P7','P3'},'P7':{'P8'},'P8':{'P9','P10'},
                     'P9':{'P10'},'P10':{'P7','P3'}}
        for previous,current in zip(local,local[1:]):
            require(current['phase'] in transitions[previous['phase']], 'TDD phases are out of order')
            require(previous['cycle']<=current['cycle'], 'TDD phase cycles are out of order')
            if previous['phase']=='P10' or (previous['phase']=='P6' and previous['outcome']=='FAILED'):
                require(current['cycle']>previous['cycle'], 'Rejected TDD round did not consume its cycle')
            if previous['phase']=='P5' and previous['outcome']=='FAILED':
                require(current['outcome']=='FAILED', 'Audit cannot complete with failing test evidence')
            if current['phase']=='P7' and current['outcome']=='SKIPPED':
                require(previous['phase']=='P6' and previous['outcome']=='COMPLETED',
                        'Improvement cannot be skipped after a rejected audit round')
            if current['phase']=='P3' and previous['phase']=='P6':
                require(previous['outcome']=='FAILED', 'Test reauthoring requires a rejected audit round')
        # A no-op refinement is deliberately recorded only after its final
        # test/audit succeeds. Failed historical rounds may omit P9 entirely.
        require(len(local)>1 and local[-2]['phase']=='P9' and
                local[-2]['outcome'] in {'COMPLETED','SKIPPED'},
                'Successful final test and audit has no preceding refinement gate')
        final[leaf]=local[-1]
    return final


def validate_audit_jobs(jobs, plan, final_phases, evidence):
    """Join audits to real stage records; a review's name alone proves nothing."""
    completed=[job for job in jobs if job.get('status')=='COMPLETED']
    # Rejected revisions remain immutable execution history. Acceptance joins
    # the final artifact set to its one producing job and successful audit.
    plan_reviews=[job for job in completed if job.get('kind')=='CODE_PLAN_REVIEW'
                  and job.get('payload',{}).get('plan_sha256')==plan['plan_sha256']
                  and job.get('output',{}).get('verdict')=='PASS']
    require(len(plan_reviews)==1, 'Completed planning execution and independent audit are required')
    review=plan_reviews[0]
    planners=[job for job in completed if job.get('kind')=='CODE_PLAN'
              and job['job_id']==review.get('payload',{}).get('planner_job_id')]
    require(len(planners)==1 and len(plan_reviews)==1, 'Completed planning execution and independent audit are required')
    planner,review=planners[0],plan_reviews[0]
    require(review['payload'].get('planner_receipt_sha256')==planner.get('receipt_sha256',digest(planner.get('receipt'))) and
            planner.get('receipt',{}).get('job_id')==planner['job_id'],
            'Planning audit is detached from its exact producing execution')
    require(evidence.get(planner['job_id'],{}).get('evidence',{}).get('plan')==plan,
            'Approved plan is detached from the executed planning stage')
    approved=validate_plan_review(review.get('output'),plan,planner_receipt=planner.get('receipt'),
                                  reviewer_receipt=review.get('receipt'))
    require(review.get('payload',{}).get('plan_sha256')==plan['plan_sha256'],
            'Planning audit task is bound to a different plan')
    reviewed={leaf:{} for leaf in final_phases}
    for job in completed:
        if job.get('kind')!='CODE_REVIEW':
            continue
        payload=job.get('payload',{}); output=job.get('output',{}); receipt=job.get('receipt',{})
        file=payload.get('file',{}); producer=payload.get('producer',{})
        require(producer.get('provider') and producer.get('model') and
                (receipt.get('provider'),receipt.get('requested_model')) != (producer['provider'],producer['model']),
                'A producing model cannot independently audit its own file')
        expected={'path':file.get('path'),'file_sha256':file.get('after_sha256'),
                  'diff_sha256':file.get('diff_sha256'),'test_receipt_sha256':payload.get('test_receipt_sha256')}
        require(expected['path'] and all(expected[key] for key in expected) and
                all(output.get(key)==value for key,value in expected.items()),
                'File audit does not bind its exact file, diff, and test receipt')
        require(type(output.get('approved')) is bool and type(output.get('objective_satisfied')) is bool,
                'File audit is missing a concrete verdict')
        leaf=payload.get('active_leaf',{}).get('id')
        final=final_phases.get(leaf)
        if (final is not None and payload.get('phase')=='P10' and
                payload.get('test_receipt_sha256')==final['evidence'].get('test_receipt_sha256')):
            require(output['approved'] and output['objective_satisfied'] and not output.get('minor_findings'),
                    'Final file audit did not approve the objective without open findings')
            reviewed[leaf][expected['path']]=job
    for leaf,final in final_phases.items():
        planned=next(item for item in plan['fracture_manifest']['leaves'] if item['id']==leaf)
        expected_paths=set(planned['file_targets'])
        authors=[job for job in completed if job.get('kind')=='CODE_TEST_AUTHOR'
                 and job.get('payload',{}).get('active_leaf',{}).get('id')==leaf]
        require(authors, 'Planned leaf has no executed test-authoring stage')
        author_evidence=evidence.get(authors[-1]['job_id'],{}).get('evidence')
        require(isinstance(author_evidence,dict) and isinstance(author_evidence.get('changes'),list),
                'Test-authoring changes have no controller evidence')
        expected_paths.update(change['path'] for change in author_evidence['changes'])
        require(expected_paths.issubset(reviewed[leaf]), 'Final independent audit is missing an implementation or test file')
    return approved


def validate_test_deadline(test):
    seconds=test.get('test_cycle_seconds')
    require(type(seconds) in (int,float) and math.isfinite(seconds) and 0<=seconds<=30
            and test.get('test_cycle_deadline_met') is True,
            'Docker test cycle has no verified execution within 30 seconds')


def validate_final_test(test):
    """Validate controller Docker metadata; this does not inspect the host."""
    require(isinstance(test,dict), 'Final Docker evidence is missing')
    reject_fixture_evidence(test)
    validate_test_deadline(test)
    require(test.get('kind')=='docker-test-execution' and
            isinstance(test.get('container_id'),str) and re.fullmatch('[0-9a-f]{64}',test['container_id']),
            'Final Docker evidence has no actual container identity')
    require(test.get('passed') is True and test.get('cleanup_verified') is True
            and test.get('source_verified') is True, 'Final physical Docker execution/cleanup did not pass')
    commands=test.get('commands')
    require(isinstance(commands,list) and commands and all(isinstance(item,dict) and
            item.get('passed') is True and type(item.get('exit_code')) is int and item['exit_code']==0
            and not any(item.get(key) for key in ('timed_out','cancelled','output_exceeded')) for item in commands),
            'Final commands did not actually pass')
    pytest_commands=[item for item in commands if item.get('kind')=='pytest']
    require(pytest_commands, 'Final acceptance has no executed passing test cases')
    for command in pytest_commands:
        counts=command.get('junit',{}); cases=command.get('junit_cases',[])
        require(all(type(counts.get(key)) is int and counts[key]>=0 for key in ('tests','failures','errors','skipped','passed'))
                and counts['tests']>0 and counts['passed']>0 and not counts['failures'] and not counts['errors']
                and counts['tests']==counts['passed']+counts['skipped'],
                'Final acceptance has no executed passing test cases')
        require(isinstance(cases,list) and len(cases)==counts['tests'] and
                all(isinstance(case,dict) and case.get('status') in {'passed','skipped'} for case in cases),
                'Final JUnit case details do not establish executed passing tests')
        for status in ('passed','skipped'):
            observed=sum(case['status']==status for case in cases)
            require(observed==counts[status],
                    'Final JUnit case details contradict the parsed execution counts')


def validate_red_test(test):
    """Require ordinary assertion failures in an actual pre-implementation run."""
    require(isinstance(test,dict), 'P3 Docker evidence is missing')
    reject_fixture_evidence(test)
    validate_test_deadline(test)
    require(test.get('kind')=='docker-test-execution' and
            isinstance(test.get('container_id'),str) and re.fullmatch('[0-9a-f]{64}',test['container_id']),
            'P3 Docker evidence has no actual container identity')
    require(test.get('passed') is False and test.get('failure_category')=='tests_failed' and
            test.get('cleanup_verified') is True and test.get('source_verified') is True,
            'P3 did not establish a verified failing-first test run')
    commands=test.get('commands')
    require(isinstance(commands,list) and commands and all(isinstance(command,dict) and
            type(command.get('exit_code')) is int and command['exit_code'] in (0,1) and
            command.get('passed') is (command['exit_code']==0) and
            not any(command.get(key) for key in ('timed_out','cancelled','output_exceeded'))
            for command in commands), 'P3 commands did not establish normal test execution')
    failed=False
    for command in commands:
        if command.get('kind')!='pytest':
            require(command['exit_code']==0, 'P3 contains a failing non-test command')
            continue
        counts=command.get('junit',{}); cases=command.get('junit_cases')
        require(isinstance(counts,dict) and all(type(counts.get(key)) is int and counts[key]>=0
                for key in ('tests','failures','errors','skipped','passed')) and
                counts['tests']>0 and counts['errors']==0 and
                counts['tests']==counts['passed']+counts['failures']+counts['skipped'] and
                command['exit_code']==(1 if counts['failures'] else 0),
                'P3 JUnit counts do not establish assertion-only RED evidence')
        require(isinstance(cases,list) and len(cases)==counts['tests'] and
                all(isinstance(case,dict) and case.get('status') in {'passed','failed','skipped'} for case in cases),
                'P3 JUnit case details are missing')
        for status,key in (('passed','passed'),('failed','failures'),('skipped','skipped')):
            require(sum(case['status']==status for case in cases)==counts[key],
                    'P3 JUnit case details contradict execution counts')
        failed=failed or counts['failures']>0
    require(failed, 'P3 has no executed assertion failure')


def validate_test_binding(job,row,expected):
    test=row.get('evidence',{})
    # The authenticated API redacts attempt_id from evidence. Its retained
    # digest authenticates the original controller record, not that projection.
    require(isinstance(test,dict) and isinstance(expected,str) and re.fullmatch('[0-9a-f]{64}',expected) and
            row.get('sha256')==expected and
            test.get('job_id')==job['job_id'], 'Leaf test evidence is detached from its execution')
    source=test.get('source',{}); files=source.get('files',{})
    require(isinstance(files,dict) and files and source.get('sha256')==digest(files) and
            all(isinstance(item,dict) and isinstance(item.get('sha256'),str) and
                re.fullmatch('[0-9a-f]{64}',item['sha256']) for item in files.values()),
            'Docker source manifest digest is inconsistent')
    snapshot=digest({path:item['sha256'] for path,item in files.items()})
    require(job.get('payload',{}).get('snapshot_sha256')==snapshot and
            job.get('output',{}).get('source_snapshot_sha256')==snapshot,
            'Docker source manifest is detached from the queued immutable snapshot')
    return test


def validate_sealed_test_identities(job,test):
    planned={item['name'] for item in job['payload'].get('test_cases',[])}
    identities=job['payload'].get('test_identities')
    require(planned and isinstance(identities,dict) and set(identities)==planned,
            'Leaf has no exact sealed test identities')
    files=test['source']['files']
    for name,identity in identities.items():
        require(isinstance(identity,dict) and identity.get('name')==name,
                'Sealed test identity does not match its planned name')
        path=identity.get('path')
        require(isinstance(path,str) and path.endswith('.py') and safe_path(path)==path,
                'Sealed test identity must identify a canonical Python file')
        module=path[:-3].replace('/','.')
        class_name=identity.get('class_name')
        require(isinstance(class_name,str) and (class_name==module or
                (class_name.startswith(module+'.') and class_name[len(module)+1:].isidentifier())),
                'Sealed test class does not match its canonical source module')
        sha=identity.get('file_sha256')
        require(isinstance(sha,str) and re.fullmatch('[0-9a-f]{64}',sha) and
                isinstance(files.get(path),dict) and files[path].get('sha256')==sha,
                'Sealed test source hash does not match the executed Docker snapshot')
    cases=[case for command in test['commands'] if command.get('kind')=='pytest'
           for case in command.get('junit_cases',[])]
    return {name:[case for case in cases if isinstance(case.get('name'),str) and
                  case['name'].split('[',1)[0]==name and case.get('class_name')==identity['class_name']]
            for name,identity in identities.items()}


def validate_precode_tests(records,plan,jobs,evidence):
    """Bind every completed P3 gate to its planned, sealed precode execution."""
    leaves=plan['fracture_manifest']['topological_order']
    for phase in records:
        if phase['phase']!='P3':
            continue
        proof=phase['evidence']; leaf=leaves[phase['leaf_index']]
        require(phase['outcome']=='COMPLETED' and proof.get('red_verified') is True and
                proof.get('strategy')=='red_green', 'Failing-first P3 RED evidence is required')
        expected=proof.get('test_receipt_sha256')
        candidates=[job for job in jobs if job.get('kind')=='CODE_TEST' and job.get('status')=='COMPLETED'
                    and job.get('payload',{}).get('phase')=='precode' and
                    job['payload'].get('active_leaf',{}).get('id')==leaf and
                    job.get('output',{}).get('test_receipt_sha256')==expected]
        require(len(candidates)==1, 'P3 has no exact completed precode Docker execution')
        job=candidates[0]; payload=job['payload']
        planned=next(item for item in plan['fracture_manifest']['leaves'] if item['id']==leaf)
        test_cases=[item for item in plan['test_cases'] if set(item['criteria_ids']) & set(planned['criteria_ids'])]
        require(payload.get('plan_sha256')==plan['plan_sha256'] and payload.get('active_leaf')==planned and
                payload.get('test_cases')==test_cases and type(phase.get('cycle')) is int and
                payload.get('cycle')==phase['cycle'], 'P3 test execution is detached from the approved leaf plan and cycle')
        test=validate_test_binding(job,evidence.get(job['job_id'],{}),expected)
        validate_red_test(test)
        matched=validate_sealed_test_identities(job,test)
        require(all(cases and all(case['status'] in {'passed','failed'} for case in cases)
                    for cases in matched.values()) and
                any(case['status']=='failed' for cases in matched.values() for case in cases),
                'P3 did not execute and fail an exact sealed planned test')


def validate_phase_tests(final_phases,jobs,evidence):
    for leaf,phase in final_phases.items():
        expected=phase['evidence'].get('test_receipt_sha256')
        candidates=[job for job in jobs if job.get('kind')=='CODE_TEST' and job.get('status')=='COMPLETED'
                    and job.get('payload',{}).get('phase')=='final'
                    and job['payload'].get('active_leaf',{}).get('id')==leaf
                    and job.get('output',{}).get('test_receipt_sha256')==expected]
        require(len(candidates)==1, 'Final leaf phase has no exact completed Docker execution')
        job=candidates[0]; row=evidence.get(job['job_id'],{})
        test=validate_test_binding(job,row,expected)
        validate_final_test(test)
        for matched in validate_sealed_test_identities(job,test).values():
            require(matched and all(case.get('status')=='passed' for case in matched),
                    'Final leaf did not execute every exact sealed planned test')


def validate_production_planning(state, plan, jobs, evidence):
    """Verify the installed 4.2.7 contract through real stage and artifact joins."""
    from .planning_governance import normalize_policy, execution_contract, validate_execution_history
    policy = normalize_policy(state.get('project', {}).get('planning'))
    registration = state.get('planning_evidence') or {}
    contract = execution_contract()
    require(registration.get('schema') == 'planning-source-registration/2'
            and registration.get('specification_id') == contract['specification_id']
            and registration.get('contract') == contract
            and registration.get('contract_sha256') == digest(contract)
            and registration.get('policy_sha256') == digest(policy)
            and registration.get('source_manifest_sha256') == state.get('original_snapshot'),
            'Production acceptance requires source-bound canonical execution policy')
    expected = {key: value for key, value in registration.items() if key != 'external_sources'}
    require(plan.get('planning_registration') == expected,
            'Final plan is detached from its captured source and execution contract')
    artifacts = plan.get('artifacts', {})
    require('ExecutionContract.json' in artifacts and json.loads(artifacts['ExecutionContract.json']) == contract,
            'Final planning audit has no exact canonical execution contract')
    require(plan.get('artifact_hashes', {}).get('ExecutionContract.json') ==
            hashlib.sha256(artifacts['ExecutionContract.json'].encode()).hexdigest(),
            'Canonical execution contract is outside the audited artifact hash set')
    if policy.get('research_sources'):
        from .planning_governance import validate_external_research
        sources = registration.get('external_sources', [])
        for job in jobs:
            if job.get('kind') == 'CODE_RESEARCH' and job.get('status') == 'COMPLETED':
                refs = job['payload'].get('active_leaf', {}).get('requirement_ids') or ['R' + str(index) for index in range(1, len(job['payload']['requirements']) + 1)]
                verified = validate_external_research(job.get('output', {}), sources, refs)
                actual = evidence.get(job['job_id'], {}).get('evidence', {})
                require(all(actual.get(key) == value for key, value in verified.items()),
                        'Research evidence is detached from registered HTTPS sources')
    return validate_execution_history(jobs, evidence)


def validate_coding_workflow(workflow):
    require(isinstance(workflow,dict) and workflow.get('status')=='COMPLETED', 'Coding workflow is not completed')
    state=workflow.get('coding',{})
    require(state.get('status')=='COMPLETED', 'Coding state is not completed')
    require(workflow.get('integration_reconciliation_required') is False, 'Git publication still requires reconciliation')
    plan=state.get('plan',{})
    require(plan.get('plan_sha256')==digest({key:value for key,value in plan.items() if key!='plan_sha256'}),
            'Approved plan digest does not match its content')
    for name,text in plan.get('artifacts',{}).items():
        require(plan.get('artifact_hashes',{}).get(name)==hashlib.sha256(text.encode()).hexdigest(),
                'Plan artifact hash mismatch: '+name)
    leaves=plan.get('fracture_manifest',{}).get('topological_order',[])
    require(leaves and [item['id'] for item in state.get('completed_leaves',[])]==leaves,
            'Not every planned leaf has an accepted result')
    chunks=state.get('chunks',[])
    require(isinstance(chunks,list) and len(chunks)==len(leaves), 'Every leaf requires a bounded staged Git patch')
    for chunk in chunks:
        staged=chunk.get('staged',{}); bounds=chunk.get('bounds',{})
        require(staged.get('max_changed_lines')==100 and type(staged.get('changed_lines')) is int
                and 1<=staged['changed_lines']<=100, 'Staged Git patch has no enforced physical diff ceiling')
        require(isinstance(staged.get('patch_sha256'),str) and re.fullmatch('[0-9a-f]{64}',staged['patch_sha256'])
                and isinstance(staged.get('patch_path'),str) and staged['patch_path'].endswith('.patch'),
                'Staged Git patch evidence is missing')
        require(type(bounds.get('context_lines')) is int and 20<=bounds['context_lines']<=100
                and isinstance(bounds.get('context_windows'),list) and bounds['context_windows']
                and all(isinstance(item,dict) and type(item.get('line_count')) is int and item['line_count']>0
                        and type(item.get('start_line')) is int and item['start_line']>0
                        and type(item.get('end_line')) is int and item['end_line']-item['start_line']+1==item['line_count']
                        and item.get('side') in {'before','after'}
                        and isinstance(item.get('content_sha256'),str) and re.fullmatch('[0-9a-f]{64}',item['content_sha256'])
                        for item in bounds['context_windows'])
                and sum(item.get('line_count',0) for item in bounds['context_windows'])==bounds['context_lines'],
                'Leaf has no verified 20–100-line physical context')
    records=state.get('phase_ledger',[])
    final_phases=validate_phase_ledger(records,leaves)
    native=[]
    for job in workflow.get('jobs',[]):
        if job.get('status')!='COMPLETED' or job.get('kind') in {'CODE_REQUEST','CODE_TEST','CODE_INTEGRATE'}:
            continue
        receipt=job.get('receipt') or {}
        reject_fixture_evidence(receipt)
        require(receipt.get('subscription_verified') is True and type(receipt.get('pid')) is int
                and 0<receipt['pid']<=4294967295 and type(receipt.get('exit_code')) is int
                and receipt['exit_code']==0 and isinstance(receipt.get('session_id'),str) and receipt['session_id'],
                'Missing successful native subscription execution evidence')
        require(receipt.get('provider') in {'codex','claude','gemini'} and receipt.get('requested_model'),
                'Native provider/model identity is missing')
        require(receipt.get('reported_model') in (None,receipt['requested_model']), 'Native model identity mismatch')
        require(receipt.get('output_sha256')==digest(job.get('output')), 'Native output hash mismatch')
        require(re.fullmatch('[0-9a-f]{64}',receipt.get('stdout_sha256','')), 'Native stdout digest is missing')
        native.append({'job_id':job['job_id'],'kind':job['kind'],'provider':receipt['provider'],
                       'model':receipt['requested_model'],'stdout_sha256':receipt['stdout_sha256']})
    evidence={item['job_id']:item for item in workflow.get('evidence',[])}
    approved=validate_audit_jobs(workflow.get('jobs',[]),plan,final_phases,evidence)
    require(approved==state.get('plan_review'), 'Accepted plan review is detached from its native audit')
    validate_precode_tests(records,plan,workflow.get('jobs',[]),evidence)
    validate_phase_tests(final_phases,workflow.get('jobs',[]),evidence)
    test=state.get('last_test') or {}
    validate_final_test(test)
    test_job=next((job for job in workflow.get('jobs',[]) if job.get('job_id')==test.get('job_id')),None)
    require(test_job is not None and test_job.get('kind')=='CODE_TEST' and test_job.get('status')=='COMPLETED'
            and test_job.get('payload',{}).get('phase')=='final' and
            evidence.get(test_job['job_id'],{}).get('evidence')==test and
            evidence[test_job['job_id']]['sha256']==test_job.get('output',{}).get('test_receipt_sha256'),
            'Final Docker evidence is detached from its completed execution stage')
    commit=state.get('last_commit','')
    require(re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',commit), 'Result commit is missing')
    require(any(item.get('state')=='APPLIED' and item.get('result_commit')==commit
                and item.get('source_sha256')==state.get('current_snapshot')
                and item.get('test_sha256')==evidence[test_job['job_id']]['sha256']
                and item.get('reviews_sha256')==digest(state.get('reviews'))
                for item in workflow.get('integration_intents',[])), 'Final Git CAS has no durable applied receipt')
    execution = validate_production_planning(state, plan, workflow.get('jobs', []), evidence)
    performance_failures = validate_startup_acceptance(workflow.get('jobs', []), evidence)
    return {'schema':1,'scope':'one completed coding workflow','workflow_id':workflow['workflow_id'],
            'verified_at':time.time(),'accepted':True,'workflow_sha256':digest(workflow),
            'plan_sha256':plan['plan_sha256'],'completed_leaves':leaves,'result_commit':commit,
            'native_executions':native,'final_test_receipt_sha256':digest(test),
            'functional_workflow_completed':True, 'execution_contract':execution,
            'performance_failures':[], 'performance_observations':performance_failures,
            'performance_acceptance':'S427-PERF owner amendment: valid measured latency is nonblocking; Windows launch benchmark pending',
            'native_evidence_source':'authenticated controller receipts; private stdout is hash-retained',
            'entire_host_certified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-config',required=True,type=Path)
    parser.add_argument('--workflow-id',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    config=json.loads(args.client_config.read_text(encoding='utf-8-sig'))
    client=ControlClient(config.get('port',47824),config['token_file'])
    workflow=client.call('/coding/workflow/'+args.workflow_id)
    try:
        report=validate_coding_workflow(workflow)
    except CodingPerformanceError as error:
        report={'schema':1,'scope':'one completed coding workflow','workflow_id':args.workflow_id,
                'verified_at':time.time(),'accepted':False,'functional_workflow_completed':True,
                'workflow_sha256':digest(workflow),
                'result_commit':workflow['coding']['last_commit'],
                'performance_failures':error.performance_failures,'entire_host_certified':False}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2,ensure_ascii=False,allow_nan=False)
    print(json.dumps({'accepted':report['accepted'],'report':str(args.output),'result_commit':report['result_commit'],
                      'performance_failures':report.get('performance_failures',[])}))
    if not report['accepted']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
