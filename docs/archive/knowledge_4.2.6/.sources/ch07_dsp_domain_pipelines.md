# Chapter 7: Domain-Specific Pipelines (DSPs) & Creation Toolkit (ch07)
**Code Forge, Academic Press, Pedagogy Engine, and Plugin Extensibility**

- **Document ID**: SRS-412-07
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 2: Quarantine VM Sandbox](ch02_quarantine_vm.md) | [Chapter 5: Research-Driven TDD Pivot](ch05_research_tdd_pivot.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Domain-Specific Pipelines (DSPs)** and the **DSP Creation Toolkit**, replacing monolithic pipelines with specialized modular workflows.

---

## 2. Boundary
The DSP architecture encompasses the three canonical pipeline domains (The Code Forge, The Academic Press, The Pedagogy Engine), the abstract plugin interface (`DSPPluginBase`), and the developer toolkit for creating new domain extensions.

---

## 3. Definitions
- **Code Forge**: Software engineering domain pipeline featuring Scientific Summit arbitration (Propose -> Test -> Arbitrate).
- **Academic Press**: Publication domain pipeline automating peer-review simulations and LaTeX/Pandoc manuscript compilation.
- **Pedagogy Engine**: Educational domain pipeline handling LMS (Canvas/Blackboard) synchronization and FERPA-compliant exam grading.
- **DSP Creation Toolkit**: Standard scaffolding scripts, Antigravity skills, and FastMCP tools for generating new domain plugins.

---

## 4. Functional Requirements

- **SRS-412-07-FR-001**: The system shall decouple monolithic workflows into three dedicated DSPs: The Code Forge, The Academic Press, and The Pedagogy Engine.
- **SRS-412-07-FR-002**: The Code Forge DSP shall execute software development tasks governed by Scientific Summit peer-arbitration protocols.
- **SRS-412-07-FR-003**: The Academic Press DSP shall manage scientific manuscript drafting, citation verification, and LaTeX compilation.
- **SRS-412-07-FR-004**: The Pedagogy Engine DSP shall execute LMS integration, FERPA-compliant grading, and R/exams test generation.
- **SRS-412-07-FR-005**: All domain pipelines shall implement the standardized `DSPPluginBase` abstract class with lifecycle hooks.
- **SRS-412-07-FR-006**: The DSP Creation Toolkit shall provide automated templates to scaffold new DSP plugins, skills, and MCP configurations.
- **SRS-412-07-FR-007**: Each DSP shall operate under dedicated CPU and memory quota allocations managed by `hardware_guard.py`.
- **SRS-412-07-FR-008**: Cross-domain communication shall occur asynchronously via structured blackboard event tuples in `job_board.db`.

---

## 5. Non-Functional Requirements
- **NFR-DSP-01**: New DSP plugins scaffolded via the toolkit shall compile and pass validation in less than 5 seconds.
- **NFR-DSP-02**: Domain-specific pipeline execution shall maintain zero side-effects across domain workspaces.
- **NFR-DSP-03**: Academic Press LaTeX compilation shall generate PDF outputs matching publication style standards (ACS/Nature).

---

## 6. Interfaces & Plugin Base Implementation

```python
from abc import ABC, abstractmethod
from typing import Any

class DSPPluginBase(ABC):
    def __init__(self, domain_name: str, memory_cap_mb: int = 4096) -> None:
        self.domain_name = domain_name
        self.memory_cap_mb = memory_cap_mb
        
    @abstractmethod
    def validate_task_payload(self, payload: dict[str, Any]) -> bool:
        """Validates domain payload."""
        ...
        
    @abstractmethod
    def execute_workflow_stage(self, task_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """Executes workflow stage."""
        ...
        
    @abstractmethod
    def run_domain_audit(self, artifact_path: str) -> bool:
        """Runs domain audit."""
        ...
```

---

## 7. Data Models
```json
{
  "dsp_registration_manifest": {
    "dsp_id": "DSP-CODE-FORGE",
    "domain": "software_engineering",
    "version": "4.1.2",
    "supported_job_types": ["micro_code", "refactor", "test_authoring"],
    "resource_caps": {
      "memory_mb": 4096,
      "max_workers": 6
    },
    "mcp_server": "cochem-codeforge-mcp"
  }
}
```

---

## 8. Failure Modes & Recovery
- **Domain Sandbox Fault**: Isolated to the specific DSP worker container; peer DSPs continue execution uninterrupted.
- **LMS API Rate Limit**: Pedagogy Engine applies jittered exponential backoff and pauses queue consumption without crashing.

---

## 9. Test Obligations & Verification
- Unit test verifies `DSPPluginBase` subclasses implement required abstract methods.
- Integration test verifies independent queue polling across Code Forge and Academic Press.
- Test verifies `hardware_guard.py` enforces domain memory limits.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-07-FR-001 | V4.1.2 Master Architecture Plan §8 | `test_f05_srs_covers_all_core_domains_vm_warden_db_tdd` |
| SRS-412-07-FR-005 | DSP Creation Toolkit Mandate | `test_scenario_1_full_pipeline_bootstrap_lifecycle` |
