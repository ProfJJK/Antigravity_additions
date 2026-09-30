"""HDF5 driver-level file-locking bypass protocol and network mount initialization.

Task 20.10811 (WBS 20.108.1.1), Method Matrix v4 section 8C.

HDF5's default file drivers take advisory locks (``flock``/``fcntl`` on POSIX,
``LockFileEx`` on Windows) when a container is opened. On NFS, SMB/CIFS,
Lustre and cloud-synced folders such as Google Drive, OneDrive and Dropbox,
these locks can fail. The result is ``BlockingIOError: [Errno 11]``,
``HDF5-DIAG: file locking failed`` or outright deadlocks.

This module ensures the following:

* ``HDF5_USE_FILE_LOCKING=FALSE`` is placed in the process environment
  **before** ``h5py`` (and therefore ``libhdf5``) is imported by this module.
* The bypass can be re-asserted idempotently at any time
  (:func:`enforce_hdf5_file_locking_bypass`).
* Child processes receive an environment carrying the bypass
  (:func:`get_hdf5_subprocess_env`). A genuine child interpreter can be
  spawned to verify this (:func:`verify_subprocess_inheritance`).
* Paths on network or cloud-synced storage can be classified
  (:func:`is_network_or_cloud_path`, :func:`inspect_mount_locking_support`).
* HDF5 containers can be opened with the bypass enforced on every open
  (:func:`safe_hdf5_open`). Where the linked HDF5/h5py supports it,
  ``locking=False`` is also passed on the file-access property list. This
  covers the case where the environment variable could not reach
  ``libhdf5`` (e.g. h5py imported earlier by another module).
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Architectural Constraint 1: the bypass must be in the environment strictly
# before h5py / libhdf5 is imported by this module.
# ---------------------------------------------------------------------------
_ENV_KEY = "HDF5_USE_FILE_LOCKING"
_BYPASS_VALUE = "FALSE"
_H5PY_PRELOADED_BEFORE_BYPASS: bool = bool(
    getattr(sys, "_cochem_h5py_preloaded", "h5py" in sys.modules)
)
os.environ[_ENV_KEY] = _BYPASS_VALUE

import json  # noqa: E402
import ntpath  # noqa: E402
import subprocess  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from pathlib import PurePath  # noqa: E402
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union  # noqa: E402

import h5py  # noqa: E402

__all__ = [
    "HDF5_FILE_LOCKING_ENV_KEY",
    "HDF5_FILE_LOCKING_BYPASS_VALUE",
    "enforce_hdf5_file_locking_bypass",
    "get_hdf5_subprocess_env",
    "get_hdf5_lock_environment",
    "verify_subprocess_inheritance",
    "is_network_or_cloud_path",
    "inspect_mount_locking_support",
    "safe_hdf5_open",
    "safe_h5py_file",
    "get_hdf5_locking_status",
    "is_hdf5_file_locking_bypassed",
    "h5py_supports_locking_kwarg",
]

HDF5_FILE_LOCKING_ENV_KEY: str = _ENV_KEY
HDF5_FILE_LOCKING_BYPASS_VALUE: str = _BYPASS_VALUE

PathLike = Union[str, PurePath, "os.PathLike[str]"]

# Windows: suppress console windows for spawned children. On POSIX,
# creationflags must be 0 (any other value raises ValueError).
_SUBPROCESS_CREATION_FLAGS: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
)

# Directory-name fragments of cloud-synchronised folders (lower-case).
_CLOUD_SYNC_MARKERS: Tuple[str, ...] = (
    "gdrive",
    "google drive",
    "googledrive",
    "google-drive",
    "my drive",
    "shared drives",
    "onedrive",
    "dropbox",
    "icloud",
    "mobile documents",  # macOS iCloud container root
    "cloudstorage",      # macOS ~/Library/CloudStorage (File Provider mounts)
    "box sync",
    "pcloud",
    "megasync",
    "nextcloud",
    "owncloud",
)

# Linux filesystem types whose advisory locking is unreliable or remote.
_REMOTE_FS_TYPES: Tuple[str, ...] = (
    "nfs",
    "nfs4",
    "cifs",
    "smb",
    "smbfs",
    "smb2",
    "smb3",
    "ncpfs",
    "afs",
    "lustre",
    "gpfs",
    "beegfs",
    "ceph",
    "cephfs",
    "glusterfs",
    "fuse.glusterfs",
    "fuse.sshfs",
    "sshfs",
    "fuse.rclone",
    "fuse.s3fs",
    "fuse.gcsfuse",
    "fuse.google-drive-ocamlfuse",
    "fuse.onedriver",
    "fuse.dropbox",
    "davfs",
    "fuse.davfs2",
    "webdav",
    "orangefs",
    "pvfs2",
    "panfs",
    "wekafs",
)

# Filesystem-type fragments that indicate cloud-backed FUSE mounts.
_CLOUD_FS_FRAGMENTS: Tuple[str, ...] = (
    "rclone",
    "gcsfuse",
    "google-drive",
    "onedriver",
    "dropbox",
    "s3fs",
)

# Windows GetDriveTypeW return codes.
_DRIVE_FIXED = 3
_DRIVE_REMOTE = 4
# Windows volume filesystem names used by cloud virtual drives.
_WINDOWS_CLOUD_FS_NAMES: Tuple[str, ...] = ("drivefs",)
# SetErrorMode flag: do not show critical-error dialogs for inaccessible volumes.
_SEM_FAILCRITICALERRORS = 0x0001


# ---------------------------------------------------------------------------
# Environment enforcement
# ---------------------------------------------------------------------------
def enforce_hdf5_file_locking_bypass(target_val: str = _BYPASS_VALUE) -> bool:
    """Enforce the HDF5 driver file-locking bypass idempotently.

    Sets ``os.environ['HDF5_USE_FILE_LOCKING'] = target_val`` when the
    variable is unset or holds a different value. Once the value is correct,
    repeated calls are no-ops. No other environment variable is touched.

    Args:
        target_val: Value to assert. Defaults to ``'FALSE'``.

    Returns:
        bool: ``True`` if the bypass is active (the variable equals
        ``'FALSE'``) after enforcement. Otherwise ``False``.

    Raises:
        TypeError: If *target_val* is not a ``str``.
    """
    if not isinstance(target_val, str):
        raise TypeError(
            f"target_val must be str, got {type(target_val).__name__}"
        )
    if os.environ.get(_ENV_KEY) != target_val:
        os.environ[_ENV_KEY] = target_val
    return os.environ.get(_ENV_KEY) == _BYPASS_VALUE


def get_hdf5_subprocess_env(
    base_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Produce a child-process environment with ``HDF5_USE_FILE_LOCKING='FALSE'``.

    Args:
        base_env: Optional base mapping. Defaults to a copy of ``os.environ``.
            The mapping passed in is never mutated. Read-only mappings are
            accepted.

    Returns:
        Dict[str, str]: New dictionary containing every entry of the base
        environment, with the bypass value forced.
    """
    if base_env is None:
        env: Dict[str, str] = dict(os.environ)
    else:
        env = {str(k): str(v) for k, v in base_env.items()}
    env[_ENV_KEY] = _BYPASS_VALUE
    return env


# Canonical alias used by the existing storage test-suite.
get_hdf5_lock_environment = get_hdf5_subprocess_env


_INHERITANCE_PROBE_SCRIPT = (
    "import json, os\n"
    "print(json.dumps({'inherited_value': os.environ.get('HDF5_USE_FILE_LOCKING')}))\n"
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def verify_subprocess_inheritance(timeout_seconds: float = 30.0) -> Dict[str, Any]:
    """Spawn a genuine child Python interpreter and verify bypass inheritance.

    First the bypass is asserted in the current process. Then the current
    interpreter (``sys.executable``) is launched with the environment from
    :func:`get_hdf5_subprocess_env`, and the child reports its own view of
    ``HDF5_USE_FILE_LOCKING``.

    Returns:
        Dict with keys ``'verified'`` (bool), ``'exit_code'`` (int),
        ``'inherited_value'`` (str or None), ``'stdout'`` (str) and
        ``'stderr'`` (str).
    """
    enforce_hdf5_file_locking_bypass()
    env = get_hdf5_subprocess_env()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _INHERITANCE_PROBE_SCRIPT],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
            creationflags=_SUBPROCESS_CREATION_FLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "verified": False,
            "exit_code": -1,
            "inherited_value": None,
            "stdout": _as_text(exc.stdout),
            "stderr": _as_text(exc.stderr) + f"\nTimed out after {timeout_seconds} s",
        }

    stdout = proc.stdout or ""
    inherited: Optional[str] = None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        value = payload.get("inherited_value")
        inherited = value if isinstance(value, str) else None
        break

    return {
        "verified": proc.returncode == 0 and inherited == _BYPASS_VALUE,
        "exit_code": int(proc.returncode),
        "inherited_value": inherited,
        "stdout": stdout,
        "stderr": proc.stderr or "",
    }


# ---------------------------------------------------------------------------
# Network / cloud mount classification
# ---------------------------------------------------------------------------
def _path_to_str(path: PathLike) -> str:
    raw = os.fspath(path)
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    return str(raw)


def _strip_win32_namespace(raw: str) -> Tuple[str, bool]:
    """Strip ``\\\\?\\`` / ``\\\\.\\`` prefixes.

    Returns (remaining_path, is_namespaced).
    """
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\?\\") or normalized.startswith("\\\\.\\"):
        return normalized[4:], True
    return raw, False


def _is_unc_path(raw: str) -> bool:
    """True for UNC shares (``\\\\server\\share``, ``//server/share``)."""
    if not raw.startswith(("\\\\", "//")):
        return False
    rest, namespaced = _strip_win32_namespace(raw)
    if namespaced:
        # \\?\UNC\server\share is remote; \\?\C:\... and \\.\PhysicalDrive0 are not.
        return rest.upper().startswith("UNC\\")
    return True


def _has_cloud_sync_marker(raw: str) -> bool:
    segments = [s for s in raw.replace("\\", "/").split("/") if s]
    for segment in segments:
        seg = segment.lower()
        for marker in _CLOUD_SYNC_MARKERS:
            if marker in seg:
                return True
    return False


def _windows_drive_root(raw: str) -> Optional[str]:
    rest, _ = _strip_win32_namespace(raw)
    drive, _tail = ntpath.splitdrive(rest)
    if len(drive) == 2 and drive[1] == ":":
        return drive.upper() + "\\"
    if not drive:
        abs_drive, _tail = ntpath.splitdrive(os.path.abspath(rest))
        if len(abs_drive) == 2 and abs_drive[1] == ":":
            return abs_drive.upper() + "\\"
    return None


def _windows_remote_or_cloud_drive(raw: str) -> Tuple[bool, bool]:
    """Return (is_remote_drive, is_cloud_virtual_drive) on Windows."""
    if os.name != "nt":
        return False, False
    root = _windows_drive_root(raw)
    if root is None:
        return False, False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        drive_type = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        if drive_type == _DRIVE_REMOTE:
            return True, False
        if drive_type != _DRIVE_FIXED:
            # Removable / optical / RAM / nonexistent: not remote, and querying
            # the volume could block on empty media.
            return False, False

        # Google Drive for desktop mounts a fixed "DriveFS" virtual volume.
        fs_name_buf = ctypes.create_unicode_buffer(261)
        previous_mode = kernel32.SetErrorMode(_SEM_FAILCRITICALERRORS)
        try:
            ok = kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root),
                None,
                0,
                None,
                None,
                None,
                fs_name_buf,
                len(fs_name_buf),
            )
        finally:
            kernel32.SetErrorMode(previous_mode)
        is_cloud = bool(ok) and (
            fs_name_buf.value.strip().lower() in _WINDOWS_CLOUD_FS_NAMES
        )
        return False, is_cloud
    except (AttributeError, OSError, ValueError):
        return False, False


def _decode_mount_field(field: str) -> str:
    """Decode the octal escapes (``\\040`` etc.) used in /proc mount tables."""
    out: List[str] = []
    i = 0
    n = len(field)
    while i < n:
        if field[i] == "\\" and i + 4 <= n:
            code = field[i + 1 : i + 4]
            if all(c in "01234567" for c in code):
                out.append(chr(int(code, 8)))
                i += 4
                continue
        out.append(field[i])
        i += 1
    return "".join(out)


def _read_posix_mount_table() -> List[Tuple[str, str]]:
    """Return (mount_point, fs_type) pairs from /proc (Linux). Empty elsewhere."""
    for table in ("/proc/self/mounts", "/proc/mounts"):
        try:
            with open(table, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        entries: List[Tuple[str, str]] = []
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            entries.append((_decode_mount_field(parts[1]), parts[2].lower()))
        return entries
    return []


def _posix_mount_fstype(raw: str) -> Optional[str]:
    if os.name == "nt":
        return None
    entries = _read_posix_mount_table()
    if not entries:
        return None
    target = os.path.realpath(os.path.abspath(raw))
    best_mount = ""
    best_type: Optional[str] = None
    for mount_point, fs_type in entries:
        mp = mount_point.rstrip("/") or "/"
        if mp == "/":
            matches = True
        else:
            matches = target == mp or target.startswith(mp + "/")
        if matches and len(mp) >= len(best_mount):
            best_mount = mp
            best_type = fs_type
    return best_type


def _is_remote_fstype(fs_type: Optional[str]) -> bool:
    if not fs_type:
        return False
    if fs_type in _REMOTE_FS_TYPES:
        return True
    return fs_type.startswith(("nfs", "cifs", "smb"))


def _classify_path(path: PathLike) -> Dict[str, Any]:
    raw = _path_to_str(path)
    is_unc = _is_unc_path(raw)
    is_cloud = _has_cloud_sync_marker(raw)

    is_remote_drive = False
    is_cloud_drive = False
    fs_type: Optional[str] = None
    if not is_unc:
        if os.name == "nt":
            is_remote_drive, is_cloud_drive = _windows_remote_or_cloud_drive(raw)
        else:
            fs_type = _posix_mount_fstype(raw)
            is_remote_drive = _is_remote_fstype(fs_type)
            if fs_type is not None and any(m in fs_type for m in _CLOUD_FS_FRAGMENTS):
                is_cloud_drive = True

    return {
        "path": raw,
        "is_unc": is_unc,
        "is_network_mount": bool(is_unc or is_remote_drive),
        "is_cloud_sync_mount": bool(is_cloud or is_cloud_drive),
        "filesystem_type": fs_type,
    }


def is_network_or_cloud_path(path: PathLike) -> bool:
    """Determine whether *path* is on a network share, a cloud-sync mount or a remote FS.

    The path does not need to exist. The following are identified:
      * UNC network paths beginning with ``//`` or ``\\\\`` (including
        ``\\\\?\\UNC\\``).
      * Cloud-sync folders whose path components contain e.g. ``gdrive``,
        ``google drive``, ``onedrive``, ``dropbox`` or ``icloud``.
      * Windows remote drives (``GetDriveTypeW == DRIVE_REMOTE``) and Google
        Drive virtual volumes (``DriveFS``).
      * Linux remote or parallel filesystems (NFS, CIFS/SMB, Lustre, GPFS,
        sshfs, rclone, ...), detected from ``/proc/self/mounts``.

    Returns:
        bool: ``True`` for remote or cloud-synced storage, ``False`` for
        standard local storage.
    """
    info = _classify_path(path)
    return bool(info["is_network_mount"] or info["is_cloud_sync_mount"])


def inspect_mount_locking_support(path: PathLike) -> Dict[str, Any]:
    """Build the file-locking risk profile of the mount that holds *path*.

    Returns:
        Dict with keys ``'path'``, ``'is_unc'``, ``'is_network_mount'``,
        ``'is_cloud_sync_mount'``, ``'filesystem_type'``, ``'risk_level'``
        (``'CRITICAL_NETWORK_MOUNT'``, ``'HIGH_CLOUD_SYNC_MOUNT'`` or
        ``'LOW'``), ``'bypass_active'`` and ``'locking_safe'``.
    """
    info = _classify_path(path)
    if info["is_network_mount"]:
        risk = "CRITICAL_NETWORK_MOUNT"
    elif info["is_cloud_sync_mount"]:
        risk = "HIGH_CLOUD_SYNC_MOUNT"
    else:
        risk = "LOW"
    bypass_active = os.environ.get(_ENV_KEY) == _BYPASS_VALUE
    info["risk_level"] = risk
    info["bypass_active"] = bypass_active
    info["locking_safe"] = bool(bypass_active or risk == "LOW")
    return info


# ---------------------------------------------------------------------------
# Safe container opener
# ---------------------------------------------------------------------------
def _version_triplet(version_tuple: Any) -> Tuple[int, int, int]:
    parts: List[int] = []
    for item in tuple(version_tuple)[:3]:
        try:
            parts.append(int(item))
        except (TypeError, ValueError):
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def h5py_supports_locking_kwarg() -> bool:
    """Return True if ``h5py.File(..., locking=False)`` works with the linked HDF5.

    This requires h5py >= 3.5 and either HDF5 >= 1.12.1 or HDF5 1.10.x >= 1.10.7.
    """
    try:
        h5py_ver = _version_triplet(h5py.version.version_tuple)
        hdf5_ver = _version_triplet(h5py.version.hdf5_version_tuple)
    except AttributeError:
        return False
    if h5py_ver < (3, 5, 0):
        return False
    if hdf5_ver >= (1, 12, 1):
        return True
    return (1, 10, 7) <= hdf5_ver < (1, 11, 0)


_LOCKING_KWARG_SUPPORTED: bool = h5py_supports_locking_kwarg()


@contextmanager
def safe_hdf5_open(
    file_path: PathLike,
    mode: str = "r",
    **kwargs: Any,
) -> Iterator[h5py.File]:
    """Open an ``h5py.File`` with the file-locking bypass enforced.

    ``HDF5_USE_FILE_LOCKING=FALSE`` is re-asserted just before the open. If the
    linked library supports it and the caller did not pass ``locking``,
    ``locking=False`` is also set on the file-access property list. Other
    keyword arguments are forwarded to :class:`h5py.File` unchanged. The
    handle is always closed, including when an exception is raised, and the
    exception is re-raised rather than swallowed.
    """
    enforce_hdf5_file_locking_bypass()
    open_kwargs: Dict[str, Any] = dict(kwargs)
    if _LOCKING_KWARG_SUPPORTED and "locking" not in open_kwargs:
        open_kwargs["locking"] = False
    handle = h5py.File(os.fspath(file_path), mode, **open_kwargs)
    try:
        yield handle
    finally:
        if handle.id.valid:
            handle.close()


# Canonical alias used by the existing storage test fixtures.
safe_h5py_file = safe_hdf5_open


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
def get_hdf5_locking_status() -> Dict[str, Any]:
    """Return live runtime telemetry about the HDF5 file-locking state.

    Returns:
        Dict with keys:
          * ``'env_value'``: current ``HDF5_USE_FILE_LOCKING`` value, or None.
          * ``'is_bypassed'``: True when ``env_value == 'FALSE'``.
          * ``'h5py_imported'``: ``'h5py' in sys.modules``.
          * ``'h5py_preloaded_before_bypass'``: whether h5py was imported
            before the bypass variable was first set.
          * ``'locking_kwarg_supported'``: whether ``locking=False`` is
            applied on open.
          * ``'h5py_version'`` / ``'hdf5_version'``: linked library versions.
    """
    env_value = os.environ.get(_ENV_KEY)
    h5py_mod = sys.modules.get("h5py")
    h5py_version: Optional[str] = None
    hdf5_version: Optional[str] = None
    if h5py_mod is not None:
        version_mod = getattr(h5py_mod, "version", None)
        raw_h5py_version = getattr(version_mod, "version", None)
        raw_hdf5_version = getattr(version_mod, "hdf5_version", None)
        h5py_version = None if raw_h5py_version is None else str(raw_h5py_version)
        hdf5_version = None if raw_hdf5_version is None else str(raw_hdf5_version)
    return {
        "env_value": env_value,
        "is_bypassed": env_value == _BYPASS_VALUE,
        "h5py_imported": h5py_mod is not None,
        "h5py_preloaded_before_bypass": _H5PY_PRELOADED_BEFORE_BYPASS,
        "locking_kwarg_supported": _LOCKING_KWARG_SUPPORTED,
        "h5py_version": h5py_version,
        "hdf5_version": hdf5_version,
    }


def is_hdf5_file_locking_bypassed() -> bool:
    """Shorthand for ``bool(get_hdf5_locking_status()['is_bypassed'])``."""
    return bool(get_hdf5_locking_status()["is_bypassed"])
