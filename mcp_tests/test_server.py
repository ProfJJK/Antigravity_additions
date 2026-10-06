"""Actual MCP protocol sessions with explicitly emulated provider processes."""
import asyncio
from pathlib import Path
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
                f'{provider}_{name}' for name in ('health', 'submit', 'submit_node', 'status', 'result', 'cancel')
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


NODE_EMULATOR = r'''
# Explicit native CLI protocol emulator, not a model or subscription service.
import json
import sys
raw = sys.stdin.read()
if '--print' in sys.argv:
    print(json.dumps({'type':'result', 'subtype':'success', 'is_error':False,
                      'session_id':'emulator-node-claude', 'result':raw}))
else:
    print(json.dumps({'type':'thread.started', 'thread_id':'emulator-node-codex'}))
    print(json.dumps({'type':'item.completed', 'item':{'type':'agent_message', 'text':raw}}))
    print(json.dumps({'type':'turn.completed', 'usage':{}}))
'''


@pytest.mark.parametrize('provider', ['codex', 'claude'])
@pytest.mark.parametrize('kind', ['MANIFEST_GENERATOR', 'CHAPTER_DRAFT'])
def test_structured_node_uses_actual_mcp_schema_and_process_without_mutating_dag(provider, kind, job_environment):
    from cochem_pipeline.store import JobStore
    from cochem_pipeline.worker import node_prompt

    create, workspace = job_environment
    manager = create(provider=provider)
    Path(manager.settings.executable).write_text(NODE_EMULATOR, encoding='utf-8')
    board = JobStore(workspace / 'existing-controller-contract.db')
    before = board.submit('Existing protected-controller contract data', ['REQ-1'], 1)
    workflow_id = before['workflow_id']
    payload = {'objective': 'Draft the Unicode recovery requirement: Δ', 'requirements': ['REQ-1']}
    payload.update({'chapter_count': 1} if kind == 'MANIFEST_GENERATOR' else
                   {'chapter_id': 'chapter-1', 'title': 'Recovery'})

    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            tools = await client.list_tools()
            tool = next(item for item in tools.tools if item.name == f'{provider}_submit_node')
            assert set(tool.inputSchema['required']) == {'kind', 'payload', 'workflow_id'}
            assert tool.inputSchema['properties']['payload']['type'] == 'object'
            assert tool.inputSchema['properties']['workspace']['default'] == ''
            assert tool.inputSchema['properties']['model']['default'] == ''
            assert 'fencing_token' not in tool.inputSchema['properties']
            accepted = await client.call_tool(tool.name, {
                'kind': kind, 'payload': payload, 'workflow_id': workflow_id,
                'workspace': str(workspace), 'model': 'test-model',
            })
            assert not accepted.isError
            receipt = accepted.structuredContent
            assert receipt['status'] == 'queued'
            assert receipt['submission_scope'] == 'standalone_node_draft'
            assert receipt['protected_dag_mutated'] is False
            assert receipt['workflow_id'] == workflow_id and receipt['node_kind'] == kind
            for _ in range(100):
                result = await client.call_tool(f'{provider}_result', {'job_id': receipt['job_id']})
                assert not result.isError
                if result.structuredContent['status'] in {'completed', 'failed'}:
                    break
                await asyncio.sleep(.02)
            record = result.structuredContent
            assert record['status'] == 'completed', record
            assert record['pid'] > 0 and record['exit_code'] == 0
            assert record['session_id'].startswith('emulator-node-')
            shared = node_prompt({'kind': kind, 'payload': payload, 'workflow_id': workflow_id})
            assert record['content'].endswith(shared)
            assert record['content'].startswith('Standalone development request only.')
            assert board.workflow(workflow_id) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('patch', [
    {'kind': 'SYNTHESIS'},
    {'kind': 'UNKNOWN'},
    {'workflow_id': '../another-workflow'},
    {'payload': {'objective': 'Draft', 'requirements': [], 'chapter_count': 1}},
    {'payload': {'objective': 'Draft', 'requirements': ['REQ-1'], 'chapter_count': True}},
    {'payload': {'objective': 'Draft', 'requirements': ['REQ-1'], 'chapter_count': 1,
                 'nested': [{'fencing_token': 'must-never-reach-provider'}]}},
])
def test_structured_node_rejects_invalid_or_control_plane_data_before_submission(patch, job_environment):
    create, _ = job_environment
    manager = create()
    arguments = {'kind': 'MANIFEST_GENERATOR', 'workflow_id': 'manual-workflow',
                 'payload': {'objective': 'Draft', 'requirements': ['REQ-1'], 'chapter_count': 1}}
    arguments.update(patch)

    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            result = await client.call_tool('codex_submit_node', arguments)
            assert result.isError
            assert list(manager.root.glob('*/receipt.json')) == []
    asyncio.run(scenario())
