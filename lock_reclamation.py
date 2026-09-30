"""Repository-root facade for ``cochem_ml.lock_reclamation`` (Task 2.12)."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from cochem_ml import lock_reclamation as _core  # noqa: E402
from cochem_ml.lock_reclamation import (  # noqa: E402
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

__all__ = list(_core.__all__)
