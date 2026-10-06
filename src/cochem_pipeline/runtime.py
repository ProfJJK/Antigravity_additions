"""Privileged scheduler, real filesystem monitor, and asynchronous rogue-process reaper."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from cochem.warden.ladder import emit_recovery_event
from .hardware_guard import HardwareGuard, effective_execution_policy
from .oracle import ContextEngine, Oracle, Rule
from .store import JobStore
from .worker import NativeRunner, WorkerCleanupError, ExecutionRevokedError
from .failures import ProviderFailure
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


def clear_ram_workspace(descriptor) -> None:
    """Cleanup only the exact mounted device after native-tree exit is proved."""
    from .ramdisk import RamWorkspace
    if type(descriptor) is not RamWorkspace:
        raise TypeError('RAM cleanup requires a genuine controller workspace descriptor')
    descriptor.validate(identity=descriptor.identity,require_capacity=False)
    clear_slot(descriptor.root)


def startup_quarantines(slot_roots, previous_cleanup, *, ramdisk_required, ramdisk_ready):
    """Keep all RAM-dependent slots closed until containment and RAM are verified.

    This is reconstructed from current boot/containment evidence at every
    startup, rather than persisting an extra hold that could outlive recovery.
    """
    result = {row['worker_slot']:row.get('reason') or 'Previous native cleanup is unverified'
              for row in previous_cleanup if row['worker_slot'] in slot_roots}
    if any(row['worker_slot'] not in slot_roots for row in previous_cleanup):
        result.update({slot:'Previous native cleanup has unknown worker ownership' for slot in slot_roots})
    if ramdisk_required and not ramdisk_ready:
        result.update({slot:'Required RAM workspace is unavailable until native containment recovery and RAM verification complete'
                       for slot in slot_roots})
    return result


class Runtime:
    def __init__(self, config):
        from .windows import WorkerIdentity, require_system, validate_layout, validate_code_path, current_boot_identity
        require_system()
        validate_code_path(Path(__file__))
        validate_code_path(Path(sys.executable))
        from .resource_limits import controller_limits
        from .controller_guard import ControllerMonitor
        self.controller_scheduling = controller_limits(config.execution_limits,apply=True)
        self.controller_monitor = ControllerMonitor()
        identities = {slot:WorkerIdentity(**value) for slot,value in config.workers.items()}
        validate_layout(config.private_root,config.slot_roots,identities,require_defender=True)
        self.config = config
        from .planning_governance import canonical_authority
        self.canonical_authority = canonical_authority()
        from .knowledge import KnowledgeService
        self.knowledge = KnowledgeService(config.knowledge)
        knowledge_evidence = self.knowledge.refresh()
        if not knowledge_evidence['index_size_sla_met']:
            raise RuntimeError('Knowledge index exceeds the SRS four-times-corpus storage bound')
        self._last_knowledge_refresh = time.monotonic()
        from .knowledge_authority import KnowledgeAuthority
        self.knowledge_authority = KnowledgeAuthority(self.knowledge,self.canonical_authority)
        self.knowledge_authority.status(force=True)
        from .operations_policy import WorkloadObjectives
        self.objectives = WorkloadObjectives(config.private_root/'operations')
        self.boot_id = current_boot_identity()
        self.containment_id = os.environ.get('COCHEM_WARDEN_CONTAINMENT_ID')
        if self.containment_id is not None and not re.fullmatch(r'[0-9a-f]{32}',self.containment_id):
            raise RuntimeError('Invalid privileged launcher containment identity')
        self.store = JobStore(config.job_db,max_attempts=config.max_attempts,routing_policy=config.routing,
                              cleanup_boot_id=self.boot_id,governing_requirements=self.canonical_authority)
        self.store.recover_execution_quarantines_after_boot(self.boot_id)
        previous_cleanup = self.store.quarantine_unclosed_executions('Warden restarted before native cleanup was confirmed')
        execution_policy = effective_execution_policy(getattr(config,'hardware',None),
            config.execution_limits.memory_limit_mb,
            config.docker.memory_mb if getattr(config,'docker',None) and config.docker.enabled else 0)
        self.guard = HardwareGuard(max_agents=min(getattr(config,'max_execution_slots',4),len(config.workers)),workspace=config.private_root,
                                   policy=execution_policy,
                                   workspaces=[config.private_root, *config.slot_roots.values()])
        rules = list(BASELINE)
        for spec in config.rules:
            rules.append(Rule(**{**spec,'patterns':tuple(spec.get('patterns',())),
                                'facets':tuple(spec.get('facets',()))}))
        self.engine = ContextEngine(rules,config.context_budget,config.reserved_fraction)
        self._context_drain_lock = threading.Lock()
        oracle_dir = config.private_root/'oracle'
        oracle_dir.mkdir(exist_ok=True)
        self.oracle = Oracle(self.engine,oracle_dir/'tracking.db',self.trip)
        self.runner = NativeRunner(config)
        self.ramdisk = None
        self.coding = None
        self.docker = None
        self.components = {}
        self._last_container_maintenance = 0.
        self._maintenance_thread = None
        if getattr(config, 'docker', None) is not None and config.docker.enabled:
            from .containers import DockerRunner
            self.docker = DockerRunner(config.docker, config.private_root / 'containers',
                                       trusted_operator=config.operator_name,host_boot_id=self.boot_id)
            # A Docker tree is outside the Warden Job Object. First prove its
            # old controller stopped, then independently remove and inspect the
            # exact registered container attempt before clearing its guard.
            for pending in previous_cleanup:
                old = self.store.get(pending['job_id'])
                if (old['kind']=='CODE_TEST' and self.store.execution_owner_stopped(
                        pending['job_id'],pending['attempt_id'],pending['fencing_token'])):
                    proof = self.docker.confirm_attempt_cleanup(pending['attempt_id'],controller_stopped=True)
                    if proof.get('cleanup_verified') is True:
                        self.store.clear_execution_quarantine(pending['job_id'],pending['attempt_id'],pending['fencing_token'])
            previous_cleanup=self.store.execution_quarantines()
            if not previous_cleanup:
                self.docker.preflight()
                from .deployment import verify_docker_access_boundary
                verify_docker_access_boundary(config.docker.endpoint, identities,
                    trusted_operator=config.operator_name,
                    trusted_server_executables=config.docker.pipe_server_executables)
                self.docker.reap_orphans(active_attempt_ids=set())
        if getattr(config, 'ramdisk', None) is not None and config.ramdisk.enabled and not previous_cleanup:
            from .ramdisk import RamdiskManager
            self.ramdisk = RamdiskManager(config.ramdisk, config.private_root, identities)
            self.ramdisk.ensure()
            self.guard = HardwareGuard(max_agents=min(getattr(config,'max_execution_slots',4),len(config.workers)),workspace=config.private_root,
                policy=execution_policy,workspaces=[config.private_root,*config.slot_roots.values(),
                                                  *(self.ramdisk.workspace(slot).root for slot in config.workers)])
        from .admission import JointAdmission
        self.admission=JointAdmission(self.store,self.docker,capacity=0,max_capacity=min(getattr(config,'max_execution_slots',4),len(config.workers)))
        if getattr(config, 'coding_projects', None) and self.ramdisk is not None and self.docker is not None:
            from .coding import CodingCoordinator
            self.coding = CodingCoordinator(config, self.store, self.runner, self.ramdisk,host_boot_id=self.boot_id)
        self.lock = threading.RLock()
        self.active = {}
        self.quarantined = startup_quarantines(config.slot_roots,previous_cleanup,
            ramdisk_required=bool(getattr(config,'ramdisk',None) and config.ramdisk.enabled),
            ramdisk_ready=self.ramdisk is not None)
        self.stop_event = threading.Event()
        self.owner = 'warden-'+uuid.uuid4().hex
        self.event_cursor = 0
        self.last_capacity = 0
        self.heartbeat = Heartbeat(config.private_root,__version__)
        self.pool = ThreadPoolExecutor(max_workers=min(getattr(config,'max_execution_slots',4),len(config.workers)),thread_name_prefix='pipeline')
        for slot,root in config.slot_roots.items():
            # Windows Job Object kill-on-close terminates former workers after service death.
            # This step must happen before any new identity can execute.
            if slot not in self.quarantined:
                clear_slot(root)
                if self.ramdisk is not None:
                    try:
                        clear_ram_workspace(self.ramdisk.workspace(slot))
                    except Exception as exc:
                        self.quarantined[slot] = 'Verified RAM startup cleanup failed: '+str(exc)

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
            raise ExecutionRevokedError('Warden is shutting down')
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            self.drain_context()
            context = self.store.get_context(node['job_id'])
            if context is not None and not self.oracle.has_pending(node['job_id']):
                return context['xml']
            if self.stop_event.wait(.025):
                raise ExecutionRevokedError('Warden is shutting down')
        raise RuntimeError('Oracle did not durably deliver core context after debounce')

    def drain_context(self):
        # Tick and worker preflight can both drain. Persist and acknowledge each
        # ordered batch under one delivery lock so an older batch cannot replace
        # a newer selection after it has already been acknowledged.
        with self._context_drain_lock:
            for context in self.oracle.drain():
                if self.store.get(context['task_id'])['status'] in ('FAILED','COMPLETED'):
                    self.oracle.ack(context['task_id'],context['watermark'],context['delivery_id'])
                    continue
                self.store.set_context(context['task_id'],context['xml'],context['watermark'])
                self.oracle.ack(context['task_id'],context['watermark'],context['delivery_id'])

    def _workspace_root(self, slot, node=None):
        if self.ramdisk is not None:
            return self.ramdisk.workspace(slot).root
        return self.config.slot_roots[slot]

    def _execute(self,node,slot):
        job_id = node['job_id']
        observer = Observer()
        observed_start = time.monotonic()
        observed_success = False
        try:
            # Each attempt owns its watcher. Stop it before supervisor cleanup,
            # so cleanup events cannot trip a completed job or a later occupant.
            coding = node['kind'].startswith('CODE_')
            def native_watch_start(workspace):
                observer.schedule(SlotMonitor(self.oracle,job_id),str(workspace),recursive=True)
                observer.start()
            def native_watch_end():
                observer.stop()
                if observer.is_alive():
                    observer.join(timeout=10)
                    if observer.is_alive():
                        raise WorkerCleanupError('Native filesystem monitor did not stop before controller staging')
            if not coding:
                if self.ramdisk is not None:
                    descriptor=self.ramdisk.workspace(slot)
                    descriptor.validate(identity=descriptor.identity)
                native_watch_start(self._workspace_root(slot,node))
            context = self._context(node)
            def heartbeat():
                with self.lock:
                    if (self.active[job_id].get('tripped') or self.active[job_id].get('resource_tripped')
                            or self.active[job_id].get('cancel_event',threading.Event()).is_set() or self.stop_event.is_set()):
                        return False
                return self.store.heartbeat(job_id,node['attempt_id'],node['fencing_token'],self.config.lease_seconds)
            def launched(pid):
                with self.lock:
                    self.active[job_id]['pid'] = pid
                observer = getattr(self,'queue_launch_observer',None)
                if observer is not None:
                    observer.observe_native_launch(node,slot,pid)
            coding = node['kind'].startswith('CODE_')
            if coding:
                if self.coding is None:
                    raise ProviderFailure('configuration')
                output,receipt,evidence,files = self.coding.run(node,slot,context,heartbeat,launched,
                    self.active[job_id]['cancel_event'],on_native_start=native_watch_start,on_native_end=native_watch_end,
                    container_reservation=self.active[job_id].get('container_reservation'))
            elif self.ramdisk is not None:
                descriptor = self.ramdisk.workspace(slot)
                output,receipt = self.runner.run(node,slot,context,heartbeat,launched,
                                                 workspace=descriptor.root,ramdisk_workspace=descriptor)
            else:
                output,receipt = self.runner.run(node,slot,context,heartbeat,launched)
            with self.lock:
                self.store.clear_execution_quarantine(job_id,node['attempt_id'],node['fencing_token'])
                self.active[job_id]['process_cleanup_verified'] = True
                if (self.active[job_id].get('tripped') or self.active[job_id].get('resource_tripped')
                        or self.active[job_id].get('cancel_event',threading.Event()).is_set() or job_id in self.oracle.tripped_tasks):
                    raise ExecutionRevokedError('Breaker revoked this attempt')
                if coding:
                    self.store.complete_coding(job_id,node['attempt_id'],node['fencing_token'],output,receipt,
                                               evidence=evidence,files=files)
                else:
                    self.store.complete(job_id,node['attempt_id'],node['fencing_token'],output,receipt)
                observed_success = self.store.get(job_id)['status'] == 'COMPLETED'
        except Exception as exc:
            if node['kind']=='CODE_TEST' and self.docker is not None and not isinstance(exc,WorkerCleanupError):
                try:
                    proof=self.docker.confirm_attempt_cleanup(node['attempt_id'],controller_stopped=True)
                    if proof.get('cleanup_verified') is not True:
                        exc=WorkerCleanupError('Prebound Docker attempt cleanup could not be verified')
                except Exception:
                    exc=WorkerCleanupError('Prebound Docker attempt cleanup could not be inspected')
            self._record_failure(node,slot,exc)
        finally:
            try:
                observer.stop()
                if observer.is_alive():
                    observer.join(timeout=10)
                    if observer.is_alive():
                        raise OSError('Filesystem observer did not stop; workspace quarantined')
                if slot not in self.quarantined:
                    if self.ramdisk is not None:
                        clear_ram_workspace(self.ramdisk.workspace(slot))
                    else:
                        clear_slot(self._workspace_root(slot,node))
            except Exception as exc:
                self.quarantined[slot] = str(exc)
                LOG.error('Slot %s quarantined after cleanup failure: %s',slot,exc)
            with self.lock:
                active = self.active.pop(job_id,None)
                if active is not None:
                    if active.get('process_cleanup_verified',False):
                        try:
                            self.store.clear_execution_quarantine(job_id,node['attempt_id'],node['fencing_token'])
                        except Exception as exc:
                            self.quarantined[slot] = 'Durable native cleanup confirmation could not be saved'
                            LOG.error('Slot %s cleanup receipt failed: %s',slot,exc)
                    active['cleanup_verified'] = slot not in self.quarantined
                    active['cleaned'].set()
            if hasattr(self,'objectives'):
                try:
                    self.objectives.record('background', node['kind'], time.monotonic()-observed_start,
                        observed_success and slot not in self.quarantined, node['attempt_id'])
                except Exception as exc:
                    LOG.error('Operational objective evidence could not be recorded: %s', type(exc).__name__)
            if hasattr(self,'admission'):
                # Refill prepared capacity as soon as execution releases a seat.
                self.admission.maintenance_requested.set()

    def _record_failure(self,node,slot,exc):
        """Report one finished dispatch; durable scheduling owns all retry budgets."""
        from .ramdisk import RamdiskError
        from .resource_limits import ResourcePolicyError
        from .windows import WindowsIsolationError
        job_id = node['job_id']
        with self.lock:
            active = self.active.get(job_id,{})
            if isinstance(exc,WorkerCleanupError):
                self.quarantined[slot] = str(exc)
                active['process_cleanup_verified'] = False
                self.store.quarantine_execution(job_id,node['attempt_id'],node['fencing_token'],slot,
                                                str(exc),pid=active.get('pid'))
            else:
                # NativeRunner either never launched, or finished its managed
                # close before returning/raising. This exact proof must precede
                # DB release so cancellation/reaper cannot free phantom capacity.
                self.store.clear_execution_quarantine(job_id,node['attempt_id'],node['fencing_token'])
                active['process_cleanup_verified'] = True
            retry = not (self.stop_event.is_set() or active.get('tripped',False)
                         or slot in self.quarantined or isinstance(exc,ExecutionRevokedError))
        readiness_error = isinstance(exc,(RamdiskError,ResourcePolicyError,WindowsIsolationError))
        category = ('resource' if active.get('resource_tripped') else
                    getattr(exc,'category','configuration') if readiness_error else
                    exc.category if isinstance(exc,ProviderFailure) else
                    'timeout' if isinstance(exc,TimeoutError) else 'code')
        if (category=='resource' and slot not in self.quarantined and not self.stop_event.is_set()
                and not active.get('tripped',False)
                and (not isinstance(exc,ExecutionRevokedError) or active.get('resource_tripped'))):
            retry = True
        retry_after = exc.retry_after_seconds if isinstance(exc,ProviderFailure) else 30 if readiness_error and category=='resource' else None
        hold_scope = 'job' if readiness_error and category!='resource' else exc.hold_scope if isinstance(exc,ProviderFailure) else None
        accepted = self.store.fail(job_id,node['attempt_id'],node['fencing_token'],
                                   f'{type(exc).__name__}: {exc}',retry=retry,
                                   category=category,retry_after_seconds=retry_after,hold_scope=hold_scope)
        # A cancelled/reaped attempt can no longer mutate its job or trigger
        # another dispatch. Its stale failure is not a fresh recovery incident.
        if accepted:
            from .crash import record_crash
            try:
                record_crash(self.config.private_root, exc, category)
            except Exception:
                LOG.error('Private crash metadata could not be persisted')
            emit_recovery_event(self.config.job_db,{
                'tier':1,'action':('routing_hold' if hold_scope=='job' or category=='configuration'
                    else 'provider_unavailable' if category in ('quota','auth','busy','context','provider','backlog','resource','compatibility') else 'worker_failure'),
                'job_id':job_id,'attempt_id':node['attempt_id'],'category':category,
                'reason':str(exc),'retry_requested':retry,'retry_after_seconds':retry_after,
                'hold_scope':hold_scope})
            LOG.error('Job %s failed (%s): %s',job_id,category,exc)
        return accepted

    def _terminate_fenced_attempts(self):
        """Stop local trees whose durable leases were cancelled or failed elsewhere."""
        with self.lock:
            claimed = [dict(value['node']) for value in self.active.values()]
        for previous in claimed:
            current = self.store.get(previous['job_id'])
            if (current['status'] != 'IN_PROGRESS' or current['attempt_id'] != previous['attempt_id']
                    or current['fencing_token'] != previous['fencing_token']
                    or current['lease_expires_at'] is None or current['lease_expires_at'] <= time.time()):
                with self.lock:
                    current_active = self.active.get(previous['job_id'])
                    if current_active is not None:
                        current_active.get('cancel_event',threading.Event()).set()
                self.runner.terminate(previous['job_id'])

    def tick(self):
        for event in self.store.event_batch(self.event_cursor):
            self.event_cursor = event['id']
            node = self.store.get(event['job_id'])
            if node['status'] not in ('FAILED','COMPLETED'):
                self.oracle.record(node['job_id'],json.dumps(node['payload'],ensure_ascii=False),
                                   facets=(node['kind'],event['event']))
        self.drain_context()
        # Terminate old local processes before the database makes their work eligible again.
        self._terminate_fenced_attempts()
        for node in self.store.active_jobs():
            if node['lease_expires_at'] <= time.time():
                self.runner.terminate(node['job_id'])
        for node in self.store.reap_expired():
            emit_recovery_event(self.config.job_db,{'tier':1,'action':'lease_reaper','job_id':node['job_id']})
        decision = self.guard.evaluate() if hasattr(self.guard,'evaluate') else {'capacity':self.guard.capacity()}
        if getattr(self,'knowledge',None) is not None:
            knowledge = self.knowledge_authority.status() if hasattr(self,'knowledge_authority') else self.knowledge.status()
            self.components['knowledge'] = {'required':True,'checked_at':time.time(),
                'state':'healthy' if knowledge['ready'] else 'unhealthy','evidence':knowledge}
            if not knowledge['ready']:
                decision = {**decision,'capacity':0,
                    'state':decision.get('state') if decision.get('action')=='terminate_active' else 'paused',
                    'action':decision.get('action') if decision.get('action')=='terminate_active' else 'pause_admission',
                    'reasons':[*decision.get('reasons',[]),knowledge.get('authority_reason') or 'Registered knowledge corpus/index is not ready']}
        if hasattr(self,'controller_monitor'):
            controller = self.controller_monitor.sample()
            self.components['warden_controller'] = {'required':True,
                'state':'healthy' if controller['ready'] else 'resource_pressure',
                **controller,'scheduling':self.controller_scheduling}
            if not controller['ready']:
                decision = {**decision,'capacity':0,
                    'state':decision.get('state') if decision.get('action')=='terminate_active' else 'paused',
                    'action':decision.get('action') if decision.get('action')=='terminate_active' else 'pause_admission',
                    'reasons':[*decision.get('reasons',[]),*controller['reasons']],
                    'controller':controller}
        ceiling = decision['capacity']
        self.store.set_transition_telemetry(decision)
        if decision.get('action') == 'terminate_active':
            with self.lock:
                pressured = list(self.active.items())
                for _, active in pressured:
                    active['resource_tripped'] = True
                    active['cancel_event'].set()
            for job_id, active in pressured:
                current=active['node']
                self.store.fail(job_id,current['attempt_id'],current['fencing_token'],
                    'Hardware governor revoked this attempt after critical pressure',retry=True,
                    category='resource',retry_after_seconds=30)
                self.runner.terminate(job_id)
        if hasattr(self,'admission'):
            self.admission.update_capacity(ceiling)
        self._container_maintenance(ceiling)
        self.last_capacity = ceiling
        if ceiling<=0:
            return
        with self.lock:
            busy = {value['slot'] for value in self.active.values()}
            available = [s for s in self.config.slot_roots if s not in busy and s not in self.quarantined]
            for slot in available:
                if len(self.active)>=ceiling:
                    break
                reservation=None
                if hasattr(self,'admission'):
                    admitted=self.admission.claim(self.owner,self.config.lease_seconds,
                        exclude_job_ids=tuple(self.active),worker_slot=slot,requires_cleanup=True,
                        cleanup_boot_id=self.boot_id,containment_id=self.containment_id)
                    node,reservation=admitted if admitted is not None else (None,None)
                else:
                    node = self.store.claim(self.owner,self.config.lease_seconds,max_workers=ceiling,
                                            exclude_job_ids=tuple(self.active),worker_slot=slot,
                                            requires_cleanup=True,cleanup_boot_id=self.boot_id,
                                            containment_id=self.containment_id)
                if node is None:
                    continue
                self.active[node['job_id']] = {'node':node,'slot':slot,'pid':None,'tripped':False,
                                              'cleaned':threading.Event(),'cleanup_verified':False,
                                              'process_cleanup_verified':False,'cancel_event':threading.Event(),'container_reservation':reservation}
                self.pool.submit(self._execute,node,slot)

    def _container_maintenance(self, capacity):
        if getattr(self,'docker',None) is None:
            return
        urgent=self.admission.maintenance_requested.is_set() if hasattr(self,'admission') else capacity==0
        if not urgent and time.monotonic()-self._last_container_maintenance<30:
            return
        if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
            return
        self._last_container_maintenance = time.monotonic()
        def maintain():
            try:
                maintenance_result=None
                if hasattr(self,'admission'):
                    maintenance_result=self.admission.maintenance()
                if hasattr(self.docker,'health'):
                    measured=self.docker.health()
                    if 'docker_engine' in measured:
                        self.components.update(measured)
                    else:
                        census=measured.get('census',{})
                        self.components['docker_engine']={'required':True,'state':measured.get('engine','unavailable'),
                            'checked_at':measured.get('checked_at'),'diagnostic':measured.get('diagnostic')}
                        self.components['containers']={'required':True,'state':'quarantined' if census.get('quarantined',0) else 'healthy',
                            'checked_at':measured.get('checked_at'),'active_count':census.get('active',0),'owned':census.get('owned',0),
                            'warm':census.get('warm',0),'preparing':census.get('preparing',0),'quarantined':census.get('quarantined',0)}
                    if isinstance(maintenance_result,dict) and 'requested_profile' in maintenance_result:
                        self.components.setdefault('containers',{}).update(
                            prepared_capacity_ready=maintenance_result['ready'],
                            readiness_reason=None if maintenance_result['ready'] else
                                maintenance_result.get('reason','awaiting_exact_preallocated_profile'))
            except Exception as exc:
                self.components['docker_engine']={'required':True,'state':'unavailable',
                    'checked_at':time.time(),'diagnostic':type(exc).__name__}
                LOG.error('Independent Docker maintenance failed: %s',exc)
        self._maintenance_thread=threading.Thread(target=maintain,name='docker-maintenance',daemon=True)
        self._maintenance_thread.start()

    def execution_bounds(self):
        native=min(86400,int(self.config.timeout_seconds)+300)
        docker=180
        for job in self.store.active_jobs():
            if job['kind']=='CODE_TEST':
                commands=self.store.coding_state(job['workflow_id'])['docker']['commands']
                docker=max(docker,sum(command['timeout_seconds'] for command in commands)+180)
        return {'native':native,'CODE_TEST':min(86400,docker),'CODE_INTEGRATE':1200}

    def submit_coding(self, project_id, objective, requirements, workflow_id=None):
        if self.coding is None:
            raise ValueError('Coding is unavailable until verified RAM and Docker deployment prerequisites pass')
        return self.coding.submit(project_id,objective,requirements,workflow_id)

    def coding_workflow(self, workflow_id):
        return self.store.coding_workflow(workflow_id)

    def cancel_coding(self, workflow_id):
        self.store.coding_state(workflow_id)
        self.cancel(workflow_id)
        return self.store.coding_workflow(workflow_id)

    def resume_coding(self, workflow_id, reason):
        if self.coding is None:
            raise ValueError('Coding is unavailable until verified RAM and Docker deployment prerequisites pass')
        return self.coding.resume(workflow_id,reason)

    def status(self):
        from .planning_governance import planning_readiness
        with self.lock:
            active = [{'job_id':key,'slot':value['slot'],'pid':value['pid'],
                       'route':value['node'].get('route')} for key,value in self.active.items()]
        return {'version':__version__,'service_identity':'SYSTEM','hardware':self.guard.latest_snapshot() if hasattr(self.guard,'latest_snapshot') else self.guard.snapshot(),
                'admission':self.store.transition_telemetry,'admission_capacity':self.last_capacity,
                'active':active,'quarantined_slots':dict(self.quarantined),
                'trip_errors':self.oracle.trip_errors,'pid':os.getpid(),
                'instance_id':self.heartbeat.instance_id,'process_started_at':self.heartbeat.started_at,
                'routing':self.store.routing_status(),'components':dict(self.components),'execution_bounds':self.execution_bounds(),
                'knowledge':self.knowledge_authority.status() if hasattr(self,'knowledge_authority') else
                    self.knowledge.status() if getattr(self,'knowledge',None) is not None else {'ready':False},
                'planning':planning_readiness(getattr(self.config,'coding_projects',{})),
                'source_root':str(Path(__file__).resolve().parents[2])}

    def cancel(self,workflow_id):
        with self.lock:
            jobs = self.store.cancel_workflow(workflow_id)
            ids = {node['job_id'] for node in jobs}
            for job_id,active in self.active.items():
                if active['node']['workflow_id']==workflow_id:
                    active['tripped'] = True
                    active.get('cancel_event',threading.Event()).set()
                    ids.add(job_id)
        for job_id in ids:
            self.runner.terminate(job_id)
        return jobs

    def run(self):
        try:
            while not self.stop_event.is_set():
                if getattr(self,'knowledge',None) is not None and time.monotonic()-self._last_knowledge_refresh>=30:
                    self.knowledge.request_refresh()
                    self._last_knowledge_refresh=time.monotonic()
                self.tick()
                self.heartbeat.completed_tick({'capacity':self.last_capacity,
                                               'active_count':len(self.active),
                                               'quarantined_count':len(self.quarantined),
                                               'hardware':self.guard.latest_snapshot() if hasattr(self.guard,'latest_snapshot') else self.guard.snapshot(),'components':dict(self.components),'execution_bounds':self.execution_bounds()})
                self.stop_event.wait(.1)
        finally:
            self.stop_event.set()
            with self.lock:
                ids = list(self.active)
                for active in self.active.values():
                    active.get('cancel_event',threading.Event()).set()
            for job_id in ids:
                self.runner.terminate(job_id)
            self.pool.shutdown(wait=True)
            if getattr(self,'objectives',None) is not None:
                try:
                    self.objectives.close()
                except Exception as error:
                    # Lost metric samples must not prevent containment cleanup.
                    LOG.error('Operational observation queue could not drain: %s',type(error).__name__)
            if self._maintenance_thread is not None:
                self._maintenance_thread.join(timeout=120)
            if self.docker is not None:
                # Prepared, unused containers survive controller restarts. The
                # durable pool reconciles their actual identity and health on
                # the next startup; active/orphan attempts are still removed.
                self.docker.reap_orphans(active_attempt_ids=set(),include_warm=False)
            self.oracle.close()
            if getattr(self,'knowledge',None) is not None:
                self.knowledge.close()
            self.store.close()
