"""Tripartite HDF5 filter pipeline assembly and DCPL introspection.

Pipeline (write direction)::

    raw float64 chunk -> Shuffle (ID 2) -> Zstandard (ID 32015) -> Fletcher32 (ID 3) -> disk

* Shuffle groups the sign/exponent and mantissa bytes of IEEE 754 float64
  values, lowering entropy before entropy coding.
* Zstandard (via ``hdf5plugin.Zstd``) performs lossless compression with a
  configurable level in ``[1, 22]``.
* Fletcher32 appends a per-chunk checksum for bit-rot detection.

HDF5 filters only operate on chunked layouts: callers must pass ``chunks=...``
to ``h5py.Group.create_dataset`` alongside the kwargs synthesised here.
"""

from __future__ import annotations

import numbers
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import h5py
import hdf5plugin  # registers the Zstandard filter (ID 32015) with libhdf5

__all__ = [
    "FILTER_SHUFFLE_ID",
    "FILTER_FLETCHER32_ID",
    "FILTER_ZSTD_ID",
    "DEFAULT_ZSTD_CLEVEL",
    "MIN_ZSTD_CLEVEL",
    "MAX_ZSTD_CLEVEL",
    "HDF5ZstdConfig",
    "build_filter_pipeline",
    "inspect_dataset_filter_pipeline",
    "is_zstd_filter_available",
]

FILTER_SHUFFLE_ID: int = 2  # H5Z_FILTER_SHUFFLE
FILTER_FLETCHER32_ID: int = 3  # H5Z_FILTER_FLETCHER32
FILTER_ZSTD_ID: int = 32015  # H5Z_FILTER_ZSTD (registered by hdf5plugin)
DEFAULT_ZSTD_CLEVEL: int = 3
MIN_ZSTD_CLEVEL: int = 1
MAX_ZSTD_CLEVEL: int = 22


def is_zstd_filter_available() -> bool:
    """Return True if libhdf5 reports the Zstandard filter as available."""
    return bool(h5py.h5z.filter_avail(FILTER_ZSTD_ID))


@dataclass(frozen=True)
class HDF5ZstdConfig:
    """Immutable configuration of the Shuffle/Zstd/Fletcher32 pipeline."""

    clevel: int = DEFAULT_ZSTD_CLEVEL
    shuffle: bool = True
    fletcher32: bool = True

    def __post_init__(self) -> None:
        level = self.clevel
        # bool is a subclass of int: reject it explicitly. Floats (even 3.0),
        # strings and None are rejected as non-integers.
        if isinstance(level, bool) or not isinstance(level, numbers.Integral):
            raise ValueError(
                f"clevel must be an integer, got {type(level).__name__}: {level!r}"
            )
        level_int = int(level)
        if not (MIN_ZSTD_CLEVEL <= level_int <= MAX_ZSTD_CLEVEL):
            raise ValueError(
                f"clevel must be in [{MIN_ZSTD_CLEVEL}, {MAX_ZSTD_CLEVEL}], "
                f"got {level_int}"
            )
        # Normalise integral types (e.g. numpy.int64) to a plain int.
        object.__setattr__(self, "clevel", level_int)

        if not isinstance(self.shuffle, bool):
            raise ValueError(
                f"shuffle must be a bool, got {type(self.shuffle).__name__}"
            )
        if not isinstance(self.fletcher32, bool):
            raise ValueError(
                f"fletcher32 must be a bool, got {type(self.fletcher32).__name__}"
            )

    def to_dataset_kwargs(self) -> Dict[str, Any]:
        """Synthesize ``h5py.Group.create_dataset`` filter keyword arguments.

        Returns a dict with ``compression`` (32015) and ``compression_opts``
        (``(clevel,)``) from ``hdf5plugin.Zstd``, plus ``shuffle=True`` and/or
        ``fletcher32=True`` when enabled.
        """
        zstd = hdf5plugin.Zstd(clevel=self.clevel)
        kwargs: Dict[str, Any] = {
            "compression": int(zstd["compression"]),
            "compression_opts": tuple(int(v) for v in zstd["compression_opts"]),
        }
        if self.shuffle:
            kwargs["shuffle"] = True
        if self.fletcher32:
            kwargs["fletcher32"] = True
        return kwargs


def build_filter_pipeline(config: Optional[HDF5ZstdConfig] = None) -> Dict[str, Any]:
    """Build dataset creation kwargs for the tripartite filter pipeline.

    ``None`` selects the default :class:`HDF5ZstdConfig`.
    """
    if config is None:
        config = HDF5ZstdConfig()
    if not isinstance(config, HDF5ZstdConfig):
        raise TypeError(
            f"config must be an HDF5ZstdConfig or None, got {type(config).__name__}"
        )
    return config.to_dataset_kwargs()


def _resolve_dcpl(dataset: Any) -> Any:
    """Obtain the native dataset creation property list from a dataset handle."""
    if isinstance(dataset, h5py.h5d.DatasetID):
        return dataset.get_create_plist()
    if isinstance(dataset, h5py.Dataset):
        return dataset.id.get_create_plist()
    if hasattr(dataset, "id") and isinstance(dataset.id, h5py.h5d.DatasetID):
        return dataset.id.get_create_plist()
    getter = getattr(dataset, "get_create_plist", None)
    if getter is not None and callable(getter):
        return getter()
    raise TypeError(
        "inspect_dataset_filter_pipeline expects an h5py.Dataset or "
        f"h5py.h5d.DatasetID, got {type(dataset).__name__}"
    )


def inspect_dataset_filter_pipeline(dataset: Any) -> Dict[str, Any]:
    """Introspect the HDF5 filter pipeline registered on a dataset's DCPL.

    Returns a dict with keys ``nfilters``, ``filter_ids`` (registration order),
    ``has_shuffle``, ``has_fletcher32``, ``has_zstd`` and ``clevel`` (the Zstd
    level taken from ``cd_values[0]`` of filter 32015, or ``None``).
    """
    dcpl = _resolve_dcpl(dataset)
    nfilters = int(dcpl.get_nfilters())

    filter_ids: List[int] = []
    clevel: Optional[int] = None
    for index in range(nfilters):
        filter_id, _flags, cd_values, _name = dcpl.get_filter(index)
        filter_id = int(filter_id)
        filter_ids.append(filter_id)
        if filter_id == FILTER_ZSTD_ID and clevel is None and len(cd_values) > 0:
            clevel = int(cd_values[0])

    return {
        "nfilters": nfilters,
        "filter_ids": filter_ids,
        "has_shuffle": FILTER_SHUFFLE_ID in filter_ids,
        "has_fletcher32": FILTER_FLETCHER32_ID in filter_ids,
        "has_zstd": FILTER_ZSTD_ID in filter_ids,
        "clevel": clevel,
    }
