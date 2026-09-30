"""Fracture SRS chapter 4 (Universal Blackboard & Task Matrix Queue) into N=1 WBS leaf nodes.

Every functional/non-functional requirement, interface function, SQL table, failure mode
and test obligation is parsed out of wiki/srs/ch04_task_matrix_blackboard.md at run time.
PLACEMENT only says where each parsed item lives in the source tree; the generator refuses
to run if the chapter contains an item PLACEMENT does not cover, or if PLACEMENT names an
item the chapter no longer contains.

Rule 18 (Whole-File Rewrite Ban) is computed per node, not asserted:
    W1  line_delta = chunk_end - chunk_start + 1 within [20, 100]
    W2  line_delta <= 25% of the projected target file length
    W3  delimited protocol opens with <<<FILE: target>>> and closes with <<<END FILE>>>
    W5  line_delta < 500 and <= 80% of the projected target file length

    python fracture_ch04_task_matrix.py [--dry-run]
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
CHAPTER_PATH: Path = BASE_DIR / "wiki" / "srs" / "ch04_task_matrix_blackboard.md"
LEAF_DIR: Path = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

SCHEMA_VERSION: str = "4.1.1-wbs-node/1"
TASK_PREFIX: str = "MC-BB"
DOMAIN: str = "task_matrix"
TEST_FILE: str = "tests/test_ch04_task_matrix_blackboard.py"
MIN_LINES, MAX_LINES = 20, 100
W2_MAX_RATIO, W5_MAX_RATIO = 0.25, 0.80

SCHEMA_PY = "src/cochem/blackboard/schema.py"
LEASES_PY = "src/cochem/blackboard/leases.py"
LIFECYCLE_PY = "src/cochem/blackboard/lifecycle.py"

# Item key -> (target file, chunk size in lines, title). Chunks are laid out in list
# order within each file; test files come first so every implementation node has a
# failing test to turn green (TDD).
PLACEMENT: list[tuple[str, str, int, str]] = [
    ("TESTS:schema", TEST_FILE, 60, "Author WAL Schema, Read-Only and Idempotent Injection Tests"),
    ("TESTS:claim", TEST_FILE, 60, "Author Atomic Lease Claim and Contention Tests"),
    ("TESTS:lifecycle", TEST_FILE, 60, "Author Terminal Transition and Poison-Pill Tests"),
    ("TESTS:lease", TEST_FILE, 60, "Author Heartbeat and Expired Lease Reclamation Tests"),
    ("TESTS:matrix", TEST_FILE, 60, "Author Urgency x Fidelity Routing Tests"),
    ("DATA_MODEL:jobs", SCHEMA_PY, 40, "Implement jobs Table DDL and ensure_schema"),
    ("SRS-412-04-FR-001", SCHEMA_PY, 40, "Implement WAL Connection Factory with Pragmas"),
    ("FAILURE:Database Lock Contention", SCHEMA_PY, 40, "Implement Busy-Timeout Lock Contention Handling"),
    ("SRS-412-04-FR-007", SCHEMA_PY, 40, "Implement Read-Only Observer Connection"),
    ("NFR-TM-02", SCHEMA_PY, 40, "Implement Non-Blocking PASSIVE WAL Checkpoint"),
    ("SRS-412-04-FR-008", SCHEMA_PY, 40, "Implement Idempotent INSERT OR IGNORE Batch Injection"),
    ("SRS-412-04-FR-003", LEASES_PY, 25, "Implement 1800 s Default Lease Constant"),
    ("SRS-412-04-FR-006", LEASES_PY, 50, "Implement Urgency x Fidelity Capability Matrix"),
    ("SRS-412-04-FR-002", LEASES_PY, 40, "Implement BEGIN IMMEDIATE Transaction Helper"),
    ("INTERFACE:claim_next_task", LEASES_PY, 50, "Implement claim_next_task Atomic Lease"),
    ("NFR-TM-03", LEASES_PY, 40, "Implement Duplicate Claim Detector"),
    ("NFR-TM-01", LEASES_PY, 40, "Implement Lease Acquisition Latency Probe"),
    ("INTERFACE:heartbeat_lease", LEASES_PY, 40, "Implement heartbeat_lease Refresh"),
    ("SRS-412-04-FR-004", LEASES_PY, 45, "Implement 5 s Heartbeat Loop"),
    ("FAILURE:Daemon Crash with Active Lease", LEASES_PY, 45, "Implement Expired Lease Reclamation"),
    ("SRS-412-04-FR-009", LIFECYCLE_PY, 40, "Implement ZD-8 Lease-Clearing Transition Helper"),
    ("SRS-412-04-FR-005", LIFECYCLE_PY, 40, "Implement Poison-Pill BLOCKED Quarantine Rule"),
    ("INTERFACE:complete_task", LIFECYCLE_PY, 40, "Implement complete_task Transition"),
    ("INTERFACE:fail_task", LIFECYCLE_PY, 40, "Implement fail_task Transition"),
]

# Which parsed items each test chunk verifies. Obligations are matched by keyword
# against the chapter's section 9 bullets.
TEST_COVERAGE: dict[str, dict[str, list[str]]] = {
    "TESTS:schema": {"items": ["DATA_MODEL:jobs", "SRS-412-04-FR-001", "SRS-412-04-FR-007",
                               "SRS-412-04-FR-008", "NFR-TM-02", "FAILURE:Database Lock Contention"],
                     "obligation_keywords": ["mode=ro"]},
    "TESTS:claim": {"items": ["SRS-412-04-FR-002", "SRS-412-04-FR-003", "INTERFACE:claim_next_task",
                              "NFR-TM-01", "NFR-TM-03"],
                    "obligation_keywords": ["double claims"]},
    "TESTS:lifecycle": {"items": ["SRS-412-04-FR-005", "SRS-412-04-FR-009", "INTERFACE:complete_task",
                                  "INTERFACE:fail_task"],
                        "obligation_keywords": ["BLOCKED"]},
    "TESTS:lease": {"items": ["SRS-412-04-FR-004", "INTERFACE:heartbeat_lease",
                              "FAILURE:Daemon Crash with Active Lease"],
                    "obligation_keywords": []},
    "TESTS:matrix": {"items": ["SRS-412-04-FR-006"], "obligation_keywords": []},
}

# Cross-file prerequisites beyond "previous chunk in the same file" and "covering test".
EXTRA_DEPENDENCIES: dict[str, list[str]] = {
    "SRS-412-04-FR-002": ["SRS-412-04-FR-001"],
    "INTERFACE:claim_next_task": ["DATA_MODEL:jobs", "SRS-412-04-FR-003", "SRS-412-04-FR-006"],
    "INTERFACE:heartbeat_lease": ["SRS-412-04-FR-002"],
    "FAILURE:Daemon Crash with Active Lease": ["SRS-412-04-FR-005", "SRS-412-04-FR-009"],
    "SRS-412-04-FR-009": ["SRS-412-04-FR-002"],
    "INTERFACE:fail_task": ["SRS-412-04-FR-005"],
}

# Design constraints the chapter implies but does not spell out next to the item.
NOTES: dict[str, str] = {
    "SRS-412-04-FR-006": ("The section 7 jobs table has no urgency/fidelity columns: derive both from "
                          "priority and payload_json without altering the section 7 DDL."),
    "FAILURE:Daemon Crash with Active Lease": "The reset to PENDING must also clear lease_owner and lease_expires_at.",
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

    sql = re.search(r"```sql\n(.*?)```", _section(md, 7), re.S)
    if sql:
        for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql.group(1), re.S):
            columns = [line.strip().rstrip(",") for line in body.strip().splitlines()]
            key = f"DATA_MODEL:{table}"
            chapter.items[key] = ChapterItem(key, "data_model", "; ".join(columns))

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
    fr = re.search(r"FR-(\d+)$", tail)
    if fr:
        return f"fr_{fr.group(1)}"
    return re.sub(r"[^a-z0-9]+", "_", tail.lower()).strip("_")


def _check_coverage(chapter: Chapter) -> None:
    placed = {key for key, *_ in PLACEMENT if not key.startswith("TESTS:")}
    parsed = set(chapter.items)
    if parsed - placed:
        raise SystemExit(f"chapter items with no leaf node placement: {sorted(parsed - placed)}")
    if placed - parsed:
        raise SystemExit(f"placements for items absent from the chapter: {sorted(placed - parsed)}")
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


def _instructions(key: str, target: str, chapter: Chapter, chunk_start: int) -> str:
    header = ("Create the file: this chunk owns the module docstring, imports and constants. "
              if chunk_start == 1 else "Append after the previous chunk without editing earlier lines. ")
    if key.startswith("TESTS:"):
        cov = TEST_COVERAGE[key]
        parts = []
        for item_key in cov["items"]:
            item = chapter.items[item_key]
            summary = item.text if item.kind != "data_model" else "section 7 CREATE TABLE"
            parts.append(f"test_ch04_{_slug(item_key)}_* for {item_key} ({summary})")
        obligations = [ob for ob in chapter.obligations if any(kw in ob for kw in cov["obligation_keywords"])]
        text = header + "Write physical pytest cases (real SQLite WAL files, real threads; no mock objects): " + "; ".join(parts)
        if obligations:
            text += ". Section 9 obligations: " + " ".join(obligations)
        return text

    item = chapter.items[key]
    if item.kind == "interface":
        body = (f"Implement {item.text} Match the section 6 reference implementation, composed from the "
                "helpers defined in earlier chunks.")
    elif item.kind == "data_model":
        body = (f"Define the {key.split(':', 1)[1]} CREATE TABLE statement verbatim from section 7 and an "
                f"ensure_schema(conn) that executes it. Columns: {item.text}")
    elif item.kind == "failure_mode":
        body = f"Implement recovery for '{key.split(':', 1)[1]}': {item.text}"
    else:
        body = f"Satisfy {key}: {item.text}"
    if key in NOTES:
        body += f" {NOTES[key]}"
    if item.trace_test:
        body += f" Traceability test: {item.trace_test}."
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

        if key.startswith("TESTS:"):
            verify = f"pytest {TEST_FILE} --collect-only -q"
        else:
            verify = f'pytest {TEST_FILE} -k "{_slug(key)}"'

        instructions = _instructions(key, target, chapter, start)
        protocol = _protocol(task_id, key, target, start, end, instructions)
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
                          "items": TEST_COVERAGE[key]["items"] if key.startswith("TESTS:") else [key]},
        })
    return nodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    chapter = parse_chapter(CHAPTER_PATH)
    nodes = build_nodes(chapter)
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
