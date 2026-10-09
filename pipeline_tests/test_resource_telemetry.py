"""Native resource probes and bounded real processes; no substituted telemetry."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from cochem_pipeline.resource_telemetry import _bounded_command, collect_resources


def test_actual_host_snapshot_reports_cpu_memory_volumes_and_counter_availability(tmp_path: Path) -> None:
    other = tmp_path / "second-workspace"
    other.mkdir()
    result = collect_resources([tmp_path, other], sample_seconds=0.02)
    cpu = result["cpu"]
    assert cpu["available"] is True
    count = psutil.cpu_count(logical=True)
    if hasattr(psutil.Process(), "cpu_affinity"):
        count = min(count, len(psutil.Process().cpu_affinity()))
    assert cpu["count"] == count
    assert 0 <= cpu["percent"] <= 100
    memory = result["memory"]
    assert memory["available"] is True
    assert memory["total_mb"] == psutil.virtual_memory().total / 1024**2
    assert 0 <= memory["available_mb"] <= memory["total_mb"]
    disks = result["disks"]
    assert disks["available"] is True
    assert [entry["path"] for entry in disks["volumes"]] == [str(tmp_path.resolve()), str(other.resolve())]
    for volume in disks["volumes"]:
        assert volume["total_mb"] == shutil.disk_usage(volume["path"]).total / 1024**2
        assert 0 <= volume["free_mb"] <= volume["total_mb"]
        assert volume["io_mapping_available"] == bool(volume["io_devices"])
    assert disks["io"]["sample_seconds"] >= 0.02
    assert disks["io"]["scope"] == "host"
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_missing_workspace_denies_complete_disk_snapshot_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    result = collect_resources([tmp_path, missing], sample_seconds=0.01)
    assert result["disks"]["available"] is False
    assert len(result["disks"]["volumes"]) == 1
    assert any("absent" in error for error in result["disks"]["errors"])
    assert not missing.exists()


def test_missing_nvidia_executable_does_not_report_zero_vram(tmp_path: Path) -> None:
    result = collect_resources([tmp_path], sample_seconds=0.01, nvidia_smi=str(tmp_path / "absent-nvidia-smi"))
    assert result["gpus"]["available"] is False
    assert result["gpus"]["devices"] == []
    assert result["gpus"]["error"]


def test_real_non_nvidia_executable_failure_is_unavailable(tmp_path: Path) -> None:
    result = collect_resources([tmp_path], sample_seconds=0.01, nvidia_smi=sys.executable)
    assert result["gpus"]["available"] is False
    assert result["gpus"]["devices"] == []
    assert result["gpus"]["error"]
    if os.name != "nt":
        assert "exited with code" in result["gpus"]["error"]


def test_native_gpu_probe_reports_real_devices_or_explicit_failure(tmp_path: Path) -> None:
    gpu = collect_resources([tmp_path], sample_seconds=0.01)["gpus"]
    if gpu["available"]:
        assert gpu["error"] is None
        assert gpu["devices"]
        assert len({device["id"] for device in gpu["devices"]}) == len(gpu["devices"])
        for device in gpu["devices"]:
            assert device["id"].startswith("GPU-")
            assert device["total_mb"] > 0
            assert 0 <= device["used_mb"] <= device["total_mb"]
            assert 0 <= device["utilization_percent"] <= 100
            assert math.isfinite(device["temperature_celsius"])
    else:
        assert gpu["devices"] == []
        assert gpu["error"]


def test_native_cpu_thermal_probe_preserves_unavailable_state(tmp_path: Path) -> None:
    cpu = collect_resources([tmp_path], sample_seconds=0.01)["cpu"]
    if cpu["temperature_available"]:
        assert math.isfinite(cpu["temperature_celsius"])
        assert cpu["temperature_source"]
        assert cpu["temperature_error"] is None
    else:
        assert cpu["temperature_celsius"] is None
        assert cpu["temperature_error"]
        if os.name == "nt":
            assert "LibreHardwareMonitor" in cpu["temperature_error"]


def test_commit_and_process_pressure_are_windows_measurements_only(tmp_path: Path) -> None:
    commit = collect_resources([tmp_path], sample_seconds=0.01)["commit"]
    assert commit["source"] == "Windows GetPerformanceInfo"
    if os.name == "nt":
        assert commit["available"] is True, commit["error"]
        assert 0 <= commit["total_mb"] <= commit["limit_mb"]
        assert commit["limit_mb"] > 0
        assert commit["free_mb"] == pytest.approx(commit["limit_mb"] - commit["total_mb"])
        assert commit["process_memory_available"] is True, commit["process_memory_error"]
        assert commit["process_private_mb"] >= 0
        assert commit["process_working_set_mb"] > 0
    else:
        assert commit["available"] is False
        assert commit["total_mb"] is commit["limit_mb"] is commit["free_mb"] is None
        assert commit["process_memory_available"] is False
        assert commit["process_private_mb"] is commit["process_working_set_mb"] is None
        assert "unavailable on this OS" in commit["error"]


def test_real_fsynced_disk_activity_is_sampled_with_per_device_counters(tmp_path: Path) -> None:
    path = tmp_path / "physical-io-sample.bin"
    stop = threading.Event()
    ready = threading.Event()
    errors = []
    writes = []

    def write_real_file() -> None:
        try:
            payload = os.urandom(1024 * 1024)
            with path.open("wb", buffering=0) as stream:
                while not stop.is_set():
                    stream.seek(0)
                    stream.write(payload)
                    os.fsync(stream.fileno())
                    writes.append(len(payload))
                    ready.set()
        except OSError as exc:
            errors.append(exc)
            ready.set()

    writer = threading.Thread(target=write_real_file, daemon=True)
    writer.start()
    try:
        assert ready.wait(5)
        assert not errors
        io_result = collect_resources([tmp_path], sample_seconds=0.15)["disks"]["io"]
    finally:
        stop.set()
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert not errors
    assert sum(writes) >= 1024 * 1024
    assert path.stat().st_size == 1024 * 1024
    assert io_result["sample_seconds"] >= 0.15
    if io_result["available"]:
        assert io_result["devices"]
        for observed in [io_result, *io_result["devices"]]:
            assert observed["read_mb_s"] >= 0
            assert observed["write_mb_s"] >= 0
            assert observed["total_mb_s"] == pytest.approx(observed["read_mb_s"] + observed["write_mb_s"])
            assert observed["total_iops"] >= 0
            if "busy_percent" in observed:
                assert 0 <= observed["busy_percent"] <= 100
        # A container's overlay or network mount may not expose its physical
        # device in this namespace. Do not invent an attribution or minimum I/O.
    else:
        assert io_result["errors"]


def test_real_command_stdout_is_drained() -> None:
    output = _bounded_command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'probe-output')"])
    assert output == b"probe-output"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_command_output_limit_terminates_producer(stream: str) -> None:
    program = f"import sys; sys.{stream}.buffer.write(b'x' * 1048576); sys.{stream}.flush()"
    with pytest.raises(OSError, match="output limit"):
        _bounded_command([sys.executable, "-c", program], max_output_bytes=32768)


def test_real_command_timeout_terminates_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "probe.pid"
    program = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    handles: list[int] = []
    observer_errors: list[Exception] = []
    stop = threading.Event()

    def observe_windows_interpreter() -> None:
        import _winapi

        # The Windows venv launcher is a different process from the interpreter.
        # Hold the interpreter's native handle while it is alive: a PID can be
        # reused, and psutil's PID list can retain a process that already exited.
        while not stop.wait(0.001):
            try:
                pid = int(pid_file.read_text())
            except (OSError, ValueError):
                continue
            try:
                handle = _winapi.OpenProcess(
                    _winapi.SYNCHRONIZE | 0x1000 | 0x0001, False, pid,
                )  # QUERY_LIMITED_INFORMATION and TERMINATE (failure cleanup).
                handles.append(handle)
                assert _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT
            except Exception as exc:
                observer_errors.append(exc)
            return

    observer = threading.Thread(target=observe_windows_interpreter, daemon=True) if os.name == "nt" else None
    if observer is not None:
        import _winapi

        observer.start()
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match="timeout"):
            _bounded_command([sys.executable, "-c", program, str(pid_file)], timeout=0.5)
        assert pid_file.exists()
        if observer is not None:
            observer.join(timeout=0.5)
            assert not observer.is_alive()
            assert not observer_errors
            assert len(handles) == 1, "Interpreter handle was not captured while running"
            # Closing the launcher's kill-on-close job terminates its interpreter
            # asynchronously; prove that exact interpreter exits within the bound.
            assert _winapi.WaitForSingleObject(handles[0], 1000) == _winapi.WAIT_OBJECT_0
            assert _winapi.GetExitCodeProcess(handles[0]) != _winapi.STILL_ACTIVE
        else:
            assert not psutil.pid_exists(int(pid_file.read_text()))
        assert time.monotonic() - started < 3
    finally:
        stop.set()
        if observer is not None:
            observer.join(timeout=0.5)
            for handle in handles:
                try:
                    if _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT:
                        # This exact handle belongs only to this test's interpreter.
                        _winapi.TerminateProcess(handle, 1)
                        assert _winapi.WaitForSingleObject(handle, 1000) == _winapi.WAIT_OBJECT_0
                finally:
                    _winapi.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows Popen process handle lifecycle")
@pytest.mark.parametrize("outcome", ["success", "exit_failure", "output_limit", "timeout"])
def test_windows_command_releases_handle_with_process_and_traceback_retained(monkeypatch, outcome: str) -> None:
    processes: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def record_real_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    # Observe the actual process without replacing any process behavior/results.
    monkeypatch.setattr(subprocess, "Popen", record_real_process)
    programs = {
        "success": "print('done')",
        "exit_failure": "import sys; sys.exit(7)",
        "output_limit": "import sys; sys.stdout.buffer.write(b'x'*1048576); sys.stdout.flush()",
        "timeout": "import time; time.sleep(30)",
    }
    retained_error = None
    try:
        result = _bounded_command([sys.executable, "-c", programs[outcome]], timeout=0.5)
    except OSError as exc:
        retained_error = exc
    assert len(processes) == 1
    process = processes[0]
    assert process.returncode is not None
    assert process._handle.closed, "Cleanup must not depend on object/traceback garbage collection"
    assert process.stdout.closed and process.stderr.closed
    if outcome == "success":
        assert retained_error is None
        assert result.strip() == b"done"
    else:
        assert retained_error is not None and retained_error.__traceback__ is not None
        expected = {"exit_failure": "code 7", "output_limit": "output limit", "timeout": "timeout"}
        assert expected[outcome] in str(retained_error)


@pytest.mark.parametrize("sample", [0, -1, 0.001, 6, True, math.nan, math.inf, "0.05"])
def test_unbounded_or_invalid_sampling_intervals_are_rejected(tmp_path: Path, sample: object) -> None:
    with pytest.raises(ValueError, match="sample_seconds"):
        collect_resources([tmp_path], sample_seconds=sample)


@pytest.mark.parametrize("workspaces", [[], "workspace", Path("workspace")])
def test_workspaces_must_be_a_nonempty_sequence(workspaces: object) -> None:
    with pytest.raises(ValueError, match="workspaces"):
        collect_resources(workspaces)


@pytest.mark.parametrize("executable", ["", " ", "invalid\x00path", None])
def test_nvidia_executable_is_one_nonempty_argument(tmp_path: Path, executable: object) -> None:
    with pytest.raises(ValueError, match="nvidia_smi"):
        collect_resources([tmp_path], nvidia_smi=executable)


def test_cpu_physical_count_is_actual_and_distinct_from_affinity_logical_count(tmp_path):
    cpu=collect_resources([tmp_path],sample_seconds=.01)['cpu']
    actual=psutil.cpu_count(logical=False)
    assert cpu['physical_count_available'] is (type(actual) is int and actual>0)
    assert cpu['physical_count']==actual
    assert cpu['physical_count_source']=='psutil.cpu_count(logical=False)'
