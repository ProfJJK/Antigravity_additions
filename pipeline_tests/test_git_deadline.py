"""Real Git and process-lock checks for operation-wide wall-clock bounds."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import threading
import time

import pytest

from cochem_pipeline.coding_git import GitSafetyError, GitStager, capture_repository
from pipeline_tests.test_coding_git import git, repository  # Actual Git fixture, no substituted implementation.


@pytest.mark.parametrize('timeout', [False, True, 0, -1, float('inf'), float('nan'), 601, '600'])
def test_git_deadline_requires_a_finite_controller_bound(tmp_path, timeout):
    with pytest.raises(GitSafetyError, match='timeout'):
        GitStager(tmp_path / 'private', timeout_seconds=timeout)


def test_expired_stage_budget_releases_context_and_preserves_project(tmp_path, repository):
    snapshot = capture_repository(repository, 'delivery')
    tiny = GitStager(tmp_path / 'private', timeout_seconds=1e-12)
    files = dict(snapshot.files, extra=b'bounded commit')
    with pytest.raises(GitSafetyError, match='deadline'):
        tiny.stage(snapshot, files, workflow_id='bounded', chunk_id='one')
    assert git(repository, 'rev-parse', 'delivery') == snapshot.commit
    healthy = GitStager(tmp_path / 'private', timeout_seconds=30)
    receipt = healthy.stage(snapshot, files, workflow_id='bounded', chunk_id='one')
    assert healthy.integrate(snapshot, receipt, auto_integrate=True)['status'] == 'INTEGRATED'


def test_parallel_git_operations_do_not_share_deadlines(tmp_path, repository):
    snapshot = capture_repository(repository, 'delivery')
    gate = threading.Barrier(2)
    def run(index, timeout):
        stager = GitStager(tmp_path / f'private-{index}', timeout_seconds=timeout)
        gate.wait(timeout=10)
        try:
            return stager.stage(snapshot, dict(snapshot.files, extra=b'bounded'),
                                workflow_id=f'workflow-{index}', chunk_id='one')['status']
        except GitSafetyError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        short = pool.submit(run, 0, 1e-12)
        healthy = pool.submit(run, 1, 30)
    assert 'deadline' in short.result()
    assert healthy.result() == 'STAGED'


def test_real_crossprocess_repository_lock_is_bounded_by_integration_deadline(tmp_path, repository):
    snapshot = capture_repository(repository, 'delivery')
    state = tmp_path / 'private'
    stager = GitStager(state, timeout_seconds=30)
    receipt = stager.stage(snapshot, dict(snapshot.files, extra=b'bounded'),
                            workflow_id='locked', chunk_id='one')
    lock_path = repository / '.git' / 'cochem-integration.lock'
    script = '''import os,sys
stream=open(sys.argv[1],'a+b')
if os.name=='nt':
 import msvcrt
 stream.write(b'0');stream.flush();stream.seek(0)
 msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
else:
 import fcntl
 fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
print('LOCKED',flush=True)
sys.stdin.read(1)
'''
    holder = subprocess.Popen([sys.executable, '-c', script, str(lock_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert holder.stdout.readline().strip() == b'LOCKED'
        bounded = GitStager(state, timeout_seconds=.3)
        started = time.monotonic()
        with pytest.raises(GitSafetyError, match='deadline'):
            bounded.integrate(snapshot, receipt, auto_integrate=True)
        elapsed = time.monotonic() - started
        assert elapsed < 3, f'Operation ignored its deadline while waiting for a physical lock: {elapsed}'
        assert git(repository, 'rev-parse', 'delivery') == snapshot.commit
    finally:
        holder.communicate(input=b'x', timeout=10)
    assert holder.returncode == 0
    assert stager.integrate(snapshot, receipt, auto_integrate=True)['status'] == 'INTEGRATED'
