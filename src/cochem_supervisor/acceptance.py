"""Protected acceptance bootstrap and strict, bounded JUnit receipt validation.

The bootstrap is installed independently of the candidate and must be invoked
with ``python -I -m cochem_supervisor.acceptance`` under the repair identity.
The supervising process, not this test process, owns the execution deadline and
must verify its actual exit code and close its entire Job Object before reading
the report. Passing this suite is one deployment gate, not model attestation or
a substitute for the independent post-deployment workflow probe.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import sys
from collections.abc import Sequence
import xml.etree.ElementTree as ET

from .releases import ReleaseError, _plain_ancestors, _stat_plain, tree_manifest


MAX_REPORT_BYTES = 16 * 1024 * 1024
_TARGET = re.compile(r"(?:pipeline_tests|mcp_tests)(?:/[A-Za-z0-9_]+\.py)?\Z")
_COUNT = re.compile(r"0|[1-9][0-9]*\Z")


class AcceptanceError(ValueError):
    """The independent acceptance run is missing or does not meet policy."""


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _source_import(name: str, source: Path) -> None:
    module = importlib.import_module(name)
    filename = getattr(module, "__file__", None)
    if not isinstance(filename, str) or source not in Path(filename).resolve().parents:
        raise AcceptanceError(f"Acceptance imported {name} from outside the frozen candidate")


def run_acceptance(candidate: Path, tests: Path, report: Path, scratch: Path,
                   targets: Sequence[str]) -> int:
    """Run only independently installed suites against explicit candidate imports."""
    candidate, tests, report, scratch = (Path(path).absolute() for path in
                                          (candidate, tests, report, scratch))
    if _overlaps(candidate, tests):
        raise AcceptanceError("Candidate and protected acceptance paths must be disjoint")
    for writable in (report, scratch):
        if any(_overlaps(writable, protected) for protected in (candidate, tests)):
            raise AcceptanceError("Acceptance outputs must be outside both protected source trees")
        _plain_ancestors(writable)
        if writable.exists() or writable.is_symlink():
            raise AcceptanceError("Acceptance output and scratch paths must not already exist")
    if _overlaps(report, scratch):
        raise AcceptanceError("Acceptance report and scratch paths must be disjoint")
    if (isinstance(targets, (str, bytes)) or not targets or len(set(targets)) != len(targets)
            or any(not isinstance(target, str) or not _TARGET.fullmatch(target) for target in targets)):
        raise AcceptanceError("Acceptance targets must be unique protected pipeline_tests/mcp_tests paths")
    tree_manifest(candidate)
    tree_manifest(tests)
    source = candidate / "src"
    _stat_plain(source, directory=True)
    config = tests / "pytest.ini"
    _stat_plain(config)
    selected = [tests / target for target in targets]
    for target in selected:
        if not target.exists():
            raise AcceptanceError("A protected acceptance target is missing")
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        os.environ.pop(name, None)
    # Child-process tests must import this same candidate, not installed baseline.
    os.environ["PYTHONPATH"] = os.pathsep.join((str(source), str(tests)))
    sys.path[:0] = [str(source), str(tests)]
    importlib.invalidate_caches()
    for name in ("cochem_pipeline", "cochem_mcp", "cochem"):
        _source_import(name, source)
    os.chdir(tests)
    import pytest
    return int(pytest.main([
        "-c", str(config), "--rootdir", str(tests), "--confcutdir", str(tests),
        "--import-mode=importlib", "-p", "no:cacheprovider", "-o", "addopts=",
        "-o", "junit_family=xunit2", "--basetemp", str(scratch), "--junitxml", str(report),
        *map(str, selected),
    ]))


def _declared_count(element: ET.Element, name: str) -> int:
    raw = element.get(name)
    if raw is None or not _COUNT.fullmatch(raw):
        raise AcceptanceError(f"JUnit {name} must be a nonnegative integer")
    count = int(raw)
    if count > 100_000:
        raise AcceptanceError("JUnit count exceeds the acceptance report limit")
    return count


def validate_junit(path: Path, min_passed: int, max_skipped: int) -> dict:
    """Require actual named cases, exact counts, zero errors/failures and policy limits.

    This validates the protected pytest run's report. The controller must also
    require an actual zero process exit and enforce an independent timeout; XML
    cannot prove that the process exited or that no descendant is still running.
    """
    if type(min_passed) is not int or min_passed < 1 or type(max_skipped) is not int or max_skipped < 0:
        raise AcceptanceError("Acceptance count policy must use positive passes and nonnegative skips")
    path = Path(path).absolute()
    try:
        _plain_ancestors(path)
        info = _stat_plain(path)
        if info.st_size < 1 or info.st_size > MAX_REPORT_BYTES:
            raise AcceptanceError("JUnit report is empty or exceeds its size limit")
        with path.open("rb") as handle:
            raw = handle.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise AcceptanceError("JUnit report exceeds its size limit")
    except (OSError, ReleaseError) as error:
        raise AcceptanceError("JUnit report is missing or is not a plain protected-run file") from error
    try:
        document = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise AcceptanceError("JUnit report must use UTF-8") from error
    if "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
        raise AcceptanceError("JUnit declarations and entities are forbidden")
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, ValueError) as error:
        raise AcceptanceError("JUnit report is malformed") from error
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root)
        if not suites or any(suite.tag != "testsuite" for suite in suites):
            raise AcceptanceError("JUnit must contain ordinary direct test suites")
    else:
        raise AcceptanceError("JUnit root must be testsuite or testsuites")
    totals = {"tests": 0, "passed": 0, "skipped": 0, "failures": 0, "errors": 0}
    test_ids: list[str] = []
    seen: set[str] = set()
    for suite in suites:
        if suite.find("testsuite") is not None:
            raise AcceptanceError("Nested JUnit suites are not accepted")
        counts = {name: _declared_count(suite, name) for name in ("tests", "skipped", "failures", "errors")}
        actual = {name: 0 for name in counts}
        for case in suite:
            if case.tag in {"properties", "system-out", "system-err"}:
                continue
            if case.tag != "testcase":
                raise AcceptanceError("JUnit suite contains an unknown result element")
            actual["tests"] += 1
            outcomes = [child.tag for child in case if child.tag in {"skipped", "failure", "error"}]
            if len(outcomes) > 1:
                raise AcceptanceError("JUnit test case has contradictory outcomes")
            if outcomes:
                actual[{"skipped": "skipped", "failure": "failures", "error": "errors"}[outcomes[0]]] += 1
            classname, name = case.get("classname", ""), case.get("name", "")
            if not name.strip() or not classname.startswith(("pipeline_tests.", "mcp_tests.")):
                raise AcceptanceError("JUnit case lacks a protected-suite test identity")
            identity = classname + "::" + name
            if identity in seen:
                raise AcceptanceError("JUnit contains duplicate test identities")
            seen.add(identity)
            test_ids.append(identity)
        if actual != counts:
            raise AcceptanceError("JUnit declared counts differ from its actual test cases")
        for name, count in counts.items():
            totals[name] += count
    if root.tag == "testsuites":
        for name in ("tests", "skipped", "failures", "errors"):
            if root.get(name) is not None and _declared_count(root, name) != totals[name]:
                raise AcceptanceError("JUnit aggregate counts differ from its test suites")
    totals["passed"] = totals["tests"] - totals["skipped"] - totals["failures"] - totals["errors"]
    if totals["failures"] or totals["errors"]:
        raise AcceptanceError("JUnit reports failed or errored tests")
    if totals["passed"] < min_passed:
        raise AcceptanceError("JUnit passed fewer than the required minimum tests")
    if totals["skipped"] > max_skipped:
        raise AcceptanceError("JUnit skipped more than the permitted maximum tests")
    return {**totals, "test_ids": sorted(test_ids), "validated": True}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--tests", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--targets", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        return run_acceptance(args.candidate, args.tests, args.report, args.scratch, args.targets)
    except (AcceptanceError, ReleaseError, OSError, ImportError) as error:
        print(json.dumps({"acceptance_error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
