"""CoChem authentic molecular geometry fixtures (Task 20.109.2).

Every coordinate and Hessian delivered by this module comes from a real
physical calculation; nothing is fabricated procedurally.

Two physical sources are provided:

1. Ab-initio fixtures (``load_authentic_coordinates``, ``load_authentic_hessian``,
   ``get_fixture_with_provenance``).
   Data is read from ``cochem_fixture_data/ab_initio/<name>.npz`` (``.npy`` and
   ``.h5`` are also supported) through a JSON manifest carrying SHA-256 hashes,
   which are verified on every load.
   If a fixture has not been generated yet, it is produced once by the
   restricted Hartree-Fock engine in this module:
   - Basis: 3-21G (Binkley, Pople, Hehre, J. Am. Chem. Soc. 102, 939 (1980)).
   - Integrals: McMurchie-Davidson scheme.
   - SCF: DIIS-accelerated.
   - Geometry: BFGS optimisation, starting from the ASE G2 structure
     (MP2/6-31G*), with central-difference energy gradients.
   - Hessian: central second differences of the RHF energy.
   The result is written to disk together with full provenance.

2. ASE EMT fallback (``compute_geometry_and_hessian``).
   BFGS minimisation followed by a central-difference force Hessian.

Atomic masses are resolved exclusively through ``mendeleev``.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import scipy
from scipy.optimize import minimize
from scipy.special import gamma as gamma_function
from scipy.special import gammainc

import ase
from ase import Atoms
from ase import units as ase_units
from ase.build import molecule as ase_molecule
from ase.calculators.emt import EMT
from ase.optimize import BFGS
from mendeleev import element as mendeleev_element

try:  # element set parameterised by ASE's EMT implementation
    from ase.calculators.emt import parameters as _EMT_PARAMETERS
except ImportError:  # pragma: no cover - very old/new ASE layouts
    _EMT_PARAMETERS = None

__all__ = [
    "load_authentic_coordinates",
    "load_authentic_hessian",
    "compute_geometry_and_hessian",
    "get_atomic_masses",
    "get_fixture_with_provenance",
    "FIXTURE_DIRECTORY",
]

# --------------------------------------------------------------------------- constants
FIXTURE_DIRECTORY = Path(__file__).resolve().parent / "cochem_fixture_data" / "ab_initio"
_MANIFEST_SCHEMA = "cochem.geometry_fixture/1"

BOHR_IN_ANGSTROM = ase_units.Bohr
HARTREE_IN_EV = ase_units.Hartree
HESSIAN_AU_TO_EV_PER_A2 = HARTREE_IN_EV / (BOHR_IN_ANGSTROM * BOHR_IN_ANGSTROM)

# SCF / optimisation / finite-difference settings (atomic units)
_SCF_MAX_ITERATIONS = 300
_SCF_ENERGY_TOL = 1e-11
_SCF_COMMUTATOR_TOL = 1e-8
_DIIS_DEPTH = 8
_OVERLAP_EIGEN_MIN = 1e-8
_BOYS_SMALL_T = 1e-10
_GRADIENT_STEP_BOHR = 1e-3
_HESSIAN_STEP_BOHR = 5e-3
_OPT_GTOL = 1e-6
_OPT_MAX_ITER = 400
_OPT_ACCEPT_MAX_GRADIENT = 1e-4
_MINIMUM_CURVATURE_AU = 1e-3
_NEGATIVE_CURVATURE_TOL_AU = 1e-3

# validation
_HESSIAN_SYMMETRY_ATOL = 1e-5
_MIN_INTERATOMIC_DISTANCE_A = 0.5
_MIN_COORDINATE_VARIANCE = 1e-6

# EMT fallback
_EMT_FMAX = 1e-4
_EMT_MAX_STEPS = 5000
_EMT_FD_DELTA = 0.01

_NAME_PATTERN = re.compile(r"[A-Za-z0-9_\-]+")

# 3-21G basis set in NWChem format (Basis Set Exchange). These are published
# basis-set parameters (Gaussian exponents and contraction coefficients),
# not molecular coordinates.
_BASIS_3_21G_NWCHEM = """\
# 3-21G  J.S. Binkley, J.A. Pople, W.J. Hehre, J. Am. Chem. Soc. 102, 939 (1980)
H    S
      5.4471780              0.1562850
      0.8245470              0.9046910
H    S
      0.1831920              1.0000000
C    S
    172.2560000              0.0617669
     25.9109000              0.3587940
      5.5333500              0.7007130
C    SP
      3.6649800             -0.3958970              0.2364600
      0.7705450              1.2158400              0.8606190
C    SP
      0.1958570              1.0000000              1.0000000
N    S
    242.7660000              0.0598657
     36.4851000              0.3529550
      7.8144900              0.7065130
N    SP
      5.4252200             -0.4133010              0.2379720
      1.1491500              1.2244200              0.8589530
N    SP
      0.2832050              1.0000000              1.0000000
O    S
    322.0370000              0.0592394
     48.4308000              0.3515000
     10.4206000              0.7076580
O    SP
      7.4029400             -0.4044530              0.2445860
      1.5762000              1.2215600              0.8539550
O    SP
      0.3736840              1.0000000              1.0000000
"""

_P_POWERS = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


# =========================================================================== masses
def _normalize_symbol(symbol: str) -> str:
    text = str(symbol).strip()
    if not text or not text.isalpha():
        raise ValueError(f"invalid element symbol {symbol!r}")
    return text[:1].upper() + text[1:].lower()


@functools.lru_cache(maxsize=None)
def _standard_atomic_weight(symbol: str) -> float:
    mass = mendeleev_element(symbol).mass
    if mass is None:
        raise ValueError(f"mendeleev provides no standard atomic weight for {symbol}")
    return float(mass)


@functools.lru_cache(maxsize=None)
def _isotopic_mass(symbol: str, mass_number: int) -> float:
    for isotope in mendeleev_element(symbol).isotopes:
        if int(isotope.mass_number) == mass_number:
            if isotope.mass is None:
                raise ValueError(f"mendeleev has no mass for {mass_number}{symbol}")
            return float(isotope.mass)
    raise ValueError(f"isotope {mass_number}{symbol} is not known to mendeleev")


def get_atomic_masses(symbols, mass_numbers: Optional[Sequence[Optional[int]]] = None) -> np.ndarray:
    """Return atomic masses (amu) aligned with ``symbols``, resolved via mendeleev.

    ``mass_numbers`` optionally selects specific isotopes per atom (``None`` entries
    fall back to the standard atomic weight ``mendeleev.element(symbol).mass``).
    """
    if isinstance(symbols, Atoms):
        symbols = symbols.get_chemical_symbols()
    if isinstance(symbols, str):
        symbols = [symbols]
    normalized = [_normalize_symbol(s) for s in symbols]
    if mass_numbers is None:
        masses = [_standard_atomic_weight(s) for s in normalized]
    else:
        numbers = list(mass_numbers)
        if len(numbers) != len(normalized):
            raise ValueError("mass_numbers must align one-to-one with symbols")
        masses = [
            _standard_atomic_weight(s) if m is None else _isotopic_mass(s, int(m))
            for s, m in zip(normalized, numbers)
        ]
    return np.array(masses, dtype=np.float64)


# =========================================================================== hashing / IO
def _sha256_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array, dtype=np.float64).tobytes()).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_array_file(path: Path, key: Optional[str]) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return np.array(np.load(path, allow_pickle=False))
    if suffix == ".npz":
        with np.load(path, allow_pickle=False) as archive:
            if key not in archive.files:
                raise KeyError(f"dataset {key!r} missing from {path}")
            return np.array(archive[key])
    if suffix in (".h5", ".hdf5"):
        try:
            import h5py
        except ImportError as exc:
            raise ImportError(
                f"h5py is required to read HDF5 fixture {path}; install it with 'pip install h5py'"
            ) from exc
        with h5py.File(path, "r") as handle:
            if key not in handle:
                raise KeyError(f"dataset {key!r} missing from {path}")
            return np.array(handle[key][()])
    raise ValueError(f"unsupported fixture file format: {path.suffix}")


def _atomic_write_bytes(target: Path, writer) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            writer(handle)
        os.replace(tmp_name, target)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def _package_version(name: str) -> str:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


# =========================================================================== validation
def _validate_coordinates(coords: np.ndarray, label: str) -> np.ndarray:
    coords = np.ascontiguousarray(coords, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] != 3 or coords.shape[0] < 1:
        raise ValueError(f"{label}: coordinates must have shape (N, 3), got {coords.shape}")
    if not np.all(np.isfinite(coords)):
        raise ValueError(f"{label}: coordinates contain non-finite values")
    if coords.shape[0] > 1:
        if float(np.max(np.var(coords, axis=0))) < _MIN_COORDINATE_VARIANCE:
            raise ValueError(f"{label}: coordinates are spatially degenerate")
        dist = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=-1)
        upper = dist[np.triu_indices(coords.shape[0], k=1)]
        if float(upper.min()) < _MIN_INTERATOMIC_DISTANCE_A:
            raise ValueError(
                f"{label}: minimum interatomic distance {upper.min():.4f} A is unphysical"
            )
    return coords


def _validate_hessian(hessian: np.ndarray, n_atoms: int, label: str):
    hessian = np.ascontiguousarray(hessian, dtype=np.float64)
    if hessian.ndim != 2 or hessian.shape[0] != hessian.shape[1]:
        raise ValueError(f"{label}: Hessian must be a square 2-D matrix, got {hessian.shape}")
    if hessian.shape[0] != 3 * n_atoms:
        raise ValueError(f"{label}: Hessian shape {hessian.shape} does not match 3N = {3 * n_atoms}")
    if not np.all(np.isfinite(hessian)):
        raise ValueError(f"{label}: Hessian contains non-finite entries")
    if not np.allclose(hessian, hessian.T, rtol=0.0, atol=_HESSIAN_SYMMETRY_ATOL):
        asym = float(np.max(np.abs(hessian - hessian.T)))
        raise ValueError(f"{label}: Hessian is not symmetric (max |H - H^T| = {asym:.3e})")
    eigenvalues = np.linalg.eigvalsh(hessian)
    if not np.all(np.isfinite(eigenvalues)):
        raise ValueError(f"{label}: Hessian eigenvalues are not finite")
    return hessian, eigenvalues


def _is_linear(coords: np.ndarray) -> bool:
    if coords.shape[0] < 3:
        return coords.shape[0] == 2
    centered = coords - coords.mean(axis=0)
    return int(np.linalg.matrix_rank(centered, tol=1e-3)) <= 1


def _vibrational_mode_count(coords: np.ndarray) -> int:
    n = coords.shape[0]
    if n == 1:
        return 0
    return 3 * n - (5 if _is_linear(coords) else 6)


def _harmonic_wavenumbers(hessian_ev_a2: np.ndarray, masses_amu: np.ndarray, n_vib: int) -> np.ndarray:
    inv_sqrt_m = 1.0 / np.sqrt(np.repeat(masses_amu, 3))
    weighted = hessian_ev_a2 * np.outer(inv_sqrt_m, inv_sqrt_m)
    lam = np.linalg.eigvalsh(0.5 * (weighted + weighted.T))
    top = lam[lam.size - n_vib:] if n_vib > 0 else lam[:0]
    si = top * ase_units._e / (ase_units._amu * 1e-20)
    omega = np.sign(si) * np.sqrt(np.abs(si))
    return omega / (2.0 * np.pi * ase_units._c * 100.0)


# =========================================================================== basis sets
@functools.lru_cache(maxsize=None)
def _basis_library(text: str = _BASIS_3_21G_NWCHEM):
    library = {}
    current = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0][0].isalpha():
            if len(parts) < 2:
                raise ValueError(f"malformed basis header: {raw!r}")
            current = (parts[1].upper(), [])
            library.setdefault(_normalize_symbol(parts[0]), []).append(current)
        else:
            if current is None:
                raise ValueError("basis data line encountered before any shell header")
            current[1].append(tuple(float(value) for value in parts))
    return {sym: tuple((kind, tuple(rows)) for kind, rows in shells) for sym, shells in library.items()}


def _double_factorial(n: int) -> int:
    return math.prod(range(n, 0, -2))


def _primitive_norm(alpha: float, powers) -> float:
    total = sum(powers)
    denominator = math.sqrt(math.prod(_double_factorial(2 * l - 1) for l in powers))
    return (2.0 * alpha / math.pi) ** 0.75 * (4.0 * alpha) ** (0.5 * total) / denominator


@dataclass(frozen=True)
class _PrimitiveBasis:
    alpha: np.ndarray
    atom_index: np.ndarray
    powers: np.ndarray
    coefficient: np.ndarray
    owner: np.ndarray
    n_functions: int


def _build_basis(symbols: Sequence[str], library) -> _PrimitiveBasis:
    alpha, atom_index, powers, coefficient, owner = [], [], [], [], []
    n_functions = 0
    for atom, symbol in enumerate(symbols):
        if symbol not in library:
            raise ValueError(
                f"element {symbol} is not covered by the 3-21G basis library "
                f"(supported: {sorted(library)})"
            )
        for kind, rows in library[symbol]:
            components = []
            if kind in ("S", "SP"):
                components.append(((0, 0, 0), 1))
            if kind == "SP":
                components.extend((pw, 2) for pw in _P_POWERS)
            elif kind == "P":
                components.extend((pw, 1) for pw in _P_POWERS)
            if not components:
                raise ValueError(f"unsupported shell type {kind!r} for {symbol}")
            for pw, column in components:
                for row in rows:
                    if len(row) <= column:
                        raise ValueError(f"basis row {row} lacks coefficient column {column}")
                    alpha.append(row[0])
                    atom_index.append(atom)
                    powers.append(pw)
                    coefficient.append(row[column] * _primitive_norm(row[0], pw))
                    owner.append(n_functions)
                n_functions += 1
    return _PrimitiveBasis(
        alpha=np.array(alpha, dtype=np.float64),
        atom_index=np.array(atom_index, dtype=np.intp),
        powers=np.array(powers, dtype=np.int64).reshape(-1, 3),
        coefficient=np.array(coefficient, dtype=np.float64),
        owner=np.array(owner, dtype=np.intp),
        n_functions=n_functions,
    )


# =========================================================================== McMurchie-Davidson integrals
def _boys(nmax: int, t_values: np.ndarray):
    t_values = np.asarray(t_values, dtype=np.float64)
    tiny = t_values < _BOYS_SMALL_T
    safe = np.where(tiny, 1.0, t_values)
    out = []
    for n in range(nmax + 1):
        a = n + 0.5
        regular = gamma_function(a) * gammainc(a, safe) / (2.0 * safe ** a)
        series = 1.0 / (2 * n + 1) - t_values / (2 * n + 3)
        out.append(np.where(tiny, series, regular))
    return out


def _hermite_indices(total_max: int, axis_max: int):
    return [
        (t, u, v)
        for t in range(axis_max + 1)
        for u in range(axis_max + 1)
        for v in range(axis_max + 1)
        if t + u + v <= total_max
    ]


def _raise_hermite(previous, half_inv_p, shift):
    top = len(previous)
    raised = []
    for t in range(top + 1):
        parts = []
        if t >= 1:
            parts.append(half_inv_p * previous[t - 1])
        if t < top:
            parts.append(shift * previous[t])
        if t + 1 < top:
            parts.append((t + 1) * previous[t + 1])
        raised.append(functools.reduce(np.add, parts))
    return raised


def _hermite_tables(imax: int, jmax: int, a, b, xab):
    p = a + b
    half_inv_p = 0.5 / p
    xpa = -(b / p) * xab
    xpb = (a / p) * xab
    tables = {(0, 0): [np.exp(-(a * b / p) * xab * xab)]}
    for i in range(imax + 1):
        if i > 0:
            tables[(i, 0)] = _raise_hermite(tables[(i - 1, 0)], half_inv_p, xpa)
        for j in range(1, jmax + 1):
            tables[(i, j)] = _raise_hermite(tables[(i, j - 1)], half_inv_p, xpb)
    return tables


def _gather(table, la, lb, t: int):
    conditions, choices = [], []
    for (i, j), coeffs in table.items():
        if t < len(coeffs):
            conditions.append((la == i) & (lb == j))
            choices.append(coeffs[t])
    return np.select(conditions, choices, default=0.0)


def _hermite_expansion(tables, la, lb, tmax: int):
    return [
        np.stack([_gather(tables[d], la[:, d], lb[:, d], t) for t in range(tmax + 1)], axis=1)
        for d in range(3)
    ]


def _hermite_coulomb(order: int, alpha, x, y, z):
    boys = _boys(order, alpha * (x * x + y * y + z * z))
    memo = {}

    def r(t, u, v, n):
        key = (t, u, v, n)
        cached = memo.get(key)
        if cached is not None:
            return cached
        if t == 0 and u == 0 and v == 0:
            value = (-2.0 * alpha) ** n * boys[n]
        elif t > 0:
            value = x * r(t - 1, u, v, n + 1)
            if t > 1:
                value = value + (t - 1) * r(t - 2, u, v, n + 1)
        elif u > 0:
            value = y * r(t, u - 1, v, n + 1)
            if u > 1:
                value = value + (u - 1) * r(t, u - 2, v, n + 1)
        else:
            value = z * r(t, u, v - 1, n + 1)
            if v > 1:
                value = value + (v - 1) * r(t, u, v - 2, n + 1)
        memo[key] = value
        return value

    return {(t, u, v): r(t, u, v, 0) for t, u, v in _hermite_indices(order, order)}


def _pair_setup(basis: _PrimitiveBasis, centers: np.ndarray, ii, jj, jextra: int):
    a = basis.alpha[ii]
    b = basis.alpha[jj]
    p = a + b
    center_a = centers[ii]
    center_b = centers[jj]
    pair_center = (a[:, None] * center_a + b[:, None] * center_b) / p[:, None]
    lmax = int(basis.powers.max())
    tables = [
        _hermite_tables(lmax, lmax + jextra, a, b, center_a[:, d] - center_b[:, d]) for d in range(3)
    ]
    return a, b, p, pair_center, tables


def _one_electron_primitives(basis: _PrimitiveBasis, centers, nuclei, charges):
    n = basis.alpha.size
    flat = np.arange(n * n)
    ii = flat // n
    jj = flat % n
    _, b, p, pair_center, tables = _pair_setup(basis, centers, ii, jj, 2)
    la = basis.powers[ii]
    lb = basis.powers[jj]
    root = np.sqrt(np.pi / p)
    s1, t1 = [], []
    for d in range(3):
        li = la[:, d]
        lj = lb[:, d]
        s_ij = _gather(tables[d], li, lj, 0) * root
        s_up = _gather(tables[d], li, lj + 2, 0) * root
        s_dn = _gather(tables[d], li, lj - 2, 0) * root
        s1.append(s_ij)
        t1.append(-2.0 * b * b * s_up + b * (2 * lj + 1) * s_ij - 0.5 * lj * (lj - 1) * s_dn)
    overlap = s1[0] * s1[1] * s1[2]
    kinetic = t1[0] * s1[1] * s1[2] + s1[0] * t1[1] * s1[2] + s1[0] * s1[1] * t1[2]

    lmax = int(basis.powers.max())
    ltot = int(basis.powers.sum(axis=1).max())
    herm = _hermite_indices(2 * ltot, 2 * lmax)
    ex = _hermite_expansion(tables, la, lb, 2 * lmax)
    terms = []
    for charge, nucleus in zip(charges, nuclei):
        rpc = pair_center - nucleus
        coulomb = _hermite_coulomb(2 * ltot, p, rpc[:, 0], rpc[:, 1], rpc[:, 2])
        acc = functools.reduce(
            np.add,
            (ex[0][:, t] * ex[1][:, u] * ex[2][:, v] * coulomb[(t, u, v)] for t, u, v in herm),
        )
        terms.append(-float(charge) * (2.0 * np.pi / p) * acc)
    potential = functools.reduce(np.add, terms)
    return overlap.reshape(n, n), kinetic.reshape(n, n), potential.reshape(n, n)


def _electron_repulsion_primitives(basis: _PrimitiveBasis, centers):
    n = basis.alpha.size
    ii, jj = np.triu_indices(n)
    _, _, p, pair_center, tables = _pair_setup(basis, centers, ii, jj, 0)
    la = basis.powers[ii]
    lb = basis.powers[jj]
    lmax = int(basis.powers.max())
    ltot = int(basis.powers.sum(axis=1).max())
    ex = _hermite_expansion(tables, la, lb, 2 * lmax)
    herm = _hermite_indices(2 * ltot, 2 * lmax)
    bra = np.stack([ex[0][:, t] * ex[1][:, u] * ex[2][:, v] for t, u, v in herm], axis=1)
    parity = np.array([-1.0 if (t + u + v) % 2 else 1.0 for t, u, v in herm])
    ket = bra * parity
    pp = p[:, None]
    qq = p[None, :]
    reduced = pp * qq / (pp + qq)
    rpq = pair_center[:, None, :] - pair_center[None, :, :]
    coulomb = _hermite_coulomb(4 * ltot, reduced, rpq[..., 0], rpq[..., 1], rpq[..., 2])
    acc = functools.reduce(
        np.add,
        (
            np.outer(bra[:, h1], ket[:, h2]) * coulomb[(t1 + t2, u1 + u2, v1 + v2)]
            for h1, (t1, u1, v1) in enumerate(herm)
            for h2, (t2, u2, v2) in enumerate(herm)
        ),
    )
    pair_eri = 2.0 * np.pi ** 2.5 / (pp * qq * np.sqrt(pp + qq)) * acc
    position = {(i, j): k for k, (i, j) in enumerate(zip(ii.tolist(), jj.tolist()))}
    pair_index = np.array(
        [[position[(min(i, j), max(i, j))] for j in range(n)] for i in range(n)], dtype=np.intp
    )
    return pair_eri[pair_index[:, :, None, None], pair_index[None, None, :, :]]


# =========================================================================== RHF engine
@dataclass
class _SCFResult:
    energy: float
    density: np.ndarray
    iterations: int
    max_commutator: float
    orbital_energies: np.ndarray


def _diis_extrapolate(focks, errors):
    n = len(errors)
    gram = [[float(np.vdot(ei, ej)) for ej in errors] for ei in errors]
    scale = max(abs(gram[k][k]) for k in range(n))
    if scale <= 0.0:
        return focks[-1]
    rows = [[value / scale for value in row] + [-1.0] for row in gram]
    rows.append([-1.0] * n + [0.0])
    rhs = [0.0] * n + [-1.0]
    solution = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return np.tensordot(solution[:n], np.stack(focks), axes=1)


class _RestrictedHartreeFock:
    """Closed-shell RHF/3-21G with McMurchie-Davidson integrals (atomic units)."""

    method = "RHF/3-21G"

    def __init__(self, symbols: Sequence[str]):
        self.symbols = [_normalize_symbol(s) for s in symbols]
        self.basis = _build_basis(self.symbols, _basis_library())
        self.charges = Atoms(symbols=self.symbols).get_atomic_numbers().astype(np.float64)
        n_electrons = int(round(float(self.charges.sum())))
        if n_electrons % 2:
            raise ValueError("RHF requires a closed-shell (even-electron) neutral molecule")
        self.n_electrons = n_electrons
        self.n_occupied = n_electrons // 2
        self._density = None
        self.energy_evaluations = 0
        self.last_result: Optional[_SCFResult] = None

    # ------------------------------------------------------------------ integrals
    def _integrals(self, coords_bohr: np.ndarray):
        centers = coords_bohr[self.basis.atom_index]
        s_p, t_p, v_p = _one_electron_primitives(self.basis, centers, coords_bohr, self.charges)
        raw = (self.basis.owner[:, None] == np.arange(self.basis.n_functions)[None, :]) * \
            self.basis.coefficient[:, None]
        norms = np.sqrt(np.diag(raw.T @ s_p @ raw))
        contraction = raw / norms
        overlap = contraction.T @ s_p @ contraction
        core = contraction.T @ (t_p + v_p) @ contraction
        g_prim = _electron_repulsion_primitives(self.basis, centers)
        eri = np.einsum("ijkl,ia,jb,kc,ld->abcd", g_prim, contraction, contraction,
                        contraction, contraction, optimize=True)
        return 0.5 * (overlap + overlap.T), 0.5 * (core + core.T), eri

    def _nuclear_repulsion(self, coords_bohr: np.ndarray) -> float:
        n = coords_bohr.shape[0]
        if n < 2:
            return 0.0
        iu = np.triu_indices(n, k=1)
        dist = np.linalg.norm(coords_bohr[:, None, :] - coords_bohr[None, :, :], axis=-1)[iu]
        return float(np.sum(self.charges[iu[0]] * self.charges[iu[1]] / dist))

    # ------------------------------------------------------------------ SCF
    def scf(self, coords_bohr: np.ndarray) -> _SCFResult:
        coords_bohr = np.asarray(coords_bohr, dtype=np.float64).reshape(len(self.symbols), 3)
        overlap, core, eri = self._integrals(coords_bohr)
        e_nuc = self._nuclear_repulsion(coords_bohr)
        s_val, s_vec = np.linalg.eigh(overlap)
        if float(s_val.min()) <= _OVERLAP_EIGEN_MIN:
            raise ValueError("basis is numerically linearly dependent at this geometry")
        ortho = (s_vec / np.sqrt(s_val)) @ s_vec.T
        nocc = self.n_occupied

        def density_from(fock):
            orbital_energies, coeff_prime = np.linalg.eigh(ortho.T @ fock @ ortho)
            coeff = ortho @ coeff_prime
            occupied = coeff[:, :nocc]
            return occupied @ occupied.T, orbital_energies

        def fock_from(density):
            coulomb = np.einsum("pqrs,rs->pq", eri, density, optimize=True)
            exchange = np.einsum("prqs,rs->pq", eri, density, optimize=True)
            return core + 2.0 * coulomb - exchange

        if self._density is not None and self._density.shape == core.shape:
            density = self._density
            orbital_energies = np.linalg.eigvalsh(ortho.T @ core @ ortho)
        else:
            density, orbital_energies = density_from(core)

        focks, errors = [], []
        previous = None
        for iteration in range(1, _SCF_MAX_ITERATIONS + 1):
            fock = fock_from(density)
            e_elec = float(np.sum(density * (core + fock)))
            commutator = ortho.T @ (fock @ density @ overlap - overlap @ density @ fock) @ ortho
            max_comm = float(np.max(np.abs(commutator)))
            if previous is not None and abs(e_elec - previous) < _SCF_ENERGY_TOL \
                    and max_comm < _SCF_COMMUTATOR_TOL:
                electrons = 2.0 * float(np.trace(density @ overlap))
                if abs(electrons - self.n_electrons) > 1e-6:
                    raise RuntimeError(
                        f"SCF density integrates to {electrons:.8f} electrons, expected {self.n_electrons}"
                    )
                self._density = density
                result = _SCFResult(e_elec + e_nuc, density, iteration, max_comm, orbital_energies)
                self.last_result = result
                self.energy_evaluations += 1
                return result
            previous = e_elec
            focks.append(fock)
            errors.append(commutator)
            if len(focks) > _DIIS_DEPTH:
                focks.pop(0)
                errors.pop(0)
            extrapolated = _diis_extrapolate(focks, errors) if len(focks) >= 2 else fock
            density, orbital_energies = density_from(extrapolated)
        raise RuntimeError(f"RHF SCF failed to converge in {_SCF_MAX_ITERATIONS} iterations")

    # ------------------------------------------------------------------ derivatives
    def energy(self, x_bohr) -> float:
        return self.scf(np.asarray(x_bohr, dtype=np.float64)).energy

    def gradient(self, x_bohr) -> np.ndarray:
        x = np.asarray(x_bohr, dtype=np.float64).ravel()
        h = _GRADIENT_STEP_BOHR
        components = []
        for i in range(x.size):
            displaced = x.copy()
            displaced[i] += h
            e_plus = self.energy(displaced)
            displaced[i] -= 2.0 * h
            e_minus = self.energy(displaced)
            components.append((e_plus - e_minus) / (2.0 * h))
        return np.array(components, dtype=np.float64)

    def energy_and_gradient(self, x_bohr):
        x = np.asarray(x_bohr, dtype=np.float64).ravel()
        e0 = self.energy(x)
        return e0, self.gradient(x)

    def hessian(self, x_bohr) -> np.ndarray:
        x = np.asarray(x_bohr, dtype=np.float64).ravel()
        h = _HESSIAN_STEP_BOHR
        n = x.size
        e0 = self.energy(x)

        def displaced(*moves):
            y = x.copy()
            for index, sign in moves:
                y[index] += sign * h
            return self.energy(y)

        plus = [displaced((i, 1.0)) for i in range(n)]
        minus = [displaced((i, -1.0)) for i in range(n)]
        diagonal = [(plus[i] - 2.0 * e0 + minus[i]) / (h * h) for i in range(n)]
        off = {}
        for i in range(n):
            for j in range(i + 1, n):
                e_pp = displaced((i, 1.0), (j, 1.0))
                e_pm = displaced((i, 1.0), (j, -1.0))
                e_mp = displaced((i, -1.0), (j, 1.0))
                e_mm = displaced((i, -1.0), (j, -1.0))
                off[(i, j)] = (e_pp - e_pm - e_mp + e_mm) / (4.0 * h * h)
        rows = [
            [diagonal[i] if i == j else off[(min(i, j), max(i, j))] for j in range(n)]
            for i in range(n)
        ]
        return np.array(rows, dtype=np.float64)


# =========================================================================== fixture generation
def _canonical_name(name: str) -> str:
    text = str(name).strip()
    if not _NAME_PATTERN.fullmatch(text):
        raise ValueError(f"invalid fixture name {name!r}")
    return text


def _generate_ab_initio_fixture(name: str, directory: Path) -> Path:
    try:
        guess = ase_molecule(name)
    except KeyError as exc:
        raise FileNotFoundError(
            f"no ab-initio fixture manifest for {name!r} in {directory} and ASE's G2 database "
            f"has no starting structure to compute one"
        ) from exc
    symbols = guess.get_chemical_symbols()
    engine = _RestrictedHartreeFock(symbols)
    x0 = guess.get_positions().ravel() / BOHR_IN_ANGSTROM
    initial_energy = engine.energy(x0)

    options = {"gtol": _OPT_GTOL, "maxiter": _OPT_MAX_ITER}
    result = minimize(engine.energy_and_gradient, x0, jac=True, method="BFGS", options=options)
    x_opt = np.asarray(result.x, dtype=np.float64)
    energy, gradient = engine.energy_and_gradient(x_opt)
    scf_final = engine.scf(x_opt)
    max_gradient = float(np.max(np.abs(gradient)))
    if max_gradient > _OPT_ACCEPT_MAX_GRADIENT:
        raise RuntimeError(
            f"RHF geometry optimisation of {name} did not converge (max |g| = {max_gradient:.3e} Eh/bohr)"
        )

    hessian_au = engine.hessian(x_opt)
    coords_bohr = x_opt.reshape(-1, 3)
    coords_angstrom = np.ascontiguousarray(coords_bohr * BOHR_IN_ANGSTROM, dtype=np.float64)
    eigen = np.linalg.eigvalsh(hessian_au)
    n_vib = _vibrational_mode_count(coords_angstrom)
    if n_vib and float(np.sort(eigen)[eigen.size - n_vib]) < _MINIMUM_CURVATURE_AU:
        raise RuntimeError(f"optimised {name} geometry is not a local minimum (eigenvalues {eigen})")
    if float(eigen.min()) < -_NEGATIVE_CURVATURE_TOL_AU:
        raise RuntimeError(f"optimised {name} geometry has negative curvature (eigenvalues {eigen})")

    masses = get_atomic_masses(symbols)
    wavenumbers = _harmonic_wavenumbers(hessian_au * HESSIAN_AU_TO_EV_PER_A2, masses, n_vib)

    npz_path = directory / f"{name}.npz"

    def write_npz(handle):
        np.savez(
            handle,
            symbols=np.array(symbols),
            atomic_numbers=engine.charges.astype(np.int64),
            coordinates_angstrom=coords_angstrom,
            hessian_hartree_per_bohr2=hessian_au,
            gradient_hartree_per_bohr=gradient.reshape(-1, 3),
        )

    _atomic_write_bytes(npz_path, write_npz)

    provenance = {
        "method": engine.method,
        "theory": "closed-shell restricted Hartree-Fock (ab initio)",
        "basis": "3-21G",
        "basis_reference": "J.S. Binkley, J.A. Pople, W.J. Hehre, J. Am. Chem. Soc. 102, 939 (1980)",
        "code": "cochem_geometry_fixtures RHF engine (McMurchie-Davidson integrals, DIIS SCF)",
        "initial_geometry_source": "ase.build.molecule G2 database (MP2/6-31G* reference structure)",
        "optimizer": "scipy.optimize.minimize BFGS with central-difference RHF gradients",
        "optimizer_iterations": int(result.nit),
        "gradient_step_bohr": _GRADIENT_STEP_BOHR,
        "hessian_method": "central second differences of converged RHF energies",
        "hessian_step_bohr": _HESSIAN_STEP_BOHR,
        "initial_energy_hartree": float(initial_energy),
        "energy_hartree": float(energy),
        "energy_eV": float(energy) * HARTREE_IN_EV,
        "max_gradient_hartree_per_bohr": max_gradient,
        "rms_gradient_hartree_per_bohr": float(np.sqrt(np.mean(gradient * gradient))),
        "scf_iterations": int(scf_final.iterations),
        "scf_max_commutator": float(scf_final.max_commutator),
        "n_basis_functions": int(engine.basis.n_functions),
        "n_primitives": int(engine.basis.alpha.size),
        "harmonic_wavenumbers_cm-1": [float(w) for w in wavenumbers],
        "energy_evaluations": int(engine.energy_evaluations),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "versions": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "ase": ase.__version__,
            "mendeleev": _package_version("mendeleev"),
        },
    }
    manifest = {
        "schema": _MANIFEST_SCHEMA,
        "name": name,
        "symbols": list(symbols),
        "coordinates": {
            "path": npz_path.name,
            "key": "coordinates_angstrom",
            "units": "angstrom",
            "sha256": _sha256_array(coords_angstrom),
        },
        "hessian": {
            "path": npz_path.name,
            "key": "hessian_hartree_per_bohr2",
            "units": "hartree/bohr^2",
            "sha256": _sha256_array(hessian_au),
        },
        "file_sha256": {npz_path.name: _sha256_file(npz_path)},
        "provenance": provenance,
    }
    manifest_path = directory / f"{name}.json"
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(manifest_path, lambda handle: handle.write(payload))
    return manifest_path


@functools.lru_cache(maxsize=None)
def _load_record(name: str):
    name = _canonical_name(name)
    directory = FIXTURE_DIRECTORY
    manifest_path = directory / f"{name}.json"
    if not manifest_path.exists():
        _generate_ab_initio_fixture(name, directory)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != _MANIFEST_SCHEMA:
        raise ValueError(f"{manifest_path}: unsupported manifest schema {manifest.get('schema')!r}")

    file_hashes = manifest.get("file_sha256", {})
    verified = {}
    for section in ("coordinates", "hessian"):
        entry = manifest[section]
        rel = entry["path"]
        path = (directory / rel).resolve()
        if rel not in file_hashes:
            raise ValueError(f"{manifest_path}: no SHA-256 recorded for {rel}")
        if rel not in verified:
            actual = _sha256_file(path)
            if actual != file_hashes[rel]:
                raise ValueError(f"{path}: SHA-256 mismatch (file tampered or corrupted)")
            verified[rel] = actual

    symbols = [str(s) for s in manifest["symbols"]]

    coord_entry = manifest["coordinates"]
    raw_coords = _read_array_file(directory / coord_entry["path"], coord_entry.get("key"))
    if _sha256_array(raw_coords) != coord_entry["sha256"]:
        raise ValueError(f"{name}: coordinate dataset hash mismatch")
    coord_units = str(coord_entry.get("units", "angstrom")).lower()
    if coord_units == "bohr":
        raw_coords = raw_coords * BOHR_IN_ANGSTROM
    elif coord_units != "angstrom":
        raise ValueError(f"{name}: unsupported coordinate units {coord_units!r}")
    coords = _validate_coordinates(raw_coords, name)
    if coords.shape[0] != len(symbols):
        raise ValueError(f"{name}: {coords.shape[0]} coordinates for {len(symbols)} symbols")

    hess_entry = manifest["hessian"]
    raw_hessian = _read_array_file(directory / hess_entry["path"], hess_entry.get("key"))
    if _sha256_array(raw_hessian) != hess_entry["sha256"]:
        raise ValueError(f"{name}: Hessian dataset hash mismatch")
    hess_units = str(hess_entry.get("units", "hartree/bohr^2")).lower()
    if hess_units == "hartree/bohr^2":
        hessian = np.asarray(raw_hessian, dtype=np.float64) * HESSIAN_AU_TO_EV_PER_A2
    elif hess_units == "ev/angstrom^2":
        hessian = np.asarray(raw_hessian, dtype=np.float64)
    else:
        raise ValueError(f"{name}: unsupported Hessian units {hess_units!r}")
    hessian, eigenvalues = _validate_hessian(hessian, len(symbols), name)

    return {
        "name": name,
        "symbols": tuple(symbols),
        "coordinates": coords,
        "hessian": hessian,
        "eigenvalues": eigenvalues,
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "file_sha256": dict(verified),
    }


# =========================================================================== public API
def load_authentic_coordinates(name: str) -> np.ndarray:
    """Return ab-initio equilibrium coordinates (Angstrom, float64, shape (N, 3))."""
    record = _load_record(_canonical_name(name))
    return np.ascontiguousarray(record["coordinates"].copy(), dtype=np.float64)


def load_authentic_hessian(name: str) -> np.ndarray:
    """Return the ab-initio Cartesian Hessian (eV/Angstrom^2, float64, shape (3N, 3N)).

    The matrix is validated (square, 3N, finite, H = H^T, finite eigenvalues) but
    never symmetrised or otherwise repaired on load.
    """
    record = _load_record(_canonical_name(name))
    return np.ascontiguousarray(record["hessian"].copy(), dtype=np.float64)


def get_fixture_with_provenance(name: str, include_hessian: bool = True) -> dict:
    """Return coordinates (and Hessian) together with verifiable provenance metadata."""
    record = _load_record(_canonical_name(name))
    coords = np.ascontiguousarray(record["coordinates"].copy(), dtype=np.float64)
    symbols = list(record["symbols"])
    masses = get_atomic_masses(symbols)
    stored = record["manifest"]["provenance"]
    provenance = dict(stored)
    provenance.update(
        {
            "name": record["name"],
            "formula": Atoms(symbols=symbols).get_chemical_formula(mode="hill"),
            "atom_count": len(symbols),
            "symbols": symbols,
            "sha256": _sha256_array(coords),
            "coordinates_sha256": _sha256_array(coords),
            "coordinate_units": "angstrom",
            "hessian_sha256": _sha256_array(record["hessian"]),
            "hessian_units": "eV/angstrom^2",
            "file_sha256": dict(record["file_sha256"]),
            "source": record["manifest_path"],
            "masses_amu": [float(m) for m in masses],
            "mass_source": "mendeleev.element(symbol).mass",
        }
    )
    fixture = {
        "name": record["name"],
        "symbols": symbols,
        "coordinates": coords,
        "masses": masses,
        "provenance": provenance,
    }
    if include_hessian:
        fixture["hessian"] = np.ascontiguousarray(record["hessian"].copy(), dtype=np.float64)
    return fixture


def _emt_forces(symbols, positions, cell, pbc) -> np.ndarray:
    probe = Atoms(symbols=symbols, positions=positions, cell=cell, pbc=pbc)
    probe.calc = EMT()
    return probe.get_forces()


def _emt_numerical_hessian(symbols, positions, cell, pbc, delta: float) -> np.ndarray:
    columns = []
    for index in range(3 * len(symbols)):
        atom, axis = divmod(index, 3)
        plus = np.array(positions, dtype=np.float64)
        minus = np.array(positions, dtype=np.float64)
        plus[atom, axis] += delta
        minus[atom, axis] -= delta
        f_plus = _emt_forces(symbols, plus, cell, pbc)
        f_minus = _emt_forces(symbols, minus, cell, pbc)
        columns.append(-(f_plus - f_minus).ravel() / (2.0 * delta))
    hessian = np.stack(columns, axis=1)
    return 0.5 * (hessian + hessian.T)


def compute_geometry_and_hessian(
    atoms: Atoms,
    fmax: float = _EMT_FMAX,
    max_steps: int = _EMT_MAX_STEPS,
    delta: float = _EMT_FD_DELTA,
) -> dict:
    """Relax ``atoms`` with ASE EMT + BFGS and compute a central-difference Hessian.

    The caller's ``Atoms`` object is not modified. Returns a dict with the optimised
    ``atoms``, ``positions`` (Angstrom), ``hessian`` (eV/Angstrom^2, (3N, 3N)),
    ``energy`` (eV), ``max_force`` (eV/Angstrom) and ``provenance``.
    """
    if not isinstance(atoms, Atoms):
        raise TypeError("compute_geometry_and_hessian expects an ase.Atoms instance")
    symbols = atoms.get_chemical_symbols()
    if not symbols:
        raise ValueError("cannot optimise an empty Atoms object")
    if _EMT_PARAMETERS is not None:
        unsupported = sorted(set(symbols) - set(_EMT_PARAMETERS))
        if unsupported:
            raise ValueError(
                f"ASE EMT has no parameters for {unsupported}; supported elements: {sorted(_EMT_PARAMETERS)}"
            )
    cell = atoms.get_cell()
    pbc = atoms.get_pbc()
    work = Atoms(symbols=symbols, positions=atoms.get_positions(), cell=cell, pbc=pbc)
    work.calc = EMT()
    try:
        initial_energy = float(work.get_potential_energy())
    except (KeyError, NotImplementedError) as exc:
        raise ValueError(f"ASE EMT cannot evaluate system {symbols}: {exc}") from exc

    optimizer = BFGS(work, logfile=None)
    optimizer.run(fmax=fmax, steps=max_steps)
    forces = work.get_forces()
    max_force = float(np.max(np.linalg.norm(forces, axis=1)))
    if max_force > fmax * 1.0001:
        raise RuntimeError(
            f"EMT BFGS optimisation did not converge: max force {max_force:.3e} eV/A > {fmax:.1e}"
        )
    energy = float(work.get_potential_energy())
    positions = np.ascontiguousarray(work.get_positions(), dtype=np.float64)

    hessian = _emt_numerical_hessian(symbols, positions, cell, pbc, delta)
    hessian, eigenvalues = _validate_hessian(hessian, len(symbols), "EMT Hessian")

    masses = get_atomic_masses(symbols)
    optimized = Atoms(symbols=symbols, positions=positions, cell=cell, pbc=pbc, masses=masses)
    provenance = {
        "method": "ASE EMT (effective medium theory) with BFGS relaxation",
        "hessian_method": "central finite differences of EMT forces, symmetrised",
        "finite_difference_delta_angstrom": float(delta),
        "optimizer": "ase.optimize.BFGS",
        "optimizer_steps": int(optimizer.get_number_of_steps()),
        "fmax_eV_per_angstrom": float(fmax),
        "formula": optimized.get_chemical_formula(mode="hill"),
        "atom_count": len(symbols),
        "initial_energy_eV": initial_energy,
        "energy_eV": energy,
        "max_force_eV_per_angstrom": max_force,
        "max_gradient_eV_per_angstrom": float(np.max(np.abs(forces))),
        "hessian_units": "eV/angstrom^2",
        "sha256": _sha256_array(positions),
        "hessian_sha256": _sha256_array(hessian),
        "masses_amu": [float(m) for m in masses],
        "mass_source": "mendeleev.element(symbol).mass",
        "versions": {"ase": ase.__version__, "numpy": np.__version__},
    }
    return {
        "atoms": optimized,
        "positions": positions,
        "hessian": hessian,
        "eigenvalues": eigenvalues,
        "energy": energy,
        "max_force": max_force,
        "provenance": provenance,
    }


if __name__ == "__main__":  # manual (re)inspection of ab-initio fixtures
    import sys

    for fixture_name in sys.argv[1:] or ["H2O"]:
        data = get_fixture_with_provenance(fixture_name)
        print(json.dumps(data["provenance"], indent=2, sort_keys=True))
