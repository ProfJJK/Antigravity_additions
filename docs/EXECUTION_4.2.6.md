# 4.2.6 upgrade and acceptance

This release closes additional code gaps found by comparing the original SRS
with actual behavior. It does **not** certify every SRS clause. The current
[requirements ledger](REQUIREMENTS_4.2.6.md) records unresolved definitions and
physical performance/Windows acceptance separately from implemented controls.

The native architecture remains Windows Python 3.12+, SYSTEM controller,
separate unprivileged subscription CLI identities, Antigravity MCP frontend,
and WSL2/Linux Docker for generated code and tests. Model IDs, routing order,
four execution seats and Gemini 3.1 Pro final synthesis are unchanged.

## Upgrade existing installations

Use fresh protected `Pipeline4.2.6` and `Supervisor4.2.6` code/venv/test
installations. Preserve worker and repair accounts, native CLI logins,
controller token, persistent job board, task identities, accepted artifacts,
fences and original state directories. Do not copy live WAL databases by
copying only their main `.db` files.

The supervisor installer defaults to migrating `CoChemSupervisor425` to
`CoChemSupervisor426`; `-PreviousDataRoot` selects another actual prior root.
Migration now includes `component-recovery.db`, so a consumed automatic Docker
recovery attempt cannot be refunded by upgrading. The previous ledger and
release pointer remain available for recovery.

Stop and disable the existing managed tasks before installation, as required
by the existing cleanup/reboot protocol. The installation scripts preserve
the task names `CoChem-4.2.2-Warden` and `CoChem-4.2.3-Supervisor` and worker
credential identities. The versioned code path is not a reason to recreate
subscription profiles. Merge the provisioned layout into the reviewed example;
its example paths are not instructions to abandon an existing job database.

The installation sequence in [the previous execution guide](EXECUTION_4.2.5.md)
still describes native dependencies, account authentication, ImDisk/AWE and
Docker provisioning. Use the new scripts/paths and the changed requirements
below. `provision-execution` and `doctor` report actual prerequisites; source
presence or a PowerShell syntax check is not successful Windows deployment.

## Required knowledge corpus

The checked-in `knowledge/` directory contains exact byte captures of the four
primary user-supplied specifications and the referenced historical chapters,
plus a linked wiki index, explicit owner decisions and their SHA256 catalog. It adds no missing planning-stage or Method Matrix definitions.
The supplied corpus was physically indexed: 18 documents, 183 sections, 20 valid
relative links, 101,886 source bytes and 241,664 index bytes (2.3719×).

The pipeline installer copies this checked-in corpus only when the reviewed
corpus root does not yet exist; it preserves any existing corpus. The default
protected location is
`C:\Program Files\CoChem\Knowledge4.2.6`, retaining `.sources`, `wiki` and
`v4.1.2_manifest.json`. Set `knowledge.enabled=true` and configure these
absolute paths plus a dedicated `state_root` below the existing protected
`private_root`. Corpus paths must be disjoint from worker, RAM, project and
credential paths. Do not use the writable project checkout as the live corpus.

The installer first creates an administrator-owned destination under protected
ancestors. SYSTEM provisioning refuses preexisting untrusted owners/writers,
validates ordinary paths and applies protected ACLs to the
existing corpus. It preserves corpus/catalog bytes and rejects missing data.
The manifest contains `documents: [{"path": ".sources/NAME.md", "sha256":
"64 lowercase hex characters"}]` and optional `srs_documents` catalog keys.
Every Markdown document must be cataloged. Registered SRS/wiki documents must
be at most400 lines; wiki links must resolve. Previously accepted `.sources`
bytes cannot be rewritten. New ratified wiki revisions require matching
catalog hashes; invalid revisions do not replace the last accepted index.

The dedicated `cochem-knowledge-mcp --client-config client.json` entrypoint
provides the same three knowledge tools. The authenticated controller exposes `knowledge_search`, `knowledge_read` and
`knowledge_status` through MCP. CLI examples:

```powershell
cochem-pipeline knowledge-search --client-config client.json --query "lease fencing"
cochem-pipeline knowledge-read --client-config client.json --doc-path wiki/00_skeleton.md
cochem-pipeline knowledge-status --client-config client.json
cochem-pipeline knowledge-refresh --client-config client.json --full
```

Incremental refresh and corruption recovery publish whole index generations.
The backend and MCP round-trip latency benchmarks are distinct. Current host
measurements do not certify the <5ms target on the Windows installation.

## Planning prerequisites and coding changes

The source refers to `v2/task_planning_orchestra.py` and `Method_Matrix.md`
(M-1–M-8), but their definitions are absent from the available repository and
history. The new `planning` project policy validates registered source paths,
physical hashes, exact ordered clause quotes and external research URLs.
Registration explicitly does not prove the seven stages executed.

Until the canonical transition definitions can be implemented and verified,
production coding submission retains a durable `PLANNING_HOLD` with the exact
reason and no runnable coding stages. This is a remaining implementation
blocker. Do not remove the hold or manufacture seven labels to claim readiness.
The primary manifest→chapter→Gemini-synthesis planning DAG remains a separate
implemented workflow with its own native acceptance requirements.

Coding component behavior now enforces:

- Real 20–100-line context windows and a combined 100 added-plus-deleted Git-line
  ceiling across source and tests. Existing short files have no 80% rewrite exemption.
- Physical failing assertions before source edits. `preserve_behavior` is no
  longer accepted as a way to bypass RED. Collection/import failures are not RED.
- Immutable ordered phase evidence, mock/stub rejection, bounded diagnostics,
  and a single 30-second execution/report-collection deadline.
- Hashed `.patch` artifacts, actual `git apply --cached` in a private index,
  result-tree verification and existing fenced branch compare-and-swap.
- Evidence-backed planning revisions, source confidence and bounded research
  pivots. Exhaustion writes an immutable autopsy; it does not claim a physics
  diagnosis for an ordinary software task.

Ordinary timeout/protocol failures retain their selected model, spend the
ordinary failure budget and persist exponential backoff. Availability failures
retain their separate tier fallback behavior. Leases default to 1800 seconds,
are capped at 3600, and renew at most every 5 seconds. Terminal ordinary poison
pills use `BLOCKED`, retain consumed budgets and cannot be resumed to reset them.

## Independent repair evidence

Candidate-side pytest/JUnit is regression evidence only: candidate Python can
forge its own report. Promotion additionally requires a trusted outer verifier
that imports no candidate code and independently checks physical state after
bounded, isolated candidate operations. It owns the verdict outside candidate
write access. Post-deployment native smoke and rollback gates remain required.
Finite black-box checks prove their tested contracts, not arbitrary code safety.

A separate detector process uses only standard library/psutil and protected
observer code. Native recovery actuators remain in the independent supervisor
installation. Crash evidence includes bounded PEP657 frame positions without
source, locals, prompts or exception values; process observations fence PID
history by creation time. Database evidence is a bounded metadata snapshot,
not an undisclosed copy of user prompts or credentials.

Failed restart escalation is permitted only after confirmed cleanup and a
code-related diagnosis. Authentication, quota, resource and environment holds
do not become reasons to spend model credits. Existing paid-repair budgets and
cooldowns remain durable.

## Physical acceptance still required

Run the actual native isolation, RAM backing, Docker API denial, sensor,
subscription model and workflow tests on the provisioned Windows host. Run the
full coding acceptance verifier against a real completed workflow after the
planning-definition blocker is resolved. Do not label offline CLI help or
SQLite fixtures as native inference.

`python -m cochem_pipeline.performance_acceptance --help` describes real queue
contention (`--queue-concurrency processes` is required for ten-daemon acceptance) and native 48-hour observation. A short diagnostic cannot pass the
48-hour gate. Missing desktop-heap used/capacity telemetry remains unavailable;
USER/GDI handle counts are not substituted for heap percentage.

Performance receipts include queue and handoff time. Prepared containers use the
same four seats; strict production dispatch requests a matching prepared profile
rather than silently falling back to cold creation. A timing miss remains an
immutable SLA failure even if the code tests finish successfully. Cold startup,
queue contention and knowledge latency must be measured and brought within the
SRS limits on the target host before any full-compliance claim.
