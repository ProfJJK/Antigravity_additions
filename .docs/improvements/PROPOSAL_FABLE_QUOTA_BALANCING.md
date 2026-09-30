# Proposal: Fable Quota Balancing, Model Registry Invariants & Context Defense

This document formalizes an architectural framework and operational protocol for multi-provider LLM load balancing, token telemetry instrumentation, model tier registry validation, and context window defense across the CoChem agent swarm ecosystem. Quantitative statements are explicitly labelled as either derived (computed from constants in the code) or assumed (normalized planning weights); no live billing or provider telemetry has been collected yet, and Section 6 defines how it will be.

## 1. Executive Summary & Problem Statement
The CoChem multi-agent autonomous chemistry framework relies on an iterative Test-Driven Development (TDD) cycle orchestrated across Stage A (Frame), Stage B (Build), and Stage C (Converge). While this architecture guarantees strict verification, autonomous execution is exposed to two structural failure modes:
1. Asymmetric Quota Depletion and Flagship Model Burn: High-tier models, notably Claude Fable (claude-fable-5-1) and Claude Opus (claude-opus-5-5), sit at the top of the relative cost scale. When assigned to low-leverage or repetitive code iteration tasks they consume shared quota disproportionately, and HTTP 429 (ResourceExhausted) errors can stall the perpetual daemon.
2. Context Ballooning: In multi-round TDD convergence loops, failure tracebacks, whole-file contents, and evidence bundles accumulate. Without bounded character budgets, prompt sizes grow round over round, degrading reasoning quality and increasing token consumption.

This proposal establishes a unified architectural remedy: isolating Claude Fable strictly to high-leverage strategic governance, enforcing formal invariants (R1-R7, plus the R8 producer-chain rule) on model fallback chains, hardening the QuotaFallbackRouter with monotonic cooldown TTLs, establishing token telemetry in LLMCallMeta and TaskContext, and introducing deterministic character budget caps across all file rendering and traceback formatting routines.

## 2. API Quota Consumption Analysis (Claude vs. Gemini CLI)
The CoChem infrastructure interfaces with two provider ecosystems whose quota structures differ. The figures below are derived from MODEL_REGISTRY_V2 constants; the cost weights themselves are normalized assumptions, not published prices.

Quota pools (as encoded in MODEL_TIERS):
- Claude Subscription CLI Provider (claude.exe, Max subscription): billing is drawn from an Agent SDK allowance rather than pay-per-token metering. claude-fable-5-1 is modelled as its own pool (claude-fable), while claude-opus-5-5 and claude-sonnet-5 share one pool (claude-shared). Haiku is not a tier, so claude-sonnet-5 is the Claude floor.
- Gemini CLI Provider (agy.exe): gemini-3.8-flash and gemini-3.1-pro-preview are modelled as a single provider-wide pool (gemini). Because Flash and Pro share it, exhausting Flash makes Pro unavailable as a relief valve; this is why retrieval agents use the Flash-to-Sonnet chain.

Assumed relative cost weights (input tokens, Flash = 1; output tokens weighted 5x input):

| Model | Provider | Pool | Input weight (assumed) |
|---|---|---|---|
| gemini-3.8-flash | gemini | gemini | 1 |
| gemini-3.1-pro-preview | gemini | gemini | 6 |
| claude-sonnet-5 | claude-subscription | claude-shared | 6 |
| claude-opus-5-5 | claude-subscription | claude-shared | 20 |
| claude-fable-5-1 | claude-subscription | claude-fable | 40 |

Per-call cost is cost = in_tokens * w + out_tokens * w * 5. Illustrative worked examples (hypothetical token counts, not measurements):
- Planner call on a 50KB SRS: about 12,500 input tokens (at 4 chars/token) and 2,000 output tokens. On Pro (w=6) this is 12,500*6 + 2,000*6*5 = 135,000 units; the same call on Flash (w=1) would be 22,500 units.
- Coder call with 30,000 input and 4,000 output tokens: on Opus (w=20) this is 1,000,000 units; on Fable (w=40) it would be 2,000,000 units, twice the Opus cost, which is why Fable is excluded from iterative coding roles.

A 30-70KB SRS corresponds to roughly 7,500-17,500 input tokens at 4 chars/token. Whether such calls actually exhaust the Gemini Pro allowance has not been measured in this repository; no exhaustion-frequency data exists yet.

Cross-Provider Asymmetry: Claude and Gemini run on disjoint infrastructure, so an outage or rate-limit event on one provider should fail over to the other. The registry encodes this by requiring the first fallback of every chain to sit on the opposite provider (invariant R1), with weights revisited once measured data is available.

## 3. Context Ballooning & Runaway TDD Loop Mitigations
During Stage B and Stage C iterations, agents inspect test failure logs and workspace file states. Unbounded, this feedback loop inflates prompts:
1. Failure Traceback Proliferation: Pytest failures with long assertion diffs, array prints, and call stacks can be large, and re-injecting full tracebacks in every convergence round compounds the growth.
2. Multi-File Bundle Escalation: As projects grow, reading full source files across the workspace inflates prompts and dilutes model attention.

To bound prompt sizes, the pipeline enforces hard character caps:
- _CAP_FILE_CHARS = 24_000: Bounds each individual file rendered by render_files(). A larger file is truncated at the boundary and annotated with [truncated, N chars total].
- _CAP_BUNDLE_CHARS = 90_000: Bounds the aggregate of rendered files. Once the accumulated total reaches the cap, remaining files are omitted with the annotation (omitted: bundle size cap reached). Each file is also individually capped by _CAP_FILE_CHARS, so the bundle cap acts on the sum of already-capped files.
- _CAP_FAILURE_CHARS = 32_000: Bounds the failure text produced by _render_failures(), with an explicit truncation annotation.
- _CAP_NEXT_WORK_CHARS = 12_000: Bounds the structured handover summary carried between iteration cycles.

Derived ceiling for these four capped components: 90,000 + 32,000 + 12,000 = 134,000 characters. At 4.0 chars/token this is about 33,500 tokens; at 3.5 chars/token about 38,300 tokens; at 3.0 chars/token (dense code or numeric output) about 44,700 tokens. This is a ceiling on the capped components only. It excludes the system prompt, task text, plan, dossier, protocol instructions and XML wrapping, so it is an estimate that depends on the tokenizer and must not be read as a guarantee on total prompt size. Section 6 describes how actual prompt sizes will be measured against it.

## 4. Model Registry Tiering & Architectural Invariants (R1-R8)
MODEL_REGISTRY_V2 enforces its invariants through validate_registry(); the seven numbered rules below are extended by the producer-chain rule R8:
- Invariant R1 (Cross-Provider First Fallback): The first fallback after the primary must reside on the alternate provider (Claude to Gemini or Gemini to Claude).
- Invariant R2 (Cost-Non-Increasing with Floor Exception): Every fallback must have a cost weight less than or equal to the primary. To preserve R1, a floor exception lets a cheapest-tier primary on Gemini (Flash, weight 1) fail over to the cheapest Claude tier (Sonnet 5, weight 6).
- Invariant R3 (Strict Provider Alternation): Chains alternate providers at each hop while an eligible, unused model exists on the opposite provider.
- Invariant R4 (Terminal Halt Sentinel): Every chain ends with exactly one sentinel entry from provider 'halt' in the final position. Standard chains end with ('halt', 'qwen3.5:9b'); the Fable chain (_FB_FABLE) ends with ('halt', 'qwen3.5:35b').
- Invariant R5 (No Duplicate Models): No (provider, model) tuple appears twice within a chain.
- Invariant R6 (Asymmetric Verification Boundary): Producer agents and verifier agents (for example cochem-coder and cochem-audit) use primaries on different providers, satisfying CoChem Rule 1.
- Invariant R6b (Blind-Spot Diversity Pairs): Complementary agents (cochem-test-author and cochem-coder) use distinct primary models (Sonnet 5 versus Opus 5.5).
- Invariant R7 (Known Model Integrity): All models must be members of KNOWN_MODELS, and retired identifiers (claude-opus-5, claude-sonnet-4-5) are rejected.
- Invariant R8 (Producer Chains Exclude the Verifier Primary): R6 only constrains primaries, but a quota fallback can silently route a producer onto the auditor's own model, so the same (provider, model) would both produce and audit code. R8 therefore requires that no producer in an asymmetry pair (cochem-coder, cochem-coder-refine, cochem-improve-code, pivot-executor) lists the verifier's primary, gemini-3.1-pro-preview for cochem-audit, anywhere in its fallback chain. Producers use the dedicated chains _FB_OPUS_PRODUCER (Flash, then Sonnet 5, then halt) and _FB_SONNET_PRODUCER (Flash, then halt), which still satisfy R1-R5 and R7.

Flagship Fable Isolation: claude-fable-5-1 is restricted to two high-leverage roles: council-adjudicator (irreversible quarantine verdicts) and pivot-strategist (lateral architectural pivoting). The legacy alias pivot-architect is re-tiered to Claude Opus with _FB_OPUS, and claude-haiku-4-5-20251001 is excluded from MODEL_TIERS so that Sonnet 5 remains the Claude cost floor.

## 5. Claude vs. Gemini Cross-Provider Load Balancing Protocols
Dynamic model dispatch is governed by QuotaFallbackRouter in llm_router.py, a stateful fallback machine designed for graceful degradation under rate limiting:
- Quota Exhaustion Detection: The router treats an error as quota exhaustion when its lower-cased message contains one of the signatures 429, quota, rate_limit, resource_exhausted, overloaded, too many requests, or capacity. Known limitation: the CamelCase spelling ResourceExhausted (no underscore) is currently matched only when the message also contains 429 or quota. Adding the signature resourceexhausted to the signature tuple closes this gap and is recommended.
- Monotonic Cooldown Tracking: When a model hits quota exhaustion, the router records the (provider, model) key in self._disabled and stores time.monotonic() in self._disabled_at.
- TTL Cooldown Window: A disabled model stays quarantined for _DISABLED_TTL_S seconds; invocations within that window skip the model without an external call. After expiry the key is evicted and the model is retried.
- Terminal Halt and Pause Protocol: If every cloud fallback is exhausted the router reaches the halt sentinel, sets PIPELINE_PAUSED_FOR_QUOTA = True, and raises a RuntimeError. The task work loop treats this as a quota pause, waits, clears the router's disabled state and retries for a bounded number of attempts before raising QuotaExhaustedError.

## 6. Implementation Rollout & Telemetry Plan
The rollout proceeds in four phases: Phase 1 establishes registry invariants and Fable isolation; Phase 2 deploys QuotaFallbackRouter cooldowns and halt sentinels; Phase 3 activates character budget caps and token telemetry in .scripts/task_work_loop.py; Phase 4 runs end-to-end TDD validation suites.

Telemetry design:
- LLMCallMeta records input_tokens, output_tokens and cost_units per invocation. Where a provider CLI does not report usage, token counts are estimates (character-count heuristics), and cost figures derived from them are estimates as well.
- Cost units come from weighted_cost(model, in_tok, out_tok), using the assumed weights of Section 2. Unknown model ids use a conservative non-zero weight (UNKNOWN_MODEL_COST_IN) so quota drain is never reported as zero. Callers must not substitute a zero-cost fallback if the registry import fails; a missing weighting function should be treated as an error, not as free usage.
- TaskContext aggregates total_input_tokens, total_output_tokens, total_cost_units and an append-only call_records ledger, serialized through to_dict() and restored by from_dict() with backwards-compatible defaults for legacy contexts.

Measurement procedure to replace assumptions with data:
1. After a representative batch of tasks, aggregate call_records from each tdd_runs/<task_id>/context.json by agent and model, reporting mean and maximum input_tokens, output_tokens and cost_units per phase.
2. Record every quota-pause event (model key, timestamp, agent) so exhaustion frequency per pool can be computed.
3. Compare observed prompt sizes (characters and estimated tokens) for Stage B and Stage C rounds against the Section 3 ceiling and record the ratio.
4. Revise the cost weights in MODEL_TIERS and the cap constants only when the measured distribution justifies it, re-running validate_registry() after each change.
5. Audit call_records for asymmetry: within one task, no (provider, model) that served an audit call may also appear on a producer call. R8 removes the registry-level cause; this check confirms it empirically.
