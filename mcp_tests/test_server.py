"""Real MCP and HTTP protocol sessions; every submitted task reaches the board."""
import asyncio
import json
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from cochem_mcp.server import create_server
from mcp_tests.test_jobs import job_environment, endpoint


@pytest.mark.parametrize('provider',['codex','claude'])
def test_mcp_provider_name_does_not_pin_model_or_claim_completion(provider,job_environment):
    create,_=job_environment;manager=create(provider=provider)
    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            tools=await client.list_tools()
            assert {t.name for t in tools.tools}=={f'{provider}_{name}' for name in
                ('health','submit','submit_node','status','result','cancel')}
            accepted=await client.call_tool(f'{provider}_submit',{'prompt':'Correct the regression'})
            assert not accepted.isError
            assert accepted.structuredContent['status']=='queued'
            assert accepted.structuredContent['provider'] is None
            denied=await client.call_tool(f'{provider}_submit',{'prompt':'Task','model':'gpt-6-sol'})
            assert denied.isError
    asyncio.run(scenario())
    assert len(manager.requests)==1


@pytest.mark.parametrize('provider',['codex','claude'])
@pytest.mark.parametrize('kind',['MANIFEST_GENERATOR','CHAPTER_DRAFT'])
def test_structured_node_creates_routed_controller_dag(provider,kind,job_environment):
    create,_=job_environment;manager=create(provider=provider)
    payload={'objective':'Draft recovery Δ','requirements':['R1']}
    payload.update({'chapter_count':1} if kind=='MANIFEST_GENERATOR' else {'chapter_id':'chapter-1','title':'Recovery'})
    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            response=await client.call_tool(f'{provider}_submit_node',
                {'kind':kind,'payload':payload,'workflow_id':'new-workflow'})
            assert not response.isError
            record=response.structuredContent
            assert record['submission_scope']=='protected_job_board'
            assert record['job_id']=='plan:new-workflow'
            result=await client.call_tool(f'{provider}_result',{'job_id':record['job_id']})
            assert result.structuredContent['content'] is None
            duplicate=await client.call_tool(f'{provider}_submit_node',
                {'kind':kind,'payload':{**payload,'objective':'Replace existing'},'workflow_id':'new-workflow'})
            assert duplicate.isError
            accepted = manager.endpoint.controller.store.workflow('new-workflow')['root']['payload']['objective']
            if kind == 'CHAPTER_DRAFT':
                assert accepted.startswith('Draft recovery Δ\n\nRequested chapter scope: ')
                assert json.loads(accepted.split('Requested chapter scope: ', 1)[1]) == {
                    'chapter_id': 'chapter-1', 'title': 'Recovery'}
            else:
                assert accepted == 'Draft recovery Δ'
    asyncio.run(scenario())


@pytest.mark.parametrize('patch',[
    {'kind':'SYNTHESIS'},{'kind':'UNKNOWN'},{'workflow_id':'../escape'},
    {'payload':{'objective':'Draft','requirements':[],'chapter_count':1}},
    {'payload':{'objective':'Draft','requirements':['R1'],'chapter_count':True}},
    {'payload':{'objective':'Draft','requirements':['R1'],'chapter_count':1,'nested':[{'fencing_token':'forged'}]}},
    {'model':'sol'}])
def test_invalid_node_or_authority_rejected_before_submission(patch,job_environment):
    create,_=job_environment;manager=create()
    args={'kind':'MANIFEST_GENERATOR','workflow_id':'manual-workflow',
          'payload':{'objective':'Draft','requirements':['R1'],'chapter_count':1}}
    args.update(patch)
    async def scenario():
        async with create_connected_server_and_client_session(create_server(manager)) as client:
            response=await client.call_tool('codex_submit_node',args)
            assert response.isError
            assert not manager.endpoint.controller.store.active_jobs()
    asyncio.run(scenario())
