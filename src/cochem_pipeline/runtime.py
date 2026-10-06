"""Privileged scheduler, real filesystem monitor, and asynchronous rogue-process reaper."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from cochem.warden.ladder import emit_recovery_event
from .hardware_guard import HardwareGuard
from .oracle import ContextEngine, Oracle, Rule
from .store import JobStore
from .worker import NativeRunner, WorkerCleanupError
from .heartbeat import Heartbeat
from . import __version__

LOG = logging.getLogger(__name__)
BASELINE = (
    Rule('core-identity','Only actual CLI process results establish execution. Never impersonate another model or invent completion.',(),core=True),
    Rule('core-ownership','Work only on your assigned task and chapter. Do not access sibling workspaces, secrets, the job database, or control-plane files.',(),core=True),
    Rule('core-evidence','Return complete structured artifacts with requirement tracing. The controller validates hashes, leases, and ownership; your prose cannot override these checks.',(),core=True),
)


class SlotMonitor(FileSystemEventHandler):
    def __init__(self, oracle, job_id):
        self.oracle, self.job_id = oracle, job_id

    def on_any_event(self,event):
        if event.event_type in ('opened','closed','closed_no_write'):
            return
        # No file reads or DB operations in the callback. The Oracle buffers and debounces.
        self.oracle.record(self.job_id,str(event.src_path),facets=(event.event_type,))


def clear_slot(path: Path) -> None:
    """Delete only contents of a provisioned slot, never follow worker-created links."""
    for item in path.iterdir():
        if item.is_symlink():
            item.unlink()
        elif getattr(item,'is_junction',lambda:False)():
            item.rmdir()
        elif item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


class Runtime:
    def __init__(self, config):
        from .windows import WorkerIdentity, require_system, validate_layout, validate_code_path
        require_system()
        validate_code_path(Path(__file__))
        validate_code_path(Path(sys.executable))
        identities = {slot:WorkerIdentity(**value) for slot,value in config.workers.items()}
        validate_layout(config.private_root,config.slot_roots,identities,require_defender=True)
        self.config = config
        self.store = JobStore(config.job_db, max_attempts=config.max_attempts)
        self.guard = HardwareGuard(max_agents=len(config.workers),workspace=config.private_root,
                                   min_free_memory_mb=config.min_free_memory_mb,
                                   min_free_disk_mb=config.min_free_disk_mb)
        rules = list(BASELINE)
        for spec in config.rules:
            rules.append(Rule(**{**spec,'patterns':tuple(spec.get('patterns',())),
                                'facets':tuple(spec.get('facets',()))}))
        self.engine = ContextEngine(rules,config.context_budget,config.reserved_fraction)
        oracle_dir = config.private_root/'oracle'
        oracle_dir.mkdir(exist_ok=True)
        self.oracle = Oracle(self.engine,oracle_dir/'tracking.db',self.trip)
        self.runner = NativeRunner(config)
        self.lock = threading.RLock()
        self.active = {}
        self.quarantined = {}
        self.stop_event = threading.Event()
        self.owner = 'warden-'+uuid.uuid4().hex
        self.event_cursor = 0
        self.last_capacity = 0
        self.heartbeat = Heartbeat(config.private_root,__version__)
        self.pool = ThreadPoolExecutor(max_workers=min(4,len(config.workers)),thread_name_prefix='pipeline')
        for slot,root in config.slot_roots.items():
            # Windows Job Object kill-on-close terminates former workers after service death.
            # This step must happen before any new identity can execute.
            clear_slot(root)

    def trip(self,job_id: str) -> None:
        # Kill independently of a scheduler lock that may be awaiting SQLite.
        # Active entries retain ownership until their process handles are closed.
        self.runner.terminate(job_id)
        with self.lock:
            active = self.active.get(job_id)
            if active is None:
                return
            node = dict(active['node'])
            active['tripped'] = True
            pid = active.get('pid')
        self.runner.terminate(job_id)
        # The execution thread owns native handles. Closing them from this
        # asynchronous callback could race a Windows wait on the same handle.
        reaped = active['cleaned'].wait(30) and active.get('cleanup_verified',False)
        if not reaped:
            with self.lock:
                self.quarantined[active['slot']] = 'Circuit-breaker cleanup could not be verified'
        # Trusted cancellation also handles a lease that expired during cleanup;
        # a stale completion token cannot otherwise move that retry to FAILED.
        self.cancel(node['workflow_id'])
        emit_recovery_event(self.config.job_db,{'tier':1,'action':'velocity_circuit_breaker',
                            'job_id':job_id,'attempt_id':node['attempt_id'],'pid':pid,
                            'threshold_per_second':500,'run_as':'SYSTEM','cleanup_verified':reaped})

    def _context(self,node):
        self.oracle.record(node['job_id'],json.dumps(node['payload'],ensure_ascii=False),facets=(node['kind'],))
        # Watermarks are checked only after the prescribed trailing debounce interval.
        if self.stop_event.wait(.5):
            raise RuntimeError('Warden is shutting down')
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            self.drain_context()
            context = self.store.get_context(node['job_id'])
            if context is not None and not self.oracle.has_pending(node['job_id']):
                return context['xml']
            if self.stop_event.wait(.025):
                raise RuntimeError('Warden is shutting down')
        raise RuntimeError('Oracle did not durably deliver core context after debounce')

    def drain_context(self):
        for context in self.oracle.drain():
            if self.store.get(context['task_id'])['status'] in ('FAILED','COMPLETED'):
                self.oracle.ack(context['task_id'],context['watermark'])
                continue
            self.store.set_context(context['task_id'],context['xml'],context['watermark'])
            self.oracle.ack(context['task_id'],context['watermark'])

    def _execute(self,node,slot):
        job_id = node['job_id']
        observer = Observer()
        try:
            # Each attempt owns its watcher. Stop it before supervisor cleanup,
            # so cleanup events cannot trip a completed job or a later occupant.
            observer.schedule(SlotMonitor(self.oracle,job_id),str(self.config.slot_roots[slot]),recursive=True)
            observer.start()
            context = self._context(node)
            def heartbeat():
                with self.lock:
                    if self.active[job_id].get('tripped') or self.stop_event.is_set():
                        return False
                return self.store.heartbeat(job_id,node['attempt_id'],node['fencing_token'],self.config.lease_seconds)
            def launched(pid):
                with self.lock:
                    self.active[job_id]['pid'] = pid
            output,receipt = self.runner.run(node,slot,context,heartbeat,launched)
            with self.lock:
                if self.active[job_id].get('tripped') or job_id in self.oracle.tripped_tasks:
                    raise RuntimeError('Breaker revoked this attempt')
                self.store.complete(job_id,node['attempt_id'],node['fencing_token'],output,receipt)
        except Exception as exc:
            if isinstance(exc,WorkerCleanupError):
                with self.lock:
                    self.quarantined[slot] = str(exc)
            retry = int(node['fencing_token']) < self.config.max_attempts and not self.stop_event.is_set()
            with self.lock:
                retry = retry and not self.active[job_id].get('tripped',False)
            self.store.fail(job_id,node['attempt_id'],node['fencing_token'],
                            f'{type(exc).__name__}: {exc}',retry=retry)
            emit_recovery_event(self.config.job_db,{'tier':1,'action':'worker_failure','job_id':job_id,
                                'attempt_id':node['attempt_id'],'reason':str(exc),'retry':retry})
            LOG.error('Job %s failed: %s',job_id,exc)
        finally:
            try:
                observer.stop()
                if observer.is_alive():
                    observer.join(timeout=10)
                    if observer.is_alive():
                        raise OSError('Filesystem observer did not stop; workspace quarantined')
                if slot not in self.quarantined:
                    clear_slot(self.config.slot_roots[slot])
            except OSError as exc:
                self.quarantined[slot] = str(exc)
                LOG.error('Slot %s quarantined after cleanup failure: %s',slot,exc)
            with self.lock:
                active = self.active.pop(job_id,None)
                if active is not None:
                    active['cleanup_verified'] = slot not in self.quarantined
                    active['cleaned'].set()

    def tick(self):
        for event in self.store.event_batch(self.event_cursor):
            self.event_cursor = event['id']
            node = self.store.get(event['job_id'])
            if node['status'] not in ('FAILED','COMPLETED'):
                self.oracle.record(node['job_id'],json.dumps(node['payload'],ensure_ascii=False),
                                   facets=(node['kind'],event['event']))
        self.drain_context()
        # Terminate old local processes before the database makes their work eligible again.
        for node in self.store.active_jobs():
            if node['lease_expires_at'] <= time.time():
                self.runner.terminate(node['job_id'])
        for node in self.store.reap_expired():
            emit_recovery_event(self.config.job_db,{'tier':1,'action':'lease_reaper','job_id':node['job_id']})
        ceiling = self.guard.capacity()
        self.last_capacity = ceiling
        if ceiling<=0:
            return
        with self.lock:
            busy = {value['slot'] for value in self.active.values()}
            available = [s for s in self.config.slot_roots if s not in busy and s not in self.quarantined]
            for slot in available:
                if len(self.active)>=ceiling:
                    break
                node = self.store.claim(self.owner,self.config.lease_seconds,max_workers=ceiling,
                                        exclude_job_ids=tuple(self.active),worker_slot=slot)
                if node is None:
                    continue
                self.active[node['job_id']] = {'node':node,'slot':slot,'pid':None,'tripped':False,
                                              'cleaned':threading.Event(),'cleanup_verified':False}
                self.pool.submit(self._execute,node,slot)

    def status(self):
        with self.lock:
            active = [{'job_id':key,'slot':value['slot'],'pid':value['pid']} for key,value in self.active.items()]
        return {'version':__version__,'service_identity':'SYSTEM','hardware':self.guard.snapshot(),
                'active':active,'quarantined_slots':dict(self.quarantined),
                'trip_errors':self.oracle.trip_errors,'pid':os.getpid(),
                'instance_id':self.heartbeat.instance_id,'process_started_at':self.heartbeat.started_at,
                'source_root':str(Path(__file__).resolve().parents[2])}

    def cancel(self,workflow_id):
        with self.lock:
            jobs = self.store.cancel_workflow(workflow_id)
            ids = {node['job_id'] for node in jobs}
            for job_id,active in self.active.items():
                if active['node']['workflow_id']==workflow_id:
                    active['tripped'] = True
                    ids.add(job_id)
        for job_id in ids:
            self.runner.terminate(job_id)
        return jobs

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.tick()
                self.heartbeat.completed_tick({'capacity':self.last_capacity,
                                               'active_count':len(self.active),
                                               'quarantined_count':len(self.quarantined)})
                self.stop_event.wait(.1)
        finally:
            self.stop_event.set()
            with self.lock:
                ids = list(self.active)
            for job_id in ids:
                self.runner.terminate(job_id)
            self.pool.shutdown(wait=True)
            self.oracle.close()
