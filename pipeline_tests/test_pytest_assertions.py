"""Actual ordinary Python/pytest execution, without Docker or SYSTEM claims."""
import hashlib
import subprocess
import sys

import pytest

from cochem_pipeline.containers import junit_cases, parse_junit
from cochem_pipeline.pytest_assertions import ASSERTION_OBSERVER, bind_assertion_evidence, executed_assertion_failure


@pytest.mark.parametrize('body,accepted', [
    ('assert 1 == 2', True),
    ('raise RuntimeError("before assertion")\n    assert 1 == 2', False),
    ('raise RuntimeError("AssertionError: assert 1 == 2")', False),
    ('raise AssertionError("explicit exception is not an executed assert")', False),
    ('assert True; raise AssertionError("same line is not a failing assert")', False),
    ('pytest.fail("AssertionError: assert False")', False),
    ('assert 1 == 1', False),
    ('pytest.skip("not executed")', False),
    ('assert (\n        1 == 2\n    )', True),
])
def test_physical_call_exception_requires_executed_assertion(tmp_path, body, accepted):
    source = ('import pytest\ndef test_regression():\n    ' + body + '\n').encode()
    (tmp_path / 'test_regression.py').write_bytes(source)
    report, sidecar = tmp_path / 'junit.xml', tmp_path / 'assertions.json'
    process = subprocess.run([sys.executable, '-I', '-c', ASSERTION_OBSERVER, str(sidecar), str(tmp_path),
        str(tmp_path / 'test_regression.py'), '--rootdir=' + str(tmp_path), '--junitxml=' + str(report),
        '-p', 'no:cacheprovider', '-q'], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert process.returncode in (0, 1), process.stdout + process.stderr
    identity = {'path': 'test_regression.py', 'file_sha256': hashlib.sha256(source).hexdigest()}
    cases = bind_assertion_evidence(junit_cases(report.read_bytes()), sidecar.read_bytes(),
                                  {'test_regression.py': {'sha256': identity['file_sha256']}})
    assert len(cases) == 1
    assert executed_assertion_failure(cases[0], identity) is accepted
    if accepted:
        assert parse_junit(report.read_bytes())['failures'] == 1
        assert cases[0]['assertion_failure']['line'] > 0
        assert not executed_assertion_failure(cases[0], {**identity, 'file_sha256': 'f' * 64})
        with pytest.raises(ValueError, match='sealed source'):
            bind_assertion_evidence(junit_cases(report.read_bytes()), sidecar.read_bytes(),
                                    {'test_regression.py': {'sha256': 'f' * 64}})


def test_junit_exception_label_alone_cannot_attest_an_assertion():
    cases = junit_cases(b'<testsuite><testcase classname="test_spoof" name="test_regression">'
        b'<failure type="AssertionError" message="assert False">AssertionError: assert False</failure>'
        b'</testcase></testsuite>')
    assert not executed_assertion_failure(cases[0], {'path': 'test_spoof.py', 'file_sha256': 'a' * 64})


def test_actual_parameterized_class_assertions_bind_exact_junit_names(tmp_path):
    source = b'import pytest\nclass TestCheck:\n @pytest.mark.parametrize("value", [1], ids=["part::other"])\n def test_value(self, value):\n  assert value == 2\n'
    (tmp_path / 'test_parameter.py').write_bytes(source)
    report, sidecar = tmp_path / 'junit.xml', tmp_path / 'assertions.json'
    result = subprocess.run([sys.executable, '-I', '-c', ASSERTION_OBSERVER, str(sidecar), str(tmp_path),
        'test_parameter.py', '--rootdir=' + str(tmp_path), '--junitxml=' + str(report), '-q'],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    identity = {'path': 'test_parameter.py', 'file_sha256': hashlib.sha256(source).hexdigest()}
    cases = bind_assertion_evidence(junit_cases(report.read_bytes()), sidecar.read_bytes(),
                                    {'test_parameter.py': {'sha256': identity['file_sha256']}})
    assert cases[0]['class_name'] == 'test_parameter.TestCheck'
    assert cases[0]['name'] == 'test_value[part::other]'
    assert executed_assertion_failure(cases[0], identity)
