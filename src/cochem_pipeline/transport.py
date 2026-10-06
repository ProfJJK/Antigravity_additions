"""Bounded, cancellable stdin delivery without prompt files.

Windows uses a SYSTEM-only, local named byte pipe. Only its read handle is
duplicated into the unprivileged worker's standard handles. The POSIX branch
supports physical transport tests; it does not emulate Windows isolation.
"""
from __future__ import annotations

import os
import threading
import uuid

MAX_PROMPT_BYTES = 8 * 1024 * 1024


class PipeCleanupError(RuntimeError):
    """A pending pipe operation could not be proved closed within its bound."""


def _windows_pipe():
    import ctypes as C
    import msvcrt
    import _winapi
    from .windows import _api, _check, _SECURITY_ATTRIBUTES, HANDLE, DWORD, BOOL, require_system
    require_system()
    api = _api()
    kernel = api['kernel32']
    kernel.CreateNamedPipeW.restype = HANDLE
    kernel.CreateNamedPipeW.argtypes = [C.c_wchar_p, DWORD, DWORD, DWORD, DWORD,
                                       DWORD, DWORD, C.POINTER(_SECURITY_ATTRIBUTES)]
    kernel.ConnectNamedPipe.restype = BOOL
    kernel.ConnectNamedPipe.argtypes = [HANDLE, HANDLE]
    descriptor = HANDLE()
    _check(api['advapi32'].ConvertStringSecurityDescriptorToSecurityDescriptorW(
        'D:P(A;;GA;;;SY)', 1, C.byref(descriptor), None), 'Build private stdin pipe ACL')
    reader = writer = None
    try:
        attributes = _SECURITY_ATTRIBUTES(C.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
        address = '\\\\.\\pipe\\cochem-stdin-' + uuid.uuid4().hex
        reader = kernel.CreateNamedPipeW(address, 0x00000001 | 0x00080000,
            0x00000008, 1, 0, 65536, 0, C.byref(attributes))
        if reader == C.c_void_p(-1).value:
            reader = None
            raise C.WinError(C.get_last_error())
        writer = _winapi.CreateFile(address, _winapi.GENERIC_WRITE, 0, 0,
            _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
        # The local writer connected before this call; ERROR_PIPE_CONNECTED is
        # success. No other principal can open the random pipe namespace.
        if not kernel.ConnectNamedPipe(reader, None) and C.get_last_error() != 535:
            raise C.WinError(C.get_last_error())
        fd = msvcrt.open_osfhandle(reader, os.O_RDONLY | os.O_BINARY)
        reader = None  # fd now owns the native handle.
        return os.fdopen(fd, 'rb', buffering=0), writer
    except BaseException:
        for handle in (reader, writer):
            if handle is not None:
                _winapi.CloseHandle(handle)
        raise
    finally:
        kernel.LocalFree(descriptor)


class PromptPipe:
    """Start writing only after child launch, keeping lease/timeout checks live."""
    def __init__(self, payload: bytes):
        if not isinstance(payload, bytes) or len(payload) > MAX_PROMPT_BYTES:
            raise ValueError('Native prompt exceeds the bounded stdin budget')
        self.payload = payload
        self.reader = None
        self._writer = None
        self._thread = None
        self._stop = threading.Event()
        self._error = None
        self.delivered = False

    def __enter__(self):
        if os.name == 'nt':
            self.reader, self._writer = _windows_pipe()
        else:
            reader, self._writer = os.pipe()
            self.reader = os.fdopen(reader, 'rb', buffering=0)
            os.set_blocking(self._writer, False)
        return self

    def start(self):
        if self._thread is not None or self.reader is None:
            raise RuntimeError('Prompt pipe may be started exactly once after launch')
        # Child already inherited its duplicate. Parent must not retain a read
        # endpoint that would conceal a dead child from the writer.
        self.reader.close()
        self._thread = threading.Thread(target=self._send, name='native-stdin', daemon=True)
        self._thread.start()

    def _write_windows(self, chunk):
        import _winapi
        operation, error = _winapi.WriteFile(self._writer, chunk, overlapped=True)
        try:
            while error == _winapi.ERROR_IO_PENDING:
                if self._stop.is_set():
                    operation.cancel()
                    break
                if _winapi.WaitForSingleObject(operation.event, 50) == _winapi.WAIT_OBJECT_0:
                    break
        except BaseException:
            operation.cancel()
            raise
        finally:
            written, error = operation.GetOverlappedResult(True)
        if error:
            raise OSError(error, 'Native stdin pipe write failed')
        return written

    def _send(self):
        try:
            view = memoryview(self.payload)
            position = 0
            while position < len(view) and not self._stop.is_set():
                chunk = view[position:position + 65536]
                try:
                    count = (self._write_windows(chunk) if os.name == 'nt'
                             else os.write(self._writer, chunk))
                except BlockingIOError:
                    self._stop.wait(.025)
                    continue
                if not count:
                    raise BrokenPipeError('Native stdin made no progress')
                position += count
            self.delivered = position == len(view)
        except BaseException as error:
            self._error = error
        finally:
            self._close_writer()

    def _close_writer(self):
        if self._writer is not None:
            if os.name == 'nt':
                import _winapi
                _winapi.CloseHandle(self._writer)
            else:
                os.close(self._writer)
            self._writer = None

    def verify_delivered(self):
        if self._thread is None:
            raise RuntimeError('Native stdin delivery never started')
        self._thread.join(timeout=2)
        if self._thread.is_alive() or not self.delivered or self._error:
            raise BrokenPipeError('Worker exited without receiving the complete prompt')

    def close(self):
        self._stop.set()
        if self.reader is not None:
            self.reader.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                # Never close/reuse a handle still owned by a pending operation.
                raise PipeCleanupError('Native stdin writer cleanup is unverified')
        else:
            self._close_writer()
        self.payload = b''

    def __exit__(self, *_):
        self.close()
