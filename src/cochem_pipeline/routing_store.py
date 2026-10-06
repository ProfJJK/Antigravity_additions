"""SQLite routing primitives. Callers hold the job board's short write lock.

Reservations are controller records, never model claims. Availability delays do
not consume dispatches or ordinary task-failure budgets. Policy snapshots and
selection evidence remain stable across configuration changes and restarts.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_routing_workflows (
 workflow_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)), max_dispatches INTEGER,
 CHECK(max_dispatches IS NULL OR max_dispatches>0)
);
CREATE TABLE IF NOT EXISTS pipeline_routing_jobs (
 job_id TEXT PRIMARY KEY REFERENCES pipeline_jobs(job_id),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 score_json TEXT NOT NULL CHECK(json_valid(score_json)),
 candidates_json TEXT NOT NULL CHECK(json_valid(candidates_json)),
 cursor INTEGER NOT NULL DEFAULT 0, cycle INTEGER NOT NULL DEFAULT 0,
 next_eligible_at REAL NOT NULL DEFAULT 0, expires_at REAL,
 failure_count INTEGER NOT NULL DEFAULT 0, dispatches INTEGER NOT NULL DEFAULT 0,
 max_dispatches INTEGER, state TEXT NOT NULL DEFAULT 'READY', wait_reason TEXT,
 created_at REAL NOT NULL,
 CHECK(cursor>=0 AND cycle>=0 AND failure_count>=0 AND dispatches>=0),
 CHECK(max_dispatches IS NULL OR max_dispatches>0),
 CHECK(state IN ('READY','WAITING','ACTIVE','COMPLETED','FAILED','BLOCKED'))
);
CREATE TABLE IF NOT EXISTS pipeline_route_reservations (
 reservation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id),
 attempt_id TEXT NOT NULL UNIQUE, fencing_token INTEGER NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, model_key TEXT NOT NULL,
 quota_pool TEXT NOT NULL, worker_slot TEXT, candidate_index INTEGER NOT NULL,
 cycle INTEGER NOT NULL, route_json TEXT NOT NULL CHECK(json_valid(route_json)),
 reserved_at REAL NOT NULL, released_at REAL, release_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_one_route_active ON pipeline_route_reservations(job_id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS pipeline_route_model_active ON pipeline_route_reservations(model_key,released_at);
CREATE INDEX IF NOT EXISTS pipeline_route_pool_active ON pipeline_route_reservations(quota_pool,released_at);
CREATE TABLE IF NOT EXISTS pipeline_route_holds (
 scope TEXT NOT NULL CHECK(scope IN ('model','provider','pool')), resource_key TEXT NOT NULL,
 until_at REAL NOT NULL, reason TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(scope,resource_key)
);
CREATE TABLE IF NOT EXISTS pipeline_execution_cleanup (
 job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id), attempt_id TEXT NOT NULL,
 fencing_token INTEGER NOT NULL, worker_slot TEXT, pid INTEGER,
 reason TEXT NOT NULL, quarantined INTEGER NOT NULL DEFAULT 0 CHECK(quarantined IN (0,1)),
 created_at REAL NOT NULL, cleared_at REAL, boot_id INTEGER CHECK(boot_id IS NULL OR boot_id>0),
 observed_boot_id INTEGER CHECK(observed_boot_id IS NULL OR observed_boot_id>0), containment_id TEXT,
 PRIMARY KEY(job_id,attempt_id), CHECK(pid IS NULL OR pid>0)
);
CREATE TRIGGER IF NOT EXISTS pipeline_routing_policy_immutable BEFORE UPDATE OF policy_json,score_json,candidates_json,created_at,max_dispatches,expires_at ON pipeline_routing_jobs
BEGIN SELECT RAISE(ABORT,'captured routing policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_workflow_routing_immutable BEFORE UPDATE ON pipeline_routing_workflows
BEGIN SELECT RAISE(ABORT,'captured workflow routing policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_workflow_routing_no_delete BEFORE DELETE ON pipeline_routing_workflows
BEGIN SELECT RAISE(ABORT,'captured workflow routing policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_job_routing_no_delete BEFORE DELETE ON pipeline_routing_jobs
BEGIN SELECT RAISE(ABORT,'captured job routing policy is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_route_reservation_owner BEFORE INSERT ON pipeline_route_reservations
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM pipeline_jobs j WHERE j.job_id=NEW.job_id
   AND j.status='IN_PROGRESS' AND j.attempt_id=NEW.attempt_id AND j.fencing_token=NEW.fencing_token)
 THEN RAISE(ABORT,'route reservation requires current job lease') END;
END;
CREATE TRIGGER IF NOT EXISTS pipeline_route_identity_immutable BEFORE UPDATE OF reservation_id,job_id,attempt_id,fencing_token,provider,model,model_key,quota_pool,worker_slot,candidate_index,cycle,route_json,reserved_at ON pipeline_route_reservations
BEGIN SELECT RAISE(ABORT,'route reservation identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_route_no_delete BEFORE DELETE ON pipeline_route_reservations
BEGIN SELECT RAISE(ABORT,'route reservation history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_execution_identity_immutable BEFORE UPDATE OF job_id,attempt_id,fencing_token,worker_slot,boot_id,observed_boot_id,containment_id,created_at ON pipeline_execution_cleanup
BEGIN SELECT RAISE(ABORT,'execution cleanup identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_execution_no_delete BEFORE DELETE ON pipeline_execution_cleanup
BEGIN SELECT RAISE(ABORT,'execution cleanup proof history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_execution_proof_immutable BEFORE UPDATE OF cleared_at ON pipeline_execution_cleanup
WHEN OLD.cleared_at IS NOT NULL AND NEW.cleared_at IS NOT OLD.cleared_at
BEGIN SELECT RAISE(ABORT,'verified execution cleanup proof is immutable'); END;
CREATE TRIGGER IF NOT EXISTS pipeline_route_receipt_binding BEFORE INSERT ON pipeline_outputs
WHEN EXISTS(SELECT 1 FROM pipeline_routing_jobs WHERE job_id=NEW.job_id)
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM pipeline_route_reservations r WHERE r.job_id=NEW.job_id
  AND r.attempt_id=NEW.attempt_id AND r.fencing_token=NEW.fencing_token AND r.released_at IS NULL
  AND json_extract(NEW.receipt_json,'$.provider')=r.provider
  AND json_extract(NEW.receipt_json,'$.requested_model')=r.model
  AND json_extract(NEW.receipt_json,'$.requested_effort') IS json_extract(r.route_json,'$.reasoning_effort')
  AND json_extract(NEW.receipt_json,'$.attempt_id')=r.attempt_id
  AND json_type(NEW.receipt_json,'$.fencing_token')='integer'
  AND json_extract(NEW.receipt_json,'$.fencing_token')=r.fencing_token
  AND json_extract(NEW.receipt_json,'$.job_id')=r.job_id
  AND json_extract(NEW.receipt_json,'$.worker_slot') IS r.worker_slot
  AND json_extract(NEW.receipt_json,'$.route_reservation_id')=r.reservation_id)
 THEN RAISE(ABORT,'completion receipt does not match reserved route') END;
END;
"""


AVAILABILITY = frozenset({'quota','auth','busy','backlog','provider','resource','context'})


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def policy_dict(policy):
    from .routing import load_routing_policy
    return load_routing_policy(policy.as_dict() if hasattr(policy, 'as_dict') else policy).as_dict()


def capture_workflow(conn, workflow_id, policy, max_dispatches=None):
    if max_dispatches is not None and (type(max_dispatches) is not int or not 1 <= max_dispatches <= 1000000):
        raise ValueError('max_dispatches must be an integer from 1 to 1000000')
    existing = conn.execute('SELECT * FROM pipeline_routing_workflows WHERE workflow_id=?', (workflow_id,)).fetchone()
    if existing:
        if max_dispatches is not None and existing['max_dispatches'] != max_dispatches:
            raise ValueError('Existing workflow dispatch budget is immutable')
        return
    if policy is not None:
        snapshot = policy_dict(policy)
        configured_limit = snapshot.get('max_dispatches',0)
        if max_dispatches is not None and configured_limit and max_dispatches>configured_limit:
            raise ValueError('Workflow max_dispatches may only lower the configured dispatch budget')
        max_dispatches = max_dispatches if max_dispatches is not None else configured_limit or None
        conn.execute('INSERT INTO pipeline_routing_workflows VALUES(?,?,?)',
                     (workflow_id, encoded(snapshot), max_dispatches))


def ensure_job(conn, job_id):
    row = conn.execute('''SELECT j.*,w.policy_json,w.max_dispatches FROM pipeline_jobs j
        JOIN pipeline_routing_workflows w ON w.workflow_id=j.workflow_id WHERE j.job_id=?''', (job_id,)).fetchone()
    if row is None or row['kind'] == 'MACRO_PLANNING_REQUEST' or (row['kind']=='SYNTHESIS' and row['status']=='BLOCKED'):
        return
    if conn.execute('SELECT 1 FROM pipeline_routing_jobs WHERE job_id=?', (job_id,)).fetchone():
        return
    from .routing import load_routing_policy, score_task
    policy = load_routing_policy(json.loads(row['policy_json']))
    score = score_task(row['kind'], json.loads(row['payload_json']))
    candidates = [target.as_dict() for target in policy.candidates(score['score'], row['kind'])]
    if not candidates:
        raise ValueError('Routing policy must provide at least one target')
    now = time.time()
    seconds = getattr(policy, 'max_routing_seconds', 0)
    conn.execute('''INSERT INTO pipeline_routing_jobs(job_id,policy_json,score_json,candidates_json,
        expires_at,max_dispatches,created_at,failure_count,dispatches) VALUES(?,?,?,?,?,?,?,?,?)''',
        (job_id, row['policy_json'], encoded(score), encoded(candidates), now + seconds if seconds else None,
         row['max_dispatches'], now, row['attempts'], row['attempts']))


def get_state(conn, job_id):
    row = conn.execute('SELECT * FROM pipeline_routing_jobs WHERE job_id=?', (job_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for source, target in (('policy_json','policy'),('score_json','score_details'),('candidates_json','candidates')):
        result[target] = json.loads(result.pop(source))
    result['policy_digest'] = digest(result['policy'])
    result['policy_hash'] = result['policy_digest']
    return result


def get_route(conn, job_id):
    row = conn.execute('''SELECT route_json FROM pipeline_route_reservations WHERE job_id=?
        ORDER BY fencing_token DESC,rowid DESC LIMIT 1''', (job_id,)).fetchone()
    return json.loads(row['route_json']) if row else None


def event(conn, job, name, **details):
    conn.execute('INSERT INTO pipeline_events(workflow_id,job_id,timestamp,event,details_json) VALUES(?,?,?,?,?)',
                 (job['workflow_id'], job['job_id'], time.time(), name, encoded(details)))


def release(conn, job_id, reason, state=None):
    conn.execute('UPDATE pipeline_route_reservations SET released_at=?,release_reason=? WHERE job_id=? AND released_at IS NULL',
                 (time.time(), reason, job_id))
    if state:
        conn.execute('UPDATE pipeline_routing_jobs SET state=? WHERE job_id=?', (state, job_id))


def release_workflow(conn, workflow_id, reason):
    for row in conn.execute("SELECT job_id,status FROM pipeline_jobs WHERE workflow_id=?", (workflow_id,)).fetchall():
        release(conn, row['job_id'], reason, 'COMPLETED' if row['status']=='COMPLETED' else 'FAILED')


def guard_execution(conn, job, slot, boot_id=None, observed_boot_id=None, containment_id=None):
    conn.execute('''INSERT OR IGNORE INTO pipeline_execution_cleanup
        (job_id,attempt_id,fencing_token,worker_slot,reason,created_at,boot_id,observed_boot_id,containment_id)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (job['job_id'],job['attempt_id'],job['fencing_token'],slot,'Native execution cleanup is not yet confirmed',
         time.time(),boot_id,observed_boot_id,containment_id))


def cleanup_barriers(conn):
    return [dict(row) for row in conn.execute('''SELECT c.* FROM pipeline_execution_cleanup c
        WHERE c.cleared_at IS NULL AND (c.quarantined=1 OR NOT EXISTS (
          SELECT 1 FROM pipeline_jobs j WHERE j.job_id=c.job_id AND j.status='IN_PROGRESS'
          AND j.attempt_id=c.attempt_id AND j.fencing_token=c.fencing_token AND j.lease_expires_at>?))
        ORDER BY c.created_at,c.job_id''',(time.time(),))]


def execution_identity(conn,job_id,attempt_id,fencing_token,worker_slot=None):
    if not isinstance(attempt_id,str) or not attempt_id or type(fencing_token) is not int:
        raise ValueError('Execution cleanup requires an exact attempt and integer fencing token')
    row = conn.execute('SELECT * FROM pipeline_execution_cleanup WHERE job_id=? AND attempt_id=?',(job_id,attempt_id)).fetchone()
    if row is None:
        row = conn.execute('SELECT * FROM pipeline_route_reservations WHERE job_id=? AND attempt_id=?',(job_id,attempt_id)).fetchone()
    if row is None:
        row = conn.execute('''SELECT j.*,w.slot AS worker_slot FROM pipeline_jobs j LEFT JOIN pipeline_worker_ownership w USING(job_id)
            WHERE j.job_id=? AND j.attempt_id=?''',(job_id,attempt_id)).fetchone()
    if row is None or row['fencing_token']!=fencing_token:
        raise ValueError('Unknown or mismatched execution cleanup identity')
    slot = row['worker_slot']
    if slot is not None and worker_slot is not None and worker_slot!=slot:
        raise ValueError('Execution cleanup worker identity does not match')
    return slot if slot is not None else worker_slot


def quarantine_execution(conn,job,attempt_id,fencing_token,worker_slot,reason,pid):
    slot = execution_identity(conn,job['job_id'],attempt_id,fencing_token,worker_slot)
    if not isinstance(reason,str) or not reason.strip() or '\x00' in reason:
        raise ValueError('Execution quarantine requires a nonempty reason')
    if pid is not None and (type(pid) is not int or pid<=0):
        raise ValueError('Execution quarantine PID must be positive')
    previous = conn.execute('SELECT * FROM pipeline_execution_cleanup WHERE job_id=? AND attempt_id=?',(job['job_id'],attempt_id)).fetchone()
    if previous is not None and (previous['cleared_at'] is not None or
        (previous['quarantined']==1 and previous['reason']==reason and (pid is None or pid==previous['pid']))):
        return False
    conn.execute('''INSERT INTO pipeline_execution_cleanup(job_id,attempt_id,fencing_token,worker_slot,pid,reason,quarantined,created_at)
        VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(job_id,attempt_id) DO UPDATE SET reason=excluded.reason,
        pid=coalesce(excluded.pid,pid),quarantined=1 WHERE cleared_at IS NULL''',
        (job['job_id'],attempt_id,fencing_token,slot,pid,reason,time.time()))
    event(conn,job,'EXECUTION_QUARANTINED',attempt_id=attempt_id,fencing_token=fencing_token,reason=reason,worker_slot=slot,pid=pid)
    return True


def clear_execution(conn,job,attempt_id,fencing_token):
    slot = execution_identity(conn,job['job_id'],attempt_id,fencing_token)
    existing = conn.execute('SELECT cleared_at FROM pipeline_execution_cleanup WHERE job_id=? AND attempt_id=?',(job['job_id'],attempt_id)).fetchone()
    if existing is not None and existing['cleared_at'] is not None:
        return False
    now = time.time()
    conn.execute('''INSERT INTO pipeline_execution_cleanup(job_id,attempt_id,fencing_token,worker_slot,reason,created_at,cleared_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id,attempt_id) DO UPDATE SET cleared_at=excluded.cleared_at''',
        (job['job_id'],attempt_id,fencing_token,slot,'Native process tree/profile closure verified by controller',now,now))
    event(conn,job,'EXECUTION_CLEANUP_CONFIRMED',attempt_id=attempt_id,fencing_token=fencing_token,worker_slot=slot)
    return True


def set_hold(conn, scope, key, seconds, reason):
    if scope not in {'model','provider','pool'} or not isinstance(key,str) or not key or '\x00' in key:
        raise ValueError('Invalid routing hold scope/key')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400:
        raise ValueError('Routing hold seconds must be finite within 0..86400')
    if not isinstance(reason,str) or not reason or '\x00' in reason:
        raise ValueError('Routing hold reason must be nonempty')
    now = time.time()
    conn.execute('''INSERT INTO pipeline_route_holds VALUES(?,?,?,?,?) ON CONFLICT(scope,resource_key)
        DO UPDATE SET until_at=max(until_at,excluded.until_at),reason=excluded.reason,updated_at=excluded.updated_at''',
        (scope, key, now+seconds, reason, now))


def _backoff(conn, job, state):
    from .routing import load_routing_policy
    policy, now = load_routing_policy(state['policy']), time.time()
    cycle = state['cycle'] + 1
    delay = policy.backoff_delay(cycle,job['job_id'])
    conn.execute("UPDATE pipeline_routing_jobs SET state='WAITING',cycle=?,cursor=0,next_eligible_at=?,wait_reason='all_candidates_unavailable' WHERE job_id=?",
                 (cycle,now+delay,job['job_id']))
    event(conn,job,'ROUTING_BACKOFF',cycle=cycle,next_eligible_at=now+delay,delay_seconds=delay)


def select(conn, job, current_policy=None):
    """Return a target or persist a bounded availability wait, without dispatch."""
    ensure_job(conn,job['job_id'])
    state = get_state(conn,job['job_id'])
    if state is None:
        return None, False
    now = time.time()
    if state['state']=='BLOCKED' or state['next_eligible_at']>now:
        return None, True
    policy = state['policy']
    cycle_limit = policy.get('max_routing_cycles',0)
    expires = state['expires_at']
    if (expires is not None and now>=expires) or (cycle_limit and state['cycle']>=cycle_limit):
        conn.execute("UPDATE pipeline_routing_jobs SET state='BLOCKED',wait_reason='routing_operator_limit' WHERE job_id=?", (job['job_id'],))
        conn.execute("UPDATE pipeline_jobs SET status='BLOCKED',error='Routing reached configured operator limit',updated_at=? WHERE job_id=?", (now,job['job_id']))
        event(conn,job,'ROUTING_BLOCKED',reason='routing_operator_limit')
        return None, True
    if state['max_dispatches'] is not None and state['dispatches']>=state['max_dispatches']:
        return {'exhausted': 'Dispatch budget exhausted'}, True
    if state['failure_count']>=job['max_attempts']:
        return {'exhausted': 'Task failure budget exhausted'}, True
    targets = state['candidates']
    for index in range(state['cursor'],len(targets)):
        target = targets[index]
        key,provider,pool = target['key'],target['provider'],target['quota_pool']
        reason = None
        for scope,value in (('model',key),('provider',provider),('pool',pool)):
            held = conn.execute('SELECT reason FROM pipeline_route_holds WHERE scope=? AND resource_key=? AND until_at>?', (scope,value,now)).fetchone()
            if held:
                reason = held['reason']; break
        caps = (("model_key", key, target['max_concurrency']),
                ("provider",provider,policy['provider_limits'][provider]['max_concurrency']),
                ("quota_pool",pool,policy['quota_pool_limits'][pool]))
        if reason is None:
            for column,value,limit in caps:
                count = conn.execute(f'SELECT count(*) FROM pipeline_route_reservations WHERE {column}=? AND released_at IS NULL', (value,)).fetchone()[0]
                if count>=limit:
                    reason='busy'; break
        if reason is None and current_policy is not None:
            # Captured selection order is immutable, but an operator lowering
            # current admission limits must take effect for old queued work.
            current = current_policy
            model_limit = current['model_limits'].get(key,target['max_concurrency'])
            provider_limit = current['provider_limits'][provider]['max_concurrency']
            current_pool = current['provider_limits'][provider]['quota_pool']
            pool_providers = [name for name,spec in current['provider_limits'].items() if spec['quota_pool']==current_pool]
            held = conn.execute("SELECT 1 FROM pipeline_route_holds WHERE scope='pool' AND resource_key=? AND until_at>?",(current_pool,now)).fetchone()
            counts = [(conn.execute('SELECT count(*) FROM pipeline_route_reservations WHERE model_key=? AND released_at IS NULL',(key,)).fetchone()[0],model_limit),
                      (conn.execute('SELECT count(*) FROM pipeline_route_reservations WHERE provider=? AND released_at IS NULL',(provider,)).fetchone()[0],provider_limit)]
            placeholders = ','.join('?' for _ in pool_providers)
            count = conn.execute('SELECT count(*) FROM pipeline_route_reservations WHERE released_at IS NULL AND (quota_pool=? OR provider IN ('+placeholders+'))',
                                 [current_pool,*pool_providers]).fetchone()[0]
            counts.append((count,current['quota_pool_limits'][current_pool]))
            if held or any(count>=limit for count,limit in counts):
                reason='busy' if not held else 'quota_pool_hold'
        threshold = policy.get('backlog_threshold', 0)
        if reason is None and threshold:
            # Only earlier eligible jobs count as backlog. Including later
            # submissions would make an otherwise idle preferred target starve.
            ahead = conn.execute('''SELECT count(*) FROM pipeline_jobs j JOIN pipeline_routing_jobs r USING(job_id)
                WHERE j.status IN ('PENDING','PENDING_RETRY') AND r.state='READY' AND r.next_eligible_at<=?
                AND (j.created_at<? OR (j.created_at=? AND j.job_id<?))
                AND json_extract(r.candidates_json,'$[' || r.cursor || '].key')=?''',
                (now,job['created_at'],job['created_at'],job['job_id'],key)).fetchone()[0]
            if ahead>=threshold:
                reason='backlog'
        if reason:
            event(conn,job,'ROUTE_SKIPPED',candidate_index=index,model_key=key,reason=reason,cycle=state['cycle'])
            continue
        return {**target,'pool':pool,'candidate_index':index,'cycle':state['cycle'],
                'score':state['score_details']['score'],'tier':state['score_details']['tier'],
                'policy_digest':state['policy_digest']}, True
    _backoff(conn,job,state)
    return None, True


def reserve(conn,job,selected,worker_slot):
    reservation = uuid.uuid4().hex
    route = {**selected,'reservation_id':reservation,
             'reservation_sha256':hashlib.sha256(reservation.encode()).hexdigest(),
             'attempt_id':job['attempt_id'],'fencing_token':job['fencing_token'],'worker_slot':worker_slot}
    conn.execute('''INSERT INTO pipeline_route_reservations(reservation_id,job_id,attempt_id,fencing_token,
        provider,model,model_key,quota_pool,worker_slot,candidate_index,cycle,route_json,reserved_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (reservation,job['job_id'],job['attempt_id'],job['fencing_token'],route['provider'],route['model'],
         route['key'],route['quota_pool'],worker_slot,route['candidate_index'],route['cycle'],encoded(route),time.time()))
    conn.execute("UPDATE pipeline_routing_jobs SET state='ACTIVE',cursor=?,next_eligible_at=0,wait_reason=NULL,dispatches=dispatches+1 WHERE job_id=?",
                 (route['candidate_index'],job['job_id']))
    event(conn,job,'ROUTE_RESERVED',**route)
    return route


def failed(conn,job,category,retry_after_seconds=None,hold_scope=None,current_policy=None):
    state = get_state(conn,job['job_id'])
    if state is None:
        return None
    route = get_route(conn,job['job_id'])
    if category in {'configuration','compatibility'} and hold_scope=='job':
        release(conn,job['job_id'],category,'BLOCKED')
        conn.execute("UPDATE pipeline_routing_jobs SET wait_reason=?,next_eligible_at=0 WHERE job_id=?", (category,job['job_id']))
        event(conn,job,'ROUTING_BLOCKED',reason=category)
        return get_state(conn,job['job_id'])
    if category not in AVAILABILITY:
        conn.execute("UPDATE pipeline_routing_jobs SET failure_count=failure_count+1,state='READY' WHERE job_id=?", (job['job_id'],))
        release(conn,job['job_id'],category)
        return get_state(conn,job['job_id'])
    if route is None:
        raise ValueError('Availability failure requires a reserved route')
    seconds = state['policy'].get('failure_cooldowns',{}).get(category,30)
    if retry_after_seconds is not None:
        if type(retry_after_seconds) not in (int,float) or not math.isfinite(retry_after_seconds) or not 0<=retry_after_seconds<=86400:
            raise ValueError('retry_after_seconds must be finite within 0..86400')
        seconds=max(seconds,retry_after_seconds)
    scope = hold_scope or ('pool' if category in ('quota','auth') else 'model')
    if scope not in {'model','provider','pool','job'}:
        raise ValueError('hold_scope must be model/provider/pool/job')
    if category!='context' and scope!='job':
        set_hold(conn,scope,{'model':route['key'],'provider':route['provider'],'pool':route['quota_pool']}[scope],seconds,category)
        if scope=='pool' and current_policy is not None:
            current_pool = current_policy['provider_limits'][route['provider']]['quota_pool']
            if current_pool!=route['quota_pool']:
                set_hold(conn,'pool',current_pool,seconds,category)
    release(conn,job['job_id'],category)
    cursor = route['candidate_index'] + 1
    conn.execute("UPDATE pipeline_routing_jobs SET state='READY',cursor=?,wait_reason=? WHERE job_id=?", (cursor,category,job['job_id']))
    event(conn,job,'ROUTE_UNAVAILABLE',category=category,hold_scope='job' if category=='context' else scope,
          candidate_index=route['candidate_index'],next_candidate_index=cursor)
    if cursor>=len(state['candidates']):
        _backoff(conn,job,get_state(conn,job['job_id']))
    return get_state(conn,job['job_id'])
