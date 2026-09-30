<<<FILE: tests/test_ch08_watchdog_sre.py>>>
# [MC-SRE-06] E2E:test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation
# SPEC: Append after the previous chunk without editing earlier lines. Write the section 10 end-to-end
# SPEC: test def test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation(tmp_path) with
# SPEC: exactly this name. It is the traceability test for SRS-412-08-FR-001 and also exercises
# SPEC: SRS-412-08-FR-003, SRS-412-08-FR-004, FAILURE:False Positive Collapse, SRS-412-08-FR-007,
# SPEC: SRS-412-08-FR-008, NFR-SRE-02. Use real processes, a real SQLite file and psutil; no mock
# SPEC: objects and no skip/xfail markers. It fails until the implementation nodes land. Build a real
# SPEC: job_board.db in WAL mode under tmp_path with the jobs columns the watchdog reads (task_id,
# SPEC: status, lease_owner, lease_expires_at, attempts, max_attempts) and seed 20 PENDING jobs. Launch
# SPEC: 10 real Python worker subprocesses; each claims one job inside BEGIN IMMEDIATE, sets lease_owner
# SPEC: to '<hostname>:<pid>' (the format parse_lease_owner_pid reads) with an 1800 s lease, then
# SPEC: blocks. Assert 10 claims on 10 distinct task_ids (zero duplicate leases). Kill worker 5 with
# SPEC: psutil (a real process death) and backdate its lease so now exceeds lease_expires_at by more
# SPEC: than 60 s (stalled 1860 s or more). Backdate one live worker's lease by only 30 s to prove the
# SPEC: grace buffer. Hash the db and -wal files (sha256 plus mtime). Run watchdog_sre.py BY FILE PATH
# SPEC: as a subprocess under 'python -X importtime' with --db, --evidence-dir, --worker-state-dir,
# SPEC: --once and --claude-exe pointing at a stand-in Python recovery script written in tmp_path (never
# SPEC: the real claude.exe). Assert FR-001 sterility from the importtime stderr: every top-level module
# SPEC: loaded is in sys.stdlib_module_names or is psutil (no cochem.*). Assert the db and -wal hashes
# SPEC: and mtimes are unchanged (read-only, NFR-SRE-02). Assert the Evidence Bundle JSON has verdict
# SPEC: COLLAPSED, a Matrix 1 failure naming the killed PID, a Matrix 2 failure naming the zombie
# SPEC: task_id, stalled_task_id equal to that task, stalled_duration_sec >= 1860, and no failure naming
# SPEC: the grace-window task. Assert the stand-in received the bundle path in argv. The stand-in,
# SPEC: acting as the out-of-band recovery agent (the watchdog itself never writes), reclaims the
# SPEC: zombie: status PENDING, lease_owner NULL, attempts + 1. A surviving worker then claims and
# SPEC: completes it: final status COMPLETED, attempts == 1, 20 jobs in total. After every worker has
# SPEC: completed its job and exited, a second watchdog --once --no-recovery pass writes no new
# SPEC: COLLAPSED bundle. Terminate all worker processes in a finally block.
# tests/test_ch08_watchdog_sre.py:279 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:280 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:281 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:282 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:283 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:284 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:285 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:286 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:287 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:288 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:289 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:290 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:291 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:292 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:293 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:294 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:295 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:296 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:297 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:298 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:299 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:300 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:301 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:302 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:303 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:304 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:305 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:306 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:307 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:308 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:309 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:310 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:311 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:312 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:313 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:314 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:315 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:316 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:317 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:318 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:319 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:320 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:321 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:322 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:323 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:324 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:325 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:326 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:327 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:328 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:329 [MC-SRE-06]
# tests/test_ch08_watchdog_sre.py:330 [MC-SRE-06]
<<<END FILE>>>
