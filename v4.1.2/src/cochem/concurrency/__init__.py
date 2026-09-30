"""Concurrency subsystem for CoChem V4.1.2 (SRS-412-03)."""
from __future__ import annotations

from cochem.concurrency.runtime import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    DEFAULT_CREATIONFLAGS,
    JIT_JITTER_MAX_MS,
    JIT_JITTER_MAX_SEC,
    JIT_JITTER_MIN_MS,
    JIT_JITTER_MIN_SEC,
    JIT_JITTER_RANGE_MS,
    JIT_JITTER_RANGE_SEC,
    JitterValue,
    get_layer2_poll_jitter,
    inject_jit_startup_jitter,
    inject_layer2_poll_jitter,
    layer2_poll_jitter,
    layer2_poll_jitter_ms,
    layer2_poll_jitter_sec,
    spawn_hidden_subprocess,
)

__all__ = [
    "CREATE_NEW_PROCESS_GROUP",
    "CREATE_NO_WINDOW",
    "DEFAULT_CREATIONFLAGS",
    "JIT_JITTER_MAX_MS",
    "JIT_JITTER_MAX_SEC",
    "JIT_JITTER_MIN_MS",
    "JIT_JITTER_MIN_SEC",
    "JIT_JITTER_RANGE_MS",
    "JIT_JITTER_RANGE_SEC",
    "JitterValue",
    "get_layer2_poll_jitter",
    "inject_jit_startup_jitter",
    "inject_layer2_poll_jitter",
    "layer2_poll_jitter",
    "layer2_poll_jitter_ms",
    "layer2_poll_jitter_sec",
    "spawn_hidden_subprocess",
]
