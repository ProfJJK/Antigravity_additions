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
from cochem_pipeline.store import artifact_digest, output_digest


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_workflow(workflow: dict, providers: dict) -> dict:
    """Check a controller snapshot; this pure function does not execute models.

    Provider identity is taken from trusted process receipts, never generated
    text. Output and artifact hashes are recomputed from the returned content.
    Raw stdout remains private to the service, so its digest is format-checked
    and retained, rather than falsely claimed to be independently recomputed.
    """
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
    for job in chapters:
        assigned = manifest_chapters[job["payload"]["chapter_index"]]
        require(assigned.get("chapter_id") == job["chapter_id"]
                and assigned.get("requirements") == job["payload"].get("requirements"), "Manifest assignment changed before drafting")
        covered.update(assigned["requirements"])
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
    intervals, accounts, sessions, unreported, counts = [], [], [], [], Counter()
    for job in [*manifests, *chapters, *syntheses]:
        job_id, kind = job["job_id"], job["kind"]
        require(job.get("status") == "COMPLETED", f"Job {job_id} is not completed")
        output, receipt = job.get("output"), job.get("receipt")
        require(isinstance(output, dict) and isinstance(receipt, dict), f"Job {job_id} lacks an output/receipt")
        require("execution_kind" not in receipt, f"Job {job_id} has a test/emulator receipt, not a live receipt")
        require(receipt.get("subscription_verified") is True, f"Job {job_id} lacks native subscription verification")
        expected_provider = "gemini" if kind == "SYNTHESIS" else "codex"
        if kind == "CHAPTER_DRAFT":
            expected_provider = "codex" if job["payload"]["chapter_index"] % 2 == 0 else "claude"
        require(receipt.get("provider") == expected_provider, f"Job {job_id} used an unexpected provider")
        model = providers.get(expected_provider, {}).get("model")
        require(isinstance(model, str) and bool(model), f"Missing configured model for {expected_provider}")
        require(receipt.get("requested_model") == model, f"Job {job_id} requested a different configured model")
        reported = receipt.get("reported_model")
        require(reported in (None, model), f"Job {job_id} reported a different model")
        if expected_provider == "gemini":
            require(reported == model, "Gemini synthesis must report the configured model")
        if reported is None:
            unreported.append(job_id)
        require(type(receipt.get("pid")) is int and receipt["pid"] > 0, f"Job {job_id} lacks a physical PID")
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
            require(isinstance(tasks, list) and tasks and all(isinstance(task, dict) and task for task in tasks), f"Job {job_id} lacks structured WBS tasks")
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
    releases = [event for event in events if event.get("event") == "SYNTHESIS_RELEASED"]
    require(len(releases) == 1, "Expected exactly one synthesis barrier release")
    require(releases[0].get("details", {}).get("chapter_hashes") == expected_hashes, "Barrier release hash set differs from accepted artifacts")
    require(syntheses[0]["receipt"]["started_at"] >= max(end for _, end in intervals), "Synthesis started before every chapter finished")
    # End events sort first at an equal timestamp: touching intervals do not overlap.
    sweep = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals])
    running = peak = 0
    for _, delta in sweep:
        running += delta
        peak = max(peak, running)
    require(2 <= peak <= 4, f"Accepted chapter overlap was {peak}; expected at least two and at most four")
    return {"verified": True, "workflow_id": workflow["workflow_id"], "chapter_artifacts": 6,
            "synthesis_artifacts": 1, "validated_process_receipts": 8, "accepted_chapter_peak": peak,
            "provider_counts": dict(counts), "jobs_without_reported_model_metadata": unreported,
            "stdout_content_independently_recomputed": False,
            "verification_scope": "Controller process receipts, canonical output/artifact hashes, identity bindings, event ledger and accepted execution overlap"}


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
    parser.add_argument("--config", required=True, help="Pipeline JSON configuration; only model names, port and token path are read")
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
        client = ControlClient(args.port or config.get("port", 47824), args.token_file or config["token_file"])
        report["health_before"] = client.call("/health")
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
        report["validation"] = validate_workflow(workflow, providers)
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
