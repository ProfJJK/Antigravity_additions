"""Durable coding state machine sharing the Warden's leases and admission table."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager

from .coding import CodingProject, digest, manifest, bind_test_identities, within
from . import routing_store as routes

ROOT_KINDS = ('MACRO_PLANNING_REQUEST', 'CODE_REQUEST')
CONTROLLER_KINDS = ('CODE_TEST', 'CODE_INTEGRATE')
NATIVE_KINDS = ('CODE_PLAN', 'CODE_PLAN_REVIEW', 'CODE_TEST_AUTHOR', 'CODE_EDIT', 'CODE_REVIEW', 'CODE_RESEARCH')
CODING_KINDS = ('CODE_REQUEST', *CONTROLLER_KINDS, *NATIVE_KINDS)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS coding_workflows (
 workflow_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id),
 state_json TEXT NOT NULL CHECK(json_valid(state_json))
);
CREATE TABLE IF NOT EXISTS coding_blobs (
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS coding_snapshots (
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json))
);
CREATE TABLE IF NOT EXISTS coding_evidence (
 job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id), evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64)
);
CREATE TABLE IF NOT EXISTS coding_controller_retries (
 job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id), failures INTEGER NOT NULL DEFAULT 0,
 next_eligible_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coding_integration_intents (
 job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id), attempt_id TEXT NOT NULL,
 fencing_token INTEGER NOT NULL, source_sha256 TEXT NOT NULL, test_sha256 TEXT NOT NULL,
 reviews_sha256 TEXT NOT NULL, baseline_commit TEXT NOT NULL, result_commit TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PREPARED','APPLIED')), created_at REAL NOT NULL,
 applied_at REAL, PRIMARY KEY(job_id,attempt_id,fencing_token)
);
CREATE TABLE IF NOT EXISTS coding_attempt_evidence (
 job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),attempt_id TEXT NOT NULL,fencing_token INTEGER NOT NULL,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),sha256 TEXT NOT NULL,
 PRIMARY KEY(job_id,attempt_id,fencing_token)
);
CREATE TRIGGER IF NOT EXISTS coding_attempt_evidence_no_update BEFORE UPDATE ON coding_attempt_evidence
BEGIN SELECT RAISE(ABORT,'attempt evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_attempt_evidence_no_delete BEFORE DELETE ON coding_attempt_evidence
BEGIN SELECT RAISE(ABORT,'attempt evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_blobs_no_update BEFORE UPDATE ON coding_blobs
BEGIN SELECT RAISE(ABORT,'coding content is immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_blobs_no_delete BEFORE DELETE ON coding_blobs
BEGIN SELECT RAISE(ABORT,'coding content is immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_snapshots_no_update BEFORE UPDATE ON coding_snapshots
BEGIN SELECT RAISE(ABORT,'coding snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_snapshots_no_delete BEFORE DELETE ON coding_snapshots
BEGIN SELECT RAISE(ABORT,'coding snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_evidence_no_update BEFORE UPDATE ON coding_evidence
BEGIN SELECT RAISE(ABORT,'coding evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS coding_evidence_no_delete BEFORE DELETE ON coding_evidence
BEGIN SELECT RAISE(ABORT,'coding evidence is immutable'); END;
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class CodingStoreMixin:
    @staticmethod
    def _coding_state(conn, workflow_id):
        row = conn.execute('SELECT state_json FROM coding_workflows WHERE workflow_id=?', (workflow_id,)).fetchone()
        if row is None:
            raise ValueError('ID does not identify a coding workflow')
        return json.loads(row[0])

    @staticmethod
    def _save_coding(conn, workflow_id, state):
        conn.execute('UPDATE coding_workflows SET state_json=? WHERE workflow_id=?', (encoded(state), workflow_id))

    @staticmethod
    def _snapshot(conn, files):
        index = manifest(files)
        for name, sha in index.items():
            conn.execute('INSERT OR IGNORE INTO coding_blobs VALUES(?,?)', (sha, files[name]))
        sha = digest(index)
        conn.execute('INSERT OR IGNORE INTO coding_snapshots VALUES(?,?)', (sha, encoded(index)))
        return sha

    def coding_files(self, snapshot_sha):
        with self._connection() as conn:
            row = conn.execute('SELECT manifest_json FROM coding_snapshots WHERE sha256=?', (snapshot_sha,)).fetchone()
            if row is None:
                raise ValueError('Unknown immutable coding snapshot')
            index = json.loads(row[0])
            files = {name: bytes(conn.execute('SELECT content FROM coding_blobs WHERE sha256=?', (sha,)).fetchone()[0])
                     for name, sha in index.items()}
            if manifest(files) != index or digest(index) != snapshot_sha:
                raise ValueError('Immutable coding snapshot failed its content hashes')
            return files

    def coding_state(self, workflow_id):
        with self._connection() as conn:
            return self._coding_state(conn, workflow_id)

    def record_coding_attempt_evidence(self, node, evidence):
        with self._write() as conn:
            current = self._get(conn,node['job_id'])
            # Cancellation may have just fenced this physical result. Evidence
            # can still be recorded against its historical, guarded attempt.
            if not self._owned(current,node['attempt_id'],node['fencing_token']):
                if not conn.execute('SELECT 1 FROM pipeline_execution_cleanup WHERE job_id=? AND attempt_id=? AND fencing_token=?',
                                    (node['job_id'],node['attempt_id'],node['fencing_token'])).fetchone():
                    raise ValueError('Attempt evidence has no controller-owned execution identity')
            conn.execute('INSERT INTO coding_attempt_evidence VALUES(?,?,?,?,?)',
                (node['job_id'],node['attempt_id'],node['fencing_token'],encoded(evidence),digest(evidence)))

    def prepare_coding_integration(self, node, staged):
        with self._write() as conn:
            current = self._get(conn,node['job_id'])
            if not self._owned(current,node['attempt_id'],node['fencing_token']):
                raise ValueError('Revoked coding attempt cannot prepare Git integration')
            state = self._coding_state(conn,node['workflow_id'])
            from .coding_git import snapshot_digest
            index = json.loads(conn.execute('SELECT manifest_json FROM coding_snapshots WHERE sha256=?',(state['current_snapshot'],)).fetchone()[0])
            files = {name:bytes(conn.execute('SELECT content FROM coding_blobs WHERE sha256=?',(sha,)).fetchone()[0])
                     for name,sha in index.items()}
            expected_tree = snapshot_digest(files,{name:state['modes'].get(name,'100644') for name in files})
            expected_parent = state['last_commit'] or state['baseline_commit']
            if node['payload'].get('approved_integration'):
                expected_parent = state['chunks'][-1]['staged']['parent_commit']
            if (node['kind']!='CODE_INTEGRATE' or not state['reviews'] or
                    not all(item['approved'] for item in state['reviews']) or
                    state['last_test'].get('passed') is not True or
                    node['payload']['reviews_sha256'] != digest(state['reviews']) or
                    node['payload']['test_receipt_sha256'] != digest(state['last_test']) or
                    staged.get('baseline_commit') != state['baseline_commit'] or
                    staged.get('snapshot_sha256') != expected_tree or staged.get('parent_commit') != expected_parent):
                raise ValueError('Git integration requires the exact accepted reviews, test evidence, and baseline')
            values = (node['job_id'],node['attempt_id'],node['fencing_token'],state['current_snapshot'],
                      digest(state['last_test']),digest(state['reviews']),state['baseline_commit'],staged['result_commit'])
            previous = conn.execute('SELECT * FROM coding_integration_intents WHERE job_id=? AND attempt_id=? AND fencing_token=?', values[:3]).fetchone()
            if previous is not None:
                if tuple(previous[key] for key in ('job_id','attempt_id','fencing_token','source_sha256','test_sha256',
                        'reviews_sha256','baseline_commit','result_commit')) != values:
                    raise ValueError('An integration intent cannot change its sealed result')
            else:
                conn.execute("INSERT INTO coding_integration_intents VALUES(?,?,?,?,?,?,?,?,'PREPARED',?,NULL)", (*values,time.time()))
            self._event(conn,current,'GIT_INTEGRATION_PREPARED',result_commit=staged['result_commit'],source_sha256=state['current_snapshot'])

    @contextmanager
    def coding_cas_guard(self, node):
        """Serialize only the final update-ref with lease cancellation/fencing.

        Git copies and hashes all objects before entering. A crash after the Git
        CAS and before SQLite commit leaves PREPARED; deterministic replay checks
        the already-integrated ref under a new valid lease and records APPLIED.
        """
        with self._write() as conn:
            current = self._get(conn,node['job_id'])
            if not self._owned(current,node['attempt_id'],node['fencing_token']):
                raise ValueError('Cancellation or lease loss revoked Git integration')
            intent = conn.execute('SELECT * FROM coding_integration_intents WHERE job_id=? AND attempt_id=? AND fencing_token=?',
                                  (node['job_id'],node['attempt_id'],node['fencing_token'])).fetchone()
            state = self._coding_state(conn,node['workflow_id'])
            if intent is None or intent['source_sha256']!=state['current_snapshot'] or intent['reviews_sha256']!=digest(state['reviews']):
                raise ValueError('Git CAS has no matching durable sealed integration intent')
            yield
            conn.execute("UPDATE coding_integration_intents SET state='APPLIED',applied_at=? WHERE job_id=? AND attempt_id=? AND fencing_token=?",
                         (time.time(),node['job_id'],node['attempt_id'],node['fencing_token']))
            self._event(conn,current,'GIT_CAS_APPLIED',result_commit=intent['result_commit'])

    def _coding_job(self, conn, root, state, kind, **payload):
        identifier = uuid.uuid4().hex
        task = {'objective': root['payload']['objective'], 'requirements': root['payload']['requirements'],
                'project_id': root['payload']['project_id'], 'cycle': state['cycle'],
                'snapshot_sha256': state['current_snapshot'], 'strategy': state.get('strategy', ''), **payload}
        if state.get('plan') and kind not in ('CODE_PLAN','CODE_PLAN_REVIEW'):
            plan = state['plan']
            leaf_id = plan['fracture_manifest']['topological_order'][state['leaf_index']]
            leaf = next(item for item in plan['fracture_manifest']['leaves'] if item['id']==leaf_id)
            task.update(active_leaf=leaf,plan_sha256=plan['plan_sha256'],
                        requirements=[plan['requirements'][key] for key in leaf['requirement_ids']],
                        acceptance_criteria=[item for item in plan['acceptance_criteria'] if item['id'] in leaf['criteria_ids']],
                        test_cases=[item for item in plan['test_cases'] if set(item['criteria_ids']) & set(leaf['criteria_ids'])])
        if kind=='CODE_TEST':
            # Capture this leaf's sealed definitions before later leaves replace
            # the current state. Public acceptance can then bind exact identities.
            task['test_identities']=json.loads(json.dumps(state.get('test_identities',{})))
        self._insert(conn, identifier, root['workflow_id'], root['workflow_id'], kind, 'PENDING', task,
                     max_attempts=self.max_attempts)
        self._event(conn, root, 'CODING_STAGE_CREATED', kind=kind, stage_job_id=identifier, cycle=state['cycle'])
        return identifier

    def _coding_phase(self, conn, root, state, phase, evidence, *, outcome='COMPLETED',reason=None):
        record = {'leaf_index':state['leaf_index'],'cycle':state['cycle'],'phase':phase,'outcome':outcome,
                  'reason':reason,'evidence_sha256':digest(evidence),'evidence':evidence,'recorded_at':time.time()}
        state['phase_ledger'].append(record)
        self._event(conn,root,'CODING_PHASE_RECORDED',**record)

    def _coding_converge(self, conn, root, state):
        minor = [finding for review in state['reviews'] for finding in review.get('minor_findings',[])]
        if minor:
            state['status'] = 'IMPROVING'
            self._coding_job(conn,root,state,'CODE_EDIT',phase='P7',minor_findings=minor,
                             allowed_paths=state['project']['allowed_paths'],test_receipt=state['last_test'])
            return
        self._coding_phase(conn,root,state,'P7',{'reviews_sha256':digest(state['reviews'])},
                           outcome='SKIPPED',reason='no_qualifying_minor_findings')
        self._coding_phase(conn,root,state,'P8',{'decision':'ACCEPT','next_work_sha256':digest(state['reviews'])})
        self._coding_phase(conn,root,state,'P9',{'source_sha256':state['current_snapshot']},
                           outcome='SKIPPED',reason='all_tests_pass_no_open_findings')
        state['status']='FINAL_TESTING'
        self._coding_job(conn,root,state,'CODE_TEST',phase='final')

    def submit_coding_snapshot(self, project: CodingProject, objective, requirements, snapshot, docker_policy,
                               workflow_id=None):
        from .store import _identifier, _strings
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError('objective must be nonempty')
        _strings(requirements, 'requirements')
        if sum(len(text.splitlines()) for text in [objective, *requirements]) > 400:
            raise ValueError('A coding request SRS may contain at most 400 lines; split larger requests')
        identifier = _identifier(workflow_id or uuid.uuid4().hex, 'workflow_id')
        payload = {'project_id': project.project_id, 'objective': objective, 'requirements': requirements}
        with self._write() as conn:
            if conn.execute('SELECT 1 FROM pipeline_jobs WHERE job_id=?', (identifier,)).fetchone():
                root = self._get(conn, identifier)
                if root['kind'] != 'CODE_REQUEST' or root['payload'] != payload:
                    raise ValueError('workflow_id belongs to a different request')
            else:
                self._insert(conn, identifier, identifier, None, 'CODE_REQUEST', 'IN_PROGRESS', payload)
                routes.capture_workflow(conn, identifier, self.routing_policy or {})
                sha = self._snapshot(conn, snapshot.files)
                state = {'project': project.as_dict(), 'docker': docker_policy.as_dict(),
                         'baseline_commit': snapshot.commit, 'baseline_ref': snapshot.ref, 'repository_snapshot': snapshot.as_dict(),
                         'modes': snapshot.modes, 'original_snapshot': sha, 'current_snapshot': sha,
                         'sealed_tests_snapshot': None, 'cycle': 1, 'max_cycles': 10, 'leaf_index':0,
                         'leaf_baseline_snapshot':sha,'phase_ledger':[], 'completed_leaves':[],
                         'consecutive_failures': 0, 'pivots': 0, 'status': 'AUTHORING_TESTS',
                         'chunks': [], 'last_commit': None, 'pending_reviews': [], 'reviews': [],
                         'last_test': None, 'strategy': '', 'failures': [],
                         'fracture_manifest': {'schema': 1, 'N': 1, 'max_tasks_per_batch': 20,
                             'max_chunk_changed_lines': 100, 'preferred_context_lines': [20, 100],
                             'leaves': [{'id': 'chunk', 'requirements': requirements, 'source_paths': list(project.allowed_paths)}],
                             'nodes': ['tests_first','baseline_test','bounded_edit','independent_test','file_reviews','staged_git'],
                             'edges': [['tests_first','baseline_test'],['baseline_test','bounded_edit'],
                                       ['bounded_edit','independent_test'],['independent_test','file_reviews'],['file_reviews','staged_git']],
                             'mermaid': 'flowchart LR\n tests_first-->baseline_test-->bounded_edit-->independent_test-->file_reviews-->staged_git'}}
                conn.execute('INSERT INTO coding_workflows VALUES(?,?)', (identifier, encoded(state)))
                root = self._get(conn, identifier)
                state['status']='PLANNING'
                self._save_coding(conn,identifier,state)
                self._coding_job(conn, root, state, 'CODE_PLAN', allowed_paths=list(project.allowed_paths))
                self._event(conn, root, 'CODING_SUBMITTED', baseline_commit=snapshot.commit,
                            source_sha256=sha, project_policy_sha256=digest(state['project']))
        return self.coding_workflow(identifier)

    def coding_workflow(self, workflow_id):
        result = self.workflow(workflow_id)
        with self._connection() as conn:
            result['coding'] = self._coding_state(conn, workflow_id)
            result['evidence'] = [{'job_id': row['job_id'], 'sha256': row['sha256'],
                                   'evidence': json.loads(row['evidence_json'])}
                                  for row in conn.execute('SELECT e.* FROM coding_evidence e JOIN pipeline_jobs j USING(job_id) '
                                                          'WHERE j.workflow_id=? ORDER BY j.created_at,j.job_id', (workflow_id,))]
            result['attempt_evidence'] = [dict(row) for row in conn.execute('SELECT a.job_id,a.attempt_id,a.fencing_token,a.sha256 '
                'FROM coding_attempt_evidence a JOIN pipeline_jobs j USING(job_id) WHERE j.workflow_id=?',(workflow_id,))]
            result['integration_intents'] = [dict(row) for row in conn.execute('SELECT i.* FROM coding_integration_intents i '
                'JOIN pipeline_jobs j USING(job_id) WHERE j.workflow_id=? ORDER BY i.created_at',(workflow_id,))]
            signature=lambda item:tuple(item[key] for key in ('baseline_commit','result_commit','source_sha256','test_sha256','reviews_sha256'))
            applied={signature(item) for item in result['integration_intents'] if item['state']=='APPLIED'}
            result['integration_reconciliation_required'] = any(item['state']=='PREPARED' and signature(item) not in applied
                                                               for item in result['integration_intents'])
        return result

    def _coding_retry(self, conn, root, state, reason):
        state.pop('pending_refine_skip',None)
        state['retry_phase']='P7' if state.get('changes') and state.get('last_test') else 'P4'
        state['repair_findings']=[str(item) for review in state.get('reviews',[]) for item in review.get('findings',[])] or [reason]
        state['consecutive_failures'] += 1
        state['failures'].append({'cycle': state['cycle'], 'reason': reason,
                                  'test_sha256': digest(state['last_test']) if state['last_test'] else None,
                                  'snapshot_sha256': state['current_snapshot']})
        if state['cycle'] >= 10:
            state['status'] = 'EXHAUSTED'
            self._fail_workflow(conn, root['workflow_id'], 'Coding exhausted its immutable ten-cycle budget')
            return
        if state['consecutive_failures'] >= 3:
            if state['pivots'] >= 3:
                state['status'] = 'RESEARCH_EXHAUSTED'
                self._fail_workflow(conn, root['workflow_id'], 'Coding exhausted its three research pivots')
                return
            state['status'] = 'RESEARCH_REQUIRED'
            self._coding_job(conn, root, state, 'CODE_RESEARCH', failures=state['failures'][-3:],
                             test_receipt=state['last_test'], failure_evidence_sha256=digest(state['failures'][-3:]))
        else:
            state['cycle'] += 1
            state['status'] = 'EDITING'
            self._coding_job(conn, root, state, state.get('retry_stage','CODE_EDIT'), allowed_paths=state['project']['allowed_paths'],
                             test_paths=state['project']['test_paths'],
                             phase=state['retry_phase'],minor_findings=state['repair_findings'],
                             previous_failure=reason, test_receipt=state['last_test'])

    @staticmethod
    def _native_coding_receipt(job, output, receipt):
        from .store import JobStore
        JobStore._receipt(output, receipt)
        route = job['route']
        if not route:
            raise ValueError('Coding inference requires a controller route')
        expected = {'job_id': job['job_id'], 'workflow_id': job['workflow_id'], 'attempt_id': job['attempt_id'],
                    'fencing_token': job['fencing_token'], 'worker_slot': job['worker_slot'],
                    'provider': route['provider'], 'requested_model': route['model'],
                    'requested_effort': route.get('reasoning_effort'), 'route_reservation_id': route['reservation_id']}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError('Coding receipt does not match the reserved native attempt')
        if receipt.get('reported_model') not in (None, route['model']):
            raise ValueError('Coding receipt reports a different model')
        if receipt.get('reported_effort') not in (None, route.get('reasoning_effort')):
            raise ValueError('Coding receipt reports a different reasoning effort')
        required = job['payload']['requirements']
        if job['kind'] in ('CODE_EDIT','CODE_TEST_AUTHOR','CODE_REVIEW'):
            traced = output.get('requirements_traced')
            if not isinstance(traced,list) or set(traced) != set(required):
                raise ValueError('Coding stage must trace exactly the captured requirements')

    def complete_coding(self, job_id, attempt_id, fencing_token, output, receipt, *, evidence, files=None):
        """Controller-only transition; native output and physical evidence remain separate."""
        with self._write() as conn:
            job = self._get(conn, job_id)
            if not self._owned(job, attempt_id, fencing_token):
                raise ValueError('Stale coding attempt cannot publish evidence')
            if job['kind'] not in (*CONTROLLER_KINDS, *NATIVE_KINDS):
                raise ValueError('Not a coding execution node')
            if job['kind'] in NATIVE_KINDS:
                self._native_coding_receipt(job, output, receipt)
            elif (receipt.get('executor') != ('docker' if job['kind']=='CODE_TEST' else 'git')
                  or any(receipt.get(key) != job[key] for key in ('job_id','attempt_id','fencing_token'))
                  or receipt.get('output_sha256') != digest(output)):
                raise ValueError('Controller receipt must bind the physical executor and exact attempt')
            root = self._get(conn, job['workflow_id'])
            state = self._coding_state(conn, job['workflow_id'])
            kind = job['kind']
            if job['payload']['snapshot_sha256'] != state['current_snapshot']:
                raise ValueError('Coding source changed after this stage was queued')
            if files is not None:
                if kind not in ('CODE_EDIT', 'CODE_TEST_AUTHOR'):
                    raise ValueError('Read-only stages cannot publish modified files')
                state['current_snapshot'] = self._snapshot(conn, files)
            conn.execute('INSERT INTO coding_evidence VALUES(?,?,?)', (job_id, encoded(evidence), digest(evidence)))
            conn.execute('INSERT INTO pipeline_outputs VALUES(?,?,?,?,?,?,?,?)',
                         (job_id, attempt_id, fencing_token, None, encoded(output), encoded(receipt), digest(output), time.time()))
            conn.execute("UPDATE pipeline_jobs SET status='COMPLETED',lease_expires_at=NULL,updated_at=? WHERE job_id=?", (time.time(), job_id))
            routes.release(conn, job_id, 'completed', 'COMPLETED')
            self._event(conn, job, 'COMPLETED', evidence_sha256=digest(evidence), output_sha256=digest(output))
            if kind == 'CODE_PLAN':
                plan = evidence['plan']
                if plan.get('plan_sha256')!=digest({key:value for key,value in plan.items() if key!='plan_sha256'}):
                    raise ValueError('Controller planning artifacts failed their immutable digest')
                state['plan'],state['planner_receipt']=plan,receipt
                state['fracture_manifest']=plan['fracture_manifest']
                state['status']='REVIEWING_PLAN'
                self._coding_job(conn,root,state,'CODE_PLAN_REVIEW',plan=plan,plan_sha256=plan['plan_sha256'],
                                 producer={'provider':receipt['provider'],'model':receipt['requested_model']})
            elif kind == 'CODE_PLAN_REVIEW':
                from .coding_plan import validate_plan_review
                approved = validate_plan_review(output,state['plan'],planner_receipt=state['planner_receipt'],reviewer_receipt=receipt)
                state['plan_review']=approved
                self._coding_phase(conn,root,state,'P1',{'plan_sha256':state['plan']['plan_sha256'],'review_sha256':digest(approved)})
                state['status']='RESEARCHING'
                self._coding_job(conn,root,state,'CODE_RESEARCH',research_phase='initial')
            elif kind == 'CODE_TEST_AUTHOR':
                selected_paths=[change['path'] for change in evidence.get('changes',[])]
                if not selected_paths:
                    if state['project'].get('test_strategy','red_green')!='preserve_behavior':
                        raise ValueError('Red-green test sealing requires newly authored regression files')
                    selected_paths=output.get('reuse_tests')
                if (not isinstance(selected_paths,list) or any(not isinstance(path,str) or
                        not within(path,state['project']['test_paths']) for path in selected_paths)):
                    raise ValueError('Selected tests must stay within the captured protected test paths')
                index=json.loads(conn.execute('SELECT manifest_json FROM coding_snapshots WHERE sha256=?',
                                              (state['current_snapshot'],)).fetchone()[0])
                sealed_files={path:bytes(conn.execute('SELECT content FROM coding_blobs WHERE sha256=?',
                    (index[path],)).fetchone()[0]) for path in selected_paths if path in index}
                state['test_identities']=bind_test_identities(sealed_files,selected_paths,job['payload']['test_cases'])
                state['sealed_tests_snapshot'] = state['current_snapshot']
                state['review_base_snapshot'] = state['current_snapshot']
                state['test_changes'] = evidence.get('changes', [])
                state['test_producer'] = {'provider': receipt['provider'], 'model': receipt['requested_model']}
                state['status'] = 'TESTING_PRECODE'
                self._coding_job(conn, root, state, 'CODE_TEST', phase='precode')
            elif kind == 'CODE_EDIT':
                state['changes'] = evidence['changes']
                if not evidence.get('no_source_change'):
                    state['producer'] = {'provider': receipt['provider'], 'model': receipt['requested_model']}
                state['producer_done'] = output.get('done') is True
                phase = job['payload'].get('phase','P4')
                if phase=='P9' and evidence.get('no_source_change'):
                    state['pending_refine_skip']={'artifact_sha256':state['current_snapshot'],'receipt_sha256':digest(receipt)}
                else:
                    self._coding_phase(conn,root,state,phase,{'artifact_sha256':state['current_snapshot'],'receipt_sha256':digest(receipt)})
                if phase=='P7':
                    self._coding_phase(conn,root,state,'P8',{'decision':'CONTINUE','next_work_sha256':digest(output)})
                    state['status']='REFINING'
                    self._coding_job(conn,root,state,'CODE_EDIT',phase='P9',minor_findings=job['payload']['minor_findings'],
                                     allowed_paths=state['project']['allowed_paths'],test_receipt=state['last_test'])
                else:
                    state['status'] = 'TESTING'
                    self._coding_job(conn, root, state, 'CODE_TEST', phase='final' if phase=='P9' else 'postedit')
            elif kind == 'CODE_TEST':
                candidate_failure = evidence.get('failure_category') in ('oom','timeout','output_limit')
                source_proven = evidence.get('source_verified') is True or (candidate_failure and evidence.get('input_source_verified') is True and evidence.get('passed') is False)
                if (evidence.get('cleanup_verified') is not True or not source_proven
                        or type(evidence.get('passed')) is not bool):
                    raise ValueError('Docker test transition requires verified physical cleanup and result')
                index_row = conn.execute('SELECT manifest_json FROM coding_snapshots WHERE sha256=?', (state['current_snapshot'],)).fetchone()
                expected_index = json.loads(index_row[0])
                actual_index = evidence.get('source',{}).get('files',{})
                from .container_policy import DockerPolicy
                captured_policy=DockerPolicy.from_dict(state['docker'])
                if (evidence.get('job_id')!=job_id or evidence.get('attempt_id')!=attempt_id
                        or evidence.get('image_id')!=captured_policy.image or evidence.get('policy_sha256')!=captured_policy.digest):
                    raise ValueError('Docker evidence must identify the exact captured image, policy, and attempt')
                if (set(actual_index) != set(expected_index)
                        or any(not isinstance(actual_index[name],dict) or actual_index[name].get('sha256') != sha
                               or actual_index[name].get('size') != conn.execute('SELECT length(content) FROM coding_blobs WHERE sha256=?',(sha,)).fetchone()[0]
                               or actual_index[name].get('executable') is not (state['modes'].get(name)=='100755')
                               for name,sha in expected_index.items())
                        or evidence.get('source',{}).get('sha256') != digest(actual_index)):
                    raise ValueError('Docker source evidence does not match the queued immutable snapshot')
                state['last_test'] = evidence
                planned={item['name'] for item in job['payload'].get('test_cases',[])}
                identities=state.get('test_identities',{})
                if set(identities)!=planned or any(expected_index.get(value['path'])!=value['file_sha256'] for value in identities.values()):
                    raise ValueError('The planned regression definitions are not bound to the sealed test snapshot')
                cases=[case for command in evidence.get('commands',[]) for case in command.get('junit_cases',[])
                       if isinstance(case,dict) and isinstance(case.get('name'),str)]
                matched={name:[case for case in cases if case['name'].split('[',1)[0]==name
                              and case.get('class_name')==identities[name]['class_name']] for name in planned}
                if evidence['passed'] and (not planned or any(not values or any(case.get('status')!='passed' for case in values)
                                                              for values in matched.values())):
                    raise ValueError('A green gate requires every planned leaf test to actually execute and pass')
                if job['payload']['phase'] == 'precode':
                    state['precode_test'] = evidence
                    red = evidence.get('failure_category')=='tests_failed' and any(command.get('exit_code') == 1 and isinstance(command.get('junit'),dict)
                              and command['junit'].get('failures',0)>0 and command['junit'].get('errors',0)==0
                              and command['junit'].get('tests',0)>0 for command in evidence.get('commands',[]))
                    red = bool(red and planned and all(matched.values())
                               and any(case.get('status')=='failed' for values in matched.values() for case in values)
                               and all(case.get('status') in ('passed','failed') for values in matched.values() for case in values)
                               and all(command.get('junit',{}).get('errors',0)==0 for command in evidence.get('commands',[])))
                    acceptable = (red if state['project'].get('test_strategy','red_green')=='red_green'
                                  else evidence['passed'] is True)
                    if not acceptable:
                        state['current_snapshot'] = state['leaf_baseline_snapshot']
                        state['sealed_tests_snapshot'] = None
                        state['retry_stage'] = 'CODE_TEST_AUTHOR'
                        self._coding_retry(conn, root, state, 'Pre-code tests did not establish the configured red-green or preservation baseline')
                    else:
                        self._coding_phase(conn,root,state,'P3',{'test_receipt_sha256':digest(evidence),
                            'red_verified':red,'strategy':state['project'].get('test_strategy','red_green')})
                        state['retry_stage'] = 'CODE_EDIT'
                        state['status'] = 'EDITING'
                        self._coding_job(conn, root, state, 'CODE_EDIT', allowed_paths=state['project']['allowed_paths'],
                                         test_receipt=evidence)
                else:
                    if job['payload']['phase']=='postedit':
                        self._coding_phase(conn,root,state,'P5',{'test_receipt_sha256':digest(evidence)},
                                           outcome='COMPLETED' if evidence['passed'] else 'FAILED')
                    state['status'], state['reviews'], state['pending_reviews'] = 'REVIEWING', [], []
                    state['review_phase'] = 'P10' if job['payload']['phase']=='final' else 'P6'
                    changes = list(state['changes']) + state['test_changes']
                    for change in changes:
                        producer = state['test_producer'] if change in state.get('test_changes', []) else state['producer']
                        review = self._coding_job(conn, root, state, 'CODE_REVIEW', file=change,
                                                  test_receipt_sha256=digest(evidence), producer=producer,phase=state['review_phase'])
                        state['pending_reviews'].append(review)
            elif kind == 'CODE_REVIEW':
                expected = {'path': job['payload']['file']['path'], 'file_sha256': job['payload']['file']['after_sha256'],
                            'diff_sha256': job['payload']['file']['diff_sha256'],
                            'test_receipt_sha256': job['payload']['test_receipt_sha256']}
                if any(output.get(key) != value for key, value in expected.items()) or type(output.get('approved')) is not bool:
                    raise ValueError('Review must bind actual file, diff, and independent test receipt hashes')
                if type(output.get('objective_satisfied')) is not bool or not isinstance(output.get('findings'),list) or not output['findings']:
                    raise ValueError('Independent review requires objective coverage and concrete findings')
                minor = output.get('minor_findings',[])
                if not isinstance(minor,list) or len(minor)>20 or any(not isinstance(item,str) or not item.strip() for item in minor):
                    raise ValueError('Review minor findings must be a bounded concrete list')
                producer = job['payload']['producer']
                if (receipt['provider'], receipt['requested_model']) == (producer['provider'], producer['model']):
                    raise ValueError('A producing model cannot approve its own file')
                state['reviews'].append({'job_id': job_id, 'approved': output['approved'],
                                        'objective_satisfied': output['objective_satisfied'], 'minor_findings':minor,
                                        'findings':output['findings'], 'receipt_sha256': digest(receipt), **expected})
                state['pending_reviews'].remove(job_id)
                if not state['pending_reviews']:
                    if not state['last_test']['passed'] or not all(review['approved'] and review['objective_satisfied'] for review in state['reviews']):
                        self._coding_phase(conn,root,state,state['review_phase'],{'review_sha256':digest(state['reviews']),
                            'test_receipt_sha256':digest(state['last_test'])},outcome='FAILED')
                        self._coding_retry(conn, root, state, 'Independent tests or diagnostic file review rejected the chunk')
                    else:
                        phase = state['review_phase']
                        if phase=='P10' and state.get('pending_refine_skip') and not any(review['minor_findings'] for review in state['reviews']):
                            self._coding_phase(conn,root,state,'P9',state.pop('pending_refine_skip'),
                                outcome='SKIPPED',reason='all_tests_pass_no_open_findings')
                        self._coding_phase(conn,root,state,phase,{'review_sha256':digest(state['reviews']),
                                                                 'test_receipt_sha256':digest(state['last_test'])})
                        if phase=='P6':
                            self._coding_converge(conn,root,state)
                        elif any(review['minor_findings'] for review in state['reviews']):
                            self._coding_retry(conn,root,state,'Final audit has unresolved minor findings')
                        else:
                            state['status'] = 'STAGING_GIT'
                            final_leaf = state['leaf_index']+1 == len(state['plan']['fracture_manifest']['topological_order'])
                            self._coding_job(conn, root, state, 'CODE_INTEGRATE', done=final_leaf,
                                             reviews_sha256=digest(state['reviews']), test_receipt_sha256=digest(state['last_test']))
            elif kind == 'CODE_RESEARCH':
                if job['payload'].get('research_phase')=='initial':
                    if output.get('plan_sha256')!=state['plan']['plan_sha256'] or not evidence.get('verified_sources') or not output.get('strategy'):
                        raise ValueError('Initial research must bind the approved plan and verified project sources')
                    state['strategy']=output['strategy']
                    self._coding_phase(conn,root,state,'P2',{'dossier_sha256':digest(output)})
                    state['status']='AUTHORING_TESTS'
                    self._coding_job(conn,root,state,'CODE_TEST_AUTHOR',test_paths=state['project']['test_paths'])
                    self._save_coding(conn,root['workflow_id'],state)
                    return self._get(conn,job_id)
                if output.get('failure_evidence_sha256') != job['payload']['failure_evidence_sha256']:
                    raise ValueError('Research dossier must bind the three actual failures')
                cause=output.get('root_cause')
                categories={'implementation','test_assumption','interface_contract','dependency','environment','requirements','unknown'}
                if (not isinstance(cause,dict) or cause.get('category') not in categories or
                        not isinstance(cause.get('diagnosis'),str) or len(cause['diagnosis'].strip())<12
                        or output.get('disposition') not in ('pivot','escalate')):
                    raise ValueError('Research requires structured root-cause triage and an explicit pivot or escalation')
                if not evidence.get('verified_sources') or not isinstance(output.get('strategy'), str) or not output['strategy'].strip():
                    raise ValueError('Research requires verified sources and a concrete changed strategy')
                if output['strategy'].strip() == state.get('strategy', '').strip():
                    raise ValueError('A research pivot must change the previous strategy')
                state['pivots'] += 1
                if output['disposition']=='escalate':
                    state['status']='RESEARCH_HOLD'
                    state['research_hold']={'dossier_sha256':digest(output),'root_cause':cause}
                    conn.execute("UPDATE pipeline_jobs SET status='BLOCKED',updated_at=? WHERE job_id=?",(time.time(),root['job_id']))
                    self._save_coding(conn,root['workflow_id'],state)
                    return self._get(conn,job_id)
                state['consecutive_failures'] = 0
                state['cycle'] += 1
                state['strategy'] = output['strategy']
                if cause['category']=='test_assumption':
                    state['current_snapshot']=state['leaf_baseline_snapshot']
                    state['sealed_tests_snapshot']=None
                    state['test_changes'],state['changes']=[],[]
                    state['retry_stage']='CODE_TEST_AUTHOR'
                    state['retry_phase']='P3'
                state['status'] = 'EDITING'
                self._coding_job(conn, root, state, state.get('retry_stage','CODE_EDIT'), allowed_paths=state['project']['allowed_paths'],
                                 test_paths=state['project']['test_paths'],
                                 phase=state.get('retry_phase','P4'),minor_findings=state.get('repair_findings',[]),
                                 research_dossier=output, test_receipt=state['last_test'])
            elif kind == 'CODE_INTEGRATE':
                staged, integration = evidence['staged'], evidence['integration']
                if evidence.get('source_snapshot_sha256') != state['current_snapshot']:
                    raise ValueError('Staged Git commit is not the independently reviewed source snapshot')
                if (evidence.get('reviews_sha256')!=digest(state['reviews']) or
                        evidence.get('test_receipt_sha256')!=digest(state['last_test'])):
                    raise ValueError('Git publication must bind the accepted tests and every file review')
                prepared = conn.execute('SELECT * FROM coding_integration_intents WHERE job_id=? AND attempt_id=? AND fencing_token=?',
                                        (job_id,attempt_id,fencing_token)).fetchone()
                if prepared is None or prepared['result_commit']!=staged.get('result_commit') or prepared['source_sha256']!=state['current_snapshot']:
                    raise ValueError('Git publication has no matching durable integration intent')
                if not job['payload'].get('approved_integration'):
                    state['chunks'].append({'cycle': state['cycle'], 'staged': staged,
                                            'reviews': state['reviews'], 'test_receipt_sha256': digest(state['last_test'])})
                state['last_commit'] = staged['result_commit']
                state['review_base_snapshot'] = state['current_snapshot']
                state['consecutive_failures'] = 0
                if not job['payload']['done']:
                    leaf_id=state['plan']['fracture_manifest']['topological_order'][state['leaf_index']]
                    state['completed_leaves'].append({'id':leaf_id,'cycle':state['cycle'],'commit':staged['result_commit']})
                    state['leaf_index']+=1
                    state['cycle'],state['pivots'],state['consecutive_failures']=1,0,0
                    state['leaf_baseline_snapshot']=state['current_snapshot']
                    state['status']='RESEARCHING'
                    self._coding_job(conn,root,state,'CODE_RESEARCH',research_phase='initial')
                elif integration.get('status') == 'INTEGRATED':
                    intent = conn.execute("SELECT result_commit FROM coding_integration_intents WHERE job_id=? AND attempt_id=? AND fencing_token=? AND state='APPLIED'",
                        (job_id,attempt_id,fencing_token)).fetchone()
                    if intent is None or intent['result_commit']!=staged['result_commit']:
                        raise ValueError('Successful integration requires a fenced durable Git CAS receipt')
                    leaf_id=state['plan']['fracture_manifest']['topological_order'][state['leaf_index']]
                    if not any(item['id']==leaf_id for item in state['completed_leaves']):
                        state['completed_leaves'].append({'id':leaf_id,'cycle':state['cycle'],'commit':staged['result_commit']})
                    state['status'] = 'COMPLETED'
                    conn.execute("UPDATE pipeline_jobs SET status='COMPLETED',updated_at=? WHERE job_id=?", (time.time(), root['job_id']))
                elif integration.get('status') == 'READY_TO_INTEGRATE':
                    state['status'] = 'READY_TO_INTEGRATE'
                    state['integration'] = integration
                    conn.execute("UPDATE pipeline_jobs SET status='BLOCKED',updated_at=? WHERE job_id=?", (time.time(), root['job_id']))
                else:
                    raise ValueError('Git integration returned an unrecognized physical result')
            self._save_coding(conn, root['workflow_id'], state)
            return self._get(conn, job_id)

    def resume_coding(self, workflow_id, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Coding resume requires an operator reason')
        with self._write() as conn:
            root = self._get(conn, workflow_id)
            state = self._coding_state(conn, workflow_id)
            if state['status']=='RESEARCH_HOLD' and root['status']=='BLOCKED':
                if state['pivots']>=3 or state['cycle']>=10:
                    raise ValueError('Research or repair budget is exhausted; resume cannot reset it')
                conn.execute("UPDATE pipeline_jobs SET status='IN_PROGRESS',updated_at=? WHERE job_id=?",(time.time(),workflow_id))
                state['status']='RESEARCH_REQUIRED'
                self._coding_job(conn,root,state,'CODE_RESEARCH',failures=state['failures'][-3:],
                    test_receipt=state['last_test'],failure_evidence_sha256=digest(state['failures'][-3:]),operator_resolution=reason)
                self._save_coding(conn,workflow_id,state)
                self._event(conn,root,'RESEARCH_RESUMED',reason=reason)
                return {'workflow_id':workflow_id,'status':'IN_PROGRESS','coding':state}
            if root['status']=='IN_PROGRESS':
                blocked = conn.execute("SELECT job_id FROM pipeline_jobs WHERE workflow_id=? AND kind IN ('CODE_TEST','CODE_INTEGRATE') AND status='BLOCKED'", (workflow_id,)).fetchall()
                if not blocked:
                    raise ValueError('Coding has no operator-held controller stage')
                for row in blocked:
                    conn.execute("UPDATE pipeline_jobs SET status='PENDING_RETRY',error=NULL,updated_at=? WHERE job_id=?",(time.time(),row['job_id']))
                    self._event(conn,self._get(conn,row['job_id']),'CODING_CONTROLLER_RESUMED',reason=reason)
                return {'workflow_id':workflow_id,'status':root['status'],'resumed_job_ids':[row['job_id'] for row in blocked]}
            if state['status'] != 'READY_TO_INTEGRATE' or root['status'] != 'BLOCKED':
                raise ValueError('Only a reviewed integration hold can be resumed; cancellation and budgets are final')
            conn.execute("UPDATE pipeline_jobs SET status='IN_PROGRESS',updated_at=? WHERE job_id=?", (time.time(), workflow_id))
            state['status'] = 'STAGING_GIT'
            self._coding_job(conn, root, state, 'CODE_INTEGRATE', done=True, approved_integration=True,
                             resume_reason=reason, reviews_sha256=digest(state['reviews']),
                             test_receipt_sha256=digest(state['last_test']))
            self._save_coding(conn, workflow_id, state)
        return self.coding_workflow(workflow_id)
