"""Real platform checks, with no simulated Win32 security or process claims.

Native tests require a real SYSTEM session and a deployed supervisor config.
Skipped checks are not evidence that Windows deployment has been validated.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

import pytest

from cochem_supervisor.windows import (
    _task_name, _walk_without_links, require_supervisor, protect_release_tree,
    run_system_child, start_task, stop_task, prepare_repair_workspace,
    _record_exited, _current_boot_id,
)
from cochem_pipeline.windows import WindowsIsolationError


@pytest.mark.parametrize("name", ["", "*", "CoChem?", "folder/task", "folder\\task", "x; Stop-Process", "x\nnext", "a" * 129])
def test_task_names_reject_wildcards_paths_and_command_text(name):
    with pytest.raises(ValueError):
        _task_name(name)


def test_task_name_preserves_literal_name():
    assert _task_name("CoChem-4.2.3-Supervisor") == "CoChem-4.2.3-Supervisor"


def test_protected_tree_inspection_rejects_real_hardlinks(tmp_path):
    original = tmp_path / "source.py"
    original.write_text("print('reviewed source')", encoding="utf-8")
    os.link(original, tmp_path / "second.py")
    with pytest.raises(WindowsIsolationError, match="hard links"):
        list(_walk_without_links(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="Creating real Windows links requires optional host policy; Linux verifies filesystem rejection")
def test_protected_tree_inspection_does_not_follow_real_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    tree = tmp_path / "release"
    tree.mkdir()
    (tree / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WindowsIsolationError, match="reparse points"):
        list(_walk_without_links(tree))


def test_protected_tree_inspection_lists_ordinary_source(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    module = source / "example.py"
    module.write_text("value = 3", encoding="utf-8")
    assert set(_walk_without_links(tmp_path)) == {tmp_path, source, module}


@pytest.mark.parametrize(("record", "current_boot", "confirmed"), [
    ({"boot_id": 101, "closed": False}, 102, True),
    ({"boot_id": 101, "closed": True}, 102, True),
    ({"boot_id": 102, "closed": False}, 102, False),
    ({"boot_id": 102, "closed": True}, 102, True),
])
def test_cleanup_policy_distinguishes_prior_boot_from_same_boot_crash(record, current_boot, confirmed):
    assert _record_exited(record, current_boot) is confirmed


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows fail-closed capability checks")
def test_nonwindows_supervisor_cannot_claim_system_or_control_tasks(tmp_path):
    for operation in (
        lambda: require_supervisor({}),
        lambda: protect_release_tree(tmp_path),
        lambda: prepare_repair_workspace(tmp_path, "ExampleRepair"),
        _current_boot_id,
        lambda: run_system_child([sys.executable, "-c", "raise SystemExit(0)"], tmp_path),
        lambda: start_task("CoChem-Test-Uncreated"),
        lambda: stop_task("CoChem-Test-Uncreated"),
    ):
        with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
            operation()


@pytest.fixture
def actual_windows_supervisor():
    if os.name != "nt":
        pytest.skip("Requires real Windows and SYSTEM; no Win32 emulation used")
    filename = os.environ.get("COCHEM_SUPERVISOR_WINDOWS_CONFIG")
    if not filename:
        pytest.skip("Set COCHEM_SUPERVISOR_WINDOWS_CONFIG to the installed supervisor.json and run its Python as SYSTEM")
    from cochem_supervisor.config import load_config
    config = load_config(filename)
    require_supervisor(config)
    return config


def test_actual_windows_independent_supervisor_layout(actual_windows_supervisor):
    config = actual_windows_supervisor
    result = require_supervisor(config)
    assert result["system"] and result["independent_installation"]
    assert result["warden_task"] != result["supervisor_task"]


def test_actual_system_child_exit_and_descendant_containment(actual_windows_supervisor):
    """Launch real native processes; a descendant must be gone when wrapper exits."""
    import psutil
    config = actual_windows_supervisor
    marker = Path(config["private_root"]) / ("supervisor-job-check-" + uuid.uuid4().hex)
    control = marker.with_suffix(".json")
    code = (
        "import pathlib,subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(120)']); "
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid),encoding='utf-8'); "
        "raise SystemExit(17)"
    )
    try:
        assert run_system_child([sys.executable, "-I", "-c", code], config["baseline_source"], control_file=control) == 17
        child_pid = int(marker.read_text(encoding="utf-8"))
        assert not psutil.pid_exists(child_pid)
        from cochem_supervisor.windows import _read_control
        assert _read_control(control)["closed"] is True
    finally:
        marker.unlink(missing_ok=True)
        control.unlink(missing_ok=True)


def test_actual_repair_account_can_modify_system_snapshot(actual_windows_supervisor):
    from cochem_pipeline.windows import WorkerIdentity, launch_worker
    import shutil
    config = actual_windows_supervisor
    identifier = "workspace-acl-check-" + uuid.uuid4().hex
    workspace = Path(config["repair_workspace"]) / identifier
    logs = Path(config["private_root"]) / identifier
    nested = workspace / "src" / "example"
    nested.mkdir(mode=0o700, parents=True)
    logs.mkdir(mode=0o700)
    module = nested / "sample.py"
    module.write_text("original", encoding="utf-8")
    identity = WorkerIdentity(**config["repair_worker"])
    try:
        prepare_repair_workspace(workspace, identity.name)
        code = ("from pathlib import Path; p=Path('src/example/sample.py'); "
                "assert p.read_text()=='original'; p.write_text('worker change'); "
                "Path('src/example/new.py').write_text('new worker file')")
        with tempfile.TemporaryFile(mode="w+b") as prompt:
            process = launch_worker(identity, [sys.executable, "-I", "-c", code], workspace,
                                    prompt, logs / "stdout", logs / "stderr")
            try:
                assert process.wait(30) == 0
            finally:
                process.close()
        assert module.read_text(encoding="utf-8") == "worker change"
        assert (nested / "new.py").read_text(encoding="utf-8") == "new worker file"
    finally:
        shutil.rmtree(workspace)
        shutil.rmtree(logs)


def test_actual_retained_job_handle_confirms_remote_tree_exit(actual_windows_supervisor):
    from concurrent.futures import ThreadPoolExecutor
    from cochem_supervisor.windows import _read_control, _duplicate_recorded_job, _wait_job_empty
    from cochem_pipeline import windows as native
    config = actual_windows_supervisor
    control = Path(config["private_root"]) / ("retained-job-check-" + uuid.uuid4().hex + ".json")
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(15)']); time.sleep(15)"
    job = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_system_child, [sys.executable, "-I", "-c", code],
                                     config["baseline_source"], control_file=control)
            deadline = time.monotonic() + 10
            while not control.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.05)
            record = _read_control(control)
            job = _duplicate_recorded_job(record)
            # Wait until a real process has entered the retained job before
            # exercising termination; the record is published before launch.
            import ctypes
            accounting = native._BASIC_ACCOUNTING()
            while time.monotonic() < deadline:
                native._check(native._api()["kernel32"].QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None), "Observe actual child assignment")
                if accounting.ActiveProcesses:
                    break
                time.sleep(0.05)
            assert accounting.ActiveProcesses > 0
            native._check(native._api()["kernel32"].TerminateJobObject(job, 1), "Terminate test process tree through retained handle")
            _wait_job_empty(job)
            assert future.result(timeout=10) == 1
            assert _read_control(control)["closed"] is True
    finally:
        native._close(job)
        control.unlink(missing_ok=True)
