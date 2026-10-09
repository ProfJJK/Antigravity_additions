"""Standard-library snapshot reads that permit atomic Windows publication."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import time


@contextmanager
def open_shared_text(path: Path):
    """Read one complete snapshot with bounded Windows sharing retries.

    Callers still own protected-path validation, size limits and parsing. Close
    promptly: some Windows replacement operations still await open readers.
    This grants no write access and imports no pipeline/candidate modules.
    """
    if os.name != 'nt':
        with Path(path).open('r', encoding='utf-8') as stream:
            yield stream
        return

    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ | WRITE | DELETE, OPEN_EXISTING.
    deadline = time.monotonic() + 0.25
    while True:
        handle = create(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
        if handle != ctypes.c_void_p(-1).value:
            break
        error = ctypes.get_last_error()
        remaining = deadline - time.monotonic()
        if error not in (5, 32, 33) or remaining <= 0:
            raise ctypes.WinError(error)
        time.sleep(min(0.01, remaining))
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise
    # Ownership transfers to the CRT descriptor, then the Python stream.
    try:
        stream = os.fdopen(descriptor, 'r', encoding='utf-8')
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream
