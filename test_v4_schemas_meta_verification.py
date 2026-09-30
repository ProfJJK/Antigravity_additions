"""External meta-verification of the V4 schema contract suite (Task 196.01).

These checks inspect ``test_v4_schemas.py`` from the outside (AST, source text and a
clean-interpreter pytest run) instead of being embedded in the suite itself. They do
not import ``v4_schemas`` and therefore stay collectable while the suite is RED.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pydantic

REPO_ROOT = Path(__file__).resolve().parent
TARGET_PATH = REPO_ROOT / "test_v4_schemas.py"
SCHEMA_MODULE_PATH = REPO_ROOT / "v4_schemas.py"

SCHEMA_CLASS_NAMES = (
    "PlanDossier",
    "AuditFeedback",
    "ExecutionChunk",
    "ImplementationDossier",
    "PhysicsAutopsyReport",
    "HandoffEnvelope",
)

# Forbidden tokens are assembled from fragments so this file never contains them literally.
FORBIDDEN_TOKENS = (
    "unittest." + "mo" + "ck",
    "Magic" + "Mo" + "ck",
    "monkey" + "patch",
    "pytest." + "skip",
    "@" + "skip",
    "NotImplemented" + "Error",
    "TO" + "DO",
    "FIX" + "ME",
    "lo" + "rem",
    "fa" + "ker",
)
BANNED_IDENTIFIER_WORDS = (
    "dum" + "my",
    "fa" + "ke",
    "place" + "holder",
    "syn" + "thetic",
    "st" + "ub",
    "mo" + "ck",
)

REQUIRED_TEST_NAMES = (
    "test_pydantic_v2_and_schema_imports",
    "test_schema_suite_completeness",
    "test_plan_dossier_rejects_invalid_action",
    "test_plan_dossier_rejects_empty_citation",
    "test_audit_feedback_score_verdict_bounds",
    "test_audit_feedback_fail_requires_reason",
    "test_execution_chunk_validation",
    "test_impl_dossier_rejects_bad_hash",
    "test_impl_dossier_sha_mismatch",
    "test_autopsy_rejects_empty_file_line",
    "test_roundtrip_all_models",
    "test_envelope_timestamp_format",
    "test_model_config_extra_forbid",
)


# --------------------------------------------------------------------------------------
# Helpers over the target suite
# --------------------------------------------------------------------------------------


def _target_source():
    return TARGET_PATH.read_bytes().decode("utf-8")


def _target_tree():
    return ast.parse(_target_source())


def _target_test_nodes():
    return [
        node
        for node in ast.walk(_target_tree())
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _function_source(name):
    """Source text of a target test function including its decorators."""
    source_lines = _target_source().splitlines()
    for node in _target_test_nodes():
        if node.name == name:
            first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return "\n".join(source_lines[first_line - 1 : node.end_lineno])
    raise AssertionError(f"test function {name} not defined in {TARGET_PATH.name}")


# --------------------------------------------------------------------------------------
# T1 - T8
# --------------------------------------------------------------------------------------


def test_v4_schemas_file_exists_syntax_and_zero_mocks():
    assert TARGET_PATH.is_file()
    decoded = TARGET_PATH.read_bytes().decode("utf-8")
    tree = ast.parse(decoded)
    assert pydantic.VERSION.startswith("2.")

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "v4_schemas":
            imported_names.update(alias.name for alias in node.names)
    assert set(SCHEMA_CLASS_NAMES) <= imported_names

    lowered = decoded.lower()
    found_tokens = [t for t in FORBIDDEN_TOKENS if t.lower() in lowered]
    assert not found_tokens, f"forbidden tokens present: {found_tokens}"
    found_words = [w for w in BANNED_IDENTIFIER_WORDS if w in lowered]
    assert not found_words, f"banned identifier words present: {found_words}"


def test_v4_schemas_test_count_and_required_functions():
    defined = [node.name for node in _target_test_nodes()]
    assert len(defined) >= 25
    assert len(defined) == len(set(defined)), "duplicate test function names"
    for required_name in REQUIRED_TEST_NAMES:
        assert required_name in defined, required_name


def test_plan_dossier_contract_specifications():
    action_source = _function_source("test_plan_dossier_rejects_invalid_action")
    assert "RENAME" in action_source
    assert "ValidationError" in action_source
    citation_source = _function_source("test_plan_dossier_rejects_empty_citation")
    assert "ValidationError" in citation_source
    assert "citation" in citation_source
    assert '""' in citation_source


def test_audit_feedback_contract_specifications():
    bounds_source = _function_source("test_audit_feedback_score_verdict_bounds")
    for literal in ("score=-1", "score=101", "MAYBE", "ValidationError"):
        assert literal in bounds_source, literal
    reason_source = _function_source("test_audit_feedback_fail_requires_reason")
    for literal in (
        "FAIL",
        "SPOOFING_RISK",
        "violations=[]",
        "missing_acceptance_criteria=[]",
        "ValidationError",
    ):
        assert literal in reason_source, literal


def test_execution_chunk_contract_specifications():
    chunk_source = _function_source("test_execution_chunk_validation")
    for literal in (
        "task_id",
        "float",
        "target_files",
        "acceptance_criteria",
        "test_spec",
        "ValidationError",
    ):
        assert literal in chunk_source, literal


def test_implementation_dossier_contract_specifications():
    hash_source = _function_source("test_impl_dossier_rejects_bad_hash")
    for literal in ("git_diff_hash", "raw_stdout_sha256", "ValidationError"):
        assert literal in hash_source, literal
    mismatch_source = _function_source("test_impl_dossier_sha_mismatch")
    for literal in ("stdout_truncated", "raw_stdout_sha256", "ValidationError"):
        assert literal in mismatch_source, literal


def test_physics_autopsy_report_contract_specifications():
    autopsy_source = _function_source("test_autopsy_rejects_empty_file_line")
    for literal in ("failing_file_line", "ValidationError", '""'):
        assert literal in autopsy_source, literal


def test_envelope_and_model_config_contract_specifications():
    timestamp_source = _function_source("test_envelope_timestamp_format")
    for literal in ("produced_at_utc", "float", "ValidationError"):
        assert literal in timestamp_source, literal
    roundtrip_source = _function_source("test_roundtrip_all_models")
    for literal in (
        "model_dump_json",
        "model_validate_json",
        "unexpected_key",
        "ValidationError",
    ):
        assert literal in roundtrip_source, literal
    config_source = _function_source("test_model_config_extra_forbid")
    for literal in ("model_config", "extra", "forbid", "frozen"):
        assert literal in config_source, literal


# --------------------------------------------------------------------------------------
# T9 - clean-subprocess RED check
# --------------------------------------------------------------------------------------


def test_v4_schemas_suite_fails_red_pre_implementation():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", TARGET_PATH.name, "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=300,
    )
    combined_output = result.stdout + result.stderr
    if not SCHEMA_MODULE_PATH.is_file():
        assert result.returncode in (1, 2), combined_output
        assert "v4_schemas" in combined_output, combined_output
    else:
        # Once the module exists the collection error for the missing module must be gone.
        assert "ModuleNotFoundError: No module named 'v4_schemas'" not in combined_output
