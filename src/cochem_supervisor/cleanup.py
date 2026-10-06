"""Reconcile exact execution guards after native contained-tree shutdown.

Native guard reconciliation uses only the standard library. External container
cleanup uses the supervisor's protected installed Docker actuator; neither path
imports candidate pipeline code or infers process death from a lease or PID.
The caller supplies the receipt from the retained Windows Job Object check.
"""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time

from .releases import _plain_ancestors, _stat_plain


def reconcile_stopped_containment(database: Path, receipt: dict) -> int:
    """Clear native guards; record controller death for external Docker guards.

    Closed-record-only and previous-boot stops deliberately carry no scope and
    clear nothing here. The launcher's own verified final teardown can also
    reconcile its nonce before returning to Task Scheduler. Reboot recovery
    is a separate native controller proof.
    The nonce is fresh for every launcher, so a subsequently started Warden's
    guards cannot match this receipt. Job leases and retry budgets are untouched.
    """
    scope = receipt.get('stopped_containment_id')
    if scope is None:
        return 0
    boot = receipt.get('stopped_boot_id')
    if (not isinstance(scope, str) or not re.fullmatch(r'[0-9a-f]{32}', scope)
            or type(boot) is not int or boot <= 0
            or receipt.get('tree_exit_verified') is not True
            or not (receipt.get('scheduling_disabled') is True
                    or receipt.get('launcher_exit_verified') is True)):
        raise ValueError('Cleanup reconciliation requires exact verified native containment proof')
    path = Path(database).absolute()
    _plain_ancestors(path.parent)
    if not path.exists() and not path.is_symlink():
        # A failed startup may exit before the controller creates any state.
        return 0
    _stat_plain(path)
    for suffix in ('-wal', '-shm'):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _stat_plain(sidecar)
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='pipeline_execution_cleanup'").fetchone():
                return 0
            columns = {row['name'] for row in db.execute('PRAGMA table_info(pipeline_execution_cleanup)')}
            external = " OR (j.kind='CODE_TEST' AND c.owner_containment_id=?)" if 'owner_containment_id' in columns else ''
            parameters = (scope,scope,boot) if external else (scope,boot)
            rows = db.execute('''SELECT c.job_id,c.attempt_id,c.fencing_token,j.workflow_id,j.kind
                FROM pipeline_execution_cleanup c JOIN pipeline_jobs j ON j.job_id=c.job_id
                WHERE c.cleared_at IS NULL AND (c.containment_id=?'''+external+''') AND c.boot_id=?
                ORDER BY c.created_at,c.job_id LIMIT 257''', parameters).fetchall()
            if len(rows) > 256:
                raise ValueError('Contained cleanup evidence exceeds the bounded reconciliation limit')
            now = time.time()
            cleared = 0
            for row in rows:
                if row['kind']=='CODE_TEST':
                    # Docker's daemon is outside the Warden Job Object. Empty
                    # native containment proves no further client work can be
                    # issued, but never proves container removal. Preserve the
                    # external guard until the new controller verifies removal.
                    db.execute('''CREATE TABLE IF NOT EXISTS pipeline_execution_owner_stops (
                        job_id TEXT NOT NULL, attempt_id TEXT NOT NULL, fencing_token INTEGER NOT NULL,
                        containment_id TEXT NOT NULL, boot_id INTEGER NOT NULL, verified_at REAL NOT NULL,
                        PRIMARY KEY(job_id,attempt_id,fencing_token))''')
                    inserted = db.execute('''INSERT OR IGNORE INTO pipeline_execution_owner_stops
                        VALUES(?,?,?,?,?,?)''',(row['job_id'],row['attempt_id'],row['fencing_token'],scope,boot,now)).rowcount
                    if inserted:
                        details = json.dumps({'attempt_id':row['attempt_id'],'fencing_token':row['fencing_token'],
                            'containment_id':scope,'boot_id':boot,'proof':'retained_native_job_empty',
                            'container_cleanup_required':True},sort_keys=True,separators=(',',':'))
                        db.execute('''INSERT INTO pipeline_events(workflow_id,job_id,timestamp,event,details_json)
                            VALUES(?,?,?,?,?)''',(row['workflow_id'],row['job_id'],now,'EXECUTION_OWNER_STOPPED_BY_SUPERVISOR',details))
                    continue
                db.execute('''UPDATE pipeline_execution_cleanup SET cleared_at=?
                    WHERE job_id=? AND attempt_id=? AND containment_id=? AND boot_id=? AND cleared_at IS NULL''',
                    (now, row['job_id'], row['attempt_id'], scope, boot))
                details = json.dumps({'attempt_id': row['attempt_id'], 'fencing_token': row['fencing_token'],
                    'containment_id': scope, 'boot_id': boot, 'proof': 'retained_native_job_empty'},
                    sort_keys=True, separators=(',', ':'))
                db.execute('''INSERT INTO pipeline_events(workflow_id,job_id,timestamp,event,details_json)
                    VALUES(?,?,?,?,?)''', (row['workflow_id'], row['job_id'], now,
                    'EXECUTION_CLEANUP_VERIFIED_BY_SUPERVISOR', details))
                cleared += 1
            return cleared


def quiesce_external_container_runner(runner, *, stop_receipt):
    """Drain only exact protected leases after native controller shutdown.

    This function measures the actual Docker engine. Labels alone never grant
    deletion authority; DockerRunner verifies each object's retained lease,
    owner, name, image and full container ID. Unknown or uncertain objects hold
    failover capacity. Database tombstones and all job attempts are preserved.
    """
    from cochem_pipeline.containers import ContainerCleanupError
    if (not isinstance(stop_receipt, dict) or stop_receipt.get('tree_exit_verified') is not True
            or stop_receipt.get('scheduling_disabled') is not True):
        raise ContainerCleanupError('External cleanup requires verified stopped native scheduling and process tree')
    import hashlib
    info = runner._json(['info', '--format', '{{json .}}'])
    engine_id = info.get('ID')
    if not isinstance(engine_id, str) or not engine_id or len(engine_id) > 256:
        raise ContainerCleanupError('External cleanup requires a bounded independent Docker engine identity')
    runner._daemon_identity = engine_id
    runner.set_capacity(0)
    discoveries = runner._discover_owned()
    before = runner.census()
    if discoveries or before['unknown_owned'] or before['owned'] > 256:
        raise ContainerCleanupError('Unknown external Docker ownership prevents repair admission')
    removed = []
    for record in before['containers']:
        runner._remove(record)
        removed.append(record['lease'])
    # A second physical census detects a late daemon response or a tombstone
    # that rematerialized after removal; an empty registry alone is insufficient.
    discoveries = runner._discover_owned()
    after = runner.census()
    if discoveries or after['owned'] or after['unknown_owned'] or after['quarantined']:
        raise ContainerCleanupError('External Docker cleanup remains unverified; repair capacity stays held')
    return {'schema': 'cochem-external-quiescence/4.2.7', 'checked_at': time.time(),
            'cleanup_verified': True, 'enabled': True, 'endpoint': runner.policy.endpoint,
            'engine_id': engine_id, 'owner': runner.owner, 'policy_sha256': runner.policy.digest,
            'stop_receipt_sha256': hashlib.sha256(json.dumps(stop_receipt, sort_keys=True,
                separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
            'previous_owned': before['owned'], 'removed_leases': removed,
            'remaining_owned': after['owned'], 'published_capacity': after['published_capacity'],
            'prepared_pool_drained': before['warm'], 'job_board_modified': False}


def ensure_external_containers_quiescent(config, *, stop_receipt):
    """Privileged production boundary for the independently installed supervisor.

    Imports resolve inside the supervisor's frozen installation, never inside
    the release under repair. Missing registries and unreachable engines remain
    explicit holds; they cannot be converted to claims of available capacity.
    """
    from cochem_pipeline import windows as native
    from cochem_pipeline.container_policy import DockerPolicy
    from cochem_pipeline.containers import DockerRunner, ContainerCleanupError
    from .windows import repair_docker_endpoint
    native.require_system()
    if (not isinstance(stop_receipt, dict) or stop_receipt.get('tree_exit_verified') is not True
            or stop_receipt.get('scheduling_disabled') is not True):
        raise ContainerCleanupError('External cleanup requires verified stopped native scheduling and process tree')
    filename = Path(config['pipeline_config'])
    native.validate_code_path(filename)
    pipeline = json.loads(filename.read_text(encoding='utf-8-sig'))
    endpoint = repair_docker_endpoint(pipeline)
    root = Path(pipeline['private_root'])
    if root != Path(config['pipeline_private_root']):
        raise ContainerCleanupError('Configured pipeline private state changed before external cleanup')
    native.validate_private_directory(root)
    state_root = root / 'containers'
    database = state_root / 'containers.db'
    if endpoint is None:
        if database.exists():
            raise ContainerCleanupError('Existing Docker ownership requires an enabled reviewed policy for physical cleanup')
        return {'schema': 'cochem-external-quiescence/4.2.7', 'enabled': False,
                'cleanup_verified': True, 'remaining_owned': 0,
                'scope': 'Docker disabled with no existing registered execution plane', 'checked_at': time.time()}
    if not database.is_file():
        raise ContainerCleanupError('Protected Docker ownership registry is missing; external capacity cannot be verified')
    _plain_ancestors(state_root)
    _stat_plain(database)
    native.validate_private_directory(state_root)
    native.validate_private_path(database)
    policy = DockerPolicy.from_dict(pipeline['docker'])
    native.validate_code_path(policy.executable)
    deadline = time.monotonic() + 60

    class DeadlineCleanupRunner(DockerRunner):
        def _call(self, arguments, *, timeout=30, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContainerCleanupError('External cleanup exceeded its bounded deadline; repair admission remains held')
            return super()._call(arguments, timeout=min(timeout, remaining), **kwargs)

    runner = DeadlineCleanupRunner(policy, state_root, trusted_operator=pipeline.get('operator_name'),
                                   host_boot_id=native.current_boot_identity())
    return quiesce_external_container_runner(runner, stop_receipt=stop_receipt)
