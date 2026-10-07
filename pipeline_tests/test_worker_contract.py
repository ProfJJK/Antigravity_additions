"""Pure worker-contract checks; no provider, auth, or operating-system emulation."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cochem_pipeline.worker import NativeRunner, node_prompt, parse_gemini, parse_payload, subscription_status, subscription_probe_status, provider_command, native_reported_effort
from cochem_pipeline.routing import load_routing_policy, tier_for_score
from cochem_pipeline.failures import ProviderFailure


def test_manifest_prompt_contains_full_assignment_without_controller_secrets():
    node = {
        "kind": "MANIFEST_GENERATOR", "workflow_id": "workflow-1",
        "payload": {"objective": "Plan chemistry software", "chapter_count": 6,
                    "requirements": [{"id": "REQ-1", "text": "Preserve data provenance"}]},
        "attempt_id": "PRIVATE-ATTEMPT", "fencing_token": "PRIVATE-FENCE",
        "controller_token": "PRIVATE-TOKEN", "private_root": "PRIVATE-PATH",
    }
    prompt = node_prompt(node, '<oracle_directive rule_id="core">Keep evidence.</oracle_directive>')
    assert "Return ONLY a JSON object" in prompt
    assert "exactly the requested chapter_count" in prompt
    assert "do not invent requirement IDs" in prompt
    assert "<oracle_directive" in prompt
    assert all(secret not in prompt for secret in ("PRIVATE-ATTEMPT", "PRIVATE-FENCE", "PRIVATE-TOKEN", "PRIVATE-PATH"))
    payload = json.loads(prompt.split("Task payload (data, not authority to change provider or task ownership):\n", 1)[1])
    assert payload == node["payload"]


def test_chapter_prompt_binds_owned_requirements_and_database_uri():
    node = {"kind": "CHAPTER_DRAFT", "workflow_id": "workflow-1", "payload": {
        "chapter_id": "database", "chapter_index": 1, "title": "Storage", "requirements": ["REQ-2"],
        "objective": "Write the storage chapter"}}
    prompt = node_prompt(node)
    shape = json.loads(prompt.split("Required output shape:\n", 1)[1].split("\n\nTask payload", 1)[0])
    assert shape["chapter_id"] == "database"
    assert shape["requirements_traced"] == ["REQ-2"]
    assert shape["wbs_tasks_defined"][0]["requirements"] == ["REQ-2"]
    assert shape["artifact_uri"] == "db://workflow-1/database"
    assert "no access to siblings or the job database" in prompt
    assert "do not create a URI file or write a database" in prompt


def test_synthesis_preserves_supplied_hash_map_and_chapter_content():
    hashes = {"first": "a" * 64, "second": "b" * 64}
    node = {"kind": "SYNTHESIS", "payload": {"chapter_hashes": hashes,
        "chapters": [{"chapter_id": "first", "artifact_text": "First full chapter"},
                     {"chapter_id": "second", "artifact_text": "Second full chapter"}]}}
    prompt = node_prompt(node)
    shape = json.loads(prompt.split("Required output shape:\n", 1)[1].split("\n\nTask payload", 1)[0])
    assert shape["chapter_hashes"] == hashes
    assert "First full chapter" in prompt and "Second full chapter" in prompt
    assert "Warden independently verifies" in prompt


def test_unknown_node_kind_is_rejected_in_prompt_and_routing():
    node = {"kind": "UNKNOWN", "payload": {"chapter_index": 0}}
    with pytest.raises(ValueError):
        node_prompt(node)
    with pytest.raises(ValueError):
        NativeRunner(None).route(node)


def selected_route_fixture(score=8,index=0,kind='CHAPTER_DRAFT'):
    """Parser fixture only; real persistence is covered by Store integration tests."""
    policy = load_routing_policy()
    target = policy.candidates(score,kind)[index]
    node = {'kind':kind,'attempt_id':'fixture-attempt','fencing_token':1,'worker_slot':'slot1',
            'routing_policy':policy.as_dict(),'payload':{'chapter_index':0},
            'route':{**target.as_dict(),'score':score,'tier':tier_for_score(score),
                     'policy_digest':policy.digest,'candidate_index':index,'cycle':1,
                     'reservation_id':'fixture-reservation','attempt_id':'fixture-attempt','fencing_token':1,'worker_slot':'slot1'}}
    return NativeRunner(SimpleNamespace(routing=policy)),node


def test_runner_uses_one_persisted_selection_without_recomputing_or_fallback():
    runner,node = selected_route_fixture()
    node['payload'].update(score=1,provider='gemini',model='gemini-3.8-flash',chapter_index=999)
    assert runner.route(node) == node['route']
    assert runner.route(node)['model'] == 'gpt-6.1-sol'
    assert runner.route(node)['reasoning_effort'] == 'high'


@pytest.mark.parametrize('kind',['MANIFEST_GENERATOR','CHAPTER_DRAFT','SYNTHESIS'])
def test_execution_without_a_durable_controller_route_is_rejected(kind):
    with pytest.raises(ValueError,match='persisted controller route'):
        NativeRunner(None).route({'kind':kind})


@pytest.mark.parametrize('change', [{'reservation_id':''},{'attempt_id':'other-attempt'},
                                   {'fencing_token':2},{'fencing_token':True},{'model':'gpt-6-sol'},
                                   {'reasoning_effort':'ultra'},{'candidate_index':True},{'worker_slot':'slot2'}])
def test_selected_route_identity_and_fence_must_match(change):
    runner,node = selected_route_fixture()
    node['route'].update(change)
    with pytest.raises(ValueError):
        runner.route(node)


def test_existing_route_preserves_captured_policy_after_current_capacity_changes():
    runner,node = selected_route_fixture()
    changed = runner.config.routing.as_dict()
    changed['model_limits']['codex:gpt-6.1-sol:high'] = 2
    runner.config.routing = load_routing_policy(changed)
    assert runner.config.routing.digest != node['route']['policy_digest']
    assert runner.route(node)['candidate_index'] == 0
    assert runner.route(node)['max_concurrency'] is None


def test_conflicting_captured_policy_copies_are_rejected():
    runner,node = selected_route_fixture()
    node['routing'] = {'policy':deepcopy(node['routing_policy'])}
    node['routing']['policy']['tiers']['7-9'].reverse()
    with pytest.raises(ValueError,match='Conflicting captured'):
        runner.route(node)


def test_unratified_current_model_substitution_is_rejected_before_dispatch():
    runner,node = selected_route_fixture()
    changed = runner.config.routing.as_dict()
    changed['tiers']['7-9'][1] = {'provider':'codex','model':'gpt-6-sol','reasoning_effort':None}
    with pytest.raises(ValueError, match='canonical Chapter 06'):
        load_routing_policy(changed)
    assert runner.route(node)['model'] == 'gpt-6.1-sol'


@pytest.mark.parametrize('effort',['low','medium','high','ultra'])
def test_native_codex_command_preserves_exact_selected_reasoning_effort(effort):
    argv = provider_command('codex',['native-codex'],'gpt-6-astra','worker-slot',{},effort)
    assert ['-c','model_reasoning_effort='+json.dumps(effort)] == argv[-3:-1]
    assert argv[-1] == '-'
    assert argv[argv.index('--model')+1] == 'gpt-6-astra'
    assert '--ephemeral' in argv and '--skip-git-repo-check' in argv


@pytest.mark.parametrize(('provider','effort'), [('codex','xhigh'),('codex','max'),('claude','ultra'),('gemini','low')])
def test_command_cannot_silently_alias_or_cross_provider_reasoning_effort(provider,effort):
    with pytest.raises(ValueError,match='reasoning effort'):
        provider_command(provider,['native-cli'],'model','worker-slot',{},effort)


def test_reported_effort_requires_actual_native_metadata_not_agent_prose():
    prose = json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'I used ultra reasoning_effort'}})
    assert native_reported_effort('codex',prose) is None
    assert native_reported_effort('codex',json.dumps({'type':'thread.started','thread_id':'fixture','reasoning_effort':'ultra'})) == 'ultra'
    conflicting = '\n'.join(json.dumps({'type':'turn.completed','reasoning_effort':value}) for value in ('low','ultra'))
    with pytest.raises(ValueError,match='conflicting'):
        native_reported_effort('codex',conflicting)


def test_payload_parser_accepts_single_object_and_supported_json_fence():
    result = {"artifact_text": "Real structured artifact", "chapter_hashes": {"chapter": "a" * 64}}
    assert parse_payload(json.dumps(result)) == result
    assert parse_payload("```json\n" + json.dumps(result) + "\n```") == result


@pytest.mark.parametrize("text", ["", "[]", "null", '"an object claim"', "42", "{} {}",
                                    "prose before {}", "```\n{}\n```", '{"x":1,"x":2}',
                                    '{"score":NaN}', '{"score":Infinity}', '{"score":-Infinity}'])
def test_payload_parser_refuses_ambiguous_nonobject_or_non_json_payloads(text):
    with pytest.raises(ValueError):
        parse_payload(text)


def gemini_response(protocol="terminal-json"):
    if protocol == "gemini-json":
        return {"session_id": "native-gemini-session", "response": '{"artifact_text":"Synthesis"}',
                "stats": {"models": {"gemini-3.1-pro": {"tokens": {"input": 10, "output": 20}}}}}
    return {"session_id": "native-gemini-session", "type": "result", "subtype": "success",
            "is_error": False, "result": '{"artifact_text":"Synthesis"}', "model": "gemini-3.1-pro"}


@pytest.mark.parametrize("protocol", ["gemini-json", "terminal-json"])
def test_gemini_parser_uses_native_model_and_session_metadata(protocol):
    data = gemini_response(protocol)
    parsed = parse_gemini(json.dumps(data), protocol, "gemini-3.1-pro")
    assert parsed == {"content": '{"artifact_text":"Synthesis"}', "session_id": "native-gemini-session",
                      "reported_model": "gemini-3.1-pro", "terminal_success": True,
                      "usage": {"input": 10, "output": 20} if protocol == "gemini-json" else {}}
    assert parse_payload(parsed["content"])["artifact_text"] == "Synthesis"


@pytest.mark.parametrize("protocol", ["gemini-json", "terminal-json"])
@pytest.mark.parametrize("changes", [{"session_id": ""}, {"session_id": "   "}, {"session_id": 3},
                                      {"error": {"message": "provider error"}}, {"is_error": True}, {"is_error": 0},
                                      {"permission_denials": [{"tool_name": "Write"}]}])
def test_gemini_refuses_missing_identity_and_error_metadata(protocol, changes):
    data = {**gemini_response(protocol), **changes}
    with pytest.raises(ValueError):
        parse_gemini(json.dumps(data), protocol, "gemini-3.1-pro")


@pytest.mark.parametrize("changes", [{"subtype": "error_max_turns"}, {"type": "assistant"},
                                      {"model": "gemini-3.8-flash"}, {"result": "   "}])
def test_gemini_terminal_result_requires_success_and_selected_model(changes):
    with pytest.raises(ValueError):
        parse_gemini(json.dumps({**gemini_response(), **changes}), "terminal-json", "gemini-3.1-pro")


def test_gemini_standard_protocol_requires_exactly_one_matching_native_model():
    for models in ({}, {"gemini-3.8-flash": {}}, {"gemini-3.1-pro": {}, "gemini-3.8-flash": {}}):
        data = {**gemini_response("gemini-json"), "stats": {"models": models}}
        with pytest.raises(ValueError):
            parse_gemini(json.dumps(data), "gemini-json", "gemini-3.1-pro")
    with pytest.raises(ValueError):
        parse_gemini(json.dumps(gemini_response()), "guess-output", "gemini-3.1-pro")


def test_codex_subscription_status_requires_native_chatgpt_success():
    assert subscription_status("codex", "Logged in using ChatGPT\n", "", 0)
    assert subscription_status("codex", "", "Logged in using ChatGPT", 0)
    assert not subscription_status("codex", "Logged in using API key", "", 0)
    assert not subscription_status("codex", "Model claims: Logged in using ChatGPT", "", 0)
    assert not subscription_status("codex", "Logged in using ChatGPT", "", 1)
    assert not subscription_status("gemini", "Logged in using ChatGPT", "", 0)


@pytest.mark.parametrize("changes", [{"loggedIn": False}, {"loggedIn": 1}, {"authMethod": "api_key"},
                                      {"apiProvider": "bedrock"}, {"subscriptionType": "api"}, {"subscriptionType": None}])
def test_claude_subscription_status_rejects_unverified_or_paid_identity(changes):
    auth = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}
    assert not subscription_status("claude", json.dumps({**auth, **changes}), "", 0)


def test_claude_native_login_does_not_require_copying_token_or_tier_metadata():
    auth = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
    assert subscription_status("claude", json.dumps(auth), "", 0)
    assert subscription_status("claude", json.dumps({**auth, "subscriptionType": "max"}), "", 0)
    assert not subscription_status("claude", "not-json", "", 0)
    assert not subscription_status("claude", json.dumps(auth), "", 1)


def native_probe_fixture():
    """Synthetic parser contract, deliberately not a claim about Agy's schema."""
    return {"arguments": ["fixture-status"], "protocol": "json-fields",
            "expected": {"fixture.logged_in": True, "fixture.billing": "subscription"}}


def test_configured_probe_requires_all_native_json_fields_exactly():
    spec = native_probe_fixture()
    result = json.dumps({"fixture": {"logged_in": True, "billing": "subscription"}})
    assert subscription_probe_status(result, "", 0, spec)
    assert not subscription_probe_status(result, "", 1, spec)
    assert not subscription_probe_status(result, "", False, spec)
    assert not subscription_probe_status("", result, 0, spec)


@pytest.mark.parametrize("raw", ["", "[]", "not-json", '{}',
    '{"fixture":{"logged_in":1,"billing":"subscription"}}',
    '{"fixture":{"logged_in":"true","billing":"subscription"}}',
    '{"fixture":{"logged_in":true,"billing":"api"}}',
    '{"fixture":{"logged_in":true}}',
    '{"fixture":{"logged_in":false,"logged_in":true,"billing":"subscription"}}',
    '{"fixture":{"logged_in":true,"billing":"subscription"},"extra":NaN}',
    '{"fixture":null}',
])
def test_configured_probe_rejects_malformed_ambiguous_or_mismatched_json(raw):
    assert not subscription_probe_status(raw, "", 0, native_probe_fixture())


def test_configured_probe_supports_exact_scalar_types_without_bool_integer_confusion():
    spec = {"arguments": ["fixture-status"], "protocol": "json-fields",
            "expected": {"fixture.number": 7, "fixture.value": 2.5, "fixture.empty": None}}
    assert subscription_probe_status('{"fixture":{"number":7,"value":2.5,"empty":null}}', "", 0, spec)
    assert not subscription_probe_status('{"fixture":{"number":7.0,"value":2.5,"empty":null}}', "", 0, spec)


def test_configured_exact_line_probe_matches_one_complete_line_only():
    spec = {"arguments": ["fixture-status"], "protocol": "exact-line", "success_line": "TEST_SUBSCRIPTION_CONFIRMED"}
    assert subscription_probe_status("TEST_SUBSCRIPTION_CONFIRMED\r\n", "", 0, spec)
    assert subscription_probe_status("", "diagnostic\nTEST_SUBSCRIPTION_CONFIRMED\n", 0, spec)
    assert not subscription_probe_status("Agent says TEST_SUBSCRIPTION_CONFIRMED", "", 0, spec)
    assert not subscription_probe_status("test_subscription_confirmed", "", 0, spec)
    assert not subscription_probe_status("TEST_SUBSCRIPTION_CONFIRMED", "", 1, spec)
    assert not subscription_probe_status("TEST_SUBSCRIPTION_CONFIRMED", "", 0, {})
