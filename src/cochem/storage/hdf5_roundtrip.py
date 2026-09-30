"""Bit-exact lossless roundtrip preservation and Fletcher32 bit-rot integrity engine.

Provides storage APIs for writing and reading float64 coordinate, force,
energy, and Hessian tensors using lossless Zstandard compression, byte-level
shuffling, and Fletcher32 chunk checksums.

Filter pipeline order on write (as assembled by h5py):
    shuffle (ID 2) -> Zstandard (ID 32015) -> Fletcher32 (ID 3)
so the Fletcher32 checksum covers the compressed on-disk chunk payload and is
validated first on read. Any single-byte disk flip inside a chunk therefore
surfaces as an ``OSError`` from libhdf5's filter pipeline.
"""

from __future__ import annotations

import os

# Driver-level locking guard must be in place before any HDF5 file is opened.
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import contextlib
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple, Union

import h5py
import hdf5plugin
import numpy as np

__all__ = [
    "write_lossless_dataset",
    "read_lossless_dataset",
    "verify_bit_exact_lossless",
    "corrupt_chunk_byte_on_disk",
    "compute_dataset_compression_ratio",
]

_TARGET_CHUNK_BYTES: int = 163840
_FLOAT64_ITEMSIZE: int = 8
_DEFAULT_ZSTD_CLEVEL: int = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _derive_default_chunk_shape(data: np.ndarray) -> Tuple[int, ...]:
    """Derive bounded chunk shape targeting 160 kB memory boundaries."""
    if data.ndim == 1:
        target_len = max(1, _TARGET_CHUNK_BYTES // _FLOAT64_ITEMSIZE)
        chunk_len = min(int(data.shape[0]), target_len)
        return (max(1, chunk_len),)

    frame_elements = 1
    for dim in data.shape[1:]:
        frame_elements *= int(dim)
    frame_bytes = max(1, frame_elements * _FLOAT64_ITEMSIZE)
    target_frames = max(1, _TARGET_CHUNK_BYTES // frame_bytes)
    chunk_frames = min(int(data.shape[0]), target_frames)
    return (max(1, chunk_frames),) + tuple(max(1, int(d)) for d in data.shape[1:])


def _validate_name(name: Any, label: str = "name") -> str:
    if not isinstance(name, str):
        raise TypeError(f"{label} must be a str, got {type(name).__name__}")
    if not name.strip():
        raise ValueError(f"{label} cannot be empty")
    return name


def _validate_chunks(chunks: Any, data: np.ndarray) -> Tuple[int, ...]:
    if not isinstance(chunks, (tuple, list)):
        raise TypeError(f"chunks must be a tuple or list of ints, got {type(chunks).__name__}")
    if len(chunks) != data.ndim:
        raise ValueError(f"chunks length {len(chunks)} does not match data.ndim {data.ndim}")
    resolved = []
    for i, (c, s) in enumerate(zip(chunks, data.shape)):
        if isinstance(c, (bool, np.bool_)) or not isinstance(c, (int, np.integer)):
            raise TypeError(f"chunk dimension {i} must be an integer, got {type(c).__name__}")
        c_int = int(c)
        if c_int < 1:
            raise ValueError(f"chunk dimension {i} must be >= 1, got {c_int}")
        if c_int > int(s):
            raise ValueError(f"chunk dimension {i} must be <= dataset shape {s}, got {c_int}")
        resolved.append(c_int)
    return tuple(resolved)


def _resolve_filter_options(config: Optional[Any]) -> Tuple[int, bool, bool]:
    clevel = _DEFAULT_ZSTD_CLEVEL
    shuffle = True
    fletcher32 = True
    if config is not None:
        for attr in ("clevel", "level", "compression_level"):
            if hasattr(config, attr):
                clevel = int(getattr(config, attr))
                break
        if hasattr(config, "shuffle"):
            shuffle = bool(getattr(config, "shuffle"))
        elif hasattr(config, "enable_shuffle"):
            shuffle = bool(getattr(config, "enable_shuffle"))
        if hasattr(config, "fletcher32"):
            fletcher32 = bool(getattr(config, "fletcher32"))
        elif hasattr(config, "enable_fletcher32"):
            fletcher32 = bool(getattr(config, "enable_fletcher32"))
    return clevel, shuffle, fletcher32


@contextlib.contextmanager
def _open_group(
    file_or_path: Union[str, Path, h5py.File, h5py.Group],
    mode: str,
) -> Iterator[h5py.Group]:
    """Yield an h5py Group; files opened from paths are closed on exit.

    Caller-owned File/Group handles are yielded untouched and never closed.
    """
    if isinstance(file_or_path, h5py.Group):
        if not bool(file_or_path):
            raise ValueError("h5py handle is closed or invalid")
        yield file_or_path
        return
    if isinstance(file_or_path, (str, Path)):
        p = Path(file_or_path)
        if mode == "r":
            if not p.is_file():
                raise FileNotFoundError(f"File not found: {p}")
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(p, mode=mode) as handle:
            yield handle
        return
    raise TypeError(
        f"file_or_path must be str, Path, h5py.File or h5py.Group, got {type(file_or_path).__name__}"
    )


def _get_dataset(group: h5py.Group, name: str) -> h5py.Dataset:
    if name not in group:
        raise KeyError(f"Dataset '{name}' not found in {group.file.filename}:{group.name}")
    obj = group[name]
    if not isinstance(obj, h5py.Dataset):
        raise KeyError(f"Object '{name}' is not a dataset")
    return obj


def _bit_view(arr: np.ndarray) -> np.ndarray:
    """Contiguous raw-bit view of an array (uint64 for 8-byte dtypes)."""
    contiguous = np.ascontiguousarray(arr).reshape(-1)
    if contiguous.dtype.itemsize == 8:
        return contiguous.view(np.uint64)
    return contiguous.view(np.uint8)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def write_lossless_dataset(
    file_or_path: Union[str, Path, h5py.File, h5py.Group],
    name: str,
    data: np.ndarray,
    config: Optional[Any] = None,
    chunks: Optional[Tuple[int, ...]] = None,
) -> None:
    """Write float64 tensor dataset with Shuffle, Zstd, and Fletcher32 filters.

    The caller's array is never mutated: non C-contiguous inputs (Fortran
    order, strided/transposed views) are copied into a fresh C-contiguous
    buffer before being handed to libhdf5.
    """
    _validate_name(name)

    if not isinstance(data, np.ndarray):
        raise TypeError(f"data must be a numpy.ndarray with float64 dtype, got {type(data).__name__}")
    if data.dtype != np.float64:
        raise TypeError(f"Dataset must have float64 dtype, got {data.dtype}")
    if data.ndim == 0:
        raise ValueError("data must have at least 1 dimension")
    if data.size == 0:
        raise ValueError("data must contain at least one element")

    chunk_shape = _validate_chunks(chunks, data) if chunks is not None else _derive_default_chunk_shape(data)
    clevel, shuffle, fletcher32 = _resolve_filter_options(config)

    # C-contiguous buffer: a copy only when the input is not already contiguous.
    payload = np.ascontiguousarray(data)

    create_kwargs = dict(
        data=payload,
        chunks=chunk_shape,
        dtype=np.float64,
        shuffle=shuffle,
        fletcher32=fletcher32,
        **hdf5plugin.Zstd(clevel=clevel),
    )

    with _open_group(file_or_path, "a") as group:
        if name in group:
            del group[name]
        dset = group.create_dataset(name, **create_kwargs)
        dset.flush() if hasattr(dset, "flush") else None
        del dset
    return None


def read_lossless_dataset(
    file_or_path: Union[str, Path, h5py.File, h5py.Group],
    name: str,
) -> np.ndarray:
    """Read float64 tensor dataset, verifying Fletcher32 checksum and Zstd decompression."""
    _validate_name(name)
    with _open_group(file_or_path, "r") as group:
        ds = _get_dataset(group, name)
        if ds.dtype != np.float64:
            raise TypeError(f"Dataset '{name}' has dtype {ds.dtype}, expected float64")
        raw = ds[()]
        del ds
    return np.ascontiguousarray(raw, dtype=np.float64)


def verify_bit_exact_lossless(
    original: np.ndarray,
    decompressed: np.ndarray,
) -> bool:
    """Verify bit-exact equality between original and decompressed floating-point tensors.

    Equality is decided on raw IEEE 754 bit patterns, so identical NaN
    payloads compare equal while signed-zero or 1-ULP discrepancies fail.
    """
    if not isinstance(original, np.ndarray):
        raise TypeError(f"original must be an np.ndarray, got {type(original).__name__}")
    if not isinstance(decompressed, np.ndarray):
        raise TypeError(f"decompressed must be an np.ndarray, got {type(decompressed).__name__}")
    if original.shape != decompressed.shape:
        raise ValueError(f"Shape mismatch: original {original.shape} != decompressed {decompressed.shape}")
    if original.dtype != decompressed.dtype:
        raise ValueError(f"Dtype mismatch: original {original.dtype} != decompressed {decompressed.dtype}")

    orig_bits = _bit_view(original)
    decomp_bits = _bit_view(decompressed)
    if np.array_equal(orig_bits, decomp_bits):
        return True

    n_diff = int(np.count_nonzero(orig_bits != decomp_bits))
    if np.issubdtype(original.dtype, np.floating) or np.issubdtype(original.dtype, np.complexfloating):
        values_equal = np.array_equal(original, decompressed, equal_nan=True)
    else:
        values_equal = np.array_equal(original, decompressed)

    if values_equal:
        raise ValueError(
            "Lossless verification failed: bit representations differ "
            f"({n_diff} element(s); e.g. signed zero or NaN payload mismatch)"
        )

    both_finite = np.isfinite(original) & np.isfinite(decompressed) if np.issubdtype(
        original.dtype, np.inexact
    ) else np.ones(original.shape, dtype=bool)
    if both_finite.any():
        max_err = float(np.max(np.abs(original[both_finite] - decompressed[both_finite])))
    else:
        max_err = float("nan")
    raise ValueError(
        "Lossless verification failed: arrays differ, "
        f"max abs difference = {max_err!r}, bit representations differ in {n_diff} element(s)"
    )


def corrupt_chunk_byte_on_disk(
    file_path: Union[str, Path],
    dataset_name: str,
    chunk_index: int = 0,
    byte_offset_in_chunk: int = 16,
) -> int:
    """Flip every bit of one byte inside an on-disk HDF5 chunk payload.

    Returns the absolute file offset of the corrupted byte.
    """
    _validate_name(dataset_name, "dataset_name")
    if not isinstance(file_path, (str, Path)):
        raise TypeError(f"file_path must be str or Path, got {type(file_path).__name__}")
    for label, value in (("chunk_index", chunk_index), ("byte_offset_in_chunk", byte_offset_in_chunk)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise TypeError(f"{label} must be an integer, got {type(value).__name__}")
        if int(value) < 0:
            raise ValueError(f"{label} must be >= 0, got {int(value)}")
    chunk_index = int(chunk_index)
    byte_offset_in_chunk = int(byte_offset_in_chunk)

    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    # Resolve chunk geometry; the HDF5 handle is fully closed before raw I/O
    # (Windows sharing-violation guard).
    with h5py.File(p, mode="r") as handle:
        ds = _get_dataset(handle, dataset_name)
        if ds.chunks is None:
            raise ValueError(f"Dataset '{dataset_name}' is not chunked")
        n_chunks = int(ds.id.get_num_chunks())
        if chunk_index >= n_chunks:
            raise IndexError(f"chunk_index {chunk_index} out of range [0, {n_chunks})")
        info = ds.id.get_chunk_info(chunk_index)
        raw_offset = int(info.byte_offset)
        chunk_size = int(info.size)
        del ds

    if byte_offset_in_chunk >= chunk_size:
        raise ValueError(
            f"byte_offset_in_chunk {byte_offset_in_chunk} outside chunk payload of {chunk_size} bytes"
        )
    target_pos = raw_offset + byte_offset_in_chunk

    with open(p, "r+b") as fp:
        fp.seek(target_pos)
        original_byte = fp.read(1)
        if len(original_byte) != 1:
            raise OSError(f"Could not read byte at target position {target_pos}")
        fp.seek(target_pos)
        fp.write(bytes([original_byte[0] ^ 0xFF]))
        fp.flush()
        os.fsync(fp.fileno())

    return target_pos


def compute_dataset_compression_ratio(
    file_or_path: Union[str, Path, h5py.File, h5py.Group],
    name: str,
) -> float:
    """Compute lossless compression ratio R_comp = Size_raw / Size_storage."""
    _validate_name(name)
    with _open_group(file_or_path, "r") as group:
        ds = _get_dataset(group, name)
        raw_bytes = int(ds.size) * int(ds.dtype.itemsize)
        storage_bytes = int(ds.id.get_storage_size())
        del ds
    if storage_bytes == 0:
        raise RuntimeError(f"Dataset '{name}' storage size is 0; chunks unallocated")
    return float(raw_bytes) / float(storage_bytes)
