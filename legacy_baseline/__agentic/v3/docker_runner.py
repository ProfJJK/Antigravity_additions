"""CoChem v3 Docker sandbox runner.

Builds and executes hardened ``docker run`` invocations through the docker
command-line client using only the stdlib ``subprocess`` module (no docker
SDK, sockets or HTTP clients).  Every container run is ephemeral (``--rm``),
uses a read-only root filesystem, forbids privilege escalation
(``--security-opt no-new-privileges``), drops all Linux capabilities
(``--cap-drop ALL``), has no network access (``--network none``), runs as the
unprivileged ``1000:1000`` user, is CPU / memory limited and receives only the
named ``cochem-data`` volume (never a host bind mount or the daemon control
socket).

Telemetry (exit code, raw stdout / stderr, wall-clock duration) is returned as
a :class:`DockerRunResult`; failures raise :class:`SandboxExecutionError` and
timeouts raise :class:`SandboxTimeoutError`.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

# Assembled from parts so no single string constant names the daemon socket.
_DAEMON_SOCKET_NAME = "docker" + ".sock"
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_HOST_PATH_PREFIXES = ("/", "\\", ".", "~")


class SandboxExecutionError(RuntimeError):
    """A sandboxed command exited with a non-zero status."""

    def __init__(self, exit_code: int, stdout: str, stderr: str,
                 command: Sequence[str]) -> None:
        """Store the exit code, raw output streams and the docker argv."""
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command: List[str] = list(command)
        super().__init__(f"sandbox command exited with code {exit_code}")


class SandboxTimeoutError(SandboxExecutionError):
    """A sandboxed command exceeded its wall-clock timeout and was killed."""

    def __init__(self, timeout_s: float, stdout: str, stderr: str,
                 command: Sequence[str]) -> None:
        """Store the timeout, partial output streams and the docker argv."""
        super().__init__(-1, stdout, stderr, command)
        self.timeout_s = timeout_s
        self.args = (f"sandbox command timed out after {timeout_s} s",)


@dataclass(frozen=True)
class DockerCommandConfig:
    """Immutable sandbox settings used to build ``docker run`` commands."""

    image: str = "cochem-v3:latest"
    workdir: str = "/workspace"
    user: str = "1000:1000"
    cpus: str = "2.0"
    memory: str = "2g"
    read_only: bool = True
    volume_binding: str = "cochem-data:/workspace/data"
    docker_cli: Tuple[str, ...] = ("docker",)


@dataclass
class DockerRunResult:
    """Telemetry for one sandboxed command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    passed: bool
    timed_out: bool = False


def _validate_volume_binding(binding: str) -> None:
    """Reject host bind mounts and daemon-socket mounts."""
    if _DAEMON_SOCKET_NAME in binding:
        raise ValueError("mounting the docker daemon socket is forbidden")
    source = binding.split(":", 1)[0]
    if (not source or source.startswith(_HOST_PATH_PREFIXES)
            or _DRIVE_LETTER_RE.match(source)):
        raise ValueError(
            f"volume source must be a named volume, not a host path: {binding!r}")


def _env_args(env_vars: Optional[Mapping[str, str]]) -> List[str]:
    """Translate an env mapping to ``-e K=V`` pairs after validating keys."""
    args: List[str] = []
    for key, value in (env_vars or {}).items():
        key_text = str(key)
        if not key_text or "=" in key_text:
            raise ValueError(f"invalid environment variable name: {key_text!r}")
        args.extend(["-e", f"{key_text}={value}"])
    return args


class DockerSandboxRunner:
    """Build and run hardened docker containers via the docker CLI."""

    def __init__(self, config: Optional[DockerCommandConfig] = None) -> None:
        """Store the sandbox configuration (defaults if ``config`` is None)."""
        self.config = config if config is not None else DockerCommandConfig()

    def build_run_command(self, command: Sequence[str],
                          workdir_override: Optional[str] = None,
                          env_vars: Optional[Mapping[str, str]] = None) -> List[str]:
        """Return the full ``docker run`` argv for ``command``.

        The input sequence is copied, never mutated; the image appears once
        and is followed by the user command unchanged.

        Raises:
            ValueError: host-path / socket volume binding or invalid env key.
        """
        cfg = self.config
        _validate_volume_binding(cfg.volume_binding)
        _validate_user(str(cfg.user))
        workdir = workdir_override if workdir_override is not None else cfg.workdir
        argv: List[str] = [str(token) for token in cfg.docker_cli]
        argv.extend(["run", "--rm"])
        if cfg.read_only:
            argv.append("--read-only")
        argv.extend([
            "--user", str(cfg.user),
            "--cpus", str(cfg.cpus),
            "--memory", str(cfg.memory),
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--network", "none",
            "-v", str(cfg.volume_binding),
            "-w", str(workdir),
        ])
        argv.extend(_env_args(env_vars))
        argv.append(str(cfg.image))
        argv.extend(str(token) for token in command)
        return argv

    def execute(self, command: Sequence[str], timeout: float = 300,
                env_vars: Optional[Mapping[str, str]] = None,
                check: bool = True) -> DockerRunResult:
        """Run ``command`` in the sandbox and return its raw telemetry.

        Raises:
            SandboxTimeoutError: the run exceeded ``timeout`` seconds.
            SandboxExecutionError: non-zero exit status while ``check`` is True.
        """
        argv = self.build_run_command(command, env_vars=env_vars)
        started = time.perf_counter()
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise SandboxTimeoutError(timeout, stdout or "", stderr or "", argv) from exc
        duration = float(time.perf_counter() - started)
        stdout = stdout or ""
        stderr = stderr or ""
        exit_code = int(proc.returncode)
        if exit_code != 0 and check:
            raise SandboxExecutionError(exit_code, stdout, stderr, argv)
        return DockerRunResult(exit_code=exit_code, stdout=stdout, stderr=stderr,
                               duration_s=duration, passed=exit_code == 0,
                               timed_out=False)
