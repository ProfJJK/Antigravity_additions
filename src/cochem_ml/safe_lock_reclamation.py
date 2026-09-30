"""Alias facade for ``cochem_ml.lock_reclamation`` (Task 2.12).

Re-exports the identical objects of the core module plus the legacy engine
aliases ``LockReclamationEngine``, ``ReclamationConfig`` and ``ReclamationReceipt``.
"""
from __future__ import annotations

from . import lock_reclamation as _core
from .lock_reclamation import (
    CANONICAL_RECLAIMER_ROLE,
    COUNCIL_OVERRIDE_TOKEN_PREFIX,
    DEFAULT_INITIAL_SETTLE_SEC,
    DEFAULT_RECLAMATION_LEDGER_FILENAME,
    GENESIS_LEDGER_DIGEST,
    LOCK_RECLAMATION_VERSION,
    MAX_RECLAMATION_LATENCY_CEILING_MS,
    PID_RECYCLE_MARGIN_SEC,
    RAM_CEILING_BYTES,
    RECLAMATION_SUBSYSTEM_NAME,
    BatchReclamationSummary,
    CouncilImmunityReclamationError,
    CryptographicChallengeError,
    LockReclamationConfig,
    LockReclamationError,
    LockReclamationReceipt,
    OrphanClassification,
    ReclamationAuditChallenge,
    ReclamationLatencyError,
    ReclamationLedger,
    ReclamationLedgerError,
    ReclamationStatus,
    SafeLockReclamationProtocol,
    TOCTOURaceError,
    check_heap_memory_guardrail,
    compute_batch_digest,
    compute_challenge_digest,
    compute_mendeleev_provenance_digest,
    create_council_override_token,
    generate_reclamation_signature,
    safe_reclaim_batch,
    safe_reclaim_lock,
    verify_council_override_token,
    verify_dynamic_mendeleev_invariants,
    verify_reclamation_receipt,
)

LockReclamationEngine = SafeLockReclamationProtocol
ReclamationConfig = LockReclamationConfig
ReclamationReceipt = LockReclamationReceipt

__all__ = [
    *_core.__all__,
    "LockReclamationEngine",
    "ReclamationConfig",
    "ReclamationReceipt",
]
