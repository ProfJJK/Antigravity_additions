"""Limit contracts, real empty Win32 jobs, and opt-in child enforcement."""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
import json
import os
import subprocess
import sys
import struct
import shutil

import pytest

from cochem_pipeline import resource_limits as resource
from cochem_pipeline.resource_limits import (ResourceLimits, apply_job_limits, _structures, _BasicLimits, _ExtendedLimits, _CpuRate,
    parse_cpu_sets, select_e_core_affinity, validate_host_limits, launch_delay_seconds, poll_delay_seconds, node_heap_options)


def test_limits_roundtrip_preserves_explicit_aggregate_caps():
    limits=ResourceLimits.from_dict({'memory_limit_mb':1024,'cpu_rate_percent':15,'max_processes':8})
    assert ResourceLimits.from_dict(json.loads(json.dumps(limits.as_dict())))==limits
    assert ResourceLimits().cpu_rate_percent==20


@pytest.mark.parametrize('settings',[{'memory_limit_mb':0},{'memory_limit_mb':63},{'memory_limit_mb':1048577},
    {'memory_limit_mb':True},{'memory_limit_mb':2048.0},{'cpu_rate_percent':0},{'cpu_rate_percent':101},
    {'max_processes':0},{'max_processes':1025},{'max_processes':'16'},{'unknown':1},
    {'e_core_policy':'guess'},{'affinity_mask':True},{'affinity_mask':-1},{'affinity_mask':1<<64},
    {'affinity_mask':16,'e_core_policy':'off'},{'node_heap_mb':513},{'node_heap_mb':63},
    {'launch_jitter_min_ms':99},{'launch_jitter_max_ms':501},{'poll_jitter_min_ms':0},
    {'poll_jitter_min_ms':400,'poll_jitter_max_ms':200}])
def test_invalid_limits_are_rejected_before_a_native_call(settings):
    with pytest.raises(ValueError): ResourceLimits.from_dict(settings)


def test_ctypes_wire_structures_include_all_limits_and_kill_on_close():
    limits=ResourceLimits()
    extended,cpu=_structures(limits)
    assert extended.BasicLimitInformation.LimitFlags==0x2228
    assert extended.BasicLimitInformation.PriorityClass==0x4000
    assert extended.BasicLimitInformation.ActiveProcessLimit==16
    assert extended.JobMemoryLimit==2048*1024*1024
    assert cpu.ControlFlags==5 and cpu.CpuRate==2000
    assert ctypes.sizeof(_CpuRate)==8
    if ctypes.sizeof(ctypes.c_void_p)==8:
        assert ctypes.sizeof(_BasicLimits)==64
        assert ctypes.sizeof(_ExtendedLimits)==144
        assert _ExtendedLimits.JobMemoryLimit.offset==120


def test_native_limits_cannot_be_claimed_on_an_unsupported_host():
    if os.name=='nt':
        with pytest.raises(ValueError): apply_job_limits(0,ResourceLimits())
    else:
        with pytest.raises(RuntimeError,match='require Windows'):
            apply_job_limits(1,ResourceLimits())


def _cpu(identifier,index,efficiency,*,group=0,flags=0):
    value=bytearray(32)
    struct.pack_into('<IIIHBBBBBB',value,0,32,0,identifier,group,index,index,0,0,efficiency,flags)
    return bytes(value)


def test_affinity_uses_reported_efficiency_classes_and_never_hardcodes_mask():
    topology=parse_cpu_sets(b''.join(_cpu(i+100,i,1 if i<16 else 0) for i in range(24)))
    selected=select_e_core_affinity(topology)
    assert selected['affinity_mask']==0x00FF0000
    assert selected['logical_processors']==list(range(16,24))
    assert select_e_core_affinity(topology,0x003F0000)['logical_processors']==list(range(16,22))
    different=parse_cpu_sets(b''.join(_cpu(i+200,i,0 if i<4 else 2) for i in range(12)))
    assert select_e_core_affinity(different)['affinity_mask']==0xF
    with pytest.raises(ValueError,match='nonexistent, unavailable or performance'):
        select_e_core_affinity(different,0x00FF0000)


def test_uniform_unavailable_and_other_group_cores_cannot_be_reported_as_usable_ecores():
    for raw in (b''.join(_cpu(i,i,0) for i in range(4)),
                _cpu(1,0,1)+_cpu(2,1,0,flags=2),
                _cpu(1,0,1)+_cpu(2,0,0,group=1)):
        with pytest.raises(RuntimeError):
            select_e_core_affinity(parse_cpu_sets(raw))
    # Parked efficiency cores can be unparked by the Windows scheduler. An
    # exclusive allocation to a different process is the exclusion criterion.
    assert select_e_core_affinity(parse_cpu_sets(_cpu(1,0,1)+_cpu(2,1,0,flags=1)))['affinity_mask']==2


@pytest.mark.parametrize('raw',[b'',bytes(7),struct.pack('<II',99,0),_cpu(1,0,1)[:-1],
    _cpu(1,0,1)+_cpu(1,1,0),_cpu(1,0,1)+_cpu(2,0,0),_cpu(1,64,0)])
def test_malformed_topology_never_authorizes_a_mask(raw):
    with pytest.raises(ValueError): parse_cpu_sets(raw)


def test_unknown_future_topology_records_are_bounded_and_skipped():
    raw=struct.pack('<II',8,99)+_cpu(1,0,1)+_cpu(2,1,0)
    assert select_e_core_affinity(parse_cpu_sets(raw))['affinity_mask']==2


def test_requested_affinity_and_priority_are_placed_in_actual_job_structures():
    extended,_=_structures(ResourceLimits(),0x30)
    assert extended.BasicLimitInformation.LimitFlags & 0x30 == 0x30
    assert extended.BasicLimitInformation.Affinity==0x30
    assert extended.BasicLimitInformation.PriorityClass==0x4000


def test_launch_and_poll_jitter_respect_the_separate_short_srs_bounds():
    for _ in range(100):
        assert .1<=launch_delay_seconds()<=.5
        assert .1<=poll_delay_seconds()<=.5
    fixed=ResourceLimits(launch_jitter_min_ms=250,launch_jitter_max_ms=250,
                         poll_jitter_min_ms=400,poll_jitter_max_ms=400)
    assert launch_delay_seconds(fixed)==.25
    assert poll_delay_seconds(fixed)==.4


def test_real_node_process_honors_sanitized_worker_heap_limit():
    node=shutil.which('node')
    if not node: pytest.skip('Actual Node binary is not installed')
    options=node_heap_options()
    assert options=='--max-old-space-size=512 --max-semi-space-size=16'
    result=subprocess.run([node,'-e','process.stdout.write(String(require("v8").getHeapStatistics().heap_size_limit))'],
        env={**os.environ,'NODE_OPTIONS':options},capture_output=True,text=True,timeout=10,check=True)
    # V8 adds implementation overhead/young generation beyond old-space. This
    # measures the live runtime and does not claim a bound for Rust/Bun CLIs.
    assert 512*1024*1024 <= int(result.stdout) <= 600*1024*1024


def test_explicit_non_srs_profile_never_claims_verified_ecore_topology():
    observed=validate_host_limits(ResourceLimits(e_core_policy='off'))
    assert observed['topology_verified'] is False and observed['affinity_mask']==0


@pytest.mark.parametrize('length',[0,15,17,32])
def test_group_affinity_readback_requires_one_complete_record(length):
    # An ABI result with correct-looking fields but the wrong returned length
    # cannot attest one complete GROUP_AFFINITY record.
    def query(handle,kind,buffer,capacity,returned):
        ctypes.cast(returned,ctypes.POINTER(ctypes.c_uint32))[0]=length
        return 1
    with pytest.raises(resource.ResourcePolicyError,match='incomplete or ambiguous'):
        resource._query_job_information(query,1,14,resource._GroupAffinity())


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows last-error propagation')
def test_native_failure_preserves_numeric_winerror_and_original_cause():
    ctypes.set_last_error(87)
    with pytest.raises(resource.ResourcePolicyError) as caught:
        resource._native_policy_error('Disposable native operation')
    assert caught.value.winerror==87
    assert isinstance(caught.value.__cause__,OSError)
    assert caught.value.__cause__.winerror==87


@contextmanager
def _empty_native_job():
    """Ordinary-user anonymous Job; never assign or launch a process."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([ctypes.c_void_p,ctypes.c_wchar_p],ctypes.c_void_p),
        'CloseHandle':([ctypes.c_void_p],ctypes.c_int),
        'SetInformationJobObject':([ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
        'QueryInformationJobObject':([ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.c_uint32)],ctypes.c_int),
    }
    for name,(arguments,result) in signatures.items():
        fn=getattr(kernel,name);fn.argtypes=arguments;fn.restype=result
    job=kernel.CreateJobObjectW(None,None)
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    try:yield kernel,job
    finally:assert kernel.CloseHandle(job),'Disposable empty Job handle must close'


def _available_native_hybrid_host():
    topology=resource.native_cpu_sets()
    if len({row['efficiency_class'] for row in topology})<2:
        pytest.skip('Actual host does not report heterogeneous efficiency classes')
    return resource.select_e_core_affinity(topology)


@pytest.mark.skipif(os.name!='nt',reason='Actual empty Windows Job and required affinity')
def test_required_ecore_empty_job_retains_every_limit_after_final_set():
    host=_available_native_hybrid_host()
    limits=ResourceLimits()
    with _empty_native_job() as (kernel,job):
        evidence=apply_job_limits(job,limits)
        assert evidence['native_limits_verified'] and evidence['affinity_mask']==host['affinity_mask']
        observed=[]
        for kind,record in ((14,resource._GroupAffinity()),(9,_ExtendedLimits()),(15,_CpuRate())):
            returned=ctypes.c_uint32()
            assert kernel.QueryInformationJobObject(job,kind,ctypes.byref(record),ctypes.sizeof(record),ctypes.byref(returned))
            assert returned.value==ctypes.sizeof(record)
            observed.append(record)
        group,extended,cpu=observed
        assert group.Group==host['affinity_group'] and group.Mask==host['affinity_mask']
        assert extended.BasicLimitInformation.Affinity==host['affinity_mask']
        assert extended.BasicLimitInformation.LimitFlags & 0x2238==0x2238
        assert extended.BasicLimitInformation.PriorityClass==0x4000
        assert extended.BasicLimitInformation.ActiveProcessLimit==16
        assert extended.JobMemoryLimit==2048*1024*1024
        assert cpu.ControlFlags & 5==5 and cpu.CpuRate==2000


_NATIVE=pytest.mark.skipif(os.name!='nt' or os.environ.get('COCHEM_RUN_NATIVE_RESOURCE_TESTS')!='1',
    reason='Opt-in native Windows Job Object enforcement requires COCHEM_RUN_NATIVE_RESOURCE_TESTS=1')


def _run_native_limited_child(script,limits):
    """Create suspended, assign constrained job, resume, and wait for real exit."""
    from ctypes import wintypes as wt
    class Startup(ctypes.Structure):
        _fields_=[('cb',wt.DWORD),('lpReserved',wt.LPWSTR),('lpDesktop',wt.LPWSTR),('lpTitle',wt.LPWSTR),
            ('dwX',wt.DWORD),('dwY',wt.DWORD),('dwXSize',wt.DWORD),('dwYSize',wt.DWORD),
            ('dwXCountChars',wt.DWORD),('dwYCountChars',wt.DWORD),('dwFillAttribute',wt.DWORD),
            ('dwFlags',wt.DWORD),('wShowWindow',wt.WORD),('cbReserved2',wt.WORD),
            ('lpReserved2',ctypes.POINTER(ctypes.c_ubyte)),('hStdInput',wt.HANDLE),
            ('hStdOutput',wt.HANDLE),('hStdError',wt.HANDLE)]
    class ProcessInfo(ctypes.Structure):
        _fields_=[('hProcess',wt.HANDLE),('hThread',wt.HANDLE),('dwProcessId',wt.DWORD),('dwThreadId',wt.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([ctypes.c_void_p,wt.LPCWSTR],wt.HANDLE),
        'CreateProcessW':([wt.LPCWSTR,wt.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,wt.BOOL,wt.DWORD,
                           ctypes.c_void_p,wt.LPCWSTR,ctypes.POINTER(Startup),ctypes.POINTER(ProcessInfo)],wt.BOOL),
        'AssignProcessToJobObject':([wt.HANDLE,wt.HANDLE],wt.BOOL),
        'ResumeThread':([wt.HANDLE],wt.DWORD),
        'WaitForSingleObject':([wt.HANDLE,wt.DWORD],wt.DWORD),
        'GetExitCodeProcess':([wt.HANDLE,ctypes.POINTER(wt.DWORD)],wt.BOOL),
        'TerminateJobObject':([wt.HANDLE,wt.UINT],wt.BOOL),
        'TerminateProcess':([wt.HANDLE,wt.UINT],wt.BOOL),
        'CloseHandle':([wt.HANDLE],wt.BOOL),
    }
    for name,(arguments,result) in signatures.items():
        function=getattr(kernel,name); function.argtypes=arguments; function.restype=result
    job=kernel.CreateJobObjectW(None,None)
    if not job: raise ctypes.WinError(ctypes.get_last_error())
    process=ProcessInfo(); assigned=False
    try:
        evidence=apply_job_limits(job,limits)
        assert evidence['native_limits_verified'] is True
        startup=Startup(); startup.cb=ctypes.sizeof(startup)
        # Use the base interpreter directly so a venv redirector cannot consume
        # an extra process or turn a rejected grandchild into launcher exit 1.
        executable=getattr(sys,'_base_executable',sys.executable)
        command=ctypes.create_unicode_buffer(subprocess.list2cmdline([executable,'-I','-c',script]))
        if not kernel.CreateProcessW(executable,command,None,None,False,0x08000004,None,None,
                                      ctypes.byref(startup),ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job,process.hProcess):
            raise ctypes.WinError(ctypes.get_last_error())
        assigned=True
        if kernel.ResumeThread(process.hThread)==0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        assert kernel.WaitForSingleObject(process.hProcess,15000)==0,'Native limit child did not exit'
        code=wt.DWORD()
        if not kernel.GetExitCodeProcess(process.hProcess,ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel.TerminateJobObject(job,99)
        if process.hProcess:
            if not assigned: kernel.TerminateProcess(process.hProcess,99)
            kernel.WaitForSingleObject(process.hProcess,5000)
        for handle in (process.hThread,process.hProcess,job):
            if handle: kernel.CloseHandle(handle)


@_NATIVE
def test_native_aggregate_memory_cap_denies_child_allocation():
    script='import sys\ntry:\n a=[bytearray(1024*1024) for _ in range(256)]\nexcept MemoryError:\n sys.exit(42)\nsys.exit(1)'
    assert _run_native_limited_child(script,ResourceLimits(memory_limit_mb=64,e_core_policy='off'))==42


@_NATIVE
def test_native_active_process_cap_denies_grandchild_creation(tmp_path):
    marker=tmp_path/'grandchild-ran'
    grandchild='from pathlib import Path;Path('+repr(str(marker))+').write_bytes(b"ran")'
    script=('import sys,subprocess\ntry:\n code=subprocess.Popen([sys.executable,"-I","-c",'+repr(grandchild)+']).wait()'
            '\nexcept OSError:\n sys.exit(43)\nsys.exit(0 if code==0 else 43)')
    assert _run_native_limited_child(script,ResourceLimits(max_processes=3,e_core_policy='off'))==0
    assert marker.read_bytes()==b'ran'
    marker.unlink()
    # Windows can reject creation or terminate the attempted process as it
    # associates with a full Job. Either must prevent its body from executing.
    assert _run_native_limited_child(script,ResourceLimits(max_processes=1,e_core_policy='off'))==43
    assert not marker.exists()


@_NATIVE
def test_native_job_forces_below_normal_priority():
    script='import ctypes,sys\nk=ctypes.WinDLL("kernel32");k.GetCurrentProcess.restype=ctypes.c_void_p;k.GetPriorityClass.argtypes=[ctypes.c_void_p]\nsys.exit(0 if k.GetPriorityClass(k.GetCurrentProcess())==0x4000 else 9)'
    assert _run_native_limited_child(script,ResourceLimits(e_core_policy='off'))==0


@_NATIVE
def test_native_hybrid_host_applies_real_efficiency_core_mask():
    limits=ResourceLimits()
    observed=validate_host_limits(limits)
    mask=observed['affinity_mask']
    script='import ctypes,sys\nk=ctypes.WinDLL("kernel32");k.GetCurrentProcess.restype=ctypes.c_void_p;k.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p];k.GetPriorityClass.argtypes=[ctypes.c_void_p]\na=ctypes.c_size_t();b=ctypes.c_size_t();process=k.GetCurrentProcess();ok=k.GetProcessAffinityMask(process,ctypes.byref(a),ctypes.byref(b))\nsys.exit(0 if ok and a.value=='+str(mask)+' and k.GetPriorityClass(process)==0x4000 else 9)'
    assert _run_native_limited_child(script,limits)==0
