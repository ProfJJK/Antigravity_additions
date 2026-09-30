"""``cochem conformer`` -- dual-track (ORCA GOAT + CREST) conformer union and deduplication.

The subcommand ingests multi-frame XYZ conformer ensembles (the native output format
of both ORCA GOAT ``*.finalensemble.xyz`` and CREST ``crest_conformers.xyz``), and
reduces the candidate pool to unique conformational minima with a two-stage filter:

1. **Rotational constants** (full molecule, mendeleev masses): two candidates can only
   be duplicates if, for each of A, B and C, ``|dB|/B < rel_b_thresh`` (default 0.005)
   or ``|dB| <= b_thresh`` (default 5.0 MHz).
2. **Heavy-atom RMSD** after optimal superposition with Horn's quaternion form of the
   Kabsch problem: duplicates must satisfy ``RMSD <= rmsd_thresh`` (default 0.05 A).
   Hydrogen atoms are masked out so that rotations of terminal X-H groups do not cause
   false "unique" verdicts.

The union duplication ratio ``R_union = N_shared / N_total`` is reported together with
per-conformer metrics, and deduplicated geometries are serialised to ``--out``.

This module also hosts the small set of shared CLI utilities (error hierarchy,
mendeleev element lookup, XYZ parsing, JSON sanitising) reused by ``cochem pes``.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("cochem.cli.conformer")

# ---------------------------------------------------------------------------
# CODATA 2022 physical constants
# ---------------------------------------------------------------------------
H_PLANCK = 6.62607015e-34  # J s (exact)
C_LIGHT_CM_S = 2.99792458e10  # cm / s (exact)
ATOMIC_MASS_CONSTANT_KG = 1.66053906892e-27  # kg
ANGSTROM_M = 1.0e-10  # m
#: B [MHz] = C_ROT_MHZ / I [u A^2]
C_ROT_MHZ = H_PLANCK / (8.0 * math.pi ** 2 * ATOMIC_MASS_CONSTANT_KG * ANGSTROM_M ** 2) / 1.0e6
#: B [cm^-1] = C_ROT_CM1 / I [u A^2]
C_ROT_CM1 = H_PLANCK / (
    8.0 * math.pi ** 2 * ATOMIC_MASS_CONSTANT_KG * ANGSTROM_M ** 2 * C_LIGHT_CM_S
)
#: 1 Hartree in kcal/mol (CODATA 2022 Hartree energy / thermochemical calorie)
HARTREE_TO_KCAL_MOL = 627.5094740631
#: 1 eV in kcal/mol (CODATA 2022)
EV_TO_KCAL_MOL = 23.060547830619026
#: 1 kJ/mol in kcal/mol (thermochemical calorie)
KJ_TO_KCAL_MOL = 1.0 / 4.184

DEFAULT_MODE = "union"
DEFAULT_ENERGY_WINDOW = 3.0
DEFAULT_RMSD_THRESH = 0.05
DEFAULT_B_THRESH = 5.0
DEFAULT_REL_B_THRESH = 0.005
CONFORMER_MODES = ("orca_goat", "crest", "union")

#: Energy unit of the XYZ comment-line energies -> kcal/mol conversion factor.
ENERGY_UNIT_TO_KCAL_MOL: Dict[str, float] = {
    "hartree": HARTREE_TO_KCAL_MOL,
    "kcal": 1.0,
    "kj": KJ_TO_KCAL_MOL,
    "ev": EV_TO_KCAL_MOL,
}
DEFAULT_ENERGY_UNIT = "hartree"

_PRIMARY_TRACK_LABEL = {"orca_goat": "orca_goat", "crest": "crest", "union": "primary"}


# ---------------------------------------------------------------------------
# Error hierarchy (exit code contract: 1 runtime failure, 2 usage failure)
# ---------------------------------------------------------------------------
class CLIExecutionError(RuntimeError):
    """Runtime failure of a CLI subcommand; mapped to exit code 1."""

    exit_code = 1


class CLIUsageError(CLIExecutionError):
    """Semantically invalid argument combination detected after parsing; exit code 2."""

    exit_code = 2


class InputFileError(CLIExecutionError):
    """Input file missing or unreadable."""


class XYZFormatError(CLIExecutionError):
    """Input file is not a valid (multi-frame) XYZ file."""


# ---------------------------------------------------------------------------
# Element data via mendeleev (no hard-coded masses)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ElementData:
    symbol: str
    atomic_number: int
    mass: float  # standard atomic weight, u
    covalent_radius: Optional[float]  # Angstrom (Pyykko single-bond radius when available)
    group: Optional[int]
    period: Optional[int]


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:  # mendeleev computed properties may raise for exotic elements
        return None


@functools.lru_cache(maxsize=None)
def element_data(identifier: str) -> ElementData:
    """Look up an element (symbol or atomic number string) in the mendeleev database."""
    try:
        from mendeleev import element
    except ImportError as exc:
        raise CLIExecutionError(
            "the 'mendeleev' package is required for atomic masses but is not installed"
        ) from exc

    key = str(identifier).strip()
    if not key:
        raise XYZFormatError("empty element symbol")
    lookup: Any = int(key) if key.isdigit() else key[:1].upper() + key[1:].lower()
    try:
        el = element(lookup)
    except Exception as exc:
        raise XYZFormatError("unknown chemical element %r" % identifier) from exc

    mass = _safe_attr(el, "mass")
    try:
        mass_f = float(mass)
    except (TypeError, ValueError):
        raise CLIExecutionError("mendeleev provides no atomic mass for %r" % identifier) from None
    if not math.isfinite(mass_f) or mass_f <= 0.0:
        raise CLIExecutionError("invalid mendeleev atomic mass for %r" % identifier)

    radius: Optional[float] = None
    for attr in ("covalent_radius_pyykko", "covalent_radius_cordero", "covalent_radius"):
        value = _safe_attr(el, attr)
        if value is None:
            continue
        try:
            radius = float(value) / 100.0  # pm -> Angstrom
        except (TypeError, ValueError):
            continue
        if math.isfinite(radius) and radius > 0.0:
            break
        radius = None

    group = _safe_attr(el, "group_id")
    period = _safe_attr(el, "period")
    return ElementData(
        symbol=str(el.symbol),
        atomic_number=int(el.atomic_number),
        mass=mass_f,
        covalent_radius=radius,
        group=int(group) if group is not None else None,
        period=int(period) if period is not None else None,
    )


def masses_for(symbols: Sequence[str]) -> np.ndarray:
    """Atomic masses (u) for a symbol sequence, retrieved from mendeleev."""
    return np.array([element_data(s).mass for s in symbols], dtype=np.float64)


def heavy_atom_mask(symbols: Sequence[str]) -> np.ndarray:
    """Boolean mask of atoms with Z > 1."""
    return np.array([element_data(s).atomic_number > 1 for s in symbols], dtype=bool)


# ---------------------------------------------------------------------------
# XYZ parsing
# ---------------------------------------------------------------------------
_SYMBOL_RE = re.compile(r"^([A-Za-z]{1,3})(?:[_\-]?\d+)?$")
_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass
class XYZFrame:
    symbols: List[str]
    coordinates: np.ndarray  # (N, 3) Angstrom
    comment: str
    frame_index: int


def _normalise_symbol(token: str, path: Path, line_no: int) -> str:
    tok = token.strip()
    if tok.isdigit():
        name = tok
    else:
        match = _SYMBOL_RE.match(tok)
        if match is None:
            raise XYZFormatError(
                "%s:%d: invalid element symbol %r" % (path, line_no, token)
            )
        name = match.group(1)
    try:
        return element_data(name).symbol
    except XYZFormatError:
        raise XYZFormatError(
            "%s:%d: unknown chemical element %r" % (path, line_no, token)
        ) from None


def read_xyz_frames(path: Any) -> List[XYZFrame]:
    """Parse a (multi-frame) XYZ file with strict validation."""
    p = Path(path)
    if not p.exists():
        raise InputFileError("input file not found: %s" % p)
    if not p.is_file():
        raise InputFileError("input path is not a regular file: %s" % p)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise XYZFormatError("%s is not a UTF-8 text XYZ file (%s)" % (p, exc)) from None
    except OSError as exc:
        raise InputFileError("cannot read %s: %s" % (p, exc)) from None

    lines = text.splitlines()
    frames: List[XYZFrame] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        head = lines[i].split()[0]
        try:
            n_atoms = int(head)
        except ValueError:
            raise XYZFormatError(
                "%s:%d: malformed XYZ file, expected an integer atom count but found %r"
                % (p, i + 1, lines[i].strip())
            ) from None
        if n_atoms <= 0:
            raise XYZFormatError("%s:%d: atom count must be positive, got %d" % (p, i + 1, n_atoms))
        if i + 2 + n_atoms > len(lines):
            raise XYZFormatError(
                "%s:%d: truncated XYZ frame, expected %d atom lines" % (p, i + 1, n_atoms)
            )
        comment = lines[i + 1]
        symbols: List[str] = []
        coords = np.empty((n_atoms, 3), dtype=np.float64)
        for a in range(n_atoms):
            line_no = i + 3 + a
            tokens = lines[i + 2 + a].split()
            if len(tokens) < 4:
                raise XYZFormatError(
                    "%s:%d: malformed atom line %r (need symbol x y z)"
                    % (p, line_no, lines[i + 2 + a].strip())
                )
            symbols.append(_normalise_symbol(tokens[0], p, line_no))
            try:
                xyz = [float(v) for v in tokens[1:4]]
            except ValueError:
                raise XYZFormatError(
                    "%s:%d: non-numeric Cartesian coordinate in %r"
                    % (p, line_no, lines[i + 2 + a].strip())
                ) from None
            if not all(math.isfinite(v) for v in xyz):
                raise XYZFormatError("%s:%d: non-finite coordinate" % (p, line_no))
            coords[a] = xyz
        frames.append(XYZFrame(symbols, coords, comment, len(frames)))
        i += n_atoms + 2

    if not frames:
        raise XYZFormatError("%s contains no XYZ frames" % p)
    return frames


def parse_comment_energy(comment: str) -> Optional[float]:
    """Return the first decimal number in an XYZ comment line (CREST/GOAT energy convention)."""
    match = _FLOAT_RE.search(comment or "")
    if match is None:
        return None
    value = float(match.group(0))
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def json_safe(obj: Any) -> Any:
    """Recursively convert numpy types / tuples to JSON-native values; non-finite -> None."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, np.ndarray):
        return json_safe(obj.tolist())
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, Path):
        return str(obj)
    return obj


def write_text_file(path: Any, text: str) -> Path:
    """Write UTF-8 text, creating parent directories; OSError -> CLIExecutionError."""
    p = Path(path)
    try:
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise CLIExecutionError("cannot write output file %s: %s" % (p, exc)) from None
    return p


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class ConformerRecord(BaseModel):
    conformer_id: Optional[str] = None
    symbols: List[str]
    coordinates: List[Tuple[float, float, float]]  # (N, 3) Angstrom
    energy_kcal_mol: Optional[float] = None
    rotational_constants_mhz: Optional[Tuple[float, float, float]] = None
    source_track: Optional[str] = None
    relative_energy_kcal_mol: Optional[float] = None
    duplicate_ids: List[str] = Field(default_factory=list)


class ConformerDeduplicationResult(BaseModel):
    total_candidates: int
    unique_conformers: int
    shared_duplicates: int
    duplication_ratio: float  # R_union = N_shared / N_total
    conformers: List[ConformerRecord]
    energy_window_rejected: int = 0
    rmsd_thresh: float = DEFAULT_RMSD_THRESH
    b_thresh: float = DEFAULT_B_THRESH
    rel_b_thresh: float = DEFAULT_REL_B_THRESH
    energy_window: float = DEFAULT_ENERGY_WINDOW


# ---------------------------------------------------------------------------
# Physics: rotational constants and quaternion Kabsch RMSD
# ---------------------------------------------------------------------------
def _as_coords(coords: Any, n_expected: Optional[int] = None) -> np.ndarray:
    xyz = np.asarray(coords, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError("coordinates must have shape (N, 3), got %r" % (xyz.shape,))
    if n_expected is not None and xyz.shape[0] != n_expected:
        raise ValueError(
            "coordinate count %d does not match %d atoms" % (xyz.shape[0], n_expected)
        )
    if not np.all(np.isfinite(xyz)):
        raise ValueError("coordinates contain non-finite values")
    return xyz


def compute_rotational_constants(
    symbols: Sequence[str], coords: np.ndarray
) -> Tuple[float, float, float]:
    """Principal rotational constants A >= B >= C (MHz) from mendeleev masses.

    Moments below 1e-10 of the largest moment (linear molecules) yield ``inf``.
    """
    xyz = _as_coords(coords, len(symbols))
    if xyz.shape[0] < 2:
        raise ValueError("rotational constants need at least two atoms")
    masses = masses_for(symbols)
    com = (masses[:, None] * xyz).sum(axis=0) / masses.sum()
    rel = xyz - com
    r2 = np.einsum("ij,ij->i", rel, rel)
    inertia = np.einsum("i,jk->jk", masses * r2, np.eye(3)) - np.einsum(
        "i,ij,ik->jk", masses, rel, rel
    )
    moments = np.sort(np.linalg.eigvalsh(0.5 * (inertia + inertia.T)))
    scale = max(float(moments.max()), 1.0e-30)
    consts = [
        C_ROT_MHZ / float(m) if float(m) > 1.0e-10 * scale else math.inf for m in moments
    ]
    return float(consts[0]), float(consts[1]), float(consts[2])


def _horn_rotation(p_centered: np.ndarray, q_centered: np.ndarray) -> np.ndarray:
    """Optimal proper rotation R (R p ~ q) from Horn's 4x4 quaternion key matrix."""
    h = p_centered.T @ q_centered
    sxx, sxy, sxz = h[0]
    syx, syy, syz = h[1]
    szx, szy, szz = h[2]
    key = np.array(
        [
            [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
            [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
            [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
            [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
        ],
        dtype=np.float64,
    )
    _, vecs = np.linalg.eigh(key)
    quat = vecs[:, -1]
    quat = quat / np.linalg.norm(quat)
    q0, q1, q2, q3 = quat
    return np.array(
        [
            [q0 * q0 + q1 * q1 - q2 * q2 - q3 * q3, 2 * (q1 * q2 - q0 * q3), 2 * (q1 * q3 + q0 * q2)],
            [2 * (q1 * q2 + q0 * q3), q0 * q0 - q1 * q1 + q2 * q2 - q3 * q3, 2 * (q2 * q3 - q0 * q1)],
            [2 * (q1 * q3 - q0 * q2), 2 * (q2 * q3 + q0 * q1), q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3],
        ],
        dtype=np.float64,
    )


def quaternion_kabsch_rmsd(
    coords1: np.ndarray, coords2: np.ndarray, heavy_mask: Optional[np.ndarray] = None
) -> float:
    """Horn quaternion superposition of ``coords1`` onto ``coords2``; RMSD over masked atoms.

    Both centring and the fit use only the atoms selected by ``heavy_mask`` (all atoms
    when ``None``), so hydrogen displacements cannot influence the heavy-atom RMSD.
    """
    p = _as_coords(coords1)
    q = _as_coords(coords2, p.shape[0])
    if heavy_mask is None:
        mask = np.ones(p.shape[0], dtype=bool)
    else:
        mask = np.asarray(heavy_mask, dtype=bool).ravel()
        if mask.shape[0] != p.shape[0]:
            raise ValueError("heavy_mask length %d != atom count %d" % (mask.shape[0], p.shape[0]))
    if not mask.any():
        raise ValueError("heavy_mask selects no atoms")
    ps = p[mask]
    qs = q[mask]
    pc = ps - ps.mean(axis=0)
    qc = qs - qs.mean(axis=0)
    rot = _horn_rotation(pc, qc)
    diff = pc @ rot.T - qc
    return float(math.sqrt(max(0.0, float(np.einsum("ij,ij->", diff, diff))) / pc.shape[0]))


def rotational_constants_match(
    b1: Sequence[float], b2: Sequence[float], b_thresh: float, rel_b_thresh: float
) -> bool:
    """Stage-1 filter: every constant agrees within |dB|/B < rel or |dB| <= b_thresh."""
    for x, y in zip(b1, b2):
        x = float(x)
        y = float(y)
        if math.isinf(x) or math.isinf(y):
            if math.isinf(x) and math.isinf(y):
                continue
            return False
        delta = abs(x - y)
        ref = max(abs(x), abs(y))
        if ref == 0.0:
            continue
        if delta / ref < rel_b_thresh or delta <= b_thresh:
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
@dataclass
class _Candidate:
    order: int
    cid: str
    record: ConformerRecord
    coords: np.ndarray
    consts: Tuple[float, float, float]
    mask: np.ndarray
    duplicates: List[str] = field(default_factory=list)


def _check_threshold(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v) or v < 0.0:
        raise ValueError("%s must be a finite non-negative number, got %r" % (name, value))
    return v


def deduplicate_conformer_pool(
    candidates: Sequence[ConformerRecord],
    rmsd_thresh: float = DEFAULT_RMSD_THRESH,
    b_thresh: float = DEFAULT_B_THRESH,
    energy_window: float = DEFAULT_ENERGY_WINDOW,
    rel_b_thresh: float = DEFAULT_REL_B_THRESH,
) -> ConformerDeduplicationResult:
    """Two-stage deduplication of a conformer pool.

    Candidates are processed in order of increasing energy (records without an energy
    are processed last, in input order). A candidate is a duplicate of an already
    accepted minimum when (1) the rotational constants match (``|dB|/B < rel_b_thresh``
    or ``|dB| <= b_thresh`` for all of A, B, C) and (2) the heavy-atom quaternion-Kabsch
    RMSD is ``<= rmsd_thresh``. Candidates more than ``energy_window`` kcal/mol above
    the lowest energy are rejected before deduplication. Only proper rotations are used
    in the superposition, so mirror-image (enantiomeric) conformers remain distinct.
    """
    rmsd_thresh = _check_threshold("rmsd_thresh", rmsd_thresh)
    b_thresh = _check_threshold("b_thresh", b_thresh)
    energy_window = _check_threshold("energy_window", energy_window)
    rel_b_thresh = _check_threshold("rel_b_thresh", rel_b_thresh)
    total = len(candidates)
    if total == 0:
        raise ValueError("conformer pool is empty")

    prepared: List[_Candidate] = []
    for order, rec in enumerate(candidates):
        coords = _as_coords(rec.coordinates, len(rec.symbols))
        consts = (
            tuple(float(c) for c in rec.rotational_constants_mhz)
            if rec.rotational_constants_mhz is not None
            else compute_rotational_constants(rec.symbols, coords)
        )
        mask = heavy_atom_mask(rec.symbols)
        if int(mask.sum()) < 3:
            # Fewer than three heavy atoms do not define an orientation; use all atoms.
            mask = np.ones(len(rec.symbols), dtype=bool)
        cid = rec.conformer_id if rec.conformer_id else "candidate_%d" % order
        prepared.append(_Candidate(order, cid, rec, coords, consts, mask))  # type: ignore[arg-type]

    energies = [c.record.energy_kcal_mol for c in prepared if c.record.energy_kcal_mol is not None]
    e_min = min(energies) if energies else None
    tolerance = 1.0e-12 * max(1.0, energy_window)
    rejected = 0
    in_window: List[_Candidate] = []
    for cand in prepared:
        e = cand.record.energy_kcal_mol
        if e is not None and e_min is not None and (e - e_min) > energy_window + tolerance:
            rejected += 1
            LOGGER.info(
                "conformer %s rejected: %.4f kcal/mol above minimum exceeds window %.4f",
                cand.cid, e - e_min, energy_window,
            )
            continue
        in_window.append(cand)

    in_window.sort(
        key=lambda c: (
            c.record.energy_kcal_mol is None,
            c.record.energy_kcal_mol if c.record.energy_kcal_mol is not None else 0.0,
            c.order,
        )
    )

    uniques: List[_Candidate] = []
    shared = 0
    for cand in in_window:
        parent: Optional[_Candidate] = None
        for ref in uniques:
            if list(ref.record.symbols) != list(cand.record.symbols):
                continue
            if not rotational_constants_match(ref.consts, cand.consts, b_thresh, rel_b_thresh):
                continue
            rmsd = quaternion_kabsch_rmsd(ref.coords, cand.coords, ref.mask)
            if rmsd <= rmsd_thresh:
                parent = ref
                break
        if parent is None:
            uniques.append(cand)
        else:
            shared += 1
            parent.duplicates.append(cand.cid)

    out_records: List[ConformerRecord] = []
    for u in uniques:
        e = u.record.energy_kcal_mol
        out_records.append(
            ConformerRecord(
                conformer_id=u.cid,
                symbols=list(u.record.symbols),
                coordinates=[tuple(float(v) for v in row) for row in u.coords],
                energy_kcal_mol=e,
                rotational_constants_mhz=tuple(float(c) for c in u.consts),
                source_track=u.record.source_track,
                relative_energy_kcal_mol=(e - e_min) if (e is not None and e_min is not None) else None,
                duplicate_ids=list(u.duplicates),
            )
        )

    return ConformerDeduplicationResult(
        total_candidates=total,
        unique_conformers=len(out_records),
        shared_duplicates=shared,
        duplication_ratio=float(shared) / float(total),
        conformers=out_records,
        energy_window_rejected=rejected,
        rmsd_thresh=rmsd_thresh,
        b_thresh=b_thresh,
        rel_b_thresh=rel_b_thresh,
        energy_window=energy_window,
    )


# ---------------------------------------------------------------------------
# Track ingestion and output
# ---------------------------------------------------------------------------
def load_track(path: Any, track: str, energy_unit: str = DEFAULT_ENERGY_UNIT) -> List[ConformerRecord]:
    """Read one ensemble file into ConformerRecords (energies converted to kcal/mol)."""
    if energy_unit not in ENERGY_UNIT_TO_KCAL_MOL:
        raise CLIUsageError("unknown energy unit %r" % energy_unit)
    factor = ENERGY_UNIT_TO_KCAL_MOL[energy_unit]
    frames = read_xyz_frames(path)
    records: List[ConformerRecord] = []
    for fr in frames:
        if len(fr.symbols) < 2:
            raise CLIExecutionError(
                "%s frame %d: a conformer needs at least two atoms" % (path, fr.frame_index)
            )
        energy = parse_comment_energy(fr.comment)
        consts = compute_rotational_constants(fr.symbols, fr.coordinates)
        records.append(
            ConformerRecord(
                conformer_id="%s:%d" % (track, fr.frame_index),
                symbols=list(fr.symbols),
                coordinates=[tuple(float(v) for v in row) for row in fr.coordinates],
                energy_kcal_mol=(energy * factor) if energy is not None else None,
                rotational_constants_mhz=consts,
                source_track=track,
            )
        )
    return records


def format_conformers_xyz(records: Sequence[ConformerRecord], energy_unit: str = DEFAULT_ENERGY_UNIT) -> str:
    """Multi-frame XYZ text; the comment line starts with the energy in ``energy_unit``."""
    factor = ENERGY_UNIT_TO_KCAL_MOL[energy_unit]
    blocks: List[str] = []
    for rec in records:
        lines = [str(len(rec.symbols))]
        tag = "id=%s track=%s" % (rec.conformer_id, rec.source_track or "unknown")
        if rec.energy_kcal_mol is not None:
            consts = rec.rotational_constants_mhz or (math.nan, math.nan, math.nan)
            comment = "%.10f %s unit=%s rot_mhz=%s" % (
                rec.energy_kcal_mol / factor,
                tag,
                energy_unit,
                ",".join("%.6f" % c if math.isfinite(c) else "inf" for c in consts),
            )
        else:
            comment = tag
        lines.append(comment)
        for sym, (x, y, z) in zip(rec.symbols, rec.coordinates):
            lines.append("%-2s %16.10f %16.10f %16.10f" % (sym, x, y, z))
        blocks.append("\n".join(lines) + "\n")
    return "".join(blocks)


def _track_summary(
    result: ConformerDeduplicationResult, id_to_track: Dict[str, str], track_counts: Dict[str, int]
) -> Tuple[Dict[str, Any], int, List[List[str]]]:
    per_track: Dict[str, Dict[str, int]] = {
        t: {"candidates": n, "unique_minima_found": 0, "exclusive_unique_minima": 0}
        for t, n in track_counts.items()
    }
    cross = 0
    found_by: List[List[str]] = []
    for rec in result.conformers:
        own = rec.source_track or id_to_track.get(rec.conformer_id or "", "unknown")
        tracks = {own}
        for dup in rec.duplicate_ids:
            t = id_to_track.get(dup, "unknown")
            tracks.add(t)
            if t != own:
                cross += 1
        ordered = sorted(tracks)
        found_by.append(ordered)
        for t in ordered:
            if t in per_track:
                per_track[t]["unique_minima_found"] += 1
        if len(ordered) == 1 and ordered[0] in per_track:
            per_track[ordered[0]]["exclusive_unique_minima"] += 1
    return per_track, cross, found_by


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _nonnegative_float(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid number: %r" % text) from None
    if not math.isfinite(value) or value < 0.0:
        raise argparse.ArgumentTypeError("must be a finite non-negative number, got %r" % text)
    return value


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Show defaults and keep the epilog layout."""


_CONFORMER_EPILOG = """\
search modes (--mode):
  orca_goat  ensemble produced by ORCA GOAT (*.finalensemble.xyz); single track
  crest      ensemble produced by CREST (crest_conformers.xyz); single track
  union      merge the --input ensemble with every --union-with ensemble
             (dual-track GOAT + CREST) before deduplication

deduplication (two stages):
  stage 1  rotational constants A >= B >= C in MHz (mendeleev masses) must agree,
           |dB|/B < --rel-b-thresh or |dB| <= --b-thresh, for each constant
  stage 2  heavy-atom RMSD after Horn quaternion superposition <= --rmsd-thresh
           (proper rotations only, so enantiomers stay distinct; hydrogens masked)

energies are read from the first decimal number of each XYZ comment line
(--energy-unit, default hartree) and filtered with --energy-window in kcal/mol.

output: a JSON report on stdout (total_candidates, unique_conformers,
shared_duplicates, duplication_ratio R_union = N_shared / N_total, conformers);
--out writes the unique conformers as multi-frame XYZ, or the JSON report when
the file name ends in .json.
"""


def register_conformer_subparser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "conformer",
        help="Dual-track GOAT + CREST conformer union search and deduplication",
        description=(
            "Merge conformer ensembles (multi-frame XYZ from ORCA GOAT and/or CREST), "
            "filter by energy window, deduplicate by rotational constants and "
            "heavy-atom quaternion-Kabsch RMSD, and report the union duplication "
            "ratio R_union = N_shared / N_total."
        ),
        epilog=_CONFORMER_EPILOG,
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Candidate conformer ensemble (multi-frame XYZ).")
    parser.add_argument(
        "--mode",
        choices=list(CONFORMER_MODES),
        default=DEFAULT_MODE,
        help="Search track provenance: orca_goat, crest, or union of both tracks.",
    )
    parser.add_argument(
        "--union-with",
        action="append",
        default=[],
        metavar="PATH",
        help="Additional ensemble(s) from the complementary track (union mode only).",
    )
    parser.add_argument(
        "--energy-window",
        type=_nonnegative_float,
        default=DEFAULT_ENERGY_WINDOW,
        help="Energy window above the global minimum in kcal/mol.",
    )
    parser.add_argument(
        "--rmsd-thresh",
        type=_nonnegative_float,
        default=DEFAULT_RMSD_THRESH,
        help="Heavy-atom Cartesian RMSD duplicate threshold in Angstrom.",
    )
    parser.add_argument(
        "--b-thresh",
        type=_nonnegative_float,
        default=DEFAULT_B_THRESH,
        help="Absolute rotational-constant tolerance in MHz.",
    )
    parser.add_argument(
        "--rel-b-thresh",
        type=_nonnegative_float,
        default=DEFAULT_REL_B_THRESH,
        help="Relative rotational-constant tolerance |dB|/B.",
    )
    parser.add_argument(
        "--energy-unit",
        choices=sorted(ENERGY_UNIT_TO_KCAL_MOL),
        default=DEFAULT_ENERGY_UNIT,
        help="Unit of the energies found in the XYZ comment lines.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write unique conformers to PATH (XYZ, or JSON report if PATH ends in .json).",
    )
    return parser


def execute_conformer(args: argparse.Namespace) -> Tuple[Dict[str, Any], ConformerDeduplicationResult, str]:
    """Run the conformer workflow; returns (json-safe payload, result, energy unit)."""
    mode = getattr(args, "mode", DEFAULT_MODE)
    if mode not in CONFORMER_MODES:
        raise CLIUsageError("unknown --mode %r (choose from %s)" % (mode, ", ".join(CONFORMER_MODES)))
    union_with = [str(p) for p in (getattr(args, "union_with", None) or [])]
    if union_with and mode != "union":
        raise CLIUsageError("--union-with is only valid with --mode union (got --mode %s)" % mode)
    energy_unit = getattr(args, "energy_unit", DEFAULT_ENERGY_UNIT) or DEFAULT_ENERGY_UNIT

    tracks: List[Tuple[str, str]] = [(str(args.input), _PRIMARY_TRACK_LABEL[mode])]
    for k, extra in enumerate(union_with):
        tracks.append((extra, "union_with_%d" % (k + 1)))

    pool: List[ConformerRecord] = []
    inputs: List[Dict[str, Any]] = []
    id_to_track: Dict[str, str] = {}
    track_counts: Dict[str, int] = {}
    for path, label in tracks:
        records = load_track(path, label, energy_unit)
        pool.extend(records)
        track_counts[label] = len(records)
        for rec in records:
            id_to_track[str(rec.conformer_id)] = label
        inputs.append(
            {
                "path": str(path),
                "track": label,
                "frames": len(records),
                "frames_with_energy": sum(1 for r in records if r.energy_kcal_mol is not None),
            }
        )

    result = deduplicate_conformer_pool(
        pool,
        rmsd_thresh=args.rmsd_thresh,
        b_thresh=args.b_thresh,
        energy_window=args.energy_window,
        rel_b_thresh=getattr(args, "rel_b_thresh", DEFAULT_REL_B_THRESH),
    )
    per_track, cross, found_by = _track_summary(result, id_to_track, track_counts)

    conformer_dicts: List[Dict[str, Any]] = []
    for rec, tracks_found in zip(result.conformers, found_by):
        d = model_to_dict(rec)
        d["found_by_tracks"] = tracks_found
        conformer_dicts.append(d)

    out = getattr(args, "out", None)
    out_info: Optional[Dict[str, Any]] = None
    if out:
        out_path = Path(str(out))
        out_info = {
            "path": str(out_path),
            "format": "json" if out_path.suffix.lower() == ".json" else "xyz",
        }

    payload = {
        "subcommand": "conformer",
        "mode": mode,
        "inputs": inputs,
        "energy_unit": energy_unit,
        "thresholds": {
            "energy_window_kcal_mol": result.energy_window,
            "rmsd_thresh_angstrom": result.rmsd_thresh,
            "b_thresh_mhz": result.b_thresh,
            "rel_b_thresh": result.rel_b_thresh,
        },
        "total_candidates": result.total_candidates,
        "energy_window_rejected": result.energy_window_rejected,
        "unique_conformers": result.unique_conformers,
        "shared_duplicates": result.shared_duplicates,
        "cross_track_duplicates": cross,
        "duplication_ratio": result.duplication_ratio,
        "tracks": per_track,
        "conformers": conformer_dicts,
        "out": out_info,
    }
    return json_safe(payload), result, energy_unit


def run_conformer_cli(args: argparse.Namespace) -> int:
    """Entry point used by ``cochem conformer``; returns the process exit code."""
    try:
        payload, result, energy_unit = execute_conformer(args)
        text = json.dumps(payload, indent=2, allow_nan=False)
        out = getattr(args, "out", None)
        if out:
            out_path = Path(str(out))
            if out_path.suffix.lower() == ".json":
                write_text_file(out_path, text + "\n")
            else:
                write_text_file(out_path, format_conformers_xyz(result.conformers, energy_unit))
    except CLIExecutionError as exc:
        print("cochem conformer: error: %s" % exc, file=sys.stderr)
        return int(exc.exit_code)
    except (ValueError, OSError) as exc:
        print("cochem conformer: error: %s" % exc, file=sys.stderr)
        return 1
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0
