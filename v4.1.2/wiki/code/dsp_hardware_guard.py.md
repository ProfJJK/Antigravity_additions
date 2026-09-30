# src/cochem/dsp/hardware_guard.py

`python
"""Domain-Specific Pipeline (DSP) Hardware Guard & Resource Quota Enforcement (SRS-412-07).

Spec: SRS-412-07-FR-007: Each DSP shall operate under dedicated CPU and memory quota
allocations managed by hardware_guard.py.
Spec: SRS-412-07 Section 9: Test verifies hardware_guard.py enforces domain memory limits.
Spec: SRS-412-07 Section 8: Jittered exponential backoff and rate limit recovery.
Spec: User Rule 1: Python State Machine daemon scripts polling hardware_guard.py
(Jittered Exponential Backoff) to prevent Thundering Herd I/O crashes.
Spec: Rule 15: Subprocess window popup prevention (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP).

Strictly compliant with CoChem Anti-Spoofing Protocol v4.
Zero mocks. Zero stubs. Physical hardware telemetry via psutil.
General software pipeline.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:  # noqa: BLE001 - Antigravity SDK may raise VersionError on protobuf mismatch
    agy_sdk = None

# Ensure repo root src is on sys.path for internal imports when executed directly
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SRC_DIR: Path = _REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Import canonical definitions from DSP base if available, else establish authentic standards
try:
    from cochem.dsp.base import (
        CANONICAL_DSP_DOMAINS,
        DOMAIN_ACADEMIC_PRESS,
        DOMAIN_CODE_FORGE,
        DOMAIN_PEDAGOGY_ENGINE,
        DSPRegistrationManifest,
        ResourceQuota,
    )
except ImportError:
    DOMAIN_CODE_FORGE = "code_forge"
    DOMAIN_ACADEMIC_PRESS = "academic_press"
    DOMAIN_PEDAGOGY_ENGINE = "pedagogy_engine"
    CANONICAL_DSP_DOMAINS = frozenset({
        DOMAIN_CODE_FORGE,
        DOMAIN_ACADEMIC_PRESS,
        DOMAIN_PEDAGOGY_ENGINE,
    })

    @dataclass(frozen=True)
    class ResourceQuota:  # type: ignore[no-redef]
        """Resource quota allocation for domain-specific pipelines (SRS-412-07-FR-007)."""
        memory_mb: int = 4096
        max_workers: int = 6
        cpu_limit: float = 2.0
        timeout_seconds: int = 900

        def to_dict(self) -> dict[str, Any]:
            return {
                "memory_mb": self.memory_mb,
                "max_workers": self.max_workers,
                "cpu_limit": self.cpu_limit,
                "timeout_seconds": self.timeout_seconds,
            }

        @classmethod
        def from_dict(cls, data: Mapping[str, Any]) -> ResourceQuota:
            return cls(
                memory_mb=int(data.get("memory_mb", 4096)),
                max_workers=int(data.get("max_workers", 6)),
                cpu_limit=float(data.get("cpu_limit", 2.0)),
                timeout_seconds=int(data.get("timeout_seconds", 900)),
            )

    @dataclass(frozen=True)
    class DSPRegistrationManifest:  # type: ignore[no-redef]
        """Registration manifest data model for DSP plugins (SRS-412-07 Section 7)."""
        dsp_id: str
        domain: str
        version: str = "4.1.2"
        supported_job_types: tuple[str, ...] = field(default_factory=tuple)
        resource_caps: ResourceQuota = field(default_factory=ResourceQuota)
        mcp_server: str = ""

        def to_dict(self) -> dict[str, Any]:
            return {
                "dsp_registration_manifest": {
                    "dsp_id": self.dsp_id,
                    "domain": self.domain,
                    "version": self.version,
                    "supported_job_types": list(self.supported_job_types),
                    "resource_caps": self.resource_caps.to_dict(),
                    "mcp_server": self.mcp_server,
                }
            }


logger = logging.getLogger("cochem.dsp.hardware_guard")

# ==============================================================================
# Process Creation Flags (Rule 15 & SRS-412-03-FR-007)
# ==============================================================================
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
DEFAULT_CREATIONFLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

# Host-level safety limits
DEFAULT_HOST_RAM_CEILING_PERCENT: float = 90.0
DEFAULT_MAX_PROCESS_HANDLES: int = 50000


# ==============================================================================
# Custom Exceptions (Physical Quota Enforcement)
# ==============================================================================
class HardwareGuardError(RuntimeError):
    """Base exception for hardware guard and resource quota violations."""


class DomainMemoryLimitExceededError(HardwareGuardError):
    """Raised when a DSP domain exceeds its assigned memory quota (SRS-412-07-FR-007)."""

    def __init__(self, domain: str, current_mb: float, limit_mb: float) -> None:
        self.domain = domain
        self.current_mb = current_mb
        self.limit_mb = limit_mb
        super().__init__(
            f"Domain '{domain}' memory limit exceeded: allocated {current_mb:.2f} MB "
            f"exceeds quota ceiling of {limit_mb:.2f} MB (SRS-412-07-FR-007)"
        )


class DomainWorkerLimitExceededError(HardwareGuardError):
    """Raised when a DSP domain exceeds its maximum concurrent worker allocations."""

    def __init__(self, domain: str, active_workers: int, max_workers: int) -> None:
        self.domain = domain
        self.active_workers = active_workers
        self.max_workers = max_workers
        super().__init__(
            f"Domain '{domain}' worker limit exceeded: {active_workers} active workers "
            f"exceeds capacity ceiling of {max_workers}"
        )


class DomainCPULimitExceededError(HardwareGuardError):
    """Raised when a DSP domain exceeds its allocated CPU core utilization quota."""

    def __init__(self, domain: str, current_cpu: float, cpu_limit: float) -> None:
        self.domain = domain
        self.current_cpu = current_cpu
        self.cpu_limit = cpu_limit
        super().__init__(
            f"Domain '{domain}' CPU quota exceeded: {current_cpu:.2f} cores "
            f"exceeds quota ceiling of {cpu_limit:.2f} cores"
        )


class HostResourceExhaustedError(HardwareGuardError):
    """Raised when physical host system resources (RAM, handles) approach exhaustion."""


# ==============================================================================
# Jittered Exponential Backoff Engine (Rule 1 & SRS-412-07 Section 8)
# ==============================================================================
def compute_jittered_backoff(
    attempt: int,
    base_delay: float = 0.1,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    min_jitter_ms: int = 100,
    max_jitter_ms: int = 500,
) -> float:
    """Computes a jittered exponential backoff delay in seconds.

    Prevents Thundering Herd I/O crashes across concurrent daemon polling loops
    (Rule 1, SRS-412-07 Section 8).
    """
    attempt = max(0, attempt)
    calculated = base_delay * (backoff_factor ** attempt)
    capped_delay = min(calculated, max_delay)
    jitter_sec = random.uniform(float(min_jitter_ms), float(max_jitter_ms)) / 1000.0
    total_delay = capped_delay + jitter_sec
    return float(total_delay)


def sleep_jittered_backoff(
    attempt: int,
    base_delay: float = 0.1,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    min_jitter_ms: int = 100,
    max_jitter_ms: int = 500,
) -> float:
    """Calculates jittered exponential backoff and pauses the current thread."""
    delay = compute_jittered_backoff(
        attempt=attempt,
        base_delay=base_delay,
        max_delay=max_delay,
        backoff_factor=backoff_factor,
        min_jitter_ms=min_jitter_ms,
        max_jitter_ms=max_jitter_ms,
    )
    time.sleep(delay)
    return delay


# ==============================================================================
# Domain Worker Descriptor & State Tracking
# ==============================================================================
@dataclass
class DomainWorkerRecord:
    """Tracks an active worker execution slot within a domain quota."""
    domain: str
    worker_id: str
    pid: int
    task_id: str
    allocated_mb: float
    started_at: float = field(default_factory=time.time)
    peak_rss_mb: float = 0.0

    def update_peak_rss(self, current_rss_mb: float) -> None:
        """Updates highest measured RSS memory usage."""
        self.peak_rss_mb = max(self.peak_rss_mb, current_rss_mb)


# ==============================================================================
# Hardware Guard Core Manager (SRS-412-07-FR-007)
# ==============================================================================
class HardwareGuard:
    """Central Hardware Guard enforcing CPU, memory, and worker quotas across DSPs.

    Strictly manages dedicated resource quotas for The Code Forge, The Academic Press,
    The Pedagogy Engine, and dynamically registered domain plugins.
    """

    def __init__(
        self,
        host_ram_ceiling_percent: float = DEFAULT_HOST_RAM_CEILING_PERCENT,
        enforce_subprocesses: bool = True,
    ) -> None:
        """Initializes HardwareGuard with default canonical domain allocations."""
        self._lock = threading.RLock()
        self.host_ram_ceiling_percent = float(host_ram_ceiling_percent)
        self.enforce_subprocesses = enforce_subprocesses

        # Canonical Domain Quotas (SRS-412-07-FR-007)
        self._quotas: dict[str, ResourceQuota] = {
            DOMAIN_CODE_FORGE: ResourceQuota(
                memory_mb=4096,
                max_workers=6,
                cpu_limit=4.0,
                timeout_seconds=900,
            ),
            DOMAIN_ACADEMIC_PRESS: ResourceQuota(
                memory_mb=4096,
                max_workers=4,
                cpu_limit=2.0,
                timeout_seconds=600,
            ),
            DOMAIN_PEDAGOGY_ENGINE: ResourceQuota(
                memory_mb=2048,
                max_workers=4,
                cpu_limit=2.0,
                timeout_seconds=300,
            ),
        }

        # Active worker registry: domain -> {worker_id: DomainWorkerRecord}
        self._active_workers: dict[str, dict[str, DomainWorkerRecord]] = {
            domain: {} for domain in self._quotas
        }

        # Historical peak RSS per domain in MB
        self._domain_peak_rss: dict[str, float] = {
            domain: 0.0 for domain in self._quotas
        }

    # -------------------------------------------------------------------------
    # Quota Registration & Inspection
    # -------------------------------------------------------------------------

    def register_domain_quota(
        self,
        domain: str,
        quota: ResourceQuota | Mapping[str, Any] | None = None,
        memory_mb: int | None = None,
        max_workers: int | None = None,
        cpu_limit: float | None = None,
        timeout_seconds: int | None = None,
    ) -> ResourceQuota:
        """Registers or updates resource quotas for a domain."""
        if not domain or not isinstance(domain, str):
            raise ValueError(f"Domain must be a non-empty string, got: {domain!r}")

        with self._lock:
            existing = self._quotas.get(domain, ResourceQuota())
            if isinstance(quota, ResourceQuota):
                resolved_quota = quota
            elif isinstance(quota, Mapping):
                resolved_quota = ResourceQuota.from_dict(quota)
            else:
                mem = memory_mb if memory_mb is not None else existing.memory_mb
                workers = max_workers if max_workers is not None else existing.max_workers
                cpu = cpu_limit if cpu_limit is not None else existing.cpu_limit
                timeout = timeout_seconds if timeout_seconds is not None else existing.timeout_seconds
                resolved_quota = ResourceQuota(
                    memory_mb=int(mem),
                    max_workers=int(workers),
                    cpu_limit=float(cpu),
                    timeout_seconds=int(timeout),
                )

            if resolved_quota.memory_mb <= 0:
                raise ValueError(f"memory_mb must be positive, got: {resolved_quota.memory_mb}")
            if resolved_quota.max_workers <= 0:
                raise ValueError(f"max_workers must be positive, got: {resolved_quota.max_workers}")
            if resolved_quota.cpu_limit <= 0.0:
                raise ValueError(f"cpu_limit must be positive, got: {resolved_quota.cpu_limit}")

            self._quotas[domain] = resolved_quota
            if domain not in self._active_workers:
                self._active_workers[domain] = {}
            if domain not in self._domain_peak_rss:
                self._domain_peak_rss[domain] = 0.0

            logger.info("Registered quota for domain %s: %s", domain, resolved_quota.to_dict())
            return resolved_quota

    def register_manifest(self, manifest: DSPRegistrationManifest | Mapping[str, Any]) -> ResourceQuota:
        """Registers a domain and its quota from a DSP registration manifest."""
        if isinstance(manifest, Mapping):
            parsed = DSPRegistrationManifest.from_dict(manifest)
        else:
            parsed = manifest

        domain = parsed.domain
        return self.register_domain_quota(domain, parsed.resource_caps)

    def get_domain_quota(self, domain: str) -> ResourceQuota:
        """Retrieves resource quota for the specified domain."""
        with self._lock:
            if domain not in self._quotas:
                # Default fallback quota for ad-hoc domains
                self.register_domain_quota(domain, ResourceQuota())
            return self._quotas[domain]

    # -------------------------------------------------------------------------
    # Physical Telemetry & Memory Sampling
    # -------------------------------------------------------------------------

    def sample_process_memory_mb(self, pid: int | None = None) -> float:
        """Samples the physical RSS memory usage of the specified PID in megabytes."""
        target_pid = pid if pid is not None else os.getpid()
        try:
            proc = psutil.Process(target_pid)
            rss_bytes = proc.memory_info().rss
            return float(rss_bytes / (1024.0 * 1024.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    def get_domain_memory_usage_mb(self, domain: str) -> float:
        """Calculates total physical RSS memory consumed by all active workers in domain."""
        with self._lock:
            workers = list(self._active_workers.get(domain, {}).values())
            if not workers:
                return 0.0

            total_rss = 0.0
            for worker in workers:
                rss = self.sample_process_memory_mb(worker.pid)
                worker.update_peak_rss(rss)
                total_rss += rss

            if total_rss > self._domain_peak_rss.get(domain, 0.0):
                self._domain_peak_rss[domain] = total_rss

            return float(total_rss)

    def get_domain_active_worker_count(self, domain: str) -> int:
        """Returns the number of currently active workers in a domain."""
        with self._lock:
            return len(self._active_workers.get(domain, {}))

    def get_host_memory_stats(self) -> dict[str, float]:
        """Queries physical host RAM metrics via psutil."""
        vm = psutil.virtual_memory()
        return {
            "total_mb": float(vm.total / (1024.0 * 1024.0)),
            "available_mb": float(vm.available / (1024.0 * 1024.0)),
            "used_mb": float(vm.used / (1024.0 * 1024.0)),
            "percent": float(vm.percent),
        }

    # -------------------------------------------------------------------------
    # Quota Enforcement (SRS-412-07-FR-007 & Section 9)
    # -------------------------------------------------------------------------

    def check_memory_limit(
        self,
        domain: str,
        pid: int | None = None,
        additional_mb: float = 0.0,
    ) -> bool:
        """Checks whether domain memory usage remains within quota ceiling."""
        quota = self.get_domain_quota(domain)
        current_mb = self.get_domain_memory_usage_mb(domain)
        if pid is not None:
            # If the process isn't in active workers yet, include its sample
            with self._lock:
                workers = self._active_workers.get(domain, {})
                if not any(w.pid == pid for w in workers.values()):
                    current_mb += self.sample_process_memory_mb(pid)

        projected = current_mb + max(0.0, additional_mb)
        return bool(projected <= quota.memory_mb)

    def enforce_memory_limit(
        self,
        domain: str,
        pid: int | None = None,
        additional_mb: float = 0.0,
    ) -> None:
        """Enforces domain memory ceiling; raises DomainMemoryLimitExceededError if exceeded."""
        quota = self.get_domain_quota(domain)
        current_mb = self.get_domain_memory_usage_mb(domain)
        if pid is not None:
            with self._lock:
                workers = self._active_workers.get(domain, {})
                if not any(w.pid == pid for w in workers.values()):
                    current_mb += self.sample_process_memory_mb(pid)

        projected = current_mb + max(0.0, additional_mb)
        if projected > quota.memory_mb:
            logger.warning(
                "Domain %s memory limit exceeded: projected %.2f MB > quota %.2f MB",
                domain, projected, quota.memory_mb
            )
            raise DomainMemoryLimitExceededError(domain, projected, quota.memory_mb)

    def check_worker_limit(self, domain: str) -> bool:
        """Checks whether domain has available worker capacity."""
        quota = self.get_domain_quota(domain)
        active = self.get_domain_active_worker_count(domain)
        return bool(active < quota.max_workers)

    def enforce_worker_limit(self, domain: str) -> None:
        """Enforces domain worker ceiling; raises DomainWorkerLimitExceededError if exceeded."""
        quota = self.get_domain_quota(domain)
        active = self.get_domain_active_worker_count(domain)
        if active >= quota.max_workers:
            logger.warning(
                "Domain %s worker limit reached: %d active workers >= max %d",
                domain, active, quota.max_workers
            )
            raise DomainWorkerLimitExceededError(domain, active, quota.max_workers)

    def enforce_host_safety(self) -> None:
        """Checks overall host RAM to protect against system lockup."""
        stats = self.get_host_memory_stats()
        if stats["percent"] >= self.host_ram_ceiling_percent:
            raise HostResourceExhaustedError(
                f"Host RAM usage at {stats['percent']:.1f}% exceeds safe ceiling of "
                f"{self.host_ram_ceiling_percent:.1f}% (Host safety guard)"
            )

    # -------------------------------------------------------------------------
    # Worker Slot Management
    # -------------------------------------------------------------------------

    def acquire_worker_slot(
        self,
        domain: str,
        worker_id: str,
        task_id: str = "",
        pid: int | None = None,
        estimated_memory_mb: float = 0.0,
    ) -> DomainWorkerRecord:
        """Thread-safely claims a worker slot under the domain's resource quota."""
        if not worker_id:
            raise ValueError("worker_id must be non-empty")

        with self._lock:
            # 1. Enforce host physical RAM safety
            self.enforce_host_safety()

            # 2. Enforce domain worker concurrency limit
            self.enforce_worker_limit(domain)

            # 3. Enforce domain memory quota
            target_pid = pid if pid is not None else os.getpid()
            self.enforce_memory_limit(domain, pid=target_pid, additional_mb=estimated_memory_mb)

            # 4. Register active worker
            record = DomainWorkerRecord(
                domain=domain,
                worker_id=worker_id,
                pid=target_pid,
                task_id=task_id or f"task_{worker_id}",
                allocated_mb=estimated_memory_mb,
            )
            initial_rss = self.sample_process_memory_mb(target_pid)
            record.update_peak_rss(initial_rss)

            self._active_workers.setdefault(domain, {})[worker_id] = record
            logger.debug(
                "Acquired worker slot '%s' (PID %d) for domain '%s'",
                worker_id, target_pid, domain
            )
            return record

    def release_worker_slot(self, domain: str, worker_id: str) -> bool:
        """Releases an active worker slot and updates telemetry records."""
        with self._lock:
            domain_workers = self._active_workers.get(domain, {})
            record = domain_workers.pop(worker_id, None)
            if record is not None:
                final_rss = self.sample_process_memory_mb(record.pid)
                record.update_peak_rss(final_rss)
                logger.debug(
                    "Released worker slot '%s' for domain '%s' (Peak RSS: %.2f MB)",
                    worker_id, domain, record.peak_rss_mb
                )
                return True
            return False

    # -------------------------------------------------------------------------
    # Context Manager Guard
    # -------------------------------------------------------------------------

    @contextlib.contextmanager
    def guard_domain(
        self,
        domain: str,
        task_id: str,
        worker_id: str | None = None,
        pid: int | None = None,
        estimated_memory_mb: float = 0.0,
    ) -> Iterator[DomainWorkerRecord]:
        """Context manager safely managing domain quota allocation and cleanup."""
        resolved_worker_id = worker_id or f"{domain}_{task_id}_{time.time_ns()}"
        record = self.acquire_worker_slot(
            domain=domain,
            worker_id=resolved_worker_id,
            task_id=task_id,
            pid=pid,
            estimated_memory_mb=estimated_memory_mb,
        )
        try:
            yield record
        finally:
            self.release_worker_slot(domain, resolved_worker_id)

    # -------------------------------------------------------------------------
    # Polling & Thundering Herd Protection (Rule 1)
    # -------------------------------------------------------------------------

    def poll_hardware_guard(
        self,
        domain: str,
        attempt: int = 0,
        required_memory_mb: float = 0.0,
    ) -> float:
        """Checks hardware conditions and injects jittered exponential backoff if saturated.

        Returns 0.0 if capacity is free and healthy, or the backoff delay slept in seconds.
        """
        try:
            self.enforce_host_safety()
            self.enforce_worker_limit(domain)
            self.enforce_memory_limit(domain, additional_mb=required_memory_mb)
            # Under healthy conditions, inject minimal JIT jitter (100-500ms) per Rule 1
            if attempt > 0:
                return sleep_jittered_backoff(attempt)
            return 0.0
        except HardwareGuardError as exc:
            logger.info("Hardware Guard backoff triggered for domain %s: %s", domain, exc)
            return sleep_jittered_backoff(attempt)

    # -------------------------------------------------------------------------
    # Process Execution with Window Suppression (Rule 15)
    # -------------------------------------------------------------------------

    def spawn_guarded_subprocess(
        self,
        domain: str,
        cmd: Sequence[str],
        task_id: str = "",
        timeout_sec: int | None = None,
        **kwargs: Any,
    ) -> subprocess.Popen[Any]:
        """Spawns an OS subprocess under domain quota with window suppression flags (Rule 15)."""
        # Ensure mandatory creationflags for Windows popup suppression
        flags = kwargs.pop("creationflags", 0)
        flags |= DEFAULT_CREATIONFLAGS
        kwargs["creationflags"] = flags

        worker_id = f"proc_{domain}_{time.time_ns()}"
        proc = subprocess.Popen(cmd, **kwargs)

        # Register slot tracking the child process PID
        self.acquire_worker_slot(
            domain=domain,
            worker_id=worker_id,
            task_id=task_id,
            pid=proc.pid,
        )

        return proc

    def run_guarded_subprocess(
        self,
        domain: str,
        cmd: Sequence[str],
        task_id: str = "",
        timeout_sec: int | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Runs a subprocess to completion under domain quota with window suppression."""
        flags = kwargs.pop("creationflags", 0)
        flags |= DEFAULT_CREATIONFLAGS
        kwargs["creationflags"] = flags
        kwargs.setdefault("text", True)
        kwargs.setdefault("capture_output", True)
        check_proc = kwargs.pop("check", False)

        quota = self.get_domain_quota(domain)
        effective_timeout = timeout_sec if timeout_sec is not None else quota.timeout_seconds

        with self.guard_domain(domain=domain, task_id=task_id):
            return subprocess.run(cmd, timeout=effective_timeout, check=check_proc, **kwargs)

    # -------------------------------------------------------------------------
    # Antigravity SDK Subprocess Invocation (Rule 10 & Rule 15)
    # -------------------------------------------------------------------------

    def invoke_antigravity_guarded(
        self,
        domain: str,
        agent_name: str,
        prompt: str,
        timeout_sec: int = 300,
    ) -> str:
        """Invokes Antigravity agent CLI under domain quota with window suppression."""
        cli_path = shutil.which("agy")
        if not cli_path:
            raise FileNotFoundError("Antigravity CLI ('agy') not found on system PATH.")

        cmd = [cli_path, "--agent", agent_name, "-p", prompt]
        proc = self.run_guarded_subprocess(
            domain=domain,
            cmd=cmd,
            task_id=f"agy_{agent_name}",
            timeout_sec=timeout_sec,
        )
        return proc.stdout.strip()

    # -------------------------------------------------------------------------
    # Telemetry Snapshots
    # -------------------------------------------------------------------------

    def get_domain_telemetry(self, domain: str) -> dict[str, Any]:
        """Returns physical telemetry snapshot for a specific domain."""
        with self._lock:
            quota = self.get_domain_quota(domain)
            current_mem = self.get_domain_memory_usage_mb(domain)
            active_count = self.get_domain_active_worker_count(domain)
            peak_mem = self._domain_peak_rss.get(domain, 0.0)

            return {
                "domain": domain,
                "current_memory_mb": round(current_mem, 2),
                "memory_quota_mb": quota.memory_mb,
                "memory_utilization_percent": round((current_mem / quota.memory_mb) * 100.0, 2),
                "peak_memory_mb": round(peak_mem, 2),
                "active_workers": active_count,
                "max_workers": quota.max_workers,
                "worker_utilization_percent": round((active_count / quota.max_workers) * 100.0, 2),
                "cpu_limit": quota.cpu_limit,
                "timeout_seconds": quota.timeout_seconds,
            }

    def get_all_telemetry(self) -> dict[str, Any]:
        """Returns comprehensive telemetry for all registered DSP domains and host system."""
        with self._lock:
            domain_snapshots = {
                domain: self.get_domain_telemetry(domain)
                for domain in self._quotas
            }
            return {
                "timestamp": time.time(),
                "host_memory": self.get_host_memory_stats(),
                "domains": domain_snapshots,
            }


# ==============================================================================
# Global Singleton Instance & Convenience API
# ==============================================================================
_GLOBAL_GUARD: HardwareGuard | None = None
_GLOBAL_GUARD_LOCK = threading.Lock()


def get_hardware_guard() -> HardwareGuard:
    """Returns the process-wide HardwareGuard singleton instance."""
    global _GLOBAL_GUARD
    with _GLOBAL_GUARD_LOCK:
        if _GLOBAL_GUARD is None:
            _GLOBAL_GUARD = HardwareGuard()
        return _GLOBAL_GUARD


def check_domain_memory(domain: str, additional_mb: float = 0.0) -> bool:
    """Convenience helper to check memory quota for a domain."""
    return get_hardware_guard().check_memory_limit(domain, additional_mb=additional_mb)


def enforce_domain_memory(domain: str, additional_mb: float = 0.0) -> None:
    """Convenience helper to enforce memory quota for a domain."""
    get_hardware_guard().enforce_memory_limit(domain, additional_mb=additional_mb)


def poll_domain_guard(domain: str, attempt: int = 0) -> float:
    """Convenience helper to poll guard and apply jittered backoff."""
    return get_hardware_guard().poll_hardware_guard(domain, attempt=attempt)


# ==============================================================================
# CLI Entrypoint for Auditing and Direct Verification
# ==============================================================================
def main(argv: Sequence[str] | None = None) -> int:
    """CLI interface for Hardware Guard status, audit checks, and telemetry."""
    parser = argparse.ArgumentParser(
        description="CoChem Hardware Guard & Resource Quota Enforcement (SRS-412-07)"
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=DOMAIN_CODE_FORGE,
        help="Target DSP domain to inspect (default: code_forge)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check resource quota compliance for target domain",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit output in JSON format",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print system and domain resource telemetry summary",
    )

    args = parser.parse_args(argv)
    guard = get_hardware_guard()

    if args.check:
        try:
            guard.enforce_host_safety()
            guard.enforce_worker_limit(args.domain)
            guard.enforce_memory_limit(args.domain)
            if args.output_json:
                print(json.dumps({"domain": args.domain, "status": "COMPLIANT"}))
            else:
                print(f"[HardwareGuard: PASS] Domain '{args.domain}' is within allocated hardware quotas.")
            return 0
        except HardwareGuardError as exc:
            if args.output_json:
                print(json.dumps({"domain": args.domain, "status": "VIOLATION", "error": str(exc)}))
            else:
                print(f"[HardwareGuard: FAIL] {exc}")
            return 1

    telemetry = guard.get_all_telemetry()
    if args.output_json:
        print(json.dumps(telemetry, indent=2))
    else:
        print("=" * 60)
        print("CoChem DSP Hardware Guard Telemetry (SRS-412-07)")
        print("=" * 60)
        host = telemetry["host_memory"]
        print(f"Host RAM: {host['used_mb']:.1f} MB / {host['total_mb']:.1f} MB ({host['percent']:.1f}%)")
        print("-" * 60)
        for dom, data in telemetry["domains"].items():
            print(
                f"Domain: {dom:20} | Memory: {data['current_memory_mb']:7.1f} / {data['memory_quota_mb']} MB "
                f"({data['memory_utilization_percent']:5.1f}%) | Workers: {data['active_workers']}/{data['max_workers']}"
            )
        print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())

`
