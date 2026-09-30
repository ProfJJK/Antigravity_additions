"""Warden IPC channels.

connect_hv_socket (MC-HW-51): Hyper-V socket readiness probe with an
AF_INET loopback fallback channel.

Semantics (read before relying on the return value):
    True means the host can allocate a working IPC socket right now. That is
    either an AF_HYPERV descriptor, or a loopback TCP round trip that really
    completed. It does NOT mean the guest VM identified by ``socket_guid`` is
    reachable or that a service is listening on ``port``. The GUID and port
    are validated but no guest handshake is attempted, because no guest
    service GUID is defined in the current SRS.
"""

from __future__ import annotations

import re
import socket
from typing import Any

GUID_REGEX = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

DEFAULT_HV_PORT = 10000
PROBE_TIMEOUT_S = 0.2  # hard ceiling per AC4 is 0.5 s
_LOOPBACK_HOST = "127.0.0.1"
_PORT_MIN = 1
_PORT_MAX = 65535


def _validate_guid(socket_guid: Any) -> str:
    if not isinstance(socket_guid, str):
        raise ValueError(
            f"socket_guid must be a str, got {type(socket_guid).__name__}"
        )
    if not socket_guid:
        raise ValueError("socket_guid must not be empty")
    # fullmatch (not match + '$') so a trailing newline is rejected.
    if GUID_REGEX.fullmatch(socket_guid) is None:
        raise ValueError(
            f"socket_guid {socket_guid!r} is not a 36-character hyphenated GUID"
        )
    return socket_guid


def _validate_port(port: Any) -> int:
    # bool is a subclass of int; reject it explicitly before the range check.
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(f"port must be an int, got {type(port).__name__}")
    if not (_PORT_MIN <= port <= _PORT_MAX):
        raise ValueError(
            f"port {port} out of range [{_PORT_MIN}, {_PORT_MAX}]"
        )
    return port


def _probe_hyperv() -> bool:
    """Try to allocate an AF_HYPERV stream socket with a bounded timeout.

    Returns False if the platform has no AF_HYPERV support or if the kernel
    refuses the socket (OSError).
    """
    family = getattr(socket, "AF_HYPERV", None)
    proto = getattr(socket, "HV_PROTOCOL_RAW", None)
    if family is None or proto is None:
        return False
    try:
        with socket.socket(family, socket.SOCK_STREAM, proto) as hv_sock:
            hv_sock.settimeout(PROBE_TIMEOUT_S)
            return hv_sock.fileno() != -1
    except OSError:
        return False


def _probe_loopback() -> bool:
    """Do a real loopback TCP round trip on an ephemeral port.

    Binds a listener on 127.0.0.1:0, connects a client to it, accepts, and
    checks that the peer addresses agree. Every blocking step is capped at
    PROBE_TIMEOUT_S. If any step fails (socket.timeout is an OSError
    subclass), the probe returns False.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.settimeout(PROBE_TIMEOUT_S)
            listener.bind((_LOOPBACK_HOST, 0))
            listener.listen(1)
            listen_addr = listener.getsockname()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(PROBE_TIMEOUT_S)
                client.connect(listen_addr)
                peer, _peer_addr = listener.accept()
                with peer:
                    return peer.getpeername() == client.getsockname()
    except OSError:
        return False


def connect_hv_socket(socket_guid: str, port: int = DEFAULT_HV_PORT) -> bool:
    """Probe host IPC readiness: AF_HYPERV first, then AF_INET loopback.

    Raises ValueError for a malformed GUID or an invalid port. Never lets an
    OSError from either probe escape to the caller. See the module docstring
    for what True does and does not guarantee.
    """
    _validate_guid(socket_guid)
    _validate_port(port)
    if _probe_hyperv():
        return True
    return _probe_loopback()
