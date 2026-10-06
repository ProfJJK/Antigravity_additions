"""Deployment configuration contracts using real JSON files and pure validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cochem_pipeline.config import PipelineConfig, load_config
from cochem_pipeline.routing import RoutingPolicy


def config_document(tmp_path, slots=6):
    return {
        "private_root": str(tmp_path / "private"),
        "slot_roots": {f"slot-{index}": str(tmp_path / "slots" / str(index)) for index in range(slots)},
        "workers": {f"slot-{index}": {"name": f"CoChemWorker{index}", "credential_target": f"CoChem/slot-{index}"} for index in range(slots)},
        "providers": {
            "codex": {"executable": "codex.exe", "model": "gpt-6.1"},
            "claude": {"executable": "claude.exe", "model": "sonnet"},
            "gemini": {"executable": "agy.exe", "model": "gemini-3.1-pro", "protocol": "terminal-json",
                       "arguments": ["--headless", "--output-format", "json", "--model", "{model}", "--workspace", "{workspace}"],
                       # Synthetic parser contract only; these are not proposed Agy flags/fields.
                       "subscription_probe": {"arguments": ["fixture-status", "--json"], "protocol": "json-fields",
                                              "expected": {"fixture.login": True, "fixture.billing": "subscription"}},
                       "inference_only": {"arguments":["fixture-no-tools","{model}"],"version_arguments":["fixture-version"],
                                          "executable_sha256":"a"*64,"version":"fixture-version",
                                          "capability_reference":"configuration parser fixture; not a native contract",
                                          "disables_tools":True,"disables_mcp":True,"disables_hooks":True}},
        },
        "rules": [], "token_file": str(tmp_path / "operator" / "controller.token"), "operator_name": "ExampleOperator",
    }


def write_config(tmp_path, data, encoding="utf-8"):
    filename = tmp_path / "pipeline.json"
    filename.write_text(json.dumps(data), encoding=encoding)
    return str(filename)


def test_load_six_slot_configuration_with_bom_and_default_budgets(tmp_path):
    raw = config_document(tmp_path)
    config = load_config(write_config(tmp_path, raw, "utf-8-sig"))
    assert len(config.slot_roots) == 6
    assert config.private_root == (tmp_path / "private").resolve()
    assert config.job_db == config.private_root / "job_board.db"
    assert config.context_budget == 16384
    assert config.reserved_fraction == .25
    assert config.lease_seconds == 30
    assert config.max_attempts == 3
    assert config.providers["gemini"]["model"] == "gemini-3.1-pro"
    assert isinstance(config.routing, RoutingPolicy)
    assert config.routing.candidates(10)[1].reasoning_effort == "ultra"
    assert not config.private_root.exists(), "Loading configuration must not provision privileged paths"


@pytest.mark.parametrize("slots", [1, 64])
def test_supported_slot_capacity_boundaries(tmp_path, slots):
    assert len(load_config(write_config(tmp_path, config_document(tmp_path, slots))).slot_roots) == slots


@pytest.mark.parametrize("slots", [0, 65])
def test_invalid_slot_capacity_boundaries(tmp_path, slots):
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, config_document(tmp_path, slots)))


@pytest.mark.parametrize("key,value", [("port", True), ("port", 1023), ("port", 65536),
    ("timeout_seconds", 0), ("timeout_seconds", 14401), ("lease_seconds", 4),
    ("max_attempts", 0), ("context_budget", 1023), ("context_budget", 1048577),
    ("min_free_memory_mb", -1), ("min_free_disk_mb", 1.5),
    ("reserved_fraction", True), ("reserved_fraction", float("nan")), ("reserved_fraction", 0),
    ("reserved_fraction", 1.1)])
def test_invalid_resource_and_budget_values_fail_closed(tmp_path, key, value):
    raw = config_document(tmp_path, 1)
    raw[key] = value
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


def test_private_and_worker_paths_must_be_absolute_distinct_and_disjoint(tmp_path):
    for name, value in [("private", "relative/private"), ("slot", "relative/slot"),
                        ("same", str(tmp_path / "private")), ("nested", str(tmp_path / "private" / "worker"))]:
        raw = config_document(tmp_path, 1)
        if name == "private":
            raw["private_root"] = value
        else:
            raw["slot_roots"]["slot-0"] = value
        with pytest.raises(ValueError):
            load_config(write_config(tmp_path, raw))


def test_worker_identity_keys_and_account_names_must_be_distinct(tmp_path):
    raw = config_document(tmp_path, 2)
    raw["workers"].pop("slot-1")
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))
    raw = config_document(tmp_path, 2)
    raw["workers"]["slot-1"]["name"] = raw["workers"]["slot-0"]["name"].upper()
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("key,value", [("name", ""), ("name", "   "), ("credential_target", ""), ("credential_target", 123)])
def test_worker_metadata_requires_nonempty_strings(tmp_path, key, value):
    raw = config_document(tmp_path, 1)
    raw["workers"]["slot-0"][key] = value
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini"])
@pytest.mark.parametrize("key,value", [("executable", ""), ("executable", "   "), ("executable", "cli\x00.exe"),
                                       ("executable", ["cli"]), ("model", ""), ("model", "   "), ("model", 42)])
def test_provider_configuration_requires_explicit_valid_strings(tmp_path, provider, key, value):
    raw = config_document(tmp_path, 1)
    raw["providers"][provider][key] = value
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("arguments", [[], "--model {model}", ["--model={model}"], ["--model", 12],
    ["REPLACE_WITH_VERIFIED_HEADLESS_ARGUMENTS", "--model", "{model}"],
    ["--model", "{model}", "\x00"], ["--model", "{model}", "{unknown_placeholder}"]])
def test_gemini_argv_requires_verified_complete_placeholder_free_configuration(tmp_path, arguments):
    raw = config_document(tmp_path, 1)
    raw["providers"]["gemini"]["arguments"] = arguments
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.1-flash", "gemini-3-pro",
                                 "gemini-3.1-pro-preview", "pretend-gemini-3.1-pro"])
def test_synthesis_requires_gemini_31_pro(tmp_path, model):
    raw = config_document(tmp_path, 1)
    raw["providers"]["gemini"]["model"] = model
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


def test_unknown_gemini_result_protocol_is_rejected(tmp_path):
    raw = config_document(tmp_path, 1)
    raw["providers"]["gemini"]["protocol"] = "guess-output"
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


def test_operator_token_cannot_use_relative_or_worker_visible_location(tmp_path):
    for location in ("relative/token", str(tmp_path / "slots" / "0" / "controller.token")):
        raw = config_document(tmp_path, 1)
        raw["token_file"] = location
        with pytest.raises(ValueError):
            load_config(write_config(tmp_path, raw))
    raw = config_document(tmp_path, 1)
    raw["operator_name"] = "  "
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("rules", [{"id": "not-a-list"}, ["not-a-rule"], [{"id": "broken"}]])
def test_malformed_rule_configuration_is_rejected_during_load(tmp_path, rules):
    raw = config_document(tmp_path, 1)
    raw["rules"] = rules
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("probe", [None, {},
    {"arguments": [], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"},
    {"arguments": ["status", "{model}"], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"},
    {"arguments": ["REPLACE_WITH_NATIVE_STATUS_ARGS"], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"},
    {"arguments": ["status", "\x00"], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"},
    {"arguments": ["status"], "protocol": "invented", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"},
    {"arguments": ["status"], "protocol": "exact-line", "success_line": ""},
    {"arguments": ["status"], "protocol": "exact-line", "success_line": "first\nsecond"},
    {"arguments": ["status"], "protocol": "exact-line", "success_line": "REPLACE_WITH_SUCCESS_LINE"},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {}},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {"auth..active": True}},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {"auth": {"nested": True}}},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {"auth.tier": ["active"]}},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {"auth.tier": float("nan")}},
    {"arguments": ["status"], "protocol": "json-fields", "expected": {"REPLACE_FIELD": "active"}},
])
def test_missing_unverified_or_malformed_subscription_probe_is_rejected(tmp_path, probe):
    raw = config_document(tmp_path, 1)
    if probe is None:
        del raw["providers"]["gemini"]["subscription_probe"]
    else:
        raw["providers"]["gemini"]["subscription_probe"] = probe
    with pytest.raises(ValueError, match="subscription_probe"):
        load_config(write_config(tmp_path, raw))


def test_exact_line_subscription_probe_loads_without_guessing_native_schema(tmp_path):
    raw = config_document(tmp_path, 1)
    probe = {"arguments": ["fixture-status"], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"}
    raw["providers"]["gemini"]["subscription_probe"] = probe
    assert load_config(write_config(tmp_path, raw)).providers["gemini"]["subscription_probe"] == probe


def test_manual_pipeline_config_constructors_receive_independent_default_routing(tmp_path):
    values = dict(private_root=tmp_path, slot_roots={}, workers={}, providers={}, rules=[],
                  token_file=tmp_path / "token", operator_name="operator")
    first, second = PipelineConfig(**values), PipelineConfig(**values)
    assert isinstance(first.routing, RoutingPolicy) and first.routing == second.routing
    assert first.routing is not second.routing


def test_config_captures_model_routing_independently_of_provider_default_model(tmp_path):
    raw = config_document(tmp_path)
    raw["routing"] = {"model_limits": {"codex:gpt-6-astra:ultra": 2},
                      "failure_cooldowns": {"quota": 60}, "backoff_jitter_fraction": 0}
    configured = load_config(write_config(tmp_path, raw))
    assert configured.providers["codex"]["model"] == "gpt-6.1"
    assert configured.routing.candidates(10)[1].model == "gpt-6-astra"
    assert configured.routing.candidates(10)[1].max_concurrency == 2
    assert configured.routing.failure_cooldowns["quota"] == 60
    raw["routing"]["model_limits"]["codex:gpt-6-astra:ultra"] = 63
    assert configured.routing.candidates(10)[1].max_concurrency == 2


@pytest.mark.parametrize("routing", [[], {"tiers": {}}, {"backoff_max_seconds": 601},
                                     {"max_routing_cycles": -1}, {"provider_limits": {"api": {}}}])
def test_invalid_routing_policy_is_rejected_before_runtime(tmp_path, routing):
    raw = config_document(tmp_path)
    raw["routing"] = routing
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


def coding_config_document(tmp_path):
    raw = config_document(tmp_path)
    raw['ramdisk'] = {'enabled': True}
    raw['docker'] = {'enabled': True, 'image': 'sha256:'+'a'*64,
                     'allowed_images': ['sha256:'+'a'*64],
                     'commands': [{'name':'tests','argv':['python','-m','pytest','tests']}]}
    raw['coding_projects'] = {'sample': {'repository':str(tmp_path/'repository'),
                                       'branch':'pipeline/accepted','allowed_paths':['src'],
                                       'test_paths':['tests'],'auto_integrate':True}}
    return raw


def test_coding_configuration_captures_explicit_execution_policy(tmp_path):
    config = load_config(write_config(tmp_path, coding_config_document(tmp_path)))
    assert config.coding_projects['sample'].auto_integrate is True
    assert config.docker.enabled and config.ramdisk.enabled
    assert config.execution_limits.cpu_rate_percent == 20
    assert config.hardware.gpu_required is True


@pytest.mark.parametrize('component', ['ramdisk','docker'])
def test_coding_projects_cannot_run_without_required_execution_plane(tmp_path, component):
    raw = coding_config_document(tmp_path)
    raw[component] = {'enabled': False}
    with pytest.raises(ValueError, match='RAM workspaces and enabled Docker'):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize('location', ['private','private/nested','slots/0','slots/0/nested','operator','operator/controller.token'])
def test_coding_project_registry_cannot_expose_control_or_identity_data(tmp_path, location):
    raw = coding_config_document(tmp_path)
    raw['coding_projects']['sample']['repository'] = str(tmp_path/location)
    with pytest.raises(ValueError, match='disjoint'):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize('component', ['hardware','execution_limits','ramdisk','docker'])
def test_unknown_execution_settings_fail_instead_of_silent_configuration_drift(tmp_path, component):
    raw = config_document(tmp_path)
    raw[component] = {'misspelled_limit': 1}
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))
