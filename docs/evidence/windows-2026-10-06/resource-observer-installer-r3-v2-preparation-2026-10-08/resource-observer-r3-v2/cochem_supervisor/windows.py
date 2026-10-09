"""Read-only Win32 boundaries for the external observer. No credential APIs."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from functools import lru_cache
import os
from pathlib import Path

SYSTEM = 'S-1-5-18'
ADMIN = 'S-1-5-32-544'
INSTALLER = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
FULL = 0x1f01ff


class BoundaryError(RuntimeError):
    """Fixed error codes only: no path, token, command or provider text."""


def require(value, code):
    if not value:
        raise BoundaryError(code)


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Sid', W.LPVOID), ('Attributes', W.DWORD)]


class ACL_SIZE_INFORMATION(C.Structure):
    _fields_ = [('AceCount', W.DWORD), ('AclBytesInUse', W.DWORD), ('AclBytesFree', W.DWORD)]


class ACE_HEADER(C.Structure):
    _fields_ = [('AceType', C.c_ubyte), ('AceFlags', C.c_ubyte), ('AceSize', W.WORD)]


@lru_cache(maxsize=1)
def api():
    require(os.name == 'nt', 'WINDOWS_REQUIRED')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    signatures = {
        kernel: {
            'GetCurrentProcess': (W.HANDLE, []),
            'OpenProcess': (W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
            'CloseHandle': (W.BOOL, [W.HANDLE]),
            'LocalFree': (W.HANDLE, [W.HANDLE]),
            'GetProcessTimes': (W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4),
            'GetProcessHandleCount': (W.BOOL, [W.HANDLE, C.POINTER(W.DWORD)]),
            'QueryFullProcessImageNameW': (W.BOOL, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]),
            'WaitForSingleObject': (W.DWORD, [W.HANDLE, W.DWORD]),
        },
        advapi: {
            'OpenProcessToken': (W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
            'GetTokenInformation': (W.BOOL, [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]),
            'ConvertSidToStringSidW': (W.BOOL, [W.LPVOID, C.POINTER(W.LPWSTR)]),
            'GetNamedSecurityInfoW': (W.DWORD, [W.LPWSTR, W.DWORD, W.DWORD, C.POINTER(W.HANDLE), W.LPVOID, C.POINTER(W.HANDLE), W.LPVOID, C.POINTER(W.HANDLE)]),
            'GetAclInformation': (W.BOOL, [W.HANDLE, W.LPVOID, W.DWORD, C.c_int]),
            'GetAce': (W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
        },
    }
    for library, values in signatures.items():
        for name, (result, arguments) in values.items():
            fn = getattr(library, name)
            fn.restype, fn.argtypes = result, arguments
    return kernel, advapi


def sid_text(sid):
    kernel, advapi = api()
    text = W.LPWSTR()
    require(advapi.ConvertSidToStringSidW(sid, C.byref(text)), 'SID_QUERY')
    try:
        return text.value
    finally:
        kernel.LocalFree(C.cast(text, W.HANDLE))


def process_sid(handle):
    kernel, advapi = api()
    token = W.HANDLE()
    require(advapi.OpenProcessToken(handle, 8, C.byref(token)), 'TOKEN_QUERY')
    try:
        size = W.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, C.byref(size))
        require(0 < size.value <= 65536, 'TOKEN_SIZE')
        buffer = C.create_string_buffer(size.value)
        require(advapi.GetTokenInformation(token, 1, buffer, size, C.byref(size)), 'TOKEN_USER')
        return sid_text(SID_AND_ATTRIBUTES.from_buffer(buffer).Sid)
    finally:
        kernel.CloseHandle(token)


def require_system():
    require(process_sid(api()[0].GetCurrentProcess()) == SYSTEM, 'SYSTEM_REQUIRED')


def ordinary(path):
    path = Path(path)
    require(path.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    for item in (path, *path.parents):
        stat = item.lstat()
        require(not item.is_symlink() and not stat.st_file_attributes & 0x400, 'REPARSE_PATH')
    stat = path.stat()
    require(path.is_dir() or (path.is_file() and stat.st_nlink == 1), 'ORDINARY_PATH_REQUIRED')
    return stat


def acl(path):
    kernel, advapi = api()
    owner, dacl, descriptor = W.HANDLE(), W.HANDLE(), W.HANDLE()
    require(advapi.GetNamedSecurityInfoW(str(path), 1, 5, C.byref(owner), None,
            C.byref(dacl), None, C.byref(descriptor)) == 0, 'ACL_QUERY')
    try:
        require(bool(dacl), 'NULL_DACL')
        size = ACL_SIZE_INFORMATION()
        require(advapi.GetAclInformation(dacl, C.byref(size), C.sizeof(size), 2), 'ACL_SIZE')
        require(0 < size.AceCount <= 64, 'ACL_COUNT')
        rules = []
        for index in range(size.AceCount):
            address = W.HANDLE()
            require(advapi.GetAce(dacl, index, C.byref(address)), 'ACE_QUERY')
            header = ACE_HEADER.from_address(address.value)
            require(header.AceType == 0, 'ALLOW_ONLY_ACL_REQUIRED')
            rules.append((sid_text(address.value + 8), W.DWORD.from_address(address.value + 4).value, header.AceFlags))
        return sid_text(owner), rules
    finally:
        kernel.LocalFree(descriptor)


def private_rules(owner, rules):
    require(owner in {SYSTEM, ADMIN} and bool(rules), 'PRIVATE_OWNER')
    require(any(sid == SYSTEM and mask == FULL and not flags & 8 for sid, mask, flags in rules), 'SYSTEM_FULL_CONTROL')
    require(all(sid in {SYSTEM, ADMIN} and mask == FULL and not flags & 8 for sid, mask, flags in rules), 'PRIVATE_DACL')


def validate_private_path(path):
    require_system()
    ordinary(path)
    private_rules(*acl(Path(path)))


def validate_private_directory(path):
    require(Path(path).is_dir(), 'PRIVATE_DIRECTORY_REQUIRED')
    validate_private_path(path)


def validate_code_path(path):
    require_system()
    ordinary(path)
    path = Path(path)
    bases = [Path(os.environ['ProgramFiles']), Path(os.environ['SystemRoot'])]
    base = next((x for x in bases if path == x or x in path.parents), None)
    require(base is not None, 'PROTECTED_CODE_ROOT')
    current = path
    while True:
        owner, rules = acl(current)
        require(owner in {SYSTEM, ADMIN, INSTALLER}, 'CODE_OWNER')
        require(all(sid in {SYSTEM, ADMIN, INSTALLER} or flags & 8 or not mask & 0x500d0116
                    for sid, mask, flags in rules), 'CODE_WRITABLE')
        if current == base:
            break
        current = current.parent


class ProcessHandle:
    """Query/synchronize handle only; never reads memory or adjusts/terminates."""
    def __init__(self, pid):
        require(type(pid) is int and 0 < pid <= 0xffffffff, 'PID_RANGE')
        self.pid = pid
        self.handle = api()[0].OpenProcess(0x1000 | 0x100000, False, pid)
        require(bool(self.handle), 'PROCESS_QUERY')

    def snapshot(self):
        kernel, _ = api()
        require(kernel.WaitForSingleObject(self.handle, 0) == 258, 'PROCESS_EXITED')
        created, ended, system, user = (W.FILETIME() for _ in range(4))
        require(kernel.GetProcessTimes(self.handle, C.byref(created), C.byref(ended), C.byref(system), C.byref(user)), 'PROCESS_TIMES')
        count = W.DWORD(32768)
        image = C.create_unicode_buffer(count.value)
        require(kernel.QueryFullProcessImageNameW(self.handle, 0, image, C.byref(count)), 'PROCESS_IMAGE')
        handles = W.DWORD()
        require(kernel.GetProcessHandleCount(self.handle, C.byref(handles)), 'PROCESS_HANDLES')
        return {'pid': self.pid, 'creation_filetime': created.dwLowDateTime | created.dwHighDateTime << 32,
                'image': image.value, 'token_sid': process_sid(self.handle), 'handles': handles.value,
                'kernel_cpu_100ns': system.dwLowDateTime | system.dwHighDateTime << 32,
                'user_cpu_100ns': user.dwLowDateTime | user.dwHighDateTime << 32}

    def close(self):
        if self.handle:
            api()[0].CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
