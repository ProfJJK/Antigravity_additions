# Antigravity additions 4.2.7

The [canonical SRS](4.2.7_SRS.md), revision `model-routing-2026-10-07`, governs
this pipeline. All 18 improvement inserts are accepted requirements. The
[owner addendum](docs/SRS_ADDENDUM_4.2.7.md) records the corrections and accepted
performance observations; [source precedence](docs/SOURCE_PRECEDENCE_4.2.7.md)
separates these decisions from historical architecture and debug material.

Every model job goes through a durable job board and this complexity policy:

| Complexity | Assignment order |
| --- | --- |
| 1–3 | Gemini 3.8 Flash, default → Claude Haiku 4.5, default → GPT-6 Luna, low |
| 4–6 | Claude Sonnet 5.5, default → GPT-6.1 Sol, medium → Gemini 3.8 Flash, Extended |
| 7–9 | GPT-6.1 Sol, high → Claude Opus 5.5, Extended → Gemini 3.1 Pro Preview, high |
| 10 | Claude Fable 5.1, Extended → GPT-6 Astra, ultra |

Sol effort choices are latency/quota hypotheses, not a demonstrated equivalence
to Opus. Extended requires a protected, reviewed native CLI profile; it is not a
guessed command-line flag. Missing support produces compatibility holds and
normal spillover. See [research and exact identifiers](docs/ROUTING_RESEARCH_2026-10-07.md).
The [native effort guide](docs/NATIVE_EFFORT_4.2.7.md) documents the protected
bindings required for Claude Extended and Agy High/Extended profiles.

This applies to planning, synthesis, coding, research, reviews, model preflight
and independent repairs. Busy/unavailable/quota-limited candidates spill over
in order; exhausted tiers wait with durable backoff, then restart at the
preferred model. Claude alone has a provider ceiling of 20 concurrent agents.
Codex and Agy have no assumed provider ceiling. Hardware admission is configured
separately, starting conservatively at four seats until the host is measured.
The [13700K/64 GB guide](docs/WORKSTATION_13700K_4.2.7.md) recommends measuring six
then eight seats after preflight, with sufficient identities and memory headroom.
New tasks capture catalogue version 2; score algorithm version 1 is unchanged.
Older captures/receipts remain immutable; retired targets hold at new dispatch
and spill over within their captured chain without silent model substitution.

Antigravity 2.0 is the primary GUI. The protected Windows Python controller
launches native subscription CLIs; WSL2/Linux containers run generated code and
tests. New work uses the pipeline MCP. Provider-named Codex/Claude MCP tools are
compatibility front ends to the same board and reject fixed-model requests.
No API-key inference fallback or model-written completion claim is accepted.

Coding preserves physical RED/GREEN tests, the ten-phase evidence sequence,
bounded changes and fenced Git integration. Before final approval a separately
routed, different-provider review reconciles the exact SRS chapters and WBS
requirements with the implementation, tests and audits. Missing or divergent
evidence blocks integration. The obsolete agentic seven-stage/Method Matrix
prerequisites remain withdrawn.

The existing ImDisk `R:` 8 GiB volume is adopted without formatting or repeated
manual setup. Automatic startup waits and reattests it. Normal Docker tests
consume persistent prepared single-use containers; cold creation is background
pool replenishment. Fixed pool targets remain the default until measured demand
supports adaptation.

`pipeline_operator_view` exposes authenticated governance, prerequisite and
routing views, measured hardware plots, RAM occupancy, Oracle decision history,
pool demand, storage forecasts, workload objectives and per-requirement
acceptance. Knowledge results carry reviewed authority badges.

- [Upgrade and launch](docs/EXECUTION_4.2.7.md)
- [Amendment operations and commands](docs/OPERATIONS_4.2.7-r2.md)
- [Requirement ledger](docs/requirements_4.2.7.json)
- [Acceptance evidence](docs/acceptance_4.2.7.json)
- [Amendment release record](docs/RELEASE_4.2.7-r2.md)
- [Windows queue observation](docs/QUEUE_LAUNCH_4.2.7.md)

The [current routing validation](docs/evidence/VALIDATION_model-routing-2026-10-07.json)
records **2,597 passed tests and 35 Windows-only skips**, with real Linux Docker
execution, offline CLI checks and exact tested-source hashes. No paid native
inference was performed. The initial historical-fixture failure and its
correction remain in the evidence history. The [public knowledge snapshot](docs/evidence/KNOWLEDGE_model-routing-2026-10-07.json)
contains 11 documents and 126 sections; it does not update the protected Windows index.

**The nine findings in the [4.2.7-r2 audit](docs/AUDIT_4.2.7-r2.md) are not closed
by this routing update. Windows/live-subscription and 48-hour acceptance remain
pending actual host evidence.** Portable test results do not certify those conditions. Preserve
existing jobs, accounts, subscription logins and recovery budgets on upgrade.
Use distinct `4.2.7-r2` code/corpus directories; preserve the original `v4.2.7`
tag and immutable source captures. The routing amendment retains version 4.2.7
and existing installation paths; it does not change a historical release tag.
The active `knowledge/` corpus and exported
FTS5 snapshot contain the amended specification. Archived corpora stay outside
active retrieval.

Earlier release records remain available under `docs/`; their fixed-model
assignments, leaf limits and old deployment examples are historical and are
superseded by the canonical SRS and current owner decisions.
