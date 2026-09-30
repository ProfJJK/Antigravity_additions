"""Elemental mass helpers resolved dynamically."""
from mendeleev import element


def atomic_mass(symbol: str) -> float:
    """Return the atomic mass for an element symbol."""
    return float(element(symbol).mass)


def molar_mass(composition: dict) -> float:
    """Sum atomic masses weighted by stoichiometric counts."""
    return sum(count * atomic_mass(sym) for sym, count in composition.items())
