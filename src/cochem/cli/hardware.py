"""Hardware auditing and environment verification for CoChem."""

import ctypes
import dataclasses
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

_GIB = 1024.0**3
_BYTES_PER_KIB = 1024

# (total bytes, free bytes or None when the free figure is unavailable)
_MemoryReading = Optional[Tuple[float, Optional[float]]]


def _creation_flags() -> int:
    """Return CREATE_NO_WINDOW on Windows so probes never spawn console windows."""
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run(
    command: Sequence[str], timeout: float = 5.0
) -> Optional["subprocess.CompletedProcess[str]"]:
    """Run a probe command windowlessly; return None if it cannot be executed."""
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creation_flags(),
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


@dataclass(frozen=True)
class HardwareProfile:
    """Snapshot of host system hardware specifications.

    A RAM figure of 0.0 means the value could not be measured; the reason is
    recorded in ``probe_notes`` rather than substituting a made-up number.
    """

    cpu_count: int
    ram_total_gb: float
    ram_free_gb: float
    disk_total_gb: float
    disk_free_gb: float
    accelerators: List[Dict[str, Any]]
    platform: str
    probe_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable representation."""
        return dataclasses.asdict(self)


def _memory_from_windows() -> _MemoryReading:
    if sys.platform != "win32":
        return None

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None
    if not ok or status.ullTotalPhys <= 0:
        return None
    return float(status.ullTotalPhys), float(status.ullAvailPhys)


def _memory_from_proc_meminfo() -> _MemoryReading:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    values: Dict[str, int] = {}
    for match in re.finditer(r"^(\w+):\s+(\d+)", text, re.MULTILINE):
        values[match.group(1)] = int(match.group(2)) * _BYTES_PER_KIB
    total = values.get("MemTotal")
    if not total:
        return None
    free = values.get("MemAvailable")
    if free is None:
        free = (
            values.get("MemFree", 0)
            + values.get("Buffers", 0)
            + values.get("Cached", 0)
        )
    return float(total), float(free)


def _memory_from_darwin() -> _MemoryReading:
    res = _run(["sysctl", "-n", "hw.memsize"])
    if res is None or res.returncode != 0:
        return None
    try:
        total = float(int(res.stdout.strip()))
    except ValueError:
        return None
    if total <= 0:
        return None

    free: Optional[float] = None
    vm = _run(["vm_stat"])
    if vm is not None and vm.returncode == 0:
        page_match = re.search(r"page size of (\d+) bytes", vm.stdout)
        counts = [
            int(m.group(2))
            for m in re.finditer(
                r"^Pages (free|inactive|speculative):\s+(\d+)",
                vm.stdout,
                re.MULTILINE,
            )
        ]
        if page_match and counts:
            free = float(int(page_match.group(1)) * sum(counts))
    return total, free


def _memory_from_sysconf() -> _MemoryReading:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None
    if page_size <= 0 or total_pages <= 0:
        return None
    free: Optional[float] = None
    try:
        free_pages = os.sysconf("SC_AVPHYS_PAGES")
        if free_pages >= 0:
            free = float(free_pages * page_size)
    except (ValueError, OSError):
        free = None
    return float(total_pages * page_size), free


def _probe_memory() -> Tuple[float, float, List[str]]:
    """Return (total GiB, free GiB, notes) using native, platform-specific probes."""
    probes: List[Tuple[str, Callable[[], _MemoryReading]]]
    if sys.platform == "win32":
        probes = [("GlobalMemoryStatusEx", _memory_from_windows)]
    elif sys.platform == "darwin":
        probes = [
            ("sysctl/vm_stat", _memory_from_darwin),
            ("sysconf", _memory_from_sysconf),
        ]
    else:
        probes = [
            ("/proc/meminfo", _memory_from_proc_meminfo),
            ("sysconf", _memory_from_sysconf),
        ]

    notes: List[str] = []
    for label, probe in probes:
        reading = probe()
        if reading is None:
            notes.append(f"RAM probe '{label}' unavailable")
            continue
        total, free = reading
        if free is None:
            notes.append(f"free RAM unavailable from probe '{label}'")
            free = 0.0
        return total / _GIB, min(free, total) / _GIB, notes

    notes.append("RAM metrics could not be determined on this host")
    return 0.0, 0.0, notes


def _probe_accelerators() -> List[Dict[str, Any]]:
    accelerators: List[Dict[str, Any]] = []

    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        res = _run(
            [nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader"]
        )
        if res is not None and res.returncode == 0 and res.stdout:
            for line in res.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if parts:
                    accelerators.append(
                        {
                            "type": "cuda",
                            "name": parts[0],
                            "memory": parts[1] if len(parts) > 1 else "Unknown",
                        }
                    )

    if sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        accelerators.append(
            {"type": "metal", "name": "Apple Silicon GPU", "memory": "unified"}
        )

    return accelerators


class HardwareAuditor:
    """Probes system hardware resources across Windows, Linux, and macOS."""

    @classmethod
    def probe(
        cls, target_path: Optional[Union[str, Path, os.PathLike]] = None
    ) -> HardwareProfile:
        """Probe CPU, RAM, disk space at target path, and accelerator devices."""
        probe_dir = Path.cwd() if target_path is None else Path(target_path).resolve()
        while not probe_dir.exists() and probe_dir.parent != probe_dir:
            probe_dir = probe_dir.parent

        notes: List[str] = []
        try:
            usage = shutil.disk_usage(probe_dir)
            disk_total_gb = float(usage.total) / _GIB
            disk_free_gb = float(usage.free) / _GIB
        except OSError as err:
            notes.append(f"disk probe failed for {probe_dir}: {err}")
            disk_total_gb = 0.0
            disk_free_gb = 0.0

        ram_total_gb, ram_free_gb, ram_notes = _probe_memory()
        notes.extend(ram_notes)

        return HardwareProfile(
            cpu_count=os.cpu_count() or 1,
            ram_total_gb=ram_total_gb,
            ram_free_gb=ram_free_gb,
            disk_total_gb=disk_total_gb,
            disk_free_gb=disk_free_gb,
            accelerators=_probe_accelerators(),
            platform=sys.platform,
            probe_notes=notes,
        )


@dataclass(frozen=True)
class BinaryAuditRecord:
    """Record of an external chemistry binary's availability and version."""

    name: str
    available: bool
    path: Optional[str]
    version: Optional[str]


class EnvironmentVerifier:
    """Verifies runtime Python environment and audits external chemistry binaries."""

    @staticmethod
    def verify_python_version(min_version: Tuple[int, int] = (3, 10)) -> bool:
        """Verify that the current Python runtime meets or exceeds min_version."""
        return sys.version_info >= min_version

    @staticmethod
    def audit_binaries(
        binaries: Sequence[str] = ("orca", "crest", "xtb", "obabel")
    ) -> Dict[str, BinaryAuditRecord]:
        """Audit PATH for external quantum chemistry and cheminformatics binaries."""
        records: Dict[str, BinaryAuditRecord] = {}

        for name in binaries:
            bin_path = shutil.which(name)
            if not bin_path:
                records[name] = BinaryAuditRecord(
                    name=name, available=False, path=None, version=None
                )
                continue

            resolved_path = str(Path(bin_path).resolve())
            version_str: Optional[str] = None

            for flag in ("--version", "-v"):
                res = _run([resolved_path, flag])
                if res is None:
                    continue
                output = ((res.stdout or "") + " " + (res.stderr or "")).strip()
                if output:
                    first_line = output.splitlines()[0].strip()
                    if first_line:
                        version_str = first_line
                        break

            records[name] = BinaryAuditRecord(
                name=name,
                available=True,
                path=resolved_path,
                version=version_str,
            )

        return records
