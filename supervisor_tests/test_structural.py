"""Actual SQLite corruption and isolated observer process boundaries."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
import psutil
import cochem_supervisor.detector as detector

from cochem_pipeline.heartbeat import Heartbeat
from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.store import JobStore
from cochem_supervisor.detector import MAXIMUM_OUTPUT_BYTES
from cochem_supervisor.structural import inspect_structure
from cochem_supervisor.workspace import clear_workspace


def detector_command(*arguments):
    package = Path(detector.__file__).parent
    return [sys.executable, '-I', '-S', str(package/'detector_bootstrap.py'),
            '--supervisor-package', str(package),
            '--psutil-package', str(Path(psutil.__file__).parent), *map(str,arguments)]


def readonly(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return closing(connection)


def new_board(root, *, routing=False):
    store = JobStore(root/'job_board.db', routing_policy=load_routing_policy() if routing else None)
    workflow = store.submit('Private objective must stay private', ['REQ-1'], 1)
    job = next(row for row in workflow['jobs'] if row['kind']=='MANIFEST_GENERATOR')
    return store, job


def test_inactive_owner_corruption_is_detected_without_exposing_or_changing_it(tmp_path):
    store, job = new_board(tmp_path)
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET status='FAILED',lease_owner=? WHERE job_id=?",
                           ('PRIVATE-LEASE-AUTHORITY', job['job_id']))
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    with readonly(store.path) as connection:
        observed = inspect_structure(connection,time.time())
        assert observed['violations'] == [{'invariant':'inactive_lease_owner','count':1,
            'summary':'Inactive jobs retain execution lease ownership'}]
        assert 'PRIVATE' not in json.dumps(observed)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('UPDATE pipeline_jobs SET lease_owner=NULL')
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    assert store.get(job['job_id'])['lease_owner'] == 'PRIVATE-LEASE-AUTHORITY'


def test_pending_failure_budget_is_structural_but_availability_dispatches_are_not(tmp_path):
    store, job = new_board(tmp_path,routing=True)
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET status='PENDING_RETRY',attempts=99 WHERE job_id=?", (job['job_id'],))
        connection.execute("UPDATE pipeline_routing_jobs SET state='WAITING',failure_count=0,next_eligible_at=? WHERE job_id=?",
                           (time.time()+600, job['job_id']))
    with readonly(store.path) as connection:
        assert inspect_structure(connection,time.time())['violations'] == []
    with store._write() as connection:
        connection.execute('UPDATE pipeline_routing_jobs SET failure_count=3 WHERE job_id=?', (job['job_id'],))
    with readonly(store.path) as connection:
        result = inspect_structure(connection,time.time())
    assert result['violations'][0]['invariant'] == 'pending_exhausted_failure_budget'
    assert result['violations'][0]['count'] == 1


def test_failed_records_without_illegal_lease_do_not_imply_structural_corruption(tmp_path):
    store, job = new_board(tmp_path)
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET status='FAILED',error='Authentication unavailable',attempts=3 WHERE job_id=?",
                           (job['job_id'],))
    with readonly(store.path) as connection:
        assert inspect_structure(connection,time.time())['violations'] == []


def test_structural_inspection_requires_query_only_connection(tmp_path):
    store, _ = new_board(tmp_path)
    with closing(sqlite3.connect(store.path)) as connection:
        with pytest.raises(ValueError,match='query-only'):
            inspect_structure(connection,time.time())


def test_real_isolated_detector_reports_structure_without_application_imports(tmp_path):
    store, job = new_board(tmp_path)
    Heartbeat(tmp_path,'observer-contract').completed_tick({'hardware':{'capacity':4},'active_count':0})
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET status='FAILED',lease_owner=? WHERE job_id=?",
                           ('PRIVATE-OWNER',job['job_id']))
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    history = tmp_path/'process-history.json'
    result = subprocess.run(detector_command('--private-root',tmp_path,'--process-history',history),
        capture_output=True,check=True,timeout=5,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
    assert len(result.stdout) <= MAXIMUM_OUTPUT_BYTES
    value = json.loads(result.stdout)
    assert value['import_audit']['sterile'] is True
    assert value['import_audit']['forbidden_modules'] == []
    assert value['import_audit']['external_roots'] == ['psutil']
    assert value['import_audit']['isolated'] is True
    assert value['import_audit']['site_disabled'] is True
    assert value['observation']['health']['structural_integrity']['state'] == 'violated'
    assert value['observation']['health']['process_resources']['state'] == 'observed'
    assert history.is_file()
    assert 'PRIVATE-OWNER' not in result.stdout.decode()
    assert 'Private objective' not in result.stdout.decode()
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before


def test_real_detector_import_guard_blocks_application_execution(tmp_path):
    program = '''import importlib,json,runpy,sys
from pathlib import Path
bootstrap=runpy.run_path(sys.argv[1])
bootstrap['configure_packages'](Path(sys.argv[2]),Path(sys.argv[3]))
from cochem_supervisor.detector import install_import_guard,import_audit
install_import_guard()
denied=[]
for name in ('cochem_pipeline','cochem_mcp','cochem'):
    try:
        importlib.import_module(name)
    except ImportError:
        denied.append(name)
print(json.dumps({'denied':denied,'audit':import_audit()}))
'''
    package = Path(detector.__file__).parent
    result = subprocess.run([sys.executable,'-I','-S','-c',program,
        str(package/'detector_bootstrap.py'),str(package),str(Path(psutil.__file__).parent)],capture_output=True,
        check=True,timeout=5,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
    value = json.loads(result.stdout)
    assert value['denied'] == ['cochem_pipeline','cochem_mcp','cochem']
    assert value['audit']['sterile'] is True and not value['audit']['forbidden_modules']


def test_detector_reports_missing_heartbeat_without_requiring_a_process_id(tmp_path):
    root = tmp_path/'missing'
    history = tmp_path/'process-history.json'
    result = subprocess.run(detector_command('--private-root',root,'--process-history',history),capture_output=True,
        check=True,timeout=5,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
    value = json.loads(result.stdout)
    assert value['import_audit']['sterile'] is True
    assert value['observation']['health']['heartbeat'] == 'missing'
    assert any(item['summary']=='The pipeline has not published a supervisor heartbeat'
               for item in value['observation']['incidents'])
    assert not root.exists() and not history.exists()


def test_detector_disables_site_startup_and_rejects_nonisolated_launch(tmp_path):
    startup = tmp_path/'sitecustomize.py'
    marker = tmp_path/'executed-startup.txt'
    startup.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")',encoding='utf-8')
    environment = {**os.environ,'PYTHONPATH':str(tmp_path),'PYTHONSTARTUP':str(startup)}
    result = subprocess.run(detector_command('--private-root',tmp_path/'absent'),
        env=environment,capture_output=True,check=True,timeout=5)
    audit = json.loads(result.stdout)['import_audit']
    assert audit['isolated'] and audit['site_disabled'] and audit['sterile']
    assert '_virtualenv' not in audit['external_roots']
    assert not marker.exists()
    command = detector_command('--private-root',tmp_path/'absent')
    command.remove('-S')
    rejected = subprocess.run(command,env=environment,capture_output=True,timeout=5)
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout) == {'error_type':'RuntimeError'}


def test_supervisor_workspace_cleanup_never_follows_sibling_links(tmp_path):
    workspace, sibling = tmp_path/'workspace',tmp_path/'sibling'
    workspace.mkdir(); sibling.mkdir()
    secret = sibling/'preserve.txt'
    secret.write_text('Preserve unrelated work',encoding='utf-8')
    (workspace/'redirect').symlink_to(sibling,target_is_directory=True)
    (workspace/'scratch').mkdir()
    (workspace/'scratch'/'temporary.txt').write_text('owned scratch',encoding='utf-8')
    clear_workspace(workspace)
    assert not list(workspace.iterdir())
    assert secret.read_text(encoding='utf-8') == 'Preserve unrelated work'
