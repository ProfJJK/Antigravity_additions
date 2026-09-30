"""Telemetry archival daemon for hive-style parquet partitions.

Each cycle scans a hot telemetry root for ``date=YYYY-MM-DD`` partition
directories whose date, parsed from the partition name, is strictly older than
``now - retention_days``. Every ``.parquet`` file in such a partition is
re-encoded with zstd into ``<cold_root>/date=YYYY-MM-DD/``. Each encoded copy is
verified before promotion: the table must be equal to the hot source, every
column chunk must report the ZSTD codec, and the SHA-256 of the promoted bytes
must match the digest computed before the write. The digests go into a
``manifest.json`` for the partition. Hot originals are deleted only after the
manifest of record has been written and re-verified against the cold files on
disk.

Guarantees:

* Cold files and manifests are staged under a ``*.tmp`` name in the destination
  directory, fsynced, and promoted with ``os.replace``. Staging files are always
  removed.
* A failure in one partition is recorded in the report. Its hot data stays
  untouched, and unverified cold files written for it are rolled back. The
  remaining partitions are still processed.
* Runs are idempotent and resumable. If a cold partition already carries a valid
  manifest, the cold copies are verified against the remaining hot sources and
  reused without being rewritten, and only the hot cleanup is completed.

Usage::

    python -m cochem_ml.telemetry_archival_daemon --hot HOT --cold COLD \
        --retention-days 30 --once
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import pyarrow as pa
import pyarrow.parquet as pq

LOGGER = logging.getLogger(__name__)

PARTITION_PATTERN = re.compile(r"^date=([0-9]{4}-[0-9]{2}-[0-9]{2})$", re.ASCII)
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT_VERSION = 1
COLD_CODEC = "zstd"
COLD_CODEC_METADATA = "ZSTD"
PARQUET_SUFFIX = ".parquet"
STAGING_SUFFIX = ".tmp"
MAX_RECORDED_ERRORS = 1000
MAX_RECORDED_REPORTS = 100
EXIT_OK = 0
EXIT_PARTITION_FAILURE = 1
EXIT_CONFIG_ERROR = 2

PathInput = Union[str, "os.PathLike[str]"]
NowInput = Optional[Union[datetime, date]]


class ArchivalError(RuntimeError):
    """A partition could not be migrated safely. Its hot data is retained."""


@dataclass
class ArchivalFailure:
    """One partition that failed to migrate during a cycle."""

    partition: str
    reason: str

    def __getitem__(self, key: str) -> str:
        if key not in ("partition", "reason"):
            raise KeyError(key)
        return getattr(self, key)

    def to_dict(self) -> Dict[str, str]:
        return {"partition": self.partition, "reason": self.reason}


@dataclass
class ArchivalReport:
    """Structured result of a single archival cycle."""

    partitions_scanned: int = 0
    partitions_migrated: int = 0
    files_migrated: int = 0
    rows_migrated: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    failures: List[ArchivalFailure] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partitions_scanned": self.partitions_scanned,
            "partitions_migrated": self.partitions_migrated,
            "files_migrated": self.files_migrated,
            "rows_migrated": self.rows_migrated,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "failures": [
                item.to_dict() if isinstance(item, ArchivalFailure) else dict(item)
                for item in self.failures
            ],
        }


@dataclass
class _PartitionOutcome:
    files: int
    rows: int
    bytes_before: int
    bytes_after: int
    resumed: bool


# --------------------------------------------------------------------------
# configuration and discovery
# --------------------------------------------------------------------------
def _check_retention_days(retention_days: Any) -> int:
    if isinstance(retention_days, bool) or not isinstance(retention_days, int):
        raise ValueError(f"retention_days must be an integer, got {retention_days!r}")
    if retention_days < 0:
        raise ValueError(f"retention_days must be >= 0, got {retention_days}")
    return retention_days


def _check_interval(interval: Any) -> float:
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ValueError(f"interval must be a number of seconds, got {interval!r}")
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError(f"interval must be a positive finite number of seconds, got {interval!r}")
    return float(interval)


def _normalised(path: Path) -> str:
    return os.path.normcase(str(path))


def _is_same_or_inside(candidate: Path, container: Path) -> bool:
    cand = _normalised(candidate)
    cont = _normalised(container)
    return cand == cont or cand.startswith(cont.rstrip(os.sep) + os.sep)


def validate_config(
    hot_root: PathInput,
    cold_root: PathInput,
    retention_days: int,
    interval: Optional[float] = None,
) -> Tuple[Path, Path]:
    """Validate the configuration without creating or modifying anything on disk.

    Returns the resolved (hot_root, cold_root) paths.
    """
    _check_retention_days(retention_days)
    if interval is not None:
        _check_interval(interval)
    if hot_root is None or not str(hot_root).strip():
        raise ValueError("hot_root must be a non-empty path")
    if cold_root is None or not str(cold_root).strip():
        raise ValueError("cold_root must be a non-empty path")

    hot_candidate = Path(hot_root)
    if not hot_candidate.exists():
        raise FileNotFoundError(f"hot_root does not exist: {hot_candidate}")
    if not hot_candidate.is_dir():
        raise ValueError(f"hot_root is not a directory: {hot_candidate}")

    hot_path = hot_candidate.resolve()
    cold_path = Path(cold_root).resolve()

    if _normalised(hot_path) == _normalised(cold_path):
        raise ValueError(f"hot_root and cold_root must be different directories; both resolve to {hot_path}")
    if _is_same_or_inside(cold_path, hot_path):
        raise ValueError(f"cold_root {cold_path} must not be nested inside hot_root {hot_path}")
    if _is_same_or_inside(hot_path, cold_path):
        raise ValueError(f"hot_root {hot_path} must not be nested inside cold_root {cold_path}")
    if cold_path.exists() and not cold_path.is_dir():
        raise ValueError(f"cold_root exists but is not a directory: {cold_path}")
    return hot_path, cold_path


def _parse_partition_date(name: str) -> Optional[date]:
    match = PARTITION_PATTERN.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _reference_date(now: NowInput) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            return now.astimezone(timezone.utc).date()
        return now.date()
    if isinstance(now, date):
        return now
    raise TypeError(f"now must be a datetime, a date or None, got {type(now).__name__}")


def _scan_partitions(hot_path: Path) -> List[Tuple[date, Path]]:
    """All conforming partition directories directly under ``hot_path``, oldest first."""
    found: List[Tuple[date, Path]] = []
    with os.scandir(hot_path) as entries:
        for entry in entries:
            partition_date = _parse_partition_date(entry.name)
            if partition_date is None:
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            found.append((partition_date, hot_path / entry.name))
    found.sort(key=lambda item: (item[0], item[1].name))
    return found


def discover_eligible_partitions(
    hot_root: PathInput,
    retention_days: int,
    now: NowInput = None,
) -> List[Path]:
    """Return the partition directories strictly older than ``now - retention_days``.

    Eligibility is derived only from the ``date=YYYY-MM-DD`` directory name.
    Filesystem timestamps are not consulted.
    """
    _check_retention_days(retention_days)
    root = Path(hot_root)
    if not root.is_dir():
        raise FileNotFoundError(f"hot_root does not exist or is not a directory: {root}")
    cutoff = _reference_date(now) - timedelta(days=retention_days)
    return [path for partition_date, path in _scan_partitions(root) if partition_date < cutoff]


# --------------------------------------------------------------------------
# low-level verification and I/O
# --------------------------------------------------------------------------
def _require_zstd() -> None:
    if not pa.Codec.is_available(COLD_CODEC):
        raise RuntimeError("this pyarrow build lacks zstd support, which cold storage requires")


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _list_parquet_files(directory: Path) -> List[Path]:
    with os.scandir(directory) as entries:
        files = [
            directory / entry.name
            for entry in entries
            if entry.name.lower().endswith(PARQUET_SUFFIX) and entry.is_file(follow_symlinks=False)
        ]
    return sorted(files, key=lambda path: path.name)


def _read_parquet_bytes(payload: bytes, label: str) -> pa.Table:
    try:
        return pq.read_table(pa.BufferReader(payload))
    except Exception as exc:
        raise ArchivalError(f"{label} is not a readable parquet file ({type(exc).__name__}: {exc})") from exc


def _verify_codec(payload: bytes, label: str) -> None:
    try:
        with pq.ParquetFile(pa.BufferReader(payload)) as parquet_file:
            metadata = parquet_file.metadata
            codecs = {
                str(metadata.row_group(rg_idx).column(col_idx).compression).upper()
                for rg_idx in range(metadata.num_row_groups)
                for col_idx in range(metadata.row_group(rg_idx).num_columns)
            }
    except ArchivalError:
        raise
    except Exception as exc:
        raise ArchivalError(f"{label}: parquet metadata unreadable ({type(exc).__name__}: {exc})") from exc
    unexpected = codecs - {COLD_CODEC_METADATA}
    if unexpected:
        raise ArchivalError(f"{label}: column chunks use {sorted(unexpected)} instead of {COLD_CODEC_METADATA}")


def _verify_digest(payload: bytes, entry: Dict[str, Any], label: str) -> None:
    if len(payload) != entry["cold_file_size"]:
        raise ArchivalError(
            f"{label}: size {len(payload)} differs from recorded cold_file_size {entry['cold_file_size']}"
        )
    digest = _sha256_hex(payload)
    if digest != entry["sha256"]:
        raise ArchivalError(f"{label}: sha256 {digest} differs from recorded {entry['sha256']}")


def _verify_cold_payload(
    payload: bytes,
    entry: Dict[str, Any],
    label: str,
    reference_table: Optional[pa.Table],
) -> None:
    _verify_digest(payload, entry, label)
    cold_table = _read_parquet_bytes(payload, label)
    if cold_table.num_rows != entry["row_count"]:
        raise ArchivalError(f"{label}: {cold_table.num_rows} rows but {entry['row_count']} recorded")
    _verify_codec(payload, label)
    if reference_table is not None:
        if not cold_table.schema.equals(reference_table.schema):
            raise ArchivalError(f"{label}: schema differs from the hot source")
        if not cold_table.equals(reference_table):
            raise ArchivalError(f"{label}: values differ from the hot source")


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError as exc:
        LOGGER.debug("could not open %s for fsync: %s", directory, exc)
        return
    try:
        os.fsync(descriptor)
    except OSError as exc:
        LOGGER.debug("directory fsync of %s failed: %s", directory, exc)
    finally:
        os.close(descriptor)


def _write_bytes_atomically(target: Path, payload: bytes) -> None:
    """Stage ``payload`` under a ``*.tmp`` name next to ``target`` and promote it with os.replace."""
    staging = target.with_name(f"{target.name}.{uuid.uuid4().hex}{STAGING_SUFFIX}")
    try:
        with open(staging, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if staging.read_bytes() != payload:
            raise ArchivalError(f"staged bytes for {target.name} do not match the encoded payload")
        os.replace(staging, target)
        _fsync_directory(target.parent)
    finally:
        if staging.exists():
            try:
                staging.unlink()
            except OSError as exc:
                LOGGER.error("could not remove staging file %s: %s", staging, exc)


def _remove_staging_files(directory: Path) -> None:
    if not directory.is_dir():
        return
    for leftover in list(directory.iterdir()):
        if leftover.is_file() and leftover.name.endswith(STAGING_SUFFIX):
            leftover.unlink()


def _remove_directory_if_empty(directory: Path) -> bool:
    try:
        with os.scandir(directory) as entries:
            occupied = next(iter(entries), None) is not None
        if occupied:
            return False
        directory.rmdir()
    except FileNotFoundError:
        return False
    except OSError as exc:
        LOGGER.debug("directory %s was kept: %s", directory, exc)
        return False
    return True


def _encode_cold_copy(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression=COLD_CODEC)
    return sink.getvalue().to_pybytes()


def _write_cold_copy(hot_table: pa.Table, hot_size: int, cold_file: Path, label: str) -> Dict[str, Any]:
    payload = _encode_cold_copy(hot_table)
    entry = {
        "name": cold_file.name,
        "row_count": hot_table.num_rows,
        "sha256": _sha256_hex(payload),
        "hot_file_size": hot_size,
        "cold_file_size": len(payload),
    }
    _verify_cold_payload(payload, entry, f"encoded cold copy {label}", hot_table)
    _write_bytes_atomically(cold_file, payload)
    _verify_digest(cold_file.read_bytes(), entry, f"promoted cold copy {label}")
    return entry


def _load_manifest(manifest_path: Path, partition: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Return the manifest entries keyed by file name, or None when no manifest exists."""
    if not manifest_path.exists():
        return None
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchivalError(f"existing manifest {manifest_path} is unreadable: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("files"), list):
        raise ArchivalError(f"existing manifest {manifest_path} has no 'files' list")
    recorded_partition = document.get("partition")
    if recorded_partition is not None and recorded_partition != partition:
        raise ArchivalError(f"manifest {manifest_path} belongs to {recorded_partition!r}, not {partition!r}")

    entries: Dict[str, Dict[str, Any]] = {}
    for item in document["files"]:
        if not isinstance(item, dict):
            raise ArchivalError(f"manifest {manifest_path} contains a non-object file entry")
        name = item.get("name")
        if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(PARQUET_SUFFIX):
            raise ArchivalError(f"manifest {manifest_path} has an invalid file name {name!r}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ArchivalError(f"manifest {manifest_path} has an invalid sha256 for {name}")
        for key in ("row_count", "cold_file_size", "hot_file_size"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ArchivalError(f"manifest {manifest_path} has an invalid {key} for {name}")
        if name in entries:
            raise ArchivalError(f"manifest {manifest_path} lists {name} twice")
        entries[name] = item
    return entries


def _manifest_payload(partition: str, entries: Dict[str, Dict[str, Any]]) -> bytes:
    document = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "partition": partition,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "codec": COLD_CODEC,
        "files": [entries[name] for name in sorted(entries)],
    }
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def _ensure_source_unchanged(hot_file: Path, recorded: Tuple[int, int]) -> None:
    current = hot_file.stat()
    if (current.st_size, current.st_mtime_ns) != recorded:
        raise ArchivalError(
            f"hot source {hot_file.name} changed during archival; it was retained for the next cycle"
        )


def _rollback_partition(
    cold_dir: Path,
    hot_names: Set[str],
    prior_names: Set[str],
    written: Set[str],
    invalid: Set[str],
    reused: Set[str],
) -> None:
    """Remove staging files and unverified cold copies for the hot names of a failed partition."""
    try:
        _remove_staging_files(cold_dir)
        for name in sorted(hot_names - reused):
            if name in written or name in invalid or name not in prior_names:
                target = cold_dir / name
                if target.is_file():
                    target.unlink()
        _remove_directory_if_empty(cold_dir)
    except OSError as exc:
        LOGGER.error("rollback of %s was incomplete: %s", cold_dir, exc)


# --------------------------------------------------------------------------
# partition migration
# --------------------------------------------------------------------------
def _migrate_partition(hot_dir: Path, cold_root: Path) -> Optional[_PartitionOutcome]:
    partition = hot_dir.name
    cold_dir = cold_root / partition
    manifest_path = cold_dir / MANIFEST_NAME
    hot_files = _list_parquet_files(hot_dir)
    prior_entries = _load_manifest(manifest_path, partition)

    if not hot_files:
        if prior_entries is None:
            LOGGER.info("partition %s contains no parquet files; left in place", partition)
            return None
        # Interrupted cleanup: every hot file is gone. Confirm the archive before pruning.
        for name, entry in prior_entries.items():
            cold_file = cold_dir / name
            if not cold_file.is_file():
                raise ArchivalError(f"manifest lists {name} but its cold copy is missing")
            _verify_cold_payload(cold_file.read_bytes(), entry, f"cold copy {partition}/{name}", None)
        if _remove_directory_if_empty(hot_dir):
            return _PartitionOutcome(0, 0, 0, 0, resumed=True)
        return None

    entries: Dict[str, Dict[str, Any]] = dict(prior_entries or {})
    prior_names = set(entries)
    hot_names = {path.name for path in hot_files}
    written: Set[str] = set()
    reused: Set[str] = set()
    invalid: Set[str] = set()
    hot_stats: Dict[str, Tuple[int, int]] = {}
    files = rows = bytes_before = bytes_after = 0

    cold_dir.mkdir(parents=True, exist_ok=True)
    try:
        _remove_staging_files(cold_dir)

        # Cold copies whose hot source was already deleted must still be intact.
        for name, entry in entries.items():
            if name in hot_names:
                continue
            cold_file = cold_dir / name
            if not cold_file.is_file():
                raise ArchivalError(f"manifest lists {name} but its cold copy is missing and no hot source remains")
            _verify_cold_payload(cold_file.read_bytes(), entry, f"cold copy {partition}/{name}", None)

        for hot_file in hot_files:
            name = hot_file.name
            label = f"{partition}/{name}"
            before = hot_file.stat()
            hot_bytes = hot_file.read_bytes()
            if len(hot_bytes) != before.st_size:
                raise ArchivalError(f"hot source {label} changed while being read")
            hot_stats[name] = (before.st_size, before.st_mtime_ns)
            hot_table = _read_parquet_bytes(hot_bytes, f"hot source {label}")

            cold_file = cold_dir / name
            entry = entries.get(name)
            cold_size = 0
            if entry is not None:
                if cold_file.is_file():
                    cold_bytes = cold_file.read_bytes()
                    try:
                        _verify_cold_payload(cold_bytes, entry, f"cold copy {label}", hot_table)
                    except ArchivalError as exc:
                        LOGGER.warning("cold copy %s failed verification (%s); rewriting from hot source", label, exc)
                        invalid.add(name)
                    else:
                        reused.add(name)
                        cold_size = len(cold_bytes)
                else:
                    invalid.add(name)

            if name not in reused:
                new_entry = _write_cold_copy(hot_table, len(hot_bytes), cold_file, label)
                entries[name] = new_entry
                written.add(name)
                cold_size = new_entry["cold_file_size"]

            files += 1
            rows += hot_table.num_rows
            bytes_before += len(hot_bytes)
            bytes_after += cold_size

        if written or prior_entries is None:
            _write_bytes_atomically(manifest_path, _manifest_payload(partition, entries))

        recorded = _load_manifest(manifest_path, partition)
        if recorded is None:
            raise ArchivalError(f"manifest for {partition} is missing after it was written")
        for name in sorted(hot_names):
            entry = recorded.get(name)
            if entry is None:
                raise ArchivalError(f"manifest for {partition} does not list {name}")
            _verify_digest((cold_dir / name).read_bytes(), entry, f"cold copy {partition}/{name}")
    except BaseException:
        _rollback_partition(cold_dir, hot_names, prior_names, written, invalid, reused)
        raise

    # Verification succeeded and the manifest of record is on disk. Now prune hot.
    for hot_file in hot_files:
        _ensure_source_unchanged(hot_file, hot_stats[hot_file.name])
    for hot_file in hot_files:
        hot_file.unlink()
    _remove_directory_if_empty(hot_dir)

    return _PartitionOutcome(
        files=files,
        rows=rows,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
        resumed=not written,
    )


def archive_partition(hot_partition_dir: PathInput, cold_root: PathInput) -> Tuple[int, int, int, int]:
    """Migrate one partition directory into ``cold_root``.

    Returns (files_migrated, rows_migrated, bytes_before, bytes_after). Raises
    ArchivalError, with hot data preserved, when the partition cannot be
    migrated safely.
    """
    hot_dir = Path(hot_partition_dir)
    if not hot_dir.is_dir():
        raise FileNotFoundError(f"hot_partition_dir does not exist or is not a directory: {hot_dir}")
    if _parse_partition_date(hot_dir.name) is None:
        raise ValueError(f"hot_partition_dir {hot_dir.name!r} is not a date=YYYY-MM-DD partition")
    hot_resolved = hot_dir.resolve()
    cold_resolved = Path(cold_root).resolve()
    if _is_same_or_inside(cold_resolved, hot_resolved) or _is_same_or_inside(hot_resolved, cold_resolved):
        raise ValueError(f"cold_root {cold_resolved} and hot_partition_dir {hot_resolved} must not overlap")
    if cold_resolved.exists() and not cold_resolved.is_dir():
        raise ValueError(f"cold_root exists but is not a directory: {cold_resolved}")
    _require_zstd()
    outcome = _migrate_partition(hot_resolved, cold_resolved)
    if outcome is None:
        return (0, 0, 0, 0)
    return (outcome.files, outcome.rows, outcome.bytes_before, outcome.bytes_after)


def run_once(
    hot_root: PathInput,
    cold_root: PathInput,
    retention_days: int,
    now: NowInput = None,
) -> ArchivalReport:
    """Run one archival cycle and return its report.

    Per-partition failures are isolated and recorded in the report.
    """
    hot_path, cold_path = validate_config(hot_root, cold_root, retention_days)
    cutoff = _reference_date(now) - timedelta(days=retention_days)
    _require_zstd()

    partitions = _scan_partitions(hot_path)
    report = ArchivalReport(partitions_scanned=len(partitions))
    for partition_date, hot_dir in partitions:
        if partition_date >= cutoff:
            continue
        try:
            outcome = _migrate_partition(hot_dir, cold_path)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            report.failures.append(ArchivalFailure(partition=hot_dir.name, reason=reason))
            LOGGER.warning("partition %s was not archived: %s", hot_dir.name, reason)
            continue
        if outcome is None:
            continue
        report.partitions_migrated += 1
        report.files_migrated += outcome.files
        report.rows_migrated += outcome.rows
        report.bytes_before += outcome.bytes_before
        report.bytes_after += outcome.bytes_after
        LOGGER.info(
            "archived %s: %d files, %d rows, %d -> %d bytes%s",
            hot_dir.name,
            outcome.files,
            outcome.rows,
            outcome.bytes_before,
            outcome.bytes_after,
            " (resumed)" if outcome.resumed else "",
        )
    return report


# --------------------------------------------------------------------------
# background runner
# --------------------------------------------------------------------------
class TelemetryArchivalDaemon:
    """Background thread that calls run_once every ``interval`` seconds."""

    def __init__(
        self,
        hot_root: PathInput,
        cold_root: PathInput,
        retention_days: int,
        interval: float = 60.0,
        now_factory: Optional[Callable[[], datetime]] = None,
        on_report: Optional[Callable[[ArchivalReport], None]] = None,
    ) -> None:
        self.hot_root, self.cold_root = validate_config(hot_root, cold_root, retention_days, interval)
        self.retention_days = retention_days
        self.interval = float(interval)
        self.now_factory = now_factory
        self.on_report = on_report
        self.reports: List[ArchivalReport] = []
        self.errors: List[BaseException] = []
        self.error_count = 0
        self._cycle_count = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def is_alive(self) -> bool:
        return self.is_running

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count

    @property
    def last_report(self) -> Optional[ArchivalReport]:
        with self._lock:
            return self.reports[-1] if self.reports else None

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("TelemetryArchivalDaemon is already running")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="telemetry-archival-daemon",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"archival thread did not stop within {timeout} seconds")

    def __enter__(self) -> "TelemetryArchivalDaemon":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    def run_cycle(self) -> ArchivalReport:
        now = self.now_factory() if self.now_factory is not None else None
        return run_once(self.hot_root, self.cold_root, self.retention_days, now=now)

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            self.errors.append(exc)
            self.error_count += 1
            if len(self.errors) > MAX_RECORDED_ERRORS:
                del self.errors[: len(self.errors) - MAX_RECORDED_ERRORS]

    def _record_report(self, report: ArchivalReport) -> None:
        with self._lock:
            self.reports.append(report)
            if len(self.reports) > MAX_RECORDED_REPORTS:
                del self.reports[: len(self.reports) - MAX_RECORDED_REPORTS]

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                report = self.run_cycle()
                self._record_report(report)
                if report.failures:
                    LOGGER.warning(
                        "archival cycle finished with %d failed partition(s)", len(report.failures)
                    )
                if self.on_report is not None:
                    self.on_report(report)
            except Exception as exc:
                self._record_error(exc)
                LOGGER.exception("archival cycle raised %s; the daemon continues", type(exc).__name__)
            finally:
                with self._lock:
                    self._cycle_count += 1
            if self._stop_event.wait(self.interval):
                break


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cochem_ml.telemetry_archival_daemon",
        description="Archive aged date=YYYY-MM-DD parquet partitions to zstd cold storage.",
    )
    parser.add_argument("--hot", required=True, help="hot telemetry root")
    parser.add_argument("--cold", required=True, help="cold storage root")
    parser.add_argument(
        "--retention-days",
        "--retention_days",
        dest="retention_days",
        type=int,
        required=True,
        help="partitions strictly older than now - N days are archived",
    )
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between cycles in daemon mode")
    parser.add_argument("--log-level", default="INFO", help="stderr logging level")
    return parser


def _emit_json(document: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.once:
        try:
            report = run_once(args.hot, args.cold, args.retention_days)
        except (ValueError, FileNotFoundError, TypeError, RuntimeError) as exc:
            sys.stderr.write(f"configuration error: {exc}\n")
            _emit_json({"error": f"{type(exc).__name__}: {exc}"})
            return EXIT_CONFIG_ERROR
        _emit_json(report.to_dict())
        return EXIT_PARTITION_FAILURE if report.failures else EXIT_OK

    try:
        daemon = TelemetryArchivalDaemon(
            args.hot,
            args.cold,
            args.retention_days,
            interval=args.interval,
            on_report=lambda cycle_report: _emit_json(cycle_report.to_dict()),
        )
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"configuration error: {exc}\n")
        _emit_json({"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_CONFIG_ERROR

    daemon.start()
    try:
        while daemon.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        LOGGER.info("interrupt received; stopping archival daemon")
    finally:
        daemon.stop(timeout=max(5.0, daemon.interval))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
