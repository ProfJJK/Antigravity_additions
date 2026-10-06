# 4.2.6: Further SRS corrections and explicit acceptance blockers

The second implementation audit found genuine gaps beyond Windows validation.
This release corrects those behaviors and retracts the stronger compliance
implications of the 4.2.5 ledger. **The complete SRS is still not certified.**
See [requirements](REQUIREMENTS_4.2.6.md), [deployment](EXECUTION_4.2.6.md) and
[planning prerequisites](PLANNING_PREREQUISITES.md).

Changes include:

- Ordered Oracle delivery generations and exact acknowledgments; A→B→A works
  across restarts. Prompt bytes use bounded native named pipes.
- Exact lease release, WAL/NORMAL, 1800-second defaults, 5-second heartbeats,
  persisted same-model timeout/protocol backoff and terminal poison pills.
- Combined source/test Git limits, real context windows, physical RED and ordered
  phase joins, mock/stub checks, 30-second test/collection deadline, literal patch
  application and immutable exhaustion/autopsy evidence.
- Registered FTS5/BM25 knowledge service, authenticated MCP/HTTP/CLI, immutable
  source captures, catalog/link validation, atomic reindex and corruption recovery.
- Actual controller affinity/priority and measured budgets, bounded CLI output,
  Windows Docker process flags, verified RAM restart cleanup, policy-specific
  warm provisioning and complete request-to-start timing.
- Sterile detector process, structural queue checks, process memory/handle
  trends, bounded PEP657 evidence and private SQLite snapshots.
- Independent outer repair verifier, corrected failed-restart escalation and
  migration of consumed component-recovery budgets.

A malicious repair candidate could forge the old in-process JUnit report.
Regression results are now supplemented by a protected outer verifier that
imports no candidate code and checks physical database/filesystem contracts.
Finite black-box checks cannot prove arbitrary code correctness; native
identity/ACL enforcement and post-deployment live smoke remain separate gates.

The owner approved amending archived FR009: ordinary terminal failures remain
isolated; structural queue corruption triggers global containment.

The seven-stage planning definitions and Method Matrix M-1–M-8 are absent from
the available repository/history. Registered-source validation, research
confidence and revision mechanics exist, but production coding remains in a
durable `PLANNING_HOLD` until the actual stage semantics can be implemented.
The original manifest/chapter/Gemini synthesis DAG is a separate workflow.
A fabricated stage flag cannot confer production coding acceptance.

Windows/WSL2/SYSTEM/ImDisk/native subscription acceptance is unrun here.
Physical measurements also retain failures against the SRS latency targets;
a warm startup result, short resource sample or passing unit suite does not
close those requirements. The 48-hour soak and desktop-heap metric require
actual host evidence. Do not present this release as meeting every SRS point.

The machine-readable [validation record](VALIDATION_4.2.6.json) records the final
single-run totals, artifact hashes, performance measurements and outstanding
acceptance. Component runs are not added together to inflate those totals.

Final validation: **2,086 passed, 34 skipped**, zero failures/errors in the full
Linux suite, including actual Docker and offline native Codex/Claude probes.
The separate frozen-source regression run passed **1,301 tests, 52 skipped**
across 43 targets; candidate, test and verifier source trees remained unchanged.
The independent outer verifier passed its four physical contracts across 24
child executions. These runs are separate, not additive totals.

The wheel contains 119 Python modules, all identical to the tested source, and
passed isolated import checks. Seven PowerShell scripts parsed without syntax
errors; no Windows provisioning commands were executed. Frozen dependency
synchronization installed 4.2.6 successfully.

Measured shortfalls remain: ten independent queue claim processes reached
29.347ms against<5ms; authenticated knowledge MCP averaged 7.731ms against<5ms
(backend queries averaged 4.100ms and passed). Historical cold Docker startup
was 4.85s against 1.5s; a prepared fixture measured 0.486536s. Full host workload
and native acceptance remain outstanding.
