"""Safe lock reclamation protocol (Task 2.12 / WP-2.0).

Forensically diagnoses orphaned lock files (dead or recycled PIDs, expired
heartbeats, zero-byte and malformed bodies) and reclaims them only after:

* RBAC Invariant 9: Council-held locks are immune unless a Council override
  token bound to the holder role and the Mendeleev provenance digest is given.
* Strict TOCTOU verification: the byte-level SHA-256 recorded before
  inspection must still match immediately before unlinking.
* Invariant 12: every operation is persisted to an append-only, hash-chained
  JSON ledger.

Physical provenance: standard atomic weights of C, H, O and N are resolved
live from ``mendeleev`` (never a static table), checked against physical
bands, and hashed into a digest bound into every receipt and override token.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import json
import math
import os
import threading
import time
import uuid
import warnings
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

import psutil
from mendeleev import element
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .deadlock_detector import (
    COUNCIL_IMMUNE_ROLES,
    DEFAULT_GUARD_TIMEOUT_SEC,
    DEFAULT_INITIAL_SETTLE_SEC,
    compute_file_sha256,
    exclusive_os_file_guard,
    is_council_immune_role,
    utc_now_iso,
)

PathLike = Union[str, "os.PathLike[str]"]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
LOCK_RECLAMATION_VERSION: str = "2.12.0"
RECLAMATION_SUBSYSTEM_NAME: str = "LOCK_RECLAMATION"
CANONICAL_RECLAIMER_ROLE: str = "cochem-lock-reclaimer"
COUNCIL_OVERRIDE_TOKEN_PREFIX: str = "COCHEM-COUNCIL-OVERRIDE"
DEFAULT_RECLAMATION_LEDGER_FILENAME: str = "lock_reclamation_ledger.json"
MAX_RECLAMATION_LATENCY_CEILING_MS: float = 250.0
RAM_CEILING_BYTES: int = 2 * 1024 * 1024 * 1024
GENESIS_LEDGER_DIGEST: str = "0" * 64
# Windows create_time() has coarse resolution; a PID is only judged recycled
# when its creation time is later than lock acquisition by this margin.
PID_RECYCLE_MARGIN_SEC: float = 1.0

_LEDGER_SCHEMA_VERSION = 1
_LEDGER_REPLACE_ATTEMPTS = 50
_LEDGER_REPLACE_BACKOFF_SEC = 0.01
_MILLISECOND_EPOCH_THRESHOLD = 1e12

__all__ = [
    "BatchReclamationSummary",
    "CANONICAL_RECLAIMER_ROLE",
    "COUNCIL_OVERRIDE_TOKEN_PREFIX",
    "CouncilImmunityReclamationError",
    "CryptographicChallengeError",
    "DEFAULT_RECLAMATION_LEDGER_FILENAME",
    "LOCK_RECLAMATION_VERSION",
    "LockReclamationConfig",
    "LockReclamationError",
    "LockReclamationReceipt",
    "MAX_RECLAMATION_LATENCY_CEILING_MS",
    "OrphanClassification",
    "RECLAMATION_SUBSYSTEM_NAME",
    "ReclamationAuditChallenge",
    "ReclamationLatencyError",
    "ReclamationLedger",
    "ReclamationLedgerError",
    "ReclamationStatus",
    "SafeLockReclamationProtocol",
    "TOCTOURaceError",
    "compute_challenge_digest",
    "create_council_override_token",
    "generate_reclamation_signature",
    "safe_reclaim_batch",
    "safe_reclaim_lock",
    "verify_council_override_token",
    # Supplementary helpers
    "DEFAULT_INITIAL_SETTLE_SEC",
    "GENESIS_LEDGER_DIGEST",
    "PID_RECYCLE_MARGIN_SEC",
    "RAM_CEILING_BYTES",
    "check_heap_memory_guardrail",
    "compute_batch_digest",
    "compute_mendeleev_provenance_digest",
    "verify_dynamic_mendeleev_invariants",
    "verify_reclamation_receipt",
]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class LockReclamationError(RuntimeError):
    """Base error for the lock reclamation subsystem; may carry the ledgered receipt."""

    def __init__(self, message: str, *, receipt: Optional["LockReclamationReceipt"] = None) -> None:
        super().__init__(message)
        self.receipt = receipt


class CouncilImmunityReclamationError(LockReclamationError):
    """Raised when a Council-held lock is targeted without a valid override token."""


class CryptographicChallengeError(LockReclamationError):
    """Raised when a reclamation audit challenge fails cryptographic verification."""


class TOCTOURaceError(CryptographicChallengeError):
    """Raised when lock bytes changed between forensic inspection and release."""

    def __init__(
        self,
        message: str,
        *,
        receipt: Optional["LockReclamationReceipt"] = None,
        expected_sha256: Optional[str] = None,
        observed_sha256: Optional[str] = None,
    ) -> None:
        super().__init__(message, receipt=receipt)
        self.expected_sha256 = expected_sha256
        self.observed_sha256 = observed_sha256


class ReclamationLatencyError(LockReclamationError):
    """Raised when a caller demands the latency ceiling and it was breached."""


class ReclamationLedgerError(LockReclamationError):
    """Raised when the append-only ledger is unreadable, tampered or unwritable."""


class _MalformedLockContent(ValueError):
    """Internal: lock body could not be interpreted as a holder record."""


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class ReclamationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    REJECTED_ALIVE = "REJECTED_ALIVE"
    REJECTED_COUNCIL_IMMUNE = "REJECTED_COUNCIL_IMMUNE"
    REJECTED_NONEXISTENT = "REJECTED_NONEXISTENT"
    REJECTED_CORRUPTED = "REJECTED_CORRUPTED"
    FAILED_TOCTOU = "FAILED_TOCTOU"


class OrphanClassification(str, Enum):
    DEAD_PID = "DEAD_PID"
    TTL_EXPIRED = "TTL_EXPIRED"
    CORRUPTED_ZERO_BYTE = "CORRUPTED_ZERO_BYTE"
    CORRUPTED_MALFORMED = "CORRUPTED_MALFORMED"
    HEALTHY_ALIVE = "HEALTHY_ALIVE"
    NONE = "NONE"


# --------------------------------------------------------------------------
# Dynamic Mendeleev provenance (AC1)
# --------------------------------------------------------------------------
_PROVENANCE_SYMBOLS: Tuple[str, ...] = ("C", "H", "O", "N")
# (symbol, exclusive lower bound, exclusive upper bound) in g/mol.
_PHYSICAL_MASS_BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("C", 12.0, 12.02),
    ("H", 1.0, 1.01),
    ("O", 15.99, 16.01),
    ("N", 14.0, 14.01),
)


@functools.lru_cache(maxsize=1)
def _resolve_standard_atomic_masses() -> Tuple[Tuple[str, float], ...]:
    """Query mendeleev once for the provenance element masses."""
    resolved: List[Tuple[str, float]] = []
    for symbol in _PROVENANCE_SYMBOLS:
        mass = element(symbol).mass
        if mass is None:
            raise LockReclamationError(f"mendeleev returned no mass for element {symbol}")
        resolved.append((symbol, float(mass)))
    return tuple(resolved)


@functools.lru_cache(maxsize=1)
def verify_dynamic_mendeleev_invariants() -> bool:
    """Check live masses lie inside physical bands and follow proton-number order."""
    masses = dict(_resolve_standard_atomic_masses())
    for symbol, lower, upper in _PHYSICAL_MASS_BANDS:
        mass = masses.get(symbol)
        if mass is None or not math.isfinite(mass) or not (lower < mass < upper):
            return False
    # Z: H=1 < C=6 < N=7 < O=8, so standard weights must be strictly ordered alike.
    return masses["H"] < masses["C"] < masses["N"] < masses["O"]


@functools.lru_cache(maxsize=1)
def compute_mendeleev_provenance_digest() -> str:
    """64-char hex SHA-256 binding the live C/H/O/N masses to this subsystem."""
    masses = dict(_resolve_standard_atomic_masses())
    material = (
        f"C:{masses['C']:.6f}|H:{masses['H']:.6f}|O:{masses['O']:.6f}|N:{masses['N']:.6f}"
        f"|SUB:{RECLAMATION_SUBSYSTEM_NAME}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Resource guardrail (AC10)
# --------------------------------------------------------------------------
def check_heap_memory_guardrail(ceiling_bytes: int = RAM_CEILING_BYTES) -> int:
    """Return current RSS in bytes; raise if it meets or exceeds ``ceiling_bytes``."""
    rss = int(psutil.Process().memory_info().rss)
    if rss >= ceiling_bytes:
        raise LockReclamationError(
            f"process RSS {rss} bytes breaches heap ceiling {ceiling_bytes} bytes; "
            "reclamation refused"
        )
    return rss


# --------------------------------------------------------------------------
# Hashing helpers
# --------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_equal(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


_SIGNED_FIELDS: Tuple[str, ...] = (
    "receipt_id",
    "target_lock_path",
    "status",
    "orphan_classification",
    "reclaimed",
    "target_pid",
    "target_role",
    "reclaimer_pid",
    "reclaimer_role",
    "pre_reclamation_sha256",
    "post_reclamation_sha256",
    "reclaimed_at_iso",
    "mendeleev_provenance_digest",
    "council_override_token",
    "diagnostic_message",
)


def generate_reclamation_signature(
    *,
    receipt_id: str,
    target_lock_path: str,
    status: Union[ReclamationStatus, str],
    orphan_classification: Union[OrphanClassification, str],
    reclaimed: bool,
    target_pid: Optional[int],
    target_role: Optional[str],
    reclaimer_pid: int,
    reclaimer_role: str,
    pre_reclamation_sha256: Optional[str],
    post_reclamation_sha256: Optional[str],
    reclaimed_at_iso: str,
    mendeleev_provenance_digest: str,
    council_override_token: Optional[str] = None,
    diagnostic_message: str = "",
) -> str:
    """Authoritative 64-char SHA-256 over the canonical JSON of a receipt's facts.

    ``latency_ms`` and ``ledger_sequence`` are deliberately excluded: the
    sequence is bound by the ledger's rolling hash chain instead.
    """
    material = {
        "subsystem": RECLAMATION_SUBSYSTEM_NAME,
        "receipt_id": receipt_id,
        "target_lock_path": target_lock_path,
        "status": _enum_value(status),
        "orphan_classification": _enum_value(orphan_classification),
        "reclaimed": bool(reclaimed),
        "target_pid": target_pid,
        "target_role": target_role,
        "reclaimer_pid": reclaimer_pid,
        "reclaimer_role": reclaimer_role,
        "pre_reclamation_sha256": pre_reclamation_sha256,
        "post_reclamation_sha256": post_reclamation_sha256,
        "reclaimed_at_iso": reclaimed_at_iso,
        "mendeleev_provenance_digest": mendeleev_provenance_digest,
        "council_override_token": council_override_token,
        "diagnostic_message": diagnostic_message,
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_text(canonical)


def _signature_from_mapping(mapping: Mapping[str, Any]) -> Optional[str]:
    try:
        return generate_reclamation_signature(**{name: mapping[name] for name in _SIGNED_FIELDS})
    except (KeyError, TypeError, ValueError):
        return None


def compute_challenge_digest(
    challenge_id: str,
    lock_path: str,
    lock_sha256: str,
    target_pid: Optional[int],
    timestamp_iso: str,
) -> str:
    """Digest binding an audit challenge's fields to the Mendeleev provenance."""
    pid_text = "" if target_pid is None else str(int(target_pid))
    material = "|".join(
        (
            RECLAMATION_SUBSYSTEM_NAME,
            challenge_id,
            lock_path,
            lock_sha256,
            pid_text,
            timestamp_iso,
            compute_mendeleev_provenance_digest(),
        )
    )
    return _sha256_text(material)


def compute_batch_digest(signatures: Iterable[str]) -> str:
    """Chained batch digest: SHA256(sig_1 + sig_2 + ... + sig_N)."""
    return _sha256_text("".join(signatures))


# --------------------------------------------------------------------------
# Council override tokens (AC6)
# --------------------------------------------------------------------------
def _validate_token_component(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty string without surrounding whitespace")
    if ":" in value or any(ch.isspace() for ch in value):
        raise ValueError(f"{label} must not contain ':' or whitespace")
    return value


def _override_signature(target_role: str, council_nonce: str) -> str:
    return _sha256_text(f"{target_role}:{council_nonce}:{compute_mendeleev_provenance_digest()}")


def create_council_override_token(target_role: str, council_nonce: str) -> str:
    """Mint ``COCHEM-COUNCIL-OVERRIDE-{role}:{nonce}:{sig}`` bound to role and provenance."""
    role = _validate_token_component(target_role, "target_role")
    nonce = _validate_token_component(council_nonce, "council_nonce")
    return f"{COUNCIL_OVERRIDE_TOKEN_PREFIX}-{role}:{nonce}:{_override_signature(role, nonce)}"


def verify_council_override_token(token: Optional[str], target_role: Optional[str]) -> bool:
    """Validate prefix, exact role binding and constant-time signature match."""
    if not isinstance(token, str) or not isinstance(target_role, str):
        return False
    head = f"{COUNCIL_OVERRIDE_TOKEN_PREFIX}-"
    if not token.startswith(head):
        return False
    parts = token[len(head):].split(":")
    if len(parts) != 3:
        return False
    role, nonce, signature = parts
    if not role or not nonce or role != target_role.strip():
        return False
    return _digest_equal(signature, _override_signature(role, nonce))


# --------------------------------------------------------------------------
# Pydantic models (AC11)
# --------------------------------------------------------------------------
_Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _check_iso(value: str) -> str:
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class LockReclamationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stale_timeout_sec: float = Field(default=60.0, gt=0.0)
    ledger_path: Optional[str] = None
    enforce_strict_toctou: bool = True
    council_immune_roles: List[str] = Field(default_factory=lambda: list(COUNCIL_IMMUNE_ROLES))
    heap_ceiling_bytes: int = Field(default=RAM_CEILING_BYTES, gt=0)
    max_latency_ceiling_ms: float = Field(default=MAX_RECLAMATION_LATENCY_CEILING_MS, gt=0.0)

    @field_validator("ledger_path", mode="before")
    @classmethod
    def _coerce_ledger_path(cls, value: Any) -> Any:
        if isinstance(value, os.PathLike):
            return os.fspath(value)
        return value

    @field_validator("council_immune_roles")
    @classmethod
    def _enforce_invariant_nine(cls, value: List[str]) -> List[str]:
        """Configuration may extend the Council roster but never shrink it."""
        merged: List[str] = []
        seen = set()
        for role in [*COUNCIL_IMMUNE_ROLES, *value]:
            cleaned = str(role).strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                merged.append(cleaned)
        return merged


class LockReclamationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    target_lock_path: str = Field(min_length=1)
    status: ReclamationStatus
    orphan_classification: OrphanClassification
    reclaimed: bool
    target_pid: Optional[int] = None
    target_role: Optional[str] = None
    reclaimer_pid: int = Field(ge=0)
    reclaimer_role: str = Field(min_length=1)
    pre_reclamation_sha256: Optional[_Hex64] = None
    post_reclamation_sha256: Optional[_Hex64] = None
    reclaimed_at_iso: str
    latency_ms: float = Field(ge=0.0)
    verification_signature: _Hex64
    mendeleev_provenance_digest: _Hex64
    ledger_sequence: int = Field(default=0, ge=0)
    council_override_token: Optional[str] = None
    diagnostic_message: str = Field(min_length=1)

    @field_validator("receipt_id")
    @classmethod
    def _check_receipt_id(cls, value: str) -> str:
        uuid.UUID(value)
        return value

    @field_validator("reclaimed_at_iso")
    @classmethod
    def _check_timestamp(cls, value: str) -> str:
        return _check_iso(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "LockReclamationReceipt":
        if self.reclaimed != (self.status is ReclamationStatus.SUCCESS):
            raise ValueError("reclaimed must be True exactly when status is SUCCESS")
        return self


class ReclamationAuditChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    challenge_id: str = Field(min_length=1)
    lock_path: str = Field(min_length=1)
    lock_sha256: _Hex64
    target_pid: Optional[int] = None
    timestamp_iso: str
    challenge_digest: _Hex64

    @field_validator("timestamp_iso")
    @classmethod
    def _check_timestamp(cls, value: str) -> str:
        return _check_iso(value)


class BatchReclamationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_scanned: int = Field(ge=0)
    total_reclaimed: int = Field(ge=0)
    total_rejected: int = Field(ge=0)
    receipts: List[LockReclamationReceipt]
    batch_digest: _Hex64
    duration_ms: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_counters(self) -> "BatchReclamationSummary":
        if self.total_scanned != len(self.receipts):
            raise ValueError("total_scanned must equal the number of receipts")
        if self.total_reclaimed + self.total_rejected != self.total_scanned:
            raise ValueError("total_reclaimed + total_rejected must equal total_scanned")
        return self


def verify_reclamation_receipt(receipt: LockReclamationReceipt) -> bool:
    """Recompute a receipt's signature from its fields and compare in constant time."""
    recomputed = _signature_from_mapping(receipt.model_dump(mode="json"))
    return recomputed is not None and _digest_equal(recomputed, receipt.verification_signature)


# --------------------------------------------------------------------------
# Append-only tamper-evident ledger (AC8)
# --------------------------------------------------------------------------
class ReclamationLedger:
    """Hash-chained JSON ledger.

    rolling_digest_i = SHA256(rolling_digest_{i-1} || seq_i || receipt_id_i || signature_i)
    with genesis ``"0" * 64``. Every append verifies the full existing chain
    (including each stored receipt's signature) under an OS-level guard, then
    rewrites the document atomically via ``<name>.tmp_ledger`` + ``os.replace``.
    """

    def __init__(self, ledger_path: PathLike, *, guard_timeout_sec: float = DEFAULT_GUARD_TIMEOUT_SEC) -> None:
        self._path = Path(ledger_path)
        self._guard_path = self._path.with_name(self._path.name + ".guard")
        self._tmp_path = self._path.with_name(self._path.name + ".tmp_ledger")
        self._guard_timeout_sec = float(guard_timeout_sec)
        self._thread_lock = threading.Lock()
        if self._path.exists():
            self.verify_chain()

    @property
    def path(self) -> Path:
        return self._path

    @staticmethod
    def compute_rolling_digest(previous_digest: str, sequence: int, receipt_id: str, signature: str) -> str:
        return _sha256_text(f"{previous_digest}{sequence}{receipt_id}{signature}")

    def _read_entries(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReclamationLedgerError(f"ledger {self._path} is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
            raise ReclamationLedgerError(f"ledger {self._path} lacks an 'entries' list")
        return document["entries"]

    def _verify_entries(self, entries: List[Any]) -> str:
        previous = GENESIS_LEDGER_DIGEST
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise ReclamationLedgerError(f"ledger entry {index} is not an object")
            sequence = entry.get("sequence")
            receipt_id = entry.get("receipt_id")
            signature = entry.get("verification_signature")
            receipt = entry.get("receipt")
            if isinstance(sequence, bool) or sequence != index:
                raise ReclamationLedgerError(f"ledger sequence break at position {index}: {sequence!r}")
            if not isinstance(receipt_id, str) or not isinstance(signature, str):
                raise ReclamationLedgerError(f"ledger entry {index} has invalid identifiers")
            if not _digest_equal(entry.get("previous_digest"), previous):
                raise ReclamationLedgerError(f"ledger entry {index} previous_digest does not link")
            expected = self.compute_rolling_digest(previous, index, receipt_id, signature)
            if not _digest_equal(entry.get("rolling_digest"), expected):
                raise ReclamationLedgerError(f"ledger entry {index} rolling_digest mismatch (tampered)")
            if (
                not isinstance(receipt, dict)
                or receipt.get("receipt_id") != receipt_id
                or receipt.get("verification_signature") != signature
                or receipt.get("ledger_sequence") != index
            ):
                raise ReclamationLedgerError(f"ledger entry {index} receipt body does not match envelope")
            if not _digest_equal(_signature_from_mapping(receipt), signature):
                raise ReclamationLedgerError(f"ledger entry {index} receipt signature invalid (tampered)")
            previous = expected
        return previous

    def verify_chain(self) -> bool:
        """Verify the full chain; raises ReclamationLedgerError on any break."""
        with self._thread_lock:
            self._verify_entries(self._read_entries())
        return True

    def entries(self) -> List[Dict[str, Any]]:
        with self._thread_lock:
            return [dict(entry) for entry in self._read_entries()]

    def head(self) -> Tuple[int, str]:
        """(last sequence, head rolling digest) after full verification."""
        with self._thread_lock:
            entries = self._read_entries()
            return len(entries), self._verify_entries(entries)

    def _replace_with_retry(self) -> None:
        last_error: Optional[OSError] = None
        for _attempt in range(_LEDGER_REPLACE_ATTEMPTS):
            try:
                os.replace(self._tmp_path, self._path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(_LEDGER_REPLACE_BACKOFF_SEC)
        raise ReclamationLedgerError(f"atomic replace of {self._path} failed: {last_error}")

    def _atomic_write(self, document: Dict[str, Any]) -> None:
        payload = json.dumps(document, indent=2, ensure_ascii=False)
        try:
            with open(self._tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry()
        finally:
            if self._tmp_path.exists():
                with contextlib.suppress(OSError):
                    self._tmp_path.unlink()

    def append(self, receipt: LockReclamationReceipt) -> LockReclamationReceipt:
        """Append a signed receipt; returns it sealed with its ledger sequence."""
        if not verify_reclamation_receipt(receipt):
            raise ReclamationLedgerError("refusing to ledger a receipt with an invalid signature", receipt=receipt)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._thread_lock, exclusive_os_file_guard(self._guard_path, self._guard_timeout_sec):
                entries = self._read_entries()
                previous = self._verify_entries(entries)
                sequence = len(entries) + 1
                sealed = receipt.model_copy(update={"ledger_sequence": sequence})
                rolling = self.compute_rolling_digest(
                    previous, sequence, sealed.receipt_id, sealed.verification_signature
                )
                entry = {
                    "sequence": sequence,
                    "receipt_id": sealed.receipt_id,
                    "verification_signature": sealed.verification_signature,
                    "previous_digest": previous,
                    "rolling_digest": rolling,
                    "appended_at_iso": utc_now_iso(),
                    "receipt": sealed.model_dump(mode="json"),
                }
                document = {
                    "ledger_schema_version": _LEDGER_SCHEMA_VERSION,
                    "subsystem": RECLAMATION_SUBSYSTEM_NAME,
                    "subsystem_version": LOCK_RECLAMATION_VERSION,
                    "genesis_digest": GENESIS_LEDGER_DIGEST,
                    "head_digest": rolling,
                    "entry_count": sequence,
                    "entries": [*entries, entry],
                }
                self._atomic_write(document)
        except ReclamationLedgerError as exc:
            if exc.receipt is None:
                raise ReclamationLedgerError(str(exc), receipt=receipt) from exc
            raise
        except OSError as exc:
            raise ReclamationLedgerError(f"ledger {self._path} write failed: {exc}", receipt=receipt) from exc
        return sealed


# --------------------------------------------------------------------------
# Lock body parsing
# --------------------------------------------------------------------------
_PID_KEYS = ("pid", "holder_pid", "owner_pid", "process_id")
_ROLE_KEYS = ("role", "holder_role", "agent_role", "owner_role")
_ACQUIRED_KEYS = ("acquired_at_ts", "acquired_at", "acquired_at_iso", "created_at", "timestamp")
_HEARTBEAT_KEYS = ("last_heartbeat_ts", "heartbeat_ts", "last_heartbeat", "heartbeat", "heartbeat_at")


def _coerce_epoch(value: Any) -> Optional[float]:
    """Epoch seconds from a float/int (s or ms) or ISO-8601 string; None if unusable.

    Naive ISO strings are interpreted in local time (``datetime.now().isoformat()``).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            return None
        return number / 1000.0 if number > _MILLISECOND_EPOCH_THRESHOLD else number
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric: Optional[float] = float(text)
        except ValueError:
            numeric = None
        if numeric is not None:
            return _coerce_epoch(numeric)
        if text[-1] in "Zz":
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return None


def _parse_lock_body(raw: bytes) -> Tuple[int, Optional[str], Optional[float], Optional[float]]:
    """Return (pid, role, acquired_epoch, heartbeat_epoch) or raise _MalformedLockContent."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _MalformedLockContent(f"body is not UTF-8 ({exc.reason})") from exc
    stripped = text.strip()
    if not stripped:
        raise _MalformedLockContent("body contains only whitespace")
    if stripped.isascii() and stripped.isdigit():
        pid = int(stripped)
        if pid <= 0:
            raise _MalformedLockContent(f"non-positive pid {pid}")
        return pid, None, None, None
    try:
        document = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise _MalformedLockContent(f"unparseable JSON ({exc.msg} at char {exc.pos})") from exc
    if not isinstance(document, dict):
        raise _MalformedLockContent(f"JSON body is {type(document).__name__}, expected object")

    pid_value = next((document[k] for k in _PID_KEYS if k in document), None)
    if isinstance(pid_value, str) and pid_value.strip().isascii() and pid_value.strip().isdigit():
        pid_value = int(pid_value.strip())
    if not isinstance(pid_value, int) or isinstance(pid_value, bool) or pid_value <= 0:
        raise _MalformedLockContent(f"missing or invalid holder pid: {pid_value!r}")

    role: Optional[str] = None
    for key in _ROLE_KEYS:
        candidate = document.get(key)
        if isinstance(candidate, str) and candidate.strip():
            role = candidate.strip()
            break

    acquired: Optional[float] = None
    for key in _ACQUIRED_KEYS:
        acquired = _coerce_epoch(document.get(key))
        if acquired is not None:
            break

    heartbeats = [ts for ts in (_coerce_epoch(document.get(k)) for k in _HEARTBEAT_KEYS) if ts is not None]
    heartbeat = max(heartbeats) if heartbeats else None
    return pid_value, role, acquired, heartbeat


def _probe_process(pid: int) -> Tuple[bool, Optional[float], str]:
    """Return (alive, create_time, note) for ``pid`` using the OS process table."""
    try:
        exists = psutil.pid_exists(pid)
    except (OverflowError, ValueError) as exc:
        return False, None, f"pid {pid} is outside the OS pid range ({exc})"
    if not exists:
        return False, None, f"pid {pid} does not exist in the OS process table"
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False, None, f"pid {pid} exited during forensic inspection"
    created: Optional[float] = None
    status: Optional[str] = None
    try:
        created = proc.create_time()
    except psutil.NoSuchProcess:
        return False, None, f"pid {pid} exited during forensic inspection"
    except psutil.AccessDenied:
        created = None
    try:
        status = proc.status()
    except psutil.NoSuchProcess:
        return False, created, f"pid {pid} exited during forensic inspection"
    except psutil.AccessDenied:
        status = None
    if status == psutil.STATUS_ZOMBIE:
        return False, created, f"pid {pid} is a zombie awaiting reaping"
    created_text = "unknown" if created is None else f"{created:.3f}"
    return True, created, f"pid {pid} alive (status={status or 'unknown'}, created_at={created_text})"


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------
DiagnosisResult = Tuple[bool, OrphanClassification, Optional[int], Optional[str], str]


class SafeLockReclamationProtocol:
    """Forensic diagnosis and verified reclamation of orphaned lock files."""

    def __init__(
        self,
        config: Optional[LockReclamationConfig] = None,
        *,
        reclaimer_role: str = CANONICAL_RECLAIMER_ROLE,
        settle_window_sec: float = DEFAULT_INITIAL_SETTLE_SEC,
    ) -> None:
        self._config = config if config is not None else LockReclamationConfig()
        if not isinstance(reclaimer_role, str) or not reclaimer_role.strip():
            raise ValueError("reclaimer_role must be a non-empty string")
        if settle_window_sec < 0:
            raise ValueError("settle_window_sec must be non-negative")
        if not verify_dynamic_mendeleev_invariants():
            raise LockReclamationError(
                "Mendeleev atomic-mass invariants failed; provenance cannot be bound to receipts"
            )
        self._provenance = compute_mendeleev_provenance_digest()
        self._reclaimer_role = reclaimer_role.strip()
        self._settle_window_sec = float(settle_window_sec)
        self._ledgers: Dict[str, ReclamationLedger] = {}
        if self._config.ledger_path:
            self._ledger_for_path(Path(self._config.ledger_path))

    @property
    def config(self) -> LockReclamationConfig:
        return self._config

    # -- ledger ---------------------------------------------------------------
    def _ledger_for_path(self, ledger_path: Path) -> ReclamationLedger:
        key = str(ledger_path.absolute())
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = ReclamationLedger(ledger_path)
            self._ledgers[key] = ledger
        return ledger

    def _ledger_for(self, lock_path: Path) -> ReclamationLedger:
        if self._config.ledger_path:
            return self._ledger_for_path(Path(self._config.ledger_path))
        return self._ledger_for_path(lock_path.absolute().parent / DEFAULT_RECLAMATION_LEDGER_FILENAME)

    # -- forensic diagnosis ---------------------------------------------------
    def diagnose_lock_orphanage(self, lock_path: PathLike) -> DiagnosisResult:
        """Read-only forensic verdict: (is_stale, classification, pid, role, message)."""
        path = Path(lock_path)
        now = time.time()
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            return False, OrphanClassification.NONE, None, None, f"lock {path} does not exist"
        if not path.is_file():
            return False, OrphanClassification.NONE, None, None, f"{path} is not a regular file"
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return False, OrphanClassification.NONE, None, None, f"lock {path} vanished during inspection"
        except PermissionError as exc:
            return (
                False,
                OrphanClassification.HEALTHY_ALIVE,
                None,
                None,
                f"lock {path} is exclusively held open by an active handle ({exc})",
            )

        age = max(0.0, now - stat_result.st_mtime)
        settle = self._settle_window_sec
        if not raw:
            if age <= settle:
                return (
                    False,
                    OrphanClassification.NONE,
                    None,
                    None,
                    f"zero-byte lock aged {age:.3f}s is within the {settle:.3f}s settle window; "
                    "acquisition may be in flight",
                )
            return (
                True,
                OrphanClassification.CORRUPTED_ZERO_BYTE,
                None,
                None,
                f"zero-byte lock aged {age:.3f}s exceeds the {settle:.3f}s settle window",
            )

        try:
            pid, role, acquired, heartbeat = _parse_lock_body(raw)
        except _MalformedLockContent as exc:
            if age <= settle:
                return (
                    False,
                    OrphanClassification.NONE,
                    None,
                    None,
                    f"unparseable lock aged {age:.3f}s is within the {settle:.3f}s settle window ({exc})",
                )
            return (
                True,
                OrphanClassification.CORRUPTED_MALFORMED,
                None,
                None,
                f"malformed lock body ({exc}); aged {age:.3f}s",
            )

        acquired_ts = acquired if acquired is not None else stat_result.st_mtime
        last_signal = max(ts for ts in (acquired_ts, heartbeat) if ts is not None)

        alive, created, note = _probe_process(pid)
        if not alive:
            return True, OrphanClassification.DEAD_PID, pid, role, f"holder {note}"
        if created is not None and created > acquired_ts + PID_RECYCLE_MARGIN_SEC:
            return (
                True,
                OrphanClassification.DEAD_PID,
                pid,
                role,
                f"pid {pid} recycled: process created at {created:.3f} is "
                f"{created - acquired_ts:.3f}s after lock acquisition at {acquired_ts:.3f}",
            )
        silence = now - last_signal
        timeout = self._config.stale_timeout_sec
        if silence > timeout:
            return (
                True,
                OrphanClassification.TTL_EXPIRED,
                pid,
                role,
                f"heartbeat silence {silence:.3f}s exceeds stale_timeout_sec={timeout:.3f}s ({note})",
            )
        return (
            False,
            OrphanClassification.HEALTHY_ALIVE,
            pid,
            role,
            f"holder {note}; last heartbeat {silence:.3f}s ago within stale_timeout_sec={timeout:.3f}s",
        )

    # -- audit challenges -----------------------------------------------------
    def issue_audit_challenge(
        self,
        lock_path: PathLike,
        lock_sha256: Optional[str] = None,
        target_pid: Optional[int] = None,
    ) -> ReclamationAuditChallenge:
        path = Path(lock_path)
        digest_of_lock = lock_sha256 if lock_sha256 is not None else compute_file_sha256(path)
        challenge_id = str(uuid.uuid4())
        absolute = str(path.absolute())
        timestamp = utc_now_iso()
        return ReclamationAuditChallenge(
            challenge_id=challenge_id,
            lock_path=absolute,
            lock_sha256=digest_of_lock,
            target_pid=target_pid,
            timestamp_iso=timestamp,
            challenge_digest=compute_challenge_digest(
                challenge_id, absolute, digest_of_lock, target_pid, timestamp
            ),
        )

    def verify_audit_challenge(self, challenge: ReclamationAuditChallenge) -> str:
        """Verify challenge integrity and that lock bytes are unchanged; returns current SHA-256."""
        expected = compute_challenge_digest(
            challenge.challenge_id,
            challenge.lock_path,
            challenge.lock_sha256,
            challenge.target_pid,
            challenge.timestamp_iso,
        )
        if not _digest_equal(expected, challenge.challenge_digest):
            raise CryptographicChallengeError(
                f"audit challenge {challenge.challenge_id} digest does not bind its fields"
            )
        observed = compute_file_sha256(challenge.lock_path)
        if not _digest_equal(observed, challenge.lock_sha256):
            raise TOCTOURaceError(
                f"lock {challenge.lock_path} mutated after inspection "
                f"(expected {challenge.lock_sha256}, observed {observed})",
                expected_sha256=challenge.lock_sha256,
                observed_sha256=observed,
            )
        return observed

    # -- receipts -------------------------------------------------------------
    def _finalize(
        self,
        *,
        started: float,
        path: Path,
        token: Optional[str],
        status: ReclamationStatus,
        message: str,
        classification: OrphanClassification = OrphanClassification.NONE,
        reclaimed: bool = False,
        pid: Optional[int] = None,
        role: Optional[str] = None,
        pre_sha: Optional[str] = None,
        post_sha: Optional[str] = None,
    ) -> LockReclamationReceipt:
        receipt_id = str(uuid.uuid4())
        reclaimed_at = utc_now_iso()
        reclaimer_pid = os.getpid()
        target = str(path.absolute())
        signature = generate_reclamation_signature(
            receipt_id=receipt_id,
            target_lock_path=target,
            status=status,
            orphan_classification=classification,
            reclaimed=reclaimed,
            target_pid=pid,
            target_role=role,
            reclaimer_pid=reclaimer_pid,
            reclaimer_role=self._reclaimer_role,
            pre_reclamation_sha256=pre_sha,
            post_reclamation_sha256=post_sha,
            reclaimed_at_iso=reclaimed_at,
            mendeleev_provenance_digest=self._provenance,
            council_override_token=token,
            diagnostic_message=message,
        )
        # Latency covers forensic analysis and release; ledger I/O follows.
        latency_ms = (time.perf_counter() - started) * 1000.0
        if latency_ms >= self._config.max_latency_ceiling_ms:
            warnings.warn(
                f"lock reclamation for {target} took {latency_ms:.1f} ms "
                f"(ceiling {self._config.max_latency_ceiling_ms:.1f} ms)",
                RuntimeWarning,
                stacklevel=3,
            )
        receipt = LockReclamationReceipt(
            receipt_id=receipt_id,
            target_lock_path=target,
            status=status,
            orphan_classification=classification,
            reclaimed=reclaimed,
            target_pid=pid,
            target_role=role,
            reclaimer_pid=reclaimer_pid,
            reclaimer_role=self._reclaimer_role,
            pre_reclamation_sha256=pre_sha,
            post_reclamation_sha256=post_sha,
            reclaimed_at_iso=reclaimed_at,
            latency_ms=latency_ms,
            verification_signature=signature,
            mendeleev_provenance_digest=self._provenance,
            ledger_sequence=0,
            council_override_token=token,
            diagnostic_message=message,
        )
        return self._ledger_for(path).append(receipt)

    @staticmethod
    def _current_sha_or_none(path: Path) -> Optional[str]:
        try:
            return compute_file_sha256(path)
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return None

    # -- reclamation ----------------------------------------------------------
    def reclaim_lock(
        self, lock_path: PathLike, council_override_token: Optional[str] = None
    ) -> LockReclamationReceipt:
        """Diagnose ``lock_path`` and unlink it only if orphaned and safe to release."""
        started = time.perf_counter()
        check_heap_memory_guardrail(self._config.heap_ceiling_bytes)
        path = Path(lock_path)
        finalize = functools.partial(self._finalize, started=started, path=path, token=council_override_token)

        if path.is_symlink():
            return finalize(
                status=ReclamationStatus.REJECTED_CORRUPTED,
                message=f"{path} is a symbolic link; reclamation refused",
            )
        if not path.exists():
            return finalize(status=ReclamationStatus.REJECTED_NONEXISTENT, message=f"lock {path} does not exist")
        if not path.is_file():
            return finalize(
                status=ReclamationStatus.REJECTED_CORRUPTED,
                message=f"{path} is not a regular file; reclamation refused",
            )
        try:
            pre_sha = compute_file_sha256(path)
        except FileNotFoundError:
            return finalize(
                status=ReclamationStatus.REJECTED_NONEXISTENT,
                message=f"lock {path} vanished before forensic hashing",
            )
        except PermissionError as exc:
            return finalize(
                status=ReclamationStatus.REJECTED_ALIVE,
                message=f"lock {path} unreadable ({exc}); an active holder retains an exclusive handle",
            )

        is_stale, classification, pid, role, diagnosis = self.diagnose_lock_orphanage(path)
        facts = {"classification": classification, "pid": pid, "role": role, "pre_sha": pre_sha}

        if not is_stale:
            if classification is not OrphanClassification.HEALTHY_ALIVE and not path.exists():
                status = ReclamationStatus.REJECTED_NONEXISTENT
            else:
                status = ReclamationStatus.REJECTED_ALIVE
            return finalize(
                status=status,
                post_sha=self._current_sha_or_none(path),
                message=f"reclamation refused: {diagnosis}",
                **facts,
            )

        if role is not None and is_council_immune_role(role, self._config.council_immune_roles):
            if not verify_council_override_token(council_override_token, role):
                reason = (
                    "no Council override token supplied"
                    if council_override_token is None
                    else "Council override token failed role binding or signature verification"
                )
                message = f"Invariant 9: lock held by Council role {role!r} is immune ({reason}); {diagnosis}"
                receipt = finalize(
                    status=ReclamationStatus.REJECTED_COUNCIL_IMMUNE,
                    post_sha=self._current_sha_or_none(path),
                    message=message,
                    **facts,
                )
                raise CouncilImmunityReclamationError(message, receipt=receipt)

        if self._config.enforce_strict_toctou:
            challenge = self.issue_audit_challenge(path, lock_sha256=pre_sha, target_pid=pid)
            try:
                self.verify_audit_challenge(challenge)
            except FileNotFoundError:
                return finalize(
                    status=ReclamationStatus.REJECTED_NONEXISTENT,
                    message=f"lock {path} released by another party before commit; {diagnosis}",
                    **facts,
                )
            except PermissionError as exc:
                return finalize(
                    status=ReclamationStatus.REJECTED_ALIVE,
                    message=f"lock {path} became exclusively held before commit ({exc})",
                    **facts,
                )
            except TOCTOURaceError as exc:
                message = f"TOCTOU race: {exc}; release halted and lock preserved"
                receipt = finalize(
                    status=ReclamationStatus.FAILED_TOCTOU,
                    post_sha=exc.observed_sha256,
                    message=message,
                    **facts,
                )
                raise TOCTOURaceError(
                    message,
                    receipt=receipt,
                    expected_sha256=pre_sha,
                    observed_sha256=exc.observed_sha256,
                ) from exc

        try:
            path.unlink()
        except FileNotFoundError:
            return finalize(
                status=ReclamationStatus.REJECTED_NONEXISTENT,
                message=f"lock {path} released by another party during unlink; {diagnosis}",
                **facts,
            )
        except PermissionError as exc:
            return finalize(
                status=ReclamationStatus.REJECTED_ALIVE,
                post_sha=self._current_sha_or_none(path),
                message=f"unlink of {path} denied ({exc}); an active handle holds the lock",
                **facts,
            )

        return finalize(
            status=ReclamationStatus.SUCCESS,
            reclaimed=True,
            message=f"reclaimed orphaned lock ({classification.value}): {diagnosis}",
            **facts,
        )

    def batch_reclaim(
        self,
        lock_paths: Union[PathLike, Iterable[PathLike]],
        council_override_token: Optional[str] = None,
    ) -> BatchReclamationSummary:
        """Reclaim every orphan in ``lock_paths``; immune/racing locks are preserved and recorded."""
        started = time.perf_counter()
        if isinstance(lock_paths, (str, bytes, os.PathLike)):
            candidates: List[Any] = [lock_paths]
        else:
            candidates = list(lock_paths)
        receipts: List[LockReclamationReceipt] = []
        for candidate in candidates:
            try:
                receipt = self.reclaim_lock(candidate, council_override_token=council_override_token)
            except (CouncilImmunityReclamationError, TOCTOURaceError) as exc:
                if exc.receipt is None:
                    raise
                receipt = exc.receipt
            receipts.append(receipt)
        reclaimed = sum(1 for r in receipts if r.reclaimed)
        return BatchReclamationSummary(
            total_scanned=len(receipts),
            total_reclaimed=reclaimed,
            total_rejected=len(receipts) - reclaimed,
            receipts=receipts,
            batch_digest=compute_batch_digest(r.verification_signature for r in receipts),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )


# --------------------------------------------------------------------------
# Functional facades
# --------------------------------------------------------------------------
def safe_reclaim_lock(
    lock_path: PathLike,
    config: Optional[LockReclamationConfig] = None,
    council_override_token: Optional[str] = None,
    *,
    reclaimer_role: str = CANONICAL_RECLAIMER_ROLE,
    enforce_latency_ceiling: bool = False,
) -> LockReclamationReceipt:
    """One-shot reclamation. With ``enforce_latency_ceiling`` a breach raises (the receipt is attached)."""
    protocol = SafeLockReclamationProtocol(config=config, reclaimer_role=reclaimer_role)
    receipt = protocol.reclaim_lock(lock_path, council_override_token=council_override_token)
    ceiling = protocol.config.max_latency_ceiling_ms
    if enforce_latency_ceiling and receipt.latency_ms >= ceiling:
        raise ReclamationLatencyError(
            f"reclamation latency {receipt.latency_ms:.1f} ms breached ceiling {ceiling:.1f} ms",
            receipt=receipt,
        )
    return receipt


def safe_reclaim_batch(
    lock_paths: Union[PathLike, Iterable[PathLike]],
    config: Optional[LockReclamationConfig] = None,
    council_override_token: Optional[str] = None,
    *,
    reclaimer_role: str = CANONICAL_RECLAIMER_ROLE,
) -> BatchReclamationSummary:
    """Batch sweep over candidate lock paths returning a chained-digest summary."""
    protocol = SafeLockReclamationProtocol(config=config, reclaimer_role=reclaimer_role)
    return protocol.batch_reclaim(lock_paths, council_override_token=council_override_token)


# Warm the Mendeleev caches at import so no reclamation pays the database cold start.
verify_dynamic_mendeleev_invariants()
compute_mendeleev_provenance_digest()
