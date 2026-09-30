"""Authentic physical fixtures loader for CoChem (Task 20.109.2, WBS 20.9.2.2).

This module locates, verifies and loads two serialized physical artifacts:

* ``complexes.h5``: transition-metal complex geometries, one HDF5 group per
  complex, named ``<Hill formula>_<first 12 hex chars of geometry digest>``.
* ``authentic_water_hessian.npy``: the Cartesian Hessian of relaxed water.

Integrity contract
------------------
``authentic_fixtures_manifest.json`` sits in the same directory as the
artifacts. It is written by ``cochem.core.fixture_builder`` and records the
SHA-256 digest of every artifact file. Before parsing, each artifact is hashed
bit for bit and compared with the manifest. Any mismatch raises
``FixtureIntegrityError``. Each complex also carries a per-geometry digest over
its canonical JSON serialisation (atomic numbers, coordinates, charge,
multiplicity), and that digest is recomputed on load.

Nothing in this module constructs coordinates or matrices. All arrays come from
disk. Molecular masses are resolved at run time through ``mendeleev``. The
zero-mode cutoff used for Hessian validation is relative to the measured
spectral radius, not an absolute constant.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import h5py
import numpy as np
from mendeleev import element as mendeleev_element

__all__ = [
    "COMPLEXES_FILENAME",
    "WATER_HESSIAN_FILENAME",
    "MANIFEST_FILENAME",
    "SCHEMA_ID",
    "PhysicalFixtureError",
    "FixtureNotFoundError",
    "FixtureIntegrityError",
    "DynamicMendeleevResolver",
    "MendeleevMassService",
    "AuthenticComplexFixture",
    "ComplexMetadataRecord",
    "HessianValidationReport",
    "HessianFixtureValidator",
    "PhysicalFixtureLoader",
    "AbInitioComplexRegistry",
    "sha256_file",
    "canonical_geometry_digest",
    "hill_formula",
    "metal_symbol_of",
    "load_authentic_complexes",
    "load_authentic_water_hessian",
]

COMPLEXES_FILENAME = "complexes.h5"
WATER_HESSIAN_FILENAME = "authentic_water_hessian.npy"
MANIFEST_FILENAME = "authentic_fixtures_manifest.json"
SCHEMA_ID = "cochem.physical_fixtures/1"

ENV_COMPLEXES_H5 = "COCHEM_COMPLEXES_H5"
ENV_WATER_HESSIAN_NPY = "COCHEM_WATER_HESSIAN_NPY"

MODULE_DIR = Path(__file__).resolve().parent

SYMMETRY_TOLERANCE = 1e-6
ZERO_MODE_RELATIVE_CUTOFF = 1e-3
OFF_DIAGONAL_FLOOR = 1e-10
METAL_BLOCKS = ("d", "f")
_HASH_CHUNK_BYTES = 1 << 20


class PhysicalFixtureError(Exception):
    """Base error for physical fixture location, integrity and validation failures."""


class FixtureNotFoundError(PhysicalFixtureError, FileNotFoundError):
    """Raised when a required fixture artifact cannot be located."""


class FixtureIntegrityError(PhysicalFixtureError):
    """Raised when an artifact fails SHA-256, schema or physical validation."""


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of the exact bytes of ``path``."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_geometry_digest(
    atomic_numbers: Sequence[int],
    coordinates: Sequence[Sequence[float]],
    formal_charge: int,
    spin_multiplicity: int,
) -> str:
    """Return the SHA-256 digest of the canonical JSON serialisation of a geometry.

    Floats are written with Python's shortest round-trip ``repr``, so float64
    coordinates read back from HDF5 reproduce the digest bit for bit.
    """
    payload = {
        "atomic_numbers": [int(z) for z in atomic_numbers],
        "coordinates_angstrom": [[float(c) for c in row] for row in coordinates],
        "formal_charge": int(formal_charge),
        "spin_multiplicity": int(spin_multiplicity),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decode(value: Any) -> Any:
    """Convert h5py attribute values (bytes, numpy scalars, arrays) to Python values."""
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8").strip()
    if isinstance(value, np.ndarray):
        if value.shape == ():
            return _decode(value[()])
        return [_decode(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _decode(value.item())
    if isinstance(value, str):
        return value.strip()
    return value


def _attr_text(attrs: Mapping[str, Any], key: str, owner: str) -> str:
    """Return a required, non-empty string attribute."""
    if key not in attrs:
        raise FixtureIntegrityError(f"{owner}: missing attribute '{key}'")
    value = _decode(attrs[key])
    if not isinstance(value, str) or not value:
        raise FixtureIntegrityError(f"{owner}: attribute '{key}' must be a non-empty string")
    return value


def _attr_int(attrs: Mapping[str, Any], key: str, owner: str) -> int:
    """Return a required integral attribute."""
    if key not in attrs:
        raise FixtureIntegrityError(f"{owner}: missing attribute '{key}'")
    value = _decode(attrs[key])
    if isinstance(value, bool):
        raise FixtureIntegrityError(f"{owner}: attribute '{key}' must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise FixtureIntegrityError(f"{owner}: attribute '{key}' must be integral")
        return int(value)
    if not isinstance(value, int):
        raise FixtureIntegrityError(f"{owner}: attribute '{key}' must be an integer")
    return value


# --------------------------------------------------------------------------
# Dynamic elemental data (mendeleev)
# --------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _cached_mass(atomic_number: int) -> float:
    mass = mendeleev_element(atomic_number).mass
    if mass is None or not math.isfinite(float(mass)) or float(mass) <= 0.0:
        raise ValueError(f"mendeleev has no standard atomic mass for Z={atomic_number}")
    return float(mass)


@lru_cache(maxsize=256)
def _cached_symbol(atomic_number: int) -> str:
    return str(mendeleev_element(atomic_number).symbol)


@lru_cache(maxsize=256)
def _cached_atomic_number(symbol: str) -> int:
    return int(mendeleev_element(symbol).atomic_number)


@lru_cache(maxsize=256)
def _cached_block(atomic_number: int) -> str:
    return str(mendeleev_element(atomic_number).block)


class DynamicMendeleevResolver:
    """Run-time elemental lookups backed by the mendeleev database."""

    @staticmethod
    def normalise_symbol(symbol: str) -> str:
        """Return a chemical symbol in canonical capitalisation (``fe`` -> ``Fe``)."""
        text = str(symbol).strip()
        if not text or not text.isalpha() or len(text) > 3:
            raise ValueError(f"not a chemical symbol: {symbol!r}")
        return text[0].upper() + text[1:].lower()

    @classmethod
    def _atomic_number_of(cls, identifier: Any) -> int:
        if isinstance(identifier, (bool, np.bool_)):
            raise TypeError("booleans are not element identifiers")
        if isinstance(identifier, (int, np.integer)):
            number = int(identifier)
            if number < 1:
                raise ValueError(f"atomic number must be >= 1, got {number}")
            return number
        if isinstance(identifier, str):
            return _cached_atomic_number(cls.normalise_symbol(identifier))
        raise TypeError(f"unsupported element identifier: {identifier!r}")

    @classmethod
    def element(cls, identifier: Any) -> Any:
        """Return the mendeleev element record for an atomic number or symbol."""
        return mendeleev_element(cls._atomic_number_of(identifier))

    @classmethod
    def atomic_number(cls, symbol: str) -> int:
        """Return the atomic number of a chemical symbol."""
        return _cached_atomic_number(cls.normalise_symbol(symbol))

    @classmethod
    def symbol(cls, atomic_number: int) -> str:
        """Return the chemical symbol of an atomic number."""
        return _cached_symbol(cls._atomic_number_of(atomic_number))

    @classmethod
    def block(cls, identifier: Any) -> str:
        """Return the periodic-table block (s, p, d, f) of an element."""
        return _cached_block(cls._atomic_number_of(identifier))

    @classmethod
    def standard_mass(cls, identifier: Any) -> float:
        """Return the standard atomic weight (amu) reported by mendeleev."""
        return _cached_mass(cls._atomic_number_of(identifier))


class MendeleevMassService:
    """Molecular mass evaluation from mendeleev standard atomic weights."""

    @classmethod
    def standard_atomic_mass(cls, identifier: Any) -> float:
        """Return the standard atomic weight (amu) of one element."""
        return DynamicMendeleevResolver.standard_mass(identifier)

    @classmethod
    def total_mass(cls, atomic_numbers: Sequence[int]) -> float:
        """Return the summed standard atomic weights (amu) of the given atoms."""
        numbers = [int(z) for z in atomic_numbers]
        if not numbers:
            raise ValueError("cannot compute the mass of an empty atom list")
        return math.fsum(DynamicMendeleevResolver.standard_mass(z) for z in numbers)


def hill_formula(atomic_numbers: Sequence[int]) -> str:
    """Return the Hill-system formula (C, H first when carbon is present; else alphabetical)."""
    counts = Counter(DynamicMendeleevResolver.symbol(int(z)) for z in atomic_numbers)
    if not counts:
        raise ValueError("cannot build a formula from zero atoms")
    if "C" in counts:
        lead = [s for s in ("C", "H") if s in counts]
        order = lead + sorted(s for s in counts if s not in lead)
    else:
        order = sorted(counts)
    return "".join(s if counts[s] == 1 else f"{s}{counts[s]}" for s in order)


def metal_symbol_of(atomic_numbers: Sequence[int]) -> str:
    """Return the single d- or f-block element present, or raise if not exactly one."""
    metals = sorted(
        {DynamicMendeleevResolver.symbol(int(z)) for z in atomic_numbers
         if DynamicMendeleevResolver.block(int(z)) in METAL_BLOCKS}
    )
    if len(metals) != 1:
        raise ValueError(f"expected exactly one d/f-block element, found {metals}")
    return metals[0]


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthenticComplexFixture:
    """One verified complex geometry loaded from complexes.h5."""

    complex_id: str
    stoichiometry: str
    metal_symbol: str
    atomic_numbers: Tuple[int, ...]
    coordinates_angstrom: Tuple[Tuple[float, float, float], ...]
    formal_charge: int
    spin_multiplicity: int
    geometry_type: str
    provenance_hash: str
    total_molecular_mass_amu: float
    source_path: str = ""

    @property
    def n_atoms(self) -> int:
        """Number of atoms in the complex."""
        return len(self.atomic_numbers)

    def coordinates_array(self) -> np.ndarray:
        """Return a fresh (N, 3) float64 copy of the Cartesian coordinates in Angstrom."""
        return np.asarray(self.coordinates_angstrom, dtype=np.float64).copy()

    def to_ase_atoms(self) -> Any:
        """Return an ``ase.Atoms`` object carrying charge and multiplicity in ``info``."""
        from ase import Atoms

        atoms = Atoms(numbers=list(self.atomic_numbers), positions=self.coordinates_array())
        atoms.info.update(
            {
                "complex_id": self.complex_id,
                "formal_charge": self.formal_charge,
                "spin_multiplicity": self.spin_multiplicity,
                "provenance_hash": self.provenance_hash,
            }
        )
        return atoms


@dataclass(frozen=True)
class ComplexMetadataRecord:
    """Coordinate-free metadata view of an ``AuthenticComplexFixture``."""

    complex_id: str
    stoichiometry: str
    metal_symbol: str
    n_atoms: int
    formal_charge: int
    spin_multiplicity: int
    geometry_type: str
    provenance_hash: str
    total_molecular_mass_amu: float
    source_path: str

    @classmethod
    def from_fixture(cls, fixture: AuthenticComplexFixture) -> "ComplexMetadataRecord":
        """Project a fixture onto its metadata record."""
        return cls(
            complex_id=fixture.complex_id,
            stoichiometry=fixture.stoichiometry,
            metal_symbol=fixture.metal_symbol,
            n_atoms=fixture.n_atoms,
            formal_charge=fixture.formal_charge,
            spin_multiplicity=fixture.spin_multiplicity,
            geometry_type=fixture.geometry_type,
            provenance_hash=fixture.provenance_hash,
            total_molecular_mass_amu=fixture.total_molecular_mass_amu,
            source_path=fixture.source_path,
        )


@dataclass(frozen=True)
class HessianValidationReport:
    """Properties measured from a Cartesian Hessian tensor."""

    shape: Tuple[int, int]
    max_asymmetry: float
    frobenius_norm: float
    nonzero_elements: int
    nonzero_off_diagonal_elements: int
    eigenvalues: Tuple[float, ...]
    zero_mode_threshold: float
    rigid_body_modes: int
    vibrational_eigenvalues: Tuple[float, ...]
    positive_modes: int


class HessianFixtureValidator:
    """Physical validation of a Cartesian Hessian using data-derived thresholds."""

    @staticmethod
    def validate(hessian: np.ndarray, rigid_body_modes: Optional[int] = None) -> HessianValidationReport:
        """Validate shape, dtype, symmetry, rigid-body null space and vibrational curvature.

        The zero-mode cutoff is ``ZERO_MODE_RELATIVE_CUTOFF`` times the spectral
        radius of the tensor. When ``rigid_body_modes`` is None, 5 (linear) or 6
        (nonlinear) rigid-body modes are accepted for three or more atoms.
        """
        matrix = np.asarray(hessian)
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape[0] % 3:
            raise FixtureIntegrityError(f"Hessian must be square with 3N rows, got {matrix.shape}")
        if matrix.dtype != np.float64:
            raise FixtureIntegrityError(f"Hessian must be float64, got {matrix.dtype}")
        if not np.all(np.isfinite(matrix)):
            raise FixtureIntegrityError("Hessian contains non-finite values")

        max_asymmetry = float(np.max(np.abs(matrix - matrix.T)))
        if max_asymmetry >= SYMMETRY_TOLERANCE:
            raise FixtureIntegrityError(f"Hessian asymmetry {max_asymmetry:.3e} exceeds tolerance")
        frobenius = float(np.linalg.norm(matrix, "fro"))
        if not frobenius > 0.0:
            raise FixtureIntegrityError("Hessian has vanishing Frobenius norm")

        off_diagonal = matrix - np.diag(np.diag(matrix))
        nonzero_off = int(np.count_nonzero(np.abs(off_diagonal) > OFF_DIAGONAL_FLOOR))
        if nonzero_off == 0:
            raise FixtureIntegrityError("Hessian has no off-diagonal coupling")

        eigenvalues = np.linalg.eigvalsh(0.5 * (matrix + matrix.T))
        spectral_radius = float(np.max(np.abs(eigenvalues)))
        threshold = ZERO_MODE_RELATIVE_CUTOFF * spectral_radius
        zero_mask = np.abs(eigenvalues) <= threshold
        rigid = int(np.sum(zero_mask))
        vibrational = eigenvalues[~zero_mask]

        n_atoms = matrix.shape[0] // 3
        if rigid_body_modes is not None:
            allowed = {int(rigid_body_modes)}
        elif n_atoms == 1:
            allowed = {3}
        elif n_atoms == 2:
            allowed = {5}
        else:
            allowed = {5, 6}
        if rigid not in allowed:
            raise FixtureIntegrityError(
                f"found {rigid} rigid-body modes (|lambda| <= {threshold:.3e}); expected {sorted(allowed)}"
            )
        if vibrational.size != matrix.shape[0] - rigid or not np.all(vibrational > 0.0):
            raise FixtureIntegrityError("vibrational eigenvalues must be strictly positive")
        if float(np.min(eigenvalues)) < -threshold:
            raise FixtureIntegrityError("Hessian has significant negative curvature")
        if vibrational.size and float(np.max(np.abs(eigenvalues[zero_mask]))) * 2.0 >= float(np.min(vibrational)):
            raise FixtureIntegrityError("rigid-body and vibrational modes are not separated")

        return HessianValidationReport(
            shape=(int(matrix.shape[0]), int(matrix.shape[1])),
            max_asymmetry=max_asymmetry,
            frobenius_norm=frobenius,
            nonzero_elements=int(np.count_nonzero(matrix)),
            nonzero_off_diagonal_elements=nonzero_off,
            eigenvalues=tuple(float(w) for w in eigenvalues),
            zero_mode_threshold=threshold,
            rigid_body_modes=rigid,
            vibrational_eigenvalues=tuple(float(w) for w in vibrational),
            positive_modes=int(np.sum(vibrational > 0.0)),
        )


# --------------------------------------------------------------------------
# Manifest / integrity
# --------------------------------------------------------------------------
def _read_manifest(directory: Path) -> Dict[str, Any]:
    path = Path(directory) / MANIFEST_FILENAME
    if not path.is_file():
        raise FixtureIntegrityError(
            f"no {MANIFEST_FILENAME} next to the artifacts in {directory}; digests cannot be verified"
        )
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_ID:
        raise FixtureIntegrityError(f"{path}: unexpected manifest schema")
    if not isinstance(manifest.get("artifacts"), dict):
        raise FixtureIntegrityError(f"{path}: manifest lacks an 'artifacts' table")
    return manifest


def _verify_artifact_digest(path: Path) -> str:
    """Hash ``path`` and compare with the manifest; return the verified digest."""
    manifest = _read_manifest(path.parent)
    record = manifest["artifacts"].get(path.name)
    if not isinstance(record, dict) or not isinstance(record.get("sha256"), str):
        raise FixtureIntegrityError(f"manifest has no SHA-256 record for {path.name}")
    expected = record["sha256"].strip().lower()
    actual = sha256_file(path)
    if actual != expected:
        raise FixtureIntegrityError(
            f"SHA-256 mismatch for {path}: manifest {expected}, file {actual}"
        )
    return actual


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------
class PhysicalFixtureLoader:
    """Locate, verify and load the authentic physical fixture artifacts."""

    def __init__(self, search_roots: Optional[Sequence[Path]] = None) -> None:
        """Use explicit ``search_roots``, or the module directory plus env overrides."""
        if search_roots is None:
            self._use_environment = True
            roots: List[Path] = [MODULE_DIR]
        else:
            self._use_environment = False
            roots = [Path(root) for root in search_roots]
            if not roots:
                raise ValueError("search_roots must contain at least one directory")
        self.search_roots: Tuple[Path, ...] = tuple(r.expanduser().resolve() for r in roots)

    def _locate(self, filename: str, env_var: str) -> Path:
        if self._use_environment:
            override = os.environ.get(env_var, "").strip()
            if override:
                candidate = Path(override).expanduser()
                if not candidate.is_file():
                    raise FixtureNotFoundError(f"{env_var} points to a missing file: {candidate}")
                return candidate.resolve()
        for root in self.search_roots:
            candidate = root / filename
            if candidate.is_file():
                return candidate
        searched = ", ".join(str(r) for r in self.search_roots)
        raise FixtureNotFoundError(f"{filename} not found in: {searched}")

    def locate_complexes_h5(self) -> Path:
        """Return the path of complexes.h5."""
        return self._locate(COMPLEXES_FILENAME, ENV_COMPLEXES_H5)

    def locate_water_hessian(self) -> Path:
        """Return the path of authentic_water_hessian.npy."""
        return self._locate(WATER_HESSIAN_FILENAME, ENV_WATER_HESSIAN_NPY)

    def manifest(self, artifact_path: Optional[Path] = None) -> Dict[str, Any]:
        """Return the provenance manifest that accompanies the artifacts."""
        anchor = Path(artifact_path) if artifact_path is not None else self.locate_water_hessian()
        return _read_manifest(anchor.parent)

    @staticmethod
    def _read_complex(name: str, group: h5py.Group, source: Path) -> AuthenticComplexFixture:
        for dataset_name in ("atomic_numbers", "coordinates_angstrom"):
            if dataset_name not in group or not isinstance(group[dataset_name], h5py.Dataset):
                raise FixtureIntegrityError(f"{name}: missing dataset '{dataset_name}'")
        numbers_raw = np.asarray(group["atomic_numbers"][()])
        coordinates = np.asarray(group["coordinates_angstrom"][()], dtype=np.float64)
        if numbers_raw.ndim != 1 or numbers_raw.dtype.kind not in "iu":
            raise FixtureIntegrityError(f"{name}: atomic_numbers must be a 1-D integer dataset")
        atomic_numbers = tuple(int(z) for z in numbers_raw)
        if not atomic_numbers or any(z < 1 for z in atomic_numbers):
            raise FixtureIntegrityError(f"{name}: invalid atomic numbers")
        if coordinates.shape != (len(atomic_numbers), 3):
            raise FixtureIntegrityError(f"{name}: coordinates shape {coordinates.shape} is not (N, 3)")
        if not np.all(np.isfinite(coordinates)):
            raise FixtureIntegrityError(f"{name}: non-finite coordinates")

        attrs = group.attrs
        charge = _attr_int(attrs, "formal_charge", name)
        multiplicity = _attr_int(attrs, "spin_multiplicity", name)
        if multiplicity < 1:
            raise FixtureIntegrityError(f"{name}: spin multiplicity must be >= 1")
        electrons = sum(atomic_numbers) - charge
        unpaired = multiplicity - 1
        if electrons < unpaired or (electrons - unpaired) % 2:
            raise FixtureIntegrityError(
                f"{name}: multiplicity {multiplicity} inconsistent with {electrons} electrons"
            )

        rows = tuple(tuple(float(c) for c in row) for row in coordinates)
        digest = canonical_geometry_digest(atomic_numbers, rows, charge, multiplicity)
        recorded = _attr_text(attrs, "provenance_hash", name).lower()
        if recorded != digest:
            raise FixtureIntegrityError(f"{name}: geometry digest {digest} != recorded {recorded}")

        stoichiometry = hill_formula(atomic_numbers)
        if _attr_text(attrs, "stoichiometry", name) != stoichiometry:
            raise FixtureIntegrityError(f"{name}: stoichiometry attribute disagrees with atoms")
        if name != f"{stoichiometry}_{digest[:12]}":
            raise FixtureIntegrityError(f"{name}: group name does not match formula and digest")
        metal = metal_symbol_of(atomic_numbers)
        if _attr_text(attrs, "metal_symbol", name) != metal:
            raise FixtureIntegrityError(f"{name}: metal_symbol attribute disagrees with atoms")

        return AuthenticComplexFixture(
            complex_id=name,
            stoichiometry=stoichiometry,
            metal_symbol=metal,
            atomic_numbers=atomic_numbers,
            coordinates_angstrom=rows,
            formal_charge=charge,
            spin_multiplicity=multiplicity,
            geometry_type=_attr_text(attrs, "geometry_type", name),
            provenance_hash=digest,
            total_molecular_mass_amu=MendeleevMassService.total_mass(atomic_numbers),
            source_path=str(source),
        )

    def load_complexes(self, h5_path: Optional[Path] = None) -> Dict[str, AuthenticComplexFixture]:
        """Verify and load every complex group from complexes.h5."""
        path = Path(h5_path) if h5_path is not None else self.locate_complexes_h5()
        if not path.is_file():
            raise FixtureNotFoundError(f"complexes container not found: {path}")
        _verify_artifact_digest(path)
        if not h5py.is_hdf5(str(path)):
            raise FixtureIntegrityError(f"{path} is not an HDF5 container")
        complexes: Dict[str, AuthenticComplexFixture] = {}
        with h5py.File(path, "r") as handle:
            if _decode(handle.attrs.get("schema", b"")) != SCHEMA_ID:
                raise FixtureIntegrityError(f"{path}: unexpected container schema")
            for name in sorted(handle.keys()):
                obj = handle[name]
                if isinstance(obj, h5py.Group):
                    complexes[name] = self._read_complex(name, obj, path)
        if not complexes:
            raise FixtureIntegrityError(f"{path} contains no complex groups")
        return complexes

    def load_water_hessian(self, npy_path: Optional[Path] = None) -> np.ndarray:
        """Verify and load the water Cartesian Hessian as a (3N, 3N) float64 array."""
        path = Path(npy_path) if npy_path is not None else self.locate_water_hessian()
        if not path.is_file():
            raise FixtureNotFoundError(f"Hessian tensor not found: {path}")
        _verify_artifact_digest(path)
        with path.open("rb") as handle:
            hessian = np.load(handle, allow_pickle=False)
        HessianFixtureValidator.validate(hessian)
        return hessian

    def water_hessian_report(self, npy_path: Optional[Path] = None) -> HessianValidationReport:
        """Return the measured validation report of the verified water Hessian."""
        return HessianFixtureValidator.validate(self.load_water_hessian(npy_path))

    def registry(self, h5_path: Optional[Path] = None) -> "AbInitioComplexRegistry":
        """Return a registry over the verified complexes."""
        return AbInitioComplexRegistry(self.load_complexes(h5_path))


class AbInitioComplexRegistry:
    """Read-only lookup table of verified complexes keyed by complex id."""

    def __init__(self, complexes: Dict[str, AuthenticComplexFixture]) -> None:
        """Store a copy of the id -> fixture mapping."""
        for key, fixture in complexes.items():
            if key != fixture.complex_id:
                raise ValueError(f"registry key {key!r} != fixture id {fixture.complex_id!r}")
        self._complexes: Dict[str, AuthenticComplexFixture] = dict(complexes)

    @classmethod
    def from_h5(cls, h5_path: Optional[Path] = None) -> "AbInitioComplexRegistry":
        """Build a registry from complexes.h5 (located by the default loader when omitted)."""
        return PhysicalFixtureLoader().registry(h5_path)

    def __len__(self) -> int:
        return len(self._complexes)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._complexes))

    def __contains__(self, complex_id: object) -> bool:
        return complex_id in self._complexes

    def ids(self) -> List[str]:
        """Return all complex ids in sorted order."""
        return sorted(self._complexes)

    def get(self, complex_id: str) -> AuthenticComplexFixture:
        """Return one fixture; raise KeyError for an unknown id."""
        if complex_id not in self._complexes:
            raise KeyError(f"no complex with id {complex_id!r}")
        return self._complexes[complex_id]

    def metadata(self, complex_id: str) -> ComplexMetadataRecord:
        """Return the metadata record of one complex."""
        return ComplexMetadataRecord.from_fixture(self.get(complex_id))

    def by_metal(self, symbol: str) -> List[AuthenticComplexFixture]:
        """Return all complexes whose central metal is ``symbol``."""
        target = DynamicMendeleevResolver.normalise_symbol(symbol)
        return [self._complexes[k] for k in self.ids() if self._complexes[k].metal_symbol == target]

    def to_ase_atoms(self, complex_id: str) -> Any:
        """Return one complex as an ``ase.Atoms`` object."""
        return self.get(complex_id).to_ase_atoms()


def load_authentic_complexes(h5_path: Optional[Path] = None) -> Dict[str, AuthenticComplexFixture]:
    """Load verified complexes with the default loader."""
    return PhysicalFixtureLoader().load_complexes(h5_path)


def load_authentic_water_hessian(npy_path: Optional[Path] = None) -> np.ndarray:
    """Load the verified water Hessian with the default loader."""
    return PhysicalFixtureLoader().load_water_hessian(npy_path)


# Names that used to live in this module (ASE EMT fallback service). They are
# forwarded to cochem.core.physical_fallback, the home assigned by Task 20.109.3.
_RELOCATED_FALLBACK_EXPORTS = frozenset(
    {
        "PhysicalFallbackService",
        "PhysicalSystemSpec",
        "PhysicalProvenance",
        "EnergyResult",
        "ForcesResult",
        "RelaxationResult",
        "UnsupportedElementError",
        "PhysicalConvergenceError",
        "numeric_force",
    }
)


def __getattr__(name: str) -> Any:
    """Forward relocated fallback-service names to cochem.core.physical_fallback."""
    if name in _RELOCATED_FALLBACK_EXPORTS:
        try:
            module = importlib.import_module("cochem.core.physical_fallback")
        except ImportError as exc:
            raise AttributeError(
                f"{name} moved out of physical_fixtures; cochem.core.physical_fallback "
                f"is not importable ({exc})"
            ) from exc
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
