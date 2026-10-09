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
from cochem_pipeline.routing import load_routing_policy, score_task


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
                "jobs": [root], "artifacts": [], "events": [],
                "acceptance_admission":{"configured_max_execution_slots":4,"configured_worker_count":6,
                    "controller_hardware_max_agents":4,"configuration_sha256":"c"*64,
                    "controller_instance_id":"contract-fixture-controller"}}

    def completed(job_id, kind, provider, output, started, finished, index=None):
        policy = load_routing_policy()
        payload = {"chapter_index":index,"requirements":["REQ-1"]}
        scoring = score_task(kind,payload)
        candidates = [target.as_dict() for target in policy.candidates(scoring['score'],kind)]
        selected = next(index for index,target in enumerate(candidates) if target["provider"]==provider)
        route = {**candidates[selected],"score":scoring['score'],"tier":scoring['tier'],"candidate_index":selected,"cycle":0,
                 "policy_digest":policy.digest,"reservation_sha256":"b"*64}
        model = route["model"]
        receipt = {"execution_kind":"native_cli", "provider": provider, "pid": 100 + len(workflow["jobs"]), "exit_code": 0,
                   "subscription_verified": True, "session_id": f"fixture-session-{job_id}",
                   "requested_model": model, "reported_model": model,
                   "requested_effort":route["reasoning_effort"],"route_reservation_sha256":"b"*64,
                   "output_sha256": digest(output), "stdout_sha256": "a" * 64,
                   "started_at": started, "finished_at": finished,
                   "worker_account": f"fixture-account-{index}"}
        job = {"job_id": job_id, "kind": kind, "status": "COMPLETED", "output": output, "receipt": receipt,
               "route":route,"routing":{"policy":policy.as_dict(),"policy_hash":policy.digest,
                   "score_details":scoring,"candidates":candidates},
               "output_sha256": digest(output), "payload": payload}
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
        {"chapter_id": f"chapter-{index}", "title": f"Fixture chapter {index}", "requirements": ["REQ-1"],
         'wbs_tasks_defined': [{'id': f'task-{index}', 'description': 'Fixture task', 'requirements': ['REQ-1']}]}
        for index in range(6)]}, 90, 95)
    for index in range(6):
        completed(f"draft-{index}", "CHAPTER_DRAFT", "codex" if index % 2 == 0 else "claude",
                  {"chapter_id": f"chapter-{index}", "requirements_traced": ["REQ-1"],
                   "wbs_tasks_defined": [{'id': f'task-{index}', 'description': 'Fixture task', 'requirements': ['REQ-1']}],
                   "artifact_uri": f"db://{workflow_id}/chapter-{index}",
                   "artifact_text": f"Fixture chapter {index}: requirements and WBS."},
                  100 + 4 * (index // 4), 103 + 4 * (index // 4), index)
        workflow['jobs'][-1]['payload'].update(
            wbs_tasks_defined=workflow['jobs'][-1]['output']['wbs_tasks_defined'],
            manifest_output_sha256=workflow['jobs'][1]['output_sha256'])
    hashes = {row["chapter_id"]: row["sha256"] for row in workflow["artifacts"]}
    chapters = workflow['jobs'][2:]
    output_hashes = {job['chapter_id']: job['output_sha256'] for job in chapters}
    wbs = {job['chapter_id']: job['output']['wbs_tasks_defined'] for job in chapters}
    coverage = {'schema': 'cochem-document-coverage/4.2.7',
        'requirements': [{'requirement': 'REQ-1', 'owning_chapters': list(hashes),
            'traced_by_chapters': list(hashes), 'overlap': True, 'gap': False}],
        'gaps': [], 'overlaps': ['REQ-1'], 'chapter_hashes': hashes, 'chapter_output_hashes': output_hashes,
        'manifest_output_sha256': workflow['jobs'][1]['output_sha256'], 'complete': True}
    commitments = {'chapter_hashes': hashes, 'chapter_output_hashes': output_hashes,
                   'coverage_report_sha256': digest(coverage), 'wbs_tasks_by_chapter': wbs}
    completed("synthesis", "SYNTHESIS", "gemini", {"artifact_text": "Fixture final SRS/WBS", **commitments}, 110, 115)
    workflow['jobs'][-1]['payload'].update(**commitments, coverage_report=coverage,
        chapters=[{'chapter_id': job['chapter_id'], 'accepted_output': copy.deepcopy(job['output']),
                   'output_sha256': job['output_sha256'], 'sha256': job['artifact_sha256']} for job in chapters])
    workflow["events"].append({"event": "SYNTHESIS_RELEASED", "details": {
        'chapter_hashes': hashes, 'chapter_output_hashes': output_hashes,
        'coverage_report': coverage, 'coverage_report_sha256': digest(coverage)}})
    for job in workflow['jobs'][1:]:
        policy = load_routing_policy()
        scoring = score_task(job['kind'], job['payload'])
        candidates = [target.as_dict() for target in policy.candidates(scoring['score'], job['kind'])]
        selected = next(index for index, target in enumerate(candidates)
                        if target['provider'] == job['receipt']['provider'])
        job['routing'].update(score_details=scoring, candidates=candidates)
        job['route'].update(**candidates[selected], score=scoring['score'], tier=scoring['tier'], candidate_index=selected)
        job['receipt'].update(requested_model=job['route']['model'], reported_model=job['route']['model'],
                              requested_effort=job['route']['reasoning_effort'])
    return workflow, providers


def test_pure_report_checks_hashes_routing_identity_barrier_and_overlap(document_contract):
    workflow, providers = document_contract
    workflow["jobs"][1]["receipt"]["reported_model"] = None
    report = acceptance.validate_workflow(workflow, providers,admission=workflow["acceptance_admission"])
    assert report["verified"] is True
    assert report["accepted_chapter_peak"] == 4
    assert report["validated_process_receipts"] == 8
    assert report["provider_counts"] == {"codex": 4, "claude": 3, "gemini": 1}
    assert report["jobs_without_reported_model_metadata"] == ["manifest"]
    assert report["stdout_content_independently_recomputed"] is False


@pytest.mark.parametrize('key', ['chapter_output_hashes', 'coverage_report_sha256', 'wbs_tasks_by_chapter'])
def test_acceptance_rejects_synthesis_missing_structured_commitments(document_contract, key):
    workflow, providers = document_contract
    synthesis = workflow['jobs'][-1]
    synthesis['output'].pop(key)
    synthesis['output_sha256'] = synthesis['receipt']['output_sha256'] = digest(synthesis['output'])
    with pytest.raises(ValueError, match='commitments'):
        acceptance.validate_workflow(workflow, providers, admission=workflow['acceptance_admission'])


def test_acceptance_detects_structured_output_change_with_unchanged_artifact_text(document_contract):
    workflow, providers = document_contract
    chapter = workflow['jobs'][2]
    chapter['output']['additional_commitment'] = {'important': 'new accepted output bytes'}
    chapter['output_sha256'] = chapter['receipt']['output_sha256'] = digest(chapter['output'])
    with pytest.raises(ValueError, match='chapter_output_hashes'):
        acceptance.validate_workflow(workflow, providers, admission=workflow['acceptance_admission'])


@pytest.mark.parametrize("field,value,reason", [
    ("pid", 0, "physical PID"),
    ("exit_code", 1, "exit successfully"),
    ("session_id", "", "session ID"),
    ("output_sha256", "0" * 64, "output hash"),
    ("provider", "claude", "selected routing provider"),
    ("reported_model", "pretend-model", "different model"),
    ("subscription_verified", False, "subscription verification"),
    ("execution_kind", "test-emulator", "test/emulator"),
])
def test_pure_report_rejects_invalid_process_receipts(document_contract, field, value, reason):
    workflow, providers = document_contract
    workflow["jobs"][1]["receipt"][field] = value
    with pytest.raises(ValueError, match=reason):
        acceptance.validate_workflow(workflow, providers,admission=workflow["acceptance_admission"])


def test_pure_report_rejects_mutated_artifacts_duplicate_identity_and_barrier(document_contract):
    original, providers = document_contract
    changed = copy.deepcopy(original)
    changed["artifacts"][0]["artifact_text"] = "Replacement"
    with pytest.raises(ValueError, match="artifact text"):
        acceptance.validate_workflow(changed, providers,admission=changed["acceptance_admission"])
    changed = copy.deepcopy(original)
    changed["jobs"][3]["worker_slot"] = changed["jobs"][2]["worker_slot"]
    with pytest.raises(ValueError, match="distinct persistent"):
        acceptance.validate_workflow(changed, providers,admission=changed["acceptance_admission"])
    changed = copy.deepcopy(original)
    changed["events"].append(changed["events"][-1])
    with pytest.raises(ValueError, match="one synthesis barrier"):
        acceptance.validate_workflow(changed, providers,admission=changed["acceptance_admission"])


@pytest.mark.parametrize("serialized", [True, False])
def test_pure_report_rejects_serial_or_above_configured_capacity_chapters(document_contract, serialized):
    workflow, providers = document_contract
    for index, job in enumerate(job for job in workflow["jobs"] if job["kind"] == "CHAPTER_DRAFT"):
        job["receipt"]["started_at"] = 100 + index if serialized else 100
        job["receipt"]["finished_at"] = 100.5 + index if serialized else 103
    with pytest.raises(ValueError, match="overlap"):
        acceptance.validate_workflow(workflow, providers,admission=workflow["acceptance_admission"])


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


def test_all_six_chapters_can_overlap_when_captured_hardware_capacity_permits(document_contract):
    workflow,providers=document_contract
    admission=workflow['acceptance_admission']
    admission.update(configured_max_execution_slots=64,controller_hardware_max_agents=6)
    for job in workflow['jobs']:
        if job['kind']=='CHAPTER_DRAFT':
            job['receipt'].update(started_at=100,finished_at=103)
    report=acceptance.validate_workflow(workflow,providers,admission=admission)
    assert report['accepted_chapter_peak']==6
    assert report['concurrency']['configured_hardware_ceiling']==6


@pytest.mark.parametrize('provider', ['codex','gemini'])
def test_sixty_one_non_claude_agents_are_permitted_by_configured_hardware_capacity(provider):
    intervals=[(provider,10.,11.) for _ in range(61)]
    assert acceptance.validate_execution_overlap(intervals,64)['native_peak']==61


def test_claude_twenty_agent_ceiling_remains_independent_of_larger_host_capacity():
    intervals=[('claude',10.,11.) for _ in range(21)]
    with pytest.raises(ValueError,match='Claude CLI'):
        acceptance.validate_execution_overlap(intervals,64)


def test_unbound_or_mismatched_hardware_capacity_cannot_pass_acceptance(document_contract):
    workflow,providers=document_contract
    with pytest.raises(ValueError,match='captured configured'):
        acceptance.validate_workflow(workflow,providers)
    evidence=copy.deepcopy(workflow['acceptance_admission'])
    evidence['controller_hardware_max_agents']=64
    with pytest.raises(ValueError,match='authenticated controller'):
        acceptance.validate_workflow(workflow,providers,admission=evidence)


@pytest.mark.parametrize('provider',['codex','claude'])
def test_synthesis_acceptance_uses_its_actual_complexity_route_not_a_fixed_gemini_model(document_contract,provider):
    workflow,providers=document_contract
    synthesis=next(job for job in workflow['jobs'] if job['kind']=='SYNTHESIS')
    candidates=synthesis['routing']['candidates']
    index=next(index for index,target in enumerate(candidates) if target['provider']==provider)
    target=candidates[index]
    synthesis['route'].update(target,candidate_index=index)
    synthesis['receipt'].update(provider=provider,requested_model=target['model'],reported_model=target['model'],
                                requested_effort=target['reasoning_effort'])
    report=acceptance.validate_workflow(workflow,providers,admission=workflow['acceptance_admission'])
    assert report['routing_decisions'][-1]['provider']==provider
