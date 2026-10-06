# Chapter 5: Research-Driven TDD Pivot & Zero-Mock Canaries (ch05)
**10-Cycle State Machine, 3-Strike Literature Research, and Physics Verification**

- **Document ID**: SRS-412-05
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 2: Quarantine VM Sandbox](ch02_quarantine_vm.md) | [Chapter 4: Task Matrix Blackboard](ch04_task_matrix_blackboard.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Research-Driven TDD Pivot State Machine**, governing the iterative test-driven development loop, expected failure handling, and automated literature research.

---

## 2. Boundary
The TDD Pivot boundary encompasses test execution runners (`pytest`), mutation testing (`mutmut`), diagnostic context collectors (Gemini Flash), external literature search APIs, and zero-mock physics canary calculations.

---

## 3. Definitions
- **10-Cycle State Machine**: A bounded loop (Execute -> Audit -> Repair) expecting failure as part of the normal development process.
- **Research Dossier**: Structured findings compiled from Consensus, arXiv, EuropePMC, PubChem, and ChEMBL after 3 consecutive failures.
- **Physics Canary**: Authentic computational chemistry benchmarks calculated via PySCF, XTB, and ASE `EMT()` without synthetic arrays (`np.zeros`).

---

## 4. Functional Requirements

- **SRS-412-05-FR-001**: Coding tasks shall execute within a bounded 10-cycle state machine following the Execute -> Audit -> Repair loop.
- **SRS-412-05-FR-002**: Failed test cycles shall receive automated, real-time diagnostic error analyses synthesized by Gemini Flash 3.8.
- **SRS-412-05-FR-003**: Three consecutive test failures shall freeze code generation and trigger the Mandatory Literature Research Stage.
- **SRS-412-05-FR-004**: The research stage shall query scientific databases (Consensus, arXiv, EuropePMC, PubChem, ChEMBL) to assemble an actionable Research Dossier.
- **SRS-412-05-FR-005**: Claude Fable 5.1 shall conduct Root Cause Triage across Scope Underestimation, Contract Ambiguity, and Library Deficiencies.
- **SRS-412-05-FR-006**: If 3 methodological pivots fail to resolve the defect (`MAX_META_PIVOT = 3`), the system shall trigger `[HARD_ABORT: PHYSICS WALL]`.
- **SRS-412-05-FR-007**: Physical chemistry routines shall be verified against zero-mock canaries (PySCF RHF H2, XTB GFN2 H2O, ASE EMT) and dynamic `mendeleev` masses.
- **SRS-412-05-FR-008**: Critical numerical routines shall achieve a mutation testing kill score of at least 85% via `mutmut`.

---

## 5. Non-Functional Requirements
- **NFR-TDD-01**: TDD cycle execution (test run and result collection) shall complete within 30 seconds per cycle.
- **NFR-TDD-02**: Diagnostic error prompts injected into repair agents shall remain bounded below 2,000 tokens.
- **NFR-TDD-03**: Zero mock libraries (`unittest.mock`, `MagicMock`, `monkeypatch`) shall be present in any production test code.

---

## 6. Interfaces & Authentic Canary Calculation

```python
import math
from ase import Atoms
from ase.calculators.emt import EMT
import mendeleev

def evaluate_zero_mock_canary() -> bool:
    # Quantum Chemistry & Molecular Mechanics: Calculate CO molecule potential energy
    c_mass = float(mendeleev.element("C").mass)
    o_mass = float(mendeleev.element("O").mass)
    
    # Assert physical mass bounds
    if not (12.0 < c_mass < 12.02) or not (15.99 < o_mass < 16.01):
        return False
        
    mol = Atoms("CO", positions=[(0.0, 0.0, 0.0), (0.0, 0.0, 1.13)])
    mol.calc = EMT()
    e_pot = float(mol.get_potential_energy())
    
    # Energy must be a valid finite float and non-zero (real physics computation: ΔG°, Ψ(r))
    return not math.isnan(e_pot) and not math.isinf(e_pot) and e_pot != 0.0
```

---

## 7. Data Models
```json
{
  "tdd_cycle_state": {
    "cycle_number": 3,
    "max_cycles": 10,
    "consecutive_failures": 3,
    "research_stage_triggered": true,
    "dossier_path": ".docs/research/DOSSIER_TASK_04.md",
    "meta_pivot_count": 1,
    "max_meta_pivots": 3
  }
}
```

---

## 8. Failure Modes & Recovery
- **Exhausted Research Stage**: If the research stage fails to find alternative literature, Fable 5.1 flags contract ambiguity for human user review.
- **Physics Wall Hard Abort**: When `MAX_META_PIVOT = 3` is reached, the system halts with `[HARD_ABORT: PHYSICS WALL]` and generates `Physics_Autopsy_Report.md`.

---

## 9. Test Obligations & Verification
- Test asserts `zero_mock_checker` flags obfuscated tokens and `monkeypatch`.
- Test asserts audit flags `raise NotImplementedError` and empty `pass` blocks.
- Test verifies real ASE EMT simulation executes and produces non-NaN energy.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-05-FR-001 | V4.1.2 Master Architecture Plan §7 | `test_scenario_2_poison_pill_quarantine_and_10_cycle_pivot` |
| SRS-412-05-FR-003 | Anti-Spoofing Rule 4 | `test_scenario_2_poison_pill_quarantine_and_10_cycle_pivot` |
| SRS-412-05-FR-007 | Anti-Spoofing Rule 8 & 14 | `test_f07_audit_verifies_zero_forbidden_tokens_base64_monkeypatch` |
