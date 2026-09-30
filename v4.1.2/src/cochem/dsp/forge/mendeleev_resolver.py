"""Dynamic Mendeleev Atomic Mass Resolver (MC-DSP-14)."""
from __future__ import annotations

import mendeleev


def get_atomic_mass(symbol: str) -> float:
    """Dynamically retrieves atomic mass from mendeleev library (Zero-Mock Rule).

    Args:
        symbol: The chemical element symbol (e.g., 'H', 'C', 'O').

    Returns:
        The atomic mass in atomic mass units (u).

    Raises:
        ValueError: If symbol is invalid or atomic mass is undefined.
    """
    elem = mendeleev.element(symbol)
    if elem.mass is None:
        raise ValueError(f"Atomic mass undefined for element {symbol}")
    return float(elem.mass)
