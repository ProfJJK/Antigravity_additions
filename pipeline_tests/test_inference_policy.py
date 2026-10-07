"""Generation-only CLI contracts; live checks use offline commands, no inference."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from cochem_pipeline.inference_policy import (
    CODEX_DISABLED_FEATURES, claude_inference_arguments, codex_policy_overrides,
    gemini_inference_arguments, parse_codex_mcp_names, validate_claude_help,
    validate_codex_features,
)


def feature_output(**changes):
    return '\n'.join(name+' stable '+str(changes.get(name,False)).lower() for name in CODEX_DISABLED_FEATURES)


def test_required_native_feature_readback_is_complete_and_actually_disabled():
    assert len(validate_codex_features(feature_output())['evidence_sha256'])==64
    for invalid in (feature_output(shell_tool=True), feature_output(code_mode_host=True),
                    feature_output().replace('shell_tool stable false',''), 'I disabled all tools'):
        with pytest.raises(ValueError):validate_codex_features(invalid)
    assert 'unified_exec' not in CODEX_DISABLED_FEATURES
    assert 'apply_patch_freeform' not in CODEX_DISABLED_FEATURES


def test_mcp_output_is_reduced_to_names_without_returning_auth_configuration():
    stdout=json.dumps([{'name':'server.one','enabled':True,'transport':{'env':{'SECRET':'not-returned'}}}])
    assert parse_codex_mcp_names(stdout)==('server.one',)
    with pytest.raises(ValueError,match='enabled MCP'):
        parse_codex_mcp_names(stdout,require_disabled=True)


@pytest.mark.parametrize('raw', ['{}','[{"name":"x","enabled":false,"enabled":true}]',
    '[{"name":"x","enabled":0}]','[{"name":"x","enabled":false},{"name":"x","enabled":false}]'])
def test_ambiguous_or_missing_mcp_state_is_rejected(raw):
    with pytest.raises(ValueError):parse_codex_mcp_names(raw,require_disabled=True)


def test_mcp_inline_table_quotes_names_without_dotted_path_parser_ambiguity():
    names=['simple','quoted.name"\\suffix']
    args=codex_policy_overrides(names)
    value=args[-1]
    assert value.startswith('mcp_servers={')
    assert json.dumps(names[1])+'={enabled=false}' in value
    assert 'disable_in_process_fallback' not in ' '.join(args)


def test_claude_tool_policy_keeps_subscription_auth_and_disables_hooks_mcp_and_skills():
    argv=claude_inference_arguments()
    assert argv[argv.index('--tools')+1]==''
    assert '--bare' not in argv and '--dangerously-skip-permissions' not in argv
    assert json.loads(argv[argv.index('--mcp-config')+1])=={'mcpServers':{}}
    assert json.loads(argv[argv.index('--settings')+1])['disableAllHooks'] is True
    with pytest.raises(ValueError):validate_claude_help('Supports --tools but no documented empty-list semantics')


def gemini_spec():
    return {'inference_only':{'arguments':['--documented-tool-mode','disabled'],
        'version_arguments':['--documented-version-probe'], 'executable_sha256':'a'*64,
        'version':'operator-observed-version','capability_reference':'operator-reviewed native documentation',
        'disables_tools':True,'disables_mcp':True,'disables_hooks':True,
        'disables_subagents':True,'disables_model_fallback':True}}


def test_gemini_never_guesses_native_flags_and_requires_operator_bound_evidence():
    spec=gemini_spec()
    assert gemini_inference_arguments(spec)==spec['inference_only']['arguments']
    with pytest.raises(ValueError):gemini_inference_arguments({})
    for key,value in [('disables_tools',False),('disables_subagents',False),
                      ('disables_model_fallback',False),('executable_sha256','mutable'),('version_arguments',[])]:
        changed=gemini_spec();changed['inference_only'][key]=value
        with pytest.raises(ValueError):gemini_inference_arguments(changed)


def test_actual_codex_offline_features_and_mcp_disable_readback(tmp_path):
    executable=shutil.which('codex')
    if executable is None:pytest.skip('Native Codex executable not installed')
    home=tmp_path/'profile';home.mkdir()
    names=['sentinel','sentinel.with.dot"quote']
    (home/'config.toml').write_text('\n'.join('[mcp_servers.'+json.dumps(name)+']\ncommand="this-sentinel-must-never-execute"\nenabled=true\n' for name in names))
    env=dict(os.environ);env['CODEX_HOME']=str(home)
    def run(arguments):
        result=subprocess.run([executable,*arguments],env=env,cwd=home,text=True,capture_output=True,timeout=15)
        assert result.returncode==0,result.stderr[:500]
        return result.stdout
    base=codex_policy_overrides()
    listed=parse_codex_mcp_names(run([*base,'mcp','list','--json']))
    assert set(listed)==set(names)
    arguments=codex_policy_overrides(listed)
    assert parse_codex_mcp_names(run([*arguments,'mcp','list','--json']),require_disabled=True)==listed
    assert validate_codex_features(run([*arguments,'features','list']))['disabled_features']==list(CODEX_DISABLED_FEATURES)


def test_actual_claude_help_confirms_empty_tools_and_subscription_compatible_flags():
    script=os.environ.get('COCHEM_TEST_CLAUDE_JS')
    command=[shutil.which('node'),script] if script and shutil.which('node') else [shutil.which('claude')]
    if not command[0]:pytest.skip('Set COCHEM_TEST_CLAUDE_JS or install the native Claude CLI')
    result=subprocess.run([*command,'--help'],capture_output=True,text=True,timeout=15)
    assert result.returncode==0
    assert validate_claude_help(result.stdout)['evidence_sha256']
