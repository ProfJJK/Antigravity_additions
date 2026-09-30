"""Fracture SRS chapter 8 (Autonomous SRE Watchdog Daemon) into N=1 WBS leaf nodes.

Every functional/non-functional requirement, interface function, JSON data model, failure mode
and test obligation is parsed out of wiki/srs/ch08_watchdog_sre.md at run time. PLACEMENT only
says where each parsed item lives in the source tree; the generator refuses to run if the
chapter contains an item PLACEMENT does not cover, or if PLACEMENT names an item the chapter no
longer contains.

The pipeline is a general software system (task queue, orchestrator, daemons), not a chemistry
calculation: BANNED_TERMS fails the run if any node mentions a chemistry library or method.

Rule 18 (Whole-File Rewrite Ban) is computed per node, not asserted:
    W1  line_delta = chunk_end - chunk_start + 1 within [20, 100]
    W2  line_delta <= 25% of the projected target file length
    W3  delimited protocol opens with <<<FILE: target>>> and closes with <<<END FILE>>>
    W5  line_delta < 500 and <= 80% of the projected target file length

    python fracture_ch08_watchdog_sre.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent
CHAPTER_PATH: Path = BASE_DIR / "wiki" / "srs" / "ch08_watchdog_sre.md"
LEAF_DIR: Path = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"
TESTS_DIR: Path = BASE_DIR / "tests"

SCHEMA_VERSION: str = "4.1.1-wbs-node/1"
TASK_PREFIX: str = "MC-SRE"
DOMAIN: str = "watchdog_sre"
TEST_PREFIX: str = "test_ch08"
TEST_FILE: str = "tests/test_ch08_watchdog_sre.py"
MIN_LINES, MAX_LINES = 20, 100
W2_MAX_RATIO, W5_MAX_RATIO = 0.25, 0.80

BANNED_TERMS = re.compile(
    r"\b(pyscf|xtb|ase|emt|mendeleev|quantum|rhf|gfn2|hartree|dft|molecul\w*|potential energy)\b", re.I)

# One module on purpose: FR-001 sterility forbids importing pipeline code, and importing a sibling
# module through the cochem package would execute src/cochem/__init__.py.
WATCHDOG_PY = "src/cochem/watchdog/watchdog_sre.py"

# Item key -> (target file, chunk size in lines, title). Chunks are laid out in list
# order within each file; test files come first so every implementation node has a
# failing test to turn green (TDD).
PLACEMENT: list[tuple[str, str, int, str]] = [
    ("TESTS:sterility", TEST_FILE, 50, "Author Sterile Import and Read-Only job_board.db Tests"),
    ("TESTS:probes", TEST_FILE, 50, "Author Binary Header, Process Liveness and Process Tree Tests"),
    ("TESTS:matrices", TEST_FILE, 50, "Author Zombie Lease, Memory Slope and Threshold Buffer Tests"),
    ("TESTS:loop", TEST_FILE, 50, "Author 30-Second Loop, 1.0s Budget and Signal ZD-8 Tests"),
    ("TESTS:collapse", TEST_FILE, 50, "Author COLLAPSED Freeze, Evidence Bundle and Recovery Launch Tests"),
    ("E2E:test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation", TEST_FILE, 80,
     "Author E2E Scenario 3: Multi-Worker Lease Contention and Zombie Reclamation"),
    ("E2E:test_scenario_1_full_pipeline_bootstrap_lifecycle", TEST_FILE, 80,
     "Author E2E Scenario 1: Full Pipeline Bootstrap Lifecycle"),
    ("SRS-412-08-FR-001", WATCHDOG_PY, 40, "Implement Sterile Header, Imports and Read-Only URI Opener"),
    ("NFR-SRE-02", WATCHDOG_PY, 40, "Implement query_only Guard and Write-Lock Refusal"),
    ("INTERFACE:verify_database_binary_header", WATCHDOG_PY, 30, "Implement verify_database_binary_header"),
    ("INTERFACE:check_process_liveness", WATCHDOG_PY, 30, "Implement check_process_liveness"),
    ("SRS-412-08-FR-003", WATCHDOG_PY, 45, "Implement Matrix 1 Process Death Tree Scan"),
    ("SRS-412-08-FR-004", WATCHDOG_PY, 45, "Implement Matrix 2 Zombie Lease Deadlock Detector"),
    ("FAILURE:False Positive Collapse", WATCHDOG_PY, 30, "Implement 1860s vs 1800s Lease Threshold Buffer"),
    ("SRS-412-08-FR-005", WATCHDOG_PY, 35, "Implement Matrix 3 Data Corruption Header Scan"),
    ("SRS-412-08-FR-006", WATCHDOG_PY, 45, "Implement Matrix 4 Memory Slope Leak Alarm"),
    ("SRS-412-08-FR-009", WATCHDOG_PY, 40, "Implement Signal ZD-8 Illegal State Query"),
    ("DATA_MODEL:evidence_bundle", WATCHDOG_PY, 40, "Implement evidence_bundle Record and Validator"),
    ("SRS-412-08-FR-007", WATCHDOG_PY, 55, "Implement COLLAPSED Freeze and Evidence Bundle Assembly"),
    ("SRS-412-08-FR-008", WATCHDOG_PY, 50, "Implement Out-of-Band Fable 5.1 Recovery Launcher"),
    ("NFR-SRE-03", WATCHDOG_PY, 30, "Implement 5-Second Self-Healing Trigger Deadline"),
    ("SRS-412-08-FR-002", WATCHDOG_PY, 50, "Implement 4-Matrix Diagnostic Engine 30-Second Loop"),
    ("NFR-SRE-01", WATCHDOG_PY, 30, "Implement 1.0s Per-Cycle Execution Budget Probe"),
    ("FAILURE:Watchdog Crash", WATCHDOG_PY, 40, "Implement Heartbeat File and Daemon Entry Point"),
]

# Which parsed items each test chunk verifies. Obligations are matched by keyword
# against the chapter's section 9 bullets.
TEST_COVERAGE: dict[str, dict[str, list[str]]] = {
    "TESTS:sterility": {"items": ["SRS-412-08-FR-001", "NFR-SRE-02"],
                        "obligation_keywords": ["WAL file untouched"]},
    "TESTS:probes": {"items": ["INTERFACE:verify_database_binary_header", "INTERFACE:check_process_liveness",
                               "SRS-412-08-FR-003", "SRS-412-08-FR-005"],
                     "obligation_keywords": ["binary header", "exited subprocess"]},
    "TESTS:matrices": {"items": ["SRS-412-08-FR-004", "FAILURE:False Positive Collapse", "SRS-412-08-FR-006"],
                       "obligation_keywords": []},
    "TESTS:loop": {"items": ["SRS-412-08-FR-009", "SRS-412-08-FR-002", "NFR-SRE-01"],
                   "obligation_keywords": []},
    "TESTS:collapse": {"items": ["DATA_MODEL:evidence_bundle", "SRS-412-08-FR-007", "SRS-412-08-FR-008",
                                 "NFR-SRE-03", "FAILURE:Watchdog Crash"],
                       "obligation_keywords": []},
}

# Section 10 end-to-end tests, keyed by exact test name. Each is a whole-system scenario written
# in TEST_FILE and must match the section 10 table exactly. They are general task-queue scenarios:
# everything is built in tmp_path, and no fixture or library from the chemistry test suites is used.
E2E_SCENARIOS: dict[str, dict[str, Any]] = {
    "test_scenario_3_multi_worker_lease_contention_and_zombie_reclamation": {
        "traces": ["SRS-412-08-FR-001"],
        "also_exercises": ["SRS-412-08-FR-003", "SRS-412-08-FR-004", "FAILURE:False Positive Collapse",
                           "SRS-412-08-FR-007", "SRS-412-08-FR-008", "NFR-SRE-02"],
        "steps": (
            "Build a real job_board.db in WAL mode under tmp_path with the jobs columns the watchdog reads "
            "(task_id, status, lease_owner, lease_expires_at, attempts, max_attempts) and seed 20 PENDING jobs. "
            "Launch 10 real Python worker subprocesses; each claims one job inside BEGIN IMMEDIATE, sets "
            "lease_owner to '<hostname>:<pid>' (the format parse_lease_owner_pid reads) with an 1800 s lease, "
            "then blocks. Assert 10 claims on 10 distinct task_ids (zero duplicate leases). Kill worker 5 with "
            "psutil (a real process death) and backdate its lease so now exceeds lease_expires_at by more than "
            "60 s (stalled 1860 s or more). Backdate one live worker's lease by only 30 s to prove the "
            "grace buffer. Hash the db and -wal files (sha256 plus mtime). Run watchdog_sre.py BY FILE PATH "
            "as a subprocess under 'python -X importtime' with --db, --evidence-dir, --worker-state-dir, "
            "--once and --claude-exe pointing at a stand-in Python recovery script written in tmp_path (never "
            "the real claude.exe). Assert FR-001 sterility from the importtime stderr: every top-level module "
            "loaded is in sys.stdlib_module_names or is psutil (no cochem.*). Assert the db and -wal hashes "
            "and mtimes are unchanged (read-only, NFR-SRE-02). Assert the Evidence Bundle JSON has verdict "
            "COLLAPSED, a Matrix 1 failure naming the killed PID, a Matrix 2 failure naming the zombie "
            "task_id, stalled_task_id equal to that task, stalled_duration_sec >= 1860, and no failure "
            "naming the grace-window task. Assert the stand-in received the bundle path in argv. The stand-in, "
            "acting as the out-of-band recovery agent (the watchdog itself never writes), reclaims the zombie: "
            "status PENDING, lease_owner NULL, attempts + 1. A surviving worker then claims and completes it: "
            "final status COMPLETED, attempts == 1, 20 jobs in total. After every worker has completed its "
            "job and exited, a second watchdog --once --no-recovery pass writes no new COLLAPSED bundle. "
            "Terminate all worker processes in a finally block."),
    },
    "test_scenario_1_full_pipeline_bootstrap_lifecycle": {
        "traces": ["SRS-412-08-FR-005"],
        "also_exercises": ["SRS-412-08-FR-002", "NFR-SRE-01", "NFR-SRE-03", "DATA_MODEL:evidence_bundle",
                           "FAILURE:Watchdog Crash"],
        "steps": (
            "Drive the task-queue lifecycle end to end as the watchdog sees it, entirely under tmp_path. "
            "(a) Bootstrap: create a fresh job_board.db in WAL mode and run watchdog_sre.py by file path with "
            "--db, --evidence-dir, --worker-state-dir, --once and --no-recovery. Assert the heartbeat JSON "
            "is written, the verdict is healthy, the reported cycle duration is under 1.0 s (NFR-SRE-01) and "
            "no bundle exists. (b) Run: seed 12 PENDING jobs and start 3 real worker subprocesses that claim "
            "and complete every job through BEGIN IMMEDIATE. Run the watchdog once mid-run and once after "
            "the drain. Both passes are healthy; the Matrix 3 header check passes on the db (16-byte "
            "b'SQLite format 3\\x00') and on the -wal file when present; all 12 jobs are COMPLETED. "
            "(c) Corruption: copy the drained db to a second path and overwrite its first 16 bytes. Run the "
            "watchdog against the copy with --claude-exe set to a stand-in recovery script. Assert a COLLAPSED "
            "Evidence Bundle with a Matrix 3 failure, the stand-in launched within 5 s of the bundle "
            "timestamp (NFR-SRE-03) with the bundle path in argv, and the bundle file is read-only. "
            "(d) Missing database: run against a path that does not exist and assert it is reported as a "
            "Matrix 3 failure with exit status 0 and no Python traceback on stderr. Do NOT use the "
            "seeded_job_board_db or mendeleev_resolver fixtures from tests/test_real_world_e2e_scenarios.py; "
            "they belong to a different suite."),
    },
}

# The node whose verification_command runs the section 10 scenarios instead of its unit tests.
E2E_GATE: set[str] = {"FAILURE:Watchdog Crash"}

# Cross-chunk prerequisites beyond "previous chunk in the same file" and "covering test".
EXTRA_DEPENDENCIES: dict[str, list[str]] = {
    # The daemon entry point is the last chunk; it is the node that turns both section 10 scenarios green.
    "FAILURE:Watchdog Crash": [f"E2E:{name}" for name in E2E_SCENARIOS],
    "SRS-412-08-FR-003": ["INTERFACE:check_process_liveness"],
    "SRS-412-08-FR-005": ["INTERFACE:verify_database_binary_header"],
    "SRS-412-08-FR-009": ["NFR-SRE-02"],
    "SRS-412-08-FR-007": ["DATA_MODEL:evidence_bundle", "SRS-412-08-FR-009"],
    "SRS-412-08-FR-002": ["SRS-412-08-FR-003", "SRS-412-08-FR-004", "SRS-412-08-FR-005",
                          "SRS-412-08-FR-006", "SRS-412-08-FR-009", "NFR-SRE-03"],
}

# Design constraints the chapter implies but does not spell out next to the item.
NOTES: dict[str, str] = {
    "SRS-412-08-FR-001": ("src/cochem/watchdog/watchdog_sre.py already exists (Stage 7 daemon, ~619 lines): "
                          "reconcile it chunk by chunk, never replace it whole. Allowed imports are the standard "
                          "library and psutil only (no cochem.*); run it by file path so src/cochem/__init__.py "
                          "never executes. Open job_board.db as file:<path>?mode=ro with uri=True."),
    "NFR-SRE-02": ("Set PRAGMA query_only=ON on every connection and never run BEGIN IMMEDIATE/EXCLUSIVE; "
                   "attempted writes must surface sqlite3.OperationalError, not be swallowed."),
    "INTERFACE:verify_database_binary_header": "Use the 16-byte SQLITE_MAGIC_HEADER constant from section 6.",
    "INTERFACE:check_process_liveness": "Treat psutil.AccessDenied as not live, exactly as section 6 does.",
    "SRS-412-08-FR-003": ("Map job lease_owner values to PIDs and flag three cases separately: dead PID, hung "
                          "(stopped status or zero CPU time delta across a cycle) and orphaned (parent gone)."),
    "SRS-412-08-FR-004": ("Read 'more than 60 seconds (calibrated to 1860s)' as now > lease_expires_at + 60 on the "
                          "1800 s lease, i.e. a task stalled for 1860 s. Take the grace period from one constant."),
    "FAILURE:False Positive Collapse": ("A lease inside its 60 s grace window must never produce a Matrix 2 "
                                        "failure or a COLLAPSED verdict."),
    "SRS-412-08-FR-005": ("Also check the -wal file when present; a missing database is a Matrix 3 failure, "
                          "not a crash."),
    "SRS-412-08-FR-006": ("Keep a bounded deque of (timestamp, rss_mb) per PID and alarm on a least-squares slope "
                          "above a named MB/min threshold; the chapter gives no number, so expose it as a constant."),
    "SRS-412-08-FR-009": ("Run the Signal ZD-8 statement verbatim from section 3. As specified, a single FAILED "
                          "row trips COLLAPSED; do not quietly narrow the predicate."),
    "DATA_MODEL:evidence_bundle": ("verdict is the literal COLLAPSED, timestamp is integer epoch seconds, "
                                   "matrix_failures is a list of strings. Write the bundle atomically (temp file "
                                   "+ os.replace) and mark it read-only so it is immutable."),
    "SRS-412-08-FR-007": ("Freeze with psutil.Process.suspend() on the worker tree and always resume in a finally "
                          "block once the bundle is dispatched. Include the DB snapshot and Python stack traces."),
    "SRS-412-08-FR-008": ("Build an argv list (no shell=True), launch detached, and pass the bundle path. The "
                          "executable path must be configurable and recovery must be disable-able "
                          "(--no-recovery). Keep the existing --claude-exe flag so tests launch a real stand-in "
                          "executable instead of claude.exe."),
    "NFR-SRE-03": "Measure from COLLAPSED confirmation to recovery Popen return with time.monotonic().",
    "SRS-412-08-FR-002": ("Schedule on time.monotonic() so a slow cycle does not drift the 30 s cadence; a matrix "
                          "exception is recorded as that matrix's failure, not a loop crash."),
    "NFR-SRE-01": "Log a budget breach with the measured duration; do not skip matrices to meet it.",
    "FAILURE:Watchdog Crash": ("Watchdog side only: write a heartbeat JSON file every cycle for the Host Warden "
                               "(ch01) to watch; the Warden restart logic belongs to ch01 nodes. The heartbeat "
                               "records verdict, cycle_duration_sec and matrix_failures. The CLI keeps --db, "
                               "--evidence-dir, --worker-state-dir, --claude-exe, --no-recovery and --once, and "
                               "--once exits 0 whatever the verdict. This node must turn both section 10 "
                               "end-to-end scenarios green."),
}


@dataclass
class ChapterItem:
    key: str
    kind: str
    text: str
    trace_test: str | None = None


@dataclass
class Chapter:
    document_id: str
    items: dict[str, ChapterItem] = field(default_factory=dict)
    obligations: list[str] = field(default_factory=list)


def _section(md: str, number: int) -> str:
    match = re.search(rf"^## {number}\.[^\n]*\n(.*?)(?=^## \d+\.|\Z)", md, re.M | re.S)
    if not match:
        raise ValueError(f"chapter has no section {number}")
    return match.group(1)


def parse_chapter(path: Path) -> Chapter:
    md = path.read_text(encoding="utf-8")
    doc_id = re.search(r"\*\*Document ID\*\*:\s*(\S+)", md)
    chapter = Chapter(document_id=doc_id.group(1) if doc_id else path.stem)

    for req_id, text in re.findall(r"^- \*\*(SRS-[\w-]+-FR-\d+)\*\*:\s*(.+)$", _section(md, 4), re.M):
        chapter.items[req_id] = ChapterItem(req_id, "functional", text.strip())
    for req_id, text in re.findall(r"^- \*\*(NFR-[\w-]+)\*\*:\s*(.+)$", _section(md, 5), re.M):
        chapter.items[req_id] = ChapterItem(req_id, "non_functional", text.strip())

    code = re.search(r"```python\n(.*?)```", _section(md, 6), re.S)
    if code:
        for sig, name, doc in re.findall(r'^(def (\w+)\(.*?\).*?):\n(?:\s+"""(.*?)""")?', code.group(1), re.M | re.S):
            key = f"INTERFACE:{name}"
            text = re.sub(r"\s+", " ", sig)[4:] + (f". {doc.strip()}" if doc else ".")
            chapter.items[key] = ChapterItem(key, "interface", text)

    model = re.search(r"```json\n(.*?)```", _section(md, 7), re.S)
    if model:
        for name, fields in json.loads(model.group(1)).items():
            key = f"DATA_MODEL:{name}"
            text = "; ".join(f"{k}: {type(v).__name__} (e.g. {json.dumps(v)})" for k, v in fields.items())
            chapter.items[key] = ChapterItem(key, "data_model", text)

    for name, text in re.findall(r"^- \*\*(.+?)\*\*:\s*(.+)$", _section(md, 8), re.M):
        key = f"FAILURE:{name}"
        chapter.items[key] = ChapterItem(key, "failure_mode", text.strip())

    chapter.obligations = [b.strip() for b in re.findall(r"^- (.+)$", _section(md, 9), re.M)]

    for req_id, source, test in re.findall(r"^\|[ \t]*(\S+)[ \t]*\|[ \t]*(.+?)[ \t]*\|[ \t]*`(\w+)`[ \t]*\|$",
                                         _section(md, 10), re.M):
        if req_id in chapter.items:
            chapter.items[req_id].trace_test = test
    return chapter


def _slug(key: str) -> str:
    tail = key.split(":", 1)[-1]
    fr = re.search(r"-FR-(\d+)$", tail)
    if fr:
        return f"fr_{fr.group(1)}"
    return re.sub(r"[^a-z0-9]+", "_", tail.lower()).strip("_")


def _check_coverage(chapter: Chapter) -> None:
    placed = {key for key, *_ in PLACEMENT if not key.startswith(("TESTS:", "E2E:"))}
    parsed = set(chapter.items)
    if parsed - placed:
        raise SystemExit(f"chapter items with no leaf node placement: {sorted(parsed - placed)}")
    if placed - parsed:
        raise SystemExit(f"placements for items absent from the chapter: {sorted(placed - parsed)}")
    traced = {item.trace_test: key for key, item in chapter.items.items() if item.trace_test}
    if not traced:
        raise SystemExit("section 10 traceability table parsed no rows")
    if set(traced) != set(E2E_SCENARIOS):
        raise SystemExit(f"section 10 tests {sorted(traced)} != E2E_SCENARIOS {sorted(E2E_SCENARIOS)}")
    for name, scenario in E2E_SCENARIOS.items():
        if scenario["traces"] != [traced[name]]:
            raise SystemExit(f"{name}: section 10 traces {traced[name]}, E2E_SCENARIOS says {scenario['traces']}")
        if unknown := set(scenario["also_exercises"]) - parsed:
            raise SystemExit(f"{name}: also_exercises names unknown items {sorted(unknown)}")
    e2e_placed = {key.split(":", 1)[1] for key, *_ in PLACEMENT if key.startswith("E2E:")}
    if e2e_placed != set(E2E_SCENARIOS):
        raise SystemExit(f"E2E placements {sorted(e2e_placed)} != E2E_SCENARIOS {sorted(E2E_SCENARIOS)}")
    tested = {item for cov in TEST_COVERAGE.values() for item in cov["items"]}
    if parsed - tested:
        raise SystemExit(f"chapter items with no covering test chunk: {sorted(parsed - tested)}")
    for key, cov in TEST_COVERAGE.items():
        for keyword in cov["obligation_keywords"]:
            if not any(keyword in ob for ob in chapter.obligations):
                raise SystemExit(f"{key}: no section 9 obligation mentions {keyword!r}")
    claimed = [ob for ob in chapter.obligations
               if any(kw in ob for cov in TEST_COVERAGE.values() for kw in cov["obligation_keywords"])]
    if len(claimed) != len(chapter.obligations):
        raise SystemExit(f"unassigned test obligations: {sorted(set(chapter.obligations) - set(claimed))}")


def _trace_items(key: str) -> list[str]:
    if key.startswith("E2E:"):
        scenario = E2E_SCENARIOS[key.split(":", 1)[1]]
        return scenario["traces"] + scenario["also_exercises"]
    if key.startswith("TESTS:"):
        return TEST_COVERAGE[key]["items"]
    return [key]


def _instructions(key: str, target: str, chapter: Chapter, chunk_start: int) -> str:
    header = ("Create the file: this chunk owns the module docstring, imports and constants. "
              if chunk_start == 1 else "Append after the previous chunk without editing earlier lines. ")
    if key.startswith("E2E:"):
        name = key.split(":", 1)[1]
        scenario = E2E_SCENARIOS[name]
        return (header + f"Write the section 10 end-to-end test def {name}(tmp_path) with exactly this name. "
                f"It is the traceability test for {', '.join(scenario['traces'])} and also exercises "
                f"{', '.join(scenario['also_exercises'])}. Use real processes, a real SQLite file and psutil; "
                "no mock objects and no skip/xfail markers. It fails until the implementation nodes land. "
                + scenario["steps"])
    if key.startswith("TESTS:"):
        cov = TEST_COVERAGE[key]
        parts = []
        for item_key in cov["items"]:
            item = chapter.items[item_key]
            summary = item.text if item.kind != "data_model" else "section 7 JSON fields"
            parts.append(f"{TEST_PREFIX}_{_slug(item_key)}_* for {item_key} ({summary})")
        obligations = [ob for ob in chapter.obligations if any(kw in ob for kw in cov["obligation_keywords"])]
        text = (header + "Write physical pytest cases (real SQLite job_board.db files under tmp_path and real "
                "child processes; no mock objects): " + "; ".join(parts))
        if obligations:
            text += ". Section 9 obligations: " + " ".join(obligations)
        return text

    item = chapter.items[key]
    if item.kind == "interface":
        body = (f"Implement {item.text} Match the section 6 reference implementation, composed from the "
                "helpers defined in earlier chunks.")
    elif item.kind == "data_model":
        body = (f"Define the {key.split(':', 1)[1]} record exactly as the section 7 JSON model, with a "
                f"validator that rejects missing or mistyped fields. Fields: {item.text}.")
    elif item.kind == "failure_mode":
        body = f"Implement recovery for '{key.split(':', 1)[1]}': {item.text}"
    else:
        body = f"Satisfy {key}: {item.text}"
    if key in NOTES:
        body += f" {NOTES[key]}"
    if item.trace_test:
        body += (f" Traceability test: {TEST_FILE}::{item.trace_test}, written by this chapter's E2E node "
                 "(the same-named test in tests/test_real_world_e2e_scenarios.py is a different suite).")
    return header + body


def _protocol(task_id: str, key: str, target: str, start: int, end: int, instructions: str) -> str:
    spec = [f"# [{task_id}] {key}"] + [f"# SPEC: {line}" for line in textwrap.wrap(instructions, 96)]
    delta = end - start + 1
    if len(spec) > delta:
        raise SystemExit(f"{task_id}: instructions need {len(spec)} lines but chunk holds {delta}")
    body = spec + [f"# {target}:{start + i} [{task_id}]" for i in range(len(spec), delta)]
    return f"<<<FILE: {target}>>>\n" + "\n".join(body) + "\n<<<END FILE>>>"


def build_nodes(chapter: Chapter) -> list[dict[str, Any]]:
    _check_coverage(chapter)
    file_totals: dict[str, int] = {}
    for _, target, size, _ in PLACEMENT:
        file_totals[target] = file_totals.get(target, 0) + size

    task_ids = {key: f"{TASK_PREFIX}-{i:02d}" for i, (key, *_) in enumerate(PLACEMENT, start=1)}
    covering_test = {item: test for test, cov in TEST_COVERAGE.items() for item in cov["items"]}
    cursor: dict[str, int] = {}
    last_in_file: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []

    for key, target, size, title in PLACEMENT:
        task_id = task_ids[key]
        start = cursor.get(target, 0) + 1
        end = start + size - 1
        cursor[target] = end
        delta = end - start + 1

        deps: list[str] = []
        if target in last_in_file:
            deps.append(last_in_file[target])
        if key in covering_test:
            deps.append(task_ids[covering_test[key]])
        deps += [task_ids[d] for d in EXTRA_DEPENDENCIES.get(key, [])]
        deps = list(dict.fromkeys(deps))
        last_in_file[target] = task_id

        if key.startswith("E2E:"):
            verify = f"pytest {TEST_FILE}::{key.split(':', 1)[1]} --collect-only -q"
        elif key.startswith("TESTS:"):
            verify = f"pytest {TEST_FILE} --collect-only -q"
        elif key in E2E_GATE:
            verify = f"pytest " + " ".join(f"{TEST_FILE}::{name}" for name in E2E_SCENARIOS) + " -v"
        else:
            verify = f'pytest {TEST_FILE} -k "{_slug(key)}"'

        instructions = _instructions(key, target, chapter, start)
        protocol = _protocol(task_id, key, target, start, end, instructions)
        banned = BANNED_TERMS.search(" ".join([title, instructions, protocol]))
        if banned:
            raise SystemExit(f"{task_id}: out-of-scope chemistry term {banned.group(0)!r}")
        ratio = delta / file_totals[target]
        content = protocol[len(f"<<<FILE: {target}>>>"):-len("<<<END FILE>>>")].strip().splitlines()
        compliance = {
            "w1_line_bounds_pass": MIN_LINES <= delta <= MAX_LINES,
            "w2_diff_ratio_pass": ratio <= W2_MAX_RATIO,
            "w3_delimited_syntax_pass": protocol.startswith(f"<<<FILE: {target}>>>")
                                        and protocol.endswith("<<<END FILE>>>") and len(content) == delta,
            "w5_whole_file_ban_pass": delta < 500 and ratio <= W5_MAX_RATIO,
        }
        failed = [k for k, ok in compliance.items() if not ok]
        if failed:
            raise SystemExit(f"{task_id} ({target} {start}-{end}, ratio {ratio:.2f}) fails Rule 18: {failed}")

        nodes.append({
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "title": title,
            "domain": DOMAIN,
            "target_file": target,
            "chunk_start": start,
            "chunk_end": end,
            "line_delta": delta,
            "instructions": instructions,
            "dependencies": deps,
            "verification_command": verify,
            "delimited_protocol": protocol,
            "rule_18_compliance": compliance,
            "srs_trace": {"document_id": chapter.document_id,
                          "items": _trace_items(key)},
        })
    return nodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="",
                        help="comma-separated FR numbers (e.g. FR-001,FR-002) to emit one part of the chapter; "
                             "task IDs and chunk bounds stay those of the full layout")
    args = parser.parse_args(argv)

    chapter = parse_chapter(CHAPTER_PATH)
    nodes = build_nodes(chapter)
    if args.only:
        # NFR keys (NFR-SRE-01) are not prefixed with the document ID in the chapter; FR keys are.
        wanted = {name if name.startswith("NFR-") else f"{chapter.document_id}-{name}"
                  for name in (fr.strip().upper() for fr in args.only.split(",")) if name}
        unknown = wanted - set(chapter.items)
        if unknown:
            raise SystemExit(f"--only names requirements absent from the chapter: {sorted(unknown)}")
        nodes = [node for node in nodes if wanted & set(node["srs_trace"]["items"])]
    for node in nodes:
        print(f"{node['task_id']}  {node['target_file']}:{node['chunk_start']}-{node['chunk_end']}"
              f"  ({node['line_delta']} lines)  deps={node['dependencies']}")
    if args.dry_run:
        return 0
    LEAF_DIR.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        (LEAF_DIR / f"{node['task_id']}.json").write_text(json.dumps(node, indent=2), encoding="utf-8")
    print(f"wrote {len(nodes)} leaf nodes for {chapter.document_id} to {LEAF_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
