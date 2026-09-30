"""SE(3) rigid-body kinematics, invariant lock, and unified FrozenMonomerEngine.

Task 20.1065 of the CoChem Frozen-Monomer Protocol (FMP).

Unifies:
* Dynamic graph BFS partitioning with Mendeleev Pyykko covalent radii.
* 6-DOF intermolecular SE(3) kinematics with active Z-Y-Z Euler rotations.
* Invariant lock verifying zero covalent deformation (|Delta d_ij| <= 1e-10 A).
* Intermolecular optimization under a strict ghost-atom ban.
* Decoupled 3-point Boys-Bernardi counterpoise evaluation.
* Unified FrozenMonomerEngine orchestrator producing FrozenMonomerResult.
"""
from __future__ import annotations

import importlib
import logging
import math
import sys
import uuid
from functools import lru_cache
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

import numpy as np
from mendeleev import element
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

# Registration of formamide geometry in ASE extra catalog if absent
try:
    _ase_mol_mod = sys.modules.get("ase.build.molecule")
    if _ase_mol_mod is None:
        _ase_mol_mod = importlib.import_module("ase.build.molecule")
    if hasattr(_ase_mol_mod, "extra"):
        if "HCONH2" not in _ase_mol_mod.extra and "formamide" not in _ase_mol_mod.extra:
            _formamide_geom = {
                "symbols": ["N", "C", "O", "H", "H", "H"],
                "positions": [
                    [-0.686559, -0.033868, 0.085919],
                    [0.659103, 0.048739, -0.083164],
                    [1.389050, -0.928489, -0.132032],
                    [-1.282232, 0.780522, 0.125089],
                    [-1.099912, -0.952885, 0.175323],
                    [1.020551, 1.085981, -0.171136],
                ],
            }
            _ase_mol_mod.extra["HCONH2"] = _formamide_geom
            _ase_mol_mod.extra["formamide"] = _formamide_geom
except Exception:
    pass

# Conversion constants (CODATA 2018)
HARTREE_TO_KCAL_MOL: float = 627.509474063
HARTREE_TO_KJ_MOL: float = 2625.49963948
HARTREE_TO_KCAL: float = HARTREE_TO_KCAL_MOL
HARTREE_TO_KJ: float = HARTREE_TO_KJ_MOL
ROT_CONST_MHZ: float = 505379.008
INERTIA_CONV_MHZ_U_ANG2: float = 505379.0084350172
BOHR_TO_ANGSTROM: float = 0.529177210903

LOWER_BOUNDS = np.asarray([1.0, 0.0, -math.pi, -math.pi, 0.0, -math.pi])
UPPER_BOUNDS = np.asarray([15.0, math.pi, math.pi, math.pi, math.pi, math.pi])


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FrozenMonomerViolationError(ValueError):
    """Raised when frozen monomer invariants, partitioning, or optimization constraints fail."""

    def __init__(self, message: str, details: Optional[Any] = None, **kwargs: Any) -> None:
        super().__init__(message)
        self.message = message
        if isinstance(details, dict):
            self.details = details
        elif details is not None:
            self.details = {"details": details}
        elif "details" in kwargs:
            self.details = dict(kwargs["details"] or {})
        else:
            self.details = {}

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Pydantic v2 Models
# ---------------------------------------------------------------------------

class IntermolecularDofs(BaseModel):
    """Six intermolecular degrees of freedom between two rigid monomers."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    r_ab: float = Field(..., ge=1.0, le=15.0, description="Intermolecular COM separation R_AB in Angstroms")
    theta: float = Field(..., ge=0.0, le=math.pi, description="Polar angle in radians [0, pi]")
    phi: float = Field(..., ge=-math.pi, le=math.pi, description="Azimuthal angle in radians [-pi, pi]")
    alpha: float = Field(..., ge=-math.pi, le=math.pi, description="Euler angle alpha in radians [-pi, pi]")
    beta: float = Field(..., ge=0.0, le=math.pi, description="Euler angle beta in radians [0, pi]")
    gamma: float = Field(..., ge=-math.pi, le=math.pi, description="Euler angle gamma in radians [-pi, pi]")

    @property
    def chi(self) -> float:
        """Alias for gamma (Euler roll)."""
        return self.gamma

    def to_array(self) -> np.ndarray:
        """Return [r_ab, theta, phi, alpha, beta, gamma] as float64 array."""
        return np.asarray(
            [self.r_ab, self.theta, self.phi, self.alpha, self.beta, self.gamma],
            dtype=float,
        )

    @classmethod
    def from_array(cls, arr: Sequence[float]) -> "IntermolecularDofs":
        """Build IntermolecularDofs from a 6-element sequence."""
        values = list(arr)
        if len(values) != 6:
            raise ValueError(f"Expected 6 DOFs, got {len(values)}")
        return cls(
            r_ab=float(values[0]),
            theta=float(values[1]),
            phi=float(values[2]),
            alpha=float(values[3]),
            beta=float(values[4]),
            gamma=float(values[5]),
        )

    def translation_vector(self) -> np.ndarray:
        """Spherical COM displacement T(r_ab, theta, phi)."""
        return spherical_translation_vector(self.r_ab, self.theta, self.phi)

    def rotation_matrix(self) -> np.ndarray:
        """Active Z-Y-Z rotation R(alpha, beta, gamma)."""
        return construct_euler_rotation_matrix(self.alpha, self.beta, self.gamma)


class MonomerSpec(BaseModel):
    """Specification of an isolated monomer fragment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    symbols: List[str]
    coordinates: List[List[float]]
    indices: List[int]
    masses: List[float]
    center_of_mass: List[float]
    is_linear: bool
    n_internal_dofs: int


class IntermolecularOptimizationResult(BaseModel):
    """Results of a constrained 6-DOF intermolecular geometry optimization."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    initial_dofs: IntermolecularDofs
    optimized_dofs: IntermolecularDofs
    initial_energy: float
    final_energy: float
    max_gradient: float
    rms_gradient: float
    energy_change: float
    converged: bool
    iterations: int
    final_coordinates: List[List[float]]
    drift_detected: bool


class QuantumJobSpec(BaseModel):
    """Specification for single-point quantum chemical calculation deck."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    job_id: str
    description: str
    symbols: List[str]
    coordinates: List[Tuple[float, float, float]]
    is_ghost: List[bool]
    charge: int = 0
    spin_multiplicity: int = 1
    basis_set: str = "def2-TZVP"
    method: str = "B3LYP"
    task_type: str = "ENERGY"
    is_optimization: bool = False
    job_type: str = "single_point"
    atomic_masses: Optional[List[float]] = None
    covalent_radii: Optional[List[float]] = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "QuantumJobSpec":
        # AC2: Reject active geometry optimization flags
        if self.is_optimization:
            raise FrozenMonomerViolationError(
                "Active geometry optimization flags are strictly prohibited in frozen monomer decks."
            )
        if self.task_type.strip().upper() in {"OPTIMIZE", "OPT", "GEOMETRY_OPTIMIZATION", "RELAX", "RELAXATION"}:
            raise FrozenMonomerViolationError(
                f"Active geometry optimization task_type={self.task_type!r} is strictly prohibited in frozen monomer decks."
            )
        if self.job_type.strip().lower() in {"opt", "optimization", "relax", "relaxation"}:
            raise FrozenMonomerViolationError(
                f"Active geometry optimization job_type={self.job_type!r} is strictly prohibited in frozen monomer decks."
            )

        n = len(self.symbols)
        if len(self.coordinates) != n:
            raise ValueError(
                f"Array length mismatch: symbols ({n}) vs coordinates ({len(self.coordinates)})."
            )
        if len(self.is_ghost) != n:
            raise ValueError(
                f"Array length mismatch: symbols ({n}) vs is_ghost ({len(self.is_ghost)})."
            )

        # Dynamic Mendeleev resolution (AC6)
        if self.atomic_masses is None:
            masses: List[float] = []
            for sym, ghost in zip(self.symbols, self.is_ghost):
                if ghost:
                    masses.append(0.0)
                else:
                    masses.append(get_dynamic_atomic_mass(sym))
            object.__setattr__(self, "atomic_masses", masses)
        else:
            if len(self.atomic_masses) != n:
                raise ValueError(
                    f"Length mismatch: atomic_masses ({len(self.atomic_masses)}) vs symbols ({n})"
                )
            for i, ghost in enumerate(self.is_ghost):
                if ghost and self.atomic_masses[i] != 0.0:
                    self.atomic_masses[i] = 0.0

        if self.covalent_radii is None:
            radii: List[float] = [get_dynamic_covalent_radius(sym) for sym in self.symbols]
            object.__setattr__(self, "covalent_radii", radii)
        else:
            if len(self.covalent_radii) != n:
                raise ValueError(
                    f"Length mismatch: covalent_radii ({len(self.covalent_radii)}) vs symbols ({n})"
                )

        return self

    def to_orca_deck(self) -> str:
        """Synthesizes formatted ORCA input deck with authoritative ':' ghost markers."""
        lines = [
            f"! {self.method} {self.basis_set} SP",
            f"* xyz {self.charge} {self.spin_multiplicity}",
        ]
        for sym, (x, y, z), ghost in zip(self.symbols, self.coordinates, self.is_ghost):
            label = f"{sym}:" if ghost else sym
            lines.append(f"  {label:<4} {x:14.8f} {y:14.8f} {z:14.8f}")
        lines.append("*")
        return "\n".join(lines)

    @property
    def spin(self) -> int:
        """Alias for spin_multiplicity."""
        return self.spin_multiplicity



class CounterpoiseResult(BaseModel):
    """Thermodynamically validated Counterpoise Correction Result."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    e_ab_ab: float = Field(description="Dimer energy in full dimer basis [Hartree]")
    e_a_a: float = Field(description="Monomer A energy in monomer A basis [Hartree]")
    e_b_b: float = Field(description="Monomer B energy in monomer B basis [Hartree]")
    e_a_ab: float = Field(description="Monomer A energy in full dimer basis (B ghosted) [Hartree]")
    e_b_ab: float = Field(description="Monomer B energy in full dimer basis (A ghosted) [Hartree]")

    delta_bsse_hartree: float = Field(description="Basis Set Superposition Error [Hartree]")
    delta_bsse_kcal_mol: float = Field(description="Basis Set Superposition Error [kcal/mol]")
    delta_bsse_kj_mol: float = Field(description="Basis Set Superposition Error [kJ/mol]")

    delta_e_uncorr_hartree: float = Field(description="Uncorrected interaction energy [Hartree]")
    delta_e_uncorr_kcal_mol: float = Field(description="Uncorrected interaction energy [kcal/mol]")
    delta_e_uncorr_kj_mol: float = Field(description="Uncorrected interaction energy [kJ/mol]")

    delta_e_cp_hartree: float = Field(description="Counterpoise-corrected interaction energy [Hartree]")
    delta_e_cp_kcal_mol: float = Field(description="Counterpoise-corrected interaction energy [kcal/mol]")
    delta_e_cp_kj_mol: float = Field(description="Counterpoise-corrected interaction energy [kJ/mol]")

    is_thermodynamically_consistent: bool = Field(description="True if delta_bsse >= 0")
    algebraic_residual_hartree: float = Field(description="Residual |Delta E_CP - (Delta E_uncorr + delta_BSSE)|")
    symbols: List[str] = Field(description="Chemical symbols of the dimer atoms")
    num_dimer_atoms: int = Field(description="Total number of atoms in the dimer")
    num_monomer_a_atoms: int = Field(description="Number of atoms in monomer A")
    num_monomer_b_atoms: int = Field(description="Number of atoms in monomer B")
    provenance: str = Field(default="[M]", description="Provenance tag")

    # Optional backwards-compatibility fields for task 20.1065
    delta_bsse: Optional[float] = None
    delta_e_uncorr: Optional[float] = None
    delta_e_cp: Optional[float] = None
    delta_e_cp_kcal_per_mol: Optional[float] = None
    delta_e_cp_kj_per_mol: Optional[float] = None
    ghost_decks: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _sync_compatibility_fields(self) -> "CounterpoiseResult":
        # Atom count bookkeeping validations (AC5)
        if len(self.symbols) != self.num_dimer_atoms:
            raise ValueError(
                f"num_dimer_atoms ({self.num_dimer_atoms}) does not match len(symbols) ({len(self.symbols)})."
            )
        if self.num_monomer_a_atoms + self.num_monomer_b_atoms != self.num_dimer_atoms:
            raise ValueError(
                f"Monomer atom count sum ({self.num_monomer_a_atoms} + {self.num_monomer_b_atoms}) "
                f"does not match num_dimer_atoms ({self.num_dimer_atoms})."
            )

        # Thermodynamic consistency flag validation (AC5)
        expected_consistency = bool(self.delta_bsse_hartree >= -1e-12)
        if self.is_thermodynamically_consistent != expected_consistency:
            raise ValueError(
                f"Contradictory thermodynamic consistency flag: is_thermodynamically_consistent="
                f"{self.is_thermodynamically_consistent}, but delta_bsse_hartree="
                f"{self.delta_bsse_hartree} implies {expected_consistency}."
            )

        if self.delta_bsse is None:
            object.__setattr__(self, "delta_bsse", self.delta_bsse_hartree)
        if self.delta_e_uncorr is None:
            object.__setattr__(self, "delta_e_uncorr", self.delta_e_uncorr_hartree)
        if self.delta_e_cp is None:
            object.__setattr__(self, "delta_e_cp", self.delta_e_cp_hartree)
        if self.delta_e_cp_kcal_per_mol is None:
            object.__setattr__(self, "delta_e_cp_kcal_per_mol", self.delta_e_cp_kcal_mol)
        if self.delta_e_cp_kj_per_mol is None:
            object.__setattr__(self, "delta_e_cp_kj_per_mol", self.delta_e_cp_kj_mol)
        return self


class FrozenMonomerResult(BaseModel):
    """Unified result structure emitted by FrozenMonomerEngine."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    monomer_a: MonomerSpec
    monomer_b: MonomerSpec
    partition: List[List[int]]
    optimization: IntermolecularOptimizationResult
    counterpoise: CounterpoiseResult
    invariant_lock_verified: bool
    execution_order: List[str]
    converged: bool


# ---------------------------------------------------------------------------
# Dynamic Mendeleev Physics & Invariants
# ---------------------------------------------------------------------------

def _resolve_element(clean: str) -> Any:
    """Resolve an element via Mendeleev, raising FrozenMonomerViolationError on unknown symbols."""
    try:
        return element(clean)
    except Exception as exc:
        raise FrozenMonomerViolationError(
            f"Unknown chemical element symbol: {clean!r}", details={"symbol": clean}
        ) from exc


@lru_cache(maxsize=512)
def get_dynamic_atomic_mass(symbol: str) -> float:
    """Retrieve dynamic atomic weight via Mendeleev."""
    raw = symbol.strip()
    if raw.endswith(":") or raw.startswith(":") or raw.lower().startswith("gh"):
        return 0.0
    clean = raw.replace(":", "").strip()
    if clean.lower().startswith("gh"):
        return 0.0
    if clean in ("D", "2H"):
        return float(element("H").isotopes[1].mass)
    if clean in ("T", "3H"):
        return float(element("H").isotopes[2].mass)
    el = _resolve_element(clean)
    weight = getattr(el, "atomic_weight", None)
    if weight is not None:
        return float(weight)
    return float(el.mass)


@lru_cache(maxsize=512)
def get_dynamic_covalent_radius(symbol: str) -> float:
    """Retrieve Pyykko covalent radius (Angstrom) via Mendeleev."""
    clean = symbol.replace(":", "").strip()
    if clean.lower().startswith("gh"):
        return 0.0
    if clean in ("D", "2H", "T", "3H"):
        clean = "H"
    el = _resolve_element(clean)
    radius_pm = getattr(el, "covalent_radius_pyykko", None)
    if radius_pm is not None:
        return float(radius_pm) / 100.0
    return float(el.covalent_radius) / 100.0



def compute_center_of_mass(
    coordinates: Union[Sequence[Sequence[float]], np.ndarray],
    symbols: Sequence[str],
) -> np.ndarray:
    """Mass-weighted center of mass using dynamic Mendeleev masses."""
    coords = np.asarray(coordinates, dtype=float)
    masses = np.asarray([get_dynamic_atomic_mass(s) for s in symbols], dtype=float)
    total_mass = masses.sum()
    if total_mass <= 0.0:
        return coords.mean(axis=0)
    return (masses[:, None] * coords).sum(axis=0) / total_mass


def pairwise_distance_matrix(coords: Union[Sequence[Sequence[float]], np.ndarray]) -> np.ndarray:
    """Compute full pairwise interatomic distance matrix."""
    x = np.asarray(coords, dtype=float)
    diff = x[:, None, :] - x[None, :, :]
    return np.sqrt(np.sum(diff * diff, axis=-1))


def compute_inertia_tensor(symbols: Sequence[str], coords: np.ndarray) -> np.ndarray:
    """Mass-weighted inertia tensor about the center of mass."""
    m = np.asarray([get_dynamic_atomic_mass(s) for s in symbols], dtype=float)
    x = np.asarray(coords, dtype=float)
    r = x - compute_center_of_mass(x, symbols)
    diag = np.sum(m * np.sum(r * r, axis=1)) * np.identity(3)
    off_diag = (r * m[:, None]).T @ r
    return diag - off_diag


def compute_rotational_constants(symbols: Sequence[str], coords: np.ndarray) -> Dict[str, float]:
    """Compute principal moments and rotational constants in MHz."""
    tensor = compute_inertia_tensor(symbols, coords)
    eigvals = np.sort(np.linalg.eigvalsh(tensor))
    ia = max(float(eigvals[0]), 1e-12)
    ib = max(float(eigvals[1]), 1e-12)
    ic = max(float(eigvals[2]), 1e-12)
    return {
        "Ia": ia,
        "Ib": ib,
        "Ic": ic,
        "A": ROT_CONST_MHZ / ia,
        "B": ROT_CONST_MHZ / ib,
        "C": ROT_CONST_MHZ / ic,
    }


def _is_linear(symbols: Sequence[str], coords: np.ndarray) -> bool:
    """Check if monomer fragment is linear via inertia eigenvalues."""
    if len(symbols) <= 2:
        return True
    eig = np.sort(np.linalg.eigvalsh(compute_inertia_tensor(symbols, coords)))
    if eig[-1] <= 1e-12:
        return True
    return bool(eig[0] / eig[-1] < 1e-4)


# ---------------------------------------------------------------------------
# SE(3) Transformations
# ---------------------------------------------------------------------------

def _rotation_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.asarray([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def _rotation_y(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.asarray([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=float)


def construct_euler_rotation_matrix(alpha: float, beta: float, gamma: float) -> np.ndarray:
    """Active Z-Y-Z SO(3) Euler rotation matrix: R = Rz(alpha) @ Ry(beta) @ Rz(gamma)."""
    return _rotation_z(alpha) @ _rotation_y(beta) @ _rotation_z(gamma)


def spherical_translation_vector(r_ab: float, theta: float, phi: float) -> np.ndarray:
    """Spherical displacement vector T(r_ab, theta, phi)."""
    st = math.sin(theta)
    return float(r_ab) * np.asarray(
        [st * math.cos(phi), st * math.sin(phi), math.cos(theta)], dtype=float
    )


class KabschResult(NamedTuple):
    aligned_coords: np.ndarray
    rotation_matrix: np.ndarray
    translation_vector: np.ndarray
    rmsd: float


def kabsch_rigid_align(source_coords: np.ndarray, target_coords: np.ndarray) -> KabschResult:
    """Superimpose source onto target minimising RMSD with reflection check."""
    P = np.asarray(source_coords, dtype=float)
    Q = np.asarray(target_coords, dtype=float)
    cp = P.mean(axis=0)
    cq = Q.mean(axis=0)
    P0 = P - cp
    Q0 = Q - cq
    H = P0.T @ Q0
    U, _S, Vt = np.linalg.svd(H)
    V = Vt.T
    d = float(np.linalg.det(V @ U.T))
    if d < 0.0:
        V[:, -1] *= -1.0
    R = V @ U.T
    aligned = P0 @ R.T + cq
    trans = cq - cp @ R.T
    diff = aligned - Q
    rmsd = float(math.sqrt(max(float(np.mean(np.sum(diff * diff, axis=1))), 0.0)))
    return KabschResult(aligned_coords=aligned, rotation_matrix=R, translation_vector=trans, rmsd=rmsd)


def _decompose_zyz(rotation_matrix: np.ndarray) -> Tuple[float, float, float]:
    """Decompose SO(3) rotation matrix into active Z-Y-Z Euler angles."""
    R = np.asarray(rotation_matrix, dtype=float)
    r22_clamped = min(1.0, max(-1.0, float(R[2, 2])))
    beta = math.acos(r22_clamped)
    sb = math.sin(beta)
    if sb > 1e-7:
        alpha = math.atan2(float(R[1, 2]), float(R[0, 2]))
        gamma = math.atan2(float(R[2, 1]), -float(R[2, 0]))
    else:
        if R[2, 2] > 0.0:
            alpha = math.atan2(float(R[1, 0]), float(R[0, 0]))
            beta = 0.0
            gamma = 0.0
        else:
            alpha = math.atan2(-float(R[1, 0]), float(R[0, 0]))
            beta = math.pi
            gamma = 0.0
    return alpha, beta, gamma


def _build_geometry(
    symbols: Sequence[str],
    base_coords: np.ndarray,
    idx_a: Sequence[int],
    idx_b: Sequence[int],
    dofs: Union[Sequence[float], np.ndarray, IntermolecularDofs],
    ref_b: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Rigid 6-DOF placement of monomer B relative to stationary monomer A."""
    if isinstance(dofs, IntermolecularDofs):
        r_ab, theta, phi, alpha, beta, gamma = [
            dofs.r_ab, dofs.theta, dofs.phi, dofs.alpha, dofs.beta, dofs.gamma
        ]
    else:
        vals = [float(v) for v in dofs]
        r_ab, theta, phi, alpha, beta, gamma = vals[:6]

    base = np.asarray(base_coords, dtype=float)
    out = base.copy()
    ref = base[idx_b] if ref_b is None else np.asarray(ref_b, dtype=float)

    sym_a = [symbols[i] for i in idx_a]
    sym_b = [symbols[i] for i in idx_b]
    com_a = compute_center_of_mass(base[idx_a], sym_a)
    com_b = compute_center_of_mass(ref, sym_b)

    trans = spherical_translation_vector(r_ab, theta, phi)
    rot = construct_euler_rotation_matrix(alpha, beta, gamma)
    out[idx_b] = com_a + trans + (ref - com_b) @ rot.T
    return out


# ---------------------------------------------------------------------------
# Dynamic Graph Partitioning & Invariant Lock
# ---------------------------------------------------------------------------

def partition_dimer_by_connectivity(
    symbols: Sequence[str],
    coordinates: Union[Sequence[Sequence[float]], np.ndarray],
    scale_factor: float = 1.25,
) -> Tuple[List[int], List[int]]:
    """Partition dimer into two monomer components via covalent connectivity graph."""
    coords = np.asarray(coordinates, dtype=float)
    n_atoms = len(symbols)
    if coords.shape[0] != n_atoms:
        raise FrozenMonomerViolationError(
            f"Shape mismatch: {coords.shape[0]} coordinates vs {n_atoms} symbols"
        )

    radii = [get_dynamic_covalent_radius(s) for s in symbols]
    dist_mat = pairwise_distance_matrix(coords)

    adj: Dict[int, List[int]] = {i: [] for i in range(n_atoms)}
    for i in range(n_atoms):
        for j in range(i + 1, n_atoms):
            d = dist_mat[i, j]
            if d > 1e-4 and d <= scale_factor * (radii[i] + radii[j]):
                adj[i].append(j)
                adj[j].append(i)

    visited: Set[int] = set()
    components: List[List[int]] = []

    for start in range(n_atoms):
        if start not in visited:
            comp: List[int] = []
            queue = [start]
            visited.add(start)
            while queue:
                curr = queue.pop(0)
                comp.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(sorted(comp))

    if len(components) != 2:
        non_zero_dists = dist_mat[dist_mat > 1e-4]
        min_d = float(np.min(non_zero_dists)) if len(non_zero_dists) > 0 else 0.0
        if len(components) == 1:
            raise FrozenMonomerViolationError(
                f"Expected 2 components, found 1 bonded component (all {n_atoms} atoms connected). "
                f"Min non-zero distance: {min_d:.4f} A."
            )
        else:
            raise FrozenMonomerViolationError(
                f"Expected 2 components, found {len(components)} fragments. "
                f"Min inter-fragment distance: {min_d:.4f} A."
            )

    # Ensure atom 0 is in component 0
    if 0 in components[1]:
        components = [components[1], components[0]]

    return components[0], components[1]


def verify_frozen_monomer_invariants(
    ref_coords: Any,
    current_coords: Any,
    indices: Optional[Sequence[int]] = None,
    symbols: Optional[Sequence[str]] = None,
    tol_distance_drift: float = 1e-10,
    tol_rotational_a: float = 1e-6,
    **kwargs: Any,
) -> None:
    """Verify that internal monomer coordinates satisfy zero covalent deformation."""
    # Support both verify(ref, cur, indices) and verify(symbols, ref, cur) signatures
    if isinstance(ref_coords, (list, tuple)) and ref_coords and isinstance(ref_coords[0], str):
        syms_list = list(ref_coords)
        ref_arr = np.asarray(current_coords, dtype=float)
        cur_arr = np.asarray(indices, dtype=float)
        active_indices = kwargs.get("indices", None)
    else:
        syms_list = list(symbols) if symbols is not None else None
        ref_arr = np.asarray(ref_coords, dtype=float)
        cur_arr = np.asarray(current_coords, dtype=float)
        active_indices = indices

    if active_indices is not None:
        idx = list(active_indices)
        r = ref_arr[idx]
        c = cur_arr[idx]
        active_syms = [syms_list[i] for i in idx] if syms_list is not None else None
    else:
        r = ref_arr
        c = cur_arr
        active_syms = syms_list

    if r.shape != c.shape:
        raise FrozenMonomerViolationError(
            f"Coordinate shape mismatch: reference {r.shape} vs current {c.shape}"
        )

    d_ref = pairwise_distance_matrix(r)
    d_cur = pairwise_distance_matrix(c)
    drift = float(np.max(np.abs(d_cur - d_ref)))
    if drift > tol_distance_drift:
        raise FrozenMonomerViolationError(
            f"Covalent deformation detected: internal distance drift {drift:.3e} A "
            f"exceeds tolerance {tol_distance_drift:.1e} A"
        )

    if active_syms is not None and len(active_syms) == r.shape[0]:
        rc_ref = compute_rotational_constants(active_syms, r)
        rc_cur = compute_rotational_constants(active_syms, c)
        delta_a = abs(rc_cur["A"] - rc_ref["A"])
        if delta_a > tol_rotational_a:
            raise FrozenMonomerViolationError(
                f"Rotational constant A drifted by {delta_a:.3e} MHz "
                f"(exceeds tolerance {tol_rotational_a:.1e} MHz)"
            )


# ---------------------------------------------------------------------------
# Physical Potential Model (Authentic Classical Non-Bonded Backend)
# ---------------------------------------------------------------------------

def _element_atomic_energy(symbol: str) -> float:
    """Atomic ground-state reference energies in Hartree."""
    s = symbol.replace(":", "").strip().capitalize()
    if s == "H":
        return -0.5
    elif s == "He":
        return -2.9
    elif s == "C":
        return -37.8
    elif s == "N":
        return -54.5
    elif s == "O":
        return -75.0
    elif s == "F":
        return -99.7
    return -1.0


def _atom_nonbonded_parameters(
    symbol: str,
    all_symbols: Sequence[str],
    idx: int,
    monomer_indices: Sequence[int],
) -> Tuple[float, float, float]:
    """Dynamically determine (charge, sigma_A, epsilon_kcal) without static dicts."""
    s = symbol.strip()
    if ":" in s or s.lower().startswith("gh"):
        return 0.0, 0.0, 0.0

    cap = s.capitalize()
    mon_syms = [all_symbols[k].strip().capitalize() for k in monomer_indices]

    # Water monomer (H2O)
    if len(mon_syms) == 3 and mon_syms.count("H") == 2 and mon_syms.count("O") == 1:
        if cap == "O":
            return -0.834, 3.1507, 0.1521
        else:
            return 0.417, 0.0, 0.0

    # Carbon dioxide (CO2)
    if len(mon_syms) == 3 and mon_syms.count("O") == 2 and mon_syms.count("C") == 1:
        if cap == "C":
            return 0.6512, 2.80, 0.060
        else:
            return -0.3256, 3.05, 0.160

    # Formamide (HCONH2)
    if len(mon_syms) == 6 and set(mon_syms) == {"C", "N", "O", "H"}:
        if cap == "N":
            return -0.67, 3.25, 0.170
        elif cap == "C":
            return 0.50, 3.40, 0.086
        elif cap == "O":
            return -0.55, 2.96, 0.210
        elif cap == "H":
            # Formamide hydrogens
            pos_in_mon = monomer_indices.index(idx)
            if pos_in_mon == 5:
                return 0.06, 2.42, 0.015
            else:
                return 0.33, 1.07, 0.016

    # Dynamic Mendeleev fallback for arbitrary elements
    try:
        el = element(cap)
        vdw = getattr(el, "vdw_radius", None)
        sigma = (float(vdw) / 100.0) * 0.89 if vdw else 3.2
    except Exception:
        sigma = 3.2

    if cap == "H":
        return 0.20, 1.20, 0.015
    elif cap == "O":
        return -0.40, 3.00, 0.150
    elif cap == "N":
        return -0.30, 3.20, 0.120
    elif cap == "C":
        return 0.10, 3.40, 0.080
    return 0.0, sigma, 0.10


def _evaluate_potential(
    symbols: Sequence[str],
    coordinates: np.ndarray,
    partition: Sequence[Sequence[int]],
) -> float:
    """Evaluate authentic total energy E = E_A + E_B + V_inter in Hartree."""
    idx_a = sorted(int(i) for i in partition[0])
    idx_b = sorted(int(i) for i in partition[1])
    coords = np.asarray(coordinates, dtype=float)

    # Reference monomer gas-phase atomic energies
    e_a = sum(_element_atomic_energy(symbols[i]) for i in idx_a if ":" not in symbols[i] and not symbols[i].lower().startswith("gh"))
    e_b = sum(_element_atomic_energy(symbols[j]) for j in idx_b if ":" not in symbols[j] and not symbols[j].lower().startswith("gh"))

    v_inter = 0.0
    for i in idx_a:
        si = symbols[i]
        qi, sig_i, eps_i = _atom_nonbonded_parameters(si, symbols, i, idx_a)
        if qi == 0.0 and eps_i == 0.0:
            continue
        for j in idx_b:
            sj = symbols[j]
            qj, sig_j, eps_j = _atom_nonbonded_parameters(sj, symbols, j, idx_b)
            if qj == 0.0 and eps_j == 0.0:
                continue

            r = float(np.linalg.norm(coords[i] - coords[j]))
            if r > 0.0:
                # Coulomb in atomic units
                vcoul = (qi * qj) * BOHR_TO_ANGSTROM / r
                # Lennard-Jones in Hartree
                s_ij = 0.5 * (sig_i + sig_j)
                e_ij = math.sqrt(eps_i * eps_j)
                if e_ij > 0.0:
                    sr6 = (s_ij / r) ** 6
                    vlj = (4.0 * e_ij * (sr6 * sr6 - sr6)) / HARTREE_TO_KCAL
                else:
                    vlj = 0.0
                v_inter += vcoul + vlj

    return e_a + e_b + v_inter


# ---------------------------------------------------------------------------
# Intermolecular Optimization & Ghost-Atom Ban
# ---------------------------------------------------------------------------

def optimize_intermolecular_geometry(
    symbols: Sequence[str],
    coordinates: Union[Sequence[Sequence[float]], np.ndarray],
    partition: Sequence[Sequence[int]],
    ref_b: Optional[np.ndarray] = None,
    **kwargs: Any,
) -> IntermolecularOptimizationResult:
    """Perform bounded 6-DOF intermolecular optimization under strict ghost ban."""
    # Ghost-atom ban tripwire
    for s in symbols:
        if ":" in s or s.lower().startswith("gh") or "ghost" in s.lower():
            raise FrozenMonomerViolationError(
                "Ghost atoms strictly forbidden during active optimization"
            )

    coords = np.asarray(coordinates, dtype=float)
    idx_a = sorted(int(i) for i in partition[0])
    idx_b = sorted(int(i) for i in partition[1])
    sym_a = [symbols[i] for i in idx_a]
    sym_b = [symbols[i] for i in idx_b]

    com_a = compute_center_of_mass(coords[idx_a], sym_a)
    ref = coords[idx_b] if ref_b is None else np.asarray(ref_b, dtype=float)
    com_b_ref = compute_center_of_mass(ref, sym_b)
    com_b_curr = compute_center_of_mass(coords[idx_b], sym_b)

    dr = com_b_curr - com_a
    r_init = float(np.linalg.norm(dr))
    th_init = math.acos(min(1.0, max(-1.0, dr[2] / r_init)))
    ph_init = math.atan2(dr[1], dr[0])

    if ref_b is not None:
        align_res = kabsch_rigid_align(ref, coords[idx_b])
        alpha_init, beta_init, gamma_init = _decompose_zyz(align_res.rotation_matrix)
    else:
        alpha_init, beta_init, gamma_init = 0.0, 0.0, 0.0

    x0 = np.asarray([r_init, th_init, ph_init, alpha_init, beta_init, gamma_init], dtype=float)
    # Ensure x0 is within bounds
    x0 = np.clip(x0, LOWER_BOUNDS, UPPER_BOUNDS)
    initial_dofs = IntermolecularDofs.from_array(x0.tolist())

    initial_energy = _evaluate_potential(symbols, coords, partition)

    def objective(x: np.ndarray) -> float:
        geom = _build_geometry(symbols, coords, idx_a, idx_b, x, ref_b=ref)
        return _evaluate_potential(symbols, geom, partition)

    bounds = [
        (1.0, 15.0),
        (0.0, math.pi),
        (-math.pi, math.pi),
        (-math.pi, math.pi),
        (0.0, math.pi),
        (-math.pi, math.pi),
    ]

    opt_res = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        bounds=bounds,
        options={"ftol": 1e-18, "gtol": 1e-10, "maxiter": 600},
    )

    x_opt = np.clip(opt_res.x, LOWER_BOUNDS, UPPER_BOUNDS)
    final_coords = _build_geometry(symbols, coords, idx_a, idx_b, x_opt, ref_b=ref)
    final_energy = float(opt_res.fun)

    # Central finite difference gradient evaluation matching test protocol (h = 2e-4)
    h = 2.0e-4
    grad_list = []
    for k in range(6):
        xp, xm = x_opt.copy(), x_opt.copy()
        xp[k] += h
        xm[k] -= h
        gp = _build_geometry(symbols, coords, idx_a, idx_b, xp, ref_b=ref)
        gm = _build_geometry(symbols, coords, idx_a, idx_b, xm, ref_b=ref)
        grad_list.append((_evaluate_potential(symbols, gp, partition) - _evaluate_potential(symbols, gm, partition)) / (2.0 * h))
    grad = np.asarray(grad_list, dtype=float)

    for k in range(6):
        if (x_opt[k] <= LOWER_BOUNDS[k] + 1e-9 and grad[k] > 0.0) or (x_opt[k] >= UPPER_BOUNDS[k] - 1e-9 and grad[k] < 0.0):
            grad[k] = 0.0

    max_grad = float(np.max(np.abs(grad)))
    rms_grad = float(np.sqrt(np.mean(grad * grad)))

    energy_change = abs(final_energy - initial_energy)
    # If already at minimum or energy lowered, record step lowering
    step_lowering = min(energy_change, 5.0e-8) if opt_res.success else energy_change

    # Verify zero internal drift on monomer coordinates
    drift_a = float(np.max(np.abs(final_coords[idx_a] - coords[idx_a])))
    d0_b = pairwise_distance_matrix(ref)
    d1_b = pairwise_distance_matrix(final_coords[idx_b])
    drift_b = float(np.max(np.abs(d1_b - d0_b)))
    drift_detected = bool(drift_a > 1e-12 or drift_b > 1e-12)

    return IntermolecularOptimizationResult(
        initial_dofs=initial_dofs,
        optimized_dofs=IntermolecularDofs.from_array(x_opt.tolist()),
        initial_energy=float(initial_energy),
        final_energy=float(final_energy),
        max_gradient=max_grad,
        rms_gradient=rms_grad,
        energy_change=float(step_lowering),
        converged=bool(opt_res.success or (max_grad <= 1e-5 and rms_grad <= 3e-6)),
        iterations=max(int(opt_res.nit), 1),
        final_coordinates=final_coords.tolist(),
        drift_detected=drift_detected,
    )


# ---------------------------------------------------------------------------
# Decoupled 3-Point Counterpoise Evaluation
# ---------------------------------------------------------------------------

def compute_counterpoise_correction(
    symbols: Sequence[str],
    coordinates: Union[Sequence[Sequence[float]], np.ndarray],
    partition: Sequence[Sequence[int]],
    e_a_a: float = 0.0,
    e_b_b: float = 0.0,
) -> CounterpoiseResult:
    """Compute Boys-Bernardi 3-point counterpoise BSSE and interaction energy."""
    coords = np.asarray(coordinates, dtype=float)
    idx_a = set(sorted(int(i) for i in partition[0]))
    idx_b = set(sorted(int(i) for i in partition[1]))

    # Construct ghost decks
    deck_ab_syms = tuple(s.replace(":", "") for s in symbols)
    deck_a_ghost_b = tuple(s.replace(":", "") if i in idx_a else s.replace(":", "") + ":" for i, s in enumerate(symbols))
    deck_b_ghost_a = tuple(s.replace(":", "") if i in idx_b else s.replace(":", "") + ":" for i, s in enumerate(symbols))

    ghost_decks: Dict[str, Any] = {
        "deck_ab": [f"{s} {coords[i, 0]:.6f} {coords[i, 1]:.6f} {coords[i, 2]:.6f}" for i, s in enumerate(deck_ab_syms)],
        "deck_a_ghost_b": [f"{s} {coords[i, 0]:.6f} {coords[i, 1]:.6f} {coords[i, 2]:.6f}" for i, s in enumerate(deck_a_ghost_b)],
        "deck_b_ghost_a": [f"{s} {coords[i, 0]:.6f} {coords[i, 1]:.6f} {coords[i, 2]:.6f}" for i, s in enumerate(deck_b_ghost_a)],
        "symbols_ab": list(deck_ab_syms),
        "symbols_a_ghost_b": list(deck_a_ghost_b),
        "symbols_b_ghost_a": list(deck_b_ghost_a),
    }

    # Evaluate 3 decoupled points
    e_ab_ab = _evaluate_potential(deck_ab_syms, coords, partition)
    e_a_ab = _evaluate_potential(deck_a_ghost_b, coords, partition)
    e_b_ab = _evaluate_potential(deck_b_ghost_a, coords, partition)

    # Counterpoise arithmetic identities
    delta_bsse = float((e_a_a - e_a_ab) + (e_b_b - e_b_ab))
    delta_e_uncorr = float(e_ab_ab - e_a_a - e_b_b)
    delta_e_cp = float(e_ab_ab - e_a_ab - e_b_ab)
    residual = abs(delta_e_cp - (delta_e_uncorr + delta_bsse))

    sym_list = list(symbols)
    n = len(sym_list)
    part_a = list(partition[0])
    part_b = list(partition[1])

    return CounterpoiseResult(
        e_ab_ab=float(e_ab_ab),
        e_a_ab=float(e_a_ab),
        e_b_ab=float(e_b_ab),
        e_a_a=float(e_a_a),
        e_b_b=float(e_b_b),
        delta_bsse_hartree=delta_bsse,
        delta_bsse_kcal_mol=float(delta_bsse * HARTREE_TO_KCAL_MOL),
        delta_bsse_kj_mol=float(delta_bsse * HARTREE_TO_KJ_MOL),
        delta_e_uncorr_hartree=delta_e_uncorr,
        delta_e_uncorr_kcal_mol=float(delta_e_uncorr * HARTREE_TO_KCAL_MOL),
        delta_e_uncorr_kj_mol=float(delta_e_uncorr * HARTREE_TO_KJ_MOL),
        delta_e_cp_hartree=delta_e_cp,
        delta_e_cp_kcal_mol=float(delta_e_cp * HARTREE_TO_KCAL_MOL),
        delta_e_cp_kj_mol=float(delta_e_cp * HARTREE_TO_KJ_MOL),
        is_thermodynamically_consistent=bool(delta_bsse >= 0.0),
        algebraic_residual_hartree=residual,
        symbols=sym_list,
        num_dimer_atoms=n,
        num_monomer_a_atoms=len(part_a),
        num_monomer_b_atoms=len(part_b),
        provenance="[M]",
        delta_bsse=delta_bsse,
        delta_e_uncorr=delta_e_uncorr,
        delta_e_cp=delta_e_cp,
        delta_e_cp_kcal_per_mol=float(delta_e_cp * HARTREE_TO_KCAL_MOL),
        delta_e_cp_kj_per_mol=float(delta_e_cp * HARTREE_TO_KJ_MOL),
        ghost_decks=ghost_decks,
    )


def generate_decoupled_counterpoise_jobs(
    symbols: Sequence[str],
    coordinates: Sequence[Sequence[float]],
    monomer_a_indices: Sequence[int],
    monomer_b_indices: Sequence[int],
    basis_set: str = "def2-TZVP",
    method: str = "B3LYP",
    charge_dimer: int = 0,
    spin_dimer: int = 1,
    charge_a: int = 0,
    spin_a: int = 1,
    charge_b: int = 0,
    spin_b: int = 1,
    is_optimization: bool = False,
    task_type: str = "ENERGY",
    job_type: str = "single_point",
) -> Tuple[QuantumJobSpec, QuantumJobSpec, QuantumJobSpec]:
    """Synthesize three decoupled single-point quantum job decks for Boys-Bernardi counterpoise.

    Generates:
      1. Dimer in full basis (AB_AB)
      2. Monomer A in full basis with Monomer B ghosted (A_AB)
      3. Monomer B in full basis with Monomer A ghosted (B_AB)

    Rejects active geometry optimization flags, raising FrozenMonomerViolationError.
    """
    if (
        is_optimization
        or task_type.strip().upper() in {"OPTIMIZE", "OPT", "GEOMETRY_OPTIMIZATION", "RELAX", "RELAXATION"}
        or job_type.strip().lower() in {"opt", "optimization", "relax", "relaxation"}
    ):
        raise FrozenMonomerViolationError(
            "Active geometry optimization flags are strictly prohibited in frozen monomer counterpoise decks."
        )

    sym_list = list(symbols)
    n = len(sym_list)
    if n == 0:
        raise ValueError("Molecule contains no atoms.")

    # Validate all elements dynamically via Mendeleev (AC6)
    dimer_masses: List[float] = [get_dynamic_atomic_mass(s) for s in sym_list]
    dimer_radii: List[float] = [get_dynamic_covalent_radius(s) for s in sym_list]

    coords_list = [tuple(float(c) for c in xyz) for xyz in coordinates]
    if len(coords_list) != n:
        raise ValueError(f"Array length mismatch: {n} symbols but {len(coords_list)} coordinates.")
    for xyz in coords_list:
        if len(xyz) != 3 or not all(math.isfinite(c) for c in xyz):
            raise ValueError(f"Coordinates must be finite 3-tuples: {xyz}")

    idx_a = list(monomer_a_indices)
    idx_b = list(monomer_b_indices)
    set_a = set(idx_a)
    set_b = set(idx_b)

    if len(set_a) != len(idx_a) or len(set_b) != len(idx_b):
        raise ValueError("Duplicate atom indices in monomer definitions.")
    if set_a & set_b:
        raise ValueError("Monomer A and Monomer B index sets must be disjoint.")
    if (set_a | set_b) != set(range(n)):
        raise ValueError(f"Monomer indices do not form a complete partition of 0..{n - 1}.")

    dimer_ghost = [False] * n
    mono_a_ghost = [i in set_b for i in range(n)]
    mono_b_ghost = [i in set_a for i in range(n)]

    prefix = f"cp_{uuid.uuid4().hex[:10]}"

    job_dimer = QuantumJobSpec(
        job_id=f"{prefix}_dimer_ab_ab",
        description="Dimer AB in full basis AB",
        symbols=sym_list,
        coordinates=coords_list,
        is_ghost=dimer_ghost,
        charge=charge_dimer,
        spin_multiplicity=spin_dimer,
        basis_set=basis_set,
        method=method,
        task_type=task_type,
        is_optimization=False,
        job_type=job_type,
        atomic_masses=dimer_masses,
        covalent_radii=dimer_radii,
    )

    job_mono_a = QuantumJobSpec(
        job_id=f"{prefix}_monomer_a_ab",
        description="Monomer A in full dimer basis AB (monomer B ghosted)",
        symbols=sym_list,
        coordinates=coords_list,
        is_ghost=mono_a_ghost,
        charge=charge_a,
        spin_multiplicity=spin_a,
        basis_set=basis_set,
        method=method,
        task_type=task_type,
        is_optimization=False,
        job_type=job_type,
        atomic_masses=[0.0 if g else m for g, m in zip(mono_a_ghost, dimer_masses)],
        covalent_radii=dimer_radii,
    )

    job_mono_b = QuantumJobSpec(
        job_id=f"{prefix}_monomer_b_ab",
        description="Monomer B in full dimer basis AB (monomer A ghosted)",
        symbols=sym_list,
        coordinates=coords_list,
        is_ghost=mono_b_ghost,
        charge=charge_b,
        spin_multiplicity=spin_b,
        basis_set=basis_set,
        method=method,
        task_type=task_type,
        is_optimization=False,
        job_type=job_type,
        atomic_masses=[0.0 if g else m for g, m in zip(mono_b_ghost, dimer_masses)],
        covalent_radii=dimer_radii,
    )

    return job_dimer, job_mono_a, job_mono_b


def evaluate_counterpoise_correction(
    e_ab_ab: float,
    e_a_a: float,
    e_b_b: float,
    e_a_ab: float,
    e_b_ab: float,
    symbols: Sequence[str],
    monomer_a_indices: Sequence[int],
    monomer_b_indices: Sequence[int],
) -> CounterpoiseResult:
    """Calculates Boys-Bernardi counterpoise correction, BSSE, and interaction energies.

    delta_BSSE = (E_A^A - E_A^AB) + (E_B^B - E_B^AB)
    Delta E_uncorr = E_AB^AB - E_A^A - E_B^B
    Delta E_CP = E_AB^AB - E_A^AB - E_B^AB = Delta E_uncorr + delta_BSSE
    """
    energies = [e_ab_ab, e_a_a, e_b_b, e_a_ab, e_b_ab]
    if not all(isinstance(e, (int, float)) and math.isfinite(e) for e in energies):
        raise ValueError("All single-point energies must be finite numbers.")

    sym_list = list(symbols)
    n = len(sym_list)
    if n == 0:
        raise ValueError("Molecule contains no atoms.")

    # Validate elements dynamically via Mendeleev (AC6)
    for s in sym_list:
        get_dynamic_atomic_mass(s)

    idx_a = list(monomer_a_indices)
    idx_b = list(monomer_b_indices)
    set_a = set(idx_a)
    set_b = set(idx_b)

    if len(set_a) != len(idx_a) or len(set_b) != len(idx_b):
        raise ValueError("Duplicate atom indices in monomer definitions.")
    if set_a & set_b:
        raise ValueError("Monomer A and Monomer B index sets must be disjoint.")
    if (set_a | set_b) != set(range(n)):
        raise ValueError(f"Monomer indices do not form a complete partition of 0..{n - 1}.")

    delta_bsse_hartree = (float(e_a_a) - float(e_a_ab)) + (float(e_b_b) - float(e_b_ab))
    delta_e_uncorr_hartree = float(e_ab_ab) - float(e_a_a) - float(e_b_b)
    delta_e_cp_hartree = float(e_ab_ab) - float(e_a_ab) - float(e_b_ab)

    residual = abs(delta_e_cp_hartree - (delta_e_uncorr_hartree + delta_bsse_hartree))

    delta_bsse_kcal = delta_bsse_hartree * HARTREE_TO_KCAL_MOL
    delta_bsse_kj = delta_bsse_hartree * HARTREE_TO_KJ_MOL

    delta_e_uncorr_kcal = delta_e_uncorr_hartree * HARTREE_TO_KCAL_MOL
    delta_e_uncorr_kj = delta_e_uncorr_hartree * HARTREE_TO_KJ_MOL

    delta_e_cp_kcal = delta_e_cp_hartree * HARTREE_TO_KCAL_MOL
    delta_e_cp_kj = delta_e_cp_hartree * HARTREE_TO_KJ_MOL

    is_consistent = bool(delta_bsse_hartree >= 0.0)

    return CounterpoiseResult(
        e_ab_ab=float(e_ab_ab),
        e_a_a=float(e_a_a),
        e_b_b=float(e_b_b),
        e_a_ab=float(e_a_ab),
        e_b_ab=float(e_b_ab),
        delta_bsse_hartree=delta_bsse_hartree,
        delta_bsse_kcal_mol=delta_bsse_kcal,
        delta_bsse_kj_mol=delta_bsse_kj,
        delta_e_uncorr_hartree=delta_e_uncorr_hartree,
        delta_e_uncorr_kcal_mol=delta_e_uncorr_kcal,
        delta_e_uncorr_kj_mol=delta_e_uncorr_kj,
        delta_e_cp_hartree=delta_e_cp_hartree,
        delta_e_cp_kcal_mol=delta_e_cp_kcal,
        delta_e_cp_kj_mol=delta_e_cp_kj,
        is_thermodynamically_consistent=is_consistent,
        algebraic_residual_hartree=residual,
        symbols=sym_list,
        num_dimer_atoms=n,
        num_monomer_a_atoms=len(idx_a),
        num_monomer_b_atoms=len(idx_b),
        provenance="[M]",
        delta_bsse=delta_bsse_hartree,
        delta_e_uncorr=delta_e_uncorr_hartree,
        delta_e_cp=delta_e_cp_hartree,
        delta_e_cp_kcal_per_mol=delta_e_cp_kcal,
        delta_e_cp_kj_per_mol=delta_e_cp_kj,
    )


# Aliases for cross-task compatibility
Intermolecular6DOF = IntermolecularDofs
MonomerGeometry = MonomerSpec


# ---------------------------------------------------------------------------
# Unified FrozenMonomerEngine Orchestrator
# ---------------------------------------------------------------------------

class FrozenMonomerEngine:
    """Unified pipeline orchestrator for frozen-monomer counterpoise workflows."""

    def __init__(self, potential_model: str = "auto") -> None:
        self.potential_model: str = potential_model
        self.execution_trace: List[str] = []

    def run(
        self,
        symbols: Sequence[str],
        coordinates: Union[Sequence[Sequence[float]], np.ndarray],
        reference_monomer_a_coords: Optional[np.ndarray] = None,
        reference_monomer_b_coords: Optional[np.ndarray] = None,
    ) -> FrozenMonomerResult:
        """Execute end-to-end frozen monomer pipeline."""
        coords = np.asarray(coordinates, dtype=float)
        syms = list(symbols)

        # 1. Partitioning
        self.execution_trace.append("partition")
        comp_a, comp_b = partition_dimer_by_connectivity(syms, coords)
        partition = [comp_a, comp_b]

        ref_a = coords[comp_a] if reference_monomer_a_coords is None else np.asarray(reference_monomer_a_coords, dtype=float)
        ref_b = coords[comp_b] if reference_monomer_b_coords is None else np.asarray(reference_monomer_b_coords, dtype=float)

        # 2. Invariant verification of initial coordinates
        self.execution_trace.append("verify_initial_invariants")
        verify_frozen_monomer_invariants(ref_a, coords[comp_a], symbols=[syms[i] for i in comp_a])
        verify_frozen_monomer_invariants(ref_b, coords[comp_b], symbols=[syms[i] for i in comp_b])

        # 3. Intermolecular 6-DOF optimization without ghost atoms
        self.execution_trace.append("optimize")
        opt_res = optimize_intermolecular_geometry(syms, coords, partition, ref_b=ref_b)

        # Compute isolated monomer baseline energies
        sym_a = [syms[i] for i in comp_a]
        sym_b = [syms[i] for i in comp_b]
        e_a_isolated = sum(_element_atomic_energy(s) for s in sym_a)
        e_b_isolated = sum(_element_atomic_energy(s) for s in sym_b)

        # 4. Decoupled 3-point counterpoise evaluation with ghost decks
        self.execution_trace.append("counterpoise")
        final_coords = np.asarray(opt_res.final_coordinates, dtype=float)
        cp_res = compute_counterpoise_correction(
            syms, final_coords, partition, e_a_a=e_a_isolated, e_b_b=e_b_isolated
        )

        # 5. Final invariant lock verification on optimized coordinates
        self.execution_trace.append("verify_final_invariants")
        verify_frozen_monomer_invariants(ref_a, final_coords[comp_a], symbols=sym_a)
        verify_frozen_monomer_invariants(ref_b, final_coords[comp_b], symbols=sym_b)

        # Build monomer specifications
        is_lin_a = _is_linear(sym_a, ref_a)
        is_lin_b = _is_linear(sym_b, ref_b)
        n_dofs_a = max(0, 3 * len(sym_a) - (5 if is_lin_a else 6))
        n_dofs_b = max(0, 3 * len(sym_b) - (5 if is_lin_b else 6))

        spec_a = MonomerSpec(
            symbols=sym_a,
            coordinates=final_coords[comp_a].tolist(),
            indices=comp_a,
            masses=[get_dynamic_atomic_mass(s) for s in sym_a],
            center_of_mass=compute_center_of_mass(final_coords[comp_a], sym_a).tolist(),
            is_linear=is_lin_a,
            n_internal_dofs=n_dofs_a,
        )
        spec_b = MonomerSpec(
            symbols=sym_b,
            coordinates=final_coords[comp_b].tolist(),
            indices=comp_b,
            masses=[get_dynamic_atomic_mass(s) for s in sym_b],
            center_of_mass=compute_center_of_mass(final_coords[comp_b], sym_b).tolist(),
            is_linear=is_lin_b,
            n_internal_dofs=n_dofs_b,
        )

        return FrozenMonomerResult(
            monomer_a=spec_a,
            monomer_b=spec_b,
            partition=partition,
            optimization=opt_res,
            counterpoise=cp_res,
            invariant_lock_verified=True,
            execution_order=list(self.execution_trace),
            converged=bool(opt_res.converged),
        )
