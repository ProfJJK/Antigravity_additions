"""Read only PawnIO device security as the current ordinary user; no IOCTL."""
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import json

k = C.WinDLL('kernel32', use_last_error=True)
a = C.WinDLL('advapi32', use_last_error=True)
k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
k.CreateFileW.restype = W.HANDLE
k.CloseHandle.argtypes = [W.HANDLE]
k.LocalFree.argtypes = [C.c_void_p]
a.GetKernelObjectSecurity.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
a.GetKernelObjectSecurity.restype = W.BOOL
a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [C.c_void_p, W.DWORD, W.DWORD, C.POINTER(W.LPWSTR), C.POINTER(W.DWORD)]
a.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = W.BOOL
path = r'\\?\GLOBALROOT\Device\PawnIO'
result = {'schema':'cochem-device-security-ordinary-user/1', 'checked_at_utc':datetime.now(timezone.utc).isoformat(),
          'scope':'READ_CONTROL handle plus security metadata only', 'device':path,
          'device_ioctls_issued':0, 'device_data_read_or_written':False,
          'worker_or_system_acceptance':False}
handle = k.CreateFileW(path, 0x20000, 3, None, 3, 0, None)
if handle == C.c_void_p(-1).value:
    result.update(status='HANDLE_NOT_OPENED', winerror=C.get_last_error())
else:
    try:
        size = W.DWORD()
        a.GetKernelObjectSecurity(handle, 5, None, 0, C.byref(size))
        error = C.get_last_error()
        if error != 122 or not 0 < size.value <= 65536:
            result.update(status='SECURITY_SIZE_UNVERIFIED', winerror=error)
        else:
            buffer = C.create_string_buffer(size.value)
            if not a.GetKernelObjectSecurity(handle, 5, buffer, len(buffer), C.byref(size)):
                result.update(status='SECURITY_READ_FAILED', winerror=C.get_last_error())
            else:
                text = W.LPWSTR()
                if not a.ConvertSecurityDescriptorToStringSecurityDescriptorW(buffer, 1, 5, C.byref(text), None):
                    result.update(status='SECURITY_FORMAT_FAILED', winerror=C.get_last_error())
                else:
                    try:
                        result.update(status='DEVICE_SECURITY_METADATA_READ', sddl=text.value)
                    finally:
                        k.LocalFree(C.cast(text, C.c_void_p))
    finally:
        k.CloseHandle(handle)
print(json.dumps(result, indent=2))
