"""Deterministic controller-owned complexity and native subscription routing.

Complexity starts at 1. Planning/manifest/synthesis add 2; chapter drafting adds
0. Estimated text tokens are max(word count, ceil(UTF-8 bytes / 4)): more than
512/2048/8192 add 1/2/3. Four/twelve requirements add 1/2; one/four dependencies
add 1/2; three/eight chapters or WBS tasks add 1/2. One/three distinct lexical
risk groups add 1/2. The sum is capped at 10. These are transparent scheduling
heuristics, not claims about model intelligence or actual tokenizer counts.

Only task content is scored. A model's score, tier, provider, model, priority or
routing fields never confer dispatch authority. The controller persists this
rationale and an immutable policy snapshot before making a reservation.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any


POLICY_VERSION = 1
SYNTHESIS_MODEL = "gemini-3.1-pro"
_PROVIDERS = ("codex", "claude", "gemini")
_KINDS = {"MACRO_PLANNING_REQUEST", "MANIFEST_GENERATOR", "CHAPTER_DRAFT", "SYNTHESIS",
          "CODE_REQUEST", "CODE_PLAN", "CODE_PLAN_REVIEW", "CODE_TEST_AUTHOR", "CODE_EDIT", "CODE_REVIEW", "CODE_RESEARCH"}
_TIER_SIZES = {"1-3": 3, "4-6": 3, "7-9": 3, "10": 2}
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
_POOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_RISK_GROUPS = {
    "security": ("security", "authentication", "authorization", "credentials", "cryptography", "permissions"),
    "data_integrity": ("migration", "schema", "transaction", "consistency", "corruption"),
    "concurrency": ("concurrency", "concurrent", "distributed", "deadlock", "race condition", "synchronization"),
    "destructive_change": ("delete", "deletion", "destructive", "irreversible", "production", "rollback"),
    "formal_reasoning": ("proof", "theorem", "optimization", "numerical", "algorithm", "invariant"),
}
_AUTHORITY_FIELDS = {"score", "complexity", "complexity_score", "tier", "routing", "route", "priority",
                     "provider", "model", "requested_model", "reasoning_effort", "policy_digest"}
_CONTENT_FIELDS = {"objective", "title", "description", "requirements", "dependencies", "chapters",
                   "chapter_count", "artifact_text", "wbs", "tasks", "acceptance_criteria", "scope"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate routing policy field: {key}")
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f"Nonfinite routing policy number: {value}")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def tier_for_score(score: int) -> str:
    if type(score) is not int or not 1 <= score <= 10:
        raise ValueError("Complexity score must be an integer in 1..10")
    return "1-3" if score <= 3 else "4-6" if score <= 6 else "7-9" if score <= 9 else "10"


def _target_key(provider: str, model: str, reasoning_effort: str | None) -> str:
    return f"{provider}:{model}" + (f":{reasoning_effort}" if reasoning_effort is not None else "")


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str
    max_concurrency: int = 1
    quota_pool: str = ""
    reasoning_effort: str | None = None

    @property
    def key(self) -> str:
        return _target_key(self.provider, self.model, self.reasoning_effort)

    def as_dict(self) -> dict:
        return {"key": self.key, "provider": self.provider, "model": self.model,
                "reasoning_effort": self.reasoning_effort, "max_concurrency": self.max_concurrency,
                "quota_pool": self.quota_pool}


def _defaults() -> dict:
    def route(provider, model, effort=None):
        return {"provider": provider, "model": model, "reasoning_effort": effort}
    return {
        "policy_version": POLICY_VERSION,
        "tiers": {
            "1-3": [route("gemini", "gemini-3.8-flash"), route("claude", "claude-haiku-4-5"), route("codex", "gpt-6-luna")],
            "4-6": [route("claude", "claude-sonnet-5-5"), route("codex", "gpt-6-sol"), route("gemini", SYNTHESIS_MODEL)],
            "7-9": [route("claude", "claude-opus-5-5"), route("codex", "gpt-6-astra", "low"), route("gemini", SYNTHESIS_MODEL)],
            "10": [route("claude", "claude-fable-5-1"), route("codex", "gpt-6-astra", "ultra")],
        },
        "model_limits": {},
        "provider_limits": {provider: {"max_concurrency": 2, "quota_pool": provider} for provider in _PROVIDERS},
        "quota_pool_limits": {provider: 2 for provider in _PROVIDERS},
        "backlog_threshold": 8,
        "failure_cooldowns": {"quota": 300, "auth": 300, "busy": 30, "backlog": 30,
                              "provider": 30, "timeout": 30, "resource": 30,
                              "configuration": 300, "compatibility": 300, "protocol": 30,
                              "context": 0, "code": 0, "unknown": 30},
        "backoff_base_seconds": 30,
        "backoff_max_seconds": 600,
        "backoff_jitter_fraction": 0.2,
        "max_routing_cycles": 0,
        "max_routing_seconds": 0,
        "max_dispatches": 0,
    }


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _number(value: Any, label: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number in {low}..{high}")
    return float(value)


def _route_pair(value: Any) -> dict:
    if not isinstance(value, Mapping) or set(value) - {"provider", "model", "reasoning_effort"}:
        raise ValueError("Each routing entry must specify provider, model and optional reasoning_effort")
    provider, model, effort = value.get("provider"), value.get("model"), value.get("reasoning_effort")
    if not isinstance(provider, str) or provider not in _PROVIDERS:
        raise ValueError("Routing requires a native codex, claude or gemini provider")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ValueError("Routing requires an explicit native model identifier without command text")
    if effort is not None and (provider != "codex" or effort not in ("low", "ultra")):
        raise ValueError("Explicit routing reasoning_effort must be native Codex low or ultra; no remapping is allowed")
    return {"provider": provider, "model": model, "reasoning_effort": effort}


def _normalize(raw: Mapping[str, Any] | None) -> dict:
    defaults = _defaults()
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping) or set(raw) - set(defaults):
        raise ValueError("Routing policy must be an object containing only documented policy fields")
    data = {**defaults, **raw}
    if type(data["policy_version"]) is not int or data["policy_version"] != POLICY_VERSION:
        raise ValueError("Unsupported routing policy_version")
    tiers = data["tiers"]
    if not isinstance(tiers, Mapping) or set(tiers) != set(_TIER_SIZES):
        raise ValueError("Routing tiers must be exactly 1-3, 4-6, 7-9 and 10")
    normalized_tiers = {}
    keys = set()
    for tier, count in _TIER_SIZES.items():
        values = tiers[tier]
        if not isinstance(values, (list, tuple)) or len(values) != count:
            raise ValueError(f"Tier {tier} requires exactly {count} ordered candidates")
        pairs = [_route_pair(value) for value in values]
        identities = [_target_key(value["provider"], value["model"], value["reasoning_effort"]) for value in pairs]
        if len(set(identities)) != count:
            raise ValueError(f"Tier {tier} must contain distinct provider/model/effort identities")
        normalized_tiers[tier] = pairs
        keys.update(identities)
    keys.add(_target_key("gemini", SYNTHESIS_MODEL, None))
    data["tiers"] = normalized_tiers
    limits = data["model_limits"]
    if not isinstance(limits, Mapping) or set(limits) - keys:
        raise ValueError("model_limits must refer only to configured routing target keys")
    data["model_limits"] = {key: _integer(limits.get(key, 1), f"model_limits.{key}", 1, 64) for key in sorted(keys)}
    providers = data["provider_limits"]
    if not isinstance(providers, Mapping) or set(providers) - set(_PROVIDERS):
        raise ValueError("provider_limits may configure only codex, claude and gemini")
    normalized_providers = {}
    for provider in _PROVIDERS:
        value = providers.get(provider, defaults["provider_limits"][provider])
        if not isinstance(value, Mapping) or set(value) - {"max_concurrency", "quota_pool"}:
            raise ValueError("Each provider limit contains max_concurrency and quota_pool")
        pool = value.get("quota_pool", provider)
        if not isinstance(pool, str) or not _POOL.fullmatch(pool):
            raise ValueError("Quota pools require literal nonempty identifiers")
        normalized_providers[provider] = {"max_concurrency": _integer(value.get("max_concurrency", 2), "provider max_concurrency", 1, 64),
                                         "quota_pool": pool}
    data["provider_limits"] = normalized_providers
    pools = data["quota_pool_limits"]
    active_pools = {value["quota_pool"] for value in normalized_providers.values()}
    if not isinstance(pools, Mapping) or any(not isinstance(pool, str) or not _POOL.fullmatch(pool) for pool in pools):
        raise ValueError("quota_pool_limits must map pool identifiers to concurrency limits")
    if active_pools - set(pools):
        raise ValueError("Every configured shared quota pool needs an explicit concurrency limit")
    data["quota_pool_limits"] = {pool: _integer(pools[pool], f"quota_pool_limits.{pool}", 1, 64) for pool in sorted(active_pools)}
    data["backlog_threshold"] = _integer(data["backlog_threshold"], "backlog_threshold", 1, 100000)
    cooldowns = data["failure_cooldowns"]
    if not isinstance(cooldowns, Mapping) or set(cooldowns) - set(defaults["failure_cooldowns"]):
        raise ValueError("failure_cooldowns contains an unsupported native failure category")
    data["failure_cooldowns"] = {key: _number(cooldowns.get(key, value), f"failure_cooldowns.{key}", 0, 86400)
                                 for key, value in defaults["failure_cooldowns"].items()}
    data["backoff_base_seconds"] = _number(data["backoff_base_seconds"], "backoff_base_seconds", .001, 600)
    data["backoff_max_seconds"] = _number(data["backoff_max_seconds"], "backoff_max_seconds", data["backoff_base_seconds"], 600)
    data["backoff_jitter_fraction"] = _number(data["backoff_jitter_fraction"], "backoff_jitter_fraction", 0, 1)
    data["max_routing_cycles"] = _integer(data["max_routing_cycles"], "max_routing_cycles", 0, 1000000)
    data["max_routing_seconds"] = _number(data["max_routing_seconds"], "max_routing_seconds", 0, 31536000)
    data["max_dispatches"] = _integer(data["max_dispatches"], "max_dispatches", 0, 1000000)
    return data


@dataclass(frozen=True)
class RoutingPolicy:
    """Immutable captured policy; public dictionaries are independent copies.

    Use load_routing_policy(mapping) or from_dict(mapping) for configuration.
    Keeping the canonical snapshot as a string prevents later mutation of a
    configuration dictionary from changing already-enqueued task authority.
    Zero cycle, duration and dispatch limits mean no operator-supplied ceiling;
    availability retries still use durable bounded-delay backoff.
    """
    _snapshot_json: str = field(default_factory=lambda: _canonical(_normalize(None)), repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_snapshot_json", _canonical(_normalize(_strict_json(self._snapshot_json))))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RoutingPolicy:
        return load_routing_policy(data)

    def as_dict(self) -> dict:
        return json.loads(self._snapshot_json)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._snapshot_json.encode("utf-8")).hexdigest()

    @property
    def provider_max_concurrency(self) -> dict[str, int]:
        return {key: value["max_concurrency"] for key, value in self.as_dict()["provider_limits"].items()}

    @property
    def quota_pool_max_concurrency(self) -> dict[str, int]:
        return self.as_dict()["quota_pool_limits"]

    @property
    def model_max_concurrency(self) -> dict[str, int]:
        return self.as_dict()["model_limits"]

    @property
    def failure_cooldowns(self) -> dict[str, float]:
        return self.as_dict()["failure_cooldowns"]

    @property
    def backlog_threshold(self) -> int:
        return self.as_dict()["backlog_threshold"]

    @property
    def backoff_base_seconds(self) -> float:
        return self.as_dict()["backoff_base_seconds"]

    @property
    def backoff_max_seconds(self) -> float:
        return self.as_dict()["backoff_max_seconds"]

    @property
    def backoff_jitter_fraction(self) -> float:
        return self.as_dict()["backoff_jitter_fraction"]

    @property
    def max_routing_cycles(self) -> int:
        return self.as_dict()["max_routing_cycles"]

    @property
    def max_routing_seconds(self) -> float:
        return self.as_dict()["max_routing_seconds"]

    @property
    def max_dispatches(self) -> int:
        return self.as_dict()["max_dispatches"]

    def candidates(self, score: int, kind: str = "CHAPTER_DRAFT") -> tuple[ModelTarget, ...]:
        tier = tier_for_score(score)
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("Unsupported pipeline task kind")
        data = self.as_dict()
        pairs = [{"provider": "gemini", "model": SYNTHESIS_MODEL, "reasoning_effort": None}] if kind == "SYNTHESIS" else data["tiers"][tier]
        result = []
        for pair in pairs:
            key = _target_key(pair["provider"], pair["model"], pair["reasoning_effort"])
            result.append(ModelTarget(**pair, max_concurrency=data["model_limits"][key],
                                      quota_pool=data["provider_limits"][pair["provider"]]["quota_pool"]))
        return tuple(result)

    def is_enabled(self, target: ModelTarget | Mapping[str, Any]) -> bool:
        """Check current catalogue identity without changing a captured route's order.

        Provider/model/effort identify a target. Current capacity/pool changes do
        not rewrite the policy of a job already captured under an older policy.
        Reservation admission still applies the controller's current limits.
        """
        value = target.as_dict() if isinstance(target, ModelTarget) else target
        if not isinstance(value, Mapping):
            return False
        identity = (value.get("provider"), value.get("model"), value.get("reasoning_effort"))
        return any(identity == (candidate.provider, candidate.model, candidate.reasoning_effort)
                   for score, kind in ((1, "CHAPTER_DRAFT"), (4, "CHAPTER_DRAFT"), (7, "CHAPTER_DRAFT"),
                                       (10, "CHAPTER_DRAFT"), (1, "SYNTHESIS"))
                   for candidate in self.candidates(score, kind))

    def backoff_delay(self, cycle: int, seed: str) -> float:
        """Deterministic per-task/cycle jitter, capped after jitter at the maximum."""
        _integer(cycle, "backoff cycle", 1, 1000000000)
        if not isinstance(seed, str) or not seed:
            raise ValueError("Backoff jitter requires a nonempty stable task seed")
        digest = hashlib.sha256(f"{seed}\0{cycle}".encode("utf-8")).digest()
        unit = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
        base = min(self.backoff_max_seconds, self.backoff_base_seconds * 2 ** min(cycle - 1, 30))
        factor = 1 + (2 * unit - 1) * self.backoff_jitter_fraction
        return min(self.backoff_max_seconds, max(.001, base * factor))


def load_routing_policy(raw: Mapping[str, Any] | None = None) -> RoutingPolicy:
    return RoutingPolicy(_canonical(_normalize(raw)))


def _content_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _content_strings(child)
    elif isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Task content object keys must be strings")
        for key in sorted(value):
            if key.casefold() not in _AUTHORITY_FIELDS:
                yield from _content_strings(value[key])


def score_task(kind: str, payload: Mapping[str, Any]) -> dict:
    """Return persisted reproducible metrics/rationale, never a requested score."""
    if not isinstance(kind, str) or kind not in _KINDS or not isinstance(payload, Mapping):
        raise ValueError("Scoring requires a supported task kind and structured task payload")
    try:
        serialized = _canonical(dict(payload))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Task scoring requires finite JSON-compatible content") from exc
    if len(serialized.encode("utf-8")) > 16 * 1024 * 1024:
        raise ValueError("Task payload exceeds the bounded complexity scoring input")
    content = {key: payload[key] for key in sorted(_CONTENT_FIELDS) if key in payload}
    strings = list(_content_strings(content))
    text = "\n".join(strings)
    byte_count = len(text.encode("utf-8"))
    estimated_tokens = max(len(re.findall(r"\S+", text)), (byte_count + 3) // 4)
    requirements = payload.get("requirements", [])
    dependencies = payload.get("dependencies", [])
    requirement_count = len({_canonical(value) for value in requirements}) if isinstance(requirements, list) else 0
    dependency_count = len({_canonical(value) for value in dependencies}) if isinstance(dependencies, list) else 0
    chapter_count = payload.get("chapter_count", 0)
    chapter_count = chapter_count if type(chapter_count) is int and chapter_count >= 0 else 0
    breadth = max([chapter_count, *(len(payload[key]) for key in ("chapters", "wbs", "tasks") if isinstance(payload.get(key), list))])
    folded = text.casefold()
    risks = sorted(group for group, phrases in _RISK_GROUPS.items()
                   if any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", folded) for phrase in phrases))
    points = {"baseline": 1, "task_kind": 0 if kind == "CHAPTER_DRAFT" else 2,
              "input_size": sum(estimated_tokens > limit for limit in (512, 2048, 8192)),
              "requirements": sum(requirement_count >= limit for limit in (4, 12)),
              "dependencies": sum(dependency_count >= limit for limit in (1, 4)),
              "work_breadth": sum(breadth >= limit for limit in (3, 8)),
              "risk_groups": sum(len(risks) >= limit for limit in (1, 3))}
    score = min(10, sum(points.values()))
    return {"score": score, "tier": tier_for_score(score), "policy_version": POLICY_VERSION,
            "rationale": [{"criterion": criterion, "points": value} for criterion, value in points.items()],
            "metrics": {"kind": kind, "utf8_bytes": byte_count, "estimated_tokens": estimated_tokens,
                        "requirement_count": requirement_count, "dependency_count": dependency_count,
                        "work_breadth": breadth, "risk_groups": risks, "uncapped_score": sum(points.values())}}


def validate_selected_route(policy: RoutingPolicy, route: Mapping[str, Any], *, kind: str | None = None) -> ModelTarget:
    """Validate captured catalogue binding; a live reservation remains DB-owned.

    This does not attest that reservation_id/attempt_id/fencing_token are active.
    The caller must match those fields to the actual claimed database job.
    """
    if not isinstance(policy, RoutingPolicy) or not isinstance(route, Mapping):
        raise ValueError("Selected route requires its captured RoutingPolicy and metadata")
    actual_kind = kind if kind is not None else route.get("kind")
    if not isinstance(actual_kind, str) or actual_kind not in _KINDS:
        raise ValueError("Selected route requires the actual pipeline task kind")
    if "kind" in route and route["kind"] != actual_kind:
        raise ValueError("Selected route kind contradicts the actual task")
    tier = tier_for_score(route.get("score"))
    if route.get("tier") != tier or route.get("policy_digest") != policy.digest:
        raise ValueError("Selected route tier/policy digest does not match captured task authority")
    candidates = policy.candidates(route["score"], actual_kind)
    index = _integer(route.get("candidate_index"), "candidate_index", 0, len(candidates) - 1)
    target = candidates[index]
    expected = target.as_dict()
    for key, value in expected.items():
        if key not in route or type(route[key]) is not type(value) or route[key] != value:
            raise ValueError(f"Selected route {key} does not match the configured candidate")
    if "pool" in route and route["pool"] != target.quota_pool:
        raise ValueError("Selected route pool disagrees with quota_pool")
    return target
