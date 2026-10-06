"""Bounded observation of the real Warden's queue on its deployed storage.

This observer never submits or changes jobs. Attach it to the already locked
daemon, then submit ordinary representative work through the existing API.
Linux and synthetic-process diagnostics cannot establish Windows acceptance.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, is_dataclass
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import threading
import time

import psutil


_NATIVE_KINDS = {'MANIFEST_GENERATOR', 'CHAPTER_DRAFT', 'SYNTHESIS', 'CODE_PLAN',
                 'CODE_PLAN_REVIEW', 'CODE_TEST_AUTHOR', 'CODE_EDIT', 'CODE_REVIEW', 'CODE_RESEARCH'}
_FIXTURE_MARKERS = ('fixture', 'mock', 'emulat', 'simulat', 'synthetic', 'python-test')


def _fixture_receipt(receipt):
    if not isinstance(receipt, dict):
        return False
    if any(marker in str(receipt.get('execution_kind', '')).casefold() for marker in _FIXTURE_MARKERS):
        return True
    return any(receipt.get(flag) for flag in ('fixture', 'is_fixture', 'mock', 'simulation',
        'simulated', 'emulated', 'synthetic', 'test_only', 'dry_run'))


def _native_receipt_reason(receipt, output, binding, observed):
    """Validate a real callback/receipt join; a schema label alone attests nothing."""
    from .store import output_digest
    if _fixture_receipt(receipt):
        return 'fixture_or_simulation'
    if (not isinstance(receipt, dict) or receipt.get('execution_kind') != 'native_cli'
            or receipt.get('subscription_verified') is not True
            or receipt.get('process_identity_source') != 'owned_windows_process_handle'
            or type(receipt.get('exit_code')) is not int or receipt['exit_code'] != 0
            or not isinstance(receipt.get('session_id'), str) or not receipt['session_id'].strip()):
        return 'native_subscription_receipt_missing'
    if (not observed or observed.get('matches_configured_worker') is not True
            or observed.get('matches_configured_executable') is not True):
        return 'native_launch_identity_unverified'
    if (type(receipt.get('pid')) is not int or receipt['pid'] != observed['pid']
            or type(receipt.get('process_creation_filetime')) is not int
            or receipt['process_creation_filetime'] != observed.get('creation_filetime')
            or receipt.get('process_creation_time') != observed['created_at']):
        return 'native_process_identity_mismatch'
    route = binding.get('route')
    if not isinstance(route, dict):
        return 'reserved_route_missing'
    required = {'job_id': binding['job_id'], 'workflow_id': binding['workflow_id'],
        'attempt_id': binding['attempt_id'], 'fencing_token': binding['fencing_token'],
        'worker_slot': observed['slot'], 'worker_account': observed['configured_worker'],
        'provider': route['provider'], 'requested_model': route['model'],
        'requested_effort': route.get('reasoning_effort'),
        'route_reservation_id': route['reservation_id']}
    if (type(receipt.get('fencing_token')) is not int
            or any(receipt.get(key) != value for key, value in required.items())
            or receipt.get('selected_route') != route
            or receipt.get('reported_model') not in (None, route['model'])
            or receipt.get('reported_effort') not in (None, route.get('reasoning_effort'))):
        return 'reserved_attempt_or_route_mismatch'
    if (receipt.get('output_sha256') != output_digest(output)
            or not re.fullmatch(r'[0-9a-f]{64}', receipt.get('stdout_sha256', ''))
            or receipt.get('route_reservation_sha256') != hashlib.sha256(route['reservation_id'].encode()).hexdigest()):
        return 'native_output_or_reservation_digest_mismatch'
    return None


def _windows_creation_identity(pid):
    """Capture the exact physical Win32 timestamp, not a rounded PID assertion."""
    import ctypes
    from ctypes import wintypes
    from .windows import process_creation_filetime, filetime_unix_seconds
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ticks = process_creation_filetime(handle)
        return ticks, filetime_unix_seconds(ticks)
    finally:
        kernel.CloseHandle(handle)


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "maximum_ms": None}
    return {"count": len(ordered), "p50_ms": statistics.median(ordered),
            "p95_ms": ordered[math.ceil(.95 * len(ordered)) - 1],
            "p99_ms": ordered[math.ceil(.99 * len(ordered)) - 1],
            "maximum_ms": ordered[-1]}


def storage_identity(directory):
    """Actual Windows volume/filesystem; portable output is diagnostic only."""
    directory = Path(directory).resolve(strict=True)
    result = {"directory": str(directory), "filesystem": None,
              "volume_root": None, "volume_serial": None}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetVolumePathNameW.restype = wintypes.BOOL
        kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetVolumeInformationW.restype = wintypes.BOOL
        root, name, filesystem = (ctypes.create_unicode_buffer(32768),
                                  ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261))
        serial, component, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        if not kernel.GetVolumePathNameW(str(directory), root, len(root)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GetVolumeInformationW(root.value, name, len(name), ctypes.byref(serial),
                ctypes.byref(component), ctypes.byref(flags), filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        result.update(volume_root=root.value, filesystem=filesystem.value,
                      volume_serial=f"{serial.value:08x}")
    else:
        matches = [item for item in psutil.disk_partitions(all=True)
                   if directory == Path(item.mountpoint) or Path(item.mountpoint) in directory.parents]
        if matches:
            mount = max(matches, key=lambda item: len(item.mountpoint))
            result.update(volume_root=mount.mountpoint, filesystem=mount.fstype)
    result["free_bytes"] = psutil.disk_usage(str(directory)).free
    return result


class QueueLaunchObserver:
    """Instrument one real Runtime without changing queue behavior or authority.

    Captures the latest 4096 operations per method, bounds tracked identities,
    and writes a report every ten seconds and at shutdown. Timing stops before
    bookkeeping. It includes SQLite lock waits, transaction work and commit;
    it does not claim to isolate SQLite busy time from other claim work.
    """
    METHODS = ("claim", "heartbeat", "complete", "complete_coding", "get",
               "workflow", "list_workflows", "event_batch", "set_context", "fail")
    CAP = 4096

    def __init__(self, runtime, output):
        self.runtime, self.store = runtime, runtime.store
        self.output = Path(output)
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.originals, self.wrappers = {}, {}
        self.timings = {name: deque(maxlen=self.CAP) for name in self.METHODS}
        self.windowed_operations = set()
        self.counts, self.errors, self.maximum = Counter(), Counter(), Counter()
        self.claims, self.completed, self.native = {}, set(), {}
        self.claim_bindings, self.native_attested = {}, set()
        self.overlap_cohorts = set()
        self.completion_rejections = Counter()
        self.fixture_completions = 0
        self.native_completion_candidates = set()
        self.launch_identity_mismatch = False
        self.last_fencing = {}
        self.acquisition_times, self.claim_samples = [], []
        self.active_native = {}
        self.maximum_native = 0
        self.duplicate_claim = False
        self.fencing_violations = 0
        self.truncated = False
        self.observer_errors = 0
        self.maximum_bookkeeping_ms = 0.
        self.kinds, self.payload_sizes = Counter(), []
        self.route_events, self.route_reasons = Counter(), Counter()
        self.last_event_id = 0
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.settings = {}

    def __enter__(self):
        if getattr(self.runtime, "queue_launch_observer", None) is not None:
            raise RuntimeError("A queue launch observer is already attached")
        if os.name == "nt":
            from .windows import require_system, validate_private_directory
            require_system()
            private = Path(self.runtime.config.private_root).resolve(strict=True)
            if private not in self.output.resolve().parents:
                raise ValueError("Queue launch evidence must be inside the configured private_root")
            validate_private_directory(self.output.parent)
        self.storage = storage_identity(self.store.path.parent)
        with self.store._connection() as connection:
            self.settings = {name: connection.execute("PRAGMA " + name).fetchone()[0]
                             for name in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys")}
        self.database_bytes_at_start = self.store.path.stat().st_size
        self.output.mkdir(exist_ok=False)
        config = self.runtime.config
        projection = asdict(config) if is_dataclass(config) else vars(config)
        self.config_digest = hashlib.sha256(json.dumps(projection, sort_keys=True,
            default=str, separators=(",", ":")).encode()).hexdigest()
        try:
            self.runtime.queue_launch_observer = self
            for name in self.METHODS:
                if hasattr(self.store, name):
                    original = getattr(self.store, name)
                    self.originals[name] = original
                    self.wrappers[name] = self._wrap(name, original)
                    setattr(self.store, name, self.wrappers[name])
            self.thread = threading.Thread(target=self._publish_loop, daemon=True,
                                           name="queue-launch-observation")
            self.thread.start()
            self.publish()
        except BaseException:
            self._detach()
            raise
        return self

    def _wrap(self, name, original):
        @functools.wraps(original)
        def call(*args, **kwargs):
            began = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except BaseException:
                elapsed = (time.perf_counter() - began) * 1000
                self._timed_record(name, elapsed, args, kwargs, None, True)
                raise
            elapsed = (time.perf_counter() - began) * 1000
            self._timed_record(name, elapsed, args, kwargs, result, False)
            return result
        return call

    def _timed_record(self, *args):
        began = time.perf_counter()
        self._record_safely(*args)
        elapsed = (time.perf_counter() - began) * 1000
        with self.lock:
            self.maximum_bookkeeping_ms = max(self.maximum_bookkeeping_ms, elapsed)

    def _record_safely(self, name, elapsed, args, kwargs, result, failed):
        # Measurement failures never transform a committed job into a retry.
        try:
            with self.lock:
                self.counts[name] += 1
                self.errors[name] += int(failed)
                self.maximum[name] = max(self.maximum[name], elapsed)
                if len(self.timings[name]) == self.timings[name].maxlen:
                    self.windowed_operations.add(name)
                self.timings[name].append(elapsed)
                if name == "claim" and not failed and result is not None:
                    self._claim(result, elapsed, kwargs)
                if name in ("complete", "complete_coding") and not failed:
                    job_id = args[0] if args else kwargs["job_id"]
                    attempt = args[1] if len(args) > 1 else kwargs["attempt_id"]
                    fence = args[2] if len(args) > 2 else kwargs["fencing_token"]
                    key = (job_id, attempt, fence)
                    output = args[3] if len(args) > 3 else kwargs.get('output')
                    receipt = args[4] if len(args) > 4 else kwargs.get('receipt')
                    self._completion(key, output, receipt, result)
                    self.active_native.pop(key, None)
                if name == "event_batch" and not failed:
                    for event in result:
                        if event["id"] <= self.last_event_id:
                            continue
                        self.last_event_id = event["id"]
                        if event["timestamp"] < self.started_at:
                            continue
                        if event["event"] in ("ROUTE_SKIPPED", "ROUTE_UNAVAILABLE", "ROUTING_BACKOFF",
                                              "TASK_RETRY_BACKOFF", "RESOURCE_BACKOFF", "ROUTING_BLOCKED"):
                            self.route_events[event["event"]] += 1
                            detail = event["details"]
                            reason = detail.get("reason", detail.get("category", "all_candidates_unavailable"))
                            recognized = {"busy", "backlog", "quota", "auth", "provider", "timeout", "protocol",
                                "resource", "context", "configuration", "compatibility", "quota_pool_hold",
                                "asymmetric_review", "routing_operator_limit", "all_candidates_unavailable"}
                            self.route_reasons[reason if reason in recognized else "other"] += 1
        except Exception:
            with self.lock:
                self.observer_errors += 1

    def _claim(self, node, elapsed, kwargs):
        key = (node["job_id"], node["attempt_id"], node["fencing_token"])
        if key in self.claims:
            self.duplicate_claim = True
        if type(key[2]) is not int or key[2] < 1:
            self.fencing_violations += 1
        if len(self.claims) >= self.CAP:
            self.truncated = True
            return
        self.claims[key] = node["kind"]
        self.claim_bindings[key] = {field: node.get(field) for field in
            ('job_id', 'workflow_id', 'attempt_id', 'fencing_token', 'kind')}
        # Do not retain task text or the whole payload inside evidence state.
        self.claim_bindings[key]['route'] = dict(node['route']) if isinstance(node.get('route'), dict) else None
        previous_fence = self.last_fencing.get(key[0])
        if previous_fence is not None and key[2] <= previous_fence:
            self.fencing_violations += 1
        self.last_fencing[key[0]] = key[2]
        self.acquisition_times.append(elapsed)
        self.kinds[node["kind"]] += 1
        self.payload_sizes.append(len(json.dumps(node.get("payload", {})).encode("utf-8")))
        self.claim_samples.append({"elapsed_ms": elapsed, "kind": node["kind"],
            "fencing_token": key[2], "worker_slot": kwargs.get("worker_slot"),
            "admission_ceiling": kwargs.get("max_workers", 4),
            "job_id_sha256": hashlib.sha256(key[0].encode()).hexdigest()})

    @staticmethod
    def _physical_key(key, identity):
        return (*key, identity['pid'], identity['created_at'])

    def _completion(self, key, output, receipt, result):
        if key not in self.claims:
            return
        if (not isinstance(result, dict) or result.get('status') != 'COMPLETED'
                or any(result.get(name) != value for name, value in zip(
                    ('job_id', 'attempt_id', 'fencing_token'), key))
                or result.get('output') != output or result.get('receipt') != receipt):
            self.completion_rejections['store_completion_not_accepted'] += 1
            return
        if key in self.completed:
            return  # Idempotent completion replays do not inflate evidence.
        self.completed.add(key)
        if self.claims[key] not in _NATIVE_KINDS:
            return
        self.native_completion_candidates.add(key)
        self.fixture_completions += int(bool(_fixture_receipt(receipt)))
        observed = self.native.get(key)
        reason = _native_receipt_reason(receipt, output, self.claim_bindings[key], observed)
        if reason is not None:
            self.completion_rejections[reason] += 1
            return
        self.native_attested.add(self._physical_key(key, observed))

    def observe_native_launch(self, node, slot, pid):
        """Called from the real NativeRunner launch callback, never a model claim."""
        try:
            process = psutil.Process(pid)
            identity = {"pid": pid, "created_at": process.create_time(),
                        "executable": process.exe(), "account": process.username(), "slot": slot}
            identity['psutil_created_at'] = identity['created_at']
            identity['creation_filetime'] = None
            identity['configured_worker'] = None
            identity['matches_configured_worker'] = None
            identity['matches_configured_executable'] = None
            route = node.get('route') or {}
            provider = route.get('provider')
            spec = getattr(self.runtime.config, 'providers', {}).get(provider)
            if spec is not None:
                from cochem_mcp.providers import executable_prefix
                prefix = (executable_prefix(provider, spec['executable'])
                          if provider in ('codex', 'claude') else [spec['executable']])
                identity['matches_configured_executable'] = os.path.samefile(identity['executable'], prefix[0])
                if len(prefix) > 1:
                    actual_argv = process.cmdline()
                    identity['matches_configured_executable'] &= (
                        len(actual_argv) >= len(prefix) and all(
                            os.path.samefile(actual_argv[index], prefix[index]) for index in range(1, len(prefix))))
            if os.name == "nt":
                from .windows import _account_sid, _sid_text
                expected = self.runtime.config.workers[slot]["name"]
                identity['configured_worker'] = expected
                identity["matches_configured_worker"] = (
                    _sid_text(_account_sid(identity["account"])) == _sid_text(_account_sid(expected)))
                identity['creation_filetime'], identity['created_at'] = _windows_creation_identity(pid)
                if abs(identity['psutil_created_at'] - identity['created_at']) > .00001:
                    raise RuntimeError('Native process identity changed while observing its launch')
            key = (node["job_id"], node["attempt_id"], node["fencing_token"])
            with self.lock:
                if key not in self.claims or len(self.native) >= self.CAP:
                    return
                self.native[key] = identity
                self.active_native[key] = identity
                self.launch_identity_mismatch |= (identity['matches_configured_worker'] is False
                                                  or identity['matches_configured_executable'] is False)
                active = {}
                for attempt, item in self.active_native.items():
                    try:
                        live = psutil.Process(item["pid"])
                        if live.create_time() == item['psutil_created_at'] and live.is_running():
                            active[attempt] = item
                    except psutil.Error:
                        pass
                self.active_native = active
                self.maximum_native = max(self.maximum_native,
                    len({(item["pid"], item["created_at"]) for item in active.values()}))
                verified = {attempt: item for attempt, item in active.items()
                            if item['matches_configured_worker'] is True
                            and item['matches_configured_executable'] is True}
                if (len(verified) == 4 and len({item['slot'] for item in verified.values()}) == 4
                        and len({(item['pid'], item['created_at']) for item in verified.values()}) == 4):
                    if len(self.overlap_cohorts) < self.CAP:
                        self.overlap_cohorts.add(frozenset(self._physical_key(attempt, item)
                                                         for attempt, item in verified.items()))
                    else:
                        self.truncated = True
        except Exception:
            with self.lock:
                self.observer_errors += 1

    def report(self):
        with self.lock:
            config = self.runtime.config
            native_completed = {identity[:3] for identity in self.native_attested}
            native_slots = {self.native[key]["slot"] for key in native_completed}
            native_pids = {(item["pid"], item["created_at"]) for item in self.native.values()}
            latency = distribution(self.acquisition_times)
            operation_latency = {key: {**distribution(values),
                "total_calls": self.counts[key], "error_calls": self.errors[key],
                "maximum_all_calls_ms": self.maximum[key] or None}
                for key, values in self.timings.items()}
            settings_ok = self.settings == {"journal_mode": "wal", "synchronous": 1,
                                            "busy_timeout": 5000, "foreign_keys": 1}
            attested_overlap = any(cohort <= self.native_attested for cohort in self.overlap_cohorts)
            coverage = {"at_least_64_acquisitions": len(self.claims) >= 64,
                "at_least_16_native_completions": len(native_completed) >= 16,
                "multiple_job_kinds": len(self.kinds) >= 2,
                "four_live_native_processes_observed": self.maximum_native == 4,
                "four_overlapping_receipt_attested_completions": attested_overlap,
                "four_completed_worker_slots": len(native_slots) >= 4,
                "no_native_launch_identity_mismatch": not self.launch_identity_mismatch,
                "all_native_completions_attested": bool(self.native_completion_candidates) and
                    len(self.native_completion_candidates) == len(self.native_attested),
                "native_worker_accounts_match_configured_slots": bool(native_completed) and all(
                    self.native[key]["matches_configured_worker"] for key in native_completed),
                "heartbeat_activity": self.counts["heartbeat"] > 0,
                "read_activity": any(self.counts[key] for key in ("get", "workflow", "event_batch")),
                "oracle_context_activity": self.counts["set_context"] > 0,
                "settings_match_production": settings_ok,
                "no_duplicate_claim_identity": not self.duplicate_claim,
                "valid_fencing_tokens": self.fencing_violations == 0,
                "no_observer_error": self.observer_errors == 0,
                "sample_not_truncated": not self.truncated}
            complete = os.name == "nt" and all(coverage.values())
            within_target = bool(self.acquisition_times) and latency["maximum_ms"] < 5
            status = ("pending_windows_launch" if os.name != "nt" else
                      "pending_representative_workload" if not complete else
                      "conditionally_accepted_launch_measured_optimization_review" if not within_target else
                      "accepted_windows_launch_target_met")
            return {"schema": 1, "kind": "actual-warden-queue-launch-observation",
                "platform": os.name, "platform_release": platform.release(),
                "python_version": platform.python_version(), "sqlite_version": __import__("sqlite3").sqlite_version,
                "started_at": self.started_at, "elapsed_seconds": time.monotonic() - self.started_monotonic,
                "topology": "one deployed controller JobStore; actual native subprocess workers; shared four-seat admission",
                "controller_pid": os.getpid(), "configured_slots": len(config.slot_roots),
                "actual_database": str(self.store.path), "storage": self.storage,
                "database_bytes_at_start": self.database_bytes_at_start,
                "database_scope": "actual deployed database; ordinary live workload observed; observer submits no jobs",
                "sqlite_settings": self.settings,
                "lease_seconds": config.lease_seconds, "heartbeat_seconds": config.heartbeat_seconds,
                "routing_policy_sha256": config.routing.digest,
                "effective_configuration_sha256": self.config_digest,
                "configuration_hash_scope": "complete effective PipelineConfig projection; raw credentials/arguments are not emitted",
                "measurement_scope": "entire actual claim call including SQLite lock wait, transaction and commit; instrumentation bookkeeping excluded",
                "busy_wait_scope": "included in whole-call timings; not independently separated by SQLite",
                "percentile_scope": "latest 4096 calls per operation; all-call maxima retained; first 4096 acquisitions tracked and identity overflow prevents acceptance",
                "windowed_operations": sorted(self.windowed_operations),
                "observation_overhead": {"maximum_record_bookkeeping_ms": self.maximum_bookkeeping_ms,
                    "operation_samples_per_method": self.CAP, "maximum_acquisition_identities": self.CAP,
                    "isolated_memory_overhead_measured": False,
                    "scope": "record bookkeeping measured after the store call; wrapper clocks, publication and callbacks can still affect scheduling/RSS"},
                "claim_acquisition": latency, "operations": operation_latency,
                "workload": {"job_kinds": dict(self.kinds),
                    "synthetic_outputs": True if self.fixture_completions else
                        False if self.native_completion_candidates and
                            len(self.native_completion_candidates) == len(self.native_attested) else None,
                    "observed_fixture_completions": self.fixture_completions,
                    "native_completion_candidates": len(self.native_completion_candidates),
                    "completion_rejections": dict(self.completion_rejections),
                    "payload_bytes_min": min(self.payload_sizes, default=None),
                    "payload_bytes_max": max(self.payload_sizes, default=None),
                    "completed_native_attempts": len(native_completed), "distinct_native_processes": len(native_pids),
                    "native_executable_paths": sorted({value["executable"] for value in self.native.values()}),
                    "native_accounts": sorted({value["account"] for value in self.native.values()}),
                    "maximum_live_native_processes": self.maximum_native},
                "coverage": coverage, "claim_samples": list(self.claim_samples),
                "routing_observations": {"events": dict(self.route_events), "reasons": dict(self.route_reasons),
                    "scope": "new events read by the real runtime event cursor; no provider failure or quota exhaustion is induced"},
                "optimization_target_ms": 5, "optimization_target_met": within_target,
                "historical_29_347_ms_owner_accepted": True,
                "launch_measurement_complete": complete, "native_windows_acceptance": complete and within_target,
                "acceptance_status": status, "redesign_review_required": complete and not within_target,
                "limitations": ["Representative workload must be selected by the operator; counters cannot establish semantic representativeness.",
                    "Observed fencing/duplicate identities and native concurrency are monitoring evidence, not injected race or stale-write tests.",
                    "Bounded instrumentation and periodic reporting consume CPU/memory and may affect observed scheduling; zero overhead is not claimed.",
                    "No queue redesign or job admission change is performed by this observer."]}

    def publish(self):
        report = self.report()
        temporary = self.output / "queue-launch.json.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(self.output / "queue-launch.json")
        return report

    def _publish_loop(self):
        while not self.stop.wait(10):
            try:
                self.publish()
            except OSError:
                with self.lock:
                    self.observer_errors += 1

    def _detach(self):
        self.stop.set()
        if getattr(self, "thread", None) is not None and self.thread.ident is not None:
            self.thread.join(timeout=15)
        for name, original in self.originals.items():
            if getattr(self.store, name) is self.wrappers[name]:
                setattr(self.store, name, original)
        if getattr(self.runtime, "queue_launch_observer", None) is self:
            self.runtime.queue_launch_observer = None

    def __exit__(self, *_):
        self._detach()
        self.publish()
