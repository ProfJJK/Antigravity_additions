"""Contract fixtures and real HTTP transport tests; no Windows/LLM validation.

The local HTTP fixture controller below supplies explicitly synthetic receipt
metadata for validator tests. It never represents a real model run.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

import pytest

from cochem_supervisor.probes import (ControllerClient, configured_routing_policy, configured_tiers, run_smoke_workflow,
    score_routing_task, verify_routing_assignment, verify_smoke_workflow, wait_for_health)

MODELS = {"codex": "gpt-6-astra", "claude": "claude-fable-5-1", "gemini": "gemini-3.1-pro-preview"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def routing_fixture(kind, payload, index=0, policy=None):
    """Synthetic contract metadata only; not a physical scheduler decision."""
    policy = configured_routing_policy(policy)
    scoring = score_routing_task(kind,payload)
    score,tier = scoring["score"],scoring["tier"]
    candidates = deepcopy(policy["tiers"][tier])
    for target in candidates:
        key = target["provider"]+":"+target["model"]+(":"+target["reasoning_effort"] if target["reasoning_effort"] else "")
        target.update(key=key,max_concurrency=policy["model_limits"][key],
                      quota_pool=policy["provider_limits"][target["provider"]]["quota_pool"])
    routing = {"policy":policy,"policy_hash":digest(policy),"score_details":scoring,
               "candidates":candidates,"dispatches":1,"max_dispatches":1}
    route = {**candidates[index],"score":score,"tier":tier,"candidate_index":index,"cycle":0,
             "policy_digest":digest(policy),"reservation_sha256":"b"*64}
    return routing, route


def workflow_fixture(workflow_id="contract-only-fixture"):
    root = {"job_id": workflow_id, "workflow_id": workflow_id, "kind": "MACRO_PLANNING_REQUEST",
            "status": "COMPLETED", "attempts": 0, "max_attempts": 1,
            "payload": {"chapter_count": 2, "requirements": ["SUPERVISOR-1", "SUPERVISOR-2"]}}
    jobs, artifacts = [root], []

    def job(kind, provider, output, chapter_id=None, index=None):
        index_suffix = str(index) if index is not None else kind
        identity = workflow_id + "-" + index_suffix
        payload = {"chapter_index":index,"requirements":[f"SUPERVISOR-{index+1}"]} if index is not None else {}
        candidate_index = 0 if kind == "SYNTHESIS" else 2 if provider == "codex" else 1
        routing, route = routing_fixture(kind, payload, candidate_index)
        receipt = {"provider": provider, "subscription_verified": True, "pid": 123, "execution_kind":"native_cli",
                   "exit_code": 0, "session_id": "synthetic-contract-fixture-not-live",
                   "requested_model": route["model"], "reported_model": route["model"],
                   "requested_effort": route["reasoning_effort"],
                   "reported_effort": route["reasoning_effort"],
                   "route_reservation_sha256": route["reservation_sha256"],
                   "output_sha256": digest(output), "stdout_sha256": "a"*64}
        record = {"job_id": identity, "workflow_id": workflow_id, "parent_job_id": workflow_id,
                  "kind": kind, "status": "COMPLETED", "attempts": 1, "max_attempts": 1,
                  "chapter_id": chapter_id, "payload": payload, "output": output,
                  "receipt": receipt, "output_sha256": digest(output), "routing":routing,"route":route}
        if "artifact_text" in output:
            sha = hashlib.sha256(output["artifact_text"].encode()).hexdigest()
            record["artifact_sha256"] = sha
            artifacts.append({"job_id": identity, "workflow_id": workflow_id,
                              "chapter_id": chapter_id or "synthesis",
                              "artifact_uri": f"db://{workflow_id}/{chapter_id or 'synthesis'}",
                              "artifact_text": output["artifact_text"], "sha256": sha})
        jobs.append(record)
        return record

    job("MANIFEST_GENERATOR", "codex", {"chapters": [{"chapter_id": "startup"}, {"chapter_id": "shutdown"}]})
    chapter_hashes = {}
    for index, name in enumerate(("startup", "shutdown")):
        record = job("CHAPTER_DRAFT", ("codex", "claude")[index], {
            "chapter_id": name, "requirements_traced": [f"SUPERVISOR-{index+1}"],
            "wbs_tasks_defined": [{"id": name+"-task", "description": "Explicit contract fixture"}],
            "artifact_uri": f"db://{workflow_id}/{name}", "artifact_text": "# Fixture " + name,
        }, name, index)
        chapter_hashes[name] = record["artifact_sha256"]
    job("SYNTHESIS", "gemini", {"artifact_text": "# Synthetic contract-test master", "chapter_hashes": chapter_hashes})
    return {"workflow_id": workflow_id, "status": "COMPLETED", "root": root, "jobs": jobs, "artifacts": artifacts}


def test_complete_contract_fixture_has_consistent_receipts_and_artifacts():
    result = verify_smoke_workflow(workflow_fixture(), MODELS)
    assert result["passed"] is True
    assert [row["provider"] for row in result["process_receipts"]] == ["codex", "codex", "claude", "gemini"]
    assert result["model_identity_verified"] is True
    assert "artifact_text" not in json.dumps(result)


def test_missing_codex_model_metadata_is_reported_honestly():
    fixture = workflow_fixture()
    fixture["jobs"][1]["receipt"]["reported_model"] = None
    result = verify_smoke_workflow(fixture, MODELS)
    assert result["unverified_model_providers"] == ["codex"]
    assert result["model_identity_verified"] is False


def test_real_job_store_public_projection_matches_independent_smoke_validator(tmp_path):
    # This is an actual SQLite DAG/public-API projection integration check.
    # Receipts are explicit contract fixture metadata, not real CLI evidence.
    from cochem_pipeline.service import public_workflow
    from cochem_pipeline.store import JobStore

    store = JobStore(tmp_path / "job_board.db")
    workflow = store.submit("Contract fixture only", ["SUPERVISOR-1", "SUPERVISOR-2"], 2,
                            max_attempts=1,max_dispatches=1)
    identifier = workflow["workflow_id"]
    fixture = workflow_fixture(identifier)
    manifest = {"chapters": [
        {"chapter_id": "startup", "title": "Startup", "requirements": ["SUPERVISOR-1"],
         'wbs_tasks_defined': [{'id': 'startup-task', 'description': 'Explicit contract fixture', 'requirements': ['SUPERVISOR-1']}]},
        {"chapter_id": "shutdown", "title": "Shutdown", "requirements": ["SUPERVISOR-2"],
         'wbs_tasks_defined': [{'id': 'shutdown-task', 'description': 'Explicit contract fixture', 'requirements': ['SUPERVISOR-2']}]},
    ]}
    while (node := store.claim("explicit-contract-fixture-controller")) is not None:
        if node["kind"] == "MANIFEST_GENERATOR":
            reference, output = fixture["jobs"][1], manifest
        elif node["kind"] == "CHAPTER_DRAFT":
            reference = fixture["jobs"][2 + node["payload"]["chapter_index"]]
            output = {**reference["output"], 'wbs_tasks_defined': node['payload']['wbs_tasks_defined']}
        else:
            reference = fixture["jobs"][-1]
            from pipeline_tests.test_store import synthesis_output
            output = synthesis_output(node, fixture['jobs'][-1]['output']['artifact_text'])
        route = node["route"]
        receipt = {**reference["receipt"], "output_sha256": digest(output),
            "provider":route["provider"],"requested_model":route["model"],"reported_model":route["model"],
            "requested_effort":route["reasoning_effort"],"reported_effort":route["reasoning_effort"],
            "route_reservation_id":route["reservation_id"],"route_reservation_sha256":route["reservation_sha256"],
            **{key:node[key] for key in ("job_id","workflow_id","worker_slot","attempt_id","fencing_token")}}
        store.complete(node["job_id"], node["attempt_id"], node["fencing_token"], output, receipt)
    public = public_workflow(store.workflow(identifier))
    assert all("attempt_id" not in job for job in public["jobs"])
    result = verify_smoke_workflow(public, MODELS)
    assert result["passed"] is True
    assert len(result["process_receipts"]) == 4


@pytest.mark.parametrize("policy", [None, {"model_limits":{"codex:gpt-6-astra:ultra":2}},
    {"failure_cooldowns":{"quota":600},"max_routing_seconds":3600,"max_dispatches":4},
    {"provider_limits":{"claude":{"quota_pool":"shared"},"codex":{"quota_pool":"shared"}},
     "quota_pool_limits":{"shared":3,"gemini":2}},
])
def test_independent_policy_normalization_matches_actual_pipeline_contract(policy):
    from cochem_pipeline.routing import load_routing_policy
    expected = load_routing_policy(policy)
    observed = configured_routing_policy(policy)
    assert observed == expected.as_dict()
    assert digest(observed) == expected.digest


@pytest.mark.parametrize('kind',['MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS'])
@pytest.mark.parametrize('payload',[{}, {'objective':'Unicode β safety check','requirements':['A','B']},
    {'objective':'security migration concurrency','requirements':[str(index) for index in range(12)],
     'dependencies':['one'],'chapter_count':3},
    {'objective':'security migration concurrency '+'analysis '*4500,
     'requirements':[str(index) for index in range(12)],'dependencies':list(range(4)),'chapter_count':8},
    {'objective':'A small item','scope':{'score':10,'model':'gpt-6-astra','provider':'codex'},'routing':{'score':10}},
])
def test_frozen_supervisor_scoring_matches_actual_task_content_contract(kind,payload):
    from cochem_pipeline.routing import score_task
    assert score_routing_task(kind,payload)==score_task(kind,payload)


@pytest.mark.parametrize('large,model,effort,candidate_index',[
    (False,'gpt-6.1-sol','high',0),
    (True,'gpt-6-astra','ultra',1),
])
def test_codex_effort_is_bound_to_score_band_and_missing_native_metadata_is_honest(
        large,model,effort,candidate_index):
    workflow=workflow_fixture()
    manifest=workflow['jobs'][1]
    # Complex contract payloads exercise Sol High and Astra Ultra, respectively.
    manifest['payload']={'objective':'security migration concurrency',
                         'requirements':[str(index) for index in range(12)]}
    if large:
        manifest['payload']['objective']+=' analysis'*4500
        manifest['payload']['dependencies']=list(range(4))
    manifest['routing'],manifest['route']=routing_fixture('MANIFEST_GENERATOR',manifest['payload'],candidate_index)
    assert manifest['route']['model']==model
    assert manifest['route']['reasoning_effort']==effort
    manifest['receipt'].update(requested_model=model,reported_model=model,
                               requested_effort=effort,reported_effort=None)
    result=verify_smoke_workflow(workflow,MODELS)
    assert result['unverified_effort_providers']==['codex']
    assert result['effort_identity_verified'] is False
    manifest['receipt']['reported_effort']=effort
    assert verify_smoke_workflow(workflow,MODELS)['effort_identity_verified'] is True
    manifest['receipt']['reported_effort']='ultra' if effort=='high' else 'high'
    with pytest.raises(ValueError,match='reasoning effort'):
        verify_smoke_workflow(workflow,MODELS)


def reviewed_profile_fixture(provider="claude", tier="7-9", *, workflow_id="contract-only-fixture"):
    """Synthetic protected config and receipts, never native capability evidence."""
    workflow = workflow_fixture(workflow_id)
    manifest = workflow["jobs"][1]
    manifest["payload"] = {"objective": "security"}
    if tier in ("7-9", "10"):
        manifest["payload"] = {"objective": "security migration concurrency",
                               "requirements": [str(index) for index in range(12)]}
    if tier == "10":
        manifest["payload"]["objective"] += " analysis" * 4500
        manifest["payload"]["dependencies"] = list(range(4))
    index = 2 if provider == "gemini" else 0 if tier == "10" else 1
    manifest["routing"], manifest["route"] = routing_fixture("MANIFEST_GENERATOR", manifest["payload"], index)
    route = manifest["route"]
    assert route["provider"] == provider and route["tier"] == tier
    model, effort = route["model"], route["reasoning_effort"]
    native = "max" if provider == "claude" else "HIGH"
    contract = {
        "arguments": ["--effort", native] if provider == "claude" else ["--thinking-level", native],
        "version_arguments": ["--version"], "executable_sha256": "d" * 64,
        "version": "explicit-profile-contract-fixture-v1",
        "capability_reference": "Synthetic validator fixture only; no native capability claim",
        "thinking_enabled": True if provider == "claude" else None,
        "native_metadata": {"path": ["metadata", "thinking_level"], "value": native},
    }
    providers = {name: {"model": value} for name, value in MODELS.items()}
    spec = providers[provider]
    spec["effort_contracts"] = {model + ":" + effort: contract}
    if provider == "gemini":
        spec["inference_only"] = {
            "arguments": ["--model", "{model}"],
            **{key: contract[key] for key in ("version_arguments", "executable_sha256", "version", "capability_reference")},
            "disables_tools": True, "disables_mcp": True, "disables_hooks": True,
            "disables_subagents": True, "disables_model_fallback": True}
    proof = {
        "requested_profile": effort, "model": model, "verified_profile": effort,
        **{key: contract[key] for key in ("executable_sha256", "version", "capability_reference", "native_metadata")},
        "contract_sha256": digest(contract), "native_metadata_path": contract["native_metadata"]["path"],
        "observed_native_value": native, "reported_effort": None}
    inference = {"mode": "inference-only", "provider": provider, "reasoning_effort": proof,
        "probes": [{"probe": "verify_reviewed_effort_version", "pid": 124, "exit_code": 0,
                    "stdout_sha256": "e" * 64, "stderr_sha256": "f" * 64}]}
    if provider == "claude":
        inference.update(tools=[], mcp_servers=[], hooks_disabled=True)
    else:
        inference.update(tools_disabled=True, mcp_disabled=True, hooks_disabled=True,
            subagents_disabled=True, model_fallback_disabled=True,
            **{key: spec["inference_only"][key] for key in ("executable_sha256", "version", "capability_reference")})
    manifest["receipt"].update(provider=provider, requested_model=model, reported_model=model,
        requested_effort=effort, reported_effort=None, inference_policy=inference)
    return workflow, providers, contract, proof


@pytest.mark.parametrize("provider,tier", [
    ("claude", "7-9"), ("claude", "10"), ("gemini", "4-6"), ("gemini", "7-9")])
def test_reviewed_profile_proof_verifies_without_relabeling_native_effort(provider, tier):
    workflow, providers, _, proof = reviewed_profile_fixture(provider, tier)
    result = verify_smoke_workflow(workflow, MODELS, provider_config=providers)
    assert result["effort_identity_verified"] is True
    assert result["unverified_effort_providers"] == []
    receipt = result["process_receipts"][0]
    assert receipt["effort_profile_verified"] is True
    assert receipt["reported_effort"] is None
    assert proof["observed_native_value"] in ("max", "HIGH")
    assert proof["observed_native_value"] != receipt["requested_effort"]


@pytest.mark.parametrize("missing", ["protected_config", "inference_policy", "reasoning_effort"])
def test_missing_profile_provenance_is_unverified(missing):
    workflow, providers, _, _ = reviewed_profile_fixture()
    receipt = workflow["jobs"][1]["receipt"]
    if missing == "inference_policy":
        receipt.pop("inference_policy")
    elif missing == "reasoning_effort":
        receipt["inference_policy"].pop("reasoning_effort")
    result = verify_smoke_workflow(workflow, MODELS,
        provider_config=None if missing == "protected_config" else providers)
    assert result["effort_identity_verified"] is False
    assert result["unverified_effort_providers"] == ["claude"]


def test_raw_extended_string_without_protected_proof_cannot_verify_profile():
    workflow, providers, _, _ = reviewed_profile_fixture()
    receipt = workflow["jobs"][1]["receipt"]
    receipt.pop("inference_policy")
    receipt["reported_effort"] = "extended"
    result = verify_smoke_workflow(workflow, MODELS, provider_config=providers)
    assert result["effort_identity_verified"] is False


@pytest.mark.parametrize("provider", ["claude", "gemini"])
def test_reviewed_cli_without_native_effort_metadata_stays_unverified(provider):
    workflow, providers, contract, proof = reviewed_profile_fixture(provider)
    contract["native_metadata"] = None
    proof.update(contract_sha256=digest(contract), native_metadata=None,
        verified_profile=None, native_metadata_path=None, observed_native_value=None,
        observation_status="not_reported_by_reviewed_cli")
    result = verify_smoke_workflow(workflow, MODELS, provider_config=providers)
    assert result["passed"] is True
    assert result["effort_identity_verified"] is False
    assert result["unverified_effort_providers"] == [provider]
    # A valid invocation binding cannot justify an invented native observation.
    proof["verified_profile"] = proof["requested_profile"]
    with pytest.raises(ValueError, match="profile proof"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize("field,value", [
    ("requested_profile", "high"), ("verified_profile", "high"), ("model", "different-model"),
    ("executable_sha256", "0" * 64), ("version", "changed-binary-version"),
    ("capability_reference", "different-provenance"), ("contract_sha256", "0" * 64),
    ("native_metadata", {"path": ["metadata", "thinking_level"], "value": "high"}),
    ("native_metadata_path", ["result", "thinking_level"]), ("observed_native_value", "high"),
    ("reported_effort", "extended"), ("unreviewed_field", True),
])
def test_changed_profile_proof_cannot_pass(field, value):
    workflow, providers, _, proof = reviewed_profile_fixture()
    proof[field] = value
    with pytest.raises(ValueError, match="profile proof"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize("mutation", [
    lambda spec: spec.pop("effort_contracts"),
    lambda spec: spec["effort_contracts"].clear(),
    lambda spec: next(iter(spec["effort_contracts"].values())).update(version="new-reviewed-version"),
    lambda spec: next(iter(spec["effort_contracts"].values())).update(executable_sha256="0" * 64),
])
def test_old_profile_proof_cannot_verify_against_changed_protected_config(mutation):
    workflow, providers, _, _ = reviewed_profile_fixture()
    mutation(providers["claude"])
    with pytest.raises(ValueError, match="profile"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize("provider,field,value", [
    ("claude", "arguments", ["--model", "different-model"]),
    ("claude", "arguments", ["--effort", "extended"]),
    ("claude", "thinking_enabled", False),
    ("claude", "version_arguments", ["--prompt", "paid call"]),
    ("claude", "native_metadata", {"path": ["result", "thinking_level"], "value": "max"}),
    ("claude", "native_metadata", {"path": ["metadata", "content", "thinking_level"], "value": "max"}),
    ("claude", "native_metadata", {"path": [], "value": "max"}),
    ("claude", "native_metadata", {"path": ["metadata"], "value": {"self_asserted": True}}),
    ("gemini", "arguments", ["--approval-mode", "yolo"]),
    ("gemini", "arguments", ["--thinking-budget", "131073"]),
    ("gemini", "arguments", ["--thinking-level", "extended"]),
    ("gemini", "thinking_enabled", True),
])
def test_self_consistent_hash_does_not_authorize_unsafe_profile_contract(provider, field, value):
    workflow, providers, contract, proof = reviewed_profile_fixture(provider)
    contract[field] = value
    proof["contract_sha256"] = digest(contract)
    with pytest.raises(ValueError, match="profile"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize('arguments,metadata',[
    (['--thinking-level','low'],{'path':['thinking_level'],'value':'low'}),
    (['--thinking-level','medium'],{'path':['thinking_level'],'value':'medium'}),
    (['--thinking-budget','1024'],{'path':['thinking_level'],'value':'high'}),
    (['--thinking-level','HIGH'],{'path':['thinking_enabled'],'value':True}),
    (['--thinking-level','HIGH'],{'path':['metadata','enabled'],'value':'HIGH'}),
    (['--thinking-level','HIGH'],{'path':['thinking_level'],'value':'low'}),
])
def test_protected_high_profile_cannot_claim_weaker_or_unrelated_native_thinking(arguments,metadata):
    workflow, providers, contract, proof = reviewed_profile_fixture('gemini')
    contract.update(arguments=arguments,native_metadata=metadata)
    proof.update(contract_sha256=digest(contract),native_metadata=metadata,
        native_metadata_path=metadata['path'],observed_native_value=metadata['value'])
    with pytest.raises(ValueError,match='High reasoning profile'):
        verify_smoke_workflow(workflow,MODELS,provider_config=providers)


@pytest.mark.parametrize('tier',['4-6','7-9'])
def test_reviewed_profile_cannot_disable_native_thinking_with_zero_budget(tier):
    workflow, providers, contract, proof = reviewed_profile_fixture('gemini',tier)
    contract['arguments'] = ['--thinking-budget','0']
    proof['contract_sha256'] = digest(contract)
    with pytest.raises(ValueError,match='thinking-only selector'):
        verify_smoke_workflow(workflow,MODELS,provider_config=providers)


@pytest.mark.parametrize("mutation", [
    lambda policy: policy.update(mode="native-tools"),
    lambda policy: policy.update(provider="gemini"),
    lambda policy: policy.update(tools=["Shell"]),
    lambda policy: policy.update(mcp_servers=["unreviewed"]),
    lambda policy: policy.update(hooks_disabled=False),
    lambda policy: policy.pop("probes"),
    lambda policy: policy["probes"][0].update(exit_code=1),
    lambda policy: policy["probes"][0].update(pid=True),
    lambda policy: policy["probes"][0].update(stdout_sha256="not-a-digest"),
])
def test_profile_proof_requires_successful_inference_only_native_provenance(mutation):
    workflow, providers, _, _ = reviewed_profile_fixture()
    mutation(workflow["jobs"][1]["receipt"]["inference_policy"])
    with pytest.raises(ValueError, match="profile"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize("field", ["disables_tools", "disables_mcp", "disables_hooks",
                                    "disables_subagents", "disables_model_fallback"])
def test_agy_profile_requires_each_protected_inference_guard(field):
    workflow, providers, _, _ = reviewed_profile_fixture("gemini")
    providers["gemini"]["inference_only"][field] = False
    with pytest.raises(ValueError, match="profile"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


@pytest.mark.parametrize("field", ["tools_disabled", "mcp_disabled", "hooks_disabled",
                                    "subagents_disabled", "model_fallback_disabled"])
def test_agy_profile_requires_each_observed_inference_guard(field):
    workflow, providers, _, _ = reviewed_profile_fixture("gemini")
    workflow["jobs"][1]["receipt"]["inference_policy"].pop(field)
    with pytest.raises(ValueError, match="profile"):
        verify_smoke_workflow(workflow, MODELS, provider_config=providers)


def test_dynamic_assignment_accepts_gemini_chapter_instead_of_fixed_codex_parity():
    workflow = workflow_fixture()
    chapter = workflow["jobs"][2]
    chapter["routing"],chapter["route"] = routing_fixture("CHAPTER_DRAFT",chapter["payload"])
    chapter["receipt"].update(provider="gemini",requested_model="gemini-3.8-flash",
        reported_model="gemini-3.8-flash",requested_effort=None,reported_effort=None)
    result = verify_smoke_workflow(workflow,MODELS)
    assert result["process_receipts"][1]["provider"] == "gemini"
    assert result["process_receipts"][1]["routing"]["tier"] == "1-3"


def test_synthesis_cannot_substitute_model_outside_its_complexity_band():
    workflow = workflow_fixture()
    synthesis = workflow["jobs"][-1]
    synthesis["routing"]["candidates"][0]["model"]="gemini-3.1-pro"
    synthesis["route"]["model"]="gemini-3.1-pro"
    synthesis["receipt"].update(requested_model="gemini-3.1-pro",reported_model="gemini-3.1-pro")
    with pytest.raises(ValueError,match="candidates differ"):
        verify_smoke_workflow(workflow,MODELS)


def test_mismatched_operator_policy_cannot_be_hidden_by_self_consistent_route_digest():
    workflow = workflow_fixture()
    with pytest.raises(ValueError,match="configured policy"):
        verify_smoke_workflow(workflow,MODELS,{"backlog_threshold":2})


@pytest.mark.parametrize("mutation", [
    lambda w: w.update(status="IN_PROGRESS"),
    lambda w: w["jobs"].pop(),
    lambda w: w["jobs"][1].update(attempts=2),
    lambda w: w["jobs"][1].update(max_attempts=3),
    lambda w: w["jobs"][1].update(max_attempts=True),
    lambda w: w["jobs"][1]["routing"].update(dispatches=2),
    lambda w: w["jobs"][1]["routing"].update(max_dispatches=True),
    lambda w: w["jobs"][1]["routing"].update(max_dispatches=2),
    lambda w: w["jobs"][1]["routing"].update(policy_hash="0"*64),
    lambda w: w["jobs"][1]["route"].update(candidate_index=0),
    lambda w: w["jobs"][1]["route"].update(score=5),
    lambda w: w["jobs"][1]["receipt"].update(requested_effort="high"),
    lambda w: w["jobs"][1]["receipt"].update(reported_effort="ultra"),
    lambda w: w["jobs"][1]["payload"].update(objective="security migration concurrency"),
    lambda w: w["jobs"][1]["routing"]["score_details"]["rationale"][0].update(points=2),
    lambda w: w["jobs"][1]["receipt"].update(route_reservation_sha256="c"*64),
    lambda w: w["jobs"][1]["receipt"].update(provider="claude"),
    lambda w: w["jobs"][3]["receipt"].update(provider="codex"),
    lambda w: w["jobs"][1]["receipt"].update(subscription_verified=False),
    lambda w: w["jobs"][1]["receipt"].update(execution_kind="emulator"),
    lambda w: w["jobs"][1]["receipt"].update(pid=True),
    lambda w: w["jobs"][1]["receipt"].update(pid=0x100000000),
    lambda w: w["jobs"][1]["receipt"].update(exit_code=1),
    lambda w: w["jobs"][1]["receipt"].update(session_id=""),
    lambda w: w["jobs"][1]["receipt"].update(requested_model="other"),
    lambda w: w["jobs"][1]["receipt"].update(reported_model="other"),
    lambda w: w["jobs"][-1]["receipt"].update(reported_model=None),
    lambda w: w["jobs"][1]["receipt"].update(output_sha256="0"*64),
    lambda w: w["jobs"][1]["receipt"].update(stdout_sha256=""),
    lambda w: w["artifacts"][0].update(sha256="0"*64),
    lambda w: w["artifacts"][0].update(artifact_uri="db://foreign/chapter"),
    lambda w: w["artifacts"][0].update(job_id=["invalid"]),
    lambda w: w["artifacts"][0].update(artifact_text="Changed after acceptance"),
    lambda w: w["jobs"][-1]["output"]["chapter_hashes"].update(startup="0"*64),
])
def test_inconsistent_or_unverified_fixture_cannot_pass(mutation):
    fixture = workflow_fixture()
    mutation(fixture)
    with pytest.raises(ValueError):
        verify_smoke_workflow(fixture, MODELS)


class ContractFixtureHandler(BaseHTTPRequestHandler):
    """HTTP fixture controller; its synthetic workflow is not native evidence."""

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        state = self.server.state
        if self.headers.get("Authorization") != "Bearer " + state["token"]:
            code, value = 401, {"error": "DO-NOT-ECHO-SECRET-" + state["token"]}
        elif state.get("forced_status"):
            code, value = state["forced_status"], {"error": "DO-NOT-ECHO-SECRET-" + state["token"]}
        else:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))) if self.command == "POST" else None
            state["calls"].append((self.path, data))
            code = 200
            if self.path == "/health":
                value = dict(state["health"])
            elif self.path == "/submit":
                state["submitted"] = data
                value = workflow_fixture(data["workflow_id"])
                if state.get("profile_provider"):
                    value = reviewed_profile_fixture(state["profile_provider"], workflow_id=data["workflow_id"])[0]
                if state["pending"]:
                    value = {"workflow_id": data["workflow_id"], "status": "IN_PROGRESS"}
                if state.get("failure_error"):
                    value = {"workflow_id": data["workflow_id"], "status": "FAILED", "jobs": [
                        {"kind": "CHAPTER_DRAFT", "status": "FAILED", "error": state["failure_error"]},
                        {"kind": "SYNTHESIS", "status": "FAILED", "error": "Upstream job failed"},
                    ]}
                state["workflow"] = value
            elif self.path == "/cancel":
                value = {"status": "FAILED"}
            else:
                value = state["workflow"]
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def endpoint(tmp_path):
    token = secrets.token_urlsafe(48)
    token_path = tmp_path / "token"
    token_path.write_text(token, encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", 0), ContractFixtureHandler)
    server.state = {"token": token, "calls": [], "health": {}, "pending": False}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.state, ControllerClient(server.server_port, token_path), tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_real_loopback_authentication_does_not_echo_remote_error_secret(endpoint):
    state, client, directory = endpoint
    client.token_file.write_text("x"*48, encoding="ascii")
    with pytest.raises(RuntimeError, match="401") as error:
        client.call("/health")
    assert state["token"] not in str(error.value)


@pytest.mark.parametrize("operation", ["https://example.com/", "//remote", "/workflow/../../token", "/claim", "/health\r\nAuthorization: bad"])
def test_client_rejects_arbitrary_urls_and_control_operations(endpoint, operation):
    _, client, _ = endpoint
    with pytest.raises(ValueError):
        client.call(operation)


def test_smoke_transport_submits_exactly_once_with_retry_disabled_and_persists_safe_report(endpoint):
    state, client, directory = endpoint
    report_file = directory / "report.json"
    result = run_smoke_workflow({"pipeline_providers": MODELS}, client, report_file)
    assert result["passed"] is True  # Contract fixture only; never a live-provider claim.
    assert [path for path, _ in state["calls"]] == ["/submit"]
    assert state["submitted"]["chapter_count"] == 2
    assert state["submitted"]["max_attempts"] == 1
    assert state["submitted"]["max_dispatches"] == 1
    assert state["submitted"]["requirements"] == ["SUPERVISOR-1", "SUPERVISOR-2"]
    assert json.loads(report_file.read_text()) == result
    assert state["token"] not in report_file.read_text()


@pytest.mark.parametrize("provider", ["claude", "gemini"])
def test_smoke_transport_supplies_protected_provider_contract_for_profile_verification(endpoint, provider):
    state, client, directory = endpoint
    state["profile_provider"] = provider
    _, providers, _, _ = reviewed_profile_fixture(provider)
    result = run_smoke_workflow({"pipeline_providers": providers}, client, directory / "profile.json")
    assert result["passed"] is True
    assert result["effort_identity_verified"] is True
    assert result["process_receipts"][0]["effort_profile_verified"] is True
    assert result["process_receipts"][0]["reported_effort"] is None


def test_timed_out_real_http_workflow_is_cancelled_without_resubmission(endpoint):
    state, client, directory = endpoint
    state["pending"] = True
    result = run_smoke_workflow({"pipeline_providers": MODELS, "smoke_timeout_seconds": .03}, client, directory / "timeout.json")
    assert result["passed"] is False
    assert result["unfinished_work_cancelled"] is True
    assert result["failure_category"] == "provider"
    assert [path for path, _ in state["calls"]].count("/submit") == 1
    assert state["calls"][-1] == ("/cancel", {"workflow_id": state["submitted"]["workflow_id"]})


@pytest.mark.parametrize("error,category,repairable", [
    ("Subscription login unverified token=secret-token", "auth", False),
    ("HTTP429 quota exceeded", "quota", False),
    ("Provider service unavailable", "provider", False),
    ("No space left on device", "resource", False),
    ("Required executable not found", "configuration", False),
    ("Unspecified native failure", "configuration", False),
    ("TypeError: scheduler state is invalid", "code", True),
    ("Unknown option --headless", "compatibility", True),
])
def test_failed_smoke_preserves_actual_job_category_to_prevent_paid_retry_for_blockers(endpoint, error, category, repairable):
    state, client, directory = endpoint
    state["failure_error"] = error
    result = run_smoke_workflow({"pipeline_providers": MODELS}, client, directory / "failure.json")
    assert result["passed"] is False
    assert result["failure_category"] == category
    assert result["failure_repairable"] is repairable
    assert len(result["failure_evidence"]) == 1
    assert result["unfinished_work_cancelled"] is True
    assert "secret-token" not in json.dumps(result)


def test_unknown_controller_failure_is_a_configuration_hold(endpoint):
    state, client, directory = endpoint
    state["forced_status"] = 500
    result = run_smoke_workflow({"pipeline_providers": MODELS}, client, directory / "controller-failure.json")
    assert result["passed"] is False
    assert result["failure_category"] == "configuration"
    assert result["failure_repairable"] is False
    assert state["token"] not in json.dumps(result)


def test_lost_supervisor_ownership_prevents_submission(endpoint):
    state, client, directory = endpoint
    result = run_smoke_workflow({"pipeline_providers": MODELS}, client, directory / "lost.json", heartbeat=lambda: False)
    assert result["passed"] is False
    assert state["calls"] == []


def write_health(state, directory, *, sequence=1, started=None, source=None):
    started = time.time()-1 if started is None else started
    source = str(directory) if source is None else str(source)
    value = {"instance_id": "explicit-test-instance", "pid": 1234, "process_started_at": started,
             "sequence": sequence, "timestamp": time.time(), "source_root": source, "version": "4.2.3", "status": {}}
    state["health"] = {**value, "service_identity": "SYSTEM", "quarantined_slots": {}, "trip_errors": {}}
    path = directory / "supervisor_status.json"
    temp = directory / "heartbeat-next.json"
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path)
    return value


def test_health_requires_two_physical_heartbeat_sequence_updates_and_matching_api(endpoint):
    state, client, directory = endpoint
    started = time.time()
    write_health(state, directory, started=started)
    stop = threading.Event()

    def publish():
        sequence = 1
        while not stop.wait(.05):
            sequence += 1
            write_health(state, directory, started=started, sequence=sequence)

    writer = threading.Thread(target=publish)
    writer.start()
    try:
        assert wait_for_health(directory, client, directory, started_after=started, timeout_seconds=1)
    finally:
        stop.set()
        writer.join(timeout=2)


@pytest.mark.parametrize("failure", ["frozen", "old-process", "wrong-source", "wrong-pid", "quarantine", "trip-error"])
def test_unhealthy_or_old_release_cannot_pass_health(endpoint, failure):
    state, client, directory = endpoint
    started = time.time()
    write_health(state, directory, started=started-100 if failure == "old-process" else started,
                 source=directory / "other" if failure == "wrong-source" else directory)
    if failure == "wrong-pid":
        state["health"]["pid"] = 999
    elif failure == "quarantine":
        state["health"]["quarantined_slots"] = {"slot": "cleanup failed"}
    elif failure == "trip-error":
        state["health"]["trip_errors"] = {"job": "reaper failed"}
    assert not wait_for_health(directory, client, directory, started_after=started, timeout_seconds=.03)


def preflight_contract_fixture(identifier, candidate_index=0):
    payload={'objective':'Respond ready without external actions.'}
    routing,route=routing_fixture('PREFLIGHT_REQUEST',payload,candidate_index,{'max_routing_seconds':300})
    routing.update(max_dispatches=3,dispatches=2)
    output={'ready':True}
    root={'job_id':identifier,'workflow_id':identifier,'status':'COMPLETED','kind':'MACRO_PLANNING_REQUEST'}
    receipt={'provider':route['provider'],'requested_model':route['model'],'reported_model':route['model'],
        'requested_effort':route.get('reasoning_effort'),'route_reservation_sha256':route['reservation_sha256'],
        'execution_kind':'native_cli','subscription_verified':True,'pid':123,'exit_code':0,
        'session_id':'synthetic-native-contract-fixture-not-a-model','stdout_sha256':'a'*64,
        'output_sha256':digest(output),'process_creation_filetime':1000,
        'process_identity_source':'owned_windows_process_handle'}
    job={'job_id':identifier+'-probe','workflow_id':identifier,'parent_job_id':identifier,'kind':'PREFLIGHT_REQUEST',
        'status':'COMPLETED','payload':payload,'output':output,'output_sha256':digest(output),
        'routing':routing,'route':route,'receipt':receipt}
    return {'workflow_id':identifier,'root':root,'jobs':[root,job],'status':'COMPLETED','artifacts':[]}


def profiled_preflight_contract_fixture(identifier, provider):
    """Exercise single-task acceptance with an explicitly synthetic routed profile."""
    profile_workflow, providers, contract, proof = reviewed_profile_fixture(provider)
    reference = profile_workflow['jobs'][1]
    workflow = preflight_contract_fixture(identifier)
    job = workflow['jobs'][1]
    job['payload'] = reference['payload']
    job['routing'],job['route'] = routing_fixture('PREFLIGHT_REQUEST',job['payload'],
        reference['route']['candidate_index'],{'max_routing_seconds':300})
    job['routing'].update(max_dispatches=3,dispatches=2)
    receipt = job['receipt']
    for field in ('provider','requested_model','reported_model','requested_effort',
                  'reported_effort','inference_policy'):
        receipt[field] = reference['receipt'][field]
    receipt['route_reservation_sha256'] = job['route']['reservation_sha256']
    return workflow,providers,contract,proof


@pytest.mark.parametrize('reported,verified',[(None,False),('low',True)])
def test_single_task_preflight_reports_missing_codex_effort_honestly(reported,verified):
    from cochem_supervisor.probes import verify_acceptance_preflight
    # This is the actual lightweight score band, spilling over to Luna Low.
    workflow = preflight_contract_fixture('codex-preflight-contract',candidate_index=2)
    receipt = workflow['jobs'][1]['receipt']
    assert receipt['requested_model'] == 'gpt-6-luna' and receipt['requested_effort'] == 'low'
    receipt['reported_effort'] = reported
    result = verify_acceptance_preflight(workflow)
    assert result['passed'] is True
    assert result['effort_identity_verified'] is verified
    assert result['unverified_effort_providers'] == ([] if verified else ['codex'])
    assert result['receipt']['reported_effort'] == reported
    receipt['reported_effort'] = 'high'
    with pytest.raises(ValueError,match='reasoning effort'):
        verify_acceptance_preflight(workflow)


@pytest.mark.parametrize('configured_deadline,expected_deadline',[(0,300),(120,120),(600,300)])
def test_real_preflight_store_projection_verifies_its_independently_bounded_policy(
        tmp_path,configured_deadline,expected_deadline):
    # Actual SQLite capture/claim/completion/public projection; process metadata
    # is an explicit receipt fixture and does not establish native execution.
    from cochem_pipeline.routing import load_routing_policy
    from cochem_pipeline.service import public_workflow
    from cochem_pipeline.store import JobStore
    from cochem_supervisor.probes import verify_acceptance_preflight
    configured = {'max_routing_seconds':configured_deadline}
    store = JobStore(tmp_path/'real-preflight.db',routing_policy=load_routing_policy(configured))
    workflow = store.submit_preflight()
    node = store.claim('explicit-storage-contract-fixture',worker_slot='slot1')
    assert node['routing']['policy']['max_routing_seconds'] == expected_deadline
    route = node['route']
    output = {'ready':True}
    receipt = {
        'provider':route['provider'],'requested_model':route['model'],'reported_model':route['model'],
        'requested_effort':route['reasoning_effort'],'reported_effort':None,
        'route_reservation_id':route['reservation_id'],'route_reservation_sha256':route['reservation_sha256'],
        'selected_route':route,'execution_kind':'native_cli','subscription_verified':True,
        'pid':123,'exit_code':0,'session_id':'explicit-storage-receipt-fixture-not-native-execution',
        'stdout_sha256':'a'*64,'output_sha256':digest(output),'process_creation_filetime':1000,
        'process_identity_source':'owned_windows_process_handle',
        **{key:node[key] for key in ('job_id','workflow_id','worker_slot','attempt_id','fencing_token')}}
    store.complete(node['job_id'],node['attempt_id'],node['fencing_token'],output,receipt)
    public = public_workflow(store.workflow(workflow['workflow_id']))
    assert verify_acceptance_preflight(public,configured)['passed'] is True
    assert configured == {'max_routing_seconds':configured_deadline}
    # Even an internally self-consistent digest cannot lengthen the independent
    # acceptance deadline beyond the protected policy's bounded value.
    child = next(job for job in public['jobs'] if job['kind']=='PREFLIGHT_REQUEST')
    child['routing']['policy']['max_routing_seconds'] = expected_deadline+1
    altered_hash = digest(child['routing']['policy'])
    child['routing']['policy_hash'] = child['route']['policy_digest'] = altered_hash
    with pytest.raises(ValueError,match='configured policy'):
        verify_acceptance_preflight(public,configured)


@pytest.mark.parametrize('provider',['claude','gemini'])
@pytest.mark.parametrize('evidence',['observed','no_config','missing_proof','not_reported','changed_proof'])
def test_current_preflight_transport_verifies_protected_profile_or_records_limit(endpoint,provider,evidence):
    from cochem_supervisor.probes import run_acceptance_preflight
    state,client,directory = endpoint
    transaction = 'c'*32
    workflow,providers,contract,proof = profiled_preflight_contract_fixture('repair-smoke-'+transaction,provider)
    config = {'pipeline_providers':providers}
    if evidence == 'no_config':
        config = {}
    elif evidence == 'missing_proof':
        workflow['jobs'][1]['receipt']['inference_policy'].pop('reasoning_effort')
    elif evidence == 'not_reported':
        contract['native_metadata'] = None
        proof.update(contract_sha256=digest(contract),native_metadata=None,verified_profile=None,
            native_metadata_path=None,observed_native_value=None,observation_status='not_reported_by_reviewed_cli')
    elif evidence == 'changed_proof':
        proof['contract_sha256'] = '0'*64
    state['workflow'] = workflow
    report = directory/'profile-preflight.json'
    result = run_acceptance_preflight(config,client,report,transaction)
    assert [path for path,_ in state['calls']] == ['/preflight']
    assert json.loads(report.read_text()) == result
    if evidence == 'changed_proof':
        assert result['passed'] is False
        assert result['failure_category'] == 'compatibility'
        return
    assert result['passed'] is True
    verified = evidence == 'observed'
    assert result['effort_identity_verified'] is verified
    assert result['unverified_effort_providers'] == ([] if verified else [provider])
    assert result['receipt']['effort_profile_verified'] is verified
    assert result['receipt']['reported_effort'] is None
    assert result['receipt']['requested_effort'] == workflow['jobs'][1]['route']['reasoning_effort']
    # Validating again after restart must not launch or refund another inference.
    resumed = run_acceptance_preflight(config,client,report,transaction)
    assert resumed['effort_identity_verified'] is verified
    assert resumed['native_dispatches'] == result['native_dispatches'] == 2
    assert [path for path,_ in state['calls']] == ['/preflight','/workflow/'+workflow['workflow_id']]


def test_live_preflight_restart_reuses_one_durable_workflow_and_spent_dispatches(endpoint):
    from cochem_supervisor.probes import run_acceptance_preflight
    state,client,directory=endpoint
    transaction='a'*32;identifier='repair-smoke-'+transaction
    state['workflow']=preflight_contract_fixture(identifier)
    report=directory/'smoke.json'
    first=run_acceptance_preflight({},client,report,transaction)
    second=run_acceptance_preflight({},client,report,transaction)
    assert first['passed'] is second['passed'] is True  # Synthetic contract data, never actual native execution.
    assert first['native_dispatches']==second['native_dispatches']==2
    assert first['deadline_at']==second['deadline_at']
    assert [path for path,_ in state['calls']]==['/preflight','/workflow/'+identifier]
    # A missing restored DB workflow cannot trigger recreation/refund.
    state['forced_status']=404
    failed=run_acceptance_preflight({},client,report,transaction)
    assert failed['passed'] is False
    assert sum(path=='/preflight' for path,_ in state['calls'])==1


def test_live_preflight_rejects_unverified_model_handoff(endpoint):
    from cochem_supervisor.probes import run_acceptance_preflight
    state,client,directory=endpoint
    transaction='b'*32
    state['workflow']=preflight_contract_fixture('repair-smoke-'+transaction)
    state['workflow']['jobs'][1]['receipt']['subscription_verified']=False
    result=run_acceptance_preflight({},client,directory/'smoke.json',transaction)
    assert result['passed'] is False and result['failure_category']=='compatibility'
