"""Physical protected-suite bootstrap processes and strict XML report contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from cochem_supervisor.acceptance import AcceptanceError, validate_junit


def report_file(tmp_path, *, cases=2, skipped=0, failures=0, errors=0):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(cases), skipped=str(skipped),
                          failures=str(failures), errors=str(errors))
    for number in range(cases):
        case = ET.SubElement(suite, "testcase", classname="pipeline_tests.test_contract",
                             name=f"test_case_{number}")
        if number < failures:
            ET.SubElement(case, "failure", message="A real assertion would be here")
        elif number < failures + errors:
            ET.SubElement(case, "error", message="A real error would be here")
        elif number < failures + errors + skipped:
            ET.SubElement(case, "skipped", message="Unavailable Windows contract")
    path = tmp_path / "report.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def child_layout(tmp_path):
    candidate = tmp_path / "frozen"
    tests = tmp_path / "acceptance"
    workspace = tmp_path / "workspace"
    for root in (candidate, tests, workspace):
        root.mkdir()
    for package in ("cochem_pipeline", "cochem_mcp", "cochem"):
        directory = candidate / "src" / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("SENTINEL = 'frozen-candidate'\n", encoding="utf-8")
    (tests / "pipeline_tests").mkdir()
    (tests / "pipeline_tests/__init__.py").write_text("")
    (tests / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tests / "pipeline_tests/test_imports.py").write_text('''
import subprocess
import sys
import cochem_pipeline
import cochem_mcp
def test_candidate_loaded():
    assert cochem_pipeline.SENTINEL == "frozen-candidate"
    assert cochem_mcp.SENTINEL == "frozen-candidate"
def test_children_use_same_candidate():
    child = subprocess.run([sys.executable, "-c", "import cochem_pipeline; print(cochem_pipeline.SENTINEL)"],
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "frozen-candidate"
''', encoding="utf-8")
    return candidate, tests, workspace


def run_child(candidate, tests, workspace, *, targets=("pipeline_tests",), extra_env=None):
    command = [sys.executable, "-I", "-m", "cochem_supervisor.acceptance", "--candidate", str(candidate),
               "--tests", str(tests), "--report", str(workspace / "result.xml"),
               "--scratch", str(workspace / "scratch"), "--targets", *targets]
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(command, capture_output=True, text=True, cwd=workspace, env=env, timeout=30)


def test_real_isolated_bootstrap_uses_candidate_for_parent_and_child(tmp_path):
    candidate, tests, workspace = child_layout(tmp_path)
    process = run_child(candidate, tests, workspace)
    assert process.returncode == 0, process.stdout + process.stderr
    checked = validate_junit(workspace / "result.xml", min_passed=2, max_skipped=0)
    assert checked["passed"] == 2
    assert checked["test_ids"] == [
        "pipeline_tests.test_imports::test_candidate_loaded",
        "pipeline_tests.test_imports::test_children_use_same_candidate",
    ]
    assert not list(candidate.rglob("__pycache__"))
    assert not list(tests.rglob("__pycache__"))
    assert not (tests / ".pytest_cache").exists()


def test_candidate_pytest_config_conftest_and_inherited_options_are_ignored(tmp_path):
    candidate, tests, workspace = child_layout(tmp_path)
    (candidate / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    (candidate / "conftest.py").write_text("raise RuntimeError('candidate conftest must not execute')\n")
    (workspace / "conftest.py").write_text("raise RuntimeError('workspace conftest must not execute')\n")
    process = run_child(candidate, tests, workspace, extra_env={
        "PYTEST_ADDOPTS": "--collect-only", "PYTEST_PLUGINS": "nonexistent_hostile_plugin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "0", "PYTHONPATH": str(workspace),
    })
    assert process.returncode == 0, process.stdout + process.stderr
    assert validate_junit(workspace / "result.xml", 2, 0)["passed"] == 2


def test_bootstrap_does_not_fall_back_to_installed_baseline_package(tmp_path):
    candidate, tests, workspace = child_layout(tmp_path)
    (candidate / "src/cochem_pipeline/__init__.py").unlink()
    process = run_child(candidate, tests, workspace)
    assert process.returncode != 0
    assert "outside the frozen candidate" in process.stderr
    assert not (workspace / "result.xml").exists()


def test_failed_real_test_preserves_nonzero_exit_and_failed_report(tmp_path):
    candidate, tests, workspace = child_layout(tmp_path)
    (candidate / "src/cochem_mcp/__init__.py").write_text("SENTINEL = 'incorrect'\n")
    process = run_child(candidate, tests, workspace)
    assert process.returncode == 1
    with pytest.raises(AcceptanceError, match="failed or errored"):
        validate_junit(workspace / "result.xml", 1, 0)


@pytest.mark.parametrize("target", ["../pipeline_tests", "tests", "pipeline_tests/../../escape.py", "-p", "mcp_tests\\test_bad.py"])
def test_bootstrap_refuses_unapproved_targets(tmp_path, target):
    candidate, tests, workspace = child_layout(tmp_path)
    process = run_child(candidate, tests, workspace, targets=(target,))
    assert process.returncode == 2
    assert not (workspace / "result.xml").exists()


def test_bootstrap_refuses_existing_report_or_scratch_and_preserves_them(tmp_path):
    candidate, tests, workspace = child_layout(tmp_path)
    report = workspace / "result.xml"
    report.write_text("must not overwrite earlier evidence")
    process = run_child(candidate, tests, workspace)
    assert process.returncode == 2
    assert report.read_text() == "must not overwrite earlier evidence"


def test_bootstrap_refuses_overlapping_protected_paths(tmp_path):
    candidate, _, workspace = child_layout(tmp_path)
    process = run_child(candidate, candidate, workspace)
    assert process.returncode == 2
    assert "disjoint" in process.stderr


def test_junit_accepts_exact_cases_and_policy(tmp_path):
    path = report_file(tmp_path, cases=3, skipped=1)
    result = validate_junit(path, min_passed=2, max_skipped=1)
    assert {key: result[key] for key in ("tests", "passed", "skipped", "errors", "failures")} == {
        "tests": 3, "passed": 2, "skipped": 1, "errors": 0, "failures": 0,
    }
    assert result["validated"] is True
    assert len(result["test_ids"]) == 3


@pytest.mark.parametrize("options,minimum,maximum,message", [
    ({"cases": 1}, 2, 0, "minimum"),
    ({"cases": 2, "skipped": 1}, 1, 0, "maximum"),
    ({"cases": 2, "failures": 1}, 1, 0, "failed or errored"),
    ({"cases": 2, "errors": 1}, 1, 0, "failed or errored"),
    ({"cases": 0}, 1, 0, "minimum"),
])
def test_junit_rejects_failures_errors_too_few_tests_and_too_many_skips(tmp_path, options, minimum, maximum, message):
    with pytest.raises(AcceptanceError, match=message):
        validate_junit(report_file(tmp_path, **options), minimum, maximum)


@pytest.mark.parametrize("content", ["", "not xml", "<testsuites>", "<verdict>PERFECT</verdict>",
    '<!DOCTYPE x [<!ENTITY fake "pass">]><testsuite>&fake;</testsuite>',
    '<!DOCTYPE x SYSTEM "file:///etc/passwd"><testsuite/>'])
def test_junit_rejects_malformed_empty_model_verdict_or_dtd(tmp_path, content):
    path = tmp_path / "report.xml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AcceptanceError):
        validate_junit(path, 1, 0)


def test_utf16_cannot_bypass_dtd_rejection(tmp_path):
    path = tmp_path / "report.xml"
    path.write_bytes('<!DOCTYPE x [<!ENTITY fake "pass">]><testsuite>&fake;</testsuite>'.encode("utf-16"))
    with pytest.raises(AcceptanceError, match="UTF-8"):
        validate_junit(path, 1, 0)


@pytest.mark.parametrize("mutation", ["forged_count", "forged_skip", "duplicate", "outside_suite", "blank_name", "contradictory", "nested", "bad_integer", "aggregate"])
def test_junit_rejects_forged_inconsistent_or_ambiguous_results(tmp_path, mutation):
    path = report_file(tmp_path)
    tree = ET.parse(path)
    root = tree.getroot()
    suite = root[0]
    if mutation == "forged_count":
        suite.set("tests", "200")
    elif mutation == "forged_skip":
        suite.set("skipped", "1")
    elif mutation == "duplicate":
        suite[1].set("name", suite[0].get("name"))
    elif mutation == "outside_suite":
        suite[0].set("classname", "model_generated_tests.test_assert_true")
    elif mutation == "blank_name":
        suite[0].set("name", "")
    elif mutation == "contradictory":
        ET.SubElement(suite[0], "skipped")
        ET.SubElement(suite[0], "failure")
    elif mutation == "nested":
        ET.SubElement(suite, "testsuite")
    elif mutation == "bad_integer":
        suite.set("tests", "2.0")
    else:
        root.set("tests", "999")
    tree.write(path, encoding="utf-8")
    with pytest.raises(AcceptanceError):
        validate_junit(path, 1, 2)


def test_junit_missing_report_or_link_fails_closed(tmp_path):
    with pytest.raises(AcceptanceError, match="missing"):
        validate_junit(tmp_path / "absent.xml", 1, 0)
    actual = report_file(tmp_path)
    link = tmp_path / "linked.xml"
    try:
        link.symlink_to(actual)
    except OSError as error:
        pytest.skip(f"OS does not permit this symlink: {error}")
    with pytest.raises(AcceptanceError, match="plain"):
        validate_junit(link, 1, 0)


@pytest.mark.parametrize("minimum,maximum", [(0, 0), (True, 0), (1, -1), (1, False)])
def test_junit_policy_rejects_invalid_limits(tmp_path, minimum, maximum):
    with pytest.raises(AcceptanceError, match="count policy"):
        validate_junit(report_file(tmp_path), minimum, maximum)
