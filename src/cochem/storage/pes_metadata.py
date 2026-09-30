"""Dynamic Mendeleev mass resolver and species metadata ingestion.

Every atomic number and atomic mass is resolved at runtime from the
``mendeleev`` element database. This module contains no mass tables, no
symbol-to-number dictionaries and no CODATA or IUPAC constants.

Public API
----------
resolve_element(identifier) -> (atomic_number, mass_amu)
    Per-element resolver, cached with ``functools.lru_cache(maxsize=1024)``.
resolve_species_metadata(symbols_or_atomic_numbers) -> (Z int32 array, mass float64 array)
    Sequence-level ingestion that preserves input order.
"""
from __future__ import annotations

import functools
import math
from typing import Sequence, Tuple, Union

import numpy as np
from mendeleev import element as _mendeleev_element

__all__ = ["resolve_element", "resolve_species_metadata"]

_MIN_ATOMIC_NUMBER = 1
_MAX_ATOMIC_NUMBER = 118
_MAX_SYMBOL_LENGTH = 3

Identifier = Union[str, int, np.integer]


def _normalize_identifier(identifier: object) -> Union[str, int]:
    """Convert one raw identifier into a canonical, hashable lookup key.

    Integers (including NumPy integer scalars) become a plain ``int`` checked
    against the periodic-table range. Strings are stripped and title-cased
    (``' he '`` -> ``'He'``). Everything else raises ``ValueError``.
    """
    # bool subclasses int: True would silently resolve to hydrogen.
    if isinstance(identifier, (bool, np.bool_)):
        raise ValueError(
            f"boolean value {identifier!r} is not a valid element identifier"
        )
    if isinstance(identifier, (int, np.integer)):
        z = int(identifier)
        if not _MIN_ATOMIC_NUMBER <= z <= _MAX_ATOMIC_NUMBER:
            raise ValueError(
                f"atomic number {z} outside the periodic table "
                f"[{_MIN_ATOMIC_NUMBER}, {_MAX_ATOMIC_NUMBER}]"
            )
        return z
    if isinstance(identifier, str):
        text = identifier.strip()
        if not text:
            raise ValueError("empty string is not a valid chemical symbol")
        if len(text) > _MAX_SYMBOL_LENGTH or not text.isascii() or not text.isalpha():
            raise ValueError(
                f"invalid chemical symbol {identifier!r}: expected 1-{_MAX_SYMBOL_LENGTH} "
                "alphabetic characters (formulas such as 'H2O' are not element symbols)"
            )
        return text[:1].upper() + text[1:].lower()
    raise ValueError(
        f"unsupported element identifier {identifier!r} of type "
        f"{type(identifier).__name__}; expected a chemical symbol (str) or an "
        "atomic number (int)"
    )


@functools.lru_cache(maxsize=1024)
def resolve_element(identifier: Union[str, int]) -> Tuple[int, float]:
    """Dynamically query mendeleev for an element's atomic number and mass.

    Parameters
    ----------
    identifier : str or int
        Chemical symbol (e.g. ``'O'``) or atomic number (e.g. ``8``).

    Returns
    -------
    tuple of (int, float)
        ``(atomic_number, mass_amu)`` with the standard atomic mass reported
        by the live mendeleev database.

    Raises
    ------
    ValueError
        If the identifier is of an unsupported type, an unknown symbol, an
        atomic number outside [1, 118], or mendeleev reports no valid mass.
    """
    key = _normalize_identifier(identifier)
    try:
        record = _mendeleev_element(key)
    except Exception as exc:  # mendeleev raises ValueError / ORM errors for unknown keys
        raise ValueError(f"element {identifier!r} not found in mendeleev database: {exc}") from exc

    atomic_number = record.atomic_number
    if atomic_number is None:
        raise ValueError(f"mendeleev returned no atomic number for element {identifier!r}")
    z = int(atomic_number)
    if not _MIN_ATOMIC_NUMBER <= z <= _MAX_ATOMIC_NUMBER:
        raise ValueError(
            f"mendeleev resolved {identifier!r} to atomic number {z}, outside "
            f"[{_MIN_ATOMIC_NUMBER}, {_MAX_ATOMIC_NUMBER}]"
        )
    if isinstance(key, str) and str(record.symbol) != key:
        raise ValueError(
            f"chemical symbol {identifier!r} does not match mendeleev symbol {record.symbol!r}"
        )

    mass = record.mass
    if mass is None:
        raise ValueError(f"mendeleev returned no mass for element {identifier!r} (Z={z})")
    mass_amu = float(mass)
    if not math.isfinite(mass_amu) or mass_amu <= 0.0:
        raise ValueError(
            f"mendeleev returned an invalid mass {mass!r} for element {identifier!r} (Z={z})"
        )
    return z, mass_amu


def resolve_species_metadata(
    symbols_or_atomic_numbers: Sequence[Identifier],
) -> Tuple[np.ndarray, np.ndarray]:
    """Resolve chemical symbols and/or atomic numbers into Z and masses.

    Parameters
    ----------
    symbols_or_atomic_numbers : Sequence[Union[str, int, np.integer]]
        Non-empty sequence of element symbols (``['H', 'O', 'H']``), atomic
        numbers (``[1, 8, 1]``) or a mixture (``['H', 8, 'H']``). Lists,
        tuples and one-dimensional NumPy arrays are accepted.

    Returns
    -------
    tuple of np.ndarray
        ``(atomic_numbers, atomic_masses_amu)`` where ``atomic_numbers`` is a
        1-D ``int32`` array and ``atomic_masses_amu`` a 1-D ``float64`` array,
        both in input order.

    Raises
    ------
    ValueError
        On a bare string/bytes input, a non-sequence, an empty sequence, an
        unknown symbol, an atomic number outside [1, 118], or an invalid
        element type (bool, float, None, nested sequences).
    """
    if isinstance(symbols_or_atomic_numbers, (str, bytes, bytearray)):
        raise ValueError(
            "input must be a sequence of element identifiers, not a bare string "
            f"({symbols_or_atomic_numbers!r}); wrap it in a list, e.g. ['H']"
        )
    if isinstance(symbols_or_atomic_numbers, np.ndarray):
        if symbols_or_atomic_numbers.ndim != 1:
            raise ValueError(
                "species array must be one-dimensional, got shape "
                f"{symbols_or_atomic_numbers.shape}"
            )
        items = symbols_or_atomic_numbers.tolist()
    else:
        try:
            items = list(symbols_or_atomic_numbers)
        except TypeError as exc:
            raise ValueError(
                "input must be a sequence of element identifiers, got "
                f"{type(symbols_or_atomic_numbers).__name__}"
            ) from exc

    if len(items) == 0:
        raise ValueError("species sequence must be non-empty (len > 0)")

    count = len(items)
    atomic_numbers = np.empty(count, dtype=np.int32)
    atomic_masses_amu = np.empty(count, dtype=np.float64)
    for index, raw in enumerate(items):
        try:
            key = _normalize_identifier(raw)
            z, mass_amu = resolve_element(key)
        except ValueError as exc:
            raise ValueError(f"species index {index}: {exc}") from exc
        atomic_numbers[index] = z
        atomic_masses_amu[index] = mass_amu

    return atomic_numbers, atomic_masses_amu
