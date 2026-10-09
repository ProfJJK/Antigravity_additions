"""Inference-only argv and evidence contracts; no native worker emulation.

Static protocol inputs are explicitly fixtures. Real temporary files exercise
binary identity checks. Installed CLI capability probes live in the separate
inference_policy tests; these checks do not claim Windows or paid model runs.
"""
import hashlib
import json

import pytest

from cochem_pipeline.inference_policy import CODEX_DISABLED_FEATURES, parse_codex_mcp_names
from cochem_pipeline.worker import (
    CODING_NATIVE_KINDS, codex_policy_probe_command, gemini_binary_digest,
    node_prompt, provider_command, validate_gemini_version, validate_coding_source_context,
    ramdisk_provider_failure, WorkerCleanupError,
)


def overrides(argv):
    return [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == '-c']


def test_native_ram_capacity_wait_does_not_become_a_permanent_security_hold():
    from cochem_pipeline.ramdisk import RamdiskCapacityError

    # Typed native diagnostic input, not a claim that Linux verified ImDisk.
    pressure = ramdisk_provider_failure(RamdiskCapacityError('Verified RAM reserve exhausted'))
    assert pressure.category == 'resource'
    assert pressure.retry_after_seconds == 30
    assert pressure.hold_scope is None

    with pytest.raises(TypeError):
        ramdisk_provider_failure(WorkerCleanupError('Process closure is unverified'))


def test_redirected_ram_tree_is_a_permanent_security_hold(tmp_path):
    from cochem_pipeline.ramdisk import RamdiskError, ordinary_tree

    target = tmp_path / 'physical'
    target.mkdir()
    redirected = tmp_path / 'redirected'
    try:
        redirected.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('Native Windows symlink creation privilege is unavailable')
        raise
    with pytest.raises(RamdiskError) as rejected:
        ordinary_tree(redirected)
    security = ramdisk_provider_failure(rejected.value)
    assert security.category == 'configuration'
    assert security.hold_scope == 'job'
    assert security.retry_after_seconds is None

@pytest.mark.parametrize('effort', [None, 'low', 'medium', 'high', 'ultra'])
def test_codex_coding_dispatch_has_readonly_sandbox_and_effective_feature_overrides(effort):
    names = ('plain', 'server.with.dots"andquotes')
    argv = provider_command('codex', ['protected-codex'], 'gpt-6-astra', 'ram-project', {}, effort,
                            inference_only=True, mcp_names=names)
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert argv[argv.index('-a') + 1] == 'never'
    assert argv[argv.index('--model') + 1] == 'gpt-6-astra'
    assert argv[-1] == '-'
    assert '--ignore-user-config' in argv and '--ephemeral' in argv
    values = overrides(argv)
    assert {f'features.{name}=false' for name in CODEX_DISABLED_FEATURES} <= set(values)
    assert 'web_search="disabled"' in values
    assert 'forced_login_method="chatgpt"' in values and 'model_provider="openai"' in values
    assert 'features.unified_exec=false' not in values
    assert 'features.apply_patch_freeform=false' not in values
    assert 'agents.enabled=false' in values
    assert not any('disable_in_process_fallback' in value for value in values)
    tables = [value for value in values if value.startswith('mcp_servers=')]
    assert tables == ['mcp_servers={"plain"={enabled=false},"server.with.dots\\"andquotes"={enabled=false}}']
    if effort is not None:
        assert values[-1] == f'model_reasoning_effort="{effort}"'


@pytest.mark.parametrize(('operation', 'suffix'), [
    ('mcp', ['mcp', 'list', '--json']), ('features', ['features', 'list']), ('auth', ['login', 'status']),
])
def test_codex_offline_probes_load_the_same_account_policy_before_subcommand(operation, suffix):
    argv = codex_policy_probe_command(['protected-node', 'protected-codex.js'], operation,
                                      mcp_names=['account-server'], reasoning_effort='ultra')
    assert argv[:2] == ['protected-node', 'protected-codex.js']
    assert argv[-len(suffix):] == suffix
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert set(f'features.{name}=false' for name in CODEX_DISABLED_FEATURES) <= set(overrides(argv))
    assert 'mcp_servers={"account-server"={enabled=false}}' in overrides(argv)
    assert 'model_reasoning_effort="ultra"' in overrides(argv)
    assert 'exec' not in argv


def test_mcp_transport_secrets_never_enter_dispatch_overrides():
    raw = json.dumps([{'name': 'configured-server', 'enabled': True,
        'transport': {'command': 'DO_NOT_LOG_COMMAND', 'env': {'TOKEN': 'DO_NOT_LOG_SECRET'}}}])
    names = parse_codex_mcp_names(raw)
    argv = provider_command('codex', ['codex'], 'gpt-6-sol', 'ram-project', {},
                            inference_only=True, mcp_names=names)
    rendered = json.dumps(argv)
    assert 'configured-server' in rendered
    assert 'DO_NOT_LOG' not in rendered
    with pytest.raises(ValueError, match='enabled MCP'):
        parse_codex_mcp_names(raw, require_disabled=True)


def test_claude_coding_tools_mcp_hooks_and_settings_are_disabled_without_disabling_oauth():
    argv = provider_command('claude', ['claude.exe'], 'claude-fable-5-1', 'ram-project',
                            {'allowed_tools': ['Bash', 'Write', 'mcp__docker__run']}, inference_only=True)
    assert argv[argv.index('--tools') + 1] == ''
    assert argv.count('--tools') == 1
    assert argv.count('--strict-mcp-config') == 1
    assert json.loads(argv[argv.index('--mcp-config') + 1]) == {'mcpServers': {}}
    assert argv[argv.index('--setting-sources') + 1] == ''
    settings = json.loads(argv[argv.index('--settings') + 1])
    assert settings == {'disableAllHooks': True, 'disableSkillShellExecution': True,
                        'fallbackModel': [], 'switchModelsOnFlag': False}
    assert argv[argv.index('--permission-mode') + 1] == 'default'
    assert '--disable-slash-commands' in argv and '--no-session-persistence' in argv
    assert '--allowedTools' not in argv and '--bare' not in argv
    assert 'acceptEdits' not in argv and '--dangerously-skip-permissions' not in argv


def gemini_spec(binary):
    return {'executable': str(binary), 'arguments': ['legacy-host-tools-enabled', '--model', '{model}'],
        'inference_only': {'arguments': ['fixture-infer-no-tools', '--selected-model', '{model}'],
            'version_arguments': ['fixture-native-version'], 'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'version': 'Agy reviewed-version fixture', 'capability_reference': 'Operator protocol fixture, not native availability evidence',
            'disables_tools': True, 'disables_mcp': True, 'disables_hooks': True,
            'disables_subagents': True, 'disables_model_fallback': True}}


def test_gemini_uses_only_reviewed_native_argv_and_selected_model(tmp_path):
    binary = tmp_path / 'agy.exe'
    binary.write_bytes(b'explicit binary identity fixture')
    spec = gemini_spec(binary)
    argv = provider_command('gemini', [str(binary)], 'gemini-3.1-pro', 'ram-project', spec, inference_only=True)
    assert argv == [str(binary), 'fixture-infer-no-tools', '--selected-model', 'gemini-3.1-pro']
    assert 'legacy-host-tools-enabled' not in argv
    digest = gemini_binary_digest(spec)
    assert validate_gemini_version(spec, 'Agy reviewed-version fixture\n', digest)['executable_sha256'] == digest
    for version in ('different-version', 'Diagnostic\nAgy reviewed-version fixture', ''):
        with pytest.raises(ValueError, match='version'):
            validate_gemini_version(spec, version, digest)


def test_gemini_binary_replacement_invalidates_contract(tmp_path):
    binary = tmp_path / 'agy.exe'
    binary.write_bytes(b'original registered binary fixture')
    spec = gemini_spec(binary)
    binary.write_bytes(b'different registered binary fixture')
    with pytest.raises(ValueError, match='reviewed inference-only'):
        gemini_binary_digest(spec)


def test_gemini_redirected_binary_path_invalidates_contract(tmp_path):
    binary = tmp_path / 'agy.exe'
    binary.write_bytes(b'original registered binary fixture')
    spec = gemini_spec(binary)
    linked = tmp_path / 'linked.exe'
    try:
        linked.symlink_to(binary)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('Native Windows symlink creation privilege is unavailable')
        raise
    linked_spec = gemini_spec(linked)
    with pytest.raises(ValueError, match='symbolic link'):
        gemini_binary_digest(linked_spec)


@pytest.mark.parametrize('mutation', ['missing_contract', 'missing_model', 'duplicate_model', 'missing_version_probe'])
def test_gemini_cannot_guess_flags_model_or_version_probe(tmp_path, mutation):
    binary = tmp_path / 'agy.exe'
    binary.write_bytes(b'fixture')
    spec = gemini_spec(binary)
    if mutation == 'missing_contract':
        del spec['inference_only']
    elif mutation == 'missing_model':
        spec['inference_only']['arguments'] = ['fixture-infer-no-tools']
    elif mutation == 'duplicate_model':
        spec['inference_only']['arguments'] += ['{model}']
    else:
        del spec['inference_only']['version_arguments']
    with pytest.raises(ValueError):
        provider_command('gemini', [str(binary)], 'gemini-3.1-pro', 'ram-project', spec, inference_only=True)


@pytest.mark.parametrize('kind', sorted(CODING_NATIVE_KINDS))
def test_every_coding_prompt_uses_source_packet_without_native_host_tools(kind):
    digest = 'a' * 64
    payload = {'objective': 'Correct a bounded function', 'requirements': ['REQ-1'],
        'allowed_paths': ['src'], 'test_paths': ['tests'], 'plan_sha256': digest,
        'plan': {'requirements': {'R1': 'REQ-1'}, 'artifact_hashes': {'srs/ch01.md': digest}},
        'file': {'path': 'src/example.py', 'after_sha256': digest, 'diff_sha256': digest},
        'test_receipt_sha256': digest, 'failure_evidence_sha256': digest,
        'source_context': {'files': {'src/example.py': {'text': 'VALUE = 41\n', 'sha256': digest}},
                           'content_omitted_count': 3, 'source_snapshot_sha256': digest}}
    prompt = node_prompt({'kind': kind, 'workflow_id': 'workflow', 'payload': payload})
    assert 'Native tools, MCP servers, hooks, and host execution are disabled.' in prompt
    assert 'Use only the supplied source_context file texts' in prompt
    assert 'do not claim to have inspected omitted contents or executed tests' in prompt
    assert 'VALUE = 41' in prompt
    assert 'Read files using native tools' not in prompt
    if kind == 'CODE_RESEARCH':
        shape = json.loads(prompt.split('Required output shape:\n', 1)[1].split('\n\nTask payload', 1)[0])
        assert set(shape['root_cause']) == {'category', 'diagnosis'}
        assert shape['disposition'] == 'pivot'
        assert 'test_assumption' in prompt and 'escalate' in prompt


def test_initial_research_is_not_misrepresented_as_a_failure_pivot():
    payload = {'objective': 'Plan implementation', 'plan_sha256': 'a' * 64, 'research_phase': 'initial',
               'source_context': {'files': {}}}
    prompt = node_prompt({'kind': 'CODE_RESEARCH', 'payload': payload})
    shape = json.loads(prompt.split('Required output shape:\n', 1)[1].split('\n\nTask payload', 1)[0])
    assert 'failure_evidence_sha256' not in shape and 'disposition' not in shape
    assert 'Native tools, MCP servers, hooks, and host execution are disabled.' in prompt


def test_legacy_planning_command_contract_remains_explicit():
    codex = provider_command('codex', ['codex'], 'gpt-6-sol', 'workspace', {})
    assert codex[codex.index('--sandbox') + 1] == 'workspace-write'
    claude = provider_command('claude', ['claude'], 'claude-sonnet-5-5', 'workspace', {'allowed_tools': ['Read']})
    assert claude[claude.index('--allowedTools') + 1] == 'Read'
    with pytest.raises(ValueError, match='explicit inference-only'):
        provider_command('codex', ['codex'], 'gpt-6-sol', 'workspace', {}, mcp_names=['unapplied'])


def source_packet_fixture():
    content = 'VALUE = 42\n# UTF-8 café\n'
    return {'payload': {'snapshot_sha256': 'a' * 64, 'source_context': {
        'source_snapshot_sha256': 'a' * 64,
        'files': {'src/value.py': {'text': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}}
    }}}


def test_tool_free_source_packet_binds_actual_utf8_bytes_and_queued_snapshot():
    node = source_packet_fixture()
    receipt = validate_coding_source_context(node)
    assert len(receipt) == 64
    node['payload']['source_context']['files']['src/value.py']['text'] = 'VALUE = 999\n'
    with pytest.raises(ValueError, match='unverified bytes'):
        validate_coding_source_context(node)


@pytest.mark.parametrize('mutation', ['missing', 'wrong_snapshot', 'too_large', 'unsafe_path'])
def test_source_packet_failure_is_detected_before_native_launch(mutation):
    node = source_packet_fixture()
    packet = node['payload']['source_context']
    if mutation == 'missing':
        del node['payload']['source_context']
    elif mutation == 'wrong_snapshot':
        packet['source_snapshot_sha256'] = 'b' * 64
    elif mutation == 'too_large':
        text = 'x' * 98305
        packet['files']['src/value.py'] = {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
    else:
        packet['files']['../outside'] = packet['files'].pop('src/value.py')
    with pytest.raises(ValueError):
        validate_coding_source_context(node)
