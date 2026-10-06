"""Supervisor composition with real SQLite/files and an explicit local test driver.

The driver launches Python subprocesses that write protocol fixtures. It does
not invoke a model, native Windows identity, ACL, or Scheduled Task. Only those
platform/driver boundaries are replaced; engine decisions, receipts, snapshots,
release journals, and durable budgets are exercised directly without mocks.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from cochem_pipeline.heartbeat import Heartbeat
from cochem_pipeline.runtime import clear_slot
from cochem_pipeline.store import JobStore
from cochem_supervisor.engine import Supervisor
from cochem_supervisor.io import write_json
from cochem_supervisor.monitor import read_observation
from cochem_supervisor.releases import ReleaseError, ReleaseStore, manifest_digest, snapshot_tree, tree_manifest
from cochem_supervisor.state import Ledger


class LocalProtocolDriver:
    """Controlled subprocess fixture; provider/model execution is never claimed."""

    def __init__(self, *, acceptance="nonzero", missing_flag=False, quarantined=False):
        self.acceptance = acceptance
        self.missing_flag = missing_flag
        self.quarantined_identities = frozenset({"repairaccount"} if quarantined else set())
        self.process_calls = []
        self.repair_calls = []
        self.repair_evidence = []
        self.terminated = False

    def _process(self, script, args, folder, heartbeat):
        assert heartbeat() is True
        folder.mkdir(parents=True, exist_ok=False)
        stdout, stderr = folder / "stdout.log", folder / "stderr.log"
        with stdout.open("wb") as out, stderr.open("wb") as err:
            child = subprocess.Popen([sys.executable, "-c", script, *args], stdout=out, stderr=err)
            returncode = child.wait(timeout=10)
        return {"test_driver": "local Python protocol fixture", "pid": child.pid,
                "exit_code": returncode, "stdout_path": str(stdout), "stderr_path": str(stderr)}

    def run_process(self, identity, argv, cwd, log_dir, *, timeout_seconds, heartbeat):
        self.process_calls.append(list(argv))
        if "cochem_supervisor.acceptance" in argv:
            report = argv[argv.index("--report") + 1]
            if self.acceptance == "nonzero":
                return self._process("import sys; print('fixture acceptance failure'); sys.exit(1)", [], log_dir, heartbeat)
            outcomes = '<failure message="fixture failure" />' if self.acceptance == "junit_failure" else ""
            count = "1" if outcomes else "0"
            report_text = ('<testsuite tests="1" failures="' + count + '" errors="0" skipped="0">'
                           '<testcase classname="pipeline_tests.local_protocol_fixture" name="acceptance_contract">' + outcomes + '</testcase></testsuite>')
            return self._process("from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2],encoding='utf-8')",
                                 [report, report_text], log_dir, heartbeat)
        if "--version" in argv:
            output = "local protocol fixture version 1; not a native provider"
        else:
            output = "--json --model --ephemeral" if self.missing_flag else "--json --model --ignore-user-config --ephemeral"
        return self._process("import sys; print(sys.argv[1])", [output], log_dir, heartbeat)

    def run(self, spec, identity, candidate, evidence, log_dir, timeout_seconds, heartbeat):
        self.repair_calls.append(spec["provider"])
        self.repair_evidence.append(json.loads(json.dumps(evidence)))
        script = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('# controlled fixture change\\n',encoding='utf-8')"
        return self._process(script, [str(candidate / "src" / "cochem_pipeline" / "runtime.py")], log_dir, heartbeat)

    def terminate(self):
        self.terminated = True


class LocalSupervisor(Supervisor):
    """Fixture construction bypasses Windows provisioning, never core methods."""

    def __init__(self, root, driver=None):
        self.private = root / "private"
        self.private.mkdir()
        baseline, workspace, releases, acceptance, pipeline = (root / name for name in
                                                               ("baseline", "workspace", "releases", "acceptance", "pipeline"))
        for directory in (baseline / "src" / "cochem_pipeline", workspace, releases, acceptance, pipeline):
            directory.mkdir(parents=True)
        (baseline / "src" / "cochem_pipeline" / "runtime.py").write_text("# original baseline\n", encoding="utf-8")
        (acceptance / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        self.config = {
            "private_root": str(self.private), "release_root": str(releases), "pointer_file": str(self.private / "current.json"),
            "repair_workspace": str(workspace), "acceptance_root": str(acceptance), "pipeline_private_root": str(pipeline),
            "repair_worker": {"name": "RepairAccount", "credential_target": "test-fixture-only"},
            "providers": [{"provider": "codex", "model": "gpt-6-astra", "executable": sys.executable}],
            "allowed_paths": ["src/cochem_pipeline/"], "test_python": sys.executable,
            "test_targets": ["pipeline_tests"], "max_per_incident": 2, "max_per_day": 4,
            "cooldown_seconds": 0, "repair_timeout_seconds": 10, "test_timeout_seconds": 10,
            "minimum_passed_tests": 1, "maximum_skipped_tests": 0, "auto_deploy": False,
            "heartbeat_timeout": 30, "stall_timeout": 600, "repeated_failures": 1,
            "startup_grace_seconds": 120, "version_probe_seconds": 3600, "poll_seconds": 1,
        }
        self.ledger = Ledger(self.private / "supervisor.db")
        self.releases = ReleaseStore(releases, self.private / "current.json", self.private / "release-journal.json")
        self.releases.bootstrap(baseline)
        self.runner = driver or LocalProtocolDriver()
        self.stop_event = threading.Event()
        self.started_at = time.time() - 121
        self.start_requested_at = 0.0
        self.stage = "starting"
        self.current_attempt = None
        self.smoke_report = None
        self.smoke_candidate = None
        self.acceptance_manifest = tree_manifest(acceptance)
        self.clear_calls = 0
        self.lifecycle = []
        self.protected = []
        self.candidate_probe_result = True
        self.previous_probe_result = True
        self.smoke_failure_category = None
        write_json(self.private / "cli-contracts.json", {
            "checked_at": time.time(), "incidents": [],
            "providers": {"codex": {"available": True, "missing_flags": [], "exit_code": 0, "help_exit_code": 0}},
        })
        JobStore(pipeline / "job_board.db")
        Heartbeat(pipeline, "4.2.3").completed_tick({"capacity": 4, "active_count": 0})

    def _clear_workspace(self):
        self.clear_calls += 1
        clear_slot(Path(self.config["repair_workspace"]))

    def _protect_release_tree(self, path):
        self.protected.append(Path(path))

    def _prepare_workspace(self, candidate):
        # Native repair-account ACL provisioning is outside this local fixture.
        assert Path(candidate).is_dir()

    def _stop(self):
        self.lifecycle.append("stop")
        return True

    def _start(self, source):
        self.lifecycle.append(("start", Path(source)))
        self.start_requested_at = time.time()
        return True

    def _probe(self, source):
        self.lifecycle.append(("probe", Path(source)))
        if self.smoke_candidate is not None and Path(source) == self.smoke_candidate:
            self.smoke_report = {"test_driver": "local lifecycle fixture; no model execution",
                                 "passed": self.candidate_probe_result,
                                 "failure_category": self.smoke_failure_category}
            return self.candidate_probe_result
        return self.previous_probe_result


def incident(supervisor, *, heartbeat=False):
    value = {"fingerprint": "fixture-heartbeat" if heartbeat else "fixture-code", "category": "code",
             "repairable": True, "summary": "Pipeline heartbeat stale" if heartbeat else "TypeError: local fixture failure",
             "evidence": {"latest_failure_at": time.time()}}
    supervisor.ledger.observe(value["fingerprint"], value["category"], value)
    return value


def test_quarantined_identity_refuses_before_workspace_clear_or_new_reservation(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(quarantined=True))
    value = incident(supervisor)
    sentinel = Path(supervisor.config["repair_workspace"]) / "possibly-active-process-data.txt"
    sentinel.write_text("must remain untouched", encoding="utf-8")
    assert supervisor._repair(value) is False
    assert sentinel.read_text() == "must remain untouched"
    assert supervisor.clear_calls == 0
    assert supervisor.ledger.get_incident(value["fingerprint"])["attempts"] == 0
    assert supervisor.runner.repair_calls == []
    # A fresh runner cannot discard a durable quarantine from its predecessor.
    supervisor.runner = LocalProtocolDriver()
    supervisor.ledger = Ledger(supervisor.ledger.path)
    assert supervisor._repair(value) is False
    assert sentinel.read_text() == "must remain untouched"
    assert supervisor.clear_calls == 0


def test_cached_failed_cli_probe_remains_visible_without_reexecuting_children(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(missing_flag=True))
    (supervisor.private / "cli-contracts.json").unlink()
    initial = supervisor._version_checks()
    assert len(initial) == 1 and initial[0]["category"] == "configuration"
    assert initial[0]["repairable"] is False
    assert "--ignore-user-config" in initial[0]["evidence"]["missing_flags"]
    cached = supervisor._version_checks()
    assert cached == initial
    assert len(supervisor.runner.process_calls) == 2
    assert (supervisor.private / "cli-contracts.json").is_file()


@pytest.mark.parametrize("failure", ["nonzero", "junit_failure"])
def test_failed_independent_validation_retries_until_durable_budget_is_exhausted(tmp_path, failure):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance=failure))
    supervisor.config["providers"].append({"provider": "claude", "model": "claude-fable-5-1", "executable": sys.executable})
    contracts = json.loads((supervisor.private / "cli-contracts.json").read_text())
    contracts["providers"]["claude"] = dict(contracts["providers"]["codex"])
    write_json(supervisor.private / "cli-contracts.json", contracts)
    value = incident(supervisor)
    original = supervisor.releases.current()
    for count, expected_status in ((1, "OPEN"), (2, "EXHAUSTED")):
        assert supervisor._repair(value) is True
        stored = supervisor.ledger.get_incident(value["fingerprint"])
        assert stored["status"] == expected_status and stored["attempts"] == count
        terminal = supervisor.ledger.history(value["fingerprint"])[-1]
        assert terminal["event"] == "ATTEMPT_FAILED"
        receipt = json.loads((supervisor.private / "attempts" / terminal["attempt_id"] / "supervisor-receipt.json").read_text())
        assert receipt["deployed"] is False
        assert supervisor.releases.current() == original
        assert supervisor.current_attempt is None
    assert supervisor._repair(value) is False
    assert supervisor.runner.repair_calls == ["codex", "claude"] and supervisor.clear_calls == 2
    first_input, retry_input = supervisor.runner.repair_evidence
    assert first_input["previous_failure"] is None
    assert retry_input["previous_failure"]["failure"]["category"] == "code"
    if failure == "nonzero":
        assert "fixture acceptance failure" in retry_input["previous_failure"]["test_diagnostic"]["stdout_path"]
    else:
        assert "JUnit reports failed" in retry_input["previous_failure"]["failure"]["diagnostic"]


def test_valid_candidate_held_by_deploy_policy_preserves_real_receipt_and_pointer(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    value = incident(supervisor)
    original = supervisor.releases.current()
    assert supervisor._repair(value) is True
    assert supervisor.ledger.get_incident(value["fingerprint"])["status"] == "BLOCKED"
    assert supervisor.releases.current() == original
    attempt = supervisor.ledger.history(value["fingerprint"])[-1]
    assert attempt["event"] == "ATTEMPT_BLOCKED"
    assert attempt["details"]["tests"]["passed"] == 1
    assert supervisor.lifecycle == []
    assert json.loads((supervisor.private / "supervisor-status.json").read_text())["quarantined_identities"] == []


def test_restart_marker_is_not_forgotten_after_display_history_rolls_over(tmp_path):
    supervisor = LocalSupervisor(tmp_path)
    value = incident(supervisor, heartbeat=True)
    assert supervisor._restart_once(value) is True
    for index in range(1005):
        supervisor.ledger.record_event("LATER_OBSERVATION", {"index": index}, fingerprint=value["fingerprint"])
    supervisor.ledger = Ledger(supervisor.ledger.path)
    assert supervisor._restart_once(value) is False
    assert len(supervisor.lifecycle) == 3


def test_resolved_old_database_failure_cannot_reopen_or_spend_again(tmp_path):
    supervisor = LocalSupervisor(tmp_path)
    store = JobStore(Path(supervisor.config["pipeline_private_root"]) / "job_board.db", max_attempts=1)
    store.submit("Physical failure observation fixture", ["REQ-1"], 1)
    job = store.claim("fixture-owner")
    store.fail(job["job_id"], job["attempt_id"], job["fencing_token"], "TypeError: repeated fixture failure")
    value = read_observation(supervisor.config["pipeline_private_root"], repeated_failures=1)["incidents"][0]
    supervisor.ledger.observe(value["fingerprint"], value["category"], value)
    reserved = supervisor.ledger.reserve(value["fingerprint"], cooldown_seconds=0)
    supervisor.ledger.finish(reserved["attempt_id"], "SUCCEEDED", {"test_only": "stored resolution boundary"})
    before = supervisor.ledger.get_incident(value["fingerprint"])
    write_json(supervisor.private / "cli-contracts.json", {"checked_at": time.time(), "providers": {}, "incidents": []})
    supervisor.tick()
    assert supervisor.ledger.get_incident(value["fingerprint"]) == before
    assert supervisor.runner.repair_calls == []


def test_explicit_once_tick_can_process_update_during_startup_grace(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    supervisor.started_at = time.time()
    value = {"fingerprint": "operator-update-fixture", "category": "update", "repairable": True,
             "summary": "Operator requested a local integration fixture update",
             "objective": "Controlled local fixture change", "evidence": {"source": "operator-request"}}
    supervisor.ledger.observe(value["fingerprint"], value["category"], value)
    supervisor.tick()
    assert supervisor.ledger.get_incident(value["fingerprint"])["attempts"] == 0
    supervisor.tick(ignore_startup_grace=True)
    stored = supervisor.ledger.get_incident(value["fingerprint"])
    assert stored["attempts"] == 1 and stored["status"] == "BLOCKED"
    assert len(supervisor.runner.repair_calls) == 1


def test_missing_native_executable_cannot_authorize_repair_or_clear_workspace(tmp_path):
    supervisor = LocalSupervisor(tmp_path)
    supervisor.config["providers"][0]["executable"] = str(tmp_path / "missing-cli")
    (supervisor.private / "cli-contracts.json").unlink()
    assert supervisor._version_checks() == []
    record = json.loads((supervisor.private / "cli-contracts.json").read_text())
    assert record["providers"]["codex"]["available"] is False
    value = incident(supervisor)
    assert supervisor._repair(value) is False
    assert supervisor.clear_calls == 0 and supervisor.runner.process_calls == []
    assert supervisor.ledger.get_incident(value["fingerprint"])["attempts"] == 0


def test_retry_evidence_uses_most_recent_failure_from_durable_history(tmp_path):
    supervisor = LocalSupervisor(tmp_path)
    value = incident(supervisor)
    for diagnostic in ("older validation failure", "most recent validation failure"):
        reserved = supervisor.ledger.reserve(value["fingerprint"], cooldown_seconds=0)
        supervisor.ledger.finish(reserved["attempt_id"], "FAILED", {
            "failure": {"category": "code", "diagnostic": diagnostic},
            "test_diagnostic": {"stdout_path": diagnostic},
        })
    supervisor.ledger = Ledger(supervisor.ledger.path)
    evidence = supervisor._previous_failure(value["fingerprint"])
    assert evidence["failure"]["diagnostic"] == "most recent validation failure"
    assert evidence["test_diagnostic"]["stdout_path"] == "most recent validation failure"


def test_successful_deployment_commits_actual_release_pointer_and_resolves_incident(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    supervisor.config["auto_deploy"] = True
    value = incident(supervisor)
    original = supervisor.releases.current()
    assert supervisor._repair(value) is True
    active = supervisor.releases.current()
    assert active["digest"] != original["digest"]
    active_source = Path(active["source_root"])
    assert (active_source / "src" / "cochem_pipeline" / "runtime.py").read_text() == "# controlled fixture change\n"
    assert manifest_digest(tree_manifest(active_source)) == active["digest"]
    journal = json.loads(supervisor.releases.journal.read_text())
    assert journal["state"] == "COMMITTED" and journal["candidate"] == active
    assert journal["previous"] == original
    assert supervisor.lifecycle == ["stop", ("start", active_source), ("probe", active_source)]
    assert supervisor.ledger.get_incident(value["fingerprint"])["status"] == "RESOLVED"
    terminal = supervisor.ledger.history(value["fingerprint"])[-1]
    assert terminal["event"] == "ATTEMPT_SUCCEEDED"
    assert terminal["details"]["deployed"] is True
    assert terminal["details"]["live_smoke"]["passed"] is True
    assert supervisor._repair(value) is False
    assert len(supervisor.runner.repair_calls) == 1


@pytest.mark.parametrize("failure_category", [None, "code"])
def test_failed_candidate_health_or_code_smoke_restores_pointer_and_allows_bounded_retry(tmp_path, failure_category):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    supervisor.config["auto_deploy"] = True
    supervisor.candidate_probe_result = False
    supervisor.smoke_failure_category = failure_category
    value = incident(supervisor)
    original = supervisor.releases.current()
    assert supervisor._repair(value) is True
    assert supervisor.releases.current() == original
    journal = json.loads(supervisor.releases.journal.read_text())
    assert journal["state"] == "ROLLED_BACK"
    candidate, previous = Path(journal["candidate"]["source_root"]), Path(original["source_root"])
    assert supervisor.lifecycle == ["stop", ("start", candidate), ("probe", candidate),
                                    "stop", ("start", previous), ("probe", previous)]
    stored = supervisor.ledger.get_incident(value["fingerprint"])
    assert stored["status"] == "OPEN" and stored["attempts"] == 1
    first_terminal = supervisor.ledger.history(value["fingerprint"])[-1]
    assert first_terminal["event"] == "ATTEMPT_ROLLED_BACK"
    assert first_terminal["details"]["deployed"] is False
    supervisor.candidate_probe_result = True
    supervisor.smoke_failure_category = None
    assert supervisor._repair(value) is True
    assert supervisor.releases.current() == journal["candidate"]
    assert json.loads(supervisor.releases.journal.read_text())["state"] == "COMMITTED"
    assert supervisor.ledger.get_incident(value["fingerprint"])["status"] == "RESOLVED"
    assert supervisor.ledger.get_incident(value["fingerprint"])["attempts"] == 2
    previous_failure = supervisor.runner.repair_evidence[1]["previous_failure"]
    assert previous_failure["live_smoke"]["passed"] is False
    assert previous_failure["live_smoke"]["failure_category"] == failure_category


def test_external_quota_smoke_failure_rolls_back_and_blocks_further_model_spend(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    supervisor.config["auto_deploy"] = True
    supervisor.candidate_probe_result = False
    supervisor.smoke_failure_category = "quota"
    value = incident(supervisor)
    original = supervisor.releases.current()
    assert supervisor._repair(value) is True
    assert supervisor.releases.current() == original
    assert json.loads(supervisor.releases.journal.read_text())["state"] == "ROLLED_BACK"
    terminal = supervisor.ledger.history(value["fingerprint"])[-1]
    assert terminal["event"] == "ATTEMPT_BLOCKED"
    assert terminal["details"]["deployment"]["state"] == "ROLLED_BACK"
    assert terminal["details"]["live_smoke"]["failure_category"] == "quota"
    assert supervisor.ledger.get_incident(value["fingerprint"])["status"] == "BLOCKED"
    assert supervisor._repair(value) is False
    assert len(supervisor.runner.repair_calls) == 1 and supervisor.clear_calls == 1


def test_unfinished_deployment_with_failed_recovery_prevents_new_paid_reservation(tmp_path):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    original = supervisor.releases.current()
    candidate = tmp_path / "interrupted-candidate"
    snapshot_tree(Path(original["source_root"]), candidate)
    (candidate / "src" / "cochem_pipeline" / "runtime.py").write_text("# interrupted fixture\n", encoding="utf-8")
    release = supervisor.releases.prepare(candidate, tree_manifest(candidate))
    record = tmp_path / "prepared-fixture.json"
    write_json(record, release)
    # A real child exits during activation after the actual pointer is switched.
    # This is a process-crash contract fixture, never a provider execution.
    program = """import json,os,sys
from pathlib import Path
from cochem_supervisor.releases import ReleaseStore
store=ReleaseStore(Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]))
def crash(source): os._exit(73)
store.deploy(json.loads(Path(sys.argv[4]).read_text()),crash,lambda:True,lambda source:True)
"""
    child = subprocess.run([sys.executable, "-c", program, str(supervisor.releases.root),
                            str(supervisor.releases.pointer), str(supervisor.releases.journal), str(record)],
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 73
    assert supervisor.releases.current() == release
    assert json.loads(supervisor.releases.journal.read_text())["state"] == "VERIFYING"
    supervisor.previous_probe_result = False
    value = incident(supervisor)
    sentinel = Path(supervisor.config["repair_workspace"]) / "preserved-evidence.txt"
    sentinel.write_text("previous repair evidence", encoding="utf-8")
    with pytest.raises(ReleaseError, match="Rollback could not restore and verify"):
        supervisor._repair(value)
    assert supervisor.releases.current() == original
    assert supervisor.releases.recovery_required() is True
    assert json.loads(supervisor.releases.journal.read_text())["state"] == "ROLLBACK_FAILED"
    assert supervisor.ledger.get_incident(value["fingerprint"])["attempts"] == 0
    assert supervisor.runner.repair_calls == [] and supervisor.clear_calls == 0
    assert sentinel.read_text() == "previous repair evidence"
