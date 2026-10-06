"""Read-only, standard-library observations for an independently running supervisor.

No pipeline modules, provider clients, credentials, prompts, artifacts or auth
logs are imported/read. SQLite uses ``mode=ro`` and ``query_only``; normal WAL
reader coordination may use SQLite's shared-memory sidecar.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

_MAX_ROWS = 256
_MAX_ERROR = 2048
_SECRET_NAME = r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|auth(?:orization)?|password|passwd|secret|credential|bearer|token)"
_CATEGORIES = (
    ("auth", False, "Subscription authentication or authorization requires operator attention", (
        r"\b(?:unauthorized|unauthenticated|authentication failed|invalid credentials|not logged in|login required|subscription login unverified|invalid api key|expired token|access token expired|401|403)\b",
        r"\b(?:logonuser|credential manager|batch logon|sebatchlogonright)\b",
    )),
    ("quota", False, "Provider quota, rate or subscription usage limit prevents execution", (
        r"\b(?:quota|rate.?limit|usage limit|credit balance|insufficient credits|billing|payment required|429|too many requests)\b",
    )),
    ("provider", False, "Provider or network availability prevents execution", (
        r"\b(?:service unavailable|bad gateway|gateway timeout|provider unavailable|overloaded|temporarily unavailable|connection refused|connection reset|connectionerror|connection timed out|network unreachable|dns|name resolution|502|503|504)\b",
        r"\b(?:provider|api|upstream)\b.{0,40}\b(?:outage|unavailable|timeout|timed out)\b",
    )),
    ("resource", False, "Host resources or a resource admission limit prevent execution", (
        r"\b(?:out of memory|memoryerror|cannot allocate memory|no space left|disk full|free disk|free memory|insufficient memory|resource exhausted|too many open files|cpu utilization|database is locked|database is busy|quotaexceedederror)\b",
    )),
    ("compatibility", True, "A native CLI, output protocol or database version contract is incompatible", (
        r"\b(?:unknown|unrecognized|unsupported|unexpected|invalid)\b.{0,40}\b(?:arguments?|options?|flags?|protocol|output format|schema version)\b",
        r"\b(?:no such column|no such table|schema mismatch|protocol mismatch|version mismatch|different model than configured|missing.*terminal.*result)\b",
        r"\b(?:jsondecodeerror|did not return a terminal result|missing.*session id|missing.*thread.started|output must be a single structured object|unsupported.*result protocol)\b",
        r"\b(?:did not return valid jsonl?|invalid event record|completed without an agent_message result|must return a native session id)\b",
    )),
    ("configuration", False, "Deployment configuration or a required prerequisite needs operator attention", (
        r"\b(?:filenotfounderror|permissionerror|permission denied|access denied|executable.*not found|file.*not found|not installed|not configured|missing configuration|invalid configuration|defender exclusion|acl|dedicated.*identity|worker identity|requires.*system|private directory|code path)\b",
    )),
    ("code", True, "A reproducible implementation exception prevents pipeline execution", (
        r"\b(?:syntaxerror|indentationerror|nameerror|attributeerror|typeerror|keyerror|indexerror|unboundlocalerror|assertionerror|modulenotfounderror|importerror|recursionerror|zerodivisionerror|operationalerror|integrityerror)\b",
        r"\b(?:traceback \(most recent call last\)|stale or unowned attempt|chapter ownership mismatch|observer did not stop)\b",
        r"\b(?:object has no attribute|object is not (?:subscriptable|iterable|callable)|cannot import name|no module named|name .{1,100} is not defined|missing .{0,30}required positional arguments?)\b",
    )),
)


def redact_diagnostic(text: str, limit: int = 512) -> str:
    """Bound diagnostic text and remove credentials and common payload echoes."""
    value = str(text)[:_MAX_ERROR]
    value = re.sub(r"(?i)\bbearer\s+[^\s,;\"']+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)\b(?:sk|ghp|github_pat|AIza)[-_A-Za-z0-9]{10,}\b", "[REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?\b", "[REDACTED]", value)
    value = re.sub(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", value)
    # Quoted, JSON and command-line assignments; include the entire value even
    # when a password contains spaces. Unquoted values end at a separator.
    names = _SECRET_NAME + r"|prompt|objective|artifact(?:_text)?|payload|response|content"
    pattern = r"(?i)([\"']?(?:" + names + r")[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
    value = re.sub(pattern, r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(--(?:" + _SECRET_NAME + r")\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)", r"\1[REDACTED]", value)
    # A payload echo can be a nested JSON object rather than one quoted value.
    # Drop its entire suffix instead of trying to parse arbitrary request data.
    value = re.sub(r"(?is)([\"']?(?:prompt|objective|artifact(?:_text)?|payload|response|content)[\"']?\s*[:=]).*",
                   r"\1[REDACTED]", value)
    # Never echo traceback source lines: they can contain embedded request data.
    value = " ".join(line.strip() for line in value.splitlines() if not line.startswith(("    ", "\t")))
    return re.sub(r"[\x00-\x1f\x7f]", " ", value)[:limit]


def _normalized(text: str) -> str:
    value = redact_diagnostic(text, _MAX_ERROR).casefold()
    value = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{24,64}\b", "<id>", value)
    value = re.sub(r"\b(?:job|workflow|attempt)(?:_id)?[\s:=\"']+[^\s,;\"']+", "identity=<id>", value)
    value = re.sub(r"\b\d{4}-\d\d-\d\d[t ][\d:.+z-]+", "<time>", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", value)
    return " ".join(value.split())


def classify_error(text: str) -> dict:
    """Classify evidence conservatively; unknown failures never authorize spend."""
    if not isinstance(text, str):
        raise TypeError("Error text must be a string")
    diagnostic = redact_diagnostic(text)
    # External-blocker matches are conservative. A repair-enabling match must
    # survive redaction so quoted request/response echoes cannot authorize it.
    for category, repairable, summary, patterns in _CATEGORIES:
        source = diagnostic if repairable else text[:_MAX_ERROR]
        if any(re.search(pattern, source, re.IGNORECASE) for pattern in patterns):
            return {"category": category, "repairable": repairable, "summary": summary, "diagnostic": diagnostic}
    return {"category": "configuration", "repairable": False,
            "summary": "Unclassified failure requires diagnosis before any model repair is authorized",
            "diagnostic": diagnostic}


def _incident(category: str, repairable: bool, summary: str, evidence: dict, signature: str) -> dict:
    material = json.dumps([category, _normalized(signature)], separators=(",", ":"), ensure_ascii=True)
    return {"fingerprint": hashlib.sha256(material.encode("ascii")).hexdigest(),
            "category": category, "repairable": repairable, "summary": summary, "evidence": evidence}


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _heartbeat(root: Path, now: float, timeout: float) -> tuple[dict, list[dict]]:
    path = root / "supervisor_status.json"
    info = {"state": "missing", "age_seconds": None, "capacity": None, "active_count": 0}
    if not path.exists():
        return info, [_incident("code", True, "The pipeline has not published a supervisor heartbeat",
                               {"source": "supervisor_status.json"}, "heartbeat missing")]
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError("Heartbeat must be a bounded regular file")
        with path.open("r", encoding="utf-8") as stream:
            data = json.loads(stream.read(65537))
        if (not isinstance(data, dict) or not isinstance(data.get("instance_id"), str)
                or not data["instance_id"] or len(data["instance_id"]) > 128
                or type(data.get("pid")) is not int or data["pid"] < 1
                or type(data.get("sequence")) is not int or data["sequence"] < 0
                or not _number(data.get("timestamp")) or not _number(data.get("process_started_at"))
                or data["process_started_at"] > data["timestamp"]
                or not isinstance(data.get("version"), str) or len(data["version"]) > 64
                or not isinstance(data.get("status"), (str, dict))):
            raise ValueError("Heartbeat does not match the supervisor version contract")
        age = now - data["timestamp"]
        if age < -5:
            raise ValueError("Heartbeat timestamp is in the future; check the host clock")
        info.update(state="fresh" if age <= timeout else "stale", age_seconds=round(max(age, 0), 3),
                    pid=data["pid"], sequence=data["sequence"])
        status = data["status"]
        if isinstance(status, dict):
            if type(status.get("active_count")) is int and status["active_count"] >= 0:
                info["active_count"] = status["active_count"]
            elif isinstance(status.get("active"), list):
                info["active_count"] = len(status["active"])
            hardware = status.get("hardware", status)
            if isinstance(hardware, dict) and type(hardware.get("capacity")) is int and hardware["capacity"] >= 0:
                info["capacity"] = hardware["capacity"]
        if age > timeout:
            return info, [_incident("code", True, "The pipeline heartbeat is stale",
                {"age_seconds": round(age, 3), "timeout_seconds": timeout}, "heartbeat stale")]
        return info, []
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        info["state"] = "invalid"
        category = "configuration" if isinstance(exc, OSError) or "future" in str(exc) else "compatibility"
        return info, [_incident(category, category == "compatibility", "Supervisor heartbeat cannot be validated",
            {"source": "supervisor_status.json", "diagnostic": redact_diagnostic(str(exc))}, "invalid heartbeat " + type(exc).__name__)]


def read_observation(private_root: str | Path, now: float | None = None, heartbeat_timeout: float = 30,
                     stall_timeout: float = 600, repeated_failures: int = 3) -> dict:
    """Read bounded heartbeat/job metadata and return stable actionable incidents.

    Evidence is a snapshot, not proof of provider execution. Repeated failures
    authorize code/compatibility repair only when they match an explicit class;
    auth, quota, provider, resource, configuration and unknown errors stay held.
    """
    observed = time.time() if now is None else now
    for name, value in (("now", observed), ("heartbeat_timeout", heartbeat_timeout), ("stall_timeout", stall_timeout)):
        if not _number(value) or value < 0 or (name != "now" and value == 0):
            raise ValueError(f"{name} must be a finite positive number")
    if type(repeated_failures) is not int or repeated_failures < 1:
        raise ValueError("repeated_failures must be a positive integer")
    root = Path(private_root).expanduser().absolute()
    heartbeat, incidents = _heartbeat(root, observed, heartbeat_timeout)
    health = {"state": "healthy", "heartbeat": heartbeat["state"], "database": "missing",
              "observed_at": observed, "heartbeat_age_seconds": heartbeat["age_seconds"],
              "hardware_capacity": heartbeat["capacity"], "job_counts": {}}
    database = root / "job_board.db"
    if not database.is_file() or database.is_symlink():
        incidents.append(_incident("configuration", False, "The configured pipeline job database is unavailable",
                                   {"source": "job_board.db"}, "job database missing"))
    else:
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")
                # Neither payload/output/receipt JSON nor artifact tables are queried.
                counts = conn.execute("SELECT status,count(*) FROM pipeline_jobs WHERE kind<>'MACRO_PLANNING_REQUEST' GROUP BY status").fetchall()
                health["job_counts"] = {str(row[0]): row[1] for row in counts if row[0] in {
                    "PENDING", "PENDING_RETRY", "IN_PROGRESS", "FAILED", "BLOCKED", "COMPLETED"}}
                rows = conn.execute("""SELECT kind,status,attempts,updated_at,lease_expires_at,
                    substr(error,1,?) AS error FROM pipeline_jobs
                    WHERE kind<>'MACRO_PLANNING_REQUEST' AND status IN ('FAILED','PENDING','PENDING_RETRY','IN_PROGRESS')
                    ORDER BY updated_at DESC LIMIT ?""", (_MAX_ERROR, _MAX_ROWS)).fetchall()
                # Events provide bounded scheduler activity evidence without
                # reading details_json, which may contain arbitrary payloads.
                events = conn.execute("SELECT event,timestamp FROM pipeline_events ORDER BY id DESC LIMIT ?", (_MAX_ROWS,)).fetchall()
                health["database"] = "readable"
                health["sampled_jobs"] = len(rows)
                health["sampled_events"] = len(events)
                event_times = [row[1] for row in events if _number(row[1])]
                health["last_event_age_seconds"] = max(0, observed - max(event_times)) if event_times else None
            groups: dict[str, dict] = {}
            for row in rows:
                status, kind = row["status"], row["kind"]
                age = observed - row["updated_at"] if _number(row["updated_at"]) else None
                error = row["error"]
                classification = None
                if status in ("FAILED", "PENDING_RETRY"):
                    error = error if isinstance(error, str) and error.strip() else "Failure did not include a diagnostic"
                    classification = classify_error(error)
                    normalized = _normalized(error)
                    entry = groups.setdefault(normalized, {"classification": classification, "count": 0, "attempts": 0, "kinds": set(), "latest_failure_at": None})
                    entry["count"] += 1
                    entry["attempts"] = max(entry["attempts"], row["attempts"] if type(row["attempts"]) is int else 0)
                    if _number(row["updated_at"]):
                        entry["latest_failure_at"] = max(entry["latest_failure_at"] or 0, row["updated_at"])
                    if kind in {"MANIFEST_GENERATOR", "CHAPTER_DRAFT", "SYNTHESIS"}:
                        entry["kinds"].add(kind)
                if status == "IN_PROGRESS" and _number(row["lease_expires_at"]) and row["lease_expires_at"] < observed:
                    incidents.append(_incident("code", True, "An execution lease expired without scheduler recovery",
                        {"kind": kind, "expired_seconds": round(observed - row["lease_expires_at"], 3)}, "expired execution lease"))
                elif (age is not None and age > stall_timeout and status in {"IN_PROGRESS", "PENDING", "PENDING_RETRY"}
                      and not (classification is not None and not classification["repairable"])):
                    busy = heartbeat["active_count"] >= (heartbeat["capacity"] or 1)
                    limited = status != "IN_PROGRESS" and heartbeat["state"] == "fresh" and (heartbeat["capacity"] == 0 or busy)
                    incidents.append(_incident("resource" if limited else "code", not limited,
                        "Pending work is paused by hardware admission limits" if limited else "Pipeline work has stalled beyond its configured progress timeout",
                        {"kind": kind, "status": status, "idle_seconds": round(age, 3), "capacity": heartbeat["capacity"], "last_progress_at": row["updated_at"]},
                        "hardware admission pause" if limited else "stalled " + status.casefold()))
            for signature, entry in groups.items():
                classification = entry["classification"]
                repeats = max(entry["count"], entry["attempts"])
                repairable = classification["repairable"] and repeats >= repeated_failures
                summary = classification["summary"]
                if classification["repairable"] and not repairable:
                    summary += "; waiting for repeated evidence before authorizing repair"
                incidents.append(_incident(classification["category"], repairable, summary,
                    {"observed_jobs": entry["count"], "attempts": entry["attempts"], "required_repetitions": repeated_failures,
                     "kinds": sorted(entry["kinds"]), "diagnostic": classification["diagnostic"],
                     "latest_failure_at": entry["latest_failure_at"]}, signature))
        except (sqlite3.Error, OSError) as exc:
            health["database"] = "unreadable"
            classification = classify_error(str(exc))
            incidents.append(_incident(classification["category"], classification["repairable"],
                "The pipeline database could not be read safely",
                {"source": "job_board.db", "diagnostic": classification["diagnostic"]}, "database read " + str(exc)))
    unique: dict[str, dict] = {}
    for incident in incidents:
        unique.setdefault(incident["fingerprint"], incident)
    incidents = list(unique.values())
    if incidents:
        health["state"] = "degraded" if any(incident["repairable"] for incident in incidents) else "blocked"
    return {"health": health, "incidents": incidents}
