"""Controller-owned Docker execution, evidence and orphan containment.

The worker receives neither Docker access nor credentials. Input travels as a
bounded regular-file archive over stdin, not a host bind mount. Work, temporary
files and JUnit live on capped RAM tmpfs. Only protected operator configuration
selects images/commands. Docker's exec exit status, inspected limits and parsed
JUnit are evidence; model prose is never used to establish test success.
"""
from __future__ import annotations

from contextlib import closing, nullcontext
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import tarfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from .container_policy import DockerPolicy


class ContainerError(RuntimeError):
    pass


class ContainerCleanupError(ContainerError):
    """A live container could not be verifiably removed; capacity stays held."""


class ContainerCapacityError(ContainerError):
    pass


_EXCLUDES = frozenset({'.git', '.hg', '.svn', '.venv', 'venv', '__pycache__',
                       '.pytest_cache', 'node_modules'})
_SECRET_NAMES = frozenset({'.env', '.aws', '.azure', '.ssh', '.gnupg', '.codex',
                           '.claude', '.gemini', '.npmrc', '.pypirc', '.netrc', '.git-credentials',
                           'auth.json', 'credentials.json'})
_LABEL_OWNER = 'org.cochem.owner'
_LABEL_LEASE = 'org.cochem.lease'
_LABEL_JOB = 'org.cochem.job-sha256'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _prepared_provenance(record, *, as_of):
    """Check real preparation clocks; legacy pool rows are never backfilled."""
    prepared, duration = record.get('prepared_at'), record.get('preparation_seconds')
    return (all(type(value) in (int, float) and math.isfinite(value)
                for value in (prepared, duration, as_of))
            and 0 < prepared <= as_of and duration >= 0)


def _ordinary(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise ContainerError('Container input/state must contain ordinary files and directories only')
    return info


def source_snapshot(root: Path, policy: DockerPolicy, *, ramdisk_workspace=None, source_modes=None):
    """Return an immutable in-memory archive and canonical content manifest."""
    if source_modes is not None and (not isinstance(source_modes, dict) or any(
            not isinstance(name, str) or mode not in ('100644', '100755') for name, mode in source_modes.items())):
        raise ValueError('Source modes must come from the captured Git file-mode manifest')
    root = Path(root)
    admitted_mount = None
    if ramdisk_workspace is not None:
        from .ramdisk import RamWorkspace
        if type(ramdisk_workspace) is not RamWorkspace:
            raise ContainerError('RAM snapshot requires a genuine controller workspace descriptor')
        ramdisk_workspace.validate(identity=ramdisk_workspace.identity, cwd=root)
        admitted_mount = os.path.normcase(os.path.abspath(ramdisk_workspace.config.mount_root))
    _ordinary(root, directory=True)
    for parent in root.absolute().parents:
        if os.path.normcase(os.path.abspath(parent)) != admitted_mount:
            _ordinary(parent, directory=True)
    manifest, total = {}, 0
    normalized_paths = set()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs.sort()
            files.sort()
            keep = []
            for name in dirs:
                _ordinary(Path(current) / name, directory=True)
                if name.casefold() in _SECRET_NAMES:
                    raise ContainerError('Credential directories are forbidden in test input')
                if name.casefold() not in _EXCLUDES:
                    keep.append(name)
            dirs[:] = keep
            for name in files:
                if name.casefold() in _EXCLUDES:
                    continue
                if name.casefold() in _SECRET_NAMES or name.casefold().startswith('.env.') or name.casefold().endswith(('.pem', '.key')):
                    raise ContainerError('Credential files are forbidden in test input')
                path = Path(current) / name
                before = _ordinary(path)
                relative = path.relative_to(root).as_posix()
                if '\\' in relative or ':' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
                    raise ContainerError('Unsafe or Windows-ambiguous source path')
                if relative.casefold() in normalized_paths:
                    raise ContainerError('Case-insensitive source path collision')
                normalized_paths.add(relative.casefold())
                total += before.st_size
                if total > policy.max_source_mb * 1048576 or len(manifest) >= policy.max_source_files:
                    raise ContainerError('Source snapshot exceeds configured limit')
                data = path.read_bytes()
                after = _ordinary(path)
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ino) or len(data) != before.st_size:
                    raise ContainerError('Source changed while snapshot was being captured')
                executable = (source_modes.get(relative, '100644') == '100755'
                              if source_modes is not None else bool(before.st_mode & stat.S_IXUSR))
                manifest[relative] = {'sha256': _digest(data), 'size': len(data), 'executable': executable}
                info = tarfile.TarInfo(relative)
                info.size, info.mode = len(data), 0o755 if executable else 0o644
                info.mtime, info.uid, info.gid = 0, 1000, 1000
                archive.addfile(info, io.BytesIO(data))
    if ramdisk_workspace is not None:
        ramdisk_workspace.validate(identity=ramdisk_workspace.identity, cwd=root)
    if not manifest:
        raise ContainerError('Empty source cannot establish a coding acceptance result')
    return buffer.getvalue(), {'sha256': _digest(_canonical(manifest)), 'files': manifest,
                              'file_count': len(manifest), 'bytes': total}


def parse_junit(data: bytes):
    """Count actual cases; reject doctypes, empty suites and inconsistent counts."""
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ContainerError('JUnit entities and doctypes are forbidden')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ContainerError('Invalid JUnit report') from error
    if root.tag not in ('testsuite', 'testsuites'):
        raise ContainerError('JUnit root is not a testsuite')
    cases = list(root.iter('testcase'))
    if not cases:
        raise ContainerError('Zero executed test cases cannot pass acceptance')
    result = {'tests': len(cases), 'failures': 0, 'errors': 0, 'skipped': 0}
    for case in cases:
        if not case.get('name'):
            raise ContainerError('JUnit testcase is missing its identity')
        result['failures'] += int(case.find('failure') is not None)
        result['errors'] += int(case.find('error') is not None)
        result['skipped'] += int(case.find('skipped') is not None)
    if result['skipped'] == result['tests']:
        raise ContainerError('An entirely skipped suite cannot pass acceptance')
    for suite in root.iter('testsuite'):
        descendants = list(suite.iter('testcase'))
        for name, actual in (
            ('tests', len(descendants)),
            ('failures', sum(case.find('failure') is not None for case in descendants)),
            ('errors', sum(case.find('error') is not None for case in descendants)),
            ('skipped', sum(case.find('skipped') is not None for case in descendants)),
        ):
            if name in suite.attrib:
                try:
                    claimed = int(suite.attrib[name])
                except ValueError as error:
                    raise ContainerError('Noninteger JUnit counts') from error
                if claimed != actual:
                    raise ContainerError('JUnit counts contradict actual test cases')
    result['passed'] = result['tests'] - result['failures'] - result['errors'] - result['skipped']
    return result


def junit_cases(data: bytes):
    """Retain every identity in the bounded report with bounded diagnostics.

    The controller bounds the entire XML before parsing. Truncating the case
    list would hide planned tests in large suites from exact red/green gates.
    """
    cases = []
    for case in ET.fromstring(data).iter('testcase'):
        status, detail = 'passed', None
        for tag, state in (('failure', 'failed'), ('error', 'error'), ('skipped', 'skipped')):
            child = case.find(tag)
            if child is not None:
                status, detail = state, child
                break
        cases.append({'name': case.get('name', '')[:512],
                      'class_name': case.get('classname', '')[:512], 'status': status,
                      'message': detail.get('message', '')[:512] if detail is not None else '',
                      'diagnostic': (detail.text or '')[:1024] if detail is not None else ''})
    return cases


# This fixed program accepts only the controller's prevalidated tar stream. It
# uses no image/project imports and never follows archive links or path escapes.
_EXTRACT = '''import sys,tarfile,pathlib,json,hashlib,os
root=pathlib.Path('/work/source');root.mkdir();pathlib.Path('/work/results').mkdir()
manifest={};total=0
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
 for item in archive:
  parts=pathlib.PurePosixPath(item.name).parts
  if not item.isfile() or item.name.startswith('/') or not parts or any(p in ('..','.') for p in parts) or '\\\\' in item.name or ':' in item.name: raise RuntimeError('Unsafe archive')
  total+=item.size
  if total>int(sys.argv[1]) or len(manifest)>=int(sys.argv[2]) or item.name in manifest: raise RuntimeError('Archive limits')
  target=root.joinpath(*parts);target.parent.mkdir(parents=True,exist_ok=True)
  data=archive.extractfile(item).read(item.size+1)
  if len(data)!=item.size: raise RuntimeError('Archive length')
  with target.open('xb') as stream: stream.write(data)
  target.chmod(0o755 if item.mode&0o100 else 0o644)
  manifest[item.name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'executable':bool(item.mode&0o100)}
encoded=json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
print(hashlib.sha256(encoded).hexdigest())
'''
_OUTPUT_TREE = '''import pathlib,hashlib,json,stat,sys
root=pathlib.Path('/work/source');manifest={};total=0
for path in sorted(root.rglob('*')):
 info=path.lstat()
 if stat.S_ISDIR(info.st_mode): continue
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise RuntimeError('Unsafe output file')
 total+=info.st_size
 if total>int(sys.argv[1]) or len(manifest)>=int(sys.argv[2]): raise RuntimeError('Output limits')
 data=path.read_bytes()
 manifest[path.relative_to(root).as_posix()]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'executable':bool(info.st_mode&0o100)}
print(json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=False))
'''

_READ_RESULT = '''import pathlib,sys,stat
path=pathlib.Path(sys.argv[1]);info=path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>int(sys.argv[2]): raise RuntimeError('Unsafe report')
sys.stdout.buffer.write(path.read_bytes())
'''


@dataclass
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False
    output_exceeded: bool = False


def _bounded_process(argv, *, env, timeout, output_limit, data=None, cancel_event=None):
    """Bound both pipes while draining them; no unbounded communicate buffer."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            creationflags=(0x08000000 | 0x00000200) if os.name == 'nt' else 0)
    captured = [bytearray(), bytearray()]
    exceeded = threading.Event()
    def consume(stream, index):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                room = output_limit - len(captured[index])
                captured[index].extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    exceeded.set()
        finally:
            stream.close()
    threads = [threading.Thread(target=consume, args=(stream, index), daemon=True)
               for index, stream in enumerate((proc.stdout, proc.stderr))]
    for thread in threads:
        thread.start()
    def send():
        try:
            proc.stdin.write(data)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    writer = threading.Thread(target=send, daemon=True) if data is not None else None
    if writer:
        writer.start()
    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    while proc.poll() is None:
        cancelled = cancel_event is not None and cancel_event.is_set()
        timed_out = time.monotonic() >= deadline
        if exceeded.is_set() or cancelled or timed_out:
            proc.kill()
            break
        time.sleep(0.025)
    proc.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
        if thread.is_alive():
            raise ContainerError('Docker client output pipe did not close')
    if writer:
        writer.join(timeout=10)
    return CommandResult(proc.returncode, bytes(captured[0]), bytes(captured[1]),
                         timed_out, cancelled, exceeded.is_set())


class DockerRunner:
    def __init__(self, policy: DockerPolicy, state_root: Path, *, trusted_operator=None, host_boot_id=None):
        self.policy = policy
        self.trusted_operator = trusted_operator
        self.host_boot_id = host_boot_id
        self._daemon_identity = None
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        _ordinary(self.state_root, directory=True)
        for parent in self.state_root.absolute().parents:
            _ordinary(parent, directory=True)
        self.database = self.state_root / 'containers.db'
        if self.database.exists():
            _ordinary(self.database)
        with closing(self._connect()) as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS containers(
                lease TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,container_id TEXT,
                image TEXT NOT NULL,job_id TEXT NOT NULL,attempt_id TEXT NOT NULL,
                created_at REAL NOT NULL,deadline REAL NOT NULL,status TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS pool_demand(
                profile_digest TEXT NOT NULL,job_id TEXT NOT NULL,requested_at REAL NOT NULL,
                handed_off_at REAL,preparation_seconds REAL,
                PRIMARY KEY(profile_digest,job_id));
              CREATE INDEX IF NOT EXISTS pool_demand_window ON pool_demand(profile_digest,requested_at);
              CREATE TABLE IF NOT EXISTS preparation_requests(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_digest TEXT UNIQUE NOT NULL,job_id TEXT NOT NULL,
                requested_at REAL NOT NULL);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(containers)')}
            for name, declaration in (('policy_digest', 'TEXT'), ('label_job_hash', 'TEXT'),
                                      ('memory_mb', 'INTEGER'), ('pool_digest', 'TEXT'), ('endpoint', 'TEXT'),
                                      ('creation_uncertain', 'INTEGER NOT NULL DEFAULT 0'),
                                      ('creation_boot_id', 'TEXT'), ('creation_daemon_id', 'TEXT'),
                                      ('request_started_at','REAL'),('reservation_started_at','REAL'),
                                      ('prepared_at', 'REAL'), ('preparation_seconds', 'REAL'),
                                      ('pool_policy_json', 'TEXT')):
                if name not in columns:
                    db.execute('ALTER TABLE containers ADD COLUMN ' + name + ' ' + declaration)
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('owner', uuid.uuid4().hex))
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('capacity', str(policy.max_containers)))
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('unknown_owned', '0'))
            self.owner = db.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()[0]
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def _call(self, arguments, *, timeout=30, output_limit=None, data=None, cancel_event=None):
        if os.name == 'nt':
            from .deployment import attest_docker_pipe_server
            attest_docker_pipe_server(self.policy.endpoint, {}, trusted_operator=self.trusted_operator,
                                      trusted_server_executables=self.policy.pipe_server_executables)
        env = dict(os.environ)
        # Local explicit endpoint cannot be redirected by inherited context/TLS.
        # Registry credential configuration stays available to the client only;
        # Docker never receives env-file, bind mounts or a daemon socket mount.
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(key, None)
        return _bounded_process([self.policy.executable, '--host', self.policy.endpoint, *arguments],
            env=env, timeout=timeout, output_limit=output_limit or self.policy.output_limit_bytes,
            data=data, cancel_event=cancel_event)

    def _json(self, arguments):
        result = self._call(arguments, output_limit=4194304)
        if result.returncode != 0 or result.output_exceeded:
            raise ContainerError('Docker inspection failed: ' + result.stderr.decode(errors='replace')[:512])
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise ContainerError('Docker returned invalid inspection JSON') from error

    def _reserve(self, job_id, attempt_id, *, preparing=False, capacity_limit=None,
                 require_warm=False, request_started_at=None):
        if not all(isinstance(value, str) and 0 < len(value) <= 256 and '\0' not in value
                   for value in (job_id, attempt_id)):
            raise ValueError('Container requires bounded job and attempt identities')
        lease = uuid.uuid4().hex
        now = time.time()
        requested = now if request_started_at is None else request_started_at
        if type(requested) not in (int,float) or not math.isfinite(requested) or not 0 <= requested <= now:
            raise ValueError('Container request start must be a real, nonfuture controller timestamp')
        record = {'lease': lease, 'name': 'cochem-' + self.owner[:12] + '-' + lease,
                  'container_id': None, 'image': self.policy.image, 'job_id': job_id,
                  'attempt_id': attempt_id, 'created_at': now,
                  'deadline': now + sum(item.timeout_seconds for item in self.policy.commands) + 180,
                  'status': 'PREPARING' if preparing else 'INTENT',
                  'policy_digest': self.policy.digest, 'label_job_hash': _digest(job_id.encode()),
                  'memory_mb': self.policy.memory_mb, 'pool_digest': self.policy.pool_digest,
                  'endpoint': self.policy.endpoint, 'creation_uncertain': 0,
                  'creation_boot_id': None, 'creation_daemon_id': None}
        record.update(request_started_at=requested,reservation_started_at=now)
        record['pool_policy_json'] = _canonical(self.policy.as_dict()).decode()
        if preparing:
            record['deadline'] = now + 120
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            total = db.execute("SELECT count(*) FROM containers WHERE status!='REMOVED'").fetchone()[0]
            published = int(db.execute("SELECT value FROM metadata WHERE key='capacity'").fetchone()[0])
            if int(db.execute("SELECT value FROM metadata WHERE key='unknown_owned'").fetchone()[0]):
                raise ContainerCapacityError('Unrecognized owned Docker objects require trusted reconciliation')
            effective_limit = min(self.policy.max_containers, published)
            if total > effective_limit:
                raise ContainerCapacityError('Stricter current ceiling requires idle-pool drainage first')
            if not preparing:
                candidates = db.execute("SELECT * FROM containers WHERE status='WARM' AND pool_digest=? "
                                        'AND deadline>? ORDER BY created_at LIMIT 256',
                                        (self.policy.pool_digest, now)).fetchall()
                warm = next((dict(row) for row in candidates
                             if _prepared_provenance(dict(row), as_of=now)), None)
                if warm is not None:
                    claimed = dict(warm)
                    claimed.update(job_id=job_id, attempt_id=attempt_id,
                                   deadline=record['deadline'], status='ACTIVE', policy_digest=self.policy.digest,
                                   request_started_at=requested,reservation_started_at=now)
                    db.execute("UPDATE containers SET job_id=?,attempt_id=?,deadline=?,status='ACTIVE',policy_digest=?,request_started_at=?,reservation_started_at=? WHERE lease=? AND status='WARM'",
                               (job_id, attempt_id, record['deadline'], self.policy.digest, requested, now, claimed['lease']))
                    db.execute('UPDATE pool_demand SET handed_off_at=?,preparation_seconds=? WHERE profile_digest=? AND job_id=?',
                               (now, warm['preparation_seconds'], self.policy.pool_digest, job_id))
                    db.commit()
                    claimed['warm_claimed'] = True
                    return claimed
                if require_warm:
                    raise ContainerCapacityError('Exact captured Docker profile is awaiting verified preallocated capacity')
            count = db.execute("SELECT count(*) FROM containers WHERE status!='REMOVED'").fetchone()[0]
            if count >= min(effective_limit, capacity_limit if capacity_limit is not None else self.policy.max_containers):
                raise ContainerCapacityError('Configured container or hardware capacity is occupied')
            db.execute('INSERT INTO containers(lease,name,container_id,image,job_id,attempt_id,created_at,deadline,status,policy_digest,label_job_hash,memory_mb,pool_digest,endpoint,request_started_at,reservation_started_at,pool_policy_json) '
                       'VALUES(:lease,:name,:container_id,:image,:job_id,:attempt_id,:created_at,:deadline,:status,:policy_digest,:label_job_hash,:memory_mb,:pool_digest,:endpoint,:request_started_at,:reservation_started_at,:pool_policy_json)', record)
            db.commit()
        return record

    def _set(self, lease, **fields):
        if set(fields) - {'container_id', 'status', 'deadline', 'creation_uncertain',
                          'creation_boot_id', 'creation_daemon_id'}:
            raise ValueError('Unsupported container state update')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status FROM containers WHERE lease=?', (lease,)).fetchone()
            if fields.get('status') in ('ACTIVE', 'WARM', 'BOUND', 'EXECUTING') and (current is None or current[0] == 'REMOVED'):
                raise ContainerCleanupError('A removed container lease cannot be resurrected')
            db.execute('UPDATE containers SET ' + ','.join(key + '=?' for key in fields)
                       + ' WHERE lease=?', [*fields.values(), lease])
            db.commit()

    def _native_boot_identity(self):
        if os.name != 'nt':
            return None
        from .windows import current_boot_identity
        observed = current_boot_identity()
        if self.host_boot_id is not None and observed != self.host_boot_id:
            raise ContainerCleanupError('Controller boot witness differs from the actual native host boot')
        return str(observed)

    def _begin_creation(self, record):
        # Commit BEFORE transmitting the request. A killed CLI/controller does
        # not cancel an already accepted asynchronous daemon create request.
        boot = self._native_boot_identity()
        if not self._daemon_identity:
            raise ContainerError('Docker creation requires an independently inspected daemon identity')
        self._set(record['lease'], creation_uncertain=1, creation_boot_id=boot,
                  creation_daemon_id=self._daemon_identity)

    def _reboot_proves_request_ended(self, record):
        previous = record.get('creation_boot_id')
        current = self._native_boot_identity()
        if not previous or not current or previous == current or not record.get('creation_daemon_id'):
            return False
        # A complete native host reboot ends every old in-flight daemon RPC.
        # Require the same locally attested daemon, not a replacement endpoint.
        info = self._json(['info', '--format', '{{json .}}'])
        return info.get('ID') == record['creation_daemon_id']

    def create_arguments(self, record):
        policy = self.policy
        args = ['create', '--pull', 'never', '--name', record['name'],
                '--label', _LABEL_OWNER + '=' + self.owner,
                '--label', _LABEL_LEASE + '=' + record['lease'],
                '--label', _LABEL_JOB + '=' + (record.get('label_job_hash') or _digest(record['job_id'].encode())),
                '--network', 'none', '--read-only', '--no-healthcheck', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges:true', '--user', '1000:1000',
                '--memory', str(policy.memory_mb) + 'm', '--memory-swap', str(policy.memory_mb) + 'm',
                '--cpus', str(policy.cpus), '--pids-limit', str(policy.pids_limit),
                '--ipc', 'none', '--log-driver', 'none', '--workdir', '/work',
                '--tmpfs', f'/work:rw,nosuid,nodev,size={policy.tmpfs_mb}m,mode=1777',
                '--tmpfs', f'/tmp:rw,nosuid,nodev,size={policy.tmpfs_mb}m,mode=1777']
        # Docker client proxy defaults would otherwise inject host proxy URLs.
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                    'http_proxy','https_proxy','all_proxy','no_proxy'):
            args += ['--env', key + '=']
        for value in ('HOME=/tmp', 'TMPDIR=/tmp', 'PYTHONDONTWRITEBYTECODE=1',
                      'PYTHONUNBUFFERED=1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1'):
            args += ['--env', value]
        return [*args, '--entrypoint', 'python', policy.image, '-I', '-c',
                'import time; time.sleep(604800)']

    def _inspect_owned(self, record, *, timeout=30):
        if record.get('endpoint') not in (None, self.policy.endpoint):
            raise ContainerCleanupError('Recorded container belongs to a different Docker endpoint')
        result = self._call(['inspect', record.get('container_id') or record['name']],
                            timeout=timeout, output_limit=4194304)
        if result.returncode:
            error = result.stderr.decode(errors='replace').casefold()
            if result.returncode == 1 and ('no such object' in error or 'no such container' in error):
                return None
            raise ContainerCleanupError('Cannot determine owned container state')
        try:
            values = json.loads(result.stdout)
            value = values[0]
            labels = value['Config']['Labels'] or {}
            if (len(values) != 1 or labels.get(_LABEL_OWNER) != self.owner
                    or labels.get(_LABEL_LEASE) != record['lease']
                    or labels.get(_LABEL_JOB) != (record.get('label_job_hash') or _digest(record['job_id'].encode()))
                    or value['Name'] != '/' + record['name'] or value['Image'] != record['image']
                    or (record.get('container_id') and value['Id'] != record['container_id'])):
                raise ContainerCleanupError('Container ownership identity does not match protected lease')
            return value
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ContainerCleanupError('Invalid owned-container inspection') from error

    def _verify_limits(self, value, *, policy=None):
        host, policy = value['HostConfig'], policy or self.policy
        expected = {'NetworkMode': 'none', 'ReadonlyRootfs': True,
                    'Memory': policy.memory_mb * 1048576, 'MemorySwap': policy.memory_mb * 1048576,
                    'NanoCpus': int(policy.cpus * 1e9), 'PidsLimit': policy.pids_limit,
                    'IpcMode': 'none', 'Privileged': False}
        if any(host.get(key) != item for key, item in expected.items()):
            raise ContainerError('Docker did not enforce the configured sandbox limits')
        if (host.get('CapDrop') != ['ALL'] or host.get('CapAdd') or host.get('Binds')
                or host.get('Devices') or host.get('DeviceRequests')
                or host.get('PidMode') or host.get('VolumesFrom')
                or 'no-new-privileges:true' not in host.get('SecurityOpt', [])
                or value['Config'].get('User') != '1000:1000'):
            raise ContainerError('Docker sandbox privilege or mount contract is not enforced')
        tmpfs = host.get('Tmpfs', {})
        if set(tmpfs) != {'/work', '/tmp'} or any('size=' + str(policy.tmpfs_mb) + 'm' not in options
                                               for options in tmpfs.values()):
            raise ContainerError('Docker RAM scratch contract is not enforced')
        if any(mount.get('Type') != 'tmpfs' for mount in value.get('Mounts', [])):
            raise ContainerError('Unexpected volume or host mount in sandbox')
        return {**expected, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                'Tmpfs': tmpfs, 'User': '1000:1000'}

    def _remove(self, record):
        try:
            with closing(self._connect()) as db:
                stored = db.execute('SELECT * FROM containers WHERE lease=?', (record['lease'],)).fetchone()
            if stored is None:
                raise ContainerCleanupError('Cannot remove a container without its retained protected lease')
            record = dict(stored)
            value = self._inspect_owned(record)
            if value is not None:
                self._call(['rm', '--force', '--volumes', value['Id']],
                           timeout=self.policy.cleanup_timeout_seconds)
                if self._inspect_owned(record) is not None:
                    raise ContainerCleanupError('Owned container cleanup could not be verified')
            elif record.get('creation_uncertain') and not self._reboot_proves_request_ended(record):
                raise ContainerCleanupError('Unacknowledged daemon creation may still complete; capacity remains quarantined')
            self._set(record['lease'], status='REMOVED', creation_uncertain=0)
        except Exception as error:
            self._set(record['lease'], status='QUARANTINED')
            if isinstance(error, ContainerCleanupError):
                raise
            raise ContainerCleanupError('Owned container teardown could not be verified') from error

    def _reattest_warm(self, record, value, daemon_identity):
        """Reconstruct only a protected, hash-bound profile; never guess old policy."""
        captured = DockerPolicy.from_dict(json.loads(record.get('pool_policy_json') or 'null'))
        if (captured.digest != record['policy_digest'] or captured.pool_digest != record['pool_digest']
                or captured.image != record['image'] or captured.image not in self.policy.allowed_images
                or captured.endpoint != self.policy.endpoint or captured.executable != self.policy.executable
                or not record.get('creation_daemon_id') or record['creation_daemon_id'] != daemon_identity
                or record.get('creation_uncertain')
                or any(getattr(captured,key)>getattr(self.policy,key) for key in
                       ('memory_mb','cpus','pids_limit','tmpfs_mb','max_containers'))):
            raise ContainerError('Prepared container policy or engine identity drifted')
        self._verify_limits(value, policy=captured)
        if (not value.get('State', {}).get('Running') or record['deadline'] <= time.time()
                or not _prepared_provenance(record, as_of=time.time())):
            raise ContainerError('Prepared container readiness or timing is invalid')

    def _discover_owned(self):
        """Reconcile actual objects against ALL leases, including tombstones.

        A historic REMOVED record is not proof that a late daemon request cannot
        materialize. Unknown/mismatching owner-labelled objects block capacity;
        they are never adopted or removed using labels alone.
        """
        unknown, diagnostics = 0, []
        scan_started=time.time()
        try:
            self.preflight()
            listed = self._call(['ps', '--all', '--no-trunc', '--filter',
                                 'label=' + _LABEL_OWNER + '=' + self.owner,
                                 '--format', '{{.ID}}'], output_limit=65536)
            identifiers = listed.stdout.decode().splitlines()
            if (listed.returncode or listed.output_exceeded or len(identifiers) > 256
                    or len(set(identifiers)) != len(identifiers)
                    or any(not re.fullmatch('[0-9a-f]{64}', value) for value in identifiers)):
                raise ContainerCleanupError('Actual Docker ownership census failed or exceeded its bound')
            for identifier in identifiers:
                try:
                    values = self._json(['inspect', identifier])
                    if not isinstance(values, list) or len(values) != 1:
                        raise ContainerCleanupError('Invalid ownership-census inspection')
                    value = values[0]
                    lease = value.get('Config', {}).get('Labels', {}).get(_LABEL_LEASE)
                    with closing(self._connect()) as db:
                        row = db.execute('SELECT * FROM containers WHERE lease=?', (lease,)).fetchone()
                    if row is None:
                        raise ContainerCleanupError('Owner-labelled container has no protected lease')
                    record = dict(row)
                    owned = self._inspect_owned(record)
                    if owned is None:
                        continue  # A concurrent verified cleanup completed.
                    if owned['Id'] != identifier:
                        raise ContainerCleanupError('Owner census contradicts protected container identity')
                    invalid_warm = False
                    if record['status'] == 'WARM':
                        try:
                            self._reattest_warm(record, owned, self._daemon_identity)
                        except Exception as error:
                            invalid_warm = True
                            diagnostics.append({'container_id': identifier, 'reason': str(error)[:512]})
                    with closing(self._connect()) as db:
                        db.execute('BEGIN IMMEDIATE')
                        # Reopen only tombstones with actual independent identity
                        # proof, never from a supplied worker ID or a 404.
                        db.execute("UPDATE containers SET status='QUARANTINED',deadline=0,container_id=?,creation_uncertain=0 "
                                   "WHERE lease=? AND status='REMOVED'", (identifier, record['lease']))
                        if (invalid_warm or not owned.get('State', {}).get('Running')
                                or not _prepared_provenance(record, as_of=time.time())):
                            # Stopped or legacy/invalid prepared instances are
                            # unavailable even before idle TTL expiry. Drain
                            # before replenishing; never invent old timing.
                            db.execute("UPDATE containers SET status='DRAINING',deadline=0 "
                                       "WHERE lease=? AND status='WARM'", (record['lease'],))
                        db.commit()
                except Exception as error:
                    unknown += 1
                    diagnostics.append({'container_id': identifier, 'reason': str(error)[:512]})
            with closing(self._connect()) as db:
                # Missing physical members cannot remain available. Do not
                # retire a concurrently published preparation newer than census.
                placeholders=','.join('?' for _ in identifiers) or 'NULL'
                db.execute("UPDATE containers SET status='DRAINING',deadline=0 WHERE status='WARM' "
                           f"AND prepared_at<=? AND container_id NOT IN ({placeholders})",
                           [scan_started,*identifiers]) if identifiers else db.execute(
                    "UPDATE containers SET status='DRAINING',deadline=0 WHERE status='WARM' AND prepared_at<=?",
                    (scan_started,))
                db.commit()
        except Exception as error:
            # An unobservable daemon cannot establish an empty physical census.
            unknown = max(1, unknown)
            diagnostics.append({'reason': str(error)[:512]})
            # Unobservable engine state cannot leave apparently ready inventory.
            with closing(self._connect()) as db:
                db.execute("UPDATE containers SET status='QUARANTINED' WHERE status='WARM'")
                db.commit()
        with closing(self._connect()) as db:
            db.execute("UPDATE metadata SET value=? WHERE key='unknown_owned'", (str(unknown),))
            db.commit()
        return diagnostics

    def reap_orphans(self, active_attempt_ids=None, *, now=None, include_warm=False, admission_lock=None):
        """Call every <=60 seconds and at controller startup.

        With no active-set argument, only expired leases are removed. A supplied
        set must come from the controller's durable live leases, never workers.
        Unlabelled, mismatching, and other-controller containers are never killed.
        """
        now = time.time() if now is None else now
        discoveries = self._discover_owned()
        with closing(self._connect()) as db:
            records = [dict(row) for row in db.execute("SELECT * FROM containers WHERE status!='REMOVED'")]
        removed, failures = [], []
        for record in records:
            with admission_lock if admission_lock is not None else nullcontext():
                active=active_attempt_ids() if callable(active_attempt_ids) else active_attempt_ids
                with closing(self._connect()) as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=db.execute('SELECT * FROM containers WHERE lease=?',(record['lease'],)).fetchone()
                    if row is None or row['status']=='REMOVED':
                        continue
                    record=dict(row)
                    invalid_prepared = (record['status']=='WARM'
                                        and not _prepared_provenance(record, as_of=now))
                    if (not include_warm and not invalid_prepared
                            and record['status'] in ('WARM', 'PREPARING') and record['deadline'] > now):
                        continue
                    if (record['deadline'] > now and (active is None or record['attempt_id'] in active)
                            and not invalid_prepared
                            and record['status'] not in ('DRAINING','QUARANTINED')
                            and not (include_warm and record['status'] in ('WARM', 'PREPARING'))):
                        continue
                    db.execute("UPDATE containers SET status='DRAINING' WHERE lease=?",(record['lease'],))
                    db.commit()
            try:
                self._remove(record)
                removed.append(record['lease'])
            except ContainerError as error:
                self._set(record['lease'], status='QUARANTINED')
                failures.append({'lease': record['lease'], 'reason': str(error)})
        return {'removed': removed, 'quarantined': [*discoveries, *failures]}

    def confirm_attempt_cleanup(self, attempt_id, *, controller_stopped=False):
        """Verify Docker cleanup independently of Windows Job Object cleanup.

        Only a caller that has proved the old controller stopped may use absence
        of an INTENT row as evidence of never starting. INTENT commits before
        any Docker create/run, including warm-lease assignment to an attempt.
        """
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValueError('Exact attempt identity required')
        with closing(self._connect()) as db:
            records = [dict(row) for row in db.execute('SELECT * FROM containers WHERE attempt_id=?', (attempt_id,))]
        if not records:
            return {'attempt_id': attempt_id, 'cleanup_verified': controller_stopped is True,
                    'never_started': True, 'leases': [], 'controller_stopped': controller_stopped is True}
        failures = []
        for record in records:
            try:
                self._remove(record)
            except ContainerCleanupError as error:
                failures.append(str(error)[:512])
        return {'attempt_id': attempt_id, 'cleanup_verified': not failures, 'never_started': False,
                'leases': [record['lease'] for record in records], 'errors': failures}

    def set_capacity(self, limit):
        """Publish the controller's global native-plus-Docker admission budget.

        The Runtime owns the shared admission lock and drains idle containers
        before allocating native workers. Every Docker allocation consults this
        durable cap in its same SQLite write transaction, including warm prep.
        Lowering a cap never silently kills active code execution.
        """
        if type(limit) is not int or not 0 <= limit <= 256:
            raise ValueError('Published Docker capacity must be between zero and the configured ceiling')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE metadata SET value=? WHERE key='capacity'", (str(limit),))
            db.commit()
        return self.census()

    def census(self):
        with closing(self._connect()) as db:
            records = [dict(row) for row in db.execute("SELECT * FROM containers WHERE status!='REMOVED'")]
            published = int(db.execute("SELECT value FROM metadata WHERE key='capacity'").fetchone()[0])
            unknown = int(db.execute("SELECT value FROM metadata WHERE key='unknown_owned'").fetchone()[0])
            requests = db.execute('SELECT count(*) FROM preparation_requests').fetchone()[0]
        return {'owned': len(records) + unknown, 'limit': min(self.policy.max_containers, published),
                'published_capacity': published,
                'unknown_owned': unknown,
                'pending_profiles': requests,
                'warm': sum(row['status'] == 'WARM' for row in records),
                'preparing': sum(row['status'] == 'PREPARING' for row in records),
                'active': sum(row['status'] in ('ACTIVE', 'INTENT', 'BOUND', 'EXECUTING') for row in records),
                'quarantined': unknown + sum(row['status'] == 'QUARANTINED' for row in records),
                'reserved_memory_mb': unknown * self.policy.memory_mb + sum(row['memory_mb'] or self.policy.memory_mb for row in records),
                'containers': records}

    def request_preparation(self, profile_digest, job_id):
        """Persist bounded FIFO demand; policy is reloaded from the trusted job.

        No worker-supplied image or command is stored here. Restart reconstructs
        the policy from the protected coding workflow and rechecks the current
        operator bounds before replenishing its matching profile.
        """
        if (not isinstance(profile_digest, str) or not re.fullmatch('[0-9a-f]{64}', profile_digest)
                or not isinstance(job_id, str) or not 0 < len(job_id) <= 256 or '\0' in job_id):
            raise ValueError('Prepared-pool demand requires bounded job and profile identities')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM preparation_requests WHERE profile_digest=?', (profile_digest,)).fetchone():
                return True
            if db.execute('SELECT count(*) FROM preparation_requests').fetchone()[0] >= 64:
                return False
            db.execute('INSERT INTO preparation_requests(profile_digest,job_id,requested_at) VALUES(?,?,?)',
                       (profile_digest, job_id, time.time()))
            db.commit()
            return True

    def preparation_requests(self):
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute('SELECT * FROM preparation_requests ORDER BY sequence LIMIT 64')]

    def complete_preparation_request(self, profile_digest):
        with closing(self._connect()) as db:
            db.execute('DELETE FROM preparation_requests WHERE profile_digest=?', (profile_digest,))
            db.commit()

    def reserve_attempt(self, job_id, attempt_id, *, retire_incompatible=False,
                        require_warm=True, request_started_at=None):
        """Bind an unused prepared slot; requests never create containers.

        Only background ``prepare_pool`` may create a physical container. A
        depleted or incompatible pool produces a bounded scheduler hold while
        that service replenishes; there is deliberately no cold-start fallback.
        """
        if not self.policy.enabled:
            raise ContainerError('Docker execution is disabled')
        if require_warm is not True:
            raise ValueError('Job requests require prepared containers; cold creation belongs to pool replenishment')
        if not all(isinstance(value,str) and 0<len(value)<=256 and '\0' not in value for value in (job_id,attempt_id)):
            raise ValueError('Container requires bounded job and attempt identities')
        with closing(self._connect()) as db:
            db.execute('INSERT OR IGNORE INTO pool_demand(profile_digest,job_id,requested_at) VALUES(?,?,?)',
                       (self.policy.pool_digest, job_id, time.time()))
            db.commit()
        if self.policy.warm_pool_size <= 0:
            raise ContainerCapacityError('Prepared-container pool is disabled; no job may cold-create a container')
        try:
            record = self._reserve(job_id, attempt_id,require_warm=require_warm,
                                   request_started_at=request_started_at)
        except ContainerCapacityError:
            if not retire_incompatible or not self._retire_incompatible_warm():
                raise
            record = self._reserve(job_id, attempt_id,require_warm=require_warm,
                                   request_started_at=request_started_at)
        self._set(record['lease'], status='BOUND')
        record['status'] = 'BOUND'
        return record

    def _consume_reservation(self, reservation, job_id, attempt_id):
        if not isinstance(reservation, dict) or not isinstance(reservation.get('lease'), str):
            raise ContainerError('Controller-owned Docker reservation is required')
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM containers WHERE lease=?', (reservation['lease'],)).fetchone()
            if row is None:
                raise ContainerError('Unknown Docker reservation')
            record = dict(row)
            expected = {'job_id': job_id, 'attempt_id': attempt_id, 'policy_digest': self.policy.digest,
                        'image': self.policy.image, 'endpoint': self.policy.endpoint, 'status': 'BOUND'}
            if any(record.get(key) != value for key, value in expected.items()) or any(
                    reservation.get(key) != record.get(key) for key in ('job_id','attempt_id','policy_digest','image','container_id','name','endpoint')):
                raise ContainerError('Docker reservation was replaced, consumed, or belongs to another attempt')
            if not isinstance(record['container_id'], str) or not re.fullmatch('[0-9a-f]{64}', record['container_id']):
                raise ContainerError('Execution requires a physically prepared single-use container')
            if not _prepared_provenance(record, as_of=record.get('reservation_started_at')):
                raise ContainerError('Prepared-container timing provenance is missing or invalid; replenish before execution')
            published = int(db.execute("SELECT value FROM metadata WHERE key='capacity'").fetchone()[0])
            owned = db.execute("SELECT count(*) FROM containers WHERE status!='REMOVED'").fetchone()[0]
            unknown = int(db.execute("SELECT value FROM metadata WHERE key='unknown_owned'").fetchone()[0])
            if unknown or published <= 0 or owned > min(self.policy.max_containers, published):
                raise ContainerCapacityError('Published Docker capacity was revoked before execution handoff')
            db.execute("UPDATE containers SET status='EXECUTING' WHERE lease=? AND status='BOUND'", (record['lease'],))
            db.commit()
            record['warm_claimed'] = record['container_id'] is not None
            record['status'] = 'EXECUTING'
            return record

    def _retire_incompatible_warm(self):
        """Make room for a pending captured profile; never evict active work."""
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM containers WHERE status='WARM' AND endpoint=? "
                             'AND (pool_digest IS NULL OR pool_digest!=?) ORDER BY created_at LIMIT 1',
                             (self.policy.endpoint, self.policy.pool_digest)).fetchone()
            if row is None:
                return False
            record = dict(row)
            db.execute("UPDATE containers SET status='DRAINING' WHERE lease=? AND status='WARM'", (record['lease'],))
            db.commit()
        self._remove(record)
        return True

    def pool_target_snapshot(self, *, now=None, window_seconds=300):
        """Bounded measured recommendation; fixed defaults until enough demand.

        Adaptation never invents physical capacity: the selected value is only
        a profile inventory target, subsequently bounded by shared admission,
        FIFO requests and native fairness. At least 32 completed warm handoffs
        spanning 60 seconds are required before opt-in adaptation can act.
        """
        observed = time.time() if now is None else now
        if type(observed) not in (int, float) or not math.isfinite(observed):
            raise ValueError('Pool observation time must be finite')
        if type(window_seconds) is not int or not 60 <= window_seconds <= 3600:
            raise ValueError('Pool demand window must be in 60..3600 seconds')
        with closing(self._connect()) as db:
            rows = db.execute('SELECT requested_at,handed_off_at,preparation_seconds FROM pool_demand '
                'WHERE profile_digest=? AND requested_at>=? AND requested_at<=? ORDER BY requested_at DESC LIMIT 4097',
                (self.policy.pool_digest, observed-window_seconds, observed)).fetchall()
        overflow = len(rows)>4096
        completed = [row for row in rows if row['handed_off_at'] is not None
                     and type(row['preparation_seconds']) in (int,float) and row['preparation_seconds']>=0]
        span = max(0, observed-min((row['requested_at'] for row in rows),default=observed))
        sufficient = not overflow and len(completed)>=32 and span>=60
        preparation_p95 = (sorted(row['preparation_seconds'] for row in completed)[math.ceil(.95*len(completed))-1]
                           if completed else None)
        rate = len(rows)/span if span else None
        recommendation = (min(self.policy.warm_pool_size, max(1, math.ceil(rate*preparation_p95)+1))
                          if sufficient and self.policy.warm_pool_size else None)
        adaptive = bool(self.policy.adaptive_pool_enabled and sufficient)
        return {'profile_digest':self.policy.pool_digest, 'mode':'adaptive' if adaptive else 'fixed',
                'configured_target':self.policy.warm_pool_size,
                'selected_target':recommendation if adaptive else self.policy.warm_pool_size,
                'recommended_target':recommendation, 'evidence_sufficient':sufficient,
                'reason':'measured_demand' if adaptive else 'adaptation_disabled' if not self.policy.adaptive_pool_enabled else 'insufficient_measured_demand',
                'demand_count':len(rows), 'completed_handoffs':len(completed),
                'observation_seconds':span, 'window_seconds':window_seconds,
                'preparation_p95_seconds':preparation_p95, 'observed_arrivals_per_second':rate,
                'sample_overflow':overflow, 'hardware_can_only_lower_target':True}

    def prepare_pool(self, target=None, *, admission_lock=None):
        """Precreate unused sandboxes outside the request path, subject to capacity.

        ``target`` is the hardware-admitted total capacity, including active and
        warm containers, never additional capacity. Every prepared instance is
        used once. Call from a bounded maintenance thread, not an API request.
        The protected registry serializes concurrent preparation and claims.
        """
        if not self.policy.enabled:
            return {'prepared': [], 'census': self.census()}
        target = self.policy.warm_pool_size if target is None else target
        if type(target) is not int or not 0 <= target <= self.policy.max_containers:
            raise ValueError('Warm-pool target must respect the hard container ceiling')
        target = min(target, self.pool_target_snapshot()['selected_target'])
        # Lower admission drains only idle containers, atomically excluding a
        # concurrent dispatch. Active tests are governed by their own lease.
        drained = []
        while self.census()['owned'] > target:
            with closing(self._connect()) as db:
                db.execute('BEGIN IMMEDIATE')
                idle = db.execute("SELECT * FROM containers WHERE status='WARM' ORDER BY created_at DESC LIMIT 1").fetchone()
                if idle is None:
                    break
                record = dict(idle)
                db.execute("UPDATE containers SET status='DRAINING' WHERE lease=? AND status='WARM'", (record['lease'],))
                db.commit()
            self._remove(record)
            drained.append(record['container_id'])
        if target == 0:
            return {'prepared': [], 'drained': drained, 'census': self.census()}
        self.preflight()
        prepared = []
        while self.census()['owned'] < target:
            try:
                with admission_lock if admission_lock is not None else nullcontext():
                    record = self._reserve('_warm_', 'warm-' + uuid.uuid4().hex, preparing=True, capacity_limit=target)
            except ContainerCapacityError:
                break
            try:
                preparation_started = time.monotonic()
                self._begin_creation(record)
                result = self._call(['run', '--detach', *self.create_arguments(record)[1:]], timeout=60)
                container_id = result.stdout.decode().strip()
                if result.returncode or not re.fullmatch('[0-9a-f]{64}', container_id):
                    raise ContainerError('Fresh warm-container creation failed')
                record['container_id'] = container_id
                self._set(record['lease'], container_id=container_id, creation_uncertain=0)
                value = self._inspect_owned(record)
                self._verify_limits(value)
                if not value['State']['Running']:
                    raise ContainerError('Prepared container did not remain running')
                with admission_lock if admission_lock is not None else nullcontext(), closing(self._connect()) as db:
                    db.execute('BEGIN IMMEDIATE')
                    published = int(db.execute("SELECT value FROM metadata WHERE key='capacity'").fetchone()[0])
                    owned = db.execute("SELECT count(*) FROM containers WHERE status!='REMOVED'").fetchone()[0]
                    state = db.execute('SELECT status FROM containers WHERE lease=?', (record['lease'],)).fetchone()
                    admitted = (state is not None and state[0] == 'PREPARING'
                                and owned <= min(self.policy.max_containers, published))
                    if admitted:
                        prepared_at = time.time()
                        db.execute("UPDATE containers SET status='WARM',deadline=?,prepared_at=?,preparation_seconds=? WHERE lease=?",
                                   (prepared_at + 3600, prepared_at, time.monotonic() - preparation_started, record['lease']))
                    db.commit()
                if not admitted:
                    self._remove(record)
                    drained.append(container_id)
                    break
                prepared.append(container_id)
            except Exception:
                self._remove(record)
                raise
        return {'prepared': prepared, 'drained': drained, 'census': self.census()}

    def prepare_requested_profile(self, capacity, *, admission_lock=None):
        """Prepare one exact queued policy without taking an additional physical seat."""
        census=self.census()
        target=min(capacity,self.policy.max_containers)
        if census['owned']>target:
            self.prepare_pool(target=max(0,target),admission_lock=admission_lock)
            census=self.census()
            if census['owned']>target:
                return {'ready':False,'reason':'pressure_requires_active_seat_drain','census':census}
        if any(row['status']=='WARM' and row['pool_digest']==self.policy.pool_digest
               and row['deadline']>time.time() and _prepared_provenance(row, as_of=time.time())
               for row in census['containers']):
            return {'ready':True,'census':census}
        if capacity<=0 or self.policy.warm_pool_size<=0 or census['quarantined']:
            return {'ready':False,'reason':'preallocated_capacity_unavailable','census':census}
        if census['owned']>=target and not self._retire_incompatible_warm():
            return {'ready':False,'reason':'all_preallocated_seats_active','census':self.census()}
        self.prepare_pool(target=min(target,self.census()['owned']+1),admission_lock=admission_lock)
        census=self.census()
        return {'ready':any(row['status']=='WARM' and row['pool_digest']==self.policy.pool_digest
                           and _prepared_provenance(row, as_of=time.time())
                           for row in census['containers']),'census':census}

    def health(self):
        checked = time.time()
        discoveries = []
        try:
            self.preflight()
            discoveries = self._discover_owned()
            engine, diagnostic = 'healthy', None
        except Exception as error:
            engine, diagnostic = 'unavailable', str(error)[:512]
        return {'engine': engine, 'required': self.policy.enabled, 'checked_at': checked,
                'census': self.census(), 'diagnostic': diagnostic, 'census_diagnostics': discoveries}

    def _check_image(self):
        images = self._json(['image', 'inspect', self.policy.image])
        if len(images) != 1 or images[0].get('Id') != self.policy.image or images[0].get('Os') != 'linux':
            raise ContainerError('Exact allowlisted Linux image must already exist locally')
        image_config = images[0].get('Config', {})
        if image_config.get('Volumes'):
            raise ContainerError('Image-declared disk volumes are forbidden')
        for variable in image_config.get('Env', []):
            name = variable.split('=', 1)[0].upper()
            if any(marker in name for marker in ('API_KEY', 'TOKEN', 'PASSWORD', 'CREDENTIAL', 'SECRET')):
                raise ContainerError('Image contains a credential-bearing environment variable')
        return {'image_id': self.policy.image, 'os': 'linux'}

    def preflight(self):
        """Read-only daemon/image checks; no pull, inference or container creation."""
        if not self.policy.enabled:
            raise ContainerError('Docker policy is disabled')
        info = self._json(['info', '--format', '{{json .}}'])
        identity = info.get('ID')
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ContainerError('Docker daemon did not supply a bounded independent identity')
        self._daemon_identity = identity
        if info.get('OSType') != 'linux':
            raise ContainerError('Linux containers are required, including on Windows Docker Desktop')
        required = ('MemoryLimit', 'SwapLimit', 'CpuCfsQuota', 'PidsLimit')
        if any(info.get(field) is not True for field in required):
            raise ContainerError('Docker daemon cannot enforce all memory/swap/CPU/PID limits')
        if not any('seccomp' in item for item in info.get('SecurityOptions', [])):
            raise ContainerError('Docker daemon must enforce its default seccomp profile')
        image = self._check_image()
        return {'ready': True, 'endpoint': self.policy.endpoint, **image,
                'server_version': info.get('ServerVersion'),
                'cgroup_version': info.get('CgroupVersion'), 'storage_driver': info.get('Driver'),
                'resource_limits_supported': {field: True for field in required}}

    def run(self, source_root, *, job_id, attempt_id, cancel_event=None, ramdisk_workspace=None,
            source_modes=None, reservation=None):
        request_started = time.time()
        if not self.policy.enabled:
            raise ContainerError('Docker execution is disabled; coding acceptance cannot be claimed')
        record = self._consume_reservation(reservation or self.reserve_attempt(job_id, attempt_id,
            request_started_at=request_started), job_id, attempt_id)
        requested = record.get('request_started_at') or request_started
        reserved = record.get('reservation_started_at') or request_started
        receipt = {'schema_version': 1, 'kind': 'docker-test-execution', 'job_id': job_id,
                   'attempt_id': attempt_id, 'policy_sha256': self.policy.digest,
                   'image_id': self.policy.image, 'source': None, 'commands': [],
                   'started_at': request_started, 'passed': False, 'cleanup_verified': False, 'source_verified': False,
                   'input_source_verified': False, 'failure_scope': None,
                   'failure_category': None, 'quarantine_required': False}
        receipt.update(request_started_at=requested,reservation_started_at=reserved,
                       execution_started_at=request_started,
                       queue_wait_seconds=max(0,reserved-requested),
                       handoff_wait_seconds=max(0,request_started-reserved),
                       container_creation_path='background_pool_replenishment',
                       prepared_at=record.get('prepared_at'),
                       preparation_seconds=record.get('preparation_seconds'))
        cycle_started = None
        def remaining_cycle_seconds():
            remaining = 30. - (time.monotonic() - cycle_started)
            if remaining <= 0:
                receipt['failure_category'], receipt['quarantine_required'] = 'timeout', True
                raise ContainerError('Aggregate test execution and evidence collection exceeded 30 seconds')
            return remaining
        def cycle_call(arguments, *, timeout=30, **kwargs):
            return self._call(arguments, timeout=min(timeout, remaining_cycle_seconds()), **kwargs)
        try:
            archive, source = source_snapshot(Path(source_root), self.policy,
                                             ramdisk_workspace=ramdisk_workspace, source_modes=source_modes)
            receipt['source'] = source
            self.preflight()
            receipt['warm_pool_used'] = True
            container_id = record['container_id']
            receipt['container_id'] = container_id
            inspected = self._inspect_owned(record)
            receipt['limits'] = self._verify_limits(inspected)
            if not inspected['State']['Running']:
                raise ContainerError('Selected fresh container is not running')
            receipt['container_startup_seconds'] = time.time() - receipt['started_at']
            result = self._call(['exec', '-i', container_id, 'python', '-I', '-c', _EXTRACT,
                                 str(self.policy.max_source_mb * 1048576), str(self.policy.max_source_files)],
                                timeout=60, data=archive, cancel_event=cancel_event)
            if result.returncode == 137:
                receipt['failure_category'], receipt['quarantine_required'] = 'oom', True
                raise ContainerError('RAM snapshot transfer exceeded the container memory limit')
            if result.timed_out or result.output_exceeded:
                receipt['failure_category'], receipt['quarantine_required'] = ('timeout' if result.timed_out else 'output_limit'), True
                raise ContainerError('RAM snapshot transfer exceeded its execution bound')
            if result.cancelled:
                receipt['failure_category'] = 'cancelled'
                raise ContainerError('Container input transfer cancelled by controller')
            if result.returncode or result.stdout.decode().strip() != source['sha256']:
                raise ContainerError('RAM snapshot transfer failed independent digest verification')
            receipt['input_source_verified'] = True
            ready_at=time.time()
            receipt['execution_startup_seconds'] = ready_at-receipt['started_at']
            receipt['startup_seconds'] = ready_at-requested
            receipt['startup_sla_seconds'] = 1.5
            receipt['startup_clock_verified'] = (requested<=reserved<=ready_at and
                (reserved<=request_started<=ready_at if reservation else requested<=request_started<=reserved))
            receipt['startup_sla_met'] = receipt['startup_clock_verified'] and 0<=receipt['startup_seconds']<=1.5
            receipt['startup_violation_reason'] = (None if receipt['startup_sla_met'] else
                'controller_startup_clock_order_unverified' if not receipt['startup_clock_verified'] else
                'request_to_test_ready_exceeded_1_5_seconds_including_queue_and_handoff')
            cycle_started = time.monotonic()
            for index, command in enumerate(self.policy.commands):
                if cancel_event is not None and cancel_event.is_set():
                    receipt['failure_category'] = 'cancelled'
                    break
                argv = list(command.argv)
                report_path = f'/work/results/{index}.xml'
                assertion_path = f'/work/results/{index}-assertions.json'
                if command.kind == 'pytest':
                    from .pytest_assertions import ASSERTION_OBSERVER
                    # Isolated Python cannot import a project-supplied pytest.py shim.
                    argv = [argv[0], '-I', '-c', ASSERTION_OBSERVER, assertion_path, '/work/source', *argv[3:]]
                    # A stable root binds JUnit classnames to the controller's
                    # sealed source-relative module/class identities, even if
                    # operator arguments select a deeper test directory.
                    argv += ['--rootdir=/work/source', '--junitxml=' + report_path, '-p', 'no:cacheprovider']
                before = time.time()
                result = cycle_call(['exec', '--workdir', '/work/source', container_id, *argv],
                                    timeout=command.timeout_seconds, cancel_event=cancel_event)
                item = {'name': command.name, 'kind': command.kind, 'argv': argv,
                        'exit_code': result.returncode, 'elapsed_seconds': time.time() - before,
                        'stdout_sha256': _digest(result.stdout), 'stderr_sha256': _digest(result.stderr),
                        'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace'),
                        'timed_out': result.timed_out, 'cancelled': result.cancelled,
                        'output_exceeded': result.output_exceeded, 'passed': False}
                receipt['commands'].append(item)
                remaining = 30. - (time.monotonic() - cycle_started)
                inspected = self._inspect_owned(record, timeout=remaining) if remaining > 0 else None
                oom = bool(inspected and inspected.get('State', {}).get('OOMKilled')) or result.returncode == 137
                if oom:
                    receipt['failure_category'], receipt['quarantine_required'] = 'oom', True
                elif result.timed_out:
                    receipt['failure_category'], receipt['quarantine_required'] = 'timeout', True
                elif result.cancelled:
                    receipt['failure_category'] = 'cancelled'
                elif result.output_exceeded:
                    receipt['failure_category'], receipt['quarantine_required'] = 'output_limit', True
                elif result.returncode:
                    receipt['failure_category'] = 'tests_failed'
                if receipt['failure_category'] and receipt['failure_category'] != 'tests_failed':
                    break
                if command.kind == 'pytest':
                    report = cycle_call(['exec', container_id, 'python', '-I', '-c', _READ_RESULT,
                                         report_path, str(self.policy.junit_limit_bytes)],
                                        output_limit=self.policy.junit_limit_bytes)
                    if report.returncode or report.output_exceeded:
                        raise ContainerError('Actual JUnit report is missing or exceeds its limit')
                    item['junit_sha256'] = _digest(report.stdout)
                    item['junit'] = parse_junit(report.stdout)
                    item['junit_cases'] = junit_cases(report.stdout)
                    from .pytest_assertions import bind_assertion_evidence
                    assertions = cycle_call(['exec', container_id, 'python', '-I', '-c', _READ_RESULT,
                                             assertion_path, str(self.policy.junit_limit_bytes)],
                                            output_limit=self.policy.junit_limit_bytes)
                    if assertions.returncode or assertions.output_exceeded:
                        raise ContainerError('Actual pytest assertion evidence is missing or exceeds its limit')
                    item['assertions_sha256'] = _digest(assertions.stdout)
                    item['assertion_observer_sha256'] = _digest(ASSERTION_OBSERVER.encode())
                    bind_assertion_evidence(item['junit_cases'], assertions.stdout, source['files'])
                    if item['junit']['failures'] or item['junit']['errors']:
                        receipt['failure_category'] = 'tests_failed'
                if receipt['failure_category']:
                    break
                item['passed'] = True
            if receipt['failure_category'] in (None, 'tests_failed'):
                processes = cycle_call(['top', container_id, '-eo', 'pid,comm'])
                if processes.returncode or len(processes.stdout.decode().strip().splitlines()) != 2:
                    raise ContainerError('Test command left unowned background processes running')
                tree = cycle_call(['exec', container_id, 'python', '-I', '-c', _OUTPUT_TREE,
                                   str(self.policy.max_source_mb * 1048576), str(self.policy.max_source_files)])
                if tree.returncode or tree.output_exceeded:
                    raise ContainerError('Cannot establish bounded output tree evidence')
                output_files = json.loads(tree.stdout)
                receipt['output'] = {'sha256': _digest(_canonical(output_files)), 'files': output_files}
                if any(output_files.get(path) != entry for path, entry in source['files'].items()):
                    raise ContainerError('Test execution modified or removed input source files')
                _, final_source = source_snapshot(Path(source_root), self.policy, ramdisk_workspace=ramdisk_workspace, source_modes=source_modes)
                remaining_cycle_seconds()
                if final_source['sha256'] != source['sha256']:
                    raise ContainerError('Host source changed during test execution')
                receipt['source_verified'] = True
            receipt['passed'] = (len(receipt['commands']) == len(self.policy.commands)
                                 and all(item['passed'] for item in receipt['commands'])
                                 and receipt['failure_category'] is None)
        except Exception as error:
            if cycle_started is not None and time.monotonic()-cycle_started >= 30:
                receipt['failure_category'], receipt['quarantine_required'] = 'timeout', True
            elif receipt['failure_category'] in (None, 'tests_failed'):
                receipt['failure_category'] = 'container_contract'
            receipt['error'] = str(error)[:1024]
        finally:
            receipt['test_cycle_limit_seconds'] = 30
            receipt['test_cycle_seconds'] = None if cycle_started is None else time.monotonic() - cycle_started
            receipt['test_cycle_deadline_met'] = (cycle_started is not None
                and receipt['test_cycle_seconds'] <= 30 and receipt['failure_category'] != 'timeout')
            if cycle_started is not None and receipt['test_cycle_seconds'] > 30:
                receipt['passed'] = False
                receipt['failure_category'], receipt['quarantine_required'] = 'timeout', True
            try:
                self._remove(record)
                receipt['cleanup_verified'] = True
            except ContainerError as error:
                receipt['passed'] = False
                receipt['failure_category'], receipt['quarantine_required'] = 'cleanup', True
                receipt['error'] = str(error)[:1024]
                self._set(record['lease'], status='QUARANTINED')
            if receipt['failure_category'] is not None:
                receipt['failure_scope'] = ('code' if receipt['input_source_verified']
                    and receipt['failure_category'] in ('tests_failed', 'oom', 'timeout', 'output_limit')
                    and receipt['cleanup_verified'] else 'infrastructure')
            receipt['finished_at'] = time.time()
            receipt['receipt_sha256'] = _digest(_canonical(receipt))
            target = self.state_root / (record['lease'] + '.receipt.json')
            with target.open('xb') as stream:
                stream.write(_canonical(receipt))
                stream.flush()
                os.fsync(stream.fileno())
        return receipt
