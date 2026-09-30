"""Dynamic atomic and isotopic mass evaluation backed by the ``mendeleev`` package.

This module is the single source of truth for mass values in the CoChem agentic
workspace. Every value is read at runtime from the SQLite database bundled with
``mendeleev``. The module makes no network calls and keeps no static periodic table.

Supported queries
-----------------
* Standard atomic weight of an element:
  ``MendeleevMassService.get_mass("C")``
* Exact nuclide mass by symbol and mass number:
  ``MendeleevMassService.get_isotope_mass("C", 13)``
* Nuclide specification strings, resolved by
  ``MendeleevMassService.parse_and_resolve(spec)``:

  - ``"D"`` resolves to 2H and ``"T"`` resolves to 3H. These are exact whole-string
    matches, so ``"Db"`` and ``"Ts"`` stay ordinary element symbols.
  - A mass-number prefix: ``"13C"``, ``"18O"``, ``"2H"``.
  - A hyphenated suffix: ``"C-13"``.
  - A bare element symbol, such as ``"C"``, gives the standard atomic weight.

* Molecular weight from a sequence of constituent atoms. Each item may be any
  specification that ``parse_and_resolve`` accepts:
  ``MendeleevMassService.get_molecular_weight(["H", "H", "O"])``

Thread safety: all database access happens while holding a module-level ``RLock``.
Results are copied into immutable snapshots of plain ``float`` and ``int`` values,
so no ORM objects outlive the lock. Public lookups are wrapped in
``functools.lru_cache``.

Error handling: invalid input raises a subclass of ``ValueError``. A non-string
symbol raises ``TypeError``.
"""
from __future__ import annotations

import math
import re
import threading
from functools import lru_cache
from typing import Iterable, NamedTuple, Optional, Tuple

from mendeleev import element

__all__ = [
    "MendeleevMassError",
    "UnknownElementError",
    "UnknownIsotopeError",
    "MassUnavailableError",
    "NuclideSpec",
    "ElementRecord",
    "MendeleevMassService",
]

# Guards every access to the mendeleev database session and its lazy relationships.
_DB_LOCK = threading.RLock()

_SYMBOL_RE = re.compile(r"^[A-Z][a-z]{0,2}$")
_NUCLIDE_RE = re.compile(
    r"^(?:(?P<a>[1-9][0-9]*)(?P<s>[A-Z][a-z]{0,2})"
    r"|(?P<s2>[A-Z][a-z]{0,2})-(?P<a2>[1-9][0-9]*))$"
)

# Hydrogen isotope aliases as (alias, element symbol, mass number) rows.
# These rows hold identities only. Masses are always resolved from mendeleev.
_HYDROGEN_ALIASES: Tuple[Tuple[str, str, int], ...] = (
    ("D", "H", 2),
    ("T", "H", 3),
)


# --------------------------------------------------------------------------- errors
class MendeleevMassError(ValueError):
    """Base domain error for mass evaluation failures."""


class UnknownElementError(MendeleevMassError):
    """Raised when a symbol does not identify a chemical element in mendeleev."""


class UnknownIsotopeError(MendeleevMassError):
    """Raised when a requested nuclide is not recorded for the element."""


class MassUnavailableError(MendeleevMassError):
    """Raised when mendeleev has no mass value for an element or isotope."""


# --------------------------------------------------------------------------- records
class NuclideSpec(NamedTuple):
    """A parsed nuclide specification. ``mass_number`` is None for a bare element."""

    symbol: str
    mass_number: Optional[int]


class ElementRecord(NamedTuple):
    """An immutable snapshot of the mendeleev data needed for mass evaluation."""

    symbol: str
    atomic_number: int
    mass: Optional[float]
    isotopes: Tuple[Tuple[int, Optional[float]], ...]


# --------------------------------------------------------------------------- helpers
def _normalize_symbol(symbol: object) -> str:
    """Validate and normalize an element symbol before any lookup."""
    if not isinstance(symbol, str):
        raise TypeError(
            f"element symbol must be a str, got {type(symbol).__name__}: {symbol!r}"
        )
    cleaned = symbol.strip()
    if not cleaned:
        raise UnknownElementError("element symbol must be a non-empty string")
    if not _SYMBOL_RE.match(cleaned):
        raise UnknownElementError(
            f"{symbol!r} is not a well-formed element symbol "
            "(expected one capital letter optionally followed by lowercase letters)"
        )
    return cleaned


def _validate_mass_number(mass_number: object) -> int:
    if isinstance(mass_number, bool) or not isinstance(mass_number, int):
        raise UnknownIsotopeError(
            f"mass number must be a positive int, got {type(mass_number).__name__}: "
            f"{mass_number!r}"
        )
    if mass_number <= 0:
        raise UnknownIsotopeError(f"mass number must be positive, got {mass_number}")
    return int(mass_number)


@lru_cache(maxsize=256)
def _load_element(symbol: str) -> ElementRecord:
    """Read one element from mendeleev under the database lock and return a snapshot."""
    with _DB_LOCK:
        try:
            el = element(symbol)
        except Exception as exc:  # mendeleev raises different types across versions
            raise UnknownElementError(f"unknown element symbol {symbol!r}") from exc
        if el is None:
            raise UnknownElementError(f"unknown element symbol {symbol!r}")
        found_symbol = getattr(el, "symbol", None)
        if found_symbol != symbol:
            # Guards against name or alias matches such as a lookup resolving by name.
            raise UnknownElementError(
                f"{symbol!r} did not resolve to an element symbol (got {found_symbol!r})"
            )
        raw_mass = el.mass
        atomic_number = int(el.atomic_number)
        # Touch the lazy relationship while still holding the lock.
        iso_rows = []
        for iso in el.isotopes:
            if iso.mass_number is None:
                continue
            iso_mass = None if iso.mass is None else float(iso.mass)
            iso_rows.append((int(iso.mass_number), iso_mass))
    mass = None if raw_mass is None else float(raw_mass)
    return ElementRecord(
        symbol=symbol,
        atomic_number=atomic_number,
        mass=mass,
        isotopes=tuple(iso_rows),
    )


# --------------------------------------------------------------------------- service
class MendeleevMassService:
    """Dynamic, cached, thread-safe access to atomic and isotopic masses (in u).

    Every public method works both on the class (``MendeleevMassService.get_mass``)
    and on an instance (``MendeleevMassService().get_mass``).
    """

    @staticmethod
    @lru_cache(maxsize=512)
    def get_mass(symbol: str) -> float:
        """Return the standard atomic weight ``element(symbol).mass`` as a float."""
        sym = _normalize_symbol(symbol)
        record = _load_element(sym)
        if record.mass is None or not math.isfinite(record.mass):
            raise MassUnavailableError(f"mendeleev has no standard atomic weight for {sym}")
        return float(record.mass)

    @staticmethod
    @lru_cache(maxsize=2048)
    def get_isotope_mass(symbol: str, mass_number: int) -> float:
        """Return the exact nuclide mass for the given element and mass number."""
        sym = _normalize_symbol(symbol)
        a = _validate_mass_number(mass_number)
        record = _load_element(sym)
        for iso_a, iso_mass in record.isotopes:
            if iso_a == a:
                if iso_mass is None or not math.isfinite(iso_mass):
                    raise MassUnavailableError(
                        f"mendeleev has no nuclide mass recorded for {a}{sym}"
                    )
                return float(iso_mass)
        raise UnknownIsotopeError(f"isotope {a}{sym} is not recorded in mendeleev")

    @staticmethod
    def parse_nuclide(spec: str) -> NuclideSpec:
        """Parse a nuclide specification string into (symbol, mass_number or None).

        Grammar (the whole string must match after surrounding whitespace is stripped):
          ``D`` | ``T`` | ``<A><Sym>`` | ``<Sym>-<A>`` | ``<Sym>``
        Here ``A`` is a positive integer without leading zeros.
        """
        if not isinstance(spec, str):
            raise TypeError(
                f"nuclide specification must be a str, got {type(spec).__name__}"
            )
        text = spec.strip()
        if not text:
            raise MendeleevMassError("nuclide specification must be a non-empty string")
        for alias, alias_symbol, alias_a in _HYDROGEN_ALIASES:
            if text == alias:
                return NuclideSpec(alias_symbol, alias_a)
        match = _NUCLIDE_RE.match(text)
        if match is not None:
            if match.group("s") is not None:
                return NuclideSpec(match.group("s"), int(match.group("a")))
            return NuclideSpec(match.group("s2"), int(match.group("a2")))
        if _SYMBOL_RE.match(text):
            return NuclideSpec(text, None)
        raise MendeleevMassError(f"malformed nuclide specification {spec!r}")

    @staticmethod
    @lru_cache(maxsize=2048)
    def parse_and_resolve(spec: str) -> float:
        """Parse a nuclide or element specification and return its mass in u."""
        parsed = MendeleevMassService.parse_nuclide(spec)
        if parsed.mass_number is None:
            return MendeleevMassService.get_mass(parsed.symbol)
        return MendeleevMassService.get_isotope_mass(parsed.symbol, parsed.mass_number)

    @staticmethod
    def get_molecular_weight(symbols: Iterable[str]) -> float:
        """Sum the masses of a sequence of constituent atoms.

        Items may be element symbols (standard weights) or nuclide specifications
        such as ``"D"`` or ``"13C"`` (exact nuclide masses). Formula strings such
        as ``"H2O"`` are not parsed. Pass one item per atom.
        """
        if isinstance(symbols, (str, bytes, bytearray)):
            raise TypeError(
                "get_molecular_weight expects a sequence of atom symbols, not a string"
            )
        try:
            items = tuple(symbols)
        except TypeError as exc:
            raise TypeError("get_molecular_weight expects an iterable of symbols") from exc
        if not items:
            raise MendeleevMassError("cannot compute molecular weight of an empty sequence")
        masses = [MendeleevMassService.parse_and_resolve(item) for item in items]
        return float(math.fsum(masses))

    @staticmethod
    def get_atomic_number(symbol: str) -> int:
        """Return the atomic number of an element read from mendeleev."""
        return _load_element(_normalize_symbol(symbol)).atomic_number

    @staticmethod
    def list_isotopes(symbol: str) -> Tuple[Tuple[int, float], ...]:
        """Return the (mass_number, mass) pairs that have a recorded mass."""
        record = _load_element(_normalize_symbol(symbol))
        return tuple((a, m) for a, m in record.isotopes if m is not None)

    @classmethod
    def cache_statistics(cls):
        """Return the LRU cache statistics of every cached lookup layer."""
        return {
            "get_mass": cls.get_mass.cache_info(),
            "get_isotope_mass": cls.get_isotope_mass.cache_info(),
            "parse_and_resolve": cls.parse_and_resolve.cache_info(),
            "load_element": _load_element.cache_info(),
        }

    @classmethod
    def clear_caches(cls) -> None:
        """Clear every LRU cache layer. The next lookups re-read from mendeleev."""
        cls.get_mass.cache_clear()
        cls.get_isotope_mass.cache_clear()
        cls.parse_and_resolve.cache_clear()
        _load_element.cache_clear()
