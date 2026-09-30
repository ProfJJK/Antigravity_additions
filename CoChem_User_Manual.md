# CoChem User Manual: Unified Command-Line Interface, Python API, and HPC Deployment

- **Author:** Dr. Joshua John Klaassen (Principal Investigator)
- **ORCiD:** [0000-0002-1825-0097](https://orcid.org/0000-0002-1825-0097)
- **Organisation:** CoChem project (repository family CoChem-BASE and CoChem-TOPOS)
- **Document version:** 20.110.4
- **Last updated:** 2026-09-26
- **Format:** plain UTF-8 Markdown with fenced, copy-pasteable code blocks
- **Scope:** the unified `cochem` command-line interface (`init`, `run`, `spectral-predict`, `pes`, `conformer`, `audit`), the standardized exit code contract (codes 0 through 6), troubleshooting guides, the programmatic Python API, and HPC batch submission for SLURM and PBS.

<!-- Release note: the ORCiD value above is ORCID's public sample identifier. Replace it with the author's registered iD before the manual is published. -->

**FAIR statement.** The manual is *Findable* (versioned, one file at the workspace root), *Accessible* (plain-text Markdown), *Interoperable* (JSON output envelope, Pickett `.cat` catalogs, HDF5 archives), and *Reusable* (every command, flag, and exit code is stated explicitly and can be checked against the parameter tables).

## Abstract

CoChem couples electronic-structure calculations, rotational spectroscopy, potential energy surface (PES) mapping with discrete variable representation (DVR) tunneling analysis, and dual-track conformer searching behind one command. This manual is the operational reference. It documents every subcommand and flag, gives end-to-end workflows that can be pasted into a shell, defines the exit code contract (codes 0 through 6), gives remediation procedures for the failures seen most often in production, and shows how to drive the same engines from Python and from HPC schedulers.

**Conventions.**

- Shell examples use POSIX `bash`. PowerShell equivalents differ only in line continuation (a trailing backtick instead of a backslash) and in reading the exit status (`$LASTEXITCODE`).
- Flags are written in the form `--flag`. Short aliases, where they exist, are given in the same table row.
- Global flags (`--verbose`, `--quiet`, `--json`) are accepted before or after the subcommand.
- Atomic masses are never typed by hand in this manual. Code obtains them from the `mendeleev` package, for example `element("C").mass`.
- All text files are read and written as UTF-8. All Python subprocess calls pass `encoding="utf-8"` and, for Windows, `creationflags` containing `CREATE_NO_WINDOW`.

**Contents.**

1. Unified CLI Architecture and Workspace Lifecycle
2. CLI Command Reference
3. Reference Workflows (End-to-End)
4. Subsystem Guides: Method Matrix, Spectroscopy, PES/DVR, Conformers
5. Exit Code Contract
6. Troubleshooting Guides
7. Programmatic Python API Tutorials
8. HPC Cluster Deployment and Batch Submission
9. Appendix A: Theoretical Foundations
10. Appendix B: Integrity Auditing and Anti-Spoofing Rules

## Chapter 1: Unified CLI Architecture and Workspace Lifecycle

### 1.1 One Entry Point, Six Subcommands

The `cochem` executable is a thin dispatcher. It validates arguments, applies Method Matrix constraints, stages files through a sandbox, invokes the requested engine, and translates the outcome into a single exit code from the contract in Chapter 5.

| Subcommand | Purpose | Primary Artifact |
| --- | --- | --- |
| `init` | Create a sealed project workspace | `.cochem_project.json` |
| `run` | Execute a quantum calculation under Method Matrix constraints | optimized geometry, Hessian archive, result record |
| `spectral-predict` | Predict rotational spectroscopic observables | Pickett `.cat` transition catalog |
| `pes` | Map a relaxed potential curve and solve the DVR problem | HDF5 archive with potential and wavefunctions |
| `conformer` | Dual-track conformer generation and deduplication | ranked conformer ensemble |
| `audit` | Verify workspace integrity, binaries, and anti-spoofing rules | audit report and exit status |

The lifecycle order is fixed: `init` creates the workspace, `run` produces a converged geometry, and `spectral-predict`, `pes`, and `conformer` consume that geometry. `audit` may be called at any point and should be called last.

### 1.2 Prerequisites

Install the CoChem package into a Python 3.10 or newer environment. The engines you plan to use must be present on the `PATH`:

- ORCA (for `--engine orca` and the GOAT conformer track)
- xtb (for `--engine xtb`)
- CFOUR (for `--engine cfour`)
- CREST (for the metadynamics conformer track)
- Pickett SPCAT (for `spectral-predict`)
- a CUDA-capable driver, if you intend to use `--device cuda`

Verify the installation with a binary health check:

```bash
cochem audit --target . --check-binaries
```

A missing binary produces exit code 2 (`ENVIRONMENT_BLOCK`) and names the missing executable.

### 1.3 Workspace Anatomy

`cochem init` scaffolds this layout:

```text
cochem_project/
+-- .cochem_project.json
+-- config/
|   +-- methods/
+-- data/
|   +-- raw/
|   +-- processed/
+-- models/
|   +-- checkpoints/
+-- telemetry/
    +-- logs/
```

`.cochem_project.json` is a cryptographic seal. It records the creation metadata and the digests of the immutable scaffold files. `cochem audit` re-computes these digests. A mismatch is an integrity violation (exit code 6).

### 1.4 Tripartite Sandbox

Every `run` promotes artifacts through three stages so that a crashed or killed job can never leave a half-written file in the results directory:

1. **tmp_path.** The engine writes into a private temporary directory.
2. **scratch.** Completed files move to the fast ephemeral directory given by `--scratch`.
3. **store.** Files that pass validation are promoted to the directory given by `--output`. Each promoted file receives a `.sha256` sidecar containing its digest.

### 1.5 Subprocess Broker Invariants

The CLI spawns engines through a broker that forces UTF-8 decoding of engine output and, on Windows, suppresses console windows so that batch runs never steal terminal focus. Any Python code you write that shells out to CoChem should follow the same rule. Section 7.7 shows the pattern.

### 1.6 JSON Output Envelope

With `--json`, standard output carries a single JSON object and human-readable messages go to standard error:

```json
{
  "status": "SUCCESS",
  "exit_code": 0,
  "subcommand": "run",
  "message": "calculation converged",
  "artifacts": ["store/water_opt.xyz", "store/water_opt.xyz.sha256"]
}
```

On failure, `status` carries the symbol from the exit code contract (for example `SCF_DIVERGENCE`), `exit_code` carries the numeric code, and `message` carries the diagnostic.

## Chapter 2: CLI Command Reference

### 2.1 Invocation Grammar

```text
cochem [global-flags] subcommand [subcommand-flags]
```

Flags that take several values, such as `--coordinate-index` and `--dipole`, consume the following space-separated tokens. `--help` is available on every subcommand. Every table below uses the same four columns: Flag, Type, Default, and Description.

### 2.2 Global Flags

Global flags apply to every subcommand.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--verbose`, `-v` | bool | `False` | Enable DEBUG logging and stream engine output in real time to standard error. |
| `--quiet`, `-q` | bool | `False` | Suppress the banner and informational progress messages. Errors are still printed. |
| `--json` | bool | `False` | Emit the structured JSON envelope on standard output instead of formatted text. |

`--verbose` and `--quiet` are mutually exclusive. Passing both is a syntax failure (exit code 1).

### 2.3 `init`: Workspace Initialization

`init` creates the workspace directory tree, writes the `.cochem_project.json` seal, and performs a disk quota preflight check before touching the disk. It has no mandatory flags.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--workspace-dir` | Path | `.` | Target directory for CoChem project scaffolding. |
| `--interactive` | bool | `False` | Prompt for a custom project name, licence, and compute backend. |
| `--min-free-bytes` | int | `1000000000` | Minimum free disk space in bytes required before initialization proceeds. |
| `--overwrite` | bool | `False` | Force re-initialization of a directory that already contains a sealed project. |

Exit codes: 0 on success, 1 for an existing project without `--overwrite`, 2 for a storage quota deficit.

```bash
cochem init --workspace-dir cochem_project --interactive
```

### 2.4 `run`: Quantum Calculation Execution

`run` validates the requested engine, method, and basis against the Method Matrix (Section 4.1), then executes the calculation inside the tripartite sandbox. The mandatory flags are `--input`, `--engine`, `--method`, and `--basis`.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--input`, `-i` | Path | *Required* | Input molecular Cartesian geometry (`.xyz`, `.mol`, or `.json`). |
| `--engine` | Enum | `orca` | Backend quantum driver: `orca`, `xtb`, `cfour`, or `mace`. |
| `--method` | str | `B3LYP` | Electronic structure method or functional, for example `GFN2-xTB`, `wB97M-V`, or `CCSD(T)`. |
| `--basis` | str | `def2-SVP` | Gaussian basis set, for example `def2-TZVP` or `cc-pVTZ`. Use `none` for semiempirical and machine-learned engines. |
| `--device` | Enum | `auto` | Execution accelerator: `auto`, `cpu`, or `cuda`. |
| `--threads`, `-t` | int | `4` | Number of OpenMP or MPI worker threads. |
| `--scratch` | Path | `scratch/` | High-speed ephemeral scratch directory. |
| `--output`, `-o` | Path | `store/` | Directory that receives promoted artifacts. |

Promoted artifacts follow the naming convention `STEM_opt.xyz` (optimized geometry), `STEM_hessian.h5` (Hessian archive), and `STEM_result.json` (result record), each with a `.sha256` sidecar. `STEM` is the input file name without its extension.

Exit codes: 0 success, 1 invalid engine/method/basis combination, 2 missing engine binary, 3 SCF divergence, 5 GPU or host resource exhaustion.

```bash
cochem run --input water.xyz --engine orca --method B3LYP --basis def2-SVP --threads 4
```

### 2.5 `spectral-predict`: Rotational Spectrum Prediction

`spectral-predict` computes rotational constants, the Ray asymmetry parameter, centrifugal distortion, and Boltzmann-weighted transition intensities, then writes a Pickett-format catalog. The mandatory flags are `--input`, `--temperature`, `--freq-max`, and `--out-cat`.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--input`, `-i` | Path | *Required* | Converged quantum geometry (`.xyz`) or Hessian archive (`.h5`). |
| `--temperature` | float | `300.0` | Simulation temperature in kelvin, used for Boltzmann populations. |
| `--freq-max` | float | `500.0` | Maximum transition search frequency in GHz. |
| `--out-cat` | Path | `spectrum.cat` | Destination of the exported Pickett transition catalog. |
| `--dipole` | float[3] | `None` | Ground-state dipole moment components (mu_a, mu_b, mu_c) in debye along the principal axes. Supply it whenever the input is a bare `.xyz` file. |

Exit codes: 0 success, 1 invalid arguments or unwritable `--out-cat`, 2 missing `spcat` binary, 4 non-physical inertia tensor.

```bash
cochem spectral-predict --input store/water_opt.xyz --temperature 300.0 --freq-max 500.0 --out-cat water.cat --dipole 0.0 1.85 0.0
```

### 2.6 `pes`: Active-Learning PES and DVR Tunneling

`pes` scans one internal coordinate, refines the potential curve with active-learning Gaussian-process regression, and solves the one- or two-dimensional Colbert-Miller DVR problem for eigenvalues and tunneling splittings. The electronic-structure settings used for the scan single points come from the method configuration in the workspace `config/methods` directory. The mandatory flags are `--input`, `--coordinate-index`, `--grid-points`, and `--dvr-modes`.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--input`, `-i` | Path | *Required* | Seed molecular geometry for the potential scan. |
| `--coordinate-index` | int[4] | *Required* | Four zero-indexed atom indices that define the internal coordinate, for example a dihedral angle. |
| `--grid-points` | int | `36` | Number of equidistant points along the scan coordinate. |
| `--dvr-modes` | int | `1` | Dimensionality of the DVR solver, either 1 or 2. |
| `--out-h5` | Path | `pes_dvr.h5` | HDF5 output archive for the potential curve, eigenvalues, and wavefunctions. |

Exit codes: 0 success, 1 invalid indices or `--dvr-modes` outside 1 or 2, 3 SCF divergence at a scan point, 4 DVR potential grid singularity.

```bash
cochem pes --input h2o2.xyz --coordinate-index 2 0 1 3 --grid-points 72 --dvr-modes 1 --out-h5 h2o2_torsion.h5
```

### 2.7 `conformer`: Dual-Track Conformer Generation

`conformer` explores conformational space with ORCA GOAT and CREST metadynamics, forms the union of both ensembles, and prunes it by energy window, rotational constants, and heavy-atom RMSD. Results are written under the workspace `data/processed` directory. The only mandatory flag is `--input`.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--input`, `-i` | Path | *Required* | Molecular structure file (`.xyz` or `.smi`). |
| `--engine` | Enum | `dual-union` | Exploration track: `orca-goat`, `crest`, or `dual-union`. |
| `--energy-window` | float | `3.0` | Energetic pruning window in kcal/mol relative to the lowest conformer. |
| `--rmsd-tol` | float | `0.05` | Heavy-atom quaternion Kabsch RMSD deduplication tolerance in angstrom. |
| `--rot-tol` | float | `5.0` | Rotational constant matching tolerance in MHz. |

Exit codes: 0 success, 1 unreadable structure or unknown track, 2 missing ORCA or CREST binary.

```bash
cochem conformer --input ethylene_glycol.smi --engine dual-union --energy-window 3.0
```

### 2.8 `audit`: Integrity and Anti-Spoofing Audit

`audit` verifies the workspace seal, checks `.sha256` sidecars, optionally probes the quantum binaries, and, with `--full`, runs an exhaustive AST sweep for anti-spoofing violations. It has no mandatory flags.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--target` | Path | `.` | Directory or script target to inspect. |
| `--full` | bool | `False` | Run the exhaustive AST anti-spoofing and storage sanity sweep. |
| `--check-binaries` | bool | `True` | Validate the presence of the quantum binaries and execute a health check on each. |

Exit codes: 0 clean, 2 missing binary, 6 integrity or anti-spoofing violation.

```bash
cochem audit --target . --full
```

### 2.9 Output Streams and Exit Status

Standard output carries the primary result (formatted text, or the JSON envelope with `--json`). Diagnostics, progress messages, and streamed engine output go to standard error. The process exit status is always one of the seven codes in Chapter 5, so automation should branch on the number and ignore the message text.

## Chapter 3: Reference Workflows (End-to-End)

This chapter chains all six subcommands into one session. Run the steps in order from a fresh shell. Every command is complete and copy-pasteable, and every flag used here is documented in Chapter 2.

The scientific pipeline behind the steps is as follows. The `run` step solves the self-consistent field (SCF) equations of Hartree-Fock or density functional theory (DFT) within the Born-Oppenheimer approximation to obtain a converged equilibrium geometry. The `spectral-predict` step turns that geometry into rotational constants from the moments of inertia and a Pickett SPCAT catalog (a `.cat` file) of rotational transitions with Boltzmann-weighted intensities that depend on the dipole moment. The `pes` step maps a potential energy surface (PES) along one torsion and solves the discrete variable representation (DVR) eigenproblem for the vibrational Hamiltonian to resolve tunneling doublets. The `conformer` step builds a conformational ensemble and prunes it by energy window and RMSD. The final `audit` step confirms the integrity of every SHA-256 sealed artifact.

### Step 1: Initialize the Workspace

1. Create the sealed project directory and enter it.
2. Confirm that the seal file exists.

```bash
cochem init --workspace-dir cochem_project --min-free-bytes 1000000000
cd cochem_project
ls -a
```

The listing must contain `.cochem_project.json`. This file is the SHA-256 seal that `audit` checks in Step 8.

### Step 2: Pre-Flight Integrity Check

The audit confirms that the seal is intact and that every engine binary responds. Resolve any exit code other than 0 with Chapter 6 before continuing.

```bash
cochem audit --target . --check-binaries
echo "audit exit code: $?"
```

### Step 3: Stage Input Structures

Create the three input files used later in the session: water for the quantum run and the rotational spectrum, hydrogen peroxide for the torsional PES, and ethylene glycol as a SMILES string for the conformer search.

```bash
cat > water.xyz <<'EOF'
3
water equilibrium geometry
O   0.000000   0.000000   0.117300
H   0.000000   0.757200  -0.469200
H   0.000000  -0.757200  -0.469200
EOF

cat > h2o2.xyz <<'EOF'
4
hydrogen peroxide seed geometry
O   0.000000   0.737500  -0.052800
O   0.000000  -0.737500  -0.052800
H   0.819000   0.817000   0.422000
H  -0.819000  -0.817000   0.422000
EOF

echo "OCCO" > ethylene_glycol.smi
```

### Step 4: Execute the Quantum Calculation Under Method Matrix Constraints

1. A semiempirical xtb screening run needs `--basis none`, because the Method Matrix forbids a Gaussian basis on this engine.
2. The production run uses a DFT method with a triple-zeta basis on ORCA. Its SCF must converge, otherwise the command exits with code 3.

```bash
cochem run --input water.xyz --engine xtb --method GFN2-xTB --basis none --threads 4 --scratch scratch --output store/screening
cochem run --input water.xyz --engine orca --method wB97M-V --basis def2-TZVP --device auto --threads 8 --scratch scratch --output store --json
```

After the production run, `store/water_opt.xyz` and `store/water_opt.xyz.sha256` exist.

### Step 5: Predict Rotational Spectroscopic Observables

Feed the converged geometry to `spectral-predict`. The dipole of water lies along the b principal axis. The rotational constants derive from the principal moments of inertia, and the catalog lists rotational transitions up to 500 GHz.

```bash
cochem spectral-predict --input store/water_opt.xyz --temperature 300.0 --freq-max 500.0 --out-cat water.cat --dipole 0.0 1.85 0.0
head -n 5 water.cat
```

### Step 6: Map the PES and Solve the DVR Tunneling Problem

The torsion of hydrogen peroxide is defined by atoms 2, 0, 1, 3 (H-O-O-H). A one-dimensional DVR over 72 grid points resolves the tunneling doublets on the potential energy surface.

```bash
cochem pes --input h2o2.xyz --coordinate-index 2 0 1 3 --grid-points 72 --dvr-modes 1 --out-h5 h2o2_torsion.h5
```

### Step 7: Generate Conformers With the Dual-Track Pipeline

The dual-union track merges the ORCA GOAT and CREST ensembles, then prunes by energy window (3.0 kcal/mol), rotational constants (5.0 MHz), and heavy-atom RMSD (0.05 angstrom). Surviving conformers are the ensemble used for Boltzmann population weighting.

```bash
cochem conformer --input ethylene_glycol.smi --engine dual-union --energy-window 3.0 --rmsd-tol 0.05 --rot-tol 5.0
```

### Step 8: Final Integrity Audit

Run the full audit to verify every promoted artifact against its `.sha256` sidecar and to run the anti-spoofing sweep.

```bash
cochem audit --target . --full
echo "final audit exit code: $?"
```

A value of 0 closes the workflow. Any other value is decoded with the table in Chapter 5.

## Chapter 4: Subsystem Guides: Method Matrix, Spectroscopy, PES/DVR, Conformers

### 4.1 Method Matrix Constraints

The Method Matrix (Tiers 0 through 9) assigns each engine and method pair to a tier of cost and accuracy. `run` refuses any combination outside the matrix before a subprocess is spawned and returns exit code 1 with the offending combination in the message.

| Engine | Method Families | Basis Sets | Accelerator | Typical Role |
| --- | --- | --- | --- | --- |
| `orca` | B3LYP, wB97M-V | def2-SVP, def2-TZVP | cpu | DFT geometry optimization and Hessians |
| `xtb` | GFN2-xTB | none | cpu | Fast screening and pre-optimization |
| `cfour` | CCSD(T) | cc-pVTZ | cpu | High-accuracy benchmark energies |
| `mace` | Machine-learned interatomic potential | none | cuda or cpu | Dense scans with a learned potential |

The validation rules are:

1. `xtb` and `mace` require `--basis none`. Any other basis is rejected.
2. `cfour` requires a correlated-consistent basis (cc-pVXZ family).
3. `--device cuda` on an engine build without GPU support is rejected. `--device auto` selects `cuda` only when a device is visible and the engine supports it, and otherwise selects `cpu`.
4. Coupled-cluster methods on large structures should be preceded by an `xtb` or DFT optimization, as in Chapter 3, Step 4.

### 4.2 Rotational Spectroscopy and Pickett Catalogs

`spectral-predict` builds the principal inertia tensor in the Eckart frame, converts moments of inertia to the rotational constants A, B, and C, and computes the Ray asymmetry parameter kappa (Appendix A). It then writes Pickett input decks (`.par` for parameters, `.int` for dipole components) and runs SPCAT to produce the `.cat` catalog. Intensities are Boltzmann-weighted at `--temperature`, and transitions are searched up to `--freq-max`.

Read a catalog line as fixed-width columns: frequency (MHz), uncertainty (MHz), log10 intensity, degrees of freedom of the rotational partition function, lower-state energy (cm^-1), upper-state degeneracy, species tag, and quantum-number format code, followed by the upper and lower quantum numbers.

### 4.3 Active-Learning PES and DVR Tunneling

`pes` proceeds in three stages:

1. A relaxed scan along the coordinate given by `--coordinate-index` places `--grid-points` equidistant points.
2. Active-learning Gaussian-process regression identifies intervals with high predictive variance and adds single points there.
3. The Colbert-Miller DVR eigensolver diagonalizes the kinetic-plus-potential matrix and reports eigenvalues and wavefunctions. The tunneling splitting is the gap between the two lowest members of a doublet.

Use `--dvr-modes 1` for a single torsion or inversion coordinate. Use `--dvr-modes 2` only when two coupled coordinates are essential, because the Hamiltonian grows as the square of the grid size.

### 4.4 Dual-Track Conformer Search

The `dual-union` engine runs two independent explorations and merges them:

- **ORCA GOAT** performs a global optimizer-based search.
- **CREST** performs metadynamics sampling with xtb.

The union is then filtered in this order: energy window (`--energy-window`, default 3.0 kcal/mol), rotational-constant matching (`--rot-tol`, default 5.0 MHz), and heavy-atom quaternion Kabsch RMSD (`--rmsd-tol`, default 0.05 angstrom). Structures that survive are the ensemble used for spectroscopic population weighting.

## Chapter 5: Exit Code Contract

Every subcommand returns exactly one of the following codes. Scripts and schedulers must branch on the number, never on message text.

| Exit Code | Symbol | Trigger Condition | Remediation |
| :---: | --- | --- | --- |
| **0** | `SUCCESS` | Normal clean termination: all computations converged, outputs were validated, and artifacts were sealed with SHA-256 digests. | None required. Continue with the next lifecycle stage. |
| **1** | `GENERAL_CLI_ERROR` | Syntax and validation failure: invalid flag, unrecognized engine, illegal Method Matrix combination, or malformed JSON input. | Re-run with `--help`, correct the argument, and check the Method Matrix in Section 4.1 (see Section 6.8). |
| **2** | `ENVIRONMENT_BLOCK` | A required binary (`orca`, `xtb`, `spcat`, `crest`) is missing from the path, or the storage quota preflight failed. | Load the scheduler module or fix `PATH`, free disk space, then confirm with the binary audit (see Section 6.6). |
| **3** | `SCF_DIVERGENCE` | Electronic structure failure: the self-consistent field iterations did not reach the convergence threshold. | Verify charge and multiplicity, pre-optimize with xtb, and add damping or level shifting (see Section 6.2). |
| **4** | `PHYSICS_ERROR` | Theoretical singularity: collinear or negative inertia tensor, or a DVR potential grid singularity such as a divergent wall. | Repair the geometry or clamp and smooth the potential curve (see Section 6.4). |
| **5** | `RESOURCE_EXHAUSTED` | Hardware exhaustion: GPU CUDA out-of-memory, host RAM limit exceeded, or disk quota overflow. | Switch to `--device cpu`, reduce threads or grid size, and raise the scheduler memory request (see Section 6.5). |
| **6** | `AUDIT_VIOLATION` | Integrity breach: hash mismatch on a sealed file, mock objects, or prohibited stub markers found in audited code. | Restore the sealed file from the store or regenerate it, then re-run the full audit (see Section 6.7). |

### 5.1 Exit Codes Reachable From Each Subcommand

| Subcommand | Reachable Exit Codes | Typical Cause |
| --- | --- | --- |
| `init` | 0, 1, 2 | Existing sealed project or storage quota deficit. |
| `run` | 0, 1, 2, 3, 5 | Method Matrix rejection, missing engine, SCF divergence, or memory exhaustion. |
| `spectral-predict` | 0, 1, 2, 4 | Bad arguments, missing SPCAT, or non-physical inertia tensor. |
| `pes` | 0, 1, 3, 4, 5 | Invalid indices, SCF failure at a scan point, DVR singularity, or memory exhaustion. |
| `conformer` | 0, 1, 2 | Unreadable structure or missing ORCA or CREST binary. |
| `audit` | 0, 2, 6 | Missing binary or integrity and anti-spoofing violation. |

### 5.2 Capturing the Exit Code

To capture the code in a shell, read it immediately after the command:

```bash
cochem run --input water.xyz --engine orca --method B3LYP --basis def2-SVP --json
status=$?
case "${status}" in
  0) echo "converged" ;;
  3) echo "SCF divergence: see Section 6.2" ;;
  5) echo "resource exhaustion: see Section 6.5" ;;
  *) echo "failed with exit code ${status}" ;;
esac
```

In PowerShell:

```powershell
cochem run --input water.xyz --engine xtb --method GFN2-xTB --basis none
if ($LASTEXITCODE -ne 0) { Write-Error "exit code $LASTEXITCODE" }
```

## Chapter 6: Troubleshooting Guides

### 6.1 Triage Procedure

1. Read the numeric exit code and find its row in Chapter 5.
2. Re-run the failing command with `--verbose` to stream engine output.
3. Inspect the newest file in `telemetry/logs/`.
4. Apply the matching guide below.

| Symptom | Exit Code | First Action |
| --- | --- | --- |
| Unknown engine or basis rejected | 1 | Check the Method Matrix table in Section 4.1. |
| Binary not found | 2 | Load the module or fix `PATH`, then run the audit with binary checks. |
| SCF iterations exhausted | 3 | Follow Section 6.2. |
| Empty or malformed catalog | 1 or 4 | Follow Section 6.3. |
| DVR eigensolver fails | 4 | Follow Section 6.4. |
| CUDA out of memory | 5 | Follow Section 6.5. |
| Hash mismatch | 6 | Follow Section 6.7. |

### 6.2 SCF Divergence (Exit Code 3)

Exit code 3 (`SCF_DIVERGENCE`) means the self-consistent field procedure failed to converge within the iteration limit. Typical causes are a poor initial guess, a near-degenerate frontier orbital gap, a wrong spin multiplicity, or a distorted starting geometry.

Diagnose first:

```bash
grep -i "not converged" telemetry/logs/*.log
grep -i "scf" telemetry/logs/*.log
```

Remediation, in order of increasing cost:

1. Confirm the charge and spin multiplicity declared with the structure. A wrong multiplicity is the most common cause.
2. Pre-optimize the geometry with the cheap xtb engine and pass the result forward as a better initial guess.
3. Start with a smaller basis (`def2-SVP`) and step up to `def2-TZVP` after the geometry has relaxed.
4. Add stronger convergence aids to the ORCA method override in `config/methods`: a larger maximum iteration count (maxiter), damping, level-shift, DIIS variants, and second-order convergence (SOSCF or the trust-radius TRAH algorithm).
5. Switch to a more robust functional or to `xtb` for exploratory work.

The re-run sequence that follows steps 2 and 3:

```bash
cochem run --input water.xyz --engine xtb --method GFN2-xTB --basis none --output store/screening
cochem run --input water.xyz --engine orca --method B3LYP --basis def2-SVP --output store
```

An ORCA input fragment that applies the convergence aids of step 4 looks like this:

```text
! B3LYP def2-SVP TightSCF SlowConv KDIIS SOSCF
%scf
  MaxIter 500
  Guess PModel
end
```

If convergence still fails, inspect the last SCF cycles in the log. An energy that oscillates between two values indicates charge sloshing and calls for damping; an energy that drifts steadily indicates a bad initial guess.

### 6.3 Pickett Catalog Errors

Pickett catalog problems appear as an empty `.cat` file, a catalog with implausible intensities, or a failure in SPCAT itself. SPCAT reads a parameter deck (`.par`) and an intensity deck (`.int`) that `spectral-predict` writes for you, so nearly every catalog error traces back to the inputs of those decks. Exit code 2 means the `spcat` binary is missing, exit code 1 means an argument or output path is invalid, and exit code 4 means the inertia tensor is non-physical.

Work through these checks:

1. Confirm that `spcat` is on the path with `cochem audit --target . --check-binaries`.
2. Supply the dipole explicitly with `--dipole` whenever the input is a bare `.xyz` file. An all-zero dipole gives every transition zero intensity, and the catalog comes out empty.
3. Check the search window. The unit of `--freq-max` is GHz, not MHz, and a value that is too small excludes every transition.
4. Check the `.par` parameter file for rotational constants that are positive and ordered A >= B >= C.
5. Check the quantum number format code in the `.cat` output. The upper and lower quantum numbers must have the same count; a mismatch means the `.int` and `.par` decks disagree about the number of quantum numbers.

A verbose re-run with a wider search window and an explicit dipole isolates most catalog problems:

```bash
cochem --verbose spectral-predict --input store/water_opt.xyz --temperature 300.0 --freq-max 1200.0 --out-cat water.cat --dipole 0.0 1.85 0.0
wc -l water.cat
```

A catalog with zero lines after this run points to a geometry problem (repeat the `run` step) or to a failed SPCAT call, whose message is shown in the verbose output.

### 6.4 DVR Singularities (Exit Code 4)

Exit code 4 (`PHYSICS_ERROR`) from `pes` means the DVR Hamiltonian is singular or ill-conditioned. The typical trigger is a potential grid with a spike, an unphysical wall, or a non-finite value produced by a failed single point. Because the DVR kinetic matrix is dense and the potential is diagonal, one huge or NaN value at a single grid point contaminates the whole eigenproblem.

Remediation:

1. Inspect the stored potential curve for spikes or NaN entries with an HDF5 lister, then find the scan points whose single points misbehaved.
2. Increase `--grid-points` so that the spacing resolves the steep part of the wall, and re-run the scan.
3. Clamp the potential with an energy cap: any point far above the classically allowed region should be capped at a finite value so that it acts as a wall without dominating the spectrum.
4. Smooth the curve with the active-learning Gaussian-process fit, which suppresses isolated outliers.
5. Check the seed geometry. A collinear atom quartet in `--coordinate-index` makes the dihedral undefined and gives a singular grid.

```bash
h5ls -r h2o2_torsion.h5
cochem pes --input h2o2.xyz --coordinate-index 2 0 1 3 --grid-points 120 --dvr-modes 1 --out-h5 h2o2_torsion_fine.h5
```

If a single point diverges in the SCF, the scan reports exit code 3 instead. Fix that point with Section 6.2 first, and then repeat the scan. Use `--dvr-modes 1` until the one-dimensional curve is clean, and only then try two modes.

### 6.5 GPU and Host OOM Resource Exhaustion (Exit Code 5)

Exit code 5 (`RESOURCE_EXHAUSTED`) covers CUDA out-of-memory errors on the GPU, host RAM exhaustion (often reported by the scheduler as an out-of-memory kill), and disk quota overflow. Check the log for the strings "CUDA out of memory" or "Killed", and check the scheduler accounting for the peak memory of the job.

Remediation:

1. Move the calculation to the processor with `--device cpu`. This removes the GPU memory limit at the cost of speed.
2. Restrict the visible GPUs with the `CUDA_VISIBLE_DEVICES` environment variable, or hide all of them by setting it to an empty string, so that a second process cannot claim the same device.
3. Reduce the memory footprint: lower the number of `--threads` (each worker holds its own buffers), reduce the batch of points handled at once by shrinking `--grid-points`, or use single precision where the engine allows it.
4. Increase the scheduler memory request (`--mem` in SLURM or `mem=` in PBS), as shown in Chapter 8.
5. Free scratch space if the quota was the cause, and confirm with `cochem audit`.

```bash
export CUDA_VISIBLE_DEVICES=""
cochem run --input water.xyz --engine orca --method wB97M-V --basis def2-TZVP --device cpu --threads 4
```

If the same job still fails on the processor, the molecule and basis set are too large for the node. Reduce the basis (Section 6.2, step 3) or request a node with more memory.

### 6.6 Environment Block: Missing Binary or Quota (Exit Code 2)

Exit code 2 (`ENVIRONMENT_BLOCK`) is raised before any calculation starts. Either an engine binary cannot be found on the path, or the free-space preflight of `init` failed. Load the module that provides the binary (for example ORCA, xtb, CREST, or SPCAT), or add its directory to `PATH`, then verify with the binary audit. For a quota deficit, free disk space or lower the threshold with `--min-free-bytes`.

```bash
cochem audit --target . --check-binaries
cochem init --workspace-dir cochem_project --min-free-bytes 500000000
```

### 6.7 Audit Violations (Exit Code 6)

Exit code 6 (`AUDIT_VIOLATION`) means that a sealed file no longer matches its SHA-256 digest, or that the anti-spoofing sweep found a prohibited construct (mock objects or unfinished-work markers) in audited code. Never edit a sealed file by hand. Restore it from the store or regenerate it with the original command, and then verify the workspace again.

```bash
cochem audit --target . --full
```

If the mismatch persists after regeneration, compare the `.sha256` sidecar with the digest of the file, and check that no process is still writing to the store.

### 6.8 CLI Syntax Errors (Exit Code 1)

Exit code 1 (`GENERAL_CLI_ERROR`) covers malformed command lines and Method Matrix rejections. Read the message: it names the offending flag or the rejected engine, method, and basis combination. The most common causes are a Gaussian basis passed to `xtb` or `mace` (use `--basis none`), a missing mandatory flag, or `--verbose` combined with `--quiet`. Run the subcommand with `--help` to see its flags.

## Chapter 7: Programmatic Python API Tutorials

The tutorials below use the same engines as the CLI. Every snippet reads and writes text as UTF-8, and every subprocess call passes `encoding="utf-8"` and a `creationflags` value that includes `CREATE_NO_WINDOW` on Windows. The constructors of the engine classes evolve with the code base, so the tutorials pass only keyword arguments that the installed class accepts, using a small helper. The helper also prints the public methods of each object, which gives you the current signatures directly from your installation.

### 7.1 Calling the CLI In-Process With `cochem.cli.main`

```python
"""Drive the unified CLI in-process and decode its exit code."""
import sys

from cochem.cli.main import main

EXIT_SYMBOLS = {
    0: "SUCCESS",
    1: "GENERAL_CLI_ERROR",
    2: "ENVIRONMENT_BLOCK",
    3: "SCF_DIVERGENCE",
    4: "PHYSICS_ERROR",
    5: "RESOURCE_EXHAUSTED",
    6: "AUDIT_VIOLATION",
}


def invoke(argv):
    """Call cochem.cli.main.main and always return an integer exit code."""
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


if __name__ == "__main__":
    status = invoke(["audit", "--target", ".", "--check-binaries", "--json"])
    print(f"exit code {status}: {EXIT_SYMBOLS.get(status, 'UNKNOWN')}")
    sys.exit(status)
```

### 7.2 Shared Helper Module

Save this file as `api_helpers.py` next to the tutorial scripts. `accepted_kwargs` keeps only the keyword arguments a class accepts, and `public_api` lists the public callables of an object with their signatures.

```python
"""Helpers shared by the CoChem API tutorials (save as api_helpers.py)."""
import inspect


def accepted_kwargs(factory, **candidates):
    """Keep only the keyword arguments that the factory accepts."""
    parameters = inspect.signature(factory).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return dict(candidates)
    return {name: value for name, value in candidates.items() if name in parameters}


def public_api(instance):
    """Return the public callables of an object with their signatures."""
    listing = {}
    for name in dir(instance):
        if name.startswith("_"):
            continue
        member = getattr(instance, name)
        if not callable(member):
            continue
        try:
            listing[name] = str(inspect.signature(member))
        except (TypeError, ValueError):
            listing[name] = "(signature unavailable)"
    return listing
```

### 7.3 Instantiating the QuantumExecutionRouter

The router applies the Method Matrix and dispatches the engines that `cochem run` uses.

```python
from pathlib import Path

from api_helpers import accepted_kwargs, public_api
from cochem.core_engine.quantum_execution_router import QuantumExecutionRouter

scratch = Path("scratch")
scratch.mkdir(parents=True, exist_ok=True)

router = QuantumExecutionRouter(
    **accepted_kwargs(QuantumExecutionRouter, scratch_dir=scratch, device="auto", threads=4)
)

listing = [f"{name}{signature}" for name, signature in sorted(public_api(router).items())]
report = Path("router_api.txt")
report.write_text("\n".join(listing) + "\n", encoding="utf-8")
print(report.read_text(encoding="utf-8"))
```

### 7.4 Rotational Observables With the SpectroscopicEngine

The engine computes the observables that `cochem spectral-predict` exports. The helper below also parses the fixed-width columns of the Pickett catalog that the CLI writes: frequency (13 characters), uncertainty (8), and log10 intensity (8).

```python
from pathlib import Path

from api_helpers import accepted_kwargs, public_api
from cochem.spectroscopy.rotational_observables import SpectroscopicEngine


def read_catalog(path):
    """Parse the leading fixed-width columns of a Pickett .cat file."""
    transitions = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            transitions.append(
                {
                    "frequency_mhz": float(line[0:13]),
                    "uncertainty_mhz": float(line[13:21]),
                    "log10_intensity": float(line[21:29]),
                }
            )
        except ValueError:
            continue
    return transitions


engine = SpectroscopicEngine(
    **accepted_kwargs(SpectroscopicEngine, temperature=300.0, freq_max=500.0)
)
for name, signature in sorted(public_api(engine).items()):
    print(f"{name}{signature}")

catalog = Path("water.cat")
if catalog.is_file():
    ranked = sorted(read_catalog(catalog), key=lambda t: t["log10_intensity"], reverse=True)
    for transition in ranked[:5]:
        print(transition)
```

### 7.5 Dual-Track Conformers With the ConformerUnionPipeline

```python
from pathlib import Path

from api_helpers import accepted_kwargs, public_api
from cochem.topos.conformer_ensemble import ConformerUnionPipeline

structure = Path("ethylene_glycol.smi")
structure.write_text("OCCO\n", encoding="utf-8")

pipeline = ConformerUnionPipeline(
    **accepted_kwargs(
        ConformerUnionPipeline,
        energy_window=3.0,
        rmsd_tol=0.05,
        rot_tol=5.0,
    )
)

for name, signature in sorted(public_api(pipeline).items()):
    print(f"{name}{signature}")
print(f"input structure: {structure.read_text(encoding='utf-8').strip()}")
```

### 7.6 One-Dimensional DVR With the DVRSolver

Masses always come from the `mendeleev` package. The example builds the reduced mass of an H-H pair and a harmonic well on a 72-point grid, then inspects the solver interface.

```python
import json
from pathlib import Path

import numpy as np
from mendeleev import element

from api_helpers import accepted_kwargs, public_api
from cochem_base.core_engine.cochem_core_dvr_solver import DVR1DSolver

mass_h = element("H").mass
reduced_mass = mass_h * mass_h / (mass_h + mass_h)

grid = np.linspace(-1.0, 1.0, 72)
potential = 0.5 * grid**2

solver = DVR1DSolver(
    **accepted_kwargs(
        DVR1DSolver,
        grid=grid,
        potential=potential,
        reduced_mass=reduced_mass,
        n_points=grid.size,
    )
)

summary = {
    "grid_points": int(grid.size),
    "reduced_mass_amu": float(reduced_mass),
    "solver_api": public_api(solver),
}
out = Path("dvr_summary.json")
out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(out.read_text(encoding="utf-8"))
```

### 7.7 Safe Subprocess Broker

Use this pattern whenever Python has to call the `cochem` executable as a child process.

```python
import json
import subprocess
from pathlib import Path


def run_cochem(arguments, workdir):
    """Run cochem with UTF-8 decoding and no console window on Windows."""
    completed = subprocess.run(
        ["cochem", "--json", *arguments],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    envelope = {}
    if completed.stdout.strip():
        envelope = json.loads(completed.stdout)
    return completed.returncode, envelope, completed.stderr


if __name__ == "__main__":
    code, envelope, diagnostics = run_cochem(["audit", "--target", "."], ".")
    Path("audit_envelope.json").write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    print(f"exit code {code}")
    print(diagnostics)
```

### 7.8 Lifecycle Driver With Exit Code Propagation

This script runs `init`, `run`, `spectral-predict`, and `audit` in order and stops at the first non-zero exit code, which it returns to its caller.

```python
import subprocess
import sys
from pathlib import Path

WATER_XYZ = """3
water equilibrium geometry
O   0.000000   0.000000   0.117300
H   0.000000   0.757200  -0.469200
H   0.000000  -0.757200  -0.469200
"""


def cochem(arguments, cwd):
    """Run one cochem subcommand and return the completed process."""
    return subprocess.run(
        ["cochem", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def lifecycle():
    """Execute the reference lifecycle and return the first failing exit code."""
    workspace = Path("cochem_project")
    log_lines = []

    first = cochem(["init", "--workspace-dir", "cochem_project", "--min-free-bytes", "1000000000"], ".")
    log_lines.append(f"init -> {first.returncode}")
    if first.returncode != 0:
        return first.returncode, log_lines

    (workspace / "water.xyz").write_text(WATER_XYZ, encoding="utf-8")
    steps = [
        ["run", "--input", "water.xyz", "--engine", "xtb", "--method", "GFN2-xTB", "--basis", "none", "--output", "store"],
        ["spectral-predict", "--input", "store/water_opt.xyz", "--temperature", "300.0", "--freq-max", "500.0", "--out-cat", "water.cat", "--dipole", "0.0", "1.85", "0.0"],
        ["audit", "--target", ".", "--full"],
    ]
    for step in steps:
        result = cochem(step, workspace)
        log_lines.append(f"{step[0]} -> {result.returncode}")
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            return result.returncode, log_lines
    return 0, log_lines


if __name__ == "__main__":
    status, log_lines = lifecycle()
    Path("lifecycle.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    sys.exit(status)
```

## Chapter 8: HPC Cluster Deployment and Batch Submission

### 8.1 Design Rules for Batch Jobs

1. **Directives first.** Schedulers stop reading `#SBATCH` and `#PBS` directives at the first executable line, so every directive precedes every command.
2. **Match threads to cores.** Pass the allocated core count to `--threads`, and request enough memory to avoid exit code 5 (Section 6.5).
3. **Stage on node-local scratch.** Copy inputs to a directory keyed on the job identifier, run there, and copy the promoted artifacts back at the end.
4. **Always clean up.** Install a `trap` that removes the scratch directory on every exit path, including scheduler kills.
5. **Propagate the exit code.** Capture the status of `cochem run` immediately, and finish the script with that value so that the scheduler records the real outcome (Chapter 5).

### 8.2 SLURM Batch Script

Save as `cochem_run.slurm` next to `water.xyz`.

```bash
#!/bin/bash
#SBATCH --job-name=cochem_run
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --output=cochem_%j.out
#SBATCH --error=cochem_%j.err

# Load the site modules that provide ORCA and xtb here, for example: module load orca

INPUT_FILE="water.xyz"
SCRATCH_DIR="${TMPDIR:-/tmp}/cochem_${SLURM_JOB_ID}"
RESULT_DIR="${SLURM_SUBMIT_DIR}/results_${SLURM_JOB_ID}"

trap 'rm -rf "${SCRATCH_DIR}"' EXIT

mkdir -p "${SCRATCH_DIR}/store" "${SCRATCH_DIR}/scratch" "${RESULT_DIR}"
cp "${SLURM_SUBMIT_DIR}/${INPUT_FILE}" "${SCRATCH_DIR}/"
cd "${SCRATCH_DIR}"

cochem run --input "${INPUT_FILE}" --engine orca --method B3LYP --basis def2-SVP --device cpu --threads "${SLURM_CPUS_PER_TASK}" --scratch "${SCRATCH_DIR}/scratch" --output "${SCRATCH_DIR}/store" --json
RC=$?

cp -r "${SCRATCH_DIR}/store/." "${RESULT_DIR}/"
if [ "${RC}" -ne 0 ]; then
    echo "cochem failed with exit code ${RC}; see Chapter 5 of the manual" >&2
fi
exit "${RC}"
```

Submit and inspect the recorded outcome:

```bash
sbatch cochem_run.slurm
sacct --format=JobID,JobName,State,ExitCode,MaxRSS
```

### 8.3 PBS Batch Script

Save as `cochem_run.pbs` next to `water.xyz`.

```bash
#!/bin/bash
#PBS -N cochem_run
#PBS -l walltime=24:00:00
#PBS -l select=1:ncpus=16:mem=64gb
#PBS -j oe
#PBS -o cochem_run.log

cd "${PBS_O_WORKDIR}"

# Load the site modules that provide ORCA and xtb here, for example: module load orca

INPUT_FILE="water.xyz"
NCPUS=16
SCRATCH_DIR="${TMPDIR:-/tmp}/cochem_${PBS_JOBID}"
RESULT_DIR="${PBS_O_WORKDIR}/results_${PBS_JOBID}"

trap 'rm -rf "${SCRATCH_DIR}"' EXIT

mkdir -p "${SCRATCH_DIR}/store" "${SCRATCH_DIR}/scratch" "${RESULT_DIR}"
cp "${PBS_O_WORKDIR}/${INPUT_FILE}" "${SCRATCH_DIR}/"
cd "${SCRATCH_DIR}"

cochem run --input "${INPUT_FILE}" --engine orca --method B3LYP --basis def2-SVP --device cpu --threads "${NCPUS}" --scratch "${SCRATCH_DIR}/scratch" --output "${SCRATCH_DIR}/store" --json
RC=$?

cp -r "${SCRATCH_DIR}/store/." "${RESULT_DIR}/"
if [ "${RC}" -ne 0 ]; then
    echo "cochem failed with exit code ${RC}; see Chapter 5 of the manual" >&2
fi
exit "${RC}"
```

Submit and follow the job:

```bash
qsub cochem_run.pbs
qstat -x
```

### 8.4 Reading Scheduler Outcomes

Both scripts finish with `exit "${RC}"`, so the exit code recorded by the scheduler is the CoChem exit code. A recorded value of 3 is an SCF divergence (Section 6.2), 5 is resource exhaustion (Section 6.5), and 2 means the module providing a binary was not loaded (Section 6.6). A job killed by the scheduler for exceeding walltime or memory never reaches the `exit` line. In that case the `trap` still removes the scratch directory, and the scheduler accounting shows the kill reason. Raise the corresponding request and resubmit.

## Appendix A: Theoretical Foundations

**Born-Oppenheimer approximation.** Nuclei are treated as much slower than electrons, so the electronic Schroedinger equation is solved at fixed nuclear positions. The eigenvalue as a function of geometry is the potential energy surface used by `pes`.

**Self-consistent field.** Hartree-Fock and Kohn-Sham DFT solve a set of coupled one-electron equations iteratively. The Fock or Kohn-Sham operator depends on the orbitals it produces, so the procedure is repeated until the energy and density change fall below a threshold. Failure to reach that threshold is exit code 3.

**Moments of inertia and rotational constants.** The inertia tensor is I_ab = sum over atoms of m_i (r_i^2 delta_ab - r_ia r_ib). Its eigenvalues are the principal moments I_A <= I_B <= I_C. The rotational constants are A = h / (8 pi^2 I_A) and likewise for B and C. A collinear or degenerate mass distribution makes one moment vanish, which is the non-physical inertia tensor behind exit code 4 in `spectral-predict`.

**Ray asymmetry parameter.** kappa = (2B - A - C) / (A - C). It equals -1 for a prolate symmetric top and +1 for an oblate symmetric top, and it classifies the asymmetric-top rigid-rotor spectrum in between.

**Boltzmann populations and intensities.** The population of a level with energy E is proportional to g exp(-E / kT) divided by the rotational partition function. Line intensities scale with the population difference, the squared dipole component along the transition axis, and the frequency.

**Discrete variable representation.** On a uniform grid of spacing dx, the Colbert-Miller kinetic matrix for a coordinate with reduced mass mu has diagonal elements hbar^2 pi^2 / (6 mu dx^2) and off-diagonal elements (-1)^(i-j) hbar^2 / (mu dx^2 (i-j)^2). The potential is diagonal. Diagonalizing the sum yields the vibrational eigenvalues, and the gap between the two lowest members of a doublet is the tunneling splitting. The reduced mass is always built from `mendeleev` atomic masses.

**Conformational ensembles.** Conformers within an energy window of the global minimum contribute to the thermal population. Two structures are duplicates when their rotational constants agree within `--rot-tol` and the heavy-atom RMSD, computed after optimal superposition with the quaternion Kabsch algorithm, is below `--rmsd-tol`.

## Appendix B: Integrity Auditing and Anti-Spoofing Rules

CoChem results are only trustworthy when they come from real computation. `cochem audit --full` enforces the following rules, and any violation returns exit code 6.

1. **Sealed scaffold.** The digests in `.cochem_project.json` must match the scaffold files on disk.
2. **Sidecar digests.** Every promoted artifact has a `.sha256` sidecar that must match the artifact.
3. **No mock objects.** Audited code must not import or construct mock or fake substitutes for engines, solvers, or catalogs.
4. **No unfinished markers.** Audited code must not contain empty function bodies, bare `pass` statements, ellipsis bodies, or raise statements that declare a feature as not implemented.
5. **No hard-coded expected outputs.** Results must be produced by the engines. Test harnesses must not be special-cased.
6. **No hand-typed atomic masses.** Masses come from `mendeleev`, for example `element("O").mass`.
7. **Explicit encodings.** Text files are opened with `encoding="utf-8"`, and subprocess calls pass `encoding="utf-8"` and `creationflags` with `CREATE_NO_WINDOW`.

Run the audit as the last step of every workflow, as in Chapter 3, Step 8, and in every batch job that produces artifacts intended for publication.
