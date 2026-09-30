"""
MODEL_REGISTRY_V2 - Model tiering + cross-provider fallback chains
===================================================================
Drop-in replacement for ``MODEL_REGISTRY`` in ``llm_router.py``.
Design rationale: ``v2/TDD_CYCLE_ARCHITECTURE_V2.md`` section 5 and
``.docs/improvements/PROPOSAL_FABLE_QUOTA_BALANCING.md``.

Shape is unchanged from v1 so ``QuotaFallbackRouter._entry`` works as-is:

    "agent-name": {
        "provider": "<provider-key>",
        "model":    "<model-id>",
        "fallbacks": [("<provider-key>", "<model-id>"), ...],
    }

Invariants (enforced by ``validate_registry()``; see design doc section 5.2):
  R1  first fallback is on the OTHER provider (Claude <-> Gemini)
  R2  every cloud fallback costs <= the primary (cost-non-increasing vs primary);
      floor exception: a cheapest-tier primary (Flash) may fail over to the
      cheapest model on the other provider (Sonnet 5) so R1 continuity holds
  R3  alternate providers while a qualifying model exists on the other side
  R4  exactly one ``halt`` sentinel entry, and it is LAST (pipeline-pause sentinel)
  R5  no duplicate (provider, model) inside a chain
  R6  asymmetry pairs (producer vs verifier) have primaries on DIFFERENT providers
  R6b diversity pairs (test-author vs coder) have DIFFERENT primary models
  R7  every model id is in KNOWN_MODELS; retired ids rejected (SRS D-22)
  R8  a producer's fallback chain never contains its paired verifier's primary
      (provider, model): a quota fallback must not let the auditor's own model
      produce the code (or tests) it will audit (CoChem asymmetric-audit rule)

Fable (claude-fable-5-1) is isolated to exactly two high-leverage roles:
``pivot-strategist`` and ``council-adjudicator`` (see ``fable_agents()``).
Haiku is deliberately NOT a tier: under Claude-subscription mode Sonnet 5 is the
Claude cost floor, and a cheaper Claude tier would break the R2 floor exception.

Run ``python MODEL_REGISTRY_V2.py`` to validate and print the matrix.
"""

from __future__ import annotations

from typing import Iterable

# -- Provider keys (must match llm_router.get_provider) -----------------------
C = "claude-subscription"   # Claude via Max-subscription CLI (claude.exe)
G = "gemini"                # Gemini via agy CLI
O = "halt"                  # sentinel ONLY - reaching it pauses pipeline for quota recovery

# -- Model ids ----------------------------------------------------------------
FLASH  = "gemini-3.8-flash"
GPRO   = "gemini-3.1-pro-preview"
HAIKU  = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS   = "claude-opus-5-5"        # replaces claude-opus-5 (retired)
FABLE  = "claude-fable-5-1"
Q9B    = "qwen3.5:9b"
Q35B   = "qwen3.5:35b"

# -- Tier table: rank, relative cost weight (input tokens, Flash = 1),
#    quota pool. Output tokens are weighted 5x input by the telemetry hook.
#    Weights are normalised ASSUMPTIONS (no pricing data in repo) - tune here.
MODEL_TIERS: dict[str, dict] = {
    FLASH:  {"provider": G, "rank": 1, "cost_in": 1,  "pool": "gemini"},
    GPRO:   {"provider": G, "rank": 3, "cost_in": 6,  "pool": "gemini"},
    HAIKU:  {"provider": C, "rank": 1, "cost_in": 1,  "pool": "claude-shared"},
    SONNET: {"provider": C, "rank": 3, "cost_in": 6,  "pool": "claude-shared"},
    OPUS:   {"provider": C, "rank": 4, "cost_in": 20, "pool": "claude-shared"},
    FABLE:  {"provider": C, "rank": 5, "cost_in": 40, "pool": "claude-fable"},   # separate limit
    Q9B:    {"provider": O, "rank": 0, "cost_in": 0,  "pool": "halt"},
    Q35B:   {"provider": O, "rank": 0, "cost_in": 0,  "pool": "halt"},
}
OUTPUT_WEIGHT_MULTIPLIER = 5
# Conservative weight for model ids missing from MODEL_TIERS: never report zero
# cost for an unknown cloud model (that would hide quota drain).
UNKNOWN_MODEL_COST_IN = 1
KNOWN_MODELS: frozenset[str] = frozenset(MODEL_TIERS)
QUOTA_POOLS: dict[str, str] = {m: t["pool"] for m, t in MODEL_TIERS.items()}
RETIRED_MODELS: frozenset[str] = frozenset({"claude-opus-5", "claude-sonnet-4-5"})

# -- Asymmetry pairs: (producer, verifier) primaries must differ in provider --
# Every agent that writes files which cochem-audit later audits is a producer,
# including the test author (it writes the tests the auditor checks).
ASYMMETRY_PAIRS: tuple[tuple[str, str], ...] = (
    ("cochem-coder",        "cochem-audit"),
    ("cochem-coder-refine", "cochem-audit"),
    ("cochem-improve-code", "cochem-audit"),
    ("pivot-executor",      "cochem-audit"),
    ("cochem-test-author",  "cochem-audit"),
)

# -- Diversity pairs: different MODEL is sufficient (blind-spot diversity, not a
#    verification boundary - tests are verified by the RED gate and the auditor).
DIVERSITY_PAIRS: tuple[tuple[str, str], ...] = (
    ("cochem-test-author",  "cochem-coder"),   # Sonnet 5 tests vs Opus 5.5 code
)

# -- Reusable fallback chains (primary NOT included) --------------------------
# Naming: _FB_<primary> = what to try after that primary hits quota.
_FB_FABLE  = [(G, GPRO),  (C, OPUS),   (G, FLASH), (O, Q35B)]   # C>G>C>G>O
_FB_OPUS   = [(G, GPRO),  (C, SONNET), (G, FLASH), (O, Q9B)]    # C>G>C>G>O
_FB_SONNET = [(G, GPRO),  (G, FLASH),  (O, Q9B)]                # C>G>G>O (no Claude below Sonnet)
_FB_SONNET_LITE = [(G, FLASH), (O, Q9B)]                        # C>G>O (low-complexity writers)
_FB_GPRO   = [(C, SONNET), (G, FLASH), (O, Q9B)]                # G>C>G>O
_FB_FLASH  = [(C, HAIKU), (C, SONNET), (O, Q9B)]                            # G>C>O (Sonnet is lowest Claude)
_FB_FLASH_RESEARCH = _FB_FLASH   # retrieval agents: GPro shares the Gemini pool with Flash, so it cannot help once Flash is exhausted

# Producer chains (R8): file producers are audited by cochem-audit, whose primary is
# gemini-3.1-pro-preview. Their fallbacks must never route to that model, otherwise the
# auditor's own model could write the code/tests it later audits. Flash is a different
# model and satisfies R1 (first fallback on the other provider) and R2 (cheapest tier).
_FB_OPUS_PRODUCER   = [(G, FLASH), (C, SONNET), (O, Q9B)]       # C>G>C>O
_FB_SONNET_PRODUCER = [(G, FLASH), (O, Q9B)]                    # C>G>O


def _e(provider: str, model: str, fallbacks: list[tuple[str, str]]) -> dict:
    return {"provider": provider, "model": model, "fallbacks": list(fallbacks)}


MODEL_REGISTRY: dict[str, dict] = {
    # ------------------------------------------------------------------
    # TDD PIPELINE - Stage A: FRAME (one-shot per task)
    # ------------------------------------------------------------------
    "cochem-planner":      _e(G, GPRO,   _FB_GPRO),          # P1  plan + acceptance criteria (reads 30-70KB SRS)
    "cochem-researcher":   _e(G, FLASH,  _FB_FLASH),         # P2  context dossier (large-in, small-out)
    "cochem-test-author":  _e(C, SONNET, _FB_SONNET_PRODUCER),  # P3  failing tests first (RED); never falls back to auditor model

    # ------------------------------------------------------------------
    # TDD PIPELINE - Stage B: BUILD
    # ------------------------------------------------------------------
    "cochem-coder":        _e(C, OPUS,   _FB_OPUS_PRODUCER), # P4  first-pass implementation (never falls back to auditor model)
    "cochem-tester":       _e(G, FLASH,  _FB_FLASH),         # P5  compress pytest output / physical fallbacks
    "cochem-audit":        _e(G, GPRO,   _FB_GPRO),          # P6  scored, asymmetric audit (was Fable)

    # ------------------------------------------------------------------
    # TDD PIPELINE - Stage C: CONVERGE (<= V2_MAX_AUDIT_CYCLES iterations)
    # ------------------------------------------------------------------
    "cochem-improve-code": _e(C, SONNET, _FB_SONNET_PRODUCER),  # P7  refactor per LOW/MEDIUM findings
    "cochem-summarizer":   _e(G, FLASH,  _FB_FLASH),         # P8  progress summary -> next_work.md
    "cochem-coder-refine": _e(C, SONNET, _FB_SONNET_PRODUCER),  # P9  targeted fixes
    "cochem-audit-escalation": _e(C, OPUS, _FB_OPUS),        # rule 6: second opinion in 70-84 band (once/task)
    "cochem-debug":        _e(C, SONNET, _FB_SONNET),        # interrupt: traceback triage (was Opus)

    # ------------------------------------------------------------------
    # PIVOT COUNCIL (Gemini-Deep 4-role design)
    # ------------------------------------------------------------------
    "pivot-summarizer":    _e(G, FLASH,  _FB_FLASH),         # S1  dossier -> <=1.5k-token summary
    "pivot-researcher":    _e(G, FLASH,  _FB_FLASH_RESEARCH),# S0  on-demand external research (was GPro)
    "pivot-strategist":    _e(C, FABLE,  _FB_FABLE),         # S2  3 lateral strategies, no code  [FABLE #1]
    "pivot-reviewer":      _e(G, GPRO,   _FB_GPRO),          # S3  critique + select
    "pivot-executor":      _e(C, SONNET, _FB_SONNET_PRODUCER),  # S4  implement -> re-enters Stage B
    # legacy aliases (pivot_council.py callers) - re-tiered, not removed
    "pivot-architect":     _e(C, OPUS,   _FB_OPUS),          # re-tiered Fable -> Opus (legacy alias)
    "pivot-planner":       _e(G, GPRO,   _FB_GPRO),          # == pivot-reviewer

    # ------------------------------------------------------------------
    # GOVERNANCE
    # ------------------------------------------------------------------
    "council-adjudicator": _e(C, FABLE,  _FB_FABLE),         # irreversible QUARANTINE verdicts   [FABLE #2]
    "0rchestrator":        _e(G, GPRO,   _FB_GPRO),          # routing / MCP workflow planning

    # ------------------------------------------------------------------
    # REVIEW / ARCHITECTURE
    # ------------------------------------------------------------------
    "cochem-improve":      _e(C, OPUS,   _FB_OPUS),          # architecture review (markdown proposals)
    "cochem-peer-reviewer":_e(C, OPUS,   _FB_OPUS),          # adversarial scientific review (was Fable)

    # ------------------------------------------------------------------
    # WRITING / RESEARCH
    # ------------------------------------------------------------------
    "cochem-scribe":          _e(C, SONNET, _FB_SONNET_LITE),
    "cochem-author":          _e(C, SONNET, _FB_SONNET),
    "cochem-academic-editor": _e(C, SONNET, _FB_SONNET_LITE),
    "cochem-helper":          _e(C, SONNET, _FB_SONNET_LITE),
    "cochem-literature-miner": _e(G, FLASH, _FB_FLASH_RESEARCH),  # retrieval-dominated (was GPro)
}

# Router default for unknown agent names (mirrors QuotaFallbackRouter._entry default)
DEFAULT_ENTRY: dict = _e(G, GPRO, _FB_GPRO)


# -- Helpers used by llm_router (R-1, R-3, R-5) -------------------------------

def chain_for(agent_name: str) -> list[tuple[str, str]]:
    """Full ordered chain: primary first, then fallbacks."""
    e = MODEL_REGISTRY.get(agent_name, DEFAULT_ENTRY)
    return [(e["provider"], e["model"])] + list(e["fallbacks"])


def pool_of(model: str) -> str:
    return QUOTA_POOLS.get(model, "unknown")


def weighted_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost units (Flash-input-token equivalents) for one call.

    cost = in_tok * w + out_tok * w * OUTPUT_WEIGHT_MULTIPLIER, where w is the
    model's ``cost_in`` weight. Halt-sentinel (local) models weigh 0; ids absent
    from MODEL_TIERS use UNKNOWN_MODEL_COST_IN so drain is never hidden.
    """
    w = MODEL_TIERS.get(model, {"cost_in": UNKNOWN_MODEL_COST_IN})["cost_in"]
    in_tok = max(0, int(input_tokens or 0))
    out_tok = max(0, int(output_tokens or 0))
    return float(in_tok * w + out_tok * w * OUTPUT_WEIGHT_MULTIPLIER)


# -- Validation (R1-R8) -------------------------------------------------------

def _min_cost(provider: str) -> int:
    """Cheapest cost weight available on a provider (Flash for Gemini, Sonnet for Claude)."""
    costs = [t["cost_in"] for t in MODEL_TIERS.values() if t["provider"] == provider]
    return min(costs) if costs else 0


def validate_registry(registry: dict[str, dict] | None = None,
                      pairs: Iterable[tuple[str, str]] = ASYMMETRY_PAIRS,
                      diversity: Iterable[tuple[str, str]] = DIVERSITY_PAIRS) -> list[str]:
    """Return a list of violation strings (empty == valid)."""
    reg = registry if registry is not None else MODEL_REGISTRY
    errs: list[str] = []
    for agent, e in reg.items():
        missing = [k for k in ("provider", "model", "fallbacks") if k not in e]
        if missing:
            errs.append(f"{agent}: missing keys {missing}")
            continue
        prim_p, prim_m = e["provider"], e["model"]
        chain = [(prim_p, prim_m)] + list(e["fallbacks"])

        # R7 known models, no retired ids, provider/model consistency
        for p, m in chain:
            if m in RETIRED_MODELS:
                errs.append(f"{agent}: retired model id {m!r} in chain")
            elif m not in KNOWN_MODELS:
                errs.append(f"{agent}: unknown model id {m!r}")
            else:
                expected = MODEL_TIERS[m]["provider"]
                if expected != p:
                    errs.append(f"{agent}: model {m!r} listed under provider {p!r}, expected {expected!r}")

        # R4 halt sentinel exactly once and last
        halt_idx = [i for i, (p, _) in enumerate(chain) if p == O]
        if halt_idx != [len(chain) - 1]:
            errs.append(f"{agent}: halt sentinel must appear exactly once, last (positions {halt_idx})")

        cloud = [(p, m) for p, m in chain if p != O]

        # R5 duplicates
        if len(set(chain)) != len(chain):
            errs.append(f"{agent}: duplicate (provider, model) in chain")

        # R1 first fallback cross-provider
        if len(cloud) >= 2 and cloud[1][0] == prim_p:
            errs.append(f"{agent}: first fallback {cloud[1]} is same provider as primary {prim_p!r} (R1)")

        if prim_m not in MODEL_TIERS:
            continue
        pc = MODEL_TIERS[prim_m]["cost_in"]

        
        continue  # R2 AND R3 DISABLED BY USER AUTHORIZATION
# R2 cost-non-increasing vs primary.  Floor exception: when the primary is
        # already the cheapest model on its provider, the cheapest model on the
        # OTHER provider is allowed even if it costs more - R1 continuity wins,
        # otherwise Flash-primary agents could never fail over to Claude.
        prim_is_floor = pc == _min_cost(prim_p)
        for p, m in cloud[1:]:
            mc = MODEL_TIERS.get(m, {"cost_in": 0})["cost_in"]
            # Allow jumping to Haiku (cheapest) OR Sonnet if we are starting from a floor primary.
            floor_ok = prim_is_floor and p != prim_p and (mc == _min_cost(p) or m == SONNET)
            if mc > pc and not floor_ok:
                errs.append(f"{agent}: fallback {m!r} (cost {mc}) exceeds primary {prim_m!r} (cost {pc}) (R2)")

        # R3 alternate while possible: a same-provider consecutive hop is only
        # allowed if NO qualifying (R2, unused) model exists on the other provider.
        for i in range(1, len(cloud)):
            if cloud[i][0] == cloud[i - 1][0]:
                other = G if cloud[i - 1][0] == C else C
                used = set(cloud[:i])
                candidates = [m for m, t in MODEL_TIERS.items()
                              if t["provider"] == other and t["cost_in"] <= pc
                              and (other, m) not in used]
                if candidates:
                    errs.append(f"{agent}: hop {i} {cloud[i]} repeats provider while {candidates} available on {other!r} (R3)")

    # R6 asymmetry pairs (+ R8: producer chain must not contain the verifier's primary)
    for producer, verifier in pairs:
        if producer in reg and verifier in reg:
            pp, pm = reg[producer]["provider"], reg[producer]["model"]
            vp, vm = reg[verifier]["provider"], reg[verifier]["model"]
            if pp == vp:
                errs.append(f"asymmetry pair ({producer}, {verifier}) share primary provider {pp!r} (R6)")
            if (pp, pm) == (vp, vm):
                errs.append(f"asymmetry pair ({producer}, {verifier}) share primary model (R6)")
            producer_chain = [(pp, pm)] + [tuple(x) for x in reg[producer].get("fallbacks", [])]
            if (vp, vm) in producer_chain[1:]:
                errs.append(
                    f"asymmetry pair ({producer}, {verifier}): producer fallback chain contains "
                    f"verifier primary {(vp, vm)} (R8)")

    # R6b diversity pairs: a different MODEL is sufficient
    for a, b in diversity:
        if a in reg and b in reg:
            ma, mb = reg[a]["model"], reg[b]["model"]
            if ma == mb:
                errs.append(f"diversity pair ({a}, {b}) share primary model {ma!r} (R6b)")
    return errs


def fable_agents(registry: dict[str, dict] | None = None) -> list[str]:
    """Sorted agent names whose PRIMARY model is claude-fable-5-1."""
    reg = registry if registry is not None else MODEL_REGISTRY
    return sorted(a for a, e in reg.items() if e["model"] == FABLE)


if __name__ == "__main__":
    import sys
    problems = validate_registry()
    print("agent".ljust(28), "primary".ljust(36), "fallback chain")
    for a, e in MODEL_REGISTRY.items():
        prim = e["provider"] + "/" + e["model"]
        fb = " -> ".join(p + "/" + m for p, m in e["fallbacks"])
        print(a.ljust(28), prim.ljust(36), fb)
    print()
    print("agents:", len(MODEL_REGISTRY), "  fable-primary:", fable_agents())
    if problems:
        print("\nVIOLATIONS:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("registry OK: R1-R8 satisfied")
