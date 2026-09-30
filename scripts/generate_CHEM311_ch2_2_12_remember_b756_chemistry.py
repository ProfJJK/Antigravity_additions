"""Generate the Stage 2 chemistry specification for CHEM311_ch2_2.12_remember_b756.2.

McMurry 10e Section 2.12 (Noncovalent Interactions between Molecules).

All elemental masses are obtained at run time from the mendeleev library via
``mendeleev.element(symbol).mass``. Molecular weights are computed as the
stoichiometric sum of those live masses, with element counts parsed from each
molecular formula. No atomic weight constants appear in this module.

The Chunk 1 pedagogy JSON is a required input. A missing file raises
FileNotFoundError. A pedagogy whose bloom_level is not 'remember' raises
ValueError. In both cases no output file is written.

Usage (from the workspace root):
    python scripts/generate_CHEM311_ch2_2_12_remember_b756_chemistry.py
    python scripts/generate_CHEM311_ch2_2_12_remember_b756_chemistry.py --output <path> --pedagogy <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Optional, Union

from mendeleev import element

TASK_ID = "CHEM311_ch2_2.12_remember_b756.2"
PARENT_TASK_ID = "CHEM311_ch2_2.12_remember_b756"
ARTIFACT_NAME = "CHEM311_ch2_2.12_remember_b756_chemistry.json"
PEDAGOGY_NAME = "CHEM311_ch2_2.12_remember_b756_pedagogy.json"
REQUIRED_BLOOM_LEVEL = "remember"

DEFAULT_KANBAN_DIR = Path("D:/Gdrive/__agentic/.sources/r_exams/kanban/tasks")
DEFAULT_OUTPUT_PATH = DEFAULT_KANBAN_DIR / ARTIFACT_NAME
DEFAULT_PEDAGOGY_PATH = DEFAULT_KANBAN_DIR / PEDAGOGY_NAME

MASS_TOLERANCE_AMU = 1e-4
PROVENANCE_ELEMENTS = ("C", "H", "O", "N", "F", "Cl")
HBOND_HETEROATOMS = ("O", "N", "F")

FORCE_HBOND = "hydrogen bonding"
FORCE_DIPOLE = "dipole-dipole"
FORCE_DISPERSION = "dispersion"

MCMURRY_REF = "McMurry, Organic Chemistry, 10th ed., Section 2.12 (Noncovalent Interactions between Molecules)"

# Structural and physical data for the five Section 2.12 systems.
# Element counts are derived from the formula at run time; masses come from mendeleev.
MOLECULE_SPECS = [
    {
        "name": "water",
        "common_name": "water",
        "iupac_name": "oxidane",
        "formula": "H2O",
        "condensed_formula": "H2O",
        "smiles": "O",
        "dominant_forces": [FORCE_HBOND, FORCE_DIPOLE, FORCE_DISPERSION],
        "force_descriptions": [
            "hydrogen bonding (O-H donor to O lone-pair acceptor)",
            "dipole-dipole forces",
            "London dispersion forces",
        ],
        "hydrogen_bonding": {
            "donor_count": 2,
            "acceptor_count": 2,
            "basis": "Two O-H hydrogens act as donors and two oxygen lone pairs act as acceptors, "
                     "producing an extended three-dimensional hydrogen-bond network.",
        },
        "polarity": {
            "classification": "polar",
            "dipole_moment_debye": 1.85,
            "basis": "Bent geometry leaves the two O-H bond dipoles unbalanced, giving a net permanent dipole.",
        },
        "boiling_point_celsius": 100.0,
        "mcmurry_citation": MCMURRY_REF + ": hydrogen bonding in water accounts for its unusually high boiling point.",
    },
    {
        "name": "acetone",
        "common_name": "acetone",
        "iupac_name": "propan-2-one",
        "formula": "C3H6O",
        "condensed_formula": "CH3COCH3",
        "smiles": "CC(=O)C",
        "dominant_forces": [FORCE_DIPOLE, FORCE_DISPERSION],
        "force_descriptions": [
            "dipole-dipole forces (C=O permanent dipole)",
            "London dispersion forces",
        ],
        "hydrogen_bonding": {
            "donor_count": 0,
            "acceptor_count": 1,
            "basis": "The carbonyl oxygen can accept a hydrogen bond from a donor solvent, but acetone has "
                     "no O-H or N-H hydrogen and cannot hydrogen bond to itself.",
        },
        "polarity": {
            "classification": "polar",
            "dipole_moment_debye": 2.88,
            "basis": "The strongly polarized C=O bond gives a large net permanent dipole.",
        },
        "boiling_point_celsius": 56.1,
        "mcmurry_citation": MCMURRY_REF + ": dipole-dipole attraction between carbonyl groups raises the boiling point.",
    },
    {
        "name": "ethanol",
        "common_name": "ethanol",
        "iupac_name": "ethanol",
        "formula": "C2H6O",
        "condensed_formula": "CH3CH2OH",
        "smiles": "CCO",
        "dominant_forces": [FORCE_HBOND, FORCE_DIPOLE, FORCE_DISPERSION],
        "force_descriptions": [
            "hydrogen bonding (O-H donor to O lone-pair acceptor)",
            "dipole-dipole forces",
            "London dispersion forces",
        ],
        "hydrogen_bonding": {
            "donor_count": 1,
            "acceptor_count": 1,
            "basis": "The hydroxyl hydrogen is a donor and the hydroxyl oxygen is an acceptor, so ethanol "
                     "self-associates through hydrogen bonds.",
        },
        "polarity": {
            "classification": "polar",
            "dipole_moment_debye": 1.69,
            "basis": "The C-O and O-H bond dipoles combine to a net permanent dipole.",
        },
        "boiling_point_celsius": 78.4,
        "mcmurry_citation": MCMURRY_REF + ": ethanol boils far higher than its isomer dimethyl ether because of hydrogen bonding.",
    },
    {
        "name": "hexane",
        "common_name": "hexane",
        "iupac_name": "hexane",
        "formula": "C6H14",
        "condensed_formula": "CH3(CH2)4CH3",
        "smiles": "CCCCCC",
        "dominant_forces": [FORCE_DISPERSION],
        "force_descriptions": [
            "London dispersion forces exclusively (temporary induced dipoles over a large surface area)",
        ],
        "hydrogen_bonding": {
            "donor_count": 0,
            "acceptor_count": 0,
            "basis": "Only C-H and C-C bonds are present; there is no O, N, or F atom.",
        },
        "polarity": {
            "classification": "nonpolar",
            "dipole_moment_debye": 0.0,
            "basis": "C-H and C-C bonds have negligible bond dipoles and no net permanent dipole.",
        },
        "boiling_point_celsius": 68.7,
        "mcmurry_citation": MCMURRY_REF + ": dispersion forces between long-chain alkanes make hexane a liquid at room temperature.",
    },
    {
        "name": "chloromethane",
        "common_name": "methyl chloride",
        "iupac_name": "chloromethane",
        "formula": "CH3Cl",
        "condensed_formula": "CH3Cl",
        "smiles": "CCl",
        "dominant_forces": [FORCE_DIPOLE, FORCE_DISPERSION],
        "force_descriptions": [
            "dipole-dipole forces (C-Cl permanent dipole)",
            "London dispersion forces",
        ],
        "hydrogen_bonding": {
            "donor_count": 0,
            "acceptor_count": 0,
            "basis": "Hydrogen is bonded only to carbon, and period-3 chlorine is too large and diffuse to "
                     "participate in classical hydrogen bonding.",
        },
        "polarity": {
            "classification": "polar",
            "dipole_moment_debye": 1.87,
            "basis": "The polar C-Cl bond gives a net permanent dipole.",
        },
        "boiling_point_celsius": -24.2,
        "mcmurry_citation": MCMURRY_REF + ": chloromethane is attracted to its neighbors by dipole-dipole forces, not hydrogen bonds.",
    },
]

ENERGY_BOUNDS_KJ_MOL = {
    "dipole_dipole": [5, 20],
    "dispersion": [2, 10],
    "hydrogen_bond": [15, 40],
    "covalent_min": 350,
}

NONCOVALENT_ENERGY_BOUNDS = {
    "energy_unit": "kJ/mol",
    "london_dispersion_forces": {
        "energy_min_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["dispersion"][0],
        "energy_max_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["dispersion"][1],
        "unit": "kJ/mol",
        "structural_criterion": "Ubiquitous: present between all molecules, polar and nonpolar, arising from "
                                "temporary fluctuating dipoles; strength grows with polarizability and surface area.",
    },
    "dipole_dipole_forces": {
        "energy_min_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["dipole_dipole"][0],
        "energy_max_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["dipole_dipole"][1],
        "unit": "kJ/mol",
        "structural_criterion": "Requires a net permanent molecular dipole; the positive end of one molecule "
                                "aligns with the negative end of a neighbor.",
    },
    "hydrogen_bonding": {
        "energy_min_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["hydrogen_bond"][0],
        "energy_max_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["hydrogen_bond"][1],
        "unit": "kJ/mol",
        "structural_criterion": "Strictly requires a hydrogen atom covalently bonded to oxygen, nitrogen, or "
                                "fluorine (O, N, F) interacting with a lone pair on another oxygen, nitrogen, or "
                                "fluorine atom. Period-3 atoms such as Cl and S are excluded.",
    },
    "intramolecular_covalent_bonds": {
        "energy_min_kj_per_mol": ENERGY_BOUNDS_KJ_MOL["covalent_min"],
        "unit": "kJ/mol",
        "representative_bond_dissociation_energies_kj_per_mol": {
            "ch3_ch3": 376,
            "ch3_h": 439,
            "ho_h": 497,
            "ch3_cl": 351,
        },
        "structural_criterion": "Covalent bonds hold atoms together within a molecule and are roughly 10 to 100 "
                                "times stronger than noncovalent forces. They remain intact during physical phase "
                                "changes such as melting, boiling, and dissolution; only noncovalent contacts "
                                "between molecules are overcome.",
    },
}

STEM = ("Which statement about the noncovalent interactions between molecules described in "
        "McMurry Section 2.12 is correct?")

CHOICES = [
    "Hydrogen bonding requires a hydrogen atom covalently bonded to O, N, or F, and all noncovalent "
    "forces are much weaker than covalent bonds.",
    "Boiling a liquid such as ethanol breaks its covalent C-C, C-H, and O-H bonds.",
    "London dispersion forces operate only between nonpolar molecules such as hexane.",
    "Chloromethane forms classical hydrogen bonds because its hydrogen atoms are near an "
    "electronegative chlorine atom.",
    "Intermolecular hydrogen bonds in water are stronger than the covalent O-H bonds within each "
    "water molecule.",
]

MISCONCEPTION_MAPPING = {
    "choice_1": {
        "role": "correct key",
        "rationale": "States the O/N/F requirement for hydrogen bonding and the noncovalent (2-40 kJ/mol) "
                     "versus covalent (>350 kJ/mol) energy hierarchy.",
    },
    "choice_2": {
        "misconception_id": "MIS-01",
        "label": "Intramolecular vs. Intermolecular Conflation Fallacy",
        "rationale": "Boiling overcomes noncovalent attractions between molecules; covalent bonds stay intact.",
    },
    "choice_3": {
        "misconception_id": "MIS-02",
        "label": "Dispersion Exclusivity Fallacy",
        "rationale": "Dispersion forces act between all molecules, including polar ones such as water and acetone.",
    },
    "choice_4": {
        "misconception_id": "MIS-03",
        "label": "Heteroatom Generality Fallacy",
        "rationale": "Classical hydrogen bonds require H bonded to O, N, or F; chloromethane has only C-H hydrogens.",
    },
    "choice_5": {
        "misconception_id": "MIS-04",
        "label": "Energy Scale Inversion Fallacy",
        "rationale": "Hydrogen bonds (15-40 kJ/mol) are an order of magnitude weaker than O-H covalent bonds.",
    },
}


def parse_formula(formula: str) -> Dict[str, int]:
    """Parse a molecular or condensed formula (nested parentheses allowed) into element counts."""
    cleaned = formula.replace(" ", "")
    tokens = re.findall(r"[A-Z][a-z]?|\d+|\(|\)", cleaned)
    if not tokens or "".join(tokens) != cleaned:
        raise ValueError(f"Unparseable formula: {formula!r}")
    stack: list[Dict[str, int]] = [{}]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(":
            stack.append({})
            i += 1
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError(f"Unbalanced parentheses in {formula!r}")
            group = stack.pop()
            i += 1
            mult = 1
            if i < len(tokens) and tokens[i].isdigit():
                mult = int(tokens[i])
                i += 1
            for sym, cnt in group.items():
                stack[-1][sym] = stack[-1].get(sym, 0) + cnt * mult
        elif tok.isdigit():
            raise ValueError(f"Unexpected digit in formula {formula!r}")
        else:
            i += 1
            cnt = 1
            if i < len(tokens) and tokens[i].isdigit():
                cnt = int(tokens[i])
                i += 1
            stack[-1][tok] = stack[-1].get(tok, 0) + cnt
    if len(stack) != 1:
        raise ValueError(f"Unbalanced parentheses in {formula!r}")
    return stack[0]


def load_pedagogy(pedagogy_path: Optional[Union[str, Path]] = None) -> tuple[Path, Dict[str, Any], str]:
    """Read and validate the Chunk 1 pedagogy JSON. Fails loudly on any problem."""
    path = Path(pedagogy_path) if pedagogy_path is not None else DEFAULT_PEDAGOGY_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Required Chunk 1 pedagogy JSON not found: {path}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Pedagogy file {path} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Pedagogy file {path} must contain a JSON object")
    bloom = data.get("bloom_level")
    if bloom != REQUIRED_BLOOM_LEVEL:
        raise ValueError(
            f"Pedagogy bloom_level must be {REQUIRED_BLOOM_LEVEL!r}, found {bloom!r} in {path}"
        )
    return path, data, digest


def query_masses() -> Dict[str, float]:
    """Query the atomic mass of each provenance element live from mendeleev."""
    masses: Dict[str, float] = {}
    for sym in PROVENANCE_ELEMENTS:
        mass = element(sym).mass
        if mass is None:
            raise ValueError(f"mendeleev returned no mass for {sym}")
        value = float(mass)
        if value <= 0:
            raise ValueError(f"mendeleev returned a non-positive mass for {sym}: {value}")
        masses[sym] = value
    return masses


def build_element_provenance() -> Dict[str, Dict[str, Any]]:
    """Detailed per-element provenance records, all sourced from mendeleev."""
    records: Dict[str, Dict[str, Any]] = {}
    for sym in PROVENANCE_ELEMENTS:
        el = element(sym)
        records[sym] = {
            "symbol": el.symbol,
            "element_name": el.name,
            "atomic_number": int(el.atomic_number),
            "atomic_mass": float(el.mass),
            "mass_unit": "amu (g/mol)",
            "pauling_electronegativity": getattr(el, "en_pauling", None),
            "query": f"mendeleev.element('{sym}').mass",
        }
    return records


def build_molecule(spec: Dict[str, Any], masses: Dict[str, float]) -> Dict[str, Any]:
    """Build one molecular record: composition, live molar mass, and force classification."""
    counts = parse_formula(spec["formula"])
    condensed_counts = parse_formula(spec["condensed_formula"])
    if counts != condensed_counts:
        raise ValueError(f"{spec['name']}: condensed formula disagrees with molecular formula")
    unknown = set(counts) - set(masses)
    if unknown:
        raise ValueError(f"{spec['name']}: elements {sorted(unknown)} lack mendeleev provenance")

    composition = {sym: int(counts.get(sym, 0)) for sym in PROVENANCE_ELEMENTS}

    contributions = []
    total = 0.0
    for sym in PROVENANCE_ELEMENTS:
        cnt = composition[sym]
        if cnt == 0:
            continue
        subtotal = cnt * masses[sym]
        total += subtotal
        contributions.append({"symbol": sym, "count": cnt, "atomic_mass": masses[sym], "subtotal": subtotal})

    hb = spec["hydrogen_bonding"]
    donor = hb["donor_count"] > 0
    acceptor = hb["acceptor_count"] > 0
    forces = list(spec["dominant_forces"])

    # Physical consistency checks on the classification.
    has_hetero = any(composition[sym] > 0 for sym in HBOND_HETEROATOMS)
    if (donor or acceptor) and not has_hetero:
        raise ValueError(f"{spec['name']}: H-bond role claimed without an O, N, or F atom")
    if donor and composition["H"] == 0:
        raise ValueError(f"{spec['name']}: H-bond donor claimed without hydrogen")
    if (FORCE_HBOND in forces) != (donor and acceptor):
        raise ValueError(f"{spec['name']}: hydrogen bonding listed inconsistently with donor/acceptor flags")
    if FORCE_DISPERSION not in forces:
        raise ValueError(f"{spec['name']}: dispersion forces act between all molecules")
    is_polar = spec["polarity"]["classification"] == "polar"
    if (FORCE_DIPOLE in forces) != is_polar:
        raise ValueError(f"{spec['name']}: dipole-dipole listing inconsistent with polarity")

    return {
        "name": spec["name"],
        "common_name": spec["common_name"],
        "iupac_name": spec["iupac_name"],
        "formula": spec["formula"],
        "condensed_formula": spec["condensed_formula"],
        "smiles": spec["smiles"],
        "composition": composition,
        "molar_mass": float(total),
        "molar_mass_unit": "g/mol",
        "provenance": "mendeleev",
        "stoichiometric_mass_contributions": contributions,
        "dominant_forces": forces,
        "force_descriptions": list(spec["force_descriptions"]),
        "hbond_donor": donor,
        "hbond_acceptor": acceptor,
        "hydrogen_bonding": dict(hb),
        "polarity": dict(spec["polarity"]),
        "boiling_point_celsius": spec["boiling_point_celsius"],
        "mcmurry_citation": spec["mcmurry_citation"],
    }


def validate(spec: Dict[str, Any]) -> None:
    """Re-verify the spec against independent live mendeleev queries and physical invariants."""
    masses = spec["masses"]
    if set(masses) != set(PROVENANCE_ELEMENTS):
        raise ValueError("masses must cover exactly C, H, O, N, F, Cl")
    for sym, value in masses.items():
        if abs(value - float(element(sym).mass)) >= MASS_TOLERANCE_AMU:
            raise ValueError(f"{sym}: recorded mass drifted from live mendeleev value")

    molecules = spec["molecules"]
    if len(molecules) != 5 or len({m["name"] for m in molecules}) != 5:
        raise ValueError("Exactly five distinct molecular systems are required")
    for mol in molecules:
        live = sum(cnt * float(element(sym).mass) for sym, cnt in mol["composition"].items() if cnt)
        if abs(mol["molar_mass"] - live) >= MASS_TOLERANCE_AMU:
            raise ValueError(f"{mol['name']}: molar mass inconsistent with live masses")

    bounds = spec["energy_bounds_kj_mol"]
    noncovalent_max = 0
    for key in ("dipole_dipole", "dispersion", "hydrogen_bond"):
        lo, hi = bounds[key]
        if not lo < hi:
            raise ValueError(f"{key}: lower energy bound must be below upper bound")
        noncovalent_max = max(noncovalent_max, hi)
    if not noncovalent_max < bounds["covalent_min"]:
        raise ValueError("Noncovalent energies must lie below the covalent minimum")

    if "Cl" in spec["electronegative_hbond_heteroatoms"]:
        raise ValueError("Cl must not be listed as a hydrogen-bonding heteroatom")

    choices = spec["choices"]
    if len(choices) != 5 or len({c.strip().lower() for c in choices}) != 5:
        raise ValueError("Exactly five distinct choices are required")
    sol = spec["exsolution"]
    if sol.count("1") != 1 or sol[spec["correct_index"]] != "1":
        raise ValueError("exsolution inconsistent with correct_index")


def build_chemistry_spec(pedagogy_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load pedagogy, query live mendeleev masses, and construct the verified chemistry specification."""
    ped_path, pedagogy, digest = load_pedagogy(pedagogy_path)

    masses = query_masses()
    molecules = [build_molecule(s, masses) for s in MOLECULE_SPECS]

    try:
        mendeleev_version = metadata.version("mendeleev")
    except metadata.PackageNotFoundError:
        mendeleev_version = "unknown"

    spec: Dict[str, Any] = {
        "task_id": TASK_ID,
        "parent_task_id": PARENT_TASK_ID,
        "course_id": "CHEM311",
        "chapter": 2,
        "section": "2.12",
        "textbook": "McMurry 10e",
        "textbook_full": "McMurry, Organic Chemistry, 10th edition",
        "bloom_level": pedagogy["bloom_level"],
        "question_type": "schoice",
        "masses": masses,
        "masses_unit": "g/mol",
        "molecules": molecules,
        "energy_bounds_kj_mol": {k: list(v) if isinstance(v, list) else v for k, v in ENERGY_BOUNDS_KJ_MOL.items()},
        "electronegative_hbond_heteroatoms": list(HBOND_HETEROATOMS),
        "mendeleev_provenance": {
            "library": "mendeleev",
            "library_version": mendeleev_version,
            "api": "mendeleev.element(symbol).mass",
            "tolerance_amu": MASS_TOLERANCE_AMU,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "elements": build_element_provenance(),
        },
        "noncovalent_energy_bounds": NONCOVALENT_ENERGY_BOUNDS,
        "stem": STEM,
        "choices": list(CHOICES),
        "correct_index": 0,
        "exsolution": "10000",
        "exshuffle": 5,
        "misconception_mapping": MISCONCEPTION_MAPPING,
        "pedagogy_source": {"path": str(ped_path.resolve()), "sha256": digest},
    }
    validate(spec)
    return spec


def main(
    output_path: Optional[Union[str, Path]] = None,
    pedagogy_path: Optional[Union[str, Path]] = None,
) -> int:
    """Build the spec and serialize it as UTF-8 JSON to output_path (defaults to DEFAULT_OUTPUT_PATH)."""
    out_path = Path(output_path) if output_path is not None else DEFAULT_OUTPUT_PATH
    spec = build_chemistry_spec(pedagogy_path=pedagogy_path)
    text = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {out_path}")
    return 0


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--pedagogy", type=Path, default=DEFAULT_PEDAGOGY_PATH)
    args = parser.parse_args(argv)
    return main(output_path=args.output, pedagogy_path=args.pedagogy)


if __name__ == "__main__":
    sys.exit(_cli())
