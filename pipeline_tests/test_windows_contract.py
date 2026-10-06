"""Real platform contracts. Windows integration tests require a provisioned SYSTEM host.

No test substitutes fake Win32 calls, privileges, credentials, or job objects.
Skipped Windows integration checks are not evidence of Windows operation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

from cochem_pipeline.windows import (
    WorkerIdentity, WindowsIsolationError, _layout_paths, launch_worker,
    require_system, validate_layout, validate_private_directory,
    validate_controller_token, protect_controller_token,
    current_boot_identity,
)


@pytest.mark.parametrize("name", ["", "SYSTEM", "a" * 21, "two words", "../outside", "user\\other"])
def test_worker_identity_rejects_invalid_local_account_names(name):
    if name == "SYSTEM":
        # Syntax can be valid while security depends on the real native token.
        assert WorkerIdentity(name, "test-target").name == "SYSTEM"
    else:
        with pytest.raises(ValueError):
            WorkerIdentity(name, "test-target")


def test_layout_rejects_shared_identities_and_nested_workspaces(tmp_path):
    first, second = tmp_path / "slot1", tmp_path / "slot2"
    same = WorkerIdentity("ExampleWorker", "example-target")
    with pytest.raises(ValueError, match="distinct"):
        _layout_paths(tmp_path / "private", {"one": first, "two": second}, {"one": same, "two": same})
    with pytest.raises(ValueError, match="disjoint"):
        _layout_paths(first, {"one": first / "nested"}, {"one": same})


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows fail-closed contract")
def test_nonwindows_does_not_claim_system_or_windows_isolation(tmp_path):
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        require_system()
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        current_boot_identity()
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        validate_private_directory(tmp_path)
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        validate_layout(tmp_path / "private", {}, {})
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        launch_worker(WorkerIdentity("ExampleWorker", "example-target"), [sys.executable], tmp_path,
                      tmp_path / "stdin", tmp_path / "stdout", tmp_path / "stderr")
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        protect_controller_token(tmp_path / "token", "ExampleOperator")


@pytest.fixture
def native_windows_layout():
    if os.name != "nt":
        pytest.skip("Requires actual Windows; no Win32 emulation used")
    filename = os.environ.get("COCHEM_WINDOWS_TEST_LAYOUT")
    if not filename:
        pytest.skip("Set COCHEM_WINDOWS_TEST_LAYOUT to a real provisioned windows-layout.json and run pytest as SYSTEM")
    require_system()
    data = json.loads(Path(filename).read_text(encoding="utf-8"))
    identities = {slot: WorkerIdentity(spec["identity"], spec["credential_target"]) for slot, spec in data["slots"].items()}
    roots = {slot: Path(spec["root"]) for slot, spec in data["slots"].items()}
    validate_layout(Path(data["private_root"]), roots, identities)
    return data, roots, identities


def test_real_windows_layout_and_controller_acl(native_windows_layout):
    data, roots, identities = native_windows_layout
    validate_private_directory(data["private_root"])
    validate_controller_token(data["token_file"], data["operator_name"], identities)
    assert len(roots) == len({identity.name for identity in identities.values()})
    boot = current_boot_identity()
    assert type(boot) is int and boot > 0 and current_boot_identity() == boot


@pytest.mark.parametrize("failure", ["invalid_environment", "duplicate_output"])
def test_real_failed_launch_releases_profile_and_identity_only_after_cleanup(native_windows_layout, failure):
    data, roots, identities = native_windows_layout
    slot = next(iter(roots))
    directory = Path(data["private_root"]) / ("launch-failure-check-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        with tempfile.TemporaryFile(mode="w+b") as prompt:
            expected = ValueError if failure == "invalid_environment" else FileExistsError
            overrides = {"FORBIDDEN_VARIABLE": "rejected"} if failure == "invalid_environment" else None
            with pytest.raises(expected):
                # Both triggers occur after LoadUserProfile. The duplicate
                # output path also occurs after real Job Object creation.
                launch_worker(identities[slot], [sys.executable, "-I", "-c", "raise SystemExit(9)"], roots[slot],
                              prompt, directory / "duplicate", directory / "duplicate", overrides)
            with launch_worker(identities[slot], [sys.executable, "-I", "-c", "raise SystemExit(0)"], roots[slot],
                               prompt, directory / "stdout", directory / "stderr") as process:
                # A second real launch cannot acquire the exclusive identity
                # reservation unless verified failure cleanup released it.
                assert process.wait(30) == 0
    finally:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def test_real_failed_assignment_cleanup_kills_unassigned_suspended_process(native_windows_layout):
    """Exercise the actual CreateProcess/Assign gap without fake Win32 results."""
    import ctypes as C
    import psutil
    from cochem_pipeline import windows as native
    data, roots, identities = native_windows_layout
    api = native._api()["kernel32"]
    create = api.CreateProcessW
    create.restype = native.BOOL
    create.argtypes = [native.LPWSTR, native.LPWSTR, native.HANDLE, native.HANDLE, native.BOOL,
                       native.DWORD, native.HANDLE, native.LPWSTR, C.POINTER(native._STARTUPINFOW),
                       C.POINTER(native._PROCESS_INFORMATION)]
    process = native._PROCESS_INFORMATION()
    startup = native._STARTUPINFOW(cb=C.sizeof(native._STARTUPINFOW))
    command = C.create_unicode_buffer(subprocess.list2cmdline([sys.executable, "-I", "-c", "import time; time.sleep(120)"]))
    native._check(create(sys.executable, command, None, None, False, 0x4 | 0x08000000, None,
                         str(next(iter(roots.values()))), C.byref(startup), C.byref(process)), "Create real suspended cleanup test child")
    pid = process.dwProcessId
    try:
        assert psutil.pid_exists(pid)
        native._cleanup_failed_launch(process, None, None, native._PROFILEINFOW(), None)
        process.hProcess = process.hThread = None
        assert not psutil.pid_exists(pid)
    finally:
        if process.hProcess:
            api.TerminateProcess(process.hProcess, 1)
            api.WaitForSingleObject(process.hProcess, 10000)
        native._close(process.hThread)
        native._close(process.hProcess)


def test_real_worker_cannot_read_private_state_or_sibling_root(native_windows_layout):
    data, roots, identities = native_windows_layout
    if len(roots) < 2:
        pytest.skip("Native cross-account isolation check requires two provisioned identities")
    first, second = list(roots)[:2]
    identifier = uuid.uuid4().hex
    private = Path(data["private_root"]) / ("security-check-" + identifier)
    private.mkdir()
    sibling = roots[second] / ("sibling-check-" + identifier)
    sibling.mkdir()
    marker = sibling / "artifact.txt"
    marker.write_text("real private sibling artifact", encoding="utf-8")
    secret = private / "oracle-private.txt"
    secret.write_text("real private oracle artifact", encoding="utf-8")
    code = (
        "import json,pathlib; result={}\n"
        "for key,value in " + repr({"sibling": str(marker), "oracle": str(secret), "controller": data["token_file"]}) + ".items():\n"
        " try:\n  pathlib.Path(value).read_bytes(); result[key]='EXPOSED'\n"
        " except PermissionError:\n  result[key]='denied'\n"
        "print(json.dumps(result),flush=True)\n"
    )
    try:
        with tempfile.TemporaryFile(mode="w+b") as prompt:
            process = launch_worker(identities[first], [sys.executable, "-c", code], roots[first],
                                    prompt, private / "stdout", private / "stderr")
            try:
                assert process.wait(30) == 0
            finally:
                process.close()
        output = json.loads((private / "stdout").read_text(encoding="utf-8"))
        assert output == {"sibling": "denied", "oracle": "denied", "controller": "denied"}
    finally:
        marker.unlink()
        sibling.rmdir()
        for path in private.iterdir():
            path.unlink()
        private.rmdir()


def test_real_windows_job_object_kills_descendant_before_close_returns(native_windows_layout):
    data, roots, identities = native_windows_layout
    slot = next(iter(roots))
    directory = Path(data["private_root"]) / ("job-check-" + uuid.uuid4().hex)
    directory.mkdir()
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); print(p.pid,flush=True); time.sleep(120)"
    try:
        with tempfile.TemporaryFile(mode="w+b") as prompt:
            process = launch_worker(identities[slot], [sys.executable, "-c", code], roots[slot], prompt,
                                    directory / "stdout", directory / "stderr")
            try:
                deadline = time.monotonic() + 20
                while not (directory / "stdout").stat().st_size and time.monotonic() < deadline:
                    assert process.poll() is None
                    time.sleep(0.05)
                child_pid = int((directory / "stdout").read_text().strip())
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(0.01)
                process.terminate()
                process.close()
                # A separate real OS process lookup establishes child-tree exit.
                import psutil
                assert not psutil.pid_exists(child_pid)
                assert process.poll() is not None
            finally:
                process.close()
    finally:
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
