# Context Dossier: Task 1.01 — Directory Dropzone & Test Support Scaffold

**Role:** P2 RESEARCHER (`cochem-researcher`)  
**Target Root:** `D:/__CoChem`  
**Subsystem:** Pipeline v2 Phase 1 Infrastructure (`__agentic/v2`)  
**Specification Reference:** [`SRS-PV2-20260922T213500-PRIORITY-1-PIPELINE-V2-PHASE.md`](file:///D:/__CoChem/__agentic/dropzones/inbox_code/SRS-PV2-20260922T213500-PRIORITY-1-PIPELINE-V2-PHASE.md#L1-L100)  
**Task Identifier:** `IMPLEMENTATION TASK 1.01` (Complexity: 1/5)  
**Target Files:**  
- [`D:/__CoChem/__agentic/v2/data/.gitkeep`](file:///D:/__CoChem/__agentic/v2/data/.gitkeep)  
- [`D:/__CoChem/__agentic/v2/tests/__init__.py`](file:///D:/__CoChem/__agentic/v2/tests/__init__.py)  
**Test File:**  
- [`D:/__CoChem/__agentic/v2/tests/test_phase1_infrastructure.py`](file:///D:/__CoChem/__agentic/v2/tests/test_phase1_infrastructure.py)

---

## 1. Executive Summary & Objective

Task 1.01 establishes the foundational directory layout and test harness packaging for Phase 1 of CoChem Pipeline v2. 

The objectives are strictly bounded:
1. **Host Mount Bind Anchor (`AC-10`, `FR-2.8`):** Create physical anchor file [`D:/__CoChem/__agentic/v2/data/.gitkeep`](file:///D:/__CoChem/__agentic/v2/data/.gitkeep). This guarantees that the host mount directory exists on the Windows filesystem before Docker Compose initializes the named volume `cochem-data` using `driver_opts` (`type: none`, `o: bind`, `device: .../__agentic/v2/data`).
2. **Python Test Package Marker (`AC-10`, `FR-6.1`):** Create [`D:/__CoChem/__agentic/v2/tests/__init__.py`](file:///D:/__CoChem/__agentic/v2/tests/__init__.py) to demarcate `__agentic.v2.tests` as an importable, structured package for the upcoming offline pytest test suite.
3. **Strict Zero-Write Isolation (`INV-4`, `AC-29`):** Ensure zero modifications occur under legacy v1 assets, specifically [`D:/__CoChem/.scripts`](file:///D:/__CoChem/.scripts) and [`D:/__CoChem/cochem_kanban.db`](file:///D:/__CoChem/cochem_kanban.db).

**Out of Scope:**
- Creating or editing `Dockerfile`, `docker-compose.yml`, `schema_v2.sql`, or `init_db.py`.
- Creating `conftest.py` (reserved for Task 1.02).
- Modifying legacy scripts under `.scripts/` or mutating `cochem_kanban.db`.
- Docker builds, network requests, or container daemon operations.

---

## 2. Existing Code & Codebase Patterns to Reuse

### 2.1 Subprocess Execution Standard
Across the repository (e.g., [`CoChem-BENCH/tests/test_subprocess_reaper.py`](file:///D:/__CoChem/GitHub-Repo/CoChem-BENCH/tests/test_subprocess_reaper.py#L276) and [`CoChem-TORQ/UI/cochem_torq_controller.py`](file:///D:/__CoChem/GitHub-Repo/CoChem-TORQ/UI/cochem_torq_controller.py#L196)), subprocess invocations on Windows must suppress console windows and enforce UTF-8:
```python
import subprocess

proc = subprocess.run(
    args,
    cwd=working_dir,
    capture_output=True,
    text=True,
    encoding="utf-8",
    creationflags=subprocess.CREATE_NO_WINDOW  # 0x08000000
)
```

### 2.2 Filesystem Inspection & Stat Pattern
Verification of physical file existence and timestamps follows standard `pathlib.Path` and `os.stat` idioms without mocks:
```python
from pathlib import Path

target = Path(r"D:\__CoChem\__agentic\v2\data\.gitkeep")
assert target.is_file(), f"File {target} does not exist as a regular file"
assert target.stat().st_size >= 0
```

### 2.3 Pytest Configuration
The root configuration is defined in [`D:/__CoChem/pytest.ini`](file:///D:/__CoChem/pytest.ini). Tests are executed under host interpreter `Python 3.14.7` with `pytest 9.1.1`. To avoid triggering zero-trust quarantine assertions present in sibling sub-repositories (such as [`CoChem-BENCH/conftest.py`](file:///D:/__CoChem/GitHub-Repo/CoChem-BENCH/conftest.py#L4)), the test runner targets the specific test file directly:
```bash
python -m pytest "D:\__CoChem\__agentic\v2\tests\test_phase1_infrastructure.py" -v
```

---

## 3. Exact APIs, Signatures & Specifications

### 3.1 Target Files

#### `D:\__CoChem\__agentic\v2\data\.gitkeep`
- **Path:** `D:\__CoChem\__agentic\v2\data\.gitkeep`
- **File Type:** Regular file (UTF-8 text).
- **Contents:** Comment string describing volume anchor purpose, e.g.:
  ```text
  # Host mount bind anchor for Docker volume cochem-data (Pipeline v2 Phase 1)
  ```
- **Encoding:** UTF-8 without BOM.

#### `D:\__CoChem\__agentic\v2\tests\__init__.py`
- **Path:** `D:\__CoChem\__agentic\v2\tests\__init__.py`
- **File Type:** Regular Python source file (UTF-8 text).
- **Contents:** Clean module docstring (avoid empty `pass` blocks per anti-spoofing rules):
  ```python
  """Pipeline v2 Phase 1 Infrastructure Test Package."""
  ```
- **Encoding:** UTF-8 without BOM.

### 3.2 Test Specifications (`test_phase1_infrastructure.py`)

The test suite must implement two test functions corresponding to `AC-10` / `AC1` and `AC-29` / `AC2`:

```python
def test_directory_scaffold_exists() -> None:
    """[T1] Verifies that __agentic/v2/data/.gitkeep and __agentic/v2/tests/__init__.py
    physically exist on disk, are regular files, and are readable (AC-10, FR-2.8)."""

def test_v1_isolation_guard() -> None:
    """[T2] Verifies zero-write isolation over legacy v1 assets:
    cochem_kanban.db mtime has not been altered, and .scripts/ has zero modifications
    (AC-29, INV-4)."""
```

#### Observable Assertion Signatures
1. **`test_directory_scaffold_exists`:**
   - `gitkeep_path = Path(r"D:\__CoChem\__agentic\v2\data\.gitkeep")`
   - `init_path = Path(r"D:\__CoChem\__agentic\v2\tests\__init__.py")`
   - Assert `gitkeep_path.is_file()` is `True`.
   - Assert `init_path.is_file()` is `True`.
   - Assert reading `gitkeep_path.read_text(encoding="utf-8")` succeeds without `OSError` or `UnicodeDecodeError`.
   - Assert reading `init_path.read_text(encoding="utf-8")` succeeds without `OSError` or `UnicodeDecodeError`.

2. **`test_v1_isolation_guard`:**
   - Target database: [`D:/__CoChem/cochem_kanban.db`](file:///D:/__CoChem/cochem_kanban.db).
   - Target directory: [`D:/__CoChem/.scripts`](file:///D:/__CoChem/.scripts).
   - Check `cochem_kanban.db` existence via `Path.is_file()` and inspect `stat().st_mtime`. Verify `st_mtime` matches the recorded pre-execution baseline (currently `2026-09-23 05:14:59` / timestamp `1790158499.x`).
   - Check `.scripts` directory: verify zero files modified or added. If git is available in the working directory, execute `git status --porcelain .scripts` and assert output is empty string `""`. If `D:\__CoChem` is not a standalone git repository root, fall back to a physical recursive file mtime / SHA-256 baseline comparison over `.scripts/prompts/` ensuring zero changes.

---

## 4. System Constraints & Invariants

1. **Anti-Spoofing Protocol v2 & v4 Enforcement:**
   - **Zero Mocks/Stubs:** `unittest.mock`, `MagicMock`, `pytest.monkeypatch` (on subprocess or filesystem), `NotImplementedError`, and empty `pass` bodies are strictly forbidden.
   - **No Silent Skips:** `pytest.skip` and `pytest.mark.skip` are strictly forbidden. Core tests must execute against physical filesystem artifacts.
   - **TDD Red-Green Cycle:** Tests must be authored and executed *before* creating target files to confirm the test fails initially (`must_fail_before_impl: true`).
2. **Subprocess Window Prevention (Rule 15):**
   - Every `subprocess.run` or `subprocess.Popen` on Windows must pass `creationflags=subprocess.CREATE_NO_WINDOW` (or `0x08000000`). Never pop visible terminal windows.
3. **Encoding & Line Endings:**
   - All files must be read and written using `encoding="utf-8"` with Unix LF line endings.
4. **Read-Only SQLite Rule:**
   - Never open `cochem_kanban.db` via `sqlite3.connect()` without read-only mode (`mode=ro`). Standard connection attempts open write handles and alter SQLite file headers and `mtime`, violating `INV-4`.

---

## 5. Critical Pitfalls & Mitigation Strategies

| Pitfall | Root Cause | Impact | Mitigation Strategy |
|---|---|---|---|
| **Git Non-Repository Error (Code 128)** | `D:\__CoChem` root lacks a `.git` folder (`D:\__agentic\.git` and `D:\__CoChem\GitHub-Repo\*` are git repos, but `D:\__CoChem` itself is not). | `git status --porcelain .scripts` throws `fatal: not a git repository`. | In `test_v1_isolation_guard`, verify whether `(Path("D:/__CoChem") / ".git").exists()`. If true, check `git status`. Otherwise, verify isolation via filesystem stat/hash inspection of all files in `D:\__CoChem\.scripts`. |
| **Accidental SQLite Header Mutation** | Connecting to `cochem_kanban.db` with default `sqlite3.connect("D:/__CoChem/cochem_kanban.db")`. | Modifies `cochem_kanban.db` `st_mtime`, violating `AC-29`. | Use only `Path.stat().st_mtime` to check modification times. Do not initiate SQLite connections against the v1 database. |
| **TDD RED Phase Bypass** | Creating target files before authoring and executing the test file. | Violates TDD contract (`must_fail_before_impl: true`). | 1. Create `test_phase1_infrastructure.py`.<br/>2. Execute `pytest` (assert fail on missing `.gitkeep` & `__init__.py`).<br/>3. Create `.gitkeep` and `__init__.py`.<br/>4. Execute `pytest` (assert pass). |
| **Directory Creation Race in Tests** | Writing `test_phase1_infrastructure.py` into `__agentic/v2/tests/` automatically creates the directory, potentially masking missing directory tests. | `tests/` directory exists, but `tests/__init__.py` does not. | Ensure test asserts `(tests_dir / "__init__.py").is_file()`, not just `tests_dir.exists()`. |
| **Linter Rejection for Stubs/Placeholders** | Leaving `__init__.py` as `pass` or empty placeholder comment like `# TODO`. | Anti-spoofing scanner rejects deliverable. | Write a meaningful docstring `"""Pipeline v2 Phase 1 Infrastructure Test Package."""` in `__init__.py`. |

---

## 6. Implementation & Verification Roadmap for Engineer

1. **Step 1 — Author Test Suite (RED Phase):**
   - Create parent directory `D:\__CoChem\__agentic\v2\tests` if absent.
   - Author [`D:/__CoChem/__agentic/v2/tests/test_phase1_infrastructure.py`](file:///D:/__CoChem/__agentic/v2/tests/test_phase1_infrastructure.py) containing `test_directory_scaffold_exists` and `test_v1_isolation_guard`.
   - Run `python -m pytest "D:\__CoChem\__agentic\v2\tests\test_phase1_infrastructure.py" -v`.
   - **Expected Result:** `test_directory_scaffold_exists` FAILS (`FileNotFoundError` or assertion failure on `data/.gitkeep` and `tests/__init__.py`), while `test_v1_isolation_guard` PASSES.

2. **Step 2 — Implement Physical Deliverables (GREEN Phase):**
   - Create directory `D:\__CoChem\__agentic\v2\data`.
   - Write [`D:/__CoChem/__agentic/v2/data/.gitkeep`](file:///D:/__CoChem/__agentic/v2/data/.gitkeep) with volume anchor comment.
   - Write [`D:/__CoChem/__agentic/v2/tests/__init__.py`](file:///D:/__CoChem/__agentic/v2/tests/__init__.py) with package docstring.

3. **Step 3 — Re-run Test Suite (GREEN Verification):**
   - Run `python -m pytest "D:\__CoChem\__agentic\v2\tests\test_phase1_infrastructure.py" -v`.
   - **Expected Result:** Both tests PASS with 0 failures, 0 errors, 0 skipped.

4. **Step 4 — Collect Evidence Bundle:**
   - Capture `pytest` execution stdout.
   - Verify `git status` or directory stat showing no modifications in `.scripts/`.
   - Verify `cochem_kanban.db` `st_mtime` equals baseline `1790158499` (`2026-09-23 05:14:59`).