"""Supervisor composition with real SQLite/files and an explicit local test driver.

The driver launches Python subprocesses that write protocol fixtures. It does
not invoke a model, native Windows identity, ACL, or Scheduled Task. Only those
platform/driver boundaries are replaced; engine decisions, receipts, snapshots,
release journals, and durable budgets are exercised directly without mocks.
"""
from __future__ import annotations

import json
import hashlib
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
                "stdout_sha256":hashlib.sha256(stdout.read_bytes()).hexdigest(),
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
        from cochem_supervisor.repair_artifacts import source_packet
        if 'review_manifest' in evidence:
            from cochem_supervisor.reconciliation import review_output_contract, digest
            output=review_output_contract(evidence['review_manifest'])
            if getattr(self,'review_verdict','PASS')=='FAIL':
                output.update(verdict='FAIL',findings=['Fixture reviewer found concrete source divergence from the SRS.'])
            process=self._process("print('controlled review fixture; no model inference')",[],log_dir,heartbeat)
            return {**process,'provider':spec['provider'],'requested_model':spec['model'],
                'requested_effort':spec.get('reasoning_effort'),'subscription_verified':True,
                'terminal_success':True,'session_id':'synthetic-review-contract-fixture',
                'output_sha256':digest(output),'review_output_sha256':digest(output),'review_output':output,
                'route_reservation_sha256':evidence['route_reservation']['reservation_sha256']}
        packet=source_packet(candidate,evidence['allowed_paths'],evidence)
        process = self._process("print('controlled proposal fixture; no model inference')", [], log_dir, heartbeat)
        return {**process, "provider":spec["provider"], "requested_model":spec["model"],
            "requested_effort":spec.get("reasoning_effort"), "subscription_verified":True,
            "terminal_success":True, "session_id":"local-protocol-fixture-not-native",
            "output_sha256":"a"*64, "source_packet":packet,
            "proposal":{"summary":"controlled fixture change","files":{"src/cochem_pipeline/runtime.py":"# controlled fixture change\n"}},
            "route_reservation_sha256":evidence["route_reservation"]["reservation_sha256"]}

    def preflight_selected_spec(self, spec, identity, workspace, log_dir, heartbeat):
        """Explicit offline native boundary fixture; no model or Windows claim."""
        assert heartbeat() is True
        return {'provider':spec['provider'], 'model':spec['model'],
                'requested_effort':spec.get('reasoning_effort'), 'paid_inference':False,
                'mode':'offline-capability', 'test_driver':'local protocol fixture'}

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
            "baseline_source":str(Path(__file__).resolve().parents[1]),
            "private_root": str(self.private), "release_root": str(releases), "pointer_file": str(self.private / "current.json"),
            "repair_workspace": str(workspace), "acceptance_root": str(acceptance), "pipeline_private_root": str(pipeline),
            "repair_worker": {"name": "RepairAccount", "credential_target": "test-fixture-only"},
            "providers": [{"provider": "codex", "model": "gpt-6-astra", "executable": sys.executable}],
            "allowed_paths": ["src/cochem_pipeline/"], "test_python": sys.executable,
            "test_targets": ["pipeline_tests"], "max_per_incident": 2, "max_per_day": 4,
            "cooldown_seconds": 0, "repair_timeout_seconds": 10, "test_timeout_seconds": 10,
            "minimum_passed_tests": 1, "maximum_skipped_tests": 0, "auto_deploy": False,
            "pipeline_routing":{"backoff_base_seconds":.001,"backoff_max_seconds":.001,"backoff_jitter_fraction":0},
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

    def _read_observation(self):
        # Detector process/import isolation is separately tested with a real
        # subprocess; this driver composes the actual readonly observer/ledger.
        return read_observation(self.config['pipeline_private_root'],
            heartbeat_timeout=self.config['heartbeat_timeout'],stall_timeout=self.config['stall_timeout'],
            repeated_failures=self.config['repeated_failures'],wal_limit_mb=self.config.get('wal_limit_mb',256))

    def _protect_release_tree(self, path):
        self.protected.append(Path(path))

    def _prepare_workspace(self, candidate):
        # Native repair-account ACL provisioning is outside this local fixture.
        assert Path(candidate).is_dir()

    def _repair_boundary_check(self):
        # This fixture never provisions or impersonates a native account.
        # Real Windows Docker pipe denial is an opt-in platform test.
        return {'enabled':False,'test_driver':'local protocol fixture; no Windows Docker assertion'}

    def _reconcile_repair(self,*args,**kwargs):
        # Enable the independently routed second provider in this local protocol
        # fixture; the actual production method and durable budgets run unchanged.
        if not any(spec['provider']=='claude' for spec in self.config['providers']):
            self.config['providers'].append({'provider':'claude','model':'claude-fable-5-1','executable':sys.executable})
        contracts=json.loads((self.private/'cli-contracts.json').read_text())
        contracts['providers']['claude']={'available':True,'missing_flags':[],'exit_code':0,'help_exit_code':0}
        write_json(self.private/'cli-contracts.json',contracts)
        return super()._reconcile_repair(*args,**kwargs)

    def _outer_acceptance(self,candidate,directory):
        # Real candidate subprocess/SQLite checks have their own physical suite.
        return {'passed':True,'test_driver':'local engine composition fixture; no independent behavior assertion'}

    def _quiesce_for_repair(self):
        # Fixture has no Windows daemon/containers; native/Docker proofs have
        # separate physical suites. No production ownership check is bypassed.
        return self._stop()

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
    assert supervisor.runner.repair_calls == ["claude", "claude"] and supervisor.clear_calls == 2
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
    assert supervisor.lifecycle == ["stop", ("start", Path(original["source_root"]))]
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


def test_restart_persists_private_bounded_diagnostics_before_request_event(tmp_path):
    supervisor=LocalSupervisor(tmp_path)
    value=incident(supervisor,heartbeat=True)
    assert supervisor._restart_once(value)
    history=supervisor.ledger.history(value['fingerprint'])
    diagnostics=next(item for item in history if item['event']=='PRE_RECOVERY_DIAGNOSTICS')
    restart=next(item for item in history if item['event']=='WARDEN_RESTART_REQUESTED')
    assert diagnostics['id']<restart['id']
    path=Path(diagnostics['details']['path'])
    assert supervisor.private in path.parents and path.is_file()
    report=json.loads(path.read_text())
    assert report['observation']['health']['database']=='readable'
    snapshot=report['bundle']['private_database_snapshot']
    assert snapshot['state']=='captured' and snapshot['consistent_sqlite_backup']
    assert snapshot['contains_private_payloads'] is True
    assert (path.with_suffix('')/'database-private.db').is_file()
    assert report['bundle']['safe_projection_omits']==[
        'prompts','payloads','artifacts','authentication','source lines','locals']


def test_failed_restart_health_probe_is_recorded_without_claiming_recovery(tmp_path):
    supervisor=LocalSupervisor(tmp_path)
    supervisor.previous_probe_result=False
    value=incident(supervisor,heartbeat=True)
    assert supervisor._restart_once(value)
    history=supervisor.ledger.history(value['fingerprint'])
    finished=next(item for item in history if item['event']=='WARDEN_RESTART_FINISHED')
    assert finished['details']['health_verified'] is False
    assert supervisor.ledger.has_event('WARDEN_RESTART_FAILED',fingerprint=value['fingerprint'])


def failed_restart_fixture(tmp_path,error='TypeError: broken scheduler state'):
    from cochem_supervisor.component_recovery import RecoveryLedger
    supervisor=LocalSupervisor(tmp_path)
    supervisor.previous_probe_result=False
    root=Path(supervisor.config['pipeline_private_root'])
    store=JobStore(root/'job_board.db',max_attempts=1)
    store.submit('Real SQLite failure evidence',['REQ-1'],1)
    job=store.claim('fixture-owner')
    store.fail(job['job_id'],job['attempt_id'],job['fencing_token'],error)
    path=root/'supervisor_status.json'
    data=json.loads(path.read_text())
    data.update(timestamp=time.time()-60,process_started_at=time.time()-120)
    write_json(path,data)
    ledger=RecoveryLedger(supervisor.private/'component-recovery.db')
    ledger.observe('warden',False,now=time.time()-40)
    ledger.observe('warden',False,now=time.time()-30)
    return supervisor


def test_failed_restart_escalates_evidenced_code_only_after_verified_cleanup(tmp_path):
    supervisor=failed_restart_fixture(tmp_path)
    supervisor.tick(ignore_startup_grace=True)
    assert not supervisor.runner.repair_calls
    observed=supervisor.tick(ignore_startup_grace=True)
    assert not observed['health']['repair_hold']
    assert supervisor.runner.repair_calls==['codex']
    assert supervisor.lifecycle.count('stop')==3  # restart, escalation cleanup, failover-board ownership
    events=supervisor.ledger.history()
    cleanup=next(item['id'] for item in events if item['event']=='WARDEN_REPAIR_CLEANUP_VERIFIED')
    reserved=next(item['id'] for item in events if item['event']=='ATTEMPT_RESERVED')
    assert cleanup<reserved
    assert 'TypeError:' in supervisor.runner.repair_evidence[0]['incident']['evidence']['diagnostic']
    # Restoring the durable objects cannot increase the original repair budget.
    supervisor.ledger=Ledger(supervisor.ledger.path)
    supervisor.tick(ignore_startup_grace=True)
    time.sleep(.01)
    supervisor.tick(ignore_startup_grace=True)
    assert supervisor.runner.repair_calls==['codex','codex']


@pytest.mark.parametrize('error',['HTTP 429 quota exceeded','PermissionError: access denied',
    'ConnectionError: connection refused','MemoryError: cannot allocate memory','Unknown failure'])
def test_failed_restart_with_environmental_evidence_never_authorizes_model(tmp_path,error):
    supervisor=failed_restart_fixture(tmp_path,error)
    for _ in range(3): supervisor.tick(ignore_startup_grace=True)
    assert not supervisor.runner.repair_calls
    assert supervisor.lifecycle.count('stop')==1


def test_failed_restart_cannot_escalate_when_contained_stop_fails(tmp_path):
    supervisor=failed_restart_fixture(tmp_path)
    supervisor.tick(ignore_startup_grace=True)
    def cannot_stop():
        raise RuntimeError('Fixture: retained process tree is not empty')
    supervisor._stop=cannot_stop
    observed=supervisor.tick(ignore_startup_grace=True)
    assert observed['health']['repair_hold']
    assert not supervisor.runner.repair_calls
    assert not any(item['event']=='ATTEMPT_RESERVED' for item in supervisor.ledger.history())


def test_structured_startup_crash_can_repair_before_database_creation(tmp_path):
    from cochem_pipeline.crash import record_crash
    supervisor=failed_restart_fixture(tmp_path)
    root=Path(supervisor.config['pipeline_private_root'])
    (root/'job_board.db').unlink()
    try:
        object().missing_startup_method()
    except AttributeError as exc:
        record_crash(root,exc)
    supervisor.tick(ignore_startup_grace=True)
    supervisor.tick(ignore_startup_grace=True)
    assert supervisor.runner.repair_calls==['codex']
    evidence=supervisor.runner.repair_evidence[0]['incident']['evidence']
    assert evidence['source']=='crash-envelope.json' and evidence['frames']


def test_model_generated_regression_pass_cannot_bypass_outer_gate(tmp_path):
    supervisor=LocalSupervisor(tmp_path,LocalProtocolDriver(acceptance='passed'))
    supervisor.config['auto_deploy']=True
    def rejected(candidate,directory):
        raise ValueError('Physical external ownership assertion failed')
    supervisor._outer_acceptance=rejected
    value=incident(supervisor)
    previous=supervisor.releases.current()
    supervisor._repair(value)
    assert supervisor.releases.current()==previous
    assert supervisor.ledger.get_incident(value['fingerprint'])['status']=='OPEN'
    assert supervisor.lifecycle == ["stop", ("start", Path(supervisor.releases.current()["source_root"]))]


@pytest.mark.parametrize('http_status',[401,503])
def test_real_http_controller_outage_never_spends_on_model_repair(tmp_path,http_status):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from cochem_supervisor.probes import ControllerClient
    from cochem_supervisor.component_recovery import RecoveryLedger
    supervisor=LocalSupervisor(tmp_path)
    secret='A'*48
    token_file=supervisor.private/'http-token'; token_file.write_text(secret)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get('Authorization')=='Bearer '+secret
            self.send_response(http_status); self.end_headers()
            self.wfile.write(b'PRIVATE-CONTROLLER-ERROR-BODY token=DO-NOT-LOG')
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    supervisor.client=ControllerClient(server.server_port,token_file,timeout=1)
    if http_status==503:
        ledger=RecoveryLedger(supervisor.private/'component-recovery.db')
        ledger.observe('warden',False,now=time.time()-40)
        ledger.observe('warden',False,now=time.time()-30)
    try:
        observed=supervisor.tick(ignore_startup_grace=True)
        supervisor.tick(ignore_startup_grace=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert observed['health']['repair_hold'] is True
    assert not supervisor.runner.repair_calls
    assert len(supervisor.lifecycle)==(3 if http_status==503 else 0)
    for diagnostic in (supervisor.private/'diagnostics').glob('*.json'):
        assert 'PRIVATE-CONTROLLER-ERROR-BODY' not in diagnostic.read_text()
        assert 'DO-NOT-LOG' not in diagnostic.read_text()


def test_live_hardware_critical_hold_prevents_repair_and_cli_version_processes(tmp_path):
    supervisor=LocalSupervisor(tmp_path)
    pipeline=Path(supervisor.config['pipeline_private_root'])
    Heartbeat(pipeline,'4.2.5').completed_tick({'hardware':{
        'capacity':0,'state':'critical','reasons':['CPU temperature exceeds critical threshold']},'active_count':0})
    # An old diagnostic that independently qualifies as code repair must not
    # defeat the current measured infrastructure hold.
    store=JobStore(pipeline/'job_board.db',max_attempts=1)
    store.submit('Physical resource-hold fixture',['REQ-1'],1)
    job=store.claim('fixture-owner')
    store.fail(job['job_id'],job['attempt_id'],job['fencing_token'],'TypeError: fixture failure')
    observed=supervisor.tick(ignore_startup_grace=True)
    assert observed['health']['repair_hold']
    assert not supervisor.runner.repair_calls and not supervisor.runner.process_calls
    assert not supervisor.lifecycle


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
    assert len(supervisor.runner.repair_calls) == 2


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
    assert supervisor.lifecycle == ["stop", "stop", ("start", active_source), ("probe", active_source)]
    assert supervisor.ledger.get_incident(value["fingerprint"])["status"] == "RESOLVED"
    terminal = supervisor.ledger.history(value["fingerprint"])[-1]
    assert terminal["event"] == "ATTEMPT_SUCCEEDED"
    assert terminal["details"]["deployed"] is True
    assert terminal["details"]["live_smoke"]["passed"] is True
    assert supervisor._repair(value) is False
    assert len(supervisor.runner.repair_calls) == 2
    assert supervisor.ledger.get_incident(value["fingerprint"])["model_calls"] == 2


@pytest.mark.parametrize("failure_category", [None, "code"])
def test_failed_candidate_health_or_code_smoke_restores_pointer_and_allows_bounded_retry(tmp_path, failure_category):
    supervisor = LocalSupervisor(tmp_path, LocalProtocolDriver(acceptance="passed"))
    supervisor.config["auto_deploy"] = True
    supervisor.candidate_probe_result = False
    supervisor.smoke_failure_category = failure_category
    supervisor.config["max_per_incident"]=4  # Explicit fixture budget for two generation/review pairs.
    value = incident(supervisor)
    original = supervisor.releases.current()
    assert supervisor._repair(value) is True
    assert supervisor.releases.current() == original
    journal = json.loads(supervisor.releases.journal.read_text())
    assert journal["state"] == "ROLLED_BACK"
    candidate, previous = Path(journal["candidate"]["source_root"]), Path(original["source_root"])
    assert supervisor.lifecycle == ["stop", "stop", ("start", candidate), ("probe", candidate),
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
    previous_failure = supervisor.runner.repair_evidence[2]["previous_failure"]
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
    assert len(supervisor.runner.repair_calls) == 2 and supervisor.clear_calls == 1


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


@pytest.mark.parametrize('blocker',[None,'Authentication failed','MemoryError','Provider unavailable'])
def test_actual_structural_corruption_freezes_before_repair_and_honors_environment(tmp_path,blocker):
    supervisor=LocalSupervisor(tmp_path)
    store=JobStore(Path(supervisor.config['pipeline_private_root'])/'job_board.db')
    workflow=store.submit('fixture private objective',['REQ-1'],1)
    job=next(item for item in workflow['jobs'] if item['kind']=='MANIFEST_GENERATOR')
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET status=?,lease_owner='illegal-owner',error=?,attempts=3 WHERE job_id=?",
                           ('PENDING' if blocker is None else 'FAILED',blocker,job['job_id']))
    observation=supervisor.tick()
    structural=next(item for item in observation['incidents'] if item['evidence'].get('component')=='database_structure')
    assert observation['health']['state']=='collapsed'
    assert supervisor.lifecycle[0]=='stop'
    assert supervisor.ledger.has_event('STRUCTURAL_FREEZE_VERIFIED',fingerprint=structural['fingerprint'])
    assert list((supervisor.private/'diagnostics').glob('*/database-private.db'))
    assert len(supervisor.runner.repair_calls)==(1 if blocker is None else 0)
    assert store.get(job['job_id'])['lease_owner']=='illegal-owner'


def test_structural_stop_failure_never_authorizes_repair(tmp_path):
    class FailedContainment(LocalSupervisor):
        def _stop(self):
            self.lifecycle.append('failed-stop')
            return False
    supervisor=FailedContainment(tmp_path)
    store=JobStore(Path(supervisor.config['pipeline_private_root'])/'job_board.db')
    workflow=store.submit('fixture',['REQ-1'],1)
    job=next(item for item in workflow['jobs'] if item['kind']=='MANIFEST_GENERATOR')
    with store._write() as connection:
        connection.execute("UPDATE pipeline_jobs SET lease_owner='illegal-owner' WHERE job_id=?",(job['job_id'],))
    observed=supervisor.tick()
    assert observed['health']['repair_hold'] and not supervisor.runner.repair_calls
    assert supervisor.lifecycle==['failed-stop']


def test_production_detector_launcher_reads_actual_board_in_sterile_subprocess(tmp_path):
    supervisor=LocalSupervisor(tmp_path)
    observed=Supervisor._read_observation(supervisor)
    assert observed['health']['database']=='readable'
    assert observed['health']['structural_integrity']['state']=='healthy'
    assert observed['health']['process_resources']['state']=='observed'
    assert (supervisor.private/'process-history.json').is_file()


def test_asymmetric_srs_rejection_prevents_promotion_and_spends_both_original_calls(tmp_path):
    driver=LocalProtocolDriver(acceptance='passed')
    driver.review_verdict='FAIL'
    supervisor=LocalSupervisor(tmp_path,driver)
    supervisor.config['auto_deploy']=True
    value=incident(supervisor)
    before=supervisor.releases.current()
    assert supervisor._repair(value) is True
    assert supervisor.releases.current()==before
    assert supervisor.runner.repair_calls==['codex','claude']
    state=supervisor.ledger.get_incident(value['fingerprint'])
    assert state['model_calls']==2 and state['status']=='EXHAUSTED'
    final=supervisor.ledger.history(value['fingerprint'])[-1]
    assert final['details']['reconciliation']['validation']['approved'] is False
    assert supervisor._repair(value) is False
