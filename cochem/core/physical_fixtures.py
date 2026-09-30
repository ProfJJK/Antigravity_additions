"""Physical fallback service powered by the ASE EMT calculator.

When external quantum-chemistry binaries are absent, ``PhysicalFallbackService``
still evaluates genuine physics: Effective Medium Theory potential energies,
analytic forces and local geometry relaxations. Nothing is simulated or
hard-coded; every number originates from ``ase.calculators.emt.EMT``.

Design contracts:

* Every result is a frozen Pydantic v2 model with ``extra="forbid"``.
* Non-finite energies or forces raise ``PhysicalConvergenceError``.
* The net force on an isolated system is verified against a tolerance
  (Newton's third law, translational invariance) on the real forces.
* Relaxations use LBFGS with a step-acceptance rule: a step that raises the
  energy is reverted and the trust radius halved. Only accepted energies are
  recorded, so the reported trajectory is the true accepted history and is
  never sorted or clamped.
* Provenance (ASE version, calculator, interpreter, platform, UTC timestamp,
  wall time, geometry digest, external binaries on PATH) is captured at
  execution time.
* Newer ASE releases no longer export ``numeric_force`` from
  ``ase.calculators.test``. This module restores that public helper with a
  genuine central finite-difference implementation so that analytic forces can
  be cross-checked against numerical derivatives on every supported ASE.
"""

import hashlib
import json
import math
import platform as host_platform
import shutil
import sys
import time
from datetime import datetime, timezone
from typing import Any, List, Sequence

import ase
import numpy as np
from ase import Atoms
from ase.calculators.emt import EMT
from ase.calculators.emt import parameters as emt_parameters
from ase.optimize.lbfgs import LBFGS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ast_eradication_engine import PhysicalConvergenceError

try:
    import ase.calculators.test as ase_calculator_checks
except ImportError:
    ase_calculator_checks = None

__all__ = [
    "PhysicalConvergenceError",
    "UnsupportedElementError",
    "PhysicalSystemSpec",
    "PhysicalProvenance",
    "EnergyResult",
    "ForcesResult",
    "RelaxationResult",
    "PhysicalFallbackService",
    "numeric_force",
]

EXTERNAL_BINARIES = ("orca", "cfour", "xtb", "g16", "psi4", "dftb+")
NET_FORCE_TOLERANCE_EV_A = 1e-4
MONOTONIC_TOLERANCE_EV = 1e-12
INITIAL_MAXSTEP_A = 0.2
MINIMUM_MAXSTEP_A = 1e-4


def numeric_force(atoms: Atoms, a: int, i: int, d: float = 0.001) -> float:
    """Return the central finite-difference force component ``-dE/dx`` in eV/A.

    ``a`` is the atom index, ``i`` the Cartesian axis and ``d`` the displacement
    in Angstrom. The coordinate is displaced by ``+d`` and ``-d`` on the real
    attached calculator and restored to its original value afterwards, even if
    an energy evaluation raises.
    """
    original = float(atoms.positions[a, i])
    try:
        atoms.positions[a, i] = original + d
        energy_plus = float(atoms.get_potential_energy())
        atoms.positions[a, i] = original - d
        energy_minus = float(atoms.get_potential_energy())
    finally:
        atoms.positions[a, i] = original
    return (energy_minus - energy_plus) / (2.0 * d)


def _restore_numeric_force_export() -> None:
    """Expose ``numeric_force`` on ``ase.calculators.test`` when ASE dropped it."""
    if ase_calculator_checks is not None and not hasattr(ase_calculator_checks, "numeric_force"):
        ase_calculator_checks.numeric_force = numeric_force


_restore_numeric_force_export()


class UnsupportedElementError(ValueError):
    """Raised when a chemical symbol has no EMT parameters."""


class PhysicalSystemSpec(BaseModel):
    """Validated atomic system: chemical symbols and Cartesian positions in Angstrom."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbols: List[str] = Field(..., min_length=1)
    positions: List[List[float]] = Field(...)

    @model_validator(mode="after")
    def validate_geometry(self) -> "PhysicalSystemSpec":
        """Require an (N, 3) array of finite coordinates matching the symbol count."""
        if len(self.positions) != len(self.symbols):
            raise ValueError(
                f"{len(self.symbols)} symbols but {len(self.positions)} position rows"
            )
        for row in self.positions:
            if len(row) != 3:
                raise ValueError("every position row must have exactly 3 coordinates")
            if not all(math.isfinite(value) for value in row):
                raise ValueError("positions must be finite")
        return self

    def geometry_digest(self) -> str:
        """Return the SHA-256 digest of the canonical geometry serialisation."""
        payload = json.dumps(
            {"symbols": self.symbols, "positions": self.positions},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PhysicalProvenance(BaseModel):
    """Execution provenance captured while the calculation runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ase_version: str
    calculator: str
    python_version: str
    platform: str
    timestamp_utc: str
    wall_time_s: float = Field(..., ge=0.0, allow_inf_nan=False)
    geometry_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    external_binaries_found: List[str]
    steps: int = Field(default=0, ge=0)


class EnergyResult(BaseModel):
    """EMT potential energy of one geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    energy_ev: float = Field(..., allow_inf_nan=False)
    n_atoms: int = Field(..., ge=1)
    provenance: PhysicalProvenance


class ForcesResult(BaseModel):
    """Analytic EMT forces (eV/A) together with the net-force conservation residual."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    forces: List[List[float]]
    net_force_norm: float = Field(..., ge=0.0, allow_inf_nan=False)
    max_atomic_force: float = Field(..., ge=0.0, allow_inf_nan=False)
    provenance: PhysicalProvenance

    @field_validator("forces")
    @classmethod
    def validate_forces(cls, value: List[List[float]]) -> List[List[float]]:
        """Require finite (N, 3) forces."""
        for row in value:
            if len(row) != 3 or not all(math.isfinite(component) for component in row):
                raise ValueError("forces must be finite rows of three components")
        return value


class RelaxationResult(BaseModel):
    """Outcome of a converged local geometry relaxation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    energy_trajectory: List[float] = Field(..., min_length=1)
    n_steps: int = Field(..., ge=0)
    converged: bool
    final_positions: List[List[float]]
    fmax_target: float = Field(..., gt=0.0, allow_inf_nan=False)
    final_max_force: float = Field(..., ge=0.0, allow_inf_nan=False)
    provenance: PhysicalProvenance

    @model_validator(mode="after")
    def validate_trajectory(self) -> "RelaxationResult":
        """Require finite, non-increasing energies with one entry per accepted step."""
        history = self.energy_trajectory
        if not all(math.isfinite(value) for value in history):
            raise ValueError("energy trajectory must be finite")
        if len(history) != self.n_steps + 1:
            raise ValueError("energy trajectory must hold the initial energy plus one per step")
        for earlier, later in zip(history, history[1:]):
            if later - earlier > MONOTONIC_TOLERANCE_EV:
                raise ValueError("energy trajectory is not monotonically non-increasing")
        return self


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _is_supported(symbol: Any) -> bool:
    """Return True when EMT carries parameters for the chemical symbol."""
    return isinstance(symbol, str) and symbol in emt_parameters


class PhysicalFallbackService:
    """Energy, force and relaxation evaluation on the ASE EMT calculator."""

    def __init__(self, net_force_tolerance: float = NET_FORCE_TOLERANCE_EV_A) -> None:
        """Store the net-force conservation tolerance in eV/A."""
        if not math.isfinite(net_force_tolerance) or net_force_tolerance <= 0.0:
            raise ValueError("net_force_tolerance must be a positive finite number")
        self.net_force_tolerance = float(net_force_tolerance)

    @staticmethod
    def supported_elements() -> List[str]:
        """Return the sorted chemical symbols EMT can evaluate."""
        return sorted(emt_parameters)

    def _spec(self, symbols: Sequence[str], positions: Sequence[Sequence[float]]) -> PhysicalSystemSpec:
        """Validate elements first, then geometry, and return the system spec."""
        symbol_list = list(symbols)
        unsupported = sorted({str(s) for s in symbol_list if not _is_supported(s)})
        if unsupported:
            raise UnsupportedElementError(
                f"EMT has no parameters for: {', '.join(unsupported)}; "
                f"supported: {', '.join(self.supported_elements())}"
            )
        rows = [[float(component) for component in row] for row in positions]
        return PhysicalSystemSpec(symbols=symbol_list, positions=rows)

    @staticmethod
    def _atoms(spec: PhysicalSystemSpec) -> Atoms:
        """Build a non-periodic Atoms object carrying a fresh EMT calculator."""
        atoms = Atoms(symbols=list(spec.symbols), positions=np.asarray(spec.positions, dtype=float))
        atoms.calc = EMT()
        return atoms

    @staticmethod
    def _energy(atoms: Atoms) -> float:
        """Return the finite potential energy or raise a typed domain error."""
        energy = float(atoms.get_potential_energy())
        if not math.isfinite(energy):
            raise PhysicalConvergenceError(
                "EMT produced a non-finite potential energy", details={"energy": repr(energy)}
            )
        return energy

    def _forces(self, atoms: Atoms) -> np.ndarray:
        """Return finite analytic forces whose net sum obeys the conservation tolerance."""
        forces = np.asarray(atoms.get_forces(apply_constraint=False), dtype=float)
        if not np.all(np.isfinite(forces)):
            raise PhysicalConvergenceError("EMT produced non-finite forces")
        net = float(np.linalg.norm(forces.sum(axis=0)))
        if net > self.net_force_tolerance:
            raise PhysicalConvergenceError(
                "net force violates momentum conservation",
                details={"net_force_norm": net, "tolerance": self.net_force_tolerance},
            )
        return forces

    def _provenance(
        self, spec: PhysicalSystemSpec, atoms: Atoms, started: datetime, t0: float, steps: int
    ) -> PhysicalProvenance:
        """Assemble provenance from live execution state."""
        found = [name for name in EXTERNAL_BINARIES if shutil.which(name) is not None]
        return PhysicalProvenance(
            ase_version=ase.__version__,
            calculator=atoms.calc.__class__.__name__,
            python_version=host_platform.python_version(),
            platform=host_platform.platform() or sys.platform,
            timestamp_utc=started.isoformat(),
            wall_time_s=max(0.0, time.perf_counter() - t0),
            geometry_sha256=spec.geometry_digest(),
            external_binaries_found=found,
            steps=steps,
        )

    def evaluate_energy(
        self, symbols: Sequence[str], positions: Sequence[Sequence[float]]
    ) -> EnergyResult:
        """Evaluate the EMT potential energy (eV) of a geometry."""
        started = _utc_now()
        t0 = time.perf_counter()
        spec = self._spec(symbols, positions)
        atoms = self._atoms(spec)
        energy = self._energy(atoms)
        return EnergyResult(
            energy_ev=energy,
            n_atoms=len(spec.symbols),
            provenance=self._provenance(spec, atoms, started, t0, 0),
        )

    def evaluate_forces(
        self, symbols: Sequence[str], positions: Sequence[Sequence[float]]
    ) -> ForcesResult:
        """Evaluate analytic EMT forces (eV/A) and verify net-force conservation."""
        started = _utc_now()
        t0 = time.perf_counter()
        spec = self._spec(symbols, positions)
        atoms = self._atoms(spec)
        forces = self._forces(atoms)
        return ForcesResult(
            forces=forces.tolist(),
            net_force_norm=float(np.linalg.norm(forces.sum(axis=0))),
            max_atomic_force=float(np.linalg.norm(forces, axis=1).max()),
            provenance=self._provenance(spec, atoms, started, t0, 0),
        )

    @staticmethod
    def _optimizer(atoms: Atoms, maxstep: float) -> LBFGS:
        """Create a quiet LBFGS optimizer with the given trust radius."""
        return LBFGS(atoms, logfile=None, trajectory=None, maxstep=maxstep)

    def _max_force(self, atoms: Atoms) -> float:
        """Return the largest per-atom force norm in eV/A."""
        return float(np.linalg.norm(self._forces(atoms), axis=1).max())

    def relax(
        self,
        symbols: Sequence[str],
        positions: Sequence[Sequence[float]],
        fmax: float = 0.05,
        steps: int = 200,
    ) -> RelaxationResult:
        """Relax a geometry with LBFGS, accepting only energy-non-increasing steps.

        Raises ``PhysicalConvergenceError`` when the force criterion is not met
        within ``steps`` accepted steps or when the trust radius collapses.
        """
        if not math.isfinite(fmax) or fmax <= 0.0:
            raise ValueError("fmax must be a positive finite number")
        if steps < 1:
            raise ValueError("steps must be at least 1")
        started = _utc_now()
        t0 = time.perf_counter()
        spec = self._spec(symbols, positions)
        atoms = self._atoms(spec)

        energy = self._energy(atoms)
        trajectory: List[float] = [energy]
        maxstep = INITIAL_MAXSTEP_A
        optimizer = self._optimizer(atoms, maxstep)
        accepted = 0
        max_force = self._max_force(atoms)

        while max_force > fmax and accepted < steps:
            previous = atoms.get_positions()
            optimizer.step()
            candidate = self._energy(atoms)
            if candidate > energy + MONOTONIC_TOLERANCE_EV:
                atoms.set_positions(previous)
                maxstep *= 0.5
                if maxstep < MINIMUM_MAXSTEP_A:
                    raise PhysicalConvergenceError(
                        "trust radius collapsed without an energy-lowering step",
                        details={"energy_ev": energy, "max_force": max_force},
                    )
                optimizer = self._optimizer(atoms, maxstep)
                continue
            energy = candidate
            trajectory.append(energy)
            accepted += 1
            max_force = self._max_force(atoms)

        if max_force > fmax:
            raise PhysicalConvergenceError(
                "relaxation did not reach the force criterion",
                details={"fmax": fmax, "max_force": max_force, "steps": accepted},
            )
        return RelaxationResult(
            energy_trajectory=trajectory,
            n_steps=accepted,
            converged=True,
            final_positions=atoms.get_positions().tolist(),
            fmax_target=fmax,
            final_max_force=max_force,
            provenance=self._provenance(spec, atoms, started, t0, accepted),
        )
