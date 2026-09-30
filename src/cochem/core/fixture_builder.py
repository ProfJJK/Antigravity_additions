"""Builder for the authentic physical fixtures (Task 20.109.2, WBS 20.9.2.2).

Usage (from the workspace root, with ``src`` on PYTHONPATH)::

    python -m cochem.core.fixture_builder --source-complexes tests/fixtures/complexes.h5

What it does:

1. **Water Hessian.** Computed here with PySCF. The starting structure is
   ``ase.build.molecule('H2O')`` from the ASE g2 collection. The structure is
   relaxed with geomeTRIC at the chosen level of theory (default RHF/cc-pVTZ).
   The analytic Cartesian Hessian is then evaluated and converted to
   eV/Angstrom^2 with ``ase.units``. The raw tensor is stored and is not
   symmetrised or adjusted.
2. **Complex geometries.** Imported from an existing HDF5 container. They are
   not recomputed. The builder records the source path and its SHA-256, copies
   the source attributes verbatim, and records whether the source names an
   originating calculation. Geometry type and metal-donor distances are
   measured from the coordinates.
3. **Manifest.** ``authentic_fixtures_manifest.json`` records artifact
   SHA-256 digests, calculation provenance, and measured properties (Frobenius
   norm, eigenvalues, harmonic frequencies).

SRS reference figures are never built in. They can be passed with
``--srs-frobenius`` and ``--srs-vibrational``, and the builder then reports
agreement or disagreement without changing any data. If validation fails,
nothing is written.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import platform as host_platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import h5py
import numpy as np

from .physical_fixtures import (
    COMPLEXES_FILENAME,
    MANIFEST_FILENAME,
    MODULE_DIR,
    SCHEMA_ID,
    WATER_HESSIAN_FILENAME,
    DynamicMendeleevResolver,
    HessianFixtureValidator,
    MendeleevMassService,
    PhysicalFixtureError,
    PhysicalFixtureLoader,
    canonical_geometry_digest,
    hill_formula,
    metal_symbol_of,
    sha256_file,
)

DONOR_CUTOFF_FACTOR = 1.3
TRANS_ANGLE_DEG = 165.0
CIS_LOW_DEG = 80.0
CIS_HIGH_DEG = 100.0
PLANARITY_TOLERANCE_A = 0.1
SCF_CONV_TOL = 1e-12
DFT_GRID_LEVEL = 6

COORD_KEYS = ("coordinates_angstrom", "coordinates", "positions", "coords", "xyz", "geometry")
Z_KEYS = ("atomic_numbers", "numbers", "atomic_number", "z")
SYMBOL_KEYS = ("symbols", "elements", "species", "atoms")
CHARGE_KEYS = ("formal_charge", "charge", "total_charge")
MULTIPLICITY_KEYS = ("spin_multiplicity", "multiplicity", "mult")
SPIN_KEYS = ("spin_s", "total_spin", "spin_quantum_number")
UNIT_KEYS = ("units", "unit", "coordinate_units", "length_unit")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Convert h5py/numpy values into JSON-serialisable Python values."""
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8", "replace").strip()
    if isinstance(value, np.ndarray):
        if value.shape == ():
            return _json_safe(value[()])
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    return value


# --------------------------------------------------------------------------
# Water Hessian (PySCF)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class WaterHessianResult:
    hessian_ev_per_a2: np.ndarray
    provenance: Dict[str, Any]


def _mean_field(mol: Any, method: str, xc: Optional[str]) -> Any:
    from pyscf import dft, scf

    label = method.upper()
    if label == "RHF":
        mf = scf.RHF(mol)
    elif label == "RKS":
        if not xc:
            raise ValueError("--xc is required for RKS")
        mf = dft.RKS(mol)
        mf.xc = xc
        mf.grids.level = DFT_GRID_LEVEL
    else:
        raise ValueError(f"unsupported method {method!r}; use RHF or RKS")
    mf.conv_tol = SCF_CONV_TOL
    mf.max_cycle = 200
    return mf


def _hessian_object(mf: Any, method: str) -> Any:
    if method.upper() == "RHF":
        from pyscf.hessian import rhf as hessian_module
    else:
        from pyscf.hessian import rks as hessian_module
    return hessian_module.Hessian(mf)


def compute_water_hessian(method: str, basis: str, xc: Optional[str]) -> WaterHessianResult:
    """Relax water and evaluate its analytic Cartesian Hessian with PySCF."""
    import pyscf
    from ase import units
    from ase.build import molecule
    from pyscf import gto
    from pyscf.hessian import thermo

    try:
        import geometric
    except ImportError as exc:
        raise PhysicalFixtureError(
            "geomeTRIC is required for the water relaxation (pip install geometric)"
        ) from exc
    from pyscf.geomopt.geometric_solver import optimize as geometric_optimize

    start = molecule("H2O")
    atom_spec = [
        (symbol, tuple(float(c) for c in position))
        for symbol, position in zip(start.get_chemical_symbols(), start.get_positions())
    ]
    mol = gto.M(atom=atom_spec, basis=basis, unit="Angstrom", charge=0, spin=0, verbose=0)
    mf = _mean_field(mol, method, xc)
    mf.kernel()
    if not mf.converged:
        raise PhysicalFixtureError("SCF on the starting water structure did not converge")

    convergence = dict(
        convergence_energy=1e-9,
        convergence_grms=1e-6,
        convergence_gmax=1e-6,
        convergence_drms=1e-5,
        convergence_dmax=1e-5,
    )
    mol_eq = geometric_optimize(mf, maxsteps=200, **convergence)

    mf_eq = _mean_field(mol_eq, method, xc)
    energy = float(mf_eq.kernel())
    if not mf_eq.converged:
        raise PhysicalFixtureError("SCF at the relaxed water geometry did not converge")
    gradient = np.asarray(mf_eq.nuc_grad_method().kernel(), dtype=np.float64)
    hessian_4d = np.asarray(_hessian_object(mf_eq, method).kernel(), dtype=np.float64)
    n_atoms = mol_eq.natm
    hessian_au = hessian_4d.transpose(0, 2, 1, 3).reshape(3 * n_atoms, 3 * n_atoms)
    conversion = units.Hartree / units.Bohr ** 2
    hessian = np.ascontiguousarray(hessian_au * conversion, dtype=np.float64)

    analysis = thermo.harmonic_analysis(mol_eq, hessian_4d)
    frequencies = np.asarray(analysis["freq_wavenumber"])
    imaginary = int(np.sum(np.abs(np.imag(frequencies)) > 0.0)) if np.iscomplexobj(frequencies) else 0

    coords = np.asarray(mol_eq.atom_coords(unit="Angstrom"), dtype=np.float64)
    numbers = [int(round(float(q))) for q in mol_eq.atom_charges()]
    symbols = [DynamicMendeleevResolver.symbol(z) for z in numbers]
    o_idx = numbers.index(8)
    h_idx = [i for i, z in enumerate(numbers) if z == 1]
    oh = [float(np.linalg.norm(coords[i] - coords[o_idx])) for i in h_idx]
    v1, v2 = coords[h_idx[0]] - coords[o_idx], coords[h_idx[1]] - coords[o_idx]
    hoh = float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (oh[0] * oh[1]), -1.0, 1.0))))

    method_label = method.upper() if method.upper() == "RHF" else f"RKS/{xc}"
    provenance = {
        "program": f"PySCF {pyscf.__version__}",
        "method": method_label,
        "basis_set": basis,
        "geometry_source": (
            "ase.build.molecule('H2O') starting structure (ASE g2 collection), "
            f"relaxed in this build at {method_label}/{basis}"
        ),
        "optimizer": f"geomeTRIC {geometric.__version__}",
        "optimizer_convergence": convergence,
        "hessian_type": "analytic second derivatives (pyscf.hessian), raw tensor, not symmetrised",
        "units": "eV/Angstrom^2 (converted from Hartree/Bohr^2 with ase.units)",
        "atom_order": symbols,
        "atomic_numbers": numbers,
        "equilibrium_geometry_angstrom": coords.tolist(),
        "oh_bond_lengths_angstrom": oh,
        "hoh_angle_degrees": hoh,
        "scf_energy_hartree": energy,
        "residual_gradient_max_hartree_per_bohr": float(np.max(np.abs(gradient))),
        "harmonic_frequencies_cm-1": [float(np.real(f)) for f in np.ravel(frequencies)],
        "imaginary_frequency_count": imaginary,
        "scf_conv_tol": SCF_CONV_TOL,
    }
    return WaterHessianResult(hessian_ev_per_a2=hessian, provenance=provenance)


# --------------------------------------------------------------------------
# Complex import
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceComplex:
    source_object: str
    atomic_numbers: Tuple[int, ...]
    coordinates: Tuple[Tuple[float, float, float], ...]
    formal_charge: int
    spin_multiplicity: int
    coordinate_units: str
    imported_attributes: Dict[str, Any]


def _lower_attrs(*objects: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for obj in objects:
        for key, value in obj.attrs.items():
            merged.setdefault(str(key).lower(), value)
    return merged


def _first(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _parse_group(name: str, group: h5py.Group) -> Optional[SourceComplex]:
    datasets = {str(k).lower(): group[k] for k in group.keys() if isinstance(group[k], h5py.Dataset)}
    candidates = {
        k: d for k, d in datasets.items()
        if d.ndim == 2 and d.shape[1] == 3 and d.dtype.kind == "f"
    }
    if not candidates:
        return None
    preferred = [k for k in COORD_KEYS if k in candidates]
    if preferred:
        coord_key = preferred[0]
    elif len(candidates) == 1:
        coord_key = next(iter(candidates))
    else:
        raise PhysicalFixtureError(f"{name}: ambiguous coordinate datasets {sorted(candidates)}")
    coord_ds = candidates[coord_key]
    coords = np.asarray(coord_ds[()], dtype=np.float64)
    attrs = _lower_attrs(group, coord_ds)

    numbers_value = None
    for key in Z_KEYS:
        if key in datasets and datasets[key].ndim == 1:
            numbers_value = datasets[key][()]
            break
    if numbers_value is None:
        numbers_value = _first(attrs, Z_KEYS)
    if numbers_value is not None:
        numbers = [int(z) for z in np.ravel(np.asarray(numbers_value))]
    else:
        symbols_value = None
        for key in SYMBOL_KEYS:
            if key in datasets:
                symbols_value = datasets[key][()]
                break
        if symbols_value is None:
            symbols_value = _first(attrs, SYMBOL_KEYS)
        if symbols_value is None:
            raise PhysicalFixtureError(f"{name}: no atomic numbers or symbols in source group")
        numbers = [DynamicMendeleevResolver.atomic_number(str(s)) for s in _json_safe(symbols_value)]
    if len(numbers) != coords.shape[0]:
        raise PhysicalFixtureError(f"{name}: {len(numbers)} atoms but {coords.shape[0]} coordinate rows")

    charge_value = _first(attrs, CHARGE_KEYS)
    if charge_value is None:
        raise PhysicalFixtureError(f"{name}: source records no charge (attributes: {sorted(attrs)})")
    charge = int(_json_safe(charge_value))
    mult_value = _first(attrs, MULTIPLICITY_KEYS)
    if mult_value is not None:
        multiplicity = int(_json_safe(mult_value))
    else:
        spin_value = _first(attrs, SPIN_KEYS)
        if spin_value is None:
            raise PhysicalFixtureError(f"{name}: source records no spin multiplicity")
        multiplicity = int(round(2.0 * float(_json_safe(spin_value)) + 1.0))

    unit_value = _first(attrs, UNIT_KEYS)
    if unit_value is None:
        units_label = "angstrom (assumed; source carries no unit attribute)"
    else:
        unit_text = str(_json_safe(unit_value)).lower()
        if "bohr" in unit_text:
            from ase import units

            coords = coords * units.Bohr
            units_label = f"converted from source unit '{unit_text}' with ase.units.Bohr"
        elif "ang" in unit_text or "\u00e5" in unit_text:
            units_label = f"angstrom (source unit '{unit_text}')"
        else:
            raise PhysicalFixtureError(f"{name}: unsupported coordinate unit {unit_text!r}")

    if not np.all(np.isfinite(coords)):
        raise PhysicalFixtureError(f"{name}: non-finite coordinates")
    imported = {k: _json_safe(v) for k, v in sorted(attrs.items())}
    return SourceComplex(
        source_object=name,
        atomic_numbers=tuple(numbers),
        coordinates=tuple(tuple(float(c) for c in row) for row in coords),
        formal_charge=charge,
        spin_multiplicity=multiplicity,
        coordinate_units=units_label,
        imported_attributes=imported,
    )


def read_source_complexes(path: Path) -> List[SourceComplex]:
    """Read every complex geometry from a source HDF5 container, deduplicated by digest."""
    if not h5py.is_hdf5(str(path)):
        raise PhysicalFixtureError(f"{path} is not an HDF5 container")
    found: Dict[str, SourceComplex] = {}
    with h5py.File(path, "r") as handle:
        group_names: List[str] = []

        def _collect(name: str, obj: Any) -> None:
            if isinstance(obj, h5py.Group):
                group_names.append(name)

        handle.visititems(_collect)
        for name in group_names:
            entry = _parse_group(name, handle[name])
            if entry is None:
                continue
            digest = canonical_geometry_digest(
                entry.atomic_numbers, entry.coordinates, entry.formal_charge, entry.spin_multiplicity
            )
            found.setdefault(digest, entry)
    if not found:
        raise PhysicalFixtureError(f"no complex geometries found in {path}")
    return list(found.values())


@lru_cache(maxsize=128)
def _covalent_radius_angstrom(atomic_number: int) -> float:
    radius_pm = DynamicMendeleevResolver.element(atomic_number).covalent_radius_pyykko
    if radius_pm is None:
        raise PhysicalFixtureError(f"mendeleev has no covalent radius for Z={atomic_number}")
    return float(radius_pm) / 100.0


def _angle_deg(a: np.ndarray, centre: np.ndarray, b: np.ndarray) -> float:
    v1, v2 = a - centre, b - centre
    ratio = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
    return float(np.degrees(np.arccos(np.clip(ratio, -1.0, 1.0))))


def coordination_analysis(atomic_numbers: Sequence[int], coordinates: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Measure the first coordination shell and classify its geometry."""
    coords = np.asarray(coordinates, dtype=np.float64)
    metal_z = DynamicMendeleevResolver.atomic_number(metal_symbol_of(atomic_numbers))
    metal_indices = [i for i, z in enumerate(atomic_numbers) if int(z) == metal_z]
    if len(metal_indices) != 1:
        raise PhysicalFixtureError("coordination analysis needs exactly one metal centre")
    m = metal_indices[0]
    r_metal = _covalent_radius_angstrom(metal_z)
    shell = []
    for i, z in enumerate(atomic_numbers):
        if i == m:
            continue
        distance = float(np.linalg.norm(coords[i] - coords[m]))
        if distance <= DONOR_CUTOFF_FACTOR * (r_metal + _covalent_radius_angstrom(int(z))):
            shell.append((i, distance))
    shell.sort(key=lambda item: item[1])
    donors = [i for i, _ in shell]
    angles = [_angle_deg(coords[a], coords[m], coords[b]) for a, b in itertools.combinations(donors, 2)]
    trans = sum(1 for a in angles if a > TRANS_ANGLE_DEG)
    cis = sum(1 for a in angles if CIS_LOW_DEG <= a <= CIS_HIGH_DEG)
    cn = len(donors)
    if cn == 6 and trans == 3 and cis == 12:
        label = "octahedral"
    elif cn == 4 and trans == 2 and cis == 4:
        core = coords[[m] + donors]
        flatness = float(np.linalg.svd(core - core.mean(axis=0), compute_uv=False)[-1])
        label = "square planar" if flatness < PLANARITY_TOLERANCE_A else "4-coordinate (non-planar)"
    elif cn == 4 and all(95.0 <= a <= 125.0 for a in angles):
        label = "tetrahedral"
    else:
        label = f"{cn}-coordinate"
    return {
        "geometry_type": label,
        "coordination_number": cn,
        "donor_elements": [DynamicMendeleevResolver.symbol(int(atomic_numbers[i])) for i in donors],
        "metal_donor_distances_angstrom": [d for _, d in shell],
    }


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
def _write_complexes(target: Path, complexes: List[SourceComplex], source: Path, source_digest: str) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    temporary = target.with_name(target.name + ".tmp")
    method_keys = ("method", "theory", "functional", "level_of_theory", "basis", "basis_set", "program")
    with h5py.File(temporary, "w") as handle:
        handle.attrs["schema"] = SCHEMA_ID
        handle.attrs["created_utc"] = _utc_now()
        handle.attrs["geometry_source"] = (
            f"imported verbatim from {source.as_posix()}; geometries were not recomputed by this build"
        )
        handle.attrs["source_container_sha256"] = source_digest
        for entry in sorted(complexes, key=lambda c: hill_formula(c.atomic_numbers)):
            stoich = hill_formula(entry.atomic_numbers)
            digest = canonical_geometry_digest(
                entry.atomic_numbers, entry.coordinates, entry.formal_charge, entry.spin_multiplicity
            )
            electrons = sum(entry.atomic_numbers) - entry.formal_charge
            unpaired = entry.spin_multiplicity - 1
            if entry.spin_multiplicity < 1 or electrons < unpaired or (electrons - unpaired) % 2:
                raise PhysicalFixtureError(
                    f"{entry.source_object}: multiplicity {entry.spin_multiplicity} inconsistent "
                    f"with {electrons} electrons"
                )
            shell = coordination_analysis(entry.atomic_numbers, entry.coordinates)
            calculation_named = any(k in entry.imported_attributes for k in method_keys)
            complex_id = f"{stoich}_{digest[:12]}"
            group = handle.create_group(complex_id)
            group.create_dataset("atomic_numbers", data=np.asarray(entry.atomic_numbers, dtype=np.int32))
            group.create_dataset("coordinates_angstrom", data=np.asarray(entry.coordinates, dtype=np.float64))
            group.attrs["complex_id"] = complex_id
            group.attrs["stoichiometry"] = stoich
            group.attrs["metal_symbol"] = metal_symbol_of(entry.atomic_numbers)
            group.attrs["formal_charge"] = int(entry.formal_charge)
            group.attrs["spin_multiplicity"] = int(entry.spin_multiplicity)
            group.attrs["geometry_type"] = shell["geometry_type"]
            group.attrs["provenance_hash"] = digest
            group.attrs["geometry_source"] = f"{source.as_posix()}::{entry.source_object}"
            group.attrs["coordinate_units"] = entry.coordinate_units
            group.attrs["originating_calculation_recorded"] = bool(calculation_named)
            group.attrs["imported_attributes"] = json.dumps(entry.imported_attributes, sort_keys=True)
            group.attrs["coordination_shell"] = json.dumps(shell, sort_keys=True)
            summary[complex_id] = {
                "stoichiometry": stoich,
                "n_atoms": len(entry.atomic_numbers),
                "provenance_hash": digest,
                "formal_charge": entry.formal_charge,
                "spin_multiplicity": entry.spin_multiplicity,
                "molecular_mass_amu_mendeleev": MendeleevMassService.total_mass(entry.atomic_numbers),
                "coordination_shell": shell,
                "originating_calculation_recorded": bool(calculation_named),
            }
    os.replace(temporary, target)
    return summary


def _write_hessian(target: Path, hessian: np.ndarray) -> None:
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("wb") as handle:
        np.save(handle, hessian, allow_pickle=False)
    os.replace(temporary, target)


def _srs_comparison(report: Any, frobenius: Optional[float], vibrational: Optional[List[float]], rel_tol: float) -> Optional[Dict[str, Any]]:
    if frobenius is None and not vibrational:
        return None
    result: Dict[str, Any] = {
        "note": "SRS figures carry no unit, method or provenance; comparison is numeric only",
        "relative_tolerance": rel_tol,
    }
    agreements: List[bool] = []
    if frobenius is not None:
        deviation = abs(report.frobenius_norm - frobenius) / abs(frobenius)
        result["norm"] = {"srs": frobenius, "measured": report.frobenius_norm, "relative_deviation": deviation}
        agreements.append(deviation <= rel_tol)
    if vibrational:
        measured = sorted(report.vibrational_eigenvalues)
        pairs = list(zip(sorted(vibrational), measured))
        deviations = [abs(m - s) / abs(s) for s, m in pairs]
        result["vibrational_values"] = {"srs": sorted(vibrational), "measured": measured, "relative_deviations": deviations}
        agreements.append(len(vibrational) == len(measured) and all(d <= rel_tol for d in deviations))
    result["agreement"] = all(agreements)
    return result


def build(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = Path(args.output_dir).resolve()
    source = Path(args.source_complexes).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    water = compute_water_hessian(args.method, args.basis, args.xc)
    report = HessianFixtureValidator.validate(water.hessian_ev_per_a2, rigid_body_modes=6)
    complexes = read_source_complexes(source)

    h5_path = output_dir / COMPLEXES_FILENAME
    npy_path = output_dir / WATER_HESSIAN_FILENAME
    complex_summary = _write_complexes(h5_path, complexes, source, sha256_file(source))
    _write_hessian(npy_path, water.hessian_ev_per_a2)

    water_record = dict(water.provenance)
    water_record["measured"] = {
        "frobenius_norm": report.frobenius_norm,
        "max_asymmetry": report.max_asymmetry,
        "eigenvalues": list(report.eigenvalues),
        "vibrational_eigenvalues": list(report.vibrational_eigenvalues),
        "zero_mode_threshold": report.zero_mode_threshold,
        "rigid_body_modes": report.rigid_body_modes,
        "nonzero_off_diagonal_elements": report.nonzero_off_diagonal_elements,
    }
    comparison = _srs_comparison(report, args.srs_frobenius, args.srs_vibrational, args.srs_rel_tol)
    if comparison is not None:
        water_record["unverified_srs_figures"] = comparison

    manifest = {
        "schema": SCHEMA_ID,
        "generated_utc": _utc_now(),
        "generator": "cochem.core.fixture_builder",
        "host": {
            "python": host_platform.python_version(),
            "platform": host_platform.platform(),
            "numpy": np.__version__,
            "h5py": h5py.__version__,
        },
        "artifacts": {
            COMPLEXES_FILENAME: {"sha256": sha256_file(h5_path), "size_bytes": h5_path.stat().st_size},
            WATER_HESSIAN_FILENAME: {"sha256": sha256_file(npy_path), "size_bytes": npy_path.stat().st_size},
        },
        "water_hessian": water_record,
        "complexes": {
            "geometry_source": source.as_posix(),
            "source_container_sha256": sha256_file(source),
            "entries": complex_summary,
        },
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    temporary = manifest_path.with_name(manifest_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, manifest_path)

    loader = PhysicalFixtureLoader(search_roots=[output_dir])
    loader.load_complexes()
    loader.load_water_hessian()
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-complexes", required=True, help="HDF5 container holding the complex geometries")
    parser.add_argument("--output-dir", default=str(MODULE_DIR))
    parser.add_argument("--method", default="RHF", choices=("RHF", "RKS"))
    parser.add_argument("--xc", default=None, help="functional for RKS, e.g. B3LYP")
    parser.add_argument("--basis", default="cc-pVTZ")
    parser.add_argument("--srs-frobenius", type=float, default=None)
    parser.add_argument("--srs-vibrational", type=float, nargs="+", default=None)
    parser.add_argument("--srs-rel-tol", type=float, default=1e-2)
    args = parser.parse_args(argv)

    manifest = build(args)
    water = manifest["water_hessian"]
    print(json.dumps({"artifacts": manifest["artifacts"], "measured": water["measured"]}, indent=2))
    comparison = water.get("unverified_srs_figures")
    if comparison is not None and not comparison["agreement"]:
        print("DISAGREEMENT: measured Hessian properties differ from the SRS figures; "
              "data was not adjusted. See unverified_srs_figures in the manifest.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
