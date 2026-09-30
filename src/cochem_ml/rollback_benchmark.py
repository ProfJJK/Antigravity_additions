"""State rollback and reconstitution latency benchmark engine (Task 2.19).

This module measures two recovery axes against the sub-500 ms SLA:

1. **Physical workspace rollback** - a real git working tree is repeatedly
   dirtied (a tracked file modified, a tracked file deleted, a nested untracked
   artifact created) and restored through
   :class:`~cochem_ml.workspace_rollback_controller.WorkspaceRollbackController`.
   Only the ``rollback_to_checkpoint`` call is timed; every cycle is then
   verified against the baseline SHA-256 workspace state hash.

2. **Persistent state reconstitution** - framed binary ``COCHSV01`` state
   vector checkpoints are read from disk, their frame headers are validated,
   they are deserialized with payload SHA-256 verification, and a
   :class:`~cochem_platform.swarm_state_manager.SwarmStateManager` JSON store is
   loaded cold from disk. Each cycle produces a SHA-256 reconstitution digest
   that must be byte-identical to an untimed reference reconstitution.

Every measured cycle is checked against the configured latency ceiling. If a
cycle exceeds it, :class:`RollbackLatencySLAExceededError` is raised
immediately. Results are aggregated into mean, p95 (linear interpolation) and
maximum statistics, then serialized into verifiable JSON receipts.
"""
from __future__ import annotations

import dataclasses
import enum
import functools
import hashlib
import inspect
import json
import math
import os
import re
import struct
import subprocess
import tempfile
import time
import typing
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from cochem_platform.swarm_state_manager import SwarmStateManager

from .state_vector_persistence import (
    BINARY_HEADER_FORMAT,
    BINARY_MAGIC_HEADER,
    StateVectorCheckpointManager,
    StateVectorRecord,
    deserialize_record_binary,
)
from .workspace_rollback_controller import WorkspaceRollbackController

__all__ = [
    "RollbackLatencyBenchmarkRunner",
    "RollbackBenchmarkRunner",
    "RollbackLatencySLAExceededError",
    "LatencySLAExceededError",
    "RollbackBenchmarkError",
    "BenchmarkIntegrityError",
    "RollbackBenchmarkConfig",
    "LatencySample",
    "BenchmarkStatisticalSummary",
    "StateRollbackBenchmarkResult",
    "RollbackBenchmarkReceipt",
    "compute_latency_statistics",
    "compute_mendeleev_provenance_digest",
    "DEFAULT_ROLLBACK_LATENCY_CEILING_MS",
    "DEFAULT_BENCHMARK_ITERATIONS",
    "DEFAULT_WARMUP_ITERATIONS",
    "WORKSPACE_ROLLBACK_PHASE",
    "STATE_RECONSTITUTION_PHASE",
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DEFAULT_ROLLBACK_LATENCY_CEILING_MS: float = 500.0
DEFAULT_BENCHMARK_ITERATIONS: int = 5
DEFAULT_WARMUP_ITERATIONS: int = 1
DEFAULT_CONTROLLER_TIMEOUT_SECONDS: float = 30.0

WORKSPACE_ROLLBACK_PHASE: str = "workspace_rollback"
STATE_RECONSTITUTION_PHASE: str = "state_reconstitution"

MENDELEEV_PROVENANCE_SYMBOLS: Tuple[str, ...] = ("C", "N", "O", "P", "S")
STATE_VECTOR_DIMENSION: int = 32
CHECKPOINT_REF_PREFIX: str = "refs/cochem/checkpoints"
CHECKPOINT_FILE_PREFIX: str = "state_checkpoint"
SWARM_STATE_FILENAME: str = "swarm_state.json"
CHECKPOINT_SUBDIR: str = "checkpoints"

_SCRATCH_DIRNAME = "cochem_rollback_benchmark_scratch"
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BINARY_HEADER_SIZE = struct.calcsize(BINARY_HEADER_FORMAT)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class RollbackBenchmarkError(Exception):
    """Base exception for rollback and reconstitution benchmark operations."""


class RollbackLatencySLAExceededError(RollbackBenchmarkError):
    """Raised when measured rollback or reconstitution latency exceeds the SLA ceiling."""

    def __init__(self, message: str, *, latency_ms: float, ceiling_ms: float, phase: str = "") -> None:
        super().__init__(message)
        self.latency_ms = float(latency_ms)
        self.ceiling_ms = float(ceiling_ms)
        self.phase = phase


# Backwards compatibility alias.
LatencySLAExceededError = RollbackLatencySLAExceededError


class BenchmarkIntegrityError(RollbackBenchmarkError):
    """Raised when post-rollback state verification or hash validation fails."""


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class RollbackBenchmarkConfig(BaseModel):
    """Configuration for rollback / reconstitution latency trials."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    latency_ceiling_ms: float = Field(default=DEFAULT_ROLLBACK_LATENCY_CEILING_MS, gt=0.0)
    iterations: int = Field(default=DEFAULT_BENCHMARK_ITERATIONS, ge=1)
    warmup_iterations: int = Field(default=DEFAULT_WARMUP_ITERATIONS, ge=0)
    auto_purge_untracked: bool = Field(default=True)
    controller_timeout_seconds: float = Field(default=DEFAULT_CONTROLLER_TIMEOUT_SECONDS, gt=0.0)


class LatencySample(BaseModel):
    """A single measured benchmark cycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration: int
    phase: str
    latency_ms: float
    hash_verified: bool
    timestamp_utc: str


class BenchmarkStatisticalSummary(BaseModel):
    """Aggregate latency statistics for one benchmark phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration_count: int
    mean_ms: float
    p95_ms: float
    max_ms: float
    min_ms: float
    latency_ceiling_ms: float
    sub_500ms_sla_verified: bool


class StateRollbackBenchmarkResult(BaseModel):
    """Result of a multi-cycle benchmark for a single phase."""

    model_config = ConfigDict(extra="forbid")

    benchmark_id: str
    phase: str
    summary: BenchmarkStatisticalSummary
    samples: List[LatencySample]
    pre_rollback_state_hash: str
    post_rollback_state_hash: str
    hashes_match: bool
    sub_500ms_sla_verified: bool
    mendeleev_digest: str
    timestamp_utc: str


class RollbackBenchmarkReceipt(BaseModel):
    """Verifiable, JSON-serializable receipt of an end-to-end benchmark run."""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    task_id: str = "TASK-2.19"
    workspace_path: str
    sub_500ms_sla_verified: bool
    workspace_rollback: Optional[StateRollbackBenchmarkResult] = None
    state_reconstitution: Optional[StateRollbackBenchmarkResult] = None
    mendeleev_digest: str
    timestamp_utc: str

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def write_receipt(self, destination: Union[str, Path]) -> Path:
        """Atomically write the receipt JSON to ``destination`` and return its path."""
        target = Path(destination).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_json()
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@functools.lru_cache(maxsize=8)
def compute_mendeleev_provenance_digest(symbols: Tuple[str, ...] = MENDELEEV_PROVENANCE_SYMBOLS) -> str:
    """SHA-256 digest of dynamically retrieved atomic masses (mendeleev)."""
    from mendeleev import element

    payload = "|".join(f"{s}:{float(element(s).mass):.6f}" for s in symbols)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


@functools.lru_cache(maxsize=2)
def _atomic_mass_state_vector(dimension: int = STATE_VECTOR_DIMENSION) -> Tuple[float, ...]:
    """State vector of atomic masses for Z=1..dimension, normalized to the heaviest element."""
    from mendeleev import element

    masses = [float(element(z).mass) for z in range(1, dimension + 1)]
    heaviest = max(masses)
    if heaviest <= 0.0 or not math.isfinite(heaviest):
        raise BenchmarkIntegrityError("mendeleev returned non-physical atomic masses")
    return tuple(m / heaviest for m in masses)


def compute_latency_statistics(
    latencies_ms: Sequence[float],
    latency_ceiling_ms: float = DEFAULT_ROLLBACK_LATENCY_CEILING_MS,
) -> BenchmarkStatisticalSummary:
    """Compute mean, p95 (rank = 0.95*(N-1), linear interpolation), max and min."""
    values = [float(v) for v in latencies_ms]
    if not values:
        raise RollbackBenchmarkError("cannot compute latency statistics from zero samples")
    for value in values:
        if not math.isfinite(value) or value < 0.0:
            raise RollbackBenchmarkError(f"invalid latency sample: {value!r}")
    if not (latency_ceiling_ms > 0.0 and math.isfinite(latency_ceiling_ms)):
        raise RollbackBenchmarkError(f"invalid latency ceiling: {latency_ceiling_ms!r}")

    ordered = sorted(values)
    count = len(ordered)
    mean_ms = math.fsum(ordered) / count
    if count == 1:
        p95_ms = ordered[0]
    else:
        rank = 0.95 * (count - 1)
        lower = int(math.floor(rank))
        upper = min(lower + 1, count - 1)
        p95_ms = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    max_ms = ordered[-1]
    min_ms = ordered[0]
    sla = (
        all(v < latency_ceiling_ms for v in ordered)
        and mean_ms < DEFAULT_ROLLBACK_LATENCY_CEILING_MS
        and p95_ms < DEFAULT_ROLLBACK_LATENCY_CEILING_MS
        and max_ms < DEFAULT_ROLLBACK_LATENCY_CEILING_MS
    )
    return BenchmarkStatisticalSummary(
        iteration_count=count,
        mean_ms=mean_ms,
        p95_ms=p95_ms,
        max_ms=max_ms,
        min_ms=min_ms,
        latency_ceiling_ms=float(latency_ceiling_ms),
        sub_500ms_sla_verified=bool(sla),
    )


def _run_git(workspace: Path, *args: str, timeout: float) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise BenchmarkIntegrityError(
            f"git {' '.join(args)} failed in {workspace} (exit {completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _close_quietly(obj: Any) -> None:
    for method_name in ("close", "shutdown", "stop"):
        method = getattr(obj, method_name, None)
        if callable(method):
            method()
            return


def _canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
        "utf-8"
    )


def _require_hex64(value: Any, what: str) -> str:
    text = str(value)
    if not _HEX64.match(text):
        raise BenchmarkIntegrityError(f"{what} is not a 64-char lowercase SHA-256 hex digest: {text!r}")
    return text


@dataclasses.dataclass
class _WorkspaceBaseline:
    controller: WorkspaceRollbackController
    checkpoint_id: str
    state_hash: str


# --------------------------------------------------------------------------- #
# StateVectorRecord construction (for e2e state store seeding)
# --------------------------------------------------------------------------- #
def _record_field_specs() -> List[Tuple[str, Any, bool]]:
    cls = StateVectorRecord
    specs: List[Tuple[str, Any, bool]] = []
    model_fields = getattr(cls, "model_fields", None)
    if isinstance(model_fields, dict):
        for fname, finfo in model_fields.items():
            specs.append((fname, finfo.annotation, finfo.is_required()))
    elif dataclasses.is_dataclass(cls):
        hints = typing.get_type_hints(cls)
        for fld in dataclasses.fields(cls):
            required = fld.default is dataclasses.MISSING and fld.default_factory is dataclasses.MISSING  # type: ignore[misc]
            specs.append((fld.name, hints.get(fld.name, fld.type), required))
    else:
        for pname, param in inspect.signature(cls).parameters.items():
            if pname == "self":
                continue
            annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
            specs.append((pname, annotation, param.default is inspect.Parameter.empty))
    return specs


def _record_field_value(name: str, annotation: Any, context: Dict[str, Any]) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return next(iter(annotation))
    if typing.get_origin(annotation) is typing.Literal:
        return typing.get_args(annotation)[0]
    vector: Tuple[float, ...] = context["vector"]
    text = str(annotation).lower()
    lname = name.lower()
    if "datetime" in text:
        return datetime.now(timezone.utc)
    if "ndarray" in text:
        import numpy as np

        return np.asarray(vector, dtype=np.float64)
    if "float" in text and any(k in text for k in ("list", "sequence", "tuple", "iterable")):
        return list(vector)
    if "bytes" in text:
        return hashlib.sha256(struct.pack(f"!{len(vector)}d", *vector)).digest()
    if "dict" in text or "mapping" in text:
        return dict(context["provenance"])
    if "bool" in text:
        return True
    if re.search(r"\bint\b", text):
        return len(vector) if "dim" in lname else 1
    if "float" in text:
        return 1.0
    if "str" in text:
        if any(k in lname for k in ("hash", "digest", "sha")):
            return context["digest"]
        if "time" in lname or "date" in lname:
            return _utc_now_iso()
        if "id" in lname:
            return f"rollback-benchmark-{lname}-{context['run_token']}"
        return "task_2_19_rollback_benchmark"
    raise BenchmarkIntegrityError(
        f"cannot construct StateVectorRecord field {name!r} with annotation {annotation!r}"
    )


def _build_state_vector_record(workspace_state_hash: str) -> StateVectorRecord:
    """Build a StateVectorRecord whose vector holds normalized mendeleev atomic masses (Z=1..32)."""
    mendeleev_digest = compute_mendeleev_provenance_digest()
    context: Dict[str, Any] = {
        "vector": _atomic_mass_state_vector(),
        "digest": hashlib.sha256(f"{workspace_state_hash}|{mendeleev_digest}".encode("ascii")).hexdigest(),
        "run_token": uuid.uuid4().hex[:12],
        "provenance": {
            "source": "TASK-2.19 rollback benchmark",
            "vector_basis": "mendeleev atomic mass Z=1..32 normalized",
            "workspace_state_hash": workspace_state_hash,
            "mendeleev_digest": mendeleev_digest,
        },
    }
    kwargs = {
        fname: _record_field_value(fname, annotation, context)
        for fname, annotation, required in _record_field_specs()
        if required
    }
    return StateVectorRecord(**kwargs)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
class RollbackLatencyBenchmarkRunner:
    """Benchmarks workspace rollback and state reconstitution latency against an SLA ceiling."""

    def __init__(
        self,
        workspace_path: Optional[Union[str, Path]] = None,
        config: Optional[RollbackBenchmarkConfig] = None,
    ) -> None:
        self.workspace_path: Optional[Path] = Path(workspace_path).resolve() if workspace_path else None
        self.config: RollbackBenchmarkConfig = config or RollbackBenchmarkConfig()
        self._baselines: Dict[str, _WorkspaceBaseline] = {}
        self._default_controllers: Dict[str, WorkspaceRollbackController] = {}

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def _enforce_ceiling(self, latency_ms: float, phase: str, iteration: int) -> None:
        ceiling = float(self.config.latency_ceiling_ms)
        if latency_ms > ceiling:
            raise RollbackLatencySLAExceededError(
                f"{phase} iteration {iteration} took {latency_ms:.4f} ms, exceeding the "
                f"{ceiling:.4f} ms ceiling",
                latency_ms=latency_ms,
                ceiling_ms=ceiling,
                phase=phase,
            )

    def _build_result(
        self,
        phase: str,
        samples: List[LatencySample],
        pre_hash: str,
        post_hash: str,
    ) -> StateRollbackBenchmarkResult:
        summary = compute_latency_statistics(
            [s.latency_ms for s in samples], latency_ceiling_ms=self.config.latency_ceiling_ms
        )
        hashes_match = pre_hash == post_hash and all(s.hash_verified for s in samples)
        return StateRollbackBenchmarkResult(
            benchmark_id=f"{phase}-{uuid.uuid4().hex}",
            phase=phase,
            summary=summary,
            samples=list(samples),
            pre_rollback_state_hash=_require_hex64(pre_hash, "pre-rollback state hash"),
            post_rollback_state_hash=_require_hex64(post_hash, "post-rollback state hash"),
            hashes_match=hashes_match,
            sub_500ms_sla_verified=bool(summary.sub_500ms_sla_verified and hashes_match),
            mendeleev_digest=compute_mendeleev_provenance_digest(),
            timestamp_utc=_utc_now_iso(),
        )

    # ------------------------------------------------------------------ #
    # Workspace rollback
    # ------------------------------------------------------------------ #
    def _resolve_workspace(self, workspace_path: Optional[Union[str, Path]], controller: Any) -> Path:
        if workspace_path is not None:
            return Path(workspace_path).resolve()
        if self.workspace_path is not None:
            return self.workspace_path
        controller_path = getattr(controller, "workspace_path", None) if controller is not None else None
        if controller_path is not None:
            return Path(controller_path).resolve()
        raise RollbackBenchmarkError("workspace rollback benchmark requires a workspace_path")

    def _resolve_controller(
        self, workspace: Path, controller: Optional[WorkspaceRollbackController]
    ) -> WorkspaceRollbackController:
        if controller is not None:
            return controller
        key = str(workspace)
        existing = self._default_controllers.get(key)
        if existing is None:
            existing = WorkspaceRollbackController(
                workspace_path=workspace,
                checkpoint_ref_prefix=CHECKPOINT_REF_PREFIX,
                auto_purge_untracked=self.config.auto_purge_untracked,
                timeout_seconds=self.config.controller_timeout_seconds,
            )
            self._default_controllers[key] = existing
        return existing

    def _establish_baseline(self, workspace: Path, controller: WorkspaceRollbackController) -> _WorkspaceBaseline:
        key = str(workspace)
        entry = self._baselines.get(key)
        if entry is not None and entry.controller is not controller:
            entry = None
        if controller.is_clean():
            current = _require_hex64(controller.compute_workspace_state_hash(), "workspace state hash")
            if entry is not None and entry.state_hash == current:
                return entry
            checkpoint = controller.create_checkpoint(label=f"rollback-benchmark-baseline-{uuid.uuid4().hex[:12]}")
            entry = _WorkspaceBaseline(controller=controller, checkpoint_id=checkpoint.checkpoint_id, state_hash=current)
            self._baselines[key] = entry
            return entry
        if entry is None:
            raise BenchmarkIntegrityError(
                f"workspace {workspace} has uncommitted changes and no benchmark baseline exists; "
                "refusing to discard work to establish a baseline"
            )
        incident = controller.rollback_to_checkpoint(entry.checkpoint_id)
        realigned = controller.compute_workspace_state_hash()
        if not (bool(incident.verified_clean) and controller.is_clean() and realigned == entry.state_hash):
            raise BenchmarkIntegrityError(
                f"workspace {workspace} could not be realigned to benchmark baseline {entry.checkpoint_id}"
            )
        return entry

    def _mutation_targets(self, workspace: Path) -> Tuple[str, str]:
        listing = _run_git(workspace, "ls-files", "-z", timeout=self.config.controller_timeout_seconds)
        tracked = sorted(
            rel
            for rel in listing.split("\0")
            if rel and (workspace / rel).is_file() and not (workspace / rel).is_symlink()
        )
        if len(tracked) < 2:
            raise BenchmarkIntegrityError(
                f"workspace {workspace} needs at least two tracked regular files to benchmark "
                f"modified + deleted rollback (found {len(tracked)})"
            )
        return tracked[0], tracked[1]

    def _mutate_workspace(self, workspace: Path, targets: Tuple[str, str], iteration: int) -> Optional[Path]:
        modify_rel, delete_rel = targets
        modify_path = workspace / modify_rel
        original = modify_path.read_bytes()
        marker = f"\n# cochem rollback benchmark mutation {iteration} {uuid.uuid4().hex}\n".encode("utf-8")
        modify_path.write_bytes(original + marker)
        (workspace / delete_rel).unlink()
        if not self.config.auto_purge_untracked:
            return None
        artifact = workspace / _SCRATCH_DIRNAME / f"iter_{iteration:04d}" / "nested" / "artifact.bin"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(hashlib.sha256(marker).digest() * 16)
        return artifact

    def _workspace_cycle(
        self,
        workspace: Path,
        baseline: _WorkspaceBaseline,
        targets: Tuple[str, str],
        iteration: int,
    ) -> Tuple[float, str]:
        controller = baseline.controller
        artifact = self._mutate_workspace(workspace, targets, iteration)
        if controller.is_clean():
            raise BenchmarkIntegrityError(f"workspace mutation for iteration {iteration} was not detected")

        start_ns = time.perf_counter_ns()
        incident = controller.rollback_to_checkpoint(baseline.checkpoint_id)
        latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        post_hash = controller.compute_workspace_state_hash()
        verified = (
            bool(incident.verified_clean)
            and controller.is_clean()
            and post_hash == baseline.state_hash
            and (artifact is None or not artifact.exists())
            and (workspace / targets[1]).is_file()
        )
        if not verified:
            raise BenchmarkIntegrityError(
                f"workspace rollback iteration {iteration} failed verification: "
                f"expected state hash {baseline.state_hash}, observed {post_hash}"
            )
        return latency_ms, post_hash

    def _run_workspace_benchmark(
        self,
        workspace: Path,
        controller: Optional[WorkspaceRollbackController],
        iterations: Optional[int],
    ) -> StateRollbackBenchmarkResult:
        if not workspace.is_dir():
            raise RollbackBenchmarkError(f"workspace path does not exist: {workspace}")
        count = int(iterations) if iterations is not None else self.config.iterations
        if count < 1:
            raise RollbackBenchmarkError("iterations must be >= 1")
        ctl = self._resolve_controller(workspace, controller)
        baseline = self._establish_baseline(workspace, ctl)
        targets = self._mutation_targets(workspace)

        for warm in range(self.config.warmup_iterations):
            self._workspace_cycle(workspace, baseline, targets, -(warm + 1))

        samples: List[LatencySample] = []
        post_hash = baseline.state_hash
        for index in range(count):
            latency_ms, post_hash = self._workspace_cycle(workspace, baseline, targets, index)
            samples.append(
                LatencySample(
                    iteration=index,
                    phase=WORKSPACE_ROLLBACK_PHASE,
                    latency_ms=latency_ms,
                    hash_verified=post_hash == baseline.state_hash,
                    timestamp_utc=_utc_now_iso(),
                )
            )
            self._enforce_ceiling(latency_ms, WORKSPACE_ROLLBACK_PHASE, index)
        return self._build_result(WORKSPACE_ROLLBACK_PHASE, samples, baseline.state_hash, post_hash)

    def benchmark_workspace_rollback(
        self,
        controller: Optional[WorkspaceRollbackController] = None,
        iterations: Optional[int] = None,
    ) -> StateRollbackBenchmarkResult:
        """Measure rollback of a real git workspace with modified, deleted and untracked files."""
        workspace = self._resolve_workspace(None, controller)
        return self._run_workspace_benchmark(workspace, controller, iterations)

    # ------------------------------------------------------------------ #
    # State reconstitution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_binary_frame(raw: bytes, source: Path) -> None:
        if len(raw) <= _BINARY_HEADER_SIZE:
            raise BenchmarkIntegrityError(f"binary state record {source.name} is truncated ({len(raw)} bytes)")
        magic, _first, _second, digest_field = struct.unpack_from(BINARY_HEADER_FORMAT, raw, 0)
        if magic != BINARY_MAGIC_HEADER:
            raise BenchmarkIntegrityError(f"binary state record {source.name} has invalid magic {magic!r}")
        digest_text = digest_field.rstrip(b"\x00").decode("ascii", errors="replace").lower()
        _require_hex64(digest_text, f"payload digest of {source.name}")

    def _reconstitute_once(self, checkpoint_dir: Path, state_mgr_path: Path) -> Tuple[float, str, int]:
        """Reconstitute all binary records and the swarm state; return (latency_ms, digest, records)."""
        manager: Optional[SwarmStateManager] = None
        try:
            start_ns = time.perf_counter_ns()
            digest = hashlib.sha256()
            record_count = 0
            for path in sorted(checkpoint_dir.iterdir()):
                if not path.is_file():
                    continue
                raw = path.read_bytes()
                if not raw.startswith(BINARY_MAGIC_HEADER):
                    continue
                self._validate_binary_frame(raw, path)
                try:
                    record = deserialize_record_binary(raw, verify_hash=True)
                except Exception as exc:
                    raise BenchmarkIntegrityError(
                        f"binary state record {path.name} failed deserialization/integrity validation: {exc}"
                    ) from exc
                if not isinstance(record, StateVectorRecord):
                    raise BenchmarkIntegrityError(
                        f"binary state record {path.name} deserialized to {type(record).__name__}"
                    )
                digest.update(path.name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(hashlib.sha256(raw).digest())
                record_count += 1
            if record_count == 0:
                raise BenchmarkIntegrityError(f"no COCHSV01 binary state records found in {checkpoint_dir}")

            manager = SwarmStateManager(
                primary_path=state_mgr_path, sync_debounce_sec=0.0, primary_debounce_sec=0.0
            )
            state = manager.load_state()
            if not isinstance(state, dict):
                raise BenchmarkIntegrityError(
                    f"SwarmStateManager returned {type(state).__name__} instead of a state mapping"
                )
            digest.update(b"swarm\0")
            digest.update(hashlib.sha256(_canonical_json_bytes(state)).digest())
            state_digest = digest.hexdigest()
            latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        finally:
            if manager is not None:
                _close_quietly(manager)
        return latency_ms, state_digest, record_count

    def benchmark_state_reconstitution(
        self,
        checkpoint_dir: Union[str, Path],
        state_mgr_path: Union[str, Path],
        iterations: Optional[int] = None,
    ) -> StateRollbackBenchmarkResult:
        """Measure reconstitution of binary COCHSV01 state vectors and SwarmStateManager JSON."""
        ckpt_dir = Path(checkpoint_dir).resolve()
        swarm_path = Path(state_mgr_path).resolve()
        if not ckpt_dir.is_dir():
            raise BenchmarkIntegrityError(f"checkpoint directory does not exist: {ckpt_dir}")
        if not swarm_path.is_file():
            raise BenchmarkIntegrityError(f"swarm state file does not exist: {swarm_path}")
        count = int(iterations) if iterations is not None else self.config.iterations
        if count < 1:
            raise RollbackBenchmarkError("iterations must be >= 1")

        # Untimed reference reconstitution establishes the pre-benchmark state digest.
        _ref_latency, reference_digest, reference_records = self._reconstitute_once(ckpt_dir, swarm_path)

        for _warm in range(self.config.warmup_iterations):
            self._reconstitute_once(ckpt_dir, swarm_path)

        samples: List[LatencySample] = []
        post_digest = reference_digest
        for index in range(count):
            latency_ms, post_digest, record_count = self._reconstitute_once(ckpt_dir, swarm_path)
            verified = post_digest == reference_digest and record_count == reference_records
            if not verified:
                raise BenchmarkIntegrityError(
                    f"state reconstitution iteration {index} digest {post_digest} diverged from "
                    f"reference {reference_digest}"
                )
            samples.append(
                LatencySample(
                    iteration=index,
                    phase=STATE_RECONSTITUTION_PHASE,
                    latency_ms=latency_ms,
                    hash_verified=verified,
                    timestamp_utc=_utc_now_iso(),
                )
            )
            self._enforce_ceiling(latency_ms, STATE_RECONSTITUTION_PHASE, index)
        return self._build_result(STATE_RECONSTITUTION_PHASE, samples, reference_digest, post_digest)

    # ------------------------------------------------------------------ #
    # End-to-end
    # ------------------------------------------------------------------ #
    def _seed_state_store(self, state_dir: Path, workspace_state_hash: str) -> Tuple[Path, Path]:
        """Ensure a binary COCHSV01 checkpoint and a SwarmStateManager JSON store exist in state_dir."""
        checkpoint_dir = state_dir / CHECKPOINT_SUBDIR
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        has_binary = False
        for path in checkpoint_dir.iterdir():
            if path.is_file():
                with open(path, "rb") as handle:
                    if handle.read(len(BINARY_MAGIC_HEADER)) == BINARY_MAGIC_HEADER:
                        has_binary = True
                        break
        if not has_binary:
            ckpt_manager = StateVectorCheckpointManager(
                checkpoint_dir=checkpoint_dir, prefix=CHECKPOINT_FILE_PREFIX, encoding_format="binary"
            )
            ckpt_manager.save_checkpoint(_build_state_vector_record(workspace_state_hash))

        swarm_path = state_dir / SWARM_STATE_FILENAME
        if not swarm_path.is_file():
            swarm = SwarmStateManager(primary_path=swarm_path, sync_debounce_sec=0.0, primary_debounce_sec=0.0)
            try:
                swarm.save_state(
                    {
                        "task_id": "TASK-2.19",
                        "workspace_state_hash": workspace_state_hash,
                        "mendeleev_digest": compute_mendeleev_provenance_digest(),
                        "latency_ceiling_ms": self.config.latency_ceiling_ms,
                        "seeded_utc": _utc_now_iso(),
                    }
                )
                swarm.flush()
            finally:
                _close_quietly(swarm)
        if not swarm_path.is_file():
            raise BenchmarkIntegrityError(f"SwarmStateManager did not persist state to {swarm_path}")
        return checkpoint_dir, swarm_path

    def run_e2e_benchmark(
        self,
        workspace_path: Optional[Union[str, Path]] = None,
        state_dir: Optional[Union[str, Path]] = None,
    ) -> RollbackBenchmarkReceipt:
        """Execute full multi-cycle statistical trials and return a verified receipt."""
        workspace = self._resolve_workspace(workspace_path, None)
        workspace_result = self._run_workspace_benchmark(workspace, None, None)

        if state_dir is not None:
            state_root = Path(state_dir).resolve()
            state_root.mkdir(parents=True, exist_ok=True)
            checkpoint_dir, swarm_path = self._seed_state_store(state_root, workspace_result.pre_rollback_state_hash)
            state_result = self.benchmark_state_reconstitution(checkpoint_dir, swarm_path)
        else:
            with tempfile.TemporaryDirectory(prefix="cochem_rollback_bench_state_") as tmp:
                checkpoint_dir, swarm_path = self._seed_state_store(
                    Path(tmp), workspace_result.pre_rollback_state_hash
                )
                state_result = self.benchmark_state_reconstitution(checkpoint_dir, swarm_path)

        return RollbackBenchmarkReceipt(
            receipt_id=f"rollback-receipt-{uuid.uuid4().hex}",
            workspace_path=str(workspace),
            sub_500ms_sla_verified=bool(
                workspace_result.sub_500ms_sla_verified and state_result.sub_500ms_sla_verified
            ),
            workspace_rollback=workspace_result,
            state_reconstitution=state_result,
            mendeleev_digest=compute_mendeleev_provenance_digest(),
            timestamp_utc=_utc_now_iso(),
        )


# Parity alias.
RollbackBenchmarkRunner = RollbackLatencyBenchmarkRunner
