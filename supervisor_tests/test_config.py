"""Supervisor policy validation using physical native-path JSON documents."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cochem_supervisor.config import DEFAULT_ALLOWED, DEFAULTS, PATH_FIELDS, load_config


def document(tmp_path):
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps({"private_root": str(tmp_path / "pipeline-private"),
        "token_file": str(tmp_path / "operator" / "token"), "port": 47824,
        "providers": {"codex": {"model": "gpt-6-astra"}, "claude": {"model": "claude-fable-5-1"},
                      "gemini": {"model": "gemini-3.1-pro"}}}), encoding="utf-8")
    raw = {key: str(tmp_path / key) for key in PATH_FIELDS}
    raw.update(pipeline_config=str(pipeline), pointer_file=str(tmp_path / "private_root" / "current.json"),
               repair_worker={"name": "CoChemRepair", "credential_target": "CoChem/Repair"},
               providers=[{"provider": "codex", "model": "gpt-6-astra", "executable": str(tmp_path / "bin" / "codex.exe")},
                          {"provider": "claude", "model": "claude-fable-5-1", "executable": str(tmp_path / "bin" / "claude.exe")}])
    return raw


def write_config(tmp_path, raw, encoding="utf-8"):
    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps(raw), encoding=encoding)
    return path


def test_defaults_native_paths_and_pipeline_settings_are_loaded_without_provisioning(tmp_path):
    raw = document(tmp_path)
    result = load_config(write_config(tmp_path, raw, "utf-8-sig"))
    assert result["max_per_incident"] == 2 and result["max_per_day"] == 4
    assert result["cooldown_seconds"] == 1800
    assert result["pipeline_private_root"] == str(tmp_path / "pipeline-private")
    assert result["pipeline_token_file"] == str(tmp_path / "operator" / "token")
    assert result["pipeline_port"] == 47824
    assert result["pipeline_providers"]["gemini"]["model"] == "gemini-3.1-pro"
    assert result["pipeline_routing"]["tiers"]["10"][1]["reasoning_effort"] == "ultra"
    assert result["providers"][1]["model"] == "claude-fable-5-1"
    assert not Path(result["private_root"]).exists()
    assert result["allowed_paths"] == DEFAULT_ALLOWED
    assert "mcp_tests/test_claude_subscription.py" not in result["test_targets"]


def test_loaded_default_policy_collections_are_independent(tmp_path):
    raw = document(tmp_path)
    first = load_config(write_config(tmp_path, raw))
    second = load_config(tmp_path / "supervisor.json")
    assert first["allowed_paths"] is not DEFAULT_ALLOWED
    assert first["allowed_paths"] is not second["allowed_paths"]
    assert first["test_targets"] is not DEFAULTS["test_targets"]
    expected_targets = list(DEFAULTS["test_targets"])
    assert expected_targets
    first["allowed_paths"].clear()
    first["test_targets"].clear()
    assert second["allowed_paths"] == DEFAULT_ALLOWED
    assert second["test_targets"] == expected_targets


@pytest.mark.parametrize("field", PATH_FIELDS)
def test_all_policy_paths_must_be_absolute_native_paths(tmp_path, field):
    raw = document(tmp_path)
    raw[field] = "relative/path"
    with pytest.raises(ValueError, match=field):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("field", ["private_root", "repair_workspace", "release_root", "acceptance_root"])
def test_security_roots_cannot_overlap(tmp_path, field):
    raw = document(tmp_path)
    other = "release_root" if field == "private_root" else "private_root"
    raw[field] = str(Path(raw[other]) / "nested")
    with pytest.raises(ValueError, match="disjoint"):
        load_config(write_config(tmp_path, raw))


def test_release_pointer_must_be_inside_private_state(tmp_path):
    raw = document(tmp_path)
    raw["pointer_file"] = str(tmp_path / "outside-pointer.json")
    with pytest.raises(ValueError, match="pointer"):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("field,value", [
    ("max_per_incident", 0), ("max_per_day", True), ("poll_seconds", -1),
    ("cooldown_seconds", "1800"), ("repair_timeout_seconds", float("nan")),
    ("minimum_passed_tests", 0), ("maximum_skipped_tests", -1),
    ("max_log_bytes", 1), ("max_log_bytes", 1023), ("max_log_bytes", 67108865),
    ("auto_deploy", "true"), ("warden_task", "name;command"), ("supervisor_task", "../outside"),
    ("max_per_incident", 1001), ("max_per_day", 1001),
    ("repair_timeout_seconds", 86401), ("test_timeout_seconds", 86401),
])
def test_invalid_policy_bounds_fail_closed(tmp_path, field, value):
    raw = document(tmp_path)
    raw[field] = value
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("value", [1024, 67108864])
def test_log_limit_boundaries_are_accepted(tmp_path, value):
    raw = document(tmp_path)
    raw["max_log_bytes"] = value
    assert load_config(write_config(tmp_path, raw))["max_log_bytes"] == value


@pytest.mark.parametrize("field,value", [
    ("max_per_incident", 1000), ("max_per_day", 1000),
    ("repair_timeout_seconds", 86400), ("test_timeout_seconds", 86400),
])
def test_policy_limits_match_runner_and_ledger_upper_bounds(tmp_path, field, value):
    raw = document(tmp_path)
    raw[field] = value
    assert load_config(write_config(tmp_path, raw))[field] == value


@pytest.mark.parametrize("change", ["wrong_model", "unknown_provider", "duplicate", "relative_executable", "nul_executable", "none"])
def test_repair_provider_contract_is_explicit_and_subscription_cli_only(tmp_path, change):
    raw = document(tmp_path)
    if change == "wrong_model":
        raw["providers"][0]["model"] = "gpt-anything"
    elif change == "unknown_provider":
        raw["providers"][0]["provider"] = "ollama"
    elif change == "duplicate":
        raw["providers"][1] = dict(raw["providers"][0])
    elif change == "relative_executable":
        raw["providers"][0]["executable"] = "codex"
    elif change == "nul_executable":
        raw["providers"][0]["executable"] = str(tmp_path / "codex\x00.exe")
    else:
        raw["providers"] = []
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("targets", [[], ["supervisor_tests"], ["pipeline_tests/../malicious.py"], ["--override"], ["pipeline_tests;command"]])
def test_acceptance_test_targets_cannot_escape_trusted_suites(tmp_path, targets):
    raw = document(tmp_path)
    raw["test_targets"] = targets
    with pytest.raises(ValueError, match="Acceptance targets"):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("allowed", [[], ["src/cochem_supervisor/"], ["scripts/"], ["src/cochem_pipeline/../config.py"], ["src\\cochem_pipeline\\"], ["src/cochem_pipeline/file\x00.py"]])
def test_repair_paths_cannot_override_supervisor_or_escape_source_policy(tmp_path, allowed):
    raw = document(tmp_path)
    raw["allowed_paths"] = allowed
    with pytest.raises(ValueError, match="Allowed repair paths"):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("pipeline", [[], None, {"private_root": "relative", "token_file": "/valid/token"},
    {"private_root": "/valid/private", "token_file": "relative"},
    {"private_root": "/valid/private", "token_file": "/valid/token", "port": True},
    {"private_root": "/valid/private", "token_file": "/valid/token", "port": 65536},
    {"private_root": "/valid/private", "token_file": "/valid/token", "providers": []}])
def test_referenced_pipeline_configuration_must_be_well_formed(tmp_path, pipeline):
    raw = document(tmp_path)
    # Ensure otherwise-valid fixture paths use this host's absolute-path syntax.
    if isinstance(pipeline, dict):
        pipeline = dict(pipeline)
        for key in ("private_root", "token_file"):
            if pipeline[key].startswith("/valid/"):
                pipeline[key] = str(tmp_path / pipeline[key].split("/")[-1])
    Path(raw["pipeline_config"]).write_text(json.dumps(pipeline), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("field", ["private_root", "token_file"])
def test_pipeline_authority_paths_reject_nul_before_runtime(tmp_path, field):
    raw = document(tmp_path)
    path = Path(raw["pipeline_config"])
    pipeline = json.loads(path.read_text(encoding="utf-8"))
    pipeline[field] = str(tmp_path / "invalid\x00path")
    path.write_text(json.dumps(pipeline), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("provider", [[], {}])
def test_unhashable_provider_input_is_rejected_as_configuration(tmp_path, provider):
    raw = document(tmp_path)
    raw["providers"][0]["provider"] = provider
    with pytest.raises(ValueError, match="Repair providers"):
        load_config(write_config(tmp_path, raw))


@pytest.mark.parametrize("policy", [[], {"tiers": {}}, {"tiers": {"1-4": []}}])
def test_pipeline_routing_contract_rejects_wrong_matrix(tmp_path, policy):
    raw = document(tmp_path)
    path = Path(raw["pipeline_config"])
    pipeline = json.loads(path.read_text(encoding="utf-8"))
    pipeline["routing"] = policy
    path.write_text(json.dumps(pipeline), encoding="utf-8")
    with pytest.raises(ValueError, match="Routing policy"):
        load_config(write_config(tmp_path, raw))
