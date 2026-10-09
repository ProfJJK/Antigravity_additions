"""Explicit human Claude login, with a private one-line fallback input channel.

This standalone helper is copied to a fresh protected session by its reviewed
PowerShell wrapper. It never runs from the checkout and never dispatches a model
request. Raw native output stays in memory; publication stops before stdin is
fed so the submitted code cannot be echoed into the operator log or receipt.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

SUPPORT_SHA256 = '8b19d224effd975423b6d9fb02f79fa949d92e73a131d710ac95de55c8580a52'
MAX_INPUT = 2048
MAX_OUTPUT = 131072
LOGIN_SECONDS = 600
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
NATIVE = Path(r'C:\Program Files\CoChem\Native4.2.7-windows-20261006\claude.exe')


def parse_packet(raw, nonce, *, cancel=False):
    """No value from the private channel appears in exception messages."""
    prefix = b'cochem-login-input/1\n' + nonce.encode('ascii') + b'\n'
    if not isinstance(raw, bytes) or len(raw) > len(prefix) + MAX_INPUT + 1 or not raw.startswith(prefix):
        raise ValueError('Private input binding or size differs')
    line = raw[len(prefix):]
    if not line.endswith(b'\n') or not 1 <= len(line) - 1 <= MAX_INPUT:
        raise ValueError('Exactly one bounded input line is required')
    value = line[:-1]
    if any(char in value for char in (b'\r', b'\n', b'\0')):
        raise ValueError('Input contains a forbidden line delimiter')
    value.decode('utf-8', 'strict')
    if cancel and value != b'CANCEL':
        raise ValueError('Cancellation marker differs')
    return value


class ChannelFile:
    """Held, no-write/no-delete-share single-link channel; optional handle delete.

    Files are opened with OPEN_REPARSE_POINT, then checked through the same
    handle. No pathname reopen is used for bytes, identity, hash or deletion.
    The root and this file must independently pass the private ACL validator.
    """
    def __init__(self, path, win):
        win.validate_private_path(path)
        self.path, self.handle = Path(path), None
        self.api = C.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        self.api.CreateFileW.restype = W.HANDLE
        self.api.CloseHandle.argtypes = [W.HANDLE]; self.api.CloseHandle.restype = W.BOOL
        self.api.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
        self.api.ReadFile.restype = W.BOOL
        self.api.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
        self.api.GetFinalPathNameByHandleW.restype = W.DWORD
        self.api.SetFileInformationByHandle.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        self.api.SetFileInformationByHandle.restype = W.BOOL
        self.handle = self.api.CreateFileW(str(path), 0x80010000, 1, None, 3, 0x00200000, None)
        if self.handle == C.c_void_p(-1).value:
            self.handle = None
            raise C.WinError(C.get_last_error())
        try:
            class Info(C.Structure):
                _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME), ('accessed', W.FILETIME),
                            ('written', W.FILETIME), ('volume', W.DWORD), ('high', W.DWORD),
                            ('low', W.DWORD), ('links', W.DWORD), ('index_high', W.DWORD), ('index_low', W.DWORD)]
            info = Info()
            self.api.GetFileInformationByHandle.argtypes = [W.HANDLE, C.POINTER(Info)]
            self.api.GetFileInformationByHandle.restype = W.BOOL
            if not self.api.GetFileInformationByHandle(self.handle, C.byref(info)):
                raise C.WinError(C.get_last_error())
            size = info.high << 32 | info.low
            if info.attributes & (0x400 | 0x10) or info.links != 1 or not 1 <= size <= MAX_INPUT + 128:
                raise ValueError('Private input must be a bounded ordinary single-link file')
            buffer = C.create_unicode_buffer(32768)
            count = self.api.GetFinalPathNameByHandleW(self.handle, buffer, len(buffer), 0)
            expected = '\\\\?\\' + str(self.path.absolute())
            if not 0 < count < len(buffer) or buffer.value.casefold() != expected.casefold():
                raise ValueError('Private input final handle path differs')
            data = C.create_string_buffer(size + 1); read = W.DWORD()
            if not self.api.ReadFile(self.handle, data, size + 1, C.byref(read), None):
                raise C.WinError(C.get_last_error())
            if read.value != size:
                raise ValueError('Private input size changed')
            self.raw = data.raw[:read.value]
            # Hash belongs only to this in-memory held-handle binding. Never
            # persist it: a receipt must not expose a code-verification oracle.
            self._sha256 = hashlib.sha256(self.raw).digest()
            C.memset(C.addressof(data), 0, len(data))
        except BaseException:
            self.close()
            raise

    def delete_after_verified_cleanup(self):
        if not self.handle:
            raise ValueError('Input handle is not held')
        delete = C.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN.
        if not self.api.SetFileInformationByHandle(self.handle, 4, C.byref(delete), C.sizeof(delete)):
            raise C.WinError(C.get_last_error())
        self.close()

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle); self.handle = None
        self.raw = b''; self._sha256 = b''


def pump(stream, events, stopped):
    """Bounded memory only. Never write raw provider output to disk."""
    try:
        while not stopped.is_set():
            data = stream.read(4096)
            if not data:
                break
            while not stopped.is_set():
                try:
                    events.put(data, timeout=.1)
                    break
                except queue.Full:
                    pass
    except (OSError, ValueError):
        if not stopped.is_set():
            events.put_nowait(None) if not events.full() else None
    finally:
        stream.close()


def feed_once(stream, value):
    # The reviewed packet is at most 2048 bytes; the anonymous pipe has at
    # least the Win32 default 4096-byte buffer. One bounded line, flush, EOF.
    try:
        line = value + b'\n'
        if stream.write(line) != len(line):
            raise OSError('Native input channel accepted an incomplete line')
        stream.flush()
    finally:
        stream.close()


def run_loop(process, writer, events, log, root, nonce, win, report, channels):
    deadline = time.monotonic() + LOGIN_SECONDS
    submitted = False; total = 0
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError('Interactive login deadline expired')
        cancel = root / 'cancel.request'
        if cancel.exists():
            try:
                channel = ChannelFile(cancel, win)
            except OSError as error:
                if error.winerror == 32:
                    time.sleep(.05); continue
                raise
            channels.append(channel)
            parse_packet(channel.raw, nonce, cancel=True)
            report['operator_cancelled'] = True
            raise InterruptedError('Operator requested cancellation')
        input_path = root / 'input.once'
        if not submitted and input_path.exists():
            try:
                channel = ChannelFile(input_path, win)
            except OSError as error:
                if error.winerror == 32:  # Owner is still finishing exclusive creation.
                    time.sleep(.05); continue
                raise
            channels.append(channel)
            value = parse_packet(channel.raw, nonce)
            submitted = True
            report['one_line_submitted'] = True
            # Stop publishing ALL native text before the secret reaches stdin;
            # redaction based on guessed native echo formats is not sufficient.
            log.flush()
            feed_once(writer, value)
            del value
        try:
            data = events.get(timeout=.1)
            if data is None:
                raise OSError('Native output channel failed')
            total += len(data)
            if total > MAX_OUTPUT:
                raise ValueError('Native output exceeded its bound')
            if not submitted:
                log.write(data); log.flush()
            del data
        except queue.Empty:
            pass
        code = process.poll()
        if code is not None:
            return code


def load_support(root):
    path = root / 'worker_native_status_support.py'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SUPPORT_SHA256:
        raise ValueError('Reviewed login support changed')
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location('login_support', path))
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def cleanup_session(process, writer, stopped, threads, channels, report, postcheck):
    """Delete only consumed private channel files after actual terminal proof."""
    if process is not None:
        process.close()
        report['cleanup_verified'] = True
    if writer is not None:
        writer.close()
    stopped.set()
    for thread in threads:
        thread.join(timeout=3)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError('Native output reader did not terminate')
    if process is not None and report['cleanup_verified']:
        postcheck()
        for channel in channels:
            channel.delete_after_verified_cleanup()
        report['input_file_deleted'] = bool(channels)


def run(slot, nonce):
    from cochem_pipeline import windows as win
    from cochem_pipeline.resource_limits import ResourceLimits
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    root = Path(r'C:\Program Files\CoChem') / f'InteractiveClaude427-{slot}-{nonce}'
    if Path(__file__).resolve() != root / 'worker_claude_login_bridge.py':
        raise ValueError('Use only the fresh protected session helper')
    for path in (root, Path(__file__), root / 'worker_native_status_support.py'):
        ordinary_tree(path); win.validate_code_path(path)
    win.validate_private_directory(root)
    support = load_support(root)
    report = {'schema': 'cochem-interactive-claude-login/1', 'slot': slot, 'nonce': nonce,
              'system_sid': win.SYSTEM_SID, 'helper_sha256': support.digest_file(Path(__file__)),
              'status': 'UNVERIFIED', 'cleanup_verified': False, 'one_line_submitted': False,
              'input_file_deleted': False, 'operator_cancelled': False, 'login_commands_executed': 0,
              'model_jobs_executed': 0, 'authentication_verified': False, 'activation_ready': False,
              'secret_published': False, 'native_output_after_input_published': False,
              'started_at_unix_ms': int(time.time()*1000)}
    channels = []; process = None; writer = None; stopped = threading.Event(); threads = []
    phase = 'runtime_custody'
    with (root / 'receipt.json').open('x', encoding='utf-8') as receipt:
        try:
            support.validate_interpreter()
            if support.digest_file(Path(win.__file__)) != support.WINDOWS_SHA256:
                raise ValueError('Installed Windows launch implementation changed')
            support.require_stopped(win)
            phase = 'layout'
            layout, layout_hash = support.protected_json(win, INSTALL / 'windows-layout.json')
            config, config_hash = support.protected_json(win, INSTALL / 'pipeline.json')
            if layout_hash != '8430fdf1109c63c4a89479f03da8a465dfebf5566c7791c64f868202170672c4' or config_hash != '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6':
                raise ValueError('Installed layout/config binding changed')
            spec = layout['slots'][slot]
            identity = win.WorkerIdentity(spec['identity'], spec['credential_target'])
            sid = win._sid_text(win._account_sid(identity.name))
            if sid != spec['sid']:
                raise ValueError('Installed account SID changed')
            phase = 'native_custody'
            ordinary_tree(NATIVE); win.validate_code_path(NATIVE)
            if support.digest_file(NATIVE) != support.CONTRACTS['claude']['sha256']:
                raise ValueError('Reviewed native Claude executable changed')
            limits = ResourceLimits.from_dict(config['execution_limits'])
            events = queue.Queue(maxsize=32)
            stdin_read, stdin_write = os.pipe(); out_read, out_write = os.pipe()
            writer = os.fdopen(stdin_write, 'wb', buffering=0)
            output_reader = os.fdopen(out_read, 'rb', buffering=0)
            thread = threading.Thread(target=pump, args=(output_reader, events, stopped), daemon=True)
            threads.append(thread); thread.start()
            phase = 'native_launch'
            with os.fdopen(stdin_read, 'rb', buffering=0) as stdin, os.fdopen(out_write, 'wb', buffering=0) as output:
                process = win.launch_worker(identity, [str(NATIVE), '--setting-sources', '', 'auth', 'login', '--claudeai'],
                                            spec['root'], stdin, output, output, limits=limits)
            report['login_commands_executed'] = 1
            phase = 'native_attestation'
            report['process'] = support.observe_child(win, process, sid, NATIVE)
            phase = 'native_wait'
            with (root / 'operator.log').open('xb', buffering=0) as log:
                report['native_exit_code'] = run_loop(process, writer, events, log, root, nonce, win, report, channels)
            report['status'] = 'LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED' if report['native_exit_code'] == 0 else 'LOGIN_COMMAND_FAILED'
        except BaseException as error:
            report['status'] = 'LOGIN_FAILED_OR_CANCELLED'
            report['failure'] = support.safe_failure(error, phase)
        finally:
            phase = 'native_cleanup'
            try:
                cleanup_session(process, writer, stopped, threads, channels, report, lambda: support.require_stopped(win))
            except BaseException as error:
                report['status'] = 'CLEANUP_UNVERIFIED'
                report['cleanup_verified'] = False
                report['failure'] = support.safe_failure(error, phase)
            for channel in channels:
                channel.close()  # Failed cleanup preserves the private file.
            report['finished_at_unix_ms'] = int(time.time()*1000)
            json.dump(report, receipt, ensure_ascii=True, sort_keys=True, indent=2)
            receipt.flush(); os.fsync(receipt.fileno())
    return 0 if report['status'] == 'LOGIN_COMMAND_EXITED_ZERO_STATUS_REQUIRED' and report['cleanup_verified'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', required=True, choices=[f'slot{i}' for i in range(1, 7)])
    parser.add_argument('--nonce', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}', args.nonce):
        parser.error('A fresh 32-hex nonce is required')
    try:
        return run(args.slot, args.nonce)
    except BaseException as error:
        # No native/error message or input material reaches public stderr.
        print(json.dumps({'status': 'PRE_RECEIPT_FAILURE', 'error_type': type(error).__name__}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
