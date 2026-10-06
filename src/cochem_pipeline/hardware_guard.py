"""Measured host admission and emergency policy for the four-worker pipeline.

Telemetry is collected from the host; ``assess_resources`` is a pure decision
function so boundary cases can be tested without claiming invented readings
are live measurements. A guard controls admission, while the runtime executes
its emergency action and confirms native process-tree cleanup in the job board.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import math
from pathlib import Path
import threading
import time
from typing import Any, Mapping, Sequence

from .resource_telemetry import collect_resources

_HARD_MAX_AGENTS = 4


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class HardwarePolicy:
    """Explicit protective thresholds; no unavailable sensor is reported as zero.

    CPU/RAM scaling and 80% VRAM throttling come from the historical daemon.
    The 90% VRAM alert comes from the older host SRS. Celsius and critical
    reserve limits below are new configurable protective policy, not claimed
    historical SRS temperature specifications.
    """
    gpu_required: bool = True
    windows_commit_required: bool = True
    cpu_temperature_required: bool = True
    disk_io_required: bool = True
    sample_seconds: float = .05
    sample_interval_seconds: float = 1.
    recovery_seconds: float = 10.
    recovery_samples: int = 3
    ramp_up_step: int = 1
    critical_pressure_seconds: float = 5.
    max_cpu_percent: float = 95.
    critical_cpu_percent: float = 99.
    cpu_temperature_pause_c: float = 85.
    cpu_temperature_critical_c: float = 95.
    gpu_temperature_pause_c: float = 85.
    gpu_temperature_critical_c: float = 92.
    gpu_vram_throttle_percent: float = 80.
    gpu_vram_pause_percent: float = 90.
    gpu_vram_critical_percent: float = 98.
    min_free_memory_mb: float = 1024.
    critical_free_memory_mb: float = 256.
    per_agent_memory_mb: float = 256.
    min_free_commit_mb: float = 6144.
    critical_free_commit_mb: float = 512.
    min_free_disk_mb: float = 512.
    critical_free_disk_mb: float = 128.
    max_disk_mb_s: float = 120.
    max_disk_iops: float = 4000.

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name.endswith('_required'):
                if type(value) is not bool:
                    raise ValueError(f'{field.name} must be a boolean')
            elif field.name in {'recovery_samples', 'ramp_up_step'}:
                if type(value) is not int or not 1 <= value <= (1000 if field.name == 'recovery_samples' else 4):
                    raise ValueError(f'{field.name} has an invalid integer bound')
            elif not _number(value) or value < 0 or value > 1e9:
                raise ValueError(f'{field.name} must be a bounded finite nonnegative number')
        if not .01 <= self.sample_seconds <= 5 or not self.sample_seconds <= self.sample_interval_seconds <= 60:
            raise ValueError('Sampling needs .01..5 seconds and an interval from sample_seconds to 60 seconds')
        if self.recovery_seconds > 600 or self.critical_pressure_seconds > 600:
            raise ValueError('Recovery and critical-pressure windows cannot exceed 600 seconds')
        if not 0 < self.max_cpu_percent <= self.critical_cpu_percent <= 100:
            raise ValueError('CPU thresholds must satisfy 0 < pause <= critical <= 100')
        for prefix in ('cpu', 'gpu'):
            pause = getattr(self, prefix+'_temperature_pause_c')
            critical = getattr(self, prefix+'_temperature_critical_c')
            if not 0 < pause < critical <= 150:
                raise ValueError('Temperature thresholds must satisfy 0 < pause < critical <= 150 C')
        if not 0 < self.gpu_vram_throttle_percent < self.gpu_vram_pause_percent < self.gpu_vram_critical_percent <= 100:
            raise ValueError('VRAM thresholds must increase from throttle through pause to critical')
        for resource in ('memory', 'commit', 'disk'):
            if getattr(self, 'critical_free_'+resource+'_mb') > getattr(self, 'min_free_'+resource+'_mb'):
                raise ValueError('Critical free-resource threshold cannot exceed its admission reserve')
        if min(self.per_agent_memory_mb, self.max_disk_mb_s, self.max_disk_iops) <= 0:
            raise ValueError('Per-worker memory and disk-rate limits must be positive')

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None = None) -> HardwarePolicy:
        if value is None:
            return cls()
        if not isinstance(value, Mapping) or set(value)-{field.name for field in fields(cls)}:
            raise ValueError('hardware must contain only documented governor policy fields')
        return cls(**dict(value))

    def as_dict(self) -> dict:
        return asdict(self)


def effective_execution_policy(policy: HardwarePolicy | Mapping | None,
                               native_memory_mb: float,
                               docker_memory_mb: float = 0) -> HardwarePolicy:
    """Reserve the largest configured execution-tree memory cap per seat.

    A seat can run native work or a prepared Docker sandbox. Reserving only a
    small expected working set would admit containers whose permitted memory
    growth exceeds measured host headroom. The same conservative policy is
    used for deployment readiness and runtime admission. Actual RAM-disk
    allocation is already reflected in the free-memory measurement and is
    deliberately not subtracted a second time here.
    """
    configured = policy if isinstance(policy, HardwarePolicy) else HardwarePolicy.from_dict(policy)
    if not _number(native_memory_mb) or not 0 < native_memory_mb <= 1e9:
        raise ValueError('Native execution memory cap must be a bounded positive number')
    if not _number(docker_memory_mb) or not 0 <= docker_memory_mb <= 1e9:
        raise ValueError('Docker execution memory cap must be a bounded nonnegative number')
    return replace(configured, per_agent_memory_mb=max(configured.per_agent_memory_mb,
                                                       native_memory_mb, docker_memory_mb))


def assess_resources(measurements: dict, policy: HardwarePolicy, max_agents: int = 4) -> dict:
    """Pure admission decision over an explicitly supplied measurement record.

    This function does not collect or attest telemetry. Sustained pressure is
    confirmed by ``GovernorState``; thermal and critical reserve trips are
    immediate. Every workspace volume must have a successful free-space probe.
    """
    if not isinstance(policy, HardwarePolicy) or not isinstance(measurements, dict):
        raise ValueError('Resource assessment requires HardwarePolicy and measurement objects')
    if type(max_agents) is not int or max_agents < 0:
        raise ValueError('max_agents must be a nonnegative integer')
    ceiling = min(_HARD_MAX_AGENTS, max_agents)
    capacity = ceiling
    reasons, alerts, emergencies, sustained = [], [], [], []

    def pause(message: str):
        nonlocal capacity
        capacity = 0
        reasons.append(message)

    def critical(message: str):
        pause(message)
        emergencies.append(message)

    cpu = measurements.get('cpu', {})
    memory = measurements.get('memory', {})
    commit = measurements.get('commit', {})
    gpu = measurements.get('gpus', {})
    disks = measurements.get('disks', {})
    cpu_ok = (isinstance(cpu, dict) and cpu.get('available') is True
              and type(cpu.get('count')) is int and cpu['count'] > 0
              and _number(cpu.get('percent')) and 0 <= cpu['percent'] <= 100)
    memory_ok = (isinstance(memory, dict) and memory.get('available') is True
                 and _number(memory.get('total_mb')) and memory['total_mb'] > 0
                 and _number(memory.get('available_mb')) and 0 <= memory['available_mb'] <= memory['total_mb'])
    if not cpu_ok:
        pause('CPU count/utilization telemetry is unavailable or invalid')
    if not memory_ok:
        pause('Physical memory telemetry is unavailable or invalid')
    if cpu_ok and memory_ok:
        percent, available = cpu['percent'], memory['available_mb']
        if percent < 30 and available > 16384:
            band = ceiling
        elif percent < 50 and available > 8192:
            band = max(1, ceiling*3//4) if ceiling else 0
        elif percent < 70 and available > 4096:
            band = max(1, ceiling//2) if ceiling else 0
        elif percent < 85 and available > 2048:
            band = max(1, ceiling//4) if ceiling else 0
        else:
            band = 0
            pause('CPU/RAM pressure is outside the safe historical admission bands')
        memory_slots = max(0, math.floor((available-policy.min_free_memory_mb)/policy.per_agent_memory_mb))
        capacity = min(capacity, cpu['count'], band, memory_slots)
        if memory_slots == 0:
            pause('Available memory cannot accommodate a worker and the configured reserve')
    # Missing unrelated telemetry must not mask an independently measured
    # emergency. For example, a failed CPU probe cannot hide exhausted RAM.
    if memory_ok and memory['available_mb'] < policy.critical_free_memory_mb:
        critical('Physical memory is below the critical free-memory threshold')
    if cpu_ok:
        if cpu['percent'] >= policy.max_cpu_percent:
            pause('CPU utilization exceeds the configured admission threshold')
        if cpu['percent'] >= policy.critical_cpu_percent:
            sustained.append('Sustained critical CPU utilization')
    temperature = cpu.get('temperature_celsius') if isinstance(cpu, dict) else None
    temperature_ok = (isinstance(cpu, dict) and cpu.get('temperature_available') is True
                      and _number(temperature) and -20 <= temperature <= 150)
    if not temperature_ok and policy.cpu_temperature_required:
        pause('Required CPU temperature telemetry is unavailable; install/expose supported CPU sensors (LibreHardwareMonitor on Windows)')
    elif temperature_ok:
        if temperature >= policy.cpu_temperature_critical_c:
            critical('CPU temperature exceeds the critical threshold')
        elif temperature >= policy.cpu_temperature_pause_c:
            pause('CPU temperature exceeds the admission threshold')

    commit_ok = (isinstance(commit, dict) and commit.get('available') is True
                 and _number(commit.get('free_mb')) and _number(commit.get('limit_mb')) and commit['limit_mb'] > 0
                 and 0 <= commit['free_mb'] <= commit['limit_mb'])
    if not commit_ok and policy.windows_commit_required:
        pause('Required Windows commit-charge telemetry is unavailable')
    elif commit_ok:
        if commit['free_mb'] < policy.critical_free_commit_mb:
            critical('Windows commit headroom is below its critical reserve')
        elif commit['free_mb'] < policy.min_free_commit_mb:
            pause('Windows commit headroom is below its admission reserve')
        else:
            capacity = min(capacity, max(0, math.floor((commit['free_mb']-policy.min_free_commit_mb)/policy.per_agent_memory_mb)))

    devices = gpu.get('devices') if isinstance(gpu, dict) else None
    gpu_ok = isinstance(gpu, dict) and gpu.get('available') is True and isinstance(devices, list) and bool(devices)
    if not gpu_ok and policy.gpu_required:
        pause('Required GPU/VRAM telemetry is unavailable; a CPU-only host requires explicit gpu_required=false')
    elif gpu_ok:
        for device in devices:
            if (not isinstance(device, dict) or not _number(device.get('total_mb')) or device['total_mb'] <= 0
                    or not _number(device.get('used_mb')) or not 0 <= device['used_mb'] <= device['total_mb']
                    or not _number(device.get('temperature_celsius')) or not -20 <= device['temperature_celsius'] <= 150
                    or not _number(device.get('utilization_percent')) or not 0 <= device['utilization_percent'] <= 100):
                pause('A detected GPU returned incomplete or invalid VRAM/temperature telemetry')
                continue
            usage = 100*device['used_mb']/device['total_mb']
            if usage >= policy.gpu_vram_pause_percent:
                alerts.append('GPU VRAM pressure reached the configured alert threshold')
                pause('GPU VRAM pressure pauses new admission')
            elif usage > policy.gpu_vram_throttle_percent:
                capacity = min(capacity, max(1, ceiling//4) if ceiling else 0)
                alerts.append('GPU VRAM pressure reduced the worker ceiling to one quarter')
            if usage >= policy.gpu_vram_critical_percent:
                sustained.append('Sustained critical GPU VRAM pressure')
            if device['temperature_celsius'] >= policy.gpu_temperature_critical_c:
                critical('GPU temperature exceeds the critical threshold')
            elif device['temperature_celsius'] >= policy.gpu_temperature_pause_c:
                pause('GPU temperature exceeds the admission threshold')

    volumes = disks.get('volumes') if isinstance(disks, dict) else None
    if not isinstance(disks, dict) or disks.get('available') is not True or not isinstance(volumes, list) or not volumes:
        pause('Free-space telemetry is unavailable for one or more required workspace volumes')
    else:
        for volume in volumes:
            if (not isinstance(volume, dict) or not _number(volume.get('total_mb')) or volume['total_mb'] <= 0
                    or not _number(volume.get('free_mb')) or not 0 <= volume['free_mb'] <= volume['total_mb']):
                pause('A workspace volume returned invalid free-space telemetry')
                continue
            if volume['free_mb'] < policy.critical_free_disk_mb:
                critical('A workspace volume has critically low free disk space')
            elif volume['free_mb'] < policy.min_free_disk_mb:
                pause('A workspace volume is below its free disk reserve')
    io = disks.get('io', {}) if isinstance(disks, dict) else {}
    io_ok = (isinstance(io, dict) and io.get('available') is True
             and all(_number(io.get(key)) and io[key] >= 0 for key in ('total_mb_s', 'total_iops')))
    if not io_ok and policy.disk_io_required:
        pause('Required disk throughput/IOPS telemetry is unavailable')
    elif io_ok and (io['total_mb_s'] > policy.max_disk_mb_s or io['total_iops'] > policy.max_disk_iops):
        pause('Disk throughput or IOPS exceeds the admission pressure threshold')
    if ceiling == 0:
        pause('Worker admission is explicitly disabled')
    state = 'critical' if emergencies else 'paused' if capacity == 0 else 'throttled' if capacity < ceiling else 'normal'
    return {'capacity':capacity, 'state':state,
            'action':'terminate_active' if emergencies else 'pause_admission' if capacity == 0 else 'none',
            'reasons':list(dict.fromkeys(reasons)), 'alerts':list(dict.fromkeys(alerts)),
            'critical_reasons':list(dict.fromkeys(emergencies)), 'sustained_critical':list(dict.fromkeys(sustained))}


class GovernorState:
    """Pure state transition: drop immediately, recover slowly without gate swaps."""
    def __init__(self):
        self.capacity = 0
        self.healthy_since = None
        self.healthy_samples = 0
        self.critical_since: dict[str, float] = {}
        self.last_sample = None

    def apply(self, decision: dict, policy: HardwarePolicy, now: float) -> dict:
        if not _number(now) or (self.last_sample is not None and now < self.last_sample):
            raise ValueError('Governor sample clock must be finite and monotonic')
        self.last_sample = now
        result = {**decision, 'reasons':list(decision['reasons']), 'critical_reasons':list(decision['critical_reasons'])}
        ongoing = set(decision['sustained_critical'])
        self.critical_since = {reason:self.critical_since.get(reason, now) for reason in ongoing}
        for reason, started in self.critical_since.items():
            if now-started >= policy.critical_pressure_seconds:
                result['critical_reasons'].append(reason)
        if result['critical_reasons']:
            result.update(capacity=0, state='critical', action='terminate_active')
        desired = result['capacity']
        if desired <= self.capacity:
            if desired < self.capacity or desired == 0:
                self.healthy_since = None
                self.healthy_samples = 0
            self.capacity = desired
        else:
            if self.healthy_since is None:
                self.healthy_since = now
                self.healthy_samples = 0
            self.healthy_samples += 1
            if now-self.healthy_since >= policy.recovery_seconds and self.healthy_samples >= policy.recovery_samples:
                self.capacity = min(desired, self.capacity+policy.ramp_up_step)
                self.healthy_since, self.healthy_samples = now, 0
            if self.capacity < desired:
                result['reasons'].append('Recovery hysteresis is holding or gradually raising admission capacity')
                if result['state'] != 'critical':
                    result['state'] = 'paused' if self.capacity == 0 else 'throttled'
                    result['action'] = 'pause_admission' if self.capacity == 0 else 'none'
        result.update(capacity=self.capacity, raw_capacity=desired,
                      healthy_samples=self.healthy_samples, recovery_started_at=self.healthy_since)
        return result


class HardwareGuard:
    """Stateful sampled governor; the job board remains the atomic lease authority."""
    def __init__(self, max_agents: int = 4, min_free_memory_mb: float | None = None,
                 min_free_disk_mb: float | None = None, workspace: Path | None = None,
                 per_agent_memory_mb: float | None = None, max_cpu_percent: float | None = None,
                 *, workspaces: Sequence[Path] | None = None, policy: HardwarePolicy | Mapping | None = None):
        if type(max_agents) is not int or max_agents < 0:
            raise ValueError('max_agents must be a nonnegative integer')
        configured = policy if isinstance(policy, HardwarePolicy) else HardwarePolicy.from_dict(policy)
        overrides = {key:value for key,value in (('min_free_memory_mb',min_free_memory_mb),
            ('min_free_disk_mb',min_free_disk_mb),('per_agent_memory_mb',per_agent_memory_mb),
            ('max_cpu_percent',max_cpu_percent)) if value is not None}
        # Backwards-compatible callers can lower reserves without needing to
        # know the new critical thresholds; never leave an inverted threshold.
        for resource in ('memory','disk'):
            key = 'min_free_'+resource+'_mb'
            if key in overrides and _number(overrides[key]):
                overrides['critical_free_'+resource+'_mb'] = min(getattr(configured,'critical_free_'+resource+'_mb'),overrides[key])
        if 'max_cpu_percent' in overrides and _number(overrides['max_cpu_percent']):
            overrides['critical_cpu_percent'] = max(configured.critical_cpu_percent,overrides['max_cpu_percent'])
        self.policy = replace(configured,**overrides)
        if isinstance(workspaces,(str,bytes,Path)):
            raise ValueError('workspaces must be a sequence of volume paths')
        supplied = tuple(workspaces) if workspaces is not None else (workspace or Path.cwd(),)
        if not supplied:
            raise ValueError('At least one workspace volume must be monitored')
        self.workspaces = tuple(dict.fromkeys(Path(path).resolve() for path in supplied))
        self.workspace = Path(workspace).resolve() if workspace is not None else self.workspaces[0]
        self.max_agents = max_agents
        self._state = GovernorState()
        self._lock = threading.Lock()
        self._sampled_at = None
        self._latest = None

    def evaluate(self, *, force: bool = False) -> dict:
        with self._lock:
            now = time.monotonic()
            if not force and self._latest is not None and now-self._sampled_at < self.policy.sample_interval_seconds:
                from copy import deepcopy
                return deepcopy(self._latest)
            measured = collect_resources(self.workspaces, sample_seconds=self.policy.sample_seconds)
            decision = self._state.apply(assess_resources(measured,self.policy,self.max_agents),self.policy,time.monotonic())
            cpu, memory, disks = measured.get('cpu',{}), measured.get('memory',{}), measured.get('disks',{})
            volumes = disks.get('volumes',[])
            result = {**decision, 'measured_at':time.time(), 'workspace':str(self.workspace),
                'workspaces':[str(path) for path in self.workspaces], 'max_agents':self.max_agents, 'hard_max_agents':4,
                'policy':self.policy.as_dict(), 'measurements':measured,
                # Stable API aliases remain genuine readings, never fabricated defaults.
                'cpu_count':cpu.get('count'), 'cpu_percent':cpu.get('percent'),
                'memory_total_mb':memory.get('total_mb'), 'memory_available_mb':memory.get('available_mb'),
                'disk_total_mb':volumes[0].get('total_mb') if volumes else None,
                'disk_free_mb':volumes[0].get('free_mb') if volumes else None,
                'min_free_memory_mb':self.policy.min_free_memory_mb, 'min_free_disk_mb':self.policy.min_free_disk_mb,
                'per_agent_memory_mb':self.policy.per_agent_memory_mb, 'max_cpu_percent':self.policy.max_cpu_percent}
            self._sampled_at, self._latest = time.monotonic(), result
            from copy import deepcopy
            return deepcopy(result)

    def snapshot(self) -> dict:
        return self.evaluate()

    def latest_snapshot(self) -> dict:
        """Read the last completed sample without waiting on slow native probes.

        ``evaluate`` publishes a new complete dictionary atomically and never
        mutates a published dictionary. Readers therefore do not acquire the
        sampling lock; the HTTP health endpoint remains responsive during WMI
        or driver probe timeouts. A cold guard reports unknown, never healthy.
        """
        from copy import deepcopy
        latest = self._latest
        if latest is not None:
            return deepcopy(latest)
        return {'capacity':0, 'raw_capacity':0, 'state':'paused',
                'action':'pause_admission','measured_at':None,'measurements':{},
                'reasons':['Required hardware telemetry has not completed its first sample'],
                'alerts':[],'critical_reasons':[],'sustained_critical':[],
                'max_agents':self.max_agents,'hard_max_agents':4,'policy':self.policy.as_dict()}

    def capacity(self) -> int:
        return self.evaluate()['capacity']
