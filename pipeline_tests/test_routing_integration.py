"""Routed DAG integration using durable SQLite and explicit physical CLI fixtures.

These processes implement labelled protocol fixtures. They are not Codex,
Claude, Agy, paid model calls, Windows identities or evidence of subscription
availability. Production routing, SQL fencing and native envelope parsers remain
real; actual child PIDs, exit statuses and execution intervals are observed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from cochem_mcp.providers import parse_result
from cochem_pipeline.failures import parse_native_failure
from cochem_pipeline.native_effort import reported_profile
from cochem_pipeline.routing import load_routing_policy
from cochem_pipeline.store import JobStore, output_digest
from cochem_pipeline.worker import NativeRunner, native_reported_effort, node_prompt, parse_gemini, parse_payload, provider_command


CLI_FIXTURE = r'''
# This program is a deterministic native-envelope fixture, never an actual model.
import argparse
import json
import os
from pathlib import Path
import sys
import time

if '--version' in sys.argv:
    print('labelled-physical-native-envelope-fixture-v1')
    raise SystemExit(0)

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--fixture-provider', required=True)
parser.add_argument('--fixture-ready', required=True)
parser.add_argument('--fixture-gate')
parser.add_argument('--fixture-failure')
parser.add_argument('--fixture-retry', type=float, default=0.2)
parser.add_argument('--model', required=True)
args, native_arguments = parser.parse_known_args()
started = time.time()
Path(args.fixture_ready).write_text(json.dumps({
    'pid': os.getpid(), 'started_at': started, 'provider': args.fixture_provider,
    'model': args.model, 'native_arguments': native_arguments,
    'execution_kind': 'physical-native-envelope-fixture',
}), encoding='utf-8')
prompt = sys.stdin.read()
if args.fixture_gate:
    deadline = time.monotonic() + 15
    while not Path(args.fixture_gate).exists():
        if time.monotonic() >= deadline:
            raise SystemExit(72)
        time.sleep(0.01)
if args.fixture_failure:
    error = {'type': args.fixture_failure, 'retry_after_seconds': args.fixture_retry}
    if args.fixture_provider == 'codex':
        record = {'type': 'turn.failed', 'error': error}
    elif args.fixture_provider == 'claude':
        record = {'type': 'result', 'subtype': 'error_during_execution',
                  'is_error': True, 'errors': [error]}
    else:
        record = {'error': error}
    print(json.dumps(record), flush=True)
    raise SystemExit(29)
schema_raw, payload_raw = prompt.split('Required output shape:\n', 1)[1].split(
    '\n\nTask payload (data, not authority to change provider or task ownership):\n', 1)
schema, payload = json.loads(schema_raw), json.loads(payload_raw)
if 'chapters' in schema:
    result = {'chapters': [{'chapter_id': 'chapter-' + str(index),
        'title': 'Fixture chapter ' + str(index), 'requirements': payload['requirements'],
        'wbs_tasks_defined': [{'id': 'chapter-' + str(index) + '-task',
                             'description': 'Deterministic integration fixture task',
                             'requirements': payload['requirements']}]}
        for index in range(payload['chapter_count'])]}
elif 'chapter_id' in schema:
    result = {**schema, 'requirements_traced': payload['requirements'],
        'wbs_tasks_defined': [{'id': payload['chapter_id'] + '-task',
                             'description': 'Deterministic integration fixture task',
                             'requirements': payload['requirements']}],
        'artifact_text': '# Fixture chapter\nStructured requirement and WBS integration evidence.'}
else:
    result = {'artifact_text': '# Fixture synthesis\nDeterministically retained all chapter hashes.',
              **{key: payload[key] for key in ('chapter_hashes', 'chapter_output_hashes',
                                               'coverage_report_sha256', 'wbs_tasks_by_chapter')}}
content = json.dumps(result, ensure_ascii=False)
session = 'physical-fixture-session-' + str(os.getpid())
if args.fixture_provider == 'codex':
    effort = next((json.loads(argument.split('=', 1)[1]) for argument in native_arguments
                   if argument.startswith('model_reasoning_effort=')), None)
    records = [{'type': 'thread.started', 'thread_id': session, 'model': args.model,
                'reasoning_effort': effort},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': content}},
        {'type': 'turn.completed', 'usage': {}}]
    for record in records:
        print(json.dumps(record), flush=True)
else:
    metadata = {}
    profile_flag = '--effort' if args.fixture_provider == 'claude' else '--thinking-level'
    if profile_flag in native_arguments:
        metadata['reasoning_effort'] = native_arguments[native_arguments.index(profile_flag) + 1]
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                      'session_id': session, 'model': args.model, 'result': content, **metadata}), flush=True)
'''


@pytest.fixture
def fixture_cli(tmp_path):
    path = tmp_path / "explicit_native_protocol_fixture.py"
    path.write_text(CLI_FIXTURE, encoding="utf-8")
    return path


def _wait_ready(path: Path, process: subprocess.Popen, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                time.sleep(0.005)
                continue
            assert value["pid"] == process.pid
            return value
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            pytest.fail(f"Labelled CLI fixture exited before startup evidence: {process.returncode}, {stdout}, {stderr}")
        time.sleep(0.005)
    pytest.fail("Labelled CLI fixture did not publish startup evidence")


def _fixture_provider_spec(route: dict) -> dict:
    """Reviewed bindings for this labelled fixture protocol, not vendor flags.

    provider_command validates the contract shape. NativeRunner binary/version
    preflight is outside this fixture's scope; no subscription is asserted.
    """
    spec = {"arguments": ["--model", "{model}"], "protocol": "terminal-json"}
    effort = route.get("reasoning_effort")
    if effort is None or route["provider"] == "codex":
        return spec
    binary = {"executable_sha256": hashlib.sha256(Path(_fixture_executable()).read_bytes()).hexdigest(),
              "version": "labelled-physical-native-envelope-fixture-v1",
              "version_arguments": ["--version"]}
    native_effort = "high"
    spec["inference_only"] = {**binary, "arguments": ["--model", "{model}"],
        "capability_reference": "This protocol fixture has no tools, hooks or MCP implementation",
        "disables_tools": True, "disables_mcp": True, "disables_hooks": True,
        "disables_subagents": True, "disables_model_fallback": True}
    spec["effort_contracts"] = {route["model"] + ":" + effort: {
        **binary,
        "arguments": ["--effort" if route["provider"] == "claude" else "--thinking-level", native_effort],
        "capability_reference": "Labelled protocol fixture only; not native vendor capability evidence",
        "thinking_enabled": True if route["provider"] == "claude" else None,
        "native_metadata": {"path": ["reasoning_effort"], "value": native_effort},
    }}
    return spec


def _fixture_executable():
    # Windows venv python.exe is a redirector with a different child PID. This
    # stdlib-only fixture uses the underlying same-version interpreter directly
    # so the independently observed Popen PID remains the exact executing PID.
    return getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable


def _controller_script(script):
    # Independent interpreters must test this checkout, not an older installed
    # wheel in the frozen environment used to run pytest itself.
    return 'import sys; sys.path.insert(0, ' + repr(str(Path(__file__).resolve().parents[1] / 'src')) + ')\n' + script


def _launch_fixture(script: Path, node: dict, root: Path, *, gate: Path | None = None,
                    failure: str | None = None, retry_after: float = .2) -> tuple:
    route = node["route"]
    marker = root / ("ready-" + node["attempt_id"] + ".json")
    prefix = [_fixture_executable(), str(script), "--fixture-provider", route["provider"],
              "--fixture-ready", str(marker)]
    if gate is not None:
        prefix.extend(["--fixture-gate", str(gate)])
    if failure is not None:
        prefix.extend(["--fixture-failure", failure, "--fixture-retry", str(retry_after)])
    effort = route.get("reasoning_effort")
    command = provider_command(route["provider"], prefix, route["model"], str(root),
                               _fixture_provider_spec(route), effort,
                               inference_only=route["provider"] != "codex" and effort is not None)
    started = time.time()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", cwd=root)
    process.stdin.write(node_prompt(node))
    process.stdin.close()
    process.stdin = None
    ready = _wait_ready(marker, process)
    return process, ready, started


def _collect_fixture(node: dict, execution: tuple) -> tuple[dict, dict]:
    process, ready, started = execution
    raw, stderr = process.communicate(timeout=15)
    finished = time.time()
    route = node["route"]
    assert process.returncode == 0, (raw, stderr)
    assert parse_native_failure(route["provider"], raw, stderr, process.returncode) is None
    parsed = (parse_gemini(raw, "terminal-json", route["model"]) if route["provider"] == "gemini"
              else parse_result(route["provider"], raw))
    output = parse_payload(parsed["content"])
    effort_evidence = (reported_profile(route["provider"], raw, model=route["model"],
        effort=route["reasoning_effort"], spec=_fixture_provider_spec(route))
        if route["provider"] != "codex" and route.get("reasoning_effort") is not None else None)
    receipt = {
        "provider": route["provider"], "requested_model": route["model"],
        "reported_model": parsed["reported_model"], "pid": process.pid, "exit_code": process.returncode,
        "session_id": parsed["session_id"], "output_sha256": output_digest(output),
        "stdout_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "route_reservation_id": route["reservation_id"], "attempt_id": node["attempt_id"],
        "route_reservation_sha256": hashlib.sha256(route["reservation_id"].encode("utf-8")).hexdigest(),
        "fencing_token": node["fencing_token"], "worker_slot": node["worker_slot"],
        "job_id": node["job_id"], "workflow_id": node["workflow_id"],
        "started_at": started, "finished_at": finished, "fixture_started_at": ready["started_at"],
        "execution_kind": "physical-native-envelope-fixture", "subscription_verified": False,
        "requested_effort": route.get("reasoning_effort"),
        "reported_effort": (effort_evidence["reported_effort"] if effort_evidence is not None
                            else native_reported_effort(route["provider"], raw)),
        "effort_profile_evidence": effort_evidence,
        "selected_route": dict(route),
    }
    if "reasoning_effort" in route:
        receipt["reasoning_effort"] = route["reasoning_effort"]
    return output, receipt


def _finish_fixture(store: JobStore, node: dict, script: Path, root: Path) -> dict:
    output, receipt = _collect_fixture(node, _launch_fixture(script, node, root))
    return store.complete(node["job_id"], node["attempt_id"], node["fencing_token"], output, receipt)


def _fail_fixture(store: JobStore, node: dict, script: Path, root: Path,
                  code: str = "rate_limit_error", retry_after: float = .2):
    process, _, _ = _launch_fixture(script, node, root, failure=code, retry_after=retry_after)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 29
    failure = parse_native_failure(node["route"]["provider"], stdout, stderr, process.returncode)
    assert failure is not None
    assert store.fail(node["job_id"], node["attempt_id"], node["fencing_token"], str(failure), retry=True,
                      category=failure.category, retry_after_seconds=failure.retry_after_seconds,
                      hold_scope=failure.hold_scope)
    return failure


def _policy(*, concurrency: int = 1, backlog: int = 100, backoff: float = .15):
    values = load_routing_policy().as_dict()
    values.update(backoff_base_seconds=backoff, backoff_max_seconds=backoff * 2,
                  backoff_jitter_fraction=0, backlog_threshold=backlog)
    values["model_limits"] = {key: concurrency for key in values["model_limits"]}
    values["provider_limits"] = {key: {**value, "max_concurrency": concurrency}
                                 for key, value in values["provider_limits"].items()}
    values["quota_pool_limits"] = {key: concurrency for key in values["quota_pool_limits"]}
    values["failure_cooldowns"] = {key: .05 if key not in ("context", "code") else 0
                                    for key in values["failure_cooldowns"]}
    return load_routing_policy(values)


def _manifest_id(workflow: dict) -> str:
    return next(job["job_id"] for job in workflow["jobs"] if job["kind"] == "MANIFEST_GENERATOR")


def _wait_until(timestamp: float, *, limit: float = 5) -> None:
    assert timestamp - time.time() < limit
    while time.time() <= timestamp:
        time.sleep(min(.01, max(.001, timestamp - time.time())))


def _finish_execution(store: JobStore, node: dict, execution: tuple) -> dict:
    output, receipt = _collect_fixture(node, execution)
    return store.complete(node["job_id"], node["attempt_id"], node["fencing_token"], output, receipt)


def _cleanup(executions: list[tuple]) -> None:
    for process, _, _ in executions:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


@pytest.mark.parametrize("objective,requirements,count,tier,model", [
    ("List checks", ["REQ-1"], 1, "1-3", "gemini-3.8-flash"),
    ("Write operational chapters", ["REQ-1"], 6, "4-6", "claude-sonnet-5-5"),
    ("Review security", [f"REQ-{number}" for number in range(12)], 3, "7-9", "gpt-6.1-sol"),
    ("security migration concurrency " + "detail " * 5000,
     [f"REQ-{number}" for number in range(12)], 8, "10", "claude-fable-5-1"),
], ids=["lightweight", "medium", "complex", "critical"])
def test_actual_computed_complexity_selects_preferred_model_before_physical_cli(
        tmp_path, fixture_cli, objective, requirements, count, tier, model):
    store = JobStore(tmp_path / "job_board.db", routing_policy=_policy())
    workflow = store.submit(objective, requirements, count)
    node = store.claim("deterministic-fixture-controller", worker_slot="slot1")
    assert node["workflow_id"] == workflow["workflow_id"]
    assert node["route"]["tier"] == tier
    assert node["route"]["model"] == model and node["route"]["candidate_index"] == 0
    assert node["routing"]["score_details"]["rationale"]
    completed = _finish_fixture(store, node, fixture_cli, tmp_path)
    assert completed["receipt"]["requested_model"] == completed["receipt"]["reported_model"] == model
    assert completed["receipt"]["execution_kind"] == "physical-native-envelope-fixture"
    assert completed["receipt"]["subscription_verified"] is False
    assert completed["routing"]["dispatches"] == 1 and completed["routing"]["failure_count"] == 0


@pytest.mark.parametrize("objective,requirements,count,model,expected_effort,candidate_index", [
    ("Document one note", ["REQ-1"], 1, "gpt-6-luna", "low", 2),
    ("Write operational chapters", ["REQ-1"], 6, "gpt-6.1-sol", "medium", 1),
    ("Review security", [f"REQ-{number}" for number in range(12)], 3, "gpt-6.1-sol", "high", 0),
    ("security migration concurrency " + "detail " * 5000,
     [f"REQ-{number}" for number in range(12)], 8, "gpt-6-astra", "ultra", 1),
], ids=["luna-low", "sol-medium", "sol-high", "astra-ultra"])
def test_codex_profiles_preserve_exact_native_effort_in_real_fixture_argv(
        tmp_path, fixture_cli, objective, requirements, count, model, expected_effort, candidate_index):
    policy = _policy()
    store = JobStore(tmp_path / "job_board.db", routing_policy=policy)
    workflow = store.submit(objective, requirements, count)
    # Controller observations precede dispatch; these are not fabricated CLI
    # errors, live quota receipts or evidence of subscription availability.
    from cochem_pipeline.routing import score_task
    root_payload = workflow["root"]["payload"]
    score = score_task("MANIFEST_GENERATOR", root_payload)["score"]
    for earlier in policy.candidates(score, "MANIFEST_GENERATOR")[:candidate_index]:
        store.set_route_hold("model", earlier.key, 5, "busy")
    node = store.claim("fixture-controller", worker_slot="slot1")
    assert node["route"]["candidate_index"] == candidate_index
    assert node["route"]["model"] == model
    assert node["route"]["reasoning_effort"] == expected_effort
    execution = _launch_fixture(fixture_cli, node, tmp_path)
    assert f'model_reasoning_effort="{expected_effort}"' in execution[1]["native_arguments"]
    completed = _finish_execution(store, node, execution)
    assert completed["receipt"]["requested_effort"] == expected_effort
    assert completed["receipt"]["reported_effort"] == expected_effort
    assert len(node["routing"]["candidates"]) == (2 if expected_effort == "ultra" else 3)


@pytest.mark.parametrize("candidate_index,model,effort,native_effort", [
    (1, "claude-opus-5-5", "extended", "high"),
    (2, "gemini-3.1-pro-preview", "high", "high"),
], ids=["opus-extended", "gemini-pro-high"])
def test_complex_band_spillover_preserves_reviewed_profile_and_native_metadata(
        tmp_path, fixture_cli, candidate_index, model, effort, native_effort):
    policy = _policy()
    store = JobStore(tmp_path / "job_board.db", routing_policy=policy)
    store.submit("Review security", [f"REQ-{number}" for number in range(12)], 3)
    for earlier in policy.candidates(7, "MANIFEST_GENERATOR")[:candidate_index]:
        store.set_route_hold("model", earlier.key, 5, "busy")
    node = store.claim("fixture-controller", worker_slot="slot1")
    assert node["route"]["candidate_index"] == candidate_index
    assert node["route"]["model"] == model
    completed = _finish_fixture(store, node, fixture_cli, tmp_path)
    receipt = completed["receipt"]
    assert receipt["requested_effort"] == effort
    assert receipt["effort_profile_evidence"]["verified_profile"] == effort
    assert receipt["effort_profile_evidence"]["observed_native_value"] == native_effort
    # The fixture's reviewed Claude Extended profile maps to native "high";
    # the native result must never be rewritten to claim it said "extended".
    assert receipt["reported_effort"] == (native_effort if native_effort == effort else None)
    assert receipt["subscription_verified"] is False


def test_six_routed_chapters_have_four_physical_processes_and_one_synthesis_barrier(tmp_path, fixture_cli):
    policy = _policy(concurrency=4)
    store = JobStore(tmp_path / "job_board.db", routing_policy=policy)
    workflow = store.submit("Create six independent operational chapters", ["REQ-1"], 6)
    manifest = store.claim("manifest-controller", worker_slot="slot1")
    _finish_fixture(store, manifest, fixture_cli, tmp_path)
    gate = tmp_path / "release-chapters"
    first_nodes, executions, completed = [], [], []
    try:
        for index in range(4):
            node = store.claim("physical-controller", max_workers=4, worker_slot=f"slot{index + 1}")
            assert node is not None and node["kind"] == "CHAPTER_DRAFT"
            first_nodes.append(node)
            executions.append(_launch_fixture(fixture_cli, node, tmp_path, gate=gate))
        assert len({execution[0].pid for execution in executions}) == 4
        assert all(execution[0].poll() is None for execution in executions)
        assert store.claim("fifth-controller", max_workers=4, worker_slot="slot5") is None
        assert len(store.routing_status()["active_reservations"]) == 4
        assert len(store.active_jobs()) == 4
        # All four child processes published their start before any was allowed
        # to emit output. This establishes physical overlap, not thread timing.
        gate.write_text("release fixture processes", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_finish_execution, store, node, execution)
                       for node, execution in zip(first_nodes, executions)]
            completed.extend(future.result(timeout=20) for future in futures)
        remaining = [store.claim("last-controller", max_workers=4, worker_slot=f"slot{index}")
                     for index in (5, 6)]
        assert all(node is not None and node["kind"] == "CHAPTER_DRAFT" for node in remaining)
        final_gate = tmp_path / "release-final-chapters"
        final_executions = [_launch_fixture(fixture_cli, node, tmp_path, gate=final_gate) for node in remaining]
        executions.extend(final_executions)
        final_gate.write_text("simultaneous final completions", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_finish_execution, store, node, execution)
                       for node, execution in zip(remaining, final_executions)]
            completed.extend(future.result(timeout=20) for future in futures)
    finally:
        _cleanup(executions)
    assert len(completed) == len({job["chapter_id"] for job in completed}) == 6
    assert len({job["worker_slot"] for job in completed}) == 6
    intervals = [(job["receipt"]["fixture_started_at"], job["receipt"]["finished_at"]) for job in completed[:4]]
    assert max(start for start, _ in intervals) < min(end for _, end in intervals)
    before = store.workflow(workflow["workflow_id"])
    assert len([event for event in before["events"] if event["event"] == "SYNTHESIS_RELEASED"]) == 1
    synthesis = store.claim("synthesis-controller", worker_slot="slot1")
    assert synthesis["kind"] == "SYNTHESIS"
    expected = load_routing_policy(synthesis["routing_policy"]).candidates(synthesis["route"]["score"], "SYNTHESIS")[0]
    assert synthesis["route"]["provider"] == expected.provider and synthesis["route"]["model"] == expected.model
    _finish_fixture(store, synthesis, fixture_cli, tmp_path)
    final = store.workflow(workflow["workflow_id"])
    assert final["status"] == "COMPLETED" and len(final["artifacts"]) == 7
    assert len(store.routing_status()["active_reservations"]) == 0
    assert all(job["receipt"]["execution_kind"] == "physical-native-envelope-fixture"
               for job in final["jobs"] if job["kind"] != "MACRO_PLANNING_REQUEST")


def test_busy_native_processes_fall_through_second_third_then_persist_backoff(tmp_path, fixture_cli):
    policy = _policy(backoff=1)
    store = JobStore(tmp_path / "job_board.db", routing_policy=policy)
    workflows = [store.submit(f"List item {number}", ["REQ-1"], 1) for number in range(4)]
    gate = tmp_path / "release-busy"
    nodes, executions = [], []
    try:
        for number in range(3):
            node = store.claim("fixture-controller", worker_slot=f"slot{number + 1}")
            nodes.append(node)
            executions.append(_launch_fixture(fixture_cli, node, tmp_path, gate=gate))
        assert [node["route"]["candidate_index"] for node in nodes] == [0, 1, 2]
        assert [node["route"]["model"] for node in nodes] == ["gemini-3.8-flash", "claude-haiku-4-5", "gpt-6-luna"]
        assert all(execution[0].poll() is None for execution in executions)
        assert store.claim("waiting-controller", worker_slot="slot4") is None
        waiting = store.get(_manifest_id(workflows[3]))
        assert waiting["routing"]["state"] == "WAITING"
        assert waiting["attempts"] == waiting["routing"]["dispatches"] == waiting["routing"]["failure_count"] == 0
        deadline = waiting["routing"]["next_eligible_at"]
        # Independent fresh interpreter reads the same wait and refuses an
        # early dispatch, just as a controller restarting from SQLite must.
        script = "from cochem_pipeline.store import JobStore; import json,sys; s=JobStore(sys.argv[1]); print(json.dumps({'claim':s.claim('restarted-controller',worker_slot='slot4'),'routing':s.get(sys.argv[2])['routing']}))"
        child = subprocess.run([_fixture_executable(), "-c", _controller_script(script), str(store.path), waiting["job_id"]],
                               capture_output=True, text=True, timeout=10)
        assert child.returncode == 0, child.stderr
        observed = json.loads(child.stdout)
        assert observed["claim"] is None and observed["routing"]["next_eligible_at"] == deadline
        gate.write_text("release busy models", encoding="utf-8")
        for node, execution in zip(nodes, executions):
            _finish_execution(store, node, execution)
        # Exclude scattered chapters so this assertion targets the waiting
        # manifest instead of unrelated newly available work.
        excluded = [job["job_id"] for workflow in workflows[:3]
                    for job in store.workflow(workflow["workflow_id"])["jobs"]
                    if job["kind"] == "CHAPTER_DRAFT"]
        if time.time() < deadline:
            assert store.claim("too-early", worker_slot="slot4", exclude_job_ids=excluded) is None
        _wait_until(deadline)
        restarted = JobStore(store.path, routing_policy=policy)
        resumed = restarted.claim("restarted-controller", worker_slot="slot4", exclude_job_ids=excluded)
        assert resumed["job_id"] == waiting["job_id"] and resumed["route"]["candidate_index"] == 0
        assert resumed["route"]["cycle"] == 1
        _finish_fixture(restarted, resumed, fixture_cli, tmp_path)
    finally:
        _cleanup(executions)


def test_independent_controller_processes_atomically_enforce_four_reservations(tmp_path):
    store = JobStore(tmp_path / "job_board.db", routing_policy=_policy(concurrency=4))
    for number in range(6):
        store.submit(f"List independent item {number}", ["REQ-1"], 1)
    gate = tmp_path / "release-independent-controllers"
    script = '''
import json, os, sys, time
from pathlib import Path
from cochem_pipeline.store import JobStore
store = JobStore(sys.argv[1])
Path(sys.argv[2]).write_text(json.dumps({'pid': os.getpid(),
    'execution_kind': 'physical-controller-claim-fixture'}), encoding='utf-8')
deadline = time.monotonic() + 10
while not Path(sys.argv[3]).exists():
    if time.monotonic() >= deadline:
        raise SystemExit(72)
    time.sleep(.005)
print(json.dumps(store.claim('independent-controller', max_workers=4,
                            worker_slot=sys.argv[4])), flush=True)
'''
    executions = []
    try:
        for number in range(6):
            marker = tmp_path / f"controller-ready-{number}.json"
            process = subprocess.Popen([_fixture_executable(), "-c", _controller_script(script), str(store.path), str(marker),
                                        str(gate), f"slot{number + 1}"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            executions.append((process, _wait_ready(marker, process), time.time()))
        assert len({process.pid for process, _, _ in executions}) == 6
        assert all(process.poll() is None for process, _, _ in executions)
        gate.write_text("simultaneous independent SQLite claims", encoding="utf-8")
        results = []
        for process, _, _ in executions:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        claimed = [node for node in results if node is not None]
        assert len(claimed) == len({node["job_id"] for node in claimed}) == 4
        assert len({node["route"]["reservation_id"] for node in claimed}) == 4
        assert all(node["route"]["candidate_index"] == 0 for node in claimed)
        assert len(store.active_jobs()) == len(store.routing_status()["active_reservations"]) == 4
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT count(*) FROM pipeline_route_reservations").fetchone()[0] == 4
    finally:
        _cleanup(executions)


def test_typed_native_quota_uses_all_candidates_then_waits_without_task_failure(tmp_path, fixture_cli):
    from cochem_pipeline.heartbeat import Heartbeat
    from cochem_supervisor.monitor import read_observation
    policy = _policy(backoff=.5)
    store = JobStore(tmp_path / "job_board.db", max_attempts=1, routing_policy=policy)
    workflow = store.submit("List concise checks", ["REQ-1"], 1)
    seen = []
    for index in range(3):
        node = store.claim("quota-controller", worker_slot="slot1")
        assert node is not None and node["route"]["candidate_index"] == index
        seen.append(node)
        failure = _fail_fixture(store, node, fixture_cli, tmp_path, retry_after=.05)
        assert failure.category == "quota"
        assert store.get(node["job_id"])["routing"]["failure_count"] == 0
    waiting = store.get(seen[-1]["job_id"])
    assert waiting["status"] == "PENDING_RETRY" and waiting["routing"]["state"] == "WAITING"
    assert waiting["attempts"] == waiting["routing"]["dispatches"] == 3
    assert waiting["routing"]["failure_count"] == 0
    assert store.claim("early-controller", worker_slot="slot1") is None
    Heartbeat(tmp_path, "physical-fixture-controller").completed_tick({"hardware": {"capacity": 4}, "active": []})
    observation = read_observation(tmp_path, repeated_failures=1, stall_timeout=.001)
    assert not any(incident["repairable"] and incident["category"] == "code" for incident in observation["incidents"])
    assert observation["health"]["routing_wait_count"] == 1
    deadline = waiting["routing"]["next_eligible_at"]
    restarted = JobStore(store.path, max_attempts=1, routing_policy=_policy(concurrency=4))
    assert restarted.get(waiting["job_id"])["routing"]["policy_digest"] == policy.digest
    assert restarted.claim("restarted-too-early", worker_slot="slot1") is None
    _wait_until(deadline)
    resumed = restarted.claim("restarted-controller", worker_slot="slot1")
    assert resumed["route"]["candidate_index"] == 0
    assert resumed["route"]["policy_digest"] == policy.digest
    assert resumed["attempts"] == 4 and resumed["routing"]["failure_count"] == 0
    assert NativeRunner(SimpleNamespace(routing=_policy(concurrency=4))).route(resumed) == resumed["route"]
    _finish_fixture(restarted, resumed, fixture_cli, tmp_path)
    assert restarted.workflow(workflow["workflow_id"])["status"] == "IN_PROGRESS"


def test_backlog_selects_next_model_without_launching_preferred(tmp_path, fixture_cli):
    store = JobStore(tmp_path / "job_board.db", routing_policy=_policy(concurrency=4, backlog=1))
    older = store.submit("List first checks", ["REQ-1"], 1)
    later = store.submit("List second checks", ["REQ-1"], 1)
    node = store.claim("backlog-controller", worker_slot="slot1", exclude_job_ids=[_manifest_id(older)])
    assert node["workflow_id"] == later["workflow_id"]
    assert node["route"]["candidate_index"] == 1 and node["route"]["model"] == "claude-haiku-4-5"
    assert store.get(_manifest_id(older))["attempts"] == 0
    assert any(event["event"] == "ROUTE_SKIPPED" and event["details"]["reason"] == "backlog"
               for event in store.events(later["workflow_id"]))
    _finish_fixture(store, node, fixture_cli, tmp_path)


def test_expired_old_process_cannot_complete_after_different_route_is_accepted(tmp_path, fixture_cli):
    store = JobStore(tmp_path / "job_board.db", routing_policy=_policy())
    workflow = store.submit("List checks", ["REQ-1"], 1)
    old = store.claim("old-controller", lease_seconds=.15, worker_slot="slot1")
    gate = tmp_path / "release-stale"
    old_execution = _launch_fixture(fixture_cli, old, tmp_path, gate=gate)
    try:
        store.set_route_hold("model", old["route"]["key"], 5, "busy")
        _wait_until(old["lease_expires_at"])
        restarted = JobStore(store.path)
        fresh = restarted.claim("new-controller", worker_slot="slot1")
        assert fresh["job_id"] == old["job_id"]
        assert fresh["attempt_id"] != old["attempt_id"] and fresh["fencing_token"] > old["fencing_token"]
        assert fresh["route"]["model"] != old["route"]["model"]
        accepted = _finish_fixture(restarted, fresh, fixture_cli, tmp_path)
        gate.write_text("release stale output", encoding="utf-8")
        output, receipt = _collect_fixture(old, old_execution)
        with pytest.raises(ValueError, match="Stale|unowned"):
            restarted.complete(old["job_id"], old["attempt_id"], old["fencing_token"], output, receipt)
        assert restarted.get(old["job_id"])["receipt"] == accepted["receipt"]
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT count(*) FROM pipeline_outputs WHERE job_id=?", (old["job_id"],)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM pipeline_route_reservations WHERE job_id=? AND released_at IS NULL", (old["job_id"],)).fetchone()[0] == 0
    finally:
        _cleanup([old_execution])


def test_real_controller_crash_after_route_claim_recovers_without_duplicate_reservation(tmp_path, fixture_cli):
    store = JobStore(tmp_path / "job_board.db", routing_policy=_policy())
    workflow = store.submit("List checks", ["REQ-1"], 1)
    script = "from cochem_pipeline.store import JobStore; import json,os,sys; s=JobStore(sys.argv[1]); print(json.dumps(s.claim('abrupt-controller',lease_seconds=.1,worker_slot='slot1')),flush=True); os._exit(73)"
    child = subprocess.run([_fixture_executable(), "-c", _controller_script(script), str(store.path)], capture_output=True, text=True, timeout=10)
    assert child.returncode == 73, child.stderr
    abandoned = json.loads(child.stdout)
    assert abandoned["route"]["reservation_id"]
    _wait_until(abandoned["lease_expires_at"])
    restarted = JobStore(store.path, routing_policy=_policy(concurrency=4))
    recovered = restarted.claim("recovered-controller", worker_slot="slot1")
    assert recovered["job_id"] == abandoned["job_id"]
    assert recovered["route"]["reservation_id"] != abandoned["route"]["reservation_id"]
    assert recovered["route"]["policy_digest"] == abandoned["route"]["policy_digest"]
    assert len(restarted.routing_status()["active_reservations"]) == 1
    _finish_fixture(restarted, recovered, fixture_cli, tmp_path)
    assert len(restarted.routing_status()["active_reservations"]) == 0
    assert len([event for event in restarted.events(workflow["workflow_id"]) if event["event"] == "COMPLETED"]) == 1


def test_expired_physical_native_process_blocks_restart_until_verified_child_closure(tmp_path, fixture_cli):
    # These numbers are declared fixture boot identities. Production obtains
    # its identity from trusted Windows metadata; this test does not attest WMI.
    policy = _policy()
    store = JobStore(tmp_path / "job_board.db", routing_policy=policy, cleanup_boot_id=123)
    store.submit("List concise checks", ["REQ-1"], 1)
    old = store.claim("old-controller", lease_seconds=.15, worker_slot="slot1", requires_cleanup=True)
    gate = tmp_path / "close-prior-native-process"
    execution = _launch_fixture(fixture_cli, old, tmp_path, gate=gate)
    try:
        _wait_until(old["lease_expires_at"])
        restarted = JobStore(store.path, routing_policy=policy, cleanup_boot_id=123)
        assert restarted.recover_execution_quarantines_after_boot(123) == []
        assert len(restarted.quarantine_unclosed_executions()) == 1
        assert execution[0].poll() is None
        assert restarted.claim("new-controller", worker_slot="slot2", requires_cleanup=True) is None
        with pytest.raises(ValueError, match="identity"):
            restarted.confirm_execution_cleanup(old["job_id"], old["attempt_id"], old["fencing_token"] + 1)
        assert restarted.claim("still-blocked", worker_slot="slot2", requires_cleanup=True) is None
        gate.write_text("allow actual fixture process to exit", encoding="utf-8")
        stale_output, stale_receipt = _collect_fixture(old, execution)
        assert execution[0].returncode == 0
        # This trusted acknowledgement follows the real process wait, never a
        # generated model statement or elapsed lease alone.
        assert restarted.confirm_execution_cleanup(old["job_id"], old["attempt_id"], old["fencing_token"])
        with pytest.raises(ValueError, match="Stale|unowned"):
            restarted.complete(old["job_id"], old["attempt_id"], old["fencing_token"], stale_output, stale_receipt)
        restarted.set_route_hold("model", old["route"]["key"], 5, "busy")
        fresh = restarted.claim("new-controller", worker_slot="slot2", requires_cleanup=True)
        assert fresh["job_id"] == old["job_id"] and fresh["route"]["candidate_index"] == 1
        output, receipt = _collect_fixture(fresh, _launch_fixture(fixture_cli, fresh, tmp_path))
        assert restarted.confirm_execution_cleanup(fresh["job_id"], fresh["attempt_id"], fresh["fencing_token"])
        restarted.complete(fresh["job_id"], fresh["attempt_id"], fresh["fencing_token"], output, receipt)
        assert restarted.execution_quarantines() == []
    finally:
        _cleanup([execution])


def test_score_ten_exhausts_exactly_two_native_candidates_before_backoff(tmp_path, fixture_cli):
    store = JobStore(tmp_path / "job_board.db", max_attempts=1, routing_policy=_policy(backoff=.5))
    store.submit("security migration concurrency " + "detail " * 5000,
                 [f"REQ-{number}" for number in range(12)], 8)
    nodes = []
    for index in range(2):
        node = store.claim("score-ten-controller", worker_slot="slot1")
        assert node["route"]["score"] == 10 and node["route"]["candidate_index"] == index
        nodes.append(node)
        _fail_fixture(store, node, fixture_cli, tmp_path, retry_after=.05)
    assert [node["route"]["model"] for node in nodes] == ["claude-fable-5-1", "gpt-6-astra"]
    assert nodes[1]["route"]["reasoning_effort"] == "ultra"
    state = store.get(nodes[0]["job_id"])["routing"]
    assert state["state"] == "WAITING" and state["dispatches"] == 2 and state["failure_count"] == 0
    assert len(state["candidates"]) == 2
    assert store.claim("no-third-dispatch", worker_slot="slot1") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM pipeline_route_reservations").fetchone()[0] == 2
    _wait_until(state["next_eligible_at"])
    resumed = JobStore(store.path).claim("next-cycle", worker_slot="slot1")
    assert resumed["route"]["candidate_index"] == 0 and resumed["route"]["model"] == "claude-fable-5-1"


def test_shared_subscription_pool_skips_otherwise_idle_second_model(tmp_path, fixture_cli):
    values = _policy(concurrency=4).as_dict()
    for provider in ("claude", "codex"):
        values["provider_limits"][provider]["quota_pool"] = "shared-subscription-fixture"
    values["quota_pool_limits"] = {"shared-subscription-fixture": 1, "gemini": 4}
    store = JobStore(tmp_path / "job_board.db", routing_policy=load_routing_policy(values))
    store.submit("Draft operational chapters", ["REQ-1"], 6)
    store.submit("Draft more operational chapters", ["REQ-1"], 6)
    first = store.claim("shared-pool-controller", worker_slot="slot1")
    gate = tmp_path / "shared-pool-release"
    execution = _launch_fixture(fixture_cli, first, tmp_path, gate=gate)
    try:
        assert first["route"]["model"] == "claude-sonnet-5-5" and execution[0].poll() is None
        second = store.claim("shared-pool-controller", worker_slot="slot2")
        assert second["route"]["candidate_index"] == 2
        assert second["route"]["model"] == "gemini-3.8-flash"
        assert second["route"]["reasoning_effort"] == "extended"
        assert [route["provider"] for route in store.routing_status()["active_reservations"]] == ["claude", "gemini"]
        _finish_fixture(store, second, fixture_cli, tmp_path)
        gate.write_text("release subscription fixture", encoding="utf-8")
        _finish_execution(store, first, execution)
    finally:
        _cleanup([execution])
