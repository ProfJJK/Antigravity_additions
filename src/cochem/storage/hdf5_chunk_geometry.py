"""Analytical dynamic HDF5 chunk geometry engine for PES trajectory tensors.

This module derives chunk shapes for chunked HDF5 datasets that store
potential-energy-surface (PES) trajectory tensors. Every derivation is a
closed-form expression of the per-frame byte footprint, so chunk footprints
``S_chunk`` stay inside the memory window

    MIN_CHUNK_BYTES (64 kB) <= S_chunk <= MAX_CHUNK_BYTES (256 kB)

whenever a single frame fits into that window.

Supported tensors
-----------------
* Atomic coordinates / Cartesian forces: ``(N_chunk, N_atoms, 3)`` with
  ``N_chunk = max(1, floor(TARGET / (3 * N_atoms * itemsize)))``.
* Scalar potential energies: ``(N_chunk,)`` with
  ``N_chunk = floor(TARGET / itemsize)`` (``20480`` for float64).
* Cartesian Hessian trajectories: ``(N_chunk, 3N, 3N)`` with
  ``N_chunk = max(1, floor(TARGET / F_frame))``, ``F_frame = 9 N^2 itemsize``.
* Static single-structure Hessians: ``(3N, 3N)``.

Clamping semantics
------------------
For trajectory tensors the result is *clamped* when the analytical frame count
``floor(TARGET / F_frame)`` collapses to zero (a single frame is larger than the
target footprint) and is lifted to one by the ``max(1, ...)`` guard, or when it
is reduced by an explicit ``max_frames`` / ``max_len`` cap. For the static 2D
Hessian there is no frame axis to scale, so the chunk is the whole matrix and
it is reported as clamped when its footprint exceeds the upper bound.
"""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, Union

__all__ = [
    "MIN_CHUNK_BYTES",
    "MAX_CHUNK_BYTES",
    "TARGET_CHUNK_BYTES",
    "DEFAULT_FLOAT_ITEMSIZE",
    "DEFAULT_SCALAR_ENERGY_CHUNK_ELEMENTS",
    "SUPPORTED_TENSORS",
    "ChunkGeometryResult",
    "DynamicChunkGeometryEngine",
    "DynamicChunkGeometry",
    "compute_chunk_byte_size",
    "derive_coordinates_chunk_shape",
    "derive_forces_chunk_shape",
    "derive_scalar_energy_chunk_shape",
    "derive_energy_chunk_shape",
    "derive_hessian_chunk_shape",
    "derive_tensor_chunk_geometry",
    "validate_chunk_memory_boundaries",
]

MIN_CHUNK_BYTES: int = 65_536  # 64 kB lower boundary
MAX_CHUNK_BYTES: int = 262_144  # 256 kB upper boundary
TARGET_CHUNK_BYTES: int = 163_840  # 160 kB target midpoint
DEFAULT_FLOAT_ITEMSIZE: int = 8  # IEEE 754 float64
DEFAULT_SCALAR_ENERGY_CHUNK_ELEMENTS: int = TARGET_CHUNK_BYTES // DEFAULT_FLOAT_ITEMSIZE

SUPPORTED_TENSORS: Tuple[str, ...] = ("coordinates", "forces", "energy", "hessian")

_CARTESIAN_DIM: int = 3


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _as_int(value: object, name: str) -> int:
    """Convert an integral value to ``int``; reject bools, floats and others."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, got bool {value!r}")
    try:
        return int(operator.index(value))  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _positive_int(value: object, name: str) -> int:
    ivalue = _as_int(value, name)
    if ivalue <= 0:
        raise ValueError(f"{name} must be strictly positive, got {ivalue}")
    return ivalue


def _optional_positive_int(value: Optional[object], name: str) -> Optional[int]:
    if value is None:
        return None
    return _positive_int(value, name)


# --------------------------------------------------------------------------- #
# Result dataclass
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChunkGeometryResult:
    """Structured report of a derived chunk geometry."""

    shape: Tuple[int, ...]
    total_elements: int
    total_byte_size: int
    clamped: bool

    @property
    def element_count(self) -> int:
        return self.total_elements

    @property
    def byte_size(self) -> int:
        return self.total_byte_size


# --------------------------------------------------------------------------- #
# Core analytical derivations (bounds passed explicitly)
# --------------------------------------------------------------------------- #
def compute_chunk_byte_size(
    chunk_shape: Sequence[int],
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
) -> int:
    """Compute the total byte footprint of a chunk shape.

    Raises ``ValueError`` if ``chunk_shape`` is empty, ``itemsize <= 0`` or any
    dimension is ``<= 0``.
    """
    item = _positive_int(itemsize, "itemsize")
    dims = tuple(chunk_shape)
    if len(dims) == 0:
        raise ValueError("chunk_shape must contain at least one dimension")
    elements = 1
    for axis, dim in enumerate(dims):
        elements *= _positive_int(dim, f"chunk_shape[{axis}]")
    return elements * item


def _derive_frame_count(
    frame_bytes: int,
    target_bytes: int,
    max_frames: Optional[int],
) -> Tuple[int, bool]:
    """Return ``(n_chunk, clamped)`` for a frame of ``frame_bytes`` bytes."""
    raw = target_bytes // frame_bytes
    n_chunk = max(1, raw)
    clamped = raw < 1
    if max_frames is not None and n_chunk > max_frames:
        n_chunk = max_frames
        clamped = True
    return n_chunk, clamped


def _trajectory_vector_geometry(
    n_atoms: int,
    itemsize: int,
    max_frames: Optional[int],
    target_bytes: int,
) -> Tuple[Tuple[int, int, int], bool]:
    n = _positive_int(n_atoms, "n_atoms")
    item = _positive_int(itemsize, "itemsize")
    cap = _optional_positive_int(max_frames, "max_frames")
    frame_bytes = _CARTESIAN_DIM * n * item
    n_chunk, clamped = _derive_frame_count(frame_bytes, target_bytes, cap)
    return (n_chunk, n, _CARTESIAN_DIM), clamped


def _scalar_geometry(
    itemsize: int,
    max_len: Optional[int],
    target_bytes: int,
) -> Tuple[Tuple[int], bool]:
    item = _positive_int(itemsize, "itemsize")
    cap = _optional_positive_int(max_len, "max_len")
    n_chunk, clamped = _derive_frame_count(item, target_bytes, cap)
    return (n_chunk,), clamped


def _hessian_geometry(
    n_atoms: int,
    itemsize: int,
    max_frames: Optional[int],
    as_2d: bool,
    target_bytes: int,
    max_bytes: int,
) -> Tuple[Union[Tuple[int, int, int], Tuple[int, int]], bool]:
    n = _positive_int(n_atoms, "n_atoms")
    item = _positive_int(itemsize, "itemsize")
    cap = _optional_positive_int(max_frames, "max_frames")
    dim = _CARTESIAN_DIM * n
    frame_bytes = dim * dim * item
    if as_2d:
        # Static matrix: the chunk is the full matrix; it is flagged as clamped
        # when that irreducible footprint exceeds the upper memory bound.
        return (dim, dim), frame_bytes > max_bytes
    raw = target_bytes // frame_bytes
    n_chunk = max(1, raw)
    clamped = frame_bytes > max_bytes
    if cap is not None and n_chunk > cap:
        n_chunk = cap
        clamped = True
    return (n_chunk, dim, dim), clamped


def _validate_boundaries(
    chunk_shape: Sequence[int],
    itemsize: int,
    n_atoms: Optional[int],
    is_hessian: bool,
    raise_on_error: bool,
    min_bytes: int,
    max_bytes: int,
) -> bool:
    dims = tuple(_positive_int(d, f"chunk_shape[{i}]") for i, d in enumerate(chunk_shape))
    size = compute_chunk_byte_size(dims, itemsize)
    in_window = min_bytes <= size <= max_bytes

    oversized_single_frame = False
    frame_bytes = size
    if is_hessian and len(dims) >= 2:
        n_chunk = dims[0] if len(dims) == 3 else 1
        if n_atoms is not None:
            n = _positive_int(n_atoms, "n_atoms")
            dim = _CARTESIAN_DIM * n
            if dims[-2:] != (dim, dim):
                raise ValueError(
                    f"Hessian chunk trailing dims {dims[-2:]} inconsistent with "
                    f"n_atoms={n} (expected {(dim, dim)})"
                )
            frame_bytes = dim * dim * _positive_int(itemsize, "itemsize")
        else:
            frame_bytes = compute_chunk_byte_size(dims[-2:], itemsize)
        oversized_single_frame = n_chunk == 1 and frame_bytes > max_bytes
    elif n_atoms is not None:
        _positive_int(n_atoms, "n_atoms")

    valid = in_window or oversized_single_frame
    if not valid and raise_on_error:
        raise ValueError(
            f"chunk shape {dims} with itemsize={itemsize} occupies {size} bytes, "
            f"outside [{min_bytes}, {max_bytes}] and not an oversized single frame "
            f"(frame footprint {frame_bytes} bytes)"
        )
    return valid


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DynamicChunkGeometryEngine:
    """Configurable analytical chunk geometry engine with validated bounds."""

    min_chunk_bytes: int = MIN_CHUNK_BYTES
    max_chunk_bytes: int = MAX_CHUNK_BYTES
    target_chunk_bytes: int = TARGET_CHUNK_BYTES
    default_itemsize: int = DEFAULT_FLOAT_ITEMSIZE

    def __post_init__(self) -> None:
        min_b = _as_int(self.min_chunk_bytes, "min_chunk_bytes")
        max_b = _as_int(self.max_chunk_bytes, "max_chunk_bytes")
        target_b = _as_int(self.target_chunk_bytes, "target_chunk_bytes")
        item = _as_int(self.default_itemsize, "default_itemsize")
        if min_b <= 0:
            raise ValueError(f"min_chunk_bytes must be strictly positive, got {min_b}")
        if max_b <= min_b:
            raise ValueError(
                f"max_chunk_bytes ({max_b}) must exceed min_chunk_bytes ({min_b})"
            )
        if not (min_b <= target_b <= max_b):
            raise ValueError(
                f"target_chunk_bytes ({target_b}) must be bounded within [{min_b}, {max_b}]"
            )
        if item <= 0:
            raise ValueError(f"default_itemsize must be strictly positive, got {item}")

    def _item(self, itemsize: Optional[int]) -> int:
        return self.default_itemsize if itemsize is None else itemsize

    def derive_coordinates_chunk_shape(
        self,
        n_atoms: int,
        itemsize: Optional[int] = None,
        max_frames: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        shape, _ = _trajectory_vector_geometry(
            n_atoms, self._item(itemsize), max_frames, self.target_chunk_bytes
        )
        return shape

    def derive_forces_chunk_shape(
        self,
        n_atoms: int,
        itemsize: Optional[int] = None,
        max_frames: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        shape, _ = _trajectory_vector_geometry(
            n_atoms, self._item(itemsize), max_frames, self.target_chunk_bytes
        )
        return shape

    def derive_scalar_energy_chunk_shape(
        self,
        itemsize: Optional[int] = None,
        max_len: Optional[int] = None,
    ) -> Tuple[int]:
        shape, _ = _scalar_geometry(self._item(itemsize), max_len, self.target_chunk_bytes)
        return shape

    def derive_hessian_chunk_shape(
        self,
        n_atoms: int,
        itemsize: Optional[int] = None,
        max_frames: Optional[int] = None,
        as_2d: bool = False,
    ) -> Union[Tuple[int, int, int], Tuple[int, int]]:
        shape, _ = _hessian_geometry(
            n_atoms,
            self._item(itemsize),
            max_frames,
            as_2d,
            self.target_chunk_bytes,
            self.max_chunk_bytes,
        )
        return shape

    def derive_tensor_chunk_geometry(
        self,
        tensor_name: str,
        n_atoms: Optional[int] = None,
        itemsize: Optional[int] = None,
        max_frames: Optional[int] = None,
        as_2d: bool = False,
    ) -> ChunkGeometryResult:
        if not isinstance(tensor_name, str):
            raise ValueError(f"tensor_name must be a string, got {tensor_name!r}")
        name = tensor_name.strip().lower()
        item = _positive_int(self._item(itemsize), "itemsize")
        shape: Tuple[int, ...]
        if name in ("coordinates", "forces"):
            if n_atoms is None:
                raise ValueError(f"n_atoms is required for tensor '{name}'")
            shape, clamped = _trajectory_vector_geometry(
                n_atoms, item, max_frames, self.target_chunk_bytes
            )
        elif name == "energy":
            shape, clamped = _scalar_geometry(item, max_frames, self.target_chunk_bytes)
        elif name == "hessian":
            if n_atoms is None:
                raise ValueError("n_atoms is required for tensor 'hessian'")
            shape, clamped = _hessian_geometry(
                n_atoms,
                item,
                max_frames,
                as_2d,
                self.target_chunk_bytes,
                self.max_chunk_bytes,
            )
        else:
            raise ValueError(
                f"unknown tensor identifier {tensor_name!r}; expected one of {SUPPORTED_TENSORS}"
            )
        total_elements = math.prod(shape)
        return ChunkGeometryResult(
            shape=tuple(shape),
            total_elements=total_elements,
            total_byte_size=total_elements * item,
            clamped=bool(clamped),
        )

    def validate_chunk_memory_boundaries(
        self,
        chunk_shape: Sequence[int],
        itemsize: Optional[int] = None,
        n_atoms: Optional[int] = None,
        is_hessian: bool = False,
        raise_on_error: bool = False,
    ) -> bool:
        return _validate_boundaries(
            chunk_shape,
            self._item(itemsize),
            n_atoms,
            is_hessian,
            raise_on_error,
            self.min_chunk_bytes,
            self.max_chunk_bytes,
        )


DynamicChunkGeometry = DynamicChunkGeometryEngine


# --------------------------------------------------------------------------- #
# Module-level API (default memory envelope)
# --------------------------------------------------------------------------- #
def derive_coordinates_chunk_shape(
    n_atoms: int,
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    max_frames: Optional[int] = None,
) -> Tuple[int, int, int]:
    """Derive ``(N_chunk, N_atoms, 3)`` with
    ``N_chunk = max(1, floor(TARGET_CHUNK_BYTES / (3 * n_atoms * itemsize)))``."""
    shape, _ = _trajectory_vector_geometry(n_atoms, itemsize, max_frames, TARGET_CHUNK_BYTES)
    return shape


def derive_forces_chunk_shape(
    n_atoms: int,
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    max_frames: Optional[int] = None,
) -> Tuple[int, int, int]:
    """Derive ``(N_chunk, N_atoms, 3)`` for Cartesian forces (same as coordinates)."""
    shape, _ = _trajectory_vector_geometry(n_atoms, itemsize, max_frames, TARGET_CHUNK_BYTES)
    return shape


def derive_scalar_energy_chunk_shape(
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    max_len: Optional[int] = None,
) -> Tuple[int]:
    """Derive ``(N_chunk,)`` with ``N_chunk = floor(TARGET_CHUNK_BYTES / itemsize)``."""
    shape, _ = _scalar_geometry(itemsize, max_len, TARGET_CHUNK_BYTES)
    return shape


derive_energy_chunk_shape = derive_scalar_energy_chunk_shape


def derive_hessian_chunk_shape(
    n_atoms: int,
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    max_frames: Optional[int] = None,
    as_2d: bool = False,
) -> Union[Tuple[int, int, int], Tuple[int, int]]:
    """Derive Hessian chunk shapes.

    * ``as_2d=True``: static single-structure matrix ``(3N, 3N)``.
    * ``as_2d=False``: trajectory ``(N_chunk, 3N, 3N)`` with
      ``N_chunk = max(1, floor(TARGET_CHUNK_BYTES / F_frame))``; a frame larger
      than the target (and therefore any frame above ``MAX_CHUNK_BYTES``) is
      stored as a single-frame chunk.
    """
    shape, _ = _hessian_geometry(
        n_atoms, itemsize, max_frames, as_2d, TARGET_CHUNK_BYTES, MAX_CHUNK_BYTES
    )
    return shape


_DEFAULT_ENGINE = DynamicChunkGeometryEngine()


def derive_tensor_chunk_geometry(
    tensor_name: str,
    n_atoms: Optional[int] = None,
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    max_frames: Optional[int] = None,
    as_2d: bool = False,
) -> ChunkGeometryResult:
    """Unified dispatcher for ``'coordinates'``, ``'forces'``, ``'energy'`` and
    ``'hessian'``. For ``'energy'`` the ``max_frames`` argument caps the series
    chunk length."""
    return _DEFAULT_ENGINE.derive_tensor_chunk_geometry(
        tensor_name,
        n_atoms=n_atoms,
        itemsize=itemsize,
        max_frames=max_frames,
        as_2d=as_2d,
    )


def validate_chunk_memory_boundaries(
    chunk_shape: Sequence[int],
    itemsize: int = DEFAULT_FLOAT_ITEMSIZE,
    n_atoms: Optional[int] = None,
    is_hessian: bool = False,
    raise_on_error: bool = False,
) -> bool:
    """Validate ``64 kB <= S_chunk <= 256 kB`` OR, for Hessians,
    ``(N_chunk == 1 AND F_frame > 256 kB)``."""
    return _validate_boundaries(
        chunk_shape,
        itemsize,
        n_atoms,
        is_hessian,
        raise_on_error,
        MIN_CHUNK_BYTES,
        MAX_CHUNK_BYTES,
    )
