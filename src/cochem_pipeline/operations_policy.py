"""Protected operational evidence: reviewed archives and measured workload SLOs.

Archival is copy-and-verify only. This module never prunes live telemetry,
receipts, or their authority. Workload objectives remain unratified until an
operator records numerical targets against measured launch evidence.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
import math
import os
import queue
import threading
from pathlib import Path
import shutil
import sqlite3
import stat
import time
import uuid


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _ordinary(path, *, directory=False):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Operational evidence cannot follow symlinks or reparse points')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
        raise ValueError('Operational evidence requires ordinary files/directories')
    return path


def _private(root, *, create=True):
    root = Path(root).absolute()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ordinary(root, directory=True)
    if os.name == 'nt':
        from .windows import require_system, validate_private_directory
        require_system()
        validate_private_directory(root)
    elif root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise PermissionError('Operational evidence directory must be controller-owned mode 0700')
    return root


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class RetentionPolicy:
    review_id: str = 'conservative-archive-defaults-not-owner-ratified'
    archive_after_days: int = 90
    forecast_days: int = 30
    max_archive_bytes: int = 512*1024*1024
    prune_enabled: bool = False
    database_snapshots_on_explicit_request: bool = True

    def __post_init__(self):
        if not isinstance(self.review_id, str) or not 1 <= len(self.review_id) <= 256:
            raise ValueError('Retention requires a bounded review decision ID')
        for key, maximum in (('archive_after_days', 3650), ('forecast_days', 3650), ('max_archive_bytes', 2**40)):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Retention policy integer bounds are invalid')
        if self.database_snapshots_on_explicit_request is not True:
            raise ValueError('Database snapshots require an explicit archive request')
        if self.prune_enabled is not False:
            raise ValueError('Pruning requires a separate reviewed implementation; archival never deletes originals')


def storage_forecast(root, policy=None, *, previous=None, now=None, max_files=4096):
    """Scan only evidence families, excluding credentials and archive copies."""
    policy = policy or RetentionPolicy()
    root = _private(root,create=False)
    if type(max_files) is not int or not 1<=max_files<=65536:
        raise ValueError('Forecast file limit must be in 1..65536')
    observed = time.time() if now is None else now
    files, scanned, complete = [], 0, True
    for directory, directories, names in os.walk(root,followlinks=False):
        for name in directories:
            _ordinary(Path(directory)/name,directory=True)
        scanned += len(directories)+len(names)
        if scanned>max_files*4:
            complete=False
            break
        for name in names:
            path=Path(directory)/name
            relative=path.relative_to(root)
            if not (name.endswith(('.receipt.json','.db','.db-wal','.db-shm')) or 'telemetry' in relative.parts
                    or ('archives' in relative.parts and name=='manifest.json')):
                continue
            if len(files)>=max_files:
                complete=False
                break
            _ordinary(path)
            info=path.stat()
            files.append({'path':relative.as_posix(),'bytes':info.st_size,'modified_at':info.st_mtime,
                          'archived':'archives' in relative.parts,
                          'archive_eligible':'archives' not in relative.parts and (path.suffix=='.db' or observed-info.st_mtime>=policy.archive_after_days*86400)})
        if not complete:
            break
    total = sum(item['bytes'] for item in files)
    rate = None
    if complete and isinstance(previous, dict) and previous.get('scan_complete') is True and type(previous.get('observed_at')) in (int,float) and type(previous.get('total_bytes')) is int:
        interval = observed-previous['observed_at']
        if interval>=60:
            rate = max(0, (total-previous['total_bytes'])/interval)
    return {'schema':'cochem-retention/1', 'observed_at':observed, 'policy':asdict(policy),
            'total_bytes':total, 'live_bytes':sum(row['bytes'] for row in files if not row['archived']),
            'archive_bytes':sum(row['bytes'] for row in files if row['archived']), 'files':files, 'scan_complete':complete, 'scanned_entries':scanned, 'growth_bytes_per_second':rate,
            'projected_additional_bytes':math.ceil(rate*policy.forecast_days*86400) if rate is not None else None,
            'forecast_availability':'measured_interval' if rate is not None else 'requires_two_samples_at_least_60_seconds_apart',
            'pruning_supported':False}


def archive_evidence(root, relative_paths, *, policy=None, now=None):
    """Explicit protected archive: SQLite online backups, immutable file hashes.

    Only receipts and SQLite telemetry/ledger databases are allowed. Database
    hashes describe the consistent backup, not a racing live WAL file. A sealed
    manifest records exact source identity, snapshot kind and archive digest.
    Any copy failure leaves originals intact and publishes no manifest.
    """
    policy = policy or RetentionPolicy()
    root = _private(root)
    observed = time.time() if now is None else now
    if not isinstance(relative_paths, (list,tuple)) or not 1 <= len(relative_paths) <= 1024:
        raise ValueError('Archive requires 1..1024 explicit evidence paths')
    archive_root = _private(root/'archives')
    destination = _private(archive_root/uuid.uuid4().hex)
    entries, total, seen = [], 0, set()
    for name in relative_paths:
        if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\0' in name:
            raise ValueError('Archive evidence uses relative POSIX paths')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or 'archives' in relative.parts:
            raise ValueError('Archive paths must remain within live protected evidence')
        if relative.as_posix().casefold() in seen:
            raise ValueError('Archive paths must be unique')
        seen.add(relative.as_posix().casefold())
        source = _ordinary(root/relative)
        if not (source.name.endswith('.receipt.json') or source.suffix == '.db'):
            raise ValueError('Only immutable receipts and consistent SQLite database snapshots can be archived')
        before = source.stat()
        if source.suffix!='.db' and observed-before.st_mtime < policy.archive_after_days*86400:
            raise ValueError('Evidence is younger than the reviewed archival age')
        if source.stat().st_size+total>policy.max_archive_bytes:
            raise ValueError('Archive exceeds the reviewed byte budget')
        target = destination/relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if source.suffix == '.db':
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as db, closing(sqlite3.connect(target)) as backup:
                page_size=db.execute('PRAGMA page_size').fetchone()[0]
                deadline=time.monotonic()+30
                def progress(status, remaining, pages):
                    if time.monotonic()>deadline:
                        raise TimeoutError('Archive snapshot exceeded its 30-second copy deadline')
                    if pages*page_size+total>policy.max_archive_bytes:
                        raise ValueError('Archive database exceeds reviewed byte budget')
                db.backup(backup,pages=256,progress=progress)
                if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Archive database snapshot failed integrity validation')
            kind, source_hash = 'sqlite_online_backup', None
        else:
            source_hash = _hash(source)
            with source.open('rb') as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024*1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            kind = 'immutable_receipt_copy'
            after = source.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns) or source_hash != _hash(source):
                raise ValueError('Immutable source changed during archival')
        target.chmod(0o600)
        digest, size = _hash(target), target.stat().st_size
        total += size
        if total > policy.max_archive_bytes:
            raise ValueError('Archive exceeds the reviewed byte budget')
        if source_hash is not None and source_hash != digest:
            raise ValueError('Receipt archive digest differs from its immutable source')
        entries.append({'source_path':relative.as_posix(), 'archive_path':relative.as_posix(), 'kind':kind,
                        'bytes':size, 'source_sha256':source_hash, 'archive_sha256':digest,
                        'source_modified_at':before.st_mtime})
    manifest = {'schema':'cochem-evidence-archive/1', 'created_at':observed, 'policy':asdict(policy),
                'entries':entries, 'bytes':total, 'originals_preserved':True}
    data = _canonical(manifest)
    with (destination/'manifest.json').open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    (destination/'manifest.json').chmod(0o600)
    return {'archive_directory':str(destination), 'manifest_sha256':hashlib.sha256(data).hexdigest(), **manifest}


class WorkloadObjectives:
    """Durable bounded statistical view over genuine controller observations."""
    def __init__(self, root):
        self.root = _private(root)
        self.database = self.root/'objectives.db'
        self._queue = queue.Queue(maxsize=1024)
        self._async_lock = threading.Lock()
        self._async_thread = None
        self._closed = False
        self._dropped = 0
        self._async_error_count = 0
        if self.database.exists():
            _ordinary(self.database)
        with self._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS observations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,workload TEXT NOT NULL,operation TEXT NOT NULL,
                observed_at REAL NOT NULL,duration_seconds REAL NOT NULL,success INTEGER NOT NULL,
                evidence_id TEXT NOT NULL UNIQUE);
                CREATE INDEX IF NOT EXISTS workload_window ON observations(workload,observed_at);
                CREATE TABLE IF NOT EXISTS objectives(workload TEXT PRIMARY KEY, p95_seconds REAL NOT NULL,
                    error_budget_fraction REAL NOT NULL,review_id TEXT NOT NULL,evidence_id TEXT NOT NULL,
                    ratified_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS objective_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workload TEXT NOT NULL,p95_seconds REAL NOT NULL,error_budget_fraction REAL NOT NULL,
                    review_id TEXT NOT NULL,evidence_id TEXT NOT NULL,ratified_at REAL NOT NULL);''')

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA journal_mode=WAL')
        return closing_connection(db)

    @staticmethod
    def _validate_observation(workload, operation, duration_seconds, success, evidence_id, now):
        if workload not in ('interactive','background') or type(success) is not bool:
            raise ValueError('Workload class and success must be explicit')
        if type(duration_seconds) not in (int,float) or not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError('Observed duration must be finite and nonnegative')
        if any(not isinstance(value,str) or not 1 <= len(value) <= 256 for value in (operation,evidence_id)):
            raise ValueError('Observation requires bounded operation and evidence IDs')
        if now is not None and (type(now) not in (int,float) or not math.isfinite(now)):
            raise ValueError('Observation clock must be finite')

    def record(self, workload, operation, duration_seconds, success, evidence_id, *, now=None):
        self._validate_observation(workload,operation,duration_seconds,success,evidence_id,now)
        with self._connect() as db:
            db.execute('INSERT OR IGNORE INTO observations(workload,operation,observed_at,duration_seconds,success,evidence_id) VALUES(?,?,?,?,?,?)',
                       (workload,operation,time.time() if now is None else now,duration_seconds,int(success),evidence_id))

    def record_async(self, workload, operation, duration_seconds, success, evidence_id, *, now=None):
        """Nonblocking authenticated-request observations; losses stay visible."""
        self._validate_observation(workload,operation,duration_seconds,success,evidence_id,now)
        event=(workload,operation,duration_seconds,success,evidence_id,time.time() if now is None else now)
        with self._async_lock:
            if self._closed:
                self._dropped+=1
                return False
            if self._async_thread is None:
                self._async_thread=threading.Thread(target=self._flush_async,name='operational-observations',daemon=True)
                self._async_thread.start()
            try:
                self._queue.put_nowait(event)
                return True
            except queue.Full:
                self._dropped+=1
                return False

    def _flush_async(self):
        while not self._closed or not self._queue.empty():
            try:
                event=self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            batch=[event]
            while len(batch)<64:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                # One transaction per batch; values were validated before enqueue.
                with self._connect() as db:
                    db.executemany('INSERT OR IGNORE INTO observations(workload,operation,duration_seconds,success,evidence_id,observed_at) VALUES(?,?,?,?,?,?)',batch)
            except Exception:
                with self._async_lock:
                    self._async_error_count+=len(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()

    def close(self):
        with self._async_lock:
            self._closed=True
            thread=self._async_thread
        if thread is not None:
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError('Operational evidence queue did not drain before shutdown')

    def ratify(self, workload, p95_seconds, error_budget_fraction, review_id, evidence_id):
        if workload not in ('interactive','background') or any(not isinstance(value,str) or not 1<=len(value)<=256 for value in (review_id,evidence_id)):
            raise ValueError('Objective requires workload, review and launch evidence IDs')
        if any(type(value) not in (int,float) or not math.isfinite(value) for value in (p95_seconds,error_budget_fraction)) or p95_seconds<=0 or not 0<=error_budget_fraction<1:
            raise ValueError('Objective thresholds are invalid')
        with self._connect() as db:
            if db.execute('SELECT count(*) FROM observations WHERE workload=?',(workload,)).fetchone()[0]<64:
                raise ValueError('Objective ratification requires at least 64 measured workload observations')
            values=(workload,p95_seconds,error_budget_fraction,review_id,evidence_id,time.time())
            db.execute('INSERT OR REPLACE INTO objectives VALUES(?,?,?,?,?,?)',values)
            db.execute('INSERT INTO objective_revisions(workload,p95_seconds,error_budget_fraction,review_id,evidence_id,ratified_at) VALUES(?,?,?,?,?,?)',values)

    def snapshot(self, *, now=None, window_seconds=86400):
        if type(window_seconds) is not int or not 60 <= window_seconds <= 2678400:
            raise ValueError('Objective window must be between 60 seconds and 31 days')
        now = time.time() if now is None else now
        result = {'schema':'cochem-workload-objectives/1', 'window_seconds':window_seconds,
                  'correctness_and_containment_remain_hard':True,'workloads':{},
                  'latency_scopes':{'interactive':'authenticated HTTP handler through response write; excludes MCP/client transport',
                    'background':'controller attempt execution through cleanup; excludes time waiting in the job board'},
                  'observation_queue':{'pending':self._queue.unfinished_tasks,'dropped':self._dropped,
                    'write_errors':self._async_error_count,'scope':'current_controller_process',
                    'complete':not(self._queue.unfinished_tasks or self._dropped or self._async_error_count)}}
        with self._connect() as db:
            for workload in ('interactive','background'):
                rows = db.execute('SELECT duration_seconds,success FROM observations WHERE workload=? AND observed_at>=? AND observed_at<=? ORDER BY id DESC LIMIT 4097',
                                  (workload,now-window_seconds,now)).fetchall()
                objective = db.execute('SELECT * FROM objectives WHERE workload=?',(workload,)).fetchone()
                durations = sorted(row['duration_seconds'] for row in rows[:4096])
                count = len(durations)
                failures = sum(not row['success'] for row in rows[:4096])
                p95 = durations[math.ceil(.95*count)-1] if count else None
                threshold = objective['p95_seconds'] if objective else None
                bad = sum(not row['success'] or row['duration_seconds']>threshold for row in rows[:4096]) if threshold else None
                result['workloads'][workload] = {'samples':count, 'sample_overflow':len(rows)>4096,
                    'p95_seconds':p95,'failures':failures, 'failure_fraction':failures/count if count else None,
                    'objective':dict(objective) if objective else None,
                    'status':'awaiting_owner_ratified_launch_targets' if not objective else 'insufficient_samples' if not count else
                             'incomplete_observations' if not result['observation_queue']['complete'] or len(rows)>4096 else
                             'within_objective' if p95<=threshold and bad/count<=objective['error_budget_fraction'] else 'objective_exceeded',
                    'bad_event_fraction':bad/count if count and bad is not None else None,
                    'error_budget_remaining_events':max(0,math.floor(count*objective['error_budget_fraction'])-bad) if objective and count else None}
        return result


class closing_connection:
    def __init__(self, connection):
        self.connection=connection
    def __enter__(self):
        return self.connection
    def __exit__(self, exc_type, exc, tb):
        try:
            self.connection.commit() if exc_type is None else self.connection.rollback()
        finally:
            self.connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root',required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    forecast=sub.add_parser('forecast')
    forecast.add_argument('--previous',help='Prior saved forecast JSON, enabling measured growth after at least 60 seconds')
    sub.add_parser('objectives')
    ratify=sub.add_parser('ratify-objective')
    ratify.add_argument('--workload',choices=('interactive','background'),required=True)
    ratify.add_argument('--p95-seconds',type=float,required=True)
    ratify.add_argument('--error-budget-fraction',type=float,required=True)
    ratify.add_argument('--review-id',required=True)
    ratify.add_argument('--launch-evidence-id',required=True)
    archive=sub.add_parser('archive')
    archive.add_argument('paths',nargs='+')
    args=parser.parse_args()
    if args.action in ('objectives','ratify-objective'):
        ledger=WorkloadObjectives(_private(args.private_root,create=False)/'operations')
        if args.action=='ratify-objective':
            ledger.ratify(args.workload,args.p95_seconds,args.error_budget_fraction,args.review_id,args.launch_evidence_id)
        result=ledger.snapshot()
    else:
        previous=None
        if args.action=='forecast' and args.previous:
            path=_ordinary(args.previous)
            if path.stat().st_size>4*1024*1024:
                raise ValueError('Previous forecast exceeds 4 MiB')
            previous=json.loads(path.read_text(encoding='utf-8'))
        result=storage_forecast(args.private_root,previous=previous) if args.action=='forecast' else archive_evidence(args.private_root,args.paths)
    print(json.dumps(result,sort_keys=True,indent=2))


if __name__=='__main__':
    main()
