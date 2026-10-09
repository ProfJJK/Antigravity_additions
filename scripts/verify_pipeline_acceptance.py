"""Verify one live six-chapter workflow through the authenticated Warden API.

Explicit --run-live consumes CLI subscription quota. Importing this module or
requesting --help never submits work. The client can run on Windows or another
local Python runtime able to reach the Windows service at 127.0.0.1.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

from cochem_pipeline.service import ControlClient
from cochem_pipeline.store import JobStore, artifact_digest, output_digest
from cochem_supervisor.probes import configured_tiers, verify_routing_assignment


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_workflow(workflow: dict, providers: dict, routing_policy: dict | None = None, *, admission: dict | None = None) -> dict:
    """Check a controller snapshot; this pure function does not execute models.

    Provider identity is taken from trusted process receipts, never generated
    text. Output and artifact hashes are recomputed from the returned content.
    Raw stdout remains private to the service, so its digest is format-checked
    and retained, rather than falsely claimed to be independently recomputed.
    """
    require(isinstance(admission,dict), 'Acceptance requires the captured configured hardware admission limit')
    limit=admission.get('configured_max_execution_slots')
    workers=admission.get('configured_worker_count')
    observed=admission.get('controller_hardware_max_agents')
    require(type(limit) is int and 1<=limit<=256 and type(workers) is int and 1<=workers<=256
            and type(observed) is int and observed==min(limit,workers),
            'Configured admission limit does not match the authenticated controller hardware ceiling')
    require(isinstance(admission.get('configuration_sha256'),str)
            and re.fullmatch('[0-9a-f]{64}',admission['configuration_sha256'])
            and isinstance(admission.get('controller_instance_id'),str) and admission['controller_instance_id'],
            'Admission evidence requires captured configuration hash and controller instance identity')
    require(isinstance(workflow, dict), "Workflow must be an object")
    require(workflow.get("status") == "COMPLETED", "Workflow has not completed successfully")
    root = workflow.get("root", {})
    require(root.get("status") == "COMPLETED", "Root is not completed")
    jobs, artifacts, events = workflow.get("jobs"), workflow.get("artifacts"), workflow.get("events")
    require(isinstance(jobs, list) and isinstance(artifacts, list) and isinstance(events, list), "Missing jobs/artifacts/event ledger")
    require(all(isinstance(job, dict) for job in jobs), "Malformed job record")
    require(len({job.get("job_id") for job in jobs}) == len(jobs), "Duplicate logical jobs")
    chapters = [job for job in jobs if job.get("kind") == "CHAPTER_DRAFT"]
    syntheses = [job for job in jobs if job.get("kind") == "SYNTHESIS"]
    manifests = [job for job in jobs if job.get("kind") == "MANIFEST_GENERATOR"]
    require(len(chapters) == 6 and len(syntheses) == 1 and len(manifests) == 1, "Expected six chapters, one manifest and one logical synthesis")
    require(len(jobs) == 9, "Unexpected DAG job count")
    require({job.get("payload", {}).get("chapter_index") for job in chapters} == set(range(6)), "Manifest chapter positions are incomplete")
    chapter_ids = {job.get("chapter_id") for job in chapters}
    require(len(chapter_ids) == 6 and all(isinstance(item, str) and item for item in chapter_ids), "Chapter IDs are not unique")
    manifest_chapters = manifests[0].get("output", {}).get("chapters")
    require(isinstance(manifest_chapters, list) and len(manifest_chapters) == 6
            and all(isinstance(item, dict) for item in manifest_chapters), "Manifest did not define six chapters")
    require({item.get("chapter_id") for item in manifest_chapters} == chapter_ids, "Manifest and executed chapter ownership differ")
    covered = set()
    manifest_task_ids = set()
    for job in chapters:
        assigned = manifest_chapters[job["payload"]["chapter_index"]]
        require(assigned.get("chapter_id") == job["chapter_id"]
                and assigned.get("requirements") == job["payload"].get("requirements"), "Manifest assignment changed before drafting")
        covered.update(assigned["requirements"])
        tasks = JobStore._wbs(assigned.get('wbs_tasks_defined'), assigned['requirements'])
        ids = {task['id'] for task in tasks}
        require(not ids & manifest_task_ids, 'Manifest WBS ownership is duplicated')
        manifest_task_ids.update(ids)
        require(job['payload'].get('wbs_tasks_defined') == tasks
                and job['payload'].get('manifest_output_sha256') == manifests[0].get('output_sha256'),
                'Manifest WBS/output commitment changed before drafting')
    require(covered == set(root.get("payload", {}).get("requirements", [])), "Manifest did not cover the requested requirements")
    identities = [job.get("worker_slot") for job in chapters]
    require(all(isinstance(item, str) and item for item in identities) and len(set(identities)) == 6, "Six distinct persistent chapter identities are required")
    require(all(isinstance(item, dict) for item in artifacts), "Malformed artifact record")
    chapter_artifacts = [item for item in artifacts if item.get("chapter_id") in chapter_ids]
    final_artifacts = [item for item in artifacts if item.get("chapter_id") == "synthesis"]
    require(len(chapter_artifacts) == 6 and len(final_artifacts) == 1 and len(artifacts) == 7,
            "Expected exactly six immutable chapter artifacts and one final document")
    by_job = {artifact.get("job_id"): artifact for artifact in artifacts}
    require(len(by_job) == 7, "Multiple artifacts claim the same job")
    intervals, accounts, sessions, unreported, counts, routing_decisions = [], [], [], [], Counter(), []
    unreported_effort = []
    all_intervals = []
    for job in [*manifests, *chapters, *syntheses]:
        job_id, kind = job["job_id"], job["kind"]
        require(job.get("status") == "COMPLETED", f"Job {job_id} is not completed")
        output, receipt = job.get("output"), job.get("receipt")
        require(isinstance(output, dict) and isinstance(receipt, dict), f"Job {job_id} lacks an output/receipt")
        require(receipt.get("execution_kind") == "native_cli", f"Job {job_id} has a test/emulator receipt, not a live receipt")
        require(receipt.get("subscription_verified") is True, f"Job {job_id} lacks native subscription verification")
        assignment = verify_routing_assignment(job, routing_policy)
        expected_provider, model = assignment["provider"], assignment["model"]
        require(expected_provider in providers, f"Missing configured CLI provider for {expected_provider}")
        routing_decisions.append({"job_id":job_id, **assignment})
        reported = receipt.get("reported_model")
        require(reported in (None, model), f"Job {job_id} reported a different model")
        if expected_provider == "gemini":
            require(reported == model, "Gemini must report the assigned configured model")
        if reported is None:
            unreported.append(job_id)
        if assignment['reasoning_effort'] is not None and receipt.get('reported_effort') is None:
            unreported_effort.append(job_id)
        require(type(receipt.get("pid")) is int and 0 < receipt["pid"] <= 0xFFFFFFFF, f"Job {job_id} lacks a physical PID")
        require(type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0, f"Job {job_id} did not exit successfully")
        session = receipt.get("session_id")
        require(isinstance(session, str) and bool(session.strip()), f"Job {job_id} lacks a native session ID")
        sessions.append((expected_provider, session))
        digest = output_digest(output)
        require(receipt.get("output_sha256") == digest == job.get("output_sha256"), f"Job {job_id} output hash mismatch")
        require(isinstance(receipt.get("stdout_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", receipt["stdout_sha256"]),
                f"Job {job_id} lacks its private stdout hash")
        started, finished = receipt.get("started_at"), receipt.get("finished_at")
        require(all(type(value) in (int, float) and math.isfinite(value) for value in (started, finished))
                and 0 < started < finished, f"Job {job_id} has invalid process timestamps")
        require(sum(event.get("event") == "COMPLETED" and event.get("job_id") == job_id for event in events) == 1,
                f"Job {job_id} lacks one accepted completion event")
        counts[expected_provider] += 1
        all_intervals.append((expected_provider,started,finished))
        if kind in {"CHAPTER_DRAFT", "SYNTHESIS"}:
            artifact = by_job.get(job_id)
            require(isinstance(artifact, dict), f"Job {job_id} lacks a stored artifact")
            text = output.get("artifact_text")
            require(isinstance(text, str) and bool(text.strip()), f"Job {job_id} artifact is empty")
            require(artifact.get("artifact_text") == text, f"Job {job_id} artifact text differs from accepted output")
            require(artifact.get("sha256") == artifact_digest(text) == job.get("artifact_sha256"), f"Job {job_id} artifact hash mismatch")
            chapter_id = job["chapter_id"] if kind == "CHAPTER_DRAFT" else "synthesis"
            require(artifact.get("artifact_uri") == f"db://{workflow['workflow_id']}/{chapter_id}", f"Job {job_id} artifact URI mismatch")
            require(artifact.get("workflow_id") == workflow["workflow_id"] and artifact.get("chapter_id") == chapter_id,
                    f"Job {job_id} artifact ownership mismatch")
        if kind == "CHAPTER_DRAFT":
            require(output.get("chapter_id") == job["chapter_id"], f"Job {job_id} submitted a sibling chapter")
            require(set(job["payload"]["requirements"]) <= set(output.get("requirements_traced", [])), f"Job {job_id} is missing requirement tracing")
            tasks = output.get("wbs_tasks_defined")
            JobStore._chapter({**job, 'workflow_id': workflow['workflow_id']}, output)
            intervals.append((started, finished))
            accounts.append(receipt.get("worker_account"))
    require(len(set(sessions)) == 8, "Native sessions were reused across independent jobs")
    require(all(isinstance(account, str) and account for account in accounts) and len(set(accounts)) == 6,
            "Chapter receipts do not identify six distinct OS worker accounts")
    expected_hashes = {artifact["chapter_id"]: artifact["sha256"] for artifact in chapter_artifacts}
    synthesis_hashes = syntheses[0]["output"].get("chapter_hashes")
    if isinstance(synthesis_hashes, list):
        require(all(isinstance(item, dict) for item in synthesis_hashes), "Malformed synthesis chapter hashes")
        converted = {item.get("chapter_id"): item.get("sha256") for item in synthesis_hashes}
        require(len(converted) == len(synthesis_hashes), "Duplicate synthesis chapter hashes")
        synthesis_hashes = converted
    require(synthesis_hashes == expected_hashes, "Synthesis did not bind exactly all accepted chapter hashes")
    output_hashes = {job['chapter_id']: output_digest(job['output']) for job in chapters}
    accepted_wbs = {job['chapter_id']: job['output']['wbs_tasks_defined'] for job in chapters}
    coverage_rows = []
    for requirement in root['payload']['requirements']:
        owners = [job['chapter_id'] for job in sorted(chapters, key=lambda job: job['payload']['chapter_index'])
                  if requirement in job['payload']['requirements']]
        traced = [job['chapter_id'] for job in sorted(chapters, key=lambda job: job['payload']['chapter_index'])
                  if requirement in job['output']['requirements_traced']]
        coverage_rows.append({'requirement': requirement, 'owning_chapters': owners,
            'traced_by_chapters': traced, 'overlap': len(owners) > 1,
            'gap': not owners or any(owner not in traced for owner in owners)})
    coverage = {'schema': 'cochem-document-coverage/4.2.7', 'requirements': coverage_rows,
        'gaps': [row['requirement'] for row in coverage_rows if row['gap']],
        'overlaps': [row['requirement'] for row in coverage_rows if row['overlap']],
        'chapter_hashes': expected_hashes, 'chapter_output_hashes': output_hashes,
        'manifest_output_sha256': manifests[0]['output_sha256'],
        'complete': all(not row['gap'] for row in coverage_rows)}
    require(coverage['complete'], 'Accepted chapter coverage has gaps')
    synthesis = syntheses[0]
    for key, expected in {'chapter_output_hashes': output_hashes, 'wbs_tasks_by_chapter': accepted_wbs,
                           'coverage_report_sha256': output_digest(coverage)}.items():
        require(synthesis['payload'].get(key) == expected and synthesis['output'].get(key) == expected,
                'Synthesis did not preserve exact accepted ' + key + ' commitments')
    require(synthesis['payload'].get('coverage_report') == coverage, 'Synthesis coverage differs from accepted outputs')
    gathered = synthesis['payload'].get('chapters')
    require(isinstance(gathered, list) and len(gathered) == len(chapters), 'Missing accepted structured chapters in synthesis')
    for accepted, supplied in zip(sorted(chapters, key=lambda job: job['payload']['chapter_index']), gathered):
        require(supplied.get('chapter_id') == accepted['chapter_id']
                and supplied.get('accepted_output') == accepted['output']
                and supplied.get('output_sha256') == accepted['output_sha256']
                and supplied.get('sha256') == accepted['artifact_sha256'],
                'Synthesis input lost accepted structured chapter commitments')
    releases = [event for event in events if event.get("event") == "SYNTHESIS_RELEASED"]
    require(len(releases) == 1, "Expected exactly one synthesis barrier release")
    require(releases[0].get("details", {}).get("chapter_hashes") == expected_hashes, "Barrier release hash set differs from accepted artifacts")
    require(releases[0]['details'].get('chapter_output_hashes') == output_hashes
            and releases[0]['details'].get('coverage_report') == coverage
            and releases[0]['details'].get('coverage_report_sha256') == output_digest(coverage),
            'Barrier release does not bind complete accepted outputs and coverage')
    require(syntheses[0]["receipt"]["started_at"] >= max(end for _, end in intervals), "Synthesis started before every chapter finished")
    # End events sort first at an equal timestamp: touching intervals do not overlap.
    sweep = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals])
    running = peak = 0
    for _, delta in sweep:
        running += delta
        peak = max(peak, running)
    require(2 <= peak <= observed, f"Accepted chapter overlap was {peak}; expected at least two and at most configured hardware limit {observed}")
    concurrency=validate_execution_overlap(all_intervals,observed)
    return {"verified": True, "workflow_id": workflow["workflow_id"], "chapter_artifacts": 6,
            "synthesis_artifacts": 1, "validated_process_receipts": 8, "accepted_chapter_peak": peak,
            "provider_counts": dict(counts), "jobs_without_reported_model_metadata": unreported,
            "routing_decisions": routing_decisions, "admission": admission, "concurrency":concurrency,
            "jobs_without_reported_effort_metadata": unreported_effort,
            "stdout_content_independently_recomputed": False,
            "verification_scope": "Controller process receipts, canonical output/artifact hashes, identity bindings, event ledger and accepted execution overlap"}


def validate_execution_overlap(intervals, capacity):
    """Bound actual receipt intervals by configured seats and Claude's own cap."""
    require(type(capacity) is int and 1<=capacity<=256, 'Invalid configured admission ceiling')
    def peak(values):
        running=maximum=0
        for _,delta in sorted([(start,1) for _,start,end in values]+[(end,-1) for _,start,end in values]):
            running+=delta;maximum=max(maximum,running)
        return maximum
    maximum=peak(intervals)
    require(maximum<=capacity, 'Native execution overlap exceeded the captured configured hardware ceiling')
    claude=peak([item for item in intervals if item[0]=='claude'])
    require(claude<=20, 'Claude CLI exceeded its twenty-concurrent-agent ceiling')
    return {'native_peak':maximum,'claude_peak':claude,'configured_hardware_ceiling':capacity,
            'claude_provider_ceiling':20,'codex_provider_ceiling':None,'gemini_provider_ceiling':None}


def capture_admission(config, health):
    import hashlib
    evidence={'configured_max_execution_slots':config.get('max_execution_slots',4),
        'configured_worker_count':len(config.get('workers',[])),
        'controller_hardware_max_agents':health.get('hardware',{}).get('max_agents'),
        'configuration_sha256':hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'controller_instance_id':health.get('instance_id')}
    # Raw configuration may contain protected command arguments; retain only
    # its commitment and the independently returned controller capacity.
    return evidence


def write_report(path: Path, report: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".pipeline-acceptance-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(report, handle, indent=2, ensure_ascii=False)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Pipeline JSON configuration; capture model routing, worker capacity and an opaque configuration hash")
    parser.add_argument("--token-file", help="Override the local client token file path")
    parser.add_argument("--port", type=int, help="Override the localhost controller port")
    parser.add_argument("--report", required=True, type=Path, help="New JSON report path; an existing file will not be overwritten")
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--objective", default="Produce a complete six-chapter SRS and WBS for a local study-note application covering import, search, spaced review, privacy, backup and acceptance validation.")
    parser.add_argument("--run-live", action="store_true", help="Explicitly submit one workflow and consume CLI subscription quota")
    args = parser.parse_args(argv)
    if not args.run_live:
        parser.error("--run-live is required before submitting a workflow")
    if not math.isfinite(args.timeout) or args.timeout <= 0 or not math.isfinite(args.poll_seconds) or not 0 < args.poll_seconds <= 60:
        parser.error("timeout must be positive; poll-seconds must be in (0,60]")
    report = {"verification_kind": "live-authenticated-controller", "status": "STARTING",
              "started_at": time.time(), "workflow_id": None, "workflow": None, "timeline": []}
    client = None
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as destination:
            json.dump(report, destination)
    except OSError as exc:
        print(f"Cannot create new report: {exc}", file=sys.stderr)
        return 1
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
        providers = config["providers"]
        require(all(isinstance(providers.get(name, {}).get("model"), str) for name in ("codex", "claude", "gemini")), "Configure all three provider models")
        routing_policy = config.get("routing", {})
        configured_tiers(routing_policy)
        client = ControlClient(args.port or config.get("port", 47824), args.token_file or config["token_file"])
        report["health_before"] = client.call("/health")
        report["admission"] = capture_admission(config,report["health_before"])
        identifier = "acceptance-" + uuid.uuid4().hex
        report["workflow_id"] = identifier  # Retain ID even if a submit response is lost.
        workflow = client.call("/submit", {"workflow_id": identifier, "objective": args.objective,
            "requirements": ["REQ-IMPORT", "REQ-SEARCH", "REQ-REVIEW", "REQ-PRIVACY", "REQ-BACKUP", "REQ-VALIDATE"],
            "chapter_count": 6})
        deadline = time.monotonic() + args.timeout
        previous = None
        while True:
            report["workflow"] = workflow
            signature = [(job["job_id"], job["status"]) for job in workflow["jobs"]]
            if signature != previous:
                report["timeline"].append({"observed_at": time.time(), "jobs": signature})
                report["status"] = "RUNNING"
                write_report(args.report, report)
                previous = signature
            if workflow["status"] == "FAILED":
                raise RuntimeError("Workflow failed; inspect job errors in the saved report")
            if workflow["status"] == "COMPLETED":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Live workflow exceeded the verification timeout")
            time.sleep(min(args.poll_seconds, max(0, deadline - time.monotonic())))
            workflow = client.call("/workflow/" + identifier)
        report["health_after"] = client.call("/health")
        require(capture_admission(config,report["health_after"])==report["admission"],
                'Controller identity or hardware capacity changed during acceptance')
        report["validation"] = validate_workflow(workflow, providers, routing_policy,admission=report["admission"])
        confirmation = client.call("/workflow/" + identifier)
        require(confirmation["artifacts"] == workflow["artifacts"], "Completed artifacts changed between reads")
        report["status"] = "PASSED"
        report["finished_at"] = time.time()
        write_report(args.report, report)
        print(json.dumps({"status": "PASSED", "workflow_id": identifier, "report": str(args.report),
                          "accepted_chapter_peak": report["validation"]["accepted_chapter_peak"]}))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        if client is not None and report["workflow_id"] and (report["workflow"] or {}).get("status") not in ("FAILED", "COMPLETED"):
            try:
                report["workflow"] = client.call("/cancel", {"workflow_id": report["workflow_id"]})
                report["unfinished_work_cancelled"] = True
            except Exception as cancel_error:
                report["cancellation_error"] = f"{type(cancel_error).__name__}: {cancel_error}"
        write_report(args.report, report)
        print(f"Live pipeline acceptance failed; details saved to {args.report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
