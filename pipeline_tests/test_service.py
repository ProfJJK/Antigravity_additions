"""Real loopback HTTP and SQLite checks; no Windows or model execution claims.

The test controller below is an explicitly limited database controller. It does
not stand in for native process execution and cannot complete a model job.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
import http.client
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline.service import ControlClient, ControlServer, public_workflow
from cochem_pipeline.store import JobStore


def test_public_redaction_preserves_hashable_user_artifacts():
    output = {'artifact_text':'A real document field named owner is user data',
              'owner':'chapter author','attempt_id':'literal document data'}
    snapshot = {'jobs':[{'attempt_id':'private-attempt','lease_owner':'private-controller',
                         'output':output,'receipt':{'attempt_id':'private-attempt'}}],
                'events':[{'details':{'owner':'private-controller','fencing_token':5}}]}
    public = public_workflow(snapshot)
    assert public['jobs'][0]['output']==output
    assert public['jobs'][0]['receipt']=={}
    assert public['events'][0]['details']=={}
    assert snapshot['jobs'][0]['attempt_id']=='private-attempt'


def test_actual_pipeline_mcp_session_creates_and_cancels_real_dag(endpoint):
    from mcp.shared.memory import create_connected_server_and_client_session
    from cochem_pipeline.server import create_server

    async def scenario():
        async with create_connected_server_and_client_session(create_server(endpoint.client)) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                'pipeline_submit','pipeline_status','pipeline_health','pipeline_cancel','pipeline_resume_routing'}
            response = await client.call_tool('pipeline_submit',{
                'objective':'Create a six-chapter SRS/WBS', 'requirements':['REQ-1'], 'chapter_count':6})
            assert not response.isError
            workflow = response.structuredContent
            assert workflow['status']=='IN_PROGRESS'
            assert {job['kind'] for job in workflow['jobs']} == {
                'MACRO_PLANNING_REQUEST','MANIFEST_GENERATOR','SYNTHESIS'}
            workflow_id=workflow['workflow_id']
            assert endpoint.controller.store.workflow(workflow_id)['root']['payload']['chapter_count']==6
            status = await client.call_tool('pipeline_status',{'workflow_id':workflow_id})
            assert not status.isError and status.structuredContent['status']=='IN_PROGRESS'
            cancelled = await client.call_tool('pipeline_cancel',{'workflow_id':workflow_id})
            assert not cancelled.isError and cancelled.structuredContent['status']=='FAILED'
    asyncio.run(scenario())


class DatabaseController:
    """Minimal controller for exercising the real HTTP-to-database boundary."""

    def __init__(self, directory: Path):
        self.config = SimpleNamespace(port=0, workers={f"slot-{number}": {} for number in range(6)})
        self.store = JobStore(directory / "http-test.db")

    def status(self):
        return {"test_kind": "http-and-sqlite", "active_jobs": len(self.store.active_jobs()),
                "routing": self.store.routing_status()}

    def cancel(self, workflow_id):
        return self.store.cancel_workflow(workflow_id)


@pytest.fixture
def endpoint(tmp_path):
    controller = DatabaseController(tmp_path)
    token = secrets.token_urlsafe(48)
    server = ControlServer(controller, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token_file = tmp_path / "client-token"
    token_file.write_text(token, encoding="utf-8")
    try:
        yield SimpleNamespace(
            controller=controller,
            port=server.server_address[1],
            token=token,
            client=ControlClient(server.server_address[1], token_file),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(endpoint, method, path, data=None, authorization=None, raw=None, content_length=None):
    body = raw if raw is not None else json.dumps(data) if data is not None else None
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    connection = http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=3)
    try:
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def submitted(endpoint, workflow_id="service-workflow"):
    return endpoint.client.call("/submit", {
        "objective": "Document thermal transport — Chapter β",
        "requirements": ["REQ-1", "REQ-2"],
        "chapter_count": 6,
        "workflow_id": workflow_id,
    })


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic wrong", ""])
def test_unauthorized_request_cannot_read_health_or_create_jobs(endpoint, authorization):
    assert request(endpoint, "GET", "/health", authorization=authorization)[0] == 401
    assert request(endpoint, "POST", "/submit", {"objective": "Unauthorized"}, authorization=authorization)[0] == 401
    assert endpoint.controller.store.list_workflows() == []


def test_real_client_submits_unicode_and_reads_durable_workflow(endpoint):
    workflow = submitted(endpoint)
    assert workflow["workflow_id"] == "service-workflow"
    assert workflow["root"]["payload"]["objective"].endswith("Chapter β")
    assert {job["kind"] for job in workflow["jobs"]} == {
        "MACRO_PLANNING_REQUEST", "MANIFEST_GENERATOR", "SYNTHESIS"
    }
    assert workflow == endpoint.client.call("/workflow/service-workflow")
    assert endpoint.controller.store.workflow("service-workflow")["root"]["payload"]["chapter_count"] == 6
    assert endpoint.client.call("/health")["test_kind"] == "http-and-sqlite"


def test_concurrent_duplicate_submission_creates_one_dag(endpoint):
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: submitted(endpoint), range(6)))
    assert all(result["workflow_id"] == "service-workflow" for result in results)
    assert len(endpoint.controller.store.list_workflows()) == 1
    assert len(endpoint.controller.store.workflow("service-workflow")["jobs"]) == 3


def test_public_api_hides_real_claim_authority(endpoint):
    submitted(endpoint)
    claimed = endpoint.controller.store.claim("private-test-controller")
    assert claimed["attempt_id"]
    returned = endpoint.client.call("/workflow/service-workflow")
    serialized = json.dumps(returned)
    assert claimed["attempt_id"] not in serialized
    assert "private-test-controller" not in serialized
    for item in [returned["root"], *returned["jobs"]]:
        assert "attempt_id" not in item
        assert "fencing_token" not in item
        assert "lease_owner" not in item
    assert endpoint.controller.store.get(claimed["job_id"])["attempt_id"] == claimed["attempt_id"]


def test_routing_submission_budget_and_real_assignment_survive_safe_api_projection(endpoint):
    endpoint.client.call('/submit', {'objective':'A short operational checklist', 'chapter_count':2,
        'workflow_id':'routed-workflow','max_attempts':1,'max_dispatches':1})
    claimed=endpoint.controller.store.claim('private-routing-controller')
    public=endpoint.client.call('/workflow/routed-workflow')
    job=next(row for row in public['jobs'] if row['job_id']==claimed['job_id'])
    assert job['max_attempts']==job['routing']['dispatches']==job['routing']['max_dispatches']==1
    assert job['routing']['score_details']['score']==claimed['routing']['score_details']['score']
    assert job['route']['provider']==claimed['route']['provider']
    assert job['route']['reservation_sha256']==claimed['route']['reservation_sha256']
    health=endpoint.client.call('/health')
    assert health['routing']['active_reservations'][0]['reservation_sha256']==job['route']['reservation_sha256']
    serialized=json.dumps([public,health])
    assert claimed['attempt_id'] not in serialized
    assert claimed['route']['reservation_id'] not in serialized
    assert 'fencing_token' not in serialized
    assert 'private-routing-controller' not in serialized


@pytest.mark.parametrize('value',[True,0,-1,'1',1000001])
def test_invalid_dispatch_budget_cannot_create_workflow(endpoint,value):
    status,_=request(endpoint,'POST','/submit',{'objective':'Budget validation','chapter_count':2,'max_dispatches':value},
                     authorization='Bearer '+endpoint.token)
    assert status==400
    assert endpoint.controller.store.list_workflows()==[]


def operator_held_job(endpoint, *, max_dispatches=3):
    endpoint.client.call('/submit', {'objective':'Operator hold fixture','chapter_count':2,
        'workflow_id':'held-workflow','max_dispatches':max_dispatches})
    job=endpoint.controller.store.claim('private-hold-controller')
    assert endpoint.controller.store.fail(job['job_id'],job['attempt_id'],job['fencing_token'],
        'Native CLI model configuration requires operator correction',retry=True,category='configuration',hold_scope='job')
    held=endpoint.controller.store.get(job['job_id'])
    assert held['status']==('FAILED' if max_dispatches==1 else 'BLOCKED')
    return held


def test_explicit_routing_resume_requires_auth_and_preserves_budgets_and_policy(endpoint):
    held=operator_held_job(endpoint)
    data={'job_id':held['job_id'],'reason':'Corrected the configured native CLI prerequisite'}
    assert request(endpoint,'POST','/routing/resume',data)[0]==401
    assert endpoint.controller.store.get(held['job_id'])['status']=='BLOCKED'
    public=endpoint.client.call('/routing/resume',data)
    resumed=next(job for job in public['jobs'] if job['job_id']==held['job_id'])
    assert resumed['status']=='PENDING_RETRY'
    assert resumed['attempts']==held['attempts']
    for field in ('failure_count','dispatches','max_dispatches','cycle','expires_at','score_details','policy','candidates'):
        assert resumed['routing'][field]==held['routing'][field]
    event=next(event for event in public['events'] if event['event']=='ROUTING_RESUMED')
    assert event['details']['reason']==data['reason']


@pytest.mark.parametrize('reason',[None,'','  ',42,'x'*513,'bad\x00reason'])
def test_resume_without_bounded_operator_reason_cannot_change_job(endpoint,reason):
    held=operator_held_job(endpoint)
    status,_=request(endpoint,'POST','/routing/resume',{'job_id':held['job_id'],'reason':reason},
                     authorization='Bearer '+endpoint.token)
    assert status==400
    assert endpoint.controller.store.get(held['job_id'])['status']=='BLOCKED'


def test_operator_resume_cannot_reset_an_exhausted_dispatch_budget(endpoint):
    held=operator_held_job(endpoint,max_dispatches=1)
    status,value=request(endpoint,'POST','/routing/resume',{'job_id':held['job_id'],'reason':'Retry anyway'},
                         authorization='Bearer '+endpoint.token)
    assert status==400
    unchanged=endpoint.controller.store.get(held['job_id'])
    assert unchanged['routing']['dispatches']==unchanged['routing']['max_dispatches']==1
    assert unchanged['status']=='FAILED'


def test_actual_cancellation_fences_a_live_database_lease(endpoint):
    submitted(endpoint)
    claimed = endpoint.controller.store.claim("private-test-controller")
    result = endpoint.client.call("/cancel", {"workflow_id": "service-workflow"})
    assert result["status"] == "FAILED"
    assert not endpoint.controller.store.heartbeat(claimed["job_id"], claimed["attempt_id"], claimed["fencing_token"])
    assert endpoint.controller.store.claim("later-owner") is None


@pytest.mark.parametrize("path", ["/claim", "/complete", "/heartbeat", "/workflow/service-workflow/complete"])
def test_even_authenticated_clients_cannot_mutate_worker_authority(endpoint, path):
    status, result = request(endpoint, "POST", path, {}, authorization="Bearer " + endpoint.token)
    assert status == 404
    assert "private" in result["error"]


@pytest.mark.parametrize("raw", ["{", "[]", "null", '{"objective":"x","chapter_count":0}', '{"objective":"x","requirements":[]}'])
def test_invalid_submissions_return_errors_without_creating_workflows(endpoint, raw):
    status, _ = request(endpoint, "POST", "/submit", raw=raw, authorization="Bearer " + endpoint.token)
    assert status == 400
    assert endpoint.controller.store.list_workflows() == []


def test_oversized_declared_body_is_rejected_before_read(endpoint):
    status, _ = request(endpoint, "POST", "/submit", raw="{}", content_length=4194305,
                        authorization="Bearer " + endpoint.token)
    assert status == 400
    assert endpoint.controller.store.list_workflows() == []


def test_request_larger_than_identity_pool_is_rejected_before_creation(endpoint):
    status, result = request(endpoint, "POST", "/submit", {
        "objective": "Seven chapters need seven separate worker identities",
        "chapter_count": 7,
    }, authorization="Bearer " + endpoint.token)
    assert status == 400
    assert endpoint.controller.store.list_workflows() == []


def test_non_ascii_authorization_is_rejected_without_dropping_response(endpoint):
    status, result = request(endpoint, "GET", "/health", authorization="Bearer \u00ff")
    assert status == 401
    assert result["error"] == "Unauthorized"


def test_real_partial_connections_are_bounded_and_released(endpoint):
    connections = []
    try:
        # None of these connections supplies complete headers or credentials.
        # The seventeenth request must be closed instead of spawning more
        # indefinitely blocked request handlers in the privileged controller.
        for _ in range(16):
            connection = socket.create_connection(("127.0.0.1", endpoint.port), timeout=3)
            connections.append(connection)
            connection.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nX-Pending: ")
        with socket.create_connection(("127.0.0.1", endpoint.port), timeout=3) as excess:
            excess.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            try:
                assert excess.recv(1024) == b""
            except ConnectionResetError:
                pass
    finally:
        for connection in connections:
            connection.close()
    deadline = time.monotonic() + 3
    while True:
        try:
            assert endpoint.client.call("/health")["test_kind"] == "http-and-sqlite"
            break
        except (ConnectionError, http.client.RemoteDisconnected):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.01)


def test_client_surfaces_authentication_error_without_token_disclosure(endpoint, tmp_path):
    token_file = tmp_path / "wrong-token"
    token_file.write_text("wrong", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unauthorized") as error:
        ControlClient(endpoint.port, token_file).call("/health")
    assert endpoint.token not in str(error.value)
