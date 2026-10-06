"""Verified, bounded ImDisk workspaces; persistent authentication stays on disk.

Only the controller can create a workspace descriptor. Every native launch
rechecks the mount's reparse data and ImDisk kernel flags, rather than trusting a
drive label, a directory name, or a successful command. The default AWE backing
uses physical, nonpageable RAM; ordinary ImDisk ``vm`` backing is explicitly
reported as pageable. No provider home, credentials, prompt or private ledger
is copied into this volume.
"""
from __future__ import annotations

import argparse
import ctypes as C
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import struct
import subprocess
import time
import uuid
from typing import Mapping


DEFAULT_MOUNT = r"D:\__CoChem\__agentic\.scripts\tdd_runs"
_SLOT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))}
_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_FSCTL_GET_REPARSE_POINT = 0x900A8
# LTRData/ImDisk inc/imdisk.h: FILE_DEVICE_IMDISK=0x8372, function 0x802,
# METHOD_BUFFERED, access=0. This is an actual driver query, not a heuristic.
_IOCTL_IMDISK_QUERY_DEVICE = (0x8372 << 16) | (0x802 << 2)
_MAX_REPARSE = 16384
_NOT_CONTENT_INDEXED = 0x2000


class RamdiskError(RuntimeError):
    """Required RAM storage could not be proved safe and available."""
    category = "configuration"


class RamdiskCapacityError(RamdiskError):
    """A real capacity wait; it must not consume ordinary coding failures."""
    category = "resource"


@dataclass(frozen=True)
class RamdiskConfig:
    enabled: bool = False
    mount_root: str = DEFAULT_MOUNT
    size_mb: int = 8192
    min_free_mb: int = 512
    reserve_host_memory_mb: int = 4096
    backing: str = "awe"
    imdisk_executable: str = r"C:\Windows\System32\imdisk.exe"
    claude_project_cache: bool = True

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.claude_project_cache) is not bool:
            raise ValueError("RAM disk enable/cache settings must be booleans")
        path = PureWindowsPath(self.mount_root)
        if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
                or len(path.parts) < 4 or any(part in {"..", "."} for part in path.parts)
                or any(any(char in part for char in ':*?"<>|') for part in path.parts[1:])
                or any(c in self.mount_root for c in ("\x00", "\r", "\n"))):
            raise ValueError("RAM disk requires a dedicated local NTFS directory mount; drive letters/UNC roots are forbidden")
        executable = PureWindowsPath(self.imdisk_executable)
        if (not executable.is_absolute() or executable.name.casefold() != "imdisk.exe"
                or any(c in self.imdisk_executable for c in ("\x00", "\r", "\n"))):
            raise ValueError("RAM disk requires an absolute protected imdisk.exe path")
        for name, minimum, maximum in (("size_mb", 256, 1048576), ("min_free_mb", 16, 1048576),
                                        ("reserve_host_memory_mb", 256, 1048576)):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"ramdisk.{name} must be an integer between {minimum} and {maximum}")
        if self.min_free_mb >= self.size_mb:
            raise ValueError("RAM disk free-space reserve must be smaller than capacity")
        if self.backing not in {"awe", "vm"}:
            raise ValueError("RAM disk backing must be awe (physical RAM) or vm (pageable memory)")

    @classmethod
    def from_dict(cls, value: Mapping | None = None):
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("ramdisk must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("Unknown ramdisk settings: " + ", ".join(sorted(unknown)))
        return cls(**dict(value))

    def as_dict(self):
        return asdict(self)


def ordinary_tree(path: Path, *, allow_missing: bool = False) -> None:
    """Reject links/reparse points before touching an existing physical tree."""
    for entry in (path, *path.parents):
        try:
            metadata = entry.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise RamdiskError(f"Required physical path is missing: {entry}") from None
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise RamdiskError(f"Unexpected reparse point or symlink: {entry}")


def backup_mount_contents(mount: Path, backup_parent: Path) -> Path | None:
    """Preserve a nonempty physical mount directory by atomic same-volume rename.

    No recursive copying or deletion follows worker-controlled links. A crash
    leaves either the original directory or its named backup intact. This
    helper is deliberately usable by portable filesystem tests; the Windows
    caller separately proves SYSTEM ownership and safe ancestors first.
    """
    ordinary_tree(mount)
    ordinary_tree(backup_parent)
    if not mount.is_dir() or not backup_parent.is_dir():
        raise RamdiskError("Mount and backup parent must be existing directories")
    if mount == backup_parent or mount in backup_parent.parents:
        raise RamdiskError("The backup must be outside the mount directory")
    if mount.stat().st_dev != backup_parent.stat().st_dev:
        raise RamdiskError("Mount backup requires an atomic same-volume rename")
    if not any(mount.iterdir()):
        return None
    backup = backup_parent / (mount.name + ".before-ramdisk-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex)
    mount.rename(backup)
    mount.mkdir(mode=0o700)
    return backup


def parse_mount_reparse(data: bytes) -> str:
    target = _parse_reparse_target(data)
    if not re.fullmatch(r"\\Device\\ImDisk[0-9]{1,8}", target, re.IGNORECASE):
        raise RamdiskError("Mount target is not an exact ImDisk device")
    return target


def _parse_reparse_target(data: bytes) -> str:
    if len(data) < 16:
        raise RamdiskError("Truncated mount reparse metadata")
    tag, length, reserved, offset, size, print_offset, print_size = struct.unpack_from("<IHHHHHH", data)
    if (tag != _REPARSE_TAG_MOUNT_POINT or reserved != 0 or length + 8 > len(data)
            or length < 8 or offset % 2 or size % 2 or size == 0
            or 16 + offset + size > length + 8):
        raise RamdiskError("Not a bounded NTFS volume mount point")
    try:
        target = data[16 + offset:16 + offset + size].decode("utf-16-le").rstrip("\x00\\")
    except UnicodeDecodeError as exc:
        raise RamdiskError("Invalid mount target encoding") from exc
    return target


def cache_junction_data(target: str) -> bytes:
    path = PureWindowsPath(target)
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or any(part == ".." or any(char in part for char in ':*?"<>|\x00') for part in path.parts[1:])):
        raise RamdiskError("Cache junction must target an exact local RAM directory")
    substitute = ("\\??\\" + str(path)).encode("utf-16-le")
    printed = str(path).encode("utf-16-le")
    payload = substitute + b"\0\0" + printed + b"\0\0"
    if len(payload) + 16 > _MAX_REPARSE:
        raise RamdiskError("Cache junction target is too long")
    return struct.pack("<IHHHHHH", _REPARSE_TAG_MOUNT_POINT, len(payload) + 8, 0,
                       0, len(substitute), len(substitute) + 2, len(printed)) + payload


def validate_cache_junction(data: bytes, target: str) -> None:
    # A caller cannot use this to authorize a broad reparse exception: the
    # exact expected target is derived from its already-verified RAM slot.
    expected = _parse_reparse_target(cache_junction_data(target))
    if _parse_reparse_target(data).casefold() != expected.casefold():
        raise RamdiskError("Native project cache junction points outside its own RAM slot")


def validate_native_cache_selection(native: dict, source: str, target: str) -> None:
    if not isinstance(native, dict):
        raise RamdiskError("Native Claude auth probe did not return cache-path evidence")
    normalize = lambda value: str(PureWindowsPath(value)).casefold() if isinstance(value, str) else None
    if (normalize(native.get("projectsDirectory")) not in {normalize(source), normalize(target)}
            or normalize(native.get("configDirectory")) != normalize(str(PureWindowsPath(source).parent))):
        raise RamdiskError("Installed Claude CLI does not confirm the protected project-cache binding; compatibility update required")


def parse_imdisk_device(data: bytes, config: RamdiskConfig, *, target: str) -> dict:
    if len(data) < 48:
        raise RamdiskError("Truncated ImDisk device query")
    number = struct.unpack_from("<I", data)[0]
    size = struct.unpack_from("<q", data, 8)[0]
    image_offset, flags, drive, filename_length = struct.unpack_from("<qIHH", data, 32)
    if 48 + filename_length > len(data) or filename_length % 2:
        raise RamdiskError("Invalid ImDisk backing-store metadata")
    filename = data[48:48 + filename_length].decode("utf-16-le")
    if number != int(re.search(r"[0-9]+$", target)[0]):
        raise RamdiskError("ImDisk query device does not match mount target")
    physical = flags & 0xF00 == 0x100 and flags & 0xF000 == 0x1000
    virtual = flags & 0xF00 == 0x200
    if not ((config.backing == "awe" and physical) or (config.backing == "vm" and virtual)):
        raise RamdiskError("ImDisk device is not the configured RAM backing type")
    if physical and filename.casefold() not in {"", r"\device\awealloc"}:
        raise RamdiskError("Unexpected AWE backing-store name")
    if virtual and filename:
        raise RamdiskError("RAM disk must not load or persist a disk image")
    if image_offset != 0 or drive != 0 or size != config.size_mb * 1024 * 1024 or flags & 1:
        raise RamdiskError("RAM disk size, write access or drive-letter policy does not match configuration")
    return {"device_number": number, "target": target, "size_bytes": size,
            "backing": config.backing, "nonpageable": physical, "flags": flags}


def imdisk_create_argv(config: RamdiskConfig) -> list[str]:
    backing = ["-t", "file", "-o", "awe"] if config.backing == "awe" else ["-t", "vm"]
    return [config.imdisk_executable, "-a", *backing, "-s", str(config.size_mb * 1024 * 1024),
            "-m", config.mount_root, "-p", "/fs:ntfs /q /y /v:CoChemRAM"]


def mount_recovery_action(prior: dict | None, config: RamdiskConfig, boot_id: int,
                          target: str | None) -> str:
    """Pure lifecycle decision; absence of a volume is never proof of reboot."""
    if type(boot_id) is not int or boot_id <= 0:
        raise RamdiskError("A real native boot identity is required for RAM recovery")
    if prior is not None:
        if (not isinstance(prior, dict) or prior.get("schema") != 1
                or prior.get("state") not in {"PREPARING", "READY"}
                or prior.get("mount_root") != str(Path(config.mount_root))
                or prior.get("config") != config.as_dict()
                or type(prior.get("boot_id")) is not int or prior["boot_id"] <= 0):
            raise RamdiskError("Existing RAM lifecycle ledger differs from reviewed configuration")
        if prior["state"] == "READY":
            observed = prior.get("observed")
            if (not isinstance(observed, dict) or not isinstance(observed.get("target"), str)
                    or not re.fullmatch(r"\\Device\\ImDisk[0-9]{1,8}", observed["target"])
                    or type(observed.get("device_number")) is not int
                    or type(observed.get("volume_serial")) is not int):
                raise RamdiskError("READY RAM ledger lacks native volume identity")
    if target is None:
        return "CREATE"
    if prior is None:
        raise RamdiskError("Existing RAM mount has no private ownership ledger; do not adopt an unknown mount")
    if target != prior.get("observed", {}).get("target", target):
        raise RamdiskError("Recorded mount target changed outside the RAM lifecycle")
    return "VERIFY" if prior["boot_id"] == boot_id else "RECREATE"


def _native_reparse(path: str | Path) -> bytes:
    from . import windows as win
    win.require_system()
    api = win._api()["kernel32"]
    ioctl = api.DeviceIoControl
    ioctl.restype, ioctl.argtypes = win.BOOL, [win.HANDLE, win.DWORD, win.HANDLE, win.DWORD,
                                            win.HANDLE, win.DWORD, C.POINTER(win.DWORD), win.HANDLE]
    # OPEN_REPARSE_POINT means inspect the link itself, never follow an arbitrary
    # junction supplied by a worker or left over from a previous installation.
    handle = api.CreateFileW(str(path), 0, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle == C.c_void_p(-1).value:
        raise RamdiskError("Cannot open the exact RAM mount for native verification")
    try:
        buffer, count = C.create_string_buffer(_MAX_REPARSE), win.DWORD()
        win._check(ioctl(handle, _FSCTL_GET_REPARSE_POINT, None, 0, buffer, len(buffer), C.byref(count), None),
                   "Read RAM mount reparse metadata")
        return buffer.raw[:count.value]
    finally:
        win._close(handle)


def _mount_target(config: RamdiskConfig) -> str:
    return parse_mount_reparse(_native_reparse(config.mount_root))


def _create_cache_junction(source: Path, target: Path) -> None:
    from . import windows as win
    ordinary_tree(source)
    if any(source.iterdir()):
        raise RamdiskError("Only an empty backed-up cache directory may become a RAM junction")
    api = win._api()["kernel32"]
    handle = api.CreateFileW(str(source), 0x40000000, 0, None, 3, 0x02000000 | 0x00200000, None)
    if handle == C.c_void_p(-1).value:
        raise RamdiskError("Cannot exclusively prepare the native project cache junction")
    try:
        payload = cache_junction_data(str(target))
        buffer, count = C.create_string_buffer(payload), win.DWORD()
        ioctl = api.DeviceIoControl
        ioctl.restype, ioctl.argtypes = win.BOOL, [win.HANDLE, win.DWORD, win.HANDLE, win.DWORD,
            win.HANDLE, win.DWORD, C.POINTER(win.DWORD), win.HANDLE]
        win._check(ioctl(handle, 0x900A4, buffer, len(payload), None, 0, C.byref(count), None),
                   "Bind only the native Claude projects cache into its own RAM slot")
    finally:
        win._close(handle)
    validate_cache_junction(_native_reparse(source), str(target))


def _remove_stale_mount(config: RamdiskConfig, expected_target: str) -> None:
    """Remove only a verified old-boot mount link, never its target volume."""
    from . import windows as win
    if _mount_target(config) != expected_target:
        raise RamdiskError("Stale RAM mount changed during recovery")
    api = win._api()["kernel32"]
    handle = api.CreateFileW(config.mount_root, 0x40000000, 0, None, 3, 0x02000000 | 0x00200000, None)
    if handle == C.c_void_p(-1).value:
        raise RamdiskError("Cannot exclusively remove the old-boot RAM mount link")
    try:
        buffer = C.create_string_buffer(struct.pack("<IHH", _REPARSE_TAG_MOUNT_POINT, 0, 0))
        count = win.DWORD()
        win._check(api.DeviceIoControl(handle, 0x900AC, buffer, 8, None, 0, C.byref(count), None),
                   "Remove only the recorded ephemeral RAM mount after reboot")
    finally:
        win._close(handle)


def _native_volume(config: RamdiskConfig) -> dict:
    from . import windows as win
    target = _mount_target(config)
    api = win._api()["kernel32"]
    ioctl = api.DeviceIoControl
    get_volume = api.GetVolumeInformationW
    get_volume.restype, get_volume.argtypes = win.BOOL, [win.LPWSTR, win.LPWSTR, win.DWORD,
        C.POINTER(win.DWORD), C.POINTER(win.DWORD), C.POINTER(win.DWORD), win.LPWSTR, win.DWORD]
    handle = api.CreateFileW("\\\\?\\GLOBALROOT" + target, 0, 3, None, 3, 0, None)
    if handle == C.c_void_p(-1).value:
        raise RamdiskError("Cannot query the mounted ImDisk driver device")
    try:
        buffer, count = C.create_string_buffer(65536), win.DWORD()
        win._check(ioctl(handle, _IOCTL_IMDISK_QUERY_DEVICE, None, 0, buffer, len(buffer), C.byref(count), None),
                   "Prove ImDisk RAM backing through its kernel device")
        observed = parse_imdisk_device(buffer.raw[:count.value], config, target=target)
    finally:
        win._close(handle)
    filesystem = C.create_unicode_buffer(32)
    serial, component, flags = win.DWORD(), win.DWORD(), win.DWORD()
    win._check(get_volume(config.mount_root.rstrip("\\") + "\\", None, 0, C.byref(serial),
                          C.byref(component), C.byref(flags), filesystem, len(filesystem)), "Read RAM filesystem")
    if filesystem.value != "NTFS":
        raise RamdiskError("RAM workspace requires actual NTFS ACL enforcement")
    return {**observed, "filesystem": filesystem.value, "volume_serial": serial.value}


def _no_content_index(path: Path, *, apply: bool = False) -> None:
    """Windows Search honors this native NTFS directory/file attribute."""
    from . import windows as win
    api = win._api()["kernel32"]
    attributes = api.GetFileAttributesW(str(path))
    if attributes == 0xFFFFFFFF:
        raise RamdiskError("Cannot inspect the native SearchIndexer exclusion attribute")
    if apply and not attributes & _NOT_CONTENT_INDEXED:
        win._check(api.SetFileAttributesW(str(path), attributes | _NOT_CONTENT_INDEXED),
                   "Exclude ephemeral RAM workspace from Windows content indexing")
        attributes = api.GetFileAttributesW(str(path))
    if attributes == 0xFFFFFFFF or not attributes & _NOT_CONTENT_INDEXED:
        raise RamdiskError("RAM workspace is not excluded from Windows content indexing")


def exclude_tree_from_indexing(root: Path, *, maximum_entries: int = 250000) -> int:
    """Mark a closed worker's exact tree without following nested reparse links.

    The caller holds the native identity reservation before a new child exists.
    New files are beneath an already excluded directory; the next launch also
    propagates the attribute to any materialized project and cache files.
    """
    count, stack = 0, [root]
    while stack:
        path = stack.pop()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise RamdiskError("Cannot mark a worker-created reparse tree for content indexing")
        _no_content_index(path, apply=True)
        count += 1
        if count > maximum_entries:
            raise RamdiskError("RAM indexing exclusion exceeded the bounded project inventory")
        if stat.S_ISDIR(metadata.st_mode):
            stack.extend(path.iterdir())
    return count


@dataclass(frozen=True)
class RamWorkspace:
    """Controller descriptor; native checks remain mandatory on every use."""
    config: RamdiskConfig
    slot: str
    identity: str
    volume_serial: int
    device_number: int
    private_root: Path

    @property
    def root(self) -> Path:
        return Path(self.config.mount_root) / self.slot

    @property
    def scratch(self) -> Path:
        return self.root / ".cochem-scratch"

    def validate(self, *, identity: str, cwd: Path | None = None, require_capacity: bool = True) -> dict:
        """Verify identity and containment; cleanup may bypass only free space.

        A full but verified RAM slot must remain deletable. Mount identity,
        reparse restrictions, ownership and ACL checks always apply.
        """
        if type(require_capacity) is not bool:
            raise ValueError('RAM capacity verification flag must be boolean')
        from . import windows as win
        win.require_system()
        if identity != self.identity or not _SLOT.fullmatch(self.slot):
            raise RamdiskError("RAM workspace belongs to a different worker identity")
        observed = _native_volume(self.config)
        if (observed["volume_serial"], observed["device_number"]) != (self.volume_serial, self.device_number):
            raise RamdiskError("RAM disk was replaced after this workspace was admitted")
        mount = Path(self.config.mount_root)
        ordinary_tree(mount.parent)
        win._validate_control_ancestors(mount.parent)
        owner, protected, rules = win._acl(mount)
        if (owner != win.SYSTEM_SID or not protected or not rules
                or any((sid in {win.SYSTEM_SID, win.ADMIN_SID} and (mask != win.FULL_CONTROL or flags != 3))
                       or (sid not in {win.SYSTEM_SID, win.ADMIN_SID} and (mask != 0x100020 or flags != 0))
                       for sid, mask, flags in rules)):
            raise RamdiskError("RAM volume ACL permits untrusted replacement or sibling access")
        # This sole mount reparse is allowed. Every descendant remains an
        # ordinary directory, checked without Path.resolve() changing its name.
        current = Path(os.path.abspath(cwd or self.root))
        if current != self.root and self.root not in current.parents:
            raise RamdiskError("Execution directory escaped its assigned RAM slot")
        while current != mount:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise RamdiskError("Worker-created links are forbidden within the RAM execution path")
            current = current.parent
        sid = win._sid_text(win._account_sid(identity))
        win._validate_worker_directory(self.root, sid, protected=True)
        _no_content_index(self.root)
        if cwd is not None:
            win._validate_worker_directory(Path(cwd), sid)
        if require_capacity and shutil.disk_usage(mount).free < self.config.min_free_mb * 1024 * 1024:
            raise RamdiskCapacityError("RAM workspace has reached its configured free-space reserve")
        return observed

    def environment(self) -> dict[str, str]:
        self.validate(identity=self.identity)
        # These documented tool caches hold rebuildable data, not CLI login
        # state. Provider HOME/CODEX_HOME/CLAUDE_CONFIG_DIR are never overridden.
        directories = {"TEMP": "temp", "TMP": "temp", "PIP_CACHE_DIR": "pip",
                       "UV_CACHE_DIR": "uv", "NPM_CONFIG_CACHE": "npm",
                       "PYTHONPYCACHEPREFIX": "pycache"}
        for path in (self.scratch, *(self.scratch / name for name in set(directories.values()))):
            if not path.exists() and not path.is_symlink():
                path.mkdir()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise RamdiskError("RAM scratch cache must not contain a reparse point")
            _no_content_index(path, apply=True)
        return {key: str(self.scratch / name) for key, name in directories.items()}

    @property
    def _cache_report_path(self) -> Path:
        return self.private_root / ("ramdisk-cache-" + self.slot + ".json")

    def _save_cache_report(self, report: dict) -> None:
        from . import windows as win
        ordinary_tree(self.private_root)
        win.validate_private_directory(self.private_root)
        temporary = self.private_root / (".ram-cache-" + uuid.uuid4().hex)
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._cache_report_path)

    def cache_binding_report(self) -> dict:
        if not self.config.claude_project_cache:
            return {"enabled": False}
        from . import windows as win
        path = self._cache_report_path
        ordinary_tree(path)
        win.validate_private_path(path)
        if path.stat().st_size > 1048576 or path.stat().st_nlink != 1:
            raise RamdiskError("Invalid private native-cache binding record")
        result = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("worker") != self.identity
                or result.get("slot") != self.slot or result.get("volume_serial") != self.volume_serial
                or result.get("target") != str(self.scratch / "claude-projects")):
            raise RamdiskError("Native cache evidence does not belong to this RAM workspace")
        return result

    def bind_native_caches(self, profile: Path) -> dict:
        """Bind the observed Claude project-cache child, never its auth home.

        Called with the native profile loaded and its identity mutex held,
        before creating any worker process. Only ``.claude/projects`` is
        eligible: the native 2.1.291 auth-status contract names that directory.
        The subsequent auth probe must independently confirm the installed
        CLI selects this path. No undocumented environment variable is used.
        """
        if not self.config.claude_project_cache:
            return {"enabled": False}
        from . import windows as win
        self.validate(identity=self.identity)
        ordinary_tree(profile)
        parent = profile / ".claude"
        if not parent.exists():
            parent.mkdir()
        ordinary_tree(parent)
        sid = win._sid_text(win._account_sid(self.identity))
        # Existing native profile ACLs remain untouched. Reject another
        # principal's replaceable config directory before SYSTEM acts in it.
        owner, _, rules = win._acl(parent)
        trusted = {sid, win.SYSTEM_SID, win.ADMIN_SID}
        if owner not in trusted or any(account not in trusted and not flags & 8 and mask & 0x500D0116
                                       for account, mask, flags in rules):
            raise RamdiskError("Native cache parent is writable by an unrelated principal")
        self.environment()  # Creates and checks ordinary RAM scratch parents.
        target = self.scratch / "claude-projects"
        if not target.exists() and not target.is_symlink():
            target.mkdir()
        target_metadata = target.lstat()
        if stat.S_ISLNK(target_metadata.st_mode) or getattr(target_metadata, "st_file_attributes", 0) & 0x400:
            raise RamdiskError("Native cache RAM target must be an ordinary directory")
        _no_content_index(target, apply=True)
        source = parent / "projects"
        backup = None
        try:
            metadata = source.lstat()
        except FileNotFoundError:
            source.mkdir()
            metadata = source.lstat()
        if getattr(metadata, "st_file_attributes", 0) & 0x400:
            validate_cache_junction(_native_reparse(source), str(target))
        else:
            ordinary_tree(source)
            if not source.is_dir():
                raise RamdiskError("Native projects cache must be a directory")
            backup = backup_mount_contents(source, parent)
            win._set_acl(source, sid)
            _create_cache_junction(source, target)
        # A real write through the persistent alias must arrive on the exact
        # RAM target. This proves the OS binding; it does not invent CLI writes.
        name = ".cochem-binding-check-" + uuid.uuid4().hex
        payload = os.urandom(64)
        try:
            with (source / name).open("xb") as stream:
                stream.write(payload)
            if (target / name).read_bytes() != payload:
                raise RamdiskError("Native project cache write did not reach RAM")
        finally:
            (source / name).unlink(missing_ok=True)
        previous = None
        if self._cache_report_path.exists():
            try:
                previous = self.cache_binding_report()
            except RamdiskError:
                # An old boot/volume's evidence cannot attest this new binding.
                previous = None
        report = {"schema": 1, "enabled": True, "worker": self.identity, "slot": self.slot,
                  "source": str(source), "target": str(target), "volume_serial": self.volume_serial,
                  "device_number": self.device_number, "backing": self.config.backing,
                  "physical_ram_write_verified": True, "checked_at": time.time(),
                  "backup": str(backup) if backup else (previous or {}).get("backup"),
                  "native_report_verified": bool(previous and previous.get("source") == str(source)
                                                  and previous.get("native_report_verified")),
                  "auth_directory_relocated": False}
        self._save_cache_report(report)
        return report

    def validate_native_cache_report(self, native: dict) -> None:
        if not self.config.claude_project_cache:
            return
        report = self.cache_binding_report()
        validate_native_cache_selection(native, report["source"], report["target"])
        validate_cache_junction(_native_reparse(Path(report["source"])), report["target"])
        report.update(native_report_verified=True, native_projects_directory=native["projectsDirectory"],
                      native_config_directory=native["configDirectory"], native_report_checked_at=time.time())
        self._save_cache_report(report)

    def cache_write_observation(self) -> dict:
        """Bounded metadata-only observation after native execution has closed."""
        report = self.cache_binding_report()
        if not report.get("enabled"):
            return report
        validate_cache_junction(_native_reparse(Path(report["source"])), report["target"])
        stack, files, directories, size = [Path(report["target"])], 0, 0, 0
        while stack:
            directory = stack.pop()
            for item in directory.iterdir():
                metadata = item.lstat()
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                    raise RamdiskError("Native cache observation encountered an unapproved nested reparse point")
                if stat.S_ISDIR(metadata.st_mode):
                    directories += 1
                    stack.append(item)
                elif stat.S_ISREG(metadata.st_mode):
                    files += 1
                    size += metadata.st_size
                if files + directories > 25000:
                    raise RamdiskError("Native cache observation exceeded its bounded inventory")
        return {**report, "observed_project_cache_files": files, "observed_project_cache_bytes": size,
                "observed_project_cache_directories": directories,
                "observation_scope": "Claude project cache only; native auth/configuration stays persistent"}


class RamdiskManager:
    def __init__(self, config: RamdiskConfig, private_root: Path, identities: Mapping):
        self.config, self.private_root = config, Path(private_root)
        self.identities = dict(identities)
        if not self.identities or any(not _SLOT.fullmatch(slot) or slot.upper() in _RESERVED_NAMES for slot in self.identities):
            raise ValueError("RAM slots must use simple, nonreserved local directory names")
        if len({slot.casefold() for slot in self.identities}) != len(self.identities):
            raise ValueError("RAM slot names must be distinct on case-insensitive Windows filesystems")
        self.workspaces: dict[str, RamWorkspace] = {}

    def _save_state(self, evidence: dict) -> None:
        destination = self.private_root / "ramdisk-state.json"
        temporary = self.private_root / (".ramdisk-state-" + uuid.uuid4().hex)
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)

    def ensure(self) -> dict:
        from . import windows as win
        win.require_system()
        if not self.config.enabled:
            raise RamdiskError("RAM disk manager cannot run with RAM storage disabled")
        mount = Path(self.config.mount_root)
        ordinary_tree(self.private_root)
        win.validate_private_directory(self.private_root)
        missing_parents = []
        parent = mount.parent
        while not parent.exists() and not parent.is_symlink():
            missing_parents.append(parent)
            parent = parent.parent
        ordinary_tree(parent)
        win._validate_control_ancestors(parent)
        worker_sids = [win._sid_text(win._account_sid(identity.name)) for identity in self.identities.values()]
        for parent in reversed(missing_parents):
            parent.mkdir()
            win._protect_boundary(parent, worker_sids)
        ordinary_tree(mount.parent)
        win._validate_control_ancestors(mount.parent)
        win.validate_code_path(self.config.imdisk_executable)
        if self.private_root == mount or mount in self.private_root.parents or self.private_root in mount.parents:
            raise RamdiskError("RAM and persistent private state must be disjoint")
        # Serialize provisioning across controllers/installers. Process death
        # releases this mutex; native volume inspection is the recovery truth.
        api = win._api()["kernel32"]
        mutex = win._check(api.CreateMutexW(None, False, "Global\\CoChemRAM-" + hashlib.sha256(
            str(mount).casefold().encode()).hexdigest()[:24]), "Open RAM provisioning mutex")
        acquired = False
        try:
            if api.WaitForSingleObject(mutex, 30000) not in (0, 0x80):
                raise RamdiskError("RAM provisioning is already running")
            acquired = True
            prior = None
            state_path = self.private_root / "ramdisk-state.json"
            if state_path.exists():
                ordinary_tree(state_path)
                win.validate_private_path(state_path)
                if state_path.stat().st_nlink != 1 or state_path.stat().st_size > 1048576:
                    raise RamdiskError("Invalid RAM mount lifecycle ledger")
                prior = json.loads(state_path.read_text(encoding="utf-8"))
            boot_id = win.current_boot_identity()
            try:
                metadata = mount.lstat()
            except FileNotFoundError:
                mount.mkdir()
                metadata = mount.lstat()
            mounted = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            target = _mount_target(self.config) if mounted else None
            action = mount_recovery_action(prior, self.config, boot_id, target)
            if action == "RECREATE":
                _remove_stale_mount(self.config, target)
                mounted = False
            backup = None
            if not mounted:
                import psutil
                if psutil.virtual_memory().available < (self.config.size_mb + self.config.reserve_host_memory_mb) * 1024 * 1024:
                    raise RamdiskCapacityError("Insufficient physical RAM for volume plus reserved host memory")
                backup = backup_mount_contents(mount, mount.parent)
                win._set_acl(mount, None)
                self._save_state({"schema": 1, "state": "PREPARING", "mount_root": str(mount),
                                  "config": self.config.as_dict(), "boot_id": boot_id,
                                  "backup": str(backup) if backup else None})
                completed = subprocess.run(imdisk_create_argv(self.config), capture_output=True, text=True,
                    timeout=180, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
                if completed.returncode:
                    raise RamdiskError("ImDisk provisioning failed; original files remain in the before-ramdisk backup")
            observed = _native_volume(self.config)
            if (mounted and prior.get("state") == "READY"
                    and (observed["volume_serial"], observed["device_number"]) != (
                        prior["observed"]["volume_serial"], prior["observed"]["device_number"])):
                raise RamdiskError("Active RAM volume was replaced without a controller lifecycle transition")
            # Workers may traverse the volume root but cannot replace slot
            # roots or enumerate sibling directories through this boundary.
            win._protect_boundary(mount, worker_sids)
            win._validate_boundary(mount, worker_sids)
            _no_content_index(mount, apply=True)
            for slot, identity in self.identities.items():
                root = mount / slot
                if root.exists():
                    metadata = root.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                        raise RamdiskError("Existing RAM slot is a link/reparse point")
                root.mkdir(exist_ok=True)
                sid = win._sid_text(win._account_sid(identity.name))
                win._set_acl(root, sid)
                win._validate_worker_directory(root, sid, protected=True)
                _no_content_index(root, apply=True)
                self.workspaces[slot] = RamWorkspace(self.config, slot, identity.name,
                                                     observed["volume_serial"], observed["device_number"], self.private_root)
            exclusions = win.defender_exclusions()
            missing = [str(descriptor.root) for descriptor in self.workspaces.values()
                       if str(descriptor.root.resolve()).casefold() not in exclusions]
            if missing:
                win._powershell("foreach ($path in $data) { Add-MpPreference -ExclusionPath $path -ErrorAction Stop }", missing)
            exclusions = win.defender_exclusions()
            if any(str(descriptor.root.resolve()).casefold() not in exclusions for descriptor in self.workspaces.values()):
                raise RamdiskError("Exact RAM execution roots lack verified Defender exclusions")
            evidence = {"schema": 1, "state": "READY", "config": self.config.as_dict(),
                        "mount_root": str(mount), "observed": observed,
                        "boot_id": boot_id, "checked_at": time.time(),
                        "backup": str(backup) if backup else None, "slots": sorted(self.workspaces),
                        "auth_profiles": "persistent_native_profiles_unchanged",
                        "search_not_content_indexed": True, "defender_exact_roots_verified": True}
            self._save_state(evidence)
            return evidence
        finally:
            if acquired:
                release = api.ReleaseMutex
                release.restype, release.argtypes = win.BOOL, [win.HANDLE]
                release(mutex)
            win._close(mutex)

    def workspace(self, slot: str) -> RamWorkspace:
        if slot not in self.workspaces:
            raise RamdiskError("RAM workspaces must be ensured before use")
        return self.workspaces[slot]

    @property
    def execution_roots(self) -> dict[str, Path]:
        return {slot: descriptor.root for slot, descriptor in self.workspaces.items()}

    def inspect(self) -> dict:
        """Read-only deployment check; never create a volume, directory or file."""
        from . import windows as win
        win.require_system()
        if not self.config.enabled:
            raise RamdiskError("RAM storage is disabled")
        ordinary_tree(self.private_root)
        win.validate_private_directory(self.private_root)
        state_path = self.private_root / "ramdisk-state.json"
        ordinary_tree(state_path)
        win.validate_private_path(state_path)
        if state_path.stat().st_nlink != 1 or state_path.stat().st_size > 1048576:
            raise RamdiskError("Invalid RAM lifecycle ledger")
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        boot_id = win.current_boot_identity()
        target = _mount_target(self.config)
        if mount_recovery_action(prior, self.config, boot_id, target) != "VERIFY" or prior["state"] != "READY":
            raise RamdiskError("RAM storage requires SYSTEM provisioning/recovery before activation")
        observed = _native_volume(self.config)
        if (observed["volume_serial"], observed["device_number"]) != (
                prior["observed"]["volume_serial"], prior["observed"]["device_number"]):
            raise RamdiskError("RAM volume identity differs from its private ownership ledger")
        _no_content_index(Path(self.config.mount_root))
        exclusions = win.defender_exclusions()
        for slot, identity in self.identities.items():
            descriptor = RamWorkspace(self.config, slot, identity.name, observed["volume_serial"],
                                      observed["device_number"], self.private_root)
            descriptor.validate(identity=identity.name, cwd=descriptor.root)
            if str(descriptor.root.resolve()).casefold() not in exclusions:
                raise RamdiskError("Exact RAM execution root lacks its Defender exclusion")
            self.workspaces[slot] = descriptor
        return {**prior, "observed": observed, "inspection_only": True, "checked_at": time.time()}


def main():
    parser = argparse.ArgumentParser(description="Provision and verify the exact ImDisk RAM workspace as SYSTEM")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    from .config import load_config
    from .windows import WorkerIdentity
    config = load_config(args.config)
    manager = RamdiskManager(config.ramdisk, config.private_root,
                            {slot: WorkerIdentity(**value) for slot, value in config.workers.items()})
    print(json.dumps(manager.ensure(), indent=2))


if __name__ == "__main__":
    main()
