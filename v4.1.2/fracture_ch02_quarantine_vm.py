"""Fracture SRS chapter 2 (Guest-Side Quarantine VM & Ephemeral Docker Sandbox) into N=1 WBS leaf nodes.

Every functional/non-functional requirement, interface function, JSON data model, failure mode
and test obligation is parsed out of wiki/srs/ch02_quarantine_vm.md at run time. PLACEMENT only
says where each parsed item lives in the source tree; the generator refuses to run if the
chapter contains an item neither PLACEMENT nor EXCLUDED covers, or if either names an item the
chapter no longer contains. A placement key may carry a "/part" suffix when one requirement is
split across several chunks; coverage is checked on the base key.

The pipeline is a general software system (task queue, orchestrator, daemons), not a chemistry
calculation: EXCLUDED lists the chapter leftovers that need physical chemistry libraries (the
section 6 canary and two section 9 obligations), and BANNED_TERMS fails the run if any node
mentions a chemistry library or method.

Rule 18 (Whole-File Rewrite Ban) is computed per node, not asserted:
    W1  line_delta = chunk_end - chunk_start + 1 within [20, 100]
    W2  line_delta <= 25% of the projected target file length
    W3  delimited protocol opens with <<<FILE: target>>> and closes with <<<END FILE>>>
    W5  line_delta < 500 and <= 80% of the projected target file length

    python fracture_ch02_quarantine_vm.py [--dry-run]
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
CHAPTER_PATH: Path = BASE_DIR / "wiki" / "srs" / "ch02_quarantine_vm.md"
LEAF_DIR: Path = BASE_DIR / "wiki" / "wbs" / "leaf_nodes"
TESTS_DIR: Path = BASE_DIR / "tests"

SCHEMA_VERSION: str = "4.1.1-wbs-node/1"
TASK_PREFIX: str = "MC-QVM"
DOMAIN: str = "quarantine_vm"
TEST_PREFIX: str = "test_ch02"
TEST_FILE: str = "tests/test_ch02_quarantine_vm.py"
MIN_LINES, MAX_LINES = 20, 100
W2_MAX_RATIO, W5_MAX_RATIO = 0.25, 0.80

EXCLUDED: dict[str, str] = {
    "INTERFACE:verify_physics_canary": "section 6 canary is a physical chemistry calculation (REMOVED in section 3)",
}
# Section 9 bullets that belong to the removed canary; matched by keyword.
EXCLUDED_OBLIGATION_KEYWORDS: list[str] = ["mendeleev", "ASE EMT"]
# Data model fields whose SRS example value is a leftover of the removed canary.
REDACTED_EXAMPLES: dict[str, str] = {
    "image": "str (configurable general-purpose sandbox image tag; the SRS example tag is a leftover of the "
             "removed canary and must not pull scientific libraries)",
}

BANNED_TERMS = re.compile(
    r"\b(pyscf|xtb|ase|emt|mendeleev|quantum|rhf|gfn2|hartree|dft|molecul\w*|physics|atomic|potential energy)\b",
    re.I)

# Host side (Windows, Hyper-V PowerShell bridge) and guest side (Ubuntu, Docker CLI) are separate modules.
VM_PROFILE_PY = "src/cochem/quarantine/vm_profile.py"
SANDBOX_PY = "src/cochem/quarantine/sandbox.py"

# Item key -> (target file, chunk size in lines, title). Chunks are laid out in list
# order within each file; test files come first so every implementation node has a
# failing test to turn green (TDD).
PLACEMENT: list[tuple[str, str, int, str]] = [
    ("TESTS:vm_profile", TEST_FILE, 50, "Author Static 32 GB Memory and 12 vCPU 70% Cap Profile Tests"),
    ("TESTS:sandbox_spec", TEST_FILE, 50, "Author sandbox_spec Model, docker run Flag and Network Drop Tests"),
    ("TESTS:ferpa", TEST_FILE, 50, "Author FERPA Air-Gapped Container and Identifier Scrub Tests"),
    ("TESTS:scheduler", TEST_FILE, 50, "Author 6-Slot Scheduler, Startup Latency and OOM Isolation Tests"),
    ("TESTS:purge", TEST_FILE, 50, "Author Exit Purge and 60-Second Orphan Scratch Sweep Tests"),
    ("SRS-412-02-FR-001", VM_PROFILE_PY, 40, "Implement Static 32 GB Set-VMMemory Command Builder"),
    ("SRS-412-02-FR-001/verify", VM_PROFILE_PY, 40, "Implement Dynamic Memory Disabled Verification Probe"),
    ("SRS-412-02-FR-002", VM_PROFILE_PY, 40, "Implement 12 vCPU 70% Cap Set-VMProcessor Command Builder"),
    ("SRS-412-02-FR-002/verify", VM_PROFILE_PY, 40, "Implement Processor Count and Maximum Cap Verification Probe"),
    ("DATA_MODEL:sandbox_spec", SANDBOX_PY, 40, "Implement sandbox_spec Record and Validator"),
    ("SRS-412-02-FR-003", SANDBOX_PY, 45, "Implement Ephemeral docker run Argument Builder"),
    ("NFR-VM-03", SANDBOX_PY, 30, "Implement In-Container Network Unreachable Probe"),
    ("SRS-412-02-FR-004", SANDBOX_PY, 50, "Implement FERPA Identifier Scrubber and Air-Gapped Launch"),
    ("NFR-VM-01", SANDBOX_PY, 30, "Implement 1.5 s Container Startup Latency Probe"),
    ("SRS-412-02-FR-007", SANDBOX_PY, 50, "Implement 6-Slot 4 GB Container Scheduler"),
    ("NFR-VM-02", SANDBOX_PY, 30, "Implement Per-Container OOM Containment Limits"),
    ("FAILURE:Container OOM Crash", SANDBOX_PY, 45, "Implement Exit 137 Capture, Memory Slope and Task Quarantine"),
    ("SRS-412-02-FR-008", SANDBOX_PY, 40, "Implement Scratch, tmpfs and Layer Purge on Container Exit"),
    ("FAILURE:Scratch Leak", SANDBOX_PY, 45, "Implement 60-Second Orphan Scratch Namespace Sweeper"),
]

# Which parsed items each test chunk verifies. Obligations are matched by keyword
# against the chapter's section 9 bullets.
TEST_COVERAGE: dict[str, dict[str, list[str]]] = {
    "TESTS:vm_profile": {"items": ["SRS-412-02-FR-001", "SRS-412-02-FR-002"],
                         "obligation_keywords": []},
    "TESTS:sandbox_spec": {"items": ["DATA_MODEL:sandbox_spec", "SRS-412-02-FR-003", "NFR-VM-03"],
                           "obligation_keywords": ["--network none"]},
    "TESTS:ferpa": {"items": ["SRS-412-02-FR-004"],
                    "obligation_keywords": []},
    "TESTS:scheduler": {"items": ["SRS-412-02-FR-007", "NFR-VM-01", "NFR-VM-02", "FAILURE:Container OOM Crash"],
                        "obligation_keywords": []},
    "TESTS:purge": {"items": ["SRS-412-02-FR-008", "FAILURE:Scratch Leak"],
                    "obligation_keywords": []},
}

# Cross-chunk prerequisites beyond "previous chunk in the same file" and "covering test".
EXTRA_DEPENDENCIES: dict[str, list[str]] = {
    "SRS-412-02-FR-003": ["DATA_MODEL:sandbox_spec"],
    "NFR-VM-03": ["SRS-412-02-FR-003"],
    "SRS-412-02-FR-004": ["SRS-412-02-FR-003"],
    "SRS-412-02-FR-007": ["SRS-412-02-FR-003", "NFR-VM-01"],
    "NFR-VM-02": ["DATA_MODEL:sandbox_spec"],
    "FAILURE:Container OOM Crash": ["SRS-412-02-FR-007", "NFR-VM-02"],
    "SRS-412-02-FR-008": ["SRS-412-02-FR-007"],
    "FAILURE:Scratch Leak": ["SRS-412-02-FR-008"],
}

# Design constraints the chapter implies but does not spell out next to the item.
NOTES: dict[str, str] = {
    "SRS-412-02-FR-001": ("Host side, runs on Windows. Reuse get_vm_ram_cap_bytes() from "
                          "src/cochem/warden/memory_cap.py (34359738368 bytes) instead of a second constant. Build "
                          "an argv list for powershell.exe -NoProfile -Command Set-VMMemory -VMName <name> "
                          "-DynamicMemoryEnabled $false -StartupBytes <cap>; never shell=True, and quote the VM "
                          "name. The VM must be Off to change memory: refuse with a clear error otherwise."),
    "SRS-412-02-FR-001/verify": ("Run Get-VMMemory -VMName <name> | ConvertTo-Json and parse it: "
                                 "DynamicMemoryEnabled must be false and Startup must equal the cap; return a "
                                 "list of violations, not a bool. 'Permanently disabled' means the probe runs at "
                                 "every Warden boot; the ch01 MemoryCap Pester suite (MC-HW-63) checks the same "
                                 "cap from the host, so keep the byte value identical."),
    "SRS-412-02-FR-002": ("Build Set-VMProcessor -VMName <name> -Count 12 -Maximum 70 -RelativeWeight 100 as an "
                          "argv list; 70 is Hyper-V's percentage Maximum (hard cap), not Reserve. Expose "
                          "VCPU_COUNT=12 and CPU_CAP_PERCENT=70 as module constants."),
    "SRS-412-02-FR-002/verify": ("Parse Get-VMProcessor -VMName <name> | ConvertTo-Json: Count must be 12 and "
                                 "Maximum must be 70; report each mismatch as a violation string."),
    "DATA_MODEL:sandbox_spec": ("Frozen dataclass with from_dict()/to_dict(). network must be 'none', cap_drop "
                                "must contain 'ALL', security_opt must contain 'no-new-privileges:true', and "
                                "memory_mb must not exceed 4096 (FR-007 slot size)."),
    "SRS-412-02-FR-003": ("Guest side, runs in the Ubuntu 24.04 VM. Return a docker run argv list that always "
                          "contains --rm, --network none, --read-only, --tmpfs /tmp:rw,size=2g plus --memory, "
                          "--cpus, --pids-limit, --cap-drop ALL and --security-opt from the sandbox_spec. Callers "
                          "cannot remove these flags; an override that weakens one raises ValueError."),
    "NFR-VM-03": ("Run a real container with the FR-003 argv that attempts a TCP connect to 1.1.1.1:53 and "
                  "assert the error text contains 'Network is unreachable'; a timeout does not count as proof."),
    "SRS-412-02-FR-004": ("Scrub student identifiers (student IDs, emails, names from the roster column list) "
                          "into stable salted SHA-256 pseudonyms before any payload enters the container, and "
                          "launch with the FR-003 flags plus a per-task --name and no bind mounts except the "
                          "scrubbed read-only input. Keep the salt outside the container."),
    "NFR-VM-01": ("Measure with time.monotonic() from the execution request to the first line the container "
                  "writes to stdout; record the value and flag breaches above 1.5 s without failing the task."),
    "SRS-412-02-FR-007": ("Use a threading.BoundedSemaphore(6) so at most 6 sandboxes with memory_mb=4096 run at "
                          "once (24 GB of the 32 GB VM); a seventh request blocks until a slot frees, and the "
                          "slot is released in a finally block."),
    "NFR-VM-02": ("Set --memory and --memory-swap to the same value so a container cannot swap past its slot, "
                  "and never pass --oom-kill-disable, so the kernel kills only the offending container."),
    "FAILURE:Container OOM Crash": ("Read State.OOMKilled and ExitCode via docker inspect before removal; on "
                                    "exit 137 store the sampled docker stats memory series and its slope, and "
                                    "mark the task QUARANTINED so the scheduler never retries it automatically."),
    "SRS-412-02-FR-008": ("--rm already removes the container; also remove any named volumes the task created "
                          "and run docker image prune --filter label=cochem.task=<id> for intermediate layers. "
                          "Purge runs in a finally block even when the container crashed."),
    "FAILURE:Scratch Leak": ("A daemon thread wakes every 60 s, lists cochem-labelled scratch paths under /tmp "
                             "that are not in /proc/self/mountinfo, and removes them; it never touches a path "
                             "that belongs to a running container."),
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
            text = "; ".join(f"{k}: {REDACTED_EXAMPLES[k]}" if k in REDACTED_EXAMPLES
                             else f"{k}: {type(v).__name__} (e.g. {json.dumps(v)})" for k, v in fields.items())
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


def _base(key: str) -> str:
    return key.split("/", 1)[0]


def _slug(key: str) -> str:
    tail = _base(key).split(":", 1)[-1]
    fr = re.search(r"-FR-(\d+)$", tail)
    if fr:
        return f"fr_{fr.group(1)}"
    return re.sub(r"[^a-z0-9]+", "_", tail.lower()).strip("_")


def _check_coverage(chapter: Chapter) -> None:
    keys = [key for key, *_ in PLACEMENT]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate placement keys")
    placed = {_base(key) for key in keys if not key.startswith("TESTS:")}
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
    keywords = [kw for cov in TEST_COVERAGE.values() for kw in cov["obligation_keywords"]]
    for keyword in keywords + EXCLUDED_OBLIGATION_KEYWORDS:
        if sum(keyword in ob for ob in chapter.obligations) != 1:
            raise SystemExit(f"expected exactly one section 9 obligation mentioning {keyword!r}")
    unassigned = [ob for ob in chapter.obligations
                  if not any(kw in ob for kw in keywords + EXCLUDED_OBLIGATION_KEYWORDS)]
    if unassigned:
        raise SystemExit(f"unassigned test obligations: {unassigned}")


def _trace_location(test_name: str) -> str:
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if re.search(rf"^def {test_name}\(", path.read_text(encoding="utf-8"), re.M):
            return path.relative_to(BASE_DIR).as_posix()
    return "not found under tests/"


def _instructions(key: str, target: str, chapter: Chapter, chunk_start: int) -> str:
    header = ("Create the file: this chunk owns the module docstring, imports and constants. "
              if chunk_start == 1 else "Append after the previous chunk without editing earlier lines. ")
    if key.startswith("TESTS:"):
        cov = TEST_COVERAGE[key]
        parts = []
        for item_key in cov["items"]:
            item = chapter.items[item_key]
            summary = item.text if item.kind != "data_model" else "section 7 JSON fields"
            parts.append(f"{TEST_PREFIX}_{_slug(item_key)}_* for {item_key} ({summary})")
        obligations = [ob for ob in chapter.obligations if any(kw in ob for kw in cov["obligation_keywords"])]
        text = (header + "Write physical pytest cases with no mock objects: command builders and parsers are "
                "tested on their real return values; live cases run the real docker or powershell.exe binary and "
                "call pytest.skip only when that binary or daemon is absent on the runner: "
                + "; ".join(parts))
        if obligations:
            text += ". Section 9 obligations: " + " ".join(obligations)
        return text

    item = chapter.items[_base(key)]
    if item.kind == "data_model":
        body = (f"Define the {item.key.split(':', 1)[1]} record exactly as the section 7 JSON model, with a "
                f"validator that rejects missing or mistyped fields. Fields: {item.text}.")
    elif item.kind == "failure_mode":
        body = f"Implement recovery for '{item.key.split(':', 1)[1]}': {item.text}"
    else:
        body = f"Satisfy {item.key}: {item.text}"
    if key in NOTES:
        body += f" {NOTES[key]}"
    if item.trace_test and key == item.key:
        body += f" Traceability test: {item.trace_test} ({_trace_location(item.trace_test)})."
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
        if _base(key) in covering_test:
            deps.append(task_ids[covering_test[_base(key)]])
        deps += [task_ids[d] for d in EXTRA_DEPENDENCIES.get(key, [])]
        deps = list(dict.fromkeys(deps))
        last_in_file[target] = task_id

        if key.startswith("TESTS:"):
            verify = f"pytest {TEST_FILE} --collect-only -q"
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
                          "items": TEST_COVERAGE[key]["items"] if key.startswith("TESTS:") else [_base(key)]},
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
        # NFR keys (NFR-VM-01) are not prefixed with the document ID in the chapter; FR keys are.
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
