# src/cochem/dsp/pedagogy/canvas_sync.py

`python
"""Assignment and Gradebook Synchronizer (MC-DSP-26)."""
from __future__ import annotations
import json
from pathlib import Path
import time
from typing import Any


def sync_grades(course_id: int, grades: list[dict[str, Any]], client: Any = None) -> int:
    """Pushes student submissions and grades to Canvas gradebook or persists to local gradebook ledger."""
    if not isinstance(course_id, int) or course_id <= 0:
        raise ValueError(f"Course ID must be a positive integer, got: {course_id}")
    if not grades:
        return 0
        
    synced_count = 0
    validated_records: list[dict[str, Any]] = []
    
    for g in grades:
        student_id = g.get("student_id")
        assignment_id = g.get("assignment_id")
        score = g.get("score")
        comment = g.get("comment", "")
        
        if student_id is None or assignment_id is None or score is None:
            raise ValueError(f"Grade record missing required fields (student_id, assignment_id, score): {g}")
        if not isinstance(student_id, int) or student_id <= 0:
            raise ValueError(f"student_id must be a positive integer, got: {student_id}")
        if not isinstance(assignment_id, int) or assignment_id <= 0:
            raise ValueError(f"assignment_id must be a positive integer, got: {assignment_id}")
        if not isinstance(score, (int, float)) or score < 0:
            raise ValueError(f"score must be a non-negative number, got: {score}")

        if client is not None and hasattr(client, "submit_grade"):
            try:
                client.submit_grade(course_id, assignment_id, student_id, float(score), str(comment))
                synced_count += 1
            except Exception as sync_err:
                validated_records.append({"sync_error": str(sync_err), "student_id": student_id})
        else:
            synced_count += 1

        validated_records.append({
            "course_id": course_id,
            "assignment_id": assignment_id,
            "student_id": student_id,
            "score": float(score),
            "comment": str(comment),
            "synced_at": int(time.time()),
        })

    # Persist audit trail of synchronized grades
    ledger_dir = Path(r"D:\__CoChem\__agentic\v4.1.2\.evidence\grades")
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = ledger_dir / f"course_{course_id}_ledger.json"
        existing = []
        if ledger_file.exists():
            try:
                existing = json.loads(ledger_file.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.extend(validated_records)
        ledger_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError as io_err:
        validated_records.append({"io_error": str(io_err)})

    return synced_count


`
