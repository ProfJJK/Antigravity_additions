# 4.2.2: Fenced planning workflows and Oracle recovery

4.2.2 adds the controller and validation machinery required by
`Oracle_SRS_WBS.md`, `Pipeline_4_2_0_Architecture.md`, `full_dossier.md` and its
untruncated counterpart. It preserves the observable standalone Codex/Claude
subscription bridges introduced in 4.2.1 and the existing scientific package.

## Changes

- Added an SQLite WAL planning DAG: one request creates a manifest job,
  chapter scatter, and a gather barrier for Gemini 3.1 Pro synthesis.
- Added attempt leases and fencing, immutable artifacts, chapter ownership
  constraints, exact synthesis hash verification, and durable recovery.
- Added real CPU/memory/disk admission checks with a four-active-worker limit.
- Added Aho-Corasick context retrieval, exact knapsack budgeting, protected core
  directives, 500ms debounce, durable watermark/outbox handling, and an
  asynchronous breaker above 500 events/sec.
- Implemented the specified `emit_recovery_event` API with exact SQLite schema,
  caller/owned connection handling, explicit commit and preserved Unicode data.
- Added the Windows SYSTEM controller boundary, dedicated worker identities,
  protected state/token ACLs, supervised Job Objects, and per-identity native
  subscription login helpers. Six identities are provisioned by default;
  active execution remains capped at four.
- Added the authenticated localhost control service and unprivileged
  Antigravity MCP frontend. Worker claim/completion operations remain private
  to the controller.
- Added standalone Codex/Claude structured node submission tools for manual
  manifest/chapter development, sharing the daemon's prompt/adapter contract
  while explicitly leaving protected DAG state unchanged.
- Added protected installation/configuration examples, requirement tracing,
  and an audit identifying the limitations of the earlier submission wrappers
  and phrase-based review loops.

## Evidence and remaining prerequisites

The integrated Linux suites finished with **420 passed and 3 skipped**
(two dependency deprecation warnings). The skipped tests require actual
Windows security/process facilities. Frozen dependency synchronization and
the `cochem-4.2.2-py3-none-any.whl` build succeeded.

The suites use real SQLite, OS resource measurements, concurrent threads
and physical Python subprocesses, with pure/explicitly emulated provider
contracts distinguished from inference. The
[requirements matrix](REQUIREMENTS_4.2.2.md) records the individual results and
their precise scope; it is not a certificate of full Windows deployment.

The native Windows installer, SYSTEM/worker isolation, Defender behavior,
Job Object cleanup and actual subscription-backed multi-agent execution
still require target-host acceptance. The installed Agy CLI's headless
arguments, worker-profile authentication and structured response metadata
have not been confirmed. Its example command is deliberately unresolved;
the pipeline rejects unsupported or unverified results instead of declaring
synthesis complete.

Follow the [4.2.2 deployment guide](PIPELINE_4.2.2.md) for installation,
configuration and the six-chapter/four-worker acceptance workflow. The
[4.2.1 release notes](RELEASE_4.2.1.md) remain the record of the prior bridge
release and its separate live-test limitations.

The reusable cloud setup recipe is preserved in
[`config/cloud-environment.proposed.json`](../config/cloud-environment.proposed.json).
Saving it to the current environment draft returned `stale_base`, so it was
not saved there. A new environment setup chat is needed only to apply that
recipe to a fresh draft; this was a stale draft conflict, not an approval
rejection or a block on the repository changes.
