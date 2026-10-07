"""Actual Git object, worktree, hook, and CAS integration tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cochem_pipeline.coding_git import (
    GitSafetyError, GitStager, RepositorySnapshot, capture_repository,
    snapshot_digest, validate_source_path,
)


def git(repository, *args, data=None):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@localhost',
               GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@localhost')
    return subprocess.run(['git', '-C', str(repository), *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=True).stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / 'project'
    repo.mkdir()
    git(repo, 'init', '--initial-branch=main', '--template=')
    (repo / 'source.py').write_bytes(b'def answer():\n    return 41\n')
    (repo / 'asset.bin').write_bytes(b'\0\xffunchanged\x01')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Baseline')
    git(repo, 'branch', 'delivery')
    return repo


def staged(tmp_path, repository, workflow='workflow', chunk='1', branch='delivery'):
    snapshot = capture_repository(repository, branch)
    stager = GitStager(tmp_path / 'private')
    files = dict(snapshot.files, **{'source.py': b'def answer():\n    return 42\n'})
    receipt = stager.stage(snapshot, files, workflow_id=workflow, chunk_id=chunk)
    return stager, snapshot, files, receipt


def test_capture_committed_bytes_roundtrip_and_size_limits(repository):
    (repository / 'source.py').write_text('uncommitted secret\n')
    (repository / 'untracked').write_text('user work\n')
    snapshot = capture_repository(repository, 'delivery')
    assert snapshot.files['source.py'] == b'def answer():\n    return 41\n'
    assert 'untracked' not in snapshot.files
    assert snapshot.ref == 'refs/heads/delivery'
    assert RepositorySnapshot.from_dict(snapshot.as_dict(), snapshot.files) == snapshot
    with pytest.raises(GitSafetyError, match='limits'):
        capture_repository(repository, 'delivery', max_files=1)
    with pytest.raises(GitSafetyError, match='oversized'):
        capture_repository(repository, 'delivery', max_file_bytes=1)
    with pytest.raises(GitSafetyError, match='digest'):
        RepositorySnapshot.from_dict(snapshot.as_dict(), {'changed': b'x'})


def test_stage_is_deterministic_and_preserves_tree_modes_and_binary(tmp_path, repository):
    git(repository, 'update-index', '--chmod=+x', 'source.py')
    git(repository, 'commit', '-m', 'Executable')
    git(repository, 'branch', '-f', 'delivery', 'HEAD')
    stager, snapshot, files, receipt = staged(tmp_path, repository)
    assert receipt == stager.stage(snapshot, files, workflow_id='workflow', chunk_id='1')
    private = Path(receipt['staging_repository'])
    assert git(private, 'show', receipt['result_commit'] + ':source.py').endswith('return 42')
    assert git(private, 'ls-tree', receipt['result_commit'], 'source.py').startswith('100755 blob ')
    captured = capture_repository(repository, 'delivery')
    assert captured.commit == snapshot.commit
    assert snapshot_digest(files, snapshot.modes) == receipt['snapshot_sha256']
    assert git(private, 'rev-parse', receipt['result_commit'] + '^') == snapshot.commit
    patch=Path(receipt['patch_path'])
    assert patch.suffix=='.patch' and '.staging' in patch.parts
    assert patch.read_bytes().startswith(b'diff --git a/source.py b/source.py\n')
    assert hashlib.sha256(patch.read_bytes()).hexdigest()==receipt['patch_sha256']
    with pytest.raises(GitSafetyError, match='already owns'):
        stager.stage(snapshot, dict(files, extra=b'different'), workflow_id='workflow', chunk_id='1')


def test_standard_patch_applies_binary_add_delete_and_mode_changes_in_an_independent_index(tmp_path,repository):
    (repository/'drop.txt').write_text('Remove this file\n')
    git(repository,'add','drop.txt')
    git(repository,'commit','-m','File to remove')
    git(repository,'branch','-f','delivery','HEAD')
    snapshot=capture_repository(repository,'delivery')
    stager=GitStager(tmp_path/'private')
    files=dict(snapshot.files,**{'asset.bin':b'\0changed\xff\x01','new.bin':b'\0new binary\xfe'})
    files.pop('drop.txt')
    before_index=(repository/'.git/index').read_bytes()
    receipt=stager.stage(snapshot,files,workflow_id='binary',chunk_id='1',modes={'source.py':'100755'})
    patch=Path(receipt['patch_path']).read_bytes()
    assert b'GIT binary patch\n' in patch
    assert b'deleted file mode 100644\n' in patch
    assert b'old mode 100644\nnew mode 100755\n' in patch
    # This is a separate ordinary Git apply, not the stager's verifier.
    env=dict(os.environ,GIT_INDEX_FILE=str(tmp_path/'review.index'))
    for args in [('read-tree',snapshot.commit),('apply','--cached',str(receipt['patch_path']))]:
        subprocess.run(['git','-C',receipt['staging_repository'],*args],env=env,check=True,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    applied=subprocess.run(['git','-C',receipt['staging_repository'],'write-tree'],env=env,check=True,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
    assert applied==receipt['tree']
    assert (repository/'.git/index').read_bytes()==before_index
    assert stager.integrate(snapshot,receipt,auto_integrate=True)['status']=='INTEGRATED'
    assert capture_repository(repository,'delivery').files==files
    assert not list(Path(receipt['patch_path']).parent.glob('.apply-*'))


@pytest.mark.parametrize('mutation',['bytes','forged_hash','missing','other_path','wrong_hash','linked'])
def test_tampered_patch_cannot_publish_or_advance_a_branch(tmp_path,repository,mutation):
    stager,snapshot,_,receipt=staged(tmp_path,repository)
    patch=Path(receipt['patch_path'])
    if mutation in {'bytes','forged_hash'}:
        patch.write_bytes(patch.read_bytes().replace(b'+    return 42',b'+    return 99'))
        if mutation=='forged_hash': receipt['patch_sha256']=hashlib.sha256(patch.read_bytes()).hexdigest()
    elif mutation=='missing': patch.unlink()
    elif mutation=='other_path': receipt['patch_path']=str(repository/'outside.patch')
    elif mutation=='wrong_hash': receipt['patch_sha256']='0'*64
    else:
        target=tmp_path/'linked.patch'
        patch.rename(target)
        patch.symlink_to(target)
    with pytest.raises(GitSafetyError):
        stager.integrate(snapshot,receipt,auto_integrate=True)
    assert git(repository,'rev-parse','delivery')==snapshot.commit
    assert not git(repository,'for-each-ref',receipt['output_ref'])


def test_stage_replay_recovers_a_saved_patch_before_its_output_ref_was_published(tmp_path,repository):
    stager,snapshot,files,receipt=staged(tmp_path,repository)
    git(Path(receipt['staging_repository']),'update-ref','-d',receipt['output_ref'])
    assert stager.stage(snapshot,files,workflow_id='workflow',chunk_id='1')==receipt
    assert stager.integrate(snapshot,receipt,auto_integrate=True)['status']=='INTEGRATED'
    assert stager.integrate(snapshot,receipt,auto_integrate=True)['reason']=='already_integrated'


def test_stage_replay_rejects_replaced_patch_instead_of_overwriting_it(tmp_path,repository):
    stager,snapshot,files,receipt=staged(tmp_path,repository)
    patch=Path(receipt['patch_path'])
    patch.write_bytes(b'Changed after staging\n')
    with pytest.raises(GitSafetyError,match='different patch'):
        stager.stage(snapshot,files,workflow_id='workflow',chunk_id='1')
    assert patch.read_bytes()==b'Changed after staging\n'


@pytest.mark.parametrize('count',[100,101])
def test_coding_patch_limit_counts_actual_git_additions_and_deletions(tmp_path,repository,count):
    snapshot=capture_repository(repository,'delivery')
    stager=GitStager(tmp_path/'private')
    files=dict(snapshot.files,**{'added.py':b'line\n'*count})
    if count==101:
        with pytest.raises(GitSafetyError,match='changed-line limit'):
            stager.stage(snapshot,files,workflow_id='limit',chunk_id='1',max_changed_lines=100)
        assert not list((stager.state_root/'.staging').glob('**/*.patch'))
    else:
        receipt=stager.stage(snapshot,files,workflow_id='limit',chunk_id='1',max_changed_lines=100)
        assert receipt['changed_lines']==100 and receipt['max_changed_lines']==100
        assert stager.integrate(snapshot,receipt,auto_integrate=True)['status']=='INTEGRATED'


def test_coding_patch_limit_includes_deletions_and_rejects_unmeasurable_binary_changes(tmp_path,repository):
    snapshot=capture_repository(repository,'delivery')
    stager=GitStager(tmp_path/'private')
    files=dict(snapshot.files,**{'source.py':b'replacement\n'*99})
    with pytest.raises(GitSafetyError,match='changed-line limit'):
        stager.stage(snapshot,files,workflow_id='limit',chunk_id='1',max_changed_lines=100)
    with pytest.raises(GitSafetyError,match='Binary changes'):
        stager.stage(snapshot,dict(snapshot.files,**{'asset.bin':b'\0new'}),
                     workflow_id='limit',chunk_id='binary',max_changed_lines=100)


@pytest.mark.parametrize('mutation',['removed_limit','larger_limit','different_count'])
def test_patch_receipt_cannot_weaken_its_committed_line_limit(tmp_path,repository,mutation):
    snapshot=capture_repository(repository,'delivery')
    stager=GitStager(tmp_path/'private')
    receipt=stager.stage(snapshot,dict(snapshot.files,extra=b'new\n'),
                         workflow_id='limit',chunk_id='1',max_changed_lines=100)
    if mutation=='removed_limit': receipt.pop('max_changed_lines')
    elif mutation=='larger_limit': receipt['max_changed_lines']=101
    else: receipt['changed_lines']=0
    with pytest.raises(GitSafetyError): stager.integrate(snapshot,receipt,auto_integrate=True)
    assert git(repository,'rev-parse','delivery')==snapshot.commit


def test_integrate_noncheckedout_branch_preserves_dirty_user_tree_and_replays(tmp_path, repository):
    stager, snapshot, files, receipt = staged(tmp_path, repository)
    (repository / 'source.py').write_text('dirty user changes\n')
    (repository / 'untracked').write_text('keep me\n')
    before = git(repository, 'status', '--porcelain=v1')
    result = stager.integrate(snapshot, receipt, auto_integrate=True)
    assert result['status'] == 'INTEGRATED'
    assert result['reason'] == 'compare_and_swap'
    assert git(repository, 'rev-parse', 'delivery') == receipt['result_commit']
    assert git(repository, 'rev-parse', 'main') == snapshot.commit
    assert git(repository, 'show', 'delivery:source.py').endswith('return 42')
    assert git(repository, 'status', '--porcelain=v1') == before
    assert (repository / 'source.py').read_text() == 'dirty user changes\n'
    assert (repository / 'untracked').read_text() == 'keep me\n'
    assert stager.integrate(snapshot, receipt, auto_integrate=True)['reason'] == 'already_integrated'


def test_review_hold_publishes_material_commit_without_advancing_branch(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    result = stager.integrate(snapshot, receipt)
    assert result['status'] == 'READY_TO_INTEGRATE'
    assert result['reason'] == 'operator_review'
    assert git(repository, 'rev-parse', 'delivery') == snapshot.commit
    assert git(repository, 'show', receipt['output_ref'] + ':source.py').endswith('return 42')


def test_checkedout_main_and_linked_worktree_are_held(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository, branch='main')
    result = stager.integrate(snapshot, receipt, auto_integrate=True)
    assert result['status'] == 'READY_TO_INTEGRATE'
    assert result['reason'] == 'branch_checked_out'
    assert str(repository) in result['checked_out_worktrees']
    worktree = tmp_path / 'linked'
    git(repository, 'worktree', 'add', str(worktree), 'delivery')
    stager2, snapshot2, _, receipt2 = staged(tmp_path, repository, workflow='other')
    result2 = stager2.integrate(snapshot2, receipt2, auto_integrate=True)
    assert result2['reason'] == 'branch_checked_out'
    assert str(worktree) in result2['checked_out_worktrees']
    assert (worktree / 'source.py').read_bytes() == snapshot2.files['source.py']


def test_multiple_chunks_form_one_cas_chain(tmp_path, repository):
    stager, snapshot, files, first = staged(tmp_path, repository)
    stager.integrate(snapshot, first, auto_integrate=False)
    files['tests/test_answer.py'] = b'assert True\n'
    second = stager.stage(snapshot, files, workflow_id='workflow', chunk_id='2',
                          parent_commit=first['result_commit'])
    assert second['parent_commit'] == first['result_commit']
    assert stager.integrate(snapshot, second, auto_integrate=True)['status'] == 'INTEGRATED'
    assert git(repository, 'rev-parse', 'delivery^') == first['result_commit']
    assert git(repository, 'rev-parse', 'delivery^^') == snapshot.commit


def test_competing_integrations_compare_and_swap_once(tmp_path, repository):
    one = staged(tmp_path, repository, workflow='one')
    two = staged(tmp_path, repository, workflow='two')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(stager.integrate, snapshot, receipt, auto_integrate=True)
                   for stager, snapshot, _, receipt in (one, two)]
    results = [future.result() for future in futures]
    assert sorted(result['status'] for result in results) == ['INTEGRATED', 'READY_TO_INTEGRATE']
    held = next(result for result in results if result['status'] == 'READY_TO_INTEGRATE')
    assert held['reason'] == 'baseline_conflict'
    assert git(repository, 'rev-parse', 'delivery') in {one[3]['result_commit'], two[3]['result_commit']}


def test_project_hooks_filters_and_signers_are_never_executed(tmp_path, repository):
    marker = tmp_path / 'executed'
    script = tmp_path / 'bad-command'
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
    script.chmod(0o755)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    for name in ('pre-commit', 'post-commit', 'reference-transaction', 'post-checkout', 'post-merge'):
        (hooks / name).write_bytes(script.read_bytes())
        (hooks / name).chmod(0o755)
    git(repository, 'config', 'core.hooksPath', str(hooks))
    git(repository, 'config', 'core.fsmonitor', str(script))
    git(repository, 'config', 'filter.bad.clean', str(script))
    git(repository, 'config', 'filter.bad.smudge', str(script))
    git(repository, 'config', 'filter.bad.required', 'true')
    git(repository, 'config', 'gpg.program', str(script))
    git(repository, 'config', 'commit.gpgsign', 'true')
    (repository / '.git' / 'info').mkdir(exist_ok=True)
    (repository / '.git' / 'info' / 'attributes').write_text('* filter=bad\n')
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    assert stager.integrate(snapshot, receipt, auto_integrate=True)['status'] == 'INTEGRATED'
    assert not marker.exists()


@pytest.mark.parametrize('path', ['', '/root', '../escape', 'a/../b', './a', 'a//b',
                                  '.git/config', 'x/.GiT/hooks/a', 'C:/work', 'a\\b',
                                  'a\nfile', 'con', 'NUL.txt', 'x/trailing.', 'x/trailing '])
def test_reject_unsafe_source_paths(path):
    with pytest.raises(GitSafetyError):
        validate_source_path(path)


@pytest.mark.parametrize('files', [
    {'A.py': b'a', 'a.py': b'b'}, {'Dir/a.py': b'a', 'dir/b.py': b'b'},
    {'a': b'a', 'a/b': b'b'}, {'.git/config': b'bad'}, {'source.py': 'not bytes'},
])
def test_reject_invalid_candidate_trees(tmp_path, repository, files):
    snapshot = capture_repository(repository, 'delivery')
    with pytest.raises(GitSafetyError):
        GitStager(tmp_path / 'private').stage(snapshot, files, workflow_id='one', chunk_id='1')


def test_reject_symlink_and_submodule_committed_entries(tmp_path, repository):
    target = tmp_path / 'target'
    target.write_text('outside')
    os.symlink(target, repository / 'linked')
    git(repository, 'add', 'linked')
    git(repository, 'commit', '-m', 'Symlink')
    with pytest.raises(GitSafetyError, match='Symlinks'):
        capture_repository(repository, 'main')
    git(repository, 'rm', 'linked')
    git(repository, 'update-index', '--add', '--cacheinfo',
        '160000,' + git(repository, 'rev-parse', 'delivery') + ',submodule')
    git(repository, 'commit', '-m', 'Gitlink')
    with pytest.raises(GitSafetyError, match='submodules'):
        capture_repository(repository, 'main')


def test_reject_tampered_receipt_and_inherited_git_environment(tmp_path, repository):
    original_environment = dict(os.environ)
    try:
        os.environ.update(GIT_DIR='/does/not/exist', GIT_WORK_TREE='/unrelated', GIT_CONFIG_COUNT='1',
                          GIT_CONFIG_KEY_0='core.hooksPath', GIT_CONFIG_VALUE_0='/evil')
        stager, snapshot, _, receipt = staged(tmp_path, repository)
        for key, value in [('output_ref', 'refs/heads/main'), ('tree', snapshot.commit),
                           ('staging_repository', str(repository)), ('baseline_commit', '0' * 40),
                           ('parent_commit', '0' * 40), ('snapshot_sha256', '0' * 64)]:
            with pytest.raises(GitSafetyError):
                stager.integrate(snapshot, dict(receipt, **{key: value}), auto_integrate=True)
    finally:
        os.environ.clear()
        os.environ.update(original_environment)


def test_stage_recovers_interrupted_initialization_without_copying_history(tmp_path, repository):
    (repository / 'source.py').write_text('second baseline\n')
    git(repository, 'add', 'source.py')
    git(repository, 'commit', '-m', 'Second baseline')
    old_parent = git(repository, 'rev-parse', 'HEAD^')
    git(repository, 'branch', '-f', 'delivery', 'HEAD')
    snapshot = capture_repository(repository, 'delivery')
    stager = GitStager(tmp_path / 'private')
    staging = stager._staging(snapshot, 'interrupted')
    staging.mkdir()  # Simulate a process death while git init is starting.
    receipt = stager.stage(snapshot, dict(snapshot.files, extra=b'new'),
                           workflow_id='interrupted', chunk_id='1')
    result = stager.integrate(snapshot, receipt, auto_integrate=True)
    assert result['status'] == 'INTEGRATED'
    missing = subprocess.run(['git', '-C', str(staging), 'cat-file', '-e', old_parent],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert missing.returncode != 0
    assert git(repository, 'rev-parse', 'delivery^^') == old_parent


def test_crossprocess_cas_uses_shared_repository_lock(tmp_path, repository):
    import json
    tasks = []
    for index in range(2):
        stager, snapshot, _, receipt = staged(tmp_path, repository, workflow=f'process-{index}')
        payload = tmp_path / f'job-{index}.json'
        payload.write_text(json.dumps({'snapshot': snapshot.as_dict(),
                                       'files': {name: data.hex() for name, data in snapshot.files.items()},
                                       'receipt': receipt, 'state_root': str(stager.state_root)}))
        tasks.append(payload)
    script = (
        'import json,sys; from pathlib import Path; '
        'from cochem_pipeline.coding_git import GitStager,RepositorySnapshot; '
        'p=json.loads(Path(sys.argv[1]).read_text()); '
        's=RepositorySnapshot.from_dict(p["snapshot"],{k:bytes.fromhex(v) for k,v in p["files"].items()}); '
        'print(json.dumps(GitStager(Path(p["state_root"])).integrate(s,p["receipt"],auto_integrate=True)))'
    )
    processes = [subprocess.Popen([sys.executable, '-c', script, str(path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for path in tasks]
    results = []
    for process in processes:
        output, errors = process.communicate(timeout=30)
        assert process.returncode == 0, errors.decode()
        results.append(json.loads(output))
    assert sorted(result['status'] for result in results) == ['INTEGRATED', 'READY_TO_INTEGRATE']


def test_repository_root_and_staging_symlinks_are_rejected(tmp_path, repository):
    (repository / 'nested').mkdir()
    with pytest.raises(GitSafetyError, match='repository root'):
        capture_repository(repository / 'nested', 'delivery')
    alias = tmp_path / 'alias'
    alias.symlink_to(repository, target_is_directory=True)
    with pytest.raises(GitSafetyError, match='symbolic links'):
        capture_repository(alias, 'delivery')
    snapshot = capture_repository(repository, 'delivery')
    stager = GitStager(tmp_path / 'private')
    stager._staging(snapshot, 'evil').symlink_to(repository, target_is_directory=True)
    with pytest.raises(GitSafetyError, match='replaced'):
        stager.stage(snapshot, snapshot.files, workflow_id='evil', chunk_id='1')


def test_attempt_guard_rejects_cancelled_cas_after_materializing_objects(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    entered = []
    @contextmanager
    def cancelled():
        entered.append(True)
        assert git(repository, 'rev-parse', receipt['output_ref']) == receipt['result_commit']
        raise RuntimeError('Durable attempt was cancelled')
        yield
    with pytest.raises(RuntimeError, match='cancelled'):
        stager.integrate(snapshot, receipt, auto_integrate=True, cas_guard=cancelled)
    assert entered == [True]
    assert git(repository, 'rev-parse', 'delivery') == snapshot.commit


def test_attempt_guard_acknowledges_success_and_replay_only(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    acknowledgements = []
    @contextmanager
    def guard():
        yield
        assert git(repository, 'rev-parse', 'delivery') == receipt['result_commit']
        acknowledgements.append(receipt['result_commit'])
    assert stager.integrate(snapshot, receipt, auto_integrate=True, cas_guard=guard)['status'] == 'INTEGRATED'
    assert stager.integrate(snapshot, receipt, auto_integrate=True, cas_guard=guard)['reason'] == 'already_integrated'
    assert acknowledgements == [receipt['result_commit']] * 2


def test_cas_conflict_inside_attempt_guard_rolls_back_acknowledgement(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    competing = stager.stage(snapshot, dict(snapshot.files, competing=b'other'), workflow_id='other', chunk_id='1')
    stager.integrate(snapshot, competing, auto_integrate=False)
    acknowledged = []
    @contextmanager
    def racing_writer():
        git(repository, 'update-ref', 'refs/heads/delivery', competing['result_commit'], snapshot.commit)
        yield
        acknowledged.append(True)
    result = stager.integrate(snapshot, receipt, auto_integrate=True, cas_guard=racing_writer)
    assert result['status'] == 'READY_TO_INTEGRATE'
    assert result['reason'] == 'baseline_conflict'
    assert acknowledged == []


def test_deleted_target_branch_is_a_reviewable_conflict(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    git(repository, 'branch', '-D', 'delivery')
    result = stager.integrate(snapshot, receipt, auto_integrate=True)
    assert result['status'] == 'READY_TO_INTEGRATE'
    assert result['reason'] == 'baseline_conflict'
    assert result['current_commit'] is None
    assert git(repository, 'rev-parse', receipt['output_ref']) == receipt['result_commit']


def test_git_safety_failure_from_attempt_guard_is_not_swallowed(tmp_path, repository):
    stager, snapshot, _, receipt = staged(tmp_path, repository)
    @contextmanager
    def guard():
        raise GitSafetyError('Attempt guard rejected sealed evidence')
        yield
    with pytest.raises(GitSafetyError, match='sealed evidence'):
        stager.integrate(snapshot, receipt, auto_integrate=True, cas_guard=guard)
    assert git(repository, 'rev-parse', 'delivery') == snapshot.commit
