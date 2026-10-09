# Windows Job Object affinity ordering repair

The first actual SYSTEM worker-denial task stopped before worker release with
`ResourcePolicyError` at `worker_launch`. Its receipt remains unchanged at
`C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slot1\worker-denial-acceptance.json`
(SHA-256 `691c70560436f948464c529b9d2d6a3e33ce240749642af86ca79c748791461d`).
The device owner/DACL check passed. This is not a successful worker denial test.

The installed launcher calls `apply_job_limits` before `CreateProcessAsUserW`.
An ordinary-user reproduction using anonymous empty Jobs confirmed that CPU-set
discovery works: 24 logical processors, two efficiency classes, group 0 E-core
mask `0xff0000` (logical processors 16–23). The old call sequence sets processor
group affinity (class 14), then aggregate limits including basic affinity
(class 9). That class-9 call fails with Windows error 87 on this workstation.

The repair applies aggregate limits (class 9), CPU-rate limits (class 15), then
group affinity (class 14). It reads back all three records after the final set,
requires exact returned record lengths, and checks all memory, process-count,
priority, CPU-rate and affinity limits. No limit is disabled. Dropping the basic
affinity flag was tested and rejected as a repair: it widens the observed group
mask to all 24 CPUs on this host. Native failures now retain their numeric
`winerror` and original `OSError` cause for bounded deployment receipts.

Microsoft documents the class-14 array of `GROUP_AFFINITY` records in
[SetInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-setinformationjobobject),
the output buffer/length contract in
[QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject),
and aggregate affinity and priority fields in
[JOBOBJECT_BASIC_LIMIT_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information).
The order-dependent error and mask widening above are actual workstation
observations, not a claim that every Windows release behaves identically.

## Actual Windows validation

`pipeline_tests/test_resource_limits.py` passed **49 tests, zero skipped**, using
the staged Python **3.12.13** with `COCHEM_RUN_NATIVE_RESOURCE_TESTS=1`.
The final run took 0.75 seconds. Tests include actual empty Jobs with independent
full readback, refusal of short/ambiguous group records, native error propagation,
and ordinary-user suspended processes assigned before resume. A live child
verified the required E-core affinity and BelowNormal priority. Bounded children
verified aggregate memory denial and process-count denial.

The process-count test first proves its child body executes with spare capacity
and writes a disposable sentinel; with a one-process cap, the child body does
not execute. It uses the base interpreter directly to avoid counting a virtual
environment launcher as an extra process. The initial run had 49 passes and one
failure in the prior launcher-based fixture; that initial XML is preserved.
A subsequent 50-pass intermediate run included a host-specific old-order
reproduction; that observation is now evidence rather than a permanent assertion
that future Windows versions must reject the old order.

- Final source: `src/cochem_pipeline/resource_limits.py`, SHA-256
  `de7fac91e32cef2f854bd53487037352bbc0915f95aaf987ee8a4a543317915f`.
- Final tests: `pipeline_tests/test_resource_limits.py`, SHA-256
  `a8a30e3f9aaf491025b1a020cbfe4c9be865320ec9b70a43b4816fa7f65710ff`.
- Final XML: [resource-limits-required-ecores-native-reviewed-tests.xml](evidence/windows-2026-10-06/resource-limits-required-ecores-native-reviewed-tests.xml), SHA-256
  `00dcf5b4db77050589b8ecc72acdd2699c04ed6f21e49d7c3f59d99cc4be5755`.
- Reproduction: [job-affinity-order-observation.json](evidence/windows-2026-10-06/job-affinity-order-observation.json).

No SYSTEM task, worker identity, native login/model job, protected configuration,
credential, database, installed runtime, or R: drive was changed by these tests.
The repair still needs a fresh protected runtime and new worker-denial acceptance
evidence. Existing Linux results and the failed Windows receipt remain separate.
