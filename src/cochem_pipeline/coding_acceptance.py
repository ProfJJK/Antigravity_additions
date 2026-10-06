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
from pathlib import Path
import re
import time

from cochem_pipeline.coding import digest, safe_path
from cochem_pipeline.coding_plan import validate_plan_review
from cochem_pipeline.service import ControlClient


def require(value, message):
    if not value:
        raise ValueError(message)


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
        require(item.get('evidence_sha256')==digest(item.get('evidence')), 'Phase evidence digest mismatch')
        if item['outcome']=='SKIPPED':
            reasons={'P7':'no_qualifying_minor_findings','P9':'all_tests_pass_no_open_findings'}
            require(item['phase'] in reasons and item.get('reason')==reasons[item['phase']],
                    'An unjustified TDD phase was skipped')
    require(any(item['phase']=='P1' and item['outcome']=='COMPLETED' for item in records),
            'Successful planning phase is missing')
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
        final[leaf]=local[-1]
    return final


def validate_audit_jobs(jobs, plan, final_phases, evidence):
    """Join audits to real stage records; a review's name alone proves nothing."""
    completed=[job for job in jobs if job.get('status')=='COMPLETED']
    planners=[job for job in completed if job.get('kind')=='CODE_PLAN']
    plan_reviews=[job for job in completed if job.get('kind')=='CODE_PLAN_REVIEW']
    require(len(planners)==1 and len(plan_reviews)==1, 'Completed planning execution and independent audit are required')
    planner,review=planners[0],plan_reviews[0]
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


def validate_final_test(test):
    """Validate controller Docker metadata; this does not inspect the host."""
    require(isinstance(test,dict), 'Final Docker evidence is missing')
    reject_fixture_evidence(test)
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


def validate_phase_tests(final_phases,jobs,evidence):
    for leaf,phase in final_phases.items():
        expected=phase['evidence'].get('test_receipt_sha256')
        candidates=[job for job in jobs if job.get('kind')=='CODE_TEST' and job.get('status')=='COMPLETED'
                    and job.get('payload',{}).get('phase')=='final'
                    and job['payload'].get('active_leaf',{}).get('id')==leaf
                    and job.get('output',{}).get('test_receipt_sha256')==expected]
        require(len(candidates)==1, 'Final leaf phase has no exact completed Docker execution')
        job=candidates[0]; row=evidence.get(job['job_id'],{})
        require(row.get('sha256')==expected and row.get('evidence',{}).get('job_id')==job['job_id'],
                'Final leaf test evidence is detached from its execution')
        validate_final_test(row['evidence'])
        planned={item['name'] for item in job['payload'].get('test_cases',[])}
        identities=job['payload'].get('test_identities')
        require(planned and isinstance(identities,dict) and set(identities)==planned,
                'Final leaf has no exact sealed test identities')
        source=row['evidence'].get('source',{})
        files=source.get('files',{})
        require(isinstance(files,dict) and source.get('sha256')==digest(files),
                'Final Docker source manifest digest is inconsistent')
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
            require(isinstance(sha,str) and re.fullmatch('[0-9a-f]{64}',sha)
                    and isinstance(files.get(path),dict) and files[path].get('sha256')==sha,
                    'Sealed test source hash does not match the executed Docker snapshot')
        cases=[case for command in row['evidence']['commands'] for case in command.get('junit_cases',[])]
        for name,identity in identities.items():
            matched=[case for case in cases if case.get('name','').split('[',1)[0]==name
                     and case.get('class_name')==identity['class_name']]
            require(matched and all(case.get('status')=='passed' for case in matched),
                    'Final leaf did not execute every exact sealed planned test')


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
    return {'schema':1,'scope':'one completed coding workflow','workflow_id':workflow['workflow_id'],
            'verified_at':time.time(),'accepted':True,'workflow_sha256':digest(workflow),
            'plan_sha256':plan['plan_sha256'],'completed_leaves':leaves,'result_commit':commit,
            'native_executions':native,'final_test_receipt_sha256':digest(test),
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
    report=validate_coding_workflow(workflow)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2,ensure_ascii=False,allow_nan=False)
    print(json.dumps({'accepted':True,'report':str(args.output),'result_commit':report['result_commit']}))


if __name__=='__main__':
    main()
