"""One controller admission budget for native jobs and prepared Docker trees.

The Warden is a protected singleton. This separate admission lock serializes
its JobStore claims, warm-slot transfers, and background pool maintenance;
it must never be the Runtime lock used by worker heartbeats/cancellation.
All slow Docker operations run in ``maintenance``, never in ``claim``.
Docker also enforces the published residual cap in its SQLite transaction.
"""
from __future__ import annotations

import threading
import time

from .container_policy import DockerPolicy
from .containers import DockerRunner,ContainerCapacityError,ContainerCleanupError
from .coding_store import CODING_KINDS

_NATIVE_KINDS=tuple(kind for kind in ('MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS','PREFLIGHT_REQUEST',*CODING_KINDS)
                    if kind not in {'CODE_REQUEST','CODE_TEST'})


class JointAdmission:
    def __init__(self,store,docker,capacity=0,*,lock=None,max_capacity=4):
        if type(max_capacity) is not int or not 1 <= max_capacity <= 256:
            raise ValueError('Configured joint capacity must be in 1..256')
        self.max_capacity=max_capacity
        self.store,self.docker=store,docker
        self.lock=lock if lock is not None else threading.Lock()
        self._budget_lock=threading.RLock()
        self._capacity=0
        self._pending_native=0
        self._drain_target=None
        self.maintenance_requested=threading.Event()
        self.update_capacity(capacity)

    def _native_active(self):
        return sum(job['kind']!='CODE_TEST' for job in self.store.active_jobs())

    def _publish_locked(self):
        residual=max(0,self._capacity-self._native_active()-self._pending_native)
        if self.store.execution_quarantines():
            residual=0
        if self.docker is not None:
            if self.docker.census()['quarantined']:
                residual=0
            self.docker.set_capacity(residual)
        return residual

    def update_capacity(self,capacity):
        """Publish pressure immediately, without waiting for slow pool creation."""
        if type(capacity) is not int or not 0<=capacity<=self.max_capacity:
            raise ValueError('Joint admission capacity must respect the configured execution ceiling')
        with self._budget_lock:
            changed=capacity!=self._capacity
            self._capacity=capacity
            residual=self._publish_locked()
            if capacity==0 or changed:
                self.maintenance_requested.set()
            return residual

    def _request_drain(self,target):
        with self._budget_lock:
            self._drain_target=target if self._drain_target is None else min(self._drain_target,target)
            self.maintenance_requested.set()

    def _native_pending(self):
        # Read only bounded eligibility metadata, not prompts or artifact data.
        with self.store._connection() as db:
            return db.execute('''SELECT 1 FROM pipeline_jobs j JOIN pipeline_jobs root ON root.job_id=j.workflow_id
                LEFT JOIN pipeline_routing_jobs r ON r.job_id=j.job_id
                LEFT JOIN coding_controller_retries cr ON cr.job_id=j.job_id
                WHERE j.status IN ('PENDING','PENDING_RETRY') AND root.status='IN_PROGRESS'
                AND j.kind NOT IN ('CODE_REQUEST','MACRO_PLANNING_REQUEST','CODE_TEST')
                AND (r.state IS NULL OR (r.state NOT IN ('BLOCKED','FAILED','COMPLETED') AND coalesce(r.next_eligible_at,0)<=?))
                AND coalesce(cr.next_eligible_at,0)<=? LIMIT 1''',(time.time(),time.time())).fetchone() is not None

    def _captured_runner(self,node):
        captured=DockerPolicy.from_dict(self.store.coding_state(node['workflow_id'])['docker'])
        current=self.docker.policy
        if captured.warm_pool_size<=0:
            raise ValueError('Production Docker testing requires preallocated warm capacity')
        if (not captured.enabled or captured.image not in current.allowed_images
                or captured.endpoint!=current.endpoint or captured.executable!=current.executable
                or any(getattr(captured,key)>getattr(current,key) for key in
                       ('memory_mb','cpus','pids_limit','tmpfs_mb','max_containers'))):
            raise ValueError('Captured Docker policy exceeds the current protected deployment bounds')
        return DockerRunner(captured,self.docker.state_root,
                            trusted_operator=self.docker.trusted_operator,
                            host_boot_id=self.docker.host_boot_id)

    def _unstarted_failure(self,node,error,*,category='resource'):
        # The caller has never returned this node to an execution thread.
        # reserve_attempt(retire_incompatible=False) never invokes Docker.
        records=[] if self.docker is None else [row for row in self.docker.census()['containers']
                                                if row['attempt_id']==node['attempt_id']]
        if records:
            # A transferred warm tree cannot be declared stopped from a failed
            # metadata operation. Preserve the physical hold for trusted cleanup.
            self.store.quarantine_execution(node['job_id'],node['attempt_id'],node['fencing_token'],
                node.get('worker_slot'),'Container reservation failed before its handoff could be confirmed')
            self.store.fail(node['job_id'],node['attempt_id'],node['fencing_token'],str(error),retry=False,category='resource')
            return
        self.store.clear_execution_quarantine(node['job_id'],node['attempt_id'],node['fencing_token'])
        self.store.fail(node['job_id'],node['attempt_id'],node['fencing_token'],str(error),retry=True,
                        category=category,retry_after_seconds=5,
                        hold_scope='job' if category=='configuration' else None)

    def claim(self,owner,lease_seconds=1800,ceiling=None,*,exclude_job_ids=(),worker_slot=None,
              requires_cleanup=True,cleanup_boot_id=None,containment_id=None):
        """Nonblocking scheduler admission; return ``(node,reservation)`` or None.

        A CODE_TEST transfers/reserves its exact captured Docker profile before
        being exposed to a worker. Native jobs obtain a residual seat only after
        old idle containers have actually been drained by maintenance.
        """
        if ceiling is not None:
            self.update_capacity(ceiling)
        if not self.lock.acquire(blocking=False):
            return None
        try:
            with self._budget_lock:
                capacity=self._capacity
                residual=self._publish_locked()
            if capacity<=0:
                return None
            kwargs={'exclude_job_ids':exclude_job_ids,'worker_slot':worker_slot,'requires_cleanup':requires_cleanup,
                    'cleanup_boot_id':cleanup_boot_id,'containment_id':containment_id}
            if self.docker is not None:
                census=self.docker.census()
                if census['quarantined']:
                    self.docker.set_capacity(0)
                    self.maintenance_requested.set()
                    return None
                if census['owned']>residual:
                    self._request_drain(residual)
                    return None
                node=self.store.claim(owner,lease_seconds,max_workers=capacity,allowed_kinds=('CODE_TEST',),**kwargs)
                if node is not None:
                    try:
                        runner=self._captured_runner(node)
                        reservation=runner.reserve_attempt(node['job_id'],node['attempt_id'],retire_incompatible=False,
                            require_warm=True,request_started_at=node['created_at'])
                    except ContainerCapacityError as exc:
                        if not self.docker.request_preparation(runner.policy.pool_digest,node['job_id']):
                            exc=ContainerCapacityError('Prepared-container request queue is full (64 profiles); '
                                'job remains held until FIFO capacity is available')
                        self._unstarted_failure(node,exc)
                        self.maintenance_requested.set()
                        return None
                    except (ValueError,ContainerCleanupError) as exc:
                        self._unstarted_failure(node,exc,category='configuration')
                        return None
                    with self._budget_lock:
                        self._drain_target=None
                    self.docker.complete_preparation_request(runner.policy.pool_digest)
                    self.maintenance_requested.set()
                    return node,reservation
                if self.docker.preparation_requests():
                    # Preserve the first queued profile's seat through its
                    # retry delay instead of letting native traffic evict it.
                    return None
                if not self._native_pending():
                    return None
                target=max(0,residual-1)
                if census['owned']>target:
                    self._request_drain(target)
                    return None
            with self._budget_lock:
                if self._capacity<=self._native_active()+self._pending_native:
                    return None
                self._pending_native+=1
                self._publish_locked()
            try:
                node=self.store.claim(owner,lease_seconds,max_workers=capacity,allowed_kinds=_NATIVE_KINDS,**kwargs)
            finally:
                with self._budget_lock:
                    self._pending_native-=1
                    self._publish_locked()
            if node is None:
                return None
            with self._budget_lock:
                if self._capacity<self._native_active()+(self.docker.census()['owned'] if self.docker is not None else 0):
                    self._unstarted_failure(node,'Hardware pressure revoked admission before worker handoff')
                    self._publish_locked()
                    return None
                self._drain_target=None
            return node,None
        finally:
            self.lock.release()

    def maintenance(self,ceiling=None):
        """Background-only: reap, drain, and prewarm under the admission lock."""
        if ceiling is not None:
            self.update_capacity(ceiling)
        if self.docker is None or not self.lock.acquire(blocking=False):
            return None
        try:
            active={job['attempt_id'] for job in self.store.active_jobs() if job['kind']=='CODE_TEST'}
            self.docker.reap_orphans(active_attempt_ids=active)
            with self._budget_lock:
                residual=self._publish_locked()
                target=min(residual,self.docker.policy.max_containers)
                if self._drain_target is not None:
                    target=min(target,self._drain_target)
            if self.docker.census()['quarantined']:
                self.docker.set_capacity(0)
                return {'blocked':'unverified_container_cleanup','census':self.docker.census()}
            # Reconstruct the FIFO from protected persistent state on every
            # pass. Never replay a stored executable policy or a cancelled job.
            pending=[]
            for request in self.docker.preparation_requests():
                digest,job_id=request['profile_digest'],request['job_id']
                try:
                    job=self.store.get(job_id)
                    if (job['status'] not in ('PENDING','PENDING_RETRY','IN_PROGRESS')
                            or self.store.get(job['workflow_id'])['status']!='IN_PROGRESS'):
                        raise ValueError('Prepared-pool request no longer has live work')
                    runner=self._captured_runner(job)
                    if runner.policy.pool_digest!=digest:
                        raise ValueError('Prepared-pool request contradicts the captured policy')
                except (KeyError,ValueError):
                    self.docker.complete_preparation_request(digest)
                    continue
                pending.append((digest,runner))
            if pending:
                digest,runner=pending[0]
                # Keep this FIFO request until its prepared slot is handed off.
                # Otherwise a later incompatible profile could immediately
                # evict the first one before its controlled retry becomes due.
                result=runner.prepare_requested_profile(target)
                if result['ready']:
                    self.maintenance_requested.clear()
                return {'requested_profile':digest,**result}
            if self._native_pending():
                # Do not cold-create an idle tree only to drain it on the very
                # next native claim. Actual eligible work keeps one vacant seat;
                # each further claim can request another bounded idle drain.
                target=min(target,max(0,residual-1))
            result=self.docker.prepare_pool(target=target)
            with self._budget_lock:
                # A pressure update while Docker was working must request a
                # second maintenance pass instead of clearing the new signal.
                if self._capacity-self._native_active()>=target:
                    self.maintenance_requested.clear()
            return result
        finally:
            self.lock.release()
