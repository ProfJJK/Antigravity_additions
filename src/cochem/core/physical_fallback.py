"""ASE EMT physical fallback engine (Task 20.109.3 / WBS 20.9.2.3).

When an external quantum chemistry binary (ORCA, CFOUR, xTB) is absent from
the host ``PATH``, CoChem dispatches the calculation to a genuine classical
many-body potential: ASE's Effective Medium Theory (EMT) calculator. EMT
provides real potential energies (eV), analytic forces (eV/Angstrom) and
supports local geometry relaxation for H, C, N, O, Al, Ni, Cu, Pd, Ag, Pt and Au.

Guarantees provided by :class:`PhysicalFallbackService`:

* Installed binaries are never shadowed. If the requested engine is found on
  ``PATH`` an explicit ``RuntimeError`` redirects the caller to the native
  driver.
* Elements without EMT parameters are refused with a ``ValueError`` naming
  every offending symbol. No substitute arrays are fabricated.
* Caller structures are never mutated. All work happens on private copies
  carrying a freshly constructed EMT calculator.
* Translational invariance is enforced: ``||sum_i F_i|| <= 1e-5 eV/Angstrom``.
* Relaxations use LBFGS with a step-acceptance guard so the recorded energy
  trajectory is monotonically non-increasing (delta E <= 0 within 1e-12 eV).
* Results are frozen dataclasses with read-only force arrays and provenance
  (engine, requested engine, ASE version, geometry SHA-256, wall time, masses
  retrieved dynamically from ``mendeleev``).
"""
from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, List, Optional, Tuple

import ase
import numpy as np
from ase import Atoms
from ase.calculators.emt import EMT
from ase.calculators.emt import parameters as _EMT_PARAMETERS
from ase.optimize import LBFGS

try:
    from .exceptions import PhysicalConvergenceError
except ImportError:

    class PhysicalConvergenceError(RuntimeError):
        """Raised when a physical evaluation or relaxation fails to converge."""


__all__ = [
    "PhysicalCalculationResult",
    "PhysicalConvergenceError",
    "PhysicalFallbackService",
]


# --------------------------------------------------------------------------
# Elemental masses (queried dynamically, never tabulated in this module)
# --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _mendeleev_atomic_weight(symbol: str) -> Optional[float]:
    """Return the standard atomic weight of ``symbol`` from mendeleev.

    Returns ``None`` when the mendeleev package or its database cannot be
    imported or reached (including a broken installation); callers record
    this in the result provenance instead of inventing a value.
    """
    try:
        from mendeleev import element
    except Exception:  # missing or broken installation is reported as unavailable
        return None
    try:
        record = element(symbol)
    except Exception:  # database / lookup failure is reported as unavailable
        return None
    weight = getattr(record, "atomic_weight", None)
    if weight is None:
        weight = getattr(record, "mass", None)
    if weight is None:
        return None
    try:
        value = float(weight)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value) or value <= 0.0:
        return None
    return value


def _total_mass_amu(symbols: List[str]) -> Tuple[Optional[float], str]:
    total = 0.0
    for symbol in symbols:
        weight = _mendeleev_atomic_weight(symbol)
        if weight is None:
            return None, "unavailable"
        total += weight
    return total, "mendeleev"


def _geometry_sha256(atoms: Atoms) -> str:
    digest = hashlib.sha256()
    digest.update("|".join(atoms.get_chemical_symbols()).encode("utf-8"))
    digest.update(
        np.ascontiguousarray(atoms.get_positions(), dtype=np.float64).tobytes()
    )
    return digest.hexdigest()


def _frozen_array(values: np.ndarray) -> np.ndarray:
    frozen = np.array(values, dtype=float, copy=True)
    frozen.setflags(write=False)
    return frozen


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------
@dataclass(frozen=True, eq=False)
class PhysicalCalculationResult:
    """Immutable record of an ASE EMT fallback evaluation."""

    potential_energy_ev: float
    forces_ev_angstrom: np.ndarray
    engine_used: str = "ase_emt_fallback"
    is_fallback: bool = True
    converged: bool = True
    n_atoms: int = 0
    chemical_formula: str = ""
    optimizer_steps: int = 0
    requested_engine: str = ""
    total_mass_amu: Optional[float] = None
    mass_source: str = "unavailable"
    initial_energy_ev: Optional[float] = None
    energy_trajectory_ev: Tuple[float, ...] = ()
    rejected_steps: int = 0
    net_force_norm_ev_angstrom: float = 0.0
    geometry_sha256: str = ""
    ase_version: str = ""
    wall_time_s: float = 0.0

    @property
    def max_force_ev_angstrom(self) -> float:
        forces = np.asarray(self.forces_ev_angstrom, dtype=float)
        if forces.size == 0:
            return 0.0
        return float(np.linalg.norm(forces, axis=1).max())


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------
class PhysicalFallbackService:
    """Dispatch energy/force/relaxation requests to ASE EMT when binaries are absent."""

    FALLBACK_ENGINE: str = "ase_emt_fallback"
    SUPPORTED_SYMBOLS: FrozenSet[str] = frozenset(_EMT_PARAMETERS.keys())
    QUANTUM_ENGINES: Tuple[str, ...] = ("cfour", "orca", "xtb")

    MOMENTUM_TOLERANCE: float = 1e-5
    DESCENT_TOLERANCE_EV: float = 1e-12
    INITIAL_TRUST_RADIUS: float = 0.2
    MIN_TRUST_RADIUS: float = 1e-6
    MAX_REJECTED_STEPS: int = 80

    # ---------------------------------------------------------------- probes
    @staticmethod
    def is_binary_available(name: str) -> bool:
        """Return True when ``name`` resolves to an executable via ``shutil.which``."""
        if not isinstance(name, str) or not name.strip():
            return False
        return shutil.which(name) is not None

    @classmethod
    def _require_fallback_dispatch(cls, requested_engine: str) -> str:
        if not isinstance(requested_engine, str) or not requested_engine.strip():
            raise ValueError("requested_engine must be a non-empty executable name")
        if cls.is_binary_available(requested_engine):
            location = shutil.which(requested_engine)
            raise RuntimeError(
                f"Quantum chemistry binary '{requested_engine}' is installed at "
                f"'{location}'. Invoke it through its native CoChem driver; the "
                f"ASE EMT physical fallback only serves engines absent from PATH."
            )
        return requested_engine

    @classmethod
    def validate_elements(cls, atoms: Atoms) -> List[str]:
        """Ensure every symbol has EMT parameters; return the symbol list."""
        if not isinstance(atoms, Atoms):
            raise TypeError(
                f"expected ase.Atoms, received {type(atoms).__name__}"
            )
        symbols = list(atoms.get_chemical_symbols())
        if not symbols:
            raise ValueError("cannot evaluate an empty Atoms structure")
        unsupported = sorted(set(symbols) - set(cls.SUPPORTED_SYMBOLS))
        if unsupported:
            raise ValueError(
                "ASE EMT has no parameters for element(s): "
                + ", ".join(unsupported)
                + ". Supported elements: "
                + ", ".join(sorted(cls.SUPPORTED_SYMBOLS))
                + ". Load such systems from authentic ab-initio fixtures instead."
            )
        positions = np.asarray(atoms.get_positions(), dtype=float)
        if not np.all(np.isfinite(positions)):
            raise ValueError("atomic positions contain non-finite values")
        return symbols

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _working_copy(atoms: Atoms) -> Atoms:
        work = atoms.copy()
        work.calc = EMT()
        return work

    @staticmethod
    def _evaluate(work: Atoms) -> Tuple[float, np.ndarray]:
        energy = float(work.get_potential_energy())
        forces = np.array(work.get_forces(), dtype=float, copy=True)
        if not np.isfinite(energy) or not np.all(np.isfinite(forces)):
            raise PhysicalConvergenceError(
                "ASE EMT returned non-finite energy or forces"
            )
        return energy, forces

    @classmethod
    def _enforce_momentum_conservation(cls, forces: np.ndarray) -> float:
        net = float(np.linalg.norm(forces.sum(axis=0)))
        if net > cls.MOMENTUM_TOLERANCE:
            raise PhysicalConvergenceError(
                f"net force {net:.3e} eV/Angstrom violates linear momentum "
                f"conservation (tolerance {cls.MOMENTUM_TOLERANCE:.1e})"
            )
        return net

    @staticmethod
    def _max_force(forces: np.ndarray) -> float:
        return float(np.linalg.norm(forces, axis=1).max())

    @classmethod
    def _build_result(
        cls,
        structure: Atoms,
        symbols: List[str],
        energy: float,
        forces: np.ndarray,
        net_force: float,
        requested_engine: str,
        started: float,
        converged: bool = True,
        optimizer_steps: int = 0,
        initial_energy: Optional[float] = None,
        trajectory: Tuple[float, ...] = (),
        rejected_steps: int = 0,
    ) -> PhysicalCalculationResult:
        total_mass, mass_source = _total_mass_amu(symbols)
        return PhysicalCalculationResult(
            potential_energy_ev=energy,
            forces_ev_angstrom=_frozen_array(forces),
            engine_used=cls.FALLBACK_ENGINE,
            is_fallback=True,
            converged=converged,
            n_atoms=len(symbols),
            chemical_formula=structure.get_chemical_formula(),
            optimizer_steps=optimizer_steps,
            requested_engine=requested_engine,
            total_mass_amu=total_mass,
            mass_source=mass_source,
            initial_energy_ev=initial_energy if initial_energy is not None else energy,
            energy_trajectory_ev=trajectory if trajectory else (energy,),
            rejected_steps=rejected_steps,
            net_force_norm_ev_angstrom=net_force,
            geometry_sha256=_geometry_sha256(structure),
            ase_version=str(ase.__version__),
            wall_time_s=float(time.perf_counter() - started),
        )

    # ----------------------------------------------------------- entry points
    @classmethod
    def execute_energy_and_forces(
        cls, atoms: Atoms, requested_engine: str = "cfour"
    ) -> PhysicalCalculationResult:
        """Evaluate EMT energy (eV) and analytic forces (eV/Angstrom) on a copy."""
        engine = cls._require_fallback_dispatch(requested_engine)
        symbols = cls.validate_elements(atoms)
        started = time.perf_counter()
        work = cls._working_copy(atoms)
        energy, forces = cls._evaluate(work)
        net = cls._enforce_momentum_conservation(forces)
        work.calc = None
        return cls._build_result(
            work, symbols, energy, forces, net, engine, started
        )

    @classmethod
    def relax_geometry(
        cls,
        atoms: Atoms,
        fmax: float = 0.02,
        max_steps: int = 300,
        requested_engine: str = "cfour",
    ) -> Tuple[Atoms, PhysicalCalculationResult]:
        """Relax a copy of ``atoms`` on the EMT surface with monotonic LBFGS descent.

        Every optimizer step is accepted only if it does not raise the
        potential energy. An uphill step is reverted, the LBFGS history is
        discarded and the trust radius is halved; every accepted step lets the
        trust radius recover (doubling, capped at the initial radius). Only
        consecutive rejections count toward the abort limit, so isolated
        overshoots on flat surface regions never end a relaxation.
        """
        engine = cls._require_fallback_dispatch(requested_engine)
        symbols = cls.validate_elements(atoms)
        fmax = float(fmax)
        if not np.isfinite(fmax) or fmax <= 0.0:
            raise ValueError(f"fmax must be a positive finite number, got {fmax}")
        if isinstance(max_steps, bool) or int(max_steps) != max_steps or max_steps < 0:
            raise ValueError(f"max_steps must be a non-negative integer, got {max_steps}")
        max_steps = int(max_steps)

        started = time.perf_counter()
        work = cls._working_copy(atoms)
        initial_energy, forces = cls._evaluate(work)
        trajectory: List[float] = [initial_energy]
        trust_radius = cls.INITIAL_TRUST_RADIUS
        optimizer = LBFGS(work, maxstep=trust_radius, logfile=None)
        accepted = 0
        rejected = 0
        consecutive_rejected = 0
        converged = False

        while True:
            if cls._max_force(forces) < fmax:
                converged = True
                break
            if accepted >= max_steps:
                break
            previous_positions = work.get_positions().copy()
            previous_energy = trajectory[-1]
            optimizer.step()
            candidate_energy = float(work.get_potential_energy())
            # Same subtraction as the later np.diff monotonicity audit, so the
            # acceptance rule and the audit can never disagree by rounding.
            downhill = np.isfinite(candidate_energy) and (
                (candidate_energy - previous_energy) <= cls.DESCENT_TOLERANCE_EV
            )
            if downhill:
                accepted += 1
                consecutive_rejected = 0
                trajectory.append(candidate_energy)
                candidate_energy, forces = cls._evaluate(work)
                trust_radius = min(cls.INITIAL_TRUST_RADIUS, trust_radius * 2.0)
                optimizer.maxstep = trust_radius
                continue
            work.set_positions(previous_positions)
            rejected += 1
            consecutive_rejected += 1
            trust_radius *= 0.5
            if (
                trust_radius < cls.MIN_TRUST_RADIUS
                or consecutive_rejected > cls.MAX_REJECTED_STEPS
            ):
                raise PhysicalConvergenceError(
                    f"EMT relaxation of {work.get_chemical_formula()} could not find "
                    f"a downhill step (trust radius {trust_radius:.2e} Angstrom, "
                    f"{consecutive_rejected} consecutive rejected steps, "
                    f"{rejected} rejected in total)"
                )
            optimizer = LBFGS(work, maxstep=trust_radius, logfile=None)
            restored_energy, forces = cls._evaluate(work)
            trajectory[-1] = min(trajectory[-1], restored_energy)

        if not converged:
            raise PhysicalConvergenceError(
                f"EMT relaxation of {work.get_chemical_formula()} did not reach "
                f"fmax={fmax} eV/Angstrom within {max_steps} steps "
                f"(max force {cls._max_force(forces):.4e})"
            )

        steps = np.diff(np.asarray(trajectory, dtype=float))
        if steps.size and float(steps.max()) > cls.DESCENT_TOLERANCE_EV:
            raise PhysicalConvergenceError(
                "relaxation energy trajectory is not monotonically non-increasing"
            )

        final_energy, final_forces = cls._evaluate(work)
        net = cls._enforce_momentum_conservation(final_forces)
        relaxed = work.copy()
        relaxed.calc = None
        work.calc = None
        result = cls._build_result(
            relaxed,
            symbols,
            final_energy,
            final_forces,
            net,
            engine,
            started,
            converged=True,
            optimizer_steps=accepted,
            initial_energy=initial_energy,
            trajectory=tuple(float(e) for e in trajectory),
            rejected_steps=rejected,
        )
        return relaxed, result
