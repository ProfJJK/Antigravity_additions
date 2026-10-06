"""Real SQLite/heartbeat-file observations without pipeline imports or mocks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cochem_supervisor.monitor import classify_error, read_observation, redact_diagnostic

NOW = 10000


def board(root: Path) -> Path:
    path = root / "job_board.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE pipeline_jobs(job_id TEXT PRIMARY KEY,kind TEXT,status TEXT,attempts INTEGER,
            updated_at REAL,lease_expires_at REAL,error TEXT,payload_json TEXT);
        CREATE TABLE pipeline_events(id INTEGER PRIMARY KEY,event TEXT,timestamp REAL,details_json TEXT);
        INSERT INTO pipeline_events VALUES(1,'CREATED',9990,'{"secret":"EVENT-PAYLOAD-MUST-STAY-PRIVATE"}');
        """)
    return path


def heartbeat(root: Path, *, timestamp=NOW, capacity=4, pid=42, sequence=2, active=0):
    (root / "supervisor_status.json").write_text(json.dumps({
        "instance_id": "physical-test-heartbeat", "pid": pid, "process_started_at": 1,
        "sequence": sequence, "timestamp": timestamp, "version": "4.2.3",
        "status": {"hardware": {"capacity": capacity}, "active_count": active,
                   "token": "HEARTBEAT-SECRET-MUST-NOT-LEAK"},
    }), encoding="utf-8")


def insert(path, *, job_id="job-1", status="FAILED", error="TypeError: invalid scheduler state", attempts=3,
           updated_at=NOW - 5, lease_expires_at=None, kind="CHAPTER_DRAFT"):
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO pipeline_jobs VALUES(?,?,?,?,?,?,?,?)", (
            job_id, kind, status, attempts, updated_at, lease_expires_at, error,
            '{"prompt":"RAW-PROMPT-MUST-STAY-PRIVATE","artifact_text":"PRIVATE-CHAPTER"}',
        ))


def test_healthy_idle_board_is_observed_without_creating_or_modifying_files(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["state"] == "healthy"
    assert result["health"]["database"] == "readable"
    assert result["incidents"] == []
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}


def test_missing_files_are_reported_without_creating_database_or_directories(tmp_path):
    root = tmp_path / "not-yet-installed"
    result = read_observation(root, now=NOW)
    assert result["health"]["heartbeat"] == "missing"
    assert result["health"]["database"] == "missing"
    assert any(item["category"] == "code" and item["repairable"] for item in result["incidents"])
    assert not root.exists()


def test_stale_heartbeat_fingerprint_ignores_pid_sequence_and_timestamps(tmp_path):
    board(tmp_path)
    heartbeat(tmp_path, timestamp=NOW-100, pid=91)
    first = read_observation(tmp_path, now=NOW)["incidents"][0]
    heartbeat(tmp_path, timestamp=NOW-200, pid=142, sequence=9)
    second = read_observation(tmp_path, now=NOW+10)["incidents"][0]
    assert first["fingerprint"] == second["fingerprint"]
    assert first["repairable"] is True
    assert first["evidence"] != second["evidence"]


@pytest.mark.parametrize("message,category,repairable", [
    ("Authentication failed; please log in", "auth", False),
    ("TypeError: subscription login unverified", "auth", False),
    ("HTTP 429 quota exceeded", "quota", False),
    ("Provider overloaded: service unavailable", "provider", False),
    ("ConnectionError: connection refused", "provider", False),
    ("MemoryError: cannot allocate memory", "resource", False),
    ("sqlite3.OperationalError: database is locked", "resource", False),
    ("PermissionError: Access denied", "configuration", False),
    ("unknown option --headless", "compatibility", True),
    ("unrecognized arguments: --json", "compatibility", True),
    ("no such column: job.attempts", "compatibility", True),
    ("AttributeError: Scheduler has no attribute next_job", "code", True),
    ("ModuleNotFoundError: No module named scheduler", "code", True),
    ("Unknown provider response with unspecified cause", "configuration", False),
])
def test_error_classes_never_authorize_model_spend_for_external_blockers(message, category, repairable):
    result = classify_error(message)
    assert result["category"] == category
    assert result["repairable"] is repairable


def test_actual_exception_message_without_class_prefix_still_identifies_code_error():
    try:
        object().missing_scheduler_method()
    except AttributeError as error:
        classification = classify_error(str(error))
    assert classification["category"] == "code"
    assert classification["repairable"] is True


def test_repeated_code_failure_exposes_bounded_safe_metadata_not_prompts(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, error="TypeError: scheduler state invalid password='sensitive password' api_key=sk-01234567890123456789")
    result = read_observation(tmp_path, now=NOW)
    assert len(result["incidents"]) == 1
    incident = result["incidents"][0]
    assert incident["category"] == "code" and incident["repairable"] is True
    assert incident["evidence"]["latest_failure_at"] == NOW-5
    serialized = json.dumps(result)
    for secret in ("sensitive password", "sk-01234567890123456789", "RAW-PROMPT", "PRIVATE-CHAPTER", "EVENT-PAYLOAD", "HEARTBEAT-SECRET"):
        assert secret not in serialized


def test_one_code_failure_does_not_authorize_repair_until_repeated(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, attempts=1)
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["state"] == "blocked"
    assert result["incidents"][0]["repairable"] is False


def test_three_matching_jobs_are_grouped_without_job_identity_in_fingerprint(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    for index in range(3):
        insert(path, job_id=f"chapter-{index}", attempts=1, error=f"TypeError: broken scheduler job_id=chapter-{index} pid={index+91}")
    result = read_observation(tmp_path, now=NOW)
    assert len(result["incidents"]) == 1
    incident = result["incidents"][0]
    assert incident["repairable"] is True
    assert incident["evidence"]["observed_jobs"] == 3


def test_new_failure_changes_evidence_time_without_resetting_fingerprint(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, error="TypeError: invalid state attempt_id=ca8b8c1d6c2748e2bba201c83f4e8b55 pid=100 timestamp=9990")
    original = read_observation(tmp_path, now=NOW)["incidents"][0]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE pipeline_jobs SET updated_at=?,error=?", (NOW, "TypeError: invalid state attempt_id=939f70afc17b4ba9b865fed32f4fba42 pid=200 timestamp=10000"))
    current = read_observation(tmp_path, now=NOW)["incidents"][0]
    assert original["fingerprint"] == current["fingerprint"]
    assert current["evidence"]["latest_failure_at"] > original["evidence"]["latest_failure_at"]


@pytest.mark.parametrize("error,category", [
    ("subscription login unverified", "auth"),
    ("usage limit quota exceeded", "quota"),
    ("provider service unavailable", "provider"),
    ("no space left on device", "resource"),
    ("Unrecognized opaque problem", "configuration"),
])
def test_old_retry_waiting_on_external_blocker_is_not_misclassified_as_code_stall(tmp_path, error, category):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="PENDING_RETRY", error=error, updated_at=1)
    result = read_observation(tmp_path, now=NOW)
    assert {item["category"] for item in result["incidents"]} == {category}
    assert not any(item["repairable"] for item in result["incidents"])


@pytest.mark.parametrize("capacity,active", [(0, 0), (4, 4)])
def test_stalled_pending_work_waiting_for_host_capacity_does_not_spend_on_repair(tmp_path, capacity, active):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=capacity, active=active)
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_legacy_active_list_retains_capacity_hold(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=4, active=4)
    heartbeat_path = tmp_path / "supervisor_status.json"
    data = json.loads(heartbeat_path.read_text())
    data["status"].pop("active_count")
    data["status"]["active"] = [{"pid": value} for value in range(4)]
    heartbeat_path.write_text(json.dumps(data), encoding="utf-8")
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_current_active_count_takes_precedence_over_legacy_list(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path, capacity=4, active=4)
    heartbeat_path = tmp_path / "supervisor_status.json"
    data = json.loads(heartbeat_path.read_text())
    data["status"]["active"] = []
    heartbeat_path.write_text(json.dumps(data), encoding="utf-8")
    insert(path, status="PENDING", error=None, updated_at=1, attempts=0)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"


def routing_wait(path, *, state="WAITING", due=NOW+30, reason="Provider quota limit", job_id="job-1"):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS pipeline_routing_jobs(job_id TEXT PRIMARY KEY,
            state TEXT,next_eligible_at REAL,expires_at REAL,cycle INTEGER,wait_reason TEXT)""")
        conn.execute("INSERT INTO pipeline_routing_jobs VALUES(?,?,?,?,?,?)", (job_id,state,due,None,2,reason))


@pytest.mark.parametrize("status,reason,category", [
    ("PENDING", "Provider busy", "provider"),
    ("PENDING_RETRY", "Provider quota limit", "quota"),
    ("PENDING", "Model backlog capacity limit", "provider"),
])
def test_planned_routing_wait_is_visible_without_authorizing_paid_code_repair(tmp_path, status, reason, category):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status=status, error="TypeError: old failed attempt", updated_at=1)
    routing_wait(path, reason=reason)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"] == []
    assert result["health"]["state"] == "waiting"
    assert result["health"]["routing_wait_count"] == 1
    assert result["health"]["routing_waits"] == [{"kind":"CHAPTER_DRAFT", "state":"WAITING",
        "category":category, "next_eligible_at":NOW+30, "retry_in_seconds":30, "cycle":2}]
    assert "RAW-PROMPT" not in json.dumps(result)


def test_just_due_queue_gets_progress_grace_then_abandoned_wait_is_detected(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="PENDING", error=None, attempts=0, updated_at=1)
    routing_wait(path, due=NOW-5)
    assert read_observation(tmp_path, now=NOW)["incidents"] == []
    heartbeat(tmp_path, timestamp=NOW+700)
    result = read_observation(tmp_path, now=NOW+700)
    assert result["incidents"][0]["category"] == "code"
    assert result["incidents"][0]["repairable"] is True


def test_operator_routing_limit_is_blocked_without_code_repair(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="BLOCKED", error=None, updated_at=1)
    routing_wait(path, state="BLOCKED", due=None)
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["state"] == "blocked"
    assert result["incidents"][0]["category"] == "configuration"
    assert result["incidents"][0]["repairable"] is False


def test_planned_wait_cannot_hide_an_expired_active_process_lease(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="IN_PROGRESS", error=None, lease_expires_at=NOW-10)
    routing_wait(path)
    result = read_observation(tmp_path, now=NOW)
    assert "lease expired" in result["incidents"][0]["summary"]


def cleanup_guard(path, *, quarantined=0, cleared_at=None):
    """Physical SQLite contract rows; no claim of a native process launch."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            ALTER TABLE pipeline_jobs ADD COLUMN attempt_id TEXT;
            ALTER TABLE pipeline_jobs ADD COLUMN fencing_token INTEGER;
            CREATE TABLE pipeline_execution_cleanup(job_id TEXT,attempt_id TEXT,fencing_token INTEGER,
                worker_slot TEXT,pid INTEGER,reason TEXT,quarantined INTEGER,created_at REAL,cleared_at REAL);
            UPDATE pipeline_jobs SET attempt_id='PRIVATE-ATTEMPT-AUTHORITY',fencing_token=5;
        """)
        conn.execute("INSERT INTO pipeline_execution_cleanup VALUES(?,?,?,?,?,?,?,?,?)",
            ('job-1','PRIVATE-ATTEMPT-AUTHORITY',5,'PRIVATE-WORKER',123,
             'SECRET-CLEANUP-REASON prompt=PRIVATE-CONTENT',quarantined,NOW-10,cleared_at))


@pytest.mark.parametrize('status,lease', [('PENDING',None),('PENDING_RETRY',None),('IN_PROGRESS',NOW-1)])
def test_unconfirmed_cleanup_blocks_paid_job_repairs_and_exposes_only_bounded_metadata(tmp_path,status,lease):
    path=board(tmp_path)
    heartbeat(tmp_path)
    insert(path,status=status,updated_at=1,lease_expires_at=lease)
    cleanup_guard(path)
    observed=read_observation(tmp_path,now=NOW)
    assert observed['health']['state']=='blocked'
    assert observed['health']['execution_cleanup_hold']=={
        'state':'BLOCKED','sampled_holds':1,'sample_truncated':False,'oldest_hold_age_seconds':10}
    assert any(item['category']=='configuration' and 'cleanup' in item['summary'] for item in observed['incidents'])
    assert not any(item['repairable'] for item in observed['incidents'])
    serialized=json.dumps(observed)
    for private in ('PRIVATE-ATTEMPT','PRIVATE-WORKER','SECRET-CLEANUP','PRIVATE-CONTENT','RAW-PROMPT'):
        assert private not in serialized


def test_normal_owned_live_execution_guard_does_not_create_cleanup_hold(tmp_path):
    path=board(tmp_path)
    heartbeat(tmp_path)
    insert(path,status='IN_PROGRESS',error=None,lease_expires_at=NOW+60)
    cleanup_guard(path)
    observed=read_observation(tmp_path,now=NOW)
    assert observed['health']['execution_cleanup_hold'] is None
    assert observed['health']['state']=='healthy'
    assert observed['incidents']==[]


@pytest.mark.parametrize('field,value',[('attempt_id','different-private-attempt'),('fencing_token',6)])
def test_unrelated_current_lease_cannot_hide_prior_execution_cleanup(tmp_path,field,value):
    path=board(tmp_path)
    heartbeat(tmp_path)
    insert(path,status='IN_PROGRESS',error=None,lease_expires_at=NOW+60)
    cleanup_guard(path)
    with sqlite3.connect(path) as conn:
        conn.execute(f'UPDATE pipeline_jobs SET {field}=?',(value,))
    observed=read_observation(tmp_path,now=NOW)
    assert observed['health']['execution_cleanup_hold']['sampled_holds']==1
    assert not any(incident['repairable'] for incident in observed['incidents'])


def test_explicit_quarantine_blocks_even_a_current_lease_then_verified_clear_releases_monitor(tmp_path):
    path=board(tmp_path)
    heartbeat(tmp_path)
    insert(path,status='IN_PROGRESS',error=None,lease_expires_at=NOW+60)
    cleanup_guard(path,quarantined=1)
    assert read_observation(tmp_path,now=NOW)['health']['state']=='blocked'
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE pipeline_execution_cleanup SET cleared_at=?',(NOW,))
    observed=read_observation(tmp_path,now=NOW)
    assert observed['health']['execution_cleanup_hold'] is None
    assert observed['incidents']==[]


def test_cleanup_hold_preserves_stale_heartbeat_restart_evidence(tmp_path):
    path=board(tmp_path)
    heartbeat(tmp_path,timestamp=NOW-100)
    insert(path,status='PENDING',updated_at=1)
    cleanup_guard(path,quarantined=1)
    observed=read_observation(tmp_path,now=NOW)
    repairable=[incident for incident in observed['incidents'] if incident['repairable']]
    assert len(repairable)==1 and 'heartbeat is stale' in repairable[0]['summary']
    assert observed['health']['execution_cleanup_hold']['state']=='BLOCKED'


def test_cleanup_hold_sampling_is_bounded(tmp_path):
    path=board(tmp_path)
    heartbeat(tmp_path)
    insert(path,status='PENDING',error=None,updated_at=1)
    cleanup_guard(path,quarantined=1)
    with sqlite3.connect(path) as conn:
        conn.executemany('INSERT INTO pipeline_execution_cleanup(job_id,quarantined,created_at) VALUES(?,1,?)',
                         [(f'old-{index}',NOW-20) for index in range(300)])
    observed=read_observation(tmp_path,now=NOW)
    assert observed['health']['execution_cleanup_hold']['sampled_holds']==256
    assert observed['health']['execution_cleanup_hold']['sample_truncated'] is True
    assert len(observed['incidents'])==1


def test_expired_execution_is_detected_from_real_database_lease_metadata(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    insert(path, status="IN_PROGRESS", error=None, lease_expires_at=NOW-40)
    result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "code"
    assert "lease expired" in result["incidents"][0]["summary"]
    assert result["incidents"][0]["evidence"]["expired_seconds"] == 40


def test_missing_schema_is_a_compatibility_incident(tmp_path):
    with sqlite3.connect(tmp_path / "job_board.db") as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    heartbeat(tmp_path)
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["database"] == "unreadable"
    assert result["incidents"][0]["category"] == "compatibility"


def test_real_sqlite_write_lock_is_a_resource_hold(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        result = read_observation(tmp_path, now=NOW)
    assert result["incidents"][0]["category"] == "resource"
    assert result["incidents"][0]["repairable"] is False


def test_wal_committed_failure_is_seen_without_immutable_stale_reads(tmp_path):
    path = board(tmp_path)
    heartbeat(tmp_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO pipeline_jobs VALUES('wal-job','CHAPTER_DRAFT','FAILED',3,9999,NULL,'TypeError: bad scheduler','{}')")
        conn.commit()
        assert Path(str(path)+"-wal").is_file()
        result = read_observation(tmp_path, now=NOW)
        assert result["incidents"][0]["repairable"] is True
        assert result["health"]["job_counts"] == {"FAILED": 1}
    finally:
        conn.close()


@pytest.mark.parametrize("content", ["{", "[]", '{"timestamp":10000}', "x"*65537])
def test_malformed_heartbeat_never_claims_healthy(tmp_path, content):
    board(tmp_path)
    (tmp_path / "supervisor_status.json").write_text(content, encoding="utf-8")
    result = read_observation(tmp_path, now=NOW)
    assert result["health"]["heartbeat"] == "invalid"
    assert result["health"]["state"] != "healthy"


def test_diagnostic_redaction_covers_authorization_urls_and_payload_echoes():
    diagnostic = redact_diagnostic('TypeError: Authorization: Bearer bearer-secret token="token secret" '
                                   'https://name:password@example.com/?api_key=url-secret '
                                   'prompt="private prompt" payload={"x":3}')
    for secret in ("bearer-secret", "token secret", "name:password", "url-secret", "private prompt"):
        assert secret not in diagnostic
    assert "TypeError" in diagnostic


def test_payload_echoes_cannot_authorize_code_repair_or_expose_nested_request_data():
    error = 'Unexpected failure payload={"details":"ordinary", "next":"TypeError: secret request text"}'
    classification = classify_error(error)
    assert classification["repairable"] is False
    assert "secret request text" not in classification["diagnostic"]


@pytest.mark.parametrize("settings", [{"heartbeat_timeout": 0}, {"stall_timeout": -1}, {"now": float("nan")}, {"repeated_failures": True}])
def test_invalid_monitor_limits_are_rejected(tmp_path, settings):
    with pytest.raises(ValueError):
        read_observation(tmp_path, **settings)


def set_heartbeat_status(root, **extra):
    path=root/'supervisor_status.json'
    data=json.loads(path.read_text())
    data['status'].update(extra)
    path.write_text(json.dumps(data),encoding='utf-8')


@pytest.mark.parametrize('state,reason',[
    ('paused','Required CPU temperature telemetry is unavailable'),
    ('critical','Windows commit headroom is below its critical reserve'),
])
def test_explicit_governor_hold_blocks_historical_code_repairs_and_queued_stalls(tmp_path,state,reason):
    path=board(tmp_path); heartbeat(tmp_path)
    insert(path,status='FAILED')
    insert(path,job_id='pending',status='PENDING',updated_at=1,error=None)
    set_heartbeat_status(tmp_path,hardware={'state':state,'capacity':0,
        'reasons':[reason,'password=PRIVATE-SENSOR-SECRET'],
        'measurements':{'private':'UNPROJECTED-SENSOR-DATA'}})
    result=read_observation(tmp_path,now=NOW)
    assert result['health']['repair_hold'] is True
    assert result['health']['hardware']['state']==state
    assert not any(item['repairable'] for item in result['incidents'])
    assert any(item['evidence'].get('component')=='hardware' for item in result['incidents'])
    assert 'PRIVATE-SENSOR-SECRET' not in json.dumps(result)
    assert 'UNPROJECTED-SENSOR-DATA' not in json.dumps(result)


@pytest.mark.parametrize('state,checked',[('unavailable',NOW),('healthy',NOW-100),('quarantined',NOW)])
def test_required_engine_or_container_failure_is_infrastructure_not_paid_code_repair(tmp_path,state,checked):
    path=board(tmp_path); heartbeat(tmp_path)
    insert(path,status='FAILED')
    component='containers' if state=='quarantined' else 'docker_engine'
    set_heartbeat_status(tmp_path,components={component:{'required':True,'state':state,'checked_at':checked,
        'diagnostic':'TypeError: infrastructure unavailable token=DOCKER-SECRET','containers':['PRIVATE-LEASE']}})
    result=read_observation(tmp_path,now=NOW)
    assert result['health']['repair_hold'] is True
    assert not any(item['repairable'] for item in result['incidents'])
    safe=result['health']['components'][component]
    assert safe['state']==('unknown' if checked<NOW-30 else state)
    assert 'PRIVATE-LEASE' not in json.dumps(result) and 'DOCKER-SECRET' not in json.dumps(result)


def test_actual_oversized_sqlite_wal_blocks_repairs_without_reading_or_checkpointing_payload(tmp_path):
    path=board(tmp_path); heartbeat(tmp_path)
    insert(path,status='FAILED')
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute('CREATE TABLE private_pressure(data BLOB)')
        conn.execute('INSERT INTO private_pressure VALUES(zeroblob(2097152))')
        conn.commit()
        wal=Path(str(path)+'-wal')
        before=wal.stat().st_size
        result=read_observation(tmp_path,now=NOW,wal_limit_mb=1)
        assert wal.stat().st_size==before
        assert result['health']['database_wal']=={'state':'oversized','size_bytes':before,'limit_bytes':1048576}
        assert result['health']['repair_hold'] is True
        assert not any(item['repairable'] for item in result['incidents'])
        assert any(item['evidence'].get('component')=='database_wal' for item in result['incidents'])


def test_completion_progress_is_distinct_from_scheduler_events_and_planned_quota_delay(tmp_path):
    path=board(tmp_path); heartbeat(tmp_path)
    insert(path,job_id='completed',status='COMPLETED',updated_at=NOW-200,error=None)
    insert(path,status='PENDING_RETRY',updated_at=1,error='Provider quota limit')
    routing_wait(path)
    result=read_observation(tmp_path,now=NOW)
    assert result['health']['last_completion_at']==NOW-200
    assert result['health']['last_completion_age_seconds']==200
    assert result['health']['last_event_age_seconds']==10
    assert result['health']['state']=='waiting' and result['incidents']==[]


@pytest.mark.parametrize('limit',[0,True,4097,1.5])
def test_wal_limit_requires_a_bounded_integer(tmp_path,limit):
    with pytest.raises(ValueError,match='wal_limit_mb'):
        read_observation(tmp_path,wal_limit_mb=limit)
