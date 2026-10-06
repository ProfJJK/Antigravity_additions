"""Native Windows Job Object limits applied before any worker is resumed.

Job memory is an aggregate committed-memory ceiling for the complete process
tree, CPU rate is a hard aggregate scheduler cap, and active-process count limits
further child creation. No GPU/VRAM containment is claimed by these Win32 limits.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass, fields
import os
import secrets
import struct
from typing import Mapping


class ResourcePolicyError(RuntimeError):
    """Native containment/topology is unavailable; hold rather than infer."""


@dataclass(frozen=True)
class ResourceLimits:
    memory_limit_mb: int = 2048
    cpu_rate_percent: int = 20
    max_processes: int = 16
    e_core_policy: str = 'required'
    affinity_mask: int | None = None
    node_heap_mb: int = 512
    launch_jitter_min_ms: int = 100
    launch_jitter_max_ms: int = 500
    poll_jitter_min_ms: int = 100
    poll_jitter_max_ms: int = 500

    def __post_init__(self):
        for key, low, high in (('memory_limit_mb',64,1048576),('cpu_rate_percent',1,100),('max_processes',1,1024)):
            value = getattr(self,key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{key} must be an integer in {low}..{high}')
        if self.memory_limit_mb*1024*1024 > ctypes.c_size_t(-1).value:
            raise ValueError('Job memory limit exceeds this native pointer width')
        if self.e_core_policy not in ('required','off'):
            raise ValueError('e_core_policy must be required, or explicitly off for a non-SRS host profile')
        if self.affinity_mask is not None and (type(self.affinity_mask) is not int
                or not 0 < self.affinity_mask <= ctypes.c_size_t(-1).value or self.e_core_policy != 'required'):
            raise ValueError('affinity_mask must be a positive native-width integer verified against actual E cores')
        if type(self.node_heap_mb) is not int or not 64 <= self.node_heap_mb <= 512:
            raise ValueError('Node worker heap must be bounded to 64..512 MiB')
        for kind in ('launch','poll'):
            low,high = getattr(self,kind+'_jitter_min_ms'),getattr(self,kind+'_jitter_max_ms')
            if type(low) is not int or type(high) is not int or not 100 <= low <= high <= 500:
                raise ValueError(f'{kind} jitter must remain inside the prescribed 100..500 ms range')

    @classmethod
    def from_dict(cls, value: Mapping | None = None) -> ResourceLimits:
        if value is None:
            return cls()
        if not isinstance(value,Mapping) or set(value)-{field.name for field in fields(cls)}:
            raise ValueError('execution_limits must contain only documented Job Object limits')
        return cls(**dict(value))

    def as_dict(self) -> dict:
        return asdict(self)


_DWORD = ctypes.c_uint32
_SIZE_T = ctypes.c_size_t
_HANDLE = ctypes.c_void_p
_JOB_MEMORY = 0x200
_ACTIVE_PROCESS = 0x8
_KILL_ON_JOB_CLOSE = 0x2000
_CPU_ENABLE = 0x1
_CPU_HARD_CAP = 0x4
_PRIORITY_CLASS = 0x20
_AFFINITY = 0x10
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def parse_cpu_sets(data: bytes) -> list[dict]:
    """Decode the documented SYSTEM_CPU_SET_INFORMATION ABI, including size.

    Unknown future record types are skipped using their declared size. A
    truncated or ambiguous topology cannot authorize an affinity constraint.
    """
    offset, result, seen_ids, seen_locations = 0, [], set(), set()
    while offset < len(data):
        if len(data)-offset < 8:
            raise ValueError('Truncated CPU-set information header')
        size,kind = struct.unpack_from('<II',data,offset)
        if size < 8 or offset+size > len(data):
            raise ValueError('Invalid CPU-set information record size')
        if kind == 0:
            if size < 32:
                raise ValueError('Truncated SYSTEM_CPU_SET_INFORMATION record')
            identifier,group,index,core,cache,node,efficiency,flags = struct.unpack_from('<IHBBBBBB',data,offset+8)
            if index >= 64 or identifier in seen_ids or (group,index) in seen_locations:
                raise ValueError('Ambiguous CPU-set identifier or processor index')
            seen_ids.add(identifier);seen_locations.add((group,index))
            result.append({'id':identifier,'group':group,'logical_index':index,'core_index':core,
                           'efficiency_class':efficiency,'parked':bool(flags&1),
                           'allocated_to_other_process':bool(flags&2 and not flags&4)})
        offset += size
    if not result:
        raise ValueError('Windows returned no usable CPU-set topology')
    return result


def select_e_core_affinity(cpu_sets: list[dict], requested_mask: int | None = None) -> dict:
    classes = sorted({entry['efficiency_class'] for entry in cpu_sets})
    if len(classes) < 2:
        raise ResourcePolicyError('The required E-core policy needs real heterogeneous EfficiencyClass topology')
    # The Windows API defines larger EfficiencyClass values as more performant
    # and less efficient. Never assume bits 16..23 identify E cores on a host.
    efficiency = classes[0]
    candidates = [entry for entry in cpu_sets if entry['efficiency_class']==efficiency
                  and entry['group']==0 and not entry['allocated_to_other_process']]
    if not candidates:
        raise ResourcePolicyError('No available efficiency cores exist in processor group 0; this host needs an explicitly reviewed topology policy')
    available = sum(1<<entry['logical_index'] for entry in candidates)
    mask = available if requested_mask is None else requested_mask
    if type(mask) is not int or mask <= 0 or mask & ~available:
        raise ValueError('Configured affinity mask includes nonexistent, unavailable or performance cores')
    selected = [entry for entry in candidates if mask & (1<<entry['logical_index'])]
    return {'affinity_group':0,'affinity_mask':mask,'affinity_mask_hex':hex(mask),
            'efficiency_class':efficiency,'cpu_set_ids':[entry['id'] for entry in selected],
            'logical_processors':[entry['logical_index'] for entry in selected],
            'topology_source':'GetSystemCpuSetInformation','topology_verified':True}


def native_cpu_sets() -> list[dict]:
    if os.name != 'nt':
        raise ResourcePolicyError('Native CPU-set topology requires Windows')
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    try:
        query = kernel.GetSystemCpuSetInformation
    except AttributeError as exc:
        raise ResourcePolicyError('Windows does not expose the required CPU-set EfficiencyClass API') from exc
    query.restype = ctypes.c_int
    query.argtypes = [ctypes.c_void_p,_DWORD,ctypes.POINTER(_DWORD),_HANDLE,_DWORD]
    size = _DWORD()
    query(None,0,ctypes.byref(size),None,0)
    if not 32 <= size.value <= 1048576:
        raise ResourcePolicyError('Windows returned an invalid CPU topology size')
    buffer = ctypes.create_string_buffer(size.value)
    if not query(buffer,len(buffer),ctypes.byref(size),None,0):
        raise ResourcePolicyError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return parse_cpu_sets(buffer.raw[:size.value])
    except ValueError as exc:
        raise ResourcePolicyError('Native CPU topology could not be verified: '+str(exc)) from exc


def validate_host_limits(limits: ResourceLimits) -> dict:
    if not isinstance(limits,ResourceLimits):
        raise TypeError('Host limits require validated ResourceLimits')
    if limits.e_core_policy == 'off':
        return {'e_core_policy':'off','affinity_mask':0,'topology_verified':False,
                'below_normal_priority':True,'node_heap_mb':limits.node_heap_mb}
    try:
        topology = select_e_core_affinity(native_cpu_sets(),limits.affinity_mask)
    except ValueError as exc:
        raise ResourcePolicyError('Configured native affinity cannot be applied: '+str(exc)) from exc
    return {**topology,
            'e_core_policy':limits.e_core_policy,'below_normal_priority':True,
            'node_heap_mb':limits.node_heap_mb}


def controller_limits(limits: ResourceLimits, *, apply=False) -> dict:
    """Apply/inspect the Warden's own scheduling policy, separately from children."""
    if os.name != 'nt':
        raise ResourcePolicyError('Native Warden scheduling requires Windows')
    import psutil
    host = validate_host_limits(limits)
    process = psutil.Process()
    try:
        if apply:
            process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            if host['affinity_mask']:
                process.cpu_affinity(host['logical_processors'])
        priority = process.nice()
        affinity = process.cpu_affinity()
    except (psutil.Error, OSError) as exc:
        raise ResourcePolicyError('Warden scheduling policy could not be applied or inspected') from exc
    if priority != psutil.BELOW_NORMAL_PRIORITY_CLASS:
        raise ResourcePolicyError('The Warden itself is not running at BelowNormal priority')
    if host['affinity_mask'] and set(affinity) != set(host['logical_processors']):
        raise ResourcePolicyError('The Warden itself is not confined to its verified efficiency cores')
    return {**host, 'pid':process.pid, 'priority_class':int(priority),
            'observed_logical_processors':affinity, 'controller_policy_verified':True}


def _jitter_seconds(limits: ResourceLimits | None, kind: str) -> float:
    limits = limits or ResourceLimits()
    low,high = getattr(limits,kind+'_jitter_min_ms'),getattr(limits,kind+'_jitter_max_ms')
    return (low+secrets.randbelow(high-low+1))/1000


def launch_delay_seconds(limits: ResourceLimits | None = None) -> float:
    return _jitter_seconds(limits,'launch')


def poll_delay_seconds(limits: ResourceLimits | None = None) -> float:
    return _jitter_seconds(limits,'poll')


def node_heap_options(limits: ResourceLimits | None = None) -> str:
    """Replace inherited injection flags; this constrains Node, not Rust/Bun."""
    limits = limits or ResourceLimits()
    return '--max-old-space-size='+str(limits.node_heap_mb)+' --max-semi-space-size=16'


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),
                ('LimitFlags',_DWORD),('MinimumWorkingSetSize',_SIZE_T),('MaximumWorkingSetSize',_SIZE_T),
                ('ActiveProcessLimit',_DWORD),('Affinity',_SIZE_T),('PriorityClass',_DWORD),('SchedulingClass',_DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                                                  'ReadTransferCount','WriteTransferCount','OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation',_BasicLimits),('IoInfo',_IOCounters),('ProcessMemoryLimit',_SIZE_T),
                ('JobMemoryLimit',_SIZE_T),('PeakProcessMemoryUsed',_SIZE_T),('PeakJobMemoryUsed',_SIZE_T)]


class _CpuRate(ctypes.Structure):
    _fields_ = [('ControlFlags',_DWORD),('CpuRate',_DWORD)]


class _GroupAffinity(ctypes.Structure):
    _fields_ = [('Mask',_SIZE_T),('Group',ctypes.c_uint16),('Reserved',ctypes.c_uint16*3)]


def _structures(limits: ResourceLimits, affinity_mask: int = 0) -> tuple[_ExtendedLimits,_CpuRate]:
    if not isinstance(limits,ResourceLimits):
        raise TypeError('Native execution requires validated ResourceLimits')
    extended = _ExtendedLimits()
    extended.BasicLimitInformation.LimitFlags = _JOB_MEMORY|_ACTIVE_PROCESS|_KILL_ON_JOB_CLOSE|_PRIORITY_CLASS
    extended.BasicLimitInformation.ActiveProcessLimit = limits.max_processes
    extended.BasicLimitInformation.PriorityClass = _BELOW_NORMAL_PRIORITY_CLASS
    if affinity_mask:
        extended.BasicLimitInformation.LimitFlags |= _AFFINITY
        extended.BasicLimitInformation.Affinity = affinity_mask
    extended.JobMemoryLimit = limits.memory_limit_mb*1024*1024
    cpu = _CpuRate(ControlFlags=_CPU_ENABLE|_CPU_HARD_CAP,CpuRate=limits.cpu_rate_percent*100)
    return extended,cpu


def apply_job_limits(job_handle, limits: ResourceLimits) -> dict:
    """Set and read back all limits; failure prevents the caller resuming a child.

    The caller owns the handle and must close/terminate its suspended child on
    any failure. This helper neither launches processes nor consumes handles.
    """
    if os.name != 'nt':
        raise ResourcePolicyError('Native Job Object limits require Windows')
    value = job_handle.value if isinstance(job_handle,_HANDLE) else job_handle
    if type(value) is not int or not 0 < value < ctypes.c_size_t(-1).value:
        raise ValueError('A valid native Job Object handle is required')
    host = validate_host_limits(limits)
    extended,cpu = _structures(limits,host['affinity_mask'])
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    set_information = kernel.SetInformationJobObject
    set_information.argtypes = [_HANDLE,ctypes.c_int,ctypes.c_void_p,_DWORD]
    set_information.restype = ctypes.c_int
    query = kernel.QueryInformationJobObject
    query.argtypes = [_HANDLE,ctypes.c_int,ctypes.c_void_p,_DWORD,ctypes.POINTER(_DWORD)]
    query.restype = ctypes.c_int
    if host['affinity_mask']:
        group = _GroupAffinity(Mask=host['affinity_mask'],Group=host['affinity_group'])
        if not set_information(_HANDLE(value),14,ctypes.byref(group),ctypes.sizeof(group)):
            raise ResourcePolicyError(str(ctypes.WinError(ctypes.get_last_error())))
        observed_group = _GroupAffinity()
        if not query(_HANDLE(value),14,ctypes.byref(observed_group),ctypes.sizeof(observed_group),None):
            raise ResourcePolicyError(str(ctypes.WinError(ctypes.get_last_error())))
        if observed_group.Group != group.Group or observed_group.Mask != group.Mask:
            raise ResourcePolicyError('Windows did not retain the required E-core processor group affinity')
    for info_class,structure in ((9,extended),(15,cpu)):
        if not set_information(_HANDLE(value),info_class,ctypes.byref(structure),ctypes.sizeof(structure)):
            raise ResourcePolicyError(str(ctypes.WinError(ctypes.get_last_error())))
    observed_extended,observed_cpu = _ExtendedLimits(),_CpuRate()
    for info_class,structure in ((9,observed_extended),(15,observed_cpu)):
        if not query(_HANDLE(value),info_class,ctypes.byref(structure),ctypes.sizeof(structure),None):
            raise ResourcePolicyError(str(ctypes.WinError(ctypes.get_last_error())))
    required = extended.BasicLimitInformation.LimitFlags
    if (observed_extended.BasicLimitInformation.LimitFlags & required != required
            or observed_extended.JobMemoryLimit != extended.JobMemoryLimit
            or observed_extended.BasicLimitInformation.ActiveProcessLimit != limits.max_processes
            or observed_extended.BasicLimitInformation.PriorityClass != _BELOW_NORMAL_PRIORITY_CLASS
            or (host['affinity_mask'] and observed_extended.BasicLimitInformation.Affinity != extended.BasicLimitInformation.Affinity)
            or observed_cpu.ControlFlags & (_CPU_ENABLE|_CPU_HARD_CAP) != (_CPU_ENABLE|_CPU_HARD_CAP)
            or observed_cpu.CpuRate != cpu.CpuRate):
        raise ResourcePolicyError('Windows did not retain the requested worker Job Object limits')
    return {**limits.as_dict(),**host,'kill_on_job_close':True,'native_limits_verified':True}
