"""Protected repair job board using the Chapter 06 contract independently.

The primary Warden must be quiescent with verified process cleanup before any
reservation is executed. This board therefore remains usable if the primary
SQLite database or candidate Python imports are broken. Budgets belong to the
separate supervisor ledger and are never reset by routing retries.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .probes import configured_routing_policy, score_routing_task


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def backoff_delay(policy, cycle, seed):
    unit=int.from_bytes(hashlib.sha256(f'{seed}\0{cycle}'.encode()).digest()[:8],'big')/((1<<64)-1)
    base=min(policy['backoff_max_seconds'],policy['backoff_base_seconds']*2**min(cycle-1,30))
    return min(policy['backoff_max_seconds'],max(.001,base*(1+(2*unit-1)*policy['backoff_jitter_fraction'])))


class RepairJobBoard:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS repair_jobs(
                    job_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, policy TEXT NOT NULL,
                    scoring TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                    candidate_index INTEGER NOT NULL DEFAULT 0, cycle INTEGER NOT NULL DEFAULT 0,
                    next_eligible REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                    reservation TEXT, receipt TEXT, dispatches INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS repair_route_holds(
                    scope TEXT NOT NULL, target TEXT NOT NULL, until_time REAL NOT NULL,
                    category TEXT NOT NULL, PRIMARY KEY(scope,target));
                CREATE TABLE IF NOT EXISTS repair_route_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    timestamp REAL NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS repair_job_identity BEFORE UPDATE OF
                    job_id,fingerprint,kind,payload,policy,scoring,created_at ON repair_jobs
                    BEGIN SELECT RAISE(ABORT,'Captured repair job authority is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS repair_history_update BEFORE UPDATE ON repair_route_events
                    BEGIN SELECT RAISE(ABORT,'Repair route history is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS repair_history_delete BEFORE DELETE ON repair_route_events
                    BEGIN SELECT RAISE(ABORT,'Repair route history is immutable'); END;
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA synchronous=NORMAL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        value = dict(row)
        for key in ('payload', 'policy', 'scoring', 'reservation', 'receipt'):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    @staticmethod
    def event(db, job_id, event, details, now):
        db.execute('INSERT INTO repair_route_events(job_id,timestamp,event,details) VALUES(?,?,?,?)',
                   (job_id, now, event, canonical(details)))

    def submit(self, fingerprint, payload, policy=None, *, kind='REPAIR_REQUEST', now=None):
        if kind not in ('REPAIR_REQUEST', 'REPAIR_REVIEW', 'PREFLIGHT_REQUEST') or not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError('Repair board requires a bounded repair or preflight identity')
        normalized = configured_routing_policy(policy)
        scoring = score_routing_task(kind, payload)
        timestamp = time.time() if now is None else now
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM repair_jobs WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row is not None:
                return self.decode(row)
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO repair_jobs(job_id,fingerprint,kind,payload,policy,scoring,created_at) VALUES(?,?,?,?,?,?,?)',
                       (identifier, fingerprint, kind, canonical(payload), canonical(normalized), canonical(scoring), timestamp))
            self.event(db, identifier, 'SUBMITTED', {'policy_digest':digest(normalized), 'scoring':scoring}, timestamp)
            return self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?', (identifier,)).fetchone())

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM repair_jobs WHERE job_id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown repair job')
            return self.decode(row)

    def claim(self, job_id, available_providers, *, primary_cleanup_verified, now=None):
        timestamp = time.time() if now is None else now
        with self.connection(True) as db:
            job = self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?', (job_id,)).fetchone())
            if job['status'] not in ('PENDING', 'PENDING_RETRY') or job['next_eligible'] > timestamp:
                return None
            # One standard repair identity means one native repair at a time.
            # This is identity containment, not a model/provider concurrency cap.
            if db.execute("SELECT 1 FROM repair_jobs WHERE status='IN_PROGRESS'").fetchone():
                return None
            policy, scoring = job['policy'], job['scoring']
            if ((policy['max_dispatches'] and job['dispatches'] >= policy['max_dispatches']) or
                (policy['max_routing_cycles'] and job['cycle'] >= policy['max_routing_cycles']) or
                (policy['max_routing_seconds'] and timestamp-job['created_at'] >= policy['max_routing_seconds'])):
                db.execute("UPDATE repair_jobs SET status='BLOCKED' WHERE job_id=?", (job_id,))
                self.event(db, job_id, 'ROUTING_BUDGET_EXHAUSTED', {}, timestamp)
                return None
            targets = policy['tiers'][scoring['tier']]
            for index in range(job['candidate_index'], len(targets)):
                target = targets[index]
                key = target['provider']+':'+target['model']+(':'+target['reasoning_effort'] if target['reasoning_effort'] else '')
                pool = policy['provider_limits'][target['provider']]['quota_pool']
                hold = db.execute('SELECT category,until_time FROM repair_route_holds WHERE until_time>? AND '
                    "((scope='provider' AND target=?) OR (scope='route' AND target=?) OR (scope='pool' AND target=?))",
                    (timestamp, target['provider'], key, pool)).fetchone()
                excluded=job['payload'].get('excluded_providers',[])
                if target['provider'] in excluded or target['provider'] not in available_providers or hold:
                    self.event(db, job_id, 'CANDIDATE_SKIPPED', {'index':index, 'key':key,
                        'reason':'asymmetric_provider_exclusion' if target['provider'] in excluded else dict(hold) if hold else 'provider_unavailable'}, timestamp)
                    continue
                if primary_cleanup_verified is not True:
                    raise ValueError('Independent repair dispatch requires verified primary containment cleanup')
                reservation = {**target, 'key':key, 'quota_pool':pool,
                    'max_concurrency':policy['model_limits'][key], 'candidate_index':index,
                    'cycle':job['cycle'], 'score':scoring['score'], 'tier':scoring['tier'],
                    'policy_digest':digest(policy), 'job_id':job_id, 'kind':job['kind'],
                    'reservation_id':uuid.uuid4().hex, 'fencing_token':uuid.uuid4().hex,
                    'reserved_at':timestamp, 'primary_cleanup_verified':True}
                reservation['reservation_sha256'] = digest(reservation)
                db.execute("UPDATE repair_jobs SET status='IN_PROGRESS',reservation=?,candidate_index=?,dispatches=dispatches+1 WHERE job_id=?",
                           (canonical(reservation), index, job_id))
                self.event(db, job_id, 'RESERVED', reservation, timestamp)
                return reservation
            cycle = job['cycle'] + 1
            delay=backoff_delay(policy,cycle,job_id)
            db.execute("UPDATE repair_jobs SET status='PENDING_RETRY',candidate_index=0,cycle=?,next_eligible=? WHERE job_id=?",
                       (cycle, timestamp+delay, job_id))
            self.event(db, job_id, 'BACKOFF', {'cycle':cycle, 'next_eligible':timestamp+delay}, timestamp)
            return None

    def finish(self, reservation, *, receipt=None, failure=None, now=None):
        timestamp = time.time() if now is None else now
        with self.connection(True) as db:
            job = self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?', (reservation['job_id'],)).fetchone())
            if job['status'] != 'IN_PROGRESS' or job['reservation'] != reservation:
                raise ValueError('Stale repair reservation cannot finish or advance routing')
            if failure is None:
                self.validate_receipt(reservation,receipt)
                db.execute("UPDATE repair_jobs SET status='COMPLETED',receipt=? WHERE job_id=?", (canonical(receipt),job['job_id']))
                self.event(db, job['job_id'], 'COMPLETED', {'receipt_sha256':digest(receipt)}, timestamp)
            else:
                category = failure if failure in job['policy']['failure_cooldowns'] else 'unknown'
                availability={'quota','auth','busy','backlog','provider','context','compatibility'}
                delay=job['policy']['failure_cooldowns'][category]
                index=reservation['candidate_index']
                status='PENDING_RETRY'; eligible=0.; scope=None
                if category=='configuration':
                    status='BLOCKED'
                elif category=='resource':
                    eligible=timestamp+max(1.,delay)
                elif category in ('timeout','protocol'):
                    failures=db.execute("SELECT count(*) FROM repair_route_events WHERE job_id=? AND event='ATTEMPT_FAILED' AND json_extract(details,'$.category') IN ('timeout','protocol')",(job['job_id'],)).fetchone()[0]+1
                    eligible=timestamp+backoff_delay(job['policy'],failures,job['job_id'])
                elif category in availability:
                    index+=1
                    if category!='context':
                        scope='pool' if category in ('quota','auth') else 'route'
                        target=reservation['quota_pool'] if scope=='pool' else reservation['key']
                        db.execute('INSERT INTO repair_route_holds VALUES(?,?,?,?) ON CONFLICT(scope,target) DO UPDATE SET until_time=max(until_time,excluded.until_time),category=excluded.category',
                                   (scope,target,timestamp+delay,category))
                db.execute('UPDATE repair_jobs SET status=?,candidate_index=?,next_eligible=?,reservation=NULL WHERE job_id=?',
                           (status,index,eligible,job['job_id']))
                self.event(db,job['job_id'],'ATTEMPT_FAILED',{'category':category,'hold_scope':scope,
                    'until':timestamp+delay if scope else eligible,'next_candidate_index':index},timestamp)


    @staticmethod
    def validate_receipt(reservation, receipt):
        expected = {'provider':reservation['provider'], 'requested_model':reservation['model'],
            'requested_effort':reservation['reasoning_effort'],
            'route_reservation_sha256':reservation['reservation_sha256']}
        if (not isinstance(receipt, dict) or any(receipt.get(k) != v for k,v in expected.items()) or
                receipt.get('subscription_verified') is not True or receipt.get('terminal_success') is not True or
                receipt.get('exit_code') != 0 or type(receipt.get('pid')) is not int or receipt['pid'] <= 0 or
                not isinstance(receipt.get('session_id'),str) or not receipt['session_id'] or
                not isinstance(receipt.get('output_sha256'),str) or len(receipt['output_sha256'])!=64):
            raise ValueError('Repair completion lacks matching process-derived native receipt evidence')

    def release_unspent(self, reservation, now=None):
        """Return a reservation when the independent monetary budget denies spend."""
        timestamp=time.time() if now is None else now
        with self.connection(True) as db:
            job=self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?',(reservation['job_id'],)).fetchone())
            if job['status']!='IN_PROGRESS' or job['reservation']!=reservation:
                raise ValueError('Stale unspent repair reservation')
            db.execute("UPDATE repair_jobs SET status='PENDING',reservation=NULL,dispatches=dispatches-1 WHERE job_id=?",(job['job_id'],))
            self.event(db,job['job_id'],'BUDGET_HOLD_NO_INFERENCE',{},timestamp)

    def bind_budget(self, reservation, attempt_id):
        with self.connection(True) as db:
            job=self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?',(reservation['job_id'],)).fetchone())
            if job['status']!='IN_PROGRESS' or job['reservation']!=reservation:
                raise ValueError('Stale repair budget attachment')
            self.event(db,job['job_id'],'BUDGET_ATTACHED',{'reservation_id':reservation['reservation_id'],
                'attempt_id':attempt_id},time.time())

    def interrupted_attempt(self, job_id):
        with self.connection() as db:
            job=self.decode(db.execute('SELECT * FROM repair_jobs WHERE job_id=?',(job_id,)).fetchone())
            if job['status']!='IN_PROGRESS':
                return None
            for row in db.execute("SELECT details FROM repair_route_events WHERE job_id=? AND event='BUDGET_ATTACHED' ORDER BY id DESC",(job_id,)):
                details=json.loads(row[0])
                if details['reservation_id']==job['reservation']['reservation_id']:
                    return {'reservation':job['reservation'],'attempt_id':details['attempt_id']}
            return {'reservation':job['reservation'],'attempt_id':None}

    def interrupted_reservations(self):
        with self.connection() as db:
            identifiers=[row[0] for row in db.execute("SELECT job_id FROM repair_jobs WHERE status='IN_PROGRESS'")]
        return [value for identifier in identifiers if (value:=self.interrupted_attempt(identifier)) is not None]

    def recover_interrupted(self, reservation, *, cleanup_verified, budget_attempt_terminal):
        """Ownership can transfer only after the old charged lease is terminal.

        The production caller holds the singleton supervisor process lock. An
        old runner's kill-on-close Job Object cannot survive its owner; verified
        primary containment shutdown and native repair boundary checks precede
        this explicit reconciliation. No reservation or spend is refunded.
        """
        if cleanup_verified is not True or budget_attempt_terminal is not True:
            raise ValueError('Interrupted repair needs verified cleanup and a terminal charged lease')
        self.finish(reservation,failure='unknown')
        return True
