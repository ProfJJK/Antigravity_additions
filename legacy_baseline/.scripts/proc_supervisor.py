import ctypes
import os
import subprocess
import threading
import time
from typing import Optional, ContextManager
import contextlib
import psutil

# Module-level registry for job handles
_job_handles = {}
_job_lock = threading.Lock()

# Constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00004000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# Windows API declarations
kernel32 = ctypes.windll.kernel32
INVALID_HANDLE_VALUE = -1

class JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", ctypes.c_uint64 * 6),
        ("IoInfo", ctypes.c_uint64 * 5),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("TotalMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("TotalProcessesTime", ctypes.c_uint64),
        ("TotalKernelTime", ctypes.c_uint64),
        ("TotalUserTime", ctypes.c_uint64),
        ("LastProcessId", ctypes.c_ulong),
    ]

def _create_job_object(name: str, limit_flags: int) -> int:
    job_handle = kernel32.CreateJobObjectW(None, name)
    if not job_handle or job_handle == INVALID_HANDLE_VALUE:
        raise RuntimeError("Failed to create job object")

    info = JobObjectExtendedLimitInformation()
    info.BasicLimitInformation[0] = limit_flags
    info.BasicLimitInformation[1] = 0x10000000  # JOB_OBJECT_LIMIT_PROCESS_TIME

    success = kernel32.SetInformationJobObject(
        job_handle,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info)
    )
    if not success:
        raise RuntimeError("Failed to set job object information")

    return job_handle

class Supervisor:
    def __init__(self, name: str, *, max_procs: int | None = None,
                 max_memory_mb: int | None = None):
        self.name = name
        self.job_handle = _create_job_object(name, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        with _job_lock:
            _job_handles[name] = self.job_handle

    def adopt_self(self) -> None:
        kernel32.AssignProcessToJobObject(self.job_handle, kernel32.GetCurrentProcess())

    def popen(self, cmd, **kw) -> subprocess.Popen:
        creationflags = kw.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW | 0x00000004  # CREATE_SUSPENDED
        kw['creationflags'] = creationflags

        proc = subprocess.Popen(cmd, **kw)

        # Assign to job object
        kernel32.AssignProcessToJobObject(self.job_handle, proc._handle)

        # Resume the main thread
        threads = psutil.Process(proc.pid).threads()
        if not threads:
            raise RuntimeError("No threads found for process")
        thread_id = threads[0].id

        thread_handle = kernel32.OpenThread(0x00000002, False, thread_id)  # THREAD_SUSPEND_RESUME
        if thread_handle:
            kernel32.ResumeThread(thread_handle)
            kernel32.CloseHandle(thread_handle)

        return proc

    def run(self, cmd, *, timeout: float, input=None, **kw) -> subprocess.CompletedProcess:
        # Create a nested job for this specific run call
        nested_name = f"{self.name}_nested_{int(time.time() * 1000)}"
        nested_job = _create_job_object(nested_name, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        with _job_lock:
            _job_handles[nested_name] = nested_job

        try:
            creationflags = kw.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW | 0x00000004  # CREATE_SUSPENDED
            kw['creationflags'] = creationflags

            proc = subprocess.Popen(cmd, **kw)

            kernel32.AssignProcessToJobObject(nested_job, proc._handle)

            threads = psutil.Process(proc.pid).threads()
            if not threads:
                raise RuntimeError("No threads found for process")
            thread_id = threads[0].id

            thread_handle = kernel32.OpenThread(0x00000002, False, thread_id)
            if thread_handle:
                kernel32.ResumeThread(thread_handle)
                kernel32.CloseHandle(thread_handle)

            try:
                stdout, stderr = proc.communicate(input=input, timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill the nested job
                kernel32.TerminateJobObject(nested_job, 1)
                raise DeadlineExceeded("Command timed out")

            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        finally:
            # Cleanup nested job
            with _job_lock:
                if nested_name in _job_handles:
                    del _job_handles[nested_name]
            kernel32.CloseHandle(nested_job)

    def terminate_all(self, exit_code: int = 1) -> None:
        kernel32.TerminateJobObject(self.job_handle, exit_code)

    def active_count(self) -> int:
        class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("TotalProcesses", ctypes.c_ulong),
                ("ActiveProcesses", ctypes.c_ulong),
                ("TotalTerminatedProcesses", ctypes.c_ulong),
            ]

        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        size = ctypes.sizeof(info)
        success = kernel32.QueryInformationJobObject(
            self.job_handle,
            1,  # JobObjectBasicAccountingInformation
            ctypes.byref(info),
            size,
            None
        )
        if not success:
            return 0
        return info.ActiveProcesses

    def close(self) -> None:
        with _job_lock:
            if self.name in _job_handles:
                del _job_handles[self.name]
        kernel32.CloseHandle(self.job_handle)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

class DeadlineExceeded(Exception): pass

class Deadline:
    def __init__(self, seconds: float):
        self.start = time.time()
        self.seconds = seconds

    def remaining(self) -> float:
        elapsed = time.time() - self.start
        remaining = self.seconds - elapsed
        if remaining <= 0:
            raise DeadlineExceeded("Deadline exceeded")
        return remaining

def llm_slot(timeout: float | None = None) -> ContextManager:
    semaphore_name = "Global\\CoChem_LLM_Slots"
    max_slots = int(os.environ.get('COCHEM_MAX_LLM_PROCS', '12'))

    # Try global first, fallback to local
    try:
        handle = kernel32.CreateSemaphoreW(None, max_slots, max_slots, semaphore_name)
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise RuntimeError("Failed to create semaphore")
    except Exception:
        semaphore_name = "Local\\CoChem_LLM_Slots"
        handle = kernel32.CreateSemaphoreW(None, max_slots, max_slots, semaphore_name)
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise RuntimeError("Failed to create semaphore")

    class SemaphoreContextManager:
        def __enter__(self):
            wait_time = timeout if timeout is not None else 0xFFFFFFFF
            result = kernel32.WaitForSingleObject(handle, wait_time)
            if result != 0:
                kernel32.CloseHandle(handle)
                raise DeadlineExceeded("Timeout waiting for LLM slot")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            kernel32.ReleaseSemaphore(handle, 1, None)
            kernel32.CloseHandle(handle)

    return SemaphoreContextManager()
