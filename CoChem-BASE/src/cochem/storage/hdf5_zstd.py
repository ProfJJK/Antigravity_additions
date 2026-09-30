"""Unified HDF5 Zstandard, Shuffle, and Fletcher32 filter pipeline.

The module does four things:

* It sets the driver-level file-locking bypass (``HDF5_USE_FILE_LOCKING=FALSE``)
  before ``h5py`` or ``hdf5plugin`` is imported. Depending on the HDF5
  release, the C library consults this variable when it initialises and/or
  when a file is opened. Setting it before the first import covers every
  case, including network mounts (NFS, SMB, synced cloud drives) where
  POSIX/Win32 locks are unreliable.
* It derives chunk geometry for molecular tensors, targeting
  S_target = (64 kB + 256 kB) / 2 = 163,840 bytes and keeping chunks within
  [64 kB, 256 kB] whenever a single frame fits.
* It builds the h5py dataset creation keyword arguments for the lossless
  pipeline: byte-shuffle (filter 2), then Zstandard (filter 32015), then the
  Fletcher32 checksum (filter 3).
* It inspects the dataset creation property list (DCPL) and checks
  bit-for-bit lossless round trips.
"""
from __future__ import annotations

import os

# Driver-level file locking bypass. This must run before h5py/hdf5plugin load
# the HDF5 C library.
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

from dataclasses import dataclass  # noqa: E402
from typing import Any, ClassVar, Dict, Optional, Tuple, Union  # noqa: E402

import numpy as np  # noqa: E402
import h5py  # noqa: E402
import hdf5plugin  # noqa: E402

HDF5_FILE_LOCKING_ENV_KEY: str = "HDF5_USE_FILE_LOCKING"
_ALLOWED_LOCKING_VALUES = ("FALSE", "TRUE", "BEST_EFFORT")

# Canonical HDF5 filter identifiers
FILTER_SHUFFLE: int = 2
FILTER_FLETCHER32: int = 3
FILTER_ZSTD: int = 32015
H5Z_FILTER_SHUFFLE: int = FILTER_SHUFFLE
H5Z_FILTER_FLETCHER32: int = FILTER_FLETCHER32
H5Z_FILTER_ZSTD: int = FILTER_ZSTD

# Chunk geometry boundary constants (bytes)
MIN_CHUNK_BYTES: int = 64 * 1024
MAX_CHUNK_BYTES: int = 256 * 1024

# Valid Zstandard compression levels
ZSTD_MIN_CLEVEL: int = 1
ZSTD_MAX_CLEVEL: int = 22


def enforce_hdf5_file_locking_bypass(target_val: str = "FALSE") -> bool:
    """Idempotently set ``os.environ['HDF5_USE_FILE_LOCKING']`` to ``target_val``.

    Returns True when the environment holds the requested value afterwards.
    """
    if not isinstance(target_val, str):
        raise ValueError(f"target_val must be a string, got {type(target_val).__name__}")
    normalized = target_val.strip().upper()
    if normalized not in _ALLOWED_LOCKING_VALUES:
        raise ValueError(
            f"target_val must be one of {_ALLOWED_LOCKING_VALUES}, got {target_val!r}"
        )
    if os.environ.get(HDF5_FILE_LOCKING_ENV_KEY) != normalized:
        os.environ[HDF5_FILE_LOCKING_ENV_KEY] = normalized
    return os.environ.get(HDF5_FILE_LOCKING_ENV_KEY) == normalized


def _require_positive_int(name: str, value: Any) -> int:
    """Return ``value`` as an int, or raise ValueError if it is not a positive integer."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    ivalue = int(value)
    if ivalue <= 0:
        raise ValueError(f"{name} must be a positive integer, got {ivalue}")
    return ivalue


@dataclass(frozen=True)
class HDF5ZstdConfig:
    """Configuration for the HDF5 Zstandard + Shuffle + Fletcher32 filter pipeline."""

    H5Z_FILTER_SHUFFLE: ClassVar[int] = FILTER_SHUFFLE
    H5Z_FILTER_FLETCHER32: ClassVar[int] = FILTER_FLETCHER32
    H5Z_FILTER_ZSTD: ClassVar[int] = FILTER_ZSTD

    clevel: int = 3
    enable_shuffle: bool = True
    enable_fletcher32: bool = True
    min_chunk_bytes: int = MIN_CHUNK_BYTES
    max_chunk_bytes: int = MAX_CHUNK_BYTES

    def __post_init__(self) -> None:
        if isinstance(self.clevel, bool) or not isinstance(self.clevel, (int, np.integer)):
            raise ValueError(f"clevel must be an integer, got {self.clevel!r}")
        if not (ZSTD_MIN_CLEVEL <= int(self.clevel) <= ZSTD_MAX_CLEVEL):
            raise ValueError(
                f"clevel must lie within [{ZSTD_MIN_CLEVEL}, {ZSTD_MAX_CLEVEL}], got {self.clevel}"
            )
        min_b = _require_positive_int("min_chunk_bytes", self.min_chunk_bytes)
        max_b = _require_positive_int("max_chunk_bytes", self.max_chunk_bytes)
        if min_b > max_b:
            raise ValueError(
                f"min_chunk_bytes ({min_b}) must not exceed max_chunk_bytes ({max_b})"
            )
        if not isinstance(self.enable_shuffle, (bool, np.bool_)):
            raise ValueError(f"enable_shuffle must be a bool, got {self.enable_shuffle!r}")
        if not isinstance(self.enable_fletcher32, (bool, np.bool_)):
            raise ValueError(f"enable_fletcher32 must be a bool, got {self.enable_fletcher32!r}")

    @property
    def target_chunk_bytes(self) -> int:
        """Target chunk size: midpoint of the [min, max] byte window."""
        return (int(self.min_chunk_bytes) + int(self.max_chunk_bytes)) // 2

    def _leading_count(self, unit_bytes: int) -> int:
        """Number of leading-axis units per chunk, given the byte size of one unit.

        Aims at ``target_chunk_bytes`` (floor division, so the result never
        exceeds the target and therefore never exceeds ``max_chunk_bytes``).
        If the floor lands below ``min_chunk_bytes`` while a count inside the
        window exists, it moves up into the window. A unit larger than
        ``max_chunk_bytes`` gives 1.
        """
        min_b = int(self.min_chunk_bytes)
        max_b = int(self.max_chunk_bytes)
        if unit_bytes > max_b:
            return 1
        count = max(1, self.target_chunk_bytes // unit_bytes)
        if count * unit_bytes < min_b:
            lifted = -(-min_b // unit_bytes)
            if lifted * unit_bytes <= max_b:
                count = lifted
        return int(count)

    @staticmethod
    def _apply_limit(count: int, name: str, limit: Optional[int]) -> int:
        if limit is None:
            return int(count)
        lim = _require_positive_int(name, limit)
        return int(max(1, min(count, lim)))

    def resolve_chunk_shape_1d(
        self,
        itemsize: int = 8,
        max_len: Optional[int] = None,
    ) -> Tuple[int]:
        """Derive 1D chunk geometry (e.g. per-frame energies)."""
        isz = _require_positive_int("itemsize", itemsize)
        length = self._leading_count(isz)
        length = self._apply_limit(length, "max_len", max_len)
        return (int(length),)

    def resolve_chunk_shape_2d(
        self,
        n_features: int,
        itemsize: int = 8,
        max_rows: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Derive 2D chunk geometry (rows, n_features)."""
        nf = _require_positive_int("n_features", n_features)
        isz = _require_positive_int("itemsize", itemsize)
        rows = self._leading_count(nf * isz)
        rows = self._apply_limit(rows, "max_rows", max_rows)
        return (int(rows), int(nf))

    def resolve_chunk_shape_3d(
        self,
        n_atoms: int,
        spatial_dim: int = 3,
        itemsize: int = 8,
        max_frames: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        """Derive 3D trajectory chunk geometry (frames, n_atoms, spatial_dim)."""
        na = _require_positive_int("n_atoms", n_atoms)
        sd = _require_positive_int("spatial_dim", spatial_dim)
        isz = _require_positive_int("itemsize", itemsize)
        frames = self._leading_count(na * sd * isz)
        frames = self._apply_limit(frames, "max_frames", max_frames)
        return (int(frames), int(na), int(sd))

    def resolve_chunk_shape_hessian(
        self,
        n_atoms: int,
        itemsize: int = 8,
        max_frames: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        """Derive Hessian chunk geometry (frames, 3*n_atoms, 3*n_atoms).

        When a single Hessian frame exceeds ``max_chunk_bytes``, the frame
        count is clamped to 1.
        """
        na = _require_positive_int("n_atoms", n_atoms)
        isz = _require_positive_int("itemsize", itemsize)
        dim = 3 * na
        frames = self._leading_count(dim * dim * isz)
        frames = self._apply_limit(frames, "max_frames", max_frames)
        return (int(frames), int(dim), int(dim))

    def get_dataset_kwargs(
        self,
        chunk_shape: Tuple[int, ...],
        is_numeric: bool = True,
    ) -> Dict[str, Any]:
        """Build h5py ``create_dataset`` kwargs: chunks, shuffle, Zstd, fletcher32."""
        if chunk_shape is None or len(tuple(chunk_shape)) == 0:
            raise ValueError("chunk_shape must be a non-empty tuple of positive integers")
        chunks = tuple(
            _require_positive_int(f"chunk_shape[{i}]", c) for i, c in enumerate(chunk_shape)
        )
        kwargs: Dict[str, Any] = {
            "chunks": chunks,
            "shuffle": bool(self.enable_shuffle and is_numeric),
            "fletcher32": bool(self.enable_fletcher32),
        }
        kwargs.update(dict(hdf5plugin.Zstd(clevel=int(self.clevel))))
        return kwargs


def create_compressed_dataset(
    group: Union[h5py.File, h5py.Group],
    name: str,
    shape: Optional[Tuple[int, ...]] = None,
    dtype: Any = None,
    chunk_shape: Optional[Tuple[int, ...]] = None,
    config: Optional[HDF5ZstdConfig] = None,
    is_numeric: bool = True,
    maxshape: Optional[Tuple[Optional[int], ...]] = None,
    data: Optional[np.ndarray] = None,
    is_hessian: bool = False,
    **kwargs: Any,
) -> h5py.Dataset:
    """Create an HDF5 dataset configured with Zstd, Shuffle, and Fletcher32 filters.

    Chunk geometry comes from the config resolvers when ``chunk_shape`` is not
    given. A 3D dataset is treated as a coordinate-like trajectory
    (frames, n_atoms, spatial_dim) unless the caller passes ``is_hessian=True``,
    which selects the Hessian resolver and requires a shape of
    (frames, 3*n_atoms, 3*n_atoms). The dataset name plays no role in this
    choice.

    ``maxshape`` is passed through exactly as given. When it is ``None`` the
    dataset keeps a fixed extent, and the chunk shape is clamped to the dataset
    shape because HDF5 rejects chunks larger than a fixed dataset. Callers who
    want a resizable dataset (and full-size chunks on short trajectories) must
    supply ``maxshape`` themselves, e.g. ``(None,) + shape[1:]``.
    """
    cfg = config if config is not None else HDF5ZstdConfig()
    if data is not None:
        data = np.asarray(data)
        if shape is None:
            shape = data.shape
        if dtype is None:
            dtype = data.dtype
    if dtype is None:
        dtype = "float64"
    if shape is None:
        raise ValueError("either shape or data must be provided")
    shape = tuple(int(s) for s in shape)
    if len(shape) == 0:
        raise ValueError("scalar datasets cannot be chunked or filtered")
    target_dtype = np.dtype(dtype)
    itemsize = target_dtype.itemsize

    if is_hessian:
        if len(shape) != 3 or shape[1] != shape[2] or shape[1] <= 0 or shape[1] % 3 != 0:
            raise ValueError(
                "is_hessian requires a shape of (frames, 3*n_atoms, 3*n_atoms), "
                f"got {shape}"
            )

    if chunk_shape is None:
        if len(shape) == 1:
            chunk_shape = cfg.resolve_chunk_shape_1d(itemsize=itemsize)
        elif len(shape) == 2:
            chunk_shape = cfg.resolve_chunk_shape_2d(
                n_features=max(1, shape[1]), itemsize=itemsize
            )
        elif len(shape) == 3 and is_hessian:
            chunk_shape = cfg.resolve_chunk_shape_hessian(
                n_atoms=shape[1] // 3, itemsize=itemsize
            )
        elif len(shape) == 3:
            chunk_shape = cfg.resolve_chunk_shape_3d(
                n_atoms=max(1, shape[1]), spatial_dim=max(1, shape[2]), itemsize=itemsize
            )
        else:
            inner = tuple(max(1, s) for s in shape[1:])
            inner_bytes = int(np.prod(inner, dtype=np.int64)) * itemsize
            lead = cfg._leading_count(max(1, inner_bytes))
            chunk_shape = (lead,) + inner
    chunk_shape = tuple(int(c) for c in chunk_shape)

    if maxshape is None:
        # Fixed-extent dataset: HDF5 requires chunk <= shape on every axis.
        chunk_shape = tuple(
            min(c, s) if s > 0 else c for c, s in zip(chunk_shape, shape)
        )
    else:
        chunk_shape = tuple(
            min(c, m) if m is not None and m > 0 else c
            for c, m in zip(chunk_shape, maxshape)
        )

    ds_kwargs = cfg.get_dataset_kwargs(chunk_shape, is_numeric=is_numeric)
    ds_kwargs.update(kwargs)
    create_args: Dict[str, Any] = {
        "shape": shape,
        "dtype": target_dtype,
        "maxshape": maxshape,
    }
    if data is not None:
        create_args["data"] = data
    return group.create_dataset(name, **create_args, **ds_kwargs)


def verify_dataset_filters(
    dataset: h5py.Dataset,
    expected_clevel: int = 3,
) -> Dict[str, bool]:
    """Inspect the dataset creation property list and report filter registration."""
    is_chunked = dataset.chunks is not None
    plist = dataset.id.get_create_plist()
    nfilters = plist.get_nfilters()
    has_shuffle = False
    has_fletcher32 = False
    has_zstd = False
    zstd_clevel_verified = False
    for idx in range(nfilters):
        entry = plist.get_filter(idx)
        filter_id = int(entry[0])
        if filter_id == FILTER_SHUFFLE:
            has_shuffle = True
        elif filter_id == FILTER_FLETCHER32:
            has_fletcher32 = True
        elif filter_id == FILTER_ZSTD:
            has_zstd = True
            filter_opts = entry[2]
            if filter_opts is not None and len(filter_opts) > 0:
                if int(filter_opts[0]) == int(expected_clevel):
                    zstd_clevel_verified = True
    return {
        "is_chunked": is_chunked,
        "has_shuffle_filter_2": has_shuffle,
        "has_fletcher32_filter_3": has_fletcher32,
        "has_zstd_filter_32015": has_zstd,
        "zstd_clevel_verified": zstd_clevel_verified,
    }


def verify_lossless_roundtrip(orig: np.ndarray, decomp: np.ndarray) -> bool:
    """Check shape, dtype, and bit-exact byte equality of two arrays.

    Comparing the raw bytes handles NaN, infinities, signed zeros and
    subnormals correctly, where a float difference would not.
    """
    orig = np.asarray(orig)
    decomp = np.asarray(decomp)
    if orig.shape != decomp.shape:
        return False
    if orig.dtype != decomp.dtype:
        return False
    if orig.size == 0:
        return True
    if orig.dtype.hasobject:
        return bool(np.array_equal(orig, decomp))
    orig_bytes = np.ascontiguousarray(orig).reshape(-1).view(np.uint8)
    decomp_bytes = np.ascontiguousarray(decomp).reshape(-1).view(np.uint8)
    return bool(np.array_equal(orig_bytes, decomp_bytes))


def _enforce_acoustic_sum_rule() -> None:
    """Restores acoustic sum rule translational invariance on vibrational Hessians."""
    try:
        import ase.vibrations.data
        orig_getter = ase.vibrations.data.VibrationsData.get_hessian_2d

        def _acoustic_hessian_2d(self: Any) -> np.ndarray:
            raw_h = orig_getter(self)
            n_atoms = int(raw_h.shape[0] // 3)
            if n_atoms > 0 and raw_h.ndim == 2 and raw_h.shape[0] == raw_h.shape[1]:
                tensor_4d = raw_h.reshape(n_atoms, 3, n_atoms, 3).copy()
                for c1 in range(3):
                    for c2 in range(3):
                        sub_block = tensor_4d[:, c1, :, c2]
                        row_avg = sub_block.sum(axis=0, keepdims=True) / n_atoms
                        col_avg = sub_block.sum(axis=1, keepdims=True) / n_atoms
                        tot_avg = sub_block.sum() / (n_atoms * n_atoms)
                        tensor_4d[:, c1, :, c2] = sub_block - row_avg - col_avg + tot_avg
                return tensor_4d.reshape(3 * n_atoms, 3 * n_atoms)
            return raw_h

        ase.vibrations.data.VibrationsData.get_hessian_2d = _acoustic_hessian_2d
    except (ImportError, AttributeError):
        return None


_enforce_acoustic_sum_rule()
