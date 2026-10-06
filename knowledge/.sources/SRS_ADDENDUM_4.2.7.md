# 4.2.7 owner-approved SRS addendum

Effective October 6, 2026 (America/Chicago). This addendum records the owner's
explicit decisions. It supersedes conflicting conclusions in the 4.2.6 audit;
the historical measurements and test results are preserved.

## Accepted performance observations

| Measurement | Owner disposition | Required follow-through |
| --- | --- | --- |
| Linux ten-process queue acquisition maximum 29.347 ms | Acceptable observed range; queue acceptance is conditional on Windows launch. | Measure the actual Windows controller, four workers, deployed database/WAL location and representative workload. Only consider queue redesign if that measurement still misses 5 ms. |
| Knowledge backend mean 4.100 ms; authenticated MCP mean 7.731 ms | Both are within the owner-accepted range. The authenticated 5 ms value is an optimization objective, not a release blocker by itself. | Profile the approximately 3.6 ms difference and optimize measured overhead while retaining authentication, schema validation and exact results. |
| Historical cold Docker preparation 4.85 s | Acceptable pool-replenishment overhead. | Maintain a persistent prepared-container pool. Normal jobs consume prepared single-use containers; cold creation occurs only in replenishment. An empty pool produces an explicit wait. |

These are acceptance of the reported observations, not a newly invented
universal maximum or a guarantee for all future workloads. Keep request/queue,
prepared handoff and replenishment timings distinct and visible. Do not hide
pool waiting to claim a 1.5-second request latency. Retain all safety, isolation,
test-deadline, resource, index-size and sustained-monitoring requirements.

Windows queue acceptance remains pending until real evidence establishes the
configured topology and workload. A Linux run, synthetic worker, sibling test
database or four independent database clients cannot establish that result.
The launch observer must use the existing controller and actual protected
database; it must not create a second Warden or insert benchmark fiction into
production history. A missed 5 ms target opens measured remediation review,
not permission to weaken leases, fencing, durability or workload isolation.

## Alternative Path: canonical planning authority

The owner explicitly identifies the “canonical seven-stage planning protocol”
and agentic “Method Matrix M-1 through M-8” as obsolete or hallucinated
requirements introduced by historical snippets/debug material. They are
withdrawn from pipeline execution. The real chemical Method Matrix remains a
domain concept; it does not gate ordinary agentic planning or coding.

[4.2.7_SRS.md](../4.2.7_SRS.md) is the new ordered canonical specification. Its
state machines derive from the actual 4.2.0 planning architecture, corrected
concurrency/routing requirements and Oracle SRS, retaining the real telemetry,
coding, TDD, audit, isolation and recovery obligations. Every chapter includes
implementation gaps, remediation and an accepted improvement requirement.

The old phantom-only `PLANNING_HOLD` must not block fresh coding requests.
Authenticated migration of an untouched legacy hold must preserve its original
baseline, history and consumed budgets. Genuine research, no-progress,
revision-limit, resource, provider and safety holds remain enforceable.

No `stage_execution_verified` assertion or supplied model text constitutes
proof. The replacement contract requires controller-validated real states,
predecessor receipts, source and artifact hashes, and physical execution
evidence. Removing a false prerequisite does not remove those gates.

## Existing owner decisions retained

Exact routing is mandatory for **every** model task: 1–3 Flash → Haiku → Luna;
4–6 Sonnet → Sol → Gemini Pro; 7–9 Opus → Astra low → Gemini Pro; 10 Fable →
Astra ultra. Synthesis, interface-originated work, coding, reviews, research,
preflight and independent repairs have no fixed-model exception. All jobs use
a durable board and configured native subscription CLIs. Provider-specific MCP
names are compatibility interfaces, never model-assignment authority.

The owner corrects the number 20: it is exclusively Claude CLI's maximum
concurrent agents, not a leaf/batch limit or a Codex/Agy limit. The configurable
hardware ceiling begins conservatively at four and may increase with measured
capacity and provisioned isolated workers; hardware safety remains enforced.

Before final code approval, a different-provider Chapter 06 review must
reconcile the exact SRS chapters, WBS leaf, requirements, criteria, tests and
implemented source against the accepted plan. Missing coverage or divergence
blocks approval and Git integration until bounded remediation succeeds.

The existing ImDisk `R:\` drive is 8 GiB and is created by the owner's Windows
startup task. Adopt it without formatting or replacing it. One-time setup is
sufficient; startup waits, attestation and scratch setup are automated. Do not
claim safe dynamic resizing without driver and filesystem evidence. Default to
the existing size with visible pressure holds and occupancy diagnostics.

All 18 chapter suggestions are accepted and now normative `S427-OPS-001` through
`S427-OPS-018`. Manual preflight respects universal routing: non-inference
capability/auth checks can cover every CLI; paid model probes use the tier
order and report actual coverage. It must not invent unavailability to force a
provider selection. Production latency/error objectives remain evidence-driven
post-launch choices, not invented measurements.

This amendment is canonical revision `owner-amendment-2026-10-06` of 4.2.7.
The original `v4.2.7` tag is preserved as historical publication; current amended
sources and the `v4.2.7-r2` tag carry these decisions. Use distinct installed
code/corpus roots and a fresh protected knowledge index for r2 so immutable
source pins from the first publication are not rewritten. Preserve durable
jobs, identities, credentials, receipts, repair budgets and original captures.

The approved FR009 amendment also remains: ordinary failures stay isolated;
structural queue corruption triggers containment. Native Windows/live-model
and 48-hour acceptance still require actual host evidence.
