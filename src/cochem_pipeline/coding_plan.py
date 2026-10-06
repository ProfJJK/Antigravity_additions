"""Validated planning artifacts, Fracture Manifest and the legacy TDD protocol.

The controller owns these deterministic gates. Model text cannot attest that a
phase ran, that a test passed, or that a native reviewer executed. Callers bind
the returned artifacts to their fenced SQLite transition and native receipts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from collections.abc import Mapping

from .coding import CodingProject,digest,observed_changes,safe_path,within

TDD_PHASES=(('P1','PLAN'),('P2','RESEARCH'),('P3','TEST_FIRST_RED'),('P4','CODE'),
            ('P5','TEST'),('P6','AUDIT'),('P7','IMPROVE'),('P8','PLAN_NEXT'),
            ('P9','REFINE'),('P10','TEST_AND_AUDIT'))
_ID=re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}')
_SHA=re.compile(r'[0-9a-f]{64}')


def _text(value,name,*,limit=64000):
    if not isinstance(value,str) or not value.strip() or '\x00' in value or len(value)>limit:
        raise ValueError(name+' must be nonempty bounded text')
    return value


def _id(value,name):
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise ValueError(name+' must be a simple identifier')
    return value


def _ids(value,name,allowed):
    if (not isinstance(value,list) or not value or any(not isinstance(item,str) for item in value)
            or len(value)!=len(set(value)) or any(item not in allowed for item in value)):
        raise ValueError(name+' must reference distinct declared identifiers')
    return list(value)


def _rows(value,name,maximum=200):
    if not isinstance(value,list) or not 1<=len(value)<=maximum or any(not isinstance(item,dict) for item in value):
        raise ValueError(name+' must be a bounded nonempty list of records')
    return value


def _unique(records,name):
    result={}
    for item in records:
        identifier=_id(item.get('id'),name+' id')
        if identifier in result:
            raise ValueError(name+' contains a duplicate identifier')
        result[identifier]=item
    return result


def validate_plan(plan: dict, project: CodingProject, source_files: Mapping[str,bytes],
                  requirements: list[str]) -> dict:
    """Validate substantive traceability and generate the actual plan artifact DAG.

    Each N=1 leaf has one implementation target and a 20–100 line planned work
    scope. Physical changes remain independently bounded by observed_changes;
    a complete-file transport block does not authorize a whole-file rewrite.
    """
    if not isinstance(plan,dict) or not isinstance(project,CodingProject):
        raise ValueError('Planning requires an object and a registered project')
    if not isinstance(requirements,list) or not requirements or len(requirements)>200:
        raise ValueError('Planning requires one to two hundred requirements')
    requirement_map={'R'+str(index):_text(value,'requirement',limit=16000) for index,value in enumerate(requirements,1)}
    goal=_text(plan.get('goal'),'goal',limit=16000)
    srs=plan.get('srs')
    if not isinstance(srs,dict):
        raise ValueError('Planning must produce a modular SRS')
    skeleton=_text(srs.get('skeleton'),'SRS skeleton')
    if len(skeleton.splitlines())>400:
        raise ValueError('SRS skeleton exceeds 400 lines')
    chapters=_rows(srs.get('chapters'),'SRS chapters',100)
    _unique(chapters,'SRS chapter')
    artifacts={'srs/00_skeleton.md':skeleton}
    covered=set()
    canonical_chapters=[]
    for index,chapter in enumerate(chapters,1):
        if chapter['id']!=f'ch{index:02d}':
            raise ValueError('SRS chapters must use contiguous ch01..chNN identifiers')
        title=_text(chapter.get('title'),'SRS title',limit=256)
        content=_text(chapter.get('text'),'SRS chapter')
        rendered='# '+title+'\n\n'+content
        if len(rendered.splitlines())>400:
            raise ValueError('Each rendered SRS chapter must contain at most 400 lines')
        refs=_ids(chapter.get('requirement_ids'),'SRS requirements',requirement_map)
        covered.update(refs)
        artifacts[f"srs/{chapter['id']}.md"]=rendered
        canonical_chapters.append({'id':chapter['id'],'title':title,'text':content,'requirement_ids':refs})
    if covered!=set(requirement_map):
        raise ValueError('Every requirement must be covered by a modular SRS chapter')
    criteria=_unique(_rows(plan.get('acceptance_criteria'),'acceptance criteria'),'acceptance criteria')
    tests=_unique(_rows(plan.get('test_cases'),'test cases'),'test cases')
    canonical_criteria=[]; criteria_coverage=set(); traced_tests=set()
    for identifier,item in criteria.items():
        refs=_ids(item.get('requirement_ids'),'criterion requirements',requirement_map)
        test_ids=_ids(item.get('test_ids'),'criterion tests',tests)
        criteria_coverage.update(refs); traced_tests.update(test_ids)
        canonical_criteria.append({'id':identifier,'statement':_text(item.get('statement'),'criterion statement',limit=8000),
                                   'requirement_ids':refs,'test_ids':test_ids})
    canonical_tests=[]
    for identifier,item in tests.items():
        name=_text(item.get('name'),'test name',limit=128)
        if not re.fullmatch(r'test_[A-Za-z0-9_]+',name):
            raise ValueError('Test cases require concrete pytest test names')
        refs=_ids(item.get('criteria_ids'),'test criteria',criteria)
        if any(identifier not in criteria[ref]['test_ids'] for ref in refs):
            raise ValueError('Test/criterion traceability must be reciprocal')
        canonical_tests.append({'id':identifier,'name':name,'asserts':_text(item.get('asserts'),'test assertion',limit=8000),
                                'criteria_ids':refs})
    if criteria_coverage!=set(requirement_map) or traced_tests!=set(tests):
        raise ValueError('Every requirement and test must be traced to acceptance criteria')
    if len({item['name'] for item in canonical_tests})!=len(canonical_tests):
        raise ValueError('Test cases must use distinct pytest test names')
    traced={item['id']:item for item in canonical_tests}
    if any(item['id'] not in traced[test]['criteria_ids'] for item in canonical_criteria for test in item['test_ids']):
        raise ValueError('Test/criterion traceability must be reciprocal in both directions')
    leaves=_unique(_rows(plan.get('leaves'),'fracture leaves',200),'fracture leaves')
    canonical_leaves=[]; leaf_criteria=set(); leaf_requirements=set()
    for identifier,item in leaves.items():
        targets=item.get('file_targets')
        if not isinstance(targets,list) or len(targets)!=1:
            raise ValueError('Strict N=1 fracture leaves require exactly one implementation file target')
        target=safe_path(targets[0])
        if not within(target,project.allowed_paths) or within(target,project.test_paths):
            raise ValueError('Fracture target is outside registered source scope')
        if any(name.startswith(target+'/') for name in source_files):
            raise ValueError('Fracture target must identify a file, not a directory')
        size=item.get('estimated_changed_lines')
        if type(size) is not int or not 20<=size<=100:
            raise ValueError('A fracture leaf must plan a 20–100 line work chunk')
        refs=_ids(item.get('requirement_ids'),'leaf requirements',requirement_map)
        ac=_ids(item.get('criteria_ids'),'leaf criteria',criteria)
        if any(not set(criteria[criterion]['requirement_ids']).issubset(refs) for criterion in ac):
            raise ValueError('Leaf requirements must include its acceptance-criterion requirements')
        dependencies=item.get('dependencies',[])
        if (not isinstance(dependencies,list) or any(not isinstance(value,str) for value in dependencies)
                or len(dependencies)!=len(set(dependencies)) or identifier in dependencies
                or any(value not in leaves for value in dependencies)):
            raise ValueError('Fracture dependencies must reference other distinct leaves')
        leaf_criteria.update(ac); leaf_requirements.update(refs)
        canonical_leaves.append({'id':identifier,'N':1,'objective':_text(item.get('objective'),'leaf objective',limit=8000),
            'requirement_ids':refs,'criteria_ids':ac,'file_targets':[target],
            'estimated_changed_lines':size,'dependencies':list(dependencies)})
    if leaf_criteria!=set(criteria) or leaf_requirements!=set(requirement_map):
        raise ValueError('Fracture leaves must cover every criterion and requirement')
    remaining={item['id']:set(item['dependencies']) for item in canonical_leaves}; ordered=[]
    while remaining:
        ready=sorted(name for name,deps in remaining.items() if not deps)
        if not ready:
            raise ValueError('Fracture dependency graph contains a cycle')
        for name in ready:
            ordered.append(name); remaining.pop(name)
        for deps in remaining.values(): deps.difference_update(ready)
    graph={'schema':'4.2.5-fracture-graph/1','nodes':ordered,
           'edges':[[dependency,item['id']] for item in canonical_leaves for dependency in item['dependencies']]}
    mermaid='flowchart TD\n'+'\n'.join('  '+name for name in ordered)+'\n'
    mermaid+=''.join('  '+left+' --> '+right+'\n' for left,right in graph['edges'])
    batches=[]
    for offset in range(0,len(ordered),20):
        batch={'schema':'4.1.1-wbs-node/1','id':f'B{offset//20+1:02d}','leaf_ids':ordered[offset:offset+20]}
        batches.append(batch)
        text=json.dumps(batch,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
        artifacts[f"wbs/batches/{batch['id']}.json"]=text
        artifacts[f"WBS_Micro_Prompts/{batch['id']}.json"]=text
    fracture={'schema':'4.2.5-fracture-manifest/1','N':1,'max_tasks_per_batch':20,'chunk_lines':[20,100],
              'leaves':canonical_leaves,'topological_order':ordered,'graph':graph,'mermaid':mermaid,'batches':batches}
    artifacts['graph.json']=json.dumps(graph,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    artifacts['graph.mmd']=mermaid
    artifacts['FractureManifest.json']=json.dumps(fracture,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    result={'schema':'4.2.5-coding-plan/1','goal':goal,'requirements':requirement_map,
            'srs':{'skeleton':skeleton,'chapters':canonical_chapters},'acceptance_criteria':canonical_criteria,
            'test_cases':canonical_tests,'fracture_manifest':fracture,'phases':[{'id':key,'name':name} for key,name in TDD_PHASES],
            'artifacts':artifacts,'artifact_hashes':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in artifacts.items()}}
    result['plan_sha256']=digest(result)
    return result


def validate_plan_review(review,plan,*,planner_receipt,reviewer_receipt):
    if not isinstance(plan,dict) or plan.get('plan_sha256')!=digest({key:value for key,value in plan.items() if key!='plan_sha256'}):
        raise ValueError('Planning audit requires an intact controller-hashed plan')
    if not isinstance(review,dict) or review.get('verdict')!='PASS' or review.get('plan_sha256')!=plan.get('plan_sha256'):
        raise ValueError('Planning requires an independent PASS bound to the exact plan hash')
    def native_identity(receipt):
        if (not isinstance(receipt,dict) or receipt.get('subscription_verified') is not True
                or type(receipt.get('exit_code')) is not int or receipt['exit_code']!=0
                or type(receipt.get('pid')) is not int or not 0<receipt['pid']<=4294967295
                or receipt.get('execution_kind') in {'emulator','fixture','mock','simulation'}
                or not isinstance(receipt.get('output_sha256'),str) or not _SHA.fullmatch(receipt['output_sha256'])):
            raise ValueError('Plan audit requires a successful verified native subscription receipt')
        provider=receipt.get('provider'); model=receipt.get('reported_model') or receipt.get('requested_model')
        if not isinstance(provider,str) or not isinstance(model,str) or not provider or not model:
            raise ValueError('Plan audit receipt must identify the actual provider and requested/reported model')
        if (receipt.get('reported_model') is not None and receipt['reported_model']!=receipt.get('requested_model')):
            raise ValueError('Plan audit receipt reported a different model than requested')
        if not any(isinstance(receipt.get(key),str) and receipt[key] for key in ('session_id','thread_id')):
            raise ValueError('Plan audit receipt requires a native session identity')
        return provider,model
    planner=native_identity(planner_receipt); reviewer=native_identity(reviewer_receipt)
    if planner==reviewer:
        raise ValueError('The planning producer cannot independently audit its own plan')
    if set(_ids(review.get('requirements_checked'),'audited requirements',plan['requirements']))!=set(plan['requirements']):
        raise ValueError('Planning audit must cover every requirement')
    findings=review.get('findings')
    if not isinstance(findings,list) or len(findings)>200:
        raise ValueError('Planning audit findings must be a bounded list')
    for finding in findings:
        if not isinstance(finding,dict) or finding.get('severity') not in {'LOW','INFO'}:
            raise ValueError('Blocking or malformed planning findings prevent dispatch')
        _text(finding.get('issue'),'planning finding',limit=4000)
    examined=review.get('artifact_hashes')
    if examined!=plan['artifact_hashes']:
        raise ValueError('Planning audit must attest every exact generated SRS/DAG/fracture artifact hash')
    return {'approved':True,'plan_sha256':plan['plan_sha256'],'review_sha256':digest(review),
            'planner':list(planner),'reviewer':list(reviewer),'requirements_checked':list(plan['requirements']),
            'artifact_hashes':deepcopy(plan['artifact_hashes'])}


def artifact_protocol(workspace,protected=()):
    listed='\n'.join('  - '+safe_path(path) for path in protected) or '  (none)'
    return ("OUTPUT PROTOCOL — the controller applies complete-file artifacts; do not write files directly.\n"
        "Emit each changed file exactly as:\n<<<FILE: relative/path/from/workspace.py>>>\n"
        "<entire file content>\n<<<END FILE>>>\n"
        f"Workspace: {workspace}\nOnly changed files may be emitted. No diffs, excerpts, duplicate or unclosed blocks.\n"
        "Complete-file transport does not authorize a whole-file rewrite; physical changed-line limits remain enforced.\n"
        "Read-only (controller rejects writes):\n"+listed)


_artifact_protocol=artifact_protocol  # Preserve the named legacy protocol entrypoint.


def apply_artifact_protocol(text,before,project,*,tests_only=False,protected=()):
    """Parse complete-file blocks and return a validated immutable file snapshot.

    No filesystem mutation occurs here. The controller must separately compare
    the actual workspace with ``before`` to reject out-of-band model writes,
    then materialize the returned files atomically under its authority.
    """
    _text(text,'artifact response',limit=16777216)
    if not isinstance(before,Mapping) or any(not isinstance(value,bytes) for value in before.values()):
        raise ValueError('Artifact baseline must contain physical file bytes')
    forbidden={safe_path(path) for path in protected}
    after=dict(before); emitted=set(); current=None; content=[]
    for line in text.splitlines(keepends=True):
        marker=line.rstrip('\r\n')
        if marker.startswith('<<<FILE:'):
            if current is not None:
                raise ValueError('Nested or unclosed artifact block')
            match=re.fullmatch(r'<<<FILE: (.+)>>>',marker)
            if not match:
                raise ValueError('Malformed complete-file artifact opening marker')
            current=safe_path(match.group(1)); content=[]
            if current in emitted or current in forbidden:
                raise ValueError('Duplicate or protected complete-file artifact')
        elif marker=='<<<END FILE>>>':
            if current is None:
                raise ValueError('Artifact close marker has no opening file')
            body=''.join(content)
            if not body or body.lstrip().startswith(('@@ ','diff --git ','--- a/')):
                raise ValueError('Artifacts require complete nonempty file contents, never a diff')
            encoded=body.encode('utf-8')
            if encoded==before.get(current):
                raise ValueError('An artifact may only identify a changed file')
            after[current]=encoded; emitted.add(current); current=None
        elif current is not None:
            content.append(line)
    if current is not None or not emitted:
        raise ValueError('Artifact response needs at least one fully closed changed-file block')
    observed_changes(before,after,before,project,tests_only=tests_only)
    return after


def initial_phase_ledger():
    return {'schema':'4.2.5-tdd-phases/1','order':[key for key,_ in TDD_PHASES],'records':[]}


def record_phase(ledger,phase,evidence,*,outcome='COMPLETED',iteration=0,reason=None):
    """Append a semantic phase record after the controller's physical gate.

    Evidence hashes are produced by the controller, not accepted from a model
    as execution proof. The Store must persist the returned ledger in the same
    transaction that accepts the corresponding fenced native/controller job.
    """
    if not isinstance(ledger,dict) or ledger.get('order')!=[key for key,_ in TDD_PHASES] or not isinstance(ledger.get('records'),list):
        raise ValueError('Invalid durable ten-phase ledger')
    if phase not in ledger['order'] or type(iteration) is not int or not 0<=iteration<=10 or not isinstance(evidence,dict):
        raise ValueError('Invalid phase, iteration or phase evidence')
    records=ledger['records']; previous=records[-1] if records else None
    if previous is None:
        expected='P1'; expected_iteration=0
    elif previous['phase']=='P10':
        expected='P7'; expected_iteration=previous['iteration']+1
    else:
        expected='P'+str(int(previous['phase'][1:])+1)
        expected_iteration=1 if expected=='P7' and previous['iteration']==0 else previous['iteration']
    if (phase,iteration)!=(expected,expected_iteration):
        raise ValueError('Ten-phase transitions must preserve FRAME, BUILD and CONVERGE order')
    required={'P1':['plan_sha256'],'P2':['dossier_sha256'],'P3':['test_receipt_sha256'],
              'P4':['artifact_sha256'],'P5':['test_receipt_sha256'],'P6':['review_sha256'],
              'P7':['artifact_sha256'],'P8':['next_work_sha256'],'P9':['artifact_sha256'],
              'P10':['test_receipt_sha256','review_sha256']}[phase]
    if outcome=='SKIPPED':
        allowed={'P7':'no_qualifying_minor_findings','P9':'all_tests_pass_no_open_findings'}
        if reason!=allowed.get(phase):
            raise ValueError('Only the legacy justified IMPROVE/REFINE skips are permitted')
    elif outcome!='COMPLETED' or any(not isinstance(evidence.get(key),str) or not _SHA.fullmatch(evidence[key]) for key in required):
        raise ValueError('Completed phases require hashes of their physical artifacts and receipts')
    if phase=='P3' and evidence.get('red_verified') is not True:
        raise ValueError('TEST-FIRST requires an independently verified red test gate')
    if phase=='P8' and evidence.get('decision') not in {'ACCEPT','CONTINUE','PIVOT','ESCALATE','QUARANTINE_CANDIDATE'}:
        raise ValueError('PLAN-NEXT requires an explicit deterministic progress decision')
    result=deepcopy(ledger)
    result['records'].append({'phase':phase,'name':dict(TDD_PHASES)[phase],'iteration':iteration,
                              'outcome':outcome,'reason':reason,'evidence':deepcopy(evidence)})
    return result
