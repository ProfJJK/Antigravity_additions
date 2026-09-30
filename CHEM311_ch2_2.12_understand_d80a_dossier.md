# P2 Context Dossier: McMurry 10e Section 2.12 Curricular Ingestion & Physical Ground-Truth Ledger

**Task ID**: `CHEM311_ch2_2.12_understand_d80a_1` (Parent: `CHEM311_ch2_2.12_understand_d80a`)  
**Target Deliverable**: [`CHEM311_ch2_2.12_understand_d80a_ledger.json`](file:///D:/__CoChem/__agentic/CHEM311_ch2_2.12_understand_d80a_ledger.json)  
**Assigned Role**: P2 RESEARCHER (`cochem-researcher`)  
**Supervising Specifications**: [`SRS_Mini_Task_CHEM311_ch2_2.12_understand_d80a_Fractured.md`](file:///D:/__CoChem/__agentic/dropzones/inbox_srs/SRS_Mini_Task_CHEM311_ch2_2.12_understand_d80a_Fractured.md), [`mcmurry_10e_topic_map.md`](file:///D:/Gdrive/__agentic/.sources/r_exams/mcmurry_10e_topic_map.md)  
**Governing Charters**: Anti-Spoofing Protocol v4, Mendeleev Mass Mandate ([`cochem-mendeleev-masses.md`](file:///C:/Users/ansac/.gemini/config/rules/cochem-mendeleev-masses.md))

---

## 1. Architectural Mission & Operational Scope

The engineer must synthesize and emit the authoritative educational and physical chemistry ground-truth ledger [`CHEM311_ch2_2.12_understand_d80a_ledger.json`](file:///D:/__CoChem/__agentic/CHEM311_ch2_2.12_understand_d80a_ledger.json) at the workspace root. This ledger defines the foundational parameter space for Chunk 2 (`.Rmd` authoring), Chunk 3 (AOT sandbox compilation), and Chunk 4 (asymmetric audit).

### In-Scope Boundaries
- **Curricular Scope**: Ingest McMurry 10th Edition Section 2.12 (*"Noncovalent Interactions between Molecules"*, ISBN 978-1-71147-185-3) under Chapter 2 (*"Polar Covalent Bonds; Acids and Bases"*).
- **Bloom Tier Calibration**: Explicitly calibrate for the **"Understand"** cognitive level (Bloom Revised Level 2). Unlike Level 1 ("Remember"), which focuses on definitions and rote energetic bounds, "Understand" requires mechanistic explanations of physical property trends (boiling points, molecular shape, surface area, and permanent dipole moments).
- **Dynamic Mass Ingestion**: Retrieve atomic and stoichiometric masses dynamically via the live `mendeleev` Python library.
- **Pedagogical Distractor Matrix**: Construct exactly five diagnostic distractors targeting distinct conceptual failure modes with a strict 1-to-1 bijection.

### Out-of-Scope Boundaries
- Authoring declarative R/exams `.Rmd` questions (Chunk 2).
- Executing AOT headless `exams2html` or `exams2nops` compilation (Chunk 3).
- Performing static AST anti-spoof linting or committing to canonical testbank (Chunk 4).

---

## 2. Existing Code & Patterns to Reuse

1. **Companion Ledger Reference**:  
   Examine [`CHEM311_ch2_2.12_remember_b756_ledger.json`](file:///D:/__CoChem/__agentic/CHEM311_ch2_2.12_remember_b756_ledger.json) for canonical Section 2.12 definitions, and [`CHEM311_ch2_2.11_understand_6c4a_ledger.json`](file:///D:/__CoChem/__agentic/CHEM311_ch2_2.11_understand_6c4a_ledger.json) for how Bloom level `"understand"` structures explanatory models, formal field requirements, and pedagogical refutations.
2. **Formula Parsing Pattern**:  
   Reuse regex token parsing from [`test_task_CHEM311_ch2_2_12_remember_b756_1.py`](file:///D:/__CoChem/__agentic/tests/tdd/test_task_CHEM311_ch2_2_12_remember_b756_1.py#L74-L109) to compute stoichiometric formula sums dynamically from elemental symbols without hardcoding intermediate weights.
3. **Provenance Validation Pattern**:  
   Reuse provenance string validation patterns established in [`test_task_CHEM311_ch2_2_11_understand_6c4a_1.py`](file:///D:/__CoChem/__agentic/tests/tdd/test_task_CHEM311_ch2_2_11_understand_6c4a_1.py#L321-L335) ensuring dynamic verification against `importlib.metadata.version('mendeleev')`.

---

## 3. Exact APIs, Signatures & Data Schemas

### 3.1 Python Runtime Queries
```python
import importlib.metadata
from mendeleev import element

# Dynamic version check
installed_version: str = importlib.metadata.version("mendeleev")  # Returns "1.2.0"

# Dynamic elemental mass retrieval (float, within 1e-4 amu)
raw_masses = {sym: float(element(sym).mass) for sym in ("H", "C", "N", "O", "F", "Cl")}
# Live values: H: 1.008, C: 12.011, N: 14.007, O: 15.999, F: 18.998403163, Cl: 35.45
```

### 3.2 Top-Level Ledger Schema
The JSON root must be an object decoding strictly as UTF-8 without BOM containing these 10 required keys:
```json
{
  "task_id": "CHEM311_ch2_2.12_understand_d80a.1",
  "parent_task_id": "CHEM311_ch2_2.12_understand_d80a",
  "course_id": "CHEM311",
  "chapter": 2,
  "section": "2.12",
  "bloom_level": "understand",
  "mcmurry_topic_scope": { ... },
  "chemical_ground_truth": { ... },
  "elemental_masses_mendeleev": { ... },
  "five_distractor_specification_matrix": [ ... ]
}
```

---

## 4. Authoritative Chemical Ground Truth & Physical Property Trends

### 4.1 Canonical Energetic Bounds
- **London Dispersion Forces**: 2–10 kJ/mol. Present in all molecules (polar and nonpolar). Caused by instantaneous electron density fluctuations inducing complementary dipoles.
- **Dipole-Dipole Forces**: 5–20 kJ/mol. Attraction between net permanent molecular dipole moments in polar molecules.
- **Hydrogen Bonding**: 15–40 kJ/mol. Strong, directional attraction between H covalently bonded to O, N, or F and a lone pair on another O, N, or F.
- **Covalent Bonds**: >350 kJ/mol (e.g., C–C 377, C–H 410, O–H 460 kJ/mol). Intramolecular bonds holding atoms together; remain 100% intact during boiling and melting.

### 4.2 Three Mechanistic Physical Property Case Studies
1. **Constitutional Isomers with Differing Forces (Hydrogen Bonding vs. Dipole-Dipole/Dispersion)**:
   - *Ethanol* ($\text{CH}_3\text{CH}_2\text{OH}$, bp $78.4^\circ\text{C}$, MW 46.069 amu): Possesses a polarized $\text{O--H}$ bond acting as both H-bond donor and acceptor, forming an extensive intermolecular network requiring high thermal energy to vaporize.
   - *Dimethyl ether* ($\text{CH}_3\text{OCH}_3$, bp $-24.0^\circ\text{C}$, MW 46.069 amu): Constitutional isomer with identical molecular formula ($\text{C}_2\text{H}_6\text{O}$) and molecular weight. Lacks an $\text{O--H}$ bond (all H attached to C); cannot donate hydrogen bonds to self-associate. Interacts solely through weaker dipole-dipole and dispersion forces, causing a $102.4^\circ\text{C}$ boiling point deficit.
2. **Molecular Shape, Branching & Surface Area (Dispersion Scaling)**:
   - *Pentane* (linear $n$-pentane, bp $36.1^\circ\text{C}$, MW 72.151 amu): Extended zig-zag cylindrical conformation provides maximum accessible van der Waals surface area, maximizing contact area and cumulative dispersion attractions between adjacent molecules.
   - *2,2-Dimethylpropane* (neopentane, bp $9.5^\circ\text{C}$, MW 72.151 amu): Constitutional isomer with identical formula ($\text{C}_5\text{H}_{12}$) and mass. Highly branched, spherical shape minimizes external surface area for a given volume, drastically reducing intermolecular contact points and cumulative dispersion forces, lowering the boiling point by $26.6^\circ\text{C}$.
3. **Permanent Dipole Strength at Iso-Mass (Dipole-Dipole vs. Dispersion)**:
   - *Acetone* ($\text{CH}_3\text{COCH}_3$, bp $56.2^\circ\text{C}$, MW 58.080 amu): Contains a strongly polarized carbonyl group ($\text{C}=\text{O}$) generating a large permanent dipole moment ($\mu = 2.88\text{ D}$), providing substantial dipole-dipole stabilization in addition to dispersion forces.
   - *Isobutane* (2-methylpropane, bp $-11.7^\circ\text{C}$, MW 58.124 amu): Nearly identical molecular weight (within 0.044 amu) and electron count, but essentially nonpolar ($\mu \approx 0.1\text{ D}$). Relies almost exclusively on dispersion forces, resulting in a $67.9^\circ\text{C}$ lower boiling point.

---

## 5. Five-Distractor Pedagogical Specification Matrix

Each distractor object must contain:
`distractor_id`, `choice_text`, `target_misconception`, `bloom_level` (`"understand"`), `cognitive_failure_mode` (>= 20 chars), `pedagogical_refutation` (>= 50 chars), and `mcmurry_10e_ground_truth_citation`.

The five distractors must map 1-to-1 across the following diagnostic failure modes:

| ID | Failure Mode | Target Misconception & Choice Text Core | Pedagogical Refutation Core |
|---|---|---|---|
| `DISTRACTOR_1_COVALENT_CLEAVAGE_VAPORIZATION` | **Covalent bond cleavage during vaporization** | Claims boiling ethanol (78.4 °C) breaks intramolecular C–H, C–C, or O–H covalent bonds (>350 kJ/mol) to separate atoms into vapor. | Boiling provides energy to overcome weak intermolecular forces (15–40 kJ/mol); intramolecular covalent bonds remain completely intact. |
| `DISTRACTOR_2_SURFACE_AREA_VS_MASS_CONFUSION` | **Surface area vs. molecular weight confusion** | Claims pentane boils higher than 2,2-dimethylpropane because pentane has a greater molecular weight and more electrons. | Pentane and neopentane are constitutional isomers with identical molecular weight (72.151 amu); difference is driven by cylindrical vs. spherical surface area. |
| `DISTRACTOR_3_HBOND_DONOR_ACCEPTOR_MISCONCEPTION` | **Hydrogen bonding donor/acceptor misconceptions** | Claims dimethyl ether boils lower because its oxygen atom lacks lone pairs and cannot participate in hydrogen bonding as an acceptor. | Ether oxygen has two lone pairs and readily accepts H-bonds from donor solvents; it cannot self-associate because it lacks an O–H donor hydrogen. |
| `DISTRACTOR_4_FORCE_HIERARCHY_INVERSION` | **Force hierarchy inversions** | Claims London dispersion forces are intrinsically stronger than hydrogen bonding in small organic molecules, driving phase stability. | In small molecules of comparable mass, hydrogen bonding (15–40 kJ/mol) is far stronger than dispersion (2–10 kJ/mol), explaining ethanol's high boiling point. |
| `DISTRACTOR_5_DIPOLE_SYMMETRY_CANCELLATION` | **Dipole symmetry cancellation errors** | Claims highly symmetric molecules with polar bonds (e.g., CCl4) exhibit permanent dipole-dipole attractions that dominate boiling points. | Tetrahedral symmetry causes polar C–Cl bond vectors to cancel completely ($\mu = 0\text{ D}$); symmetrical halocarbons experience zero dipole-dipole attractions. |

---

## 6. Hard Constraints, Anti-Spoofing Protocols & Forensic Pitfalls

1. **Zero-Mock & Forbidden Tokens**:  
   The raw JSON string must contain zero occurrences of `"todo"`, `"tbd"`, `"placeholder"`, `"lorem ipsum"`, `"notimplemented"`, or `"fixme"`. Total content must exceed 2,000 characters.
2. **Dynamic Mendeleev Mandate**:  
   Never hardcode static atomic weights into the generation script. Query `element(symbol).mass` and record provenance affirming dynamic retrieval in compliance with [`cochem-mendeleev-masses.md`](file:///C:/Users/ansac/.gemini/config/rules/cochem-mendeleev-masses.md).
3. **No BOM Encoding**:  
   Write the file as standard UTF-8 (`open(path, 'w', encoding='utf-8')`). Do not use UTF-8 with BOM (`utf-8-sig`).
4. **Avoid Bloom Drift**:  
   Keep the cognitive demand focused on *explaining* and *comparing* physical property trends (Why does ethanol boil higher than dimethyl ether? Why does branching lower boiling points?). Do not reduce options to pure terminology matching (Remember) or multi-step reaction prediction (Analyze).
5. **Exact Type Matching**:  
   - `chapter`: `2` (integer, not string `"2"`)
   - `section`: `"2.12"` (string, not float `2.12`)
   - `bloom_level`: `"understand"` (lowercase)
   - `course_id`: `"CHEM311"`

---

## 7. Verification & Implementation Roadmap for the Engineer

1. **Script Implementation**:  
   Create an executable generator script (e.g., `scripts/generate_CHEM311_ch2_2_12_understand_d80a_ledger.py`) that:
   - Imports `mendeleev.element` and `importlib.metadata`.
   - Computes stoichiometric masses for all benchmark species (`C2H6O`, `C5H12`, `C3H6O`, `C4H10`, `H2O`, `NH3`, `CH4`, `HF`, `CH3Cl`).
   - Populates the 10 top-level keys with rich, substantive chemical descriptions.
   - Serializes formatted JSON directly to [`CHEM311_ch2_2.12_understand_d80a_ledger.json`](file:///D:/__CoChem/__agentic/CHEM311_ch2_2.12_understand_d80a_ledger.json).
2. **Self-Verification Checklist**:
   - Verify file existence: `Test-Path CHEM311_ch2_2.12_understand_d80a_ledger.json`.
   - Verify file length: `(Get-Content CHEM311_ch2_2.12_understand_d80a_ledger.json -Raw).Length > 2000`.
   - Verify forbidden tokens: Ensure case-insensitive scan yields zero matches.
   - Run python validation snippet checking elemental and molecular tolerances against live `mendeleev`.
