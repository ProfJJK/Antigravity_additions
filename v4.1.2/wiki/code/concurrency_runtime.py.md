# src/cochem/concurrency/runtime.py

`python
"""Two-Staged Concurrency Runtime and Utilities (SRS-412-03).

This module provides foundational runtime utilities for Chapter 3 Two-Staged Concurrency:
1. Subprocess Creation Flags & Process Spawning (SRS-412-03-FR-007)
   Enforces CREATE_NO_WINDOW (0x08000000) | CREATE_NEW_PROCESS_GROUP (0x00000200)
   to protect the Windows Desktop Heap and isolate process groups.
2. Randomized JIT Startup Jitter (SRS-412-03-FR-006)
   Injects randomized delays within [100, 500] ms into daemon polling loops
   to eradicate disk contention and thundering-herd I/O locks.
3. Cognitive Complexity Routing (SRS-412-03-FR-008)
   Maps complexity scores 1-4 to Gemini Flash, 5-8 to Sonnet 5.5, 9-10 to Opus 5.5.
4. Concurrency Profile Telemetry Data Model (SRS-412-03 Section 7)
   Frozen, hashable dataclass for runtime telemetry snapshots across Layer 1 and Layer 2.

Physical, zero-mock execution. Strictly compliant with CoChem Anti-Spoofing Protocol v4.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import logging
import numbers
import os
import random
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.concurrency.runtime")

# ==============================================================================
# [MC-CON-06] Process Creation Flags & Process Spawning (SRS-412-03-FR-007)
# ==============================================================================
CREATE_NO_WINDOW: int = 0x08000000
CREATE_NEW_PROCESS_GROUP: int = 0x00000200
CREATIONFLAGS: int = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
DEFAULT_CREATIONFLAGS: int = CREATIONFLAGS


def enforce_creationflags(flags: int = 0) -> int:
    """Returns ``flags`` with CREATE_NO_WINDOW and CREATE_NEW_PROCESS_GROUP OR-ed in."""
    return flags | CREATIONFLAGS


def _argv(cmd: Sequence[str]) -> str | list[str]:
    """A command string passes through whole; any other sequence becomes a list (never split a str)."""
    return cmd if isinstance(cmd, str) else list(cmd)


def spawn_hidden_process(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Runs ``cmd`` to completion (subprocess.run, text=True by default) with the mandatory flags."""
    flags = enforce_creationflags(kwargs.pop("creationflags", 0))
    kwargs.setdefault("text", True)
    return subprocess.run(_argv(cmd), creationflags=flags, **kwargs)


def spawn_hidden_subprocess(cmd: Sequence[str], **kwargs: Any) -> subprocess.Popen[Any]:
    """Starts ``cmd`` asynchronously (subprocess.Popen) with the mandatory flags; other kwargs pass through."""
    flags = enforce_creationflags(kwargs.pop("creationflags", 0))
    return subprocess.Popen(_argv(cmd), creationflags=flags, **kwargs)


# ==============================================================================
# [MC-CON-07] JIT Startup Jitter & Poll Delays (SRS-412-03-FR-006)
# ==============================================================================
JIT_JITTER_MIN_MS: int = 100
JIT_JITTER_MAX_MS: int = 500
JIT_JITTER_RANGE_MS: tuple[int, int] = (JIT_JITTER_MIN_MS, JIT_JITTER_MAX_MS)
JIT_JITTER_MIN_SEC: float = JIT_JITTER_MIN_MS / 1000.0
JIT_JITTER_MAX_SEC: float = JIT_JITTER_MAX_MS / 1000.0
JIT_JITTER_RANGE_SEC: tuple[float, float] = (JIT_JITTER_MIN_SEC, JIT_JITTER_MAX_SEC)
JitterValue = float


def get_jit_startup_jitter_ms(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Uniform random jitter in ms within [min_ms, max_ms]; ValueError if that leaves [100, 500] ms or min > max."""
    if isinstance(min_ms, bool) or isinstance(max_ms, bool):
        raise ValueError("jitter bounds must be numbers of milliseconds, not bool")
    if not (JIT_JITTER_MIN_MS <= min_ms <= max_ms <= JIT_JITTER_MAX_MS):
        raise ValueError(f"jitter range [{min_ms}, {max_ms}] ms must satisfy 100 <= min_ms <= max_ms <= 500")
    return min(float(max_ms), random.uniform(float(min_ms), float(max_ms)))


def get_jit_startup_jitter_sec(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Same validated draw as get_jit_startup_jitter_ms, expressed in seconds."""
    return get_jit_startup_jitter_ms(min_ms, max_ms) / 1000.0


def inject_jit_startup_jitter(min_ms: int = JIT_JITTER_MIN_MS, max_ms: int = JIT_JITTER_MAX_MS) -> float:
    """Really sleeps (time.sleep) for a random delay in [min_ms, max_ms]; returns that delay in seconds."""
    jitter_sec = get_jit_startup_jitter_sec(min_ms, max_ms)
    time.sleep(jitter_sec)
    return jitter_sec


def layer2_poll_jitter(in_ms: bool = False) -> float:
    """Draws one Layer 2 poll-loop jitter value in [100, 500] ms (ms when in_ms, else seconds); no sleep."""
    return get_jit_startup_jitter_ms() if in_ms else get_jit_startup_jitter_sec()


# Aliases for cross-module compatibility
get_layer2_poll_jitter = layer2_poll_jitter
layer2_poll_jitter_ms = get_jit_startup_jitter_ms
layer2_poll_jitter_sec = get_jit_startup_jitter_sec
inject_layer2_poll_jitter = inject_jit_startup_jitter


# ==============================================================================
# [MC-CON-08] Cognitive Complexity Score Routing (SRS-412-03-FR-008)
# ==============================================================================
MODEL_TIER_FLASH: str = "Gemini Flash"
MODEL_TIER_SONNET: str = "Sonnet 5.5"
MODEL_TIER_OPUS: str = "Opus 5.5"

# Single source of truth for FR-008; route_by_complexity only looks scores up here.
COMPLEXITY_ROUTING_MAP: dict[int, str] = {
    **{score: MODEL_TIER_FLASH for score in range(1, 5)},
    **{score: MODEL_TIER_SONNET for score in range(5, 9)},
    **{score: MODEL_TIER_OPUS for score in range(9, 11)},
}


def route_by_complexity(score: int | float) -> str:
    """Return the model tier for an integral cognitive complexity score in 1..10 (SRS-412-03-FR-008).

    ValueError for bool, non-numbers, non-integral values (4.5, nan, inf) and scores outside 1..10.
    Integral floats such as 4.0 are accepted.
    """
    if isinstance(score, bool) or not isinstance(score, numbers.Real):
        raise ValueError(f"Cognitive complexity score must be an integer 1..10, got {score!r}")
    if not isinstance(score, numbers.Integral) and not float(score).is_integer():
        raise ValueError(f"Cognitive complexity score must be integral, got {score!r}")
    key = int(score)
    if key not in COMPLEXITY_ROUTING_MAP:
        raise ValueError(f"Cognitive complexity score must be between 1 and 10, got {score!r}")
    return COMPLEXITY_ROUTING_MAP[key]


class ComplexityRouter:
    """Cognitive complexity model router (SRS-412-03-FR-008)."""

    @staticmethod
    def route(score: int | float) -> str:
        return route_by_complexity(score)


# ==============================================================================
# [MC-CON-09] Concurrency Profile Telemetry (SRS-412-03 Section 7)
# ==============================================================================
_PROFILE_INT_FIELDS: tuple[str, ...] = (
    "layer1_director_pid",
    "active_subagents",
    "v8_heap_allocated_mb",
    "layer2_active_daemons",
)


@dataclass(frozen=True)
class concurrency_profile:
    """Frozen, hashable runtime concurrency snapshot (SRS-412-03 section 7)."""

    layer1_director_pid: int
    active_subagents: int
    v8_heap_allocated_mb: int
    layer2_active_daemons: int
    jit_jitter_range_ms: tuple[int, int] = JIT_JITTER_RANGE_MS

    def __post_init__(self) -> None:
        for name in _PROFILE_INT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int, got {value!r}")
        rng = tuple(self.jit_jitter_range_ms)
        if (
            len(rng) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in rng)
            or rng[0] < 0
            or rng[0] > rng[1]
        ):
            raise ValueError(f"jit_jitter_range_ms must be two non-negative ints (min <= max), got {self.jit_jitter_range_ms!r}")
        object.__setattr__(self, "jit_jitter_range_ms", rng)

    def to_dict(self) -> dict[str, Any]:
        """Convert profile to serialized dictionary format."""
        return {**asdict(self), "jit_jitter_range_ms": list(self.jit_jitter_range_ms)}

    def to_json(self, indent: int | None = None) -> str:
        """Serialize profile to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> concurrency_profile:
        """Instantiate profile from dictionary, unpacking section 7 wrapper if present."""
        flat = data["concurrency_profile"] if "concurrency_profile" in data else data
        return cls(**{name: flat[name] for name in (*_PROFILE_INT_FIELDS, "jit_jitter_range_ms")})

    @classmethod
    def from_json(cls, json_str: str) -> concurrency_profile:
        """Instantiate profile from JSON string."""
        return cls.from_dict(json.loads(json_str))


# Canonical alias matching TitleCase convention
ConcurrencyProfile = concurrency_profile


# ==============================================================================
# CLI Entrypoint for Diagnostics
# ==============================================================================
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for concurrency runtime diagnostics and utilities."""
    parser = argparse.ArgumentParser(description="Chapter 3 Two-Staged Concurrency Runtime Diagnostics")
    parser.add_argument("--route", type=float, help="Route a cognitive complexity score (1-10) to a model tier")
    parser.add_argument("--jitter", action="store_true", help="Draw and display a random JIT startup jitter delay")
    parser.add_argument("--profile", action="store_true", help="Generate and print a default concurrency profile JSON")
    parser.add_argument("--verify-flags", action="store_true", help="Print verified Windows subprocess creationflags")

    args = parser.parse_args(argv)

    if args.route is not None:
        try:
            tier = route_by_complexity(args.route)
            print(f"Score {args.route} -> {tier}")
            return 0
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if args.jitter:
        jitter_ms = get_jit_startup_jitter_ms()
        jitter_sec = jitter_ms / 1000.0
        print(f"JIT Jitter: {jitter_ms:.2f} ms ({jitter_sec:.4f} s)")
        return 0

    if args.profile:
        prof = concurrency_profile(
            layer1_director_pid=os.getpid(),
            active_subagents=0,
            v8_heap_allocated_mb=0,
            layer2_active_daemons=1,
        )
        print(prof.to_json(indent=2))
        return 0

    if args.verify_flags:
        print(f"CREATIONFLAGS: 0x{CREATIONFLAGS:08X} (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

__all__ = [
    "CREATE_NO_WINDOW",
    "CREATE_NEW_PROCESS_GROUP",
    "CREATIONFLAGS",
    "DEFAULT_CREATIONFLAGS",
    "enforce_creationflags",
    "spawn_hidden_process",
    "spawn_hidden_subprocess",
    "JIT_JITTER_MIN_MS",
    "JIT_JITTER_MAX_MS",
    "JIT_JITTER_RANGE_MS",
    "JIT_JITTER_MIN_SEC",
    "JIT_JITTER_MAX_SEC",
    "JIT_JITTER_RANGE_SEC",
    "JitterValue",
    "get_jit_startup_jitter_ms",
    "get_jit_startup_jitter_sec",
    "inject_jit_startup_jitter",
    "layer2_poll_jitter",
    "get_layer2_poll_jitter",
    "layer2_poll_jitter_ms",
    "layer2_poll_jitter_sec",
    "inject_layer2_poll_jitter",
    "MODEL_TIER_FLASH",
    "MODEL_TIER_SONNET",
    "MODEL_TIER_OPUS",
    "COMPLEXITY_ROUTING_MAP",
    "route_by_complexity",
    "ComplexityRouter",
    "concurrency_profile",
    "ConcurrencyProfile",
    "main",
]

`
