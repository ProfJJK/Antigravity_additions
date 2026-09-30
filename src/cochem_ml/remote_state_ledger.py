"""Append-only remote state ledger for self-healing state transitions (Task 2.18).

Every self-healing state transition is streamed synchronously into a JSON sink
on disk. Records are chained with a rolling SHA-256 hash anchored at
``GENESIS_HASH``, carry strictly monotonic sequence numbers (S_{i+1} = S_i + 1),
and embed a physical provenance digest derived from the atomic mass that the
``mendeleev`` package reports at runtime.

Durability model: each append builds a staging file in the same directory from
the already-verified bytes of the existing sink plus the single newly rendered
record (history is not re-serialised). The staging file is flushed and fsynced
once in full, then the fixed-width ``write_latency_ms`` slot of the new record
is patched in place (one tiny write plus fsync), and finally the staging file
is atomically swapped in with ``os.replace``. A corrupted or tampered sink is
never rewritten: before every append (and on every verification) the full
chain is re-verified from disk and any defect raises
``RemoteLedgerIntegrityError``.

Latency model: ``write_latency_ms`` is the wall-clock time from the start of
record construction until the staged ledger bytes are durably fsynced to disk.
The fixed-width slot patch and the atomic swap happen after the measurement.
If ``enforce_latency_ceiling`` is set and the measurement exceeds
``latency_ceiling_ms`` the staged file is discarded, nothing is committed, and
``RemoteLedgerLatencyError`` is raised.

Coverage note: the rolling hash binds predecessor hash, sequence number,
incident id, target state, state digest, payload summary and provenance
digest. ``from_state``, ``cycle`` and the timestamps are schema-validated but
are not part of the chained hash material.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .fault_ontology import (
    GENESIS_HASH,
    HEX64_PATTERN,
    StateTransitionLedgerEntry,
    get_current_utc_iso,
    is_hex_sha256,
    validate_utc_iso_timestamp,
)

__all__ = [
    "DEFAULT_LATENCY_CEILING_MS",
    "DEFAULT_REFERENCE_ELEMENT",
    "GENESIS_HASH",
    "PROVENANCE_ELEMENTS",
    "AppendOnlyRemoteLedger",
    "RemoteLedgerConfig",
    "RemoteLedgerIntegrityError",
    "RemoteLedgerLatencyError",
    "RemoteLedgerRecord",
    "RemoteLedgerStreamingError",
    "RemoteStateLedgerError",
    "StateTransitionLedgerEntry",
    "compute_mendeleev_provenance_digest",
    "compute_rolling_ledger_hash",
]

DEFAULT_LATENCY_CEILING_MS: float = 250.0
DEFAULT_REFERENCE_ELEMENT: str = "C"
PROVENANCE_ELEMENTS: Tuple[str, ...] = ("C", "N", "O", "Si")

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_S = 0.002
_NS_PER_MS = 1_000_000
# Fixed-width JSON whitespace-padded slot holding ``write_latency_ms``. A float
# repr is at most 24 characters, so the slot can always be patched in place.
_LATENCY_SLOT_WIDTH = 32
_LATENCY_FIELD = "write_latency_ms"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class RemoteStateLedgerError(Exception):
    """Base error of the remote state ledger domain."""


class RemoteLedgerIntegrityError(RemoteStateLedgerError):
    """Sequence gaps/duplicates/inversions, hash chain breaks, tampering, corrupt sinks."""


class RemoteLedgerStreamingError(RemoteStateLedgerError):
    """I/O or serialisation failure while streaming to the sink."""


class RemoteLedgerLatencyError(RemoteStateLedgerError):
    """Durable write latency exceeded the configured real-time ceiling."""

    def __init__(self, latency_ms: float, ceiling_ms: float, sequence_number: int) -> None:
        self.latency_ms = latency_ms
        self.ceiling_ms = ceiling_ms
        self.sequence_number = sequence_number
        super().__init__(
            f"streaming write latency {latency_ms:.6f} ms for sequence {sequence_number} "
            f"exceeds ceiling {ceiling_ms} ms; record was NOT committed"
        )


# ---------------------------------------------------------------------------
# Hashing & provenance
# ---------------------------------------------------------------------------
def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=128)
def compute_mendeleev_provenance_digest(symbol: str = DEFAULT_REFERENCE_ELEMENT) -> Tuple[str, float]:
    """Retrieve the atomic mass of ``symbol`` via ``mendeleev.element`` and digest it.

    Returns:
        (64-char SHA-256 hex digest, atomic mass as reported by mendeleev)

    Raises:
        ValueError: if ``symbol`` is empty.
        RuntimeError: if mendeleev is unavailable or reports a non-physical mass.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("element symbol must be a non-empty string")
    try:
        from mendeleev import element as mendeleev_element
    except ImportError as exc:
        raise RuntimeError("the 'mendeleev' package is required for physical provenance") from exc

    elem = mendeleev_element(symbol.strip())
    raw_mass = elem.mass
    if raw_mass is None:
        raise RuntimeError(f"mendeleev reports no atomic mass for {symbol!r}")
    mass = float(raw_mass)
    if not math.isfinite(mass) or mass <= 0:
        raise RuntimeError(f"mendeleev reports non-physical atomic mass {mass!r} for {symbol!r}")
    material = (
        f"mendeleev-atomic-mass|symbol={elem.symbol}|Z={int(elem.atomic_number)}|mass={mass!r}"
    )
    return _sha256_hex(material), mass


def compute_rolling_ledger_hash(
    predecessor_hash: str,
    sequence_number: int,
    incident_id: str,
    target_state: str,
    state_digest: str,
    payload_summary: str,
    physical_provenance_digest: str,
) -> str:
    """Rolling SHA-256 over the transition metadata, chained to ``predecessor_hash``.

    The payload summary is sub-hashed with SHA-256 first. Every component is
    length-prefixed (``<len>:<value>``) so that no two distinct inputs share an
    encoding.
    """
    if not is_hex_sha256(predecessor_hash):
        raise ValueError("predecessor_hash must be a 64-char lowercase hex SHA-256 digest")
    if isinstance(sequence_number, bool) or not isinstance(sequence_number, int) or sequence_number < 1:
        raise ValueError(f"sequence_number must be an integer >= 1, got {sequence_number!r}")
    payload_digest = _sha256_hex(payload_summary)
    components = (
        predecessor_hash,
        str(sequence_number),
        incident_id,
        target_state,
        state_digest,
        payload_digest,
        physical_provenance_digest,
    )
    material = "|".join(f"{len(c)}:{c}" for c in components)
    return _sha256_hex(material)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RemoteLedgerRecord(BaseModel):
    """One committed, immutable record of the remote ledger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_number: int = Field(..., ge=1)
    incident_id: str = Field(..., min_length=1)
    from_state: str = Field(..., min_length=1)
    to_state: str = Field(..., min_length=1)
    cycle: int = Field(..., ge=0)
    timestamp_iso: str
    predecessor_hash: str = Field(..., min_length=64, max_length=64, pattern=HEX64_PATTERN)
    state_digest: str = Field(..., min_length=64, max_length=64, pattern=HEX64_PATTERN)
    payload_summary: str
    physical_provenance_digest: str = Field(..., min_length=64, max_length=64, pattern=HEX64_PATTERN)
    rolling_hash: str = Field(..., min_length=64, max_length=64, pattern=HEX64_PATTERN)
    streamed_at_iso: str
    write_latency_ms: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)

    @field_validator("timestamp_iso", "streamed_at_iso")
    @classmethod
    def _check_utc_timestamp(cls, value: str) -> str:
        return validate_utc_iso_timestamp(value)


@dataclass(frozen=True, slots=True)
class RemoteLedgerConfig:
    """Immutable configuration of an ``AppendOnlyRemoteLedger``."""

    ledger_path: Path
    genesis_hash: str = GENESIS_HASH
    enforce_latency_ceiling: bool = True
    latency_ceiling_ms: float = DEFAULT_LATENCY_CEILING_MS
    reference_element: str = DEFAULT_REFERENCE_ELEMENT

    def __post_init__(self) -> None:
        if not isinstance(self.ledger_path, Path):
            object.__setattr__(self, "ledger_path", Path(self.ledger_path))
        if not is_hex_sha256(self.genesis_hash):
            raise ValueError("genesis_hash must be a 64-char lowercase hex digest")
        ceiling = float(self.latency_ceiling_ms)
        if not math.isfinite(ceiling) or ceiling <= 0:
            raise ValueError(f"latency_ceiling_ms must be a positive finite number, got {ceiling!r}")
        if not isinstance(self.reference_element, str) or not self.reference_element.strip():
            raise ValueError("reference_element must be a non-empty element symbol")


# ---------------------------------------------------------------------------
# Per-path locks (shared by every ledger instance of this process)
# ---------------------------------------------------------------------------
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _state_text(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


# ---------------------------------------------------------------------------
# Ledger engine
# ---------------------------------------------------------------------------
class AppendOnlyRemoteLedger:
    """Thread-safe, append-only, hash-chained JSON ledger of state transitions."""

    def __init__(
        self,
        ledger_path: Optional[Union[str, Path]] = None,
        config: Optional[RemoteLedgerConfig] = None,
    ) -> None:
        if config is None:
            if ledger_path is None:
                raise ValueError("either ledger_path or config must be supplied")
            config = RemoteLedgerConfig(ledger_path=Path(ledger_path))
        elif ledger_path is not None and Path(ledger_path) != config.ledger_path:
            raise ValueError(
                f"ledger_path {ledger_path!s} conflicts with config.ledger_path {config.ledger_path!s}"
            )
        self._config = config
        self._provenance_digest, self._reference_mass = compute_mendeleev_provenance_digest(
            config.reference_element
        )
        config.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for_path(config.ledger_path)
        with self._lock:
            self._ensure_sink_provisioned()
            records = self._load_records_from_disk()
            self._tail_hash = self._verify_chain(records)
            self._count = len(records)

    # -- public properties -------------------------------------------------
    @property
    def ledger_path(self) -> Path:
        return self._config.ledger_path

    @property
    def config(self) -> RemoteLedgerConfig:
        return self._config

    @property
    def provenance_digest(self) -> str:
        return self._provenance_digest

    @property
    def reference_atomic_mass(self) -> float:
        return self._reference_mass

    # -- public API --------------------------------------------------------
    def record_count(self) -> int:
        with self._lock:
            return self._count

    def get_tail_hash(self) -> str:
        with self._lock:
            return self._tail_hash

    def read_all_records(self) -> List[RemoteLedgerRecord]:
        """Return every record after full chain verification (raises on tampering)."""
        with self._lock:
            records = self._load_records_from_disk()
            tail = self._verify_chain(records)
            self._reconcile_with_memory(records, tail)
            return records

    def verify_remote_ledger_integrity(self) -> Tuple[bool, int, str]:
        """Verify monotonicity, chaining, provenance and rolling hashes from disk.

        Returns ``(True, record_count, tail_hash)``; raises
        ``RemoteLedgerIntegrityError`` on any defect. The sink is never modified.
        """
        with self._lock:
            records = self._load_records_from_disk()
            tail = self._verify_chain(records)
            self._reconcile_with_memory(records, tail)
            return True, len(records), tail

    def stream_transition(self, transition_entry: StateTransitionLedgerEntry) -> RemoteLedgerRecord:
        """Append one transition, durably and atomically, returning the committed record."""
        if not isinstance(transition_entry, StateTransitionLedgerEntry):
            raise TypeError(
                "stream_transition expects a StateTransitionLedgerEntry, "
                f"received {type(transition_entry).__name__}"
            )
        with self._lock:
            raw = self._read_raw()
            records = self._parse_records(raw)
            disk_tail = self._verify_chain(records)
            self._reconcile_with_memory(records, disk_tail)

            sequence_number = len(records) + 1
            started_ns = time.perf_counter_ns()
            incident_id = transition_entry.incident_id
            to_state = _state_text(transition_entry.to_state)
            rolling_hash = compute_rolling_ledger_hash(
                disk_tail,
                sequence_number,
                incident_id,
                to_state,
                transition_entry.state_digest,
                transition_entry.payload_summary,
                self._provenance_digest,
            )
            draft = RemoteLedgerRecord(
                sequence_number=sequence_number,
                incident_id=incident_id,
                from_state=_state_text(transition_entry.from_state),
                to_state=to_state,
                cycle=transition_entry.cycle,
                timestamp_iso=transition_entry.timestamp_iso,
                predecessor_hash=disk_tail,
                state_digest=transition_entry.state_digest,
                payload_summary=transition_entry.payload_summary,
                physical_provenance_digest=self._provenance_digest,
                rolling_hash=rolling_hash,
                streamed_at_iso=get_current_utc_iso(),
                write_latency_ms=0.0,
            )
            record_bytes, slot_in_record = self._render_record(draft)
            prefix = self._append_prefix(raw, has_records=bool(records))
            staged_bytes = prefix + b"\n" + record_bytes + b"\n]"
            slot_offset = len(prefix) + 1 + slot_in_record

            staging = self._new_staging_path()
            try:
                self._write_durably(staging, staged_bytes)
                latency_ms = (time.perf_counter_ns() - started_ns) / _NS_PER_MS
                if self._config.enforce_latency_ceiling and latency_ms > self._config.latency_ceiling_ms:
                    raise RemoteLedgerLatencyError(
                        latency_ms, self._config.latency_ceiling_ms, sequence_number
                    )
                final = RemoteLedgerRecord(**{**draft.model_dump(), _LATENCY_FIELD: latency_ms})
                self._patch_latency_slot(staging, slot_offset, latency_ms)
                self._commit_staging(staging)
            except OSError as exc:
                raise RemoteLedgerStreamingError(
                    f"failed to persist sequence {sequence_number} to {self.ledger_path}: {exc}"
                ) from exc
            finally:
                staging.unlink(missing_ok=True)

            self._count = sequence_number
            self._tail_hash = rolling_hash
            return final

    # -- internals ---------------------------------------------------------
    def _ensure_sink_provisioned(self) -> None:
        path = self.ledger_path
        if path.exists():
            if not path.is_file():
                raise RemoteLedgerIntegrityError(f"ledger sink {path} exists but is not a regular file")
            return
        staging = self._new_staging_path()
        try:
            self._write_durably(staging, self._serialise([]))
            self._commit_staging(staging)
        except OSError as exc:
            raise RemoteLedgerStreamingError(f"failed to provision ledger sink {path}: {exc}") from exc
        finally:
            staging.unlink(missing_ok=True)

    def _read_raw(self) -> bytes:
        path = self.ledger_path
        if not path.exists():
            raise RemoteLedgerIntegrityError(f"ledger sink {path} has disappeared")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise RemoteLedgerStreamingError(f"cannot read ledger sink {path}: {exc}") from exc

    def _parse_records(self, raw: bytes) -> List[RemoteLedgerRecord]:
        path = self.ledger_path
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteLedgerIntegrityError(f"ledger sink {path} is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise RemoteLedgerIntegrityError(
                f"ledger sink {path} must hold a JSON array, found {type(payload).__name__}"
            )
        records: List[RemoteLedgerRecord] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise RemoteLedgerIntegrityError(f"ledger entry at index {index} is not a JSON object")
            try:
                records.append(RemoteLedgerRecord.model_validate(item))
            except ValidationError as exc:
                raise RemoteLedgerIntegrityError(
                    f"ledger entry at index {index} violates the record schema: {exc}"
                ) from exc
        return records

    def _load_records_from_disk(self) -> List[RemoteLedgerRecord]:
        return self._parse_records(self._read_raw())

    def _verify_chain(self, records: List[RemoteLedgerRecord]) -> str:
        predecessor = self._config.genesis_hash
        all_sequences = [r.sequence_number for r in records]
        seen: set = set()
        for index, record in enumerate(records):
            expected = index + 1
            actual = record.sequence_number
            if actual != expected:
                if actual in seen:
                    kind = "duplicate sequence index"
                elif actual < expected:
                    kind = "sequence inversion"
                elif expected in all_sequences[index + 1:]:
                    kind = "sequence inversion (out-of-order records)"
                else:
                    kind = "sequence gap (missing record)"
                raise RemoteLedgerIntegrityError(
                    f"sequence monotonicity violated at index {index}: expected S={expected} "
                    f"(S_i+1 = S_i + 1), found S={actual} [{kind}]"
                )
            seen.add(actual)
            if record.predecessor_hash != predecessor:
                raise RemoteLedgerIntegrityError(
                    f"hash chain broken at sequence {actual}: predecessor_hash "
                    f"{record.predecessor_hash} != expected {predecessor}"
                )
            if record.physical_provenance_digest != self._provenance_digest:
                raise RemoteLedgerIntegrityError(
                    f"physical provenance digest mismatch at sequence {actual}: "
                    f"{record.physical_provenance_digest} != mendeleev digest "
                    f"{self._provenance_digest} ({self._config.reference_element})"
                )
            recomputed = compute_rolling_ledger_hash(
                record.predecessor_hash,
                record.sequence_number,
                record.incident_id,
                record.to_state,
                record.state_digest,
                record.payload_summary,
                record.physical_provenance_digest,
            )
            if recomputed != record.rolling_hash:
                raise RemoteLedgerIntegrityError(
                    f"rolling hash mismatch at sequence {actual}: stored {record.rolling_hash}, "
                    f"recomputed {recomputed} (record content was altered)"
                )
            predecessor = record.rolling_hash
        return predecessor

    def _reconcile_with_memory(self, records: List[RemoteLedgerRecord], disk_tail: str) -> None:
        """Detect truncation/rewrites relative to what this instance committed; adopt appends."""
        disk_count = len(records)
        if disk_count < self._count:
            raise RemoteLedgerIntegrityError(
                f"ledger truncated: disk holds {disk_count} records but {self._count} were committed"
            )
        if self._count > 0 and records[self._count - 1].rolling_hash != self._tail_hash:
            raise RemoteLedgerIntegrityError(
                f"ledger history rewritten: record {self._count} no longer carries committed "
                f"tail hash {self._tail_hash}"
            )
        self._count = disk_count
        self._tail_hash = disk_tail

    def _new_staging_path(self) -> Path:
        path = self.ledger_path
        return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")

    @staticmethod
    def _serialise(records: List[RemoteLedgerRecord]) -> bytes:
        return json.dumps(
            [r.model_dump(mode="json") for r in records], indent=2, ensure_ascii=False
        ).encode("utf-8")

    def _append_prefix(self, raw: bytes, has_records: bool) -> bytes:
        """Verified sink bytes up to (excluding) the closing ``]``, ready for one more element.

        ``raw`` has already been parsed as a JSON array, so its last
        non-whitespace byte is the closing bracket.
        """
        stripped = raw.rstrip()
        if not stripped.endswith(b"]"):
            raise RemoteLedgerIntegrityError(
                f"ledger sink {self.ledger_path} does not terminate with a JSON array bracket"
            )
        body = stripped[:-1].rstrip()
        return body + (b"," if has_records else b"")

    @staticmethod
    def _render_record(record: RemoteLedgerRecord) -> Tuple[bytes, int]:
        """Render one array element; return its bytes and the byte offset of the latency slot."""
        fields = record.model_dump(mode="json")
        fields.pop(_LATENCY_FIELD)
        head = json.dumps(fields, indent=2, ensure_ascii=False)[:-2]  # drop the closing "\n}"
        indented_head = "\n".join("  " + line for line in head.split("\n"))
        lead = indented_head + f',\n    "{_LATENCY_FIELD}": '
        slot = repr(float(0.0)).ljust(_LATENCY_SLOT_WIDTH)
        text = lead + slot + "\n  }"
        return text.encode("utf-8"), len(lead.encode("utf-8"))

    @staticmethod
    def _patch_latency_slot(target: Path, offset: int, latency_ms: float) -> None:
        text = repr(float(latency_ms)).ljust(_LATENCY_SLOT_WIDTH)
        with open(target, "r+b") as handle:
            handle.seek(offset)
            handle.write(text.encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_durably(target: Path, payload: bytes) -> None:
        with open(target, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def _commit_staging(self, staging: Path) -> None:
        for attempt in range(1, _REPLACE_ATTEMPTS + 1):
            try:
                os.replace(staging, self.ledger_path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS:
                    raise
                time.sleep(_REPLACE_BACKOFF_S * attempt)
