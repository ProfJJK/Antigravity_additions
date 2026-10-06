"""Pure acceptance-report checks against labelled, deterministic data fixtures.

These fixtures represent the controller's JSON contract. They do not execute or
claim to be Codex, Claude, Gemini, or a live Windows service acceptance run.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_pipeline_acceptance.py"
spec = importlib.util.spec_from_file_location("pipeline_acceptance_report", SCRIPT)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


@pytest.fixture
def document_contract():
    providers = {name: {"model": f"fixture-{name}"} for name in ("codex", "claude", "gemini")}
    workflow_id = "fixture-workflow"
    root = {"job_id": workflow_id, "kind": "MACRO_PLANNING_REQUEST", "status": "COMPLETED",
            "payload": {"requirements": ["REQ-1"], "chapter_count": 6}}
    workflow = {"workflow_id": workflow_id, "status": "COMPLETED", "root": root,
                "jobs": [root], "artifacts": [], "events": []}

    def completed(job_id, kind, provider, output, started, finished, index=None):
        receipt = {"provider": provider, "pid": 100 + len(workflow["jobs"]), "exit_code": 0,
                   "subscription_verified": True, "session_id": f"fixture-session-{job_id}",
                   "requested_model": providers[provider]["model"], "reported_model": providers[provider]["model"],
                   "output_sha256": digest(output), "stdout_sha256": "a" * 64,
                   "started_at": started, "finished_at": finished,
                   "worker_account": f"fixture-account-{index}"}
        job = {"job_id": job_id, "kind": kind, "status": "COMPLETED", "output": output, "receipt": receipt,
               "output_sha256": digest(output), "payload": {"chapter_index": index, "requirements": ["REQ-1"]}}
        if index is not None:
            job.update(chapter_id=f"chapter-{index}", worker_slot=f"slot-{index}")
        if "artifact_text" in output:
            chapter_id = job.get("chapter_id", "synthesis")
            text = output["artifact_text"]
            sha = hashlib.sha256(text.encode()).hexdigest()
            job["artifact_sha256"] = sha
            workflow["artifacts"].append({"job_id": job_id, "workflow_id": workflow_id, "chapter_id": chapter_id,
                "artifact_uri": f"db://{workflow_id}/{chapter_id}", "artifact_text": text, "sha256": sha})
        workflow["jobs"].append(job)
        workflow["events"].append({"event": "COMPLETED", "job_id": job_id})

    completed("manifest", "MANIFEST_GENERATOR", "codex", {"chapters": [
        {"chapter_id": f"chapter-{index}", "title": f"Fixture chapter {index}", "requirements": ["REQ-1"]}
        for index in range(6)]}, 90, 95)
    for index in range(6):
        completed(f"draft-{index}", "CHAPTER_DRAFT", "codex" if index % 2 == 0 else "claude",
                  {"chapter_id": f"chapter-{index}", "requirements_traced": ["REQ-1"],
                   "wbs_tasks_defined": [{"task_id": f"task-{index}", "description": "Fixture task"}],
                   "artifact_uri": f"db://{workflow_id}/chapter-{index}",
                   "artifact_text": f"Fixture chapter {index}: requirements and WBS."},
                  100 + 4 * (index // 4), 103 + 4 * (index // 4), index)
    hashes = {row["chapter_id"]: row["sha256"] for row in workflow["artifacts"]}
    completed("synthesis", "SYNTHESIS", "gemini", {"artifact_text": "Fixture final SRS/WBS", "chapter_hashes": hashes}, 110, 115)
    workflow["events"].append({"event": "SYNTHESIS_RELEASED", "details": {"chapter_hashes": hashes}})
    return workflow, providers


def test_pure_report_checks_hashes_routing_identity_barrier_and_overlap(document_contract):
    workflow, providers = document_contract
    workflow["jobs"][1]["receipt"]["reported_model"] = None
    report = acceptance.validate_workflow(workflow, providers)
    assert report["verified"] is True
    assert report["accepted_chapter_peak"] == 4
    assert report["validated_process_receipts"] == 8
    assert report["provider_counts"] == {"codex": 4, "claude": 3, "gemini": 1}
    assert report["jobs_without_reported_model_metadata"] == ["manifest"]
    assert report["stdout_content_independently_recomputed"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("pid", 0, "physical PID"),
    ("exit_code", 1, "exit successfully"),
    ("session_id", "", "session ID"),
    ("output_sha256", "0" * 64, "output hash"),
    ("provider", "claude", "unexpected provider"),
    ("reported_model", "pretend-model", "different model"),
    ("subscription_verified", False, "subscription verification"),
    ("execution_kind", "test-emulator", "test/emulator"),
])
def test_pure_report_rejects_invalid_process_receipts(document_contract, field, value, reason):
    workflow, providers = document_contract
    workflow["jobs"][1]["receipt"][field] = value
    with pytest.raises(ValueError, match=reason):
        acceptance.validate_workflow(workflow, providers)


def test_pure_report_rejects_mutated_artifacts_duplicate_identity_and_barrier(document_contract):
    original, providers = document_contract
    changed = copy.deepcopy(original)
    changed["artifacts"][0]["artifact_text"] = "Replacement"
    with pytest.raises(ValueError, match="artifact text"):
        acceptance.validate_workflow(changed, providers)
    changed = copy.deepcopy(original)
    changed["jobs"][3]["worker_slot"] = changed["jobs"][2]["worker_slot"]
    with pytest.raises(ValueError, match="distinct persistent"):
        acceptance.validate_workflow(changed, providers)
    changed = copy.deepcopy(original)
    changed["events"].append(changed["events"][-1])
    with pytest.raises(ValueError, match="one synthesis barrier"):
        acceptance.validate_workflow(changed, providers)


@pytest.mark.parametrize("serialized", [True, False])
def test_pure_report_rejects_serial_or_more_than_four_concurrent_chapters(document_contract, serialized):
    workflow, providers = document_contract
    for index, job in enumerate(job for job in workflow["jobs"] if job["kind"] == "CHAPTER_DRAFT"):
        job["receipt"]["started_at"] = 100 + index if serialized else 100
        job["receipt"]["finished_at"] = 100.5 + index if serialized else 103
    with pytest.raises(ValueError, match="overlap"):
        acceptance.validate_workflow(workflow, providers)


def test_script_requires_explicit_live_flag_before_reading_config_or_creating_report(tmp_path):
    report = tmp_path / "not-created.json"
    with pytest.raises(SystemExit) as failure:
        acceptance.main(["--config", str(tmp_path / "absent-config.json"), "--report", str(report)])
    assert failure.value.code == 2
    assert not report.exists()


def test_help_runs_in_a_real_process_without_submitting_work():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert "--run-live" in proc.stdout


def test_failure_report_is_preserved_when_configuration_is_unavailable(tmp_path):
    report = tmp_path / "failed-report.json"
    result = acceptance.main(["--config", str(tmp_path / "absent.json"), "--report", str(report), "--run-live"])
    saved = json.loads(report.read_text())
    assert result == 1
    assert saved["status"] == "FAILED"
    assert saved["workflow_id"] is None
    assert "FileNotFoundError" in saved["error"]
