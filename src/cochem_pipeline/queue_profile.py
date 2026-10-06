"""Disposable queue workload using the controller's actual ownership topology.

Four spawned Python processes stand in for CLI latency and output size. Only the
controller opens SQLite. These are explicitly synthetic storage diagnostics;
actual Windows acceptance uses QueueLaunchObserver in the real daemon.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time

from .queue_launch import distribution, storage_identity
from .routing import load_routing_policy
from .store import JobStore, output_digest


def _synthetic_worker(channel):
    try:
        while True:
            request = channel.recv()
            if request is None:
                return
            node, delay, artifact_size = request
            time.sleep(delay)
            if node["kind"] == "MANIFEST_GENERATOR":
                output = {"chapters": [{"chapter_id": f"ch-{index}", "title": f"Chapter {index}",
                    "requirements": node["payload"]["requirements"]}
                    for index in range(node["payload"]["chapter_count"])]}
            elif node["kind"] == "CHAPTER_DRAFT":
                output = {"chapter_id": node["chapter_id"],
                    "requirements_traced": node["payload"]["requirements"],
                    "wbs_tasks_defined": [{"task_id": "queue-storage-fixture", "description": "Synthetic queue workload"}],
                    "artifact_uri": f"db://{node['workflow_id']}/{node['chapter_id']}",
                    "artifact_text": "Synthetic storage measurement.\n" + "x" * artifact_size}
            else:
                output = {"chapter_hashes": node["payload"]["chapter_hashes"],
                          "artifact_text": "Synthetic synthesis.\n" + "x" * artifact_size}
            route = node["route"]
            receipt = {"provider": route["provider"], "requested_model": route["model"],
                "requested_effort": route.get("reasoning_effort"), "selected_route": route,
                "route_reservation_id": route["reservation_id"], "attempt_id": node["attempt_id"],
                "fencing_token": node["fencing_token"], "worker_slot": node["worker_slot"],
                "job_id": node["job_id"], "workflow_id": node["workflow_id"],
                "pid": os.getpid(), "exit_code": 0, "session_id": "synthetic-storage-profile",
                "execution_kind": "python-synthetic-queue-workload-not-inference",
                "output_sha256": output_digest(output)}
            channel.send((output, receipt))
    finally:
        channel.close()


def benchmark_controller_queue(output, *, database_directory, workflows=12,
                               routing=None, lease_seconds=1800, heartbeat_seconds=5,
                               worker_delays=(.01, .05, 5.2), artifact_sizes=(1024, 8192, 65536),
                               deadline_seconds=600):
    """A new sibling DB is the only DB opened; existing production files stay intact."""
    if type(workflows) is not int or not 4 <= workflows <= 200:
        raise ValueError("Profile workflows must be between four and 200")
    if (type(heartbeat_seconds) is not int or type(lease_seconds) is not int
            or not 1 <= heartbeat_seconds <= 5 or not 3 * heartbeat_seconds <= lease_seconds <= 3600):
        raise ValueError("Use configured heartbeat/lease durations with at least three heartbeats per lease")
    if (not worker_delays or any(type(value) not in (int, float) or not 0 <= value <= 30 for value in worker_delays)
            or not artifact_sizes or any(type(value) is not int or not 1 <= value <= 1048576 for value in artifact_sizes)):
        raise ValueError("Provide bounded synthetic delays and artifact sizes")
    if type(deadline_seconds) not in (int, float) or not 1 <= deadline_seconds <= 1800:
        raise ValueError("Profile deadline must be between one and 1800 seconds")
    directory = Path(database_directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Configured database directory must already exist")
    if os.name == "nt":
        from .windows import require_system, validate_private_directory
        require_system()
        validate_private_directory(directory)
        validate_private_directory(Path(output).parent)
    output = Path(output)
    output.mkdir(exist_ok=False)
    storage = storage_identity(directory)
    policy = load_routing_policy(routing)
    fd, filename = tempfile.mkstemp(prefix="queue-launch-profile-", suffix=".db", dir=directory)
    os.close(fd)
    database = Path(filename)
    store = JobStore(database, routing_policy=policy)
    samples, claims, completions, stale_rejections = [], set(), set(), []
    lock = threading.Lock()
    children, channels, tasks = [], [], {}
    context = multiprocessing.get_context("spawn")
    started = time.perf_counter()
    caught = None
    report = None

    def measure(operation, function, *args, **kwargs):
        began = time.perf_counter()
        value = function(*args, **kwargs)
        elapsed = (time.perf_counter() - began) * 1000
        with lock:
            if len(samples) >= 100000:
                raise RuntimeError("Bounded queue profile sample budget exhausted")
            samples.append({"operation": operation, "elapsed_ms": elapsed,
                            "acquired": operation == "claim" and value is not None})
        return value

    def execute(index, node, number):
        channel = channels[index]
        delay = worker_delays[number % len(worker_delays)]
        size = artifact_sizes[number % len(artifact_sizes)]
        channel.send((node, delay, size))
        while not channel.poll(heartbeat_seconds):
            if time.perf_counter() - started > deadline_seconds:
                raise TimeoutError("Queue profile exceeded its bounded deadline")
            if not measure("heartbeat", store.heartbeat, node["job_id"], node["attempt_id"],
                           node["fencing_token"], lease_seconds):
                raise RuntimeError("Synthetic worker lost its genuine fenced lease")
        result, receipt = channel.recv()
        # Actual SQLite reads and context writes interleave with claims/completions.
        measure("get", store.get, node["job_id"])
        measure("context", store.set_context, node["job_id"],
                "<context>" + "x" * 4096 + "</context>", hashlib.sha256(f"profile-{number}".encode()).hexdigest())
        # Deliberately wrong authority must not update or complete even an active job.
        stale_heartbeat = measure("stale_heartbeat", store.heartbeat, node["job_id"],
                                  node["attempt_id"], node["fencing_token"] + 1, lease_seconds)
        try:
            store.complete(node["job_id"], node["attempt_id"], node["fencing_token"] + 1, result, receipt)
        except ValueError as exc:
            if "Stale or unowned" not in str(exc):
                raise
            stale_completion = True
        else:
            stale_completion = False
        measure("complete", store.complete, node["job_id"], node["attempt_id"], node["fencing_token"], result, receipt)
        with lock:
            completions.add((node["job_id"], node["attempt_id"], node["fencing_token"]))
            stale_rejections.append(not stale_heartbeat and stale_completion)

    try:
        # This audit trigger records violations; it does not enforce the ceiling
        # or conceal a broken admission implementation by rejecting its writes.
        with store._write() as connection:
            connection.executescript("""
                CREATE TABLE queue_profile_audit(active INTEGER NOT NULL);
                CREATE TRIGGER queue_profile_active AFTER UPDATE OF status ON pipeline_jobs
                BEGIN INSERT INTO queue_profile_audit SELECT count(*) FROM pipeline_jobs
                    WHERE status='IN_PROGRESS' AND kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST'); END;
            """)
        workflow_ids = []
        for number in range(workflows):
            objectives = ("Write a brief specification", "Plan a concurrent transaction migration",
                          "Specify security production rollback optimization numerical invariants")
            requirements = [f"REQ-{item}" for item in range((1, 4, 12)[number % 3])]
            workflow_ids.append(store.submit(objectives[number % 3], requirements, 3)["workflow_id"])
        with store._connection() as connection:
            settings = {key: connection.execute("PRAGMA " + key).fetchone()[0]
                        for key in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys")}
        for _ in range(4):
            parent, child = context.Pipe()
            process = context.Process(target=_synthetic_worker, args=(child,))
            process.start()
            child.close()
            channels.append(parent)
            children.append(process)
        sequence, next_read = 0, 0.
        with ThreadPoolExecutor(max_workers=4) as pool:
            while len(completions) < workflows * 5:
                if time.perf_counter() - started > deadline_seconds:
                    raise TimeoutError("Queue profile did not drain within its bounded deadline")
                for index, task in list(tasks.items()):
                    if task.done():
                        task.result()
                        del tasks[index]
                for index in range(4):
                    if index in tasks:
                        continue
                    node = measure("claim", store.claim, "controller-storage-profile", lease_seconds,
                                   max_workers=4, worker_slot=f"slot{index + 1}", requires_cleanup=False)
                    if node is None:
                        continue
                    key = (node["job_id"], node["attempt_id"], node["fencing_token"])
                    if key in claims:
                        raise RuntimeError("Queue issued duplicate attempt authority")
                    claims.add(key)
                    tasks[index] = pool.submit(execute, index, node, sequence)
                    sequence += 1
                if time.perf_counter() >= next_read:
                    measure("event_batch", store.event_batch, 0, 32)
                    measure("list_workflows", store.list_workflows, 20)
                    next_read = time.perf_counter() + .1
                time.sleep(.01)
        with store._connection() as connection:
            peak = connection.execute("SELECT max(active) FROM queue_profile_audit").fetchone()[0]
            remaining = connection.execute("SELECT count(*) FROM pipeline_jobs WHERE status <> 'COMPLETED'").fetchone()[0]
            outputs = connection.execute("SELECT count(*) FROM pipeline_outputs").fetchone()[0]
        acquisition = [row["elapsed_ms"] for row in samples if row["acquired"]]
        correctness = {"four_seat_ceiling_preserved": peak <= 4,
            "four_seat_load_exercised": peak == 4, "no_duplicate_authority": len(claims) == outputs,
            "all_claims_completed": claims == completions,
            "all_workflows_completed": remaining == 0,
            "stale_heartbeat_and_completion_rejected": bool(stale_rejections) and all(stale_rejections)}
        report = {"schema": 1, "kind": "synthetic-controller-owned-queue-profile", "platform": os.name,
            "topology": "one controller-owned pooled JobStore; four spawned Python job processes without DB access",
            "database": str(database), "production_database": str(directory / "job_board.db"),
            "database_scope": "disposable sibling file in configured database directory; active production DB never opened",
            "storage": storage, "sqlite_settings": settings, "routing_policy_sha256": policy.digest,
            "lease_seconds": lease_seconds, "heartbeat_seconds": heartbeat_seconds,
            "worker_processes": 4, "worker_pids": [child.pid for child in children],
            "elapsed_seconds": time.perf_counter() - started,
            "workload": {"workflows": workflows, "chapters_per_workflow": 3,
                "completed_jobs": len(completions), "synthetic_worker_delay_seconds": list(worker_delays),
                "artifact_bytes": list(artifact_sizes), "context_bytes": 4115,
                "operations": sorted({row["operation"] for row in samples}),
                "live_model_inference": False},
            "correctness": correctness, "maximum_active_jobs": peak,
            "claim_acquisition": distribution(acquisition),
            "operations": {operation: distribution([row["elapsed_ms"] for row in samples if row["operation"] == operation])
                           for operation in sorted({row["operation"] for row in samples})},
            "measurement_scope": "entire claim call including lock wait, transaction and commit; schema/seed/process startup excluded",
            "samples": samples, "optimization_target_ms": 5,
            "optimization_target_met": max(acquisition) < 5, "passed": all(correctness.values()),
            "native_windows_acceptance": False, "acceptance_status": "pending_actual_windows_launch",
            "limitations": ["Synthetic Python workers do not exercise native CLI inference or worker account boundaries.",
                "Sibling database uses the same directory/storage, but not the live database's size, WAL or in-place contention.",
                "Job mix is declared; it does not replace a representative operator workload on the deployed daemon."]}
    except BaseException as exc:
        caught = exc
    finally:
        for channel in channels:
            try:
                channel.send(None)
            except (EOFError, OSError):
                pass
        for child in children:
            child.join(timeout=2)
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        for channel in channels:
            channel.close()
        store.close()
        # Only the exact random paths allocated by this invocation are deleted.
        for suffix in ("", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
    if caught is not None:
        raise caught
    report["disposable_database_removed"] = not database.exists()
    (output / "queue-profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def benchmark_configured_queue(config_path, output, *, workflows=12):
    path = Path(config_path)
    with path.open('rb') as stream:
        raw_bytes = stream.read(1048577)
    if len(raw_bytes) > 1048576:
        raise ValueError('Queue profile configuration exceeds one MiB')
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    directory = Path(raw["private_root"]).expanduser()
    if not directory.is_absolute():
        raise ValueError("Use the deployed configuration's absolute private_root")
    if len(raw.get("slot_roots", {})) < 4:
        raise ValueError("The requested launch topology needs at least four configured worker slots")
    report = benchmark_controller_queue(output, database_directory=directory, workflows=workflows,
        routing=raw.get("routing"), lease_seconds=raw.get("lease_seconds", 1800),
        heartbeat_seconds=raw.get("heartbeat_seconds", 5))
    report["config_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    report["config_scope"] = "queue settings only; native provider/credential configuration is exercised by the real daemon observer"
    (Path(output) / "queue-profile.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
