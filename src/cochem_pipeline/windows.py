"""Fail-closed Windows isolation for the SYSTEM Warden and native CLI workers.

The security boundary is a separate, unprivileged Windows logon for every slot,
not a prompt or an undisclosed SQLite filename. Passwords live exclusively in
the SYSTEM account's Windows Credential Manager. Each CLI owns its own native
subscription login in its worker profile; this module never copies CLI tokens.

Windows execution requires an actual provisioned Windows host. Importing this
module on another OS does not emulate Windows security or process supervision.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, suppress
import ctypes as C
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import tempfile
import time
from typing import BinaryIO, Mapping, TextIO


DWORD = C.c_uint32
WORD = C.c_uint16
BYTE = C.c_ubyte
BOOL = C.c_int32
HANDLE = C.c_void_p
LPWSTR = C.c_wchar_p
SIZE_T = C.c_size_t
SYSTEM_SID = "S-1-5-18"
ADMIN_SID = "S-1-5-32-544"
TRUSTED_INSTALLER_SID = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
FULL_CONTROL = 0x1F01FF
MODIFY = 0x1301BF
_PRIVILEGED_GROUPS = {ADMIN_SID, "S-1-5-32-547", "S-1-5-32-548", "S-1-5-32-549", "S-1-5-32-550", "S-1-5-32-551"}
_SPAWN_LOCK = threading.Lock()


class WindowsIsolationError(RuntimeError):
    """A required native security precondition is unavailable or failed."""


class WindowsCleanupError(WindowsIsolationError):
    """Tree/profile cleanup is unverified; quarantine the identity without reuse."""


@dataclass(frozen=True)
class WorkerIdentity:
    name: str
    credential_target: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,19}", self.name):
            raise ValueError("Worker name must be a simple local Windows account name (20 characters maximum)")
        if not self.credential_target or "\x00" in self.credential_target:
            raise ValueError("A nonempty native Credential Manager target is required")


class _LUID(C.Structure):
    _fields_ = [("LowPart", DWORD), ("HighPart", C.c_int32)]


class _LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", DWORD)]


class _TOKEN_PRIVILEGES_ONE(C.Structure):
    _fields_ = [("PrivilegeCount", DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


class _SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("Sid", HANDLE), ("Attributes", DWORD)]


class _TOKEN_GROUPS_ONE(C.Structure):
    _fields_ = [("GroupCount", DWORD), ("Groups", _SID_AND_ATTRIBUTES * 1)]


class _IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T), ("ActiveProcessLimit", DWORD),
                ("Affinity", SIZE_T), ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]


class _EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class _BASIC_ACCOUNTING(C.Structure):
    _fields_ = [("TotalUserTime", C.c_int64), ("TotalKernelTime", C.c_int64),
                ("ThisPeriodTotalUserTime", C.c_int64), ("ThisPeriodTotalKernelTime", C.c_int64),
                ("TotalPageFaultCount", DWORD), ("TotalProcesses", DWORD),
                ("ActiveProcesses", DWORD), ("TotalTerminatedProcesses", DWORD)]


class _STARTUPINFOW(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", LPWSTR), ("lpDesktop", LPWSTR), ("lpTitle", LPWSTR),
                ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD), ("dwYSize", DWORD),
                ("dwXCountChars", DWORD), ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD),
                ("dwFlags", DWORD), ("wShowWindow", WORD), ("cbReserved2", WORD),
                ("lpReserved2", HANDLE), ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class _STARTUPINFOEXW(C.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", HANDLE)]


class _PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class _SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", HANDLE), ("bInheritHandle", BOOL)]


class _PROFILEINFOW(C.Structure):
    _fields_ = [("dwSize", DWORD), ("dwFlags", DWORD), ("lpUserName", LPWSTR),
                ("lpProfilePath", LPWSTR), ("lpDefaultPath", LPWSTR), ("lpServerName", LPWSTR),
                ("lpPolicyPath", LPWSTR), ("hProfile", HANDLE)]


class _FILETIME(C.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class _CREDENTIALW(C.Structure):
    _fields_ = [("Flags", DWORD), ("Type", DWORD), ("TargetName", LPWSTR), ("Comment", LPWSTR),
                ("LastWritten", _FILETIME), ("CredentialBlobSize", DWORD), ("CredentialBlob", HANDLE),
                ("Persist", DWORD), ("AttributeCount", DWORD), ("Attributes", HANDLE),
                ("TargetAlias", LPWSTR), ("UserName", LPWSTR)]


class _USER_INFO_1(C.Structure):
    _fields_ = [("name", LPWSTR), ("password", LPWSTR), ("password_age", DWORD), ("priv", DWORD),
                ("home_dir", LPWSTR), ("comment", LPWSTR), ("flags", DWORD), ("script_path", LPWSTR)]


class _LSA_UNICODE_STRING(C.Structure):
    _fields_ = [("Length", WORD), ("MaximumLength", WORD), ("Buffer", LPWSTR)]


class _LSA_OBJECT_ATTRIBUTES(C.Structure):
    _fields_ = [("Length", DWORD), ("RootDirectory", HANDLE), ("ObjectName", HANDLE),
                ("Attributes", DWORD), ("SecurityDescriptor", HANDLE), ("SecurityQualityOfService", HANDLE)]


class _ACL_SIZE_INFORMATION(C.Structure):
    _fields_ = [("AceCount", DWORD), ("AclBytesInUse", DWORD), ("AclBytesFree", DWORD)]


class _ACE_HEADER(C.Structure):
    _fields_ = [("AceType", BYTE), ("AceFlags", BYTE), ("AceSize", WORD)]


def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsIsolationError("This operation requires real Windows SYSTEM security; Linux/WSL is not a Windows isolation substitute")


@lru_cache(maxsize=1)
def _api():
    _require_windows()
    libraries = {name: C.WinDLL(name, use_last_error=True) for name in ("kernel32", "advapi32", "userenv", "netapi32")}
    signatures = {
        "kernel32": {
            "GetCurrentProcess": (HANDLE, []), "CloseHandle": (BOOL, [HANDLE]), "LocalFree": (HANDLE, [HANDLE]),
            "CreateFileW": (HANDLE, [LPWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE]),
            "CreateMutexW": (HANDLE, [HANDLE, BOOL, LPWSTR]),
            "CreateJobObjectW": (HANDLE, [HANDLE, LPWSTR]),
            "SetInformationJobObject": (BOOL, [HANDLE, C.c_int, HANDLE, DWORD]),
            "QueryInformationJobObject": (BOOL, [HANDLE, C.c_int, HANDLE, DWORD, C.POINTER(DWORD)]),
            "AssignProcessToJobObject": (BOOL, [HANDLE, HANDLE]), "TerminateJobObject": (BOOL, [HANDLE, C.c_uint]),
            "TerminateProcess": (BOOL, [HANDLE, C.c_uint]), "ResumeThread": (DWORD, [HANDLE]),
            "WaitForSingleObject": (DWORD, [HANDLE, DWORD]), "GetExitCodeProcess": (BOOL, [HANDLE, C.POINTER(DWORD)]),
            "DuplicateHandle": (BOOL, [HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, BOOL, DWORD]),
            "InitializeProcThreadAttributeList": (BOOL, [HANDLE, DWORD, DWORD, C.POINTER(SIZE_T)]),
            "UpdateProcThreadAttribute": (BOOL, [HANDLE, DWORD, SIZE_T, HANDLE, SIZE_T, HANDLE, HANDLE]),
            "DeleteProcThreadAttributeList": (None, [HANDLE]),
            "GetFileAttributesW": (DWORD, [LPWSTR]), "SetFileAttributesW": (BOOL, [LPWSTR, DWORD]),
        },
        "advapi32": {
            "OpenProcessToken": (BOOL, [HANDLE, DWORD, C.POINTER(HANDLE)]),
            "GetTokenInformation": (BOOL, [HANDLE, C.c_int, HANDLE, DWORD, C.POINTER(DWORD)]),
            "LookupPrivilegeValueW": (BOOL, [LPWSTR, LPWSTR, C.POINTER(_LUID)]),
            "AdjustTokenPrivileges": (BOOL, [HANDLE, BOOL, HANDLE, DWORD, HANDLE, HANDLE]),
            "ConvertSidToStringSidW": (BOOL, [HANDLE, C.POINTER(LPWSTR)]),
            "LookupAccountNameW": (BOOL, [LPWSTR, LPWSTR, HANDLE, C.POINTER(DWORD), LPWSTR, C.POINTER(DWORD), C.POINTER(DWORD)]),
            "LogonUserW": (BOOL, [LPWSTR, LPWSTR, LPWSTR, DWORD, DWORD, C.POINTER(HANDLE)]),
            "CreateProcessAsUserW": (BOOL, [HANDLE, LPWSTR, LPWSTR, HANDLE, HANDLE, BOOL, DWORD, HANDLE, LPWSTR, HANDLE, C.POINTER(_PROCESS_INFORMATION)]),
            "CredReadW": (BOOL, [LPWSTR, DWORD, DWORD, C.POINTER(C.POINTER(_CREDENTIALW))]),
            "CredWriteW": (BOOL, [C.POINTER(_CREDENTIALW), DWORD]), "CredFree": (None, [HANDLE]),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (BOOL, [LPWSTR, DWORD, C.POINTER(HANDLE), HANDLE]),
            "SetFileSecurityW": (BOOL, [LPWSTR, DWORD, HANDLE]),
            "GetNamedSecurityInfoW": (DWORD, [LPWSTR, DWORD, DWORD, C.POINTER(HANDLE), HANDLE, C.POINTER(HANDLE), HANDLE, C.POINTER(HANDLE)]),
            "GetSecurityDescriptorControl": (BOOL, [HANDLE, C.POINTER(WORD), C.POINTER(DWORD)]),
            "GetAclInformation": (BOOL, [HANDLE, HANDLE, DWORD, DWORD]),
            "GetAce": (BOOL, [HANDLE, DWORD, C.POINTER(HANDLE)]),
            "LsaOpenPolicy": (DWORD, [HANDLE, C.POINTER(_LSA_OBJECT_ATTRIBUTES), DWORD, C.POINTER(HANDLE)]),
            "LsaAddAccountRights": (DWORD, [HANDLE, HANDLE, C.POINTER(_LSA_UNICODE_STRING), DWORD]),
            "LsaClose": (DWORD, [HANDLE]), "LsaNtStatusToWinError": (DWORD, [DWORD]),
        },
        "userenv": {
            "LoadUserProfileW": (BOOL, [HANDLE, C.POINTER(_PROFILEINFOW)]),
            "UnloadUserProfile": (BOOL, [HANDLE, HANDLE]),
            "CreateEnvironmentBlock": (BOOL, [C.POINTER(HANDLE), HANDLE, BOOL]),
            "DestroyEnvironmentBlock": (BOOL, [HANDLE]),
            "GetUserProfileDirectoryW": (BOOL, [HANDLE, LPWSTR, C.POINTER(DWORD)]),
        },
        "netapi32": {"NetUserAdd": (DWORD, [LPWSTR, DWORD, HANDLE, C.POINTER(DWORD)])},
    }
    for library, functions in signatures.items():
        for name, (restype, argtypes) in functions.items():
            function = getattr(libraries[library], name)
            function.restype, function.argtypes = restype, argtypes
    return libraries


def _check(value, operation: str):
    if not value:
        raise WindowsIsolationError(f"{operation} failed (Windows error {C.get_last_error()})")
    return value


def _close(handle) -> None:
    if handle:
        _api()["kernel32"].CloseHandle(handle)


def _sid_text(sid) -> str:
    api = _api()
    text = LPWSTR()
    _check(api["advapi32"].ConvertSidToStringSidW(sid, C.byref(text)), "SID conversion")
    try:
        return text.value
    finally:
        api["kernel32"].LocalFree(C.cast(text, HANDLE))


def _token_info(token, kind: int):
    size = DWORD()
    api = _api()["advapi32"]
    api.GetTokenInformation(token, kind, None, 0, C.byref(size))
    if not size.value:
        raise WindowsIsolationError("Cannot inspect native Windows token")
    buffer = C.create_string_buffer(size.value)
    _check(api.GetTokenInformation(token, kind, buffer, size, C.byref(size)), "Token inspection")
    return buffer


def require_system() -> None:
    """Require the actual LocalSystem SID; administrator elevation is insufficient."""
    api = _api()
    token = HANDLE()
    _check(api["advapi32"].OpenProcessToken(api["kernel32"].GetCurrentProcess(), 0x8, C.byref(token)), "Open current token")
    try:
        user = _SID_AND_ATTRIBUTES.from_buffer(_token_info(token, 1))
        if _sid_text(user.Sid) != SYSTEM_SID:
            raise WindowsIsolationError("The Warden must run as NT AUTHORITY\\SYSTEM, not an administrator or an agent account")
    finally:
        _close(token)


def _enable_system_privileges() -> None:
    require_system()
    api = _api()
    token = HANDLE()
    _check(api["advapi32"].OpenProcessToken(api["kernel32"].GetCurrentProcess(), 0x28, C.byref(token)), "Open SYSTEM privileges")
    try:
        for name in ("SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege", "SeBackupPrivilege", "SeRestorePrivilege"):
            state = _TOKEN_PRIVILEGES_ONE(PrivilegeCount=1)
            _check(api["advapi32"].LookupPrivilegeValueW(None, name, C.byref(state.Privileges[0].Luid)), "Resolve required privilege")
            state.Privileges[0].Attributes = 2
            C.set_last_error(0)
            _check(api["advapi32"].AdjustTokenPrivileges(token, False, C.byref(state), 0, None, None), "Enable SYSTEM privilege")
            if C.get_last_error() == 1300:
                raise WindowsIsolationError(f"Required SYSTEM privilege is unavailable: {name}")
    finally:
        _close(token)


def _account_sid(name: str):
    api = _api()["advapi32"]
    full_name = name if "\\" in name else f"{os.environ['COMPUTERNAME']}\\{name}"
    sid_size, domain_size, use = DWORD(), DWORD(), DWORD()
    api.LookupAccountNameW(None, full_name, None, C.byref(sid_size), None, C.byref(domain_size), C.byref(use))
    if not sid_size.value:
        raise WindowsIsolationError(f"Dedicated local worker account is missing: {name}")
    sid, domain = C.create_string_buffer(sid_size.value), C.create_unicode_buffer(domain_size.value)
    _check(api.LookupAccountNameW(None, full_name, sid, C.byref(sid_size), domain, C.byref(domain_size), C.byref(use)), "Resolve worker account")
    return sid


def _worker_token(identity: WorkerIdentity):
    require_system()
    api = _api()["advapi32"]
    credential = C.POINTER(_CREDENTIALW)()
    _check(api.CredReadW(identity.credential_target, 1, 0, C.byref(credential)), "Read SYSTEM worker credential; provision this identity first")
    token = HANDLE()
    password_buffer = None
    try:
        expected = f"{os.environ['COMPUTERNAME']}\\{identity.name}"
        if (credential.contents.UserName or "").casefold() != expected.casefold():
            raise WindowsIsolationError("Credential target belongs to a different worker account")
        raw = C.string_at(credential.contents.CredentialBlob, credential.contents.CredentialBlobSize)
        password_buffer = C.create_unicode_buffer(raw.decode("utf-16-le"))
        del raw
        _check(api.LogonUserW(identity.name, os.environ["COMPUTERNAME"], password_buffer, 4, 0, C.byref(token)),
               "Worker batch logon; verify stored credential and SeBatchLogonRight")
    finally:
        if password_buffer is not None:
            C.memset(C.addressof(password_buffer), 0, C.sizeof(password_buffer))
        api.CredFree(credential)
    try:
        user = _SID_AND_ATTRIBUTES.from_buffer(_token_info(token, 1))
        if _sid_text(user.Sid) != _sid_text(_account_sid(identity.name)):
            raise WindowsIsolationError("Worker token SID does not match the configured local identity")
        groups = _token_info(token, 2)
        count = DWORD.from_buffer(groups).value
        values = (_SID_AND_ATTRIBUTES * count).from_buffer(groups, _TOKEN_GROUPS_ONE.Groups.offset)
        if any(_sid_text(group.Sid) in _PRIVILEGED_GROUPS for group in values):
            raise WindowsIsolationError("Workers must not belong to administrative/operator groups")
        # Remove every token privilege, including SeChangeNotifyPrivilege. Its
        # traverse bypass would otherwise permit access to a permissive child
        # below another slot's protected root when its exact filename is known.
        privileges = _token_info(token, 3)
        count = DWORD.from_buffer(privileges).value
        values = (_LUID_AND_ATTRIBUTES * count).from_buffer(privileges, _TOKEN_PRIVILEGES_ONE.Privileges.offset)
        for entry in values:
            entry.Attributes = 4  # SE_PRIVILEGE_REMOVED, cannot be re-enabled.
        _check(api.AdjustTokenPrivileges(token, False, privileges, 0, None, None), "Remove worker token privileges")
        if DWORD.from_buffer(_token_info(token, 3)).value:
            raise WindowsIsolationError("Worker token retained privileges after restriction")
        return token
    except BaseException:
        _close(token)
        raise


def _acl(path: Path) -> tuple[str, bool, list[tuple[str, int, int]]]:
    api = _api()
    owner, dacl, descriptor = HANDLE(), HANDLE(), HANDLE()
    status = api["advapi32"].GetNamedSecurityInfoW(str(path), 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
    if status:
        raise WindowsIsolationError(f"Cannot inspect directory ACL (Windows error {status}): {path}")
    try:
        if not dacl:
            raise WindowsIsolationError(f"A null DACL exposes this directory to every user: {path}")
        control, revision = WORD(), DWORD()
        _check(api["advapi32"].GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)), "Read security control")
        size = _ACL_SIZE_INFORMATION()
        _check(api["advapi32"].GetAclInformation(dacl, C.byref(size), C.sizeof(size), 2), "Read directory ACL")
        rules = []
        for index in range(size.AceCount):
            ace = HANDLE()
            _check(api["advapi32"].GetAce(dacl, index, C.byref(ace)), "Read ACL entry")
            header = _ACE_HEADER.from_address(ace.value)
            if header.AceType != 0:
                raise WindowsIsolationError(f"Unexpected ACL entry; reprovision an explicit allow-only directory: {path}")
            mask = DWORD.from_address(ace.value + 4).value
            rules.append((_sid_text(ace.value + 8), mask, header.AceFlags))
        return _sid_text(owner), bool(control.value & 0x1000), rules
    finally:
        api["kernel32"].LocalFree(descriptor)


def validate_private_path(path: str | Path) -> None:
    """Require an effective private file/directory DACL, including inherited ACLs."""
    require_system()
    directory = Path(path).resolve(strict=True)
    owner, _, rules = _acl(directory)
    trusted = {SYSTEM_SID, ADMIN_SID}
    if owner not in trusted or not rules or not any(sid == SYSTEM_SID and mask == FULL_CONTROL for sid, mask, flags in rules):
        raise WindowsIsolationError("Oracle state requires SYSTEM ownership/access and no active worker access")
    if any(sid not in trusted or mask != FULL_CONTROL or flags & 8 for sid, mask, flags in rules):
        raise WindowsIsolationError("Oracle private state grants access outside SYSTEM/administrators")


def validate_private_directory(path: str | Path) -> None:
    require_system()
    if not Path(path).is_dir():
        raise WindowsIsolationError("Oracle state parent must be a private directory")
    validate_private_path(path)


def validate_code_path(path: str | Path) -> None:
    """Reject writable code or a replaceable ancestor in the SYSTEM deployment."""
    require_system()
    resolved = Path(path).resolve(strict=True)
    bases = [Path(os.environ[key]).resolve() for key in ("ProgramFiles", "SystemRoot")]
    base = next((entry for entry in bases if resolved == entry or entry in resolved.parents), None)
    if base is None:
        raise WindowsIsolationError("Privileged code and native worker executables must be deployed under protected Program Files/Windows paths")
    trusted = {SYSTEM_SID, ADMIN_SID, TRUSTED_INSTALLER_SID}
    current = resolved
    while True:
        owner, _, rules = _acl(current)
        if owner not in trusted or any(sid not in trusted and not flags & 8 and mask & 0x500D0116 for sid, mask, flags in rules):
            raise WindowsIsolationError(f"Untrusted code writes or path replacement are possible: {current}")
        if current == base:
            break
        current = current.parent


def validate_controller_token(path: str | Path, operator_name: str, worker_ids=None) -> None:
    """Validate secret-file ACL without returning, reading or printing its value."""
    require_system()
    token_path = Path(path)
    if not token_path.is_file() or token_path.is_symlink() or token_path.stat().st_nlink != 1:
        raise WindowsIsolationError("Controller token must be a real private file without symlinks/hardlinks")
    operator_sid = _sid_text(_account_sid(operator_name))
    identities = worker_ids.values() if isinstance(worker_ids, Mapping) else (worker_ids or [])
    for identity in identities:
        name = identity.name if isinstance(identity, WorkerIdentity) else str(identity)
        if operator_sid == _sid_text(_account_sid(name)):
            raise WindowsIsolationError("The controller operator cannot be a worker account")
    owner, protected, rules = _acl(token_path)
    expected = {(SYSTEM_SID, FULL_CONTROL), (ADMIN_SID, FULL_CONTROL), (operator_sid, 0x120089)}
    actual = {(sid, mask) for sid, mask, flags in rules}
    if owner != SYSTEM_SID or not protected or actual != expected or any(flags for sid, mask, flags in rules):
        raise WindowsIsolationError("Controller token requires protected SYSTEM/admin full and operator-read-only ACLs")
    if not 32 <= token_path.stat().st_size <= 256:
        raise WindowsIsolationError("Controller token file is empty or has an invalid size")


def protect_controller_token(path: str | Path, operator_name: str) -> None:
    """Create a random controller secret directly on disk with its restrictive ACL.

    Existing tokens are preserved only when their ACL already matches. Values
    are never returned, put on argv, included in config JSON, or printed.
    """
    require_system()
    token_path = Path(path)
    if not token_path.is_absolute():
        raise ValueError("Controller token path must be absolute")
    if token_path.exists():
        validate_controller_token(token_path, operator_name)
        return
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with _create_operator_file(token_path, operator_name) as stream:
        stream.write(secrets.token_urlsafe(48).encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    validate_controller_token(token_path, operator_name)


def _create_operator_file(path: Path, operator_name: str):
    """Atomically create with its final DACL; no pre-ACL inheritable access gap."""
    api = _api()
    descriptor = HANDLE()
    sid = _sid_text(_account_sid(operator_name))
    sddl = f"O:SYG:SYD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x120089;;;{sid})"
    _check(api["advapi32"].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), "Build operator-readable private file ACL")
    try:
        attributes = _SECURITY_ATTRIBUTES(C.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
        handle = api["kernel32"].CreateFileW(str(path), 0x40000000, 1, C.byref(attributes), 1, 0x80, None)
        if handle == C.c_void_p(-1).value:
            raise WindowsIsolationError(f"Could not exclusively create protected file (Windows error {C.get_last_error()})")
    finally:
        api["kernel32"].LocalFree(descriptor)
    import msvcrt
    try:
        descriptor_number = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        _close(handle)
        raise
    return os.fdopen(descriptor_number, "wb")


def _validate_worker_directory(path: Path, sid: str, *, protected: bool = False) -> None:
    owner, is_protected, rules = _acl(path)
    effective = {(account, mask) for account, mask, flags in rules if not flags & 8}
    if owner != SYSTEM_SID or (protected and not is_protected) or effective != {(SYSTEM_SID, FULL_CONTROL), (sid, MODIFY)}:
        raise WindowsIsolationError(f"Worker directory is not isolated to its own account and SYSTEM: {path}")
    if any(account not in {SYSTEM_SID, sid} for account, _, _ in rules):
        raise WindowsIsolationError(f"Worker directory includes an unexpected trustee: {path}")


def _powershell(script: str, data=None):
    _require_windows()
    payload = base64.b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    preamble = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $data=([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + payload + "')) | ConvertFrom-Json); "
    encoded = base64.b64encode((preamble + script).encode("utf-16-le")).decode("ascii")
    executable = str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    completed = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
                               creationflags=subprocess.CREATE_NO_WINDOW, shell=False)
    if completed.returncode:
        raise WindowsIsolationError("Native Windows security configuration check failed: " + completed.stderr.strip()[-2000:])
    return completed.stdout.strip()


@lru_cache(maxsize=1)
def current_boot_identity() -> int:
    """Read trusted native boot metadata once; never infer reboot from a clock."""
    require_system()
    data = json.loads(_powershell(
        "$boot=(Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime; "
        "@{boot_id=$boot.ToFileTimeUtc()} | ConvertTo-Json -Compress"))
    boot = data.get("boot_id") if isinstance(data, dict) else None
    if type(boot) is not int or boot <= 0:
        raise WindowsIsolationError("Windows did not report a reliable boot identity")
    return boot


def defender_exclusions() -> set[str]:
    require_system()
    raw = _powershell("ConvertTo-Json -InputObject @((Get-MpPreference -ErrorAction Stop).ExclusionPath) -Compress")
    entries = json.loads(raw)
    return {str(Path(entry).resolve()).casefold() for entry in entries if isinstance(entry, str) and entry}


def _layout_paths(private_root, slot_roots: Mapping[str, Path], identities: Mapping[str, WorkerIdentity]):
    if not slot_roots or set(slot_roots) != set(identities):
        raise ValueError("Every slot root requires exactly one dedicated worker identity")
    if len({entry.name.casefold() for entry in identities.values()}) != len(identities):
        raise ValueError("Worker accounts must be distinct across slots")
    if len({entry.credential_target.casefold() for entry in identities.values()}) != len(identities):
        raise ValueError("Worker credential targets must be distinct across slots")
    paths = [Path(private_root), *(Path(path) for path in slot_roots.values())]
    if any(not path.is_absolute() or len(path.parts) < 4 for path in paths):
        raise ValueError("Use exact dedicated absolute directories, never drive/system/user roots")
    paths = [path.resolve() for path in paths]
    for index, path in enumerate(paths):
        if any(path == other or path in other.parents or other in path.parents for other in paths[index + 1:]):
            raise ValueError("Private and per-slot directories must be disjoint; slots cannot nest")
    return paths[0], dict(zip(slot_roots, paths[1:]))


def _layout_boundaries(private: Path, roots: Mapping[str, Path]) -> list[Path]:
    common = Path(os.path.commonpath([str(private), *(str(root) for root in roots.values())]))
    if len(common.parts) < 3:
        raise ValueError("Private and worker roots must share one dedicated deployment directory below the system data root")
    boundaries = {common}
    for path in [private, *roots.values()]:
        parent = path.parent
        while parent != common:
            boundaries.add(parent)
            parent = parent.parent
    return sorted(boundaries, key=lambda item: len(item.parts))


def _protect_boundary(path: Path, worker_sids: list[str]) -> None:
    api = _api()
    descriptor = HANDLE()
    sddl = "O:SYG:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)" + "".join(f"(A;;0x100020;;;{sid})" for sid in worker_sids)
    _check(api["advapi32"].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), "Build deployment boundary ACL")
    try:
        _check(api["advapi32"].SetFileSecurityW(str(path), 0x80000005, descriptor), "Protect deployment boundary")
    finally:
        api["kernel32"].LocalFree(descriptor)


def _validate_boundary(path: Path, worker_sids: list[str]) -> None:
    owner, protected, rules = _acl(path)
    expected = {(SYSTEM_SID, FULL_CONTROL, 3), (ADMIN_SID, FULL_CONTROL, 3), *((sid, 0x100020, 0) for sid in worker_sids)}
    if owner != SYSTEM_SID or not protected or set(rules) != expected:
        raise WindowsIsolationError(f"Deployment ancestor permits replacement or lacks worker traversal: {path}")


def _validate_control_ancestors(path: Path) -> None:
    """A private root cannot be secure below a parent others may rename/replace."""
    trusted = {SYSTEM_SID, ADMIN_SID, TRUSTED_INSTALLER_SID}
    for ancestor in (path, *path.parents):
        owner, _, rules = _acl(ancestor)
        # Creating a different sibling is harmless; deleting/replacing this
        # protected child, changing its parent's ACL, or taking ownership is not.
        if owner not in trusted or any(sid not in trusted and not flags & 8 and mask & 0x500D0040 for sid, mask, flags in rules):
            raise WindowsIsolationError(f"Untrusted replacement of the private deployment tree is possible through: {ancestor}")


def validate_layout(private_root: str | Path, slot_roots: Mapping[str, Path], worker_identities: Mapping[str, WorkerIdentity],
                    require_defender: bool = True) -> dict:
    require_system()
    private, roots = _layout_paths(private_root, slot_roots, worker_identities)
    worker_sids = [_sid_text(_account_sid(identity.name)) for identity in worker_identities.values()]
    boundaries = _layout_boundaries(private, roots)
    _validate_control_ancestors(boundaries[0].parent)
    for boundary in boundaries:
        _validate_boundary(boundary, worker_sids)
    validate_private_directory(private)
    exclusions = defender_exclusions() if require_defender else set()
    observed = {}
    for slot, root in roots.items():
        token = _worker_token(worker_identities[slot])
        try:
            sid = _sid_text(_account_sid(worker_identities[slot].name))
            _validate_worker_directory(root, sid, protected=True)
            if require_defender and str(root).casefold() not in exclusions:
                raise WindowsIsolationError(f"Exact attempt workspace root lacks its Defender exclusion: {root}")
            observed[slot] = {"identity": worker_identities[slot].name, "sid": sid, "root": str(root)}
        finally:
            _close(token)
    return {"system": True, "private_root": str(private), "slots": observed, "defender_checked": require_defender}


def _set_acl(path: Path, sid: str | None) -> None:
    api = _api()
    descriptor = HANDLE()
    sddl = "O:SYG:SYD:P(A;OICI;FA;;;SY)" + (f"(A;OICI;0x{MODIFY:x};;;{sid})" if sid else "")
    _check(api["advapi32"].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None), "Build protected directory ACL")
    try:
        _check(api["advapi32"].SetFileSecurityW(str(path), 0x80000005, descriptor), "Apply protected directory ACL")
    finally:
        api["kernel32"].LocalFree(descriptor)


def _grant_batch_logon(sid) -> None:
    api = _api()["advapi32"]
    attributes = _LSA_OBJECT_ATTRIBUTES(Length=C.sizeof(_LSA_OBJECT_ATTRIBUTES))
    policy = HANDLE()
    status = api.LsaOpenPolicy(None, C.byref(attributes), 0x810, C.byref(policy))
    if status:
        raise WindowsIsolationError(f"Cannot open LSA policy (Windows error {api.LsaNtStatusToWinError(status)})")
    try:
        name = "SeBatchLogonRight"
        right = _LSA_UNICODE_STRING(len(name) * 2, (len(name) + 1) * 2, name)
        status = api.LsaAddAccountRights(policy, sid, C.byref(right), 1)
        if status:
            raise WindowsIsolationError(f"Cannot grant worker batch logon (Windows error {api.LsaNtStatusToWinError(status)})")
    finally:
        api.LsaClose(policy)


def _provision_identity(identity: WorkerIdentity) -> None:
    api = _api()
    password = "C0!" + secrets.token_urlsafe(48)
    user = _USER_INFO_1(identity.name, password, 0, 1, None, "CoChem isolated CLI worker; credentials in SYSTEM native store", 0x10200, None)
    parameter = DWORD()
    status = api["netapi32"].NetUserAdd(None, 1, C.byref(user), C.byref(parameter))
    if status not in (0, 2224):
        raise WindowsIsolationError(f"Cannot create standard worker account (Windows status {status}, parameter {parameter.value})")
    if status == 0:
        blob = C.create_string_buffer(password.encode("utf-16-le"))
        credential = _CREDENTIALW(Type=1, TargetName=identity.credential_target,
                                  CredentialBlobSize=len(password.encode("utf-16-le")), CredentialBlob=C.addressof(blob),
                                  Persist=2, UserName=f"{os.environ['COMPUTERNAME']}\\{identity.name}")
        try:
            _check(api["advapi32"].CredWriteW(C.byref(credential), 0), "Store worker password in SYSTEM Credential Manager")
        finally:
            C.memset(C.addressof(blob), 0, C.sizeof(blob))
    del password
    sid = _account_sid(identity.name)
    _grant_batch_logon(sid)
    # Preserve existing accounts/passwords; require a matching stored credential.
    token = _worker_token(identity)
    _close(token)


def provision_layout(private_root: str | Path, slot_roots: Mapping[str, Path], worker_identities: Mapping[str, WorkerIdentity],
                     add_defender: bool = True) -> dict:
    """Provision fresh dedicated roots/accounts. Existing nonempty roots are validated, never rewritten."""
    _enable_system_privileges()
    private, roots = _layout_paths(private_root, slot_roots, worker_identities)
    boundaries = _layout_boundaries(private, roots)
    _validate_control_ancestors(boundaries[0].parent)
    for identity in worker_identities.values():
        _provision_identity(identity)
    worker_sids = [_sid_text(_account_sid(identity.name)) for identity in worker_identities.values()]
    for boundary in boundaries:
        if boundary.exists():
            try:
                _validate_boundary(boundary, worker_sids)
            except WindowsIsolationError:
                if any(boundary.iterdir()):
                    raise WindowsIsolationError(f"Existing nonempty deployment boundary is not provisioned; choose a fresh dedicated location: {boundary}") from None
                _protect_boundary(boundary, worker_sids)
        else:
            boundary.mkdir(parents=True)
            _protect_boundary(boundary, worker_sids)
    for slot, root in {"__private__": private, **roots}.items():
        sid = None if slot == "__private__" else _sid_text(_account_sid(worker_identities[slot].name))
        if root.exists() and any(root.iterdir()):
            if sid is None:
                validate_private_directory(root)
            else:
                _validate_worker_directory(root, sid, protected=True)
        else:
            root.mkdir(parents=True, exist_ok=True)
            _set_acl(root, sid)
        attributes = _api()["kernel32"].GetFileAttributesW(str(root))
        if attributes == 0xFFFFFFFF:
            raise WindowsIsolationError(f"Cannot inspect indexing attributes: {root}")
        _check(_api()["kernel32"].SetFileAttributesW(str(root), attributes | 0x2000), "Exclude dedicated directory from content indexing")
    if add_defender:
        _powershell("foreach ($root in $data) { Add-MpPreference -ExclusionPath $root -ErrorAction Stop }", [str(root) for root in roots.values()])
    return validate_layout(private, roots, worker_identities, require_defender=add_defender)


def _worker_environment(token, overrides: Mapping[str, str] | None) -> C.Array:
    api = _api()["userenv"]
    block = HANDLE()
    _check(api.CreateEnvironmentBlock(C.byref(block), token, False), "Create native worker environment")
    try:
        environment, offset = {}, 0
        while offset < 1024 * 1024:
            entry = C.wstring_at(block.value + offset)
            if not entry:
                break
            offset += (len(entry) + 1) * C.sizeof(C.c_wchar)
            if not entry.startswith("=") and "=" in entry:
                key, value = entry.split("=", 1)
                environment[key.upper()] = value
        else:
            raise WindowsIsolationError("Native worker environment exceeded the supported size")
    finally:
        api.DestroyEnvironmentBlock(block)
    count = DWORD()
    api.GetUserProfileDirectoryW(token, None, C.byref(count))
    profile = C.create_unicode_buffer(count.value)
    _check(api.GetUserProfileDirectoryW(token, profile, C.byref(count)), "Resolve native worker profile")
    protected = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "USERNAME", "USERDOMAIN"}
    for key, value in (overrides or {}).items():
        if not re.fullmatch(r"COCHEM_[A-Z0-9_]+", key) or key in protected or not isinstance(value, str) or "\x00" in value:
            raise ValueError("Worker environment overrides may contain only nonsecret COCHEM_ job metadata")
        environment[key] = value
    from cochem_mcp.providers import _REMOVED_KEYS, _REMOVED_PREFIXES
    environment = {key: value for key, value in environment.items()
                   if key not in _REMOVED_KEYS and not key.startswith(_REMOVED_PREFIXES)
                   and not (key.startswith("CLAUDE_CODE_") and "SKIP_AUTH" in key)
                   and not key.startswith(("GEMINI_", "GOOGLE_API_", "GOOGLE_GENAI_", "GOOGLE_CLOUD_", "CLOUDSDK_AUTH_", "GCLOUD_"))
                   and key not in {"CODEX_HOME", "CLAUDE_CONFIG_DIR"}}
    protected_path = []
    for entry in environment.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            validate_code_path(entry)
        except (WindowsIsolationError, OSError):
            continue
        protected_path.append(entry)
    if not protected_path:
        raise WindowsIsolationError("Worker PATH contains no protected machine directories")
    environment["PATH"] = os.pathsep.join(protected_path)
    environment.update(USERPROFILE=profile.value, HOME=profile.value,
                       LOCALAPPDATA=str(Path(profile.value) / "AppData" / "Local"),
                       APPDATA=str(Path(profile.value) / "AppData" / "Roaming"),
                       TEMP=str(Path(profile.value) / "AppData" / "Local" / "Temp"),
                       TMP=str(Path(profile.value) / "AppData" / "Local" / "Temp"))
    return C.create_unicode_buffer("\x00".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\x00\x00")


class WindowsProcess:
    """Native child plus owned Job Object, profile and logon token handles."""

    def __init__(self, process, job, token, profile, pid: int, argv: list[str], slot_lock=None):
        self._process, self._job, self._token, self._profile = process, job, token, profile
        self.pid, self.args, self.returncode = pid, argv, None
        self._lock = threading.RLock()
        self._slot_lock = slot_lock

    def poll(self) -> int | None:
        with self._lock:
            if not self._process:
                return self.returncode
            result = _api()["kernel32"].WaitForSingleObject(self._process, 0)
            if result == 0x102:
                return None
            if result != 0:
                raise WindowsIsolationError("Native process wait failed")
            code = DWORD()
            _check(_api()["kernel32"].GetExitCodeProcess(self._process, C.byref(code)), "Read worker exit code")
            self.returncode = code.value
            return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and timeout < 0:
            raise ValueError("Timeout must be nonnegative")
        milliseconds = 0xFFFFFFFF if timeout is None else min(int(timeout * 1000), 0xFFFFFFFE)
        # Do not hold the lock while waiting: the circuit breaker must be able
        # to terminate this Job Object from another thread immediately.
        handle = self._process
        if not handle:
            if self.returncode is None:
                raise WindowsIsolationError("Process handles are already closed")
            return self.returncode
        status = _api()["kernel32"].WaitForSingleObject(handle, milliseconds)
        if status == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if status != 0:
            raise WindowsIsolationError("Native worker wait failed")
        return self.poll()

    def terminate(self) -> None:
        with self._lock:
            if self._job:
                _check(_api()["kernel32"].TerminateJobObject(self._job, 1), "Terminate worker process tree")

    def close(self) -> None:
        with self._lock:
            if not self._process and not self._profile:
                return
            # Even if the CLI parent already exited, close/terminate the Job
            # Object to kill lingering descendants before unloading its profile.
            try:
                if self._process:
                    self.terminate()
                    self.wait(timeout=10)
                    deadline = time.monotonic() + 10
                    while True:
                        accounting = _BASIC_ACCOUNTING()
                        _check(_api()["kernel32"].QueryInformationJobObject(self._job, 1, C.byref(accounting), C.sizeof(accounting), None),
                               "Confirm all worker descendants have exited")
                        if accounting.ActiveProcesses == 0:
                            break
                        if time.monotonic() >= deadline:
                            raise WindowsCleanupError("Worker descendants remain active; quarantine this identity/workspace without cleanup or reuse")
                        time.sleep(0.02)
                    _close(self._job)
                    _close(self._process)
                    self._job = self._process = None
                if self._profile:
                    _check(_api()["userenv"].UnloadUserProfile(self._token, self._profile), "Unload worker profile")
                    self._profile = None
                _close(self._token)
                self._token = None
                _close(self._slot_lock)
                self._slot_lock = None
            except (WindowsIsolationError, subprocess.TimeoutExpired) as exc:
                raise WindowsCleanupError("Worker tree/profile cleanup is unverified; quarantine this identity: " + str(exc)) from exc

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _cleanup_failed_launch(process, job, token, profile, slot_lock) -> None:
    """Prove exit even when assignment/resume failed before a wrapper existed.

    An explicit process handle covers the suspended CreateProcess/Assign gap.
    A successful wait and empty Job Object establish teardown even if a
    termination request races an already exited process. On unverified tree or
    profile cleanup, retain the native identity reservation and ownership
    handles: the caller must persist quarantine instead of admitting a retry.
    """
    api = _api()
    failures = []
    try:
        if process.hProcess:
            try:
                _check(api["kernel32"].TerminateProcess(process.hProcess, 1), "Terminate failed-launch worker")
            except WindowsIsolationError:
                # An already exited process can reject termination; the actual
                # process wait below determines whether exit is proven.
                pass
        if job:
            try:
                _check(api["kernel32"].TerminateJobObject(job, 1), "Terminate failed-launch worker tree")
            except WindowsIsolationError:
                # Query the retained job even when the termination request
                # fails. Never treat the request itself as proof of exit.
                pass
        if process.hProcess and api["kernel32"].WaitForSingleObject(process.hProcess, 10000) != 0:
            failures.append("worker process exit was not confirmed")
        if job:
            deadline = time.monotonic() + 10
            while True:
                accounting = _BASIC_ACCOUNTING()
                if not api["kernel32"].QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), None):
                    failures.append("worker descendant count could not be verified")
                    break
                if accounting.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    failures.append("worker descendants remain active")
                    break
                time.sleep(.02)
        if failures:
            raise WindowsCleanupError("Failed launch left tree cleanup unverified; quarantine this identity: " + "; ".join(failures))
        # Do not unload a profile while any process using it may still run.
        if profile.hProfile and not api["userenv"].UnloadUserProfile(token, profile.hProfile):
            raise WindowsCleanupError("Failed launch left profile cleanup unverified; quarantine this identity")
        _close(job)
        _close(process.hProcess)
        _close(token)
        _close(slot_lock)
    finally:
        _close(process.hThread)


def launch_worker(identity: WorkerIdentity, argv: list[str], cwd: str | Path,
                  stdin_file: BinaryIO | TextIO | Path, stdout_file: BinaryIO | TextIO | Path,
                  stderr_file: BinaryIO | TextIO | Path, env_overrides: Mapping[str, str] | None = None) -> WindowsProcess:
    """Create suspended under a separate logon; assign to kill-on-close job before resume.

    Streams are open file objects or paths. Path outputs use exclusive creation.
    Only three supervisor-provided standard handles are inherited. Prompt writes
    never block a pipe before the timeout clock starts on Windows.
    """
    _enable_system_privileges()
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv) or not Path(argv[0]).is_absolute():
        raise ValueError("Worker argv must begin with an absolute native executable and contain no NULs")
    validate_code_path(argv[0])
    directory = Path(cwd).resolve(strict=True)
    sid = _sid_text(_account_sid(identity.name))
    _validate_worker_directory(directory, sid)
    api = _api()
    C.set_last_error(0)
    slot_lock = api["kernel32"].CreateMutexW(None, False, "Global\\CoChemPipeline422-" + sid)
    _check(slot_lock, "Reserve exclusive worker identity")
    if C.get_last_error() == 183:
        _close(slot_lock)
        raise WindowsIsolationError("This worker identity already has an active supervised process; stop it before another job/login")
    try:
        token = _worker_token(identity)
    except BaseException:
        _close(slot_lock)
        raise
    profile = _PROFILEINFOW(dwSize=C.sizeof(_PROFILEINFOW), dwFlags=1, lpUserName=identity.name)
    job, process = None, _PROCESS_INFORMATION()
    try:
        _check(api["userenv"].LoadUserProfileW(token, C.byref(profile)), "Load native worker profile")
        environment = _worker_environment(token, env_overrides)
        job = _check(api["kernel32"].CreateJobObjectW(None, None), "Create worker Job Object")
        limit = _EXTENDED_LIMIT()
        limit.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(api["kernel32"].SetInformationJobObject(job, 9, C.byref(limit), C.sizeof(limit)), "Enable Job Object kill-on-close")
        import msvcrt
        with _SPAWN_LOCK, ExitStack() as stack:
            duplicates = []
            for stream, mode, access in ((stdin_file, "rb", 0x120089), (stdout_file, "xb", 0x120116), (stderr_file, "xb", 0x120116)):
                if not hasattr(stream, "fileno"):
                    stream = stack.enter_context(Path(stream).open(mode))
                stream.flush()
                duplicate = HANDLE()
                original = HANDLE(msvcrt.get_osfhandle(stream.fileno()))
                current = api["kernel32"].GetCurrentProcess()
                _check(api["kernel32"].DuplicateHandle(current, original, current, C.byref(duplicate), access, True, 0), "Prepare inherited standard handle")
                duplicates.append(duplicate.value)
                stack.callback(_close, duplicate)
            size = SIZE_T()
            api["kernel32"].InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
            if not size.value:
                raise WindowsIsolationError("Explicit Windows handle inheritance is unavailable")
            attributes = C.create_string_buffer(size.value)
            _check(api["kernel32"].InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)), "Initialize inherited-handle allowlist")
            stack.callback(api["kernel32"].DeleteProcThreadAttributeList, attributes)
            handles = (HANDLE * len(duplicates))(*duplicates)
            _check(api["kernel32"].UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, C.sizeof(handles), None, None), "Restrict inherited handles")
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = C.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = duplicates
            startup.lpAttributeList = C.addressof(attributes)
            command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
            flags = 0x4 | 0x400 | 0x80000 | 0x08000000  # suspended, Unicode env, extended startup, no window
            _check(api["advapi32"].CreateProcessAsUserW(token, argv[0], command, None, None, True, flags,
                                                        environment, str(directory), C.byref(startup), C.byref(process)), "Create suspended worker")
            _check(api["kernel32"].AssignProcessToJobObject(job, process.hProcess), "Assign suspended worker to Job Object")
            if api["kernel32"].ResumeThread(process.hThread) == 0xFFFFFFFF:
                raise WindowsIsolationError("Could not resume supervised worker")
        _close(process.hThread)
        return WindowsProcess(process.hProcess, job, token, profile.hProfile, process.dwProcessId, list(argv), slot_lock)
    except BaseException as launch_error:
        try:
            _cleanup_failed_launch(process, job, token, profile, slot_lock)
        except WindowsCleanupError as cleanup_error:
            raise cleanup_error from launch_error
        except BaseException as cleanup_error:
            raise WindowsCleanupError("Failed launch cleanup could not be verified; quarantine this identity") from cleanup_error
        raise


def login_worker(layout_file: str | Path, slot: str, provider: str, executable: str, log_path: str | Path) -> int:
    """Run the real native subscription login in the selected worker's profile.

    The operator reads the provider's login link/code locally from a protected
    log. No provider credential is parsed, copied, returned, or placed in argv.
    """
    require_system()
    layout = json.loads(Path(layout_file).read_text(encoding="utf-8"))
    if slot not in layout["slots"] or provider not in {"codex", "claude"}:
        raise ValueError("Choose a provisioned slot and codex or claude")
    spec = layout["slots"][slot]
    identity = WorkerIdentity(spec["identity"], spec["credential_target"])
    operator = layout["operator_name"]
    from cochem_mcp.providers import executable_prefix
    prefix = executable_prefix(provider, executable)
    argv = prefix + (["login", "--device-auth"] if provider == "codex" else ["--setting-sources", "", "auth", "login", "--claudeai"])
    destination = Path(log_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _create_operator_file(destination, operator) as output, tempfile.TemporaryFile(mode="w+b") as prompt:
        with launch_worker(identity, argv, spec["root"], prompt, output, output) as process:
            try:
                return process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                process.terminate()
                raise WindowsIsolationError("Native subscription login exceeded ten minutes; no login success was assumed") from None


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision real Windows SYSTEM/worker isolation; never copies provider credentials")
    parser.add_argument("operation", choices=("provision", "validate", "login"))
    parser.add_argument("--private-root")
    parser.add_argument("--workers-root")
    parser.add_argument("--slots", type=int, default=6, choices=range(1, 65),
                        help="Dedicated identity pool size; runtime concurrency remains capped at four")
    parser.add_argument("--operator-name")
    parser.add_argument("--controller-token")
    parser.add_argument("--layout-output")
    parser.add_argument("--layout")
    parser.add_argument("--slot")
    parser.add_argument("--provider", choices=("codex", "claude"))
    parser.add_argument("--executable")
    parser.add_argument("--log-path")
    args = parser.parse_args()
    if args.operation == "login":
        if not all((args.layout, args.slot, args.provider, args.executable, args.log_path)):
            parser.error("login requires --layout, --slot, --provider, --executable and --log-path")
        raise SystemExit(login_worker(args.layout, args.slot, args.provider, args.executable, args.log_path))
    if not args.private_root or not args.workers_root:
        parser.error("provision/validate requires --private-root and --workers-root")
    identities = {f"slot{index}": WorkerIdentity(f"CoChem422Worker{index}", f"CoChem422/slot{index}") for index in range(1, args.slots + 1)}
    roots = {slot: Path(args.workers_root) / slot for slot in identities}
    operation = provision_layout if args.operation == "provision" else validate_layout
    if bool(args.operator_name) != bool(args.controller_token):
        parser.error("--operator-name and --controller-token must be supplied together")
    result = operation(args.private_root, roots, identities)
    if args.operator_name:
        if args.operation == "provision":
            protect_controller_token(args.controller_token, args.operator_name)
        validate_controller_token(args.controller_token, args.operator_name, identities)
        result.update(operator_name=args.operator_name, token_file=args.controller_token)
    for slot, identity in identities.items():
        result["slots"][slot]["credential_target"] = identity.credential_target
    output = json.dumps(result, indent=2)
    if args.layout_output:
        Path(args.layout_output).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
