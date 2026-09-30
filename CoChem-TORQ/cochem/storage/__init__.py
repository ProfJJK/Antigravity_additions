"""CoChem storage layer: SWMR HDF5 trajectory containers with Zstd/Shuffle/Fletcher32 filters.

The public API is re-exported from three authoritative modules:

* :mod:`cochem.storage.hdf5_lock_bypass`: HDF5 driver-level file-locking
  bypass protocol, network/cloud mount classification, the safe container
  opener and locking telemetry (Task 20.10811). This is the canonical source
  of ``enforce_hdf5_file_locking_bypass``.
* :mod:`cochem.storage.hdf5_zstd`: the tripartite Zstd/Shuffle/Fletcher32
  filter pipeline, frozen configuration, chunk geometry helpers and DCPL
  introspection.
* :mod:`cochem.storage.pes_store`: the production SWMR PES trajectory store,
  unit conversion and dynamic Mendeleev mass resolution.

Chunk geometry derivation lives in ``pes_store.derive_trajectory_chunk_shape``
and ``hdf5_zstd.HDF5ZstdConfig``. There is no separate geometry module.

The optional legacy helper modules (``hdf5_filters``, ``pes_metadata``) are not
part of the mirrored package. Their historic names resolve lazily, and only
when the helper module is actually present next to this file.
"""
from __future__ import annotations

import os
import sys

# Record whether h5py was already loaded before the bypass was first asserted.
# The root package usually records this marker first; only fill it in if it is
# missing.
if not hasattr(sys, "_cochem_h5py_preloaded"):
    sys._cochem_h5py_preloaded = "h5py" in sys.modules  # type: ignore[attr-defined]

# The driver-level HDF5 lock bypass must be set before h5py is first imported.
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

import importlib  # noqa: E402
from typing import Any, Dict  # noqa: E402

# The hardened bypass module is imported first, so the canonical implementation
# is the one bound to the package namespace (not the legacy hdf5_zstd helper).
from cochem.storage.hdf5_lock_bypass import (  # noqa: E402
    HDF5_FILE_LOCKING_BYPASS_VALUE,
    HDF5_FILE_LOCKING_ENV_KEY,
    enforce_hdf5_file_locking_bypass,
    get_hdf5_lock_environment,
    get_hdf5_locking_status,
    get_hdf5_subprocess_env,
    h5py_supports_locking_kwarg,
    inspect_mount_locking_support,
    is_hdf5_file_locking_bypassed,
    is_network_or_cloud_path,
    safe_h5py_file,
    safe_hdf5_open,
    verify_subprocess_inheritance,
)
from cochem.storage.hdf5_zstd import (  # noqa: E402
    FILTER_FLETCHER32,
    FILTER_SHUFFLE,
    FILTER_ZSTD,
    MAX_CHUNK_BYTES,
    MIN_CHUNK_BYTES,
    ZSTD_MAX_CLEVEL,
    ZSTD_MIN_CLEVEL,
    HDF5ZstdConfig,
    create_compressed_dataset,
    verify_dataset_filters,
    verify_lossless_roundtrip,
)
from cochem.storage.pes_store import (  # noqa: E402
    CHUNK_MAX_BYTES,
    CHUNK_MIN_BYTES,
    CHUNK_TARGET_BYTES,
    CREATED_BY,
    SCHEMA_VERSION,
    ZSTD_FILTER_ID,
    CorruptChunkError,
    SWMRPESStore,
    convert_energy,
    convert_ev_to_hartree,
    convert_force,
    convert_hartree_to_ev,
    derive_trajectory_chunk_shape,
    elemental_mass_amu,
    get_bohr_in_angstrom,
    get_hartree_in_ev,
    resolve_atomic_masses_amu,
)

__all__ = [
    # hdf5_lock_bypass (Task 20.10811)
    "HDF5_FILE_LOCKING_ENV_KEY",
    "HDF5_FILE_LOCKING_BYPASS_VALUE",
    "enforce_hdf5_file_locking_bypass",
    "get_hdf5_subprocess_env",
    "get_hdf5_lock_environment",
    "verify_subprocess_inheritance",
    "is_network_or_cloud_path",
    "inspect_mount_locking_support",
    "h5py_supports_locking_kwarg",
    "safe_hdf5_open",
    "safe_h5py_file",
    "get_hdf5_locking_status",
    "is_hdf5_file_locking_bypassed",
    # hdf5_zstd
    "FILTER_FLETCHER32",
    "FILTER_SHUFFLE",
    "FILTER_ZSTD",
    "MAX_CHUNK_BYTES",
    "MIN_CHUNK_BYTES",
    "ZSTD_MAX_CLEVEL",
    "ZSTD_MIN_CLEVEL",
    "HDF5ZstdConfig",
    "create_compressed_dataset",
    "verify_dataset_filters",
    "verify_lossless_roundtrip",
    # pes_store
    "CHUNK_MAX_BYTES",
    "CHUNK_MIN_BYTES",
    "CHUNK_TARGET_BYTES",
    "CREATED_BY",
    "SCHEMA_VERSION",
    "ZSTD_FILTER_ID",
    "CorruptChunkError",
    "SWMRPESStore",
    "convert_energy",
    "convert_ev_to_hartree",
    "convert_force",
    "convert_hartree_to_ev",
    "derive_trajectory_chunk_shape",
    "elemental_mass_amu",
    "get_bohr_in_angstrom",
    "get_hartree_in_ev",
    "resolve_atomic_masses_amu",
    "resolve_element",
    "resolve_species_metadata",
]

# Legacy names served lazily from optional helper modules (never imported eagerly).
_LAZY_EXPORTS: Dict[str, str] = {
    "DEFAULT_ZSTD_CLEVEL": "hdf5_filters",
    "FILTER_FLETCHER32_ID": "hdf5_filters",
    "FILTER_SHUFFLE_ID": "hdf5_filters",
    "FILTER_ZSTD_ID": "hdf5_filters",
    "MAX_ZSTD_CLEVEL": "hdf5_filters",
    "MIN_ZSTD_CLEVEL": "hdf5_filters",
    "build_filter_pipeline": "hdf5_filters",
    "inspect_dataset_filter_pipeline": "hdf5_filters",
    "is_zstd_filter_available": "hdf5_filters",
    "resolve_element": "pes_metadata",
    "resolve_species_metadata": "pes_metadata",
}


def __getattr__(name: str) -> Any:
    module_leaf = _LAZY_EXPORTS.get(name)
    if module_leaf is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    try:
        module = importlib.import_module("%s.%s" % (__name__, module_leaf))
    except ModuleNotFoundError as exc:
        raise AttributeError(
            "module %r has no attribute %r (optional helper module %r is not installed)"
            % (__name__, name, module_leaf)
        ) from exc
    return getattr(module, name)
