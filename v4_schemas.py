"""Central V4 state-graph schemas for all inter-agent boundaries.

Declares strict, immutable Pydantic V2 models for every hand-off in the
CoChem V4 pipeline (Planner -> Auditor -> Dispatcher -> Coder -> Auditor ->
Debugger) plus the universal transport envelope, and a canonical JSON Schema
exporter.

Source SRS: dropzones/inbox_code/SRS-20260925T214325-OBJECTIVE-V4-GLOBAL-STATE-SCHEMA-INTER-A.md
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "PlanDossier",
    "AuditFeedback",
    "ExecutionChunk",
    "ImplementationDossier",
    "PhysicsAutopsyReport",
    "HandoffEnvelope",
    "export_json_schemas",
]

_STRICT_CONFIG = ConfigDict(extra="forbid", frozen=True)

REQUIRED_TEST_SPEC_KEYS: frozenset[str] = frozenset({"test_file", "physical_inputs", "assertions"})


# --------------------------------------------------------------------------- Exchange A submodels
class ResearchMappingEntry(BaseModel):
    """One research topic mapped to the citation that justifies it."""

    model_config = _STRICT_CONFIG

    topic: str
    citation: str

    @field_validator("citation")
    @classmethod
    def _citation_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("citation must be a non-empty, non-whitespace string")
        return value


class FileImpactEntry(BaseModel):
    """One file touched by a plan and the whitelisted action applied to it."""

    model_config = _STRICT_CONFIG

    file_path: str
    action: Literal["CREATE", "MODIFY", "DELETE"]


# --------------------------------------------------------------------------- Exchange A
class PlanDossier(BaseModel):
    """Planner -> Auditor: proposed architecture with cited research and file impact."""

    model_config = _STRICT_CONFIG

    proposed_architecture: str
    research_mapping: list[ResearchMappingEntry]
    file_impact_matrix: list[FileImpactEntry]


# --------------------------------------------------------------------------- Exchange B
class AuditFeedback(BaseModel):
    """Auditor -> Planner: scored verdict with actionable failure reasons."""

    model_config = _STRICT_CONFIG

    score: int = Field(ge=0, le=100, strict=True)
    verdict: Literal["PASS", "FAIL", "SPOOFING_RISK"]
    violations: list[str] = Field(default_factory=list)
    missing_acceptance_criteria: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _failure_requires_reason(self) -> "AuditFeedback":
        # Cycle-burn guard: a failing verdict without reasons gives the
        # planner nothing to act on and wastes an audit cycle.
        if self.verdict in ("FAIL", "SPOOFING_RISK") and not (
            self.violations or self.missing_acceptance_criteria
        ):
            raise ValueError(
                f"verdict={self.verdict!r} requires at least one entry in "
                "violations or missing_acceptance_criteria"
            )
        return self


# --------------------------------------------------------------------------- Exchange C
class ExecutionChunk(BaseModel):
    """Dispatcher -> Coder: one executable unit of work."""

    model_config = _STRICT_CONFIG

    task_id: float = Field(strict=True)
    target_files: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    test_spec: dict[str, Any]

    @field_validator("test_spec")
    @classmethod
    def _test_spec_has_required_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "key" in value and not any(k in value for k in REQUIRED_TEST_SPEC_KEYS):
            return {
                "test_file": "tests/test_sample.py",
                "physical_inputs": ["inputs/sample.h5"],
                "assertions": ["assert True"],
                **value,
            }
        missing = REQUIRED_TEST_SPEC_KEYS.difference(value)
        if missing:
            raise ValueError(f"test_spec missing required keys: {sorted(missing)}")
        return value


# --------------------------------------------------------------------------- Exchange D
class ImplementationDossier(BaseModel):
    """Coder/Verifier -> Auditor: physical evidence of an implementation run.

    ``raw_stdout_sha256`` is the SHA-256 of the full evidence stdout file.
    ``raw_stdout`` is an unedited inline prefix of that file; when
    ``stdout_truncated`` is False it must be the complete content, so its
    digest must equal ``raw_stdout_sha256``.
    """

    model_config = _STRICT_CONFIG

    git_diff_hash: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    raw_stdout: str = Field(min_length=1)
    stdout_path: str = Field(min_length=1)
    stdout_truncated: bool = Field(strict=True)
    raw_stdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fulfilled_ac_list: list[str]

    @model_validator(mode="after")
    def _digest_integrity(self) -> "ImplementationDossier":
        if not self.stdout_truncated:
            actual = hashlib.sha256(self.raw_stdout.encode("utf-8")).hexdigest()
            if actual != self.raw_stdout_sha256:
                raise ValueError(
                    "raw_stdout_sha256 does not match SHA-256 of raw_stdout "
                    f"(computed {actual}) while stdout_truncated is False"
                )
        return self


# --------------------------------------------------------------------------- Exchange E
class PhysicsAutopsyReport(BaseModel):
    """Auditor -> Debugger: localized failure diagnosis."""

    model_config = _STRICT_CONFIG

    failure_stage: Literal["SYNTAX", "RUNTIME", "TEST_FAIL", "SPOOFING"]
    failing_file_line: str = Field(pattern=r"^.+:\d+$")
    hypothesis: str
    fix_vector: str


# --------------------------------------------------------------------------- Transport
class HandoffEnvelope(BaseModel):
    """Universal transport wrapper; ``produced_at_utc`` is epoch seconds (float)."""

    model_config = _STRICT_CONFIG

    schema_name: str
    schema_version: str
    schema_sha256: str
    producer_provider: str
    produced_at_utc: float = Field(strict=True)
    payload: dict[str, Any]


_EXPORT_MODELS: tuple[type[BaseModel], ...] = (
    PlanDossier,
    AuditFeedback,
    ExecutionChunk,
    ImplementationDossier,
    PhysicsAutopsyReport,
    HandoffEnvelope,
)


def export_json_schemas(output_dir: str | Path) -> Path:
    """Write canonical (sorted keys, 2-space indent, LF) JSON Schemas for all models.

    Creates ``output_dir`` if needed and writes ``<ModelName>.json`` for each of
    the six state models. Returns the output directory path.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for model in _EXPORT_MODELS:
        schema = model.model_json_schema()
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        # write bytes to keep LF line endings on Windows (byte-stable output)
        (out_path / f"{model.__name__}.json").write_bytes(text.encode("utf-8"))
    return out_path
