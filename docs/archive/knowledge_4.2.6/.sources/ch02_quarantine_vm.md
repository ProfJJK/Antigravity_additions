# Chapter 2: Guest-Side Quarantine VM & Ephemeral Docker Sandbox (ch02)
**Virtual Machine Isolation, 32GB RAM Ceiling, and Ephemeral Container Execution**

- **Document ID**: SRS-412-02
- **Cross-Reference**: [Skeleton Index](00_skeleton.md) | [Chapter 1: Host Warden](ch01_host_warden.md) | [Chapter 3: Two-Staged Concurrency Layers](ch03_concurrency_layers.md)

---

## 1. Purpose
This chapter specifies the execution environment for the **Guest-Side Quarantine VM**, providing an isolated Ubuntu 24.04 LTS Linux sandbox for agentic execution.

---

## 2. Boundary
The Quarantine VM boundary encompasses the Hyper-V virtual hardware configuration (static 32 GB RAM ceiling, 12 vCPUs at 70% cap), the internal Docker daemon with zero external network connectivity, and the ephemeral container filesystem mounts.

---

## 3. Definitions
- **Static RAM Ceiling**: Disabling Dynamic Memory in Hyper-V to fix guest memory allocation at exactly 32 GB.
- **Ephemeral Sandbox**: A Docker container launched with `--rm`, `--network none`, `--read-only`, and a 2GB tmpfs scratch volume.
> [!WARNING] (REMOVED: The 'Physics Canary' requirement was a hallucinated CoChem artifact. This is a general software orchestration pipeline. Physical chemistry concepts have been stripped.)

---

## 4. Functional Requirements

- **SRS-412-02-FR-001**: The Quarantine VM shall enforce a static 32 GB RAM ceiling with Hyper-V Dynamic Memory permanently disabled.
- **SRS-412-02-FR-002**: The VM hypervisor configuration shall allocate 12 virtual CPUs with a hard 70% CPU resource allocation cap.
- **SRS-412-02-FR-003**: The execution plane shall spawn all agent coding tasks inside ephemeral Docker containers with `--network none`, `--read-only`, and `--tmpfs /tmp:rw,size=2g`.
- **SRS-412-02-FR-004**: Student pedagogy tasks shall execute in isolated, air-gapped FERPA containers with scrubbed student identifiers.
> [!WARNING] (REMOVED: SRS-412-02-FR-005 hallucinated PySCF and ASE EMT requirements. Stripped to enforce general software standards.)
> [!WARNING] (REMOVED: SRS-412-02-FR-006 hallucinated mendeleev library usage. Stripped to enforce general software standards.)
- **SRS-412-02-FR-007**: The container scheduler shall enforce a concurrency limit of at most 6 parallel 4 GB sandboxes inside the 32 GB VM.
- **SRS-412-02-FR-008**: The container engine shall purge all scratch volumes, tmpfs allocations, and intermediate layers immediately upon container exit.

---

## 5. Non-Functional Requirements
- **NFR-VM-01**: Container startup latency from execution request to test execution shall not exceed 1.5 seconds.
- **NFR-VM-02**: Out-of-memory container terminations shall be isolated to the offending container without crashing the VM kernel.
- **NFR-VM-03**: Network isolation shall be verifiable via physical connection drop (`Network is unreachable`).

---

## 6. Interfaces
> [!WARNING] (REMOVED: Physics interfaces stripped)

## 7. Data Models
```json
{
  "sandbox_spec": {
    "image": "general-sandbox:latest",
    "network": "none",
    "memory_mb": 4096,
    "cpus": 2,
    "pids_limit": 512,
    "tmpfs_size": "2g",
    "security_opt": ["no-new-privileges:true"],
    "cap_drop": ["ALL"]
  }
}
```

---

## 8. Failure Modes & Recovery
- **Container OOM Crash**: The container scheduler records an exit code of 137, captures memory slope, and quarantines the task to prevent cascading VM exhaustion.
- **Scratch Leak**: Automated cron job inspects `/tmp` mounts every 60 seconds and unlinks unmounted orphan namespaces.

---

## 9. Test Obligations & Verification
- Test verifies container execution flags include `--network none` and `--read-only`.

---

## 10. Traceability
| Requirement ID | Architectural Source | Test Verification |
|---|---|---|
| SRS-412-02-FR-001 | V4.1.2 Master Architecture Plan §1.3 | `test_f05_srs_covers_all_core_domains_vm_warden_db_tdd` |
