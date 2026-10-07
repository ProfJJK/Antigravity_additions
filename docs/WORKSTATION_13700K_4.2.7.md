# Windows workstation capacity guidance for 4.2.7

This guidance addresses the owner's Intel Core i7-13700K, 64 GB DDR5-6400 RAM, 2 TB NVMe and RTX 3090 Windows 11/WSL2 workstation. “64 GM” is interpreted as 64 GB of installed RAM. Memory calculations below use GiB (1,024 MiB). They are calculations from the current admission policy, **not measurements or benchmark results from this workstation**.

## Recommended progression

Keep the repository's global default at **four shared execution slots**. Complete the specified Windows four-worker queue benchmark at its original topology and actual database location. Changing that acceptance benchmark to eight workers would not close its requirement.

After installation preflight and the known audit defects are resolved, provision enough isolated identities and evaluate an **eight-slot workstation configuration**, increasing through six slots while measuring a representative mix of planning, coding, review and testing. Eight is a useful initial ceiling for overlapping network-bound native CLI work on this machine; it is neither a guaranteed simultaneous agent count nor a demonstrated optimum.

**Twelve slots is a conditional upper configuration to evaluate, not a throughput promise.** Its default reservation needs at least 49 GiB of *currently available* physical memory and 54 GiB of available Windows commit. An 8 GiB RAM disk plus Windows, applications, WSL2 and active jobs will commonly make that impossible. Ten slots already needs 41 GiB of current free physical memory. Retain eight unless measurements justify increasing it. **Do not configure sixteen expecting sixteen simultaneous jobs under the current default memory policy: it requires 65 GiB of available physical memory, exceeding a 64 GiB machine before any other use.**

Changing `max_execution_slots` alone is insufficient. Its value cannot exceed the configured isolated worker identities; the current example has six identities and four slots. Provision and authenticate additional identities through the reviewed installation path, then update the protected configuration. The current normal-worker login shortcomings are recorded in [the 4.2.7-r2 audit](AUDIT_4.2.7-r2.md); an increased ceiling does not fix those deployment defects.

## What the current guard actually permits

The effective reservation is the largest of the configured hardware per-agent memory budget, the native Job Object memory limit and, when Docker is enabled, the container memory limit. With current defaults:

```text
native memory limit = 2,048 MiB per process tree
Docker memory limit = 4,096 MiB per container
effective reservation = 4,096 MiB per shared execution seat

physical-memory seats = floor((current available MiB - 1,024) / 4,096)
commit seats          = floor((current free commit MiB - 6,144) / 4,096)

raw capacity <= min(configured slots,
                    configured isolated identities,
                    physical CPU count,
                    CPU/RAM admission band,
                    physical-memory seats,
                    commit seats)
```

Other required telemetry, thermal, GPU, disk and controller checks can reduce capacity further or pause admission; recovery hysteresis ramps it back gradually. These expressions describe the healthy/default case, not the complete decision function. See [hardware_guard.py](../src/cochem_pipeline/hardware_guard.py), [runtime.py](../src/cochem_pipeline/runtime.py), [config.py](../src/cochem_pipeline/config.py) and [resource_limits.py](../src/cochem_pipeline/resource_limits.py).

| Shared seats | Minimum current available physical memory | Minimum current free commit |
| --- | ---: | ---: |
| 4 | 17 GiB | 22 GiB |
| 6 | 25 GiB | 30 GiB |
| 8 | 33 GiB | 38 GiB |
| 10 | 41 GiB | 46 GiB |
| 12 | 49 GiB | 54 GiB |
| 16 | 65 GiB | 70 GiB |

These thresholds use **free memory at the time of the sample**, not installed memory. Active jobs, WSL2 and an allocated RAM disk already affect the measurement. Do not subtract the RAM disk again. As running jobs grow, the guard can lower the admission ceiling without an operator changing the configured maximum. Adequate pagefile/commit capacity does not substitute for the independent physical-memory requirement.

For an eight-slot ceiling, the healthy CPU/RAM bands are 8, 6, 4 and 2 seats at CPU utilization below 30%, 50%, 70% and 85%, respectively, provided their corresponding available-memory bands also hold. At 85% or higher, new admission pauses under this policy. Per-seat memory and other gates still apply inside each band.

The i7-13700K has 16 physical cores and 24 logical processors. The guard's physical-core ceiling is therefore at most 16 when native topology is correctly reported. However, the native worker and Warden policy confines their work to **verified efficiency cores**, so those processes share the eight E cores. Sixteen physical cores does not mean sixteen dedicated cores for native CLI work. The default 20% CPU Job Object limit is an upper bound for each process tree; it is not reserved CPU capacity and does not multiply available processor time.

## Benefits, costs and risks

| Effect | Practical implication |
| --- | --- |
| More overlap during remote inference | Independent chapters, reviews and other eligible jobs spend less time waiting for a local seat while another CLI waits on the provider. Benefit depends on workload independence and provider response times. |
| More useful work during provider spillover | Eligible jobs for another provider can proceed when seats are available. Extra slots do not increase subscription quotas, remove provider holds or change Chapter 06 routing. |
| Higher local resource contention | More CLI trees, Git operations, parsing, test runs and container maintenance compete for E-core time, RAM, commit, disk I/O, handles and the shared RAM volume. Throughput can rise while individual job latency worsens. |
| Faster quota consumption | More simultaneous requests can exhaust a subscription window sooner. Fallbacks may also share a provider quota pool. Extra concurrency is not extra entitlement or a way around a service limit. |
| More concurrent changes and failures to reconcile | Independent identities protect execution, but project dependencies and final integration still constrain useful parallelism. More slots do not accelerate a sequential dependency chain. |
| More thermal and storage pressure | Sustained builds can trigger the governor before the configured seat limit. The current example's disk thresholds are 120 MiB/s and 4,000 IOPS; a fast NVMe does not bypass those configured limits. |

The RTX 3090 does not accelerate GPT, Claude or Gemini inference performed remotely through their subscription CLIs. It matters for generated CUDA workloads and other local GPU applications. Current VRAM pressure policy can throttle admission above 80% usage and pause it at 90%; a concurrent GPU workload can therefore reduce available pipeline capacity even when native CLIs are waiting on the network. Disk capacity and RAM transfer rate alone do not predict safe concurrency.

Use the actual sensor readings and configured thermal limits. Verify workstation stability, firmware/BIOS maintenance and the selected DDR5-6400 memory profile before attributing crashes under sustained load to pipeline scheduling. This document makes no claim that the installed BIOS, memory profile or cooling has been validated.

## Docker, WSL2 and the existing R: drive

The slot budget is shared by native execution and prepared/active containers. It is not “eight model agents plus an unlimited test pool.” Prepared containers count even before a test uses them; native demand can cause idle members to be drained. Keep the Docker ceiling at the existing **four containers** initially. A prepared target of two to four is a tuning choice to measure against test demand, not a reason to raise both limits automatically.

Inspect effective WSL2 and Docker CPU/memory limits before increasing capacity. Host free-memory telemetry and individual container caps do not prove that the guest has enough room for every concurrent test container. For example, a 32 GiB guest limit leaves no guest/engine headroom for eight containers each permitted 4 GiB. Four such container caps permit 16 GiB in aggregate before guest overhead. The current production preflight does not attest a WSL2-wide memory/CPU budget; record and check that deployment prerequisite separately until it is implemented.

Retain the owner's existing **8 GiB R: ImDisk volume** and startup task. Increasing slots does not enlarge it, and this recommendation does not introduce recurring administrator setup. All workers share its capacity; caches and temporary working files can exhaust it before host RAM runs low. Preserve the configured 512 MiB volume reserve and measure peak occupancy. Do not claim live automatic resizing is implemented.

The [audit](AUDIT_4.2.7-r2.md) also records that cold prepared-pool replenishment holds the shared admission lock and can delay ready work. Resolve or account for that known defect before interpreting a scaling result as an intrinsic hardware limit. The pool restart attestation defect and the other deployment/acceptance gaps remain separate from slot tuning.

## Measurements required before keeping a higher ceiling

1. Preserve the original four-worker Windows queue result. Use the actual job-board database location and realistic workload; collect queue wait and acquisition latency separately from CLI/provider latency.
2. Verify the configured number of isolated identities, each required native subscription login, exact executable/model/effort acceptance, RAM mount, Docker authority and required sensors. Retain Claude's provider-wide 20-agent ceiling; Codex and Agy have no default provider-specific ceiling.
3. Repeat the same representative workload at four, six and eight configured seats. Record achieved concurrency and the guard's limiting reason, not just the requested ceiling. Include native calls, test-container handoffs, cancellation, retries and independent asymmetric reviews.
4. Track useful completed workflows per hour alongside median/tail job latency, provider quota/backlog holds, retry and failure rates, UI responsiveness, CPU/E-core load and temperature, free physical memory, free commit, RAM-disk occupancy, disk pressure and process/handle growth.
5. Inspect effective WSL2 memory/CPU allocation and engine overhead while tests run. Keep native and Docker concurrency counts separate in the measurements while respecting their shared ceiling.
6. Retain eight only if the workload completes more useful work without unacceptable contention or new failures. Evaluate ten or twelve only when their measured memory/commit headroom exists. Keep the canonical 48-hour stability acceptance requirement; a short tuning run does not replace it.

No live Windows workload, subscription benchmark or higher-slot deployment was performed to produce this recommendation. The global four-slot default, resource limits and canonical four-worker queue benchmark remain unchanged by this document.
