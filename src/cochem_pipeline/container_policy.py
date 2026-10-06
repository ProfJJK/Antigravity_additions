"""Operator-owned, immutable offline test-container policy.

Commands and image IDs come from protected configuration, never model output.
Only local Docker endpoints are accepted; Windows Docker Desktop's named pipe
and Linux/WSL Unix sockets both avoid host-path translation and bind mounts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from pathlib import PureWindowsPath
from typing import Any

_IMAGE = re.compile(r'^sha256:[0-9a-f]{64}$')
_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
    return value


@dataclass(frozen=True)
class TestCommand:
    name: str
    argv: tuple[str, ...]
    kind: str = 'pytest'
    timeout_seconds: int = 300

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or set(raw) - {'name', 'argv', 'kind', 'timeout_seconds'}:
            raise ValueError('Invalid operator test command')
        name, argv, kind = raw.get('name'), raw.get('argv'), raw.get('kind', 'pytest')
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError('Test command needs a stable name')
        if not isinstance(argv, (list, tuple)) or not argv or len(argv) > 128 or any(
                not isinstance(arg, str) or not arg or '\0' in arg or len(arg) > 8192 for arg in argv):
            raise ValueError('Test command argv must be a bounded nonempty string array')
        if kind not in ('pytest', 'command'):
            raise ValueError('Test command kind must be pytest or command')
        if kind == 'pytest':
            if list(argv[:3]) not in (['python', '-m', 'pytest'], ['python3', '-m', 'pytest']):
                raise ValueError('pytest commands must use python -m pytest')
            if any(arg.startswith(('--junit', '--collect-only', '--co')) for arg in argv[3:]):
                raise ValueError('Controller owns JUnit and tests must actually execute')
        return cls(name, tuple(argv), kind,
                   _integer(raw.get('timeout_seconds', 300), 'timeout_seconds', 1, 3600))

    def as_dict(self):
        return {'name': self.name, 'argv': list(self.argv), 'kind': self.kind,
                'timeout_seconds': self.timeout_seconds}


@dataclass(frozen=True)
class DockerPolicy:
    enabled: bool = False
    image: str = ''
    allowed_images: tuple[str, ...] = ()
    commands: tuple[TestCommand, ...] = ()
    executable: str = 'docker'
    endpoint: str = 'unix:///var/run/docker.sock'
    pipe_server_executables: tuple[str, ...] = ()
    memory_mb: int = 4096
    cpus: float = 2.0
    pids_limit: int = 512
    tmpfs_mb: int = 2048
    max_containers: int = 4
    warm_pool_size: int = 4
    max_source_mb: int = 128
    max_source_files: int = 20000
    output_limit_bytes: int = 1048576
    junit_limit_bytes: int = 4194304
    cleanup_timeout_seconds: int = 30

    @classmethod
    def from_dict(cls, raw: dict | None = None):
        raw = {} if raw is None else raw
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError('Unknown Docker policy fields')
        enabled = raw.get('enabled', False)
        if type(enabled) is not bool:
            raise ValueError('docker.enabled must be boolean')
        image, allowed = raw.get('image', ''), raw.get('allowed_images', [])
        if not isinstance(image, str) or not isinstance(allowed, (list, tuple)) or any(
                not isinstance(value, str) or not _IMAGE.fullmatch(value) for value in allowed):
            raise ValueError('Docker requires exact local sha256 image IDs')
        if enabled and (not _IMAGE.fullmatch(image) or image not in allowed):
            raise ValueError('Enabled Docker image must be an exact allowlisted local image ID')
        if image and not _IMAGE.fullmatch(image):
            raise ValueError('Tags and mutable Docker image references are not accepted')
        raw_commands = raw.get('commands', [])
        if not isinstance(raw_commands, (list, tuple)):
            raise ValueError('Docker commands must be an operator-owned array')
        commands = tuple(TestCommand.from_dict(value) for value in raw_commands)
        if len(commands) > 32 or len({command.name for command in commands}) != len(commands):
            raise ValueError('Test command names must be unique; at most 32 commands')
        if enabled and not commands:
            raise ValueError('Enabled Docker policy needs operator-owned test commands')
        executable = raw.get('executable', 'docker')
        if not isinstance(executable, str) or not executable or '\0' in executable:
            raise ValueError('Docker executable must be one path, not a shell command')
        endpoint = raw.get('endpoint', 'unix:///var/run/docker.sock')
        if not isinstance(endpoint, str) or not (
            endpoint.startswith('unix:///') or re.fullmatch(r'npipe:////\./pipe/[A-Za-z0-9_.-]+', endpoint)
        ) or any(char in endpoint for char in ('\0', '\n', '\r')):
            raise ValueError('Docker endpoint must be a local Unix socket or Windows named pipe')
        pipe_servers = raw.get('pipe_server_executables', [])
        if not isinstance(pipe_servers, (list, tuple)) or len(pipe_servers) > 16 or any(
                not isinstance(path, str) or '\0' in path or not PureWindowsPath(path).is_absolute()
                or not re.fullmatch('[A-Za-z]:', PureWindowsPath(path).drive)
                or '..' in PureWindowsPath(path).parts or PureWindowsPath(path).suffix.casefold() != '.exe'
                for path in pipe_servers):
            raise ValueError('Docker named-pipe servers require exact absolute local Windows executable paths')
        if len({str(PureWindowsPath(path)).casefold() for path in pipe_servers}) != len(pipe_servers):
            raise ValueError('Docker named-pipe server paths must be unique')
        cpus = raw.get('cpus', 2.0)
        if type(cpus) not in (float, int) or not math.isfinite(cpus) or not 0.1 <= cpus <= 2:
            raise ValueError('Container cpus must be between 0.1 and 2')
        numeric = {}
        for key, default, low, high in (
            ('memory_mb', 4096, 64, 4096), ('pids_limit', 512, 16, 512),
            ('tmpfs_mb', 2048, 16, 2048), ('max_containers', 4, 1, 4),
            ('warm_pool_size', 4, 0, 4),
            ('max_source_mb', 128, 1, 512), ('max_source_files', 20000, 1, 100000),
            ('output_limit_bytes', 1048576, 1024, 8388608),
            ('junit_limit_bytes', 4194304, 1024, 16777216),
            ('cleanup_timeout_seconds', 30, 1, 60),
        ):
            numeric[key] = _integer(raw.get(key, min(default, numeric.get('max_containers', default))
                                           if key == 'warm_pool_size' else default), key, low, high)
        if numeric['warm_pool_size'] > numeric['max_containers']:
            raise ValueError('Warm pool cannot exceed the global container ceiling')
        return cls(enabled, image, tuple(allowed), commands, executable, endpoint,
                   cpus=float(cpus), pipe_server_executables=tuple(pipe_servers), **numeric)

    def as_dict(self):
        return {name: [command.as_dict() for command in self.commands] if name == 'commands'
                else list(getattr(self, name)) if name in ('allowed_images', 'pipe_server_executables')
                else getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def pool_digest(self):
        # Empty, single-use containers have no commands or source yet. Only
        # their immutable engine/image/security/resource profile determines
        # compatibility; captured per-job test commands remain independent.
        keys = ('image', 'executable', 'endpoint', 'memory_mb', 'cpus', 'pids_limit', 'tmpfs_mb')
        return hashlib.sha256(json.dumps({key: getattr(self, key) for key in keys},
                                         sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()


def load_docker_policy(raw: dict[str, Any] | None = None) -> DockerPolicy:
    return DockerPolicy.from_dict(raw)
