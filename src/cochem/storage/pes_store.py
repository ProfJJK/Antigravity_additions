"""Production SWMR-enabled PES HDF5 trajectory store (Tasks 20.1082 - 20.1085).

The module owns both halves of the CoChem potential-energy-surface (PES)
trajectory container:

* **Initialisation** - driver-level HDF5 file-lock bypass
  (``HDF5_USE_FILE_LOCKING=FALSE``) established *before* ``h5py`` is imported,
  cross-process writer exclusion through :class:`filelock.FileLock`, a
  ``libver="latest"`` container, pre-allocated extensible datasets with
  analytical chunk geometries (64 KiB <= chunk <= 256 KiB) and Zstandard +
  shuffle + Fletcher32 filters, followed by an irreversible switch to SWMR
  write mode.
* **Streaming** - frame appends (single frame or batch) with unit conversion
  to storage units (Hartree, Hartree/Bohr, Angstrom) and lock-free SWMR
  readers (``mode="r"``) that observe appended frames after ``refresh()``.

Datasets (``N`` atoms):

============== ======================== ========================== =======
dataset        initial shape            maxshape                   dtype
============== ======================== ========================== =======
/coordinates   (0, N, 3)  [Angstrom]    (None, N, 3)               float64
/forces        (0, N, 3)  [Ha/Bohr]     (None, N, 3)               float64
/hessians      (0, 3N, 3N)              (None, 3N, 3N)             float64
/energies      (0,)       [Hartree]     (None,)                    float64
/atomic_numbers (N,)                    (N,)  (fixed)              int32
============== ======================== ========================== =======

Elemental masses are resolved dynamically through
``mendeleev.element(int(Z)).mass``; the module contains no static mass table.
Physical constants for unit conversion come from ``ase.units`` (CODATA).

Every append writes forces, hessians, coordinates and finally energies, with a
per-dataset flush after each write. Readers derive the visible frame count from
``min(len(coordinates), len(energies))``, so a frame becomes visible only after
all of its data has been committed.
"""
from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Driver-level lock bypass. MUST precede the first ``import h5py`` so that the
# HDF5 C library reads the setting when it is initialised. Cross-process
# exclusion is enforced by ``filelock.FileLock`` below instead.
# --------------------------------------------------------------------------- #
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

import inspect  # noqa: E402
import math  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union  # noqa: E402

import h5py  # noqa: E402
import hdf5plugin  # noqa: E402  (registers the Zstd filter, id 32015)
import numpy as np  # noqa: E402
from ase import units as ase_units  # noqa: E402
from filelock import FileLock, Timeout  # noqa: E402
from mendeleev import element  # noqa: E402

from cochem.storage.hdf5_zstd import HDF5ZstdConfig  # noqa: E402

__all__ = [
    "SCHEMA_VERSION",
    "CREATED_BY",
    "ZSTD_FILTER_ID",
    "CHUNK_MIN_BYTES",
    "CHUNK_TARGET_BYTES",
    "CHUNK_MAX_BYTES",
    "CorruptChunkError",
    "SWMRPESStore",
    "elemental_mass_amu",
    "resolve_atomic_masses_amu",
    "derive_trajectory_chunk_shape",
    "get_hartree_in_ev",
    "get_bohr_in_angstrom",
    "convert_ev_to_hartree",
    "convert_hartree_to_ev",
    "convert_energy",
    "convert_force",
]

SCHEMA_VERSION = "2.0.0"
CREATED_BY = "CoChem-SWMRPESStore-v4"
ZSTD_FILTER_ID = 32015

CHUNK_MIN_BYTES = 64 * 1024
CHUNK_TARGET_BYTES = 160 * 1024
CHUNK_MAX_BYTES = 256 * 1024

_FLOAT_DTYPE = np.dtype("float64")
_INT_DTYPE = np.dtype("int32")

_TRAJECTORY_DATASETS = ("coordinates", "forces", "hessians", "energies")
_TOPOLOGY_DATASET = "atomic_numbers"

_MAX_ATOMIC_NUMBER = 118

_STORAGE_ENERGY_UNIT = "Hartree"
_STORAGE_FORCE_UNIT = "Hartree/Bohr"
_STORAGE_LENGTH_UNIT = "Angstrom"

# Exact mandated lockout message for late dataset creation (AC3).
_DATASET_LOCKOUT_MESSAGE = "Dataset creation prohibited after SWMR mode activation."


# --------------------------------------------------------------------------- #
# Physical constants and unit conversion
# --------------------------------------------------------------------------- #
def get_hartree_in_ev() -> float:
    """Return one Hartree expressed in electron-volts (CODATA via ase.units)."""
    return float(ase_units.Hartree)


def get_bohr_in_angstrom() -> float:
    """Return one Bohr radius expressed in Angstrom (CODATA via ase.units)."""
    return float(ase_units.Bohr)


def _return_like(result: np.ndarray) -> Union[float, np.ndarray]:
    if result.ndim == 0:
        return float(result)
    return result


def convert_ev_to_hartree(value: Any) -> Union[float, np.ndarray]:
    """Convert energies from eV to Hartree."""
    return _return_like(np.asarray(value, dtype=np.float64) / get_hartree_in_ev())


def convert_hartree_to_ev(value: Any) -> Union[float, np.ndarray]:
    """Convert energies from Hartree to eV."""
    return _return_like(np.asarray(value, dtype=np.float64) * get_hartree_in_ev())


def _canonical_energy_unit(unit: str) -> str:
    key = str(unit).strip().lower().replace(" ", "")
    if key == "ev":
        return "ev"
    if key in ("hartree", "ha", "eh"):
        return "hartree"
    if key in ("kcal/mol", "kcalmol-1"):
        return "kcal/mol"
    if key in ("kj/mol", "kjmol-1"):
        return "kj/mol"
    raise ValueError(f"unsupported energy unit {unit!r}")


def _energy_unit_in_ev(canonical: str) -> float:
    if canonical == "ev":
        return 1.0
    if canonical == "hartree":
        return get_hartree_in_ev()
    if canonical == "kcal/mol":
        return float(ase_units.kcal / ase_units.mol)
    return float(ase_units.kJ / ase_units.mol)


def convert_energy(values: Any, from_unit: str, to_unit: str) -> Union[float, np.ndarray]:
    """Convert energies between eV, Hartree, kcal/mol and kJ/mol."""
    src = _canonical_energy_unit(from_unit)
    dst = _canonical_energy_unit(to_unit)
    arr = np.array(values, dtype=np.float64)
    if src == dst:
        return _return_like(arr)
    return _return_like((arr * _energy_unit_in_ev(src)) / _energy_unit_in_ev(dst))


def _canonical_force_unit(unit: str) -> str:
    key = str(unit).strip().lower().replace(" ", "").replace("\u00e5", "angstrom")
    if key in ("ev/angstrom", "ev/ang"):
        return "ev/angstrom"
    if key in ("hartree/bohr", "ha/bohr", "eh/bohr"):
        return "hartree/bohr"
    raise ValueError(f"unsupported force unit {unit!r}")


def _force_unit_in_ev_per_angstrom(canonical: str) -> float:
    if canonical == "ev/angstrom":
        return 1.0
    return get_hartree_in_ev() / get_bohr_in_angstrom()


def convert_force(values: Any, from_unit: str, to_unit: str) -> Union[float, np.ndarray]:
    """Convert forces between eV/Angstrom and Hartree/Bohr."""
    src = _canonical_force_unit(from_unit)
    dst = _canonical_force_unit(to_unit)
    arr = np.array(values, dtype=np.float64)
    if src == dst:
        return _return_like(arr)
    return _return_like((arr * _force_unit_in_ev_per_angstrom(src)) / _force_unit_in_ev_per_angstrom(dst))


# --------------------------------------------------------------------------- #
# Dynamic Mendeleev mass resolution
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=128)
def elemental_mass_amu(z: int) -> float:
    """Return the standard atomic weight (amu) of element ``z``.

    The value is resolved from the mendeleev database at runtime. The cache
    only avoids repeated SQLite queries for the same element.
    """
    if not 1 <= int(z) <= _MAX_ATOMIC_NUMBER:
        raise ValueError(f"atomic number {z!r} outside the periodic table [1, {_MAX_ATOMIC_NUMBER}]")
    mass = element(int(z)).mass
    if mass is None or not math.isfinite(float(mass)) or float(mass) <= 0.0:
        raise ValueError(f"mendeleev returned no valid mass for Z={z}: {mass!r}")
    return float(mass)


def resolve_atomic_masses_amu(atomic_numbers: Sequence[int]) -> np.ndarray:
    """Resolve per-atom masses in input order via :func:`elemental_mass_amu`."""
    return np.asarray([elemental_mass_amu(int(z)) for z in atomic_numbers], dtype=np.float64)


# --------------------------------------------------------------------------- #
# Analytical chunk geometry
# --------------------------------------------------------------------------- #
def _prod(values: Sequence[int]) -> int:
    result = 1
    for v in values:
        result *= int(v)
    return result


def derive_trajectory_chunk_shape(
    frame_shape: Tuple[int, ...],
    itemsize: int = 8,
    target_bytes: int = CHUNK_TARGET_BYTES,
    max_bytes: int = CHUNK_MAX_BYTES,
) -> Tuple[int, ...]:
    """Derive a chunk shape for a frame-extensible dataset.

    The dataset has shape ``(n_frames,) + frame_shape``. The chunk memory
    footprint aims for ``target_bytes`` and never exceeds ``max_bytes``:

    * If a whole frame fits in ``max_bytes``, whole frames are batched
      along axis 0: ``frames = max(1, target // frame_bytes)``.
    * Otherwise a chunk holds a single frame. The per-frame axes are then
      tiled from the outermost axis inwards until the tile fits.
    """
    if itemsize <= 0:
        raise ValueError("itemsize must be positive")
    frame_shape = tuple(int(d) for d in frame_shape)
    if any(d < 1 for d in frame_shape):
        raise ValueError(f"frame axes must be >= 1, got {frame_shape}")

    frame_bytes = _prod(frame_shape) * itemsize
    if frame_bytes <= max_bytes:
        frames = max(1, target_bytes // frame_bytes)
        return (int(frames),) + frame_shape

    tile = list(frame_shape)
    for axis in range(len(tile)):
        if _prod(tile[axis:]) * itemsize <= max_bytes:
            break
        inner_bytes = _prod(tile[axis + 1:]) * itemsize
        if inner_bytes <= target_bytes:
            tile[axis] = max(1, min(frame_shape[axis], target_bytes // inner_bytes))
            break
        tile[axis] = 1
    return (1,) + tuple(int(t) for t in tile)


# --------------------------------------------------------------------------- #
# Writer handle with post-SWMR layout lockout
# --------------------------------------------------------------------------- #
class _SWMRGuardedFile(h5py.File):
    """``h5py.File`` that refuses layout changes after SWMR activation.

    HDF5 does not reliably reject object creation in SWMR-write mode, and
    SWMR readers cannot observe such objects. The writer handle therefore
    enforces the ban itself.

    Dataset creation raises ``RuntimeError`` with the exact message
    ``'Dataset creation prohibited after SWMR mode activation.'``; other
    structural mutations (groups, links, deletion, relinking) raise
    ``RuntimeError`` naming the blocked operation.
    """

    _layout_frozen = False

    def _freeze_layout(self) -> None:
        self._layout_frozen = True

    def _assert_dataset_creation_allowed(self) -> None:
        if self._layout_frozen:
            raise RuntimeError(_DATASET_LOCKOUT_MESSAGE)

    def _assert_structure_mutable(self, operation: str) -> None:
        if self._layout_frozen:
            raise RuntimeError(f"{operation} prohibited after SWMR mode activation.")

    def create_dataset(self, name, *args, **kwargs):
        self._assert_dataset_creation_allowed()
        return super().create_dataset(name, *args, **kwargs)

    def create_dataset_like(self, name, other, **kwupdate):
        self._assert_dataset_creation_allowed()
        return super().create_dataset_like(name, other, **kwupdate)

    def create_virtual_dataset(self, name, layout, fillvalue=None):
        self._assert_dataset_creation_allowed()
        return super().create_virtual_dataset(name, layout, fillvalue=fillvalue)

    def require_dataset(self, name, shape, dtype, exact=False, **kwds):
        if name not in self:
            self._assert_dataset_creation_allowed()
        return super().require_dataset(name, shape, dtype, exact=exact, **kwds)

    def create_group(self, name, track_order=None):
        self._assert_structure_mutable("Group creation")
        return super().create_group(name, track_order=track_order)

    def require_group(self, name):
        if name not in self:
            self._assert_structure_mutable("Group creation")
        return super().require_group(name)

    def __setitem__(self, name, obj):
        self._assert_structure_mutable("Link/object creation")
        super().__setitem__(name, obj)

    def __delitem__(self, name):
        self._assert_structure_mutable("Object deletion")
        super().__delitem__(name)

    def move(self, source, dest):
        self._assert_structure_mutable("Object relinking")
        super().move(source, dest)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class CorruptChunkError(RuntimeError):
    """Raised when an on-disk chunk fails checksum or integrity verification."""

    def __init__(
        self,
        message: str,
        frame_index: Optional[int] = None,
        dataset_name: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.frame_index = frame_index
        self.dataset_name = dataset_name
        self.original_error = original_error


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class SWMRPESStore:
    """Production SWMR-enabled PES HDF5 trajectory container.

    ``mode="a"`` (default) opens the exclusive writer: it acquires a
    cross-process :class:`FileLock` (``<container>.lock``), opens or creates
    the container with ``libver="latest"``, pre-allocates the schema and, when
    ``enable_swmr`` is true, irreversibly activates SWMR writing. The lock is
    held until :meth:`close`.

    ``mode="r"`` opens a lock-free SWMR reader on an existing container.
    """

    def __init__(
        self,
        h5_path: Union[str, Path],
        atomic_numbers: Sequence[int] = inspect.Parameter.empty,
        n_atoms: Optional[int] = None,
        zstd_level: int = 3,
        shuffle: bool = True,
        enable_swmr: bool = True,
        lock_timeout_s: float = 30.0,
        enable_forces: bool = True,
        enable_hessians: bool = True,
        mode: str = "a",
    ) -> None:
        if mode not in ("a", "r+", "r"):
            raise ValueError(f"mode must be 'a', 'r+' (exclusive writer) or 'r' (SWMR reader), got {mode!r}")
        self._path = Path(h5_path)
        self._mode = mode
        self._read_only = mode == "r"
        self._lock_path = Path(str(self._path) + ".lock")
        self._lock: Optional[FileLock] = None
        self._file: Optional[h5py.File] = None
        self._swmr_active = False
        self._frame_count = 0
        self._enable_forces = bool(enable_forces)
        self._enable_hessians = bool(enable_hessians)
        self._zstd_level = int(zstd_level)
        self._shuffle = bool(shuffle)
        self._enable_swmr = bool(enable_swmr)
        self._lock_timeout_s = float(lock_timeout_s)

        if self._read_only:
            self._atomic_numbers = np.empty(0, dtype=_INT_DTYPE)
            self._masses_amu = np.empty(0, dtype=np.float64)
            self._n_atoms = 0
            self._open_reader(n_atoms, atomic_numbers)
            return

        if mode == "r+" and not self._path.is_file():
            raise FileNotFoundError(f"cannot open non-existent file {self._path} in 'r+' mode")

        existing = self._path.is_file() and self._path.stat().st_size > 0
        if atomic_numbers is not None and atomic_numbers is not inspect.Parameter.empty:
            self._atomic_numbers = self._validate_atomic_numbers(atomic_numbers)
            derived_n = int(self._atomic_numbers.shape[0])
            if n_atoms is not None and int(n_atoms) != derived_n:
                raise ValueError(f"n_atoms={n_atoms} disagrees with len(atomic_numbers)={derived_n}")
            self._n_atoms = derived_n
            self._masses_amu = resolve_atomic_masses_amu(self._atomic_numbers.tolist())
        elif existing:
            if n_atoms is not None:
                if int(n_atoms) < 1:
                    raise ValueError(f"n_atoms must be a positive integer, got {n_atoms!r}")
                self._n_atoms = int(n_atoms)
            else:
                self._n_atoms = 0
            self._atomic_numbers = np.empty(0, dtype=_INT_DTYPE)
            self._masses_amu = np.empty(0, dtype=np.float64)
        else:
            raise ValueError("a new container requires atomic_numbers to initialise topology")

        if not 1 <= self._zstd_level <= 22:
            raise ValueError(f"Zstd compression level must be in [1, 22], got {zstd_level}")
        if lock_timeout_s < 0:
            raise ValueError("lock_timeout_s must be non-negative")

        self._open_session()

    # ------------------------------------------------------------------ #
    # Public properties
    # ------------------------------------------------------------------ #
    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def n_atoms(self) -> int:
        return self._n_atoms

    @property
    def atomic_numbers(self) -> Union[np.ndarray, List[int]]:
        """Writer: int32 array copy. Reader: list of Python ints."""
        if self._read_only:
            return [int(z) for z in self._atomic_numbers]
        return self._atomic_numbers.copy()

    @property
    def atomic_masses_amu(self) -> np.ndarray:
        return self._masses_amu.copy()

    @property
    def swmr_mode(self) -> bool:
        """Return True if the open handle operates in SWMR mode."""
        if self._file is None or not self._file.id.valid:
            return False
        if self._read_only:
            return bool(self._file.swmr_mode)
        return bool(self._swmr_active)

    @property
    def is_open(self) -> bool:
        return self._file is not None and self._file.id.valid

    @property
    def frame_count(self) -> int:
        """Number of frames currently visible in the container."""
        if self.is_open:
            self._frame_count = self._visible_frame_count(self._file)
        return self._frame_count

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def open_writer(self) -> h5py.File:
        """Return the active writer handle, re-opening the session if closed."""
        if self._read_only:
            raise RuntimeError("a read-only store has no writer handle")
        if not self.is_open:
            self._open_session()
        return self._file

    def close(self) -> None:
        """Flush datasets, close the handle and release the file lock."""
        handle = self._file
        self._file = None
        try:
            if handle is not None and handle.id.valid:
                try:
                    self._frame_count = self._visible_frame_count(handle)
                    if not self._read_only:
                        self._two_tier_flush(handle)
                finally:
                    handle.close()
        finally:
            self._swmr_active = False
            lock = self._lock
            self._lock = None
            if lock is not None and lock.is_locked:
                lock.release(force=True)
            if self._lock_path.exists():
                try:
                    self._lock_path.unlink()
                except OSError as exc:
                    _ = exc

    def __enter__(self) -> "SWMRPESStore":
        if not self._read_only:
            self.open_writer()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"SWMRPESStore(path={str(self._path)!r}, mode={self.mode!r}, n_atoms={self._n_atoms}, "
            f"swmr_mode={self.swmr_mode}, open={self.is_open})"
        )

    # ------------------------------------------------------------------ #
    # Reader API
    # ------------------------------------------------------------------ #
    def refresh(self) -> int:
        """Re-read dataset metadata from disk and return the visible frame count."""
        handle = self._require_open("refresh")
        if self._read_only and handle.swmr_mode:
            for name in _TRAJECTORY_DATASETS:
                if name in handle:
                    handle[name].refresh()
        return self.frame_count

    def read_range(self, start: int, stop: int) -> Dict[str, np.ndarray]:
        """Read frames ``[start, stop)`` of coordinates, energies (and forces if present)."""
        handle = self._require_open("read_range")
        available = self._visible_frame_count(handle)
        start = int(start)
        stop = min(int(stop), available)
        if start < 0 or start > stop:
            raise ValueError(f"invalid frame range [{start}, {stop}) for {available} frames")
        result: Dict[str, np.ndarray] = {
            "coordinates": np.asarray(handle["coordinates"][start:stop]),
            "energies": np.asarray(handle["energies"][start:stop]).reshape(-1),
        }
        if "forces" in handle:
            result["forces"] = np.asarray(handle["forces"][start:stop])
        return result

    # ------------------------------------------------------------------ #
    # Writer API
    # ------------------------------------------------------------------ #
    def append_frame(
        self,
        positions: Any,
        energy: Any,
        forces: Any = None,
        hessian: Any = None,
        energy_unit: str = _STORAGE_ENERGY_UNIT,
        force_unit: str = _STORAGE_FORCE_UNIT,
    ) -> int:
        """Append one frame. Returns the new frame count."""
        pos = np.asarray(positions, dtype=np.float64)
        if pos.ndim != 2:
            raise ValueError(f"positions must have shape (n_atoms, 3), got {pos.shape}")
        return self.append_batch(
            pos[np.newaxis],
            energies=np.asarray([float(energy)], dtype=np.float64),
            forces=None if forces is None else np.asarray(forces, dtype=np.float64)[np.newaxis],
            hessians=None if hessian is None else np.asarray(hessian, dtype=np.float64)[np.newaxis],
            energy_unit=energy_unit,
            force_unit=force_unit,
        )

    def append_batch(
        self,
        positions: Any,
        energies: Any,
        forces: Any = None,
        hessians: Any = None,
        energy_unit: str = _STORAGE_ENERGY_UNIT,
        force_unit: str = _STORAGE_FORCE_UNIT,
    ) -> int:
        """Append ``T`` frames at once. Returns the new frame count.

        Positions are stored in Angstrom, energies converted to Hartree and
        forces converted to Hartree/Bohr. All inputs are validated before the
        first dataset is extended.
        """
        handle = self._require_open("append")
        if self._read_only:
            raise RuntimeError("append prohibited on a read-only store")
        n = self._n_atoms

        coords = self._as_block("positions", positions, (n, 3))
        count = int(coords.shape[0])
        if count == 0:
            return self.frame_count

        energy_arr = np.asarray(energies, dtype=np.float64).reshape(-1)
        if energy_arr.shape[0] != count:
            raise ValueError(f"expected {count} energies, got {energy_arr.shape[0]}")
        if not np.isfinite(energy_arr).all():
            raise ValueError("energies contain non-finite values")
        energy_storage = np.asarray(convert_energy(energy_arr, energy_unit, _STORAGE_ENERGY_UNIT), dtype=np.float64)

        blocks: List[Tuple[str, np.ndarray]] = []
        if self._enable_forces:
            if forces is None:
                raise ValueError("forces are required because the store was opened with enable_forces=True")
            force_block = self._as_block("forces", forces, (n, 3))
            if force_block.shape[0] != count:
                raise ValueError(f"expected {count} force frames, got {force_block.shape[0]}")
            blocks.append(
                ("forces", np.asarray(convert_force(force_block, force_unit, _STORAGE_FORCE_UNIT), dtype=np.float64))
            )
        elif forces is not None:
            raise ValueError("forces supplied but the store was opened with enable_forces=False")

        if self._enable_hessians:
            if hessians is None:
                raise ValueError("hessians are required because the store was opened with enable_hessians=True")
            hessian_block = self._as_block("hessians", hessians, (3 * n, 3 * n))
            if hessian_block.shape[0] != count:
                raise ValueError(f"expected {count} hessian frames, got {hessian_block.shape[0]}")
            blocks.append(("hessians", hessian_block))
        elif hessians is not None:
            raise ValueError("hessians supplied but the store was opened with enable_hessians=False")

        # Coordinates then energies last: a frame becomes visible only once
        # min(len(coordinates), len(energies)) covers it.
        blocks.append(("coordinates", coords))
        blocks.append(("energies", energy_storage))

        for name, block in blocks:
            ds = handle[name]
            old = int(ds.shape[0])
            ds.resize(old + count, axis=0)
            ds[old:old + count] = block
            ds.flush()
            del ds
        handle.flush()
        return self.frame_count

    # ------------------------------------------------------------------ #
    # Two-tier flush and SWMR activation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _two_tier_flush(handle: h5py.File) -> None:
        """Tier 1: flush every dataset individually. Tier 2: flush the container."""
        for name in _TRAJECTORY_DATASETS + (_TOPOLOGY_DATASET,):
            if name in handle:
                ds = handle[name]
                ds.flush()
                del ds
        handle.flush()

    def _activate_swmr(self, handle: h5py.File) -> None:
        """Two-tier flush, then irreversibly switch the writer handle to SWMR mode."""
        if self._read_only:
            raise RuntimeError("SWMR write mode cannot be activated on a read-only store")
        self._two_tier_flush(handle)
        if not handle.swmr_mode:
            handle.swmr_mode = True
        if handle.swmr_mode is not True:
            raise RuntimeError("failed to activate SWMR write mode on the PES container")
        freeze = getattr(handle, "_freeze_layout", None)
        if freeze is not None:
            freeze()
        self._swmr_active = True

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_atomic_numbers(atomic_numbers: Sequence[int]) -> np.ndarray:
        arr = np.asarray(atomic_numbers)
        if arr.ndim != 1 or arr.shape[0] < 1:
            raise ValueError("atomic_numbers must be a non-empty one-dimensional sequence")
        if not np.issubdtype(arr.dtype, np.integer):
            as_float = arr.astype(np.float64)
            if not np.all(as_float == np.floor(as_float)):
                raise ValueError("atomic_numbers must be integers")
        ints = arr.astype(np.int64)
        if ints.min() < 1 or ints.max() > _MAX_ATOMIC_NUMBER:
            raise ValueError(f"atomic numbers must lie in [1, {_MAX_ATOMIC_NUMBER}]")
        return ints.astype(_INT_DTYPE)

    @staticmethod
    def _as_block(label: str, value: Any, frame_shape: Tuple[int, ...]) -> np.ndarray:
        arr = np.asarray(value, dtype=np.float64)
        if arr.size == 0 and arr.ndim >= 1 and arr.shape[0] == 0:
            return arr.reshape((0,) + tuple(frame_shape))
        if arr.ndim != len(frame_shape) + 1 or tuple(arr.shape[1:]) != tuple(frame_shape):
            raise ValueError(
                f"{label} must have shape (T,) + {tuple(frame_shape)}, got {arr.shape}"
            )
        if not np.isfinite(arr).all():
            raise ValueError(f"{label} contain non-finite values")
        return arr

    @staticmethod
    def _visible_frame_count(handle: h5py.File) -> int:
        if "coordinates" not in handle or "energies" not in handle:
            return 0
        return int(min(handle["coordinates"].shape[0], handle["energies"].shape[0]))

    def _require_open(self, operation: str) -> h5py.File:
        if not self.is_open:
            raise RuntimeError(f"{operation} requires an open store")
        return self._file

    def _compression_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = dict(hdf5plugin.Zstd(clevel=self._zstd_level))
        kwargs["shuffle"] = self._shuffle
        kwargs["fletcher32"] = True
        return kwargs

    def _create_extensible(
        self,
        handle: h5py.File,
        name: str,
        frame_shape: Tuple[int, ...],
        chunks: Optional[Tuple[int, ...]] = None,
    ) -> None:
        if chunks is None:
            chunks = derive_trajectory_chunk_shape(frame_shape, itemsize=_FLOAT_DTYPE.itemsize)
        handle.create_dataset(
            name,
            shape=(0,) + tuple(frame_shape),
            maxshape=(None,) + tuple(frame_shape),
            dtype=_FLOAT_DTYPE,
            chunks=chunks,
            **self._compression_kwargs(),
        )

    def _initialise_schema(self, handle: h5py.File) -> None:
        n = self._n_atoms
        handle.attrs["schema_version"] = SCHEMA_VERSION
        handle.attrs["created_by"] = CREATED_BY
        handle.attrs["n_atoms"] = n
        handle.attrs["energy_unit"] = _STORAGE_ENERGY_UNIT
        handle.attrs["force_unit"] = _STORAGE_FORCE_UNIT
        handle.attrs["length_unit"] = _STORAGE_LENGTH_UNIT
        if self._masses_amu.shape[0] > 0:
            handle.attrs["atomic_masses_amu"] = self._masses_amu

        if _TOPOLOGY_DATASET not in handle:
            handle.create_dataset(
                _TOPOLOGY_DATASET,
                data=self._atomic_numbers,
                dtype=_INT_DTYPE,
            )

        cfg = HDF5ZstdConfig(clevel=self._zstd_level, enable_shuffle=self._shuffle, enable_fletcher32=True)
        coords_chunks = tuple(cfg.resolve_chunk_shape_3d(n_atoms=n, spatial_dim=3, itemsize=_FLOAT_DTYPE.itemsize))
        forces_chunks = tuple(cfg.resolve_chunk_shape_3d(n_atoms=n, spatial_dim=3, itemsize=_FLOAT_DTYPE.itemsize))
        hessians_chunks = tuple(cfg.resolve_chunk_shape_hessian(n_atoms=n, itemsize=_FLOAT_DTYPE.itemsize))
        energies_chunks = tuple(cfg.resolve_chunk_shape_1d(itemsize=_FLOAT_DTYPE.itemsize))

        self._create_extensible(handle, "coordinates", (n, 3), coords_chunks)
        if self._enable_forces:
            self._create_extensible(handle, "forces", (n, 3), forces_chunks)
        if self._enable_hessians:
            self._create_extensible(handle, "hessians", (3 * n, 3 * n), hessians_chunks)
        self._create_extensible(handle, "energies", (), energies_chunks)

    def _adopt_existing_schema(self, handle: h5py.File) -> None:
        if "coordinates" not in handle or "energies" not in handle:
            raise ValueError(f"{self._path} is not a CoChem PES container (missing datasets)")
        if "schema_version" in handle.attrs and str(handle.attrs["schema_version"]) != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {handle.attrs['schema_version']!r}, expected {SCHEMA_VERSION!r}")
        existing_n = int(handle["coordinates"].shape[1])
        if self._n_atoms == 0:
            self._n_atoms = existing_n
        elif existing_n != self._n_atoms:
            raise ValueError(
                f"existing container holds {existing_n} atoms (n_atoms={existing_n}) but store configured with n_atoms={self._n_atoms}"
            )
        if _TOPOLOGY_DATASET in handle:
            stored = np.asarray(handle[_TOPOLOGY_DATASET][...], dtype=_INT_DTYPE)
            if self._atomic_numbers.shape[0] > 0:
                if self._atomic_numbers.shape[0] != existing_n or not np.array_equal(stored, self._atomic_numbers):
                    raise ValueError(
                        f"atomic_numbers {self._atomic_numbers.tolist()} disagree with container topology (n_atoms={existing_n})"
                    )
            elif np.all(stored >= 1):
                self._atomic_numbers = stored
                self._masses_amu = resolve_atomic_masses_amu(stored.tolist())
        if "forces" not in handle:
            self._enable_forces = False
        if "hessians" not in handle:
            self._enable_hessians = False

    def _open_session(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self._lock_path), timeout=self._lock_timeout_s)
        try:
            lock.acquire()
        except Timeout as exc:
            raise TimeoutError(
                f"could not acquire the writer lock {self._lock_path} within {self._lock_timeout_s} s"
            ) from exc
        self._lock = lock
        handle: Optional[h5py.File] = None
        try:
            existed = self._path.exists() and self._path.stat().st_size > 0
            file_mode = "r+" if self._mode == "r+" else "a"
            handle = _SWMRGuardedFile(str(self._path), file_mode, libver="latest", driver="stdio")
            if existed and "coordinates" in handle:
                self._adopt_existing_schema(handle)
            else:
                self._initialise_schema(handle)
                self._two_tier_flush(handle)
            self._file = handle
            self._swmr_active = False
            if self._enable_swmr:
                self._activate_swmr(handle)
            self._frame_count = self._visible_frame_count(handle)
        except BaseException:
            self._file = None
            if handle is not None and handle.id.valid:
                handle.close()
            self._lock = None
            if lock.is_locked:
                lock.release(force=True)
            if self._lock_path.exists():
                try:
                    self._lock_path.unlink()
                except OSError as exc:
                    _ = exc
            raise

    def _open_reader(self, n_atoms: Optional[int], atomic_numbers: Optional[Sequence[int]]) -> None:
        if not self._path.is_file():
            raise FileNotFoundError(f"PES container not found: {self._path}")
        handle = h5py.File(str(self._path), "r", libver="latest", swmr=True)
        try:
            if "coordinates" not in handle or "energies" not in handle:
                raise ValueError(f"{self._path} is not a CoChem PES container (missing datasets)")
            file_n = int(handle["coordinates"].shape[1])
            if n_atoms is not None and int(n_atoms) != file_n:
                raise ValueError(f"n_atoms={n_atoms} disagrees with the container ({file_n})")
            self._n_atoms = file_n
            if _TOPOLOGY_DATASET in handle:
                stored = np.asarray(handle[_TOPOLOGY_DATASET][...], dtype=_INT_DTYPE)
                self._atomic_numbers = stored
                if stored.shape[0] > 0 and np.all(stored >= 1):
                    self._masses_amu = resolve_atomic_masses_amu(stored.tolist())
            if atomic_numbers is not None and atomic_numbers is not inspect.Parameter.empty:
                expected = self._validate_atomic_numbers(atomic_numbers)
                if not np.array_equal(expected, self._atomic_numbers):
                    raise ValueError("atomic_numbers disagree with the container")
        except BaseException:
            handle.close()
            raise
        self._file = handle
        self._frame_count = self._visible_frame_count(handle)
