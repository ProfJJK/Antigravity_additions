"""Bounded metadata preview of explicitly pinned legacy SQLite snapshots.

This does not import jobs or initialize a JobStore. Original whole databases
remain the authority for payloads, quotas, leases, results and historical rules.
Every unfinished row remains held outside the executable queue. The returned
map contains private source identities and must be saved only in private staging.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time

from .ramdisk import ordinary_tree

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_MAX_DB = 256 * 1048576
_JOB_FIELDS = {"id", "status", "attempts", "max_attempts", "parent_job_id", "fencing_token"}


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate snapshot manifest field")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite manifest value")))


def _ordinary_file(path: Path, maximum: int):
    ordinary_tree(path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or not 0 < metadata.st_size <= maximum:
        raise ValueError("Expected a bounded ordinary single-link snapshot file")
    return metadata


def _checked_read(path: Path, expected: str, maximum: int):
    if not isinstance(expected, str) or not _SHA.fullmatch(expected):
        raise ValueError("Expected a full SHA256 snapshot pin")
    before = _ordinary_file(path, maximum)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    actual = hashlib.sha256(raw).hexdigest()
    after = _ordinary_file(path, maximum)
    if len(raw) > maximum or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or actual != expected:
        raise ValueError("Pinned snapshot bytes changed")
    return raw


def _sidecars_absent(path):
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("Only closed snapshots without SQLite sidecars may be inspected")


def _reference(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _scalar(value):
    return type(value) is int or isinstance(value, str) and 0 < len(value) <= 1024


def _inspect(path, raw, source, snapshot_sha, *, maximum_jobs, deadline):
    # Restrict the query vocabulary, including column reads. Job payloads, rule
    # text, quota values and any application SQL are never selected or executed.
    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ:
            if table in {"sqlite_master", "sqlite_schema"} or table == "jobs" and column in _JOB_FIELDS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and table in {"table_info", "integrity_check"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    # Parse precisely the hash-verified buffer, never reopen a pathname after
    # its custody check. A concurrent writer swapping and restoring the source
    # cannot make SQLite select different rows under the original hash.
    if raw[:16] != b'SQLite format 3\0' or raw[18:20] not in (b'\1\1', b'\2\2'):
        raise ValueError('Snapshot header is not a supported SQLite database')
    wal_header = raw[18:20] == b'\2\2'
    with closing(sqlite3.connect(':memory:')) as database:
        if not hasattr(database, 'deserialize'):
            raise ValueError('Snapshot preview requires SQLite deserialize support')
        # SQLite documents this in-memory-only header adjustment for a closed,
        # consistent WAL-mode backup. Original snapshot bytes stay unchanged:
        # https://www.sqlite.org/c3ref/deserialize.html
        database.deserialize(raw[:18] + b'\1\1' + raw[20:] if wal_header else raw)
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA query_only=ON")
        database.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        # SQLite's built-in FTS integrity path initializes its virtual-table
        # module and reads internal shadow tables. It is a bounded engine check,
        # not a query selecting historical document or rule text. Restrict the
        # application query vocabulary after this engine check.
        if database.execute("PRAGMA integrity_check").fetchmany(2) != [("ok",)]:
            raise ValueError("Snapshot integrity check failed")
        database.set_authorizer(authorize)
        objects = dict(database.execute("SELECT name,type FROM sqlite_schema WHERE name IN ('jobs','pipeline_jobs')"))
        if "pipeline_jobs" in objects:
            raise ValueError("A modern pipeline database is not a legacy import source")
        namespace = _reference({"source_identity": source, "snapshot_sha256": snapshot_sha})
        result = {"source_identity": source, "snapshot": path.name, "sha256": snapshot_sha,
                  "reference_namespace": namespace, "integrity_check": "ok", "rows": [],
                  "archive_action": "PRESERVE_WHOLE_DATABASE", "automatic_import": False,
                  "query_source": "HASH_VERIFIED_BUFFER_IN_MEMORY",
                  "in_memory_wal_header_normalized": wal_header}
        if "jobs" not in objects:
            result["kind"] = "HISTORICAL_DATABASE_ONLY"
            return result
        if objects["jobs"] != "table":
            raise ValueError("Legacy jobs must be a physical table")
        columns = database.execute("PRAGMA table_info(jobs)").fetchall()
        available = {row[1] for row in columns}
        if not {"id", "status", "attempts"} <= available or [row[1] for row in columns if row[5]] != ["id"]:
            raise ValueError("Unsupported legacy job identity/schema; preserve without importing")
        fields = [name for name in ("id", "status", "attempts", "max_attempts", "parent_job_id", "fencing_token") if name in available]
        rows = database.execute('SELECT ' + ','.join('"' + name + '"' for name in fields) + ' FROM jobs ORDER BY id LIMIT ?',
                                (maximum_jobs + 1,)).fetchall()
        if len(rows) > maximum_jobs:
            raise ValueError("Legacy job preview exceeds the reviewed row bound")
        seen = set()
        for values in rows:
            row = dict(zip(fields, values))
            if not _scalar(row["id"]) or not isinstance(row["status"], str) or not 0 < len(row["status"]) <= 64:
                raise ValueError("Legacy row identity/status is not bounded metadata")
            for field in ("attempts", "max_attempts"):
                if field in row and row[field] is not None and (type(row[field]) is not int or row[field] < 0):
                    raise ValueError("Legacy attempt metadata requires explicit reconciliation")
            reference = "legacy:" + namespace + ":" + _reference(row["id"])
            if reference in seen:
                raise ValueError("Ambiguous legacy primary key")
            seen.add(reference)
            # Keep actual historic attempts, including unknown limits. No zero
            # default, new retry allowance, native route or success attestation.
            metadata = {key: row.get(key) for key in ("id", "status", "attempts", "max_attempts")}
            metadata.update(parent_reference_sha256=_reference(row["parent_job_id"]) if row.get("parent_job_id") is not None else None,
                            fencing_token_sha256=_reference(row["fencing_token"]) if row.get("fencing_token") is not None else None)
            result["rows"].append({"reference": reference, "original": metadata,
                "disposition": "HISTORICAL_COMPLETION_ONLY" if row["status"] == "COMPLETED" else "LEGACY_IMPORT_HOLD",
                "executable": False, "counts_as_current_acceptance": False,
                "release_requires": ["quiesced_source_lineage", "immutable_request_and_result_mapping",
                                     "current_chapter_06_capture", "preserved_attempt_and_quota_policy"]})
        result["kind"] = "LEGACY_JOB_METADATA"
        return result


def preview_legacy_archive(manifest_path, expected_sha256, *, maximum_jobs=10000, deadline_seconds=30):
    """Validate whole snapshots and return a held continuation map; write nothing.

    The explicit manifest digest is the trust boundary. Only an existing
    COMPLETE_PER_DATABASE backup manifest is supported, never a live DB path.
    This is not a cross-database consistency or repair-budget authority claim.
    """
    if type(maximum_jobs) is not int or not 1 <= maximum_jobs <= 10000 or type(deadline_seconds) not in (int, float) or not 1 <= deadline_seconds <= 120:
        raise ValueError("Invalid bounded preview limits")
    path = Path(manifest_path).absolute()
    manifest = _json(_checked_read(path, expected_sha256, 1048576))
    if (not isinstance(manifest, dict) or manifest.get("schema") != "cochem-legacy-online-snapshots/1"
            or manifest.get("status") != "COMPLETE_PER_DATABASE"
            or not isinstance(manifest.get("created_at_utc"), str)
            or not isinstance(manifest.get("snapshots"), list) or not 1 <= len(manifest["snapshots"]) <= 32):
        raise ValueError("An explicitly completed snapshot manifest is required")
    inputs = []; seen = set(); total_bytes = 0
    for row in manifest["snapshots"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid snapshot record")
        name = row.get("snapshot"); source = row.get("source")
        if (not isinstance(name, str) or not re.fullmatch(r"[0-9]{2}-[A-Za-z0-9_.-]+\.db", name)
                or name.casefold() in seen or not isinstance(source, str) or not 1 <= len(source) <= 4096
                or type(row.get("bytes")) is not int or not 0 < row["bytes"] <= _MAX_DB
                or row.get("integrity_check") != "ok"):
            raise ValueError("Snapshot path/identity/size differs from the reviewed format")
        seen.add(name.casefold()); target = path.parent / name
        total_bytes += row['bytes']
        if total_bytes > 512 * 1048576:
            raise ValueError("Snapshot set exceeds the bounded archive preview")
        raw = _checked_read(target, row.get("sha256"), _MAX_DB)
        if len(raw) != row["bytes"]:
            raise ValueError("Snapshot size differs from capture")
        _sidecars_absent(target); inputs.append((target, row))
    _checked_read(path, expected_sha256, 1048576)
    deadline = time.monotonic() + deadline_seconds
    snapshots = []
    for target, row in inputs:
        if time.monotonic() > deadline:
            raise TimeoutError("Legacy preview deadline exceeded")
        raw = _checked_read(target, row['sha256'], _MAX_DB)
        snapshots.append(_inspect(target, raw, row["source"], row["sha256"], maximum_jobs=maximum_jobs, deadline=deadline))
        _sidecars_absent(target); _checked_read(target, row["sha256"], _MAX_DB)
    _checked_read(path, expected_sha256, 1048576)
    for target, row in inputs:
        _sidecars_absent(target); _checked_read(target, row['sha256'], _MAX_DB)
    return {"schema": "cochem-held-legacy-archive-preview/1", "read_only": True,
        "snapshot_manifest_sha256": expected_sha256, "snapshot_capture_utc": manifest["created_at_utc"],
        "cross_database_point_in_time_verified": False, "live_databases_opened": False,
        "private_output": True, "repair_budget_authority": "UNRESOLVED_LEGACY_AUTHORITY",
        "repair_budget_imported_or_reset": False, "modern_jobs_created": 0,
        "payloads_or_rule_text_selected": False, "snapshots": snapshots}
