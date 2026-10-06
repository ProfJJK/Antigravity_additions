"""Native protocol parser fixtures; no live provider or subscription assertions."""
import json

import pytest

from cochem_pipeline.failures import ProviderFailure, parse_native_failure


@pytest.mark.parametrize(('provider','record','category','scope'), [
    ('codex', {'type':'turn.failed','error':{'code':'rate_limit_exceeded'}}, 'quota','pool'),
    ('codex', {'type':'error','message':"You've hit your usage limit. Try again later."}, 'quota','pool'),
    ('codex', {'type':'turn.failed','error':{'message':'Your input exceeds the context window of this model.'}}, 'context',None),
    ('claude', {'type':'result','subtype':'error_during_execution','is_error':True,'errors':["You've hit your limit · resets later"]}, 'quota','pool'),
    ('claude', {'type':'error','error':{'type':'authentication_error'}}, 'auth','pool'),
    ('claude', {'type':'result','subtype':'error_during_execution','is_error':True,'errors':[{'code':'overloaded_error'}]}, 'busy','model'),
    ('gemini', {'error':{'code':429,'status':'RESOURCE_EXHAUSTED'}}, 'quota','pool'),
    ('gemini', {'error':{'code':401}}, 'auth','pool'),
    ('gemini', {'error':{'code':503}}, 'busy','model'),
    ('gemini', {'error':{'code':502}}, 'provider','model'),
    ('codex', {'type':'turn.failed','error':{'code':'invalid_argument'}}, 'protocol',None),
])
def test_recognized_native_failure_envelopes(provider,record,category,scope):
    failure = parse_native_failure(provider,json.dumps(record),'',1)
    assert isinstance(failure,ProviderFailure)
    assert failure.category == category
    assert failure.hold_scope == scope
    assert record.get('message','DO NOT COPY RAW ERRORS') not in str(failure)


@pytest.mark.parametrize(('provider','record'), [
    ('codex', {'type':'item.completed','item':{'type':'agent_message','text':"You've hit your usage limit. 429 authentication_error"}}),
    ('codex', {'type':'item.completed','item':{'type':'command_execution','output':'{"type":"error","code":429}'}}),
    ('claude', {'type':'result','subtype':'success','is_error':False,'result':"You've hit your limit · resets later"}),
    ('claude', {'type':'assistant','message':{'content':[{'type':'text','text':'authentication_error'}]}}),
    ('gemini', {'response':'{"error":{"code":429}}','session_id':'fixture-session'}),
])
def test_generated_answers_and_tool_output_cannot_create_provider_holds(provider,record):
    assert parse_native_failure(provider,json.dumps(record),'',0) is None


@pytest.mark.parametrize('text', [
    "You've hit your usage limit.", 'Please log in',
    'Assistant says: {"type":"error","code":429}',
    'rate_limit_exceeded', '429 Authentication failed',
])
def test_unstructured_stderr_does_not_establish_availability(text):
    assert parse_native_failure('codex','',text,1) is None


def test_known_diagnostic_requires_anchor_inside_native_failure_envelope():
    data = {'type':'error','message':'The document says: You have hit your usage limit.'}
    assert parse_native_failure('codex',json.dumps(data),'',1).category == 'code'


def test_native_error_json_line_survives_unrelated_cli_warning():
    stderr = 'WARNING: optional shell integration unavailable\n'+json.dumps({'type':'error','error':{'status':429}})+'\n'
    assert parse_native_failure('codex','',stderr,1).category == 'quota'


def test_typed_cause_wins_over_native_diagnostic_text():
    data = {'type':'error','error':{'code':'authentication_error','message':"You've hit your usage limit."}}
    assert parse_native_failure('codex',json.dumps(data),'',1).category == 'auth'


@pytest.mark.parametrize(('hint','expected'), [(60,60.0),('12.5',12.5),(-7,0.0),(999999,86400.0),
                                               (True,None),('NaN',None),('Infinity',None),({},None)])
def test_retry_after_is_numeric_finite_and_bounded(hint,expected):
    record = {'type':'error','error':{'code':429,'retry_after_seconds':hint}}
    assert parse_native_failure('codex',json.dumps(record),'',1).retry_after_seconds == expected


def test_retry_info_duration_and_retry_after_header_are_supported():
    record = {'error':{'status':'RESOURCE_EXHAUSTED','headers':{'Retry-After':'17'},
                       'details':[{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'35.5s'}]}}
    assert parse_native_failure('gemini',json.dumps(record),'',1).retry_after_seconds == 35.5


def test_native_failed_terminal_remains_failure_on_zero_exit():
    data = {'type':'result','subtype':'error_during_execution','is_error':True,'errors':[{'type':'authentication_error'}]}
    assert parse_native_failure('claude',json.dumps(data),'',0).category == 'auth'


def test_context_failure_is_job_local_not_a_shared_provider_hold():
    failure = ProviderFailure('context')
    assert failure.hold_scope is None
    with pytest.raises(ValueError,match='globally'):
        ProviderFailure('context',hold_scope='pool')


def test_plain_native_messages_outside_allowlist_remain_execution_failures():
    failure = parse_native_failure('codex',json.dumps({'type':'turn.failed','error':{'message':'Internal tool diagnostic with private details'}}),'',1)
    assert failure.category == 'code'
    assert 'private details' not in str(failure)
