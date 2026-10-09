"""Durable coding contracts with real SQLite and Git.

Native and Docker completion metadata below are explicitly storage-protocol
fixtures, not live CLI/container execution claims.  Git operations, byte
hashing, immutable storage, leases, routing, and compare-and-swap are real.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time

import pytest
from pipeline_tests.windows_test_context import require_controller_git_context

from cochem_pipeline.coding import CodingProject, digest, manifest, observed_changes
from cochem_pipeline.coding_git import GitStager, RepositorySnapshot, capture_repository
from cochem_pipeline.coding_plan import validate_plan
from cochem_pipeline.container_policy import DockerPolicy
from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.store import JobStore, output_digest


def git(repository, *args):
    result = subprocess.run(['git', '-C', str(repository), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True,
                            env=dict(os.environ, GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@localhost',
                                     GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@localhost'))
    return result.stdout.decode().strip()


class CodingFixture:
    """Explicit completion fixtures around the actual controller storage API."""
    def __init__(self, tmp_path, *, strategy='red_green', auto=True, branch='delivery', two_leaves=False,
                 same_named_baseline_test=False):
        require_controller_git_context()
        self.two_leaves = two_leaves
        self.repository = tmp_path / 'repository'
        self.repository.mkdir()
        git(self.repository, 'init', '--initial-branch=main', '--template=')
        (self.repository / 'src').mkdir()
        source = ''.join(f'# Existing documentation line {index}\n' for index in range(30))
        (self.repository / 'src' / 'answer.py').write_text(source + 'ANSWER = 41\n')
        if two_leaves:
            (self.repository / 'src' / 'helper.py').write_text(source + 'HELPER = 0\n')
        (self.repository / 'tests').mkdir()
        existing_test = ('from pathlib import Path\nfrom runpy import run_path\ndef test_regression():\n'
            '    assert run_path(str(Path(__file__).parents[1] / "src/answer.py"))["ANSWER"] == 42\n'
            if same_named_baseline_test else 'def test_existing():\n    assert True\n')
        (self.repository / 'tests' / 'test_existing.py').write_text(existing_test)
        git(self.repository, 'add', '.')
        git(self.repository, 'commit', '-m', 'Original project')
        git(self.repository, 'branch', 'delivery')
        self.project = CodingProject.from_dict('project', {'repository': str(self.repository), 'branch': branch,
            'allowed_paths': ['src'], 'test_paths': ['tests'], 'auto_integrate': auto, 'test_strategy': strategy})
        self.git_executable = os.environ.get('COCHEM_WINDOWS_TEST_GIT_EXECUTABLE') or shutil.which('git')
        self.snapshot = capture_repository(self.repository, branch,git_executable=self.git_executable)
        self.policy = load_routing_policy({'backoff_base_seconds': .01, 'backoff_max_seconds': .02,
                                          'backoff_jitter_fraction': 0})
        self.store = JobStore(tmp_path / 'state.db', routing_policy=self.policy)
        image = 'sha256:' + '1' * 64
        self.docker = DockerPolicy.from_dict({'enabled': True, 'image': image, 'allowed_images': [image],
            'commands': [{'name': 'regression', 'argv': ['python', '-m', 'pytest',
                'tests/test_existing.py' if same_named_baseline_test else 'tests'], 'kind': 'pytest'}]})
        self.store.submit_coding_snapshot(self.project, 'Correct the answer to forty-two', ['REQ-1'],
                                         self.snapshot, self.docker, workflow_id='coding-request')
        self.workflow_id = 'coding-request'
        self.stager = GitStager(tmp_path / 'git-state',git_executable=self.git_executable)

    def claim(self, kind=None, *, lease_seconds=60, requires_cleanup=False):
        node = self.store.claim('storage-contract-fixture', worker_slot='worker-one', lease_seconds=lease_seconds,
                                requires_cleanup=requires_cleanup)
        assert node is not None, self.store.coding_workflow(self.workflow_id)
        if kind:
            assert node['kind'] == kind
        return node

    def state(self):
        return self.store.coding_state(self.workflow_id)

    def native_receipt(self, node, output):
        route = node['route']
        return {'execution_kind': 'controller-storage-contract-fixture', 'provider': route['provider'],
            'requested_model': route['model'], 'requested_effort': route.get('reasoning_effort'),
            'route_reservation_id': route['reservation_id'], 'pid': os.getpid(), 'exit_code': 0,
            'session_id': 'storage-protocol-fixture-only', 'output_sha256': output_digest(output),
            'attempt_id': node['attempt_id'], 'fencing_token': node['fencing_token'],
            'worker_slot': node['worker_slot'], 'job_id': node['job_id'], 'workflow_id': node['workflow_id'],
            'selected_route': route, 'subscription_verified': True}

    def complete_native(self, node, output, evidence, files=None):
        return self.store.complete_coding(node['job_id'], node['attempt_id'], node['fencing_token'],
            output, self.native_receipt(node, output), evidence=evidence, files=files)

    def author(self):
        if not self.state().get('plan'):
            self.plan()
        if self.state()['status'] == 'RESEARCHING':
            self.initial_research()
        node = self.claim('CODE_TEST_AUTHOR')
        before = self.store.coding_files(self.state()['current_snapshot'])
        name = node['payload']['test_cases'][0]['name']
        target = node['payload']['active_leaf']['file_targets'][0]
        variable, expected = ('ANSWER', 42) if target == 'src/answer.py' else ('HELPER', 1)
        test_source = ('from pathlib import Path\nfrom runpy import run_path\n'
                       f'def {name}():\n'
                       f'    source = Path(__file__).parents[1] / "{target}"\n'
                       f'    assert run_path(str(source))["{variable}"] == {expected}\n').encode()
        after = dict(before, **{f'tests/{name}.py': test_source})
        evidence = {'changes': observed_changes(before, after, self.snapshot.files, self.project, tests_only=True),
                    'snapshot_sha256': digest(manifest(after))}
        self.complete_native(node, {'summary': 'Protocol fixture authored regression', 'requirements_traced': ['REQ-1']}, evidence, after)
        return node

    def plan(self):
        node = self.claim('CODE_PLAN')
        output = {'goal': 'Correct the answer while preserving the existing module',
            'srs': {'skeleton': 'REQ-1: Answer is forty-two.', 'chapters': [
                {'id': 'ch01', 'title': 'Answer behavior', 'text': 'The answer must equal forty-two.', 'requirement_ids': ['R1']}]},
            'acceptance_criteria': [{'id': 'AC1', 'statement': 'The answer equals forty-two.',
                                      'requirement_ids': ['R1'], 'test_ids': ['T1']}],
            'test_cases': [{'id': 'T1', 'name': 'test_regression', 'asserts': 'ANSWER equals 42.', 'criteria_ids': ['AC1']}],
            'leaves': [{'id': 'L1', 'objective': 'Correct the answer in its existing module', 'file_targets': ['src/answer.py'],
                         'estimated_changed_lines': 20, 'estimated_added_deleted_lines':7, 'requirement_ids': ['R1'], 'criteria_ids': ['AC1'], 'dependencies': []}]}
        if self.two_leaves:
            output['acceptance_criteria'].append({'id': 'AC2', 'statement': 'The helper equals one.',
                                                  'requirement_ids': ['R1'], 'test_ids': ['T2']})
            output['test_cases'].append({'id': 'T2', 'name': 'test_helper_regression', 'asserts': 'HELPER equals one.',
                                         'criteria_ids': ['AC2']})
            output['leaves'].append({'id': 'L2', 'objective': 'Enable the helper after the answer is corrected',
                'file_targets': ['src/helper.py'], 'estimated_changed_lines': 20, 'estimated_added_deleted_lines':7, 'requirement_ids': ['R1'],
                'criteria_ids': ['AC2'], 'dependencies': ['L1']})
        plan = validate_plan(output, self.project, self.snapshot.files, ['REQ-1'])
        self.complete_native(node, output, {'plan': plan})
        review = self.claim('CODE_PLAN_REVIEW')
        self.complete_native(review, {'verdict': 'PASS', 'plan_sha256': plan['plan_sha256'],
             'requirements_checked': ['R1'], 'artifact_hashes': plan['artifact_hashes'], 'findings': []},
             {'source_snapshot_sha256': self.state()['current_snapshot']})

    def initial_research(self):
        node = self.claim('CODE_RESEARCH')
        assert node['payload']['research_phase'] == 'initial'
        source = node['payload']['active_leaf']['file_targets'][0]
        self.complete_native(node, {'plan_sha256': self.state()['plan']['plan_sha256'],
             'strategy': 'Read the existing answer module and implement its exact planned assertion'},
             {'verified_sources': [{'path': source, 'source_sha256': manifest(self.snapshot.files)[source]}]})

    def test_evidence(self, node, *, passed, errors=0):
        files = self.store.coding_files(node['payload']['snapshot_sha256'])
        index = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                        'executable': self.snapshot.modes.get(name) == '100755'} for name, data in files.items()}
        planned = [{'name': item['name'], 'class_name': 'tests.'+item['name'],
                    'status': 'error' if errors else 'passed' if passed else 'failed',
                    'assertion_failure': None if passed or errors else {
                        'phase': 'call', 'exception_type': 'AssertionError',
                        'path': node['payload']['test_identities'][item['name']]['path'],
                        'source_sha256': node['payload']['test_identities'][item['name']]['file_sha256'], 'line': 6}}
                   for item in node['payload']['test_cases']]
        return {'execution_kind': 'controller-storage-contract-fixture', 'kind': 'docker-test-execution',
            'job_id': node['job_id'], 'attempt_id': node['attempt_id'], 'image_id': self.docker.image,
            'policy_sha256': self.docker.digest, 'cleanup_verified': True, 'source_verified': True,
            'test_cycle_seconds':0.1,'test_cycle_deadline_met':True,
            'passed': passed, 'failure_category': None if passed else 'tests_failed', 'quarantine_required': False,
            'source': {'sha256': digest(index), 'files': index, 'file_count': len(index), 'bytes': sum(map(len, files.values()))},
            'commands': [{'name': 'regression', 'kind': 'pytest', 'argv': ['python', '-I', '-m', 'pytest', 'tests'],
                'exit_code': 0 if passed else 1, 'passed': passed, 'stdout': 'Protocol fixture diagnostics',
                'stderr': '', 'timed_out': False, 'cancelled': False, 'output_exceeded': False,
                'junit': {'tests': len(planned), 'failures': 0 if passed or errors else len(planned),
                          'errors': errors, 'skipped': 0},
                'junit_cases': planned}]}

    def complete_controller(self, node, output, evidence):
        receipt = {'execution_kind': 'controller-storage-contract-fixture',
                   'executor': 'docker' if node['kind'] == 'CODE_TEST' else 'git',
                   'job_id': node['job_id'], 'attempt_id': node['attempt_id'],
                   'fencing_token': node['fencing_token'], 'output_sha256': digest(output)}
        return self.store.complete_coding(node['job_id'], node['attempt_id'], node['fencing_token'],
                                         output, receipt, evidence=evidence)

    def test(self, *, passed, errors=0):
        node = self.claim('CODE_TEST')
        evidence = self.test_evidence(node, passed=passed, errors=errors)
        self.complete_controller(node, {'passed': passed, 'source_snapshot_sha256': node['payload']['snapshot_sha256'],
                                       'test_receipt_sha256': digest(evidence), 'phase': node['payload']['phase']}, evidence)
        return node

    def edit(self, *, done=True, value=42):
        node = self.claim('CODE_EDIT')
        before = self.store.coding_files(self.state()['current_snapshot'])
        after = dict(before)
        target = node['payload']['active_leaf']['file_targets'][0]
        variable = 'ANSWER' if target == 'src/answer.py' else 'HELPER'
        lines = after[target].decode().splitlines()
        lines[-1] = f'{variable} = {value}'
        after[target] = ('\n'.join(lines) + '\n').encode()
        review_base = self.store.coding_files(self.state()['review_base_snapshot'])
        evidence = {'changes': observed_changes(review_base, after, self.snapshot.files, self.project),
                    'snapshot_sha256': digest(manifest(after))}
        self.complete_native(node, {'summary': 'Protocol fixture changes one line', 'done': done,
                                   'requirements_traced': ['REQ-1']}, evidence, after)
        if node['payload'].get('phase') == 'P7':
            # A failed audit schedules improvement, then a distinct refinement
            # attempt. This fixture proposes no further refinement; only the
            # subsequent accepted test+audit may authorize its P9 skip.
            refinement = self.claim('CODE_EDIT')
            assert refinement['payload']['phase'] == 'P9'
            self.complete_native(refinement, {'done': done, 'remaining_work': False,
                'requirements_traced': ['REQ-1'], 'summary': 'Protocol fixture proposes no additional refinement'},
                {**evidence,'no_source_change': True}, after)
        return node

    def review_output(self, node, *, approved=True, objective_satisfied=True):
        if node['payload'].get('review_scope')=='srs_wbs_reconciliation':
            manifest=node['payload']['reconciliation_manifest']
            rows=[]
            for requirement in manifest['requirements']:
                criteria=sorted(item['id'] for item in manifest['acceptance_criteria'] if requirement in item['requirement_ids'])
                rows.append({'requirement_id':requirement,
                    'chapter_ids':sorted(item['id'] for item in manifest['srs_chapters'] if requirement in item['requirement_ids']),
                    'criteria_ids':criteria,
                    'test_ids':sorted(item['id'] for item in manifest['test_cases'] if set(item['criteria_ids']) & set(criteria)),
                    'wbs_leaf_ids':sorted(item['id'] for item in manifest['wbs_leaves'] if requirement in item['requirement_ids']),
                    'wbs_leaf_id':sorted(item['id'] for item in manifest['wbs_leaves'] if requirement in item['requirement_ids'])[0],
                    'status':'SATISFIED' if approved and objective_satisfied else 'DIVERGED',
                    'rationale':'Storage fixture joins the captured requirement to the exact SRS chapter, WBS leaf and physical test commitment.'})
            return {'approved':approved and objective_satisfied,'requirements_traced':['REQ-1'],
                'reconciliation_manifest_sha256':digest(manifest),'requirements_checked':rows,
                'divergences':[] if approved and objective_satisfied else ['Storage fixture reports a concrete SRS/WBS divergence.']}
        change = node['payload']['file']
        return {'path': change['path'], 'file_sha256': change['after_sha256'], 'diff_sha256': change['diff_sha256'],
            'test_receipt_sha256': node['payload']['test_receipt_sha256'], 'approved': approved,
            'objective_satisfied': objective_satisfied, 'requirements_traced': ['REQ-1'],
            'findings': ['Protocol fixture checks exact file and test evidence']}

    def reviews(self, *, approved=True, objective_satisfied=True, minor_findings=None):
        nodes = []
        while True:
            while self.state()['pending_reviews']:
                node = self.claim('CODE_REVIEW')
                output = self.review_output(node, approved=approved, objective_satisfied=objective_satisfied)
                if minor_findings is not None:
                    output['minor_findings'] = minor_findings
                self.complete_native(node, output, {'source_snapshot_sha256': self.state()['current_snapshot']})
                nodes.append(node)
            if self.state()['status'] != 'FINAL_TESTING':
                break
            self.test(passed=True)
        return nodes

    def ready(self, *, value=42, requires_cleanup=False):
        self.author()
        self.test(passed=False if self.project.test_strategy == 'red_green' else True)
        self.edit(value=value)
        self.test(passed=True)
        self.reviews()
        return self.claim('CODE_INTEGRATE',requires_cleanup=requires_cleanup)

    def stage(self, node):
        state = self.state()
        baseline = RepositorySnapshot.from_dict(state['repository_snapshot'], self.store.coding_files(state['original_snapshot']))
        if node['payload'].get('approved_integration'):
            staged = state['chunks'][-1]['staged']
        else:
            staged = self.stager.stage(baseline, self.store.coding_files(state['current_snapshot']),
                workflow_id=self.workflow_id, chunk_id=f'leaf-{state["leaf_index"]}-cycle-{state["cycle"]}',
                parent_commit=state['last_commit'],max_changed_lines=100)
        self.store.prepare_coding_integration(node, staged)
        return baseline, staged

    def integration(self, node, *, auto=True, acknowledge=True):
        baseline, staged = self.stage(node)
        result = self.stager.integrate(baseline, staged, auto_integrate=auto,
            cas_guard=lambda: self.store.coding_cas_guard(node))
        evidence = {'staged': staged, 'integration': result, 'source_snapshot_sha256': self.state()['current_snapshot'],
                    'reviews_sha256': node['payload']['reviews_sha256'],
                    'reconciliation_sha256':node['payload']['reconciliation_sha256'], 'test_receipt_sha256': node['payload']['test_receipt_sha256']}
        if acknowledge:
            self.complete_controller(node, {'status': result['status'], 'result_commit': staged['result_commit']}, evidence)
        return staged, result, evidence


def test_real_git_completion_requires_tests_asymmetric_reviews_and_durable_intent(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    state = case.state()
    assert state['precode_test']['passed'] is False
    assert state['last_test']['passed'] is True
    assert len(state['reviews']) == 2
    assert [entry['phase'] for entry in state['phase_ledger']] == [f'P{index}' for index in range(1, 11)]
    reviews = [job for job in case.store.coding_workflow(case.workflow_id)['jobs'] if job['kind'] == 'CODE_REVIEW']
    for review in reviews:
        assert (review['receipt']['provider'], review['receipt']['requested_model']) != (
            review['payload']['producer']['provider'], review['payload']['producer']['model'])
    staged, result, _ = case.integration(node)
    assert result['status'] == 'INTEGRATED'
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'COMPLETED'
    assert case.state()['status'] == 'COMPLETED'
    assert git(case.repository, 'rev-parse', 'delivery') == staged['result_commit']
    assert git(case.repository, 'show', 'delivery:src/answer.py').endswith('ANSWER = 42')
    assert (case.repository / 'src' / 'answer.py').read_text().endswith('ANSWER = 41\n')


def test_review_hold_resume_reuses_same_chunk_commit(tmp_path):
    case = CodingFixture(tmp_path, auto=False)
    first = case.ready()
    staged, result, _ = case.integration(first, auto=False)
    assert result['status'] == case.state()['status'] == 'READY_TO_INTEGRATE'
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'BLOCKED'
    assert git(case.repository, 'rev-parse', 'delivery') == case.snapshot.commit
    case.store.resume_coding(case.workflow_id, 'Operator reviewed the immutable change')
    second = case.claim('CODE_INTEGRATE')
    replayed, result, _ = case.integration(second)
    assert result['status'] == 'INTEGRATED'
    assert replayed['result_commit'] == staged['result_commit']
    assert len(case.state()['chunks']) == 1


@pytest.mark.parametrize('passed,errors', [(True, 0), (False, 1)])
def test_red_green_rejects_initial_green_or_collection_errors(tmp_path, passed, errors):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=passed, errors=errors)
    assert case.state()['current_snapshot'] == case.state()['original_snapshot']
    assert case.state()['sealed_tests_snapshot'] is None
    assert case.state()['cycle'] == 2
    case.claim('CODE_TEST_AUTHOR')


def test_preservation_policy_cannot_bypass_required_failing_first(tmp_path):
    with pytest.raises(ValueError,match='mandatory failing-first'):
        CodingFixture(tmp_path, strategy='preserve_behavior')


def test_existing_registered_test_mocks_cannot_enter_the_execution_snapshot(tmp_path):
    case=CodingFixture(tmp_path)
    (case.repository/'tests/test_existing.py').write_text('from unittest.mock import Mock\ndef test_existing():\n    assert Mock()\n')
    git(case.repository,'add','.');git(case.repository,'commit','-m','Add disallowed test dependency')
    git(case.repository,'branch','-f','delivery','HEAD')
    snapshot=capture_repository(case.repository,'delivery')
    with pytest.raises(ValueError,match='Zero-mock'):
        case.store.submit_coding_snapshot(case.project,'Inspect tests',['REQ-1'],snapshot,case.docker,workflow_id='unsafe-tests')


@pytest.mark.parametrize('category',['protocol','timeout'])
def test_native_transport_failure_retries_same_stage_after_durable_backoff(tmp_path,monkeypatch,category):
    case=CodingFixture(tmp_path)
    case.author(); case.test(passed=False)
    node=case.claim('CODE_EDIT')
    from types import SimpleNamespace
    from cochem_pipeline import routing_store,store as store_module
    clock=[time.time()]
    measured=SimpleNamespace(time=lambda:clock[0])
    monkeypatch.setattr(routing_store,'time',measured)
    monkeypatch.setattr(store_module,'time',measured)
    assert case.store.fail(node['job_id'],node['attempt_id'],node['fencing_token'],
        'Native transport produced no usable artifact',retry=True,category=category)
    failed=case.store.get(node['job_id'])
    assert failed['routing']['failure_count']==1 and failed['routing']['state']=='WAITING'
    assert case.state()['cycle']==1 and case.state()['consecutive_failures']==0
    assert case.store.claim('before-backoff',worker_slot='worker-one') is None
    case.store=JobStore(case.store.path,routing_policy=case.policy)
    clock[0]=failed['routing']['next_eligible_at']-.001
    assert case.store.claim('before-exact-boundary',worker_slot='worker-one') is None
    clock[0]=failed['routing']['next_eligible_at']
    retry=case.claim('CODE_EDIT')
    assert retry['job_id']==node['job_id'] and retry['attempt_id']!=node['attempt_id']
    assert case.store.fail(retry['job_id'],retry['attempt_id'],retry['fencing_token'],
        'Actual proposed artifact violates its bounded contract',retry=True,category='code')
    assert case.state()['cycle']==2 and case.state()['consecutive_failures']==1


@pytest.mark.parametrize('duration,met',[(30.01,False),(float('nan'),True),(None,True),(1,False)])
def test_precode_receipt_cannot_bypass_test_and_collection_deadline(tmp_path,duration,met):
    case=CodingFixture(tmp_path);case.author();node=case.claim('CODE_TEST')
    evidence=case.test_evidence(node,passed=False)
    evidence.update(test_cycle_seconds=duration,test_cycle_deadline_met=met)
    with pytest.raises(ValueError):
        case.complete_controller(node,{'passed':False,'phase':'precode'},evidence)
    assert case.state()['status']=='TESTING_PRECODE'


@pytest.mark.parametrize('mutation',['missing','skipped','unrelated_failure','outcome_alias'])
def test_precode_red_gate_requires_the_actual_planned_regression_to_fail(tmp_path,mutation):
    case=CodingFixture(tmp_path)
    case.author()
    node=case.claim('CODE_TEST')
    evidence=case.test_evidence(node,passed=False)
    command=evidence['commands'][0]
    planned=command['junit_cases'][0]
    if mutation=='missing':
        command['junit_cases']=[]
    elif mutation=='skipped':
        planned['status']='skipped'
    elif mutation=='unrelated_failure':
        planned['status']='passed'
        command['junit_cases'].append({'name':'test_unrelated_old_failure','class_name':'tests.test_old','status':'failed'})
    else:
        planned['outcome']=planned.pop('status')
    case.complete_controller(node,{'passed':False},evidence)
    assert case.state()['cycle']==2
    assert case.state()['current_snapshot']==case.state()['leaf_baseline_snapshot']
    assert case.state()['sealed_tests_snapshot'] is None
    assert not any(job['kind']=='CODE_EDIT' for job in case.store.coding_workflow(case.workflow_id)['jobs'])
    case.claim('CODE_TEST_AUTHOR')


@pytest.mark.parametrize('mutation',['missing','skipped','failed','outcome_alias'])
def test_green_gate_cannot_publish_reviews_when_planned_test_execution_is_absent(tmp_path,mutation):
    case=CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    case.edit()
    node=case.claim('CODE_TEST')
    evidence=case.test_evidence(node,passed=True)
    command=evidence['commands'][0]
    if mutation=='missing':
        command['junit_cases']=[]
    elif mutation=='outcome_alias':
        command['junit_cases'][0]['outcome']=command['junit_cases'][0].pop('status')
    else:
        command['junit_cases'][0]['status']=mutation
    with pytest.raises(ValueError,match='planned leaf test'):
        case.complete_controller(node,{'passed':True},evidence)
    assert case.store.get(node['job_id'])['status']=='IN_PROGRESS'
    assert not case.state()['pending_reviews']


@pytest.mark.parametrize('mutation', ['file_bytes', 'source_hash', 'cleanup', 'verification'])
def test_docker_receipt_cannot_change_source_or_skip_physical_proof(tmp_path, mutation):
    case = CodingFixture(tmp_path)
    case.author()
    node = case.claim('CODE_TEST')
    evidence = case.test_evidence(node, passed=False)
    if mutation == 'file_bytes':
        evidence['source']['files']['src/answer.py']['sha256'] = '0' * 64
        evidence['source']['sha256'] = digest(evidence['source']['files'])
    elif mutation == 'source_hash':
        evidence['source']['sha256'] = '0' * 64
    else:
        evidence['cleanup_verified' if mutation == 'cleanup' else 'source_verified'] = False
    with pytest.raises(ValueError, match='Docker'):
        case.complete_controller(node, {'passed': False}, evidence)
    assert case.store.get(node['job_id'])['status'] == 'IN_PROGRESS'
    assert case.store.get(node['job_id'])['output'] is None


@pytest.mark.parametrize('field', ['file_sha256', 'diff_sha256', 'test_receipt_sha256'])
def test_review_must_bind_exact_files_diff_and_test_receipt(tmp_path, field):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    case.edit()
    case.test(passed=True)
    node = case.claim('CODE_REVIEW')
    output = case.review_output(node)
    output[field] = '0' * 64
    with pytest.raises(ValueError, match='bind'):
        case.complete_native(node, output, {'source_snapshot_sha256': case.state()['current_snapshot']})
    assert node['job_id'] in case.state()['pending_reviews']


def test_independent_objective_rejection_starts_next_cycle(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    case.edit()
    case.test(passed=True)
    case.reviews(objective_satisfied=False)
    assert case.state()['cycle'] == 2
    assert case.state()['consecutive_failures'] == 1
    case.claim('CODE_EDIT')


def test_cancelled_attempt_cannot_apply_real_git_cas(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    baseline, staged = case.stage(node)
    case.store.cancel_workflow(case.workflow_id)
    with pytest.raises(ValueError, match='Cancellation|revoked'):
        case.stager.integrate(baseline, staged, auto_integrate=True,
                              cas_guard=lambda: case.store.coding_cas_guard(node))
    assert git(case.repository, 'rev-parse', 'delivery') == case.snapshot.commit
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'FAILED'


def test_restart_after_git_cas_replays_without_creating_another_commit(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    staged, result, _ = case.integration(node, acknowledge=False)
    assert result['status'] == 'INTEGRATED'
    # No model or container runs here: simulate controller death between the
    # physical CAS and job completion by reopening its real durable database.
    case.store = JobStore(case.store.path, routing_policy=case.policy)
    assert case.store.get(node['job_id'])['status'] == 'IN_PROGRESS'
    replayed, result, _ = case.integration(node)
    assert result['reason'] == 'already_integrated'
    assert replayed['result_commit'] == staged['result_commit']
    assert len(case.state()['chunks']) == 1


def test_integration_claim_without_applied_intent_cannot_complete(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    _, staged = case.stage(node)
    evidence = {'staged': staged, 'integration': {'status': 'INTEGRATED'},
                'source_snapshot_sha256': case.state()['current_snapshot'],
                'reviews_sha256': node['payload']['reviews_sha256'],
                'reconciliation_sha256':node['payload']['reconciliation_sha256'],
                'test_receipt_sha256': node['payload']['test_receipt_sha256']}
    with pytest.raises(ValueError, match='CAS receipt'):
        case.complete_controller(node, {'status': 'INTEGRATED'}, evidence)
    assert case.state()['chunks'] == []
    assert git(case.repository, 'rev-parse', 'delivery') == case.snapshot.commit


def test_three_failures_force_evidence_bound_research_and_keep_durable_budget(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    for value in (42, 43, 44):
        case.edit(value=value)
        case.test(passed=False)
        case.reviews(approved=False,objective_satisfied=False)
    assert case.state()['status'] == 'RESEARCH_REQUIRED'
    assert case.state()['cycle'] == 3
    node = case.claim('CODE_RESEARCH')
    assert node['payload']['failure_evidence_sha256'] == digest(case.state()['failures'][-3:])
    output = {'failure_evidence_sha256': node['payload']['failure_evidence_sha256'],
              'root_cause':{'category':'implementation','diagnosis':'The changed answer fails its exact reviewed assertion'},
              'disposition':'pivot',
              'strategy': 'Examine the exact failing assertion before changing the return value'}
    with pytest.raises(ValueError, match='verified sources'):
        case.complete_native(node, output, {'verified_sources': []})
    case.complete_native(node, output, {'verified_sources': [
        {'path': '$test_receipt', 'source_sha256': digest(case.state()['last_test'])},
        {'path': 'src/answer.py', 'source_sha256': manifest(case.store.coding_files(case.state()['current_snapshot']))['src/answer.py']}]})
    restarted = JobStore(case.store.path, routing_policy=case.policy)
    assert restarted.coding_state(case.workflow_id)['pivots'] == 1
    assert restarted.coding_state(case.workflow_id)['cycle'] == 4
    assert restarted.coding_state(case.workflow_id)['consecutive_failures'] == 0


def test_coding_artifact_tables_reject_mutation(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    with sqlite3.connect(case.store.path) as conn:
        for sql in ('UPDATE coding_blobs SET content=X\'00\'', 'DELETE FROM coding_snapshots',
                    "UPDATE coding_evidence SET evidence_json='{}'"):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute(sql)


def test_edits_from_failed_cycle_still_require_file_review_after_later_fix(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    first = case.claim('CODE_EDIT')
    before = case.store.coding_files(case.state()['current_snapshot'])
    after = dict(before)
    after['src/answer.py']=after['src/answer.py'].replace(b'# Existing documentation line 0',b'# Corrected explanation from the first failed cycle')
    changes = observed_changes(before, after, case.snapshot.files, case.project)
    case.complete_native(first, {'done': False, 'requirements_traced': ['REQ-1']},
                          {'changes': changes, 'snapshot_sha256': digest(manifest(after))}, after)
    case.test(passed=False)
    # The second attempt changes only the final answer assignment. Its accepted
    # file diff must retain the earlier explanation change too.
    case.reviews(approved=False,objective_satisfied=False)
    case.edit()
    case.test(passed=True)
    pending = [case.store.get(identifier) for identifier in case.state()['pending_reviews']]
    assert {node['payload']['file']['path'] for node in pending} == {'src/answer.py', 'tests/test_regression.py'}
    source_review = next(node for node in pending if node['payload']['file']['path'] == 'src/answer.py')
    assert '+# Corrected explanation from the first failed cycle' in source_review['payload']['file']['patch']


def test_ten_cycle_and_three_pivot_budgets_survive_restart(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    case.test(passed=False)
    for cycle in range(1, 11):
        case.edit(value=41 + cycle)
        case.test(passed=False)
        case.reviews(approved=False,objective_satisfied=False)
        case.store = JobStore(case.store.path, routing_policy=case.policy)
        if case.state()['status'] == 'RESEARCH_REQUIRED':
            node = case.claim('CODE_RESEARCH')
            case.complete_native(node, {'failure_evidence_sha256': node['payload']['failure_evidence_sha256'],
                'root_cause':{'category':'implementation','diagnosis':'The changed answer fails its exact reviewed assertion'},
                'disposition':'pivot',
                'strategy': f'Analyze the specific failure set from cycle {cycle}'},
                {'verified_sources': [{'path': '$test_receipt', 'source_sha256': digest(case.state()['last_test'])},
                    {'path': 'src/answer.py', 'source_sha256': manifest(case.store.coding_files(case.state()['current_snapshot']))['src/answer.py']}]})
    assert case.state()['status'] == 'PHYSICS_WALL'
    assert case.state()['cycle'] == 10
    assert case.state()['pivots'] == 3
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'FAILED'
    autopsy=case.state()['autopsy']
    assert autopsy['marker']=='[HARD_ABORT: PHYSICS WALL]'
    artifacts=case.store.coding_files(autopsy['snapshot_sha256'])
    report=artifacts['Physics_Autopsy_Report.md']
    assert hashlib.sha256(report).hexdigest()==autopsy['report_sha256']
    assert report.decode()==autopsy['report'] and b'does not establish a physical impossibility' in report
    evidence=json.loads(artifacts['AutopsyEvidence.json'])
    assert digest(evidence)==autopsy['evidence_sha256']
    assert evidence['scientific_diagnosis'] is None and evidence['trigger']=='methodological_pivots'
    assert len(evidence['failures'])==10 and len(evidence['research_dossiers'])==3
    assert digest(evidence['failure_history'])==evidence['failure_history_sha256']
    assert all(len(json.dumps(item['diagnostics'],separators=(',',':')).encode())<2000
               for item in evidence['research_dossiers'])
    with sqlite3.connect(case.store.path) as conn:
        assert conn.execute('SELECT error FROM pipeline_jobs WHERE job_id=?',(case.workflow_id,)).fetchone()[0].startswith('[HARD_ABORT: PHYSICS WALL]')
        retained=json.loads(conn.execute('SELECT evidence_json FROM coding_evidence WHERE job_id=?',(case.workflow_id,)).fetchone()[0])
        assert retained==evidence
        for execution in evidence['executions']:
            assert conn.execute('SELECT output_sha256 FROM pipeline_outputs WHERE job_id=?',(execution['job_id'],)).fetchone()[0]==execution['output_sha256']
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('UPDATE coding_evidence SET evidence_json=? WHERE job_id=?',('{}',case.workflow_id))
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('DELETE FROM coding_blobs WHERE sha256=?',(autopsy['report_sha256'],))
    assert case.store.claim('no-eleventh-cycle', worker_slot='worker-one') is None
    with pytest.raises(ValueError, match='final'):
        case.store.resume_coding(case.workflow_id, 'Cannot reset an exhausted budget')


def test_ten_cycle_limit_still_writes_autopsy_without_claiming_three_failed_pivots(tmp_path):
    case=CodingFixture(tmp_path);case.author();case.test(passed=False)
    # Boundary restoration of an old durable state with fewer research pivots.
    # No model/container execution is claimed by this storage-contract case.
    with case.store._write() as conn:
        state=case.store._coding_state(conn,case.workflow_id);state['cycle']=10;state['pivots']=2
        root=case.store._get(conn,case.workflow_id)
        case.store._coding_retry(conn,root,state,'Restored final cycle failed its actual contract')
        case.store._save_coding(conn,case.workflow_id,state)
    restarted=JobStore(case.store.path,routing_policy=case.policy)
    state=restarted.coding_state(case.workflow_id)
    assert state['status']=='EXHAUSTED'
    assert state['autopsy']['marker']=='[HARD_ABORT: TDD CYCLE LIMIT]'
    assert state['autopsy']['trigger']=='tdd_cycles'
    assert 'Physics_Autopsy_Report.md' in restarted.coding_files(state['autopsy']['snapshot_sha256'])
    assert restarted.claim('no-resume',worker_slot='worker-one') is None


def test_third_unresolved_research_escalation_writes_autopsy_instead_of_unresumable_hold(tmp_path):
    case=CodingFixture(tmp_path);case.author();case.test(passed=False)
    for value in (42,43,44):
        case.edit(value=value);case.test(passed=False);case.reviews(approved=False,objective_satisfied=False)
    for pivot in range(1,4):
        node=case.claim('CODE_RESEARCH')
        case.complete_native(node,{'failure_evidence_sha256':node['payload']['failure_evidence_sha256'],
            'root_cause':{'category':'interface_contract','diagnosis':'The recorded assertion conflicts with the documented interface contract'},
            'disposition':'escalate','strategy':f'Resolve interface ambiguity number {pivot}'},
            {'verified_sources':[{'path':'$test_receipt','source_sha256':digest(case.state()['last_test'])}]})
        if pivot<3:
            assert case.state()['status']=='RESEARCH_HOLD'
            case.store.resume_coding(case.workflow_id,'Operator supplied an additional contract interpretation')
    assert case.state()['status']=='PHYSICS_WALL' and case.state()['cycle']==3
    evidence=json.loads(case.store.coding_files(case.state()['autopsy']['snapshot_sha256'])['AutopsyEvidence.json'])
    assert len(evidence['research_dossiers'])==3 and evidence['pivots']==3
    assert case.store.claim('fourth-research',worker_slot='worker-one') is None


def test_late_container_startup_records_event_bound_to_immutable_attempt_receipt(tmp_path):
    case=CodingFixture(tmp_path);case.author();node=case.claim('CODE_TEST')
    evidence=case.test_evidence(node,passed=False)
    evidence.update(startup_sla_met=False,startup_seconds=2.5,startup_sla_seconds=1.5,
        queue_wait_seconds=2.0,handoff_wait_seconds=.2,execution_startup_seconds=.3,
        startup_violation_reason='queue_and_container_readiness_exceeded_1.5_seconds')
    case.store.record_coding_attempt_evidence(node,evidence)
    with sqlite3.connect(case.store.path) as conn:
        row=conn.execute("SELECT details_json FROM pipeline_events WHERE job_id=? AND event='CONTAINER_STARTUP_SLA_MISSED'",(node['job_id'],)).fetchone()
        details=json.loads(row[0])
        assert details['evidence_sha256']==digest(evidence) and details['startup_seconds']==2.5
        assert details['queue_wait_seconds']==2.0 and details['handoff_wait_seconds']==.2
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            conn.execute('UPDATE coding_attempt_evidence SET evidence_json=? WHERE job_id=?',('{}',node['job_id']))


def test_controller_and_native_jobs_share_configured_worker_global_cap(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    for index in range(5):
        case.store.submit('Document a simple note', ['REQ-1'], 1, workflow_id=f'planning-{index}')
    active = []
    for index in range(5):
        node = case.store.claim(f'owner-{index}', worker_slot=f'worker-{index}', max_workers=4)
        if node is not None:
            active.append(node)
    assert len(active) == 4
    assert active[0]['kind'] == 'CODE_TEST'
    assert active[0]['route'] is None
    assert len(case.store.routing_status()['active_reservations']) == 3


def test_resource_hold_preserves_code_cycle_and_controller_failure_budget(tmp_path):
    case = CodingFixture(tmp_path)
    case.author()
    node = case.claim('CODE_TEST')
    assert case.store.fail(node['job_id'], node['attempt_id'], node['fencing_token'],
                           'Storage fixture resource pressure', retry=True, category='resource',
                           retry_after_seconds=1, hold_scope='job')
    assert case.store.get(node['job_id'])['status'] == 'BLOCKED'
    assert case.state()['cycle'] == 1
    assert case.state()['consecutive_failures'] == 0
    case.store.resume_coding(case.workflow_id, 'Measured capacity recovered')
    assert case.store.claim('cooldown-not-elapsed', worker_slot='worker-one') is None
    time.sleep(2.05)
    retried = case.claim('CODE_TEST')
    assert retried['job_id'] == node['job_id']
    assert retried['attempt_id'] != node['attempt_id']
    with sqlite3.connect(case.store.path) as conn:
        assert conn.execute('SELECT failures FROM coding_controller_retries WHERE job_id=?', (node['job_id'],)).fetchone()[0] == 0


def test_diff_boundaries_apply_to_aggregate_history_and_preserve_tests(tmp_path):
    case = CodingFixture(tmp_path)
    original = {'src/large.py': ''.join(f'value_{index} = {index}\n' for index in range(1000)).encode()}
    before = dict(original)
    before['src/large.py'] = ''.join(f'changed_{index} = 1\n' if index < 500 else f'value_{index} = {index}\n'
                                    for index in range(1000)).encode()
    after = dict(before)
    after['src/large.py'] = after['src/large.py'].replace(b'value_500 = 500', b'changed_500 = 1')
    with pytest.raises(ValueError, match='Aggregate'):
        observed_changes(before, after, original, case.project)
    huge_chunk = dict(original, **{'src/new.py': b'x = 1\n' * 101})
    with pytest.raises(ValueError, match='100 lines'):
        observed_changes(original, huge_chunk, original, case.project)
    with pytest.raises(ValueError, match='protected test'):
        observed_changes({}, {'tests/test_passing.py': b'assert True\n'}, {}, case.project)
    for filename in ('conftest.py', 'pytest.ini', 'tox.ini', 'pyproject.toml', 'setup.cfg', 'setup.py'):
        with pytest.raises(ValueError, match='configuration'):
            observed_changes({}, {f'tests/{filename}': b'ignored = True\n'}, {}, case.project, tests_only=True)


def test_legacy_job_table_migration_preserves_outputs_and_foreign_keys(tmp_path):
    from cochem_pipeline.store import SCHEMA
    from cochem_pipeline.coding_store import CODING_KINDS
    database = tmp_path / 'legacy.db'
    legacy_schema = SCHEMA
    for kind in CODING_KINDS:
        # The legacy enum had four planning kinds, while the other table layouts
        # and immutable output ownership remain the actual production schema.
        legacy_schema = legacy_schema.replace("," + repr(kind), '')
    now = time.time()
    with sqlite3.connect(database) as conn:
        conn.executescript(legacy_schema)
        payload = json.dumps({'objective': 'Legacy note', 'requirements': ['REQ-1'], 'chapter_count': 1})
        statement = ('INSERT INTO pipeline_jobs(job_id,workflow_id,parent_job_id,kind,status,payload_json,'
                     'attempt_id,fencing_token,attempts,max_attempts,lease_expires_at,created_at,updated_at) '
                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)')
        conn.execute(statement, ('legacy-root', 'legacy-root', None, 'MACRO_PLANNING_REQUEST', 'IN_PROGRESS',
                                  payload, None, 0, 0, 3, None, now, now))
        conn.execute(statement, ('legacy-manifest', 'legacy-root', 'legacy-root', 'MANIFEST_GENERATOR', 'IN_PROGRESS',
                                  payload, 'legacy-attempt', 1, 1, 3, now + 60, now, now))
        output = {'chapters': [{'chapter_id': 'one', 'title': 'Legacy', 'requirements': ['REQ-1']}]}
        conn.execute('INSERT INTO pipeline_outputs VALUES(?,?,?,?,?,?,?,?)',
            ('legacy-manifest', 'legacy-attempt', 1, None, json.dumps(output), '{}', digest(output), now))
        conn.execute("UPDATE pipeline_jobs SET status='COMPLETED' WHERE job_id='legacy-manifest'")
    migrated = JobStore(database)
    assert migrated.get('legacy-manifest')['output'] == output
    assert migrated.get('legacy-manifest')['attempts'] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='pipeline_jobs'").fetchone()[0]
        assert "'CODE_REQUEST'" in sql
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE pipeline_outputs SET output_json='{}'")


def test_two_validated_leaves_keep_test_seals_and_git_parent_lineage(tmp_path):
    case = CodingFixture(tmp_path, two_leaves=True)
    first = case.ready()
    assert first['payload']['done'] is False
    first_commit, held, _ = case.integration(first, auto=False)
    assert held['status'] == 'READY_TO_INTEGRATE'
    assert case.state()['status'] == 'RESEARCHING'
    assert case.state()['leaf_index'] == 1
    assert case.state()['cycle'] == 1
    first_tests = case.store.coding_files(case.state()['current_snapshot'])['tests/test_regression.py']
    assert case.state()['completed_leaves'][0]['id'] == 'L1'
    assert git(case.repository, 'rev-parse', 'delivery') == case.snapshot.commit
    case.store = JobStore(case.store.path, routing_policy=case.policy)
    second = case.ready(value=1)
    assert second['payload']['done'] is True
    final_commit, integrated, _ = case.integration(second)
    assert integrated['status'] == 'INTEGRATED'
    assert final_commit['parent_commit'] == first_commit['result_commit']
    assert git(case.repository, 'rev-parse', 'delivery^') == first_commit['result_commit']
    assert git(case.repository, 'rev-parse', 'delivery^^') == case.snapshot.commit
    assert git(case.repository, 'show', 'delivery:src/answer.py').endswith('ANSWER = 42')
    assert git(case.repository, 'show', 'delivery:src/helper.py').endswith('HELPER = 1')
    assert case.store.coding_files(case.state()['current_snapshot'])['tests/test_regression.py'] == first_tests
    assert len(case.state()['chunks']) == 2


def test_real_sqlite_attempt_guard_serializes_cancel_with_git_cas(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    baseline, staged = case.stage(node)
    guard_entered = threading.Event()
    cancel_started = threading.Event()
    cancel_finished = threading.Event()
    release_cas = threading.Event()
    results, errors = [], []

    @contextmanager
    def fenced_guard():
        with case.store.coding_cas_guard(node):
            guard_entered.set()
            assert release_cas.wait(timeout=10)
            yield

    def integrate():
        try:
            results.append(case.stager.integrate(baseline, staged, auto_integrate=True, cas_guard=fenced_guard))
        except BaseException as error:
            errors.append(error)

    def cancel():
        cancel_started.set()
        try:
            case.store.cancel_workflow(case.workflow_id)
        except BaseException as error:
            errors.append(error)
        finally:
            cancel_finished.set()

    worker = threading.Thread(target=integrate)
    cancellation = threading.Thread(target=cancel)
    worker.start()
    assert guard_entered.wait(timeout=10)
    cancellation.start()
    assert cancel_started.wait(timeout=10)
    try:
        assert not cancel_finished.wait(timeout=.1), 'Cancellation passed an open SQLite attempt fence'
    finally:
        release_cas.set()
    worker.join(timeout=10)
    cancellation.join(timeout=10)
    assert not worker.is_alive() and not cancellation.is_alive()
    assert errors == []
    assert results[0]['status'] == 'INTEGRATED'
    assert git(case.repository, 'rev-parse', 'delivery') == staged['result_commit']
    assert case.store.coding_workflow(case.workflow_id)['status'] == 'FAILED'
    with sqlite3.connect(case.store.path) as conn:
        assert conn.execute('SELECT state,result_commit FROM coding_integration_intents WHERE job_id=?',
                            (node['job_id'],)).fetchone() == ('APPLIED', staged['result_commit'])


def pending_noop_refinement(case):
    case.author()
    case.test(passed=False)
    case.edit()
    case.test(passed=True)
    case.reviews(minor_findings=['Clarify the first source comment without changing behavior'])
    improvement = case.claim('CODE_EDIT')
    assert improvement['payload']['phase'] == 'P7'
    before = case.store.coding_files(case.state()['current_snapshot'])
    after = dict(before)
    after['src/answer.py'] = after['src/answer.py'].replace(
        b'# Existing documentation line 0\n', b'# Clarified answer documentation\n')
    review_base = case.store.coding_files(case.state()['review_base_snapshot'])
    changes = observed_changes(review_base, after, case.snapshot.files, case.project)
    case.complete_native(improvement, {'done': True, 'requirements_traced': ['REQ-1']},
        {'changes': changes, 'snapshot_sha256': digest(manifest(after))}, after)
    refinement = case.claim('CODE_EDIT')
    assert refinement['payload']['phase'] == 'P9'
    case.complete_native(refinement, {'done': True, 'remaining_work': False, 'requirements_traced': ['REQ-1']},
        {'changes': changes, 'snapshot_sha256': digest(manifest(after)), 'no_source_change': True}, after)
    assert case.state()['pending_refine_skip']['artifact_sha256'] == case.state()['current_snapshot']
    assert not any(record['phase'] == 'P9' for record in case.state()['phase_ledger'])


@pytest.mark.parametrize('rejection', ['tests_failed', 'minor_findings'])
def test_noop_refinement_skip_requires_final_test_and_audit_proof(tmp_path, rejection):
    case = CodingFixture(tmp_path)
    pending_noop_refinement(case)
    case.test(passed=rejection != 'tests_failed')
    if rejection == 'minor_findings':
        case.reviews(minor_findings=['The documentation clarification remains ambiguous'])
    else:
        case.reviews(approved=False,objective_satisfied=False)
    assert case.state()['cycle'] == 2
    assert not case.state().get('pending_refine_skip')
    assert not any(record['phase'] == 'P9' for record in case.state()['phase_ledger'])


def test_noop_refinement_records_skip_after_accepted_final_evidence(tmp_path):
    case = CodingFixture(tmp_path)
    pending_noop_refinement(case)
    case.test(passed=True)
    assert not any(record['phase'] == 'P9' for record in case.state()['phase_ledger'])
    case.reviews()
    records = case.state()['phase_ledger']
    assert [record['phase'] for record in records[-2:]] == ['P9', 'P10']
    assert records[-2]['outcome'] == 'SKIPPED'
    assert records[-2]['reason'] == 'all_tests_pass_no_open_findings'
    assert records[-2]['evidence']['artifact_sha256'] == case.state()['current_snapshot']
    assert not case.state().get('pending_refine_skip')


def test_reviewed_snapshot_cannot_authorize_different_real_git_bytes(tmp_path):
    case = CodingFixture(tmp_path)
    node = case.ready()
    baseline = RepositorySnapshot.from_dict(case.state()['repository_snapshot'], case.snapshot.files)
    changed = dict(case.store.coding_files(case.state()['current_snapshot']))
    changed['src/answer.py'] = changed['src/answer.py'].replace(b'ANSWER = 42', b'ANSWER = 999')
    unreviewed = case.stager.stage(baseline, changed, workflow_id=case.workflow_id,
                                    chunk_id='unreviewed-result')
    with pytest.raises(ValueError, match='accepted reviews'):
        case.store.prepare_coding_integration(node, unreviewed)
    assert git(case.repository, 'rev-parse', 'delivery') == case.snapshot.commit
    with sqlite3.connect(case.store.path) as conn:
        assert conn.execute('SELECT count(*) FROM coding_integration_intents').fetchone()[0] == 0
