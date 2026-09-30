"""Fracture SRS chapter 3 (Two-Staged Concurrency Model) into N=1 WBS leaf nodes.

Every requirement, failure mode, data model and test obligation is parsed out of
wiki/srs/ch03_concurrency_layers.md at run time. PLACEMENT only says where each
parsed item lives in the source tree; the generator refuses to run if the chapter
contains an item PLACEMENT does not cover, or if PLACEMENT names an item the chapter
no longer contains.

Rule 18 (Whole-File Rewrite Ban) is computed per node, not asserted:
    W1  line_delta = chunk_end - chunk_start + 1 within [20, 100]
    W2  line_delta <= 25% of the projected target file length
    W3  delimited protocol opens with <<<FILE: target>>> and closes with <<<END FILE>>>
    W5  line_delta < 500 and <= 80% of the projected target file length

    python fracture_ch03_concurrency.py [--dry-run] [--only FR-001,FR-002,...,FAILURES]
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
CHAPTER_PATH: Path = BASE_DIR / "wiki" / "srs" / "ch03_concurrency_layers.md"
LEAF_DIR: Path = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

SCHEMA_VERSION: str = "4.1.1-wbs-node/1"
TASK_PREFIX: str = "MC-CON"
DOMAIN: str = "concurrency"
TEST_FILE: str = "tests/test_ch03_concurrency_layers.py"
MIN_LINES, MAX_LINES = 20, 100
W2_MAX_RATIO, W5_MAX_RATIO = 0.25, 0.80

# Item key -> (target file, chunk size in lines, title). Chunks are laid out in list
# order within each file; test files come first so every implementation node has a
# failing test to turn green (TDD).
PLACEMENT: list[tuple[str, str, int, str]] = [
    ("TESTS:launch", TEST_FILE, 60, "Author Layer 1 Director Invocation Tests"),
    ("TESTS:hang", TEST_FILE, 60, "Author Subprocess Timeout and Orphan Tests"),
    ("TESTS:swarm", TEST_FILE, 60, "Author Sub-Agent Multiplexer and Heap Guard Tests"),
    ("TESTS:layer2", TEST_FILE, 60, "Author Layer 2 Daemon Isolation Tests"),
    ("TESTS:routing", TEST_FILE, 60, "Author Complexity Routing and Profile Tests"),
    ("SRS-412-03-FR-007", "src/cochem/concurrency/runtime.py", 40, "Implement Hidden Process-Group Subprocess Spawner"),
    ("SRS-412-03-FR-006", "src/cochem/concurrency/runtime.py", 40, "Implement JIT Startup Jitter Helper"),
    ("SRS-412-03-FR-008", "src/cochem/concurrency/runtime.py", 40, "Implement Cognitive Complexity Model Router"),
    ("DATA_MODEL:concurrency_profile", "src/cochem/concurrency/runtime.py", 40, "Implement ConcurrencyProfile Data Model"),
    ("SRS-412-03-FR-004", "src/cochem/concurrency/layer2.py", 45, "Implement Queue-Only Layer 2 Daemon Coordination"),
    ("SRS-412-03-FR-005", "src/cochem/concurrency/layer2.py", 45, "Implement Layer 2 Worker 512 MB V8 Environment"),
    ("NFR-CON-01", "src/cochem/concurrency/layer2.py", 45, "Implement Desktop Heap Budget Probe"),
    ("NFR-CON-02", "src/cochem/concurrency/layer2.py", 45, "Implement Process Handle Leak Sampler"),
    ("SRS-412-03-FR-001", "src/cochem/concurrency/layer1.py", 30, "Implement Fable Director CLI Command Builder"),
    ("SRS-412-03-FR-002", "src/cochem/concurrency/layer1.py", 25, "Implement Director 4096 MB V8 Environment"),
    ("SRS-412-03-FR-003", "src/cochem/concurrency/layer1.py", 60, "Implement 20-Slot Sub-Agent Multiplexer"),
    ("NFR-CON-03", "src/cochem/concurrency/layer1.py", 40, "Implement Sub-Agent Message Latency Meter"),
    ("FAILURE:V8 Heap Warning", "src/cochem/concurrency/layer1.py", 45, "Implement Director V8 Heap Recycle Guard"),
    ("FAILURE:Subprocess Hang", "src/cochem/concurrency/layer1.py", 50, "Implement 1740 s Process-Tree Timeout Runner"),
    ("INTERFACE:launch_layer1_fable_director", "src/cochem/concurrency/layer1.py", 40, "Implement launch_layer1_fable_director Entry Point"),
]

# Which parsed items each test chunk verifies. Obligations are matched by keyword
# against the chapter's section 9 bullets.
TEST_COVERAGE: dict[str, dict[str, list[str]]] = {
    "TESTS:launch": {"items": ["SRS-412-03-FR-001", "SRS-412-03-FR-002", "SRS-412-03-FR-007",
                               "INTERFACE:launch_layer1_fable_director"],
                     "obligation_keywords": ["NODE_OPTIONS", "CLI arguments"]},
    "TESTS:hang": {"items": ["FAILURE:Subprocess Hang"], "obligation_keywords": ["TimeoutExpired"]},
    "TESTS:swarm": {"items": ["SRS-412-03-FR-003", "NFR-CON-03", "FAILURE:V8 Heap Warning"],
                    "obligation_keywords": []},
    "TESTS:layer2": {"items": ["SRS-412-03-FR-004", "SRS-412-03-FR-005", "SRS-412-03-FR-006",
                               "NFR-CON-01", "NFR-CON-02"], "obligation_keywords": []},
    "TESTS:routing": {"items": ["SRS-412-03-FR-008", "DATA_MODEL:concurrency_profile"],
                      "obligation_keywords": []},
}

# Exact pytest function name for every parsed item. Items listed in the chapter's
# section 10 Traceability table MUST carry the section 10 name verbatim; the generator
# aborts if they diverge. The remaining items get specific (non-wildcard) names.
TEST_NAMES: dict[str, str] = {
    "SRS-412-03-FR-001": "test_f04_fable_invocation_constructs_valid_cli_arguments",
    "SRS-412-03-FR-002": "test_f04_fable_invocation_injects_4096mb_v8_memory_cap",
    "SRS-412-03-FR-007": "test_f04_fable_invocation_enforces_create_no_window_flag",
    "INTERFACE:launch_layer1_fable_director": "test_f04_fable_invocation_launch_layer1_fable_director_returns_completed_process",
    "FAILURE:Subprocess Hang": "test_f04_subprocess_hang_times_out_at_1740s_without_orphan_children",
    "SRS-412-03-FR-003": "test_f04_director_multiplexer_caps_concurrent_subagents_at_20",
    "NFR-CON-03": "test_f04_subagent_message_overhead_averages_under_10ms",
    "FAILURE:V8 Heap Warning": "test_f04_director_recycles_process_when_heap_reaches_3800mb",
    "SRS-412-03-FR-004": "test_f04_layer2_daemons_coordinate_only_through_job_board_wal",
    "SRS-412-03-FR-005": "test_f04_layer2_worker_injects_512mb_v8_memory_cap",
    "SRS-412-03-FR-006": "test_f04_layer2_poll_jitter_stays_within_100_to_500ms",
    "NFR-CON-01": "test_f04_desktop_heap_usage_below_15_percent_under_20_agents",
    "NFR-CON-02": "test_f04_poll_loop_leaks_zero_process_handles",
    "SRS-412-03-FR-008": "test_f04_complexity_router_maps_scores_to_model_tiers",
    "DATA_MODEL:concurrency_profile": "test_f04_concurrency_profile_round_trips_json",
}

# This chapter is general systems software; no chemistry tooling may leak into a node.
FORBIDDEN_DOMAIN_TOKENS: tuple[str, ...] = ("pyscf", "xtb", "ase.", "rdkit", "psi4", "quantum chem",
                                            "physical chem", "hartree", "dft ")

# Cross-file prerequisites beyond "previous chunk in the same file" and "covering test".
EXTRA_DEPENDENCIES: dict[str, list[str]] = {
    "INTERFACE:launch_layer1_fable_director": ["SRS-412-03-FR-006", "SRS-412-03-FR-007", "FAILURE:Subprocess Hang"],
    "FAILURE:Subprocess Hang": ["SRS-412-03-FR-007"],
    "SRS-412-03-FR-004": ["SRS-412-03-FR-006"],
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

    interface = _section(md, 6)
    for func in re.findall(r"^def (\w+)\(", interface, re.M):
        key = f"INTERFACE:{func}"
        code = re.search(r"```python\n(.*?)```", interface, re.S)
        chapter.items[key] = ChapterItem(key, "interface", code.group(1).strip() if code else func)

    model_block = re.search(r"```json\n(.*?)```", _section(md, 7), re.S)
    if model_block:
        for name, fields in json.loads(model_block.group(1)).items():
            key = f"DATA_MODEL:{name}"
            chapter.items[key] = ChapterItem(key, "data_model", json.dumps(fields))

    for name, text in re.findall(r"^- \*\*(.+?)\*\*:\s*(.+)$", _section(md, 8), re.M):
        key = f"FAILURE:{name}"
        chapter.items[key] = ChapterItem(key, "failure_mode", text.strip())

    chapter.obligations = [b.strip() for b in re.findall(r"^- (.+)$", _section(md, 9), re.M)]

    for req_id, source, test in re.findall(r"^\|\s*(\S+)\s*\|\s*(.+?)\s*\|\s*`(\w+)`\s*\|$", _section(md, 10), re.M):
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
    for key, item in chapter.items.items():
        if key not in TEST_NAMES:
            raise SystemExit(f"{key} has no exact test name")
        if item.trace_test and item.trace_test != TEST_NAMES[key]:
            raise SystemExit(f"{key}: TEST_NAMES has {TEST_NAMES[key]!r} but section 10 says {item.trace_test!r}")
    traced = {k for k, i in chapter.items.items() if i.trace_test}
    if not traced:
        raise SystemExit("section 10 traceability table parsed no rows")
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
            summary = item.text if item.kind != "interface" else f"{item_key.split(':', 1)[1]}() per section 6"
            name = TEST_NAMES[item_key]
            parts.append(f"{name} for {item_key} ({summary})")
        obligations = [ob for ob in chapter.obligations if any(kw in ob for kw in cov["obligation_keywords"])]
        text = header + "Write physical pytest cases (real subprocess, real SQLite WAL, psutil; no mock objects): " + "; ".join(parts)
        if obligations:
            text += ". Section 9 obligations: " + " ".join(obligations)
        return text

    item = chapter.items[key]
    if item.kind == "interface":
        body = (f"Implement {key.split(':', 1)[1]}() matching the section 6 reference: jitter, NODE_OPTIONS, "
                "claude.exe argv, stdin=DEVNULL, capture_output, text=True and the timeout runner, composed "
                "from the helpers defined in earlier chunks.")
    elif item.kind == "data_model":
        body = f"Implement a frozen dataclass {key.split(':', 1)[1]} with fields and to_json()/from_json() for: {item.text}"
    elif item.kind == "failure_mode":
        body = f"Implement recovery for '{key.split(':', 1)[1]}': {item.text}"
    else:
        body = f"Satisfy {key}: {item.text}"
    body += f" Verified by test: {TEST_NAMES[key]}."
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
            verify = f"pytest {TEST_FILE}::{TEST_NAMES[key]}"

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
        lowered = (title + instructions + protocol).lower()
        leaked = [t for t in FORBIDDEN_DOMAIN_TOKENS if t in lowered]
        if leaked:
            raise SystemExit(f"{task_id} mentions chemistry tooling {leaked}; this chapter is general software")
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
    parser.add_argument("--only", default="",
                        help="comma-separated FR numbers (e.g. FR-001,FR-002) to emit one part of the chapter; "
                             "task IDs and chunk bounds stay those of the full layout")
    args = parser.parse_args(argv)

    chapter = parse_chapter(CHAPTER_PATH)
    nodes = build_nodes(chapter)
    if args.only:
        # NFR keys (NFR-CON-01) are not prefixed with the document ID in the chapter; FR keys are.
        # FAILURES selects every section 8 failure mode; FAILURE:<name> selects one verbatim.
        wanted: set[str] = set()
        for raw in (part.strip() for part in args.only.split(",")):
            name = raw.upper()
            if not name:
                continue
            if name == "FAILURES":
                wanted |= {k for k, i in chapter.items.items() if i.kind == "failure_mode"}
            elif name.startswith("FAILURE:"):
                wanted.add(raw)
            else:
                wanted.add(name if name.startswith("NFR-") else f"{chapter.document_id}-{name}")
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
