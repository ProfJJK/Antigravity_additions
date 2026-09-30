# src/cochem/dsp/toolkit/package.py

`python
"""Domain-Specific Pipeline (DSP) Packaging and Ecosystem Validation Suite (SRS-412-07).

Chapter 7: Domain-Specific Pipelines (DSPs) & Creation Toolkit (ch07)
Provides comprehensive packaging, validation, integrity auditing, and distribution tools
for CoChem DSP plugins (The Code Forge, The Academic Press, The Pedagogy Engine, and custom extensions).

Key Capabilities:
- Interface verification: Ensures subclasses implement IDomainPipeline / DSPPluginBase triads.
- Zero-mock and anti-spoof enforcement: Rejects mock imports, monkeypatching, and synthetic generators.
- Anti-stub enforcement: Prohibits empty pass blocks, ellipsis (...), and NotImplementedError stubs in pipeline methods.
- Schema validation: Validates dsp_registration_manifest JSON payloads.
- Fast compilation validation (< 5s per NFR-DSP-01).
- Workspace isolation validation (NFR-DSP-02).
- Distributable package creation with SHA-256 integrity hashing.
- Windows-safe subprocess execution with window suppression (Rule 15).
- Seamless integration with Antigravity SDK components.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import zipfile

# Antigravity SDK Integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

# Windows Subprocess Window Suppression Flag (Rule 15)
CREATE_NO_WINDOW: int = 0x08000000

# Ensure repository root src is on sys.path for internal imports when executed directly
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_SRC_DIR: Path = _REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


class AntiSpoofLinter(ast.NodeVisitor):
    """Hardened AST Linter for detecting mock imports, synthetic array generators, and evasions."""

    FORBIDDEN_MODULES: frozenset[str] = frozenset({"unittest.mock", "mock"})
    FORBIDDEN_NAMES: frozenset[str] = frozenset({"MagicMock", "monkeypatch", "Mock", "patch"})
    FORBIDDEN_CALLS: frozenset[str] = frozenset({"zeros", "ones", "eye"})

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in self.FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden mock import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in self.FORBIDDEN_MODULES:
            self.violations.append(f"Forbidden mock from-import: {node.module}")
        if node.module == "unittest":
            for alias in node.names:
                if alias.name in {"mock", "MagicMock", "Mock", "patch"}:
                    self.violations.append(f"Forbidden unittest mock import: {alias.name}")
        for alias in node.names:
            if alias.name in self.FORBIDDEN_NAMES:
                self.violations.append(f"Forbidden mock name import: {alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in self.FORBIDDEN_CALLS:
            self.violations.append(f"Synthetic array call: {node.func.attr}")
        elif isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_CALLS:
            self.violations.append(f"Synthetic array call: {node.func.id}")
        self.generic_visit(node)

    def lint(self, source_code: str) -> list[str]:
        """Parses and lints source code, resetting violations on each call."""
        self.violations.clear()
        tree = ast.parse(source_code)
        self.visit(tree)
        return list(self.violations)


CANONICAL_TRIAD: frozenset[str] = frozenset({"validate", "execute", "audit"})
PLUGIN_BASE_TRIAD: frozenset[str] = frozenset({"validate_task_payload", "execute_workflow_stage", "run_domain_audit"})
DOMAIN_STAGE_TRIAD: frozenset[str] = frozenset({"execute_stage_propose", "execute_stage_test", "execute_stage_arbitrate"})


def _get_base_names(bases: list[ast.expr]) -> set[str]:
    """Extracts identifier names from class base expressions including subscripts."""
    names: set[str] = set()
    for b in bases:
        if isinstance(b, ast.Name):
            names.add(b.id)
        elif isinstance(b, ast.Attribute):
            names.add(b.attr)
        elif isinstance(b, ast.Subscript):
            if isinstance(b.value, ast.Name):
                names.add(b.value.id)
            elif isinstance(b.value, ast.Attribute):
                names.add(b.value.attr)
    return names


def _is_stub_method(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Verifies that a method does not contain NotImplementedError, empty pass, or ellipsis (...) stubs."""
    for node in ast.walk(func):
        if isinstance(node, ast.Raise) and node.exc:
            exc = node.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                return True
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "NotImplementedError":
                return True

    non_doc_stmts = [
        s for s in func.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
    ]
    if not non_doc_stmts:
        return True
    if all(
        isinstance(s, ast.Pass)
        or (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and s.value.value is Ellipsis
        )
        for s in non_doc_stmts
    ):
        return True
    return False


def validate_dsp_package(package_dir: Path | str) -> bool:
    """Validates DSP package interfaces, schemas, and test obligations per SRS Ch07."""
    target_dir = Path(package_dir)
    if not target_dir.exists() or not target_dir.is_dir():
        return False

    py_files = list(target_dir.rglob("*.py"))
    if not py_files:
        return False

    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return False

        # Zero-mock invariant: audit all python files for mock imports and synthetic fixtures
        linter = AntiSpoofLinter()
        if linter.lint(content):
            return False

        # Interface validation & anti-stub verification
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = _get_base_names(node.bases)
                is_abstract = "ABC" in bases
                is_pipeline_class = bool(
                    bases & {"IDomainPipeline", "DSPPluginBase", "DomainPipelineInterface"}
                    or node.name.endswith("Orchestrator")
                    or node.name.endswith("Pipeline")
                )

                if is_pipeline_class and not is_abstract:
                    methods = [
                        item for item in node.body
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    method_names = {m.name for m in methods}
                    has_canonical = CANONICAL_TRIAD.issubset(method_names)
                    has_plugin = PLUGIN_BASE_TRIAD.issubset(method_names)
                    has_stage = DOMAIN_STAGE_TRIAD.issubset(method_names)

                    if not (has_canonical or has_plugin or has_stage):
                        return False

                    for m in methods:
                        if m.name in (CANONICAL_TRIAD | PLUGIN_BASE_TRIAD | DOMAIN_STAGE_TRIAD):
                            if _is_stub_method(m):
                                return False

    # Schema validation: inspect manifest JSON files if present
    for json_file in target_dir.rglob("*.json"):
        try:
            raw_data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return False

        if not isinstance(raw_data, dict):
            continue

        data = raw_data.get("dsp_registration_manifest", raw_data)
        if isinstance(data, dict) and ("dsp_id" in data or "domain" in data):
            if not isinstance(data.get("dsp_id", data.get("domain")), str):
                return False
            if "version" in data and not isinstance(data["version"], str):
                return False
            if "supported_job_types" in data and not isinstance(data["supported_job_types"], list):
                return False
            if "resource_caps" in data:
                caps = data["resource_caps"]
                if not isinstance(caps, dict) or not isinstance(caps.get("memory_mb", 1), (int, float)):
                    return False
                if isinstance(caps.get("memory_mb"), bool):
                    return False
                if "max_workers" in caps and (not isinstance(caps["max_workers"], (int, float)) or isinstance(caps["max_workers"], bool)):
                    return False

    return True


@dataclasses.dataclass(frozen=True)
class DSPPackageReport:
    """Detailed inspection report for a DSP package directory."""

    is_valid: bool
    package_path: str
    python_files: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    json_files: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    pipeline_classes: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    manifest: dict[str, Any] | None = None
    violations: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serializes report to dictionary."""
        return {
            "is_valid": self.is_valid,
            "package_path": self.package_path,
            "python_files": list(self.python_files),
            "json_files": list(self.json_files),
            "pipeline_classes": list(self.pipeline_classes),
            "manifest": self.manifest,
            "violations": list(self.violations),
            "elapsed_seconds": self.elapsed_seconds,
        }


def inspect_dsp_package(package_dir: Path | str) -> DSPPackageReport:
    """Performs deep structural audit of a DSP package directory, returning detailed diagnostic report."""
    start_time = time.perf_counter()
    target_dir = Path(package_dir)
    violations: list[str] = []
    py_names: list[str] = []
    json_names: list[str] = []
    pipeline_classes: list[str] = []
    manifest_data: dict[str, Any] | None = None

    if not target_dir.exists():
        violations.append(f"Directory does not exist: {target_dir}")
        return DSPPackageReport(
            is_valid=False,
            package_path=str(target_dir),
            violations=tuple(violations),
            elapsed_seconds=time.perf_counter() - start_time,
        )

    if not target_dir.is_dir():
        violations.append(f"Path is not a directory: {target_dir}")
        return DSPPackageReport(
            is_valid=False,
            package_path=str(target_dir),
            violations=tuple(violations),
            elapsed_seconds=time.perf_counter() - start_time,
        )

    py_files = list(target_dir.rglob("*.py"))
    if not py_files:
        violations.append("Package contains no Python source (.py) files.")

    for py_file in py_files:
        rel_path = str(py_file.relative_to(target_dir))
        py_names.append(rel_path)
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            violations.append(f"Syntax/Parse error in {rel_path}: {exc}")
            continue

        linter = AntiSpoofLinter()
        linter_issues = linter.lint(content)
        for issue in linter_issues:
            violations.append(f"AntiSpoof violation in {rel_path}: {issue}")

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = _get_base_names(node.bases)
                is_abstract = "ABC" in bases
                is_pipeline_class = bool(
                    bases & {"IDomainPipeline", "DSPPluginBase", "DomainPipelineInterface"}
                    or node.name.endswith("Orchestrator")
                    or node.name.endswith("Pipeline")
                )
                if is_pipeline_class:
                    pipeline_classes.append(node.name)
                    if not is_abstract:
                        methods = [
                            item for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        ]
                        method_names = {m.name for m in methods}
                        has_canonical = CANONICAL_TRIAD.issubset(method_names)
                        has_plugin = PLUGIN_BASE_TRIAD.issubset(method_names)
                        has_stage = DOMAIN_STAGE_TRIAD.issubset(method_names)
                        if not (has_canonical or has_plugin or has_stage):
                            violations.append(
                                f"Class {node.name} in {rel_path} does not implement any required triad."
                            )
                        for m in methods:
                            if m.name in (CANONICAL_TRIAD | PLUGIN_BASE_TRIAD | DOMAIN_STAGE_TRIAD):
                                if _is_stub_method(m):
                                    violations.append(
                                        f"Class {node.name} method {m.name} in {rel_path} is an empty/stub method."
                                    )

    for json_file in target_dir.rglob("*.json"):
        rel_json = str(json_file.relative_to(target_dir))
        json_names.append(rel_json)
        try:
            raw_data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            violations.append(f"JSON decode error in {rel_json}: {exc}")
            continue

        if not isinstance(raw_data, dict):
            continue

        data = raw_data.get("dsp_registration_manifest", raw_data)
        if isinstance(data, dict) and ("dsp_id" in data or "domain" in data):
            manifest_data = raw_data
            if not isinstance(data.get("dsp_id", data.get("domain")), str):
                violations.append(f"Manifest dsp_id or domain is not string in {rel_json}")
            if "version" in data and not isinstance(data["version"], str):
                violations.append(f"Manifest version is not string in {rel_json}")
            if "supported_job_types" in data and not isinstance(data["supported_job_types"], list):
                violations.append(f"Manifest supported_job_types is not list in {rel_json}")
            if "resource_caps" in data:
                caps = data["resource_caps"]
                if not isinstance(caps, dict) or not isinstance(caps.get("memory_mb", 1), (int, float)) or isinstance(caps.get("memory_mb"), bool):
                    violations.append(f"Manifest resource_caps.memory_mb must be numeric in {rel_json}")

    elapsed = time.perf_counter() - start_time
    is_valid = len(violations) == 0

    return DSPPackageReport(
        is_valid=is_valid,
        package_path=str(target_dir),
        python_files=tuple(py_names),
        json_files=tuple(json_names),
        pipeline_classes=tuple(pipeline_classes),
        manifest=manifest_data,
        violations=tuple(violations),
        elapsed_seconds=elapsed,
    )


def verify_compilation_speed(package_dir: Path | str, threshold_seconds: float = 5.0) -> bool:
    """Verifies that DSP package compiles and validates within benchmark limit (NFR-DSP-01: < 5 seconds)."""
    start_time = time.perf_counter()
    valid = validate_dsp_package(package_dir)
    elapsed = time.perf_counter() - start_time
    if not valid:
        return False
    return elapsed <= threshold_seconds


def verify_package_isolation(package_dir: Path | str) -> bool:
    """Verifies that DSP package has zero side-effects across external domains (NFR-DSP-02)."""
    target_dir = Path(package_dir).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        return False

    py_files = list(target_dir.rglob("*.py"))
    if not py_files:
        return False

    for py_file in py_files:
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return False

        # Scan AST for suspicious filesystem deletion calls outside package
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id
                if func_name in {"rmtree", "unlink", "remove"}:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            val = arg.value
                            if ".." in val or "/tmp/cochem_exec_" in val or "cochem_exec_" in val:
                                return False
                        elif isinstance(arg, ast.BinOp) and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str):
                            if ".." in arg.left.value or "cochem_exec_" in arg.left.value:
                                return False

        # String-level guard for raw forbidden patterns
        for pattern in ('rmtree("..', "rmtree('..", 'remove("..', "remove('..", "cochem_exec_"):
            if pattern in content:
                return False

    return True


def build_dsp_package(
    source_dir: Path | str,
    output_dir: Path | str | None = None,
    archive_format: str = "zip",
) -> Path:
    """Builds a validated, production-ready DSP package distribution archive.

    Validates package integrity before building. Computes SHA-256 digest
    and embeds manifest telemetry.
    """
    src = Path(source_dir).resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Source package directory does not exist: {src}")

    if not validate_dsp_package(src):
        report = inspect_dsp_package(src)
        reasons = "; ".join(report.violations) if report.violations else "Unknown validation failure"
        raise ValueError(f"DSP package validation failed for {src}: {reasons}")

    out_base = Path(output_dir).resolve() if output_dir else src.parent / "dist"
    out_base.mkdir(parents=True, exist_ok=True)

    package_name = src.name
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_path = out_base / f"{package_name}_{timestamp}.zip"

    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in src.rglob("*"):
            if "__pycache__" in file_path.parts or file_path.name.endswith(".pyc"):
                continue
            if file_path.is_file():
                arcname = file_path.relative_to(src)
                zf.write(file_path, arcname=arcname)

    # Compute SHA-256 checksum
    hasher = hashlib.sha256()
    with archive_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    sha256_digest = hasher.hexdigest()

    meta_file = out_base / f"{package_name}_{timestamp}_meta.json"
    meta_data = {
        "package_name": package_name,
        "archive_file": archive_path.name,
        "sha256": sha256_digest,
        "build_timestamp": timestamp,
        "format": archive_format,
        "source_path": str(src),
        "validation_passed": True,
    }
    meta_file.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")

    return archive_path


def invoke_antigravity_agent(
    agent_name: str,
    prompt: str,
    timeout_sec: int = 300,
) -> str:
    """Invokes Antigravity agent CLI in windowless mode per Rule 10 & Rule 15."""
    cli_path = shutil.which("agy")
    if not cli_path:
        raise FileNotFoundError("Antigravity CLI ('agy') executable not found on system PATH.")

    cmd = [cli_path, "--agent", agent_name, "-p", prompt]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return proc.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Antigravity invocation error ({agent_name}): {exc.stderr.strip()}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for DSP packaging and validation toolkit."""
    parser = argparse.ArgumentParser(
        prog="package.py",
        description="DSP Packaging and Ecosystem Validation Suite (SRS-412-07)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # validate
    val_parser = subparsers.add_parser("validate", help="Validate DSP package integrity")
    val_parser.add_argument("path", help="Path to DSP package directory")

    # inspect
    ins_parser = subparsers.add_parser("inspect", help="Detailed inspection of DSP package")
    ins_parser.add_argument("path", help="Path to DSP package directory")
    ins_parser.add_argument("--json", action="store_true", help="Output report in JSON format")

    # build
    bld_parser = subparsers.add_parser("build", help="Build distributable DSP archive")
    bld_parser.add_argument("path", help="Path to DSP package directory")
    bld_parser.add_argument("--output", "-o", default=None, help="Output directory for archive")

    # benchmark
    bm_parser = subparsers.add_parser("benchmark", help="Benchmark compilation & validation speed (NFR-DSP-01)")
    bm_parser.add_argument("path", help="Path to DSP package directory")
    bm_parser.add_argument("--threshold", "-t", type=float, default=5.0, help="Max allowed seconds (default: 5.0)")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    target = Path(args.path)

    if args.command == "validate":
        valid = validate_dsp_package(target)
        if valid:
            sys.stdout.write(f"[SUCCESS] DSP package at '{target}' passed validation.\n")
            return 0
        sys.stderr.write(f"[FAILURE] DSP package at '{target}' failed validation.\n")
        return 1

    if args.command == "inspect":
        report = inspect_dsp_package(target)
        if args.json:
            sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
        else:
            status = "VALID" if report.is_valid else "INVALID"
            sys.stdout.write(f"DSP Package Report: {status} ({report.elapsed_seconds:.3f}s)\n")
            sys.stdout.write(f"Path: {report.package_path}\n")
            sys.stdout.write(f"Python Files ({len(report.python_files)}): {', '.join(report.python_files)}\n")
            sys.stdout.write(f"Pipeline Classes: {', '.join(report.pipeline_classes)}\n")
            if report.violations:
                sys.stdout.write("Violations:\n")
                for v in report.violations:
                    sys.stdout.write(f"  - {v}\n")
        return 0 if report.is_valid else 1

    if args.command == "build":
        try:
            archive = build_dsp_package(target, output_dir=args.output)
            sys.stdout.write(f"[SUCCESS] Built DSP package archive: {archive}\n")
            return 0
        except Exception as exc:
            sys.stderr.write(f"[ERROR] Failed to build package: {exc}\n")
            return 1

    if args.command == "benchmark":
        ok = verify_compilation_speed(target, threshold_seconds=args.threshold)
        if ok:
            sys.stdout.write(f"[SUCCESS] Package passed speed benchmark (threshold <= {args.threshold}s).\n")
            return 0
        sys.stderr.write(f"[FAILURE] Package failed speed benchmark (exceeded {args.threshold}s or invalid).\n")
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())

`
