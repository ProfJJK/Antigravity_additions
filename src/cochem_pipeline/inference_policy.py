"""Fail-closed native CLI generation policy; no model or CLI alias substitution.

Native CLIs provide subscription-authenticated inference. The controller applies
structured file changes, and project code runs only in the Docker execution
plane. Offline per-account capability probes must validate these overrides before
paid inference. Never log raw MCP configuration or credential-bearing output.
"""
from __future__ import annotations
import hashlib
import json
import re

# UnifiedExec is intentionally absent: Codex 0.159 forces that feature on even
# after an override. ShellTool is the actual registry gate for shell/exec/stdin.
# ApplyPatchFreeform is removed; read-only sandbox plus file-integrity checks
# contain any remaining patch handler. Neither flag is falsely attested here.
CODEX_DISABLED_FEATURES = (
    'shell_tool', 'shell_snapshot', 'code_mode_host', 'code_mode', 'code_mode_only',
    'apps', 'plugins', 'hooks', 'remote_plugin', 'recommended_plugins',
    'multi_agent', 'multi_agent_v2', 'browser_use', 'browser_use_external',
    'browser_use_full_cdp_access', 'computer_use', 'in_app_local_automation',
    'workspace_dependencies', 'skill_search', 'skill_mcp_dependency_install',
    'tool_suggest', 'request_permissions_tool', 'view_image', 'image_generation',
)


def _json(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('Native policy response exceeds its bounded JSON contract')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate native policy JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def parse_codex_mcp_names(stdout: str, *, require_disabled=False) -> tuple[str, ...]:
    """Parse only names/enabled; callers must discard the remaining raw config."""
    value = _json(stdout)
    if not isinstance(value, list) or len(value) > 512:
        raise ValueError('Codex MCP enumeration must be a bounded server array')
    names = []
    for server in value:
        if not isinstance(server, dict):
            raise ValueError('Malformed Codex MCP server')
        name, enabled = server.get('name'), server.get('enabled')
        if (not isinstance(name, str) or not name or len(name) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or type(enabled) is not bool or name in names):
            raise ValueError('Ambiguous Codex MCP server identity or enabled state')
        if require_disabled and enabled:
            raise ValueError('Codex still has an enabled MCP server')
        names.append(name)
    return tuple(sorted(names))


def codex_policy_overrides(mcp_names=()) -> list[str]:
    """Exact argv entries, including quoted TOML keys; no shell interpolation."""
    result = []
    for name in CODEX_DISABLED_FEATURES:
        result += ['-c', 'features.' + name + '=false']
    result += ['-c', 'web_search="disabled"', '-c', 'agents.enabled=false']
    seen = set()
    entries = []
    for name in mcp_names:
        if (not isinstance(name, str) or not name or len(name) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in name) or name in seen):
            raise ValueError('Invalid MCP name for native policy override')
        seen.add(name)
        entries.append(json.dumps(name, ensure_ascii=False) + '={enabled=false}')
    if entries:
        # Codex's -c dotted-path parser does not honor quoted dotted key names.
        # A TOML inline table at the simple mcp_servers key merges safely and
        # disables each exact original name, including dots/quotes/backslashes.
        result += ['-c', 'mcp_servers={' + ','.join(entries) + '}']
    return result


def validate_codex_features(stdout: str) -> dict:
    if not isinstance(stdout, str) or len(stdout) > 262144:
        raise ValueError('Native Codex feature evidence is missing or oversized')
    actual = {}
    for line in stdout.splitlines():
        parts = line.split()
        if parts and parts[0] in CODEX_DISABLED_FEATURES:
            if len(parts) < 3 or parts[-1] not in ('true', 'false') or parts[0] in actual:
                raise ValueError('Native Codex feature evidence is ambiguous')
            actual[parts[0]] = parts[-1] == 'true'
    if set(actual) != set(CODEX_DISABLED_FEATURES) or any(actual.values()):
        raise ValueError('Installed Codex cannot prove all required host tool gates disabled')
    return {'disabled_features': list(CODEX_DISABLED_FEATURES),
            'evidence_sha256': hashlib.sha256(stdout.encode()).hexdigest()}


def claude_inference_arguments() -> list[str]:
    # --bare must NOT be used: it disables OAuth/keychain subscription login.
    return ['--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--setting-sources', '', '--disable-slash-commands', '--settings',
            '{"disableAllHooks":true,"disableSkillShellExecution":true,'
            '"fallbackModel":[],"switchModelsOnFlag":false}']


def validate_claude_help(stdout: str) -> dict:
    required = ('--tools', '--strict-mcp-config', '--mcp-config', '--setting-sources',
                '--disable-slash-commands', '--settings')
    if not isinstance(stdout, str) or len(stdout) > 262144 or any(
            re.search(r'(?<![A-Za-z0-9-])' + re.escape(flag) + r'(?![A-Za-z0-9-])', stdout) is None
            for flag in required):
        raise ValueError('Installed Claude CLI lacks the required inference-only flags')
    if not re.search(r'Use\s+""\s+to\s+disable\s+all\s+tools\b', stdout):
        raise ValueError('Claude help does not confirm that an empty tool list disables tools')
    return {'flags': list(required), 'evidence_sha256': hashlib.sha256(stdout.encode()).hexdigest()}


def gemini_inference_arguments(spec: dict) -> list[str]:
    """Require reviewed native capability evidence; never invent Agy options.

    The operator records the exact executable SHA256, version and published
    capability reference after confirming this contract on the actual host.
    NativeRunner must compare the binary digest and version before every use.
    """
    contract = spec.get('inference_only') if isinstance(spec, dict) else None
    if not isinstance(contract, dict) or set(contract) != {
            'arguments', 'version_arguments', 'executable_sha256', 'version', 'capability_reference',
            'disables_tools', 'disables_mcp', 'disables_hooks', 'disables_subagents', 'disables_model_fallback'}:
        raise ValueError('Agy requires an operator-verified inference-only native contract')
    if any(contract[key] is not True for key in ('disables_tools','disables_mcp','disables_hooks',
                                                'disables_subagents','disables_model_fallback')):
        raise ValueError('Agy inference contract must disable tools, MCP, hooks, subagents and model fallback')
    if not isinstance(contract['executable_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', contract['executable_sha256']):
        raise ValueError('Agy inference contract requires the exact verified native binary digest')
    if any(not isinstance(contract[key], str) or not contract[key].strip() or len(contract[key]) > 2048
           for key in ('version','capability_reference')):
        raise ValueError('Agy inference contract requires version and native capability provenance')
    version_arguments = contract['version_arguments']
    if not isinstance(version_arguments, list) or not version_arguments or len(version_arguments) > 32 or any(
            not isinstance(item, str) or not item or '\0' in item or len(item) > 8192 for item in version_arguments):
        raise ValueError('Agy version probe must be an operator-documented bounded argv array')
    arguments = contract['arguments']
    if not isinstance(arguments, list) or not arguments or len(arguments) > 64 or any(
            not isinstance(item, str) or '\0' in item or len(item) > 8192 for item in arguments):
        raise ValueError('Agy inference contract arguments must be an explicit bounded argv array')
    return list(arguments)
