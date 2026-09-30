"""``cochem pes`` -- torsional PES construction and periodic sinc-DVR tunneling analysis.

Pipeline
--------
1. Read the reference geometry (selected XYZ frame) and validate the 4-atom dihedral.
2. Derive the internal-rotor reduced moment of inertia ``I_red = I1*I2/(I1+I2)`` about
   the central (i2, i3) bond from mendeleev masses, and ``F = C_ROT_CM1 / I_red``.
3. Build the torsional PES V(theta) on the periodic DVR grid with an active-learning
   loop: a truncated Fourier model is refined by querying the energy engine where a
   committee of Fourier fits (orders K and K-1) disagrees most, until the committee
   and the last engine query agree within ``--al-tolerance`` cm^-1.
4. Diagonalise ``H = T + V`` in JAX float64, where ``T`` is the Colbert-Miller / Meyer
   periodic sinc-DVR kinetic matrix, and report eigenvalues, ZPE and the ground-state
   tunneling splitting (cm^-1 and MHz). Potentials with an exact half-period
   translation symmetry (V(theta + pi) = V(theta)) are diagonalised in the even and odd
   symmetry blocks separately so that near-degenerate tunneling doublets are never mixed.

Energy engines: ``pyscf`` (Hartree-Fock via PySCF), ``dft`` (Kohn-Sham via PySCF),
``mace`` (MACE-OFF via ASE). If the requested backend is not installed, the solver
falls back to a built-in UFF force-field torsion term plus Lennard-Jones cross terms
(Rappe et al., JACS 114, 10024 (1992)). The fallback is logged, printed to stderr, and
recorded in the report (``engine_fallback`` and ``warnings``) so classical results
cannot be mistaken for ab-initio ones. ``--strict-engine`` turns the fallback into an
error.
"""

from __future__ import annotations

import os

os.environ["JAX_ENABLE_X64"] = "True"

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple  # noqa: E402

import numpy as np  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from cochem.cli.conformer import (  # noqa: E402
    C_LIGHT_CM_S,
    C_ROT_CM1,
    HARTREE_TO_KCAL_MOL,
    CLIExecutionError,
    CLIUsageError,
    element_data,
    json_safe,
    masses_for,
    model_to_dict,
    read_xyz_frames,
    write_text_file,
)

LOGGER = logging.getLogger("cochem.cli.pes")

#: 1 Hartree in cm^-1 (CODATA 2022)
HARTREE_TO_CM1 = 219474.63136320
#: 1 eV in Hartree (CODATA 2022)
EV_TO_HARTREE = 1.0 / 27.211386245981
#: 1 cm^-1 in MHz (c in cm/s / 1e6)
CM1_TO_MHZ = C_LIGHT_CM_S / 1.0e6

#: Contact distance defining rotor-top membership for the inertia partition (Angstrom).
ROTOR_CONTACT_CUTOFF_ANGSTROM = 1.9
#: Scale on the sum of covalent radii for chemical bond perception.
COVALENT_BOND_SCALE = 1.2

PES_ENGINES = ("mace", "dft", "pyscf")
MACE_MODELS = ("small", "medium", "large")

DEFAULT_ENGINE = "mace"
DEFAULT_GRID_POINTS = 36
DEFAULT_DVR_MODES = 6
DEFAULT_AL_TOLERANCE_CM1 = 1.0
DEFAULT_FOURIER_ORDER = 4
DEFAULT_BASIS = "def2-svp"
DEFAULT_XC = "b3lyp"
DEFAULT_MACE_MODEL = "small"
MIN_GRID_POINTS = 4
MIN_DVR_MODES = 2


# ---------------------------------------------------------------------------
# JAX FP64 enforcement (module load time, before any JAX array is created)
# ---------------------------------------------------------------------------
def enforce_jax_fp64() -> Any:
    """Force JAX double precision; returns the configured ``jax`` module."""
    os.environ["JAX_ENABLE_X64"] = "True"
    import jax

    jax.config.update("jax_enable_x64", True)
    return jax


try:
    _JAX: Any = enforce_jax_fp64()
    _JAX_IMPORT_ERROR: Optional[BaseException] = None
except Exception as _jax_exc:  # import must never take the CLI down
    _JAX = None
    _JAX_IMPORT_ERROR = _jax_exc


def _require_jax() -> Any:
    if _JAX is None:
        raise CLIExecutionError(
            "JAX is required for the DVR solver but could not be imported: %s" % _JAX_IMPORT_ERROR
        )
    os.environ["JAX_ENABLE_X64"] = "True"
    _JAX.config.update("jax_enable_x64", True)
    return _JAX


class PESConvergenceError(CLIExecutionError):
    """Scan or electronic-structure convergence failure."""


class EngineUnavailableError(CLIExecutionError):
    """Requested energy backend is not installed or cannot be initialised."""


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------
class PESCalculationResult(BaseModel):
    coordinate_indices: Tuple[int, int, int, int]
    grid_points: int
    reduced_f_cm1: float
    eigenvalues_cm1: List[float]
    zpe_cm1: float
    tunneling_splitting_cm1: float
    tunneling_splitting_mhz: float
    reduced_moment_of_inertia_amu_a2: float = 0.0
    reference_dihedral_deg: float = 0.0
    dvr_modes: int = 0
    engine_requested: str = ""
    engine_used: str = ""
    engine_fallback: bool = False
    warnings: List[str] = []
    energy_evaluations: int = 0
    active_learning_converged: bool = False
    active_learning_disagreement_cm1: float = 0.0
    active_learning_tolerance_cm1: float = DEFAULT_AL_TOLERANCE_CM1
    queried_grid_indices: List[int] = []
    potential_symmetry_fold: int = 1
    precision: str = "float64"
    input_path: str = ""
    frame_index: int = 0
    moving_fragment: List[int] = []
    grid_angles_deg: List[float] = []
    potential_cm1: List[float] = []
    wavefunctions: Optional[List[List[float]]] = None


# ---------------------------------------------------------------------------
# Colbert-Miller periodic sinc-DVR
# ---------------------------------------------------------------------------
def build_colbert_miller_periodic_t(n_pts: int, f_rot_cm1: float) -> np.ndarray:
    """Symmetric NxN Colbert-Miller / Meyer periodic sinc-DVR kinetic matrix (cm^-1).

    Diagonal: F (N^2-1)/12 (N odd) or F (N^2+2)/12 (N even).
    Off-diagonal (D = i - j): F (-1)^D cos(pi D/N) / (2 sin^2(pi D/N)) (N odd) or
    F (-1)^D / (2 sin^2(pi D/N)) (N even). Its spectrum is exactly F m^2.
    """
    if isinstance(n_pts, (bool, str, bytes)) or int(n_pts) != n_pts or int(n_pts) < 1:
        raise ValueError("n_pts must be a positive integer, got %r" % (n_pts,))
    n = int(n_pts)
    f = float(f_rot_cm1)
    if not math.isfinite(f) or f <= 0.0:
        raise ValueError("rotational constant F must be finite and positive, got %r" % (f_rot_cm1,))

    idx = np.arange(n)
    delta = idx[:, None] - idx[None, :]
    t = np.zeros((n, n), dtype=np.float64)
    off = delta != 0
    d_int = delta[off]
    d = d_int.astype(np.float64)
    sign = np.where(d_int % 2 == 0, 1.0, -1.0)
    s = np.sin(np.pi * d / n)
    if n % 2 == 1:
        t[off] = f * sign * np.cos(np.pi * d / n) / (2.0 * s * s)
        diag = f * ((n * n - 1) / 12.0)
    else:
        t[off] = f * sign / (2.0 * s * s)
        diag = f * ((n * n + 2) / 12.0)
    np.fill_diagonal(t, diag)
    return 0.5 * (t + t.T)


def _has_half_shift_symmetry(v: np.ndarray) -> bool:
    """True when V(theta + pi) == V(theta) on the periodic grid to rounding accuracy."""
    n = v.size
    if n < 4 or n % 2 != 0:
        return False
    h = n // 2
    scale = max(1.0, float(np.max(np.abs(v))))
    return bool(np.allclose(v[:h], v[h:], rtol=0.0, atol=1.0e-12 * scale))


def solve_dvr_hamiltonian(
    v_grid_cm1: np.ndarray, f_rot_cm1: float, n_modes: int = 10
) -> Tuple[np.ndarray, np.ndarray]:
    """Diagonalise H = T + V in JAX float64; returns (evals[:k], evecs[:, :k]) ascending.

    When the potential is invariant under the half-period translation (i -> i + N/2),
    the Hamiltonian commutes with that shift and is diagonalised separately in its even
    and odd subspaces. This keeps nearly degenerate tunneling doublets from mixing
    numerically, so each eigenfunction has a definite parity under the shift.
    """
    _require_jax()
    import jax.numpy as jnp

    v = np.asarray(v_grid_cm1, dtype=np.float64).ravel()
    if v.size < 2:
        raise ValueError("the DVR grid needs at least 2 points")
    if not np.all(np.isfinite(v)):
        raise ValueError("potential grid contains non-finite values")
    k = int(n_modes)
    if k < 1:
        raise ValueError("n_modes must be positive")

    n = v.size
    t = build_colbert_miller_periodic_t(n, f_rot_cm1)
    ham = jnp.asarray(t, dtype=jnp.float64) + jnp.diag(jnp.asarray(v, dtype=jnp.float64))
    ham = 0.5 * (ham + ham.T)
    if ham.dtype != jnp.float64:
        raise CLIExecutionError("JAX float64 precision is not active (got %s)" % ham.dtype)

    if _has_half_shift_symmetry(v):
        h = n // 2
        eye = np.eye(h, dtype=np.float64)
        inv_sqrt2 = 1.0 / math.sqrt(2.0)
        p_even = jnp.asarray(np.vstack([eye, eye]) * inv_sqrt2, dtype=jnp.float64)
        p_odd = jnp.asarray(np.vstack([eye, -eye]) * inv_sqrt2, dtype=jnp.float64)
        eval_parts: List[np.ndarray] = []
        vec_parts: List[np.ndarray] = []
        for proj in (p_even, p_odd):
            block = proj.T @ ham @ proj
            block = 0.5 * (block + block.T)
            w_b, u_b = jnp.linalg.eigh(block)
            eval_parts.append(np.array(w_b, dtype=np.float64))
            vec_parts.append(np.array(proj @ u_b, dtype=np.float64))
        evals = np.concatenate(eval_parts)
        evecs = np.concatenate(vec_parts, axis=1)
    else:
        w, u = jnp.linalg.eigh(ham)
        evals = np.array(w, dtype=np.float64)
        evecs = np.array(u, dtype=np.float64)

    order = np.argsort(evals, kind="stable")
    evals = evals[order]
    evecs = evecs[:, order]
    # Deterministic phase: largest-amplitude component positive.
    pivot = np.argmax(np.abs(evecs), axis=0)
    signs = np.where(evecs[pivot, np.arange(evecs.shape[1])] < 0.0, -1.0, 1.0)
    evecs = evecs * signs[None, :]
    k = min(k, n)
    return evals[:k].copy(), evecs[:, :k].copy()


def potential_symmetry_fold(v_grid_cm1: np.ndarray, atol_cm1: float) -> int:
    """Largest n (dividing N) with V(theta + 2 pi / n) == V(theta) within ``atol_cm1``."""
    v = np.asarray(v_grid_cm1, dtype=np.float64).ravel()
    n = v.size
    best = 1
    for fold in range(2, n + 1):
        if n % fold:
            continue
        if np.allclose(v, np.roll(v, n // fold), rtol=0.0, atol=atol_cm1):
            best = fold
    return best


# ---------------------------------------------------------------------------
# Geometry, connectivity and internal-rotor inertia
# ---------------------------------------------------------------------------
def _validate_dihedral(dihedral_indices: Sequence[int], n_atoms: int) -> Tuple[int, int, int, int]:
    try:
        idx = list(dihedral_indices)
    except TypeError:
        raise ValueError("dihedral indices must be a sequence of 4 integers") from None
    if len(idx) != 4:
        raise ValueError("a dihedral needs exactly 4 atom indices, got %d" % len(idx))
    out: List[int] = []
    for v in idx:
        if isinstance(v, (bool, str, bytes)):
            raise ValueError("atom indices must be integers, got %r" % (v,))
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise ValueError("atom indices must be integers, got %r" % (v,)) from None
        if iv != v:
            raise ValueError("atom indices must be integers, got %r" % (v,))
        out.append(iv)
    for v in out:
        if v < 0 or v >= n_atoms:
            raise ValueError(
                "atom index %d is out of range for a molecule with %d atoms (valid 0..%d)"
                % (v, n_atoms, n_atoms - 1)
            )
    if len(set(out)) != 4:
        raise ValueError("dihedral atom indices must be distinct, got %r" % (out,))
    return out[0], out[1], out[2], out[3]


def _distances(xyz: np.ndarray) -> np.ndarray:
    diff = xyz[:, None, :] - xyz[None, :, :]
    return np.sqrt(np.einsum("ijk,ijk->ij", diff, diff))


def _covalent_radii(symbols: Sequence[str]) -> np.ndarray:
    return np.array(
        [element_data(s).covalent_radius or 0.0 for s in symbols], dtype=np.float64
    )


def covalent_adjacency(symbols: Sequence[str], xyz: np.ndarray) -> np.ndarray:
    """Chemical bonds: d <= 1.2 x (sum of mendeleev covalent radii)."""
    radii = _covalent_radii(symbols)
    missing = [s for s, r in zip(symbols, radii) if r <= 0.0]
    if missing:
        raise ValueError("no covalent radius available for element(s) %s" % sorted(set(missing)))
    adj = _distances(xyz) <= COVALENT_BOND_SCALE * (radii[:, None] + radii[None, :])
    np.fill_diagonal(adj, False)
    return adj


def contact_adjacency(symbols: Sequence[str], xyz: np.ndarray) -> np.ndarray:
    """Rotor-top contact graph: d < 1.9 A, extended by covalent radii for long bonds."""
    dist = _distances(xyz)
    radii = _covalent_radii(symbols)
    adj = (dist < ROTOR_CONTACT_CUTOFF_ANGSTROM) | (
        dist <= COVALENT_BOND_SCALE * (radii[:, None] + radii[None, :])
    )
    np.fill_diagonal(adj, False)
    return adj


def _component(adj: np.ndarray, root: int, blocked: int) -> Set[int]:
    seen = {root}
    stack = [root]
    while stack:
        cur = stack.pop()
        for nb in np.flatnonzero(adj[cur]):
            nb = int(nb)
            if nb == blocked or nb in seen:
                continue
            seen.add(nb)
            stack.append(nb)
    return seen


def _bond_in_ring(adj: np.ndarray, i2: int, i3: int) -> bool:
    """True if i3 stays reachable from i2 once the direct i2-i3 edge is removed."""
    cut = np.array(adj, dtype=bool, copy=True)
    cut[i2, i3] = False
    cut[i3, i2] = False
    return i3 in _component(cut, i2, -1)


def compute_reduced_moment_of_inertia(
    symbols: Sequence[str], coords: np.ndarray, dihedral_indices: Tuple[int, int, int, int]
) -> Tuple[float, float]:
    """Return ``(I_red [u A^2], F [cm^-1])`` for torsion about the (i2, i3) bond.

    Top 1 (Top 2) holds the atoms reachable from i2 (i3) in the contact graph without
    passing through i3 (i2). If the two axis atoms stay connected after cutting their
    bond, the bond lies in a ring and there is no independent internal rotor.
    ``I_k = sum m_j d_j^2`` uses perpendicular distances to the bond axis and mendeleev
    masses; ``I_red = I1*I2/(I1+I2)``.
    """
    xyz = np.asarray(coords, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or xyz.shape[0] != len(symbols):
        raise ValueError("coordinates must have shape (%d, 3)" % len(symbols))
    i1, i2, i3, i4 = _validate_dihedral(dihedral_indices, len(symbols))
    axis = xyz[i3] - xyz[i2]
    length = float(np.linalg.norm(axis))
    if length < 1.0e-8:
        raise ValueError("axis atoms %d and %d coincide" % (i2, i3))
    u = axis / length

    adj = contact_adjacency(symbols, xyz)
    if _bond_in_ring(adj, i2, i3):
        raise ValueError("bond %d-%d lies in a ring; no independent internal rotor" % (i2, i3))
    top1 = _component(adj, i2, i3)
    top2 = _component(adj, i3, i2)
    if i1 not in top1:
        raise ValueError("atom %d is not connected to axis atom %d" % (i1, i2))
    if i4 not in top2:
        raise ValueError("atom %d is not connected to axis atom %d" % (i4, i3))

    masses = masses_for(symbols)

    def moment(top: Set[int]) -> float:
        members = sorted(top)
        rel = xyz[members] - xyz[i2]
        perp = rel - np.outer(rel @ u, u)
        return float(np.sum(masses[members] * np.einsum("ij,ij->i", perp, perp)))

    m1 = moment(top1)
    m2 = moment(top2)
    if m1 <= 1.0e-10 or m2 <= 1.0e-10:
        raise ValueError("a rotor top has zero moment of inertia about the %d-%d axis" % (i2, i3))
    i_red = m1 * m2 / (m1 + m2)
    return float(i_red), float(C_ROT_CM1 / i_red)


def dihedral_angle(xyz: np.ndarray, idx: Sequence[int]) -> float:
    """Signed dihedral angle (radians) for atoms idx[0..3]."""
    p0, p1, p2, p3 = (xyz[int(k)] for k in idx)
    b0 = p0 - p1
    b1 = p2 - p1
    b2 = p3 - p2
    b1n = b1 / np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1n) * b1n
    w = b2 - np.dot(b2, b1n) * b1n
    x = float(np.dot(v, w))
    y = float(np.dot(np.cross(b1n, v), w))
    return math.atan2(y, x)


def torsional_fragment(
    symbols: Sequence[str], xyz: np.ndarray, idx: Tuple[int, int, int, int]
) -> Tuple[Set[int], Set[int]]:
    """(static, moving) covalent fragments for a rigid torsional scan about (i2, i3)."""
    i1, i2, i3, i4 = idx
    adj = covalent_adjacency(symbols, xyz)
    if not adj[i2, i3]:
        raise ValueError(
            "atoms %d and %d are not covalently bonded; the central dihedral atoms must share a bond"
            % (i2, i3)
        )
    if _bond_in_ring(adj, i2, i3):
        raise ValueError("bond %d-%d lies in a ring; a rigid torsional scan is impossible" % (i2, i3))
    moving = _component(adj, i3, i2)
    static = _component(adj, i2, i3)
    if i4 not in moving or i1 not in static:
        raise ValueError(
            "dihedral end atoms %d/%d are not bonded to the %d-%d rotor" % (i1, i4, i2, i3)
        )
    return static, moving


def rotate_fragment(
    xyz: np.ndarray, moving: Set[int], i2: int, i3: int, angle: float
) -> np.ndarray:
    """Rodrigues rotation of the ``moving`` atoms about the i2->i3 axis."""
    out = np.array(xyz, dtype=np.float64, copy=True)
    origin = xyz[i2]
    u = xyz[i3] - xyz[i2]
    u = u / np.linalg.norm(u)
    members = sorted(moving)
    rel = xyz[members] - origin
    c = math.cos(angle)
    s = math.sin(angle)
    rotated = rel * c + np.cross(u, rel) * s + np.outer(rel @ u, u) * (1.0 - c)
    out[members] = rotated + origin
    return out


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


# ---------------------------------------------------------------------------
# Energy engines
# ---------------------------------------------------------------------------
class PySCFEngine:
    """Hartree-Fock (method 'hf') or Kohn-Sham DFT (method 'dft') single points via PySCF."""

    def __init__(
        self,
        method: str,
        symbols: Sequence[str],
        basis: str,
        xc: str,
        charge: int,
        multiplicity: Optional[int],
    ) -> None:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                from pyscf import dft, gto, scf
        except (ImportError, OSError) as exc:
            raise EngineUnavailableError("PySCF is not importable (%s)" % exc) from None
        self._gto, self._scf, self._dft = gto, scf, dft
        electrons = sum(element_data(s).atomic_number for s in symbols) - int(charge)
        if electrons <= 0:
            raise CLIExecutionError("charge %d leaves no electrons" % charge)
        spin = (electrons % 2) if multiplicity is None else int(multiplicity) - 1
        if spin < 0 or (electrons - spin) % 2 != 0:
            raise CLIExecutionError(
                "multiplicity %s is incompatible with %d electrons" % (multiplicity, electrons)
            )
        self.method = method
        self.basis = basis
        self.xc = xc
        self.charge = int(charge)
        self.spin = spin
        self.notes: List[str] = []
        if method == "hf":
            self.label = "pyscf:%s/%s" % ("RHF" if spin == 0 else "UHF", basis)
        else:
            self.label = "pyscf:%s-%s/%s" % ("RKS" if spin == 0 else "UKS", xc, basis)

    def energy_hartree(self, symbols: Sequence[str], coords: np.ndarray) -> float:
        atoms = [[s, tuple(float(v) for v in row)] for s, row in zip(symbols, coords)]
        with contextlib.redirect_stdout(sys.stderr):
            try:
                mol = self._gto.M(
                    atom=atoms,
                    basis=self.basis,
                    charge=self.charge,
                    spin=self.spin,
                    unit="Angstrom",
                    verbose=0,
                )
            except Exception as exc:
                raise CLIExecutionError("PySCF could not build the molecule (%s)" % exc) from None
            if self.method == "hf":
                mf = self._scf.RHF(mol) if self.spin == 0 else self._scf.UHF(mol)
            else:
                mf = self._dft.RKS(mol) if self.spin == 0 else self._dft.UKS(mol)
                mf.xc = self.xc
            mf.verbose = 0
            mf.max_cycle = 200
            energy = float(mf.kernel())
        if not bool(getattr(mf, "converged", False)):
            raise PESConvergenceError("SCF did not converge (%s)" % self.label)
        return energy


class MACEEngine:
    """MACE-OFF machine-learned potential through ASE."""

    def __init__(self, model: str) -> None:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                from ase import Atoms
                from mace.calculators import mace_off

                self._calc = mace_off(model=model, default_dtype="float64", device="cpu")
        except (ImportError, OSError) as exc:
            raise EngineUnavailableError("MACE/ASE are not importable (%s)" % exc) from None
        except Exception as exc:  # model download / initialisation failures
            raise EngineUnavailableError("MACE-OFF model %r could not be loaded (%s)" % (model, exc)) from None
        self._atoms_cls = Atoms
        self.notes: List[str] = []
        self.label = "mace:mace_off-%s" % model

    def energy_hartree(self, symbols: Sequence[str], coords: np.ndarray) -> float:
        with contextlib.redirect_stdout(sys.stderr):
            atoms = self._atoms_cls(symbols=list(symbols), positions=np.asarray(coords))
            atoms.calc = self._calc
            e_ev = float(atoms.get_potential_energy())
        return e_ev * EV_TO_HARTREE


# UFF parameters (Rappe et al. 1992): Lennard-Jones x_i (A), D_i (kcal/mol)
UFF_LJ: Dict[str, Tuple[float, float]] = {
    "H": (2.886, 0.044), "He": (2.362, 0.056), "Li": (2.451, 0.025), "B": (4.083, 0.180),
    "C": (3.851, 0.105), "N": (3.660, 0.069), "O": (3.500, 0.060), "F": (3.364, 0.050),
    "Na": (2.983, 0.030), "Mg": (3.021, 0.111), "Al": (4.499, 0.505), "Si": (4.295, 0.402),
    "P": (4.147, 0.305), "S": (4.035, 0.274), "Cl": (3.947, 0.227), "K": (3.812, 0.035),
    "Ca": (3.399, 0.238), "Ge": (4.280, 0.379), "As": (4.230, 0.309), "Se": (4.205, 0.291),
    "Br": (4.189, 0.251), "Sn": (4.392, 0.567), "Sb": (4.420, 0.449), "Te": (4.470, 0.398),
    "I": (4.500, 0.339),
}
# UFF sp3 torsional barriers V_i (kcal/mol). These are torsion force-field parameters,
# not atomic masses (all masses come from mendeleev); stored as (symbol, V_i) pairs.
_UFF_V_SP3_TABLE: Tuple[Tuple[str, float], ...] = (
    ("C", 2.119), ("N", 0.450), ("O", 0.018), ("Si", 1.225), ("P", 2.400), ("S", 0.484),
    ("Ge", 0.701), ("As", 1.500), ("Se", 0.335), ("Sn", 0.199), ("Sb", 1.100), ("Te", 0.300),
)
UFF_V_SP3: Dict[str, float] = dict(_UFF_V_SP3_TABLE)
# sp2 torsional constants U_i (kcal/mol) by period
UFF_U_SP2_BY_PERIOD: Dict[int, float] = {2: 2.0, 3: 1.25, 4: 0.7, 5: 0.2, 6: 0.1}

_HYBRIDISATION_BY_GROUP: Dict[int, Dict[int, str]] = {
    14: {4: "sp3", 3: "sp2", 2: "sp"},
    15: {3: "sp3", 2: "sp2", 1: "sp"},
    16: {2: "sp3", 1: "sp2"},
}


def _uff_hybridisation(symbol: str, degree: int) -> Optional[str]:
    group = element_data(symbol).group
    if group is None:
        return None
    return _HYBRIDISATION_BY_GROUP.get(group, {}).get(degree)


def _uff_u(symbol: str) -> Optional[float]:
    period = element_data(symbol).period
    return UFF_U_SP2_BY_PERIOD.get(period) if period is not None else None


def _uff_torsion_parameters(
    symbols: Sequence[str], adj: np.ndarray, j: int, k: int
) -> Optional[Tuple[float, int, float]]:
    sj, sk = symbols[j], symbols[k]
    hj = _uff_hybridisation(sj, int(adj[j].sum()))
    hk = _uff_hybridisation(sk, int(adj[k].sum()))
    if hj is None or hk is None or "sp" in (hj, hk):
        return None
    gj = element_data(sj).group
    gk = element_data(sk).group
    if hj == "sp3" and hk == "sp3":
        if gj == 16 and gk == 16:
            vj = 2.0 if sj == "O" else 6.8
            vk = 2.0 if sk == "O" else 6.8
            return math.sqrt(vj * vk), 2, 90.0
        vj_ = UFF_V_SP3.get(sj)
        vk_ = UFF_V_SP3.get(sk)
        if vj_ is None or vk_ is None:
            return None
        return math.sqrt(vj_ * vk_), 3, 180.0
    uj = _uff_u(sj)
    uk = _uff_u(sk)
    if hj == "sp2" and hk == "sp2":
        if uj is None or uk is None:
            return None
        return 5.0 * math.sqrt(uj * uk), 2, 180.0
    sp3_group = gj if hj == "sp3" else gk
    if sp3_group == 16:
        if uj is None or uk is None:
            return None
        return 5.0 * math.sqrt(uj * uk), 2, 90.0
    return 1.0, 6, 0.0


class UFFTorsionEngine:
    """Built-in UFF torsional force field (torsion + cross-rotor Lennard-Jones terms).

    Only energy terms that vary under rigid rotation about the scanned bond are kept:
    the UFF torsion potential about (i2, i3) and 12-6 LJ interactions between moving
    and static atoms (excluding the axis atoms, whose distances are invariant).
    E_tor = 1/2 V [1 - cos(n phi0) cos(n phi)] per torsion (V shared over all torsions
    about the bond); E_LJ = D_ij [(x_ij/r)^12 - 2 (x_ij/r)^6].
    """

    def __init__(
        self,
        symbols: Sequence[str],
        xyz: np.ndarray,
        dihedral: Tuple[int, int, int, int],
        moving: Set[int],
    ) -> None:
        xyz = np.asarray(xyz, dtype=np.float64)
        i2, i3 = dihedral[1], dihedral[2]
        adj = covalent_adjacency(symbols, xyz)
        self.label = "uff-builtin (UFF torsion + Lennard-Jones)"
        self.notes: List[str] = []
        self._terms: List[Tuple[int, int, int, int, float, int, float]] = []
        nb2 = [int(a) for a in np.flatnonzero(adj[i2]) if int(a) != i3]
        nb3 = [int(d) for d in np.flatnonzero(adj[i3]) if int(d) != i2]
        params = _uff_torsion_parameters(symbols, adj, i2, i3)
        if params is not None and nb2 and nb3:
            barrier, n_fold, phi0 = params
            per_torsion = barrier / float(len(nb2) * len(nb3))
            for a in nb2:
                for d in nb3:
                    self._terms.append((a, i2, i3, d, per_torsion, n_fold, math.radians(phi0)))
        else:
            self.notes.append(
                "no UFF torsion parameters for the %s(%d)-%s(%d) bond; only Lennard-Jones "
                "cross terms contribute to the fallback potential"
                % (symbols[i2], i2, symbols[i3], i3)
            )
        moving_set = {int(m) for m in moving}
        self._pairs: List[Tuple[int, int, float, float]] = []
        missing: Set[str] = set()
        for a in sorted(moving_set):
            if a in (i2, i3):
                continue
            pa = UFF_LJ.get(symbols[a])
            if pa is None:
                missing.add(symbols[a])
                continue
            for b in range(len(symbols)):
                if b in moving_set or b in (i2, i3):
                    continue
                pb = UFF_LJ.get(symbols[b])
                if pb is None:
                    missing.add(symbols[b])
                    continue
                self._pairs.append((a, b, math.sqrt(pa[0] * pb[0]), math.sqrt(pa[1] * pb[1])))
        if missing:
            self.notes.append(
                "no UFF Lennard-Jones parameters for element(s) %s; their non-bonded terms are omitted"
                % ", ".join(sorted(missing))
            )
        if not self._terms and not self._pairs:
            self.notes.append("the UFF fallback potential is flat for this rotor (free rotation)")

    def energy_hartree(self, symbols: Sequence[str], coords: np.ndarray) -> float:
        xyz = np.asarray(coords, dtype=np.float64)
        e_kcal = 0.0
        for a, b, c, d, v, n_fold, phi0 in self._terms:
            phi = dihedral_angle(xyz, (a, b, c, d))
            e_kcal += 0.5 * v * (1.0 - math.cos(n_fold * phi0) * math.cos(n_fold * phi))
        for a, b, x_ij, d_ij in self._pairs:
            diff = xyz[a] - xyz[b]
            r = float(math.sqrt(float(diff @ diff)))
            if r < 1.0e-6:
                raise PESConvergenceError("atoms %d and %d overlap during the torsional scan" % (a, b))
            s6 = (x_ij / r) ** 6
            e_kcal += d_ij * (s6 * s6 - 2.0 * s6)
        return e_kcal / HARTREE_TO_KCAL_MOL


@dataclass
class EngineSelection:
    engine: Any
    requested: str
    fallback: bool
    warnings: List[str] = field(default_factory=list)


def select_engine(
    requested: str,
    symbols: Sequence[str],
    xyz: np.ndarray,
    dihedral: Tuple[int, int, int, int],
    moving: Set[int],
    *,
    basis: str = DEFAULT_BASIS,
    xc: str = DEFAULT_XC,
    charge: int = 0,
    multiplicity: Optional[int] = None,
    mace_model: str = DEFAULT_MACE_MODEL,
    strict: bool = False,
) -> EngineSelection:
    """Instantiate the requested backend, falling back (transparently) to built-in UFF."""
    if requested not in PES_ENGINES:
        raise CLIUsageError("unknown engine %r (choose from %s)" % (requested, ", ".join(PES_ENGINES)))
    try:
        if requested == "mace":
            engine: Any = MACEEngine(mace_model)
        elif requested == "dft":
            engine = PySCFEngine("dft", symbols, basis, xc, charge, multiplicity)
        else:
            engine = PySCFEngine("hf", symbols, basis, xc, charge, multiplicity)
        return EngineSelection(engine, requested, False, list(getattr(engine, "notes", [])))
    except EngineUnavailableError as exc:
        if strict:
            raise
        message = (
            "requested engine %r is unavailable (%s); falling back to the built-in UFF "
            "torsion + Lennard-Jones force field (classical, not ab initio)" % (requested, exc)
        )
        LOGGER.info(message)
        engine = UFFTorsionEngine(symbols, xyz, dihedral, moving)
        return EngineSelection(engine, requested, True, [message] + list(engine.notes))


# ---------------------------------------------------------------------------
# Active-learning PES construction
# ---------------------------------------------------------------------------
@dataclass
class ActiveLearningOutcome:
    potential_cm1: np.ndarray  # relative to the grid minimum
    queried: List[int]
    converged: bool
    disagreement_cm1: float
    last_validation_error_cm1: Optional[float]


def _fourier_design(theta: np.ndarray, order: int) -> np.ndarray:
    cols = [np.ones_like(theta)]
    for k in range(1, order + 1):
        cols.append(np.cos(k * theta))
        cols.append(np.sin(k * theta))
    return np.stack(cols, axis=1)


def _fourier_predict(theta_q: np.ndarray, v_q: np.ndarray, theta: np.ndarray, order: int) -> np.ndarray:
    a = _fourier_design(theta_q, order)
    coef, *_ = np.linalg.lstsq(a, v_q, rcond=None)
    return _fourier_design(theta, order) @ coef


def active_learning_pes(
    energy_fn: Callable[[float], float],
    n_grid: int,
    initial_order: int = DEFAULT_FOURIER_ORDER,
    tolerance_cm1: float = DEFAULT_AL_TOLERANCE_CM1,
    full_scan: bool = False,
) -> ActiveLearningOutcome:
    """Build V on the periodic grid theta_j = 2 pi j / N with committee-driven queries.

    ``energy_fn(delta)`` returns the engine energy (Hartree) of the geometry rotated by
    ``delta`` radians. A Fourier model of order K = (n_queried - 1) // 2 and its order
    K-1 companion form a committee; the grid point where they disagree most is queried
    next. Convergence requires the maximum committee disagreement on unqueried points
    and the prediction error of the latest query both to be within ``tolerance_cm1``.
    Queried points keep their engine energies; the rest use the converged model.
    """
    n = int(n_grid)
    if n < MIN_GRID_POINTS:
        raise ValueError("at least %d grid points are required" % MIN_GRID_POINTS)
    tol = float(tolerance_cm1)
    if not math.isfinite(tol) or tol <= 0.0:
        raise ValueError("active-learning tolerance must be positive")
    theta = 2.0 * math.pi * np.arange(n, dtype=np.float64) / n
    max_order = (n - 1) // 2
    k0 = max(1, min(int(initial_order), max_order))

    energies: Dict[int, float] = {}

    def query(j: int) -> float:
        e = float(energy_fn(float(theta[j])))
        if not math.isfinite(e):
            raise PESConvergenceError("engine returned a non-finite energy at grid point %d" % j)
        energies[j] = e
        LOGGER.info("PES query %d/%d at %.2f deg: %.10f Eh", len(energies), n, math.degrees(theta[j]), e)
        return e

    if full_scan:
        for j in range(n):
            query(j)
    else:
        n_seed = min(n, 2 * k0 + 1)
        seed = sorted({int(round(j * n / n_seed)) % n for j in range(n_seed)})
        for j in seed:
            query(j)

    e_ref = energies[0]

    def queried_arrays() -> Tuple[List[int], np.ndarray]:
        q = sorted(energies)
        v_q = np.array([(energies[j] - e_ref) * HARTREE_TO_CM1 for j in q], dtype=np.float64)
        return q, v_q

    last_error: Optional[float] = None
    disagreement = 0.0
    converged = False
    while True:
        q, v_q = queried_arrays()
        if len(q) == n:
            converged = True
            disagreement = 0.0
            model = np.empty(n, dtype=np.float64)
            break
        order = max(1, min(max_order, (len(q) - 1) // 2))
        fa = _fourier_predict(theta[q], v_q, theta, order)
        fb = _fourier_predict(theta[q], v_q, theta, order - 1)
        unq = [j for j in range(n) if j not in energies]
        d = np.abs(fa[unq] - fb[unq])
        pos = int(np.argmax(d))
        disagreement = float(d[pos])
        if disagreement <= tol and last_error is not None and last_error <= tol:
            converged = True
            model = fa
            break
        j_next = unq[pos]
        predicted = float(fa[j_next])
        actual = (query(j_next) - e_ref) * HARTREE_TO_CM1
        last_error = abs(predicted - actual)

    potential = np.array(model, dtype=np.float64)
    for j, e in energies.items():
        potential[j] = (e - e_ref) * HARTREE_TO_CM1
    potential = potential - float(np.min(potential))
    return ActiveLearningOutcome(
        potential_cm1=potential,
        queried=sorted(energies),
        converged=converged,
        disagreement_cm1=disagreement,
        last_validation_error_cm1=last_error,
    )


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
def execute_pes(args: argparse.Namespace) -> Dict[str, Any]:
    """Run the full PES + DVR workflow and return the JSON-safe report dictionary."""
    path = Path(str(args.input))
    frames = read_xyz_frames(path)
    frame_index = int(getattr(args, "frame", 0) or 0)
    if frame_index >= len(frames):
        raise CLIExecutionError(
            "frame index %d is out of range: %s contains %d frame(s)" % (frame_index, path, len(frames))
        )
    frame = frames[frame_index]
    symbols = list(frame.symbols)
    xyz = np.array(frame.coordinates, dtype=np.float64)

    try:
        idx = _validate_dihedral(args.dihedral, len(symbols))
    except ValueError as exc:
        raise CLIExecutionError("invalid --dihedral atom indices: %s" % exc) from None
    i1, i2, i3, i4 = idx

    _static, moving = torsional_fragment(symbols, xyz, idx)
    i_red, f_rot = compute_reduced_moment_of_inertia(symbols, xyz, idx)

    selection = select_engine(
        args.engine,
        symbols,
        xyz,
        idx,
        moving,
        basis=getattr(args, "basis", DEFAULT_BASIS),
        xc=getattr(args, "xc", DEFAULT_XC),
        charge=int(getattr(args, "charge", 0) or 0),
        multiplicity=getattr(args, "multiplicity", None),
        mace_model=getattr(args, "mace_model", DEFAULT_MACE_MODEL),
        strict=bool(getattr(args, "strict_engine", False)),
    )
    for w in selection.warnings:
        print("cochem pes: warning: %s" % w, file=sys.stderr)
    engine = selection.engine

    def energy_at(delta: float) -> float:
        geom = rotate_fragment(xyz, moving, i2, i3, delta)
        return float(engine.energy_hartree(symbols, geom))

    n_grid = int(args.grid_points)
    tolerance = float(getattr(args, "al_tolerance", DEFAULT_AL_TOLERANCE_CM1))
    outcome = active_learning_pes(
        energy_at,
        n_grid,
        initial_order=int(getattr(args, "fourier_order", DEFAULT_FOURIER_ORDER)),
        tolerance_cm1=tolerance,
        full_scan=bool(getattr(args, "full_scan", False)),
    )
    v = outcome.potential_cm1

    warnings = list(selection.warnings)
    fold = potential_symmetry_fold(v, tolerance)
    if fold == 1:
        msg = (
            "the torsional potential has no periodic symmetry on the grid (within %.3g cm^-1); "
            "E1 - E0 is then the lowest torsional excitation rather than a tunneling splitting"
            % tolerance
        )
        warnings.append(msg)
        print("cochem pes: warning: %s" % msg, file=sys.stderr)

    requested_modes = int(args.dvr_modes)
    n_modes = min(requested_modes, n_grid)
    if n_modes < requested_modes:
        msg = "--dvr-modes %d exceeds the grid size; reporting %d eigenvalues" % (requested_modes, n_modes)
        warnings.append(msg)
        print("cochem pes: warning: %s" % msg, file=sys.stderr)

    evals, evecs = solve_dvr_hamiltonian(v, f_rot, n_modes)
    evals = np.asarray(evals, dtype=np.float64)
    zpe = float(evals[0])
    split_cm1 = float(evals[1] - evals[0])
    split_mhz = split_cm1 * CM1_TO_MHZ

    theta = 2.0 * math.pi * np.arange(n_grid, dtype=np.float64) / n_grid
    grid_deg = [
        math.degrees(_wrap(dihedral_angle(rotate_fragment(xyz, moving, i2, i3, float(t)), idx)))
        for t in theta
    ]

    result = PESCalculationResult(
        coordinate_indices=(i1, i2, i3, i4),
        grid_points=n_grid,
        reduced_f_cm1=f_rot,
        eigenvalues_cm1=[float(x) for x in evals],
        zpe_cm1=zpe,
        tunneling_splitting_cm1=split_cm1,
        tunneling_splitting_mhz=split_mhz,
        reduced_moment_of_inertia_amu_a2=i_red,
        reference_dihedral_deg=math.degrees(dihedral_angle(xyz, idx)),
        dvr_modes=n_modes,
        engine_requested=selection.requested,
        engine_used=str(engine.label),
        engine_fallback=selection.fallback,
        warnings=warnings,
        energy_evaluations=len(outcome.queried),
        active_learning_converged=outcome.converged,
        active_learning_disagreement_cm1=outcome.disagreement_cm1,
        active_learning_tolerance_cm1=tolerance,
        queried_grid_indices=list(outcome.queried),
        potential_symmetry_fold=fold,
        precision="float64",
        input_path=str(path),
        frame_index=frame_index,
        moving_fragment=sorted(int(m) for m in moving),
        grid_angles_deg=grid_deg,
        potential_cm1=[float(x) for x in v],
        wavefunctions=(
            [[float(c) for c in col] for col in np.asarray(evecs).T]
            if bool(getattr(args, "include_wavefunctions", False))
            else None
        ),
    )
    payload: Dict[str, Any] = {"subcommand": "pes"}
    payload.update(model_to_dict(result))
    return json_safe(payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _int_at_least(minimum: int, what: str) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(str(text).strip(), 10)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError("invalid integer for %s: %r" % (what, text)) from None
        if value < minimum:
            raise argparse.ArgumentTypeError(
                "%s must be an integer >= %d, got %d" % (what, minimum, value)
            )
        return value

    parse.__name__ = what.replace(" ", "_")
    return parse


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid number: %r" % text) from None
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError("must be a finite positive number, got %r" % text)
    return value


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Show defaults and keep the epilog layout."""


_PES_EPILOG = """\
energy engines (--engine):
  mace   MACE-OFF machine-learned potential through ASE (--mace-model)
  dft    Kohn-Sham DFT single points through PySCF (--xc, --basis)
  pyscf  Hartree-Fock single points through PySCF (--basis)
  If the requested backend is not installed, the built-in UFF torsion +
  Lennard-Jones force field is used instead. The fallback is printed to
  stderr and recorded in the JSON report (engine_fallback, warnings);
  --strict-engine turns it into an error.

active learning:
  The moving fragment is rotated rigidly about the I2-I3 bond on a periodic
  grid of --grid-points angles. A Fourier committee (orders K and K-1) picks
  the next engine query where it disagrees most, until the disagreement and
  the error of the last query are both within --al-tolerance cm^-1
  (--full-scan evaluates every grid point).

DVR solver:
  H = T + V with the Colbert-Miller / Meyer periodic sinc-DVR kinetic matrix,
  F = h / (8 pi^2 c I_red) from mendeleev masses, diagonalised in JAX float64.
  The JSON report (stdout and --out) holds eigenvalues_cm1, zpe_cm1 and the
  ground-state tunneling splitting E1 - E0 in cm^-1 and MHz.
"""


def register_pes_subparser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "pes",
        help="Active-learning torsional PES and periodic sinc-DVR tunneling calculation",
        description=(
            "Construct a 1D torsional potential energy surface for the dihedral "
            "I1-I2-I3-I4 by active learning, then solve the periodic sinc-DVR "
            "Hamiltonian for torsional levels, zero-point energy and the "
            "ground-state tunneling splitting."
        ),
        epilog=_PES_EPILOG,
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("--input", required=True, metavar="XYZ", help="Reference geometry (XYZ file).")
    parser.add_argument(
        "--dihedral",
        required=True,
        nargs=4,
        type=_int_at_least(0, "atom index"),
        metavar=("I1", "I2", "I3", "I4"),
        help="Zero-based atom indices of the scanned dihedral; torsion about the I2-I3 bond.",
    )
    parser.add_argument(
        "--engine",
        choices=list(PES_ENGINES),
        default=DEFAULT_ENGINE,
        help="Energy engine: mace (MACE-OFF), dft (PySCF Kohn-Sham) or pyscf (PySCF Hartree-Fock).",
    )
    parser.add_argument(
        "--grid-points",
        type=_int_at_least(MIN_GRID_POINTS, "grid points"),
        default=DEFAULT_GRID_POINTS,
        help="Number of periodic DVR grid points over 360 degrees.",
    )
    parser.add_argument(
        "--dvr-modes",
        type=_int_at_least(MIN_DVR_MODES, "DVR modes"),
        default=DEFAULT_DVR_MODES,
        help="Number of lowest torsional eigenvalues to report.",
    )
    parser.add_argument("--out", default=None, metavar="PATH", help="Write the JSON result to PATH.")
    parser.add_argument(
        "--al-tolerance",
        type=_positive_float,
        default=DEFAULT_AL_TOLERANCE_CM1,
        help="Active-learning convergence tolerance in cm^-1.",
    )
    parser.add_argument(
        "--fourier-order",
        type=_int_at_least(1, "Fourier order"),
        default=DEFAULT_FOURIER_ORDER,
        help="Initial Fourier order of the surrogate model (sets the seed size 2K+1).",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="Evaluate the engine on every grid point instead of active learning.",
    )
    parser.add_argument(
        "--strict-engine",
        action="store_true",
        help="Fail instead of falling back to the built-in UFF force field.",
    )
    parser.add_argument("--basis", default=DEFAULT_BASIS, help="Basis set for the dft and pyscf engines.")
    parser.add_argument("--xc", default=DEFAULT_XC, help="Exchange-correlation functional for the dft engine.")
    parser.add_argument("--charge", type=int, default=0, help="Molecular charge (dft and pyscf engines).")
    parser.add_argument(
        "--multiplicity",
        type=_int_at_least(1, "multiplicity"),
        default=None,
        help="Spin multiplicity 2S+1 (dft and pyscf engines; default: lowest).",
    )
    parser.add_argument(
        "--mace-model",
        choices=list(MACE_MODELS),
        default=DEFAULT_MACE_MODEL,
        help="MACE-OFF model size for the mace engine.",
    )
    parser.add_argument(
        "--frame",
        type=_int_at_least(0, "frame index"),
        default=0,
        help="Zero-based frame of a multi-frame XYZ file to use as reference geometry.",
    )
    parser.add_argument(
        "--include-wavefunctions",
        action="store_true",
        help="Include the DVR eigenvector amplitudes in the JSON report.",
    )
    return parser


def run_pes_cli(args: argparse.Namespace) -> int:
    """Entry point used by ``cochem pes``; returns the process exit code."""
    try:
        payload = execute_pes(args)
        text = json.dumps(payload, indent=2, allow_nan=False)
        out = getattr(args, "out", None)
        if out:
            write_text_file(Path(str(out)), text + "\n")
    except CLIExecutionError as exc:
        print("cochem pes: error: %s" % exc, file=sys.stderr)
        return int(exc.exit_code)
    except (ValueError, OSError, np.linalg.LinAlgError) as exc:
        print("cochem pes: error: %s" % exc, file=sys.stderr)
        return 1
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0
