"""
cochem_platform.io_guardrail — Storage I/O backpressure guardrail.

Measures real host storage state before bursty write workloads (agent task
processing, pytest subprocess batches, kanban file moves). It blocks, with
adaptive backoff, until headroom exists or a timeout expires.

Signals (all read from the live host, never synthesised):
  1. Free-space headroom on the target volume (shutil.disk_usage).
  2. Sustained write throughput across physical disks, from two
     psutil.disk_io_counters() samples taken over a real wall-clock interval.
     If psutil is not installed, this signal is reported as unavailable and
     skipped. No substitute value is invented.

Also provides verify_mendeleev_integrity(), which resolves atomic masses
dynamically through mendeleev (Mendeleev Dynamic Mass Mandate). No static
atomic-weight table exists in this module.

Environment overrides:
  COCHEM_IO_MIN_FREE_BYTES           (default 1 GiB)
  COCHEM_IO_MIN_FREE_FRACTION        (default 0.02)
  COCHEM_IO_MAX_WRITE_BYTES_PER_SEC  (default 400 MiB/s; 0 disables the check)
  COCHEM_IO_SAMPLE_INTERVAL_SEC      (default 0.25)
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import psutil as _psutil
except ImportError:
    _psutil = None

logger = logging.getLogger("cochem_platform.io_guardrail")

_GIB = 1024 ** 3
_MIB = 1024 ** 2


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric %s=%r; using %s", name, raw, default)
        return default
    return value if math.isfinite(value) and value >= 0 else default


@dataclass(frozen=True)
class GuardrailConfig:
    """Thresholds that define 'headroom'."""
    min_free_bytes: int
    min_free_fraction: float
    max_write_bytes_per_sec: float
    sample_interval_sec: float

    @classmethod
    def from_env(cls) -> "GuardrailConfig":
        return cls(
            min_free_bytes=int(_env_float("COCHEM_IO_MIN_FREE_BYTES", float(_GIB))),
            min_free_fraction=_env_float("COCHEM_IO_MIN_FREE_FRACTION", 0.02),
            max_write_bytes_per_sec=_env_float(
                "COCHEM_IO_MAX_WRITE_BYTES_PER_SEC", float(400 * _MIB)),
            sample_interval_sec=max(0.05, _env_float("COCHEM_IO_SAMPLE_INTERVAL_SEC", 0.25)),
        )


@dataclass(frozen=True)
class StorageSample:
    """One real measurement of host storage state."""
    path: str
    total_bytes: int
    free_bytes: int
    write_bytes_per_sec: Optional[float]  # None => counters unavailable on this host
    sampled_at: float

    @property
    def free_fraction(self) -> float:
        return (self.free_bytes / self.total_bytes) if self.total_bytes > 0 else 0.0


def _resolve_probe_path(path: Optional[str]) -> str:
    """Return an existing path on the volume to probe (walks up to an existing ancestor)."""
    candidate = Path(path) if path else Path.cwd()
    candidate = candidate.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return str(candidate)


def _write_bytes_total() -> Optional[int]:
    if _psutil is None:
        return None
    try:
        counters = _psutil.disk_io_counters(perdisk=False)
    except (RuntimeError, OSError) as exc:
        logger.debug("disk_io_counters unavailable: %s", exc)
        return None
    if counters is None:
        return None
    return int(counters.write_bytes)


def sample_storage(path: Optional[str] = None, interval_sec: float = 0.25) -> StorageSample:
    """Take a live storage sample: free space plus write rate over interval_sec."""
    probe = _resolve_probe_path(path)
    before = _write_bytes_total()
    t0 = time.perf_counter()
    rate: Optional[float] = None
    if before is not None:
        time.sleep(interval_sec)
        after = _write_bytes_total()
        elapsed = time.perf_counter() - t0
        if after is not None and elapsed > 0:
            # Counters can wrap/reset on some drivers; a negative delta is not a rate.
            delta = after - before
            rate = float(delta) / elapsed if delta >= 0 else None
    usage = shutil.disk_usage(probe)
    return StorageSample(
        path=probe,
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
        write_bytes_per_sec=rate,
        sampled_at=time.time(),
    )


def evaluate_headroom(sample: StorageSample, config: GuardrailConfig) -> tuple[bool, list[str]]:
    """Return (clear, reasons_blocked) for a sample against config."""
    reasons: list[str] = []
    if sample.free_bytes < config.min_free_bytes:
        reasons.append(
            f"free bytes {sample.free_bytes} < minimum {config.min_free_bytes} on {sample.path}")
    if sample.free_fraction < config.min_free_fraction:
        reasons.append(
            f"free fraction {sample.free_fraction:.4f} < minimum {config.min_free_fraction:.4f}")
    if (config.max_write_bytes_per_sec > 0
            and sample.write_bytes_per_sec is not None
            and sample.write_bytes_per_sec > config.max_write_bytes_per_sec):
        reasons.append(
            f"write rate {sample.write_bytes_per_sec / _MIB:.1f} MiB/s > ceiling "
            f"{config.max_write_bytes_per_sec / _MIB:.1f} MiB/s")
    return (not reasons), reasons


class DiskIOGuardrail:
    """Blocking storage-headroom gate with exponential backoff."""

    def __init__(self, config: Optional[GuardrailConfig] = None,
                 path: Optional[str] = None,
                 max_backoff_sec: float = 5.0) -> None:
        self.config = config or GuardrailConfig.from_env()
        self.path = path
        self.max_backoff_sec = max(0.1, float(max_backoff_sec))
        self.last_sample: Optional[StorageSample] = None
        self.last_reasons: list[str] = []

    def wait(self, timeout_sec: float = 30.0) -> bool:
        """Block until headroom is available (True) or timeout_sec elapses (False)."""
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        backoff = self.config.sample_interval_sec
        attempt = 0
        while True:
            attempt += 1
            sample = sample_storage(self.path, self.config.sample_interval_sec)
            clear, reasons = evaluate_headroom(sample, self.config)
            self.last_sample, self.last_reasons = sample, reasons
            if sample.write_bytes_per_sec is None and attempt == 1:
                logger.info("Disk write-rate counters unavailable; gating on free space only.")
            if clear:
                logger.debug(
                    "Storage headroom clear on %s (attempt %d, free=%d, rate=%s)",
                    sample.path, attempt, sample.free_bytes, sample.write_bytes_per_sec)
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Storage headroom NOT acquired within %.1fs: %s",
                               timeout_sec, "; ".join(reasons))
                return False
            logger.info("Storage backpressure (attempt %d): %s; backing off %.2fs",
                        attempt, "; ".join(reasons), min(backoff, remaining))
            time.sleep(min(backoff, remaining))
            backoff = min(backoff * 2.0, self.max_backoff_sec)


def wait_for_storage_headroom(timeout_sec: float = 30.0,
                              path: Optional[str] = None,
                              config: Optional[GuardrailConfig] = None) -> bool:
    """Block until the storage volume holding `path` (default: cwd) has headroom.

    Returns True when headroom is acquired, False when timeout_sec expires first.
    """
    return DiskIOGuardrail(config=config, path=path).wait(timeout_sec=timeout_sec)


# Symbols only. Masses come from mendeleev at call time.
_INTEGRITY_SYMBOLS: tuple[str, ...] = ("H", "C", "N", "O", "S", "Fe")


def verify_mendeleev_integrity(symbols: Optional[tuple[str, ...]] = None) -> dict[str, float]:
    """Resolve atomic masses dynamically via mendeleev.element(symbol).mass.

    Checks physical invariants: every mass is finite and positive, and mass
    increases strictly with atomic number across the probed elements.
    Returns {symbol: mass}. Raises RuntimeError if an invariant is violated.
    """
    import mendeleev

    probe = symbols or _INTEGRITY_SYMBOLS
    resolved: list[tuple[int, str, float]] = []
    for sym in probe:
        el = mendeleev.element(sym)
        mass = float(el.mass)
        if not math.isfinite(mass) or mass <= 0.0:
            raise RuntimeError(f"mendeleev returned non-physical mass for {sym}: {mass}")
        resolved.append((int(el.atomic_number), sym, mass))

    resolved.sort(key=lambda item: item[0])
    for (z_a, s_a, m_a), (z_b, s_b, m_b) in zip(resolved, resolved[1:]):
        if z_b > z_a and not m_b > m_a:
            raise RuntimeError(
                f"Mass ordering invariant violated: {s_a}(Z={z_a}, {m_a}) vs {s_b}(Z={z_b}, {m_b})")
    return {sym: mass for _, sym, mass in resolved}


__all__ = [
    "GuardrailConfig",
    "StorageSample",
    "DiskIOGuardrail",
    "sample_storage",
    "evaluate_headroom",
    "wait_for_storage_headroom",
    "verify_mendeleev_integrity",
]
