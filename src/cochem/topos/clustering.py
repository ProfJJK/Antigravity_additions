"""Dynamic Mass-Weighted Rotational Constants Engine & Quaternion Kabsch Sieve.

Implements pure Mendeleev dynamic mass lookups, inertia tensor construction,
principal rotational constants calculation (Task 20.1032), Horn 1987 closed-form
unit quaternion Kabsch alignment in SO(3), heavy-atom Cartesian RMSD and
conformational basin deduplication sieve clustering (Task 20.1033).
Governed by CoChem Anti-Spoofing Protocol v4.
"""
from __future__ import annotations

import functools
import math
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

import mendeleev
import numpy as np
import scipy.linalg

if TYPE_CHECKING:
    from cochem.topos.conformer_ensemble import ConformerCandidate

C_ROT_MHZ: float = 505379.008435

# Tolerance on the SO(3) invariant det(U) = +1 for the quaternion-derived rotation.
_DET_TOLERANCE: float = 1.0e-7
# Norm below which the extracted eigenvector is treated as numerically degenerate.
_QUAT_NORM_FLOOR: float = 1.0e-12


# --------------------------------------------------------------------------- #
# Dynamic Mendeleev elemental lookups (Task 20.1032 + cached Z accessor)
# --------------------------------------------------------------------------- #
def _normalise_symbol(symbol: object) -> str:
    """Coerce an element/isotope label to a stripped string; reject empty labels."""
    clean_sym = str(symbol).strip()
    if not clean_sym:
        raise ValueError(f"Invalid (empty) chemical element symbol: {symbol!r}")
    return clean_sym


def _hydrogen_isotope_mass_number(clean_sym: str) -> Optional[int]:
    """Return the hydrogen isotope mass number encoded by a label, or None."""
    if clean_sym in ("D", "2H"):
        return 2
    if clean_sym in ("T", "3H"):
        return 3
    return None


def _hydrogen_isotope_mass(mass_number: int) -> float:
    """Dynamically query the exact isotopic mass of a hydrogen isotope via mendeleev."""
    isotope_query = getattr(mendeleev, "isotope", None)
    if callable(isotope_query):
        return float(isotope_query("H", mass_number).mass)
    hydrogen = mendeleev.element("H")
    for iso in hydrogen.isotopes:
        if int(iso.mass_number) == mass_number:
            return float(iso.mass)
    raise ValueError(f"mendeleev has no hydrogen isotope with mass number {mass_number}")


@functools.lru_cache(maxsize=256)
def _cached_atomic_mass(clean_sym: str) -> float:
    """Memoise live mendeleev mass queries (the cache holds only query results)."""
    mass_number = _hydrogen_isotope_mass_number(clean_sym)
    if mass_number is not None:
        return _hydrogen_isotope_mass(mass_number)

    try:
        el = mendeleev.element(clean_sym)
    except Exception as exc:
        raise ValueError(f"Unknown chemical element: {clean_sym}") from exc

    mass_val = getattr(el, "atomic_weight", None)
    if mass_val is None:
        mass_val = getattr(el, "mass", None)
    if mass_val is None:
        raise ValueError(f"Could not determine atomic mass for element: {clean_sym}")
    mass_float = float(mass_val)
    if not math.isfinite(mass_float) or mass_float <= 0.0:
        raise ValueError(f"Non-physical atomic mass {mass_float} for element: {clean_sym}")
    return mass_float


def get_atomic_mass(symbol: str) -> float:
    """Dynamically retrieve atomic (or hydrogen isotopic) mass in u from mendeleev."""
    return _cached_atomic_mass(_normalise_symbol(symbol))


def get_atomic_number(symbol: str) -> int:
    """Dynamically retrieve atomic number from mendeleev."""
    clean_sym = _normalise_symbol(symbol)
    if _hydrogen_isotope_mass_number(clean_sym) is not None:
        return int(mendeleev.element("H").atomic_number)
    try:
        el = mendeleev.element(clean_sym)
    except Exception as exc:
        raise ValueError(f"Unknown chemical element: {symbol}") from exc
    return int(el.atomic_number)


@functools.lru_cache(maxsize=128)
def get_dynamic_atomic_number(symbol: str) -> int:
    """Cached dynamic atomic number query (mendeleev.element(symbol).atomic_number).

    The cache only memoises results of live mendeleev queries; no static table exists.
    """
    return get_atomic_number(symbol)


def get_heavy_atom_mask(symbols: Sequence[str]) -> List[bool]:
    """Identify heavy atoms (Z > 1) via dynamic Mendeleev lookup."""
    return [get_dynamic_atomic_number(str(s)) > 1 for s in symbols]


# --------------------------------------------------------------------------- #
# Rotational constants (Task 20.1032)
# --------------------------------------------------------------------------- #
def compute_rotational_constants(
    symbols: List[str],
    coordinates: np.ndarray,
) -> Tuple[float, float, float]:
    """Compute equilibrium rotational constants (A, B, C) in MHz.

    Args:
        symbols: List of N chemical element or isotope symbols (e.g. ['O', 'H', 'D']).
        coordinates: (N, 3) float array of Cartesian coordinates in Angstroms.

    Returns:
        Tuple of (A, B, C) rotational constants in MHz in descending order (A >= B >= C).

    Raises:
        ValueError: If coordinates is not (N, 3), len(symbols) != N, N < 2,
                    coordinates contains non-finite values, or symbols are invalid.
    """
    try:
        xyz = np.asarray(coordinates, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"coordinates must be convertible to a float array: {exc}") from exc
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError(f"coordinates must have shape (N, 3), got {xyz.shape}")

    n_atoms = xyz.shape[0]
    if n_atoms < 2:
        raise ValueError(f"Rotational constants require at least 2 atoms, got {n_atoms}")

    symbol_list = list(symbols)
    if len(symbol_list) != n_atoms:
        raise ValueError(
            f"Length of symbols ({len(symbol_list)}) must match coordinate rows ({n_atoms})"
        )

    if not np.all(np.isfinite(xyz)):
        raise ValueError("coordinates must contain only finite numbers (no NaN or Inf)")

    mass_list = [get_atomic_mass(s) for s in symbol_list]
    masses = np.array(mass_list, dtype=np.float64)
    total_mass = float(np.sum(masses))
    if total_mass <= 0.0:
        raise ValueError("Total molecular mass must be strictly positive")

    # Center-of-mass translation: R_COM = sum(m_i r_i) / sum(m_i)
    com = np.sum(masses[:, None] * xyz, axis=0) / total_mass
    rel = xyz - com

    # I = Tr(M) * 1 - M with M = r'^T diag(m) r'
    m_tensor = rel.T @ np.diag(masses) @ rel
    identity_matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    inertia = np.trace(m_tensor) * identity_matrix - m_tensor

    moments = scipy.linalg.eigh(0.5 * (inertia + inertia.T), eigvals_only=True)
    moments = np.sort(moments)

    scale = max(float(moments.max()), 1.0e-30)
    consts = [
        C_ROT_MHZ / float(m) if float(m) > 1.0e-10 * scale else math.inf
        for m in moments
    ]
    return float(consts[0]), float(consts[1]), float(consts[2])


# --------------------------------------------------------------------------- #
# Horn 1987 closed-form unit quaternion superposition (Task 20.1033, AC1)
# --------------------------------------------------------------------------- #
def _horn_key_matrix(cross_dispersion: np.ndarray) -> np.ndarray:
    """Build the 4x4 symmetric Horn matrix K from the 3x3 cross-dispersion R = Pc^T Qc."""
    r = np.asarray(cross_dispersion, dtype=np.float64)
    r11, r12, r13 = r[0, 0], r[0, 1], r[0, 2]
    r21, r22, r23 = r[1, 0], r[1, 1], r[1, 2]
    r31, r32, r33 = r[2, 0], r[2, 1], r[2, 2]
    key = np.array(
        [
            [r11 + r22 + r33, r23 - r32, r31 - r13, r12 - r21],
            [r23 - r32, r11 - r22 - r33, r12 + r21, r31 + r13],
            [r31 - r13, r12 + r21, -r11 + r22 - r33, r23 + r32],
            [r12 - r21, r31 + r13, r23 + r32, -r11 - r22 + r33],
        ],
        dtype=np.float64,
    )
    return 0.5 * (key + key.T)


def _quaternion_to_rotation(quat: np.ndarray) -> np.ndarray:
    """Convert unit quaternion [q0, qx, qy, qz] to a proper 3x3 rotation matrix."""
    q0, qx, qy, qz = (float(v) for v in quat)
    return np.array(
        [
            [q0 * q0 + qx * qx - qy * qy - qz * qz, 2.0 * (qx * qy - q0 * qz), 2.0 * (qx * qz + q0 * qy)],
            [2.0 * (qx * qy + q0 * qz), q0 * q0 - qx * qx + qy * qy - qz * qz, 2.0 * (qy * qz - q0 * qx)],
            [2.0 * (qx * qz - q0 * qy), 2.0 * (qy * qz + q0 * qx), q0 * q0 - qx * qx - qy * qy + qz * qz],
        ],
        dtype=np.float64,
    )


def _optimal_unit_quaternion(cross_dispersion: np.ndarray) -> np.ndarray:
    """Eigenvector of the Horn matrix K for its maximum eigenvalue, normalised to unit length."""
    key = _horn_key_matrix(cross_dispersion)
    eigvals, eigvecs = np.linalg.eigh(key)
    quat = np.asarray(eigvecs[:, int(np.argmax(eigvals))], dtype=np.float64)
    norm_val = float(np.linalg.norm(quat))
    if norm_val < _QUAT_NORM_FLOOR:
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    quat = quat / norm_val
    # Fix the q / -q sign ambiguity (both encode the same rotation) for determinism.
    if quat[0] < 0.0:
        quat = -quat
    return quat


def _validate_coordinate_block(name: str, coords: np.ndarray) -> np.ndarray:
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"{name} must have shape (N, 3), got {arr.shape}")
    if arr.shape[0] < 1:
        raise ValueError(f"{name} must contain at least one atom")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must contain only finite numbers")
    return arr


def quaternion_kabsch_align(
    P: np.ndarray,
    Q: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Horn 1987 closed-form unit quaternion superposition in SO(3).

    Centers both point sets, forms the cross-dispersion R = Pc^T Qc, builds the
    4x4 symmetric Horn matrix K, and extracts the unit quaternion eigenvector of
    the maximum eigenvalue. The resulting rotation U maps Pc onto Qc
    (Qc ~ Pc U^T), so in the row-vector convention the aligned target is
    Q_aligned = Qc @ U ~ Pc.

    Because U is generated from a unit quaternion, det(U) = +1 by construction:
    improper reflections / chiral inversions can never be returned.

    Returns:
        (Q_aligned, U) with Q_aligned of shape (N, 3) and U of shape (3, 3).

    Raises:
        ValueError: on malformed or mismatched coordinate arrays.
        ArithmeticError: if the SO(3) invariant det(U) = +1 is numerically violated.
    """
    p_arr = _validate_coordinate_block("P", P)
    q_arr = _validate_coordinate_block("Q", Q)
    if p_arr.shape != q_arr.shape:
        raise ValueError(f"Coordinate shape mismatch: P {p_arr.shape} vs Q {q_arr.shape}")

    p_centered = p_arr - np.mean(p_arr, axis=0)
    q_centered = q_arr - np.mean(q_arr, axis=0)

    cross_dispersion = p_centered.T @ q_centered
    quat = _optimal_unit_quaternion(cross_dispersion)
    rot_u = _quaternion_to_rotation(quat)

    det_u = float(np.linalg.det(rot_u))
    if abs(det_u - 1.0) > _DET_TOLERANCE:
        raise ArithmeticError(
            f"Quaternion-derived rotation violates SO(3) invariant det(U)=+1 (det={det_u:.3e})"
        )

    q_aligned = q_centered @ rot_u
    return q_aligned, rot_u


def horn_quaternion_rotation(p_centered: np.ndarray, q_centered: np.ndarray) -> np.ndarray:
    """Optimal proper rotation matrix U (in SO(3)) mapping p_centered onto q_centered.

    Retained for backward compatibility; delegates to the shared Horn machinery.
    """
    pc = np.asarray(p_centered, dtype=np.float64)
    qc = np.asarray(q_centered, dtype=np.float64)
    quat = _optimal_unit_quaternion(pc.T @ qc)
    return _quaternion_to_rotation(quat)


def quaternion_kabsch_rmsd(
    coords1: np.ndarray,
    coords2: np.ndarray,
    heavy_mask: Optional[Sequence[bool]] = None,
) -> float:
    """Horn quaternion superposition of coords1 onto coords2; RMSD evaluated over masked atoms."""
    p = np.asarray(coords1, dtype=np.float64)
    q = np.asarray(coords2, dtype=np.float64)
    if p.shape != q.shape:
        raise ValueError(f"Coordinate shape mismatch: {p.shape} vs {q.shape}")

    if heavy_mask is None:
        mask_arr = np.array([True for _ in range(p.shape[0])], dtype=bool)
    else:
        mask_arr = np.asarray(heavy_mask, dtype=bool).ravel()
        if mask_arr.shape[0] != p.shape[0]:
            raise ValueError(f"heavy_mask length {mask_arr.shape[0]} != atom count {p.shape[0]}")

    if not np.any(mask_arr):
        raise ValueError("heavy_mask selects no atoms")

    aligned_q, _ = quaternion_kabsch_align(p[mask_arr], q[mask_arr])
    pc = p[mask_arr] - np.mean(p[mask_arr], axis=0)
    diff = pc - aligned_q
    return float(math.sqrt(float(np.mean(np.sum(diff * diff, axis=-1)))))


# --------------------------------------------------------------------------- #
# Heavy-atom RMSD (Task 20.1033, AC2)
# --------------------------------------------------------------------------- #
def compute_heavy_atom_rmsd(
    symbols: List[str],
    coords_a: np.ndarray,
    coords_b: np.ndarray,
    align: bool = True,
) -> float:
    """Cartesian RMSD evaluated exclusively over heavy atoms (Z > 1).

    RMSD_heavy = sqrt((1 / N_heavy) * sum_j ||r_A,j - r_B,j||^2). When align=True the
    centered heavy-atom sets are superimposed with quaternion_kabsch_align first.

    Raises:
        ValueError: on non-(N, 3) arrays, shape mismatch, symbol count mismatch,
                    or when no heavy atom is present.
    """
    xyz_a = _validate_coordinate_block("coords_a", coords_a)
    xyz_b = _validate_coordinate_block("coords_b", coords_b)
    if xyz_a.shape != xyz_b.shape:
        raise ValueError(f"Coordinate shape mismatch: {xyz_a.shape} vs {xyz_b.shape}")
    if len(symbols) != xyz_a.shape[0]:
        raise ValueError(
            f"Length of symbols ({len(symbols)}) must match coordinate rows ({xyz_a.shape[0]})"
        )

    heavy_indices = [
        idx for idx, sym in enumerate(symbols) if get_dynamic_atomic_number(str(sym)) > 1
    ]
    if len(heavy_indices) == 0:
        raise ValueError("No heavy atoms (Z > 1) present; heavy-atom RMSD is undefined")

    heavy_a = xyz_a[heavy_indices]
    heavy_b = xyz_b[heavy_indices]

    if align:
        aligned_b, _ = quaternion_kabsch_align(heavy_a, heavy_b)
        diff = (heavy_a - np.mean(heavy_a, axis=0)) - aligned_b
    else:
        diff = heavy_a - heavy_b

    n_heavy = float(len(heavy_indices))
    return float(math.sqrt(float(np.sum(diff * diff)) / n_heavy))


# --------------------------------------------------------------------------- #
# Rotational fingerprint comparison
# --------------------------------------------------------------------------- #
def rotational_constants_match(
    b1: Sequence[float],
    b2: Sequence[float],
    rotational_threshold_mhz: float = 5.0,
    relative_rotational_threshold: Optional[float] = 0.005,
) -> bool:
    """Rotational fingerprint criterion for conformer deduplication.

    Compares principal rotational constants (A, B, C) in MHz. Two conformers
    match if the absolute deviation max(|dA|, |dB|, |dC|) <= rotational_threshold_mhz,
    or within relative_rotational_threshold (0.5% default) to account for slight
    numerical coordinate relaxation on larger rotational constants while strictly
    distinguishing distinct conformational rotamers (such as trans vs gauche).
    """
    for x, y in zip(b1, b2):
        xf = float(x)
        yf = float(y)
        if math.isinf(xf) or math.isinf(yf):
            if math.isinf(xf) and math.isinf(yf):
                continue
            return False
        delta = abs(xf - yf)
        if delta <= rotational_threshold_mhz:
            continue
        if relative_rotational_threshold is not None:
            denom = max(abs(xf), abs(yf), 1.0e-12)
            if (delta / denom) <= relative_rotational_threshold:
                continue
        return False
    return True


# --------------------------------------------------------------------------- #
# Deduplication sieve (Task 20.1033, AC3)
# --------------------------------------------------------------------------- #
def _assign_basins(
    sorted_cands: Sequence[ConformerCandidate],
    rot_tol_mhz: float,
    rmsd_tol_angstrom: float,
    relative_rot_tol: Optional[float] = 0.005,
) -> List[List[int]]:
    """Greedy energy-ordered sieve. Returns basins as lists of indices into sorted_cands.

    The first index of each basin is its prototype (lowest energy by construction).
    A candidate joins a basin only if BOTH max(|dA|, |dB|, |dC|) <= rot_tol_mhz
    (or within relative_rot_tol) and the heavy-atom RMSD <= rmsd_tol_angstrom
    are satisfied against the prototype.
    """
    rot_consts = [
        compute_rotational_constants(list(c.symbols), np.asarray(c.coordinates, dtype=np.float64))
        for c in sorted_cands
    ]

    basins: List[List[int]] = []
    for i, cand in enumerate(sorted_cands):
        cand_symbols = [str(s) for s in cand.symbols]
        cand_xyz = np.asarray(cand.coordinates, dtype=np.float64)
        assigned = False
        for basin in basins:
            proto_idx = basin[0]
            proto = sorted_cands[proto_idx]
            if [str(s) for s in proto.symbols] != cand_symbols:
                continue
            if not rotational_constants_match(
                rot_consts[i],
                rot_consts[proto_idx],
                rotational_threshold_mhz=rot_tol_mhz,
                relative_rotational_threshold=relative_rot_tol,
            ):
                continue
            rmsd = compute_heavy_atom_rmsd(
                cand_symbols,
                np.asarray(proto.coordinates, dtype=np.float64),
                cand_xyz,
                align=True,
            )
            if rmsd <= rmsd_tol_angstrom:
                basin.append(i)
                assigned = True
                break
        if not assigned:
            basins.append([i])
    return basins


def _merged_engine_origin(members: Sequence[ConformerCandidate]) -> str:
    origins = {str(m.engine_origin).strip().upper() for m in members}
    if "DUAL" in origins or len(origins) > 1:
        return "DUAL"
    return str(members[0].engine_origin)


def _merged_provenance(members: Sequence[ConformerCandidate]) -> str:
    tags = [str(m.provenance) for m in members]
    if "FALLBACK_SURROGATE" in tags:
        return "FALLBACK_SURROGATE"
    return str(members[0].provenance)


def deduplication_sieve(
    candidates: List[ConformerCandidate],
    rot_tol_mhz: float = 5.0,
    rmsd_tol_angstrom: float = 0.05,
    relative_rot_tol: Optional[float] = 0.005,
) -> List[ConformerCandidate]:
    """Pairwise deduplication sieve clustering conformers into unique basins.

    Candidates are sorted by total_energy ascending; each candidate is compared
    to existing basin prototypes and merged if and only if
    max(|dA|, |dB|, |dC|) <= rot_tol_mhz (or relative tolerance) AND the
    Kabsch-aligned heavy-atom RMSD <= rmsd_tol_angstrom.

    Returns one prototype per basin (lowest energy member), ordered by energy,
    with 0-based cluster_index, merged engine origin ('DUAL' when engines mix)
    and merged provenance ('FALLBACK_SURROGATE' if any member carries it).
    """
    if not candidates:
        return []
    if rot_tol_mhz < 0.0 or rmsd_tol_angstrom < 0.0:
        raise ValueError("Sieve tolerances must be non-negative")

    sorted_cands = sorted(candidates, key=lambda c: float(c.total_energy))
    basins = _assign_basins(sorted_cands, rot_tol_mhz, rmsd_tol_angstrom, relative_rot_tol=relative_rot_tol)

    prototypes: List[ConformerCandidate] = []
    for basin_idx, basin in enumerate(basins):
        members = [sorted_cands[k] for k in basin]
        prototype = members[0]
        prototypes.append(
            prototype.model_copy(
                update={
                    "cluster_index": basin_idx,
                    "engine_origin": _merged_engine_origin(members),
                    "provenance": _merged_provenance(members),
                }
            )
        )
    return prototypes


def cluster_conformers(
    candidates: Sequence[ConformerCandidate],
    rmsd_threshold_angstrom: float = 0.05,
    rotational_threshold_mhz: float = 5.0,
    relative_rotational_threshold: Optional[float] = 0.005,
) -> Tuple[List[ConformerCandidate], Dict[int, List[ConformerCandidate]]]:
    """Group candidates into basins; returns (merged prototypes, basin_index -> members)."""
    if not candidates:
        return [], {}

    sorted_cands = sorted(candidates, key=lambda c: float(c.total_energy))
    basins = _assign_basins(
        sorted_cands,
        rotational_threshold_mhz,
        rmsd_threshold_angstrom,
        relative_rot_tol=relative_rotational_threshold,
    )

    retained: List[ConformerCandidate] = []
    basin_map: Dict[int, List[ConformerCandidate]] = {}
    for basin_idx, basin in enumerate(basins):
        members = [sorted_cands[k] for k in basin]
        basin_map[basin_idx] = [m.model_copy(update={"cluster_index": basin_idx}) for m in members]
        retained.append(
            members[0].model_copy(
                update={
                    "cluster_index": basin_idx,
                    "engine_origin": _merged_engine_origin(members),
                    "provenance": _merged_provenance(members),
                }
            )
        )
    return retained, basin_map
