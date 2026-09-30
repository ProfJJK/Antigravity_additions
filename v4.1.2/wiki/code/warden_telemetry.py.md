# src/cochem/warden/telemetry.py

`python
"""Host Warden Telemetry and PEP 657 Traceback Parsing (MC-HW-52, MC-HW-53)."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

# Default directory for host warden crash telemetry storage
DEFAULT_CRASH_DIR: Path = Path(".evidence/crashes")
INLINE_TELEMETRY_CAP_BYTES: int = 16384


def record_crash_envelope(
    envelope_data: dict[str, Any],
    output_dir: Path | str | None = None,
) -> Path:
    """Serializes diagnostic crash envelope to JSONL packet in .evidence/crashes/.

    Conforms to SRS-412-01-FR-006 and CAP-13 PEP 657 telemetry specifications.
    Appends structured JSON records to the corresponding task crash journal file.
    """
    if not isinstance(envelope_data, dict):
        raise TypeError(f"envelope_data must be a dict, got {type(envelope_data).__name__}")

    # Normalize target output directory or file path
    if output_dir is None:
        target_dir = DEFAULT_CRASH_DIR
    elif isinstance(output_dir, str):
        target_dir = Path(output_dir)
    else:
        target_dir = output_dir

    # Extract task id and sanitize for safe filesystem naming
    raw_task_id = envelope_data.get("task_id", "unknown")
    safe_task_id = str(raw_task_id).replace("/", "_").replace("\\", "_").replace(":", "_").strip()
    if not safe_task_id:
        safe_task_id = "unknown"

    if target_dir.suffix.lower() in (".jsonl", ".json"):
        target_file = target_dir
        target_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"crash_{safe_task_id}.jsonl"

    # Assemble authentic payload with required PEP 657 fields
    payload: dict[str, Any] = dict(envelope_data)
    payload.setdefault("task_id", str(raw_task_id))
    payload.setdefault("worker_pid", os.getpid())
    payload.setdefault("host_timestamp", datetime.now(timezone.utc).isoformat())

    # Atomically append JSONL record to destination file
    encoded_line = json.dumps(payload, ensure_ascii=False)
    with open(target_file, "a", encoding="utf-8") as f:
        f.write(encoded_line + "\n")

    return target_file


def sanitize_inline_telemetry(raw_text: str, max_bytes: int = INLINE_TELEMETRY_CAP_BYTES) -> str:
    """Applies the strict 16 KiB inline telemetry ceiling per CAP-13 Section 5."""
    encoded = raw_text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw_text

    marker = "\n\n[... TELEMETRY TRUNCATED BY HOST WARDEN ...]\n\n"
    head_bytes = 8000
    tail_bytes = 8000
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


def load_crash_envelopes(source: Path | str) -> list[dict[str, Any]]:
    """Loads and deserializes JSONL crash packets from a file or directory."""
    src_path = Path(source)
    if not src_path.exists():
        return []

    results: list[dict[str, Any]] = []
    if src_path.is_file():
        files = [src_path]
    else:
        files = sorted(list(src_path.glob("*.jsonl")) + list(src_path.glob("*.json")))

    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                    if isinstance(record, dict):
                        results.append(record)
                except json.JSONDecodeError:
                    continue

    return results


def parse_pep657_traceback(tb_str: str) -> list[dict[str, Any]]:
    """Extracts fine-grained PEP 657 column and expression offsets from traceback text.

    Conforms to PEP 657 and CAP-13 specification for structured crash telemetry.
    Parses Python 3.11+ tracebacks with fine-grained caret/tilde source ranges into
    frame dictionaries with line, column range (start, end), and expression offsets.
    """
    frames: list[dict[str, Any]] = []
    if not tb_str or not isinstance(tb_str, str):
        return frames

    lines = tb_str.splitlines()
    frame_pattern = re.compile(r'^\s*File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+(.+))?')
    caret_pattern = re.compile(r"^[ ~^]+$")

    i = 0
    num_lines = len(lines)
    while i < num_lines:
        line = lines[i]
        match = frame_pattern.match(line)
        if match:
            filename = match.group(1)
            lineno = int(match.group(2))
            function_name = (match.group(3) or "").strip()

            code_line = ""
            caret_line = ""
            col_start: int | None = None
            col_end: int | None = None
            expression: str | None = None

            # Look ahead for source code line and PEP 657 caret/tilde line
            j = i
            if i + 1 < num_lines:
                next_line = lines[i + 1]
                if (
                    not frame_pattern.match(next_line)
                    and not next_line.startswith("Traceback")
                    and not next_line.strip().startswith("During handling")
                    and not next_line.strip().startswith("The above exception")
                ):
                    potential_code = next_line
                    if (
                        i + 2 < num_lines
                        and ("^" in lines[i + 2] or "~" in lines[i + 2])
                        and caret_pattern.match(lines[i + 2])
                    ):
                        code_line = potential_code
                        caret_line = lines[i + 2]
                        j = i + 2
                    elif potential_code.startswith(" ") or potential_code.startswith("\t"):
                        code_line = potential_code
                        j = i + 1

            if caret_line:
                indices = [idx for idx, ch in enumerate(caret_line) if ch in ("^", "~")]
                if indices:
                    col_start = min(indices)
                    col_end = max(indices) + 1
                    if col_start < len(code_line):
                        extracted = code_line[col_start : min(col_end, len(code_line))].strip()
                        if extracted:
                            expression = extracted

            col_range = (col_start, col_end) if col_start is not None and col_end is not None else None

            frame_entry: dict[str, Any] = {
                "frame_info": line.strip(),
                "filename": filename,
                "lineno": lineno,
                "function": function_name,
                "code": code_line.strip(),
                "col_start": col_start,
                "col_end": col_end,
                "col_range": col_range,
                "expression": expression,
                "caret_line": caret_line.strip() if caret_line else None,
            }
            frames.append(frame_entry)
            i = j
        i += 1

    return frames


`
