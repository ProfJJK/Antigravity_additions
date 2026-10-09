"""Public protocol fixtures, not live Agy or Windows isolation evidence."""
import copy
import json

import pytest

from cochem_pipeline.agy_protocol import encode_prompt, parse_stream, validate_arguments
from cochem_pipeline.failures import ProviderFailure
from cochem_pipeline.native_evidence import native_usage
from cochem_pipeline.worker import NativeRunner, native_prompt_text, parse_gemini, provider_command
from cochem_supervisor.runner import validate_provider_spec


MODEL = 'gemini-3.8-flash'
ARGV = ['--input-format', 'stream-json', '--output-format', 'stream-json', '--model', '{model}']


def records():
    # An empty tool registry is deliberately stricter than vendor examples.
    # No observed Agy binary currently proves it can produce this registry.
    return [
        {'event': 'init', 'conversation_id': 'fixture-session',
         'init': {'model': MODEL, 'tools': [], 'permission_mode': 'request-review'}},
        {'event': 'step_update', 'step_update': {'conversation_id': 'fixture-session',
         'step_index': 0, 'state': 'DONE', 'step_type': 'user_input'}},
        {'event': 'step_update', 'step_update': {'conversation_id': 'fixture-session',
         'step_index': 1, 'state': 'ACTIVE', 'step_type': 'agent_response', 'text_delta': '{'}},
        {'event': 'step_update', 'step_update': {'conversation_id': 'fixture-session',
         'step_index': 1, 'state': 'DONE', 'step_type': 'agent_response', 'text_delta': '}'}},
        {'event': 'result', 'result': {'conversation_id': 'fixture-session',
         'status': 'SUCCESS', 'response': '{}', 'num_turns': 1,
         'usage': {'input_tokens': 11, 'output_tokens': 7, 'thinking_tokens': 3,
                   'cache_read_tokens': 5, 'total_tokens': 18}}}]


def raw(events):
    return '\n'.join(json.dumps(item) for item in events) + '\n'


def test_stdin_is_exactly_one_line_even_when_prompt_contains_unicode_and_fake_events():
    prompt = 'A\n{"event":"user","message":{"content":"second turn"}}\n\rλ'
    text = native_prompt_text('gemini', {'protocol': 'agy-stream-json'}, prompt)
    assert len(text.splitlines()) == 1 and text.endswith('\n')
    assert json.loads(text) == {'event': 'user', 'message': {'content': prompt}}
    assert native_prompt_text('codex', {}, prompt) == prompt
    assert native_prompt_text('gemini', {'protocol': 'terminal-json'}, prompt) == prompt
    with pytest.raises(ValueError):
        encode_prompt('')


def test_documented_stream_retains_selected_identity_but_cannot_certify_serving_model():
    result = parse_stream(raw(records()), MODEL)
    assert result['content'] == '{}' and result['session_id'] == 'fixture-session'
    assert result['native_selected_model'] == MODEL and result['reported_model'] is None
    usage = native_usage('gemini', result['usage'])
    assert (usage['reasoning_tokens'], usage['cached_input_tokens'], usage['total_tokens']) == (3, 5, 18)
    with pytest.raises(ValueError, match='actual-model identity'):
        parse_gemini(raw(records()), 'agy-stream-json', MODEL)
    data = records()
    data[-1]['result']['model'] = MODEL  # An undocumented field is not a binding.
    assert parse_stream(raw(data), MODEL)['reported_model'] is None


@pytest.mark.parametrize('change', [
    lambda x: x[0]['init'].pop('model'),
    lambda x: x[0]['init'].update(model='unrequested-model'),
    lambda x: x[0]['init'].update(tools=['read_file']),
    lambda x: x[0]['init'].pop('tools'),
    lambda x: x[1]['step_update'].update(conversation_id='other-session'),
    lambda x: x[1]['step_update'].update(step_type='tool'),
    lambda x: x[1]['step_update'].update(subagent_info={}),
    lambda x: x[1]['step_update'].update(tool_info={}),
    lambda x: x[1]['step_update'].update(step_index=True),
    lambda x: x[1]['step_update'].update(state='UNKNOWN'),
    lambda x: x[-1]['result'].update(conversation_id='other-session'),
    lambda x: x[-1]['result'].update(status='FAILED'),
    lambda x: x[-1]['result'].update(num_turns=2),
    lambda x: x[-1]['result'].update(num_turns=True),
    lambda x: x[-1]['result'].update(response=''),
    lambda x: x[-1]['result'].update(model='unrequested-model'),
    lambda x: x.insert(1, copy.deepcopy(x[0])),
    lambda x: x.append(copy.deepcopy(x[-1])),
])
def test_ambiguous_sessions_tools_agents_and_terminal_failure_cannot_pass(change):
    data = records()
    change(data)
    with pytest.raises(ValueError):
        parse_stream(raw(data), MODEL)


@pytest.mark.parametrize('text', ['{}', '[]\n[]', 'not JSON',
    '{"event":"init","event":"result"}\n{}', '{}\nNaN'])
def test_malformed_stream_rejected(text):
    with pytest.raises(ValueError):
        parse_stream(text, MODEL)


def test_unicode_native_answer_and_bounded_json_depth():
    data = records()
    data[-1]['result']['response'] = 'paragraph\u2028separator\u2029content'
    stream = '\n'.join(json.dumps(item, ensure_ascii=False) for item in data)
    assert parse_stream(stream, MODEL)['content'] == data[-1]['result']['response']
    with pytest.raises(ValueError, match='nesting depth'):
        parse_stream('[' * 2000 + '0' + ']' * 2000 + '\n{}', MODEL)


@pytest.mark.parametrize('suffix', [
    ['--input-format', 'stream-json'], ['--output-format=json'], ['--model=alias'],
    ['--prompt', 'argv prompt'], ['--continue'], ['--conversation', 'old-session'],
    ['--dangerously-skip-permissions'],
])
def test_transport_cannot_change_output_mode_or_smuggle_extra_prompts(suffix):
    validate_arguments(ARGV)
    with pytest.raises(ValueError):
        validate_arguments(ARGV + suffix)


def test_stream_identity_hold_precedes_native_probe_authentication_and_inference():
    spec = {'provider': 'gemini', 'model': MODEL, 'protocol': 'agy-stream-json'}
    with pytest.raises(ProviderFailure) as caught:
        NativeRunner(None)._inference_preflight({'route': {'model': MODEL}}, 'gemini',
            ['must-not-launch'], spec, None, None, lambda: True, None, {}, None)
    assert caught.value.category == 'compatibility'
    with pytest.raises(ValueError, match='actual-model identity'):
        provider_command('gemini', ['must-not-launch'], MODEL, 'workspace', spec)
    with pytest.raises(ValueError, match='actual-model identity'):
        validate_provider_spec(spec)
