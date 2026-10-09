"""Agy's documented NDJSON transport, not a deployable isolation contract.

The init event reports the selected model, not a server attestation of which
model served the request. Authentication, inference isolation and model identity
remain separate required deployment evidence; this module cannot supply them.
"""
from __future__ import annotations

import json


PROTOCOL = 'agy-stream-json'


def require_serving_model_contract(spec):
    """Keep unsupported identity semantics held before any native dispatch."""
    if spec.get('protocol') == PROTOCOL:
        raise ValueError('Agy stream actual-model identity has no verified native contract')


def validate_arguments(arguments):
    """Bind a one-prompt stdin transport without conversation/argv prompt reuse."""
    if (not isinstance(arguments, list) or not 1 <= len(arguments) <= 64
            or any(not isinstance(arg, str) or '\0' in arg or len(arg) > 8192 for arg in arguments)):
        raise ValueError('Agy stream transport requires bounded argv strings')
    for flag, value in (('--input-format', 'stream-json'),
                        ('--output-format', 'stream-json'), ('--model', '{model}')):
        if (arguments.count(flag) != 1 or arguments.index(flag) + 1 >= len(arguments)
                or arguments[arguments.index(flag) + 1] != value
                or any(arg.startswith(flag + '=') for arg in arguments)):
            raise ValueError('Agy stream transport requires exact input/output format and model selectors')
    forbidden = {'-p', '--print', '--prompt', '-i', '--prompt-interactive',
                 '-c', '--continue', '--conversation', '--dangerously-skip-permissions'}
    if any(arg.split('=', 1)[0] in forbidden for arg in arguments):
        raise ValueError('Agy stream transport must receive one fresh prompt only through stdin')


def encode_prompt(prompt):
    # JSON escaping makes embedded newlines and user-looking events data, not
    # additional turns. The owned pipe closes after this one line is delivered.
    if not isinstance(prompt, str) or not prompt:
        raise ValueError('Agy requires one nonempty controller prompt')
    return json.dumps({'event': 'user', 'message': {'content': prompt}},
                      ensure_ascii=False, separators=(',', ':')) + '\n'


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate Agy native JSON member')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Invalid Agy native JSON constant')
    def bound_depth(value, depth=0):
        if depth > 32:
            raise ValueError('Agy native JSON exceeds the supported nesting depth')
        if isinstance(value, dict):
            for child in value.values():
                bound_depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                bound_depth(child, depth + 1)
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        bound_depth(value)
        return value
    except RecursionError as exc:
        raise ValueError('Agy native JSON exceeds the supported nesting depth') from exc


def parse_stream(raw, requested_model):
    """Validate one native conversation; never infer identity from answer prose."""
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Agy native output exceeds its bounded stream contract')
    if not isinstance(requested_model, str) or not requested_model.strip():
        raise ValueError('Agy requires an exact selected model')
    # NDJSON delimiters are LF; Unicode paragraph separators inside JSON text
    # are content and must not split an otherwise valid native record.
    lines = [line for line in raw.split('\n') if line.strip()]
    if not 2 <= len(lines) <= 32768:
        raise ValueError('Agy must report one bounded native conversation')
    events = [_json(line) for line in lines]
    if (any(not isinstance(event, dict) for event in events)
            or events[0].get('event') != 'init' or events[-1].get('event') != 'result'):
        raise ValueError('Agy stream requires initial and terminal native events')
    first, terminal = events[0], events[-1].get('result')
    init = first.get('init')
    session = first.get('conversation_id')
    if (not isinstance(session, str) or not 0 < len(session.strip()) <= 512
            or any(ord(char) < 32 for char in session) or not isinstance(init, dict)
            or init.get('model') != requested_model):
        raise ValueError('Agy native selected model and session must match the controller request')
    if init.get('tools') != []:
        raise ValueError('Agy stream did not attest an empty native tool registry')
    for event in events[1:-1]:
        step = event.get('step_update')
        if (event.get('event') != 'step_update' or not isinstance(step, dict)
                or step.get('conversation_id') != session
                or step.get('step_type') not in ('user_input', 'agent_response', 'checkpoint')
                or step.get('state') not in ('ACTIVE', 'DONE')
                or type(step.get('step_index')) is not int or step['step_index'] < 0
                or any(key in step for key in ('tool_name', 'tool_info', 'subagent_info'))
                or ('model' in step and step['model'] != requested_model)):
            raise ValueError('Agy stream contains unverified steps, tools, agents or model identity')
    if (not isinstance(terminal, dict) or terminal.get('conversation_id') != session
            or terminal.get('status') != 'SUCCESS'
            or type(terminal.get('num_turns')) is not int or terminal['num_turns'] != 1
            or terminal.get('error') or terminal.get('permission_denials')
            or not isinstance(terminal.get('response'), str) or not terminal['response'].strip()
            or ('model' in terminal and terminal['model'] != requested_model)):
        raise ValueError('Agy must report one successful result for the same native conversation')
    # init.model is configuration metadata. No terminal model field has a
    # verified public contract, so even a field with that spelling cannot
    # manufacture a serving-model receipt. The caller must retain its hold.
    return {'content': terminal['response'], 'session_id': session,
            'native_selected_model': init['model'], 'reported_model': None,
            'terminal_success': True,
            'usage': terminal.get('usage') if isinstance(terminal.get('usage'), dict) else {}}
