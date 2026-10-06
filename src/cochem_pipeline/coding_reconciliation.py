"""Controller-bound SRS/WBS reconciliation and honest predispatch size estimates.

Model verdicts are attributed judgments. Physical source/test/audit commitments,
traceability coverage and native model asymmetry are independently checked.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from .coding import digest


def estimate_leaf_sizes(leaves, source_files, *, raw_leaves=()):
    raw={leaf['id']:leaf for leaf in raw_leaves}
    rows=[]
    for leaf in leaves:
        path=leaf['file_targets'][0]
        physical=source_files.get(path,b'')
        declared=raw.get(leaf['id'],{})
        estimate=declared.get('estimated_added_deleted_lines')
        if estimate is not None and (type(estimate) is not int or not 1<=estimate<=100000):
            raise ValueError('Estimated added plus deleted lines must be a positive bounded integer')
        reasons=[]
        if estimate is None:
            reasons.append('missing_source_and_test_change_estimate')
        if not 20<=leaf['estimated_context_lines']<=100:
            reasons.append('planned_context_outside_20_100')
        if estimate is not None and estimate>100:
            reasons.append('planned_source_and_test_diff_exceeds_100')
        row={'leaf_id':leaf['id'],'file_target':path,
             'source_present':path in source_files,
             'source_sha256':hashlib.sha256(physical).hexdigest(),
             'physical_source_lines':len(physical.splitlines()),
             'estimated_context_lines':leaf['estimated_context_lines'],
             'estimated_added_deleted_lines':estimate,
             'estimate_source':'model_plan_with_controller_source_inventory',
             'physical_diff_verified':False,'dispatch_ready':not reasons,
             'blocking_reasons':reasons,
             'remediation':None if not reasons else {
                 'action':'further_fracture_before_dispatch',
                 'preserve_requirement_ids':list(leaf['requirement_ids']),
                 'preserve_criteria_ids':list(leaf['criteria_ids']),
                 'single_implementation_target':path,
                 'context_range':[20,100],'combined_source_test_changed_line_limit':100,
                 'instructions':'Split the change into ordered N=1 leaves with reciprocal requirement, criterion and test coverage; preserve dependencies and re-audit the complete plan.'}}
        rows.append(row)
    return {'schema':'leaf-size-estimates/1','physical_diff_proof_required_after_edit':True,
            'all_dispatch_ready':all(row['dispatch_ready'] for row in rows),'leaves':rows}


def reconciliation_manifest(state):
    plan=state['plan']
    leaf_id=plan['fracture_manifest']['topological_order'][state['leaf_index']]
    leaf=next(item for item in plan['fracture_manifest']['leaves'] if item['id']==leaf_id)
    final=state['leaf_index']+1==len(plan['fracture_manifest']['topological_order'])
    selected_leaves=plan['fracture_manifest']['leaves'] if final else [leaf]
    requirement_ids=set(key for item in selected_leaves for key in item['requirement_ids'])
    criterion_ids=set(key for item in selected_leaves for key in item['criteria_ids'])
    chapters=[item for item in plan['srs']['chapters'] if set(item['requirement_ids']) & requirement_ids]
    criteria=[item for item in plan['acceptance_criteria'] if item['id'] in criterion_ids]
    tests=[item for item in plan['test_cases'] if set(item['criteria_ids']) & criterion_ids]
    prior=[chunk for chunk in state['chunks'] if chunk.get('reconciliation_manifest',{}).get('active_leaf_id')!=leaf_id] if final else []
    identities={name:value for chunk in prior for name,value in chunk['reconciliation_manifest']['sealed_test_identities'].items()}
    identities.update(state['test_identities'])
    changes={item['path']:item for chunk in prior for item in chunk['reconciliation_manifest']['changes']}
    changes.update({item['path']:item for item in state['changes']+state['test_changes']})
    return {'schema':'srs-wbs-reconciliation/1','reconciliation_scope':'workflow' if final else 'leaf',
            'active_leaf_id':leaf_id,'wbs_leaves':deepcopy(selected_leaves),
            'prior_leaf_reconciliations':[deepcopy(chunk['reconciliation']) for chunk in prior],
            'plan_sha256':plan['plan_sha256'],
            'artifact_hashes':deepcopy(plan['artifact_hashes']),
            'source_snapshot_sha256':state['current_snapshot'],
            'test_receipt_sha256':digest(state['last_test']),
            'file_reviews_sha256':digest(state['reviews']),
            'file_reviews':deepcopy(state['reviews']),
            'test_results':[{'name':item['name'],'passed':item['passed'],
                'junit_cases':deepcopy(item.get('junit_cases',[]))}
                for item in state['last_test'].get('commands',[])],
            'requirements':{key:plan['requirements'][key] for key in plan['requirements'] if key in requirement_ids},
            'srs_chapters':deepcopy(chapters),'wbs_leaf':deepcopy(leaf),
            'acceptance_criteria':deepcopy(criteria),'test_cases':deepcopy(tests),
            'sealed_test_identities':deepcopy(identities),
            'changes':deepcopy(list(changes.values()))}


def validate_reconciliation(output, manifest, receipt, producer, *, allow_rejection=False):
    if not isinstance(output,dict) or type(output.get('approved')) is not bool:
        raise ValueError('SRS/WBS reconciliation requires an explicit verdict')
    if output.get('reconciliation_manifest_sha256')!=digest(manifest):
        raise ValueError('SRS/WBS reconciliation is detached from captured source, plan, tests or file reviews')
    if (not isinstance(receipt,dict) or receipt.get('subscription_verified') is not True
            or receipt.get('exit_code')!=0 or receipt.get('output_sha256')!=digest(output)
            or type(receipt.get('pid')) is not int or receipt['pid']<=0
            or receipt.get('provider') not in {'claude','codex','gemini'}
            or receipt.get('provider')==producer.get('provider')):
        raise ValueError('SRS/WBS reconciliation requires a successful native execution from a different provider')
    rows=output.get('requirements_checked')
    required=manifest['requirements']
    if (not isinstance(rows,list) or len(rows)!=len(required)
            or any(not isinstance(row,dict) or row.get('requirement_id') not in required for row in rows)
            or len({row['requirement_id'] for row in rows})!=len(rows)):
        raise ValueError('SRS/WBS reconciliation must cover every active leaf requirement exactly once')
    for row in rows:
        requirement=row['requirement_id']
        chapters=sorted(item['id'] for item in manifest['srs_chapters'] if requirement in item['requirement_ids'])
        criteria=sorted(item['id'] for item in manifest['acceptance_criteria'] if requirement in item['requirement_ids'])
        tests=sorted(item['id'] for item in manifest['test_cases'] if set(item['criteria_ids']) & set(criteria))
        owners=sorted(item['id'] for item in manifest['wbs_leaves'] if requirement in item['requirement_ids'])
        if (row.get('chapter_ids')!=chapters or row.get('criteria_ids')!=criteria
                or row.get('test_ids')!=tests or row.get('wbs_leaf_ids')!=owners or row.get('wbs_leaf_id')!=owners[0]
                or row.get('status') not in {'SATISFIED','DIVERGED'}
                or not isinstance(row.get('rationale'),str) or not 12<=len(row['rationale'].strip())<=4000):
            raise ValueError('SRS/WBS reconciliation must explicitly join each requirement to its exact chapters, WBS leaf, criteria and tests')
    divergences=output.get('divergences')
    if (not isinstance(divergences,list) or len(divergences)>200
            or any(not isinstance(item,str) or not 12<=len(item.strip())<=4000 for item in divergences)):
        raise ValueError('SRS/WBS reconciliation divergences require bounded concrete findings')
    approved=output['approved']
    if approved and (divergences or any(row['status']!='SATISFIED' for row in rows)):
        raise ValueError('SRS/WBS divergence prevents final approval')
    if not approved and (not allow_rejection or not divergences):
        raise ValueError('SRS/WBS reconciliation did not approve the implementation')
    return {'schema':'srs-wbs-reconciliation-verification/1','approved':approved,
            'manifest_sha256':digest(manifest),'output_sha256':digest(output),
            'receipt_sha256':digest(receipt),'reviewer_provider':receipt['provider'],
            'reviewer_model':receipt['requested_model'],'producer':deepcopy(producer),
            'source_snapshot_sha256':manifest['source_snapshot_sha256'],
            'test_receipt_sha256':manifest['test_receipt_sha256'],
            'file_reviews_sha256':manifest['file_reviews_sha256'],
            'plan_sha256':manifest['plan_sha256'],'requirements_checked':deepcopy(rows)}
