"""Real subprocess + SQLite checks; these do not assert Windows ACL isolation."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from cochem_supervisor.blackbox import BlackboxRejected,run_blackbox,_rows
from cochem_supervisor.releases import tree_manifest


def layout(tmp_path):
    candidate=tmp_path/'candidate'; candidate.mkdir()
    source=Path(__file__).resolve().parents[1]/'src'
    shutil.copytree(source,candidate/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    private=tmp_path/'private'; private.mkdir()
    return candidate,tmp_path/'worker',private/'verdict.json'


def invoke(arguments,request,logs,timeout):
    logs.mkdir()
    stdout,stderr=logs/'stdout',logs/'stderr'
    with stdout.open('w') as out,stderr.open('w') as err:
        process=subprocess.Popen([sys.executable,'-I','-m','cochem_supervisor.blackbox_child',*arguments],
            stdin=subprocess.PIPE,stdout=out,stderr=err,text=True)
        process.communicate(json.dumps(request),timeout=timeout)
    # This portable driver only runs known physical fixtures, not arbitrary
    # Windows workers. Production cleanup receipts come from native Job Objects.
    return {'pid':process.pid,'exit_code':process.returncode,'cleanup_verified':True,
            'stdout_path':str(stdout),'stderr_path':str(stderr),'test_driver':'portable process fixture'}


def test_external_assertions_read_actual_candidate_sqlite_and_artifact_bytes(tmp_path):
    candidate,workspace,report=layout(tmp_path)
    before=tree_manifest(candidate)
    result=run_blackbox(candidate,workspace,report,invoke)
    assert result['passed'] and len(result['checks'])==4
    assert len(result['executions'])>=15
    assert json.loads(report.read_text())==result
    assert (workspace/'contract.db').is_file()
    assert tree_manifest(candidate)==before


@pytest.mark.parametrize('payload',[
    # The original exploit writes XML and exits before any protected pytest ran.
    "import os,sys\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index('--report')+1])\np.write_text('<testsuite tests=\"200\"/>')\nos._exit(0)\n",
    "import os\nprint('{\"passed\":true}',flush=True)\nos._exit(0)\n",
    "import os\nprint('{\"result\":true}',flush=True)\nos._exit(0)\n",
])
def test_candidate_bootstrap_cannot_replace_parent_verdict_with_forged_pass(tmp_path,payload):
    candidate,workspace,report=layout(tmp_path)
    (candidate/'src/cochem_pipeline/__init__.py').write_text(payload)
    with pytest.raises(BlackboxRejected):
        run_blackbox(candidate,workspace,report,invoke)
    assert json.loads(report.read_text())['passed'] is False


def test_broken_candidate_fencing_fails_physical_state_check(tmp_path):
    candidate,workspace,report=layout(tmp_path)
    path=candidate/'src/cochem_pipeline/store.py'
    text=path.read_text()
    text=text.replace('if not self._owned(job, attempt_id, fencing_token):','if False:',1)
    path.write_text(text)
    with pytest.raises(BlackboxRejected):
        run_blackbox(candidate,workspace,report,invoke)
    assert json.loads(report.read_text())['passed'] is False


def test_unverified_process_cleanup_blocks_before_any_physical_acceptance(tmp_path):
    candidate,workspace,report=layout(tmp_path)
    def unverified(*args):
        result=invoke(*args); result['cleanup_verified']=False; return result
    with pytest.raises(BlackboxRejected,match='cleanup'):
        run_blackbox(candidate,workspace,report,unverified)
    assert not json.loads(report.read_text())['checks']


def test_hostile_sqlite_view_cannot_allocate_a_giant_blob_in_trusted_parent(tmp_path):
    path=tmp_path/'hostile.db'
    with sqlite3.connect(path) as db:
        for name in ('pipeline_worker_ownership','pipeline_artifacts','pipeline_route_reservations','coding_workflows'):
            db.execute('CREATE TABLE '+name+'(value TEXT)')
        db.execute('CREATE VIEW pipeline_jobs AS SELECT zeroblob(2147483647) AS payload')
    with pytest.raises(BlackboxRejected,match='ordinary physical tables'):
        _rows(path,'SELECT * FROM pipeline_jobs')


def test_sqlite_expression_length_limit_precedes_blob_allocation(tmp_path):
    path=tmp_path/'hostile.db'
    with sqlite3.connect(path) as db:
        for name in ('pipeline_jobs','pipeline_worker_ownership','pipeline_artifacts','pipeline_route_reservations','coding_workflows'):
            db.execute('CREATE TABLE '+name+'(value TEXT)')
    with pytest.raises(sqlite3.DataError,match='too big'):
        _rows(path,'SELECT zeroblob(2147483647) FROM pipeline_jobs UNION ALL SELECT zeroblob(2147483647)')
