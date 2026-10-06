import json
import pytest
from cochem_mcp.config import load_settings


def test_config_paths_models_and_limits(tmp_path):
    path = tmp_path / 'bridge.json'
    path.write_text(json.dumps({'workspace_roots': [str(tmp_path)], 'state_dir': 'state',
        'providers': {'codex': {'models': {'sol': 'gpt-6-sol'}, 'default_model': 'sol'}}}))
    settings = load_settings(str(path), 'codex')
    assert settings.model('sol') == 'gpt-6-sol'
    assert settings.state_dir == tmp_path / 'state' / 'codex'
    assert settings.workspace('') == tmp_path
    with pytest.raises(ValueError, match='configured aliases'):
        settings.model('unknown')
    with pytest.raises(ValueError, match='workspace_roots'):
        settings.workspace(str(tmp_path.parent))
    data = json.loads(path.read_text())
    data['providers']['codex']['max_workers'] = 100
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='max_workers'):
        load_settings(str(path), 'codex')


def test_claude_tool_permissions_are_explicit_and_validated(tmp_path):
    path = tmp_path / 'bridge.json'
    section = {'models': {'sonnet': 'claude-example'}, 'default_model': 'sonnet',
               'allowed_tools': ['Bash(python -m pytest:*)']}
    data = {'workspace_roots': [str(tmp_path)], 'providers': {'claude': section}}
    path.write_text(json.dumps(data))
    assert load_settings(str(path), 'claude').allowed_tools == ('Bash(python -m pytest:*)',)
    section['allowed_tools'] = ['--dangerously-skip-permissions']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='allowed_tools'):
        load_settings(str(path), 'claude')
