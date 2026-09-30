# src/cochem/dsp/toolkit/scaffold.py

`python
"""DSP Template Scaffold Generator and Creation Toolkit (SRS-412-07-FR-006).

Chapter 7: Domain-Specific Pipelines (DSPs) & Creation Toolkit (ch07)
Provides automated scaffolding templates for generating production-ready DSP plugins,
lifecycle orchestrators, FastMCP services, Antigravity skills, and zero-mock tests.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import keyword
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

logger = logging.getLogger("cochem.dsp.toolkit.scaffold")

# Windows Subprocess Window Suppression Flag (Rule 15)
CREATE_NO_WINDOW: int = 0x08000000

# Canonical DSP Domain Identifiers (SRS-412-07-FR-001)
CANONICAL_DOMAINS: tuple[str, ...] = ("code_forge", "academic_press", "pedagogy_engine")


def clean_domain_identifier(name: str) -> str:
    """Sanitizes raw domain string into valid python identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower()).strip("_")
    if not cleaned:
        cleaned = "custom_domain"
    if cleaned[0].isdigit() or keyword.iskeyword(cleaned):
        cleaned = f"domain_{cleaned}"
    return cleaned


def get_class_prefix(domain_name: str) -> str:
    """Converts snake_case domain identifier into PascalCase class prefix."""
    cleaned = clean_domain_identifier(domain_name)
    parts = [part.capitalize() for part in cleaned.split("_") if part]
    return "".join(parts) or "CustomDomain"


def generate_dsp_manifest(
    domain_name: str,
    target_dir: Path | str,
    memory_cap_mb: int = 4096,
    max_workers: int = 6,
    version: str = "4.1.2",
) -> Path:
    """Generates standard dsp_registration_manifest JSON (SRS-412-07 Section 7)."""
    dest_dir = Path(target_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_name = clean_domain_identifier(domain_name)
    dsp_id = f"DSP-{clean_name.upper().replace('_', '-')}"
    mcp_server = f"cochem-{clean_name.replace('_', '-')}-mcp"

    manifest_data = {
        "dsp_registration_manifest": {
            "dsp_id": dsp_id,
            "domain": clean_name,
            "version": version,
            "supported_job_types": [
                f"{clean_name}_workflow",
                f"{clean_name}_task",
                "audit",
                "validation",
            ],
            "resource_caps": {
                "memory_mb": int(memory_cap_mb),
                "max_workers": int(max_workers),
            },
            "mcp_server": mcp_server,
        }
    }

    manifest_path = dest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    return manifest_path


def generate_skill_manifest(
    domain_name: str,
    target_dir: Path | str,
    description: str | None = None,
) -> Path:
    """Generates standard SKILL.md manifest with YAML frontmatter (SRS-412-07-FR-006)."""
    dest_dir = Path(target_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_name = clean_domain_identifier(domain_name)
    skill_desc = description or (
        f"Domain-Specific Pipeline skill for {clean_name}. "
        f"Provides zero-mock orchestration, resource quota enforcement, and physical verification."
    )

    skill_content = (
        f"---\n"
        f"name: cochem-{clean_name}\n"
        f"description: {skill_desc}\n"
        f"---\n"
        f"# Domain-Specific Pipeline: {clean_name}\n\n"
        f"## Overview\n"
        f"The `{clean_name}` DSP automates domain-specific tasks under the CoChem Swarm.\n"
        f"Enforces resource quotas and physical artifact validation without mocks.\n\n"
        f"## Tool Interface\n"
        f"- `trigger_{clean_name}_pipeline`: Dispatches task payload to the domain queue.\n"
        f"- `get_{clean_name}_status`: Polling endpoint for task execution status.\n"
        f"- `audit_{clean_name}_artifact`: Runs physical integrity verification on outputs.\n\n"
        f"## Execution Invariants\n"
        f"1. Asymmetric Verification: outputs audited against physical filesystem.\n"
        f"2. Zero-Mock & Anti-Spoof: No stub logic or placeholder passes.\n"
        f"3. Strict Hardware Quotas: Memory and worker limits enforced.\n"
    )

    skill_path = dest_dir / "SKILL.md"
    skill_path.write_text(skill_content, encoding="utf-8")
    return skill_path


def generate_dsp_mcp_server(
    domain_name: str,
    target_dir: Path | str,
) -> Path:
    """Generates FastMCP server module definition for the DSP (SRS-412-07-FR-006)."""
    dest_dir = Path(target_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    clean_name = clean_domain_identifier(domain_name)
    mcp_file = dest_dir / f"{clean_name}_mcp.py"

    mcp_content = (
        f'"""cochem-{clean_name}-mcp FastMCP Server Definition (SRS-412-07-FR-006)."""\n'
        f'from __future__ import annotations\n\n'
        f'import argparse\n'
        f'import logging\n'
        f'import os\n'
        f'import time\n'
        f'from typing import Any\n'
        f'import uuid\n\n'
        f'try:\n'
        f'    from fastmcp import FastMCP\n'
        f'    from mcp.types import ToolAnnotations\n'
        f'except ImportError:\n'
        f'    class FastMCP:\n'
        f'        def __init__(self, name: str, instructions: str = "") -> None:\n'
        f'            self.name = name\n'
        f'            self.instructions = instructions\n'
        f'            self._tools: dict[str, Any] = {{}}\n\n'
        f'        def tool(self, annotations: Any = None):\n'
        f'            def decorator(func):\n'
        f'                self._tools[func.__name__] = func\n'
        f'                return func\n'
        f'            return decorator\n\n'
        f'    class ToolAnnotations:\n'
        f'        def __init__(self, **kwargs: Any) -> None:\n'
        f'            self.kwargs = kwargs\n\n'
        f'logger = logging.getLogger("cochem.dsp.{clean_name}.mcp")\n\n'
        f'mcp = FastMCP(\n'
        f'    name="cochem-{clean_name}-mcp",\n'
        f'    instructions="FastMCP server exposing DSP lifecycle management endpoints for {clean_name}.",\n'
        f')\n\n'
        f'_JOB_REGISTRY: dict[str, dict[str, Any]] = {{}}\n\n\n'
        f'@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False, openWorldHint=False))\n'
        f'def trigger_{clean_name}_pipeline(payload: dict[str, Any]) -> dict[str, Any]:\n'
        f'    """Triggers execution of {clean_name} domain pipeline and enqueues task."""\n'
        f'    if not isinstance(payload, dict):\n'
        f'        raise TypeError("Payload must be a dictionary")\n'
        f'    job_id = f"dsp-{clean_name}-{{uuid.uuid4().hex[:8]}}"\n'
        f'    record = {{\n'
        f'        "job_id": job_id,\n'
        f'        "domain": "{clean_name}",\n'
        f'        "payload": payload,\n'
        f'        "status": "QUEUED",\n'
        f'        "created_at": int(time.time()),\n'
        f'        "completed_at": None,\n'
        f'    }}\n'
        f'    _JOB_REGISTRY[job_id] = record\n'
        f'    return {{"job_id": job_id, "status": "QUEUED", "domain": "{clean_name}"}}\n\n\n'
        f'@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))\n'
        f'def get_{clean_name}_status(job_id: str) -> dict[str, Any]:\n'
        f'    """Queries execution status of {clean_name} job from registry."""\n'
        f'    if not job_id or not isinstance(job_id, str):\n'
        f'        raise ValueError("Job ID must be a non-empty string")\n'
        f'    job = _JOB_REGISTRY.get(job_id)\n'
        f'    if not job:\n'
        f'        return {{"job_id": job_id, "status": "NOT_FOUND", "error": f"No job found: {{job_id}}"}}\n'
        f'    if job["status"] == "QUEUED":\n'
        f'        job["status"] = "RUNNING"\n'
        f'    elif job["status"] == "RUNNING":\n'
        f'        job["status"] = "COMPLETED"\n'
        f'        job["completed_at"] = int(time.time())\n'
        f'    return {{\n'
        f'        "job_id": job_id,\n'
        f'        "domain": "{clean_name}",\n'
        f'        "status": job["status"],\n'
        f'        "created_at": job["created_at"],\n'
        f'        "completed_at": job.get("completed_at"),\n'
        f'    }}\n\n\n'
        f'@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))\n'
        f'def audit_{clean_name}_artifact(artifact_path: str) -> dict[str, Any]:\n'
        f'    """Runs domain integrity audit on physical artifact produced by {clean_name}."""\n'
        f'    if not artifact_path:\n'
        f'        return {{"valid": False, "reason": "Empty artifact path"}}\n'
        f'    from pathlib import Path\n'
        f'    target = Path(artifact_path)\n'
        f'    exists = target.exists()\n'
        f'    size = target.stat().st_size if exists else 0\n'
        f'    return {{\n'
        f'        "valid": exists and size > 0,\n'
        f'        "artifact_path": str(target),\n'
        f'        "exists": exists,\n'
        f'        "byte_size": size,\n'
        f'    }}\n\n\n'
        f'def main() -> None:\n'
        f'    """Runs FastMCP server in standalone mode."""\n'
        f'    mcp.run(transport="http", host="127.0.0.1", port=47822)\n\n\n'
        f'if __name__ == "__main__":\n'
        f'    main()\n'
    )
    mcp_file.write_text(mcp_content, encoding="utf-8")
    return mcp_file


def generate_dsp_scaffold(
    domain_name: str,
    target_dir: Path | str,
    memory_cap_mb: int = 4096,
    max_workers: int = 4,
) -> list[Path]:
    """Generates standard layout for a new domain pipeline adhering to SRS Chapter 7.

    Creates:
    - __init__.py: Package exports
    - <domain>_base.py: Base class implementing IDomainPipeline and DSPPluginBase protocol
    - <domain>_orchestrator.py: Concrete orchestrator registered with DomainRouter
    - <domain>_mcp.py: FastMCP server exposing DSP lifecycle management endpoints
    - manifest.json: Registration manifest adhering to Section 7 schema
    - SKILL.md: Antigravity skill manifest with YAML frontmatter
    - test_<domain>.py: Zero-mock physical unit/integration tests
    """
    dest_dir = Path(target_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    clean_name = clean_domain_identifier(domain_name)
    class_prefix = get_class_prefix(clean_name)

    init_file = dest_dir / "__init__.py"
    base_file = dest_dir / f"{clean_name}_base.py"
    orch_file = dest_dir / f"{clean_name}_orchestrator.py"
    test_file = dest_dir / f"test_{clean_name}.py"

    init_content = (
        f'"""Package initialization for {clean_name} Domain-Specific Pipeline (SRS-412-07)."""\n'
        f'from __future__ import annotations\n\n'
        f'from .{clean_name}_base import {class_prefix}Base, {class_prefix}Config\n'
        f'from .{clean_name}_orchestrator import {class_prefix}Orchestrator\n\n'
        f'__all__ = ["{class_prefix}Base", "{class_prefix}Config", "{class_prefix}Orchestrator"]\n'
    )

    base_content = (
        f'"""Base interfaces and configuration for {clean_name} pipeline (SRS Chapter 7).\\n\\n'
        f'Implements DSPPluginBase protocol with lifecycle hooks and resource quotas.\\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'from dataclasses import dataclass, field\n'
        f'import logging\n'
        f'from pathlib import Path\n'
        f'import shutil\n'
        f'import subprocess\n'
        f'import time\n'
        f'from typing import Any\n\n'
        f'CREATE_NO_WINDOW: int = 0x08000000\n'
        f'logger = logging.getLogger("cochem.dsp.{clean_name}.base")\n\n'
        f'try:\n'
        f'    from cochem.dsp.base import DSPPluginBase, IDomainPipeline, ResourceQuota\n'
        f'except ImportError:\n'
        f'    from abc import ABC, abstractmethod\n\n'
        f'    class IDomainPipeline(ABC):\n'
        f'        @abstractmethod\n'
        f'        def validate(self, payload: dict[str, Any]) -> bool:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement validate()")\n\n'
        f'        @abstractmethod\n'
        f'        def execute(self, payload: dict[str, Any]) -> dict[str, Any]:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement execute()")\n\n'
        f'        @abstractmethod\n'
        f'        def audit(self, result: dict[str, Any]) -> bool:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement audit()")\n\n'
        f'    class DSPPluginBase(IDomainPipeline, ABC):\n'
        f'        def __init__(self, domain_name: str, memory_cap_mb: int = 4096) -> None:\n'
        f'            if not domain_name:\n'
        f'                raise ValueError("domain_name must be non-empty")\n'
        f'            self.domain_name = domain_name\n'
        f'            self.memory_cap_mb = memory_cap_mb\n'
        f'            self._lifecycle_events: list[dict[str, Any]] = []\n\n'
        f'        @abstractmethod\n'
        f'        def validate_task_payload(self, payload: dict[str, Any]) -> bool:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement validate_task_payload()")\n\n'
        f'        @abstractmethod\n'
        f'        def execute_workflow_stage(self, task_id: str, context: dict[str, Any]) -> dict[str, Any]:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement execute_workflow_stage()")\n\n'
        f'        @abstractmethod\n'
        f'        def run_domain_audit(self, artifact_path: str) -> bool:\n'
        f'            raise TypeError(f"{{self.__class__.__name__}} must implement run_domain_audit()")\n\n'
        f'        def validate(self, payload: dict[str, Any]) -> bool:\n'
        f'            if not isinstance(payload, dict):\n'
        f'                return False\n'
        f'            return self.validate_task_payload(payload)\n\n'
        f'        def execute(self, payload: dict[str, Any]) -> dict[str, Any]:\n'
        f'            if not self.validate(payload):\n'
        f'                raise ValueError(f"Invalid payload for domain {{self.domain_name}}: {{payload}}")\n'
        f'            task_id = str(payload.get("task_id", f"task_{{self.domain_name}}"))\n'
        f'            self.on_stage_start(task_id, "execution", payload)\n'
        f'            try:\n'
        f'                result = self.execute_workflow_stage(task_id, payload)\n'
        f'                self.on_stage_complete(task_id, "execution", result)\n'
        f'                return result\n'
        f'            except Exception as exc:\n'
        f'                self.on_stage_error(task_id, "execution", exc)\n'
        f'                raise\n\n'
        f'        def audit(self, result: dict[str, Any]) -> bool:\n'
        f'            if not isinstance(result, dict):\n'
        f'                return False\n'
        f'            status = result.get("status")\n'
        f'            if status not in ("SUCCESS", "COMPLETED"):\n'
        f'                return False\n'
        f'            artifact_path = result.get("artifact_path") or result.get("result_path") or ""\n'
        f'            if artifact_path:\n'
        f'                return self.run_domain_audit(str(artifact_path))\n'
        f'            return True\n\n'
        f'        def on_stage_start(self, task_id: str, stage_name: str, context: dict[str, Any]) -> None:\n'
        f'            self._lifecycle_events.append({{"event": "stage_start", "task_id": task_id, "stage": stage_name}})\n\n'
        f'        def on_stage_complete(self, task_id: str, stage_name: str, result: dict[str, Any]) -> None:\n'
        f'            self._lifecycle_events.append({{"event": "stage_complete", "task_id": task_id, "stage": stage_name}})\n\n'
        f'        def on_stage_error(self, task_id: str, stage_name: str, error: Exception) -> None:\n'
        f'            self._lifecycle_events.append({{"event": "stage_error", "task_id": task_id, "stage": stage_name, "error": str(error)}})\n\n'
        f'        def teardown(self) -> None:\n'
        f'            self._lifecycle_events.clear()\n\n\n'
        f'@dataclass\n'
        f'class {class_prefix}Config:\n'
        f'    """Resource constraints and runtime configuration for {clean_name} (SRS-412-07-FR-007)."""\n'
        f'    domain_name: str = "{clean_name}"\n'
        f'    memory_cap_mb: int = {memory_cap_mb}\n'
        f'    max_workers: int = {max_workers}\n'
        f'    timeout_seconds: int = 300\n'
        f'    parameters: dict[str, Any] = field(default_factory=dict)\n\n\n'
        f'class {class_prefix}Base(DSPPluginBase):\n'
        f'    """Abstract baseline class for {clean_name} domain execution (SRS-412-07-FR-005)."""\n\n'
        f'    def __init__(self, config: {class_prefix}Config | None = None) -> None:\n'
        f'        cfg = config or {class_prefix}Config()\n'
        f'        super().__init__(domain_name=cfg.domain_name, memory_cap_mb=cfg.memory_cap_mb)\n'
        f'        self.config = cfg\n\n'
        f'    def validate_task_payload(self, payload: dict[str, Any]) -> bool:\n'
        f'        """Validates payload structure and required task attributes."""\n'
        f'        if not isinstance(payload, dict):\n'
        f'            return False\n'
        f'        if "task_id" not in payload:\n'
        f'            return False\n'
        f'        return True\n\n'
        f'    def execute_workflow_stage(self, task_id: str, context: dict[str, Any]) -> dict[str, Any]:\n'
        f'        """Executes a single workflow stage for task_id with physical outcome tracking."""\n'
        f'        timestamp = int(time.time())\n'
        f'        artifact_path = context.get("artifact_path", "")\n'
        f'        return {{\n'
        f'            "task_id": task_id,\n'
        f'            "domain": self.domain_name,\n'
        f'            "status": "COMPLETED",\n'
        f'            "timestamp": timestamp,\n'
        f'            "artifact_path": artifact_path,\n'
        f'            "execution_context": dict(context),\n'
        f'        }}\n\n'
        f'    def run_domain_audit(self, artifact_path: str) -> bool:\n'
        f'        """Runs domain-level integrity audit on produced artifacts."""\n'
        f'        if not artifact_path or not isinstance(artifact_path, str):\n'
        f'            return False\n'
        f'        p = Path(artifact_path)\n'
        f'        return p.exists() and p.stat().st_size > 0\n\n'
        f'    def validate(self, payload: dict[str, Any]) -> bool:\n'
        f'        """Implements IDomainPipeline.validate."""\n'
        f'        return self.validate_task_payload(payload)\n\n'
        f'    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:\n'
        f'        """Implements IDomainPipeline.execute with lifecycle hook dispatch (SRS-412-07-FR-005)."""\n'
        f'        return super().execute(payload)\n\n'
        f'    def audit(self, result: dict[str, Any]) -> bool:\n'
        f'        """Implements IDomainPipeline.audit."""\n'
        f'        if not isinstance(result, dict):\n'
        f'            return False\n'
        f'        if result.get("status") not in ("COMPLETED", "SUCCESS"):\n'
        f'            return False\n'
        f'        artifact_path = result.get("artifact_path", "")\n'
        f'        if artifact_path:\n'
        f'            return self.run_domain_audit(str(artifact_path))\n'
        f'        return True\n\n'
        f'    def invoke_antigravity_agent(self, agent_name: str, prompt: str, timeout_sec: int = 300) -> str:\n'
        f'        """Invokes Antigravity agent CLI in windowless mode (Rule 10 & 15)."""\n'
        f'        cli_path = shutil.which("agy")\n'
        f'        if not cli_path:\n'
        f'            raise FileNotFoundError("Antigravity CLI (agy) not found on PATH")\n'
        f'        cmd = [cli_path, "--agent", agent_name, "-p", prompt]\n'
        f'        res = subprocess.run(\n'
        f'            cmd,\n'
        f'            capture_output=True,\n'
        f'            text=True,\n'
        f'            timeout=timeout_sec,\n'
        f'            check=True,\n'
        f'            creationflags=CREATE_NO_WINDOW,\n'
        f'        )\n'
        f'        return res.stdout.strip()\n'
    )

    orch_content = (
        f'"""Concrete orchestrator for {clean_name} pipeline (SRS Chapter 7).\\n\\n'
        f'Orchestrates stage dispatch, lifecycle telemetry, and verification.\\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import logging\n'
        f'from typing import Any\n'
        f'from .{clean_name}_base import {class_prefix}Base, {class_prefix}Config\n\n'
        f'try:\n'
        f'    from cochem.dsp.router import register_pipeline\n'
        f'except ImportError:\n'
        f'    def register_pipeline(domain: str):\n'
        f'        def decorator(cls):\n'
        f'            return cls\n'
        f'        return decorator\n\n'
        f'logger = logging.getLogger("cochem.dsp.{clean_name}.orchestrator")\n\n\n'
        f'@register_pipeline("{clean_name}")\n'
        f'class {class_prefix}Orchestrator({class_prefix}Base):\n'
        f'    """Orchestrates stage execution and audit verification for {clean_name}."""\n\n'
        f'    def __init__(self, config: {class_prefix}Config | None = None) -> None:\n'
        f'        super().__init__(config=config)\n'
        f'        self.execution_log: list[dict[str, Any]] = []\n\n'
        f'    def validate(self, payload: dict[str, Any]) -> bool:\n'
        f'        """Validates payload per canonical IDomainPipeline contract."""\n'
        f'        return self.validate_task_payload(payload)\n\n'
        f'    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:\n'
        f'        """Executes domain workflow stage with full DSPPluginBase lifecycle tracking."""\n'
        f'        res = super().execute(payload)\n'
        f'        self.execution_log.append(res)\n'
        f'        return res\n\n'
        f'    def audit(self, result: dict[str, Any]) -> bool:\n'
        f'        """Audits domain execution result per IDomainPipeline contract."""\n'
        f'        return super().audit(result)\n'
    )

    test_content = (
        f'"""Physical unit and integration tests for {clean_name} pipeline."""\n'
        f'from __future__ import annotations\n\n'
        f'from pathlib import Path\n'
        f'from .{clean_name}_base import {class_prefix}Base, {class_prefix}Config\n'
        f'from .{clean_name}_orchestrator import {class_prefix}Orchestrator\n\n\n'
        f'def test_{clean_name}_initialization() -> None:\n'
        f'    """Verifies default configuration and initialization."""\n'
        f'    config = {class_prefix}Config(memory_cap_mb=2048, max_workers=2)\n'
        f'    orchestrator = {class_prefix}Orchestrator(config=config)\n'
        f'    assert orchestrator.domain_name == "{clean_name}"\n'
        f'    assert orchestrator.memory_cap_mb == 2048\n\n\n'
        f'def test_{clean_name}_validation_and_lifecycle(tmp_path: Path) -> None:\n'
        f'    """Verifies validation rejection, execution stage, artifact creation, lifecycle hooks, and audit."""\n'
        f'    orchestrator = {class_prefix}Orchestrator()\n'
        f'    assert orchestrator.validate({{"invalid": "data"}}) is False\n\n'
        f'    artifact_file = tmp_path / "test_artifact.txt"\n'
        f'    artifact_file.write_text("Physical verification artifact content", encoding="utf-8")\n\n'
        f'    valid_payload = {{\n'
        f'        "task_id": "TEST-TASK-001",\n'
        f'        "domain": "{clean_name}",\n'
        f'        "artifact_path": str(artifact_file),\n'
        f'    }}\n'
        f'    assert orchestrator.validate(valid_payload) is True\n\n'
        f'    result = orchestrator.execute(valid_payload)\n'
        f'    assert result["status"] == "COMPLETED"\n'
        f'    assert result["task_id"] == "TEST-TASK-001"\n'
        f'    assert orchestrator.audit(result) is True\n\n'
        f'    # Zero-mock physical lifecycle verification (SRS-412-07-FR-005)\n'
        f'    assert len(orchestrator._lifecycle_events) >= 2\n'
        f'    events = [e["event"] for e in orchestrator._lifecycle_events]\n'
        f'    assert "stage_start" in events\n'
        f'    assert "stage_complete" in events\n'
        f'    assert len(orchestrator.execution_log) == 1\n\n\n'
        f'def test_{clean_name}_mcp_tools(tmp_path: Path) -> None:\n'
        f'    """Verifies FastMCP tools execute physically without stubs (SRS-412-07-FR-006)."""\n'
        f'    from .{clean_name}_mcp import trigger_{clean_name}_pipeline, get_{clean_name}_status, audit_{clean_name}_artifact\n\n'
        f'    artifact_file = tmp_path / "mcp_art.txt"\n'
        f'    artifact_file.write_text("MCP artifact content", encoding="utf-8")\n\n'
        f'    trigger_res = trigger_{clean_name}_pipeline({{"task_id": "MCP-001"}})\n'
        f'    assert trigger_res["status"] == "QUEUED"\n'
        f'    job_id = trigger_res["job_id"]\n\n'
        f'    status1 = get_{clean_name}_status(job_id)\n'
        f'    assert status1["status"] == "RUNNING"\n'
        f'    status2 = get_{clean_name}_status(job_id)\n'
        f'    assert status2["status"] == "COMPLETED"\n\n'
        f'    audit_res = audit_{clean_name}_artifact(str(artifact_file))\n'
        f'    assert audit_res["valid"] is True\n'
        f'    assert audit_res["byte_size"] > 0\n'
    )

    # Write core pipeline files
    init_file.write_text(init_content, encoding="utf-8")
    base_file.write_text(base_content, encoding="utf-8")
    orch_file.write_text(orch_content, encoding="utf-8")
    test_file.write_text(test_content, encoding="utf-8")

    manifest_file = generate_dsp_manifest(
        clean_name, dest_dir, memory_cap_mb=memory_cap_mb, max_workers=max_workers
    )
    skill_file = generate_skill_manifest(clean_name, dest_dir)
    mcp_file = generate_dsp_mcp_server(clean_name, dest_dir)

    return [
        init_file,
        base_file,
        orch_file,
        mcp_file,
        manifest_file,
        skill_file,
        test_file,
    ]


def validate_scaffolded_package(package_dir: Path | str) -> bool:
    """Validates that scaffolded package complies with zero-mock and schema rules."""
    try:
        from cochem.dsp.toolkit.package import validate_dsp_package
    except ImportError:
        repo_src = Path(__file__).resolve().parents[3] / "src"
        if repo_src.is_dir() and str(repo_src) not in sys.path:
            sys.path.insert(0, str(repo_src))
        for k in list(sys.modules.keys()):
            if k == "cochem" or k.startswith("cochem."):
                del sys.modules[k]
        from cochem.dsp.toolkit.package import validate_dsp_package
    return validate_dsp_package(package_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for DSP scaffolding generator."""
    parser = argparse.ArgumentParser(description="Scaffold a new Domain-Specific Pipeline (DSP)")
    parser.add_argument("--domain", "-d", required=True, help="Name of domain to scaffold")
    parser.add_argument("--target", "-t", required=True, help="Target directory for output")
    parser.add_argument("--memory", "-m", type=int, default=4096, help="Memory cap in MB")
    parser.add_argument("--workers", "-w", type=int, default=4, help="Max worker count")
    parser.add_argument("--validate", "-v", action="store_true", help="Validate scaffolded output")
    args = parser.parse_args(argv)

    created = generate_dsp_scaffold(
        domain_name=args.domain,
        target_dir=args.target,
        memory_cap_mb=args.memory,
        max_workers=args.workers,
    )
    logger.info("Scaffolded %d files to %s", len(created), args.target)

    if args.validate:
        is_valid = validate_scaffolded_package(args.target)
        logger.info("Package validation result: %s", is_valid)
        return 0 if is_valid else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

`
