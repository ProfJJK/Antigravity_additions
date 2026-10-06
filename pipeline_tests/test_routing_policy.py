"""Pure, deterministic task scoring and immutable native routing contracts.

No test invokes a provider or treats fixture content as native execution.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from cochem_pipeline.routing import (
    ModelTarget, RoutingPolicy, load_routing_policy,
    score_task, tier_for_score, validate_selected_route,
)


EXPECTED = {
    "1-3": [("gemini", "gemini-3.8-flash", None), ("claude", "claude-haiku-4-5", None), ("codex", "gpt-6-luna", None)],
    "4-6": [("claude", "claude-sonnet-5-5", None), ("codex", "gpt-6-sol", None), ("gemini", "gemini-3.1-pro", None)],
    "7-9": [("claude", "claude-opus-5-5", None), ("codex", "gpt-6-astra", "low"), ("gemini", "gemini-3.1-pro", None)],
    "10": [("claude", "claude-fable-5-1", None), ("codex", "gpt-6-astra", "ultra")],
}


@pytest.mark.parametrize("score,tier", [(1, "1-3"), (3, "1-3"), (4, "4-6"), (6, "4-6"),
                                      (7, "7-9"), (9, "7-9"), (10, "10")])
def test_authoritative_tier_boundaries_order_models_and_exact_effort(score, tier):
    policy = RoutingPolicy()
    assert tier_for_score(score) == tier
    candidates = policy.candidates(score, "CHAPTER_DRAFT")
    assert [(value.provider, value.model, value.reasoning_effort) for value in candidates] == EXPECTED[tier]
    assert all(value.max_concurrency is None and value.quota_pool == value.provider for value in candidates)
    assert len({value.key for value in candidates}) == len(candidates)


def test_ultra_and_low_are_distinct_target_identities_without_remapping():
    low = RoutingPolicy().candidates(7)[1]
    ultra = RoutingPolicy().candidates(10)[1]
    assert low.key == "codex:gpt-6-astra:low"
    assert ultra.key == "codex:gpt-6-astra:ultra"
    assert low != ultra and low.as_dict()["reasoning_effort"] == "low"
    assert ultra.as_dict()["reasoning_effort"] == "ultra"


@pytest.mark.parametrize("score", range(1, 11))
@pytest.mark.parametrize("kind", ["SYNTHESIS", "REPAIR_REQUEST", "REPAIR_REVIEW", "PREFLIGHT_REQUEST", "CODE_PLAN", "CODE_REVIEW"])
def test_every_model_job_uses_the_same_complexity_tier(score, kind):
    candidates = RoutingPolicy().candidates(score, kind)
    assert [(value.provider, value.model, value.reasoning_effort) for value in candidates] == EXPECTED[tier_for_score(score)]


@pytest.mark.parametrize("score", [True, False, 0, 11, -1, 1.0, "1", None, {}, []])
def test_only_integer_controller_complexity_scores_are_accepted(score):
    with pytest.raises(ValueError):
        RoutingPolicy().candidates(score)


def test_policy_snapshot_digest_and_collections_survive_roundtrip_and_mutation():
    policy = RoutingPolicy()
    data = policy.as_dict()
    expected = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    assert policy.digest == expected
    assert RoutingPolicy.from_dict(data) == policy == load_routing_policy(data)
    data["tiers"]["10"][1]["reasoning_effort"] = "high"
    data["provider_limits"]["codex"]["max_concurrency"] = 64
    assert policy.candidates(10)[1].reasoning_effort == "ultra"
    assert policy.provider_max_concurrency["codex"] is None
    copy = policy.failure_cooldowns
    copy["quota"] = 0
    assert policy.failure_cooldowns["quota"] == 300
    with pytest.raises(FrozenInstanceError):
        policy._snapshot_json = "{}"


def test_shared_subscription_pool_and_exact_model_caps_are_explicit():
    policy = load_routing_policy({
        "provider_limits": {"codex": {"max_concurrency": 3, "quota_pool": "shared"},
                            "claude": {"max_concurrency": 2, "quota_pool": "shared"}},
        "quota_pool_limits": {"shared": 2, "gemini": 1},
        "model_limits": {"codex:gpt-6-astra:ultra": 2},
    })
    assert policy.provider_max_concurrency == {"codex": 3, "claude": 2, "gemini": None}
    assert policy.quota_pool_max_concurrency == {"gemini": 1, "shared": 2}
    assert policy.candidates(10)[0].quota_pool == "shared"
    assert policy.candidates(10)[1].quota_pool == "shared"
    assert policy.candidates(10)[1].max_concurrency == 2
    assert policy.candidates(7)[1].max_concurrency is None


@pytest.mark.parametrize("config", [
    [], "default", {"unexpected": 1}, {"policy_version": True}, {"policy_version": 2},
    {"tiers": {}}, {"tiers": []}, {"model_limits": {"unknown": 1}},
    {"model_limits": {"codex:gpt-6-luna": True}}, {"model_limits": {"codex:gpt-6-luna": 0}},
    {"model_limits": {"codex:gpt-6-luna": 1000001}}, {"provider_limits": {"other": {}}},
    {"provider_limits": {"codex": {"quota_pool": "unconfigured"}}},
    {"provider_limits": {"codex": {"max_concurrency": False}}},
    {"provider_limits": {"codex": {"quota_pool": "../outside"}}},
    {"provider_limits": {"codex": {"unknown": 1}}},
    {"quota_pool_limits": {"codex": 0, "claude": 2, "gemini": 2}},
    {"failure_cooldowns": {"invented_error": 0}}, {"failure_cooldowns": {"quota": -1}},
    {"failure_cooldowns": {"busy": float("nan")}}, {"backlog_threshold": 0},
    {"backoff_base_seconds": 0}, {"backoff_max_seconds": 601},
    {"backoff_base_seconds": 20, "backoff_max_seconds": 10},
    {"backoff_jitter_fraction": 1.1}, {"backoff_jitter_fraction": True},
    {"max_routing_cycles": -1}, {"max_routing_cycles": True},
    {"max_routing_seconds": -1}, {"max_routing_seconds": float("inf")},
    {"max_dispatches": -1},
])
def test_invalid_or_unbounded_policy_configuration_fails_closed(config):
    with pytest.raises(ValueError):
        load_routing_policy(config)


@pytest.mark.parametrize("change", ["third_tier10", "too_few", "duplicate", "wrong_tier", "wrong_provider",
                                   "model_command", "model_colon", "unknown_field", "bad_effort", "claude_effort"])
def test_policy_entries_cannot_add_phantom_fallbacks_or_ambiguous_identity(change):
    data = RoutingPolicy().as_dict()
    if change == "third_tier10":
        data["tiers"]["10"].append(data["tiers"]["1-3"][0])
    elif change == "too_few":
        data["tiers"]["4-6"].pop()
    elif change == "duplicate":
        data["tiers"]["1-3"][1] = data["tiers"]["1-3"][0]
    elif change == "wrong_tier":
        data["tiers"]["1-4"] = data["tiers"].pop("1-3")
    elif change == "wrong_provider":
        data["tiers"]["1-3"][0]["provider"] = "api"
    elif change == "model_command":
        data["tiers"]["1-3"][0]["model"] = "gemini --command"
    elif change == "model_colon":
        data["tiers"]["1-3"][0]["model"] = "ambiguous:model"
    elif change == "unknown_field":
        data["tiers"]["1-3"][0]["executable"] = "untrusted.exe"
    elif change == "bad_effort":
        data["tiers"]["10"][1]["reasoning_effort"] = "xhigh"
    else:
        data["tiers"]["10"][0]["reasoning_effort"] = "ultra"
    with pytest.raises(ValueError):
        load_routing_policy(data)


def test_availability_backoff_is_reproducible_jittered_and_capped_after_jitter():
    policy = RoutingPolicy()
    assert 24 <= policy.backoff_delay(1, "task-a") <= 36
    assert 48 <= policy.backoff_delay(2, "task-a") <= 72
    assert policy.backoff_delay(3, "task-a") == RoutingPolicy().backoff_delay(3, "task-a")
    assert policy.backoff_delay(3, "task-a") != policy.backoff_delay(3, "task-b")
    assert 0 < policy.backoff_delay(1000000, "task-a") <= 600
    assert policy.max_routing_cycles == policy.max_routing_seconds == policy.max_dispatches == 0


def test_explicit_operator_ceilings_and_short_test_backoff_are_supported():
    policy = load_routing_policy({"max_routing_cycles": 24, "max_routing_seconds": 3600,
                                 "max_dispatches": 9, "backoff_base_seconds": .001,
                                 "backoff_max_seconds": .01, "backoff_jitter_fraction": 0,
                                 "failure_cooldowns": {"quota": 0, "busy": .001}})
    assert policy.max_routing_cycles == 24 and policy.max_routing_seconds == 3600 and policy.max_dispatches == 9
    assert policy.backoff_delay(1, "task") == .001
    assert policy.backoff_delay(2, "task") == .002
    assert policy.backoff_delay(100, "task") == .01
    assert policy.failure_cooldowns["auth"] == 300


def test_model_claimed_scores_routes_and_efforts_do_not_change_controller_score():
    payload = {"objective": "Write a short summary.", "requirements": ["REQ-1"]}
    actual = score_task("CHAPTER_DRAFT", payload)
    claimed = {**payload, "score": 10, "complexity_score": 10, "tier": "10",
               "routing": {"provider": "claude", "model": "claude-fable-5-1"},
               "reasoning_effort": "ultra", "priority": "highest"}
    assert score_task("CHAPTER_DRAFT", claimed) == actual
    assert actual["score"] == 1 and actual["tier"] == "1-3"


@pytest.mark.parametrize("kind,expected", [("CHAPTER_DRAFT", 1), ("MANIFEST_GENERATOR", 3),
                                         ("MACRO_PLANNING_REQUEST", 3), ("SYNTHESIS", 3)])
def test_task_kind_has_documented_reproducible_weight(kind, expected):
    scored = score_task(kind, {"objective": "Summary."})
    assert scored["score"] == expected
    assert scored["rationale"][0] == {"criterion": "baseline", "points": 1}
    assert scored["metrics"]["kind"] == kind


@pytest.mark.parametrize("length,points", [(2048, 0), (2049, 1), (8192, 1), (8193, 2), (32768, 2), (32769, 3)])
def test_conservative_utf8_size_thresholds_are_deterministic(length, points):
    scored = score_task("CHAPTER_DRAFT", {"objective": "x" * length})
    assert scored["metrics"]["estimated_tokens"] == (length + 3) // 4
    assert {entry["criterion"]: entry["points"] for entry in scored["rationale"]}["input_size"] == points


def test_counts_dependencies_breadth_and_risk_groups_have_persistable_rationale():
    payload = {"objective": "Security migration and concurrency with rollback and numerical proof.",
               "requirements": [f"REQ-{index}" for index in range(12)],
               "dependencies": [f"dependency-{index}" for index in range(4)],
               "chapter_count": 8}
    scored = score_task("CHAPTER_DRAFT", payload)
    assert scored["score"] == 9 and scored["tier"] == "7-9"
    assert scored["metrics"]["requirement_count"] == 12 and scored["metrics"]["dependency_count"] == 4
    assert scored["metrics"]["work_breadth"] == 8
    assert len(scored["metrics"]["risk_groups"]) == 5
    assert json.loads(json.dumps(scored)) == scored
    assert score_task("SYNTHESIS", payload)["score"] == 10


def test_reordered_payload_keys_requirements_and_duplicate_requirements_do_not_inflate_score():
    left = {"objective": "Summary.", "requirements": ["R1", "R2", "R3", "R4"]}
    right = {"requirements": ["R4", "R3", "R2", "R1"], "objective": "Summary."}
    assert score_task("CHAPTER_DRAFT", left) == score_task("CHAPTER_DRAFT", right)
    repeated = score_task("CHAPTER_DRAFT", {"requirements": ["R"] * 12})
    assert repeated["metrics"]["requirement_count"] == 1
    assert repeated["score"] == 1


@pytest.mark.parametrize("kind,payload", [("UNKNOWN", {}), (None, {}), ("CHAPTER_DRAFT", []),
                                        ("CHAPTER_DRAFT", {"objective": float("nan")}),
                                        ("CHAPTER_DRAFT", {"requirements": [{1: "text"}]})])
def test_malformed_scoring_inputs_fail_closed(kind, payload):
    with pytest.raises(ValueError):
        score_task(kind, payload)


def selected(policy=None, *, score=7, index=1, kind="CHAPTER_DRAFT"):
    policy = policy or RoutingPolicy()
    target = policy.candidates(score, kind)[index]
    return {**target.as_dict(), "pool": target.quota_pool, "score": score, "tier": tier_for_score(score),
            "policy_digest": policy.digest, "candidate_index": index, "cycle": 1,
            "reservation_id": "controller-owned-reservation", "attempt_id": "controller-owned-attempt",
            "fencing_token": "controller-owned-token", "kind": kind}


def test_selected_route_binds_effort_ordinal_score_tier_policy_and_pool():
    policy = RoutingPolicy()
    target = validate_selected_route(policy, selected(policy), kind="CHAPTER_DRAFT")
    assert target == policy.candidates(7)[1]
    assert target.reasoning_effort == "low"


@pytest.mark.parametrize("change", [
    {"score": True}, {"tier": "10"}, {"candidate_index": 2}, {"candidate_index": True},
    {"provider": "claude"}, {"model": "gpt-6-luna"}, {"reasoning_effort": "ultra"},
    {"policy_digest": "0" * 64}, {"quota_pool": "foreign"}, {"pool": "foreign"},
    {"max_concurrency": True}, {"max_concurrency": 64}, {"key": "codex:gpt-6-astra:ultra"},
    {"kind": "SYNTHESIS"},
])
def test_selected_route_rejects_mutated_or_misbound_authority(change):
    with pytest.raises(ValueError):
        validate_selected_route(RoutingPolicy(), {**selected(), **change}, kind="CHAPTER_DRAFT")


@pytest.mark.parametrize("missing", ["provider", "model", "reasoning_effort", "key", "quota_pool",
                                     "max_concurrency", "score", "tier", "candidate_index", "policy_digest"])
def test_selected_route_cannot_omit_required_captured_binding(missing):
    route = selected()
    del route[missing]
    with pytest.raises(ValueError):
        validate_selected_route(RoutingPolicy(), route)


def test_current_capacity_change_cannot_reinterpret_old_captured_assignment():
    captured = RoutingPolicy()
    route = selected(captured)
    updated = captured.as_dict()
    updated["model_limits"]["codex:gpt-6-astra:low"] = 2
    current = load_routing_policy(updated)
    assert validate_selected_route(captured, route).reasoning_effort == "low"
    assert current.is_enabled(route)
    with pytest.raises(ValueError):
        validate_selected_route(current, route)
    assert not current.is_enabled({**route, "reasoning_effort": "high"})


def test_synthesis_binding_rejects_wrong_kind_but_accepts_scored_tier_candidate():
    with pytest.raises(ValueError):
        validate_selected_route(RoutingPolicy(), selected(score=1, index=0), kind="SYNTHESIS")
    policy = RoutingPolicy()
    assert validate_selected_route(policy, selected(policy, score=10, index=0, kind="SYNTHESIS")).model == "claude-fable-5-1"


def test_only_claude_has_a_provider_hard_ceiling():
    policy = RoutingPolicy()
    assert policy.provider_max_concurrency == {"codex": None, "claude": 20, "gemini": None}
    assert all(value is None for value in policy.model_max_concurrency.values())
    assert all(value is None for value in policy.quota_pool_max_concurrency.values())
    assert load_routing_policy({"provider_limits": {"gemini": {"max_concurrency": 128}}}).provider_max_concurrency['gemini'] == 128
    for limit in (None, 0, 21, 64, True):
        with pytest.raises(ValueError, match="Claude CLI"):
            load_routing_policy({"provider_limits": {"claude": {"max_concurrency": limit}}})


@pytest.mark.parametrize("tier", ["1-3", "4-6", "7-9", "10"])
def test_unratified_route_reordering_or_model_substitution_is_rejected(tier):
    reordered = RoutingPolicy().as_dict()
    reordered['tiers'][tier].reverse()
    with pytest.raises(ValueError, match='canonical Chapter 06'):
        load_routing_policy(reordered)
    substituted = RoutingPolicy().as_dict()
    substituted['tiers'][tier][0]['model'] = 'unratified-model'
    with pytest.raises(ValueError, match='canonical Chapter 06'):
        load_routing_policy(substituted)
