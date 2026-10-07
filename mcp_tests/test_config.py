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
    data['providers']['codex']['max_workers'] = 257
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


def test_deprecated_bridge_worker_and_queue_fields_cannot_reimpose_controller_limits(tmp_path):
    path = tmp_path/'bridge.json'
    provider = {'models': {'sol': 'gpt-6-sol'}, 'default_model': 'sol',
                'max_workers': 256, 'max_pending': 1}
    raw = {'workspace_roots': [str(tmp_path)], 'providers': {'codex': provider}}
    path.write_text(json.dumps(raw))
    settings = load_settings(str(path), 'codex')
    assert settings.max_workers == 256 and settings.max_pending == 1
    provider['max_pending'] = 1000000
    path.write_text(json.dumps(raw))
    assert load_settings(str(path), 'codex').max_pending == 1000000
    for invalid in (0, True, 1000001):
        provider['max_pending'] = invalid
        path.write_text(json.dumps(raw))
        with pytest.raises(ValueError, match='controller owns its queue'):
            load_settings(str(path), 'codex')
