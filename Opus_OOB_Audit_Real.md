## Verdict: I agree with Astra. The fail-open is not closed.

I checked Astra's main claims against the live file, `src/cochem/dsp/worker_daemon.py`. Its code matches what Astra reviewed. I did not run anything; this is a code reading only.

**The patch does one thing:** if a job names a `target_file` and that file is missing or unchanged afterwards, the job is rejected. It does not prove that this job produced valid output. An empty run can still reach `COMPLETED` in several ways.

### Astra's findings, confirmed and ranked

| # | Finding | My rating | Notes |
|---|---|---|---|
| 1 | Jobs with no `target_file` skip the check entirely | **Critical** | Confirmed at `worker_daemon.py:659` and `:842`. The pipeline's own `CHAPTER_DRAFT` and `SYNTHESIS` jobs have no `target_file`. Without one, the only check left is the exact stderr string `"jetski: no output produced"`. If agy changes that wording, or prints nothing, the job still completes. |
| 5 | `complete_task()` result is ignored | **Critical** | Confirmed at `:850`. If the database update fails, the code still logs success, unblocks downstream DAG jobs (`:891-909`) and returns `"COMPLETED"`. |
| 4 | Stale RAM-disk copies pass the check | **High** | Worse than Astra said. The RAM workspace (`layer1.py:311-372`) is one directory shared by every task in a Director session, and it stays until the Director is recycled. An earlier task's copy of `work_dir/<target>` stays there. A later task that does nothing on the same target copies that stale file back, the hash differs, and the job passes. A missing RAM file still only logs a warning (`:748`). |
| 2 | Two jobs on the same file: one job's write satisfies the other | **High** | Up to 20 jobs run at once in a shared workspace, so this is realistic, not theoretical. |
| 7 | Layer 2 doesn't check that the path stays inside the repo | **High** | `REPO_ROOT / "C:\\..."` resolves to that absolute path. Any outside file that changes on its own (a log, for example) satisfies the check. |
| 6 | "File changed" is not the same as "task done" | **High** | A newly created empty file passes. A correct run that writes identical bytes fails. |
| 3 | The daemon's own files can be named as the target | **Medium** (Astra: High) | Real: the result JSON is written before the check, and `worker_<pid>.json` is rewritten every 5 s. But it needs a wrong or malicious payload, so I rate it one level lower. |

There is also the non-atomic completion and DAG update Astra flagged. If the DAG insert fails after `complete_task`, the error handler's `retry_or_block_task` does nothing, because the row is no longer `RUNNING`. The task then reports `"PENDING"` while the database says `COMPLETED` and no child jobs exist.

### Problems Astra missed

1. **A guard failure can be treated as a quota error.** The error handler searches the error message for words like `"quota"`, `"429"` or `"spend limit"`. The guard's own error message contains the target path, and the non-zero-exit error contains agy's stderr. So a target like `docs/quota_policy.md`, or agy stderr that mentions a quota, turns a guard rejection into a requeue that refunds the attempt and switches model. The job doesn't reach `COMPLETED`, but the rejection disappears into up to 3 silent retries before the hard abort. Severity: Medium.
2. **The "lease lost" check doesn't really check the lease.** The heartbeat thread stops quietly after 1740 s without raising the lost flag. A passing `beat.lost` check therefore doesn't mean the lease is still valid. Together with finding 5, an over-time attempt can still finish.
3. **The agent can stall a job forever.** If its last line of output is `{"status":"PAUSED","retry_after_sec":0}`, the job is requeued and the attempt refunded, every time, so it never reaches `BLOCKED`. This isn't a false `COMPLETED`, but it is a fail-open on liveness.

### What has to change before calling it closed

1. Every job type declares an output contract. A job without one is rejected, not passed.
2. Check `complete_task()`'s return value. Put the completion and the DAG update in one transaction, protected by the same lease check.
3. Each claim gets a unique token, and lease expiry is checked on every heartbeat and final status update.
4. Each attempt writes into its own clean output directory, both the RAM workspace and Layer 2. Publishing to the real target is serialised and checks that the destination hasn't changed since the start. A missing source file raises an error.
5. Resolve and check every path before running: it must stay inside approved artifact folders, and control and state files are excluded.
6. Accept output on content checks for its job type (format, schema, required content), not on "the hash changed".
7. Classify quota errors from the structured provider error, not by searching message text.

Astra's test list should be the closure criteria. I'd add four cases: a guard failure whose path contains "quota", a stale RAM file left by an earlier task, a run that passes 1740 s, and a forged PAUSED line in the output.