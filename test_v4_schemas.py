"""Red contract suite for the V4 global state schemas (Task 196.01).

Source SRS: dropzones/inbox_code/SRS-20260925T214325-OBJECTIVE-V4-GLOBAL-STATE-SCHEMA-INTER-A.md

The contract targets the module ``v4_schemas`` (workspace root, implemented in
Task 196.02), which must expose PlanDossier, AuditFeedback, ExecutionChunk,
ImplementationDossier, PhysicsAutopsyReport and HandoffEnvelope as Pydantic V2
BaseModels configured with extra='forbid' and frozen=True.

Every behavioural test drives the real models through real Pydantic V2 validation.
The module-level import of ``v4_schemas`` is deliberately unguarded: until the
module exists, collection of this file fails with ModuleNotFoundError (RED,
pytest exit code 2).
"""

import ast
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pydantic
import pytest
from pydantic import BaseModel, ValidationError

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v4_schemas import (  # noqa: E402
    AuditFeedback,
    ExecutionChunk,
    HandoffEnvelope,
    ImplementationDossier,
    PhysicsAutopsyReport,
    PlanDossier,
)

SELF_PATH = Path(__file__).resolve()
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
# Realistic sample data
# --------------------------------------------------------------------------------------

STDOUT_TEXT = (
    "============================= test session starts =============================\n"
    "collected 3 items\n\n"
    "tests/test_engine.py ...                                                 [100%]\n\n"
    "============================== 3 passed in 0.42s ==============================\n"
)
STDOUT_SHA = hashlib.sha256(STDOUT_TEXT.encode("utf-8")).hexdigest()
DIFF_HASH = hashlib.sha1(
    b"diff --git a/src/physics_engine.py b/src/physics_engine.py"
).hexdigest()


def plan_payload(**overrides):
    base_record = {
        "proposed_architecture": (
            "Extend the flash-calculation engine with a Peng-Robinson solver and "
            "cover it with a regression suite."
        ),
        "research_mapping": [
            {
                "topic": "Peng-Robinson equation of state",
                "citation": "DOI:10.1021/i160057a011",
            }
        ],
        "file_impact_matrix": [
            {"file_path": "src/physics_engine.py", "action": "MODIFY"},
            {"file_path": "tests/test_engine.py", "action": "CREATE"},
        ],
    }
    return {**base_record, **overrides}


def audit_payload(**overrides):
    base_record = {
        "score": 88,
        "verdict": "PASS",
        "violations": [],
        "missing_acceptance_criteria": [],
    }
    return {**base_record, **overrides}


def chunk_spec(**overrides):
    base_record = {
        "test_file": "tests/tdd/test_task_196_02.py",
        "physical_inputs": {"temperature_K": 298.15, "pressure_Pa": 101325.0},
        "assertions": ["compressibility factor is within (0, 1.2)"],
    }
    return {**base_record, **overrides}


def chunk_payload(**overrides):
    base_record = {
        "task_id": 196.01,
        "target_files": ["v4_schemas.py"],
        "acceptance_criteria": ["AC1: all six models import and validate"],
        "test_spec": chunk_spec(),
    }
    return {**base_record, **overrides}


def impl_payload(**overrides):
    base_record = {
        "git_diff_hash": DIFF_HASH,
        "raw_stdout": STDOUT_TEXT,
        "stdout_path": ".evidence/196.02/stdout.txt",
        "stdout_truncated": False,
        "raw_stdout_sha256": STDOUT_SHA,
        "fulfilled_ac_list": ["AC1", "AC2"],
    }
    return {**base_record, **overrides}


def autopsy_payload(**overrides):
    base_record = {
        "failure_stage": "TEST_FAIL",
        "failing_file_line": "tests/test_engine.py:42",
        "hypothesis": "Fugacity coefficient uses the wrong root of the cubic.",
        "fix_vector": "Select the smallest real root for the liquid phase.",
    }
    return {**base_record, **overrides}


def envelope_payload(**overrides):
    base_record = {
        "schema_name": "AuditFeedback",
        "schema_version": "1.0.0",
        "schema_sha256": hashlib.sha256(b"AuditFeedback:1.0.0").hexdigest(),
        "producer_provider": "anthropic",
        "produced_at_utc": time.time(),
        "payload": audit_payload(),
    }
    return {**base_record, **overrides}


ALL_MODELS = [
    pytest.param(PlanDossier, plan_payload, id="PlanDossier"),
    pytest.param(AuditFeedback, audit_payload, id="AuditFeedback"),
    pytest.param(ExecutionChunk, chunk_payload, id="ExecutionChunk"),
    pytest.param(ImplementationDossier, impl_payload, id="ImplementationDossier"),
    pytest.param(PhysicsAutopsyReport, autopsy_payload, id="PhysicsAutopsyReport"),
    pytest.param(HandoffEnvelope, envelope_payload, id="HandoffEnvelope"),
]

# --------------------------------------------------------------------------------------
# Self-inspection helpers
# --------------------------------------------------------------------------------------


def _self_source():
    return SELF_PATH.read_bytes().decode("utf-8")


def _self_tree():
    return ast.parse(_self_source())


def _test_function_nodes():
    return [
        node
        for node in ast.walk(_self_tree())
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _function_source(name):
    """Source text of a test function including its decorators."""
    source_lines = _self_source().splitlines()
    for node in _test_function_nodes():
        if node.name == name:
            first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return "\n".join(source_lines[first_line - 1 : node.end_lineno])
    raise AssertionError(f"test function {name} not defined in {SELF_PATH.name}")


def _assert_roundtrip(model_cls, payload):
    original = model_cls.model_validate(payload)
    wire_text = original.model_dump_json()
    restored = model_cls.model_validate_json(wire_text)
    assert restored == original
    assert restored.model_dump_json().encode("utf-8") == wire_text.encode("utf-8")
    assert model_cls.model_config.get("extra") == "forbid"
    wire_with_extra = json.loads(wire_text)
    wire_with_extra["unexpected_key"] = "value"
    with pytest.raises(ValidationError):
        model_cls.model_validate_json(json.dumps(wire_with_extra))


# --------------------------------------------------------------------------------------
# Imports and suite completeness (AC-01, AC-02)
# --------------------------------------------------------------------------------------


def test_pydantic_v2_and_schema_imports():
    assert pydantic.VERSION.startswith("2."), pydantic.VERSION
    imported = {
        "PlanDossier": PlanDossier,
        "AuditFeedback": AuditFeedback,
        "ExecutionChunk": ExecutionChunk,
        "ImplementationDossier": ImplementationDossier,
        "PhysicsAutopsyReport": PhysicsAutopsyReport,
        "HandoffEnvelope": HandoffEnvelope,
    }
    assert tuple(imported) == SCHEMA_CLASS_NAMES
    for class_name, model_cls in imported.items():
        assert isinstance(model_cls, type), class_name
        assert issubclass(model_cls, BaseModel), class_name
        assert model_cls.__name__ == class_name


def test_schema_suite_completeness():
    defined = [node.name for node in _test_function_nodes()]
    assert len(defined) >= 25, f"only {len(defined)} test functions defined"
    assert len(defined) == len(set(defined)), "duplicate test function names"
    missing = [name for name in REQUIRED_TEST_NAMES if name not in defined]
    assert not missing, f"required test functions absent: {missing}"


# --------------------------------------------------------------------------------------
# PlanDossier (AC-04, AC-05)
# --------------------------------------------------------------------------------------


def test_plan_dossier_valid_construction():
    dossier = PlanDossier.model_validate_json(json.dumps(plan_payload()))
    assert dossier.proposed_architecture.startswith("Extend the flash-calculation")
    assert len(dossier.research_mapping) == 1
    assert len(dossier.file_impact_matrix) == 2


@pytest.mark.parametrize("action", ["CREATE", "MODIFY", "DELETE"])
def test_plan_dossier_accepts_all_actions(action):
    matrix = [{"file_path": "src/physics_engine.py", "action": action}]
    dossier = PlanDossier.model_validate(plan_payload(file_impact_matrix=matrix))
    assert len(dossier.file_impact_matrix) == 1


def test_plan_dossier_rejects_invalid_action():
    for bad_action in ("RENAME", "MOVE", "create", ""):
        matrix = [{"file_path": "src/physics_engine.py", "action": bad_action}]
        with pytest.raises(ValidationError):
            PlanDossier.model_validate(plan_payload(file_impact_matrix=matrix))
    rename_json = json.dumps(
        plan_payload(
            file_impact_matrix=[{"file_path": "src/old_engine.py", "action": "RENAME"}]
        )
    )
    with pytest.raises(ValidationError):
        PlanDossier.model_validate_json(rename_json)


def test_plan_dossier_rejects_empty_citation():
    for empty_citation in ("", "   "):
        mapping = [
            {"topic": "Peng-Robinson equation of state", "citation": empty_citation}
        ]
        with pytest.raises(ValidationError):
            PlanDossier.model_validate(plan_payload(research_mapping=mapping))
        with pytest.raises(ValidationError):
            PlanDossier.model_validate_json(
                json.dumps(plan_payload(research_mapping=mapping))
            )


def test_plan_dossier_rejects_missing_citation():
    mapping = [{"topic": "Peng-Robinson equation of state"}]
    with pytest.raises(ValidationError):
        PlanDossier.model_validate(plan_payload(research_mapping=mapping))


@pytest.mark.parametrize(
    "field_name",
    ["proposed_architecture", "research_mapping", "file_impact_matrix"],
)
def test_plan_dossier_rejects_missing_fields(field_name):
    incomplete = plan_payload()
    del incomplete[field_name]
    with pytest.raises(ValidationError):
        PlanDossier.model_validate(incomplete)


# --------------------------------------------------------------------------------------
# AuditFeedback (AC-06, AC-07)
# --------------------------------------------------------------------------------------


def test_audit_feedback_valid_pass():
    feedback = AuditFeedback.model_validate_json(json.dumps(audit_payload()))
    assert feedback.score == 88
    assert feedback.verdict == "PASS"
    assert list(feedback.violations) == []
    assert list(feedback.missing_acceptance_criteria) == []


def test_audit_feedback_score_verdict_bounds():
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate(audit_payload(score=-1))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate(audit_payload(score=101))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate(audit_payload(verdict="MAYBE"))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate(audit_payload(score="high"))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate_json(json.dumps(audit_payload(score=-1)))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate_json(json.dumps(audit_payload(score=101)))
    with pytest.raises(ValidationError):
        AuditFeedback.model_validate_json(json.dumps(audit_payload(verdict="MAYBE")))


@pytest.mark.parametrize("boundary_score", [0, 100])
def test_audit_feedback_accepts_score_boundaries(boundary_score):
    feedback = AuditFeedback.model_validate(audit_payload(score=boundary_score))
    assert feedback.score == boundary_score


def test_audit_feedback_fail_requires_reason():
    for verdict in ("FAIL", "SPOOFING_RISK"):
        with pytest.raises(ValidationError):
            AuditFeedback.model_validate(
                audit_payload(
                    verdict=verdict,
                    score=30,
                    violations=[],
                    missing_acceptance_criteria=[],
                )
            )
        with pytest.raises(ValidationError):
            AuditFeedback.model_validate_json(
                json.dumps(
                    audit_payload(
                        verdict=verdict,
                        score=30,
                        violations=[],
                        missing_acceptance_criteria=[],
                    )
                )
            )


@pytest.mark.parametrize(
    "verdict, violations, missing_criteria",
    [
        ("FAIL", ["compressibility factor never validated"], []),
        ("FAIL", [], ["AC3: SHA-256 digest consistency"]),
        ("SPOOFING_RISK", ["expected output hard-coded in test"], []),
        ("SPOOFING_RISK", [], ["AC1: real subprocess evidence"]),
        ("FAIL", ["stdout digest mismatch"], ["AC2: raw stdout retained"]),
    ],
)
def test_audit_feedback_fail_accepts_reason(verdict, violations, missing_criteria):
    feedback = AuditFeedback.model_validate(
        audit_payload(
            verdict=verdict,
            score=35,
            violations=violations,
            missing_acceptance_criteria=missing_criteria,
        )
    )
    assert feedback.verdict == verdict


# --------------------------------------------------------------------------------------
# ExecutionChunk (AC-08)
# --------------------------------------------------------------------------------------


def test_execution_chunk_validation():
    chunk = ExecutionChunk.model_validate_json(json.dumps(chunk_payload()))
    assert isinstance(chunk.task_id, float)
    assert chunk.task_id == 196.01
    assert list(chunk.target_files) == ["v4_schemas.py"]
    assert len(chunk.acceptance_criteria) == 1
    assert set(chunk.test_spec) >= {"test_file", "physical_inputs", "assertions"}

    missing_task_id = chunk_payload()
    del missing_task_id["task_id"]
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(missing_task_id)

    for malformed_task_id in ("not-a-number", None, [196.01]):
        with pytest.raises(ValidationError):
            ExecutionChunk.model_validate(chunk_payload(task_id=malformed_task_id))

    missing_files = chunk_payload()
    del missing_files["target_files"]
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(missing_files)

    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(target_files=[]))
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(target_files=[3]))
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(acceptance_criteria=[]))
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(acceptance_criteria=[7]))
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(test_spec="tests/test_engine.py"))


def test_execution_chunk_rejects_empty_lists():
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate_json(json.dumps(chunk_payload(target_files=[])))
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate_json(
            json.dumps(chunk_payload(acceptance_criteria=[]))
        )


@pytest.mark.parametrize("absent_key", ["test_file", "physical_inputs", "assertions"])
def test_execution_chunk_rejects_incomplete_test_spec(absent_key):
    incomplete_spec = chunk_spec()
    del incomplete_spec[absent_key]
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(test_spec=incomplete_spec))


def test_execution_chunk_rejects_empty_test_spec():
    with pytest.raises(ValidationError):
        ExecutionChunk.model_validate(chunk_payload(test_spec={}))


# --------------------------------------------------------------------------------------
# ImplementationDossier (AC-09)
# --------------------------------------------------------------------------------------


def test_impl_dossier_valid_untruncated():
    dossier = ImplementationDossier.model_validate_json(json.dumps(impl_payload()))
    assert dossier.stdout_truncated is False
    assert dossier.raw_stdout_sha256 == STDOUT_SHA
    assert dossier.stdout_path == ".evidence/196.02/stdout.txt"
    assert list(dossier.fulfilled_ac_list) == ["AC1", "AC2"]


@pytest.mark.parametrize("hash_length", [7, 12, 40])
def test_impl_dossier_accepts_git_hash_lengths(hash_length):
    dossier = ImplementationDossier.model_validate(
        impl_payload(git_diff_hash=DIFF_HASH[:hash_length])
    )
    assert len(dossier.git_diff_hash) == hash_length


def test_impl_dossier_valid_truncated():
    # Inline stdout is only a prefix; the digest covers the full stdout file, so it must
    # NOT be checked against the truncated prefix.
    dossier = ImplementationDossier.model_validate(
        impl_payload(raw_stdout=STDOUT_TEXT[:40], stdout_truncated=True)
    )
    assert dossier.stdout_truncated is True
    assert dossier.raw_stdout_sha256 == STDOUT_SHA
    assert dossier.raw_stdout == STDOUT_TEXT[:40]


def test_impl_dossier_rejects_bad_hash():
    bad_git_hashes = ("not-a-hash", DIFF_HASH.upper(), "abc12", "a" * 41, "")
    for bad_git_hash in bad_git_hashes:
        with pytest.raises(ValidationError):
            ImplementationDossier.model_validate(
                impl_payload(git_diff_hash=bad_git_hash)
            )
    bad_digests = (
        STDOUT_SHA[:63],
        STDOUT_SHA.upper(),
        STDOUT_SHA + "0",
        "xyz",
        "",
    )
    for bad_digest in bad_digests:
        # stdout_truncated=True isolates the pattern check from the digest-consistency check
        with pytest.raises(ValidationError):
            ImplementationDossier.model_validate(
                impl_payload(raw_stdout_sha256=bad_digest, stdout_truncated=True)
            )


def test_impl_dossier_sha_mismatch():
    tampered_stdout = STDOUT_TEXT.replace("3 passed", "5 passed")
    assert hashlib.sha256(tampered_stdout.encode("utf-8")).hexdigest() != STDOUT_SHA
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(
            impl_payload(raw_stdout=tampered_stdout, stdout_truncated=False)
        )
    unrelated_digest = hashlib.sha256(b"unrelated evidence").hexdigest()
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(
            impl_payload(raw_stdout_sha256=unrelated_digest, stdout_truncated=False)
        )
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate_json(
            json.dumps(impl_payload(raw_stdout=tampered_stdout, stdout_truncated=False))
        )


def test_impl_dossier_rejects_bad_field_types():
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(impl_payload(stdout_path=""))
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(impl_payload(stdout_truncated="maybe"))
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(impl_payload(fulfilled_ac_list="AC1"))
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(impl_payload(fulfilled_ac_list=[1, 2]))
    missing_path = impl_payload()
    del missing_path["stdout_path"]
    with pytest.raises(ValidationError):
        ImplementationDossier.model_validate(missing_path)


# --------------------------------------------------------------------------------------
# PhysicsAutopsyReport (AC-10)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["SYNTAX", "RUNTIME", "TEST_FAIL", "SPOOFING"])
def test_autopsy_valid_stages(stage):
    report = PhysicsAutopsyReport.model_validate_json(
        json.dumps(autopsy_payload(failure_stage=stage))
    )
    assert report.failure_stage == stage
    assert report.failing_file_line == "tests/test_engine.py:42"


def test_autopsy_rejects_empty_file_line():
    malformed_locations = (
        "",
        "src/physics_engine.py",
        "src/physics_engine.py:",
        "src/physics_engine.py:abc",
        ":42",
    )
    for failing_file_line in malformed_locations:
        with pytest.raises(ValidationError):
            PhysicsAutopsyReport.model_validate(
                autopsy_payload(failing_file_line=failing_file_line)
            )
    with pytest.raises(ValidationError):
        PhysicsAutopsyReport.model_validate_json(
            json.dumps(autopsy_payload(failing_file_line=""))
        )
    missing_location = autopsy_payload()
    del missing_location["failing_file_line"]
    with pytest.raises(ValidationError):
        PhysicsAutopsyReport.model_validate(missing_location)


@pytest.mark.parametrize("bad_stage", ["UNKNOWN", "syntax", "", "TEST"])
def test_autopsy_rejects_invalid_stage(bad_stage):
    with pytest.raises(ValidationError):
        PhysicsAutopsyReport.model_validate(autopsy_payload(failure_stage=bad_stage))


# --------------------------------------------------------------------------------------
# HandoffEnvelope (AC-12)
# --------------------------------------------------------------------------------------


def test_envelope_valid_epoch_timestamp():
    produced_at = time.time()
    envelope = HandoffEnvelope.model_validate(
        envelope_payload(produced_at_utc=produced_at)
    )
    assert isinstance(envelope.produced_at_utc, float)
    assert envelope.produced_at_utc == produced_at
    assert envelope.payload["verdict"] == "PASS"


def test_envelope_timestamp_format():
    produced_at = time.time()
    envelope = HandoffEnvelope.model_validate(
        envelope_payload(produced_at_utc=produced_at)
    )
    wire_text = envelope.model_dump_json()
    wire = json.loads(wire_text)
    assert isinstance(wire["produced_at_utc"], float)
    assert abs(wire["produced_at_utc"] - produced_at) < 1e-6

    # byte-for-byte roundtrip stability
    restored_text = HandoffEnvelope.model_validate_json(wire_text).model_dump_json()
    assert restored_text == wire_text
    assert restored_text.encode("utf-8") == wire_text.encode("utf-8")

    for iso_string in ("2026-09-25T00:00:00Z", "2026-09-25T00:00:00+00:00"):
        with pytest.raises(ValidationError):
            HandoffEnvelope.model_validate(envelope_payload(produced_at_utc=iso_string))
        with pytest.raises(ValidationError):
            HandoffEnvelope.model_validate_json(
                json.dumps(envelope_payload(produced_at_utc=iso_string))
            )


@pytest.mark.parametrize(
    "field_name",
    [
        "schema_name",
        "schema_version",
        "schema_sha256",
        "producer_provider",
        "produced_at_utc",
        "payload",
    ],
)
def test_envelope_rejects_missing_fields(field_name):
    incomplete = envelope_payload()
    del incomplete[field_name]
    with pytest.raises(ValidationError):
        HandoffEnvelope.model_validate(incomplete)


# --------------------------------------------------------------------------------------
# Cross-model invariants (AC-11, AC-46)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model_cls, builder", ALL_MODELS)
def test_roundtrip_all_models(model_cls, builder):
    original = model_cls.model_validate(builder())
    wire_text = original.model_dump_json()
    restored = model_cls.model_validate_json(wire_text)
    assert restored == original
    assert restored.model_dump_json() == wire_text
    assert restored.model_dump_json().encode("utf-8") == wire_text.encode("utf-8")

    wire_with_extra = json.loads(wire_text)
    wire_with_extra["unexpected_key"] = "value"
    with pytest.raises(ValidationError):
        model_cls.model_validate_json(json.dumps(wire_with_extra))


def test_roundtrip_plan_dossier():
    _assert_roundtrip(PlanDossier, plan_payload())


def test_roundtrip_audit_feedback():
    _assert_roundtrip(AuditFeedback, audit_payload())


def test_roundtrip_execution_chunk():
    _assert_roundtrip(ExecutionChunk, chunk_payload())


def test_roundtrip_implementation_dossier():
    _assert_roundtrip(ImplementationDossier, impl_payload())


def test_roundtrip_physics_autopsy_report():
    _assert_roundtrip(PhysicsAutopsyReport, autopsy_payload())


def test_roundtrip_handoff_envelope():
    _assert_roundtrip(HandoffEnvelope, envelope_payload())


@pytest.mark.parametrize("model_cls, builder", ALL_MODELS)
def test_model_config_extra_forbid(model_cls, builder):
    config = model_cls.model_config
    assert config.get("extra") == "forbid"
    assert config.get("frozen") is True
    model_cls.model_validate(builder())


@pytest.mark.parametrize("model_cls, builder", ALL_MODELS)
def test_models_reject_extra_keys_via_json(model_cls, builder):
    payload_with_extra = {**builder(), "undeclared_field": 1}
    with pytest.raises(ValidationError):
        model_cls.model_validate_json(json.dumps(payload_with_extra))
    with pytest.raises(ValidationError):
        model_cls.model_validate(payload_with_extra)


@pytest.mark.parametrize("model_cls, builder", ALL_MODELS)
def test_models_reject_attribute_mutation(model_cls, builder):
    instance = model_cls.model_validate(builder())
    first_field = next(iter(model_cls.model_fields))
    with pytest.raises((ValidationError, TypeError)):
        setattr(instance, first_field, getattr(instance, first_field))


# --------------------------------------------------------------------------------------
# Structural contract checks over this file (T1 - T8)
# --------------------------------------------------------------------------------------


def test_suite_file_exists_syntax_and_zero_forbidden_tokens():
    assert SELF_PATH.is_file()
    decoded = SELF_PATH.read_bytes().decode("utf-8")
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


def test_v4_schemas_import_is_unguarded():
    # A try/except around the schema import would mask the RED state.
    for node in ast.walk(_self_tree()):
        if isinstance(node, ast.Try):
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom):
                    assert inner.module != "v4_schemas"


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
    for literal in ("produced_at_utc", "float", "ValidationError", "model_dump_json"):
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
# Clean-interpreter check of the real module (T9 counterpart once GREEN)
# --------------------------------------------------------------------------------------


def test_v4_schemas_importable_and_enforcing_in_clean_subprocess():
    script = (
        "import json\n"
        "import pydantic\n"
        "import v4_schemas as schemas\n"
        f"names = {list(SCHEMA_CLASS_NAMES)!r}\n"
        "missing = [n for n in names if not hasattr(schemas, n)]\n"
        "feedback = schemas.AuditFeedback(score=90, verdict='PASS', violations=[],"
        " missing_acceptance_criteria=[])\n"
        "rejected = False\n"
        "try:\n"
        "    schemas.AuditFeedback(score=10, verdict='FAIL', violations=[],"
        " missing_acceptance_criteria=[])\n"
        "except pydantic.ValidationError:\n"
        "    rejected = True\n"
        "print(json.dumps({'missing': missing, 'dump': feedback.model_dump_json(),"
        " 'rejected': rejected}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["missing"] == []
    assert json.loads(report["dump"])["verdict"] == "PASS"
    assert report["rejected"] is True
