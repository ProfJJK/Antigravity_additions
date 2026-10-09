"""Controller-owned pytest observer; exception prose is never RED evidence.

The program is passed to isolated Python in the test container. Its sidecar
records actual call-phase exceptions and the innermost executed assertion,
bound to source bytes. Semantic relevance remains the independent plan review's
responsibility; this observer establishes execution, not requirement correctness.
"""
from __future__ import annotations

import json


ASSERTION_OBSERVER = r'''
import ast, dis, hashlib, json, pathlib, sys
import pytest
report_path, root_path = sys.argv[1:3]
root = pathlib.Path(root_path).resolve()
observed = []
class Observer:
    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        evidence = None
        if call.when == 'call' and call.excinfo is not None and call.excinfo.type is AssertionError:
            entry = call.excinfo.traceback[-1]
            path = pathlib.Path(str(entry.path)).resolve()
            try:
                relative = path.relative_to(root).as_posix()
                source = path.read_bytes()
                line = entry.lineno + 1
                traceback = call.excinfo.value.__traceback__
                while traceback.tb_next is not None:
                    traceback = traceback.tb_next
                position = next((instruction.positions for instruction in dis.get_instructions(traceback.tb_frame.f_code)
                                 if instruction.offset == traceback.tb_lasti), None)
                assertions = [node for node in ast.walk(ast.parse(source))
                              if isinstance(node, ast.Assert) and position is not None
                              and position.lineno is not None and position.col_offset is not None
                              and (node.lineno, node.col_offset) <= (position.lineno, position.col_offset)
                              and position.end_lineno is not None and position.end_col_offset is not None
                              and (position.end_lineno, position.end_col_offset) <= (node.end_lineno, node.end_col_offset)]
                if assertions:
                    evidence = {'phase': 'call', 'exception_type': 'AssertionError',
                                'path': relative, 'line': line,
                                'source_sha256': hashlib.sha256(source).hexdigest()}
            except (ValueError, OSError, SyntaxError, UnicodeError):
                pass
        outcome = yield
        report = outcome.get_result()
        if report.when == 'call':
            observed.append({'nodeid': item.nodeid, 'outcome': report.outcome,
                             'assertion': evidence if report.failed else None})
observer = Observer()
code = pytest.main(sys.argv[3:], plugins=[observer])
pathlib.Path(report_path).write_text(json.dumps(observed, sort_keys=True, separators=(',', ':')), encoding='utf-8')
raise SystemExit(code)
'''


def bind_assertion_evidence(cases, data: bytes, source_files: dict):
    """Bind observed call exceptions to exact JUnit identities and source hashes."""
    records = json.loads(data)
    if not isinstance(records, list):
        raise ValueError('Invalid pytest assertion evidence')
    by_identity = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('nodeid'), str):
            raise ValueError('Invalid pytest assertion identity')
        address, separator, parameters = record['nodeid'].partition('[')
        parts = address.split('::')
        if len(parts) < 2 or not parts[0].endswith('.py'):
            continue
        identity = ('.'.join([parts[0][:-3].replace('/', '.'), *parts[1:-1]]), parts[-1] + separator + parameters)
        if identity in by_identity:
            raise ValueError('Duplicate pytest assertion identity')
        by_identity[identity] = record
    for case in cases:
        record = by_identity.get((case['class_name'], case['name']))
        assertion = record.get('assertion') if record else None
        if case['status'] != 'failed' or not record or record.get('outcome') != 'failed' or not assertion:
            continue
        if (not isinstance(assertion, dict) or assertion.get('phase') != 'call'
                or assertion.get('exception_type') != 'AssertionError'
                or type(assertion.get('line')) is not int or assertion['line'] <= 0
                or source_files.get(assertion.get('path'), {}).get('sha256') != assertion.get('source_sha256')):
            raise ValueError('Pytest assertion evidence does not match sealed source')
        case['assertion_failure'] = assertion
    return cases


def executed_assertion_failure(case, identity):
    assertion = case.get('assertion_failure')
    return (case.get('status') == 'failed' and isinstance(assertion, dict)
            and assertion.get('phase') == 'call' and assertion.get('exception_type') == 'AssertionError'
            and assertion.get('path') == identity['path']
            and assertion.get('source_sha256') == identity['file_sha256']
            and type(assertion.get('line')) is int and assertion['line'] > 0)
