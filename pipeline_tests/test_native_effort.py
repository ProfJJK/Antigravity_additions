"""Native effort parser/argv fixtures and real binary identity checks.

These tests do not claim a vendor supports the fixture metadata or that paid
inference ran. They verify that unreviewed profiles never acquire such proof.
"""
import hashlib
import json

import pytest

from cochem_pipeline.failures import ProviderFailure
from cochem_pipeline.native_effort import (effort_contract, effort_binary_digest,
    effort_version_evidence, reported_profile)
from cochem_pipeline.worker import NativeRunner, provider_command
from cochem_supervisor.runner import repair_command, RepairRunner, NativeRepairProtocolError


def reviewed_fixture(tmp_path, provider='claude', effort='extended'):
    binary = tmp_path / f'{provider}.exe'
    binary.write_bytes(b'explicit reviewed contract binary fixture, not executable')
    model = 'claude-opus-5-5' if provider == 'claude' else 'gemini-3.1-pro-preview'
    contract = {'arguments': ['--effort', 'max'] if provider == 'claude' else ['--thinking-level', 'HIGH'],
        'version_arguments': ['--version'], 'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'version': 'native-version-fixture', 'capability_reference': 'Protocol fixture, not native vendor evidence',
        'thinking_enabled': True if provider == 'claude' else None,
        'native_metadata': {'path': ['reasoning_effort'], 'value': 'max' if provider == 'claude' else 'HIGH'}}
    spec = {'provider': provider, 'model': model, 'reasoning_effort': effort,
            'executable': str(binary), 'effort_contracts': {f'{model}:{effort}': contract}}
    if provider == 'gemini':
        spec.update(arguments=['--fixture-infer', '{model}'], protocol='terminal-json',
            subscription_probe={'arguments': ['fixture-auth'], 'protocol': 'exact-line', 'success_line': 'fixture-authenticated'})
        spec['inference_only'] = {key: contract[key] for key in
            ('version_arguments', 'version', 'executable_sha256', 'capability_reference')}
        spec['inference_only'].update(arguments=['--fixture-infer-no-tools', '{model}'],
            disables_tools=True, disables_mcp=True, disables_hooks=True,
            disables_subagents=True, disables_model_fallback=True)
    return model, spec, contract


@pytest.mark.parametrize(('provider', 'model', 'effort'), [
    ('claude', 'claude-opus-5-5', 'extended'), ('claude', 'claude-fable-5-1', 'extended'),
    ('gemini', 'gemini-3.8-flash', 'extended'), ('gemini', 'gemini-3.1-pro-preview', 'high')])
def test_unreviewed_profiles_hold_before_any_native_probe_or_authentication(provider, model, effort):
    # There is no launchable executable or Windows identity in this fixture.
    # An early compatibility rejection proves those paths cannot be reached.
    with pytest.raises(ProviderFailure) as caught:
        NativeRunner(None)._inference_preflight({'route': {'model': model}}, provider,
            ['must-not-launch'], {}, None, None, lambda: True, None, {}, effort)
    assert caught.value.category == 'compatibility'
    assert caught.value.hold_scope == 'model'
    with pytest.raises(ValueError, match='reasoning effort'):
        provider_command(provider, ['must-not-launch'], model, 'workspace', {}, effort, inference_only=True)


def test_reviewed_claude_profile_preserves_all_host_gates_and_actual_native_effort(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path)
    argv = provider_command('claude', [spec['executable']], model, 'workspace', spec,
                            'extended', inference_only=True)
    assert argv[argv.index('--effort') + 1] == 'max'
    assert argv[argv.index('--model') + 1] == model
    assert argv[argv.index('--tools') + 1] == ''
    settings = json.loads(argv[argv.index('--settings') + 1])
    assert settings == {'disableAllHooks': True, 'disableSkillShellExecution': True,
        'alwaysThinkingEnabled': True, 'fallbackModel': [], 'switchModelsOnFlag': False}
    assert '--strict-mcp-config' in argv and '--disable-slash-commands' in argv
    assert repair_command(spec, [spec['executable']], 'workspace') == argv
    observation = reported_profile('claude', json.dumps({'type': 'result', 'reasoning_effort': 'max',
        'result': 'I claim to use extended'}), model=model, effort='extended', spec=spec)
    assert observation['observed_native_value'] == 'max'
    assert observation['verified_profile'] == 'extended'
    assert observation['reported_effort'] is None  # Never rename native max to extended.


@pytest.mark.parametrize('effort', ['extended', 'high'])
def test_agy_profile_binds_same_reviewed_binary_and_keeps_real_metadata(tmp_path, effort):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', effort)
    argv = provider_command('gemini', [spec['executable']], model, 'workspace', spec,
                            effort, inference_only=True)
    assert argv[-2:] == ['--thinking-level', 'HIGH']
    assert argv.count(model) == 1
    observation = reported_profile('gemini', json.dumps({'type': 'result', 'reasoning_effort': 'HIGH'}),
        model=model, effort=effort, spec=spec)
    assert observation['observed_native_value'] == 'HIGH'
    assert observation['reported_effort'] is None
    spec['inference_only']['version'] = 'changed-native-version'
    with pytest.raises(ValueError, match='same native binary'):
        effort_contract('gemini', model, effort, spec)


@pytest.mark.parametrize('arguments', [
    ['--thinking-level', 'minimal'], ['--thinking-level', 'low'], ['--thinking-level', 'MEDIUM'],
    ['--thinking-budget', '0'], ['--thinking-budget', '16384'], ['--thinking-budget', '131072'],
])
def test_agy_high_cannot_be_redefined_as_lower_effort_or_an_assumed_budget_equivalent(tmp_path, arguments):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'high')
    contract['arguments'] = arguments
    with pytest.raises(ValueError):
        effort_contract('gemini', model, 'high', spec)
    with pytest.raises(ValueError):
        provider_command('gemini', [spec['executable']], model, 'workspace', spec, 'high', inference_only=True)


@pytest.mark.parametrize('native', ['low', 'medium', 'high', 'xhigh', 'max'])
def test_reviewed_agy_effort_selector_remains_a_conditional_binding(tmp_path, native):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'extended')
    contract['arguments'] = ['--effort', native]
    contract['native_metadata'] = None
    assert effort_contract('gemini', model, 'extended', spec) is contract
    from cochem_supervisor.probes import _reviewed_effort_contract
    assert _reviewed_effort_contract('gemini', model, 'extended', spec) is contract
    observation = reported_profile('gemini', '{}', model=model, effort='extended', spec=spec)
    assert observation['verified_profile'] is None
    assert observation['reported_effort'] is None
    contract['version'] = 'different-native-version'
    with pytest.raises(ValueError, match='same native binary'):
        effort_contract('gemini', model, 'extended', spec)


@pytest.mark.parametrize('native', ['low', 'medium', 'high', 'xhigh', 'max'])
def test_agy_high_effort_selector_cannot_alias_even_a_higher_label(tmp_path, native):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'high')
    contract['arguments'] = ['--effort', native]
    contract['native_metadata'] = None
    from cochem_supervisor.probes import _reviewed_effort_contract
    if native == 'high':
        assert effort_contract('gemini', model, 'high', spec) is contract
        assert _reviewed_effort_contract('gemini', model, 'high', spec) is contract
    else:
        with pytest.raises(ValueError):
            effort_contract('gemini', model, 'high', spec)
        with pytest.raises(ValueError):
            _reviewed_effort_contract('gemini', model, 'high', spec)


def test_agy_stream_cannot_invent_effort_metadata_binding(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'extended')
    spec['protocol'] = 'agy-stream-json'
    contract['arguments'] = ['--effort', 'high']
    with pytest.raises(ValueError, match='no verified native effort'):
        effort_contract('gemini', model, 'extended', spec)
    from cochem_supervisor.probes import _reviewed_effort_contract
    with pytest.raises(ValueError, match='no verified native effort'):
        _reviewed_effort_contract('gemini', model, 'extended', spec)


@pytest.mark.parametrize('metadata', [
    {'path':['thinking_level'], 'value':'low'},
    {'path':['reasoning_effort'], 'value':'extended'},
    {'path':['thinking_enabled'], 'value':True},
    {'path':['metadata', 'unrelated_field'], 'value':'HIGH'},
])
def test_agy_high_cannot_be_attested_by_lower_or_unrelated_native_metadata(tmp_path, metadata):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'high')
    contract['native_metadata'] = metadata
    with pytest.raises(ValueError, match='native high effort level'):
        effort_contract('gemini', model, 'high', spec)
    with pytest.raises(ValueError, match='native high effort level'):
        reported_profile('gemini', json.dumps({'type':'result', 'thinking_level':'low'}),
                         model=model, effort='high', spec=spec)


def test_agy_high_without_native_level_metadata_stays_unverified(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'high')
    contract['native_metadata'] = None
    assert effort_contract('gemini', model, 'high', spec) == contract
    observation = reported_profile('gemini', json.dumps({'type':'result'}), model=model, effort='high', spec=spec)
    assert observation['verified_profile'] is observation['reported_effort'] is None


def test_agy_extended_cannot_disable_thinking_with_zero_budget(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'extended')
    contract['arguments'] = ['--thinking-budget', '0']
    with pytest.raises(ValueError, match='thinking-only selector'):
        effort_contract('gemini', model, 'extended', spec)
    contract['arguments'] = ['--thinking-budget', '16384']
    assert effort_contract('gemini', model, 'extended', spec) == contract


def test_reviewed_profile_checks_physical_binary_version_and_contract_digest(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path)
    digest = effort_binary_digest(spec, contract)
    evidence = effort_version_evidence(contract, contract['version'] + '\n', digest,
        model=model, effort='extended')
    assert evidence['executable_sha256'] == digest
    assert len(evidence['contract_sha256']) == 64
    with pytest.raises(ValueError, match='version'):
        effort_version_evidence(contract, 'different version', digest, model=model, effort='extended')
    from pathlib import Path
    Path(spec['executable']).write_bytes(b'replaced physical binary')
    with pytest.raises(ValueError, match='reviewed reasoning effort'):
        effort_binary_digest(spec, contract)


@pytest.mark.parametrize('raw', [
    {'type': 'result', 'result': '{"reasoning_effort":"max"}'},
    {'type': 'result', 'reasoning_effort': 'high'},
    {'type': 'result', 'reasoning_effort': True},
    {'type': 'assistant', 'reasoning_effort': 'max'},
])
def test_prose_missing_mismatched_and_nonterminal_metadata_never_attest_profile(tmp_path, raw):
    model, spec, _ = reviewed_fixture(tmp_path)
    with pytest.raises(ValueError):
        reported_profile('claude', json.dumps(raw), model=model, effort='extended', spec=spec)


@pytest.mark.parametrize('mutation', ['model', 'tools', 'settings', 'thinking', 'result_path', 'empty_provenance'])
def test_effort_contract_cannot_override_routing_or_inference_authority(tmp_path, mutation):
    model, spec, contract = reviewed_fixture(tmp_path)
    if mutation in ('model', 'tools', 'settings'):
        contract['arguments'] = ['--' + mutation, 'unreviewed']
    elif mutation == 'thinking':
        contract['thinking_enabled'] = False
    elif mutation == 'result_path':
        contract['native_metadata']['path'] = ['metadata', 'result', 'reasoning_effort']
    else:
        contract['capability_reference'] = ''
    with pytest.raises(ValueError):
        effort_contract('claude', model, 'extended', spec)


def test_exact_profile_cannot_reuse_contract_from_other_model(tmp_path):
    model, spec, _ = reviewed_fixture(tmp_path)
    with pytest.raises(ValueError, match='exact reviewed model/profile'):
        effort_contract('claude', 'claude-fable-5-1', 'extended', spec)
    assert effort_contract('claude', model, None, spec) is None


def test_cli_without_native_effort_metadata_can_complete_without_fabricated_attestation(tmp_path):
    model, spec, contract = reviewed_fixture(tmp_path)
    contract['native_metadata'] = None
    assert effort_contract('claude', model, 'extended', spec) == contract
    argv = provider_command('claude', [spec['executable']], model, 'workspace', spec,
                            'extended', inference_only=True)
    assert argv[argv.index('--effort') + 1] == 'max'
    observation = reported_profile('claude', json.dumps({'type': 'result',
        'result': 'I claim to use extended'}), model=model, effort='extended', spec=spec)
    assert observation == {'verified_profile': None, 'native_metadata_path': None,
        'observed_native_value': None, 'reported_effort': None,
        'observation_status': 'not_reported_by_reviewed_cli'}


@pytest.mark.parametrize('arguments', [['-p', 'would spend tokens'], ['version'], ['--version', '--prompt', 'paid']])
def test_prebudget_contract_cannot_disguise_inference_as_a_version_probe(tmp_path, arguments):
    model, spec, contract = reviewed_fixture(tmp_path)
    contract['version_arguments'] = arguments
    with pytest.raises(ValueError, match='offline --version'):
        effort_contract('claude', model, 'extended', spec)
    # Fails before checking Windows identity or creating logs, never sends a
    # prompt and does not need a fake native process to demonstrate rejection.
    logs = tmp_path / 'must-not-create'
    with pytest.raises(NativeRepairProtocolError) as caught:
        RepairRunner().preflight_selected_spec(spec, {}, tmp_path, logs, lambda: True)
    assert caught.value.category == 'compatibility'
    assert not logs.exists()


@pytest.mark.parametrize('arguments', [['-m', 'other-model'], ['--model=other-model'],
    ['--fixture-maybe-enable-tools', 'true'], ['--thinking-level', 'HIGH', '--yolo'],
    ['--thinking-budget', '-1'], ['--thinking-budget', '131073']])
def test_agy_profile_accepts_only_reviewed_thinking_selectors(tmp_path, arguments):
    model, spec, contract = reviewed_fixture(tmp_path, 'gemini', 'high')
    contract['arguments'] = arguments
    with pytest.raises(ValueError):
        effort_contract('gemini', model, 'high', spec)


@pytest.mark.parametrize(('model', 'effort'), [('gpt-6-luna', 'low'), ('gpt-6.1-sol', 'medium'),
    ('gpt-6.1-sol', 'high'), ('gpt-6-astra', 'ultra')])
def test_codex_current_routes_preserve_exact_model_effort_and_disable_native_delegation(model, effort):
    argv = provider_command('codex', ['codex'], model, 'workspace', {}, effort, inference_only=True)
    assert argv[argv.index('--model') + 1] == model
    assert f'model_reasoning_effort="{effort}"' in argv
    assert 'agents.enabled=false' in argv
    assert 'features.multi_agent=false' in argv and 'features.multi_agent_v2=false' in argv
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
