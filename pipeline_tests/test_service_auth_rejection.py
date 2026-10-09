"""Real loopback auth rejection with fragmented bodies; no model or production service."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time

import pytest

from cochem_pipeline.service import ControlClient
from pipeline_tests.test_service import endpoint


@pytest.mark.parametrize("valid_json", [True, False])
def test_unauthorized_fragmented_post_returns_401_without_reset_or_jobs(endpoint, valid_json):
    body = json.dumps({"objective": "untrusted" * 32768}).encode() if valid_json else b"invalid-json" * 32768

    def fragments():
        # Force the body to arrive after headers, as a normal HTTP client may
        # do. Immediate server close leaves unread TCP data on Windows.
        for offset in range(0, len(body), 8192):
            yield body[offset:offset + 8192]
            time.sleep(.002)

    connection = http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=3)
    try:
        connection.request("POST", "/submit", fragments(), {
            "Authorization": "Bearer wrong",
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        assert response.status == 401
        assert json.loads(response.read()) == {"error": "Unauthorized"}
    finally:
        connection.close()
    assert endpoint.controller.store.list_workflows() == []


def test_auth_rejection_is_immediate_and_incomplete_body_drain_is_bounded(endpoint):
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=3) as connection:
        started = time.monotonic()
        connection.sendall(b"POST /submit HTTP/1.1\r\nHost: localhost\r\n"
                           b"Authorization: Bearer wrong\r\nContent-Length: 4194304\r\n\r\n")
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 401
        assert response.getheader("Connection") == "close"
        assert json.loads(response.read()) == {"error": "Unauthorized"}
        # The client deliberately never sends the declared body. The server
        # must release its bounded handler without a five-second request wait.
        connection.settimeout(2)
        assert connection.recv(1) == b""
        assert time.monotonic() - started < 2
    assert endpoint.controller.store.list_workflows() == []


def test_repeated_real_control_client_rejects_wrong_token_without_job_creation(endpoint, tmp_path):
    token_file = tmp_path / "bad-token"
    token_file.write_text("wrong", encoding="utf-8")
    client = ControlClient(endpoint.port, token_file)
    try:
        for _ in range(32):
            with pytest.raises(RuntimeError, match="^Unauthorized$"):
                client.call("/submit", {"objective": "never accepted", "chapter_count": 1})
    finally:
        client.close()
    assert endpoint.controller.store.list_workflows() == []


def test_rejected_body_trickle_cannot_extend_absolute_discard_deadline(endpoint):
    stop = threading.Event()
    sent = []
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=3) as connection:
        connection.sendall(b"POST /submit HTTP/1.1\r\nHost: localhost\r\n"
                           b"Authorization: Bearer wrong\r\nContent-Length: 4194304\r\n\r\n")
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 401
        assert json.loads(response.read()) == {"error": "Unauthorized"}

        def trickle():
            while not stop.wait(.05):
                try:
                    connection.sendall(b"x")
                    sent.append(1)
                except OSError:
                    return

        sender = threading.Thread(target=trickle)
        sender.start()
        try:
            started = time.monotonic()
            connection.settimeout(2)
            assert connection.recv(1) == b""
            assert time.monotonic() - started < 2
        finally:
            stop.set()
            sender.join(timeout=1)
        assert not sender.is_alive()
        assert len(sent) >= 3
    assert endpoint.controller.store.list_workflows() == []


@pytest.mark.parametrize("declared_length", ["invalid", "-1", "4194305"])
def test_rejected_invalid_or_oversized_declaration_does_not_start_body_drain(endpoint, declared_length):
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=3) as connection:
        connection.sendall(("POST /submit HTTP/1.1\r\nHost: localhost\r\n"
                            "Authorization: Bearer wrong\r\n"
                            f"Content-Length: {declared_length}\r\n\r\n").encode())
        response = http.client.HTTPResponse(connection)
        response.begin()
        assert response.status == 401
        assert json.loads(response.read()) == {"error": "Unauthorized"}
        connection.settimeout(.5)
        assert connection.recv(1) == b""
    assert endpoint.controller.store.list_workflows() == []
