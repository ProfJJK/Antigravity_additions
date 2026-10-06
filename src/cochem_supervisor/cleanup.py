"""Reconcile exact execution guards after native contained-tree shutdown.

This frozen supervisor module uses only the standard library. It neither
imports candidate pipeline code nor infers process death from a lease or PID.
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
