#!/usr/bin/env python
"""CoChem command-line interface: ``cochem run`` and ``cochem spectral-predict``.

Task 20.110.2.

``cochem run``
    Validates a quantum-chemistry calculation deck (pydantic v2,
    ``extra='forbid'``), enforces the Method Matrix Sec. 8.3 rules
    (basis-function crossover engine rule, model-Hessian preconditioning,
    rejection of ``Calc_Hess true``), sizes ORCA ``%maxcore`` from the
    available RAM and dispatches the calculation.  External engines (ORCA,
    CFOUR, xTB) run as background subprocesses with ``CREATE_NO_WINDOW`` on
    Windows.  When a binary is absent the job falls back to a real in-process
    calculation (PySCF CPU, or ASE EMT when PySCF is not installed).  The
    engine that actually produced the numbers is always reported.

``cochem spectral-predict``
    Computes the mass-weighted inertia tensor from a geometry using
    ``mendeleev.element`` masses, gives the equilibrium rotational constants
    A_e, B_e, C_e (MHz) and Ray's asymmetry parameter kappa.  It writes
    Pickett ``.par``/``.var``/``.int`` decks and compiles a ``.cat``
    catalogue from the Watson A-reduced Hamiltonian eigenstates (I^r
    representation), bounded by ``--freq-max`` and ``--temperature``.

Exit codes: 0 success, 1 execution failure, 2 argument/schema error,
6 Method Matrix violation.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from mendeleev import element
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

try:  # optional acceleration for the partition-function sum
    from scipy.linalg import eigvalsh_tridiagonal as _eigvalsh_tridiagonal
except Exception:  # scipy absent or broken: dense numpy fallback is used
    _eigvalsh_tridiagonal = None

__all__ = [
    "CREATE_NO_WINDOW",
    "QuantumCalculationDeckConfig",
    "RoutingDecision",
    "RotorParameters",
    "build_parser",
    "calculate_orca_maxcore",
    "compute_inertia_rotational_constants",
    "compute_principal_moments",
    "compute_ray_asymmetry_parameter",
    "derive_basis_function_count",
    "enforce_method_matrix",
    "main",
    "predict_rotational_catalog",
    "route_calculation",
    "run_external_command",
    "synthesize_orca_deck",
]

LOGGER = logging.getLogger("cochem.cli")

# ---------------------------------------------------------------------------
# Exit codes and constants
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_METHOD_MATRIX = 6

CREATE_NO_WINDOW = 0x08000000  # Windows process creation flag (no console popup)

PLANCK_J_S = 6.62607015e-34  # exact (SI 2019)
BOLTZMANN_J_K = 1.380649e-23  # exact (SI 2019)
ATOMIC_MASS_UNIT_KG = 1.66053906892e-27  # CODATA 2022
ANGSTROM_M = 1.0e-10
# h / (8 pi^2 u A^2) expressed in MHz * amu * A^2
INERTIA_TO_MHZ = PLANCK_J_S / (8.0 * math.pi ** 2 * ATOMIC_MASS_UNIT_KG * ANGSTROM_M ** 2) * 1.0e-6
BOLTZMANN_MHZ_PER_K = BOLTZMANN_J_K / PLANCK_J_S * 1.0e-6
SPEED_OF_LIGHT_CM_S = 2.99792458e10
MHZ_PER_WAVENUMBER = SPEED_OF_LIGHT_CM_S * 1.0e-6
HARTREE_TO_EV = 27.211386245981  # CODATA 2022
HARTREE_TO_WAVENUMBER = 219474.6313632  # CODATA 2022
# JPL / Pickett integrated intensity constant: nm^2 MHz per (MHz Debye^2)
JPL_INTENSITY_CONSTANT = 4.16231e-5

CROSSOVER_BASIS_FUNCTIONS = 50  # Method Matrix Sec. 8.3
MAXCORE_FLOOR_MB = 256
MAXCORE_RAM_FRACTION = 0.75

ENGINE_CHOICES = ("auto", "orca", "cfour", "pyscf", "gpu4pyscf", "xtb", "crest")
TASK_CHOICES = ("sp", "opt", "freq", "opt_freq")
ALLOWED_HESSIAN_PRECONDITIONERS = {"XTB2": "XTB2", "LINDH": "Lindh"}

_CALC_HESS_TRUE_RE = re.compile(r"\bcalc_hess\s+true\b", re.IGNORECASE)
_INHESS_RE = re.compile(r"\binhess\s+([A-Za-z0-9_+-]+)", re.IGNORECASE)
_ELEMENT_SYMBOL_RE = re.compile(r"^[A-Z][a-z]?$")

SQRT2 = math.sqrt(2.0)

# Spectroscopy limits imposed by the fixed-width Pickett .cat format
CAT_FREQ_LIMIT_MHZ = 99999999.9999  # F13.4
CAT_ELOW_LIMIT_CM = 99999.9999  # F10.4
CAT_MAX_QN = 359  # I2 with Pickett letter encoding (A0 = 100 ... Z9 = 359)
PARTITION_J_CAP = 3000
LINE_STRENGTH_FLOOR = 1.0e-10
MIN_RESOLVABLE_FREQ_MHZ = 5.0e-5  # smallest frequency that prints non-zero in F13.4


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class CochemCliError(Exception):
    """Base error carrying a process exit code."""

    exit_code = EXIT_FAILURE


class CliUsageError(CochemCliError):
    """Invalid arguments or inconsistent input (exit code 2)."""

    exit_code = EXIT_USAGE


class MethodMatrixViolation(CochemCliError):
    """Method Matrix rule violation (exit code 6)."""

    exit_code = EXIT_METHOD_MATRIX


class ExternalEngineError(CochemCliError):
    """An engine was available but the calculation failed (exit code 1)."""

    exit_code = EXIT_FAILURE


class EngineUnavailable(Exception):
    """The requested backend cannot run on this host; try the next fallback."""


# ---------------------------------------------------------------------------
# Element data (mendeleev only - no embedded mass tables)
# ---------------------------------------------------------------------------
def _normalize_symbol(raw: str) -> str:
    match = re.match(r"^\s*([A-Za-z]{1,2})", str(raw))
    if not match:
        raise CliUsageError(f"cannot interpret atom label {raw!r} as an element symbol")
    token = match.group(1)
    symbol = token[0].upper() + token[1:].lower()
    if len(symbol) == 2:
        try:
            _lookup_element(symbol)
        except CliUsageError:
            symbol = symbol[0]
    return symbol


@lru_cache(maxsize=None)
def _lookup_element(symbol: str):
    try:
        return element(symbol)
    except Exception as exc:  # mendeleev raises ORM-specific errors for unknown symbols
        raise CliUsageError(f"unknown chemical element symbol {symbol!r}") from exc


@lru_cache(maxsize=None)
def atomic_mass(symbol: str) -> float:
    """Standard atomic weight (amu) from ``mendeleev.element(symbol).mass``."""
    value = _lookup_element(symbol).mass
    if value is None:
        raise CliUsageError(f"mendeleev has no atomic mass for {symbol!r}")
    return float(value)


@lru_cache(maxsize=None)
def atomic_number(symbol: str) -> int:
    return int(_lookup_element(symbol).atomic_number)


@lru_cache(maxsize=None)
def most_abundant_isotope_mass(symbol: str) -> Tuple[float, int]:
    """Mass (amu) and mass number of the most abundant isotope (mendeleev)."""
    el = _lookup_element(symbol)
    candidates = [iso for iso in el.isotopes if iso.abundance and iso.mass]
    if not candidates:
        raise CliUsageError(f"mendeleev lists no naturally abundant isotope for {symbol!r}")
    best = max(candidates, key=lambda iso: float(iso.abundance))
    return float(best.mass), int(best.mass_number)


# ---------------------------------------------------------------------------
# Calculation deck schema
# ---------------------------------------------------------------------------
class QuantumCalculationDeckConfig(BaseModel):
    """Validated quantum calculation deck (pydantic v2, unknown keys forbidden)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_uuid: str = Field(min_length=1, description="Unique calculation job identifier")
    engine: str = Field(default="auto", description="auto, orca, cfour, pyscf, gpu4pyscf, xtb, crest")
    symbols: List[str] = Field(min_length=1, description="Element symbols")
    coordinates_angstrom: List[List[float]] = Field(description="Cartesian coordinates (N x 3), Angstrom")
    charge: int = Field(default=0, description="Total molecular charge")
    multiplicity: int = Field(default=1, ge=1, description="Spin multiplicity 2S+1")
    method: str = Field(default="r2SCAN-3c", description="Electronic structure method")
    basis_set: str = Field(default="def2-TZVP", description="Orbital basis set")
    aux_basis_set: Optional[str] = Field(default=None, description="Auxiliary density-fitting basis")
    task_type: str = Field(default="sp", description="sp, opt, freq, opt_freq")
    grid_level: str = Field(default="defgrid2", description="Integration grid")
    in_hess: Optional[str] = Field(default="XTB2", description="Initial Hessian preconditioner")
    frozen_atoms: List[int] = Field(default_factory=list, description="Frozen atom indices (0-based)")
    counterpoise: bool = Field(default=False, description="Counterpoise correction flag")
    raw_deck: Optional[str] = Field(default=None, description="Raw input deck text to validate")

    @field_validator("engine")
    @classmethod
    def _normalise_engine(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in ENGINE_CHOICES:
            raise ValueError(f"engine must be one of {', '.join(ENGINE_CHOICES)}")
        return norm

    @field_validator("task_type")
    @classmethod
    def _normalise_task(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in TASK_CHOICES:
            raise ValueError(f"task_type must be one of {', '.join(TASK_CHOICES)}")
        return norm

    @field_validator("symbols")
    @classmethod
    def _normalise_symbols(cls, value: List[str]) -> List[str]:
        out = []
        for raw in value:
            token = str(raw).strip()
            symbol = token[:1].upper() + token[1:].lower()
            if not _ELEMENT_SYMBOL_RE.match(symbol):
                raise ValueError(f"invalid element symbol {raw!r}")
            try:
                _lookup_element(symbol)
            except CliUsageError as exc:
                raise ValueError(str(exc)) from exc
            out.append(symbol)
        return out

    @field_validator("job_uuid", "method", "basis_set", "grid_level")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _check_geometry(self) -> "QuantumCalculationDeckConfig":
        if len(self.coordinates_angstrom) != len(self.symbols):
            raise ValueError(
                f"coordinates_angstrom has {len(self.coordinates_angstrom)} rows "
                f"but {len(self.symbols)} symbols were given"
            )
        for i, row in enumerate(self.coordinates_angstrom):
            if len(row) != 3:
                raise ValueError(f"coordinate row {i} must have exactly 3 components")
            if not all(math.isfinite(x) for x in row):
                raise ValueError(f"coordinate row {i} contains non-finite values")
        n_atoms = len(self.symbols)
        if len(set(self.frozen_atoms)) != len(self.frozen_atoms):
            raise ValueError("frozen_atoms contains duplicate indices")
        for idx in self.frozen_atoms:
            if not 0 <= idx < n_atoms:
                raise ValueError(f"frozen atom index {idx} out of range 0..{n_atoms - 1}")
        return self


# ---------------------------------------------------------------------------
# Basis-function counting and the Sec. 8.3 crossover rule
# ---------------------------------------------------------------------------
def _basis_table_entry(basis_key: str, symbol: str) -> Optional[int]:
    """Spherical contracted-function counts for H/He and B-Ne (offline fallback)."""
    light = ("H", "He")
    second_row_p = ("B", "C", "N", "O", "F", "Ne")
    families = {
        "def2-svp": (5, 14),
        "def2-tzvp": (6, 31),
        "def2-tzvpp": (14, 31),
        "cc-pvdz": (5, 14),
        "cc-pvtz": (14, 30),
    }
    counts = families.get(basis_key)
    if counts is None:
        return None
    if symbol in light:
        if basis_key == "def2-tzvpp" and symbol == "He":
            return None
        return counts[0]
    if symbol in second_row_p:
        return counts[1]
    return None


def _pyscf_basis_count(symbols: Sequence[str], basis_set: str) -> int:
    from pyscf import gto  # imported lazily: heavy dependency

    atoms = [(sym, (1.5 * i, 0.0, 0.0)) for i, sym in enumerate(symbols)]
    n_electrons = sum(atomic_number(sym) for sym in symbols)
    mol = gto.M(
        atom=atoms,
        basis=basis_set,
        spin=n_electrons % 2,
        unit="Angstrom",
        verbose=0,
        cart=False,
    )
    return int(mol.nao_nr())


def derive_basis_function_count(symbols: Sequence[str], basis_set: str) -> int:
    """Number of spherical contracted basis functions N_bf for a molecule.

    PySCF's basis library is authoritative when installed; otherwise a
    compact table for H/He and B-Ne is used.
    """
    syms = [_normalize_symbol(s) for s in symbols]
    if not syms:
        raise ValueError("at least one atom is required")
    pyscf_error: Optional[Exception] = None
    try:
        return _pyscf_basis_count(syms, basis_set)
    except ImportError as exc:
        pyscf_error = exc
    except CliUsageError:
        raise
    except Exception as exc:  # unknown basis name in PySCF, try the table
        pyscf_error = exc
    key = basis_set.strip().lower()
    total = 0
    for sym in syms:
        count = _basis_table_entry(key, sym)
        if count is None:
            raise ValueError(
                f"cannot derive basis-function count for {sym} in {basis_set!r} "
                f"(PySCF unavailable or failed: {pyscf_error})"
            )
        total += count
    return total


@dataclass(frozen=True)
class RoutingDecision:
    engine: str
    n_basis_functions: Optional[int]
    device: str
    precision: str
    reason: str


def route_calculation(deck: Any) -> RoutingDecision:
    """Apply the Method Matrix Sec. 8.3 crossover engine rule."""
    if isinstance(deck, Mapping):
        deck = QuantumCalculationDeckConfig(**deck)
    requested = deck.engine
    if requested == "crest":
        raise ValueError("CREST conformer searches are handled by 'cochem conformer', not 'cochem run'")
    if requested == "xtb":
        return RoutingDecision("xtb", None, "cpu", "fp64", "explicit semi-empirical GFN2-xTB request")
    n_bf = derive_basis_function_count(deck.symbols, deck.basis_set)
    if requested == "cfour":
        return RoutingDecision(
            "cfour", n_bf, "cpu", "fp64", f"explicit CFOUR request (N_bf={n_bf}); crossover rule not applicable"
        )
    if n_bf < CROSSOVER_BASIS_FUNCTIONS:
        engine = "orca" if requested == "orca" else "pyscf-cpu"
        reason = f"N_bf={n_bf} < {CROSSOVER_BASIS_FUNCTIONS}: CPU tier (host P-cores)"
        if requested == "gpu4pyscf":
            reason += "; gpu4pyscf request overridden by crossover rule"
        return RoutingDecision(engine, n_bf, "cpu", "fp64", reason)
    reason = f"N_bf={n_bf} >= {CROSSOVER_BASIS_FUNCTIONS}: gpu4pyscf FP64 tier"
    if requested in ("orca", "pyscf"):
        reason += f"; {requested} request overridden by crossover rule"
    return RoutingDecision("gpu4pyscf-fp64", n_bf, "gpu", "fp64", reason)


# ---------------------------------------------------------------------------
# Memory sizing and Hessian preconditioning
# ---------------------------------------------------------------------------
def _windows_available_ram_mb() -> float:
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise RuntimeError("GlobalMemoryStatusEx failed")
    return float(status.ullAvailPhys) / (1024.0 * 1024.0)


def _available_ram_mb() -> float:
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        return psutil.virtual_memory().available / (1024.0 * 1024.0)
    if sys.platform == "win32":
        return _windows_available_ram_mb()
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1024.0
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages) * float(page_size) / (1024.0 * 1024.0)
    except (AttributeError, ValueError, OSError) as exc:
        raise RuntimeError("cannot determine the available RAM on this host") from exc


def calculate_orca_maxcore(mpi_processes: int, available_ram_mb: Optional[float] = None) -> int:
    """ORCA %maxcore = floor(available RAM [MB] * 0.75 / MPI processes), >= 256 MB."""
    if isinstance(mpi_processes, bool) or int(mpi_processes) != mpi_processes or mpi_processes < 1:
        raise ValueError(f"MPI process count must be a positive integer, got {mpi_processes!r}")
    ram = _available_ram_mb() if available_ram_mb is None else float(available_ram_mb)
    if not math.isfinite(ram) or ram <= 0.0:
        raise ValueError(f"available RAM must be positive, got {ram!r}")
    return max(MAXCORE_FLOOR_MB, int(math.floor(ram * MAXCORE_RAM_FRACTION / int(mpi_processes))))


def enforce_method_matrix(deck: QuantumCalculationDeckConfig) -> Optional[str]:
    """Reject exact initial Hessians; return the canonical model-Hessian preconditioner."""
    raw = deck.raw_deck or ""
    if _CALC_HESS_TRUE_RE.search(raw):
        raise MethodMatrixViolation(
            "Method Matrix violation: 'Calc_Hess true' requests an exact initial Hessian. "
            "Geometry optimisations must be preconditioned with a model Hessian "
            "('%geom InHess XTB2 end' or '%geom InHess Lindh end')."
        )
    for match in _INHESS_RE.finditer(raw):
        token = match.group(1).upper()
        if token not in ALLOWED_HESSIAN_PRECONDITIONERS:
            raise MethodMatrixViolation(
                f"Method Matrix violation: InHess {match.group(1)} is not an approved "
                "preconditioner (allowed: XTB2, Lindh)."
            )
    if deck.in_hess is None:
        if deck.task_type in ("opt", "opt_freq"):
            raise MethodMatrixViolation(
                "Method Matrix violation: geometry optimisations require in_hess XTB2 or Lindh."
            )
        return None
    token = deck.in_hess.strip().upper()
    if token not in ALLOWED_HESSIAN_PRECONDITIONERS:
        raise MethodMatrixViolation(
            f"Method Matrix violation: in_hess {deck.in_hess!r} is not an approved "
            "preconditioner (allowed: XTB2, Lindh)."
        )
    return ALLOWED_HESSIAN_PRECONDITIONERS[token]


def _check_electron_count(deck: QuantumCalculationDeckConfig) -> int:
    n_electrons = sum(atomic_number(s) for s in deck.symbols) - deck.charge
    unpaired = deck.multiplicity - 1
    if n_electrons < 0:
        raise CliUsageError(f"charge {deck.charge} leaves a negative electron count")
    if unpaired > n_electrons or (n_electrons - unpaired) % 2 != 0:
        raise CliUsageError(
            f"multiplicity {deck.multiplicity} is inconsistent with {n_electrons} electrons "
            f"(charge {deck.charge})"
        )
    return n_electrons


# ---------------------------------------------------------------------------
# Subprocess dispatch (CREATE_NO_WINDOW on Windows)
# ---------------------------------------------------------------------------
def run_external_command(
    command: Sequence[Any],
    cwd: Path,
    timeout_s: float,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run an external engine in the background without a console window."""
    argv = [str(part) for part in command]
    LOGGER.debug("dispatching %s in %s", argv, cwd)
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            env=dict(env) if env is not None else None,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError as exc:
        raise EngineUnavailable(f"executable {argv[0]!r} could not be launched") from exc
    except PermissionError as exc:
        raise EngineUnavailable(f"executable {argv[0]!r} is not executable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExternalEngineError(f"{argv[0]} exceeded the {timeout_s:.0f} s timeout") from exc


def _locate_executable(env_var: str, names: Sequence[str]) -> Optional[Path]:
    override = os.environ.get(env_var)
    if override:
        path = Path(override)
        return path if path.is_file() else None
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _locate_orca() -> Optional[Path]:
    exe = _locate_executable("COCHEM_ORCA_PATH", ("orca",))
    if exe is None:
        return None
    # Guard against the GNOME 'orca' screen reader: real ORCA ships orca_scf.
    parent = exe.resolve().parent
    if (parent / "orca_scf").exists() or (parent / "orca_scf.exe").exists():
        return exe
    return None


@dataclass
class RunContext:
    nprocs: int
    maxcore_mb: int
    preconditioner: Optional[str]
    scratch_root: Optional[Path]
    keep_scratch: bool
    timeout_s: float


@contextmanager
def _scratch_directory(ctx: RunContext, deck: QuantumCalculationDeckConfig, backend: str) -> Iterator[Path]:
    safe_job = re.sub(r"[^A-Za-z0-9_.-]", "_", deck.job_uuid)
    if ctx.scratch_root is not None:
        work = ctx.scratch_root / f"{safe_job}_{backend}"
        work.mkdir(parents=True, exist_ok=True)
        yield work
        return
    work = Path(tempfile.mkdtemp(prefix=f"cochem_{safe_job}_{backend}_"))
    try:
        yield work
    finally:
        if not ctx.keep_scratch:
            shutil.rmtree(work, ignore_errors=True)


def _xyz_block(deck: QuantumCalculationDeckConfig) -> List[str]:
    return [
        f"  {sym:<2s} {x:16.10f} {y:16.10f} {z:16.10f}"
        for sym, (x, y, z) in zip(deck.symbols, deck.coordinates_angstrom)
    ]


def _read_xyz_rows(path: Path, n_atoms: int) -> List[List[float]]:
    rows = path.read_text(encoding="utf-8").splitlines()
    coords: List[List[float]] = []
    for line in rows[2:2 + n_atoms]:
        parts = line.split()
        coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
    if len(coords) != n_atoms:
        raise ExternalEngineError(f"optimised geometry file {path.name} is truncated")
    return coords


# ---------------------------------------------------------------------------
# ORCA
# ---------------------------------------------------------------------------
def synthesize_orca_deck(
    deck: QuantumCalculationDeckConfig,
    maxcore_mb: int,
    nprocs: int,
    preconditioner: Optional[str],
) -> str:
    keywords = [deck.method]
    if not deck.method.lower().endswith("-3c"):  # composite methods carry their own basis
        keywords.append(deck.basis_set)
        if deck.aux_basis_set:
            keywords.append(deck.aux_basis_set)
    task_keywords = {"sp": "SP", "opt": "Opt", "freq": "Freq", "opt_freq": "Opt Freq"}
    keywords.append(task_keywords[deck.task_type])
    if deck.grid_level:
        keywords.append(deck.grid_level)
    lines = [f"# CoChem job {deck.job_uuid}", "! " + " ".join(keywords), f"%maxcore {maxcore_mb}"]
    if nprocs > 1:
        lines.append(f"%pal nprocs {nprocs} end")
    if deck.task_type in ("opt", "opt_freq"):
        pre = preconditioner or "XTB2"
        if deck.frozen_atoms:
            lines.append("%geom")
            lines.append(f"  InHess {pre}")
            lines.append("  Constraints")
            lines.extend(f"    {{ C {idx} C }}" for idx in deck.frozen_atoms)
            lines.append("  end")
            lines.append("end")
        else:
            lines.append(f"%geom InHess {pre} end")
    lines.append(f"* xyz {deck.charge} {deck.multiplicity}")
    lines.extend(_xyz_block(deck))
    lines.append("*")
    return "\n".join(lines) + "\n"


def _parse_orca_frequencies(text: str) -> List[float]:
    marker = text.rfind("VIBRATIONAL FREQUENCIES")
    if marker < 0:
        return []
    freqs: List[float] = []
    for line in text[marker:].splitlines()[1:]:
        match = re.match(r"^\s*(\d+):\s+(-?\d+\.\d+)\s+cm\*\*-1", line)
        if match:
            freqs.append(float(match.group(2)))
        elif freqs and not line.strip():
            break
    return freqs


def _run_orca(deck: QuantumCalculationDeckConfig, ctx: RunContext) -> Dict[str, Any]:
    exe = _locate_orca()
    if exe is None:
        raise EngineUnavailable("ORCA executable not found (PATH or COCHEM_ORCA_PATH)")
    deck_text = synthesize_orca_deck(deck, ctx.maxcore_mb, ctx.nprocs, ctx.preconditioner)
    with _scratch_directory(ctx, deck, "orca") as work:
        inp = work / "cochem_job.inp"
        inp.write_text(deck_text, encoding="utf-8")
        completed = run_external_command([exe, inp.name], cwd=work, timeout_s=ctx.timeout_s)
        (work / "cochem_job.out").write_text(completed.stdout, encoding="utf-8")
        if "ORCA TERMINATED NORMALLY" not in completed.stdout:
            tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-15:])
            raise ExternalEngineError(f"ORCA failed (return code {completed.returncode}):\n{tail}")
        energies = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", completed.stdout)
        if not energies:
            raise ExternalEngineError("ORCA output contains no FINAL SINGLE POINT ENERGY")
        results: Dict[str, Any] = {
            "energy_hartree": float(energies[-1]),
            "method_executed": f"{deck.method}/{deck.basis_set} (ORCA)",
            "orca_input": deck_text,
        }
        if deck.task_type in ("opt", "opt_freq"):
            xyz_path = work / "cochem_job.xyz"
            if not xyz_path.is_file():
                raise ExternalEngineError("ORCA optimisation finished without writing cochem_job.xyz")
            results["optimized_coordinates_angstrom"] = _read_xyz_rows(xyz_path, len(deck.symbols))
        if deck.task_type in ("freq", "opt_freq"):
            freqs = _parse_orca_frequencies(completed.stdout)
            if not freqs:
                raise ExternalEngineError("ORCA output contains no vibrational frequencies")
            results["frequencies_cm1"] = freqs
        return results


# ---------------------------------------------------------------------------
# xTB (GFN2) and CFOUR
# ---------------------------------------------------------------------------
def _engine_env(nprocs: int) -> Dict[str, str]:
    env = dict(os.environ)
    env["OMP_NUM_THREADS"] = str(nprocs)
    env["MKL_NUM_THREADS"] = str(nprocs)
    env.setdefault("OMP_STACKSIZE", "2G")
    return env


def _run_xtb(deck: QuantumCalculationDeckConfig, ctx: RunContext) -> Dict[str, Any]:
    exe = _locate_executable("COCHEM_XTB_PATH", ("xtb",))
    if exe is None:
        raise EngineUnavailable("xtb executable not found (PATH or COCHEM_XTB_PATH)")
    with _scratch_directory(ctx, deck, "xtb") as work:
        xyz = work / "cochem_job.xyz"
        lines = [str(len(deck.symbols)), f"CoChem job {deck.job_uuid}"]
        lines += [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in zip(deck.symbols, deck.coordinates_angstrom)]
        xyz.write_text("\n".join(lines) + "\n", encoding="utf-8")
        argv: List[Any] = [exe, xyz.name, "--gfn", "2", "--chrg", deck.charge, "--uhf", deck.multiplicity - 1]
        task_flags = {"sp": [], "opt": ["--opt"], "freq": ["--hess"], "opt_freq": ["--ohess"]}
        argv += task_flags[deck.task_type]
        if deck.frozen_atoms and deck.task_type in ("opt", "opt_freq"):
            constraint = work / "cochem_fix.inp"
            atoms = ",".join(str(i + 1) for i in deck.frozen_atoms)
            constraint.write_text(f"$fix\n  atoms: {atoms}\n$end\n", encoding="utf-8")
            argv += ["--input", constraint.name]
        completed = run_external_command(argv, cwd=work, timeout_s=ctx.timeout_s, env=_engine_env(ctx.nprocs))
        (work / "cochem_job.out").write_text(completed.stdout, encoding="utf-8")
        energies = re.findall(r"TOTAL ENERGY\s+(-?\d+\.\d+)\s+Eh", completed.stdout)
        if completed.returncode != 0 or not energies:
            tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-15:])
            raise ExternalEngineError(f"xtb failed (return code {completed.returncode}):\n{tail}")
        results: Dict[str, Any] = {"energy_hartree": float(energies[-1]), "method_executed": "GFN2-xTB (xtb)"}
        if deck.task_type in ("opt", "opt_freq"):
            results["optimized_coordinates_angstrom"] = _read_xyz_rows(work / "xtbopt.xyz", len(deck.symbols))
        if deck.task_type in ("freq", "opt_freq"):
            vib = work / "vibspectrum"
            freqs: List[float] = []
            if vib.is_file():
                for line in vib.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and not line.lstrip().startswith(("$", "#")):
                        try:
                            freqs.append(float(parts[-4] if len(parts) >= 5 else parts[1]))
                        except ValueError:
                            continue
            if not freqs:
                raise ExternalEngineError("xtb Hessian run produced no vibspectrum")
            results["frequencies_cm1"] = freqs
        return results


_CFOUR_BASIS = {
    "cc-pvdz": "PVDZ",
    "cc-pvtz": "PVTZ",
    "cc-pvqz": "PVQZ",
    "aug-cc-pvdz": "AUG-PVDZ",
    "aug-cc-pvtz": "AUG-PVTZ",
    "aug-cc-pvqz": "AUG-PVQZ",
}
_CFOUR_CALC = {"HF": "SCF", "SCF": "SCF", "MP2": "MP2", "CCSD": "CCSD", "CCSD(T)": "CCSD(T)"}


def _run_cfour(deck: QuantumCalculationDeckConfig, ctx: RunContext) -> Dict[str, Any]:
    exe = _locate_executable("COCHEM_CFOUR_PATH", ("xcfour",))
    if exe is None:
        raise EngineUnavailable("CFOUR executable xcfour not found (PATH or COCHEM_CFOUR_PATH)")
    if deck.task_type != "sp":
        raise EngineUnavailable("CFOUR dispatch supports single points only")
    calc = _CFOUR_CALC.get(deck.method.strip().upper())
    basis = _CFOUR_BASIS.get(deck.basis_set.strip().lower())
    if calc is None or basis is None:
        raise EngineUnavailable(f"CFOUR has no mapping for {deck.method}/{deck.basis_set}")
    genbas_candidates = [
        Path(os.environ["COCHEM_CFOUR_GENBAS"]) if os.environ.get("COCHEM_CFOUR_GENBAS") else None,
        exe.resolve().parent / "GENBAS",
        exe.resolve().parent.parent / "basis" / "GENBAS",
    ]
    genbas = next((p for p in genbas_candidates if p is not None and p.is_file()), None)
    if genbas is None:
        raise EngineUnavailable("CFOUR GENBAS basis library not found (COCHEM_CFOUR_GENBAS)")
    reference = "RHF" if deck.multiplicity == 1 else "UHF"
    with _scratch_directory(ctx, deck, "cfour") as work:
        shutil.copyfile(genbas, work / "GENBAS")
        zmat = [f"CoChem {deck.job_uuid}"]
        zmat += [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in zip(deck.symbols, deck.coordinates_angstrom)]
        zmat += [
            "",
            f"*CFOUR(CALC={calc},BASIS={basis}",
            f"CHARGE={deck.charge},MULTIPLICITY={deck.multiplicity},REF={reference}",
            "COORDINATES=CARTESIAN,UNITS=ANGSTROM",
            f"MEMORY_SIZE={ctx.maxcore_mb * ctx.nprocs},MEM_UNIT=MB)",
            "",
        ]
        (work / "ZMAT").write_text("\n".join(zmat) + "\n", encoding="utf-8")
        completed = run_external_command([exe], cwd=work, timeout_s=ctx.timeout_s, env=_engine_env(ctx.nprocs))
        (work / "cochem_job.out").write_text(completed.stdout, encoding="utf-8")
        energies = re.findall(r"The final electronic energy is\s+(-?\d+\.\d+)", completed.stdout)
        if completed.returncode != 0 or not energies:
            tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-15:])
            raise ExternalEngineError(f"CFOUR failed (return code {completed.returncode}):\n{tail}")
        return {"energy_hartree": float(energies[-1]), "method_executed": f"{calc}/{basis} (CFOUR)"}


# ---------------------------------------------------------------------------
# PySCF (CPU / gpu4pyscf) and ASE EMT in-process physical fallbacks
# ---------------------------------------------------------------------------
_PYSCF_GRID_LEVELS = {"defgrid1": 2, "defgrid2": 3, "defgrid3": 4}


def _check_gpu_available() -> None:
    try:
        import cupy  # noqa: F401
        n_devices = int(cupy.cuda.runtime.getDeviceCount())
    except Exception as exc:
        raise EngineUnavailable(f"no CUDA device usable through cupy: {exc}") from exc
    if n_devices < 1:
        raise EngineUnavailable("no CUDA device detected")
    try:
        import gpu4pyscf  # noqa: F401
    except Exception as exc:
        raise EngineUnavailable(f"gpu4pyscf is not installed: {exc}") from exc


def _run_pyscf(deck: QuantumCalculationDeckConfig, ctx: RunContext, use_gpu: bool = False) -> Dict[str, Any]:
    try:
        from pyscf import dft, gto, lib, scf
    except ImportError as exc:
        raise EngineUnavailable("PySCF is not installed") from exc
    if use_gpu:
        _check_gpu_available()
    method = deck.method.strip()
    upper = method.upper()
    if upper.endswith("-3C"):
        raise EngineUnavailable(f"composite method {method} is not available in PySCF")
    correlated = upper if upper in ("MP2", "CCSD", "CCSD(T)") else None
    is_hf = upper in ("HF", "RHF", "UHF", "ROHF") or correlated is not None
    if not is_hf:
        try:
            from pyscf.dft import libxc

            libxc.parse_xc(method)
        except Exception as exc:
            raise EngineUnavailable(f"PySCF does not recognise functional {method!r}: {exc}") from exc
    if correlated and deck.task_type != "sp":
        raise EngineUnavailable(f"{method} gradients/Hessians are not dispatched through PySCF")
    lib.num_threads(ctx.nprocs)

    def build_mol(coords: Sequence[Sequence[float]]):
        return gto.M(
            atom=[(s, tuple(c)) for s, c in zip(deck.symbols, coords)],
            basis=deck.basis_set,
            charge=deck.charge,
            spin=deck.multiplicity - 1,
            unit="Angstrom",
            verbose=0,
        )

    def build_mf(mol):
        if is_hf:
            if upper == "ROHF":
                mf = scf.ROHF(mol)
            elif upper == "UHF":
                mf = scf.UHF(mol)
            else:
                mf = scf.HF(mol)
        else:
            mf = dft.KS(mol)
            mf.xc = method
            level = _PYSCF_GRID_LEVELS.get(deck.grid_level.lower())
            if level is not None:
                mf.grids.level = level
        mf.max_cycle = 200
        if use_gpu:
            mf = mf.to_gpu()
        return mf

    try:
        mol = build_mol(deck.coordinates_angstrom)
    except Exception as exc:
        raise EngineUnavailable(f"PySCF cannot build the molecule with basis {deck.basis_set!r}: {exc}") from exc
    mf = build_mf(mol)
    results: Dict[str, Any] = {}
    if deck.task_type in ("opt", "opt_freq"):
        try:
            from pyscf.geomopt.geometric_solver import optimize
        except ImportError as exc:
            raise EngineUnavailable("geomeTRIC is required for PySCF optimisations") from exc
        kwargs: Dict[str, Any] = {"maxsteps": 300}
        with _scratch_directory(ctx, deck, "pyscf") as work:
            if deck.frozen_atoms:
                cons = work / "constraints.txt"
                cons.write_text(
                    "$freeze\nxyz " + ",".join(str(i + 1) for i in deck.frozen_atoms) + "\n", encoding="utf-8"
                )
                kwargs["constraints"] = str(cons)
            mol_eq = optimize(mf, **kwargs)
        coords = (np.asarray(mol_eq.atom_coords(unit="Angstrom"), dtype=float)).tolist()
        results["optimized_coordinates_angstrom"] = coords
        mol = build_mol(coords)
        mf = build_mf(mol)
    energy = float(mf.kernel())
    if not bool(getattr(mf, "converged", False)):
        raise ExternalEngineError(f"PySCF SCF did not converge for {method}/{deck.basis_set}")
    if correlated == "MP2":
        from pyscf import mp

        energy = float(mp.MP2(mf).run().e_tot)
    elif correlated in ("CCSD", "CCSD(T)"):
        from pyscf import cc

        mycc = cc.CCSD(mf).run()
        if not mycc.converged:
            raise ExternalEngineError("PySCF CCSD amplitudes did not converge")
        energy = float(mycc.e_tot)
        if correlated == "CCSD(T)":
            energy += float(mycc.ccsd_t())
    if deck.task_type in ("freq", "opt_freq"):
        try:
            from pyscf.hessian import thermo

            hessian = mf.Hessian().kernel()
            analysis = thermo.harmonic_analysis(mf.mol, hessian)
        except (AttributeError, ImportError, NotImplementedError) as exc:
            raise EngineUnavailable(f"PySCF analytic Hessian unavailable for {method}: {exc}") from exc
        freqs = []
        for value in np.asarray(analysis["freq_wavenumber"]).ravel():
            complex_value = complex(value)
            freqs.append(float(complex_value.real) if abs(complex_value.imag) < 1e-9 else -float(abs(complex_value.imag)))
        results["frequencies_cm1"] = freqs
    tier = "gpu4pyscf" if use_gpu else "PySCF CPU"
    results["energy_hartree"] = energy
    results["method_executed"] = f"{method}/{deck.basis_set} ({tier})"
    return results


def _run_emt(deck: QuantumCalculationDeckConfig, ctx: RunContext) -> Dict[str, Any]:
    try:
        from ase import Atoms
        from ase.calculators import emt as emt_module
    except ImportError as exc:
        raise EngineUnavailable("ASE is not installed") from exc
    supported = getattr(emt_module, "parameters", None)
    if supported is not None:
        missing = sorted({s for s in deck.symbols if s not in supported})
        if missing:
            raise EngineUnavailable(f"ASE EMT has no parameters for {', '.join(missing)}")
    if deck.charge != 0 or deck.multiplicity != 1:
        raise EngineUnavailable("ASE EMT cannot represent charged or open-shell systems")
    atoms = Atoms(symbols=list(deck.symbols), positions=np.asarray(deck.coordinates_angstrom, dtype=float), pbc=False)
    atoms.calc = emt_module.EMT()
    results: Dict[str, Any] = {
        "method_executed": "EMT effective-medium potential (ASE)",
        "warnings": [
            f"requested {deck.method}/{deck.basis_set} could not be executed on this host; "
            "energy is from the ASE EMT empirical potential (charge and spin not represented)"
        ],
    }
    try:
        if deck.task_type in ("opt", "opt_freq"):
            from ase.constraints import FixAtoms
            from ase.optimize import BFGS

            if deck.frozen_atoms:
                atoms.set_constraint(FixAtoms(indices=list(deck.frozen_atoms)))
            optimizer = BFGS(atoms, logfile=None)
            converged = optimizer.run(fmax=0.01, steps=2000)
            if converged is False:
                raise ExternalEngineError("EMT BFGS optimisation did not converge in 2000 steps")
            results["optimized_coordinates_angstrom"] = atoms.get_positions().tolist()
        energy_ev = float(atoms.get_potential_energy())
        if deck.task_type in ("freq", "opt_freq"):
            from ase.vibrations import Vibrations

            free = [i for i in range(len(atoms)) if i not in set(deck.frozen_atoms)]
            with _scratch_directory(ctx, deck, "emt") as work:
                vib = Vibrations(atoms, indices=free, name=str(work / "vib"), delta=0.01)
                vib.run()
                raw = vib.get_frequencies()
                vib.clean()
            results["frequencies_cm1"] = [
                float(complex(f).real) if abs(complex(f).imag) < 1e-9 else -float(abs(complex(f).imag)) for f in raw
            ]
    except KeyError as exc:
        raise EngineUnavailable(f"ASE EMT has no parameters for element {exc}") from exc
    if not math.isfinite(energy_ev):
        raise ExternalEngineError("EMT returned a non-finite energy")
    results["energy_hartree"] = energy_ev / HARTREE_TO_EV
    return results


_BACKENDS: Dict[str, Callable[[QuantumCalculationDeckConfig, RunContext], Dict[str, Any]]] = {
    "orca": _run_orca,
    "cfour": _run_cfour,
    "xtb": _run_xtb,
    "pyscf-cpu": lambda deck, ctx: _run_pyscf(deck, ctx, use_gpu=False),
    "gpu4pyscf-fp64": lambda deck, ctx: _run_pyscf(deck, ctx, use_gpu=True),
    "emt": _run_emt,
}

_FALLBACK_CHAINS: Dict[str, Tuple[str, ...]] = {
    "orca": ("orca", "pyscf-cpu", "emt"),
    "pyscf-cpu": ("pyscf-cpu", "orca", "emt"),
    "gpu4pyscf-fp64": ("gpu4pyscf-fp64", "pyscf-cpu", "orca", "emt"),
    "cfour": ("cfour", "pyscf-cpu", "emt"),
    "xtb": ("xtb", "emt"),
}


def _dispatch(
    deck: QuantumCalculationDeckConfig, routing: RoutingDecision, ctx: RunContext
) -> Tuple[str, Dict[str, Any], List[Dict[str, str]]]:
    attempts: List[Dict[str, str]] = []
    for backend in _FALLBACK_CHAINS[routing.engine]:
        try:
            result = _BACKENDS[backend](deck, ctx)
        except EngineUnavailable as exc:
            LOGGER.info("backend %s unavailable: %s", backend, exc)
            attempts.append({"engine": backend, "status": "unavailable", "reason": str(exc)})
            continue
        attempts.append({"engine": backend, "status": "executed"})
        return backend, result, attempts
    detail = "; ".join(f"{a['engine']}: {a['reason']}" for a in attempts)
    raise ExternalEngineError(f"no engine could execute the calculation ({detail})")


def _load_deck(deck_arg: str, raw_deck_path: Optional[str]) -> QuantumCalculationDeckConfig:
    try:
        text = sys.stdin.read() if deck_arg == "-" else Path(deck_arg).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliUsageError(f"cannot read calculation deck {deck_arg!r}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"calculation deck is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliUsageError("calculation deck must be a JSON object")
    if raw_deck_path is not None:
        try:
            data["raw_deck"] = Path(raw_deck_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliUsageError(f"cannot read raw deck {raw_deck_path!r}: {exc}") from exc
    try:
        return QuantumCalculationDeckConfig(**data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ())) or '<deck>'}: {err.get('msg')}" for err in exc.errors()
        )
        raise CliUsageError(f"calculation deck schema violation: {problems}") from exc


# ---------------------------------------------------------------------------
# Rotational spectroscopy: inertia tensor, constants, Ray's kappa
# ---------------------------------------------------------------------------
def _atom_masses(symbols: Sequence[str], isotopes: str = "standard") -> np.ndarray:
    if isotopes == "most-abundant":
        return np.array([most_abundant_isotope_mass(s)[0] for s in symbols], dtype=float)
    return np.array([atomic_mass(s) for s in symbols], dtype=float)


def compute_principal_moments(
    symbols: Sequence[str],
    coordinates_angstrom: Sequence[Sequence[float]],
    masses: Optional[Sequence[float]] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Principal moments (amu A^2, ascending), principal axes (columns) and centre of mass."""
    syms = [_normalize_symbol(s) for s in symbols]
    r = np.asarray(coordinates_angstrom, dtype=float)
    if r.ndim != 2 or r.shape != (len(syms), 3):
        raise CliUsageError(f"expected {len(syms)} x 3 coordinates, got shape {r.shape}")
    if not np.all(np.isfinite(r)):
        raise CliUsageError("coordinates contain non-finite values")
    m = _atom_masses(syms) if masses is None else np.asarray(masses, dtype=float)
    if m.shape != (len(syms),) or np.any(m <= 0):
        raise CliUsageError("masses must be positive, one per atom")
    com = (m[:, None] * r).sum(axis=0) / m.sum()
    x = r - com
    inertia = np.eye(3) * float(np.sum(m * np.sum(x * x, axis=1))) - (m[:, None] * x).T @ x
    moments, axes = np.linalg.eigh(inertia)
    moments = np.where(moments < 0.0, 0.0, moments)
    return moments, axes, com


def compute_inertia_rotational_constants(
    symbols: Sequence[str],
    coordinates_angstrom: Sequence[Sequence[float]],
    masses: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Equilibrium rotational constants A >= B >= C (MHz). A is None for linear rotors."""
    moments, _axes, com = compute_principal_moments(symbols, coordinates_angstrom, masses)
    i_max = float(moments[2])
    if i_max <= 1.0e-10:
        raise CliUsageError("a single atom (or coincident atoms) has no rotational spectrum")
    zero_tol = 1.0e-8 * i_max
    linear = float(moments[0]) <= zero_tol
    if linear:
        i_perp = 0.5 * (float(moments[1]) + float(moments[2]))
        b_const = INERTIA_TO_MHZ / i_perp
        a_const: Optional[float] = None
        c_const = b_const
    else:
        a_const = INERTIA_TO_MHZ / float(moments[0])
        b_const = INERTIA_TO_MHZ / float(moments[1])
        c_const = INERTIA_TO_MHZ / float(moments[2])
    return {
        "A": a_const,
        "B": b_const,
        "C": c_const,
        "linear": linear,
        "principal_moments_amu_angstrom2": [float(v) for v in moments],
        "center_of_mass_angstrom": [float(v) for v in com],
    }


def compute_ray_asymmetry_parameter(a_const: Optional[float], b_const: float, c_const: float) -> Optional[float]:
    """Ray's kappa = (2B - A - C)/(A - C); -1 prolate, +1 oblate, None for spherical tops."""
    if a_const is None:
        return -1.0  # linear rotor: prolate limit A -> infinity
    span = a_const - c_const
    if span <= 1.0e-12 * max(abs(a_const), 1.0):
        return None
    kappa = (2.0 * b_const - a_const - c_const) / span
    return float(min(1.0, max(-1.0, kappa)))


@dataclass(frozen=True)
class RotorParameters:
    """Watson A-reduced Hamiltonian constants (MHz), I^r representation."""

    A: Optional[float]
    B: float
    C: float
    DJ: float = 0.0
    DJK: float = 0.0
    DK: float = 0.0
    dJ: float = 0.0
    dK: float = 0.0

    def validate(self) -> "RotorParameters":
        values = [self.B, self.C, self.DJ, self.DJK, self.DK, self.dJ, self.dK]
        if self.A is not None:
            values.append(self.A)
        if not all(math.isfinite(v) for v in values):
            raise CliUsageError("rotational and distortion constants must be finite")
        if self.B <= 0.0 or self.C <= 0.0:
            raise CliUsageError("rotational constants must be positive")
        tol = 1.0e-9 * max(self.B, 1.0)
        if self.A is None:
            if abs(self.B - self.C) > tol:
                raise CliUsageError("a linear rotor requires B == C")
            if self.DJK or self.DK or self.dJ or self.dK:
                raise CliUsageError("linear rotors only accept the D_J distortion constant")
        elif not (self.A + tol >= self.B and self.B + tol >= self.C):
            raise CliUsageError(f"rotational constants must satisfy A >= B >= C (got {self.A}, {self.B}, {self.C})")
        return self

    @property
    def linear(self) -> bool:
        return self.A is None


def _diag_element(p: RotorParameters, J: int, K: int) -> float:
    jj = float(J * (J + 1))
    if p.A is None:
        return p.B * jj - p.DJ * jj * jj
    bc = 0.5 * (p.B + p.C)
    k2 = float(K * K)
    return bc * jj + (p.A - bc) * k2 - p.DJ * jj * jj - p.DJK * jj * k2 - p.DK * k2 * k2


def _off_element(p: RotorParameters, J: int, K: int) -> float:
    """<J, K+2 | H | J, K> of the A-reduced Hamiltonian."""
    jj = float(J * (J + 1))
    prod = (jj - K * (K + 1)) * (jj - (K + 1) * (K + 2))
    if prod <= 0.0:
        return 0.0
    return math.sqrt(prod) * (0.25 * (p.B - p.C) - p.dJ * jj - 0.5 * p.dK * (K * K + (K + 2) ** 2))


def _wang_tridiagonals(p: RotorParameters, J: int) -> List[Tuple[int, int, Tuple[int, ...], np.ndarray, np.ndarray]]:
    """Wang-symmetrised blocks (K parity, sign, K list, diagonal, off-diagonal)."""
    if p.A is None:
        return [(0, 1, (0,), np.array([_diag_element(p, J, 0)]), np.zeros(0))]
    blocks = []
    for parity in (0, 1):
        for sign in (1, -1):
            ks = tuple(K for K in range(parity, J + 1, 2) if K > 0 or sign == 1)
            if not ks:
                continue
            diag = np.array(
                [_diag_element(p, J, K) + (sign * _off_element(p, J, -1) if K == 1 else 0.0) for K in ks]
            )
            off = np.array([_off_element(p, J, K) * (SQRT2 if K == 0 else 1.0) for K in ks[:-1]])
            blocks.append((parity, sign, ks, diag, off))
    return blocks


def _level_energies(p: RotorParameters, J: int) -> np.ndarray:
    values = []
    for _parity, _sign, _ks, diag, off in _wang_tridiagonals(p, J):
        if len(diag) == 1:
            values.append(diag.copy())
        elif _eigvalsh_tridiagonal is not None:
            values.append(_eigvalsh_tridiagonal(diag, off))
        else:
            values.append(np.linalg.eigvalsh(np.diag(diag) + np.diag(off, 1) + np.diag(off, -1)))
    return np.concatenate(values)


@dataclass
class _RotorLevels:
    J: int
    energies: np.ndarray
    vectors: np.ndarray  # (n_basis, n_states); basis |J,K>, K ascending
    k_values: np.ndarray
    ka: np.ndarray
    kc: np.ndarray
    ka_par: np.ndarray
    sum_par: np.ndarray


def _rotor_levels(p: RotorParameters, J: int) -> _RotorLevels:
    linear = p.A is None
    k_values = np.array([0]) if linear else np.arange(-J, J + 1)
    offset = 0 if linear else J
    n_basis = len(k_values)
    energies, vectors, ka_par, sum_par = [], [], [], []
    for parity, sign, ks, diag, off in _wang_tridiagonals(p, J):
        n = len(ks)
        h = np.diag(diag)
        if n > 1:
            h = h + np.diag(off, 1) + np.diag(off, -1)
        w, u = np.linalg.eigh(h)
        wang = np.zeros((n_basis, n))
        for a, K in enumerate(ks):
            if K == 0:
                wang[offset, a] = 1.0
            else:
                wang[offset + K, a] = 1.0 / SQRT2
                wang[offset - K, a] = sign / SQRT2
        energies.append(w)
        vectors.append(wang @ u)
        ka_par += [parity] * n
        # C2(b) eigenvalue (-1)^(Ka+Kc) = sign * (-1)^J
        sum_par += [0 if sign * (-1) ** J == 1 else 1] * n
    e = np.concatenate(energies)
    v = np.concatenate(vectors, axis=1)
    kp = np.array(ka_par)
    sp = np.array(sum_par)
    order = np.argsort(e, kind="stable")
    e, v, kp, sp = e[order], v[:, order], kp[order], sp[order]
    n_states = len(e)
    idx = np.arange(n_states)
    if linear:
        ka = np.zeros(1, dtype=int)
        kc = np.array([J])
    else:
        ka = (idx + 1) // 2
        kc = J - idx // 2
        # Near-degenerate asymmetry doublets: order labels consistently with state symmetry.
        lab_ka, lab_sum = ka % 2, (ka + kc) % 2
        for i in range(n_states - 1):
            mismatch_i = kp[i] != lab_ka[i] or sp[i] != lab_sum[i]
            mismatch_j = kp[i + 1] != lab_ka[i + 1] or sp[i + 1] != lab_sum[i + 1]
            close = abs(e[i + 1] - e[i]) <= 1.0e-9 * max(1.0, abs(e[i]))
            fixes = kp[i + 1] == lab_ka[i] and sp[i + 1] == lab_sum[i] and kp[i] == lab_ka[i + 1] and sp[i] == lab_sum[i + 1]
            if mismatch_i and mismatch_j and close and fixes:
                for arr in (e, kp, sp):
                    arr[[i, i + 1]] = arr[[i + 1, i]]
                v[:, [i, i + 1]] = v[:, [i + 1, i]]
    return _RotorLevels(J, e, v, k_values, np.asarray(ka), np.asarray(kc), kp, sp)


def _clebsch_gordan_j1(J: int, K: int, q: int, Jp: int) -> float:
    """<J K; 1 q | J' K+q> for J' in {J, J+1}."""
    if Jp == J + 1:
        if q == 1:
            num, den = (J + K + 1) * (J + K + 2), (2 * J + 1) * (2 * J + 2)
        elif q == 0:
            num, den = (J - K + 1) * (J + K + 1), (2 * J + 1) * (J + 1)
        else:
            num, den = (J - K + 1) * (J - K + 2), (2 * J + 1) * (2 * J + 2)
        return math.sqrt(num / den) if num > 0 else 0.0
    if Jp == J and J > 0:
        den = 2.0 * J * (J + 1)
        if q == 1:
            num = (J + K + 1) * (J - K)
            return -math.sqrt(num / den) if num > 0 else 0.0
        if q == 0:
            return K / math.sqrt(J * (J + 1))
        num = (J - K + 1) * (J + K)
        return math.sqrt(num / den) if num > 0 else 0.0
    return 0.0


def _cg_matrix(J: int, Jp: int, q: int, k_lo: np.ndarray, k_up: np.ndarray) -> np.ndarray:
    index_up = {int(k): i for i, k in enumerate(k_up)}
    mat = np.zeros((len(k_up), len(k_lo)))
    for j, K in enumerate(k_lo):
        target = index_up.get(int(K) + q)
        if target is not None:
            mat[target, j] = _clebsch_gordan_j1(J, int(K), q, Jp)
    return mat


def _partition_function(p: RotorParameters, temperature_k: float) -> Tuple[float, int]:
    """Rotational partition function sum_J (2J+1) sum_tau exp(-E/kT), converged in J."""
    kt = BOLTZMANN_MHZ_PER_K * temperature_k
    total = 0.0
    previous_min: Optional[float] = None
    for J in range(PARTITION_J_CAP + 1):
        energies = _level_energies(p, J)
        e_min = float(energies.min())
        if previous_min is not None and e_min < previous_min:
            raise CliUsageError(
                f"centrifugal distortion constants make the rotational energy decrease with J at J={J}; "
                "the Hamiltonian is unbound"
            )
        term = (2 * J + 1) * float(np.exp(-energies / kt).sum())
        total += term
        if term < 1.0e-14 * total and e_min > kt:
            return total, J
        previous_min = e_min
    raise CliUsageError(
        f"partition function not converged by J={PARTITION_J_CAP}; lower --temperature or check constants"
    )


def predict_rotational_catalog(
    params: RotorParameters,
    dipoles_debye: Tuple[float, float, float],
    temperature_k: float,
    freq_max_mhz: float,
    freq_min_mhz: float = 0.0,
    j_max: int = 99,
    min_log_intensity: float = -10.0,
) -> Dict[str, Any]:
    """Line list from the Watson A-reduced Hamiltonian with JPL/Pickett intensities."""
    params.validate()
    mu_a, mu_b, mu_c = (abs(float(v)) for v in dipoles_debye)
    if not all(math.isfinite(v) for v in (mu_a, mu_b, mu_c)) or (mu_a == 0.0 and mu_b == 0.0 and mu_c == 0.0):
        raise CliUsageError("at least one non-zero, finite dipole component is required for a spectrum")
    if params.linear and (mu_b or mu_c):
        raise CliUsageError("a linear rotor only has an a-axis dipole component")
    if not (math.isfinite(temperature_k) and temperature_k > 0.0):
        raise CliUsageError("--temperature must be a positive number of kelvin")
    if not (math.isfinite(freq_max_mhz) and 0.0 < freq_max_mhz <= CAT_FREQ_LIMIT_MHZ):
        raise CliUsageError(f"--freq-max must lie in (0, {CAT_FREQ_LIMIT_MHZ}] MHz")
    if not (math.isfinite(freq_min_mhz) and 0.0 <= freq_min_mhz < freq_max_mhz):
        raise CliUsageError("--freq-min must be >= 0 and below --freq-max")
    if not (1 <= j_max <= CAT_MAX_QN):
        raise CliUsageError(f"--j-max must lie in 1..{CAT_MAX_QN}")
    if not (math.isfinite(min_log_intensity) and -99.0 <= min_log_intensity <= 0.0):
        raise CliUsageError("--min-log-intensity must lie in [-99, 0]")

    kt = BOLTZMANN_MHZ_PER_K * temperature_k
    q_rot, j_partition = _partition_function(params, temperature_k)
    log_q = math.log10(q_rot)
    mu2_max = max(mu_a, mu_b, mu_c) ** 2
    linear = params.linear
    levels = [_rotor_levels(params, J) for J in range(j_max + 1)]
    lines: List[Dict[str, Any]] = []

    def collect(lo: _RotorLevels, up: _RotorLevels, same_j: bool) -> None:
        J, Jp = lo.J, up.J
        e_floor = min(float(lo.energies.min()), float(up.energies.min()))
        bound = JPL_INTENSITY_CONSTANT * freq_max_mhz * 3.0 * (2 * J + 3) * mu2_max * math.exp(-e_floor / kt) / q_rot
        if bound <= 0.0 or math.log10(bound) < min_log_intensity - 1.0:
            return
        qs = (0,) if linear else (-1, 0, 1)
        amps = {q: up.vectors.T @ _cg_matrix(J, Jp, q, lo.k_values, up.k_values) @ lo.vectors for q in qs}
        pref = 2 * J + 1
        weight = np.zeros(amps[0].shape)
        kind = np.full(amps[0].shape, -1, dtype=int)
        if mu_a:
            s_a = pref * amps[0] ** 2
            mask = s_a > LINE_STRENGTH_FLOOR
            weight[mask] += mu_a ** 2 * s_a[mask]
            kind[mask] = 0
        if not linear and (mu_b or mu_c):
            s_p = pref * (amps[1] ** 2 + amps[-1] ** 2)
            ka_diff = up.ka_par[:, None] != lo.ka_par[None, :]
            sum_same = up.sum_par[:, None] == lo.sum_par[None, :]
            if mu_b:
                mask = (s_p > LINE_STRENGTH_FLOOR) & ka_diff & sum_same
                weight[mask] += mu_b ** 2 * s_p[mask]
                kind[mask] = 1
            if mu_c:
                mask = (s_p > LINE_STRENGTH_FLOOR) & ka_diff & ~sum_same
                weight[mask] += mu_c ** 2 * s_p[mask]
                kind[mask] = 2
        select = weight > 0.0
        if same_j:
            select &= np.arange(weight.shape[0])[:, None] > np.arange(weight.shape[1])[None, :]
        bi, ai = np.nonzero(select)
        if bi.size == 0:
            return
        e_b, e_a = up.energies[bi], lo.energies[ai]
        nu = e_b - e_a
        upper_is_b = nu > 0.0
        freq = np.abs(nu)
        e_low = np.where(upper_is_b, e_a, e_b)
        with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
            population = np.exp(-e_low / kt) * (-np.expm1(-freq / kt))
            intensity = JPL_INTENSITY_CONSTANT * freq * weight[bi, ai] * population / q_rot
            log_int = np.where(intensity > 0.0, np.log10(np.where(intensity > 0.0, intensity, 1.0)), -np.inf)
        e_low_cm = e_low / MHZ_PER_WAVENUMBER
        keep = (
            (freq >= max(freq_min_mhz, MIN_RESOLVABLE_FREQ_MHZ))
            & (freq <= freq_max_mhz)
            & np.isfinite(log_int)
            & (log_int >= min_log_intensity)
            & (e_low_cm < CAT_ELOW_LIMIT_CM)
        )
        for n in np.nonzero(keep)[0]:
            b, a = int(bi[n]), int(ai[n])
            state_b = (Jp, int(up.ka[b]), int(up.kc[b]))
            state_a = (J, int(lo.ka[a]), int(lo.kc[a]))
            upper, lower = (state_b, state_a) if upper_is_b[n] else (state_a, state_b)
            lines.append(
                {
                    "frequency_mhz": float(freq[n]),
                    "log10_intensity": float(log_int[n]),
                    "lower_energy_cm1": float(e_low_cm[n]),
                    "upper_degeneracy": 2 * upper[0] + 1,
                    "upper": upper,
                    "lower": lower,
                    "dipole_type": "abc"[int(kind[b, a])],
                    "line_strength_debye2": float(weight[b, a]),
                }
            )

    for J in range(j_max + 1):
        if J > 0:
            collect(levels[J], levels[J], same_j=True)
        if J + 1 <= j_max:
            collect(levels[J], levels[J + 1], same_j=False)
    lines.sort(key=lambda rec: (rec["frequency_mhz"], rec["upper"], rec["lower"]))
    return {
        "lines": lines,
        "partition_function": q_rot,
        "log10_partition_function": log_q,
        "partition_function_j_max": j_partition,
        "temperature_k": temperature_k,
        "freq_max_mhz": freq_max_mhz,
        "freq_min_mhz": freq_min_mhz,
        "j_max": j_max,
        "min_log_intensity": min_log_intensity,
    }


# ---------------------------------------------------------------------------
# Pickett file writers
# ---------------------------------------------------------------------------
def _pickett_qn(value: int) -> str:
    if 0 <= value <= 99:
        return f"{value:2d}"
    if 100 <= value <= CAT_MAX_QN:
        return chr(ord("A") + value // 10 - 10) + str(value % 10)
    raise CliUsageError(f"quantum number {value} cannot be encoded in the Pickett I2 field")


def _pickett_parameters(p: RotorParameters) -> List[Tuple[int, float, str]]:
    """(ID, value, label) with Pickett sign convention: distortion IDs hold -Delta."""
    if p.linear:
        rows = [(100, p.B, "B")]
        if p.DJ:
            rows.append((200, -p.DJ, "-D"))
        return rows
    rows = [(10000, float(p.A), "A"), (20000, p.B, "B"), (30000, p.C, "C")]
    for pid, value, label in (
        (200, p.DJ, "-DeltaJ"),
        (1100, p.DJK, "-DeltaJK"),
        (2000, p.DK, "-DeltaK"),
        (40100, p.dJ, "-deltaJ"),
        (41000, p.dK, "-deltaK"),
    ):
        if value:
            rows.append((pid, -value, label))
    return rows


def _pickett_title(stem: str, p: RotorParameters) -> str:
    a_text = "inf" if p.A is None else f"{p.A:.12g}"
    return (
        f"CoChem spectral-predict {stem}: Watson A-reduction I^r [MHz] A={a_text} B={p.B:.12g} "
        f"C={p.C:.12g} DJ={p.DJ:.12g} DJK={p.DJK:.12g} DK={p.DK:.12g} dJ={p.dJ:.12g} dK={p.dK:.12g}"
    )


def _pickett_par_text(stem: str, p: RotorParameters, j_max: int) -> str:
    rows = _pickett_parameters(p)
    out = [
        _pickett_title(stem, p),
        f"{len(rows):4d} {0:5d} {0:5d} {0:5d} {0.0:13.4E} {1.0e6:13.4E} {1.0:13.4E} {1.0:13.10f}",
        f"{'a':<2s}{1:4d}{1:5d}{0:5d}{j_max:5d}{0:5d}{1:5d}{1:5d}{1:5d}{0:5d}{1:5d}{0:5d}",
    ]
    for pid, value, label in rows:
        out.append(f"{pid:13d} {value: .15E} {1.0e-37:.8E} /{label}")
    return "\n".join(out) + "\n"


def _pickett_int_text(stem: str, p: RotorParameters, tag: int, catalog: Dict[str, Any], dipoles) -> str:
    out = [
        _pickett_title(stem, p),
        f" 0000 {tag:7d} {catalog['partition_function']:14.4f} {0:4d} {catalog['j_max']:4d} "
        f"{catalog['min_log_intensity']:8.2f} {catalog['min_log_intensity']:8.2f} "
        f"{catalog['freq_max_mhz'] / 1000.0:12.4f} {catalog['temperature_k']:10.3f}",
    ]
    for ident, mu in zip((1, 2, 3), dipoles):
        if mu:
            out.append(f" {ident:4d} {float(mu):12.6f}")
    return "\n".join(out) + "\n"


def _pickett_cat_line(rec: Dict[str, Any], tag: int, linear: bool) -> str:
    freq = min(rec["frequency_mhz"], CAT_FREQ_LIMIT_MHZ)
    head = (
        f"{freq:13.4f}{0.0:8.4f}{rec['log10_intensity']:8.4f}{(2 if linear else 3):2d}"
        f"{rec['lower_energy_cm1']:10.4f}{rec['upper_degeneracy']:3d}{tag:7d}{(101 if linear else 303):4d}"
    )

    def qn_block(state: Tuple[int, int, int]) -> str:
        values = [state[0]] if linear else list(state)
        return "".join(_pickett_qn(v) for v in values) + "  " * (6 - len(values))

    return head + qn_block(rec["upper"]) + qn_block(rec["lower"])


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_sanitize(v) for v in obj.tolist()]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _emit_json(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(_sanitize(payload), indent=2, allow_nan=False, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _say(args: argparse.Namespace, text: str) -> None:
    if not args.quiet:
        sys.stdout.write(text + "\n")


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                continue


def _configure_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.WARNING)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("cochem: %(levelname)s: %(message)s"))
    LOGGER.handlers[:] = [handler]
    LOGGER.setLevel(level)
    LOGGER.propagate = False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> int:
    deck = _load_deck(args.deck, args.raw_deck)
    n_electrons = _check_electron_count(deck)
    preconditioner = enforce_method_matrix(deck)
    try:
        routing = route_calculation(deck)
    except ValueError as exc:
        raise CliUsageError(str(exc)) from exc
    if args.nprocs < 1:
        raise CliUsageError("--nprocs must be a positive integer")
    if not (math.isfinite(args.timeout) and args.timeout > 0):
        raise CliUsageError("--timeout must be positive")
    try:
        maxcore = calculate_orca_maxcore(args.nprocs, available_ram_mb=args.available_ram_mb)
    except ValueError as exc:
        raise CliUsageError(str(exc)) from exc
    scratch_root = Path(args.scratch_dir).resolve() if args.scratch_dir else None
    ctx = RunContext(args.nprocs, maxcore, preconditioner, scratch_root, args.keep_scratch, args.timeout)
    payload: Dict[str, Any] = {
        "command": "run",
        "job_uuid": deck.job_uuid,
        "task_type": deck.task_type,
        "engine_requested": deck.engine,
        "routing": asdict(routing),
        "n_electrons": n_electrons,
        "maxcore_mb": maxcore,
        "nprocs": args.nprocs,
        "hessian_preconditioner": preconditioner,
    }
    if args.dry_run:
        payload["status"] = "validated"
        if args.json:
            _emit_json(payload)
        else:
            _say(args, f"deck {deck.job_uuid} valid; routed to {routing.engine} ({routing.reason})")
        return EXIT_OK
    executed, result, attempts = _dispatch(deck, routing, ctx)
    energy = float(result["energy_hartree"])
    if not math.isfinite(energy):
        raise ExternalEngineError(f"{executed} returned a non-finite energy")
    warnings = list(result.pop("warnings", []))
    if executed != routing.engine:
        warnings.insert(0, f"routed engine {routing.engine} unavailable on this host; executed {executed}")
    payload.update(
        {
            "status": "ok",
            "engine_executed": executed,
            "energy_hartree": energy,
            "energy_ev": energy * HARTREE_TO_EV,
            "fallback_chain": attempts,
            "warnings": warnings,
        }
    )
    payload.update({k: v for k, v in result.items() if k != "energy_hartree"})
    if args.json:
        _emit_json(payload)
    else:
        for warning in warnings:
            LOGGER.warning(warning)
        _say(args, f"job {deck.job_uuid}: routed {routing.engine} ({routing.reason})")
        _say(args, f"executed: {executed} [{payload.get('method_executed', '')}]")
        sys.stdout.write(f"E = {energy:.10f} Eh\n")
    return EXIT_OK


def _read_geometry(path_text: str) -> Tuple[List[str], List[List[float]]]:
    path = Path(path_text)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliUsageError(f"cannot read geometry {path_text!r}: {exc}") from exc
    lines = [ln for ln in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise CliUsageError(f"geometry file {path_text!r} is empty")
    head = lines[0].split()
    if len(head) == 1 and head[0].isdigit():
        n_atoms = int(head[0])
        atom_lines = [ln for ln in lines[2:2 + n_atoms]]
        if len(atom_lines) != n_atoms:
            raise CliUsageError(f"XYZ file declares {n_atoms} atoms but lists {len(atom_lines)}")
    else:
        atom_lines = [ln for ln in lines if ln.strip()]
    symbols: List[str] = []
    coords: List[List[float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            raise CliUsageError(f"malformed XYZ atom line: {line!r}")
        symbol = _normalize_symbol(parts[0])
        _lookup_element(symbol)
        try:
            xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
        except ValueError as exc:
            raise CliUsageError(f"non-numeric coordinate in line {line!r}") from exc
        if not all(math.isfinite(v) for v in xyz):
            raise CliUsageError(f"non-finite coordinate in line {line!r}")
        symbols.append(symbol)
        coords.append(xyz)
    if not symbols:
        raise CliUsageError("geometry contains no atoms")
    return symbols, coords


def _cmd_spectral_predict(args: argparse.Namespace) -> int:
    for name, value in (("--temperature", args.temperature), ("--freq-max", args.freq_max)):
        if not (math.isfinite(value) and value > 0.0):
            raise CliUsageError(f"{name} must be a positive finite number, got {value!r}")
    derived: Optional[Dict[str, Any]] = None
    symbols: List[str] = []
    if args.geometry:
        symbols, coords = _read_geometry(args.geometry)
        masses = _atom_masses(symbols, args.isotopes)
        derived = compute_inertia_rotational_constants(symbols, coords, masses)
        a_const, b_const, c_const = derived["A"], derived["B"], derived["C"]
    else:
        if args.rot_a is None or args.rot_b is None or args.rot_c is None:
            raise CliUsageError("spectral-predict needs --geometry or all of -A, -B and -C (MHz)")
        a_const, b_const, c_const = None, None, None
    if args.rot_a is not None:
        a_const = args.rot_a
    if args.rot_b is not None:
        b_const = args.rot_b
    if args.rot_c is not None:
        c_const = args.rot_c
    params = RotorParameters(
        A=a_const, B=float(b_const), C=float(c_const),
        DJ=args.DJ, DJK=args.DJK, DK=args.DK, dJ=args.small_delta_J, dK=args.small_delta_K,
    ).validate()
    kappa = compute_ray_asymmetry_parameter(params.A, params.B, params.C)
    mu = [args.mu_a, args.mu_b, args.mu_c]
    notes: List[str] = []
    if all(v is None for v in mu):
        mu = [1.0, 0.0, 0.0]
        notes.append("no dipole components supplied; a unit a-axis dipole (1 D) was assumed for intensities")
    dipoles = tuple(0.0 if v is None else float(v) for v in mu)
    catalog = predict_rotational_catalog(
        params,
        dipoles,
        temperature_k=args.temperature,
        freq_max_mhz=args.freq_max,
        freq_min_mhz=args.freq_min,
        j_max=args.j_max,
        min_log_intensity=args.min_log_intensity,
    )
    out_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.catalog_stem or (Path(args.geometry).stem if args.geometry else "cochem_rotor")
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    par_text = _pickett_par_text(stem, params, args.j_max)
    files = {
        "par": out_dir / f"{stem}.par",
        "var": out_dir / f"{stem}.var",
        "int": out_dir / f"{stem}.int",
        "cat": out_dir / f"{stem}.cat",
    }
    _write_text(files["par"], par_text)
    _write_text(files["var"], par_text)
    _write_text(files["int"], _pickett_int_text(stem, params, args.tag, catalog, dipoles))
    cat_lines = [_pickett_cat_line(rec, args.tag, params.linear) for rec in catalog["lines"]]
    _write_text(files["cat"], "".join(line + "\n" for line in cat_lines))
    if not catalog["lines"]:
        notes.append("no transitions satisfy the frequency and intensity bounds")
    strongest = sorted(catalog["lines"], key=lambda r: r["log10_intensity"], reverse=True)[:10]
    payload: Dict[str, Any] = {
        "command": "spectral-predict",
        "status": "ok",
        "rotational_constants_mhz": {"A": params.A, "B": params.B, "C": params.C},
        "ray_kappa": kappa,
        "linear_rotor": params.linear,
        "distortion_constants_mhz": {
            "DJ": params.DJ, "DJK": params.DJK, "DK": params.DK, "dJ": params.dJ, "dK": params.dK,
        },
        "dipole_components_debye": {"mu_a": dipoles[0], "mu_b": dipoles[1], "mu_c": dipoles[2]},
        "geometry_source": args.geometry,
        "mass_source": f"mendeleev ({args.isotopes})" if args.geometry else None,
        "temperature_k": args.temperature,
        "freq_max_mhz": args.freq_max,
        "partition_function": catalog["partition_function"],
        "partition_function_j_max": catalog["partition_function_j_max"],
        "n_lines": len(catalog["lines"]),
        "files": {k: str(v.resolve()) for k, v in files.items()},
        "strongest_lines": strongest,
        "notes": notes,
    }
    if derived is not None:
        payload["principal_moments_amu_angstrom2"] = derived["principal_moments_amu_angstrom2"]
        payload["atoms"] = symbols
    if args.json:
        _emit_json(payload)
    else:
        for note in notes:
            LOGGER.warning(note)
        a_text = "inf (linear)" if params.A is None else f"{params.A:.6f}"
        kappa_text = "undefined (spherical top)" if kappa is None else f"{kappa:.8f}"
        _say(args, f"A = {a_text} MHz  B = {params.B:.6f} MHz  C = {params.C:.6f} MHz")
        _say(args, f"Ray kappa = {kappa_text}")
        _say(args, f"Q({args.temperature:g} K) = {catalog['partition_function']:.4f}; {len(catalog['lines'])} lines")
        _say(args, f"catalog written to {files['cat']}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser and entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cochem",
        description="CoChem quantum-chemistry workflow and rotational-spectroscopy CLI.",
        allow_abbrev=False,
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON on stdout")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="debug logging on stderr")
    verbosity.add_argument("--quiet", action="store_true", help="suppress informational output")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    run = sub.add_parser(
        "run",
        help="validate a calculation deck and execute it (Method Matrix routing)",
        description="Validate a JSON calculation deck, apply the Method Matrix Sec. 8.3 rules and execute it.",
        allow_abbrev=False,
    )
    run.add_argument("deck", help="JSON calculation deck (QuantumCalculationDeckConfig fields) or '-' for stdin")
    run.add_argument("--raw-deck", default=None, help="raw engine input text to audit against the Method Matrix")
    run.add_argument("--nprocs", type=int, default=1, help="MPI processes / threads (default 1)")
    run.add_argument("--available-ram-mb", type=float, default=None, help="override detected available RAM (MB)")
    run.add_argument("--scratch-dir", default=None, help="persistent scratch root (default: temporary directory)")
    run.add_argument("--keep-scratch", action="store_true", help="keep temporary scratch directories")
    run.add_argument("--timeout", type=float, default=86400.0, help="external engine timeout in seconds")
    run.add_argument("--dry-run", action="store_true", help="validate and route only; do not execute")

    sp = sub.add_parser(
        "spectral-predict",
        help="rotational constants, Ray kappa and Pickett .par/.var/.int/.cat catalogue",
        description="Predict rotational constants from a geometry and compile a Pickett catalogue.",
        allow_abbrev=False,
    )
    sp.add_argument("--xyz", "--geometry", dest="geometry", default=None, help="XYZ geometry (Angstrom)")
    sp.add_argument("-A", "--rot-a", dest="rot_a", type=float, default=None, help="rotational constant A (MHz)")
    sp.add_argument("-B", "--rot-b", dest="rot_b", type=float, default=None, help="rotational constant B (MHz)")
    sp.add_argument("-C", "--rot-c", dest="rot_c", type=float, default=None, help="rotational constant C (MHz)")
    sp.add_argument("--DJ", dest="DJ", type=float, default=0.0, help="quartic distortion Delta_J (MHz)")
    sp.add_argument("--DJK", dest="DJK", type=float, default=0.0, help="quartic distortion Delta_JK (MHz)")
    sp.add_argument("--DK", dest="DK", type=float, default=0.0, help="quartic distortion Delta_K (MHz)")
    sp.add_argument("--small-delta-J", dest="small_delta_J", type=float, default=0.0, help="delta_J (MHz)")
    sp.add_argument("--small-delta-K", dest="small_delta_K", type=float, default=0.0, help="delta_K (MHz)")
    sp.add_argument("--mu-a", dest="mu_a", type=float, default=None, help="dipole component mu_a (Debye)")
    sp.add_argument("--mu-b", dest="mu_b", type=float, default=None, help="dipole component mu_b (Debye)")
    sp.add_argument("--mu-c", dest="mu_c", type=float, default=None, help="dipole component mu_c (Debye)")
    sp.add_argument("--temperature", type=float, default=300.0, help="rotational temperature in K (default 300)")
    sp.add_argument("--freq-max", dest="freq_max", type=float, default=1.0e6,
                    help="upper frequency bound of catalogue lines in MHz (default 1e6)")
    sp.add_argument("--freq-min", dest="freq_min", type=float, default=0.0, help="lower frequency bound in MHz")
    sp.add_argument("--j-max", dest="j_max", type=int, default=99, help="highest J in the catalogue (default 99)")
    sp.add_argument("--min-log-intensity", dest="min_log_intensity", type=float, default=-10.0,
                    help="log10 intensity cutoff in nm^2 MHz (default -10)")
    sp.add_argument("--tag", type=int, default=999, help="Pickett species tag (default 999)")
    sp.add_argument("--isotopes", choices=("standard", "most-abundant"), default="standard",
                    help="mendeleev standard atomic weights or most-abundant isotope masses")
    sp.add_argument("-o", "--output-dir", dest="output_dir", default=None, help="directory for Pickett files")
    sp.add_argument("--catalog-stem", dest="catalog_stem", default=None, help="file stem for Pickett files")
    return parser


def _report_error(args: argparse.Namespace, code: int, message: str) -> int:
    sys.stderr.write(f"cochem: error: {message}\n")
    if getattr(args, "json", False):
        _emit_json({"command": getattr(args, "command", None), "status": "error", "exit_code": code, "error": message})
    return code


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging(args)
    handlers = {"run": _cmd_run, "spectral-predict": _cmd_spectral_predict}
    try:
        return handlers[args.command](args)
    except CochemCliError as exc:
        return _report_error(args, exc.exit_code, str(exc))
    except ValidationError as exc:
        return _report_error(args, EXIT_USAGE, f"validation error: {exc}")
    except KeyboardInterrupt:
        return _report_error(args, EXIT_FAILURE, "interrupted")
    except Exception as exc:
        LOGGER.debug("unhandled failure", exc_info=True)
        return _report_error(args, EXIT_FAILURE, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
