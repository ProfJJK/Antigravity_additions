"""Protected outer assertions against an OS-isolated candidate subprocess.

Only standard library and frozen supervisor modules are imported here. The
candidate cannot write this verifier's private verdict. A child response is
never a test verdict: SQLite transitions, exact artifacts and ownership are
read independently after native process-tree cleanup. These finite behavior
checks do not attest all in-process pytest assertions, live models, or Git
publication; those remain separately scoped regression/live acceptance gates.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .io import write_json
from .releases import _plain_ancestors,_stat_plain


class BlackboxRejected(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _require(value,message):
    if not value:
        raise BlackboxRejected(message)


def _rows(database,query,parameters=()):
    _plain_ancestors(database)
    info=_stat_plain(database)
    _require(info.st_size<=16*1024*1024,'Candidate database exceeded the independent size bound')
    for suffix in ('-wal','-shm'):
        sidecar=database.with_name(database.name+suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _require(_stat_plain(sidecar).st_size<=16*1024*1024,'Candidate SQLite sidecar exceeded its bound')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=1)) as connection:
        # Candidate SQLite is an untrusted input to the protected process.
        # Progress callbacks cannot interrupt an individual huge allocation.
        for limit,maximum in ((sqlite3.SQLITE_LIMIT_LENGTH,262144),(sqlite3.SQLITE_LIMIT_SQL_LENGTH,8192),
                (sqlite3.SQLITE_LIMIT_COLUMN,128),(sqlite3.SQLITE_LIMIT_EXPR_DEPTH,32),
                (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT,16),(sqlite3.SQLITE_LIMIT_ATTACHED,0),
                (sqlite3.SQLITE_LIMIT_VDBE_OP,100000),(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH,8)):
            connection.setlimit(limit,maximum)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        deadline=time.monotonic()+1
        connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        required={'pipeline_jobs','pipeline_worker_ownership','pipeline_artifacts',
                  'pipeline_route_reservations','coding_workflows'}
        schema=connection.execute('SELECT name,type,sql,rootpage FROM sqlite_schema WHERE name IN ('+
            ','.join('?' for _ in required)+')',tuple(sorted(required))).fetchall()
        _require({row['name'] for row in schema}==required and all(row['type']=='table' and row['rootpage']>0
            and isinstance(row['sql'],str) and row['sql'].lstrip().upper().startswith('CREATE TABLE ')
            for row in schema),'Candidate evidence must use ordinary physical tables, never views or virtual tables')
        result=connection.execute(query,parameters).fetchmany(129)
        _require(len(result)<=128,'Candidate query exceeded its row bound')
        return [dict(row) for row in result]


def run_blackbox(candidate: Path, workspace: Path, private_report: Path, invoke, *, timeout_seconds=240):
    """Invoke a trusted process runner; it must close the complete child tree.

    invoke(argv, request, log_directory, timeout) returns its own process receipt,
    not a value supplied by the child. Windows production uses RepairRunner;
    portable tests use explicit real subprocess drivers and claim no OS ACLs.
    """
    candidate,workspace,private_report=map(lambda p:Path(p).absolute(),(candidate,workspace,private_report))
    _require(private_report!=workspace and workspace not in private_report.parents,'Outer verdict must be outside candidate-writeable workspace')
    _require(not workspace.exists() and not workspace.is_symlink(),'Outer contract workspace must be fresh')
    workspace.mkdir()
    database=workspace/'contract.db'
    nonce=uuid.uuid4().hex
    workflow='outer-'+nonce
    requirement='REQ-'+nonce[:12]
    chapter_ids=['ch-'+nonce[:8]+'-'+str(i) for i in range(2)]
    checks=[]; executions=[]; counter=0; deadline=time.monotonic()+timeout_seconds
    report={'schema':1,'passed':False,'scope':'external physical storage and coding-stage contracts; no live inference',
            'checks':checks,'executions':executions,'challenge':nonce,'started_at':time.time()}

    def call(operation,data=None):
        nonlocal counter
        remaining=deadline-time.monotonic()
        _require(remaining>0,'Outer verification deadline exceeded')
        counter+=1
        receipt=invoke(['--candidate',str(candidate),'--database',str(database)],
            {'operation':operation,'data':data or {}},private_report.parent/('outer-process-'+str(counter)),min(30,remaining))
        _require(receipt.get('exit_code')==0 and receipt.get('cleanup_verified') is True,
                 'Candidate adapter did not exit with independently verified cleanup')
        executions.append({key:receipt[key] for key in ('pid','exit_code','cleanup_verified')})
        path=Path(receipt['stdout_path'])
        _require(_stat_plain(path).st_size<=262144,'Candidate adapter response exceeded its bound')
        value=json.loads(path.read_text(encoding='utf-8'))
        _require(isinstance(value,dict) and set(value) in ({'result'},{'error_type'}),'Candidate returned no bounded adapter result')
        return value

    def jobs():
        return _rows(database,'SELECT * FROM pipeline_jobs ORDER BY job_id')

    def job(kind,*,status=None):
        selected=[row for row in jobs() if row['kind']==kind and (status is None or row['status']==status)]
        _require(len(selected)==1,'Expected one '+kind+' row in the physical database')
        return selected[0]

    def identity(row):
        return {key:row[key] for key in ('job_id','attempt_id','fencing_token')}

    def receipt(output,provider='codex',row=None):
        result={'provider':provider,'pid':executions[-1]['pid'],'exit_code':0,
                'session_id':'outer-storage-contract-'+nonce,'output_sha256':_digest(output),
                'execution_kind':'external-storage-contract-fixture'}
        if row is not None:
            route=_rows(database,'SELECT route_json FROM pipeline_route_reservations WHERE job_id=? AND attempt_id=? AND fencing_token=?',(row['job_id'],row['attempt_id'],row['fencing_token']))
            _require(len(route)==1,'Model stage has no physical route reservation')
            selected=json.loads(route[0]['route_json'])
            result.update(**identity(row),workflow_id=row['workflow_id'],worker_slot=selected['worker_slot'],
                provider=selected['provider'],requested_model=selected['model'],
                requested_effort=selected.get('reasoning_effort'),route_reservation_id=selected['reservation_id'],
                selected_route=selected,subscription_verified=True)
        return result

    def complete(row,output,provider='codex',**changes):
        data={**identity(row),'output':output,'receipt':receipt(output,provider,row=row)}
        data.update(changes)
        return call('complete',data)

    def unchanged(before,message):
        _require(jobs()==before,message)

    try:
        call('submit',{'objective':'Physical outer verification '+nonce,'requirements':[requirement],
                       'chapter_count':2,'workflow_id':workflow})
        _require(len(jobs())==3 and job('SYNTHESIS')['status']=='BLOCKED','Submission bypassed synthesis barrier')
        call('claim',{'owner':'outer-manifest-'+nonce})
        manifest_job=job('MANIFEST_GENERATOR',status='IN_PROGRESS')
        _require(manifest_job['attempt_id'] and manifest_job['fencing_token']==1,'Claim did not persist its fence')
        manifest={'chapters':[{'chapter_id':key,'title':'Chapter '+key,'requirements':[requirement],
            'wbs_tasks_defined':[{'id':key+'-task','description':'Verify '+nonce,'requirements':[requirement]}]}
            for key in chapter_ids]}
        before=jobs()
        rejected=complete(manifest_job,manifest,attempt_id=uuid.uuid4().hex)
        _require('error_type' in rejected,'Stale attempt was not rejected')
        unchanged(before,'Stale completion mutated physical workflow state')
        bad=receipt(manifest,row=manifest_job); bad['output_sha256']='0'*64
        _require('error_type' in complete(manifest_job,manifest,receipt=bad),'Forged output receipt was accepted')
        unchanged(before,'Forged receipt mutated workflow state')
        _require(call('heartbeat',{**identity(manifest_job),'fencing_token':0})=={'result':False},'Stale heartbeat was accepted')
        unchanged(before,'Stale heartbeat changed persisted lease state')
        checks.append('planning stale fence and forged receipt leave physical state unchanged')
        complete(manifest_job,manifest)
        chapters=[row for row in jobs() if row['kind']=='CHAPTER_DRAFT']
        _require({row['chapter_id'] for row in chapters}==set(chapter_ids) and job('SYNTHESIS')['status']=='BLOCKED',
                 'Manifest did not scatter exactly the requested chapter ownership')
        artifacts={}
        for index,key in enumerate(chapter_ids):
            call('claim',{'owner':'outer-owner-'+str(index),'worker_slot':'outer-slot-'+str(index)})
            active=[row for row in jobs() if row['kind']=='CHAPTER_DRAFT' and row['status']=='IN_PROGRESS']
            _require(len(active)==1,'Claim duplicated a chapter execution')
            current=active[0]; key=current['chapter_id']
            ownership=_rows(database,'SELECT * FROM pipeline_worker_ownership WHERE job_id=?',(current['job_id'],))
            _require(len(ownership)==1 and ownership[0]['slot']=='outer-slot-'+str(index),'Persistent chapter ownership is absent')
            text='Immutable chapter '+key+' '+nonce
            output={'chapter_id':key,'requirements_traced':[requirement],
                    'wbs_tasks_defined':[{'id':key+'-task','description':'Verify '+nonce,'requirements':[requirement]}],
                    'artifact_uri':f'db://{workflow}/{key}','artifact_text':text}
            before=jobs()
            wrong={**output,'chapter_id':'unowned-'+nonce}
            _require('error_type' in complete(current,wrong),'Cross-chapter publication was accepted')
            unchanged(before,'Cross-chapter output changed the database')
            complete(current,output)
            rows=_rows(database,'SELECT chapter_id,artifact_text,sha256 FROM pipeline_artifacts WHERE job_id=?',(current['job_id'],))
            expected=hashlib.sha256(text.encode()).hexdigest()
            _require(rows==[{'chapter_id':key,'artifact_text':text,'sha256':expected}],'Accepted chapter bytes/hash disagree')
            artifacts[key]=expected
            if index==0:
                _require(job('SYNTHESIS')['status']=='BLOCKED','Synthesis released before all chapters completed')
            before_artifacts=_rows(database,'SELECT * FROM pipeline_artifacts ORDER BY chapter_id')
            _require('error_type' in complete(current,{**output,'artifact_text':'tampered-'+nonce}),'Accepted output could be overwritten')
            _require(_rows(database,'SELECT * FROM pipeline_artifacts ORDER BY chapter_id')==before_artifacts,'Accepted artifact changed')
        _require(job('SYNTHESIS')['status']=='PENDING','Completed chapters failed to release synthesis')
        checks.append('chapter ownership, exact artifact hashes, immutability and single synthesis barrier')
        call('claim',{'owner':'outer-synthesis'})
        synthesis=job('SYNTHESIS',status='IN_PROGRESS')
        gathered=json.loads(synthesis['payload_json'])
        output={'artifact_text':'Synthesis '+nonce, **{key:gathered[key] for key in
            ('chapter_hashes','chapter_output_hashes','coverage_report_sha256','wbs_tasks_by_chapter')}}
        before=jobs()
        bad=receipt(output,row=synthesis); bad['requested_model']='wrong-'+nonce
        _require('error_type' in complete(synthesis,output,receipt=bad),'Synthesis with a forged reserved model was accepted')
        unchanged(before,'Rejected synthesis mutated root state')
        complete(synthesis,output)
        _require(job('MACRO_PLANNING_REQUEST')['status']=='COMPLETED','Synthesis did not complete the physical DAG')
        checks.append('exact synthesis tracing and provider contract gate DAG completion')

        coding_id='coding-'+nonce
        source=(''.join('# Existing context '+str(i)+'\n' for i in range(30))+'VALUE = 41\n').encode()
        test=b'def test_existing():\n    assert True\n'
        files={'src/value.py':source.hex(),'tests/test_existing.py':test.hex()}
        call('coding_init',{'objective':'Correct value '+nonce,'requirements':[requirement],
                            'workflow_id':coding_id,'files':files})
        state=json.loads(_rows(database,'SELECT state_json FROM coding_workflows WHERE workflow_id=?',(coding_id,))[0]['state_json'])
        _require(state['status']=='PLANNING' and state['sealed_tests_snapshot'] is None,'Coding skipped initial planning/RED barrier')
        expected_files={name:hashlib.sha256(bytes.fromhex(value)).hexdigest() for name,value in files.items()}
        _require(state['original_snapshot']==_digest(expected_files),'Coding baseline is detached from actual supplied bytes')
        for name,value in files.items():
            path=workspace/'project'/name
            _plain_ancestors(path); _stat_plain(path)
            _require(path.read_bytes()==bytes.fromhex(value),'Coding source file differs from baseline')
        call('claim',{'owner':'outer-coder','worker_slot':'outer-slot'})
        planner=job('CODE_PLAN',status='IN_PROGRESS')
        # A minimally real plan is generated by the trusted verifier. Its
        # normalized representation is candidate output, later checked in SQL.
        plan={'goal':'Correct '+nonce,'srs':{'skeleton':requirement+': value equals 42.',
            'chapters':[{'id':'ch01','title':'Value','text':'Value must equal 42.','requirement_ids':['R1']}]},
            'acceptance_criteria':[{'id':'AC1','statement':'Value equals 42.','requirement_ids':['R1'],'test_ids':['T1']}],
            'test_cases':[{'id':'T1','name':'test_value_'+nonce[:8],'asserts':'VALUE equals 42.','criteria_ids':['AC1']}],
            'leaves':[{'id':'L1','objective':'Correct value','file_targets':['src/value.py'],
                'estimated_changed_lines':20,'estimated_added_deleted_lines':2,'requirement_ids':['R1'],'criteria_ids':['AC1'],'dependencies':[]}]}
        good_receipt=receipt(plan,row=planner)
        before=jobs(); bad={**good_receipt,'requested_model':'wrong-'+nonce}
        _require('error_type' in call('coding_plan',{**identity(planner),'output':plan,'receipt':bad}),
                 'Coding accepted a different reserved model')
        unchanged(before,'Invalid coding receipt advanced physical stage')
        call('coding_plan',{**identity(planner),'output':plan,'receipt':good_receipt})
        _require(job('CODE_PLAN')['status']=='COMPLETED' and job('CODE_PLAN_REVIEW')['status']=='PENDING',
                 'Valid coding plan did not require independent audit')
        state=json.loads(_rows(database,'SELECT state_json FROM coding_workflows WHERE workflow_id=?',(coding_id,))[0]['state_json'])
        _require(state['status']=='REVIEWING_PLAN' and state['current_snapshot']==state['original_snapshot'] and
                 not any(row['kind'] in {'CODE_EDIT','CODE_INTEGRATE','CODE_TEST'} for row in jobs()),
                 'Coding edit/integration escaped mandatory audit and RED stages')
        call('claim',{'owner':'outer-auditor','worker_slot':'outer-slot'})
        audit=job('CODE_PLAN_REVIEW',status='IN_PROGRESS')
        review={'verdict':'PASS','plan_sha256':state['plan']['plan_sha256'],'requirements_checked':['R1'],
                'artifact_hashes':state['plan']['artifact_hashes'],'findings':[]}
        audit_receipt=receipt(review,row=audit)
        before=jobs()
        forged={**audit_receipt,'provider':good_receipt['provider'],'requested_model':good_receipt['requested_model']}
        _require('error_type' in call('complete_coding',{**identity(audit),'output':review,'receipt':forged,
                    'evidence':{'source_snapshot_sha256':state['current_snapshot']}}),'Self-review coding receipt was accepted')
        unchanged(before,'Self-review advanced coding state')
        call('complete_coding',{**identity(audit),'output':review,'receipt':audit_receipt,
                                'evidence':{'source_snapshot_sha256':state['current_snapshot']}})
        _require(job('CODE_RESEARCH')['status']=='PENDING' and not any(row['kind']=='CODE_INTEGRATE' for row in jobs()),
                 'Coding accepted integration before research/tests')
        checks.append('physical coding baseline, reserved model receipt, asymmetric plan review and ordered stages')
        report['passed']=True
    except Exception as exc:
        report.update(error_type=type(exc).__name__,error=str(exc)[:512])
        raise BlackboxRejected('Independent outer acceptance failed: '+str(exc)[:256]) from exc
    finally:
        report['finished_at']=time.time()
        write_json(private_report,report)
    return report
