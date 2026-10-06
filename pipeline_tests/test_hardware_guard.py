"""Functional hardware admission checks against the actual local OS."""

from __future__ import annotations

import json
import math
from pathlib import Path

import psutil
import pytest

from cochem_pipeline.hardware_guard import HardwareGuard


def test_snapshot_reports_real_host_and_workspace_resources(tmp_path: Path) -> None:
    result = HardwareGuard(workspace=tmp_path, max_cpu_percent=100).snapshot()
    assert result["workspace"] == str(tmp_path.resolve())
    assert result["cpu_count"] > 0
    assert result["cpu_count"] <= psutil.cpu_count(logical=True)
    assert 0 <= result["cpu_percent"] <= 100
    assert result["memory_total_mb"] == psutil.virtual_memory().total / (1024 * 1024)
    assert 0 <= result["memory_available_mb"] <= result["memory_total_mb"]
    assert 0 <= result["disk_free_mb"] <= result["disk_total_mb"]
    assert result["disk_total_mb"] > 0
    assert 0 <= result["capacity"] <= 4
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("max_agents", [0, 1, 2, 4, 8, 100])
def test_actual_capacity_obeys_configured_and_hard_limits(tmp_path: Path, max_agents: int) -> None:
    guard = HardwareGuard(
        max_agents=max_agents,
        workspace=tmp_path,
        min_free_memory_mb=0,
        min_free_disk_mb=0,
        per_agent_memory_mb=0.001,
        max_cpu_percent=100,
    )
    snapshot = guard.snapshot()
    assert snapshot["capacity"] == min(max_agents, 4, snapshot["cpu_count"])
    assert guard.capacity() <= min(max_agents, 4)


def test_real_memory_below_explicit_reserve_blocks_admission(tmp_path: Path) -> None:
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    guard = HardwareGuard(workspace=tmp_path, min_free_memory_mb=total_mb + 1024)
    result = guard.snapshot()
    assert result["capacity"] == 0
    assert any("memory" in reason for reason in result["reasons"])


def test_per_worker_memory_budget_blocks_admission(tmp_path: Path) -> None:
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    result = HardwareGuard(workspace=tmp_path, per_agent_memory_mb=total_mb + 1024).snapshot()
    assert result["capacity"] == 0
    assert any("memory" in reason for reason in result["reasons"])


def test_real_disk_below_explicit_reserve_blocks_admission(tmp_path: Path) -> None:
    result = HardwareGuard(workspace=tmp_path, min_free_disk_mb=1e30).snapshot()
    assert result["capacity"] == 0
    assert any("disk" in reason for reason in result["reasons"])


def test_missing_workspace_fails_closed_without_creating_it(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    result = HardwareGuard(workspace=workspace).snapshot()
    assert result["capacity"] == 0
    assert result["disk_free_mb"] is None
    assert any("Disk measurement failed" in reason for reason in result["reasons"])
    assert not workspace.exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"max_agents": -1},
        {"max_agents": 2.5},
        {"max_agents": True},
        {"min_free_memory_mb": -1},
        {"min_free_memory_mb": math.nan},
        {"min_free_disk_mb": math.inf},
        {"min_free_disk_mb": "100"},
        {"per_agent_memory_mb": 0},
        {"max_cpu_percent": 101},
        {"max_cpu_percent": -1},
    ],
)
def test_invalid_resource_bounds_are_rejected(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HardwareGuard(**settings)
