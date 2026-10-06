"""Legacy DSP requests must never become successful just because they are polled."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest
from fastmcp import Client


@pytest.fixture
def legacy_server():
    source = (
        Path(__file__).resolve().parents[1]
        / "v4.1.2/src/cochem/dsp/toolkit/mcp_server.py"
    )
    spec = importlib.util.spec_from_file_location("legacy_dsp_mcp_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_submit_and_repeated_mcp_polling_never_claim_execution(legacy_server):
    async def scenario():
        async with Client(legacy_server.mcp) as client:
            submission = await client.call_tool(
                "trigger_dsp_pipeline",
                {"domain": "code_forge", "payload": {"prompt": "run a real job"}},
            )
            job = submission.data
            assert job["status"] == "UNAVAILABLE"
            assert job["execution_started"] is False
            assert "No task was queued or dispatched" in job["error"]
            before = copy.deepcopy(legacy_server.DSP_JOB_REGISTRY)
            for _ in range(4):
                status = await client.call_tool("get_dsp_status", {"job_id": job["job_id"]})
                assert status.data["status"] == "UNAVAILABLE"
                assert status.data["execution_started"] is False
                assert status.data["completed_at"] is None
            assert legacy_server.DSP_JOB_REGISTRY == before

    asyncio.run(scenario())


def test_legacy_unknown_job_does_not_create_or_execute_work(legacy_server):
    async def scenario():
        async with Client(legacy_server.mcp) as client:
            status = await client.call_tool("get_dsp_status", {"job_id": "missing"})
            assert status.data["status"] == "NOT_FOUND"
            assert legacy_server.DSP_JOB_REGISTRY == {}

    asyncio.run(scenario())


def test_legacy_preexisting_queued_job_is_not_advanced_by_polling(legacy_server):
    legacy_server.DSP_JOB_REGISTRY["old-job"] = {
        "job_id": "old-job",
        "domain": "code_forge",
        "status": "QUEUED",
        "created_at": 1,
        "completed_at": None,
    }
    before = copy.deepcopy(legacy_server.DSP_JOB_REGISTRY)

    async def scenario():
        async with Client(legacy_server.mcp) as client:
            for _ in range(3):
                status = await client.call_tool("get_dsp_status", {"job_id": "old-job"})
                assert status.data["status"] == "QUEUED"
                assert status.data["execution_started"] is False
                assert status.data["completed_at"] is None
        assert legacy_server.DSP_JOB_REGISTRY == before

    asyncio.run(scenario())
