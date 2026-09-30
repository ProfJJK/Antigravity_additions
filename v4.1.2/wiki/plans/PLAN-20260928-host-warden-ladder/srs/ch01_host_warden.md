# Chapter 1: Windows 11 Host Warden Daemon & E-Core Supervision (ch01)
**Host-Side SRE Supervisory Architecture and Hyper-V Control Plane**

- **Document ID**: SRS-412-01
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 2: Quarantine VM Sandbox](ch02_quarantine_vm.md)

---

## 1. Purpose
This chapter specifies the software requirements for the **Host Warden Daemon** (`host_warden.py` / `host_warden.ps1`), the primary host-side supervisory sentinel running natively on Windows 11 Pro.

---

## 2. Boundary
The Host Warden operates in user space on the Windows 11 host. Its operational boundary encompasses host process affinity management, Windows job object controls, Hyper-V management cmdlets (`Start-VM`, `Stop-VM`, `Restore-VMSnapshot`), and named-pipe telemetry ingestion.

---

## 3. Definitions
- **E-Core Pinning**: Restricting process affinity to Intel Efficient Cores via CPU affinity mask `0x00FF0000`.
- **CREATE_NO_WINDOW**: Windows subprocess creation flag `0x08000000` preventing hidden console window allocation.
- **Resuscitation**: Automated forced restart and checkpoint rollback of a frozen guest execution plane.

---

## 4. Functional Requirements

- **SRS-412-01-FR-001**: The Host Warden shall pin its process execution strictly to Intel Efficient Cores via CPU affinity mask `0x00FF0000` and configure priority class to `BELOW_NORMAL_PRIORITY_CLASS`.
- **SRS-412-01-FR-002**: The Host Warden shall execute all host-side subprocesses with `creationflags = 0x08000000` (`CREATE_NO_WINDOW`) to prevent console handle allocation and desktop heap exhaustion.
- **SRS-412-01-FR-003**: The Host Warden shall poll guest VM liveness over the dedicated named pipe `\\.\pipe\cochem_warden_vm` or HTTP `/healthz` at 15-second intervals.
- **SRS-412-01-FR-004**: The Host Warden shall execute automated Hyper-V resuscitation (`Stop-VM -TurnOff` followed by `Start-VM`) upon detecting 3 consecutive liveness poll timeouts (>45 seconds).
- **SRS-412-01-FR-005**: The Host Warden shall maintain rolling Hyper-V production checkpoints taken at quiescent milestone boundaries and restore them via `Restore-VMSnapshot` if filesystem corruption occurs.
- **SRS-412-01-FR-006**: The Host Warden shall ingest PEP 657 structured crash envelopes streamed over the telemetry channel.
- **SRS-412-01-FR-007**: The Host Warden shall dynamically resolve atomic masses via `mendeleev` when validating chemistry pipeline environments.
- **SRS-412-01-FR-008**: The Host Warden shall register as an automatic Windows startup service with restart resilience.

---

## 5. Non-Functional Requirements
- **NFR-HW-01**: Host Warden steady-state RAM consumption shall not exceed 50 MB.
- **NFR-HW-02**: CPU utilization on E-Cores during polling loops shall remain below 5%.
- **NFR-HW-03**: Emergency VM power-cycle resuscitation sequence shall initiate within 3 seconds of the 45-second timeout confirmation.

---

## 6. Interfaces & Authentic Code Specification

```python
import os
import subprocess
import win32api
import win32con
import win32process
import mendeleev

CREATE_NO_WINDOW: int = 0x08000000
E_CORE_MASK: int = 0x00FF0000

def initialize_host_warden() -> dict[str, float]:
    """Configures host process affinity, priority class, and verifies physical constants."""
    handle = win32api.GetCurrentProcess()
    win32process.SetPriorityClass(handle, win32process.BELOW_NORMAL_PRIORITY_CLASS)
    win32process.SetProcessAffinityMask(handle, E_CORE_MASK)
    
    # Authentic Mendeleev mass resolution for chemical safety checks
    c_mass = float(mendeleev.element("C").mass)
    o_mass = float(mendeleev.element("O").mass)
    return {"C_mass": c_mass, "O_mass": o_mass}
```

---

## 7. Data Models
```json
{
  "telemetry_packet": {
    "timestamp": 1727574000,
    "host_e_core_load_pct": 2.4,
    "guest_vm_status": "HEALTHY",
    "pipe_latency_ms": 1.2,
    "active_checkpoints": ["M1_PURGE_SNAP", "M2_SRS_SNAP"]
  }
}
```

---

## 8. Failure Modes & Recovery
- **Pipe Timeout**: If the guest named pipe fails to respond, retry twice at 15s intervals. If unanswered, trigger VM resuscitation.
- **Hyper-V API Deadlock**: If `Stop-VM` hangs, terminate `vmwp.exe` worker process via PID and restart Hyper-V VMMS service.

---

## 9. Test Obligations & Verification
- Unit test verifies `CREATE_NO_WINDOW == 0x08000000`.
- Integration test verifies CPU affinity mask and non-zero `mendeleev` masses.
- Simulation test triggers 45s simulated timeout and asserts resuscitation trigger.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-01-FR-001 | V4.1.2 Master Architecture Plan §1.3 | `test_f04_fable_invocation_enforces_create_no_window_flag` |
| SRS-412-01-FR-007 | Mendeleev Library Mandate | `test_f07_audit_rejects_unanchored_atomic_mass_constants` |
