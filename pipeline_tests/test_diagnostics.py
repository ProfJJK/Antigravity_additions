"""Prompt bounds over real serialized evidence, without claiming native inference."""
import hashlib
import json

import pytest

from cochem_pipeline.diagnostics import bounded_coding_diagnostics


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode()


def test_large_unicode_failure_evidence_is_bounded_without_changing_task_authority():
    receipt = {'passed':False,'failure_category':'tests_failed','commands':[
        {'name':'tests','exit_code':1,'junit':{'tests':4,'failures':1,'errors':0},
         'stdout':'故障\n'*100000,'stderr':'AssertionError: expected 2, observed 1'}]}
    payload = {'objective':'Implement R1','requirements':['R1'],'allowed_paths':['src'],
               'test_receipt':receipt,'failure_evidence_sha256':'a'*64}
    result = bounded_coding_diagnostics(payload)
    assert result['objective']==payload['objective'] and result['requirements']==['R1']
    assert result['failure_evidence_sha256']=='a'*64 and result['allowed_paths']==['src']
    assert 'test_receipt' not in result and payload['test_receipt']==receipt
    evidence = result['diagnostics']
    assert len(encoded(evidence)) == evidence['utf8_bytes'] < 2000
    assert evidence['sources']['test_receipt']['sha256'] == hashlib.sha256(encoded(receipt)).hexdigest()
    assert any('AssertionError' in excerpt['text'] for excerpt in evidence['excerpts'])


def test_oversized_nested_summaries_and_multiple_failures_cannot_escape_budget():
    payload = {'requirements':['R1'],'test_receipt':{'commands':[
        {'name':'x'*50000,'junit':{'tests':'malformed'*10000},'stderr':'y'*10000}]},
        'last_test':{'error':'z'*50000},'previous_failure':'bad'*50000,
        'failures':[{'reason':'!'*50000}]*3,'research_dossier':{'strategy':'s'*50000}}
    result = bounded_coding_diagnostics(payload)
    assert len(encoded(result['diagnostics'])) < 2000
    assert len(result['diagnostics']['sources'])==5


def test_projection_leaves_a_first_assignment_intact_and_rejects_nonfinite_evidence():
    original={'objective':'Start','requirements':['R1']}
    assert bounded_coding_diagnostics(original)==original
    with pytest.raises(ValueError):
        bounded_coding_diagnostics({'test_receipt':{'time':float('nan')}})
