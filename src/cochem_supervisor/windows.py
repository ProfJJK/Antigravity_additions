"""Native Windows controls for the independently installed repair supervisor.

This adapter never imports or executes a repair candidate. Its process/ACL
primitives come from the supervisor's own protected package installation,
independent of whichever pipeline release the Warden currently runs.
"""
from __future__ import annotations

import argparse
import ctypes as C
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Mapping

from cochem_pipeline import windows as native


REPAIR_IDENTITY = native.WorkerIdentity("CoChem423Repair", "CoChem423/repair")


def _task_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("Use an exact root-folder Scheduled Task name; wildcards, paths and command text are forbidden")
    return name


def _walk_without_links(root: str | Path):
    """Traverse files without following reparse points or accepting hard links."""
    pending = [Path(root)]
    while pending:
        item = pending.pop()
        info = item.lstat()
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise native.WindowsIsolationError(f"Privileged source/test trees cannot contain reparse points: {item}")
        if not item.is_dir() and (not item.is_file() or info.st_nlink != 1):
            raise native.WindowsIsolationError(f"Privileged source/test files must be ordinary files without hard links: {item}")
        yield item
        if item.is_dir():
            pending.extend(item.iterdir())


def _protected_tree(path: str | Path) -> None:
    for item in _walk_without_links(path):
        native.validate_code_path(item)


def require_supervisor(config: Mapping) -> dict:
    """Validate actual SYSTEM, independent deployment, repair identity and trust roots."""
    native.require_system()
    native.validate_code_path(Path(__file__))
    native.validate_code_path(Path(native.__file__))
    native.validate_code_path(Path(sys.executable))
    private = Path(config["private_root"]).resolve(strict=True)
    workspace = Path(config["repair_workspace"]).resolve(strict=True)
    identity = native.WorkerIdentity(**config["repair_worker"])
    layout = native.validate_layout(private, {"repair": workspace}, {"repair": identity}, require_defender=True)
    for key in ("release_root", "baseline_source", "acceptance_root"):
        _protected_tree(config[key])
    for key in ("pipeline_python", "test_python", "pipeline_config"):
        native.validate_code_path(config[key])
    pipeline_python = Path(config["pipeline_python"]).resolve()
    if pipeline_python == Path(sys.executable).resolve() or Path(sys.prefix).resolve() in pipeline_python.parents:
        raise native.WindowsIsolationError("The supervisor must use its own installation/interpreter, separate from the managed pipeline")
    if Path(config["test_python"]).resolve() != Path(sys.executable).resolve():
        raise native.WindowsIsolationError("Acceptance tests must use this protected supervisor interpreter")
    acceptance = Path(config["acceptance_root"]).resolve(strict=True)
    if not all((acceptance / suite).is_dir() for suite in ("pipeline_tests", "mcp_tests")):
        raise native.WindowsIsolationError("Both protected pipeline_tests and mcp_tests snapshots are required")
    for support in ("pytest.ini",):
        if not (acceptance / support).is_file():
            raise native.WindowsIsolationError(f"Protected acceptance support file is missing: {support}")
    pointer = Path(config["pointer_file"]).resolve()
    if private not in pointer.parents:
        raise native.WindowsIsolationError("Release pointer must be inside the supervisor's SYSTEM-private state")
    if pointer.exists():
        native.validate_private_path(pointer)
    warden_task = _task_name(config.get("warden_task", "CoChem-4.2.2-Warden"))
    supervisor_task = _task_name(config.get("supervisor_task", "CoChem-4.2.3-Supervisor"))
    if warden_task == supervisor_task:
        raise native.WindowsIsolationError("Supervisor and managed Warden must be separate Scheduled Tasks")
    pipeline = json.loads(Path(config["pipeline_config"]).read_text(encoding="utf-8-sig"))
    native.validate_private_directory(pipeline["private_root"])
    pipeline_private = Path(pipeline["private_root"]).resolve()
    if pipeline_private == private or pipeline_private in private.parents or private in pipeline_private.parents:
        raise native.WindowsIsolationError("Supervisor ledger and pipeline database must have separate private roots")
    for worker in pipeline.get("workers", {}).values():
        if worker.get("name", "").casefold() == identity.name.casefold():
            raise native.WindowsIsolationError("The repair account cannot be one of the managed pipeline's chapter accounts")
    return {"system": True, "independent_installation": True, "repair_layout": layout,
            "warden_task": warden_task, "supervisor_task": supervisor_task}


def protect_release_tree(path: str | Path) -> None:
    """Apply exact code ACLs to an already copied release; does not attest its tests.

    Callers must independently verify candidate hashes and acceptance results
    before selecting the resulting tree in the protected release pointer.
    """
    native._enable_system_privileges()
    root = Path(path).absolute()
    native.validate_code_path(root.parent)
    items = list(_walk_without_links(root))
    api = native._api()
    for item in items:
        inheritance = "OICI" if item.is_dir() else ""
        sddl = f"O:SYG:SYD:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;BA)(A;{inheritance};0x1200a9;;;BU)"
        descriptor = native.HANDLE()
        native._check(api["advapi32"].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None),
                      "Build immutable release ACL")
        try:
            native._check(api["advapi32"].SetFileSecurityW(str(item), 0x80000005, descriptor), "Protect immutable release file")
        finally:
            api["kernel32"].LocalFree(descriptor)
    _protected_tree(root)


def prepare_repair_workspace(path: str | Path, identity_name: str) -> None:
    """Restore worker access after a SYSTEM snapshot copy, including mode-0700 dirs.

    The caller must close its repair worker before copying or changing these
    ACLs. Candidate data never receives authority over supervisor private state.
    """
    native._enable_system_privileges()
    native.WorkerIdentity(identity_name, "acl-validation")
    sid = native._sid_text(native._account_sid(identity_name))
    if sid in {native.SYSTEM_SID, native.ADMIN_SID}:
        raise native.WindowsIsolationError("Repair workspace requires a dedicated unprivileged account")
    items = list(_walk_without_links(Path(path).absolute()))
    api = native._api()
    for item in items:
        inheritance = "OICI" if item.is_dir() else ""
        sddl = (f"O:SYG:SYD:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;BA)"
                f"(A;{inheritance};0x1301bf;;;{sid})")
        descriptor = native.HANDLE()
        native._check(api["advapi32"].ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None),
                      "Build repair workspace ACL")
        try:
            native._check(api["advapi32"].SetFileSecurityW(str(item), 0x80000005, descriptor), "Grant dedicated repair worker snapshot access")
        finally:
            api["kernel32"].LocalFree(descriptor)
    expected = {(native.SYSTEM_SID, native.FULL_CONTROL), (native.ADMIN_SID, native.FULL_CONTROL), (sid, native.MODIFY)}
    for item in _walk_without_links(path):
        owner, protected, rules = native._acl(item)
        if owner != native.SYSTEM_SID or not protected or len(rules) != 3 or {(who, mask) for who, mask, _ in rules} != expected or any(flags & 8 for _, _, flags in rules):
            raise native.WindowsIsolationError(f"Dedicated repair workspace ACL validation failed: {item}")


def _task_operation(name: str, operation: str) -> dict:
    native.require_system()
    name = _task_name(name)
    commands = {
        "start": "Start-ScheduledTask -InputObject $task;",
        "stop": (
            "Stop-ScheduledTask -InputObject $task; $deadline=(Get-Date).AddSeconds(30); "
            "do { $task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\'; "
            "if ($task.State -notin @('Running','Queued')) { break }; "
            "if ((Get-Date) -gt $deadline) { throw 'Managed task did not stop within thirty seconds' }; "
            "Start-Sleep -Milliseconds 100 } while ($true);"
        ),
    }
    if operation not in commands:
        raise ValueError("Unsupported task operation")
    script = (
        "$task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\' -ErrorAction Stop; "
        "if ($task.Principal.UserId -notin @('SYSTEM','NT AUTHORITY\\SYSTEM','S-1-5-18')) "
        "{ throw 'Managed task must run as SYSTEM' }; "
        + commands[operation]
        + "$task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\'; "
        "@{name=$task.TaskName;state=[string]$task.State;operation=$data.operation} | ConvertTo-Json -Compress"
    )
    return json.loads(native._powershell(script, {"name": name, "operation": operation}))


def _process_creation_time(handle) -> int:
    api = native._api()["kernel32"]
    get_times = api.GetProcessTimes
    get_times.restype = native.BOOL
    get_times.argtypes = [native.HANDLE] + [C.POINTER(native._FILETIME)] * 4
    created, exited, kernel, user = (native._FILETIME() for _ in range(4))
    native._check(get_times(handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)), "Verify launcher process creation time")
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


@lru_cache(maxsize=1)
def _current_boot_id() -> int:
    """Use trusted Windows boot metadata, never a clock/uptime approximation."""
    native.require_system()
    data = json.loads(native._powershell(
        "$boot=(Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime; "
        "@{boot_id=$boot.ToFileTimeUtc()} | ConvertTo-Json -Compress"))
    boot = data.get("boot_id")
    if type(boot) is not int or boot <= 0:
        raise native.WindowsIsolationError("Windows did not report a reliable boot identity")
    return boot


def _record_exited(record: dict, current_boot: int) -> bool:
    # Processes and kernel Job Objects from a completed boot cannot survive
    # into another boot. Same-boot crashes still require a cleanup receipt.
    return record["closed"] or record["boot_id"] != current_boot


def _read_control(path: Path) -> dict:
    native.validate_private_path(path)
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise native.WindowsIsolationError("Warden process control record must be a private ordinary file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("schema") != 1
            or any(type(data.get(key)) is not int or data[key] <= 0 for key in ("pid", "created", "job_handle", "boot_id"))
            or type(data.get("closed")) is not bool or type(data.get("contained")) is not bool):
        raise native.WindowsIsolationError("Invalid Warden process ownership record")
    return data


def _write_control(path: Path, data: dict) -> None:
    native.validate_private_directory(path.parent)
    if path.exists():
        native.validate_private_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".warden-control-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        native.validate_private_path(temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _duplicate_recorded_job(record: dict):
    if record["boot_id"] != _current_boot_id():
        raise native.WindowsIsolationError("Recorded Job Object belongs to a prior Windows boot")
    api = native._api()["kernel32"]
    open_process = api.OpenProcess
    open_process.restype = native.HANDLE
    open_process.argtypes = [native.DWORD, native.BOOL, native.DWORD]
    process = native._check(open_process(0x1000 | 0x0040, False, record["pid"]), "Open recorded Warden launcher")
    job = native.HANDLE()
    try:
        if _process_creation_time(process) != record["created"]:
            raise native.WindowsIsolationError("Recorded Warden PID was reused by a different process")
        native._check(api.DuplicateHandle(process, record["job_handle"], api.GetCurrentProcess(), C.byref(job), 0x000C, False, 0),
                      "Retain verified Warden Job Object before stopping its launcher")
        accounting = native._BASIC_ACCOUNTING()
        native._check(api.QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), None), "Verify recorded handle is a Job Object")
        return job
    except BaseException:
        native._close(job)
        raise
    finally:
        native._close(process)


def _wait_job_empty(job, timeout: float = 30) -> None:
    api = native._api()["kernel32"]
    deadline = time.monotonic() + timeout
    while True:
        accounting = native._BASIC_ACCOUNTING()
        native._check(api.QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), None), "Confirm all Warden job descendants have exited")
        if not accounting.ActiveProcesses:
            return
        if time.monotonic() >= deadline:
            raise native.WindowsCleanupError("Warden descendants remain active; do not deploy or reuse managed state")
        time.sleep(0.05)


def _task_state(name: str) -> str:
    return json.loads(native._powershell(
        "$task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\' -ErrorAction Stop; "
        "if ($task.Principal.UserId -notin @('SYSTEM','NT AUTHORITY\\SYSTEM','S-1-5-18')) { throw 'Managed task must run as SYSTEM' }; "
        "@{state=[string]$task.State} | ConvertTo-Json -Compress", {"name": _task_name(name)}))["state"]


def stop_task(name: str, *, pointer_file: str | Path | None = None) -> dict:
    native.require_system()
    if pointer_file is None:
        raise native.WindowsIsolationError("Confirmed Warden stop requires its protected release pointer")
    native.validate_private_path(pointer_file)
    control = Path(pointer_file).parent / "warden-process.json"
    state = _task_state(name)
    current_boot = _current_boot_id()
    deadline = time.monotonic() + 10
    record = None
    job = None
    while job is None:
        if control.exists():
            record = _read_control(control)
            exited = _record_exited(record, current_boot)
            if exited and state in {"Running", "Queued"}:
                state = _task_state(name)
            if exited and state not in {"Running", "Queued"}:
                return {**_task_operation(name, "stop"), "tree_exit_verified": True}
            # Do not kill the launcher in the CreateProcess/AssignProcess gap.
            # It publishes containment before resuming the suspended child.
            if not exited and record["contained"]:
                try:
                    job = _duplicate_recorded_job(record)
                    break
                except native.WindowsIsolationError:
                    if time.monotonic() >= deadline:
                        raise native.WindowsCleanupError("Warden ownership is stale or unavailable; process-tree exit cannot be confirmed") from None
        elif state not in {"Running", "Queued"}:
            return {**_task_operation(name, "stop"), "tree_exit_verified": True}
        if time.monotonic() >= deadline:
            raise native.WindowsCleanupError("Running Warden did not publish a verifiable process-tree ownership record")
        time.sleep(0.05)
    try:
        try:
            result = _task_operation(name, "stop")
        finally:
            native._check(native._api()["kernel32"].TerminateJobObject(job, 1), "Terminate retained Warden process tree")
            _wait_job_empty(job)
            _write_control(control, {**record, "closed": True})
        return {**result, "tree_exit_verified": True}
    finally:
        native._close(job)


def start_task(name: str) -> dict:
    return _task_operation(name, "start")


def restart_task(name: str, *, pointer_file: str | Path | None = None) -> dict:
    stop_task(name, pointer_file=pointer_file)
    return start_task(name)


def run_system_child(argv: list[str], cwd: str | Path, *, control_file: str | Path | None = None) -> int:
    """Run the launcher's already-verified pipeline release in a kill-on-close job.

    This is not a repair/test entrypoint. The caller must verify its protected
    release pointer and source hash before passing the fixed bootstrap argv.
    Stopping the Scheduled Task closes the launcher's job handle and terminates
    its pipeline child and nested worker process trees.
    """
    native.require_system()
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise ValueError("System child requires an explicit argv without NULs")
    native.validate_code_path(argv[0])
    native.validate_code_path(cwd)
    control = Path(control_file) if control_file is not None else None
    if control is not None:
        native.validate_private_directory(control.parent)
        if control.exists() and not _record_exited(_read_control(control), _current_boot_id()):
            raise native.WindowsCleanupError("Previous Warden tree lacks a confirmed cleanup receipt; do not overwrite its ownership record")
    api = native._api()
    create = api["kernel32"].CreateProcessW
    create.restype = native.BOOL
    create.argtypes = [native.LPWSTR, native.LPWSTR, native.HANDLE, native.HANDLE, native.BOOL,
                       native.DWORD, native.HANDLE, native.LPWSTR, C.POINTER(native._STARTUPINFOW),
                       C.POINTER(native._PROCESS_INFORMATION)]
    job = native._check(api["kernel32"].CreateJobObjectW(None, None), "Create launcher process-tree job")
    process = native._PROCESS_INFORMATION()
    record = None
    try:
        limit = native._EXTENDED_LIMIT()
        limit.BasicLimitInformation.LimitFlags = 0x2000
        native._check(api["kernel32"].SetInformationJobObject(job, 9, C.byref(limit), C.sizeof(limit)),
                      "Enable launcher kill-on-close containment")
        if control is not None:
            record = {"schema": 1, "pid": os.getpid(), "created": _process_creation_time(api["kernel32"].GetCurrentProcess()),
                      "job_handle": int(job), "closed": False, "contained": False, "boot_id": _current_boot_id()}
            _write_control(control, record)
        startup = native._STARTUPINFOW(cb=C.sizeof(native._STARTUPINFOW))
        command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
        native._check(create(argv[0], command, None, None, False, 0x4 | 0x08000000, None, str(cwd),
                             C.byref(startup), C.byref(process)), "Create suspended verified Warden")
        native._check(api["kernel32"].AssignProcessToJobObject(job, process.hProcess), "Contain verified Warden before resume")
        if control is not None and record is not None:
            record["contained"] = True
            _write_control(control, record)
        if api["kernel32"].ResumeThread(process.hThread) == 0xFFFFFFFF:
            raise native.WindowsIsolationError("Could not resume verified Warden")
        native._close(process.hThread)
        process.hThread = None
        status = api["kernel32"].WaitForSingleObject(process.hProcess, 0xFFFFFFFF)
        if status != 0:
            raise native.WindowsIsolationError("Verified Warden wait failed")
        code = native.DWORD()
        native._check(api["kernel32"].GetExitCodeProcess(process.hProcess, C.byref(code)), "Read Warden exit status")
        return code.value
    finally:
        # Assignment can fail before the suspended process is in the job. Its
        # explicit process handle still permits termination in that case.
        if process.hProcess:
            api["kernel32"].TerminateProcess(process.hProcess, 1)
        try:
            if job:
                native._check(api["kernel32"].TerminateJobObject(job, 1), "Terminate verified Warden process tree")
                deadline = time.monotonic() + 10
                while True:
                    accounting = native._BASIC_ACCOUNTING()
                    native._check(api["kernel32"].QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), None),
                                  "Confirm verified Warden descendants have exited")
                    if not accounting.ActiveProcesses:
                        break
                    if time.monotonic() >= deadline:
                        raise native.WindowsCleanupError("Verified Warden descendants remain active; do not reuse managed state")
                    time.sleep(0.05)
            if process.hProcess and api["kernel32"].WaitForSingleObject(process.hProcess, 10000) != 0:
                raise native.WindowsCleanupError("Verified Warden process termination was not confirmed")
            if control is not None and record is not None:
                _write_control(control, {**record, "closed": True})
        finally:
            native._close(job)
            native._close(process.hThread)
            native._close(process.hProcess)


def provision_supervisor(private_root: str | Path, workspace: str | Path, operator_name: str,
                         login_log_root: str | Path) -> dict:
    """Provision only the repair account; existing pipeline accounts remain separate."""
    result = native.provision_layout(private_root, {"repair": Path(workspace)}, {"repair": REPAIR_IDENTITY})
    result["slots"]["repair"]["credential_target"] = REPAIR_IDENTITY.credential_target
    result["operator_name"] = operator_name
    result["login_log_root"] = str(Path(login_log_root).absolute())
    return result


def configure_tasks(config_file: str | Path, previous_source: str | Path) -> dict:
    """Bootstrap a reviewed initial release and migrate task actions without starting work.

    The old Warden task XML/source record are saved before changing its action.
    Existing pointer and rollback files are preserved on subsequent invocations.
    Automated repair candidates never use this administrator-invoked path.
    """
    native.require_system()
    filename = Path(config_file).resolve(strict=True)
    native.validate_code_path(filename)
    from .config import load_config
    config = load_config(filename)
    require_supervisor(config)
    _protected_tree(previous_source)
    from .releases import ReleaseStore, manifest_digest, tree_manifest
    private = Path(config["private_root"])
    rollback = private / "installation-rollback"
    rollback.mkdir(exist_ok=True)
    native.validate_private_directory(rollback)
    previous = rollback / "previous-source.json"
    if not previous.exists():
        previous.write_text(json.dumps({"source_root": str(Path(previous_source).resolve()),
                                        "digest": manifest_digest(tree_manifest(Path(previous_source)))}, indent=2), encoding="utf-8")
    pointer = Path(config["pointer_file"])
    releases = ReleaseStore(Path(config["release_root"]), pointer, private / "release-journal.json")
    if not pointer.exists():
        releases.bootstrap(Path(config["baseline_source"]))
    native.validate_private_path(pointer)
    supervisor_python = str(Path(sys.executable).resolve())
    warden_arguments = subprocess.list2cmdline([
        "-I", "-m", "cochem_supervisor.launcher", "--pointer", str(pointer),
        "--python", str(config["pipeline_python"]), "--config", str(config["pipeline_config"]),
    ])
    supervisor_arguments = subprocess.list2cmdline(["-I", "-m", "cochem_supervisor", "daemon", "--config", str(filename)])
    task_data = {
        "warden": _task_name(config.get("warden_task", "CoChem-4.2.2-Warden")),
        "supervisor": _task_name(config.get("supervisor_task", "CoChem-4.2.3-Supervisor")),
        "backup": str(rollback / "warden-task.xml"), "python": supervisor_python,
        "working_directory": str(Path(sys.executable).resolve().parents[2]),
        "warden_arguments": warden_arguments, "supervisor_arguments": supervisor_arguments,
    }
    script = (
        "$existing=Get-ScheduledTask -TaskName $data.warden -TaskPath '\\' -ErrorAction Stop; "
        "if ($existing.Principal.UserId -notin @('SYSTEM','NT AUTHORITY\\SYSTEM','S-1-5-18')) { throw 'Existing Warden must be SYSTEM' }; "
        "$previousWarden=Export-ScheduledTask -TaskName $data.warden -TaskPath '\\'; "
        "$previousSupervisor=$null; "
        "if (Get-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -ErrorAction SilentlyContinue) { "
        "$previousSupervisor=Export-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' }; "
        "if (-not (Test-Path -LiteralPath $data.backup)) { "
        "[IO.File]::WriteAllText($data.backup,$previousWarden,[Text.Encoding]::Unicode) }; "
        "try { "
        "$wardenAction=New-ScheduledTaskAction -Execute $data.python -Argument $data.warden_arguments -WorkingDirectory $data.working_directory; "
        "Set-ScheduledTask -TaskName $data.warden -TaskPath '\\' -Action $wardenAction | Out-Null; "
        "$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew "
        "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
        "$action=New-ScheduledTaskAction -Execute $data.python -Argument $data.supervisor_arguments -WorkingDirectory $data.working_directory; "
        "Register-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -Action $action -Principal $principal "
        "-Settings $settings -Trigger (New-ScheduledTaskTrigger -AtStartup) -Force | Out-Null; "
        "$service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); $folder=$service.GetFolder('\\'); "
        "foreach ($name in @($data.warden,$data.supervisor)) { "
        "$folder.GetTask($name).SetSecurityDescriptor('O:SYG:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)',0) }; "
        "} catch { "
        "$failure=$_; Register-ScheduledTask -TaskName $data.warden -TaskPath '\\' -Xml $previousWarden -Force | Out-Null; "
        "if ($null -ne $previousSupervisor) { "
        "Register-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -Xml $previousSupervisor -Force | Out-Null "
        "} elseif (Get-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -ErrorAction SilentlyContinue) { "
        "Unregister-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -Confirm:$false }; throw $failure }; "
        "@{warden=$data.warden;supervisor=$data.supervisor;started=$false;rollback_xml=$data.backup} | ConvertTo-Json -Compress"
    )
    return json.loads(native._powershell(script, task_data))


def main() -> None:
    parser = argparse.ArgumentParser(description="Native independent CoChem supervisor deployment controls")
    sub = parser.add_subparsers(dest="operation", required=True)
    provision = sub.add_parser("provision")
    provision.add_argument("--private-root", required=True)
    provision.add_argument("--repair-workspace", required=True)
    provision.add_argument("--operator-name", required=True)
    provision.add_argument("--layout-output", required=True)
    provision.add_argument("--login-log-root", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--config", required=True)
    configure = sub.add_parser("configure")
    configure.add_argument("--config", required=True)
    configure.add_argument("--previous-source", required=True)
    args = parser.parse_args()
    if args.operation == "provision":
        result = provision_supervisor(args.private_root, args.repair_workspace, args.operator_name, args.login_log_root)
        output = Path(args.layout_output)
        native.validate_code_path(output.parent)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif args.operation == "configure":
        result = configure_tasks(args.config, args.previous_source)
    else:
        native.validate_code_path(args.config)
        from .config import load_config
        result = require_supervisor(load_config(args.config))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
