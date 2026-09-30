"""Fracture SRS chapter 5 (Research-Driven TDD Pivot & Zero-Mock Canaries) into N=1 WBS leaf nodes.

Every functional/non-functional requirement, interface function, JSON data model, failure mode
and test obligation is parsed out of wiki/srs/ch05_research_tdd_pivot.md at run time.
PLACEMENT only says where each parsed item lives in the source tree; the generator refuses
to run if the chapter contains an item PLACEMENT does not cover, or if PLACEMENT names an
item the chapter no longer contains.

The pipeline is a general software system, not a chemistry calculation: EXCLUDED lists the
chapter items that need physical/quantum chemistry libraries, and no node may mention them.

Rule 18 (Whole-File Rewrite Ban) is computed per node, not asserted:
    W1  line_delta = chunk_end - chunk_start + 1 within [20, 100]
    W2  line_delta <= 25% of the projected target file length
    W3  delimited protocol opens with <<<FILE: target>>> and closes with <<<END FILE>>>
    W5  line_delta < 500 and <= 80% of the projected target file length

    python fracture_ch05_tdd_pivot.py [--dry-run]
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
CHAPTER_PATH: Path = BASE_DIR / "wiki" / "srs" / "ch05_research_tdd_pivot.md"
LEAF_DIR: Path = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"

SCHEMA_VERSION: str = "4.1.1-wbs-node/1"
TASK_PREFIX: str = "MC-TDD"
DOMAIN: str = "tdd_pivot"
TEST_FILE: str = "tests/test_ch05_research_tdd_pivot.py"
MIN_LINES, MAX_LINES = 20, 100
W2_MAX_RATIO, W5_MAX_RATIO = 0.25, 0.80

STATE_MACHINE_PY = "src/cochem/tdd_pivot/state_machine.py"
RESEARCH_PY = "src/cochem/tdd_pivot/research.py"
VERIFICATION_PY = "src/cochem/tdd_pivot/verification.py"

# Item key -> (target file, chunk size in lines, title). Chunks are laid out in list
# order within each file; test files come first so every implementation node has a
# failing test to turn green (TDD).
PLACEMENT: list[tuple[str, str, int, str]] = [
    ("TESTS:state_machine", TEST_FILE, 60, "Author 10-Cycle State Machine and Diagnostic Prompt Tests"),
    ("TESTS:research", TEST_FILE, 60, "Author 3-Strike Freeze and Research Dossier Tests"),
    ("TESTS:pivot", TEST_FILE, 60, "Author Root Cause Triage and Physics Wall Abort Tests"),
    ("TESTS:audit", TEST_FILE, 60, "Author Zero-Mock Audit and Mutation Score Tests"),
    ("DATA_MODEL:tdd_cycle_state", STATE_MACHINE_PY, 45, "Implement tdd_cycle_state Dataclass and JSON Round-Trip"),
    ("SRS-412-05-FR-001", STATE_MACHINE_PY, 50, "Implement Bounded 10-Cycle Execute-Audit-Repair Loop"),
    ("NFR-TDD-01", STATE_MACHINE_PY, 40, "Implement 30 s Per-Cycle pytest Runner Timeout"),
    ("NFR-TDD-02", STATE_MACHINE_PY, 40, "Implement 2,000-Token Diagnostic Prompt Bound"),
    ("SRS-412-05-FR-002", STATE_MACHINE_PY, 45, "Implement Gemini Flash Diagnostic Analysis Hook"),
    ("SRS-412-05-FR-003", RESEARCH_PY, 40, "Implement 3-Strike Code Generation Freeze"),
    ("SRS-412-05-FR-004", RESEARCH_PY, 60, "Implement Multi-Database Research Dossier Assembly"),
    ("SRS-412-05-FR-005", RESEARCH_PY, 50, "Implement Fable Root Cause Triage Classifier"),
    ("FAILURE:Exhausted Research Stage", RESEARCH_PY, 40, "Implement Contract Ambiguity Escalation for Human Review"),
    ("SRS-412-05-FR-006", RESEARCH_PY, 40, "Implement MAX_META_PIVOT Counter and Hard Abort Trigger"),
    ("FAILURE:Physics Wall Hard Abort", RESEARCH_PY, 45, "Implement Physics_Autopsy_Report.md Generation"),
    ("NFR-TDD-03", VERIFICATION_PY, 45, "Implement Zero-Mock Library Scanner"),
    ("OBLIGATION:zero_mock_checker", VERIFICATION_PY, 45, "Implement Obfuscated Token and monkeypatch Detection"),
    ("OBLIGATION:NotImplementedError", VERIFICATION_PY, 45, "Implement Stub Auditor for NotImplementedError and Empty pass"),
    ("SRS-412-05-FR-008", VERIFICATION_PY, 45, "Implement mutmut 85% Kill Score Gate"),
]

# This pipeline is a general software system (task queue, orchestrator, daemons). Chapter
# items that can only be met with physical/quantum chemistry libraries get no leaf node.
# Each key must still exist in the chapter so spec drift is caught; OBLIGATION keys match
# section 9 bullets by keyword.
EXCLUDED: dict[str, str] = {
    "INTERFACE:evaluate_zero_mock_canary": "section 6 canary is an ASE EMT / mendeleev physical chemistry calculation",
    "SRS-412-05-FR-007": "requires PySCF, XTB and ASE physical chemistry canaries",
    "OBLIGATION:ASE EMT": "section 9 obligation runs an ASE EMT physical chemistry simulation",
}

# No generated node may mention a chemistry library or calculation.
BANNED_TERMS = re.compile(r"\b(pyscf|xtb|ase|emt|mendeleev|quantum|rhf|gfn2|np\.zeros|potential energy)\b", re.I)

# Which parsed items each test chunk verifies. Obligations are matched by keyword
# against the chapter's section 9 bullets.
TEST_COVERAGE: dict[str, dict[str, list[str]]] = {
    "TESTS:state_machine": {"items": ["DATA_MODEL:tdd_cycle_state", "SRS-412-05-FR-001", "NFR-TDD-01",
                                      "NFR-TDD-02", "SRS-412-05-FR-002"],
                            "obligation_keywords": []},
    "TESTS:research": {"items": ["SRS-412-05-FR-003", "SRS-412-05-FR-004", "FAILURE:Exhausted Research Stage"],
                       "obligation_keywords": []},
    "TESTS:pivot": {"items": ["SRS-412-05-FR-005", "SRS-412-05-FR-006", "FAILURE:Physics Wall Hard Abort"],
                    "obligation_keywords": []},
    "TESTS:audit": {"items": ["NFR-TDD-03", "OBLIGATION:zero_mock_checker", "OBLIGATION:NotImplementedError",
                              "SRS-412-05-FR-008"],
                    "obligation_keywords": ["zero_mock_checker", "NotImplementedError"]},
}

# Cross-file prerequisites beyond "previous chunk in the same file" and "covering test".
EXTRA_DEPENDENCIES: dict[str, list[str]] = {
    "SRS-412-05-FR-003": ["DATA_MODEL:tdd_cycle_state", "SRS-412-05-FR-001"],
    "SRS-412-05-FR-006": ["DATA_MODEL:tdd_cycle_state"],
}

# Design constraints the chapter implies but does not spell out next to the item.
NOTES: dict[str, str] = {
    "DATA_MODEL:tdd_cycle_state": "The section 7 values are an example snapshot, not defaults: default every counter to 0.",
    "SRS-412-05-FR-002": ("Take the Gemini Flash client as an injected callable so tests can pass a real local "
                          "summariser instead of a mock (NFR-TDD-03)."),
    "SRS-412-05-FR-004": ("Each database is a pluggable backend; tests call the real APIs and skip when the "
                          "network is unavailable, never mock them. Write the dossier to dossier_path."),
    "SRS-412-05-FR-006": "The abort marker is a literal status string; no scientific computation is involved.",
    "FAILURE:Physics Wall Hard Abort": ("The report is a Markdown summary of the tdd_cycle_state history, "
                                        "failed pivots and dossier path; no scientific computation is involved."),
    "NFR-TDD-03": "Scan with the ast module so imports, attribute access and aliases are all caught.",
    "OBLIGATION:zero_mock_checker": ("Decode base64 and hex string literals and concatenations before matching, "
                                     "so an obfuscated 'monkeypatch' or 'MagicMock' is still flagged."),
    "OBLIGATION:NotImplementedError": ("Flag functions whose body is only `pass`, `...` or "
                                       "`raise NotImplementedError`; report file, line and function name."),
    "SRS-412-05-FR-008": ("Apply the gate to the tdd_pivot modules (cycle counters, token bound, kill score "
                          "arithmetic). Parse the mutmut results summary; do not reimplement mutation testing."),
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
            text = "; ".join(f"{field} (e.g. {json.dumps(value)})" for field, value in fields.items())
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
    fr = re.search(r"FR-(\d+)$", tail)
    if fr:
        return f"fr_{fr.group(1)}"
    return re.sub(r"[^a-z0-9]+", "_", tail.lower()).strip("_")


def _resolve_obligations(chapter: Chapter) -> None:
    """Turn each OBLIGATION:<keyword> key into an item holding the one section 9 bullet it names."""
    keys = [key for key, *_ in PLACEMENT if key.startswith("OBLIGATION:")]
    keys += [key for key in EXCLUDED if key.startswith("OBLIGATION:")]
    for key in keys:
        matches = [ob for ob in chapter.obligations if key.split(":", 1)[1] in ob]
        if len(matches) != 1:
            raise SystemExit(f"{key}: expected one section 9 obligation, found {len(matches)}")
        chapter.items[key] = ChapterItem(key, "obligation", matches[0])
    unclaimed = [ob for ob in chapter.obligations
                 if not any(item.kind == "obligation" and item.text == ob for item in chapter.items.values())]
    if unclaimed:
        raise SystemExit(f"section 9 obligations with no OBLIGATION key: {unclaimed}")


def _check_coverage(chapter: Chapter) -> None:
    placed = {key for key, *_ in PLACEMENT if not key.startswith("TESTS:")}
    excluded = set(EXCLUDED)
    parsed = set(chapter.items)
    if placed & excluded:
        raise SystemExit(f"items both placed and excluded: {sorted(placed & excluded)}")
    if parsed - placed - excluded:
        raise SystemExit(f"chapter items with no leaf node placement: {sorted(parsed - placed - excluded)}")
    if (placed | excluded) - parsed:
        raise SystemExit(f"placements for items absent from the chapter: {sorted((placed | excluded) - parsed)}")
    tested = {item for cov in TEST_COVERAGE.values() for item in cov["items"]}
    if placed - tested:
        raise SystemExit(f"placed items with no covering test chunk: {sorted(placed - tested)}")
    if tested & excluded:
        raise SystemExit(f"test chunks cover excluded items: {sorted(tested & excluded)}")
    for key, cov in TEST_COVERAGE.items():
        for keyword in cov["obligation_keywords"]:
            if not any(keyword in ob for ob in chapter.obligations):
                raise SystemExit(f"{key}: no section 9 obligation mentions {keyword!r}")
    excluded_obligations = {chapter.items[key].text for key in excluded if key.startswith("OBLIGATION:")}
    claimed = [ob for ob in chapter.obligations
               if any(kw in ob for cov in TEST_COVERAGE.values() for kw in cov["obligation_keywords"])]
    unassigned = set(chapter.obligations) - set(claimed) - excluded_obligations
    if unassigned:
        raise SystemExit(f"unassigned test obligations: {sorted(unassigned)}")


def _instructions(key: str, target: str, chapter: Chapter, chunk_start: int) -> str:
    header = ("Create the file: this chunk owns the module docstring, imports and constants. "
              if chunk_start == 1 else "Append after the previous chunk without editing earlier lines. ")
    if key.startswith("TESTS:"):
        cov = TEST_COVERAGE[key]
        parts = []
        for item_key in cov["items"]:
            item = chapter.items[item_key]
            summary = item.text if item.kind != "data_model" else "section 7 JSON model"
            parts.append(f"test_ch05_{_slug(item_key)}_* for {item_key} ({summary})")
        obligations = [ob for ob in chapter.obligations if any(kw in ob for kw in cov["obligation_keywords"])]
        text = header + ("Write physical pytest cases (real pytest subprocesses, real files and SQLite under "
                         "tmp_path; no mock objects): ") + "; ".join(parts)
        if obligations:
            text += ". Section 9 obligations: " + " ".join(obligations)
        return text

    item = chapter.items[key]
    if item.kind == "interface":
        body = (f"Implement {item.text} Match the section 6 reference implementation, composed from the "
                "helpers defined in earlier chunks.")
    elif item.kind == "data_model":
        body = (f"Define the {key.split(':', 1)[1]} model from section 7 with to_json/from_json that "
                f"round-trip the section 7 layout. Fields: {item.text}")
    elif item.kind == "failure_mode":
        body = f"Implement recovery for '{key.split(':', 1)[1]}': {item.text}"
    elif item.kind == "obligation":
        body = f"Implement the behaviour required by the section 9 test obligation: {item.text}"
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
    _resolve_obligations(chapter)
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
        banned = BANNED_TERMS.search(" ".join([title, instructions, protocol]))
        if banned:
            raise SystemExit(f"{task_id}: node mentions chemistry term {banned.group(0)!r}")

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
    current = {f"{node['task_id']}.json" for node in nodes}
    for stale in sorted(LEAF_DIR.glob(f"{TASK_PREFIX}-*.json")):
        if stale.name not in current:
            stale.unlink()
            print(f"removed stale {stale.name}")
    for node in nodes:
        (LEAF_DIR / f"{node['task_id']}.json").write_text(json.dumps(node, indent=2), encoding="utf-8")
    print(f"wrote {len(nodes)} leaf nodes for {chapter.document_id} to {LEAF_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
