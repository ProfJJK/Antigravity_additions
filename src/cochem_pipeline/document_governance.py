"""Versioned captures of the actual document and manual-preflight state machines.

These contracts describe existing controller gates; they neither replace native
evidence nor borrow the unrelated coding/TDD execution-contract identity.
"""
from __future__ import annotations

from copy import deepcopy

from .planning_governance import SPECIFICATION_ID, digest
from .failures import ProviderFailure


class DocumentCommitmentHold(ProviderFailure):
    """Retain pre-repair accepted plans instead of inventing missing WBS pins."""
    def __init__(self):
        super().__init__('compatibility', hold_scope='job')
        self.summary = ('Historical document planning lacks required WBS/output commitments; '
                        'explicit operator reconciliation is required, preserving accepted outputs and budgets')
        self.args = (self.summary,)


def execution_contract(workflow_type='document_plan'):
    common = {'specification_id': SPECIFICATION_ID,
        'root_kind': 'MACRO_PLANNING_REQUEST', 'workflow_type': workflow_type,
        'job_states': ['PENDING', 'PENDING_RETRY', 'IN_PROGRESS', 'BLOCKED', 'FAILED', 'COMPLETED'],
        'model_routing': 'captured_chapter_06_complexity_order_with_live_admission',
        'attempt_gates': ['active-workflow', 'dependency-completion', 'eligible-deadline',
            'shared-admission', 'fresh-lease-attempt-and-fence', 'reserved-model-identity',
            'native-subscription-receipt', 'exact-output-hash', 'confirmed-process-cleanup'],
        'retry_gates': ['classified-failure', 'retained-budgets', 'fenced-prior-attempt',
            'durable-availability-backoff', 'confirmed-cleanup-before-redispatch'],
        'cancellation': {'persisted_status': 'FAILED', 'event': 'WORKFLOW_CANCELLED',
                         'terminal': True, 'unfinished_descendants_fenced': True}}
    if workflow_type == 'document_plan':
        return {**common, 'schema': 'cochem-document/4.2.7',
            'specification_path': '4.2.7_SRS.md#chapter-04-planning-dag',
            'submission_nodes': [{'kind': 'MANIFEST_GENERATOR', 'status': 'PENDING'},
                                 {'kind': 'SYNTHESIS', 'status': 'BLOCKED'}],
            'manifest_completion': {'creates': 'CHAPTER_DRAFT', 'transactional_scatter': True,
                'requirements': ['declared-chapter-count', 'unique-chapter-identities',
                    'nonempty-assigned-requirements', 'complete-requested-requirement-coverage',
                    'bounded-owned-wbs-declarations', 'unique-owned-wbs-task-identities']},
            'chapter_completion': {'requirements': ['assigned-chapter-ownership',
                'assigned-requirement-tracing', 'structured-wbs', 'preserved-manifest-wbs',
                'exact-artifact-uri', 'nonempty-artifact-bytes']},
            'synthesis_release': {'from_status': 'BLOCKED', 'to_status': 'PENDING',
                'requirements': ['all-declared-chapters-completed', 'immutable-chapter-hashes',
                    'complete-accepted-structured-outputs', 'immutable-output-hashes',
                    'deterministic-coverage-report', 'no-unresolved-coverage-gaps']},
            'workflow_completion': {'accepted_kind': 'SYNTHESIS',
                'requirements': ['all-exact-chapter-hashes', 'all-exact-output-hashes',
                    'exact-coverage-report-hash', 'preserved-accepted-wbs-by-chapter',
                    'nonempty-master-artifact', 'current-native-receipt']}}
    if workflow_type == 'provider_preflight':
        return {**common, 'schema': 'cochem-preflight/4.2.7',
            'specification_path': '4.2.7_SRS.md#chapter-12-native-cli-and-mcp-interfaces',
            'submission_nodes': [{'kind': 'PREFLIGHT_REQUEST', 'status': 'PENDING'}],
            'inference_only': True, 'accepted_response': {'ready': True}, 'extra_response_fields_allowed': False,
            'maximum_dispatches': 3, 'maximum_routing_seconds': 300,
            'maximum_inference_seconds': 60, 'stricter_captured_limits_apply': True,
            'maximum_accepted_results': 1, 'periodic_resubmission': False,
            'workflow_completion': {'accepted_kind': 'PREFLIGHT_REQUEST',
                                    'requirements': ['exact-readiness-response', 'current-native-receipt']}}
    raise ValueError('Unsupported document execution-contract type')


def capture_authority(governing_requirements, workflow_type):
    contract = execution_contract(workflow_type)
    return {**deepcopy(governing_requirements), 'contract_type': workflow_type,
            'contract_sha256': digest(contract), 'contract': contract}
