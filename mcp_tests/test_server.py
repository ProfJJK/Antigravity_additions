"""Actual MCP protocol sessions with explicitly emulated provider processes."""
import asyncio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from cochem_mcp.server import create_server
from mcp_tests.test_jobs import job_environment, request


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_mcp_submission_is_distinct_from_result(provider, job_environment):
    create, workspace = job_environment
    manager = create(provider=provider)
    prompt, marker = request(workspace, delay=0.1)

    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {
                f'{provider}_{name}' for name in ('health', 'submit', 'status', 'result', 'cancel')
            }
            accepted = await client.call_tool(f'{provider}_submit', {'prompt': prompt})
            assert not accepted.isError
            job = accepted.structuredContent
            assert job['status'] == 'queued'
            for _ in range(100):
                status = await client.call_tool(f'{provider}_status', {'job_id': job['job_id']})
                assert not status.isError
                if status.structuredContent['status'] == 'completed':
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail(str(status.structuredContent))
            result = await client.call_tool(f'{provider}_result', {'job_id': job['job_id']})
            record = result.structuredContent
            assert record['status'] == 'completed'
            assert record['pid'] > 0 and record['exit_code'] == 0
            assert record['session_id'].startswith('emulator')
            assert record['content'].startswith('EMULATED')
            assert record['finished_at']
            assert marker.is_file()
            failed = await client.call_tool(f'{provider}_status', {'job_id': 'nonexistent'})
            assert failed.isError
    asyncio.run(scenario())
