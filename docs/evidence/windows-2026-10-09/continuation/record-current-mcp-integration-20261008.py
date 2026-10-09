"""Record the current chat's observed tool discovery and read-only health call."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06')
NAMES = ('knowledge_read', 'knowledge_search', 'knowledge_status', 'pipeline_cancel',
         'pipeline_code', 'pipeline_code_cancel', 'pipeline_code_resume', 'pipeline_code_status',
         'pipeline_health', 'pipeline_operator_view', 'pipeline_projects',
         'pipeline_provider_preflight_submit', 'pipeline_resume_routing', 'pipeline_status',
         'pipeline_submit')

value = {
    'schema': 'cochem-current-chat-mcp-integration/1',
    'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
    'status': 'CURRENT_CODEX_TOOL_CATALOG_AVAILABLE_CONTROLLER_CONNECTION_REFUSED',
    'platform': 'actual Windows operator process',
    'tool_catalog_discovery': {
        'method': 'functions.ALL_TOOLS metadata enumeration',
        'count': len(NAMES),
        'names': ['mcp__cochem_pipeline__' + name for name in NAMES],
        'current_chat_catalog_loaded': True,
    },
    'actual_call': {
        'name': 'mcp__cochem_pipeline__pipeline_health',
        'arguments': {},
        'is_error': True,
        'winerror': 10061,
        'disposition': 'connection_refused',
        'controller_health_verified': False,
        'read_only_operation': True,
    },
    'model_jobs_submitted': 0,
    'daemon_or_task_changes': 0,
    'token_handling': 'The configured MCP client uses its normal authentication path; no token contents were printed, copied or archived.',
    'owner_reconnection_action_required_for_this_chat': False,
    'full_pipeline_running_verified': False,
    'full_srs_acceptance': False,
    'previous_connection_evidence_preserved': True,
    'next': 'Successful reviewed first-start and live health/workflow evidence remain necessary.',
}
raw = (json.dumps(value, indent=2) + '\n').encode()
target = ROOT / 'codex-current-chat-mcp-20261008.json'
with target.open('xb') as stream:
    stream.write(raw)
print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(raw).hexdigest(), 'status': value['status']}))
