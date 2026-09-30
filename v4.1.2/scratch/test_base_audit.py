"""Adversarial Audit Verification Suite for staged ch07 base.py (SRS-412-07)."""
from __future__ import annotations

import ast
import inspect
import sys
import importlib.util
from pathlib import Path
from cochem.dsp.forge.linter import AntiSpoofLinter
from cochem.dsp.toolkit.package import validate_dsp_package

def run_audit() -> None:
    staged_path = Path(r"D:\__CoChem\__agentic\v4.1.2\.staging\ch07\base\base.py")
    assert staged_path.exists(), f"Staged file {staged_path} does not exist"
    code = staged_path.read_text(encoding="utf-8")
    tree = ast.parse(code)

    print(f"[AUDIT] Testing against staged script: {staged_path}")
    print(f"[AUDIT] Python version: {sys.version}")

    # 1. Anti-Spoof Linter Check
    linter = AntiSpoofLinter()
    violations = linter.lint(code)
    print(f"[AUDIT] AntiSpoofLinter violations count: {len(violations)}")
    assert len(violations) == 0, f"AntiSpoofLinter failed with: {violations}"

    # 2. Chemistry Libraries Independence
    banned_chem = {"pyscf", "ase", "mendeleev", "rdkit"}
    found_chem = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                if any(b in n.name.lower() for b in banned_chem):
                    found_chem.append((n.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(b in node.module.lower() for b in banned_chem):
                found_chem.append((node.module, node.lineno))
    print(f"[AUDIT] Chemistry libraries detected: {len(found_chem)}")
    assert len(found_chem) == 0, f"Found chemistry library imports: {found_chem}"

    # 3. Zero-Mock & Anti-Stub Invariants
    passes = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Pass)]
    print(f"[AUDIT] AST 'pass' statements: {len(passes)}")
    assert len(passes) == 0, f"Found 'pass' statements at lines: {passes}"

    not_implemented = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc:
            exc = node.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                not_implemented.append(node.lineno)
            elif isinstance(exc, ast.Call) and getattr(exc.func, "id", None) == "NotImplementedError":
                not_implemented.append(node.lineno)
    print(f"[AUDIT] AST 'NotImplementedError' raises: {len(not_implemented)}")
    assert len(not_implemented) == 0, f"Found NotImplementedError at lines: {not_implemented}"

    body_ellipses = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
                    body_ellipses.append((node.name, stmt.lineno))
    print(f"[AUDIT] Function body ellipses stubs: {len(body_ellipses)}")
    assert len(body_ellipses) == 0, f"Found body ellipses at: {body_ellipses}"

    # 4. validate_dsp_package Ecosystem Validation
    package_valid = validate_dsp_package(staged_path.parent)
    print(f"[AUDIT] validate_dsp_package: {package_valid}")
    assert package_valid is True, "validate_dsp_package returned False"

    # 5. Windows Subprocess Security Verification
    has_cflags_constant = False
    cflags_val = None
    uses_cflags_in_run = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "CREATE_NO_WINDOW":
                    has_cflags_constant = True
                    if isinstance(node.value, ast.Constant):
                        cflags_val = node.value.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "CREATE_NO_WINDOW":
                has_cflags_constant = True
                if isinstance(node.value, ast.Constant):
                    cflags_val = node.value.value
        elif isinstance(node, ast.Call):
            if getattr(node.func, "attr", None) == "run":
                for kw in node.keywords:
                    if kw.arg == "creationflags" and getattr(kw.value, "id", None) == "CREATE_NO_WINDOW":
                        uses_cflags_in_run = True

    print(f"[AUDIT] CREATE_NO_WINDOW constant: {has_cflags_constant} (value={hex(cflags_val) if cflags_val else None})")
    print(f"[AUDIT] Subprocess uses CREATE_NO_WINDOW: {uses_cflags_in_run}")
    assert has_cflags_constant and cflags_val == 0x08000000, "CREATE_NO_WINDOW must equal 0x08000000"
    assert uses_cflags_in_run, "Subprocess invocation must pass creationflags=CREATE_NO_WINDOW"

    # 6. Dynamic Import & Runtime Interface Contract
    spec = importlib.util.spec_from_file_location("cochem.dsp.base", str(staged_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    IDomainPipeline = mod.IDomainPipeline
    DSPPluginBase = mod.DSPPluginBase
    ResourceQuota = mod.ResourceQuota
    DSPRegistrationManifest = mod.DSPRegistrationManifest

    # Test IDomainPipeline abstract contract
    assert inspect.isabstract(IDomainPipeline)
    assert IDomainPipeline.__abstractmethods__ == {"validate", "execute", "audit"}

    # Test DSPPluginBase abstract contract
    assert inspect.isabstract(DSPPluginBase)
    assert issubclass(DSPPluginBase, IDomainPipeline)
    assert DSPPluginBase.__abstractmethods__ == {"validate_task_payload", "execute_workflow_stage", "run_domain_audit"}

    # Test ResourceQuota & Manifest round-trip
    quota = ResourceQuota(memory_mb=2048, max_workers=4, cpu_limit=1.5, timeout_seconds=600)
    manifest = DSPRegistrationManifest(
        dsp_id="DSP-AUDIT-TEST",
        domain="software_engineering",
        version="4.1.2",
        supported_job_types=("test_authoring",),
        resource_caps=quota,
        mcp_server="test-mcp"
    )
    m_dict = manifest.to_dict()
    m_restored = DSPRegistrationManifest.from_dict(m_dict)
    assert manifest == m_restored, "Manifest serialization round-trip mismatch"

    # Test Concrete Plugin Implementation & Lifecycle
    class AuditedPlugin(DSPPluginBase):
        def __init__(self):
            super().__init__(domain_name="test_audit", memory_cap_mb=1024)
        def validate_task_payload(self, payload):
            return "required_key" in payload
        def execute_workflow_stage(self, task_id, context):
            if context.get("should_fail"):
                raise ValueError("Stage failure injected")
            return {"status": "SUCCESS", "task_id": task_id, "artifact_path": "output.txt"}
        def run_domain_audit(self, artifact_path):
            return artifact_path == "output.txt"

    plugin = AuditedPlugin()
    assert plugin.validate({"required_key": True}) is True
    assert plugin.validate({"other": True}) is False

    # Execute success
    res = plugin.execute({"required_key": True, "task_id": "AUDIT-001"})
    assert res["status"] == "SUCCESS"
    assert plugin.audit(res) is True
    assert len(plugin._lifecycle_events) == 2
    assert plugin._lifecycle_events[0]["event"] == "stage_start"
    assert plugin._lifecycle_events[1]["event"] == "stage_complete"

    # Execute failure & error hook
    try:
        plugin.execute({"required_key": True, "task_id": "AUDIT-002", "should_fail": True})
        raise AssertionError("Should have raised ValueError")
    except ValueError:
        pass
    assert len(plugin._lifecycle_events) == 4
    assert plugin._lifecycle_events[3]["event"] == "stage_error"

    # Teardown
    plugin.teardown()
    assert len(plugin._lifecycle_events) == 0

    print("[AUDIT] ALL RIGOROUS ADVERSARIAL CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    run_audit()
