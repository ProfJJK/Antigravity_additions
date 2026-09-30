"""DSP Pipeline Context and Configuration Dataclasses (MC-DSP-02).

Implements immutable pipeline execution context and DSP configuration
manifests conforming to SRS-412-07 (Domain-Specific Pipelines).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResourceCaps:
    """Resource quota constraints for a domain pipeline (SRS-412-07-FR-007)."""
    memory_mb: int = 4096
    max_workers: int = 6


@dataclass(frozen=True)
class DSPConfig:
    """Registration and configuration manifest for domain pipelines (SRS-412-07)."""
    dsp_id: str
    domain: str
    version: str = "4.1.2"
    supported_job_types: tuple[str, ...] = field(default_factory=tuple)
    resource_caps: ResourceCaps = field(default_factory=ResourceCaps)
    mcp_server: str = ""


@dataclass(frozen=True)
class PipelineContext:
    """Immutable context carried across domain pipeline stages."""
    task_id: str
    target_file: str
    timeout_sec: int = 900
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_artifact(self, artifact_path: str | Path) -> PipelineContext:
        """Returns a new PipelineContext with the specified artifact appended."""
        return PipelineContext(
            task_id=self.task_id,
            target_file=self.target_file,
            timeout_sec=self.timeout_sec,
            artifacts=self.artifacts + (str(artifact_path),),
            metadata=dict(self.metadata),
        )

    def has_artifact(self, artifact_path: str | Path) -> bool:
        """Checks if a given artifact path is recorded in context artifacts."""
        return str(artifact_path) in self.artifacts
