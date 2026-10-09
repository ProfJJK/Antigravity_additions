"""Bounded, native resource measurements; unavailable is never a zero reading.

All memory and throughput values named ``mb`` are MiB. Disk I/O describes the
host counters reported by psutil, not a guessed mapping between a Windows drive
letter and a physical disk. These measurements do not make admission decisions.
"""

from __future__ import annotations

import csv
import ctypes
import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psutil

_MIB = 1024 * 1024
_GPU_QUERY = "index,uuid,memory.used,memory.total,temperature.gpu,utilization.gpu"
_PROBE_ERRORS = (OSError, psutil.Error, ValueError, NotImplementedError)


def _error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:256] or type(exc).__name__


def _finite(value: Any, name: str, minimum: float = 0, maximum: float | None = None) -> float:
    number = float(value)
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        raise ValueError(f"OS reported invalid {name}")
    return number


def _cpu(sample_seconds: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False, "count": None, "percent": None,
        "physical_count": None, "physical_count_available": False,
        "physical_count_source": "psutil.cpu_count(logical=False)", "physical_count_error": None,
        "source": "psutil.cpu_count/cpu_affinity/cpu_percent",
        "temperature_available": False, "temperature_celsius": None,
        "temperature_source": None, "temperature_error": None, "error": None,
    }
    try:
        count = psutil.cpu_count(logical=True)
        if not isinstance(count, int) or count < 1:
            raise ValueError("OS did not report a usable CPU count")
        process = psutil.Process()
        if hasattr(process, "cpu_affinity"):
            affinity = process.cpu_affinity()
            if not affinity:
                raise ValueError("OS reported no CPUs in process affinity")
            count = min(count, len(affinity))
        result["count"] = count
        try:
            physical = psutil.cpu_count(logical=False)
            if type(physical) is not int or physical < 1:
                raise ValueError('OS did not report a usable physical CPU count')
            result['physical_count'] = physical
            result['physical_count_available'] = True
        except _PROBE_ERRORS as exc:
            result['physical_count_error'] = _error(exc)
        result["percent"] = _finite(psutil.cpu_percent(interval=sample_seconds), "CPU utilization", maximum=100)
        result["available"] = True
    except _PROBE_ERRORS as exc:
        result["error"] = _error(exc)
    return result


def _cpu_temperature(result: dict[str, Any], probe: str | None = None) -> None:
    if probe is not None:
        result['temperature_source'] = 'Protected LibreHardwareMonitorLib 0.9.6 CPU probe'
        try:
            if os.name != 'nt':
                raise OSError('The configured CPU probe requires native Windows SYSTEM')
            from .cpu_temperature import parse_cpu_temperature, verify_probe_bundle
            executable = Path(probe)
            executable = verify_probe_bundle(executable)
            nonce = uuid.uuid4().hex
            raw = _bounded_command([str(executable), '--nonce', nonce], cwd=executable.parent)
            result.update(temperature_available=True,
                          temperature_celsius=parse_cpu_temperature(raw, nonce, time.time()),
                          temperature_error=None)
        except (*_PROBE_ERRORS, AttributeError, UnicodeError, subprocess.SubprocessError) as exc:
            result['temperature_error'] = 'Protected CPU temperature probe unavailable: ' + _error(exc)
        return
    try:
        probe = getattr(psutil, "sensors_temperatures", None)
        if probe is None:
            raise OSError("CPU temperature sensors are unavailable on this OS")
        readings = []
        sources = set()
        for chip, entries in probe(fahrenheit=False).items():
            # ACPI and NVMe temperatures cannot be assumed to measure the CPU.
            if chip.casefold() not in {"coretemp", "k10temp", "cpu_thermal", "cpu-thermal", "zenpower", "cpu"}:
                continue
            for entry in entries:
                if entry.current is not None:
                    readings.append(_finite(entry.current, "CPU temperature", minimum=-273.15, maximum=300))
                    sources.add(chip)
        if not readings:
            raise OSError("OS did not expose an identifiable CPU temperature sensor")
        result.update(temperature_available=True, temperature_celsius=max(readings), temperature_source=",".join(sorted(sources)))
    except _PROBE_ERRORS as exc:
        result["temperature_error"] = _error(exc)
    if not result["temperature_available"] and os.name == "nt":
        _windows_cpu_temperature(result)


def _windows_system_directory() -> Path:
    """Resolve native probes independently of PATH, cwd, and environment values."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    directory = kernel.GetSystemDirectoryW
    directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    directory.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Could not locate the protected Windows system directory")
    return Path(buffer.value)


def _protected_windows_probe(executable: Path) -> str:
    # The production governor runs as SYSTEM. A telemetry command is privileged
    # executable code and needs the same owner/ancestor protection as the worker
    # binaries; an absolute path alone does not establish that protection.
    from .windows import WindowsIsolationError, validate_code_path
    if not executable.is_absolute():
        raise OSError("A Windows resource probe must use a protected absolute executable path")
    try:
        validate_code_path(executable)
    except WindowsIsolationError as exc:
        raise OSError("Resource probe executable failed privileged code-path validation: " + _error(exc)) from exc
    return str(executable)


def _windows_cpu_temperature(result: dict[str, Any]) -> None:
    """Read identifiable CPU sensors from the installed LHM WMI provider."""
    result["temperature_source"] = "LibreHardwareMonitor WMI root/LibreHardwareMonitor:Sensor"
    try:
        executable = _protected_windows_probe(_windows_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        command = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "@(Get-CimInstance -Namespace 'root/LibreHardwareMonitor' -ClassName Sensor -ErrorAction Stop | "
            "Where-Object {$_.SensorType -eq 'Temperature' -and "
            "($_.Identifier -like '/intelcpu/*' -or $_.Identifier -like '/amdcpu/*')} | "
            "Select-Object Identifier,Value) | ConvertTo-Json -Compress"
        )
        raw = _bounded_command([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command])
        sensors = json.loads(raw.decode("utf-8-sig"))
        if isinstance(sensors, dict):
            sensors = [sensors]
        if not isinstance(sensors, list) or not sensors or len(sensors) > 256:
            raise ValueError("WMI did not report supported CPU temperature sensors")
        temperatures = []
        for sensor in sensors:
            if not isinstance(sensor, dict) or not isinstance(sensor.get("Identifier"), str):
                raise ValueError("WMI reported an invalid CPU sensor")
            if not sensor["Identifier"].startswith(("/intelcpu/", "/amdcpu/")):
                raise ValueError("WMI sensor identity is not a CPU")
            value = sensor.get("Value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("WMI CPU sensor did not provide a temperature")
            temperatures.append(_finite(value, "WMI CPU temperature", minimum=-273.15, maximum=300))
        result.update(temperature_available=True, temperature_celsius=max(temperatures), temperature_error=None)
    except (*_PROBE_ERRORS, AttributeError, UnicodeError, subprocess.SubprocessError) as exc:
        result["temperature_error"] = (
            "CPU temperature requires a running LibreHardwareMonitor WMI provider with CPU sensors enabled: " + _error(exc)
        )


def _memory() -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False, "total_mb": None, "available_mb": None,
        "source": "psutil.virtual_memory", "error": None,
    }
    try:
        memory = psutil.virtual_memory()
        total = _finite(memory.total, "physical memory", minimum=1)
        available = _finite(memory.available, "available memory", maximum=total)
        result.update(available=True, total_mb=total / _MIB, available_mb=available / _MIB)
    except _PROBE_ERRORS as exc:
        result["error"] = _error(exc)
    return result


def _commit() -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False, "total_mb": None, "limit_mb": None, "free_mb": None,
        "source": "Windows GetPerformanceInfo", "error": None,
        "process_memory_available": False, "process_private_mb": None,
        "process_working_set_mb": None, "process_memory_error": None,
        "process_memory_source": "psutil.Process.memory_info (Windows private/rss)",
    }
    if os.name != "nt":
        result["error"] = "Windows commit accounting is unavailable on this OS"
        result["process_memory_error"] = "Windows process private-commit accounting is unavailable on this OS"
        return result

    class PerformanceInformation(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            *[(name, ctypes.c_size_t) for name in (
                "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal",
                "PhysicalAvailable", "SystemCache", "KernelTotal", "KernelPaged",
                "KernelNonpaged", "PageSize",
            )],
            ("HandleCount", ctypes.c_uint32), ("ProcessCount", ctypes.c_uint32),
            ("ThreadCount", ctypes.c_uint32),
        ]

    try:
        library = ctypes.WinDLL("psapi", use_last_error=True)
        probe = library.GetPerformanceInfo
        probe.argtypes = [ctypes.POINTER(PerformanceInformation), ctypes.c_uint32]
        probe.restype = ctypes.c_int
        info = PerformanceInformation()
        info.cb = ctypes.sizeof(info)
        if not probe(ctypes.byref(info), info.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        page_size = _finite(info.PageSize, "Windows page size", minimum=1)
        limit = _finite(info.CommitLimit * page_size, "Windows commit limit", minimum=1)
        total = _finite(info.CommitTotal * page_size, "Windows commit charge", maximum=limit)
        result.update(available=True, total_mb=total / _MIB, limit_mb=limit / _MIB, free_mb=(limit - total) / _MIB)
    except (*_PROBE_ERRORS, AttributeError) as exc:
        result["error"] = _error(exc)
    try:
        process_memory = psutil.Process().memory_info()
        private = _finite(process_memory.private, "process private commit")
        working_set = _finite(process_memory.rss, "process working set")
        result.update(process_memory_available=True, process_private_mb=private / _MIB, process_working_set_mb=working_set / _MIB)
    except (*_PROBE_ERRORS, AttributeError) as exc:
        result["process_memory_error"] = _error(exc)
    return result


def _bounded_command(argv: list[str], *, timeout: float = 3, max_output_bytes: int = 32768,
                     cwd: Path | None = None) -> bytes:
    """Drain both native pipes with bounded memory, including on Windows."""
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        shell=False, close_fds=True, cwd=cwd,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    output = [bytearray(), bytearray()]
    exceeded = threading.Event()
    read_errors: list[str] = []

    def read_pipe(pipe: Any, index: int) -> None:
        try:
            while chunk := pipe.read(4096):
                remaining = max_output_bytes - len(output[index])
                output[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
        except OSError as exc:
            read_errors.append(_error(exc))
        finally:
            pipe.close()

    readers = [threading.Thread(target=read_pipe, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=1)
    finally:
        for reader in readers:
            reader.join(timeout=0.5)
        if os.name == "nt" and process.returncode is not None and not any(reader.is_alive() for reader in readers):
            # Popen otherwise keeps this Windows handle until finalization;
            # retained error tracebacks can retain the terminated process too.
            # A still-running pipe reader may be inside process.kill().
            process._handle.Close()
    if timed_out:
        raise OSError("Resource probe command exceeded its timeout")
    if exceeded.is_set():
        raise OSError("Resource probe command exceeded its output limit")
    if any(reader.is_alive() for reader in readers):
        raise OSError("Resource probe command left an open output pipe")
    if read_errors:
        raise OSError("Resource probe command output could not be read")
    if process.returncode:
        raise OSError(f"Resource probe command exited with code {process.returncode}")
    return bytes(output[0])


def _gpus(executable: str) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "devices": [], "source": "nvidia-smi", "error": None}
    try:
        if os.name == "nt":
            path = (_windows_system_directory() / "nvidia-smi.exe"
                    if executable in {"nvidia-smi", "nvidia-smi.exe"} else Path(executable))
            executable = _protected_windows_probe(path)
        raw = _bounded_command([executable, f"--query-gpu={_GPU_QUERY}", "--format=csv,noheader,nounits"])
        rows = list(csv.reader(io.StringIO(raw.decode("ascii"))))
        if not rows or len(rows) > 64:
            raise ValueError("NVIDIA probe did not report a supported number of devices")
        devices = []
        identifiers = set()
        indices = set()
        for row in rows:
            if len(row) != 6:
                raise ValueError("NVIDIA probe returned an invalid device record")
            index, identity, used, total, temperature, utilization = (value.strip() for value in row)
            if not index.isdecimal() or not re.fullmatch(r"GPU-[0-9a-fA-F-]+", identity):
                raise ValueError("NVIDIA probe did not report a device identity")
            if identity in identifiers or int(index) in indices:
                raise ValueError("NVIDIA probe repeated a device identity")
            identifiers.add(identity)
            indices.add(int(index))
            total_mb = _finite(total, "GPU memory", minimum=1)
            devices.append({
                "id": identity, "index": int(index), "total_mb": total_mb,
                "used_mb": _finite(used, "GPU used memory", maximum=total_mb),
                "utilization_percent": _finite(utilization, "GPU utilization", maximum=100),
                "temperature_celsius": _finite(temperature, "GPU temperature", minimum=-273.15, maximum=300),
            })
        result.update(available=True, devices=devices)
    except (*_PROBE_ERRORS, UnicodeError, csv.Error, subprocess.SubprocessError) as exc:
        result["error"] = _error(exc)
    return result


def _disk_counters() -> tuple[dict[str, Any], Any, float, str | None]:
    try:
        devices = psutil.disk_io_counters(perdisk=True, nowrap=False)
        aggregate = psutil.disk_io_counters(perdisk=False, nowrap=False)
        if not devices or aggregate is None:
            raise OSError("OS did not report disk I/O counters")
        return devices, aggregate, time.perf_counter(), None
    except _PROBE_ERRORS as exc:
        return {}, None, time.perf_counter(), _error(exc)


def _io_delta(before: Any, after: Any, elapsed: float) -> dict[str, Any]:
    delta = {}
    for field in ("read_bytes", "write_bytes", "read_count", "write_count"):
        delta[field] = _finite(getattr(after, field) - getattr(before, field), f"disk {field} delta")
    result = {
        "read_mb_s": delta["read_bytes"] / elapsed / _MIB,
        "write_mb_s": delta["write_bytes"] / elapsed / _MIB,
        "total_mb_s": (delta["read_bytes"] + delta["write_bytes"]) / elapsed / _MIB,
        "total_iops": (delta["read_count"] + delta["write_count"]) / elapsed,
    }
    if hasattr(before, "busy_time") and hasattr(after, "busy_time"):
        busy_ms = _finite(after.busy_time - before.busy_time, "disk busy-time delta")
        # Kernel counters are quantized. Saturate their sampled fraction at 100%.
        result["busy_percent"] = min(100.0, busy_ms / (elapsed * 10))
    return result


def _disk_io(before: tuple, after: tuple) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False, "read_mb_s": None, "write_mb_s": None,
        "total_mb_s": None, "total_iops": None,
        "source": "psutil.disk_io_counters", "scope": "host",
        "devices": [], "errors": [], "sample_seconds": after[2] - before[2],
    }
    if before[3] or after[3]:
        result["errors"] = list(dict.fromkeys(error for error in (before[3], after[3]) if error))
        return result
    elapsed = result["sample_seconds"]
    try:
        if elapsed <= 0:
            raise ValueError("Disk I/O sampling interval was not positive")
        aggregate = _io_delta(before[1], after[1], elapsed)
        # Summed busy time is not a host utilization percentage.
        aggregate.pop("busy_percent", None)
        result.update(aggregate)
        for device in sorted(set(before[0]) | set(after[0])):
            if device not in before[0] or device not in after[0]:
                result["errors"].append(f"Disk counter appeared or disappeared during sample: {device}")
                continue
            try:
                result["devices"].append({"id": device, **_io_delta(before[0][device], after[0][device], elapsed)})
            except (ValueError, AttributeError) as exc:
                result["errors"].append(f"Disk {device}: {_error(exc)}")
        busy = [device["busy_percent"] for device in result["devices"] if "busy_percent" in device]
        if busy:
            result["busy_percent"] = max(busy)
            result["busy_source"] = "maximum observed per-device busy fraction"
        result["available"] = bool(result["devices"]) and not result["errors"]
    except (ValueError, AttributeError) as exc:
        result["errors"].append(_error(exc))
    return result


def _volumes(workspaces: Sequence[Path], io_result: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"available": False, "volumes": [], "io": io_result, "errors": []}
    try:
        partitions = psutil.disk_partitions(all=True)
    except _PROBE_ERRORS as exc:
        partitions = []
        result["errors"].append(f"Volume identification failed: {_error(exc)}")
    for supplied in workspaces:
        path = Path(supplied).resolve()
        try:
            disk = shutil.disk_usage(path)
            total = _finite(disk.total, "disk total bytes", minimum=1)
            free = _finite(disk.free, "disk free bytes", maximum=total)
            matches = [partition for partition in partitions if path.is_relative_to(Path(partition.mountpoint))]
            partition = max(matches, key=lambda item: len(item.mountpoint), default=None)
            # On Linux a visible sysfs identity is actual evidence. Never map a
            # Windows drive letter to PhysicalDrive0 without querying the OS.
            io_devices = []
            if os.name == "posix":
                stat = path.stat()
                sysfs = Path(f"/sys/dev/block/{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}")
                if sysfs.exists():
                    identity = sysfs.resolve().name
                    if any(device["id"] == identity for device in io_result["devices"]):
                        io_devices.append(identity)
            result["volumes"].append({
                "path": str(path), "volume": partition.mountpoint if partition else path.anchor,
                "device": partition.device if partition else None,
                "volume_identity_available": partition is not None,
                "total_mb": total / _MIB, "free_mb": free / _MIB,
                "io_devices": io_devices, "io_mapping_available": bool(io_devices),
            })
        except _PROBE_ERRORS as exc:
            result["errors"].append(f"Disk measurement failed for {path}: {_error(exc)}")
    result["available"] = len(result["volumes"]) == len(workspaces) and not result["errors"]
    return result


def collect_resources(
    workspaces: Sequence[Path], sample_seconds: float = 0.05, *, nvidia_smi: str = "nvidia-smi",
    cpu_temperature_probe: str | None = None,
) -> dict[str, Any]:
    """Measure actual resources; each unsupported probe reports unavailable.

    CPU and disk I/O share a timed sample (10 ms through five seconds). NVIDIA
    stdout/stderr are each limited to 32 KiB and its process to three seconds.
    A custom NVIDIA executable is a single argument, never shell command text.
    Windows defaults to the native System32 NVIDIA utility and validates the
    executable and all protected ancestors before starting a privileged probe.
    """
    if isinstance(sample_seconds, bool) or not isinstance(sample_seconds, (int, float)) or not math.isfinite(sample_seconds) or not 0.01 <= sample_seconds <= 5:
        raise ValueError("sample_seconds must be a finite number between 0.01 and 5")
    if isinstance(workspaces, (str, bytes, Path)) or not workspaces:
        raise ValueError("workspaces must be a nonempty sequence of filesystem paths")
    if not isinstance(nvidia_smi, str) or not nvidia_smi.strip() or "\x00" in nvidia_smi:
        raise ValueError("nvidia_smi must identify one executable")
    from .cpu_temperature import validate_probe_path
    validate_probe_path(cpu_temperature_probe)
    before = _disk_counters()
    cpu = _cpu(sample_seconds)
    # CPU sampling can fail before waiting; a disk delta still needs real time.
    # CPython 3.12 Windows monotonic can have a ~15.6 ms tick. Use the
    # high-resolution performance counter for these short physical samples.
    remaining = sample_seconds - (time.perf_counter() - before[2])
    while remaining > 0:
        time.sleep(remaining)
        remaining = sample_seconds - (time.perf_counter() - before[2])
    disk_io = _disk_io(before, _disk_counters())
    _cpu_temperature(cpu, cpu_temperature_probe)
    return {
        "measured_at": time.time(), "cpu": cpu, "memory": _memory(),
        "commit": _commit(), "gpus": _gpus(nvidia_smi),
        "disks": _volumes(workspaces, disk_io),
    }
