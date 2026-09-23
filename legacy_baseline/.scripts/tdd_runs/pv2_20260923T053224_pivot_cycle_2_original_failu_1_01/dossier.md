# Context Dossier: Task 1.01 — Persistence & Test Package Initialization Markers

**Card:** Card 1.1 (Persistence Core — PS/PR Protocol)  
**Run Identifier:** `pv2-p1-card-1-1-persistence-pspr`  
**Task ID:** `IMPLEMENTATION TASK 1.01`  
**Target Codebase:** [`D:/__CoChem`](file:///D:/__CoChem)  
**Target Files:** [`persistence/__init__.py`](file:///D:/__CoChem/persistence/__init__.py), [`tests/card_1_1/__init__.py`](file:///D:/__CoChem/tests/card_1_1/__init__.py)  
**Role:** P2 Researcher (`cochem-researcher`)

---

## 1. Executive Summary & Objective

The objective of Task 1.01 is to establish explicit Python package boundaries for the persistence layer and the Card 1.1 test suite by creating two empty module marker files:
1. [`persistence/__init__.py`](file:///D:/__CoChem/persistence/__init__.py)
2. [`tests/card_1_1/__init__.py`](file:///D:/__CoChem/tests/card_1_1/__init__.py)

Under Python 3.3+ (PEP 420), directories lacking `__init__.py` default to implicit namespace packages. Explicit package markers are strictly required across [`D:/__CoChem`](file:///D:/__CoChem) to:
- Guarantee deterministic module resolution for relative and absolute imports (e.g., `import persistence`, `from tests.card_1_1 import host_facts`).
- Enforce explicit `__path__` binding on package namespaces.
- Satisfy **AC-20 (Scope Purity Gate)** as items 3 and 6 of the closed 16-file scope defined in [`brief_card_1_1.md`](file:///D:/__CoChem/__agentic/v2/brief_card_1_1.md) and [`SRS-PV2-20260923T053224-PIVOT-CYCLE-2-ORIGINAL-FAILU.md`](file:///D:/__CoChem/__agentic/dropzones/inbox_code/SRS-PV2-20260923T053224-PIVOT-CYCLE-2-ORIGINAL-FAILU.md).

These files must contain **exactly 0 bytes** on disk. No docstrings, code, comments, or imports are permitted.

---

## 2. Existing Code & Architectural Patterns to Reuse

### 2.1 Scope Boundary Reference
The closed 16-file scope defined in [`brief_card_1_1.md`](file:///D:/__CoChem/__agentic/v2/brief_card_1_1.md) restricts filesystem modifications to:
```text
1. persistence/schema_v2.sql               9. tests/card_1_1/test_hard_1_fresh_init.py
2. persistence/init_db.py                 10. tests/card_1_1/test_hard_2_no_touch_states.py
3. persistence/__init__.py                11. tests/card_1_1/test_hard_3_wal_unsupported.py
4. __agentic/v2/spikes/card_1_1_host_spike.py 12. tests/card_1_1/test_hard_4_schema_apply_atomic.py
5. __agentic/v2/spikes/sqlite_host_facts.json 13. tests/card_1_1/test_soft_schema_semantics.py
6. tests/card_1_1/__init__.py             14. tests/card_1_1/test_soft_harness_hygiene.py
7. tests/card_1_1/host_facts.py           15. .docs/cards/CARD_1_1.md
8. tests/card_1_1/conftest.py             16. .gitignore
```
Task 1.01 allocates **strictly items 3 and 6**. All other files remain out of scope for this task.

### 2.2 Parent Directory Creation Pattern
Both target files require parent directory hierarchy instantiation if directories do not yet exist:
- [`D:/__CoChem/persistence`](file:///D:/__CoChem/persistence)
- [`D:/__CoChem/tests/card_1_1`](file:///D:/__CoChem/tests/card_1_1)

Standard pattern to adopt:
```python
from pathlib import Path

target = Path(r"D:\__CoChem\persistence\__init__.py")
target.parent.mkdir(parents=True, exist_ok=True)
```

### 2.3 Zero-Byte File Creation Pattern
Files must be instantiated in binary mode without emitting UTF-8 byte order marks (BOM `0xEF, 0xBB, 0xBF`), trailing newlines (`\n` or `\r\n`), or whitespace:
```python
with open(target, "wb") as f:
    pass  # writing 0 bytes
```
Alternatively:
```python
target.touch(exist_ok=True)
# If target existed previously with content, truncate:
target.write_bytes(b"")
```

---

## 3. Exact APIs and Signatures Involved

### 3.1 Filesystem & Inspection APIs
* **[`pathlib.Path`](file:///D:/__CoChem)**:
  - `Path.mkdir(parents: bool = False, exist_ok: bool = False) -> None`: Recursively creates directories. Must use `parents=True, exist_ok=True`.
  - `Path.is_file() -> bool`: Returns `True` if destination is a regular file.
  - `Path.stat() -> os.stat_result`: Retrieves `st_size` (must equal `0`).
  - `Path.write_bytes(data: bytes) -> int`: Atomic byte write. Passing `b""` guarantees 0-byte size.
* **[`os.path`](file:///D:/__CoChem)**:
  - `os.path.isfile(path: str | bytes | os.PathLike) -> bool`
  - `os.path.getsize(path: str | bytes | os.PathLike) -> int`: Returns file length in bytes.
* **[`importlib`](file:///D:/__CoChem)**:
  - `importlib.invalidate_caches() -> None`: Clears internal path finders (`sys.path_importer_cache`). Must be called after creating marker files if Python was already running.
  - `importlib.import_module(name: str, package: str | None = None) -> types.ModuleType`: Loads module.
    - `importlib.import_module("persistence")`
    - `importlib.import_module("tests.card_1_1")`
  - Validation signature:
    - `hasattr(module, "__path__")` must be `True` (indicates a package rather than a leaf module).
    - `module.__file__` must resolve to the physical path of the `__init__.py` file.

### 3.2 Git Subprocess API
For verifying **AC-20 (Scope Purity)** and zero diff on [`brief_card_1_1.md`](file:///D:/__CoChem/__agentic/v2/brief_card_1_1.md):
* **[`subprocess.run`](file:///D:/__CoChem)**:
  ```python
  import subprocess

  proc = subprocess.run(
      ["git", "status", "--porcelain"],
      cwd=r"D:\__CoChem",
      capture_output=True,
      text=True,
      encoding="utf-8",
      creationflags=subprocess.CREATE_NO_WINDOW  # 0x08000000
  )
  ```
* **Git diff check on immutable specification**:
  ```python
  proc_diff = subprocess.run(
      ["git", "diff", "--exit-code", "__agentic/v2/brief_card_1_1.md"],
      cwd=r"D:\__CoChem",
      capture_output=True,
      creationflags=subprocess.CREATE_NO_WINDOW
  )
  assert proc_diff.returncode == 0
  ```

---

## 4. Constraints

### 4.1 CoChem Swarm Invariants
1. **Zero Mocks & Stubs (`RULE[cochem-anti-spoofing-v4.md]`):** All checks must run against real physical filesystem state on `D:\`. Synthetic monkeypatching (`pytest.monkeypatch`, `unittest.mock`) is forbidden.
2. **Subprocess Window Suppression:** Every subprocess call on Windows **MUST** pass `creationflags=subprocess.CREATE_NO_WINDOW` (`0x08000000`) and `encoding='utf-8'`.
3. **Encoding & Text Files:** Open text files strictly with `encoding='utf-8'`. For 0-byte markers, opening in binary write mode (`"wb"`) ensures no line terminator or encoding header is introduced.
4. **Scope Purity (AC-20 / G-05):**
   - Untracked or modified files must strictly be confined to the closed 16-file list.
   - For Task 1.01, only `persistence/__init__.py` and `tests/card_1_1/__init__.py` may be touched or created.
   - [`__agentic/v2/brief_card_1_1.md`](file:///D:/__CoChem/__agentic/v2/brief_card_1_1.md) is immutable; any modification causes an immediate audit abort.

### 4.2 Acceptance Criteria Mapping
* **[AC1] Physical File Presence & 0-Byte Size:**
  - Files exist as regular filesystem entries.
  - `os.path.getsize(path) == 0`.
* **[AC2] Importability & Namespace Verification:**
  - `importlib.import_module('persistence')` succeeds without `ImportError`.
  - `importlib.import_module('tests.card_1_1')` succeeds without `ImportError`.
  - Both modules exhibit `__path__` attribute.
* **[AC3] Git Scope Purity:**
  - `git status --porcelain` reveals no files modified or untracked outside items 3 and 6 (and runtime cache artifacts excluded by gitignore).
  - `git diff --exit-code __agentic/v2/brief_card_1_1.md` returns code `0`.

---

## 5. Critical Pitfalls & Mitigation Strategies

| Pitfall | Risk / Manifestation | Mitigation Strategy |
|:---|:---|:---|
| **1. Accidental BOM or Newline Injection** | Using `print("", file=f)` or `encoding="utf-8-sig"` writes 1 to 3 bytes (`\r\n` or `\xef\xbb\xbf`). Fails `st_size == 0` check. | Open in `"wb"` mode and write `b""` or call `pathlib.Path.touch()`. Never write string literals. |
| **2. Module Finder Cache Stale State** | `importlib.import_module` raises `ModuleNotFoundError` if Python runtime cached directory tree prior to marker creation. | Invoke `importlib.invalidate_caches()` immediately prior to package import assertions in test runners. |
| **3. `sys.path` Root Omission** | Python cannot resolve top-level `persistence` or `tests.card_1_1` if [`D:\__CoChem`](file:///D:/__CoChem) is absent from `sys.path`. | Ensure the test harness or execution script prepends `str(Path(r"D:\__CoChem").resolve())` to `sys.path` if not already present. |
| **4. Git Porcelain Directory Collapsing** | `git status --porcelain` may report untracked directories as `?? persistence/` rather than individual files if `-uall` is omitted. | In test verification for AC-20, parse `git status --porcelain -uall` to inspect file-level granularity, or normalize directory prefixes against the closed 16-file list. |
| **5. Subprocess Terminal Popping** | Running `git` without `creationflags=subprocess.CREATE_NO_WINDOW` triggers visual console window popups, violating anti-spoofing/hygiene gates. | Explicitly supply `creationflags=subprocess.CREATE_NO_WINDOW` (value `0x08000000`) in all `subprocess.run` / `Popen` calls. |
| **6. Premature Creation of Downstream Files** | Creating placeholder files for `init_db.py`, `conftest.py`, or `schema_v2.sql` violates Task 1.01 scope isolation. | Restrict write targets strictly to `persistence/__init__.py` and `tests/card_1_1/__init__.py`. Do not scaffold downstream tasks. |

---

## 6. Verification Procedures & TDD Test Specifications

The implementing engineer should structure the pre-implementation test assertions according to the following specifications in [`tests/card_1_1/test_soft_harness_hygiene.py`](file:///D:/__CoChem/tests/card_1_1/test_soft_harness_hygiene.py) (allocated to Task 1.05, but verified via standalone TDD check for Task 1.01):

### 6.1 Test Specification [T1]: `test_package_init_markers_exist_and_empty`
* **Stimulus:** Read filesystem metadata for [`persistence/__init__.py`](file:///D:/__CoChem/persistence/__init__.py) and [`tests/card_1_1/__init__.py`](file:///D:/__CoChem/tests/card_1_1/__init__.py).
* **Assertions:**
  - `Path(p).is_file() is True`
  - `Path(p).stat().st_size == 0`
* **Initial Failure Mode:** `FileNotFoundError` (files do not exist yet).

### 6.2 Test Specification [T2]: `test_package_init_markers_importable`
* **Stimulus:**
  ```python
  import sys
  from pathlib import Path
  import importlib

  root_dir = str(Path(r"D:\__CoChem").resolve())
  if root_dir not in sys.path:
      sys.path.insert(0, root_dir)
  importlib.invalidate_caches()
  mod_p = importlib.import_module("persistence")
  mod_t = importlib.import_module("tests.card_1_1")
  ```
* **Assertions:**
  - `hasattr(mod_p, "__path__") is True`
  - `hasattr(mod_t, "__path__") is True`
  - `Path(mod_p.__file__).resolve() == Path(r"D:\__CoChem\persistence\__init__.py").resolve()`
  - `Path(mod_t.__file__).resolve() == Path(r"D:\__CoChem\tests\card_1_1\__init__.py").resolve()`
* **Initial Failure Mode:** `ModuleNotFoundError`.

### 6.3 Test Specification [T3]: `test_ac20_scope_purity`
* **Stimulus:** Run `git status --porcelain -uall` and `git diff --exit-code __agentic/v2/brief_card_1_1.md`.
* **Assertions:**
  - Output lines from `git status` correspond only to permitted target files (`persistence/__init__.py`, `tests/card_1_1/__init__.py`).
  - Return code of `git diff --exit-code __agentic/v2/brief_card_1_1.md` is `0`.
* **Initial Failure Mode:** Test passes when clean, fails if foreign files or brief mutations are detected.

---

## 7. Implementation Handoff Checklist

Before handing off the task artifact to `cochem-audit`, the engineer must ensure:
- [ ] Parent directories [`persistence/`](file:///D:/__CoChem/persistence) and [`tests/card_1_1/`](file:///D:/__CoChem/tests/card_1_1) exist.
- [ ] [`persistence/__init__.py`](file:///D:/__CoChem/persistence/__init__.py) exists and has `len == 0`.
- [ ] [`tests/card_1_1/__init__.py`](file:///D:/__CoChem/tests/card_1_1/__init__.py) exists and has `len == 0`.
- [ ] `importlib.import_module` verifies both as valid packages with `__path__`.
- [ ] `git status --porcelain` contains no extraneous untracked files outside items 3 and 6.
- [ ] `git diff --exit-code __agentic/v2/brief_card_1_1.md` produces zero diff.