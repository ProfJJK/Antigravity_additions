"""Native Windows controls for the independently installed repair supervisor.

This adapter never imports or executes a repair candidate. Its process/ACL
primitives come from the supervisor's own protected package installation,
independent of whichever pipeline release the Warden currently runs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes as C
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping
import uuid

from cochem_pipeline import windows as native


REPAIR_IDENTITY = native.WorkerIdentity("CoChem423Repair", "CoChem423/repair")


def verify_repair_execution_limits(config: Mapping) -> dict:
    """Measure topology and memory before paid repair, without stopping recovery."""
    native.require_system()
    import psutil
    from cochem_pipeline.resource_limits import ResourceLimits, ResourcePolicyError, validate_host_limits
    from cochem_pipeline.resource_telemetry import _commit
    limits = ResourceLimits.from_dict(config.get('repair_execution_limits'))
    topology = validate_host_limits(limits)
    available_mb = psutil.virtual_memory().available / (1024*1024)
    commit = _commit()
    # Reserve the child's full hard cap, plus the same baseline RAM/commit
    # headroom used by the default pipeline policy. No model can relax this.
    if available_mb < limits.memory_limit_mb + 1024:
        raise ResourcePolicyError('Insufficient measured RAM for the bounded repair child plus reserve')
    if not commit.get('available') or commit.get('free_mb',0) < limits.memory_limit_mb + 6144:
        raise ResourcePolicyError('Insufficient verified Windows commit headroom for the bounded repair child')
    return {'limits':limits.as_dict(),'topology':topology,'available_memory_mb':available_mb,
            'commit':commit,'ram_reserve_mb':1024,'commit_reserve_mb':6144,'checked_at':time.time()}


def repair_docker_endpoint(pipeline: Mapping) -> str | None:
    """Read the reviewed Docker policy without treating malformed input as off."""
    if not isinstance(pipeline, Mapping):
        raise ValueError('The reviewed pipeline configuration must be an object')
    if 'docker' in pipeline and not isinstance(pipeline['docker'], dict):
        raise ValueError('An explicit Docker policy must be an object')
    from cochem_pipeline.container_policy import DockerPolicy
    policy = DockerPolicy.from_dict(pipeline.get('docker'))
    if not policy.enabled:
        return None
    if not re.fullmatch(r'npipe:////\./pipe/([A-Za-z0-9_.-]+)', policy.endpoint or ''):
        raise ValueError('Repair isolation requires the configured local Windows Docker pipe')
    return policy.endpoint


def verify_repair_docker_boundary(config: Mapping, identity: Mapping | None = None) -> dict:
    """Prove repair-account denial immediately before untrusted process work.

    This is deliberately not a supervisor startup requirement: an unavailable
    daemon must hold repairs while the independent watchdog can recover it.
    Only real ACCESS_DENIED on read, write and duplex opens is accepted.
    """
    native.require_system()
    filename = Path(config['pipeline_config'])
    native.validate_code_path(filename)
    pipeline = json.loads(filename.read_text(encoding='utf-8-sig'))
    endpoint = repair_docker_endpoint(pipeline)
    expected = native.WorkerIdentity(**config['repair_worker'])
    if identity is not None and native.WorkerIdentity(**identity) != expected:
        raise native.WindowsIsolationError('Repair execution identity does not match its reviewed Docker boundary')
    # Imported from this frozen supervisor installation, never a candidate.
    from cochem_pipeline.deployment import verify_docker_access_boundary
    return {'enabled': endpoint is not None,
            'scope': 'configured and existing known local Desktop API pipes',
            'worker_access': verify_docker_access_boundary(endpoint, {'repair': expected},
                required=endpoint is not None,trusted_operator=pipeline.get('operator_name'),
                trusted_server_executables=pipeline.get('docker',{}).get('pipe_server_executables',[]))}


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
        "snapshot": "",
        "disable": "Disable-ScheduledTask -InputObject $task | Out-Null;",
        "enable": "Enable-ScheduledTask -InputObject $task | Out-Null;",
        "start": (
            "if (-not $task.Settings.Enabled) { throw 'Administrator-disabled task cannot be started without owned enable intent' }; "
            "if ($registered.GetInstances(0).Count -eq 0) { Start-ScheduledTask -InputObject $task };"
        ),
        "stop": (
            "if ($task.Settings.Enabled) { throw 'Disable task scheduling before confirming process-tree stop' }; "
            "Stop-ScheduledTask -InputObject $task; $deadline=(Get-Date).AddSeconds(30); "
            "do { $task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\'; "
            "if ($task.Settings.Enabled) { throw 'Task scheduling was enabled during controlled stop' }; "
            "if ($registered.GetInstances(0).Count -eq 0 -and $task.State -notin @('Running','Queued')) { break }; "
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
        "$service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); "
        "$registered=$service.GetFolder('\\').GetTask($data.name); "
        + commands[operation]
        + "$task=Get-ScheduledTask -TaskName $data.name -TaskPath '\\'; "
        "@{name=$task.TaskName;state=[string]$task.State;enabled=[bool]$task.Settings.Enabled;"
        "instances=[int]$registered.GetInstances(0).Count;operation=$data.operation} | ConvertTo-Json -Compress"
    )
    result = _validate_task_snapshot(json.loads(native._powershell(script, {"name": name, "operation": operation})))
    if operation == "disable" and result["enabled"]:
        raise native.WindowsIsolationError("Task scheduling could not be disabled")
    if operation == "enable" and not result["enabled"]:
        raise native.WindowsIsolationError("Owned task scheduling could not be restored")
    if operation == "stop" and (result["enabled"] or _task_active(result)):
        raise native.WindowsCleanupError("Managed task scheduling or instances remain active after stop")
    return result


def _validate_task_snapshot(value: object) -> dict:
    if (not isinstance(value, dict) or type(value.get("enabled")) is not bool
            or type(value.get("instances")) is not int or value["instances"] < 0
            or value.get("state") not in ("Ready", "Running", "Queued", "Disabled")):
        raise native.WindowsIsolationError("Scheduled Task did not return a reliable state/instance snapshot")
    return dict(value)


def _task_active(snapshot: dict) -> bool:
    snapshot = _validate_task_snapshot(snapshot)
    return snapshot["instances"] > 0 or snapshot["state"] in {"Running", "Queued"}


def _validate_task_marker(value: object, name: str) -> dict:
    expected = {"schema", "task_name", "original_enabled", "phase"}
    if (not isinstance(value, dict) or set(value) != expected or type(value["schema"]) is not int
            or value["schema"] != 1 or value["task_name"] != _task_name(name)
            or type(value["original_enabled"]) is not bool
            or value["phase"] not in ("DISABLING", "DISABLED", "STOPPED", "STARTING")):
        raise native.WindowsIsolationError("Invalid protected Scheduled Task control intent")
    return dict(value)


def _stop_intent(name: str, snapshot: dict, existing: dict | None = None) -> dict:
    """Persist the first observed enabled intent across interrupted stop retries."""
    snapshot = _validate_task_snapshot(snapshot)
    if existing is not None:
        original = _validate_task_marker(existing, name)["original_enabled"]
    else:
        original = snapshot["enabled"]
    return {"schema": 1, "task_name": _task_name(name), "original_enabled": original, "phase": "DISABLING"}


def _start_intent(name: str, snapshot: dict, marker: dict | None) -> bool:
    """Return whether this controlled start owns authority to enable scheduling."""
    snapshot = _validate_task_snapshot(snapshot)
    if marker is None:
        if not snapshot["enabled"]:
            raise native.WindowsIsolationError("Warden task is administrator-disabled; no supervisor stop intent permits enabling it")
        return False
    marker = _validate_task_marker(marker, name)
    if not marker["original_enabled"]:
        raise native.WindowsIsolationError("Warden task was administrator-disabled before the controlled stop; enabling requires operator action")
    if marker["phase"] not in {"STOPPED", "STARTING"}:
        raise native.WindowsCleanupError("Complete the interrupted controlled stop before restoring task scheduling")
    if snapshot["enabled"] or _task_active(snapshot):
        raise native.WindowsCleanupError("Owned task scheduling changed before controlled start; repeat the disabled stop proof")
    return True


def _read_task_marker(path: Path, name: str) -> dict | None:
    if not path.exists() and not path.is_symlink():
        return None
    native.validate_private_path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 4096:
        raise native.WindowsIsolationError("Scheduled Task intent must be a bounded private ordinary file")
    return _validate_task_marker(json.loads(path.read_text(encoding="utf-8")), name)


def _write_task_marker(path: Path, name: str, marker: dict) -> None:
    _write_control(path, _validate_task_marker(marker, name))


@contextmanager
def _task_control_lock(name: str):
    """Serialize independent privileged callers; an abandoned owner is recoverable."""
    native.require_system()
    api = native._api()["kernel32"]
    identifier = hashlib.sha256(_task_name(name).casefold().encode("utf-8")).hexdigest()
    lock = native._check(api.CreateMutexW(None, False, "Global\\CoChemTaskControl-" + identifier), "Open task control mutex")
    acquired = False
    try:
        status = api.WaitForSingleObject(lock, 30000)
        if status not in (0, 0x80):
            raise native.WindowsIsolationError("Another controller still owns Scheduled Task lifecycle control")
        acquired = True
        yield
    finally:
        try:
            if acquired:
                release = api.ReleaseMutex
                release.restype = native.BOOL
                release.argtypes = [native.HANDLE]
                native._check(release(lock), "Release task control mutex")
        finally:
            native._close(lock)


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
    return native.current_boot_identity()


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
    _validate_containment_id(data.get("containment_id"))
    return data


def _validate_containment_id(value: object) -> str | None:
    # Old records intentionally have no same-boot guard-clearing authority.
    if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None):
        raise native.WindowsIsolationError("Invalid protected Warden containment identity")
    return value


def _warden_environment(containment_id: str | None, inherited: Mapping[str, str]) -> str:
    """Bind exactly this launcher-owned Job Object, never an inherited scope."""
    _validate_containment_id(containment_id)
    environment = {key: value for key, value in inherited.items()
                   if key.upper() != "COCHEM_WARDEN_CONTAINMENT_ID"}
    if containment_id is not None:
        environment["COCHEM_WARDEN_CONTAINMENT_ID"] = containment_id
    return "\x00".join(f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + "\x00\x00"


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
    return _task_operation(name, "snapshot")["state"]


def _task_control_paths(pointer_file: str | Path | None) -> tuple[Path, Path]:
    if pointer_file is None:
        raise native.WindowsIsolationError("Controlled Warden start/stop requires its protected release pointer")
    native.validate_private_path(pointer_file)
    root = Path(pointer_file).parent
    native.validate_private_directory(root)
    return root / "warden-process.json", root / "warden-task-control.json"


def _can_finish_without_retained_job(snapshot: dict, record: dict | None, current_boot: int) -> bool:
    """A closed old record is useful only after all scheduling is disabled."""
    snapshot = _validate_task_snapshot(snapshot)
    if snapshot["enabled"] or _task_active(snapshot):
        return False
    return record is None or _record_exited(record, current_boot)


def _stop_task_locked(name: str, control: Path, marker_path: Path) -> dict:
    snapshot = _task_operation(name, "snapshot")
    marker = _stop_intent(name, snapshot, _read_task_marker(marker_path, name))
    # Write intent before changing Task Scheduler. If either process crashes,
    # the next controller retains the original enabled state and finishes stop.
    _write_task_marker(marker_path, name, marker)
    _task_operation(name, "disable")
    marker = {**marker, "phase": "DISABLED"}
    _write_task_marker(marker_path, name, marker)
    current_boot = _current_boot_id()
    deadline = time.monotonic() + 10
    record = None
    job = None
    while job is None:
        # Disabling prevents the restart/trigger race between this snapshot and
        # Stop-ScheduledTask. GetInstances also sees a running instance when the
        # disabled registration's displayed state alone is not informative.
        snapshot = _task_operation(name, "snapshot")
        if snapshot["enabled"]:
            raise native.WindowsCleanupError("Task scheduling was reenabled during controlled stop")
        if control.exists():
            record = _read_control(control)
            exited = _record_exited(record, current_boot)
            if _can_finish_without_retained_job(snapshot, record, current_boot):
                result = _task_operation(name, "stop")
                _write_task_marker(marker_path, name, {**marker, "phase": "STOPPED"})
                return {**result, "tree_exit_verified": True, "scheduling_disabled": True,
                        "original_enabled": marker["original_enabled"]}
            # Do not kill the launcher in the CreateProcess/AssignProcess gap.
            # It publishes containment before resuming the suspended child.
            if not exited and record["contained"]:
                try:
                    job = _duplicate_recorded_job(record)
                    break
                except native.WindowsIsolationError:
                    if time.monotonic() >= deadline:
                        raise native.WindowsCleanupError("Warden ownership is stale or unavailable; process-tree exit cannot be confirmed") from None
        elif _can_finish_without_retained_job(snapshot, None, current_boot):
            result = _task_operation(name, "stop")
            _write_task_marker(marker_path, name, {**marker, "phase": "STOPPED"})
            return {**result, "tree_exit_verified": True, "scheduling_disabled": True,
                    "original_enabled": marker["original_enabled"]}
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
        _write_task_marker(marker_path, name, {**marker, "phase": "STOPPED"})
        result = {**result, "tree_exit_verified": True, "scheduling_disabled": True,
                  "original_enabled": marker["original_enabled"]}
        # Only this path retained the actual Job Object and measured it empty.
        # Closed-file records alone never authorize same-boot guard recovery.
        if record.get("containment_id") is not None:
            result.update(stopped_containment_id=record["containment_id"], stopped_boot_id=record["boot_id"])
        return result
    finally:
        native._close(job)


def stop_task(name: str, *, pointer_file: str | Path | None = None) -> dict:
    """Disable future scheduling first, then prove the current process tree empty.

    The durable private marker deliberately remains after success. Only a
    subsequent controlled start may restore the previous enabled intent.
    """
    native.require_system()
    name = _task_name(name)
    control, marker_path = _task_control_paths(pointer_file)
    with _task_control_lock(name):
        return _stop_task_locked(name, control, marker_path)


def start_task(name: str, *, pointer_file: str | Path | None = None) -> dict:
    """Restore only scheduling disabled by our stop, never an admin-disabled task."""
    native.require_system()
    name = _task_name(name)
    control, marker_path = _task_control_paths(pointer_file)
    with _task_control_lock(name):
        marker = _read_task_marker(marker_path, name)
        snapshot = _task_operation(name, "snapshot")
        if marker is not None:
            if not marker["original_enabled"]:
                raise native.WindowsIsolationError("Warden task was administrator-disabled before controlled stop; operator action is required")
            # An interrupted enable/start may already have launched a process.
            # Repeat the disabled stop proof instead of assuming it never ran.
            _stop_task_locked(name, control, marker_path)
            marker = _read_task_marker(marker_path, name)
            snapshot = _task_operation(name, "snapshot")
        enable = _start_intent(name, snapshot, marker)
        if marker is not None:
            _write_task_marker(marker_path, name, {**marker, "phase": "STARTING"})
        if enable:
            _task_operation(name, "enable")
        result = _task_operation(name, "start")
        if marker is not None:
            native.validate_private_path(marker_path)
            marker_path.unlink()
        return {**result, "scheduling_restored": enable}


def restart_task(name: str, *, pointer_file: str | Path | None = None) -> dict:
    stop_task(name, pointer_file=pointer_file)
    return start_task(name, pointer_file=pointer_file)


def _matching_fresh_upgrade_receipt(value: object, source: Path, target: Path) -> bool:
    """A previous reviewed fresh install may repeat without inventing history."""
    return (isinstance(value, dict)
            and set(value) == {"source_private", "target_private", "status", "quarantine_preserved"}
            and value.get("status") == "NO_PRIOR_LEDGER"
            and value.get("quarantine_preserved") is False
            and value.get("source_private") == str(source.absolute())
            and value.get("target_private") == str(target.absolute()))


def migrate_ledger(source_private: str | Path, target_private: str | Path, supervisor_task: str) -> dict:
    """Administrator upgrade gate: preserve budgets, never resume an old daemon."""
    native.require_system()
    native.validate_private_directory(target_private)
    source = Path(source_private)
    target = Path(target_private)
    if source.exists() or source.is_symlink():
        native.validate_private_directory(source)
        for filename in ('supervisor.db', 'supervisor.db-wal', 'supervisor.db-shm',
                         'repair-quarantine.json', 'release-journal.json'):
            item = source / filename
            if item.exists() or item.is_symlink():
                native.validate_private_path(item)
    prior_fresh = False
    receipt = target / "budget-upgrade.json"
    source_ledger_exists = (source / "supervisor.db").exists()
    if not source_ledger_exists and (receipt.exists() or receipt.is_symlink()):
        native.validate_private_path(receipt)
        if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_nlink != 1 or receipt.stat().st_size > 16384:
            raise native.WindowsIsolationError("Budget upgrade receipt must be a bounded private ordinary file")
        prior_fresh = _matching_fresh_upgrade_receipt(json.loads(receipt.read_text(encoding="utf-8")), source, target)
    native._powershell(
        "$service=New-Object -ComObject 'Schedule.Service'; $service.Connect(); "
        "$registered=$null; foreach ($candidate in $service.GetFolder('\\').GetTasks(1)) { "
        "if ($candidate.Name -eq $data.name) { $registered=$candidate; break } }; "
        "if ($null -ne $registered) { "
        "if (-not $data.source_ledger_exists -and -not $data.prior_fresh) { throw 'Existing supervisor task requires the actual previous supervisor.db budget ledger or its exact prior fresh-install receipt; refusing to assume zero historical spend' }; "
        "if ($registered.GetInstances(0).Count -gt 0 -or $registered.State -in @(2,4) -or $registered.Enabled) "
        "{ throw 'Stop and disable the previous supervisor task before copying its budget ledger' } }",
        {'name': _task_name(supervisor_task), 'source_ledger_exists': source_ledger_exists, 'prior_fresh': prior_fresh})
    from .upgrade import migrate_budget_state
    result = migrate_budget_state(source, Path(target_private))
    for filename in ('supervisor.db', 'repair-quarantine.json', 'budget-upgrade.json'):
        item = Path(target_private) / filename
        if item.exists():
            native.validate_private_path(item)
    return result


def run_system_child(argv: list[str], cwd: str | Path, *, control_file: str | Path | None = None,
                     on_tree_exit: Callable[[dict], None] | None = None) -> int:
    """Run the launcher's already-verified pipeline release in a kill-on-close job.

    This is not a repair/test entrypoint. The caller must verify its protected
    release pointer and source hash before passing the fixed bootstrap argv.
    Stopping the Scheduled Task closes the launcher's job handle and terminates
    its pipeline child and nested worker process trees.
    """
    native.require_system()
    if on_tree_exit is not None and not callable(on_tree_exit):
        raise ValueError("Tree-exit reconciliation must be a trusted callable")
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
    containment_id = uuid.uuid4().hex if control is not None else None
    try:
        limit = native._EXTENDED_LIMIT()
        limit.BasicLimitInformation.LimitFlags = 0x2000
        native._check(api["kernel32"].SetInformationJobObject(job, 9, C.byref(limit), C.sizeof(limit)),
                      "Enable launcher kill-on-close containment")
        if control is not None:
            record = {"schema": 1, "pid": os.getpid(), "created": _process_creation_time(api["kernel32"].GetCurrentProcess()),
                      "job_handle": int(job), "closed": False, "contained": False, "boot_id": _current_boot_id(),
                      "containment_id": containment_id}
            _write_control(control, record)
        startup = native._STARTUPINFOW(cb=C.sizeof(native._STARTUPINFOW))
        command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = C.create_unicode_buffer(_warden_environment(containment_id, os.environ))
        native._check(create(argv[0], command, None, None, False, 0x4 | 0x400 | 0x08000000, environment, str(cwd),
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
                # Reconcile at the actual live-handle proof, including ordinary
                # Warden crashes before Task Scheduler starts a new instance.
                # A later reader of a closed JSON record has no such authority.
                if on_tree_exit is not None:
                    on_tree_exit({"stopped_containment_id": record["containment_id"],
                                  "stopped_boot_id": record["boot_id"],
                                  "tree_exit_verified": True, "launcher_exit_verified": True})
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
        # This is an administrator-reviewed action replacement. The installer
        # requires the previous Warden disabled during upgrade; restore that
        # registration only after both replacement actions and ACLs are ready.
        "Enable-ScheduledTask -TaskName $data.warden -TaskPath '\\' | Out-Null; "
        "} catch { "
        "$failure=$_; Register-ScheduledTask -TaskName $data.warden -TaskPath '\\' -Xml $previousWarden -Force | Out-Null; "
        "if ($null -ne $previousSupervisor) { "
        "Register-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -Xml $previousSupervisor -Force | Out-Null "
        "} elseif (Get-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -ErrorAction SilentlyContinue) { "
        "Unregister-ScheduledTask -TaskName $data.supervisor -TaskPath '\\' -Confirm:$false }; throw $failure }; "
        "@{warden=$data.warden;supervisor=$data.supervisor;started=$false;rollback_xml=$data.backup} | ConvertTo-Json -Compress"
    )
    result = json.loads(native._powershell(script, task_data))
    # A reviewed configure operation is explicit operator authorization to
    # enable the new action, unlike an automatic recovery start. It supersedes
    # the old scheduling intent but never deletes process-tree ownership proof.
    marker = private / "warden-task-control.json"
    if _read_task_marker(marker, task_data["warden"]) is not None:
        marker.unlink()
    return result


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
    migrate = sub.add_parser("migrate-ledger")
    migrate.add_argument("--source-private", required=True)
    migrate.add_argument("--target-private", required=True)
    migrate.add_argument("--supervisor-task", required=True)
    args = parser.parse_args()
    if args.operation == "provision":
        result = provision_supervisor(args.private_root, args.repair_workspace, args.operator_name, args.login_log_root)
        output = Path(args.layout_output)
        native.validate_code_path(output.parent)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    elif args.operation == "configure":
        result = configure_tasks(args.config, args.previous_source)
    elif args.operation == "migrate-ledger":
        result = migrate_ledger(args.source_private, args.target_private, args.supervisor_task)
    else:
        native.validate_code_path(args.config)
        from .config import load_config
        result = require_supervisor(load_config(args.config))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
