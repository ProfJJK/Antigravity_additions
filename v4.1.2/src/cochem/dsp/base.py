"""Domain-Specific Pipeline (DSP) Abstract Interfaces and Base Plugin Framework (SRS-412-07).

Provides abstract base classes, lifecycle protocols, and data models for CoChem
Domain-Specific Pipelines (The Code Forge, The Academic Press, and The Pedagogy Engine)
and the DSP Creation Toolkit.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

logger = logging.getLogger("cochem.dsp.base")

# Canonical DSP Domain Identifiers (SRS-412-07-FR-001)
DOMAIN_CODE_FORGE: str = "code_forge"
DOMAIN_ACADEMIC_PRESS: str = "academic_press"
DOMAIN_PEDAGOGY_ENGINE: str = "pedagogy_engine"

CANONICAL_DSP_DOMAINS: frozenset[str] = frozenset({
    DOMAIN_CODE_FORGE,
    DOMAIN_ACADEMIC_PRESS,
    DOMAIN_PEDAGOGY_ENGINE,
})

# Windows Subprocess Window Suppression Flag (Rule 15)
CREATE_NO_WINDOW: int = 0x08000000


@dataclass(frozen=True)
class ResourceQuota:
    """Resource quota allocation for domain-specific pipelines (SRS-412-07-FR-007)."""
    memory_mb: int = 4096
    max_workers: int = 6
    cpu_limit: float = 2.0
    timeout_seconds: int = 900

    def to_dict(self) -> dict[str, Any]:
        """Serializes resource quota to dictionary."""
        return {
            "memory_mb": self.memory_mb,
            "max_workers": self.max_workers,
            "cpu_limit": self.cpu_limit,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceQuota:
        """Constructs ResourceQuota from dictionary mapping."""
        return cls(
            memory_mb=int(data.get("memory_mb", 4096)),
            max_workers=int(data.get("max_workers", 6)),
            cpu_limit=float(data.get("cpu_limit", 2.0)),
            timeout_seconds=int(data.get("timeout_seconds", 900)),
        )


@dataclass(frozen=True)
class DSPRegistrationManifest:
    """Registration manifest data model for DSP plugins (SRS-412-07 Section 7)."""
    dsp_id: str
    domain: str
    version: str = "4.1.2"
    supported_job_types: tuple[str, ...] = field(default_factory=tuple)
    resource_caps: ResourceQuota = field(default_factory=ResourceQuota)
    mcp_server: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes manifest to JSON-compatible dictionary."""
        return {
            "dsp_registration_manifest": {
                "dsp_id": self.dsp_id,
                "domain": self.domain,
                "version": self.version,
                "supported_job_types": list(self.supported_job_types),
                "resource_caps": self.resource_caps.to_dict(),
                "mcp_server": self.mcp_server,
            }
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DSPRegistrationManifest:
        """Parses registration manifest from dictionary."""
        payload = data.get("dsp_registration_manifest", data)
        raw_caps = payload.get("resource_caps", {})
        caps = ResourceQuota.from_dict(raw_caps) if isinstance(raw_caps, Mapping) else ResourceQuota()
        raw_types = payload.get("supported_job_types", ())
        types = tuple(str(t) for t in raw_types) if isinstance(raw_types, (list, tuple)) else ()
        return cls(
            dsp_id=str(payload.get("dsp_id", "")),
            domain=str(payload.get("domain", "")),
            version=str(payload.get("version", "4.1.2")),
            supported_job_types=types,
            resource_caps=caps,
            mcp_server=str(payload.get("mcp_server", "")),
        )


class IDomainPipeline(abc.ABC):
    """Abstract interface defining the canonical DSP triad (SRS-412-07)."""

    @abc.abstractmethod
    def validate(self, payload: dict[str, Any]) -> bool:
        """Validates domain payload structure and parameters."""
        raise TypeError(f"{self.__class__.__name__} must implement validate()")

    @abc.abstractmethod
    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executes domain workflow against given payload."""
        raise TypeError(f"{self.__class__.__name__} must implement execute()")

    @abc.abstractmethod
    def audit(self, result: dict[str, Any]) -> bool:
        """Audits workflow execution results against physical constraints."""
        raise TypeError(f"{self.__class__.__name__} must implement audit()")


class DSPPluginBase(IDomainPipeline, abc.ABC):
    """Abstract base class for all Domain-Specific Pipelines (SRS-412-07-FR-005).

    Encapsulates resource quotas, execution lifecycle hooks, payload validation,
    and domain-level verification. Bridges both the canonical IDomainPipeline triad
    (validate, execute, audit) and the specialized DSPPluginBase triad
    (validate_task_payload, execute_workflow_stage, run_domain_audit).
    """

    def __init__(self, domain_name: str, memory_cap_mb: int = 4096) -> None:
        """Initializes DSP plugin with domain identifier and memory allocation."""
        if not domain_name or not isinstance(domain_name, str):
            raise ValueError(f"domain_name must be a non-empty string, got: {domain_name!r}")
        if memory_cap_mb <= 0:
            raise ValueError(f"memory_cap_mb must be a positive integer, got: {memory_cap_mb}")

        self.domain_name = domain_name
        self.memory_cap_mb = memory_cap_mb
        self.resource_quota = ResourceQuota(memory_mb=memory_cap_mb)
        self._lifecycle_events: list[dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # DSP Abstract Plugin Protocol (SRS-412-07 Section 6)
    # -------------------------------------------------------------------------

    @abc.abstractmethod
    def validate_task_payload(self, payload: dict[str, Any]) -> bool:
        """Validates domain payload according to domain-specific schema."""
        raise TypeError(f"{self.__class__.__name__} must implement validate_task_payload()")

    @abc.abstractmethod
    def execute_workflow_stage(self, task_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """Executes a domain workflow stage for the specified task."""
        raise TypeError(f"{self.__class__.__name__} must implement execute_workflow_stage()")

    @abc.abstractmethod
    def run_domain_audit(self, artifact_path: str) -> bool:
        """Runs domain-level verification audit against physical output artifacts."""
        raise TypeError(f"{self.__class__.__name__} must implement run_domain_audit()")

    # -------------------------------------------------------------------------
    # Canonical IDomainPipeline Triad Implementation & Adapter
    # -------------------------------------------------------------------------

    def validate(self, payload: dict[str, Any]) -> bool:
        """Implements IDomainPipeline.validate via validate_task_payload."""
        if not isinstance(payload, dict):
            return False
        return self.validate_task_payload(payload)

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Executes domain workflow stage with lifecycle telemetry tracking."""
        if not self.validate(payload):
            raise ValueError(f"Invalid payload for domain {self.domain_name}: {payload}")

        task_id = str(payload.get("task_id", f"task_{self.domain_name}"))
        self.on_stage_start(task_id, "execution", payload)
        try:
            result = self.execute_workflow_stage(task_id, payload)
            self.on_stage_complete(task_id, "execution", result)
            return result
        except Exception as exc:
            self.on_stage_error(task_id, "execution", exc)
            raise

    def audit(self, result: dict[str, Any]) -> bool:
        """Implements IDomainPipeline.audit via run_domain_audit."""
        if not isinstance(result, dict):
            return False
        status = result.get("status")
        if status not in ("SUCCESS", "COMPLETED"):
            return False
        artifact_path = result.get("artifact_path") or result.get("result_path") or ""
        if artifact_path:
            return self.run_domain_audit(str(artifact_path))
        return True

    # -------------------------------------------------------------------------
    # Lifecycle Hooks (SRS-412-07-FR-005)
    # -------------------------------------------------------------------------

    def on_stage_start(self, task_id: str, stage_name: str, context: dict[str, Any]) -> None:
        """Hook triggered prior to workflow stage execution."""
        event = {
            "event": "stage_start",
            "task_id": task_id,
            "stage_name": stage_name,
            "domain": self.domain_name,
        }
        self._lifecycle_events.append(event)
        logger.debug("Domain %s started stage %s for task %s", self.domain_name, stage_name, task_id)

    def on_stage_complete(self, task_id: str, stage_name: str, result: dict[str, Any]) -> None:
        """Hook triggered upon successful completion of workflow stage."""
        event = {
            "event": "stage_complete",
            "task_id": task_id,
            "stage_name": stage_name,
            "domain": self.domain_name,
            "status": result.get("status", "COMPLETED"),
        }
        self._lifecycle_events.append(event)
        logger.debug("Domain %s completed stage %s for task %s", self.domain_name, stage_name, task_id)

    def on_stage_error(self, task_id: str, stage_name: str, error: Exception) -> None:
        """Hook triggered when workflow stage encounters an error."""
        event = {
            "event": "stage_error",
            "task_id": task_id,
            "stage_name": stage_name,
            "domain": self.domain_name,
            "error": str(error),
        }
        self._lifecycle_events.append(event)
        logger.error("Domain %s error in stage %s for task %s: %s", self.domain_name, stage_name, task_id, error)

    def teardown(self) -> None:
        """Cleans up domain resources and releases memory reservations."""
        self._lifecycle_events.clear()

    # -------------------------------------------------------------------------
    # Antigravity SDK Integration Helpers
    # -------------------------------------------------------------------------

    def get_antigravity_cli_path(self) -> str | None:
        """Locates the Antigravity CLI executable on the system PATH."""
        return shutil.which("agy")

    def invoke_antigravity_agent(
        self,
        agent_name: str,
        prompt: str,
        timeout_sec: int = 300,
    ) -> str:
        """Invokes an Antigravity agent CLI subprocess in windowless mode (Rule 10 & 15)."""
        cli_path = self.get_antigravity_cli_path()
        if not cli_path:
            raise FileNotFoundError("Antigravity CLI ('agy') not found on system PATH.")

        cmd = [cli_path, "--agent", agent_name, "-p", prompt]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=True,
                creationflags=CREATE_NO_WINDOW,
            )
            return proc.stdout.strip()
        except subprocess.CalledProcessError as exc:
            logger.error("Antigravity agent %s failed with code %d: %s", agent_name, exc.returncode, exc.stderr)
            raise RuntimeError(f"Antigravity invocation error: {exc.stderr.strip()}") from exc
