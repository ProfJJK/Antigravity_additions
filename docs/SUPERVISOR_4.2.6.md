# 4.2.6 supervisor recovery and independent acceptance

The detector runs in a separate protected Python process with `-I -S`; site,
`.pth` and virtualenv startup hooks do not execute. It imports only the
standard library, `psutil` and supervisor modules with the same dependency
boundary through exact independently installed package paths, verified by
native ACL checks at supervisor startup. It opens the pipeline database
read-only. The recovery actuator is
a different process; it retains the protected Windows account, task-control
and release-management implementation. Actuator cleanup reconciliation can
write verified containment records to the pipeline database. This distinction
does not claim that the actuator itself satisfies the old read-only rule.

A missing heartbeat first receives the bounded component restart. A failed
restart can proceed to native Astra/Fable repair only when repeated classified
code/protocol diagnostics or a recent structured code crash establish a cause.
The actuator must stop and verify the complete contained process tree, then
recheck cleanup and environmental holds before reserving inference. Heartbeat
absence alone, authentication, quota, provider, resource and unknown failures
do not authorize code repair. Neither escalation nor deployment resets any
pipeline task budget or resumes an exhausted workflow.

The model-attempt ledger and the separate `component-recovery.db` are both
preserved during upgrades, including committed WAL records and interrupted
reservations. Restarting or upgrading does not refund a Docker recovery or
repair attempt. The default two-per-incident/four-per-day repair policy and
cooldown remain unchanged.

Process observations record native PID **and creation time**, RSS and Windows
handle counts (POSIX file descriptors in portable tests). At most 64 current
processes and eight samples per identity are inspected. A sustained monotonic
increase over at least 30 seconds must exceed both a total and a slope
threshold: 128 MiB and 1 MiB/second for memory; 128 handles and one/second for
handles. These observations produce resource holds, not paid code repair.
Recent exited-process samples are retained for ten minutes as crash/OOM
evidence. Process existence alone does not establish workflow progress.

An expired execution lease receives the full additional 60-second reaper grace.
Before recovery, the supervisor writes a private evidence bundle containing a
transactionally consistent SQLite backup (at most 32 MiB, one-second copy
deadline), a separate bounded **SQLite metadata projection**, process metrics,
validated PEP 657 frames and file hashes. The raw backup includes private
workflow data and stays in SYSTEM-private storage; it is never sent to repair
inference. The safe projection excludes prompts, artifacts, credentials,
exception values, source lines and locals. Oversized or unreadable databases
produce an explicit unavailable-backup result. A corrupt database retains at
most 4 KiB of private header bytes; other diagnostic evidence remains available.
The bounded structured crash file can
diagnose startup code failures even before the pipeline creates its database.

Signal ZD-8 checks physical inactive lease owners and pending jobs that exhausted
their ordinary failure budgets. Availability dispatches do not consume that
budget. Violations collapse and freeze the queue, with diagnostics captured
before verified process containment. Only persistent structural evidence without
environmental blockers proceeds to bounded automatic code repair. The owner
approved amending archived FR009 on 2026-10-06: ordinary failed/cancelled
workflows stay isolated and do not freeze unrelated work. The amended source
clause records this decision explicitly.

`SUPERVISOR_OBSERVATION_TIMED` and `RECOVERY_ACTION_STARTED` ledger events retain
actual observation duration and confirmation-to-action timing. Native target
host measurements are required for the one-second/five-second SRS targets;
portable tests do not certify these performance limits.

## Three deployment gates

1. Protected pytest files run against the frozen candidate as regression
   diagnostics. Candidate code shares this interpreter and could forge its
   JUnit output; a zero exit and consistent XML do **not** attest independent
   execution of every assertion.
2. A protected outer verifier imports no candidate code. It owns randomized
   requests, assertions and the private verdict. A separate unprivileged child
   executes candidate APIs in the bounded repair Job Object. After actual child
   cleanup, the verifier independently reads physical SQLite state and scratch
   files. Checks cover stale fencing, receipt binding, chapter ownership,
   accepted artifact bytes/hashes, synthesis ordering, coding baseline bytes,
   reserved coding model receipts, asymmetric planning review and stage order.
   Child `PASS` text has no authority. SQLite readers impose length/VM limits
   and reject views/virtual tables before reading candidate-controlled data.
3. Automatic deployment still requires the existing authenticated live workflow
   smoke and health progression; failure restores the previous release using
   the durable release journal.

Automatic repair remains enabled by the reviewed policy. An outer gate failure
rejects the candidate under the existing attempt budget. These finite external
checks establish only the recorded behavior for those challenges; they do not
prove all possible behavior, reconstruct in-process unit-test execution, or
establish real provider execution. Their storage-contract receipt inputs are
explicitly labeled fixtures and are never promoted to live provider evidence.
Actual Windows ACLs, process-tree cleanup, subscription logins and live
deployment/rollback still require native acceptance on the target host.
