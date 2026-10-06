"""Portable durable-intent policy tests; native scheduling needs real Windows.

No simulated Win32 calls establish process-tree or ACL guarantees here. The
opt-in native test creates a disposable task and never touches the real Warden.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from cochem_pipeline import windows as native
from cochem_supervisor.io import write_json
from cochem_supervisor.windows import (
    _can_finish_without_retained_job, _read_control, _read_task_marker,
    _start_intent, _stop_intent, _task_active,
    _validate_task_marker, _validate_task_snapshot, _validate_containment_id,
    _warden_environment, _matching_fresh_upgrade_receipt, start_task, stop_task,
)


NAME = "CoChem-Policy-Test"


def snapshot(*, enabled=False, state="Disabled", instances=0):
    return {"enabled": enabled, "state": state, "instances": instances}


def marker(*, original_enabled=True, phase="STOPPED"):
    return {"schema": 1, "task_name": NAME, "original_enabled": original_enabled, "phase": phase}


@pytest.mark.parametrize("record", [None, {"boot_id": 11, "closed": True}, {"boot_id": 10, "closed": False}])
def test_ready_enabled_task_with_old_closed_record_is_never_stop_proof(record):
    assert not _can_finish_without_retained_job(snapshot(enabled=True, state="Ready"), record, 11)


@pytest.mark.parametrize("state,instances", [("Disabled", 1), ("Disabled", 3), ("Running", 0), ("Queued", 0)])
def test_disabled_registration_with_active_or_queued_instance_is_not_empty(state, instances):
    observed = snapshot(state=state, instances=instances)
    assert _task_active(observed)
    assert not _can_finish_without_retained_job(observed, {"boot_id": 11, "closed": True}, 11)


@pytest.mark.parametrize("record,expected", [
    (None, True), ({"boot_id": 11, "closed": True}, True),
    ({"boot_id": 10, "closed": False}, True),
    ({"boot_id": 11, "closed": False, "contained": False}, False),
    ({"boot_id": 11, "closed": False, "contained": True}, False),
])
def test_disabled_inactive_task_still_requires_same_boot_tree_exit(record, expected):
    assert _can_finish_without_retained_job(snapshot(), record, 11) is expected


@pytest.mark.parametrize("phase", ["DISABLING", "DISABLED", "STOPPED", "STARTING"])
@pytest.mark.parametrize("observed_enabled", [False, True])
def test_retry_preserves_administrator_disabled_intent(phase, observed_enabled):
    previous = marker(original_enabled=False, phase=phase)
    retried = _stop_intent(NAME, snapshot(enabled=observed_enabled), previous)
    assert retried == marker(original_enabled=False, phase="DISABLING")
    with pytest.raises(native.WindowsIsolationError, match="administrator-disabled"):
        _start_intent(NAME, snapshot(), {**retried, "phase": "STOPPED"})


@pytest.mark.parametrize("phase", ["DISABLING", "DISABLED", "STOPPED", "STARTING"])
def test_owned_stop_survives_restart_without_forgetting_original_enabled_state(tmp_path, phase):
    filename = tmp_path / "control.json"
    first = _stop_intent(NAME, snapshot(enabled=True, state="Ready"))
    write_json(filename, {**first, "phase": phase})
    # A real durable JSON roundtrip exercises restart policy, not native ACLs.
    restored = _validate_task_marker(json.loads(filename.read_text()), NAME)
    retried = _stop_intent(NAME, snapshot(), restored)
    assert retried["original_enabled"] is True
    with pytest.raises(native.WindowsCleanupError, match="interrupted"):
        _start_intent(NAME, snapshot(), retried)
    assert _start_intent(NAME, snapshot(), {**retried, "phase": "STOPPED"}) is True


@pytest.mark.parametrize("phase", ["DISABLING", "DISABLED"])
def test_partial_stop_never_authorizes_enable(phase):
    with pytest.raises(native.WindowsCleanupError, match="interrupted"):
        _start_intent(NAME, snapshot(), marker(phase=phase))


@pytest.mark.parametrize("observed", [snapshot(enabled=True, state="Ready"), snapshot(instances=1), snapshot(state="Queued")])
def test_interrupted_start_requires_fresh_disabled_stop_proof(observed):
    with pytest.raises(native.WindowsCleanupError, match="repeat"):
        _start_intent(NAME, observed, marker(phase="STARTING"))


def test_unmarked_disabled_task_has_no_enable_authority():
    with pytest.raises(native.WindowsIsolationError, match="administrator-disabled"):
        _start_intent(NAME, snapshot(), None)
    assert _start_intent(NAME, snapshot(enabled=True, state="Ready"), None) is False


@pytest.mark.parametrize("invalid", [None, {}, {"enabled": 1, "state": "Ready", "instances": 0},
    snapshot(instances=True), snapshot(instances=-1), snapshot(state="Unknown")])
def test_unreliable_scheduler_snapshots_fail_closed(invalid):
    with pytest.raises(native.WindowsIsolationError):
        _validate_task_snapshot(invalid)


@pytest.mark.parametrize("invalid", [None, {}, {**marker(), "schema": True},
    {**marker(), "schema": 2}, {**marker(), "task_name": "Another-Task"},
    {**marker(), "original_enabled": 1}, {**marker(), "phase": "FINISHED"},
    {**marker(), "extra_authority": True}])
def test_corrupt_or_wrong_task_marker_never_grants_enable(invalid):
    with pytest.raises(native.WindowsIsolationError):
        _validate_task_marker(invalid, NAME)


@pytest.mark.parametrize("invalid", ["", "x" * 32, "A" * 32, "a" * 31, "a" * 33, 1, True, {}])
def test_containment_scope_rejects_malformed_or_noncanonical_identity(invalid):
    with pytest.raises(native.WindowsIsolationError):
        _validate_containment_id(invalid)


def test_launcher_never_reuses_an_inherited_containment_scope():
    previous, fresh = "a" * 32, "b" * 32
    inherited = {"PATH": "protected-machine-path", "CoChem_Warden_Containment_Id": previous}
    assert _validate_containment_id(None) is None
    environment = _warden_environment(fresh, inherited)
    assert environment.endswith("\x00\x00")
    entries = environment.rstrip("\x00").split("\x00")
    assert entries == [f"COCHEM_WARDEN_CONTAINMENT_ID={fresh}", "PATH=protected-machine-path"]
    assert _warden_environment(None, inherited) == "PATH=protected-machine-path\x00\x00"
    assert inherited["CoChem_Warden_Containment_Id"] == previous


def test_fresh_upgrade_retry_requires_matching_durable_receipt(tmp_path):
    source, target = tmp_path / "absent423", tmp_path / "fresh424"
    target.mkdir()
    receipt = {"source_private": str(source.absolute()), "target_private": str(target.absolute()),
               "status": "NO_PRIOR_LEDGER", "quarantine_preserved": False}
    filename = target / "budget-upgrade.json"
    write_json(filename, receipt)
    actual = json.loads(filename.read_text())
    assert _matching_fresh_upgrade_receipt(actual, source, target)
    assert not _matching_fresh_upgrade_receipt(actual, tmp_path / "typo", target)
    assert not _matching_fresh_upgrade_receipt(actual, source, tmp_path / "wrong-target")
    for change in ({"status": "LEDGER_COPIED"}, {"status": "IDENTICAL_LEDGER_PRESERVED"},
                   {"quarantine_preserved": 0}, {"quarantine_preserved": True}, {"extra_authority": True}):
        assert not _matching_fresh_upgrade_receipt({**actual, **change}, source, target)
    assert not _matching_fresh_upgrade_receipt(None, source, target)


def test_fresh_upgrade_receipt_does_not_authorize_another_existing_empty_source(tmp_path):
    source, target, wrong_source = (tmp_path / name for name in ("fresh423", "fresh424", "empty-typo"))
    for directory in (source, target, wrong_source):
        directory.mkdir()
    receipt = {"source_private": str(source.absolute()), "target_private": str(target.absolute()),
               "status": "NO_PRIOR_LEDGER", "quarantine_preserved": False}
    write_json(target / "budget-upgrade.json", receipt)
    actual = json.loads((target / "budget-upgrade.json").read_text())
    assert not (source / "supervisor.db").exists()
    assert not (wrong_source / "supervisor.db").exists()
    assert _matching_fresh_upgrade_receipt(actual, source, target)
    assert not _matching_fresh_upgrade_receipt(actual, wrong_source, target)


def test_native_disposable_task_stop_and_controlled_restart():
    if os.name != "nt" or os.environ.get("COCHEM_SUPERVISOR_WINDOWS_TASK_CONTROL") != "1":
        pytest.skip("Requires real Windows SYSTEM and explicit disposable-task test opt-in")
    filename = os.environ.get("COCHEM_SUPERVISOR_WINDOWS_CONFIG")
    if not filename:
        pytest.skip("Requires installed supervisor configuration")
    from cochem_supervisor.config import load_config
    from cochem_supervisor.windows import require_supervisor, migrate_ledger
    config = load_config(filename)
    require_supervisor(config)
    directory = Path(config["private_root"]) / ("task-control-check-" + uuid.uuid4().hex)
    directory.mkdir()
    native.validate_private_directory(directory)
    pointer = directory / "test-pointer.json"
    pointer.write_text("{}", encoding="utf-8")
    native.validate_private_path(pointer)
    control = directory / "warden-process.json"
    scope_file = directory / "observed-child-scope.txt"
    task_name = "CoChem-Control-Check-" + uuid.uuid4().hex
    child_code = (f"import os,pathlib,time; pathlib.Path({str(scope_file)!r}).write_text("
                  "os.environ['COCHEM_WARDEN_CONTAINMENT_ID']); time.sleep(120)")
    code = (
        "import sys; from cochem_supervisor.windows import run_system_child; "
        f"raise SystemExit(run_system_child([sys.executable,'-I','-c',{child_code!r}], "
        f"{config['baseline_source']!r}, control_file={str(control)!r}))"
    )
    arguments = subprocess.list2cmdline(["-I", "-c", code])
    registered = False
    empty_source = directory.parent / ("empty-previous-" + uuid.uuid4().hex)
    try:
        native._powershell(
            "$action=New-ScheduledTaskAction -Execute $data.python -Argument $data.arguments; "
            "$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
            "$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; "
            "Register-ScheduledTask -TaskName $data.name -TaskPath '\\' -Action $action -Principal $principal -Settings $settings | Out-Null",
            {"name": task_name, "python": sys.executable, "arguments": arguments})
        registered = True
        with pytest.raises(native.WindowsIsolationError, match="actual previous supervisor.db budget ledger"):
            migrate_ledger(directory.parent / ("missing-" + uuid.uuid4().hex), directory, task_name)
        empty_source.mkdir()
        native.validate_private_directory(empty_source)
        with pytest.raises(native.WindowsIsolationError, match="refusing to assume zero historical spend"):
            migrate_ledger(empty_source, directory, task_name)
        fresh_source = directory.parent / ("missing-" + uuid.uuid4().hex)
        fresh = migrate_ledger(fresh_source, directory, task_name + "-absent")
        assert fresh["status"] == "NO_PRIOR_LEDGER"
        for repetition in range(2):
            started = start_task(task_name, pointer_file=pointer)
            assert started["scheduling_restored"] is bool(repetition)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if control.exists():
                    record = _read_control(control)
                    if record["contained"] and not record["closed"] and scope_file.exists():
                        break
                time.sleep(.05)
            else:
                pytest.fail("Native disposable launcher did not publish real containment")
            stopped = stop_task(task_name, pointer_file=pointer)
            assert stopped["tree_exit_verified"] and stopped["scheduling_disabled"]
            assert stopped["enabled"] is False and stopped["instances"] == 0
            assert stopped["stopped_containment_id"] == record["containment_id"] == scope_file.read_text()
            assert stopped["stopped_boot_id"] == record["boot_id"]
            assert _read_control(control)["closed"] is True
            saved = _read_task_marker(directory / "warden-task-control.json", task_name)
            assert saved["phase"] == "STOPPED" and saved["original_enabled"] is True
            scope_file.unlink()
        assert migrate_ledger(fresh_source, directory, task_name)["status"] == "NO_PRIOR_LEDGER"
    finally:
        if registered:
            stop_task(task_name, pointer_file=pointer)
            native._powershell("Unregister-ScheduledTask -TaskName $data.name -TaskPath '\\' -Confirm:$false", {"name": task_name})
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        if empty_source.exists():
            empty_source.rmdir()
