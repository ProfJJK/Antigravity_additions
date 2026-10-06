"""Physical byte/context and AST checks for the original coding SRS gates."""
from pathlib import Path

import pytest

from cochem_pipeline.coding import CodingProject,observed_changes,validate_leaf_chunk
from cochem_pipeline.coding_checks import validate_generated_files
from cochem_pipeline.diagnostics import bounded_coding_diagnostics
from cochem_pipeline.coding import digest


def project():
    return CodingProject('scope',Path('/tmp/registered-project'),'delivery',('src',),('tests',))


def source(lines=200):
    return {'src/main.py':''.join(f'value_{index} = {index}\n' for index in range(lines)).encode()}


def test_existing_short_file_has_no_whole_rewrite_exemption():
    before=source(19)
    after={'src/main.py':before['src/main.py'].replace(b'value_',b'other_')}
    with pytest.raises(ValueError,match='rewrite boundary'):
        observed_changes(before,after,before,project())


def test_source_and_tests_share_one_added_plus_deleted_line_ceiling():
    before=source()
    after={**before,'tests/test_behavior.py':b'def test_behavior():\n    assert True\n'+b'# context\n'*97}
    after['src/main.py']=before['src/main.py'].replace(b'value_99 = 99',b'value_99 = -1')
    # Each proposal is below its own limit; their actual combined diff is 101.
    with pytest.raises(ValueError,match='source/test Git diff'):
        validate_leaf_chunk(before,after,before,project())


def test_small_actual_edit_uses_existing_context_without_padding_source():
    before=source()
    after={**before,'tests/test_behavior.py':b'def test_behavior():\n    assert True\n'}
    after['src/main.py']=before['src/main.py'].replace(b'value_99 = 99',b'value_99 = -1')
    evidence=validate_leaf_chunk(before,after,before,project())
    assert evidence['changed_lines']==4
    assert evidence['context_lines']==22
    assert len(after['src/main.py'].splitlines())==200
    window=next(item for item in evidence['context_windows'] if item['path']=='src/main.py')
    assert window['start_line']<=100<=window['end_line'] and window['line_count']==20


@pytest.mark.parametrize('lines,indices',[(19,[9]),(200,[1,198])])
def test_actual_context_minimum_and_maximum_require_refracture(lines,indices):
    before=source(lines);after=dict(before)
    for index in indices:
        after['src/main.py']=after['src/main.py'].replace(f'value_{index} = {index}'.encode(),f'value_{index} = -1'.encode())
    with pytest.raises(ValueError,match='physical context'):
        validate_leaf_chunk(before,after,before,project())


def test_missing_final_newline_counts_as_actual_git_line_replacement():
    before=source(30);after={'src/main.py':before['src/main.py'].rstrip(b'\n')}
    assert validate_leaf_chunk(before,after,before,project())['changed_lines']==2


@pytest.mark.parametrize('code',[
    'from unittest import mock as replacement\n',
    'import unittest as u\nx=u.mock.patch("module.function")\n',
    'def test_real(monkeypatch):\n    assert True\n',
    'import importlib\nx=importlib.import_module("unittest." + "mock")\n',
    'import base64\nx=__import__(base64.b64decode("dW5pdHRlc3QubW9jaw==").decode())\n',
    'def unfinished():\n    """Pending routine."""\n    pass\n',
    'async def unfinished():\n    ...\n',
    'def unfinished():\n    raise NotImplementedError("pending")\n',
])
def test_generated_mock_obfuscation_and_stubs_are_rejected(code):
    with pytest.raises(ValueError,match='Zero-mock|Unfinished'):
        validate_generated_files({'tests/test_code.py':code.encode()},['tests/test_code.py'])


def test_real_function_and_failure_assertion_are_permitted():
    code=b'def add(left,right):\n    return left+right\ndef test_add():\n    assert add(1,2)==4\n'
    validate_generated_files({'tests/test_code.py':code},['tests/test_code.py'])


def test_all_repair_fields_share_a_single_diagnostic_budget():
    import json
    raw={key:['failure '*5000] for key in ('minor_findings','repair_findings','findings','debug_notes')}
    result=bounded_coding_diagnostics({'requirements':['R1'],**raw})
    assert all(key not in result for key in raw)
    assert len(json.dumps(result['diagnostics'],ensure_ascii=False).encode())<2000
    assert result['diagnostics']['source_sha256']==digest(raw)
