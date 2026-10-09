Read-only preparation; no SYSTEM/worker process, device handle, sensor sample, IOCTL, task or protected artifact has been created by this plan. It is separate from the frozen installation source and requires root review before an administrator action.

The installed PawnIO receipt is `docs/evidence/windows-2026-10-06/pawnio-installed-owner-followup.json`. It proves the observed signed driver/file metadata, not device containment. HWiNFO and the existing 8 GiB R: task remain untouched.

1. Establish the exact interface and limits.

   The official [PawnIO 2.1.0 header](https://raw.githubusercontent.com/namazso/PawnIO/2.1.0/PawnIO/include/pawnio_um.h) defines kernel name `\Device\PawnIO`. The official [LibreHardwareMonitor 0.9.6 adapter](https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/v0.9.6/LibreHardwareMonitorLib/PawnIo/PawnIo.cs) opens `\\?\GLOBALROOT\Device\PawnIO`, requests numeric desired access `3` (`System.IO.FileAccess.ReadWrite`), shares read/write, uses OPEN_EXISTING and FILE_ATTRIBUTE_NORMAL. This is not the GENERIC_READ|GENERIC_WRITE mask. We must reproduce the observed contract explicitly rather than assume a `\\.\PawnIO` DOS alias exists.

   [PawnIO 2.1.0 driver source](https://raw.githubusercontent.com/namazso/PawnIO/2.1.0/PawnIO/src/driver.cpp) supplies default device DACL `D:P(A;;GA;;;SY)(A;;GA;;;BA)` and its CREATE dispatch does not load a module. Module loading and execution occur in separate device-control dispatches. No test described here calls DeviceIoControl, PawnIOLib, LibreHardwareMonitor, ReadFile or WriteFile on the device.

   Under actual SYSTEM, first open the exact device with READ_CONTROL (`0x20000`), read/share write, OPEN_EXISTING and no inheritable handle. Use [GetKernelObjectSecurity](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-getkernelobjectsecurity) for owner/DACL only, with the size query bounded to 64 KiB; record the actual descriptor/hash and parse its ACEs. Request neither SACL nor write-owner/write-DACL access. Close the handle in finally. An unsupported or failed query leaves the installed-device ACL unverified; the service registry ACL or driver-file ACL is not a substitute. Do not reset the descriptor to the source default.

2. Keep the existing runtime boundary.

   Use the installed protected pipeline Python as the SYSTEM supervisor. Require actual SYSTEM, exact interpreter/helper paths, ordinary non-reparse files, protected ancestor ACLs, and reviewed hashes for `windows.py` plus the helper. Read only the installed protected `windows-layout.json` and `pipeline.json`; cross-check all six identities/SIDs/credential-target mappings and four shared execution slots. Do not use the uninstalled repository as an import path or bypass any current SYSTEM/layout validator.

   Reuse the native-status helper's actual token/image/Job observation pattern and the current `launch_worker` context manager, including native profile loading, explicit inherited standard-handle allowlist, per-SID mutex and cleanup. A worker child can be a small pure-stdlib Win32 check executed by the protected base interpreter `C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe -I`. Bind that interpreter to the reviewed payload hash. Using this base executable avoids treating a venv launcher PID as the actual checker interpreter. No model/native login command is needed.

   Each child first waits on a bounded inherited stdin pipe. Before releasing its nonce, the parent opens the owned process token, verifies the selected worker SID against both account lookup and installed layout, queries the exact process image and creation time, and verifies membership of that exact owned Job. The child independently records its own token SID and nonce. Reject SYSTEM/admin identities, enabled Administrators membership or unexpected elevation. No device request is allowed before parent token/Job attestation.

   Run one worker at a time. Proposed collision-free names are `CoChem-4.2.7-WorkerDenial-slotN` and protected root `C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slotN`. Use a distinct global setup reservation plus the runtime's per-worker reservation; do not overlap native status/login work. No triggers, TASK_CREATE only, no replacement or retry of an existing task/root/receipt. Check actual 4.2.7 Warden/Supervisor tasks and installed task-name metadata; legacy Warden is a different responsibility and is not stopped. Actual pipeline daemons must remain stopped/disabled throughout.

3. Request handles only; never use the device.

   Under each actual worker token, call CreateFileW on the exact source-grounded device with access mask `3`, then GENERIC_READ|GENERIC_WRITE (`0xC0000000`), OPEN_EXISTING, share read/write, noninheritable handle. A denial passes only when the call returns INVALID_HANDLE_VALUE and the immediately captured error is ERROR_ACCESS_DENIED (`5`). Missing device/path, busy/sharing errors, exceptions or timeouts are inconclusive and hold acceptance. Any successful handle is closed immediately and fails this denial case, without reading data or issuing an IOCTL.

   Include zero-access handle diagnostics with normal flags and FILE_FLAG_OVERLAPPED (`0x40000000`). [CreateFileW documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) explains that zero access may allow metadata handles despite denied read access; the PawnIO header labels its device controls FILE_ANY_ACCESS. Therefore successful zero-access open must be reported separately and hold any broad device-containment claim for review. It does not, by itself, prove hardware access. Do not test that hypothesis with a module or IOCTL.

4. Check actual pipeline boundaries without reading secrets or modifying data.

   SYSTEM first attests that each exact target exists and that the installed boundary validators succeed. The worker then requests OPEN_EXISTING handles and immediately closes any handle, without ReadFile/WriteFile. Expected observations are: own workspace directory may be listed; private/oracle directory listing is denied; listing every other worker root is denied; controller-token read access is denied. A token handle request does not dereference or emit token contents. Do not enumerate auth-store directories or choose arbitrary job/database files. Missing targets are unresolved rather than a passing denial. ACL inspection can additionally attest protected-code write restrictions; there is no write/truncate/create test on production code or data.

   Docker pipe access and repair-identity boundaries remain explicit separate rows until the actual configured endpoint and provisioned repair identity are known. An absent pipe/identity does not pass. No guessed pipe names, destructive canaries, production writes, R: remount, budget reset or repair jobs are included in this first check.

5. Preserve evidence and verify cleanup.

   Bound each worker to 30 seconds and combined output to 64 KiB. Inherited output/error handles terminate at SYSTEM-private temporary files; publish only structured allowlisted results, paths by target category, error codes, token SID, process creation time/image hash, Job membership, source/config/device-descriptor hashes and timestamps. Keep no raw credential/account output. A successful handle yields a failure record without reading its contents.

   Exit through the actual `launch_worker` context manager. Mark cleanup verified only after it confirms zero active Job processes and unloads the profile/releases the identity. Any cleanup uncertainty blocks reuse and activation; do not kill unrelated PIDs or turn a failed cleanup into an ordinary failed test.

   Create the receipt with exclusive creation; preserve failures and partial evidence. The task wait may treat only Refresh HRESULT `0x8004130B` as a completed instance, then must verify the unique nonce-bound protected receipt, actual SYSTEM identity and exact task result. Only HRESULT `0x80070002` means an absent task. Each slot has its own result; no six-slot aggregate pass is possible with missing or skipped slots.

Prepared scope is a plan, not a runnable or executed worker test. Broader adversarial isolation, descendant cleanup acceptance, Docker denial, repair identity separation, actual sensor acceptance and the 48-hour soak remain separate requirements.
