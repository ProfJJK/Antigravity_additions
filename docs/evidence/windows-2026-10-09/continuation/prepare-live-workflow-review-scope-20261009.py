"""Save exact prepared synthetic workflow requests for review; no network IO."""
import hashlib
import json
from pathlib import Path
import types

here = Path(__file__).absolute().parent
source = here / 'run-live-commissioning-r3-v2.py'
raw = source.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
m = types.ModuleType('synthetic_workflow_scope')
m.__file__ = str(source)
exec(compile(raw, str(source), 'exec'), m.__dict__)
config = json.loads((m.INSTALL / 'pipeline.json').read_text(encoding='utf-8-sig'))
assert config['rules'] == []
assert config['providers']['gemini']['integration_hold']['state'] == 'verification_pending'
scope = {
    'schema': 'cochem-prepared-live-workflow-review-scope/1',
    'status': 'PREPARED_NOT_SUBMITTED',
    'exact_initial_requests': m.requests(),
    'model_prompt_context': ['Synthetic study-note requirements and derived SRS/WBS artifacts',
                             'Disposable calculator source and pytest tests from the registered windows-acceptance project',
                             'Canonical pipeline governing requirements, provenance hashes and fixed execution directives',
                             'Derived planning, review and test-result diagnostics for these same two workflows'],
    'provider_destinations': ['OpenAI through the existing authenticated Codex subscription CLI',
                              'Anthropic through the existing authenticated Claude subscription CLI when Chapter 06 native effort compatibility allows it'],
    'agy_integration_hold_preserved': True,
    'unrelated_user_project_inputs': False,
    'credentials_are_model_inputs': False,
    'private_database_contents_are_model_inputs': False,
    'native_model_tools_hooks_delegation_disabled': True,
    'code_execution': 'Controller runs disposable calculator tests in the prepared Docker boundary',
    'branch_changed': 'Only windows-acceptance pipeline/accepted branch if independent acceptance succeeds',
    'shared_capacity_limit': 4,
    'model_routing': 'Canonical Chapter 06 unchanged',
    'no_api_key_route_or_paid_repair': True,
    'original_fixed_request_ids_preserved': True,
    'no_automatic_resubmission_after_attempt': True,
    'initial_review_result': 'Execution rejected before process creation; exact scope has now been inspected',
}
target = here / 'LIVE_WORKFLOW_TEST_SCOPE_20261009.json'
with target.open('x', encoding='utf-8', newline='\n') as stream:
    json.dump(scope, stream, sort_keys=True, indent=2)
    stream.write('\n')
print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'initial_requests': 2, 'network_calls': 0, 'model_jobs_submitted': 0}))
