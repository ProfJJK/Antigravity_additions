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
from supervisor_tests.test_engine import LocalProtocolDriver, LocalSupervisor


class ReviewedEffortContractDriver(LocalProtocolDriver):
    """Real contract parsing; native processes remain explicit boundary fixtures."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.preflight_calls = []

    def preflight_selected_spec(self, spec, identity, workspace, log_dir, heartbeat):
        from cochem_pipeline.native_effort import effort_contract
        from cochem_supervisor.runner import NativeRepairProtocolError
        self.preflight_calls.append((spec['provider'], spec['model'], spec.get('reasoning_effort')))
        try:
            effort_contract(spec['provider'], spec['model'], spec.get('reasoning_effort'), spec)
        except ValueError as exc:
            raise NativeRepairProtocolError('Reviewed native effort contract is missing in this explicit fixture') from exc
        return super().preflight_selected_spec(spec, identity, workspace, log_dir, heartbeat)


def test_catalogue_v2_open_preserves_v1_database_authority_and_active_reservation(tmp_path):
    from cochem_pipeline.routing import RoutingPolicy, load_routing_policy, validate_selected_route
    legacy = load_routing_policy({"policy_version": 1})
    database = tmp_path / "captured_v1.db"
    original = JobStore(database, routing_policy=legacy)
    workflow = original.submit("Review concurrent security schema migration", ["REQ-1"], 1)
    active = original.claim("historical-controller", worker_slot="slot1")
    assert active["route"]["policy_digest"] == legacy.digest
    with sqlite3.connect(database) as connection:
        before = {
            table: connection.execute("SELECT * FROM " + table).fetchall()
            for table in ("pipeline_routing_workflows", "pipeline_routing_jobs", "pipeline_route_reservations")
        }
    upgraded = JobStore(database, routing_policy=RoutingPolicy())
    observed = upgraded.get(active["job_id"])
    assert observed["route"] == active["route"]
    assert observed["routing"]["policy"] == legacy.as_dict()
    validate_selected_route(legacy, observed["route"], kind=observed["kind"])
    assert upgraded.workflow(workflow["workflow_id"])["status"] == original.workflow(workflow["workflow_id"])["status"]
    with sqlite3.connect(database) as connection:
        after = {table: connection.execute("SELECT * FROM " + table).fetchall() for table in before}
    assert after == before


@pytest.mark.parametrize('top_tier', [False, True])
def test_primary_old_jobs_skip_retired_targets_before_reserving_without_rewriting_policy(tmp_path, top_tier):
    from cochem_pipeline.routing import RoutingPolicy, load_routing_policy
    legacy = load_routing_policy({'policy_version': 1})
    database = tmp_path / 'primary-legacy-pending.db'
    original = JobStore(database, routing_policy=legacy)
    objective = 'security migration concurrency' + (' detail' * 600 if top_tier else '')
    workflow = original.submit(objective, [f'REQ-{index}' for index in range(12)], 8 if top_tier else 1)
    manifest = next(job for job in original.workflow(workflow['workflow_id'])['jobs'] if job['kind'] == 'MANIFEST_GENERATOR')
    assert manifest['routing']['score_details']['tier'] == ('10' if top_tier else '7-9')
    policy_before = manifest['routing']['policy']
    upgraded = JobStore(database, routing_policy=RoutingPolicy())
    route = upgraded.claim('current-controller', worker_slot='slot1')
    stored = upgraded.get(manifest['job_id'])
    assert stored['routing']['policy'] == policy_before == legacy.as_dict()
    if top_tier:
        assert route['route']['key'] == 'codex:gpt-6-astra:ultra'
        assert route['route']['candidate_index'] == 1
        assert route['route']['policy_digest'] == legacy.digest
        assert stored['attempts'] == stored['routing']['dispatches'] == 1
    else:
        assert route is None
        assert stored['attempts'] == stored['routing']['dispatches'] == 0
        assert stored['routing']['state'] == 'WAITING'
    with sqlite3.connect(database) as connection:
        skipped = connection.execute("SELECT details_json FROM pipeline_events WHERE job_id=? AND event='ROUTE_SKIPPED'", (manifest['job_id'],)).fetchall()
    assert len(skipped) == (1 if top_tier else 3)
    assert all(json.loads(row[0])['reason'] == 'retired_target' for row in skipped)


@pytest.mark.parametrize('top_tier', [False, True])
def test_repair_old_jobs_use_only_still_approved_exact_captured_targets(tmp_path, top_tier):
    from cochem_pipeline.routing import load_routing_policy
    from cochem_supervisor.job_board import RepairJobBoard
    legacy = load_routing_policy({'policy_version': 1}).as_dict()
    board = RepairJobBoard(tmp_path / 'repair-legacy-pending.db')
    payload = {'objective': 'security migration concurrency' + (' detail' * 600 if top_tier else ''),
               'requirements': [f'REQ-{index}' for index in range(12)], 'chapter_count': 8 if top_tier else 1}
    job = board.submit('legacy', payload, legacy, now=100)
    assert job['scoring']['tier'] == ('10' if top_tier else '7-9')
    route = board.claim(job['job_id'], ['codex', 'claude', 'gemini'], primary_cleanup_verified=True, now=101)
    state = board.get(job['job_id'])
    assert state['policy'] == legacy
    if top_tier:
        assert route['key'] == 'codex:gpt-6-astra:ultra' and route['candidate_index'] == 1
        assert route['policy_digest'] == load_routing_policy(legacy).digest
        assert state['dispatches'] == 1
    else:
        assert route is None and state['dispatches'] == 0
        assert state['status'] == 'PENDING_RETRY' and state['next_eligible'] > 101
    with sqlite3.connect(board.path) as connection:
        skipped = connection.execute("SELECT details FROM repair_route_events WHERE event='CANDIDATE_SKIPPED'").fetchall()
        assert connection.execute("SELECT count(*) FROM repair_route_events WHERE event='BUDGET_ATTACHED'").fetchone()[0] == 0
    assert len(skipped) == (1 if top_tier else 3)
    assert all(json.loads(row[0])['reason'] == 'retired_target' for row in skipped)


def test_supervisor_legacy_retired_routes_do_not_charge_paid_model_budget(tmp_path):
    from cochem_pipeline.routing import load_routing_policy
    from cochem_supervisor.job_board import RepairJobBoard
    from cochem_supervisor.replay import repair_scoring_payload
    from supervisor_tests.test_engine import incident
    supervisor = LocalSupervisor(tmp_path)
    value = incident(supervisor)
    value['objective'] = 'Review implementation' + ' detail' * 600
    board = RepairJobBoard(supervisor.private / 'repair-job-board.db')
    job = board.submit(value['fingerprint'], repair_scoring_payload(value), load_routing_policy({'policy_version': 1}).as_dict())
    assert job['scoring']['tier'] == '7-9'
    assert supervisor._repair(value) is False
    assert supervisor.runner.repair_calls == []
    budget = supervisor.ledger.get_incident(value['fingerprint'])
    assert budget['attempts'] == budget['model_calls'] == 0
    assert board.get(job['job_id'])['dispatches'] == 0
    with sqlite3.connect(supervisor.ledger.path) as connection:
        assert connection.execute('SELECT count(*) FROM supervisor_attempts').fetchone()[0] == 0


def test_generation_missing_effort_contract_does_not_charge_budget_or_run_model(tmp_path):
    from cochem_supervisor.io import write_json
    from cochem_supervisor.job_board import RepairJobBoard
    from supervisor_tests.test_engine import incident
    driver = ReviewedEffortContractDriver()
    supervisor = LocalSupervisor(tmp_path, driver)
    supervisor.config['providers'] = [{'provider':'claude', 'model':'claude-opus-5-5', 'executable':sys.executable}]
    write_json(supervisor.private/'cli-contracts.json', {'providers': {'claude': {
        'available':True, 'missing_flags':[], 'exit_code':0, 'help_exit_code':0}}})
    value = incident(supervisor)
    value['objective'] = 'Review implementation' + ' detail' * 600
    assert supervisor._repair(value) is False
    assert driver.preflight_calls == [('claude', 'claude-opus-5-5', 'extended')]
    assert driver.repair_calls == []
    budget = supervisor.ledger.get_incident(value['fingerprint'])
    assert budget['attempts'] == budget['model_calls'] == 0
    board = RepairJobBoard(supervisor.private/'repair-job-board.db')
    with sqlite3.connect(board.path) as connection:
        row = connection.execute('SELECT job_id,dispatches,status FROM repair_jobs').fetchone()
        assert row[1:] == (0, 'PENDING_RETRY')
        assert connection.execute("SELECT count(*) FROM repair_route_events WHERE event='BUDGET_ATTACHED'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM repair_route_events WHERE event='PREFLIGHT_REJECTED_NO_INFERENCE'").fetchone()[0] == 1


def test_review_missing_effort_contract_preserves_original_paid_generation_budget(tmp_path):
    from supervisor_tests.test_engine import incident
    driver = ReviewedEffortContractDriver(acceptance='passed')
    supervisor = LocalSupervisor(tmp_path, driver)
    supervisor.config.update(auto_deploy=True, repair_timeout_seconds=.03)
    value = incident(supervisor)
    previous_release = supervisor.releases.current()
    supervisor._repair(value)
    budget = supervisor.ledger.get_incident(value['fingerprint'])
    assert budget['attempts'] == budget['model_calls'] == 1
    assert driver.repair_calls == ['codex']
    assert any(provider == 'claude' and effort == 'extended' for provider, model, effort in driver.preflight_calls)
    assert supervisor.releases.current() == previous_release
    assert supervisor.ledger.pending_reviews()
    with sqlite3.connect(supervisor.private/'repair-job-board.db') as connection:
        row = connection.execute("SELECT dispatches,status FROM repair_jobs WHERE kind='REPAIR_REVIEW'").fetchone()
        assert row == (0, 'PENDING_RETRY')


def test_missing_fable_contract_spills_to_exact_astra_before_any_paid_charge(tmp_path):
    from cochem_supervisor.job_board import RepairJobBoard
    driver = ReviewedEffortContractDriver()
    supervisor = LocalSupervisor(tmp_path, driver)
    board = RepairJobBoard(supervisor.private/'repair-job-board.db')
    payload = {'objective':'security migration concurrency' + ' detail' * 600,
               'requirements':[f'REQ-{index}' for index in range(12)], 'chapter_count':8}
    job = board.submit('top-tier-unspent', payload)
    assert job['scoring']['tier'] == '10'
    providers = {provider:{'provider':provider, 'model':'unused-until-exact-selection', 'executable':sys.executable}
                 for provider in ('claude', 'codex')}
    route = supervisor._claim_preflighted_route(board, job['job_id'], providers, Path(supervisor.config['repair_workspace']))
    assert route['key'] == 'codex:gpt-6-astra:ultra' and route['candidate_index'] == 1
    assert driver.preflight_calls == [('claude', 'claude-fable-5-1', 'extended'), ('codex', 'gpt-6-astra', 'ultra')]
    assert board.get(job['job_id'])['dispatches'] == 1
    with sqlite3.connect(supervisor.ledger.path) as connection:
        assert connection.execute('SELECT count(*) FROM supervisor_attempts').fetchone()[0] == 0


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
    # Explicit historical fixture only: emulate the pre-universal writer. New
    # production JobStore construction always captures Chapter 06 routing.
    legacy.routing_policy = None
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
    assert synthesis["route"]["model"] == synthesis["routing"]["candidates"][0]["model"]
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
