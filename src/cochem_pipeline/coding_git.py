"""Controller-owned Git objects and compare-and-swap project integration.

Workers receive file bytes, never this repository or its Git directory.  No
checkout, add, filter, hook, or project command is used to construct commits.
The caller must authorize the staged bytes using its independent test/review
receipts before calling :meth:`GitStager.integrate`.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
import time
from typing import Mapping


class GitSafetyError(ValueError):
    """A repository or staged object cannot be used safely."""


class _IntegrationHold(Exception):
    def __init__(self, reason, current_commit, checked_out=None):
        self.reason, self.current_commit, self.checked_out = reason, current_commit, checked_out


class _CasFailure(Exception):
    """A Git ref CAS failed after admission, distinct from a guard failure."""


_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DOS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?\Z", re.I)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_GIT_EXECUTABLE = ContextVar('coding_git_executable', default=None)
_GIT_DEADLINE = ContextVar('coding_git_deadline', default=None)


def _remaining_timeout(requested=120):
    deadline = _GIT_DEADLINE.get()
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GitSafetyError('Git operation exceeded its bounded wall-clock deadline')
    return min(requested, remaining)


def _check_deadline():
    _remaining_timeout()


def _reject_links(path: Path, *, missing_ok=False):
    for current in (path, *path.parents):
        _check_deadline()
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing_ok:
                continue
            raise GitSafetyError('A required Git path is missing')
        if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise GitSafetyError('Git paths must not traverse symbolic links, junctions, or reparse points')


def _metadata(repository: Path):
    """Reject redirected Git metadata before plumbing touches its contents."""
    _reject_links(repository)
    for current, directories, files in os.walk(repository, followlinks=False):
        _check_deadline()
        for name in (*directories, *files):
            _reject_links(Path(current) / name)


def _trusted_git_executable(executable=None):
    if os.name != 'nt':
        return str(executable or 'git')
    if not isinstance(executable, (str, Path)) or not Path(executable).is_absolute():
        raise GitSafetyError('Windows coding requires an explicit protected absolute Git executable')
    path = Path(executable)
    _reject_links(path)
    if not path.is_file() or path.suffix.casefold() != '.exe':
        raise GitSafetyError('Windows coding requires a native git.exe file')
    from .windows import validate_code_path
    validate_code_path(path)
    return str(path)


def _using_stager_git(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        token = _GIT_EXECUTABLE.set(self.git_executable)
        outer = _GIT_DEADLINE.get()
        deadline = time.monotonic() + self.timeout_seconds
        deadline_token = _GIT_DEADLINE.set(min(outer, deadline) if outer is not None else deadline)
        try:
            _check_deadline()
            result = function(self, *args, **kwargs)
            _check_deadline()
            return result
        finally:
            _GIT_DEADLINE.reset(deadline_token)
            _GIT_EXECUTABLE.reset(token)
    return wrapped


def _using_capture_git(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _GIT_EXECUTABLE.set(_trusted_git_executable(kwargs.get('git_executable')))
        try:
            return function(*args, **kwargs)
        finally:
            _GIT_EXECUTABLE.reset(token)
    return wrapped


def _environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1',
               GIT_AUTHOR_NAME='CoChem Controller', GIT_AUTHOR_EMAIL='controller@localhost',
               GIT_COMMITTER_NAME='CoChem Controller', GIT_COMMITTER_EMAIL='controller@localhost',
               LC_ALL='C')
    return env


def _git(repository: Path, *args: str, data: bytes | None = None,
         env_extra: Mapping[str, str] | None = None, timeout: int = 120) -> bytes:
    env = _environment()
    env.update(env_extra or {})
    executable = _GIT_EXECUTABLE.get() or _trusted_git_executable()
    command = [executable, '--no-replace-objects', '-C', str(repository),
               '-c', f'core.hooksPath={os.devnull}', '-c', 'core.fsmonitor=false',
               '-c', f'core.attributesFile={os.devnull}', '-c', 'core.pager=cat',
               '-c', 'core.quotePath=false', '-c', 'protocol.allow=never',
               '-c', 'submodule.recurse=false', '-c', 'maintenance.auto=false',
               '-c', 'gc.auto=0', '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
               '-c', 'pack.threads=1', '-c', 'pack.compression=1',
               '-c', 'core.useReplaceRefs=false', *args]
    try:
        completed = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env,
                                   timeout=_remaining_timeout(timeout), check=False)
    except subprocess.TimeoutExpired as error:
        raise GitSafetyError('Git command exceeded its bounded wall-clock deadline') from error
    _check_deadline()
    if completed.returncode:
        # Errors contain paths, but never provider profiles or credentials.
        raise GitSafetyError(f'Git {args[0] if args else "command"} failed: '
                             + completed.stderr.decode('utf-8', 'replace')[-4096:].strip())
    return completed.stdout


def _oid(value: str) -> str:
    if not isinstance(value, str) or not _OID.fullmatch(value):
        raise GitSafetyError('Expected a complete Git object ID')
    return value


def validate_source_path(value: str) -> str:
    """Require a portable relative regular-file path for Windows and Linux."""
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 4096:
        raise GitSafetyError('Source path must be a bounded relative UTF-8 path')
    if any(char in value for char in '\\:"*<>?|') or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise GitSafetyError('Source path contains unsafe characters')
    parts = value.split('/')
    if PurePosixPath(value).is_absolute() or any(
            not part or part in ('.', '..') or part.casefold() == '.git'
            or part.endswith(('.', ' ')) or _DOS_DEVICE.fullmatch(part) for part in parts):
        raise GitSafetyError('Source path is not portable or escapes its staging tree')
    return value


def _validate_files(files: Mapping[str, bytes], *, max_files=10000,
                    max_bytes=67108864, max_file_bytes=8388608) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or len(files) > max_files:
        raise GitSafetyError('Source tree exceeds its file limit')
    normalized: dict[str, str] = {}
    total = 0
    result = {}
    for path, content in files.items():
        _check_deadline()
        validate_source_path(path)
        if not isinstance(content, bytes) or len(content) > max_file_bytes:
            raise GitSafetyError('Source files must be bounded byte strings')
        key = path.casefold()
        if key in normalized:
            raise GitSafetyError('Source paths collide on Windows')
        normalized[key] = path
        result[path] = content
        total += len(content)
    if total > max_bytes:
        raise GitSafetyError('Source tree exceeds its byte limit')
    for key in normalized:
        parts = key.split('/')
        if any('/'.join(parts[:index]) in normalized for index in range(1, len(parts))):
            raise GitSafetyError('Source file conflicts with a directory')
    # Directory spellings must also agree on a case-insensitive filesystem.
    directories: dict[str, str] = {}
    for path in result:
        parts = path.split('/')
        for index in range(1, len(parts)):
            directory = '/'.join(parts[:index])
            if directories.setdefault(directory.casefold(), directory) != directory:
                raise GitSafetyError('Source directory spellings collide on Windows')
    return result


def snapshot_digest(files: Mapping[str, bytes], modes: Mapping[str, str] | None = None) -> str:
    manifest = [{'path': path, 'mode': (modes or {}).get(path, '100644'),
                 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                for path, content in sorted(files.items())]
    return hashlib.sha256(json.dumps(manifest, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class RepositorySnapshot:
    repository: str
    branch: str
    ref: str
    commit: str
    files: dict[str, bytes]
    modes: dict[str, str]
    blobs: dict[str, str]
    manifest_sha256: str
    object_format: str = 'sha1'

    def as_dict(self) -> dict:
        """Metadata for durable storage; file bytes belong in the blob store."""
        return {'repository': self.repository, 'branch': self.branch, 'ref': self.ref,
                'commit': self.commit, 'modes': dict(self.modes), 'blobs': dict(self.blobs),
                'manifest_sha256': self.manifest_sha256, 'object_format': self.object_format}

    @classmethod
    def from_dict(cls, value: Mapping, files: Mapping[str, bytes]) -> 'RepositorySnapshot':
        snapshot = cls(files=dict(files), **dict(value))
        if snapshot_digest(snapshot.files, snapshot.modes) != snapshot.manifest_sha256:
            raise GitSafetyError('Stored repository snapshot digest does not match its bytes')
        return snapshot


def _repository(repository: Path) -> Path:
    repository = Path(os.path.normpath(Path(repository).absolute()))
    _reject_links(repository)
    if not repository.is_dir():
        raise GitSafetyError('Registered repository is not a directory')
    git_entry = repository / '.git'
    if git_entry.exists() or git_entry.is_symlink():
        _reject_links(git_entry)
    # GIT_DIR and discovery inherited from the service environment are ignored.
    _git(repository, 'rev-parse', '--git-dir')
    bare = _git(repository, 'rev-parse', '--is-bare-repository').decode().strip() == 'true'
    root = _git(repository, 'rev-parse', '--absolute-git-dir' if bare else '--show-toplevel')
    if os.path.normcase(os.path.normpath(os.fsdecode(root).strip())) != os.path.normcase(str(repository)):
        raise GitSafetyError('Project must register the repository root, not a nested directory')
    for argument in ('--absolute-git-dir', '--git-common-dir'):
        metadata_root = Path(os.fsdecode(_git(repository, 'rev-parse', '--path-format=absolute', argument)).strip())
        _metadata(metadata_root)
    return repository


def _validate_snapshot(snapshot: RepositorySnapshot):
    _validate_files(snapshot.files)
    _oid(snapshot.commit)
    if snapshot.object_format not in ('sha1', 'sha256'):
        raise GitSafetyError('Unsupported Git object format')
    if snapshot.ref != 'refs/heads/' + snapshot.branch or snapshot.branch.startswith('-'):
        raise GitSafetyError('Snapshot does not name the registered local branch')
    if set(snapshot.modes) != set(snapshot.files) or set(snapshot.blobs) != set(snapshot.files):
        raise GitSafetyError('Snapshot file metadata is incomplete')
    for path, content in snapshot.files.items():
        _check_deadline()
        if snapshot.modes[path] not in ('100644', '100755'):
            raise GitSafetyError('Snapshot contains a non-regular file')
        digest = hashlib.new(snapshot.object_format, f'blob {len(content)}\0'.encode() + content).hexdigest()
        if snapshot.blobs[path] != digest:
            raise GitSafetyError('Snapshot blob identity does not match its bytes')
    if snapshot_digest(snapshot.files, snapshot.modes) != snapshot.manifest_sha256:
        raise GitSafetyError('Repository baseline bytes no longer match their receipt')


@_using_capture_git
def capture_repository(repository: Path, branch: str, *, max_files=10000,
                       max_bytes=67108864, max_file_bytes=8388608,
                       git_executable=None) -> RepositorySnapshot:
    """Read one immutable committed tree; never read uncommitted user files."""
    repository = _repository(repository)
    if not isinstance(branch, str) or not branch or branch.startswith('-'):
        raise GitSafetyError('Project branch must name a local branch')
    ref = branch if branch.startswith('refs/heads/') else 'refs/heads/' + branch
    if not ref.startswith('refs/heads/') or ref == 'refs/heads/':
        raise GitSafetyError('Only local project branches can be integrated')
    _git(repository, 'check-ref-format', ref)
    commit = _oid(_git(repository, 'rev-parse', '--verify', ref + '^{commit}').decode().strip())
    object_format = _git(repository, 'rev-parse', '--show-object-format').decode().strip()
    if object_format not in ('sha1', 'sha256'):
        raise GitSafetyError('Unsupported Git object format')
    listing = _git(repository, 'ls-tree', '-r', '-l', '-z', '--full-tree', commit)
    entries = []
    total = 0
    for record in listing.split(b'\0'):
        if not record:
            continue
        header, raw_path = record.split(b'\t', 1)
        mode, kind, blob, size = header.split()
        try:
            path = raw_path.decode('utf-8', 'strict')
        except UnicodeDecodeError as exc:
            raise GitSafetyError('Source paths must be UTF-8') from exc
        validate_source_path(path)
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise GitSafetyError('Symlinks, submodules, and special Git entries are forbidden')
        size_int = int(size)
        if size_int > max_file_bytes:
            raise GitSafetyError('Source tree contains an oversized file')
        total += size_int
        entries.append((path, mode.decode(), _oid(blob.decode()), size_int))
        if len(entries) > max_files or total > max_bytes:
            raise GitSafetyError('Source tree exceeds its configured limits')
    contents = _git(repository, 'cat-file', '--batch',
                    data=b''.join(blob.encode() + b'\n' for _, _, blob, _ in entries))
    files, modes, blobs = {}, {}, {}
    offset = 0
    for path, mode, blob, size in entries:
        newline = contents.find(b'\n', offset)
        expected = f'{blob} blob {size}'.encode()
        if newline < 0 or contents[offset:newline] != expected:
            raise GitSafetyError('Git object batch does not match the captured tree')
        offset = newline + 1
        files[path] = contents[offset:offset + size]
        if len(files[path]) != size or contents[offset + size:offset + size + 1] != b'\n':
            raise GitSafetyError('Truncated Git object batch')
        offset += size + 1
        modes[path], blobs[path] = mode, blob
    if offset != len(contents):
        raise GitSafetyError('Unexpected bytes in Git object batch')
    _validate_files(files, max_files=max_files, max_bytes=max_bytes, max_file_bytes=max_file_bytes)
    return RepositorySnapshot(str(repository), ref.removeprefix('refs/heads/'), ref,
                              commit, files, modes, blobs, snapshot_digest(files, modes), object_format)


@contextmanager
def _exclusive_lock(path: Path):
    """A persistent lock file, with both thread and native process locking."""
    key = str(path.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    while not lock.acquire(timeout=_remaining_timeout(.1)):
        _check_deadline()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _reject_links(path, missing_ok=True)
        with path.open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
            else:
                import fcntl
            while True:
                _check_deadline()
                try:
                    if os.name == 'nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EBUSY):
                        raise
                    time.sleep(_remaining_timeout(.02))
            try:
                _check_deadline()
                yield
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


def _copy_objects(source: Path, destination: Path, object_ids: list[str]):
    for value in object_ids:
        _check_deadline()
        _oid(value)
    pack = _git(source, 'pack-objects', '--stdout', '--window=0',
                data=''.join(value + '\n' for value in sorted(set(object_ids))).encode())
    _git(destination, 'index-pack', '--stdin', '--fix-thin', data=pack)


def _tree_objects(repository: Path, commit: str) -> list[str]:
    values = _git(repository, 'rev-list', '--objects', '--no-object-names', commit + '^{tree}')
    return [_oid(value) for value in values.decode().splitlines()]


def _checked_out(repository: Path, ref: str) -> list[str]:
    result = []
    path = ''
    for field in _git(repository, 'worktree', 'list', '--porcelain', '-z').split(b'\0'):
        if field.startswith(b'worktree '):
            path = os.fsdecode(field[len(b'worktree '):])
        elif field == b'branch ' + ref.encode():
            result.append(path)
    return result


def _current_commit(repository: Path, ref: str) -> str | None:
    listing = _git(repository, 'for-each-ref', '--format=%(objectname)%00%(refname)', ref)
    for line in listing.decode('utf-8').splitlines():
        object_id, name = line.split('\0', 1)
        if name == ref:
            return _oid(object_id)
    return None


class GitStager:
    """Own private staged commits and integrate only with a guarded ref CAS."""

    def __init__(self, state_root: Path, *, git_executable=None, timeout_seconds=600):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 600):
            raise GitSafetyError('Git operation timeout must be finite, positive, and at most 600 seconds')
        self.timeout_seconds = float(timeout_seconds)
        self.state_root = Path(state_root).absolute()
        self.git_executable = _trusted_git_executable(git_executable)
        _reject_links(self.state_root, missing_ok=True)
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _staging(self, snapshot: RepositorySnapshot, workflow_id: str) -> Path:
        if not isinstance(workflow_id, str) or not workflow_id or len(workflow_id) > 1024:
            raise GitSafetyError('Workflow ID must be a bounded string')
        identity = hashlib.sha256((snapshot.repository + '\0' + workflow_id).encode()).hexdigest()
        return self.state_root / (identity + '.git')

    @_using_stager_git
    def stage(self, snapshot: RepositorySnapshot, files: Mapping[str, bytes], *,
              workflow_id: str, chunk_id: str, parent_commit: str | None = None,
              modes: Mapping[str, str] | None = None, message: str = 'Verified coding chunk') -> dict:
        """Create an immutable commit in private storage, without changing the project."""
        files = _validate_files(files)
        if not isinstance(chunk_id, str) or not chunk_id or len(chunk_id) > 1024:
            raise GitSafetyError('Chunk ID must be a bounded string')
        if not isinstance(message, str) or len(message.encode()) > 16384 or '\0' in message:
            raise GitSafetyError('Commit message must be bounded text')
        repository = _repository(Path(snapshot.repository))
        _validate_snapshot(snapshot)
        _git(repository, 'check-ref-format', snapshot.ref)
        selected_modes = {path: (modes or {}).get(path, snapshot.modes.get(path, '100644')) for path in files}
        if any(value not in ('100644', '100755') for value in selected_modes.values()):
            raise GitSafetyError('Only regular-file modes can be staged')
        staging = self._staging(snapshot, workflow_id)
        with _exclusive_lock(self.state_root / (staging.name + '.lock')):
            if staging.is_symlink() or (staging.exists() and not staging.is_dir()):
                raise GitSafetyError('Staging repository was replaced')
            _reject_links(staging, missing_ok=True)
            if staging.exists():
                _metadata(staging)
            # init is idempotent and also repairs a crash during its first run.
            _git(self.state_root, 'init', '--bare', '--template=',
                 '--object-format=' + snapshot.object_format, str(staging))
            if _git(staging, 'rev-parse', '--show-object-format').decode().strip() != snapshot.object_format:
                raise GitSafetyError('Staging repository has a different object format')
            try:
                _git(staging, 'cat-file', '-e', snapshot.commit + '^{commit}')
            except GitSafetyError:
                # A crash after init or during index-pack is safely replayable.
                _copy_objects(repository, staging, [snapshot.commit, *_tree_objects(repository, snapshot.commit)])
            # Baseline ancestry is intentionally absent: never copy project history.
            shallow = staging / 'shallow'
            if not shallow.exists():
                pending = staging / 'shallow.cochem-new'
                pending.write_text(snapshot.commit + '\n', encoding='ascii')
                pending.replace(shallow)
            elif shallow.read_text(encoding='ascii') != snapshot.commit + '\n':
                raise GitSafetyError('Staging repository belongs to a different baseline')
            parent = _oid(parent_commit or snapshot.commit)
            _git(staging, 'cat-file', '-e', parent + '^{commit}')
            if parent != snapshot.commit:
                _git(staging, 'merge-base', '--is-ancestor', snapshot.commit, parent)
            hierarchy: dict = {}
            for path, content in sorted(files.items()):
                _check_deadline()
                if content == snapshot.files.get(path):
                    blob = snapshot.blobs[path]
                else:
                    blob = _oid(_git(staging, 'hash-object', '-w', '--stdin', data=content).decode().strip())
                level = hierarchy
                parts = path.split('/')
                for part in parts[:-1]:
                    level = level.setdefault(part, {})
                level[parts[-1]] = (selected_modes[path], blob)

            def write_tree(level):
                records = []
                for name, value in sorted(level.items()):
                    _check_deadline()
                    if isinstance(value, dict):
                        mode, kind, object_id = '040000', 'tree', write_tree(value)
                    else:
                        mode, object_id = value
                        kind = 'blob'
                    records.append(f'{mode} {kind} {object_id}\t'.encode() + name.encode() + b'\0')
                return _oid(_git(staging, 'mktree', '-z', data=b''.join(records)).decode().strip())

            tree = write_tree(hierarchy)
            digest = snapshot_digest(files, selected_modes)
            parent_time = int(_git(staging, 'show', '-s', '--format=%ct', parent).decode().strip())
            date = f'@{parent_time + 1} +0000'
            body = f'{message}\n\nWorkflow: {workflow_id}\nChunk: {chunk_id}\nSnapshot-SHA256: {digest}\n'
            result = _oid(_git(staging, 'commit-tree', tree, '-p', parent, data=body.encode(),
                               env_extra={'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date}).decode().strip())
            workflow_hash = hashlib.sha256(workflow_id.encode()).hexdigest()
            chunk_hash = hashlib.sha256(chunk_id.encode()).hexdigest()
            output_ref = f'refs/cochem/coding/{workflow_hash}/{chunk_hash}'
            try:
                prior = _git(staging, 'rev-parse', '--verify', output_ref).decode().strip()
            except GitSafetyError:
                prior = '0' * len(result)
            if prior not in ('0' * len(result), result):
                raise GitSafetyError('A different commit already owns this workflow chunk')
            _git(staging, 'update-ref', output_ref, result, prior)
            return {'baseline_commit': snapshot.commit, 'parent_commit': parent,
                    'result_commit': result, 'tree': tree, 'snapshot_sha256': digest,
                    'staging_repository': str(staging), 'output_ref': output_ref,
                    'workflow_id': workflow_id, 'chunk_id': chunk_id,
                    'repository': snapshot.repository, 'branch': snapshot.branch,
                    'status': 'STAGED'}

    @_using_stager_git
    def integrate(self, snapshot: RepositorySnapshot, staged_receipt: Mapping, *,
                  auto_integrate: bool = False, cas_guard=None) -> dict:
        """Publish a reviewable ref; CAS a branch only if it is not checked out.

        A checked-out branch is held even when its working tree is clean.  There
        is deliberately no reset or checkout path that could overwrite a user's
        work.  Replaying a successful CAS is idempotent.
        """
        if type(auto_integrate) is not bool:
            raise GitSafetyError('auto_integrate must be boolean')
        _validate_snapshot(snapshot)
        receipt = dict(staged_receipt)
        staging = self._staging(snapshot, receipt.get('workflow_id'))
        if receipt.get('staging_repository') != str(staging) or receipt.get('repository') != snapshot.repository:
            raise GitSafetyError('Staged receipt does not belong to this registered project')
        if receipt.get('baseline_commit') != snapshot.commit or receipt.get('branch') != snapshot.branch:
            raise GitSafetyError('Staged receipt does not match the captured baseline')
        result = _oid(receipt.get('result_commit'))
        tree = _oid(receipt.get('tree'))
        chunk_id = receipt.get('chunk_id')
        if not isinstance(chunk_id, str) or not chunk_id:
            raise GitSafetyError('Staged chunk identity is missing')
        expected_ref = ('refs/cochem/coding/' + hashlib.sha256(receipt['workflow_id'].encode()).hexdigest()
                        + '/' + hashlib.sha256(chunk_id.encode()).hexdigest())
        if receipt.get('output_ref') != expected_ref:
            raise GitSafetyError('Staged output ref is not controller-owned')
        if staging.is_symlink() or not staging.is_dir():
            raise GitSafetyError('Staging repository is missing or was replaced')
        _metadata(staging)
        if _git(staging, 'rev-parse', '--verify', expected_ref).decode().strip() != result:
            raise GitSafetyError('Staged result does not match its protected output ref')
        if _git(staging, 'rev-parse', result + '^{tree}').decode().strip() != tree:
            raise GitSafetyError('Staged commit tree does not match its receipt')
        parent = _oid(receipt.get('parent_commit'))
        if _git(staging, 'rev-parse', result + '^').decode().strip() != parent:
            raise GitSafetyError('Staged commit parent does not match its receipt')
        digest = receipt.get('snapshot_sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise GitSafetyError('Staged snapshot digest is invalid')
        commit_bytes = _git(staging, 'cat-file', 'commit', result)
        if not commit_bytes.endswith(f'\nSnapshot-SHA256: {digest}\n'.encode()):
            raise GitSafetyError('Staged snapshot digest does not match its immutable commit')
        _git(staging, 'merge-base', '--is-ancestor', snapshot.commit, result)
        repository = _repository(Path(snapshot.repository))
        _git(repository, 'check-ref-format', snapshot.ref)
        common_dir = Path(os.fsdecode(_git(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir')).strip())
        # The common Git directory gives all controller processes/worktrees the
        # same lock, regardless of their private state-root location.
        with _exclusive_lock(common_dir / 'cochem-integration.lock'):
            current = _current_commit(repository, snapshot.ref)
            checked_out = _checked_out(repository, snapshot.ref)
            objects = [_oid(value) for value in _git(staging, 'rev-list', '--objects',
                                                    '--no-object-names', result).decode().splitlines()]
            _copy_objects(staging, repository, objects)
            try:
                old_output = _git(repository, 'rev-parse', '--verify', expected_ref).decode().strip()
            except GitSafetyError:
                old_output = '0' * len(result)
            if old_output not in ('0' * len(result), result):
                raise GitSafetyError('Published chunk ref was changed outside the controller')
            _git(repository, 'update-ref', expected_ref, result, old_output)
            published = dict(receipt, current_commit=current, checked_out_worktrees=checked_out,
                             integration_ref=snapshot.ref)
            def held(error):
                return dict(published, status='READY_TO_INTEGRATE', reason=error.reason,
                            current_commit=error.current_commit,
                            checked_out_worktrees=error.checked_out or checked_out)

            def acknowledge():
                try:
                    with cas_guard() if cas_guard is not None else nullcontext():
                        actual = _current_commit(repository, snapshot.ref)
                        if actual != result:
                            raise _IntegrationHold('baseline_conflict', actual)
                        return dict(published, status='INTEGRATED', reason='already_integrated', current_commit=result)
                except _IntegrationHold as error:
                    return held(error)

            if current == result:
                return acknowledge()
            if current != snapshot.commit:
                return dict(published, status='READY_TO_INTEGRATE', reason='baseline_conflict')
            if not auto_integrate:
                return dict(published, status='READY_TO_INTEGRATE', reason='operator_review')
            if checked_out:
                return dict(published, status='READY_TO_INTEGRATE', reason='branch_checked_out')
            # Object transfer may take time; recheck immediately before the CAS.
            checked_out = _checked_out(repository, snapshot.ref)
            if checked_out:
                return dict(published, status='READY_TO_INTEGRATE', reason='branch_checked_out',
                            checked_out_worktrees=checked_out)
            # The controller guard holds its durable attempt fence only during
            # the final CAS, not during potentially slow object transfer.
            try:
                with cas_guard() if cas_guard is not None else nullcontext():
                    checked_out = _checked_out(repository, snapshot.ref)
                    if checked_out:
                        raise _IntegrationHold('branch_checked_out', current, checked_out)
                    # A failure must exit the guard exceptionally so it cannot
                    # record a committed integration intent for a rejected CAS.
                    try:
                        _git(repository, 'update-ref', snapshot.ref, result, snapshot.commit)
                    except GitSafetyError as error:
                        raise _CasFailure() from error
                    return dict(published, status='INTEGRATED', reason='compare_and_swap', current_commit=result)
            except _IntegrationHold as error:
                return held(error)
            except _CasFailure:
                actual = _current_commit(repository, snapshot.ref)
                if actual == result:
                    return acknowledge()
                return dict(published, status='READY_TO_INTEGRATE', reason='baseline_conflict', current_commit=actual)
