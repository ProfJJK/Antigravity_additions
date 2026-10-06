"""Routing adoption and independent supervisor spending guards using real files/SQL.

Native envelopes and legacy receipts here are explicitly labelled physical
Python fixtures. They establish compatibility and budgeting, not model access,
Windows identity isolation, or a successful production data migration.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from cochem_pipeline.store import JobStore, canonical_json, output_digest
from pipeline_tests.test_routing_integration import CLI_FIXTURE, _fail_fixture, _finish_fixture, _policy
from supervisor_tests.test_engine import LocalSupervisor


def _legacy_complete(store, node, output, provider):
    child = subprocess.run([sys.executable, "-c",
        "import hashlib,json,os,sys; raw=sys.stdin.buffer.read(); print(json.dumps({'pid':os.getpid(),'output_sha256':hashlib.sha256(raw).hexdigest()}))"],
        input=canonical_json(output), capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert child.returncode == 0, child.stderr
    receipt = {**json.loads(child.stdout), "provider": provider, "exit_code": child.returncode,
               "session_id": "legacy-physical-protocol-fixture", "execution_kind": "physical-legacy-receipt-fixture"}
    assert receipt["output_sha256"] == output_digest(output)
    return store.complete(node["job_id"], node["attempt_id"], node["fencing_token"], output, receipt)


def test_legacy_accepted_artifact_survives_routing_adoption_for_pending_sibling(tmp_path):
    legacy = JobStore(tmp_path / "job_board.db")
    workflow = legacy.submit("Draft two operational chapters", ["REQ-1"], 2)
    manifest = legacy.claim("legacy-controller")
    assert manifest["routing"] is None
    _legacy_complete(legacy, manifest, {"chapters": [
        {"chapter_id": f"chapter-{index}", "title": f"Chapter {index}", "requirements": ["REQ-1"]}
        for index in range(2)]}, "codex")
    first = legacy.claim("legacy-controller", worker_slot="slot1")
    old_output = {"chapter_id": first["chapter_id"], "requirements_traced": ["REQ-1"],
        "wbs_tasks_defined": [{"task_id": "legacy-fixture-task", "description": "Preserved historical acceptance"}],
        "artifact_uri": f"db://{workflow['workflow_id']}/{first['chapter_id']}",
        "artifact_text": "Immutable artifact accepted before routing adoption."}
    accepted = _legacy_complete(legacy, first, old_output, "claude")
    with sqlite3.connect(legacy.path) as connection:
        before = connection.execute("SELECT output_json,receipt_json,output_sha256 FROM pipeline_outputs WHERE job_id=?",
                                    (first["job_id"],)).fetchone()
    upgraded = JobStore(legacy.path, routing_policy=_policy())
    assert upgraded.get(first["job_id"])["receipt"] == accepted["receipt"]
    remaining = upgraded.claim("new-routing-controller", worker_slot="slot2")
    assert remaining["kind"] == "CHAPTER_DRAFT" and remaining["job_id"] != first["job_id"]
    assert remaining["route"]["model"] == "gemini-3.8-flash"
    fixture = tmp_path / "explicit_routing_fixture.py"
    fixture.write_text(CLI_FIXTURE, encoding="utf-8")
    _finish_fixture(upgraded, remaining, fixture, tmp_path)
    synthesis = upgraded.claim("new-routing-controller", worker_slot="slot1")
    assert synthesis["route"]["model"] == "gemini-3.1-pro"
    _finish_fixture(upgraded, synthesis, fixture, tmp_path)
    final = upgraded.workflow(workflow["workflow_id"])
    assert final["status"] == "COMPLETED"
    assert next(artifact for artifact in final["artifacts"] if artifact["job_id"] == first["job_id"])["artifact_text"] == old_output["artifact_text"]
    with sqlite3.connect(upgraded.path) as connection:
        assert connection.execute("SELECT output_json,receipt_json,output_sha256 FROM pipeline_outputs WHERE job_id=?",
                                  (first["job_id"],)).fetchone() == before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE pipeline_artifacts SET artifact_text='replacement' WHERE job_id=?", (first["job_id"],))


def test_independent_supervisor_does_not_reserve_repairs_for_real_routing_quota_wait(tmp_path):
    supervisor = LocalSupervisor(tmp_path)
    pipeline = Path(supervisor.config["pipeline_private_root"])
    store = JobStore(pipeline / "job_board.db", max_attempts=1, routing_policy=_policy(backoff=1))
    store.submit("List concise checks", ["REQ-1"], 1)
    fixture = pipeline / "explicit_routing_fixture.py"
    fixture.write_text(CLI_FIXTURE, encoding="utf-8")
    for index in range(3):
        node = store.claim("fixture-controller", worker_slot="slot1")
        assert node["route"]["candidate_index"] == index
        _fail_fixture(store, node, fixture, pipeline, retry_after=.05)
    observation = supervisor.tick(ignore_startup_grace=True)
    assert observation["health"]["routing_wait_count"] == 1
    assert not any(item["repairable"] for item in observation["incidents"])
    assert supervisor.runner.repair_calls == []
    assert supervisor.ledger.list_incidents() == []
    with sqlite3.connect(supervisor.ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM supervisor_attempts").fetchone()[0] == 0
