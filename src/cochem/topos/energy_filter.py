"""Subsystem for relative potential energy filtering of conformer ensembles.

Implements the thermodynamic energy window used by the CoChem TOPOS conformer
pipeline. Given a pool of conformer candidates with total electronic energies
expressed in kcal/mol, the filter

1. locates the global minimum ``E_min = min(E_i)``,
2. computes relative energies ``delta_E_i = E_i - E_min``,
3. retains conformers satisfying ``delta_E_i <= window_threshold``.

The window threshold may be supplied in kcal/mol, kJ/mol, hartree or eV and is
converted to kcal/mol with the authoritative CODATA-derived constants below.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:
    from cochem.topos.conformer_ensemble import ConformerCandidate

# Authoritative CODATA recommended constants:
# 1 thermochemical calorie = 4.184 Joules  -> 1 kcal = 4.184 kJ
# 1 Hartree = 627.509474 kcal/mol
# 1 eV = 23.060542 kcal/mol
KJ_PER_KCAL: float = 4.184
KCAL_TO_KJ: float = 4.184
HARTREE_TO_KCAL: float = 627.509474
EV_TO_KCAL: float = 23.060542

# Absolute tolerance (kcal/mol) protecting boundary conformers from IEEE-754
# round-off during unit conversion (e.g. 12.552 / 4.184 -> 2.9999999999999996).
_WINDOW_TOLERANCE_KCAL: float = 1e-9

_KCAL_ALIASES = frozenset({"kcal/mol", "kcal"})
_KJ_ALIASES = frozenset({"kj/mol", "kj"})
_HARTREE_ALIASES = frozenset({"hartree", "ha"})
_EV_ALIASES = frozenset({"ev", "electronvolt"})


def _normalize_unit(unit: str) -> str:
    """Return the canonical lower-case unit token or raise ValueError."""
    if not isinstance(unit, str):
        raise ValueError(f"Energy unit must be a string, got {type(unit).__name__}")
    token = unit.strip().lower()
    if token in _KCAL_ALIASES or token in _KJ_ALIASES or token in _HARTREE_ALIASES or token in _EV_ALIASES:
        return token
    raise ValueError(
        f"Unsupported energy unit: {unit!r}. "
        "Supported units are 'kcal/mol', 'kJ/mol', 'hartree', and 'eV'."
    )


def _to_kcal_per_mol(value: float, unit_token: str) -> float:
    """Convert a scalar expressed in a canonical unit token into kcal/mol."""
    val = float(value)
    if unit_token in _KCAL_ALIASES:
        return val
    if unit_token in _KJ_ALIASES:
        return val / KJ_PER_KCAL
    if unit_token in _HARTREE_ALIASES:
        return val * HARTREE_TO_KCAL
    if unit_token in _EV_ALIASES:
        return val * EV_TO_KCAL
    raise ValueError(f"Unsupported energy unit: {unit_token!r}")


def _from_kcal_per_mol(value_kcal: float, unit_token: str) -> float:
    """Convert a kcal/mol scalar into the unit identified by a canonical token."""
    val = float(value_kcal)
    if unit_token in _KCAL_ALIASES:
        return val
    if unit_token in _KJ_ALIASES:
        return val * KJ_PER_KCAL
    if unit_token in _HARTREE_ALIASES:
        return val / HARTREE_TO_KCAL
    if unit_token in _EV_ALIASES:
        return val / EV_TO_KCAL
    raise ValueError(f"Unsupported energy unit: {unit_token!r}")


def convert_energy(value: float, from_unit: str, to_unit: str) -> float:
    """Convert an energy scalar between kcal/mol, kJ/mol, hartree and eV."""
    src = _normalize_unit(from_unit)
    dst = _normalize_unit(to_unit)
    return float(_from_kcal_per_mol(_to_kcal_per_mol(value, src), dst))


def _candidate_energy_kcal(candidate: ConformerCandidate) -> float:
    """Extract the total electronic energy of a candidate in kcal/mol."""
    total = getattr(candidate, "total_energy", None)
    if total is not None:
        energy = float(total)
    elif getattr(candidate, "energy_kcal_mol", None) is not None:
        energy = float(candidate.energy_kcal_mol)
    elif getattr(candidate, "energy_hartree", None) is not None:
        energy = float(candidate.energy_hartree) * HARTREE_TO_KCAL
    else:
        raise ValueError(f"Candidate {candidate!r} has no recognized total electronic energy")
    if not math.isfinite(energy):
        raise ValueError(f"Candidate total electronic energy must be finite, got {energy!r}")
    return energy


def filter_by_energy_window(
    candidates: Sequence[ConformerCandidate],
    window_threshold: float = 3.0,
    unit: str = "kcal/mol",
    window_kcal_mol: Optional[float] = None,
) -> List[ConformerCandidate]:
    """Filter conformer candidates within a relative energy window above the global minimum.

    Parameters
    ----------
    candidates : Sequence[ConformerCandidate]
        Input pool of conformer candidates (total energies in kcal/mol).
    window_threshold : float, default=3.0
        Cutoff window above the global minimum energy, expressed in ``unit``.
    unit : str, default="kcal/mol"
        Unit of ``window_threshold``: 'kcal/mol', 'kJ/mol', 'hartree' or 'eV'
        (case-insensitive, surrounding whitespace ignored).
    window_kcal_mol : Optional[float], default=None
        Backward-compatibility alias overriding ``window_threshold`` in kcal/mol.

    Returns
    -------
    List[ConformerCandidate]
        Retained candidates (original order) with ``relative_energy`` populated
        in kcal/mol. Relative energies are assigned on every input candidate.

    Raises
    ------
    ValueError
        If the unit is unsupported, the threshold is negative / non-finite, or a
        candidate lacks a finite total electronic energy.
    """
    if window_kcal_mol is not None:
        window_threshold = window_kcal_mol
        unit = "kcal/mol"

    # Unit validation happens before the empty-input short-circuit so that
    # misconfigured callers are detected regardless of pool size.
    unit_token = _normalize_unit(unit)

    try:
        threshold_kcal = _to_kcal_per_mol(window_threshold, unit_token)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid energy window threshold: {window_threshold!r}") from exc
    if not math.isfinite(threshold_kcal) or threshold_kcal < 0.0:
        raise ValueError(
            f"Energy window threshold must be finite and non-negative, got {window_threshold!r} {unit}"
        )

    if not candidates:
        return []

    energies: List[float] = [_candidate_energy_kcal(cand) for cand in candidates]
    min_energy = min(energies)

    retained: List[ConformerCandidate] = []
    for candidate, energy_val in zip(candidates, energies):
        delta_energy = float(energy_val - min_energy)
        if hasattr(candidate, "relative_energy"):
            candidate.relative_energy = delta_energy
        if hasattr(candidate, "relative_energy_kcal_mol"):
            candidate.relative_energy_kcal_mol = delta_energy
        if delta_energy <= threshold_kcal + _WINDOW_TOLERANCE_KCAL:
            retained.append(candidate)

    return retained
