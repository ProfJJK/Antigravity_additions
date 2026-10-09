"""Provider-named compatibility front ends for the authenticated job board.

No inference process is launched here. All new work is a controller workflow
whose nodes receive Chapter 06 reservations; a tool name is not a model pin.
"""
from __future__ import annotations

import json
import re

from .config import Settings
from cochem_pipeline.service import ControlClient

TERMINAL = {'completed', 'failed', 'cancelled'}


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.state_dir
        self._closed = False
        self._client = (ControlClient(settings.controller_port, settings.controller_token_file)
                        if settings.controller_port and settings.controller_token_file else None)

    def _controller(self):
        if self._closed:
            raise RuntimeError('MCP server is shutting down')
        if self._client is None:
            raise RuntimeError('Chapter 06 job-board routing is required. Configure controller.port, '
                               'controller.token_file and controller.projects, or use cochem_pipeline MCP.')
        return self._client

    @staticmethod
    def _identity(job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'(code|plan):[A-Za-z0-9][A-Za-z0-9._-]{0,127}', job_id):
            raise ValueError('Expected a controller workflow ID returned by submit')
        return job_id.split(':', 1)

    @staticmethod
    def _record(workflow, kind):
        workflow_id = workflow['workflow_id']
        # Coding and planning controllers expose different aggregate states.
        aggregate = workflow.get('status')
        coding = workflow.get('coding')
        state = coding.get('status') if kind == 'code' and isinstance(coding, dict) else workflow.get('state')
        if not isinstance(state, str):
            state = aggregate if isinstance(aggregate, str) else 'UNKNOWN'
        cancelled = state == 'CANCELLED' or (aggregate == 'FAILED' and any(
            isinstance(event, dict) and event.get('event') == 'WORKFLOW_CANCELLED'
            for event in workflow.get('events', [])))
        if cancelled:
            status = 'cancelled'
        elif state == 'COMPLETED' and aggregate in (None, 'COMPLETED'):
            status = 'completed'
        elif aggregate == 'FAILED' or state == 'FAILED':
            status = 'failed'
        elif aggregate == 'BLOCKED' or state in {'BLOCKED', 'PLANNING_HOLD', 'RESEARCH_HOLD', 'PHYSICS_WALL', 'READY_TO_INTEGRATE'}:
            status = 'blocked'
        elif state in {'PLANNING', 'PENDING', 'PENDING_RETRY'}:
            status = 'queued'
        else:
            status = 'unknown' if state == 'UNKNOWN' else 'running'
        return {'job_id': kind + ':' + workflow_id, 'workflow_id': workflow_id,
                'status': status, 'controller_state': state, 'controller_aggregate_status': aggregate,
                'provider': None,
                'routing': 'Chapter 06 complexity routing and spillover',
                'requested_model': None, 'reported_model': None,
                'workflow': workflow, 'submission_scope': 'protected_job_board'}

    def health(self):
        try:
            health = self._controller().call('/health')
            return {'ready': isinstance(health, dict), 'ready_scope': 'authenticated controller submission interface',
                    'native_execution_verified': False,
                    'provider': self.settings.provider, 'inference_authority': 'controller_job_board',
                    'provider_name_is_model_pin': False, 'controller': health,
                    'model_availability_verified': False}
        except (OSError, ValueError, RuntimeError) as exc:
            return {'ready': False, 'provider': self.settings.provider, 'reason': str(exc)}

    def submit(self, prompt: str, workspace: str = '', model: str = ''):
        if model:
            raise ValueError('Model pinning is forbidden; Chapter 06 selects every model task')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 4*1024*1024:
            raise ValueError('Prompt must contain 1..4194304 UTF-8 bytes')
        client = self._controller()
        project = self.settings.project(workspace)
        workflow = client.call('/coding/submit', {'project_id': project, 'objective': prompt,
                                                 'requirements': ['REQ-001']})
        return self._record(workflow, 'code')

    def submit_node(self, kind, payload, workflow_id, workspace='', model=''):
        # Shared validation rejects control authority before contacting controller.
        from .server import _validate_structured_request
        _validate_structured_request(kind, payload, workflow_id)
        if model:
            raise ValueError('Model pinning is forbidden; Chapter 06 selects every model task')
        if workspace:
            self.settings.workspace(workspace)
        client = self._controller()
        objective = payload['objective']
        if kind == 'CHAPTER_DRAFT':
            # Preserve the requested chapter's meaning without impersonating an
            # already accepted native chapter result or mutating another DAG.
            objective += '\n\nRequested chapter scope: ' + json.dumps(
                {'chapter_id': payload['chapter_id'], 'title': payload['title']}, ensure_ascii=False)
        workflow = client.call('/submit', {'objective': objective,
            'requirements': payload['requirements'], 'workflow_id': workflow_id,
            'chapter_count': payload.get('chapter_count', 1)})
        record = self._record(workflow, 'plan')
        record.update(node_kind=kind, notice='Accepted a complete controller-owned planning DAG. '
                      'Individual nodes cannot bypass scatter/gather, leases or Chapter 06 routing.')
        return record

    def status(self, job_id):
        kind, workflow_id = self._identity(job_id)
        workflow = self._controller().call(('/coding/workflow/' if kind == 'code' else '/workflow/') + workflow_id)
        return self._record(workflow, kind)

    def result(self, job_id, offset=0, limit=16000):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 65536:
            raise ValueError('Result paging requires offset >= 0 and limit 1..65536')
        record = self.status(job_id)
        if record['status'] != 'completed':
            return {key: value for key, value in {**record, 'content': None, 'next_offset': None}.items()
                    if key != 'workflow'}
        content = json.dumps(record.pop('workflow'), ensure_ascii=False, sort_keys=True)
        end = min(len(content), offset+limit)
        return {**record, 'content': content[offset:end], 'total_characters': len(content),
                'next_offset': end if end < len(content) else None}

    def cancel(self, job_id):
        kind, workflow_id = self._identity(job_id)
        workflow = self._controller().call('/coding/cancel' if kind == 'code' else '/cancel',
                                             {'workflow_id': workflow_id})
        return self._record(workflow, kind)

    def close(self):
        if self._client is not None:
            self._client.close()
        self._closed = True
