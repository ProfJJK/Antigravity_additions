# Queue launch measurement — 4.2.7

The owner accepts the previously measured **29.347 ms** queue acquisition result
as an acceptable provisional result. Queue acceptance remains conditional upon
measurement on the deployed Windows machine. **5 ms remains an optimization
target.** Missing it in a Linux stress test does not authorize a queue redesign.
Review any redesign only after measuring the actual Windows launch topology.

The production topology is one Windows SYSTEM Warden owning one pooled
`JobStore`, with at most four active native or Docker executions. Low-privilege
CLI workers do **not** open the private database. Four independent processes
writing SQLite, as used in the older ten-process contention test, do not mirror
that ownership topology.

## Actual Windows launch observation

Add these optional arguments to the **existing** Warden daemon service action
for a planned launch, retaining its installed interpreter, SYSTEM identity,
service lock and deployed configuration:

```text
cochem-pipeline daemon --config <deployed-config.json> --queue-launch-output <private_root>\queue-launch-427
```

The output directory must be new, inside the configuration's `private_root`,
with an existing protected parent. Do not start a second Warden or grant worker
accounts access to the private directory. The ordinary single-service lock
still applies. Omitting the option removes all observation overhead.

Submit representative work through the normal authenticated GUI/MCP or CLI.
Use the jobs you expect to run after launch: several simultaneous planning
requests, real coding tasks in registered repositories, normal payload/context
sizes, and realistic bursts/backlogs. Subscription CLIs run normally and consume
their ordinary subscription allowance. The observer itself submits no jobs,
induces no quota failures and makes no direct provider API requests.

`queue-launch.json` is refreshed every ten seconds and on shutdown. It captures:

- The actual live database path, starting database bytes, native volume serial,
  filesystem, controller PID, Python/SQLite version, routing hash and a hash of
  the complete effective configuration. Secrets and raw task text are omitted.
- WAL, NORMAL (`synchronous=1`), 5,000 ms busy timeout and foreign-key settings,
  plus the configured lease and heartbeat periods.
- Full claim-call latency including lock waiting, transaction execution and
  commit: p50, p95, p99 and maximum. SQLite does not expose the busy-wait portion
  separately, so the report does not invent an isolated busy-time measurement.
- Heartbeat, completion, failure, Oracle context and read traffic. Native
  launch callbacks bind PID/create-time, executable and actual account to each
  attempt. On Windows, account SIDs must match the configured worker slot.
- Completed native attempts require a successful stored completion and a real
  `native_cli` subscription receipt matching the callback's exact Windows
  process-creation FILETIME, slot, configured executable, reserved provider,
  model, effort and output hash. Fixture receipts and nonaccepted return values
  do not count. Unknown and fixture evidence is reported explicitly.
- Naturally observed routing skips, unavailability and backoff reasons from
  the actual event cursor. The observer never exhausts subscriptions to create
  synthetic quota evidence.

To complete the automated coverage check, collect at least **64 acquisitions**,
**16 completed native attempts**, two job kinds, heartbeat/read/context traffic,
and four simultaneously live native processes with completed work in at least
four configured slots. The requirements remain pending if host pressure or
provider availability prevents this load. The operator must also confirm that
the workload represents intended use; counts alone cannot establish this.

All four overlapping processes must later supply those matching native
completion receipts. Simultaneous authentication/capability probes followed by
sequential inference do not satisfy this check. Any observed executable or
worker-identity mismatch remains a failure for that observation window.

The report bounds each operation's latency window to its latest 4,096 calls and
retains all-call maxima. Routine reads can roll that window without invalidating
the run. Acquisition identities are retained up to 4,096; exceeding that budget
requires a fresh observation window. Duplicate attempt identities, nonmonotonic
fences, mismatched accounts, observation errors or native concurrency above four
prevent acceptance. This is passive evidence; it does not replace the existing
adversarial fencing and stale-write tests.

Record-bookkeeping time is reported separately from the timed store calls.
Wrappers, native callbacks and periodic report serialization still consume CPU
and memory and can affect scheduling. The observer does not claim zero overhead
or separately measured RSS overhead; retain normal controller resource evidence
when interpreting the run.

`launch_measurement_complete=true` means the native coverage checks completed.
If every acquisition is below 5 ms, status becomes
`accepted_windows_launch_target_met`. Otherwise it becomes
`conditionally_accepted_launch_measured_optimization_review`, and
`redesign_review_required=true` records a review trigger. That flag does not
redesign the queue, reject user jobs, or reverse the owner's provisional latency
acceptance. Compare tail latency, normal user-visible delays, workload and host
conditions before deciding whether a redesign is useful.

## Optional storage rehearsal without live jobs

The separate profile reads the queue fields from the deployed configuration and
creates a disposable sibling SQLite file in the same directory/storage as
`job_board.db`. It never opens or mutates the active production database:

```text
python -m cochem_pipeline.performance_acceptance --queue-launch-config <deployed-config.json> --queue-workflows 12 --output <private_root>\queue-profile-427
```

On Windows run this diagnostic as SYSTEM, with the same protected-directory
requirements. It uses the configured routing policy, lease and heartbeat;
one controller owns SQLite while four spawned Python processes return
**explicitly synthetic** outputs. The default mix is 12 workflows, three
chapters per workflow, 60 completed jobs, 1/8/64 KiB artifacts, 4 KiB contexts,
and 10 ms/50 ms/5.2 s simulated execution delays. Long jobs exercise real
heartbeats. Submission/scatter, claims, context writes, completion, synthesis
hashes and reads use the actual store implementation.

The profile verifies four-seat occupancy/ceiling, no duplicate authority,
completed workflow/output joins, and rejection of wrong-fence heartbeat and
completion attempts. Its timing report contains p50/p95/p99/max per operation
and the declared workload. Temporary sibling database, WAL and SHM files are
removed; `queue-profile.json` retains the evidence.

This is a storage rehearsal, even on Windows. It does not reproduce the active
database's size, live WAL contention, low-privilege account behavior or native
model execution. It always reports `native_windows_acceptance=false` and
`pending_actual_windows_launch`. A successful exit means the physical workload
and correctness checks passed; it is not a 5 ms certificate.

## Current implementation and remaining acceptance

Both harnesses are implemented and covered by physical SQLite/process tests.
The shared writer, routing order, WAL/NORMAL durability and four-seat admission
algorithm are unchanged. The actual Windows launch measurement has not run in
the Linux cloud environment; queue acceptance remains conditional until the
deployed-host evidence and representative workload review are complete.
