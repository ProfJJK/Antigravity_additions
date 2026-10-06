"""Independent physical crash boundaries for the SQLite/Git publication split.

Preparation uses explicitly labelled storage-contract fixtures for native model
and test evidence. The child process, process death, SQLite rollback, Git CAS,
fencing and replay below are actual operations; no kernel/Git mocks are used.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from pipeline_tests.test_coding_workflow import CodingFixture, git
from cochem_pipeline.store import JobStore
from cochem_pipeline.coding import validate_project_files, read_workspace, materialize, digest, observed_changes, manifest
from cochem_pipeline.coding_git import capture_repository
from cochem_pipeline.containers import parse_junit, junit_cases


_CRASH_AFTER_CAS = r'''
from contextlib import contextmanager
import json,os,sys
from pathlib import Path
from cochem_pipeline.store import JobStore
from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.coding_git import GitStager,RepositorySnapshot

args=json.loads(Path(sys.argv[1]).read_text())
store=JobStore(args['db'],routing_policy=load_routing_policy(args['policy']))
state=store.coding_state(args['node']['workflow_id'])
baseline=RepositorySnapshot.from_dict(state['repository_snapshot'],store.coding_files(state['original_snapshot']))
stager=GitStager(Path(args['git_state']),git_executable=args['git_executable'])
@contextmanager
def crash_before_sqlite_commit():
    with store.coding_cas_guard(args['node']):
        yield
        os._exit(73)
stager.integrate(baseline,args['staged'],auto_integrate=True,cas_guard=crash_before_sqlite_commit)
raise SystemExit('The injected physical crash boundary was not reached')
'''


def _crash_during_publication(case, tmp_path):
    # This test actually launches a child, unlike the completion-only protocol
    # fixtures. Register its cleanup barrier before creating that process.
    node = case.ready(requires_cleanup=True)
    _, staged = case.stage(node)
    arguments = tmp_path / "crash-input.json"
    arguments.write_text(json.dumps({"node": node, "staged": staged, "db": str(case.store.path),
        "policy": case.policy.as_dict(), "git_state": str(case.stager.state_root),
        "git_executable": case.stager.git_executable}), encoding="utf-8")
    child = subprocess.run([sys.executable, "-c", _CRASH_AFTER_CAS, str(arguments)],
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == 73, child.stderr
    assert git(case.repository, "rev-parse", "delivery") == staged["result_commit"]
    with sqlite3.connect(case.store.path) as conn:
        row = conn.execute("SELECT state,result_commit FROM coding_integration_intents WHERE job_id=? AND attempt_id=?",
                           (node["job_id"], node["attempt_id"])).fetchone()
    assert row == ("PREPARED", staged["result_commit"]), "Actual process death must roll back the open SQLite acknowledgement"
    return node, staged


def test_real_death_between_git_cas_and_sqlite_commit_requires_a_new_fence_and_replays_once(tmp_path):
    case = CodingFixture(tmp_path)
    old, staged = _crash_during_publication(case, tmp_path)
    assert case.store.heartbeat(old["job_id"], old["attempt_id"], old["fencing_token"], .001)
    time.sleep(.02)
    case.store.reap_expired()
    # The test has observed the real child exit after Git itself returned. This
    # supplies the controller's cleanup acknowledgement; no model can call it.
    case.store.clear_execution_quarantine(old["job_id"], old["attempt_id"], old["fencing_token"])
    case.store = JobStore(case.store.path, routing_policy=case.policy)
    replacement = case.claim("CODE_INTEGRATE")
    assert replacement["attempt_id"] != old["attempt_id"]
    assert replacement["fencing_token"] > old["fencing_token"]
    replayed, result, _ = case.integration(replacement)
    assert result["status"] == "INTEGRATED" and result["reason"] == "already_integrated"
    assert replayed["result_commit"] == staged["result_commit"]
    assert git(case.repository, "rev-list", "--count", case.snapshot.commit + "..delivery") == "1"
    assert case.state()["status"] == "COMPLETED"
    public = case.store.coding_workflow(case.workflow_id)
    assert public["integration_reconciliation_required"] is False
    with sqlite3.connect(case.store.path) as conn:
        rows = conn.execute("SELECT attempt_id,state FROM coding_integration_intents WHERE job_id=? ORDER BY created_at",
                            (old["job_id"],)).fetchall()
    assert rows == [(old["attempt_id"], "PREPARED"), (replacement["attempt_id"], "APPLIED")]


def test_cancel_after_real_cas_crash_preserves_the_uncertain_publication_journal(tmp_path):
    case = CodingFixture(tmp_path)
    old, staged = _crash_during_publication(case, tmp_path)
    case.store.cancel_workflow(case.workflow_id, "Operator stops after controller crash")
    state = case.store.coding_workflow(case.workflow_id)
    assert state["status"] == "FAILED"
    assert state["integration_reconciliation_required"] is True
    assert any(intent["state"] == "PREPARED" and intent["result_commit"] == staged["result_commit"]
               for intent in state["integration_intents"])
    assert case.store.claim("later-controller", worker_slot="worker-one") is None
    assert git(case.repository, "rev-parse", "delivery") == staged["result_commit"]
    # Cancelling does not manufacture a database acknowledgement, erase the
    # real Git side effect, or authorize an unreviewed rollback.
    with sqlite3.connect(case.store.path) as conn:
        row = conn.execute("SELECT state,result_commit FROM coding_integration_intents WHERE job_id=? AND attempt_id=?",
                           (old["job_id"], old["attempt_id"])).fetchone()
    assert row == ("PREPARED", staged["result_commit"])


@pytest.mark.parametrize("name", [".ENV", ".CLAUDE/fake-state.json", "src/PRIVATE.PEM", "src/fake.KEY"])
def test_case_variants_of_secret_or_cache_material_are_rejected_from_real_committed_input(tmp_path, name):
    case = CodingFixture(tmp_path)
    filename = case.repository / name
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text("Synthetic boundary fixture, never a real credential\n")
    git(case.repository, "add", "--", name)
    git(case.repository, "commit", "-m", "Synthetic excluded input")
    git(case.repository, "branch", "-f", "delivery", "HEAD")
    snapshot = capture_repository(case.repository, "delivery", git_executable=case.stager.git_executable)
    assert name in snapshot.files
    with pytest.raises(ValueError, match="Credential|cache|forbidden"):
        validate_project_files(snapshot.files)


def test_workspace_capture_rejects_a_real_hardlink_to_external_material(tmp_path):
    source = tmp_path / "synthetic-private-input"
    source.write_bytes(b"Not a real secret; physical external inode fixture")
    workspace = tmp_path / "worker"
    workspace.mkdir()
    (workspace / "source.py").hardlink_to(source)
    with pytest.raises(ValueError, match="[Hh]ardlink|[Hh]ard link|link"):
        read_workspace(workspace)
    assert source.read_bytes() == b"Not a real secret; physical external inode fixture"


def _run_actual_pytest(case,node,root,selection):
    root.mkdir()
    materialize(root,case.store.coding_files(node['payload']['snapshot_sha256']))
    report=root.parent/(root.name+'-junit.xml')
    process=subprocess.run([sys.executable,'-I','-m','pytest',selection,
        '--junitxml='+str(report),'-p','no:cacheprovider','-q'],cwd=root,
        capture_output=True,text=True,timeout=30)
    assert process.returncode in (0,1),process.stdout+process.stderr
    data=report.read_bytes()
    # Executor admission/cleanup fields remain explicitly labelled protocol
    # fixtures. Only these command results and JUnit bytes are actual execution;
    # ordinary pytest here makes no Docker/Windows/RAM acceptance claim.
    evidence=case.test_evidence(node,passed=process.returncode==0)
    evidence['commands'][0].update(junit=parse_junit(data),junit_cases=junit_cases(data),
        junit_sha256=hashlib.sha256(data).hexdigest(),stdout=process.stdout,stderr=process.stderr,
        exit_code=process.returncode)
    return evidence


def test_actual_same_named_old_module_cannot_substitute_for_newly_sealed_regression(tmp_path):
    case=CodingFixture(tmp_path,same_named_baseline_test=True)
    case.author()
    node=case.claim('CODE_TEST')
    evidence=_run_actual_pytest(case,node,tmp_path/'old-only','tests/test_existing.py')
    observed=evidence['commands'][0]['junit_cases']
    assert [(item['class_name'],item['name'],item['status']) for item in observed]==[
        ('tests.test_existing','test_regression','failed')]
    case.complete_controller(node,{'passed':False,'test_receipt_sha256':digest(evidence)},evidence)
    assert case.state()['cycle']==2
    assert case.state()['sealed_tests_snapshot'] is None
    assert not any(job['kind']=='CODE_EDIT' for job in case.store.coding_workflow(case.workflow_id)['jobs'])


@pytest.mark.parametrize('shape',['module','class','class_parameterized'])
def test_actual_pytest_module_class_and_parameterized_identities_remain_accepted(tmp_path,shape):
    case=CodingFixture(tmp_path)
    case.plan()
    case.initial_research()
    author=case.claim('CODE_TEST_AUTHOR')
    before=case.store.coding_files(case.state()['current_snapshot'])
    source='from pathlib import Path\nfrom runpy import run_path\n'
    prefix=''
    arguments=''
    if shape!='module':
        if shape=='class_parameterized':
            source+='import pytest\n'
        source+='class TestAnswer:\n'
        prefix='    '
        arguments='self'
    if shape=='class_parameterized':
        source+=prefix+'@pytest.mark.parametrize("example", [0, 1])\n'
        arguments+=', example'
    source+=prefix+'def test_regression('+arguments+'):\n'
    source+=prefix+'    assert run_path(str(Path(__file__).parents[1] / "src/answer.py"))["ANSWER"] == 42\n'
    after={**before,'tests/test_regression.py':source.encode()}
    case.complete_native(author,{'requirements_traced':['REQ-1']},
        {'changes':observed_changes(before,after,before,case.project,tests_only=True),
         'snapshot_sha256':digest(manifest(after))},after)
    precode=case.claim('CODE_TEST')
    red=_run_actual_pytest(case,precode,tmp_path/'actual-red','tests/test_regression.py')
    expected_class='tests.test_regression'+('.TestAnswer' if shape!='module' else '')
    cases=red['commands'][0]['junit_cases']
    assert {item['class_name'] for item in cases}=={expected_class}
    assert {item['status'] for item in cases}=={'failed'}
    if shape=='class_parameterized':
        assert {item['name'] for item in cases}=={'test_regression[0]','test_regression[1]'}
    case.complete_controller(precode,{'passed':False,'test_receipt_sha256':digest(red)},red)
    assert case.state()['status']=='EDITING'
    case.edit()
    postedit=case.claim('CODE_TEST')
    green=_run_actual_pytest(case,postedit,tmp_path/'actual-green','tests/test_regression.py')
    assert {item['status'] for item in green['commands'][0]['junit_cases']}=={'passed'}
    case.complete_controller(postedit,{'passed':True,'test_receipt_sha256':digest(green)},green)
    assert case.state()['status']=='REVIEWING'
    assert case.state()['last_test']['passed'] is True


@pytest.mark.parametrize('shape',['dynamic','imported','ambiguous'])
def test_unsupported_or_ambiguous_planned_test_definitions_cannot_be_sealed(tmp_path,shape):
    case=CodingFixture(tmp_path)
    case.plan()
    case.initial_research()
    author=case.claim('CODE_TEST_AUTHOR')
    before=case.store.coding_files(case.state()['current_snapshot'])
    ordinary=b'def test_regression():\n    assert False\n'
    source=(b'globals()["test_regression"] = lambda: False\n' if shape=='dynamic' else
            b'from helper_tests import test_regression\n' if shape=='imported' else ordinary)
    after={**before,'tests/test_regression.py':source}
    if shape=='ambiguous':
        after['tests/test_second.py']=ordinary
    evidence={'changes':observed_changes(before,after,before,case.project,tests_only=True),
              'snapshot_sha256':digest(manifest(after))}
    with pytest.raises(ValueError,match='unambiguous static definition'):
        case.complete_native(author,{'requirements_traced':['REQ-1']},evidence,after)
    assert case.store.get(author['job_id'])['status']=='IN_PROGRESS'
    assert case.state()['current_snapshot']==digest(manifest(before))
    assert case.state()['sealed_tests_snapshot'] is None


@pytest.mark.parametrize('selected',['tests/test_existing.py','src/answer.py'])
def test_preservation_identity_is_bound_only_to_selected_registered_existing_tests(tmp_path,selected):
    case=CodingFixture(tmp_path,strategy='preserve_behavior',same_named_baseline_test=True)
    case.plan()
    case.initial_research()
    author=case.claim('CODE_TEST_AUTHOR')
    before=case.store.coding_files(case.state()['current_snapshot'])
    output={'requirements_traced':['REQ-1'],'reuse_tests':[selected]}
    evidence={'changes':[],'snapshot_sha256':digest(manifest(before))}
    if selected.startswith('src/'):
        with pytest.raises(ValueError,match='protected test paths'):
            case.complete_native(author,output,evidence,before)
        assert case.state()['sealed_tests_snapshot'] is None
    else:
        case.complete_native(author,output,evidence,before)
        identity=case.state()['test_identities']['test_regression']
        assert identity=={'path':selected,'class_name':'tests.test_existing','name':'test_regression',
                         'file_sha256':hashlib.sha256(before[selected]).hexdigest()}
